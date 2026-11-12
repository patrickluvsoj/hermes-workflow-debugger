"""Build a made-up Hermes profile, scaffold folder and summary store.

Everything in here is invented: job IDs, names, errors and session IDs. The
output is shaped like a real profile (cron/jobs.json, cron/output/<job_id>/*.md,
cron/executions.db, scripts/) so the debugger runs against it unchanged.

    python fixtures/build_fixtures.py [output folder]

The clock is fixed at NOW, so the result never drifts. Running it twice gives
identical files.

Scenarios
  F1 Industry Analysis Blog Post     analysis failed after the scout; gate blocked; summary ready
  F2 Weekly AI Analysis Publication  briefing BLOCKED by the prompt scanner; no summary; gate paused
  F3 Google OAuth token refresh      latest scheduled run never ran (missed)
  F4 Hermes CRON error email report  ok now; old failures and one missed day in the history
  F5 Weekly Store Sync               two independent failures with summaries; one step with no files
  F6 broken-example.yaml             scaffold that points at an unknown job
  F7 scaffolds-empty/                no scaffold files at all
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from wfd.summaries import SCHEMA as SUMMARY_SCHEMA  # noqa: E402
from wfd.traces import parse_trace  # noqa: E402

NOW = datetime(2026, 11, 12, 10, 42, 0)
MARKER = "fixture.json"

EXECUTIONS_SCHEMA = """
create table executions (
  id text primary key, job_id text not null, source text not null, process_id text not null,
  pid integer not null, process_started_at integer, status text not null, claimed_at text not null,
  started_at text, finished_at text, error text, handoff_pending integer not null default 0,
  handoff_started_at real, scheduled_instant text, delivery_outcome text, progress_at text);
create table cron_incidents (
  id text primary key, job_id text not null, error_sig text not null, state text not null,
  failure_type text not null default 'unknown', first_seen_at text not null, last_seen_at text not null,
  acked_at text, closed_at text, error text not null, output_file text, alerted_at text);
"""


def at(month: int, day: int, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, month, day, hour, minute, second)


def ok_body(lines: str) -> str:
    return "---\n\n" + lines.strip() + "\n"


FAIL_ANALYSIS = """Script exited with code 1
stdout:
# Industry analysis generation
run_id=20261111T160004Z-41877
step=model-availability-probe
step_exit=model-availability-probe code=0 output_bytes=64
step=source-collection
step_exit=source-collection code=1 output_bytes=212
failure_reason=source collection failed: HTTPError: 403 Forbidden for https://filings.example.com/ACME/10-K
source_packet=
workflow_run_id=wfr-industry-analysis-blog-post-20261109T040004Z-1a2b
"""
FAIL_ANALYSIS_OLD = """Script exited with code 1
stdout:
# Industry analysis generation
run_id=20261021T150003Z-20114
step=note-validation
failure_reason=note validation failed: 2 required sections missing (Unit economics, Risks)
"""
BLOCKED_BRIEFING = """The assembled prompt (user prompt + loaded skill content) tripped the cron injection scanner and the agent was NOT run.

**Scanner result:** Blocked: prompt matches threat pattern 'read_secrets'. Cron prompts must not contain injection or exfiltration payloads.

Audit the skill(s) attached to this job for prompt-injection payloads or invisible-unicode markers.
"""
FAIL_ORDERS = """Script exited with code 1
stdout:
# Store sync: orders
step=export
session_id=20261110_020011_ab12cd
failure_reason=orders export failed: TimeoutError: no response from https://shop.example.com/api/orders after 3 attempts (30s each)
rows_written=0
"""
FAIL_INVENTORY = """Script exited with code 2
stderr:
Traceback (most recent call last):
  File "store-sync-inventory.py", line 88, in main
    rows = load_sheet(SHEET_ID)
  File "store-sync-inventory.py", line 41, in load_sheet
    raise PermissionError(f"sheet {sheet_id} is not shared with the service account")
PermissionError: sheet inv-2026 is not shared with the service account
"""
FAIL_MAIL = """Script exited with code 1
stderr:
smtplib.SMTPAuthenticationError: (535, b'5.7.8 Username and Password not accepted')
"""
SILENT = "Script produced no output.\n"

# job_id -> definition. Trace tuples: (time, kind, body, duration seconds)
JOBS = [
    {"id": "a1f0c0de0001", "name": "Industry Watchlist Scout", "script": "industry-watchlist-scout.py",
     "schedule": ("cron", "0 20 * * 0"), "next": at(11, 15, 20), "changed": at(11, 2, 9, 14),
     "traces": [(at(m, d, 20, 0, 52), "ok", ok_body("candidates_scored=18\nadded=2\nworkflow_run_id=wfr-industry-analysis-blog-post-2026%02d%02dT040004Z-1a2b" % (m, d + 1)), 51)
                for m, d in ((11, 8), (11, 1), (10, 25), (10, 18))]},
    {"id": "a1f0c0de0002", "name": "Industry + Business Analysis to Knowledge OS", "script": "industry-analysis-runner.py",
     "schedule": ("cron", "0 8 * * 3"), "next": at(11, 18, 8), "changed": at(11, 10, 17, 40),
     "traces": [(at(11, 11, 8, 1, 9), "script failed", FAIL_ANALYSIS, 68),
                (at(11, 4, 8, 6, 31), "ok", ok_body("note=Acme Robotics.md\ngeneration_manifest=written"), 390),
                (at(10, 28, 8, 5, 58), "ok", ok_body("note=Borealis Freight.md\ngeneration_manifest=written"), 357),
                (at(10, 21, 8, 4, 2), "script failed", FAIL_ANALYSIS_OLD, 241),
                (at(10, 14, 8, 6, 12), "ok", ok_body("note=Cobalt Health.md\ngeneration_manifest=written"), 371)]},
    {"id": "a1f0c0de0003", "name": "Industry analysis publish gate", "script": "industry-analysis-publish-gate.py",
     "schedule": ("cron", "0 12 * * 4"), "next": at(11, 12, 12), "changed": at(10, 3, 11, 5),
     "traces": [(at(11, 5, 12, 0, 21), "ok", ok_body("published=Acme Robotics.md\ndeploy=ok"), 20),
                (at(10, 29, 12, 0, 19), "ok", ok_body("published=Borealis Freight.md\ndeploy=ok"), 18),
                (at(10, 22, 12, 0, 4), "ok", ok_body("safe_noop=manifest_not_eligible"), 3)]},
    {"id": "b2f0c0de0001", "name": "Weekly AI analysis briefing to Knowledge OS", "script": "ai-briefing.py",
     "schedule": ("cron", "0 21 * * 0"), "next": at(11, 15, 21), "changed": at(11, 7, 16, 22),
     "traces": [(at(11, 8, 21, 0, 3), "BLOCKED", BLOCKED_BRIEFING, 2),
                (at(11, 1, 21, 4, 40), "ok", ok_body("briefing=2026-11-01.md\nsources=41"), 278),
                (at(10, 25, 21, 4, 12), "ok", ok_body("briefing=2026-10-25.md\nsources=37"), 250),
                (at(10, 18, 21, 3, 55), "ok", ok_body("briefing=2026-10-18.md\nsources=44"), 233)]},
    {"id": "b2f0c0de0002", "name": "Weekly AI analysis publish gate", "script": "ai-publish-gate.py",
     "schedule": ("cron", "30 23 * * 0"), "next": None, "changed": at(8, 30, 10, 0), "paused_at": at(9, 8, 14, 5),
     "traces": [(at(9, 6, 23, 30, 17), "ok", ok_body("published=2026-09-06.md\ndeploy=ok"), 16),
                (at(8, 30, 23, 30, 15), "ok", ok_body("published=2026-08-30.md\ndeploy=ok"), 14)]},
    {"id": "c3f0c0de0001", "name": "google-token-refresh", "script": "google-token-refresh.sh",
     "schedule": ("interval", 360), "next": at(11, 12, 6), "changed": at(9, 19, 8, 30),
     "traces": [(t, "ok", ok_body('{"aggregate":{"status":"ok"},"targets":[{"label":"daily","status":"ok"}]}'), 1)
                for t in (at(11, 12, 0, 0, 2), at(11, 11, 18, 0, 2), at(11, 11, 12, 0, 2), at(11, 11, 6, 0, 2),
                          at(11, 11, 0, 0, 2), at(11, 10, 18, 0, 2))]},
    {"id": "d4f0c0de0001", "name": "Daily cron failure Gmail alert", "script": "cron-health-gmail-alert.py",
     "schedule": ("cron", "0 8 * * *"), "next": at(11, 13, 8), "changed": at(10, 20, 13, 45),
     "traces": [(at(11, 12, 8, 0, 6), "ok", ok_body("failures_last_24h=2\nemail=sent"), 5),
                (at(11, 11, 8, 0, 5), "ok", ok_body("failures_last_24h=1\nemail=sent"), 4),
                (at(11, 10, 8, 0, 5), "ok", ok_body("failures_last_24h=0\nemail=skipped"), 4),
                # Nov 9: scheduled, no execution, no file -> missed in the history
                (at(11, 8, 8, 0, 6), "ok", ok_body("failures_last_24h=0\nemail=skipped"), 5),
                (at(11, 7, 8, 0, 4), "silent (empty output)", SILENT, 3),
                (at(11, 6, 8, 0, 5), "ok", ok_body("failures_last_24h=1\nemail=sent"), 4),
                (at(11, 5, 8, 0, 7), "script failed", FAIL_MAIL, 6),
                (at(11, 4, 8, 0, 5), "ok", ok_body("failures_last_24h=0\nemail=skipped"), 4),
                (at(11, 3, 8, 0, 5), "ok", ok_body("failures_last_24h=0\nemail=skipped"), 4)]},
    {"id": "e5f0c0de0001", "name": "Store sync: orders", "script": "store-sync-orders.py",
     "schedule": ("cron", "0 2 * * 2"), "next": at(11, 17, 2), "changed": at(10, 30, 21, 10),
     "traces": [(at(11, 10, 2, 1, 43), "script failed", FAIL_ORDERS, 102),
                (at(11, 3, 2, 0, 38), "ok", ok_body("rows_written=412"), 37),
                (at(10, 27, 2, 0, 35), "ok", ok_body("rows_written=388"), 34)]},
    {"id": "e5f0c0de0002", "name": "Store sync: inventory", "script": "store-sync-inventory.py",
     "schedule": ("cron", "30 2 * * 2"), "next": at(11, 17, 2, 30), "changed": at(10, 30, 21, 12),
     "traces": [(at(11, 10, 2, 30, 4), "script failed", FAIL_INVENTORY, 3),
                (at(11, 3, 2, 30, 21), "ok", ok_body("rows_written=96"), 20)]},
    {"id": "e5f0c0de0003", "name": "Store sync: archive", "script": "store-sync-archive.py",
     "schedule": ("cron", "0 3 1 * *"), "next": at(12, 1, 3), "changed": at(11, 5, 9, 0), "traces": []},
    # Not named by any workflow file: the dashboard must not show it or serve its files.
    {"id": "f6f0c0de0001", "name": "Nightly notes backup", "script": "notes-backup.sh",
     "schedule": ("cron", "0 4 * * *"), "next": at(11, 13, 4), "changed": at(7, 1, 12, 0),
     "traces": [(at(11, 12, 4, 0, 9), "ok", ok_body("archived=214 files"), 8)]},
]

INCIDENTS = {  # (job_id, trace time) -> error_sig
    ("a1f0c0de0002", at(11, 11, 8, 1, 9)): "7d1e0a4c92bf",
    ("a1f0c0de0002", at(10, 21, 8, 4, 2)): "0c55e1b7a3d4",
    ("e5f0c0de0001", at(11, 10, 2, 1, 43)): "3b9f5e7a1c02",
    ("d4f0c0de0001", at(11, 5, 8, 0, 7)): "f20a6d9914ce",
}

SUMMARIES = {  # (job_id, trace time) -> (summary, written at)
    ("a1f0c0de0002", at(11, 11, 8, 1, 9)): (
        "The source collection stage could not download the ACME 10-K because the filings site answered "
        "403 Forbidden. The run stopped there, so no analysis note or manifest was written. "
        "Publish gate has nothing new to publish.", at(11, 11, 8, 15, 20)),
    ("e5f0c0de0001", at(11, 10, 2, 1, 43)): (
        "The orders export got no response from the shop API in three attempts of 30 seconds each. "
        "No rows were written.", at(11, 10, 2, 15, 9)),
    ("e5f0c0de0002", at(11, 10, 2, 30, 4)): (
        "The inventory script could not open the sheet inv-2026 because it is not shared with the service account. "
        "The script stopped with a PermissionError before reading any rows.", at(11, 10, 2, 45, 12)),
}

WORKFLOWS = {
    "industry-analysis-blog-post.yaml": """schema_version: 1
id: industry-analysis-blog-post
title: Industry Analysis Blog Post
description: Scouts the watch list, writes an industry analysis and publishes it to Knowledge OS.
steps:
  - id: watchlist-scout
    title: Watch list scout
    description: Scores new candidates and adds them to the watch list.
    job_id: a1f0c0de0001
    depends_on: []
  - id: industry-analysis
    title: Industry analysis
    description: Picks a company from the watch list and writes the analysis note.
    job_id: a1f0c0de0002
    depends_on: [watchlist-scout]
  - id: publish-gate
    title: Publish gate
    description: Publishes the note if the analysis succeeded.
    job_id: a1f0c0de0003
    depends_on: [industry-analysis]
""",
    "weekly-ai-analysis-publication.yaml": """schema_version: 1
id: weekly-ai-analysis-publication
title: Weekly AI Analysis Publication
description: Writes the weekly AI briefing and publishes it to Knowledge OS.
steps:
  - id: briefing
    title: Briefing
    description: Gathers sources and writes the briefing note.
    job_id: b2f0c0de0001
    depends_on: []
  - id: publish-gate
    title: Publish gate
    description: Publishes the note if the briefing succeeded.
    job_id: b2f0c0de0002
    depends_on: [briefing]
""",
    "google-oauth-token-refresh.yaml": """schema_version: 1
id: google-oauth-token-refresh
title: Google OAuth token refresh
description: Refreshes the Google OAuth token every six hours.
steps:
  - id: refresh
    title: Token refresh
    description: Refreshes the token file used by the Google tools.
    job_id: c3f0c0de0001
    depends_on: []
""",
    "hermes-cron-error-email-report.yaml": """schema_version: 1
id: hermes-cron-error-email-report
title: Hermes CRON error email report
description: Emails a daily digest of failed CRON runs.
steps:
  - id: email-report
    title: Email report
    description: Collects the last day of failures and sends one email.
    job_id: d4f0c0de0001
    depends_on: []
""",
    "weekly-store-sync.yaml": """schema_version: 1
id: weekly-store-sync
title: Weekly Store Sync
description: Pulls orders and inventory from the shop, then archives last month.
steps:
  - id: pull-orders
    title: Pull orders
    description: Exports the week's orders from the shop API.
    job_id: e5f0c0de0001
    depends_on: []
  - id: pull-inventory
    title: Pull inventory
    description: Reads stock levels from the inventory sheet.
    job_id: e5f0c0de0002
    depends_on: []
  - id: archive
    title: Archive
    description: Moves last month's exports to cold storage.
    job_id: e5f0c0de0003
    depends_on: []
""",
    "broken-example.yaml": """schema_version: 1
id: broken-example
title: Broken example
description: A scaffold file that points at a job that does not exist.
steps:
  - id: only-step
    title: Only step
    job_id: ffffffffffff
    depends_on: []
""",
}


def output_name(when: datetime) -> str:
    return when.strftime("%Y-%m-%d_%H-%M-%S") + ".md"


def output_text(job: dict, when: datetime, kind: str, body: str) -> str:
    head = [f"# Cron Job: {job['name']}", "", f"**Job ID:** {job['id']}",
            f"**Run Time:** {when.strftime('%Y-%m-%d %H:%M:%S')}"]
    if kind != "BLOCKED":
        head.append("**Mode:** no_agent (script)")
    if kind != "ok":
        head.append(f"**Status:** {kind}")
    return "\n".join(head) + "\n\n" + body


def _write(path: Path, text: str, when: datetime = NOW) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    stamp = when.timestamp()
    os.utime(path, (stamp, stamp))


def _job_record(job: dict) -> dict:
    kind, value = job["schedule"]
    schedule = ({"kind": "cron", "expr": value, "display": value} if kind == "cron"
                else {"kind": "interval", "minutes": value, "display": f"every {value}m"})
    paused = job.get("paused_at")
    latest = job["traces"][0] if job["traces"] else None
    return {
        "id": job["id"], "name": job["name"], "script": job["script"], "no_agent": True,
        "schedule": schedule, "schedule_display": schedule["display"],
        "enabled": paused is None, "state": "paused" if paused else "scheduled",
        "paused_at": paused.isoformat() if paused else None,
        "created_at": "2026-05-11T14:25:42",
        "next_run_at": job["next"].isoformat() if job["next"] else None,
        "last_run_at": latest[0].isoformat() if latest else None,
        "last_status": None if latest is None else ("ok" if latest[1] == "ok" else "error"),
        "deliver": "local",
    }


def build(out: Path | str) -> Path:
    out = Path(out).resolve()
    if out.exists():
        if any(out.iterdir()) and not (out / MARKER).is_file():
            raise SystemExit(f"refusing to overwrite {out}: not a fixture folder")
        shutil.rmtree(out)
    profile = out / "profile"
    (profile / "cron" / "output").mkdir(parents=True)

    _write(profile / "cron" / "jobs.json",
           json.dumps({"jobs": [_job_record(job) for job in JOBS], "updated_at": NOW.isoformat()}, indent=2) + "\n")
    _write(profile / "config.yaml", "model:\n  default: example-model\n  provider: example-provider\n")

    db_path = profile / "cron" / "executions.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(EXECUTIONS_SCHEMA)
    counter = 0
    local_sigs: dict[tuple, str] = {}
    for job in JOBS:
        _write(profile / "scripts" / job["script"],
               f"# {job['name']}\n# Placeholder script for the fixture profile.\nprint('ok')\n", job["changed"])
        for when, kind, body, seconds in job["traces"]:
            name = output_name(when)
            text = output_text(job, when, kind, body)
            _write(profile / "cron" / "output" / job["id"] / name, text, when)
            local_sigs[(job["id"], when)] = parse_trace(job["id"], name, text)["local_sig"]
            counter += 1
            started = when - timedelta(seconds=seconds)
            conn.execute(
                "insert into executions (id, job_id, source, process_id, pid, status, claimed_at, started_at, "
                "finished_at, scheduled_instant) values (?, ?, 'builtin', ?, ?, ?, ?, ?, ?, ?)",
                (f"fx{counter:04d}", job["id"], f"fixture-{counter}", 1000 + counter,
                 "completed" if kind == "ok" else "failed", (started - timedelta(seconds=1)).isoformat(),
                 started.isoformat(), when.isoformat(), started.replace(second=0).isoformat()))
            sig = INCIDENTS.get((job["id"], when))
            if sig:
                conn.execute(
                    "insert into cron_incidents (id, job_id, error_sig, state, failure_type, first_seen_at, "
                    "last_seen_at, error, output_file) values (?, ?, ?, 'detected', 'script', ?, ?, ?, ?)",
                    (f"{job['id'][:6]}_{sig}", job["id"], sig, when.isoformat(), when.isoformat(), body[:400],
                     f"/fixture/profile/cron/output/{job['id']}/{name}"))
        (profile / "cron" / "output" / job["id"]).mkdir(parents=True, exist_ok=True)
    conn.commit()
    conn.close()
    os.utime(db_path, (NOW.timestamp(), NOW.timestamp()))

    for name, text in WORKFLOWS.items():
        _write(out / "scaffolds" / name, text)
    (out / "scaffolds-empty").mkdir()

    summary_db = out / "debugger.db"
    conn = sqlite3.connect(str(summary_db))
    conn.execute(SUMMARY_SCHEMA)
    for (job_id, when), (text, written) in SUMMARIES.items():
        sig = INCIDENTS.get((job_id, when)) or local_sigs[(job_id, when)]
        conn.execute(
            "insert into summaries (job_id, error_sig, trace_file, status, summary, session_id, attempts, created_at) "
            "values (?, ?, ?, 'ready', ?, NULL, 1, ?)", (job_id, sig, output_name(when), text, written.isoformat()))
    conn.commit()
    conn.close()
    os.utime(summary_db, (NOW.timestamp(), NOW.timestamp()))

    _write(out / MARKER, json.dumps({"now": NOW.isoformat(), "note": "made-up data, safe to delete"}, indent=2) + "\n")
    return out


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "var" / "fixtures"
    print(build(target))
