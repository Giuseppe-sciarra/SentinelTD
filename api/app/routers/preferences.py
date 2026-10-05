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
async def detected_servers(s: AsyncSession = Depends(get_session), by: str = "ip"):
    """Server su cui girano i siti (IP del dominio): per ognuno quanti siti, in quali cartelle,
    e il nome del server ricavato dall'IP (spesso dice il provider). Serve a scegliere a
    quali server mettere il freno."""
    import asyncio
    from redis.asyncio import from_url
    from sqlalchemy import select
    from ..config import settings
    from ..models import Site
    from ..servers import server_of
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
        ips = await asyncio.gather(*(server_of(r, x.url) for x in sites))
        from ..servers import key_host, key_ip, machine_key, site_hostname
        from ..settings_store import get_operational_settings
        # by=ip (Impostazioni): una riga per IP, con le macchine dentro; by=machine (Gestione server):
        # una riga per macchina quando l'IP e' diviso
        split = set((await get_operational_settings()).get("server_split") or []) if by == "machine" else set()
        # macchine viste dietro ogni IP (nome macchina dal controllo normale): se l'IP e' "diviso",
        # ogni macchina e' una riga a parte ("IP|nome macchina")
        machines: dict[str, dict] = {}
        for site, ip in zip(sites, ips):
            m = machines.setdefault(ip or "?", {})
            hn = site_hostname(site)
            if hn:
                m[hn] = m.get(hn, 0) + 1
        keys = [machine_key(ip or "?", site_hostname(x), split) for x, ip in zip(sites, ips)]
        groups: dict[str, dict] = {}
        for site, key in zip(sites, keys):
            g = groups.setdefault(key or "?", {"names": [], "folders": {}, "items": []})
            g["names"].append(site.name)
            g["items"].append({"id": site.id, "name": site.name, "url": site.url, "cms": site.cms, "status": site.status,
                               "folder": " / ".join(p.strip() for p in (site.tags or "").split(",")[0].split("/") if p.strip()) if (site.tags or "").strip() else ""})
            tags = [" / ".join(p.strip() for p in t.split("/") if p.strip()) for t in (site.tags or "").split(",") if t.strip()]
            for t in (tags or [""]):
                g["folders"][t] = g["folders"].get(t, 0) + 1
        hosts = await asyncio.gather(*(ptr(key_ip(k)) for k in groups))
    finally:
        await r.aclose()
    labels = (await get_operational_settings()).get("server_labels") or {}

    def _row_title(key: str, labels: dict, host: str) -> str:
        ip, machine = key_ip(key), key_host(key)
        if labels.get(key):
            return labels[key]
        if machine:
            return f"{labels[ip]} · {machine}" if labels.get(ip) else machine
        return labels.get(ip) or host or ip

    out = []
    for (k, g), host in zip(groups.items(), hosts):
        out.append({
            "server": k, "ip": key_ip(k), "machine": key_host(k), "host": host, "sites": len(g["names"]),
            # titolo come nella versione con lo Stato server: il nome dato alla macchina; altrimenti il nome
            # dato all'IP, ereditato dalle macchine ("Tastiere · Hosting-Websites-WP"); altrimenti nome
            # macchina, nome tecnico dell'IP o l'IP stesso
            "title": _row_title(k, labels, host),
            # macchine diverse viste dietro l'IP e quanti siti ancora senza nome macchina: la riga mostra
            # "Dividi per macchina" quando sono almeno due
            "machines": sorted(({"hostname": h, "sites": n} for h, n in machines.get(key_ip(k), {}).items()), key=lambda m: (-m["sites"], m["hostname"])),
            "unknown": sum(1 for x, ip in zip(sites, ips) if (ip or "?") == key_ip(k) and not site_hostname(x)),
            "names": sorted(g["names"], key=str.lower),
            "items": sorted(g["items"], key=lambda x: x["name"].lower()),
            "folders": [{"name": n, "count": c} for n, c in sorted(g["folders"].items(), key=lambda x: (-x[1], x[0].lower()))],
        })
    return sorted(out, key=lambda x: (-x["sites"], x["server"]))
