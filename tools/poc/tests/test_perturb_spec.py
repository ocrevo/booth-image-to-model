"""P3 tests for perturb_spec.py: six broken variants of one baseline spec (docs/poc/03-scoring-spec.md section 5).

The baseline spec, the fit and the answer sheet are small and programmatic: nothing here is a real picture or a client value.
"""
from __future__ import annotations
import contextlib
import copy
import hashlib
import io
import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parents[0]))        # tools/poc, so that `score` is the package
sys.path.insert(0, str(HERE / 'fixtures'))
sys.path.append(str(ROOT / 'booth-image-to-model' / 'scripts'))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402
import synthetic as fx  # noqa: E402
import camera_math  # noqa: E402
import validate_spec  # noqa: E402
from score import metrics  # noqa: E402
from score import perturb_spec as pt  # noqa: E402

W, H = 640, 480
VARIANTS = ['B1', 'B2', 'B3', 'B4', 'B5', 'B6']
CAMERA = {'projection': 'perspective', 'position': [-7.0, -8.0, 1.8], 'target': [0, 0, 1.6], 'lens_mm': 28, 'sensor_width_mm': 36,
          'principal_point': [0.5, 0.5], 'pixel_aspect': [1, 1], 'fit_status': 'landmark_fitted', 'roll_deg': 0}

# id, location, size, group, sRGB colour
PARTS = [
    ('carpet', (0, 0, 0.01), (8, 5, 0.02), None, (0.30, 0.35, 0.40)),
    ('fascia', (0, 0.5, 2.8), (4, 0.3, 0.5), None, (0.80, 0.80, 0.80)),
    ('sales_desk', (2.5, -1.5, 0.5), (2, 0.7, 1.0), None, (0.20, 0.30, 0.70)),
    ('back_wall', (0, 2.3, 1.2), (3, 0.15, 2.4), None, (0.15, 0.15, 0.20)),
    ('cabinet', (-3.2, 1.9, 1.0), (1.2, 0.5, 2.0), None, (0.85, 0.85, 0.80)),
    ('display_base', (-1.8, -1.2, 0.45), (1.0, 1.0, 0.9), 'display', (0.60, 0.40, 0.30)),
    ('display_top', (-1.8, -1.2, 0.95), (1.1, 1.1, 0.1), 'display', (0.60, 0.40, 0.30)),
    ('shelf', (3.3, 1.8, 1.2), (1.0, 0.4, 2.4), None, (0.45, 0.50, 0.45)),
    ('stool', (0.5, -2.4, 0.3), (0.4, 0.4, 0.6), None, (0.90, 0.30, 0.10)),
]


def make_spec():
    comps, scope = [], []
    for ident, loc, size, group, rgb in PARTS:
        c = {'id': ident, 'scope_id': ident, 'owner': 'booth', 'kind': 'box', 'role': 'floor' if ident == 'carpet' else 'furniture',
             'critical': False, 'evidence': {'status': 'observed', 'roi_xyxy_norm': [0.1, 0.1, 0.2, 0.2], 'note': 'synthetic fixture'},
             'location': list(loc), 'rotation_deg': [0, 0, 0], 'params': {'size': list(size)},
             'material': {'color': list(rgb) + [1], 'color_space': 'srgb', 'roughness': 0.5, 'metallic': 0, 'emission': 0}}
        if group:
            c['group'] = group
        comps.append(c)
        scope.append({'id': ident, 'owner': 'booth', 'decision': 'include', 'evidence': 'synthetic fixture'})
    return {'schema_version': '2.0', 'project': {'id': 'p3-fixture', 'status': 'draft_not_image_matched'},
            'source': {'image': 'reference.png', 'sha256': 'a' * 64, 'width': W, 'height': H},
            'scale': {'units': 'm', 'basis': 'user_assumption', 'footprint_m': [8, 5], 'notes': 'invented fixture'},
            'scope': {'decisions': scope}, 'components': comps, 'camera': copy.deepcopy(CAMERA),
            'requirements': [{'id': 'desk_is_box', 'component_id': 'display_base', 'kind': 'box', 'must_be_critical': False}],
            'landmarks': [{'id': 'lm_display', 'component_id': 'display_top', 'local_xyz': [0, 0, 0], 'uv': [0.4, 0.6],
                           'visibility': 'visible', 'fit': True}],
            'uncertainties': [{'id': 'scale', 'critical': False, 'status': 'open', 'description': 'invented'}],
            'quality': {'silhouette_iou_min': 0.9, 'part_iou_min': 0.85, 'landmark_median_max': 0.025, 'landmark_p90_max': 0.05,
                        'boundary_distance_max': 0.012, 'threshold_basis': 'fixture'}}


def centre_px(spec, ident):
    c = next(c for c in spec['components'] if c['id'] == ident)
    low, high = metrics.points_bounds(metrics.component_points(c))
    mid = [(a + b) / 2 for a, b in zip(low, high)]
    return camera_math.project(mid, spec['camera'], W, H)[:2]


def bbox_around(uv, r=3):
    return [round(uv[0]) - r, round(uv[1]) - r, round(uv[0]) + r, round(uv[1]) + r]


def make_truth(folder, spec):
    ids = ['fascia', 'sales_desk', 'back_wall', 'cabinet', 'display', 'shelf', 'stool', 'carpet']
    inv = {'schema_version': 'truth.v1', 'picture_id': 'pic_T',
           'items': [{'id': i, 'name': i, 'kind_hint': 'box', 'hero': i in ('fascia', 'sales_desk')} for i in ids]}
    parts = ['fascia', 'sales_desk', 'back_wall', 'cabinet']
    corners = ['top_left', 'top_right', 'bottom_left', 'bottom_right']
    hold = {'schema_version': 'truth.v1', 'picture_id': 'pic_T',
            'points': [{'id': f'h{n + 1}', 'part_id': p, 'corner': corners[n], 'uv_px': [100, 100], 'note': 'x'}
                       for n, p in enumerate(parts)]}
    sw = {'schema_version': 'truth.v1', 'picture_id': 'pic_T',
          'swatches': [{'id': 's_dom', 'bbox_px': bbox_around(centre_px(spec, 'back_wall')), 'role': 'dominant'},
                       {'id': 's_sec', 'bbox_px': bbox_around(centre_px(spec, 'cabinet')), 'role': 'secondary'},
                       {'id': 's_acc', 'bbox_px': bbox_around(centre_px(spec, 'stool')), 'role': 'accent'}]}
    return fx.write_truth(folder, **{'inventory.json': inv, 'holdout_px.json': hold, 'swatches.json': sw})


MAPPING = {'sales_desk': 'sales_desk', 'display': ['display_base', 'display_top']}


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = pt.main([str(a) for a in argv])
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_picture(path, size=(W, H)):
    """A small synthetic picture; returns its SHA-256 (the spec has to record it)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.full((size[1], size[0], 3), 90, np.uint8), 'RGB').save(path, compress_level=0)     # stored blocks: the same bytes everywhere
    return hashlib.sha256(path.read_bytes()).hexdigest()


def visible_pixel(spec, ident, width=W, height=H):
    """A pixel that shows component `ident`, with its 5 x 5 surroundings also showing it when there is such a pixel (the one nearest
    to the middle of those), else the visible pixel nearest to the middle."""
    ids, idmap = pt.render_ids(spec, spec['camera'], width, height)
    mine = idmap == ids.index(ident)
    assert mine.any(), f'{ident} is not visible'
    inner = mine.copy()
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            shifted = np.zeros_like(mine)
            ys, xs = slice(max(0, dy), height + min(0, dy)), slice(max(0, dx), width + min(0, dx))
            yd, xd = slice(max(0, -dy), height + min(0, -dy)), slice(max(0, -dx), width + min(0, -dx))
            shifted[yd, xd] = mine[ys, xs]
            inner &= shifted
    ys, xs = np.nonzero(inner if inner.any() else mine)
    cy, cx = np.nonzero(mine)
    k = int(np.argmin((xs - cx.mean()) ** 2 + (ys - cy.mean()) ** 2))
    return int(xs[k]) + 0.5, int(ys[k]) + 0.5


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.spec = make_spec()
        self.spec['source']['sha256'] = write_picture(self.tmp / 'reference.png')
        self.spec_path = self.tmp / 'spec.json'
        self.spec_path.write_text(json.dumps(self.spec), encoding='utf-8')
        self.fit_path = self.tmp / 'box_fit.json'
        self.fit_path.write_text(json.dumps({'image': {'width': W, 'height': H}, 'camera': CAMERA}), encoding='utf-8')
        self.truth = make_truth(self.tmp / 'truth', self.spec)
        self.map_path = self.tmp / 'map.json'
        self.map_path.write_text(json.dumps(MAPPING), encoding='utf-8')
        self.out = self.tmp / 'out'

    def cli(self, exclude=(), **over):
        args = {'spec': self.spec_path, 'fit': self.fit_path, 'truth': self.truth, 'map': self.map_path, 'out': self.out}
        args.update(over)
        argv = []
        for k, v in args.items():
            if v is not None:
                argv += [f'--{k}', v]
        for e in exclude:
            argv += ['--exclude', e]
        return run_cli(*argv)

    def swatches_on(self, dominant, secondary, accent):
        at = {'dominant': dominant, 'secondary': secondary, 'accent': accent}
        sw = {'schema_version': 'truth.v1', 'picture_id': 'pic_T',
              'swatches': [{'id': f's_{r}', 'bbox_px': bbox_around(visible_pixel(self.spec, c), 2), 'role': r} for r, c in at.items()]}
        (self.truth / 'swatches.json').write_text(json.dumps(sw), encoding='utf-8')

    def variants(self):
        return {v: json.loads((self.out / v / 'spec.json').read_text(encoding='utf-8')) for v in VARIANTS}


class PixelsToMetres(unittest.TestCase):
    def test_px_to_metres_round_trips_through_project_within_2px(self):
        # camera 10 m from the origin looking along +Y, f = 320 px: a move along X is exactly along the image x axis
        cam = fx.camera()
        point = [1.0, 0.0, 0.5]
        u0, v0, z = camera_math.project(point, cam, 320, 200)
        f_px = camera_math.focal_px(cam, 320)
        for px in (20, 60):
            d = pt.px_to_metres(px, z, f_px)
            u1, v1, _ = camera_math.project([point[0] + d, point[1], point[2]], cam, 320, 200)
            self.assertAlmostEqual(math.hypot(u1 - u0, v1 - v0), px, delta=2, msg=f'{px} px moved to {d} m')

    def test_px_to_metres_uses_depth_and_focal_length(self):
        self.assertAlmostEqual(pt.px_to_metres(20, 10.0, 320.0), 0.625)
        self.assertAlmostEqual(pt.px_to_metres(60, 5.0, 500.0), 0.6)

    def test_moved_parts_reproject_within_tolerance_on_an_oblique_camera(self):
        spec = make_spec()
        cam = spec['camera']
        for ident, px in (('display_base', 20), ('sales_desk', 60), ('shelf', 20)):
            comps = [c for c in spec['components'] if c['id'] == ident]
            before = centre_px(spec, ident)
            moved = pt.move_components(copy.deepcopy(comps), cam, W, H, px)
            trial = copy.deepcopy(spec)
            trial['components'] = moved
            after = centre_px(trial, ident)
            self.assertAlmostEqual(math.hypot(after[0] - before[0], after[1] - before[1]), px, delta=2, msg=ident)


class Colour(unittest.TestCase):
    def test_lab_agrees_with_opencv(self):
        import cv2
        import numpy as np
        for rgb in ((0.9, 0.3, 0.1), (0.2, 0.3, 0.7), (0.5, 0.5, 0.5), (0.05, 0.05, 0.05)):
            ref = cv2.cvtColor(np.array([[rgb]], np.float32), cv2.COLOR_RGB2Lab)[0, 0]
            self.assertTrue(all(abs(a - b) < 0.5 for a, b in zip(pt.srgb_to_lab(rgb), ref)), rgb)

    def test_recolour_hits_delta_e_15(self):
        for rgb in ((0.9, 0.3, 0.1), (0.2, 0.3, 0.7), (0.5, 0.5, 0.5), (0.97, 0.97, 0.97), (0.03, 0.03, 0.03)):
            new = pt.recolour_srgb(rgb, 15.0)
            self.assertTrue(all(0 <= x <= 1 for x in new), new)
            self.assertAlmostEqual(pt.delta_e76(pt.srgb_to_lab(rgb), pt.srgb_to_lab(new)), 15.0, delta=1.0, msg=str(rgb))


class Variants(Base):
    def test_variants_validate_with_validate_spec(self):
        self.assertEqual(self.cli()[0], 0)
        for name, spec in self.variants().items():
            result = validate_spec.validate(spec)
            self.assertTrue(result['valid'], f'{name}: {result["errors"]}')

    def test_files_and_expected_order(self):
        code, out, err = self.cli()
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads((self.out / 'expected_order.json').read_text(encoding='utf-8')), VARIANTS)
        for v in VARIANTS:
            self.assertTrue((self.out / v / 'spec.json').is_file())

    def test_inputs_are_not_modified(self):
        before = [sha(p) for p in (self.spec_path, self.fit_path, self.map_path)] + [sha(p) for p in sorted(self.truth.iterdir())]
        self.assertEqual(self.cli()[0], 0)
        after = [sha(p) for p in (self.spec_path, self.fit_path, self.map_path)] + [sha(p) for p in sorted(self.truth.iterdir())]
        self.assertEqual(before, after)

    def test_removed_parts_are_absent_and_others_untouched(self):
        self.assertEqual(self.cli()[0], 0)
        base = {c['id']: c for c in self.spec['components']}
        var = self.variants()
        removed = {}
        for v in ('B4', 'B5', 'B6'):
            ids = {c['id'] for c in var[v]['components']}
            removed[v] = set(base) - ids
            self.assertTrue(removed[v], v)
            for c in var[v]['components']:                       # every part that is left is the baseline's, B6 swaps two colours
                want = copy.deepcopy(base[c['id']])
                if v == 'B6' and c['id'] in ('back_wall', 'cabinet'):
                    want['material']['color'] = c['material']['color']
                self.assertEqual(c, want, f'{v}: {c["id"]} changed')
            for r in var[v]['requirements'] + var[v]['landmarks']:
                self.assertNotIn(r['component_id'], removed[v], f'{v}: {r}')
        self.assertEqual(removed['B4'], {'display_base', 'display_top'})        # one non-hero part: the whole group
        self.assertEqual(removed['B5'], {'fascia'})                              # one hero part
        self.assertEqual(len(removed['B6'] & {'fascia'}), 1)
        self.assertEqual(removed['B6'], {'fascia', 'display_base', 'display_top', 'shelf'})   # hero + two non-hero parts
        # the swatch parts (wall, cabinet, stool) are spared so that the colour swap still has parts to swap
        for v in ('B4', 'B5', 'B6'):
            self.assertFalse(removed[v] & {'back_wall', 'cabinet', 'stool', 'carpet'}, v)

    def test_deleted_part_scope_is_deferred_not_included(self):
        self.assertEqual(self.cli()[0], 0)
        b4 = self.variants()['B4']
        states = {d['id']: d['decision'] for d in b4['scope']['decisions']}
        self.assertEqual(states['display_base'], 'defer')
        self.assertEqual(states['fascia'], 'include')

    def test_moves_reproject_20_and_60_px(self):
        self.assertEqual(self.cli()[0], 0)
        var = self.variants()
        base = self.spec
        for v, ident, px in (('B2', 'display_base', 20), ('B3', 'fascia', 60)):
            a, b = centre_px(base, ident), centre_px(var[v], ident)
            self.assertAlmostEqual(math.hypot(b[0] - a[0], b[1] - a[1]), px, delta=2, msg=v)
            others = [c for c in var[v]['components'] if c['id'] not in ('display_base', 'display_top', 'fascia')]
            self.assertEqual(others, [c for c in base['components'] if c['id'] in {o['id'] for o in others}], v)
            self.assertEqual(len(var[v]['components']), len(base['components']))
        # a part of two components moves as one
        b2 = {c['id']: c for c in var['B2']['components']}
        b0 = {c['id']: c for c in base['components']}
        delta = [b2['display_base']['location'][i] - b0['display_base']['location'][i] for i in range(3)]
        delta_top = [b2['display_top']['location'][i] - b0['display_top']['location'][i] for i in range(3)]
        self.assertTrue(all(abs(x - y) < 1e-9 for x, y in zip(delta, delta_top)))
        self.assertAlmostEqual(delta[2], 0.0)                     # along a ground axis

    def test_b1_recolours_the_accent_part_by_about_15_de(self):
        self.assertEqual(self.cli()[0], 0)
        b1 = {c['id']: c for c in self.variants()['B1']['components']}
        base = {c['id']: c for c in self.spec['components']}
        changed = [i for i in base if b1[i] != base[i]]
        self.assertEqual(changed, ['stool'])
        old, new = base['stool']['material'], b1['stool']['material']
        self.assertEqual(new['color_space'], 'srgb')
        self.assertEqual(new['color'][3], 1)
        de = pt.delta_e76(pt.srgb_to_lab(old['color'][:3]), pt.srgb_to_lab(new['color'][:3]))
        self.assertAlmostEqual(de, 15.0, delta=1.0)
        self.assertEqual({k: v for k, v in new.items() if k != 'color'}, {k: v for k, v in old.items() if k != 'color'})

    def test_b6_swaps_dominant_and_secondary_colours(self):
        self.assertEqual(self.cli()[0], 0)
        b6 = {c['id']: c for c in self.variants()['B6']['components']}
        base = {c['id']: c for c in self.spec['components']}
        self.assertEqual(b6['back_wall']['material']['color'], base['cabinet']['material']['color'])
        self.assertEqual(b6['cabinet']['material']['color'], base['back_wall']['material']['color'])

    def test_report_names_what_each_variant_touched(self):
        self.assertEqual(self.cli()[0], 0)
        rep = json.loads((self.out / 'perturb_report.json').read_text(encoding='utf-8'))
        self.assertEqual(rep['variants']['B3']['part'], 'fascia')
        self.assertEqual(rep['variants']['B5']['part'], 'fascia')
        self.assertEqual(rep['variants']['B1']['components'], ['stool'])
        self.assertEqual(rep['variants']['B2']['part'], 'display')
        self.assertLess(abs(rep['variants']['B3']['achieved_px'] - 60), 2)

    def test_same_input_same_output(self):
        self.assertEqual(self.cli()[0], 0)
        first = [sha(self.out / v / 'spec.json') for v in VARIANTS]
        self.out = self.tmp / 'out2'
        self.assertEqual(self.cli()[0], 0)
        self.assertEqual(first, [sha(self.out / v / 'spec.json') for v in VARIANTS])


class WholePart(Base):
    """Recolour and swap act on the whole inventory part, not on the one component that shows most under the swatch."""

    def test_b1_recolours_every_same_coloured_component_of_the_part(self):
        self.swatches_on('back_wall', 'cabinet', 'display_base')
        self.assertEqual(self.cli()[0], 0)
        b1 = {c['id']: c for c in self.variants()['B1']['components']}
        base = {c['id']: c for c in self.spec['components']}
        self.assertEqual([i for i in base if b1[i] != base[i]], ['display_base', 'display_top'])
        self.assertEqual(b1['display_base']['material']['color'], b1['display_top']['material']['color'])
        de = pt.delta_e76(pt.srgb_to_lab(base['display_top']['material']['color']), pt.srgb_to_lab(b1['display_top']['material']['color']))
        self.assertAlmostEqual(de, 15.0, delta=1.0)
        rep = json.loads((self.out / 'perturb_report.json').read_text(encoding='utf-8'))['variants']['B1']
        self.assertEqual(rep['components'], ['display_base', 'display_top'])

    def test_b1_leaves_a_differently_coloured_component_of_the_part_alone(self):
        for c in self.spec['components']:
            if c['id'] == 'display_top':
                c['material']['color'] = [0.1, 0.5, 0.9, 1]
        self.spec_path.write_text(json.dumps(self.spec), encoding='utf-8')
        self.swatches_on('back_wall', 'cabinet', 'display_base')
        self.assertEqual(self.cli()[0], 0)
        b1 = {c['id']: c for c in self.variants()['B1']['components']}
        base = {c['id']: c for c in self.spec['components']}
        self.assertEqual([i for i in base if b1[i] != base[i]], ['display_base'])

    def test_b6_swaps_the_colours_of_whole_parts(self):
        self.swatches_on('display_base', 'cabinet', 'stool')
        self.assertEqual(self.cli()[0], 0)
        b6 = {c['id']: c for c in self.variants()['B6']['components']}
        base = {c['id']: c for c in self.spec['components']}
        for i in ('display_base', 'display_top'):
            self.assertEqual(b6[i]['material']['color'], base['cabinet']['material']['color'], i)
        self.assertEqual(b6['cabinet']['material']['color'], base['display_base']['material']['color'])
        rep = json.loads((self.out / 'perturb_report.json').read_text(encoding='utf-8'))['variants']['B6']
        self.assertEqual(sorted(rep['swapped_components']), ['cabinet', 'display_base', 'display_top'])
        for v, spec in self.variants().items():
            self.assertTrue(validate_spec.validate(spec, source_root=self.out / v, source_size=[W, H])['valid'], v)


# SHA-256 of every file a run without --exclude writes for the fixture above (taken from the code before --exclude existed)
GOLDEN = {
    'B1/reference.png': '5753d3a5e60c31db576f58c72decf7d24939b7768dd0a6d35faf0a5dec8c2334',
    'B1/spec.json': '5d2c325a89283b5b363ba0362ed790ae11507af52653060b01064488222dfcff',
    'B2/reference.png': '5753d3a5e60c31db576f58c72decf7d24939b7768dd0a6d35faf0a5dec8c2334',
    'B2/spec.json': '86e6174504ce4c090dabe2e98c17cbdb8e06bb0f5324c3a9633f754e8de721f8',
    'B3/reference.png': '5753d3a5e60c31db576f58c72decf7d24939b7768dd0a6d35faf0a5dec8c2334',
    'B3/spec.json': '64c201392c0cd26aa819140278e7753c24b537c4c69b5a480c125418f89f97e3',
    'B4/reference.png': '5753d3a5e60c31db576f58c72decf7d24939b7768dd0a6d35faf0a5dec8c2334',
    'B4/spec.json': 'a0841bef77a490f012522797d1336bbbf29ae5fdd4d7606610a4b4e8e6b7adae',
    'B5/reference.png': '5753d3a5e60c31db576f58c72decf7d24939b7768dd0a6d35faf0a5dec8c2334',
    'B5/spec.json': '6102fbef6856bbb586891820ea5502a4e1a872f25a2fd3eebd1c1ff17aae4d5a',
    'B6/reference.png': '5753d3a5e60c31db576f58c72decf7d24939b7768dd0a6d35faf0a5dec8c2334',
    'B6/spec.json': 'a026e38f9f3d6c2e73debd384844524f2d688aff3cec31f480dcb3eeb3f33f17',
    'expected_order.json': 'aebbda592b167e762829885e4a5b34259f7f2b82c7532b69dd98ba40b5fd200f',
    'perturb_report.json': '231c942cc980d77110421e643ea9518dfe5b7da8e2b22cd39dc527be97ac8597',
}


class Exclusions(Base):
    """--exclude <inventory id>=<reason>: an excluded inventory part is never the part of any variant."""

    def report(self):
        return json.loads((self.out / 'perturb_report.json').read_text(encoding='utf-8'))

    def test_without_exclude_every_output_is_byte_identical_to_the_old_run(self):
        self.assertEqual(self.cli()[0], 0)
        got = {str(p.relative_to(self.out)).replace('\\', '/'): sha(p) for p in sorted(self.out.rglob('*')) if p.is_file()}
        self.assertEqual(got, GOLDEN)

    def test_excluding_the_usual_non_hero_part_moves_b2_b4_b6_to_the_next_one(self):
        self.assertEqual(self.cli()[0], 0)
        usual = self.report()['variants']
        self.assertEqual((usual['B2']['part'], usual['B4']['part'], usual['B6']['part']), ('display', 'display', ['fascia', 'display', 'shelf']))
        self.out = self.tmp / 'excluded'
        code, out, err = self.cli(exclude=['display=its shape is an opening in the back wall'])
        self.assertEqual(code, 0, err)
        v = self.report()['variants']
        self.assertEqual(v['B2']['part'], 'shelf')
        self.assertEqual(v['B4']['part'], 'shelf')
        self.assertEqual(v['B6']['part'], ['fascia', 'shelf', 'stool'])
        variants = self.variants()
        left4 = {c['id'] for c in variants['B4']['components']}
        self.assertIn('display_base', left4)                       # the excluded part is untouched everywhere
        self.assertNotIn('shelf', left4)
        base = {c['id']: c for c in self.spec['components']}
        for name in VARIANTS:
            comps = {c['id']: c for c in variants[name]['components']}
            for i in ('display_base', 'display_top'):
                self.assertEqual(comps[i], base[i], f'{name}: {i}')
            result = validate_spec.validate(variants[name], source_root=self.out / name, source_size=[W, H])
            self.assertTrue(result['valid'], f'{name}: {result["errors"]}')

    def test_excluding_a_hero_part_moves_b3_b5_b6_to_the_next_hero(self):
        code, out, err = self.cli(exclude=['fascia=the sign is part of another item'])
        self.assertEqual(code, 0, err)
        v = self.report()['variants']
        self.assertEqual((v['B3']['part'], v['B5']['part']), ('sales_desk', 'sales_desk'))
        self.assertEqual(v['B6']['part'][0], 'sales_desk')
        self.assertIn('fascia', {c['id'] for c in self.variants()['B5']['components']})

    def assert_swatch_refused(self, code, err, role, item, component):
        self.assertEqual(code, 2, err)
        self.assertTrue(err.startswith('ERROR'), err)
        for word in (role, 'swatch', item, component):
            self.assertIn(word, err)
        self.assertFalse(self.out.exists() and any(self.out.iterdir()), 'nothing may be written')

    def test_excluding_the_part_under_the_accent_swatch_is_an_error(self):
        code, out, err = self.cli(exclude=['stool=a stand-in for another item'])
        self.assert_swatch_refused(code, err, 'accent', 'stool', 'stool')

    def test_excluding_the_part_under_the_dominant_swatch_is_an_error(self):
        code, out, err = self.cli(exclude=['back_wall=its shape is made by another item'])
        self.assert_swatch_refused(code, err, 'dominant', 'back_wall', 'back_wall')

    def test_excluding_the_part_under_the_secondary_swatch_is_an_error(self):
        code, out, err = self.cli(exclude=['cabinet=its shape is made by another item'])
        self.assert_swatch_refused(code, err, 'secondary', 'cabinet', 'cabinet')

    def test_an_exclusion_never_falls_through_to_the_next_visible_component(self):
        # the accent box is mostly the stool with a little carpet in it: excluding the stool must not recolour the carpet
        sw = json.loads((self.truth / 'swatches.json').read_text(encoding='utf-8'))
        u, v = visible_pixel(self.spec, 'stool')
        accent = next(item for item in sw['swatches'] if item['role'] == 'accent')
        for r in range(4, 60):                                  # grow the box until a second component shows in it
            accent['bbox_px'] = bbox_around((u, v), r)
            cover = pt._swatch_components(self.spec, sw['swatches'], CAMERA, W, H)['accent']
            if len(cover) > 1:
                break
        self.assertEqual(cover[0], 'stool', 'the stool must still be the main part of the box')
        self.assertGreater(len(cover), 1, 'the fixture must show a second component in the box')
        (self.truth / 'swatches.json').write_text(json.dumps(sw), encoding='utf-8')
        self.assertEqual(self.cli()[0], 0)
        self.out = self.tmp / 'excluded'
        code, out, err = self.cli(exclude=['stool=a stand-in for another item'])
        self.assert_swatch_refused(code, err, 'accent', 'stool', 'stool')

    def test_a_part_that_shares_a_component_with_an_excluded_part_is_refused_by_name(self):
        (self.tmp / 'map.json').write_text(json.dumps(dict(MAPPING, fascia=['fascia', 'display_top'])), encoding='utf-8')
        code, out, err = self.cli(exclude=['display=its shape is an opening in the back wall'])
        self.assertEqual(code, 2, err)
        self.assertTrue(err.startswith('ERROR'), err)
        for word in ('fascia', 'display_top', 'display', 'shares'):
            self.assertIn(word, err)
        self.assertFalse(self.out.exists() and any(self.out.iterdir()))

    def test_a_non_hero_part_sharing_with_an_excluded_part_is_named_too(self):
        (self.tmp / 'map.json').write_text(json.dumps(dict(MAPPING, shelf=['shelf', 'display_top'])), encoding='utf-8')
        code, out, err = self.cli(exclude=['display=its shape is an opening in the back wall'])
        self.assertEqual(code, 2, err)
        for word in ('shelf', 'display_top', 'display', 'shares'):
            self.assertIn(word, err)
        self.assertNotIn('TOO_FEW_PARTS', err)

    def test_sharing_without_an_exclusion_is_not_an_error(self):
        (self.tmp / 'map.json').write_text(json.dumps(dict(MAPPING, shelf=['shelf', 'display_top'])), encoding='utf-8')
        self.assertEqual(self.cli()[0], 0)

    def test_two_excluded_parts_may_share_a_component(self):
        (self.tmp / 'map.json').write_text(json.dumps(dict(MAPPING, shelf=['shelf', 'display_top'])), encoding='utf-8')
        self.swatches_on('carpet', 'fascia', 'sales_desk')           # colour parts that are neither of the two
        code, out, err = self.cli(exclude=['display=first reason', 'shelf=second reason'])
        self.assertEqual(code, 0, err)
        base = {c['id']: c for c in self.spec['components']}
        for name, spec in self.variants().items():
            comps = {c['id']: c for c in spec['components']}
            for i in ('display_base', 'display_top', 'shelf'):
                self.assertEqual(comps[i], base[i], f'{name}: {i}')

    def test_the_reason_and_the_replacement_are_recorded(self):
        code, out, err = self.cli(exclude=['display=first reason', 'fascia=second reason'])
        self.assertEqual(code, 0, err)
        rep = self.report()
        self.assertEqual(rep['exclusions'], [{'id': 'display', 'reason': 'first reason'}, {'id': 'fascia', 'reason': 'second reason'}])
        v = rep['variants']
        self.assertEqual(v['B2']['part'], 'shelf')
        self.assertEqual(v['B2']['part_without_exclusions'], 'display')
        self.assertEqual((v['B3']['part'], v['B3']['part_without_exclusions']), ('sales_desk', 'fascia'))
        self.assertEqual(v['B6']['part_without_exclusions'], ['fascia', 'display', 'shelf'])
        self.assertNotIn('swatch_component_without_exclusions', v['B1'])       # an exclusion can no longer change B1

    def test_a_reason_may_contain_an_equals_sign(self):
        self.assertEqual(self.cli(exclude=['display=a=b'])[0], 0)
        self.assertEqual(self.report()['exclusions'], [{'id': 'display', 'reason': 'a=b'}])

    def test_bad_exclude_arguments_exit_2_and_write_nothing(self):
        for bad in ('display', 'display=', 'display=   ', '=reason', 'no_such_part=reason'):
            self.out = self.tmp / ('out_' + str(abs(hash(bad))))
            code, out, err = self.cli(exclude=[bad])
            self.assertEqual(code, 2, bad)
            self.assertTrue(err.startswith('ERROR'), err)
            self.assertIn('exclude', err.lower(), bad)
            self.assertFalse(self.out.exists() and any(self.out.iterdir()), bad)
        self.out = self.tmp / 'out_dup'
        code, out, err = self.cli(exclude=['display=one', 'display=two'])
        self.assertEqual(code, 2)
        self.assertIn('display', err)

    def test_excluding_every_non_hero_part_is_an_error_not_a_skip(self):
        self.swatches_on('carpet', 'fascia', 'sales_desk')          # colour parts that are heroes or the floor
        self.assertEqual(self.cli()[0], 0)
        self.out = self.tmp / 'excluded'
        every = ['back_wall=a', 'cabinet=b', 'display=c', 'shelf=d', 'stool=e']
        code, out, err = self.cli(exclude=every)
        self.assertEqual(code, 2)
        self.assertTrue(err.startswith('ERROR'), err)
        self.assertIn('B2', err)
        self.assertIn('B6', err)
        self.assertIn('excluding', err)
        self.assertFalse(self.out.exists() and any(self.out.iterdir()))

    def test_excluding_every_hero_part_is_an_error_not_a_skip(self):
        code, out, err = self.cli(exclude=['fascia=a', 'sales_desk=b'])
        self.assertEqual(code, 2)
        self.assertIn('B5', err)
        self.assertIn('hero', err)
        self.assertFalse(self.out.exists() and any(self.out.iterdir()))

    def test_leaving_a_single_non_hero_part_is_an_error_for_b6(self):
        code, out, err = self.cli(exclude=['display=a', 'shelf=b'])        # back_wall and cabinet hold the swap parts, stool the accent one
        self.assertEqual(code, 2)
        self.assertIn('B6', err)

    def test_help_mentions_exclude(self):
        code, out, err = run_cli('--help')
        self.assertIn('--exclude', out)


class SourcePicture(Base):
    def test_every_variant_folder_validates_with_check_source(self):
        self.assertEqual(self.cli()[0], 0)
        for v in VARIANTS:
            folder = self.out / v
            self.assertTrue((folder / 'reference.png').is_file(), v)
            self.assertEqual(sha(folder / 'reference.png'), self.spec['source']['sha256'])
            spec = json.loads((folder / 'spec.json').read_text(encoding='utf-8'))
            self.assertEqual(spec['source'], self.spec['source'])
            result = validate_spec.validate(spec, source_root=folder, source_size=[W, H])
            self.assertTrue(result['valid'], f'{v}: {result["errors"]}')
            run = subprocess.run([sys.executable, str(ROOT / 'booth-image-to-model' / 'scripts' / 'validate_spec.py'),
                                  '--spec', str(folder / 'spec.json'), '--check-source'], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, f'{v}: {run.stdout[-600:]}')

    def test_a_picture_in_a_sub_folder_keeps_its_relative_path(self):
        self.spec['source']['image'] = 'inputs/source.png'
        self.spec['source']['sha256'] = write_picture(self.tmp / 'inputs' / 'source.png')
        self.spec_path.write_text(json.dumps(self.spec), encoding='utf-8')
        self.assertEqual(self.cli()[0], 0)
        for v in VARIANTS:
            folder = self.out / v
            self.assertTrue((folder / 'inputs' / 'source.png').is_file(), v)
            spec = json.loads((folder / 'spec.json').read_text(encoding='utf-8'))
            self.assertTrue(validate_spec.validate(spec, source_root=folder, source_size=[W, H])['valid'], v)

    def test_a_missing_picture_is_refused(self):
        (self.tmp / 'reference.png').unlink()
        code, out, err = self.cli()
        self.assertEqual(code, 2)
        self.assertTrue(err.startswith('ERROR'), err)
        self.assertIn('reference.png', err)
        self.assertFalse(self.out.exists() and any(self.out.iterdir()))

    def test_a_picture_outside_the_spec_folder_is_refused(self):
        self.spec['source']['image'] = '../reference.png'
        self.spec_path.write_text(json.dumps(self.spec), encoding='utf-8')
        code, out, err = self.cli()
        self.assertEqual(code, 2)
        self.assertIn('source.image', err)


class Visibility(unittest.TestCase):
    def test_nearer_part_wins_the_pixel(self):
        spec = make_spec()
        u, v = visible_pixel(spec, 'stool')
        ids, idmap = pt.render_ids(spec, CAMERA, W, H, (int(u), int(v), int(u) + 1, int(v) + 1))
        self.assertEqual(ids[idmap[0, 0]], 'stool')          # the carpet is under it, the stool is nearer

    def test_the_nearer_part_wins_whatever_the_drawing_order(self):
        # the carpet comes last in the list: a "last drawn wins" rule would let it cover the stool
        spec = make_spec()
        spec['components'] = [c for c in spec['components'] if c['id'] != 'carpet'] + [c for c in spec['components'] if c['id'] == 'carpet']
        u, v = visible_pixel(make_spec(), 'stool')
        ids, idmap = pt.render_ids(spec, CAMERA, W, H, (int(u), int(v), int(u) + 1, int(v) + 1))
        self.assertEqual(ids[idmap[0, 0]], 'stool')

    def test_a_near_box_listed_first_beats_a_far_box_listed_after_it(self):
        spec = make_spec()
        base = next(c for c in spec['components'] if c['id'] == 'stool')
        near, far = copy.deepcopy(base), copy.deepcopy(base)
        near.update(id='near_box', scope_id='near_box', location=[0, -2, 1], params={'size': [1, 1, 1]})
        far.update(id='far_box', scope_id='far_box', location=[4.67, 2, 0.47], params={'size': [2, 0.5, 2]})
        spec['components'] = [near, far]                         # the far one is drawn last
        u, v = camera_math.project([0, -2, 1], CAMERA, W, H)[:2]
        fu, fv = camera_math.project([4.67, 2, 0.47], CAMERA, W, H)[:2]
        self.assertLess(math.hypot(u - fu, v - fv), 3, 'the fixture must put both boxes on one line of sight')
        ids, idmap = pt.render_ids(spec, CAMERA, W, H, (int(u), int(v), int(u) + 1, int(v) + 1))
        self.assertEqual(ids[idmap[0, 0]], 'near_box')
        spec['components'] = [far, near]
        ids, idmap = pt.render_ids(spec, CAMERA, W, H, (int(u), int(v), int(u) + 1, int(v) + 1))
        self.assertEqual(ids[idmap[0, 0]], 'near_box')

    def test_a_hollow_ring_in_front_of_a_wall_does_not_take_the_wall_swatch(self):
        spec = make_spec()
        keep = [c for c in spec['components'] if c['id'] == 'carpet']
        wall = {'id': 'wall', 'scope_id': 'wall', 'owner': 'booth', 'kind': 'box', 'role': 'wall', 'critical': False,
                'evidence': {'status': 'observed', 'roi_xyxy_norm': [0.1, 0.1, 0.2, 0.2], 'note': 'x'},
                'location': [0, 2.3, 1.5], 'rotation_deg': [0, 0, 0], 'params': {'size': [8, 0.15, 3]},
                'material': {'color': [0.2, 0.2, 0.3, 1], 'color_space': 'srgb'}}
        ring = copy.deepcopy(wall)
        ring.update(id='frame', scope_id='frame', kind='rounded_ring', location=[0, -0.5, 1.5], rotation_deg=[90, 0, 0],
                    params={'outer_size': [3, 2], 'inner_size': [2, 1], 'outer_radius': 0.4, 'inner_radius': 0.2, 'height': 0.2,
                            'segments_per_corner': 8})
        spec['components'] = keep + [wall, ring]
        centre = camera_math.project([0, -0.5, 1.5], CAMERA, W, H)[:2]          # the middle of the hole
        swatches = [{'id': f's_{r}', 'bbox_px': bbox_around(centre), 'role': r} for r in ('dominant', 'secondary', 'accent')]
        ids, idmap = pt.render_ids(spec, CAMERA, W, H)
        self.assertIn(ids.index('frame'), set(idmap.ravel().tolist()), 'the ring itself must be visible somewhere')
        cover = pt._swatch_components(spec, swatches, CAMERA, W, H)
        self.assertEqual(cover['dominant'], ['wall'])            # its box is larger than the wall's smallest rival, but the hole shows the wall
        # a swatch on the ring itself belongs to the ring
        left = camera_math.project([-1.3, -0.5, 1.5], CAMERA, W, H)[:2]
        on_ring = [{'id': 's', 'bbox_px': bbox_around(left, 2), 'role': r} for r in ('dominant', 'secondary', 'accent')]
        self.assertEqual(pt._swatch_components(spec, on_ring, CAMERA, W, H)['dominant'][0], 'frame')


class SwapAndLimits(unittest.TestCase):
    """The regression example spec (an invented booth) with an eight-item answer sheet."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.spec = json.loads((ROOT / 'booth-image-to-model' / 'examples' / 'regression' / 'candidate_spec.json').read_text(encoding='utf-8'))
        self.spec['source']['sha256'] = write_picture(self.tmp / self.spec['source']['image'], (640, 480))
        mats = {c['id']: c['material'] for c in self.spec['components']}
        mats['rear_wall'].update(color=[0.2, 0.3, 0.5, 1], color_space='srgb')          # dominant: sRGB
        mats['storage'].update(color=[0.5, 0.2, 0.1, 1])                                 # secondary: no colour space, i.e. linear
        mats['storage'].pop('color_space', None)
        mats['round_display'].update(color=[0.9, 0.3, 0.1, 1], color_space='srgb')      # accent
        (self.tmp / 'spec.json').write_text(json.dumps(self.spec), encoding='utf-8')
        (self.tmp / 'box_fit.json').write_text(json.dumps({'image': {'width': 640, 'height': 480}, 'camera': self.spec['camera']}), encoding='utf-8')
        (self.tmp / 'map.json').write_text(json.dumps({'round_display': ['round_display', 'round_display_top']}), encoding='utf-8')
        self.out = self.tmp / 'out'

    def truth(self, heroes):
        ids = ['booth_carpet', 'top_ring', 'main_portal', 'reception', 'rear_wall', 'round_display', 'storage', 'signage']
        inv = {'schema_version': 'truth.v1', 'picture_id': 'pic_T',
               'items': [{'id': i, 'name': i, 'kind_hint': 'box', 'hero': i in heroes} for i in ids]}
        at = {r: visible_pixel(self.spec, c, 640, 480) for r, c in (('dominant', 'rear_wall'), ('secondary', 'storage'), ('accent', 'round_display'))}
        sw = {'schema_version': 'truth.v1', 'picture_id': 'pic_T',
              'swatches': [{'id': f's_{r}', 'bbox_px': bbox_around(at[r], 2), 'role': r} for r in at]}
        folder = self.tmp / 'truth'
        folder.mkdir(exist_ok=True)
        (folder / 'inventory.json').write_text(json.dumps(inv), encoding='utf-8')
        (folder / 'swatches.json').write_text(json.dumps(sw), encoding='utf-8')
        return folder

    def run_pt(self, truth):
        return run_cli('--spec', self.tmp / 'spec.json', '--fit', self.tmp / 'box_fit.json', '--truth', truth,
                       '--map', self.tmp / 'map.json', '--out', self.out)

    def test_b6_never_removes_the_dominant_and_secondary_parts_and_swaps_colour_and_space(self):
        # non-hero parts: top_ring, rear_wall (dominant), storage (secondary), round_display (accent): the accent part may go
        code, out, err = self.run_pt(self.truth({'main_portal', 'reception'}))
        self.assertEqual(code, 0, err)
        base = {c['id']: c for c in self.spec['components']}
        b6 = json.loads((self.out / 'B6' / 'spec.json').read_text(encoding='utf-8'))
        left = {c['id']: c for c in b6['components']}
        self.assertEqual(set(base) - set(left), {'main_portal', 'top_ring', 'round_display', 'round_display_top'})
        self.assertIn('rear_wall', left)
        self.assertIn('storage', left)
        self.assertNotIn('round_display', left)
        wall, store = left['rear_wall']['material'], left['storage']['material']
        self.assertEqual(wall['color'], base['storage']['material']['color'])
        self.assertNotIn('color_space', wall)                                  # it took the colour space of the other part: none
        self.assertEqual(store['color'], base['rear_wall']['material']['color'])
        self.assertEqual(store['color_space'], 'srgb')
        rep = json.loads((self.out / 'perturb_report.json').read_text(encoding='utf-8'))['variants']['B6']
        self.assertEqual(sorted(rep['swapped']), ['rear_wall', 'storage'])
        # the report measures the two colours as they look: the sRGB one against the linear one converted to sRGB
        want = pt.delta_e76(pt.srgb_to_lab([0.2, 0.3, 0.5]), pt.srgb_to_lab([pt._to_srgb(v) for v in (0.5, 0.2, 0.1)]))
        self.assertAlmostEqual(rep['swap_delta_e76'], want, places=6)
        self.assertGreater(want, 5)
        for v in VARIANTS:
            folder = self.out / v
            spec = json.loads((folder / 'spec.json').read_text(encoding='utf-8'))
            self.assertTrue(validate_spec.validate(spec, source_root=folder, source_size=[640, 480])['valid'], v)

    def test_b6_is_refused_when_the_only_cuttable_parts_are_too_few_and_says_so(self):
        # non-hero parts: rear_wall (dominant), storage (secondary), round_display (accent): only the accent part could go
        code, out, err = self.run_pt(self.truth({'top_ring', 'main_portal', 'reception'}))
        self.assertEqual(code, 2, err)
        self.assertTrue(err.startswith('ERROR'), err)
        self.assertIn('B6', err)
        self.assertIn('dominant or secondary', err)
        self.assertIn('found 1 of 3', err)
        self.assertFalse(self.out.exists() and any(self.out.iterdir()))


class BadInput(Base):
    def assert_refused(self, code, err, *words):
        self.assertEqual(code, 2, err)
        self.assertTrue(err.startswith('ERROR'), err)
        for w in words:
            self.assertIn(w, err)
        self.assertFalse(self.out.exists() and any(self.out.iterdir()), 'nothing may be written when the input is refused')

    def test_map_naming_a_part_that_is_not_in_the_spec_is_refused(self):
        self.map_path.write_text(json.dumps({'sales_desk': 'no_such_part'}), encoding='utf-8')
        code, out, err = self.cli()
        self.assert_refused(code, err, 'no_such_part')

    def test_map_key_that_is_not_an_inventory_id_is_refused(self):
        self.map_path.write_text(json.dumps({'not_in_inventory': 'sales_desk'}), encoding='utf-8')
        code, out, err = self.cli()
        self.assert_refused(code, err, 'not_in_inventory')

    def test_no_hero_part_refuses_b5_and_b6(self):
        inv = json.loads((self.truth / 'inventory.json').read_text(encoding='utf-8'))
        for it in inv['items']:
            it['hero'] = False
        (self.truth / 'inventory.json').write_text(json.dumps(inv), encoding='utf-8')
        code, out, err = self.cli()
        self.assert_refused(code, err, 'B5', 'B6', 'hero')

    def test_hero_parts_that_are_not_in_the_spec_refuse_b5_and_b6(self):
        spec = make_spec()
        spec['source']['sha256'] = self.spec['source']['sha256']
        gone = {'fascia', 'sales_desk'}                     # both hero parts of the answer sheet
        spec['components'] = [c for c in spec['components'] if c['id'] not in gone]
        spec['scope']['decisions'] = [d for d in spec['scope']['decisions'] if d['id'] not in gone]
        self.spec_path.write_text(json.dumps(spec), encoding='utf-8')
        self.map_path.write_text(json.dumps({'display': ['display_base', 'display_top']}), encoding='utf-8')
        code, out, err = self.cli()
        self.assert_refused(code, err, 'B5', 'B6', 'hero')

    def test_accent_swatch_that_covers_no_part_is_refused(self):
        sw = json.loads((self.truth / 'swatches.json').read_text(encoding='utf-8'))
        for s in sw['swatches']:
            if s['role'] == 'accent':
                s['bbox_px'] = [0, 0, 4, 4]
        (self.truth / 'swatches.json').write_text(json.dumps(sw), encoding='utf-8')
        code, out, err = self.cli()
        self.assert_refused(code, err, 'accent')

    def test_swatch_box_that_is_a_string_is_refused_without_a_traceback(self):
        sw = json.loads((self.truth / 'swatches.json').read_text(encoding='utf-8'))
        sw['swatches'][0]['bbox_px'] = '10,10,20,20'
        (self.truth / 'swatches.json').write_text(json.dumps(sw), encoding='utf-8')
        code, out, err = self.cli()
        self.assert_refused(code, err, 'bbox_px')
        self.assertNotIn('Traceback', err)

    def test_wrongly_typed_answer_sheet_fields_are_refused(self):
        inv = json.loads((self.truth / 'inventory.json').read_text(encoding='utf-8'))
        inv['items'][0]['hero'] = 'yes'
        (self.truth / 'inventory.json').write_text(json.dumps(inv), encoding='utf-8')
        code, out, err = self.cli()
        self.assert_refused(code, err, 'hero')
        inv['items'][0]['hero'] = True
        inv['items'][1] = 'sales_desk'
        (self.truth / 'inventory.json').write_text(json.dumps(inv), encoding='utf-8')
        code, out, err = self.cli()
        self.assert_refused(code, err, 'items[1]')
        (self.truth / 'inventory.json').write_text(json.dumps([1, 2]), encoding='utf-8')
        code, out, err = self.cli()
        self.assert_refused(code, err, 'items')

    def test_unusable_fit_files_are_refused_as_bad_fit(self):
        def broken(change):
            cam = copy.deepcopy(CAMERA)
            fit = {'image': {'width': W, 'height': H}, 'camera': cam}
            change(fit)
            self.fit_path.write_text(json.dumps(fit), encoding='utf-8')
            code, out, err = self.cli()
            self.assert_refused(code, err, 'BAD_FIT')
            self.assertNotIn('Traceback', err)
        broken(lambda f: f['camera'].update(position=[1.0, 2.0]))
        broken(lambda f: f['camera'].update(target=[0.0]))
        broken(lambda f: f['camera'].update(lens_mm=0))
        broken(lambda f: f['camera'].update(sensor_width_mm=-36))
        broken(lambda f: f['image'].update(width=0))
        broken(lambda f: f['image'].update(height=-480))
        broken(lambda f: f['image'].update(width='640'))
        broken(lambda f: f.pop('camera'))

    def test_duplicate_inventory_ids_are_refused(self):
        inv = json.loads((self.truth / 'inventory.json').read_text(encoding='utf-8'))
        inv['items'][3]['id'] = inv['items'][2]['id']
        (self.truth / 'inventory.json').write_text(json.dumps(inv), encoding='utf-8')
        code, out, err = self.cli()
        self.assert_refused(code, err, 'twice', inv['items'][2]['id'])

    def test_missing_files_are_refused(self):
        code, out, err = self.cli(fit=self.tmp / 'nope.json')
        self.assert_refused(code, err, 'nope.json')

    def test_variant_that_does_not_validate_is_refused(self):
        # the floor is the only part of its inventory item and it is not allowed to move or vanish: break the footprint instead
        spec = make_spec()
        spec['scale']['footprint_m'] = [9, 5]
        self.spec_path.write_text(json.dumps(spec), encoding='utf-8')
        code, out, err = self.cli()
        self.assert_refused(code, err, 'validate')


class Help(unittest.TestCase):
    def test_help_exits_zero_and_names_the_options(self):
        code, out, err = run_cli('--help')
        self.assertEqual(code, 0)
        for opt in ('--spec', '--fit', '--truth', '--map', '--out'):
            self.assertIn(opt, out)


if __name__ == '__main__':
    unittest.main()
