"""Gestione domini: import del foglio dei domini, abbinamento ai provider, scheda e controllo DNS.

I dati qui sono inventati: il foglio vero dei clienti non entra mai nel repository."""
import io
import json
import unittest
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from openpyxl import Workbook
from sqlalchemy import select

import test_screenshot_schedule as fixtures
from app import client_cards as cc
from app import client_dns, db, notify
from app.auth import require_auth
from app.db import get_session
from app.models import Client, ClientDomain, ClientProvider, EventLog, Site
from app.routers import client_cards as rcards
from app.routers import clients as rclients

CPANEL_HOWTO = "WHM alpha → Elenca account → icona cP del dominio → Email → Account email"


def workbook(extra_rows=()) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Riepilogo"
    ws.append(["Dove sono le email", "N. domini"])
    ws.append(["alpha (cPanel)", 2])
    ws.append(["Fonte: record letti dal DNS pubblico.", None])
    ws = wb.create_sheet("Email per dominio")
    ws.append(["Dominio", "Email ospitate su", "Come gestire le caselle", "Link pannello", "Account da usare",
               "Webmail", "DNS / record MX modificabili su", "Sito su", "Note"])
    ws.append(["uno.example", "alpha (cPanel)", CPANEL_HOWTO, "https://alpha.example:2087", "root (vedi PDF credenziali)",
               "https://uno.example:2096", "Registrar Uno (area clienti → DNS)", "alpha", "Sito ed email su alpha."])
    ws.append(["https://www.Due.example/", "Zoho Mail (EU)", "Pannello del cliente", None, None, None, "Registrar Uno", "—", None])
    ws.append(["tre.example", "Mailcow Test (mail.example)", "Admin Mailcow", "https://mail.example", "admin", None, "OVH", "beta", None])
    ws.append(["quattro.example", "Hosting X / Hosting Y", None, None, None, None, None, None, "Vedi la colonna G."])
    ws.append(["quattro.test", "Hosting X / Hosting Y", None, None, None, None, None, None, None])
    ws.append(["web.agenziatest.example", "alpha (cPanel)", None, None, None, None, None, None, None])
    for row in extra_rows:
        ws.append(row)
    ws = wb.create_sheet("Senza email")
    ws.append(["Dominio", "Situazione", "Sito su", "DNS su"])
    ws.append(["vecchio.example", "Il dominio non risolve: scaduto, non registrato o nome incompleto", "—", "—"])
    ws.append(["tre.example", "Nessun record MX", "beta", "OVH"])   # anche fra gli attivi: vince quello attivo
    ws = wb.create_sheet("Come fare")
    ws.append(["Provider", "Dove entrare", "Crea / elimina casella", "Cambia password", "Inoltri e risposta automatica", "Webmail"])
    ws.append(["alpha (cPanel)", "https://alpha.example:2087 → Elenca account → icona cP", "Email → Account email → Crea",
               "Email → Account email → Gestisci → Sicurezza", "Email → Inoltri", "https://<dominio>:2096"])
    ws.append(["Mailcow Test", "https://mail.example (admin)", "Caselle → Aggiungi", "Caselle → Modifica", "Alias", "https://mail.example/SOGo"])
    ws.append(["Microsoft 365 / Zoho / Register", "Pannello del cliente", "Lo fa il cliente", "—", "—", "—"])
    ws.append(["Spostare la posta su un altro provider: cambia i record MX dove sta il DNS, indicato nella colonna G del foglio \"Email per dominio\".", None, None, None, None, None])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class ParserTests(unittest.TestCase):
    def test_sheets_are_recognised_by_their_headers(self):
        r = cc.parse_workbook(workbook(), "domini.xlsx")
        self.assertEqual([(s["name"], s["kind"]) for s in r["sheets"]],
                         [("Riepilogo", ""), ("Email per dominio", "domains"), ("Senza email", "inactive"), ("Come fare", "providers")])
        self.assertEqual([d["domain"] for d in r["domains"]], ["uno.example", "www.due.example", "tre.example", "quattro.example",
                                                              "quattro.test", "web.agenziatest.example"])
        self.assertEqual([d["domain"] for d in r["inactive"]], ["vecchio.example"])
        self.assertEqual(len(r["providers"]), 3)
        self.assertEqual(len(r["notes"]), 1)
        first = r["domains"][0]
        self.assertEqual(first["manage_howto"], CPANEL_HOWTO)
        self.assertEqual(first["account_ref"], "root (vedi PDF credenziali)")
        self.assertEqual(r["domains"][1]["site_host"], "")          # "—" = vuoto
        self.assertFalse(r["inactive"][0]["active"])

    def test_excel_column_references_become_panel_places(self):
        r = cc.parse_workbook(workbook(), "domini.xlsx")
        self.assertEqual(r["notes"], ['Spostare la posta su un altro provider: cambia i record MX dove sta il DNS, indicato '
                                      'nel campo "DNS / record MX modificabili su" del dominio (Gestione domini → scheda).'])
        quattro = next(d for d in r["domains"] if d["domain"] == "quattro.example")
        self.assertEqual(quattro["notes"], 'Vedi nel campo "DNS / record MX modificabili su" del dominio (Gestione domini → scheda).')
        self.assertEqual(cc.rewrite_refs("colonna Z", r, "email per dominio"), "colonna Z")     # non risolvibile: resta

    def test_domains_are_grouped_by_common_name(self):
        self.assertEqual([cc.base_name(x) for x in ("2m-test.com", "2m-test.it", "blog.autonoleggio", "shop.autonoleggio.it",
                                                    "autonoleggio", "backend.circolo.it", "esempio.co.uk", "rossi.digital")],
                         ["2m-test", "2m-test", "autonoleggio", "autonoleggio", "autonoleggio", "circolo", "esempio", "rossi"])
        groups = cc.group_domains([{"domain": "autonoleggio", "active": False}, {"domain": "shop.autonoleggio.it", "active": True},
                                   {"domain": "blog.autonoleggio", "active": False}, {"domain": "solo.example", "active": True, "client": "Rossi"}])
        self.assertEqual(sorted((g["name"], len(g["domains"])) for g in groups.values()), [("Rossi", 1), ("shop.autonoleggio.it", 1 + 2)])

    def test_provider_matching_by_name_and_alternatives(self):
        r = cc.parse_workbook(workbook(), "domini.xlsx")

        class P:
            def __init__(self, i, n):
                self.id, self.name = i, n
        ps = [P(i, p["name"]) for i, p in enumerate(r["providers"], 1)]
        got = {d["domain"]: cc.match_provider(d["mail_host"], ps) for d in r["domains"][:4]}
        self.assertEqual(got, {"uno.example": 1, "www.due.example": 3, "tre.example": 2, "quattro.example": None})
        self.assertEqual(cc.match_provider("Register.it", ps), 3)
        self.assertIsNone(cc.match_provider("", ps))

    def test_csv_with_semicolons(self):
        data = "Dominio;Email su;Chi paga;Costo;Scadenza\nalfa.example;Aruba;Cliente;120 €;31/12/2026\n".encode()
        r = cc.parse_workbook(data, "domini.csv")
        self.assertEqual([{k: v for k, v in d.items() if k != "_sheet"} for d in r["domains"]], [{"domain": "alfa.example", "active": True, "mail_host": "Aruba",
                                         "renewal_payer": "Cliente", "renewal_cost": "120 €", "hosting_expires": date(2026, 12, 31)}])

    def test_rows_that_are_not_domains_are_reported_not_imported(self):
        r = cc.parse_workbook(workbook([["Nota libera con spazi", None, None, None, None, None, None, None, None]]), "x.xlsx")
        self.assertIn("Nota libera con spazi", r["skipped"])
        self.assertNotIn("nota libera con spazi", [d["domain"] for d in r["domains"]])


class DnsDiffTests(unittest.TestCase):
    PREV = {"mx": ["10 mx.uno.example"], "ns": ["ns1.reg.example", "ns2.reg.example"], "a": ["192.0.2.1", "192.0.2.2"], "nx": False}

    def cur(self, **kw):
        return {**self.PREV, "error": "", **kw}

    def test_first_reading_and_errors_are_not_changes(self):
        self.assertEqual(client_dns.diff(None, self.cur()), {})
        self.assertEqual(client_dns.diff(self.PREV, self.cur(mx=[], error="timeout")), {})

    def test_mx_and_ns_changes(self):
        ch = client_dns.diff(self.PREV, self.cur(mx=["0 uno-example.mail.protection.outlook.com"]))
        self.assertEqual(ch, {"mx": {"from": ["10 mx.uno.example"], "to": ["0 uno-example.mail.protection.outlook.com"]}})
        self.assertIn("ns", client_dns.diff(self.PREV, self.cur(ns=["a.cloudflare.example"])))

    def test_a_records_alert_only_when_nothing_is_left_in_common(self):
        self.assertEqual(client_dns.diff(self.PREV, self.cur(a=["192.0.2.2", "192.0.2.9"])), {})
        self.assertIn("a", client_dns.diff(self.PREV, self.cur(a=["198.51.100.7"])))

    def test_domain_that_stops_resolving(self):
        ch = client_dns.diff(self.PREV, {"mx": [], "ns": [], "a": [], "nx": True, "error": ""})
        self.assertEqual(ch, {"resolve": {"from": "risolve", "to": "non risolve"}})

    def test_unseen_change_keeps_the_oldest_value_and_disappears_when_back(self):
        first = client_dns.merge_change("", {"mx": {"from": ["10 a"], "to": ["10 b"]}})
        second = client_dns.merge_change(json.dumps(first), {"mx": {"from": ["10 b"], "to": ["10 c"]}})
        self.assertEqual(second, {"mx": {"from": ["10 a"], "to": ["10 c"]}})
        self.assertEqual(client_dns.merge_change(json.dumps(second), {"mx": {"from": ["10 c"], "to": ["10 a"]}}), {})


class CardApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.SchedulerTests.asyncSetUp(self)
        self.more = [patch.object(rcards, "_redis_get", AsyncMock(return_value=None)),
                     patch.object(db, "SessionLocal", self.sessions),
                     patch.object(notify, "SessionLocal", self.sessions)]
        for p in self.more:
            p.start()
        self.app = FastAPI()
        self.app.include_router(rcards.router)
        self.app.include_router(rclients.router)
        self.app.dependency_overrides[require_auth] = lambda: True

        async def session():
            async with self.sessions() as s:
                yield s
        self.app.dependency_overrides[get_session] = session
        async with self.sessions() as s:
            cl = Client(name="Agenzia Test", emails="", enabled=False)
            s.add(cl)
            s.add(Site(name="Uno", url="https://www.uno.example", cms="wp", token="t"))
            await s.commit()
            self.cid = cl.id

    async def asyncTearDown(self):
        for p in reversed(self.more):
            p.stop()
        await fixtures.SchedulerTests.asyncTearDown(self)

    def client(self):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")

    async def upload(self, c, data, dry_run):
        return await c.post(f"/api/clients/{self.cid}/import", data={"dry_run": "true" if dry_run else "false"},
                            files={"file": ("domini.xlsx", data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})

    async def test_import_preview_then_import_then_reimport(self):
        async with self.client() as c:
            prev = (await self.upload(c, workbook(), True)).json()
            self.assertEqual((prev["domains_new"], prev["inactive_new"], prev["providers_new"], prev["notes_added"]), (6, 1, 3, 1))
            async with self.sessions() as s:
                self.assertEqual((await s.execute(select(ClientDomain))).scalars().all(), [])   # anteprima: niente scritto
            done = (await self.upload(c, workbook(), False)).json()
            self.assertEqual((done["domains_new"], done["subs_new"]), (6, 0))
            card = (await c.get(f"/api/clients/{self.cid}/card")).json()
            self.assertEqual(card["summary"]["active"], 6)
            self.assertEqual(card["summary"]["inactive"], 1)
            self.assertIn("Spostare la posta", card["client"]["notes"])
            uno = next(d for d in card["domains"] if d["domain"] == "uno.example")
            self.assertEqual(uno["provider_name"], "alpha (cPanel)")
            self.assertEqual(uno["site"]["name"], "Uno")                 # collegato al sito di Sentinel (www. tolto)
            self.assertEqual(card["summary"]["mail_hosts"][0], {"name": "alpha (cPanel)", "count": 2})
            # nota scritta a mano: il foglio senza nota non la cancella
            r = await c.put(f"/api/clients/{self.cid}/domains/{next(d['id'] for d in card['domains'] if d['domain'] == 'tre.example')}",
                            json={"notes": "Caselle migrate a maggio", "renewal_payer": "Cliente", "hosting_expires": "2026-12-31"})
            self.assertEqual(r.status_code, 200, r.text)
            again = (await self.upload(c, workbook(), False)).json()
            self.assertEqual((again["domains_new"], again["domains_updated"], again["providers_new"], again["notes_added"]), (0, 0, 0, 0))
            card = (await c.get(f"/api/clients/{self.cid}/card")).json()
            tre = next(d for d in card["domains"] if d["domain"] == "tre.example")
            self.assertEqual((tre["notes"], tre["renewal_payer"], tre["hosting_expires"]), ("Caselle migrate a maggio", "Cliente", "2026-12-31"))
            listing = (await c.get("/api/clients")).json()
            self.assertEqual(listing[0]["domains"], 6)

    async def test_domain_crud_validation_and_expiry_level(self):
        soon = (date.today() + timedelta(days=5)).isoformat()
        async with self.client() as c:
            r = await c.post(f"/api/clients/{self.cid}/domains", json={"domain": "non valido!", "mail_host": "x"})
            self.assertEqual(r.status_code, 422)
            r = await c.post(f"/api/clients/{self.cid}/domains", json={"domain": "nuovo.example", "hosting_expires": soon})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["hosting_level"], "crit")
            self.assertEqual((await c.post(f"/api/clients/{self.cid}/domains", json={"domain": "NUOVO.example"})).status_code, 409)
            did = r.json()["id"]
            self.assertEqual((await c.delete(f"/api/clients/{self.cid}/domains/{did}")).status_code, 200)
            self.assertEqual((await c.delete(f"/api/clients/{self.cid}/domains/{did}")).status_code, 404)

    async def test_providers_crud_relinks_domains(self):
        async with self.client() as c:
            await c.post(f"/api/clients/{self.cid}/domains", json={"domain": "uno.example", "mail_host": "Aruba"})
            p = (await c.post("/api/clients/providers", json={"name": "Aruba", "login": "https://managehosting.example"})).json()
            self.assertEqual((await c.post("/api/clients/providers", json={"name": "aruba"})).status_code, 409)
            card = (await c.get(f"/api/clients/{self.cid}/card")).json()
            self.assertEqual(card["domains"][0]["provider_id"], p["id"])
            self.assertEqual(card["providers"][0]["login"], "https://managehosting.example")
            await c.delete(f"/api/clients/providers/{p['id']}")
            card = (await c.get(f"/api/clients/{self.cid}/card")).json()
            self.assertIsNone(card["domains"][0]["provider_id"])

    async def test_merge_moves_domains_and_delete_removes_them(self):
        async with self.client() as c:
            other = (await c.post("/api/clients", json={"name": "Altro"})).json()
            await c.post(f"/api/clients/{self.cid}/domains", json={"domain": "uno.example"})
            await c.post(f"/api/clients/{other['id']}/domains", json={"domain": "uno.example"})
            await c.post(f"/api/clients/{other['id']}/domains", json={"domain": "solo-altro.example"})
            await c.put(f"/api/clients/{other['id']}", json={"name": "Altro", "notes": "Nota dell'altro"})
            r = await c.post("/api/clients/merge", json={"ids": [self.cid, other["id"]], "name": "Agenzia Test"})
            self.assertEqual(r.status_code, 200, r.text)
            card = (await c.get(f"/api/clients/{self.cid}/card")).json()
            self.assertEqual(sorted(d["domain"] for d in card["domains"]), ["solo-altro.example", "uno.example"])
            self.assertIn("Nota dell'altro", card["client"]["notes"])
            await c.delete(f"/api/clients/{self.cid}")
            async with self.sessions() as s:
                self.assertEqual((await s.execute(select(ClientDomain))).scalars().all(), [])

    async def add_domains(self, *items):
        async with self.sessions() as s:
            for dom, active in items:
                s.add(ClientDomain(client_id=self.cid, domain=dom, active=active))
            await s.commit()

    async def test_dns_check_records_confirmed_changes_and_notifies_once_per_client(self):
        await self.add_domains(("uno.example", True), ("due.example", True), ("tre.example", False), ("flaky.example", True))
        state = {"uno.example": {"mx": ["10 mx.reg.example"], "ns": ["ns1.reg.example"], "a": ["192.0.2.1"], "nx": False, "error": ""},
                 "due.example": {"mx": ["10 mx.due.example"], "ns": ["ns1.reg.example"], "a": ["192.0.2.2"], "nx": False, "error": ""},
                 "tre.example": {"mx": [], "ns": [], "a": [], "nx": True, "error": ""},
                 "flaky.example": {"mx": ["10 mx.flaky.example"], "ns": [], "a": ["192.0.2.3"], "nx": False, "error": ""}}
        calls = {}

        async def lookup(domain, res=None):
            calls[domain] = calls.get(domain, 0) + 1
            if domain == "flaky.example" and calls[domain] == 2:      # lettura strana una volta sola
                return {**state[domain], "mx": ["10 altro.example"]}
            return dict(state[domain])

        dispatch = AsyncMock(return_value={"telegram": True})
        with patch.object(client_dns, "lookup", lookup), patch.object(client_dns, "CONFIRM_DELAY", 0), \
             patch.object(notify, "dispatch", dispatch):
            first = await client_dns.run_check()
            self.assertEqual(first["changed"], 0)               # prima lettura: solo la fotografia
            state["uno.example"] = {**state["uno.example"], "mx": ["0 uno-example.mail.protection.outlook.com"]}
            state["tre.example"] = {"mx": [], "ns": ["ns1.reg.example"], "a": ["192.0.2.9"], "nx": False, "error": ""}
            state["due.example"] = {**state["due.example"], "mx": [], "error": "timeout"}
            second = await client_dns.run_check()
        self.assertEqual(second["changed"], 1)                  # solo uno.example: tre non attivo, flaky non confermato, due in errore
        self.assertEqual(dispatch.await_count, 1)
        ctx = dispatch.await_args.args[1]
        self.assertEqual(ctx["client_name"], "Agenzia Test")
        self.assertEqual(ctx["changes"], [{"domain": "uno.example", "kind": "mx", "from": ["10 mx.reg.example"],
                                           "to": ["0 uno-example.mail.protection.outlook.com"]}])
        async with self.sessions() as s:
            rows = {d.domain: d for d in (await s.execute(select(ClientDomain))).scalars().all()}
            self.assertTrue(rows["uno.example"].dns_change)
            self.assertTrue(rows["tre.example"].dns_change)       # registrato sulla scheda, ma senza avviso
            self.assertFalse(rows["flaky.example"].dns_change)
            self.assertEqual(json.loads(rows["due.example"].dns_mx), ["10 mx.due.example"])   # errore: resta la lettura buona
            self.assertEqual(rows["due.example"].dns_error, "timeout")
            log = (await s.execute(select(EventLog).where(EventLog.category == "domains"))).scalars().all()
            self.assertEqual(len(log), 1)
        async with self.client() as c:
            listing = (await c.get("/api/clients")).json()
            self.assertEqual(listing[0]["dns_changes"], 2)
            self.assertEqual((await c.post(f"/api/clients/{self.cid}/dns-ack")).json()["acked"], 2)
            card = (await c.get(f"/api/clients/{self.cid}/card")).json()
            self.assertEqual(card["summary"]["dns_changes"], 0)

    async def upload_subs(self, c, dry_run=False):
        return (await c.post(f"/api/clients/{self.cid}/import", data={"dry_run": "true" if dry_run else "false", "subs": "true"},
                             files={"file": ("domini.xlsx", workbook(), "application/octet-stream")})).json()

    async def test_import_with_sub_clients(self):
        async with self.client() as c:
            await c.put(f"/api/clients/{self.cid}", json={"name": "Agenzia Test"})
            prev = await self.upload_subs(c, dry_run=True)
            # uno, due, tre, quattro(.example + .example.org insieme), vecchio; web.agenziatest resta sul cliente
            self.assertEqual(prev["subs_new"], 5)
            async with self.sessions() as s:
                self.assertEqual(len((await s.execute(select(Client))).scalars().all()), 1)   # anteprima: nessun sottocliente
            done = await self.upload_subs(c)
            self.assertEqual(done["subs_new"], 5)
            card = (await c.get(f"/api/clients/{self.cid}/card")).json()
            subs = {x["name"]: x for x in card["subs"]}
            self.assertEqual(sorted(subs), ["quattro.test", "tre.example", "uno.example", "vecchio.example", "www.due.example"])
            self.assertEqual(subs["quattro.test"]["active"], 2)
            own = [d["domain"] for d in card["domains"] if d["client_id"] == self.cid]
            self.assertEqual(own, ["web.agenziatest.example"])          # ha il nome del cliente: resta sul principale
            self.assertEqual(len(card["targets"]), 6)
            listing = (await c.get("/api/clients")).json()
            root = next(x for x in listing if x["id"] == self.cid)
            self.assertEqual((root["subs"], root["domains"]), (5, 6))
            # reimport: niente doppioni, i domini restano dove sono
            again = await self.upload_subs(c)
            self.assertEqual((again["subs_new"], again["domains_new"]), (0, 0))
            # spostare un dominio sul cliente principale, e la scheda del sottocliente
            uno = next(d for d in card["domains"] if d["domain"] == "uno.example")
            r = await c.put(f"/api/clients/{self.cid}/domains/{uno['id']}", json={"client_id": self.cid})
            self.assertEqual(r.json()["client_id"], self.cid)
            other = (await c.post("/api/clients", json={"name": "Estraneo"})).json()
            self.assertEqual((await c.put(f"/api/clients/{self.cid}/domains/{uno['id']}", json={"client_id": other["id"]})).status_code, 422)
            sub = subs["quattro.test"]
            sc = (await c.get(f"/api/clients/{sub['id']}/card")).json()
            self.assertEqual(sc["parent"], {"id": self.cid, "name": "Agenzia Test"})
            self.assertEqual(sorted(d["domain"] for d in sc["domains"]), ["quattro.example", "quattro.test"])
            # un sottocliente non puo' avere sottoclienti, un principale con sottoclienti non diventa sottocliente
            self.assertEqual((await c.post("/api/clients", json={"name": "X", "parent_id": sub["id"]})).status_code, 422)
            self.assertEqual((await c.put(f"/api/clients/{self.cid}", json={"name": "Agenzia Test", "parent_id": other["id"]})).status_code, 422)
            # eliminare il principale porta via sottoclienti e schede
            await c.delete(f"/api/clients/{self.cid}")
            async with self.sessions() as s:
                self.assertEqual([x.name for x in (await s.execute(select(Client))).scalars().all()], ["Estraneo"])
                self.assertEqual((await s.execute(select(ClientDomain))).scalars().all(), [])

    async def test_dns_alert_of_a_sub_client_goes_to_its_main_client(self):
        async with self.sessions() as s:
            sub = Client(name="Sotto", emails="", parent_id=self.cid)
            s.add(sub)
            await s.flush()
            s.add(ClientDomain(client_id=sub.id, domain="sotto.example", active=True, dns_mx='["10 a.example"]', dns_ns="[]", dns_a="[]",
                               dns_checked_at=__import__("datetime").datetime(2026, 10, 1)))
            await s.commit()

        async def lookup(domain, res=None):
            return {"mx": ["10 b.example"], "ns": [], "a": [], "nx": False, "error": ""}
        dispatch = AsyncMock(return_value={})
        with patch.object(client_dns, "lookup", lookup), patch.object(client_dns, "CONFIRM_DELAY", 0), patch.object(notify, "dispatch", dispatch):
            res = await client_dns.run_check(self.cid)            # il controllo del principale include i sottoclienti
        self.assertEqual(res["changed"], 1)
        self.assertEqual(dispatch.await_args.args[1]["client_name"], "Agenzia Test")

    async def test_domains_from_sentinel_folders_and_folder_rename(self):
        from app.routers import sites as rsites
        async with self.sessions() as s:
            s.add_all([Site(name="Due", url="https://due.example", cms="wp", token="t", tags="Clienti"),
                       Site(name="Tre", url="https://www.tre.example/", cms="wp", token="t", tags="Clienti/Agenzia,Altro"),
                       Site(name="Quattro", url="https://quattro.example", cms="joomla", token="t", tags="")])
            await s.commit()
        self.app.include_router(rsites.router)
        with patch.object(rcards, "_site_server_labels", AsyncMock(return_value={})):
            async with self.client() as c:
                folders = {f["folder"]: f for f in (await c.get("/api/clients/site-folders")).json()}
                self.assertEqual(sorted(folders), ["Clienti", "Clienti/Agenzia", "__none"])
                self.assertEqual(folders["__none"]["sites"], 2)          # "Uno" del fixture e "Quattro"
                # "Agenzia Test" (cliente del fixture, creato a mano) non c'entra; "Clienti" non esiste: si crea
                res = (await c.post("/api/clients/from-folders", json={"folders": ["Clienti", "Clienti/Agenzia", "__none"]})).json()
                self.assertEqual((res["entries_new"], res["domains_new"]), (3, 4))
                again = (await c.post("/api/clients/from-folders", json={"folders": ["Clienti", "Clienti/Agenzia"]})).json()
                self.assertEqual((again["entries_new"], again["domains_new"], again["already"]), (0, 0, 2))
                listing = {x["name"]: x for x in (await c.get("/api/clients")).json()}
                self.assertEqual(listing["Agenzia"]["parent_id"], listing["Clienti"]["id"])
                self.assertEqual(listing["Agenzia"]["folder"], "Clienti/Agenzia")
                card = (await c.get(f"/api/clients/{listing['Clienti']['id']}/card")).json()
                self.assertEqual(sorted(d["domain"] for d in card["domains"]), ["due.example", "tre.example"])
                # rinominare la cartella rinomina le voci collegate
                r = await c.post("/api/sites/bulk/rename-folder", json={"from": "Clienti", "to": "Clienti diretti"})
                self.assertEqual(r.status_code, 200, r.text)
                listing = {x["name"]: x for x in (await c.get("/api/clients")).json()}
                self.assertIn("Clienti diretti", listing)
                self.assertEqual(listing["Agenzia"]["folder"], "Clienti diretti/Agenzia")

    async def test_folder_into_existing_entry_as_sub_clients(self):
        async with self.sessions() as s:
            s.add_all([Site(name="Gia", url="https://gia.example", cms="wp", token="t", tags="Esterni"),
                       Site(name="Nuovo", url="https://nuovo.example", cms="wp", token="t", tags="Esterni"),
                       Site(name="Nuovo IT", url="https://nuovo.it", cms="wp", token="t", tags="Esterni/Sotto")])
            sub = Client(name="gia.example", emails="", parent_id=self.cid)
            s.add(sub)
            await s.flush()
            s.add(ClientDomain(client_id=sub.id, domain="gia.example", active=True))
            await s.commit()
        with patch.object(rcards, "_site_server_labels", AsyncMock(return_value={})):
            async with self.client() as c:
                res = (await c.post("/api/clients/from-folders", json={"folders": ["Esterni", "Esterni/Sotto"],
                                                                         "targets": {"Esterni": self.cid}})).json()
                self.assertEqual((res["entries_new"], res["subs_new"], res["domains_new"], res["already"]), (0, 1, 2, 1))
                card = (await c.get(f"/api/clients/{self.cid}/card")).json()
                subs = {x["name"]: x for x in card["subs"]}
                self.assertEqual(sorted(subs), ["gia.example", "nuovo.example"])          # nuovo.it nello stesso di nuovo.example
                self.assertEqual(subs["nuovo.example"]["active"], 2)
                listing = {x["name"]: x for x in (await c.get("/api/clients")).json()}
                self.assertEqual(listing["Agenzia Test"]["folder"], "")                  # non collegata: non si rinomina
                self.assertNotIn("Esterni", listing)
                bad = await c.post("/api/clients/from-folders", json={"folders": ["Esterni"], "targets": {"Esterni": subs["gia.example"]["id"]}})
                self.assertEqual(bad.status_code, 422)

    async def test_mail_host_is_guessed_from_mx_when_empty(self):
        await self.add_domains(("ms.example", True), ("stesso.example", True), ("scritto.example", True))
        async with self.sessions() as s:
            d = (await s.execute(select(ClientDomain).where(ClientDomain.domain == "scritto.example"))).scalar()
            d.mail_host = "Il mio server"
            d = (await s.execute(select(ClientDomain).where(ClientDomain.domain == "stesso.example"))).scalar()
            d.site_host = "server-a"
            await s.commit()
        answers = {"ms.example": ["0 ms-example.mail.protection.outlook.com"], "stesso.example": ["10 mail.stesso.example"],
                   "scritto.example": ["10 mx.aruba.it"]}

        async def lookup(domain, res=None):
            return {"mx": answers[domain], "ns": [], "a": ["192.0.2.5"], "nx": False, "error": ""}

        async def query(res, name, rtype):
            return (["192.0.2.5"], "") if name == "mail.stesso.example" else ([], "")
        with patch.object(client_dns, "lookup", lookup), patch.object(client_dns, "_query", query):
            await client_dns.run_check(notify=False)
        async with self.sessions() as s:
            got = {d.domain: d.mail_host for d in (await s.execute(select(ClientDomain))).scalars().all()}
        self.assertEqual(got, {"ms.example": "Microsoft 365", "stesso.example": "Stesso server del sito (server-a)",
                               "scritto.example": "Il mio server"})

    async def test_worker_job_respects_switch_and_running_flag(self):
        from app import worker
        run = AsyncMock(return_value={})
        with patch.object(client_dns, "run_check", run):
            await client_dns.save_config({"enabled": False})
            await worker.client_dns_check({"redis": self.redis})                 # giro automatico spento
            self.assertEqual(run.await_count, 0)
            await worker.client_dns_check({"redis": self.redis}, 7, True)        # pulsante: parte lo stesso
            run.assert_awaited_with(7)
            await client_dns.save_config({"enabled": True})
            await self.redis.set(client_dns.RUNNING_KEY, b"running")
            await worker.client_dns_check({"redis": self.redis})                 # gia' in corso: non si sovrappone
            self.assertEqual(run.await_count, 1)
            await self.redis.delete(client_dns.RUNNING_KEY)
            await worker.client_dns_check({"redis": self.redis})
            self.assertEqual(run.await_count, 2)
        self.assertIsNone(await self.redis.get(client_dns.RUNNING_KEY))

    async def test_notification_lists_every_change(self):
        cfg = notify.default_config("client_dns_changed", "it")
        r = notify.render("client_dns_changed", cfg, notify.SAMPLE["client_dns_changed"], "it")
        self.assertEqual(r["error"], "")
        self.assertIn("2 domini con DNS cambiati", r["subject"])
        self.assertIn("esempio.it</b> — MX: 10 mail.esempio.it → 0 esempio-it.mail.protection.outlook.com", r["body_telegram"])
        self.assertIn("esempio.com</b> — Dominio: risolve → non risolve", r["body_telegram"])
        en = notify.render("client_dns_changed", notify.default_config("client_dns_changed", "en"),
                           notify.sample_context("client_dns_changed", "en"), "en")
        self.assertIn("2 domains with changed DNS", en["subject"])
        self.assertIn("Domain: resolves → does not resolve", en["body_telegram"])


if __name__ == "__main__":
    unittest.main()
