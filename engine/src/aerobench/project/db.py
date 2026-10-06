"""SQLite project database with numbered SQL migrations (ADR-0004)."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from typing import Any, Iterator, Optional

from ..core.errors import AeroBenchError, ErrorCategory
from ..core.ids import utc_now_iso

_MIG = re.compile(r"^(\d{4})_.*\.sql$")


def _migrations() -> list[tuple[int, str]]:
    out = []
    for entry in resources.files("aerobench.project").joinpath("sql").iterdir():
        m = _MIG.match(entry.name)
        if m:
            out.append((int(m.group(1)), entry.read_text(encoding="utf-8")))
    return sorted(out)


LATEST_DB_VERSION = max(v for v, _ in _migrations())


class ProjectDB:
    """Thin, thread-safe wrapper around one SQLite file (WAL, busy timeout)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._local = threading.local()
        self.migrate()

    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(str(self.path), timeout=30, isolation_level=None, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA busy_timeout=30000")
            c.execute("PRAGMA foreign_keys=ON")
            self._local.conn = c
        return c

    def close(self) -> None:
        c = getattr(self._local, "conn", None)
        if c is not None:
            c.close()
            self._local.conn = None

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        c = self._conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            yield c
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise

    # ------------------------------------------------------------------ migrations
    def version(self) -> int:
        c = self._conn()
        c.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        row = c.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
        return int(row["v"] or 0)

    def migrate(self) -> None:
        cur = self.version()
        if cur > LATEST_DB_VERSION:
            raise AeroBenchError.make("project.db_newer", ErrorCategory.PROJECT,
                                      f"The project database (v{cur}) is newer than this application supports (v{LATEST_DB_VERSION}).",
                                      action="Open the project with a newer release.")
        for v, sql in _migrations():
            if v <= cur:
                continue
            c = self._conn()
            c.execute("BEGIN IMMEDIATE")
            try:
                for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
                    c.execute(stmt)
                c.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)", (v, utc_now_iso()))
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise

    # ------------------------------------------------------------------ helpers
    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        return self._conn().execute(sql, params)

    def one(self, sql: str, params: tuple | dict = ()) -> Optional[dict[str, Any]]:
        r = self._conn().execute(sql, params).fetchone()
        return dict(r) if r else None

    def all(self, sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
        return [dict(r) for r in self._conn().execute(sql, params).fetchall()]

    def insert(self, table: str, row: dict[str, Any]) -> None:
        row = {k: _enc(v, k) for k, v in row.items()}
        cols = ", ".join(row)
        ph = ", ".join(f":{k}" for k in row)
        self._conn().execute(f"INSERT INTO {table} ({cols}) VALUES ({ph})", row)

    def update(self, table: str, key: str, key_value: Any, changes: dict[str, Any], where_status: Optional[tuple[str, ...]] = None) -> int:
        """Update one row; with ``where_status`` only if its status is one of those values
        (compare-and-set against concurrent writers).  Returns the number of rows changed."""
        if not changes:
            return 0
        changes = {k: _enc(v, k) for k, v in changes.items()}
        sets = ", ".join(f"{k} = :{k}" for k in changes)
        params = {**changes, "__key": key_value}
        cond = ""
        if where_status:
            cond = " AND status IN (%s)" % ", ".join(f":__s{i}" for i in range(len(where_status)))
            params.update({f"__s{i}": v for i, v in enumerate(where_status)})
        cur = self._conn().execute(f"UPDATE {table} SET {sets} WHERE {key} = :__key{cond}", params)
        return cur.rowcount

    def log_event(self, event_kind: str, event_ref: Optional[str] = None, **data: Any) -> None:
        self.insert("events", {"t": utc_now_iso(), "kind": event_kind, "ref": event_ref, "data_json": data})

    def get_meta(self, key: str, default: Any = None) -> Any:
        r = self.one("SELECT value FROM meta WHERE key = ?", (key,))
        return json.loads(r["value"]) if r else default

    def set_meta(self, key: str, value: Any) -> None:
        self.execute("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (key, json.dumps(value)))


def _enc(v: Any, key: str) -> Any:
    if key.endswith("_json") and not isinstance(v, (str, type(None))):
        return json.dumps(v, default=str)
    if isinstance(v, bool):
        return int(v)
    return v


def dec(row: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Decode *_json columns of a row (returns a new dict with the suffix stripped)."""
    if row is None:
        return None
    out = {}
    for k, v in row.items():
        if k.endswith("_json"):
            out[k[:-5]] = json.loads(v) if isinstance(v, str) and v else None
        else:
            out[k] = v
    return out
