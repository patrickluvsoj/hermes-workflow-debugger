---
name: workflow-scaffolds
description: "Use when asked how CRON workflows connect or fail."
---

# Workflow scaffolds

A workflow is a set of Hermes CRON jobs that together do one task. Each job is a step. This skill holds the map: which jobs belong to which workflow, and which step depends on which.

The Workflow Debugger dashboard reads the same files. They are the single source for both.

## Where the files are

One file per workflow in `~/.hermes/profiles/<profile>/skills/devops/workflow-scaffolds/workflows/`.

List the folder, then read the file you need. Do not answer from memory: the files change.

## How to read a file

```yaml
id: weekly-report            # slug, unique across files
title: Weekly Report         # name shown in the dashboard
description: One sentence on what the workflow does.
steps:
  - id: draft                # slug, unique inside the file
    title: Draft
    description: One sentence on what the step does.
    job_id: 0123456789ab     # the CRON job's ID in cron/jobs.json
    depends_on: []           # step ids this step needs to have succeeded
  - id: publish
    title: Publish
    job_id: ba9876543210
    depends_on: [draft]
```

- A step depends only on the steps named in its own `depends_on`. Follow the chain upward for indirect dependencies.
- `job_id` links the step to its CRON job. Job details are in `cron/jobs.json` in the profile. Each run's output is in `cron/output/<job_id>/`.

## What is failing right now

The files say how workflows are built, not how they are doing. For current state, run `wfd status` from the debugger's virtualenv (for example `~/Code/hermes-workflow-debugger/.venv/bin/wfd status`).

It prints every workflow, worst first, with each step's health: `FAILED`, `MISSED`, `blocked`, `ok` or `did not run`. A failed step shows its raw status line and either a short summary or the first error line. `blocked` means an upstream step failed or was missed and this step has not run since. `[paused]` is separate from health. Add `--json` for the same data as JSON.

Report what the command prints. If it cannot run, say so; do not guess the state.

## Editing

- These files are hand-edited. Change one only when the user asks.
- After any edit, run `wfd status`. A file with a problem is listed as `FAILED` with the field at fault.
- Find a job's ID with `hermes cron list`.
- The dashboard is read-only. It never edits these files or any CRON.
