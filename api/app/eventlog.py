"""Registro degli eventi: tutto quello che Sentinel fa o vede, in una tabella sola.

Una riga per evento, con categoria, livello, sito, testo e dettagli. Lo leggono la pagina
Registro (ricerca per testo, sito, categoria, giorno e ora) e nient'altro: non manda notifiche
e non cambia il comportamento del pannello. Si pulisce da solo ogni notte secondo
"log_retention_days" (Impostazioni). Scrivere nel registro non deve MAI rompere il lavoro vero:
ogni errore e' inghiottito.

Categorie:
  availability  sito offline / tornato online / controllo rinviato
  updates       aggiornamenti riusciti, falliti, saltati, in attesa; ripristini
  home          controllo visivo della home dopo gli aggiornamenti
  screenshots   anteprime (fallite, respinte dall'antibot)
  connectors    installazione / distribuzione dei connettori
  domains       scadenze domini, rinnovi
  security      vulnerabilita' trovate
  reports       report mensili / clienti inviati
  system        avvio, pulizie, cicli, errori del pannello
"""
import json
import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger("sentinel.eventlog")

CATEGORIES = ("availability", "updates", "home", "screenshots", "connectors", "domains", "security", "reports", "system")
LEVELS = ("info", "ok", "warn", "error")


async def record(session, category: str, level: str, message: str, *, site=None, site_id: int | None = None,
                 site_name: str = "", details: dict | None = None) -> None:
    """Aggiunge una riga usando la sessione gia' aperta (chi chiama fa il commit)."""
    try:
        from .models import EventLog
        if site is not None:
            site_id = getattr(site, "id", site_id)
            site_name = getattr(site, "name", site_name) or site_name
        session.add(EventLog(
            at=datetime.now(timezone.utc),
            category=category if category in CATEGORIES else "system",
            level=level if level in LEVELS else "info",
            site_id=site_id, site_name=(site_name or "")[:190],
            message=(message or "")[:500],
            details=json.dumps(details, ensure_ascii=False, default=str)[:4000] if details else "",
        ))
    except Exception as ex:  # noqa: BLE001
        log.debug("eventlog: riga non scritta (%s)", ex)


async def record_now(category: str, level: str, message: str, **kw) -> None:
    """Come record(), ma con una sessione propria e commit immediato: per i punti del worker
    che non hanno una sessione aperta."""
    try:
        from .db import SessionLocal
        async with SessionLocal() as s:
            await record(s, category, level, message, **kw)
            await s.commit()
    except Exception as ex:  # noqa: BLE001
        log.debug("eventlog: riga non scritta (%s)", ex)


async def purge(days: int, max_rows: int = 0) -> int:
    """Cancella le righe piu' vecchie di `days` giorni e, se il registro supera `max_rows`
    righe, anche le piu' vecchie oltre il tetto. Ritorna quante righe sono state cancellate."""
    from sqlalchemy import delete, select, func, or_, and_
    from .db import SessionLocal
    from .models import EventLog
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))
    removed = 0
    async with SessionLocal() as s:
        res = await s.execute(delete(EventLog).where(EventLog.at < cutoff))
        removed += int(res.rowcount or 0)
        if max_rows and max_rows > 0:
            total = (await s.execute(select(func.count()).select_from(EventLog))).scalar_one()
            if total > max_rows:
                # riga al confine (la piu' recente tra quelle da togliere): se ne va lei e tutto
                # cio' che e' piu' vecchio, con lo stesso ordine della lettura (at, poi id)
                border = (await s.execute(select(EventLog.at, EventLog.id).order_by(EventLog.at.desc(), EventLog.id.desc())
                                          .offset(max_rows).limit(1))).first()
                if border is not None:
                    b_at, b_id = border
                    res = await s.execute(delete(EventLog).where(
                        or_(EventLog.at < b_at, and_(EventLog.at == b_at, EventLog.id <= b_id))))
                    removed += int(res.rowcount or 0)
        await s.commit()
    return removed
