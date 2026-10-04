"""Domain-only WHOIS parsing and reconciliation. No registrant/contact storage."""
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
import json
import re

from .domain_requests import SourceError, page_block_reason

UTC = timezone.utc
EXPIRY_RE = re.compile(
    r"(?im)^\s*(?:registry expiry date|registrar registration expiration date|"
    r"expiration date|expiry date|expire date|expires(?: on)?|paid-till|renewal date)\s*:\s*(.+?)\s*$"
)


def parse_date(value):
    if not value:
        return None
    raw = str(value).strip()
    match = re.search(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?", raw)
    if match:
        try:
            dt = datetime.fromisoformat(match[0].replace("Z", "+00:00"))
            return (dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt).astimezone(UTC)
        except ValueError:
            pass
    # English month names without depending on the process locale.
    months = {name.lower(): i for i, name in enumerate(
        ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"), 1)}
    match = re.fullmatch(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", raw)
    if match and match[1].lower() in months:
        try:
            return datetime(int(match[3]), months[match[1].lower()], int(match[2]), tzinfo=UTC)
        except ValueError:
            return None
    clean = re.sub(r"\s+\([^)]*\)\s*$", "", raw).strip()
    for fmt in ("%d-%b-%Y", "%d-%b-%Y %H:%M:%S %Z", "%d/%m/%Y", "%Y.%m.%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(clean, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def domain_key(value):
    return str(value or "").strip().rstrip(".").lower().encode("idna").decode("ascii")


def validate_whois_domain(text, domain):
    match = re.search(r"(?im)^\s*domain(?: name)?\s*:\s*(\S+)", text)
    if match and domain_key(match[1]) != domain_key(domain):
        raise RuntimeError("WHOIS restituisce un dominio diverso da quello richiesto")


def whois_record_info(text):
    # .it contacts also have 'Last Update': only the DOMAIN block is relevant.
    top = re.split(r"(?im)^\s*(?:registrant|admin contact|technical contacts?|registrar)\s*$", text, maxsplit=1)[0]
    match = re.search(r"(?im)^\s*(?:last update|updated date|last updated(?: on)?|modified|changed)\s*:\s*(.+)$", top)
    statuses = re.findall(r"(?im)^\s*(?:domain status|status)\s*:\s*([^\n]+)", top)
    return {"updated_at": parse_date(match[1]) if match else None,
            "statuses": [x.strip().split("https://")[0].strip()[:100] for x in statuses][:12]}


class WhoisHTML(HTMLParser):
    """Keep visible fields/time attributes, never interpret scripts as WHOIS."""
    def __init__(self, body):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.headings = []
        self.heading = None
        self.skip = 0
        self.time_skip = False
        self.feed(body)

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self.skip += 1
        if self.skip:
            return
        if tag in ("h1", "h2", "h3", "div", "p", "dt", "dd", "tr", "td", "th", "li", "br", "pre"):
            self.parts.append("\n")
        if tag == "h1":
            self.heading = []
        if tag == "time":
            stamp = dict(attrs).get("datetime")
            if parse_date(stamp):
                self.parts.append(stamp)
                self.time_skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag == "time":
            self.time_skip = False
        if tag == "h1" and self.heading is not None:
            self.headings.append("".join(self.heading).strip())
            self.heading = None
        if tag in ("h1", "h2", "h3", "div", "p", "dt", "dd", "tr", "td", "th", "li", "pre"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip and not self.time_skip:
            self.parts.append(data)
            if self.heading is not None:
                self.heading.append(data)

    def text(self):
        return "\n".join(line.strip() for line in "".join(self.parts).splitlines() if line.strip())


def parse_who_is(body, domain):
    page = WhoisHTML(body)
    text = page.text()
    if not any(domain_key(h) == domain_key(domain) for h in page.headings):
        blocked = page_block_reason(text)
        if blocked:
            raise SourceError(blocked)
        mismatch = any(re.fullmatch(r"[\w.-]+\.[A-Za-z]{2,}", h) for h in page.headings)
        if mismatch:
            raise SourceError("who.is: pagina relativa a un altro dominio")
        raise SourceError("who.is: pagina temporaneamente assente o incompleta", transient=True)
    # Restrict date fields to Important Dates: certificate and similar domains
    # elsewhere in the page must never supply an expiry.
    block = re.search(r"(?is)(?:^|\n)Important Dates\n(.*?)(?=\n(?:Nameservers|Domain Status|Similar Domains|Raw WHOIS Data|About WHOIS)\b|$)", text)
    if not block:
        raise RuntimeError("who.is non espone la data di scadenza")
    def field(label):
        match = re.search(r"(?im)^" + label + r"\s*:?\s*\n?\s*([^\n]+)$", block[1])
        return parse_date(match[1]) if match else None
    expiry = field("Expires")
    if not expiry:
        raise RuntimeError("who.is non espone la data di scadenza")
    reg = re.search(r"(?im)^Registrar\n([^\n]+)$", text)
    ns = re.search(r"(?is)(?:^|\n)Nameservers\n(.*?)(?=\n(?:Domain Status|Similar Domains|Raw WHOIS Data|About WHOIS)\b|$)", text)
    names = re.findall(r"(?im)^((?:[a-z0-9-]+\.)+[a-z][a-z0-9-]*)\s*$", ns[1]) if ns else []
    status = re.search(r"(?is)(?:^|\n)Domain Status\n(.*?)(?=\n(?:Similar Domains|Raw WHOIS Data|About WHOIS)\b|$)", text)
    info = {"updated_at": field("Updated"), "snapshot_at": field("WHOIS data last fetched"),
            "registrar": reg[1].strip()[:200] if reg else "",
            "nameservers": ", ".join(sorted(set(names))[:8])[:500],
            "statuses": status[1].strip().splitlines()[:12] if status else []}
    return expiry, info


def reconcile(candidates, now):
    """Use record changes, then live authority or comparable snapshot age.

    Expiry length is never a freshness signal. Missing timestamps never count
    as old timestamps, and third-party copies are not independent votes.
    """
    if not candidates:
        raise RuntimeError("Nessuna fonte espone una scadenza utilizzabile")
    ordered = sorted(candidates, key=lambda x: x["priority"])
    for item in ordered:
        for key in ("updated_at", "snapshot_at"):
            stamp = item.get(key)
            if stamp and stamp > now + timedelta(days=1):
                item[key] = None
    days = {x["expiry"].date() for x in ordered}
    if len(days) == 1:
        return ordered[0], "agreement" if len(ordered) > 1 else "single_source", ""
    # A newer domain record can supersede a live registry response. Require
    # timestamps on ALL conflicting records; no inference from a larger date.
    if all(x.get("updated_at") for x in ordered):
        latest = max(x["updated_at"] for x in ordered)
        newest = [x for x in ordered if x["updated_at"] == latest]
        if len({x["expiry"].date() for x in newest}) == 1:
            return newest[0], "newer_record", ""
    live = [x for x in ordered if x["authoritative"]]
    if live:
        # Unknown/older web snapshots cannot override a current registry.
        if len({x["expiry"].date() for x in live}) == 1:
            primary = live[0]
            newer = [x for x in ordered if x.get("updated_at") and primary.get("updated_at")
                     and x["updated_at"] > primary["updated_at"]]
            if newer and len({x["expiry"].date() for x in newer}) == 1:
                return newer[0], "newer_record", ""
            return primary, "live_registry", ""
    if all(x.get("snapshot_at") for x in ordered):
        latest = max(x["snapshot_at"] for x in ordered)
        newest = [x for x in ordered if x["snapshot_at"] == latest]
        if len({x["expiry"].date() for x in newest}) == 1:
            return newest[0], "newer_snapshot", ""
    dates = "; ".join(f'{x["source"]}: {x["expiry"].date().isoformat()}' for x in ordered)
    return ordered[0], "conflict", ("Fonti discordanti senza un dato piu' recente verificabile: " + dates)[:500]


def renewal_pending(summary, domain, expiry, now):
    """Recognize the documented .it grace state, never fabricate +1 year."""
    if not domain.endswith(".it") or not expiry or not summary:
        return False
    expiry = expiry.replace(tzinfo=UTC) if expiry.tzinfo is None else expiry
    age = (now.date() - expiry.date()).days
    if not 0 <= age < 15 or summary.get("warning"):
        return False
    chosen = next((x for x in summary.get("sources", []) if x.get("selected")), {})
    statuses = " ".join(chosen.get("statuses") or []).lower().replace(" ", "")
    stamp = parse_date(chosen.get("snapshot_at"))
    fresh = chosen.get("authoritative") or (stamp and timedelta(0) <= now - stamp <= timedelta(days=2))
    return bool(fresh and "autorenewperiod" in statuses and "inactive" not in statuses
                and "pendingdelete" not in statuses and "notrenewed" not in statuses)


def load_summary(raw):
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def site_renewal_pending(site, now=None):
    return renewal_pending(load_summary(getattr(site, "domain_check_details", "")),
        getattr(site, "domain_name", "") or "", getattr(site, "domain_expires_at", None), now or datetime.now(UTC))
