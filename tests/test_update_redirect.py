"""Un aggiornamento WordPress non si perde per un redirect.

Il primo tentativo passa da admin-ajax.php con un POST. Se il sito (o un proxy) risponde con un
301/302, seguirlo trasforma il POST in GET e i dati si perdono: il connettore riceveva una
richiesta vuota e rispondeva "type non valido", e l'aggiornamento risultava fallito senza
esserci mai stato. Ora il redirect non si segue e si passa al percorso REST."""
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx

import test_screenshot_schedule  # noqa: F401  (percorso di api/ e variabili d'ambiente dei test)
from app import worker
from app.models import Site


def site():
    return Site(id=991, name="Prova", url="https://prova.example", cms="wp", token="t")


class UpdateRedirectTests(unittest.IsolatedAsyncioTestCase):
    async def run_update(self, ajax):
        seen = []

        def handler(request: httpx.Request):
            seen.append((request.method, request.url.path))
            if request.url.path.endswith("admin-ajax.php"):
                return ajax(request)
            if request.url.path.endswith("/tdpanopticon/v1/update"):
                body = json.loads(request.content or b"{}")
                if body.get("type") != "plugin":
                    return httpx.Response(200, json={"ok": False, "error": "type non valido"})
                return httpx.Response(200, json={"ok": True, "error": "", "new": "1.17.1"})
            return httpx.Response(200, json={"ok": False, "error": "type non valido"})   # GET dopo un redirect: dati persi

        real = httpx.AsyncClient
        with patch.object(worker.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)), \
             patch("app.settings_store.get_operational_settings", AsyncMock(return_value={"pre_update_backup": True})):
            res = await worker._update_one(site(), "plugin", "wp-mail-logging", "1.17.1")
        return res, seen

    async def test_redirect_on_admin_ajax_falls_back_to_rest(self):
        res, seen = await self.run_update(lambda r: httpx.Response(301, headers={"Location": "https://www.prova.example/wp-admin/admin-ajax.php"}))
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["new"], "1.17.1")
        self.assertNotIn(("GET", "/wp-admin/admin-ajax.php"), seen)      # il redirect non si segue

    async def test_request_arrived_empty_is_not_a_result(self):
        res, seen = await self.run_update(lambda r: httpx.Response(200, json={"ok": False, "error": "type non valido"}))
        self.assertTrue(res["ok"], res)
        self.assertEqual(seen[-1], ("POST", "/wp-json/tdpanopticon/v1/update"))

    async def test_normal_admin_ajax_answer_is_used(self):
        res, seen = await self.run_update(lambda r: httpx.Response(200, json={"ok": True, "error": "", "new": "1.17.1", "context": "admin"}))
        self.assertTrue(res["ok"])
        self.assertEqual(len(seen), 1)


if __name__ == "__main__":
    unittest.main()
