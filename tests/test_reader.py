import sqlite3

import pytest

from wfd.hermes_reader import HermesReader, to_local


def test_reads_jobs(reader):
    jobs = reader.jobs()
    assert len(jobs) == 10
    assert jobs["b2f0c0de0002"]["enabled"] is False


def test_lists_outputs_newest_first(reader):
    names = [name for name, _m, _s in reader.list_outputs("a1f0c0de0002")]
    assert names[0] == "2026-11-11_08-01-09.md"
    assert names == sorted(names, reverse=True)
    assert reader.list_outputs("e5f0c0de0003") == []
    assert reader.list_outputs("nosuchjob") == []


def test_reads_one_output(reader):
    assert "**Status:** script failed" in reader.read_output("a1f0c0de0002", "2026-11-11_08-01-09.md")


@pytest.mark.parametrize("job_id,name", [
    ("a1f0c0de0002", "../../jobs.json"),
    ("a1f0c0de0002", "..%2f..%2fjobs.json"),
    ("a1f0c0de0002", "notes.txt"),
    ("../..", "2026-11-11_08-01-09.md"),
    ("a1f0c0de0002/..", "2026-11-11_08-01-09.md"),
])
def test_refuses_names_outside_the_output_folder(reader, job_id, name):
    with pytest.raises(ValueError):
        reader.read_output(job_id, name)


def test_executions_are_read_from_a_private_copy(reader, fx):
    rows = reader.executions("d4f0c0de0001")
    assert len(rows) == 9
    copy = reader.data_dir / "hermes-copy" / "executions.db"
    assert copy.is_file()
    assert not list((fx / "profile" / "cron").glob("executions.db-*")), "the original must not gain wal/shm files"


def test_copy_follows_wal_and_refreshes_when_the_source_changes(tmp_path):
    profile = tmp_path / "profile"
    (profile / "cron").mkdir(parents=True)
    source = profile / "cron" / "executions.db"
    writer = sqlite3.connect(str(source))
    writer.execute("pragma journal_mode=wal")
    writer.execute("create table executions (job_id, status, claimed_at, started_at, finished_at, scheduled_instant)")
    writer.execute("insert into executions values ('j1','completed','2026-11-12T08:00:00',null,null,null)")
    writer.commit()  # stays in the -wal file while `writer` is open
    assert (profile / "cron" / "executions.db-wal").exists()
    reader = HermesReader(profile, tmp_path / "data")
    assert len(reader.executions("j1")) == 1
    writer.execute("insert into executions values ('j1','failed','2026-11-12T09:00:00',null,null,null)")
    writer.commit()
    assert [row["status"] for row in reader.executions("j1")] == ["failed", "completed"]
    writer.close()


def test_missing_executions_db_gives_no_rows(tmp_path):
    (tmp_path / "p" / "cron").mkdir(parents=True)
    reader = HermesReader(tmp_path / "p", tmp_path / "data")
    assert reader.executions("x") == [] and reader.incidents("x") == []


def test_script_info_and_text(reader):
    job = reader.jobs()["a1f0c0de0002"]
    assert reader.script_info(job) == {"script": "industry-analysis-runner.py", "changed_at": "2026-11-10T17:40:00"}
    assert "Placeholder script" in reader.read_script(job)


def test_script_outside_the_scripts_folder_is_refused(reader):
    assert reader.script_path({"script": "../cron/jobs.json"}) is None
    assert reader.script_path({"script": "/etc/hosts"}) is None
    assert reader.script_path({"script": None}) is None
    assert reader.script_path({"script": "missing.py"}) is None


def test_to_local_handles_offsets_and_naive_values():
    assert to_local("2026-11-12T08:00:00") .isoformat() == "2026-11-12T08:00:00"
    assert to_local("2026-11-12T16:00:00+00:00").tzinfo is None
    assert to_local(None) is None and to_local("nonsense") is None
