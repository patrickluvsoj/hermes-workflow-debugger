"""Parse one CRON output file into a trace."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime

FAILED_STATUSES = ("script failed", "silent (empty output)", "BLOCKED")
KNOWN_OK_STATUSES = ("\u2705 Published",)

_HEADER_KEY = re.compile(r"^\*\*([A-Za-z ]+):\*\*\s*(.*)$")
_SESSION = re.compile(r"session_id=((?:\d{8}_\d{6}_[0-9a-f]{4,})|(?:local-[0-9a-f]{16,}))")
_RUN = re.compile(r"^workflow_run_id=(\S+)\s*$", re.M)
_ERROR_HINT = re.compile(r"(error|exception|failed|failure|traceback|blocked|denied|refused|timed out)", re.I)
_EXC_LINE = re.compile(r"^[A-Za-z_][\w.]*(Error|Exception)\b.*")
NO_RUN_IDS = ("unattached", "unavailable")


def _split(text: str) -> tuple[dict, str]:
    """Header fields and body. The header is the leading block of `# title`,
    `**Key:** value` and blank lines."""
    fields: dict[str, str] = {}
    lines = text.splitlines()
    index = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("# ") or stripped == "---":
            continue
        match = _HEADER_KEY.match(stripped)
        if match and match.group(1) in ("Job ID", "Run Time", "Mode", "Status", "Schedule"):
            fields.setdefault(match.group(1), match.group(2).strip())
            continue
        break
    else:
        index = len(lines)
    return fields, "\n".join(lines[index:]).strip()


def time_from_name(name: str) -> datetime | None:
    try:
        return datetime.strptime(name[:19], "%Y-%m-%d_%H-%M-%S")
    except ValueError:
        return None


def first_error_line(body: str, status_line: str | None) -> str:
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    for line in lines:
        if line.startswith("failure_reason="):
            return line[len("failure_reason="):].strip()
    for line in lines:
        if line.startswith("**Scanner result:**"):
            return line[len("**Scanner result:**"):].strip()
    for line in reversed(lines):
        if _EXC_LINE.match(line):
            return line
    for line in lines:
        if _ERROR_HINT.search(line) and not line.startswith("Script exited") and not line.startswith("#"):
            return line
    for line in lines:
        if line.startswith("Script exited"):
            return line
    return lines[0] if lines else (status_line or "no output")


def local_error_sig(job_id: str, body: str) -> str:
    digest = hashlib.sha256((job_id + "\n" + body[:200]).encode("utf-8")).hexdigest()
    return "local-" + digest[:12]


def is_failure_line(line: str) -> bool:
    stripped = line.strip()
    if stripped.startswith("**Status:**"):
        return any(status in stripped for status in FAILED_STATUSES)
    return bool(
        stripped.startswith(("failure_reason=", "**Scanner result:**", "Script exited with code", "Traceback"))
        or _EXC_LINE.match(stripped)
        or re.match(r"^(ERROR|FATAL)\b", stripped)
    )


def parse_trace(job_id: str, name: str, text: str) -> dict:
    fields, body = _split(text)
    status_line = fields.get("Status")
    when = None
    if fields.get("Run Time"):
        try:
            when = datetime.strptime(fields["Run Time"][:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            when = None
    if when is None:
        when = time_from_name(name)
    failed = status_line in FAILED_STATUSES
    known = status_line is None or failed or status_line in KNOWN_OK_STATUSES
    session = _SESSION.search(text)
    run = None
    for match in _RUN.finditer(text):
        if match.group(1) not in NO_RUN_IDS:
            run = match.group(1)
    return {
        "job_id": job_id,
        "file": name,
        "time": when.isoformat() if when else None,
        "state": "failed" if failed else "ok",
        "status_line": status_line,
        "status_known": known,
        "first_error_line": first_error_line(body, status_line) if failed else None,
        "session_id": session.group(1) if session else None,
        "workflow_run_id": run,
        "local_sig": local_error_sig(job_id, body) if failed else None,
        "duration_s": None,
    }


class TraceStore:
    """Parsed traces per job, cached on (name, mtime, size)."""

    def __init__(self, reader, on_unknown=None):
        self.reader = reader
        self.on_unknown = on_unknown
        self._cache: dict[tuple, dict] = {}
        self._warned: set[str] = set()

    def traces(self, job_id: str) -> list[dict]:
        out = []
        for name, mtime_ns, size in self.reader.list_outputs(job_id):
            key = (job_id, name, mtime_ns, size)
            trace = self._cache.get(key)
            if trace is None:
                trace = parse_trace(job_id, name, self.reader.read_output(job_id, name))
                self._cache[key] = trace
                if not trace["status_known"] and self.on_unknown and trace["status_line"] not in self._warned:
                    self._warned.add(trace["status_line"])
                    self.on_unknown(job_id, name, trace["status_line"])
            out.append(dict(trace))
        out.sort(key=lambda t: t["time"] or "", reverse=True)
        return out
