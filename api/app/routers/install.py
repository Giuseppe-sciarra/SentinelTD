"""
Mass install plugin/tema.

Riceve UNO zip + l'elenco dei siti selezionati e lo installa (e attiva) su ciascuno,
parlando con lo stesso connettore usato per status/update:
 - WP     -> POST {url}/wp-json/tdpanopticon/v1/install   (multipart: package, kind, activate)
 - Joomla -> POST {url}/index.php?option=com_ajax&plugin=tdpanopticon&group=system&format=json
                  &task=install&activate=N                (multipart: package; l'Installer
                  rileva il tipo dal manifest, quindi 'kind' non serve)

Lo zip viene letto UNA volta in memoria e riusato per ogni sito (uso interno, file
nell'ordine dei MB: ok). Ogni sito ha il suo esito, gli errori non bloccano gli altri.
"""
import time
import httpx
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Body, Query
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import Site, Extension
from ..auth import require_auth
from ..connectors import wp_rest_url

router = APIRouter(prefix="/api/install", tags=["install"], dependencies=[Depends(require_auth)])

# install di un singolo sito: puo' durare (download/unzip/copia file). Generoso.
_INSTALL_TIMEOUT = 300.0


def _unwrap_joomla(payload):
    """com_ajax wrappa in {"success":bool,"data":[...]}. Gestisco anche risposte piatte."""
    if isinstance(payload, dict) and "success" in payload:
        if not payload.get("success"):
            return {"ok": False, "error": payload.get("message") or "com_ajax error (token/plugin?)"}
        data = payload.get("data") or []
        return data[0] if data else {"ok": False, "error": "risposta com_ajax vuota"}
    if isinstance(payload, list):
        return payload[0] if payload else {"ok": False, "error": "risposta com_ajax vuota"}
    if isinstance(payload, dict):
        return payload
    return {"ok": False, "error": "formato risposta Joomla non valido"}


async def _install_one(site: Site, content: bytes, filename: str, kind: str, activate: bool) -> dict:
    """Invia lo zip al connettore del sito. Ritorna l'esito normalizzato (non solleva)."""
    headers = {
        "Authorization": f"Bearer {site.token}",
        # copia del token in un header che nessun hosting filtra: alcuni Apache in
        # CGI/FastCGI buttano via Authorization prima di PHP (401 rest_forbidden fisso)
        "X-Sentinel-Token": site.token,
        "Accept": "application/json",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    cb = int(time.time())
    files = {"package": (filename, content, "application/zip")}
    try:
        async with httpx.AsyncClient(timeout=_INSTALL_TIMEOUT, follow_redirects=True) as client:
            if site.cms == "wp":
                r = await client.post(
                    wp_rest_url(site, "install", f"_={cb}"),
                    headers=headers,
                    data={"kind": kind, "activate": "1" if activate else "0"},
                    files=files,
                )
                r.raise_for_status()
                d = r.json()
            else:  # joomla
                params = {
                    "option": "com_ajax", "plugin": "tdpanopticon", "group": "system",
                    "format": "json", "task": "install",
                    "activate": "1" if activate else "0", "_": cb,
                }
                r = await client.post(
                    f"{site.url.rstrip('/')}/index.php",
                    headers=headers, params=params, files=files,
                )
                r.raise_for_status()
                d = _unwrap_joomla(r.json())
        return {
            "ok": bool(d.get("ok")),
            "error": str(d.get("error", "")),
            "name": str(d.get("name", "")),
            "slug": str(d.get("slug", "")),
            "new": str(d.get("new", "")),
            "type": str(d.get("type", "")),
            "activated": bool(d.get("activated", False)),
        }
    except httpx.HTTPStatusError as ex:
        body = ""
        try:
            body = ex.response.text[:200]
        except Exception:  # noqa: BLE001
            pass
        return {"ok": False, "error": f"HTTP {ex.response.status_code} {body}".strip()}
    except httpx.TimeoutException:
        return {"ok": False, "error": "Timeout: il sito ci mette troppo a installare"}
    except Exception as ex:  # noqa: BLE001
        return {"ok": False, "error": str(ex)[:300]}


@router.post("")
async def mass_install(
    package: UploadFile = File(...),
    cms: str = Form(...),                 # 'wp' | 'joomla'
    kind: str = Form("plugin"),           # 'plugin' | 'theme' (usato solo da WP)
    activate: bool = Form(True),
    site_ids: str = Form(...),            # CSV di id: "3,7,12"
    s: AsyncSession = Depends(get_session),
):
    if cms not in ("wp", "joomla"):
        raise HTTPException(422, "cms deve essere 'wp' o 'joomla'")
    if kind not in ("plugin", "theme"):
        raise HTTPException(422, "kind deve essere 'plugin' o 'theme'")

    fname = (package.filename or "package.zip")
    if not fname.lower().endswith(".zip"):
        raise HTTPException(422, "Il pacchetto deve essere un file .zip")

    try:
        ids = [int(x) for x in str(site_ids).split(",") if x.strip()]
    except ValueError:
        raise HTTPException(422, "site_ids non valido")
    if not ids:
        raise HTTPException(422, "Nessun sito selezionato")

    rows = (await s.execute(select(Site).where(Site.id.in_(ids)))).scalars().all()
    # rispetta il cms scelto: ignora eventuali siti dell'altro cms finiti per errore nella lista
    targets = [r for r in rows if r.cms == cms]
    if not targets:
        raise HTTPException(422, f"Nessun sito {cms} tra quelli selezionati")

    content = await package.read()
    if not content:
        raise HTTPException(422, "Pacchetto vuoto")
    return await start_install_job(targets, content, fname, cms, kind, bool(activate))


async def start_install_job(targets: list, content: bytes, fname: str, cms: str, kind: str, activate: bool,
                            label: str = "", per_site: dict | None = None, connector: str = "",
                            target: str = "") -> dict:
    """Avvia un'installazione in blocco in sottofondo: lo zip su disco, un lavoro del worker per
    sito (rispetta i posti sui server), avanzamento leggibile da /api/install/jobs/{id}.
    Usata dall'installazione in blocco e dalla distribuzione del connettore.
    per_site: {id sito: zip o [zip, ...]} per i siti che ricevono pacchetti propri (connettore WP:
    uno per ogni copia, nella sua cartella), installati uno dopo l'altro.
    connector: "wp"/"joomla" se e' la distribuzione del connettore; target: la versione consegnata."""
    import json
    import os
    import uuid
    from arq import create_pool
    from arq.connections import RedisSettings
    from ..config import settings
    from .packages import PACKAGES_DIR

    job = uuid.uuid4().hex[:12]
    jobs_dir = os.path.join(PACKAGES_DIR, "_jobs")
    os.makedirs(jobs_dir, exist_ok=True)
    path = os.path.join(jobs_dir, f"{job}.zip")
    with open(path, "wb") as f:
        f.write(content)
    paths: dict[str, list] = {}
    variants: dict[int, str] = {}           # stesso zip -> stesso file
    for sid, packs in (per_site or {}).items():
        out = []
        for data in (packs if isinstance(packs, list) else [packs]):
            if data is content:
                out.append(path)
                continue
            key = id(data)
            if key not in variants:
                vpath = os.path.join(jobs_dir, f"{job}-{len(variants) + 1}.zip")
                with open(vpath, "wb") as f:
                    f.write(data)
                variants[key] = vpath
            out.append(variants[key])
        paths[str(sid)] = out
    meta = {"job": job, "filename": fname, "cms": cms, "kind": kind, "activate": bool(activate), "path": path,
            "paths": paths, "connector": connector, "target": target,
            "total": len(targets), "site_ids": [t.id for t in targets], "created": int(time.time()), "label": label}
    pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    try:
        await pool.set(f"inst:{job}", json.dumps(meta), ex=86400)
        for i, site in enumerate(targets):
            await pool.hset(f"inst:{job}:res", str(site.id), json.dumps(
                {"site_id": site.id, "site_name": site.name, "url": site.url, "state": "queued", "ok": False, "error": ""}))
            await pool.enqueue_job("install_site", job, site.id, 1, _defer_by=i * 2)
        await pool.expire(f"inst:{job}:res", 86400)
    finally:
        await pool.aclose()
    return {"job": job, "filename": fname, "cms": cms, "kind": kind, "activate": activate, "total": len(targets)}


@router.get("/jobs/{job}")
async def install_job(job: str):
    """Avanzamento di un'installazione in blocco: un esito per sito, e i totali."""
    import json
    import os
    import re
    from redis.asyncio import from_url
    from ..config import settings
    if not re.fullmatch(r"[0-9a-f]{12}", job or ""):
        raise HTTPException(404)
    r = from_url(settings.REDIS_URL)
    try:
        raw = await r.get(f"inst:{job}")
        rows = await r.hgetall(f"inst:{job}:res")
    finally:
        await r.aclose()
    if not raw:
        raise HTTPException(404, "Installazione non trovata o scaduta")
    meta = json.loads(raw)
    results = []
    for v in (rows or {}).values():
        try:
            results.append(json.loads(v))
        except Exception:  # noqa: BLE001
            pass
    results.sort(key=lambda x: (x.get("site_name") or "").lower())
    count = lambda st: sum(1 for x in results if x.get("state") == st)  # noqa: E731
    done = count("done")
    finished = done >= meta["total"]
    if finished:
        for zpath in {meta["path"], *(p for ps in (meta.get("paths") or {}).values() for p in (ps if isinstance(ps, list) else [ps]))}:
            try:
                os.remove(zpath)         # lo zip non serve piu'
            except OSError:
                pass
    return {
        "job": job, "filename": meta["filename"], "cms": meta["cms"], "kind": meta["kind"], "activate": meta["activate"],
        "total": meta["total"], "done": done, "ok": sum(1 for x in results if x.get("state") == "done" and x.get("ok")),
        "failed": sum(1 for x in results if x.get("state") == "done" and not x.get("ok")),
        "retrying": count("retry"), "waiting": count("waiting") + count("queued"), "running": count("running"),
        "finished": finished, "results": results,
    }


async def _uninstall_one(site: Site, ext_type: str, slug: str) -> dict:
    """Chiede al connettore di rimuovere un'estensione. Ritorna l'esito (non solleva)."""
    headers = {
        "Authorization": f"Bearer {site.token}",
        # copia del token in un header che nessun hosting filtra: alcuni Apache in
        # CGI/FastCGI buttano via Authorization prima di PHP (401 rest_forbidden fisso)
        "X-Sentinel-Token": site.token,
        "Accept": "application/json",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    cb = int(time.time())
    try:
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            if site.cms == "wp":
                r = await client.post(
                    wp_rest_url(site, "uninstall", f"_={cb}"),
                    headers={**headers, "Content-Type": "application/json"},
                    json={"type": ext_type, "slug": slug},
                )
                r.raise_for_status()
                d = r.json()
            else:  # joomla
                params = {
                    "option": "com_ajax", "plugin": "tdpanopticon", "group": "system",
                    "format": "json", "task": "uninstall",
                    "extype": ext_type, "slug": slug, "_": cb,
                }
                r = await client.post(f"{site.url.rstrip('/')}/index.php", headers=headers, params=params)
                r.raise_for_status()
                d = _unwrap_joomla(r.json())
        return {
            "ok": bool(d.get("ok")),
            "error": str(d.get("error", "")),
            "name": str(d.get("name", "")),
            "slug": str(d.get("slug", slug)),
            "type": str(d.get("type", ext_type)),
            "removed": bool(d.get("removed", False)),
        }
    except httpx.HTTPStatusError as ex:
        return {"ok": False, "error": f"HTTP {ex.response.status_code}"}
    except httpx.TimeoutException:
        return {"ok": False, "error": "Timeout"}
    except Exception as ex:  # noqa: BLE001
        return {"ok": False, "error": str(ex)[:300]}


@router.post("/remove")
async def mass_uninstall(
    cms: str = Body(...),                 # 'wp' | 'joomla'
    items: list[dict] = Body(...),        # [{"type": "...", "slug": "..."}]
    site_ids: list[int] = Body(...),
    s: AsyncSession = Depends(get_session),
):
    if cms not in ("wp", "joomla"):
        raise HTTPException(422, "cms deve essere 'wp' o 'joomla'")
    if not items:
        raise HTTPException(422, "Nessuna estensione selezionata")
    if not site_ids:
        raise HTTPException(422, "Nessun sito selezionato")

    # normalizza/valida gli item
    clean = []
    for it in items:
        t = str((it or {}).get("type", "")).strip()
        sl = str((it or {}).get("slug", "")).strip()
        if t and sl:
            clean.append({"type": t, "slug": sl})
    if not clean:
        raise HTTPException(422, "Estensioni non valide")

    # i package per primi: rimuovendo il contenitore spariscono i figli (che poi
    # risulteranno "non trovati", innocuo). Evita di fallire sui figli protetti dal package.
    clean.sort(key=lambda it: 0 if it["type"] == "package" else 1)

    rows = (await s.execute(select(Site).where(Site.id.in_(site_ids)))).scalars().all()
    targets = [r for r in rows if r.cms == cms]
    if not targets:
        raise HTTPException(422, f"Nessun sito {cms} tra quelli selezionati")

    # Quali (sito, estensione) esistono DAVVERO secondo l'ultimo check: la rimozione va
    # chiesta solo a chi ce l'ha. Prima si mandava a tutti i siti selezionati, e quelli
    # senza l'estensione rispondevano "non trovata" -> errori certi nel mass uninstall.
    have = set()
    ext_rows = (await s.execute(
        select(Extension.site_id, Extension.type, Extension.slug)
        .where(Extension.site_id.in_([t.id for t in targets]))
    )).all()
    for sid, et, esl in ext_rows:
        have.add((sid, (et or "").lower(), (esl or "").lower()))

    results = []
    for site in targets:
        for it in clean:
            key = (site.id, it["type"].lower(), it["slug"].lower())
            if key not in have:
                # non installata su questo sito: niente chiamata, niente errore
                results.append({
                    "site_id": site.id, "site_name": site.name, "url": site.url,
                    "req_type": it["type"], "req_slug": it["slug"],
                    "ok": True, "skipped": True, "error": "",
                    "message": "non presente su questo sito: saltato",
                })
                continue
            res = await _uninstall_one(site, it["type"], it["slug"])
            results.append({
                "site_id": site.id, "site_name": site.name, "url": site.url,
                "req_type": it["type"], "req_slug": it["slug"],
                **res,
            })

    done = [r for r in results if not r.get("skipped")]
    ok_n = sum(1 for r in done if r["ok"])
    return {
        "cms": cms, "items": clean,
        "total": len(done), "ok": ok_n, "failed": len(done) - ok_n,
        "skipped": len(results) - len(done),
        "results": results,
    }


# slug del connettore stesso, per CMS: da segnalare come protetto nella UI
# Slug del connettore stesso: va protetto dalla rimozione. Su WordPress lo slug e'
# il nome della CARTELLA, e il connettore puo' stare in "td-panopticon" (parco storico)
# oppure in "sentinel-td" (nuove installazioni): vanno protetti entrambi.
_SELF_SLUG = {"wp": {"td-panopticon", "sentinel-td"}, "joomla": {"tdpanopticon"}}


@router.get("/search")
async def search_extensions(
    cms: str = Query(...),
    q: str = Query(...),
    s: AsyncSession = Depends(get_session),
):
    """Cerca tra le estensioni gia' note (ultimo check) dei siti del CMS scelto.
    Aggrega per (type, slug): mostra su quali siti e' presente. Nessuna chiamata ai siti."""
    if cms not in ("wp", "joomla"):
        raise HTTPException(422, "cms deve essere 'wp' o 'joomla'")
    term = q.strip()
    if len(term) < 2:
        raise HTTPException(422, "Inserisci almeno 2 caratteri")

    like = f"%{term}%"
    rows = (await s.execute(
        select(Extension, Site)
        .join(Site, Extension.site_id == Site.id)
        .where(Site.cms == cms, or_(Extension.name.ilike(like), Extension.slug.ilike(like)))
    )).all()

    agg: dict[tuple[str, str], dict] = {}
    for ext, site in rows:
        key = (ext.type, ext.slug)
        if key not in agg:
            agg[key] = {
                "type": ext.type, "slug": ext.slug, "name": ext.name,
                "site_ids": [], "sites": [],
                "protected": (ext.slug in _SELF_SLUG.get(cms, set())),
            }
        agg[key]["site_ids"].append(site.id)
        agg[key]["sites"].append(site.name)
        # dettaglio per sito: versione installata (e se ha un update pendente),
        # cosi' dalla modale si vede QUALI siti e A CHE VERSIONE, non solo quanti
        agg[key].setdefault("site_details", []).append({
            "id": site.id,
            "name": site.name,
            "version": ext.current_version or "",
            "update_available": bool(ext.update_available),
            "new_version": ext.new_version or "",
        })

    def _vkey(v: str):
        return [int(x) if x.isdigit() else x for x in (v or "0").replace("-", ".").split(".")]

    results = sorted(agg.values(), key=lambda r: (r["type"], (r["name"] or "").lower()))
    for r in results:
        r["count"] = len(r["site_ids"])
        r["site_details"].sort(key=lambda d: d["name"].lower())
        # versioni distinte installate (ordinate): 1 sola = parco allineato, >1 = versioni miste
        vers = sorted({d["version"] for d in r["site_details"] if d["version"]}, key=_vkey)
        r["versions"] = vers
    return {"cms": cms, "q": term, "results": results}
