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
