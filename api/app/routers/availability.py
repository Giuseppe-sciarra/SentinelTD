"""Read-only history from the existing checks: never probes a site or server."""
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_auth
from ..availability import utc
from ..db import get_session
from ..models import OfflineEpisode as E

router = APIRouter(prefix="/api/availability", tags=["availability"], dependencies=[Depends(require_auth)])


def stamp(value):
    return utc(value).isoformat() if value is not None else None


@router.get("")
async def list_episodes(days: int = Query(30, ge=1, le=3650),
                        site_id: int | None = Query(None, ge=1), server: str = Query("", max_length=255),
                        offset: int = Query(0, ge=0, le=1000000), limit: int = Query(50, ge=1, le=100),
                        s: AsyncSession = Depends(get_session)):
    now = datetime.now(timezone.utc)
    period = or_(E.confirmed_at >= now - timedelta(days=days), E.ended_at.is_(None))
    active = func.sum(case((E.ended_at.is_(None), 1), else_=0))
    # Summaries include the whole selected period so choosing a filter never hides
    # the other options. These rows are small: one per site/IP, not one per check.
    grouped = (await s.execute(select(E.site_id, E.server_key, func.max(E.site_name),
        func.count(E.id), active, func.sum(E.failed_checks), func.max(E.last_failed_at))
        .where(period).group_by(E.site_id, E.server_key))).all()
    sites, servers = {}, {}
    for sid, key, name, count, ongoing, checks, last in grouped:
        for table, ident, extra in ((sites, sid, {"site_id": sid, "site_name": name}),
                                    (servers, key, {"server": key})):
            row = table.setdefault(ident, extra | {"episodes": 0, "active": 0, "failed_checks": 0, "last_failed_at": None})
            row["episodes"] += count
            row["active"] += ongoing
            row["failed_checks"] += checks
            date = stamp(last)
            row["last_failed_at"] = max(row["last_failed_at"] or date, date)
    filters = [period]
    if site_id is not None:
        filters.append(E.site_id == site_id)
    if server:
        filters.append(E.server_key == server)
    total, ongoing, checks = (await s.execute(select(func.count(E.id), active, func.sum(E.failed_checks)).where(*filters))).one()
    rows = (await s.execute(select(E).where(*filters).order_by(E.confirmed_at.desc(), E.id.desc())
                          .offset(offset).limit(limit))).scalars().all()
    return {"timezone": "Europe/Rome", "days": days, "as_of": stamp(now), "total": total,
            "active": ongoing or 0, "failed_checks": checks or 0, "offset": offset, "limit": limit,
            "sites": sorted(sites.values(), key=lambda r: (-r["episodes"], r["site_name"].lower())),
            "servers": sorted(servers.values(), key=lambda r: (-r["episodes"], r["server"])),
            "items": [{"id": r.id, "site_id": r.site_id, "site_name": r.site_name,
                       "site_url": r.site_url, "server": r.server_key,
                       "started_at": stamp(r.started_at), "confirmed_at": stamp(r.confirmed_at),
                       "last_failed_at": stamp(r.last_failed_at), "ended_at": stamp(r.ended_at),
                       "failed_checks": r.failed_checks, "reason": r.reason,
                       "duration_seconds": max(0, int(((utc(r.ended_at) if r.ended_at else now) - utc(r.started_at)).total_seconds()))}
                      for r in rows]}
