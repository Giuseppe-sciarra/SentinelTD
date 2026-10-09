"""Public page formats, source freshness and .it renewal-period behavior."""
import asyncio
import json
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import test_screenshot_schedule as fixtures
import httpx
from app import worker
from app.domain_sources import (parse_date, parse_who_is, reconcile, whois_record_info,
                                renewal_pending, site_renewal_pending)
from app.problems import site_problems

NOW = fixtures.NOW
OLD = NOW - timedelta(days=3)
NEW = NOW + timedelta(days=365)


def who_is_page(domain='example.it', expires='October 1, 2027', updated=None, fetched=None, status='ok'):
    return f'''<h1>{domain}</h1><h2>Registrar Information</h2><dt>Registrar</dt><dd>Test Registrar</dd>
      <h2>Important Dates</h2><dt>Created</dt><dd>October 1, 2019</dd>
      <dt>Updated</dt><dd>{updated or 'October 2, 2026'}</dd>
      <dt>Expires</dt><dd>{expires}</dd>
      <p>WHOIS data last fetched <time datetime="{fetched or NOW.isoformat()}">October 3, 2026</time></p>
      <h2>Nameservers</h2><table><tr><td>ns1.example.test</td><td>192.0.2.1</td></tr></table>
      <h2>Domain Status</h2><li>{status}</li><h2>Similar Domains</h2><p>other.test</p>'''


def candidate(source, expiry, priority=0, authoritative=False, updated=None, snapshot=None):
    return dict(source=source, expiry=expiry, priority=priority, authoritative=authoritative,
                updated_at=updated, snapshot_at=snapshot)


class ParserTests(unittest.TestCase):
    def test_public_who_is_english_dates_and_snapshot_time(self):
        expiry, info = parse_who_is(who_is_page(), 'example.it')
        self.assertEqual(expiry.date().isoformat(), '2027-10-01')
        self.assertEqual(info['updated_at'].date().isoformat(), '2026-10-02')
        self.assertEqual(info['snapshot_at'], NOW)
        self.assertEqual(info['registrar'], 'Test Registrar')
        self.assertEqual(info['nameservers'], 'ns1.example.test')
        self.assertEqual(info['statuses'], ['ok'])

    def test_datetime_attributes_preserve_precision(self):
        body = who_is_page(expires='<time datetime="2027-10-01T06:25:33Z">October 1, 2027</time>')
        expiry, _ = parse_who_is(body, 'example.it')
        self.assertEqual(expiry.hour, 6)
        self.assertEqual(expiry.minute, 25)

    def test_rejects_different_domain(self):
        with self.assertRaisesRegex(RuntimeError, 'altro dominio'):
            parse_who_is(who_is_page('wrong.it'), 'example.it')

    def test_captcha_and_script_dates_are_not_whois(self):
        for body in ('<h1>Security Check</h1>CAPTCHA',
                     '<h1>example.it</h1><script>Important Dates\nExpires\n2027-10-01</script>',
                     '<h1>example.it</h1><h2>Certificate</h2><dt>Expires</dt><dd>2027-10-01</dd>'):
            with self.assertRaises(RuntimeError):
                parse_who_is(body, 'example.it')

    def test_ignores_created_date_when_expiry_is_missing(self):
        with self.assertRaises(RuntimeError):
            parse_who_is(who_is_page(expires='Unavailable'), 'example.it')

    def test_expiry_outside_date_section_cannot_replace_missing_expiry(self):
        body = who_is_page(expires='Unavailable')+'<h2>Certificate</h2><dt>Expires</dt><dd>2030-10-01</dd>'
        with self.assertRaises(RuntimeError):
            parse_who_is(body, 'example.it')

    def test_contact_update_is_not_domain_record_update(self):
        text = 'Domain: example.it\nLast Update: 2026-09-01\n\nRegistrant\n Last Update: 2026-10-03\n'
        self.assertEqual(whois_record_info(text)['updated_at'].date().isoformat(), '2026-09-01')

    def test_english_months_do_not_require_locale_change(self):
        self.assertEqual(parse_date('February 29, 2028').date().isoformat(), '2028-02-29')
        self.assertIsNone(parse_date('February 29, 2027'))
        self.assertEqual(parse_date('2027-10-01T10:00:00+02:00').hour, 8)


class ReconciliationTests(unittest.TestCase):
    def test_newer_record_wins_even_when_its_expiry_is_shorter(self):
        items = [candidate('old', NEW+timedelta(days=365), updated=NOW-timedelta(days=10)),
                 candidate('who.is', NEW, 1, updated=NOW-timedelta(hours=1))]
        chosen, reason, error = reconcile(items, NOW)
        self.assertEqual(chosen['source'], 'who.is')
        self.assertEqual(reason, 'newer_record')
        self.assertFalse(error)

    def test_fresh_who_is_record_can_supersede_registry_date(self):
        items = [candidate('registry', OLD, authoritative=True, updated=NOW-timedelta(days=5)),
                 candidate('who.is', NEW, 1, updated=NOW-timedelta(hours=1))]
        self.assertEqual(reconcile(items, NOW)[0]['expiry'], NEW)

    def test_live_registry_beats_undated_later_web_expiry(self):
        items = [candidate('registry', NEW, authoritative=True), candidate('who.is', NEW+timedelta(days=365), 1)]
        self.assertEqual(reconcile(items, NOW)[0]['source'], 'registry')

    def test_comparable_snapshots_choose_latest_snapshot(self):
        items = [candidate('who.is', NEW, snapshot=NOW-timedelta(hours=1)),
                 candidate('web', OLD, 1, snapshot=NOW-timedelta(days=10))]
        self.assertEqual(reconcile(items, NOW)[1], 'newer_snapshot')

    def test_unknown_freshness_conflict_is_not_resolved_by_max_expiry(self):
        items = [candidate('who.is', OLD), candidate('web', NEW, 1)]
        chosen, reason, warning = reconcile(items, NOW)
        self.assertEqual(reason, 'conflict')
        self.assertEqual(chosen['expiry'], OLD)
        self.assertIn('Fonti discordanti', warning)

    def test_identical_record_timestamps_with_different_dates_are_a_conflict(self):
        items = [candidate('one', OLD, updated=NOW), candidate('two', NEW, 1, updated=NOW)]
        self.assertEqual(reconcile(items, NOW)[1], 'conflict')

    def test_same_calendar_day_is_agreement_despite_precision(self):
        items = [candidate('RDAP', NEW.replace(hour=12), authoritative=True), candidate('web', NEW.replace(hour=0), 1)]
        self.assertEqual(reconcile(items, NOW)[1], 'agreement')

    def test_future_update_timestamp_cannot_override_live_registry(self):
        items = [candidate('registry', NEW, authoritative=True, updated=NOW),
                 candidate('web', OLD, 1, updated=NOW+timedelta(days=100))]
        self.assertEqual(reconcile(items, NOW)[0]['source'], 'registry')

    def test_disagreeing_live_sources_without_record_dates_are_unverified(self):
        items = [candidate('RDAP', OLD, authoritative=True, snapshot=NOW),
                 candidate('WHOIS', NEW, 1, authoritative=True, snapshot=NOW)]
        self.assertEqual(reconcile(items, NOW)[1], 'conflict')

    def test_third_party_copies_are_not_majority_votes(self):
        items = [candidate('registry', NEW, authoritative=True), candidate('who.is', OLD, 1), candidate('web', OLD, 2)]
        self.assertEqual(reconcile(items, NOW)[0]['source'], 'registry')


class RenewalTests(unittest.TestCase):
    def summary(self, **kwargs):
        return {'sources':[{'selected':True, 'statuses':['ok / autoRenewPeriod'], 'snapshot_at':NOW.isoformat()} | kwargs]}

    def test_it_auto_renew_is_a_state_not_a_fabricated_future_date(self):
        self.assertTrue(renewal_pending(self.summary(), 'example.it', OLD, NOW))
        self.assertFalse(renewal_pending(self.summary(), 'example.com', OLD, NOW))
        self.assertFalse(renewal_pending(self.summary(), 'example.it', NOW-timedelta(days=15), NOW))
        self.assertFalse(renewal_pending(self.summary(), 'example.it', NEW, NOW))

    def test_stale_or_inactive_renewal_is_not_hidden(self):
        self.assertFalse(renewal_pending(self.summary(snapshot_at=(NOW-timedelta(days=3)).isoformat()), 'example.it', OLD, NOW))
        self.assertFalse(renewal_pending(self.summary(statuses=['inactive / dnsHold / autoRenewPeriod']), 'example.it', OLD, NOW))
        self.assertFalse(renewal_pending(self.summary() | {'warning':'Conflict'}, 'example.it', OLD, NOW))

    def test_panel_problems_do_not_classify_grace_period_as_expired(self):
        site = SimpleNamespace(enabled=True, status='ok', error='', diag={}, php_version='', domain_name='example.it',
                               domain_expires_at=OLD, domain_check_error='', domain_check_details=json.dumps(self.summary()))
        self.assertTrue(site_renewal_pending(site, NOW))
        self.assertNotIn('domain', [x['kind'] for x in site_problems(site, now=NOW)])
        site.domain_check_details = ''
        self.assertIn('domain', [x['kind'] for x in site_problems(site, now=NOW)])


class MultiSourceLookupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.page = who_is_page(updated=NOW.isoformat())
        async def handler(request):
            self.requests.append(request.url.host)
            body = self.page if request.url.host == 'who.is' else '<pre>Domain: example.it\nExpire Date: 2026-09-30\nLast Update: 2026-09-29\n</pre>'
            return httpx.Response(200, text=body)
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
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

    async def test_reads_all_sources_and_selects_newer_who_is_record(self):
        registry = 'Domain: example.it\nExpire Date: 2026-09-30\nLast Update: 2026-09-29\n'
        with patch.object(worker, '_whois_query', AsyncMock(return_value=registry)):
            domain, expiry = await worker._domain_expiry(self.client, 'www.example.it')
        self.assertEqual(self.requests, ['who.is', 'www.whois.com'])
        self.assertEqual(expiry.year, 2027)
        info = worker._LAST_INFO[domain]
        self.assertEqual(info['registrar'], 'Test Registrar')
        self.assertEqual(info['source_summary']['selected'], 'who.is')
        self.assertEqual(len(info['source_summary']['sources']), 3)
        self.assertEqual(sum(x['selected'] for x in info['source_summary']['sources']), 1)

    async def test_failing_source_does_not_erase_successful_registry_metadata(self):
        self.page = '<h1>CAPTCHA</h1>'
        registry = 'Domain: example.it\nExpire Date: 2027-10-01\nRegistrar: Registry Registrar\n'
        with patch.object(worker, '_whois_query', AsyncMock(return_value=registry)):
            domain, _ = await worker._domain_expiry(self.client, 'example.it')
        info = worker._LAST_INFO[domain]
        self.assertEqual(info['registrar'], 'Registry Registrar')
        self.assertTrue(any(x.get('error') for x in info['source_summary']['sources']))
        self.assertNotIn('check_warning', info)

    async def test_discordant_cached_sources_are_unverified_if_registry_fails(self):
        self.page = who_is_page(updated='Unavailable')
        with patch.object(worker, '_whois_query', AsyncMock(side_effect=RuntimeError('blocked'))):
            domain, _ = await worker._domain_expiry(self.client, 'example.it')
        self.assertEqual(worker._LAST_INFO[domain]['source_summary']['reason'], 'conflict')
        self.assertIn('Fonti discordanti', worker._LAST_INFO[domain]['check_warning'])

    async def test_cancelled_job_is_not_swallowed_as_source_failure(self):
        with patch.object(worker, '_whois_query', AsyncMock(side_effect=asyncio.CancelledError())):
            with self.assertRaises(asyncio.CancelledError):
                await worker._domain_expiry(self.client, 'example.it')
        self.assertFalse(self.requests)

    async def test_fresh_who_is_autorenew_survives_unreachable_registry(self):
        self.page = who_is_page(expires=OLD.isoformat(), status='ok / autoRenewPeriod')
        async def whois_com(*args):
            raise RuntimeError('CAPTCHA')
        with patch.object(worker, '_whois_query', AsyncMock(side_effect=RuntimeError('blocked'))), patch.object(worker, '_web_whois_expiry', whois_com):
            domain, expiry = await worker._domain_expiry(self.client, 'example.it')
        info = worker._LAST_INFO[domain]
        self.assertEqual(expiry, OLD)
        self.assertTrue(info['source_summary']['renewal_pending'])
        self.assertNotIn('check_warning', info)


if __name__ == '__main__':
    unittest.main()
