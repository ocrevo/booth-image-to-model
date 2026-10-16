// The studio page: one window from picture to render. The editor (app.js, createEditor) lives in the stage; this file talks to the
// local server (/api), draws the step bar and the one card that says what to do next, keeps the model saved, and compares the model
// with the photo. What to show is decided in studio_logic.js; the words are in studio_copy.js.

import { createEditor } from './app.js';
import { COPY, fill } from './studio_copy.js';
import { deriveView } from './studio_logic.js';

const M = COPY.main;
const A = COPY.advanced;
const $ = (id) => document.getElementById(id);
const STEPS = ['scene', 'compile', 'preview', 'build', 'glb'];
const MAX_IMAGE_MB = 25;
const SAVE_DELAY_MS = 800;

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v === null || v === undefined) continue;
    if (k === 'class') el.className = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === 'value') el.value = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat(Infinity)) if (kid !== null && kid !== undefined && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return el;
}

function loadDurations() {
  try { return JSON.parse(localStorage.getItem('studio-durations')) || {}; } catch { return {}; }
}
function rememberDuration(step, seconds) {
  if (!(seconds > 0)) return;
  ui.durations[step] = seconds;
  try { localStorage.setItem('studio-durations', JSON.stringify(ui.durations)); } catch { /* storage may be blocked */ }
}

const freshCompare = () => ({ mode: 'wipe', after: 'live', split: 50 });
const freshSave = () => ({ dirty: false, state: 'idle', timer: null, inflight: null, lastJson: null });
const ui = {
  job: null, current: null, health: null, jobs: [], consent: false, key: '', hd: false, compare: freshCompare(),
  pro: { open: false, tab: 0 }, more: false, editorJob: null, timer: null, ticker: null, last: '', offline: false, notes: {}, done: {},
  save: freshSave(), knownSpec: null, loadingSpec: false, starting: false,
  durations: loadDurations(), view: null, spaceHold: null, editor: null,
};

// ---------------------------------------------------------------- server
async function call(method, path, { json, raw, type } = {}) {
  const init = { method, headers: {} };
  if (json !== undefined) { init.body = JSON.stringify(json); init.headers['Content-Type'] = 'application/json'; }
  if (raw !== undefined) { init.body = raw; init.headers['Content-Type'] = type || 'application/octet-stream'; }
  let res;
  try {
    res = await fetch(path, init);
  } catch {
    return { ok: false, status: 0, data: { error: M.offline } };
  }
  let data = null;
  try { data = await res.json(); } catch { /* not JSON */ }
  return { ok: res.ok, status: res.status, data };
}

const fileUrl = (rel, stamp = '') => `/api/jobs/${ui.job.id}/files/${rel}${stamp ? `?t=${encodeURIComponent(stamp)}` : ''}`;
const ctx = () => ({ health: ui.health, consent: ui.consent, durations: ui.durations, hd: ui.hd, now: Date.now() / 1000 });

async function refreshHealth() {
  const { ok, data } = await call('GET', '/api/health');
  ui.health = ok ? data : null;
}

async function refreshJobs() {
  const { ok, data } = await call('GET', '/api/jobs');
  ui.jobs = ok ? data.jobs : [];
  renderRecent();
}

/** Before leaving a job: write pending edits; if they cannot be written, ask. True when it is fine to leave. */
async function leaveJob() {
  if (!ui.save.dirty || !ui.job) return true;
  if (await flushSave()) return true;
  return window.confirm(M.discardEdits);
}

async function go(id, { force = false } = {}) {
  if (!force && !(await leaveJob())) return;
  ui.consent = false;                         // the consent is for one picture
  ui.notes = {};
  ui.done = {};
  ui.current = id || null;
  ui.job = null;
  ui.knownSpec = null;
  ui.editorJob = null;
  ui.compare = freshCompare();
  clearTimeout(ui.timer);
  clearTimeout(ui.save.timer);
  ui.save = freshSave();
  ui.editor.viewport.leaveSourceView();       // no frame, photo or render of the job we are leaving
  ui.editor.setRender(null);
  ui.editor.setCompare('model');
  const url = new URL(window.location.href);
  if (id) url.searchParams.set('job', id); else url.searchParams.delete('job');
  window.history.replaceState({}, '', url);
  if (id) loadJob(id); else render();
  refreshJobs();
}

async function loadJob(id, { quiet = false } = {}) {
  ui.current = id;
  clearTimeout(ui.timer);
  const { ok, status, data } = await call('GET', `/api/jobs/${encodeURIComponent(id)}`);
  if (ui.current !== id) return;                            // the user moved on while this request was out
  if (status === 0) {                                       // the server is away: say so and keep trying
    ui.offline = true;
    renderChips();
    ui.timer = setTimeout(() => loadJob(id, { quiet: true }), 3000);
    return;
  }
  if (ui.offline) { ui.offline = false; await refreshHealth(); renderChips(); }
  if (!ok) { await go(null, { force: true }); $('message').textContent = fill(M.jobNotFound, { id }); return; }
  const previous = ui.job;
  ui.job = data;
  onStatus(previous, data);
  document.body.dataset.job = data.id;
  const signature = JSON.stringify(data);
  if (!quiet || signature !== ui.last) { ui.last = signature; render(); }
  await syncEditor();
  if (!data.busy && ui.save.dirty && ui.save.state === 'wait') flushSave();
  const working = Boolean(data.busy) || Object.values(data.steps).some((s) => s.state === 'running' || s.state === 'queued');
  document.body.dataset.busy = String(working);
  if (working) ui.timer = setTimeout(() => loadJob(id, { quiet: true }), 1500);
}

/** What changed since the last status: remember how long steps took, and show the newest picture when a render finishes. */
function onStatus(previous, next) {
  if (!previous || previous.id !== next.id) return;
  for (const step of STEPS) {
    const was = previous.steps[step].state;
    const now = next.steps[step].state;
    if (was !== 'done' && now === 'done') {
      const hdBuild = step === 'build' && next.steps.build.detail?.hd_requested;      // a high-definition render would skew the usual time
      if (was === 'running' && !hdBuild) rememberDuration(step, next.steps[step].seconds);
      if (step === 'preview' || step === 'build') {                                   // show the newest picture, in a way that can show it
        ui.compare.after = step === 'preview' ? 'check' : 'final';
        if (step === 'build' || ui.compare.mode === 'model' || ui.compare.mode === 'photo') ui.compare.mode = 'wipe';
      }
    }
  }
}

// ---------------------------------------------------------------- actions
function saveBlocker() {
  if (ui.save.state === 'failed') return ui.notes.save || M.failChip;
  if (ui.save.state === 'offline') return M.offlineChip;
  return M.waitChip;
}

async function startRun(list, extra = {}) {
  if (!ui.job || ui.starting) return;
  ui.starting = true;
  try {
    ui.notes.card = null;
    if (!(await flushSave())) { ui.notes.card = { error: saveBlocker() }; render(); return; }
    const body = { steps: list, hd: ui.hd, ...extra };
    if (list.includes('scene')) Object.assign(body, { mode: 'openai', consent: ui.consent });
    const res = await call('POST', `/api/jobs/${ui.job.id}/run`, { json: body });
    if (!res.ok) ui.notes.card = res.data || { error: `HTTP ${res.status}` };
    await loadJob(ui.job.id);
  } finally {
    ui.starting = false;
  }
}

const actions = {
  generate: () => startRun(['scene', 'compile']),
  compileAgain: () => startRun(['compile']),
  generateAll: () => startRun(['scene', 'compile', 'build', 'glb']),
  render: () => startRun(['build', 'glb']),
  quickCheck: () => startRun(['preview']),
  exportGlb: () => startRun(['glb']),
  moreWays: () => { ui.more = true; render(); $('stepCard').querySelector('details')?.scrollIntoView({ block: 'nearest' }); },
  proData: () => openPro(1),
};

async function uploadImage(file) {
  ui.notes.image = null;
  if (file.size > MAX_IMAGE_MB * 1024 * 1024) { ui.notes.image = fill(M.imageTooBig, { mb: MAX_IMAGE_MB }); render(); return; }
  const res = await call('POST', `/api/jobs?name=${encodeURIComponent(file.name)}`, { raw: await file.arrayBuffer(), type: file.type || 'application/octet-stream' });
  if (!res.ok) { ui.notes.image = res.data?.error || `HTTP ${res.status}`; render(); return; }
  go(res.data.id);
}

/** The demo picture: demo/demo_source.png (a synthetic render, docs/sample-images.md), or a plain
 *  stand-in at the demo camera's frame size when the file is missing. */
async function demoPicture(source) {
  if (ui.health?.demo_picture) {                       // the server says whether it exists, so a missing file is never requested
    const res = await fetch('demo/demo_source.png');
    if (res.ok) return { name: 'demo_source.png', bytes: await res.arrayBuffer() };
  }
  const canvas = document.createElement('canvas');
  canvas.width = source.width;
  canvas.height = source.height;
  const g = canvas.getContext('2d');
  g.fillStyle = '#d6dade';
  g.fillRect(0, 0, canvas.width, canvas.height);
  g.fillStyle = '#5a6068';
  g.font = '20px sans-serif';
  g.fillText(M.demoPicture, 24, 40);
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));
  return { name: 'demo_placeholder.png', bytes: await blob.arrayBuffer() };
}

/** The synthetic demo booth (web/app/demo): its model on its picture, so the whole studio can be tried without a key. */
async function tryDemo() {
  ui.notes.card = null;
  try {
    const spec = await (await fetch('demo/demo-booth.spec.json')).json();
    const picture = await demoPicture(spec.source);
    const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', picture.bytes));
    spec.source = { ...spec.source, image: picture.name, sha256: Array.from(digest, (b) => b.toString(16).padStart(2, '0')).join('') };
    const job = await call('POST', `/api/jobs?name=${picture.name}`, { raw: picture.bytes, type: 'image/png' });
    if (!job.ok) throw new Error(job.data?.error || `HTTP ${job.status}`);
    const put = await call('PUT', `/api/jobs/${job.data.id}/spec?origin=uploaded`, { json: spec });
    if (!put.ok) throw new Error(put.data?.error || `HTTP ${put.status}`);
    await go(job.data.id);
  } catch (e) {
    ui.notes.card = { error: String(e.message || e) };
    render();
  }
}

async function uploadJson(kind, file) {
  ui.notes.more = null;
  ui.done.more = null;
  let body;
  try { body = JSON.parse(await file.text()); } catch (e) { ui.notes.more = fill(A.uploadRefused, { name: file.name, msg: e.message }); render(); return; }
  const path = kind === 'scene' ? 'scene' : 'spec?origin=uploaded';
  const res = await call('PUT', `/api/jobs/${ui.job.id}/${path}`, { json: body });
  if (!res.ok) { ui.notes.more = fill(A.uploadRefused, { name: file.name, msg: res.data?.error || `HTTP ${res.status}` }); render(); return; }
  ui.done.more = fill(kind === 'scene' ? A.uploaded : A.uploadedSpec, { name: file.name, n: res.data.components });
  if (kind === 'scene') await startRun(['compile']);
  else await loadJob(ui.job.id);
}

async function saveKey() {
  const res = await call('POST', '/api/config', { json: { openai_api_key: ui.key } });
  ui.key = '';
  ui.notes.key = res.ok ? null : res.data?.error || `HTTP ${res.status}`;
  await refreshHealth();
  render();
}

async function openBlender() {
  ui.notes.opened = null;
  const res = await call('POST', `/api/jobs/${ui.job.id}/open-blender`, { json: {} });
  if (res.ok) ui.notes.opened = M.openedBlender; else ui.notes.card = res.data;
  render();
}

// ---------------------------------------------------------------- keeping the model saved
function scheduleSave() {
  if (!ui.job) return;
  ui.save.dirty = true;
  ui.save.state = ui.job.busy ? 'wait' : 'saving';
  ui.notes.save = null;
  renderChips();
  clearTimeout(ui.save.timer);
  ui.save.timer = setTimeout(flushSave, SAVE_DELAY_MS);
}

/** Write the edited model to the job. Resolves true when nothing is left unsaved (or nothing needed saving). */
async function flushSave() {
  clearTimeout(ui.save.timer);
  if (ui.save.inflight) await ui.save.inflight;                                     // never two writes at once
  if (!ui.save.dirty || !ui.job) return true;
  if (ui.job.busy) { ui.save.state = 'wait'; renderChips(); return false; }         // the server refuses edits while a step runs; saved when it ends
  const id = ui.job.id;
  const sent = JSON.stringify(ui.editor.state.spec);
  if (sent === ui.save.lastJson) {                                                  // changed and changed back: nothing to write, and a write would make a finished render stale
    ui.save.dirty = false;
    ui.save.state = 'idle';
    ui.editor.markClean();
    renderChips();
    return true;
  }
  ui.save.state = 'saving';
  renderChips();
  const attempt = (async () => {
    const res = await call('PUT', `/api/jobs/${id}/spec?origin=edited`, { raw: sent, type: 'application/json' });
    if (ui.current !== id) return false;
    if (res.ok) {
      ui.save.lastJson = sent;
      if (JSON.stringify(ui.editor.state.spec) !== sent) { scheduleSave(); return false; }    // changed again while this was on its way
      ui.save.dirty = false;
      ui.save.state = 'saved';
      ui.editor.markClean();
      renderChips();
      await loadJob(id, { quiet: true });
      return true;
    }
    if (res.status === 409) { ui.save.state = 'wait'; renderChips(); return false; }
    if (res.status === 0) {                                                         // the server is away: not a refused edit, try again
      ui.save.state = 'offline';
      renderChips();
      ui.save.timer = setTimeout(flushSave, 3000);
      return false;
    }
    ui.save.state = 'failed';
    const first = (res.data?.errors || [])[0];
    ui.notes.save = fill(M.saveFailed, { msg: first ? `${first.code || ''} ${first.where || first.path || ''} ${first.message || ''}`.trim() : res.data?.error || `HTTP ${res.status}` });
    renderChips();
    render();
    return false;
  })();
  ui.save.inflight = attempt;
  try {
    return await attempt;
  } finally {
    if (ui.save.inflight === attempt) ui.save.inflight = null;
  }
}

// ---------------------------------------------------------------- the editor and the model on the stage
async function syncEditor() {
  const job = ui.job;
  if (!job || !job.files?.spec || ui.loadingSpec) return;
  const loaded = ui.editorJob === job.id;
  if (loaded && job.spec.origin === 'edited') { ui.knownSpec = job.spec.updated; return; }  // our own saves: the editor already has them
  if (loaded && job.spec.updated === ui.knownSpec) return;
  ui.loadingSpec = true;
  try {
    const res = await call('GET', `/api/jobs/${job.id}/spec`);
    if (!res.ok || ui.current !== job.id) return;
    ui.editor.loadSpec(res.data, `${job.id}.spec.json`);
    ui.editor.say(fill(M.modelLoaded, { n: res.data.components.length }), 'ok');
    ui.knownSpec = job.spec.updated;
    ui.editorJob = job.id;
    ui.save = freshSave();
    ui.save.lastJson = JSON.stringify(ui.editor.state.spec);
    ui.compare = freshCompare();
    const v = deriveView(job, ctx());
    ui.compare.after = v.compare.final ? 'final' : v.compare.check ? 'check' : 'live';
    render();
  } finally {
    ui.loadingSpec = false;
  }
}

function applyCompare() {
  const view = ui.view;
  const editor = ui.editor;
  if (!view || view.phase !== 'model' || !ui.knownSpec) return;
  const c = ui.compare;
  let after = c.after;
  if (after === 'check' && !view.compare.check) after = 'live';
  if (after === 'final' && !view.compare.final) after = 'live';
  let url = null;
  if (c.mode === 'wipe' || c.mode === 'overlay') {
    if (after === 'check') url = fileUrl(view.compare.check, ui.job.steps.preview.updated);
    if (after === 'final') {
      const rel = c.mode === 'wipe' ? view.compare.final.wipe : view.compare.final.overlay;       // over the photo only a transparent render works
      url = rel ? fileUrl(rel, ui.job.steps.build.updated) : null;
      if (!rel) after = 'live';
    }
  }
  editor.setRender(url);
  const applied = editor.setCompare(c.mode, c.split);
  if (applied !== c.mode) c.mode = applied;
  c.effectiveAfter = after;
  c.rendering = Boolean(url);
  updateCompareBar();
}

function onFrame(rect) {
  if (rect === null && ui.compare.mode !== 'model') { ui.compare.mode = 'model'; ui.compare.rendering = false; updateCompareBar(); }
}

function updateCompareBar() {
  const view = ui.view;
  if (!view) return;
  const c = ui.compare;
  for (const b of $('viewSeg').children) b.setAttribute('aria-pressed', String(b.dataset.mode === c.mode));
  $('splitSlider').hidden = c.mode !== 'wipe';
  if (document.activeElement !== $('splitSlider')) $('splitSlider').value = String(c.split);
  for (const b of $('afterSeg').children) {
    const available = b.dataset.after === 'live' || (b.dataset.after === 'check' && view.compare.check) || (b.dataset.after === 'final' && view.compare.final);
    b.disabled = !available;
    b.setAttribute('aria-pressed', String(b.dataset.after === (c.effectiveAfter || c.after)));
  }
  $('afterSeg').hidden = c.mode === 'model' || c.mode === 'photo';
  $('compareNote').textContent = c.rendering ? M.renderNote : c.mode === 'model' ? '' : `${M.liveNote} · ${M.holdSpace}`;
}

function initStatic() {
  $('appName').textContent = M.appName;
  $('btnNew').textContent = M.newJob;
  $('btnPro').textContent = M.pro;
  $('dropTitle').textContent = M.drop;
  $('dropSmall').textContent = M.dropSmall;
  $('partsTitle').textContent = M.parts;
  $('filter').placeholder = M.filter;
  $('snapLabel').textContent = M.tools.snap;
  for (const [id, key] of [['btnMove', 'move'], ['btnRotate', 'rotate'], ['btnDup', 'dup'], ['btnDel', 'del'], ['btnUndo', 'undo'], ['btnRedo', 'redo']]) $(id).textContent = M.tools[key];
  const modes = [['photo', 0], ['wipe', 1], ['overlay', 2], ['model', 3]];
  $('viewSeg').replaceChildren(...modes.map(([mode, i]) => h('button', {
    'data-mode': mode, id: `view-${mode}`, onClick: () => {
      ui.compare.mode = mode;
      if (mode === 'model') ui.editor.viewport.frame(ui.editor.state.spec); else applyCompare();
      updateCompareBar();
      refocusStage();
    },
  }, M.compareName[i])));
  $('afterSeg').replaceChildren(...['live', 'check', 'final'].map((after, i) => h('button', {
    'data-after': after, id: `after-${after}`, onClick: () => { ui.compare.after = after; if (ui.compare.mode === 'model') ui.compare.mode = 'wipe'; applyCompare(); refocusStage(); },
  }, M.afterName[i])));
  $('splitSlider').addEventListener('input', (e) => { ui.compare.split = Number(e.target.value); ui.editor.viewport.setCompare(ui.compare.mode, ui.compare.split); });
  $('splitSlider').addEventListener('change', refocusStage);
  $('btnNew').addEventListener('click', () => go(null));
  $('stage').addEventListener('pointerdown', (e) => { if (e.target.closest('.st-compare, .st-tools, .st-drop')) return; refocusStage(); });
  $('btnPro').addEventListener('click', () => (ui.pro.open ? closePro() : openPro(ui.pro.tab)));
  $('btnProClose').addEventListener('click', closePro);
  $('imageInput').addEventListener('change', (e) => { const f = e.target.files[0]; e.target.value = ''; if (f) uploadImage(f); });

  const stage = $('stage');
  stage.addEventListener('dragover', (e) => { e.preventDefault(); if (ui.view?.phase === 'empty') $('dropZone').classList.add('over'); });
  stage.addEventListener('dragleave', () => $('dropZone').classList.remove('over'));
  stage.addEventListener('drop', (e) => {
    e.preventDefault();
    $('dropZone').classList.remove('over');
    const f = e.dataTransfer?.files?.[0];
    if (f && ui.view?.phase === 'empty') uploadImage(f);
  });
  for (const type of ['dragover', 'drop']) window.addEventListener(type, (e) => e.preventDefault());       // a file dropped beside the stage must not navigate away

  // Hold the space bar to look at the photo alone. Anywhere except a text field, a button or the advanced view: after a click on the
  // comparison controls the stage is given the focus back, so the bar works right after them too.
  document.addEventListener('keydown', (e) => {
    if (e.code !== 'Space' || ui.view?.phase !== 'model') return;
    const target = e.target;
    if (['INPUT', 'TEXTAREA', 'SELECT', 'BUTTON', 'A', 'SUMMARY'].includes(target.tagName) || target.isContentEditable || target.closest?.('#pro, dialog')) return;
    e.preventDefault();
    if (!e.repeat && !ui.spaceHold && ui.compare.mode !== 'model' && ui.compare.mode !== 'photo') { ui.spaceHold = ui.compare.mode; ui.editor.setCompare('photo'); }
  });
  document.addEventListener('keyup', (e) => {
    if (e.code === 'Space' && ui.spaceHold) { const back = ui.spaceHold; ui.spaceHold = null; ui.editor.setCompare(back, ui.compare.split); }
  });
}

function refocusStage() {
  const stage = $('stage');
  if (document.activeElement && document.activeElement !== stage) document.activeElement.blur?.();
  stage.focus({ preventScroll: true });
}

// ---------------------------------------------------------------- drawing
function render() {
  const view = deriveView(ui.job, ctx());
  ui.view = view;
  document.body.dataset.phase = view.phase;
  renderSteps(view);
  renderStage(view);
  renderCard(view);
  renderChips();
  if (ui.pro.open) renderPro();
  applyCompare();
  updateCompareBar();
  startTicker(view);
}

function renderSteps(view) {
  $('steps').replaceChildren(...view.bar.map((b) => h('span', {
    class: 'st-step', 'data-state': b.state, 'data-n': b.n, ...(b.n === view.current ? { 'aria-current': 'step' } : {}),
  }, h('span', { class: 'dot' }, b.state === 'done' ? '✓' : b.state === 'error' ? '!' : b.n), b.label)));
}

function renderStage(view) {
  const empty = view.phase === 'empty';
  const model = view.phase === 'model';
  $('dropZone').hidden = !empty;
  $('recent').hidden = !empty || !ui.jobs.length;
  $('dropSmall').textContent = ui.notes.image || M.dropSmall;
  $('dropSmall').style.color = ui.notes.image ? 'var(--bad)' : '';
  const plain = $('photoPlain');
  plain.hidden = view.phase !== 'photo';
  if (view.phase === 'photo') { const src = fileUrl(ui.job.image.name); if (plain.getAttribute('src') !== src) plain.src = src; }
  $('stageTools').hidden = !model;
  $('compareBar').hidden = !model;
  $('outlinePanel').hidden = !model;
  $('inspector').hidden = !model;
  $('studioMain').classList.toggle('no-outline', !model);
  $('btnNew').hidden = empty;
  $('photoName').replaceChildren(...(ui.job ? [h('span', { class: 'nm', title: ui.job.image.original_name }, ui.job.image.original_name),
    h('span', { class: 'sz' }, fill(M.imageInfo, { w: ui.job.image.width, h: ui.job.image.height }))] : []));
}

function renderRecent() {
  const box = $('recent');
  box.replaceChildren(h('span', { class: 'st-recent-title' }, M.recent), ...ui.jobs.slice(0, 6).map((j) => h('button', {
    type: 'button', title: j.id, onClick: (e) => { e.preventDefault(); e.stopPropagation(); go(j.id); },
  }, h('img', { src: `/api/jobs/${j.id}/files/${j.file}`, alt: '', loading: 'lazy' }), h('span', {}, j.image))));
  box.hidden = ui.view?.phase !== 'empty' || !ui.jobs.length;
}

function renderChips() {
  const save = $('saveChip');
  const show = ui.view?.phase === 'model' && ui.save.state !== 'idle';
  save.hidden = !show;
  if (show) {
    const [text, tone] = { saved: [M.savedChip, 'good'], saving: [M.savingChip, ''], wait: [M.waitChip, 'warn'], offline: [M.offlineChip, 'warn'], failed: [M.failChip, 'bad'] }[ui.save.state] || ['', ''];
    save.textContent = text;
    save.className = `st-chip ${tone}`;
    save.dataset.state = ui.save.state;
  }
  const env = $('envChip');
  env.hidden = !ui.offline;
  env.textContent = ui.offline ? M.offline : '';
}

function errorLine(note) {
  if (!note) return null;
  const text = typeof note === 'string' ? note : note.error || note.message || '';
  return text ? h('p', { class: 'st-warn', role: 'alert' }, text) : null;
}

function actionButton(spec, big = true, disabled = false) {
  return h('button', { id: big ? 'btnRetry' : undefined, class: big ? 'big primary' : 'link', disabled, onClick: actions[spec.action] }, spec.label);
}

function renderCard(view) {
  const card = view.card;
  const root = $('stepCard');
  const kids = [];
  if (card.kind === 'empty') {
    kids.push(h('h2', {}, M.emptyTitle), h('p', { class: 'muted' }, M.emptyBody),
      h('button', { id: 'btnTryDemo', onClick: tryDemo }, M.tryDemo), h('p', { class: 'muted' }, M.tryDemoHint));
  } else if (card.kind === 'generate') {
    kids.push(...generateCard(view));
  } else if (card.kind === 'working') {
    kids.push(h('h2', { id: 'workingText' }, card.text), h('div', { class: 'st-bar-progress', role: 'progressbar', 'aria-label': card.text }, h('i')),
      card.progress && h('p', { class: 'muted', id: 'progressText' }, card.progress), card.slow && h('p', { class: 'st-warn' }, M.slow));
  } else if (card.kind === 'error') {
    const reading = card.primary.action === 'generate';                  // a new reading needs the key and the consent again, right here
    kids.push(h('div', { class: 'st-error', role: 'alert' }, h('h2', {}, card.title), card.reason && h('p', { class: 'reason', id: 'errorReason' }, card.reason),
      reading && keyAndConsent(card.needKey),
      actionButton(card.primary, true, !card.canRetry), reading && !card.canRetry && !card.needKey && h('p', { class: 'muted' }, M.needConsent),
      card.secondary.map((s) => actionButton(s, false)),
      card.raw && h('details', {}, h('summary', {}, M.rawLog), h('p', { class: 'reason' }, card.raw))));
    kids.push(moreWays(view));                                           // the way out of a failed reading: upload a file, or change the key
  } else {
    kids.push(...modelCard(view));
  }
  kids.push(errorLine(ui.notes.card));
  root.replaceChildren(...kids.filter(Boolean));
}

/** The key (when there is none yet) and the consent for sending this picture: what a new reading needs. */
function keyAndConsent(needKey) {
  const out = [];
  if (needKey) {
    out.push(h('p', { class: 'muted' }, M.needKey), h('div', { class: 'st-row' },
      h('input', { type: 'password', id: 'keyInput', placeholder: M.keyPlaceholder, autocomplete: 'off', value: ui.key, onInput: (e) => { ui.key = e.target.value; } }),
      h('button', { id: 'btnSaveKey', onClick: saveKey }, M.saveKey)), errorLine(ui.notes.key));
  }
  out.push(h('label', { class: 'check' }, h('input', { type: 'checkbox', id: 'chkConsent', checked: ui.consent, onChange: (e) => { ui.consent = e.target.checked; render(); } }),
    h('span', {}, fill(M.consent, { model: ui.health?.openai?.model || '' }))));
  return out;
}

function generateCard(view) {
  const card = view.card;
  const out = [h('h2', {}, M.stepNames[1]), ...keyAndConsent(card.needKey),
    h('button', { id: 'btnGenerate', class: 'big primary', disabled: !card.canGenerate, onClick: actions.generate }, M.generate)];
  if (view.blender) out.push(h('button', { id: 'btnGenerateAll', disabled: !card.canGenerateAll, onClick: actions.generateAll }, M.generateAll));
  out.push(h('p', { class: 'muted' }, M.generateHint), moreWays(view));
  return out;
}

function moreWays(view) {
  return h('details', { id: 'moreWays', ...(ui.more ? { open: true } : {}), onToggle: (e) => { ui.more = e.target.open; } },
    h('summary', {}, A.moreWays),
    h('div', {},
      view.keyConfigured && h('label', {}, A.changeKey, h('div', { class: 'st-row' },
        h('input', { type: 'password', id: 'keyInput', autocomplete: 'off', value: ui.key, onInput: (e) => { ui.key = e.target.value; } }),
        h('button', { id: 'btnSaveKey', onClick: saveKey }, M.saveKey))),
      errorLine(ui.notes.key),
      h('label', {}, A.uploadScene, h('input', { type: 'file', id: 'sceneInput', accept: '.json,application/json', onChange: (e) => { const f = e.target.files[0]; if (f) uploadJson('scene', f); } })),
      h('label', {}, A.uploadSpec, h('input', { type: 'file', id: 'specInput', accept: '.json,application/json', onChange: (e) => { const f = e.target.files[0]; if (f) uploadJson('spec', f); } })),
      ui.done.more && h('p', { class: 'muted', id: 'moreDone', role: 'status' }, `✓ ${ui.done.more}`), errorLine(ui.notes.more)));
}

function modelCard(view) {
  const out = [];
  const lines = view.card.fit || [];
  for (const line of lines) {
    out.push(h('div', { class: `st-line ${line.tone}` }, h('span', {}, line.text), line.look && h('button', { class: 'link', onClick: () => openPro(line.look === 'align' ? 0 : 1) }, '→')));
  }
  if (!lines.length) out.push(h('p', { class: 'muted' }, M.modelReady));
  const notice = view.card.notice;
  if (notice) {                                                         // a step that failed beside a good model: said here, nothing else is taken away
    out.push(h('div', { class: 'st-warn', role: 'alert', id: 'sideNotice', 'data-step': notice.step }, h('strong', {}, notice.title), h('p', {}, notice.reason),
      h('div', { class: 'st-row' }, [notice.primary, ...notice.secondary].map((a) => actionButton(a, false))),
      notice.raw && h('details', {}, h('summary', {}, M.rawLog), h('p', { class: 'reason' }, notice.raw))));
  }
  if (ui.notes.save) {
    out.push(h('div', { class: 'st-warn', role: 'alert' }, h('p', {}, ui.notes.save), h('button', { id: 'btnUndoStep', class: 'link', onClick: () => { ui.notes.save = null; ui.editor.undo(); } }, M.undoStep)));
  }
  if (view.render.stale) out.push(h('p', { class: 'st-warn', id: 'staleNote' }, M.stale));
  if (view.blender) {
    out.push(h('button', { id: 'btnRender', class: 'big primary', onClick: actions.render }, view.render.built || view.render.stale ? M.renderAgain : M.render));
    if (view.render.hdAllowed) out.push(h('label', { class: 'check' }, h('input', { type: 'checkbox', id: 'chkHd', checked: ui.hd, onChange: (e) => { ui.hd = e.target.checked; } }), h('span', {}, M.hd)));
    out.push(h('button', { id: 'btnQuickCheck', class: 'link', onClick: actions.quickCheck }, M.quickCheck));
  } else {
    out.push(h('p', { class: 'st-warn' }, M.noBlender));
  }
  if (!view.blender || !view.downloads.some((d) => d.key === 'glb')) {
    out.push(h('button', { id: 'btnExportGlb', disabled: !view.canExportGlb, onClick: actions.exportGlb }, M.exportGlb));
  }
  if (view.downloads.length) {
    out.push(h('h2', {}, M.downloads), h('div', { class: 'st-downloads', id: 'downloads' }, view.downloads.map((d) => h('a', {
      href: fileUrl(d.rel, ui.job.steps[d.key === 'glb' ? 'glb' : 'build'].updated), download: '', class: d.primary ? 'primary' : '', 'data-key': d.key,
    }, d.label, d.stale && h('span', { class: 'stale' }, M.staleTag)))));
    if (view.blender && view.downloads.some((d) => d.key === 'blend')) out.push(h('button', { id: 'btnOpenBlender', onClick: openBlender }, M.openBlender));
    if (ui.notes.opened) out.push(h('p', { class: 'muted', id: 'openedNote' }, ui.notes.opened));
  }
  return out;
}

// ---------------------------------------------------------------- the clock on a running step
function startTicker(view) {
  clearInterval(ui.ticker);
  document.title = M.appName;
  if (view.card.kind !== 'working') return;
  const tick = () => {
    const v = deriveView(ui.job, ctx());
    if (v.card.kind !== 'working') return;
    const text = $('progressText');
    if (text && v.card.progress) text.textContent = v.card.progress;
    document.title = `⏳ ${v.card.text} ${v.card.elapsed ? `${Math.floor(v.card.elapsed / 60)}:${String(Math.round(v.card.elapsed % 60)).padStart(2, '0')}` : ''} · ${M.appName}`;
  };
  tick();
  ui.ticker = setInterval(tick, 1000);
}

// ---------------------------------------------------------------- advanced view
function openPro(tab = 0) {
  ui.pro = { open: true, tab };
  $('pro').hidden = false;
  $('btnPro').setAttribute('aria-expanded', 'true');
  renderPro();
}

function closePro() {
  ui.pro.open = false;
  $('pro').hidden = true;
  $('btnPro').setAttribute('aria-expanded', 'false');
}

function renderPro() {
  $('proTabs').replaceChildren(...A.proTabs.map((label, i) => h('button', { role: 'tab', 'aria-selected': String(i === ui.pro.tab), onClick: () => { ui.pro.tab = i; renderPro(); } }, label)));
  const job = ui.job;
  const body = $('proBody');
  if (!job) { body.replaceChildren(h('p', { class: 'muted' }, A.noDetail)); return; }
  const compile = job.steps.compile.detail;
  const preview = job.steps.preview.detail;
  const list = (title, items, render) => (items && items.length ? h('div', {}, h('h3', {}, `${title} (${items.length})`), h('ul', {}, items.map((x) => h('li', {}, render(x))))) : null);
  if (ui.pro.tab === 0) {
    const fit = compile?.fit;
    body.replaceChildren(...[
      compile?.overlay && h('div', {}, h('h3', {}, A.overlayTitle), h('img', { src: fileUrl(compile.overlay, job.steps.compile.updated), alt: '' })),
      fit && h('p', { id: 'fitLine' }, fill(A.fitLine, { status: fit.status, rms: fit.rms_px, max: fit.max_px, n: compile.components })),
      compile?.points?.length && h('div', {}, h('h3', {}, A.pointsTitle), h('table', {}, compile.points.map((p) => h('tr', {}, h('td', {}, p.id), h('td', { class: p.err_px > 6 ? 'bad' : '' }, String(p.err_px)))))),
      preview?.sheet && h('div', {}, h('h3', {}, A.sheetTitle),
        h('div', { class: 'st-crop', style: `aspect-ratio:${job.image.width} / ${job.image.height};background-image:url('${fileUrl(preview.sheet, job.steps.preview.updated)}')` })),
      !compile && !preview && h('p', { class: 'muted' }, A.noDetail),
    ].filter(Boolean));
  } else {
    body.replaceChildren(...[
      list(A.droppedTitle, compile?.dropped, (x) => `${x.id}: ${x.reason}`),
      list(A.assumedTitle, compile?.assumed, (x) => `${x.id}.${x.what} = ${typeof x.value === 'object' ? JSON.stringify(x.value) : x.value}`),
      list(A.adjustTitle, compile?.adjustments, (x) => `${x.id}: ${x.what}`),
      list(A.warnTitle, compile?.warnings, (x) => String(x)),
      list(A.designTitle, preview?.design_findings, (x) => `${x.check}: ${x.message}`),
      h('div', {}, h('h3', {}, A.logTitle), h('ul', {}, STEPS.map((s) => {
        const e = job.steps[s];
        return h('li', {}, `${s}: ${e.state}${e.message ? ` · ${e.message}` : ''}${e.seconds != null ? ` · ${e.seconds} s` : ''}`,
          (e.detail?.log_tail || e.detail?.trace) && h('pre', {}, e.detail.log_tail || e.detail.trace));
      }))),
      h('div', {}, h('h3', {}, A.files), h('ul', {},
        job.files?.scene && h('li', {}, h('a', { href: fileUrl('scene.json'), target: '_blank', rel: 'noopener' }, 'scene.json')),
        job.files?.spec && h('li', {}, h('a', { href: fileUrl('spec.json'), download: '' }, 'spec.json')),
        preview?.sheet && h('li', {}, h('a', { href: fileUrl(preview.sheet), target: '_blank', rel: 'noopener' }, 'compare_sheet.png')))),
    ].filter(Boolean));
  }
}

// ---------------------------------------------------------------- start
async function boot() {
  initStatic();
  ui.editor = createEditor(document.body, {
    loadDemo: false, dropFiles: false, hotkeyTargets: [$('stage'), $('outlinePanel')],
    photoUrl: () => (ui.job ? fileUrl(ui.job.image.name) : null), onCommit: scheduleSave, onMatchFrame: onFrame,
  });
  await refreshHealth();
  await refreshJobs();
  const id = new URLSearchParams(window.location.search).get('job');
  if (id) await loadJob(id); else render();
  document.body.dataset.ready = 'true';
  window.studio = { ui, loadJob, render, go, editor: ui.editor, flushSave };
}

boot();
