"""Cosa non va in un sito: UNA definizione per tutto il pannello (dashboard,
report). Un aggiornamento fallito conta solo finche' e' ANCORA fallito (Extension.update_failed_at,
tolto quando poi riesce): niente avvisi rimasti indietro dallo storico."""
from .domain_sources import site_renewal_pending
from datetime import datetime, timezone

KINDS = ("offline", "slow", "failed", "php", "domain", "dns", "check")
LABELS = {"offline": "offline", "failed": "aggiornamenti falliti",
          "php": "PHP fuori supporto", "domain": "dominio scaduto", "dns": "verifica DNS non riuscita", "check": "verifica da confermare", "slow": "server lento"}


def site_problems(site, failed_names: list[str] | None = None, now: datetime | None = None) -> list[dict]:
    from .report import _php_state
    now = now or datetime.now(timezone.utc)
    out = []
    if not site.enabled:
        return out
    if site.status == "dns_error":
        out.append({"kind": "dns", "text": (site.error or "verifica DNS non riuscita da Sentinel")[:160]})
    elif site.status == "check_pending":
        out.append({"kind": "check", "text": (site.error or "verifica non conclusa: ricontrollo programmato")[:160]})
    elif site.status == "slow":
        out.append({"kind": "slow", "text": (site.error or "server lento: il connettore non risponde in tempo, la home si'")[:160]})
    elif site.status != "ok":
        out.append({"kind": "offline", "text": (site.error or "non risponde")[:160]})
    names = failed_names or []
    if names:
        shown = ", ".join(names[:3]) + (f" e altri {len(names) - 3}" if len(names) > 3 else "")
        out.append({"kind": "failed", "text": f"non aggiornati: {shown}"})
    php = (site.php_version or "").split("-")[0]
    if php and _php_state(php) == "fuori supporto":
        out.append({"kind": "php", "text": f"PHP {php} fuori supporto"})
    dexp = getattr(site, "domain_expires_at", None)
    if dexp is not None and not getattr(site, "domain_check_error", "") and not site_renewal_pending(site, now) and (dexp - now).days < 0:
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
