"""Core WordPress: pacchetto proposto da wordpress.org ma non ancora sul server di download.

Il 18/11/2026 la 7.1.5 in italiano e' comparsa alle 19:05; fra le 19:05 e le 19:09 alcuni siti
hanno ricevuto "Il download non è andato a buon fine.: Not Found [download_failed]" ed e' partita
l'email rossa. Non e' un guasto del sito: in giallo ("da riprovare"), nuovo tentativo dopo 30
minuti. Se dopo 3 ore non passa ancora, torna rosso. Gli altri errori del core restano rossi."""
import json
import time
import unittest
from unittest.mock import AsyncMock, patch

import test_screenshot_schedule as fixtures
from sqlalchemy import select

from app import connectors, worker
from app.models import UpdateHistory

NOT_FOUND = {"ok": False, "new": "", "error": "Il download non è andato a buon fine.: Not Found [download_failed]"}
STATUS = {"cms": "wp", "core": {"current": "7.1.4", "latest": "7.1.5", "update": True}, "extensions": []}


class CoreNotPublishedTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.SchedulerTests.asyncSetUp(self)

    async def asyncTearDown(self):
        await fixtures.SchedulerTests.asyncTearDown(self)

    async def run_site(self, result, first_ago=None):
        sid = await fixtures.SchedulerTests.add_site(self, status="ok", auto_update=True, cms="wp")
        if first_ago is not None:
            await self.redis.set(worker._core_key(sid), str(time.time() - first_ago))

        async def fetch(site, force=False, **kw):
            return STATUS

        notify = AsyncMock(return_value={"email": True, "telegram": True})
        outcome = {}
        with patch.object(connectors, "fetch_status", fetch), \
             patch.object(worker, "_update_one", AsyncMock(return_value=dict(result))), \
             patch.object(worker, "_visual_shot", AsyncMock(return_value=None)), \
             patch.object(worker, "notify_dispatch", notify), patch.object(worker.asyncio, "sleep", AsyncMock()):
            await worker._update_site({"redis": self.redis}, sid, False, outcome)
        async with self.sessions() as s:
            hist = (await s.execute(select(UpdateHistory))).scalars().all()
        return sid, hist, [c.args[0] for c in notify.await_args_list], outcome

    async def test_not_found_is_yellow_and_retried_in_30_minutes(self):
        sid, hist, events, outcome = await self.run_site(NOT_FOUND)
        self.assertEqual([(h.ext_name, h.ok, h.retry) for h in hist], [("WordPress core", False, True)])
        self.assertNotIn("update_failed", events)
        self.assertEqual(outcome["text"], "0 aggiornati, 1 da riprovare")
        jobs = await self.redis.queued_jobs()
        self.assertEqual([(j.function, j.args) for j in jobs], [("update_site", (sid,))])
        self.assertAlmostEqual((jobs[0].score - jobs[0].enqueue_time.timestamp() * 1000) / 60000, 30, delta=1)
        cycle = [json.loads(x) for x in await self.redis.lrange("tg:cycle:report", 0, -1)]
        self.assertEqual((cycle[0]["failed"], [r["name"] for r in cycle[0]["retry"]]), ([], ["WordPress core"]))

    async def test_still_yellow_at_the_next_round_without_a_second_job(self):
        _sid, hist, events, _o = await self.run_site(NOT_FOUND, first_ago=3600)
        self.assertEqual([(h.ok, h.retry) for h in hist], [(False, True)])
        self.assertEqual(await self.redis.queued_jobs(), [])

    async def test_after_3_hours_it_is_a_real_failure(self):
        _sid, hist, events, _o = await self.run_site(NOT_FOUND, first_ago=4 * 3600)
        self.assertEqual([(h.ok, h.retry) for h in hist], [(False, False)])
        self.assertIn("update_failed", events)

    async def test_other_core_errors_stay_red(self):
        for err in ("Il download non è andato a buon fine.: cURL error 28: Operation too slow [download_failed]",
                    "Attualmente è in corso un altro aggiornamento. [locked]"):
            _sid, hist, events, _o = await self.run_site({"ok": False, "new": "", "error": err})
            self.assertEqual([h.retry for h in hist][-1], False, err)
            self.assertIn("update_failed", events)

    async def test_success_clears_the_yellow(self):
        sid, _h, _e, _o = await self.run_site({"ok": True, "error": "", "new": "7.1.5", "noop": False, "message": "",
                                               "manual": False, "backup": ""}, first_ago=1800)
        self.assertIsNone(await self.redis.get(worker._core_key(sid)))


if __name__ == "__main__":
    unittest.main()
