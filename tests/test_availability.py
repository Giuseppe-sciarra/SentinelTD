"""Outage lifecycle through real checks, SQL transactions and authenticated API routes."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from fastapi import FastAPI
import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateIndex, CreateTable
from sqlalchemy.dialects import postgresql

import test_screenshot_schedule as fixtures
from app import connectors, worker, settings_store
from app.availability import record_availability
from app.auth import require_auth
from app.check_gate import observed_server, CheckDeferred
from app.db import get_session
from app.models import Site, OfflineEpisode as E
from app.routers import availability, sites


class Clock(datetime):
    instant = fixtures.NOW
    @classmethod
    def now(cls, tz=None):
        return cls.instant if tz else cls.instant.replace(tzinfo=None)


class AvailabilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.SchedulerTests.asyncSetUp(self)
        Clock.instant = fixtures.NOW
        self.extra = [patch.object(connectors, 'datetime', Clock), patch.object(worker, 'datetime', Clock),
                      patch.object(availability, 'datetime', Clock)]
        for p in self.extra: p.start()
        self.app = FastAPI(); self.app.include_router(availability.router)
        self.app.dependency_overrides[require_auth] = lambda: True
        async def session():
            async with self.sessions() as s: yield s
        self.app.dependency_overrides[get_session] = session

    async def asyncTearDown(self):
        for p in reversed(self.extra): p.stop()
        await fixtures.SchedulerTests.asyncTearDown(self)

    async def add_site(self, **kwargs):
        return await fixtures.SchedulerTests.add_site(self, **({'status': 'ok', 'notifications_silenced': True} | kwargs))

    async def check(self, sid, success=False):
        async def result(*args, **kwargs):
            observed_server.set('203.0.113.10')
            if not success: raise httpx.ConnectTimeout('ConnectTimeout')
            return {'cms': 'wp', 'core': {}, 'extensions': []}
        with patch.object(connectors, 'fetch_status', side_effect=result), patch.object(worker, 'notify_dispatch', AsyncMock()) as notify:
            await worker.poll_site({'redis': self.redis}, sid)
        notify.assert_not_awaited()

    async def rows(self):
        async with self.sessions() as s:
            return (await s.execute(select(E).order_by(E.id))).scalars().all()

    async def api(self, query=''):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://test') as client:
            r = await client.get('/api/availability' + query)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    async def test_pending_failure_and_recovery_leave_no_false_offline_episode(self):
        sid = await self.add_site()
        await self.check(sid)
        self.assertEqual(await self.rows(), [])
        Clock.instant += timedelta(minutes=1)
        await self.check(sid, True)
        self.assertEqual(await self.rows(), [])

    async def test_confirmed_outage_is_one_episode_until_manual_recovery_then_new_episode(self):
        sid = await self.add_site()
        start = Clock.instant
        await self.check(sid)
        Clock.instant += timedelta(minutes=6)
        await self.check(sid)
        Clock.instant += timedelta(minutes=1)
        await self.check(sid)
        rows = await self.rows(); self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].failed_checks, 2)
        self.assertEqual(rows[0].server_key, '203.0.113.10')
        self.assertEqual(rows[0].started_at.replace(tzinfo=timezone.utc), start)
        Clock.instant += timedelta(minutes=1)
        with patch.object(connectors, 'fetch_status', AsyncMock(return_value={'cms': 'wp'})):
            async with self.sessions() as s: await sites.refresh_now(sid, s=s)
        rows = await self.rows(); self.assertEqual(rows[0].ended_at.replace(tzinfo=timezone.utc), Clock.instant)
        Clock.instant += timedelta(minutes=1)
        await self.check(sid)
        Clock.instant += timedelta(minutes=6)
        await self.check(sid)
        self.assertEqual(len(await self.rows()), 2)
        data = await self.api()
        self.assertEqual((data['total'], data['active'], data['failed_checks']), (2, 1, 3))
        self.assertEqual(data['timezone'], 'Europe/Rome')
        self.assertEqual(data['items'][1]['duration_seconds'], 480)
        self.assertTrue(data['items'][0]['started_at'].endswith('+00:00'))

    async def test_zero_confirmation_window_records_manual_failure_without_notifications(self):
        await settings_store.save_operational_settings({'offline_alert_minutes': 0})
        sid = await self.add_site(notifications_silenced=False)
        with patch.object(connectors, 'fetch_status', AsyncMock(side_effect=httpx.ConnectTimeout('connect'))):
            async with self.sessions() as s: await sites.refresh_now(sid, s=s)
        self.assertEqual(len(await self.rows()), 1)

    async def test_dns_or_deferred_check_does_not_close_or_increment_an_existing_outage(self):
        await settings_store.save_operational_settings({'offline_alert_minutes': 0})
        sid = await self.add_site(); await self.check(sid)
        Clock.instant += timedelta(minutes=1)
        with patch.object(connectors, 'fetch_status', AsyncMock(side_effect=CheckDeferred('busy'))):
            await worker.poll_site({'redis': self.redis}, sid)
        self.assertEqual((await self.rows())[0].failed_checks, 1)
        from test_connector_retry import dns_error
        with patch.object(connectors, 'fetch_status', AsyncMock(side_effect=dns_error())):
            await worker.poll_site({'redis': self.redis}, sid)
        row = (await self.rows())[0]
        self.assertEqual(row.failed_checks, 1); self.assertIsNone(row.ended_at)

    async def test_double_record_is_idempotent_and_history_survives_site_deletion_and_session_restart(self):
        await settings_store.save_operational_settings({'offline_alert_minutes': 0})
        sid = await self.add_site(); await self.check(sid)
        async with self.sessions() as s:
            site = await s.get(Site, sid)
            await record_availability(s, site); await record_availability(s, site)
            await s.commit(); await s.delete(site); await s.commit()
        data = await self.api()
        self.assertEqual(data['total'], 1); self.assertEqual(data['failed_checks'], 1)
        self.assertEqual(data['items'][0]['site_name'], 'Test site')

    async def test_filters_pagination_and_old_open_episodes_have_exact_totals(self):
        now = Clock.instant
        async with self.sessions() as s:
            for i in range(55):
                s.add(E(site_id=i % 2 + 1, site_name='Site '+str(i % 2 + 1), site_url='https://example.test',
                        server_key='203.0.113.'+str(i % 2 + 10), started_at=now-timedelta(hours=2),
                        confirmed_at=now-timedelta(hours=1), last_failed_at=now-timedelta(minutes=10),
                        ended_at=now, failed_checks=2, reason='ConnectTimeout'))
            s.add(E(site_id=3, site_name='Old open', site_url='https://old.test', server_key='203.0.113.12',
                    started_at=now-timedelta(days=40), confirmed_at=now-timedelta(days=40), last_failed_at=now))
            s.add(E(site_id=4, site_name='Old closed', site_url='https://old.test', server_key='203.0.113.13',
                    started_at=now-timedelta(days=40), confirmed_at=now-timedelta(days=40), last_failed_at=now-timedelta(days=39), ended_at=now-timedelta(days=39)))
            await s.commit()
        data = await self.api('?days=30'); self.assertEqual(data['total'], 56); self.assertEqual(len(data['items']), 50)
        data = await self.api('?days=30&offset=50'); self.assertEqual(len(data['items']), 6)
        data = await self.api('?site_id=1&server=203.0.113.10')
        self.assertEqual(data['total'], 28); self.assertEqual(data['failed_checks'], 56)
        self.assertEqual(len(data['servers']), 3)  # options remain stable under filtering

    async def test_stale_failure_does_not_reopen_a_closed_episode(self):
        await settings_store.save_operational_settings({'offline_alert_minutes': 0})
        sid = await self.add_site(); await self.check(sid)
        stale = Clock.instant
        Clock.instant += timedelta(minutes=1); await self.check(sid, True)
        async with self.sessions() as s:
            site = await s.get(Site, sid)
            site.status='error'; site.offline_since=stale; site.last_checked=stale
            await record_availability(s, site); await s.commit()
        self.assertEqual(len(await self.rows()), 1)

    async def test_unique_open_index_and_postgres_ddl_are_valid(self):
        now=Clock.instant
        async with self.sessions() as s:
            for _ in range(2): s.add(E(site_id=1, site_name='test', site_url='https://test', started_at=now, confirmed_at=now, last_failed_at=now))
            with self.assertRaises(IntegrityError): await s.commit()
        ddl=str(CreateTable(E.__table__).compile(dialect=postgresql.dialect()))
        self.assertIn('TIMESTAMP WITH TIME ZONE', ddl)
        idx=next(i for i in E.__table__.indexes if i.name=='ux_offline_episode_open')
        self.assertIn('WHERE ended_at IS NULL', str(CreateIndex(idx).compile(dialect=postgresql.dialect())))

    async def test_endpoint_is_authenticated_and_read_only(self):
        app=FastAPI(); app.include_router(availability.router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            self.assertEqual((await client.get('/api/availability')).status_code, 401)
        self.assertEqual({m for r in availability.router.routes for m in r.methods}, {'GET'})
