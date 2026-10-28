"""Aggiornamento partito sul sito ma senza esito: niente secondo tentativo da REST.

Il 28/10/2026 l'aggiornamento del core di un sito e' rimasto appeso sul download da wordpress.org.
Sentinel aspettava 180 secondi, poi riprovava da REST mentre il sito stava ancora aggiornando:
WordPress rispondeva "[locked]" e il motivo vero (PHP fermato da php-fpm a 300 secondi) spariva.
Ora: si aspetta piu' del limite del server, e se la richiesta e' arrivata a PHP senza esito
(nessuna risposta, 502/504) si riporta questo, senza secondo tentativo e senza altri aggiornamenti
su quel sito nello stesso giro."""
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx

import test_screenshot_schedule as fixtures
from app import connectors, worker
from app.models import Site


def site():
    return Site(id=993, name="Prova", url="https://prova.example", cms="wp", token="t", backup_mode="")


class UpdateOneTests(unittest.IsolatedAsyncioTestCase):
    async def update(self, ajax):
        calls = []

        def handler(request: httpx.Request):
            path = request.url.path
            calls.append("ajax" if path.endswith("admin-ajax.php") else "rest")
            if path.endswith("admin-ajax.php"):
                return ajax(request)
            return httpx.Response(200, json={"ok": False, "error": "Attualmente è in corso un altro aggiornamento. [locked]"})

        real = httpx.AsyncClient
        with patch.object(worker.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)), \
             patch("app.settings_store.get_operational_settings", AsyncMock(return_value={"pre_update_backup": True})):
            res = await worker._update_one(site(), "core", "wordpress", "7.1.4")
        return res, calls

    async def test_no_answer_in_time_is_not_retried_from_rest(self):
        def ajax(request):
            raise httpx.ReadTimeout("timed out", request=request)
        res, calls = await self.update(ajax)
        self.assertEqual(calls, ["ajax"])                       # niente REST: troverebbe "[locked]"
        self.assertFalse(res["ok"])
        self.assertTrue(res["busy"])
        self.assertIn("nessuna risposta dal sito in 330 secondi", res["error"])
        self.assertIn("15 minuti", res["error"])               # core: WordPress lo tiene bloccato
        self.assertNotIn("locked", res["error"])

    async def test_server_stopped_php_is_not_retried_from_rest(self):
        res, calls = await self.update(lambda request: httpx.Response(504, text="Gateway Time-out"))
        self.assertEqual(calls, ["ajax"])
        self.assertTrue(res["busy"])
        self.assertIn("HTTP 504", res["error"])

    async def test_old_connector_still_falls_back_to_rest(self):
        # admin-ajax risponde "0" (connettore vecchio): la richiesta non ha fatto niente, REST va bene
        res, calls = await self.update(lambda request: httpx.Response(200, text="0"))
        self.assertEqual(calls, ["ajax", "rest"])
        self.assertFalse(res.get("busy", False))

    async def test_unreachable_backend_still_falls_back_to_rest(self):
        def ajax(request):
            raise httpx.ConnectError("refused", request=request)
        res, calls = await self.update(ajax)
        self.assertEqual(calls, ["ajax", "rest"])

    def test_waits_longer_than_the_server_limit(self):
        self.assertGreater(worker.UPDATE_WAIT, 300)


class UpdateLoopTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.SchedulerTests.asyncSetUp(self)

    async def asyncTearDown(self):
        await fixtures.SchedulerTests.asyncTearDown(self)

    async def test_nothing_else_on_the_site_after_an_update_without_outcome(self):
        sid = await fixtures.SchedulerTests.add_site(self, status="ok", auto_update=True, cms="wp")
        status = {"cms": "wp", "core": {"current": "7.1.3", "latest": "7.1.3", "update": False},
                  "extensions": [
                      {"type": "plugin", "name": "Rank Math", "slug": "seo-by-rank-math",
                       "current": "1.0.270", "new": "1.0.271", "update": True},
                      {"type": "plugin", "name": "Yoast SEO", "slug": "wordpress-seo",
                       "current": "28.6", "new": "28.7", "update": True},
                  ]}

        async def fetch(s, force=False, **kw):
            return status

        busy = worker._busy_result(site(), "plugin", "nessuna risposta dal sito in 330 secondi")
        upd = AsyncMock(return_value=busy)
        with patch.object(connectors, "fetch_status", fetch), patch.object(worker, "_update_one", upd), \
             patch.object(worker, "_visual_shot", AsyncMock(return_value=None)), \
             patch.object(worker, "notify_dispatch", AsyncMock()), patch.object(worker.asyncio, "sleep", AsyncMock()):
            await worker._update_site({"redis": self.redis}, sid, False, {})
        self.assertEqual(upd.await_count, 1)                   # il secondo plugin resta al prossimo giro
        cycle = [json.loads(x) for x in await self.redis.lrange("tg:cycle:report", 0, -1)]
        self.assertEqual([f["name"] for f in cycle[0]["failed"]], ["Rank Math"])


if __name__ == "__main__":
    unittest.main()
