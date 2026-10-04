"""No bursts across scans; transient recovery and the configured day interval."""
import asyncio
import json
import socket
import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import test_screenshot_schedule as fixtures
import httpx
from app import worker
from app.domain_requests import SourceGate, SourceError, retry_delay

NOW = fixtures.NOW
FUTURE = NOW + timedelta(days=365)


class RetryClassificationTests(unittest.TestCase):
    def error(self, status, headers=None):
        response = httpx.Response(status, headers=headers, request=httpx.Request('GET', 'https://who.is/whois/example.it'))
        return httpx.HTTPStatusError('HTTP error', request=response.request, response=response)

    def test_retries_transient_network_errors_with_backoff(self):
        for error in (httpx.ReadTimeout('slow'), httpx.ConnectError('DNS'), RuntimeError('Timeout WHOIS whois.nic.it'),
                      socket.gaierror(socket.EAI_AGAIN, 'Temporary failure'), ConnectionResetError(104, 'reset'),
                      SourceError('page incomplete', transient=True)):
            self.assertEqual(retry_delay(error, 1), 15)
            self.assertEqual(retry_delay(error, 2), 30)

    def test_bad_domains_non_exposed_dates_and_challenges_are_not_hammered(self):
        for error in (SourceError('CAPTCHA'), RuntimeError('WHOIS non espone la scadenza'),
                      socket.gaierror(socket.EAI_NONAME, 'No such name'), self.error(404), self.error(403)):
            self.assertIsNone(retry_delay(error, 1))

    def test_rate_limit_waits_at_least_retry_after(self):
        self.assertEqual(retry_delay(self.error(429, {'Retry-After':'60'}), 1), 60)
        self.assertEqual(retry_delay(self.error(503), 2), 30)
        self.assertIsNone(retry_delay(self.error(429, {'Retry-After':'3600'}), 1))


class GateTests(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_scans_have_one_active_request_per_source_and_spacing(self):
        clock = [100.0]
        active = [0]
        starts, waits = [], []
        async def sleep(seconds):
            waits.append(seconds)
            clock[0] += seconds
            await asyncio.sleep(0)
        gate = SourceGate(3, clock=lambda: clock[0], sleep=sleep)
        async def fetch():
            active[0] += 1
            self.assertEqual(active[0], 1)
            starts.append(clock[0])
            await asyncio.sleep(0)
            active[0] -= 1
            return 'ok'
        result = await asyncio.gather(*(gate.call('who.is', fetch) for _ in range(3)))
        self.assertEqual(result, ['ok'] * 3)
        self.assertEqual(starts, [100, 103, 106])
        self.assertEqual(waits, [3, 3])

    async def test_source_wait_does_not_consume_network_timeout(self):
        gate = SourceGate(0)
        entered = asyncio.Event()
        release = asyncio.Event()
        async def first():
            entered.set()
            await release.wait()
        first_task = asyncio.create_task(gate.call('registry', first, timeout=1))
        await entered.wait()
        second_task = asyncio.create_task(gate.call('registry', AsyncMock(return_value='ok'), timeout=.01))
        await asyncio.sleep(.025)
        self.assertFalse(second_task.done())
        release.set()
        self.assertEqual(await second_task, 'ok')
        await first_task

    async def test_slow_whois_does_not_block_another_source(self):
        gate = SourceGate(0)
        release = asyncio.Event()
        entered = asyncio.Event()
        async def slow():
            entered.set()
            await release.wait()
        pending = asyncio.create_task(gate.call('registry', slow))
        await entered.wait()
        self.assertEqual(await gate.call('who.is', AsyncMock(return_value='ok')), 'ok')
        release.set()
        await pending

    async def test_cancelled_or_failed_call_releases_source_slot(self):
        gate = SourceGate(0)
        with self.assertRaises(asyncio.CancelledError):
            await gate.call('registry', AsyncMock(side_effect=asyncio.CancelledError()))
        self.assertEqual(await gate.call('registry', AsyncMock(return_value='ok')), 'ok')


class LookupRetryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clock = patch.object(worker, 'datetime', fixtures.Clock)
        self.clock.start()
        self.gate = patch.object(worker, '_DOMAIN_GATE', SourceGate(0))
        self.gate.start()
        self.scan_gate = patch.object(worker, "_DOMAIN_SCAN_GATE", SourceGate(0))
        self.scan_gate.start()
        self.attempts = patch.object(worker, "_DOMAIN_ATTEMPTS", 3)
        self.attempts.start()
        worker._LAST_INFO.clear()
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(404)))

    async def asyncTearDown(self):
        await self.client.aclose()
        worker._LAST_INFO.clear()
        self.attempts.stop()
        self.scan_gate.stop()
        self.gate.stop()
        self.clock.stop()

    async def test_retry_recovers_registry_and_records_the_successful_attempt(self):
        async def recovered(domain):
            worker._LAST_INFO[domain] = {'registrar':'Current Registrar'}
            return domain, FUTURE
        failures = [RuntimeError('Timeout WHOIS whois.nic.it'), RuntimeError('Timeout WHOIS whois.nic.it')]
        async def fetch(domain):
            if failures:
                raise failures.pop(0)
            return await recovered(domain)
        query = AsyncMock(side_effect=fetch)
        sleep = AsyncMock()
        with patch.object(worker, '_whois_expiry', query), patch.object(worker.asyncio, 'sleep', sleep):
            domain, expiry = await worker._domain_expiry(self.client, 'example.it')
        self.assertEqual(expiry, FUTURE)
        self.assertEqual(query.await_count, 3)
        self.assertEqual([c.args[0] for c in sleep.await_args_list], [15, 30])
        selected = next(x for x in worker._LAST_INFO[domain]['source_summary']['sources'] if x['selected'])
        self.assertEqual(selected['attempts'], 3)
        self.assertEqual(worker._LAST_INFO[domain]['registrar'], 'Current Registrar')
        self.assertNotIn('check_warning', worker._LAST_INFO[domain])

    async def test_persistent_timeout_stops_after_three_attempts_and_keeps_source_evidence(self):
        query = AsyncMock(side_effect=RuntimeError('Timeout WHOIS whois.nic.it'))
        with patch.object(worker, '_whois_expiry', query), patch.object(worker.asyncio, 'sleep', AsyncMock()):
            with self.assertRaisesRegex(RuntimeError, 'Timeout WHOIS'):
                await worker._domain_expiry(self.client, 'example.it')
        self.assertEqual(query.await_count, 3)
        evidence = worker._LAST_INFO['example.it']['source_summary']['sources']
        self.assertEqual(next(x for x in evidence if x['source']=='Registro WHOIS')['attempts'], 3)

    async def test_temporary_who_is_page_recovers_without_repeating_successful_registry(self):
        query = AsyncMock(return_value=('example.it', FUTURE))
        page = AsyncMock(side_effect=[SourceError('page incomplete', transient=True), ('example.it', FUTURE)])
        with patch.object(worker, '_whois_expiry', query), patch.object(worker, '_who_is_expiry', page), patch.object(worker.asyncio, 'sleep', AsyncMock()) as sleep:
            await worker._domain_expiry(self.client, 'example.it')
        self.assertEqual(query.await_count, 1)
        self.assertEqual(page.await_count, 2)
        sleep.assert_awaited_once_with(15)

    async def test_explicit_captcha_is_not_retried(self):
        query = AsyncMock(return_value=('example.it', FUTURE))
        page = AsyncMock(side_effect=SourceError('CAPTCHA'))
        with patch.object(worker, '_whois_expiry', query), patch.object(worker, '_who_is_expiry', page), patch.object(worker.asyncio, 'sleep', AsyncMock()) as sleep:
            await worker._domain_expiry(self.client, 'example.it')
        self.assertEqual(page.await_count, 1)
        sleep.assert_not_awaited()

    async def test_rdap_transient_errors_keep_their_type_for_retry(self):
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(503)))
        try:
            with self.assertRaises(httpx.HTTPStatusError):
                await worker._rdap_expiry(client, 'example.com')
        finally:
            await client.aclose()


class IntervalTests(unittest.TestCase):
    def site(self, checked):
        from types import SimpleNamespace
        return SimpleNamespace(domain_checked_at=checked, domain_name='example.it', domain_expires_at=FUTURE, domain_check_error='')

    def test_exact_configured_day_is_due_despite_last_lookup_finishing_after_daily_cron(self):
        previous = NOW - timedelta(days=7) + timedelta(minutes=2)
        self.assertTrue(worker._domain_lookup_due(self.site(previous), 'example.it', NOW, 7))

    def test_not_due_before_configured_day(self):
        self.assertFalse(worker._domain_lookup_due(self.site(NOW-timedelta(days=6)), 'example.it', NOW, 7))

    def test_one_day_setting_rechecks_on_the_next_day(self):
        self.assertTrue(worker._domain_lookup_due(self.site(NOW-timedelta(days=1)+timedelta(hours=1)), 'example.it', NOW, 1))

    def test_failed_lookup_is_retried_daily_even_with_longer_interval(self):
        site = self.site(NOW-timedelta(days=1))
        site.domain_check_error = 'Timeout WHOIS'
        self.assertTrue(worker._domain_lookup_due(site, 'example.it', NOW, 7))

    def test_long_scan_timeout_applies_to_the_daily_cron_too(self):
        job = next(x for x in worker.WorkerSettings.cron_jobs if x.name == 'cron:domain_expiry_scan')
        self.assertEqual(job.timeout_s, 21600)


class DomainPacingTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_domain_at_a_time_across_manual_and_automatic_calls_with_30_second_pause(self):
        clock = [100.0]
        starts = []
        async def sleep(seconds):
            clock[0] += seconds
            await asyncio.sleep(0)
        gate = SourceGate(30, clock=lambda:clock[0], sleep=sleep)
        active = [0]
        async def lookup(client, domain):
            active[0] += 1
            self.assertEqual(active[0], 1)
            starts.append(clock[0])
            await asyncio.sleep(0)
            active[0] -= 1
            return domain, FUTURE
        with patch.object(worker, '_DOMAIN_SCAN_GATE', gate), patch.object(worker, '_compare_domain_sources', lookup):
            await asyncio.gather(*(worker._domain_expiry(None, name) for name in ['a.it','b.it','c.it']))
        self.assertEqual(starts, [100,130,160])

    async def test_pause_follows_completion_even_when_previous_domain_was_slow(self):
        clock = [100.0]
        starts = []
        async def sleep(seconds):
            clock[0] += seconds
        gate = SourceGate(30, clock=lambda:clock[0], sleep=sleep)
        async def lookup(client, domain):
            starts.append(clock[0])
            clock[0] += 40
            return domain, FUTURE
        with patch.object(worker, '_DOMAIN_SCAN_GATE', gate), patch.object(worker, '_compare_domain_sources', lookup):
            await worker._domain_expiry(None, 'a.it')
            await worker._domain_expiry(None, 'b.it')
        self.assertEqual(starts, [100,170])


if __name__ == '__main__':
    unittest.main()
