"""SQLite access: schema, startup checks, connections, the write transaction.

Every write command runs inside write_transaction(): BEGIN IMMEDIATE ... COMMIT,
with ROLLBACK on any exception. Connections are per request and never shared.
"""

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .errors import ApiError

APPLICATION_ID = 1145459522  # 0x44465742, "DFWB"
SCHEMA_VERSION = 2
DB_FILENAME = "workbench.sqlite3"
REQUIRED_TABLES = {"profile", "lead", "activity", "audit", "idempotency"}

SCHEMA_STATEMENTS = (
    """CREATE TABLE profile (
      id INTEGER PRIMARY KEY CHECK (id = 1),
      display_label TEXT NOT NULL CHECK (length(display_label) BETWEEN 1 AND 120),
      version INTEGER NOT NULL CHECK (version >= 1),
      created_at_utc TEXT NOT NULL,
      updated_at_utc TEXT NOT NULL
    )""",
    """CREATE TABLE lead (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      label TEXT NOT NULL CHECK (length(label) BETWEEN 1 AND 120),
      country_tag TEXT NOT NULL CHECK (country_tag IN ('NL','other','unknown')),
      work_mode TEXT NOT NULL CHECK (work_mode IN ('remote','hybrid','onsite','unknown')),
      source_channel TEXT NOT NULL CHECK (source_channel IN ('manual','direct','platform','referral','other')),
      source_label TEXT CHECK (source_label IS NULL OR length(source_label) BETWEEN 1 AND 120),
      stage TEXT NOT NULL CHECK (stage IN ('new','researching','contacted')),
      version INTEGER NOT NULL CHECK (version >= 1),
      next_action_text TEXT CHECK (next_action_text IS NULL OR length(next_action_text) BETWEEN 1 AND 240),
      next_action_due TEXT,
      created_at_utc TEXT NOT NULL,
      updated_at_utc TEXT NOT NULL,
      CHECK ((next_action_text IS NULL) = (next_action_due IS NULL)),
      CHECK (stage = 'new' OR next_action_text IS NOT NULL)
    )""",
    """CREATE TABLE activity (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      lead_id INTEGER NOT NULL REFERENCES lead(id),
      kind TEXT NOT NULL CHECK (kind IN ('outreach','note')),
      business_date TEXT NOT NULL,
      summary TEXT NOT NULL CHECK (length(summary) BETWEEN 1 AND 1000),
      recorded_at_utc TEXT NOT NULL
    )""",
    "CREATE INDEX activity_lead ON activity(lead_id, id)",
    """CREATE TABLE audit (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      command_type TEXT NOT NULL,
      entity_type TEXT NOT NULL,
      entity_id INTEGER NOT NULL,
      resulting_version INTEGER NOT NULL,
      recorded_at_utc TEXT NOT NULL,
      from_stage TEXT,
      to_stage TEXT
    )""",
    """CREATE TABLE idempotency (
      key TEXT PRIMARY KEY,
      method TEXT NOT NULL,
      target TEXT NOT NULL,
      request_hash TEXT NOT NULL,
      status_code INTEGER NOT NULL,
      response_json TEXT NOT NULL,
      created_at_utc TEXT NOT NULL
    )""",
)


# Upgrades, applied in order inside one transaction. A fresh database runs
# SCHEMA_STATEMENTS (the v1 schema) and then every upgrade, so new and upgraded
# databases end up with the same schema.
UPGRADES = {
    # v1 (M1) -> v2: leads saved from the bundled catalog remember its stable key.
    # NULL for manual leads; the unique index means one lead per catalog item.
    2: (
        "ALTER TABLE lead ADD COLUMN catalog_key TEXT"
        " CHECK (catalog_key IS NULL OR length(catalog_key) BETWEEN 1 AND 64)",
        "CREATE UNIQUE INDEX lead_catalog_key ON lead(catalog_key) WHERE catalog_key IS NOT NULL",
    ),
}


class DatabaseRejected(Exception):
    """The database file is corrupt or not ours. It has been left untouched."""

    def __init__(self, reason, path):
        super().__init__(reason)
        self.reason = reason
        self.path = path


# --- startup: create or verify the database file


def prepare_database(data_dir):
    """Create the data dir and DB if needed; verify an existing DB. Returns its Path."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    path = (data_dir / DB_FILENAME).resolve()
    if not path.exists() or path.stat().st_size == 0:
        _initialise(path)
        return path
    status = _check_without_leftovers(path)
    if status == "empty":
        _initialise(path)
    elif status == "upgrade":
        _upgrade(path)
    return path


def _check_without_leftovers(path):
    """Run _check_existing; if the file is rejected, remove any SQLite sidecar
    files (-wal, -shm) that the read-only check itself created, so a rejected
    file is left exactly as it was found."""
    sidecars = [path.with_name(path.name + suffix) for suffix in ("-wal", "-shm")]
    existed = {sidecar: sidecar.exists() for sidecar in sidecars}
    try:
        return _check_existing(path)
    except DatabaseRejected:
        for sidecar, was_there in existed.items():
            if not was_there and sidecar.exists():
                try:
                    sidecar.unlink()
                except OSError:
                    pass
        raise


def _check_existing(path):
    """Inspect an existing file read-only. Returns "ok", "empty" or "upgrade"; raises DatabaseRejected."""
    uri = path.as_uri() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
        try:
            if conn.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise DatabaseRejected("the database failed its integrity check", path)
            application_id = conn.execute("PRAGMA application_id").fetchone()[0]
            user_version = conn.execute("PRAGMA user_version").fetchone()[0]
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                )
            }
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        raise DatabaseRejected("the file is not a readable SQLite database", path) from None

    if application_id == 0 and user_version == 0 and not tables:
        return "empty"  # a valid but blank SQLite file
    if application_id != APPLICATION_ID:
        raise DatabaseRejected("the file is not a Dutch Freelance Workbench database", path)
    if not 1 <= user_version <= SCHEMA_VERSION:
        raise DatabaseRejected(f"unsupported database schema version {user_version}", path)
    if not REQUIRED_TABLES <= tables:
        raise DatabaseRejected("the database is missing required tables", path)
    return "ok" if user_version == SCHEMA_VERSION else "upgrade"


def _initialise(path):
    conn = sqlite3.connect(str(path), timeout=5.0, isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("BEGIN IMMEDIATE")
        try:
            for statement in SCHEMA_STATEMENTS:
                conn.execute(statement)
            for version in sorted(UPGRADES):
                for statement in UPGRADES[version]:
                    conn.execute(statement)
            conn.execute(f"PRAGMA application_id = {APPLICATION_ID}")
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def _upgrade(path):
    """Bring an older schema up to SCHEMA_VERSION in one transaction.

    Existing rows are not touched: upgrades only add nullable columns and
    indexes. On any failure everything is rolled back and the file keeps its
    old schema.
    """
    conn = sqlite3.connect(str(path), timeout=5.0, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            current = conn.execute("PRAGMA user_version").fetchone()[0]
            for version in range(current + 1, SCHEMA_VERSION + 1):
                for statement in UPGRADES[version]:
                    conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    except sqlite3.Error:
        raise DatabaseRejected("the database could not be upgraded to the current schema", path) from None
    finally:
        conn.close()


# --- connections


def _casefold(value):
    return value.casefold() if isinstance(value, str) else value


def connect(path, busy_timeout_ms=5000):
    conn = sqlite3.connect(str(path), timeout=busy_timeout_ms / 1000.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.create_function("casefold", 1, _casefold, deterministic=True)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    conn.execute("PRAGMA synchronous = FULL")
    return conn


@dataclass
class Context:
    """What a request needs to reach the database: path, clock, faults, timeout."""

    db_path: Path
    clock: object
    faults: object
    busy_timeout_ms: int = 5000
    catalog: object = None  # discovery.Catalog: the bundled fictional catalog

    def connect(self):
        return connect(self.db_path, self.busy_timeout_ms)

    @contextmanager
    def read(self):
        """A connection inside one read transaction (a consistent snapshot)."""
        conn = self.connect()
        try:
            conn.execute("BEGIN")
            yield conn
        finally:
            conn.close()  # closing rolls back the read transaction


def is_busy(exc):
    text = str(exc).lower()
    return isinstance(exc, sqlite3.OperationalError) and ("locked" in text or "busy" in text)


def storage_busy():
    return ApiError(
        503,
        "STORAGE_BUSY",
        "The database is busy. Nothing was saved; please try again.",
        headers={"Retry-After": "1"},
    )


@contextmanager
def write_transaction(conn):
    """BEGIN IMMEDIATE ... COMMIT; ROLLBACK and re-raise on any exception."""
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as exc:
        if is_busy(exc):
            raise storage_busy() from None
        raise
    try:
        yield
        conn.execute("COMMIT")
    except BaseException as exc:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        if is_busy(exc):
            raise storage_busy() from None
        raise


# --- idempotency and audit rows


def request_hash(body):
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8", "surrogatepass")).hexdigest()


def find_idempotent(conn, key):
    return conn.execute(
        "SELECT method, target, request_hash, status_code, response_json FROM idempotency WHERE key = ?",
        (key,),
    ).fetchone()


def store_idempotent(conn, key, method, target, rhash, status, response_json, stamp):
    conn.execute(
        "INSERT INTO idempotency (key, method, target, request_hash, status_code, response_json, created_at_utc)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (key, method, target, rhash, status, response_json, stamp),
    )


def insert_audit(conn, command_type, entity_type, entity_id, resulting_version, stamp,
                 from_stage=None, to_stage=None):
    """Audit rows hold ids, versions and stage names only: never labels, text or keys."""
    conn.execute(
        "INSERT INTO audit (command_type, entity_type, entity_id, resulting_version, recorded_at_utc,"
        " from_stage, to_stage) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (command_type, entity_type, entity_id, resulting_version, stamp, from_stage, to_stage),
    )
