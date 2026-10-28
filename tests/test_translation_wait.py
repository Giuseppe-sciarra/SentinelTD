"""Traduzioni WordPress appena annunciate: nei giri automatici si aspetta prima di installarle.

wordpress.org annuncia una traduzione nuova prima di avere il pacchetto su tutti i server di
download. Il 28/10/2026 l'en_GB di WordPress e' stata annunciata alle 20:33 ed e' comparsa sul
server di download alle 21:01; Sentinel l'ha installata alle 21:02, ha preso quella vecchia e la
traduzione e' andata in giallo con l'email. Ora il giro aspetta "translation_wait_minutes"
(Impostazioni, di base 90) dall'annuncio. Con "Aggiorna" a mano si installa subito."""
import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs

import httpx

import test_screenshot_schedule as fixtures
from app import connectors, settings_store, worker
from app.models import Site

OK = {"ok": True, "error": "", "new": "traduzioni aggiornate (1/1): WordPress (en_GB)", "noop": False,
      "message": "", "manual": False}


def status(announced_ago: timedelta | None):
    lang = {"type": "language", "name": "Traduzioni (1)", "slug": "wp-translations", "current": "", "new": "1",
            "update": True}
    if announced_ago is not None:
        lang["announced"] = int((fixtures.NOW - announced_ago).timestamp())
    return {"cms": "wp", "core": {"current": "7.1.4", "latest": "7.1.4", "update": False}, "extensions": [lang]}


class TranslationWaitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.SchedulerTests.asyncSetUp(self)

    async def asyncTearDown(self):
        await fixtures.SchedulerTests.asyncTearDown(self)

    async def run_site(self, payload, manual=False):
        sid = await fixtures.SchedulerTests.add_site(self, status="ok", auto_update=True, cms="wp")

        async def fetch(site, force=False, **kw):
            return payload

        upd = AsyncMock(return_value=OK)
        notify = AsyncMock()
        outcome = {}
        with patch.object(connectors, "fetch_status", fetch), patch.object(worker, "_update_one", upd), \
             patch.object(worker, "_visual_shot", AsyncMock(return_value=None)), \
             patch.object(worker, "notify_dispatch", notify), patch.object(worker.asyncio, "sleep", AsyncMock()):
            await worker._update_site({"redis": self.redis}, sid, manual, outcome)
        return upd, notify, outcome

    async def test_just_announced_waits_for_the_next_round(self):
        upd, notify, outcome = await self.run_site(status(timedelta(minutes=30)))
        self.assertEqual(upd.await_count, 0)
        self.assertEqual(notify.await_count, 0)                       # niente giallo, niente email
        self.assertIn("traduzioni appena uscite", outcome["text"])

    async def test_after_the_wait_it_installs_and_tells_the_connector(self):
        upd, notify, outcome = await self.run_site(status(timedelta(minutes=95)))
        self.assertEqual(upd.await_count, 1)
        self.assertEqual(upd.await_args.kwargs.get("tr_wait"), 90)    # le altre appena uscite restano

    async def test_manual_update_installs_at_once(self):
        upd, _n, _o = await self.run_site(status(timedelta(minutes=5)), manual=True)
        self.assertEqual(upd.await_count, 1)
        self.assertNotIn("tr_wait", upd.await_args.kwargs)

    async def test_zero_minutes_means_at_once(self):
        await settings_store.save_operational_settings({"translation_wait_minutes": 0})
        upd, _n, _o = await self.run_site(status(timedelta(minutes=5)))
        self.assertEqual(upd.await_count, 1)
        self.assertNotIn("tr_wait", upd.await_args.kwargs)

    async def test_older_connector_without_the_date_installs_as_before(self):
        upd, _n, _o = await self.run_site(status(None))
        self.assertEqual(upd.await_count, 1)

    def test_setting_default_and_range(self):
        self.assertEqual(settings_store.normalize({})["translation_wait_minutes"], 90)
        self.assertEqual(settings_store.normalize({"translation_wait_minutes": -5})["translation_wait_minutes"], 0)
        self.assertEqual(settings_store.normalize({"translation_wait_minutes": 5000})["translation_wait_minutes"], 1440)

    def test_announced_date_from_the_connector(self):
        self.assertEqual(connectors._announced({"announced": 1793215000}).timestamp(), 1793215000)
        self.assertIsNone(connectors._announced({}))
        self.assertIsNone(connectors._announced({"announced": "x"}))


class SentToConnectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_tr_wait_reaches_the_connector(self):
        sent = []

        def handler(request: httpx.Request):
            sent.append(parse_qs(request.content.decode()))
            return httpx.Response(200, json={"ok": True, "error": "", "new": "traduzioni aggiornate (1/1)"})

        real = httpx.AsyncClient
        site = Site(id=994, name="Prova", url="https://prova.example", cms="wp", token="t", backup_mode="")
        with patch.object(worker.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)), \
             patch("app.settings_store.get_operational_settings", AsyncMock(return_value={"pre_update_backup": True})):
            await worker._update_one(site, "language", "wp-translations", "1", tr_wait=90)
            await worker._update_one(site, "plugin", "td-forms", "1.23.3")
        self.assertEqual(sent[0]["tr_wait"], ["90"])
        self.assertNotIn("tr_wait", sent[1])


if __name__ == "__main__":
    unittest.main()
