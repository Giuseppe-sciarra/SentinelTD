"""Removed monitoring must not send requests, survive preferences or appear as an event."""
import unittest
from unittest.mock import AsyncMock, patch
from fastapi import FastAPI
import httpx
import test_screenshot_schedule as fixtures
from app import worker, notify, settings_store
from app.models import Site
from app.routers import notifications, dashboard
from app.auth import require_auth

class RemovalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.SchedulerTests.asyncSetUp(self)
        self.notify_patch=patch.object(notify,"SessionLocal",self.sessions);self.notify_patch.start()
    async def asyncTearDown(self):
        self.notify_patch.stop()
        await fixtures.SchedulerTests.asyncTearDown(self)

    async def test_old_queued_nightly_diagnostics_make_no_requests_or_notifications(self):
        with patch.object(worker,'fetch_status',AsyncMock()) as fetch, patch.object(worker,'notify_dispatch',AsyncMock()) as send:
            await worker.retired_diagnostics_job({'redis':self.redis},1,50,'old-nightly-batch')
        fetch.assert_not_awaited();send.assert_not_awaited()

    async def test_background_schedules_have_no_server_or_nightly_diagnostic_work(self):
        jobs=[getattr(job,'__name__',getattr(job,'name','')) for job in worker.WorkerSettings.functions]
        cron=[job.coroutine.__name__ for job in worker.WorkerSettings.cron_jobs]
        for name in ['server_metrics_tick','sample_server_metrics','diag_all','nightly_summary_tick']:
            self.assertNotIn(name,jobs+cron)
        self.assertIn('poll_site',jobs);self.assertIn('screenshot_tick',cron)

    async def test_saved_old_resource_settings_are_not_exposed_or_reactivated(self):
        p=await settings_store.save_operational_settings({'server_metrics_enabled':True,'server_metrics_minutes':1,'server_split':['203.0.113.1'], 'server_limited':['203.0.113.1']})
        for key in ['server_metrics_enabled','server_metrics_minutes','server_split']:self.assertNotIn(key,p)
        self.assertEqual(p['server_limited'],['203.0.113.1'])
        self.assertEqual(p,await settings_store.get_operational_settings())

    async def test_removed_server_notification_returns_404_and_sends_nothing(self):
        app=FastAPI();app.include_router(notifications.router);app.dependency_overrides[require_auth]=lambda: True
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            listing=(await client.get('/api/notifications')).json()
            self.assertNotIn('nightly_summary',[x['event'] for x in listing])
            r=await client.post('/api/notifications/nightly_summary/preview',json={})
            self.assertEqual(r.status_code,404)
        with patch.object(notify,'send_report',AsyncMock()) as email,patch.object(notify,'send_telegram',AsyncMock()) as telegram:
            self.assertEqual(await notify.dispatch('nightly_summary',{}),{'email':False,'telegram':False})
        email.assert_not_awaited();telegram.assert_not_awaited()

    async def test_dashboard_problems_do_not_contact_sites(self):
        sid=await fixtures.SchedulerTests.add_site(self,status='check_pending',error='ConnectTimeout')
        with patch.object(worker,'fetch_status',AsyncMock()) as fetch:
            async with self.sessions() as session:rows=await dashboard.problems(session)
        fetch.assert_not_awaited()
        self.assertTrue(any(x['site_id']==sid and x['kind']=='check' for x in rows))
        self.assertFalse(any(x['site_id']==sid and x['kind']=='offline' for x in rows))

    async def test_old_diagnostic_resources_are_hidden_without_losing_site_checks(self):
        import json
        site=Site(diag_json=json.dumps({"server":{"load":[9],"mem_total":100},"space":{"ok":True},"core":{"status":"ok"}}))
        self.assertIsNone(site.diag)

    async def test_removed_diagnostic_notifications_cannot_be_previewed_or_sent(self):
        app=FastAPI();app.include_router(notifications.router);app.dependency_overrides[require_auth]=lambda: True
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
            listed=(await client.get("/api/notifications")).json()
            for event in ["site_space","core_integrity"]:
                self.assertNotIn(event,[x["event"] for x in listed])
                self.assertEqual((await client.post("/api/notifications/"+event+"/preview",json={})).status_code,404)
                with patch.object(notify,"send_report",AsyncMock()) as mail,patch.object(notify,"send_telegram",AsyncMock()) as tg:
                    self.assertEqual(await notify.dispatch(event,{}),{"email":False,"telegram":False})
                mail.assert_not_awaited();tg.assert_not_awaited()

    async def test_old_diagnostics_do_not_produce_dashboard_or_report_warnings(self):
        import json
        from app.problems import site_problems
        from app import report
        sid=await fixtures.SchedulerTests.add_site(self,status="ok",diag_json=json.dumps({"space":{"ok":False,"written_mb":1},"core":{"status":"issues"},"sizes":{"big_logs":[{"path":"error.log","bytes":500000000}]}}))
        async with self.sessions() as session:
            site=await session.get(Site,sid)
            self.assertFalse(any(p["kind"] in ["space","logs","core"] for p in site_problems(site)))
            with patch.object(report,"from_url",create=True),patch("app.servers.server_of",AsyncMock(return_value="203.0.113.1")):
                rows=await report._site_stats(session,[site])
            for key in ["space","space_low","core_files","core_issues","size"]:self.assertNotIn(key,rows[0])

    async def test_diagnostic_and_size_actions_are_no_longer_exposed(self):
        from app.routers import sites
        app=FastAPI();app.include_router(sites.router);app.dependency_overrides[require_auth]=lambda: True
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            self.assertEqual((await client.post('/api/sites/1/diagnostics?space=150')).status_code,404)
            self.assertEqual((await client.get('/api/sites/1/sizes')).status_code,404)

    async def test_previous_default_report_does_not_restore_diagnostic_columns(self):
        from app import report
        from app.models import AppSetting
        import ast
        from pathlib import Path
        previous=(Path(__file__).parent/'fixtures/report-2.28.14.html').read_text()
        async with self.sessions() as session:
            session.add(AppSetting(key=report.TEMPLATE_KEY,value=previous));await session.commit()
        with patch.object(report,'SessionLocal',self.sessions):current=await report.get_template()
        self.assertNotIn('<th>Spazio libero</th>',current)
        self.assertNotIn('<th>File del core</th>',current)
