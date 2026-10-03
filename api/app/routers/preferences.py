"""Impostazioni operative esposte alla UI: nessun token/password/SMTP."""
from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_auth
from ..db import get_session
from ..settings_store import get_operational_settings, save_operational_settings

router = APIRouter(prefix="/api/preferences", tags=["preferences"], dependencies=[Depends(require_auth)])


@router.get("")
async def get_preferences():
    return await get_operational_settings()


@router.put("")
async def put_preferences(payload: dict = Body(...)):
    # un salvataggio senza le soglie (pagina che non aveva letto le impostazioni) non diventa
    # "valori di base": il pannello manda sempre l'elenco completo
    if not payload or "domain_alert_days" not in payload:
        raise HTTPException(422, "Impostazioni incomplete: niente salvato. Ricarica la pagina e riprova")
    return await save_operational_settings(payload)


@router.get("/servers")
async def detected_servers(s: AsyncSession = Depends(get_session)):
    """Server su cui girano i siti (IP del dominio): per ognuno quanti siti, in quali cartelle,
    e il nome del server ricavato dall'IP (spesso dice il provider). Serve a scegliere a
    quali server mettere il freno."""
    import asyncio
    from redis.asyncio import from_url
    from sqlalchemy import select
    from ..config import settings
    from ..models import Site
    from ..servers import server_of, site_hostname
    sites = (await s.execute(select(Site).where(Site.enabled == True))).scalars().all()  # noqa: E712
    r = from_url(settings.REDIS_URL)
    loop = asyncio.get_running_loop()

    async def ptr(ip: str) -> str:
        """Nome del server dall'IP (DNS inverso), in memoria per un giorno."""
        if not ip or ip.startswith("host:") or ip == "?":
            return ""
        try:
            cached = await r.get(f"srv:ptr:{ip}")
            if cached is not None:
                v = cached.decode() if isinstance(cached, (bytes, bytearray)) else str(cached)
                return "" if v == "-" else v
        except Exception:  # noqa: BLE001
            pass
        name = ""
        try:
            addr = (ip, 0, 0, 0) if ":" in ip else (ip, 0)
            name = (await asyncio.wait_for(loop.getnameinfo(addr, 0), timeout=3))[0]
        except Exception:  # noqa: BLE001
            name = ""
        if name == ip:
            name = ""
        try:
            await r.set(f"srv:ptr:{ip}", name or "-", ex=86400)
        except Exception:  # noqa: BLE001
            pass
        return name

    try:
        keys = await asyncio.gather(*(server_of(r, x.url) for x in sites))
        groups: dict[str, dict] = {}
        for site, key in zip(sites, keys):
            g = groups.setdefault(key or "?", {"names": [], "folders": {}, "machines": {}, "unknown": 0})
            g["names"].append(site.name)
            hn = site_hostname(site)
            if hn:
                m = g["machines"].setdefault(hn, {"hostname": hn, "sites": 0, "names": []})
                m["sites"] += 1
                m["names"].append(site.name)
            else:
                g["unknown"] += 1
            tags = [" / ".join(p.strip() for p in t.split("/") if p.strip()) for t in (site.tags or "").split(",") if t.strip()]
            for t in (tags or [""]):
                g["folders"][t] = g["folders"].get(t, 0) + 1
        hosts = await asyncio.gather(*(ptr(k) for k in groups))
    finally:
        await r.aclose()
    out = []
    for (k, g), host in zip(groups.items(), hosts):
        out.append({
            "server": k, "host": host, "sites": len(g["names"]),
            "machines": sorted(({**m, "names": sorted(m["names"], key=str.lower)} for m in g["machines"].values()), key=lambda m: (-m["sites"], m["hostname"])),
            "unknown": g["unknown"],
            "names": sorted(g["names"], key=str.lower),
            "folders": [{"name": n, "count": c} for n, c in sorted(g["folders"].items(), key=lambda x: (-x[1], x[0].lower()))],
        })
    return sorted(out, key=lambda x: (-x["sites"], x["server"]))
