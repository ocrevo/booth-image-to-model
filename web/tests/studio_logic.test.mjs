// What the studio decides from a job status: the plain-language conclusion, the one next action, the working and error cards.
// No DOM, no server: the job statuses below have the shape of GET /api/jobs/<id>.

import test from 'node:test';
import assert from 'node:assert/strict';
import { COPY, BANNED, findBanned, fill, mainStrings } from '../app/js/studio_copy.js';
import { deriveView, describeError, estimate, expectedText, formatElapsed, plainFit, DEFAULT_SECONDS, HD_MAX_PIXELS } from '../app/js/studio_logic.js';

const idle = () => ({ state: 'idle', message: '' });
function job(steps = {}, { spec = false, scene = false, width = 960, height = 600, origin = null } = {}) {
  return {
    id: '20260101-000000-abcd', image: { name: 'source.png', width, height },
    spec: { origin: spec ? origin || 'compiled' : null }, files: { scene, spec },
    steps: { scene: idle(), compile: idle(), preview: idle(), build: idle(), glb: idle(), ...steps },
  };
}
const ctx = (extra = {}) => ({ health: { blender: 'blender.exe', openai: { configured: true, model: 'm' } }, consent: true, durations: {}, now: 1000, ...extra });
const fitted = { fit: { status: 'fitted', rms_px: 0.5, max_px: 1, worst_points: [{ id: 'floor_bl', err_px: 1 }] }, components: 62, dropped: [], assumed_total: 0 };

test('the main path has no engineering words', () => {
  const strings = [...mainStrings()];
  assert.ok(strings.length > 50);
  for (const text of strings) assert.deepEqual(findBanned(text), [], `engineering words in main copy: ${text}`);
});

test('the banned-word check matches whole words in any case, and nothing harmless', () => {
  assert.ok(BANNED.length >= 10);
  assert.deepEqual(findBanned('Export the GLB and the Spec'), ['spec', 'GLB']);
  assert.deepEqual(findBanned('Off by 7 px'), ['px']);
  assert.deepEqual(findBanned('the camera fit is good'), ['fit', 'camera fit']);
  assert.deepEqual(findBanned('A specific scene description'), ['scene', 'scene description']);   // "specific" is not "spec"
  assert.deepEqual(findBanned('Off by {px} pixels'), []);                                          // a placeholder name is not text
  assert.deepEqual(findBanned('Profit, benefits and fitness are fine, so are forms and terms'), []);
  assert.deepEqual(findBanned('compile and compiling'), ['compile', 'compiling']);
});

test('every placeholder in the copy is one the code fills', () => {
  const known = new Set(['model', 'name', 'w', 'h', 'n', 'px', 'elapsed', 'expected', 'lo', 'hi', 'msg', 'id', 'mb']);
  for (const text of mainStrings()) for (const [, name] of text.matchAll(/\{(\w+)\}/g)) assert.ok(known.has(name), `{${name}} in: ${text}`);
  assert.equal(fill('{a}-{b}', { a: 1 }), '1-{b}');
});

test('waiting times: measured beats default, text is human', () => {
  assert.equal(estimate('scene', {}), DEFAULT_SECONDS.scene);
  assert.equal(estimate('scene', { scene: 47 }), 47);
  assert.equal(estimate('scene', { scene: 0 }), DEFAULT_SECONDS.scene);
  assert.equal(estimate('nonsense', {}), 30);
  assert.equal(formatElapsed(42), '0:42');
  assert.equal(formatElapsed(125.4), '2:05');
  assert.equal(formatElapsed(-3), '0:00');
  assert.equal(expectedText(6), 'a few seconds');
  assert.equal(expectedText(15), 'about 15 sec');
  assert.equal(expectedText(90), 'about 1–2 min');
  assert.equal(expectedText(300), 'about 4–6 min');
  assert.equal(expectedText(60), 'about 1 min');
});

test('the fit in plain words: good, off by a number, size unknown, dropped, assumed', () => {
  assert.deepEqual(plainFit(fitted), [{ tone: 'good', text: 'Model ready, 62 parts' }]);
  const off = plainFit({ ...fitted, fit: { status: 'high_residual', rms_px: 4.7, max_px: 7.2, worst_points: [{ id: 'top_left', err_px: 7.24 }] } });
  assert.equal(off[0].tone, 'warn');
  assert.match(off[0].text, /off by 7 pixels/);
  assert.equal(off[0].look, 'align');
  assert.equal(plainFit({ ...fitted, fit: { status: 'ambiguous' } })[0].look, 'data');
  const extra = plainFit({ ...fitted, dropped: [{ id: 'a' }, { id: 'b' }, { id: 'c' }], assumed_total: 5 });
  assert.deepEqual(extra.map((l) => l.tone), ['good', 'info', 'info']);
  assert.match(extra[1].text, /3 parts could not be added to the model/);
  assert.match(extra[2].text, /5 sizes are assumed values/);
  assert.deepEqual(plainFit(null), []);
  assert.deepEqual(plainFit({}), []);
  for (const line of [...off, ...extra]) assert.deepEqual(findBanned(line.text), [], line.text);
});

test('each failed step has one main action', () => {
  const read = describeError('scene', { message: 'OpenAI rejected this key' });
  assert.equal(read.primary.action, 'generate');
  assert.equal(read.secondary[0].action, 'moreWays');
  assert.equal(read.reason, COPY.main.errReadWhy);
  assert.equal(read.raw, 'OpenAI rejected this key');
  assert.equal(describeError('compile', {}).primary.action, 'compileAgain');                 // the picture is already read: no new reading, no consent
  assert.equal(describeError('preview', {}).primary.action, 'quickCheck');
  assert.equal(describeError('build', {}).primary.action, 'render');
  assert.equal(describeError('build', {}).secondary[0].action, 'proData');
  assert.equal(describeError('glb', {}).primary.action, 'exportGlb');
  for (const step of ['compile', 'preview', 'build', 'glb']) {
    const e = describeError(step, { message: 'Compile failed: spec preview GLB scene description' });
    assert.deepEqual(findBanned(e.reason), [], `${step}: ${e.reason}`);                // the server's engineering text never reaches the main card
    assert.ok(e.raw.includes('Compile failed'));
  }
  for (const step of ['scene', 'compile', 'preview', 'build', 'glb']) {
    const e = describeError(step, { message: 'x' });
    assert.ok(e.title && e.primary.label, step);
    assert.deepEqual(findBanned(e.title), [], e.title);
  }
});

test('what the large model service said is told in plain words, with the original kept apart', () => {
  const said = (message, detail) => describeError('scene', { message, detail });
  const key = said('OpenAI rejected this key (HTTP 401): Incorrect API key provided: sk-…', { http: 401 });
  assert.equal(key.reason, COPY.main.errKey);
  assert.match(key.raw, /HTTP 401/);
  assert.equal(key.secondary[0].label, COPY.main.changeKeyOrUpload);
  assert.equal(said('x', { http: 403 }).reason, COPY.main.errKey);
  assert.equal(said('x', { http: 429 }).reason, COPY.main.errBusy);
  assert.equal(said('Waited 900 seconds', { timeout: 900 }).reason, COPY.main.errSlow);
  assert.equal(said('x', { incomplete: 'length' }).reason, COPY.main.errCut);
  assert.equal(said('x', { refusal: 'no' }).reason, COPY.main.errRefused);
  assert.equal(said('Read 3 times', { errors: [{ code: 'SCOPE' }], attempts: [] }).reason, COPY.main.errUnusable);
  assert.equal(said('Cannot connect to http://x: boom', {}).reason, COPY.main.errNet);
  const unknown = said('something odd with compile and spec', {});
  assert.equal(unknown.reason, COPY.main.errReadWhy);                              // nothing recognised: still plain words; the original is kept apart
  assert.equal(unknown.raw, 'something odd with compile and spec');
  for (const text of [unknown.reason, key.reason, COPY.main.errBusy, COPY.main.errSlow, COPY.main.errCut, COPY.main.errRefused, COPY.main.errUnusable, COPY.main.errNet])
    assert.deepEqual(findBanned(text), [], text);
  assert.equal(describeError('compile', { message: 'x', detail: { http: 401 } }).reason, COPY.main.errBuildWhy);   // only the reading step has a service behind it
});

test('no job: an empty stage and nothing to do', () => {
  const v = deriveView(null, ctx());
  assert.equal(v.phase, 'empty');
  assert.equal(v.current, 1);
  assert.deepEqual(v.bar.map((b) => b.state), ['current', 'todo', 'todo', 'todo']);
  assert.equal(v.card.kind, 'empty');
});

test('a picture without a model: generate needs a key and the consent', () => {
  let v = deriveView(job(), ctx());
  assert.equal(v.phase, 'photo');
  assert.deepEqual(v.bar.map((b) => b.state), ['done', 'current', 'todo', 'todo']);
  assert.deepEqual([v.card.kind, v.card.canGenerate, v.card.canGenerateAll], ['generate', true, true]);
  v = deriveView(job(), ctx({ consent: false }));
  assert.deepEqual([v.card.canGenerate, v.card.canGenerateAll], [false, false]);
  v = deriveView(job(), ctx({ health: { blender: null, openai: { configured: true } } }));
  assert.deepEqual([v.card.canGenerate, v.card.canGenerateAll], [true, false]);                     // no Blender: no "straight to the picture"
  v = deriveView(job(), ctx({ health: { blender: 'b', openai: { configured: false } } }));
  assert.deepEqual([v.card.needKey, v.card.canGenerate], [true, false]);
});

test('while the model is being made the card shows what and for how long', () => {
  const v = deriveView(job({ scene: { state: 'running', message: 'Running', started: 958 }, compile: { state: 'queued', message: 'Waiting for the earlier steps' } }), ctx());
  assert.equal(v.card.kind, 'working');
  assert.equal(v.card.text, 'Reading the picture…');
  assert.equal(v.card.elapsed, 42);
  assert.equal(v.card.progress, '0:42 elapsed, usually about 1–2 min');
  assert.equal(v.card.slow, false);
  assert.equal(v.bar[1].state, 'running');
  assert.equal(v.working, true);
  const slow = deriveView(job({ scene: { state: 'running', message: '', started: 1000 - 400 } }), ctx());
  assert.equal(slow.card.slow, true);
});

test('queued behind another Blender job says so instead of counting', () => {
  const v = deriveView(job({ build: { state: 'running', message: 'Waiting for Blender (only one runs at a time)', started: 990 } }, { spec: true }), ctx());
  assert.equal(v.card.text, 'Waiting: another job is using Blender');
  assert.equal(v.card.progress, '');
  assert.equal(v.card.queued, true);
});

test('a failed reading: the reason, one main action, an alternative, and what the retry needs', () => {
  let v = deriveView(job({ scene: { state: 'error', message: 'The large model returned something unusable' } }), ctx());
  assert.equal(v.card.kind, 'error');
  assert.equal(v.card.primary.action, 'generate');
  assert.equal(v.card.reason, COPY.main.errReadWhy);
  assert.equal(v.card.raw, 'The large model returned something unusable');
  assert.deepEqual([v.card.needKey, v.card.canRetry], [false, true]);
  v = deriveView(job({ scene: { state: 'error', message: 'x' } }), ctx({ consent: false }));
  assert.deepEqual([v.card.canRetry, v.card.consent], [false, false]);                          // the retry needs the consent again
  v = deriveView(job({ scene: { state: 'error', message: 'x' } }), ctx({ health: { blender: 'b', openai: { configured: false } } }));
  assert.deepEqual([v.card.needKey, v.card.canRetry], [true, false]);
  v = deriveView(job({ scene: { state: 'done' }, compile: { state: 'error', message: 'Compile failed' } }), ctx({ consent: false }));
  assert.deepEqual([v.card.primary.action, v.card.canRetry], ['compileAgain', true]);           // compiling again needs no new consent
  v = deriveView(job({ scene: { state: 'error', message: 'The large model returned something unusable' } }), ctx());
  assert.equal(v.bar[1].state, 'error');
  assert.equal(v.current, 2);
});

test('a leftover compile error does not hide a job that has its model', () => {
  const v = deriveView(job({ scene: { state: 'skipped' }, compile: { state: 'error', message: 'old click' } }, { spec: true, origin: 'uploaded' }), ctx());
  assert.equal(v.phase, 'model');
  assert.equal(v.card.kind, 'model');
});

test('a model: conclusion, render options, nothing to download yet', () => {
  const v = deriveView(job({ scene: { state: 'done' }, compile: { state: 'done', detail: fitted } }, { spec: true }), ctx());
  assert.equal(v.phase, 'model');
  assert.equal(v.card.kind, 'model');
  assert.equal(v.card.fit[0].text, 'Model ready, 62 parts');
  assert.deepEqual(v.bar.map((b) => b.state), ['done', 'done', 'current', 'todo']);
  assert.deepEqual([v.render.can, v.render.hdAllowed, v.render.built], [true, true, false]);
  assert.deepEqual(v.downloads, []);
  assert.deepEqual(v.compare, { live: true, check: null, final: null });
  assert.equal(v.canExportGlb, true);
});

test('high definition is offered only when twice the picture stays under the cap', () => {
  const hd = (width, height) => deriveView(job({}, { spec: true, width, height }), ctx()).render.hdAllowed;
  assert.equal(HD_MAX_PIXELS, 1_250_000);
  assert.equal(hd(1250, 1000), true);                                                           // exactly 1.25 MP: 2x is the 5 MP cap
  assert.equal(hd(1251, 1000), false);
  assert.equal(hd(1000, 1000), true);
  assert.equal(hd(1600, 1200), false);
  assert.equal(deriveView(job({}, { spec: true, width: 640, height: 480 }), ctx({ health: { blender: null } })).render.hdAllowed, false);
});

test('without Blender the journey stops before the picture but the model file can still be exported', () => {
  const v = deriveView(job({ compile: { state: 'done', detail: fitted } }, { spec: true }), ctx({ health: { blender: null, openai: { configured: true } } }));
  assert.deepEqual([v.render.can, v.canExportGlb, v.blender], [false, true, false]);
});

test('with a model every failure is a notice on the model card: render, check and downloads stay reachable', () => {
  const files = { blend: 'b/booth.blend', client: 'b/client_render.png' };
  const v = deriveView(job({ compile: { state: 'done', detail: fitted }, preview: { state: 'error', message: 'The Blender preview was not made' } }, { spec: true }), ctx());
  assert.equal(v.card.kind, 'model');
  assert.equal(v.card.notice.primary.action, 'quickCheck');
  assert.equal(v.render.can, true);
  const glb = deriveView(job({ compile: { state: 'done', detail: fitted }, build: { state: 'done', detail: { files } }, glb: { state: 'error', message: 'GLB export failed' } }, { spec: true }), ctx());
  assert.equal(glb.card.kind, 'model');
  assert.equal(glb.card.notice.primary.action, 'exportGlb');
  assert.deepEqual(glb.downloads.map((d) => d.key), ['client', 'blend']);                       // the finished picture can still be downloaded
  assert.equal(glb.bar[3].state, 'error');                                                      // and the step bar still says something failed
  const build = deriveView(job({ build: { state: 'error', message: 'Blender build failed' } }, { spec: true }), ctx());
  assert.equal(build.card.kind, 'model');
  assert.equal(build.card.notice.primary.action, 'render');
  assert.equal(build.bar[3].state, 'error');
});

test('a high-definition render is expected to take longer', () => {
  assert.equal(estimate('build', {}, { hd: true }), DEFAULT_SECONDS.build * 3);
  assert.equal(estimate('build', {}, { hd: false }), DEFAULT_SECONDS.build);
  assert.equal(estimate('glb', {}, { hd: true }), DEFAULT_SECONDS.glb);
  const v = deriveView(job({ build: { state: 'running', started: 900 } }, { spec: true }), ctx({ hd: true }));
  assert.equal(v.card.expected, DEFAULT_SECONDS.build * 3);
});

test('both problems with the fit are said, not just one', () => {
  const both = plainFit({ ...fitted, fit: { status: 'ambiguous', rms_px: 6, max_px: 9, worst_points: [{ id: 'a', err_px: 9 }] } });
  assert.deepEqual(both.map((l) => l.look), ['data', 'align']);
  assert.match(both[0].text, /sizes/);
  assert.doesNotMatch(both[0].text, /estimat/);                                                    // nothing is claimed that does not happen
  assert.equal(plainFit({ ...fitted, fit: { status: 'ambiguous', rms_px: 1 } }).length, 1);
});

test('after a render: downloads in order, the picture first; compare sources appear', () => {
  const build = { state: 'done', detail: { files: { blend: 'build_001/booth.blend', client: 'build_001/client_render.png', reference: 'build_001/reference_render.png', inspection: 'build_001/inspection_render.png' } } };
  const v = deriveView(job({ compile: { state: 'done', detail: fitted }, preview: { state: 'done', detail: { render: 'preview/preview_001/preview_render.png' } }, build,
    glb: { state: 'done', detail: { glb: 'exports/booth.glb' } } }, { spec: true }), ctx());
  assert.deepEqual(v.downloads.map((d) => d.key), ['client', 'inspection', 'glb', 'blend']);
  assert.equal(v.downloads[0].primary, true);
  assert.deepEqual(v.bar.map((b) => b.state), ['done', 'done', 'done', 'done']);
  assert.equal(v.compare.check, 'preview/preview_001/preview_render.png');
  assert.deepEqual(v.compare.final, { wipe: 'build_001/client_render.png', overlay: 'build_001/reference_render.png' });
});

test('after an edit the render is stale: marked, still downloadable, not offered for comparison', () => {
  const files = { blend: 'b/booth.blend', client: 'b/client_render.png' };
  const v = deriveView(job({ preview: { state: 'stale', detail: { render: 'p/preview_render.png' } }, build: { state: 'stale', detail: { files } },
    glb: { state: 'stale', detail: { glb: 'exports/booth.glb' } } }, { spec: true }), ctx());
  assert.equal(v.bar[3].state, 'stale');
  assert.equal(v.render.stale, true);
  assert.equal(v.compare.check, null);
  assert.equal(v.compare.final, null);
  assert.deepEqual(v.downloads.map((d) => [d.key, Boolean(d.stale)]), [['client', true], ['glb', true], ['blend', true]]);   // still real files: offered, marked stale
});

test('the copy has a name for every step mark', () => {
  assert.equal(COPY.main.stepNames.length, 4);
  assert.deepEqual(deriveView(job(), ctx()).bar.map((b) => b.label), COPY.main.stepNames);
});
