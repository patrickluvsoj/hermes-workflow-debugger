"""AC-22: compare job configuration with a saved baseline, ignoring run-time fields.

    python tools/compare_jobs.py <baseline.json> [jobs.json]

The baseline is {job_id: config}. Exit code 0 when every baseline job is unchanged.
"""
import json
import sys
from pathlib import Path

RUNTIME = {"next_run_at", "repeat", "failure_streak", "fire_claim"}


def config(job: dict) -> dict:
    return {k: v for k, v in job.items() if k not in RUNTIME and not k.startswith("last_")}


def main() -> int:
    baseline = json.loads(Path(sys.argv[1]).read_text())
    live_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path.home() / ".hermes/profiles/daily/cron/jobs.json"
    live = {job["id"]: config(job) for job in json.loads(live_path.read_text())["jobs"]}
    bad = 0
    for job_id, before in baseline.items():
        after = live.get(job_id)
        changed = sorted(k for k in set(before) | set(after or {}) if before.get(k) != (after or {}).get(k))
        print(f"{'SAME   ' if after is not None and not changed else 'CHANGED'} {job_id}" + (f"  {changed}" if changed else ""))
        bad += bool(changed) or after is None
    print(f"{len(baseline) - bad} of {len(baseline)} unchanged; jobs in file now: {len(live)}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
