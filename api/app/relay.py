"""
Modalita' firewall (2.49.0): quando un firewall o un antibot davanti al sito blocca Sentinel
(Cloudflare, SiteGround, Sucuri, un IP bannato...), e' il connettore a chiamare Sentinel.

Il firewall filtra chi ENTRA nel sito, non quello che il sito manda fuori. Cosi':
- fuori dalla modalita', il connettore (WP 2.41+, Joomla 1.43+) manda un ping leggero quando
  Sentinel non lo cerca da mezz'ora; la risposta gli dice in che modalita' stare;
- quando il controllo diretto trova un firewall, il sito passa in modalita' firewall da solo:
  il connettore si fa sentire ogni 5 minuti con lo stato del sito, prende i lavori in coda
  (aggiornamenti, ripristini, installazioni...), li esegue e manda l'esito;
- ogni 3 ore Sentinel riprova la strada diretta: se passa, si torna come prima.

Stato in Redis: l'ultimo stato mandato dal sito, la coda dei lavori e i loro esiti. Nel
database solo relay_mode (il pannello vuole la modalita' firewall) e relay_seen_at (ultimo
contatto del connettore).
"""
import asyncio
import contextvars
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone

from redis.asyncio import from_url

from .config import settings

log = logging.getLogger("sentinel.relay")

# Tempi regolabili da Impostazioni → Modalita' firewall (apply_config). Questi sono i valori di base.
ENABLED = True                # modalita' firewall automatica
REFRESH_SECONDS = 6 * 3600    # ricerca forzata degli aggiornamenti dal backend del sito
TICK_SECONDS = 300            # in modalita' firewall il connettore chiede i lavori ogni 5 minuti
STATUS_SECONDS = 3600         # ...e manda lo stato completo ogni 60 (e sempre dopo un lavoro o una ricerca)
PING_SECONDS = 1800           # fuori modalita': un ping se Sentinel non lo cerca da 30 minuti
SEEN_WINDOW = timedelta(seconds=2 * PING_SECONDS + 600)   # connettore che sa chiamare ed e' vivo
STALE_MINUTES = 60            # stato piu' vecchio: "il sito non si fa sentire" (i siti con poche visite
                              # di notte fanno girare il cron di rado)
PICKUP_SECONDS = 480          # tempo che il sito ha per prendere un lavoro (un giro + margine)
RUN_SECONDS = 600             # tempo per eseguirlo, una volta preso (aggiornamento del core compreso)
# pickup + run restano sotto il job_timeout di arq (1800 s) anche con le foto prima e dopo
DIRECT_RETRY_SECONDS = 3 * 3600   # ogni quanto si riprova la strada diretta
WAIT_SECONDS = 45             # attesa del lavoro successivo, quando il sito ha appena finito il primo
BUSY_SECONDS = 120            # dopo un lavoro: niente controllo normale in parallelo (vedi mark_busy)
CYCLE_SECONDS = 1500          # un ciclo di aggiornamenti in modalita' firewall resta sotto il job_timeout di arq

# scadenza del ciclo degli aggiornamenti in corso (time.monotonic()): i lavori non la superano
_DEADLINE: contextvars.ContextVar[float | None] = contextvars.ContextVar("relay_deadline", default=None)
FP_REPIN_SECONDS = 6 * 3600   # copia del sito diversa da quella nota: accettata solo dopo 6 ore di silenzio
MAX_JOBS = 5                  # lavori consegnati per chiamata
POLL = 2.0                    # ogni quanto il pannello guarda se l'esito e' arrivato
TTL = 3600

# versioni dei connettori che sanno chiamare Sentinel
MIN_CONNECTOR = {"wp": "2.41.0", "joomla": "1.43.0"}
CMS_LABEL = {"wp": "WordPress", "joomla": "Joomla"}

_redis = None
_loop = None


def apply_config(prefs: dict) -> None:
    """Tempi della modalita' firewall dalle Impostazioni (gia' normalizzate da settings_store)."""
    global ENABLED, TICK_SECONDS, STATUS_SECONDS, PING_SECONDS, STALE_MINUTES, PICKUP_SECONDS, SEEN_WINDOW
    global REFRESH_SECONDS
    ENABLED = bool(prefs.get("relay_enabled", True))
    REFRESH_SECONDS = 3600 * int(prefs.get("relay_refresh_hours", 6))
    TICK_SECONDS = 60 * int(prefs.get("relay_poll_minutes", 5))
    STATUS_SECONDS = 60 * int(prefs.get("relay_status_minutes", 60))
    PING_SECONDS = 60 * int(prefs.get("relay_ping_minutes", 30))
    STALE_MINUTES = int(prefs.get("relay_silent_minutes", 60))
    # un giro del connettore piu' margine; mai meno di 8 minuti (i connettori WP 2.41.0-2.41.2 hanno
    # il cron fisso a 5 minuti), mai tanto da superare il job_timeout di arq con l'esecuzione
    PICKUP_SECONDS = max(480, min(1080, TICK_SECONDS + 180))
    SEEN_WINDOW = timedelta(seconds=max(2 * PING_SECONDS, STALE_MINUTES * 60) + 600)


_CFG_AT = 0.0


async def refresh_config() -> None:
    """Rilegge le Impostazioni (al massimo una volta al minuto)."""
    global _CFG_AT
    if _CFG_AT and time.monotonic() - _CFG_AT < 60:
        return
    _CFG_AT = time.monotonic()
    try:
        from .settings_store import get_operational_settings
        apply_config(await get_operational_settings())
    except Exception:  # noqa: BLE001
        pass


def client():
    global _redis, _loop
    loop = asyncio.get_running_loop()
    if _redis is None or _loop is not loop:
        _redis = from_url(settings.REDIS_URL, socket_connect_timeout=3, socket_timeout=5)
        _loop = loop
    return _redis


def set_client(redis) -> None:
    """Per i test: un Redis finto."""
    global _redis, _loop
    _redis = redis
    try:
        _loop = asyncio.get_running_loop()
    except RuntimeError:
        _loop = None


async def close() -> None:
    global _redis, _loop
    r, _redis, _loop = _redis, None, None
    if r is not None:
        try:
            await r.aclose()
        except Exception:  # noqa: BLE001
            pass


def _k(*parts) -> str:
    return "relay:" + ":".join(str(p) for p in parts)


def _vt(v: str) -> tuple:
    import re
    return tuple(int(x) for x in re.findall(r"\d+", v or ""))


def connector_can_relay(site) -> bool:
    need = MIN_CONNECTOR.get(getattr(site, "cms", "") or "", "")
    have = getattr(site, "connector_version", "") or ""
    return bool(need and have and _vt(have) >= _vt(need))


def needed_connector(site) -> str:
    cms = getattr(site, "cms", "") or ""
    return f"{CMS_LABEL.get(cms, cms)} {MIN_CONNECTOR.get(cms, '')}".strip()


def _aware(dt):
    if dt is not None and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def seen_recently(site, now: datetime | None = None) -> bool:
    """Il connettore di questo sito ha chiamato Sentinel di recente: sa farlo ed e' vivo."""
    seen = _aware(getattr(site, "relay_seen_at", None))
    if seen is None:
        return False
    return (now or datetime.now(timezone.utc)) - seen < SEEN_WINDOW


def active(site) -> bool:
    """Modalita' firewall in uso: accesa in Impostazioni, voluta dal pannello e connettore che si fa sentire."""
    return ENABLED and bool(getattr(site, "relay_mode", False)) and seen_recently(site)


def minutes_since_seen(site) -> int:
    seen = _aware(getattr(site, "relay_seen_at", None))
    if seen is None:
        return 0
    return max(0, int((datetime.now(timezone.utc) - seen).total_seconds() // 60))


# --------------------------------------------------------------------------
# Stato mandato dal sito
# --------------------------------------------------------------------------
async def save_status(site_id: int, payload: dict) -> None:
    await client().set(_k("status", site_id), json.dumps({"at": time.time(), "payload": payload}), ex=7 * 86400)


async def load_status(site_id: int) -> tuple[float, dict] | None:
    """(eta' in secondi, stato) dell'ultimo stato mandato dal sito, None se non ce n'e'."""
    raw = await client().get(_k("status", site_id))
    if not raw:
        return None
    try:
        d = json.loads(raw)
        return max(0.0, time.time() - float(d["at"])), dict(d["payload"])
    except Exception:  # noqa: BLE001
        return None


async def note_seen(site_id: int) -> None:
    await client().set(_k("seen", site_id), str(time.time()), ex=7 * 86400)


async def _minutes_since_seen_live(site) -> int:
    """Come minutes_since_seen, ma anche con le chiamate arrivate dopo che il sito e' stato letto
    dal database (un ciclo degli aggiornamenti lungo tiene il sito letto all'inizio)."""
    m = minutes_since_seen(site) if getattr(site, "relay_seen_at", None) is not None else 10**6
    try:
        raw = await client().get(_k("seen", site.id))
        if raw:
            m = min(m, max(0, int((time.time() - float(raw)) // 60)))
    except Exception:  # noqa: BLE001
        pass
    return m


async def need_status(site_id: int) -> bool:
    """Il pannello non ha uno stato abbastanza recente: il connettore lo manda subito."""
    st = await load_status(site_id)
    return st is None or st[0] > STATUS_SECONDS + 600


async def status_for(site) -> dict | None:
    """Stato da usare in modalita' firewall. Il sito e' vivo se chiama (anche la sola domanda
    leggera, ogni pochi minuti): lo stato completo arriva piu' di rado e resta valido finche' il
    sito chiama e non e' piu' vecchio di due giri dello stato completo (piu' un'ora di margine)."""
    st = await load_status(site.id)
    if st is None or await _minutes_since_seen_live(site) >= STALE_MINUTES:
        return None
    if st[0] > max(STALE_MINUTES * 60, 2 * STATUS_SECONDS + 3600):
        return None
    return st[1]


async def fresh_status(site_id: int) -> dict | None:
    st = await load_status(site_id)
    if st and st[0] <= STALE_MINUTES * 60:
        return st[1]
    return None


async def refresh_due(site_id: int) -> bool:
    """E' ora della ricerca forzata periodica degli aggiornamenti (Impostazioni)."""
    r = client()
    return not await r.exists(_k("refreshed", site_id)) and not await r.exists(_k("refresh", site_id))


async def direct_retry_due(site_id: int) -> bool:
    """True una volta ogni DIRECT_RETRY_SECONDS: e' ora di riprovare la strada diretta."""
    return bool(await client().set(_k("direct", site_id), "1", nx=True, ex=DIRECT_RETRY_SECONDS))


async def request_refresh(site_id: int) -> None:
    """Ricalcolo forzato degli aggiornamenti al prossimo contatto (Check, ciclo degli aggiornamenti)."""
    if await jobs_blocked(site_id):
        return                    # il connettore non prende lavori: si accumulerebbero
    # la chiave resta finche' il connettore prende il lavoro (take_jobs la toglie): un sito che tace
    # di notte non accumula una ricerca per ogni controllo
    if await client().set(_k("refresh", site_id), "1", nx=True, ex=6 * 3600):
        await push_job(site_id, "refresh", {})


async def clear(site_id: int) -> None:
    """Fine della modalita' firewall: via lo stato e i lavori rimasti."""
    r = client()
    await r.delete(_k("status", site_id), _k("jobs", site_id), _k("refresh", site_id), _k("since", site_id),
                   _k("direct", site_id), _k("nojobs", site_id))


async def mark_switch(site_id: int) -> None:
    """Inizio della modalita' firewall: il connettore la scopre al suo prossimo ping. La strada
    diretta si riprova fra 3 ore, non al controllo subito dopo (il firewall ha appena risposto)."""
    r = client()
    await r.set(_k("since", site_id), str(time.time()), ex=86400)
    await r.set(_k("direct", site_id), "1", ex=DIRECT_RETRY_SECONDS)


async def forget_fingerprint(site_id: int) -> None:
    """Dopo un trasloco (Modifica → Salva sul sito): la prossima copia che chiama diventa quella nota."""
    await client().delete(_k("fp", site_id), _k("fpseen", site_id))


async def switching(site_id: int) -> bool:
    """Modalita' firewall appena accesa: il connettore ha ancora tempo per farsi sentire (al
    massimo un ping, 30 minuti, piu' un margine)."""
    raw = await client().get(_k("since", site_id))
    try:
        return raw is not None and time.time() - float(raw) < PING_SECONDS + 600
    except ValueError:
        return False


# --------------------------------------------------------------------------
# Lavori
# --------------------------------------------------------------------------
def job_site(job_id: str) -> int | None:
    head = str(job_id or "").split("-", 1)[0]
    return int(head) if head.isdigit() else None


async def push_job(site_id: int, action: str, params: dict, file: bytes | None = None) -> str:
    job_id = f"{site_id}-{uuid.uuid4().hex}"
    job = {"id": job_id, "action": action, **params}
    r = client()
    if file is not None:
        await r.set(_k("file", job_id), file, ex=TTL)
        job["download"] = f"/api/agent/relay/file/{job_id}"
    raw = json.dumps(job)
    await r.set(_k("raw", job_id), raw, ex=TTL)
    await r.rpush(_k("jobs", site_id), raw)
    await r.ltrim(_k("jobs", site_id), -100, -1)     # mai una coda senza fine
    await r.expire(_k("jobs", site_id), 86400)
    return job_id


async def take_jobs(site_id: int, wait: float = 0.0) -> list[dict]:
    """Lavori in coda per il sito (al massimo MAX_JOBS). Con wait aspetta che ne arrivi uno:
    il sito ha appena consegnato un esito e il ciclo degli aggiornamenti accoda il prossimo."""
    r = client()
    deadline = time.monotonic() + max(0.0, wait)
    while True:
        out = []
        for _ in range(MAX_JOBS):
            raw = await r.lpop(_k("jobs", site_id))
            if not raw:
                break
            try:
                job = json.loads(raw)
            except Exception:  # noqa: BLE001
                continue
            await r.set(_k("taken", job["id"]), "1", ex=TTL)
            if job.get("action") == "refresh":
                await r.delete(_k("refresh", site_id))
            out.append(job)
        if out or time.monotonic() >= deadline:
            return out
        await asyncio.sleep(0.5)


async def post_result(site_id: int, job_id: str, result) -> bool:
    if job_site(job_id) != site_id or not isinstance(result, dict):
        return False
    r = client()
    if result.get("_requeue"):
        # il connettore ha finito il suo tempo prima di cominciarlo: torna in testa alla coda
        raw = await r.get(_k("raw", job_id))
        if raw:
            await r.lpush(_k("jobs", site_id), raw)
            await r.delete(_k("taken", job_id))
        return True
    raw = await r.get(_k("raw", job_id))
    try:
        action = json.loads(raw).get("action") if raw else ""
    except Exception:  # noqa: BLE001
        action = ""
    if action == "refresh":
        # ricerca fatta (anche dai connettori 2.41.0-2.41.2, che mandano lo stato a parte): la
        # prossima fra N ore
        await r.set(_k("refreshed", site_id), "1", ex=REFRESH_SECONDS)
    status = result.get("status")
    if isinstance(status, dict) and status.get("extensions") is not None:
        # ricerca degli aggiornamenti fatta dal sito: e' anche lo stato piu' fresco
        await save_status(site_id, status)
        if not await busy(site_id):
            await enqueue_apply(site_id)
    await r.set(_k("result", job_id), json.dumps(result), ex=TTL)
    return True


async def mark_busy(site_id: int, seconds: int = BUSY_SECONDS, redis=None) -> None:
    """Il ciclo degli aggiornamenti sta lavorando sul sito: il controllo normale non deve girare
    in parallelo (riscrive le estensioni del sito mentre il ciclo le sta usando). Allunga, non
    accorcia mai: il ciclo lo tiene per tutta la sua durata."""
    r = redis or client()
    left = await r.ttl(_k("busy", site_id))
    if left is None or left < seconds:
        await r.set(_k("busy", site_id), "1", ex=seconds)


async def clear_busy(site_id: int, redis=None) -> None:
    await (redis or client()).delete(_k("busy", site_id))


async def busy(site_id: int, redis=None) -> bool:
    try:
        return bool(await (redis or client()).exists(_k("busy", site_id)))
    except Exception:  # noqa: BLE001
        return False


async def set_jobs_ok(site_id: int, ok: bool) -> None:
    """Il connettore dice se sa eseguire lavori (Joomla senza fastcgi_finish_request: no)."""
    if ok:
        await client().delete(_k("nojobs", site_id))
    else:
        await client().set(_k("nojobs", site_id), "1", ex=3600)


async def jobs_blocked(site_id: int) -> bool:
    return bool(await client().exists(_k("nojobs", site_id)))


async def fingerprint_ok(site_id: int, fp: str) -> bool:
    """Una copia del sito con lo stesso token e lo stesso indirizzo (vecchio server dopo un
    trasloco, copia locale col file hosts, backup ripristinato altrove) non deve parlare al posto
    del sito vero. Il connettore manda un'impronta della sua installazione (cartella del sito):
    la prima che si fa sentire viene ricordata; un'altra e' accettata solo se quella nota tace da
    6 ore (trasloco vero)."""
    fp = str(fp or "")[:64]
    if not fp:
        return True               # connettore senza impronta: niente da confrontare
    r = client()
    known = await r.get(_k("fp", site_id))
    known = known.decode() if isinstance(known, bytes) else known
    if known and known != fp:
        last = await r.get(_k("fpseen", site_id))
        try:
            if last is not None and time.time() - float(last) < FP_REPIN_SECONDS:
                return False
        except ValueError:
            pass
    await r.set(_k("fp", site_id), fp)
    await r.set(_k("fpseen", site_id), str(time.time()))
    return True


async def load_file(job_id: str) -> bytes | None:
    return await client().get(_k("file", job_id))


async def wait_result(site_id: int, job_id: str, pickup: float | None = None,
                      run: float | None = None) -> dict | None:
    """Esito del lavoro, None se il sito non l'ha preso in tempo (lavoro tolto dalla coda) o
    non ha consegnato l'esito."""
    pickup = PICKUP_SECONDS if pickup is None else pickup
    run = RUN_SECONDS if run is None else run
    r = client()
    start = time.monotonic()
    raw_job = await r.get(_k("raw", job_id))
    missing_since = None
    while True:
        raw = await r.get(_k("result", job_id))
        if raw:
            await r.delete(_k("result", job_id), _k("taken", job_id), _k("raw", job_id), _k("file", job_id))
            try:
                return json.loads(raw)
            except Exception:  # noqa: BLE001
                return {"ok": False, "error": "esito illeggibile dal sito"}
        el = time.monotonic() - start
        taken = await r.exists(_k("taken", job_id))
        if not taken and el >= pickup:
            removed = await r.lrem(_k("jobs", site_id), 0, raw_job) if raw_job else 0
            if removed:
                # tolto dalla coda da qui: il connettore non lo fara' piu'
                await r.delete(_k("raw", job_id), _k("file", job_id))
                return None
            # non c'e' piu' in coda e non risulta preso: o il connettore l'ha appena tolto e sta
            # segnando "preso" (un attimo), o la modalita' e' stata spenta. Si aspetta un poco.
            now = time.monotonic()
            missing_since = missing_since or now
            if now - missing_since > 3 and not await r.exists(_k("taken", job_id)):
                await r.delete(_k("raw", job_id), _k("file", job_id))
                return None
        if el >= pickup + run:
            return None
        await asyncio.sleep(POLL)


async def call(site, action: str, params: dict, file: bytes | None = None,
               pickup: float | None = None) -> dict | None:
    """Lavoro per il sito in modalita' firewall: in coda, poi l'esito che manda il connettore."""
    run = None
    deadline = _DEADLINE.get()
    if deadline is not None:
        # ciclo degli aggiornamenti: il lavoro non supera la scadenza del ciclo
        left = max(5.0, deadline - time.monotonic())
        pickup = min(PICKUP_SECONDS if pickup is None else pickup, left * 0.7)
        run = min(RUN_SECONDS, max(5.0, left - pickup))
    await mark_busy(site.id, int((PICKUP_SECONDS if pickup is None else pickup) + (RUN_SECONDS if run is None else run)) + 60)
    job_id = await push_job(site.id, action, params, file)
    log.info("MODALITA' FIREWALL '%s' (id=%s): %s in coda (%s)", getattr(site, "name", ""), site.id, action, job_id)
    try:
        return await wait_result(site.id, job_id, pickup=pickup, run=run)
    finally:
        await mark_busy(site.id)       # copre anche la pausa prima del lavoro successivo


def set_cycle_deadline(seconds: float = CYCLE_SECONDS):
    """Inizio di un ciclo degli aggiornamenti: torna il token per reset."""
    return _DEADLINE.set(time.monotonic() + seconds)


def reset_cycle_deadline(token) -> None:
    _DEADLINE.reset(token)


def cycle_time_left() -> float | None:
    d = _DEADLINE.get()
    return None if d is None else d - time.monotonic()


def not_taken_message() -> str:
    return "il sito non ha preso il lavoro in tempo (modalità firewall): si riprova al prossimo giro"


def no_jobs_message() -> str:
    return ("in modalità firewall questo sito Joomla non può eseguire aggiornamenti (PHP senza "
            "fastcgi_finish_request): aggiornalo dal backend o fai consentire l'IP del pannello")


# --------------------------------------------------------------------------
# Indirizzo pubblico del pannello: lo impara il connettore (X-Sentinel-Hub)
# --------------------------------------------------------------------------
_HUB = {"url": "", "at": 0.0}


async def hub_url() -> str:
    if time.monotonic() - _HUB["at"] > 300 or not _HUB["at"]:
        try:
            from .routers.connectors import _hub_url
            url = await _hub_url()
            # solo https: il connettore manda il token del sito a questo indirizzo
            _HUB["url"] = url if url.lower().startswith("https://") else ""
        except Exception:  # noqa: BLE001
            pass
        _HUB["at"] = time.monotonic()
    return _HUB["url"]


async def enqueue_apply(site_id: int) -> None:
    """Stato nuovo dal sito: il controllo normale lo applica (al massimo uno ogni 10 minuti)."""
    try:
        now = int(time.time())
        bucket, defer = now // 600, 0
        if not await client().set(_k("applied", site_id), "1", nx=True, ex=600):
            # applicato da poco: si applica alla fine dei 10 minuti, non si perde
            bucket, defer = bucket + 1, 600 - now % 600 + 5
        from arq import create_pool
        from arq.connections import RedisSettings
        pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
        try:
            await pool.enqueue_job("poll_site", site_id, _job_id=f"relay-apply:{site_id}:{bucket}",
                                   _defer_by=defer or None)
        finally:
            await pool.close()
    except Exception as ex:  # noqa: BLE001
        log.warning("Stato del sito non applicato (id=%s): %s", site_id, type(ex).__name__)
