"""Modalita' firewall (2.49.0): quando un firewall blocca Sentinel, e' il connettore a chiamarlo.

Si prova il giro completo con un Redis finto e il database SQLite: il connettore che si fa
sentire, il passaggio automatico alla modalita' firewall, lo stato letto da quello che manda
il sito, gli aggiornamenti messi in coda e presi dal sito, il ritorno alla strada diretta.
"""
import asyncio
import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import test_screenshot_schedule  # noqa: F401  (percorso di app e variabili d'ambiente di prova)
import httpx
from fakeredis import FakeServer
from fakeredis.aioredis import FakeRedis
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from app import connectors, relay, worker
from app.db import Base
from app.models import Site
from app.routers import agent
from test_connector_down import OK_PAYLOAD, unrestricted_slot
from test_firewall import CF_CHALLENGE as CF_PAGE, SG_CHALLENGE, html

TOKEN = "t" * 48
NOW = lambda: datetime.now(timezone.utc)  # noqa: E731


def request(token=TOKEN):
    headers = [(b"x-sentinel-token", token.encode())] if token else []
    return Request({"type": "http", "method": "POST", "path": "/api/agent/relay", "headers": headers,
                    "client": ("203.0.113.9", 1234), "query_string": b""})


class RelayBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.redis = FakeRedis(server=FakeServer())
        relay.set_client(self.redis)
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions() as s:
            site = Site(name="Prima Piuma", url="https://www.primapiuma.test", cms="wp", token=TOKEN,
                        connector_version="2.41.0")
            s.add(site)
            await s.commit()
            self.site_id = site.id
        self.patches = [
            patch.object(agent, "SessionLocal", self.sessions),
            patch.object(relay, "enqueue_apply", AsyncMock()),
            patch.object(relay, "_enqueue", AsyncMock()),
            patch.object(relay, "POLL", 0.05),
            patch("app.connectors._wp_auto_updates_choice", AsyncMock(return_value="block")),
        ]
        for p in self.patches:
            p.start()
        relay._HUB.update(url="https://sentinel.example.test", at=10**12)

    async def asyncTearDown(self):
        for p in reversed(self.patches):
            p.stop()
        relay._HUB.update(url="", at=0.0)
        await self.engine.dispose()
        await self.redis.aclose()
        relay.set_client(None)

    async def site(self):
        async with self.sessions() as s:
            return await s.get(Site, self.site_id)

    async def set_site(self, **kw):
        async with self.sessions() as s:
            site = await s.get(Site, self.site_id)
            for k, v in kw.items():
                setattr(site, k, v)
            await s.commit()

    async def connector(self, **body):
        """Una chiamata del connettore all'endpoint del pannello."""
        body.setdefault("url", "https://primapiuma.test")
        return await agent.agent_relay.__wrapped__(request(), body)


class EndpointTests(RelayBase):
    async def test_ping_records_contact_and_answers_direct(self):
        out = await self.connector(kind="ping", connector="2.41.1")
        self.assertEqual(out["mode"], "direct")
        self.assertEqual(out["jobs"], [])
        self.assertEqual((out["tick"], out["ping"], out["status_every"]),
                         (relay.TICK_SECONDS, relay.PING_SECONDS, relay.STATUS_SECONDS))
        site = await self.site()
        self.assertIsNotNone(site.relay_seen_at)
        self.assertEqual(site.connector_version, "2.41.1")

    async def test_wrong_token_or_cloned_site_is_refused(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as cm:
            await agent.agent_relay.__wrapped__(request("x" * 48), {"url": "https://primapiuma.test"})
        self.assertEqual(cm.exception.status_code, 403)
        with self.assertRaises(HTTPException) as cm:
            await agent.agent_relay.__wrapped__(request(""), {"url": "https://primapiuma.test"})
        self.assertEqual(cm.exception.status_code, 401)
        # copia di staging con lo stesso token: non parla al posto del sito vero
        with self.assertRaises(HTTPException) as cm:
            await self.connector(url="https://staging.latuademo.test")
        self.assertEqual(cm.exception.status_code, 403)
        self.assertIsNone((await self.site()).relay_seen_at)

    async def test_report_in_relay_mode_saves_status_and_delivers_jobs(self):
        await self.set_site(relay_mode=True)
        job_id = await relay.push_job(self.site_id, "update", {"type": "plugin", "slug": "woocommerce"})
        out = await self.connector(kind="report", status=OK_PAYLOAD)
        self.assertEqual(out["mode"], "relay")
        self.assertEqual([j["id"] for j in out["jobs"]], [job_id])
        self.assertEqual(out["jobs"][0]["slug"], "woocommerce")
        self.assertEqual(await relay.fresh_status(self.site_id), OK_PAYLOAD)
        relay.enqueue_apply.assert_awaited_with(self.site_id)
        # in modalita' diretta lo stato non si tiene e i lavori restano dove sono
        await self.set_site(relay_mode=False)
        await relay.push_job(self.site_id, "refresh", {})
        out = await self.connector(kind="report", status={"extensions": [], "core": {}})
        self.assertEqual((out["mode"], out["jobs"]), ("direct", []))

    async def test_job_round_trip_with_the_connector(self):
        await self.set_site(relay_mode=True, relay_seen_at=NOW())
        site = await self.site()

        async def fake_connector():
            for _ in range(100):
                out = await self.connector(kind="report", status=OK_PAYLOAD)
                if out["jobs"]:
                    job = out["jobs"][0]
                    res = {"ok": True, "new": "9.5.1", "error": ""}
                    await self.connector(kind="report", status=OK_PAYLOAD, results=[{"id": job["id"], "result": res}])
                    return job
                await asyncio.sleep(0.02)

        task = asyncio.create_task(fake_connector())
        res = await worker._update_one(site, "plugin", "woocommerce", expected="9.5.1")
        job = await task
        self.assertEqual(job["action"], "update")
        self.assertEqual((job["type"], job["slug"], job["expected"]), ("plugin", "woocommerce", "9.5.1"))
        self.assertTrue(res["ok"])
        self.assertEqual(res["new"], "9.5.1")

    async def test_job_not_taken_in_time_is_withdrawn(self):
        await self.set_site(relay_mode=True, relay_seen_at=NOW())
        site = await self.site()
        with patch.object(relay, "PICKUP_SECONDS", 0.2):
            res = await worker._update_one(site, "plugin", "woocommerce")
        self.assertTrue(res["relay_timeout"])
        self.assertFalse(res["ok"])
        self.assertEqual(await self.redis.llen(relay._k("jobs", self.site_id)), 0)   # non lo fara' piu' tardi

    async def test_first_ping_of_a_blocked_site_switches_on_at_once(self):
        # caso reale (primapiuma): sito bloccato da SiteGround, connettore 2.41 appena installato a
        # mano. Prima: ping -> "diretta", poi il controllo accendeva la modalita' e il connettore la
        # scopriva al ping dopo (fino a un'ora). Ora si accende in questa chiamata.
        await self.set_site(connector_version="2.40.0", status="error",
                            error="Connettore: bloccato da SiteGround davanti al sito (HTTP 202): installa a mano…")
        with patch("app.eventlog.record_now", AsyncMock()):
            out = await self.connector(kind="ping", connector="2.41.2")
        self.assertEqual(out["mode"], "relay")
        site = await self.site()
        self.assertTrue(site.relay_mode)
        self.assertEqual(site.connector_version, "2.41.2")
        self.assertTrue(await relay.switching(self.site_id))

    async def test_first_ping_of_a_normal_site_stays_direct(self):
        await self.set_site(status="error", error="HTTP 500")
        out = await self.connector(kind="ping", connector="2.41.2")
        self.assertEqual(out["mode"], "direct")
        self.assertFalse((await self.site()).relay_mode)

    async def test_ping_never_takes_jobs(self):
        await self.set_site(relay_mode=True)
        await relay.push_job(self.site_id, "uninstall", {"type": "plugin", "slug": "hello"})
        out = await self.connector(kind="ping")
        self.assertEqual((out["mode"], out["jobs"]), ("relay", []))
        self.assertEqual(await self.redis.llen(relay._k("jobs", self.site_id)), 1)

    async def test_another_copy_of_the_site_is_refused(self):
        from fastapi import HTTPException
        await self.connector(kind="ping", fp="aaaa")
        with self.assertRaises(HTTPException) as cm:
            await self.connector(kind="ping", fp="bbbb")      # vecchio server, copia locale...
        self.assertEqual(cm.exception.status_code, 409)
        await self.redis.set(relay._k("fpseen", self.site_id), "1")   # la copia nota tace da anni
        out = await self.connector(kind="ping", fp="bbbb")    # trasloco vero: accettato
        self.assertEqual(out["mode"], "direct")

    async def test_job_not_started_goes_back_to_the_queue(self):
        await self.set_site(relay_mode=True)
        job_id = await relay.push_job(self.site_id, "update", {"type": "plugin", "slug": "a"})
        out = await self.connector(kind="report", status=OK_PAYLOAD)
        self.assertEqual(out["jobs"][0]["id"], job_id)
        await self.connector(kind="report", status=OK_PAYLOAD, nojobs=True,
                             results=[{"id": job_id, "result": {"_requeue": True}}])
        out = await self.connector(kind="report", status=OK_PAYLOAD)
        self.assertEqual([j["id"] for j in out["jobs"]], [job_id])

    async def test_waiter_returns_when_relay_mode_is_cleared(self):
        await self.set_site(relay_mode=True, relay_seen_at=NOW())
        site = await self.site()
        with patch.object(relay, "PICKUP_SECONDS", 0.3):
            task = asyncio.create_task(relay.call(site, "update", {"type": "plugin", "slug": "a"}))
            await asyncio.sleep(0.1)
            await relay.clear(self.site_id)                  # la strada diretta si e' riaperta
            self.assertIsNone(await asyncio.wait_for(task, 8))    # 3 s di tolleranza (job appena preso)

    async def test_no_status_apply_while_an_update_is_running(self):
        await self.set_site(relay_mode=True)
        await relay.mark_busy(self.site_id)
        relay.enqueue_apply.reset_mock()
        await self.connector(kind="report", status=OK_PAYLOAD)
        relay.enqueue_apply.assert_not_awaited()

    async def test_joomla_without_finish_request_gets_no_jobs_and_says_so(self):
        await self.set_site(relay_mode=True, relay_seen_at=NOW(), cms="joomla", connector_version="1.43.0")
        await self.connector(kind="report", status=OK_PAYLOAD, jobs_ok=False, nojobs=True)
        site = await self.site()
        res = await worker._update_one(site, "component", "com_akeeba")
        self.assertTrue(res["relay_timeout"])
        self.assertIn("fastcgi_finish_request", res["error"])
        self.assertEqual(await self.redis.llen(relay._k("jobs", self.site_id)), 0)

    async def test_result_for_another_site_is_ignored(self):
        self.assertFalse(await relay.post_result(self.site_id, "999-abc", {"ok": True}))

    async def test_install_file_only_for_its_site(self):
        await self.set_site(relay_mode=True)
        job_id = await relay.push_job(self.site_id, "install", {"kind": "plugin"}, file=b"PK zip")
        out = await self.connector(kind="report", status=OK_PAYLOAD)
        self.assertEqual(out["jobs"][0]["download"], f"/api/agent/relay/file/{job_id}")
        resp = await agent.agent_relay_file.__wrapped__(request(), job_id)
        self.assertEqual(resp.body, b"PK zip")
        # un clone registrato nel pannello con lo stesso token non ruba lo zip, ne' lo perde
        async with self.sessions() as s:
            s.add(Site(name="Clone", url="https://staging.primapiuma.test", cms="wp", token=TOKEN))
            await s.commit()
        resp = await agent.agent_relay_file.__wrapped__(request(), job_id)
        self.assertEqual(resp.body, b"PK zip")
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            await agent.agent_relay_file.__wrapped__(request(), "999-" + job_id.split("-", 1)[1])


class FetchTests(RelayBase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.responses, self.requests = [], []

        async def handler(req):
            self.requests.append(req)
            return self.responses.pop(0)
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True,
                                        event_hooks={"request": [connectors._strip_token_offsite]})
        self.fetch_patches = [patch.object(connectors, "status_slot", unrestricted_slot),
                              patch.object(connectors, "status_client", AsyncMock(return_value=self.client)),
                              patch("app.eventlog.record_now", AsyncMock()),
                              patch("app.db.SessionLocal", self.sessions)]
        for p in self.fetch_patches:
            p.start()
        connectors._WP_REST_STYLE.clear()

    async def asyncTearDown(self):
        for p in reversed(self.fetch_patches):
            p.stop()
        await self.client.aclose()
        await super().asyncTearDown()

    async def test_firewall_switches_to_what_the_connector_sends(self):
        await self.set_site(relay_seen_at=NOW())          # il connettore 2.41 fa gia' i suoi ping
        await relay.save_status(self.site_id, OK_PAYLOAD)
        site = await self.site()
        self.responses = [html(202, SG_CHALLENGE)]
        data = await connectors.fetch_status(site)
        self.assertEqual(data, OK_PAYLOAD)
        self.assertTrue(site.relay_mode)
        self.assertEqual(self.requests[0].headers["x-sentinel-hub"], "https://sentinel.example.test")

    async def test_firewall_without_contact_explains_what_to_do(self):
        await self.set_site(connector_version="2.40.0")
        site = await self.site()
        self.responses = [html(202, SG_CHALLENGE)]
        with self.assertRaises(connectors.FirewallBlocked) as cm:
            await connectors.fetch_status(site)
        self.assertIn("installa a mano sul sito il connettore WordPress 2.41.0 o successivo", str(cm.exception))
        # connettore che non sa chiamare: niente modalita' firewall (ne' etichetta ne' evento)
        self.assertFalse(site.relay_mode)

        # connettore nuovo che non si e' ancora fatto sentire: "da confermare", niente avvisi
        site.connector_version = "2.41.0"
        self.responses = [html(202, SG_CHALLENGE)]
        with self.assertRaises(connectors.RelayStarting):
            await connectors.fetch_status(site)
        self.assertTrue(issubclass(connectors.RelayStarting, connectors.CheckDeferred))

        # mezz'ora dopo, ancora muto: si dice cosa controllare
        await self.redis.delete(relay._k("since", self.site_id))
        self.responses = [html(202, SG_CHALLENGE)]
        with self.assertRaises(connectors.FirewallBlocked) as cm:
            await connectors.fetch_status(site)
        self.assertIn("il connettore non chiama Sentinel", str(cm.exception))

        relay._HUB.update(url="")
        self.responses = [html(202, SG_CHALLENGE)]
        with self.assertRaises(connectors.FirewallBlocked) as cm:
            await connectors.fetch_status(site)
        self.assertIn("imposta l'indirizzo pubblico del pannello", str(cm.exception))

    async def test_switch_is_saved_even_if_the_check_is_rolled_back(self):
        # poll_site annulla le modifiche del sito durante la finestra di conferma: la modalita'
        # firewall deve restare, altrimenti il connettore non la scopre mai
        site = await self.site()
        self.responses = [html(202, SG_CHALLENGE)]
        with self.assertRaises(connectors.RelayStarting):
            await connectors.fetch_status(site)
        self.assertTrue((await self.site()).relay_mode)
        out = await self.connector(kind="ping")
        self.assertEqual(out["mode"], "relay")

    async def test_just_switched_waits_for_the_first_report(self):
        await self.set_site(relay_seen_at=NOW())
        site = await self.site()
        self.responses = [html(403, CF_PAGE, server="cloudflare")]
        with self.assertRaises(connectors.RelayStarting):
            await connectors.fetch_status(site)
        # dopo la mezz'ora senza stato: connettore muto
        await self.redis.delete(relay._k("since", self.site_id))
        await self.redis.set(relay._k("direct", self.site_id), "1")
        with self.assertRaises(connectors.RelaySilent):
            await connectors.fetch_status(site)

    async def test_relay_mode_reads_the_saved_status_without_calling_the_site(self):
        await self.set_site(relay_mode=True, relay_seen_at=NOW())
        await self.redis.set(relay._k("direct", self.site_id), "1")     # strada diretta gia' riprovata
        await relay.save_status(self.site_id, OK_PAYLOAD)
        site = await self.site()
        self.assertEqual(await connectors.fetch_status(site, force=True), OK_PAYLOAD)
        self.assertEqual(await connectors.fetch_status(site, force=True), OK_PAYLOAD)
        self.assertEqual(self.requests, [])
        jobs = [json.loads(j) for j in await self.redis.lrange(relay._k("jobs", self.site_id), 0, -1)]
        self.assertEqual([j["action"] for j in jobs], ["refresh"])      # un solo ricalcolo in coda

    async def test_silent_connector_is_a_connector_problem(self):
        await self.set_site(relay_mode=True, relay_seen_at=NOW() - timedelta(minutes=50))
        await self.redis.set(relay._k("direct", self.site_id), "1")
        site = await self.site()
        with self.assertRaises(connectors.RelaySilent) as cm:
            await connectors.fetch_status(site)
        # ambiguo: sito giu' o cron fermo, decide la controprova sulla home (disponibilita' giusta)
        self.assertTrue(cm.exception.ambiguous)
        self.assertIn("non si fa sentire da 50 minuti", str(cm.exception))

    async def test_direct_route_back_ends_relay_mode(self):
        await self.set_site(relay_mode=True, relay_seen_at=NOW())
        await relay.save_status(self.site_id, {"extensions": [], "core": {}})
        site = await self.site()
        self.responses = [httpx.Response(200, json=OK_PAYLOAD)]
        self.assertEqual(await connectors.fetch_status(site), OK_PAYLOAD)
        self.assertFalse(site.relay_mode)
        self.assertIsNone(await relay.load_status(self.site_id))

    async def test_ordinary_errors_never_switch_to_relay(self):
        # connettore 2.41 che fa i suoi ping: una manutenzione, un 429, un 403 generico, un
        # server lento restano quelli di sempre
        await self.set_site(relay_seen_at=NOW())
        site = await self.site()
        cases = [
            lambda: html(503, "<h1>Briefly unavailable for scheduled maintenance</h1>"),
            lambda: html(429, "<h1>Too Many Requests</h1>"),
            lambda: html(403, "<h1>Forbidden</h1>"),
            lambda: html(200, "<html>home</html>"),
        ]
        for make in cases:
            connectors._WP_REST_STYLE.clear()
            self.responses = [make(), make()]
            with self.assertRaises(Exception) as cm:
                await connectors.fetch_status(site)
            self.assertNotIsInstance(cm.exception, (connectors.RelayStarting, connectors.RelaySilent))
            self.assertFalse(site.relay_mode)
        self.assertFalse((await self.site()).relay_mode)

    async def test_no_repeated_hits_on_the_firewall_while_switching(self):
        site = await self.site()
        self.responses = [html(202, SG_CHALLENGE)]
        with self.assertRaises(connectors.RelayStarting):
            await connectors.fetch_status(site)
        for _ in range(3):           # i ricontrolli al minuto non toccano il sito
            with self.assertRaises(connectors.RelayStarting):
                await connectors.fetch_status(site)
        self.assertEqual(len(self.requests), 1)

    async def test_no_second_hit_when_the_connector_already_pings(self):
        await self.set_site(relay_seen_at=NOW())
        site = await self.site()
        self.responses = [html(202, SG_CHALLENGE)]
        with self.assertRaises(connectors.RelayStarting):
            await connectors.fetch_status(site)
        with self.assertRaises(connectors.RelayStarting):    # in modalita': niente strada diretta subito
            await connectors.fetch_status(site)
        self.assertEqual(len(self.requests), 1)

    async def test_blip_then_back_retries_direct_route_next_time(self):
        await self.set_site(relay_mode=True, relay_seen_at=NOW())
        await relay.save_status(self.site_id, OK_PAYLOAD)
        site = await self.site()
        self.responses = [httpx.Response(200, json=OK_PAYLOAD)]
        await connectors.fetch_status(site)               # 3 ore: strada diretta, riuscita
        self.assertFalse(site.relay_mode)
        self.assertFalse(await self.redis.exists(relay._k("direct", self.site_id)))


class CycleTests(RelayBase):
    async def test_check_waits_while_an_update_cycle_runs(self):
        # il controllo normale riscriverebbe le estensioni che il ciclo sta usando (StaleDataError)
        await relay.mark_busy(self.site_id, 1800)
        with patch.object(worker, "SessionLocal", AsyncMock(side_effect=AssertionError("non deve leggere il sito"))):
            await worker.poll_site({"redis": self.redis}, self.site_id)
        await relay.clear_busy(self.site_id)
        self.assertFalse(await relay.busy(self.site_id))

    async def test_busy_is_only_extended_never_shortened(self):
        await relay.mark_busy(self.site_id, 1800)
        await relay.mark_busy(self.site_id)              # fine di un lavoro: 120 s
        self.assertGreater(await self.redis.ttl(relay._k("busy", self.site_id)), 1000)

    async def test_jobs_respect_the_cycle_deadline(self):
        await self.set_site(relay_mode=True, relay_seen_at=NOW())
        site = await self.site()
        token = relay.set_cycle_deadline(0.4)
        try:
            with patch.object(relay, "POLL", 0.02):
                t0 = asyncio.get_running_loop().time()
                res = await worker._update_one(site, "plugin", "a")
                self.assertLess(asyncio.get_running_loop().time() - t0, 5)
        finally:
            relay.reset_cycle_deadline(token)
        self.assertTrue(res["relay_timeout"])

    async def test_silent_relay_site_is_offline_only_if_the_firewall_cannot_reach_it(self):
        from app import settings_store  # noqa: F401
        site = await self.site()
        site.offline_since = NOW() - timedelta(hours=1)
        with patch("app.settings_store.get_operational_settings", AsyncMock(return_value={"offline_alert_minutes": 5})):
            with patch.object(connectors, "probe_home", AsyncMock(return_value=(403, 0.4))):   # pagina di Cloudflare
                await connectors._answer_not_from_connector(site, "Connettore: x", "x", answers_below=500)
            self.assertTrue(site.error.startswith("Connettore: "))
            site.error = ""
            with patch.object(connectors, "probe_home", AsyncMock(return_value=(522, 0.4))):   # origine giu'
                await connectors._answer_not_from_connector(site, "Connettore: x", "x", answers_below=500)
            self.assertFalse(site.error.startswith("Connettore: "))

    def test_joomla_old_connector_message_says_where_to_write_the_address(self):
        from app.site_http import firewall_reason
        self.assertIn("scrivi l'indirizzo del pannello nelle sue impostazioni",
                      firewall_reason("Cloudflare", 403, "connector", "Joomla 1.43.0"))
        self.assertIn("scaricato da Impostazioni → Connettori",
                      firewall_reason("Cloudflare", 403, "connector", "WordPress 2.41.0"))


class RefreshTests(RelayBase):
    async def test_refresh_result_becomes_the_status(self):
        await self.set_site(relay_mode=True)
        job_id = await relay.push_job(self.site_id, "refresh", {})
        out = await self.connector(kind="report")
        fresh = {"core": {}, "extensions": [{"type": "plugin", "slug": "elementor-pro", "update": True}]}
        await self.connector(kind="report", results=[{"id": out["jobs"][0]["id"], "result": {"ok": True, "status": fresh}}])
        self.assertEqual(await relay.fresh_status(self.site_id), fresh)
        self.assertFalse(await relay.refresh_due(self.site_id))     # ricerca fatta: la prossima fra N ore
        self.assertEqual(job_id, out["jobs"][0]["id"])

    async def test_update_cycle_waits_for_the_update_search(self):
        await self.set_site(relay_mode=True, relay_seen_at=NOW())
        await relay.save_status(self.site_id, {"core": {}, "extensions": []})          # stato vecchio
        await self.redis.set(relay._k("direct", self.site_id), "1")
        site = await self.site()
        fresh = {"core": {}, "extensions": [{"type": "plugin", "slug": "elementor-pro", "update": True}]}

        async def fake_connector():
            for _ in range(200):
                out = await self.connector(kind="report")
                for job in out["jobs"]:
                    if job["action"] == "refresh":
                        await self.connector(kind="report", results=[{"id": job["id"], "result": {"ok": True, "status": fresh}}])
                        return
                await asyncio.sleep(0.02)

        task = asyncio.create_task(fake_connector())
        token = relay.set_cycle_deadline(60)
        try:
            with patch("app.eventlog.record_now", AsyncMock()):
                data = await connectors.fetch_status(site, force=True)
        finally:
            relay.reset_cycle_deadline(token)
        await task
        self.assertEqual(data, fresh)       # si aggiorna su dati freschi, Elementor Pro compreso

    async def test_periodic_update_search(self):
        await self.set_site(relay_mode=True, relay_seen_at=NOW())
        await relay.save_status(self.site_id, OK_PAYLOAD)
        await self.redis.set(relay._k("direct", self.site_id), "1")
        site = await self.site()
        await connectors.fetch_status(site)              # nessuna ricerca da N ore: in coda
        jobs = [json.loads(j)["action"] for j in await self.redis.lrange(relay._k("jobs", self.site_id), 0, -1)]
        self.assertEqual(jobs, ["refresh"])
        await connectors.fetch_status(site)              # gia' in coda: non si duplica
        self.assertEqual(await self.redis.llen(relay._k("jobs", self.site_id)), 1)


class ThirdReviewTests(RelayBase):
    async def test_old_connector_refresh_without_status_still_counts(self):
        # connettori 2.41.0-2.41.2: esito {"ok": true}, stato a parte. Prima la ricerca risultava mai
        # fatta e se ne accodava una nuova a ogni controllo (una ogni 10 minuti, per sempre)
        await self.set_site(relay_mode=True)
        await relay.request_refresh(self.site_id)
        out = await self.connector(kind="report", status=OK_PAYLOAD)
        await self.connector(kind="report", status=OK_PAYLOAD, results=[{"id": out["jobs"][0]["id"], "result": {"ok": True}}])
        self.assertFalse(await relay.refresh_due(self.site_id))

    async def test_refreshed_status_in_a_result_is_applied(self):
        await self.set_site(relay_mode=True)
        await relay.push_job(self.site_id, "refresh", {})
        out = await self.connector(kind="report")
        relay.enqueue_apply.reset_mock()
        await self.connector(kind="report", results=[{"id": out["jobs"][0]["id"], "result": {"ok": True, "status": OK_PAYLOAD}}])
        relay.enqueue_apply.assert_awaited_with(self.site_id)

    async def test_hub_asks_for_status_when_it_has_none(self):
        await self.set_site(relay_mode=True)
        out = await self.connector(kind="report")                       # domanda leggera, nessuno stato
        self.assertTrue(out["need_status"])
        out = await self.connector(kind="report", status=OK_PAYLOAD)
        self.assertFalse(out["need_status"])

    async def test_status_is_saved_before_results_are_posted(self):
        await self.set_site(relay_mode=True)
        await relay.push_job(self.site_id, "refresh", {})
        out = await self.connector(kind="report")
        seen = {}
        real = relay.post_result

        async def spy(site_id, job_id, result):
            seen["status"] = await relay.fresh_status(site_id)
            return await real(site_id, job_id, result)
        fresh = {"core": {}, "extensions": [{"slug": "nuovo"}]}
        with patch.object(relay, "post_result", spy):
            await self.connector(kind="report", status=fresh, results=[{"id": out["jobs"][0]["id"], "result": {"ok": True}}])
        self.assertEqual(seen["status"], fresh)

    async def test_live_calls_count_even_if_the_site_was_read_long_ago(self):
        await self.set_site(relay_mode=True, relay_seen_at=NOW() - timedelta(minutes=55))
        await relay.save_status(self.site_id, OK_PAYLOAD)
        site = await self.site()                          # letto all'inizio di un ciclo lungo
        with patch.object(relay, "STALE_MINUTES", 30):
            self.assertIsNone(await relay.status_for(site))
            await relay.note_seen(self.site_id)           # intanto il sito ha chiamato
            self.assertEqual(await relay.status_for(site), OK_PAYLOAD)

    def test_pickup_follows_the_poll_interval(self):
        try:
            relay.apply_config({"relay_poll_minutes": 1})
            self.assertEqual(relay.PICKUP_SECONDS, 480)       # connettori vecchi: cron fisso a 5 minuti
            relay.apply_config({"relay_poll_minutes": 15})
            self.assertEqual(relay.PICKUP_SECONDS, 1080)
            self.assertLess(relay.PICKUP_SECONDS + relay.RUN_SECONDS, 1800)   # sotto il job_timeout di arq
        finally:
            relay.apply_config({})


class MassUpdateTests(RelayBase):
    async def test_update_all_includes_firewall_sites_even_with_nothing_pending(self):
        # caso reale: "Aggiorna tutto" saltava primapiuma perche' nel pannello risultava "0 da
        # aggiornare", e la ricerca dal backend (plugin a licenza) non partiva mai
        await self.set_site(relay_mode=True, relay_seen_at=NOW(), auto_update=True)
        async with self.sessions() as s:
            s.add(Site(name="Normale", url="https://normale.test", cms="wp", token="n" * 48, auto_update=True))
            await s.commit()
        queued = []

        class Redis:
            async def enqueue_job(self, name, *args, **kw):
                queued.append((name, args))
        with patch.object(worker, "SessionLocal", self.sessions):
            await worker.mass_update_now({"redis": Redis()})
        self.assertEqual([(n, a[0]) for n, a in queued if n == "update_site"], [("update_site", self.site_id)])


class WakeTests(RelayBase):
    """Sito in modalita' firewall con il cron fermo (SiteGround: pagine dalla cache, PHP non gira):
    il browser di Sentinel lo sveglia (caso reale primapiuma, 24 ottobre 2026)."""

    async def asyncTearDown(self):
        relay.apply_config({})
        await super().asyncTearDown()

    def woken(self):
        return [c.args for c in relay._enqueue.call_args_list if c.args and c.args[0] == "relay_wake"]

    async def test_job_for_a_quiet_site_wakes_it_once(self):
        await self.set_site(relay_mode=True, relay_seen_at=NOW() - timedelta(minutes=40))
        site = await self.site()
        self.assertIsNone(await relay.call(site, "refresh", {}, pickup=0.1))
        self.assertEqual(self.woken(), [("relay_wake", self.site_id)])
        self.assertIsNone(await relay.call(site, "update", {"type": "plugin", "slug": "x"}, pickup=0.1))
        self.assertEqual(len(self.woken()), 1)                  # al massimo ogni 4 minuti

    async def test_second_wake_while_the_job_waits(self):
        await self.set_site(relay_mode=True, relay_seen_at=NOW() - timedelta(minutes=40))
        with patch.object(relay, "WAKE_JOB_GAP", 0.2), patch.object(relay, "REWAKE_AFTER", 0.3):
            self.assertIsNone(await relay.call(await self.site(), "refresh", {}, pickup=0.8))
        self.assertEqual(len(self.woken()), 2)

    async def test_site_that_calls_regularly_is_never_woken(self):
        await self.set_site(relay_mode=True, relay_seen_at=NOW() - timedelta(minutes=40))
        await relay.note_seen(self.site_id)                     # chiamata appena arrivata
        site = await self.site()
        await relay.call(site, "refresh", {}, pickup=0.1)
        self.assertFalse(await relay.maybe_wake(site, periodic=True))
        self.assertEqual(self.woken(), [])

    async def test_periodic_wake_follows_settings(self):
        relay.apply_config({"relay_wake_minutes": 15})
        await self.set_site(relay_mode=True, relay_seen_at=NOW() - timedelta(minutes=10))
        self.assertFalse(await relay.maybe_wake(await self.site(), periodic=True))
        await self.set_site(relay_seen_at=NOW() - timedelta(minutes=20))
        self.assertTrue(await relay.maybe_wake(await self.site(), periodic=True))
        self.assertFalse(await relay.maybe_wake(await self.site(), periodic=True))   # gia' svegliato ora
        relay.apply_config({"relay_wake_minutes": 0})
        await self.redis.delete(relay._k("wake", self.site_id))
        self.assertFalse(await relay.maybe_wake(await self.site(), periodic=True))   # 0 = mai

    async def test_wake_tick_only_for_firewall_sites(self):
        relay.apply_config({"relay_wake_minutes": 15})
        relay._CFG_AT = 10**12
        await self.set_site(relay_mode=True, relay_seen_at=NOW() - timedelta(minutes=30))
        async with self.sessions() as s:
            s.add(Site(name="Diretto", url="https://diretto.test", cms="wp", token="d" * 48))
            await s.commit()
        try:
            with patch.object(worker, "SessionLocal", self.sessions):
                await worker.relay_wake_tick({})
        finally:
            relay._CFG_AT = 0.0
        self.assertEqual(self.woken(), [("relay_wake", self.site_id)])

    async def shooter(self, response):
        await self.set_site(relay_mode=True, relay_seen_at=NOW() - timedelta(minutes=30))
        sent = []
        client_type = httpx.AsyncClient

        async def handler(request):
            sent.append((request.url.path, json.loads(request.content)))
            return response
        with patch.object(worker.httpx, "AsyncClient",
                          lambda **kw: client_type(transport=httpx.MockTransport(handler), **kw)), \
                patch.object(worker, "SessionLocal", self.sessions):
            await worker.relay_wake({}, self.site_id)
        return sent

    async def events(self):
        from sqlalchemy import select
        from app.models import EventLog
        async with self.sessions() as s:
            return [e.message for e in (await s.execute(select(EventLog))).scalars().all()]

    async def test_wake_job_calls_the_browser_with_site_and_cms(self):
        sent = await self.shooter(httpx.Response(200, json={"ok": True, "cron_status": 200}))
        self.assertEqual(sent, [("/wake", {"url": "https://www.primapiuma.test", "site_id": self.site_id,
                                            "cms": "wp", "max_wait": 60})])
        self.assertEqual(await self.events(), [])

    async def test_wake_refused_by_antibot_is_logged_once(self):
        await self.shooter(httpx.Response(200, json={"ok": False, "blocked": True}))
        await self.shooter(httpx.Response(200, json={"ok": False, "blocked": True}))
        msgs = await self.events()
        self.assertEqual(len(msgs), 1)
        self.assertIn("respinge anche il browser di Sentinel", msgs[0])

    async def test_busy_shooter_leaves_the_wake_for_the_next_round(self):
        await self.redis.set(relay._k("wake", self.site_id), "123")
        await self.shooter(httpx.Response(503, json={"detail": "busy"}))
        self.assertIsNone(await self.redis.get(relay._k("wake", self.site_id)))
        self.assertEqual(await self.events(), [])

    async def test_not_taken_message_says_why(self):
        await self.set_site(relay_mode=True, relay_seen_at=NOW() - timedelta(minutes=50))
        text = await relay.not_taken_text(await self.site())
        self.assertIn("non chiama Sentinel da 50 minuti invece che ogni 5", text)
        self.assertIn("cron di WordPress non parte", text)
        await relay.note_seen(self.site_id)
        self.assertEqual(await relay.not_taken_text(await self.site()), relay.not_taken_message())


class ManualCallAndCycleTests(RelayBase):
    """Caso reale (24 ottobre 2026): dopo ogni "Chiama Sentinel TD ora" il sito risultava sveglio
    ("ultima chiamata 0 min fa") e la sveglia non partiva, mentre i lavori restavano in coda; e
    piu' cicli di aggiornamento giravano insieme sullo stesso sito."""

    def woken(self):
        return [c.args for c in relay._enqueue.call_args_list if c.args and c.args[0] == "relay_wake"]

    async def test_manual_call_does_not_count_as_awake(self):
        await self.set_site(relay_mode=True, relay_seen_at=NOW() - timedelta(minutes=40))
        await self.connector(kind="report", connector="2.41.3", jobs_ok=True, nojobs=True)   # il pulsante
        await self.redis.set(relay._k("jobseen", self.site_id), str(relay.time.time() - 2400))
        self.assertTrue(await relay.maybe_wake(await self.site()))
        self.assertEqual(self.woken(), [("relay_wake", self.site_id)])

    async def test_a_call_that_takes_jobs_counts(self):
        await self.set_site(relay_mode=True, relay_seen_at=NOW() - timedelta(minutes=40))
        await self.connector(kind="report", connector="2.41.3", jobs_ok=True)
        self.assertFalse(await relay.maybe_wake(await self.site()))

    async def test_one_cycle_per_site(self):
        self.assertTrue(await relay.start_cycle(self.site_id, "job-a"))
        self.assertFalse(await relay.start_cycle(self.site_id, "job-b"))
        self.assertTrue(await relay.start_cycle(self.site_id, "job-a"))   # lo stesso lavoro dopo un riavvio
        await relay.end_cycle(self.site_id, "job-b")                       # non e' suo: non lo toglie
        self.assertFalse(await relay.start_cycle(self.site_id, "job-c"))
        await relay.end_cycle(self.site_id, "job-a")
        self.assertTrue(await relay.start_cycle(self.site_id, "job-c"))

    async def test_second_update_does_not_start_while_a_cycle_runs(self):
        await self.set_site(relay_mode=True, relay_seen_at=NOW(), auto_update=True)
        await relay.start_cycle(self.site_id, "primo")
        statuses = []

        async def status(redis, sid, state, text, parts=None):
            statuses.append((state, text))
        inner = AsyncMock()
        with patch.object(worker, "SessionLocal", self.sessions), patch.object(worker, "_update_site", inner), \
                patch.object(worker, "_set_upd_status", status):
            await worker.update_site({"redis": self.redis, "job_id": "secondo"}, self.site_id, True)
        inner.assert_not_called()
        self.assertEqual(statuses, [("running", "già in corso: in modalità firewall il sito fa un ciclo alla volta")])


class StaleJobTests(RelayBase):
    """Caso reale: dopo i riavvii del worker in coda restavano cinque ricerche, due volte lo stesso
    aggiornamento e un'installazione, di cicli che nessuno aspettava piu'."""

    async def test_expired_jobs_are_not_delivered(self):
        old = await relay.push_job(self.site_id, "refresh", {}, expires=relay.time.time() - 1)
        live = await relay.push_job(self.site_id, "update", {"slug": "x"}, expires=relay.time.time() + 60)
        plain = await relay.push_job(self.site_id, "refresh", {})          # "Check ora": senza scadenza
        jobs = await relay.take_jobs(self.site_id)
        self.assertEqual([j["id"] for j in jobs], [live, plain])
        self.assertNotIn("exp", jobs[0])
        self.assertIsNone(await self.redis.get(relay._k("raw", old)))

    async def test_call_sets_the_expiry(self):
        await self.set_site(relay_mode=True, relay_seen_at=NOW())
        await relay.note_seen(self.site_id)
        await relay.call(await self.site(), "refresh", {}, pickup=0.1)
        self.assertEqual(await self.redis.llen(relay._k("jobs", self.site_id)), 0)   # tolto alla scadenza
        jid = await relay.push_job(self.site_id, "refresh", {}, expires=relay.time.time() + 30)
        raw = json.loads((await self.redis.lrange(relay._k("jobs", self.site_id), 0, -1))[0])
        self.assertEqual(raw["id"], jid)
        self.assertIn("exp", raw)


class SettingsTests(RelayBase):
    async def asyncTearDown(self):
        relay.apply_config({})
        relay._CFG_AT = 0.0
        await super().asyncTearDown()

    def test_settings_are_normalized(self):
        from app.settings_store import normalize
        out = normalize({"relay_poll_minutes": 10, "relay_status_minutes": 90, "relay_silent_minutes": 20,
                         "relay_ping_minutes": 5, "relay_enabled": False})
        self.assertEqual(out["relay_ping_minutes"], 15)
        self.assertEqual(out["relay_silent_minutes"], 30)      # almeno tre domande mancate
        self.assertEqual(normalize({"relay_poll_minutes": 0})["relay_poll_minutes"], 1)
        defaults = normalize({})
        self.assertEqual((defaults["relay_poll_minutes"], defaults["relay_status_minutes"], defaults["relay_ping_minutes"],
                          defaults["relay_silent_minutes"], defaults["relay_refresh_hours"]), (5, 60, 30, 60, 6))
        self.assertFalse(out["relay_enabled"])
        self.assertEqual(defaults["relay_wake_minutes"], 15)
        self.assertEqual(normalize({"relay_wake_minutes": 2})["relay_wake_minutes"], 5)
        self.assertEqual(normalize({"relay_wake_minutes": 0})["relay_wake_minutes"], 0)

    async def test_times_from_settings_reach_the_connector(self):
        relay.apply_config({"relay_poll_minutes": 2, "relay_status_minutes": 45, "relay_ping_minutes": 60,
                            "relay_silent_minutes": 120})
        relay._CFG_AT = 10**12                              # niente rilettura dal database nel test
        out = await self.connector(kind="ping")
        self.assertEqual((out["tick"], out["status_every"], out["ping"]), (120, 2700, 3600))
        self.assertEqual(relay.PICKUP_SECONDS, 480)          # mai sotto gli 8 minuti

    async def test_disabled_in_settings_never_switches(self):
        relay.apply_config({"relay_enabled": False})
        relay._CFG_AT = 10**12
        await self.set_site(relay_mode=True, relay_seen_at=NOW(), status="error",
                            error="Connettore: bloccato da SiteGround davanti al sito (HTTP 202): …")
        self.assertFalse(relay.active(await self.site()))
        out = await self.connector(kind="ping", connector="2.41.3")
        self.assertEqual(out["mode"], "direct")              # il connettore torna alla strada diretta


class HelpersTests(unittest.TestCase):
    def test_connector_versions(self):
        self.assertTrue(relay.connector_can_relay(SimpleNamespace(cms="wp", connector_version="2.41.0")))
        self.assertTrue(relay.connector_can_relay(SimpleNamespace(cms="joomla", connector_version="1.43.2")))
        self.assertFalse(relay.connector_can_relay(SimpleNamespace(cms="wp", connector_version="2.40.0")))
        self.assertFalse(relay.connector_can_relay(SimpleNamespace(cms="wp", connector_version="")))
        self.assertEqual(relay.needed_connector(SimpleNamespace(cms="joomla")), "Joomla 1.43.0")

    def test_active_needs_recent_contact(self):
        now = datetime.now(timezone.utc)
        self.assertTrue(relay.active(SimpleNamespace(relay_mode=True, relay_seen_at=now)))
        self.assertFalse(relay.active(SimpleNamespace(relay_mode=False, relay_seen_at=now)))
        self.assertFalse(relay.active(SimpleNamespace(relay_mode=True, relay_seen_at=now - timedelta(hours=2))))
        self.assertFalse(relay.active(SimpleNamespace()))


if __name__ == "__main__":
    unittest.main()
