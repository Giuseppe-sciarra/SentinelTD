import asyncio
import logging
import re

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text
from .config import settings

log = logging.getLogger("sentinel.db")


class Base(DeclarativeBase):
    pass


engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True, pool_size=10, max_overflow=5)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# advisory lock condiviso: serializza create_all/ALTER tra i vari worker/processi al boot
_MIGRATION_LOCK = 841020260529


class _OnlyNeeded:
    """Esegue solo le modifiche allo schema che servono davvero (2.50.1).

    "ALTER TABLE ... ADD COLUMN IF NOT EXISTS" su una colonna che c'e' gia' non cambia niente ma
    prende comunque il lucchetto esclusivo della tabella, e aspetta che finisca ogni transazione
    aperta su quella tabella. Un ciclo di aggiornamenti in modalita' firewall tiene aperta la sua
    per minuti mentre aspetta il sito: all'avvio il pannello restava fermo su "Waiting for
    application startup" e non si apriva (24 ottobre 2026). Ora le colonne, gli indici e i
    "DROP NOT NULL" gia' a posto si saltano, senza toccare la tabella."""

    _ADD = re.compile(r"^\s*ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+(\w+)", re.I)
    _NULL = re.compile(r"^\s*ALTER\s+TABLE\s+(\w+)\s+ALTER\s+COLUMN\s+(\w+)\s+DROP\s+NOT\s+NULL\s*$", re.I)
    _INDEX = re.compile(r"^\s*CREATE\s+(?:UNIQUE\s+)?INDEX\s+IF\s+NOT\s+EXISTS\s+(\w+)", re.I)

    def __init__(self, conn, columns: set, nullable: set, indexes: set):
        self._conn = conn
        self.columns, self.nullable, self.indexes = columns, nullable, indexes
        self.skipped = 0

    def needed(self, sql: str) -> bool:
        m = self._ADD.match(sql)
        if m:
            return (m.group(1).lower(), m.group(2).lower()) not in self.columns
        m = self._NULL.match(sql)
        if m:
            return (m.group(1).lower(), m.group(2).lower()) not in self.nullable
        m = self._INDEX.match(sql)
        if m:
            return m.group(1).lower() not in self.indexes
        return True

    async def execute(self, stmt, *args, **kw):
        if not self.needed(str(stmt)):
            self.skipped += 1
            return None
        return await self._conn.execute(stmt, *args, **kw)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _lock_busy(ex: Exception) -> bool:
    orig = getattr(ex, "orig", None)
    code = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return code == "55P03" or "lock timeout" in str(ex).lower() or "LockNotAvailable" in type(orig).__name__


async def migrate(eng=None, attempts: int = 360, pause: float = 5.0) -> None:
    """run_migrations con la sua transazione. Se una tabella da modificare e' occupata (un ciclo di
    aggiornamenti in corso), si riprova ogni 5 secondi, fino a 30 minuti, scrivendolo nel log:
    prima l'avvio restava appeso senza dire niente."""
    eng = eng or engine
    for attempt in range(1, attempts + 1):
        try:
            async with eng.begin() as conn:
                await run_migrations(conn)
            return
        except Exception as ex:  # noqa: BLE001
            if not _lock_busy(ex) or attempt == attempts:
                raise
            if attempt == 1 or attempt % 12 == 0:
                log.warning("Avvio: una tabella del database e' occupata da un lavoro in corso (di solito un ciclo "
                            "di aggiornamenti in modalita' firewall); riprovo ogni %d secondi", int(pause))
            await asyncio.sleep(pause)


async def run_migrations(conn) -> None:
    """
    Crea le tabelle mancanti e applica gli ALTER idempotenti (ADD COLUMN IF NOT EXISTS).
    Eseguita SIA dall'API (lifespan) SIA dal worker (on_startup), cosi' i due container
    sono autonomi: chi parte per primo migra, gli altri rieseguono gli ALTER (no-op).
    Va invocata dentro `async with engine.begin() as conn:`.
    """
    from . import models  # noqa: F401  (registra i modelli per create_all)

    # serializza i processi concorrenti (gunicorn -w 2 + worker) sul boot
    await conn.execute(text(f"SELECT pg_advisory_xact_lock({_MIGRATION_LOCK})"))
    # nessuna attesa infinita su un lucchetto: oltre, migrate() riprova (e lo dice nel log)
    await conn.execute(text("SET LOCAL lock_timeout = '30s'"))
    await conn.run_sync(Base.metadata.create_all)

    # cosa c'e' gia': gli ALTER che non cambiano niente non devono toccare le tabelle
    schema = "current_schema()"
    rows = (await conn.execute(text(
        f"SELECT table_name, column_name, is_nullable FROM information_schema.columns WHERE table_schema = {schema}"
    ))).all()
    idx = (await conn.execute(text(f"SELECT indexname FROM pg_indexes WHERE schemaname = {schema}"))).all()
    conn = _OnlyNeeded(conn,
                       {(str(t).lower(), str(c).lower()) for t, c, _ in rows},
                       {(str(t).lower(), str(c).lower()) for t, c, n in rows if str(n).upper() == "YES"},
                       {str(i).lower() for (i,) in idx})

    # contatori per categoria
    for col in ("upd_plugins", "tot_plugins", "upd_themes", "tot_themes", "upd_other", "tot_other"):
        await conn.execute(text(f"ALTER TABLE sites ADD COLUMN IF NOT EXISTS {col} INTEGER NOT NULL DEFAULT 0"))
    await conn.execute(text("ALTER TABLE sites ADD COLUMN IF NOT EXISTS auto_update BOOLEAN NOT NULL DEFAULT FALSE"))
    # tag/cartelle multiple sui siti (CSV)
    await conn.execute(text("ALTER TABLE sites ADD COLUMN IF NOT EXISTS tags TEXT NOT NULL DEFAULT ''"))
    # flag: notifica offline gia' inviata con successo per l'episodio corrente
    await conn.execute(text("ALTER TABLE sites ADD COLUMN IF NOT EXISTS offline_notified BOOLEAN NOT NULL DEFAULT FALSE"))
    # silenziamento notifiche per sito + scadenza dominio WHOIS/RDAP
    await conn.execute(text("ALTER TABLE sites ADD COLUMN IF NOT EXISTS notifications_silenced BOOLEAN NOT NULL DEFAULT FALSE"))
    await conn.execute(text("ALTER TABLE sites ADD COLUMN IF NOT EXISTS domain_name VARCHAR(255) NOT NULL DEFAULT ''"))
    await conn.execute(text("ALTER TABLE sites ADD COLUMN IF NOT EXISTS domain_expires_at TIMESTAMPTZ NULL"))
    await conn.execute(text("ALTER TABLE sites ADD COLUMN IF NOT EXISTS domain_checked_at TIMESTAMPTZ NULL"))
    await conn.execute(text("ALTER TABLE sites ADD COLUMN IF NOT EXISTS domain_check_error TEXT NOT NULL DEFAULT ''"))
    await conn.execute(text("ALTER TABLE sites ADD COLUMN IF NOT EXISTS domain_alert_state TEXT NOT NULL DEFAULT ''"))
    await conn.execute(text("ALTER TABLE sites ADD COLUMN IF NOT EXISTS domain_check_details TEXT NOT NULL DEFAULT ''"))
    # registro globale plugin/temi/licenze: sganciato dai singoli siti.
    await conn.execute(text("ALTER TABLE site_expiries ADD COLUMN IF NOT EXISTS category VARCHAR(40) NOT NULL DEFAULT 'Licenza'"))
    await conn.execute(text("ALTER TABLE site_expiries ADD COLUMN IF NOT EXISTS platform VARCHAR(20) NOT NULL DEFAULT 'both'"))
    await conn.execute(text("ALTER TABLE site_expiries ALTER COLUMN site_id DROP NOT NULL"))
    await conn.execute(text("ALTER TABLE site_expiries ADD COLUMN IF NOT EXISTS recur_months INTEGER NOT NULL DEFAULT 0"))

    # Rollup mensile per i report: chiave unica (periodo, sito, tipo, slug) per l'upsert.
    await conn.execute(text("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_update_monthly_key
        ON update_monthly (period, site_id, ext_type, slug)
    """))
    # Sito non raggiungibile: inizio dell'episodio, per avvisare solo dopo N minuti
    await conn.execute(text("ALTER TABLE sites ADD COLUMN IF NOT EXISTS offline_since TIMESTAMPTZ NULL"))
    # 2.34.0: tipo dell'episodio confermato ("site" / "connector"), per l'avviso giusto
    await conn.execute(text("ALTER TABLE sites ADD COLUMN IF NOT EXISTS offline_kind VARCHAR(16) NOT NULL DEFAULT ''"))
    # 2.35.0: dove gira il connettore WP e versione rimasta non in uso (giro notturno)
    await conn.execute(text("ALTER TABLE sites ADD COLUMN IF NOT EXISTS connector_mode VARCHAR(80) NOT NULL DEFAULT ''"))
    await conn.execute(text("ALTER TABLE sites ADD COLUMN IF NOT EXISTS connector_hold VARCHAR(32) NOT NULL DEFAULT ''"))
    # 2.9.0: diagnostica dal connettore (la tabella site_sizes la crea create_all)
    await conn.execute(text("ALTER TABLE sites ADD COLUMN IF NOT EXISTS diag_json TEXT NOT NULL DEFAULT ''"))
    await conn.execute(text("ALTER TABLE sites ADD COLUMN IF NOT EXISTS diag_at TIMESTAMPTZ NULL"))
    await conn.execute(text("ALTER TABLE sites ADD COLUMN IF NOT EXISTS updates_unverified_at TIMESTAMPTZ NULL"))
    await conn.execute(text("ALTER TABLE sites ADD COLUMN IF NOT EXISTS locked_items TEXT NOT NULL DEFAULT ''"))
    await conn.execute(text("ALTER TABLE sites ADD COLUMN IF NOT EXISTS connector_version VARCHAR(32) NOT NULL DEFAULT ''"))
    await conn.execute(text("ALTER TABLE sites ADD COLUMN IF NOT EXISTS extra_headers TEXT NOT NULL DEFAULT ''"))
    await conn.execute(text("ALTER TABLE sites ADD COLUMN IF NOT EXISTS relay_mode BOOLEAN NOT NULL DEFAULT FALSE"))
    await conn.execute(text("ALTER TABLE sites ADD COLUMN IF NOT EXISTS relay_seen_at TIMESTAMPTZ NULL"))
    await conn.execute(text("ALTER TABLE sites ADD COLUMN IF NOT EXISTS shot_error TEXT NOT NULL DEFAULT ''"))
    await conn.execute(text("ALTER TABLE sites ADD COLUMN IF NOT EXISTS shot_blocked_at TIMESTAMPTZ NULL"))
    await conn.execute(text("ALTER TABLE sites ADD COLUMN IF NOT EXISTS server_hostname VARCHAR(80) NOT NULL DEFAULT ''"))
    await conn.execute(text("ALTER TABLE sites ADD COLUMN IF NOT EXISTS shot_attempted_at TIMESTAMPTZ NULL"))
    await conn.execute(text("ALTER TABLE update_history ADD COLUMN IF NOT EXISTS backup_file VARCHAR(255) NOT NULL DEFAULT ''"))
    # traduzioni non applicate: in giallo, si riprova (2.46.0)
    await conn.execute(text("ALTER TABLE update_history ADD COLUMN IF NOT EXISTS retry BOOLEAN NOT NULL DEFAULT FALSE"))

    # Estensioni a licenza da aggiornare a mano (es. Elementor Pro senza pacchetto remoto)
    await conn.execute(text("ALTER TABLE extensions ADD COLUMN IF NOT EXISTS update_manual BOOLEAN NOT NULL DEFAULT FALSE"))

    # Domini: registrar, nameserver e decisione di rinnovo.
    for ddl in (
        "ALTER TABLE sites ADD COLUMN IF NOT EXISTS domain_registrar VARCHAR(200) NOT NULL DEFAULT ''",
        "ALTER TABLE sites ADD COLUMN IF NOT EXISTS domain_nameservers TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE sites ADD COLUMN IF NOT EXISTS domain_renew VARCHAR(8) NOT NULL DEFAULT ''",
        "ALTER TABLE sites ADD COLUMN IF NOT EXISTS domain_renew_note TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE sites ADD COLUMN IF NOT EXISTS domain_renew_at TIMESTAMPTZ NULL",
    ):
        await conn.execute(text(ddl))

    # Versione di partenza nel rollup (per il report dettagliato "dalla X alla Y").
    await conn.execute(text("ALTER TABLE update_monthly ADD COLUMN IF NOT EXISTS first_version VARCHAR(64) NOT NULL DEFAULT ''"))
    done_fv = (await conn.execute(text(
        "SELECT value FROM app_settings WHERE key = 'migr:monthly_first_version_done'"
    ))).scalar()
    if not done_fv:
        # recupera la versione di partenza dallo storico ancora disponibile: per ogni
        # (mese, sito, estensione) la from_version del PRIMO aggiornamento del mese
        await conn.execute(text("""
            UPDATE update_monthly m SET first_version = h.from_version
            FROM (
                SELECT DISTINCT ON (to_char(created_at, 'YYYY-MM'), site_id, ext_type, slug)
                       to_char(created_at, 'YYYY-MM') AS period, site_id, ext_type, slug, from_version
                FROM update_history
                WHERE from_version <> ''
                ORDER BY to_char(created_at, 'YYYY-MM'), site_id, ext_type, slug, created_at ASC
            ) h
            WHERE m.period = h.period AND m.site_id = h.site_id AND m.ext_type = h.ext_type
              AND m.slug = h.slug AND m.first_version = ''
        """))
        await conn.execute(text(
            "INSERT INTO app_settings (key, value) VALUES ('migr:monthly_first_version_done', '1') "
            "ON CONFLICT (key) DO UPDATE SET value = '1'"
        ))

    # Backfill una tantum dallo storico ancora presente (max 7 giorni): evita un report
    # vuoto se il primo invio cade poco dopo l'aggiornamento del sistema.
    done_roll = (await conn.execute(text(
        "SELECT value FROM app_settings WHERE key = 'migr:monthly_backfill_done'"
    ))).scalar()
    if not done_roll:
        await conn.execute(text("""
            INSERT INTO update_monthly (period, site_id, site_name, cms, ext_type, ext_name, slug, ok_count, fail_count, last_version)
            SELECT to_char(created_at, 'YYYY-MM'), site_id, max(site_name), max(cms), ext_type, max(ext_name), slug,
                   count(*) FILTER (WHERE ok), count(*) FILTER (WHERE NOT ok), max(to_version)
            FROM update_history
            GROUP BY to_char(created_at, 'YYYY-MM'), site_id, ext_type, slug
            ON CONFLICT (period, site_id, ext_type, slug) DO NOTHING
        """))
        await conn.execute(text(
            "INSERT INTO app_settings (key, value) VALUES ('migr:monthly_backfill_done', '1') "
            "ON CONFLICT (key) DO UPDATE SET value = '1'"
        ))
    # Migrazione DAVVERO una tantum dei record legacy (eredita il CMS del sito, poi
    # diventa globale). Protetta da un flag in app_settings: senza, queste UPDATE
    # girerebbero a OGNI avvio e cancellerebbero qualsiasi futura associazione
    # scadenza->sito al primo riavvio.
    done = (await conn.execute(text(
        "SELECT value FROM app_settings WHERE key = 'migr:expiries_global_done'"
    ))).scalar()
    if not done:
        await conn.execute(text("""
            UPDATE site_expiries e
            SET platform = CASE WHEN lower(s.cms) IN ('wp','wordpress') THEN 'wp' ELSE 'joomla' END
            FROM sites s
            WHERE e.site_id = s.id AND (e.platform IS NULL OR e.platform = '' OR e.platform = 'both')
        """))
        await conn.execute(text("UPDATE site_expiries SET site_id = NULL WHERE site_id IS NOT NULL"))
        await conn.execute(text(
            "INSERT INTO app_settings (key, value) VALUES ('migr:expiries_global_done', '1') "
            "ON CONFLICT (key) DO UPDATE SET value = '1'"
        ))

    # estensioni: cooldown fallimento update
    await conn.execute(text("ALTER TABLE extensions ADD COLUMN IF NOT EXISTS update_failed_at TIMESTAMPTZ NULL"))
    await conn.execute(text("ALTER TABLE extensions ADD COLUMN IF NOT EXISTS update_failed_version VARCHAR(40) NOT NULL DEFAULT ''"))
    await conn.execute(text("ALTER TABLE extensions ADD COLUMN IF NOT EXISTS dlkey_missing BOOLEAN NOT NULL DEFAULT FALSE"))

    # 2.39.0: scheda cliente (le tabelle dei domini e dei provider le crea create_all)
    await conn.execute(text("ALTER TABLE clients ADD COLUMN IF NOT EXISTS notes TEXT NOT NULL DEFAULT ''"))
    await conn.execute(text("ALTER TABLE clients ADD COLUMN IF NOT EXISTS parent_id INTEGER NULL REFERENCES clients(id) ON DELETE SET NULL"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_clients_parent_id ON clients (parent_id)"))
    await conn.execute(text("ALTER TABLE clients ADD COLUMN IF NOT EXISTS folder VARCHAR(255) NOT NULL DEFAULT ''"))

    # admin_auth: 2FA / passkey
    await conn.execute(text("ALTER TABLE admin_auth ADD COLUMN IF NOT EXISTS totp_secret VARCHAR(64) NOT NULL DEFAULT ''"))
    await conn.execute(text("ALTER TABLE admin_auth ADD COLUMN IF NOT EXISTS totp_enabled BOOLEAN NOT NULL DEFAULT FALSE"))
    await conn.execute(text("ALTER TABLE admin_auth ADD COLUMN IF NOT EXISTS passkeys TEXT NOT NULL DEFAULT '[]'"))
    await conn.execute(text("ALTER TABLE admin_auth ADD COLUMN IF NOT EXISTS totp_last_window BIGINT NOT NULL DEFAULT 0"))


async def get_session() -> AsyncSession:
    async with SessionLocal() as session:
        yield session
