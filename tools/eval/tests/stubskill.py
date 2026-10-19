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

BOOTHCTL = r'''
import json, sys
from pathlib import Path
argv = sys.argv[1:]
assert argv[0] == 'compile'
opt = {argv[i]: argv[i + 1] for i in range(1, len(argv) - 1, 2)}
MODE = %(mode)r
NEW = %(new)r
if MODE == 'blocked':
    print(json.dumps({'status': 'scene_invalid', 'errors': [{'code': 'FIT_FAILED'}]}))
    sys.exit(2)
out = Path(opt['--out']); out.mkdir(parents=True, exist_ok=True)
fit = {'status': 'fitted', 'rms_px': 0.5, 'dims_m': {'W': 6.0, 'D': 3.0, 'H': 3.0}, 'camera': {}}
report = {'status': 'ok', 'valid': True, 'fit': fit}
if NEW:
    report['quality_gate'] = {'status': 'pass', 'reasons': []}
(out / 'box_fit.json').write_text(json.dumps(fit)); (out / 'spec.json').write_text(json.dumps({'components': []}))
(out / 'compile_report.json').write_text(json.dumps(report))
print(json.dumps({'status': 'ok', 'valid': True}))
'''


def make_stub_skill(root, new, mode='ok'):
    """Folder with scripts/box_fit.py, scripts/boothctl.py, scripts/draft_compile.py, schemas/ and SKILL.md. new=True: knows `fit`."""
    root = Path(root)
    (root / 'scripts').mkdir(parents=True, exist_ok=True)
    (root / 'schemas').mkdir(exist_ok=True)
    fill = {'mode': mode, 'new': new}
    (root / 'scripts' / 'box_fit.py').write_text((BOX_FIT_HEAD + (BOX_FIT_NEW if new else BOX_FIT_OLD)) % fill, encoding='utf-8')
    (root / 'scripts' / 'boothctl.py').write_text(BOOTHCTL % fill, encoding='utf-8')
    (root / 'scripts' / 'draft_compile.py').write_text("report['quality_gate'] = {}\n" if new else 'pass\n', encoding='utf-8')
    point = {'id': {}, 'uv_px': {}, 'xyz': {}}
    if new:
        point['fit'] = {'type': ['boolean', 'null']}
    versions = ['scene-description.v1', '1.0', '1.1'] if new else ['scene-description.v1']
    schema = {'properties': {'schema_version': {'enum': versions}, 'corner_points': {'items': {'properties': point}}}}
    (root / 'schemas' / 'scene-description.v1.json').write_text(json.dumps(schema), encoding='utf-8')
    (root / 'SKILL.md').write_text('---\nname: stub\nmetadata:\n  version: "0.0.1"\n---\n', encoding='utf-8')
    return root
