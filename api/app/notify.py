"""
Engine notifiche di Sentinel TD.

Ogni EVENTO ha: canali (email/telegram) attivabili, un oggetto e un corpo email (HTML),
un corpo Telegram (HTML-mode di Telegram), e un set di VARIABILI documentate. I template
sono Jinja2, editabili dal pannello e salvati in app_settings (chiave "notif:<evento>");
se non c'e' nulla di salvato valgono i default qui sotto (che sono anche il "ripristina").

Il worker chiama dispatch(event, ctx): l'engine carica la config, renderizza e invia sui
canali attivi. Le variabili "ricche" (tabelle/liste gia' formattate) evitano all'utente di
scrivere cicli Jinja: {{ updates_table }} per l'email, {{ updates_lines }} per Telegram.
"""
import html
import json
import logging
from typing import Any

from jinja2 import Environment, BaseLoader, TemplateError

from .db import SessionLocal
from .models import AppSetting
from .email import send_report
from .telegram import send_telegram
from .config import settings

from .i18n import DEFAULT_LANGUAGE, normalize_language, t, template as localize_template

log = logging.getLogger("notify")

# ------------------------------------------------------------------ eventi
EVENTS: dict[str, dict[str, Any]] = {
    "site_report": {
        "label": "Report update per sito",
        "desc": "Inviato al termine degli update di un sito (riusciti e falliti).",
        "channels": {"email": True, "telegram": False},
        "vars": {
            "site_name": "Nome del sito", "site_url": "URL del sito", "cms": "WordPress / Joomla",
            "folder": "Cartella del sito (es. Clienti / Flash Factory)",
            "outcome_icon": "Icona dell'esito (✅ ⚠️ ❌)", "outcome_text": "Esito in breve (es. 13 aggiornati)",
            "status_box": "Riquadro controllo home (HTML)", "report_table": "Tabella degli update, problemi in cima (HTML)",
            "panel_link": "Link al sito in Sentinel (HTML)",
            "ok_count": "Update riusciti", "failed_count": "Update falliti", "date": "Data e ora",
            "updates_table": "Tabella HTML degli update (formato precedente)",
            "updates_lines": "Elenco testuale degli update (per Telegram)",
            "results": "Lista grezza [{name, from, to, ok, manual, held, error}] per template avanzati",
            "manual_count": "Prodotti da aggiornare a mano (licenza)",
            "visual": "Controllo home prima/dopo: {status: ok|warn|ko|na, message, diff}",
        },
        "subject": "[Sentinel] {{ outcome_icon }} {{ site_name }} — {{ outcome_text }}{% if folder %} · {{ folder }}{% endif %}",
        "email": """<div style="font-family:system-ui,Segoe UI,Roboto,sans-serif;color:#222;max-width:640px">
  <h2 style="margin:0 0 4px">{{ outcome_icon }} {{ site_name }}</h2>
  <div style="color:#666;margin-bottom:{% if folder %}2px{% else %}14px{% endif %}">{{ cms }} &middot; <a href="{{ site_url }}">{{ site_url }}</a></div>
  {% if folder %}<div style="color:#666;margin-bottom:14px">📁 {{ folder }}</div>{% endif %}
  {{ status_box }}
  {{ report_table }}
  {% if panel_link %}<p style="margin-top:14px">{{ panel_link }}</p>{% endif %}
  <p style="color:#999;font-size:12px;margin-top:16px">Sentinel TD &middot; {{ date }}</p>
</div>""",
        "telegram": "{{ outcome_icon }} <b>{{ site_name }}</b> — {{ outcome_text }}\n{% if folder %}📁 {{ folder }}\n{% endif %}{{ updates_lines }}",
    },
    "update_failed": {
        "label": "Update fallito",
        "desc": "Gli aggiornamenti non riusciti di un sito, in un solo messaggio, ciascuno col suo motivo.",
        "channels": {"email": False, "telegram": True},
        "vars": {"site_name": "Nome del sito", "site_url": "URL", "folder": "Cartella del sito",
                 "failures_head": "Riga di sintesi (es. 2 aggiornamenti non riusciti)",
                 "failures_lines": "Elenco per Telegram: un elemento per riga, col motivo sotto",
                 "failures_html": "Elenco per email (HTML)",
                 "item": "Primo elemento fallito (formato precedente)", "reason": "Motivo del primo (formato precedente)",
                 "date": "Data e ora"},
        "subject": "[Sentinel] ❌ {{ site_name }} — {{ failures_head }}{% if folder %} · {{ folder }}{% endif %}",
        "email": """<div style="font-family:system-ui,Segoe UI,Roboto,sans-serif;color:#222;max-width:640px">
  <h2 style="margin:0 0 4px">❌ {{ site_name }}</h2>
  <div style="color:#666;margin-bottom:{% if folder %}2px{% else %}14px{% endif %}"><a href="{{ site_url }}">{{ site_url }}</a></div>
  {% if folder %}<div style="color:#666;margin-bottom:14px">📁 {{ folder }}</div>{% endif %}
  <p style="margin:0 0 8px"><b>{{ failures_head }}</b></p>
  {{ failures_html }}
  <p style="color:#999;font-size:12px;margin-top:16px">Sentinel TD &middot; {{ date }}</p>
</div>""",
        "telegram": "❌ <b>{{ site_name }}</b> — {{ failures_head }}{% if folder %}\n📁 {{ folder }}{% endif %}\n{{ failures_lines }}",
    },
    "core_integrity": {
        "label": "File del core da controllare",
        "desc": "La verifica notturna dei file di WordPress ha trovato file modificati, mancanti o in più (solo quando cambia qualcosa).",
        "channels": {"email": False, "telegram": True},
        "vars": {"site_name": "Nome del sito", "site_url": "URL", "folder": "Cartella del sito",
                 "core_summary": "Sintesi (es. 2 modificati · 1 mancante · 3 in più)",
                 "core_lines": "Elenco dei file per Telegram", "core_html": "Elenco dei file per email (HTML)",
                 "panel_link": "Link al sito in Sentinel (HTML)", "date": "Data e ora"},
        "subject": "[Sentinel] 🛡 {{ site_name }} — file del core da controllare{% if folder %} · {{ folder }}{% endif %}",
        "email": """<div style="font-family:system-ui,Segoe UI,Roboto,sans-serif;color:#222;max-width:640px">
  <h2 style="margin:0 0 4px">🛡 {{ site_name }}</h2>
  <div style="color:#666;margin-bottom:14px"><a href="{{ site_url }}">{{ site_url }}</a>{% if folder %} &middot; 📁 {{ folder }}{% endif %}</div>
  <p style="margin:0 0 8px"><b>File del core da controllare:</b> {{ core_summary }}</p>
  {{ core_html }}
  {% if panel_link %}<p style="margin-top:14px">{{ panel_link }}</p>{% endif %}
  <p style="color:#999;font-size:12px;margin-top:16px">Sentinel TD &middot; {{ date }}</p>
</div>""",
        "telegram": "🛡 <b>{{ site_name }}</b> — file del core da controllare{% if folder %}\n📁 {{ folder }}{% endif %}\n{{ core_summary }}\n{{ core_lines }}",
    },
    "cycle_summary": {
        "label": "Riepilogo ciclo",
        "desc": "Un messaggio a fine ciclo automatico (solo se c'e' stato qualcosa da applicare).",
        "channels": {"email": False, "telegram": True},
        "vars": {"headline": "Riga di sintesi (es. 58 aggiornamenti su 7 siti)",
                 "summary": "Riepilogo completo per Telegram: problemi in cima, una riga per sito, dettaglio richiudibile",
                 "summary_email": "Riepilogo completo per email (HTML)",
                 "applied": "Update applicati", "sites_touched": "Siti coinvolti", "failed": "Update falliti",
                 "ok_lines": "Elenco successi (una riga per sito)", "failed_lines": "Elenco fallimenti", "date": "Data e ora"},
        "subject": "[Sentinel] {{ headline }}",
        "email": """{{ summary_email }}""",
        "telegram": "{{ summary }}",
    },
    "site_offline": {
        "label": "Sito non raggiungibile",
        "desc": "Il sito risulta offline dopo piu' check consecutivi.",
        "channels": {"email": False, "telegram": True},
        "vars": {"site_name": "Nome", "site_url": "URL", "reason": "Errore riscontrato", "attempts": "Check falliti consecutivi", "window_min": "Minuti dal primo fallimento", "date": "Data e ora"},
        "subject": "[Sentinel] {{ site_name }} non raggiungibile",
        "email": """<p>🔴 <b>{{ site_name }}</b> non raggiungibile<br><a href="{{ site_url }}">{{ site_url }}</a></p><p>{{ reason }}</p><p style="color:#666">Confermato dopo {{ attempts }} check in ~{{ window_min }} min</p>""",
        "telegram": "🔴 <b>{{ site_name }}</b> non raggiungibile\n{{ site_url }}\nConfermato dopo {{ attempts }} check in ~{{ window_min }} min\n<i>{{ reason }}</i>",
    },
    "site_online": {
        "label": "Sito di nuovo raggiungibile",
        "desc": "Il sito e' tornato online.",
        "channels": {"email": False, "telegram": True},
        "vars": {"site_name": "Nome", "site_url": "URL", "date": "Data e ora"},
        "subject": "[Sentinel] {{ site_name }} di nuovo online",
        "email": """<p>🟢 <b>{{ site_name }}</b> di nuovo raggiungibile<br><a href="{{ site_url }}">{{ site_url }}</a></p>""",
        "telegram": "🟢 <b>{{ site_name }}</b> di nuovo raggiungibile\n{{ site_url }}",
    },
    "client_report": {
        "label": "Report mensile al cliente",
        "desc": "Email al cliente con il PDF del report dei suoi siti allegato. Solo email, mai Telegram.",
        "channels": {"email": True, "telegram": False},
        "vars": {
            "client_name": "Nome del cliente", "period_label": "Mese del report (es. settembre 2026)",
            "company": "Ragione sociale nel report", "sites_total": "Siti del cliente",
            "sites_names": "Nomi dei siti del cliente", "total_updates": "Aggiornamenti applicati nel mese",
            "sites_touched": "Siti aggiornati nel mese", "attachment": "Nome del file allegato", "date": "Data e ora di generazione",
        },
        "subject": "Report manutenzione {{ period_label }} — {{ client_name }}",
        "email": """<div style="font-family:system-ui,Segoe UI,Roboto,sans-serif;color:#222;max-width:640px;line-height:1.55">
  <p>Gentile {{ client_name }},</p>
  <p>in allegato trova il report delle attività di manutenzione svolte nel mese di <b>{{ period_label }}</b>
  {% if sites_total == 1 %}sul sito {{ sites_names }}{% else %}sui suoi {{ sites_total }} siti ({{ sites_names }}){% endif %}.</p>
  <p>{% if total_updates %}Nel periodo abbiamo applicato <b>{{ total_updates }}</b> aggiornamenti{% if sites_total > 1 %} su {{ sites_touched }} siti{% endif %}, tra componenti, temi e sistema.{% else %}Nel periodo non è stato necessario applicare aggiornamenti.{% endif %}
  Nel report trova anche lo stato dei siti: versioni, scadenza del dominio, spazio occupato e sicurezza.</p>
  <p>Per qualsiasi domanda restiamo a disposizione.</p>
  <p>Cordiali saluti,<br>{{ company }}</p>
</div>""",
        "telegram": "",
    },
    "monthly_report": {
        "label": "Report mensile",
        "desc": "Email di fine mese con il PDF del report allegato.",
        "channels": {"email": True, "telegram": False},
        "vars": {
            "period_label": "Mese del report (es. Agosto 2026)", "company": "Ragione sociale nel report",
            "total_updates": "Aggiornamenti applicati nel mese", "total_failed": "Aggiornamenti non riusciti",
            "sites_touched": "Siti aggiornati", "sites_total": "Siti monitorati",
            "distinct_items": "Componenti diversi aggiornati", "top_lines": "Top componenti (elenco testuale)",
            "attachment": "Nome del file allegato", "date": "Data e ora di generazione",
            "scope_label": "Ambito del report (Tutti i siti oppure il nome della cartella)",
        },
        "subject": "{{ company }} — report manutenzione {{ period_label }}{% if scope_label and not scope_global %} · {{ scope_label }}{% endif %}",
        "email": """<div style="font-family:system-ui,Segoe UI,Roboto,sans-serif;color:#222;max-width:640px">
  <h2 style="margin:0 0 6px">Report manutenzione — {{ period_label }}</h2>
  {% if scope_label and not scope_global %}<div style="color:#666;margin:-4px 0 10px">{{ scope_label }}</div>{% endif %}
  <p>Nel periodo sono stati applicati <b>{{ total_updates }}</b> aggiornamenti su <b>{{ sites_touched }}</b> siti
  ({{ distinct_items }} componenti diversi){% if total_failed %}, con {{ total_failed }} tentativi non riusciti e ritentati automaticamente{% endif %}.</p>
  {% if top_lines %}<p style="color:#555">{{ top_lines }}</p>{% endif %}
  <p>Il dettaglio completo, sito per sito, è nel PDF allegato (<b>{{ attachment }}</b>).</p>
  <p style="color:#999;font-size:12px;margin-top:18px">Sentinel TD &middot; {{ date }}</p>
</div>""",
        "telegram": "📄 <b>Report {{ period_label }}</b>\n{{ total_updates }} aggiornamenti su {{ sites_touched }} siti\nPDF inviato per email.",
    },
    "vuln_alert": {
        "label": "Vulnerabilita' rilevata",
        "desc": "Il Centro Sicurezza ha trovato una nuova vulnerabilita' su un'estensione installata.",
        "channels": {"email": False, "telegram": True},
        "vars": {"site_name": "Nome", "site_url": "URL", "ext_name": "Estensione", "ext_version": "Versione installata",
                 "cve_id": "CVE", "title": "Titolo", "severity": "Gravita'", "cvss": "Punteggio CVSS", "exploited": "Sfruttata attivamente (si/no)",
                 "version_fixed": "Versione che corregge", "url": "Link alla scheda", "date": "Data e ora"},
        "subject": "[Sentinel] {{ severity|upper }}: {{ cve_id }} su {{ site_name }}",
        "email": """<p>🚨 <b>{{ severity|upper }}</b> {{ cve_id }} — {{ title }}</p><p>🌐 <b>{{ site_name }}</b> · {{ ext_name }} <code>{{ ext_version }}</code>{% if version_fixed %}<br>Corretta in: <b>{{ version_fixed }}</b>{% endif %}</p><p><a href="{{ url }}">{{ url }}</a></p>""",
        "telegram": "🚨 <b>{{ severity|upper }}</b> {{ cve_id }}\n{{ title }}\n🌐 <b>{{ site_name }}</b>\n🧩 {{ ext_name }} <code>{{ ext_version }}</code>{% if version_fixed %}\n✅ corretta in {{ version_fixed }}{% endif %}{% if exploited == 'si' %}\n⚠️ sfruttata attivamente{% endif %}\n🔗 {{ url }}",
    },
    "expiry_alert": {
        "label": "Scadenza / rinnovo",
        "desc": "Dominio oppure plugin/tema/licenza in scadenza. Le soglie si configurano in Impostazioni.",
        "channels": {"email": False, "telegram": True},
        "vars": {
            "site_name": "Sito/siti collegati (solo domini)", "site_url": "URL sito (solo domini)",
            "kind": "Tipo di scadenza", "item": "Dominio, plugin, tema o licenza",
            "platform": "WordPress / Joomla / Entrambi", "provider": "Fornitore",
            "expires_on": "Data di scadenza", "days": "Giorni mancanti",
            "notes": "Note", "date": "Data e ora dell'avviso",
        },
        "subject": "[Sentinel] {{ item }} scade tra {{ days }} giorni",
        "email": """<div style="font-family:Inter,Arial,sans-serif;background:#f5f7fb;padding:24px;color:#172033">
  <div style="max-width:640px;margin:0 auto;background:#ffffff;border:1px solid #e7eaf0;border-radius:14px;overflow:hidden">
    <div style="padding:18px 22px;background:#111827;color:#fff"><div style="font-size:12px;opacity:.7">SENTINEL TD</div><div style="font-size:20px;font-weight:700;margin-top:3px">⏳ Scadenza in arrivo</div></div>
    <div style="padding:22px">
      <div style="font-size:22px;font-weight:750;margin-bottom:8px">{{ item }}</div>
      <div style="font-size:15px;color:#596174;margin-bottom:18px">{{ kind }}{% if platform %} · {{ platform }}{% endif %}</div>
      <div style="padding:14px 16px;border-radius:10px;background:#fff7ed;border:1px solid #fed7aa;margin-bottom:16px">
        Scade il <b>{{ expires_on }}</b> · <b>{{ days }} giorni</b> rimanenti
      </div>
      {% if site_name %}<p style="margin:8px 0"><b>Sito/i:</b> {{ site_name }}</p>{% endif %}
      {% if provider %}<p style="margin:8px 0"><b>Fornitore:</b> {{ provider }}</p>{% endif %}
      {% if notes %}<p style="margin:12px 0;color:#596174">{{ notes }}</p>{% endif %}
      {% if site_url %}<p style="margin:16px 0 0"><a href="{{ site_url }}" style="color:#2563eb">{{ site_url }}</a></p>{% endif %}
    </div>
  </div>
</div>""",
        "telegram": "⏳ <b>{{ item }}</b>\n{{ kind }}{% if platform %} · {{ platform }}{% endif %}\n📅 <b>{{ expires_on }}</b> — tra <b>{{ days }} giorni</b>{% if site_name %}\n🌐 {{ site_name }}{% endif %}{% if provider %}\n🏢 {{ provider }}{% endif %}{% if notes %}\n<i>{{ notes }}</i>{% endif %}",
    },
}

# The dictionaries above stay in the Italian source language. They are localized
# when requested so the notification editor follows the browser language while
# background jobs keep using DEFAULT_UI_LANGUAGE.
def event_meta(event: str, language: str | None = None) -> dict:
    lang = normalize_language(language, DEFAULT_LANGUAGE)
    e = EVENTS[event]
    return {
        "label": localize_template(e["label"], lang),
        "desc": localize_template(e["desc"], lang),
        "vars": {key: t(value, lang) for key, value in e["vars"].items()},
    }


def sample_context(event: str, language: str | None = None) -> dict:
    """Localized application-owned sample values used only by preview/test."""
    lang = normalize_language(language, DEFAULT_LANGUAGE)

    def walk(value):
        if isinstance(value, str):
            return t(value, lang)
        if isinstance(value, list):
            return [walk(x) for x in value]
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        return value

    return walk(SAMPLE[event])

# ------------------------------------------------------------------ dati di esempio (anteprima/test)
SAMPLE: dict[str, dict[str, Any]] = {
    "site_report": {"site_name": "Sito di prova", "site_url": "https://esempio.it", "cms": "WordPress",
                    "results": [{"name": "WooCommerce", "from": "11.0.0", "to": "11.0.1", "ok": True, "error": ""},
                                {"name": "Yoast SEO", "from": "28.3", "to": "28.4", "ok": True, "error": ""},
                                {"name": "ACF", "from": "6.8.7", "to": "6.8.8", "ok": False, "error": "Errore di filesystem"}]},
    "update_failed": {"site_name": "Sito di prova", "site_url": "https://esempio.it", "folder": "Clienti",
                      "failures": [{"name": "Elementor Pro", "from": "4.2.3", "to": "4.3.1", "error": "Il pacchetto non può essere installato. PCLZIP_ERR_BAD_FORMAT"},
                                   {"name": "WordPress core", "from": "6.9.9", "to": "7.1.2", "error": "Il download non è andato a buon fine"}],
                      "item": "Elementor Pro 4.2.3 -> 4.3.1", "reason": "Il pacchetto non può essere installato. PCLZIP_ERR_BAD_FORMAT"},
    "core_integrity": {"site_name": "Sito di prova", "site_url": "https://esempio.it", "folder": "Clienti",
                       "core": {"status": "issues", "version": "7.1.2", "checked": 1987, "modified_count": 1, "missing_count": 0,
                                "extra_count": 2, "modified": ["wp-includes/load.php"], "missing": [],
                                "extra": ["wp-includes/class-wp-old.php", "wp-admin/x.php"]}},
    "cycle_summary": {"applied": 9, "sites_touched": 7, "failed": 1,
                      "ok_lines": "• Sito A: WooCommerce 11.0.0→11.0.1\n• Sito B: Traduzioni (1)\n• Sito C: Yoast 28.3→28.4",
                      "failed_lines": "• Sito D: ACF 6.8.7→6.8.8"},
    "site_offline": {"site_name": "Sito di prova", "site_url": "https://esempio.it", "reason": "HTTP 503", "attempts": 3, "window_min": 12},
    "site_online": {"site_name": "Sito di prova", "site_url": "https://esempio.it"},
    "client_report": {"client_name": "Hotel Esempio", "period_label": "settembre 2026", "company": "Tastiere Digitali",
                      "sites_total": 1, "sites_names": "hotelesempio.it", "total_updates": 14, "sites_touched": 1,
                      "attachment": "report-2026-09-cliente-hotel-esempio.pdf"},
    "monthly_report": {"period_label": "Agosto 2026", "company": "Tastiere Digitali", "total_updates": 128,
                       "total_failed": 2, "sites_touched": 31, "sites_total": 42, "distinct_items": 24,
                       "top_lines": "Più aggiornati: WooCommerce (14), YOOtheme (11), Akeeba Backup (9)",
                       "attachment": "report-2026-08-globale.pdf", "scope_label": "Tutti i siti"},
    "vuln_alert": {"site_name": "Sito di prova", "site_url": "https://esempio.it", "ext_name": "Contact Form 7", "ext_version": "5.9.2",
                   "cve_id": "CVE-2026-12345", "title": "Stored XSS in form fields", "severity": "high", "cvss": 7.5,
                   "exploited": "no", "version_fixed": "5.9.3", "url": "https://www.cve.org/CVERecord?id=CVE-2026-12345"},
    "expiry_alert": {"site_name": "Sito di prova", "site_url": "https://esempio.it", "kind": "Dominio",
                     "item": "esempio.it", "platform": "", "provider": "Registro dominio",
                     "expires_on": "15/10/2026", "days": 30, "notes": "Rinnovo automatico disattivato"},
}

_env = Environment(loader=BaseLoader(), autoescape=False)


def _esc(s: Any) -> str:
    return html.escape(str(s or ""))


# Modelli predefiniti delle versioni precedenti: una copia salvata IDENTICA a uno di questi non
# e' una personalizzazione, e' il vecchio default rimasto nel database -> si usa quello nuovo.
_LEGACY_DEFAULTS = {('cycle_summary', 'subject'): ('[Sentinel] Ciclo completato: {{ applied }} update su {{ sites_touched }} siti',), ('cycle_summary', 'body_email'): ('<h3>Ciclo aggiornamenti completato</h3><p>✅ {{ applied }} update applicati su {{ sites_touched }} siti</p><pre style="font-family:inherit">{{ ok_lines }}</pre>{% if failed %}<p>❌ {{ failed }} falliti</p><pre style="font-family:inherit">{{ failed_lines }}</pre>{% endif %}',), ('cycle_summary', 'body_telegram'): ('📊 <b>Ciclo aggiornamenti completato</b>\n✅ {{ applied }} update applicati su {{ sites_touched }} siti\n{{ ok_lines }}{% if failed %}\n\n❌ {{ failed }} falliti\n{{ failed_lines }}{% endif %}',), ('site_report', 'subject'): ('[Sentinel] {{ site_name }}: {{ ok_count }} aggiornati, {{ failed_count }} falliti', '[Sentinel] {{ outcome_icon }} {{ site_name }} — {{ outcome_text }}'), ('site_report', 'body_email'): ('<div style="font-family:system-ui,Segoe UI,Roboto,sans-serif;color:#222;max-width:640px">\n  <h2 style="margin:0 0 4px">{{ site_name }}</h2>\n  <div style="color:#666;margin-bottom:14px">{{ cms }} &middot; <a href="{{ site_url }}">{{ site_url }}</a></div>\n  {{ updates_table }}\n  <p style="color:#999;font-size:12px;margin-top:16px">Sentinel TD &middot; {{ date }}</p>\n</div>', '<div style="font-family:system-ui,Segoe UI,Roboto,sans-serif;color:#222;max-width:640px">\n  <h2 style="margin:0 0 4px">{{ outcome_icon }} {{ site_name }}</h2>\n  <div style="color:#666;margin-bottom:14px">{{ cms }} &middot; <a href="{{ site_url }}">{{ site_url }}</a></div>\n  {{ status_box }}\n  {{ report_table }}\n  {% if panel_link %}<p style="margin-top:14px">{{ panel_link }}</p>{% endif %}\n  <p style="color:#999;font-size:12px;margin-top:16px">Sentinel TD &middot; {{ date }}</p>\n</div>'), ('site_report', 'body_telegram'): ('🛠 <b>{{ site_name }}</b> — {{ ok_count }} aggiornati, {{ failed_count }} falliti\n{{ updates_lines }}', '{{ outcome_icon }} <b>{{ site_name }}</b> — {{ outcome_text }}\n{{ updates_lines }}'), ('update_failed', 'subject'): ('[Sentinel] Update fallito su {{ site_name }}: {{ item }}',), ('update_failed', 'body_email'): ('<p><b>{{ site_name }}</b> — update fallito: <b>{{ item }}</b></p><p style="color:#b00020">{{ reason }}</p><p><a href="{{ site_url }}">{{ site_url }}</a></p>',), ('update_failed', 'body_telegram'): ('❌ <b>{{ site_name }}</b> — update fallito: {{ item }}\n<i>{{ reason }}</i>',)}


def default_config(event: str, language: str | None = None) -> dict:
    lang = normalize_language(language, DEFAULT_LANGUAGE)
    e = EVENTS[event]
    return {
        "enabled": True,
        "email": e["channels"]["email"],
        "telegram": e["channels"]["telegram"],
        "subject": localize_template(e["subject"], lang),
        "body_email": localize_template(e["email"], lang),
        "body_telegram": localize_template(e["telegram"], lang),
    }


async def get_config(event: str, language: str | None = None) -> dict:
    """Effective config: localized default overridden by a saved custom template."""
    cfg = default_config(event, language)
    try:
        async with SessionLocal() as s:
            row = await s.get(AppSetting, f"notif:{event}")
            if row and row.value:
                saved = json.loads(row.value)
                for k in cfg:
                    if k in saved:
                        olds = _LEGACY_DEFAULTS.get((event, k)) or ()
                        if saved[k] in olds or saved[k] in tuple(localize_template(o, language) for o in olds):
                            continue        # vecchio predefinito, non una personalizzazione
                        cfg[k] = saved[k]
    except Exception as ex:  # noqa: BLE001
        log.warning("notify: config %s non leggibile: %s", event, ex)
    return cfg


# --------------------------------------------------------------------------
# Riepiloghi leggibili: report per sito e riepilogo di ciclo (Telegram + email)
# --------------------------------------------------------------------------
import re as _re_rep



def _short(text, n=34):
    text = str(text or "")
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _major_of(v):
    m = _re_rep.match(r"\s*(\d+)", str(v or ""))
    return int(m.group(1)) if m else 0


def _is_core(name):
    return str(name or "") in ("WordPress core", "Joomla core")


# Prodotti dove un salto di versione principale puo' davvero rompere un sito. Altri plugin
# (Yoast passa da 27 a 28 ogni mese) cambiano il primo numero senza che significhi nulla:
# segnalarli toglierebbe valore alla freccia proprio dove serve.
_MAJOR_WATCH = ("elementor", "elementor pro", "woocommerce", "yootheme", "yootheme pro", "advanced custom fields",
                "advanced custom fields pro", "acf", "wpml", "wpml multilingual cms", "polylang", "divi", "joomla", "wordpress")


def _is_major_jump(r):
    frm, to = str(r.get("from") or ""), str(r.get("to") or "")
    name = str(r.get("name") or "").strip().lower()
    return bool(frm and to) and _major_of(to) > _major_of(frm) and name in _MAJOR_WATCH


def _highlights(oks):
    """Le versioni che contano davvero: core e salti di versione principale."""
    out = []
    for r in oks:
        if _is_core(r.get("name")):
            out.append(f"{str(r.get('name')).replace(' core', '')} {r.get('to') or ''}".strip())
        elif _is_major_jump(r):
            out.append(f"{_short(r.get('name'), 26)} {r.get('to') or ''} ⬆️")
    if not out and len(oks) == 1:
        out.append(f"{_short(oks[0].get('name'), 30)} {oks[0].get('to') or ''}".strip())
    return out[:3]


def _site_outcome(results, visual, lang):
    ok = [r for r in results if r.get("ok")]
    failed = [r for r in results if not r.get("ok") and not r.get("manual") and not r.get("held")]
    waiting = [r for r in results if r.get("manual") or r.get("held")]
    vis = (visual or {}).get("status")
    n_ok = len(ok)
    upd = f"{n_ok} {t('aggiornati', lang) if n_ok != 1 else t('aggiornato', lang)}"
    if failed:
        return "❌", f"{len(failed)} {t('falliti', lang) if len(failed) != 1 else t('fallito', lang)} " \
                     f"{t('su', lang)} {len(results)}"
    if vis in ("ko",):
        return "🛑", f"{upd}, {t('home con errori', lang)}"
    if vis in ("warn",):
        return "⚠️", f"{upd}, {t('home da controllare', lang)}"
    if waiting:
        return "⏸", f"{upd}, {len(waiting)} {t('in attesa', lang)}"
    return "✅", upd


def _upd_on(applied, sites, lang, bold=False):
    """"58 aggiornamenti su 7 siti" con singolare e plurale giusti."""
    a = f"<b>{applied}</b>" if bold else str(applied)
    n = f"<b>{sites}</b>" if bold else str(sites)
    return (f"{a} {t('aggiornamento su', lang) if applied == 1 else t('aggiornamenti su', lang)} "
            f"{n} {t('sito', lang) if sites == 1 else t('siti', lang)}")


def _cycle_numbers(rep):
    return {
        "applied": sum(len(x.get("ok") or []) for x in rep),
        "sites": len([x for x in rep if x.get("ok")]),
        "failed": sum(len(x.get("failed") or []) for x in rep),
        "held": sum(len(x.get("held") or []) for x in rep),
        "manual": sum(len(x.get("manual") or []) for x in rep),
        "vis_ok": sum(1 for x in rep if (x.get("visual") or {}).get("status") == "ok"),
        "vis_bad": [x for x in rep if (x.get("visual") or {}).get("status") in ("warn", "ko")],
    }


def _cycle_headline(rep, lang):
    n = _cycle_numbers(rep)
    icon = "❌" if n["failed"] else ("⚠️" if (n["vis_bad"] or n["held"] or n["manual"]) else "✅")
    text = _upd_on(n["applied"], n["sites"], lang)
    if n["failed"]:
        text += f", {n['failed']} {t('falliti', lang) if n['failed'] != 1 else t('fallito', lang)}"
    return f"{icon} {t('Ciclo', lang)} {text}"


def _problem_lines(rep, lang, html=True):
    """Una riga per ogni cosa da guardare: home, falliti, in attesa, a mano."""
    E = _esc if html else (lambda x: str(x or ""))
    out = []
    for x in rep:
        site = f"<b>{E(x['site'])}</b>" if html else x["site"]
        if x.get("folder"):
            site += f" · <i>{E(x['folder'])}</i>" if html else f" · {x['folder']}"
        v = x.get("visual") or {}
        if v.get("status") in ("warn", "ko"):
            msg = t(str(v.get("message") or ""), lang)
            out.append((site, E(msg.split(":")[0] if v.get("status") == "warn" else msg), ""))
        for f in x.get("failed") or []:
            out.append((site, f"{E(_short(f.get('name'), 32))} {t('non aggiornato', lang)}",
                        E(_short(t(str(f.get("error") or ""), lang), 110))))
        for h in x.get("held") or []:
            out.append((site, f"{E(_short(h.get('name'), 32))} {t('in attesa del Pro', lang)}", ""))
        for m in x.get("manual") or []:
            out.append((site, f"{E(_short(m.get('name'), 32))} {t('da aggiornare a mano', lang)}", ""))
    return out


def _cycle_telegram(rep, lang, panel_url, when):
    """Riepilogo di ciclo per Telegram.

    In testa i numeri e cio' che va guardato; poi UN BLOCCO PER SITO: nome in grassetto e,
    in una cornice che lo separa dal sito successivo, un elemento aggiornato per riga.
    Le cornici con piu' di 6 righe partono chiuse e si aprono al tocco. I blocchi sono
    separati da una riga vuota: se il messaggio e' troppo lungo, l'invio lo divide li'.
    """
    n = _cycle_numbers(rep)
    problems = _problem_lines(rep, lang)
    head = [f"🔄 <b>{t('Ciclo aggiornamenti', lang)} · {_esc(when)}</b>"]
    line = "✅ " + _upd_on(n["applied"], n["sites"], lang, bold=True)
    if not problems:
        line += f" · {t('tutto ok', lang)}"
    head.append(line)
    extra = []
    if n["failed"]:
        extra.append(f"❌ {n['failed']} {t('falliti', lang) if n['failed'] != 1 else t('fallito', lang)}")
    if n["held"]:
        extra.append(f"⏸ {n['held']} {t('in attesa', lang)}")
    if n["manual"]:
        extra.append(f"🔧 {n['manual']} {t('a mano', lang)}")
    if extra:
        head.append(" · ".join(extra))
    if n["vis_ok"] or n["vis_bad"]:
        head.append(f"🖼 {t('Home', lang)}: {n['vis_ok']} ok" +
                    (f" · ⚠️ {len(n['vis_bad'])} {t('da guardare', lang)}" if n["vis_bad"] else ""))
    blocks = ["\n".join(head)]

    if problems:
        pl = [f"⚠️ <b>{t('Da guardare', lang)}</b>"]
        for site, what, why in problems:
            pl.append(f"• {site} — {what}")
            if why:
                pl.append(f"   <i>{why}</i>")
        blocks.append("\n".join(pl))

    MAX_LINES = 60
    for x in sorted([x for x in rep if x.get("ok")], key=lambda x: -len(x["ok"])):
        # prima il core, poi le nuove versioni principali: si vedono anche con la cornice chiusa
        items = sorted(x["ok"], key=lambda r: 0 if _is_core(r.get("name")) else (1 if _is_major_jump(r) else 2))
        rows = []
        for r in items[:MAX_LINES]:
            row = f"{_esc(_short(r.get('name'), 40))}  {_esc(r.get('from') or '')} → {_esc(r.get('to') or '')}"
            if _is_core(r.get("name")) or _is_major_jump(r):
                row = f"<b>{row}</b>" + (" ⬆️" if _is_major_jump(r) else "")
            rows.append(row)
        if len(items) > MAX_LINES:
            rows.append(f"<i>…{t('e altri', lang)} {len(items) - MAX_LINES}</i>")
        cnt = len(items)
        title = (f"🌐 <b>{_esc(x['site'])}</b> · {cnt} "
                 f"{t('aggiornamento', lang) if cnt == 1 else t('aggiornamenti', lang)}")
        if x.get("folder"):
            title += f"\n📁 {_esc(x['folder'])}"
        tag = "<blockquote expandable>" if cnt > 6 else "<blockquote>"
        blocks.append(title + "\n" + tag + "\n".join(rows) + "</blockquote>")

    if panel_url:
        blocks.append(f"🔗 <a href=\"{_esc(panel_url)}/#/history\">{t('Apri in Sentinel', lang)}</a>")
    return "\n\n".join(blocks)


def _cycle_email(rep, lang, panel_url, when):
    n = _cycle_numbers(rep)
    td = 'style="padding:6px 10px;border-bottom:1px solid #eee"'
    h = ['<div style="font-family:system-ui,Segoe UI,Roboto,sans-serif;color:#222;max-width:680px">',
         f'<h2 style="margin:0 0 6px">{t("Ciclo aggiornamenti", lang)} · {_esc(when)}</h2>',
         f'<p style="margin:0 0 4px">✅ {_upd_on(n["applied"], n["sites"], lang, bold=True)}</p>']
    bits = []
    if n["failed"]:
        bits.append(f'❌ {n["failed"]} {t("falliti", lang) if n["failed"] != 1 else t("fallito", lang)}')
    if n["held"]:
        bits.append(f'⏸ {n["held"]} {t("in attesa", lang)}')
    if n["manual"]:
        bits.append(f'🔧 {n["manual"]} {t("a mano", lang)}')
    if n["vis_ok"] or n["vis_bad"]:
        bits.append(f'🖼 {t("Home", lang)}: {n["vis_ok"]} ok' + (f' · ⚠️ {len(n["vis_bad"])} {t("da guardare", lang)}' if n["vis_bad"] else ""))
    if bits:
        h.append(f'<p style="margin:0 0 14px;color:#555">{" · ".join(bits)}</p>')
    problems = _problem_lines(rep, lang)
    if problems:
        h.append('<div style="margin:0 0 16px;padding:10px 14px;border:1px solid #f0c36d;background:#fff8e6;border-radius:8px">')
        h.append(f'<b>⚠️ {t("Da guardare", lang)}</b><ul style="margin:6px 0 0;padding-left:18px">')
        for site, what, why in problems:
            h.append(f'<li>{site} — {what}' + (f'<br><span style="color:#888">{why}</span>' if why else '') + '</li>')
        h.append('</ul></div>')
    h.append('<table style="border-collapse:collapse;width:100%;font-size:14px">')
    for x in sorted([x for x in rep if x.get("ok")], key=lambda x: -len(x["ok"])):
        link = (f' <a href="{_esc(panel_url)}/#/site/{int(x.get("id") or 0)}" style="font-weight:400;font-size:12px">'
                f'{t("apri", lang)}</a>') if panel_url and x.get("id") else ""
        folder = (f'<br><span style="color:#888;font-weight:400;font-size:12px">📁 {_esc(x["folder"])}</span>'
                  if x.get("folder") else "")
        h.append(f'<tr><td colspan="2" style="padding:14px 10px 6px;font-weight:600;border-bottom:2px solid #ddd">'
                 f'{_esc(x["site"])} <span style="color:#888;font-weight:400">· {len(x["ok"])}</span>{link}{folder}</td></tr>')
        for r in x["ok"]:
            name = _esc(r.get("name"))
            if _is_core(r.get("name")) or _is_major_jump(r):
                name = f"<b>{name}</b>" + (' <span style="color:#b26a00">⬆ ' + t("nuova versione principale", lang) + '</span>' if _is_major_jump(r) else "")
            h.append(f'<tr><td {td}>{name}</td><td {td} style="font-family:monospace;white-space:nowrap">'
                     f'{_esc(r.get("from") or "")} &rarr; {_esc(r.get("to") or "")}</td></tr>')
    h.append('</table>')
    if panel_url:
        h.append(f'<p style="margin-top:16px"><a href="{_esc(panel_url)}/#/history">{t("Apri in Sentinel", lang)}</a></p>')
    h.append(f'<p style="color:#999;font-size:12px;margin-top:16px">Sentinel TD &middot; {_esc(when)}</p></div>')
    return "".join(h)


def _site_report_extras(out, lang):
    """Variabili nuove del report per sito: esito, riquadro home, tabella con i problemi in cima."""
    results = out.get("results") or []
    vis = out.get("visual") or {}
    out["outcome_icon"], out["outcome_text"] = _site_outcome(results, vis, lang)
    box = ""
    if vis.get("status"):
        icon, color, bg = {"ok": ("✅", "#1a7f4b", "#eef8f2"), "warn": ("⚠️", "#b26a00", "#fff8e6"),
                           "ko": ("🛑", "#b00020", "#fdeeee"), "na": ("ℹ️", "#666", "#f6f7f9")}.get(vis["status"], ("ℹ️", "#666", "#f6f7f9"))
        extra = (" " + t("Le istantanee prima e dopo sono allegate.", lang)) if vis["status"] in ("warn", "ko") else ""
        box = (f'<p style="margin:0 0 14px;padding:10px 12px;border-radius:8px;background:{bg};color:{color}">'
               f'{icon} <b>{t("Controllo home", lang)}:</b> {_esc(t(str(vis.get("message") or ""), lang))}{_esc(extra)}</p>')
    out["status_box"] = box
    td = 'style="padding:6px 10px;border-bottom:1px solid #eee"'
    def rank(r):
        if r.get("ok"):
            return 3
        return 0 if not (r.get("manual") or r.get("held")) else 1
    rows = []
    for r in sorted(results, key=rank):
        name, frm, to = _esc(r.get("name")), _esc(r.get("from")), _esc(r.get("to"))
        if r.get("ok"):
            if _is_core(r.get("name")) or _is_major_jump(r):
                name = f"<b>{name}</b>" + (' <span style="color:#b26a00;font-size:12px">⬆ ' + t("nuova versione principale", lang) + '</span>' if _is_major_jump(r) else "")
            badge = '<span style="color:#1a7f4b;font-weight:600">' + t("aggiornato", lang) + "</span>"
        elif r.get("held"):
            badge = '<span style="color:#b26a00;font-weight:600">' + t("in attesa", lang) + '</span> <span style="color:#888">' + _esc(t(str(r.get("error") or ""), lang)) + "</span>"
        elif r.get("manual"):
            badge = '<span style="color:#b26a00;font-weight:600">' + t("da aggiornare a mano", lang) + '</span> <span style="color:#888">' + _esc(t(str(r.get("error") or ""), lang)) + "</span>"
        else:
            badge = '<span style="color:#b00020;font-weight:600">' + t("fallito", lang) + '</span> <span style="color:#888">' + _esc(t(str(r.get("error") or ""), lang)) + "</span>"
        ver = f"{frm} &rarr; {to}" if to and to != frm else frm
        rows.append(f'<tr><td {td}>{name}</td><td {td} style="font-family:monospace;white-space:nowrap">{ver}</td><td {td}>{badge}</td></tr>')
    out["report_table"] = ('<table style="border-collapse:collapse;width:100%;font-size:14px"><thead><tr style="text-align:left;color:#888;font-size:12px">'
                           f'<th style="padding:6px 10px">{t("Elemento", lang)}</th><th style="padding:6px 10px">{t("Versione", lang)}</th>'
                           f'<th style="padding:6px 10px">{t("Esito", lang)}</th></tr></thead><tbody>{"".join(rows)}</tbody></table>')
    panel, sid = str(out.get("panel_url") or "").rstrip("/"), out.get("site_id")
    out["panel_link"] = f'<a href="{_esc(panel)}/#/site/{int(sid)}">{t("Apri in Sentinel", lang)}</a>' if panel and sid else ""


def _failures_vars(out: dict, lang: str) -> None:
    """Avviso di fallimento: un solo messaggio per sito, un elemento per riga col motivo."""
    fl = [f for f in (out.get("failures") or []) if isinstance(f, dict)]
    if not fl and out.get("item"):
        # chiamata nel formato precedente (un elemento solo)
        fl = [{"name": str(out.get("item")), "from": "", "to": "", "error": str(out.get("reason") or "")}]
    n = len(fl)
    out["failed_count"] = n
    out["failures_head"] = (f"{n} {t('aggiornamento non riuscito', lang)}" if n == 1
                            else f"{n} {t('aggiornamenti non riusciti', lang)}")
    tg, em = [], []
    for f in fl:
        ver = f"{f.get('from') or ''} → {f.get('to') or '?'}" if (f.get("from") or f.get("to")) else ""
        why = t(str(f.get("error") or ""), lang)
        tg.append(f"• <b>{_esc(f.get('name'))}</b> {_esc(ver)}".rstrip() + (f"\n   <i>{_esc(why)}</i>" if why else ""))
        em.append(f'<li style="margin-bottom:8px"><b>{_esc(f.get("name"))}</b> '
                  f'<span style="font-family:monospace;color:#555">{_esc(ver)}</span>'
                  + (f'<br><span style="color:#b00020">{_esc(why)}</span>' if why else "") + "</li>")
    out["failures_lines"] = "\n".join(tg)
    out["failures_html"] = '<ul style="margin:0;padding-left:18px">' + "".join(em) + "</ul>"


def _core_vars(out: dict, lang: str) -> None:
    """Avviso sui file del core: sintesi ed elenco dei file (i modificati e i mancanti per primi)."""
    c = out.get("core") or {}
    parts = []
    if c.get("modified_count"):
        parts.append(f"{c['modified_count']} {t('modificati', lang) if c['modified_count'] != 1 else t('modificato', lang)}")
    if c.get("missing_count"):
        parts.append(f"{c['missing_count']} {t('mancanti', lang) if c['missing_count'] != 1 else t('mancante', lang)}")
    if c.get("extra_count"):
        parts.append(f"{c['extra_count']} {t('in più', lang)}")
    out["core_summary"] = " · ".join(parts)
    rows = ([(t("modificato", lang), f) for f in (c.get("modified") or [])]
            + [(t("mancante", lang), f) for f in (c.get("missing") or [])]
            + [(t("in più", lang), f) for f in (c.get("extra") or [])])
    shown = rows[:12]
    out["core_lines"] = "\n".join(f"• <code>{_esc(f)}</code> — {_esc(k)}" for k, f in shown) + (
        f"\n<i>…{t('e altri', lang)} {len(rows) - len(shown)}</i>" if len(rows) > len(shown) else "")
    out["core_html"] = ('<ul style="margin:0;padding-left:18px;font-size:13px">'
                        + "".join(f'<li><code>{_esc(f)}</code> — {_esc(k)}</li>' for k, f in rows[:60]) + "</ul>")
    panel, sid = str(out.get("panel_url") or "").rstrip("/"), out.get("site_id")
    out["panel_link"] = f'<a href="{_esc(panel)}/#/site/{int(sid)}">{t("Apri in Sentinel", lang)}</a>' if panel and sid else ""


def enrich(event: str, ctx: dict, escape: bool = True, language: str | None = None) -> dict:
    """Add rich variables and localize app-owned values, never free-form user content."""
    from datetime import datetime

    lang = normalize_language(language, DEFAULT_LANGUAGE)
    out = dict(ctx)
    out.setdefault("date", datetime.now().strftime("%d/%m/%Y %H:%M"))
    raw_scope = str(ctx.get("scope_label", "") or "").strip()
    out["scope_global"] = raw_scope in {"", "Tutti i siti", "All sites", "Tous les sites", "Alle Websites"}

    # These values are produced by Sentinel itself (not written by the end user).
    for k in ("kind", "platform", "provider", "reason", "scope_label", "period_label", "top_lines", "severity"):
        if k in out and isinstance(out[k], str):
            out[k] = t(out[k], lang)

    if event == "site_report":
        results = out.get("results") or []
        out["ok_count"] = sum(1 for r in results if r.get("ok"))
        # i prodotti da aggiornare a mano (licenza) NON sono falliti
        out["failed_count"] = sum(1 for r in results if not r.get("ok") and not r.get("manual") and not r.get("held"))
        out["manual_count"] = sum(1 for r in results if r.get("manual"))
        rows, lines = [], []
        for r in results:
            name, frm, to = _esc(r.get("name")), _esc(r.get("from")), _esc(r.get("to"))
            if r.get("held"):
                badge = ('<span style="color:#b26a00;font-weight:600">' + t("in attesa", lang) + "</span> "
                         '<span style="color:#888">' + _esc(t(str(r.get("error") or ""), lang)) + "</span>")
                ver = f"{frm} &rarr; {to}" if to and to != frm else frm
                lines.append(f"⏸ {name} {frm}→{to} — " + t("in attesa", lang))
            elif r.get("manual"):
                badge = ('<span style="color:#b26a00;font-weight:600">' + t("da aggiornare a mano", lang) + "</span> "
                         '<span style="color:#888">' + _esc(t(str(r.get("error") or ""), lang)) + "</span>")
                ver = f"{frm} &rarr; {to}" if to and to != frm else frm
                lines.append(f"🔧 {name} {frm}→{to} — " + t("da aggiornare a mano", lang))
            elif r.get("ok"):
                badge = '<span style="color:#1a7f4b;font-weight:600">' + t("aggiornato", lang) + "</span>"
                ver = f"{frm} &rarr; {to}" if to and to != frm else (to or frm)
                lines.append(f"✅ {name} {frm}→{to}" if to and to != frm else f"✅ {name}")
            else:
                error = t(str(r.get("error") or ""), lang)
                badge = f'<span style="color:#b00020;font-weight:600">{t("fallito", lang)}</span> <span style="color:#888">{_esc(error)}</span>'
                ver = frm
                lines.append(f"❌ {name} — {_esc(error)}")
            rows.append(
                f'<tr><td style="padding:6px 10px;border-bottom:1px solid #eee">{name}</td>'
                f'<td style="padding:6px 10px;border-bottom:1px solid #eee;font-family:monospace">{ver}</td>'
                f'<td style="padding:6px 10px;border-bottom:1px solid #eee">{badge}</td></tr>'
            )
        out["updates_table"] = (
            '<table style="border-collapse:collapse;width:100%;font-size:14px"><thead><tr style="text-align:left;color:#888;font-size:12px">'
            f'<th style="padding:6px 10px">{t("Elemento", lang)}</th>'
            f'<th style="padding:6px 10px">{t("Versione", lang)}</th>'
            f'<th style="padding:6px 10px">{t("Esito", lang)}</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>'
        )
        # controllo della home prima/dopo
        vis = out.get("visual") or {}
        if vis.get("status"):
            icon, color = {"ok": ("✅", "#1a7f4b"), "warn": ("⚠️", "#b26a00"), "ko": ("🛑", "#b00020"),
                           "na": ("ℹ️", "#888")}.get(vis["status"], ("ℹ️", "#888"))
            msg = _esc(t(str(vis.get("message") or ""), lang))
            extra = (" " + t("Le istantanee prima e dopo sono allegate.", lang)) if vis["status"] in ("warn", "ko") else ""
            out["updates_table"] += (
                f'<p style="margin:14px 0 0;padding:10px 12px;border-radius:8px;background:#f6f7f9;color:{color}">'
                f'{icon} <b>{t("Controllo home", lang)}:</b> {msg}{_esc(extra)}</p>'
            )
            lines.append(f"\n{icon} {t('Controllo home', lang)}: {msg}")
        out["updates_lines"] = "\n".join(lines)
        out["cms"] = "WordPress" if str(out.get("cms", "")).lower() in ("wp", "wordpress") else ("Joomla" if out.get("cms") else "")
        _site_report_extras(out, lang)

    if event == "update_failed":
        _failures_vars(out, lang)
    if event == "core_integrity":
        _core_vars(out, lang)

    if event == "cycle_summary" and isinstance(out.get("report"), list):
        when = str(out.get("when") or out.get("date") or "")
        panel = str(out.get("panel_url") or "").rstrip("/")
        out["headline"] = _cycle_headline(out["report"], lang)
        out["summary"] = _cycle_telegram(out["report"], lang, panel, when)
        out["summary_email"] = _cycle_email(out["report"], lang, panel, when)

    if not escape:
        return out
    for k in (
        "site_name", "site_url", "item", "reason", "ext_name", "ext_version", "cve_id", "title", "url",
        "ok_lines", "failed_lines", "version_fixed", "kind", "platform", "provider", "expires_on", "notes",
        "period_label", "scope_label", "top_lines", "folder", "client_name", "sites_names",
    ):
        if k in out and isinstance(out[k], str):
            out[k] = _esc(out[k])
    return out


def render(event: str, cfg: dict, ctx: dict, language: str | None = None) -> dict:
    """Return {subject, body_email, body_telegram, error}."""
    lang = normalize_language(language, DEFAULT_LANGUAGE)
    data = enrich(event, ctx, language=lang)
    data_plain = enrich(event, ctx, escape=False, language=lang)
    res = {"subject": "", "body_email": "", "body_telegram": "", "error": ""}
    for key in ("subject", "body_email", "body_telegram"):
        try:
            res[key] = _env.from_string(cfg.get(key) or "").render(**(data_plain if key == "subject" else data))
        except TemplateError as ex:
            res["error"] = f"{key}: {ex}"
            res[key] = ""
    return res


async def dispatch(event: str, ctx: dict, channels: dict | None = None) -> dict:
    """Send a background event using DEFAULT_UI_LANGUAGE or a saved custom template.
    channels: forza l'invio su un canale ({"email": False}) a prescindere dalla configurazione."""
    sent = {"email": False, "telegram": False}
    if event not in EVENTS:
        return sent
    lang = DEFAULT_LANGUAGE
    # gli allegati (es. istantanee prima/dopo) non passano dal template: vanno all'email
    ctx = dict(ctx)
    attachments = ctx.pop("_attachments", None) or []
    cfg = await get_config(event, lang)
    if not cfg.get("enabled", True):
        return sent
    for k, v in (channels or {}).items():
        cfg[k] = bool(v)
    r = render(event, cfg, ctx, lang)
    if r["error"]:
        log.warning("notify %s: template non valido (%s) — uso i default", event, r["error"])
        r = render(event, default_config(event, lang), ctx, lang)
    if cfg.get("email"):
        try:
            await send_report(r["subject"], r["body_email"], attachments=attachments or None)
            sent["email"] = True
        except Exception as ex:  # noqa: BLE001
            log.warning("notify %s: email fallita: %s", event, ex)
    if cfg.get("telegram"):
        try:
            sent["telegram"] = bool(await send_telegram(r["body_telegram"]))
        except Exception as ex:  # noqa: BLE001
            log.warning("notify %s: telegram fallito: %s", event, ex)
    return sent
