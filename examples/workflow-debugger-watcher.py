#!/usr/bin/env python3
"""Hermes CRON shim for the Workflow Debugger watcher.

Copy this file into your profile's scripts folder and schedule it as a
no-agent job (see the README). It runs one watcher pass and passes the output
through. No output means there was nothing to do, which Hermes treats as a
silent, successful run.

WFD_REPO  folder of the hermes-workflow-debugger checkout
          (default: ~/Code/hermes-workflow-debugger)
"""
import os
import subprocess
import sys
from pathlib import Path

repo = Path(os.environ.get("WFD_REPO") or Path.home() / "Code" / "hermes-workflow-debugger")
python = repo / ".venv" / "bin" / "python"
if not python.is_file():
    print(f"workflow-debugger-watcher: no virtualenv at {python}", file=sys.stderr)
    sys.exit(2)

done = subprocess.run([str(python), "-m", "wfd.cli", "watch", "--once"], cwd=str(repo), timeout=900)
sys.exit(done.returncode)
