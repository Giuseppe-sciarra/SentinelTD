"""Regression tests: real SQLAlchemy queries and arq jobs, with local substitutes
for PostgreSQL (SQLite), Redis (fakeredis) and the shooter's HTTP endpoint.

Run: pip install -r tests/requirements.txt
     python -m unittest discover -s tests -p 'test_*.py' -v
"""
import asyncio
import json
import os
from pathlib import Path
import secrets
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("ADMIN_PASSWORD", secrets.token_urlsafe(24))
os.environ.setdefault("JWT_SECRET", secrets.token_urlsafe(48))

import httpx
from arq.connections import ArqRedis
from arq.worker import Worker
from fakeredis import FakeServer
from fakeredis.aioredis import FakeAsyncRedisConnection
from redis.asyncio import ConnectionPool
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import settings_store, worker
from app.db import Base
from app.models import Site
from app.screenshot_schedule import QUEUE, request_shots, screenshot_due

NOW = datetime(2026, 10, 3, 20, 0, tzinfo=timezone.utc)


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz else NOW.replace(tzinfo=None)


class DueTests(unittest.TestCase):
    def test_missing_preview_is_due(self):
        self.assertTrue(screenshot_due(None, None, NOW, 12))

    def test_exact_interval_boundary(self):
        self.assertFalse(screenshot_due(NOW - timedelta(hours=12, microseconds=-1), None, NOW, 12))
        self.assertTrue(screenshot_due(NOW - timedelta(hours=12), None, NOW, 12))

    def test_failed_attempt_obeys_same_interval(self):
        old = NOW - timedelta(days=15)
        self.assertFalse(screenshot_due(old, NOW - timedelta(hours=11), NOW, 12))
        self.assertTrue(screenshot_due(old, NOW - timedelta(hours=12), NOW, 12))

    def test_failed_capture_retries_after_one_hour(self):
        # scatto non riuscito (motivo salvato): si riprova dopo un'ora, non dopo l'intervallo intero
        old = NOW - timedelta(days=15)
        self.assertFalse(screenshot_due(old, NOW - timedelta(minutes=59), NOW, 8, failed=True))
        self.assertTrue(screenshot_due(old, NOW - timedelta(hours=1), NOW, 8, failed=True))

    def test_success_completion_and_manual_capture_reset_timer(self):
        self.assertFalse(screenshot_due(NOW, NOW - timedelta(minutes=2), NOW, 1))

    def test_interval_change_and_naive_database_dates(self):
        shot = (NOW - timedelta(hours=8)).replace(tzinfo=None)
        self.assertFalse(screenshot_due(shot, None, NOW, 12))
        self.assertTrue(screenshot_due(shot, None, NOW, 6))


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        pool = ConnectionPool(connection_class=FakeAsyncRedisConnection, server=FakeServer())
        self.redis = ArqRedis(pool_or_conn=pool)
        self.patches = [patch.object(worker, "SessionLocal", self.sessions),
                        patch.object(settings_store, "SessionLocal", self.sessions),
                        patch.object(worker, "datetime", Clock)]
        for p in self.patches:
            p.start()

    async def asyncTearDown(self):
        for p in reversed(self.patches):
            p.stop()
        await self.redis.aclose(close_connection_pool=True)
        await self.engine.dispose()

    async def add_site(self, **kwargs):
        async with self.sessions() as s:
            site = Site(**({"name": "Test site", "url": "https://example.test", "cms": "wp", "token": "test"} | kwargs))
            s.add(site)
            await s.commit()
            return site.id

    async def read_site(self, sid):
        async with self.sessions() as s:
            return await s.get(Site, sid)

    async def queue(self):
        return [int(x) for x in await self.redis.zrange(QUEUE, 0, -1)]

    async def test_scheduler_ignores_poll_interval_and_connector_errors(self):
        sid = await self.add_site(shot_at=NOW - timedelta(days=15),
                                  last_checked=NOW, poll_interval_minutes=43200,
                                  status="error", error="Connector unavailable")
        await worker.screenshot_tick({"redis": self.redis})
        self.assertEqual(await self.queue(), [sid])
        jobs = await self.redis.queued_jobs()
        self.assertEqual([(j.function, j.args) for j in jobs], [("shot_runner", ())])
        await worker.screenshot_tick({"redis": self.redis})
        self.assertEqual(await self.queue(), [sid])                 # una volta sola nella fila
        self.assertEqual(len(await self.redis.queued_jobs()), 1)    # un solo esecutore

    async def test_preferences_change_is_read_without_restart(self):
        await self.add_site(shot_at=NOW - timedelta(hours=8))
        await settings_store.save_operational_settings({"screenshot_every_hours": 12})
        await worker.screenshot_tick({"redis": self.redis})
        self.assertEqual(await self.queue(), [])
        await settings_store.save_operational_settings({"screenshot_every_hours": 6})
        await worker.screenshot_tick({"redis": self.redis})
        self.assertEqual(len(await self.queue()), 1)

    async def test_disabled_fresh_and_recently_failed_sites_are_skipped(self):
        await self.add_site(enabled=False)
        await self.add_site(shot_at=NOW - timedelta(hours=1))
        await self.add_site(shot_at=NOW - timedelta(days=15), shot_attempted_at=NOW)
        await self.add_site(shot_at=NOW - timedelta(days=15), shot_attempted_at=NOW - timedelta(minutes=20),
                            shot_error="servizio delle anteprime non raggiungibile")
        sid = await self.add_site()
        await worker.screenshot_tick({"redis": self.redis})
        self.assertEqual(await self.queue(), [sid])

    async def test_manual_request_jumps_the_queue(self):
        first = await self.add_site()
        second = await self.add_site()
        third = await self.add_site()
        await worker.screenshot_tick({"redis": self.redis})
        self.assertEqual(await self.queue(), [first, second, third])
        await request_shots(self.redis, [third], manual=True)     # "Aggiorna" a mano
        self.assertEqual(await self.queue(), [third, first, second])
        self.assertEqual(len(await self.redis.queued_jobs()), 1)

    async def capture(self, sid, payload=None, fail=False):
        # Verify the attempt is durable BEFORE HTTP succeeds or raises.
        async def handler(request):
            site = await self.read_site(sid)
            self.assertEqual(site.shot_attempted_at, NOW.replace(tzinfo=None))
            if fail:
                raise httpx.ConnectError("Shooter unavailable", request=request)
            return httpx.Response(200, json=payload)
        client_type = httpx.AsyncClient
        def client(**kwargs):
            return client_type(transport=httpx.MockTransport(handler), **kwargs)
        with patch.object(worker.httpx, "AsyncClient", client), patch.object(worker, "evlog", AsyncMock()):
            await worker._shoot_one(sid)

    async def test_unchanged_success_updates_real_timestamp(self):
        sid = await self.add_site(shot_path="site_1.jpg", shot_at=NOW - timedelta(days=15),
                                  shot_blocked_at=NOW - timedelta(days=1), shot_error="vecchio errore")
        await self.capture(sid, {"path": "site_1.jpg", "blocked": False})
        site = await self.read_site(sid)
        self.assertEqual(site.shot_at, NOW.replace(tzinfo=None))
        self.assertIsNone(site.shot_blocked_at)
        self.assertEqual(site.shot_error, "")
        await worker.screenshot_tick({"redis": self.redis})
        self.assertEqual(await self.queue(), [])

    async def test_blocked_capture_keeps_good_preview_and_retries_on_interval(self):
        old = NOW - timedelta(days=15)
        sid = await self.add_site(shot_path="site_1.jpg", shot_at=old)
        await self.capture(sid, {"path": "site_1.jpg", "blocked": True, "kept_previous": True})
        site = await self.read_site(sid)
        self.assertEqual(site.shot_at, old.replace(tzinfo=None))
        self.assertEqual(site.shot_path, "site_1.jpg")
        self.assertIsNotNone(site.shot_blocked_at)
        await worker.screenshot_tick({"redis": self.redis})
        self.assertEqual(await self.queue(), [])
        async with self.sessions() as s:
            site = await s.get(Site, sid)
            site.shot_attempted_at = NOW - timedelta(hours=12)
            await s.commit()
        await worker.screenshot_tick({"redis": self.redis})
        self.assertEqual(await self.queue(), [sid])

    async def test_transport_failure_is_recorded_and_retried_after_an_hour(self):
        old = NOW - timedelta(days=15)
        sid = await self.add_site(shot_path="site_1.jpg", shot_at=old)
        await self.capture(sid, fail=True)
        site = await self.read_site(sid)
        self.assertEqual(site.shot_at, old.replace(tzinfo=None))
        self.assertEqual(site.shot_error, "servizio delle anteprime non raggiungibile")
        # Fresh session, as after a restart: the failure is in the database, not in memory.
        await worker.screenshot_tick({"redis": self.redis})
        self.assertEqual(await self.queue(), [])
        async with self.sessions() as s:
            site = await s.get(Site, sid)
            site.shot_attempted_at = NOW - timedelta(hours=1)
            await s.commit()
        await worker.screenshot_tick({"redis": self.redis})
        self.assertEqual(await self.queue(), [sid])

    async def test_runner_shoots_one_at_a_time_manual_first(self):
        # caso reale: con 4 scatti insieme davanti a uno shooter che ne fa uno per volta, le
        # chiamate in fondo scadevano e quelle anteprime restavano vecchie di giorni
        ids = [await self.add_site(name=f"Sito {i}") for i in range(4)]
        await request_shots(self.redis, ids[:3])
        await request_shots(self.redis, [ids[3]], manual=True)
        order, live, peak = [], [0], [0]
        client_type = httpx.AsyncClient

        async def handler(request):
            live[0] += 1
            peak[0] = max(peak[0], live[0])
            await asyncio.sleep(0.02)
            order.append(json.loads(request.content)["site_id"])
            live[0] -= 1
            return httpx.Response(200, json={"path": "x.jpg", "blocked": False})
        transport = httpx.MockTransport(handler)
        registration = next(f for f in worker.WorkerSettings.functions if getattr(f, "name", "") == "shot_runner")
        runner = Worker(functions=[registration], redis_pool=self.redis, burst=True,
                        handle_signals=False, poll_delay=0.01, max_jobs=4)
        with patch.object(worker.httpx, "AsyncClient",
                          lambda **kwargs: client_type(transport=transport, **kwargs)), \
                patch("arq.worker.log_redis_info", new=AsyncMock()):  # fakeredis has no INFO command
            await runner.async_run()
        self.assertEqual(order, [ids[3], ids[0], ids[1], ids[2]])
        self.assertEqual(peak[0], 1)
        self.assertEqual(await self.queue(), [])
        for sid in ids:
            self.assertEqual((await self.read_site(sid)).shot_at, NOW.replace(tzinfo=None))

    async def test_jobs_left_by_the_previous_version_go_through_the_queue(self):
        sid = await self.add_site()
        await worker.shoot_site({"redis": self.redis}, sid)
        self.assertEqual(await self.queue(), [sid])

    def test_independent_cron_runs_every_minute_and_at_startup(self):
        c = next(c for c in worker.WorkerSettings.cron_jobs
                 if c.coroutine is worker.screenshot_tick)
        self.assertEqual(c.minute, set(range(60)))
        self.assertTrue(c.run_at_startup)


if __name__ == "__main__":
    unittest.main()
