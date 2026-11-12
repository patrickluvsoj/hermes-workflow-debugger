"""State is computed on every read and never stored.

Rules (spec: Design Decisions > States):
- A step's own result is the state of its latest trace: failed, missed, noop or ok.
  No traces at all is "none" (did not run).
- No-op: the run worked and did nothing, on a step where that is not expected
  (wfd/traces.py has the rules). A no-op upstream does not block the steps after it.
- Blocked: an upstream step is failed, missed or blocked, and this step has not
  run since that problem started.
- Paused is separate from health. A paused step keeps its own last result, is
  never blocked or missed, and is left out of the workflow roll-up.
- Workflow health is the worst of its unpaused steps: failed, missed, blocked, noop, ok.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .hermes_reader import to_local

RANK = {"failed": 0, "missed": 1, "blocked": 2, "noop": 3, "ok": 4, "none": 5}
OVERDUE_GRACE = timedelta(minutes=30)
SLOT_SLACK = timedelta(seconds=90)


def is_paused(job: dict) -> bool:
    return job.get("enabled") is False or job.get("state") == "paused"


# ---- missed runs --------------------------------------------------------
def missed_times(job: dict, run_times: list[datetime], execution_times: list[datetime], now: datetime) -> list[datetime]:
    """Scheduled times with no run. `run_times` are trace times, `execution_times`
    are executions.db claim times. Newest first."""
    if is_paused(job):
        return []
    runs = sorted(set(run_times) | set(execution_times))
    missed: list[datetime] = []
    due = to_local(job.get("next_run_at"))
    if due and due < now - OVERDUE_GRACE and not any(r >= due - SLOT_SLACK for r in runs):
        missed.append(due)
    schedule = job.get("schedule") or {}
    if schedule.get("kind") == "cron" and schedule.get("expr") and execution_times:
        try:
            from croniter import croniter
        except ImportError:  # history is optional
            croniter = None
        if croniter is not None:
            anchor = min(execution_times)
            slots = []
            try:
                it = croniter(schedule["expr"], anchor)
                while True:
                    slot = it.get_next(datetime)
                    if slot > now:
                        break
                    slots.append(slot)
                    if len(slots) > 500:
                        break
            except Exception:
                slots = []
            for index, slot in enumerate(slots):
                end = slots[index + 1] if index + 1 < len(slots) else None
                if end is None and slot >= now - OVERDUE_GRACE:
                    continue
                ran = any(slot - SLOT_SLACK <= r and (end is None or r < end - SLOT_SLACK) for r in runs)
                if not ran and not any(abs((slot - m).total_seconds()) < 120 for m in missed):
                    missed.append(slot)
    return sorted(missed, reverse=True)


def missed_trace(when: datetime) -> dict:
    return {
        "file": None, "time": when.replace(microsecond=0).isoformat(), "state": "missed",
        "status_line": "scheduled, never ran", "status_known": True, "first_error_line": None,
        "outcome": None, "reason": None, "detail": None, "result_problem": None,
        "session_id": None, "workflow_run_id": None, "local_sig": None, "duration_s": None,
    }


# ---- step and workflow health -------------------------------------------
def evaluate_steps(steps: list[dict]) -> None:
    """Fill in health for steps given in dependency order. Each step needs
    id, title, depends_on, paused and traces (newest first, missed included)."""
    by_id = {step["id"]: step for step in steps}
    for step in steps:
        traces = step["traces"]
        latest = traces[0] if traces else None
        own = latest["state"] if latest else "none"
        real = [t for t in traces if t["state"] != "missed"]
        step["latest"] = latest
        step["last_run"] = real[0]["time"] if real else None
        step["own"] = own
        step["blocked_by"] = []
        step["problem_time"] = latest["time"] if own in ("failed", "missed", "noop") else None
        if step["paused"]:
            step["health"] = own
            continue
        since = None
        for dep_id in step["depends_on"]:
            dep = by_id.get(dep_id)
            if dep is None or dep.get("health") not in ("failed", "missed", "blocked"):
                continue
            started = dep.get("problem_time")
            if step["last_run"] is None or started is None or step["last_run"] < started:
                step["blocked_by"].append(dep["title"])
                since = started if since is None or (started and started > since) else since
        if step["blocked_by"] and RANK["blocked"] < RANK[own]:
            step["health"] = "blocked"
            step["problem_time"] = since
        else:
            step["health"] = own
            if own not in ("failed", "missed"):
                step["blocked_by"] = []


def roll_up(steps: list[dict]) -> str:
    counted = [step["health"] for step in steps if not step["paused"]]
    return min(counted, key=RANK.__getitem__) if counted else "none"


def rail_order(workflows: list[dict]) -> list[dict]:
    """Problems first; inside one health level the most recent problem first."""
    def newest_first(value):
        return (0, "".join(chr(255 - ord(c)) for c in value)) if value else (1, "")

    def key(workflow):
        return (RANK[workflow["health"]], newest_first(workflow.get("problem_time")),
                newest_first(workflow.get("last_run")), workflow["title"].lower())
    return sorted(workflows, key=key)


# ---- assembling the whole picture ---------------------------------------
def _schedule_text(job: dict) -> str:
    schedule = job.get("schedule") or {}
    return str(job.get("schedule_display") or schedule.get("display") or schedule.get("expr") or "")


def _attach_durations(traces: list[dict], executions: list[dict]) -> None:
    spans = []
    for row in executions:
        start, end = to_local(row.get("started_at")), to_local(row.get("finished_at"))
        if start and end and end >= start:
            spans.append((end, (end - start).total_seconds()))
    for trace in traces:
        if not trace.get("time"):
            continue
        when = datetime.fromisoformat(trace["time"])
        for end, seconds in spans:
            if abs((end - when).total_seconds()) <= 90:
                trace["duration_s"] = round(seconds, 1)
                break


def _error_sig(trace: dict, incidents: list[dict]) -> str | None:
    if trace["state"] != "failed":
        return None
    tail = f"/cron/output/{trace['job_id']}/{trace['file']}"
    for row in incidents:
        if str(row.get("output_file") or "").replace("\\", "/").endswith(tail):
            return str(row["error_sig"])
    return trace["local_sig"]


def step_traces(reader, store_traces, job: dict, job_id: str, now: datetime, expect: dict | None = None) -> list[dict]:
    """All traces for one job, newest first, with missed runs and signatures.
    `expect` is the step's expectation from its scaffold file."""
    traces = store_traces.traces(job_id, expect)
    executions = reader.executions(job_id)
    incidents = reader.incidents(job_id)
    _attach_durations(traces, executions)
    for trace in traces:
        trace["error_sig"] = _error_sig(trace, incidents)
        trace.pop("local_sig", None)
    run_times = [datetime.fromisoformat(t["time"]) for t in traces if t.get("time")]
    execution_times = [t for t in (to_local(row.get("claimed_at")) for row in executions) if t]
    for when in missed_times(job, run_times, execution_times, now):
        missed = missed_trace(when)
        missed["job_id"] = job_id
        missed["error_sig"] = None
        missed.pop("local_sig", None)
        traces.append(missed)
    traces.sort(key=lambda t: t["time"] or "", reverse=True)
    return traces


def _summary_for(trace: dict, summaries) -> dict:
    row = summaries.get(trace["job_id"], trace["error_sig"]) if summaries and trace.get("error_sig") else None
    if row and row["status"] == "ready" and row.get("summary"):
        return {"status": "ready", "text": row["summary"], "summarized_at": row["created_at"],
                "first_error_line": trace["first_error_line"]}
    status = "unavailable" if row and row["status"] == "unavailable" else "pending"
    return {"status": status, "text": None, "summarized_at": None, "first_error_line": trace["first_error_line"]}


def compute_state(reader, trace_store, scaffold_dir, summaries, now: datetime, mode: str = "live") -> dict:
    from . import scaffold

    jobs = reader.jobs()
    workflows = []
    for definition in scaffold.load_dir(scaffold_dir, jobs):
        if definition["invalid"]:
            workflows.append({
                "id": definition["id"], "title": definition["title"], "description": definition["description"],
                "file": definition["file"], "invalid": True, "error": definition["errors"][0],
                "errors": definition["errors"], "health": "failed", "paused_count": 0, "last_run": None,
                "problem_time": None, "steps": [], "problems": [],
            })
            continue
        steps = []
        for item in definition["steps"]:
            job = jobs[item["job_id"]]
            paused = is_paused(job)
            steps.append({
                **item,
                "job_name": job.get("name") or item["job_id"],
                "schedule": _schedule_text(job),
                "paused": paused,
                "paused_since": (to_local(job.get("paused_at")).replace(microsecond=0).isoformat()
                                 if paused and to_local(job.get("paused_at")) else None),
                "source": reader.script_info(job),
                "traces": step_traces(reader, trace_store, job, item["job_id"], now, item["expect"]),
            })
        evaluate_steps(steps)
        problems = []
        for step in steps:
            step["trace_count"] = len(step["traces"])
            step["summary"] = None
            if step["own"] == "failed":
                step["summary"] = _summary_for(step["latest"], summaries)
            if not step["paused"] and step["health"] in ("failed", "missed"):
                problems.append({"step_id": step["id"], "title": step["title"], "kind": step["health"],
                                 "time": step["latest"]["time"], "summary": step["summary"]})
            del step["traces"], step["own"]
        times = [step["problem_time"] for step in steps if not step["paused"] and step["problem_time"]
                 and step["health"] != "ok"]
        runs = [step["last_run"] for step in steps if step["last_run"]]
        workflows.append({
            "id": definition["id"], "title": definition["title"], "description": definition["description"],
            "file": definition["file"], "invalid": False, "error": None, "errors": [],
            "health": roll_up(steps), "paused_count": sum(1 for step in steps if step["paused"]),
            "last_run": max(runs) if runs else None, "problem_time": max(times) if times else None,
            "steps": steps, "problems": problems,
        })
    return {
        "mode": mode,
        "read_at": now.replace(microsecond=0).isoformat(),
        "profile": reader.profile_root.name,
        "scaffold_dir": str(scaffold_dir),
        "workflows": rail_order(workflows),
    }
