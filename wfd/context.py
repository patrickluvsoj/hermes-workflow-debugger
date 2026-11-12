"""Where things are, for one run of the debugger. No path here is tied to one
person's machine: everything comes from arguments or environment variables."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .hermes_reader import HermesReader, default_profile_root
from .summaries import SummaryStore
from .traces import TraceStore

REPO = Path(__file__).resolve().parent.parent
SKILL_SCAFFOLDS = Path("skills") / "devops" / "workflow-scaffolds" / "workflows"


def default_data_dir() -> Path:
    return Path(os.environ.get("WFD_DATA_DIR") or REPO / "var")


@dataclass
class Context:
    mode: str                 # "live" or "fixtures"
    profile_root: Path
    scaffold_dir: Path
    data_dir: Path
    summary_db: Path
    fixed_now: datetime | None = None
    label: str | None = None   # shown in the rail footer; defaults to the profile folder name

    def __post_init__(self):
        self.reader = HermesReader(self.profile_root, self.data_dir)
        self.traces = TraceStore(self.reader, on_unknown=self._unknown_status, on_bad_result=self._bad_result)

    @staticmethod
    def _unknown_status(job_id: str, name: str, status: str) -> None:
        print(f"wfd: unrecognised status line {status!r} in {job_id}/{name}; treated as ok", file=sys.stderr)

    @staticmethod
    def _bad_result(job_id: str, name: str, problem: str) -> None:
        print(f"wfd: {problem} in {job_id}/{name}; ignored", file=sys.stderr)

    def now(self) -> datetime:
        return self.fixed_now or datetime.now()

    def summaries(self, readonly: bool = True) -> SummaryStore:
        return SummaryStore(self.summary_db, readonly=readonly)

    def state(self) -> dict:
        from .state import compute_state

        state = compute_state(self.reader, self.traces, self.scaffold_dir, self.summaries(), self.now(), self.mode)
        if self.label:
            state["profile"] = self.label
        return state


def build_context(profile_root=None, scaffolds=None, fixtures: bool = False, data_dir=None) -> Context:
    data = Path(data_dir).expanduser() if data_dir else default_data_dir()
    if fixtures:
        sys.path.insert(0, str(REPO / "fixtures"))
        import build_fixtures

        root = build_fixtures.build(data / "fixtures")
        return Context(
            mode="fixtures",
            profile_root=root / "profile",
            scaffold_dir=Path(scaffolds).expanduser() if scaffolds else root / "scaffolds",
            data_dir=data / "fixture-data",
            summary_db=root / "debugger.db",
            fixed_now=build_fixtures.NOW,
            label="example",
        )
    profile = Path(profile_root).expanduser() if profile_root else default_profile_root()
    folder = scaffolds or os.environ.get("WFD_SCAFFOLDS")
    return Context(
        mode="live",
        profile_root=profile,
        scaffold_dir=Path(folder).expanduser() if folder else profile / SKILL_SCAFFOLDS,
        data_dir=data,
        summary_db=data / "debugger.db",
    )
