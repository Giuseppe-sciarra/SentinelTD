"""Gestione domini: scheda del cliente (domini, posta, DNS, rinnovi) e provider.

Solo consultazione e appunti: nessuna password, solo riferimenti all'account."""
import json
from datetime import datetime, timezone

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import client_cards as cc
from .. import client_dns
from ..auth import require_auth
from ..config import settings
from ..db import get_session
from ..models import Client, ClientDomain, ClientProvider, Site
from ..settings_store import get_operational_settings

router = APIRouter(prefix="/api/clients", tags=["client-cards"], dependencies=[Depends(require_auth)])

MAX_UPLOAD = 5 * 1024 * 1024
PROVIDER_FIELDS = ("name", "login", "create_mailbox", "change_password", "forwarding", "webmail", "notes")
DOMAIN_EDIT = ("mail_host", "manage_howto", "panel_url", "account_ref", "webmail_url", "dns_host", "site_host",
               "notes", "status_note", "renewal_payer", "renewal_cost")


def _iso(v):
    return v.isoformat() if v else None


def _json_list(v):
    try:
        x = json.loads(v or "[]")
        return x if isinstance(x, list) else []
    except ValueError:
        return []


def _days(d) -> int | None:
    if not d:
        return None
    if isinstance(d, datetime):
        d = (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).date()
    return (d - datetime.now(timezone.utc).date()).days


def _level(days, warn, crit) -> str:
    if days is None:
        return ""
    if days < 0:
        return "expired"
    if days <= crit:
        return "crit"
    return "warn" if days <= warn else ""


def provider_out(p: ClientProvider) -> dict:
    return {"id": p.id, **{f: getattr(p, f) or "" for f in PROVIDER_FIELDS}}


async def _site_map(s: AsyncSession) -> dict:
    """dominio -> sito di Sentinel (indirizzo del sito o dominio registrato)."""
    out = {}
    for site in (await s.execute(select(Site))).scalars().all():
        host = cc.site_host(site.url)
        for key in (host, (site.domain_name or "").lower().strip()):
            if key and key not in out:
                out[key] = site
    return out


def domain_out(d: ClientDomain, providers: dict, sites: dict, warn: int, crit: int) -> dict:
    dom = d.domain
    site = sites.get(dom) or sites.get(dom[4:] if dom.startswith("www.") else "")
    hd = _days(d.hosting_expires)
    try:
        change = json.loads(d.dns_change) if d.dns_change else {}
    except ValueError:
        change = {}
    p = providers.get(d.provider_id)
    site_out = None
    if site:
        sd = _days(site.domain_expires_at)
        site_out = {"id": site.id, "name": site.name, "url": site.url, "status": site.status,
                    "domain_expires_at": _iso(site.domain_expires_at), "domain_days": sd,
                    "domain_level": _level(sd, warn, crit)}
    return {
        "id": d.id, "client_id": d.client_id, "domain": dom, "active": bool(d.active),
        **{f: getattr(d, f) or "" for f in DOMAIN_EDIT},
        "provider_id": d.provider_id, "provider_name": p.name if p else "",
        "hosting_expires": _iso(d.hosting_expires), "hosting_days": hd, "hosting_level": _level(hd, warn, crit),
        "dns": {"mx": _json_list(d.dns_mx), "ns": _json_list(d.dns_ns), "a": _json_list(d.dns_a),
                "nx": bool(d.dns_nx), "error": d.dns_error or "", "checked_at": _iso(d.dns_checked_at)},
        "dns_change": change, "dns_changed_at": _iso(d.dns_changed_at),
        "site": site_out,
    }


async def _client(s: AsyncSession, cid: int) -> Client:
    cl = await s.get(Client, cid)
    if not cl:
        raise HTTPException(404, "Cliente non trovato")
    return cl


async def _children(s: AsyncSession, cid: int) -> list[Client]:
    return (await s.execute(select(Client).where(Client.parent_id == cid).order_by(func.lower(Client.name)))).scalars().all()


async def _shown_ids(s: AsyncSession, cl: Client) -> list[int]:
    """Domini mostrati nella scheda: quelli del cliente e dei suoi sottoclienti."""
    return [cl.id, *[c.id for c in await _children(s, cl.id)]]


async def _family_ids(s: AsyncSession, cl: Client) -> list[int]:
    """Dove si puo' spostare un dominio: il cliente principale e tutti i suoi sottoclienti."""
    root = cl.parent_id or cl.id
    return [root, *[c.id for c in await _children(s, root)]]


async def _redis_get(key: str):
    pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    try:
        return await pool.get(key)
    finally:
        await pool.aclose()


async def _start_check(client_id: int | None) -> dict:
    pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    try:
        if not await pool.set(client_dns.RUNNING_KEY, b"queued", ex=900, nx=True):
            raise HTTPException(409, "Controllo DNS già in corso: riprova tra poco")
        await pool.enqueue_job("client_dns_check", client_id, True)
    finally:
        await pool.aclose()
    return {"queued": True}


# ------------------------------------------------------------------ provider
@router.get("/providers")
async def list_providers(s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(ClientProvider).order_by(func.lower(ClientProvider.name)))).scalars().all()
    used = dict((await s.execute(select(ClientDomain.provider_id, func.count()).group_by(ClientDomain.provider_id))).all())
    return [{**provider_out(p), "domains": int(used.get(p.id, 0))} for p in rows]


def _provider_data(payload: dict) -> dict:
    data = {f: str(payload.get(f) or "").strip() for f in PROVIDER_FIELDS}
    data["name"] = data["name"][:190]
    if not data["name"]:
        raise HTTPException(422, "Serve il nome del provider")
    return data


async def _relink(s: AsyncSession) -> None:
    """Domini senza provider: si riprova l'abbinamento dal campo "Email ospitate su"."""
    providers = (await s.execute(select(ClientProvider))).scalars().all()
    for d in (await s.execute(select(ClientDomain).where(ClientDomain.provider_id.is_(None)))).scalars().all():
        d.provider_id = cc.match_provider(d.mail_host, providers)


@router.post("/providers")
async def create_provider(payload: dict = Body(...), s: AsyncSession = Depends(get_session)):
    data = _provider_data(payload)
    if (await s.execute(select(ClientProvider).where(func.lower(ClientProvider.name) == data["name"].lower()))).scalar():
        raise HTTPException(409, "Esiste già un provider con questo nome")
    p = ClientProvider(**data)
    s.add(p)
    await s.flush()
    await _relink(s)
    await s.commit()
    return provider_out(p)


@router.put("/providers/{pid}")
async def update_provider(pid: int, payload: dict = Body(...), s: AsyncSession = Depends(get_session)):
    p = await s.get(ClientProvider, pid)
    if not p:
        raise HTTPException(404, "Provider non trovato")
    data = _provider_data({**provider_out(p), **payload})
    other = (await s.execute(select(ClientProvider).where(func.lower(ClientProvider.name) == data["name"].lower(),
                                                          ClientProvider.id != pid))).scalar()
    if other:
        raise HTTPException(409, "Esiste già un provider con questo nome")
    for k, v in data.items():
        setattr(p, k, v)
    await _relink(s)
    await s.commit()
    return provider_out(p)


@router.delete("/providers/{pid}")
async def delete_provider(pid: int, s: AsyncSession = Depends(get_session)):
    p = await s.get(ClientProvider, pid)
    if not p:
        raise HTTPException(404, "Provider non trovato")
    for d in (await s.execute(select(ClientDomain).where(ClientDomain.provider_id == pid))).scalars().all():
        d.provider_id = None
    await s.delete(p)
    await s.commit()
    return {"deleted": True}


# ------------------------------------------------------------------ controllo DNS
@router.get("/dns/config")
async def dns_config():
    cfg = await client_dns.get_config()
    running = await _redis_get(client_dns.RUNNING_KEY)
    return {**cfg, "running": bool(running)}


@router.put("/dns/config")
async def save_dns_config(payload: dict = Body(...)):
    return await client_dns.save_config(payload)


@router.post("/dns/check")
async def dns_check_all():
    return await _start_check(None)


# ------------------------------------------------------------------ scheda cliente
@router.get("/{cid}/card")
async def client_card(cid: int, s: AsyncSession = Depends(get_session)):
    cl = await _client(s, cid)
    prefs = await get_operational_settings()
    warn, crit = int(prefs.get("expiry_warning_days") or 30), int(prefs.get("expiry_critical_days") or 7)
    providers = {p.id: p for p in (await s.execute(select(ClientProvider))).scalars().all()}
    sites = await _site_map(s)
    children = await _children(s, cid)
    parent = await s.get(Client, cl.parent_id) if cl.parent_id else None
    siblings = await _children(s, parent.id) if parent else []
    rows = (await s.execute(select(ClientDomain).where(ClientDomain.client_id.in_([cid, *[c.id for c in children]]))
                            .order_by(ClientDomain.domain))).scalars().all()
    domains = [domain_out(d, providers, sites, warn, crit) for d in rows]
    per_sub: dict[int, dict] = {}
    for d in domains:
        c = per_sub.setdefault(d["client_id"], {"active": 0, "inactive": 0, "dns_changes": 0})
        c["active" if d["active"] else "inactive"] += 1
        c["dns_changes"] += int(bool(d["dns_change"]))
    hosts: dict[str, int] = {}
    for d in domains:
        if d["active"]:
            key = d["mail_host"] or "—"
            hosts[key] = hosts.get(key, 0) + 1
    used = {d["provider_id"] for d in domains if d["provider_id"]}
    checked = [d["dns"]["checked_at"] for d in domains if d["dns"]["checked_at"]]
    running = await _redis_get(client_dns.RUNNING_KEY)
    return {
        "client": {"id": cl.id, "name": cl.name, "notes": cl.notes or "", "emails": cl.emails, "enabled": bool(cl.enabled),
                   "parent_id": cl.parent_id},
        "parent": {"id": parent.id, "name": parent.name} if parent else None,
        # sottoclienti (per un cliente principale) e dove si possono spostare i domini
        "subs": [{"id": c.id, "name": c.name, "notes": c.notes or "", "emails": c.emails,
                  **per_sub.get(c.id, {"active": 0, "inactive": 0, "dns_changes": 0})} for c in children],
        "targets": ([{"id": parent.id, "name": parent.name, "root": True}, *[{"id": c.id, "name": c.name, "root": False} for c in siblings]]
                    if parent else [{"id": cl.id, "name": cl.name, "root": True}, *[{"id": c.id, "name": c.name, "root": False} for c in children]]),
        "domains": domains,
        "providers": [provider_out(providers[pid]) for pid in sorted(used, key=lambda x: providers[x].name.lower())],
        "summary": {
            "active": sum(1 for d in domains if d["active"]),
            "inactive": sum(1 for d in domains if not d["active"]),
            "mail_hosts": [{"name": k, "count": v} for k, v in sorted(hosts.items(), key=lambda x: (-x[1], x[0].lower()))],
            "dns_changes": sum(1 for d in domains if d["dns_change"]),
            "dns_errors": sum(1 for d in domains if d["dns"]["error"]),
            "expiring": sum(1 for d in domains if d["hosting_level"] in ("warn", "crit", "expired")),
            "last_check": max(checked) if checked else None,
        },
        "dns_running": bool(running),
        "thresholds": {"warn": warn, "crit": crit},
    }


@router.put("/{cid}/notes")
async def save_notes(cid: int, payload: dict = Body(...), s: AsyncSession = Depends(get_session)):
    cl = await _client(s, cid)
    cl.notes = str(payload.get("notes") or "")[:20000]
    await s.commit()
    return {"notes": cl.notes}


async def _target_client(s: AsyncSession, cl: Client, payload: dict, current: int) -> int:
    """Cliente a cui assegnare il dominio: lui, il principale o un altro sottocliente della famiglia."""
    if "client_id" not in payload or payload.get("client_id") in (None, ""):
        return current
    try:
        tid = int(payload["client_id"])
    except (TypeError, ValueError):
        raise HTTPException(422, "Cliente non valido")
    if tid not in await _family_ids(s, cl):
        raise HTTPException(422, "Cliente non valido")
    return tid


async def _domain_payload(s: AsyncSession, payload: dict, base: ClientDomain | None = None) -> dict:
    raw = payload.get("domain", base.domain if base else "")
    dom = cc.clean_domain(raw)
    if not dom:
        raise HTTPException(422, "Nome di dominio non valido")
    data = {"domain": dom, "active": bool(payload.get("active", base.active if base else True))}
    for f in DOMAIN_EDIT:
        if f in payload or base is None:
            data[f] = str(payload.get(f) or "").strip()[:cc.LIMITS.get(f, 4000)]
    if "hosting_expires" in payload or base is None:
        v = payload.get("hosting_expires")
        data["hosting_expires"] = cc.parse_date(v) if v else None
        if v and data["hosting_expires"] is None:
            raise HTTPException(422, "Data di scadenza non valida")
    if "provider_id" in payload:
        pid = payload.get("provider_id")
        if pid in (None, "", 0, "0"):
            data["provider_id"] = None
        else:
            if not await s.get(ClientProvider, int(pid)):
                raise HTTPException(422, "Provider non trovato")
            data["provider_id"] = int(pid)
    return data


async def _out_one(s: AsyncSession, d: ClientDomain) -> dict:
    prefs = await get_operational_settings()
    providers = {p.id: p for p in (await s.execute(select(ClientProvider))).scalars().all()}
    return domain_out(d, providers, await _site_map(s), int(prefs.get("expiry_warning_days") or 30),
                      int(prefs.get("expiry_critical_days") or 7))


async def _dup(s: AsyncSession, cid: int, dom: str, not_id: int | None = None) -> bool:
    q = select(ClientDomain.id).where(ClientDomain.client_id.in_(await _family_ids(s, await _client(s, cid))),
                                      ClientDomain.domain == dom)
    if not_id:
        q = q.where(ClientDomain.id != not_id)
    return (await s.execute(q)).scalar() is not None


@router.post("/{cid}/domains")
async def create_domain(cid: int, payload: dict = Body(...), s: AsyncSession = Depends(get_session)):
    cl = await _client(s, cid)
    target = await _target_client(s, cl, payload, cid)
    data = await _domain_payload(s, payload)
    if await _dup(s, cid, data["domain"]):
        raise HTTPException(409, "Dominio già presente nella scheda")
    if "provider_id" not in data:
        data["provider_id"] = cc.match_provider(data.get("mail_host", ""), (await s.execute(select(ClientProvider))).scalars().all())
    d = ClientDomain(client_id=target, **data)
    s.add(d)
    await s.commit()
    return await _out_one(s, d)


async def _domain(s: AsyncSession, cid: int, did: int) -> ClientDomain:
    """Dominio del cliente o di un suo sottocliente (la scheda del principale li mostra tutti)."""
    d = await s.get(ClientDomain, did)
    if not d or d.client_id not in await _shown_ids(s, await _client(s, cid)):
        raise HTTPException(404, "Dominio non trovato")
    return d


@router.put("/{cid}/domains/{did}")
async def update_domain(cid: int, did: int, payload: dict = Body(...), s: AsyncSession = Depends(get_session)):
    d = await _domain(s, cid, did)
    data = await _domain_payload(s, payload, d)
    data["client_id"] = await _target_client(s, await _client(s, cid), payload, d.client_id)
    if await _dup(s, cid, data["domain"], did):
        raise HTTPException(409, "Dominio già presente nella scheda")
    if data["domain"] != d.domain:      # altro dominio: la lettura DNS di prima non vale piu'
        d.dns_mx = d.dns_ns = d.dns_a = d.dns_change = d.dns_error = ""
        d.dns_nx, d.dns_checked_at, d.dns_changed_at = False, None, None
    host_changed = "mail_host" in data and data["mail_host"] != d.mail_host
    for k, v in data.items():
        setattr(d, k, v)
    if host_changed and "provider_id" not in payload:
        d.provider_id = cc.match_provider(d.mail_host, (await s.execute(select(ClientProvider))).scalars().all())
    await s.commit()
    return await _out_one(s, d)


@router.delete("/{cid}/domains/{did}")
async def delete_domain(cid: int, did: int, s: AsyncSession = Depends(get_session)):
    d = await _domain(s, cid, did)
    await s.delete(d)
    await s.commit()
    return {"deleted": True}


@router.post("/{cid}/domains/{did}/dns-ack")
async def ack_domain(cid: int, did: int, s: AsyncSession = Depends(get_session)):
    d = await _domain(s, cid, did)
    d.dns_change, d.dns_changed_at = "", None
    await s.commit()
    return {"ok": True}


@router.post("/{cid}/dns-ack")
async def ack_all(cid: int, s: AsyncSession = Depends(get_session)):
    ids = await _shown_ids(s, await _client(s, cid))
    rows = (await s.execute(select(ClientDomain).where(ClientDomain.client_id.in_(ids), ClientDomain.dns_change != ""))).scalars().all()
    for d in rows:
        d.dns_change, d.dns_changed_at = "", None
    await s.commit()
    return {"acked": len(rows)}


@router.post("/{cid}/dns-check")
async def dns_check_client(cid: int, s: AsyncSession = Depends(get_session)):
    await _client(s, cid)
    return await _start_check(cid)


# ------------------------------------------------------------------ domini dai siti di Sentinel
NO_FOLDER = "__none"
NO_FOLDER_NAME = "Siti senza cartella"


def _site_folder(site: Site) -> str:
    """Cartella del sito (la prima, se ne ha piu' d'una): "Clienti" o "Clienti/Agenzia"."""
    tags = [t.strip().strip("/") for t in (site.tags or "").split(",") if t.strip().strip("/")]
    return tags[0] if tags else NO_FOLDER


async def _site_server_labels(sites) -> dict:
    """Sito -> nome del server su cui gira (quello dato in Gestione server, altrimenti l'IP)."""
    from redis.asyncio import from_url
    from ..servers import machine_key, server_of, site_hostname
    out = {}
    try:
        prefs = await get_operational_settings()
        labels, split = prefs.get("server_labels") or {}, set(prefs.get("server_split") or [])
        r = from_url(settings.REDIS_URL)
        try:
            for site in sites:
                ip = await server_of(r, site.url)
                key = machine_key(ip, site_hostname(site), split)
                name = labels.get(key) or labels.get(ip) or ""
                out[site.id] = name or ("" if ip.startswith("host:") else ip)
        finally:
            await r.aclose()
    except Exception:  # noqa: BLE001
        pass
    return out


async def _sites_by_folder(s: AsyncSession) -> dict[str, list]:
    sites = (await s.execute(select(Site).order_by(func.lower(Site.name)))).scalars().all()
    out: dict[str, list] = {}
    for site in sites:
        if cc.site_host(site.url):
            out.setdefault(_site_folder(site), []).append(site)
    return out


@router.get("/site-folders")
async def site_folders(s: AsyncSession = Depends(get_session)):
    """Cartelle di Sentinel con i loro siti: quanti domini sono gia' in Gestione domini."""
    have = set((await s.execute(select(ClientDomain.domain))).scalars().all())
    folders = await _sites_by_folder(s)
    linked = {c.folder.lower(): c.name for c in (await s.execute(select(Client).where(Client.folder != ""))).scalars().all()}
    out = []
    for path in sorted(folders, key=lambda p: (p == NO_FOLDER, p.lower())):
        doms = [cc.site_host(x.url) for x in folders[path]]
        out.append({"folder": path, "name": NO_FOLDER_NAME if path == NO_FOLDER else path.replace("/", " / "),
                    "sites": len(doms), "new": sum(1 for d in doms if d not in have), "linked": linked.get(path.lower(), "")})
    return out


async def _folder_entry(s: AsyncSession, path: str, cache: dict, stats: dict) -> int:
    """Voce di Gestione domini per una cartella: la cartella principale e' un cliente, la
    sottocartella un suo sottocliente. Si riusa la voce gia' collegata alla cartella, o quella
    con lo stesso nome (es. un cliente creato a mano), altrimenti se ne crea una."""
    if path.lower() in cache:
        return cache[path.lower()]
    parts = [p for p in path.split("/") if p] if path != NO_FOLDER else [NO_FOLDER]
    parent_id = await _folder_entry(s, parts[0], cache, stats) if len(parts) > 1 else None
    name = NO_FOLDER_NAME if path == NO_FOLDER else cc.folder_entry_name(path)
    q = select(Client).where(func.lower(Client.folder) == path.lower())
    cl = (await s.execute(q)).scalars().first()
    if cl is None:
        q = select(Client).where(func.lower(Client.name) == name.lower(), Client.folder == "")
        q = q.where(Client.parent_id == parent_id) if parent_id else q.where(Client.parent_id.is_(None))
        cl = (await s.execute(q)).scalars().first()
        if cl is not None:
            cl.folder = path
    if cl is None:
        cl = Client(name=name[:190], emails="", enabled=False, notes="", parent_id=parent_id, folder=path)
        s.add(cl)
        await s.flush()
        stats["entries_new"] += 1
    cache[path.lower()] = cl.id
    return cl.id


async def _sites_into(s: AsyncSession, dest: Client, sites, labels: dict, providers, have: set, stats: dict) -> None:
    """Domini dei siti dentro una voce esistente: un sottocliente per sito (i domini con lo stesso
    nome vanno nello stesso sottocliente, anche uno che c'e' gia')."""
    children = await _children(s, dest.id)
    by_name = {cc.norm(c.name): c.id for c in children}
    by_base: dict[str, int] = {}
    for d in (await s.execute(select(ClientDomain).where(ClientDomain.client_id.in_([c.id for c in children] or [0])))).scalars().all():
        by_base.setdefault(cc.base_name(d.domain), d.client_id)
    for site in sites:
        dom = cc.site_host(site.url)
        if dom in have:
            stats["already"] += 1
            continue
        have.add(dom)
        sub_id = by_base.get(cc.base_name(dom)) or by_name.get(cc.norm(dom))
        if not sub_id:
            sub = Client(name=dom[:190], emails="", enabled=False, notes="", parent_id=dest.id)
            s.add(sub)
            await s.flush()
            sub_id = by_name[cc.norm(dom)] = by_base[cc.base_name(dom)] = sub.id
            stats["subs_new"] += 1
        d = ClientDomain(client_id=sub_id, domain=dom, active=True, site_host=(labels.get(site.id) or "")[:190])
        d.provider_id = cc.match_provider(d.mail_host, providers)
        s.add(d)
        stats["domains_new"] += 1


@router.post("/from-folders")
async def domains_from_folders(payload: dict = Body(...), s: AsyncSession = Depends(get_session)):
    """Aggiunge a Gestione domini i domini dei siti di Sentinel, una voce per cartella.
    I domini gia' presenti (in qualsiasi voce) non si toccano."""
    wanted = {str(x).strip().strip("/").lower() for x in (payload.get("folders") or []) if str(x).strip()}
    if not wanted:
        raise HTTPException(422, "Scegli almeno una cartella")
    # "Metti in": una cartella (con le sue sottocartelle) puo' finire in una voce gia' esistente,
    # per esempio un cliente creato a mano: un sottocliente per sito, e la voce non si collega
    # alla cartella (non cambia nome se la cartella si rinomina)
    targets: dict[str, Client] = {}
    for path, cid in (payload.get("targets") or {}).items():
        if cid in (None, "", 0, "0"):
            continue
        cl = await s.get(Client, int(cid))
        if not cl or cl.parent_id:
            raise HTTPException(422, "Voce di destinazione non valida")
        targets[str(path).strip().strip("/").lower()] = cl
    folders = await _sites_by_folder(s)
    chosen = [p for p in folders if p.lower() in wanted]
    sites = [x for p in chosen for x in folders[p]]
    labels = await _site_server_labels(sites)
    providers = (await s.execute(select(ClientProvider))).scalars().all()
    have = set((await s.execute(select(ClientDomain.domain))).scalars().all())
    stats = {"entries_new": 0, "domains_new": 0, "already": 0}
    cache: dict[str, int] = {}
    stats["subs_new"] = 0
    for path in sorted(chosen, key=str.lower):
        dest = targets.get(path.lower()) or targets.get(path.split("/")[0].lower())
        if dest is not None:
            await _sites_into(s, dest, folders[path], labels, providers, have, stats)
            continue
        target = await _folder_entry(s, path, cache, stats)
        for site in folders[path]:
            dom = cc.site_host(site.url)
            if dom in have:
                stats["already"] += 1
                continue
            have.add(dom)
            d = ClientDomain(client_id=target, domain=dom, active=True, site_host=(labels.get(site.id) or "")[:190])
            d.provider_id = cc.match_provider(d.mail_host, providers)
            s.add(d)
            stats["domains_new"] += 1
    await s.commit()
    return stats


# ------------------------------------------------------------------ import da Excel / CSV
def _squash(text: str) -> str:
    return "".join(ch for ch in cc.norm(text) if ch.isalnum())


async def _placement(s: AsyncSession, cl: Client, parsed: dict, existing: dict, subs: bool, stats: dict) -> dict:
    """Dominio nuovo -> cliente a cui va. Con subs: un sottocliente per gruppo di domini (lo
    stesso se il gruppo ne ha gia' uno, altrimenti nuovo); i domini col nome del cliente stesso
    (es. rossiweb.it per "Rossi Web") restano sul cliente principale."""
    items = parsed["domains"] + parsed["inactive"]
    if not subs or cl.parent_id:
        return {it["domain"]: cl.id for it in items}
    children = await _children(s, cl.id)
    by_name = {cc.norm(c.name): c.id for c in children}
    by_base: dict[str, int] = {}
    for d in existing.values():
        if d.client_id != cl.id:
            by_base.setdefault(cc.base_name(d.domain), d.client_id)
    own = _squash(cl.name)
    out = {}
    for key, g in cc.group_domains(items).items():
        new = [it for it in g["domains"] if it["domain"] not in existing]
        if not new:
            continue
        if key.startswith("d:") and own and key[2:].replace("-", "") == own:
            target = cl.id
        else:
            target = (by_base.get(key[2:]) if key.startswith("d:") else None) or by_name.get(cc.norm(g["name"]))
        if not target:
            sub = Client(name=g["name"][:190], emails="", enabled=False, notes="", parent_id=cl.id)
            s.add(sub)
            await s.flush()
            target = by_name[cc.norm(sub.name)] = sub.id
            stats["subs_new"] += 1
        for it in new:
            out[it["domain"]] = target
    return out


async def apply_import(s: AsyncSession, cid: int, parsed: dict, subs: bool = False) -> dict:
    """Scrive (nella sessione data) domini, provider e note letti dal foglio.
    Un valore vuoto nel foglio non cancella quello gia' scritto nella scheda; i domini gia'
    presenti (anche in un sottocliente) restano dove sono."""
    stats = {"domains_new": 0, "domains_updated": 0, "inactive_new": 0, "inactive_updated": 0,
             "providers_new": 0, "providers_updated": 0, "notes_added": 0, "subs_new": 0}
    providers = (await s.execute(select(ClientProvider))).scalars().all()
    by_key = {cc.provider_key(p.name): p for p in providers}
    for item in parsed["providers"]:
        key = cc.provider_key(item["name"])
        p = by_key.get(key)
        if p is None:
            p = ClientProvider(name=item["name"], **{f: item.get(f, "") for f in cc.PROVIDER_TEXT})
            s.add(p)
            by_key[key] = p
            providers.append(p)
            stats["providers_new"] += 1
        else:
            changed = False
            for f in cc.PROVIDER_TEXT:
                if item.get(f) and item[f] != (getattr(p, f) or ""):
                    setattr(p, f, item[f])
                    changed = True
            stats["providers_updated"] += int(changed)
    await s.flush()
    cl = await s.get(Client, cid)
    family = await _family_ids(s, cl)
    existing = {d.domain: d for d in (await s.execute(select(ClientDomain).where(ClientDomain.client_id.in_(family)))).scalars().all()}
    place = await _placement(s, cl, parsed, existing, subs, stats)
    for item in parsed["domains"] + parsed["inactive"]:
        active = bool(item["active"])
        d = existing.get(item["domain"])
        prefix = "domains" if active else "inactive"
        if d is None:
            d = ClientDomain(client_id=place.get(item["domain"], cid), domain=item["domain"], active=active,
                             **{f: item.get(f, "") for f in cc.DOMAIN_TEXT},
                             hosting_expires=item.get("hosting_expires"))
            d.provider_id = cc.match_provider(d.mail_host, providers)
            s.add(d)
            existing[d.domain] = d
            stats[prefix + "_new"] += 1
            continue
        was_active = d.active
        changed = was_active != active
        d.active = active
        for f in cc.DOMAIN_TEXT:
            v = item.get(f, "")
            if v and v != (getattr(d, f) or ""):
                setattr(d, f, v)
                changed = True
        if active and not was_active and not item.get("status_note") and d.status_note:
            d.status_note = ""          # tornato attivo: la vecchia "situazione" non vale piu'
            changed = True
        if item.get("hosting_expires") and item["hosting_expires"] != d.hosting_expires:
            d.hosting_expires = item["hosting_expires"]
            changed = True
        if d.provider_id is None or changed:
            pid = cc.match_provider(d.mail_host, providers)
            if pid and pid != d.provider_id:
                d.provider_id = pid
                changed = True
        stats[prefix + "_updated"] += int(changed)
    for note in parsed["notes"]:
        if note not in (cl.notes or ""):
            cl.notes = ((cl.notes or "").rstrip() + "\n\n" + note).strip()
            stats["notes_added"] += 1
    await s.flush()
    return stats


@router.post("/{cid}/import")
async def import_sheet(cid: int, file: UploadFile = File(...), dry_run: bool = Form(True), subs: bool = Form(False),
                       s: AsyncSession = Depends(get_session)):
    await _client(s, cid)
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "File troppo grande (massimo 5 MB)")
    try:
        parsed = cc.parse_workbook(data, file.filename or "")
    except ValueError as ex:
        raise HTTPException(422, str(ex))
    if not (parsed["domains"] or parsed["inactive"] or parsed["providers"]):
        raise HTTPException(422, "Nel file non ho trovato fogli con le colonne \"Dominio\" o \"Provider\"")
    stats = await apply_import(s, cid, parsed, subs)
    if dry_run:
        await s.rollback()
    else:
        await s.commit()
    return {"dry_run": dry_run, **stats, "sheets": parsed["sheets"], "skipped": parsed["skipped"][:20],
            "found": {"domains": len(parsed["domains"]), "inactive": len(parsed["inactive"]),
                      "providers": len(parsed["providers"]), "notes": len(parsed["notes"])}}
