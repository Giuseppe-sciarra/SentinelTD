"""Persistent, passive outage journal. No HTTP, DNS, cron jobs or notifications."""
from datetime import timezone
from urllib.parse import urlsplit

from sqlalchemy import select
from .eventlog import record as evlog
from .models import OfflineEpisode, Site


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


async def record_availability(session, site):
    if not getattr(site, "_availability_observed", True):
        return
    if site.status not in ("ok", "error") or site.last_checked is None:
        return
    # Error states without a confirmation window (e.g. connector credentials) are
    # not evidence of a confirmed outage. Worker may confirm them subsequently.
    if site.status == "error" and site.offline_since is None:
        return
    with session.no_autoflush:
        if session.get_bind().dialect.name == "postgresql":
            # Lock the same row already written by apply_status, in the same order.
            # An additional advisory lock here would invert locks after autoflush.
            present = (await session.execute(select(Site.id).where(Site.id == site.id).with_for_update())).scalar_one_or_none()
            if present is None:
                return
        episode = (await session.execute(select(OfflineEpisode).where(
            OfflineEpisode.site_id == site.id, OfflineEpisode.ended_at.is_(None)
        ).with_for_update())).scalar_one_or_none()
    checked = utc(site.last_checked)
    if site.status == "ok":
        if episode and checked >= utc(episode.last_failed_at):
            episode.ended_at = checked
            mins = int((checked - utc(episode.confirmed_at)).total_seconds() // 60)
            await evlog(session, "availability", "ok", f"Di nuovo raggiungibile dopo {mins} min", site=site,
                        details={"offline_minutes": mins, "failed_checks": episode.failed_checks})
        return
    if episode:
        # Applying the same result twice (worker notification path) is idempotent.
        if checked > utc(episode.last_failed_at):
            episode.last_failed_at = checked
            episode.failed_checks += 1
            episode.reason = (site.error or "")[:480]
        return
    # A delayed/stale failure must not reopen an episode already closed by a newer check.
    latest_end = (await session.execute(select(OfflineEpisode.ended_at).where(
        OfflineEpisode.site_id == site.id, OfflineEpisode.ended_at.is_not(None)
    ).order_by(OfflineEpisode.ended_at.desc()).limit(1))).scalar_one_or_none()
    if latest_end and checked <= utc(latest_end):
        return
    server = getattr(site, "_status_server", "") or "host:" + (urlsplit(site.url).hostname or "")
    session.add(OfflineEpisode(site_id=site.id, site_name=site.name, site_url=site.url,
                              server_key=server, started_at=min(utc(site.offline_since), checked),
                              confirmed_at=checked, last_failed_at=checked, reason=(site.error or "")[:480]))
    await evlog(session, "availability", "error", f"Sito non raggiungibile: {(site.error or 'nessuna risposta')[:200]}", site=site,
                details={"reason": site.error or "", "since": str(site.offline_since or "")})
    # Make it visible if the worker reuses this transaction before committing.
    await session.flush()
