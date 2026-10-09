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
