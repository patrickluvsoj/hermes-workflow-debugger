"""AC-1: the real workflow files load and point at real jobs. Read-only. WFD_LIVE=1."""
import os
from pathlib import Path

import pytest

from wfd.context import SKILL_SCAFFOLDS
from wfd.hermes_reader import HermesReader, default_profile_root
from wfd.scaffold import load_dir

pytestmark = pytest.mark.live


def test_four_workflow_files_load_with_seven_real_jobs(tmp_path):
    profile = default_profile_root()
    reader = HermesReader(profile, tmp_path / "data")
    jobs = reader.jobs()
    folder = Path(os.environ.get("WFD_SCAFFOLDS") or profile / SKILL_SCAFFOLDS)
    workflows = load_dir(folder, jobs)
    assert len(workflows) == 4
    assert [w["errors"] for w in workflows] == [[], [], [], []]
    job_ids = [s["job_id"] for w in workflows for s in w["steps"]]
    assert len(job_ids) == 7 and len(set(job_ids)) == 7
    assert all(job_id in jobs for job_id in job_ids)
    expected = os.environ.get("WFD_LIVE_JOB_IDS")
    if expected:
        assert set(job_ids) == {j.strip() for j in expected.split(",")}
    for w in workflows:
        print(f"\n{w['file']}: " + ", ".join(f"{s['id']}={s['job_id'][:4]}…" for s in w["steps"]), end="")
    print()
