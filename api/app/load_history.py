"""Carico e disco dei server nelle ultime 24 ore.

A ogni controllo normale il connettore (WP 2.28+ / Joomla 1.36+) manda carico (1, 5, 15 min),
core e disco: misure istantanee che costano niente. Qui si tengono 24 ore per sito in Redis
(una lista per sito, si pulisce da sola) e si riassumono per server: adesso, media, picco
con l'ora, e l'andamento a mezz'ore. Prima carico e disco erano solo una foto notturna delle
3:40, quando i siti non li visita nessuno: non diceva com'e' il server di giorno.
"""
import json
import time

KEEP_SECONDS = 24 * 3600
MAX_SAMPLES = 1600         # almeno 24 ore anche col timer risorse impostato a 1 minuto
BUCKET_SECONDS = 1800      # fasce fisse ai minuti :00 / :30
HISTORY_BUCKETS = KEEP_SECONDS // BUCKET_SECONDS + 1  # 24 ore, più la fascia corrente

_redis = None


def _r():
    global _redis
    if _redis is None:
        from redis.asyncio import from_url
        from .config import settings
        _redis = from_url(settings.REDIS_URL)
    return _redis


def _key(site_id: int) -> str:
    return f"load:site:{site_id}"


async def record(site_id: int, server: dict) -> bool:
    """Salva una misura. Mai bloccante: se Redis non risponde si perde una misura e basta."""
    load = (server or {}).get("load")
    load = load if isinstance(load, list) else []
    if not load and not server.get("mem_total") and not server.get("disk_total"):
        return False
    try:
        sample = {"t": int(time.time()), "l": [round(float(x), 2) for x in load[:3]],
                  "c": int(server.get("cores") or 0) or None,
                  "dt": server.get("disk_total"), "df": server.get("disk_free")}
        # memoria (RAM) e swap, in byte: connettori WP 2.29+ / Joomla 1.37+
        if server.get("mem_total"):
            sample.update({"mt": server.get("mem_total"), "ma": server.get("mem_available"),
                           "st": server.get("swap_total"), "sf": server.get("swap_free")})
        r = _r()
        await r.lpush(_key(site_id), json.dumps(sample, separators=(",", ":")))
        await r.ltrim(_key(site_id), 0, MAX_SAMPLES - 1)
        await r.expire(_key(site_id), 2 * KEEP_SECONDS)
        return True
    except Exception:  # noqa: BLE001
        return False


async def samples_for(site_ids: list[int]) -> dict[int, list[dict]]:
    """Misure delle ultime 24 ore per ogni sito, dalla piu' recente."""
    out: dict[int, list[dict]] = {}
    if not site_ids:
        return out
    cutoff = time.time() - KEEP_SECONDS
    try:
        r = _r()
        pipe = r.pipeline()
        for sid in site_ids:
            pipe.lrange(_key(sid), 0, MAX_SAMPLES - 1)
        rows = await pipe.execute()
    except Exception:  # noqa: BLE001
        return out
    for sid, raw in zip(site_ids, rows):
        items = []
        for x in raw or []:
            try:
                d = json.loads(x)
            except Exception:  # noqa: BLE001
                continue
            if d.get("t", 0) >= cutoff:
                items.append(d)
        if items:
            out[sid] = items
    return out


def _median(values: list[float]) -> float:
    v = sorted(values)
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def _mem_summary(samples: list[dict], start: int) -> dict | None:
    """Memoria usata nelle 24 ore: la quota occupata dai programmi (totale - disponibile,
    dove "disponibile" conta anche la cache che il sistema libera subito)."""
    ms = [d for d in samples if d.get("mt") and d.get("ma") is not None]
    if not ms:
        return None

    def used(d: dict) -> float:
        return max(0.0, min(100.0, (d["mt"] - d["ma"]) * 100.0 / d["mt"]))

    pcts = [used(d) for d in ms]
    last = ms[-1]
    peak = max(ms, key=used)
    buckets: list[list[float]] = [[] for _ in range(HISTORY_BUCKETS)]
    for d, pct in zip(ms, pcts):
        i = min(len(buckets) - 1, int((d["t"] - start) // BUCKET_SECONDS))
        if 0 <= i < len(buckets):
            buckets[i].append(pct)
    st, sf = last.get("st"), last.get("sf")
    return {
        "total": last["mt"],
        "now": {"t": last["t"], "used_pct": round(used(last), 1), "used": last["mt"] - last["ma"], "avail": last["ma"],
                "swap_total": st, "swap_used": (st - sf) if st is not None and sf is not None else None},
        "usual": round(_median(pcts), 1),
        "peak": {"t": peak["t"], "pct": round(used(peak), 1)},
        "series": [round(sum(b) / len(b), 1) if b else None for b in buckets],
        "samples": len(ms),
    }


def summarize(samples: list[dict]) -> dict | None:
    """Riassunto per un server (misure di tutti i suoi siti: misurano la stessa macchina)."""
    if not samples:
        return None
    samples = sorted(samples, key=lambda d: d["t"])
    cores = max((d.get("c") or 0) for d in samples) or None
    l1 = [d["l"][0] for d in samples if d.get("l")]
    cpu_samples = [d for d in samples if d.get("l")]
    peak = max(cpu_samples, key=lambda d: d["l"][0]) if cpu_samples else samples[-1]
    now_t = int(time.time())
    # Stabilizza gli orari delle caselle. La prima e l'ultima fascia possono essere
    # parziali: samples_for() conserva solo le misure reali delle ultime 24 ore.
    start = now_t // BUCKET_SECONDS * BUCKET_SECONDS - KEEP_SECONDS
    buckets: list[list[float]] = [[] for _ in range(HISTORY_BUCKETS)]
    for d in samples:
        i = min(len(buckets) - 1, int((d["t"] - start) // BUCKET_SECONDS))
        if 0 <= i < len(buckets) and d.get("l"):
            buckets[i].append(d["l"][0])
    series = [round(sum(b) / len(b), 2) if b else None for b in buckets]
    last = samples[-1]
    last_cpu = next((d for d in reversed(samples) if d.get("l")), None)
    disk = next(({"total": d["dt"], "free": d["df"], "t": d["t"]}
                 for d in reversed(samples) if d.get("dt") and d.get("df") is not None), None)
    return {
        "cores": cores,
        "now": {"t": (last_cpu or last)["t"], "load": last_cpu["l"] if last_cpu else []},
        # "di solito": la MEDIANA, non la media. Un'ora di backup notturno al 1000% tirerebbe su
        # la media di tutta la giornata; la mediana dice dove sta il server meta' del tempo
        "usual": (sorted(l1)[len(l1) // 2] if len(l1) % 2 else round((sorted(l1)[len(l1) // 2 - 1] + sorted(l1)[len(l1) // 2]) / 2, 2)) if l1 else None,
        "avg": round(sum(l1) / len(l1), 2) if l1 else None,
        "peak": {"t": peak["t"], "load": peak["l"][0] if peak.get("l") else None},
        "series": series, "series_start": start, "bucket_seconds": BUCKET_SECONDS,
        "samples": len(samples), "first_t": samples[0]["t"],
        "disk": disk,
        "mem": _mem_summary(samples, start),
    }
