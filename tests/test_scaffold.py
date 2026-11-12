import textwrap

import pytest

from wfd.scaffold import load_dir, load_file

JOBS = {"job-a": {}, "job-b": {}, "job-c": {}}
GOOD = """
id: demo
title: Demo
description: Two steps.
steps:
  - id: second
    title: Second
    job_id: job-b
    depends_on: [first]
  - id: first
    title: First
    description: Runs first.
    job_id: job-a
"""


def load(tmp_path, text, jobs=JOBS, name="demo.yaml"):
    path = tmp_path / name
    path.write_text(textwrap.dedent(text))
    return load_file(path, jobs)


def test_valid_file_loads_in_dependency_order(tmp_path):
    wf = load(tmp_path, GOOD)
    assert wf["invalid"] is False and wf["errors"] == []
    assert [s["id"] for s in wf["steps"]] == ["first", "second"]
    assert wf["steps"][0]["depends_on"] == [] and wf["steps"][1]["description"] == ""


@pytest.mark.parametrize("field", ["id", "title", "description", "steps"])
def test_missing_workflow_field_names_the_field(tmp_path, field):
    lines = [l for l in textwrap.dedent(GOOD).splitlines()]
    if field == "steps":
        text = "id: demo\ntitle: Demo\ndescription: x\n"
    else:
        text = "\n".join(l for l in lines if not l.startswith(field + ":"))
    wf = load(tmp_path, text)
    assert wf["invalid"] and wf["errors"][0] == f"{field}: missing required field"


@pytest.mark.parametrize("field", ["id", "title", "job_id"])
def test_missing_step_field_names_the_step_and_field(tmp_path, field):
    step = {"id": "only", "title": "Only", "job_id": "job-a"}
    del step[field]
    body = "\n".join(f"    {k}: {v}" for k, v in step.items())
    wf = load(tmp_path, f"id: demo\ntitle: Demo\ndescription: x\nsteps:\n  -\n{body}\n")
    assert wf["invalid"] and f"steps[0].{field}: missing required field" in wf["errors"]


def test_duplicate_step_id(tmp_path):
    wf = load(tmp_path, GOOD.replace("id: second", "id: first"))
    assert "steps[1].id: duplicate step id 'first' (also steps[0])" in wf["errors"]


def test_duplicate_job_id(tmp_path):
    wf = load(tmp_path, GOOD.replace("job_id: job-b", "job_id: job-a"))
    assert "steps[1].job_id: duplicate job id 'job-a' (also steps[0])" in wf["errors"]


def test_job_id_not_in_jobs_json(tmp_path):
    wf = load(tmp_path, GOOD.replace("job-b", "job-zzz"))
    assert wf["errors"] == ["steps[0].job_id: job 'job-zzz' is not in cron/jobs.json"]


def test_dependency_on_unknown_step(tmp_path):
    wf = load(tmp_path, GOOD.replace("[first]", "[nope]"))
    assert wf["errors"] == ["steps[0].depends_on: unknown step 'nope'"]


def test_dependency_cycle(tmp_path):
    wf = load(tmp_path, GOOD.replace("    job_id: job-a", "    job_id: job-a\n    depends_on: [second]"))
    assert wf["errors"] == ["steps.depends_on: dependency cycle"]


def test_yaml_that_does_not_parse(tmp_path):
    wf = load(tmp_path, "id: demo\ntitle: [unclosed\n")
    assert wf["invalid"] and wf["errors"][0].startswith("yaml: cannot be parsed at line")
    assert wf["id"] == "demo" and wf["title"] == "demo"


def test_not_a_mapping(tmp_path):
    assert load(tmp_path, "- just\n- a list\n")["errors"][0].startswith("file: must be a YAML mapping")


def test_invalid_file_keeps_its_title_for_the_rail(tmp_path):
    wf = load(tmp_path, GOOD.replace("job-b", "job-zzz"))
    assert (wf["id"], wf["title"], wf["file"]) == ("demo", "Demo", "demo.yaml")


def test_folder_loads_every_file_and_flags_duplicate_workflow_ids(tmp_path):
    (tmp_path / "a.yaml").write_text(textwrap.dedent(GOOD))
    (tmp_path / "b.yaml").write_text(textwrap.dedent(GOOD).replace("job-a", "job-c"))
    (tmp_path / "notes.txt").write_text("ignored")
    found = load_dir(tmp_path, JOBS)
    assert [w["file"] for w in found] == ["a.yaml", "b.yaml"]
    assert found[0]["invalid"] is False
    assert found[1]["errors"] == ["id: duplicate workflow id 'demo' (also in a.yaml)"]


def test_missing_folder_is_empty(tmp_path):
    assert load_dir(tmp_path / "nope", JOBS) == []
