"""Plan section 6: every fixture scenario gives its expected health (AC-5)."""
import build_fixtures

from wfd.hermes_reader import HermesReader
from wfd.state import step_traces
from wfd.traces import TraceStore

NOW = build_fixtures.NOW


def by_id(state):
    return {w["id"]: w for w in state["workflows"]}


def steps(workflow):
    return {s["id"]: s for s in workflow["steps"]}


def test_f1_analysis_failed_gate_blocked(fixture_state):
    wf = by_id(fixture_state())["industry-analysis-blog-post"]
    s = steps(wf)
    assert wf["health"] == "failed"
    assert [x["id"] for x in wf["steps"]] == ["watchlist-scout", "industry-analysis", "publish-gate"]
    assert (s["watchlist-scout"]["health"], s["industry-analysis"]["health"], s["publish-gate"]["health"]) == (
        "ok", "failed", "blocked")
    assert s["publish-gate"]["blocked_by"] == ["Industry analysis"]
    assert s["industry-analysis"]["summary"]["status"] == "ready"
    assert "403 Forbidden" in s["industry-analysis"]["summary"]["text"]
    assert s["industry-analysis"]["latest"]["error_sig"] == "7d1e0a4c92bf"
    assert [p["step_id"] for p in wf["problems"]] == ["industry-analysis"]
    assert wf["last_run"] == "2026-11-11T08:01:09"


def test_f1_is_first_in_the_rail(fixture_state):
    assert [w["id"] for w in fixture_state()["workflows"]] == [
        "industry-analysis-blog-post",     # failed Nov 11
        "weekly-store-sync",               # failed Nov 10
        "weekly-ai-analysis-publication",  # failed Nov 8
        "broken-example",                  # failed, bad file
        "google-oauth-token-refresh",      # missed
        "hermes-cron-error-email-report",  # ok
    ]


def test_f2_briefing_blocked_by_scanner_gate_paused(fixture_state):
    wf = by_id(fixture_state())["weekly-ai-analysis-publication"]
    s = steps(wf)
    assert wf["health"] == "failed" and wf["paused_count"] == 1
    assert s["briefing"]["health"] == "failed"
    assert s["briefing"]["latest"]["status_line"] == "BLOCKED"
    assert s["briefing"]["summary"]["status"] == "pending"
    assert s["briefing"]["summary"]["text"] is None
    assert s["briefing"]["summary"]["first_error_line"].startswith("Blocked: prompt matches threat pattern")
    assert s["publish-gate"]["paused"] is True
    assert s["publish-gate"]["health"] == "ok", "a paused step keeps its own result and is never blocked"
    assert s["publish-gate"]["paused_since"] == "2026-09-08T14:05:00"


def test_f3_token_refresh_missed(fixture_state):
    wf = by_id(fixture_state())["google-oauth-token-refresh"]
    assert wf["health"] == "missed" and len(wf["steps"]) == 1
    step = wf["steps"][0]
    assert step["health"] == "missed" and step["depends_on"] == []
    assert step["latest"]["state"] == "missed" and step["latest"]["time"] == "2026-11-12T06:00:00"
    assert step["last_run"] == "2026-11-12T00:00:02"
    assert wf["problems"] == [{"step_id": "refresh", "title": "Token refresh", "kind": "missed",
                               "time": "2026-11-12T06:00:00", "summary": None}]


def test_f4_email_report_ok_with_old_failures_kept(fixture_state, reader):
    wf = by_id(fixture_state())["hermes-cron-error-email-report"]
    assert wf["health"] == "ok" and wf["problems"] == []
    assert wf["steps"][0]["summary"] is None
    traces = step_traces(reader, TraceStore(reader), reader.jobs()["d4f0c0de0001"], "d4f0c0de0001", NOW)
    assert [(t["time"][:10], t["state"]) for t in traces if t["state"] != "ok"] == [
        ("2026-11-09", "missed"), ("2026-11-07", "failed"), ("2026-11-05", "failed")]
    assert {t["status_line"] for t in traces if t["state"] == "failed"} == {"silent (empty output)", "script failed"}


def test_f5_two_failures_one_step_without_files(fixture_state, reader):
    wf = by_id(fixture_state())["weekly-store-sync"]
    s = steps(wf)
    assert wf["health"] == "failed"
    assert (s["pull-orders"]["health"], s["pull-inventory"]["health"], s["archive"]["health"]) == (
        "failed", "failed", "none")
    assert [p["step_id"] for p in wf["problems"]] == ["pull-orders", "pull-inventory"]
    assert all(p["summary"]["status"] == "ready" for p in wf["problems"])
    assert s["pull-orders"]["latest"]["session_id"] == "20261110_020011_ab12cd"
    assert s["pull-inventory"]["latest"]["session_id"] is None
    assert s["pull-inventory"]["latest"]["error_sig"].startswith("local-"), "no incident row: local fallback"
    assert s["archive"]["latest"] is None and s["archive"]["trace_count"] == 0


def test_f6_bad_file_is_listed_as_failed_and_names_the_field(fixture_state):
    wf = by_id(fixture_state())["broken-example"]
    assert wf["invalid"] is True and wf["health"] == "failed"
    assert wf["error"] == "steps[0].job_id: job 'ffffffffffff' is not in cron/jobs.json"
    assert wf["file"] == "broken-example.yaml" and wf["steps"] == []


def test_f7_no_scaffold_files(fixture_state, fx):
    state = fixture_state("scaffolds-empty")
    assert state["workflows"] == []
    assert state["scaffold_dir"] == str(fx / "scaffolds-empty")
    assert state["mode"] == "fixtures"


def test_duration_comes_from_executions(fixture_state):
    wf = by_id(fixture_state())["industry-analysis-blog-post"]
    assert steps(wf)["industry-analysis"]["latest"]["duration_s"] == 68.0


def test_state_holds_only_the_latest_trace_per_step(fixture_state):
    for wf in fixture_state()["workflows"]:
        for step in wf["steps"]:
            assert "traces" not in step and "own" not in step
