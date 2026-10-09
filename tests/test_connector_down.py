"""Connettore rimosso o disattivato su un sito che risponde: classificazione dell'errore,
conferma dalla finestra di minuti, avviso "Connettore non risponde" e avviso di ritorno.

Caso reale (9 ottobre 2026): i plugin del sito spenti e il connettore cancellato.
Il ciclo orario degli aggiornamenti prendeva 404 rest_no_route, scriveva l'errore e usciva;
il controllo normale (l'unico che conferma e avvisa) non partiva mai perche' il ciclo
aggiorna last_checked ogni ora: sito rosso nel pannello per due ore, nessun avviso.
"""
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import test_screenshot_schedule as fixtures
import httpx
from sqlalchemy import select

from app import connectors, worker
from app.models import Site, OfflineEpisode, EventLog

T0 = datetime(2026, 10, 9, 9, 13, tzinfo=timezone.utc)   # 11:13 ora italiana


class MovingClock(datetime):
    cur = T0

    @classmethod
    def now(cls, tz=None):
        return cls.cur if tz else cls.cur.replace(tzinfo=None)


def at(minutes: float):
    MovingClock.cur = T0 + timedelta(minutes=minutes)


def http_error(code: int, payload=None, text=None):
    req = httpx.Request("GET", "https://example.test/wp-json/tdpanopticon/v1/status")
    if text is not None:
        resp = httpx.Response(code, text=text, headers={"content-type": "text/html"}, request=req)
    else:
        resp = httpx.Response(code, json=payload if payload is not None else {}, request=req)
    return httpx.HTTPStatusError(f"HTTP {code}", request=req, response=resp)


REST_NO_ROUTE = {"code": "rest_no_route", "message": "Nessun percorso corrisponde", "data": {"status": 404}}
OK_PAYLOAD = {"core": {"current": "6.8.3", "latest": "6.8.3", "update": False}, "extensions": []}


@asynccontextmanager
async def unrestricted_slot(*args, **kwargs):
    yield


class FetchClassificationTests(unittest.IsolatedAsyncioTestCase):
    """Risposte HTTP vere (MockTransport): cosa solleva fetch_status."""

    async def asyncSetUp(self):
        self.site = SimpleNamespace(id=71, name="Sito di prova", cms="wp", url="https://example.test", token="t")
        self.responses = []
        self.requests = []

        async def handler(request):
            self.requests.append(request)
            return self.responses.pop(0)
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.patches = [patch.object(connectors, "status_slot", unrestricted_slot),
                        patch.object(connectors, "status_client", AsyncMock(return_value=self.client))]
        for p in self.patches:
            p.start()
        connectors._WP_REST_STYLE.clear()

    async def asyncTearDown(self):
        for p in reversed(self.patches):
            p.stop()
        await self.client.aclose()
        connectors._WP_REST_STYLE.clear()

    async def test_wp_connector_removed_is_404_rest_no_route_on_both_routes(self):
        self.responses = [httpx.Response(404, json=REST_NO_ROUTE), httpx.Response(404, json=REST_NO_ROUTE)]
        with self.assertRaises(httpx.HTTPStatusError) as cm:
            await connectors.fetch_status(self.site)
        self.assertEqual(len(self.requests), 2)
        reason = connectors.connector_http_reason(cm.exception.response)
        self.assertTrue(reason.startswith(connectors.CONNECTOR_PREFIX))
        self.assertIn("assente o disattivato (HTTP 404)", reason)

    async def test_html_page_instead_of_connector_is_a_connector_reply_error(self):
        page = lambda: httpx.Response(200, text="<html>home</html>", headers={"content-type": "text/html; charset=UTF-8"})
        self.responses = [page(), page()]
        with self.assertRaises(connectors.ConnectorReplyError) as cm:
            await connectors.fetch_status(self.site)
        self.assertIn("risposta senza i dati del connettore (HTTP 200, text/html)", str(cm.exception))
        self.assertEqual(len(self.requests), 2)        # prova anche la forma REST alternativa

    async def test_joomla_plugin_disabled_is_empty_com_ajax(self):
        self.site.cms = "joomla"
        self.responses = [httpx.Response(200, json={"success": True, "message": None, "messages": None, "data": []})]
        with self.assertRaises(connectors.ConnectorReplyError) as cm:
            await connectors.fetch_status(self.site)
        self.assertIn("plugin del connettore non c'è o è disattivato", str(cm.exception))

    def test_reasons_for_codes(self):
        mk = lambda code, **kw: http_error(code, **kw).response
        self.assertIn("accesso rifiutato (HTTP 401)", connectors.connector_http_reason(mk(401)))
        self.assertIn("accesso rifiutato (HTTP 403)", connectors.connector_http_reason(mk(403)))
        self.assertIn("indirizzo del connettore non trovato (HTTP 404)", connectors.connector_http_reason(mk(404, text="<h1>404</h1>")))
        self.assertIn("risposta inattesa", connectors.connector_http_reason(mk(400)))
        for code in (408, 429, 500, 502, 503, 504):
            self.assertIsNone(connectors.connector_http_reason(mk(code)))


class ConnectorDownFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.SchedulerTests.asyncSetUp(self)
        at(0)
        from app import db
        self.extra = [patch.object(worker, "datetime", MovingClock),
                      patch.object(connectors, "datetime", MovingClock),
                      patch.object(db, "SessionLocal", self.sessions)]   # righe del registro nel DB di prova
        for p in self.extra:
            p.start()
        self.notify = AsyncMock(return_value={"email": False, "telegram": True})
        self.np = patch.object(worker, "notify_dispatch", self.notify)
        self.np.start()

    async def asyncTearDown(self):
        self.np.stop()
        for p in reversed(self.extra):
            p.stop()
        await fixtures.SchedulerTests.asyncTearDown(self)

    async def add_site(self, **kwargs):
        return await fixtures.SchedulerTests.add_site(self, **kwargs)

    async def read(self, sid):
        async with self.sessions() as s:
            return await s.get(Site, sid)

    def events(self):
        return [c.args[0] for c in self.notify.await_args_list]

    async def jobs(self):
        return [(j.function, j.args, j.job_id) for j in await self.redis.queued_jobs()]

    async def test_cycle_hands_over_and_alert_arrives_after_the_window(self):
        """Il caso reale dall'inizio alla fine."""
        sid = await self.add_site(status="ok", auto_update=True, last_checked=T0 - timedelta(hours=1))
        gone = AsyncMock(side_effect=http_error(404, REST_NO_ROUTE))
        with patch.object(connectors, "fetch_status", gone):
            outcome = {}
            await worker._update_site({"redis": self.redis}, sid, False, outcome)
            self.assertIn("Connettore: assente o disattivato", outcome["text"])
            site = await self.read(sid)
            self.assertEqual(site.status, "ok")                 # non ancora confermato: pannello com'era
            self.assertIsNotNone(site.offline_since)           # ma la finestra e' partita
            jobs = await self.jobs()
            self.assertEqual([(f, a) for f, a, _ in jobs], [("poll_site", (sid,))])   # ricontrollo programmato
            self.assertEqual(self.events(), [])

            # ricontrolli al minuto: niente avviso dentro la finestra (5 minuti di base)
            for minute in (1, 2, 3, 4):
                at(minute)
                await worker.poll_site({"redis": self.redis}, sid)
                self.assertEqual(self.events(), [])
                self.assertEqual((await self.read(sid)).status, "ok")
            at(5)
            await worker.poll_site({"redis": self.redis}, sid)
            at(6)
            await worker.poll_site({"redis": self.redis}, sid)    # nessun doppione

        self.assertEqual(self.events(), ["connector_down"])
        ctx = self.notify.await_args_list[0].args[1]
        self.assertTrue(ctx["reason"].startswith("assente o disattivato (HTTP 404)"))
        self.assertNotIn("Connettore:", ctx["reason"])
        self.assertEqual(ctx["window_min"], 5)
        site = await self.read(sid)
        self.assertEqual(site.status, "error")
        self.assertTrue(site.offline_notified)
        self.assertTrue(connectors.connector_problem(site.error))
        async with self.sessions() as s:
            # il sito era online: nessun episodio offline nel registro della disponibilita'
            self.assertEqual((await s.execute(select(OfflineEpisode))).scalars().all(), [])
            msgs = [e.message for e in (await s.execute(select(EventLog))).scalars().all()]
        self.assertEqual(sum(m.startswith("Connettore non risponde: assente o disattivato") for m in msgs), 1)

        # connettore rimesso: lo vede il ciclo degli aggiornamenti -> avviso di ritorno e flag spento
        self.notify.reset_mock()
        at(60)
        with patch.object(connectors, "fetch_status", AsyncMock(return_value=OK_PAYLOAD)):
            await worker._update_site({"redis": self.redis}, sid, False, {})
        self.assertEqual(self.events(), ["connector_up"])
        site = await self.read(sid)
        self.assertEqual(site.status, "ok")
        self.assertFalse(site.offline_notified)
        self.assertIsNone(site.offline_since)

    async def test_blip_shorter_than_the_window_never_alerts(self):
        """Connettore assente per un attimo (es. mentre si aggiorna): nessun avviso."""
        sid = await self.add_site(status="ok", auto_update=True)
        with patch.object(connectors, "fetch_status", AsyncMock(side_effect=http_error(404, REST_NO_ROUTE))):
            await worker._update_site({"redis": self.redis}, sid, False, {})
        at(1)
        with patch.object(connectors, "fetch_status", AsyncMock(return_value=OK_PAYLOAD)):
            await worker.poll_site({"redis": self.redis}, sid)
        self.assertEqual(self.events(), [])
        site = await self.read(sid)
        self.assertEqual(site.status, "ok")
        self.assertIsNone(site.offline_since)

    async def test_token_rejected_and_joomla_disabled_get_the_connector_alert(self):
        for cms, error in (("wp", http_error(401, {"code": "rest_forbidden"})),
                           ("joomla", connectors.ConnectorReplyError("Joomla risponde ma il plugin del connettore non c'è o è disattivato (com_ajax)"))):
            self.notify.reset_mock()
            at(0)
            sid = await self.add_site(status="ok", cms=cms, offline_since=T0 - timedelta(minutes=6))
            with patch.object(connectors, "fetch_status", AsyncMock(side_effect=error)):
                await worker.poll_site({"redis": self.redis}, sid)
            self.assertEqual(self.events(), ["connector_down"], cms)
            self.assertTrue((await self.read(sid)).error.startswith(connectors.CONNECTOR_PREFIX))

    async def test_real_outage_still_sends_site_offline(self):
        sid = await self.add_site(status="ok", offline_since=T0 - timedelta(minutes=6))
        with patch.object(connectors, "fetch_status", AsyncMock(side_effect=httpx.ConnectError("Connection refused"))), \
             patch.object(connectors, "probe_home", AsyncMock(return_value=None)):
            await worker.poll_site({"redis": self.redis}, sid)
        self.assertEqual(self.events(), ["site_offline"])
        at(10)
        with patch.object(connectors, "fetch_status", AsyncMock(return_value=OK_PAYLOAD)):
            await worker.poll_site({"redis": self.redis}, sid)
        self.assertEqual(self.events(), ["site_offline", "site_online"])

    async def test_silenced_site_is_confirmed_but_not_notified(self):
        sid = await self.add_site(status="ok", notifications_silenced=True, offline_since=T0 - timedelta(minutes=6))
        with patch.object(connectors, "fetch_status", AsyncMock(side_effect=http_error(404, REST_NO_ROUTE))):
            await worker.poll_site({"redis": self.redis}, sid)
        self.assertEqual(self.events(), [])
        self.assertEqual((await self.read(sid)).status, "error")

    async def test_update_after_failure_alert_does_not_hold_again(self):
        """Gia' avvisato: il ciclo scrive l'errore e non riparte con un'altra finestra."""
        sid = await self.add_site(status="error", error="Connettore: assente o disattivato (HTTP 404)",
                                  offline_notified=True, offline_kind="connector", auto_update=True)
        with patch.object(connectors, "fetch_status", AsyncMock(side_effect=http_error(404, REST_NO_ROUTE))):
            await worker._update_site({"redis": self.redis}, sid, False, {})
        self.assertEqual(await self.jobs(), [])
        self.assertEqual(self.events(), [])

    async def test_silenced_or_undelivered_sites_get_no_extra_requests_every_hour(self):
        """Episodio confermato ma senza avviso consegnato (silenziato, o nessun canale): il ciclo
        orario non riparte con finestre e ricontrolli. Il mancato invio si ritenta col risultato
        che il ciclo ha gia', senza altre richieste al sito."""
        silenced = await self.add_site(status="ok", notifications_silenced=True, auto_update=True,
                                       offline_since=T0 - timedelta(minutes=6))
        failing = await self.add_site(status="ok", auto_update=True, offline_since=T0 - timedelta(minutes=6))
        self.notify.return_value = {"email": False, "telegram": False}
        gone = AsyncMock(side_effect=http_error(404, REST_NO_ROUTE))
        with patch.object(connectors, "fetch_status", gone):
            for sid in (silenced, failing):
                await worker.poll_site({"redis": self.redis}, sid)          # conferma
            self.assertEqual(self.events(), ["connector_down"])            # solo quello non silenziato, non consegnato
            self.assertFalse((await self.read(failing)).offline_notified)
            self.assertEqual((await self.read(failing)).offline_kind, "connector")
            calls = gone.await_count
            at(60)
            for sid in (silenced, failing):
                await worker._update_site({"redis": self.redis}, sid, False, {})
            self.assertEqual(gone.await_count, calls + 2)                  # una lettura per sito, la sua
        self.assertEqual(await self.jobs(), [])                            # nessun ricontrollo in coda
        self.assertEqual(self.events(), ["connector_down", "connector_down"])   # ritentato senza fetch extra
        self.notify.return_value = {"email": False, "telegram": True}

    async def test_tick_safety_net_for_errors_written_elsewhere_and_stuck_flags(self):
        fresh = T0 - timedelta(minutes=10)
        red = await self.add_site(status="error", error="Connettore: accesso rifiutato (HTTP 401)", last_checked=fresh)
        flag = await self.add_site(status="ok", offline_notified=True, offline_kind="connector", last_checked=fresh)
        notified = await self.add_site(status="error", error="HTTP 404", offline_notified=True, last_checked=fresh)
        silenced = await self.add_site(status="error", error="HTTP 404", notifications_silenced=True, last_checked=fresh)
        running = await self.add_site(status="error", error="HTTP 404", offline_since=T0 - timedelta(minutes=2), last_checked=fresh)
        confirmed = await self.add_site(status="error", error="Connettore: x", offline_kind="connector", last_checked=fresh)
        normal = await self.add_site(status="ok", last_checked=fresh)
        await worker.tick({"redis": self.redis})
        self.assertEqual([a[0] for f, a, _ in await self.jobs()], [red])   # solo l'errore mai confermato
        self.assertTrue((await self.jobs())[0][2].startswith("confirm:"))
        # il sito gia' a posto riceve l'avviso di ritorno del tipo giusto senza nessuna richiesta
        self.assertEqual(self.events(), ["connector_up"])
        site = await self.read(flag)
        self.assertFalse(site.offline_notified)
        self.assertEqual(site.offline_kind, "")
        await worker.tick({"redis": self.redis})           # stesso giro di 10 minuti: nessun doppione
        self.assertEqual(len(await self.jobs()), 1)
        self.assertEqual(self.events(), ["connector_up"])
        del notified, silenced, running, confirmed, normal

    async def test_html_page_with_dead_home_is_a_real_outage(self):
        """403/404 HTML (hosting sospeso, sito cancellato): scaduta la finestra la home non
        risponde -> "sito non raggiungibile" ed episodio offline, non "connettore"."""
        sid = await self.add_site(status="ok", offline_since=T0 - timedelta(minutes=6))
        probe = AsyncMock(return_value=(403, 0.4))
        with patch.object(connectors, "fetch_status", AsyncMock(side_effect=http_error(403, text="<h1>Account suspended</h1>"))), \
             patch.object(connectors, "probe_home", probe):
            await worker.poll_site({"redis": self.redis}, sid)
        probe.assert_awaited_once()
        self.assertEqual(self.events(), ["site_offline"])
        site = await self.read(sid)
        self.assertFalse(connectors.connector_problem(site.error))
        self.assertEqual(site.offline_kind, "site")
        async with self.sessions() as s:
            self.assertEqual(len((await s.execute(select(OfflineEpisode))).scalars().all()), 1)

    async def test_html_page_with_live_home_is_the_connector_and_probes_only_once(self):
        sid = await self.add_site(status="ok", offline_since=T0 - timedelta(minutes=6))
        probe = AsyncMock(return_value=(200, 0.8))
        with patch.object(connectors, "fetch_status", AsyncMock(side_effect=http_error(404, text="<h1>Not found</h1>"))), \
             patch.object(connectors, "probe_home", probe):
            await worker.poll_site({"redis": self.redis}, sid)
            site = await self.read(sid)
            self.assertIn("indirizzo del connettore non trovato (HTTP 404)", site.error)
            self.assertIn("home: HTTP 200", site.error)
            at(70)
            await worker.poll_site({"redis": self.redis}, sid)   # episodio confermato: niente altre controprove
        self.assertEqual(probe.await_count, 1)
        self.assertEqual(self.events(), ["connector_down"])
        self.assertTrue(connectors.connector_problem((await self.read(sid)).error))
        async with self.sessions() as s:
            self.assertEqual((await s.execute(select(OfflineEpisode))).scalars().all(), [])

    async def test_html_page_inside_the_window_does_not_probe(self):
        sid = await self.add_site(status="ok", auto_update=True)
        probe = AsyncMock(return_value=(200, 0.8))
        with patch.object(connectors, "fetch_status", AsyncMock(side_effect=http_error(404, text="<h1>Not found</h1>"))), \
             patch.object(connectors, "probe_home", probe):
            outcome = {}
            await worker._update_site({"redis": self.redis}, sid, False, outcome)
        probe.assert_not_awaited()
        self.assertIn("HTTP 404", outcome["text"])
        self.assertEqual(len(await self.jobs()), 1)

    async def test_connector_episode_then_real_outage_alerts_again_with_its_own_start(self):
        sid = await self.add_site(status="ok", offline_since=T0 - timedelta(minutes=6))
        with patch.object(connectors, "fetch_status", AsyncMock(side_effect=http_error(404, REST_NO_ROUTE))):
            await worker.poll_site({"redis": self.redis}, sid)
        self.assertEqual(self.events(), ["connector_down"])
        self.assertIsNone((await self.read(sid)).offline_since)
        # tre ore dopo il sito va giu' davvero: finestra sua, poi "non raggiungibile"
        outage = T0 + timedelta(hours=3)
        refused = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
        with patch.object(connectors, "fetch_status", refused), patch.object(connectors, "probe_home", AsyncMock(return_value=None)):
            for minute in range(0, 7):
                MovingClock.cur = outage + timedelta(minutes=minute)
                await worker.poll_site({"redis": self.redis}, sid)
        self.assertEqual(self.events(), ["connector_down", "site_offline"])
        async with self.sessions() as s:
            ep = (await s.execute(select(OfflineEpisode))).scalars().one()
        started = ep.started_at.replace(tzinfo=timezone.utc) if ep.started_at.tzinfo is None else ep.started_at
        self.assertGreaterEqual(started, outage)          # non l'inizio del problema del connettore
        # torna tutto: un solo avviso di ritorno, del tipo dell'ultimo avviso
        MovingClock.cur = outage + timedelta(minutes=30)
        with patch.object(connectors, "fetch_status", AsyncMock(return_value=OK_PAYLOAD)):
            await worker.poll_site({"redis": self.redis}, sid)
        self.assertEqual(self.events(), ["connector_down", "site_offline", "site_online"])

    async def test_outage_then_site_answers_without_connector_closes_the_outage(self):
        sid = await self.add_site(status="ok", offline_since=T0 - timedelta(minutes=6))
        with patch.object(connectors, "fetch_status", AsyncMock(side_effect=httpx.ConnectError("Connection refused"))), \
             patch.object(connectors, "probe_home", AsyncMock(return_value=None)):
            await worker.poll_site({"redis": self.redis}, sid)
        at(40)
        with patch.object(connectors, "fetch_status", AsyncMock(side_effect=http_error(404, REST_NO_ROUTE))):
            await worker.poll_site({"redis": self.redis}, sid)
        self.assertEqual(self.events(), ["site_offline", "connector_down"])
        async with self.sessions() as s:
            ep = (await s.execute(select(OfflineEpisode))).scalars().one()
        self.assertIsNotNone(ep.ended_at)                   # il sito risponde: episodio offline chiuso
        at(90)
        with patch.object(connectors, "fetch_status", AsyncMock(return_value=OK_PAYLOAD)):
            await worker.poll_site({"redis": self.redis}, sid)
        self.assertEqual(self.events(), ["site_offline", "connector_down", "connector_up"])

    async def test_blip_of_another_kind_during_a_connector_episode_waits_for_the_window(self):
        """Connettore gia' avvisato, poi un 522 di passaggio: niente "non raggiungibile" al primo
        colpo; se al minuto dopo torna il solito errore del connettore, nessun avviso."""
        sid = await self.add_site(status="error", error="Connettore: assente o disattivato (HTTP 404)",
                                  offline_notified=True, offline_kind="connector", auto_update=True)
        with patch.object(connectors, "fetch_status", AsyncMock(side_effect=http_error(522, text="<h1>522</h1>"))):
            await worker._update_site({"redis": self.redis}, sid, False, {})
        self.assertEqual(self.events(), [])
        site = await self.read(sid)
        self.assertEqual(site.offline_kind, "connector")
        self.assertTrue(connectors.connector_problem(site.error))     # il pannello resta com'era
        self.assertIsNotNone(site.offline_since)
        self.assertEqual(len(await self.jobs()), 1)
        at(1)
        with patch.object(connectors, "fetch_status", AsyncMock(side_effect=http_error(404, REST_NO_ROUTE))):
            await worker.poll_site({"redis": self.redis}, sid)
        self.assertEqual(self.events(), [])
        site = await self.read(sid)
        self.assertIsNone(site.offline_since)                        # finestra chiusa: nessun inizio vecchio
        async with self.sessions() as s:
            self.assertEqual((await s.execute(select(OfflineEpisode))).scalars().all(), [])

    async def test_back_online_is_not_sent_if_the_site_fell_again_meanwhile(self):
        """tick lavora su una fotografia: se nel frattempo un altro lavoro ha confermato un nuovo
        episodio, niente "di nuovo raggiungibile" e il nuovo episodio resta com'e'."""
        sid = await self.add_site(status="ok", offline_notified=True, offline_kind="site")
        async with self.sessions() as s:
            stale = await s.get(Site, sid)                           # quello che vede tick: ok
            async with self.sessions() as other:                     # intanto un altro lavoro conferma
                fresh = await other.get(Site, sid)
                fresh.status, fresh.error = "error", "HTTP 500"
                await other.commit()
            await worker._notify_back(s, stale)
        self.assertEqual(self.events(), [])
        async with self.sessions() as s:
            site = await s.get(Site, sid)
            self.assertEqual((site.status, site.offline_kind, site.offline_notified), ("error", "site", True))

    async def test_zero_minute_window_alerts_from_the_cycle_without_rechecks(self):
        from app import settings_store
        await settings_store.save_operational_settings({"offline_alert_minutes": 0})
        sid = await self.add_site(status="ok", auto_update=True)
        gone = AsyncMock(side_effect=http_error(404, REST_NO_ROUTE))
        with patch.object(connectors, "fetch_status", gone):
            await worker._update_site({"redis": self.redis}, sid, False, {})
        self.assertEqual(gone.await_count, 1)
        self.assertEqual(self.events(), ["connector_down"])
        self.assertEqual(await self.jobs(), [])
        site = await self.read(sid)
        self.assertEqual((site.status, site.offline_kind, site.offline_notified), ("error", "connector", True))

    async def test_two_jobs_at_once_send_one_alert_and_one_back_online(self):
        import asyncio
        sid = await self.add_site(status="error", error="Connettore: assente o disattivato (HTTP 404)",
                                  offline_kind="connector")
        async with self.sessions() as a, self.sessions() as b:
            sa, sb = await a.get(Site, sid), await b.get(Site, sid)
            await asyncio.gather(worker._confirm_down(a, sa), worker._confirm_down(b, sb))
        self.assertEqual(self.events(), ["connector_down"])
        async with self.sessions() as s:
            site = await s.get(Site, sid)
            site.status, site.error = "ok", ""
            await s.commit()
        async with self.sessions() as a, self.sessions() as b:
            sa, sb = await a.get(Site, sid), await b.get(Site, sid)
            await asyncio.gather(worker._notify_back(a, sa), worker._notify_back(b, sb))
        self.assertEqual(self.events(), ["connector_down", "connector_up"])

    async def test_check_now_button_starts_the_confirmation(self):
        from app.routers import sites as routes
        sid = await self.add_site(status="ok")
        pool = SimpleNamespace(aclose=AsyncMock())
        recheck = AsyncMock()
        with patch.object(connectors, "fetch_status", AsyncMock(side_effect=http_error(404, REST_NO_ROUTE))), \
             patch.object(routes, "create_pool", AsyncMock(return_value=pool)), \
             patch.object(connectors, "schedule_pending_recheck", recheck):
            async with self.sessions() as session:
                result = await routes.refresh_now(sid, session)
        self.assertEqual(result.status, "error")
        self.assertTrue(result.error.startswith("Connettore: assente o disattivato"))
        recheck.assert_awaited_once_with(pool, sid)
        self.assertIsNotNone((await self.read(sid)).offline_since)

    async def test_update_that_breaks_the_connector_is_confirmed_too(self):
        """Ricontrollo finale dopo gli aggiornamenti: se il connettore non risponde piu',
        parte la conferma invece di restare li' fino al ciclo dopo."""
        sid = await self.add_site(status="error", error="Connettore: risposta senza i dati del connettore (HTTP 200, text/html)")
        async with self.sessions() as session:
            site = await session.get(Site, sid)
            self.assertTrue(await connectors.start_confirmation(session, site, self.redis))
            self.assertFalse(await connectors.start_confirmation(session, site, self.redis))   # gia' in corso
        self.assertEqual(len(await self.jobs()), 1)


if __name__ == "__main__":
    unittest.main()
