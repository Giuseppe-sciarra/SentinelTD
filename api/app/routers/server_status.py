"""Stato server: il quadro di ogni server (o di ogni cartella cliente) con dentro siti, PHP,
spazio, carico, log, problemi, peso con andamento e aggiornamenti degli ultimi mesi."""
import asyncio
import json
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from redis.asyncio import from_url
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_auth
from ..config import settings
from ..db import get_session
from ..models import Extension, Site, SiteSize, UpdateMonthly
from ..servers import server_of
from ..settings_store import get_operational_settings
from ..problems import failed_by_site, server_label, site_problems
from ..servers import key_host, key_ip, machine_key, site_hostname

router = APIRouter(prefix="/api/servers", tags=["servers"], dependencies=[Depends(require_auth)])


def _php_state(v: str) -> str:
    from ..report import _php_state
    return _php_state(v)


async def _ptr(r, ip: str) -> str:
    """Nome del server dall'IP, dalla cache riempita dalle Impostazioni (o una ricerca breve)."""
    if not ip or ip.startswith("host:") or ip == "?":
        return ""
    try:
        cached = await r.get(f"srv:ptr:{ip}")
        if cached is not None:
            v = cached.decode() if isinstance(cached, (bytes, bytearray)) else str(cached)
            return "" if v == "-" else v
        addr = (ip, 0, 0, 0) if ":" in ip else (ip, 0)
        name = (await asyncio.wait_for(asyncio.get_running_loop().getnameinfo(addr, 0), timeout=3))[0]
        name = "" if name == ip else name
        await r.set(f"srv:ptr:{ip}", name or "-", ex=86400)
        return name
    except Exception:  # noqa: BLE001
        return ""


def _periods(n: int = 6) -> list[str]:
    today = date.today().replace(day=1)
    out = []
    y, m = today.year, today.month
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


@router.get("/overview")
async def overview(by: str = Query("server"), s: AsyncSession = Depends(get_session)):
    by = "folder" if by == "folder" else "server"
    now = datetime.now(timezone.utc)
    sites = (await s.execute(select(Site).order_by(Site.name))).scalars().all()
    exts = (await s.execute(select(Extension))).scalars().all()
    since = date.today() - timedelta(days=31)
    sizes = (await s.execute(select(SiteSize).where(SiteSize.day >= since).order_by(SiteSize.day))).scalars().all()
    periods = _periods(6)
    monthly = (await s.execute(select(UpdateMonthly).where(UpdateMonthly.period.in_(periods)))).scalars().all()
    prefs = await get_operational_settings()
    limited = set(prefs.get("server_limited") or [])
    labels = prefs.get("server_labels") or {}
    split = set(prefs.get("server_split") or [])
    fails = await failed_by_site(s)

    # --- per sito: aggiornamenti, fallimenti, blocchi
    pend: dict[int, int] = {}
    failed: dict[int, int] = {}
    locked_sets = {x.id: x.locked_set for x in sites}
    for e in exts:
        if e.update_available and f"{e.type}:{e.slug}" not in locked_sets.get(e.site_id, set()):
            pend[e.site_id] = pend.get(e.site_id, 0) + 1
        if e.update_failed_at is not None:
            failed[e.site_id] = failed.get(e.site_id, 0) + 1
    # --- peso: ultimo valore e serie giornaliera per sito
    last_size: dict[int, int] = {}
    series: dict[int, dict[str, int]] = {}
    for r in sizes:
        last_size[r.site_id] = int(r.total or 0)
        series.setdefault(r.site_id, {})[r.day.isoformat()] = int(r.total or 0)
    # --- aggiornamenti per mese per sito
    upd_month: dict[int, dict[str, list[int]]] = {}
    for m in monthly:
        d = upd_month.setdefault(m.site_id, {}).setdefault(m.period, [0, 0])
        d[0] += int(getattr(m, "ok_count", 0) or 0)
        d[1] += int(getattr(m, "fail_count", 0) or 0)

    # --- chiave del gruppo per ogni sito
    r = from_url(settings.REDIS_URL)
    try:
        site_ip: dict[int, str] = {}
        site_mkey: dict[int, str] = {}
        if by == "server":
            ips = await asyncio.gather(*(server_of(r, x.url) for x in sites))
            ips = [k or "?" for k in ips]
            site_ip = {x.id: ip for x, ip in zip(sites, ips)}
            names = dict(zip(set(ips), await asyncio.gather(*(_ptr(r, k) for k in set(ips)))))
            # server con lo STESSO nome (Impostazioni) = un gruppo solo; senza nome, uno per IP
            # IP diviso per macchina: "IP|nome macchina"; il nome dato in Impostazioni vale per la macchina
            site_mkey = {x.id: machine_key(ip, site_hostname(x), split) for x, ip in zip(sites, ips)}
            keys = [labels.get(site_mkey[x.id]) or site_mkey[x.id] for x in sites]
        else:
            keys = []
            for x in sites:
                tags = [t.strip() for t in (x.tags or "").split(",") if t.strip()]
                keys.append(" / ".join(p.strip() for p in tags[0].split("/") if p.strip()) if tags else "")
            names = {}
    finally:
        await r.aclose()

    groups: dict[str, dict] = {}
    for x, key in zip(sites, keys):
        ip = site_ip.get(x.id, "")
        g = groups.setdefault(key, {"key": key, "host": "", "braked": False, "ips": {}, "sites": [], "php": {},
                                    "folders": {}, "loads": [], "disks": [], "problems": [], "series": {}, "months": {p: [0, 0] for p in periods}})
        if ip:
            g["ips"][ip] = names.get(ip, "")
            g["braked"] = g["braked"] or ip in limited
        diag = x.diag or {}
        sp = diag.get("space") or {}
        logs = (diag.get("sizes") or {}).get("big_logs") or []
        srv = diag.get("server") or {}
        core = diag.get("core") or {}
        php = (x.php_version or "").split("-")[0]
        php_mm = ".".join(php.split(".")[:2]) if php else "?"
        g["php"][php_mm] = g["php"].get(php_mm, 0) + 1
        for t in [t.strip() for t in (x.tags or "").split(",") if t.strip()]:
            g["folders"][t] = g["folders"].get(t, 0) + 1
        if srv.get("load"):
            g["loads"].append({"site": x.name, "load": srv["load"], "cores": srv.get("cores"), "at": x.diag_at.isoformat() if x.diag_at else ""})
        if srv.get("disk_total"):
            g["disks"].append({"total": srv["disk_total"], "free": srv.get("disk_free") or 0})
        row = {
            "id": x.id, "name": x.name, "url": x.url, "cms": x.cms, "enabled": x.enabled, "status": x.status, "error": (x.error or "")[:160],
            "core": x.core_current or "", "core_update": bool(x.core_update), "php": php, "php_state": _php_state(php),
            "pending": pend.get(x.id, 0) + (1 if x.core_update else 0), "failed": failed.get(x.id, 0), "locked": len(locked_sets.get(x.id, set())),
            "auto_update": bool(x.auto_update), "last_checked": x.last_checked.isoformat() if x.last_checked else "",
            "size": last_size.get(x.id), "space_low": bool(sp) and not sp.get("ok"), "space_written_mb": sp.get("written_mb"),
            "big_logs": len(logs), "big_logs_mb": round(sum(int(l.get("bytes", 0)) for l in logs) / 1048576),
            "core_issues": core.get("status") == "issues", "unverified": bool(x.updates_unverified_at),
            "connector": x.connector_version or "", "folders": [t.strip() for t in (x.tags or "").split(",") if t.strip()],
        }
        g["sites"].append(row)
        # versioni del CMS, per il riquadro Siti
        cmsv = ("WordPress " if x.cms == "wp" else "Joomla ") + (x.core_current or "?")
        g.setdefault("cms_versions", {})[cmsv] = g.setdefault("cms_versions", {}).get(cmsv, 0) + 1
        # domini: scaduti e in scadenza entro 30 giorni
        dexp = getattr(x, "domain_expires_at", None)
        if dexp is not None and x.enabled:
            days = (dexp - now).days
            key_d = "domains_expired" if days < 0 else ("domains_soon" if days <= 30 else None)
            if key_d:
                g.setdefault(key_d, []).append({"site": x.name, "site_id": x.id, "days": days})
        if srv.get("software"):
            sw = str(srv["software"]).split("/")[0].split(" ")[0] or str(srv["software"])
            g.setdefault("software", {})[sw] = g.setdefault("software", {}).get(sw, 0) + 1
        if x.connector_version:
            g.setdefault("connectors", {})[x.connector_version] = g.setdefault("connectors", {}).get(x.connector_version, 0) + 1
        for day, tot in series.get(x.id, {}).items():
            g["series"][day] = g["series"].get(day, 0) + tot
        for p, (ok, ko) in upd_month.get(x.id, {}).items():
            if p in g["months"]:
                g["months"][p][0] += ok
                g["months"][p][1] += ko
        # problemi: la stessa definizione di dashboard e report
        for p in site_problems(x, fails.get(x.id), now):
            g["problems"].append({**p, "site": x.name, "site_id": x.id})

    # carico e disco delle ultime 24 ore, dai controlli normali
    from ..load_history import samples_for, summarize
    load_samples = await samples_for([x.id for x in sites])

    out = []
    for key, g in groups.items():
        rows = g["sites"]
        days = sorted(g["series"])
        serie = [{"day": d, "total": g["series"][d]} for d in days]
        total = rows and sum((r["size"] or 0) for r in rows) or 0
        first_total = serie[0]["total"] if serie else 0
        loads = [l["load"][0] for l in g["loads"] if l.get("load")]
        max_load = max(loads) if loads else None
        cores = max((l.get("cores") or 0) for l in g["loads"]) if g["loads"] else None
        disk = max(g["disks"], key=lambda d: d["total"]) if g["disks"] else None
        order = {"offline": 0, "space": 1, "failed": 2, "logs": 3, "php": 4, "domain": 5}
        g["problems"].sort(key=lambda p: (order.get(p["kind"], 9), p["site"].lower()))
        out.append({
            "key": key, "braked": g["braked"], "by": by,
            "label": key if by == "server" and key in set(labels.values()) else "",
            # titolo: il nome dato, altrimenti il nome della macchina (se diviso), altrimenti l'IP
            "title": key if (by == "server" and key in set(labels.values())) else (key_host(key) or key),
            "machine": key_host(key) if (by == "server" and key not in set(labels.values())) else "",
            "named": by == "server" and (key in set(labels.values()) or "|" in key),
            "ips": [{"ip": i, "host": h} for i, h in sorted(g["ips"].items())],
            "host": " · ".join(h for h in g["ips"].values() if h) if len(g["ips"]) == 1 else "",
            "folders": sorted(g["folders"].items(), key=lambda t: (-t[1], t[0].lower())),
            "counts": {"sites": len(rows), "enabled": sum(1 for r in rows if r["enabled"]),
                       "offline": sum(1 for r in rows if r["enabled"] and r["status"] not in ("ok", "dns_error")),
                       "dns": sum(1 for r in rows if r["enabled"] and r["status"] == "dns_error"),
                       "pending": sum(r["pending"] for r in rows), "failed": sum(r["failed"] for r in rows),
                       "space_low": sum(1 for r in rows if r["space_low"]), "big_logs": sum(1 for r in rows if r["big_logs"]),
                       "core_issues": sum(1 for r in rows if r["core_issues"]), "php_eol": sum(1 for r in rows if r["php_state"] == "fuori supporto"),
                       "wp": sum(1 for r in rows if r["cms"] == "wp"), "joomla": sum(1 for r in rows if r["cms"] != "wp")},
            "php": sorted(g["php"].items(), key=lambda t: t[0]),
            "load": {"max": max_load, "cores": cores, "samples": g["loads"][:3]} if loads else None,
            "disk": disk,
            "size": {"total": total, "delta": (total - first_total) if serie else 0, "days": len(serie), "series": serie},
            "months": [{"period": p, "ok": g["months"][p][0], "failed": g["months"][p][1]} for p in periods],
            "problems": g["problems"][:200],
            "problem_counts": {k: len({p["site_id"] for p in g["problems"] if p["kind"] == k}) for k in ("offline", "space", "failed", "logs", "php", "domain", "dns")},
            "sites_with_problems": len({p["site_id"] for p in g["problems"]}),
            "software": sorted((g.get("software") or {}).items(), key=lambda t: -t[1]),
            "connectors": sorted((g.get("connectors") or {}).items(), key=lambda t: t[0], reverse=True),
            "avg_size": (total // len([r for r in rows if r["size"]])) if any(r["size"] for r in rows) else None,
            "cms_versions": sorted((g.get("cms_versions") or {}).items(), key=lambda t: (-t[1], t[0])),
            "auto_update": sum(1 for r in rows if r["auto_update"] and r["enabled"]),
            "locked": sum(r["locked"] for r in rows),
            "largest": [{"id": r["id"], "name": r["name"], "size": r["size"]} for r in sorted((r for r in rows if r["size"]), key=lambda r: -r["size"])[:5]],
            "domains_expired": sorted(g.get("domains_expired") or [], key=lambda d: d["days"]),
            "domains_soon": sorted(g.get("domains_soon") or [], key=lambda d: d["days"]),
            # carico: il campione piu' recente (1, 5, 15 minuti) e il picco visto tra i siti
            "load_now": (max(g["loads"], key=lambda l: l.get("at") or "") if g["loads"] else None),
            "load24": summarize([smp for r in rows for smp in load_samples.get(r["id"], [])]),
            "hostnames": sorted({str(((x2.diag or {}).get("server") or {}).get("hostname") or "") for x2 in sites if x2.id in {r["id"] for r in rows}} - {""}),
            "sites": sorted(rows, key=lambda r: (r["status"] == "ok", -(r["pending"] + r["failed"]), r["name"].lower())),
        })
    out.sort(key=lambda g: (-g["counts"]["sites"], g["key"]))
    return {"by": by, "periods": periods, "groups": out, "generated_at": now.isoformat(),
            "metrics_interval_minutes": prefs["server_metrics_minutes"]}


@router.get("/problems")
async def problems(s: AsyncSession = Depends(get_session)):
    """Tutti i problemi di tutti i siti, per la dashboard: stessa definizione di Stato server e report."""
    sites = (await s.execute(select(Site).where(Site.enabled == True).order_by(Site.name))).scalars().all()  # noqa: E712
    fails = await failed_by_site(s)
    now = datetime.now(timezone.utc)
    out = []
    for x in sites:
        for p in site_problems(x, fails.get(x.id), now):
            out.append({**p, "site": x.name, "site_id": x.id})
    order = {"offline": 0, "space": 1, "failed": 2, "logs": 3, "php": 4, "domain": 5}
    out.sort(key=lambda p: (order.get(p["kind"], 9), p["site"].lower()))
    return out
