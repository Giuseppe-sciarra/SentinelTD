"""Un aggiornamento riuscito (o fallito) finisce SEMPRE nello storico e nelle notifiche.

Dalla 2.31.0 la riga del registro eventi usava una variabile mai definita nel giro normale
(`sl` invece di `slug`): il primo aggiornamento del sito faceva cadere il lavoro dopo
l'installazione e prima del salvataggio. Il plugin si aggiornava sul sito, ma niente storico,
niente email, niente Telegram, e il resto della coda slittava all'ora dopo."""
import json
import unittest
from unittest.mock import AsyncMock, patch

import test_screenshot_schedule as fixtures
from sqlalchemy import select

from app import connectors, worker
from app.models import Site, UpdateHistory, EventLog


def payload(updated: bool):
    return {"cms": "wp", "core": {"current": "6.8.3", "latest": "6.8.3", "update": False},
            "extensions": [
                {"type": "plugin", "name": "Rank Math", "slug": "seo-by-rank-math",
                 "current": "1.0.271" if updated else "1.0.270", "new": "" if updated else "1.0.271",
                 "update": not updated},
                {"type": "translation", "name": "Traduzioni (it_IT)", "slug": "it_IT",
                 "current": "", "new": "" if updated else "2026-10-09", "update": not updated},
            ]}


class UpdateCycleHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.SchedulerTests.asyncSetUp(self)

    async def asyncTearDown(self):
        await fixtures.SchedulerTests.asyncTearDown(self)

    async def run_cycle(self, update_results):
        sid = await fixtures.SchedulerTests.add_site(self, status="ok", auto_update=True, cms="wp")
        calls = {"n": 0}

        async def fetch(site, force=False, **kw):
            calls["n"] += 1
            return payload(updated=calls["n"] > 1)

        notify = AsyncMock(return_value={"email": True, "telegram": True})
        with patch.object(connectors, "fetch_status", fetch), \
             patch.object(worker, "_update_one", AsyncMock(side_effect=update_results)), \
             patch.object(worker, "_visual_shot", AsyncMock(return_value=None)), \
             patch.object(worker, "notify_dispatch", notify), \
             patch.object(worker.asyncio, "sleep", AsyncMock()):
            outcome = {}
            await worker._update_site({"redis": self.redis}, sid, False, outcome)
        async with self.sessions() as s:
            hist = (await s.execute(select(UpdateHistory).order_by(UpdateHistory.id))).scalars().all()
            log = (await s.execute(select(EventLog).where(EventLog.category == "updates"))).scalars().all()
        return outcome, hist, log, notify

    async def test_successful_updates_reach_history_report_and_cycle_summary(self):
        ok = lambda new: {"ok": True, "error": "", "new": new, "noop": False, "message": "", "manual": False, "backup": ""}
        outcome, hist, log, notify = await self.run_cycle([ok("1.0.271"), ok("2026-10-09")])
        self.assertEqual([(h.ext_name, h.ok) for h in hist],
                         [("Rank Math", True), ("Traduzioni (it_IT)", True)])
        self.assertEqual(outcome["text"], "2 aggiornati")
        self.assertIn("site_report", [c.args[0] for c in notify.await_args_list])
        self.assertEqual(sorted(json.loads(e.details).get("slug") for e in log), ["it_IT", "seo-by-rank-math"])
        self.assertEqual(await self.redis.get("tg:cycle:applied"), b"2")

    async def test_failed_update_is_recorded_and_notified(self):
        bad = {"ok": False, "error": "download fallito", "new": "", "noop": False, "message": "", "manual": False}
        ok = {"ok": True, "error": "", "new": "2026-10-09", "noop": False, "message": "", "manual": False, "backup": ""}
        outcome, hist, log, notify = await self.run_cycle([bad, ok])
        self.assertEqual([(h.ext_name, h.ok) for h in hist],
                         [("Rank Math", False), ("Traduzioni (it_IT)", True)])
        events = [c.args[0] for c in notify.await_args_list]
        self.assertIn("update_failed", events)
        self.assertIn("site_report", events)


if __name__ == "__main__":
    unittest.main()
