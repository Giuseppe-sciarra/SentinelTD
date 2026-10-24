"""Avvio del pannello con un database gia' aggiornato (2.50.1).

Caso reale (24 ottobre 2026): dopo l'aggiornamento il pannello restava fermo su "Waiting for
application startup". Gli "ALTER TABLE ... ADD COLUMN IF NOT EXISTS" prendono il lucchetto esclusivo
della tabella anche quando la colonna c'e' gia', e il worker teneva aperta una transazione sulla
tabella dei siti (ciclo di aggiornamenti in modalita' firewall in attesa del sito).
"""
import re
import unittest

import test_screenshot_schedule  # noqa: F401  (percorso di app e variabili d'ambiente di prova)

from app import db


class Result:
    def __init__(self, rows=(), scalar=None):
        self._rows, self._scalar = list(rows), scalar

    def all(self):
        return self._rows

    def scalar(self):
        return self._scalar


class FakeConn:
    """Registra le istruzioni; risponde alle domande sullo schema con quello che gli si da'."""

    def __init__(self, columns=(), nullable=(), indexes=()):
        self.columns, self.nullable, self.indexes = set(columns), set(nullable), set(indexes)
        self.sql = []

    async def execute(self, stmt, *a, **kw):
        sql = str(stmt)
        self.sql.append(sql)
        if "information_schema.columns" in sql:
            return Result([(t, c, "YES" if (t, c) in self.nullable else "NO") for t, c in self.columns])
        if "pg_indexes" in sql:
            return Result([(i,) for i in self.indexes])
        if "FROM app_settings" in sql:
            return Result(scalar="1")          # migrazioni una tantum gia' fatte
        return Result()

    async def run_sync(self, fn, *a, **kw):
        return None

    def changes(self):
        return [s for s in self.sql if re.match(r"\s*(ALTER|CREATE)\s", s, re.I)]


class StartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_up_to_date_database_is_not_locked(self):
        first = FakeConn()                                   # database vuoto: tutto da fare
        await db.run_migrations(first)
        changes = first.changes()
        self.assertGreater(len(changes), 40)
        cols = set()
        for s in changes:
            m = db._OnlyNeeded._ADD.match(s)
            if m:
                cols.add((m.group(1).lower(), m.group(2).lower()))
        idx = {db._OnlyNeeded._INDEX.match(s).group(1).lower() for s in changes if db._OnlyNeeded._INDEX.match(s)}
        nulls = {(m.group(1).lower(), m.group(2).lower()) for s in changes if (m := db._OnlyNeeded._NULL.match(s))}
        # ogni modifica e' di un tipo che si sa riconoscere come "gia' fatta"
        self.assertEqual(len(cols) + len(idx) + len(nulls), len(changes), [s for s in changes
                         if not (db._OnlyNeeded._ADD.match(s) or db._OnlyNeeded._INDEX.match(s) or db._OnlyNeeded._NULL.match(s))])

        again = FakeConn(columns=cols | nulls, nullable=nulls, indexes=idx)
        await db.run_migrations(again)
        self.assertEqual(again.changes(), [])                # nessun lucchetto sulle tabelle
        self.assertIn("SET LOCAL lock_timeout = '30s'", again.sql)

    async def test_missing_column_is_still_added(self):
        first = FakeConn()
        await db.run_migrations(first)
        cols = {(m.group(1).lower(), m.group(2).lower()) for s in first.changes() if (m := db._OnlyNeeded._ADD.match(s))}
        cols.discard(("sites", "shot_error"))
        conn = FakeConn(columns=cols | {("site_expiries", "site_id")}, indexes={"ux_update_monthly_key", "ix_clients_parent_id"},
                        nullable={("site_expiries", "site_id")})
        await db.run_migrations(conn)
        self.assertEqual([s for s in conn.changes()], ["ALTER TABLE sites ADD COLUMN IF NOT EXISTS shot_error TEXT NOT NULL DEFAULT ''"])


class Busy(Exception):
    def __init__(self):
        super().__init__("canceling statement due to lock timeout")
        self.orig = type("LockNotAvailableError", (Exception,), {"sqlstate": "55P03"})()


class Engine:
    def __init__(self, fail_times):
        self.fail_times, self.calls = fail_times, 0

    def begin(self):
        engine = self

        class Ctx:
            async def __aenter__(self):
                engine.calls += 1
                if engine.calls <= engine.fail_times:
                    raise Busy()
                return FakeConn()

            async def __aexit__(self, *exc):
                return False
        return Ctx()


class RetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_busy_table_is_retried(self):
        eng = Engine(fail_times=2)
        with self.assertLogs("sentinel.db", "WARNING"):
            await db.migrate(eng, pause=0)
        self.assertEqual(eng.calls, 3)

    async def test_other_errors_are_not_retried(self):
        class Broken(Engine):
            def begin(self):
                raise RuntimeError("password authentication failed")
        with self.assertRaises(RuntimeError):
            await db.migrate(Broken(0), pause=0)

    async def test_gives_up_after_the_attempts(self):
        with self.assertRaises(Busy):
            await db.migrate(Engine(fail_times=99), attempts=3, pause=0)


if __name__ == "__main__":
    unittest.main()
