"""The debugger's own store: one AI summary per (job, error signature)."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA = """
create table if not exists summaries (
  job_id     text not null,
  error_sig  text not null,
  trace_file text,
  status     text not null check (status in ('pending', 'ready', 'unavailable')),
  summary    text,
  session_id text,
  attempts   integer not null default 0,
  created_at text not null,
  primary key (job_id, error_sig)
)
"""


class SummaryStore:
    def __init__(self, path: Path | str, readonly: bool = False):
        self.path = Path(path)
        self.readonly = readonly
        if not readonly:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as conn:
                conn.execute(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        if self.readonly:
            conn = sqlite3.connect(f"file:{self.path}?mode=ro&immutable=1", uri=True)
        else:
            conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        return conn

    def get(self, job_id: str, error_sig: str) -> dict | None:
        if self.readonly and not self.path.is_file():
            return None
        try:
            conn = self._connect()
        except sqlite3.Error:
            return None
        try:
            row = conn.execute(
                "select * from summaries where job_id = ? and error_sig = ?", (job_id, error_sig)
            ).fetchone()
            return dict(row) if row else None
        except sqlite3.Error:
            return None
        finally:
            conn.close()

    def put(self, job_id: str, error_sig: str, *, trace_file: str | None, status: str, summary: str | None,
            session_id: str | None, attempts: int, created_at: str | None = None) -> None:
        if self.readonly:
            raise RuntimeError("summary store is read-only")
        created = created_at or datetime.now().replace(microsecond=0).isoformat()
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    "insert into summaries (job_id, error_sig, trace_file, status, summary, session_id, attempts, created_at) "
                    "values (?, ?, ?, ?, ?, ?, ?, ?) "
                    "on conflict(job_id, error_sig) do update set trace_file=excluded.trace_file, status=excluded.status, "
                    "summary=excluded.summary, session_id=excluded.session_id, attempts=excluded.attempts, "
                    "created_at=excluded.created_at",
                    (job_id, error_sig, trace_file, status, summary, session_id, attempts, created),
                )
        finally:
            conn.close()
