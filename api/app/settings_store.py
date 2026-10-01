"""Impostazioni operative modificabili dalla UI (mai segreti/token)."""
import json
from copy import deepcopy

from .db import SessionLocal
from .models import AppSetting

KEY = "ui:operational_settings"
DEFAULTS = {
    "domain_alert_days": [30, 14, 7],
    "component_alert_days": [30, 14, 7],
    "domain_scan_days": 7,
    "domain_parallel_lookups": 4,
    "expiry_warning_days": 30,
    "expiry_critical_days": 7,
    "screenshot_every_hours": 12,   # ogni quante ore rigenerare l'anteprima dei siti
    "history_retention_days": 400,  # cronologia dettagliata degli update (report dettagliato)
    "domain_decision_days": 60,     # quanti giorni prima chiedere "si rinnova o no?"
    "domain_alert_norenew": 1,      # 1 = avvisa anche per i domini da NON rinnovare
    "offline_alert_minutes": 5,     # avvisa "non raggiungibile" solo dopo N minuti di errori continui (0 = subito)
    "email_report_mode": "site",    # "site" = un'email per ogni sito; "cycle" = un riepilogo unico per ciclo
    "server_parallel": 1,           # siti in contemporanea sullo stesso server
    "server_pause_seconds": 30,     # riposo del server dopo un sito dove si e' installato/aggiornato qualcosa
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
        ("domain_parallel_lookups", 1, 8),
        ("expiry_warning_days", 1, 3650),
        ("expiry_critical_days", 1, 3650),
        ("screenshot_every_hours", 1, 720),
        ("history_retention_days", 7, 3650),
        ("domain_decision_days", 0, 3650),
        ("domain_alert_norenew", 0, 1),
        ("offline_alert_minutes", 0, 120),
        ("server_parallel", 1, 4),
        ("server_pause_seconds", 0, 600),
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
