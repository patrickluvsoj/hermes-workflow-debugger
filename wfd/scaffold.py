"""Load and validate workflow scaffold files (one YAML file per workflow)."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

WORKFLOW_REQUIRED = ("id", "title", "description", "steps")
STEP_REQUIRED = ("id", "title", "job_id")

# What a step is expected to produce. Optional; a step without the block gets these.
#   allow_silent  a run that prints nothing is ok for this step
#   noop          "flag": a run that did nothing is shown as no-op. "ok": it counts as ok
#   success_when  regex. Used only when the run has no step_result line. No match means no-op
#   noop_when     regexes. Used only when the run has no step_result line. A match means no-op
DEFAULT_EXPECT = {"allow_silent": False, "noop": "flag", "success_when": None, "noop_when": []}
NOOP_SETTINGS = ("flag", "ok")


def _regex_ok(value: str) -> bool:
    try:
        re.compile(value)
    except re.error:
        return False
    return True


def validate_expect(expect, where: str) -> list[str]:
    if expect is None:
        return []
    if not isinstance(expect, dict):
        return [f"{where}: must be a mapping with allow_silent, noop, success_when or noop_when"]
    errors = []
    for key in expect:
        if key not in DEFAULT_EXPECT:
            errors.append(f"{where}.{key}: unknown key (use allow_silent, noop, success_when, noop_when)")
    if "allow_silent" in expect and not isinstance(expect["allow_silent"], bool):
        errors.append(f"{where}.allow_silent: must be true or false")
    if "noop" in expect and (not isinstance(expect["noop"], str) or expect["noop"] not in NOOP_SETTINGS):
        errors.append(f"{where}.noop: must be 'flag' or 'ok'")
    success = expect.get("success_when")
    if success is not None:
        if not isinstance(success, str):
            errors.append(f"{where}.success_when: must be one regular expression (text)")
        elif not _regex_ok(success):
            errors.append(f"{where}.success_when: not a valid regular expression")
    patterns = expect.get("noop_when")
    if patterns is not None:
        if not isinstance(patterns, list):
            errors.append(f"{where}.noop_when: must be a list of regular expressions")
        else:
            for index, pattern in enumerate(patterns):
                if not isinstance(pattern, str):
                    errors.append(f"{where}.noop_when[{index}]: must be a regular expression (text)")
                elif not _regex_ok(pattern):
                    errors.append(f"{where}.noop_when[{index}]: not a valid regular expression")
    return errors


def normal_expect(expect) -> dict:
    expect = expect or {}
    return {
        "allow_silent": bool(expect.get("allow_silent", False)),
        "noop": expect.get("noop") or "flag",
        "success_when": expect.get("success_when") or None,
        "noop_when": list(expect.get("noop_when") or []),
    }


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def validate(data, jobs: dict | None) -> list[str]:
    """Return a list of problems. Each one starts with the field it is about."""
    if not isinstance(data, dict):
        return ["file: must be a YAML mapping with id, title, description and steps"]
    errors = []
    for field in WORKFLOW_REQUIRED:
        if field not in data or data[field] in (None, "", []):
            errors.append(f"{field}: missing required field")
    steps = data.get("steps")
    if steps in (None, "", []):
        return errors
    if not isinstance(steps, list):
        return errors + ["steps: must be a list"]
    seen: dict[str, int] = {}
    seen_jobs: dict[str, int] = {}
    for index, step in enumerate(steps):
        where = f"steps[{index}]"
        if not isinstance(step, dict):
            errors.append(f"{where}: must be a mapping")
            continue
        for field in STEP_REQUIRED:
            if step.get(field) in (None, ""):
                errors.append(f"{where}.{field}: missing required field")
        step_id = str(step.get("id") or "")
        if step_id:
            if step_id in seen:
                errors.append(f"{where}.id: duplicate step id '{step_id}' (also steps[{seen[step_id]}])")
            seen.setdefault(step_id, index)
        job_id = str(step.get("job_id") or "")
        if job_id:
            if job_id in seen_jobs:
                errors.append(f"{where}.job_id: duplicate job id '{job_id}' (also steps[{seen_jobs[job_id]}])")
            seen_jobs.setdefault(job_id, index)
            if jobs is not None and job_id not in jobs:
                errors.append(f"{where}.job_id: job '{job_id}' is not in cron/jobs.json")
        depends = step.get("depends_on", [])
        if depends is None:
            depends = []
        if not isinstance(depends, list):
            errors.append(f"{where}.depends_on: must be a list of step ids")
        errors.extend(validate_expect(step.get("expect"), f"{where}.expect"))
    ids = {str(s.get("id")) for s in steps if isinstance(s, dict) and s.get("id")}
    graph: dict[str, list[str]] = {}
    for index, step in enumerate(steps):
        if not isinstance(step, dict) or not isinstance(step.get("depends_on") or [], list):
            continue
        deps = [str(d) for d in (step.get("depends_on") or [])]
        for dep in deps:
            if dep not in ids:
                errors.append(f"steps[{index}].depends_on: unknown step '{dep}'")
        if step.get("id"):
            graph[str(step["id"])] = [d for d in deps if d in ids]
    if _has_cycle(graph):
        errors.append("steps.depends_on: dependency cycle")
    return errors


def _has_cycle(graph: dict[str, list[str]]) -> bool:
    state: dict[str, int] = {}

    def visit(node: str) -> bool:
        if state.get(node) == 1:
            return True
        if state.get(node) == 2:
            return False
        state[node] = 1
        for dep in graph.get(node, []):
            if visit(dep):
                return True
        state[node] = 2
        return False

    return any(visit(node) for node in list(graph))


def _ordered(steps: list[dict]) -> list[dict]:
    """Dependency order, keeping file order where there is a choice."""
    done: list[dict] = []
    placed: set[str] = set()
    remaining = list(steps)
    while remaining:
        for step in remaining:
            if all(dep in placed for dep in step["depends_on"]):
                done.append(step)
                placed.add(step["id"])
                remaining.remove(step)
                break
        else:  # unreachable after validation
            done.extend(remaining)
            break
    return done


def load_file(path: Path, jobs: dict | None) -> dict:
    """A workflow dict. On any problem: {'invalid': True, 'error': '<field>: ...'}."""
    base = {"file": path.name, "id": path.stem, "title": path.stem, "description": ""}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" at line {mark.line + 1}" if mark else ""
        return {**base, "invalid": True, "errors": [f"yaml: cannot be parsed{where}"]}
    except OSError as exc:
        return {**base, "invalid": True, "errors": [f"file: cannot be read ({type(exc).__name__})"]}
    if isinstance(data, dict):
        base["id"] = _text(data.get("id")) or base["id"]
        base["title"] = _text(data.get("title")) or base["title"]
        base["description"] = _text(data.get("description"))
    errors = validate(data, jobs)
    if errors:
        return {**base, "invalid": True, "errors": errors}
    steps = [
        {
            "id": str(step["id"]),
            "title": _text(step["title"]),
            "description": _text(step.get("description")),
            "job_id": str(step["job_id"]),
            "depends_on": [str(d) for d in (step.get("depends_on") or [])],
            "expect": normal_expect(step.get("expect")),
        }
        for step in data["steps"]
    ]
    return {**base, "invalid": False, "errors": [], "steps": _ordered(steps)}


def load_dir(folder: Path | str, jobs: dict | None) -> list[dict]:
    folder = Path(folder).expanduser()
    if not folder.is_dir():
        return []
    workflows = [load_file(path, jobs) for path in sorted(folder.glob("*.yaml")) + sorted(folder.glob("*.yml"))]
    first: dict[str, str] = {}
    for workflow in workflows:
        if workflow["id"] in first and not workflow["invalid"]:
            workflow["invalid"] = True
            workflow["errors"] = [f"id: duplicate workflow id '{workflow['id']}' (also in {first[workflow['id']]})"]
            workflow.pop("steps", None)
        first.setdefault(workflow["id"], workflow["file"])
    return workflows
