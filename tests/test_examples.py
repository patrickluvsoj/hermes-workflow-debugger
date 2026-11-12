from pathlib import Path

from wfd.scaffold import load_dir

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def test_example_workflow_files_load(tmp_path):
    jobs = {"0123456789ab": {}, "ba9876543210": {}, "00aa11bb22cc": {}}
    found = load_dir(EXAMPLES / "workflows", jobs)
    assert [(w["id"], w["errors"], len(w["steps"])) for w in found] == [("nightly-backup", [], 1), ("weekly-report", [], 2)]
    assert found[1]["steps"][1]["depends_on"] == ["draft"]


def test_example_files_report_unknown_jobs_against_another_profile(reader):
    found = load_dir(EXAMPLES / "workflows", reader.jobs())
    assert all(w["invalid"] and "is not in cron/jobs.json" in w["errors"][0] for w in found)


def test_fonts_fall_back_to_the_hermes_install(tmp_path, monkeypatch):
    from wfd.hermes_reader import hermes_font

    (tmp_path / "Collapse-Bold.woff2").write_bytes(b"x")
    monkeypatch.setenv("WFD_HERMES_FONTS", str(tmp_path))
    assert hermes_font("Collapse-Bold.woff2") == tmp_path / "Collapse-Bold.woff2"
    assert hermes_font("Missing.woff2") is None and hermes_font("../x.woff2") is None
