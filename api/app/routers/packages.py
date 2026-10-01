"""Pacchetti di plugin e temi caricati in Sentinel.

Servono ai prodotti a licenza (Elementor Pro, ACF Pro, WP Rocket…) quando un sito non
riceve il file di aggiornamento dal produttore, di solito perche' la licenza non e'
attiva o e' scaduta su quel dominio. Carichi lo zip una volta; quando l'aggiornamento
normale non puo' scaricarlo, Sentinel installa questo zip sopra la versione presente —
la stessa cosa che fa WordPress con "Sostituisci la versione installata con quella caricata".

Un solo pacchetto per prodotto: caricarne uno nuovo sostituisce il precedente.
"""
import io
import os
import re
import uuid
import zipfile

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_auth
from ..config import settings
from ..db import get_session
from ..models import Extension, Package, Site

router = APIRouter(prefix="/api/packages", tags=["packages"], dependencies=[Depends(require_auth)])

PACKAGES_DIR = os.getenv("PACKAGES_DIR", "/data/packages")
MAX_SIZE = 150 * 1024 * 1024        # 150 MB: abbondante anche per i temi piu' pesanti


def version_tuple(v: str) -> tuple:
    """'4.3.1' -> (4, 3, 1). Le parti non numeriche contano zero: basta per confrontare."""
    return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.\-+_]", (v or "").strip()) if x != "")


def version_gt(a: str, b: str) -> bool:
    ta, tb = version_tuple(a), version_tuple(b)
    n = max(len(ta), len(tb))
    return ta + (0,) * (n - len(ta)) > tb + (0,) * (n - len(tb))


def _header(text: str, field: str) -> str:
    m = re.search(rf"^[ \t/*#@]*{re.escape(field)}\s*:\s*(.+?)\s*$", text, re.M | re.I)
    return m.group(1).strip() if m else ""


def inspect_zip(data: bytes) -> dict:
    """Riconosce plugin o tema dallo zip, come fa WordPress: una sola cartella principale,
    intestazione 'Plugin Name' in un file PHP di primo livello oppure 'Theme Name' in style.css."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("il file non è uno zip valido")
    names = [n for n in z.namelist() if not n.startswith("__MACOSX/") and not n.endswith(".DS_Store")]
    tops = {n.split("/")[0] for n in names if "/" in n}
    if len(tops) != 1:
        raise ValueError("lo zip deve contenere una sola cartella principale, come quelli scaricati dal produttore")
    slug = tops.pop()

    style = f"{slug}/style.css"
    if style in names:
        head = z.read(style)[:16384].decode("utf-8", "replace")
        if _header(head, "Theme Name"):
            return {"kind": "theme", "slug": slug, "name": _header(head, "Theme Name"), "version": _header(head, "Version")}
    for n in names:
        if n.count("/") == 1 and n.lower().endswith(".php"):
            head = z.read(n)[:16384].decode("utf-8", "replace")
            if _header(head, "Plugin Name"):
                return {"kind": "plugin", "slug": slug, "name": _header(head, "Plugin Name"),
                        "version": _header(head, "Version")}
    raise ValueError("non trovo l'intestazione del plugin o del tema nello zip")


async def _pending_for(s: AsyncSession, pkg: Package) -> list[Extension]:
    """Estensioni dei siti attivi che questo pacchetto aggiornerebbe."""
    rows = (await s.execute(
        select(Extension).join(Site, Site.id == Extension.site_id)
        .where(Extension.slug == pkg.slug, Extension.type == pkg.kind, Site.enabled == True)  # noqa: E712
    )).scalars().all()
    return [e for e in rows if pkg.version and version_gt(pkg.version, e.current_version or "0")]


def _out(pkg: Package, pending: list[Extension]) -> dict:
    return {
        "id": pkg.id, "kind": pkg.kind, "slug": pkg.slug, "name": pkg.name, "version": pkg.version,
        "size": pkg.size, "uploaded_at": pkg.uploaded_at.isoformat() if pkg.uploaded_at else None,
        "pending_sites": len({e.site_id for e in pending}),
    }


@router.get("")
async def list_packages(s: AsyncSession = Depends(get_session)):
    pkgs = (await s.execute(select(Package).order_by(Package.name))).scalars().all()
    return [_out(p, await _pending_for(s, p)) for p in pkgs]


async def _store_package(s: AsyncSession, data: bytes) -> dict:
    """Salva uno zip come pacchetto (caricato a mano o preso da un sito). Uno per prodotto."""
    if not data:
        raise HTTPException(422, "File vuoto")
    if len(data) > MAX_SIZE:
        raise HTTPException(413, "File troppo grande (massimo 150 MB)")
    try:
        meta = inspect_zip(data)
    except ValueError as ex:
        raise HTTPException(422, str(ex))
    if not meta["version"]:
        raise HTTPException(422, "Nello zip manca il numero di versione")

    os.makedirs(PACKAGES_DIR, exist_ok=True)
    # nome del file deciso qui, mai quello del caricamento
    fname = f"{meta['kind']}-{re.sub(r'[^a-zA-Z0-9_.-]', '_', meta['slug'])}-{uuid.uuid4().hex[:8]}.zip"
    tmp = os.path.join(PACKAGES_DIR, fname + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, os.path.join(PACKAGES_DIR, fname))

    old = (await s.execute(select(Package).where(Package.kind == meta["kind"], Package.slug == meta["slug"]))).scalars().all()
    for o in old:
        try:
            os.remove(os.path.join(PACKAGES_DIR, o.filename))
        except OSError:
            pass
        await s.delete(o)
    pkg = Package(kind=meta["kind"], slug=meta["slug"], name=meta["name"][:300],
                  version=meta["version"][:60], filename=fname, size=len(data))
    s.add(pkg)
    await s.flush()

    # i siti che aspettavano questo prodotto "a mano" ripartono al prossimo ciclo
    pending = await _pending_for(s, pkg)
    for e in pending:
        if e.update_manual or e.update_failed_at is not None:
            e.update_failed_at = None
            e.update_failed_version = ""
            e.update_manual = False
    await s.commit()
    await s.refresh(pkg)
    return _out(pkg, pending)


@router.post("")
async def upload_package(file: UploadFile = File(...), s: AsyncSession = Depends(get_session)):
    return await _store_package(s, await file.read())


@router.get("/candidates")
async def harvest_candidates(q: str = "", s: AsyncSession = Depends(get_session)):
    """Plugin e temi che si possono prendere da un sito WordPress: per ognuno la versione
    piu' alta installata nel parco e il sito su cui si trova."""
    q = (q or "").strip().lower()
    if len(q) < 2:
        return []
    rows = (await s.execute(
        select(Extension, Site).join(Site, Site.id == Extension.site_id)
        .where(Site.enabled == True, Site.cms == "wp", Extension.type.in_(("plugin", "theme")))  # noqa: E712
    )).all()
    best: dict[tuple, dict] = {}
    for e, site in rows:
        if q not in (e.name or "").lower() and q not in (e.slug or "").lower():
            continue
        if not e.current_version:
            continue
        key = (e.type, e.slug)
        cur = best.get(key)
        if cur is None or version_gt(e.current_version, cur["version"]):
            best[key] = {"kind": e.type, "slug": e.slug, "name": e.name, "version": e.current_version,
                         "site_id": site.id, "site_name": site.name, "sites": 0}
    for e, site in rows:
        k = (e.type, e.slug)
        if k in best:
            best[k]["sites"] += 1
    return sorted(best.values(), key=lambda x: (x["name"] or "").lower())[:30]


@router.post("/harvest")
async def harvest_package(body: dict, s: AsyncSession = Depends(get_session)):
    """Prende dal sito indicato lo zip del plugin o tema installato e lo salva come pacchetto.
    Serve ai prodotti a licenza: dove l'aggiornamento e' riuscito si prende il file, e lo
    si installa sui siti a cui il produttore non lo consegna."""
    from ..connectors import ConnectorTooOld, fetch_package
    site = await s.get(Site, int(body.get("site_id") or 0))
    kind, slug = str(body.get("kind") or ""), str(body.get("slug") or "")
    if not site or kind not in ("plugin", "theme") or not slug:
        raise HTTPException(422, "Sito, tipo o prodotto non validi")
    try:
        data, _ver = await fetch_package(site, kind, slug, MAX_SIZE)
    except ConnectorTooOld as ex:
        raise HTTPException(409, str(ex))
    except Exception as ex:  # noqa: BLE001
        raise HTTPException(502, f"Pacchetto non ottenuto da {site.name}: {str(ex)[:300]}")
    out = await _store_package(s, data)
    out["from_site"] = site.name
    return out


@router.delete("/{pkg_id}")
async def delete_package(pkg_id: int, s: AsyncSession = Depends(get_session)):
    pkg = await s.get(Package, pkg_id)
    if not pkg:
        raise HTTPException(404, "Pacchetto non trovato")
    try:
        os.remove(os.path.join(PACKAGES_DIR, pkg.filename))
    except OSError:
        pass
    await s.delete(pkg)
    await s.commit()
    return {"deleted": pkg_id}


@router.post("/{pkg_id}/apply")
async def apply_package(pkg_id: int, s: AsyncSession = Depends(get_session)):
    """Avvia subito l'aggiornamento sui siti che hanno una versione piu' vecchia."""
    pkg = await s.get(Package, pkg_id)
    if not pkg:
        raise HTTPException(404, "Pacchetto non trovato")
    pending = await _pending_for(s, pkg)
    site_ids = sorted({e.site_id for e in pending})
    for e in pending:
        e.update_failed_at = None
        e.update_failed_version = ""
        e.update_manual = False
    await s.commit()
    if site_ids:
        pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
        try:
            for i, sid in enumerate(site_ids):
                # richiesta esplicita: si installa anche dove l'aggiornamento automatico e' spento
                await pool.enqueue_job("update_site", sid, True, _defer_by=i * 10)
        finally:
            await pool.close()
    return {"queued": len(site_ids)}
