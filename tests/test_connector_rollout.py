"""Giro notturno del connettore: si aggiorna solo la copia in uso, nella sua cartella; mai un
mu-plugin, mai una seconda copia, nessun reinstallo ogni notte. E il connettore che risponde
500 con la home che funziona: avviso "Connettore non risponde", non "server lento"."""
import io
import json
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import test_screenshot_schedule as fixtures
import httpx
from sqlalchemy import select

from app import connectors, worker
from app.models import Site, EventLog, Extension
from app.routers import connectors as rconn
from app.routers import packages as rpackages

T0 = datetime(2026, 10, 10, 2, 30, tzinfo=timezone.utc)
TARGET = "2.36.0"


class MovingClock(datetime):
    cur = T0

    @classmethod
    def now(cls, tz=None):
        return cls.cur if tz else cls.cur.replace(tzinfo=None)


def at(minutes):
    MovingClock.cur = T0 + timedelta(minutes=minutes)


def wp_zip(root="td-panopticon"):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr(f"{root}/", "")
        z.writestr(f"{root}/td-panopticon.php", "<?php\n/**\n * Plugin Name: Sentinel TD Agent\n * Version: 2.36.0\n */\n")
        z.writestr(f"{root}/includes/helper.php", "<?php\n")
    return out.getvalue()


def zip_names(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return sorted(z.namelist())


def payload(version="2.35.0", plugins=(), **extra):
    return {"cms": "wp", "connector": version, "core": {"current": "6.8.3", "latest": "6.8.3", "update": False},
            "extensions": [{"type": "plugin", "name": n, "slug": s, "current": v, "new": "", "update": False}
                           for n, s, v in plugins], **extra}


class ModeTests(unittest.TestCase):
    def test_declared_by_new_connectors(self):
        self.assertEqual(connectors.connector_mode_of({"mode": "mu"}, "2.36.0", []), "mu")
        self.assertEqual(connectors.connector_mode_of({"mode": "plugin", "folder": "sentinel-td"}, "2.36.0", []), "plugin:sentinel-td")
        self.assertEqual(connectors.connector_mode_of({"mode": "plugin", "folder": "../x"}, "2.36.0", []), "other")
        self.assertEqual(connectors.connector_mode_of({"mode": "other"}, "2.36.0", []), "other")

    def test_old_connectors_from_the_plugin_list(self):
        plugins = [{"type": "plugin", "name": "Sentinel TD Agent", "slug": "td-panopticon", "current": "2.35.0"}]
        self.assertEqual(connectors.connector_mode_of({}, "2.35.0", plugins), "plugin:td-panopticon")
        # i mu-plugin non sono nell'elenco dei plugin: la copia in uso non c'e' -> mu
        self.assertEqual(connectors.connector_mode_of({}, "2.35.0", []), "mu")
        # copia normale vecchia e spenta accanto a un mu-plugin piu' nuovo
        old_copy = [{"type": "plugin", "name": "Sentinel TD Agent", "slug": "td-panopticon", "current": "2.30.0"}]
        self.assertEqual(connectors.connector_mode_of({}, "2.35.0", old_copy), "mu")
        self.assertEqual(connectors.connector_mode_of({}, "", []), "")
        # file singolo nella radice dei plugin: non e' un mu-plugin
        root_file = [{"type": "plugin", "name": "Sentinel TD Agent", "slug": ".", "current": "2.35.0"}]
        self.assertEqual(connectors.connector_mode_of({}, "2.35.0", root_file), "other")


class PackageTests(unittest.TestCase):
    def test_root_folder_renamed_to_the_site_folder(self):
        data = rconn.wp_package_for_folder(wp_zip(), "sentinel-td")
        self.assertEqual(zip_names(data), ["sentinel-td/", "sentinel-td/includes/helper.php", "sentinel-td/td-panopticon.php"])
        same = wp_zip()
        self.assertIs(rconn.wp_package_for_folder(same, "td-panopticon"), same)
        with self.assertRaises(ValueError):
            rconn.wp_package_for_folder(same, "../plugins")
        flat = io.BytesIO()
        with zipfile.ZipFile(flat, "w") as z:
            z.writestr("td-panopticon.php", "<?php\n")
        with self.assertRaises(ValueError):                 # senza cartella sarebbe una copia nuova
            rconn.wp_package_for_folder(flat.getvalue(), "td-panopticon")


class RolloutTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.SchedulerTests.asyncSetUp(self)
        at(0)
        from app import db
        self.tmp = tempfile.TemporaryDirectory()
        self.extra = [patch.object(worker, "datetime", MovingClock), patch.object(connectors, "datetime", MovingClock),
                      patch.object(db, "SessionLocal", self.sessions), patch.object(rconn, "shipped_version", lambda kind: TARGET),
                      patch.object(rpackages, "PACKAGES_DIR", self.tmp.name)]
        for p in self.extra:
            p.start()
        self.notify = AsyncMock(return_value={"email": False, "telegram": True})
        self.np = patch.object(worker, "notify_dispatch", self.notify)
        self.np.start()

    async def asyncTearDown(self):
        self.np.stop()
        for p in reversed(self.extra):
            p.stop()
        self.tmp.cleanup()
        await fixtures.SchedulerTests.asyncTearDown(self)

    async def add_site(self, **kw):
        # di base: sito a posto e controllato da poco (elenco dei plugin affidabile)
        return await fixtures.SchedulerTests.add_site(self, **({"status": "ok", "last_checked": T0 - timedelta(hours=1)} | kw))

    async def read(self, sid):
        async with self.sessions() as s:
            return await s.get(Site, sid)

    def events(self):
        return [c.args[0] for c in self.notify.await_args_list]

    async def test_check_records_where_the_connector_runs(self):
        sid = await self.add_site(status="ok")
        async with self.sessions() as s:
            site = await s.get(Site, sid)
            with patch.object(connectors, "fetch_status", AsyncMock(return_value=payload("2.35.0"))):
                await connectors.apply_status(s, site)
            self.assertEqual(site.connector_mode, "mu")
            with patch.object(connectors, "fetch_status", AsyncMock(return_value=payload("2.36.0", mode="plugin", folder="sentinel-td"))):
                await connectors.apply_status(s, site)
            self.assertEqual(site.connector_mode, "plugin:sentinel-td")

    async def add_copy(self, sid, folder, version):
        async with self.sessions() as s:
            s.add(Extension(site_id=sid, type="plugin", name="Sentinel TD Agent", slug=folder,
                            current_version=version, new_version="", update_available=False))
            await s.commit()

    async def sites(self):
        ids = {}
        ids["regular"] = await self.add_site(name="Normale", connector_version="2.35.0", connector_mode="plugin:td-panopticon")
        await self.add_copy(ids["regular"], "td-panopticon", "2.35.0")
        ids["other_dir"] = await self.add_site(name="Cartella nuova", connector_version="2.35.0", connector_mode="plugin:sentinel-td")
        await self.add_copy(ids["other_dir"], "sentinel-td", "2.35.0")
        ids["mu"] = await self.add_site(name="Sito mu", connector_version="2.35.0", connector_mode="mu")
        ids["mu_leftover"] = await self.add_site(name="Mu con copia vecchia", connector_version="2.35.0", connector_mode="mu")
        await self.add_copy(ids["mu_leftover"], "td-panopticon", "2.30.0")
        ids["double"] = await self.add_site(name="Due copie", connector_version="2.35.0", connector_mode="plugin:sentinel-td")
        await self.add_copy(ids["double"], "sentinel-td", "2.35.0")
        await self.add_copy(ids["double"], "td-panopticon", "2.30.0")
        ids["held"] = await self.add_site(name="Gia' fatto", connector_version="2.35.0", connector_mode="mu", connector_hold=TARGET)
        await self.add_copy(ids["held"], "td-panopticon", "2.30.0")
        ids["unknown"] = await self.add_site(name="Mai visto", status="unknown", last_checked=None)
        ids["stale"] = await self.add_site(name="Controllato tempo fa", connector_version="2.35.0", connector_mode="plugin:td-panopticon",
                                           last_checked=T0 - timedelta(days=3))
        await self.add_copy(ids["stale"], "td-panopticon", "2.35.0")
        ids["held_regular"] = await self.add_site(name="Installato, non cambia", connector_version="2.35.0",
                                                  connector_mode="plugin:td-panopticon", connector_hold=TARGET)
        await self.add_copy(ids["held_regular"], "td-panopticon", "2.35.0")
        ids["current"] = await self.add_site(name="Aggiornato", connector_version=TARGET, connector_mode="plugin:td-panopticon")
        await self.add_copy(ids["current"], "td-panopticon", TARGET)
        return ids

    async def test_only_existing_copies_are_updated_never_a_new_one_never_the_mu(self):
        ids = await self.sites()
        jold = await self.add_site(name="Joomla", cms="joomla", connector_version="1.40.0")
        jheld = await self.add_site(name="Joomla fatto", cms="joomla", connector_version="1.40.0", connector_hold=TARGET)
        async with self.sessions() as s:
            go, keep = await rconn._rollout_split("wp", s)
            jgo, jkeep = await rconn._rollout_split("joomla", s)
        folders = {x.id: x._rollout_folders for x in go}
        self.assertEqual(folders, {ids["regular"]: ["td-panopticon"], ids["other_dir"]: ["sentinel-td"],
                                   ids["mu_leftover"]: ["td-panopticon"],          # la copia vecchia si zittisce
                                   ids["double"]: ["sentinel-td", "td-panopticon"]})  # quella in uso per prima
        self.assertEqual({x.id: rconn._keep_reason(x) for x in keep},
                         {ids["mu"]: "mu-plugin", ids["held"]: "mu-plugin", ids["unknown"]: "non ancora controllato",
                          ids["stale"]: "da ricontrollare", ids["held_regular"]: "già installato"})
        self.assertEqual([x.id for x in jgo], [jold])
        self.assertEqual([x.id for x in jkeep], [jheld])

    async def test_rollout_overwrites_each_copy_in_its_folder_without_activating_and_only_once(self):
        ids = await self.sites()
        pool = SimpleNamespace(set=self.redis.set, hset=self.redis.hset, expire=self.redis.expire,
                               enqueue_job=self.redis.enqueue_job, aclose=AsyncMock())
        with patch.object(rconn, "connector_package", AsyncMock(return_value=(wp_zip(), "sentinel-td-wp-2.36.0.zip"))), \
             patch("arq.create_pool", AsyncMock(return_value=pool)):
            async with self.sessions() as s:
                targets = await rconn.outdated_sites("wp", s)
            res = await rconn.start_connector_rollout("wp", targets, "connettore wp 2.36.0 (notturno)")
        self.assertEqual(res["total"], 4)
        meta = json.loads(await self.redis.get(f"inst:{res['job']}"))
        self.assertEqual((meta["connector"], meta["target"], meta["activate"]), ("wp", TARGET, False))

        sent = {}

        async def fake_install(site, content, filename, kind, activate):
            self.assertFalse(activate)
            sent.setdefault(site.id, []).append(zip_names(content)[0])
            return {"ok": True, "error": "", "name": "Sentinel TD Agent", "slug": zip_names(content)[0].rstrip("/"),
                    "new": TARGET, "type": "plugin", "activated": False}
        with patch("app.routers.install._install_one", fake_install):
            for sid in meta["site_ids"]:
                await worker.install_site({"redis": self.redis}, res["job"], sid)
        self.assertEqual(sent, {ids["regular"]: ["td-panopticon/"], ids["other_dir"]: ["sentinel-td/"],
                                ids["mu_leftover"]: ["td-panopticon/"], ids["double"]: ["sentinel-td/", "td-panopticon/"]})
        # una volta per versione: anche se il sito non dichiarasse ancora la nuova, il giro non riprova
        for key in ("regular", "other_dir", "mu_leftover", "double"):
            self.assertEqual((await self.read(ids[key])).connector_hold, TARGET)
        async with self.sessions() as s:
            self.assertEqual(await rconn.outdated_sites("wp", s), [])

    async def test_failed_install_is_retried_next_night(self):
        sid = await self.add_site(name="Normale", connector_version="2.35.0", connector_mode="plugin:td-panopticon")
        await self.add_copy(sid, "td-panopticon", "2.35.0")
        pool = SimpleNamespace(set=self.redis.set, hset=self.redis.hset, expire=self.redis.expire,
                               enqueue_job=self.redis.enqueue_job, aclose=AsyncMock())
        with patch.object(rconn, "connector_package", AsyncMock(return_value=(wp_zip(), "c.zip"))), \
             patch("arq.create_pool", AsyncMock(return_value=pool)):
            async with self.sessions() as s:
                res = await rconn.start_connector_rollout("wp", await rconn.outdated_sites("wp", s), "x")
        with patch("app.routers.install._install_one", AsyncMock(return_value={"ok": False, "error": "DISALLOW_FILE_MODS attivo"})):
            await worker.install_site({"redis": self.redis}, res["job"], sid)
        self.assertEqual((await self.read(sid)).connector_hold, "")
        async with self.sessions() as s:
            self.assertEqual([x.id for x in await rconn.outdated_sites("wp", s)], [sid])


class ConnectorCrashTests(unittest.IsolatedAsyncioTestCase):
    """Il connettore risponde 500 (errore critico di WordPress, eccezione del plugin Joomla)."""

    async def asyncSetUp(self):
        await RolloutTests.asyncSetUp(self)

    async def asyncTearDown(self):
        await RolloutTests.asyncTearDown(self)

    add_site = RolloutTests.add_site
    read = RolloutTests.read
    events = RolloutTests.events

    def crash(self, code=500, body=None):
        req = httpx.Request("GET", "https://example.test/wp-json/tdpanopticon/v1/status")
        body = body if body is not None else {
            "code": "internal_server_error", "message": "<p>Si è verificato un errore critico nel sito.</p>",
            "data": {"status": 500, "error": {"type": 1, "message": "Uncaught Error: Call to undefined function foo()"}}}
        return httpx.HTTPStatusError("HTTP", request=req, response=httpx.Response(code, json=body, request=req))

    async def run_window(self, sid, error, probe, minutes=7):
        with patch.object(connectors, "fetch_status", AsyncMock(side_effect=error)), \
             patch.object(connectors, "probe_home", probe):
            for minute in range(0, minutes):
                at(minute)
                await worker.poll_site({"redis": self.redis}, sid)

    async def test_crashing_connector_with_working_home_alerts_instead_of_slow(self):
        sid = await self.add_site(status="ok")
        probe = AsyncMock(return_value=(200, 0.9))
        await self.run_window(sid, self.crash(), probe)
        self.assertEqual(self.events(), ["connector_down"])
        ctx = self.notify.await_args_list[0].args[1]
        self.assertIn("errore interno del connettore (HTTP 500): Uncaught Error: Call to undefined function foo()", ctx["reason"])
        site = await self.read(sid)
        self.assertEqual((site.status, site.offline_kind), ("error", "connector"))
        self.assertEqual(probe.await_count, 1)

    async def test_joomla_exception_message_is_kept(self):
        sid = await self.add_site(status="ok", cms="joomla")
        await self.run_window(sid, self.crash(body={"success": False, "data": None, "message": "Table '#__updates' doesn't exist"}),
                              AsyncMock(return_value=(200, 0.5)))
        self.assertEqual(self.events(), ["connector_down"])
        self.assertIn("Table '#__updates' doesn't exist", self.notify.await_args_list[0].args[1]["reason"])

    async def test_500_with_home_answering_4xx_is_the_connector_not_a_slow_server(self):
        sid = await self.add_site(status="ok")
        await self.run_window(sid, self.crash(), AsyncMock(return_value=(403, 0.4)))
        self.assertEqual(self.events(), ["connector_down"])

    async def test_500_with_dead_home_is_still_offline(self):
        sid = await self.add_site(status="ok")
        await self.run_window(sid, self.crash(), AsyncMock(return_value=(500, 0.3)))
        self.assertEqual(self.events(), ["site_offline"])

    async def test_503_with_working_home_is_still_a_slow_server(self):
        sid = await self.add_site(status="ok")
        await self.run_window(sid, self.crash(503, {}), AsyncMock(return_value=(200, 0.5)), minutes=6)
        self.assertEqual(self.events(), [])
        self.assertEqual((await self.read(sid)).status, "slow")

    async def test_confirmed_connector_episode_stays_without_new_probes(self):
        sid = await self.add_site(status="error", error="Connettore: errore interno del connettore (HTTP 500)",
                                  offline_kind="connector", offline_notified=True)
        probe = AsyncMock(return_value=(200, 0.9))
        with patch.object(connectors, "fetch_status", AsyncMock(side_effect=self.crash())), \
             patch.object(connectors, "probe_home", probe):
            await worker.poll_site({"redis": self.redis}, sid)
        probe.assert_not_awaited()
        self.assertEqual(self.events(), [])
        site = await self.read(sid)
        self.assertEqual((site.status, site.offline_kind), ("error", "connector"))
        self.assertEqual(await self.redis.queued_jobs(), [])


if __name__ == "__main__":
    unittest.main()
