import sqlite3

import pytest

from wfd.summaries import SummaryStore


def test_creates_table_and_round_trips(tmp_path):
    store = SummaryStore(tmp_path / "sub" / "debugger.db")
    assert store.get("j1", "sig") is None
    store.put("j1", "sig", trace_file="a.md", status="ready", summary="One. Two.", session_id="s1", attempts=1,
              created_at="2026-11-12T10:00:00")
    assert store.get("j1", "sig") == {"job_id": "j1", "error_sig": "sig", "trace_file": "a.md", "status": "ready",
                                      "summary": "One. Two.", "session_id": "s1", "attempts": 1,
                                      "created_at": "2026-11-12T10:00:00"}


def test_key_is_job_plus_signature_and_put_replaces(tmp_path):
    store = SummaryStore(tmp_path / "debugger.db")
    store.put("j1", "a", trace_file=None, status="pending", summary=None, session_id=None, attempts=1)
    store.put("j1", "a", trace_file=None, status="ready", summary="x", session_id=None, attempts=2)
    store.put("j1", "b", trace_file=None, status="unavailable", summary=None, session_id=None, attempts=3)
    store.put("j2", "a", trace_file=None, status="pending", summary=None, session_id=None, attempts=1)
    assert (store.get("j1", "a")["status"], store.get("j1", "a")["attempts"]) == ("ready", 2)
    assert store.get("j1", "b")["status"] == "unavailable" and store.get("j2", "a")["status"] == "pending"
    assert sqlite3.connect(tmp_path / "debugger.db").execute("select count(*) from summaries").fetchone() == (3,)


def test_unknown_status_is_rejected(tmp_path):
    with pytest.raises(sqlite3.IntegrityError):
        SummaryStore(tmp_path / "d.db").put("j", "s", trace_file=None, status="great", summary=None, session_id=None, attempts=1)


def test_readonly_store_cannot_write_and_tolerates_a_missing_file(tmp_path):
    assert SummaryStore(tmp_path / "none.db", readonly=True).get("j", "s") is None
    assert not (tmp_path / "none.db").exists()
    SummaryStore(tmp_path / "d.db").put("j", "s", trace_file=None, status="ready", summary="x", session_id=None, attempts=1)
    reader = SummaryStore(tmp_path / "d.db", readonly=True)
    assert reader.get("j", "s")["summary"] == "x"
    with pytest.raises(RuntimeError):
        reader.put("j", "s", trace_file=None, status="ready", summary="y", session_id=None, attempts=1)
