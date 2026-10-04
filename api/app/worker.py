"""
Worker arq. Due responsabilità:
 - poll_site(site_id): interroga il connettore e salva stato
 - tick(): ogni SCHEDULER_TICK_MINUTES guarda quali siti vanno pollati e accoda i job
 - screenshot_tick(): ogni minuto accoda le anteprime scadute, indipendentemente dai poll
 - shoot_site(site_id): chiede al shooter uno screenshot e salva path/timestamp

Niente loop busy: i cron fanno da dispatcher.
"""
from datetime import datetime, timezone, timedelta
import asyncio
import os
import json
import logging
import re
import time
from urllib.parse import urlparse
import httpx
from arq import cron
from arq.worker import func as arq_func
from arq.connections import RedisSettings
from sqlalchemy import select, delete, func

from .config import settings
from .check_gate import updating_server
from .db import SessionLocal, engine, run_migrations
from .models import UpdateHistory, UpdateMonthly, Site, Extension, SiteExpiry, Package
from .connectors import apply_status, fetch_status, _category, wp_rest_url, ConnectorTooOld, schedule_dns_recheck, schedule_pending_recheck
from .errtext import clean_error
from .servers import server_of, acquire as srv_acquire, release as srv_release
from .notify import dispatch as notify_dispatch
from .settings_store import get_operational_settings
from .screenshot_schedule import screenshot_due, enqueue_screenshot
from .telegram import send_telegram
from .i18n import DEFAULT_LANGUAGE, t
from . import security
from .domain_requests import SourceGate, SourceError, page_block_reason, retry_delay
from .domain_sources import (parse_date, domain_key, validate_whois_domain,
    whois_record_info, WhoisHTML, parse_who_is, reconcile, renewal_pending)

log = logging.getLogger("panopticon.worker")
# Il logger non aveva ne' livello ne' handler: ereditava il default di Python, che mostra
# solo WARNING e superiori. Tutte le righe informative del ciclo (UPDATE OK, update non
# necessario, report non inviato…) erano quindi invisibili in `docker compose logs worker`.
if not log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
    log.addHandler(_h)
log.setLevel(logging.INFO)
log.propagate = False

_WHOIS_SERVERS: dict[str, str] = {"it": "whois.nic.it"}
_RDAP_UNSUPPORTED_TLDS: set[str] = set()
_MULTI_LEVEL_SUFFIXES = {
    "co.uk", "org.uk", "me.uk", "ac.uk", "gov.uk",
    "com.au", "net.au", "org.au", "edu.au", "gov.au",
    "co.nz", "org.nz", "net.nz", "ac.nz", "govt.nz",
    "co.jp", "ne.jp", "or.jp", "ac.jp", "go.jp",
    "com.br", "net.br", "org.br", "com.mx", "com.ar",
    "com.tr", "com.cn", "net.cn", "org.cn", "com.sg",
    "com.hk", "com.tw", "co.za", "com.pl", "edu.it", "gov.it",
}

def _site_host(url: str) -> str:
    """Hostname pulito del sito, senza schema/porta/path."""
    raw = (url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    return (urlparse(raw).hostname or "").strip(".").lower()


def _parse_rdap_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        v = str(value).strip()
        if v.endswith("Z"):
            v = v[:-1] + "+00:00"
        dt = datetime.fromisoformat(v)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def _registrable_domain(host: str) -> str:
    """Riduce un hostname al dominio registrabile senza interrogazioni esterne.

    Copre i TLD semplici (.it, .com, .org, ...) e i suffissi multilivello piu'
    comuni. Esempio: franchising.5sapori.it -> 5sapori.it.
    """
    host = (host or "").strip(".").lower()
    labels = [x for x in host.split(".") if x]
    if len(labels) < 2:
        return host
    suffix2 = ".".join(labels[-2:])
    if suffix2 in _MULTI_LEVEL_SUFFIXES and len(labels) >= 3:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


# Registrar e nameserver rilevati durante l'ultimo lookup, per dominio: servono a
# mostrare "dove e' registrato" senza una seconda interrogazione.
_LAST_INFO: dict[str, dict] = {}


def _rdap_info(payload: dict) -> dict:
    """Registrar e nameserver da una risposta RDAP."""
    registrar = ""
    for ent in payload.get("entities") or []:
        roles = [str(r).lower() for r in (ent.get("roles") or [])]
        if "registrar" not in roles:
            continue
        # il nome sta nel vCard: ["vcard", [["fn", {}, "text", "Nome Registrar"], ...]]
        for item in (ent.get("vcardArray") or [None, []])[1] or []:
            if isinstance(item, list) and len(item) >= 4 and item[0] == "fn":
                registrar = str(item[3]).strip()
                break
        if not registrar:
            registrar = str(ent.get("handle") or "").strip()
        if registrar:
            break
    ns = []
    for n in payload.get("nameservers") or []:
        name = str(n.get("ldhName") or "").strip().lower().rstrip(".")
        if name:
            ns.append(name)
    return {"registrar": registrar[:200], "nameservers": ", ".join(sorted(set(ns))[:8])[:500]}


def _whois_info(text_body: str) -> dict:
    """Registrar e nameserver da un testo WHOIS."""
    import re as _re
    reg = ""
    m = _re.search(r"(?im)^(?:registrar|registrar name|sponsoring registrar|registrar organization)\s*:\s*(.+)$", text_body)
    if m:
        reg = m.group(1).strip()
    if not reg:
        m = _re.search(r"(?is)registrar\s*\n\s*organization:\s*(.+?)\n", text_body)
        if m:
            reg = m.group(1).strip()
    # Formato gTLD: "Name Server: ns1.example.com" (i due punti sono obbligatori, altrimenti
    # l'intestazione "Nameservers" dei .it veniva letta come server "s").
    ns = [x for x in _re.findall(r"(?im)^\s*(?:name server|nserver|nameserver)s?\s*:\s*(\S+)", text_body)]
    # Formato .it e simili: intestazione "Nameservers" e poi un server per riga, indentato.
    blk = _re.search(r"(?ims)^nameservers\s*\n((?:[ \t]+\S+[ \t]*\n?)+)", text_body)
    if blk:
        ns += blk.group(1).split()
    ns = [x.strip().lower().rstrip(".") for x in ns]
    ns = [x for x in ns if "." in x]          # solo nomi host veri
    return {"registrar": reg[:200], "nameservers": ", ".join(sorted(set(ns))[:8])[:500]}


async def _rdap_expiry(client: httpx.AsyncClient, host: str) -> tuple[str, datetime]:
    """Trova la scadenza del dominio registrabile via RDAP.

    Il sottodominio non viene mai interrogato: prima lo normalizziamo al
    dominio registrabile (es. franchising.5sapori.it -> 5sapori.it).
    """
    domain = _registrable_domain(host)
    if not domain or "." not in domain:
        raise RuntimeError("Hostname non registrabile")

    bases = ["https://rdap.org"]

    errors: list[str] = []
    for base in bases:
        try:
            r = await client.get(
                f"{base}/domain/{domain}",
                headers={"User-Agent": "Sentinel-TD/1.1 (+domain-expiry-monitor)"},
            )
            if r.status_code in (400, 404, 422):
                errors.append(f"{base}: dominio non trovato")
                continue
            r.raise_for_status()
            payload = r.json()
            candidates: list[datetime] = []
            for ev in payload.get("events") or []:
                action = str(ev.get("eventAction") or "").lower()
                if action in {"expiration", "expiry", "expired", "registration expiration"} or "expir" in action:
                    dt = _parse_rdap_date(ev.get("eventDate"))
                    if dt:
                        candidates.append(dt)
            if not candidates:
                errors.append(f"{base}: data di scadenza non esposta")
                continue
            now = datetime.now(timezone.utc)
            future = [d for d in candidates if d.date() >= now.date()]
            canonical = str(payload.get("ldhName") or domain).lower().rstrip(".")
            if domain_key(canonical) != domain_key(domain):
                raise RuntimeError("RDAP restituisce un dominio diverso da quello richiesto")
            info = _rdap_info(payload)
            updates = [parse_date(ev.get("eventDate")) for ev in payload.get("events") or []
                       if str(ev.get("eventAction") or "").lower() in {"last changed", "last update", "last modified"}]
            updates = [dt for dt in updates if dt]
            info.update(updated_at=max(updates) if updates else None, statuses=payload.get("status") or [])
            _LAST_INFO[domain] = info
            return canonical, min(future or candidates)
        except Exception as ex:  # noqa: BLE001
            if retry_delay(ex, 1) is not None:
                raise  # Preserve HTTP status/Retry-After and transient network errors.
            errors.append(f"{base}: {ex}")

    raise RuntimeError("; ".join(errors) or "RDAP non disponibile")


def _parse_whois_date(value: str | None) -> datetime | None:
    return parse_date(value)


async def _whois_query(server: str, query: str, timeout: float = 8.0) -> str:
    """Query WHOIS RFC 3912 con limite di tempo e dimensione della risposta."""
    server = (server or "").strip().rstrip(".")
    if not server:
        raise RuntimeError("Server WHOIS non disponibile")

    async def _run() -> str:
        reader, writer = await asyncio.open_connection(server, 43)
        try:
            writer.write((query.strip() + "\r\n").encode("utf-8"))
            await writer.drain()
            chunks: list[bytes] = []
            total = 0
            while total < 1024 * 1024:
                part = await reader.read(min(65536, 1024 * 1024 - total))
                if not part:
                    break
                chunks.append(part)
                total += len(part)
            return b"".join(chunks).decode("utf-8", errors="replace")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass

    try:
        return await asyncio.wait_for(_run(), timeout=timeout)
    except asyncio.TimeoutError as ex:
        raise RuntimeError(f"Timeout WHOIS {server}") from ex


async def _whois_server_for_tld(tld: str) -> str:
    """Chiede a IANA il server WHOIS autorevole del TLD e lo mette in cache."""
    tld = tld.lower().lstrip(".")
    if tld in _WHOIS_SERVERS:
        return _WHOIS_SERVERS[tld]
    text = await _whois_query("whois.iana.org", tld, timeout=10.0)
    m = re.search(r"(?im)^whois:\s*(\S+)\s*$", text)
    if not m:
        raise RuntimeError(f"Nessun server WHOIS pubblicato per .{tld}")
    server = m.group(1).strip()
    _WHOIS_SERVERS[tld] = server
    return server


async def _whois_expiry(host: str) -> tuple[str, datetime]:
    """Fallback WHOIS RFC3912 sul solo dominio registrabile."""
    domain = _registrable_domain(host)
    if not domain or "." not in domain:
        raise RuntimeError("Hostname non registrabile")
    tld = domain.rsplit(".", 1)[-1].encode("idna").decode("ascii")
    server = await _whois_server_for_tld(tld)
    expiry_re = re.compile(
        r"(?im)^(?:registry expiry date|registrar registration expiration date|"
        r"expiration date|expiry date|expire date|expires(?: on)?|paid-till|renewal date)\s*:\s*(.+?)\s*$"
    )

    text = await _whois_query(server, domain.encode("idna").decode("ascii").lower(), timeout=20.0)
    validate_whois_domain(text, domain)
    _LAST_INFO[domain] = _whois_info(text) | whois_record_info(text)
    dates: list[datetime] = []
    for m in expiry_re.finditer(text):
        dt = _parse_whois_date(m.group(1))
        if dt:
            dates.append(dt)
    if dates:
        now = datetime.now(timezone.utc)
        future = [d for d in dates if d.date() >= now.date()]
        return domain, min(future or dates)
    raise RuntimeError(f"WHOIS {server} non espone la data di scadenza")


async def _web_whois_expiry(client: httpx.AsyncClient, host: str) -> tuple[str, datetime]:
    """Fallback HTTPS quando la porta TCP/43 e' filtrata dal provider del server.

    Usa la pagina pubblica whois.com come sorgente di confronto; estrae
    esclusivamente la data di scadenza e non salva dati del registrante.
    """
    domain = _registrable_domain(host)
    if not domain or "." not in domain:
        raise RuntimeError("Hostname non registrabile")
    r = await client.get(
        f"https://www.whois.com/whois/{domain}",
        headers={"User-Agent": "Mozilla/5.0 Sentinel-TD/1.1"},
    )
    r.raise_for_status()
    # Porta il contenuto HTML a testo semplice; le pagine WHOIS .it espongono
    # normalmente 'Expire Date: YYYY-MM-DD'. Gestiamo anche i nomi gTLD comuni.
    text = WhoisHTML(r.text).text()
    blocked = page_block_reason(text)
    if blocked:
        raise SourceError(blocked)
    validate_whois_domain(text, domain)
    _LAST_INFO[domain] = _whois_info(text) | whois_record_info(text)
    expiry_re = re.compile(
        r"(?im)^(?:registry expiry date|registrar registration expiration date|"
        r"expiration date|expiry date|expire date|expires(?: on)?|paid-till|renewal date)\s*:\s*(.+?)\s*$"
    )
    dates: list[datetime] = []
    for m in expiry_re.finditer(text):
        dt = _parse_whois_date(m.group(1))
        if dt:
            dates.append(dt)
    if not dates:
        raise RuntimeError("WHOIS HTTPS non espone la data di scadenza")
    now = datetime.now(timezone.utc)
    future = [d for d in dates if d.date() >= now.date()]
    return domain, min(future or dates)


async def _who_is_expiry(client: httpx.AsyncClient, host: str) -> tuple[str, datetime]:
    """Public who.is lookup; no account, API key or paid refresh required."""
    domain = _registrable_domain(host)
    r = await client.get(f"https://who.is/whois/{domain.encode('idna').decode('ascii')}",
                         headers={"User-Agent": "Mozilla/5.0 Sentinel-TD/2.28.12",
                                  "Cache-Control": "no-cache"})
    r.raise_for_status()
    expiry, info = parse_who_is(r.text, domain)
    _LAST_INFO[domain] = info
    return domain, expiry


_DOMAIN_LOOKUP_LOCKS: dict[str, asyncio.Lock] = {}
_DOMAIN_GATE = SourceGate()
_DOMAIN_SCAN_GATE = SourceGate(pause_seconds=30)
_DOMAIN_ATTEMPTS = 3


async def _domain_expiry(client: httpx.AsyncClient, host: str) -> tuple[str, datetime]:
    """Check every available source, sequentially within each domain."""
    domain = _registrable_domain(host)
    if not domain or "." not in domain:
        raise RuntimeError("Hostname non registrabile")
    async with _DOMAIN_LOOKUP_LOCKS.setdefault(domain, asyncio.Lock()):
        return await _DOMAIN_SCAN_GATE.call("domains", lambda: _compare_domain_sources(client, domain), timeout=None)


async def _compare_domain_sources(client, domain):
    tld = domain.rsplit(".", 1)[-1].lower()
    candidates, observations, errors = [], [], []
    sources = []
    # .it has no RDAP bootstrap: use the registry published by IANA.
    if tld != "it" and tld not in _RDAP_UNSUPPORTED_TLDS:
        sources.append(("RDAP", True, lambda: _rdap_expiry(client, domain)))
    sources += [("Registro WHOIS", True, lambda: _whois_expiry(domain)),
                ("who.is", False, lambda: _who_is_expiry(client, domain)),
                ("whois.com", False, lambda: _web_whois_expiry(client, domain))]
    _LAST_INFO.pop(domain, None)
    max_attempts = _DOMAIN_ATTEMPTS
    for priority, (source, authoritative, fetch) in enumerate(sources):
        for attempt in range(1, max_attempts + 1):
            _LAST_INFO.pop(domain, None)
            try:
                resolved, expiry = await _DOMAIN_GATE.call(source, fetch)
                if domain_key(resolved) != domain_key(domain):
                    raise RuntimeError("La fonte restituisce un dominio diverso")
                info = _LAST_INFO.pop(domain, {})
                item = dict(info, source=source, authoritative=authoritative,
                            priority=priority, expiry=expiry, attempts=attempt)
                if authoritative:
                    item["snapshot_at"] = datetime.now(timezone.utc)
                candidates.append(item)
                break
            except Exception as ex:
                _LAST_INFO.pop(domain, None)
                delay = retry_delay(ex, attempt)
                if delay is not None and attempt < max_attempts:
                    log.info("WHOIS RETRY '%s' via %s: tentativo %d/%d, riprovo tra %.0fs (%s)",
                             domain, source, attempt, max_attempts, delay, type(ex).__name__)
                    await asyncio.sleep(delay)
                    continue
                message = str(ex) or "Timeout della fonte"
                # Source labels belong to the aggregator, not repeated errors.
                prefix = source.lower() + ": "
                if message.lower().startswith(prefix):
                    message = message[len(prefix):]
                errors.append(f"{source}: {message}")
                observations.append({"source": source, "error": message[:250], "selected": False, "attempts": attempt})
                break
    now = datetime.now(timezone.utc)
    if not candidates:
        _LAST_INFO[domain] = {"source_summary": {"selected": "", "reason": "unavailable", "sources": observations}}
        raise RuntimeError("; ".join(errors)[:500] or "Scadenza dominio non rilevata")
    chosen, reason, warning = reconcile(candidates, now)
    for item in candidates:
        observations.append({"source": item["source"], "authoritative": item["authoritative"],
            "expires_at": item["expiry"].isoformat(),
            "updated_at": item.get("updated_at").isoformat() if item.get("updated_at") else None,
            "snapshot_at": item.get("snapshot_at").isoformat() if item.get("snapshot_at") else None,
            "statuses": item.get("statuses") or [], "selected": item is chosen, "attempts": item["attempts"]})
    summary = {"selected": chosen["source"], "reason": reason, "sources": observations, "warning": warning}
    pending = renewal_pending(summary, domain, chosen["expiry"], now)
    if not warning and chosen["expiry"].date() < now.date() and not chosen["authoritative"] and not pending:
        warning = ("Registro non raggiungibile: la vecchia scadenza dalla fonte WHOIS secondaria va verificata. "
                   + "; ".join(errors))[:500]
    summary.update(warning=warning, renewal_pending=pending)
    _LAST_INFO[domain] = {"registrar": chosen.get("registrar", ""), "nameservers": chosen.get("nameservers", ""),
                          "source_summary": summary}
    if warning:
        _LAST_INFO[domain]["check_warning"] = warning
    log.info("Scadenza '%s': %s via %s (%s)%s", domain, chosen["expiry"].date(), chosen["source"], reason,
             " — da verificare" if warning else " — rinnovo in corso" if pending else "")
    return domain, chosen["expiry"]


def _deadline_threshold(days: int, thresholds: list[int]) -> int | None:
    """Soglia piu' vicina gia' raggiunta (es. 25 giorni -> 30)."""
    if days < 0:
        return None
    eligible = [int(x) for x in thresholds if days <= int(x)]
    return min(eligible) if eligible else None


def _alert_state(raw: str, expires: datetime) -> tuple[dict, set[int]]:
    key = expires.astimezone(timezone.utc).date().isoformat()
    try:
        state = json.loads(raw or "{}")
        if state.get("expires") != key:
            state = {"expires": key, "sent": []}
    except Exception:  # noqa: BLE001
        state = {"expires": key, "sent": []}
    sent = {int(x) for x in (state.get("sent") or []) if str(x).isdigit()}
    return state, sent


def _save_alert_state(state: dict, sent: set[int]) -> str:
    state["sent"] = sorted(sent, reverse=True)
    return json.dumps(state, separators=(",", ":"))


async def _dispatch_expiry(*, kind: str, item: str, provider: str, notes: str,
                           expires_at: datetime, state_raw: str, thresholds: list[int],
                           site_name: str = "", site_url: str = "", platform: str = "",
                           silenced: bool = False) -> tuple[str, bool]:
    """Invia il reminder secondo le soglie configurate e ritorna (stato, inviato)."""
    exp = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
    days = (exp.astimezone(timezone.utc).date() - datetime.now(timezone.utc).date()).days
    threshold = _deadline_threshold(days, thresholds)
    state, sent = _alert_state(state_raw, exp)
    if threshold is None or threshold in sent or silenced:
        return _save_alert_state(state, sent), False
    result = await notify_dispatch("expiry_alert", {
        "site_name": site_name,
        "site_url": site_url,
        "kind": kind,
        "item": item,
        "provider": provider or "",
        "platform": platform or "",
        "notes": notes or "",
        "expires_on": exp.astimezone(timezone.utc).strftime("%d/%m/%Y"),
        "days": days,
    })
    if result["email"] or result["telegram"]:
        sent.add(threshold)
        return _save_alert_state(state, sent), True
    return _save_alert_state(state, sent), False


def _domain_lookup_due(site, domain: str, now: datetime, scan_days: int) -> bool:
    def utc(value):
        return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value
    checked, expires = utc(site.domain_checked_at), utc(site.domain_expires_at)
    return (checked is None
            or checked.date() <= (now - timedelta(days=scan_days)).date()
            or (site.domain_name or "").strip(".").lower() != domain
            or ((site.domain_check_error or (expires is not None and expires <= now + timedelta(days=30)))
                and checked < now - timedelta(hours=20)))


async def _persist_domain_updates(updates: list[dict]):
    if not updates:
        return
    async with SessionLocal() as s:
        for upd in updates:
            row = await s.get(Site, upd["id"])
            if not row:
                continue
            previous = row.domain_checked_at
            if previous is not None:
                previous = previous.replace(tzinfo=timezone.utc) if previous.tzinfo is None else previous
                if previous > upd["domain_checked_at"]:
                    continue  # Non sovrascrivere il risultato di un controllo piu' recente.
            row.domain_name = upd["domain_name"]
            row.domain_checked_at = upd["domain_checked_at"]
            if not upd["domain_check_error"] or row.domain_expires_at is None:
                row.domain_expires_at = upd["domain_expires_at"]
            row.domain_check_error = upd["domain_check_error"]
            row.domain_check_details = upd.get("domain_check_details", "")
            row.domain_registrar = upd.get("domain_registrar") or row.domain_registrar
            row.domain_nameservers = upd.get("domain_nameservers") or row.domain_nameservers
            if upd["reset_alert_state"]:
                row.domain_alert_state = ""
        await s.commit()


async def domain_expiry_scan(ctx, force: bool = False, site_id: int | None = None, sequential: bool = False, domains: list[str] | None = None):
    """Scansione domini deduplicata e cadenzata + reminder giornalieri.

    Ogni sito viene normalizzato al dominio registrabile, quindi sottodomini dello
    stesso dominio generano un solo lookup. Frequenza, pausa e tentativi sono
    configurabili da Impostazioni > Scadenze e avvisi.
    """
    prefs = await get_operational_settings()
    now = datetime.now(timezone.utc)

    async with SessionLocal() as s:
        sites = (await s.execute(select(Site))).scalars().all()
    if site_id is not None:
        target = next((site for site in sites if site.id == site_id), None)
        target_domain = _registrable_domain(_site_host(target.url)) if target else ""
        # Il controllo dal dettaglio sito aggiorna comunque tutti gli eventuali
        # sottodomini che condividono lo stesso dominio registrabile.
        sites = [site for site in sites if _registrable_domain(_site_host(site.url)) == target_domain] if target_domain else []

    # Raggruppa per dominio registrabile: es. www.X.it e shop.X.it => X.it.
    groups: dict[str, list[Site]] = {}
    invalid_sites: list[Site] = []
    for site in sites:
        host = _site_host(site.url)
        domain = _registrable_domain(host)
        if not domain or "." not in domain:
            invalid_sites.append(site)
            continue
        groups.setdefault(domain, []).append(site)

    if domains is not None:
        wanted = {_registrable_domain(_site_host(x)) for x in domains}
        groups = {name: members for name, members in groups.items() if name in wanted}
        invalid_sites = []

    registry_updates: list[dict] = []
    for site in invalid_sites:
        registry_updates.append({
            "id": site.id, "domain_name": _site_host(site.url), "domain_checked_at": now,
            "domain_expires_at": site.domain_expires_at, "domain_check_error": "Dominio non valido",
            "reset_alert_state": False,
        })

    # One domain at a time across manual and automatic jobs in this worker.
    global _DOMAIN_ATTEMPTS
    _DOMAIN_ATTEMPTS = prefs["domain_source_attempts"]
    _DOMAIN_SCAN_GATE.pause = prefs["domain_pause_seconds"]
    sem = asyncio.Semaphore(1)
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0), follow_redirects=True) as client:
        async def scan_one(domain: str, members: list[Site]) -> list[dict]:
            due = force or any(_domain_lookup_due(x, domain, now, prefs["domain_scan_days"]) for x in members)
            if not due:
                return []
            async with sem:
                resolved = domain
                expiry: datetime | None = None
                error = ""
                try:
                    resolved, expiry = await _domain_expiry(client, domain)
                except Exception as ex:  # noqa: BLE001
                    error = str(ex)[:500]
                    log.info("Scadenza dominio '%s' non rilevata: %s", domain, ex)

            info = _LAST_INFO.pop(resolved or domain, None) or _LAST_INFO.pop(domain, None) or {}
            error = error or info.get("check_warning", "")
            completed = datetime.now(timezone.utc)
            out: list[dict] = []
            for site in members:
                old_date = site.domain_expires_at.date() if site.domain_expires_at else None
                out.append({
                    "id": site.id,
                    "domain_name": resolved or domain,
                    "domain_checked_at": completed,
                    "domain_expires_at": expiry if expiry is not None and (not error or site.domain_expires_at is None) else site.domain_expires_at,
                    "domain_check_error": error,
                    "domain_check_details": json.dumps(info.get("source_summary") or {}, ensure_ascii=False),
                    "reset_alert_state": bool(expiry and not error and old_date != expiry.date()),
                    # dove e' registrato: si aggiorna solo se il lookup l'ha rilevato
                    "domain_registrar": info.get("registrar") or site.domain_registrar,
                    "domain_nameservers": info.get("nameservers") or site.domain_nameservers,
                })
            return out

        # Automatic scans now persist each result too: long scans, cancellation
        # or worker restarts must not discard already completed domains.
        await _persist_domain_updates(registry_updates)
        for domain, members in sorted(groups.items()):
            await _persist_domain_updates(await scan_one(domain, members))

    # 3) Snapshot per i reminder. Anche qui la sessione viene chiusa PRIMA degli invii
    # email/Telegram, che possono richiedere secondi e non devono trattenere lock DB.
    async with SessionLocal() as s:
        sites = (await s.execute(select(Site))).scalars().all()
        manual = (await s.execute(select(SiteExpiry))).scalars().all()

    domain_states: dict[int, str] = {}
    manual_states: dict[int, str] = {}

    # Un solo alert per dominio registrabile, anche quando piu' sottodomini dello
    # stesso cliente sono monitorati. Lo stato 30/14/7 viene poi replicato su tutti
    # i siti del gruppo per mantenere coerente il ciclo degli avvisi.
    reminder_groups: dict[str, list[Site]] = {}
    for site in sites:
        if not site.domain_expires_at:
            continue
        domain = _registrable_domain(site.domain_name or _site_host(site.url))
        reminder_groups.setdefault(domain, []).append(site)

    for domain, members in reminder_groups.items():
        if any(x.domain_check_error for x in members):
            continue  # Una data conservata dopo un lookup fallito non conferma la scadenza.
        representative = next((x for x in members if not x.notifications_silenced), members[0])
        source_state = next((x.domain_alert_state for x in members if x.domain_alert_state), representative.domain_alert_state)
        source_expiry = next((x.domain_expires_at for x in members if x.domain_expires_at), None)
        if not source_expiry:
            continue
        # Decisione di rinnovo: "" da decidere, "yes" si rinnova, "no" non si rinnova.
        renew = next((x.domain_renew for x in members if x.domain_renew), "")
        registrar = next((x.domain_registrar for x in members if x.domain_registrar), "")
        note = next((x.domain_renew_note for x in members if x.domain_renew_note), "")
        if renew == "no" and not prefs.get("domain_alert_norenew", 1):
            continue                      # deciso: non si rinnova, e gli avvisi sono disattivati

        # Soglie: a quelle di scadenza si aggiunge, per i domini ancora da decidere,
        # un promemoria molto prima, per avere il tempo di sentire il cliente.
        thresholds = list(prefs["domain_alert_days"])
        decision_days = int(prefs.get("domain_decision_days") or 0)
        if not renew and decision_days > 0 and decision_days not in thresholds:
            thresholds.append(decision_days)

        renew_label = {"yes": "Da rinnovare", "no": "Da NON rinnovare"}.get(renew, "Rinnovo da decidere")
        extra_notes = [x for x in (representative.domain_check_error or "", renew_label,
                                   f"Registrar: {registrar}" if registrar else "", note) if x]
        new_state, _ = await _dispatch_expiry(
            kind="Dominio", item=domain, provider=registrar or "Registro dominio",
            notes=" · ".join(extra_notes),
            expires_at=source_expiry, state_raw=source_state,
            thresholds=sorted(set(thresholds), reverse=True),
            site_name=", ".join(x.name for x in members), site_url=representative.url,
            silenced=all(x.notifications_silenced for x in members),
        )
        for member in members:
            domain_states[member.id] = new_state

    for row in manual:
        platform = {"wp": "WordPress", "joomla": "Joomla", "both": "WordPress + Joomla"}.get((row.platform or "both").lower(), "WordPress + Joomla")
        new_state, _ = await _dispatch_expiry(
            kind="Plugin / tema / licenza", item=row.name, provider=row.provider, notes=row.notes,
            expires_at=row.expires_at, state_raw=row.alert_state,
            thresholds=prefs["component_alert_days"], platform=platform,
        )
        manual_states[row.id] = new_state

    # 4) Salva solo gli stati dei reminder in una transazione corta.
    if domain_states or manual_states:
        async with SessionLocal() as s:
            for site_id, state in domain_states.items():
                row = await s.get(Site, site_id)
                if row:
                    row.domain_alert_state = state
            for expiry_id, state in manual_states.items():
                row = await s.get(SiteExpiry, expiry_id)
                if row:
                    row.alert_state = state
            await s.commit()


def _redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(settings.REDIS_URL)


async def poll_site(ctx, site_id: int, force: bool = False):
    """Controllo di un sito. force=True: ricalcolo forzato degli aggiornamenti sul sito, usato
    per il PRIMO controllo di un sito appena aggiunto (non c'e' ancora nessuna conoscenza
    precedente, e la cache di WordPress puo' essere vecchia o mancare: in modalita' passiva
    il sito dichiarerebbe meno aggiornamenti di quanti ne ha, o "tutto ok")."""
    async with SessionLocal() as s:
        site = await s.get(Site, site_id)
        if not site or not site.enabled:
            return
        # stato PRIMA del check, per rilevare la transizione (no spam: notifico solo
        # quando lo stato cambia, non a ogni check mentre resta offline)
        prev_status = site.status
        await apply_status(s, site, force=force)

        if site.status == "check_pending":
            await s.commit()
            await schedule_pending_recheck(ctx["redis"], site_id)
            log.info("CHECK DA CONFERMARE '%s' (id=%s): %s", site.name, site.id, site.error)
            return

        if site.status == "dns_error":
            # Resolver del monitor in difficolta': non e' una conferma di sito offline.
            await s.commit()
            await schedule_dns_recheck(ctx["redis"], site_id)
            return

        # --- conferma "non raggiungibile" (senza bloccare il worker) ---
        # Siti su server lenti hanno buchi di qualche minuto: l'avviso parte solo dopo
        # N minuti di errori CONTINUI (Impostazioni → "Avvisa che un sito non risponde dopo").
        # Prima l'attesa avveniva dormendo DENTRO il job, che teneva occupato uno dei 4 posti
        # del worker per minuti: con piu' siti giu' insieme si fermava tutto, aggiornamenti
        # compresi. Ora si segna l'inizio dell'episodio, si programma un ricontrollo fra un
        # minuto e il posto si libera subito; basta un controllo riuscito per annullare.
        if site.status == "error" and not site.offline_notified:
            try:
                from .settings_store import get_operational_settings
                window_min = int((await get_operational_settings()).get("offline_alert_minutes", 5))
            except Exception:  # noqa: BLE001
                window_min = 5
            now_c = datetime.now(timezone.utc)
            since = site.offline_since
            if since is not None and since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
            if window_min > 0 and (since is None or (now_c - since) < timedelta(minutes=window_min)):
                # non ancora confermato: nel pannello il sito resta com'era
                await s.rollback()
                site = await s.get(Site, site_id)
                if not site:
                    return
                if site.offline_since is None:
                    site.offline_since = now_c
                    await s.commit()
                log.info("SITO NON RISPONDE '%s' (id=%s): ricontrollo fra un minuto (avviso dopo %d min)",
                         site.name, site.id, window_min)
                # id diverso per ogni minuto: arq tiene occupato un id per un'ora dopo l'esecuzione
                await ctx["redis"].enqueue_job("poll_site", site_id, _defer_by=60,
                                               _job_id=f"recheck:{site_id}:{int(now_c.timestamp()) // 60}")
                return
            if site.offline_since is None:
                site.offline_since = now_c       # avviso immediato (0 minuti)
        elif site.status == "ok" and site.offline_since is not None:
            site.offline_since = None            # ha risposto: episodio chiuso

        await s.commit()

        # --- notifica Telegram robusta (parte A) ---
        # NON si basa sulla transizione effimera vista da questo singolo check (che salta
        # se il worker era fermo al momento del down), ma sullo STATO corrente + un flag
        # persistente 'offline_notified'. Cosi':
        #  - se il sito e' in error e non e' ancora stato avvisato -> avvisa
        #  - il flag si segna SOLO a invio riuscito: se Telegram fallisce, il prossimo
        #    check ritenta (niente notifica persa)
        #  - una sola notifica per episodio offline (niente spam mentre resta giu')
        #  - quando torna ok -> 🟢 e azzera il flag
        if site.status == "error" and not site.offline_notified and not site.notifications_silenced:
            _since = site.offline_since
            if _since is not None and _since.tzinfo is None:
                _since = _since.replace(tzinfo=timezone.utc)
            _elapsed = (datetime.now(timezone.utc) - _since).total_seconds() if _since else 0
            _window = max(0, round(_elapsed / 60))
            _attempts = _window + 1              # un controllo al minuto
            _r = await notify_dispatch("site_offline", {"site_name": site.name, "site_url": site.url,
                                        "reason": site.error or "", "attempts": _attempts, "window_min": _window})
            sent = _r["email"] or _r["telegram"]
            if sent:
                site.offline_notified = True
                await s.commit()
        elif site.status == "ok" and site.offline_notified:
            if not site.notifications_silenced:
                await notify_dispatch("site_online", {"site_name": site.name, "site_url": site.url})
            site.offline_notified = False
            await s.commit()


async def screenshot_tick(ctx):
    """Timer autonomo: rilegge ogni minuto l'intervallo salvato nel pannello."""
    prefs = await get_operational_settings()
    hours = max(1, min(720, int(prefs["screenshot_every_hours"])))
    now = datetime.now(timezone.utc)
    async with SessionLocal() as s:
        # Solo le colonne utili: niente caricamento delle estensioni dei siti.
        rows = (await s.execute(
            select(Site.id, Site.shot_at, Site.shot_attempted_at)
            .where(Site.enabled == True)  # noqa: E712
            .order_by(func.coalesce(Site.shot_attempted_at, Site.shot_at).asc().nullsfirst(), Site.id)
        )).all()
    queued = 0
    for site_id, shot_at, attempted_at in rows:
        if not screenshot_due(shot_at, attempted_at, now, hours):
            continue
        try:
            job = await enqueue_screenshot(ctx["redis"], site_id, delay_seconds=queued * 12)
            if job is not None:
                queued += 1
        except Exception:  # noqa: BLE001
            log.exception("Accodamento screenshot fallito (id=%s)", site_id)
    if queued:
        log.info("ANTEPRIME: accodati %d screenshot (intervallo %d ore)", queued, hours)


async def shoot_site(ctx, site_id: int):
    async with SessionLocal() as s:
        site = await s.get(Site, site_id)
        if not site or not site.enabled:
            return
        # Persistito prima della chiamata HTTP, anche se fallisce o il worker
        # riparte. Non modifica la data dell'ultima immagine riuscita.
        site.shot_attempted_at = datetime.now(timezone.utc)
        await s.commit()
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:   # video e verifiche antibot: serve margine
                r = await client.post(f"{settings.SHOOTER_URL}/shot", json={"url": site.url, "site_id": site.id})
                r.raise_for_status()
                res = r.json()
                now = datetime.now(timezone.utc)
                if res.get("blocked"):
                    # il sito respinge l'accesso automatico (antibot, 403): lo segno, cosi' si vede
                    site.shot_blocked_at = now
                    log.warning("ANTEPRIMA RESPINTA DAL SITO '%s' (id=%s): probabile antibot, consenti l'IP del pannello",
                                site.name, site.id)
                else:
                    site.shot_blocked_at = None
                if not res.get("blocked") and not res.get("kept_previous"):
                    site.shot_path = res["path"]
                    site.shot_at = now
                    log.info("SCREENSHOT OK '%s' (id=%s)", site.name, site.id)
                await s.commit()
        except Exception as ex:  # noqa: BLE001
            # WARNING e non info: a livello info il messaggio veniva filtrato dai log del
            # container e gli screenshot fallivano in silenzio per giorni senza che nulla
            # lo segnalasse. Lo screenshot resta comunque non bloccante per il monitoraggio.
            log.warning("SCREENSHOT FALLITO '%s' (id=%s): %s: %s",
                        site.name, site.id, type(ex).__name__, str(ex)[:200])


async def tick(ctx):
    # pulizia storico update piu' vecchio di 7 giorni (best effort, non blocca il tick)
    try:
        async with SessionLocal() as _s:
            # Conservazione della cronologia dettagliata: la pagina "Storico" e la dashboard
            # mostrano comunque 7 giorni; il resto serve al report dettagliato ("dalla X alla Y"
            # per ogni singolo aggiornamento). Giorni configurabili in Impostazioni.
            try:
                from .settings_store import get_operational_settings
                _keep = int((await get_operational_settings()).get("history_retention_days") or 400)
            except Exception:  # noqa: BLE001
                _keep = 400
            await _s.execute(delete(UpdateHistory).where(
                UpdateHistory.created_at < datetime.now(timezone.utc) - timedelta(days=max(7, _keep))))
            await _s.commit()
    except Exception:  # noqa: BLE001
        pass
    """Dispatcher: accoda i siti il cui ultimo check e' piu' vecchio del loro intervallo."""
    now = datetime.now(timezone.utc)
    async with SessionLocal() as s:
        rows = (await s.execute(select(Site).where(Site.enabled == True))).scalars().all()  # noqa: E712
        for site in rows:
            due = (
                site.last_checked is None
                or site.last_checked < now - timedelta(minutes=site.poll_interval_minutes)
            )
            if due:
                # _job_id stabile: arq deduplica. Se un poll_site per questo sito e'
                # gia' in coda o in esecuzione, non ne accoda un secondo (evita che su
                # batch lenti o poll piu' lunghi del tick i job si impilino).
                await ctx["redis"].enqueue_job("poll_site", site.id, _job_id=f"poll:{site.id}")


# --------------------------------------------------------------------------
# Auto-update schedulato
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# CONTROLLO VISIVO: istantanea della home prima e dopo gli aggiornamenti
# --------------------------------------------------------------------------
async def _visual_shot(site: Site, variant: str) -> dict | None:
    """Istantanea della home ('before'/'after'). None se lo shooter non risponde:
    il controllo visivo non deve mai bloccare gli aggiornamenti."""
    try:
        async with httpx.AsyncClient(timeout=150.0) as client:   # lo shooter fa una cattura alla volta
            r = await client.post(f"{settings.SHOOTER_URL}/shot",
                                  json={"site_id": site.id, "url": site.url, "variant": variant})
            r.raise_for_status()
            return r.json()
    except Exception as ex:  # noqa: BLE001
        log.warning("Istantanea %s non riuscita per '%s' (id=%s): %s", variant, site.name, site.id, ex)
        return None


async def _visual_compare(site: Site) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(f"{settings.SHOOTER_URL}/compare", json={"site_id": site.id})
            r.raise_for_status()
            return r.json()
    except Exception as ex:  # noqa: BLE001
        log.warning("Confronto visivo non riuscito per '%s' (id=%s): %s", site.name, site.id, ex)
        return None


VISUAL_DIFF_WARN = 35.0   # % di pixel cambiati oltre cui la home va guardata


def _visual_verdict(before: dict | None, after: dict | None, cmp: dict | None) -> dict:
    """ok / warn / ko / na, con un messaggio breve per email e Telegram.
    Gli errori contano solo se NON c'erano gia' prima dell'aggiornamento."""
    if not after:
        return {"status": "na", "message": "controllo visivo non disponibile"}
    if after.get("blocked"):
        # il sito ha respinto l'accesso automatico (antibot, 403): non si puo' giudicare. MAI "ko":
        # un "ko" fa partire il ripristino automatico, e qui non e' il sito che si e' rotto
        return {"status": "na", "message": "controllo della home non possibile: il sito respinge l'accesso automatico (antibot)"}
    b = before or {}
    if b.get("blocked"):
        before = None   # il "prima" era una pagina di verifica o di blocco: non si confronta con quella
    if after.get("error_text") and not b.get("error_text"):
        return {"status": "ko", "message": "errore in home dopo l'aggiornamento: «%s»" % after["error_text"][:120]}
    sa, sb = int(after.get("http_status") or 0), int(b.get("http_status") or 0)
    if sa >= 500 and sb < 500:
        return {"status": "ko", "message": f"la home risponde con errore HTTP {sa}"}
    if cmp and cmp.get("blank_after") and not cmp.get("blank_before"):
        return {"status": "ko", "message": "la home è diventata una pagina bianca"}
    if not cmp or not before:
        return {"status": "ok", "message": "home raggiungibile (confronto con il prima non disponibile)"}
    diff = float(cmp.get("diff") or 0)
    masked = float(cmp.get("masked") or 0)
    # video e slider sono esclusi dal confronto (cambiano da soli): si dice quando coprono quasi tutto
    note = f" (video e slider esclusi: {masked:g}% della home)" if masked >= 80 else (" (video e slider esclusi)" if masked > 0 else "")
    if diff >= VISUAL_DIFF_WARN:
        return {"status": "warn", "diff": diff, "message": f"la home è cambiata del {diff:g}%{note}: controlla che sia tutto a posto"}
    return {"status": "ok", "diff": diff, "message": f"home invariata (differenza {diff:g}%){note}"}


def _visual_attachments(site: Site) -> list:
    """Le due istantanee, da allegare all'email quando la home va controllata."""
    out = []
    shots = os.getenv("SHOTS_DIR", "/data/screenshots")
    for variant, label in (("before", "prima"), ("after", "dopo")):
        path = os.path.join(shots, f"site_{site.id}_{variant}.jpg")
        try:
            with open(path, "rb") as f:
                out.append((f"{label}-sito-{site.id}.jpg", "image/jpeg", f.read()))
        except OSError:
            pass
    return out


# --------------------------------------------------------------------------
# COPPIE GRATUITO + PRO: devono muoversi insieme
# --------------------------------------------------------------------------
# Portare solo il gratuito a una nuova versione principale (Elementor 3 -> 4) lasciando il
# Pro alla vecchia e' la combinazione che rompe i siti: WordPress stesso lo segnala come
# "avviso compatibilita'". Regola: prima il Pro, poi il gratuito, e solo se il Pro ce l'ha
# fatta. Gli aggiornamenti minori (4.1 -> 4.3) non vengono trattenuti.
COUPLED_PAIRS = {"elementor": "elementor-pro"}
LICENSED_FIRST = ("elementor-pro",)


def _major(v: str) -> int:
    from .routers.packages import version_tuple
    t = version_tuple(v or "")
    return t[0] if t else 0


def _plan_coupled(queue: list, exts: dict, now=None, retry: bool = False, locked: set | None = None) -> tuple[list, list, dict]:
    """Riordina la coda per le coppie gratuito + Pro.

    queue = [(ext, type, slug, name, current)]
    exts  = {slug: Extension} dei plugin installati sul sito
    Ritorna (queue, held, wait_for):
      held     = elementi tolti dalla coda (il Pro e' gia' stato tentato oggi senza esito)
      wait_for = {slug_gratuito: (slug_pro, versione_principale_richiesta)}, da verificare
                 dopo il tentativo sul Pro

    Se il Pro e' indietro ma NON risulta da aggiornare, lo si tenta lo stesso, per primo:
    con la licenza attiva Elementor Pro spesso non mostra la versione nuova finche' non
    gliela si chiede dal backend, e senza questo tentativo il gratuito resterebbe fermo.
    """
    now = now or datetime.now(timezone.utc)
    locked = locked or set()
    held, wait_for = [], {}
    for free, pro in COUPLED_PAIRS.items():
        fe = exts.get(free)
        pe = exts.get(pro)
        # Il gratuito e' BLOCCATO alla sua versione: il Pro non deve superarne la versione
        # principale da solo (Pro 4 su Elementor 3 rompe il sito quanto il contrario)
        if fe is not None and f"plugin:{free}" in locked:
            pi = next((q for q in queue if q[0] is not None and q[1] == "plugin" and q[2] == pro), None)
            if pi is not None and _major(pi[0].new_version) > _major(fe.current_version):
                queue = [q for q in queue if q is not pi]
                pi[0]._hold_reason = (f"in attesa: {fe.name} è bloccato alla {fe.current_version} e portare {pi[3]} alla "
                                      f"{_major(pi[0].new_version)}.x da solo potrebbe rompere il sito. Sblocca {fe.name} "
                                      f"per aggiornarli insieme")
                held.append(pi)
        fi = next((q for q in queue if q[0] is not None and q[1] == "plugin" and q[2] == free), None)
        if fi is None or pe is None:
            continue
        target = _major(fi[0].new_version)
        if target <= _major(fi[4]):
            continue                      # nessun salto di versione principale
        if _major(pe.current_version) >= target:
            continue                      # il Pro e' gia' alla versione principale giusta
        # Il Pro e' BLOCCATO: non si tenta (il blocco vale anche qui) e il gratuito aspetta
        if f"plugin:{pro}" in locked:
            queue = [q for q in queue if q is not fi]
            fi[0]._hold_reason = (f"in attesa: {pe.name} è bloccato alla {pe.current_version} e portare {fi[3]} alla "
                                  f"{target}.x da solo potrebbe rompere il sito. Sblocca {pe.name} per aggiornarli insieme")
            held.append(fi)
            continue
        pi = next((q for q in queue if q[0] is not None and q[1] == "plugin" and q[2] == pro), None)
        if pi is None:
            # gia' tentato nelle ultime 24 ore senza esito: non insistere, il gratuito aspetta
            # con Aggiorna premuto a mano (retry) si ritenta comunque: e' una richiesta esplicita
            tried = (not retry and getattr(pe, "update_manual", False) and pe.update_failed_at is not None
                     and (now - pe.update_failed_at) < timedelta(hours=24))
            if tried:
                queue = [q for q in queue if q is not fi]
                held.append(fi)
                continue
            pi = (pe, "plugin", pro, pe.name, pe.current_version)    # tentativo sul Pro
        else:
            queue = [q for q in queue if q is not pi]
        queue.insert(queue.index(fi), pi)    # prima il Pro…
        wait_for[free] = (pro, target)       # …poi il gratuito, solo se il Pro e' arrivato alla nuova versione
    return queue, held, wait_for


def _held_reason(name: str, pro_name: str, target: int) -> str:
    return (f"in attesa: {pro_name} non si è aggiornato su questo sito, e portare {name} alla {target}.x "
            f"da solo potrebbe rompere il sito. Carica lo zip di {pro_name} in Sentinel "
            f"(Impostazioni → Pacchetti): si aggiorneranno insieme")


def _pkg_newer(pkg_version: str, current: str) -> bool:
    from .routers.packages import version_gt
    return bool(pkg_version) and version_gt(pkg_version, current or "0")


async def _install_package(site: Site, pkg) -> dict:
    """Installa lo zip caricato in Sentinel sopra la versione presente sul sito.
    Non cambia lo stato di attivazione: un plugin attivo resta attivo, uno spento resta spento."""
    from .routers.install import _install_one
    from .routers.packages import PACKAGES_DIR
    try:
        with open(os.path.join(PACKAGES_DIR, pkg.filename), "rb") as f:
            content = f.read()
    except OSError as ex:
        return {"ok": False, "error": f"file del pacchetto non leggibile: {ex}"}
    return await _install_one(site, content, f"{pkg.slug}.zip", pkg.kind, False)


async def _panel_url() -> str:
    """Indirizzo pubblico di Sentinel (Impostazioni → Connettori), per i link nei report."""
    try:
        from .routers.connectors import _hub_url
        return await _hub_url()
    except Exception:  # noqa: BLE001
        return ""


def _folder_label(site: Site) -> str:
    """Cartelle del sito per i report: "Clienti/Flash Factory" -> "Clienti / Flash Factory"."""
    tags = [t.strip() for t in (site.tags or "").split(",") if t.strip()]
    return ", ".join(" / ".join(p.strip() for p in t.split("/") if p.strip()) for t in tags)


async def _email_mode() -> str:
    try:
        from .settings_store import get_operational_settings
        return (await get_operational_settings()).get("email_report_mode", "site")
    except Exception:  # noqa: BLE001
        return "site"


async def rollback_item(site: Site, ext_type: str, slug: str, backup_file: str) -> dict:
    """Chiede al connettore WordPress di rimettere la copia fatta prima dell'aggiornamento.
    Ritorna {ok, error, from, to}."""
    headers = {"Authorization": f"Bearer {site.token}", "X-Sentinel-Token": site.token}
    try:
        async with httpx.AsyncClient(timeout=180.0, follow_redirects=True) as client:
            r = await client.post(wp_rest_url(site, "rollback"), headers=headers,
                                  json={"type": ext_type, "slug": slug, "file": backup_file})
            r.raise_for_status()
            d = r.json()
            return {"ok": bool(d.get("ok")), "error": clean_error(d.get("error", "")), "from": str(d.get("from", "")), "to": str(d.get("to", ""))}
    except Exception as ex:  # noqa: BLE001
        return {"ok": False, "error": str(ex)[:300], "from": "", "to": ""}


async def record_rollback(s, site: Site, ext_type: str, name: str, slug: str, res: dict, how: str) -> None:
    """Riga nello storico + blocco del componente alla versione ripristinata, cosi' il ciclo
    successivo non lo riaggiorna alla versione che ha rotto il sito."""
    import json as _json
    s.add(UpdateHistory(site_id=site.id, site_name=site.name, cms=site.cms, ext_type=ext_type, ext_name=name, slug=slug,
                        from_version=res.get("from") or "", to_version=res.get("to") or "", ok=bool(res.get("ok")),
                        error=(("ripristino " + how) if res.get("ok") else f"ripristino {how} non riuscito: {res.get('error') or ''}")[:2000]))
    if res.get("ok") and ext_type in ("plugin", "theme"):
        items = site.locked_set
        items.add(f"{ext_type}:{slug}")
        site.locked_items = _json.dumps(sorted(items))


async def _update_one(site: Site, ext_type: str, slug: str, expected: str = "") -> dict:
    """Chiama il connettore per aggiornare UNA estensione/core. Ritorna {ok, error, new}."""
    headers = {
        "Authorization": f"Bearer {site.token}",
        # copia del token in un header che nessun hosting filtra: alcuni Apache in
        # CGI/FastCGI buttano via Authorization prima di PHP (401 rest_forbidden fisso)
        "X-Sentinel-Token": site.token,
        "Accept": "application/json",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    cb = int(time.time())   # cache-buster: evita risposte cachate dal reverse proxy
    try:
        async with httpx.AsyncClient(timeout=180.0, follow_redirects=True) as client:
            if site.cms == "wp":
                # 1) dal BACKEND del sito (admin-ajax.php): li' i plugin a licenza come
                #    Elementor Pro caricano il proprio sistema di aggiornamento, via REST no.
                # 2) se il connettore e' vecchio (risponde "0") o admin-ajax non e' raggiungibile,
                #    il percorso REST di sempre.
                d = None
                try:
                    ra = await client.post(
                        f"{site.url.rstrip('/')}/wp-admin/admin-ajax.php",
                        params={"action": "tdpanop_update", "_": cb},
                        headers=headers,
                        data={"type": ext_type, "slug": slug, "expected": expected or ""},
                    )
                    if ra.status_code == 200:
                        ja = ra.json()
                        if isinstance(ja, dict) and "ok" in ja:
                            d = ja
                except Exception:  # noqa: BLE001
                    d = None
                if d is None:
                    r = await client.post(
                        wp_rest_url(site, "update", f"_={cb}"),
                        headers=headers, json={"type": ext_type, "slug": slug, "expected": expected or ""},
                    )
                    r.raise_for_status()
                    d = r.json()
            else:  # joomla via com_ajax
                params = {
                    "option": "com_ajax", "plugin": "tdpanopticon", "group": "system",
                    "format": "json", "task": "update", "extype": ext_type, "slug": slug,
                    "_": cb,
                }
                r = await client.post(f"{site.url.rstrip('/')}/index.php", headers=headers, params=params)
                r.raise_for_status()
                payload = r.json()
                # com_ajax di norma wrappa in {"success":bool,"data":[...]}.
                # Gestisco anche risposte gia' "piatte" per robustezza.
                if isinstance(payload, dict) and "success" in payload:
                    if not payload.get("success"):
                        return {"ok": False, "error": payload.get("message") or "com_ajax error", "new": ""}
                    arr = payload.get("data") or []
                    d = arr[0] if arr else {"ok": False, "error": "risposta vuota", "new": ""}
                elif isinstance(payload, dict):
                    d = payload                      # risposta gia' piatta
                elif isinstance(payload, list):
                    d = payload[0] if payload else {"ok": False, "error": "risposta vuota", "new": ""}
                else:
                    d = {"ok": False, "error": "formato risposta non valido", "new": ""}
            return {"ok": bool(d.get("ok")), "error": clean_error(d.get("error", "")), "new": str(d.get("new", "")),
                    "noop": bool(d.get("noop")), "message": str(d.get("message", "")),
                    "manual": bool(d.get("manual")), "reason": str(d.get("reason", "")),
                    "backup": str(d.get("backup") or ""), "backup_error": str(d.get("backup_error") or "")}
    except Exception as ex:  # noqa: BLE001
        return {"ok": False, "error": str(ex)[:300], "new": ""}


# --------------------------------------------------------------------------
# Auto-update estensioni "vendor" (Balbooa Forms/Gallery)
# Queste NON pubblicano le nuove versioni sul canale update di Joomla: il connettore
# le prende dal file API pubblico del vendore (task=vendorupdate) e installa se piu' nuove.
# Girano DENTRO il check periodico (poll_site), non in un job separato.
# --------------------------------------------------------------------------
VENDOR_PACKAGES = {"pkg_BaForms": "Balbooa Forms", "pkg_Gallery": "Balbooa Gallery"}


async def _vendor_update_one(site: Site, slug: str) -> dict:
    """Chiama il connettore Joomla task=vendorupdate per un package vendor. {ok, error, new}."""
    headers = {
        "Authorization": f"Bearer {site.token}",
        # copia del token in un header che nessun hosting filtra: alcuni Apache in
        # CGI/FastCGI buttano via Authorization prima di PHP (401 rest_forbidden fisso)
        "X-Sentinel-Token": site.token,
        "Accept": "application/json",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    cb = int(time.time())
    try:
        async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
            params = {
                "option": "com_ajax", "plugin": "tdpanopticon", "group": "system",
                "format": "json", "task": "vendorupdate", "slug": slug, "_": cb,
            }
            r = await client.post(f"{site.url.rstrip('/')}/index.php", headers=headers, params=params)
            r.raise_for_status()
            payload = r.json()
            if isinstance(payload, dict) and "success" in payload:
                if not payload.get("success"):
                    return {"ok": False, "error": payload.get("message") or "com_ajax error", "new": ""}
                arr = payload.get("data") or []
                d = arr[0] if arr else {"ok": False, "error": "risposta vuota", "new": ""}
            elif isinstance(payload, dict):
                d = payload
            elif isinstance(payload, list):
                d = payload[0] if payload else {"ok": False, "error": "risposta vuota", "new": ""}
            else:
                d = {"ok": False, "error": "formato risposta non valido", "new": ""}
            return {"ok": bool(d.get("ok")), "error": clean_error(d.get("error", "")), "new": str(d.get("new", "")),
                    "noop": bool(d.get("noop")), "message": str(d.get("message", "")),
                    "manual": bool(d.get("manual")), "reason": str(d.get("reason", "")),
                    "backup": str(d.get("backup") or ""), "backup_error": str(d.get("backup_error") or "")}
    except Exception as ex:  # noqa: BLE001
        return {"ok": False, "error": str(ex)[:300], "new": ""}


# URL del file API pubblico + nome variabile JS, per prodotto vendor
VENDOR_API = {
    "pkg_BaForms": ("https://www.balbooa.com/updates/baforms/formsApi/formsApi.js", "formsApi"),
    "pkg_Gallery": ("https://www.balbooa.com/updates/gallery/galleryApi/galleryApi.js", "galleryApi"),
}


def _vercmp_gt(a: str, b: str) -> bool:
    """True se la versione a e' piu' recente di b (confronto numerico per componenti)."""
    pa = [int(x) for x in re.findall(r"\d+", a or "")]
    pb = [int(x) for x in re.findall(r"\d+", b or "")]
    return pa > pb


async def _fetch_vendor_latest(slug: str) -> str:
    """Scarica il file API pubblico del vendore e ne estrae la versione. '' se non riesce.
    Lo stesso file usato dal pannello 'About' del componente; una sola volta per ciclo."""
    url, var = VENDOR_API.get(slug, ("", ""))
    if not url:
        return ""
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            if r.status_code != 200:
                return ""
            m = re.search(re.escape(var) + r"\.version\s*=\s*['\"]([0-9][0-9.]*)['\"]", r.text)
            return m.group(1) if m else ""
    except Exception:  # noqa: BLE001
        return ""


async def vendor_scan(ctx):
    """RILEVAMENTO update 'vendor' Balbooa (Forms/Gallery): scarica il file API pubblico UNA
    volta (non per sito), ne legge la versione, e per ogni sito Joomla che ha quel package
    installato marca l'update come pending nel DB -> cosi' appare nel pannello come qualsiasi
    altro update. L'INSTALLAZIONE la fa il ciclo auto-update (update_site -> vendorupdate) o
    il pulsante 'Aggiorna'. Girato come cron, poco prima del ciclo di auto-update."""
    latest = {}
    for slug in VENDOR_API:
        v = await _fetch_vendor_latest(slug)
        if v:
            latest[slug] = v
    if not latest:
        return
    # la versione VERA del prodotto e' quella del COMPONENT, non del package: Balbooa
    # rilascia spesso manifest incoerenti (pkg_BaForms dichiara 2.4.3 mentre com_baforms
    # dentro e' 2.4.3.2). Confrontando col solo package si rimarcherebbe pending un
    # prodotto GIA' aggiornato -> reinstall + notifica a ogni ciclo, per sempre.
    VENDOR_COMPONENT = {"pkg_BaForms": "com_baforms", "pkg_Gallery": "com_gallery"}
    touched = set()
    async with SessionLocal() as s:
        for slug, ver in latest.items():
            exts = (await s.execute(
                select(Extension).join(Site, Site.id == Extension.site_id).where(
                    Extension.slug == slug,
                    Site.enabled == True,   # noqa: E712
                    Site.cms != "wp",
                )
            )).scalars().all()
            for e in exts:
                cur = e.current_version or ""
                # versione effettiva del prodotto sul sito: la piu' alta tra package e component
                comp_slug = VENDOR_COMPONENT.get(slug, "")
                if comp_slug:
                    comp = (await s.execute(
                        select(Extension.current_version).where(
                            Extension.site_id == e.site_id,
                            Extension.slug == comp_slug,
                        )
                    )).scalars().first() or ""
                    if comp and _vercmp_gt(comp, cur or "0"):
                        cur = comp
                if cur and _vercmp_gt(ver, cur):
                    # marca pending solo se non gia' segnato con la stessa versione
                    if not e.update_available or (e.new_version or "") != ver:
                        e.update_available = True
                        e.new_version = ver
                    touched.add(e.site_id)
                elif e.update_available and e.slug in VENDOR_PACKAGES and not _vercmp_gt(ver, cur or "0"):
                    # gia' aggiornato (il component e' >= dell'ultima): spegni eventuali
                    # pending residui marcati dai cicli precedenti col confronto sul package
                    e.update_available = False
                    e.new_version = ""
                    touched.add(e.site_id)
        # aggiorna SUBITO i contatori dei siti toccati, cosi' la card in lista diventa gialla
        # senza aspettare il prossimo poll (apply_status poi li ricalcola in modo coerente,
        # preservando i pending vendor).
        for sid in touched:
            site = await s.get(Site, sid)
            if not site:
                continue
            upd_exts = (await s.execute(
                select(Extension).where(
                    Extension.site_id == sid, Extension.update_available == True  # noqa: E712
                )
            )).scalars().all()
            up = {"plugin": 0, "theme": 0, "other": 0}
            for e in upd_exts:
                up[_category(e.type)] += 1
            site.upd_plugins, site.upd_themes, site.upd_other = up["plugin"], up["theme"], up["other"]
            site.updates_count = up["plugin"] + up["theme"] + up["other"] + (1 if site.core_update else 0)
        await s.commit()


async def _set_upd_status(redis, site_id: int, state: str, text: str = "", parts: list | None = None) -> None:
    """Esito di un Aggiorna premuto a mano, letto dal pannello per dirti com'e' andata.
    parts: l'esito a pezzi ("2 aggiornati", "1 fallito"), che il pannello traduce uno per uno."""
    try:
        await redis.set(f"upd:status:{site_id}", json.dumps({"state": state, "text": text, "parts": parts or [text],
                        "at": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False), ex=3600)
    except Exception as ex:  # noqa: BLE001
        log.warning("Esito aggiornamento non salvato (sito %s): %s", site_id, ex)


async def update_site(ctx, site_id: int, manual: bool = False):
    """Aggiorna un sito. manual=True: Aggiorna premuto a mano. Si prova subito, anche con
    l'aggiornamento automatico spento e senza la pausa dopo un fallimento, e l'esito resta
    in Redis per il pannello. Prima Aggiorna poteva fermarsi senza dire niente: interruttore
    spento, elementi in pausa o sito che non rispondeva, e il pannello diceva "accodato"."""
    redis = ctx["redis"]
    # un sito alla volta per server (vedi servers.py): se il server e' occupato o a riposo,
    # il lavoro torna in coda dopo qualche secondo invece di tenere fermo il worker
    async with SessionLocal() as s:
        _site = await s.get(Site, site_id)
        _url = _site.url if _site else ""
    server = await server_of(redis, _url) if _url else ""
    slot, wait = await srv_acquire(redis, server)
    if slot is None:
        if manual:
            await _set_upd_status(redis, site_id, "running", "in attesa: sullo stesso server sta lavorando un altro sito")
        await redis.enqueue_job("update_site", site_id, manual, _defer_by=wait)
        return

    outcome = {"text": "", "parts": [], "worked": False, "item_pause": settings.AUTOUPDATE_PAUSE_SECONDS}
    if slot != "free":
        # server col freno: anche tra un aggiornamento e l'altro dello stesso sito si aspetta
        # quanto impostato (Impostazioni -> Server dei siti), invece della pausa fissa
        try:
            outcome["item_pause"] = int((await get_operational_settings()).get(
                "server_item_pause_seconds", settings.AUTOUPDATE_PAUSE_SECONDS))
        except Exception:  # noqa: BLE001
            pass
    if manual:
        await _set_upd_status(redis, site_id, "running", "aggiornamento in corso")
    owned_context = updating_server.set(server)
    try:
        await _update_site(ctx, site_id, manual, outcome)
    except Exception as ex:  # noqa: BLE001
        outcome["text"] = f"aggiornamento interrotto da un errore: {str(ex)[:200]}"
        raise
    finally:
        updating_server.reset(owned_context)
        await srv_release(redis, server, slot, bool(outcome.get("worked")))
        if manual:
            await _set_upd_status(redis, site_id, "done", outcome["text"] or "aggiornamento concluso",
                                  outcome["parts"] or None)


async def _update_site(ctx, site_id: int, manual: bool, outcome: dict):
    item_pause = max(0, int(outcome.get("item_pause", settings.AUTOUPDATE_PAUSE_SECONDS)))
    async with SessionLocal() as s:
        site = await s.get(Site, site_id)
        if not site or not site.enabled:
            outcome["text"] = "sito disattivato in Sentinel: non aggiornato"
            return
        if not site.auto_update and not manual:
            return

        # 1) refresh per avere la lista aggiornata.
        # WP: SEMPRE forzato. Con object cache persistente il transient di update e'
        # inaffidabile ad ogni giro (sfrattato dalla LRU o vuoto) e wp_update_plugins() e'
        # throttlato a 12h: un check passivo qui leggerebbe uno stato vecchio e azzererebbe
        # la coda -> update mai tentato. Il refresh WP forzato e' leggero (contatta solo
        # wordpress.org, ~1-2s) e il connettore ora cancella i transient prima, bypassando
        # il throttle. Joomla: forzato SOLO se c'e' gia' pending nel DB, perche' li' il
        # refresh (rebuild update sites + findUpdates su tutti i canali) e' pesante e
        # inutile quando non c'e' nulla da aggiornare.
        _had_pending = bool(site.core_update) or ((site.upd_plugins or 0) + (site.upd_themes or 0) + (site.upd_other or 0)) > 0
        _force = (site.cms == "wp") or _had_pending
        await apply_status(s, site, force=_force)
        await s.commit()
        if site.status != "ok":
            # PRIMA usciva in silenzio: se il connect moriva proprio qui, il ciclo saltava
            # il sito senza log ne' notifica e il pending restava li' per ore senza che
            # nessuno se ne accorgesse (caso reale: core WP di shop). Ora lascia traccia.
            log.warning("UPDATE SALTATO '%s' (id=%s): sito in errore dopo il refresh (%s) — riprovo al prossimo ciclo",
                        site.name, site.id, (site.error or "status=" + str(site.status))[:200])
            if site.status == "check_pending":
                await schedule_pending_recheck(ctx["redis"], site.id)
                outcome["text"] = "verifica non conclusa: aggiornamento rimandato e ricontrollo programmato"
            elif site.status == "dns_error":
                await schedule_dns_recheck(ctx["redis"], site_id)
                outcome["text"] = f"verifica DNS non riuscita: {(site.error or '')[:200]}"
            else:
                outcome["text"] = f"il sito non risponde: {(site.error or str(site.status))[:200]}"
            return

        now = datetime.now(timezone.utc)
        cooldown = timedelta(hours=max(0, settings.AUTOUPDATE_RETRY_HOURS))

        # 2) costruisci la coda: prima il core, poi le estensioni con update.
        #    Il core non ha cooldown (raro e importante). Le estensioni si'.
        queue = []   # ognuno: (ext|None, type, slug, name, current)
        if site.core_update:
            label = "WordPress core" if site.cms == "wp" else "Joomla core"
            queue.append((None, "core", "", label, site.core_current))

        exts = (await s.execute(
            select(Extension).where(Extension.site_id == site.id, Extension.update_available == True)  # noqa: E712
        )).scalars().all()

        skipped = []   # estensioni saltate per cooldown (per log)
        skipped_dlkey = []   # saltate per download key mancante
        locked = site.locked_set
        for e in exts:
            # bloccato alla versione installata (pagina del sito): mai aggiornato, nemmeno a mano
            if f"{e.type}:{e.slug}" in locked:
                continue
            # download key mancante: l'update non e' scaricabile, inutile tentare (fallirebbe).
            if getattr(e, "dlkey_missing", False):
                skipped_dlkey.append(e.name)
                continue
            # cooldown: salta se ha gia' fallito di recente SULLA STESSA versione target.
            # Se e' uscita una versione nuova (new_version diverso da quello fallito) -> riprova.
            if (
                not manual
                and e.update_failed_at is not None
                and e.update_failed_version == (e.new_version or "")
                and (now - e.update_failed_at) < (max(cooldown, timedelta(hours=24)) if getattr(e, "update_manual", False) else cooldown)
            ):
                skipped.append(e.name)
                continue
            queue.append((e, e.type, e.slug, e.name, e.current_version))

        # Pacchetti caricati in Sentinel: con la licenza scaduta spesso il sito NON segnala
        # nemmeno l'aggiornamento. Se hai caricato una versione piu' nuova di quella
        # installata, il prodotto va aggiornato comunque.
        packages = {(p.kind, p.slug): p for p in (await s.execute(select(Package))).scalars().all()}
        if packages and site.cms == "wp":
            queued_ids = {q[0].id for q in queue if q[0] is not None}
            others = (await s.execute(
                select(Extension).where(Extension.site_id == site.id, Extension.update_available == False)  # noqa: E712
            )).scalars().all()
            for e in others:
                pkg = packages.get((e.type, e.slug))
                if e.id in queued_ids or not pkg or not _pkg_newer(pkg.version, e.current_version):
                    continue
                if not manual and e.update_failed_at is not None and e.update_failed_version == pkg.version \
                        and (now - e.update_failed_at) < cooldown:
                    skipped.append(e.name)
                    continue
                e.new_version = pkg.version          # cosi' il report mostra la versione di arrivo
                queue.append((e, e.type, e.slug, e.name, e.current_version))

        if skipped:
            log.info("Site '%s' (id=%s): %d update saltati per cooldown: %s",
                     site.name, site.id, len(skipped), ", ".join(skipped))
        if skipped_dlkey:
            log.info("Site '%s' (id=%s): %d update saltati per download key mancante: %s",
                     site.name, site.id, len(skipped_dlkey), ", ".join(skipped_dlkey))

        # coppie gratuito + Pro (Elementor / Elementor Pro): prima il Pro, e il gratuito
        # non salta di versione principale da solo
        held, wait_for = [], {}
        installed, names = {}, {}
        if site.cms == "wp":
            plugin_exts = {}
            for e in (await s.execute(select(Extension).where(Extension.site_id == site.id, Extension.type == "plugin"))).scalars().all():
                installed[e.slug] = e.current_version or ""
                names[e.slug] = e.name
                plugin_exts[e.slug] = e
            queue, held, wait_for = _plan_coupled(queue, plugin_exts, now, retry=manual, locked=locked)
            # I prodotti a licenza vanno PER PRIMI: subito dopo il controllo i loro dati di
            # aggiornamento sono freschi, mentre ogni aggiornamento successivo li svuota e
            # Elementor Pro interroga il proprio server al massimo una volta al minuto.
            first = [q for q in queue if q[0] is not None and q[1] == "plugin" and q[2] in LICENSED_FIRST]
            if first:
                queue = first + [q for q in queue if q not in first]
            for (e, _t, sl, nm, cur) in held:
                pro = COUPLED_PAIRS.get(sl, "")
                log.info("UPDATE IN ATTESA '%s' (id=%s): %s %s -> %s, il Pro (%s) e' gia' stato tentato oggi senza esito",
                         site.name, site.id, nm, cur, e.new_version, pro)

        if not queue:
            if held:
                outcome["text"] = f"niente da aggiornare adesso: {len(held)} in attesa del prodotto Pro"
            elif skipped_dlkey:
                outcome["text"] = f"niente da aggiornare adesso: {len(skipped_dlkey)} senza download key"
            elif skipped:
                outcome["text"] = f"niente da aggiornare adesso: {len(skipped)} in pausa dopo un tentativo fallito"
            else:
                outcome["text"] = "niente da aggiornare: il sito è già a posto"
            return

        outcome["worked"] = True     # da qui si lavora davvero sul sito: dopo, il server riposa

        # controllo visivo: istantanea della home PRIMA di toccare qualunque cosa
        shot_before = await _visual_shot(site, "before")

        # 3) aggiorna UNA per volta, con pausa; registra successo/fallimento per il cooldown
        results = []
        failures = []   # fallimenti del sito: un solo messaggio a fine giro, non uno per elemento
        done = {}   # slug -> versione dopo un aggiornamento riuscito in questo giro
        for (ext, etype, slug, name, current) in queue:
            if slug in wait_for:
                pro_slug, tmaj = wait_for[slug]
                if _major(done.get(pro_slug, installed.get(pro_slug, ""))) < tmaj:
                    pro_name = names.get(pro_slug, pro_slug)
                    log.info("UPDATE IN ATTESA '%s' (id=%s): %s resta a %s, %s non si e' aggiornato",
                             site.name, site.id, name, current, pro_name)
                    results.append({"name": name, "from": current, "to": (ext.new_version if ext is not None else ""),
                                    "ok": False, "held": True, "error": _held_reason(name, pro_name, tmaj)})
                    continue
            # SEMPRE il task update normale: e' il CONNETTORE a decidere il percorso.
            # Se l'update sta sul canale Joomla lo installa da li' (manifest coerente, riga
            # #__updates rimossa); se il canale non ha nulla e il prodotto e' Balbooa, il
            # connettore fa da solo il fallback al file API. Il vecchio instradamento
            # forzato su vendorupdate causava un loop quando il canale offriva l'update:
            # vendorupdate diceva "gia' all'ultima" senza installare, la riga restava in
            # #__updates, e il pending (con notifica) rinasceva a ogni ciclo.
            res = await _update_one(site, etype, slug, expected=(ext.new_version if ext is not None else ""))

            # PRODOTTO A LICENZA che non si aggiorna da remoto (es. Elementor Pro senza
            # pacchetto scaricabile): non e' un guasto, va fatto dal backend del sito.
            # Niente "fallito", niente notifica d'errore, riprova al massimo una volta al giorno.
            # Messaggio onesto: "licenza non attiva" solo se il connettore l'ha VERIFICATO
            # (lo dice tra parentesi); altrimenti si riporta solo il fatto. Con la licenza
            # attiva il file puo' mancare anche per una cache del produttore non ancora aggiornata.
            if not res["ok"] and res.get("manual") and res.get("reason") == "no_package":
                err = res.get("error") or ""
                hint = err[err.find("(") + 1:err.rfind(")")] if ("(" in err and err.rstrip().endswith(")")) else ""
                res["error"] = "il server del produttore non ha consegnato il file di aggiornamento a questo sito" + (f" ({hint})" if hint else "")

            via_package = False
            if not res["ok"] and res.get("manual") and site.cms == "wp":
                pkg = packages.get((etype, slug)) if packages else None
                if pkg and _pkg_newer(pkg.version, current):
                    inst = await _install_package(site, pkg)
                    if inst.get("ok"):
                        log.info("UPDATE DA PACCHETTO '%s' (id=%s): %s %s -> %s",
                                 site.name, site.id, name, current, inst.get("new") or pkg.version)
                        res = {"ok": True, "error": "", "new": inst.get("new") or pkg.version,
                               "noop": False, "message": "", "manual": False}
                        via_package = True
                    else:
                        res["error"] = (res.get("error") or "") + " · pacchetto caricato non installato: " + (inst.get("error") or "?")
                elif not pkg:
                    res["error"] = (res.get("error") or "") + " · carica lo zip in Sentinel (Impostazioni → Pacchetti) e verrà installato in automatico"

            if not res["ok"] and res.get("manual"):
                log.info("UPDATE DA FARE A MANO '%s' (id=%s): %s %s -> %s (prodotto a licenza)",
                         site.name, site.id, name, current, res["new"] or "?")
                if ext is not None:
                    ext.update_failed_at = datetime.now(timezone.utc)
                    ext.update_failed_version = ext.new_version or ""
                    ext.update_manual = True
                results.append({"name": name, "from": current, "to": res["new"] or (ext.new_version if ext is not None else ""),
                                "ok": False, "manual": True, "error": res.get("error") or ""})
                await asyncio.sleep(item_pause)
                continue

            # NIENTE DA FARE: solo quando il CONNETTORE lo dichiara esplicitamente (noop).
            # NON dedurlo da "versione uguale" o "versione assente": un pacchetto che non
            # aggiorna la propria etichetta (Balbooa, ma puo' farlo qualunque produttore)
            # farebbe sparire un aggiornamento VERO — niente storico, niente email, niente
            # Telegram. E' successo nella 2.5.1: meglio un report in piu' che uno in meno.
            nothing_done = bool(res["ok"]) and bool(res.get("noop"))
            # Per il connettore "niente da installare" puo' comunque essere un cambio di
            # versione per SENTINEL: con Balbooa il pannello vedeva 2.4.3.3 (etichetta del
            # pacchetto) e dopo vede 2.4.3.4. Per l'utente e' un aggiornamento a tutti gli
            # effetti: va nel report come "2.4.3.3 -> 2.4.3.4", nello storico e nelle statistiche.
            ver_after = (res["new"] or "").strip()
            if nothing_done and ver_after and (current or "").strip() and ver_after != (current or "").strip():
                nothing_done = False
            if nothing_done:
                # Anche quando non c'era niente da installare, Sentinel HA AGITO su quel sito
                # (ha ripulito la riga pendente): l'email e il Telegram partono comunque,
                # scrivendo chiaramente "gia' aggiornato". Nascondere questi casi ha fatto
                # sparire notifiche che l'utente si aspetta (2.5.1-2.5.4): mai piu'.
                # Resta fuori SOLO dallo storico e dal rollup, per non gonfiare le statistiche.
                log.info("UPDATE NON NECESSARIO '%s' (id=%s): %s resta a %s%s",
                         site.name, site.id, name, current or "?",
                         f" ({res.get('message')})" if res.get("message") else "")
                if ext is not None:
                    ext.update_failed_at = None
                    ext.update_failed_version = ""
                ver = (res["new"] or current or "").strip()
                results.append({
                    "name": f"{name} — già aggiornato, nessuna installazione necessaria",
                    "from": ver, "to": ver, "ok": True, "error": "",
                })
                await asyncio.sleep(item_pause)
                continue

            if res["ok"] and res["new"]:
                done[slug] = res["new"]
            results.append({
                "name": name + (" (pacchetto Sentinel)" if via_package else ""), "from": current,
                "to": res["new"] or "", "ok": res["ok"], "error": res["error"],
                "type": etype, "slug": slug, "backup": res.get("backup") or "",
            })
            # storico (7 giorni): alimenta timeline e dashboard di Sentinel
            s.add(UpdateHistory(
                site_id=site.id, site_name=site.name, cms=site.cms,
                ext_type=etype, ext_name=name, slug=slug,
                from_version=current or "", to_version=(res["new"] or ""),
                ok=bool(res["ok"]), error=(res["error"] or "")[:2000],
                backup_file=(res.get("backup") or "")[:255],
            ))
            # rollup mensile (conservato per sempre: alimenta il report mensile in PDF)
            await _roll_monthly(s, site, etype, name, slug, res, frm=current or "")
            if ext is not None:
                if res["ok"]:
                    # successo: azzera eventuale cooldown
                    ext.update_failed_at = None
                    ext.update_failed_version = ""
                    ext.update_manual = False
                else:
                    # fallito: segna timestamp e versione target, cosi' non riprova ogni ora
                    ext.update_failed_at = datetime.now(timezone.utc)
                    ext.update_failed_version = ext.new_version or ""
            # notifica immediata sul fallimento (parte A): solo i problemi, subito
            if not res["ok"]:
                log.warning("UPDATE FALLITO '%s' (id=%s): %s  %s -> %s  | motivo: %s",
                            site.name, site.id, name, current, res["new"] or "?", res.get("error") or "")
                failures.append({"name": name, "from": current or "",
                                 "to": res["new"] or (ext.new_version if ext is not None else "")
                                       or (site.core_latest if etype == "core" else "") or "",
                                 "error": res.get("error") or ""})
            elif res["new"]:
                log.info("UPDATE OK '%s' (id=%s): %s  %s -> %s",
                         site.name, site.id, name, current, res["new"])
            await asyncio.sleep(item_pause)

        for (e, _t, sl, nm, cur) in held:
            pro_slug = COUPLED_PAIRS.get(sl, "")
            results.append({"name": nm, "from": cur, "to": e.new_version or "", "ok": False, "held": True,
                            "error": getattr(e, "_hold_reason", None) or _held_reason(nm, names.get(pro_slug, pro_slug), _major(e.new_version))})

        if failures and not site.notifications_silenced:
            first = failures[0]
            await notify_dispatch("update_failed", {
                "site_name": site.name, "site_url": site.url, "folder": _folder_label(site),
                "failures": failures,
                # variabili del formato precedente (primo fallimento), per i modelli personalizzati
                "item": f"{first['name']} {first['from']} -> {first['to'] or '?'}", "reason": first["error"],
            })

        await s.commit()   # persisti lo stato di cooldown prima del re-check

        # 4) re-check finale + email report
        # FORZATO: dopo un update la cache degli update del CMS puo' essere stantia
        # (transient WP non ancora ricostruito, riga #__updates non ripulita). Un re-check
        # passivo qui ri-flaggherebbe come "da aggiornare" cio' che hai appena aggiornato
        # -> update riproposto al ciclo dopo, con relativa email/Telegram. force=True legge
        # lo stato vero (contatta i server di update); gira una sola volta per sito toccato.
        await apply_status(s, site, force=True)
        await s.commit()

        # Nessun aggiornamento REALE in questo giro (solo "niente da fare", tipicamente la
        # riga fantasma Balbooa appena ripulita): il re-check sopra serve comunque a
        # registrare il nuovo stato, ma email e riepilogo Telegram non devono partire —
        # altrimenti arriva un "0 aggiornati, 0 falliti" con la tabella vuota.
        if not results:
            log.info("Nessun aggiornamento reale per '%s' (id=%s): report non inviato", site.name, site.id)
            outcome["text"] = "niente da aggiornare: tutto era già alla versione giusta"
            return

        _n_ok = sum(1 for r in results if r.get("ok"))
        _n_fail = sum(1 for r in results if not r.get("ok") and not r.get("manual") and not r.get("held"))
        _n_other = sum(1 for r in results if r.get("manual") or r.get("held"))
        _parts = [f"{_n_ok} {'aggiornato' if _n_ok == 1 else 'aggiornati'}"]
        if _n_fail:
            _parts.append(f"{_n_fail} {'fallito' if _n_fail == 1 else 'falliti'}")
        if _n_other:
            _parts.append(f"{_n_other} in attesa o da fare a mano")
        outcome["text"] = ", ".join(_parts)
        outcome["parts"] = _parts

        # controllo visivo DOPO: solo se qualcosa e' cambiato davvero sul sito
        visual = None
        if any(r.get("ok") for r in results):
            shot_after = await _visual_shot(site, "after")
            cmp = await _visual_compare(site) if (shot_before and shot_after) else None
            visual = _visual_verdict(shot_before, shot_after, cmp)
            lvl = log.warning if visual["status"] in ("warn", "ko") else log.info
            lvl("CONTROLLO HOME '%s' (id=%s): %s — %s", site.name, site.id, visual["status"], visual["message"])

            # RIPRISTINO AUTOMATICO: la home si e' ROTTA (errore o 5xx che prima non c'erano) e
            # ci sono copie fatte prima degli aggiornamenti -> si rimettono, dall'ultimo al
            # primo, e si bloccano alla versione ripristinata. Poi si riguarda la home.
            prefs_rb = await get_operational_settings()
            restorable = [r for r in results if r.get("ok") and r.get("backup") and r.get("type") in ("plugin", "theme")]
            # solo se la home PRIMA e' stata davvero vista sana: senza quella foto (servizio delle
            # schermate giu') un sito gia' rotto prima risulterebbe rotto dall'aggiornamento
            healthy_before = bool(shot_before) and int((shot_before or {}).get("http_status") or 0) < 500 \
                and not (shot_before or {}).get("error_text")
            if visual["status"] == "ko" and site.cms == "wp" and restorable and healthy_before and prefs_rb.get("auto_rollback", True):
                rolled = []
                for r in reversed(restorable):
                    rb = await rollback_item(site, r["type"], r["slug"], r["backup"])
                    await record_rollback(s, site, r["type"], r["name"], r["slug"], rb, "automatico")
                    rolled.append({"name": r["name"], "from": r["to"], "to": rb.get("to") or r["from"], "ok": rb["ok"], "error": rb.get("error") or ""})
                    log.warning("RIPRISTINO AUTOMATICO '%s' (id=%s): %s %s -> %s: %s", site.name, site.id, r["name"], r["to"], rb.get("to"), "ok" if rb["ok"] else rb.get("error"))
                await s.commit()
                shot_fixed = await _visual_shot(site, "after")
                fixed = _visual_verdict(shot_before, shot_fixed, None)
                visual = {"status": fixed["status"], "message": "ripristino automatico: " + ("la home è tornata a rispondere" if fixed["status"] != "ko" else "la home è ancora in errore") + " — " + fixed["message"]}
                if not site.notifications_silenced:
                    await notify_dispatch("auto_rollback", {
                        "site_name": site.name, "site_url": site.url, "folder": _folder_label(site), "site_id": site.id,
                        "items": rolled, "fixed": fixed["status"] != "ko", "home_message": fixed["message"], "panel_url": await _panel_url(),
                    })

        try:
            if not site.notifications_silenced:
                ctx_report = {"site_name": site.name, "site_url": site.url, "cms": site.cms, "results": results,
                              "site_id": site.id, "panel_url": await _panel_url(), "folder": _folder_label(site)}
                if visual:
                    ctx_report["visual"] = visual
                    if visual["status"] in ("warn", "ko"):
                        ctx_report["_attachments"] = _visual_attachments(site)
                # modalita' "riepilogo per ciclo": niente email per sito (Telegram per sito resta com'e')
                per_cycle = (await _email_mode()) == "cycle"
                await notify_dispatch("site_report", ctx_report, channels={"email": False} if per_cycle else None)
        except Exception as ex:  # noqa: BLE001
            # l'invio email non deve far fallire il job, ma l'errore va tracciato
            log.warning("Invio email report fallito per '%s' (id=%s): %s", site.name, site.id, ex)

        # 5) contatori per il riepilogo Telegram aggregato (parte B).
        # Accumulo su Redis i risultati del ciclo corrente; il job cycle_summary
        # (accodato dopo tutti gli update) li legge e manda UN solo messaggio.
        try:
            if site.notifications_silenced:
                return
            n_ok = sum(1 for r in results if r["ok"])
            n_fail = sum(1 for r in results if not r["ok"] and not r.get("manual") and not r.get("held"))
            redis = ctx["redis"]
            # dati strutturati per il riepilogo di ciclo (Telegram ed email per ciclo)
            held_once = []
            for r in results:
                if r.get("held"):
                    once = f"tg:held:{site.id}:{r['name']}:{r.get('to') or ''}"
                    if await redis.set(once, "1", ex=86400, nx=True):
                        held_once.append({"name": r["name"], "error": r.get("error") or ""})
            report = {
                "site": site.name, "id": site.id, "folder": _folder_label(site),
                "ok": [{"name": r["name"], "from": r.get("from") or "", "to": r.get("to") or ""} for r in results if r.get("ok")],
                "failed": [{"name": r["name"], "error": r.get("error") or ""} for r in results
                           if not r.get("ok") and not r.get("manual") and not r.get("held")],
                "manual": [{"name": r["name"], "error": r.get("error") or ""} for r in results if r.get("manual")],
                "held": held_once,
                "visual": ({"status": visual["status"], "message": visual["message"]} if visual else None),
            }
            await redis.rpush("tg:cycle:report", json.dumps(report, ensure_ascii=False))
            for r in results:
                if r.get("manual"):
                    await redis.rpush("tg:cycle:manual_detail", f"{site.name}: {r['name']}")
                elif r.get("held") and any(h["name"] == r["name"] for h in held_once):
                    await redis.rpush("tg:cycle:manual_detail", f"{site.name}: {r['name']} (in attesa del Pro)")
            if visual:
                if visual["status"] == "ok":
                    await redis.incr("tg:cycle:visual_ok")
                elif visual["status"] in ("warn", "ko"):
                    icon = "⚠️" if visual["status"] == "warn" else "🛑"
                    await redis.rpush("tg:cycle:visual_detail", f"{icon} {site.name}: {visual['message']}")
            redis = ctx["redis"]
            if n_ok:
                await redis.incrby("tg:cycle:applied", n_ok)
                await redis.incr("tg:cycle:sites")
                # dettaglio successi: una riga per sito con i plugin aggiornati e le versioni
                # es. "Anip: Elementor 4.1.0->4.1.1, Rank Math 1.0.270->1.0.271"
                ok_items = []
                for r in results:
                    if r["ok"]:
                        frm = r.get("from") or ""
                        to = r.get("to") or ""
                        if frm and to:
                            ok_items.append(f"{r['name']} {frm}\u2192{to}")
                        else:
                            ok_items.append(r["name"])
                if ok_items:
                    await redis.rpush("tg:cycle:ok_detail", f"{site.name}: " + ", ".join(ok_items))
            if n_fail:
                await redis.incrby("tg:cycle:failed", n_fail)
                for r in results:
                    if not r["ok"] and not r.get("manual") and not r.get("held"):
                        await redis.rpush("tg:cycle:failed_detail", f"{site.name}: {r['name']}")
            # TTL di sicurezza: i contatori si autodistruggono dopo 2h se qualcosa va storto
            for k in ("tg:cycle:applied", "tg:cycle:sites", "tg:cycle:failed",
                      "tg:cycle:failed_detail", "tg:cycle:ok_detail",
                      "tg:cycle:manual_detail", "tg:cycle:visual_ok", "tg:cycle:visual_detail", "tg:cycle:report"):
                await redis.expire(k, 7200)
        except Exception as ex:  # noqa: BLE001
            log.warning("Aggiornamento contatori Telegram fallito: %s", ex)


async def auto_update_cycle(ctx):
    """Ogni ora: accoda l'update dei siti con auto_update attivo, scaglionati nel tempo."""
    if not settings.AUTOUPDATE_ENABLED:
        return
    async with SessionLocal() as s:
        rows = (await s.execute(
            select(Site).where(Site.enabled == True, Site.auto_update == True)  # noqa: E712
        )).scalars().all()

    # Niente reset dei contatori: il riepilogo li prende e li svuota da solo. Cosi' ci finisce
    # anche quello che e' stato aggiornato a mano (Aggiorna, Aggiorna tutto) dall'ultimo giro.

    delay = 0
    for site in rows:
        await ctx["redis"].enqueue_job("update_site", site.id, _defer_by=delay)
        delay += settings.AUTOUPDATE_SITE_STAGGER_SECONDS

    # accoda il riepilogo DOPO l'ultimo update (con un margine di 60s perche' i job
    # di update durano un po'). Manda un solo messaggio aggregato se e' successo qualcosa.
    summary_delay = delay + 60
    await ctx["redis"].enqueue_job("cycle_summary", _defer_by=summary_delay)


async def mass_update_now(ctx):
    """
    Mass update ON-DEMAND (pulsante 'Aggiorna tutto' in dashboard).
    Come auto_update_cycle ma:
    - accoda SOLO i siti con update effettivamente pending (core o estensioni),
      cosi' il worker non spreca giri su siti gia' aggiornati;
    - rispetta comunque il flag auto_update (non tocca siti con auto-update spento);
    - usa uno stagger piu' corto (10s) per finire prima, dato che e' lanciato a mano
      e i siti pending sono pochi.
    """
    async with SessionLocal() as s:
        # siti abilitati + auto_update on + con qualcosa da aggiornare
        rows = (await s.execute(
            select(Site).where(
                Site.enabled == True,          # noqa: E712
                Site.auto_update == True,       # noqa: E712
            )
        )).scalars().all()
        # filtro pending: core update oppure contatori estensioni > 0
        pending = [
            site for site in rows
            if site.core_update
            or (site.upd_plugins or 0) > 0
            or (site.upd_themes or 0) > 0
            or (site.upd_other or 0) > 0
        ]

    # reset contatori del riepilogo Telegram (nuovo "ciclo" manuale)

    if not pending:
        # niente da aggiornare: manda comunque un ping cosi' sai che ha girato a vuoto
        log.info("mass_update_now: nessun sito con update pending")
        return

    # stagger piu' corto del ciclo orario: qui l'utente aspetta il risultato
    stagger = min(getattr(settings, "AUTOUPDATE_SITE_STAGGER_SECONDS", 60), 10)
    delay = 0
    for site in pending:
        await ctx["redis"].enqueue_job("update_site", site.id, _defer_by=delay)
        delay += stagger

    # Riepilogo Telegram: niente messaggio proprio, i risultati finiscono nel riepilogo del
    # ciclo orario. Solo con il ciclo automatico spento si manda qui, altrimenti non arriverebbe mai.
    if not settings.AUTOUPDATE_ENABLED:
        await ctx["redis"].enqueue_job("cycle_summary", _defer_by=delay + 60)
    log.info("mass_update_now: accodati %d siti pending", len(pending))


async def mass_update_selected(ctx, site_ids: list):
    """
    Aggiorna SOLO i siti il cui id e' nella lista (Aggiorna sul sito, Aggiorna selezionati).
    E' una richiesta esplicita: si prova subito, anche con l'aggiornamento automatico spento
    e senza la pausa dopo un fallimento (update_site con manual=True). Solo i siti disattivati
    in Sentinel restano fuori.
    """
    if not site_ids:
        return
    async with SessionLocal() as s:
        rows = (await s.execute(
            select(Site).where(
                Site.id.in_(site_ids),
                Site.enabled == True,       # noqa: E712
            )
        )).scalars().all()

    if not rows:
        log.info("mass_update_selected: nessun sito valido tra i selezionati")
        return

    stagger = min(getattr(settings, "AUTOUPDATE_SITE_STAGGER_SECONDS", 60), 10)
    delay = 0
    for site in rows:
        await ctx["redis"].enqueue_job("update_site", site.id, True, _defer_by=delay)
        delay += stagger

    if not settings.AUTOUPDATE_ENABLED:
        await ctx["redis"].enqueue_job("cycle_summary", _defer_by=delay + 60)
    log.info("mass_update_selected: accodati %d siti", len(rows))


_CYCLE_KEYS = ("tg:cycle:applied", "tg:cycle:sites", "tg:cycle:failed", "tg:cycle:failed_detail",
               "tg:cycle:ok_detail", "tg:cycle:manual_detail", "tg:cycle:visual_ok", "tg:cycle:visual_detail",
               "tg:cycle:report")


async def cycle_summary(ctx):
    """Legge i contatori del ciclo da Redis e manda UN riepilogo Telegram (parte B).

    I contatori si PRENDONO E SVUOTANO in un colpo solo (transazione Redis). Prima restavano
    li' fino alla scadenza di sicurezza (2 ore): ogni altro riepilogo in coda nel frattempo
    (ciclo orario, "Aggiorna tutto", "Aggiorna" su un sito) rimandava le stesse cose, e su
    Telegram arrivavano doppioni a un minuto di distanza. Un solo riepilogo alla volta: se ne
    arriva un secondo mentre il primo lavora, si rimette in coda e manda solo il nuovo.
    """
    try:
        redis = ctx["redis"]
        if not await redis.set("tg:cycle:summary_lock", "1", ex=300, nx=True):
            await redis.enqueue_job("cycle_summary", _defer_by=60)
            return
        try:
            async with redis.pipeline(transaction=True) as pipe:
                pipe.get("tg:cycle:applied")
                pipe.get("tg:cycle:sites")
                pipe.get("tg:cycle:failed")
                pipe.lrange("tg:cycle:failed_detail", 0, -1)
                pipe.lrange("tg:cycle:ok_detail", 0, -1)
                pipe.lrange("tg:cycle:manual_detail", 0, -1)
                pipe.lrange("tg:cycle:visual_detail", 0, -1)
                pipe.get("tg:cycle:visual_ok")
                pipe.lrange("tg:cycle:report", 0, -1)
                pipe.delete(*_CYCLE_KEYS)
                (r_applied, r_sites, r_failed, r_failed_detail, r_ok_detail, r_manual_detail,
                 r_visual_detail, r_visual_ok, r_report, _deleted) = await pipe.execute()
        finally:
            await redis.delete("tg:cycle:summary_lock")

        def _dec(items):
            return [d.decode() if isinstance(d, (bytes, bytearray)) else str(d) for d in (items or [])]

        applied = int(r_applied or 0)
        sites_touched = int(r_sites or 0)
        failed = int(r_failed or 0)
        failed_detail = _dec(r_failed_detail)
        ok_detail = _dec(r_ok_detail)
        manual_detail = _dec(r_manual_detail)
        visual_detail = _dec(r_visual_detail)
        visual_ok = int(r_visual_ok or 0)
        if applied > 0 or failed > 0:
            MAX_ROWS = 30
            ok_lines = "\n".join(f"• {x}" for x in ok_detail[:MAX_ROWS]) + (f"\n…e altri {len(ok_detail) - MAX_ROWS} siti" if len(ok_detail) > MAX_ROWS else "")
            # controllo della home dopo gli aggiornamenti
            if visual_ok or visual_detail:
                ok_lines += f"\n\n🖼 Controllo home: {visual_ok} ok" + (f", {len(visual_detail)} da guardare" if visual_detail else "")
                if visual_detail:
                    ok_lines += "\n" + "\n".join(visual_detail[:MAX_ROWS])
            # prodotti a licenza da aggiornare dal backend del sito
            if manual_detail:
                ok_lines += "\n\n🔧 Da aggiornare a mano (licenza): " + str(len(manual_detail))
                ok_lines += "\n" + "\n".join(f"• {x}" for x in manual_detail[:MAX_ROWS])
            failed_lines = "\n".join(f"• {x}" for x in failed_detail[:MAX_ROWS]) + (f"\n…e altri {len(failed_detail) - MAX_ROWS}" if len(failed_detail) > MAX_ROWS else "")
            report = []
            for raw in _dec(r_report):
                try:
                    report.append(json.loads(raw))
                except Exception:  # noqa: BLE001
                    pass
            per_cycle = (await _email_mode()) == "cycle"
            await notify_dispatch("cycle_summary", {"applied": applied, "sites_touched": sites_touched, "failed": failed,
                                  "ok_lines": ok_lines, "failed_lines": failed_lines,
                                  "report": report, "when": datetime.now().strftime("%H:%M"),
                                  "panel_url": await _panel_url()},
                                  channels={"email": True} if per_cycle else None)
    except Exception as ex:  # noqa: BLE001
        log.warning("Riepilogo ciclo Telegram fallito: %s", ex)


# --------------------------------------------------------------------------
# CENTRO SICUREZZA - scansione vulnerabilita'
# --------------------------------------------------------------------------
async def security_scan(ctx):
    """
    Scarica tutte le fonti di vulnerabilita', incrocia con l'inventario estensioni,
    aggiorna i match nel DB e manda su Telegram SOLO i nuovi alert (vulnerabilita'
    appena comparse su siti non aggiornati). I match gia' notificati non ri-notificano.
    """
    try:
        result = await security.refresh_and_match()
    except Exception as ex:  # noqa: BLE001
        log.warning("security_scan: refresh_and_match fallito: %s", ex)
        return

    new_alerts = result.get("new_alerts", [])
    log.info(
        "security_scan: %d vuln totali (kev=%d vel=%d wporg=%d nvd=%d), %d nuovi alert",
        result.get("vulns", 0),
        result.get("sources", {}).get("kev", 0),
        result.get("sources", {}).get("vel", 0),
        result.get("sources", {}).get("wporg", 0),
        result.get("sources", {}).get("nvd", 0),
        len(new_alerts),
    )

    if new_alerts:
        try:
            sent = await _notify_vulns(new_alerts)
            log.info("security_scan: inviati %d alert Telegram", sent)
            if sent:
                await _mark_alerts_notified(new_alerts)
        except Exception as ex:  # noqa: BLE001
            log.warning("security_scan: invio alert Telegram fallito: %s", ex)




async def _roll_monthly(s, site, etype: str, name: str, slug: str, res: dict, frm: str = "") -> None:
    """Incrementa il contatore mensile per (periodo, sito, estensione). Upsert atomico:
    nessuna race tra update paralleli, e il conteggio 'quante volte' resta esatto."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    period = datetime.now().strftime("%Y-%m")
    ok = bool(res.get("ok"))
    stmt = pg_insert(UpdateMonthly.__table__).values(
        period=period, site_id=site.id, site_name=site.name, cms=site.cms,
        ext_type=etype, ext_name=name, slug=slug,
        ok_count=1 if ok else 0, fail_count=0 if ok else 1,
        last_version=(res.get("new") or "") if ok else "",
        first_version=frm or "",
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["period", "site_id", "ext_type", "slug"],
        set_={
            "ok_count": UpdateMonthly.__table__.c.ok_count + (1 if ok else 0),
            "fail_count": UpdateMonthly.__table__.c.fail_count + (0 if ok else 1),
            "site_name": site.name,
            "ext_name": name,
            "last_version": (res.get("new") or "") if ok else UpdateMonthly.__table__.c.last_version,
            # la versione di partenza resta quella del PRIMO aggiornamento del mese
            "first_version": func.coalesce(func.nullif(UpdateMonthly.__table__.c.first_version, ""),
                                           stmt.excluded.first_version),
            "updated_at": datetime.now(timezone.utc),
        },
    )
    try:
        await s.execute(stmt)
    except Exception as ex:  # noqa: BLE001
        log.warning("rollup mensile fallito (%s/%s): %s", site.name, slug, ex)


async def monthly_report(ctx):
    """Invio del report mensile. Gira ogni ora: quando il giorno e l'ora combaciano con
    la configurazione, genera il PDF del mese PRECEDENTE e lo invia. Il periodo gia'
    inviato viene memorizzato, quindi nessun doppione anche con riavvii o ore ripetute."""
    from . import report as rep
    from .routers.reports import send_all, send_clients
    from .models import AppSetting

    cfg = await rep.get_config()
    ccfg = await rep.get_client_config()
    now = datetime.now()
    # il tuo report e quelli dei clienti hanno giorno, ora e interruttore propri
    mine = rep.is_due(cfg, now)
    clients = rep.is_due(ccfg, now)
    if not (mine or clients):
        return
    period = rep.prev_period(now)

    # stato: quali report di QUESTO periodo sono gia' partiti (uno per cartella + globale).
    # Cosi' un fallimento su un singolo report non fa rispedire gli altri all'ora dopo.
    import json as _json
    sent_before: list[str] = []
    async with SessionLocal() as s:
        row = await s.get(AppSetting, rep.LAST_SENT_KEY)
        if row and row.value:
            try:
                data = _json.loads(row.value)
                if data.get("period") == period:
                    sent_before = list(data.get("scopes") or [])
            except (ValueError, AttributeError):
                if row.value == period:   # formato vecchio: periodo secco
                    sent_before = [rep.GLOBAL_KEY]

    # il tuo report quando e' il suo giorno e la sua ora; i clienti quando e' il loro (Report clienti
    # -> Impostazioni), ognuno poi col suo interruttore. La memoria di cosa e' gia' partito nel
    # periodo e' una sola, quindi anche con giorni diversi nessun doppione.
    results = await send_all(period, only_pending=True, already=sent_before) if mine else []
    if clients:
        results += await send_clients(period, only_pending=True, already=sent_before)
    if not results:
        return
    ok_scopes = sent_before + [r["scope"] for r in results if r.get("sent")]
    async with SessionLocal() as s:
        row = await s.get(AppSetting, rep.LAST_SENT_KEY)
        value = _json.dumps({"period": period, "scopes": ok_scopes})
        if row:
            row.value = value
        else:
            s.add(AppSetting(key=rep.LAST_SENT_KEY, value=value))
        await s.commit()
    for r in results:
        if r.get("sent"):
            log.info("REPORT MENSILE inviato (%s / %s): %s update su %s siti",
                     period, r.get("scope_label"), r.get("updates"), r.get("sites"))
        else:
            log.warning("REPORT MENSILE non inviato (%s / %s): %s", period, r.get("scope_label"), r.get("error"))

async def _notify_vulns(alerts: list) -> int:
    """Alert per le nuove vulnerabilita' via engine notifiche (template editabili).
    Ordina per gravita', invia i primi 15 singolarmente, il resto in un riepilogo."""
    sev_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1, "unknown": 0}
    alerts = [a for a in alerts if not a.get("notifications_silenced")]
    alerts = sorted(alerts, key=lambda a: (1 if a["vuln"].exploited_in_wild else 0, sev_rank.get(a["vuln"].severity, 0), a["vuln"].cvss or 0), reverse=True)
    MAX_SINGLE = 15
    sent = 0
    for a in alerts[:MAX_SINGLE]:
        v = a["vuln"]
        r = await notify_dispatch("vuln_alert", {
            "site_name": a["site_name"], "site_url": a["site_url"], "ext_name": a["ext_name"], "ext_version": a["ext_version"],
            "cve_id": v.cve_id or "", "title": (v.title or "")[:300], "severity": v.severity or "unknown",
            "cvss": f"{v.cvss:.1f}" if v.cvss else "", "exploited": "si" if v.exploited_in_wild else "no",
            "version_fixed": v.version_fixed or "", "url": v.url or a["site_url"],
        })
        if r["email"] or r["telegram"]:
            sent += 1
    extra = len(alerts) - MAX_SINGLE
    if extra > 0:
        crit = sum(1 for a in alerts if a["vuln"].exploited_in_wild)
        await send_telegram(
            "➕ <b>" + t("Altre {count} vulnerabilità", DEFAULT_LANGUAGE).format(count=extra) + "</b> "
            + t("rilevate (non elencate qui).", DEFAULT_LANGUAGE) + " "
            + t("Sfruttate attivamente: {count}.", DEFAULT_LANGUAGE).format(count=crit) + " "
            + t("Apri il Centro Sicurezza in Sentinel TD per l'elenco completo.", DEFAULT_LANGUAGE)
        )
        sent += 1
    return sent

async def _mark_alerts_notified(new_alerts):
    """Segna notified=True sui match appena notificati (per non re-inviarli domani)."""
    from sqlalchemy import select as _select, update as _sa_update
    from .models import VulnMatch, Site as _Site
    try:
        async with SessionLocal() as s:
            for a in new_alerts:
                if a.get("notifications_silenced"):
                    continue
                v = a["vuln"]
                site = (await s.execute(
                    _select(_Site).where(_Site.url == a["site_url"])
                )).scalar_one_or_none()
                if not site:
                    continue
                await s.execute(
                    _sa_update(VulnMatch)
                    .where(VulnMatch.site_id == site.id,
                           VulnMatch.vulnerability_id == v.id,
                           VulnMatch.is_vulnerable == True)  # noqa: E712
                    .values(notified=True)
                )
            await s.commit()
    except Exception as ex:  # noqa: BLE001
        log.warning("_mark_alerts_notified fallito: %s", ex)


async def _startup(ctx):
    """Allinea lo schema DB anche quando parte (o riparte) solo il worker, senza
    dipendere dal boot dell'API. Stessi ALTER idempotenti del lifespan API."""
    async with engine.begin() as conn:
        await run_migrations(conn)


async def _shutdown(ctx):
    from .connectors import close_status_client
    await close_status_client()


# --------------------------------------------------------------------------
# INSTALLAZIONE IN BLOCCO (2.9.4): un lavoro per sito, nel worker
# --------------------------------------------------------------------------
_TRANSIENT = re.compile(r"^Timeout|^HTTP 5\d\d|ConnectError|ConnectTimeout|ReadTimeout|ReadError|"
                        r"RemoteProtocolError|Server disconnected|Connection (reset|refused|aborted)", re.I)


async def _inst_set(redis, job: str, site_id: int, data: dict) -> None:
    await redis.hset(f"inst:{job}:res", str(site_id), json.dumps(data, ensure_ascii=False))
    await redis.expire(f"inst:{job}:res", 86400)


async def install_site(ctx, job: str, site_id: int, attempt: int = 1):
    """Installa lo zip di un lavoro di installazione in blocco su UN sito.

    Prende un posto sul server del sito come gli aggiornamenti. Timeout ed errori del server
    (5xx, connessione caduta) si ritentano una volta dopo un minuto: sui server deboli sono
    quasi sempre momentanei. Gli altri errori (token, connettore, zip) restano tali.
    """
    from .routers.install import _install_one
    redis = ctx["redis"]
    raw = await redis.get(f"inst:{job}")
    if not raw:
        return
    meta = json.loads(raw)
    async with SessionLocal() as s:
        site = await s.get(Site, site_id)
    if site is None:
        await _inst_set(redis, job, site_id, {"site_id": site_id, "site_name": "?", "state": "done", "ok": False,
                                              "error": "sito non trovato", "attempt": attempt})
        return
    base = {"site_id": site.id, "site_name": site.name, "url": site.url, "attempt": attempt}
    server = await server_of(redis, site.url)
    slot, wait = await srv_acquire(redis, server)
    if slot is None:
        await _inst_set(redis, job, site.id, {**base, "state": "waiting", "ok": False, "error": ""})
        await redis.enqueue_job("install_site", job, site_id, attempt, _defer_by=wait)
        return
    try:
        await _inst_set(redis, job, site.id, {**base, "state": "running", "ok": False, "error": ""})
        try:
            with open(meta["path"], "rb") as f:
                content = f.read()
        except OSError as ex:
            await _inst_set(redis, job, site.id, {**base, "state": "done", "ok": False,
                                                  "error": f"pacchetto non leggibile: {ex}"})
            return
        res = await _install_one(site, content, meta["filename"], meta["kind"], bool(meta["activate"]))
        if not res.get("ok") and attempt == 1 and _TRANSIENT.search(str(res.get("error") or "")):
            await _inst_set(redis, job, site.id, {**base, **res, "state": "retry", "ok": False,
                                                  "error": clean_error(res.get("error"))})
            await redis.enqueue_job("install_site", job, site_id, 2, _defer_by=60)
            return
        await _inst_set(redis, job, site.id, {**base, **res, "state": "done", "error": clean_error(res.get("error"))})
    finally:
        await srv_release(redis, server, slot, True)


# --------------------------------------------------------------------------
# DIAGNOSTICA DEI SITI (2.9.0)
# --------------------------------------------------------------------------
async def connector_rollout(ctx):
    """Ogni notte: il connettore consegnato dal pannello sui siti che ne hanno uno piu' vecchio,
    con i lavori dell'installazione in blocco (un sito per lavoro, posti sui server rispettati).
    Si spegne da Impostazioni -> Connettori."""
    from .routers.connectors import KINDS, connector_package, outdated_sites, shipped_version
    from .routers.install import start_install_job
    prefs = await get_operational_settings()
    if not prefs.get("connector_auto_update", True):
        return
    for kind in KINDS:
        async with SessionLocal() as s:
            targets = await outdated_sites(kind, s)
        if not targets:
            continue
        try:
            content, name = await connector_package(kind)
        except Exception as ex:  # noqa: BLE001
            log.warning("Connettore %s: pacchetto non pronto, distribuzione saltata: %s", kind, ex)
            continue
        res = await start_install_job(targets, content, name, "wp" if kind == "wp" else "joomla", "plugin", True,
                                      label=f"connettore {kind} {shipped_version(kind)} (notturno)")
        log.info("Connettore %s %s: distribuzione notturna su %s siti (lavoro %s)", kind, shipped_version(kind), len(targets), res["job"])


async def plugin_catalog_scan(ctx, force: bool = False):
    """Catalogo dei plugin da wordpress.org: ogni settimana, o subito dal pulsante."""
    from .plugin_catalog import scan
    try:
        res = await scan(force=force)
        log.info("Catalogo plugin aggiornato: %s", res)
    except Exception as ex:  # noqa: BLE001
        log.warning("Catalogo plugin: scansione non riuscita: %s", ex)


async def retired_diagnostics_job(ctx, *args):
    """Discard diagnostics queued by a previous version without contacting the site."""
    return


class WorkerSettings:
    functions = [poll_site, arq_func(shoot_site, keep_result=0), update_site, cycle_summary, mass_update_now,
                 mass_update_selected, security_scan, vendor_scan, arq_func(domain_expiry_scan, timeout=21600, keep_result=0), monthly_report,
                 arq_func(retired_diagnostics_job, name="diag_site", keep_result=0), connector_rollout, plugin_catalog_scan, install_site]
    cron_jobs = [
        cron(tick, minute=set(range(0, 60, max(1, settings.SCHEDULER_TICK_MINUTES))), run_at_startup=True),
        cron(screenshot_tick, minute=set(range(60)), run_at_startup=True),
        cron(vendor_scan, minute={50}, run_at_startup=True),   # rileva update Balbooa (ogni ora)
        cron(auto_update_cycle, minute={0}),   # ogni ora, al minuto 0 (installa i pending)
        cron(security_scan, hour={6}, minute={30}),   # scansione sicurezza giornaliera 06:30
        cron(domain_expiry_scan, hour={7}, minute={15}, run_at_startup=True, timeout=21600),  # reminder giornalieri; registro secondo i giorni impostati; errori ritentati ogni giorno
        cron(monthly_report, minute=set(range(0, 60, 5))),   # ogni 5 minuti: invia quando giorno e orario sono arrivati
        cron(connector_rollout, hour={4}, minute={30}),   # connettore nuovo sui siti che ne hanno uno vecchio
        cron(plugin_catalog_scan, weekday={6}, hour={5}, minute={0}, run_at_startup=True),   # catalogo plugin da wordpress.org: la domenica, e al primo avvio
    ]
    on_startup = _startup
    on_shutdown = _shutdown
    redis_settings = _redis_settings()
    max_jobs = 4             # max job concorrenti: limita quanti siti si aggiornano insieme
    job_timeout = 1800       # gli update di un sito possono durare diversi minuti
