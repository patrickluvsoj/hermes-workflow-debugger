"""AC-32: the README has the required parts and stays inside the limits in plan section 5.7.

    python tools/check_readme.py [README.md]
"""
import re
import sys
from pathlib import Path

path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "README.md"
text = path.read_text()
ok = True


def check(name, passed, detail=""):
    global ok
    ok = ok and bool(passed)
    print(f"{'PASS' if passed else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))


def section(title):
    match = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return match.group(1).strip() if match else None


def sentences(block):
    plain = re.sub(r"`[^`]*`", "x", block)
    return len([s for s in re.split(r"(?<=[.!?])[\"\')]*\s+", plain.strip()) if s])


lines = text.splitlines()
check("title on line 1", lines[0].startswith("# "), lines[0])
one_liner = lines[2]
check("one sentence under the title", sentences(one_liner) == 1, f"{sentences(one_liner)} sentence")
image = re.search(r"^!\[[^\]]+\]\(([^)]+)\)", text, re.M)
before_image = text[: image.start()].count("\n") if image else 99
check("one screenshot directly under the title and one line", image is not None and before_image <= 4 and len(re.findall(r"^!\[", text, re.M)) == 1,
      f"line {before_image + 1}")
check("screenshot path resolves", image is not None and (path.parent / image.group(1)).is_file(), image.group(1) if image else "")
for title, low, high in (("Why", 3, 4), ("What", 2, 3)):
    body = section(title)
    count = sentences(body) if body else 0
    check(f"{title}: {low}-{high} sentences", body is not None and low <= count <= high, f"{count} sentences")
caps = section("Capabilities")
bullets = [l for l in (caps or "").splitlines() if l.startswith("- ")]
check("Capabilities: 8 bullets at most, one line each", caps is not None and 1 <= len(bullets) <= 8 and len(bullets) == len([l for l in caps.splitlines() if l.strip()]), f"{len(bullets)} bullets")
check("Capabilities name what it does not do", any("does not do" in b for b in bullets))
start = section("Getting started")
blocks = re.findall(r"```bash\n(.*?)```", start or "", re.S)
first = [l for l in (blocks[0] if blocks else "").splitlines() if l.strip()]
check("Getting started: fixture mode runs in 5 commands or fewer", start is not None and 1 <= len(first) <= 5 and "--fixtures" in first[-1], f"{len(first)} commands")
check("Getting started: command for a real profile", len(blocks) >= 2 and "wfd serve" in blocks[1] and "--fixtures" not in blocks[1])
scaffold = section("Set up a scaffold file")
check("Scaffold: 1 example and 1 table", scaffold is not None and scaffold.count("```yaml") == 1 and len(re.findall(r"^\|---", scaffold, re.M)) == 1)
for field in ("id", "title", "description", "steps", "steps[].id", "steps[].title", "steps[].description", "steps[].job_id", "steps[].depends_on"):
    check(f"Scaffold table has `{field}`", scaffold is not None and f"| `{field}` |" in scaffold)
for needle, name in (("workflow-scaffolds/workflows/", "where the file goes"), ("hermes cron list", "how to find a job ID"),
                     ("depends_on", "how dependencies are written"), ("wfd status", "how to check the file"),
                     ("appears in the dashboard as failed", "what an invalid file looks like")):
    check(f"Scaffold: {name}", scaffold is not None and needle in scaffold)
summaries = section("Error summaries (optional)")
check("Error summaries: add and remove the watcher", summaries is not None and "hermes cron create" in summaries and "hermes cron remove" in summaries and "hermes cron pause" in summaries)
status = section("Status")
check("Status says prototype, 1-2 sentences plus licence line", status is not None and "prototype" in status.lower() and sentences(status.split("\n\n")[0]) <= 2)
check("no em dashes or marketing words", not re.search(r"\u2014|seamless|powerful|effortless|robust|blazing|revolution", text, re.I))
print("README OK" if ok else "README FAILED")
raise SystemExit(0 if ok else 1)
