"""Dashboard warnings computed from stored site data; no network checks."""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ..auth import require_auth
from ..db import get_session
from ..models import Site
from ..problems import failed_by_site, site_problems

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"], dependencies=[Depends(require_auth)])

@router.get("/problems")
async def problems(s: AsyncSession = Depends(get_session)):
    sites = (await s.execute(select(Site).where(Site.enabled == True).order_by(Site.name))).scalars().all()
    fails = await failed_by_site(s)
    now = datetime.now(timezone.utc)
    out = [{**p, "site": x.name, "site_id": x.id} for x in sites for p in site_problems(x, fails.get(x.id), now)]
    order = {"offline": 0, "space": 1, "failed": 2, "logs": 3, "php": 4, "domain": 5}
    out.sort(key=lambda p: (order.get(p["kind"], 9), p["site"].lower()))
    return out
