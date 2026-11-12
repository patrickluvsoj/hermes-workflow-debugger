// Hermes Workflow Debugger frontend. Reads /api/state every 30 seconds. Read-only:
// the only requests it makes are GETs to this server.
'use strict';

const POLL_MS = 30000;
const I = {
  failed: '<svg viewBox="0 0 18 18"><rect class="solid" x="1.5" y="1.5" width="15" height="15"/><path class="cut" d="M6 6l6 6M12 6l-6 6"/></svg>',
  ok: '<svg viewBox="0 0 18 18"><rect x="1.75" y="1.75" width="14.5" height="14.5"/><path d="M5.2 9.3l2.6 2.6 5-5.6"/></svg>',
  blocked: '<svg viewBox="0 0 18 18"><path d="M9 1.8l7.2 7.2L9 16.2 1.8 9z"/></svg>',
  paused: '<svg viewBox="0 0 18 18"><rect x="1.75" y="1.75" width="14.5" height="14.5"/><path class="bars" d="M7 5.8v6.4M11 5.8v6.4"/></svg>',
  missed: '<svg viewBox="0 0 18 18"><circle cx="9" cy="9" r="6.8" stroke-dasharray="2.6 2.6"/></svg>',
  none: '<svg viewBox="0 0 18 18"><path d="M5 9h8"/></svg>',
  arrow: '<svg viewBox="0 0 22 22"><path d="M2 11h17M14 6l5 5-5 5"/></svg>',
  ext: '<svg viewBox="0 0 16 16"><path d="M9.5 2.5h4v4M13.5 2.5L7.5 8.5M11.5 9.5v4h-9v-9h4"/></svg>',
  copy: '<svg viewBox="0 0 16 16"><rect x="5.5" y="5.5" width="8" height="8"/><path d="M10.5 5.5v-3h-8v8h3"/></svg>',
  file: '<svg viewBox="0 0 16 16"><path d="M9.5 1.5h-6v13h9v-10zM9.5 1.5v3h3M5.5 8.5h5M5.5 11h5"/></svg>',
  session: '<svg viewBox="0 0 16 16"><path d="M2.5 3.5h11v7h-6l-3 2.5v-2.5h-2z"/></svg>',
  folder: '<svg viewBox="0 0 18 18"><path d="M1.75 4.25h5l1.5 2h8v8.5H1.75z"/></svg>',
  theme: '<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="5.5"/><path class="solid" d="M8 2.5a5.5 5.5 0 0 1 0 11z"/></svg>'
};
const WORD = { failed: 'Failed', ok: 'OK', blocked: 'Blocked: an upstream step failed', paused: 'Paused',
  missed: 'Missed: scheduled, never ran', none: 'Did not run' };
const DAY = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

const S = { data: null, lastGood: null, stale: false, error: null, wf: null, step: null, open: null,
  traces: {}, outputs: {}, picked: false };
const Q = new URLSearchParams(location.search);

const esc = v => String(v == null ? '' : v).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const g = (state, tip, focusable) => `<span class="glyph g-${state}" role="img" aria-label="${esc(tip || WORD[state])}" data-tip="${esc(tip || WORD[state])}"${focusable ? ' tabindex="0"' : ''}>${I[state]}</span>`;
const parse = iso => { const m = /^(\d+)-(\d+)-(\d+)T(\d+):(\d+)/.exec(iso || ''); return m ? new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]) : null; };
const p2 = n => String(n).padStart(2, '0');
const hm = d => `${p2(d.getHours())}:${p2(d.getMinutes())}`;
const day = d => `${MON[d.getMonth()]} ${d.getDate()}`;
const when = (iso, sep) => { const d = parse(iso); return d ? `${DAY[d.getDay()]} ${day(d)}${sep == null ? ' · ' : sep}${hm(d)}` : ''; };
const short = iso => { const d = parse(iso); return d ? `${day(d)} ${hm(d)}` : ''; };
const dur = s => s == null ? '' : `${Math.floor(s / 60)}m ${p2(Math.round(s % 60))}s`;

function schedule(text) {
  let m = /^(\d+) (\d+) \* \* (\d)$/.exec(text);
  if (m) return `${DAY[+m[3] % 7]} ${p2(m[2])}:${p2(m[1])}`;
  m = /^(\d+) (\d+) \* \* \*$/.exec(text);
  if (m) return `Daily ${p2(m[2])}:${p2(m[1])}`;
  m = /^(\d+) (\d+) (\d+) \* \*$/.exec(text);
  if (m) return `Monthly, day ${m[3]} ${p2(m[2])}:${p2(m[1])}`;
  m = /^every (\d+)m$/.exec(text);
  if (m) return +m[1] % 60 === 0 ? `Every ${+m[1] / 60}h` : `Every ${m[1]}m`;
  return text;
}

const curWf = () => S.data && S.data.workflows.find(w => w.id === S.wf);
const curStep = w => w && w.steps.find(s => s.id === S.step);
const badStep = w => w.steps.find(s => !s.paused && s.health === 'failed') || w.steps.find(s => !s.paused && s.health === 'missed') || w.steps[0];
const traceKey = t => t.file || 'missed-' + t.time;

function pickWf(id, stepId, expand) {
  const w = S.data.workflows.find(x => x.id === id);
  if (!w) return;
  S.wf = w.id;
  const st = w.steps.find(s => s.id === stepId) || (w.steps.length ? badStep(w) : null);
  pickStep(st, expand);
}
function pickStep(st, expand) {
  S.step = st ? st.id : null;
  S.open = null;
  if (!st) return;
  if (expand && st.latest && ['failed', 'missed'].includes(st.health)) openTrace(st, st.latest);
  loadTraces(st);
}
function openTrace(st, t) {
  S.open = traceKey(t);
  if (t.file) loadOutput(st.job_id, t.file);
}

// ---- data ---------------------------------------------------------------
async function getJSON(path) { const r = await fetch(path, { cache: 'no-store' }); if (!r.ok) throw new Error(String(r.status)); return r.json(); }
async function getText(path) { const r = await fetch(path, { cache: 'no-store' }); if (!r.ok) throw new Error(String(r.status)); return r.text(); }

async function poll() {
  try {
    S.data = await getJSON('/api/state');
    S.lastGood = S.data.read_at; S.stale = false; S.error = null;
    const list = S.data.workflows;
    if (!S.picked && list.length) {
      S.picked = true;
      // AC-8: the rail is already ordered problems first, so the first row is the first failing workflow.
      pickWf(list.some(w => w.id === Q.get('wf')) ? Q.get('wf') : list[0].id, Q.get('step'), Q.get('open') !== '0');
    } else if (list.length && !curWf()) {
      pickWf(list[0].id, null, true);
    } else if (curWf()) {
      const st = curStep(curWf());
      if (st) loadTraces(st, true);
    }
  } catch (e) {
    S.stale = true; S.error = e.message;
  }
  render();
}
async function loadTraces(st, refresh) {
  if (S.traces[st.job_id] && !refresh) return;
  try { S.traces[st.job_id] = (await getJSON(`/api/steps/${encodeURIComponent(st.job_id)}/traces`)).traces; }
  catch (e) { if (!S.traces[st.job_id]) S.traces[st.job_id] = { error: true }; }
  render();
}
async function loadOutput(jobId, file) {
  const key = jobId + '/' + file;
  if (S.outputs[key] != null) return;
  try { S.outputs[key] = await getText(`/api/traces/${encodeURIComponent(jobId)}/${encodeURIComponent(file)}`); }
  catch (e) { S.outputs[key] = { error: true }; }
  render();
}

// ---- rendering ----------------------------------------------------------
// Same rule as is_failure_line() in wfd/traces.py.
const FAILED_STATUS = /^\*\*Status:\*\*.*(script failed|silent \(empty output\)|BLOCKED)/;
const hit = line => { const s = line.trim(); return FAILED_STATUS.test(s) || /^(failure_reason=|\*\*Scanner result:\*\*|Script exited with code|Traceback)/.test(s) || /^[A-Za-z_][\w.]*(Error|Exception)\b/.test(s) || /^(ERROR|FATAL)\b/.test(s); };
const outputHTML = text => text.split('\n').map(l => hit(l) ? `<span class="hit">${esc(l)}</span>` : esc(l)).join('\n');

function renderRail() {
  const d = S.data;
  document.getElementById('wfs').innerHTML = !d ? '' : d.workflows.map(w => {
    const p = w.paused_count;
    const meta = w.invalid ? `${w.file}: ${w.error}` : w.last_run ? when(w.last_run) : 'no runs on file';
    return `<button class="wf" data-wf="${esc(w.id)}" aria-current="${w.id === S.wf}">
      ${g(w.health, w.invalid ? 'Failed: workflow file is not valid' : null)}<span class="row"><span class="name">${esc(w.title)}</span>${p ? g('paused', p + (p > 1 ? ' steps paused' : ' step paused')) : ''}</span>
      <span class="desc">${esc(w.description)}</span>
      <span class="meta mono">${esc(meta)}</span>
    </button>`;
  }).join('');
  const read = S.lastGood ? hm(parse(S.lastGood)) : '';
  const mode = d ? d.mode : '';
  const text = !d ? (S.stale ? 'cannot reach the server' : 'reading') : `${d.profile} · ${S.stale ? 'last read' : 'read'} ${read} · ${mode}`;
  document.getElementById('foot').innerHTML =
    `<div class="live mono"><span class="dot${S.stale ? ' stale' : ''}" role="img" tabindex="0" aria-label="${S.stale ? 'Not updating: the server did not answer' : 'Up to date'}" data-tip="${S.stale ? 'Not updating: the server did not answer' : 'Up to date'}"></span><span id="foot-text">${esc(text)}</span></div>` +
    `<button class="ib" data-theme-toggle aria-label="Switch between dark and light" data-tip="Dark / light">${I.theme}</button>`;
}

function summaryBlocks(w) {
  if (w.invalid) {
    return `<div class="why">${g('failed', 'Failed: workflow file is not valid', true)}<h2>${esc(w.file)} is not valid</h2>` +
      w.errors.map(e => `<p class="raw">${esc(e)}</p>`).join('') + `</div>`;
  }
  return w.problems.map(p => {
    if (p.kind === 'missed') return `<div class="calm">${g('missed', null, true)}<span>${esc(p.title)}: scheduled ${esc(when(p.time, ' '))}, never ran.</span></div>`;
    const s = p.summary || {};
    const head = `${g('failed', null, true)}<h2>${esc(p.title)} failed<span class="mono">${esc(when(p.time))}</span></h2>`;
    if (s.status === 'ready') return `<div class="why" data-summary="ready">${head}<p>${esc(s.text)}</p><span class="mono">summarized ${esc(short(s.summarized_at))}</span></div>`;
    const note = s.status === 'unavailable' ? 'first error line · summary unavailable' : 'first error line · no summary yet';
    return `<div class="why" data-summary="${esc(s.status || 'pending')}">${head}<p class="raw">${esc(s.first_error_line || 'no output')}</p><span class="mono">${note}</span></div>`;
  }).join('');
}

function stepCaption(st) {
  const last = st.last_run ? 'last ' + when(st.last_run, ' ') : 'no runs on file';
  const parts = [];
  if (st.paused) parts.push('paused' + (st.paused_since ? ' since ' + day(parse(st.paused_since)) : ''));
  if (st.health === 'blocked') parts.push('blocked by ' + st.blocked_by.join(', '));
  if (st.health === 'missed') parts.push('missed ' + short(st.latest.time));
  parts.push(last);
  return parts.join(' · ');
}
function pipeHTML(w) {
  return `<div class="pipe">` + w.steps.map((st, i) =>
    // An arrow means "depends on the step before". Independent neighbours get a plain gap.
    (!i ? '' : st.depends_on.includes(w.steps[i - 1].id) ? `<div class="arrow" aria-hidden="true">${I.arrow}</div>` : `<div class="gap" aria-hidden="true"></div>`) +
    `<button class="step" data-step="${esc(st.id)}" aria-pressed="${st.id === S.step}">
      <span class="kind">cron · ${esc(schedule(st.schedule))}</span><span class="state">${g(st.health)}${st.paused ? g('paused') : ''}</span>
      <span class="name">${esc(st.title)}</span>
      <span class="cap mono">${esc(stepCaption(st))}</span>
    </button>`).join('') + `</div>`;
}

function traceRow(st, t) {
  const key = traceKey(t), open = S.open === key;
  const label = `${WORD[t.state].split(':')[0]}, ${when(t.time, ' ')}`;
  const id = t.workflow_run_id || (t.file ? `${st.job_id}/${t.file}` : null);
  let acts = '';
  if (t.file) acts += `<button class="ib" data-open="${esc(key)}" aria-expanded="${open}" aria-label="Show output for ${esc(when(t.time, ' '))}" data-tip="Output">${I.file}</button>`;
  if (id) acts += `<button class="ib" data-copy="${esc(id)}" aria-label="Copy ${t.workflow_run_id ? 'run' : 'trace'} ID ${esc(id)}" data-tip="Copy ${t.workflow_run_id ? 'run' : 'trace'} ID">${I.copy}</button>`;
  if (st.source) acts += `<a class="ib" href="/api/source/${encodeURIComponent(st.job_id)}" target="_blank" rel="noopener" aria-label="Open CRON source ${esc(st.source.script)}" data-tip="CRON source ${esc(st.source.script)}">${I.ext}</a>`;
  if (t.session_id) acts += `<a class="ib" href="hermes://open/sessions/${encodeURIComponent(t.session_id)}" data-copy="${esc(t.session_id)}" data-follow aria-label="Open session ${esc(t.session_id)} in Hermes and copy its ID" data-tip="Open session in Hermes · copies ID">${I.session}</a>`;
  let h = `<div class="t"><button class="tog" data-open="${esc(key)}" aria-expanded="${open}" aria-label="${esc(label)}">
      ${g(t.state)}<span class="mono">${esc(when(t.time))}</span><span class="mono status${t.state === 'failed' ? ' bad' : ''}">${esc(t.status_line || '')}</span><span class="mono dur">${esc(dur(t.duration_s))}</span>
    </button><span class="act">${acts}</span></div>`;
  if (open) {
    const out = t.file ? S.outputs[st.job_id + '/' + t.file] : null;
    h += `<div class="x">` + (!t.file ? `<div class="empty">Scheduled, never ran. No output file.</div>`
      : out == null ? `<div class="empty mono">reading</div>`
      : out.error ? `<div class="empty">This output file could not be read.</div>`
      : `<pre class="out${t.state === 'failed' ? ' error' : ''}" tabindex="0" aria-label="Output file ${esc(t.file)}">${outputHTML(out)}</pre>`) + `</div>`;
  }
  return h;
}
function tracesHTML(st) {
  const script = st.source
    ? `${esc(st.source.script)} · changed ${esc(short(st.source.changed_at))}`
    : 'no script on file';
  const src = `<h2 class="what-name">${esc(st.title)}</h2>` +
    (st.description ? `<p class="what">${esc(st.description)}</p>` : '') +
    `<span class="mono">${script}</span>`;
  const list = S.traces[st.job_id];
  let body;
  if (!list) body = `<div class="detail"><div class="empty mono">reading</div></div>`;
  else if (list.error) body = `<div class="detail"><div class="empty">The trace list could not be read.</div></div>`;
  else if (!list.length) body = `<div class="detail"><div class="empty">${g('none', null, true)}<span>No traces on file.${st.paused && st.paused_since ? ' Paused since ' + esc(day(parse(st.paused_since))) + '.' : ''}</span></div></div>`;
  else body = `<div class="traces">` + list.map(t => traceRow(st, t)).join('') + `</div>`;
  return `<div class="src">${src}</div>` + body;
}

function renderMain() {
  const d = S.data, main = document.getElementById('main');
  if (!d) { main.innerHTML = S.stale ? `<div class="calm">${g('missed', 'No answer from the server', true)}<span>The server did not answer. Start it with <span class="mono path">wfd serve</span>.</span></div>` : ''; return; }
  if (!d.workflows.length) {
    main.innerHTML = `<div class="calm">${g('none', 'No workflows', true)}<span>No workflow files in <span class="mono path">${esc(d.scaffold_dir)}</span></span></div>`;
    return;
  }
  const w = curWf(); if (!w) { main.innerHTML = ''; return; }
  const st = curStep(w);
  const blocks = summaryBlocks(w);
  main.innerHTML = `<header><h1>${esc(w.title)}</h1><p class="lede">${esc(w.description)}</p></header>` +
    (blocks ? `<section class="section" id="summary">${blocks}</section>` : '') +
    (w.steps.length ? `<section class="section">${pipeHTML(w)}</section>` : '') +
    (st ? `<section id="detail">${tracesHTML(st)}</section>` : '');
}

function render() {
  const keep = document.activeElement && document.activeElement.closest ? focusKey(document.activeElement) : null;
  const scroll = [...document.querySelectorAll('pre.out')].map(p => p.scrollTop);
  renderRail(); renderMain();
  document.querySelectorAll('pre.out').forEach((p, i) => { if (scroll[i]) p.scrollTop = scroll[i]; });
  if (keep) { const el = document.querySelector(keep); if (el) el.focus({ preventScroll: true }); }
}
function focusKey(el) {
  for (const a of ['data-wf', 'data-step']) if (el.hasAttribute(a)) return `[${a}="${CSS.escape(el.getAttribute(a))}"]`;
  if (el.hasAttribute('data-open')) return `${el.classList.contains('tog') ? '.tog' : '.ib'}[data-open="${CSS.escape(el.getAttribute('data-open'))}"]`;
  if (el.hasAttribute('data-theme-toggle')) return '[data-theme-toggle]';
  return null;
}

// ---- interaction --------------------------------------------------------
function toast(msg) { const t = document.getElementById('toast'); t.textContent = msg; t.classList.add('on'); clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove('on'), 1400); }
function setTheme(theme) { document.documentElement.dataset.theme = theme; try { localStorage.setItem('wfd-theme', theme); } catch (e) { /* private mode */ } }

document.addEventListener('click', e => {
  const b = e.target.closest('[data-copy],[data-wf],[data-step],[data-open],[data-theme-toggle]'); if (!b) return;
  if (b.dataset.copy) { if (navigator.clipboard) navigator.clipboard.writeText(b.dataset.copy).catch(() => {}); toast('copied ' + b.dataset.copy); return; }  // a link with data-copy still follows its href
  if (b.hasAttribute('data-theme-toggle')) { setTheme(document.documentElement.dataset.theme === 'light' ? 'dark' : 'light'); return; }
  const w = curWf();
  if (b.dataset.wf) pickWf(b.dataset.wf, null, true);
  else if (b.dataset.step) pickStep(w.steps.find(s => s.id === b.dataset.step), true);
  else if (b.dataset.open) {
    const st = curStep(w), list = S.traces[st.job_id] || [];
    const t = list.find && list.find(x => traceKey(x) === b.dataset.open);
    if (S.open === b.dataset.open) S.open = null; else if (t) openTrace(st, t);
  }
  render();
});

const tipEl = document.getElementById('tip');
let tipAnchor = null;
function tipFor(el, focus) {
  const own = el.closest('[data-tip]');
  if (own) return [own, own.dataset.tip];
  if (!focus) return null;
  // A focused row or step button has no tooltip of its own: show the tooltips of the glyphs inside it.
  const host = el.closest('button');
  const inner = host ? [...host.querySelectorAll('.glyph[data-tip]')] : [];
  return inner.length ? [inner[0], inner.map(x => x.dataset.tip).join(' · ')] : null;
}
function placeTip() {
  if (!tipAnchor || !tipAnchor.isConnected) return hideTip();
  const b = tipAnchor.getBoundingClientRect(), w = tipEl.offsetWidth;
  tipEl.style.left = Math.max(6, Math.min(innerWidth - w - 6, b.left + b.width / 2 - w / 2)) + 'px';
  tipEl.style.top = (b.bottom + 6 + tipEl.offsetHeight > innerHeight ? b.top - 6 - tipEl.offsetHeight : b.bottom + 6) + 'px';
}
function showTip(target, focus) {
  const found = tipFor(target, focus);
  if (!found) return;
  tipAnchor = found[0]; tipEl.textContent = found[1]; tipEl.classList.add('on'); tipEl.dataset.focus = focus ? '1' : '';
  placeTip();
}
function hideTip() { tipAnchor = null; tipEl.classList.remove('on'); }
document.addEventListener('mouseover', e => { if (e.target.closest) showTip(e.target, false); });
document.addEventListener('mouseout', e => { if (!tipEl.dataset.focus) hideTip(); });
document.addEventListener('focusin', e => { if (e.target.matches && e.target.matches(':focus-visible')) showTip(e.target, true); });
document.addEventListener('focusout', hideTip);
// Focusing a control can scroll it into view: keep a focus tooltip attached, drop a hover one.
document.addEventListener('scroll', () => { if (tipEl.dataset.focus) placeTip(); else hideTip(); }, true);
document.addEventListener('keydown', e => { if (e.key === 'Escape') hideTip(); });

let saved = null; try { saved = localStorage.getItem('wfd-theme'); } catch (e) { /* private mode */ }
const theme = Q.get('theme') || saved;
if (theme === 'light' || theme === 'dark') document.documentElement.dataset.theme = theme;
poll();
setInterval(poll, +Q.get('poll') >= 1000 ? +Q.get('poll') : POLL_MS);
