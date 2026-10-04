"""Pacing/retries for public domain sources, isolated from site/server checks."""
import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import socket
import time

import httpx


class SourceError(RuntimeError):
    def __init__(self, message, *, transient=False):
        super().__init__(message)
        self.transient = transient


def page_block_reason(text):
    body = text.lower()
    if any(x in body for x in ("captcha", "verify you are human", "security check", "checking your browser", "access denied")):
        return "Pagina di verifica CAPTCHA/antibot: dati WHOIS non disponibili"
    return ""


def retry_delay(error, attempt):
    """None means a permanent failure. Do not hammer CAPTCHA or bad domains."""
    delay = 15.0 * attempt
    if isinstance(error, SourceError):
        return delay if error.transient else None
    if isinstance(error, httpx.HTTPStatusError):
        if error.response.status_code not in (408, 429, 500, 502, 503, 504):
            return None
        raw = error.response.headers.get("Retry-After", "")
        try:
            delay = max(delay, float(raw))
        except ValueError:
            try:
                stamp = parsedate_to_datetime(raw)
                stamp = stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp
                delay = max(delay, (stamp - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                pass
        # A long service ban is better left to the next scheduled scan than a
        # job waiting indefinitely. Never retry BEFORE the advertised period.
        return delay if delay <= 120 else None
    if isinstance(error, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError, TimeoutError)):
        return delay
    if isinstance(error, socket.gaierror):
        return delay if error.errno == socket.EAI_AGAIN else None
    if isinstance(error, (ConnectionError, OSError)):
        return delay if getattr(error, "errno", None) in (104, 110, 111, 113) else None
    if isinstance(error, RuntimeError) and str(error).startswith("Timeout WHOIS"):
        return delay
    return None


class SourceGate:
    """One request per source in this worker; pause even across concurrent scans.

    Waiting for a slot is outside the network timeout. Other sources and all
    site/server jobs can continue while a source is busy or a retry is waiting.
    """
    def __init__(self, pause_seconds=3.0, clock=time.monotonic, sleep=asyncio.sleep):
        self.pause = pause_seconds
        self.clock = clock
        self.sleep = sleep
        self.locks = {}
        self.next_at = {}

    async def call(self, source, fetch, timeout=45.0):
        async with self.locks.setdefault(source, asyncio.Lock()):
            remaining = self.next_at.get(source, 0) - self.clock()
            if remaining > 0:
                await self.sleep(remaining)
            try:
                return await asyncio.wait_for(fetch(), timeout=timeout)
            finally:
                self.next_at[source] = self.clock() + self.pause
