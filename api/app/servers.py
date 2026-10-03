"""Server dei siti: freno per i server deboli, scelti dall'utente.

Sui server deboli (hosting condivisi) piu' siti aggiornati insieme sullo stesso server lo
mettono in ginocchio: download, estrazione e istantanee della home tutti in contemporanea.
Qui ogni sito prende un "posto" sul suo server (riconosciuto dall'IP del dominio) prima
di lavorare; se il server e' occupato il lavoro si rimette in coda dopo qualche secondo,
senza tenere bloccato il worker. Server diversi lavorano in parallelo come prima.

Dopo un sito dove si e' installato o aggiornato qualcosa, il server resta a riposo per la
pausa impostata. I controlli a vuoto (niente da aggiornare) non la fanno scattare.

Il freno vale SOLO per i server scelti in Impostazioni (elenco "server_limited"): di base
nessun server lo ha e si lavora come sempre.
"""
import asyncio
import random
import socket
from urllib.parse import urlparse

from .settings_store import get_operational_settings

SLOT_TTL = 1800          # come il limite di un job: se il worker muore, il posto si libera da solo
IP_CACHE_SECONDS = 6 * 3600


def _s(v) -> str:
    return v.decode() if isinstance(v, (bytes, bytearray)) else str(v)


async def server_of(redis, url: str) -> str:
    """Server di un sito: l'IP del dominio (IPv4 se c'e'). Senza risoluzione, il nome host."""
    host = (urlparse(url or "").hostname or "").lower()
    if not host:
        return ""
    key = f"srv:ip:{host}"
    try:
        cached = await redis.get(key)
        if cached:
            return _s(cached)
    except Exception:  # noqa: BLE001
        pass
    ip = ""
    try:
        infos = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM), timeout=10)
        ips = sorted({i[4][0] for i in infos})
        v4 = [x for x in ips if ":" not in x]
        ip = (v4 or ips or [""])[0]
    except Exception:  # noqa: BLE001
        ip = ""
    value = ip or f"host:{host}"
    try:
        await redis.set(key, value, ex=IP_CACHE_SECONDS)
    except Exception:  # noqa: BLE001
        pass
    return value


async def policy() -> tuple[int, int, set]:
    """(siti in contemporanea per server, pausa in secondi, server con il freno)"""
    st = await get_operational_settings()
    return (int(st.get("server_parallel", 1) or 1), int(st.get("server_pause_seconds", 30) or 0),
            set(st.get("server_limited") or []))


async def acquire(redis, server: str) -> tuple[str | None, int]:
    """Prova a prendere un posto sul server. Ritorna (posto, attesa consigliata in secondi):
    posto None = server occupato o a riposo, riprovare dopo l'attesa indicata."""
    parallel, _pause, limited = await policy()
    if not server or server not in limited:
        return "free", 0          # server senza freno: si lavora come sempre
    try:
        rest = await redis.ttl(f"srv:cool:{server}")
        if rest and rest > 0:
            return None, int(rest) + random.randint(1, 5)
        for i in range(max(1, parallel)):
            slot = f"srv:slot:{server}:{i}"
            if await redis.set(slot, "1", ex=SLOT_TTL, nx=True):
                return slot, 0
    except Exception:  # noqa: BLE001
        return "free", 0          # Redis in difficolta': meglio lavorare che fermarsi
    return None, random.randint(15, 30)


async def release(redis, server: str, slot: str | None, worked: bool) -> None:
    """Libera il posto; se sul sito si e' lavorato davvero, il server riposa per la pausa."""
    if not slot or slot == "free":
        return
    try:
        await redis.delete(slot)
        if worked:
            _parallel, pause, _unl = await policy()
            if pause > 0:
                await redis.set(f"srv:cool:{server}", "1", ex=pause)
    except Exception:  # noqa: BLE001
        pass


# ---- macchine dietro lo stesso IP -----------------------------------------------------------
# Due container (es. uno WordPress e uno Joomla) dietro lo stesso IP pubblico sono per il pannello
# un server solo: carico, RAM e core si mescolano. Il connettore comunica il nome della macchina
# (gethostname); se l'IP e' nell'elenco "diviso" (Impostazioni -> Server dei siti) ogni macchina
# diventa un server a parte, con chiave "IP|nome macchina".

def site_hostname(site) -> str:
    """Nome della macchina su cui gira il sito, dalla diagnostica (vuoto se non ancora noto)."""
    try:
        return str((((site.diag or {}).get("server") or {}).get("hostname")) or "").strip()[:80]
    except Exception:  # noqa: BLE001
        return ""


def machine_key(ip: str, hostname: str, split: set) -> str:
    """"IP|nome" se l'IP e' diviso e il nome si conosce, altrimenti l'IP."""
    return f"{ip}|{hostname}" if ip in split and hostname else ip


def key_ip(key: str) -> str:
    return key.partition("|")[0]


def key_host(key: str) -> str:
    return key.partition("|")[2]
