"""Cosa non va in un sito: UNA definizione per tutto il pannello (dashboard, Stato server,
report). Un aggiornamento fallito conta solo finche' e' ANCORA fallito (Extension.update_failed_at,
tolto quando poi riesce): niente avvisi rimasti indietro dallo storico."""
from datetime import datetime, timezone

KINDS = ("offline", "space", "failed", "logs", "php", "domain")
LABELS = {"offline": "offline", "space": "spazio quasi esaurito", "failed": "aggiornamenti falliti",
          "logs": "log grandi", "php": "PHP fuori supporto", "domain": "dominio scaduto"}


def site_problems(site, failed_names: list[str] | None = None, now: datetime | None = None) -> list[dict]:
    from .report import _php_state
    now = now or datetime.now(timezone.utc)
    out = []
    if not site.enabled:
        return out
    if site.status != "ok":
        out.append({"kind": "offline", "text": (site.error or "non risponde")[:160]})
    diag = site.diag or {}
    sp = diag.get("space") or {}
    if sp and not sp.get("ok"):
        out.append({"kind": "space", "text": f"solo {sp.get('written_mb')} MB liberi".replace(".", ",")})
    # i log grandi NON sono un problema da elencare (troppo rumore): restano la notifica
    # "Spazio e log del sito" e la diagnostica del sito
    names = failed_names or []
    if names:
        shown = ", ".join(names[:3]) + (f" e altri {len(names) - 3}" if len(names) > 3 else "")
        out.append({"kind": "failed", "text": f"non aggiornati: {shown}"})
    php = (site.php_version or "").split("-")[0]
    if php and _php_state(php) == "fuori supporto":
        out.append({"kind": "php", "text": f"PHP {php} fuori supporto"})
    dexp = getattr(site, "domain_expires_at", None)
    if dexp is not None and (dexp - now).days < 0:
        out.append({"kind": "domain", "text": f"dominio scaduto da {-(dexp - now).days} giorni"})
    return out


async def failed_by_site(s, site_ids: list[int] | None = None) -> dict[int, list[str]]:
    """Nomi dei componenti ancora falliti, per sito."""
    from sqlalchemy import select
    from .models import Extension
    q = select(Extension).where(Extension.update_failed_at.is_not(None))
    if site_ids is not None:
        q = q.where(Extension.site_id.in_(site_ids or [0]))
    out: dict[int, list[str]] = {}
    for e in (await s.execute(q)).scalars().all():
        out.setdefault(e.site_id, []).append(e.name or e.slug)
    return out


def server_label(key: str, labels: dict) -> str:
    """Nome dato al server in Impostazioni, se c'e'."""
    return str((labels or {}).get(key) or "").strip()
