"""A small, automation-owned headless browser for the browser checks.

It starts its own Chromium with a temporary profile on a random debugging port,
creates one page, and only ever talks to that page. On exit it stops the exact
process it started and deletes its temporary profile. It never attaches to an
existing browser.

Needs: `pip install websockets` and a Chromium binary (WFD_CHROMIUM, or the
newest one in the Playwright cache).
"""
from __future__ import annotations

import base64
import glob
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from websockets.sync.client import connect


def find_chromium() -> str:
    if os.environ.get("WFD_CHROMIUM"):
        return os.environ["WFD_CHROMIUM"]
    cache = Path.home() / "Library" / "Caches" / "ms-playwright"
    if not cache.is_dir():
        cache = Path.home() / ".cache" / "ms-playwright"
    found = sorted(glob.glob(str(cache / "chromium_headless_shell-*" / "*" / "chrome-headless-shell")))
    if not found:
        raise SystemExit("No Chromium found. Set WFD_CHROMIUM to a Chromium or Chrome for Testing binary.")
    return found[-1]


class Browser:
    def __init__(self, width: int = 1440, height: int = 900):
        self.width, self.height = width, height
        self._id = 0

    def __enter__(self) -> "Browser":
        self.profile = tempfile.mkdtemp(prefix="wfd-browser-")
        self.proc = subprocess.Popen(
            [find_chromium(), "--headless", "--remote-debugging-port=0", "--remote-allow-origins=*",
             f"--user-data-dir={self.profile}", "--no-first-run", "--no-default-browser-check",
             "--use-mock-keychain", "--disable-background-networking", "--hide-scrollbars", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            port_file = Path(self.profile) / "DevToolsActivePort"
            deadline = time.time() + 20
            while not port_file.exists() or not port_file.read_text().strip():
                if time.time() > deadline or self.proc.poll() is not None:
                    raise RuntimeError("Chromium did not start")
                time.sleep(0.05)
            port = int(port_file.read_text().split()[0])
            version = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version"))
            self.ws = connect(version["webSocketDebuggerUrl"], origin="http://localhost", max_size=None)
            self.target = self._send("Target.createTarget", {"url": "about:blank"})["targetId"]
            self.session = self._send("Target.attachToTarget", {"targetId": self.target, "flatten": True})["sessionId"]
            self.cdp("Page.enable")
            self.cdp("Runtime.enable")
            self.resize(self.width, self.height)
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *exc) -> None:
        try:
            if getattr(self, "ws", None):
                self.ws.close()
        finally:
            try:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(timeout=5)
            finally:
                shutil.rmtree(self.profile, ignore_errors=True)

    def _send(self, method: str, params: dict | None = None, session: str | None = None) -> dict:
        self._id += 1
        message = {"id": self._id, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        self.ws.send(json.dumps(message))
        while True:
            reply = json.loads(self.ws.recv(timeout=30))
            if reply.get("id") == self._id:
                if "error" in reply:
                    raise RuntimeError(f"{method}: {reply['error']}")
                return reply.get("result", {})

    def cdp(self, method: str, params: dict | None = None) -> dict:
        return self._send(method, params, self.session)

    # ---- page helpers ---------------------------------------------------
    def resize(self, width: int, height: int) -> None:
        self.width, self.height = width, height
        self.cdp("Emulation.setDeviceMetricsOverride",
                 {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False})

    def js(self, expression: str):
        result = self.cdp("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
        if "exceptionDetails" in result:
            raise RuntimeError(result["exceptionDetails"].get("exception", {}).get("description") or str(result["exceptionDetails"]))
        return result["result"].get("value")

    def wait(self, expression: str, timeout: float = 10) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.js(f"!!({expression})"):
                return
            time.sleep(0.05)
        raise TimeoutError(f"timed out waiting for: {expression}")

    def goto(self, url: str, ready: str = "document.querySelector('#foot-text')") -> None:
        self.cdp("Page.navigate", {"url": url})
        self.wait("document.readyState === 'complete'")
        if ready:
            self.wait(ready)
        self.wait("document.fonts.status === 'loaded'")
        time.sleep(0.15)

    def settle(self, seconds: float = 0.25) -> None:
        time.sleep(seconds)

    def center(self, selector: str) -> tuple[float, float]:
        box = self.js(f"""(() => {{ const e = document.querySelector({json.dumps(selector)}); if (!e) return null;
            e.scrollIntoView({{block: 'nearest'}}); const r = e.getBoundingClientRect();
            return [r.left + r.width / 2, r.top + r.height / 2]; }})()""")
        if not box:
            raise RuntimeError(f"no element for {selector}")
        return box[0], box[1]

    def click(self, selector: str) -> None:
        """A real mouse click at the element's centre."""
        x, y = self.center(selector)
        self.cdp("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        for kind in ("mousePressed", "mouseReleased"):
            self.cdp("Input.dispatchMouseEvent", {"type": kind, "x": x, "y": y, "button": "left", "clickCount": 1})
        self.settle()

    def hover(self, selector: str) -> None:
        self.center(selector)          # may scroll; a scroll event hides tooltips
        time.sleep(0.06)
        x, y = self.center(selector)
        self.cdp("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        self.settle(0.1)

    def key(self, key: str, shift: bool = False) -> None:
        codes = {"Tab": 9, "Enter": 13, "Escape": 27, " ": 32}
        base = {"key": key, "code": "Space" if key == " " else key, "windowsVirtualKeyCode": codes[key],
                "modifiers": 8 if shift else 0}
        text = {"Enter": "\r", " ": " "}.get(key)
        self.cdp("Input.dispatchKeyEvent", {**base, "type": "keyDown", **({"text": text} if text else {})})
        self.cdp("Input.dispatchKeyEvent", {**base, "type": "keyUp"})
        self.settle(0.1)

    def shot(self, path: str | Path, full: bool = True) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        params = {"format": "png"}
        if full:
            size = self.js("[document.documentElement.scrollWidth, document.documentElement.scrollHeight]")
            params.update({"captureBeyondViewport": True,
                           "clip": {"x": 0, "y": 0, "width": max(size[0], self.width), "height": max(size[1], self.height), "scale": 1}})
        path.write_bytes(base64.b64decode(self.cdp("Page.captureScreenshot", params)["data"]))
        return path
