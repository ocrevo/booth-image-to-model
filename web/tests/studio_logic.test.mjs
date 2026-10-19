// What the studio decides from a job status: the plain-language conclusion, the one next action, the working and error cards.
// No DOM, no server: the job statuses below have the shape of GET /api/jobs/<id>.

import test from 'node:test';
import assert from 'node:assert/strict';
import { COPY, BANNED, findBanned, fill, mainStrings } from '../app/js/studio_copy.js';
import { deriveView, describeError, estimate, expectedText, failedTopologyChecks, formatElapsed, gateOf, nextChain, plainFit, scaleCheckFailed, topologyLines, DEFAULT_SECONDS, HD_MAX_PIXELS } from '../app/js/studio_logic.js';

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
// What the compile step reports about the independent checks (quality_gate is the engine's verdict, passed on unchanged). The usual
// reading has every corner in the fit (n_check 0) and each corner predicted from the others (leave-one-out); the scale checks agree.
const checks = { n_fit: 8, n_check: 0, check_points: [], check_median_px: null, check_max_px: null,
  loo_reliable: true, loo_errors_px: { floor_bl: 0.1 }, loo_median_px: 0.1, loo_max_px: 0.2, outliers: [], scale_checks: [] };
// Corners held out by hand: too few left in the fit for leave-one-out, but the held-out corners are the independent check.
const heldOut = { n_fit: 6, n_check: 2, check_points: [{ id: 'top_br', err_px: 0.3 }, { id: 'floor_fl', err_px: 0.2 }], check_median_px: 0.25, check_max_px: 0.3,
  loo_reliable: false, loo_errors_px: null, loo_median_px: null, loo_max_px: null, outliers: [] };
const passing = { ...fitted, fit: { ...fitted.fit, ...checks }, quality_gate: { status: 'pass', reasons: [] } };
const reviewing = (fit = {}, reasons = ['the engine words its own reason here']) => ({
  ...fitted, fit: { ...fitted.fit, ...checks, ...fit }, quality_gate: { status: 'review', reasons } });
const offScale = [{ id: 'table_height', expected_m: 1.5, measured_m: 0.74, ratio: 0.49, tolerance: 0.15, ok: false }];
// What the compile step reports about the booth layout (`topology`: the type and open sides the reading stated, and the engine's checks
// of them). A reading with no layout, or an engine without the check, has none.
const layoutChecks = (status = {}) => ['type_matches_sides', 'wall_on_open_side', 'closed_side_without_wall', 'camera_side'].map((id) => ({
  id, status: status[id] ?? (id === 'closed_side_without_wall' ? 'warn' : 'ok'), message: `the engine words ${id} here` }));
const layout = (status = {}, extra = {}) => ({ booth_type: 'peninsula', open_sides: ['front', 'left', 'right'], cutaway_sides: [], checks: layoutChecks(status), ...extra });
const withLayout = (detail, status = {}, extra = {}) => ({ ...detail, topology: layout(status, extra) });
const layoutReasons = {
  type_matches_sides: 'the booth type does not match the open sides',
  wall_on_open_side: 'a wall was placed on an open side and left out',
  camera_side: 'the camera looks in through closed sides: front/back or left/right may be swapped',
};
// A model whose layout failed some checks: the gate says review with the engine's own reasons, and the corner fit itself is fine.
const layoutFailing = (...ids) => withLayout(reviewing({}, ids.map((id) => layoutReasons[id])), Object.fromEntries(ids.map((id) => [id, 'fail'])));

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

test('the quality verdict: read as the engine sends it, ignored when it is not one', () => {
  assert.deepEqual(gateOf(passing), { status: 'pass', reasons: [] });
  assert.deepEqual(gateOf(reviewing()), { status: 'review', reasons: ['the engine words its own reason here'] });
  assert.equal(gateOf(fitted), null);                                                      // an engine without the verdict: nothing is held back
  assert.equal(gateOf({ quality_gate: { status: 'maybe' } }), null);
  assert.equal(gateOf(null), null);
  assert.deepEqual(gateOf({ quality_gate: { status: 'review' } }), { status: 'review', reasons: [] });
});

test('a passing check keeps the plain "ready" line; held-out corners that agree are an independent check on their own', () => {
  assert.deepEqual(plainFit(passing), [{ tone: 'good', text: 'Model ready, 62 parts' }]);
  const byHand = { ...fitted, fit: { ...fitted.fit, ...heldOut }, quality_gate: { status: 'pass', reasons: [] } };
  assert.deepEqual(plainFit(byHand), [{ tone: 'good', text: 'Model ready, 62 parts' }]);
});

test('a review says why in plain words, from the fit keys, and never calls the model ready', () => {
  const failed = plainFit(reviewing({ status: 'check_failed', loo_median_px: 9, loo_max_px: 30 }));      // the corners do not predict each other
  assert.equal(failed[0].tone, 'info');
  assert.match(failed[0].text, /Compare it with the picture/);
  assert.equal(failed[1].tone, 'warn');
  assert.match(failed[1].text, /do not agree with each other well enough/);
  assert.equal(failed[1].look, 'align');
  const failedHeld = plainFit(reviewing({ ...heldOut, status: 'check_failed', check_median_px: 20, check_max_px: 31 }));
  assert.deepEqual(failedHeld.filter((l) => l.tone === 'warn').map((l) => l.text), [COPY.main.gateWhyChecks]);   // held-out corners that miss: the same plain note
  const outlier = plainFit(reviewing({ status: 'check_failed', outliers: ['top_fl'] }));
  assert.deepEqual(outlier.filter((l) => l.tone === 'warn').map((l) => l.text), [COPY.main.gateWhyOutlier]);     // named corners: the more specific note only
  assert.match(outlier.find((l) => l.tone === 'warn').text, /may be in the wrong place/);
  const scale = plainFit(reviewing({ scale_checks: offScale }));                                      // the corners agree, the scale does not
  assert.deepEqual(scale.filter((l) => l.tone === 'warn').map((l) => l.text), [COPY.main.gateWhyScale]);
  assert.match(scale.find((l) => l.tone === 'warn').text, /table, counter or chair/);
  assert.equal(scale[0].tone, 'info');                                                                // not "Model ready"
  const bothWrong = plainFit(reviewing({ outliers: ['top_fl'], scale_checks: offScale }));
  assert.deepEqual(bothWrong.filter((l) => l.tone === 'warn').map((l) => l.text), [COPY.main.gateWhyOutlier, COPY.main.gateWhyScale]);
  const few = plainFit(reviewing({ n_fit: 6, loo_reliable: false, loo_errors_px: null, loo_median_px: null, loo_max_px: null }));   // nothing independent was possible
  assert.deepEqual(few.filter((l) => l.tone === 'warn').map((l) => l.text), [COPY.main.gateWhyNoCheck]);
  assert.match(few.find((l) => l.tone === 'warn').text, /Too few corners/);
  const olderEngine = plainFit(reviewing({ n_check: undefined, loo_median_px: undefined }));            // an engine that reports neither: say nothing it cannot know
  assert.deepEqual(olderEngine.filter((l) => l.tone === 'warn').map((l) => l.text), [COPY.main.gateWhyGeneric]);
  const unknownWhy = plainFit(reviewing());                                                // fitted, numbers present, verdict review: the generic line
  assert.equal(unknownWhy.filter((l) => l.tone === 'warn').length, 1);
  assert.match(unknownWhy[1].text, /could not confirm that the model matches the picture/);
  const ambiguous = plainFit(reviewing({ status: 'ambiguous' }));                         // the size line already says it: no second, generic one
  assert.deepEqual(ambiguous.map((l) => l.look), ['data']);
  const off = plainFit(reviewing({ status: 'high_residual', rms_px: 6, max_px: 9, worst_points: [{ id: 'a', err_px: 9 }] }));
  assert.equal(off.filter((l) => l.tone === 'warn').length, 1);
  for (const line of [...failed, ...failedHeld, ...outlier, ...scale, ...bothWrong, ...few, ...olderEngine, ...unknownWhy, ...ambiguous, ...off]) {
    assert.deepEqual(findBanned(line.text), [], line.text);
  }
  for (const key of ['gateWhyChecks', 'gateWhyOutlier', 'gateWhyScale', 'gateWhyNoCheck', 'gateWhyGeneric', 'gateHeldTitle', 'gateHeld', 'renderAnyway', 'startAgain']) {
    assert.deepEqual(findBanned(COPY.main[key]), [], key);
  }
  assert.doesNotMatch(JSON.stringify([failed, outlier, scale, few]), /px|leave-one-out|check_failed|the engine words/);    // the engine's own wording stays in the Advanced view
});

test('a failed scale check is a failed scale check, however the engine spells the entry', () => {
  assert.equal(scaleCheckFailed({ scale_checks: offScale }), true);
  assert.equal(scaleCheckFailed({ scale_checks: [{ id: 'a', ok: true }, { id: 'b', ok: false, error: 'cannot be measured' }] }), true);   // one that could not be measured is not an agreement
  assert.equal(scaleCheckFailed({ scale_checks: [{ id: 'a', ok: true }] }), false);
  for (const fit of [{ scale_checks: [] }, { scale_checks: null }, {}, null, undefined]) assert.equal(scaleCheckFailed(fit), false);
});

test('the pause promises nothing the studio cannot do: render anyway, or start again', () => {
  const text = COPY.main.gateHeld;
  assert.doesNotMatch(text, /\b(fix|correct|adjust|repair|edit)\b/i);                                  // the studio cannot move a corner
  assert.match(text, /render anyway/i);
  assert.match(text, /start again with another picture or your own file/i);
  assert.equal(COPY.main.renderAnyway, 'Render anyway');
  assert.equal(COPY.main.startAgain, 'Start again');
  assert.deepEqual(findBanned(text), []);
});

test('one-click render: the model first, then the render only when the checks agree', () => {
  const done = { state: 'done', message: '' };
  const model = (gate, over = {}) => job({ scene: done, compile: { state: 'done', detail: gate }, ...over }, { spec: true });
  assert.deepEqual(nextChain(model(passing)), { action: 'render' });
  assert.deepEqual(nextChain(model(fitted)), { action: 'render' });                        // no verdict from the engine: as before, go on
  assert.deepEqual(nextChain(model(reviewing())), { action: 'hold' });                     // review: stop before the render
  assert.deepEqual(nextChain(job({ scene: { state: 'running', started: 1 }, compile: { state: 'queued' } })), { action: 'wait' });
  assert.deepEqual(nextChain(job({ scene: done, compile: { state: 'running', started: 1 } })), { action: 'wait' });
  assert.deepEqual(nextChain({ ...model(passing), busy: 'compile' }), { action: 'wait' });
  assert.deepEqual(nextChain(job({ scene: { state: 'error', message: 'x' } })), { action: 'stop' });
  assert.deepEqual(nextChain(job({ scene: done, compile: { state: 'error', message: 'x' } })), { action: 'stop' });
  assert.deepEqual(nextChain(job()), { action: 'stop' });
  assert.deepEqual(nextChain(null), { action: 'stop' });
});

test('a paused one-click render shows the pause and nothing else changes; a finished render ends it', () => {
  const compile = { state: 'done', detail: reviewing({ status: 'check_failed' }) };
  const base = job({ scene: { state: 'done' }, compile }, { spec: true });
  const held = deriveView(base, ctx({ held: true }));
  assert.deepEqual([held.card.kind, held.card.gate, held.card.held], ['model', 'review', true]);
  assert.deepEqual([held.render.can, held.canExportGlb], [true, true]);                     // the render, the quick check and the export stay reachable
  const free = deriveView(base, ctx());
  assert.deepEqual([free.card.gate, free.card.held], ['review', false]);                    // a review verdict alone only warns: nothing was paused
  assert.equal(free.card.fit.some((l) => l.tone === 'warn'), true);
  const pass = deriveView(job({ scene: { state: 'done' }, compile: { state: 'done', detail: passing } }, { spec: true }), ctx({ held: true }));
  assert.deepEqual([pass.card.gate, pass.card.held], ['pass', false]);                      // nothing to decide when the checks agree
  const files = { blend: 'b/booth.blend', client: 'b/client_render.png' };
  const rendered = deriveView(job({ scene: { state: 'done' }, compile, build: { state: 'done', detail: { files } } }, { spec: true }), ctx({ held: true }));
  assert.equal(rendered.card.held, false);                                                  // rendered anyway: the pause is over
});

test('a layout check that failed is said in plain words, and a passing or information-only one is not', () => {
  const type = plainFit(layoutFailing('type_matches_sides'));
  assert.equal(type[0].tone, 'info');                                                       // the corner fit is fine: not "ready", and not an alarm of its own
  assert.deepEqual(type.filter((l) => l.tone === 'warn').map((l) => l.text), [COPY.main.gateWhyTopologyType]);
  assert.match(type.find((l) => l.tone === 'warn').text, /^The booth type does not match the open sides/);
  assert.equal(type.find((l) => l.tone === 'warn').look, 'align');
  const wall = plainFit(layoutFailing('wall_on_open_side'));
  assert.deepEqual(wall.filter((l) => l.tone === 'warn').map((l) => l.text), [COPY.main.gateWhyWallOpen]);
  assert.match(wall.find((l) => l.tone === 'warn').text, /^A wall was placed on an open side and left out/);
  const mirrored = plainFit(layoutFailing('camera_side'));
  assert.deepEqual(mirrored.filter((l) => l.tone === 'warn').map((l) => l.text), [COPY.main.gateWhyMirrored]);
  assert.match(mirrored.find((l) => l.tone === 'warn').text, /^The picture seems to be read back to front or left to right/);
  const all = plainFit(layoutFailing('type_matches_sides', 'wall_on_open_side', 'camera_side'));
  assert.deepEqual(all.filter((l) => l.tone === 'warn').map((l) => l.text), [COPY.main.gateWhyTopologyType, COPY.main.gateWhyWallOpen, COPY.main.gateWhyMirrored]);
  for (const line of [...type, ...wall, ...mirrored, ...all]) assert.deepEqual(findBanned(line.text), [], line.text);
  for (const key of ['gateWhyTopologyType', 'gateWhyWallOpen', 'gateWhyMirrored']) assert.deepEqual(findBanned(COPY.main[key]), [], key);
  assert.doesNotMatch(JSON.stringify([type, wall, mirrored]), /type_matches_sides|wall_on_open_side|camera_side|the engine words|swapped/);   // the engine's own wording stays in the Advanced view
  const fine = plainFit(withLayout(passing));                                               // all ok, and the information-only check warns: still just "ready"
  assert.deepEqual(fine, [{ tone: 'good', text: 'Model ready, 62 parts' }]);
  const unchecked = plainFit(withLayout(passing, { type_matches_sides: 'not_checked', wall_on_open_side: 'not_checked', camera_side: 'not_checked' }));
  assert.deepEqual(unchecked, [{ tone: 'good', text: 'Model ready, 62 parts' }]);
});

test('a layout failure comes before the corner notes, and an unknown failed check falls back to the generic line', () => {
  const both = plainFit(withLayout(reviewing({ status: 'check_failed', outliers: ['top_fl'], scale_checks: offScale }), { camera_side: 'fail' }));
  assert.deepEqual(both.filter((l) => l.tone === 'warn').map((l) => l.text), [COPY.main.gateWhyMirrored, COPY.main.gateWhyOutlier, COPY.main.gateWhyScale]);
  const odd = withLayout(reviewing(), {});
  odd.topology.checks.push({ id: 'something_new', status: 'fail', message: 'a check a later engine adds' });
  assert.deepEqual(failedTopologyChecks(odd), ['something_new']);
  assert.deepEqual(plainFit(odd).filter((l) => l.tone === 'warn').map((l) => l.text), [COPY.main.gateWhyGeneric]);
  assert.deepEqual(failedTopologyChecks(layoutFailing('camera_side', 'type_matches_sides')), ['type_matches_sides', 'camera_side']);
  for (const detail of [fitted, passing, withLayout(passing), { topology: null }, { topology: { checks: 'no' } }, { topology: { checks: [null, { id: 'a', status: 'ok' }] } }, null, undefined]) {
    assert.deepEqual(failedTopologyChecks(detail), [], JSON.stringify(detail));
  }
  const noLayout = plainFit(reviewing({ outliers: ['top_fl'] }));                           // an engine without the layout check: the notes are unchanged
  assert.deepEqual(noLayout.filter((l) => l.tone === 'warn').map((l) => l.text), [COPY.main.gateWhyOutlier]);
});

test('one-click render: a failed layout check holds the render like any other review, and a good layout does not', () => {
  const done = { state: 'done', message: '' };
  const model = (detail) => job({ scene: done, compile: { state: 'done', detail } }, { spec: true });
  for (const ids of [['type_matches_sides'], ['wall_on_open_side'], ['camera_side'], ['type_matches_sides', 'camera_side']]) {
    assert.deepEqual(nextChain(model(layoutFailing(...ids))), { action: 'hold' }, ids.join());
  }
  assert.deepEqual(nextChain(model(withLayout(passing))), { action: 'render' });             // every layout check ok (or information only): on to the render
  const held = deriveView(model(layoutFailing('camera_side')), ctx({ held: true }));
  assert.deepEqual([held.card.kind, held.card.gate, held.card.held], ['model', 'review', true]);
  assert.deepEqual([held.render.can, held.canExportGlb], [true, true]);                      // render anyway, the quick check and the export stay reachable
  assert.equal(held.card.fit.some((l) => l.tone === 'warn' && l.text === COPY.main.gateWhyMirrored), true);
  const free = deriveView(model(layoutFailing('camera_side')), ctx());
  assert.deepEqual([free.card.gate, free.card.held], ['review', false]);
});

test('the Advanced view lists what the reading said about the layout and every check with its status', () => {
  const ok = topologyLines(layout());
  assert.equal(ok.length, 3);                                                                // the reading, the checks, and the information-only warn in the engine's words
  assert.equal(ok[0], 'Booth layout read from the picture: type peninsula, open sides front, left, right, cutaway sides none');
  assert.equal(ok[1], 'Layout checks: type_matches_sides ok, wall_on_open_side ok, closed_side_without_wall warn, camera_side ok');
  assert.equal(ok[2], 'Layout check closed_side_without_wall (warn): the engine words closed_side_without_wall here');
  const failing = topologyLines(layout({ camera_side: 'fail', type_matches_sides: 'not_checked' }, { cutaway_sides: ['back'] }));
  assert.match(failing[0], /cutaway sides back$/);
  assert.match(failing[1], /type_matches_sides not_checked.*camera_side fail$/);
  assert.match(failing[failing.length - 1], /^Layout check camera_side \(fail\): the engine words camera_side here$/);
  const typeOnly = topologyLines({ booth_type: 'inline', open_sides: null, cutaway_sides: null, checks: [] });
  assert.deepEqual(typeOnly, ['Booth layout: the reading said type inline but did not state the open sides, so the layout checks did not run.']);
  const sidesOnly = topologyLines({ booth_type: null, open_sides: ['front'], cutaway_sides: null, checks: [] });
  assert.deepEqual(sidesOnly, ['Booth layout read from the picture: type not stated, open sides front, cutaway sides not stated']);
  const none = topologyLines({ booth_type: null, open_sides: null, cutaway_sides: null,
    checks: ['type_matches_sides', 'wall_on_open_side', 'closed_side_without_wall', 'camera_side'].map((id) => ({ id, status: 'not_checked', message: 'not stated' })) });
  assert.match(none[0], /did not state a type or the open sides, so the layout checks did not run/);
  assert.match(none[1], /camera_side not_checked$/);
  assert.equal(none.length, 2);                                                              // not_checked is not a warning: no extra lines
  for (const empty of [null, undefined, 'x', 3]) assert.deepEqual(topologyLines(empty), []);   // an engine or a job without the layout: nothing to list
  assert.deepEqual(topologyLines({ booth_type: 'other', open_sides: ['front', 'back'], cutaway_sides: [], checks: [null, { status: 'ok' }] }),
    ['Booth layout read from the picture: type other, open sides front, back, cutaway sides none']);   // damaged check entries are skipped, not a crash
});
