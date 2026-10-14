"""Giro degli aggiornamenti ogni N ore (Impostazioni → "Giro degli aggiornamenti ogni"). Di base
ogni ora, come sempre. Il cron resta orario: con N > 1 i giri intermedi si saltano."""
import time
import unittest
from unittest.mock import patch

import test_screenshot_schedule as fixtures
from app import settings_store, worker


class CycleIntervalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.SchedulerTests.asyncSetUp(self)
        self.enabled = patch.object(worker.settings, "AUTOUPDATE_ENABLED", True)
        self.enabled.start()
        await fixtures.SchedulerTests.add_site(self, status="ok", auto_update=True, cms="wp")

    async def asyncTearDown(self):
        self.enabled.stop()
        await fixtures.SchedulerTests.asyncTearDown(self)

    async def run_cycle(self):
        before = len(await self.redis.queued_jobs())
        await worker.auto_update_cycle({"redis": self.redis})
        jobs = await self.redis.queued_jobs()
        return [j.function for j in jobs][before:] if len(jobs) > before else []

    async def test_default_is_every_hour(self):
        self.assertEqual(sorted(await self.run_cycle()), ["cycle_summary", "update_site"])
        await self.redis.flushdb()
        self.assertEqual(sorted(await self.run_cycle()), ["cycle_summary", "update_site"])

    async def test_every_3_hours_skips_the_rounds_in_between(self):
        await settings_store.save_operational_settings({"update_every_hours": 3})
        self.assertEqual(sorted(await self.run_cycle()), ["cycle_summary", "update_site"])
        last = float(await self.redis.get(worker.CYCLE_LAST_KEY))
        await self.redis.flushdb()
        for hours in (1, 2):                                  # giri delle ore dopo: saltati
            await self.redis.set(worker.CYCLE_LAST_KEY, str(last - hours * 3600))
            self.assertEqual(await self.run_cycle(), [])
        # tre ore dopo (qualche minuto prima del secondo esatto): tocca
        await self.redis.set(worker.CYCLE_LAST_KEY, str(time.time() - 3 * 3600 + 120))
        self.assertEqual(sorted(await self.run_cycle()), ["cycle_summary", "update_site"])

    def test_setting_stays_between_1_and_24_hours(self):
        self.assertEqual(settings_store.normalize({})["update_every_hours"], 1)
        self.assertEqual(settings_store.normalize({"update_every_hours": 0})["update_every_hours"], 1)
        self.assertEqual(settings_store.normalize({"update_every_hours": 99})["update_every_hours"], 24)
        self.assertEqual(settings_store.normalize({"update_every_hours": "6"})["update_every_hours"], 6)


if __name__ == "__main__":
    unittest.main()
