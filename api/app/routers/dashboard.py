"""Dashboard warnings computed from stored site data; no network checks."""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ..auth import require_auth
from ..db import get_session
from ..models import Site
from ..problems import failed_by_site, site_problems
from ..i18n import DEFAULT_LANGUAGE, normalize_language, t

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"], dependencies=[Depends(require_auth)])

@router.get("/problems")
async def problems(s: AsyncSession = Depends(get_session), request: Request = None):
    """Avvisi della dashboard, nella lingua del pannello (X-UI-Language)."""
    lang = normalize_language(request.headers.get("x-ui-language") if request is not None else None, DEFAULT_LANGUAGE)
    sites = (await s.execute(select(Site).where(Site.enabled == True).order_by(Site.name))).scalars().all()
    fails = await failed_by_site(s)
    now = datetime.now(timezone.utc)
    out = [{**p, "text": t(p["text"], lang), "site": x.name, "site_id": x.id}
           for x in sites for p in site_problems(x, fails.get(x.id), now)]
    order = {"offline": 0, "space": 1, "failed": 2, "logs": 3, "php": 4, "domain": 5}
    out.sort(key=lambda p: (order.get(p["kind"], 9), p["site"].lower()))
    return out
