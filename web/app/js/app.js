// Booth spec editor: UI, selection, edits and history. The spec object is the single source of truth;
// every change goes through spec.js (tryEdit), then the viewport is refreshed and a history step is stored.
//
// createEditor(root, options) starts it inside a page. The standalone page (index.html) starts it on <body>; the studio page
// (studio.html, <body data-editor="embedded">) starts it itself and hands in its own hooks. One editor per page.
//   options.standalone      the page is the editor: it sets the title, catches errors, takes dropped files
//   options.loadDemo        load the demo booth at start (default true)
//   options.hotkeyTargets   elements (or window) whose keydown drives the shortcuts (default: window)
//   options.dropFiles       accept a dropped spec.json (default: standalone only)
//   options.photoUrl(spec)  url of the source picture, drawn at the matched camera for before/after
//   options.onCommit(spec)  every time the spec changed (edit, undo, redo)
//   options.onMatchFrame(rect|null)  the camera entered (rect of the source picture in the stage) or left (null) the source view

import { Viewport } from './viewport.js';
import * as S from './spec.js';
import { t } from './i18n.js';

let root = document.body;
let opts = {};
/** Element by id inside the editor's root. Markup without a control (the studio has no "open file" button) gets a harmless stand-in. */
const $ = (id) => root.querySelector(`#${id}`) || document.createElement('span');
const fmt = (v) => (v === undefined || v === null ? '' : String(+Number(v).toFixed(4)));
const HISTORY_LIMIT = 100;
const DEMO_URL = 'demo/demo-booth.spec.json';

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v === null || v === undefined) continue;
    if (k === 'class') el.className = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2).toLowerCase(), v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat(Infinity)) if (kid !== null && kid !== undefined && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return el;
}

const state = {
  spec: null, fileName: 'booth.spec.json', isDemo: false, selection: [], mode: 'translate', snap: true, partOnly: false,
  history: [], at: -1, dirty: false, issues: [], gizmo: null, filter: '', collapsed: new Set(), allCollapsed: true,
};
let viewport;
let bindings = [];
const partRows = new Map();
const groupRows = new Map();

// ---------------------------------------------------------------- messages and toolbar
function say(text, kind = '') {
  const el = $('message');
  el.textContent = text;
  el.className = `message ${kind}`;
}

function updateToolbar() {
  $('btnUndo').disabled = state.at <= 0;
  $('btnRedo').disabled = state.at >= state.history.length - 1;
  $('btnDup').disabled = $('btnDel').disabled = state.selection.length === 0;
  $('btnMove').setAttribute('aria-pressed', String(state.mode === 'translate'));
  $('btnRotate').setAttribute('aria-pressed', String(state.mode === 'rotate'));
  $('btnMatch').setAttribute('aria-pressed', String(Boolean(viewport?.matching)));
  $('fileName').textContent = `${state.isDemo ? t('demoName') : state.fileName}${state.dirty ? ' •' : ''}`;
}

function renderIssues() {
  const n = state.issues.length;
  const btn = $('btnIssues');
  btn.textContent = n ? t('issueCount', { n }) : t('noIssues');
  btn.className = `issues ${n ? 'bad' : ''}`;
  const list = $('issueList');
  list.replaceChildren(...state.issues.map((issue) => h('button', {
    onClick: () => {
      const c = S.findComponent(state.spec, String(issue.where).split('.')[0]);
      if (c) select(S.expandToGroups(state.spec, [c.id], true));
      list.hidden = true;
    },
  }, h('span', { class: 'code' }, issue.code), `${issue.where}: ${issue.message}`)));
  if (!n) list.hidden = true;
}

function runChecks() {
  state.issues = S.lightValidate(state.spec);
  renderIssues();
}

// ---------------------------------------------------------------- history
function commit() {
  const json = JSON.stringify(state.spec);
  if (json === state.history[state.at]) return;
  state.history = state.history.slice(0, state.at + 1);
  state.history.push(json);
  if (state.history.length > HISTORY_LIMIT) state.history.shift();
  state.at = state.history.length - 1;
  state.dirty = true;
  runChecks();
  updateToolbar();
  opts.onCommit?.(state.spec);
}

function restore(index) {
  state.at = index;
  state.spec = JSON.parse(state.history[index]);
  state.dirty = index !== 0 || state.dirty;
  viewport.loadSpec(state.spec);
  state.selection = state.selection.filter((id) => S.findComponent(state.spec, id));
  viewport.setSelection(state.spec, state.selection);
  renderOutline();
  renderInspector();
  runChecks();
  updateToolbar();
  opts.onCommit?.(state.spec);
}

const undo = () => state.at > 0 && restore(state.at - 1);
const redo = () => state.at < state.history.length - 1 && restore(state.at + 1);

// ---------------------------------------------------------------- loading and saving
function loadSpec(spec, name, isDemo = false) {
  state.spec = spec;
  state.fileName = name;
  state.isDemo = isDemo;
  state.selection = [];
  state.history = [JSON.stringify(spec)];
  state.at = 0;
  state.dirty = false;
  state.collapsed = new Set(S.groupNames(spec));
  viewport.loadSpec(spec);
  viewport.setSelection(spec, []);
  viewport.setPhoto(opts.photoUrl?.(spec) ?? null);
  viewport.frame(spec);
  renderOutline();
  renderInspector();
  runChecks();
  updateToolbar();
  say(t('loaded', { name: isDemo ? t('demoName') : name, n: spec.components.length }), state.issues.length ? 'error' : 'ok');
  root.dataset.parts = String(spec.components.length);
}

async function loadDemo() {
  try {
    const res = await fetch(DEMO_URL);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    loadSpec(await res.json(), 'demo-booth.spec.json', true);
    root.dataset.ready = 'true';
  } catch (e) {
    say(t('serveHint'), 'error');
    root.dataset.ready = 'failed';
  }
}

async function openFile(file) {
  try {
    const spec = JSON.parse(await file.text());
    if (!spec || !Array.isArray(spec.components)) throw new Error('no components array');
    loadSpec(spec, file.name);
  } catch (e) {
    say(t('badFile', { msg: e.message }), 'error');
  }
}

function download(blob, name) {
  const url = URL.createObjectURL(blob);
  const a = h('a', { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

function saveSpec() {
  const base = state.fileName.replace(/\.json$/i, '').replace(/\.spec$/i, '').replace(/\.edited$/i, '');
  const name = `${base}.edited.spec.json`;
  download(new Blob([`${JSON.stringify(state.spec, null, 2)}\n`], { type: 'application/json' }), name);
  state.dirty = false;
  updateToolbar();
  say(t('saved', { name }), 'ok');
}

async function exportGlb() {
  try {
    const buffer = await viewport.exportGlb(state.spec);
    const name = `${state.spec.project.id || 'booth'}.glb`;
    download(new Blob([buffer], { type: 'model/gltf-binary' }), name);
    say(t('exported', { name, kb: Math.round(buffer.byteLength / 1024) }), 'ok');
  } catch (e) {
    say(t('refused', { msg: e.message }), 'error');
  }
}

// ---------------------------------------------------------------- selection
function select(ids) {
  state.selection = ids;
  viewport.setSelection(state.spec, ids);
  updateOutlineSelection();
  renderInspector();
  updateToolbar();
}

function onPick(id, { additive, partOnly }) {
  if (!id) {
    if (!additive) select([]);
    return;
  }
  let ids = S.expandToGroups(state.spec, [id], partOnly || state.partOnly);
  if (additive) {
    const current = new Set(state.selection);
    const allIn = ids.every((i) => current.has(i));
    ids.forEach((i) => (allIn ? current.delete(i) : current.add(i)));
    ids = [...current];
  }
  select(ids);
}

// ---------------------------------------------------------------- edits
function edit(fn, refresh = state.selection) {
  const footprint = state.spec.scale.footprint_m.join();
  const result = S.tryEdit(state.spec, fn);
  if (!result.ok) {
    say(t('refused', { msg: result.error }), 'error');
    refreshInspectorValues();
    return false;
  }
  viewport.updateParts(state.spec, refresh);
  if (footprint !== state.spec.scale.footprint_m.join()) say(t('footprint', { w: state.spec.scale.footprint_m[0], d: state.spec.scale.footprint_m[1] }), 'ok');
  commit();
  refreshInspectorValues();
  return true;
}

/** Edits that change which parts exist or how they are grouped: reload everything. */
function structural(fn, after = () => state.selection) {
  const result = S.tryEdit(state.spec, fn);
  if (!result.ok) {
    say(t('refused', { msg: result.error }), 'error');
    return false;
  }
  viewport.loadSpec(state.spec);
  renderOutline();
  select(after().filter((id) => S.findComponent(state.spec, id)));
  commit();
  return true;
}

/** Live preview while a colour or slider is dragged; the history step is stored by commitLive() on release. */
function liveEdit(fn, refresh) {
  const result = S.tryEdit(state.spec, fn);
  if (result.ok) {
    viewport.updateParts(state.spec, refresh);
    refreshInspectorValues();
  }
  return result.ok;
}
const commitLive = () => commit();

function duplicateSelection() {
  if (!state.selection.length) return say(t('nothingToDo'));
  const ids = [...state.selection];
  let created = [];
  if (structural((s) => { created = S.duplicate(s, ids); return created; }, () => created)) say(t('duplicated', { n: created.length }), 'ok');
}

function deleteSelection() {
  if (!state.selection.length) return say(t('nothingToDo'));
  const ids = [...state.selection];
  const critical = ids.filter((id) => S.findComponent(state.spec, id)?.critical);
  if (critical.length && !window.confirm(t('confirmCritical', { ids: critical.join(', ') }))) return;
  let summary;
  if (structural((s) => { summary = S.remove(s, ids, { force: critical.length > 0 }); }, () => [])) {
    const extra = summary.requirements || summary.landmarks || summary.scopes.length
      ? t('deletedExtra', { r: summary.requirements, l: summary.landmarks, s: summary.scopes.length }) : '';
    say(t('deleted', { n: summary.removed.length }) + extra, 'ok');
  }
}

function nudge(delta) {
  if (!state.selection.length) return;
  edit((s) => S.translate(s, state.selection, delta));
}

// gizmo -----------------------------------------------------------
const gizmoHooks = {
  onGizmoStart(info) {
    state.gizmo = { json: JSON.stringify(state.spec), ids: info.ids, snapshot: info.ids.map((id) => structuredClone(S.findComponent(state.spec, id))) };
  },
  onGizmoChange(info) {
    const g = state.gizmo;
    if (!g) return;
    for (const snap of g.snapshot) state.spec.components[state.spec.components.findIndex((c) => c.id === snap.id)] = structuredClone(snap);
    if (info.type === 'single') S.setTransform(state.spec, info.id, { location: info.location, rotation_deg: info.rotation_deg });
    else if (info.type === 'move') S.translate(state.spec, g.ids, info.delta);
    else S.rotateYaw(state.spec, g.ids, info.deg, info.pivot);
    viewport.syncTransforms(state.spec, g.ids, info.type === 'single' ? info.id : null);
    refreshInspectorValues();
  },
  onGizmoEnd() {
    const g = state.gizmo;
    state.gizmo = null;
    if (g && JSON.stringify(state.spec) !== g.json) commit();
  },
};

// ---------------------------------------------------------------- outline
function renderOutline() {
  const root = $('outlineList');
  root.replaceChildren();
  partRows.clear();
  groupRows.clear();
  const q = state.filter.trim().toLowerCase();
  const groups = new Map();
  const loose = [];
  for (const c of state.spec.components) {
    if (q && !(c.id.includes(q) || c.role.toLowerCase().includes(q) || (c.group || '').includes(q))) continue;
    if (c.group) {
      if (!groups.has(c.group)) groups.set(c.group, []);
      groups.get(c.group).push(c);
    } else loose.push(c);
  }
  const partRow = (c) => {
    const row = h('div', { class: 'row part' },
      h('button', { class: 'name', title: `${c.id} · ${c.role}`, onClick: (e) => onOutlineClick(e, [c.id], true) }, c.id),
      h('span', { class: 'kind' }, c.kind));
    partRows.set(c.id, row);
    return row;
  };
  for (const [name, list] of groups) {
    const open = q || !state.collapsed.has(name);
    const row = h('div', { class: 'row' },
      h('button', { class: 'chev', 'aria-label': 'toggle', onClick: () => { if (state.collapsed.has(name)) state.collapsed.delete(name); else state.collapsed.add(name); renderOutline(); updateOutlineSelection(); } }, open ? '▾' : '▸'),
      h('button', { class: 'name', onClick: (e) => onOutlineClick(e, S.groupMembers(state.spec, name), false) }, name),
      h('span', { class: 'count' }, t('parts', { n: list.length })));
    groupRows.set(name, row);
    root.append(row);
    if (open) list.forEach((c) => root.append(partRow(c)));
  }
  if (loose.length) {
    root.append(h('div', { class: 'hint' }, t('ungrouped')));
    loose.forEach((c) => root.append(partRow(c)));
  }
  updateOutlineSelection();
}

function onOutlineClick(event, ids, single) {
  const additive = event.ctrlKey || event.metaKey || event.shiftKey;
  let next = ids;
  if (additive) {
    const current = new Set(state.selection);
    const allIn = ids.every((i) => current.has(i));
    ids.forEach((i) => (allIn ? current.delete(i) : current.add(i)));
    next = [...current];
  }
  select(single && !additive ? ids : next);
}

function updateOutlineSelection() {
  const sel = new Set(state.selection);
  for (const [id, row] of partRows) row.classList.toggle('sel', sel.has(id));
  for (const [name, row] of groupRows) {
    const members = S.groupMembers(state.spec, name);
    const n = members.filter((m) => sel.has(m)).length;
    row.classList.toggle('sel', n === members.length && n > 0);
    row.classList.toggle('some', n > 0 && n < members.length);
  }
}

// ---------------------------------------------------------------- inspector
function refreshInspectorValues() {
  for (const fn of bindings) fn();
}

function field(label, ...controls) {
  return h('div', { class: label ? 'field' : 'field nolabel' }, label && h('label', {}, label), h('div', { class: 'inputs' }, controls));
}

function numberInput({ get, set, step = 0.01, min, integer = false, optional = false, onDone }) {
  const el = h('input', { type: 'number', step: integer ? 1 : step, ...(min !== undefined ? { min } : {}), ...(optional ? { placeholder: '—' } : {}) });
  const show = () => { if (document.activeElement !== el) el.value = fmt(get()); };
  show();
  el.addEventListener('change', () => {
    if (el.value.trim() === '') {
      if (optional) set(null);
      else el.value = fmt(get());
      return;
    }
    const v = parseFloat(el.value);
    if (!Number.isFinite(v)) { el.value = fmt(get()); return; }
    set(integer ? Math.round(v) : v);
    onDone?.();
  });
  bindings.push(show);
  return el;
}

function renderInspector() {
  const root = $('inspector');
  root.replaceChildren();
  bindings = [];
  if (!state.selection.length) {
    root.append(h('p', { class: 'hint' }, t('nothing')));
    return;
  }
  if (state.selection.length === 1) renderSingle(root, S.findComponent(state.spec, state.selection[0]));
  else renderMulti(root, state.selection);
}

function evidenceBadges(c) {
  const edited = String(c.evidence.note || '').includes(S.EDIT_NOTE);
  return h('div', { class: 'badges' },
    h('span', { class: 'badge' }, c.kind),
    h('span', { class: `badge ${c.evidence.status}` }, c.evidence.status === 'observed' ? t('evObserved') : t('evInferred')),
    edited && h('span', { class: 'badge edited' }, t('edited')),
    c.critical && h('span', { class: 'badge' }, 'critical'));
}

function scaleTool(ids) {
  const input = h('input', { type: 'number', step: 0.05, value: '1', min: '0.1' });
  const run = () => {
    const f = parseFloat(input.value);
    const b = S.selectionBounds(state.spec, ids);
    if (!Number.isFinite(f) || !b) return;
    if (edit((s) => S.scaleSelection(s, ids, f, [b.centre[0], b.centre[1], b.min[2]]), ids)) input.value = '1';
  };
  return field(t('groupScale'), input, h('button', { onClick: run }, t('apply')));
}

function colorInput(ids) {
  const first = S.findComponent(state.spec, ids[0]);
  const el = h('input', { type: 'color', value: S.toHex(first.material) });
  const apply = (live) => {
    const fn = (s) => { for (const id of ids) { const c = S.findComponent(s, id); S.setMaterial(s, [id], { color: S.fromHex(el.value, c.material) }); } return []; };
    if (live) liveEdit(fn, ids); else edit(fn, ids);
  };
  el.addEventListener('input', () => apply(true));
  el.addEventListener('change', () => { apply(true); commitLive(); });
  bindings.push(() => { if (document.activeElement !== el) el.value = S.toHex(S.findComponent(state.spec, ids[0]).material); });
  return el;
}

function materialSection(root, c) {
  const id = c.id;
  const slider = (label, key, max, step) => {
    const el = h('input', { type: 'range', min: 0, max, step });
    const read = () => (key === 'alpha' ? S.findComponent(state.spec, id).material.color[3] : S.findComponent(state.spec, id).material[key] ?? 0);
    el.value = read();
    const patch = () => (key === 'alpha'
      ? (s) => { const m = S.findComponent(s, id).material; S.setMaterial(s, [id], { color: [...m.color.slice(0, 3), parseFloat(el.value)] }); return []; }
      : (s) => { S.setMaterial(s, [id], { [key]: parseFloat(el.value) }); return []; });
    el.addEventListener('input', () => liveEdit(patch(), [id]));
    el.addEventListener('change', () => { liveEdit(patch(), [id]); commitLive(); });
    bindings.push(() => { if (document.activeElement !== el) el.value = read(); });
    return field(label, el);
  };
  root.append(h('h3', {}, t('material')), h('div', { class: 'fields' },
    field(t('color'), colorInput([id])),
    slider(t('alpha'), 'alpha', 1, 0.01), slider(t('roughness'), 'roughness', 1, 0.01), slider(t('metallic'), 'metallic', 1, 0.01),
    field(t('emission'), numberInput({
      step: 0.1, min: 0, optional: true,
      get: () => S.findComponent(state.spec, id).material.emission,
      set: (v) => edit((s) => S.setMaterial(s, [id], { emission: v && v > 0 ? v : null }), [id]),
    }))));
}

function paramRow(c, key, required) {
  const id = c.id;
  const live = () => S.findComponent(state.spec, id).params[key];
  const label = h('label', {}, key, !required && h('span', { class: 'opt' }, ` (${t('optional')})`));
  const wrap = (...controls) => h('div', { class: 'field' }, label, h('div', { class: 'inputs' }, controls));
  const value = live();
  if (Array.isArray(value) && value.length <= 4 && value.every((v) => typeof v === 'number')) {
    return wrap(value.map((_, i) => numberInput({
      get: () => live()?.[i], set: (v) => edit((s) => S.setParam(s, id, [key, i], v), [id]),
    })));
  }
  if (Array.isArray(value)) {
    const area = h('textarea', { spellcheck: 'false', title: t('jsonParam') });
    const show = () => { if (document.activeElement !== area) area.value = JSON.stringify(live()); };
    show();
    area.addEventListener('change', () => {
      try { edit((s) => S.setParam(s, id, [key], JSON.parse(area.value)), [id]); } catch (e) { say(t('refused', { msg: e.message }), 'error'); show(); }
    });
    bindings.push(show);
    return wrap(area);
  }
  if (key === 'bevel_mode') {
    const sel = h('select', {}, h('option', { value: 'all' }, 'all'), h('option', { value: 'vertical' }, 'vertical'));
    sel.value = value ?? 'all';
    sel.addEventListener('change', () => edit((s) => S.setParam(s, id, [key], sel.value), [id]));
    return wrap(sel);
  }
  if (typeof value === 'string' || key === 'body') {
    const el = h('input', { type: 'text' });
    el.value = value ?? '';
    el.addEventListener('change', () => edit((s) => S.setParam(s, id, [key], el.value), [id]));
    return wrap(el);
  }
  const integer = /segments/.test(key);
  return wrap(numberInput({
    integer, optional: !required, min: integer ? 1 : undefined,
    get: () => live(),
    set: (v) => edit((s) => (v === null ? S.removeParam(s, id, key) : S.setParam(s, id, [key], v)), [id]),
  }));
}

function renderSingle(root, c) {
  const id = c.id;
  const live = () => S.findComponent(state.spec, id);
  root.append(h('div', { class: 'idline' }, id), evidenceBadges(c),
    h('div', { class: 'fields' },
      field(t('role'), h('span', {}, c.role)), field(t('scope'), h('span', {}, c.scope_id))));
  // group
  const groupInput = h('input', { type: 'text', list: 'groupNames', value: c.group || '' });
  groupInput.addEventListener('change', () => {
    const name = groupInput.value.trim();
    if (name && !S.ID_PATTERN.test(name)) { say(t('groupInvalid'), 'error'); groupInput.value = live().group || ''; return; }
    structural((s) => { S.setGroup(s, [id], name || null); });
  });
  const datalist = h('datalist', { id: 'groupNames' }, S.groupNames(state.spec).map((g) => h('option', { value: g })));
  root.append(h('div', { class: 'fields' }, field(t('group'), groupInput, datalist),
    c.group && h('div', { class: 'actions' }, h('button', { onClick: () => select(S.groupMembers(state.spec, c.group)) }, t('selectGroup')))));
  // transform
  const axis = ['X', 'Y', 'Z'];
  root.append(h('h3', {}, t('location')), h('div', { class: 'fields' },
    field('', axis.map((a, i) => numberInput({ get: () => live().location[i], set: (v) => edit((s) => { const p = [...S.findComponent(s, id).location]; p[i] = v; return S.setTransform(s, id, { location: p }); }, [id]) })))));
  root.append(h('h3', {}, t('rotation')), h('div', { class: 'fields' },
    field('', axis.map((a, i) => numberInput({ step: 1, get: () => live().rotation_deg[i], set: (v) => edit((s) => { const r = [...S.findComponent(s, id).rotation_deg]; r[i] = v; return S.setTransform(s, id, { rotation_deg: r }); }, [id]) })))));
  // parameters
  const [required, allowed] = S.PARAM_KEYS[c.kind];
  root.append(h('h3', {}, t('params')), h('div', { class: 'fields' }, allowed.map((key) => paramRow(c, key, required.includes(key))), scaleTool([id])));
  materialSection(root, c);
}

function renderMulti(root, ids) {
  const b = S.selectionBounds(state.spec, ids);
  const groups = new Set(ids.map((i) => S.findComponent(state.spec, i)?.group || ''));
  root.append(h('div', { class: 'idline' }, t('selected', { n: ids.length })));
  if (b) root.append(h('p', { class: 'hint' }, t('size3', { w: fmt(b.size[0]), d: fmt(b.size[1]), h: fmt(b.size[2]) })));
  const nameInput = h('input', { type: 'text', list: 'groupNames2', value: groups.size === 1 ? [...groups][0] : '' });
  const apply = h('button', {
    onClick: () => {
      const name = nameInput.value.trim();
      if (name && !S.ID_PATTERN.test(name)) return say(t('groupInvalid'), 'error');
      structural((s) => { S.setGroup(s, ids, name || null); });
    },
  }, t('apply'));
  root.append(h('div', { class: 'fields' }, field(t('groupName'), nameInput, apply),
    h('datalist', { id: 'groupNames2' }, S.groupNames(state.spec).map((g) => h('option', { value: g })))));
  // move by / turn
  const d = [0, 1, 2].map(() => h('input', { type: 'number', step: 0.05, value: '0' }));
  root.append(h('h3', {}, t('groupMove')), h('div', { class: 'fields' }, field('', d, h('button', {
    onClick: () => { const delta = d.map((el) => parseFloat(el.value) || 0); if (edit((s) => S.translate(s, ids, delta), ids)) d.forEach((el) => { el.value = '0'; }); },
  }, t('apply')))));
  const yaw = h('input', { type: 'number', step: 5, value: '0' });
  root.append(h('div', { class: 'fields' }, field(t('groupYaw'), yaw, h('button', {
    onClick: () => {
      const deg = parseFloat(yaw.value) || 0;
      if (b && edit((s) => S.rotateYaw(s, ids, deg, [b.centre[0], b.centre[1]]), ids)) yaw.value = '0';
    },
  }, t('apply'))), scaleTool(ids)));
  root.append(h('h3', {}, t('material')), h('div', { class: 'fields' }, field(t('allColor'), colorInput(ids))));
}

// ---------------------------------------------------------------- chrome: texts, guide, events
function applyText() {
  if (opts.standalone) {
    document.title = t('title');
  }
  root.querySelectorAll('[data-i18n]').forEach((el) => { el.textContent = t(el.dataset.i18n); });
  if (opts.standalone) $('filter').placeholder = t('filter');                // an embedding page words its own controls
  $('guideTitle').textContent = t('guideTitle');
  $('guideSub').textContent = t('subtitle');
  $('guideBody').replaceChildren(...t('guideBody').map(([head, body]) => h('li', {}, h('b', {}, head), body)));
  if (state.spec) {
    renderOutline();
    renderInspector();
    renderIssues();
    updateToolbar();
  }
}

function setMode(mode) {
  state.mode = mode;
  viewport.setMode(mode);
  updateToolbar();
}

function toggleMatch() {
  if (viewport.matching) {
    viewport.frame(state.spec);
    updateToolbar();
    return;
  }
  if (viewport.matchSourceView(state.spec)) say(t('matchOn'), 'ok');
  else say(t('matchFailed', { msg: viewport.lastCameraError }), 'error');
  updateToolbar();
}

function wire() {
  $('btnOpen').addEventListener('click', () => $('fileInput').click());
  $('fileInput').addEventListener('change', (e) => { if (e.target.files[0]) openFile(e.target.files[0]); e.target.value = ''; });
  $('btnDemo').addEventListener('click', loadDemo);
  $('btnSave').addEventListener('click', saveSpec);
  $('btnGlb').addEventListener('click', exportGlb);
  $('btnUndo').addEventListener('click', undo);
  $('btnRedo').addEventListener('click', redo);
  $('btnMove').addEventListener('click', () => setMode('translate'));
  $('btnRotate').addEventListener('click', () => setMode('rotate'));
  $('chkSnap').addEventListener('change', (e) => { state.snap = e.target.checked; viewport.setSnap(state.snap); });
  $('btnDup').addEventListener('click', duplicateSelection);
  $('btnDel').addEventListener('click', deleteSelection);
  $('btnMatch').addEventListener('click', toggleMatch);
  $('btnFrame').addEventListener('click', () => { viewport.frame(state.spec, state.selection); updateToolbar(); });
  $('btnGuide').addEventListener('click', () => $('guide').showModal());
  $('btnIssues').addEventListener('click', () => { if (state.issues.length) $('issueList').hidden = !$('issueList').hidden; });
  $('filter').addEventListener('input', (e) => { state.filter = e.target.value; renderOutline(); });

  if (opts.dropFiles ?? opts.standalone) {
    window.addEventListener('dragover', (e) => { e.preventDefault(); $('dropHint').hidden = false; });
    window.addEventListener('dragleave', (e) => { if (!e.relatedTarget) $('dropHint').hidden = true; });
    window.addEventListener('drop', (e) => { e.preventDefault(); $('dropHint').hidden = true; if (e.dataTransfer.files[0]) openFile(e.dataTransfer.files[0]); });
  }
  window.addEventListener('beforeunload', (e) => { if (state.dirty) { e.preventDefault(); e.returnValue = t('dirty'); } });

  const onKey = (e) => {
    if (e.target.closest?.('dialog') || ['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) return;
    const mod = e.ctrlKey || e.metaKey;
    const key = e.key.toLowerCase();
    const step = e.shiftKey ? 0.5 : 0.05;
    if (mod && key === 'z') { e.preventDefault(); if (e.shiftKey) redo(); else undo(); }
    else if (mod && key === 'y') { e.preventDefault(); redo(); }
    else if (mod && key === 'd') { e.preventDefault(); duplicateSelection(); }
    else if (!mod && (e.key === 'Delete' || e.key === 'Backspace')) { e.preventDefault(); deleteSelection(); }
    else if (!mod && key === 'w') setMode('translate');
    else if (!mod && key === 'e') setMode('rotate');
    else if (!mod && key === 'f') viewport.frame(state.spec, state.selection);
    else if (e.key === 'Escape') select([]);
    else if (!mod && e.key === 'ArrowLeft') { e.preventDefault(); nudge([-step, 0, 0]); }
    else if (!mod && e.key === 'ArrowRight') { e.preventDefault(); nudge([step, 0, 0]); }
    else if (!mod && e.key === 'ArrowUp') { e.preventDefault(); nudge([0, step, 0]); }
    else if (!mod && e.key === 'ArrowDown') { e.preventDefault(); nudge([0, -step, 0]); }
    else if (!mod && e.key === 'PageUp') { e.preventDefault(); nudge([0, 0, step]); }
    else if (!mod && e.key === 'PageDown') { e.preventDefault(); nudge([0, 0, -step]); }
  };
  for (const target of opts.hotkeyTargets ?? [window]) target.addEventListener('keydown', onKey);
}

/** Before/after: show the source picture at the matched camera. mode: 'model' | 'photo' | 'wipe' | 'overlay'. Returns the mode that
 *  took effect: 'model' when the spec has no usable camera. */
function setCompare(mode, split = 50) {
  if (mode !== 'model' && !viewport.matching && !viewport.matchSourceView(state.spec)) mode = 'model';
  viewport.setCompare(mode, split);
  updateToolbar();
  return mode;
}

export function createEditor(rootEl = document.body, options = {}) {
  root = rootEl;
  opts = options;
  if (opts.standalone) {
    window.addEventListener('error', (e) => { root.dataset.error = e.message; say(e.message, 'error'); });
    window.addEventListener('unhandledrejection', (e) => { root.dataset.error = String(e.reason); say(String(e.reason), 'error'); });
  }
  viewport = new Viewport($('stage'), {
    ...gizmoHooks,
    onPick,
    onViewChange: updateToolbar,
    onMatchFrame: (rect) => opts.onMatchFrame?.(rect),
  });
  wire();
  applyText();
  if (opts.loadDemo !== false) loadDemo();
  return {
    state, viewport, S, select, edit, undo, redo, duplicateSelection, deleteSelection, toggleMatch,
    loadSpec: (spec, name = 'booth.spec.json') => loadSpec(spec, name, false),
    setCompare, say, setRender: (url) => viewport.setRender(url),
    isDirty: () => state.dirty,
    markClean: () => { state.dirty = false; updateToolbar(); },
  };
}

if (document.body.dataset.editor !== 'embedded') {
  window.boothEditor = createEditor(document.body, { standalone: true });
}
