"""Campionamento delle risorse, indipendente dai controlli CMS e dalla diagnostica."""
import asyncio
import logging
import time

from sqlalchemy import select
from sqlalchemy.orm import noload

from .connectors import fetch_status
from .db import SessionLocal
from .load_history import record
from .models import Site
from .servers import server_of, site_hostname, machine_key
from .settings_store import get_operational_settings

log = logging.getLogger("panopticon.worker")


def _attempt_key(server: str) -> str:
    return f"server:metrics:attempt:{server}"


async def server_metrics_tick(ctx):
    prefs = await get_operational_settings()
    minutes = prefs["server_metrics_minutes"]
    redis = ctx["redis"]
    async with SessionLocal() as s:
        sites = (await s.execute(select(Site).options(noload(Site.extensions))
                                .where(Site.enabled == True, Site.token != "")  # noqa: E712
                                .order_by(Site.id))).scalars().all()
    if not sites:
        return
    # DNS con concorrenza limitata; IP e separazione per macchina seguono le
    # stesse regole della pagina Stato server, senza dipendere dalle etichette.
    sem = asyncio.Semaphore(4)
    async def identify(site):
        async with sem:
            ip = await server_of(redis, site.url)
        return machine_key(ip, site_hostname(site), set(prefs["server_split"]))
    keys = await asyncio.gather(*(identify(site) for site in sites))
    groups = {}
    for site, key in zip(sites, keys):
        groups.setdefault(key, []).append(site)
    queued = 0
    now = time.time()
    for key, candidates in groups.items():
        # Un solo controllo per server, non uno per ogni sito ospitato.
        last = await redis.get(_attempt_key(key))
        if last is not None and now - float(last) < minutes * 60:
            continue
        candidates.sort(key=lambda x: (x.status != "ok", not bool(x.connector_version), x.id))
        try:
            job = await redis.enqueue_job("sample_server_metrics", key,
                                          [site.id for site in candidates],
                                          _job_id=f"server-metrics:{key}", _defer_by=queued * 2)
            if job is not None:
                queued += 1
        except Exception:
            log.exception("RISORSE SERVER: accodamento fallito (%s)", key)
    if queued:
        log.info("RISORSE SERVER: accodati %d controlli (ogni %d minuti)", queued, minutes)


async def sample_server_metrics(ctx, server: str, site_ids: list[int]):
    # Segna anche i tentativi falliti: niente raffiche ogni minuto su un server
    # offline. A ogni successivo intervallo si riprova, anche dopo un riavvio.
    await ctx["redis"].set(_attempt_key(server), str(time.time()), ex=86400)
    for sid in site_ids[:3]:  # se il primo sito non risponde, prova altri due dello stesso server
        async with SessionLocal() as s:
            site = await s.get(Site, sid, options=[noload(Site.extensions)])
        if not site or not site.enabled or not site.token:
            continue
        try:
            # Lettura passiva gia' disponibile nei connettori: nessuna scansione
            # di file, scrittura di prova o richiesta ai server di aggiornamento.
            data = await fetch_status(site, timeout=15.0, force=False)
            metrics = data.get("server")
            if not isinstance(metrics, dict) or not await record(site.id, metrics):
                continue
            log.info("RISORSE SERVER OK %s (sito=%s)", server, site.id)
            return
        except Exception as ex:
            log.warning("RISORSE SERVER: sito %s non disponibile: %s", sid, type(ex).__name__)
    log.warning("RISORSE SERVER %s: nessuna misura disponibile; riprova al prossimo intervallo", server)
