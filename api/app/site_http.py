"""
Richieste HTTP verso i siti monitorati: firma del pannello, intestazioni extra del sito,
token che non esce dal dominio del sito e riconoscimento dei firewall/antibot.

Perche' esiste (2.48.0). Un sito spostato dietro Cloudflare, l'antibot di SiteGround, Sucuri,
Imunify360 & co. smetteva di rispondere a Sentinel senza che si capisse il perche':
- le chiamate al connettore partivano con la firma di default della libreria
  ("python-httpx/x.y"), la prima che questi sistemi trattano da robot;
- la pagina di verifica (per SiteGround un HTTP 202 con un rimando a /.well-known/sgcaptcha)
  arrivava al posto del JSON del connettore e diventava un generico "risposta senza i dati
  del connettore", o un "sito non raggiungibile";
- non c'era modo di mandare a un sito un'intestazione propria (per una regola di Cloudflare
  che lascia passare Sentinel, o i service token di Cloudflare Access).
"""
import re
from urllib.parse import urlparse

import httpx

from .version import __version__

DEFAULT_USER_AGENT = f"Mozilla/5.0 (compatible; SentinelTD/{__version__}; +https://www.tastieredigitali.it)"

# intestazioni che il pannello gestisce da se': non si possono sovrascrivere dalla scheda del sito
_RESERVED = {
    "authorization", "x-sentinel-token", "host", "content-length", "content-type",
    "transfer-encoding", "connection", "cookie", "accept", "accept-encoding",
    "cache-control", "pragma", "te", "upgrade", "expect", "proxy-authorization",
}
_NAME = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,64}$")
MAX_EXTRA_HEADERS = 10
MAX_HEADER_VALUE = 1024


def user_agent() -> str:
    """Firma delle richieste ai siti. SENTINEL_USER_AGENT nel .env per forzarne un'altra."""
    try:
        from .config import settings
        custom = str(getattr(settings, "SENTINEL_USER_AGENT", "") or "").strip()
    except Exception:  # noqa: BLE001
        custom = ""
    return custom or DEFAULT_USER_AGENT


# --------------------------------------------------------------------------
# Intestazioni extra del sito
# --------------------------------------------------------------------------
def parse_extra_headers(text: str | None) -> list[tuple[str, str]]:
    """Righe "Nome: valore" della scheda del sito -> elenco validato. ValueError se non va bene."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for n, raw in enumerate(str(text or "").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"Intestazioni extra, riga {n}: manca «:» fra nome e valore")
        name, value = line.split(":", 1)
        name, value = name.strip(), value.strip()
        if not _NAME.fullmatch(name):
            raise ValueError(f"Intestazioni extra, riga {n}: nome non valido")
        if name.lower() in _RESERVED:
            raise ValueError(f"Intestazioni extra, riga {n}: {name} la gestisce Sentinel")
        if name.lower() in seen:
            raise ValueError(f"Intestazioni extra, riga {n}: {name} ripetuta")
        if not value or len(value) > MAX_HEADER_VALUE or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError(f"Intestazioni extra, riga {n}: valore vuoto, troppo lungo o con caratteri non ammessi")
        seen.add(name.lower())
        out.append((name, value))
    if len(out) > MAX_EXTRA_HEADERS:
        raise ValueError(f"Intestazioni extra: al massimo {MAX_EXTRA_HEADERS}")
    return out


def normalize_extra_headers(text: str | None) -> str:
    """Forma salvata nel database: una "Nome: valore" per riga, gia' validata."""
    return "\n".join(f"{k}: {v}" for k, v in parse_extra_headers(text))


def extra_headers_of(site) -> dict[str, str]:
    """Intestazioni extra salvate sul sito (valori gia' validati al salvataggio)."""
    try:
        return dict(parse_extra_headers(getattr(site, "extra_headers", "") or ""))
    except ValueError:
        return {}


def site_headers(site, base: dict | None = None) -> dict[str, str]:
    """Intestazioni di una richiesta al sito: firma del pannello, poi quelle extra del sito
    (che possono cambiare anche la firma), poi quelle della chiamata (token, Accept...)."""
    headers = {"User-Agent": user_agent()}
    headers.update(extra_headers_of(site))
    headers.update(base or {})
    return headers


def secret_header_names(site) -> set[str]:
    """Intestazioni che non devono uscire dal dominio del sito: token e quelle extra."""
    names = {"authorization", "x-sentinel-token"}
    names.update(k.lower() for k in extra_headers_of(site) if k.lower() != "user-agent")
    return names


# --------------------------------------------------------------------------
# Token solo sul dominio del sito (www e senza www sono lo stesso sito)
# --------------------------------------------------------------------------
def _bare(host: str | None) -> str:
    h = (host or "").strip().lower().rstrip(".")
    return h[4:] if h.startswith("www.") else h


def same_site_host(a: str | None, b: str | None) -> bool:
    """True se i due host sono lo stesso sito: uguali, oppure uno e' la forma con www dell'altro.
    Prima un redirect da sito.it a www.sito.it (tipico dopo un cambio di hosting) toglieva il
    token e il connettore rispondeva 401, che sembrava un firewall."""
    return bool(_bare(a)) and _bare(a) == _bare(b)


def strip_offsite(request: httpx.Request, site_host: str, names: set[str]) -> None:
    """Su una richiesta verso un host che non e' il sito (redirect altrove) toglie i segreti."""
    if site_host and not same_site_host(request.url.host, site_host):
        for name in names:
            request.headers.pop(name, None)


def site_client(site, **kwargs) -> httpx.AsyncClient:
    """Client per le chiamate al connettore di un sito: segue i redirect come prima, ma token e
    intestazioni extra restano sul dominio del sito."""
    host = (urlparse(getattr(site, "url", "") or "").hostname or "").lower()
    names = secret_header_names(site)

    async def _hook(request: httpx.Request) -> None:
        strip_offsite(request, host, names)

    hooks = dict(kwargs.pop("event_hooks", None) or {})
    hooks["request"] = list(hooks.get("request", [])) + [_hook]
    return httpx.AsyncClient(event_hooks=hooks, **kwargs)


# --------------------------------------------------------------------------
# Firewall e antibot davanti al sito
# --------------------------------------------------------------------------
# solo segni forti di una verifica antibot: "access denied" e simili li scrive anche un hosting
# sospeso, e non devono accendere la modalita' firewall
_GENERIC = ("captcha", "verify you are human", "checking your browser", "bot protection",
            "are you a robot", "security check")
_CF_BODY = ("challenge-platform", "just a moment", "attention required", "cf-error-details",
            "cf-chl", "error code: 1020", "cloudflare ray id", "cf-turnstile")


def firewall_block(r: httpx.Response) -> str:
    """Nome del firewall/antibot che ha risposto al posto del connettore, "" se non e' un blocco.

    Una risposta JSON e' del CMS o del connettore: non e' mai un blocco. Il riconoscimento usa
    le firme di ciascun sistema (intestazioni, rimandi, testi della pagina), non il solo codice:
    un 403 di WordPress resta "accesso rifiutato" con le spiegazioni di sempre."""
    h = r.headers
    ctype = (h.get("content-type") or "").lower()
    if "json" in ctype or "zip" in ctype or "octet-stream" in ctype:
        return ""
    code = r.status_code
    server = (h.get("server") or "").lower()
    location = (h.get("location") or "").lower()
    try:
        body = r.text[:30000].lower() if len(r.content) <= 2_000_000 else ""
    except Exception:  # noqa: BLE001
        body = ""

    if h.get("cf-mitigated") or (("cloudflare" in server or h.get("cf-ray")) and code in (403, 429, 503)
                                 and any(m in body for m in _CF_BODY)):
        return "Cloudflare"
    if "sgcaptcha" in body or "sgcaptcha" in location:
        return "SiteGround"
    if h.get("x-sucuri-block") or "sucuri website firewall" in body or "sucuri.net/privacy" in body:
        return "Sucuri"
    if "imunify360" in body or "imunify360" in server:
        return "Imunify360"
    if code in (403, 503) and "wordfence" in body:
        return "Wordfence"
    if "mod_security" in body or "modsecurity" in body or (code == 406 and "not acceptable" in body):
        return "ModSecurity"
    if h.get("x-amzn-waf-action") or "aws waf" in body:
        return "AWS WAF"
    if code == 403 and "akamaighost" in server:
        return "Akamai"
    if "ddos-guard" in server:
        return "DDoS-Guard"
    if code in (202, 403, 429, 503) and any(m in body for m in _GENERIC):
        return "WAF/antibot"
    return ""


def firewall_reason(name: str, code: int, hint: str = "", needed: str = "") -> str:
    """Testo dell'errore del sito (senza il prefisso "Connettore: ").

    hint (2.49.0, modalita' firewall): "connector" = il connettore del sito non sa ancora chiamare
    Sentinel; "hub" = sa farlo ma non sa dove (indirizzo del pannello non impostato);
    "nocall" = sa farlo, ma non si e' fatto sentire."""
    if hint == "connector" and needed.startswith("Joomla"):
        return (f"bloccato da {name} davanti al sito (HTTP {code}): installa a mano sul sito il connettore "
                f"{needed} o successivo e scrivi l'indirizzo del pannello nelle sue impostazioni: "
                f"Sentinel passerà da solo")
    if hint == "connector":
        return (f"bloccato da {name} davanti al sito (HTTP {code}): installa a mano sul sito il connettore "
                f"{needed} o successivo scaricato da Impostazioni → Connettori e Sentinel passerà da solo")
    if hint == "hub":
        return (f"bloccato da {name} davanti al sito (HTTP {code}): il connettore non sa dove chiamare Sentinel, "
                f"imposta l'indirizzo pubblico del pannello in Impostazioni → Connettori")
    if hint == "nocall":
        return (f"bloccato da {name} davanti al sito (HTTP {code}): il connettore non chiama Sentinel, "
                f"controlla che il sito raggiunga l'indirizzo del pannello e che il suo cron giri")
    if name == "SiteGround":
        return (f"bloccato da SiteGround davanti al sito (HTTP {code}): chiedi al supporto SiteGround "
                f"di escludere l'IP del pannello dall'antibot")
    return (f"bloccato da {name} davanti al sito (HTTP {code}): consenti l'IP del pannello nel firewall "
            f"o aggiungi un'intestazione per Sentinel nella scheda del sito")
