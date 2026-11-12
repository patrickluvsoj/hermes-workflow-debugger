"""Browser checks for the acceptance criteria marked B in the plan.

Runs the real server in fixture mode on a free port, drives an automation-owned
headless Chromium (tools/browser.py) with real mouse and keyboard events, and
prints one PASS or FAIL line per check. Screenshots go to the output folder.

    python tools/browser_checks.py [output folder]     (default: evidence/browser)

Exit code 0 only when every check passes.
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO), str(REPO / "fixtures"), str(REPO / "tools")]

import build_fixtures  # noqa: E402
from browser import Browser  # noqa: E402

from wfd.context import Context  # noqa: E402
from wfd.server import make_server  # noqa: E402
from wfd.summaries import SummaryStore  # noqa: E402

RESULTS: list[tuple[bool, str, str]] = []


def check(criterion: str, name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((bool(ok), criterion, name))
    print(f"{'PASS' if ok else 'FAIL'}  {criterion:<6} {name}" + (f"  [{detail}]" if detail else ""), flush=True)
    return bool(ok)


def serve(root: Path, data: Path, scaffolds: str = "scaffolds"):
    context = Context(mode="fixtures", profile_root=root / "profile", scaffold_dir=root / scaffolds,
                      data_dir=data, summary_db=root / "debugger.db", fixed_now=build_fixtures.NOW, label="example")
    server = make_server(context, 0)
    threading.Thread(target=lambda: server.serve_forever(poll_interval=0.02), daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


# JavaScript helpers evaluated in the page -----------------------------------
JS_LIB = r"""
window.T = {
  txt: s => { const e = document.querySelector(s); return e ? e.textContent.replace(/\s+/g, ' ').trim() : null; },
  all: s => [...document.querySelectorAll(s)].map(e => e.textContent.replace(/\s+/g, ' ').trim()),
  n: s => document.querySelectorAll(s).length,
  css: (s, p) => { const e = document.querySelector(s); return e ? getComputedStyle(e)[p] : null; },
  rgb: c => { // "rgb(a) / color(srgb ...)" -> [r,g,b,a] 0..255
    let m = /^rgba?\(([^)]+)\)/.exec(c);
    if (m) { const v = m[1].split(/[ ,\/]+/).map(Number); return [v[0], v[1], v[2], v.length > 3 ? v[3] : 1]; }
    m = /^color\(srgb ([^)]+)\)/.exec(c);
    if (m) { const v = m[1].split(/[ \/]+/).map(Number); return [v[0] * 255, v[1] * 255, v[2] * 255, v.length > 3 ? v[3] : 1]; }
    return null; },
  bg: el => { // effective background: composite every ancestor's background colour
    const stack = []; for (let e = el; e; e = e.parentElement) stack.push(T.rgb(getComputedStyle(e).backgroundColor));
    let out = T.rgb(getComputedStyle(document.documentElement).getPropertyValue('--bg').trim().replace(/^#(..)(..)(..)$/, (_, r, g, b) => `rgb(${parseInt(r,16)}, ${parseInt(g,16)}, ${parseInt(b,16)})`));
    for (const c of stack.reverse()) { if (!c || c[3] === 0) continue; out = [0, 1, 2].map(i => c[i] * c[3] + out[i] * (1 - c[3])).concat(1); }
    return out; },
  lum: c => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); },
  ratio: (a, b) => { const x = T.lum(a), y = T.lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); },
  contrast: () => { // every element that directly holds visible text
    const seen = {};
    for (const el of document.querySelectorAll('body *')) {
      if (el.closest('svg') || el.id === 'tip' || el.id === 'toast') continue;
      const own = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
      if (!own) continue;
      const r = el.getBoundingClientRect(); if (!r.width || !r.height) continue;
      const cs = getComputedStyle(el); const fg0 = T.rgb(cs.color); const bg = T.bg(el);
      const fg = [0, 1, 2].map(i => fg0[i] * fg0[3] + bg[i] * (1 - fg0[3]));
      const hex = c => '#' + c.slice(0, 3).map(v => Math.round(v).toString(16).padStart(2, '0')).join('');
      const key = hex(fg) + ' on ' + hex(bg);
      const ratio = T.ratio(fg, bg);
      if (!seen[key]) seen[key] = { ratio: Math.round(ratio * 100) / 100, n: 0, eg: (el.className || el.tagName).toString().slice(0, 24) + ': ' + el.textContent.trim().slice(0, 28) };
      seen[key].n++;
    }
    return seen; },
  inView: s => { const e = document.querySelector(s); if (!e) return false; const r = e.getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight && r.width > 0; },
  focusables: () => [...document.querySelectorAll('button, a[href], [tabindex="0"]')].filter(e => e.getBoundingClientRect().width > 0),
  describe: e => e ? (e.getAttribute('aria-label') || e.textContent.replace(/\s+/g, ' ').trim()).slice(0, 60) : null,
};
"""


def lib(b: Browser) -> None:
    b.js(JS_LIB)


def goto(b: Browser, url: str, wait_main: bool = True) -> None:
    if "poll=" not in url:  # keep the 30-second poll from re-rendering the page in the middle of a check
        url += ("&" if "?" in url else "?") + "poll=600000"
    b.goto(url)
    if wait_main:
        b.wait("document.querySelector('#main').children.length > 0")
    b.settle(0.3)
    lib(b)


def sentences(text: str) -> int:
    import re

    return len([s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s])


def main(out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="wfd-checks-"))
    root = build_fixtures.build(work / "fixtures")
    server, base = serve(root, work / "data")
    empty_server, empty_base = serve(root, work / "data-empty", "scaffolds-empty")
    contrast: dict[str, dict] = {}

    def collect_contrast(b: Browser, theme: str, where: str) -> None:
        for key, value in b.js("T.contrast()").items():
            contrast.setdefault(theme, {}).setdefault(key, {**value, "where": where})

    with Browser(1440, 900) as b:
        # ---------------- first screen, zero clicks (O1, AC-8) ----------------
        goto(b, base + "/")
        b.wait("document.querySelector('pre.out')")
        lib(b)
        b.shot(out / "01-first-screen-dark.png", full=False)
        wf, step = b.js("T.txt('.wf[aria-current=true] .name')"), b.js("T.txt('.step[aria-pressed=true] .name')")
        headline, body = b.js("T.txt('.why h2')"), b.js("T.txt('.why p')")
        check("O1", "first screen names the failing workflow", wf == "Industry Analysis Blog Post" and b.js("T.txt('h1')") == wf, wf)
        check("O1", "first screen names the failing step", headline.startswith("Industry analysis failed"), headline)
        check("O1", "reason is 2-3 sentences", 2 <= sentences(body) <= 3, f"{sentences(body)} sentences")
        check("O1", "workflow, step and reason are inside the first viewport, zero clicks",
              b.js("T.inView('h1') && T.inView('.why') && T.inView('.step[aria-pressed=true]')"))
        check("AC-8", "first failing workflow is selected", b.js("document.querySelector('.wf').getAttribute('aria-current')") == "true")
        check("AC-8", "its failing step is selected", step == "Industry analysis", step)
        check("AC-8", "its latest trace is expanded",
              b.js("document.querySelector('.t .tog').getAttribute('aria-expanded') === 'true' && T.n('pre.out') === 1 && "
                   "document.querySelector('.traces').children[1].classList.contains('x')"))
        check("O3", "raw error output visible with zero clicks on open",
              b.js("T.inView('pre.out .hit') || document.querySelector('pre.out').getBoundingClientRect().top < innerHeight")
              and "403 Forbidden" in b.js("T.txt('pre.out')"))

        check("AC-12", "a three-step chain is drawn with two arrows in dependency order",
              b.js("T.n('.arrow')") == 2 and b.js("T.all('.step .name')") == ["Watch list scout", "Industry analysis", "Publish gate"])
        # ---------------- rail (AC-3, AC-7, AC-25) ----------------
        names = b.js("T.all('.wf .name')")
        check("AC-7", "rail lists problems first", names == [
            "Industry Analysis Blog Post", "Weekly Store Sync", "Weekly AI Analysis Publication", "Broken example",
            "Google OAuth token refresh", "Hermes CRON error email report"], " > ".join(n.split()[0] for n in names))
        glyphs = b.js("[...document.querySelectorAll('.wf')].map(w => w.querySelector('.glyph').className.replace('glyph g-', ''))")
        check("AC-7", "rail glyphs are failed, failed, failed, failed, missed, ok", glyphs == ["failed"] * 4 + ["missed", "ok"], ",".join(glyphs))
        broken = b.js("T.txt('.wf[data-wf=broken-example] .meta')")
        check("AC-3", "invalid file is in the rail as failed and names the field",
              glyphs[3] == "failed" and "steps[0].job_id" in broken, broken)
        foot = b.js("T.txt('#foot-text')")
        check("AC-25", "rail footer reads fixtures", foot.endswith("· fixtures") and "read 10:42" in foot, foot)

        # ---------------- trace rows and output (AC-10, AC-11) ----------------
        row = b.js("[...document.querySelector('.t .tog').children].map(c => c.textContent.trim() || c.className)")
        check("AC-10", "trace row shows time, state and the raw status line",
              row[1] == "Wed Nov 11 · 08:01" and row[2] == "script failed" and "g-failed" in row[0], " | ".join(row))
        red = b.js("getComputedStyle(document.documentElement).getPropertyValue('--destructive').trim()")
        hits = b.js("[...document.querySelectorAll('pre.out .hit')].map(h => [h.textContent.slice(0, 26), getComputedStyle(h).color])")
        check("AC-10", "failure lines in the output are red", len(hits) == 3 and all(c == "rgb(248, 81, 73)" for _t, c in hits) and red == "#f85149",
              "; ".join(t for t, _c in hits))
        check("AC-10", "output is monospace, wrapped, scrolls inside a fixed height",
              b.js("(() => { const c = getComputedStyle(document.querySelector('pre.out')); return c.fontFamily.includes('JetBrains Mono') && c.whiteSpace === 'pre-wrap' && c.maxHeight === '300px' && c.overflowY === 'auto'; })()"))
        b.click(".t .tog")
        check("AC-10", "row collapses on click", b.js("T.n('pre.out')") == 0)
        b.click(".traces .t:nth-of-type(1) .tog")
        b.wait("document.querySelector('pre.out')")
        check("AC-10", "row expands in place, directly under its own row",
              b.js("document.querySelector('.traces').children[1].querySelector('pre.out') !== null"))
        acts = b.js("[...document.querySelector('.t .act').children].map(e => e.dataset.tip)")
        check("AC-11", "no session link when the output has no session ID", acts == ["Output", "Copy run ID", "CRON source industry-analysis-runner.py"], " | ".join(acts))
        src = b.js("T.txt('.step[aria-pressed=true] .script')")
        check("AC-11", "selected step shows its script and when it last changed", src == "industry-analysis-runner.py · changed Nov 10 17:40", src)
        check("AC-11", "selected step box expands to hold its description, in primary text at 14px; other boxes stay short",
              b.js("T.txt('.step[aria-pressed=true] .what')") == "Picks a company from the watch list and writes the analysis note."
              and b.js("T.css('.step[aria-pressed=true] .what', 'color')") == "rgb(230, 237, 243)" and b.js("T.css('.step[aria-pressed=true] .what', 'fontSize')") == "14px"
              and b.js("T.n('.step .what')") == 1
              and b.js("document.querySelector('.step[aria-pressed=true]').offsetHeight > document.querySelector('.step[aria-pressed=false]').offsetHeight + 40"),
              b.js("[...document.querySelectorAll('.step')].map(e => e.offsetWidth + 'x' + e.offsetHeight).join(' ')"))
        href = b.js("document.querySelector('.t .act a').getAttribute('href')")
        check("AC-11", "step links to its CRON source", href == "/api/source/a1f0c0de0002", href)
        collect_contrast(b, "dark", "F1")

        # ---------------- blocked step ----------------
        b.click(".step[data-step=publish-gate]")
        lib(b)
        check("AC-7", "blocked step shows the blocked glyph and what blocks it",
              b.js("document.querySelector('.step[data-step=publish-gate] .glyph').classList.contains('g-blocked')")
              and "blocked by Industry analysis" in b.js("T.txt('.step[data-step=publish-gate] .cap')"))
        b.shot(out / "02-blocked-step-dark.png", full=False)
        collect_contrast(b, "dark", "F1 blocked step")

        # ---------------- F5: several failures, session link (O3, AC-11, AC-19) ----------------
        b.click(".wf[data-wf=weekly-store-sync]")
        b.wait("document.querySelector('pre.out')")
        lib(b)
        heads = b.js("T.all('.why h2')")
        check("AC-19", "several failed steps: one block per failed step, in step order",
              len(heads) == 2 and heads[0].startswith("Pull orders failed") and heads[1].startswith("Pull inventory failed"), " / ".join(heads))
        acts = b.js("[...document.querySelector('.t .act').children].map(e => e.dataset.tip)")
        link = b.js("document.querySelector('.t .act a[href^=\"hermes://\"]').getAttribute('href')")
        check("AC-11", "session link appears when the output has a session ID",
              link == "hermes://open/sessions/20261110_020011_ab12cd" and len(acts) == 4, link)
        b.resize(700 + 304, 900)
        b.settle(0.3)
        check("AC-12", "in a narrow window the step strip scrolls sideways instead of wrapping; the page itself does not",
              b.js("(() => { const p = document.querySelector('.pipe'); const tops = [...p.querySelectorAll('.step')].map(e => e.getBoundingClientRect().top); "
                   "return getComputedStyle(p).flexWrap === 'nowrap' && new Set(tops).size === 1 && p.scrollWidth > p.clientWidth && document.documentElement.scrollWidth <= innerWidth; })()"),
              b.js("document.querySelector('.pipe').scrollWidth + ' wide in ' + document.querySelector('.pipe').clientWidth"))
        b.shot(out / "03b-step-strip-overflows-dark.png", full=False)
        b.resize(1440, 900)
        b.settle(0.3)
        check("AC-12", "step with no traces on file says so in the step box",
              "no runs on file" in b.js("T.txt('.step[data-step=archive] .cap')")
              and b.js("document.querySelector('.step[data-step=archive] .glyph').classList.contains('g-none')"))
        check("AC-12", "arrows only join a step to the step it depends on (independent steps: none)", b.js("T.n('.arrow')") == 0 and b.js("T.n('.step')") == 3)
        b.shot(out / "03-several-failures-session-link-dark.png")
        collect_contrast(b, "dark", "F5")
        b.click(".step[data-step=pull-inventory]")
        b.wait("document.querySelector('pre.out')")
        lib(b)
        check("O3", "raw error output of a second failing step in 2 clicks (workflow, step)",
              "PermissionError: sheet inv-2026" in b.js("T.txt('pre.out .hit:last-of-type')"))
        check("AC-11", "no session link on a trace without a session ID", b.js("T.n('.t .act a[href^=\"hermes://\"]')") == 0)
        b.click(".step[data-step=archive]")
        lib(b)
        check("AC-12", "step with no traces: the trace area says so", b.js("T.txt('#detail .empty')") == "No traces on file.")
        collect_contrast(b, "dark", "F5 no traces")

        # ---------------- F2: first error line, paused (AC-6, AC-19) ----------------
        b.click(".wf[data-wf=weekly-ai-analysis-publication]")
        b.wait("document.querySelector('pre.out')")
        lib(b)
        check("AC-19", "pending: the block shows the first error line, not an empty block",
              b.js("document.querySelector('.why').dataset.summary") == "pending"
              and b.js("T.txt('.why p')").startswith("Blocked: prompt matches threat pattern")
              and b.js("T.txt('.why > .mono')") == "first error line · no summary yet")
        check("AC-10", "Hermes BLOCKED shows as a failed trace with the raw status line",
              b.js("T.txt('.t .status')") == "BLOCKED" and b.js("document.querySelector('.t .glyph').classList.contains('g-failed')"))
        pg = b.js("[...document.querySelectorAll('.step[data-step=publish-gate] .state .glyph')].map(g => [g.className.replace('glyph g-', ''), getComputedStyle(g).color])")
        grey = b.js("getComputedStyle(document.documentElement).getPropertyValue('--text-3').trim()")
        check("AC-6", "paused step: grey paused glyph next to its health glyph",
              [p[0] for p in pg] == ["ok", "paused"] and pg[1][1] == "rgb(125, 133, 144)" and grey == "#7d8590", str(pg))
        check("AC-6", "workflow row shows a paused glyph and stays failed because of Briefing only",
              b.js("[...document.querySelectorAll('.wf[data-wf=weekly-ai-analysis-publication] .glyph')].map(g => g.className.replace('glyph g-', ''))") == ["failed", "paused"])
        b.shot(out / "04-first-error-line-paused-dark.png", full=False)
        collect_contrast(b, "dark", "F2")
        b.click(".step[data-step=publish-gate]")
        lib(b)
        check("AC-6", "paused step is not shown as blocked although Briefing failed",
              "paused since Sep 8" in b.js("T.txt('.step[data-step=publish-gate] .cap')")
              and b.js("T.n('.step[data-step=publish-gate] .g-blocked')") == 0)

        # unavailable: write the row the watcher would write after two failed attempts, then reload
        state = json.loads(__import__("urllib.request").request.urlopen(base + "/api/state").read())
        briefing = next(w for w in state["workflows"] if w["id"] == "weekly-ai-analysis-publication")["steps"][0]
        SummaryStore(root / "debugger.db").put(briefing["job_id"], briefing["latest"]["error_sig"], trace_file=briefing["latest"]["file"],
                                               status="unavailable", summary=None, session_id=None, attempts=2,
                                               created_at="2026-11-08T21:15:00")
        goto(b, base + "/?wf=weekly-ai-analysis-publication")
        check("AC-19", "unavailable: the block still shows the first error line",
              b.js("document.querySelector('.why').dataset.summary") == "unavailable"
              and b.js("T.txt('.why p')").startswith("Blocked: prompt matches")
              and b.js("T.txt('.why > .mono')") == "first error line · summary unavailable")
        b.shot(out / "05-summary-unavailable-dark.png", full=False)

        # ---------------- F3: missed, single step (AC-12, AC-13) ----------------
        b.click(".wf[data-wf=google-oauth-token-refresh]")
        b.settle(0.4)
        lib(b)
        check("AC-12", "single-step workflow: one step box, no arrows", b.js("T.n('.step')") == 1 and b.js("T.n('.arrow')") == 0)
        check("AC-13", "missed run shows as missed in rail, step, banner and trace list",
              b.js("document.querySelector('.step .glyph').classList.contains('g-missed')")
              and b.js("T.txt('.calm')") == "Token refresh: scheduled Thu Nov 12 06:00, never ran."
              and b.js("T.txt('.t .status')") == "scheduled, never ran"
              and b.js("document.querySelector('.t .glyph').classList.contains('g-missed')"), b.js("T.txt('.calm')"))
        check("AC-19", "a missed step gets no summary block", b.js("T.n('.why')") == 0)
        b.shot(out / "06-missed-single-step-dark.png", full=False)
        collect_contrast(b, "dark", "F3")

        # ---------------- F4: no failure (AC-19, AC-9) ----------------
        b.click(".wf[data-wf=hermes-cron-error-email-report]")
        b.settle(0.4)
        lib(b)
        check("AC-19", "no failure: no block, the step strip moves up under the title",
              b.js("T.n('.why')") == 0 and b.js("T.n('#summary')") == 0
              and b.js("document.querySelector('.pipe').getBoundingClientRect().top - document.querySelector('.lede').getBoundingClientRect().bottom") < 30)
        states = b.js("[...document.querySelectorAll('.t .tog .glyph')].map(g => g.className.replace('glyph g-', ''))")
        check("AC-9", "old failed and missed traces stay in the list under an ok step",
              states == ["ok", "ok", "ok", "missed", "ok", "failed", "ok", "failed", "ok", "ok"], ",".join(states))
        b.shot(out / "07-no-failure-history-dark.png", full=False)
        collect_contrast(b, "dark", "F4")
        b.click(".traces .t:nth-of-type(6) .tog")
        b.wait("document.querySelector('pre.out')")
        lib(b)
        check("O3", "an old failure's raw output opens with one click on its row",
              b.js("T.txt('.traces .t:nth-of-type(6) .status')") == "silent (empty output)" and b.js("T.n('pre.out')") == 1)

        # ---------------- F6: invalid file (AC-3, AC-12) ----------------
        b.click(".wf[data-wf=broken-example]")
        lib(b)
        check("AC-12", "invalid scaffold file: main area names the file and the bad field",
              b.js("T.txt('.why h2')") == "broken-example.yaml is not valid"
              and b.js("T.txt('.why p')") == "steps[0].job_id: job 'ffffffffffff' is not in cron/jobs.json", b.js("T.txt('.why p')"))
        b.shot(out / "08-invalid-file-dark.png", full=False)
        collect_contrast(b, "dark", "F6")

        # ---------------- AC-24: none of the five NOs ----------------
        page_text = ""
        for wf_id in ["industry-analysis-blog-post", "weekly-store-sync", "weekly-ai-analysis-publication", "broken-example",
                      "google-oauth-token-refresh", "hermes-cron-error-email-report"]:
            b.click(f".wf[data-wf={wf_id}]")
            b.settle(0.3)
            page_text += " " + b.js("document.querySelector('.app').innerText + ' ' + [...document.querySelectorAll('[data-tip]')].map(e => e.dataset.tip).join(' ')")
        lib(b)
        check("AC-24", "no editing control anywhere (input, textarea, select, form, contenteditable)",
              b.js("T.n('input, textarea, select, form, [contenteditable]')") == 0)
        controls = sorted(set(b.js("[...document.querySelectorAll('[data-tip]')].map(e => e.dataset.tip.replace(/ \\S+\\.(py|sh)$/, ''))")))
        import re

        lowered = re.sub(r"\s+", " ", page_text.lower())
        patterns = [r"\bsuggest", r"\brecommend", r"\byou should\b", r"\bto fix\b", r"\btry (to|running|again)\b", r"\bnotif",
                    r"\btool calls?\b", r"\b(input|output|prompt) tokens\b", r"\bprompt:", r"\bnext step", r"\bedit\b", r"\bsave\b", r"\bdelete\b"]
        banned = [p for p in patterns if re.search(p, lowered)]
        check("AC-24", "no fix or scaffold suggestion, no model or tool-call detail, no notification or editing wording on any screen",
              not banned, f"found: {banned}" if banned else f"0 of {len(patterns)} patterns in {len(lowered)} characters of page text and tooltips")

        # ---------------- light theme, same checks of the first screen (AC-26) ----------------
        goto(b, base + "/?theme=light")
        b.wait("document.querySelector('pre.out')")
        lib(b)
        check("AC-26", "light theme applies the light tokens",
              b.js("T.css('body', 'backgroundColor')") == "rgb(255, 255, 255)" and b.js("T.css('.rail', 'backgroundColor')") == "rgb(246, 248, 250)")
        b.shot(out / "09-first-screen-light.png", full=False)
        for wf_id in ["industry-analysis-blog-post", "weekly-store-sync", "weekly-ai-analysis-publication", "broken-example",
                      "google-oauth-token-refresh", "hermes-cron-error-email-report"]:
            b.click(f".wf[data-wf={wf_id}]")
            b.settle(0.35)
            lib(b)
            collect_contrast(b, "light", wf_id)
        b.click(".wf[data-wf=weekly-ai-analysis-publication]")
        b.settle(0.4)
        b.shot(out / "10-paused-light.png", full=False)
        b.click("[data-theme-toggle]")
        check("AC-28", "theme button switches dark and light", b.js("document.documentElement.dataset.theme") == "dark")

        # ---------------- narrow (AC-26, 4.8) ----------------
        b.resize(800, 900)
        goto(b, base + "/")
        b.wait("document.querySelector('pre.out')")
        lib(b)
        check("AC-26", "below 900px: rail stacks on top, step strip is vertical, no sideways scroll",
              b.js("T.css('.app', 'gridTemplateColumns').split(' ').length") == 1
              and b.js("T.css('.pipe', 'flexDirection')") == "column"
              and b.js("document.documentElement.scrollWidth <= innerWidth")
              and b.js("document.querySelector('.rail').getBoundingClientRect().bottom <= document.querySelector('.main').getBoundingClientRect().top + 1"),
              f"scrollWidth {b.js('document.documentElement.scrollWidth')} of {b.js('innerWidth')}")
        b.shot(out / "11-narrow-800-dark.png")
        collect_contrast(b, "dark", "narrow")
        b.resize(390, 844)
        goto(b, base + "/?theme=light")
        lib(b)
        check("AC-26", "at 390px wide there is still no sideways scroll", b.js("document.documentElement.scrollWidth <= innerWidth"),
              f"scrollWidth {b.js('document.documentElement.scrollWidth')} of {b.js('innerWidth')}")
        b.shot(out / "12-narrow-390-light.png")
        b.resize(1440, 900)

        # ---------------- glyphs and tooltips (AC-27) ----------------
        goto(b, base + "/")
        b.wait("document.querySelector('pre.out')")
        lib(b)
        shapes = b.js("(() => { const out = {}; for (const s of ['failed','ok','blocked','missed','paused','none']) { const d = document.createElement('div'); d.innerHTML = I[s]; out[s] = d.firstChild.innerHTML; } return out; })()")
        check("AC-27", "six states, six different glyph shapes", len(set(shapes.values())) == 6, ", ".join(shapes))
        seen_states = set()
        hover_fail, hovered = [], 0
        for wf_id in ["industry-analysis-blog-post", "weekly-store-sync", "weekly-ai-analysis-publication", "google-oauth-token-refresh"]:
            b.click(f".wf[data-wf={wf_id}]")
            b.settle(0.35)
            lib(b)
            count = b.js("document.querySelectorAll('[data-tip]').length")
            for i in range(count):
                b.js(f"document.querySelectorAll('[data-tip]')[{i}].setAttribute('data-probe', '1')")
                expected = b.js("document.querySelector('[data-probe]').dataset.tip")
                seen_states.update(b.js("[...document.querySelector('[data-probe]').classList].filter(c => c.startsWith('g-'))"))
                b.cdp("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": 2, "y": 898})
                b.hover("[data-probe]")
                shown = b.js("document.querySelector('#tip').classList.contains('on') ? document.querySelector('#tip').textContent : null")
                hovered += 1
                if shown != expected:
                    hover_fail.append(f"{wf_id}:{expected!r} got {shown!r}")
                b.js("document.querySelector('[data-probe]').removeAttribute('data-probe')")
        check("AC-27", "every glyph and icon button shows its tooltip on hover", not hover_fail, f"{hovered} elements hovered" if not hover_fail else "; ".join(hover_fail[:4]))
        check("AC-27", "the hover pass covered all six glyph states (failed, ok, blocked, missed, paused, did not run)", seen_states == {"g-failed", "g-ok", "g-blocked", "g-missed", "g-paused", "g-none"}, ",".join(sorted(seen_states)))
        b.click(".wf[data-wf=weekly-store-sync]")
        b.settle(0.3)
        b.hover(".step[data-step=archive] .glyph")
        check("AC-27", "the did-not-run glyph has a tooltip", b.js("document.querySelector('#tip').textContent") == "Did not run")
        b.shot(out / "13-tooltip-hover-dark.png", full=False)

        # ---------------- keyboard (AC-27, AC-28) ----------------
        for target in ["industry-analysis-blog-post", "weekly-ai-analysis-publication"]:
            goto(b, base + f"/?wf={target}")
            b.wait("document.querySelector('pre.out')")
            lib(b)
            b.js("window.__f = T.focusables(); window.__f.forEach((e, i) => e.setAttribute('data-fi', i)); document.activeElement.blur(); null")
            total = b.js("window.__f.length")
            reached, no_ring, no_tip, order = set(), [], [], []
            for _ in range(total + 4):
                b.key("Tab")
                info = b.js("""(() => { const e = document.activeElement; if (!e || e === document.body) return null;
                    const c = getComputedStyle(e); const tip = document.querySelector('#tip');
                    const needs = e.hasAttribute('data-tip') || e.querySelector('.glyph[data-tip]') !== null;
                    return { i: e.getAttribute('data-fi'), name: T.describe(e), outline: c.outlineStyle + ' ' + c.outlineWidth + ' ' + c.outlineColor,
                             needs, tip: tip.classList.contains('on') ? tip.textContent : null, label: !!(e.getAttribute('aria-label') || e.textContent.trim()) }; })()""")
                if not info or info["i"] is None or info["i"] in reached:
                    if info and info["i"] in reached:
                        break
                    continue
                reached.add(info["i"])
                order.append(info["name"])
                if info["outline"] != "solid 2px rgb(74, 132, 254)":
                    no_ring.append(f"{info['name']}: {info['outline']}")
                if info["needs"] and not info["tip"]:
                    no_tip.append(info["name"])
                if not info["label"]:
                    no_ring.append(f"{info['name']}: no accessible name")
            check("AC-28", f"Tab reaches every control ({target.split('-')[0]})", len(reached) == total, f"{len(reached)} of {total}")
            check("AC-28", f"every focused control has a 2px blue focus ring and an accessible name ({target.split('-')[0]})", not no_ring, "; ".join(no_ring[:3]))
            check("AC-27", f"every glyph and icon button shows its tooltip on keyboard focus ({target.split('-')[0]})", not no_tip, "; ".join(no_tip[:4]) or f"{total} controls")
        check("AC-27", "every glyph is focusable or sits inside a focusable control",
              b.js("[...document.querySelectorAll('.glyph')].every(g => g.tabIndex === 0 || g.closest('button, a'))"))
        check("AC-28", "every control is a real button or link",
              b.js("[...document.querySelectorAll('[data-wf],[data-step],[data-open],[data-copy],[data-theme-toggle]')].every(e => ['BUTTON', 'A'].includes(e.tagName))"))
        # keyboard-only path to the raw output
        goto(b, base + "/?open=0")
        lib(b)
        check("AC-8", "with ?open=0 nothing is expanded (control case for the keyboard test)", b.js("T.n('pre.out')") == 0)
        b.js("document.querySelector('.t .tog').focus()")
        b.key("Enter")
        b.wait("document.querySelector('pre.out')")
        check("AC-28", "Enter on a trace row opens its output and focus stays on the row",
              b.js("document.activeElement.classList.contains('tog') && document.activeElement.getAttribute('aria-expanded') === 'true'"))
        b.js("document.querySelector('.t .ib[data-copy]').focus()")
        b.key("Tab")
        b.key("Tab", shift=True)
        b.settle(0.15)
        b.shot(out / "14-focus-ring-and-tooltip-dark.png", full=False)
        b.key("Enter")
        b.settle(0.2)
        check("AC-28", "copy button confirms with a toast",
              b.js("document.querySelector('#toast').classList.contains('on') && document.querySelector('#toast').textContent.startsWith('copied wfr-industry-analysis-blog-post-')"),
              b.js("document.querySelector('#toast').textContent"))
        b.js("document.querySelector('.step[data-step=watchlist-scout]').focus()")
        b.key("Tab", shift=True)
        b.key("Tab")
        b.settle(0.15)
        b.shot(out / "15-focused-glyph-tooltip-dark.png", full=False)

        # ---------------- polling (4.6, AC-12) ----------------
        # A separate server process, so that stopping it really closes every connection.
        import socket
        import subprocess

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        proc = subprocess.Popen([sys.executable, "-m", "wfd.cli", "serve", "--fixtures", "--port", str(port),
                                 "--data-dir", str(work / "poll")], cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        poll_base = f"http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                __import__("urllib.request").request.urlopen(poll_base + "/api/state").read()
                break
            except OSError:
                time.sleep(0.1)
        goto(b, poll_base + "/?poll=1000")
        b.wait("document.querySelector('pre.out')")
        lib(b)
        b.js("document.querySelector('pre.out').scrollTop = 40; null")
        b.settle(1.6)
        check("AC-12", "a poll does not collapse the open trace or reset its scroll",
              b.js("T.n('pre.out')") == 1 and b.js("document.querySelector('pre.out').scrollTop") == 40)
        proc.terminate()
        proc.wait(timeout=10)
        b.wait("document.querySelector('.dot.stale')", timeout=8)
        lib(b)
        foot = b.js("T.txt('#foot-text')")
        check("AC-12", "poll fails: data is kept and the footer shows the last good read time",
              foot == "example · last read 10:42 · fixtures" and b.js("T.n('.wf')") == 6 and b.js("T.n('pre.out')") == 1, foot)
        b.hover(".dot")
        check("AC-27", "the stale marker has a tooltip", b.js("document.querySelector('#tip').textContent") == "Not updating: the server did not answer")
        b.shot(out / "16-poll-failed-dark.png", full=False)

        # ---------------- no workflows (AC-12) ----------------
        goto(b, empty_base + "/")
        text = b.js("T.txt('.calm')")
        check("AC-12", "no workflows: the scaffold folder path is shown", text == f"No workflow files in {root / 'scaffolds-empty'}" and b.js("T.n('.wf')") == 0, text.replace(str(root), "<fixtures>"))
        b.shot(out / "17-no-workflows-dark.png", full=False)
        collect_contrast(b, "dark", "no workflows")
        check("AC-12", "fixture mode: footer says fixtures", b.js("T.txt('#foot-text')").endswith("· fixtures"))
        for running in (server, empty_server):
            running.shutdown()
            running.server_close()

    # ---------------- contrast (AC-28) ----------------
    print("\nContrast of every rendered text and background pair (WCAG 2 ratio, need >= 4.5)")
    worst = 99.0
    for theme in ("dark", "light"):
        pairs = sorted(contrast.get(theme, {}).items(), key=lambda kv: kv[1]["ratio"])
        print(f"  {theme}: {len(pairs)} pairs")
        for key, value in pairs:
            worst = min(worst, value["ratio"])
            print(f"    {value['ratio']:>5.2f}  {key}   e.g. {value['eg']!r} ({value['where']})")
    check("AC-28", "all rendered text passes WCAG AA contrast in both themes", worst >= 4.5, f"lowest ratio {worst:.2f}")

    failed = [r for r in RESULTS if not r[0]]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    by = {}
    for ok, criterion, _name in RESULTS:
        by.setdefault(criterion, []).append(ok)
    print("  ".join(f"{c}:{sum(v)}/{len(v)}" for c, v in by.items()))
    import shutil

    shutil.rmtree(work, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "evidence" / "browser"))
