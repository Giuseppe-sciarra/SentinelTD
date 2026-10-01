"""Clienti e report mensile per cliente.

Un cliente riceve ogni mese lo stesso report che arriva all'agenzia, limitato ai suoi siti
(perimetro "client:<id>" del generatore dei report), insieme allo stato dei siti: versioni,
dominio, peso, spazio, file del core. Di norma un sito ha un cliente, ma i legami sono
molti-a-molti. L'invio automatico parte nello stesso giorno e alla stessa ora del report
dell'agenzia (Report mensile), solo per i clienti con l'interruttore acceso.
"""
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import report as rep
from ..auth import require_auth, verify_token
from ..db import get_session
from ..models import Client, ClientSite, Site
from .reports import _strip_page_rule, client_emails, send_client_report

router = APIRouter(prefix="/api/clients", tags=["clients"])


def _out(cl: Client, sites: list[Site]) -> dict:
    return {
        "id": cl.id, "name": cl.name, "emails": cl.emails, "enabled": bool(cl.enabled),
        "email_list": client_emails(cl.emails),
        "sites": [{"id": x.id, "name": x.name, "url": x.url, "cms": x.cms, "tags": x.tags or ""} for x in sites],
    }


async def _sites_of(s: AsyncSession, cid: int) -> list[Site]:
    return (await s.execute(select(Site).join(ClientSite, ClientSite.site_id == Site.id)
                            .where(ClientSite.client_id == cid).order_by(Site.name))).scalars().all()


async def _set_sites(s: AsyncSession, cid: int, site_ids) -> None:
    ids = sorted({int(x) for x in (site_ids or []) if str(x).strip().isdigit()})
    valid = set((await s.execute(select(Site.id).where(Site.id.in_(ids or [0])))).scalars().all())
    await s.execute(delete(ClientSite).where(ClientSite.client_id == cid))
    for sid in ids:
        if sid in valid:
            s.add(ClientSite(client_id=cid, site_id=sid))


def _clean(payload: dict) -> dict:
    name = str(payload.get("name") or "").strip()[:190]
    if not name:
        raise HTTPException(422, "Serve il nome del cliente")
    raw = str(payload.get("emails") or "")
    bad = [x for x in __import__("re").split(r"[,;\s]+", raw) if x.strip() and x.strip() not in client_emails(x)]
    if bad:
        raise HTTPException(422, f"Indirizzo email non valido: {bad[0]}")
    return {"name": name, "emails": ", ".join(client_emails(raw)), "enabled": bool(payload.get("enabled"))}


@router.get("", dependencies=[Depends(require_auth)])
async def list_clients(s: AsyncSession = Depends(get_session)):
    clients = (await s.execute(select(Client).order_by(Client.name))).scalars().all()
    return [_out(cl, await _sites_of(s, cl.id)) for cl in clients]


@router.post("", dependencies=[Depends(require_auth)])
async def create_client(payload: dict = Body(...), s: AsyncSession = Depends(get_session)):
    data = _clean(payload)
    cl = Client(**data)
    s.add(cl)
    await s.flush()
    await _set_sites(s, cl.id, payload.get("site_ids"))
    await s.commit()
    return _out(cl, await _sites_of(s, cl.id))


@router.get("/config", dependencies=[Depends(require_auth)])
async def get_clients_config():
    """Impostazioni uniche per tutti i report dei clienti (separate da quelle del Report mensile)."""
    return {"config": await rep.get_client_config(), "template": await rep.get_client_template(),
            "default_template": rep.DEFAULT_TEMPLATE, "suggested_period": rep.prev_period()}


@router.put("/config", dependencies=[Depends(require_auth)])
async def save_clients_config(payload: dict = Body(...)):
    cfg = await rep.save_client_config(payload.get("config") or {})
    if "template" in payload:
        html = (payload.get("template") or "").strip()
        if html:
            # un layout rotto non deve bloccare l'invio ai clienti
            try:
                rep._env.from_string(html)
            except Exception as ex:  # noqa: BLE001
                raise HTTPException(422, f"Template non valido: {ex}")
            await rep.save_client_template(html)
        else:
            await rep.save_client_template(rep.DEFAULT_TEMPLATE)
    return {"ok": True, "config": cfg}


@router.post("/from-sites", dependencies=[Depends(require_auth)])
async def clients_from_sites(payload: dict = Body(...), s: AsyncSession = Depends(get_session)):
    """Clienti dai siti scelti.

    mode "each" (default): un cliente per ogni sito che non ne ha ancora uno, col nome del sito
        e l'invio spento.
    mode "group": UN cliente con tutti i siti scelti (es. tutta la cartella di un'agenzia), con
        nome ed email del gruppo. I siti possono avere anche il loro cliente singolo: cosi'
        arrivano sia il report del singolo sito sia quello unico del gruppo.
    """
    ids = sorted({int(x) for x in (payload.get("site_ids") or []) if str(x).strip().isdigit()})
    sites = (await s.execute(select(Site).where(Site.id.in_(ids or [0])).order_by(Site.name))).scalars().all()
    if str(payload.get("mode") or "each") == "group":
        if not sites:
            raise HTTPException(422, "Scegli almeno un sito")
        data = _clean({"name": payload.get("name"), "emails": payload.get("emails"), "enabled": False})
        cl = Client(**data)
        s.add(cl)
        await s.flush()
        for site in sites:
            s.add(ClientSite(client_id=cl.id, site_id=site.id))
        await s.commit()
        return {"created": 1, "names": [cl.name], "skipped": 0, "client": _out(cl, await _sites_of(s, cl.id))}
    taken = set((await s.execute(select(ClientSite.site_id))).scalars().all())
    created = []
    for site in sites:
        if site.id in taken:
            continue
        cl = Client(name=site.name[:190], emails="", enabled=False)
        s.add(cl)
        await s.flush()
        s.add(ClientSite(client_id=cl.id, site_id=site.id))
        created.append(cl.name)
    await s.commit()
    return {"created": len(created), "names": created, "skipped": len(sites) - len(created)}


@router.post("/merge", dependencies=[Depends(require_auth)])
async def merge_clients(payload: dict = Body(...), s: AsyncSession = Depends(get_session)):
    """Unisce piu' clienti in uno: un solo cliente, un solo report, con i siti e gli indirizzi
    di tutti. Resta il primo cliente (col nome indicato), gli altri vengono eliminati."""
    ids = [int(x) for x in (payload.get("ids") or []) if str(x).strip().isdigit()]
    clients = (await s.execute(select(Client).where(Client.id.in_(ids or [0])).order_by(Client.id))).scalars().all()
    if len(clients) < 2:
        raise HTTPException(422, "Scegli almeno due clienti da unire")
    target, others = clients[0], clients[1:]
    site_ids = set()
    emails = []
    for cl in clients:
        site_ids |= set((await s.execute(select(ClientSite.site_id).where(ClientSite.client_id == cl.id))).scalars().all())
        emails += client_emails(cl.emails)
    raw_emails = payload.get("emails")
    data = _clean({"name": payload.get("name") or target.name,
                   "emails": raw_emails if raw_emails is not None else ", ".join(emails),
                   "enabled": bool(payload.get("enabled", False))})
    if data["enabled"] and not client_emails(data["emails"]):
        data["enabled"] = False
    target.name, target.emails, target.enabled = data["name"], data["emails"], data["enabled"]
    for cl in others:
        await s.execute(delete(ClientSite).where(ClientSite.client_id == cl.id))
        await s.delete(cl)
    await s.flush()
    await _set_sites(s, target.id, sorted(site_ids))
    await s.commit()
    return _out(target, await _sites_of(s, target.id))


@router.post("/bulk-delete", dependencies=[Depends(require_auth)])
async def bulk_delete_clients(payload: dict = Body(...), s: AsyncSession = Depends(get_session)):
    ids = [int(x) for x in (payload.get("ids") or []) if str(x).strip().isdigit()]
    if not ids:
        return {"deleted": 0}
    await s.execute(delete(ClientSite).where(ClientSite.client_id.in_(ids)))
    res = await s.execute(delete(Client).where(Client.id.in_(ids)))
    await s.commit()
    return {"deleted": res.rowcount or 0}


@router.post("/{cid}/add-sites", dependencies=[Depends(require_auth)])
async def add_sites_to_client(cid: int, payload: dict = Body(...), s: AsyncSession = Depends(get_session)):
    """Aggiunge siti a un cliente/gruppo gia' creato: quelli gia' dentro restano, i nuovi si
    aggiungono. Gli indirizzi email indicati si aggiungono a quelli che il cliente ha gia'."""
    cl = await s.get(Client, cid)
    if not cl:
        raise HTTPException(404, "Cliente non trovato")
    ids = {int(x) for x in (payload.get("site_ids") or []) if str(x).strip().isdigit()}
    have = set((await s.execute(select(ClientSite.site_id).where(ClientSite.client_id == cid))).scalars().all())
    valid = set((await s.execute(select(Site.id).where(Site.id.in_(sorted(ids) or [0])))).scalars().all())
    new = sorted(valid - have)
    for sid in new:
        s.add(ClientSite(client_id=cid, site_id=sid))
    extra = str(payload.get("emails") or "").strip()
    if extra:
        data = _clean({"name": cl.name, "emails": f"{cl.emails}, {extra}", "enabled": cl.enabled})
        cl.emails = data["emails"]
    await s.commit()
    return {"added": len(new), "already": len(valid & have), "client": _out(cl, await _sites_of(s, cid))}


@router.put("/{cid}", dependencies=[Depends(require_auth)])
async def update_client(cid: int, payload: dict = Body(...), s: AsyncSession = Depends(get_session)):
    cl = await s.get(Client, cid)
    if not cl:
        raise HTTPException(404, "Cliente non trovato")
    data = _clean({"name": cl.name, "emails": cl.emails, "enabled": cl.enabled, **payload})
    cl.name, cl.emails, cl.enabled = data["name"], data["emails"], data["enabled"]
    if "site_ids" in payload:
        await _set_sites(s, cid, payload.get("site_ids"))
    await s.commit()
    return _out(cl, await _sites_of(s, cid))


@router.delete("/{cid}", dependencies=[Depends(require_auth)])
async def delete_client(cid: int, s: AsyncSession = Depends(get_session)):
    cl = await s.get(Client, cid)
    if not cl:
        raise HTTPException(404, "Cliente non trovato")
    await s.execute(delete(ClientSite).where(ClientSite.client_id == cid))
    await s.delete(cl)
    await s.commit()
    return {"deleted": True}


@router.post("/{cid}/preview", dependencies=[Depends(require_auth)])
async def preview_client(cid: int, payload: dict = Body(default={})):
    """HTML del report del cliente, mostrato come un foglio A4 (come l'anteprima del Report mensile)."""
    period = payload.get("period") or rep.prev_period()
    # impostazioni e layout non ancora salvati (anteprima dal pannello Impostazioni dei clienti)
    cfg = rep.normalize(payload.get("config")) if payload.get("config") else None
    tpl = payload.get("template") or None
    try:
        html = await rep.render_html(period, template=tpl, cfg=cfg, scope=f"{rep.CLIENT_PREFIX}{cid}")
    except Exception as ex:  # noqa: BLE001
        raise HTTPException(422, f"Errore nel template: {ex}")
    screen_css = ('<style media="screen">html{background:#e9edf2;padding:18px 0}'
                  'body{max-width:210mm;margin:0 auto;padding:16mm 14mm;background:#fff;'
                  'box-shadow:0 2px 14px rgba(0,0,0,.14);border-radius:2px}</style>')
    html = _strip_page_rule(html)
    html = html.replace("</head>", screen_css + "</head>", 1) if "</head>" in html else screen_css + html
    return HTMLResponse(html)


@router.get("/{cid}/pdf")
async def pdf_client(cid: int, period: str = Query(...), k: str = Query("")):
    """PDF del report del cliente (token in query: il browser scarica con un link diretto)."""
    if not verify_token(k):
        raise HTTPException(401, "Invalid token")
    html, blob, filename = await rep.build(period, f"{rep.CLIENT_PREFIX}{cid}")
    if blob is None:
        return HTMLResponse(html, headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    return Response(blob, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/{cid}/send", dependencies=[Depends(require_auth)])
async def send_client_now(cid: int, payload: dict = Body(default={})):
    res = await send_client_report(payload.get("period") or rep.prev_period(), f"{rep.CLIENT_PREFIX}{cid}")
    if not res.get("sent"):
        raise HTTPException(400, res.get("error") or "Invio non riuscito")
    return res
