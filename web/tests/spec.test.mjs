import { fileURLToPath } from 'node:url';
import { PYTHON } from './python.mjs';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync, mkdtempSync, writeFileSync, copyFileSync, rmSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { transformPoint, meshFor } from '../app/js/geometry.js';
import * as S from '../app/js/spec.js';

const DEMO = new URL('../app/demo/', import.meta.url);
const demo = () => JSON.parse(readFileSync(new URL('demo-booth.spec.json', DEMO), 'utf8'));
const reference = JSON.parse(readFileSync(new URL('./fixtures/geometry_reference.json', import.meta.url), 'utf8'));
const close = (a, b, tol, label) => assert.ok(Math.abs(a - b) <= tol, `${label}: ${a} vs ${b}`);

function worldPoint(spec, id, local = [0.01, 0.02, 0.03]) {
  const c = S.findComponent(spec, id);
  return transformPoint(local, c.location, c.rotation_deg);
}

test('PARAM_KEYS matches the Python validator table', () => {
  const js = Object.fromEntries(Object.entries(S.PARAM_KEYS).map(([k, v]) => [k, [[...v[0]].sort(), [...v[1]].sort()]]));
  assert.deepEqual(js, reference.param_keys);
});

test('the demo spec passes the light validation and has groups', () => {
  const spec = demo();
  assert.deepEqual(S.lightValidate(spec), []);
  assert.ok(S.groupNames(spec).includes('table_a'));
  assert.deepEqual(S.expandToGroups(spec, ['tv_frame']).sort(), S.groupMembers(spec, 'tv').sort());
  assert.deepEqual(S.expandToGroups(spec, ['tv_frame'], true), ['tv_frame']);
});

test('translate moves a group together and marks the parts as edited', () => {
  const spec = demo();
  const ids = S.groupMembers(spec, 'counter');
  const before = ids.map((id) => worldPoint(spec, id));
  const r = S.tryEdit(spec, (s) => S.translate(s, ids, [0.5, -0.25, 0]));
  assert.ok(r.ok);
  ids.forEach((id, i) => {
    const now = worldPoint(spec, id);
    close(now[0], before[i][0] + 0.5, 1e-9, 'x');
    close(now[1], before[i][1] - 0.25, 1e-9, 'y');
    const c = S.findComponent(spec, id);
    assert.equal(c.evidence.status, 'inferred');
    assert.match(c.evidence.note, /Edited in the web editor/);
  });
  assert.equal(spec.camera.fit_status, 'unfitted');
});

test('rotateYaw turns every point of a group about the pivot, including tilted parts', () => {
  const spec = demo();
  const ids = S.groupMembers(spec, 'chair_a1');
  const tilted = S.findComponent(spec, ids[1]);
  tilted.rotation_deg = [12, -7, tilted.rotation_deg[2]];       // make the check meaningful for rx, ry
  const pivot = [1.0, 0.5];
  const before = ids.map((id) => worldPoint(spec, id));
  assert.ok(S.tryEdit(spec, (s) => S.rotateYaw(s, ids, 90, pivot)).ok);
  ids.forEach((id, i) => {
    const [x, y, z] = before[i];
    const want = [pivot[0] - (y - pivot[1]), pivot[1] + (x - pivot[0]), z];      // Rz(+90) about the pivot
    const got = worldPoint(spec, id);
    want.forEach((w, k) => close(got[k], w, 5e-4, `${id}[${k}]`));
  });
});

test('scaleSelection scales positions and lengths about the pivot', () => {
  for (const group of ['chair_b1', 'table_a', 'planter', 'header']) {
    const spec = demo();
    const ids = S.groupMembers(spec, group);
    const b0 = S.selectionBounds(spec, ids);
    const pivot = b0.centre;
    const r = S.tryEdit(spec, (s) => S.scaleSelection(s, ids, 1.25, pivot));
    assert.ok(r.ok, r.error);
    const b1 = S.selectionBounds(spec, ids);
    for (let i = 0; i < 3; i++) {
      close(b1.min[i], pivot[i] + (b0.min[i] - pivot[i]) * 1.25, 2e-3, `${group} min${i}`);
      close(b1.max[i], pivot[i] + (b0.max[i] - pivot[i]) * 1.25, 2e-3, `${group} max${i}`);
    }
  }
  const spec = demo();
  assert.equal(S.tryEdit(spec, (s) => S.scaleSelection(s, ['tv_frame'], 0.01, [0, 0, 0])).ok, false);
});

test('a refused edit leaves the spec untouched', () => {
  const spec = demo();
  const before = structuredClone(spec);
  const r = S.tryEdit(spec, (s) => S.setParam(s, 'table_a_top', ['radius'], -1));
  assert.equal(r.ok, false);
  assert.match(r.error, /table_a_top.*positive/);
  assert.deepEqual(spec, before);
  const tooBig = S.tryEdit(spec, (s) => S.setParam(s, 'counter_body', ['bevel_radius'], 5));
  assert.equal(tooBig.ok, false);
  assert.deepEqual(spec, before);
});

test('resizing the floor keeps the footprint in step', () => {
  const spec = demo();
  assert.ok(S.tryEdit(spec, (s) => S.setParam(s, 'floor', ['size', 0], 7.5)).ok);
  assert.deepEqual(spec.scale.footprint_m, [7.5, 5]);
  assert.deepEqual(S.lightValidate(spec), []);
});

test('duplicate copies a group under new, unique names', () => {
  const spec = demo();
  const ids = S.groupMembers(spec, 'table_a');
  let created;
  assert.ok(S.tryEdit(spec, (s) => { created = S.duplicate(s, ids); return created; }).ok);
  assert.equal(created.length, ids.length);
  assert.equal(new Set(spec.components.map((c) => c.id)).size, spec.components.length);
  const copy = S.findComponent(spec, created[0]);
  assert.equal(copy.group, 'table_a_2');
  assert.equal(copy.critical, false);
  assert.match(copy.evidence.note, /Duplicated in the web editor from/);
  assert.equal(copy.location[0], S.findComponent(spec, ids[0]).location[0] + 0.6);
  assert.deepEqual(S.lightValidate(spec), []);
  let again;                                           // a second copy must not collide
  assert.ok(S.tryEdit(spec, (s) => { again = S.duplicate(s, ids); return again; }).ok);
  assert.equal(S.findComponent(spec, again[0]).group, 'table_a_3');
});

test('remove cleans requirements, landmarks and empty scopes', () => {
  const spec = demo();
  assert.equal(S.tryEdit(spec, (s) => { S.remove(s, ['floor'], { force: true }); }).ok, false);
  assert.equal(S.tryEdit(spec, (s) => { S.remove(s, ['back_wall']); }).ok, false);              // critical needs confirmation
  let summary;
  assert.ok(S.tryEdit(spec, (s) => { summary = S.remove(s, ['back_wall'], { force: true }); }).ok);
  assert.equal(summary.requirements, 1);
  assert.ok(spec.requirements.every((r) => r.component_id !== 'back_wall'));
  const media = [...S.groupMembers(spec, 'tv'), ...S.groupMembers(spec, 'led')];
  assert.ok(S.tryEdit(spec, (s) => { summary = S.remove(s, media); }).ok);
  assert.deepEqual(summary.scopes, ['media']);
  assert.deepEqual(S.lightValidate(spec), []);
});

test('light validation catches the mistakes the Python validator catches', () => {
  const codes = (mutate) => { const s = demo(); mutate(s); return S.lightValidate(s).map((i) => i.code); };
  assert.ok(codes((s) => { s.components[1].id = s.components[0].id; }).includes('DUPLICATE_COMPONENT'));
  assert.ok(codes((s) => { s.components[2].group = 'Bad Group'; }).includes('COMPONENT_GROUP'));
  assert.ok(codes((s) => { s.scale.footprint_m = [1, 1]; }).includes('FOOTPRINT_MISMATCH'));
  assert.ok(codes((s) => { delete s.components[2].params.size; }).includes('MISSING_PARAMETER'));
  assert.ok(codes((s) => { s.components[2].params.nonsense = 1; }).includes('UNUSED_PARAMETER'));
  assert.ok(codes((s) => { s.components[2].scope_id = 'nowhere'; }).includes('SCOPE_BLOCKED'));
  assert.ok(codes((s) => { s.requirements[0].component_id = 'ghost'; }).includes('MISSING_REQUIRED_COMPONENT'));
  assert.ok(codes((s) => { S.findComponent(s, 'table_a_top').params.radius = -2; }).includes('GEOMETRY'));
});

test('colour helpers round-trip in both colour spaces', () => {
  const srgb = { color: [0.62, 0.47, 0.35, 1], color_space: 'srgb' };
  assert.equal(S.toHex(srgb), '#9e7859');
  assert.deepEqual(S.fromHex('#9e7859', srgb).slice(0, 3).map((v) => Math.round(v * 255)), [158, 120, 89]);
  const lin = { color: [S.srgbToLinear(128 / 255), S.srgbToLinear(64 / 255), S.srgbToLinear(1), 0.5] };
  assert.equal(S.toHex(lin), '#8040ff');
  const back = S.fromHex('#8040ff', lin);
  back.slice(0, 3).forEach((v, i) => close(v, lin.color[i], 1e-4, 'linear'));
  assert.equal(back[3], 0.5);
});

test('an edited spec still validates in Python and exports to GLB', () => {
  const spec = demo();
  const everything = (fn) => assert.ok(S.tryEdit(spec, fn).ok);
  everything((s) => S.translate(s, S.groupMembers(s, 'counter'), [0.4, 0.2, 0]));
  everything((s) => S.rotateYaw(s, S.groupMembers(s, 'chair_b1'), 35, [-1.0, 0.3]));
  everything((s) => S.scaleSelection(s, S.groupMembers(s, 'planter'), 1.3, S.selectionBounds(s, S.groupMembers(s, 'planter')).centre));
  everything((s) => S.setMaterial(s, S.groupMembers(s, 'table_b'), { color: S.fromHex('#336699', S.findComponent(s, 'table_b_top').material), roughness: 0.3 }));
  everything((s) => S.setParam(s, 'floor', ['size', 1], 5.5));
  everything((s) => S.setGroup(s, ['chair_a1_seat'], 'loose_seat'));
  let copies;
  everything((s) => { copies = S.duplicate(s, S.groupMembers(s, 'chair_a2')); return copies; });
  everything((s) => { S.remove(s, S.groupMembers(s, 'spot')); });
  const dir = mkdtempSync(join(tmpdir(), 'booth-web-'));
  try {
    // The demo picture can be swapped (docs/sample-images.md); if it is missing, a plain stand-in of the same size, as the studio does.
    const picture = new URL('demo_source.png', DEMO);
    const local = join(dir, 'demo_source.png');
    if (existsSync(picture)) copyFileSync(picture, local);
    else {
      const made = spawnSync(PYTHON, ['-c', 'import sys; from PIL import Image; Image.new("RGB", (int(sys.argv[2]), int(sys.argv[3])), (214, 218, 222)).save(sys.argv[1])',
        local, String(spec.source.width), String(spec.source.height)], { encoding: 'utf8' });
      assert.equal(made.status, 0, made.stderr);
      spec.source.sha256 = createHash('sha256').update(readFileSync(local)).digest('hex');
    }
    const file = join(dir, 'edited.spec.json');
    writeFileSync(file, `${JSON.stringify(spec, null, 2)}\n`);
    const scripts = new URL('../../booth-image-to-model/scripts/', import.meta.url);
    const run = (script, ...args) => spawnSync(PYTHON, [fileURLToPath(new URL(script, scripts)), ...args], { encoding: 'utf8' });
    const valid = run('validate_spec.py', '--spec', file, '--check-source');
    assert.equal(valid.status, 0, valid.stdout + valid.stderr);
    assert.equal(JSON.parse(valid.stdout).valid, true);
    const glb = run('boothctl.py', 'glb', '--spec', file, '--out', join(dir, 'edited.glb'));
    assert.equal(glb.status, 0, glb.stdout + glb.stderr);
    assert.equal(JSON.parse(glb.stdout).status, 'glb_ready');
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
  assert.ok(meshFor(S.findComponent(spec, copies[0])).length === 2);
});
