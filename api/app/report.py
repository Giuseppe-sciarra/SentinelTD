"""
Report mensile di Sentinel TD.

Raccoglie i dati del mese dal rollup (update_monthly) piu' lo stato attuale di
sicurezza e scadenze, li passa a un template HTML EDITABILE dal pannello e ne
produce un PDF intestato con il logo.

Il PDF e' generato con WeasyPrint se disponibile; se manca (immagine senza le
librerie di sistema) il report viene comunque prodotto e allegato in HTML, cosi'
la funzione degrada invece di rompersi.
"""
import base64
import hashlib
import html
import json
import logging
import mimetypes
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jinja2 import Environment, BaseLoader, TemplateError
from sqlalchemy import bindparam, select, func, text

from .db import SessionLocal
from .models import AppSetting, UpdateMonthly, Site, SiteExpiry, SiteSize, Client, ClientSite, Extension
from .problems import LABELS as PROBLEM_LABELS, failed_by_site, site_problems
from .config import settings

from .i18n import t, template as localize_template

log = logging.getLogger("report")

CONFIG_KEY = "report:config"
TEMPLATE_KEY = "report:template"
LAST_SENT_KEY = "report:last_sent"
# Report dei clienti: impostazioni e layout separati da quelli dell'agenzia, uniche per tutti i
# clienti. La prima volta sono una copia di quelli dell'agenzia (vedi get_client_config).
CLIENT_CONFIG_KEY = "report:client_config"
CLIENT_TEMPLATE_KEY = "report:client_template"

MESI = ["", "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
        "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]

DEFAULTS = {
    "enabled": True,
    "send_day": 1,          # giorno del mese in cui inviare (1-28)
    "send_hour": 8,         # ora locale
    "send_minute": 0,       # minuti (es. 30 per le 8:30): il controllo gira ogni 5 minuti
    "recipients": "",       # vuoto = usa REPORT_TO del .env; piu' indirizzi separati da virgola
    "company": "Tastiere Digitali",
    "title": "Report manutenzione siti web",
    "intro": "Di seguito il riepilogo delle attività di manutenzione e aggiornamento svolte nel periodo.",
    "footer": "Report generato automaticamente da Sentinel TD.",
    "show_summary": True,        # KPI del mese
    "show_sites": True,          # dettaglio per sito (quante volte, quali plugin)
    "show_top": True,            # classifica estensioni piu' aggiornate
    "show_failed": True,         # elenco update falliti
    "show_security": True,       # stato vulnerabilita'
    "show_expiries": True,       # scadenze domini/licenze in arrivo
    "show_compare": True,        # confronto col mese precedente + andamento
    "show_site_stats": True,     # stato dei siti: versioni, PHP, dominio, peso, spazio, file del core
    "expiry_horizon_days": 60,
    # Report da produrre/inviare ogni mese. Ogni voce: {key, enabled}
    # key = "__all__" (tutti i siti) oppure il nome esatto di una cartella/tag.
    # Tutti i report vengono inviati all'unico indirizzo configurato in "recipients".
    "scopes": [{"key": "__all__", "enabled": True}],
}

GLOBAL_KEY = "__all__"
CLIENT_PREFIX = "client:"        # perimetro "cliente": client:<id>


def is_due(cfg: dict, now: datetime) -> bool:
    """Giorno e orario d'invio arrivati: dal momento impostato fino a un'ora dopo. Il controllo
    gira ogni 5 minuti; la finestra di un'ora recupera un giro saltato (worker riavviato proprio
    in quel momento) e la memoria degli invii del periodo evita i doppioni."""
    if not cfg.get("enabled", True) or now.day != int(cfg.get("send_day", 1)):
        return False
    at = now.replace(hour=int(cfg.get("send_hour", 8)), minute=int(cfg.get("send_minute", 0)), second=0, microsecond=0)
    return at <= now < at + timedelta(hours=1)


def is_client_scope(scope: str) -> bool:
    return bool(scope) and scope.startswith(CLIENT_PREFIX)


def client_id_of(scope: str) -> int:
    try:
        return int(scope[len(CLIENT_PREFIX):])
    except (TypeError, ValueError):
        return 0


# Impronte dei modelli predefiniti delle versioni precedenti: una copia salvata IDENTICA non e'
# una personalizzazione, e' il vecchio default rimasto nel database -> si usa quello nuovo
# (che ha in piu' la sezione "Stato dei siti").
_LEGACY_TEMPLATE_HASHES = {"e6a0d240b2555349529d2f6f3ca8c34be80f1018bd7990e69407850013c87381", "1815888d7299da0ab27e1ffcfcf59aefb419fbbda23a4a76e9f26e3c8b0f38e3", "633d8df19d1ed79cb7063d00f185375e8aea88d3f0df92ffa35b013ecff10030", "d44068bb751e54a79759fe390483e4fd9ca8c178b4e6fd058309b516dee18c90"}

# ---------------------------------------------------------------- template
DEFAULT_TEMPLATE = """<!doctype html>
<html lang="it"><head><meta charset="utf-8">
<style>
  @page { size: A4; margin: 18mm 15mm 16mm; @bottom-center { content: "Pagina " counter(page) " di " counter(pages); font: 9px Helvetica, sans-serif; color: #999; } }
  * { box-sizing: border-box; }
  body { font: 11px/1.5 Helvetica, Arial, sans-serif; color: #24292f; margin: 0; }
  .head { display: flex; align-items: center; border-bottom: 2px solid #1f6feb; padding-bottom: 12px; margin-bottom: 18px; }
  .head img { max-height: 46px; max-width: 200px; }
  .head .t { margin-left: auto; text-align: right; }
  .head h1 { font-size: 17px; margin: 0 0 2px; }
  .head .p { color: #6a737d; font-size: 11px; }
  h2 { font-size: 13px; margin: 20px 0 8px; color: #1f2933; border-left: 3px solid #1f6feb; padding-left: 8px; }
  .intro { color: #444; margin-bottom: 14px; }
  .kpi { display: flex; gap: 10px; margin-bottom: 6px; }
  .kpi > div { flex: 1; border: 1px solid #e1e4e8; border-radius: 6px; padding: 10px 12px; }
  .kpi .v { font-size: 20px; font-weight: 700; color: #1f6feb; }
  .kpi .k { font-size: 9.5px; color: #6a737d; text-transform: uppercase; letter-spacing: .04em; }
  table { width: 100%; border-collapse: collapse; margin-bottom: 10px; }
  th { text-align: left; font-size: 9.5px; text-transform: uppercase; letter-spacing: .04em; color: #6a737d; border-bottom: 1px solid #d0d7de; padding: 5px 6px; }
  td { padding: 5px 6px; border-bottom: 1px solid #eef1f4; vertical-align: top; }
  .site { page-break-inside: avoid; margin-bottom: 14px; }
  .site h3 { font-size: 12px; margin: 0 0 1px; }
  .site .u { color: #6a737d; font-size: 10px; margin-bottom: 5px; }
  .n { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
  .mut { color: #6a737d; }
  .ko { color: #b00020; }
  .up { color: #1a7f4b; } .dn { color: #b00020; }
  .trend { display: flex; align-items: flex-end; gap: 8px; height: 76px; margin: 10px 0 4px; }
  .trend .tb { flex: 1; text-align: center; }
  .trend .tv { font-size: 9px; color: #6a737d; margin-bottom: 2px; }
  .trend .tbar { background: #cfe0ff; border-radius: 3px 3px 0 0; }
  .trend .tbar.cur { background: #1f6feb; }
  .trend .tl { font-size: 9px; color: #6a737d; margin-top: 3px; text-transform: capitalize; }
  .foot { margin-top: 22px; padding-top: 10px; border-top: 1px solid #e1e4e8; color: #8a949e; font-size: 9.5px; }
  .st td { font-size: 10px; }
  .st tr { page-break-inside: avoid; }
  .st tr.prob td { border-top: 0; padding-top: 0; font-size: 9.5px; }
  h3.srvh { font-size: 11.5px; margin: 14px 0 4px; color: #444; }
  .sm { font-size: 9px; margin-top: 1px; }
</style></head><body>

<div class="head">
  {% if logo %}<img src="{{ logo }}">{% endif %}
  <div class="t"><h1>{{ cfg.title }}</h1><div class="p">{{ period_label }}{% if cfg.company %} &middot; {{ cfg.company }}{% endif %}</div>
  {% if not is_global %}<div class="p" style="font-weight:600;color:#1f6feb">{{ scope_label }}</div>{% endif %}</div>
</div>

{% if cfg.intro %}<div class="intro">{{ cfg.intro }}</div>{% endif %}

{% if cfg.show_summary %}
<h2>Riepilogo del periodo</h2>
<div class="kpi">
  <div><div class="v">{{ total_updates }}</div><div class="k">Aggiornamenti applicati</div></div>
  <div><div class="v">{{ sites_touched }}</div><div class="k">Siti aggiornati</div></div>
  <div><div class="v">{{ distinct_items }}</div><div class="k">Componenti diversi</div></div>
  <div><div class="v">{{ total_failed }}</div><div class="k">Non riusciti</div></div>
</div>
<div class="mut">Siti in monitoraggio: {{ sites_total }} ({{ wp_count }} WordPress, {{ joomla_count }} Joomla).</div>
{% endif %}

{% if cfg.show_compare %}
<h2>Confronto con {{ prev.label }}</h2>
<table>
  <thead><tr><th>Indicatore</th><th class="n">{{ period_label }}</th><th class="n">{{ prev.label }}</th><th class="n">Variazione</th></tr></thead>
  <tbody>
    <tr><td>Aggiornamenti applicati</td><td class="n">{{ total_updates }}</td><td class="n">{{ prev.updates }}</td><td class="n {% if delta.updates.up %}up{% elif delta.updates.down %}dn{% endif %}">{{ delta.updates.arrow }} {{ delta.updates.text }}</td></tr>
    <tr><td>Siti aggiornati</td><td class="n">{{ sites_touched }}</td><td class="n">{{ prev.sites }}</td><td class="n">{{ delta.sites.arrow }} {{ delta.sites.text }}</td></tr>
    <tr><td>Componenti diversi</td><td class="n">{{ distinct_items }}</td><td class="n">{{ prev.components }}</td><td class="n">{{ delta.components.arrow }} {{ delta.components.text }}</td></tr>
    <tr><td>Non riusciti</td><td class="n">{{ total_failed }}</td><td class="n">{{ prev.failed }}</td><td class="n {% if delta.failed.up %}dn{% elif delta.failed.down %}up{% endif %}">{{ delta.failed.arrow }} {{ delta.failed.text }}</td></tr>
  </tbody>
</table>
{% if trend %}
<div class="trend">
  {% for t in trend %}<div class="tb"><div class="tv">{{ t.updates }}</div><div class="tbar{% if t.current %} cur{% endif %}" style="height:{{ t.h }}px"></div><div class="tl">{{ t.label.split(' ')[0][:3] }}</div></div>{% endfor %}
</div>
<div class="mut">Aggiornamenti applicati negli ultimi 6 mesi.</div>
{% endif %}
{% endif %}

{% if cfg.show_sites and sites %}
<h2>Dettaglio per sito</h2>
{% for s in sites %}
<div class="site">
  <h3>{{ s.name }}</h3>
  <div class="u">{{ s.url }} &middot; {{ s.cms_label }} &middot; {{ s.total }} aggiornamenti su {{ s.components|length }} componenti</div>
  <table>
    <thead><tr><th>Componente</th><th>Tipo</th><th class="n">Volte</th><th>Ultima versione</th></tr></thead>
    <tbody>
    {% for i in s.components %}
      <tr><td>{{ i.name }}</td><td class="mut">{{ i.type_label }}</td><td class="n">{{ i.count }}</td><td class="mut">{{ i.last_version or '—' }}</td></tr>
    {% endfor %}
    </tbody>
  </table>
</div>
{% endfor %}
{% endif %}

{% if cfg.show_top and top_items %}
<h2>Componenti più aggiornati</h2>
<table>
  <thead><tr><th>Componente</th><th class="n">Aggiornamenti</th><th class="n">Siti</th></tr></thead>
  <tbody>{% for t in top_items %}<tr><td>{{ t.name }}</td><td class="n">{{ t.count }}</td><td class="n">{{ t.sites }}</td></tr>{% endfor %}</tbody>
</table>
{% endif %}

{% if cfg.show_failed and failed_items %}
<h2>Aggiornamenti non riusciti</h2>
<table>
  <thead><tr><th>Sito</th><th>Componente</th><th class="n">Tentativi</th></tr></thead>
  <tbody>{% for f in failed_items %}<tr><td>{{ f.site }}</td><td>{{ f.name }}</td><td class="n ko">{{ f.count }}</td></tr>{% endfor %}</tbody>
</table>
<div class="mut">Gli aggiornamenti non riusciti vengono ritentati automaticamente nei cicli successivi.</div>
{% endif %}

{% if cfg.show_security %}
<h2>Sicurezza</h2>
{% if security.total %}
<div class="mut">Vulnerabilità note attualmente rilevate sulle estensioni installate:</div>
<table>
  <thead><tr><th>Gravità</th><th class="n">Occorrenze</th></tr></thead>
  <tbody>
    <tr><td>Critiche</td><td class="n">{{ security.critical }}</td></tr>
    <tr><td>Alte</td><td class="n">{{ security.high }}</td></tr>
    <tr><td>Medie</td><td class="n">{{ security.medium }}</td></tr>
    <tr><td>Basse</td><td class="n">{{ security.low }}</td></tr>
  </tbody>
</table>
{% else %}
<div>Nessuna vulnerabilità nota attiva sui siti monitorati.</div>
{% endif %}
{% endif %}

{% if cfg.show_site_stats and site_stats %}
<h2>Stato dei siti</h2>
{% set _probs = site_stats | selectattr("problems") | list %}
<p class="mut">{% if _probs %}{{ _probs | length }} {% if _probs | length == 1 %}sito ha{% else %}siti hanno{% endif %} qualcosa da guardare, indicato sotto il sito.{% else %}Nessun problema rilevato sui siti.{% endif %}</p>
{% for grp in site_stat_groups %}
{% if site_stat_groups | length > 1 or grp.server %}<h3 class="srvh">Server {{ grp.server or "non rilevato" }} · {{ grp.sites | length }} {% if grp.sites | length == 1 %}sito{% else %}siti{% endif %}{% if grp.problems %} · <span class="ko">{{ grp.problems }} con problemi</span>{% endif %}</h3>{% endif %}
<table class="st">
  <thead><tr><th>Sito</th><th>Versioni</th><th>Dominio</th><th>Peso</th><th>Spazio libero</th><th>File del core</th></tr></thead>
  <tbody>
  {% for x in grp.sites %}
    <tr>
      <td><b>{{ x.name }}</b><div class="sm mut">{{ x.url }}</div></td>
      <td>{{ x.cms_label }} {{ x.core or '' }}<div class="sm{% if x.php_state == 'fuori supporto' %} ko{% else %} mut{% endif %}">PHP {{ x.php or '—' }}{% if x.php_state %} · {{ x.php_state }}{% endif %}</div></td>
      <td>{% if x.domain_date %}{{ x.domain_date }}<div class="sm{% if x.domain_days is not none and x.domain_days < 30 %} ko{% else %} mut{% endif %}">{% if x.domain_days is not none and x.domain_days < 0 %}scaduto da {{ -x.domain_days }} giorni{% else %}tra {{ x.domain_days }} giorni{% endif %}</div>{% else %}<span class="mut">—</span>{% endif %}</td>
      <td>{% if x.size %}{{ x.size }}<div class="sm mut">{% if x.growth %}{{ x.growth }} in {{ x.growth_days }} giorni{% endif %}{% if x.db %}{% if x.growth %} · {% endif %}database {{ x.db }}{% endif %}</div>{% else %}<span class="mut">—</span>{% endif %}</td>
      <td>{% if x.space %}<span class="{% if x.space_low %}ko{% endif %}">{{ x.space }}</span>{% else %}<span class="mut">—</span>{% endif %}</td>
      <td>{% if x.core_files %}<span class="{% if x.core_issues %}ko{% endif %}">{{ x.core_files }}</span>{% else %}<span class="mut">—</span>{% endif %}</td>
    </tr>
    {% if x.problems %}<tr class="prob"><td colspan="6"><span class="ko">⚠ {{ x.problems | join(" · ") }}</span></td></tr>{% endif %}
  {% endfor %}
  </tbody>
</table>
{% endfor %}
<div class="mut">Peso: file e database del sito. Spazio libero: quanto si riesce davvero a scrivere sul sito, misurato con l'ultima diagnostica.</div>
{% endif %}

{% if cfg.show_expiries and (domains or licenses) %}
<h2>Scadenze nei prossimi {{ cfg.expiry_horizon_days }} giorni</h2>
{% if domains %}
<table>
  <thead><tr><th>Dominio</th><th>Scadenza</th><th class="n">Giorni</th></tr></thead>
  <tbody>{% for d in domains %}<tr><td>{{ d.name }}</td><td>{{ d.date }}</td><td class="n">{{ d.days }}</td></tr>{% endfor %}</tbody>
</table>
{% endif %}
{% if licenses %}
<table>
  <thead><tr><th>Licenza / componente</th><th>Fornitore</th><th>Scadenza</th><th class="n">Giorni</th></tr></thead>
  <tbody>{% for l in licenses %}<tr><td>{{ l.name }}</td><td class="mut">{{ l.provider or '—' }}</td><td>{{ l.date }}</td><td class="n">{{ l.days }}</td></tr>{% endfor %}</tbody>
</table>
{% endif %}
{% endif %}

<div class="foot">{% if is_client %}{% if cfg.company %}Report di {{ cfg.company }}{% endif %}{% else %}{{ cfg.footer }} &middot; generato il {{ generated_at }}{% if app_version %} &middot; Sentinel TD v{{ app_version }}{% endif %}{% endif %}</div>
</body></html>
"""

_env = Environment(loader=BaseLoader(), autoescape=True)


# ---------------------------------------------------------------- config
def normalize(data: dict | None) -> dict:
    src = data or {}
    out = deepcopy(DEFAULTS)
    for key in ("title", "intro", "footer"):
        out[key] = t(out[key])
    for k in ("company", "title", "intro", "footer", "recipients"):
        if k in src and isinstance(src[k], str):
            out[k] = src[k].strip()[:2000]
    for k in ("enabled", "show_summary", "show_sites", "show_top", "show_failed", "show_security", "show_expiries", "show_compare",
              "show_site_stats"):
        if k in src:
            out[k] = bool(src[k])
    try:
        out["send_day"] = max(1, min(28, int(src.get("send_day", out["send_day"]))))
    except (TypeError, ValueError):
        pass
    try:
        out["send_hour"] = max(0, min(23, int(src.get("send_hour", out["send_hour"]))))
        out["send_minute"] = max(0, min(59, int(src.get("send_minute", out["send_minute"]))))
    except (TypeError, ValueError):
        pass
    try:
        out["expiry_horizon_days"] = max(7, min(365, int(src.get("expiry_horizon_days", out["expiry_horizon_days"]))))
    except (TypeError, ValueError):
        pass

    # scopes: uno per cartella + quello globale. Migra la vecchia "only_folder".
    raw = src.get("scopes")
    scopes: list[dict] = []
    if isinstance(raw, list):
        for item in raw[:60]:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key", "")).strip()[:190]
            if not key:
                continue
            if any(x["key"].lower() == key.lower() for x in scopes):
                continue
            scopes.append({"key": key, "enabled": bool(item.get("enabled", True))})
    elif src.get("only_folder"):
        scopes.append({"key": str(src["only_folder"]).strip(), "enabled": True})
    if not scopes:
        scopes = [{"key": GLOBAL_KEY, "enabled": True}]
    out["scopes"] = scopes
    return out


def scope_label(key: str) -> str:
    return "Tutti i siti" if (not key or key == GLOBAL_KEY) else key


def scope_slug(key: str, label: str = "") -> str:
    if not key or key == GLOBAL_KEY:
        return "globale"
    if is_client_scope(key):
        import re as _re
        base = _re.sub(r"[^a-z0-9]+", "-", (label or "").lower()).strip("-")
        return "cliente-" + (base or str(client_id_of(key)))
    import re as _re
    s = _re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-")
    return s or "cartella"


def report_recipients(cfg: dict) -> str:
    """Destinatario dei report: unico, dalle impostazioni (vuoto = REPORT_TO del .env)."""
    return (cfg.get("recipients") or "").strip()


async def get_config() -> dict:
    try:
        async with SessionLocal() as s:
            row = await s.get(AppSetting, CONFIG_KEY)
            if row and row.value:
                return normalize(json.loads(row.value))
    except Exception:  # noqa: BLE001
        pass
    return normalize({})


async def save_config(data: dict) -> dict:
    clean = normalize(data)
    async with SessionLocal() as s:
        row = await s.get(AppSetting, CONFIG_KEY)
        if row:
            row.value = json.dumps(clean)
        else:
            s.add(AppSetting(key=CONFIG_KEY, value=json.dumps(clean)))
        await s.commit()
    return clean


async def get_client_config() -> dict:
    """Impostazioni dei report ai clienti. Se non esistono ancora sono una COPIA di quelle del
    report dell'agenzia, salvata subito: da li' in poi le due vivono separate. L'invio automatico
    parte acceso, come prima che fossero separate (i clienti partivano anche col report
    dell'agenzia spento)."""
    try:
        async with SessionLocal() as s:
            row = await s.get(AppSetting, CLIENT_CONFIG_KEY)
            if row and row.value:
                return normalize(json.loads(row.value))
    except Exception:  # noqa: BLE001
        pass
    base = await get_config()
    base["enabled"] = True
    try:
        return await save_client_config(base)
    except Exception:  # noqa: BLE001
        return base


async def save_client_config(data: dict) -> dict:
    clean = normalize(data)
    async with SessionLocal() as s:
        row = await s.get(AppSetting, CLIENT_CONFIG_KEY)
        if row:
            row.value = json.dumps(clean)
        else:
            s.add(AppSetting(key=CLIENT_CONFIG_KEY, value=json.dumps(clean)))
        await s.commit()
    return clean


async def get_client_template() -> str:
    """Layout del PDF dei clienti. Se non esiste ancora: copia del layout dell'agenzia se e' stato
    personalizzato, altrimenti il predefinito (che segue gli aggiornamenti di Sentinel)."""
    try:
        async with SessionLocal() as s:
            row = await s.get(AppSetting, CLIENT_TEMPLATE_KEY)
            if row is not None:
                if row.value.strip() and hashlib.sha256(row.value.strip().encode()).hexdigest() not in _LEGACY_TEMPLATE_HASHES:
                    return row.value
                return localize_template(DEFAULT_TEMPLATE)
            mine = await s.get(AppSetting, TEMPLATE_KEY)
            if mine and mine.value.strip() and hashlib.sha256(mine.value.strip().encode()).hexdigest() not in _LEGACY_TEMPLATE_HASHES:
                s.add(AppSetting(key=CLIENT_TEMPLATE_KEY, value=mine.value))
                await s.commit()
                return mine.value
    except Exception:  # noqa: BLE001
        pass
    return localize_template(DEFAULT_TEMPLATE)


async def save_client_template(html: str) -> None:
    async with SessionLocal() as s:
        row = await s.get(AppSetting, CLIENT_TEMPLATE_KEY)
        if row:
            row.value = html
        else:
            s.add(AppSetting(key=CLIENT_TEMPLATE_KEY, value=html))
        await s.commit()


async def get_template() -> str:
    try:
        async with SessionLocal() as s:
            row = await s.get(AppSetting, TEMPLATE_KEY)
            if row and row.value.strip():
                if hashlib.sha256(row.value.strip().encode()).hexdigest() in _LEGACY_TEMPLATE_HASHES:
                    return localize_template(DEFAULT_TEMPLATE)
                return row.value
    except Exception:  # noqa: BLE001
        pass
    return localize_template(DEFAULT_TEMPLATE)


async def save_template(html: str) -> None:
    async with SessionLocal() as s:
        row = await s.get(AppSetting, TEMPLATE_KEY)
        if row:
            row.value = html
        else:
            s.add(AppSetting(key=TEMPLATE_KEY, value=html))
        await s.commit()


# ---------------------------------------------------------------- helpers
def period_label(period: str) -> str:
    try:
        y, m = period.split("-")
        return f"{t(MESI[int(m)]).capitalize()} {y}"
    except Exception:  # noqa: BLE001
        return period


def prev_period(ref: datetime | None = None) -> str:
    d = (ref or datetime.now()).replace(day=1) - timedelta(days=1)
    return d.strftime("%Y-%m")


def current_period() -> str:
    return datetime.now().strftime("%Y-%m")


_TYPE_LABELS = {"plugin": "Plugin", "theme": "Tema", "core": "Core", "translation": "Traduzioni",
                "component": "Componente", "module": "Modulo", "package": "Pacchetto", "library": "Libreria",
                "file": "File", "other": "Altro"}



def shift_period(period: str, months: int) -> str:
    """Periodo spostato di N mesi (negativo = indietro)."""
    y, m = int(period[:4]), int(period[5:7])
    t = (y * 12 + (m - 1)) + months
    return f"{t // 12:04d}-{t % 12 + 1:02d}"


async def month_stats(s, period: str, allowed: set[int] | None) -> dict:
    """Totali di un mese per il perimetro indicato (None = tutti i siti)."""
    rows = (await s.execute(select(UpdateMonthly).where(UpdateMonthly.period == period))).scalars().all()
    if allowed is not None:
        rows = [x for x in rows if x.site_id in allowed]
    ok = sum(x.ok_count for x in rows)
    return {
        "period": period, "label": period_label(period),
        "updates": ok,
        "failed": sum(x.fail_count for x in rows),
        "sites": len({x.site_id for x in rows if x.ok_count > 0}),
        "components": len({(x.ext_name or x.slug).lower() for x in rows if x.ok_count > 0}),
    }


def _delta(cur: int, prev: int) -> dict:
    """Variazione assoluta e percentuale, con freccia gia' pronta per il template."""
    diff = cur - prev
    pct = None if prev == 0 else round(diff * 100 / prev)
    return {"diff": diff, "pct": pct, "up": diff > 0, "down": diff < 0,
            "arrow": "▲" if diff > 0 else ("▼" if diff < 0 else "="),
            "text": ("+" if diff > 0 else "") + str(diff) + ("" if pct is None else f" ({'+' if diff > 0 else ''}{pct}%)")}

async def _logo_data_uri() -> str:
    """Logo come data URI: il PDF deve essere autonomo, senza chiamate HTTP."""
    try:
        async with SessionLocal() as s:
            row = await s.get(AppSetting, "brand:logo_path")
            p = Path(row.value) if row and row.value else Path("static/logo.png")
        if not p.is_file():
            p = Path("static/logo.png")
        if not p.is_file():
            return ""
        mime = mimetypes.guess_type(str(p))[0] or "image/png"
        return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------- dati
# ---------------------------------------------------------------- stato dei siti
_PHP_EOL = {"8.1": ("2023-11-25", "2025-12-31"), "8.2": ("2024-12-31", "2026-12-31"), "8.3": ("2025-12-31", "2027-12-31"),
            "8.4": ("2026-12-31", "2028-12-31"), "8.5": ("2027-12-31", "2029-12-31")}


def _php_state(v: str) -> str:
    """Stessa regola del pannello: '' (supportata), 'solo sicurezza', 'fuori supporto'."""
    import re as _re
    m = _re.match(r"\s*(\d+)\.(\d+)", v or "")
    if not m:
        return ""
    major, minor = int(m.group(1)), int(m.group(2))
    if major < 8 or (major == 8 and minor == 0):
        return "fuori supporto"
    d = _PHP_EOL.get(f"{major}.{minor}")
    if not d:
        return ""
    today = datetime.now(timezone.utc).date().isoformat()
    if today > d[1]:
        return "fuori supporto"
    if today > d[0]:
        return "solo sicurezza"
    return ""


def _fmt_bytes(b) -> str:
    b = float(b or 0)
    if b >= 1073741824:
        return f"{b / 1073741824:.2f} GB".replace(".", ",")
    if b >= 1048576:
        return f"{b / 1048576:.1f} MB".replace(".", ",")
    return f"{max(0, round(b / 1024))} KB"


def _group_by_server(rows: list[dict]) -> list[dict]:
    """Stato dei siti diviso per server, i server con piu' siti prima."""
    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(r.get("server") or "", []).append(r)
    return [{"server": k, "sites": v, "problems": sum(1 for x in v if x["problems"])}
            for k, v in sorted(groups.items(), key=lambda t: (-len(t[1]), t[0]))]


async def _site_stats(s, sites: list) -> list[dict]:
    """Una riga per sito: versioni, PHP, dominio, peso e crescita, spazio scrivibile, file del core."""
    if not sites:
        return []
    ids = [x.id for x in sites]
    since = (datetime.now() - timedelta(days=45)).date()
    fails = await failed_by_site(s, ids)
    # server di ogni sito (IP del dominio) col nome dato in Impostazioni, per dividere la sezione
    servers: dict[int, str] = {}
    try:
        from redis.asyncio import from_url
        from .config import settings as _settings
        from .servers import key_host, key_ip, machine_key, server_of, site_hostname
        from .settings_store import get_operational_settings
        _prefs = await get_operational_settings()
        labels = _prefs.get("server_labels") or {}
        split = set(_prefs.get("server_split") or [])
        r = from_url(_settings.REDIS_URL)
        try:
            ip_of = {x.id: (await server_of(r, x.url) or "?") for x in sites}
        finally:
            await r.aclose()
        # IP diviso per macchina ("IP|nome"): ogni macchina e' un server a parte
        mk = {x.id: machine_key(ip_of[x.id], site_hostname(x), split) for x in sites}
        # server con lo STESSO nome = un gruppo solo, con i suoi IP tra parentesi
        ips_by_label: dict[str, set] = {}
        for key in mk.values():
            if labels.get(key):
                ips_by_label.setdefault(labels[key], set()).add(key_ip(key))
        for sid, key in mk.items():
            lab = labels.get(key)
            if lab:
                servers[sid] = f"{lab} ({', '.join(sorted(ips_by_label[lab]))})"
            elif key_host(key):
                servers[sid] = f"{key_host(key)} ({key_ip(key)})"   # macchina divisa e senza nome: il suo nome tecnico
            else:
                servers[sid] = key
    except Exception:  # noqa: BLE001
        pass
    hist: dict[int, list] = {}
    for r in (await s.execute(select(SiteSize).where(SiteSize.site_id.in_(ids), SiteSize.day >= since)
                              .order_by(SiteSize.day))).scalars().all():
        hist.setdefault(r.site_id, []).append(r)
    now = datetime.now(timezone.utc)
    out = []
    for x in sorted(sites, key=lambda z: (z.name or "").lower()):
        diag = x.diag or {}
        rows = hist.get(x.id) or []
        last = rows[-1] if rows else None
        size, growth, growth_days, db = "", "", 0, ""
        if last:
            size, db = _fmt_bytes(last.total), _fmt_bytes(last.db)
            # crescita: rispetto al valore di circa un mese fa (il piu' recente con almeno 30 giorni),
            # oppure al piu' vecchio disponibile; i giorni scritti sono quelli veri
            older = [r for r in rows if (last.day - r.day).days >= 30]
            ref = older[-1] if older else rows[0]
            if ref is not last:
                d = int(last.total) - int(ref.total)
                growth = ("+" if d >= 0 else "−") + _fmt_bytes(abs(d)) if abs(d) >= 1048576 else "stabile"
                growth_days = (last.day - ref.day).days
        elif (diag.get("sizes") or {}).get("total"):
            size, db = _fmt_bytes(diag["sizes"]["total"]), _fmt_bytes(diag["sizes"].get("db"))
        sp = diag.get("space") or {}
        space = ""
        if sp:
            space = (f"almeno {sp.get('tested_mb')} MB" if sp.get("ok") else f"solo {sp.get('written_mb')} MB").replace(".", ",")
        core = diag.get("core") or {}
        core_txt = {"ok": "integri", "issues": "da controllare"}.get(core.get("status"), "")
        dexp = getattr(x, "domain_expires_at", None)
        # problemi: la stessa definizione di dashboard e Stato server
        probs = []
        for pr in site_problems(x, fails.get(x.id), now):
            lab = PROBLEM_LABELS.get(pr["kind"], "")
            probs.append(pr["text"] if pr["kind"] in ("php", "domain", "logs") else f"{lab}: {pr['text']}")
        out.append({
            "name": x.name, "url": (x.url or "").replace("https://", "").replace("http://", "").rstrip("/"),
            "cms_label": "WordPress" if x.cms == "wp" else "Joomla", "core": x.core_current or "",
            "php": x.php_version or "", "php_state": _php_state(x.php_version or ""),
            "domain": getattr(x, "domain_name", "") or "",
            "domain_date": dexp.strftime("%d/%m/%Y") if dexp else "",
            "domain_days": (dexp - now).days if dexp else None,
            "size": size, "growth": growth, "growth_days": growth_days, "db": db,
            "space": space, "space_low": bool(sp) and not sp.get("ok"),
            "core_files": core_txt, "core_issues": core.get("status") == "issues",
            "problems": probs, "server": servers.get(x.id, ""),
        })
    return out


async def gather(period: str, cfg: dict | None = None, scope: str = "") -> dict:
    cfg = cfg or await get_config()
    horizon = int(cfg.get("expiry_horizon_days", 60))
    client = is_client_scope(scope)
    only = "" if (not scope or scope == GLOBAL_KEY) else scope.strip().lower()
    label = scope_label(scope)

    async with SessionLocal() as s:
        sites_rows = (await s.execute(select(Site))).scalars().all()
        by_id = {x.id: x for x in sites_rows}
        if client:
            # perimetro cliente: i siti associati al cliente, e il suo nome nell'intestazione
            cl = await s.get(Client, client_id_of(scope))
            label = cl.name if cl else "Cliente"
            allowed = set((await s.execute(select(ClientSite.site_id).where(
                ClientSite.client_id == client_id_of(scope)))).scalars().all())
        elif only:
            allowed = {x.id for x in sites_rows
                       if any(t.strip().lower() == only or t.strip().lower().startswith(only + "/")
                              for t in (x.tags or "").split(","))}
        else:
            allowed = {x.id for x in sites_rows}

        rows = (await s.execute(
            select(UpdateMonthly).where(UpdateMonthly.period == period).order_by(UpdateMonthly.site_name, UpdateMonthly.ext_name)
        )).scalars().all()
        # filtro rigido: prima con un perimetro vuoto ("or not allowed") passavano TUTTI i siti,
        # e nel report di un cliente senza siti sarebbero finiti i dati degli altri clienti
        rows = [r for r in rows if r.site_id in allowed]

        # --- per sito ---
        sites: dict[int, dict] = {}
        for r in rows:
            if r.ok_count <= 0:
                continue
            site = by_id.get(r.site_id)
            entry = sites.setdefault(r.site_id, {
                "name": r.site_name or (site.name if site else f"Sito {r.site_id}"),
                "url": (site.url if site else "").replace("https://", "").replace("http://", ""),
                "cms_label": "WordPress" if (r.cms or (site.cms if site else "")) in ("wp", "wordpress") else "Joomla",
                "components": [], "total": 0,
            })
            entry["components"].append({
                "name": html.unescape(r.ext_name or r.slug), "type_label": _TYPE_LABELS.get(r.ext_type, (r.ext_type or "").capitalize() or "Altro"),
                "count": r.ok_count, "last_version": r.last_version,
            })
            entry["total"] += r.ok_count
        for e in sites.values():
            e["components"].sort(key=lambda i: (-i["count"], i["name"].lower()))
        site_list = sorted(sites.values(), key=lambda e: (-e["total"], e["name"].lower()))

        # --- classifica componenti ---
        agg: dict[str, dict] = {}
        for r in rows:
            if r.ok_count <= 0:
                continue
            key = (html.unescape(r.ext_name or r.slug)).lower()
            a = agg.setdefault(key, {"name": html.unescape(r.ext_name or r.slug), "count": 0, "sites": 0})
            a["count"] += r.ok_count
            a["sites"] += 1
        top_items = sorted(agg.values(), key=lambda a: (-a["count"], a["name"].lower()))[:15]

        # --- falliti ---
        failed_items = [{"site": r.site_name, "name": html.unescape(r.ext_name or r.slug), "count": r.fail_count}
                        for r in rows if r.fail_count > 0]
        failed_items.sort(key=lambda f: -f["count"])

        # --- sicurezza (stato attuale) ---
        security = {"total": 0, "critical": 0, "high": 0, "medium": 0, "low": 0}
        try:
            # contate sui soli siti del perimetro (prima: su tutti, anche nel report di una cartella)
            q = text("""
                SELECT v.severity, count(*) FROM vuln_matches m
                JOIN vulnerabilities v ON v.id = m.vulnerability_id
                WHERE m.is_vulnerable = true AND m.resolved_at IS NULL AND m.site_id IN :ids
                GROUP BY v.severity
            """).bindparams(bindparam("ids", expanding=True))
            res = (await s.execute(q, {"ids": sorted(allowed)})).all() if allowed else []
            for sev, n in res:
                security["total"] += n
                if (sev or "").lower() in security:
                    security[(sev or "").lower()] += n
        except Exception:  # noqa: BLE001
            pass

        # --- scadenze ---
        now = datetime.now(timezone.utc)
        limit = now + timedelta(days=horizon)
        domains, seen = [], set()
        for x in sites_rows:
            if only and x.id not in allowed:
                continue
            d = getattr(x, "domain_expires_at", None)
            nm = getattr(x, "domain_name", "") or ""
            if not d or not nm or nm in seen:
                continue
            if d <= limit:
                seen.add(nm)
                domains.append({"name": nm, "date": d.strftime("%d/%m/%Y"), "days": (d - now).days})
        domains.sort(key=lambda d: d["days"])

        lic_q = select(SiteExpiry).where(SiteExpiry.expires_at <= limit).order_by(SiteExpiry.expires_at)
        if client:
            # le licenze sono dell'agenzia (scadenze globali): al cliente solo quelle legate ai suoi siti
            lic_q = lic_q.where(SiteExpiry.site_id.in_(sorted(allowed) or [0]))
        lic_rows = (await s.execute(lic_q)).scalars().all()
        licenses = [{"name": x.name, "provider": x.provider, "date": x.expires_at.strftime("%d/%m/%Y"),
                     "days": (x.expires_at - now).days} for x in lic_rows]

        # --- confronto col mese precedente + andamento ultimi 6 mesi ---
        perimeter = allowed if only else None   # per un cliente "only" e' "client:<id>", quindi i suoi siti
        prev = await month_stats(s, shift_period(period, -1), perimeter)
        trend = []
        for i in range(5, -1, -1):
            trend.append(await month_stats(s, shift_period(period, -i), perimeter))
        top_trend = max([t["updates"] for t in trend] + [1])
        for t in trend:
            t["h"] = max(2, round(t["updates"] * 46 / top_trend))   # altezza barra nel PDF (px)
            t["current"] = t["period"] == period

        site_stats = await _site_stats(s, [x for x in sites_rows if x.id in allowed]) if cfg.get("show_site_stats", True) else []

    total_updates = sum(e["total"] for e in site_list)
    cur_stats = {"updates": total_updates, "failed": sum(f["count"] for f in failed_items),
                 "sites": len(site_list), "components": len(agg)}
    delta = {k: _delta(cur_stats[k], prev[k]) for k in ("updates", "failed", "sites", "components")}
    from .version import __version__ as _ver
    return {
        "cfg": cfg,
        "app_version": _ver,
        "period": period,
        "period_label": period_label(period),
        "scope": scope or GLOBAL_KEY,
        "scope_label": label,
        "is_global": not only,
        "is_client": client,
        "site_stats": site_stats,
        "site_stat_groups": _group_by_server(site_stats),
        "generated_at": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "total_updates": total_updates,
        "total_failed": sum(f["count"] for f in failed_items),
        "sites_touched": len(site_list),
        "distinct_items": len(agg),
        "sites_total": len([x for x in sites_rows if x.id in allowed]),
        "wp_count": sum(1 for x in sites_rows if x.id in allowed and x.cms == "wp"),
        "joomla_count": sum(1 for x in sites_rows if x.id in allowed and x.cms != "wp"),
        "sites": site_list,
        "top_items": top_items,
        "failed_items": failed_items[:25],
        "security": security,
        "domains": domains,
        "licenses": licenses,
        "prev": prev,
        "delta": delta,
        "trend": trend,
    }


# ---------------------------------------------------------------- render
async def render_html(period: str, template: str | None = None, cfg: dict | None = None, scope: str = "") -> str:
    client = is_client_scope(scope)
    cfg = cfg or (await get_client_config() if client else await get_config())
    ctx = await gather(period, cfg, scope)
    ctx["logo"] = await _logo_data_uri()
    tpl = template if template is not None else (await get_client_template() if client else await get_template())
    try:
        return _env.from_string(tpl).render(**ctx)
    except TemplateError as ex:
        log.warning("template report non valido (%s): uso il default", ex)
        return _env.from_string(localize_template(DEFAULT_TEMPLATE)).render(**ctx)


def html_to_pdf(html: str) -> bytes | None:
    """PDF con WeasyPrint. None se la libreria non e' disponibile nell'immagine."""
    try:
        from weasyprint import HTML  # import locale: l'app parte anche senza
        return HTML(string=html, base_url=".").write_pdf()
    except Exception as ex:  # noqa: BLE001
        log.warning("PDF non generato (%s): verra' allegato l'HTML", ex)
        return None


async def build(period: str, scope: str = "") -> tuple[str, bytes | None, str]:
    """Ritorna (html, pdf_bytes|None, filename) per il periodo e la cartella (o il cliente) indicati."""
    cfg = await get_client_config() if is_client_scope(scope) else await get_config()
    html = await render_html(period, cfg=cfg, scope=scope)
    pdf = html_to_pdf(html)
    label = scope_label(scope)
    if is_client_scope(scope):
        async with SessionLocal() as s:
            cl = await s.get(Client, client_id_of(scope))
            label = cl.name if cl else ""
    base = f"report-{period}-{scope_slug(scope, label)}"
    return html, pdf, (f"{base}.pdf" if pdf else f"{base}.html")
