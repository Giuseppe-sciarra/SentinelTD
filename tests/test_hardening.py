"""Irrobustimenti della 2.36.0: limite del login non aggirabile, indirizzi interni rifiutati,
input lunghi troncati, token del sito mai mandato a un altro host, 2FA tolta solo con la password."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import test_screenshot_schedule as fixtures
import httpx
from fastapi import HTTPException

from app import connectors, rate_limit
from app.routers import agent
from app.routers import auth as rauth
from app.models import Site


class RateLimitKeyTests(unittest.TestCase):
    def req(self, xff=None, peer="10.0.0.5"):
        headers = {"x-forwarded-for": xff} if xff else {}
        return SimpleNamespace(headers=headers, client=SimpleNamespace(host=peer))

    def test_client_cannot_pick_its_own_key_through_x_forwarded_for(self):
        # NPMplus accoda il vero indirizzo in fondo: il primo valore lo scrive chi chiama
        self.assertEqual(rate_limit._client_ip(self.req("9.9.9.1, 203.0.113.7")), "203.0.113.7")
        self.assertEqual(rate_limit._client_ip(self.req("9.9.9.2, 203.0.113.7")), "203.0.113.7")

    def test_without_proxy_header_the_peer_counts(self):
        with patch.object(rate_limit, "get_remote_address", lambda r: r.client.host):
            self.assertEqual(rate_limit._client_ip(self.req()), "10.0.0.5")


class SiteUrlTests(unittest.TestCase):
    def test_public_urls_pass_and_trailing_slash_is_removed(self):
        with patch.object(agent.socket, "getaddrinfo", lambda h, p: [(0, 0, 0, "", ("93.184.216.34", 0))]):
            self.assertEqual(agent.safe_site_url("https://esempio.it/"), "https://esempio.it")

    def test_internal_addresses_are_refused(self):
        for bad in ("http://127.0.0.1:8080", "http://localhost", "http://10.1.1.219", "http://192.168.1.1/x",
                    "http://169.254.169.254/latest", "http://[::1]/", "ftp://esempio.it", "https://user:pw@esempio.it",
                    "http://pannello.internal", "http://nas.local"):
            with self.assertRaises(HTTPException, msg=bad):
                agent.safe_site_url(bad)

    def test_host_resolving_to_a_private_address_is_refused(self):
        with patch.object(agent.socket, "getaddrinfo", lambda h, p: [(0, 0, 0, "", ("10.0.0.9", 0))]):
            with self.assertRaises(HTTPException):
                agent.safe_site_url("https://interno.esempio.it")

    def test_unresolvable_host_is_accepted_for_now(self):
        def boom(h, p):
            raise OSError("no dns")
        with patch.object(agent.socket, "getaddrinfo", boom):
            self.assertEqual(agent.safe_site_url("https://nuovo.esempio.it"), "https://nuovo.esempio.it")


class TokenOffsiteTests(unittest.IsolatedAsyncioTestCase):
    async def test_token_is_dropped_on_a_redirect_to_another_host(self):
        seen = []

        async def handler(request):
            seen.append((request.url.host, request.headers.get("X-Sentinel-Token"), request.headers.get("Authorization")))
            if request.url.host == "sito.esempio.it":
                return httpx.Response(302, headers={"location": "https://parcheggio.example/x"})
            return httpx.Response(200, json={"cms": "wp", "core": {}, "extensions": []})
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True,
                                   event_hooks={"request": [connectors._strip_token_offsite]})
        connectors._EXPECTED_HOST.set("sito.esempio.it")
        await client.get("https://sito.esempio.it/wp-json/x", headers={"X-Sentinel-Token": "segreto", "Authorization": "Bearer segreto"})
        await client.aclose()
        self.assertEqual(seen[0], ("sito.esempio.it", "segreto", "Bearer segreto"))
        self.assertEqual(seen[1], ("parcheggio.example", None, None))


class TruncationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.SchedulerTests.asyncSetUp(self)

    async def asyncTearDown(self):
        await fixtures.SchedulerTests.asyncTearDown(self)

    async def test_long_extension_fields_do_not_break_the_check(self):
        sid = await fixtures.SchedulerTests.add_site(self, status="ok")
        payload = {"core": {"current": "6.8.3"}, "extensions": [
            {"type": "plugin", "name": "N" * 400, "slug": "s" * 400, "current": "1." * 60, "new": "", "update": False}]}
        with patch.object(connectors, "fetch_status", AsyncMock(return_value=payload)):
            async with self.sessions() as s:
                site = await s.get(Site, sid)
                await connectors.apply_status(s, site)
                await s.commit()
                self.assertEqual(site.status, "ok")
                from sqlalchemy import select
                from app.models import Extension
                e = (await s.execute(select(Extension))).scalars().one()
                self.assertEqual((len(e.name), len(e.slug), len(e.current_version)), (190, 190, 40))


class SecondFactorRemovalTests(unittest.IsolatedAsyncioTestCase):
    async def test_wrong_password_keeps_totp_and_passkeys(self):
        admin = SimpleNamespace(totp_enabled=True, totp_secret="x", password_hash="h", passkeys_json="[]")
        session = SimpleNamespace(commit=AsyncMock())
        with patch.object(rauth, "get_or_bootstrap_admin", AsyncMock(return_value=admin)), \
             patch.object(rauth, "verify_password", lambda pw, h: pw == "giusta"):
            with self.assertRaises(HTTPException) as cm:
                await rauth.totp_disable(rauth.CurrentPw(password="sbagliata"), True, session)
            self.assertEqual(cm.exception.status_code, 403)
            self.assertTrue(admin.totp_enabled)
            with self.assertRaises(HTTPException):
                await rauth.webauthn_delete(rauth.PasskeyLabel(label="k", password=""), True, session)
            session.commit.assert_not_awaited()
            await rauth.totp_disable(rauth.CurrentPw(password="giusta"), True, session)
            self.assertFalse(admin.totp_enabled)


if __name__ == "__main__":
    unittest.main()
