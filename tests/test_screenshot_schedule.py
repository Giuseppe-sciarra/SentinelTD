"""Regression tests: real SQLAlchemy queries and arq jobs, with local substitutes
for PostgreSQL (SQLite), Redis (fakeredis) and the shooter's HTTP endpoint.

Run: pip install -r tests/requirements.txt
     python -m unittest discover -s tests -p 'test_*.py' -v
"""
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
from app.screenshot_schedule import enqueue_screenshot, screenshot_due

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

    async def test_scheduler_ignores_poll_interval_and_connector_errors(self):
        sid = await self.add_site(shot_at=NOW - timedelta(days=15),
                                  last_checked=NOW, poll_interval_minutes=43200,
                                  status="error", error="Connector unavailable")
        await worker.screenshot_tick({"redis": self.redis})
        jobs = await self.redis.queued_jobs()
        self.assertEqual([(j.function, j.args) for j in jobs], [("shoot_site", (sid,))])
        await worker.screenshot_tick({"redis": self.redis})
        self.assertEqual(len(await self.redis.queued_jobs()), 1)

    async def test_preferences_change_is_read_without_restart(self):
        await self.add_site(shot_at=NOW - timedelta(hours=8))
        await settings_store.save_operational_settings({"screenshot_every_hours": 12})
        await worker.screenshot_tick({"redis": self.redis})
        self.assertEqual(await self.redis.queued_jobs(), [])
        await settings_store.save_operational_settings({"screenshot_every_hours": 6})
        await worker.screenshot_tick({"redis": self.redis})
        self.assertEqual(len(await self.redis.queued_jobs()), 1)

    async def test_disabled_fresh_and_recently_failed_sites_are_skipped(self):
        await self.add_site(enabled=False)
        await self.add_site(shot_at=NOW - timedelta(hours=1))
        await self.add_site(shot_at=NOW - timedelta(days=15), shot_attempted_at=NOW)
        sid = await self.add_site()
        await worker.screenshot_tick({"redis": self.redis})
        self.assertEqual([j.args for j in await self.redis.queued_jobs()], [(sid,)])

    async def test_staggering_and_manual_queue_share_deduplication(self):
        first = await self.add_site()
        second = await self.add_site()
        await worker.screenshot_tick({"redis": self.redis})
        jobs = await self.redis.queued_jobs()
        self.assertEqual([j.args for j in jobs], [(first,), (second,)])
        self.assertGreaterEqual(jobs[1].score - jobs[0].score, 12000)
        self.assertIsNone(await enqueue_screenshot(self.redis, first))
        self.assertIsNone(await enqueue_screenshot(self.redis, second, delay_seconds=12))

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
        with patch.object(worker.httpx, "AsyncClient", client):
            await worker.shoot_site({"redis": self.redis}, sid)

    async def test_unchanged_success_updates_real_timestamp(self):
        sid = await self.add_site(shot_path="site_1.jpg", shot_at=NOW - timedelta(days=15),
                                  shot_blocked_at=NOW - timedelta(days=1))
        await self.capture(sid, {"path": "site_1.jpg", "blocked": False})
        site = await self.read_site(sid)
        self.assertEqual(site.shot_at, NOW.replace(tzinfo=None))
        self.assertIsNone(site.shot_blocked_at)
        await worker.screenshot_tick({"redis": self.redis})
        self.assertEqual(await self.redis.queued_jobs(), [])

    async def test_blocked_capture_keeps_good_preview_and_retries_on_interval(self):
        old = NOW - timedelta(days=15)
        sid = await self.add_site(shot_path="site_1.jpg", shot_at=old)
        await self.capture(sid, {"path": "site_1.jpg", "blocked": True, "kept_previous": True})
        site = await self.read_site(sid)
        self.assertEqual(site.shot_at, old.replace(tzinfo=None))
        self.assertEqual(site.shot_path, "site_1.jpg")
        self.assertIsNotNone(site.shot_blocked_at)
        await worker.screenshot_tick({"redis": self.redis})
        self.assertEqual(await self.redis.queued_jobs(), [])
        async with self.sessions() as s:
            site = await s.get(Site, sid)
            site.shot_attempted_at = NOW - timedelta(hours=12)
            await s.commit()
        await worker.screenshot_tick({"redis": self.redis})
        self.assertEqual(len(await self.redis.queued_jobs()), 1)

    async def test_transport_failure_preserves_attempt_across_worker_restart(self):
        old = NOW - timedelta(days=15)
        sid = await self.add_site(shot_path="site_1.jpg", shot_at=old)
        await self.capture(sid, fail=True)
        self.assertEqual((await self.read_site(sid)).shot_at, old.replace(tzinfo=None))
        # Fresh session, as after a restart: no in-memory failure cooldown.
        await worker.screenshot_tick({"redis": self.redis})
        self.assertEqual(await self.redis.queued_jobs(), [])

    async def test_real_arq_job_has_no_completion_cooldown(self):
        sid = await self.add_site()
        first = await enqueue_screenshot(self.redis, sid)
        self.assertIsNotNone(first)
        self.assertIsNone(await enqueue_screenshot(self.redis, sid))
        registration = next(f for f in worker.WorkerSettings.functions
                            if getattr(f, "name", "") == "shoot_site")
        self.assertEqual(registration.keep_result_s, 0)
        client_type = httpx.AsyncClient
        async def handler(request):
            # Stable id also deduplicates while the job is actively executing.
            self.assertIsNone(await enqueue_screenshot(self.redis, sid))
            return httpx.Response(200, json={"path": "site_1.jpg", "blocked": False})
        transport = httpx.MockTransport(handler)
        runner = Worker(functions=[registration], redis_pool=self.redis, burst=True,
                        handle_signals=False, poll_delay=0.01)
        with patch.object(worker.httpx, "AsyncClient",
                          lambda **kwargs: client_type(transport=transport, **kwargs)), \
                patch("arq.worker.log_redis_info", new=AsyncMock()):  # fakeredis has no INFO command
            await runner.async_run()
        self.assertEqual(runner.jobs_complete, 1)
        self.assertEqual(runner.jobs_failed, 0)
        self.assertIsNotNone(await enqueue_screenshot(self.redis, sid))

    def test_independent_cron_runs_every_minute_and_at_startup(self):
        c = next(c for c in worker.WorkerSettings.cron_jobs
                 if c.coroutine is worker.screenshot_tick)
        self.assertEqual(c.minute, set(range(60)))
        self.assertTrue(c.run_at_startup)


if __name__ == "__main__":
    unittest.main()
