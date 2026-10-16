// End-to-end smoke test of the real page: starts tools/serve.py and a headless Chrome/Edge, then drives the editor through
// the DevTools protocol (real mouse clicks, key presses, inspector inputs) and checks the results, including the saved spec
// (Python validator) and the exported GLB (trimesh).  Needs Chrome or Edge and Python; not part of `npm test`.
//
//   node tests/smoke.mjs [--shots <dir>] [--chrome <path>]       (npm run smoke)

import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync, copyFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import net from 'node:net';
import assert from 'node:assert/strict';
import { findChrome } from './browser.mjs';
import { PYTHON } from './python.mjs';

const here = fileURLToPath(new URL('.', import.meta.url));
const webDir = resolve(here, '..');
const repo = resolve(webDir, '..');
const arg = (name, fallback) => { const i = process.argv.indexOf(name); return i > 0 ? process.argv[i + 1] : fallback; };
const shots = resolve(arg('--shots', join(repo, 'output', '_web_smoke')));
mkdirSync(shots, { recursive: true });

const chrome = findChrome(arg('--chrome'));
if (!chrome) { console.error('No Chrome, Chromium or Edge found; pass --chrome <path> or set CHROME'); process.exit(2); }

const freePort = () => new Promise((res, rej) => {
  const srv = net.createServer();
  srv.on('error', rej);
  srv.listen(0, '127.0.0.1', () => { const { port } = srv.address(); srv.close(() => res(port)); });
});
const APP_PORT = await freePort();
const DEBUG_PORT = await freePort();
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const children = [];
const profile = mkdtempSync(join(tmpdir(), 'booth-smoke-'));
const work = mkdtempSync(join(tmpdir(), 'booth-smoke-out-'));

async function waitFor(fn, what, ms = 15000) {
  const end = Date.now() + ms;
  for (;;) {
    try { const v = await fn(); if (v) return v; } catch { /* retry */ }
    if (Date.now() > end) throw new Error(`timed out waiting for ${what}`);
    await sleep(100);
  }
}

function cleanup() {
  for (const c of children) {
    try {
      if (process.platform === 'win32') spawnSync('taskkill', ['/PID', String(c.pid), '/T', '/F'], { stdio: 'ignore' });
      else c.kill();
    } catch { /* already gone */ }
  }
  try { rmSync(profile, { recursive: true, force: true }); } catch { /* chrome may still hold files */ }
  try { rmSync(work, { recursive: true, force: true }); } catch { /* ignore */ }
}

async function connect(url) {
  const ws = new WebSocket(url);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('devtools socket failed')); });
  let id = 0;
  const pending = new Map();
  const listeners = [];
  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data);
    if (msg.id && pending.has(msg.id)) { const { res, rej } = pending.get(msg.id); pending.delete(msg.id); if (msg.error) rej(new Error(msg.error.message)); else res(msg.result); }
    else listeners.forEach((l) => l(msg));
  };
  return {
    send: (method, params = {}) => new Promise((res, rej) => { const i = ++id; pending.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params })); }),
    on: (l) => listeners.push(l),
    close: () => ws.close(),
  };
}

const failures = [];
const log = (ok, name, extra = '') => { console.log(`${ok ? 'ok  ' : 'FAIL'} ${name}${extra ? ` — ${extra}` : ''}`); if (!ok) failures.push(name); };
let diagnose = async () => '';
async function step(name, fn) {
  try { await fn(); log(true, name); } catch (e) {
    log(false, name, String(e.message).split('\n').slice(0, 8).join(' | '));
    console.log(`     page: ${await diagnose().catch(() => '?')}`);
  }
}

async function main() {
  children.push(spawn(PYTHON, [join(webDir, 'tools', 'serve.py'), '--port', String(APP_PORT), '--no-browser'], { stdio: 'ignore' }));
  await waitFor(async () => (await fetch(`http://127.0.0.1:${APP_PORT}/`)).ok, 'the local server');
  children.push(spawn(chrome, ['--headless=new', `--remote-debugging-port=${DEBUG_PORT}`, '--remote-allow-origins=*', `--user-data-dir=${profile}`,
    '--no-first-run', '--no-default-browser-check', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist',
    '--disable-gpu-sandbox', '--hide-scrollbars', '--window-size=1500,900', 'about:blank'], { stdio: 'ignore' }));
  const target = await waitFor(async () => (await (await fetch(`http://127.0.0.1:${DEBUG_PORT}/json/list`)).json()).find((t) => t.type === 'page'), 'the browser');
  const cdp = await connect(target.webSocketDebuggerUrl);
  const consoleErrors = [];
  cdp.on((m) => {
    if (m.method === 'Runtime.exceptionThrown') consoleErrors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
    if (m.method === 'Log.entryAdded' && m.params.entry.level === 'error') consoleErrors.push(`${m.params.entry.text} ${m.params.entry.url || ''}`);
    if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') consoleErrors.push(m.params.args.map((a) => a.value ?? a.description).join(' '));
  });
  await cdp.send('Runtime.enable');
  await cdp.send('Log.enable');
  await cdp.send('Page.enable');
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 900, deviceScaleFactor: 1, mobile: false });

  const ev = async (expression) => {
    const r = await cdp.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
    return r.result.value;
  };
  const shot = async (name) => {
    await sleep(250);
    const { data } = await cdp.send('Page.captureScreenshot', { format: 'png' });
    writeFileSync(join(shots, name), Buffer.from(data, 'base64'));
  };
  const mouse = async (type, x, y, mods = 0) => cdp.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', buttons: type === 'mouseReleased' ? 0 : 1, clickCount: 1, modifiers: mods });
  const click = async (x, y, mods = 0) => { await mouse('mouseMoved', x, y, mods); await mouse('mousePressed', x, y, mods); await mouse('mouseReleased', x, y, mods); await sleep(150); };
  const key = async (k, code, vk, mods = 0) => {
    await cdp.send('Input.dispatchKeyEvent', { type: 'rawKeyDown', key: k, code, windowsVirtualKeyCode: vk, modifiers: mods });
    await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key: k, code, windowsVirtualKeyCode: vk, modifiers: mods });
    await sleep(150);
  };
  const E = 'window.boothEditor';
  diagnose = () => ev(`JSON.stringify({ selection: ${E}.state.selection, status: document.getElementById('message').textContent, inspector: document.getElementById('inspector').innerText.slice(0, 260) })`);
  const spec = (path) => ev(`${E}.state.spec.${path}`);
  const comp = (id) => ev(`JSON.stringify(${E}.S.findComponent(${E}.state.spec, '${id}'))`).then(JSON.parse);
  const screenPoint = (id) => ev(`(() => { const { viewport } = ${E}; const m = viewport.parts.get('${id}'); m.updateMatrixWorld(true);
    const c = m.geometry.boundingSphere.center.clone().applyMatrix4(m.matrixWorld).project(viewport.camera);
    const r = document.getElementById('stage').getBoundingClientRect();
    return { x: r.left + (c.x + 1) / 2 * r.width, y: r.top + (1 - c.y) / 2 * r.height }; })()`);
  const selection = () => ev(`${E}.state.selection`);
  const count = () => ev(`${E}.state.spec.components.length`);

  await cdp.send('Page.navigate', { url: `http://127.0.0.1:${APP_PORT}/` });
  await step('page loads the synthetic demo without errors', async () => {
    await waitFor(() => ev('document.body.dataset.ready'), 'the demo', 45000);
    assert.equal(await ev('document.body.dataset.ready'), 'true');
    assert.equal(await ev('document.body.dataset.parts'), '79');
    assert.equal(await ev('document.body.dataset.error || ""'), '');
    assert.match(await ev("document.getElementById('btnIssues').textContent"), /无问题/);
    assert.equal(await ev(`document.querySelectorAll('#outlineList .row').length > 10`), true);
    assert.equal(await ev("document.getElementById('btnWorkspace') === null && document.getElementById('linkFlow') === null"), true);   // no studio controls in static mode
    assert.deepEqual(await ev("performance.getEntriesByType('resource').map((r) => new URL(r.name).pathname).filter((p) => p.startsWith('/api'))"), []);   // and no calls to a server API
    await shot('01_start.png');
  });

  await step('clicking a part selects its whole group; Alt+click one part; empty space clears', async () => {
    const p = await screenPoint('counter_top');
    const empty = await ev(`(() => { const r = document.getElementById('stage').getBoundingClientRect(); return { x: r.left + 20, y: r.top + 20 }; })()`);
    await click(p.x, p.y);
    const members = await ev(`${E}.S.groupMembers(${E}.state.spec, 'counter')`);
    assert.deepEqual((await selection()).sort(), members.sort());
    await shot('02_group_selected.png');
    await click(empty.x, empty.y);                                       // empty space clears (and moves the gizmo away)
    assert.equal((await selection()).length, 0);
    await click(p.x, p.y, 1);                                   // Alt: a single part, not the group
    assert.equal((await selection()).length, 1);
    await click(empty.x, empty.y);
    assert.equal((await selection()).length, 0);
  });

  await step('arrow keys move a group, Undo/Redo step through it', async () => {
    await ev(`${E}.select(${E}.S.groupMembers(${E}.state.spec, 'table_a'))`);
    const x0 = (await comp('table_a_top')).location[0];
    await key('ArrowRight', 'ArrowRight', 39);
    assert.ok(Math.abs((await comp('table_a_top')).location[0] - (x0 + 0.05)) < 1e-9);
    assert.match((await comp('table_a_top')).evidence.note, /Edited in the web editor/);
    await key('z', 'KeyZ', 90, 2);
    assert.ok(Math.abs((await comp('table_a_top')).location[0] - x0) < 1e-9);
    await key('y', 'KeyY', 89, 2);
    assert.ok(Math.abs((await comp('table_a_top')).location[0] - (x0 + 0.05)) < 1e-9);
    await key('z', 'KeyZ', 90, 2);
  });

  await step('Ctrl+D duplicates a group, Delete removes it, Undo brings it back', async () => {
    await ev(`${E}.select(${E}.S.groupMembers(${E}.state.spec, 'table_a'))`);
    await key('d', 'KeyD', 68, 2);
    assert.equal(await count(), 82);
    assert.deepEqual(await ev(`${E}.state.selection.map((id) => ${E}.S.findComponent(${E}.state.spec, id).group)`), ['table_a_2', 'table_a_2', 'table_a_2']);
    await key('Delete', 'Delete', 46);
    assert.equal(await count(), 79);
    await key('z', 'KeyZ', 90, 2);
    assert.equal(await count(), 82);
    await key('z', 'KeyZ', 90, 2);
    await key('z', 'KeyZ', 90, 2);
    assert.equal(await count(), 79);
  });

  await step('gizmo drags (group move, group yaw, single part) update the spec and history', async () => {
    const drag = (fn) => ev(`(() => { const { viewport } = ${E}; const t = viewport.transform; t.dispatchEvent({ type: 'mouseDown' }); (${fn})(viewport); t.dispatchEvent({ type: 'objectChange' }); t.dispatchEvent({ type: 'mouseUp' }); })()`);
    await ev(`${E}.select(${E}.S.groupMembers(${E}.state.spec, 'counter'))`);
    const before = await comp('counter_body');
    const at0 = await ev(`${E}.state.at`);
    await drag('(v) => { v.pivot.position.x += 0.5; }');
    const moved = await comp('counter_body');
    assert.ok(Math.abs(moved.location[0] - (before.location[0] + 0.5)) < 1e-9);
    assert.equal(await ev(`${E}.state.at`), at0 + 1);
    await ev(`${E}.toggleMatch ? 0 : 0; document.getElementById('btnRotate').click()`);
    await drag('(v) => { v.pivot.rotation.z = Math.PI / 2; }');
    const turned = await comp('counter_body');
    assert.ok(Math.abs(turned.rotation_deg[2] - (before.rotation_deg[2] + 90)) < 1e-6);
    await ev("document.getElementById('btnMove').click()");
    await ev(`${E}.select(['planter_box'])`);
    const pb = await comp('planter_box');
    await drag('(v) => { v.parts.get("planter_box").position.x -= 0.3; }');
    assert.ok(Math.abs((await comp('planter_box')).location[0] - (pb.location[0] - 0.3)) < 1e-9);
    await ev(`${E}.undo(); ${E}.undo(); ${E}.undo();`);
    assert.ok(Math.abs((await comp('counter_body')).location[0] - before.location[0]) < 1e-9);
  });

  await step('the inspector edits colour and size, and refuses a bad size', async () => {
    await ev(`${E}.select(['table_b_top'])`);
    await ev(`(() => { const el = document.querySelector('#inspector input[type=color]'); el.value = '#336699'; el.dispatchEvent(new Event('input', { bubbles: true })); el.dispatchEvent(new Event('change', { bubbles: true })); })()`);
    assert.equal(await ev(`${E}.S.toHex(${E}.S.findComponent(${E}.state.spec, 'table_b_top').material)`), '#336699');
    const setRadius = (v) => ev(`(() => { const row = [...document.querySelectorAll('#inspector .field')].find((f) => f.querySelector('label')?.textContent.startsWith('radius')); const el = row.querySelector('input'); el.value = '${v}'; el.dispatchEvent(new Event('change', { bubbles: true })); })()`);
    await setRadius(0.6);
    assert.equal((await comp('table_b_top')).params.radius, 0.6);
    await setRadius(-1);
    assert.equal((await comp('table_b_top')).params.radius, 0.6);
    assert.match(await ev("document.getElementById('message').textContent"), /已拒绝/);
  });

  await step('resizing the floor keeps the footprint in step', async () => {
    await ev(`${E}.select(['floor'])`);
    await ev(`(() => { const row = [...document.querySelectorAll('#inspector .field')].find((f) => f.querySelector('label')?.textContent.startsWith('size')); const el = row.querySelectorAll('input')[0]; el.value = '7'; el.dispatchEvent(new Event('change', { bubbles: true })); })()`);
    assert.deepEqual(await spec('scale.footprint_m'), [7, 5]);
    assert.match(await ev("document.getElementById('btnIssues').textContent"), /无问题/);
    await ev(`${E}.undo()`);
    assert.deepEqual(await spec('scale.footprint_m'), [6, 5]);
  });

  await step('Match source view places the camera and frame; dragging leaves it', async () => {
    await ev("document.getElementById('btnMatch').click()");
    assert.equal(await ev(`Boolean(${E}.viewport.matching) && !document.querySelector('.source-frame').hidden`), true);
    await shot('03_source_view.png');
    await mouse('mouseMoved', 700, 400); await mouse('mousePressed', 700, 400); await mouse('mouseMoved', 760, 430); await mouse('mouseReleased', 760, 430);
    assert.equal(await ev(`Boolean(${E}.viewport.matching)`), false);
  });

  await step('language toggle switches the UI', async () => {
    await ev("document.getElementById('btnLang').click()");
    assert.equal(await ev('document.title'), 'Booth Spec Editor');
    await ev("document.getElementById('btnLang').click()");
    assert.equal(await ev('document.title'), '展台规格编辑器');
  });

  await step('exported GLB loads in trimesh with one node per part and the groups', async () => {
    const b64 = await ev(`${E}.viewport.exportGlb(${E}.state.spec).then((buf) => { let s = ''; const a = new Uint8Array(buf); for (let i = 0; i < a.length; i += 8192) s += String.fromCharCode(...a.subarray(i, i + 8192)); return btoa(s); })`);
    const file = join(work, 'web.glb');
    writeFileSync(file, Buffer.from(b64, 'base64'));
    const py = spawnSync(PYTHON, ['-c', `
import sys, json, trimesh
s = trimesh.load(sys.argv[1], force='scene')
nodes = set(s.graph.nodes)
print(json.dumps({'meshes': len(s.geometry), 'groups': sorted(n for n in nodes if n.startswith('group:')), 'bounds': s.bounds.round(2).tolist()}))
`, file], { encoding: 'utf8' });
    assert.equal(py.status, 0, py.stderr);
    const info = JSON.parse(py.stdout);
    assert.equal(info.meshes, 79);
    assert.ok(info.groups.includes('group:counter'), py.stdout);
    assert.ok(info.bounds[1][1] > 3 && info.bounds[1][1] < 3.2, `top of the booth in Y-up metres: ${py.stdout}`);
  });

  await step('the edited spec still passes the Python validator', async () => {
    await ev(`${E}.select(${E}.S.groupMembers(${E}.state.spec, 'chair_b1'))`);
    await key('ArrowLeft', 'ArrowLeft', 37);
    await key('PageUp', 'PageUp', 33);
    const json = await ev(`JSON.stringify(${E}.state.spec, null, 2)`);
    const dir = join(work, 'spec');
    mkdirSync(dir);
    const picture = join(webDir, 'app', 'demo', 'demo_source.png');      // may be swapped or missing; docs/sample-images.md
    if (existsSync(picture)) copyFileSync(picture, join(dir, 'demo_source.png'));
    writeFileSync(join(dir, 'edited.spec.json'), json);
    const check = existsSync(picture) ? ['--check-source'] : [];
    const run = spawnSync(PYTHON, [join(repo, 'booth-image-to-model', 'scripts', 'validate_spec.py'), '--spec', join(dir, 'edited.spec.json'), ...check], { encoding: 'utf8' });
    assert.equal(run.status, 0, run.stdout + run.stderr);
    assert.equal(JSON.parse(run.stdout).valid, true);
    await shot('04_after_edits.png');
  });

  log(consoleErrors.length === 0, 'no console errors or exceptions', consoleErrors.slice(0, 3).join(' | '));
  cdp.close();
}

for (const sig of ['SIGINT', 'SIGTERM']) process.on(sig, () => { cleanup(); process.exit(130); });
try {
  await main();
} catch (e) {
  log(false, 'smoke run', e.message);
} finally {
  cleanup();
}
console.log(failures.length ? `\n${failures.length} FAILED` : `\nall smoke steps passed; screenshots in ${shots}`);
process.exit(failures.length ? 1 : 0);
