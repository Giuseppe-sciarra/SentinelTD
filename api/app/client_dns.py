"""Controllo DNS dei domini dei clienti: MX, NS e A letti dal DNS pubblico.

Ogni lettura si confronta con la precedente. Un cambio si registra solo se:
- la lettura e' riuscita (un timeout o un errore del resolver non e' un cambio);
- una seconda lettura, pochi secondi dopo, dice la stessa cosa (niente falsi allarmi da un
  server DNS che risponde male una volta).
Per i record A conta solo un cambio completo (nessun indirizzo in comune): i servizi con piu'
indirizzi che ruotano (CDN) non fanno scattare l'avviso.
Il cambio resta sulla scheda finche' non lo segni come visto; l'avviso parte una volta per
cliente, con tutti i domini cambiati in quel giro.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

log = logging.getLogger("client_dns")

RUNNING_KEY = "clients:dns:running"
CONFIG_KEY = "clients:dns_config"
CONCURRENCY = 8
CONFIRM_DELAY = 3.0


def _resolver():
    import dns.asyncresolver
    r = dns.asyncresolver.Resolver()
    r.timeout = 4
    r.lifetime = 8
    return r


async def _query(res, name: str, rtype: str):
    import dns.exception
    import dns.resolver
    try:
        ans = await res.resolve(name, rtype, search=False)
    except dns.resolver.NXDOMAIN:
        return None, "nx"
    except dns.resolver.NoAnswer:
        return [], ""
    except dns.resolver.NoNameservers:
        return None, "nessun server DNS ha risposto"
    except dns.exception.Timeout:
        return None, "timeout"
    except Exception as ex:  # noqa: BLE001
        return None, str(ex)[:120] or ex.__class__.__name__
    out = []
    for r in ans:
        if rtype == "MX":
            out.append(f"{int(r.preference)} {str(r.exchange).rstrip('.').lower()}")
        elif rtype == "NS":
            out.append(str(r.target).rstrip(".").lower())
        else:
            out.append(r.to_text())
    return sorted(set(out)), ""


async def lookup(domain: str, resolver=None) -> dict:
    """{"mx": [...], "ns": [...], "a": [...], "nx": bool, "error": str}"""
    res = resolver or _resolver()
    mx, e1 = await _query(res, domain, "MX")
    if e1 == "nx":
        return {"mx": [], "ns": [], "a": [], "nx": True, "error": ""}
    ns, e2 = await _query(res, domain, "NS")
    a, e3 = await _query(res, domain, "A")
    err = next((e for e in (e1, e2, e3) if e and e != "nx"), "")
    return {"mx": mx or [], "ns": ns or [], "a": a or [], "nx": False, "error": err}


def snapshot_of(d) -> dict | None:
    """Ultima lettura salvata sul dominio, None se mai controllato."""
    if not d.dns_checked_at:
        return None

    def lst(v):
        try:
            x = json.loads(v or "[]")
            return x if isinstance(x, list) else []
        except ValueError:
            return []
    return {"mx": lst(d.dns_mx), "ns": lst(d.dns_ns), "a": lst(d.dns_a), "nx": bool(d.dns_nx)}


def diff(prev: dict | None, cur: dict) -> dict:
    """Cambi tra due letture: {"mx": {"from": [...], "to": [...]}, ...}; {} se niente."""
    if prev is None or cur.get("error"):
        return {}
    if bool(prev.get("nx")) != bool(cur.get("nx")):
        return {"resolve": {"from": "non risolve" if prev.get("nx") else "risolve",
                            "to": "non risolve" if cur.get("nx") else "risolve"}}
    if cur.get("nx"):
        return {}
    out = {}
    for k in ("mx", "ns"):
        if sorted(prev.get(k) or []) != sorted(cur.get(k) or []):
            out[k] = {"from": sorted(prev.get(k) or []), "to": sorted(cur.get(k) or [])}
    pa, ca = set(prev.get("a") or []), set(cur.get("a") or [])
    if pa != ca and not (pa & ca):
        out["a"] = {"from": sorted(pa), "to": sorted(ca)}
    return out


def merge_change(old_json: str, new: dict) -> dict:
    """Un cambio non ancora visto + uno nuovo: resta il "prima" piu' vecchio; se si torna
    com'era, quella voce sparisce."""
    try:
        old = json.loads(old_json or "{}")
    except ValueError:
        old = {}
    out = dict(old) if isinstance(old, dict) else {}
    for k, v in new.items():
        start = out.get(k, {}).get("from", v["from"]) if isinstance(out.get(k), dict) else v["from"]
        if start == v["to"]:
            out.pop(k, None)
        else:
            out[k] = {"from": start, "to": v["to"]}
    return out


async def get_config() -> dict:
    from .db import SessionLocal
    from .models import AppSetting
    cfg = {"enabled": True}
    try:
        async with SessionLocal() as s:
            row = await s.get(AppSetting, CONFIG_KEY)
            if row and row.value:
                cfg.update({k: v for k, v in json.loads(row.value).items() if k in cfg})
    except Exception:  # noqa: BLE001
        pass
    cfg["enabled"] = bool(cfg["enabled"])
    return cfg


async def save_config(data: dict) -> dict:
    from .db import SessionLocal
    from .models import AppSetting
    cfg = {"enabled": bool((data or {}).get("enabled", True))}
    async with SessionLocal() as s:
        row = await s.get(AppSetting, CONFIG_KEY)
        if row:
            row.value = json.dumps(cfg)
        else:
            s.add(AppSetting(key=CONFIG_KEY, value=json.dumps(cfg)))
        await s.commit()
    return cfg


async def check_domains(session, domains, resolver=None, sleep=asyncio.sleep) -> dict:
    """Legge il DNS dei domini dati (stessa sessione, chi chiama fa il commit).
    Ritorna {client_id: [(dominio, cambio), ...]} con i soli cambi nuovi dei domini attivi."""
    sem = asyncio.Semaphore(CONCURRENCY)
    res = resolver or _resolver()

    async def one(d):
        if "." not in d.domain:
            return d, None, {}
        async with sem:
            cur = await lookup(d.domain, res)
            ch = diff(snapshot_of(d), cur)
            if ch:
                await sleep(CONFIRM_DELAY)
                again = await lookup(d.domain, res)
                if diff(snapshot_of(d), again) != ch:
                    return d, None, {}          # seconda lettura diversa: si riprova al prossimo giro
                cur = again
            return d, cur, ch

    results = await asyncio.gather(*(one(d) for d in domains))
    now = datetime.now(timezone.utc)
    changed: dict[int, list] = {}
    for d, cur, ch in results:
        if cur is None:
            continue
        if cur.get("error"):
            d.dns_error = cur["error"][:255]     # l'ultima lettura buona resta quella di prima
            continue
        if ch:
            merged = merge_change(d.dns_change, ch)
            d.dns_change = json.dumps(merged, ensure_ascii=False) if merged else ""
            d.dns_changed_at = now if merged else None
            if d.active:
                changed.setdefault(d.client_id, []).append((d.domain, ch))
        d.dns_mx, d.dns_ns, d.dns_a = (json.dumps(cur[k]) for k in ("mx", "ns", "a"))
        d.dns_nx = bool(cur["nx"])
        d.dns_error = ""
        d.dns_checked_at = now
    return changed


async def run_check(client_id: int | None = None, resolver=None, notify=True) -> dict:
    """Controllo di tutti i domini (o di un cliente), con registro eventi e avviso."""
    from sqlalchemy import select
    from .db import SessionLocal
    from .eventlog import record as evlog
    from .models import Client, ClientDomain

    async with SessionLocal() as s:
        clients = {c.id: c for c in (await s.execute(select(Client))).scalars().all()}
        q = select(ClientDomain).order_by(ClientDomain.client_id, ClientDomain.domain)
        if client_id:      # un cliente: anche i domini dei suoi sottoclienti
            q = q.where(ClientDomain.client_id.in_([client_id, *[c.id for c in clients.values() if c.parent_id == client_id]]))
        domains = (await s.execute(q)).scalars().all()
        found = await check_domains(s, domains, resolver)
        # un avviso per cliente principale: i sottoclienti finiscono nel messaggio del loro cliente
        changed: dict[int, list] = {}
        for cid, items in found.items():
            sub = clients.get(cid)
            root = sub.parent_id if sub and sub.parent_id else cid
            for dom, ch in items:
                changed.setdefault(root, []).append((dom, ch))
                label = sub.name if sub else ""
                if sub and sub.parent_id and root in clients:
                    label = f"{clients[root].name} › {sub.name}"
                await evlog(s, "domains", "warn", f"DNS cambiati: {dom} ({label})",
                            details={"client_id": cid, "domain": dom, "change": ch})
        names = {cid: clients[cid].name for cid in changed if cid in clients}
        await s.commit()
    errors = sum(1 for d in domains if d.dns_error)
    if notify and changed:
        await _notify(changed, names)
    log.info("Controllo DNS clienti: %s domini, %s clienti con cambi, %s errori", len(domains), len(changed), errors)
    return {"checked": len(domains), "changed": sum(len(v) for v in changed.values()), "clients": len(changed), "errors": errors}


async def _notify(changed: dict, names: dict) -> None:
    from .notify import dispatch
    try:
        from .routers.connectors import _hub_url
        base = (await _hub_url()).rstrip("/")
    except Exception:  # noqa: BLE001
        base = ""
    for cid, items in changed.items():
        ctx = {"client_name": names.get(cid, ""),
               "changes": [{"domain": dom, "kind": k, "from": v.get("from"), "to": v.get("to")}
                           for dom, ch in items for k, v in ch.items()],
               "panel_url": f"{base}/#/customers/{cid}" if base else ""}
        try:
            await dispatch("client_dns_changed", ctx)
        except Exception as ex:  # noqa: BLE001
            log.warning("Avviso DNS cambiati non inviato: %s", ex)
