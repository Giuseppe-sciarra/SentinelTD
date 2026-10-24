"""Regole condivise dal timer delle anteprime e dai comandi manuali.

Fila delle anteprime (2.49.0). Lo shooter fotografa una pagina alla volta. Prima il pannello gli
mandava un lavoro per sito, fino a 4 insieme e scaglionati di 12 secondi: con video e verifiche
antibot uno scatto dura 30-60 secondi, la fila davanti allo shooter cresceva e le chiamate in
attesa scadevano dopo 2 minuti. L'anteprima non veniva segnata, il tentativo si', e si riprovava
dopo l'intervallo intero: sempre gli stessi siti in fondo alla fila restavano vecchi di giorni, e
"Aggiorna" a mano finiva scartato dietro a un lavoro gia' in coda.

Ora c'e' una fila sola (Redis, ordinata) e un solo esecutore (shot_runner) che fa uno scatto per
volta: nessuna chiamata aspetta lo shooter. Le richieste a mano passano davanti a tutte.
"""
from datetime import datetime, timedelta, timezone
import time

QUEUE = "shots:queue"            # sito -> priorita' (0 = a mano, altrimenti momento della richiesta)
RUNNER_JOB = "shot-runner"
RUNNER_LOCK = "shots:runner"     # un esecutore alla volta
RETRY_FAILED_HOURS = 1           # scatto non riuscito: nuovo tentativo dopo un'ora, non dopo l'intervallo


def screenshot_due(shot_at, attempted_at, now: datetime, hours: int, failed: bool = False) -> bool:
    """E' ora di una nuova anteprima: l'ultimo tentativo (riuscito o no) e' piu' vecchio
    dell'intervallo impostato. Se l'ultimo tentativo e' fallito si riprova dopo un'ora."""
    dates = [d if d.tzinfo else d.replace(tzinfo=timezone.utc)
             for d in (shot_at, attempted_at) if d is not None]
    wait = min(hours, RETRY_FAILED_HOURS) if failed else hours
    return not dates or max(dates) <= now - timedelta(hours=wait)


async def request_shots(redis, site_ids, *, manual: bool = False) -> int:
    """Mette i siti nella fila delle anteprime e avvia l'esecutore. A mano: in testa alla fila
    (anche se il sito c'era gia'). Torna quanti siti sono entrati nella fila."""
    added = 0
    now = time.time()
    for sid in site_ids:
        key = str(int(sid))
        if manual:
            before = await redis.zscore(QUEUE, key)
            await redis.zadd(QUEUE, {key: 0})
            added += 1 if before is None else 0
        else:
            added += int(await redis.zadd(QUEUE, {key: now}, nx=True) or 0)
    await start_runner(redis)
    return added


async def start_runner(redis):
    """Avvia l'esecutore (se gira gia', ci pensa lui: arq non ne accoda un secondo)."""
    if await redis.zcard(QUEUE):
        return await redis.enqueue_job("shot_runner", _job_id=RUNNER_JOB)
    return None


async def in_queue(redis, site_id: int) -> bool:
    return await redis.zscore(QUEUE, str(int(site_id))) is not None


async def enqueue_screenshot(redis, site_id: int, *, delay_seconds: int = 0):
    """Compatibilita': una richiesta per un sito (gli orari li decide la fila)."""
    return await request_shots(redis, [site_id])
