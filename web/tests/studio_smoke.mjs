// End-to-end smoke test of the studio: starts the local server (web/server/run.py) with a stand-in for the OpenAI API, then drives the
// real page in headless Chrome/Edge through the DevTools protocol (real key presses, file choosers, clicks) in ONE window:
//   empty stage -> picture -> a refused key -> the upload alternative -> the model over the photo -> before/after (wipe, overlay, photo,
//   hold space) -> edit on the stage and autosave -> model file export -> quick check -> render -> downloads -> stale after an edit ->
//   advanced view; then the whole way again with a good key, "generate and go straight to the picture" (and, when a corner is
//   clicked in the wrong place, the pause before the render with "Render anyway"), a ready-made model file, a vanished server, and the
//   recent jobs. The Blender steps run when the server finds Blender (skip with --skip-blender).
//
//   node tests/studio_smoke.mjs [--shots <dir>] [--chrome <path>] [--skip-blender] [--image <png> --scene <json>]     (npm run smoke:studio)

import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import net from 'node:net';
import assert from 'node:assert/strict';
import { findChrome } from './browser.mjs';
import { PYTHON } from './python.mjs';
import { findBanned } from '../app/js/studio_copy.js';

const here = fileURLToPath(new URL('.', import.meta.url));
const webDir = resolve(here, '..');
const repo = resolve(webDir, '..');
const arg = (name, fallback) => { const i = process.argv.indexOf(name); return i > 0 ? process.argv[i + 1] : fallback; };
const flag = (name) => process.argv.includes(name);
const shots = resolve(arg('--shots', join(repo, 'output', '_web_studio_smoke')));
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
const profile = mkdtempSync(join(tmpdir(), 'booth-studio-profile-'));
const work = mkdtempSync(join(tmpdir(), 'booth-studio-work-'));
const jobsDir = join(work, 'jobs');
const BAD_KEY = 'sk-smoke-reject-0123456789';
const GOOD_KEY = 'sk-smoke-secret-0123456789';
const BASE = `http://127.0.0.1:${APP_PORT}`;
const custom = Boolean(arg('--image'));                                  // a real picture: the fit need not be perfect, only reported

async function waitFor(fn, what, ms = 15000) {
  const end = Date.now() + ms;
  for (;;) {
    try { const v = await fn(); if (v) return v; } catch { /* retry */ }
    if (Date.now() > end) throw new Error(`timed out waiting for ${what}`);
    await sleep(150);
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
  if (!flag('--keep')) { try { rmSync(work, { recursive: true, force: true }); } catch { /* ignore */ } }
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
  const started = Date.now();
  try { await fn(); log(true, name, `${Math.round((Date.now() - started) / 1000)} s`); } catch (e) {
    log(false, name, String(e.message).split('\n').slice(0, 8).join(' | '));
    console.log(`     page: ${await diagnose().catch(() => '?')}`);
  }
}

function files(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...files(p)); else out.push(p);
  }
  return out;
}

async function main() {
  // ---- inputs: the synthetic picture and scene, or the ones given on the command line
  const fixtures = join(work, 'fixtures');
  const made = spawnSync(PYTHON, [join(here, 'make_studio_fixtures.py'), fixtures], { encoding: 'utf8' });
  assert.equal(made.status, 0, made.stderr);
  const imageFile = resolve(arg('--image', join(fixtures, 'booth.png')));
  const sceneFile = resolve(arg('--scene', join(fixtures, 'scene.json')));
  const badSceneFile = join(fixtures, 'bad_scene.json');

  // ---- the stand-in for OpenAI, then the studio server without a key
  // Readings, in order: the refused key (the first answer is spent on it), a good one, the one-click render's, and then readings whose
  // one corner is clicked far from where it belongs ("review", from scene_fixture.review_scene()). A real picture (--image) gets good ones
  // only: its own numbers decide.
  const mockScript = custom ? 'good' : 'good,good,good,review';
  const mock = spawn(PYTHON, [join(here, 'mock_openai.py'), '--scene', sceneFile, '--script', mockScript], { stdio: ['ignore', 'pipe', 'inherit'] });
  children.push(mock);
  const base = await new Promise((res, rej) => {
    let text = '';
    mock.stdout.on('data', (d) => { text += d; const m = text.match(/at (http:\S+)/); if (m) res(m[1]); });
    mock.on('exit', () => rej(new Error('the OpenAI stand-in did not start')));
  });
  const env = { ...process.env }; delete env.OPENAI_API_KEY;
  children.push(spawn(PYTHON, [join(webDir, 'server', 'run.py'), '--port', String(APP_PORT), '--no-browser', '--jobs-dir', jobsDir,
    '--openai-base-url', base, '--openai-model', 'test-model'], { stdio: 'ignore', env }));
  const health = await waitFor(async () => (await fetch(`${BASE}/api/health`)).json(), 'the studio server');
  const withBlender = Boolean(health.blender) && !flag('--skip-blender');
  console.log(`studio ${BASE}, OpenAI stand-in ${base}, Blender ${withBlender ? health.blender : 'skipped'}`);

  children.push(spawn(chrome, ['--headless=new', `--remote-debugging-port=${DEBUG_PORT}`, '--remote-allow-origins=*', `--user-data-dir=${profile}`,
    '--no-first-run', '--no-default-browser-check', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist',
    '--disable-gpu-sandbox', '--hide-scrollbars', '--window-size=1500,950', 'about:blank'], { stdio: 'ignore' }));
  const target = await waitFor(async () => (await (await fetch(`http://127.0.0.1:${DEBUG_PORT}/json/list`)).json()).find((t) => t.type === 'page'), 'the browser');
  const cdp = await connect(target.webSocketDebuggerUrl);
  const consoleErrors = [];
  cdp.on((m) => {
    if (m.method === 'Runtime.exceptionThrown') consoleErrors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
    if (m.method === 'Log.entryAdded' && m.params.entry.level === 'error' && !/status of 4(09|22)/.test(m.params.entry.text)) consoleErrors.push(`${m.params.entry.text} ${m.params.entry.url || ''}`);
    if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') consoleErrors.push(m.params.args.map((a) => a.value ?? a.description).join(' '));
  });
  await cdp.send('Runtime.enable');
  await cdp.send('Log.enable');
  await cdp.send('Page.enable');
  await cdp.send('DOM.enable');
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 950, deviceScaleFactor: 1, mobile: false });

  const ev = async (expression) => {
    const r = await cdp.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
    return r.result.value;
  };
  const shot = async (name) => {
    await sleep(350);
    const { data } = await cdp.send('Page.captureScreenshot', { format: 'png' });
    writeFileSync(join(shots, name), Buffer.from(data, 'base64'));
  };
  const api = async (path, init) => (await fetch(`${BASE}${path}`, init)).json();
  const post = (path, json) => api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(json) });
  const goto = async (path) => { await cdp.send('Page.navigate', { url: `${BASE}${path}` }); await waitFor(() => ev('document.body.dataset.ready'), `${path} ready`, 60000); };
  const upload = async (selector, file) => {
    const { root } = await cdp.send('DOM.getDocument', { depth: 0 });
    const { nodeId } = await cdp.send('DOM.querySelector', { nodeId: root.nodeId, selector });
    assert.ok(nodeId, `no element ${selector}`);
    await cdp.send('DOM.setFileInputFiles', { files: [file], nodeId });
  };
  const click = (id) => ev(`document.getElementById('${id}').click()`);
  const text = (selector) => ev(`document.querySelector('${selector}')?.innerText || ''`);
  const visible = (selector) => ev(`(() => { const e = document.querySelector('${selector}'); if (!e) return false; const r = e.getBoundingClientRect();
    const shown = e.checkVisibility ? e.checkVisibility({ checkVisibilityCSS: true, contentVisibilityAuto: true }) : getComputedStyle(e).visibility !== 'hidden';   // not inside a closed <details>
    return shown && !e.hidden && r.width > 0 && r.height > 0; })()`);
  const phase = () => ev('document.body.dataset.phase');
  const waitPhase = (wanted, ms = 60000) => waitFor(async () => (await phase()) === wanted, `phase ${wanted}`, ms);
  const view = () => ev('JSON.stringify(window.studio.ui.view.card.kind)').then(JSON.parse);
  const stepState = (n) => ev(`document.querySelector('.st-step[data-n="${n}"]')?.dataset.state || ''`);
  const waitStep = (n, wanted, ms = 120000) => waitFor(async () => (await stepState(n)) === wanted, `step ${n} to be ${wanted}`, ms);
  const jobId = () => ev('window.studio.ui.job?.id');
  const key = async (k, code, vk, type = 'both', modifiers = 0) => {
    if (type !== 'up') await cdp.send('Input.dispatchKeyEvent', { type: 'rawKeyDown', key: k, code, windowsVirtualKeyCode: vk, modifiers });
    if (type !== 'down') await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key: k, code, windowsVirtualKeyCode: vk, modifiers });
    await sleep(150);
  };
  const mouseAt = async (x, y) => {                                                    // a real pointer click, as a person would make it
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y });
    await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', buttons: 1, clickCount: 1 });
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', buttons: 0, clickCount: 1 });
    await sleep(200);
  };
  const mouseClickEl = async (id) => {
    const c = await ev(`(() => { const r = document.getElementById('${id}').getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()`);
    await mouseAt(c.x, c.y);
  };
  const rects = () => ev(`(() => { const a = document.querySelector('.source-photo').getBoundingClientRect(), b = document.querySelector('.source-frame').getBoundingClientRect();
    return { photo: [a.left, a.top, a.width, a.height], frame: [b.left, b.top, b.width, b.height] }; })()`);
  const E = 'window.studio.editor';
  const screenPoint = (id) => ev(`(() => { const m = ${E}.viewport.parts.get('${id}'); m.geometry.computeBoundingSphere(); m.updateMatrixWorld(true);
    const c = m.geometry.boundingSphere.center.clone().applyMatrix4(m.matrixWorld).project(${E}.viewport.camera);
    const r = document.getElementById('stage').getBoundingClientRect();
    return { x: r.left + (c.x + 1) / 2 * r.width, y: r.top + (1 - c.y) / 2 * r.height }; })()`);
  diagnose = () => ev(`JSON.stringify({ url: location.pathname + location.search, phase: document.body.dataset.phase, card: window.studio?.ui?.view?.card?.kind,
    steps: [...document.querySelectorAll('.st-step')].map((b) => b.dataset.n + ':' + b.dataset.state).join(' '), card_text: document.getElementById('stepCard')?.innerText.slice(0, 200) })`);
  const newJob = async () => {
    if ((await phase()) !== 'empty') { await click('btnNew'); await waitPhase('empty'); }
    await upload('#imageInput', imageFile);
    await waitPhase('photo');
    return jobId();
  };
  const toModel = async (jobFor) => {                                     // a job with a model: scene upload, compile
    await click('moreWays').catch(() => {});
    await ev("document.getElementById('moreWays').open = true");
    await upload('#sceneInput', sceneFile);
    await waitPhase('model');
    await waitFor(() => ev(`Boolean(${E}.viewport.matching) && ${E}.state.spec?.components.length > 10`), 'the model on the photo', 60000);
    return jobFor;
  };

  // ------------------------------------------------------------ the stage before anything
  await cdp.send('Page.navigate', { url: `${BASE}/` });
  await step('the studio opens on an empty stage: a drop zone and four steps', async () => {
    await waitFor(() => ev('document.body.dataset.ready'), 'the studio', 60000);
    assert.equal(await phase(), 'empty');
    assert.equal(await visible('#dropZone'), true);
    assert.deepEqual(await ev("[...document.querySelectorAll('.st-step')].map((s) => s.textContent.replace(/^[^A-Za-z]*/, ''))"), ['Choose picture', 'Make model', 'Adjust', 'Render']);
    assert.equal(await stepState(1), 'current');
    assert.equal(await visible('#envChip'), false);
    assert.equal(await visible('#compareBar'), false);
    assert.equal(await ev("document.querySelectorAll('a[target=_blank]').length"), 0);
    await shot('01_empty.png');
  });

  await step('a picture is shown at once and the next step asks for what it needs', async () => {
    await upload('#imageInput', imageFile);
    await waitPhase('photo');
    assert.equal(await visible('#photoPlain'), true);
    assert.match(await text('#photoName'), /\d+ × \d+ pixels/);
    assert.equal(await stepState(2), 'current');
    assert.equal(await visible('#keyInput'), true);                                  // no key yet: the card asks for it
    assert.equal(await ev("document.getElementById('btnGenerate').disabled"), true);
    await shot('02_picture.png');
  });

  await step('a key the service refuses is explained, with one main button and a way out', async () => {
    await ev(`(() => { const el = document.getElementById('keyInput'); el.value = '${BAD_KEY}'; el.dispatchEvent(new Event('input', { bubbles: true })); })()`);
    await click('btnSaveKey');
    await waitFor(async () => !(await visible('#keyInput')), 'the key box to go away once a key is held');
    assert.equal(await ev("document.getElementById('btnGenerate').disabled"), true);   // the consent is still missing
    await click('chkConsent');
    assert.equal(await ev("document.getElementById('btnGenerate').disabled"), false);
    await click('btnGenerate');
    await waitStep(2, 'error');
    assert.equal(await view(), 'error');
    assert.equal(await visible('#btnRetry'), true);
    assert.match(await text('.st-error'), /The large model could not read this picture/);
    assert.equal(await visible('.st-error #chkConsent'), true);                         // a new reading needs the consent again: the box is on the error card
    await click('chkConsent');
    assert.equal(await ev("document.getElementById('btnRetry').disabled"), true);
    await click('chkConsent');
    assert.equal(await ev("document.getElementById('btnRetry').disabled"), false);
    assert.match(await text('#errorReason'), /does not accept this key/);                       // plain words; the service's own text is kept apart
    assert.doesNotMatch(await text('#errorReason'), /HTTP|sk-/);
    assert.match(await ev("document.querySelector('.st-error details p')?.textContent || ''"), /HTTP 401/);
    await shot('03_read_failed.png');
  });

  await step('the upload alternative gets from the same picture to a model over the photo', async () => {
    await ev("[...document.querySelectorAll('#stepCard button.link')].find((b) => /upload a file instead/i.test(b.textContent))?.click()");
    await waitFor(() => ev("document.getElementById('moreWays')?.open"), 'the folded area to open');
    assert.equal(await visible('#keyInput'), true);                                   // and the refused key can be replaced there
    await upload('#sceneInput', badSceneFile);
    await waitFor(() => text('#stepCard .st-warn').then((t) => /SCOPE|Did not load/.test(t)), 'the refusal that names the file');
    assert.equal(await phase(), 'photo');
    await ev("document.getElementById('moreWays').open = true");
    await upload('#sceneInput', sceneFile);
    await waitPhase('model');
    await waitFor(() => ev(`Boolean(${E}.viewport.matching) && ${E}.state.spec?.components.length > 10`), 'the model on the photo', 60000);
    await waitStep(2, 'done', 60000);                                                  // the model file exists a moment before the step is marked done
    assert.equal(await stepState(3), 'current');
    assert.equal(await jobId() === (await ev('window.studio.ui.current')), true);
  });

  let jobA;
  await step('the model sits on the photo: same rectangle, wipe by default, plain words, nothing in another window', async () => {
    jobA = await jobId();
    assert.equal(await visible('.source-photo'), true);
    const { photo, frame } = await rects();
    for (let i = 0; i < 4; i++) assert.ok(Math.abs(photo[i] - frame[i]) <= 1, `photo ${photo} vs frame ${frame}`);
    assert.equal(await ev("document.getElementById('view-wipe').getAttribute('aria-pressed')"), 'true');
    assert.equal(await visible('#compareBar'), true);
    assert.equal(await visible('#stageTools'), true);
    assert.ok((await ev("document.querySelectorAll('#outlineList .row').length")) > 10);
    assert.match(await text('#stepCard'), custom ? /model/i : /Model ready, \d+ parts/);
    const mainText = await ev("['stepCard', 'steps', 'compareBar', 'stageTools'].map((id) => document.getElementById(id).innerText).join(' ')");
    assert.deepEqual(findBanned(mainText), []);
    assert.equal(await ev("document.querySelectorAll('a[target=_blank]').length"), 0);
    await shot('04_model_wipe.png');
  });

  await step('clicking a part selects it and keeps the comparison; only moving the camera leaves it', async () => {
    const id = await ev(`${E}.state.spec.components[3].id`);
    const p = await screenPoint(id);
    await mouseAt(p.x, p.y);
    assert.equal(await ev(`${E}.viewport.compare.mode`), 'wipe');
    assert.equal(await ev('window.studio.ui.compare.mode'), 'wipe');
    assert.equal(await visible('.source-photo'), true);
    assert.ok((await ev(`${E}.state.selection.length`)) > 0);
    await ev(`${E}.select([])`);
    const c = await ev("(() => { const r = document.getElementById('stage').getBoundingClientRect(); return { x: r.left + r.width * 0.7, y: r.top + r.height * 0.3 }; })()");
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: c.x, y: c.y });
    await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: c.x, y: c.y, button: 'left', buttons: 1, clickCount: 1 });
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: c.x + 60, y: c.y + 30, button: 'left', buttons: 1 });
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: c.x + 60, y: c.y + 30, button: 'left', buttons: 0, clickCount: 1 });
    await waitFor(async () => (await ev('window.studio.ui.compare.mode')) === 'model', 'the drag to leave the source view');
    assert.equal(await visible('.source-photo'), false);
    await mouseClickEl('view-wipe');
    assert.equal(await ev(`${E}.viewport.compare.mode`), 'wipe');
    assert.equal(await visible('.source-photo'), true);
  });

  await step('before/after: the split moves, overlay puts the model over the photo, photo shows it alone, the space bar peeks', async () => {
    await ev("(() => { const s = document.getElementById('splitSlider'); s.value = '30'; s.dispatchEvent(new Event('input', { bubbles: true })); })()");
    assert.match(await ev("document.querySelector('.source-photo').style.clipPath"), /70%/);
    await click('view-overlay');
    assert.equal(await ev("document.querySelector('.source-photo').classList.contains('under')"), true);
    assert.equal(await ev(`${E}.viewport.scene.background === null`), true);               // the canvas is transparent over the photo
    assert.equal(await ev(`${E}.viewport.helpers.every((o) => !o.visible)`), true);        // no grid, axes or ground while comparing
    await shot('05_model_overlay.png');
    await click('view-photo');
    assert.equal(await ev("document.querySelector('.source-photo').style.clipPath"), '');
    await mouseClickEl('view-wipe');                                                        // a real click on the control, then the space bar: it still peeks
    await key(' ', 'Space', 32, 'down');
    assert.equal(await ev(`${E}.viewport.compare.mode`), 'photo');
    await key(' ', 'Space', 32, 'up');
    assert.equal(await ev(`${E}.viewport.compare.mode`), 'wipe');
    await click('view-model');
    assert.equal(await visible('.source-photo'), false);
    assert.equal(await ev(`${E}.viewport.helpers.every((o) => o.visible)`), true);
    await click('view-wipe');
    assert.equal(await visible('.source-photo'), true);
  });

  await step('an edit on the stage saves itself, in the same window, and the model file can be exported', async () => {
    const id = await ev(`${E}.state.spec.components[3].id`);
    const x0 = await ev(`${E}.state.spec.components[3].location[0]`);
    await ev(`${E}.select(['${id}']); document.getElementById('stage').focus()`);
    await key('ArrowRight', 'ArrowRight', 39);
    const edited = x0 + 0.05;
    assert.ok(Math.abs(await ev(`${E}.state.spec.components[3].location[0]`) - edited) < 1e-9);
    await waitFor(() => ev("document.getElementById('saveChip').dataset.state === 'saved'"), 'the saved chip');
    assert.match(await text('#saveChip'), /Saved/);
    const saved = await api(`/api/jobs/${jobA}/spec`);
    assert.ok(Math.abs(saved.components[3].location[0] - edited) < 1e-9);
    assert.equal((await api(`/api/jobs/${jobA}`)).spec.origin, 'edited');
    assert.equal(await ev('location.pathname'), '/');
    assert.equal(await ev(`${E}.isDirty()`), false);
    await ev("document.getElementById('btnExportGlb').click()");
    await waitFor(() => ev("Boolean(document.querySelector('#downloads a[data-key=glb]'))"), 'the model file link', 240000);
    const head = await ev(`fetch(document.querySelector('#downloads a[data-key=glb]').href).then((r) => r.arrayBuffer()).then((b) => String.fromCharCode(...new Uint8Array(b).slice(0, 4)))`);
    assert.equal(head, 'glTF');
    await shot('06_edited_saved.png');
  });

  if (withBlender) {
    await step('a quick check becomes the "after" picture and hides the live model while it shows', async () => {
      await click('btnQuickCheck');
      await waitStep(2, 'done');
      await waitFor(() => ev("document.getElementById('after-check')?.getAttribute('aria-pressed') === 'true'"), 'the check picture to be chosen', 600000);
      assert.equal(await visible('.source-render'), true);
      assert.equal(await ev(`${E}.viewport.renderer.domElement.style.visibility`), 'hidden');
      assert.match(await text('#compareNote'), /"Live model"/);
      const { photo, frame } = await rects();
      for (let i = 0; i < 4; i++) assert.ok(Math.abs(photo[i] - frame[i]) <= 1);
      await shot('07_quick_check.png');
      await click('after-live');
      assert.equal(await visible('.source-render'), false);
      assert.equal(await ev(`${E}.viewport.renderer.domElement.style.visibility`), '');
    });

    await step('the render makes the client picture; the downloads are real files and the picture is the first', async () => {
      await click('btnRender');
      for (const n of [4]) await waitStep(n, 'done', 1500000);
      await waitFor(() => ev("document.getElementById('after-final')?.getAttribute('aria-pressed') === 'true'"), 'the final picture to be chosen');
      const keys = await ev("[...document.querySelectorAll('#downloads a')].map((a) => a.dataset.key)");
      assert.equal(keys[0], 'client');
      assert.ok(keys.includes('blend') && keys.includes('glb'));
      const got = await ev(`(async () => { const out = []; for (const a of document.querySelectorAll('#downloads a')) {
        const r = await fetch(a.href); const b = new Uint8Array(await r.arrayBuffer());
        out.push({ key: a.dataset.key, status: r.status, size: b.length, head: String.fromCharCode(...b.slice(0, 4)) }); } return out; })()`);
      for (const g of got) assert.ok(g.status === 200 && g.size > 100, JSON.stringify(g));
      assert.equal(got.find((g) => g.key === 'client').head.slice(1, 4), 'PNG');
      assert.equal(got.find((g) => g.key === 'glb').head, 'glTF');
      assert.equal(await visible('#btnOpenBlender'), true);
      assert.equal(await visible('.source-render'), true);
      assert.match(await text('#btnRender'), /Render again/);
      await shot('08_rendered.png');
    });

    await step('another edit makes the picture stale: said plainly, offered again, never silently out of date', async () => {
      await ev(`${E}.select([${E}.state.spec.components[3].id]); document.getElementById('stage').focus()`);
      await key('ArrowLeft', 'ArrowLeft', 37);
      await waitFor(() => ev("document.getElementById('saveChip').dataset.state === 'saved'"), 'the saved chip');
      await waitStep(4, 'stale');
      assert.equal(await visible('#staleNote'), true);
      assert.equal(await ev("document.getElementById('after-final').disabled"), true);              // a stale picture is not offered for comparison
      assert.equal(await visible('.source-render'), false);
      assert.equal(await visible('#downloads a[data-key=client] .stale'), true);                       // a picture of the earlier model is still a real file: offered, marked
      assert.equal(await visible('#btnRender'), true);
      await shot('09_stale.png');
    });
  }

  await step('the advanced view opens in the same window with the alignment and the records', async () => {
    await click('btnPro');
    assert.equal(await visible('#pro'), true);
    await waitFor(() => text('#proBody').then((t) => /Fit|px/.test(t)), 'the alignment page');
    assert.ok((await ev("document.querySelectorAll('#proBody img').length")) >= 1);
    assert.match(await text('#qualityBlock'), /Quality check[\s\S]*Verdict: (pass|review)/);       // the engine's own verdict and numbers
    assert.match(await text('#proBody'), /Error when predicted from the others/);                     // the corner table says what leaving a corner out does
    if (!custom) assert.match(await text('#qualityBlock'), /Leave-one-out error/);                    // every corner of the synthetic reading is fitted and checked by it
    await ev("document.querySelectorAll('#proTabs button')[1].click()");
    assert.match(await text('#proBody'), /Step log/);
    assert.equal(await ev('location.pathname'), '/');
    await shot('10_pro.png');
    await click('btnProClose');
    assert.equal(await visible('#pro'), false);
  });

  // ------------------------------------------------------------ again, with a key the service accepts
  await step('with a good key: consent, generate, and the model appears over the photo', async () => {
    await post('/api/config', { openai_api_key: GOOD_KEY });
    await newJob();
    await ev('window.studio.loadJob(window.studio.ui.job.id)');
    assert.equal(await ev("document.getElementById('btnGenerate').disabled"), true);              // no consent for this picture yet
    await click('chkConsent');
    await click('btnGenerate');
    await waitPhase('model');
    await waitFor(() => ev(`Boolean(${E}.viewport.matching) && ${E}.state.spec?.components.length > 10`), 'the model on the photo', 60000);
    assert.equal((await api(`/api/jobs/${await jobId()}`)).steps.scene.state, 'done');
    await shot('11_generated.png');
  });

  if (withBlender) {
    await step('"generate and go straight to the picture" reads, builds, renders and exports in one go', async () => {
      await newJob();
      await click('chkConsent');
      await click('btnGenerateAll');
      if (custom) {                                                         // a real picture: its own numbers may ask for the pause; answer it
        await waitFor(async () => (await visible('#btnRenderAnyway')) || (await stepState(4)) === 'done', 'the render or the pause', 1500000);
        if (await visible('#btnRenderAnyway')) await click('btnRenderAnyway');
      }
      await waitStep(4, 'done', 1500000);
      await waitFor(() => ev("Boolean(document.querySelector('#downloads a[data-key=client]'))"), 'the client picture link');
      assert.equal(await visible('#gateHold'), false);                      // the checks agreed: nothing was paused
      await shot('12_straight_to_picture.png');
    });

    if (!custom) {
      await step('when a corner is clicked in the wrong place, the one-click render stops before the render, says why plainly, and renders when asked', async () => {
        await newJob();
        await click('chkConsent');
        await click('btnGenerateAll');
        await waitFor(() => visible('#gateHold'), 'the pause before the render', 300000);
        const id = await jobId();
        const held = await api(`/api/jobs/${id}`);
        assert.equal(held.steps.compile.state, 'done');
        assert.equal(held.steps.compile.detail.quality_gate.status, 'review');
        assert.equal(held.steps.build.state, 'idle');                        // nothing was rendered
        assert.equal(await stepState(4), 'todo');
        assert.match(await text('#gateHold'), /paused/i);
        assert.match(await text('#stepCard'), /wrong place|not line up/);   // the reason, in plain words
        assert.match(await text('#gateHold'), /render anyway, or start again with another picture or your own file/i);   // no promise to fix anything
        assert.doesNotMatch(await text('#gateHold'), /\bfix\b/i);
        assert.equal(await visible('#btnStartAgain'), true);
        assert.equal(await visible('#btnRenderAnyway'), true);
        assert.equal(await visible('#btnRender'), false);
        assert.deepEqual(findBanned(await text('#stepCard')), []);
        await shot('12b_review_pause.png');
        await click('btnRenderAnyway');
        await waitStep(4, 'done', 1500000);
        assert.equal(await visible('#gateHold'), false);
        assert.match(await text('#btnRender'), /Render again/);
      });
    }
  }

  await step('a ready-made model file for this picture skips reading; one for another picture is refused with the file named', async () => {
    const spec = await api(`/api/jobs/${jobA}/spec`);
    const good = join(work, 'good.spec.json');
    const wrong = join(work, 'wrong.spec.json');
    writeFileSync(good, JSON.stringify(spec));
    writeFileSync(wrong, JSON.stringify({ ...spec, source: { ...spec.source, sha256: '0'.repeat(64) } }));
    await newJob();
    await ev("document.getElementById('moreWays').open = true");
    await upload('#specInput', wrong);
    await waitFor(() => text('#stepCard .st-warn').then((t) => /Did not load wrong\.spec\.json.*sha256/s.test(t)), 'the refusal');
    assert.equal(await phase(), 'photo');
    await ev("document.getElementById('moreWays').open = true");
    await upload('#specInput', good);
    await waitPhase('model');
    assert.equal((await api(`/api/jobs/${await jobId()}`)).spec.origin, 'uploaded');
    assert.equal(await stepState(2), 'done');
  });

  await step('an edit undone before it is saved writes nothing; leaving a job right after an edit saves it first', async () => {
    await goto(`/?job=${jobA}`);
    await waitFor(() => ev(`Boolean(${E}.viewport.matching) && ${E}.state.spec?.components.length > 10`), 'the model on the photo', 60000);
    const before = await api(`/api/jobs/${jobA}`);
    await ev(`${E}.select([${E}.state.spec.components[3].id]); document.getElementById('stage').focus()`);
    await key('ArrowRight', 'ArrowRight', 39);
    await key('z', 'KeyZ', 90, 'both', 2);                                                     // Ctrl+Z before the 0.8 s are up
    await sleep(1800);
    const same = await api(`/api/jobs/${jobA}`);
    assert.equal(same.spec.updated, before.spec.updated);                                    // nothing was written
    assert.deepEqual(same.steps.build.state, before.steps.build.state);                      // so a finished render is not made stale for nothing
    const x0 = await ev(`${E}.state.spec.components[3].location[0]`);
    await ev(`${E}.select([${E}.state.spec.components[3].id]); document.getElementById('stage').focus()`);
    await key('ArrowRight', 'ArrowRight', 39);
    await click('btnNew');                                                                     // straight away, before the save timer fires
    await waitPhase('empty');
    const saved = await api(`/api/jobs/${jobA}/spec`);
    assert.ok(Math.abs(saved.components[3].location[0] - (x0 + 0.05)) < 1e-9, 'the edit was written before the page left the job');
  });

  await step('"try the demo" loads a synthetic booth and its model with no key: before/after works at once', async () => {
    await click('btnNew');
    await waitPhase('empty');
    await click('btnTryDemo');
    await waitPhase('model');
    await waitFor(() => ev(`Boolean(${E}.viewport.matching) && ${E}.state.spec?.components.length === 79`), 'the demo model on its picture', 60000);
    assert.equal(await visible('.source-photo'), true);
    assert.equal((await api(`/api/jobs/${await jobId()}`)).spec.origin, 'uploaded');
    await shot('14_demo.png');
  });

  await step('the recent jobs are on the empty stage and one click opens one', async () => {
    await click('btnNew');
    await waitPhase('empty');
    await waitFor(() => visible('#recent button'), 'the recent jobs');
    assert.ok((await ev("document.querySelectorAll('#recent button').length")) >= 2);
    assert.equal(await ev("document.querySelector('#recent img').complete && document.querySelector('#recent img').naturalWidth > 0"), true);
    await shot('13_recent.png');
    await ev("document.querySelector('#recent button').click()");
    await waitFor(async () => (await phase()) !== 'empty', 'the job to open');
  });

  await step('leaving a job while its poll is in flight does not bring it back; a vanished server is said and forgiven', async () => {
    await ev(`(() => { const real = window.fetch; let release;
      window.fetch = (u, i) => (String(u).split('?')[0].split('/').length === 4 && String(u).includes('/api/jobs/') && (!i || !i.method || i.method === 'GET')) ? new Promise((res) => { release = () => res(real(u, i)); }) : real(u, i);
      window.__release = () => { window.fetch = real; release(); };
      window.studio.loadJob(window.studio.ui.current, { quiet: true }); })()`);
    await click('btnNew');
    await waitPhase('empty');
    await ev('window.__release()');
    await sleep(800);
    assert.equal(await ev('window.studio.ui.job'), null);
    assert.equal(await phase(), 'empty');
    const id = (await api('/api/jobs')).jobs[0].id;
    await goto(`/?job=${id}`);
    await ev("window.__real = window.fetch; window.fetch = () => Promise.reject(new TypeError('Failed to fetch'))");
    await ev(`window.studio.loadJob('${id}', { quiet: true })`);
    await waitFor(() => visible('#envChip'), 'the offline chip');
    assert.match(await text('#envChip'), /Cannot reach the local server/);
    await ev('window.fetch = window.__real');
    await waitFor(async () => !(await visible('#envChip')), 'the page to reconnect', 15000);
  });

  await step('no key or picture bytes were written into any job file', async () => {
    const bad = files(jobsDir).filter((p) => /\.(json|txt|md|log)$/.test(p) && ['sk-smoke-secret', 'sk-smoke-reject'].some((k) => readFileSync(p, 'utf8').includes(k)));
    assert.deepEqual(bad, []);
    const requests = files(jobsDir).filter((p) => p.endsWith('request.json'));
    assert.ok(requests.length >= 1);
    for (const r of requests) assert.ok(!readFileSync(r, 'utf8').includes('base64,'), r);
  });

  log(consoleErrors.length === 0, 'no console errors or exceptions', consoleErrors.slice(0, 3).join(' | '));
  cdp.close();
}

for (const sig of ['SIGINT', 'SIGTERM']) process.on(sig, () => { cleanup(); process.exit(130); });
try {
  await main();
} catch (e) {
  log(false, 'studio smoke run', e.message);
} finally {
  cleanup();
}
console.log(failures.length ? `\n${failures.length} FAILED` : `\nall studio steps passed; screenshots in ${shots}`);
process.exit(failures.length ? 1 : 0);
