import os
import stat
import sys
from datetime import datetime
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "fixtures"))

import build_fixtures  # noqa: E402

from wfd.hermes_reader import HermesReader  # noqa: E402
from wfd.state import compute_state  # noqa: E402
from wfd.summaries import SummaryStore  # noqa: E402
from wfd.traces import TraceStore  # noqa: E402

NOW = build_fixtures.NOW


def _lock(root: Path, on: bool) -> None:
    for folder, _dirs, names in os.walk(root):
        for name in names:
            os.chmod(Path(folder) / name, 0o444 if on else 0o644)
        os.chmod(folder, 0o555 if on else 0o755)


@pytest.fixture(scope="session", params=["writable", "readonly"])
def fx(request, tmp_path_factory):
    """The fixture folder. Every test that uses it runs twice: once as built,
    once with write permission removed from every file and folder (AC-20)."""
    root = build_fixtures.build(tmp_path_factory.mktemp(request.param) / "fixtures")
    if request.param == "readonly":
        _lock(root, True)
        assert not os.access(root / "profile" / "cron" / "jobs.json", os.W_OK)
        with pytest.raises(PermissionError):
            (root / "profile" / "cron" / "probe").write_text("x")
    yield root
    if request.param == "readonly":
        _lock(root, False)


@pytest.fixture()
def reader(fx, tmp_path):
    return HermesReader(fx / "profile", tmp_path / "data")


@pytest.fixture()
def fixture_state(fx, reader):
    def compute(scaffolds="scaffolds", now: datetime = NOW):
        return compute_state(reader, TraceStore(reader), fx / scaffolds,
                             SummaryStore(fx / "debugger.db", readonly=True), now, mode="fixtures")
    return compute


def pytest_collection_modifyitems(config, items):
    if os.environ.get("WFD_LIVE") == "1":
        return
    skip = pytest.mark.skip(reason="live check: set WFD_LIVE=1")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
