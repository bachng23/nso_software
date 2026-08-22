"""
Design persistence.

The registry keeps Design IDs and their recipes. Losing that store is not a
cache miss: a clinician who returns tomorrow cannot submit the design to
manufacturing, a vendor cannot pull its package, and a follow-up cannot refit
(ASSUMPTIONS.md P2-1). It therefore has to outlive the process.

Two backends share one schema and one set of SQL statements:

    sqlite:///path/to/nso.db     what runs today
    postgresql://user@host/db    the intended production backend

Selecting one is a URL, so the move to Postgres is a configuration change
rather than a code change. The SQL is deliberately plain -- TEXT payloads, no
JSONB, no dialect-specific types -- so both backends execute the same
statements and the SQLite behaviour that is tested is the behaviour Postgres
will reproduce.

Serialization: entries hold ``DesignRecipe`` objects, nested inside the
candidate structures as well as at the top level. They round-trip through JSON
with a type tag, so the shape a caller gets back is the shape it stored.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Protocol, runtime_checkable
from urllib.parse import urlparse

from dataclasses import asdict

from ..config import get_config
from ..recipe import DesignRecipe

JOB_SERIAL_COUNTER = "job_serial"


# --------------------------------------------------------------------------- #
# JSON round-trip for entries containing DesignRecipe objects
# --------------------------------------------------------------------------- #

_RECIPE_TAG = "__design_recipe__"


class _EntryEncoder(json.JSONEncoder):
    def default(self, obj: Any) -> Any:
        if isinstance(obj, DesignRecipe):
            return {_RECIPE_TAG: asdict(obj)}
        return super().default(obj)


def _decode_entry(obj: Dict[str, Any]) -> Any:
    if _RECIPE_TAG in obj and len(obj) == 1:
        return DesignRecipe(**obj[_RECIPE_TAG])
    return obj


def dumps_entry(entry: Dict[str, Any]) -> str:
    return json.dumps(entry, cls=_EntryEncoder)


def loads_entry(blob: str) -> Dict[str, Any]:
    return json.loads(blob, object_hook=_decode_entry)


# --------------------------------------------------------------------------- #
# Store protocol
# --------------------------------------------------------------------------- #

@runtime_checkable
class DesignStore(Protocol):
    """Persistence contract for design entries."""

    def put(self, design_id: str, payload: Dict[str, Any]) -> None: ...
    def get(self, design_id: str) -> Dict[str, Any]: ...
    def has(self, design_id: str) -> bool: ...
    def next_job_serial(self) -> int: ...


class InMemoryDesignStore:
    """Process-local store. Used by tests and by a bare local run.

    Job serials restart with the process, so two processes hand out the same
    serial -- fine for tests, never acceptable for a real manufacturing job
    (which is why the SQL stores take the serial from the database).
    """

    def __init__(self) -> None:
        self._data: Dict[str, Dict[str, Any]] = {}
        self._serial = get_config().job_serial_start - 1
        self._lock = threading.Lock()

    def put(self, design_id: str, payload: Dict[str, Any]) -> None:
        self._data[design_id] = payload

    def get(self, design_id: str) -> Dict[str, Any]:
        if design_id not in self._data:
            raise KeyError(design_id)
        return self._data[design_id]

    def has(self, design_id: str) -> bool:
        return design_id in self._data

    def next_job_serial(self) -> int:
        with self._lock:
            self._serial += 1
            return self._serial


# --------------------------------------------------------------------------- #
# SQL backends
# --------------------------------------------------------------------------- #

# Portable across SQLite and Postgres: TEXT payload, INTEGER counter, no
# dialect-specific types. Kept as one definition so the two backends cannot
# drift into different schemas.
SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS designs (
        design_id   TEXT PRIMARY KEY,
        payload     TEXT NOT NULL,
        created_at  TEXT NOT NULL,
        updated_at  TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS counters (
        name   TEXT PRIMARY KEY,
        value  BIGINT NOT NULL
    )
    """,
)


class SqlDesignStore:
    """Shared SQL. Subclasses supply a connection and a parameter placeholder."""

    #: DB-API paramstyle marker: "?" for sqlite3, "%s" for psycopg.
    placeholder = "?"

    def _connect(self):                                   # pragma: no cover
        raise NotImplementedError

    # -- helpers --------------------------------------------------------- #
    def _sql(self, statement: str) -> str:
        return statement.replace("?", self.placeholder)

    def _init_schema(self) -> None:
        with self._connect() as conn:
            cur = conn.cursor()
            for statement in SCHEMA:
                cur.execute(statement)
            cur.execute(
                self._sql(
                    "INSERT INTO counters (name, value) VALUES (?, ?) "
                    "ON CONFLICT (name) DO NOTHING"
                ),
                (JOB_SERIAL_COUNTER, get_config().job_serial_start - 1),
            )
            conn.commit()

    # -- DesignStore ----------------------------------------------------- #
    def put(self, design_id: str, payload: Dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        blob = dumps_entry(payload)
        with self._connect() as conn:
            conn.cursor().execute(
                self._sql(
                    "INSERT INTO designs (design_id, payload, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT (design_id) DO UPDATE SET "
                    "payload = EXCLUDED.payload, updated_at = EXCLUDED.updated_at"
                ),
                (design_id, blob, now, now),
            )
            conn.commit()

    def get(self, design_id: str) -> Dict[str, Any]:
        with self._connect() as conn:
            row = conn.cursor().execute(
                self._sql("SELECT payload FROM designs WHERE design_id = ?"),
                (design_id,),
            ).fetchone()
        if row is None:
            raise KeyError(design_id)
        return loads_entry(row[0])

    def has(self, design_id: str) -> bool:
        with self._connect() as conn:
            row = conn.cursor().execute(
                self._sql("SELECT 1 FROM designs WHERE design_id = ?"),
                (design_id,),
            ).fetchone()
        return row is not None

    def next_job_serial(self) -> int:
        """Allocate a serial atomically.

        A single UPDATE ... RETURNING is atomic on both backends, so two
        processes -- or two workers of one server -- cannot be handed the same
        manufacturing serial.
        """
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                self._sql(
                    "UPDATE counters SET value = value + 1 WHERE name = ? "
                    "RETURNING value"
                ),
                (JOB_SERIAL_COUNTER,),
            )
            row = cur.fetchone()
            conn.commit()
        if row is None:                                    # pragma: no cover
            raise RuntimeError("job serial counter row is missing")
        return int(row[0])


class SQLiteDesignStore(SqlDesignStore):
    """SQLite backend. What runs today; also what the tests exercise."""

    placeholder = "?"

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # One shared connection for :memory:, which would otherwise get a fresh
        # empty database per connect.
        self._shared: Optional[sqlite3.Connection] = None
        if self.path == ":memory:":
            self._shared = sqlite3.connect(self.path, check_same_thread=False)
        self._init_schema()

    def _connect(self):
        if self._shared is not None:
            return _NonClosing(self._shared)
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        # WAL lets a reader run while a writer holds the lock, which matters as
        # soon as the API serves more than one request at a time.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("BEGIN IMMEDIATE")
        return conn


class _NonClosing:
    """Context manager that lends a shared connection without closing it."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def __enter__(self) -> sqlite3.Connection:
        return self._conn

    def __exit__(self, *exc) -> None:
        return None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


class PostgresDesignStore(SqlDesignStore):
    """Postgres backend — the intended production store.

    !! NOT YET EXERCISED AGAINST A REAL SERVER !!
    No Postgres instance was available when this was written. The schema and
    every statement are shared with :class:`SQLiteDesignStore`, which is fully
    tested, so the untested surface is the connection handling and the
    paramstyle -- but "should work" is not "works". Run the store test suite
    against a real instance before trusting a manufacturing serial to it.
    """

    placeholder = "%s"

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._init_schema()

    def _connect(self):
        try:
            import psycopg                                 # type: ignore
        except ImportError:                                # pragma: no cover
            try:
                import psycopg2 as psycopg                 # type: ignore
            except ImportError as exc:
                raise RuntimeError(
                    "PostgresDesignStore needs psycopg (or psycopg2) installed"
                ) from exc
        return psycopg.connect(self.dsn)


# --------------------------------------------------------------------------- #
# Selection
# --------------------------------------------------------------------------- #

def store_from_url(url: str) -> DesignStore:
    """Build a store from a database URL.

    Follows the usual three-vs-four slash convention:

    ==============================  =========================
    ``sqlite:///nso.db``            relative to the working dir
    ``sqlite:////var/data/nso.db``  absolute path
    ``sqlite://:memory:``           in-process, nothing on disk
    ``postgresql://user@host/db``   Postgres
    ``memory://`` or empty          in-memory store
    ==============================  =========================
    """
    parsed = urlparse(url)
    scheme = parsed.scheme

    if scheme in ("", "memory"):
        return InMemoryDesignStore()
    if scheme == "sqlite":
        path = url[len("sqlite://"):]
        if path in (":memory:", "/:memory:"):
            return SQLiteDesignStore(":memory:")
        # sqlite:///x.db -> x.db ; sqlite:////tmp/x.db -> /tmp/x.db
        return SQLiteDesignStore(path.lstrip("/") if not path.startswith("//") else path[1:])
    if scheme in ("postgres", "postgresql"):
        return PostgresDesignStore(url)
    raise ValueError(f"unsupported database URL scheme: {scheme!r}")


def default_store() -> DesignStore:
    """The store the application uses.

    ``NSO_DATABASE_URL`` selects it. With nothing configured the store is
    in-memory, which is correct for tests and for a throwaway local run but
    loses every Design ID on restart -- so a deployment must set it.
    """
    url = os.environ.get("NSO_DATABASE_URL", "").strip()
    return store_from_url(url) if url else InMemoryDesignStore()
