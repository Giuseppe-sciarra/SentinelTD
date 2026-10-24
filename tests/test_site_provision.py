"""Flusso mu-plugin: crea il sito con un token coniato dal pannello (gen_token)."""
import os
import unittest
from unittest.mock import patch, AsyncMock

os.environ.setdefault("ADMIN_PASSWORD", "xxxxxxxxxxxx")
os.environ.setdefault("JWT_SECRET", "x" * 36)
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker  # noqa: E402
from app.db import Base  # noqa: E402
from app import models  # noqa: F401,E402
from app.models import Site  # noqa: E402
from app.routers import sites as S  # noqa: E402
from app.schemas import SiteIn  # noqa: E402


class ProvisionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        self.SL = async_sessionmaker(self.engine, expire_on_commit=False)
        self.patches = [
            patch.object(S, "_resolve_final_url", new=AsyncMock(side_effect=lambda u: u)),
            patch.object(S, "_enqueue", new=AsyncMock(return_value=None)),
        ]
        for p in self.patches:
            p.start()

    async def asyncTearDown(self):
        for p in reversed(self.patches):
            p.stop()
        await self.engine.dispose()

    async def test_gen_token_mints_and_persists(self):
        async with self.SL() as s:
            site = await S.create_site(SiteIn(name="S", url="https://x.it", cms="wp", gen_token=True), s)
            self.assertTrue(site.token)
            self.assertGreaterEqual(len(site.token), 32)
            fresh = await s.get(Site, site.id)
            self.assertEqual(fresh.token, site.token)   # il token del mu-plugin combacera' con questo

    async def test_two_sites_get_distinct_tokens(self):
        async with self.SL() as s:
            a = await S.create_site(SiteIn(name="A", url="https://a.it", cms="wp", gen_token=True), s)
            b = await S.create_site(SiteIn(name="B", url="https://b.it", cms="wp", gen_token=True), s)
            self.assertNotEqual(a.token, b.token)

    async def test_no_token_without_gen_is_rejected(self):
        async with self.SL() as s:
            with self.assertRaises(Exception) as ctx:
                await S.create_site(SiteIn(name="S", url="https://x.it", cms="wp"), s)
            self.assertIn("Token", str(ctx.exception))

    async def test_explicit_token_is_kept(self):
        async with self.SL() as s:
            site = await S.create_site(SiteIn(name="S", url="https://x.it", cms="wp", token="MYTOKEN123456"), s)
            self.assertEqual(site.token, "MYTOKEN123456")


if __name__ == "__main__":
    unittest.main()


class ExtraHeadersApiTests(unittest.IsolatedAsyncioTestCase):
    """Intestazioni extra del sito (2.48.0): salvate normalizzate, rifiutate se non valide,
    visibili solo nel dettaglio del sito come il token."""
    asyncSetUp = ProvisionTests.asyncSetUp
    asyncTearDown = ProvisionTests.asyncTearDown

    async def test_saved_normalized_and_only_in_detail(self):
        from fastapi import HTTPException
        from app.schemas import SiteUpdate, SiteOut, SiteDetailOut
        async with self.SL() as s:
            site = await S.create_site(SiteIn(name="S", url="https://x.it", cms="wp", token="T" * 16,
                                              extra_headers=" X-Sentinel-Pass :  abc "), s)
            self.assertEqual(site.extra_headers, "X-Sentinel-Pass: abc")
            await S.update_site(site.id, SiteUpdate(extra_headers="CF-Access-Client-Id: id\nCF-Access-Client-Secret: sec"), s)
            fresh = await s.get(Site, site.id)
            self.assertEqual(fresh.extra_headers, "CF-Access-Client-Id: id\nCF-Access-Client-Secret: sec")
            with self.assertRaises(HTTPException) as ctx:
                await S.update_site(site.id, SiteUpdate(extra_headers="Authorization: Bearer x"), s)
            self.assertEqual(ctx.exception.status_code, 422)
            await S.update_site(site.id, SiteUpdate(name="Rinominato"), s)     # campo assente: non si tocca
            fresh = await s.get(Site, site.id)
            self.assertIn("CF-Access-Client-Secret", fresh.extra_headers)
            self.assertNotIn("extra_headers", SiteOut.model_fields)
            self.assertIn("extra_headers", SiteDetailOut.model_fields)
