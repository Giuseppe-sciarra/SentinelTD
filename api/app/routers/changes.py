"""Impronta dello stato dei siti, per un pannello che si aggiorna da solo senza pesare.

GET /api/changes[?site=ID]: una riga di numeri calcolata con una sola query aggregata
(ultimo controllo, aggiornamenti in sospeso, siti non ok, quanti siti).
Il pannello la chiede ogni pochi secondi con If-None-Match: finche' non cambia niente la
risposta e' un 304 vuoto, e la lista completa viene scaricata solo quando serve davvero.
Con ?site=ID arriva anche l'impronta di quel sito, per rinfrescare la sua pagina solo se e'
cambiato lui.
"""
import json
import hashlib
from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_auth
from ..db import get_session
from ..models import Site

router = APIRouter(prefix="/api/changes", tags=["changes"], dependencies=[Depends(require_auth)])


def _stamp(*parts) -> str:
    out = []
    for p in parts:
        if p is None:
            out.append("-")
        elif hasattr(p, "timestamp"):
            out.append(str(int(p.timestamp() * 1_000_000)))
        else:
            out.append(str(p))
    return ":".join(out)


@router.get("")
async def changes(request: Request, site: int | None = Query(None), s: AsyncSession = Depends(get_session)):
    row = (await s.execute(select(
        func.count(Site.id), func.max(Site.id), func.max(Site.last_checked),
        func.coalesce(func.sum(Site.updates_count), 0),
        func.coalesce(func.sum(case((Site.status != "ok", 1), else_=0)), 0),
        func.max(Site.updates_unverified_at),
        func.coalesce(func.sum(case((Site.enabled == True, 1), else_=0)), 0),  # noqa: E712
        func.max(Site.domain_checked_at), func.max(Site.domain_expires_at),
        func.coalesce(func.sum(case((Site.domain_check_error != "", 1), else_=0)), 0),
    ))).one()
    rev = _stamp(*row)
    site_rev = ""
    if site:
        x = await s.get(Site, site)
        if x:
            site_rev = _stamp(x.last_checked, x.updates_count, x.status, x.updates_unverified_at,
                              x.core_update, x.auto_update, x.enabled, x.notifications_silenced,
                              x.domain_checked_at, x.domain_expires_at, x.domain_check_error)
    etag = '"' + hashlib.sha256(f'{rev}|{site_rev}'.encode()).hexdigest() + '"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-cache"})
    return Response(content=json.dumps({"rev": rev, "site": site_rev}), media_type="application/json",
                    headers={"ETag": etag, "Cache-Control": "no-cache"})
