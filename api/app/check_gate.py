"""Shared admission/pacing for connector status GETs across API and worker processes."""
import asyncio
import hashlib
import logging
import math
import secrets
from contextlib import asynccontextmanager
from contextvars import ContextVar

from redis.asyncio import from_url
from redis.exceptions import RedisError
from .config import settings
from .servers import server_of

log = logging.getLogger("sentinel.check_gate")
_redis = None
_loop = None
updating_server = ContextVar("updating_server", default="")
queue_seconds = ContextVar("check_queue_seconds", default=None)
# Already resolved by the existing check; the passive journal performs no DNS lookup.
observed_server = ContextVar("observed_server", default="")

# Redis' clock is shared across processes. Claims and both limits are atomic.
_ACQUIRE = """
local t = redis.call('TIME')
local now = tonumber(t[1])*1000 + math.floor(tonumber(t[2])/1000)
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
local cool = redis.call('PTTL', KEYS[3])
if cool > 0 then return {0, math.min(cool, 500)} end
if redis.call('EXISTS', KEYS[2]) == 1 then return {0, 250} end
for i=4,#KEYS do
 if redis.call('EXISTS', KEYS[i]) == 1 then return {0, 500} end
end
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[2]) then return {0, 250} end
redis.call('SET', KEYS[2], ARGV[1], 'PX', ARGV[3])
redis.call('ZADD', KEYS[1], now+tonumber(ARGV[3]), ARGV[1])
if redis.call('PTTL', KEYS[1]) < tonumber(ARGV[3])+1000 then
 redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[3])+1000)
end
return {1, 0}
"""
_RELEASE = """
redis.call('ZREM', KEYS[1], ARGV[1])
if redis.call('GET', KEYS[2]) == ARGV[1] then
 redis.call('DEL', KEYS[2])
 if tonumber(ARGV[2]) > 0 then redis.call('SET', KEYS[3], '1', 'PX', ARGV[2]) end
 return 1
end
return 0
"""


class CheckDeferred(Exception):
    """No request was sent: admission could not be obtained safely."""


def client():
    global _redis, _loop
    loop = asyncio.get_running_loop()
    if _redis is None or _loop is not loop:
        _redis = from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
        _loop = loop
    return _redis


async def close():
    global _redis, _loop
    r, _redis = _redis, None
    _loop = None
    if r is not None:
        try:
            await r.aclose()
        except RuntimeError:
            pass


@asynccontextmanager
async def status_slot(endpoint, network_budget, *, redis=None, identify=None):
    r = redis if redis is not None else client()
    resolve = identify or server_of
    try:
        server = await resolve(r, endpoint)
    except (RedisError, OSError) as exc:
        raise CheckDeferred("Coordinamento controlli non disponibile; ricontrollo programmato") from exc
    observed_server.set(server)
    digest = hashlib.sha256(server.encode()).hexdigest()[:32]
    keys = ["status:active", f"status:server:{digest}", f"status:cool:{digest}"]
    # Respect the existing update brake: do not start a check on an updating server.
    if updating_server.get() != server:
        keys += [f"srv:slot:{server}:{i}" for i in range(4)] + [f"srv:cool:{server}"]
    owner = secrets.token_hex(16)
    lease = math.ceil((network_budget + 30) * 1000)
    wait = queue_seconds.get()
    deadline = asyncio.get_running_loop().time() + (settings.STATUS_CHECK_QUEUE_SECONDS if wait is None else wait)
    acquired = False
    try:
        while True:
            try:
                result = await r.eval(_ACQUIRE, len(keys), *keys, owner, settings.STATUS_CHECK_CONCURRENCY, lease)
            except (RedisError, OSError) as exc:
                raise CheckDeferred("Coordinamento controlli non disponibile; ricontrollo programmato") from exc
            if int(result[0]) == 1:
                acquired = True
                break
            if asyncio.get_running_loop().time() >= deadline:
                raise CheckDeferred("Server occupato da altri controlli; ricontrollo programmato")
            await asyncio.sleep(min(float(result[1]) / 1000, max(0, deadline - asyncio.get_running_loop().time())))
        yield
    finally:
        if acquired:
            try:
                await r.eval(_RELEASE, 3, *keys[:3], owner, round(settings.STATUS_CHECK_SERVER_PAUSE_SECONDS * 1000))
            except (RedisError, OSError):
                log.warning("Rilascio controllo non riuscito: il posto scadra' automaticamente")
