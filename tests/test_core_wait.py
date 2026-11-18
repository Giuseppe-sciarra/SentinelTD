"""Core appena uscito: nei giri automatici si aspetta prima di installarlo (Impostazioni →
"Core appena uscito: aspetta", di base 30 minuti da quando Sentinel vede la versione nuova sul
sito). Con "Aggiorna" a mano si installa subito."""
import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import test_screenshot_schedule as fixtures
from sqlalchemy import select

from app import connectors, settings_store, worker
from app.models import Site

OK = {"ok": True, "error": "", "new": "7.1.5", "noop": False, "message": "", "manual": False, "backup": ""}


def status(latest="7.1.5"):
    return {"cms": "wp", "core": {"current": "7.1.4", "latest": latest, "update": True}, "extensions": []}


class CoreWaitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.SchedulerTests.asyncSetUp(self)

    async def asyncTearDown(self):
        await fixtures.SchedulerTests.asyncTearDown(self)

    async def run_site(self, seen_ago=None, manual=False, seen_version="7.1.5", sid=None):
        sid = sid or await fixtures.SchedulerTests.add_site(self, status="ok", auto_update=True, cms="wp")
        if seen_ago is not None:
            async with self.sessions() as s:
                site = await s.get(Site, sid)
                site.core_seen_version, site.core_seen_at = seen_version, fixtures.NOW - seen_ago
                await s.commit()

        async def fetch(site, force=False, **kw):
            return status()

        upd = AsyncMock(return_value=dict(OK))
        notify = AsyncMock()
        outcome = {}
        with patch.object(connectors, "fetch_status", fetch), patch.object(worker, "_update_one", upd), \
             patch.object(connectors, "datetime", fixtures.Clock), \
             patch.object(worker, "_visual_shot", AsyncMock(return_value=None)), \
             patch.object(worker, "notify_dispatch", notify), patch.object(worker.asyncio, "sleep", AsyncMock()):
            await worker._update_site({"redis": self.redis}, sid, manual, outcome)
        return sid, upd, notify, outcome

    async def test_version_seen_now_waits(self):
        sid, upd, notify, outcome = await self.run_site()            # vista adesso per la prima volta
        self.assertEqual((upd.await_count, notify.await_count), (0, 0))
        self.assertIn("versione del core appena uscita", outcome["text"])
        async with self.sessions() as s:
            site = await s.get(Site, sid)
            self.assertEqual(site.core_seen_version, "7.1.5")

    async def test_seen_10_minutes_ago_still_waits(self):
        _sid, upd, _n, _o = await self.run_site(timedelta(minutes=10))
        self.assertEqual(upd.await_count, 0)

    async def test_seen_31_minutes_ago_installs(self):
        _sid, upd, _n, _o = await self.run_site(timedelta(minutes=31))
        self.assertEqual(upd.await_count, 1)

    async def test_a_newer_version_starts_the_wait_again(self):
        _sid, upd, _n, _o = await self.run_site(timedelta(hours=5), seen_version="7.1.4.1")
        self.assertEqual(upd.await_count, 0)

    async def test_manual_update_installs_at_once(self):
        _sid, upd, _n, _o = await self.run_site(manual=True)
        self.assertEqual(upd.await_count, 1)

    async def test_zero_minutes_means_at_once(self):
        await settings_store.save_operational_settings({"core_wait_minutes": 0})
        _sid, upd, _n, _o = await self.run_site()
        self.assertEqual(upd.await_count, 1)

    def test_setting_default_and_range(self):
        self.assertEqual(settings_store.normalize({})["core_wait_minutes"], 30)
        self.assertEqual(settings_store.normalize({"core_wait_minutes": -1})["core_wait_minutes"], 0)
        self.assertEqual(settings_store.normalize({"core_wait_minutes": 9999})["core_wait_minutes"], 1440)


if __name__ == "__main__":
    unittest.main()
