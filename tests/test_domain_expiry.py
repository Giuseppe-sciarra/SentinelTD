"""Registry selection, renewed-domain persistence and sequential scan regressions."""
import asyncio
import json
import unittest
from datetime import timedelta, timezone
from unittest.mock import AsyncMock, patch

import test_screenshot_schedule as fixtures
import httpx
from starlette.requests import Request
from app import worker
from app.models import Site
from app.problems import site_problems
from app.routers import changes, expiries

NOW = fixtures.NOW
OLD = NOW - timedelta(days=3)
NEW = NOW + timedelta(days=365)


class RegistryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.body = 'Domain: vivaioesempio.it\nExpire Date: 2026-09-30\n'
        self.status = 200
        async def handler(request):
            self.requests.append(request)
            return httpx.Response(self.status, text=self.body)
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
        self.clock = patch.object(worker, 'datetime', fixtures.Clock)
        self.clock.start()
        worker._LAST_INFO.clear()
        self.gate_patch = patch.object(worker, "_DOMAIN_GATE", worker.SourceGate(pause_seconds=0))
        self.gate_patch.start()
        self.scan_gate_patch = patch.object(worker, "_DOMAIN_SCAN_GATE", worker.SourceGate(pause_seconds=0))
        self.scan_gate_patch.start()
        self.attempts_patch = patch.object(worker, "_DOMAIN_ATTEMPTS", 1)
        self.attempts_patch.start()

    async def asyncTearDown(self):
        self.attempts_patch.stop()
        self.scan_gate_patch.stop()
        self.gate_patch.stop()
        self.clock.stop()
        worker._LAST_INFO.clear()
        await self.client.aclose()

    async def test_it_reads_renewal_from_registry_before_stale_web_cache(self):
        query = AsyncMock(return_value='Domain: vivaioesempio.it\nExpire Date: 2027-09-30\n\nRegistrar\n Organization: Test Registrar\n\nNameservers\n ns1.example.test\n ns2.example.test\n')
        with patch.object(worker, '_whois_query', query):
            domain, date = await worker._domain_expiry(self.client, 'www.vivaioesempio.it')
        self.assertEqual(domain, 'vivaioesempio.it')
        self.assertEqual(date.date().isoformat(), '2027-09-30')
        query.assert_awaited_once_with('whois.nic.it', 'vivaioesempio.it', timeout=20.0)
        self.assertEqual(len(self.requests), 2)
        self.assertEqual({r.url.host for r in self.requests}, {"who.is", "www.whois.com"})
        self.assertEqual(worker._LAST_INFO[domain]['registrar'], 'Test Registrar')
        self.assertIn('ns1.example.test', worker._LAST_INFO[domain]['nameservers'])

    async def test_https_fallback_still_reads_a_future_renewal_if_port_43_fails(self):
        self.body = '<pre>Domain: vivaioesempio.it\nExpire Date: 2027-09-30\n</pre>'
        with patch.object(worker, '_whois_query', AsyncMock(side_effect=RuntimeError('TCP/43 blocked'))):
            domain, date = await worker._domain_expiry(self.client, 'vivaioesempio.it')
        self.assertEqual(date.year, 2027)
        self.assertNotIn('check_warning', worker._LAST_INFO[domain])
        self.assertEqual(worker._LAST_INFO[domain]["source_summary"]["selected"], "whois.com")
        self.assertEqual(len(self.requests), 2)

    async def test_expired_secondary_date_is_unverified_when_registry_is_unreachable(self):
        with patch.object(worker, '_whois_query', AsyncMock(side_effect=RuntimeError('DNS failed'))):
            domain, date = await worker._domain_expiry(self.client, 'vivaioesempio.it')
        self.assertLess(date, NOW)
        self.assertIn('va verificata', worker._LAST_INFO[domain]['check_warning'])
        self.assertIn('DNS failed', worker._LAST_INFO[domain]['check_warning'])

    async def test_captcha_is_an_error_and_never_a_successful_whois_result(self):
        self.body = '<div id="security">Security Check CAPTCHA</div>'
        with patch.object(worker, '_whois_query', AsyncMock(side_effect=RuntimeError('TCP/43 blocked'))):
            with self.assertRaisesRegex(RuntimeError, 'TCP/43 blocked.*CAPTCHA'):
                await worker._domain_expiry(self.client, 'vivaioesempio.it')

    async def test_gtld_rdap_still_has_priority_and_parses_utc(self):
        self.body = json.dumps({'ldhName':'example.com','events':[{'eventAction':'expiration','eventDate':'2027-10-01T00:00:00Z'}]})
        query = AsyncMock(side_effect=RuntimeError("TCP/43 blocked"))
        with patch.object(worker, '_whois_query', query):
            domain, date = await worker._domain_expiry(self.client, 'shop.example.com')
        self.assertEqual(domain, 'example.com')
        self.assertEqual(date.tzinfo, timezone.utc)
        query.assert_awaited()
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(str(self.requests[0].url), 'https://rdap.org/domain/example.com')

    async def test_registry_whois_precedes_web_when_rdap_fails(self):
        self.status = 404
        query = AsyncMock(return_value='Registry Expiry Date: 2027-10-01T00:00:00Z\n')
        with patch.object(worker, '_whois_server_for_tld', AsyncMock(return_value='registry.test')), patch.object(worker, '_whois_query', query):
            _, date = await worker._domain_expiry(self.client, 'example.com')
        self.assertEqual(date.year, 2027)
        self.assertEqual(len(self.requests), 3)
        query.assert_awaited_once_with('registry.test', 'example.com', timeout=20.0)

    async def test_renewed_date_beats_an_expired_event_from_yesterday(self):
        self.body = json.dumps({'ldhName':'example.com','events':[
            {'eventAction':'expiration','eventDate':(NOW-timedelta(days=1)).isoformat()},
            {'eventAction':'expiration','eventDate':NEW.isoformat()}]})
        with patch.object(worker, '_whois_query', AsyncMock(side_effect=RuntimeError('blocked'))):
            _, date = await worker._domain_expiry(self.client, 'example.com')
        self.assertEqual(date, NEW)


class ScanTests(unittest.IsolatedAsyncioTestCase):
    add_site = fixtures.SchedulerTests.add_site

    async def asyncSetUp(self):
        await fixtures.SchedulerTests.asyncSetUp(self)
        actual = httpx.AsyncClient
        self.client_patch = patch.object(worker.httpx, 'AsyncClient', lambda **kwargs: actual(trust_env=False, **kwargs))
        self.client_patch.start()

    async def asyncTearDown(self):
        self.client_patch.stop()
        await fixtures.SchedulerTests.asyncTearDown(self)

    async def add_domain(self, **kwargs):
        return await self.add_site(**({'name':'Vivaio Mares','url':'https://vivaioesempio.it','status':'ok',
            'domain_name':'vivaioesempio.it','domain_expires_at':OLD,'domain_checked_at':NOW-timedelta(days=1),
            'domain_check_error':'old lookup error','domain_alert_state':'old cycle','domain_renew':'yes',
            'domain_renew_note':'Client renewal decision'} | kwargs))

    async def test_renewal_updates_all_subdomains_clears_error_and_preserves_decision(self):
        ids = [await self.add_domain(), await self.add_domain(url='https://shop.vivaioesempio.it')]
        lookup = AsyncMock(return_value=('vivaioesempio.it', NEW))
        with patch.object(worker, '_domain_expiry', lookup):
            await worker.domain_expiry_scan({'redis':self.redis}, True, ids[0])
        lookup.assert_awaited_once()
        async with self.sessions() as s:
            for sid in ids:
                site = await s.get(Site, sid)
                self.assertEqual(site.domain_expires_at.date(), NEW.date())
                self.assertEqual(site.domain_check_error, '')
                self.assertEqual(site.domain_renew, 'yes')
                self.assertEqual(site.domain_renew_note, 'Client renewal decision')
                self.assertIn(NEW.date().isoformat(), site.domain_alert_state)
                self.assertNotIn('old cycle', site.domain_alert_state)

    async def test_comparison_evidence_is_saved_for_all_subdomains_and_returned_by_api(self):
        ids = [await self.add_domain(), await self.add_domain(url='https://shop.vivaioesempio.it')]
        summary = {'selected':'who.is', 'reason':'newer_record', 'sources':[
            {'source':'who.is', 'expires_at':NEW.isoformat(), 'updated_at':NOW.isoformat(), 'selected':True}]}
        async def lookup(client, domain):
            worker._LAST_INFO[domain] = {'source_summary':summary}
            return domain, NEW
        with patch.object(worker, '_domain_expiry', lookup):
            await worker.domain_expiry_scan({'redis':self.redis}, True)
        async with self.sessions() as s:
            for sid in ids:
                self.assertEqual(json.loads((await s.get(Site, sid)).domain_check_details), summary)
            rows = await expiries.list_domain_expiries(s)
            self.assertEqual(rows[0]['source_summary'], summary)
            self.assertFalse(rows[0]['renewal_pending'])

    async def test_failed_lookup_keeps_date_records_failure_and_sends_no_expiry_reminder(self):
        sid = await self.add_domain()
        dispatch = AsyncMock()
        with patch.object(worker, '_domain_expiry', AsyncMock(side_effect=RuntimeError('WHOIS DNS failed'))), patch.object(worker, '_dispatch_expiry', dispatch):
            await worker.domain_expiry_scan({'redis':self.redis}, True)
        dispatch.assert_not_awaited()
        async with self.sessions() as s:
            site = await s.get(Site, sid)
            self.assertEqual(site.domain_expires_at.date(), OLD.date())
            self.assertEqual(site.domain_check_error, 'WHOIS DNS failed')
            self.assertEqual(site.domain_checked_at.replace(tzinfo=timezone.utc), NOW)
            self.assertEqual(site.domain_alert_state, 'old cycle')
            self.assertNotIn('domain', [p['kind'] for p in site_problems(site, now=NOW)])

    async def test_previous_failure_retries_next_day_even_for_far_future_expiry(self):
        sid = await self.add_domain(domain_expires_at=NEW)
        lookup = AsyncMock(return_value=('vivaioesempio.it', NEW))
        with patch.object(worker, '_domain_expiry', lookup):
            await worker.domain_expiry_scan({'redis':self.redis})
        lookup.assert_awaited_once()
        async with self.sessions() as s:
            self.assertEqual((await s.get(Site, sid)).domain_check_error, '')

    async def test_healthy_far_future_domain_obeys_scan_interval(self):
        await self.add_domain(domain_expires_at=NEW, domain_check_error='')
        lookup = AsyncMock()
        with patch.object(worker, '_domain_expiry', lookup):
            await worker.domain_expiry_scan({'redis':self.redis})
        lookup.assert_not_awaited()

    async def test_expired_secondary_result_cannot_replace_a_confirmed_future_date(self):
        sid = await self.add_domain(domain_expires_at=NEW)
        async def lookup(client, domain):
            worker._LAST_INFO[domain] = {'check_warning':'Secondary date unverified'}
            return domain, OLD
        with patch.object(worker, '_domain_expiry', lookup):
            await worker.domain_expiry_scan({'redis':self.redis}, True)
        async with self.sessions() as s:
            site = await s.get(Site, sid)
            self.assertEqual(site.domain_expires_at.date(), NEW.date())
            self.assertEqual(site.domain_check_error, 'Secondary date unverified')
            self.assertEqual(site.domain_alert_state, 'old cycle')

    async def test_sequential_scan_commits_each_domain_before_starting_next(self):
        first = await self.add_domain(url='https://aaa.it', domain_name='aaa.it')
        await self.add_domain(url='https://zzz.it', domain_name='zzz.it')
        order=[]
        async def lookup(client, domain):
            order.append(domain)
            if domain == 'zzz.it':
                async with self.sessions() as s:
                    row = await s.get(Site, first)
                    self.assertEqual(row.domain_expires_at.date(), NEW.date())
                    self.assertEqual(row.domain_check_error, '')
            return domain, NEW
        with patch.object(worker, '_domain_expiry', lookup):
            await worker.domain_expiry_scan({'redis':self.redis}, True, None, True)
        self.assertEqual(order, ['aaa.it','zzz.it'])

    async def test_automatic_scan_commits_first_domain_before_starting_second(self):
        first = await self.add_domain(url='https://aaa.it', domain_name='aaa.it')
        await self.add_domain(url='https://zzz.it', domain_name='zzz.it')
        async def lookup(client, domain):
            if domain == 'zzz.it':
                async with self.sessions() as s:
                    self.assertEqual((await s.get(Site, first)).domain_expires_at.date(), NEW.date())
            return domain, NEW
        with patch.object(worker, '_domain_expiry', lookup):
            await worker.domain_expiry_scan({'redis':self.redis}, True)

    async def test_selected_domains_use_one_job_instead_of_filling_worker_slots(self):
        await self.add_domain(url='https://aaa.it', domain_name='aaa.it')
        await self.add_domain(url='https://zzz.it', domain_name='zzz.it')
        enqueue = AsyncMock()
        with patch.object(expiries, '_enqueue', enqueue):
            async with self.sessions() as s:
                result = await expiries.scan_domains_now({'domains':['zzz.it','aaa.it','aaa.it']}, s)
        enqueue.assert_awaited_once_with('domain_expiry_scan', True, None, True, ['aaa.it','zzz.it'], _job_id='domain-whois-sequential')
        self.assertEqual(result['queued'], 2)

    async def test_selected_scan_only_looks_up_selected_domains(self):
        await self.add_domain(url='https://aaa.it', domain_name='aaa.it')
        sid = await self.add_domain(url='https://zzz.it', domain_name='zzz.it')
        lookup = AsyncMock(return_value=('aaa.it', NEW))
        with patch.object(worker, '_domain_expiry', lookup):
            await worker.domain_expiry_scan({'redis':self.redis}, True, None, True, ['aaa.it'])
        lookup.assert_awaited_once()
        async with self.sessions() as s:
            self.assertEqual((await s.get(Site, sid)).domain_expires_at.date(), OLD.date())

    async def test_whois_pacing_settings_are_persisted_without_restart(self):
        from app.settings_store import save_operational_settings, get_operational_settings
        await save_operational_settings({'domain_scan_days':3, 'domain_pause_seconds':45, 'domain_source_attempts':2, 'domain_parallel_lookups':8})
        values = await get_operational_settings()
        self.assertEqual(values['domain_scan_days'], 3)
        self.assertEqual(values['domain_pause_seconds'], 45)
        self.assertEqual(values['domain_source_attempts'], 2)
        self.assertEqual(values['domain_parallel_lookups'], 1)
        await self.add_domain()
        with patch.object(worker, '_domain_expiry', AsyncMock(return_value=('vivaioesempio.it', NEW))):
            await worker.domain_expiry_scan({'redis':self.redis}, True)
        self.assertEqual(worker._DOMAIN_SCAN_GATE.pause, 45)
        self.assertEqual(worker._DOMAIN_ATTEMPTS, 2)

    async def test_scan_endpoint_queues_one_sequential_job_for_all_domains(self):
        enqueue = AsyncMock()
        with patch.object(expiries, '_enqueue', enqueue):
            async with self.sessions() as s:
                result = await expiries.scan_domains_now({'sequential':True}, s)
        enqueue.assert_awaited_once_with('domain_expiry_scan', True, None, True, _job_id='domain-whois-sequential')
        self.assertTrue(result['sequential'])

    async def test_second_sequential_scan_is_rejected_while_the_first_is_queued(self):
        from fastapi import HTTPException
        with patch.object(expiries, '_enqueue', AsyncMock(return_value=None)):
            async with self.sessions() as s:
                with self.assertRaises(HTTPException) as caught:
                    await expiries.scan_domains_now({'sequential':True}, s)
        self.assertEqual(caught.exception.status_code, 409)

    async def test_older_scan_result_cannot_overwrite_a_newer_renewal(self):
        sid = await self.add_domain(domain_checked_at=NOW, domain_expires_at=NEW, domain_check_error='')
        await worker._persist_domain_updates([{'id':sid,'domain_name':'vivaioesempio.it',
            'domain_checked_at':NOW-timedelta(hours=1),'domain_expires_at':OLD,
            'domain_check_error':'','reset_alert_state':True}])
        async with self.sessions() as s:
            row = await s.get(Site, sid)
            self.assertEqual(row.domain_expires_at.date(), NEW.date())
            self.assertEqual(row.domain_alert_state, 'old cycle')

    async def test_grouped_api_uses_latest_date_instead_of_first_site(self):
        await self.add_domain(domain_checked_at=NOW-timedelta(days=2))
        await self.add_domain(url='https://shop.vivaioesempio.it', domain_expires_at=NEW, domain_checked_at=NOW, domain_check_error='')
        async with self.sessions() as s:
            rows = await expiries.list_domain_expiries(s)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['site_count'], 2)
        self.assertTrue(rows[0]['expires_at'].startswith(NEW.date().isoformat()))
        self.assertEqual(rows[0]['error'], '')

    async def test_changes_detects_whois_only_update_and_returns_304_when_unchanged(self):
        sid = await self.add_domain()
        def request(etag=None):
            return Request({'type':'http','headers':[(b'if-none-match', etag.encode())] if etag else []})
        async with self.sessions() as s:
            before = await changes.changes(request(), sid, s)
            same = await changes.changes(request(before.headers['etag']), sid, s)
            self.assertEqual(same.status_code, 304)
            row = await s.get(Site, sid)
            row.domain_checked_at = NOW
            row.domain_expires_at = NEW
            row.domain_check_error = 'WHOIS "quoted" error\nsecond line'
            await s.commit()
            after = await changes.changes(request(before.headers['etag']), sid, s)
        self.assertEqual(after.status_code, 200)
        self.assertNotEqual(before.headers['etag'], after.headers['etag'])
        self.assertNotEqual(json.loads(before.body)['rev'], json.loads(after.body)['rev'])
        self.assertIn('quoted', json.loads(after.body)['site'])
        self.assertRegex(after.headers['etag'], r'^"[a-f0-9]{64}"$')


if __name__ == '__main__':
    unittest.main()
