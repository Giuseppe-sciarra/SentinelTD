"""Build mu-plugin headless: token del sito cablato, nessuna voce di menu, header intatto.
Nessun DB, nessuna rete: si stubba solo l'hub e la sorgente del connettore."""
import os
import unittest
from unittest.mock import patch, AsyncMock

os.environ.setdefault("ADMIN_PASSWORD", "xxxxxxxxxxxx")
os.environ.setdefault("JWT_SECRET", "x" * 36)
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")

from app.routers import connectors as C  # noqa: E402

CONN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "connectors", "wordpress", "td-panopticon", "td-panopticon.php")
GOOD = open(CONN, "rb").read()


class MuHeadlessTests(unittest.IsolatedAsyncioTestCase):
    async def _build(self, token="TESTTOKEN_abcdef0123456789"):
        with patch.object(C, "_hub_url", new=AsyncMock(return_value="")), \
             patch.object(C, "_wp_main_php_from_zip", return_value=None), \
             patch.object(C, "_wp_main_php_from_src", return_value=GOOD):
            return await C.wp_mu_headless(token)

    async def test_token_baked_and_menu_removed(self):
        data = await self._build()
        self.assertIn(b"const TDPANOP_TOKEN = 'TESTTOKEN_abcdef0123456789';", data)
        self.assertNotIn(b"add_action('admin_menu'", data)          # nessuna voce di menu
        self.assertIn(b"Plugin Name: Sentinel TD Agent", data)      # visibile in Must-Use
        self.assertIn(b"register_rest_route('tdpanopticon/v1'", data)  # fa ancora il suo lavoro

    async def test_token_is_php_escaped(self):
        # token con apice e backslash: devono finire quotati come li quota il PHP-writer
        tok = "ab'cd\\ef"
        data = await self._build(tok)
        expected = ("const TDPANOP_TOKEN = '%s';" % C._php_quote(tok)).encode()
        self.assertIn(expected, data)
        self.assertNotIn(b"const TDPANOP_TOKEN = '';", data)  # il placeholder e' stato sostituito

    async def test_old_connector_without_feature_is_rejected(self):
        old = GOOD.replace(b"const TDPANOP_TOKEN = '';", b"")  # simula un 2.34.0
        with patch.object(C, "_hub_url", new=AsyncMock(return_value="")), \
             patch.object(C, "_wp_main_php_from_zip", return_value=None), \
             patch.object(C, "_wp_main_php_from_src", return_value=old):
            with self.assertRaises(Exception) as ctx:
                await C.wp_mu_headless("T")
        self.assertIn("2.35.0", str(ctx.exception))

    async def test_prefers_source_that_has_the_feature(self):
        old = GOOD.replace(b"const TDPANOP_TOKEN = '';", b"")
        with patch.object(C, "_hub_url", new=AsyncMock(return_value="")), \
             patch.object(C, "_wp_main_php_from_zip", return_value=old), \
             patch.object(C, "_wp_main_php_from_src", return_value=GOOD):
            data = await C.wp_mu_headless("T")
        self.assertIn(b"const TDPANOP_TOKEN = 'T';", data)


if __name__ == "__main__":
    unittest.main()
