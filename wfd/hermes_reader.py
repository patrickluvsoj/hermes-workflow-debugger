"""The only module that touches Hermes files. Everything here is read-only.

The one write it performs is into the debugger's own data folder: a private copy
of ``cron/executions.db`` (Hermes keeps that file open, and a strict read-only
open of the original fails).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
OUTPUT_NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.md$")


def default_profile_root() -> Path:
    return Path(os.environ.get("WFD_PROFILE_ROOT") or Path.home() / ".hermes" / "profiles" / "daily")


def hermes_font(name: str) -> Path | None:
    """A font file from the local Hermes install. The wordmark font is Hermes's own and is
    not shipped with this repository; when Hermes is not installed the page falls back to
    the system font."""
    if not re.match(r"^[A-Za-z0-9_-]+\.woff2$", name):
        return None
    root = Path(os.environ.get("WFD_HERMES_FONTS") or Path.home() / ".hermes" / "hermes-agent" / "hermes_cli" / "web_dist" / "fonts")
    path = root / name
    return path if path.is_file() else None


def to_local(value) -> datetime | None:
    """Parse an ISO time into a naive local datetime. Naive input is taken as local."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


class HermesReader:
    def __init__(self, profile_root: Path | str, data_dir: Path | str):
        self.profile_root = Path(profile_root).expanduser()
        self.data_dir = Path(data_dir).expanduser()
        self._lock = threading.Lock()
        self._copy_stamp: tuple | None = None

    # ---- jobs ---------------------------------------------------------
    @property
    def cron_dir(self) -> Path:
        return self.profile_root / "cron"

    def jobs(self) -> dict[str, dict]:
        with open(self.cron_dir / "jobs.json", "r", encoding="utf-8") as handle:
            data = json.load(handle)
        rows = data.get("jobs", []) if isinstance(data, dict) else data
        return {str(row["id"]): row for row in rows if isinstance(row, dict) and row.get("id")}

    # ---- output files -------------------------------------------------
    def output_dir(self, job_id: str) -> Path:
        if not JOB_ID_RE.match(job_id):
            raise ValueError("bad job id")
        return self.cron_dir / "output" / job_id

    def list_outputs(self, job_id: str) -> list[tuple[str, int, int]]:
        """(file name, mtime_ns, size), newest name first."""
        folder = self.output_dir(job_id)
        found = []
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    if OUTPUT_NAME_RE.match(entry.name) and entry.is_file():
                        stat = entry.stat()
                        found.append((entry.name, stat.st_mtime_ns, stat.st_size))
        except FileNotFoundError:
            return []
        return sorted(found, reverse=True)

    def read_output(self, job_id: str, name: str) -> str:
        if not OUTPUT_NAME_RE.match(name):
            raise ValueError("bad output file name")
        with open(self.output_dir(job_id) / name, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()

    # ---- scripts ------------------------------------------------------
    def script_path(self, job: dict) -> Path | None:
        name = job.get("script")
        if not name or not isinstance(name, str):
            return None
        scripts = (self.profile_root / "scripts").resolve()
        path = (scripts / name).resolve()
        if scripts not in path.parents or not path.is_file():
            return None
        return path

    def script_info(self, job: dict) -> dict | None:
        path = self.script_path(job)
        if path is None:
            return None
        changed = datetime.fromtimestamp(path.stat().st_mtime).replace(microsecond=0)
        return {"script": job.get("script"), "changed_at": changed.isoformat()}

    def read_script(self, job: dict) -> str | None:
        path = self.script_path(job)
        if path is None:
            return None
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()

    # ---- executions.db (read through a private copy) --------------------
    def _refresh_copy(self) -> Path | None:
        source = self.cron_dir / "executions.db"
        if not source.is_file():
            return None
        target_dir = self.data_dir / "hermes-copy"
        target = target_dir / "executions.db"
        parts = [source] + [p for p in (Path(str(source) + "-wal"), Path(str(source) + "-shm")) if p.exists()]
        stamp = tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in parts)
        with self._lock:
            if stamp != self._copy_stamp or not target.exists():
                target_dir.mkdir(parents=True, exist_ok=True)
                for suffix in ("", "-wal", "-shm"):
                    stale = Path(str(target) + suffix)
                    if stale.exists():
                        stale.unlink()
                for part in parts:
                    copied = target_dir / part.name
                    shutil.copyfile(part, copied)
                    os.chmod(copied, 0o600)
                self._copy_stamp = stamp
        return target

    def _query(self, sql: str, params: tuple = ()) -> list[dict]:
        copy = self._refresh_copy()
        if copy is None:
            return []
        with self._lock:
            conn = sqlite3.connect(str(copy))
            try:
                conn.row_factory = sqlite3.Row
                return [dict(row) for row in conn.execute(sql, params)]
            except sqlite3.DatabaseError:
                return []
            finally:
                conn.close()

    def executions(self, job_id: str) -> list[dict]:
        return self._query(
            "select job_id, status, claimed_at, started_at, finished_at, scheduled_instant "
            "from executions where job_id = ? order by claimed_at desc",
            (job_id,),
        )

    def incidents(self, job_id: str) -> list[dict]:
        return self._query(
            "select job_id, error_sig, failure_type, first_seen_at, last_seen_at, output_file "
            "from cron_incidents where job_id = ?",
            (job_id,),
        )

    # ---- profile default model (used by the watcher only) ---------------
    def default_model(self) -> tuple[str | None, str | None]:
        import yaml

        try:
            with open(self.profile_root / "config.yaml", "r", encoding="utf-8") as handle:
                config = yaml.safe_load(handle) or {}
        except OSError:
            return None, None
        model = config.get("model") if isinstance(config, dict) else None
        if isinstance(model, dict):
            return model.get("default"), model.get("provider")
        if isinstance(model, str):
            return model, None
        return None, None
