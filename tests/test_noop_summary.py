"""Plan section 13: which no-op gets a reason summary, and where its text comes from."""
import pytest

from wfd.state import noop_problem, noop_summary_mode
from wfd.traces import noop_sig
from wfd.watcher import build_prompt, check_summary


def trace(**fields):
    return {"job_id": "j1", "state": "noop", "time": "2026-11-12T08:00:00", "outcome": "noop", "reason": None,
            "detail": None, "noop_sig": "noop-abc", **fields}


@pytest.mark.parametrize("fields, mode", [
    ({"reason": "quality_gate_not_met", "detail": "The draft scored 61."}, "script"),   # row 1
    ({"reason": "already_published"}, "ai"),                                            # row 2
    ({"outcome": None, "detail": "safe_noop=manifest_not_eligible"}, "ai"),             # row 3
    ({"outcome": None}, "ai"),                                                          # row 4
    ({"reason": "check_only", "detail": "Check mode."}, "none"),                        # row 8
    ({"reason": "canary_only", "detail": "Canary run."}, "none"),                       # row 8
    ({"reason": "upstream_failed", "detail": "Manifest status is failed."}, "line"),    # row 9
])
def test_mode_per_scenario(fields, mode):
    assert noop_summary_mode(trace(**fields)) == mode


class Store:
    def __init__(self, row=None):
        self.row = row

    def get(self, job_id, sig):
        assert (job_id, sig) == ("j1", "noop-abc")
        return self.row


def step(health="noop", paused=False, depends_on=(), **fields):
    return {"id": "gate", "title": "Gate", "health": health, "paused": paused, "depends_on": list(depends_on),
            "latest": trace(**fields)}


def test_script_sentence_is_shown_as_written_without_the_store():
    entry = noop_problem(step(reason="quality_gate_not_met", detail="The draft scored 61."), [], None)
    assert (entry["kind"], entry["summary"]["status"], entry["summary"]["text"]) == ("noop", "script", "The draft scored 61.")
    assert entry["summary"]["reason"] == "quality_gate_not_met"


@pytest.mark.parametrize("row, status, text", [
    (None, "pending", None),
    ({"status": "pending", "summary": None, "created_at": "x"}, "pending", None),
    ({"status": "unavailable", "summary": None, "created_at": "x"}, "unavailable", None),
    ({"status": "ready", "summary": "It checked and found nothing. So it stopped.", "created_at": "t"}, "ready",
     "It checked and found nothing. So it stopped."),
])
def test_written_summary_states(row, status, text):
    summary = noop_problem(step(outcome=None, detail="safe_noop=x"), [], Store(row))["summary"]
    assert (summary["status"], summary["text"], summary["fallback"]) == (status, text, "safe_noop=x")


def test_fallback_text_when_the_script_printed_nothing_useful():
    assert noop_problem(step(outcome=None), [], Store())["summary"]["fallback"] == "The expected success line was not printed."
    assert noop_problem(step(reason="already_published"), [], Store())["summary"]["fallback"] == "already_published"


@pytest.mark.parametrize("kwargs", [
    {"paused": True, "reason": "quality_gate_not_met", "detail": "x"},   # row 13
    {"health": "ok", "reason": "quality_gate_not_met", "detail": "x"},   # rows 5, 11
    {"health": "blocked", "reason": "quality_gate_not_met", "detail": "x"},
    {"reason": "check_only"},                                            # row 8
])
def test_no_entry(kwargs):
    assert noop_problem(step(**kwargs), [], Store()) is None


def test_upstream_line_names_the_failed_step():
    gate = step(reason="upstream_failed", depends_on=["draft", "other"])
    others = [{"id": "draft", "title": "Draft", "health": "failed"}, {"id": "other", "title": "Other", "health": "ok"}]
    entry = noop_problem(gate, others, Store())
    assert (entry["summary"]["mode"], entry["upstream_failed"]) == ("line", ["Draft"])


def test_same_cause_on_another_run_has_the_same_signature():  # row 12
    a = "workflow_run_id=wfr-x-20261106T180005Z-ab12\nsafe_noop=manifest_not_eligible\nmanifest_status=needs_research\nmanifest_run_id=20261016T152725311019Z-5778\n"
    b = "workflow_run_id=wfr-x-20261113T180003Z-9f3e\nsafe_noop=manifest_not_eligible\nmanifest_status=needs_research\nmanifest_run_id=20261113T160000000000Z-1201\n"
    c = b.replace("needs_research", "failed")
    assert noop_sig("j1", a) == noop_sig("j1", b) != noop_sig("j1", c)
    assert noop_sig("j1", a) != noop_sig("j2", a) and noop_sig("j1", a).startswith("noop-")


def test_noop_prompt_does_not_call_it_a_failure():
    workflow = {"title": "W", "description": "Does W."}
    stp = {"id": "send", "title": "Send", "description": "Sends.", "depends_on": ["gate"]}
    prompt = build_prompt(workflow, "id: w", stp, trace(reason="already_published", status_line=None), "recipients=0")
    assert "ran without an error and did nothing" in prompt and "Step that did nothing: Send" in prompt
    assert "already_published" in prompt and "Step that failed" not in prompt and "<<<OUTPUT\nrecipients=0\nOUTPUT>>>" in prompt
    assert check_summary("The send step found no approved draft. It sent nothing this week.") == []
