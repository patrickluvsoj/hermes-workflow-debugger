"""AC-14: the server has no path that calls a model.

The summarizer lives in wfd/watcher.py. These tests fail if wfd/server.py, or any
wfd module it can reach through imports (including imports inside functions),
imports the watcher or anything that can start a process or open a network client.
"""
import ast
import subprocess
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "wfd"
FORBIDDEN_MODULES = {"subprocess", "socket", "urllib", "urllib.request", "http.client", "requests", "httpx", "asyncio",
                     "multiprocessing", "pty", "wfd.watcher"}
FORBIDDEN_CALLS = {("os", "system"), ("os", "popen"), ("os", "execv"), ("os", "execvp"), ("os", "spawnv"), ("os", "fork")}


def imports_of(path: Path) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = ("wfd" + ("." + node.module if node.module else "")) if node.level else (node.module or "")
            found.add(base)
            found.update(f"{base}.{alias.name}" for alias in node.names)
    return found


def reachable_from_server() -> dict[str, Path]:
    todo, seen = ["wfd.server"], {}
    while todo:
        name = todo.pop()
        path = PKG / (name.split(".", 1)[1] + ".py") if "." in name else None
        if name in seen or path is None or not path.is_file():
            continue
        seen[name] = path
        todo.extend(found for found in imports_of(path) if found.startswith("wfd."))
    return seen


def test_server_reaches_only_read_modules():
    modules = reachable_from_server()
    assert set(modules) == {"wfd.server", "wfd.scaffold", "wfd.hermes_reader", "wfd.state"}, sorted(modules)


def test_no_reachable_module_can_start_a_process_or_call_out():
    for name, path in reachable_from_server().items():
        bad = imports_of(path) & FORBIDDEN_MODULES
        assert not bad, f"{name} imports {sorted(bad)}"
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                assert (node.value.id, node.attr) not in FORBIDDEN_CALLS, f"{name} uses {node.value.id}.{node.attr}"
        text = path.read_text()
        assert "hermes chat" not in text and "summarizer" not in text.lower(), name


def test_importing_and_serving_never_loads_the_watcher():
    code = (
        "import sys, json, threading, urllib.request\n"
        "from wfd.context import build_context\n"
        "from wfd.server import make_server\n"
        "import tempfile\n"
        "c = build_context(fixtures=True, data_dir=tempfile.mkdtemp())\n"
        "s = make_server(c, 0)\n"
        "threading.Thread(target=lambda: s.serve_forever(poll_interval=0.02), daemon=True).start()\n"
        "b = 'http://127.0.0.1:%d' % s.server_address[1]\n"
        "for p in ('/api/state', '/api/steps/a1f0c0de0002/traces', '/api/traces/a1f0c0de0002/2026-11-11_08-01-09.md', '/api/source/a1f0c0de0002', '/'):\n"
        "    urllib.request.urlopen(b + p).read()\n"
        "s.shutdown()\n"
        "print('wfd.watcher' in sys.modules)\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=PKG.parent, timeout=60)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "False"


def test_the_watcher_is_the_only_module_with_a_model_call():
    callers = [p.name for p in PKG.glob("*.py") if "subprocess" in imports_of(p)]
    assert callers == ["watcher.py"]
