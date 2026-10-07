"""Impostazioni operative modificabili dalla UI (mai segreti/token)."""
import json
import re
from copy import deepcopy

from .config import settings
from .db import SessionLocal
from .models import AppSetting

KEY = "ui:operational_settings"
DEFAULTS = {
    "domain_alert_days": [30, 14, 7],
    "component_alert_days": [30, 14, 7],
    "domain_scan_days": 7,
    "domain_parallel_lookups": 1,  # compatibility: now strictly one domain at a time
    "domain_pause_seconds": 30,
    "domain_source_attempts": 3,
    "expiry_warning_days": 30,
    "expiry_critical_days": 7,
    "screenshot_every_hours": 12,   # ogni quante ore rigenerare l'anteprima dei siti
    "history_retention_days": 400,  # cronologia dettagliata degli update (report dettagliato)
    "log_retention_days": 30,       # registro degli eventi (pagina Registro): giorni conservati
    "log_max_rows": 20000,          # registro degli eventi: tetto massimo di righe, oltre si cancellano le piu' vecchie
    "domain_decision_days": 60,     # quanti giorni prima chiedere "si rinnova o no?"
    "domain_alert_norenew": 1,      # 1 = avvisa anche per i domini da NON rinnovare
    "status_check_attempts": settings.STATUS_CHECK_ATTEMPTS,
    "status_check_retry_seconds": settings.STATUS_CHECK_RETRY_SECONDS,
    "offline_alert_minutes": 5,     # avvisa "non raggiungibile" solo dopo N minuti di errori continui (0 = subito)
    "email_report_mode": "site",    # "site" = un'email per ogni sito; "cycle" = un riepilogo unico per ciclo
    "server_parallel": 1,           # siti in contemporanea sullo stesso server
    "server_pause_seconds": 30,     # riposo del server dopo un sito dove si e' installato/aggiornato qualcosa
    "server_item_pause_seconds": 5, # pausa tra un aggiornamento e l'altro sullo stesso sito (server col freno)
    "connector_auto_update": True,  # ogni notte installa il connettore nuovo sui siti che ne hanno uno vecchio
    "auto_rollback": True,          # se la home si rompe dopo un aggiornamento, rimette le copie e blocca
    "pre_update_backup": True,      # copia zip di plugin/tema prima di aggiornarlo (serve al ripristino; pesa sull'hosting)
    "visual_after_delay": 30,       # secondi di attesa prima della foto "dopo": su hosting lenti la pagina si assesta
    "visual_retry": True,           # se la home risulta cambiata, seconda foto dopo la stessa attesa: vale la migliore
    "visual_noise": True,           # due foto "prima": la differenza tra loro (video, slider) e' rumore e si sottrae
    "server_labels": {},            # nome dato a ogni server (IP -> nome), mostrato ovunque accanto all'IP
    "server_panels": {},            # pagina Server: IP -> {"url": link al pannello dell'hosting, "note": appunto}
    "server_split": [],             # IP da dividere per macchina (nome macchina del connettore): piu' macchine dietro un IP
    "server_limited": [],           # server (IP) con il freno; di base nessuno, si lavora come sempre
}


def _days_list(value, fallback):
    try:
        vals = sorted({int(x) for x in value if 1 <= int(x) <= 3650}, reverse=True)
        return vals[:8] or list(fallback)
    except Exception:
        return list(fallback)


def normalize(data: dict | None) -> dict:
    src = data or {}
    out = deepcopy(DEFAULTS)
    out["domain_alert_days"] = _days_list(src.get("domain_alert_days", out["domain_alert_days"]), DEFAULTS["domain_alert_days"])
    out["component_alert_days"] = _days_list(src.get("component_alert_days", out["component_alert_days"]), DEFAULTS["component_alert_days"])
    for key, lo, hi in (
        ("domain_scan_days", 1, 90),
        ("domain_parallel_lookups", 1, 1),
        ("domain_pause_seconds", 5, 600),
        ("domain_source_attempts", 1, 3),
        ("expiry_warning_days", 1, 3650),
        ("expiry_critical_days", 1, 3650),
        ("screenshot_every_hours", 1, 720),
        ("history_retention_days", 7, 3650),
        ("log_retention_days", 1, 3650),
        ("log_max_rows", 500, 1000000),
        ("domain_decision_days", 0, 3650),
        ("domain_alert_norenew", 0, 1),
        ("status_check_attempts", 1, 5),
        ("status_check_retry_seconds", 1, 60),
        ("offline_alert_minutes", 0, 120),
        ("server_parallel", 1, 4),
        ("server_pause_seconds", 0, 600),
        ("server_item_pause_seconds", 0, 300),
    ):
        try:
            out[key] = max(lo, min(hi, int(src.get(key, out[key]))))
        except Exception:
            pass
    # (2.9.4 aveva l'elenco opposto, "server_unlimited": non si converte, il default e' cambiato)
    raw = src.get("server_limited", out["server_limited"])
    out["server_limited"] = sorted({str(x).strip() for x in (raw if isinstance(raw, list) else []) if str(x).strip()})[:200]
    mode = str(src.get("email_report_mode", out["email_report_mode"]) or "").strip().lower()
    out["email_report_mode"] = mode if mode in ("site", "cycle") else "site"
    out["connector_auto_update"] = bool(src.get("connector_auto_update", out["connector_auto_update"]))
    out["auto_rollback"] = bool(src.get("auto_rollback", out["auto_rollback"]))
    out["pre_update_backup"] = bool(src.get("pre_update_backup", out["pre_update_backup"]))
    try:
        out["visual_after_delay"] = max(0, min(180, int(src.get("visual_after_delay", out["visual_after_delay"]))))
    except (TypeError, ValueError):
        pass
    out["visual_retry"] = bool(src.get("visual_retry", out["visual_retry"]))
    out["visual_noise"] = bool(src.get("visual_noise", out["visual_noise"]))
    labels = src.get("server_labels", out["server_labels"])
    out["server_labels"] = {str(k)[:160]: str(v).strip()[:60] for k, v in (labels.items() if isinstance(labels, dict) else []) if str(v).strip()}
    rawsplit = src.get("server_split", out["server_split"])
    out["server_split"] = sorted({str(x).strip() for x in (rawsplit if isinstance(rawsplit, list) else []) if str(x).strip()})[:200]
    panels = src.get("server_panels", out["server_panels"])
    out["server_panels"] = {}
    for k, v in (panels.items() if isinstance(panels, dict) else []):
        if not isinstance(v, dict):
            continue
        url = str(v.get("url") or "").strip()[:500]
        note = str(v.get("note") or "").strip()[:300]
        if url and not re.match(r"^https?://", url, re.I):
            url = "https://" + url      # scritto senza schema: si completa
        if url or note:
            out["server_panels"][str(k)[:160]] = {"url": url, "note": note}
    if out["expiry_critical_days"] > out["expiry_warning_days"]:
        out["expiry_critical_days"] = out["expiry_warning_days"]
    return out


async def get_operational_settings() -> dict:
    try:
        async with SessionLocal() as s:
            row = await s.get(AppSetting, KEY)
            if row and row.value:
                return normalize(json.loads(row.value))
    except Exception:
        pass
    return deepcopy(DEFAULTS)


async def save_operational_settings(data: dict) -> dict:
    clean = normalize(data)
    async with SessionLocal() as s:
        row = await s.get(AppSetting, KEY)
        value = json.dumps(clean, separators=(",", ":"))
        if row:
            row.value = value
        else:
            s.add(AppSetting(key=KEY, value=value))
        await s.commit()
    return clean
