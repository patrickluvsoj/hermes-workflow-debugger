"""Checks against the real Hermes profile. Read-only. Skipped unless WFD_LIVE=1.

Real job IDs are never written in this repository. They come from the scaffold
folder (WFD_SCAFFOLDS) or from WFD_LIVE_JOB_IDS (comma separated).
"""
import os
import re
import subprocess
from pathlib import Path

import pytest

from wfd.hermes_reader import HermesReader, default_profile_root
from wfd.scaffold import load_dir
from wfd.traces import TraceStore

pytestmark = pytest.mark.live
FAILED_GREP = r"^\*\*Status:\*\* (script failed|silent \(empty output\)|BLOCKED)$"


@pytest.fixture()
def live(tmp_path):
    return HermesReader(default_profile_root(), tmp_path / "data")


def live_job_ids(reader) -> list[str]:
    if os.environ.get("WFD_LIVE_JOB_IDS"):
        return [j.strip() for j in os.environ["WFD_LIVE_JOB_IDS"].split(",") if j.strip()]
    scaffolds = os.environ.get("WFD_SCAFFOLDS")
    assert scaffolds, "set WFD_LIVE_JOB_IDS or WFD_SCAFFOLDS"
    return [s["job_id"] for w in load_dir(Path(scaffolds), reader.jobs()) if not w["invalid"] for s in w["steps"]]


def test_every_live_output_file_is_classified(live):  # AC-4
    unknown = []
    store = TraceStore(live, on_unknown=lambda job, name, status: unknown.append((job, name, status)))
    job_ids = live_job_ids(live)
    assert len(job_ids) == 7
    report = []
    for job_id in job_ids:
        traces = store.traces(job_id)
        folder = live.output_dir(job_id)
        files = sorted(p.name for p in folder.glob("*.md"))
        assert len(traces) == len(files), f"{job_id}: every file must parse"
        assert all(t["state"] in ("ok", "failed") and t["time"] for t in traces)
        grep = subprocess.run(["grep", "-l", "-E", FAILED_GREP, *[str(folder / f) for f in files]],
                              capture_output=True, text=True)
        grep_failed = len([line for line in grep.stdout.splitlines() if line.strip()])
        parsed_failed = sum(1 for t in traces if t["state"] == "failed")
        report.append(f"{job_id[:4]}… files={len(files)} failed parser={parsed_failed} grep={grep_failed}")
        assert parsed_failed == grep_failed, report[-1]
    print("\n" + "\n".join(report))
    assert unknown == [], f"unknown status lines: {unknown}"


def test_live_failure_resolves_to_its_incident_signature(live):  # task 1.7
    """WFD_LIVE_SIG = <job_id>:<output file>:<expected error_sig>"""
    from datetime import datetime

    from wfd.state import step_traces

    job_id, name, expected = os.environ["WFD_LIVE_SIG"].split(":")
    traces = step_traces(live, TraceStore(live), live.jobs()[job_id], job_id, datetime.now())
    match = [t for t in traces if t["file"] == name]
    assert match and match[0]["error_sig"] == expected
    print(f"\n{name} -> error_sig={match[0]['error_sig']}")


def test_api_state_matches_the_newest_output_file_of_every_step(tmp_path):  # O2
    """Independent of the parser: the newest file is found with ls-style sorting
    and judged with a plain regex on its status line."""
    import json
    import threading
    import urllib.request

    from wfd.context import build_context
    from wfd.server import make_server

    context = build_context(data_dir=tmp_path)
    server = make_server(context, 0)
    threading.Thread(target=lambda: server.serve_forever(poll_interval=0.02), daemon=True).start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_address[1]}/api/state") as response:
            state = json.loads(response.read())
    finally:
        server.shutdown()
        server.server_close()
    assert state["mode"] == "live"
    rows = []
    for workflow in state["workflows"]:
        for step in workflow["steps"]:
            folder = context.profile_root / "cron" / "output" / step["job_id"]
            newest = sorted(p.name for p in folder.glob("*.md"))[-1]
            head = (folder / newest).read_text(errors="replace").split("\n\n", 2)
            failed = bool(re.search(FAILED_GREP, "\n\n".join(head[:2]), re.M))
            api = next((t for t in [step["latest"]] if t and t["state"] != "missed"), None)
            shown = (api["file"], api["state"]) if api else (None, None)
            if step["latest"] and step["latest"]["state"] == "missed":
                shown = ("(missed is newer) " + str(step["last_run"]), "missed")
            ok = shown == (newest, "failed" if failed else "ok")
            rows.append((ok, f"{step['title']:<18} disk={newest} {'failed' if failed else 'ok':<6} api={shown[0]} {shown[1]}"))
    print("\n" + "\n".join(("MATCH  " if ok else "DIFF   ") + line for ok, line in rows))
    assert len(rows) == 7 and all(ok for ok, _ in rows)
