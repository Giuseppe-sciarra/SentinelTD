"""Regole condivise dal timer delle anteprime e dai comandi manuali."""
from datetime import datetime, timedelta, timezone


def screenshot_due(shot_at, attempted_at, now: datetime, hours: int) -> bool:
    # Il tentativo resta distinto dall'ultima immagine riuscita: se lo shooter
    # fallisce, si ritenta al prossimo intervallo, senza accodare ogni minuto.
    dates = [d if d.tzinfo else d.replace(tzinfo=timezone.utc)
             for d in (shot_at, attempted_at) if d is not None]
    return not dates or max(dates) <= now - timedelta(hours=hours)


async def enqueue_screenshot(redis, site_id: int, *, delay_seconds: int = 0):
    # Stesso id per timer, pulsante e rigenerazione in blocco: arq deduplica
    # finche' il lavoro e' in coda/in esecuzione. shoot_site non conserva il
    # risultato arq, per poter rigenerare subito dopo anche manualmente.
    return await redis.enqueue_job("shoot_site", site_id,
                                   _job_id=f"shot:{site_id}",
                                   _defer_by=timedelta(seconds=delay_seconds))
