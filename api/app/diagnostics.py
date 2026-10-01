"""Salvataggio della diagnostica dei siti e dello storico del peso."""
import hashlib
import json
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Site, SiteSize

SIZE_KEYS = ("total", "files_total", "uploads", "plugins", "themes", "content_other", "core", "db")
SIZES_RETENTION_DAYS = 730


def _core_signature(core: dict | None) -> str:
    """Impronta dei problemi del core: cambia solo se cambiano i file segnalati."""
    if not core or core.get("status") != "issues":
        return ""
    raw = json.dumps([core.get("version"), core.get("modified"), core.get("missing"), core.get("extra"),
                      core.get("modified_count"), core.get("missing_count"), core.get("extra_count")], sort_keys=True)
    return hashlib.sha1(raw.encode()).hexdigest()


async def store_diagnostics(s: AsyncSession, site: Site, data: dict) -> tuple[dict, bool]:
    """Salva la diagnostica sul sito. Ritorna (diagnostica salvata, problemi del core NUOVI).

    Le parti assenti (es. la prova di spazio, fatta solo a richiesta) restano quelle
    dell'ultima volta, con la loro data: la scheda del sito mostra sempre tutto.
    """
    prev = site.diag or {}
    now = datetime.now(timezone.utc).isoformat()
    new = {k: v for k, v in data.items() if k not in ("space", "sizes", "core")}
    new["at"] = now
    for part in ("space", "sizes", "core"):
        if data.get(part) is not None:
            new[part] = data[part]
            new[f"{part}_at"] = now
        elif prev.get(part) is not None:
            new[part] = prev[part]
            new[f"{part}_at"] = prev.get(f"{part}_at")

    sig = _core_signature(new.get("core")) if data.get("core") is not None else prev.get("core_sig", "")
    new["core_sig"] = sig
    core_changed = bool(sig) and sig != prev.get("core_sig", "") and data.get("core") is not None

    site.diag_json = json.dumps(new, ensure_ascii=False)
    site.diag_at = datetime.now(timezone.utc)

    sizes = data.get("sizes")
    if isinstance(sizes, dict) and sizes.get("total"):
        today = date.today()
        row = (await s.execute(select(SiteSize).where(SiteSize.site_id == site.id, SiteSize.day == today))).scalars().first()
        if row is None:
            row = SiteSize(site_id=site.id, day=today)
            s.add(row)
        for k in SIZE_KEYS:
            setattr(row, k, int(sizes.get(k) or 0))
        row.complete = bool(sizes.get("complete", True))
    return new, core_changed


async def store_diag_error(site: Site, message: str) -> dict:
    """Diagnostica non riuscita: si tiene quella vecchia e si annota l'errore."""
    prev = site.diag or {}
    prev["error"] = message[:500]
    prev["error_at"] = datetime.now(timezone.utc).isoformat()
    site.diag_json = json.dumps(prev, ensure_ascii=False)
    site.diag_at = datetime.now(timezone.utc)
    return prev


def clear_diag_error(diag: dict) -> dict:
    diag.pop("error", None)
    diag.pop("error_at", None)
    return diag


async def prune_sizes(s: AsyncSession) -> None:
    cutoff = date.today() - timedelta(days=SIZES_RETENTION_DAYS)
    await s.execute(delete(SiteSize).where(SiteSize.day < cutoff))


async def sizes_history(s: AsyncSession, site_id: int, days: int = 365) -> list[dict]:
    since = date.today() - timedelta(days=max(1, min(SIZES_RETENTION_DAYS, days)))
    rows = (await s.execute(select(SiteSize).where(SiteSize.site_id == site_id, SiteSize.day >= since)
                            .order_by(SiteSize.day))).scalars().all()
    return [{"day": r.day.isoformat(), **{k: int(getattr(r, k) or 0) for k in SIZE_KEYS}, "complete": bool(r.complete)}
            for r in rows]
