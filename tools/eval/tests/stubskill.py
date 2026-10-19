"""A stand-in skill folder for testing the runner without scipy or the real engine (not a test module)."""
from __future__ import annotations
import json
from pathlib import Path

BOX_FIT_HEAD = r'''
import argparse, json, sys
p = argparse.ArgumentParser()
p.add_argument('--points'); p.add_argument('--output'); p.add_argument('--seeds', type=int, default=40)
a = p.parse_args()
pts = json.load(open(a.points))
if %(mode)r == 'blocked':
    json.dump({'status': 'blocked', 'reason': 'NO_SCALE_ANCHOR'}, open(a.output, 'w'))
    print('NO_SCALE_ANCHOR')
    sys.exit(2)
cam = {'projection': 'perspective', 'position': [0.0, -9.0, 3.0], 'target': [0.0, 0.0, 1.0], 'lens_mm': 30.0, 'sensor_width_mm': 36.0,
       'principal_point': [0.5, 0.5], 'roll_deg': 0.0}
out = {'status': 'fitted', 'rms_px': 0.5, 'max_px': 1.0, 'dims_m': {'W': 6.0, 'D': 3.0, 'H': 3.0}, 'camera': cam, 'warnings': [],
       'points': [{'id': q['id'], 'err_px': 0.5} for q in pts['points']]}
'''

BOX_FIT_OLD = r'''
json.dump(out, open(a.output, 'w'))
print(json.dumps({'status': 'fitted'}))
print(json.dumps(cam))
'''

# the strings n_check, loo_errors_px, soft_l1, check_failed and get('fit') are what the harness looks for in a newer script
BOX_FIT_NEW = r'''
out.update(n_fit=len(pts['points']), n_check=sum(1 for q in pts['points'] if q.get('fit') is False), outliers=[], loss='soft_l1',
           loo_errors_px={}, loo_median_px=0.6)
status_names = ['check_failed']
json.dump(out, open(a.output, 'w'))
print(json.dumps(out))
'''

# The topology part is a small, honest copy of the rules (type from the open sides, a wall on an open side is left out, the camera of the
# stand-in fit stands in front of the booth, i.e. beyond the front side), so that the runner's plumbing can be tested without the engine.
# Every stand-in builds the walls of the scene into its spec (the topology one leaves out those on an open side).
BOOTHCTL = r'''
import json, sys
from pathlib import Path
argv = sys.argv[1:]
assert argv[0] == 'compile'
opt = {argv[i]: argv[i + 1] for i in range(1, len(argv) - 1, 2)}
MODE = %(mode)r
NEW = %(new)r
TOPO = %(topology)r
if MODE == 'blocked':
    print(json.dumps({'status': 'scene_invalid', 'errors': [{'code': 'FIT_FAILED'}]}))
    sys.exit(2)
out = Path(opt['--out']); out.mkdir(parents=True, exist_ok=True)
fit = {'status': 'fitted', 'rms_px': 0.5, 'dims_m': {'W': 6.0, 'D': 3.0, 'H': 3.0}, 'camera': {'position': [0.0, -9.0, 3.0]}}
report = {'status': 'ok', 'valid': True, 'fit': fit, 'dropped': []}
spec = {'components': []}
if NEW:
    report['quality_gate'] = {'status': 'pass', 'reasons': []}
scene = json.loads(Path(opt['--scene']).read_text(encoding='utf-8'))
walls = [c for c in scene['components'] if c['type'] == 'wall']
kept = walls                                                  # a skill without the booth grammar builds every wall of the scene
if TOPO:
    opened, cut, booth_type = scene.get('open_sides'), scene.get('cutaway_sides') or [], scene.get('booth_type')
    ids = ['type_matches_sides', 'wall_on_open_side', 'closed_side_without_wall', 'camera_side']
    if opened is None:
        checks = [{'id': i, 'status': 'not_checked', 'message': 'open_sides is null'} for i in ids]
    else:
        adjacent = [{'front', 'left'}, {'front', 'right'}, {'back', 'left'}, {'back', 'right'}]
        n = len(set(opened))
        expected = {1: 'inline', 3: 'peninsula', 4: 'island'}.get(n, 'corner' if (n == 2 and set(opened) in adjacent) else 'other')
        kept = [w for w in walls if w['plane'] not in opened]
        report['dropped'] = [{'id': w['id'], 'reason': 'wall on an open side'} for w in walls if w['plane'] in opened]
        bare = [s for s in ('front', 'back', 'left', 'right') if s not in opened and s not in cut and s not in [w['plane'] for w in kept]]
        checks = [
            {'id': ids[0], 'status': 'not_checked' if booth_type is None else ('ok' if booth_type in ('other', expected) else 'fail'), 'message': ''},
            {'id': ids[1], 'status': 'fail' if report['dropped'] else 'ok', 'message': ''},
            {'id': ids[2], 'status': 'warn' if bare else 'ok', 'message': ''},
            {'id': ids[3], 'status': 'ok' if ('front' in opened or 'front' in cut) else 'fail', 'message': ''},
        ]
    report['topology'] = {'booth_type': booth_type, 'open_sides': opened, 'cutaway_sides': scene.get('cutaway_sides'), 'checks': checks}
    sentences = {ids[0]: 'the booth type does not match the open sides', ids[1]: 'a wall was placed on an open side and left out',
                 ids[3]: 'the camera looks in through closed sides: front/back or left/right may be swapped'}
    reasons = [sentences[c['id']] for c in checks if c['status'] == 'fail' and c['id'] in sentences]
    if reasons and NEW:
        report['quality_gate'] = {'status': 'review', 'reasons': reasons}
place = {'back': ([0.0, 1.475, 1.5], [6.0, 0.05, 3.0]), 'front': ([0.0, -1.475, 1.5], [6.0, 0.05, 3.0]),
         'left': ([-2.975, 0.0, 1.5], [0.05, 3.0, 3.0]), 'right': ([2.975, 0.0, 1.5], [0.05, 3.0, 3.0])}
spec['components'] = [{'id': 'floor', 'kind': 'box', 'location': [0.0, 0.0, 0.0], 'rotation_deg': [0, 0, 0], 'params': {'size': [6.0, 3.0, 0.05]}}] + [
    {'id': w['id'], 'kind': 'box', 'location': place[w['plane']][0], 'rotation_deg': [0, 0, 0], 'params': {'size': place[w['plane']][1]}} for w in kept]
(out / 'box_fit.json').write_text(json.dumps(fit)); (out / 'spec.json').write_text(json.dumps(spec))
(out / 'compile_report.json').write_text(json.dumps(report))
print(json.dumps({'status': 'ok', 'valid': True}))
'''


def make_stub_skill(root, new, mode='ok', topology=False):
    """Folder with scripts/box_fit.py, scripts/boothctl.py, scripts/draft_compile.py, schemas/ and SKILL.md. new=True: knows `fit`.

    topology=True (with new=True): the scene schema knows booth_type, open_sides and cutaway_sides (schema_version 1.2), and the compile
    stand-in writes report.topology, a gate reason for each failed topology check and a spec without the walls it left out."""
    root = Path(root)
    (root / 'scripts').mkdir(parents=True, exist_ok=True)
    (root / 'schemas').mkdir(exist_ok=True)
    fill = {'mode': mode, 'new': new, 'topology': topology}
    (root / 'scripts' / 'box_fit.py').write_text((BOX_FIT_HEAD + (BOX_FIT_NEW if new else BOX_FIT_OLD)) % fill, encoding='utf-8')
    (root / 'scripts' / 'boothctl.py').write_text(BOOTHCTL % fill, encoding='utf-8')
    compile_src = "report['quality_gate'] = {}\n" if new else 'pass\n'
    if topology:
        compile_src += "report['topology'] = {}\n"
    (root / 'scripts' / 'draft_compile.py').write_text(compile_src, encoding='utf-8')
    point = {'id': {}, 'uv_px': {}, 'xyz': {}}
    if new:
        point['fit'] = {'type': ['boolean', 'null']}
    versions = ['scene-description.v1', '1.0', '1.1'] if new else ['scene-description.v1']
    props = {'schema_version': {'enum': versions + (['1.2'] if topology else [])}, 'corner_points': {'items': {'properties': point}}}
    if topology:
        props.update(booth_type={'type': ['string', 'null']}, open_sides={'type': ['array', 'null']}, cutaway_sides={'type': ['array', 'null']})
    (root / 'schemas' / 'scene-description.v1.json').write_text(json.dumps({'properties': props}), encoding='utf-8')
    (root / 'SKILL.md').write_text('---\nname: stub\nmetadata:\n  version: "0.0.1"\n---\n', encoding='utf-8')
    return root
