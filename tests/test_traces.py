import pytest

from wfd.traces import TraceStore, is_failure_line, parse_trace

HEAD = "# Cron Job: Example\n\n**Job ID:** j1\n**Run Time:** 2026-11-08 21:01:00\n**Mode:** no_agent (script)\n"
NAME = "2026-11-08_21-01-00.md"


def parse(status, body="some output\n"):
    status_line = f"**Status:** {status}\n" if status else ""
    return parse_trace("j1", NAME, HEAD + status_line + "\n" + body)


@pytest.mark.parametrize("status", ["script failed", "silent (empty output)", "BLOCKED"])
def test_three_status_values_mean_failed(status):
    trace = parse(status)
    assert (trace["state"], trace["status_line"], trace["status_known"]) == ("failed", status, True)
    assert trace["local_sig"].startswith("local-")


def test_published_status_is_ok():
    trace = parse("\u2705 Published")
    assert (trace["state"], trace["status_known"]) == ("ok", True)


def test_missing_status_line_is_ok():
    trace = parse(None)
    assert (trace["state"], trace["status_line"], trace["first_error_line"]) == ("ok", None, None)


def test_unrecognised_status_is_ok_but_flagged():
    trace = parse("partially done")
    assert (trace["state"], trace["status_known"]) == ("ok", False)


def test_status_line_inside_the_body_is_not_the_status():
    trace = parse(None, "---\n\nreport text\n**Status:** script failed\n")
    assert trace["state"] == "ok"


def test_time_comes_from_run_time_then_file_name():
    assert parse(None)["time"] == "2026-11-08T21:01:00"
    assert parse_trace("j1", "2026-11-09_07-30-15.md", "no header at all\n")["time"] == "2026-11-09T07:30:15"


def test_session_id_none_is_not_a_session():
    body = "failure_reason=probe failed rc=1; session_id=none; tail=could not start\n"
    trace = parse("script failed", body)
    assert trace["session_id"] is None
    assert trace["first_error_line"] == "probe failed rc=1; session_id=none; tail=could not start"


@pytest.mark.parametrize("value", ["20260102_030405_abc123", "local-0123456789abcdef0123456789abcdef"])
def test_session_id_is_found_and_trailing_punctuation_dropped(value):
    assert parse(None, f"generated ok; session_id={value}; note=x\n")["session_id"] == value


def test_workflow_run_id_is_parsed_and_placeholders_ignored():
    assert parse(None, "workflow_run_id=wfr-demo-20261112T190156Z-ab12\n")["workflow_run_id"] == "wfr-demo-20261112T190156Z-ab12"
    assert parse(None, "workflow_run_id=unattached\n")["workflow_run_id"] is None
    assert parse(None, "nothing here\n")["workflow_run_id"] is None


def test_first_error_line_prefers_reason_then_scanner_then_exception():
    assert parse("BLOCKED", "intro\n\n**Scanner result:** Blocked: pattern x\n")["first_error_line"] == "Blocked: pattern x"
    tb = "Script exited with code 2\nstderr:\nTraceback (most recent call last):\n  File \"a.py\"\nValueError: bad\n"
    assert parse("script failed", tb)["first_error_line"] == "ValueError: bad"
    assert parse("script failed", "Script exited with code 3\n")["first_error_line"] == "Script exited with code 3"
    assert parse("silent (empty output)", "")["first_error_line"] == "silent (empty output)"


def test_local_signature_is_stable_and_depends_on_job_and_error():
    one = parse("script failed", "boom\n")["local_sig"]
    assert one == parse("script failed", "boom\n")["local_sig"]
    assert one != parse("script failed", "bang\n")["local_sig"]
    assert one != parse_trace("j2", NAME, HEAD + "**Status:** script failed\n\nboom\n")["local_sig"]


def test_failure_lines():
    assert is_failure_line("**Status:** script failed")
    assert is_failure_line("failure_reason=x")
    assert is_failure_line("PermissionError: nope")
    assert is_failure_line("Script exited with code 1")
    assert not is_failure_line("**Status:** \u2705 Published")
    assert not is_failure_line("rows_written=0")


def test_store_reports_unknown_status_once(reader, tmp_path):
    class Fake:
        def list_outputs(self, job_id):
            return [("2026-11-08_21-01-00.md", 1, 1), ("2026-11-07_21-01-00.md", 1, 1)]

        def read_output(self, job_id, name):
            return HEAD + "**Status:** weird\n\nbody\n"

    seen = []
    store = TraceStore(Fake(), on_unknown=lambda *a: seen.append(a))
    assert len(store.traces("j1")) == 2 and len(store.traces("j1")) == 2
    assert seen == [("j1", "2026-11-08_21-01-00.md", "weird")]


def test_store_parses_fixture_job(reader):
    traces = TraceStore(reader).traces("d4f0c0de0001")
    assert [t["state"] for t in traces] == ["ok", "ok", "ok", "ok", "failed", "ok", "failed", "ok", "ok"]
    assert all(t["status_known"] for t in traces)
