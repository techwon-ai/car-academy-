"""Database adapter: the ledger runs unchanged on SQLite (local) or Postgres (online).

The ledger writes SQLite-flavoured SQL with `?` placeholders. For Postgres this
module translates placeholders, adds `RETURNING id` so `lastrowid` works, turns
integer SUM() results back into ints, and serialises writers with an advisory
lock (the equivalent of SQLite's BEGIN IMMEDIATE).
"""

import re
import sqlite3
from decimal import Decimal

try:  # optional: only needed for Postgres
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None

IntegrityError = (sqlite3.IntegrityError,) + ((psycopg.errors.IntegrityError,) if psycopg else ())


class Row(dict):
    """Row usable as row["col"], row[0] and dict(row)."""
    __slots__ = ("_vals",)

    def __init__(self, names, vals):
        super().__init__(zip(names, vals))
        self._vals = vals

    def __getitem__(self, k):
        return self._vals[k] if isinstance(k, int) else dict.__getitem__(self, k)


def _sqlite_row(cursor, values):
    return Row([d[0] for d in cursor.description], values)


def _pg_row_factory(cursor):
    names = [c.name for c in cursor.description] if cursor.description else []

    def make(values):
        return Row(names, tuple(int(v) if isinstance(v, Decimal) and v == v.to_integral_value() else v
                                for v in values))
    return make


class SQLiteDB:
    dialect = "sqlite"

    def __init__(self, path):
        self.raw = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.raw.row_factory = _sqlite_row
        self.raw.execute("PRAGMA foreign_keys = ON")
        self.raw.execute("PRAGMA busy_timeout = 5000")
        if path != ":memory:":
            self.raw.execute("PRAGMA journal_mode = WAL")
        self.user = None

    @property
    def in_transaction(self):
        return self.raw.in_transaction

    def begin(self):
        self.raw.execute("BEGIN IMMEDIATE")

    def commit(self):
        self.raw.execute("COMMIT")

    def rollback(self):
        self.raw.execute("ROLLBACK")

    def execute(self, sql, args=()):
        return self.raw.execute(sql, args)

    def executemany(self, sql, seq):
        return self.raw.executemany(sql, seq)

    def executescript(self, script):
        self.raw.executescript(script)

    def columns(self, table):
        return {r[1] for r in self.raw.execute(f"PRAGMA table_info({table})")}

    def close(self):
        self.raw.close()


class _PGResult:
    def __init__(self, cur, lastrowid=None):
        self._cur, self.lastrowid = cur, lastrowid

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def __iter__(self):
        return iter(self._cur.fetchall())


_INSERT = re.compile(r"^\s*INSERT\s+INTO\s+(\w+)", re.I)
NO_ID_TABLES = {"settings", "app_secrets"}


def _pg_sql(sql):
    sql = sql.replace("%", "%%").replace("?", "%s")
    return sql


class PostgresDB:
    dialect = "postgres"
    WRITE_LOCK = 7310  # arbitrary advisory-lock key for "one writer at a time"

    def __init__(self, url):
        if psycopg is None:
            raise RuntimeError("Postgres support needs: pip install 'psycopg[binary]'")
        self.raw = psycopg.connect(url, autocommit=True, row_factory=_pg_row_factory, connect_timeout=10)
        self._in_tx = False
        self.user = None

    @property
    def in_transaction(self):
        return self._in_tx

    def begin(self):
        self.raw.execute("BEGIN")
        self._in_tx = True
        self.raw.execute("SELECT pg_advisory_xact_lock(%s)", (self.WRITE_LOCK,))

    def commit(self):
        self._in_tx = False
        self.raw.execute("COMMIT")

    def rollback(self):
        self._in_tx = False
        self.raw.execute("ROLLBACK")

    def execute(self, sql, args=()):
        m = _INSERT.match(sql)
        wants_id = m and m.group(1).lower() not in NO_ID_TABLES and "RETURNING" not in sql.upper()
        cur = self.raw.execute(_pg_sql(sql) + (" RETURNING id" if wants_id else ""), tuple(args))
        if wants_id:
            row = cur.fetchone()  # None when ON CONFLICT DO NOTHING skipped the insert
            return _PGResult(cur, row[0] if row else None)
        return _PGResult(cur)

    def executemany(self, sql, seq):
        with self.raw.cursor() as cur:
            cur.executemany(_pg_sql(sql), [tuple(s) for s in seq])

    def executescript(self, script):
        script = (script.replace("INTEGER PRIMARY KEY", "SERIAL PRIMARY KEY")
                        .replace("(datetime('now'))", "(to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'))"))
        for stmt in [s.strip() for s in script.split(";")]:
            if stmt:
                self.raw.execute(stmt)

    def columns(self, table):
        return {r[0] for r in self.raw.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s", (table,))}

    def close(self):
        self.raw.close()


def connect(target):
    """target: a file path / ':memory:' for SQLite, or a postgres:// URL."""
    if str(target).startswith(("postgres://", "postgresql://")):
        return PostgresDB(target)
    return SQLiteDB(target)
