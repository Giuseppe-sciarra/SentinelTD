"""Persist night diagnostics; deliver one digest per chosen channel at local time."""
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import delete, select

from .db import SessionLocal
from .models import AppSetting, NightlyBatch, NightlyItem, Site
from . import notify

log = logging.getLogger("panopticon.worker")
EVENT = "nightly_summary"


def validate_schedule(cfg):
    if not isinstance(cfg.get("send_time"), str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", cfg["send_time"]):
        raise ValueError("Orario non valido: usa HH:MM")
    try:
        ZoneInfo(cfg.get("timezone", ""))
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise ValueError("Fuso orario non valido") from None


def _utc(dt):
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def due(created, now, cfg):
    """Live settings, DST-aware date, and no catch-up/retry before today's send time."""
    validate_schedule(cfg)
    zone = ZoneInfo(cfg["timezone"])
    start, local = _utc(created).astimezone(zone), _utc(now).astimezone(zone)
    hh, mm = map(int, cfg["send_time"].split(":"))
    clock = hh * 60 + mm
    target_day = start.date() + timedelta(days=int(start.hour * 60 + start.minute > clock))
    return local.date() >= target_day and local.hour * 60 + local.minute >= clock


async def create_batch(ids, now):
    key = _utc(now).strftime("%Y-%m-%d")
    async with SessionLocal() as s:
        if await s.get(NightlyBatch, key):
            return key, False
        s.add(NightlyBatch(id=key, created_at=now))
        await s.flush()
        s.add_all([NightlyItem(batch_id=key, site_id=sid) for sid in ids])
        await s.commit()
    return key, True


async def complete_item(s, batch, sid, payload):
    row = await s.get(NightlyItem, (batch, sid))
    if row:
        row.payload_json = json.dumps(payload, ensure_ascii=False)
        row.completed = True


def site_payload(site, diag, server_key="", error=""):
    return {"site_id": site.id, "site_name": site.name, "site_url": site.url,
            "folder": site.tags or site.group or "", "server_key": server_key or site.url,
            "server": diag.get("server") or {}, "space": diag.get("space") or {},
            "logs": (diag.get("sizes") or {}).get("big_logs") or [],
            "core": diag.get("core") or {}, "error": error, "checked_at": diag.get("at") or ""}


def has_issues(p):
    if p.get("error") or p.get("logs") or (p.get("space") and not p["space"].get("ok")) or p.get("core", {}).get("status") == "issues":
        return True
    m = p.get("server") or {}
    loads, cores = m.get("load") or [], m.get("cores") or 0
    return bool((loads and cores and float(loads[0]) / cores > 1.5)
                or (m.get("mem_total") and m.get("mem_available") is not None
                    and (m["mem_total"] - m["mem_available"]) / m["mem_total"] >= .9)
                or (m.get("disk_total") and m.get("disk_free") is not None
                    and (m["disk_total"] - m["disk_free"]) / m["disk_total"] >= .9))


async def nightly_summary_tick(ctx):
    """One minute scheduler; a restart retains reports, snapshots and channel receipts."""
    redis = ctx["redis"]
    token = uuid4().hex
    if not await redis.set("nightly:delivery:lock", token, nx=True, ex=1200):
        return
    try:
        await _deliver(datetime.now(timezone.utc))
    finally:
        # WATCH avoids deleting a newer worker's lock after expiration.
        async with redis.pipeline(transaction=True) as pipe:
            try:
                await pipe.watch("nightly:delivery:lock")
                value = await pipe.get("nightly:delivery:lock")
                if value in (token, token.encode()):
                    pipe.multi()
                    pipe.delete("nightly:delivery:lock")
                    await pipe.execute()
            except Exception:
                pass


async def _deliver(now):
    cfg = await notify.get_config(EVENT)
    if not cfg.get("enabled") or not (cfg.get("email") or cfg.get("telegram")):
        return
    try:
        validate_schedule(cfg)
    except ValueError as ex:
        log.warning("Riepilogo notturno: %s", ex)
        return
    today = now.astimezone(ZoneInfo(cfg["timezone"])).date().isoformat()
    async with SessionLocal() as s:
        batches = (await s.execute(select(NightlyBatch).where(NightlyBatch.closed == False).order_by(NightlyBatch.created_at))).scalars().all()  # noqa: E712
        eligible = []
        for batch in batches:
            if (not batch.ready and now - _utc(batch.created_at) < timedelta(days=1)) or not due(batch.created_at, now, cfg):
                continue
            items = (await s.execute(select(NightlyItem).where(NightlyItem.batch_id == batch.id))).scalars().all()
            # Wait for every site. An interrupted/expired job is reported after 24 h,
            # never followed by an additional notification for late completions.
            if any(not i.completed for i in items) and now - _utc(batch.created_at) < timedelta(days=1):
                continue
            if not batch.context_json:
                entries = []
                for item in items:
                    site = await s.get(Site, item.site_id)
                    if not site or not site.enabled or site.notifications_silenced:
                        continue
                    p = json.loads(item.payload_json) if item.payload_json else {}
                    if not p.get("site_id"):
                        p = site_payload(site, {}, error="Controllo non completato")
                    entries.append(p)
                if not entries or (cfg.get("only_problems", True) and not any(has_issues(p) for p in entries)):
                    batch.closed = True
                    continue
                batch.context_json = json.dumps({"entries": entries, "scan_date": batch.id}, ensure_ascii=False)
            eligible.append(batch)
        # Freeze before external delivery: retries retain exactly the same details.
        await s.commit()
        channels = {}
        selected = {}
        for channel in ("email", "telegram"):
            receipt = await s.get(AppSetting, f"nightly:last:{channel}")
            rows = [b for b in eligible if not getattr(b, f"{channel}_sent")]
            if cfg.get(channel) and rows and (not receipt or receipt.value != today):
                channels[channel] = True
                selected[channel] = rows
        if channels:
            chosen = {b.id: b for rows in selected.values() for b in rows}
            entries = {}
            for b in chosen.values():
                for p in json.loads(b.context_json)["entries"]:
                    # Respect mute/removal even if it changed while delivery was pending.
                    site = await s.get(Site, p["site_id"])
                    if site and site.enabled and not site.notifications_silenced:
                        entries[p["site_id"]] = p
            if entries:
                context = {"entries": list(entries.values()), "scan_date": ", ".join(chosen),
                           "date": now.astimezone(ZoneInfo(cfg["timezone"])).strftime("%d/%m/%Y %H:%M"),
                           "timezone": cfg["timezone"]}
                result = await notify.dispatch(EVENT, context, channels={c: bool(channels.get(c)) for c in ("email", "telegram")})
                for channel, rows in selected.items():
                    if result.get(channel):
                        for b in rows:
                            setattr(b, f"{channel}_sent", True)
                        key = f"nightly:last:{channel}"
                        receipt = await s.get(AppSetting, key)
                        if receipt:
                            receipt.value = today
                        else:
                            s.add(AppSetting(key=key, value=today))
            else:
                for b in chosen.values():
                    b.closed = True
        for b in eligible:
            if all(not cfg.get(c) or getattr(b, f"{c}_sent") for c in ("email", "telegram")):
                b.closed = True
        # Keep receipts for 40 days; do not discard undelivered reports.
        old = [b.id for b in (await s.execute(select(NightlyBatch).where(NightlyBatch.closed == True, NightlyBatch.created_at < now - timedelta(days=40)))).scalars()]  # noqa: E712
        if old:
            await s.execute(delete(NightlyItem).where(NightlyItem.batch_id.in_(old)))
            await s.execute(delete(NightlyBatch).where(NightlyBatch.id.in_(old)))
        await s.commit()
