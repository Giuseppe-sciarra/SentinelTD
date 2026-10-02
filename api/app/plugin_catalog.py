"""Catalogo dei plugin del parco: scansione su wordpress.org e classificazione.

Stati:
  closed      chiuso da wordpress.org (sicurezza, abbandono, violazioni): va sostituito
  abandoned   ultimo aggiornamento dell'autore da piu' di 2 anni
  stale       fermo da piu' di 1 anno
  ok          aggiornato nell'ultimo anno
  unknown     non su wordpress.org (prodotti a licenza, plugin privati): niente da dire
  unchecked   non ancora controllato
"""
import asyncio
import html
import logging
import re
from datetime import datetime, timezone

import httpx
from sqlalchemy import select

from .db import SessionLocal
from .models import Extension, PluginCatalog, Site

log = logging.getLogger("sentinel.plugins")
WPORG_INFO_URL = "https://api.wordpress.org/plugins/info/1.2/"
UA = {"User-Agent": "SentinelTD-PluginCatalog/1.0 (+monitoring)"}
ABANDONED_DAYS = 730
STALE_DAYS = 365


def _parse_wporg_date(raw: str) -> datetime | None:
    """'2023-04-05 7:30am GMT' -> datetime; basta la data."""
    m = re.match(r"(\d{4}-\d{2}-\d{2})", str(raw or ""))
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


async def fetch_one(client: httpx.AsyncClient, slug: str) -> dict:
    out = {"slug": slug, "found": False, "closed": False, "closed_date": "", "closed_reason": "", "name": "",
           "last_updated": None, "tested": "", "latest_version": "", "active_installs": 0, "error": ""}
    try:
        r = await client.get(WPORG_INFO_URL, params={"action": "plugin_information", "request[slug]": slug,
                                                    "request[fields][sections]": "0", "request[fields][description]": "0"},
                             headers=UA, timeout=20.0)
    except Exception as ex:  # noqa: BLE001
        out["error"] = str(ex)[:180]
        return out
    # un plugin CHIUSO risponde 404 ma col JSON dentro ("error": "closed", closed_date, reason):
    # si legge il corpo anche sul 404, e solo gli altri codici sono errori veri
    if r.status_code not in (200, 404):
        out["error"] = f"HTTP {r.status_code}"
        return out
    try:
        j = r.json()
    except Exception:  # noqa: BLE001
        out["error"] = "risposta non leggibile"
        return out
    if not isinstance(j, dict):
        return out
    if j.get("error"):
        if "closed" in str(j.get("error")).lower() or j.get("closed"):
            out.update(found=True, closed=True, name=html.unescape(str(j.get("name") or slug)),
                       closed_date=str(j.get("closed_date") or "")[:32],
                       closed_reason=str(j.get("reason_text") or j.get("reason") or "")[:190])
        return out   # "Plugin not found." -> non su wordpress.org
    out.update(found=True, name=html.unescape(str(j.get("name") or slug))[:190], last_updated=_parse_wporg_date(j.get("last_updated")),
               tested=str(j.get("tested") or "")[:32], latest_version=str(j.get("version") or "")[:64],
               active_installs=int(j.get("active_installs") or 0), closed=bool(j.get("closed")))
    return out


async def scan(force: bool = False, max_age_days: int = 6) -> dict:
    """Aggiorna il catalogo per tutti i plugin installati sui siti WordPress. Senza force
    salta le schede controllate negli ultimi max_age_days."""
    now = datetime.now(timezone.utc)
    async with SessionLocal() as s:
        slugs = sorted({e.slug for e in (await s.execute(
            select(Extension).join(Site, Site.id == Extension.site_id).where(Extension.type == "plugin", Site.cms == "wp")
        )).scalars().all() if e.slug})
        rows = {r.slug: r for r in (await s.execute(select(PluginCatalog))).scalars().all()}
    todo = [x for x in slugs if force or x not in rows or rows[x].checked_at is None
            or (now - rows[x].checked_at).days >= max_age_days]
    sem = asyncio.Semaphore(4)
    results: list[dict] = []
    async with httpx.AsyncClient(follow_redirects=True) as client:
        async def one(slug: str):
            async with sem:
                results.append(await fetch_one(client, slug))
        await asyncio.gather(*(one(x) for x in todo))
    async with SessionLocal() as s:
        for res in results:
            row = await s.get(PluginCatalog, res["slug"])
            if row is None:
                row = PluginCatalog(slug=res["slug"])
                s.add(row)
            for k, v in res.items():
                setattr(row, k, v)
            row.checked_at = now
        await s.commit()
    log.info("Catalogo plugin: %d controllati su %d (forzato: %s)", len(results), len(slugs), force)
    return {"checked": len(results), "total": len(slugs)}


def status_of(row: PluginCatalog | None, now: datetime | None = None) -> tuple[str, int | None]:
    """(stato, giorni dall'ultimo aggiornamento dell'autore)."""
    now = now or datetime.now(timezone.utc)
    if row is None or row.checked_at is None:
        return "unchecked", None
    if row.closed:
        return "closed", None
    if not row.found:
        return "unknown", None
    if row.last_updated is None:
        return "unknown", None
    days = (now - row.last_updated).days
    if days >= ABANDONED_DAYS:
        return "abandoned", days
    if days >= STALE_DAYS:
        return "stale", days
    return "ok", days


async def overview() -> dict:
    """Tutti i plugin del parco con stato e siti; i casi da guardare per primi."""
    now = datetime.now(timezone.utc)
    async with SessionLocal() as s:
        exts = (await s.execute(
            select(Extension, Site.name, Site.id).join(Site, Site.id == Extension.site_id)
            .where(Extension.type == "plugin", Site.cms == "wp").order_by(Site.name)
        )).all()
        rows = {r.slug: r for r in (await s.execute(select(PluginCatalog))).scalars().all()}
    # per ogni sito: cartella, server (col nome dato in Impostazioni) e clienti
    from .models import Client, ClientSite
    async with SessionLocal() as s:
        site_rows = {x.id: x for x in (await s.execute(select(Site))).scalars().all()}
        clients: dict[int, list[str]] = {}
        for cs, cname in (await s.execute(select(ClientSite, Client.name).join(Client, Client.id == ClientSite.client_id))).all():
            clients.setdefault(cs.site_id, []).append(cname)
    servers: dict[int, str] = {}
    try:
        from redis.asyncio import from_url
        from .config import settings as _settings
        from .servers import server_of
        from .settings_store import get_operational_settings
        labels = (await get_operational_settings()).get("server_labels") or {}
        r = from_url(_settings.REDIS_URL)
        try:
            for sid in {sid for _e, _n, sid in exts}:
                x = site_rows.get(sid)
                ip = (await server_of(r, x.url)) if x else ""
                servers[sid] = labels.get(ip) or ip or ""
        finally:
            await r.aclose()
    except Exception:  # noqa: BLE001
        pass
    by: dict[str, dict] = {}
    for e, site_name, site_id in exts:
        d = by.setdefault(e.slug, {"slug": e.slug, "name": e.name, "sites": [], "versions": set()})
        x = site_rows.get(site_id)
        tags = [t.strip() for t in ((x.tags if x else "") or "").split(",") if t.strip()]
        d["sites"].append({"id": site_id, "name": site_name, "version": e.current_version or "",
                           "folder": tags[0] if tags else "", "server": servers.get(site_id, ""),
                           "clients": sorted(clients.get(site_id, []))})
        if e.current_version:
            d["versions"].add(e.current_version)
    out = []
    for slug, d in by.items():
        row = rows.get(slug)
        st, days = status_of(row, now)
        out.append({
            "slug": slug, "name": html.unescape(row.name if row and row.name else d["name"]), "sites": d["sites"], "sites_count": len(d["sites"]),
            "versions": sorted(d["versions"]), "status": st, "days_since_update": days,
            "last_updated": row.last_updated.date().isoformat() if row and row.last_updated else "",
            "tested": row.tested if row else "", "latest_version": row.latest_version if row else "",
            "active_installs": row.active_installs if row else 0, "closed_date": row.closed_date if row else "",
            "closed_reason": html.unescape(row.closed_reason) if row else "", "checked_at": row.checked_at.isoformat() if row and row.checked_at else "",
        })
    # chi non e' su wordpress.org (prodotti a licenza, plugin privati) non ha niente da dire qui
    out = [x for x in out if x["status"] != "unknown"]
    order = {"closed": 0, "abandoned": 1, "stale": 2, "unchecked": 3, "ok": 4}
    out.sort(key=lambda x: (order.get(x["status"], 9), -(x["days_since_update"] or 0), -x["sites_count"], x["name"].lower()))
    counts = {k: sum(1 for x in out if x["status"] == k) for k in order}
    last = max((x["checked_at"] for x in out if x["checked_at"]), default="")
    return {"plugins": out, "counts": counts, "total": len(out), "last_scan": last}
