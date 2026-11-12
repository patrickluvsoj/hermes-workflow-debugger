"""One test per state rule in plan section 5.3 (spec: Design Decisions > States)."""
from datetime import datetime

from wfd.state import evaluate_steps, missed_times, roll_up, rail_order


def tr(day, state="ok", hour=8):
    return {"time": datetime(2026, 11, day, hour).isoformat(), "state": state, "file": f"d{day}.md"}


def step(sid, traces=(), depends_on=(), paused=False):
    return {"id": sid, "title": sid.upper(), "depends_on": list(depends_on), "paused": paused, "traces": list(traces)}


def health(steps):
    evaluate_steps(steps)
    return {s["id"]: s["health"] for s in steps}


def test_failed_when_latest_trace_failed():
    assert health([step("a", [tr(5, "failed"), tr(4)])] ) == {"a": "failed"}


def test_later_ok_trace_clears_failed_and_keeps_history():  # AC-9
    s = step("a", [tr(6), tr(5, "failed")])
    assert health([s]) == {"a": "ok"}
    assert [t["state"] for t in s["traces"]] == ["ok", "failed"]


def test_missed_when_latest_scheduled_run_has_no_execution():
    assert health([step("a", [tr(6, "missed"), tr(5)])]) == {"a": "missed"}


def test_blocked_when_upstream_failed_and_step_has_not_run_since():
    steps = [step("a", [tr(5, "failed")]), step("b", [tr(4)], depends_on=["a"])]
    assert health(steps) == {"a": "failed", "b": "blocked"}
    assert steps[1]["blocked_by"] == ["A"]


def test_not_blocked_when_step_ran_after_upstream_failure():
    steps = [step("a", [tr(5, "failed")]), step("b", [tr(6)], depends_on=["a"])]
    assert health(steps) == {"a": "failed", "b": "ok"}


def test_blocked_when_upstream_missed():
    steps = [step("a", [tr(5, "missed"), tr(3)]), step("b", [tr(4)], depends_on=["a"])]
    assert health(steps) == {"a": "missed", "b": "blocked"}


def test_blocked_passes_down_the_chain():
    steps = [step("a", [tr(5, "failed")]), step("b", [tr(4)], ["a"]), step("c", [tr(4, hour=10)], ["b"])]
    assert health(steps) == {"a": "failed", "b": "blocked", "c": "blocked"}


def test_step_with_no_files_did_not_run():
    assert health([step("a")]) == {"a": "none"}


def test_step_with_no_files_under_a_failed_upstream_is_blocked():
    assert health([step("a", [tr(5, "failed")]), step("b", depends_on=["a"])])["b"] == "blocked"


def test_own_failure_outranks_blocked():
    steps = [step("a", [tr(5, "failed")]), step("b", [tr(4, "failed")], ["a"])]
    assert health(steps)["b"] == "failed"


def test_paused_step_is_never_blocked_and_keeps_its_own_result():  # AC-6
    steps = [step("a", [tr(5, "failed")]), step("b", [tr(4)], ["a"], paused=True)]
    assert health(steps) == {"a": "failed", "b": "ok"}
    assert steps[1]["paused"] is True


def test_paused_step_is_left_out_of_workflow_health():  # AC-6
    steps = [step("a", [tr(5)]), step("b", [tr(4, "failed")], paused=True)]
    evaluate_steps(steps)
    assert steps[1]["health"] == "failed"
    assert roll_up(steps) == "ok"


def test_workflow_health_is_the_worst_step_in_order():  # AC-7
    def wf(*states):
        steps = [step(f"s{i}", [tr(5, st)] if st != "none" else []) for i, st in enumerate(states)]
        evaluate_steps(steps)
        return roll_up(steps)

    assert wf("ok", "failed", "missed") == "failed"
    assert wf("ok", "missed") == "missed"
    assert wf("ok", "ok") == "ok"
    assert wf("none", "ok") == "ok"
    assert wf("none") == "none"
    blocked = [step("a", [tr(5, "missed"), tr(3)], paused=False), step("b", [tr(4)], ["a"])]
    evaluate_steps(blocked)
    assert roll_up(blocked) == "missed"
    only_blocked = [step("a", [tr(5, "failed")], paused=True), step("b", [tr(4)], ["a"])]
    evaluate_steps(only_blocked)
    assert roll_up(only_blocked) == "blocked"


def test_rail_lists_problems_first():  # AC-7
    rows = [
        {"id": "ok", "title": "A", "health": "ok", "problem_time": None, "last_run": "2026-11-12T08:00:00"},
        {"id": "blocked", "title": "B", "health": "blocked", "problem_time": "2026-11-10T08:00:00", "last_run": None},
        {"id": "missed", "title": "C", "health": "missed", "problem_time": "2026-11-12T06:00:00", "last_run": None},
        {"id": "old-fail", "title": "D", "health": "failed", "problem_time": "2026-11-08T21:00:00", "last_run": None},
        {"id": "new-fail", "title": "E", "health": "failed", "problem_time": "2026-11-11T08:00:00", "last_run": None},
        {"id": "bad-file", "title": "F", "health": "failed", "problem_time": None, "last_run": None},
    ]
    assert [r["id"] for r in rail_order(rows)] == ["new-fail", "old-fail", "bad-file", "missed", "blocked", "ok"]


# ---- missed runs (plan 5.6) ----------------------------------------------
NOW = datetime(2026, 11, 12, 10, 42)


def job(**kw):
    base = {"enabled": True, "state": "scheduled", "schedule": {"kind": "interval", "minutes": 360},
            "next_run_at": "2026-11-12T12:00:00"}
    base.update(kw)
    return base


def test_overdue_now_is_missed():
    assert missed_times(job(next_run_at="2026-11-12T06:00:00"), [datetime(2026, 11, 12, 0, 0)], [], NOW) == [
        datetime(2026, 11, 12, 6, 0)]


def test_not_missed_inside_the_30_minute_grace():
    assert missed_times(job(next_run_at="2026-11-12T10:30:00"), [], [], NOW) == []


def test_not_missed_when_a_run_exists_for_that_time():
    assert missed_times(job(next_run_at="2026-11-12T06:00:00"), [datetime(2026, 11, 12, 6, 0, 40)], [], NOW) == []


def test_paused_job_is_never_missed():
    assert missed_times(job(enabled=False, state="paused", next_run_at="2026-11-12T06:00:00"), [], [], NOW) == []


def test_history_marks_a_scheduled_slot_with_no_run():
    daily = job(schedule={"kind": "cron", "expr": "0 8 * * *"}, next_run_at="2026-11-13T08:00:00")
    runs = [datetime(2026, 11, d, 8, 0, 5) for d in (12, 11, 10, 8, 7)]
    execs = [datetime(2026, 11, d, 8, 0, 1) for d in (12, 11, 10, 8, 7)]
    assert missed_times(daily, runs, execs, NOW) == [datetime(2026, 11, 9, 8, 0)]


def test_history_accepts_a_late_run_in_the_same_slot():
    daily = job(schedule={"kind": "cron", "expr": "0 8 * * *"}, next_run_at="2026-11-13T08:00:00")
    runs = [datetime(2026, 11, 12, 8, 0, 5), datetime(2026, 11, 11, 19, 30), datetime(2026, 11, 10, 8, 0, 5)]
    assert missed_times(daily, runs, [datetime(2026, 11, 10, 8, 0, 1)], NOW) == []


def test_history_needs_an_execution_row_to_anchor_the_window():
    daily = job(schedule={"kind": "cron", "expr": "0 8 * * *"}, next_run_at="2026-11-13T08:00:00")
    assert missed_times(daily, [datetime(2026, 11, 1, 8, 0, 5)], [], NOW) == []
