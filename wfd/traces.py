"""Parse one CRON output file into a trace."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

# How a run's result becomes a state. First match wins.
#   1. Hermes status `script failed` or `BLOCKED`: failed.
#   2. Hermes status `silent (empty output)` (exited 0, printed nothing; Hermes records a
#      success): ok if the step's scaffold says `allow_silent: true`, otherwise failed.
#   3. A valid `step_result=` line printed by the script: failed, done (ok) or noop.
#   4. No result line and the step has patterns: a `noop_when` match is no-op; otherwise a
#      `success_when` that does not match is no-op.
#   5. Otherwise ok.
# A no-op on a step with `noop: ok` is shown as ok and keeps its reason.
SILENT_STATUS = "silent (empty output)"
HARD_FAILED_STATUSES = ("script failed", "BLOCKED")
FAILED_STATUSES = HARD_FAILED_STATUSES + (SILENT_STATUS,)
OUTCOMES = ("done", "noop", "failed")
RESULT_SCHEMA = 1
DETAIL_CLIP = 300
_RESULT = re.compile(r"^step_result=(.*)$", re.M)
KNOWN_OK_STATUSES = ("\u2705 Published", "silent (wakeAgent=false)")  # the second is a script choosing to stay quiet

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


def _clip(value) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    value = " ".join(value.split())
    return value if len(value) <= DETAIL_CLIP else value[:DETAIL_CLIP] + "\u2026"


def parse_result_line(body: str) -> tuple[dict | None, str | None]:
    """The last valid `step_result=` line as {outcome, reason, detail}, and a note on
    any line that was ignored. A line that is not valid is treated as absent."""
    found, problem = None, None
    for match in _RESULT.finditer(body):
        raw = match.group(1).strip()
        try:
            data = json.loads(raw)
        except ValueError:
            problem = "step_result line is not valid JSON"
            continue
        if not isinstance(data, dict):
            problem = "step_result line is not a JSON object"
        elif type(data.get("schema")) is not int or data["schema"] != RESULT_SCHEMA:
            problem = f"step_result line has schema {data.get('schema')!r}, expected {RESULT_SCHEMA}"
        elif data.get("outcome") not in OUTCOMES:
            problem = f"step_result line has unknown outcome {data.get('outcome')!r}"
        else:
            reason = data.get("reason")
            found = {"outcome": data["outcome"],
                     "reason": reason.strip()[:80] if isinstance(reason, str) and reason.strip() else None,
                     "detail": _clip(data.get("detail"))}
    return found, problem


def _pattern_noop(body: str, expect: dict) -> tuple[bool, str | None]:
    """Rule 4. (is a no-op, the line that says so)."""
    for pattern in expect.get("noop_when") or []:
        match = re.search(pattern, body, re.M)
        if match:
            start = body.rfind("\n", 0, match.start()) + 1
            end = body.find("\n", match.end())
            return True, _clip(body[start:end if end != -1 else len(body)])
    success = expect.get("success_when")
    if success and not re.search(success, body, re.M):
        return True, None
    return False, None


def parse_trace(job_id: str, name: str, text: str, expect: dict | None = None) -> dict:
    expect = expect or {}
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
    known = status_line is None or status_line in FAILED_STATUSES or status_line in KNOWN_OK_STATUSES
    result, result_problem = parse_result_line(body)
    state, outcome, reason, detail = "ok", None, None, None
    if status_line in HARD_FAILED_STATUSES:
        state = "failed"
    elif status_line == SILENT_STATUS:
        state = "ok" if expect.get("allow_silent") else "failed"
    elif result is not None:
        outcome, reason, detail = result["outcome"], result["reason"], result["detail"]
        state = {"done": "ok", "noop": "noop", "failed": "failed"}[outcome]
    else:
        is_noop, line = _pattern_noop(body, expect)
        if is_noop:
            state, detail = "noop", line
    if state == "noop" and expect.get("noop") == "ok":
        state = "ok"
    failed = state == "failed"
    if failed and outcome == "failed":
        error_line = ": ".join(part for part in (reason, detail) if part) or "step_result outcome is failed"
    else:
        error_line = first_error_line(body, status_line) if failed else None
    session = _SESSION.search(text)
    run = None
    for match in _RUN.finditer(text):
        if match.group(1) not in NO_RUN_IDS:
            run = match.group(1)
    return {
        "job_id": job_id,
        "file": name,
        "time": when.isoformat() if when else None,
        "state": state,
        "status_line": status_line,
        "status_known": known,
        "outcome": outcome,
        "reason": reason,
        "detail": detail,
        "result_problem": result_problem,
        "first_error_line": error_line,
        "session_id": session.group(1) if session else None,
        "workflow_run_id": run,
        "local_sig": local_error_sig(job_id, body) if failed else None,
        "duration_s": None,
    }


class TraceStore:
    """Parsed traces per job, cached on (name, mtime, size) and the step's expectation."""

    def __init__(self, reader, on_unknown=None, on_bad_result=None):
        self.reader = reader
        self.on_unknown = on_unknown
        self.on_bad_result = on_bad_result
        self._cache: dict[tuple, dict] = {}
        self._warned: set[str] = set()
        self._warned_results: set[tuple] = set()

    def traces(self, job_id: str, expect: dict | None = None) -> list[dict]:
        out = []
        expect_key = json.dumps(expect, sort_keys=True) if expect else ""
        for name, mtime_ns, size in self.reader.list_outputs(job_id):
            key = (job_id, name, mtime_ns, size, expect_key)
            trace = self._cache.get(key)
            if trace is None:
                trace = parse_trace(job_id, name, self.reader.read_output(job_id, name), expect)
                self._cache[key] = trace
                if trace["result_problem"] and self.on_bad_result and (job_id, name) not in self._warned_results:
                    self._warned_results.add((job_id, name))
                    self.on_bad_result(job_id, name, trace["result_problem"])
                if not trace["status_known"] and self.on_unknown and trace["status_line"] not in self._warned:
                    self._warned.add(trace["status_line"])
                    self.on_unknown(job_id, name, trace["status_line"])
            out.append(dict(trace))
        out.sort(key=lambda t: t["time"] or "", reverse=True)
        return out
