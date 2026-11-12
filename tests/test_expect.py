"""Plan section 12: expected outcome in the scaffold, the result line, and the no-op state."""
import textwrap

import pytest

from wfd.scaffold import DEFAULT_EXPECT, load_file
from wfd.state import evaluate_steps, rail_order, roll_up
from wfd.traces import TraceStore, parse_result_line, parse_trace

JOBS = {"job-a": {}, "job-b": {}}
HEAD = "# Cron Job: Example\n\n**Job ID:** j1\n**Run Time:** 2026-11-08 21:01:00\n**Mode:** no_agent (script)\n"
NAME = "2026-11-08_21-01-00.md"


def scaffold(tmp_path, expect_yaml):
    text = textwrap.dedent("""
        id: demo
        title: Demo
        description: One step.
        steps:
          - id: gate
            title: Gate
            job_id: job-a
    """) + textwrap.indent(textwrap.dedent(expect_yaml), "    ")
    path = tmp_path / "demo.yaml"
    path.write_text(text)
    return load_file(path, JOBS)


def parse(status=None, body="---\n\nsome output\n", **expect):
    status_line = f"**Status:** {status}\n" if status else ""
    return parse_trace("j1", NAME, HEAD + status_line + "\n" + body, {**DEFAULT_EXPECT, **expect} if expect else None)


def line(**fields):
    import json
    return "step_result=" + json.dumps({"schema": 1, **fields})


# ---- scaffold: the expect block (12.3) ----------------------------------
def test_step_without_expect_gets_the_defaults(tmp_path):
    wf = scaffold(tmp_path, "")
    assert wf["invalid"] is False
    assert wf["steps"][0]["expect"] == {"allow_silent": False, "noop": "flag", "success_when": None, "noop_when": []}


def test_expect_block_is_loaded(tmp_path):
    wf = scaffold(tmp_path, """
        expect:
          allow_silent: true
          noop: ok
          success_when: 'published='
          noop_when: ['^safe_noop=', 'nothing to do']
    """)
    assert wf["invalid"] is False, wf["errors"]
    assert wf["steps"][0]["expect"] == {"allow_silent": True, "noop": "ok", "success_when": "published=",
                                        "noop_when": ["^safe_noop=", "nothing to do"]}


@pytest.mark.parametrize("block, error", [
    ("expect: yes please", "steps[0].expect: must be a mapping"),
    ("expect:\n  quiet: true", "steps[0].expect.quiet: unknown key (use allow_silent, noop, success_when, noop_when)"),
    ("expect:\n  allow_silent: 'yes'", "steps[0].expect.allow_silent: must be true or false"),
    ("expect:\n  allow_silent: 1", "steps[0].expect.allow_silent: must be true or false"),
    ("expect:\n  noop: fine", "steps[0].expect.noop: must be 'flag' or 'ok'"),
    ("expect:\n  noop: true", "steps[0].expect.noop: must be 'flag' or 'ok'"),
    ("expect:\n  success_when: [a]", "steps[0].expect.success_when: must be one regular expression (text)"),
    ("expect:\n  success_when: '('", "steps[0].expect.success_when: not a valid regular expression"),
    ("expect:\n  noop_when: safe_noop", "steps[0].expect.noop_when: must be a list of regular expressions"),
    ("expect:\n  noop_when: [ok, '[']", "steps[0].expect.noop_when[1]: not a valid regular expression"),
    ("expect:\n  noop_when: [3]", "steps[0].expect.noop_when[0]: must be a regular expression (text)"),
])
def test_each_expect_error_names_the_field(tmp_path, block, error):  # AC-41
    wf = scaffold(tmp_path, block + "\n")
    assert wf["invalid"] is True
    assert any(e.startswith(error) for e in wf["errors"]), wf["errors"]
    assert wf["id"] == "demo" and wf["title"] == "Demo", "an invalid file still has a rail entry"


# ---- rule 1: Hermes's own status wins -----------------------------------
@pytest.mark.parametrize("status", ["script failed", "BLOCKED"])
def test_hermes_failure_outranks_a_done_result_line(status):
    trace = parse(status, "Script exited with code 1\nstdout:\n" + line(outcome="done") + "\n", allow_silent=True, noop="ok")
    assert trace["state"] == "failed"


# ---- rule 2: silent runs (AC-38) ----------------------------------------
def test_silent_run_is_failed_by_default():
    assert parse("silent (empty output)", "")["state"] == "failed"
    assert parse("silent (empty output)", "", allow_silent=False)["state"] == "failed"


def test_silent_run_is_ok_when_the_step_allows_it():
    trace = parse("silent (empty output)", "", allow_silent=True)
    assert (trace["state"], trace["first_error_line"], trace["local_sig"]) == ("ok", None, None)


# ---- rule 3: the result line (AC-39) ------------------------------------
def test_done_is_ok():
    trace = parse(body="---\n\nwork\n" + line(outcome="done") + "\n")
    assert (trace["state"], trace["outcome"], trace["reason"], trace["detail"]) == ("ok", "done", None, None)


def test_noop_is_noop_with_reason_and_detail():
    trace = parse(body="---\n\n" + line(outcome="noop", reason="quality_gate_not_met", detail="Manifest status is needs_research.") + "\n")
    assert (trace["state"], trace["outcome"]) == ("noop", "noop")
    assert (trace["reason"], trace["detail"]) == ("quality_gate_not_met", "Manifest status is needs_research.")
    assert trace["first_error_line"] is None and trace["local_sig"] is None


def test_failed_result_line_is_failed_even_with_a_clean_exit():
    trace = parse(body="---\n\n" + line(outcome="failed", reason="upstream_failed", detail="No manifest.") + "\n")
    assert trace["state"] == "failed" and trace["local_sig"].startswith("local-")
    assert trace["first_error_line"] == "upstream_failed: No manifest."


def test_the_last_result_line_counts():
    body = "---\n\n" + line(outcome="noop", reason="first") + "\nmore\n" + line(outcome="done") + "\n"
    assert parse(body=body)["state"] == "ok"


@pytest.mark.parametrize("bad", [
    "step_result={not json}",
    'step_result={"schema": 1, "outcome": "skipped"}',
    'step_result={"schema": 2, "outcome": "noop", "reason": "x"}',
    'step_result={"schema": true, "outcome": "noop", "reason": "x"}',
    'step_result=["noop"]',
    'step_result={"outcome": "noop", "reason": "x"}',
])
def test_invalid_result_line_is_ignored_and_reported(bad):
    trace = parse(body="---\n\n" + bad + "\n")
    assert (trace["state"], trace["outcome"]) == ("ok", None)
    assert trace["result_problem"]
    assert parse_result_line(bad + "\n")[0] is None


def test_invalid_last_line_does_not_hide_an_earlier_valid_one():
    body = "---\n\n" + line(outcome="noop", reason="nothing_new_upstream") + "\nstep_result={broken\n"
    trace = parse(body=body)
    assert trace["state"] == "noop" and trace["result_problem"]


def test_result_line_must_start_a_line():
    assert parse(body="---\n\nnote: step_result={\"schema\": 1, \"outcome\": \"noop\"}\n")["state"] == "ok"


def test_reason_and_detail_are_text_and_clipped():
    trace = parse(body="---\n\n" + line(outcome="noop", reason=7, detail="x" * 900) + "\n")
    assert trace["state"] == "noop" and trace["reason"] is None
    assert len(trace["detail"]) <= 301 and trace["detail"].endswith("\u2026")


def test_result_line_beats_the_patterns():
    body = "---\n\nsafe_noop=old line\n" + line(outcome="done") + "\n"
    assert parse(body=body, noop_when=["^safe_noop="])["state"] == "ok"


# ---- rule 4: fallback patterns (AC-40) ----------------------------------
def test_noop_when_match_is_noop_and_the_line_is_the_detail():
    trace = parse(body="---\n\nsafe_noop=manifest_not_eligible\nmanifest_status=needs_research\n", noop_when=["^safe_noop="])
    assert (trace["state"], trace["outcome"], trace["reason"]) == ("noop", None, None)
    assert trace["detail"] == "safe_noop=manifest_not_eligible"


def test_success_when_not_matched_is_noop():
    trace = parse(body="---\n\nchecked 4 notes\n", success_when="^published=")
    assert trace["state"] == "noop" and trace["detail"] is None


def test_success_when_matched_is_ok():
    assert parse(body="---\n\npublished=a.md\n", success_when="^published=")["state"] == "ok"


def test_noop_when_is_checked_before_success_when():
    body = "---\n\npublished=a.md\nsafe_noop=later\n"
    assert parse(body=body, success_when="^published=", noop_when=["^safe_noop="])["state"] == "noop"


def test_patterns_do_not_apply_to_a_failed_run():
    assert parse("script failed", "Script exited with code 1\nsafe_noop=x\n", noop_when=["^safe_noop="])["state"] == "failed"


# ---- rule 5 and the noop: ok setting ------------------------------------
def test_no_expect_and_no_line_is_ok_as_before():
    assert parse(body="---\n\nsafe_noop=manifest_not_eligible\n")["state"] == "ok"


def test_noop_on_a_step_that_accepts_it_is_ok_and_keeps_the_reason():
    trace = parse(body="---\n\n" + line(outcome="noop", reason="nothing_new_upstream", detail="No new failures.") + "\n", noop="ok")
    assert (trace["state"], trace["outcome"], trace["reason"], trace["detail"]) == (
        "ok", "noop", "nothing_new_upstream", "No new failures.")


def test_store_caches_per_expectation():
    class Fake:
        def list_outputs(self, job_id):
            return [(NAME, 1, 1)]

        def read_output(self, job_id, name):
            return HEAD + "**Status:** silent (empty output)\n"

    store = TraceStore(Fake())
    assert store.traces("j1")[0]["state"] == "failed"
    assert store.traces("j1", {**DEFAULT_EXPECT, "allow_silent": True})[0]["state"] == "ok"
    assert store.traces("j1")[0]["state"] == "failed"


# ---- step and workflow health (12.5) ------------------------------------
def tr(day, state="ok"):
    return {"time": f"2026-11-{day:02d}T08:00:00", "state": state, "file": f"d{day}.md"}


def step(sid, traces=(), depends_on=(), paused=False):
    return {"id": sid, "title": sid.upper(), "depends_on": list(depends_on), "paused": paused, "traces": list(traces)}


def health(steps):
    evaluate_steps(steps)
    return {s["id"]: s["health"] for s in steps}


def test_step_is_noop_when_its_latest_trace_is_noop():
    assert health([step("a", [tr(6, "noop"), tr(5)])]) == {"a": "noop"}


def test_later_ok_trace_clears_noop():
    assert health([step("a", [tr(7), tr(6, "noop")])]) == {"a": "ok"}


def test_noop_upstream_does_not_block():
    steps = [step("a", [tr(6, "noop")]), step("b", [tr(4)], depends_on=["a"])]
    assert health(steps) == {"a": "noop", "b": "ok"}
    assert steps[1]["blocked_by"] == []


def test_noop_step_under_a_failed_upstream_is_blocked():
    steps = [step("a", [tr(6, "failed")]), step("b", [tr(4, "noop")], depends_on=["a"])]
    assert health(steps) == {"a": "failed", "b": "blocked"}


def test_roll_up_order_is_failed_missed_blocked_noop_ok():
    def wf(*states):
        return [{"health": s, "paused": False} for s in states]
    assert roll_up(wf("ok", "noop")) == "noop"
    assert roll_up(wf("noop", "blocked")) == "blocked"
    assert roll_up(wf("noop", "missed")) == "missed"
    assert roll_up(wf("noop", "failed", "ok")) == "failed"
    assert roll_up(wf("noop", "none")) == "noop"
    assert roll_up([{"health": "noop", "paused": True}, {"health": "ok", "paused": False}]) == "ok"


def test_rail_puts_noop_between_blocked_and_ok():
    rows = [{"id": h, "title": h, "health": h, "problem_time": None, "last_run": None}
            for h in ("ok", "noop", "blocked", "failed", "missed")]
    assert [w["id"] for w in rail_order(rows)] == ["failed", "missed", "blocked", "noop", "ok"]
