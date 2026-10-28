"""Copia di sicurezza prima degli aggiornamenti, per singolo sito (Modifica del sito).

Impostazioni decide per tutti; il sito puo' dire "si'" o "no" per conto suo, per esempio "no"
sugli hosting con poco spazio. Il pannello lo manda al connettore come backup=1 / backup=0."""
import unittest
from urllib.parse import parse_qs
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException

import test_screenshot_schedule  # noqa: F401  (percorso di api/ e variabili d'ambiente dei test)
from app import worker
from app.models import Site
from app.routers import sites as rsites


def site(mode=""):
    return Site(id=992, name="Prova", url="https://prova.example", cms="wp", token="t", backup_mode=mode)


class BackupFlagTests(unittest.IsolatedAsyncioTestCase):
    async def sent_backup(self, mode, global_on):
        sent = []

        def handler(request: httpx.Request):
            if request.url.path.endswith("admin-ajax.php"):
                sent.append(parse_qs(request.content.decode())["backup"][0])
                return httpx.Response(200, json={"ok": True, "error": "", "new": "1.17.1", "context": "admin"})
            return httpx.Response(404)

        real = httpx.AsyncClient
        with patch.object(worker.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)), \
             patch("app.settings_store.get_operational_settings", AsyncMock(return_value={"pre_update_backup": global_on})):
            res = await worker._update_one(site(mode), "plugin", "wp-mail-logging", "1.17.1")
        self.assertTrue(res["ok"], res)
        return sent[0]

    async def test_site_follows_settings_by_default(self):
        self.assertEqual(await self.sent_backup("", True), "1")
        self.assertEqual(await self.sent_backup("", False), "0")

    async def test_site_choice_wins_over_settings(self):
        self.assertEqual(await self.sent_backup("off", True), "0")     # hosting con poco spazio
        self.assertEqual(await self.sent_backup("on", False), "1")     # server tuo: il ripristino resta


class BackupModeValueTests(unittest.TestCase):
    def test_only_three_values(self):
        self.assertEqual([rsites._backup_mode(v) for v in ("", None, "ON", " off ")], ["", "", "on", "off"])
        with self.assertRaises(HTTPException):
            rsites._backup_mode("forse")


if __name__ == "__main__":
    unittest.main()
