"""Un aggiornamento riuscito (o fallito) finisce SEMPRE nello storico e nelle notifiche.

Dalla 2.31.0 la riga del registro eventi usava una variabile mai definita nel giro normale
(`sl` invece di `slug`): il primo aggiornamento del sito faceva cadere il lavoro dopo
l'installazione e prima del salvataggio. Il plugin si aggiornava sul sito, ma niente storico,
niente email, niente Telegram, e il resto della coda slittava all'ora dopo."""
import json
import unittest
from datetime import timedelta
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


def wp_payload(with_plugin: bool):
    """Come lo manda il connettore WordPress: le traduzioni sono una voce sola, "language", per ultima."""
    def build(updated: bool):
        exts = []
        if with_plugin:
            exts.append({"type": "plugin", "name": "Elementor", "slug": "elementor",
                         "current": "4.3.5" if updated else "4.3.4", "new": "" if updated else "4.3.5",
                         "update": not updated})
        exts.append({"type": "language", "name": "Traduzioni (2)", "slug": "wp-translations",
                     "current": "", "new": "" if updated else "2", "update": not updated})
        return {"cms": "wp", "core": {"current": "6.8.3", "latest": "6.8.3", "update": False}, "extensions": exts}
    return build


class UpdateCycleHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.SchedulerTests.asyncSetUp(self)

    async def asyncTearDown(self):
        await fixtures.SchedulerTests.asyncTearDown(self)

    async def run_cycle(self, update_results, build=payload):
        sid = await fixtures.SchedulerTests.add_site(self, status="ok", auto_update=True, cms="wp")
        calls = {"n": 0}

        async def fetch(site, force=False, **kw):
            calls["n"] += 1
            return build(updated=calls["n"] > 1)

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


    # Traduzioni installate da WordPress insieme al plugin aggiornato nello stesso giro: la voce
    # "Traduzioni (N)" trova la coda vuota, ma nel report vanno contate come aggiornate.
    NO_LANG = {"ok": True, "error": "", "new": "nessuna traduzione da aggiornare", "noop": False, "message": "", "manual": False}

    async def test_translations_installed_with_a_plugin_count_as_updated(self):
        ok = {"ok": True, "error": "", "new": "4.3.5", "noop": False, "message": "", "manual": False, "backup": ""}
        outcome, hist, log, notify = await self.run_cycle([ok, self.NO_LANG], build=wp_payload(with_plugin=True))
        self.assertEqual([(h.ext_name, h.to_version) for h in hist],
                         [("Elementor", "4.3.5"), ("Traduzioni (2)", "traduzioni aggiornate (2/2)")])
        report = next(c.args[1] for c in notify.await_args_list if c.args[0] == "site_report")
        self.assertEqual([r["to"] for r in report["results"]], ["4.3.5", "traduzioni aggiornate (2/2)"])

    async def test_translations_alone_with_nothing_to_install_stay_as_they_are(self):
        outcome, hist, log, notify = await self.run_cycle([self.NO_LANG], build=wp_payload(with_plugin=False))
        self.assertEqual([h.to_version for h in hist], ["nessuna traduzione da aggiornare"])

    @staticmethod
    def still_offered(updated):      # WordPress propone la stessa traduzione anche dopo
        return {"cms": "wp", "core": {"current": "6.8.3", "latest": "6.8.3", "update": False},
                "extensions": [{"type": "language", "name": "Traduzioni (1)", "slug": "wp-translations",
                                "current": "", "new": "1", "update": True}]}

    async def test_translations_not_applied_go_yellow_and_retry_in_30_minutes(self):
        # Traduzioni non applicate: in giallo ("da riprovare"), mai "fallito". Niente avviso di
        # fallimento, e un nuovo tentativo dopo 30 minuti invece che dopo 24 ore.
        same = {"ok": True, "error": "", "new": "traduzioni aggiornate (0/1)", "noop": False, "message": "", "manual": False}
        outcome, hist, log, notify = await self.run_cycle([same], build=self.still_offered)
        self.assertEqual([(h.ok, h.retry, h.to_version) for h in hist], [(False, True, "")])
        self.assertIn("non applicate", hist[0].error)
        events = [c.args[0] for c in notify.await_args_list]
        self.assertNotIn("update_failed", events)
        self.assertIn("site_report", events)
        report = next(c.args[1] for c in notify.await_args_list if c.args[0] == "site_report")
        self.assertTrue(report["results"][0]["retry"])
        self.assertEqual(outcome["text"], "0 aggiornati, 1 da riprovare")
        self.assertEqual([e.level for e in log], ["warn"])
        async with self.sessions() as s:
            from app.models import Extension
            ext = (await s.execute(select(Extension).where(Extension.type == "language"))).scalar()
        self.assertIsNotNone(ext.update_failed_at)
        jobs = await self.redis.queued_jobs()
        self.assertEqual([(j.function, j.args) for j in jobs], [("update_site", (ext.site_id,))])
        self.assertAlmostEqual((jobs[0].score - jobs[0].enqueue_time.timestamp() * 1000) / 60000, 30, delta=1)
        cycle = [json.loads(x) for x in await self.redis.lrange("tg:cycle:report", 0, -1)]
        self.assertEqual((cycle[0]["failed"], [r["name"] for r in cycle[0]["retry"]]), ([], ["Traduzioni (1)"]))
        self.assertIsNone(await self.redis.get("tg:cycle:failed"))

    async def test_translations_yellow_again_wait_for_the_hourly_cycle(self):
        # gia' in giallo da prima: niente secondo tentativo programmato, ci pensa il giro
        await self.redis.set("tr:retry:1", str((fixtures.NOW - timedelta(minutes=40)).timestamp()))
        same = {"ok": True, "error": "", "new": "traduzioni aggiornate (0/1)", "noop": False, "message": "", "manual": False}
        outcome, hist, log, notify = await self.run_cycle([same], build=self.still_offered)
        self.assertEqual([(h.ok, h.retry) for h in hist], [(False, True)])
        self.assertEqual(await self.redis.queued_jobs(), [])

    async def retry_after(self, failed_ago, first_ago=None):
        """Traduzioni andate in giallo `failed_ago` fa: al giro si riprovano?"""
        from app.models import Extension
        sid = await fixtures.SchedulerTests.add_site(self, status="ok", auto_update=True, cms="wp")
        async with self.sessions() as s:
            s.add(Extension(site_id=sid, type="language", name="Traduzioni (1)", slug="wp-translations",
                            current_version="", new_version="1", update_available=True,
                            update_failed_at=fixtures.NOW - failed_ago, update_failed_version="1"))
            await s.commit()
        if first_ago is not None:
            await self.redis.set(f"tr:retry:{sid}", str((fixtures.NOW - first_ago).timestamp()))
        upd = AsyncMock(return_value={"ok": True, "error": "", "new": "traduzioni aggiornate (1/1)",
                                      "noop": False, "message": "", "manual": False})

        async def fetch(site, force=False, **kw):
            return self.still_offered(False)
        with patch.object(connectors, "fetch_status", fetch), patch.object(worker, "_update_one", upd), \
             patch.object(worker, "_visual_shot", AsyncMock(return_value=None)) as shot, \
             patch.object(worker, "notify_dispatch", AsyncMock()), patch.object(worker.asyncio, "sleep", AsyncMock()):
            await worker._update_site({"redis": self.redis}, sid, False, {})
        return upd.await_count == 1, shot, sid

    async def test_yellow_translations_retry_after_25_minutes_not_24_hours(self):
        retried, shot, sid = await self.retry_after(timedelta(minutes=30), first_ago=timedelta(minutes=30))
        self.assertTrue(retried)
        self.assertEqual(shot.await_count, 0)            # solo traduzioni: niente foto della home
        self.assertIsNone(await self.redis.get(f"tr:retry:{sid}"))   # riuscita: giallo azzerato

    async def test_yellow_translations_wait_25_minutes_between_attempts(self):
        retried, _shot, _sid = await self.retry_after(timedelta(minutes=20), first_ago=timedelta(minutes=50))
        self.assertFalse(retried)

    async def test_yellow_translations_after_3_hours_wait_like_failures(self):
        # dopo 3 ore che non passano: una volta al giorno, cosi' non arriva un avviso ogni ora
        retried, _shot, _sid = await self.retry_after(timedelta(hours=1), first_ago=timedelta(hours=4))
        self.assertFalse(retried)

    async def test_partial_translations_stay_updated(self):
        part = {"ok": True, "error": "", "new": "traduzioni aggiornate (1/2)", "noop": False, "message": "", "manual": False}
        outcome, hist, log, notify = await self.run_cycle([part], build=wp_payload(with_plugin=False))
        self.assertEqual([(h.ok, h.to_version) for h in hist], [(True, "traduzioni aggiornate (1/2)")])

    async def test_failed_plugin_does_not_turn_translations_into_updated(self):
        bad = {"ok": False, "error": "download fallito", "new": "", "noop": False, "message": "", "manual": False}
        outcome, hist, log, notify = await self.run_cycle([bad, self.NO_LANG], build=wp_payload(with_plugin=True))
        self.assertEqual([h.to_version for h in hist], ["", "nessuna traduzione da aggiornare"])


if __name__ == "__main__":
    unittest.main()
