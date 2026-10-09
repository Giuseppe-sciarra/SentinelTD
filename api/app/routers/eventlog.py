"""Registro degli eventi: lettura con filtri (pagina Registro)."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_auth
from ..db import get_session
from ..models import EventLog
from ..eventlog import CATEGORIES

router = APIRouter(prefix="/api/eventlog", tags=["eventlog"], dependencies=[Depends(require_auth)])


def _parse_dt(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


@router.get("")
async def list_events(
    s: AsyncSession = Depends(get_session),
    q: str = Query("", max_length=200),                 # testo: messaggio, sito, dettagli
    category: str = Query("", max_length=32),            # una categoria, vuoto = tutte
    level: str = Query("", max_length=16),               # info|ok|warn|error, vuoto = tutti
    site_id: int | None = None,
    since: str | None = None,                            # ISO; di base ultimi 7 giorni
    until: str | None = None,
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
):
    d_since = _parse_dt(since) or (datetime.now(timezone.utc) - timedelta(days=7))
    d_until = _parse_dt(until)
    w = [EventLog.at >= d_since]
    if d_until:
        w.append(EventLog.at <= d_until)
    if category and category in CATEGORIES:
        w.append(EventLog.category == category)
    if level in ("info", "ok", "warn", "error"):
        w.append(EventLog.level == level)
    if site_id:
        w.append(EventLog.site_id == site_id)
    if q.strip():
        like = f"%{q.strip()}%"
        w.append(or_(EventLog.message.ilike(like), EventLog.site_name.ilike(like), EventLog.details.ilike(like)))
    total = (await s.execute(select(func.count()).select_from(EventLog).where(*w))).scalar_one()
    rows = (await s.execute(select(EventLog).where(*w).order_by(EventLog.at.desc(), EventLog.id.desc()).limit(limit).offset(offset))).scalars().all()
    # conteggi per categoria nello stesso intervallo (per i filtri), senza il filtro categoria
    w_cat = [x for x in w if not (hasattr(x, "left") and getattr(x.left, "name", "") == "category")]
    cats = (await s.execute(select(EventLog.category, func.count()).where(*w_cat).group_by(EventLog.category))).all()
    return {
        "total": total, "since": d_since.isoformat(), "retention_note": "",
        "counts": {c: n for c, n in cats},
        "items": [{"id": r.id, "at": r.at.isoformat() if r.at else None, "category": r.category, "level": r.level,
                   "site_id": r.site_id, "site_name": r.site_name, "message": r.message, "details": r.details} for r in rows],
    }
