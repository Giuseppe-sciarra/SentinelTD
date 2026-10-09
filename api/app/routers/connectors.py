"""
Archivio connettori (Joomla / WP).

Due slot fissi: 'joomla' (plg_system_tdpanopticon) e 'wp' (td-panopticon). Upload di uno
zip per slot, download quando serve (es. nuovo sito da agganciare o redeploy dopo un
aggiornamento del connettore). La versione viene estratta automaticamente dallo zip:
 - joomla -> tdpanopticon.xml, tag <version>
 - wp     -> td-panopticon.php, header "Version: x.y.z"

Storage su /data/connectors (volume docker). Metadati in un .json accanto allo zip.
Upload autenticato con la sessione admin; download con il token immagini in query
(stesso meccanismo degli screenshot: il browser deve poter scaricare con un link diretto).
"""
import io
import json
import os
import re
import time
import zipfile

from fastapi import APIRouter, Body, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import Response

from ..auth import require_auth

router = APIRouter(prefix="/api/connectors", tags=["connectors"])

CONN_DIR = "/data/connectors"
# Sorgenti inclusi nell'immagine (COPY connectors ./connectors nel Dockerfile): il pannello
# ne costruisce il pacchetto installabile al volo, cosi' non serve zippare o caricare nulla.
SRC_DIR = os.getenv("CONNECTOR_SRC_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "connectors"))
SRC_SUB = {"wp": os.path.join("wordpress", "td-panopticon"), "joomla": os.path.join("joomla", "plg_system_tdpanopticon")}
KINDS = ("joomla", "wp")
MAX_SIZE = 20 * 1024 * 1024   # 20 MB: i connettori sono nell'ordine dei KB, largo comunque


def _paths(kind: str):
    return os.path.join(CONN_DIR, f"{kind}.zip"), os.path.join(CONN_DIR, f"{kind}.json")


def _src_dir(kind: str) -> str:
    return os.path.join(SRC_DIR, SRC_SUB.get(kind, ""))


def _source_version(kind: str) -> str:
    """Versione letta dai sorgenti inclusi ('' se i sorgenti non ci sono)."""
    base = _src_dir(kind)
    try:
        if kind == "wp":
            main = os.path.join(base, "td-panopticon.php")
            m = re.search(r"^\s*\*\s*Version:\s*([\w.\-]+)", open(main, encoding="utf-8").read(), re.M)
            return m.group(1) if m else ""
        m = re.search(r"<version>([\w.\-]+)</version>", open(os.path.join(base, "tdpanopticon.xml"), encoding="utf-8").read())
        return m.group(1) if m else ""
    except Exception:  # noqa: BLE001
        return ""


def _package_name(kind: str, version: str) -> str:
    tag = "wp" if kind == "wp" else "jm"
    return f"sentinel-td-{tag}-{version or 'dev'}.zip"


def _build_from_sources(kind: str) -> bytes:
    """Zip installabile costruito dai sorgenti inclusi.

    WordPress: cartella 'td-panopticon/' dentro lo zip (WP installa la cartella).
    Joomla: manifest e cartelle alla radice dello zip (com'e' richiesto dall'installer).
    """
    base = _src_dir(kind)
    if not os.path.isdir(base):
        raise HTTPException(404, "Sorgenti del connettore non disponibili in questa installazione")
    out = io.BytesIO()
    prefix = "td-panopticon/" if kind == "wp" else ""
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in ("__MACOSX", "__pycache__")]
            for f in sorted(files):
                if f == ".DS_Store":
                    continue
                full = os.path.join(root, f)
                arc = prefix + os.path.relpath(full, base).replace(os.sep, "/")
                z.write(full, arc)
    return out.getvalue()


def _extract_version(kind: str, content: bytes) -> str:
    """Versione dal contenuto dello zip. '' se non trovata (upload comunque accettato)."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
        names = zf.namelist()
        if kind == "joomla":
            # manifest del plugin: tdpanopticon.xml (a root o in una sottocartella)
            for n in names:
                if os.path.basename(n).lower() == "tdpanopticon.xml":
                    m = re.search(r"<version>\s*([0-9][0-9.]*)\s*</version>", zf.read(n).decode("utf-8", "ignore"))
                    if m:
                        return m.group(1)
        else:
            # header del plugin WP: td-panopticon.php
            for n in names:
                if os.path.basename(n).lower() == "td-panopticon.php":
                    m = re.search(r"Version:\s*([0-9][0-9.]*)", zf.read(n).decode("utf-8", "ignore"))
                    if m:
                        return m.group(1)
    except Exception:  # noqa: BLE001
        pass
    return ""


def _meta(kind: str) -> dict:
    zpath, jpath = _paths(kind)
    if not os.path.isfile(zpath):
        v = _source_version(kind)
        if v:
            return {"kind": kind, "present": True, "builtin": True, "version": v,
                    "filename": _package_name(kind, v), "size": 0, "mtime": 0}
        return {"kind": kind, "present": False, "builtin": False}
    out = {"kind": kind, "present": True, "builtin": False, "size": os.path.getsize(zpath),
           "mtime": int(os.path.getmtime(zpath)), "version": "", "filename": f"{kind}.zip"}
    try:
        with open(jpath, encoding="utf-8") as f:
            saved = json.load(f)
        out["version"] = saved.get("version", "")
        out["filename"] = saved.get("filename", out["filename"])
    except Exception:  # noqa: BLE001
        pass
    return out


@router.get("", dependencies=[Depends(require_auth)])
async def list_connectors():
    return [_meta(k) for k in KINDS]


@router.get("/regkey", dependencies=[Depends(require_auth)])
async def show_register_key():
    """Chiave di registrazione per l'auto-collegamento dei connettori (generata al primo uso)."""
    from ..db import SessionLocal
    from .agent import get_register_key
    async with SessionLocal() as s:
        return {"key": await get_register_key(s)}


@router.post("/regkey/rotate", dependencies=[Depends(require_auth)])
async def rotate_register_key_ep():
    """Rigenera la chiave: i connettori non ancora collegati dovranno usare quella nuova."""
    from ..db import SessionLocal
    from .agent import rotate_register_key
    async with SessionLocal() as s:
        return {"key": await rotate_register_key(s)}


# ---------------------------------------------------------------- pacchetto personalizzato
HUB_URL_KEY = "connector:hub_url"
_PHP_URL_RE = re.compile(rb"const TDPANOP_HUB_URL = '[^']*';")
_PHP_KEY_RE = re.compile(rb"const TDPANOP_HUB_KEY = '[^']*';")
_PHP_TOKEN_RE = re.compile(rb"const TDPANOP_TOKEN = '[^']*';")
# Il blocco che registra la pagina Impostazioni → Sentinel TD. Nella build headless si
# toglie SOLO questo: nessuna voce di menu. Tutto il resto del connettore resta identico,
# e il file resta visibile nella tab Must-Use con la sua intestazione.
_PHP_ADMIN_MENU_RE = re.compile(
    rb"add_action\('admin_menu', function \(\) \{\n[^\n]*add_options_page\([^\n]*\n\}\);\n")


async def _hub_url() -> str:
    """Indirizzo pubblico di questo Sentinel, impostato in Connettori."""
    from ..db import SessionLocal
    from ..models import AppSetting
    async with SessionLocal() as s:
        row = await s.get(AppSetting, HUB_URL_KEY)
        return (row.value if row and row.value else "").strip().rstrip("/")


def _php_quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _personalize_wp(content: bytes, hub_url: str, hub_key: str) -> bytes:
    """Scrive indirizzo e chiave nelle costanti del connettore WordPress.

    Il sorgente pubblicato e' neutro (costanti vuote) e chiunque puo' compilarle dal
    backend del sito; qui prepariamo la copia gia' configurata per QUESTO pannello,
    cosi' i siti nuovi si collegano da soli senza incollare nulla.
    """
    hub_url = (hub_url or "").strip().rstrip("/")
    src = io.BytesIO(content)
    out = io.BytesIO()
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename.endswith(".php") and _PHP_URL_RE.search(data):
                data = _PHP_URL_RE.sub(("const TDPANOP_HUB_URL = '%s';" % _php_quote(hub_url)).encode(), data, count=1)
                data = _PHP_KEY_RE.sub(("const TDPANOP_HUB_KEY = '%s';" % _php_quote(hub_key)).encode(), data, count=1)
            zout.writestr(item, data)
    return out.getvalue()


def _wp_main_php_from_zip() -> bytes | None:
    zpath, _ = _paths("wp")
    if os.path.isfile(zpath):
        with zipfile.ZipFile(zpath) as zf:
            for n in zf.namelist():
                if os.path.basename(n).lower() == "td-panopticon.php":
                    return zf.read(n)
    return None


def _wp_main_php_from_src() -> bytes | None:
    main = os.path.join(_src_dir("wp"), "td-panopticon.php")
    if os.path.isfile(main):
        with open(main, "rb") as f:
            return f.read()
    return None


def _wp_main_php() -> bytes:
    """Il file principale del connettore WP (td-panopticon.php) per la build headless.

    Serve una versione che DEFINISCE e USA la costante TDPANOP_TOKEN (connettore 2.35.0+):
    senza, il token cablato verrebbe ignorato dal PHP sul sito. Si preferisce lo zip caricato
    se ce l'ha, altrimenti i sorgenti inclusi; se nessuno dei due ha la feature, meglio un
    errore chiaro che un mu-plugin che non autentica."""
    for data in (_wp_main_php_from_zip(), _wp_main_php_from_src()):
        if data and _PHP_TOKEN_RE.search(data):
            return data
    raise HTTPException(422, "Il connettore WordPress caricato è precedente alla 2.35.0 "
                             "(manca il supporto al token cablato): aggiornalo per usare il mu-plugin headless.")


async def wp_mu_headless(token: str) -> bytes:
    """Build mu-plugin HEADLESS per UN sito gia' collegato: il connettore WP con il token di
    quel sito gia' cablato (costante TDPANOP_TOKEN), hub url/chiave compilati, e la voce di
    menu Impostazioni → Sentinel TD rimossa. Un solo .php da mettere in wp-content/mu-plugins/:
    si attiva da solo (niente pulsante Disattiva), resta visibile nella tab Must-Use con la
    sua intestazione, ma non aggiunge nessuna pagina di amministrazione."""
    data = _wp_main_php()
    hub = await _hub_url()
    if hub:
        from ..db import SessionLocal
        from .agent import get_register_key
        async with SessionLocal() as s:
            key = await get_register_key(s)
        # repl a funzione: la stringa escapizzata (puo' contenere backslash) NON deve essere
        # re-interpretata da re.sub come sequenza di sostituzione.
        _url = ("const TDPANOP_HUB_URL = '%s';" % _php_quote(hub)).encode()
        _key = ("const TDPANOP_HUB_KEY = '%s';" % _php_quote(key or "")).encode()
        data = _PHP_URL_RE.sub(lambda m: _url, data, count=1)
        data = _PHP_KEY_RE.sub(lambda m: _key, data, count=1)
    _tok = ("const TDPANOP_TOKEN = '%s';" % _php_quote(token or "")).encode()
    data = _PHP_TOKEN_RE.sub(lambda m: _tok, data, count=1)
    data, n = _PHP_ADMIN_MENU_RE.subn(b"", data, count=1)
    if n == 0:
        # il sorgente non contiene piu' il blocco atteso: meglio fermarsi che consegnare un
        # file con ancora la voce di menu, cosi' non si scopre il problema sul sito.
        raise HTTPException(500, "Connettore non compatibile con la build headless (blocco menu non trovato)")
    return data


@router.get("/hub-url", dependencies=[Depends(require_auth)])
async def get_hub_url():
    return {"url": await _hub_url()}


@router.put("/hub-url", dependencies=[Depends(require_auth)])
async def set_hub_url(payload: dict = Body(...)):
    from ..db import SessionLocal
    from ..models import AppSetting
    url = str(payload.get("url") or "").strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        raise HTTPException(422, "L'indirizzo deve iniziare con http:// o https://")
    if len(url) > 300:
        raise HTTPException(422, "Indirizzo troppo lungo")
    async with SessionLocal() as s:
        row = await s.get(AppSetting, HUB_URL_KEY)
        if row:
            row.value = url
        else:
            s.add(AppSetting(key=HUB_URL_KEY, value=url))
        await s.commit()
    return {"url": url}


@router.post("/{kind}", dependencies=[Depends(require_auth)])
async def upload_connector(kind: str, package: UploadFile = File(...)):
    if kind not in KINDS:
        raise HTTPException(400, "kind deve essere 'joomla' o 'wp'")
    content = await package.read()
    if not content:
        raise HTTPException(400, "File vuoto")
    if len(content) > MAX_SIZE:
        raise HTTPException(400, "File troppo grande")
    if not zipfile.is_zipfile(io.BytesIO(content)):
        raise HTTPException(400, "Il file non è uno zip valido")

    version = _extract_version(kind, content)
    os.makedirs(CONN_DIR, exist_ok=True)
    zpath, jpath = _paths(kind)
    with open(zpath, "wb") as f:
        f.write(content)
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump({"version": version, "filename": package.filename or f"{kind}.zip",
                   "uploaded_at": int(time.time())}, f)
    return _meta(kind)


async def connector_package(kind: str, plain: bool = False) -> tuple[bytes, str]:
    """Lo zip del connettore pronto per i siti: quello caricato o costruito dai sorgenti, e per
    WordPress gia' personalizzato (indirizzo del pannello + chiave di registrazione)."""
    meta = _meta(kind)
    name = meta.get("filename") or f"{kind}.zip"
    zpath, _ = _paths(kind)
    if os.path.isfile(zpath):
        with open(zpath, "rb") as f:
            content = f.read()
    else:
        content = _build_from_sources(kind)     # nessun caricamento: costruisci dai sorgenti
    if kind == "wp" and not plain:
        hub = await _hub_url()
        if hub:
            from ..db import SessionLocal
            from .agent import get_register_key
            async with SessionLocal() as s:
                key = await get_register_key(s)
            content = _personalize_wp(content, hub, key or "")
    return content, name


def shipped_version(kind: str) -> str:
    """Versione del connettore che il pannello consegna (zip caricato o sorgenti inclusi)."""
    meta = _meta(kind)
    zpath, _ = _paths(kind)
    if os.path.isfile(zpath) and meta.get("version"):
        return str(meta["version"])
    return _source_version(kind)


def wp_folder(site) -> str:
    """Cartella in cui gira il connettore WordPress del sito, '' se non e' una copia nei plugin
    (mu-plugin, altro posto, o non ancora noto)."""
    mode = str(getattr(site, "connector_mode", "") or "")
    return mode.split(":", 1)[1] if mode.startswith("plugin:") else ""


_ZIP_ROOT_RE = re.compile(r"^([^/]+)/td-panopticon\.php$")
_SAFE_FOLDER_RE = re.compile(r"[A-Za-z0-9._-]{1,80}")


def _good_folder(folder: str) -> bool:
    return bool(_SAFE_FOLDER_RE.fullmatch(folder or "")) and folder not in (".", "..")


async def _wp_copies(s, site_ids: list) -> dict:
    """Copie del connettore nei plugin di ogni sito, dall'ultimo controllo: {id: [(cartella, versione)]}.
    I mu-plugin non ci sono (WordPress non li elenca tra i plugin)."""
    from sqlalchemy import select as _select, or_ as _or
    from ..models import Extension
    from ..connectors import SELF_WP_FOLDERS
    out: dict = {}
    if not site_ids:
        return out
    rows = (await s.execute(_select(Extension).where(
        Extension.site_id.in_(site_ids), Extension.type == "plugin",
        _or(Extension.slug.in_(SELF_WP_FOLDERS), Extension.name == "Sentinel TD Agent")))).scalars().all()
    for e in rows:
        out.setdefault(e.site_id, []).append((e.slug or "", (e.current_version or "").strip()))
    return out


async def _rollout_split(kind: str, s) -> tuple[list, list]:
    """(da aggiornare, lasciati stare) per il connettore consegnato.

    Joomla: come sempre, i siti col connettore piu' vecchio (o senza versione dichiarata).

    WordPress: si aggiornano SOLO copie del connettore che ci sono gia' nei plugin, ciascuna nella
    sua cartella, senza attivarle: mai una copia nuova. La copia in uso diventa la versione nuova;
    una copia vecchia in piu' (rimasta da prima) diventa la versione nuova anche lei, che quando
    trova un'altra copia gia' caricata esce in silenzio, senza avvisi in amministrazione.
    Il mu-plugin non si tocca. Un sito riceve una sola installazione per versione consegnata
    (connector_hold): se dopo la versione non cambia, non si riprova ogni notte.
    Sul sito, ogni copia da aggiornare finisce in x._rollout_folders (la copia in uso per prima)."""
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import select as _select
    from ..models import Site
    from .packages import version_gt
    target = shipped_version(kind)
    cms = "wp" if kind == "wp" else "joomla"
    rows = (await s.execute(_select(Site).where(Site.cms == cms, Site.enabled == True).order_by(Site.name))).scalars().all()  # noqa: E712
    older = lambda v: (not v) or version_gt(target, v)   # noqa: E731
    fresh_after = datetime.now(timezone.utc) - timedelta(hours=26)

    def fresh(x) -> bool:
        """Elenco dei plugin affidabile: sito a posto e controllato da poco. Con un elenco vecchio
        si rischierebbe di ricreare una copia cancellata nel frattempo."""
        last = x.last_checked
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return x.status == "ok" and last is not None and last >= fresh_after
    if kind != "wp":
        go = [x for x in rows if older(x.connector_version) and (x.connector_hold or "") != target]
        keep = [x for x in rows if older(x.connector_version) and (x.connector_hold or "") == target]
        for x in keep:
            x._keep_why = "già installato"
        return go, keep
    copies = await _wp_copies(s, [x.id for x in rows])
    go, keep = [], []
    for x in rows:
        in_use = wp_folder(x)
        mode = x.connector_mode or ""
        folders = sorted({f for f, v in copies.get(x.id, []) if _good_folder(f) and older(v)},
                         key=lambda f: (f != in_use, f))
        if not (folders or older(x.connector_version)):
            continue                                        # tutto alla versione consegnata
        if (x.connector_hold or "") == target or not folders or not fresh(x):
            x._keep_why = ("mu-plugin" if mode == "mu" else
                           "già installato" if (x.connector_hold or "") == target else
                           "fuori dai plugin" if mode == "other" else
                           "da ricontrollare" if folders else "non ancora controllato")
            keep.append(x)
        else:
            x._rollout_folders = folders
            go.append(x)
    return go, keep


async def outdated_sites(kind: str, s) -> list:
    """Siti su cui il giro installa il connettore consegnato (vedi _rollout_split)."""
    return (await _rollout_split(kind, s))[0]


def _keep_reason(site) -> str:
    return getattr(site, "_keep_why", "") or "non ancora controllato"


# cosa fare, per ogni motivo di esclusione (mostrato nell'elenco "esclusi dal giro")
KEEP_HINTS = {
    "mu-plugin": "aggiornalo a mano: Connettore mu-plugin nella pagina del sito, poi sostituisci il file in wp-content/mu-plugins",
    "già installato": "installato, ma sul sito gira ancora un'altra copia: guarda in wp-content/mu-plugins",
    "fuori dai plugin": "il connettore non sta nella cartella dei plugin: va sistemato a mano",
    "da ricontrollare": "nessun controllo riuscito nelle ultime 26 ore: entra nel giro appena il sito risponde",
    "non ancora controllato": "Sentinel non ha ancora letto la versione del connettore: fai Check ora sul sito",
}


def wp_package_for_folder(content: bytes, folder: str) -> bytes:
    """Lo zip del connettore con la cartella radice rinominata in "folder".

    WordPress installa la cartella dello zip: se sul sito il connettore sta in "sentinel-td" e
    lo zip ha "td-panopticon/", l'aggiornamento ne affiancherebbe una seconda copia invece di
    sostituire quella che c'e'. Con la cartella giusta la sostituisce."""
    if not _good_folder(folder):
        raise ValueError(f"cartella del connettore non valida: {folder!r}")
    with zipfile.ZipFile(io.BytesIO(content)) as zin:
        root = next((m.group(1) for n in zin.namelist() if (m := _ZIP_ROOT_RE.match(n))), None)
        if root is None:
            # zip senza cartella: WordPress lo metterebbe in una cartella NUOVA (dal nome del file
            # temporaneo), cioe' una copia in piu'. Quella copia non si tocca.
            raise ValueError("lo zip del connettore non ha la cartella td-panopticon/ (o simile)")
        if root == folder:
            return content
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                name = item.filename
                if name == root or name.startswith(root + "/"):
                    name = folder + name[len(root):]
                info = zipfile.ZipInfo(name, date_time=item.date_time)
                info.external_attr = item.external_attr
                info.compress_type = zipfile.ZIP_DEFLATED
                zout.writestr(info, data)
    return out.getvalue()


async def start_connector_rollout(kind: str, targets: list, label: str) -> dict:
    """Installa il connettore consegnato sui siti indicati (un lavoro in sottofondo per sito).
    WordPress: per ogni copia gia' presente uno zip con la sua cartella, installato senza
    attivarlo (la copia attiva resta attiva, quelle spente restano spente)."""
    from .install import start_install_job
    content, name = await connector_package(kind)
    per_site: dict[int, list] = {}
    if kind == "wp":
        cache: dict[str, bytes] = {}
        ok = []
        for t in targets:
            packs = []
            for folder in getattr(t, "_rollout_folders", None) or ([wp_folder(t)] if wp_folder(t) else []):
                if folder not in cache:
                    try:
                        cache[folder] = wp_package_for_folder(content, folder)
                    except ValueError:
                        continue                 # cartella strana: quella copia non si tocca
                packs.append(cache[folder])
            if packs:
                per_site[t.id] = packs
                ok.append(t)
        targets = ok
    if not targets:
        return {"job": None, "total": 0}
    return await start_install_job(targets, content, name, "wp" if kind == "wp" else "joomla", "plugin",
                                   kind != "wp", label=label, per_site=per_site, connector=kind,
                                   target=shipped_version(kind))


@router.get("/rollout", dependencies=[Depends(require_auth)])
async def rollout_status():
    """Per ogni CMS: versione consegnata, siti aggiornati e siti da aggiornare."""
    from ..db import SessionLocal
    from ..settings_store import get_operational_settings
    prefs = await get_operational_settings()
    out = {"auto": bool(prefs.get("connector_auto_update", True))}
    async with SessionLocal() as s:
        for kind in KINDS:
            old, kept = await _rollout_split(kind, s)
            from sqlalchemy import select as _select, func as _func
            from ..models import Site
            cms = "wp" if kind == "wp" else "joomla"
            total = (await s.execute(_select(_func.count(Site.id)).where(Site.cms == cms, Site.enabled == True))).scalar() or 0  # noqa: E712
            out[kind] = {"version": shipped_version(kind), "total": total, "outdated": len(old),
                         "sites": [{"id": x.id, "name": x.name, "version": x.connector_version or "?"} for x in old],
                         # versione piu' vecchia ma lasciati stare (mu-plugin, altra copia in uso, ...)
                         "kept": len(kept),
                         "kept_sites": [{"id": x.id, "name": x.name, "version": x.connector_version or "?",
                                         "why": _keep_reason(x), "hint": KEEP_HINTS.get(_keep_reason(x), "")}
                                        for x in kept]}
    return out


@router.post("/{kind}/rollout", dependencies=[Depends(require_auth)])
async def rollout_now(kind: str):
    """Installa il connettore consegnato su tutti i siti che ne hanno uno piu' vecchio: un lavoro
    in sottofondo per sito, come l'installazione in blocco."""
    if kind not in KINDS:
        raise HTTPException(400, "kind non valido")
    from ..db import SessionLocal
    async with SessionLocal() as s:
        targets = await outdated_sites(kind, s)
    if not targets:
        return {"job": None, "total": 0, "version": shipped_version(kind)}
    try:
        res = await start_connector_rollout(kind, targets, label=f"connettore {kind} {shipped_version(kind)}")
    except Exception as ex:  # noqa: BLE001
        raise HTTPException(500, f"Distribuzione del connettore non avviata: {ex}")
    res["version"] = shipped_version(kind)
    return res


@router.get("/{kind}/download", dependencies=[Depends(require_auth)])
async def download_connector(kind: str, plain: str = Query("")):
    """Download dello zip del connettore.

    Richiede il JWT pieno nell'header, NON il token immagine in query string: lo zip
    del connettore WordPress contiene la chiave di registrazione (TDPANOP_HUB_KEY), e
    i token in query finiscono in cronologia, log del proxy e header Referer.
    Il frontend lo scarica via fetch + blob."""
    if kind not in KINDS:
        raise HTTPException(400, "kind non valido")
    try:
        content, name = await connector_package(kind, plain == "1")
    except Exception as ex:  # noqa: BLE001
        raise HTTPException(500, f"Personalizzazione non riuscita: {ex}")
    return Response(content, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.delete("/{kind}", dependencies=[Depends(require_auth)])
async def reset_connector(kind: str):
    """Rimuove lo zip caricato: si torna al pacchetto incluso nell'installazione."""
    if kind not in KINDS:
        raise HTTPException(400, "kind non valido")
    zpath, jpath = _paths(kind)
    for p in (zpath, jpath):
        try:
            os.remove(p)
        except FileNotFoundError:
            pass
    return _meta(kind)
