"""DNS retries use real HTTPX requests; state tests run real SQLite and Redis substitutes."""
import asyncio
import socket
import ssl
import unittest
from types import SimpleNamespace
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import test_screenshot_schedule as fixtures
import httpx
from app import connectors, worker
from app.models import Site
from app.problems import site_problems


@asynccontextmanager
async def unrestricted_slot(*args, **kwargs):
    yield


def dns_error(code=socket.EAI_AGAIN, text='Temporary failure in name resolution'):
    error = httpx.ConnectError(f'[Errno {code}] {text}')
    error.__cause__ = socket.gaierror(code, text)
    return error


class ConnectorRetryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.site = SimpleNamespace(id=63, name='Test', cms='wp', url='https://example.test', token='test')
        self.requests = []
        self.results = []
        async def handler(request):
            self.requests.append(request)
            result = self.results.pop(0)
            if isinstance(result, BaseException):
                raise result
            if isinstance(result, int):
                return httpx.Response(result, json={})
            return httpx.Response(200, json=result)
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
        self.sleep = AsyncMock()
        self.patches = [patch.object(connectors, 'status_slot', unrestricted_slot), patch.object(connectors, 'status_client', AsyncMock(return_value=self.client)),
                        patch.object(connectors.asyncio, 'sleep', self.sleep),
                        patch.object(connectors.settings, 'STATUS_CHECK_ATTEMPTS', 3),
                        patch.object(connectors.settings, 'STATUS_CHECK_RETRY_SECONDS', 15)]
        for p in self.patches: p.start()
        connectors._WP_REST_STYLE.clear()

    async def asyncTearDown(self):
        for p in reversed(self.patches): p.stop()
        await self.client.aclose()
        connectors._WP_REST_STYLE.clear()

    async def test_two_dns_failures_recover_with_three_gets_and_two_15_second_delays(self):
        self.results = [dns_error(), dns_error(), {'cms':'wp', 'core':{}, 'extensions':[]}]
        result = await connectors.fetch_status(self.site, timeout=120, force=True)
        self.assertEqual(result['cms'], 'wp')
        self.assertEqual(len(self.requests), 3)
        self.assertEqual([call.args for call in self.sleep.await_args_list], [(15,), (15,)])
        for request in self.requests:
            self.assertEqual(request.method, 'GET')
            self.assertIn('/wp-json/', request.url.path)
            self.assertEqual(request.url.params['refresh'], '1')
            self.assertEqual(request.extensions['timeout']['connect'], 15)
            self.assertEqual(request.extensions['timeout']['read'], 120)
            self.assertEqual(request.headers['X-Sentinel-Token'], 'test')
        self.assertNotIn(self.site.id, connectors._WP_REST_STYLE)

    async def test_persistent_dns_failure_never_tries_the_alternate_rest_route(self):
        self.results = [dns_error(), dns_error(), dns_error()]
        with self.assertRaises(httpx.ConnectError):
            await connectors.fetch_status(self.site)
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(self.sleep.await_count, 2)
        self.assertNotIn(self.site.id, connectors._WP_REST_STYLE)

    async def test_joomla_gets_the_same_dns_retries(self):
        self.site.cms = 'joomla'
        self.results = [dns_error(), {'success':True, 'data':[{'cms':'joomla'}]}]
        self.assertEqual((await connectors.fetch_status(self.site))['cms'], 'joomla')
        self.assertEqual(len(self.requests), 2)
        self.sleep.assert_awaited_once_with(15)

    async def test_permanent_dns_error_is_not_retried_or_given_another_url(self):
        self.results = [dns_error(socket.EAI_NONAME, 'Name or service not known')]
        with self.assertRaises(httpx.ConnectError):
            await connectors.fetch_status(self.site)
        self.assertEqual(len(self.requests), 1)
        self.sleep.assert_not_awaited()

    async def test_tls_certificate_failure_is_not_retried(self):
        error = httpx.ConnectError('certificate verify failed')
        error.__cause__ = ssl.SSLCertVerificationError('certificate verify failed')
        self.results = [error]
        with self.assertRaises(httpx.ConnectError):
            await connectors.fetch_status(self.site)
        self.assertEqual(len(self.requests), 1)
        self.sleep.assert_not_awaited()

    async def test_connect_timeout_retries_but_read_timeout_does_not_repeat_a_heavy_refresh(self):
        self.results = [httpx.ConnectTimeout('connect'), {'cms':'wp'}]
        await connectors.fetch_status(self.site, force=True)
        self.sleep.assert_awaited_once_with(15)
        self.requests.clear(); self.sleep.reset_mock()
        self.results = [httpx.ReadTimeout('read')]
        with self.assertRaises(httpx.ReadTimeout):
            await connectors.fetch_status(self.site, force=True)
        self.assertEqual(len(self.requests), 1)
        self.sleep.assert_not_awaited()

    async def test_overload_http_error_does_not_double_requests_via_alternate_url(self):
        self.results = [503]
        with self.assertRaises(httpx.HTTPStatusError):
            await connectors.fetch_status(self.site, force=True)
        self.assertEqual(len(self.requests), 1)
        self.sleep.assert_not_awaited()

    async def test_connection_budget_is_fifteen_seconds(self):
        self.results = [{"cms":"wp"}]
        await connectors.fetch_status(self.site, timeout=20)
        self.assertEqual(self.requests[0].extensions["timeout"]["connect"], 15)

    async def test_real_rest_path_error_still_falls_back_and_remembers_only_success(self):
        self.results = [404, {'cms':'wp'}]
        self.assertEqual((await connectors.fetch_status(self.site))['cms'], 'wp')
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.requests[1].url.params['rest_route'], '/tdpanopticon/v1/status')
        self.assertEqual(connectors._WP_REST_STYLE[self.site.id], 'restroute')
        self.sleep.assert_not_awaited()

    async def test_failed_alternate_route_does_not_poison_the_preferred_route(self):
        self.results = [404, 404]
        with self.assertRaises(httpx.HTTPStatusError):
            await connectors.fetch_status(self.site)
        self.assertNotIn(self.site.id, connectors._WP_REST_STYLE)
        self.assertEqual(len(self.requests), 2)

    async def test_existing_alternate_style_stays_selected_on_a_dns_failure(self):
        connectors._WP_REST_STYLE[self.site.id] = 'restroute'
        self.results = [dns_error(), {'cms':'wp'}]
        await connectors.fetch_status(self.site)
        self.assertTrue(all(r.url.params['rest_route'] == '/tdpanopticon/v1/status' for r in self.requests))
        self.assertEqual(connectors._WP_REST_STYLE[self.site.id], 'restroute')

    async def test_cancellation_is_not_swallowed_or_retried(self):
        self.results = [asyncio.CancelledError()]
        with self.assertRaises(asyncio.CancelledError):
            await connectors.fetch_status(self.site)
        self.sleep.assert_not_awaited()


class RetryPreferencesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.SchedulerTests.asyncSetUp(self)
        self.site = SimpleNamespace(id=63, name='Test', cms='wp', url='https://example.test', token='test')

    async def asyncTearDown(self):
        await fixtures.SchedulerTests.asyncTearDown(self)

    async def test_saved_api_preferences_change_the_next_check_without_restart(self):
        from app.routers.preferences import get_preferences, put_preferences
        prefs = await get_preferences()
        self.assertEqual((prefs['status_check_attempts'], prefs['status_check_retry_seconds']), (3, 15))
        await put_preferences(prefs | {'status_check_attempts': 2, 'status_check_retry_seconds': 7})
        once = AsyncMock(side_effect=[httpx.ConnectTimeout('connect'), {'cms': 'wp'}])
        sleep = AsyncMock()
        with patch.object(connectors, '_fetch_status_once', once), patch.object(connectors.asyncio, 'sleep', sleep):
            self.assertEqual(await connectors.fetch_status(self.site), {'cms': 'wp'})
        self.assertEqual(once.await_count, 2)
        sleep.assert_awaited_once_with(7)
        saved = await get_preferences()
        await put_preferences(saved | {'status_check_attempts': 1})
        once = AsyncMock(side_effect=httpx.ConnectTimeout('connect'))
        with patch.object(connectors, '_fetch_status_once', once), patch.object(connectors.asyncio, 'sleep', AsyncMock()) as sleep:
            with self.assertRaises(httpx.ConnectTimeout):
                await connectors.fetch_status(self.site)
        self.assertEqual(once.await_count, 1)
        sleep.assert_not_awaited()

    async def test_api_normalizes_retry_limits_and_preserves_other_preferences(self):
        from app.routers.preferences import get_preferences, put_preferences
        prefs = await get_preferences()
        saved = await put_preferences(prefs | {'status_check_attempts': 99, 'status_check_retry_seconds': 0})
        self.assertEqual((saved['status_check_attempts'], saved['status_check_retry_seconds']), (5, 1))
        self.assertEqual(saved['offline_alert_minutes'], prefs['offline_alert_minutes'])
        self.assertEqual(await get_preferences(), saved)


class CheckStateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.SchedulerTests.asyncSetUp(self)
        self.extra = [patch.object(connectors, 'datetime', fixtures.Clock)]
        for p in self.extra: p.start()

    async def asyncTearDown(self):
        for p in reversed(self.extra): p.stop()
        await fixtures.SchedulerTests.asyncTearDown(self)

    async def add_site(self, **kwargs):
        return await fixtures.SchedulerTests.add_site(self, **kwargs)

    async def test_dns_failure_has_unknown_health_preserves_update_counts_and_is_not_offline(self):
        sid = await self.add_site(status='ok', updates_count=7, upd_plugins=7, offline_since=fixtures.NOW)
        with patch.object(connectors, 'fetch_status', AsyncMock(side_effect=dns_error())):
            async with self.sessions() as session:
                site = await session.get(Site, sid)
                await connectors.apply_status(session, site, force=True)
                await session.commit()
                self.assertEqual(site.status, 'dns_error')
                self.assertEqual(site.updates_count, 7)
                self.assertIsNone(site.offline_since)
                kinds = [p['kind'] for p in site_problems(site)]
                self.assertIn('dns', kinds)
                self.assertNotIn('offline', kinds)

    async def test_dns_poll_schedules_recheck_and_sends_no_offline_or_online_notification(self):
        sid = await self.add_site(status='ok')
        notify = AsyncMock(return_value={'email':True,'telegram':True})
        with patch.object(connectors, 'fetch_status', AsyncMock(side_effect=dns_error())), patch.object(worker, 'notify_dispatch', notify):
            await worker.poll_site({'redis':self.redis}, sid)
        notify.assert_not_awaited()
        jobs = await self.redis.queued_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].function, 'poll_site')
        self.assertEqual(jobs[0].args, (sid,))
        async with self.sessions() as session:
            self.assertEqual((await session.get(Site,sid)).status, 'dns_error')

    async def test_preupdate_dns_failure_skips_updates_and_enqueues_check(self):
        sid = await self.add_site(status='ok', auto_update=True, updates_count=7, upd_plugins=7)
        outcome = {}
        with patch.object(connectors, 'fetch_status', AsyncMock(side_effect=dns_error())):
            await worker._update_site({'redis':self.redis}, sid, False, outcome)
        self.assertIn('verifica DNS non riuscita', outcome['text'])
        self.assertEqual(len(await self.redis.queued_jobs()), 1)
        async with self.sessions() as session:
            site = await session.get(Site,sid)
            self.assertEqual(site.status, 'dns_error')
            self.assertEqual(site.updates_count, 7)

    async def test_real_connection_failure_keeps_existing_offline_confirmation_window(self):
        sid = await self.add_site(status='ok')
        notify = AsyncMock()
        with patch.object(connectors, 'fetch_status', AsyncMock(side_effect=httpx.ConnectError('Connection refused'))), patch.object(worker,'notify_dispatch',notify):
            await worker.poll_site({'redis':self.redis}, sid)
        notify.assert_not_awaited()
        async with self.sessions() as session:
            site = await session.get(Site,sid)
            self.assertEqual(site.status,'check_pending')
            self.assertNotIn('offline', [p['kind'] for p in site_problems(site)])
            self.assertIsNotNone(site.offline_since)

    async def test_manual_timeout_is_pending_and_confirmed_only_after_configured_window(self):
        from datetime import timedelta
        sid = await self.add_site(status="ok", updates_count=7)
        with patch.object(connectors, "fetch_status", AsyncMock(side_effect=httpx.ConnectTimeout("Connection timed out"))):
            async with self.sessions() as session:
                site = await session.get(Site, sid)
                await connectors.apply_status(session, site, force=True)
                self.assertEqual(site.status, "check_pending")
                self.assertEqual(site.updates_count, 7)
                self.assertNotIn("offline", [p["kind"] for p in site_problems(site)])
                site.offline_since = fixtures.NOW - timedelta(minutes=5)
                await connectors.apply_status(session, site, force=True)
                self.assertEqual(site.status, "error")

    async def test_zero_window_preserves_immediate_offline_setting(self):
        from app import settings_store
        await settings_store.save_operational_settings({"offline_alert_minutes":0})
        sid = await self.add_site(status="ok")
        with patch.object(connectors, "fetch_status", AsyncMock(side_effect=httpx.ConnectTimeout("Connection timed out"))):
            async with self.sessions() as session:
                site = await session.get(Site, sid)
                await connectors.apply_status(session, site)
                self.assertEqual(site.status, "error")

    async def test_gate_deferral_never_starts_an_offline_episode(self):
        sid = await self.add_site(status="ok")
        with patch.object(connectors, "fetch_status", AsyncMock(side_effect=connectors.CheckDeferred("busy"))):
            async with self.sessions() as session:
                site = await session.get(Site, sid)
                await connectors.apply_status(session, site)
                self.assertEqual(site.status, "check_pending")
                self.assertIsNone(site.offline_since)

    async def test_pending_poll_schedules_recheck_without_offline_notification(self):
        sid = await self.add_site(status="ok")
        notify = AsyncMock()
        with patch.object(connectors, "fetch_status", AsyncMock(side_effect=httpx.ConnectTimeout("Connection timed out"))), patch.object(worker,"notify_dispatch",notify):
            await worker.poll_site({"redis":self.redis}, sid)
        notify.assert_not_awaited()
        self.assertEqual((await self.redis.queued_jobs())[0].args, (sid,))

    async def test_manual_route_enqueues_pending_recheck(self):
        from app.routers import sites as routes
        sid = await self.add_site(status="ok")
        pool = SimpleNamespace(aclose=AsyncMock())
        recheck = AsyncMock()
        with patch.object(connectors, "fetch_status", AsyncMock(side_effect=httpx.ConnectTimeout("Connection timed out"))), \
             patch.object(routes, "create_pool", AsyncMock(return_value=pool)), \
             patch.object(connectors, "schedule_pending_recheck", recheck):
            async with self.sessions() as session:
                result = await routes.refresh_now(sid, session)
        self.assertEqual(result.status, "check_pending")
        recheck.assert_awaited_once_with(pool, sid)

    async def test_successful_check_clears_dns_error_and_old_offline_timer(self):
        sid = await self.add_site(status='dns_error', error='DNS temporary', offline_since=fixtures.NOW)
        with patch.object(connectors,'fetch_status',AsyncMock(return_value={'core':{},'extensions':[]})):
            async with self.sessions() as session:
                site = await session.get(Site,sid)
                await connectors.apply_status(session,site)
                await session.commit()
                self.assertEqual(site.status,'ok')
                self.assertEqual(site.error,'')
                self.assertIsNone(site.offline_since)


class ConnectionPoolTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_is_reused_and_closed_at_shutdown(self):
        await connectors.close_status_client()
        actual = httpx.AsyncClient
        with patch.object(connectors.httpx,'AsyncClient',side_effect=lambda **kwargs: actual(trust_env=False, **kwargs)) as factory:
            first = await connectors.status_client()
            second = await connectors.status_client()
            self.assertIs(first,second)
            self.assertEqual(factory.call_count,1)
            self.assertEqual(factory.call_args.kwargs['limits'].keepalive_expiry,600)
            await worker._shutdown({})
            self.assertTrue(first.is_closed)
            third = await connectors.status_client()
            self.assertIsNot(first,third)
            self.assertEqual(factory.call_count,2)
            await connectors.close_status_client()

    async def test_status_gets_are_limited_to_four_in_flight(self):
        await connectors.close_status_client()
        active = 0
        peak = 0
        entered = asyncio.Event()
        release = asyncio.Event()
        async def get(*args, **kwargs):
            nonlocal active, peak
            active += 1; peak = max(peak, active)
            if active == 4: entered.set()
            await release.wait()
            active -= 1
            return httpx.Response(200, json={})
        client = SimpleNamespace(get=get)
        gate_patch = patch.object(connectors, 'status_slot', unrestricted_slot)
        gate_patch.start(); self.addCleanup(gate_patch.stop)
        tasks = [asyncio.create_task(connectors._status_get(client, 'https://example.test', {}, 10)) for _ in range(12)]
        await asyncio.wait_for(entered.wait(), 1)
        self.assertEqual(active, 4)
        release.set()
        await asyncio.gather(*tasks)
        self.assertEqual(peak, 4)
        await connectors.close_status_client()

    async def test_two_real_http_gets_reuse_one_tcp_connection(self):
        await connectors.close_status_client()
        connections = []
        async def handle(reader, writer):
            connections.append(writer)
            try:
                while True:
                    await reader.readuntil(b'\r\n\r\n')
                    writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nContent-Type: application/json\r\n\r\n{}')
                    await writer.drain()
            except (asyncio.IncompleteReadError, ConnectionError):
                pass
            finally:
                writer.close()
                await writer.wait_closed()
        server = await asyncio.start_server(handle, '127.0.0.1', 0)
        port = server.sockets[0].getsockname()[1]
        actual = httpx.AsyncClient
        try:
            with patch.object(connectors.httpx,'AsyncClient',side_effect=lambda **kwargs: actual(trust_env=False, **kwargs)), patch.object(connectors, 'status_slot', unrestricted_slot):
                client = await connectors.status_client()
                for _ in range(2):
                    response = await connectors._status_get(client, f'http://127.0.0.1:{port}/status', {}, 2)
                    self.assertEqual(response.json(), {})
                self.assertEqual(len(connections), 1)
                await connectors.close_status_client()
        finally:
            server.close()
            await server.wait_closed()
