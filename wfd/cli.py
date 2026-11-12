"""wfd serve | status | watch"""
from __future__ import annotations

import argparse
import json
import sys

from .context import build_context

LABEL = {"failed": "FAILED", "missed": "MISSED", "blocked": "blocked", "noop": "no-op", "ok": "ok", "none": "did not run"}


def _why(trace) -> str:
    """The script's own reason for a no-op, as written."""
    return ": ".join(part for part in (trace.get("reason"), trace.get("detail")) if part)


def _when(value) -> str:
    return value.replace("T", " ")[:16] if value else "never"


def render_status(state: dict) -> str:
    lines = [f"{state['mode']} · read {_when(state['read_at'])} · scaffolds: {state['scaffold_dir']}"]
    if not state["workflows"]:
        lines.append("No workflow files found. Add one YAML file per workflow to the scaffold folder.")
    for workflow in state["workflows"]:
        paused = f"  [{workflow['paused_count']} paused]" if workflow["paused_count"] else ""
        lines.append("")
        lines.append(f"{LABEL[workflow['health']]:<8} {workflow['title']}  ({workflow['id']}){paused}")
        if workflow["invalid"]:
            for error in workflow["errors"]:
                lines.append(f"         {workflow['file']}: {error}")
            continue
        for step in workflow["steps"]:
            latest = step["latest"]
            note = f"last run {_when(step['last_run'])}"
            if step["health"] == "failed":
                note = f"{_when(latest['time'])}  {latest['status_line']}"
            elif step["health"] == "missed":
                note = f"scheduled {_when(latest['time'])}, never ran"
            elif step["health"] == "blocked":
                note = "waiting on " + ", ".join(step["blocked_by"])
            elif step["health"] == "noop":
                note = f"{_when(latest['time'])}  ran and did nothing" + (f"  ({_why(latest)})" if _why(latest) else "")
            elif step["health"] == "none":
                note = "no runs on file"
            elif step["health"] == "ok" and latest and latest.get("state") == "ok" and _why(latest):
                note += f"  (did nothing, accepted: {_why(latest)})"
            deps = f"  <- {', '.join(step['depends_on'])}" if step["depends_on"] else ""
            flag = "  [paused]" if step["paused"] else ""
            lines.append(f"  {LABEL[step['health']]:<12} {step['title']} ({step['id']}, job {step['job_id']}){flag}{deps}")
            lines.append(f"               {note}")
            summary = step.get("summary")
            if summary:
                lines.append(f"               {summary['text'] or summary['first_error_line']}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="wfd", description="Local, read-only workflow debugger for Hermes CRON automations.")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--profile-root", help="Hermes profile folder (default: $WFD_PROFILE_ROOT or ~/.hermes/profiles/daily)")
        p.add_argument("--scaffolds", help="folder of workflow YAML files (default: the workflow-scaffolds skill in the profile)")
        p.add_argument("--fixtures", action="store_true", help="use the built-in made-up data instead of a real profile")
        p.add_argument("--data-dir", help="where the debugger keeps its own files (default: $WFD_DATA_DIR or ./var)")

    serve = sub.add_parser("serve", help="start the dashboard on 127.0.0.1")
    common(serve)
    serve.add_argument("--port", type=int, default=8787)

    status = sub.add_parser("status", help="print the state of every workflow")
    common(status)
    status.add_argument("--json", action="store_true")

    watch = sub.add_parser("watch", help="write a summary for each new failure")
    common(watch)
    watch.add_argument("--once", action="store_true", help="run one pass and exit (the only mode)")

    args = parser.parse_args(argv)
    context = build_context(args.profile_root, args.scaffolds, args.fixtures, args.data_dir)

    if args.command == "status":
        try:
            state = context.state()
        except (OSError, ValueError) as exc:
            print(f"wfd: cannot read the profile at {context.profile_root}: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(state, indent=2) if args.json else render_status(state))
        return 1 if any(w["invalid"] for w in state["workflows"]) else 0

    if args.command == "serve":
        from .server import serve as run_server

        return run_server(context, args.port)

    if args.command == "watch":
        if context.mode != "live":
            print("wfd: the watcher only runs against a real profile", file=sys.stderr)
            return 2
        from .watcher import run_once

        return run_once(context)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
