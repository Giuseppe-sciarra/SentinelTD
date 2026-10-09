"""
Interroga il connettore installato sul sito e normalizza la risposta.

Contratto JSON atteso (uguale per WP e Joomla):
{
  "cms": "wp" | "joomla",
  "core": {"current": "6.7.1", "latest": "6.7.1", "update": false},
  "php": "8.2.10",
  "extensions": [
     {"type": "plugin", "name": "WooCommerce", "slug": "woocommerce",
      "current": "9.4.0", "new": "9.5.1", "update": true},
     ...
  ]
}
Vengono restituite TUTTE le estensioni installate (update true/false); i contatori
per categoria li calcola qui il backend.
"""
import html
import asyncio
import contextvars
import logging
import socket
import ssl
import time
import re
from urllib.parse import quote
import httpx
from datetime import datetime, timedelta, timezone
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from .models import Site, Extension
from .config import settings
from .check_gate import CheckDeferred, status_slot, close as close_check_gate

log = logging.getLogger("sentinel.connectors")


# Package "vendor" che NON pubblicano gli update sul canale del CMS (li rileva vendor_scan
# dal file API del vendore). Vanno preservati attraverso il refresh di apply_status e
# contati nei contatori del sito, altrimenti il pending sparisce a ogni check e la card
# non mostra la barra gialla.
VENDOR_SLUGS = {"pkg_BaForms", "pkg_Gallery"}


# --------------------------------------------------------------------------
# Connettore che non risponde con un sito che invece risponde
# --------------------------------------------------------------------------
# Il sito ha RISPOSTO (HTTP 4xx, oppure 200 senza i dati del connettore): non e' offline,
# ma il connettore e' stato rimosso, disattivato, rifiuta il token o qualcosa risponde al
# posto suo. Su quel sito si fermano aggiornamenti e controlli, quindi va segnalato con
# un avviso suo ("Connettore non risponde"), non come "sito non raggiungibile", e senza
# aprire un episodio offline nel registro della disponibilita'.
CONNECTOR_PREFIX = "Connettore: "


class ConnectorReplyError(ValueError):
    """Il sito ha risposto, ma senza i dati del connettore (com_ajax vuota perche' il plugin
    Joomla e' disattivato o rimosso, errore del plugin, pagina HTML al posto del JSON).
    ambiguous=True: la risposta non prova che il CMS giri (pagina HTML): puo' essere anche un
    hosting sospeso o un dominio parcheggiato, e decide la controprova sulla home.
    Sottoclasse di ValueError: su WordPress fa ancora provare la forma REST alternativa."""

    def __init__(self, message: str, *, ambiguous: bool = False):
        super().__init__(message)
        self.ambiguous = ambiguous


def connector_problem(error: str | None) -> bool:
    """True se l'errore salvato sul sito e' un problema del connettore (non un sito offline)."""
    return str(error or "").startswith(CONNECTOR_PREFIX)


def down_kind(site) -> str:
    """Tipo dell'errore attuale: "connector" (il sito risponde, il connettore no) o "site"."""
    return "connector" if connector_problem(site.error) else "site"


def needs_confirmation(site) -> bool:
    """Errore non ancora confermato per il suo tipo, quindi da far passare per la finestra:
    episodio nuovo, oppure sito che va giu' davvero durante un problema del connettore gia'
    confermato (un blip di un altro tipo non deve far partire un avviso al primo colpo)."""
    if site.status != "error":
        return False
    if not site.offline_notified and not site.offline_kind:
        return True
    return site.offline_kind == "connector" and down_kind(site) == "site"


def _connector_json(r: httpx.Response):
    """r.json(), ma una risposta che non e' JSON diventa un errore che dice cosa e' arrivato."""
    try:
        return r.json()
    except ValueError as exc:
        ctype = (r.headers.get("content-type") or "").split(";")[0].strip() or "?"
        raise ConnectorReplyError(
            f"risposta senza i dati del connettore (HTTP {r.status_code}, {ctype})", ambiguous=True) from exc


def _cms_answered(response: httpx.Response) -> bool:
    """La risposta di errore e' JSON del CMS: errore REST di WordPress ({"code": ...}) o
    involucro com_ajax di Joomla ({"success": false, "message": ...}). Prova che il CMS gira e
    che e' il connettore a mancare o a rifiutare. Una pagina HTML invece non prova niente:
    puo' essere anche un hosting sospeso, un sito cancellato o un firewall."""
    try:
        data = response.json()
    except Exception:  # noqa: BLE001
        return False
    return isinstance(data, dict) and any(k in data for k in ("code", "success", "message"))


def connector_http_reason(response: httpx.Response) -> str | None:
    """Motivo leggibile per un errore HTTP che viene dal sito e non da un sito giu'.
    None per i codici che restano "sito che non risponde" (5xx, 408, 429).
    I pezzi di testo fissi sono voci del catalogo delle lingue: i codici restano fuori."""
    code = response.status_code
    if code < 400 or code >= 500 or code in (408, 429):
        return None
    if code in (401, 403):
        return (f"{CONNECTOR_PREFIX}accesso rifiutato (HTTP {code}): "
                f"token cambiato, intestazione filtrata dall'hosting o firewall che blocca Sentinel")
    if code in (404, 410):
        if _rest_no_route(response):
            return f"{CONNECTOR_PREFIX}assente o disattivato (HTTP {code}): WordPress risponde ma il connettore non c'è"
        return f"{CONNECTOR_PREFIX}indirizzo del connettore non trovato (HTTP {code}): rimosso, disattivato o bloccato"
    return f"{CONNECTOR_PREFIX}risposta inattesa all'indirizzo del connettore (HTTP {code})"


# cartelle in cui sta il connettore WordPress (parco storico / nuove installazioni)
SELF_WP_FOLDERS = ("td-panopticon", "sentinel-td")
_SAFE_FOLDER = re.compile(r"[A-Za-z0-9._-]{1,80}")


def connector_mode_of(data: dict, running: str, exts: list) -> str:
    """Dove gira il connettore WordPress: "mu", "plugin:<cartella>" o "other" ("" = non si sa).

    Il connettore 2.36.0+ lo dichiara. Per quelli prima si ricava dall'elenco dei plugin, che
    NON contiene i mu-plugin: la copia in uso e' il plugin del connettore con la stessa versione
    che il connettore dichiara. Se non c'e', il connettore gira da mu-plugin (o da un posto che
    il giro notturno non deve toccare)."""
    mode = str(data.get("mode") or "").strip()
    folder = str(data.get("folder") or "").strip()
    if mode == "mu":
        return "mu"
    if mode == "plugin" and folder and _SAFE_FOLDER.fullmatch(folder) and folder not in (".", ".."):
        return f"plugin:{folder}"
    if mode in ("plugin", "other"):
        return "other"
    running = (running or "").strip()
    if not running:
        return ""
    found = sorted(
        str(e.get("slug") or "") for e in exts
        if str(e.get("type", "")).lower() == "plugin"
        and (str(e.get("slug") or "") in SELF_WP_FOLDERS or str(e.get("name") or "") == "Sentinel TD Agent")
        and str(e.get("current") or "").strip() == running
    )
    if not found:
        return "mu"
    good = [f for f in found if _SAFE_FOLDER.fullmatch(f) and f not in (".", "..")]
    return f"plugin:{good[0]}" if good else "other"     # "." = file singolo nella radice dei plugin


def connector_crash_reason(response: httpx.Response) -> str:
    """Motivo per un 500 all'indirizzo del connettore, con il messaggio del CMS se c'e'
    (errore critico di WordPress, eccezione del plugin Joomla)."""
    msg = ""
    try:
        data = response.json()
    except Exception:  # noqa: BLE001
        data = None
    if isinstance(data, dict):
        err = (data.get("data") or {}).get("error") if isinstance(data.get("data"), dict) else None
        if isinstance(err, dict) and err.get("message"):
            msg = str(err.get("message"))
        elif data.get("message"):
            msg = str(data.get("message"))
    msg = re.sub(r"<[^>]+>", " ", html.unescape(msg))
    msg = re.sub(r"\s+", " ", msg).strip()[:200]
    return f"{CONNECTOR_PREFIX}errore interno del connettore (HTTP {response.status_code})" + (f": {msg}" if msg else "")


def _vgt(a: str, b: str) -> bool:
    """True se la versione a e' piu' recente di b (confronto numerico per componenti)."""
    pa = [int(x) for x in re.findall(r"\d+", a or "")]
    pb = [int(x) for x in re.findall(r"\d+", b or "")]
    return pa > pb


def _category(ext_type: str) -> str:
    """Mappa il tipo di estensione (WP/Joomla) in: plugin | theme | other."""
    t = (ext_type or "").lower()
    if t == "plugin":
        return "plugin"
    if t in ("theme", "template"):
        return "theme"
    return "other"  # component | module | library | ...


# forma dell'endpoint REST WP per sito: "wpjson" (pretty, /wp-json/...) oppure
# "restroute" (index.php?rest_route=..., unica forma che funziona coi permalink
# "semplici"). Il fetch prova la preferita e, se fallisce, l'altra: quella buona
# viene ricordata qui (in-process; al riavvio si ri-scopre da sola al primo check).
_WP_REST_STYLE: dict[int, str] = {}
_STATUS_CLIENT = None
_STATUS_LOOP = None
_STATUS_LIMIT = None
_STATUS_LIMIT_LOOP = None


_EXPECTED_HOST: contextvars.ContextVar[str] = contextvars.ContextVar("sentinel_expected_host", default="")


async def _strip_token_offsite(request: httpx.Request) -> None:
    """Hook di httpx: su una richiesta verso un host diverso da quello del sito (redirect altrove)
    toglie Authorization e X-Sentinel-Token."""
    expected = _EXPECTED_HOST.get()
    if expected and (request.url.host or "").lower() != expected:
        request.headers.pop("Authorization", None)
        request.headers.pop("X-Sentinel-Token", None)


async def status_client():
    """Riusa connessioni HTTP per evitare un nuovo lookup DNS a ogni campione."""
    global _STATUS_CLIENT, _STATUS_LOOP
    loop = asyncio.get_running_loop()
    if _STATUS_CLIENT is None or _STATUS_CLIENT.is_closed or _STATUS_LOOP is not loop:
        await close_status_client()
        # i redirect si seguono (http->https, www), ma il token del sito resta SOLO sull'host del
        # sito: un dominio dirottato o parcheggiato che rimanda altrove non deve riceverlo
        # (httpx toglie Authorization cambiando host, non le intestazioni proprie)
        _STATUS_CLIENT = httpx.AsyncClient(follow_redirects=True, event_hooks={"request": [_strip_token_offsite]},
            limits=httpx.Limits(max_connections=128, max_keepalive_connections=128, keepalive_expiry=600))
        _STATUS_LOOP = loop
    return _STATUS_CLIENT


async def close_status_client():
    global _STATUS_CLIENT, _STATUS_LOOP, _STATUS_LIMIT, _STATUS_LIMIT_LOOP
    client, _STATUS_CLIENT, _STATUS_LOOP = _STATUS_CLIENT, None, None
    _STATUS_LIMIT = _STATUS_LIMIT_LOOP = None
    await close_check_gate()
    if client is not None and not client.is_closed:
        try:
            await client.aclose()
        except RuntimeError:  # loop precedente gia' terminato (test / reload)
            pass


async def _status_get(client, endpoint, headers, timeout):
    global _STATUS_LIMIT, _STATUS_LIMIT_LOOP
    loop = asyncio.get_running_loop()
    if _STATUS_LIMIT is None or _STATUS_LIMIT_LOOP is not loop:
        _STATUS_LIMIT, _STATUS_LIMIT_LOOP = asyncio.Semaphore(4), loop
    # Il pool conserva connessioni a piu' domini; solo quattro GET contemporanee
    # per processo. Il posto viene liberato prima di aspettare un retry.
    # Admission/queue waits are outside HTTP connect/read deadlines.
    read = timeout.read if isinstance(timeout, httpx.Timeout) else float(timeout)
    connect = timeout.connect if isinstance(timeout, httpx.Timeout) else float(timeout)
    budget = read + connect
    async with status_slot(endpoint, budget):
        async with _STATUS_LIMIT:
            _EXPECTED_HOST.set((httpx.URL(endpoint).host or "").lower())
            try:
                return await asyncio.wait_for(client.get(endpoint, headers=headers, timeout=timeout), timeout=budget)
            except asyncio.TimeoutError as exc:
                raise httpx.ReadTimeout("Tempo totale del controllo superato") from exc


def wp_rest_url(site: Site, path: str, qs: str = "", *, style: str | None = None) -> str:
    """URL dell'endpoint REST del connettore WP nello stile giusto per il sito."""
    base = site.url.rstrip("/")
    style = style or _WP_REST_STYLE.get(site.id, "wpjson")
    if style == "restroute":
        url = f"{base}/index.php?rest_route=/tdpanopticon/v1/{path}"
        return url + (("&" + qs) if qs else "")
    url = f"{base}/wp-json/tdpanopticon/v1/{path}"
    return url + (("?" + qs) if qs else "")


def _causes(exc: BaseException):
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


def temporary_dns_error(exc: BaseException) -> bool:
    return any((isinstance(cause, socket.gaierror) and cause.errno == socket.EAI_AGAIN)
               or "temporary failure in name resolution" in str(cause).lower()
               or f"[Errno {socket.EAI_AGAIN}]" in str(cause)
               for cause in _causes(exc))


def _retry_connection(exc: BaseException) -> bool:
    if temporary_dns_error(exc):
        return True
    if any(isinstance(cause, (socket.gaierror, ssl.SSLError)) for cause in _causes(exc)):
        return False  # nome inesistente o certificato errato: non sono buchi temporanei
    return isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))


async def schedule_dns_recheck(redis, site_id: int):
    try:
        await redis.enqueue_job("poll_site", site_id, _defer_by=60,
                                _job_id=f"dns-recheck:{site_id}:{int(time.time()) // 60}")
    except Exception as exc:
        log.warning("Ricontrollo DNS non accodato (id=%s): %s", site_id, type(exc).__name__)


async def fetch_status(site: Site, timeout: float = 20.0, force: bool = False) -> dict:
    """Tentativi configurabili distanziati solo per errori di connessione, prima di cambiare stato.

    Gli errori DNS non attivano l'URL REST alternativo: il dominio e' lo stesso.
    POST di aggiornamento/installazione non vengono ritentate da questa funzione.
    """
    from .settings_store import get_operational_settings
    prefs = await get_operational_settings()
    attempts = prefs["status_check_attempts"]
    retry_seconds = prefs["status_check_retry_seconds"]
    for attempt in range(1, attempts + 1):
        try:
            result = await _fetch_status_once(site, timeout, force)
            if attempt > 1:
                log.info("CHECK RECUPERATO '%s' (id=%s): tentativo %s/%s", site.name, site.id,
                         attempt, attempts)
            return result
        except Exception as exc:
            if not _retry_connection(exc) or attempt == attempts:
                raise
            log.warning("CHECK RETRY '%s' (id=%s): tentativo %s/%s, riprovo tra %ss: %s: %s",
                        site.name, site.id, attempt, attempts,
                        retry_seconds, type(exc).__name__, str(exc)[:300])
            await asyncio.sleep(retry_seconds)
    raise RuntimeError("Nessun tentativo di controllo configurato")


async def _wp_auto_updates_choice() -> str:
    """"block" = il connettore WordPress spegne gli aggiornamenti automatici di WordPress (di
    base: li fa Sentinel); "allow" = li lascia a WordPress. Impostazioni -> Aggiornamenti."""
    try:
        from .settings_store import get_operational_settings
        return "block" if (await get_operational_settings()).get("wp_block_auto_updates", True) else "allow"
    except Exception:  # noqa: BLE001
        return "block"


async def _fetch_status_once(site: Site, timeout: float = 20.0, force: bool = False) -> dict:
    """GET autenticata verso il connettore. Solleva eccezione su errore.

    force=False (default): CHECK PASSIVO. Il connettore legge i dati di update gia'
        presenti nel CMS (tabella #__updates su Joomla, transient su WP), mantenuti
        freschi dal cron/wp-cron del sito. Impatto minimo, nessuna richiesta in uscita
        dal sito monitorato. E' la modalita' usata dai check automatici schedulati.
    force=True: REFRESH ON-DEMAND. Il connettore contatta i server di update e ripopola
        i dati prima di rispondere (piu' pesante). Usato solo dal pulsante 'Check' manuale
        in dashboard, come l'icona reload di Akeeba Panopticon.
    """
    # cache-buster: timestamp univoco + header no-cache. Evita che reverse proxy
    # (es. NPMplus) servano risposte cachate, senza dover configurare ogni sito.
    cb = int(time.time())
    if site.cms == "wp":
        # Impostazioni -> aggiornamenti automatici di WordPress: il connettore (2.38+) se lo
        # segna e li spegne o li lascia accesi; i connettori piu' vecchi ignorano il parametro
        refresh = ("&refresh=1" if force else "") + "&wp_auto_updates=" + await _wp_auto_updates_choice()
        endpoint = wp_rest_url(site, "status", f"_={cb}" + refresh)
    else:  # joomla
        task = "refresh" if force else "status"
        endpoint = f"{site.url.rstrip('/')}/index.php?option=com_ajax&plugin=tdpanopticon&group=system&format=json&task={task}&_={cb}"
    headers = {
        "Authorization": f"Bearer {site.token}",
        # copia del token in un header che nessun hosting filtra: alcuni Apache in
        # CGI/FastCGI buttano via Authorization prima di PHP (401 rest_forbidden fisso)
        "X-Sentinel-Token": site.token,
        "Accept": "application/json",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    client = await status_client()
    request_timeout = httpx.Timeout(timeout, connect=min(timeout, settings.STATUS_CHECK_CONNECT_SECONDS))
    try:
        r = await _status_get(client, endpoint, headers, request_timeout)
        r.raise_for_status()
        payload = _connector_json(r)
    except (httpx.HTTPStatusError, ValueError) as exc:
        if site.cms != "wp" or (isinstance(exc, httpx.HTTPStatusError)
                                 and exc.response.status_code in (408, 429, 500, 502, 503, 504)):
            raise
        # Solo errori HTTP/formato provano la forma REST alternativa. Un problema
        # di DNS/connessione non dipende dal percorso e non deve raddoppiare le GET.
        cur = _WP_REST_STYLE.get(site.id, "wpjson")
        alternate = "restroute" if cur == "wpjson" else "wpjson"
        alt = wp_rest_url(site, "status", f"_={cb}" + refresh, style=alternate)
        r = await _status_get(client, alt, headers, request_timeout)
        r.raise_for_status()
        payload = _connector_json(r)
        _WP_REST_STYLE[site.id] = alternate

    # WordPress: payload diretto. Joomla com_ajax: {"success":bool,"data":[ {...} ]}
    if site.cms == "joomla":
        # gestisco sia il wrapping com_ajax sia risposte gia' piatte (robustezza).
        # com_ajax con il plugin disattivato o rimosso risponde 200 con "data": [] (nessun
        # plugin ha gestito la richiesta): Joomla e' su, il connettore no.
        if isinstance(payload, dict) and "success" in payload:
            if not payload.get("success"):
                raise ConnectorReplyError("errore del plugin del connettore: " + str(payload.get("message") or "com_ajax")[:300])
            data = payload.get("data") or []
            if not data:
                raise ConnectorReplyError("Joomla risponde ma il plugin del connettore non c'è o è disattivato (com_ajax)")
            return data[0]
        if isinstance(payload, list):
            if not payload:
                raise ConnectorReplyError("Joomla risponde ma il plugin del connettore non c'è o è disattivato (com_ajax)")
            return payload[0]
        if isinstance(payload, dict):
            return payload          # gia' piatta
        raise ConnectorReplyError("risposta senza i dati del connettore (Joomla)")
    if not isinstance(payload, dict):
        raise ConnectorReplyError("risposta senza i dati del connettore (WordPress)")
    return payload


SLOW_PREFIX = "Lento: "


async def probe_home(url: str, timeout: float = 45.0) -> tuple[int, float] | None:
    """Controprova senza connettore: una GET alla home del sito, con tempo largo.
    Torna (codice HTTP, secondi) se il sito risponde, None se non risponde affatto."""
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=15.0), follow_redirects=True,
                                     headers={"User-Agent": "Mozilla/5.0 Sentinel-TD/2.31 (+availability)"}) as c:
            r = await c.get(url)
            return r.status_code, round(time.monotonic() - t0, 1)
    except Exception:  # noqa: BLE001
        return None


async def _pending_failure(site: Site, reason: str, crash: str | None = None):
    """Timeout / errore di rete del connettore. NON e' una prova di sito offline: si apre una
    finestra (Impostazioni → "Avvisa che un sito non risponde dopo") e si ricontrolla ogni
    minuto. Solo quando la finestra e' scaduta si fa la CONTROPROVA sulla home del sito:
    se la home risponde il sito e' su, e' il server che e' lento (o il connettore che non ce
    la fa in tempo): lo stato resta "error" con la spiegazione, ma niente episodio offline e
    niente avviso "non raggiungibile". Offline confermato solo se non risponde nemmeno la home.

    crash: motivo pronto quando l'errore e' un HTTP 500 all'indirizzo del connettore. Un 500
    non e' lentezza: se dura tutta la finestra e la home funziona, e' il connettore che va in
    errore (o un plugin che rompe l'API), e va segnalato come "Connettore non risponde" invece
    di finire in "server lento", che non avvisa mai."""
    from .settings_store import get_operational_settings
    if crash and getattr(site, "offline_kind", "") == "connector":
        # connettore gia' confermato come non funzionante: resta tale, senza finestra ne' controprova
        _mark_connector(site, crash)
        return
    window = (await get_operational_settings())["offline_alert_minutes"]
    now = datetime.now(timezone.utc)
    since = site.offline_since
    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    site.offline_since = since or now
    elapsed = (now - site.offline_since).total_seconds()
    # (prima qui contava anche offline_notified: un flag rimasto acceso da un episodio
    #  precedente confermava "offline" al PRIMO timeout, senza finestra. Ora decide solo il tempo.)
    if window > 0 and elapsed < window * 60:
        site.status = "check_pending"
        site.error = reason
        return
    probe = await probe_home(site.url)
    if crash and probe is not None and probe[0] < 500:
        # il sito risponde, il connettore da' 500 da tutta la finestra: non e' un server lento
        # (un 500 non e' lentezza), e' il connettore che va in errore
        _mark_connector(site, f"{crash} — home: HTTP {probe[0]}")
        return
    if probe is not None and probe[0] < 500:
        code, secs = probe
        site._availability_observed = False      # niente episodio offline: il sito risponde
        site._slow_not_offline = True
        site.status = "slow"                    # stato a parte: nel pannello e' giallo, non rosso
        site.error = (f"{SLOW_PREFIX}il connettore non ha risposto in tempo ({reason[:160]}), "
                      f"ma la home risponde (HTTP {code} in {secs}s). Server lento, non offline.")[:480]
        site.offline_since = None               # la finestra riparte da zero al prossimo timeout
        return
    site.status = "error"
    site.error = reason


def _mark_connector(site: Site, reason: str) -> None:
    """Il sito risponde, il connettore no: errore "Connettore: ...". Per il registro della
    disponibilita' il sito e' su (nessun episodio offline; se ce n'era uno aperto si chiude)."""
    site.status = "error"
    site.error = reason[:480]
    site._site_answers = True


async def _answer_not_from_connector(site: Site, connector_reason: str, plain_reason: str) -> None:
    """Pagina che non e' del connettore (HTML con 4xx, oppure 200 senza JSON). Puo' essere il
    connettore tolto con l'API REST bloccata, ma anche un hosting sospeso, un sito cancellato,
    un dominio parcheggiato: dalla sola risposta non si sa.

    Episodio gia' confermato: resta del suo tipo, senza altre richieste al sito. Episodio
    nuovo: dentro la finestra si aspetta come prima ("HTTP 404"); scaduta la finestra UNA GET
    alla home decide: se la home risponde (< 400) e' il connettore, altrimenti e' il sito."""
    from .settings_store import get_operational_settings
    site.status = "error"
    kind = getattr(site, "offline_kind", "") or ""
    if kind == "connector":
        _mark_connector(site, connector_reason)
        return
    if kind == "site":
        site.error = plain_reason[:480]
        return
    try:
        window = int((await get_operational_settings()).get("offline_alert_minutes", 5))
    except Exception:  # noqa: BLE001
        window = 5
    now = datetime.now(timezone.utc)
    since = site.offline_since
    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    if window > 0 and (since is None or (now - since).total_seconds() < window * 60):
        site.error = plain_reason[:480]
        return
    probe = await probe_home(site.url)
    if probe is not None and probe[0] < 400:
        _mark_connector(site, f"{connector_reason} — home: HTTP {probe[0]}")
    else:
        site.error = (f"{plain_reason} — home: " + (f"HTTP {probe[0]}" if probe else "nessuna risposta"))[:480]


async def schedule_pending_recheck(redis, site_id: int):
    try:
        await redis.enqueue_job("poll_site", site_id, _defer_by=60,
                                _job_id=f"pending-recheck:{site_id}:{int(time.time()) // 60}")
    except Exception as exc:
        log.warning("Ricontrollo non accodato (id=%s): %s", site_id, type(exc).__name__)


def confirmation_running(site: Site, window_min: int, now: datetime | None = None) -> bool:
    """Una conferma (ricontrollo al minuto) e' gia' in corso: l'episodio e' iniziato da meno
    della finestra impostata, piu' qualche minuto di margine."""
    since = site.offline_since
    if since is None:
        return False
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return now - since < timedelta(minutes=max(0, int(window_min or 0)) + 3)


async def start_confirmation(session: AsyncSession, site: Site, redis) -> bool:
    """Un errore visto FUORI dal controllo normale (ciclo degli aggiornamenti, Check ora,
    ricontrollo dopo gli aggiornamenti) entra nella stessa conferma del controllo normale:
    ricontrollo fra un minuto e avviso quando la finestra e' passata.

    Prima l'errore restava scritto e basta. Il controllo normale, l'unico che conferma e
    avvisa, non partiva mai: il ciclo orario degli aggiornamenti aggiorna last_checked, e il
    sito non risultava mai "da controllare". Caso reale: connettore tolto da un sito, sito
    rosso nel pannello per due ore e nessun avviso.

    Torna True se ha programmato il ricontrollo, False se una conferma e' gia' in corso."""
    from .settings_store import get_operational_settings
    try:
        window = int((await get_operational_settings()).get("offline_alert_minutes", 5))
    except Exception:  # noqa: BLE001
        window = 5
    now = datetime.now(timezone.utc)
    if confirmation_running(site, window, now):
        return False
    if site.offline_since is None:
        site.offline_since = now        # la finestra parte da qui
        await session.commit()
    await schedule_pending_recheck(redis, site.id)
    return True


async def apply_status(session: AsyncSession, site: Site, force: bool = False) -> None:
    """Polla il sito e scrive lo stato a DB. Non solleva: registra l'errore sul Site.
    force=True forza il refresh lato connettore (vedi fetch_status)."""
    from .check_gate import observed_server
    observed_server.set("")
    site._availability_observed = True
    site._site_answers = False
    try:
        # timeout proporzionato: il check passivo deve essere reattivo (20s), ma con
        # force=True il connettore esegue sul sito il refresh COMPLETO (wp_version_check +
        # wp_update_plugins + wp_update_themes in sincrono verso wordpress.org; su Joomla il
        # rebuild dei canali): su un sito carico (es. WooCommerce con molti plugin) supera
        # i 20s. Col timeout corto il ciclo di auto-update andava in timeout -> status err
        # -> return silenzioso PRIMA di tentare gli update: pending eternamente acceso e
        # mai processato (caso reale: core WP di shop mai aggiornato, senza errori nei log).
        try:
            data = await fetch_status(site, timeout=(120.0 if force else 20.0), force=force)
        except httpx.TimeoutException:
            if not force:
                raise
            # Il ricalcolo FORZATO (wp_update_plugins verso wordpress.org, rebuild canali Joomla)
            # su un server lento puo' superare anche i 120s: non e' il sito che e' giu', e' il
            # ricalcolo che e' pesante. Si ritenta in passivo, con tempo largo: se risponde, si
            # va avanti con i dati che il connettore ha (il connettore dice se la cache c'era).
            log.warning("CHECK FORZATO LENTO '%s' (id=%s): ricalcolo oltre il tempo, ritento in passivo", site.name, site.id)
            data = await fetch_status(site, timeout=40.0, force=False)
        core = data.get("core", {})
        exts = data.get("extensions", []) or []

        # Il connettore WP (dalla 2.19.2) dice se la cache degli aggiornamenti c'era davvero.
        # Se mancava (ricalcolo fallito: wordpress.org non raggiunto, timeout, object cache),
        # i suoi "update: false" sono zeri NON verificati: si tengono i dati dell'ultimo
        # controllo riuscito, pause dei fallimenti comprese, invece di azzerare tutto e far
        # risultare "ok" un sito con aggiornamenti in sospeso. I connettori vecchi non mandano
        # il campo e vengono trattati come prima (tutto verificato).
        known = data.get("updates_known")
        if isinstance(known, dict):
            known_cat = {"plugin": bool(known.get("plugin", True)), "theme": bool(known.get("theme", True))}
            known_cat["other"] = known_cat["plugin"] and known_cat["theme"]
            core_known = bool(core.get("known", True))
        else:
            known_cat = {"plugin": True, "theme": True, "other": True}
            core_known = True
        unverified = not (core_known and all(known_cat.values()))

        # nome della macchina (WP 2.34+/Joomla 1.41+): una stringa, serve a Gestione server per dividere
        # due macchine dietro lo stesso IP
        hn = str(data.get("hostname") or "").strip()[:80]
        if hn and hn != (site.server_hostname or ""):
            site.server_hostname = hn

        # versione del connettore sul sito: dichiarata (WP 2.23+/Joomla 1.32+) o ricavata dal plugin
        declared = str(data.get("connector") or "").strip()
        if declared:
            site.connector_version = declared
        else:
            for e in data.get("extensions") or []:
                if str(e.get("slug", "")).lower() in ("td-panopticon", "tdpanopticon"):
                    site.connector_version = str(e.get("current") or "").strip()
                    break
        if site.cms == "wp":
            mode = connector_mode_of(data, site.connector_version or "", exts)
            if mode and mode != (site.connector_mode or ""):
                site.connector_mode = mode
        site.core_current = str(core.get("current", ""))
        if core_known:
            site.core_latest = str(core.get("latest", core.get("current", "")))
            site.core_update = bool(core.get("update", False))
        elif site.core_update and site.core_latest and not _vgt(site.core_latest, site.core_current or "0"):
            site.core_update = False   # nel frattempo e' stato aggiornato a mano
        site.php_version = str(data.get("php", ""))

        # snapshot completo estensioni: cancella e reinserisci TUTTE.
        # PRIMA salva lo stato di cooldown (fallimenti update) per non perderlo nel refresh.
        prev = (await session.execute(
            select(Extension).where(Extension.site_id == site.id)
        )).scalars().all()
        cooldown_map = {
            (p.type, p.slug): (p.update_failed_at, p.update_failed_version)
            for p in prev
            if p.update_failed_at is not None
        }
        locked = site.locked_set   # bloccati alla versione installata: non contano come "da aggiornare"
        # stato "da aggiornare" dell'ultimo controllo riuscito, per le categorie non verificate
        last_known = {(p.type, p.slug): (bool(p.update_available), p.new_version or "") for p in prev}
        # preserva lo stato "vendor" (Balbooa) rilevato da vendor_scan: il connettore non lo
        # riporta (non e' sul canale del CMS), ma va tenuto attraverso il refresh finche' e'
        # piu' recente dell'installato -> cosi' resta pending e la card mostra il giallo.
        vendor_map = {
            p.slug: (p.new_version or "")
            for p in prev
            if p.slug in VENDOR_SLUGS and p.update_available and p.new_version
        }
        # versione del COMPONENT dei prodotti vendor, dal payload appena letto dal sito:
        # e' la versione "vera" del prodotto quando il manifest del package e' incoerente.
        _VENDOR_COMP = {"pkg_BaForms": "com_baforms", "pkg_Gallery": "com_gallery"}
        vendor_comp_ver: dict[str, str] = {}
        for pkg_slug, comp_slug in _VENDOR_COMP.items():
            for e in exts:
                if str(e.get("slug", "")) == comp_slug:
                    vendor_comp_ver[pkg_slug] = str(e.get("current", ""))
                    break

        await session.execute(delete(Extension).where(Extension.site_id == site.id))
        tot = {"plugin": 0, "theme": 0, "other": 0}
        upd = {"plugin": 0, "theme": 0, "other": 0}
        for e in exts:
            cat = _category(e.get("type", ""))
            etype = str(e.get("type", ""))
            eslug = str(e.get("slug", ""))
            enew = str(e.get("new", ""))
            ecur = str(e.get("current", ""))
            is_upd = bool(e.get("update"))
            # preserva l'update vendor (Balbooa) rilevato da vendor_scan: non arriva dal
            # connettore, ma se e' piu' recente dell'installato va tenuto e CONTATO, cosi'
            # finisce in upd_other e la card in lista diventa gialla come per ogni altro update.
            if not is_upd and eslug in VENDOR_SLUGS:
                vnew = vendor_map.get(eslug, "")
                # confronto con la versione VERA del prodotto: la piu' alta tra il package e
                # il suo component (Balbooa rilascia manifest incoerenti: pkg 2.4.3 con dentro
                # com 2.4.3.2). Senza, un prodotto gia' aggiornato resterebbe pending.
                vcur = ecur
                comp_v = vendor_comp_ver.get(eslug, "")
                if comp_v and _vgt(comp_v, vcur or "0"):
                    vcur = comp_v
                if vnew and _vgt(vnew, vcur):
                    is_upd = True
                    enew = vnew

            if not known_cat.get(cat, True) and not is_upd and (etype, eslug) in last_known:
                # categoria non verificata: vale l'ultimo controllo riuscito, a meno che nel
                # frattempo la versione installata abbia raggiunto quella attesa
                prev_upd, prev_new = last_known[(etype, eslug)]
                if prev_upd and prev_new and _vgt(prev_new, ecur or "0"):
                    is_upd, enew = True, prev_new

            tot[cat] += 1
            if is_upd and f"{etype}:{eslug}" not in locked:
                upd[cat] += 1

            # riapplica il cooldown SOLO se la versione target e' ancora la stessa che aveva fallito;
            # se e' uscita una versione nuova, il cooldown decade (riprovera').
            failed_at, failed_ver = cooldown_map.get((etype, eslug), (None, ""))
            if failed_at is not None and failed_ver != enew:
                failed_at, failed_ver = None, ""

            session.add(Extension(
                site_id=site.id,
                type=etype[:30],
                name=html.unescape(str(e.get("name", "")))[:190],   # "&#8211;" -> "–"; limiti delle colonne
                slug=eslug[:190],
                current_version=ecur[:40],
                new_version=enew[:40],
                update_available=is_upd,
                dlkey_missing=bool(e.get("dlkey_missing", False)),
                update_failed_at=failed_at,
                update_failed_version=failed_ver,
            ))

        site.tot_plugins, site.upd_plugins = tot["plugin"], upd["plugin"]
        site.tot_themes, site.upd_themes = tot["theme"], upd["theme"]
        site.tot_other, site.upd_other = tot["other"], upd["other"]
        site.updates_count = upd["plugin"] + upd["theme"] + upd["other"] + (1 if site.core_update else 0)
        site.updates_unverified_at = datetime.now(timezone.utc) if unverified else None
        if unverified:
            log.warning("Site '%s' (id=%s): aggiornamenti non verificabili (cache WP mancante%s): tenuti i dati dell'ultimo controllo riuscito",
                        site.name, site.id, ", dopo un ricalcolo forzato" if force else "")
        site.status = "ok"
        site.error = ""
        site.offline_since = None
        site.last_checked = datetime.now(timezone.utc)
    except httpx.HTTPStatusError as ex:
        site.status = "error"
        site.error = f"HTTP {ex.response.status_code}"
        if ex.response.status_code in (408, 429, 500, 502, 503, 504):
            crash = connector_crash_reason(ex.response) if ex.response.status_code == 500 else None
            await _pending_failure(site, site.error, crash=crash)
        else:
            reason = connector_http_reason(ex.response)
            if reason and _cms_answered(ex.response):
                # il CMS ha risposto (JSON di WordPress o di Joomla): non e' offline, e' il
                # connettore che manca o rifiuta. L'avviso lo da' il worker dopo la stessa
                # finestra di conferma, con l'evento "Connettore non risponde".
                _mark_connector(site, reason)
            elif reason:
                # pagina HTML: connettore o hosting sospeso/sito cancellato? decide la home
                await _answer_not_from_connector(site, reason, site.error)
        site.last_checked = datetime.now(timezone.utc)
        log.warning("CHECK FALLITO '%s' (id=%s): %s", site.name, site.id, site.error)
    except Exception as ex:  # noqa: BLE001
        if temporary_dns_error(ex):
            site._availability_observed = False
            site.status = "dns_error"
            site.error = ("DNS temporaneo: verifica non riuscita da Sentinel. " + str(ex))[:480]
            site.offline_since = None
        elif isinstance(ex, CheckDeferred):
            site._availability_observed = False
            if site.status != "error":
                site.status = "check_pending"
                site.error = str(ex)[:480]
            # Waiting in Sentinel is not evidence of a failed connection.
        elif isinstance(ex, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)):
            await _pending_failure(site, str(ex)[:480] or type(ex).__name__)
        elif isinstance(ex, ConnectorReplyError):
            # il sito risponde, ma senza i dati del connettore (vedi connector_http_reason)
            if ex.ambiguous:
                await _answer_not_from_connector(site, CONNECTOR_PREFIX + str(ex), str(ex))
            else:
                _mark_connector(site, CONNECTOR_PREFIX + str(ex))
        else:
            site.status = "error"
            site.error = str(ex)[:480] or type(ex).__name__
        site.last_checked = datetime.now(timezone.utc)
        log.warning("CHECK FALLITO '%s' (id=%s): %s: %s", site.name, site.id,
                    type(ex).__name__, site.error)

    site._status_server = observed_server.get()
    from .availability import record_availability
    await record_availability(session, site)

# --------------------------------------------------------------------------
# Diagnostica e pacchetti (connettore WordPress 2.19.0 / Joomla 1.30.0)
# --------------------------------------------------------------------------
class ConnectorTooOld(RuntimeError):
    """Il connettore del sito non ha ancora la funzione richiesta."""


def _auth_headers(site: Site, accept: str = "application/json") -> dict:
    return {
        "Authorization": f"Bearer {site.token}",
        "X-Sentinel-Token": site.token,
        "Accept": accept,
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }


def _rest_no_route(r: httpx.Response) -> bool:
    try:
        return r.status_code == 404 and (r.json() or {}).get("code") == "rest_no_route"
    except Exception:  # noqa: BLE001
        return False


async def _wp_get(client: httpx.AsyncClient, site: Site, path: str, qs: str, headers: dict) -> httpx.Response:
    """GET verso il connettore WP provando entrambe le forme dell'indirizzo REST."""
    r = await client.get(wp_rest_url(site, path, qs), headers=headers)
    if r.status_code < 400 or _rest_no_route(r):
        return r
    cur = _WP_REST_STYLE.get(site.id, "wpjson")
    _WP_REST_STYLE[site.id] = "restroute" if cur == "wpjson" else "wpjson"
    r2 = await client.get(wp_rest_url(site, path, qs), headers=headers)
    if r2.status_code < 400 or _rest_no_route(r2):
        return r2
    _WP_REST_STYLE[site.id] = cur
    return r




async def fetch_package(site: Site, kind: str, slug: str, max_size: int, timeout: float = 300.0) -> tuple[bytes, str]:
    """Zip di un plugin o tema installato sul sito (solo WordPress, connettore 2.19.0)."""
    if site.cms != "wp":
        raise RuntimeError("il recupero dei pacchetti funziona solo con i siti WordPress")
    qs = f"type={quote(kind, safe='')}&slug={quote(slug, safe='')}&_={int(time.time())}"
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        r = await _wp_get(client, site, "package", qs, _auth_headers(site, "application/zip, application/json"))
        if _rest_no_route(r):
            raise ConnectorTooOld("il connettore di questo sito non sa ancora preparare i pacchetti: aggiornalo alla 2.19.0")
        r.raise_for_status()
        ctype = (r.headers.get("content-type") or "").lower()
        if "zip" not in ctype:
            try:
                err = (r.json() or {}).get("error") or "risposta inattesa"
            except Exception:  # noqa: BLE001
                err = f"risposta inattesa ({ctype or 'senza tipo'})"
            raise RuntimeError(err)
        data = r.content
    if len(data) > max_size:
        raise RuntimeError("pacchetto troppo grande")
    return data, (r.headers.get("x-sentinel-version") or "")
