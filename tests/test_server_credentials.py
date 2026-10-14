"""Gestione server: accessi del pannello e FTP, con password cifrate che non escono nell'elenco."""
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from sqlalchemy import select

import test_screenshot_schedule as fixtures
from app.auth import require_auth
from app.db import get_session
from app.models import EventLog, ServerCredential
from app.routers import server_credentials as rcred


class ServerCredentialTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.SchedulerTests.asyncSetUp(self)
        self.app = FastAPI()
        self.app.include_router(rcred.router)
        self.app.dependency_overrides[require_auth] = lambda: True

        async def session():
            async with self.sessions() as s:
                yield s
        self.app.dependency_overrides[get_session] = session

    async def asyncTearDown(self):
        await fixtures.SchedulerTests.asyncTearDown(self)

    def client(self):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")

    async def test_passwords_are_encrypted_hidden_and_revealed_on_request(self):
        key = "192.0.2.10|web01"
        async with self.client() as c:
            r = await c.post("/api/server-credentials/save", json={
                "key": key, "panel_user": "admin", "panel_password": "Pa$$w0rd!", "ftp_proto": "sftp",
                "ftp_host": "ftp.esempio.it", "ftp_port": "2222", "ftp_user": "deploy", "ftp_password": "segreta"})
            self.assertEqual(r.status_code, 200, r.text)
            listing = (await c.get("/api/server-credentials")).json()
            self.assertEqual(listing[key]["panel_password"], True)
            self.assertNotIn("Pa$$w0rd!", r.text + str(listing))
            async with self.sessions() as s:
                row = await s.get(ServerCredential, key)
                self.assertNotIn("Pa$$w0rd!", row.panel_password)               # cifrata nel database
            self.assertEqual((await c.post("/api/server-credentials/reveal", json={"key": key, "field": "panel"})).json(), {"password": "Pa$$w0rd!"})
            self.assertEqual((await c.post("/api/server-credentials/reveal", json={"key": key, "field": "ftp"})).json(), {"password": "segreta"})
            # password vuota = resta quella di prima; clear_ = tolta
            await c.post("/api/server-credentials/save", json={"key": key, "panel_user": "admin2", "panel_password": ""})
            self.assertEqual((await c.post("/api/server-credentials/reveal", json={"key": key, "field": "panel"})).json(), {"password": "Pa$$w0rd!"})
            await c.post("/api/server-credentials/save", json={"key": key, "clear_ftp_password": True})
            self.assertEqual((await c.post("/api/server-credentials/reveal", json={"key": key, "field": "ftp"})).status_code, 404)
            self.assertEqual((await c.post("/api/server-credentials/save", json={"key": key, "ftp_port": "99999"})).status_code, 422)
        async with self.sessions() as s:
            log = (await s.execute(select(EventLog).where(EventLog.category == "system"))).scalars().all()
            self.assertEqual(len(log), 3)                                        # ogni lettura nel Registro
            self.assertTrue(all("visualizzata" in e.message for e in log))

    async def test_unreadable_after_secret_change(self):
        async with self.client() as c:
            await c.post("/api/server-credentials/save", json={"key": "x", "panel_password": "abc"})
            with patch.object(rcred, "decrypt", lambda t: None):
                r = await c.post("/api/server-credentials/reveal", json={"key": "x", "field": "panel"})
            self.assertEqual(r.status_code, 409)

    async def test_empty_entry_is_removed(self):
        async with self.client() as c:
            await c.post("/api/server-credentials/save", json={"key": "y", "panel_user": "u"})
            await c.post("/api/server-credentials/save", json={"key": "y", "panel_user": ""})
            self.assertEqual((await c.get("/api/server-credentials")).json(), {})


if __name__ == "__main__":
    unittest.main()
