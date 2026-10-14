"""Gestione server: accessi del pannello dell'hosting e FTP di ogni server.

Le password si salvano cifrate e non compaiono mai nell'elenco: "Mostra" ne legge una alla
volta (e lo scrive nel Registro)."""
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_auth
from ..credentials import decrypt, encrypt
from ..db import get_session
from ..eventlog import record as evlog
from ..models import ServerCredential

router = APIRouter(prefix="/api/server-credentials", tags=["server-credentials"], dependencies=[Depends(require_auth)])
PROTOS = ("ftp", "ftps", "sftp")


def _out(c: ServerCredential) -> dict:
    return {"key": c.key, "panel_user": c.panel_user or "", "panel_password": bool(c.panel_password),
            "ftp_proto": c.ftp_proto or "ftp", "ftp_host": c.ftp_host or "", "ftp_port": c.ftp_port,
            "ftp_user": c.ftp_user or "", "ftp_password": bool(c.ftp_password),
            "updated_at": c.updated_at.isoformat() if c.updated_at else None}


@router.get("")
async def list_credentials(s: AsyncSession = Depends(get_session)):
    """Per ogni server: utenti, indirizzo FTP e se c'e' una password salvata (mai la password)."""
    return {c.key: _out(c) for c in (await s.execute(select(ServerCredential))).scalars().all()}


@router.post("/save")
async def save_credentials(payload: dict = Body(...), s: AsyncSession = Depends(get_session)):
    """Salva gli accessi di un server. Una password vuota lascia quella salvata; per toglierla
    si manda clear_panel_password / clear_ftp_password."""
    key = str(payload.get("key") or "").strip()[:200]
    if not key:
        raise HTTPException(422, "Server non indicato")
    c = await s.get(ServerCredential, key)
    if c is None:
        c = ServerCredential(key=key)
        s.add(c)
    for f, n in (("panel_user", 190), ("ftp_host", 255), ("ftp_user", 190)):
        if f in payload:
            setattr(c, f, str(payload.get(f) or "").strip()[:n])
    if "ftp_proto" in payload:
        proto = str(payload.get("ftp_proto") or "ftp").lower()
        c.ftp_proto = proto if proto in PROTOS else "ftp"
    if "ftp_port" in payload:
        raw = str(payload.get("ftp_port") or "").strip()
        if raw and not (raw.isdigit() and 1 <= int(raw) <= 65535):
            raise HTTPException(422, "Porta non valida")
        c.ftp_port = int(raw) if raw else None
    for f in ("panel_password", "ftp_password"):
        if payload.get("clear_" + f):
            setattr(c, f, "")
        elif str(payload.get(f) or ""):
            setattr(c, f, encrypt(str(payload[f])[:500]))
    c.updated_at = datetime.now(timezone.utc)
    empty = not any([c.panel_user, c.panel_password, c.ftp_host, c.ftp_user, c.ftp_password, c.ftp_port])
    if empty:
        await s.delete(c)
    await s.commit()
    return {} if empty else _out(c)


@router.post("/reveal")
async def reveal_password(payload: dict = Body(...), s: AsyncSession = Depends(get_session)):
    key = str(payload.get("key") or "").strip()
    field = str(payload.get("field") or "")
    if field not in ("panel", "ftp"):
        raise HTTPException(422, "Campo non valido")
    c = await s.get(ServerCredential, key)
    token = getattr(c, field + "_password", "") if c else ""
    if not token:
        raise HTTPException(404, "Nessuna password salvata")
    plain = decrypt(token)
    if plain is None:
        raise HTTPException(409, "Password non leggibile: il segreto del pannello (JWT_SECRET) è cambiato. Riscrivila.")
    await evlog(s, "system", "info", f"Password {'del pannello' if field == 'panel' else 'FTP'} visualizzata: server {key}")
    await s.commit()
    return {"password": plain}
