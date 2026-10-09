"""Aggiornamenti automatici di WordPress on/off dalle Impostazioni: la scelta parte con ogni
controllo del sito (parametro wp_auto_updates), il connettore 2.38+ se la segna."""
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import test_screenshot_schedule  # noqa: F401  (prepara percorso e ambiente dei test)
import httpx

from app import connectors, settings_store


@asynccontextmanager
async def unrestricted_slot(*args, **kwargs):
    yield


class SettingTests(unittest.TestCase):
    def test_on_by_default_and_saved_when_turned_off(self):
        self.assertTrue(settings_store.normalize({})["wp_block_auto_updates"])
        self.assertFalse(settings_store.normalize({"wp_block_auto_updates": False})["wp_block_auto_updates"])

    def test_nightly_connector_switch_is_kept(self):
        # l'interruttore del giro notturno passa dallo stesso salvataggio: spento resta spento
        self.assertFalse(settings_store.normalize({"connector_auto_update": False})["connector_auto_update"])


class StatusRequestTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []

        async def handler(request):
            self.requests.append(request)
            return httpx.Response(200, json={"cms": "wp", "core": {}, "extensions": []})
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

    async def fetch(self, prefs, cms="wp", force=False):
        site = SimpleNamespace(id=7, name="Test", cms=cms, url="https://example.test", token="t")
        with patch.object(settings_store, "get_operational_settings", AsyncMock(return_value=prefs)):
            await connectors._fetch_status_once(site, force=force)
        return self.requests[-1].url.params

    async def test_wordpress_check_carries_block_by_default(self):
        params = await self.fetch(dict(settings_store.DEFAULTS))
        self.assertEqual(params.get("wp_auto_updates"), "block")

    async def test_wordpress_check_carries_allow_when_turned_off(self):
        params = await self.fetch({**settings_store.DEFAULTS, "wp_block_auto_updates": False}, force=True)
        self.assertEqual((params.get("wp_auto_updates"), params.get("refresh")), ("allow", "1"))

    async def test_joomla_check_is_unchanged(self):
        site = SimpleNamespace(id=8, name="J", cms="joomla", url="https://example.test", token="t")

        async def handler(request):
            self.requests.append(request)
            return httpx.Response(200, json={"success": True, "data": [{"cms": "joomla", "core": {}, "extensions": []}]})
        self.client._transport = httpx.MockTransport(handler)
        await connectors._fetch_status_once(site)
        self.assertNotIn("wp_auto_updates", self.requests[-1].url.params)


if __name__ == "__main__":
    unittest.main()
