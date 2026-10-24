"""Design route: design.json -> spec compiler, element kinds, text provenance, brief checks, and one native build."""
from __future__ import annotations
import copy
import hashlib
import json
import math
import os
import random
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import camera_math as cmath
import design_compile as dc
import motifs as mt
from design_check import run as design_check_run, world_bounds
from validate_spec import validate

TEMPLATE = json.loads((ROOT / 'templates' / 'design_template.json').read_text(encoding='utf-8'))


def design(**changes):
    d = copy.deepcopy(TEMPLATE)
    d.update(changes)
    return d


def compile_(d, canvas=(320, 200)):
    with tempfile.TemporaryDirectory() as tmp:
        spec, report = dc.compile_design(d, tmp, canvas)
        files = sorted(p.name for p in Path(tmp).iterdir())
        raw = (Path(tmp) / 'spec.json').read_bytes() if (Path(tmp) / 'spec.json').exists() else None
    return spec, report, files, raw


def walls(spec):
    return sorted(c['id'] for c in spec['components'] if c['id'].startswith('perimeter_wall_'))


class Template(unittest.TestCase):
    def test_template_compiles_validates_and_meets_its_brief(self):
        spec, report, files, _ = compile_(TEMPLATE)
        self.assertEqual(report['status'], 'ok', report['dropped'])
        self.assertEqual(files, ['compile_report.json', 'design_canvas.png', 'design_context.json', 'spec.json'])
        self.assertTrue(validate(spec, source_size=[320, 200])['valid'])
        self.assertEqual(report['brief']['status'], 'satisfied', report['brief'])
        self.assertEqual(report['design_check']['counts']['advisory'], 0, report['design_check'])
        self.assertEqual(spec['scale']['basis'], 'user_dimensions')
        self.assertEqual(spec['camera']['fit_status'], 'unfitted')
        self.assertEqual(spec['source']['image'], 'design_canvas.png')

    def test_canvas_on_disk_matches_the_recorded_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec, _ = dc.compile_design(TEMPLATE, tmp, (320, 200))
            self.assertTrue(validate(spec, tmp, source_size=[320, 200])['valid'])

    def test_same_design_gives_the_same_bytes(self):
        self.assertEqual(compile_(TEMPLATE)[3], compile_(TEMPLATE)[3])

    def test_every_part_carries_its_element_group_and_provenance(self):
        spec, report, _, _ = compile_(TEMPLATE)
        ids = {e['id'] for e in TEMPLATE['elements']}
        for c in spec['components']:
            if c['id'] == 'floor' or c['id'].startswith('perimeter_wall_'):
                continue
            self.assertIn(c.get('group'), ids, c['id'])
            self.assertEqual(c['evidence']['status'], 'inferred')
            self.assertIn('Generated design', c['evidence']['note'])

    def test_hero_camera_sees_the_whole_booth(self):
        spec, _, _, _ = compile_(TEMPLATE)
        cam = spec['camera']
        for c in spec['components']:
            if c['kind'] == 'text':
                continue
            lo, hi = world_bounds(c)
            for x in (lo[0], hi[0]):
                for y in (lo[1], hi[1]):
                    for z in (lo[2], hi[2]):
                        u, v, _ = cmath.project((x, y, z), cam, 320, 200)
                        self.assertTrue(0 <= u <= 320 and 0 <= v <= 200, (c['id'], u, v))


class Layout(unittest.TestCase):
    def test_floor_equals_the_rounded_footprint(self):
        d = design()
        d['form'] = dict(d['form'], footprint_m=[5.123456, 4.0])
        spec, report, _, _ = compile_(d)
        floor = next(c for c in spec['components'] if c['role'] == 'floor')
        self.assertEqual(floor['params']['size'][:2], spec['scale']['footprint_m'])

    def test_walls_only_on_closed_sides(self):
        spec, _, _, _ = compile_(TEMPLATE)
        self.assertEqual(walls(spec), ['perimeter_wall_back', 'perimeter_wall_left', 'perimeter_wall_right'])

    def test_island_has_no_walls_and_wall_elements_are_dropped(self):
        d = design(booth_type='island')
        d['form'] = dict(d['form'], open_sides=['front', 'back', 'left', 'right'])
        spec, report, _, _ = compile_(d)
        self.assertEqual(walls(spec), [])
        dropped = {x['id'] for x in report['dropped']}
        self.assertEqual(dropped, {'hero_wall', 'screen'})
        self.assertEqual(report['status'], 'partial')

    def test_booth_type_must_match_the_open_sides(self):
        d = design(booth_type='corner')
        with self.assertRaises(dc.DesignError) as ctx:
            compile_(d)
        self.assertEqual(ctx.exception.errors[0]['code'], 'TOPOLOGY')

    def test_parts_stay_inside_the_footprint_and_under_the_limit(self):
        spec, report, _, _ = compile_(TEMPLATE)
        W, D = spec['scale']['footprint_m']
        for c in spec['components']:
            if c['kind'] == 'text' or c['role'] == 'floor':
                continue
            lo, hi = world_bounds(c)
            self.assertGreaterEqual(lo[0], -W / 2 - 1e-6, c['id'])
            self.assertLessEqual(hi[0], W / 2 + 1e-6, c['id'])
            self.assertGreaterEqual(lo[1], -D / 2 - 1e-6, c['id'])
            self.assertLessEqual(hi[1], D / 2 + 1e-6, c['id'])
            self.assertLessEqual(hi[2], TEMPLATE['form']['height_limit_m'] + 1e-6, c['id'])

    def test_wall_elements_keep_clear_of_the_header_band(self):
        d = design()
        d['elements'].append({'id': 'side_slats', 'kind': 'feature_wall', 'side': 'right', 'params': {'variant': 'panel'}})
        d['elements'] = [e for e in d['elements'] if e['id'] != 'greens']
        spec, report, _, _ = compile_(d)
        self.assertFalse([w for w in report['warnings'] if 'overlap' in w], report['warnings'])
        self.assertIn({'id': 'side_slats', 'what': 'kept clear of the header band on the front side'}, report['adjustments'])

    def test_overlap_is_reported(self):
        d = design()
        d['elements'].append({'id': 'crate', 'kind': 'box', 'side': 'centre', 'centre_m': [-1.6, 0.2], 'size_m': [0.6, 0.6, 0.6]})
        _, report, _, _ = compile_(d)
        self.assertTrue(any('meeting and crate overlap' in w for w in report['warnings']), report['warnings'])


def every_kind_design():
    """The design with one element of every kind that existed before canopy_frame (its output is pinned by a hash below)."""
    d = design(booth_type='peninsula')
    d['form'] = dict(d['form'], footprint_m=[9.0, 6.0], height_limit_m=4.5, open_sides=['front', 'left', 'right'])
    d['elements'] = [
        {'id': 'fw', 'kind': 'feature_wall', 'side': 'back', 'along': 0.3, 'size_m': [3.0, None, None], 'params': {'variant': 'panel'}},
        {'id': 'gp', 'kind': 'graphic_panel', 'side': 'back', 'along': 0.85, 'material': {'emissive': True}},
        {'id': 'tvs', 'kind': 'tv', 'side': 'back', 'along': 0.6, 'params': {'inch': 65}},
        {'id': 'led', 'kind': 'led_wall', 'side': 'back', 'along': 0.1, 'size_m': [1.5, None, 1.5]},
        {'id': 'backwall', 'kind': 'wall', 'side': 'back', 'material': {'color_hex': '#334455'}},
        {'id': 'hb', 'kind': 'header_band', 'params': {'sides': ['front', 'left', 'right']}},
        {'id': 'halo', 'kind': 'ring', 'params': {'inset_m': 1.2}},
        {'id': 'lights', 'kind': 'spotlights', 'side': 'front', 'count': 5},
        {'id': 'desk', 'kind': 'counter', 'side': 'front', 'along': 0.5, 'params': {'floating': True}},
        {'id': 'table', 'kind': 'round_table', 'side': 'centre', 'centre_m': [2.8, 1.0]},
        {'id': 'seat', 'kind': 'chair', 'side': 'centre', 'centre_m': [2.8, 0.2], 'yaw_deg': 180},
        {'id': 'lounge', 'kind': 'seating_group', 'side': 'centre', 'centre_m': [-2.8, 0.6], 'count': 4},
        {'id': 'pl', 'kind': 'planter', 'side': 'left', 'along': 0.7, 'count': 3},
        {'id': 'plinth', 'kind': 'display_plinth', 'side': 'centre', 'centre_m': [0.0, 0.8]},
        {'id': 'store', 'kind': 'storage_block', 'side': 'back', 'along': 0.95, 'size_m': [1.0, 1.0, None]},
        {'id': 'divider', 'kind': 'partition', 'side': 'centre', 'centre_m': [0.0, -0.4], 'size_m': [1.2, 0.1, 1.2]},
        {'id': 'crate', 'kind': 'box', 'side': 'centre', 'centre_m': [1.5, -1.5]},
    ]
    d['requirements'] = []
    return d


class EveryKind(unittest.TestCase):
    def test_every_element_kind_builds(self):
        d = every_kind_design()
        d['elements'].append({'id': 'crown', 'kind': 'canopy_frame', 'size_m': [5.0, 3.0, 0.3]})
        d['elements'].append({'id': 'litwall', 'kind': 'lit_panel', 'side': 'centre', 'centre_m': [0.0, 1.8], 'size_m': [2.0, 0.15, 2.4],
                              'params': {'niche': {}}})
        d['elements'].append({'id': 'phone_one', 'kind': 'prop_block', 'side': 'centre', 'centre_m': [-1.5, -1.5],
                              'size_m': [0.08, 0.16, 0.01], 'z_m': 0.9, 'params': {'label': 'phone'}})
        spec, report, _, _ = compile_(d)
        self.assertEqual(report['dropped'], [])
        kinds = {e['kind'] for e in d['elements']}
        self.assertEqual(kinds, set(mt.KINDS))
        built = {e['id'] for e in report['elements']}
        self.assertEqual(built, {e['id'] for e in d['elements']})
        self.assertTrue(validate(spec, source_size=[320, 200])['valid'])
        posts = [c for c in spec['components'] if c['role'] == 'header_post']
        self.assertGreaterEqual(len(posts), 4)       # header free ends and the ring (hanging not allowed)


# ------------------------------------------------------------------ canopy_frame and the overhead hook
SIDES4 = ['front', 'back', 'left', 'right']
GRID = {'type': 'grid', 'spacing': [0.1, 0.1], 'line_width': 0.005, 'line_color': [0.2, 0.2, 0.2], 'coordinates': 'auto'}


def island(*elements, hanging=True, footprint=(8.0, 6.0), height=4.5):
    """A booth open on all four sides (no walls) holding only the given elements; the floor sits at 0.1 m."""
    d = design(booth_type='island')
    d['form'] = dict(d['form'], footprint_m=list(footprint), height_limit_m=height, open_sides=list(SIDES4), hanging_allowed=hanging)
    d['elements'] = [copy.deepcopy(e) for e in elements]
    d['requirements'] = []
    return d


def canopy(**kw):
    el = {'id': 'crown', 'kind': 'canopy_frame', 'size_m': [5.0, 3.0, 0.3]}
    el.update(kw)
    return el


def pieces(spec, eid='crown'):
    return [c for c in spec['components'] if c.get('group') == eid and c['role'] in ('canopy_frame', 'canopy_sign')]


def plan_polygon(c):
    """World plan polygon of a profile_extrusion that lies flat (rotated -90 degrees about X)."""
    assert c['kind'] == 'profile_extrusion' and c['rotation_deg'] == [-90, 0, 0], (c['id'], c['kind'], c['rotation_deg'])
    return [(c['location'][0] + x, c['location'][1] + z) for x, z in c['params']['profile']]


def plan_footprint(c):
    """Plan polygon of any canopy piece: a flat piece (rotated -90 degrees about X) or a sign end piece (profile in elevation)."""
    if c['rotation_deg'] == [-90, 0, 0]:
        return plan_polygon(c)
    if c['rotation_deg'] == [0, 0, 0]:
        xs = [c['location'][0] + x for x, _ in c['params']['profile']]
        ys = (c['location'][1] - c['params']['depth'] / 2, c['location'][1] + c['params']['depth'] / 2)
        return [(min(xs), ys[0]), (max(xs), ys[0]), (max(xs), ys[1]), (min(xs), ys[1])]
    assert c['rotation_deg'] == [0, 0, 90], c['rotation_deg']
    ys = [c['location'][1] + x for x, _ in c['params']['profile']]
    xs = (c['location'][0] - c['params']['depth'] / 2, c['location'][0] + c['params']['depth'] / 2)
    return [(xs[0], min(ys)), (xs[1], min(ys)), (xs[1], max(ys)), (xs[0], max(ys))]


def piece_lowest(c):
    """Lowest height of a canopy piece."""
    if c['rotation_deg'] == [-90, 0, 0]:
        return z_range(c)[0]
    return c['location'][2] + min(z for _, z in c['params']['profile'])


def z_range(c):
    return c['location'][2] - c['params']['depth'] / 2, c['location'][2] + c['params']['depth'] / 2


def group_order(spec):
    groups = []
    for c in spec['components']:
        if c.get('group') and c['group'] not in groups:
            groups.append(c['group'])
    return groups


def inside(poly, x, y):
    hit = False
    for i in range(len(poly)):
        (x0, y0), (x1, y1) = poly[i], poly[(i + 1) % len(poly)]
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            hit = not hit
    return hit


def in_rrect(x, y, rect, radii, delta=0.0):
    """Point in a rounded rectangle (x0, x1, y0, y1) with corner radii (front-left, front-right, back-right, back-left),
    grown by delta (negative shrinks it)."""
    x0, x1, y0, y1 = rect[0] - delta, rect[1] + delta, rect[2] - delta, rect[3] + delta
    if not (x0 <= x <= x1 and y0 <= y <= y1):
        return False
    for (cx_, cy_), r, (sx, sy) in zip(((x0, y0), (x1, y0), (x1, y1), (x0, y1)), radii, ((1, 1), (-1, 1), (-1, -1), (1, -1))):
        r = max(0.0, r + delta)
        ax, ay = cx_ + sx * r, cy_ + sy * r
        if (x - ax) * sx < 0 and (y - ay) * sy < 0 and math.hypot(x - ax, y - ay) > r:
            return False
    return True


def ring_shape(ow, od, bands, radius, centre=(0.0, 0.0)):
    """The analytic outer and inner rounded rectangles of a canopy: (outer rect, outer radii, inner rect, inner radii)."""
    cx, cy = centre
    outer = (cx - ow / 2, cx + ow / 2, cy - od / 2, cy + od / 2)
    inner = (outer[0] + bands['left'], outer[1] - bands['right'], outer[2] + bands['front'], outer[3] - bands['back'])
    fl, fr = max(bands['front'], bands['left']), max(bands['front'], bands['right'])
    br_, bl = max(bands['back'], bands['right']), max(bands['back'], bands['left'])
    half = min(inner[1] - inner[0], inner[3] - inner[2]) / 2          # the opening's corners never round beyond half its short side
    return outer, (radius,) * 4, inner, tuple(min(max(0.0, radius - m), half) for m in (fl, fr, br_, bl))


class OverheadHook(unittest.TestCase):
    def test_overhead_hook_dispatches_new_kinds_and_keeps_old_ones(self):
        calls = []

        def probe(b, frame, el, look, note):
            calls.append((frame, el['id'], look, note))
            frame.assume(el['id'], 'probe_value', 7)
            frame.adjust(el['id'], 'probe adjusted')
            frame.warn('probe warned')
            b.box(f"{el['id']}_probe", 'shell', 'probe', (-0.1, 0.1), (-0.1, 0.1), (3.0, 3.2), look['color'], inferred=True)

        d = island({'id': 'desk', 'kind': 'counter', 'side': 'front', 'along': 0.5},
                   {'id': 'prop', 'kind': 'box', 'side': 'centre', 'centre_m': [1.0, 1.0]},
                   {'id': 'hb', 'kind': 'header_band', 'params': {'sides': ['front']}},
                   {'id': 'halo', 'kind': 'ring', 'params': {'inset_m': 1.2}})
        with mock.patch.dict(mt.OVERHEAD_BUILDERS, {'box': probe}):
            spec, report, _, _ = compile_(d)
        self.assertEqual(len(calls), 1)
        frame, eid, look, note = calls[0]
        self.assertEqual(eid, 'prop')
        self.assertIsInstance(frame, mt.Frame)
        self.assertEqual((frame.X0, frame.X1, frame.Y0, frame.Y1), (-4.0, 4.0, -3.0, 3.0))
        self.assertEqual((frame.zf, frame.H), (0.1, 4.5))
        self.assertAlmostEqual(frame.wall_top, 3.0)
        self.assertEqual(list(frame.open_sides), SIDES4)
        self.assertEqual(list(frame.closed_sides), [])
        self.assertIs(frame.hanging_allowed, True)
        self.assertIn('color', look)
        self.assertIn("Design element 'prop'", note)
        self.assertIn({'id': 'prop', 'what': 'probe_value', 'value': 7}, report['assumed'])
        self.assertIn({'id': 'prop', 'what': 'probe adjusted'}, report['adjustments'])
        self.assertIn('probe warned', report['warnings'])
        self.assertEqual(report['dropped'], [])
        ids = [c['id'] for c in spec['components']]
        self.assertIn('prop_probe', ids)
        self.assertNotIn('prop', ids, 'the box builder must not run for a kind that has an overhead builder')
        # the hook parts are sorted with the header band and the ring, before every floor-standing element
        groups = group_order(spec)
        self.assertEqual(groups[:3], ['hb', 'prop', 'halo'])
        self.assertLess(groups.index('prop'), groups.index('desk'))
        for kind in ('header_band', 'ring', 'spotlights'):
            self.assertNotIn(kind, mt.OVERHEAD_BUILDERS, 'the three old overhead kinds keep their own code')

    def test_canopy_parts_come_before_floor_parts_in_the_spec(self):
        d = island({'id': 'desk', 'kind': 'counter', 'side': 'front', 'along': 0.5}, canopy(),
                   {'id': 'hb', 'kind': 'header_band', 'params': {'sides': ['front']}})
        spec, report, _, _ = compile_(d)
        self.assertEqual(report['dropped'], [])
        self.assertEqual(group_order(spec), ['hb', 'crown', 'desk'])

    def test_role_and_param_checks_come_from_motifs_tables(self):
        self.assertNotIn('canopy_frame', dc.ROLE, 'the colour role of a new kind lives in motifs.ROLE')
        accent = dc._rgba(TEMPLATE['style']['accent_hex'])
        dominant = dc._rgba(TEMPLATE['style']['dominant_hex'])
        with mock.patch.dict(mt.ROLE, {'canopy_frame': 'accent'}):
            spec, _, _, _ = compile_(island(canopy()))
            self.assertTrue(all(c['material']['color'][:3] == accent[:3] for c in pieces(spec)))
        with mock.patch.dict(mt.ROLE, {'canopy_frame': 'dominant'}):
            spec, _, _, _ = compile_(island(canopy()))
            self.assertTrue(all(c['material']['color'][:3] == dominant[:3] for c in pieces(spec)))
        # a role in the compiler's own table still wins for the old kinds
        self.assertEqual(dc.ROLE['box'], 'secondary')

        seen = []

        def check(el, form):
            seen.append((el['id'], list(form['footprint_m'])))
            return [('params.role', 'no roles today'), ('size_m', 'too big')]
        d = island({'id': 'prop', 'kind': 'box', 'side': 'centre', 'centre_m': [1.0, 1.0]})
        with mock.patch.dict(mt.PARAM_CHECKS, {'box': check}):
            errors = dc.validate_design(d)
            with self.assertRaises(dc.DesignError):
                compile_(d)
        self.assertEqual(seen[0], ('prop', [8.0, 6.0]))
        self.assertEqual([(e['code'], e['where']) for e in errors],
                         [('PARAM', 'elements[0].params.role'), ('PARAM', 'elements[0].size_m')])
        self.assertEqual(dc.validate_design(d), [])

    def test_design_pattern_is_copied_to_parts_and_validated(self):
        d = island({'id': 'slatwall', 'kind': 'feature_wall', 'side': 'back', 'material': {'role': 'secondary', 'pattern': GRID}},
                   {'id': 'lamp', 'kind': 'graphic_panel', 'side': 'back', 'material': {'emissive': True, 'pattern': GRID}},
                   {'id': 'hb', 'kind': 'header_band', 'params': {'sides': ['front']}, 'text': {'body': 'DEMO COFFEE'},
                    'provenance': {'text': 'brief'}, 'material': {'pattern': GRID}},
                   {'id': 'plain', 'kind': 'box', 'side': 'centre', 'centre_m': [1.0, 1.0]})
        d['form'] = dict(d['form'], open_sides=['front', 'left', 'right'])
        d['booth_type'] = 'peninsula'
        d['style'] = dict(d['style'], floor_pattern=GRID)
        spec, report, _, _ = compile_(d)
        self.assertEqual(report['dropped'], [])
        self.assertTrue(report['valid'], report['validation_errors'])
        by_group = {}
        for c in spec['components']:
            by_group.setdefault(c.get('group'), []).append(c)
        slat = by_group['slatwall']
        self.assertGreaterEqual(len(slat), 2)
        for c in slat:
            self.assertEqual(c['material'].get('pattern'), GRID, c['id'])
        self.assertIsNot(slat[0]['material']['pattern'], slat[1]['material']['pattern'], 'each part gets its own copy')
        for c in by_group['lamp']:
            self.assertNotIn('pattern', c['material'], 'an emissive part keeps its own look')
        texts = [c for c in by_group['hb'] if c['kind'] == 'text']
        self.assertTrue(texts)
        for c in by_group['hb']:
            if c['kind'] == 'text':
                self.assertNotIn('pattern', c['material'])
            else:
                self.assertEqual(c['material'].get('pattern'), GRID, c['id'])
        for c in by_group['plain']:
            self.assertNotIn('pattern', c['material'])
        floor = next(c for c in spec['components'] if c['role'] == 'floor')
        self.assertEqual(floor['material'].get('pattern'), GRID)
        # a null floor pattern and no element pattern leave no trace
        d['style'] = dict(d['style'], floor_pattern=None)
        spec, _, _, _ = compile_(d)
        self.assertNotIn('pattern', next(c for c in spec['components'] if c['role'] == 'floor')['material'])

    def test_unknown_pattern_type_ends_the_compile_with_exit_code_2(self):
        bad = {'type': 'no_such_pattern', 'spacing': [0.1, 0.1]}
        for where in ('element', 'floor'):
            with self.subTest(where=where):
                d = island({'id': 'plain', 'kind': 'box', 'side': 'centre', 'centre_m': [1.0, 1.0],
                            'material': {'pattern': bad if where == 'element' else None}})
                if where == 'floor':
                    d['style'] = dict(d['style'], floor_pattern=bad)
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / 'design.json'
                    path.write_text(json.dumps(d), encoding='utf-8')
                    code, summary = dc.run(path, Path(tmp) / 'out', '320x200')
                    self.assertEqual(code, 2, summary)
                    self.assertFalse(summary['valid'])
                    self.assertTrue(any('PATTERN' in e['code'] for e in summary['validation_errors']), summary['validation_errors'])


class CanopyFrame(unittest.TestCase):
    def test_canopy_frame_band_widths_follow_each_side(self):
        bands = {'front': 0.2, 'back': 0.5, 'left': 0.3, 'right': 0.6}
        spec, report, _, _ = compile_(island(canopy(params={'corner_radius_m': 0.2, 'band_m': bands})))
        self.assertEqual(report['dropped'], [])
        parts = pieces(spec)
        self.assertGreaterEqual(len(parts), 2, 'profile_extrusion has no inner hole: the frame is made of several pieces')
        polys = [plan_polygon(c) for c in parts]

        def solid(x, y):
            return sum(inside(p, x, y) for p in polys)
        ox0, ox1, oy0, oy1 = -2.5, 2.5, -1.5, 1.5
        eps = 0.03
        # each band is solid up to its own width and open from there on: the opening is not centred
        for x, y in ((ox0 + bands['left'] - eps, 0.05), (ox1 - bands['right'] + eps, 0.05),
                     (0.05, oy0 + bands['front'] - eps), (0.05, oy1 - bands['back'] + eps)):
            self.assertEqual(solid(x, y), 1, (x, y))
        for x, y in ((ox0 + bands['left'] + eps, 0.05), (ox1 - bands['right'] - eps, 0.05),
                     (0.05, oy0 + bands['front'] + eps), (0.05, oy1 - bands['back'] - eps), (0.05, 0.05)):
            self.assertEqual(solid(x, y), 0, (x, y))
        self.assertEqual(solid(ox0 - eps, 0.05), 0)
        self.assertEqual(solid(0.05, oy1 + eps), 0)
        # the outer edge is the stated 5 x 3 m
        xs = [x for p in polys for x, _ in p]
        ys = [y for p in polys for _, y in p]
        self.assertAlmostEqual(min(xs), ox0, places=3)
        self.assertAlmostEqual(max(xs), ox1, places=3)
        self.assertAlmostEqual(min(ys), oy0, places=3)
        self.assertAlmostEqual(max(ys), oy1, places=3)
        # one height for the whole frame: 0.3 m, top 0.1 m under the limit
        for c in parts:
            lo, hi = z_range(c)
            self.assertAlmostEqual(lo, 4.1, places=3)
            self.assertAlmostEqual(hi, 4.4, places=3)
        # the other way round
        mirrored = {'front': 0.6, 'back': 0.3, 'left': 0.5, 'right': 0.2}
        spec, _, _, _ = compile_(island(canopy(params={'corner_radius_m': 0.2, 'band_m': mirrored})))
        polys = [plan_polygon(c) for c in pieces(spec)]
        self.assertEqual(sum(inside(p, ox0 + 0.45, 0.05) for p in polys), 1)
        self.assertEqual(sum(inside(p, ox1 - 0.45, 0.05) for p in polys), 0)
        self.assertEqual(sum(inside(p, 0.05, oy0 + 0.55) for p in polys), 1)
        self.assertEqual(sum(inside(p, 0.05, oy1 - 0.45) for p in polys), 0)

    def test_canopy_frame_pieces_tile_the_ring_exactly(self):
        bands = {'front': 0.25, 'back': 0.45, 'left': 0.35, 'right': 0.3}
        radius = 0.5
        outer, ro, inner, ri = ring_shape(5.0, 3.0, bands, radius)
        rng = random.Random(7)
        for sign in (None, {'corner': 'back_right', 'along_m': [1.3, 0.9], 'drop_m': 0.25, 'text_chars': 5}):
            with self.subTest(sign=sign):
                spec, _, _, _ = compile_(island(canopy(params={'corner_radius_m': radius, 'band_m': bands, 'sign': sign})))
                polys = [plan_polygon(c) for c in pieces(spec)]
                checked = 0
                for _ in range(3000):
                    x, y = rng.uniform(-2.7, 2.7), rng.uniform(-1.7, 1.7)
                    clear_in = in_rrect(x, y, outer, ro, -0.01) and not in_rrect(x, y, inner, ri, 0.01)
                    clear_out = not in_rrect(x, y, outer, ro, 0.01) or in_rrect(x, y, inner, ri, -0.01)
                    n = sum(inside(p, x, y) for p in polys)
                    if clear_in:
                        self.assertEqual(n, 1, (x, y))
                        checked += 1
                    elif clear_out:
                        self.assertEqual(n, 0, (x, y))
                self.assertGreater(checked, 500)

    def test_canopy_frame_sign_is_flush_with_the_frame(self):
        bands = {'front': 0.3, 'back': 0.4, 'left': 0.35, 'right': 0.3}
        along, drop, radius = [1.2, 0.8], 0.3, 0.3
        outer, ro, inner, ri = ring_shape(5.0, 3.0, bands, radius)
        rng = random.Random(11)
        for corner in ('front_left', 'front_right', 'back_left', 'back_right'):
            with self.subTest(corner=corner):
                sign = {'corner': corner, 'along_m': along, 'drop_m': drop, 'text_chars': 4}
                spec, report, _, _ = compile_(island(canopy(params={'corner_radius_m': radius, 'band_m': bands, 'sign': sign})))
                self.assertEqual(report['dropped'], [])
                signs = [c for c in pieces(spec) if c['role'] == 'canopy_sign']
                rest = [c for c in pieces(spec) if c['role'] == 'canopy_frame']
                self.assertEqual(len(signs), 1)
                self.assertTrue(rest)
                # the deeper piece reaches `drop` lower than the frame and ends at the same top
                s_lo, s_hi = z_range(signs[0])
                f_lo = min(z_range(c)[0] for c in rest)
                f_hi = max(z_range(c)[1] for c in rest)
                self.assertAlmostEqual(f_hi, s_hi, places=4)
                self.assertAlmostEqual(f_lo - s_lo, drop, places=4)
                for c in rest:
                    self.assertAlmostEqual(z_range(c)[0], f_lo, places=4)
                    self.assertAlmostEqual(z_range(c)[1], f_hi, places=4)
                # a sign of a different shape than the frame would show as a step: the deep piece and the rest together
                # cover the ring once, and the sign takes exactly the two stretches next to its corner
                sx = 1 if corner.endswith('left') else -1
                sy = 1 if corner.startswith('front') else -1
                x_corner, y_corner = (outer[0] if sx == 1 else outer[1]), (outer[2] if sy == 1 else outer[3])
                sign_poly = plan_polygon(signs[0])
                rest_polys = [plan_polygon(c) for c in rest]
                band_x = bands['left'] if sx == 1 else bands['right']
                band_y = bands['front'] if sy == 1 else bands['back']
                probes = ((x_corner + sx * (along[0] - 0.2), y_corner + sy * band_y / 2, True),
                          (x_corner + sx * (along[0] + 0.2), y_corner + sy * band_y / 2, False),
                          (x_corner + sx * band_x / 2, y_corner + sy * (along[1] - 0.2), True),
                          (x_corner + sx * band_x / 2, y_corner + sy * (along[1] + 0.2), False))
                for x, y, in_sign in probes:
                    self.assertEqual(inside(sign_poly, x, y), in_sign, (corner, x, y))
                    self.assertEqual(any(inside(p, x, y) for p in rest_polys), not in_sign, (corner, x, y))
                for _ in range(1500):
                    x, y = rng.uniform(-2.7, 2.7), rng.uniform(-1.7, 1.7)
                    if in_rrect(x, y, outer, ro, -0.01) and not in_rrect(x, y, inner, ri, 0.01):
                        n = inside(sign_poly, x, y) + sum(inside(p, x, y) for p in rest_polys)
                        self.assertEqual(n, 1, (corner, x, y))
                # outer faces are in the same planes as the frame's: the sign touches the outer edge on both of its sides
                sx_vals = [x for x, _ in sign_poly]
                sy_vals = [y for _, y in sign_poly]
                self.assertAlmostEqual(min(sx_vals) if sx == 1 else max(sx_vals), x_corner, places=3)
                self.assertAlmostEqual(min(sy_vals) if sy == 1 else max(sy_vals), y_corner, places=3)
                # the placeholder letters stand on the outer face of the first edge, inside the sign's height
                texts = [c for c in spec['components'] if c['kind'] == 'text' and c.get('group') == 'crown']
                self.assertEqual(len(texts), 1)
                t = texts[0]
                self.assertEqual(len(t['params']['body']), 4)
                self.assertTrue(t['params']['body'].isalpha() and t['params']['body'].isupper())
                self.assertAlmostEqual(t['location'][1], y_corner - sy * 0.012, places=3)
                self.assertEqual(t['rotation_deg'], [90, 0, 0 if sy == 1 else 180])
                self.assertTrue(s_lo < t['location'][2] < s_hi)
                lo_x, hi_x = sorted((x_corner + sx * radius, x_corner + sx * along[0]))
                self.assertTrue(lo_x <= t['location'][0] <= hi_x)
                self.assertLessEqual(t['params']['size'] * 0.7 * 4, along[0] - radius + 1e-6)
                self.assertLessEqual(t['params']['size'], s_hi - s_lo)

    def test_canopy_frame_placeholder_text_never_copies_brief_text(self):
        brief = 'XXXX LOUNGE for the DEMO COFFEE stand'
        for chars in (4, 7, 1):
            with self.subTest(chars=chars):
                d = island(canopy(params={'sign': {'corner': 'front_left', 'along_m': [1.5, 1.0], 'drop_m': 0.25, 'text_chars': chars}},
                                  text={'body': 'DEMO COFFEE'}, provenance={'text': 'brief'}))
                d['brief'] = {'text': brief, 'language': 'en'}
                spec, report, _, _ = compile_(d)
                texts = [c['params']['body'] for c in spec['components'] if c['kind'] == 'text' and c.get('group') == 'crown']
                self.assertEqual(len(texts), 1, texts)
                body = texts[0]
                self.assertEqual(len(body), chars)
                self.assertTrue(body.isalpha() and body.isupper())
                if chars > 1:
                    self.assertNotIn(body, brief.upper())
                self.assertNotEqual(body, 'DEMO COFFEE')
                self.assertIn({'id': 'crown', 'what': 'sign_text', 'value': body}, report['choices'])
        # no letters asked for, no text part
        spec, _, _, _ = compile_(island(canopy(params={'sign': {'corner': 'front_left', 'along_m': [1.5, 1.0], 'drop_m': 0.25,
                                                                'text_chars': 0}})))
        self.assertFalse([c for c in spec['components'] if c['kind'] == 'text'])
        self.assertTrue([c for c in spec['components'] if c['role'] == 'canopy_sign'])

    def test_canopy_frame_rejects_bad_params(self):
        def errors(**params):
            return dc.validate_design(island(canopy(params=params)))
        sign = {'corner': 'front_left', 'along_m': [1.2, 0.8], 'drop_m': 0.3, 'text_chars': 4}
        bad = {
            'band: front and back fill the depth': {'band_m': {'front': 1.5, 'back': 1.5}},
            'band: front and back exceed the depth': {'band_m': {'front': 2.0, 'back': 1.2}},
            'band: left and right fill the width': {'band_m': {'left': 2.5, 'right': 2.5}},
            'band: a zero band': {'band_m': {'left': 0}},
            'band: a negative band': {'band_m': {'front': -0.2}},
            'a negative corner radius': {'corner_radius_m': -0.1},
            'a corner radius beyond half the depth': {'corner_radius_m': 1.6},
            'sign along on the first edge longer than the edge': {'sign': dict(sign, along_m=[5.5, 0.8])},
            'sign along on the second edge longer than the edge': {'sign': dict(sign, along_m=[1.2, 3.5])},
            'sign along that stops inside the corner band': {'sign': dict(sign, along_m=[0.1, 0.8])},
            'sign along that reaches the far corner': {'sign': dict(sign, along_m=[4.9, 0.8])},
            'sign a zero along': {'sign': dict(sign, along_m=[0, 0.8])},
            'sign negative text_chars': {'sign': dict(sign, text_chars=-1)},
            'sign fractional text_chars': {'sign': dict(sign, text_chars=2.5)},
            'sign too many text_chars': {'sign': dict(sign, text_chars=500)},
            'sign a zero drop': {'sign': dict(sign, drop_m=0)},
            'sign a negative drop': {'sign': dict(sign, drop_m=-0.3)},
        }
        for label, params in bad.items():
            with self.subTest(label):
                found = errors(**params)
                self.assertTrue(found, label)
                self.assertTrue(all(e['code'] in ('PARAM', 'SCHEMA') for e in found), found)
                if label.startswith('band'):
                    self.assertTrue(any(e['code'] == 'PARAM' and e['where'].startswith('elements[0].params.band_m') for e in found), found)
                if label.startswith('sign') and 'text_chars' not in label:
                    self.assertTrue(any(e['code'] == 'PARAM' and e['where'].startswith('elements[0].params.sign') for e in found), found)
        with self.assertRaises(dc.DesignError):
            compile_(island(canopy(params={'band_m': {'front': 1.5, 'back': 1.5}})))
        with self.assertRaises(dc.DesignError):
            compile_(island(canopy(params={'sign': dict(sign, text_chars=-1)})))
        # a frame wider than the footprint and a corner that is not one of the four are refused too
        self.assertTrue(dc.validate_design(island(canopy(size_m=[9.0, 3.0, 0.3]))))
        self.assertTrue(dc.validate_design(island(canopy(params={'sign': dict(sign, corner='middle')}))))
        # and a good one passes
        self.assertEqual(errors(sign=sign, band_m={'front': 0.3, 'back': 0.5, 'left': 0.4, 'right': 0.2}, corner_radius_m=0.3), [])

    def test_bare_canopy_frame_compiles_with_assumed_defaults(self):
        for label, hanging, footprint in (('hanging allowed', True, (8.0, 6.0)), ('hanging not stated', None, (4.0, 3.0))):
            with self.subTest(label):
                el = {'id': 'canopy_frame', 'kind': 'canopy_frame', 'hero': True, 'side': 'centre', 'centre_m': [0.0, 0.0]}
                d = island(el, hanging=hanging, footprint=footprint, height=3.5)
                spec, report, _, _ = compile_(d)
                self.assertEqual(report['dropped'], [])
                self.assertTrue(report['valid'], report['validation_errors'])
                self.assertIn('canopy_frame', {e['id'] for e in report['elements']})
                parts = pieces(spec, 'canopy_frame')
                self.assertGreaterEqual(len(parts), 2)
                self.assertFalse([c for c in parts if c['role'] == 'canopy_sign'], 'no sign unless one is asked for')
                assumed = {a['what'] for a in report['assumed'] if a['id'] == 'canopy_frame'}
                for what in ('width_m', 'depth_m', 'height_m', 'corner_radius_m', 'band_m.front', 'band_m.back', 'band_m.left', 'band_m.right', 'z_m'):
                    self.assertIn(what, assumed)
                W, D = footprint
                xs = [x for c in parts for x, _ in plan_polygon(c)]
                ys = [y for c in parts for _, y in plan_polygon(c)]
                self.assertAlmostEqual(max(xs) - min(xs), W - 1.0, places=3)      # every side set in by 0.5 m, like the ring
                self.assertAlmostEqual(max(ys) - min(ys), D - 1.0, places=3)
                top = max(z_range(c)[1] for c in parts)
                self.assertAlmostEqual(top, d['form']['height_limit_m'] - 0.1, places=3)
                posts = [c for c in spec['components'] if c['role'] == 'header_post']
                self.assertEqual(len(posts), 0 if hanging else 4)
                if not hanging:
                    self.assertTrue(any(a['what'] == 'posts' for a in report['choices']))
                for c in posts:
                    self.assertEqual(c.get('group'), 'canopy_frame')

    def test_every_kind_covers_canopy_frame(self):
        schema = dc.load_schema()
        enum = schema['$defs']['element']['properties']['kind']['enum']
        self.assertIn('canopy_frame', enum)
        self.assertEqual(set(enum), set(mt.KINDS))
        self.assertIn('canopy_frame', mt.OVERHEAD_KINDS)
        self.assertIn('canopy_frame', mt.OVERHEAD_BUILDERS)
        self.assertIn('canopy_frame', mt.PARAM_CHECKS)
        self.assertEqual(mt.SCOPE['canopy_frame'], 'shell')
        self.assertIn('canopy_frame', mt.ROLE)
        self.assertNotIn('canopy_frame', mt.BUILDERS)
        special = {'wall', 'header_band', 'ring', 'spotlights'}
        for kind in mt.KINDS:
            self.assertIn(kind, mt.SCOPE, kind)
            self.assertTrue(kind in mt.BUILDERS or kind in mt.OVERHEAD_BUILDERS or kind in special, kind)
        params = schema['$defs']['element']['properties']['params']['properties']
        self.assertIn('band_m', params)
        self.assertIn('sign', params)
        self.assertIn('pattern', schema['$defs']['element']['properties']['material']['properties'])
        self.assertIn('floor_pattern', schema['properties']['style']['properties'])
        # a requirement can ask for it
        d = island(canopy())
        d['requirements'] = [{'id': 'r1', 'priority': 'must', 'kind': 'must_have', 'element_kind': 'canopy_frame'}]
        _, report, _, _ = compile_(d)
        self.assertEqual(report['brief']['status'], 'satisfied', report['brief'])


    def test_canopy_posts_stop_at_the_sign_and_the_frame(self):
        bands = {'front': 0.3, 'back': 0.4, 'left': 0.35, 'right': 0.3}
        for corner in ('front_left', 'front_right', 'back_left', 'back_right'):
            with self.subTest(corner=corner):
                sign = {'corner': corner, 'along_m': [1.2, 0.8], 'drop_m': 0.3, 'text_chars': 4}
                spec, report, _, _ = compile_(island(canopy(params={'band_m': bands, 'sign': sign}), hanging=None))
                self.assertEqual(report['dropped'], [])
                signs = [c for c in pieces(spec) if c['role'] == 'canopy_sign']
                rest = [c for c in pieces(spec) if c['role'] == 'canopy_frame']
                sign_poly = plan_polygon(signs[0])
                sign_bottom = z_range(signs[0])[0]
                frame_bottom = min(z_range(c)[0] for c in rest)
                posts = [c for c in spec['components'] if c['role'] == 'header_post']
                self.assertEqual(len(posts), 4)
                under_sign = [c for c in posts if inside(sign_poly, c['location'][0], c['location'][1])]
                self.assertEqual(len(under_sign), 1, 'exactly one post stands under the sign')
                for c in posts:
                    top = c['location'][2] + c['params']['size'][2] / 2
                    bottom = c['location'][2] - c['params']['size'][2] / 2
                    self.assertAlmostEqual(bottom, 0.1, places=3)
                    if c in under_sign:
                        self.assertAlmostEqual(top, sign_bottom, places=3)       # stops at the sign's bottom, no overlap with it
                        self.assertLess(top, frame_bottom)
                    else:
                        self.assertAlmostEqual(top, frame_bottom, places=3)

    def test_canopy_text_size_is_honoured_and_clamped_with_a_warning(self):
        sign = {'corner': 'front_left', 'along_m': [1.5, 1.0], 'drop_m': 0.3, 'text_chars': 4}

        def built(**text):
            spec, report, _, _ = compile_(island(canopy(params={'sign': sign}, text=text or None)))
            texts = [c for c in spec['components'] if c['kind'] == 'text' and c.get('group') == 'crown']
            self.assertEqual(len(texts), 1)
            return texts[0]['params']['size'], report
        auto, report = built()
        self.assertFalse([w for w in report['warnings'] if 'crown' in w])
        small, report = built(size_m=0.1)
        self.assertEqual(small, 0.1)
        self.assertFalse([w for w in report['warnings'] if 'crown' in w])
        huge, report = built(size_m=5.0)
        self.assertEqual(huge, auto, 'a size that does not fit is clamped to what fits the sign face')
        self.assertTrue([w for w in report['warnings'] if 'crown' in w and 'size' in w], report['warnings'])
        same, _ = built(size_m=auto)
        self.assertEqual(same, auto)

    def test_canopy_placeholder_text_provenance_says_choice_only(self):
        sign = {'corner': 'front_left', 'along_m': [1.5, 1.0], 'drop_m': 0.3, 'text_chars': 4}
        for hanging in (True, None):          # with hanging not stated the four posts are canopy parts too
            d = island(canopy(params={'sign': sign}, text={'body': 'DEMO COFFEE'}, provenance={'text': 'brief'}), hanging=hanging)
            spec, _, _, _ = compile_(d)
            parts = [c for c in spec['components'] if c.get('group') == 'crown']
            self.assertEqual(len([c for c in parts if c['role'] == 'header_post']), 0 if hanging else 4)
            text = next(c for c in parts if c['kind'] == 'text')
            note = text['evidence']['note']
            self.assertIn('choice', note)
            self.assertNotIn('text brief', note)
            for c in parts:
                self.assertNotIn('text brief', c['evidence']['note'], (hanging, c['id']))
        # the scope line about text stays true: placeholders are marked as choices
        decisions = {s['id']: s for s in spec['scope']['decisions']}
        self.assertIn('placeholder', decisions['brand_text']['evidence'].lower())
        self.assertIn('choice', decisions['brand_text']['evidence'].lower())
        # and it is the old line when no placeholder text was built
        spec, _, _, _ = compile_(island(canopy()))
        decisions = {s['id']: s for s in spec['scope']['decisions']}
        self.assertEqual(decisions['brand_text']['evidence'],
                         'No text or logo is copied from the reference picture; only text written in the brief is built.')

    def test_canopy_records_only_the_missing_size_values_as_assumed(self):
        _, report, _, _ = compile_(island(canopy(size_m=[5.0, 3.0, None])))
        whats = {a['what']: a['value'] for a in report['assumed'] if a['id'] == 'crown'}
        self.assertEqual(whats['height_m'], 0.3)
        self.assertNotIn('size_m', whats)
        self.assertNotIn('width_m', whats)
        self.assertNotIn('depth_m', whats)
        _, report, _, _ = compile_(island(canopy(size_m=[None, 3.0, 0.4])))
        whats = {a['what']: a['value'] for a in report['assumed'] if a['id'] == 'crown'}
        self.assertEqual(whats['width_m'], 7.0)
        self.assertNotIn('height_m', whats)
        self.assertNotIn('depth_m', whats)

    def test_empty_pattern_object_is_copied_and_rejected(self):
        for where in ('element', 'floor'):
            with self.subTest(where=where):
                d = island({'id': 'plain', 'kind': 'box', 'side': 'centre', 'centre_m': [1.0, 1.0],
                            'material': {'pattern': {} if where == 'element' else None}})
                if where == 'floor':
                    d['style'] = dict(d['style'], floor_pattern={})
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / 'design.json'
                    path.write_text(json.dumps(d), encoding='utf-8')
                    code, summary = dc.run(path, Path(tmp) / 'out', '320x200')
                    self.assertEqual(code, 2, summary)
                    self.assertTrue(any('PATTERN' in e['code'] for e in summary['validation_errors']), summary['validation_errors'])
        # an empty pattern on a perimeter wall element is copied too
        d = design()
        d['elements'] = [dict(e) for e in d['elements']] + [{'id': 'sidewall', 'kind': 'wall', 'side': 'left', 'material': {'pattern': {}}}]
        spec, report, _, _ = compile_(d)
        self.assertFalse(report['valid'])
        self.assertEqual(next(c for c in spec['components'] if c['id'] == 'perimeter_wall_left')['material'].get('pattern'), {})


    def check_posts_do_not_enter_pieces(self, spec, label):
        """Every post ends at or below the bottom of each canopy piece that stands above any part of its footprint."""
        parts = pieces(spec)
        for post in (c for c in spec['components'] if c['role'] == 'header_post'):
            sx, sy, sz = post['params']['size']
            x, y, z = post['location']
            top = z + sz / 2
            for i in range(5):
                for j in range(5):
                    px, py = x - sx / 2 + sx * (i + 0.5) / 5, y - sy / 2 + sy * (j + 0.5) / 5
                    for c in parts:
                        if inside(plan_footprint(c), px, py):
                            self.assertLessEqual(top, piece_lowest(c) + 1e-6, (label, post['id'], c['id'], px, py))

    def test_canopy_sign_cuts_stay_on_the_straight_parts_for_a_grid_of_inputs(self):
        accepted, refused, compiled = [], 0, 0
        along_values = (0.3, 0.9, 1.5, 2.7, 4.7)
        band_sets = ({'front': 0.15, 'back': 0.15, 'left': 0.15, 'right': 0.15},
                     {'front': 0.4, 'back': 0.4, 'left': 0.4, 'right': 0.4},
                     {'front': 0.2, 'back': 0.5, 'left': 0.3, 'right': 0.6})
        for ow, od in ((5.0, 3.0), (3.0, 3.0)):
            for bands in band_sets:
                for radius in (0.0, 0.3, 1.0, 1.4):
                    for corner in ('front_left', 'front_right', 'back_left', 'back_right'):
                        for a1 in along_values:
                            for a2 in along_values:
                                el = canopy(size_m=[ow, od, 0.3], params={'corner_radius_m': radius, 'band_m': bands,
                                                                          'sign': {'corner': corner, 'along_m': [a1, a2], 'drop_m': 0.3,
                                                                                   'text_chars': 3}})
                                errors = dc.validate_design(island(el, hanging=None))
                                if errors:
                                    refused += 1
                                    self.assertTrue(all(e['code'] == 'PARAM' for e in errors), errors)
                                    continue
                                accepted.append((el, ow, od, bands, radius, corner, a1, a2))
        self.assertGreater(refused, 300)
        self.assertGreater(len(accepted), 100)
        rng = random.Random(3)
        for n, (el, ow, od, bands, radius, corner, a1, a2) in enumerate(accepted):
            label = (ow, od, bands, radius, corner, a1, a2)
            plan, _, problems = mt._canopy_plan(el, (-4.0, 4.0, -3.0, 3.0))
            self.assertEqual(problems, [], label)
            frame_polys, sign_poly = mt._canopy_geometry(plan)
            outer, ro, inner, ri = ring_shape(ow, od, bands, radius)
            sx = 1 if corner.endswith('left') else -1
            sy = 1 if corner.startswith('front') else -1
            for _ in range(120):
                x, y = rng.uniform(outer[0] - 0.1, outer[1] + 0.1), rng.uniform(outer[2] - 0.1, outer[3] + 0.1)
                clear_in = in_rrect(x, y, outer, ro, -0.01) and not in_rrect(x, y, inner, ri, 0.01)
                clear_out = not in_rrect(x, y, outer, ro, 0.01) or in_rrect(x, y, inner, ri, -0.01)
                in_sign = inside(sign_poly, x, y)
                count = in_sign + sum(inside(p, x, y) for p in frame_polys)
                if clear_in:
                    self.assertEqual(count, 1, (label, x, y))
                    u = (x - outer[0]) if sx == 1 else (outer[1] - x)
                    v = (y - outer[2]) if sy == 1 else (outer[3] - y)
                    if abs(u - a1) > 0.02 and abs(v - a2) > 0.02:
                        # the sign is the ring between the two cuts, round the corner; it is never most of the ring
                        self.assertEqual(in_sign, u <= a1 and v <= a2, (label, x, y))
                elif clear_out:
                    self.assertEqual(count, 0, (label, x, y))
            if n % 10 == 0:        # the full compile on a sample: nothing is dropped, nothing is left unbuilt, posts stay clear
                spec, report, _, _ = compile_(island(el, hanging=None))
                compiled += 1
                self.assertEqual(report['dropped'], [], label)
                self.assertTrue(report['valid'], label)
                self.assertEqual(len([c for c in spec['components'] if c['role'] == 'canopy_sign']), 1, label)
                self.assertEqual(len([c for c in spec['components'] if c['role'] == 'header_post']), 4, label)
                self.check_posts_do_not_enter_pieces(spec, label)
        self.assertGreater(compiled, 10)

    def test_canopy_sign_cuts_on_a_corner_arc_are_refused(self):
        arc_cases = (
            {'corner_radius_m': 2.0, 'sign': {'corner': 'front_right', 'along_m': [0.55, 0.55]}},
            {'corner_radius_m': 2.0, 'sign': {'corner': 'front_right', 'along_m': [0.8, 0.8]}},
            {'corner_radius_m': 1.0, 'band_m': {'front': 0.4, 'back': 0.4, 'left': 0.4, 'right': 0.4},
             'sign': {'corner': 'front_left', 'along_m': [0.5, 0.5]}},
        )
        for params in arc_cases:
            with self.subTest(params=params):
                found = dc.validate_design(island(canopy(size_m=None, params=params), hanging=True))
                self.assertTrue(found)
                self.assertTrue(any(e['code'] == 'PARAM' and e['where'].startswith('elements[0].params.sign.along_m') for e in found), found)
                self.assertTrue(any('straight' in e['message'] for e in found), found)
                with self.assertRaises(dc.DesignError):
                    compile_(island(canopy(size_m=None, params=params), hanging=True))
        # the same sign with a cut on the straight part is built, and it is a small piece of the ring
        spec, report, _, _ = compile_(island(canopy(size_m=None, params={'corner_radius_m': 2.0, 'sign': {'corner': 'front_right',
                                                                                                          'along_m': [3.0, 2.5]}}),
                                              hanging=True))
        self.assertEqual(report['dropped'], [])
        sign = next(c for c in pieces(spec) if c['role'] == 'canopy_sign')
        ring = sum(mt._area(plan_polygon(c)) for c in pieces(spec))
        self.assertLess(mt._area(plan_polygon(sign)), 0.6 * ring)

    def test_canopy_posts_never_stand_inside_a_piece_above_them(self):
        # thin bands, a small radius and a cut near the end of the edge (the far corner post is close to the cut)
        for along in ([4.84, 0.8], [4.7, 0.8], [4.6, 0.8], [0.4, 2.4], [0.4, 2.55]):
            for corner in ('front_left', 'front_right', 'back_left', 'back_right'):
                with self.subTest(along=along, corner=corner):
                    params = {'corner_radius_m': 0.3, 'band_m': {'front': 0.15, 'back': 0.15, 'left': 0.15, 'right': 0.15},
                              'sign': {'corner': corner, 'along_m': along, 'drop_m': 0.3, 'text_chars': 2}}
                    d = island(canopy(params=params), hanging=None)
                    if dc.validate_design(d):
                        continue                      # refused: nothing to build
                    spec, report, _, _ = compile_(d)
                    self.assertEqual(report['dropped'], [])
                    self.check_posts_do_not_enter_pieces(spec, (along, corner))


    def test_canopy_that_cannot_fit_vertically_is_refused_by_validation(self):
        sign = {'drop_m': 2.0}
        cases = (
            ('drop too deep for the default height', dict(height=2.5), canopy(size_m=None, params={'sign': sign}),
             'params.sign.drop_m', ['2.000']),
            ('z_m puts the top over the limit', dict(height=4.5), canopy(z_m=4.3), 'z_m', ['0.300', '4.100']),
            ('negative z_m', dict(height=4.5), canopy(z_m=-1), 'z_m', ['0.300', '4.100']),
            ('frame taller than the limit', dict(height=2.0), canopy(size_m=[5.0, 3.0, 3.0]), 'size_m', ['1.500']),
            ('z_m and drop leave no room', dict(height=3.0), canopy(z_m=2.0, params={'sign': {'drop_m': 1.9}}), 'z_m', ['2.200', '2.600']),
        )
        for label, kw, el, path, numbers in cases:
            with self.subTest(label):
                d = island(el, hanging=True, **kw)
                found = dc.validate_design(d)
                self.assertTrue(found, label)
                self.assertTrue(all(e['code'] == 'PARAM' for e in found), found)
                hit = [e for e in found if e['where'] == f'elements[0].{path}']
                self.assertTrue(hit, found)
                for n in numbers:
                    self.assertIn(n, hit[0]['message'])
                with self.assertRaises(dc.DesignError):
                    compile_(d)

    def test_canopy_vertical_validation_agrees_with_the_build(self):
        accepted = 0
        for height in (2.0, 2.5, 3.5, 4.5):
            for z_m in (None, -1, 0.3, 1.0, 2.0, 2.3, 3.0, 4.1, 4.3):
                for h in (0.3, 1.0, 3.0):
                    for drop in (None, 0.5, 2.0):
                        params = {} if drop is None else {'sign': {'drop_m': drop, 'text_chars': 0}}
                        el = canopy(size_m=[5.0, 3.0, h], params=params)
                        if z_m is not None:
                            el['z_m'] = z_m
                        d = island(el, hanging=True, height=height)
                        if dc.validate_design(d):
                            continue
                        accepted += 1
                        _, report, _, _ = compile_(d)
                        self.assertEqual(report['dropped'], [], (height, z_m, h, drop))
        self.assertGreater(accepted, 20)
        # the very edge is accepted and built: top exactly under the limit, bottom exactly 0.3 m over the floor
        d = island(canopy(z_m=4.1), hanging=True, height=4.5)
        self.assertEqual(dc.validate_design(d), [])
        self.assertEqual(compile_(d)[1]['dropped'], [])
        d = island(canopy(z_m=0.3), hanging=True, height=4.5)
        self.assertEqual(dc.validate_design(d), [])
        self.assertEqual(compile_(d)[1]['dropped'], [])

    def test_default_along_clamped_to_the_limit_is_never_refused_by_rounding(self):
        for ow, od in ((4.1, 4.2), (4.3, 4.7), (4.15, 4.25), (4.07, 4.33)):
            with self.subTest(size=(ow, od)):
                d = island(canopy(size_m=[ow, od, 0.3], params={'corner_radius_m': 2.0, 'sign': {}}), hanging=True)
                self.assertEqual(dc.validate_design(d), [])
                spec, report, _, _ = compile_(d)
                self.assertEqual(report['dropped'], [])
                self.assertEqual(len([c for c in spec['components'] if c['role'] == 'canopy_sign']), 1)
                along = next(a['value'] for a in report['assumed'] if a['id'] == 'crown' and a['what'] == 'sign.along_m')
                self.assertEqual(len(along), 2)

    def test_canopy_sign_lower_bound_is_set_by_the_outer_radius_when_it_is_larger(self):
        # band beside the first edge larger than the band at the corner's side: band + inner radius = 0.7 m, outer radius 1.0 m
        a_bands = {'front': 0.5, 'back': 0.5, 'left': 0.2, 'right': 0.2}
        # the other way round for the second edge
        b_bands = {'front': 0.2, 'back': 0.2, 'left': 0.5, 'right': 0.5}

        def refused(bands, along):
            params = {'corner_radius_m': 1.0, 'band_m': bands, 'sign': {'corner': 'front_left', 'along_m': along, 'text_chars': 0}}
            found = dc.validate_design(island(canopy(params=params), hanging=True))
            return bool(found)
        self.assertTrue(refused(a_bands, [0.8, 1.2]), 'first edge: 0.8 m is on the outer corner arc (radius 1.0)')
        self.assertFalse(refused(a_bands, [1.1, 1.2]))
        self.assertTrue(refused(b_bands, [1.2, 0.8]), 'second edge: 0.8 m is on the outer corner arc (radius 1.0)')
        self.assertFalse(refused(b_bands, [1.2, 1.1]))
        # and the same at the far end: the cut must stay 1.0 m (the outer radius) short of the far corner on a 5 x 3 frame
        self.assertTrue(refused(a_bands, [4.2, 1.2]))
        self.assertFalse(refused(a_bands, [3.9, 1.2]))
        self.assertTrue(refused(b_bands, [1.2, 2.2]))
        self.assertFalse(refused(b_bands, [1.2, 1.9]))
        # what is accepted is built
        params = {'corner_radius_m': 1.0, 'band_m': a_bands, 'sign': {'corner': 'front_left', 'along_m': [1.1, 1.2], 'text_chars': 0}}
        _, report, _, _ = compile_(island(canopy(params=params), hanging=True))
        self.assertEqual(report['dropped'], [])

    def test_canopy_warns_about_values_it_ignores(self):
        def warnings(el, **kw):
            _, report, _, _ = compile_(island(el, **kw))
            self.assertEqual(report['dropped'], [])
            return [w for w in report['warnings'] if w.startswith('crown')]
        self.assertEqual(warnings(canopy()), [])
        self.assertEqual(warnings(canopy(side='centre')), [], 'centre means no side: the catalogue writes it')
        for key, value in (('side', 'front'), ('along', 0.3), ('yaw_deg', 90)):
            with self.subTest(key=key):
                found = warnings(canopy(**{key: value}))
                self.assertEqual(len(found), 1, found)
                self.assertIn(key, found[0])
                self.assertIn('centre_m', found[0])
        # text.size_m without a sign has nothing to size
        found = warnings(canopy(text={'size_m': 0.2}))
        self.assertTrue(any('text.size_m' in w for w in found), found)
        self.assertEqual(warnings(canopy(params={'sign': {'corner': 'front_left', 'along_m': [1.5, 1.0], 'text_chars': 4}},
                                         text={'size_m': 0.1})), [])
        # thin bands make the posts smaller than the usual 0.15 m: say the size
        thin = {'front': 0.1, 'back': 0.1, 'left': 0.1, 'right': 0.1}
        found = warnings(canopy(params={'corner_radius_m': 0.3, 'band_m': thin}), hanging=None)
        self.assertEqual(len(found), 1, found)
        self.assertIn('post', found[0])
        self.assertIn('0.06', found[0])
        self.assertIn('0.15', found[0])
        self.assertEqual(warnings(canopy(params={'corner_radius_m': 0.3, 'band_m': thin}), hanging=True), [])
        self.assertEqual(warnings(canopy(), hanging=None), [])


    # ---- sign.end_radius_m: the deeper sign ends in a vertical fillet instead of a square step
    def sign_params(self, corner='front_left', along=(1.6, 1.0), drop=0.3, end=None, **extra):
        sign = {'corner': corner, 'along_m': list(along), 'drop_m': drop, 'text_chars': 4}
        if end is not None:
            sign['end_radius_m'] = end
        sign.update(extra)
        return sign

    def footprints(self, spec):
        """-> (frame polygons, sign polygons in plan); a sign end piece (profile in elevation) is its plan rectangle."""
        frame, sign = [], []
        for c in pieces(spec):
            if c['rotation_deg'] == [-90, 0, 0]:
                poly = plan_polygon(c)
            elif c['rotation_deg'] == [0, 0, 0]:
                xs = [c['location'][0] + x for x, _ in c['params']['profile']]
                ys = (c['location'][1] - c['params']['depth'] / 2, c['location'][1] + c['params']['depth'] / 2)
                poly = [(min(xs), ys[0]), (max(xs), ys[0]), (max(xs), ys[1]), (min(xs), ys[1])]
            elif c['rotation_deg'] == [0, 0, 90]:
                ys = [c['location'][1] + x for x, _ in c['params']['profile']]
                xs = (c['location'][0] - c['params']['depth'] / 2, c['location'][0] + c['params']['depth'] / 2)
                poly = [(xs[0], min(ys)), (xs[1], min(ys)), (xs[1], max(ys)), (xs[0], max(ys))]
            else:
                self.fail(f"unexpected rotation {c['rotation_deg']} on {c['id']}")
            (sign if c['role'] == 'canopy_sign' else frame).append(poly)
        return frame, sign

    def bottom_at(self, spec, x, y):
        """Lowest height of the canopy at plan point (x, y)."""
        lows = []
        for c in pieces(spec):
            lx, ly, lz = c['location']
            if c['rotation_deg'] == [-90, 0, 0]:
                if inside(plan_polygon(c), x, y):
                    lows.append(z_range(c)[0])
                continue
            if c['rotation_deg'] == [0, 0, 0]:
                u, across, along_depth = x - lx, y - ly, ly
            else:
                u, across, along_depth = y - ly, x - lx, lx
            if abs(across) > c['params']['depth'] / 2:
                continue
            prof = c['params']['profile']
            zs = []
            for i in range(len(prof)):
                (u0, z0), (u1, z1) = prof[i], prof[(i + 1) % len(prof)]
                if (u0 - u) * (u1 - u) <= 0 and u0 != u1:
                    zs.append(z0 + (u - u0) * (z1 - z0) / (u1 - u0))
            if zs:
                lows.append(lz + min(zs))
        return min(lows) if lows else None

    def test_end_radius_zero_or_absent_gives_the_same_bytes(self):
        base = canopy(params={'sign': self.sign_params()})
        raws = []
        for end in (None, 0, 0.0):
            el = copy.deepcopy(base)
            if end is not None:
                el['params']['sign']['end_radius_m'] = end
            raws.append(compile_(island(el, hanging=None))[3])
        self.assertEqual(raws[0], raws[1])
        self.assertEqual(raws[0], raws[2])
        el = copy.deepcopy(base)
        el['params']['sign']['end_radius_m'] = None
        self.assertEqual(compile_(island(el, hanging=None))[3], raws[0])
        spec = json.loads(raws[0])
        self.assertEqual(len([c for c in spec['components'] if c['role'] == 'canopy_sign']), 1)

    def test_end_radius_makes_a_quarter_round_at_each_free_end(self):
        bands = {'front': 0.3, 'back': 0.4, 'left': 0.35, 'right': 0.3}
        radius, along = 0.3, (1.6, 1.0)
        outer = (-2.5, 2.5, -1.5, 1.5)
        for corner in ('front_left', 'front_right', 'back_left', 'back_right'):
            for end in (0.12, 0.3):
                with self.subTest(corner=corner, end=end):
                    sign = self.sign_params(corner, along, 0.3, end)
                    spec, report, _, _ = compile_(island(canopy(params={'corner_radius_m': radius, 'band_m': bands, 'sign': sign}),
                                                         hanging=None))
                    self.assertEqual(report['dropped'], [])
                    first, second = corner.split('_')
                    sx = 1 if second == 'left' else -1
                    sy = 1 if first == 'front' else -1
                    x_corner, y_corner = (outer[0] if sx == 1 else outer[1]), (outer[2] if sy == 1 else outer[3])
                    frame_bottom = 4.1
                    zs = frame_bottom - 0.3
                    ends = (('first', lambda s: (x_corner + sx * along[0] - sx * s, y_corner + sy * bands[first] / 2)),
                            ('second', lambda s: (x_corner + sx * bands[second] / 2, y_corner + sy * along[1] - sy * s)))
                    for name, at in ends:
                        previous = None
                        for k in range(0, 61):
                            s = 0.002 + k * (end + 0.15) / 60
                            x, y = at(s)
                            z = self.bottom_at(spec, x, y)
                            self.assertIsNotNone(z, (name, s))
                            if previous is not None:
                                self.assertLessEqual(z, previous + 1e-6, f'{name} end: the bottom only goes down, into the sign')
                            previous = z
                            if s < end:
                                # on the quarter circle that starts at the end face and is level at the sign bottom
                                self.assertAlmostEqual(math.hypot(s - end, z - (zs + end)), end, delta=0.003, msg=(name, s, z))
                            else:
                                self.assertAlmostEqual(z, zs, places=4, msg=(name, s))
                        # next to the cut the bottom is where the end face meets the arc: the frame bottom for a full-drop radius
                        z0 = self.bottom_at(spec, *at(0.00001))        # the arc is vertical where it meets the end face
                        self.assertAlmostEqual(z0, zs + end, delta=0.01)
                        if end == 0.3:
                            self.assertAlmostEqual(z0, frame_bottom, delta=0.01)
                    # the frame itself is untouched: its bottom stays level just past each end
                    for name, at in ends:
                        x, y = at(-0.05)
                        self.assertAlmostEqual(self.bottom_at(spec, x, y), frame_bottom, places=4)

    def test_end_radius_pieces_tile_the_ring_and_keep_one_sign(self):
        bands = {'front': 0.3, 'back': 0.4, 'left': 0.35, 'right': 0.3}
        radius, along, drop = 0.3, (1.6, 1.0), 0.3
        outer, ro, inner, ri = ring_shape(5.0, 3.0, bands, radius)
        rng = random.Random(5)
        for corner in ('front_left', 'front_right', 'back_left', 'back_right'):
            for end in (0.12, 0.3):
                with self.subTest(corner=corner, end=end):
                    sign = self.sign_params(corner, along, drop, end)
                    spec, report, _, _ = compile_(island(canopy(params={'corner_radius_m': radius, 'band_m': bands, 'sign': sign}),
                                                         hanging=None))
                    self.assertEqual(report['dropped'], [])
                    self.assertFalse([w for w in report['warnings'] if 'COPLANAR' in w], report['warnings'])
                    self.assertEqual(report['coplanar_unresolved'], [])
                    frame_polys, sign_polys = self.footprints(spec)
                    self.assertEqual(len(sign_polys), 3, 'the sign is the middle piece and one end piece at each free end')
                    sx = 1 if corner.endswith('left') else -1
                    sy = 1 if corner.startswith('front') else -1
                    for _ in range(1500):
                        x, y = rng.uniform(-2.7, 2.7), rng.uniform(-1.7, 1.7)
                        if not (in_rrect(x, y, outer, ro, -0.01) and not in_rrect(x, y, inner, ri, 0.01)):
                            continue
                        in_frame = sum(inside(p, x, y) for p in frame_polys)
                        in_sign = sum(inside(p, x, y) for p in sign_polys)
                        self.assertEqual(in_frame + in_sign, 1, (corner, end, x, y))
                        u = (x - outer[0]) if sx == 1 else (outer[1] - x)
                        v = (y - outer[2]) if sy == 1 else (outer[3] - y)
                        if abs(u - along[0]) > 0.02 and abs(v - along[1]) > 0.02:
                            self.assertEqual(in_sign == 1, u <= along[0] and v <= along[1], (corner, end, x, y))
                    # one element, nothing floating: the posts and every piece touch (design check finds no support problem)
                    sign_parts = [c for c in spec['components'] if c['role'] == 'canopy_sign']
                    self.assertEqual(len(sign_parts), 3)
                    self.assertTrue(all(c.get('group') == 'crown' for c in sign_parts))
                    result = design_check_run(spec, {'height_limit_m': 4.5, 'hanging_allowed': False})
                    self.assertEqual([f for f in result['findings'] if f['check'] == 'support_path'], [])

    def test_end_radius_text_stays_off_the_fillet(self):
        bands = {'front': 0.3, 'back': 0.4, 'left': 0.35, 'right': 0.3}
        radius, drop = 0.3, 0.3
        outer = (-2.5, 2.5, -1.5, 1.5)
        for corner in ('front_left', 'front_right', 'back_left', 'back_right'):
            for end in (0.0, 0.2, 0.3):
                with self.subTest(corner=corner, end=end):
                    along = (1.3, 1.0)
                    sign = self.sign_params(corner, along, drop, end or None, text_chars=3)
                    spec, report, _, _ = compile_(island(canopy(params={'corner_radius_m': radius, 'band_m': bands, 'sign': sign}),
                                                         hanging=True))
                    self.assertEqual(report['dropped'], [])
                    texts = [c for c in spec['components'] if c['kind'] == 'text']
                    self.assertEqual(len(texts), 1)
                    t = texts[0]
                    sx = 1 if corner.endswith('left') else -1
                    x_corner = outer[0] if sx == 1 else outer[1]
                    half = t['params']['size'] * 0.7 * 3 / 2
                    near = x_corner + sx * radius                         # where the corner arc ends
                    far = x_corner + sx * (along[0] - end)                # where the fillet starts
                    lo, hi = sorted((near, far))
                    self.assertGreaterEqual(t['location'][0] - half, lo - 1e-6)
                    self.assertLessEqual(t['location'][0] + half, hi + 1e-6)

    def test_end_radius_bad_values_are_refused(self):
        def errors(**sign):
            return dc.validate_design(island(canopy(params={'sign': self.sign_params(**sign)}), hanging=True))
        for label, end in (('negative', -0.1), ('larger than the drop', 0.31), ('far larger', 2.0)):
            with self.subTest(label):
                found = errors(end=end)
                self.assertTrue(any(e['code'] == 'PARAM' and e['where'] == 'elements[0].params.sign.end_radius_m' for e in found), found)
        self.assertEqual(errors(end=0.3), [])
        self.assertEqual(errors(end=0.05), [])
        self.assertEqual(errors(end=0), [])
        # a fillet needs its own length of straight edge before the cut: the lower along_m bound grows by the radius
        base = canopy(size_m=[5.0, 3.0, 0.3], params={'corner_radius_m': 0.3, 'band_m': {'front': 0.3, 'back': 0.3, 'left': 0.3, 'right': 0.3}})

        def refused(a1, end):
            el = copy.deepcopy(base)
            el['params']['sign'] = self.sign_params('front_left', (a1, 1.0), 0.3, end)
            return bool(dc.validate_design(island(el, hanging=True)))
        self.assertFalse(refused(0.45, 0.0))
        self.assertTrue(refused(0.45, 0.2))
        self.assertFalse(refused(0.65, 0.2))
        with self.assertRaises(dc.DesignError):
            compile_(island(canopy(params={'sign': self.sign_params(end=0.5)}), hanging=True))

    def test_end_radius_validation_passes_implies_the_compile_builds_it(self):
        along_values = (0.5, 0.9, 1.5, 2.7, 4.5)
        band_sets = ({'front': 0.15, 'back': 0.15, 'left': 0.15, 'right': 0.15},
                     {'front': 0.2, 'back': 0.5, 'left': 0.3, 'right': 0.6})
        accepted = []
        refused = 0
        for bands in band_sets:
            for radius in (0.3, 1.0):
                for corner in ('front_left', 'front_right', 'back_left', 'back_right'):
                    for a1 in along_values:
                        for a2 in along_values:
                            for end in (0.1, 0.3):
                                el = canopy(params={'corner_radius_m': radius, 'band_m': bands,
                                                    'sign': self.sign_params(corner, (a1, a2), 0.3, end, text_chars=0)})
                                if dc.validate_design(island(el, hanging=None)):
                                    refused += 1
                                else:
                                    accepted.append((el, bands, radius, corner, a1, a2, end))
        self.assertGreater(refused, 100)
        self.assertGreater(len(accepted), 60)
        compiled = 0
        for n, (el, bands, radius, corner, a1, a2, end) in enumerate(accepted):
            if n % 4:
                continue
            label = (bands, radius, corner, a1, a2, end)
            spec, report, _, _ = compile_(island(el, hanging=None))
            compiled += 1
            self.assertEqual(report['dropped'], [], label)
            self.assertTrue(report['valid'], label)
            self.assertEqual(len([c for c in spec['components'] if c['role'] == 'canopy_sign']), 3, label)
            outer, ro, inner, ri = ring_shape(5.0, 3.0, bands, radius)
            frame_polys, sign_polys = self.footprints(spec)
            rng = random.Random(n)
            for _ in range(150):
                x, y = rng.uniform(-2.7, 2.7), rng.uniform(-1.7, 1.7)
                clear_in = in_rrect(x, y, outer, ro, -0.01) and not in_rrect(x, y, inner, ri, 0.01)
                clear_out = not in_rrect(x, y, outer, ro, 0.01) or in_rrect(x, y, inner, ri, -0.01)
                count = sum(inside(p, x, y) for p in frame_polys + sign_polys)
                if clear_in:
                    self.assertEqual(count, 1, (label, x, y))
                elif clear_out:
                    self.assertEqual(count, 0, (label, x, y))
            self.check_posts_do_not_enter_pieces(spec, label)
        self.assertGreater(compiled, 12)


    def test_default_along_is_never_refused_for_a_small_frame(self):
        # the default along_m is clamped to the limit and rounded to 4 decimals: the rounded value must lie inside the range
        # (a floor() of a value like 0.45399999999999996 * 1e4 came out above the limit)
        sizes = ((2.0, 1.7), (2.0, 0.6), (4.67, 0.69), (6.6, 0.7301), (2.3, 1.1), (3.7, 2.5), (5.1, 0.93), (2.0, 4.2), (4.1, 4.2))
        radii = (0.0, 0.235, 0.2918, 0.8)
        checked = 0
        for ow, od in sizes:
            for radius in radii:
                for corner in ('front_left', 'front_right', 'back_left', 'back_right'):
                    el = canopy(size_m=[ow, od, 0.3], params={'corner_radius_m': radius, 'sign': {'corner': corner}})
                    found = dc.validate_design(island(el, hanging=True))
                    for e in found:
                        if e['where'].endswith('sign.along_m'):
                            m = re.search(r'must stay between ([0-9.]+) and ([0-9.]+) m', e['message'])
                            self.assertIsNotNone(m, e)
                            # only a frame that really has no room may refuse its default: then the range is empty
                            self.assertGreater(float(m.group(1)), float(m.group(2)), ((ow, od, radius, corner), e['message']))
                    if not found:
                        checked += 1
        self.assertGreater(checked, 80)
        for ow, od, radius, corner in ((2.0, 1.7, 0.8, 'front_left'), (2.0, 0.6, 0.0, 'front_left'), (4.67, 0.69, 0.235, 'front_left'),
                                       (6.6, 0.7301, 0.2918, 'back_left')):
            el = canopy(size_m=[ow, od, 0.3], params={'corner_radius_m': radius, 'sign': {'corner': corner}})
            self.assertEqual(dc.validate_design(island(el, hanging=True)), [], (ow, od, radius, corner))
            self.assertEqual(compile_(island(el, hanging=True))[1]['dropped'], [])
        for v, lo, hi in ((0.45399999999999996, 0.151, 0.45399999999999996), (0.8989999999999999, 0.801, 0.8989999999999999),
                          (1.23456, 1.2, 1.2345), (1.2, 1.2001, 1.3), (0.30004, 0.3, 0.30004)):
            r = mt._round_into(v, lo, hi)
            self.assertTrue(lo - 1e-6 <= r <= hi + 1e-6, (v, lo, hi, r))       # within the 1e-6 tolerance the limits are compared with

    def test_validation_and_build_agree_on_the_floor_height_of_a_fit(self):
        def design_with(platform, z_m, limit=4.4):
            d = island(canopy(z_m=z_m), hanging=True, height=limit)
            d['layout'] = dict(d['layout'], platform_height_m=platform)
            return d

        def run_fit(d, floor_z):
            with tempfile.TemporaryDirectory() as tmp:
                fit_path, image_path, fit = fit_file(tmp, floor_z=floor_z)
                try:
                    _, report = dc.compile_design(d, Path(tmp) / 'out', fit=json.loads(fit_path.read_text(encoding='utf-8')),
                                                  image_path=image_path, fit_path=fit_path)
                except dc.DesignError as ex:
                    return None, ex
                return report, None
        # the design says 0.0, the fit says 0.1: z_m 4.08 puts the top at 4.48 m, over the 4.4 m limit
        report, error = run_fit(design_with(0.0, 4.08), 0.1)
        self.assertIsNone(report)
        message = ' '.join(e['message'] for e in error.errors)
        self.assertIn('4.000', message, 'the range is the one the build uses: 4.4 - 0.1 - 0.3')
        # the design says 0.1, the fit says 0.0: z_m 4.05 fits (top 4.35 m); the stricter design platform must not refuse it
        report, error = run_fit(design_with(0.1, 4.05), 0.0)
        self.assertIsNone(error)
        self.assertEqual(report['dropped'], [])
        # the sweep: whatever validation lets through is built, and a refusal states the range of the fit's floor height
        for platform in (0.0, 0.1, 0.3):
            for floor_z in (0.0, 0.1, 0.3):
                for z_m in (3.9, 4.0, 4.05, 4.08, 4.2):
                    report, error = run_fit(design_with(platform, z_m), floor_z)
                    if error is None:
                        self.assertEqual(report['dropped'], [], (platform, floor_z, z_m))
                    else:
                        hi = 4.4 - floor_z - 0.3
                        self.assertIn(f'{hi:.3f}', ' '.join(e['message'] for e in error.errors), (platform, floor_z, z_m))
                        self.assertGreater(z_m, hi - 1e-9, (platform, floor_z, z_m))


    def test_floor_and_wall_builders_can_record_assumptions_through_the_frame(self):
        seen = {}

        def floor_builder(b, p, scope, note, frame):
            seen['floor'] = frame
            frame.assume(p.id, 'label', 'other')
            frame.adjust(p.id, 'floor builder adjusted')
            frame.warn('floor builder warned')
            b.box(p.id, scope, 'box', p.xr, p.yr, (p.z0, p.z1), p.mat['color'], inferred=True, note=note)

        def wall_builder(b, p, scope, note, frame):
            seen['wall'] = frame
            frame.assume(p.id, 'inset_m', 0.05)
            frame.warn('wall builder warned')
            b.box(p.id, scope, 'graphic', p.xr, p.yr, (p.z0, p.z1), p.mat['color'], inferred=True, note=note)

        def four_argument_builder(b, p, scope, note):          # a builder that does not take the frame reads it from the Placed
            seen['placed'] = p.frame
            p.frame.assume(p.id, 'groove_m', 0.02, 'choice')
            b.box(p.id, scope, 'partition', p.xr, p.yr, (p.z0, p.z1), p.mat['color'], inferred=True, note=note)
        d = island({'id': 'prop', 'kind': 'box', 'side': 'centre', 'centre_m': [1.0, 1.0]},
                   {'id': 'poster', 'kind': 'graphic_panel', 'side': 'back'},
                   {'id': 'divider', 'kind': 'partition', 'side': 'centre', 'centre_m': [-1.0, 0.0], 'size_m': [1.2, 0.1, 1.2]})
        d['form'] = dict(d['form'], open_sides=['front', 'left', 'right'])
        d['booth_type'] = 'peninsula'
        with mock.patch.dict(mt.BUILDERS, {'box': floor_builder, 'graphic_panel': wall_builder, 'partition': four_argument_builder}):
            spec, report, _, _ = compile_(d)
        self.assertEqual(report['dropped'], [])
        for key in ('floor', 'wall', 'placed'):
            self.assertIsInstance(seen[key], mt.Frame, key)
        self.assertEqual((seen['floor'].X0, seen['floor'].X1, seen['floor'].H), (-4.0, 4.0, 4.5))
        self.assertIn({'id': 'prop', 'what': 'label', 'value': 'other'}, report['assumed'])
        self.assertIn({'id': 'poster', 'what': 'inset_m', 'value': 0.05}, report['assumed'])
        self.assertIn({'id': 'divider', 'what': 'groove_m', 'value': 0.02}, report['choices'])
        self.assertIn({'id': 'prop', 'what': 'floor builder adjusted'}, report['adjustments'])
        self.assertIn('floor builder warned', report['warnings'])
        self.assertIn('wall builder warned', report['warnings'])
        # a builder that fails leaves nothing behind, as before
        def failing(b, p, scope, note, frame):
            frame.assume(p.id, 'label', 'lost')
            frame.warn('lost warning')
            raise ValueError('cannot build')
        with mock.patch.dict(mt.BUILDERS, {'box': failing}):
            _, report, _, _ = compile_(island({'id': 'prop', 'kind': 'box', 'side': 'centre', 'centre_m': [1.0, 1.0]}))
        self.assertEqual([x['id'] for x in report['dropped']], ['prop'])
        self.assertNotIn('lost warning', report['warnings'])
        self.assertFalse([a for a in report['assumed'] if a['what'] == 'label'])

    def test_a_default_along_read_back_from_the_report_is_accepted(self):
        cases = ((2.0, 1.7, 0.8, 'front_left'), (2.0, 0.6, 0.0, 'front_left'), (4.67, 0.69, 0.235, 'front_left'),
                 (6.6, 0.7301, 0.2918, 'back_left'), (2.3, 1.1, 0.0, 'front_right'), (3.7, 2.5, 0.8, 'back_right'),
                 (5.1, 0.93, 0.235, 'back_left'), (4.1, 4.2, 2.0, 'front_left'))
        for ow, od, radius, corner in cases:
            with self.subTest(size=(ow, od), radius=radius, corner=corner):
                el = canopy(size_m=[ow, od, 0.3], params={'corner_radius_m': radius, 'sign': {'corner': corner}})
                spec, report, _, _ = compile_(island(el, hanging=True))
                self.assertEqual(report['dropped'], [])
                along = next(a['value'] for a in report['assumed'] if a['id'] == 'crown' and a['what'] == 'sign.along_m')
                self.assertEqual(along, [round(v, 4) for v in along], 'assumed values are recorded to 4 decimals')
                again = copy.deepcopy(el)
                again['params']['sign']['along_m'] = list(along)
                d = island(again, hanging=True)
                self.assertEqual(dc.validate_design(d), [], (along, 'the value an agent reads back must be accepted'))
                spec2, report2, _, _ = compile_(d)
                self.assertEqual(report2['dropped'], [])
                self.assertEqual(spec2['components'], spec['components'], 'and it builds the same canopy')

    def test_end_pieces_meet_the_frame_and_the_middle_exactly_for_odd_decimals(self):
        bands = {'front': 0.3117, 'back': 0.4213, 'left': 0.3571, 'right': 0.3009}
        for corner in ('front_left', 'front_right', 'back_left', 'back_right'):
            with self.subTest(corner=corner):
                sign = {'corner': corner, 'along_m': [1.6071, 1.0033], 'drop_m': 0.3017, 'end_radius_m': 0.1234, 'text_chars': 0}
                el = canopy(size_m=[5.0123, 3.0457, 0.3], centre_m=[0.0123, -0.0211],
                            params={'corner_radius_m': 0.3337, 'band_m': bands, 'sign': sign})
                spec, report, _, _ = compile_(island(el, hanging=True))
                self.assertEqual(report['dropped'], [])
                parts = pieces(spec)
                ends = [c for c in parts if c['rotation_deg'] != [-90, 0, 0]]
                flats = [c for c in parts if c['rotation_deg'] == [-90, 0, 0]]
                middle = next(c for c in flats if c['role'] == 'canopy_sign')
                others = [c for c in flats if c['role'] == 'canopy_frame']
                self.assertEqual(len(ends), 2)
                vertices = [p for c in flats for p in plan_polygon(c)]
                for c in ends:
                    poly = plan_footprint(c)
                    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
                    rect = (min(xs), max(xs), min(ys), max(ys))
                    for corner_point in ((rect[0], rect[2]), (rect[1], rect[2]), (rect[1], rect[3]), (rect[0], rect[3])):
                        self.assertLess(min(math.dist(corner_point, v) for v in vertices), 1e-9, (c['id'], corner_point))
                    # no overlap into the frame or into the middle piece, and no gap (the tiling test covers the rest)
                    for other in others + [middle]:
                        self.assertLess(mt._clip_area(plan_polygon(other), rect), 1e-9, (c['id'], other['id']))
                    # the same top and the same lowest point as the middle piece
                    top_m, bottom_m = z_range(middle)[1], z_range(middle)[0]
                    zs = [c['location'][2] + z for _, z in c['params']['profile']]
                    self.assertAlmostEqual(max(zs), top_m, delta=1e-9)
                    self.assertAlmostEqual(min(zs), bottom_m, delta=1e-9)
                # a point 1 mm into the stretch is in the end piece only, a point 1 mm beyond the free end is in the frame only
                first, second = corner.split('_')
                sx = 1 if second == 'left' else -1
                sy = 1 if first == 'front' else -1
                ox0, ox1, oy0, oy1 = 0.0123 - 5.0123 / 2, 0.0123 + 5.0123 / 2, -0.0211 - 3.0457 / 2, -0.0211 + 3.0457 / 2
                x_corner, y_corner = (ox0 if sx == 1 else ox1), (oy0 if sy == 1 else oy1)
                band_first, band_second = bands[first], bands[second]
                free = (x_corner + sx * 1.6071, y_corner + sy * band_first / 2, sx, 0)
                free2 = (x_corner + sx * band_second / 2, y_corner + sy * 1.0033, 0, sy)
                footprints = [plan_footprint(c) for c in ends]
                frame_polys = [plan_polygon(c) for c in others]
                for x, y, dx, dy in (free, free2):
                    self.assertTrue(any(inside(f, x - dx * 0.001, y - dy * 0.001) for f in footprints))
                    self.assertFalse(any(inside(p, x - dx * 0.001, y - dy * 0.001) for p in frame_polys))
                    self.assertFalse(any(inside(f, x + dx * 0.001, y + dy * 0.001) for f in footprints))
                    self.assertTrue(any(inside(p, x + dx * 0.001, y + dy * 0.001) for p in frame_polys))

    def test_end_radius_lower_bound_includes_the_radius(self):
        params = {'corner_radius_m': 0.6, 'band_m': {'front': 0.2, 'back': 0.2, 'left': 0.2, 'right': 0.2}}

        def found(along, end):
            sign = {'corner': 'front_left', 'along_m': along, 'drop_m': 0.3, 'end_radius_m': end, 'text_chars': 0}
            return dc.validate_design(island(canopy(params=dict(params, sign=sign)), hanging=True))
        self.assertEqual(found([0.7, 1.0], 0.0), [])
        self.assertTrue(found([0.7, 1.0], 0.3), 'the cut would put the 0.3 m fillet partly on the corner arc: lower bound 0.901')
        self.assertTrue(found([1.0, 0.7], 0.3))
        self.assertTrue(any('0.901' in e['message'] for e in found([0.7, 1.0], 0.3)), found([0.7, 1.0], 0.3))
        self.assertEqual(found([0.95, 1.0], 0.3), [])


EXISTING_SHA256 = {          # sha256 of the output of the designs below, compiled at 320x200 before canopy_frame and the hook existed
    'template': {
        'spec.json': '78cd14e64918695daf8ca67aabc2504ca20147291bd0052675f82381fedc6d55',
        'compile_report.json': '37534c078451e538c24575108fcf97a9e9fde56d2df34364e9e404c89532ce44',
    },
    'every_old_kind': {
        'spec.json': 'db3d5942ed50b9cdd8ca64736fd3072cd2a9963c942a1f0d83f8a7b7e326d7e2',
        'compile_report.json': '0cdd28b1dacbd3cac75ce84395cd67da59fdcdaa492c28d1dd0bebcb53ba5f7f',
    },
}


class Unchanged(unittest.TestCase):
    def test_existing_kinds_output_unchanged(self):
        for name, d in (('template', TEMPLATE), ('every_old_kind', every_kind_design())):
            with self.subTest(name):
                with tempfile.TemporaryDirectory() as tmp:
                    dc.compile_design(d, tmp, (320, 200))
                    got = {f: hashlib.sha256((Path(tmp) / f).read_bytes()).hexdigest() for f in EXISTING_SHA256[name]}
                self.assertEqual(got, EXISTING_SHA256[name])


class TextAndBrief(unittest.TestCase):
    def test_text_not_in_the_brief_is_refused(self):
        d = design()
        d['elements'][1]['text'] = {'body': 'BEST COFFEE IN TOWN'}
        with self.assertRaises(dc.DesignError) as ctx:
            compile_(d)
        self.assertEqual(ctx.exception.errors[0]['code'], 'TEXT_PROVENANCE')

    def test_text_needs_brief_provenance(self):
        d = design()
        d['elements'][1]['provenance'] = dict(d['elements'][1]['provenance'], text='reference')
        with self.assertRaises(dc.DesignError):
            compile_(d)

    def test_brief_text_is_built(self):
        spec, _, _, _ = compile_(TEMPLATE)
        bodies = [c['params']['body'] for c in spec['components'] if c['kind'] == 'text']
        self.assertEqual(bodies, ['DEMO COFFEE', 'DEMO COFFEE'])

    def test_each_requirement_kind_reports_unmet(self):
        d = design()
        d['elements'] = [e for e in d['elements'] if e['id'] not in ('screen', 'greens')]
        d['elements'][1]['text'] = None
        d['elements'][2]['text'] = None
        d['elements'].append({'id': 'halo', 'kind': 'ring'})
        _, report, _, _ = compile_(d)
        status = {i['id']: i['status'] for i in report['brief']['items']}
        self.assertEqual(status, {'r1': 'satisfied', 'r2': 'satisfied', 'r3': 'unsatisfied', 'r4': 'satisfied',
                                  'r5': 'unsatisfied', 'r6': 'unsatisfied', 'r7': 'unsatisfied'})
        self.assertEqual(report['brief']['status'], 'unsatisfied')

    def test_invalid_design_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'design.json'
            p.write_text(json.dumps(design(booth_type='corner')), encoding='utf-8')
            code, summary = dc.run(p, Path(tmp) / 'out')
            self.assertEqual(code, 2)
            self.assertEqual(summary['status'], 'design_invalid')
            self.assertFalse((Path(tmp) / 'out').exists())

    def test_cli_through_boothctl(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'boothctl.py'), 'design-compile', '--design',
                                str(ROOT / 'templates' / 'design_template.json'), '--out', tmp, '--canvas', '320x200'],
                               capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(r.returncode, 0, r.stderr)
            summary = json.loads(r.stdout)
            self.assertEqual(summary['brief']['status'], 'satisfied')


BASELINE_SHA256 = {          # sha256 of the template design compiled at 320x200 before --fit existed
    'spec.json': '78cd14e64918695daf8ca67aabc2504ca20147291bd0052675f82381fedc6d55',
    'compile_report.json': '37534c078451e538c24575108fcf97a9e9fde56d2df34364e9e404c89532ce44',
    'design_context.json': 'c61bfa15655c2881c199f32d95594348a297b3bd3be47b179a2d23602443d3d1',
}


def fit_file(tmp, status='fitted', size=(320, 200), image_size=None, floor_z=0.0, dims=None, camera=None):
    """A box_fit.json stand-in and a matching picture in tmp -> (fit_path, image_path, fit)."""
    from PIL import Image
    cam = {'projection': 'perspective', 'position': [1.5, -9.0, 1.6], 'target': [0.2, 0.1, 1.2], 'lens_mm': 41.5,
           'sensor_width_mm': 36.0, 'principal_point': [0.52, 0.48], 'pixel_aspect': [1, 1], 'fit_status': 'landmark_fitted',
           'roll_deg': 1.25}
    fit = {'status': status, 'dims_m': dims or {'W': 7.25, 'D': 4.5, 'H': 3.0}, 'floor_z': floor_z,
           'image': {'width': size[0], 'height': size[1]}, 'camera': camera or cam}
    fit_path, image_path = Path(tmp) / 'box_fit.json', Path(tmp) / 'picture.png'
    fit_path.write_text(json.dumps(fit), encoding='utf-8')
    Image.new('RGB', image_size or size, (90, 120, 150)).save(image_path)
    return fit_path, image_path, fit


class FitCamera(unittest.TestCase):
    def compile_fit(self, **kw):
        with tempfile.TemporaryDirectory() as tmp:
            fit_path, image_path, fit = fit_file(tmp, **kw)
            out = Path(tmp) / 'out'
            spec, report = dc.compile_design(TEMPLATE, out, fit=json.loads(fit_path.read_text(encoding='utf-8')),
                                             image_path=image_path, fit_path=fit_path)
            files = sorted(p.name for p in out.iterdir())
            image_bytes = image_path.read_bytes()
            copied = (out / spec['source']['image']).read_bytes() if (out / spec['source']['image']).exists() else None
        return spec, report, fit, files, image_bytes, copied

    def test_fit_camera_is_copied_verbatim_including_fit_status(self):
        for cam_changes in ({}, {'fit_status': 'reviewed', 'roll_deg': -3.5, 'lens_mm': 28.0}):
            _, _, fit, _, _, _ = self.compile_fit()
            cam = dict(fit['camera'], **cam_changes)
            spec, report, _, files, image_bytes, copied = self.compile_fit(camera=cam)
            self.assertEqual(spec['camera'], cam)
            self.assertEqual(spec['camera']['fit_status'], cam['fit_status'])
            self.assertNotIn('design_canvas.png', files)
            self.assertEqual(copied, image_bytes)
            self.assertEqual((spec['source']['width'], spec['source']['height']), (320, 200))
            self.assertEqual(spec['source']['sha256'], __import__('hashlib').sha256(image_bytes).hexdigest())
            self.assertTrue(report['valid'], report['validation_errors'])
            self.assertEqual(report['fit']['status'], 'fitted')
            self.assertTrue(report['fit']['path'].endswith('box_fit.json'))

    def test_fit_dims_override_footprint_and_are_recorded_as_fitted(self):
        spec, report, _, _, _, _ = self.compile_fit(dims={'W': 7.25, 'D': 4.5, 'H': 3.0}, floor_z=0.1)
        self.assertEqual(spec['scale']['footprint_m'], [7.25, 4.5])
        floor = next(c for c in spec['components'] if c['id'] == 'floor')
        lo, hi = world_bounds(floor)
        self.assertAlmostEqual(hi[0] - lo[0], 7.25, 3)
        self.assertAlmostEqual(hi[1] - lo[1], 4.5, 3)
        self.assertAlmostEqual(hi[2], 0.1, 3)                       # floor_z comes from the fit, not the design
        notes = [u for u in spec['uncertainties'] if 'footprint: fitted' in u['description']]
        self.assertEqual(len(notes), 1, spec['uncertainties'])

    def test_no_fit_output_is_byte_identical_to_before(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            dc.compile_design(TEMPLATE, tmp, (320, 200))
            got = {n: hashlib.sha256((Path(tmp) / n).read_bytes()).hexdigest() for n in BASELINE_SHA256}
            self.assertTrue((Path(tmp) / 'design_canvas.png').is_file())
        self.assertEqual(got, BASELINE_SHA256)

    def test_fit_and_image_must_come_together(self):
        with tempfile.TemporaryDirectory() as tmp:
            fit_path, image_path, _ = fit_file(tmp)
            d = Path(tmp) / 'design.json'
            d.write_text(json.dumps(TEMPLATE), encoding='utf-8')
            for kw in ({'fit_path': fit_path}, {'image_path': image_path}):
                code, summary = dc.run(d, Path(tmp) / 'out', **kw)
                self.assertEqual(code, 2)
                self.assertEqual(summary['status'], 'input_error')
                self.assertFalse((Path(tmp) / 'out').exists())
            r = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'boothctl.py'), 'design-compile', '--design', str(d),
                                '--out', str(Path(tmp) / 'out'), '--fit', str(fit_path)], capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            code, summary = dc.run(d, Path(tmp) / 'out', fit_path=fit_path, image_path=image_path)
            self.assertEqual(code, 0, summary)

    def test_unusable_fit_or_picture_exits_2(self):
        for kw in ({'status': 'ambiguous'}, {'status': 'blocked'}, {'image_size': (160, 100)}):
            with tempfile.TemporaryDirectory() as tmp:
                fit_path, image_path, _ = fit_file(tmp, **kw)
                d = Path(tmp) / 'design.json'
                d.write_text(json.dumps(TEMPLATE), encoding='utf-8')
                code, summary = dc.run(d, Path(tmp) / 'out', fit_path=fit_path, image_path=image_path)
                self.assertEqual(code, 2, kw)
                self.assertEqual(summary['status'], 'input_error', kw)
                self.assertFalse((Path(tmp) / 'out').exists())


# ------------------------------------------------------------------ lit_panel: a wall panel with a recessed light groove and a niche
from geometry import mesh_for, transform_point

LIT_NICHE = {'width_m': 0.8, 'height_m': 1.4, 'bottom_m': 0.8, 'depth_m': 0.1, 'corner_radius_m': 0.4, 'ring_width_m': 0.04}
LIT_PARAMS = {'corner_radius_m': 0.5, 'inset_m': 0.08, 'groove_width_m': 0.03, 'groove_depth_m': 0.02, 'light_hex': '#ffe2a8'}
LIT_SIZE = [2.4, 0.15, 2.8]


def lit(**kw):
    """A lit_panel standing free in the middle of an island, facing the front (yaw 0 faces -Y), 2.4 x 0.15 x 2.8 m."""
    el = {'id': 'wallpanel', 'kind': 'lit_panel', 'side': 'centre', 'centre_m': [0.0, 0.0], 'size_m': list(LIT_SIZE),
          'params': dict(LIT_PARAMS)}
    params = kw.pop('params', None)
    if params is not None:
        el['params'] = dict(LIT_PARAMS, **params)
    el.update(kw)
    return el


def lit_parts(spec, eid='wallpanel'):
    return [c for c in spec['components'] if c.get('group') == eid]


def panel_vertices(c, centre, yaw_deg):
    """Vertices of a part as (u, n, z) in the panel frame: u along the width, n away from the viewer (the face is at
    n = -thickness / 2), z the world height. They come from the real mesh, never from the builder's own numbers."""
    yaw = math.radians(yaw_deg)
    out = []
    for v in mesh_for(c)[0]:
        x, y, z = transform_point(v, c['location'], c['rotation_deg'])
        dx, dy = x - centre[0], y - centre[1]
        out.append((dx * math.cos(yaw) + dy * math.sin(yaw), -dx * math.sin(yaw) + dy * math.cos(yaw), z))
    return out


def panel_prism(c, centre, yaw_deg):
    """-> (polygon of (u, world z), n range) of a profile_extrusion part of a lit_panel, in the panel frame."""
    assert c['kind'] == 'profile_extrusion' and c['rotation_deg'][:2] == [0, 0], (c['id'], c['kind'], c['rotation_deg'])
    yaw = math.radians(yaw_deg)
    dx, dy = c['location'][0] - centre[0], c['location'][1] - centre[1]
    u0, n0 = dx * math.cos(yaw) + dy * math.sin(yaw), -dx * math.sin(yaw) + dy * math.cos(yaw)
    poly = [(u0 + x, c['location'][2] + z) for x, z in c['params']['profile']]
    return poly, (n0 - c['params']['depth'] / 2, n0 + c['params']['depth'] / 2)


def solid_at(prisms, u, n, z):
    return any(lo < n < hi and inside(poly, u, z) for poly, (lo, hi) in prisms)


def arch_distance(u, z, a, zb, zt, r):
    """Distance from (u, z) to the arch shape |u| <= a, zb <= z <= zt, its two top corners rounded by r (0 on or inside it)."""
    def to_rect(u0, u1, z0, z1):
        return math.hypot(max(u0 - u, 0.0, u - u1), max(z0 - z, 0.0, z - z1))
    parts = [to_rect(-a, a, zb, zt - r), to_rect(-(a - r), a - r, zb, zt)]
    if r > 0:
        parts += [max(0.0, math.hypot(u - s * (a - r), z - (zt - r)) - r) for s in (-1, 1)]
    return min(parts)


def inside_depth(u, z, a, zt, r):
    """How far (u, z) lies inside the edge of the shape |u| <= a, z <= zt, top corners rounded by r (negative outside)."""
    d = min(a - abs(u), zt - z)
    if r > 0 and abs(u) > a - r and z > zt - r:
        d = r - math.hypot(abs(u) - (a - r), z - (zt - r))
    return d


class LitPanel(unittest.TestCase):
    CENTRE = (0.0, 0.0)

    def build(self, el, **kw):
        spec, report, _, _ = compile_(island(el, **kw))
        self.assertEqual(report['dropped'], [])
        return spec, report

    def role(self, spec, role, eid='wallpanel'):
        return [c for c in lit_parts(spec, eid) if c['role'] == role]

    def prisms(self, spec, centre=(0.0, 0.0), yaw=0.0, roles=None):
        return [panel_prism(c, centre, yaw) for c in lit_parts(spec) if roles is None or c['role'] in roles]

    def base(self, spec, centre=(0.0, 0.0), yaw=0.0):
        return min(v[2] for c in lit_parts(spec) for v in panel_vertices(c, centre, yaw))

    # ---- the groove
    def test_lit_panel_groove_is_recessed_behind_the_panel_face(self):
        for depth in (0.02, 0.035):
            for yaw in (0.0, 30.0):
                with self.subTest(groove_depth_m=depth, yaw=yaw):
                    centre = [0.4, -0.3] if yaw else [0.0, 0.0]
                    spec, _ = self.build(lit(params={'groove_depth_m': depth}, centre_m=centre, yaw_deg=yaw or None))
                    strips = self.role(spec, 'lit_groove')
                    self.assertTrue(strips, 'the light band is a part of its own')
                    bodies = [c for c in lit_parts(spec) if c['role'] != 'lit_groove']
                    face = min(v[1] for c in bodies for v in panel_vertices(c, centre, yaw))
                    self.assertAlmostEqual(face, -LIT_SIZE[1] / 2, delta=1e-4, msg='the panel face is the front of its thickness')
                    for c in strips:
                        front = min(v[1] for v in panel_vertices(c, centre, yaw))
                        self.assertGreaterEqual(front - face, depth - 0.001, f'{c["id"]}: the light band lies behind the panel face')
                        self.assertGreater(c['material'].get('emission', 0), 0, 'the band glows')
                        self.assertEqual([round(x, 4) for x in c['material']['color'][:3]],
                                         [round(int('ffe2a8'[i:i + 2], 16) / 255, 4) for i in (0, 2, 4)], 'light_hex is the light colour')
                    # the body leaves the groove open at the front, closes it behind, and has the panel face on both sides
                    prisms = self.prisms(spec, centre, yaw, roles=('lit_panel',))
                    z = self.base(spec, centre, yaw) + 1.0
                    u_groove = -(LIT_SIZE[0] / 2 - 0.08 - 0.015)
                    self.assertFalse(solid_at(prisms, u_groove, face + depth / 2, z), 'the groove is open at the front')
                    self.assertTrue(solid_at(prisms, u_groove, face + depth + 0.005, z), 'and has a floor behind it')
                    self.assertTrue(solid_at(prisms, -1.16, face + 0.005, z), 'with the panel face outside it')
                    self.assertTrue(solid_at(prisms, -1.0, face + 0.005, z), 'and inside it')

    def test_lit_panel_groove_follows_the_panel_outline_for_two_shapes(self):
        shapes = (([2.4, 0.15, 2.6], 0.5, 0.08, 0.03), ([1.6, 0.12, 2.0], 0.8, 0.05, 0.04), ([3.0, 0.2, 2.4], 0.0, 0.1, 0.02))
        extents = []
        for size, radius, inset, width in shapes:
            with self.subTest(size=size, corner_radius_m=radius):
                spec, _ = self.build(lit(size_m=size, params={'corner_radius_m': radius, 'inset_m': inset, 'groove_width_m': width}))
                strip = self.role(spec, 'lit_groove')
                self.assertEqual(len(strip), 1)
                pts = panel_vertices(strip[0], self.CENTRE, 0.0)
                base = self.base(spec)
                W, H = size[0], size[2]
                for u, _, z in pts:
                    d = inside_depth(u, z - base, W / 2, H, radius)
                    self.assertTrue(inset - 1e-6 <= d <= inset + width + 1e-6, f'({u:.4f}, {z - base:.4f}) is {d:.5f} m inside the edge')
                us = [v[0] for v in pts]
                zs = [v[2] - base for v in pts]
                self.assertAlmostEqual(min(us), -(W / 2 - inset), delta=1e-5)
                self.assertAlmostEqual(max(us), W / 2 - inset, delta=1e-5)
                self.assertAlmostEqual(max(zs), H - inset, delta=1e-5, msg='it runs across the top')
                self.assertAlmostEqual(min(zs), 0.0, delta=1e-5, msg='and down to the bottom: an upside down U')
                if radius > inset + width:
                    corner = [1 for u, _, z in pts if abs(u) > W / 2 - radius and z - base > H - radius]
                    self.assertGreater(len(corner), 20, 'the rounded top corners are followed by points on the curve, not cut off')
                extents.append((round(min(us), 3), round(max(zs), 3)))
        self.assertEqual(len(set(extents)), len(shapes), 'a different panel gives a different groove')

    def test_lit_panel_groove_is_the_only_light_when_there_is_no_niche(self):
        for el in (lit(), lit(params={'niche': None})):
            spec, _ = self.build(el)
            self.assertEqual({c['role'] for c in lit_parts(spec) if c['material'].get('emission')}, {'lit_groove'})
            self.assertFalse(self.role(spec, 'lit_niche'))
            self.assertFalse(self.role(spec, 'lit_niche_light'))

    # ---- the niche
    def niche_spec(self, **niche):
        n = dict(LIT_NICHE, **niche)
        spec, report = self.build(lit(params={'niche': n}))
        return spec, report, n

    def test_lit_panel_niche_has_back_and_floor_and_does_not_go_through(self):
        for niche in ({}, {'depth_m': 0.14}, {'corner_radius_m': 0.0, 'depth_m': 0.06}, {'bottom_m': 0.5, 'height_m': 1.9}):
            with self.subTest(niche=niche):
                spec, _, n = self.niche_spec(**niche)
                prisms = self.prisms(spec)
                base = self.base(spec)
                T, D = LIT_SIZE[1], n['depth_m']
                front, back = -T / 2, T / 2
                backs = self.role(spec, 'lit_niche')
                self.assertEqual(len(backs), 1, 'one back wall')
                lo, hi = panel_prism(backs[0], self.CENTRE, 0.0)[1]
                self.assertAlmostEqual(lo, front + D, delta=1e-5, msg='the back wall is depth_m behind the panel face')
                self.assertAlmostEqual(hi, back, delta=1e-5, msg='and runs to the back of the panel')
                self.assertGreaterEqual(hi - lo, 0.01 - 1e-9, 'with a wall behind the niche')
                # every column of the opening is empty for depth_m, then solid to the back of the panel
                a, hn, r, bn = n['width_m'] / 2, n['height_m'], n['corner_radius_m'], n['bottom_m']
                step, checked = 0.005, 0
                for iu in range(-5, 6):
                    for iz in range(1, 13):
                        u, zl = iu * a / 5.5, iz * hn / 13.0
                        if min(zl, inside_depth(u, zl, a, hn, r)) < 0.008:
                            continue
                        z = base + bn + zl
                        column = [front + (k + 0.5) * step for k in range(int(T / step)) if solid_at(prisms, u, front + (k + 0.5) * step, z)]
                        self.assertTrue(column, f'something stands behind the opening at ({u:.3f}, {zl:.3f}): it does not go through')
                        self.assertGreater(column[0], front + D, 'the opening is empty in front of the back wall')
                        self.assertAlmostEqual(column[-1], back - step / 2, delta=1e-6, msg='and solid to the back of the panel')
                        checked += 1
                self.assertGreater(checked, 50)
                # the floor: solid under the opening over its whole depth, empty above it
                mid_n = front + D / 2
                self.assertTrue(solid_at(prisms, 0.0, mid_n, base + bn - 0.004), 'the niche has a floor')
                self.assertTrue(solid_at(prisms, a - 0.02, front + 0.004, base + bn - 0.004))
                self.assertTrue(solid_at(prisms, a - 0.02, front + D - 0.004, base + bn - 0.004))
                self.assertFalse(solid_at(prisms, 0.0, mid_n, base + bn + 0.004), 'and the opening starts at the floor')
                # the opening is an arch where it is rounded: the corner of its rectangle is filled
                if r > 0.05:
                    self.assertTrue(solid_at(prisms, a - 0.01, mid_n, base + bn + hn - 0.01))
                self.assertFalse(solid_at(prisms, 0.0, mid_n, base + bn + hn - 0.01))

    def test_lit_panel_niche_ring_of_light_surrounds_the_opening(self):
        spec, _, n = self.niche_spec()
        rw = n['ring_width_m']
        base = self.base(spec)
        face = -LIT_SIZE[1] / 2
        lights = self.role(spec, 'lit_niche_light')
        self.assertTrue(lights)
        for c in lights:
            self.assertGreater(c['material'].get('emission', 0), 0)
            front = min(v[1] for v in panel_vertices(c, self.CENTRE, 0.0))
            self.assertAlmostEqual(front, face, delta=1e-5, msg=f'{c["id"]}: the ring is flush with the panel face: no step, no gap')
        glow = [panel_prism(c, self.CENTRE, 0.0) for c in lights]
        every = self.prisms(spec)
        a, zb, r = n['width_m'] / 2, n['bottom_m'], n['corner_radius_m']
        zt = zb + n['height_m']
        sides = set()
        for iu in range(-50, 51):
            for iz in range(-12, 180):
                u, z = iu * 0.0117, 0.55 + iz * 0.0131
                opening = arch_distance(u, z, a, zb, zt, r)
                grown = arch_distance(u, z, a + rw, zb - rw, zt + rw, r + rw)
                shrunk = arch_distance(u, z, a + rw - 0.004, zb - rw + 0.004, zt + rw - 0.004, max(r + rw - 0.004, 0.0))
                lit_here = solid_at(glow, u, face + 0.003, base + z)
                if opening == 0.0 and arch_distance(u, z, a - 0.004, zb + 0.004, zt - 0.004, max(r - 0.004, 0.0)) == 0.0:
                    self.assertFalse(solid_at(every, u, face + 0.003, base + z), f'the opening is open at ({u:.3f}, {z:.3f})')
                elif opening > 0.004 and shrunk == 0.0:
                    self.assertTrue(lit_here, f'({u:.3f}, {z:.3f}) lies in the ring of light')
                    sides.add('left' if u < -a else 'right' if u > a else 'top' if z > zt - r else 'bottom')
                elif grown > 0.004:
                    self.assertFalse(lit_here, f'({u:.3f}, {z:.3f}) lies outside the ring of light')
        self.assertEqual(sides, {'left', 'right', 'top', 'bottom'}, 'the ring runs round all four sides of the opening')
        # the panel face around the ring is one flat plane with it: every part that reaches the front reaches the same plane
        for c in self.role(spec, 'lit_panel'):
            front = min(v[1] for v in panel_vertices(c, self.CENTRE, 0.0))
            if front < face + 0.001:
                self.assertAlmostEqual(front, face, delta=1e-5)

    def test_lit_panel_niche_follows_a_turned_panel(self):
        centre = [0.5, -0.4]
        spec, _ = self.build(lit(centre_m=centre, yaw_deg=40.0, params={'niche': dict(LIT_NICHE)}))
        base = self.base(spec, centre, 40.0)
        prisms = self.prisms(spec, centre, 40.0)
        self.assertFalse(solid_at(prisms, 0.0, -LIT_SIZE[1] / 2 + 0.03, base + 1.4))
        self.assertTrue(solid_at(prisms, 0.0, -LIT_SIZE[1] / 2 + 0.12, base + 1.4))
        self.assertTrue(solid_at(prisms, 0.0, 0.0, base + 0.3))

    def test_lit_panel_is_placed_like_a_partition(self):
        d = design()
        d['elements'] = [lit(side='back', centre_m=None, size_m=[2.0, 0.15, None], along=0.3, params={'niche': {}})]
        d['requirements'] = []
        spec, report, _, _ = compile_(d)
        self.assertEqual(report['dropped'], [])
        back = next(c for c in spec['components'] if c['id'] == 'perimeter_wall_back')
        face = back['location'][1] - back['params']['size'][1] / 2
        ys = [v[1] for c in lit_parts(spec) for v in (transform_point(w, c['location'], c['rotation_deg']) for w in mesh_for(c)[0])]
        self.assertAlmostEqual(max(ys), face, delta=0.02, msg='it stands against the back wall')
        self.assertTrue(validate(spec, source_size=[320, 200])['valid'])
        niche = {a['what'] for a in report['assumed'] if a['id'] == 'wallpanel'}
        self.assertTrue({'niche.width_m', 'niche.height_m', 'niche.bottom_m', 'niche.depth_m'} <= niche, niche)

    def test_lit_panel_height_cut_by_the_limit_is_recorded(self):
        spec, report = self.build(lit(size_m=[2.4, 0.15, 6.0]), height=3.0)
        cut = [a for a in report['adjustments'] if a['id'] == 'wallpanel']
        self.assertTrue(cut and 'height' in cut[0]['what'], report['adjustments'])
        top = max(v[2] for c in lit_parts(spec) for v in panel_vertices(c, self.CENTRE, 0.0))
        self.assertLessEqual(top, 3.0 + 1e-6)

    def test_lit_panel_rejects_bad_params(self):
        def found(el):
            return dc.validate_design(island(el))

        def param_error(el, path_part):
            errors = found(el)
            self.assertTrue(any(e['code'] == 'PARAM' and path_part in e['where'] for e in errors), (path_part, errors))
        self.assertEqual(found(lit()), [])
        self.assertEqual(found(lit(params={'niche': dict(LIT_NICHE)})), [])
        self.assertEqual(found(lit(params={'niche': dict(LIT_NICHE, depth_m=0.14)})), [], 'a wall of 0.01 m is left behind the niche')
        param_error(lit(params={'inset_m': 0.7, 'groove_width_m': 0.5}), 'inset_m')              # inset + width = half the panel
        param_error(lit(params={'inset_m': 0.9, 'groove_width_m': 0.5}), 'inset_m')
        param_error(lit(params={'inset_m': 0.0}), 'inset_m')
        param_error(lit(params={'groove_width_m': -0.01}), 'groove_width_m')
        param_error(lit(params={'groove_depth_m': 0.0}), 'groove_depth_m')
        param_error(lit(params={'groove_depth_m': 0.15}), 'groove_depth_m')                      # as deep as the panel is thick
        param_error(lit(params={'corner_radius_m': 1.3}), 'corner_radius_m')                     # more than half the width
        param_error(lit(params={'corner_radius_m': -0.1}), 'corner_radius_m')
        param_error(lit(size_m=[2.4, 0.15, 0.4], params={'corner_radius_m': 0.9}), 'corner_radius_m')
        param_error(lit(size_m=[-1.0, 0.15, 2.8]), 'size_m')
        param_error(lit(size_m=[2.4, 0.02, 2.8]), 'size_m')                                      # too thin for a groove
        param_error(lit(params={'niche': dict(LIT_NICHE, depth_m=0.15)}), 'niche.depth_m')       # through the whole panel
        param_error(lit(params={'niche': dict(LIT_NICHE, depth_m=0.2)}), 'niche.depth_m')
        param_error(lit(params={'niche': dict(LIT_NICHE, depth_m=0.145)}), 'niche.depth_m')      # leaves under 0.01 m behind it
        param_error(lit(params={'niche': dict(LIT_NICHE, depth_m=0.0)}), 'niche.depth_m')
        param_error(lit(params={'niche': dict(LIT_NICHE, width_m=2.3)}), 'niche')                # wider than the panel core
        param_error(lit(params={'niche': dict(LIT_NICHE, bottom_m=1.9)}), 'niche')               # runs out of the top
        param_error(lit(params={'niche': dict(LIT_NICHE, bottom_m=0.02)}), 'niche.bottom_m')     # the ring would reach the floor
        param_error(lit(params={'niche': dict(LIT_NICHE, corner_radius_m=0.5)}), 'niche.corner_radius_m')   # more than half the width
        param_error(lit(params={'niche': dict(LIT_NICHE, ring_width_m=0.0)}), 'niche.ring_width_m')
        param_error(lit(params={'niche': dict(LIT_NICHE, height_m=-1.0)}), 'niche.height_m')
        # schema: a bad colour, an unknown key
        errors = found(lit(params={'light_hex': 'warm'}))
        self.assertTrue(errors and errors[0]['code'] == 'SCHEMA', errors)
        errors = found(lit(params={'niche': dict(LIT_NICHE, shelves=3)}))
        self.assertTrue(errors and errors[0]['code'] == 'SCHEMA', errors)
        with self.assertRaises(dc.DesignError) as ctx:
            compile_(island(lit(params={'niche': dict(LIT_NICHE, depth_m=0.15)})))
        self.assertEqual(ctx.exception.errors[0]['code'], 'PARAM')

    def test_bare_lit_panel_compiles_with_assumed_defaults(self):
        bare_island = {'id': 'lit_panel', 'kind': 'lit_panel', 'hero': True, 'side': 'centre', 'centre_m': [0.0, 0.0]}
        bare_inline = {'id': 'lit_panel', 'kind': 'lit_panel', 'hero': True, 'side': 'back'}
        inline = design()
        inline['elements'] = [bare_inline]
        inline['requirements'] = []
        for name, el, d in (('island', bare_island, island(bare_island)), ('inline', bare_inline, inline)):
            with self.subTest(name):
                spec, report, _, _ = compile_(d)
                self.assertEqual(report['dropped'], [])
                self.assertIn('lit_panel', {e['id'] for e in report['elements']})
                self.assertTrue(validate(spec, source_size=[320, 200])['valid'])
                parts = lit_parts(spec, 'lit_panel')
                self.assertTrue({'lit_panel', 'lit_groove'} <= {c['role'] for c in parts})
                assumed = {a['what'] for a in report['assumed'] if a['id'] == 'lit_panel'}
                for what in ('corner_radius_m', 'inset_m', 'groove_width_m', 'groove_depth_m', 'light_hex'):
                    self.assertIn(what, assumed)
                known = assumed | {a['what'] for a in report['choices'] if a['id'] == 'lit_panel'}
                self.assertTrue({'width_m', 'depth_m', 'height_m'} <= known, known)
                self.assertFalse(any('niche' in w for w in known), 'no niche unless one is asked for')
                for c in parts:
                    self.assertEqual(c.get('group'), 'lit_panel')
                    self.assertTrue(c['id'].startswith('lit_panel'))
                # the values an agent reads back from the report are accepted and build the same panel
                value = {a['what']: a['value'] for a in report['choices'] + report['assumed'] if a['id'] == 'lit_panel'}
                again = copy.deepcopy(el)
                again['size_m'] = [value[k] for k in ('width_m', 'depth_m', 'height_m')]
                again['params'] = {k: value[k] for k in ('corner_radius_m', 'inset_m', 'groove_width_m', 'groove_depth_m', 'light_hex')}
                d2 = copy.deepcopy(d)
                d2['elements'] = [again]
                self.assertEqual(dc.validate_design(d2), [])
                spec2, report2, _, _ = compile_(d2)
                self.assertEqual(spec2['components'], spec['components'])
        # a niche asked for as {} is filled with defaults that are recorded, and accepted when read back
        spec, report, _, _ = compile_(island(lit(size_m=[2.4, 0.15, None], params={'niche': {}})))
        self.assertEqual(report['dropped'], [])
        values = {a['what']: a['value'] for a in report['assumed'] if a['id'] == 'wallpanel'}
        niche = {k[len('niche.'):]: v for k, v in values.items() if k.startswith('niche.')}
        self.assertEqual(set(niche), {'width_m', 'height_m', 'bottom_m', 'depth_m', 'corner_radius_m', 'ring_width_m'})
        self.assertEqual(dc.validate_design(island(lit(size_m=[2.4, 0.15, None], params={'niche': niche}))), [])

    def test_every_kind_covers_lit_panel(self):
        schema = dc.load_schema()
        enum = schema['$defs']['element']['properties']['kind']['enum']
        self.assertIn('lit_panel', enum)
        self.assertEqual(set(enum), set(mt.KINDS))
        self.assertIn('lit_panel', mt.FLOOR_KINDS)
        self.assertIn('lit_panel', mt.BUILDERS)
        self.assertNotIn('lit_panel', mt.OVERHEAD_BUILDERS)
        self.assertIn('lit_panel', mt.PARAM_CHECKS)
        self.assertEqual(mt.SCOPE['lit_panel'], 'shell')
        self.assertIn('lit_panel', mt.ROLE)
        self.assertNotIn('lit_panel', dc.ROLE, 'the colour role of a new kind lives in motifs.ROLE')
        self.assertIn('lit_panel', mt.DEFAULT_SIZE)
        params = schema['$defs']['element']['properties']['params']['properties']
        for key in ('groove_width_m', 'groove_depth_m', 'light_hex', 'niche', 'inset_m', 'corner_radius_m'):
            self.assertIn(key, params)
        self.assertEqual(set(params['niche']['properties']),
                         {'width_m', 'height_m', 'bottom_m', 'depth_m', 'corner_radius_m', 'ring_width_m'})
        d = island(lit())
        d['requirements'] = [{'id': 'r1', 'priority': 'must', 'kind': 'must_have', 'element_kind': 'lit_panel'}]
        _, report, _, _ = compile_(d)
        self.assertEqual(report['brief']['status'], 'satisfied', report['brief'])

    def test_lit_panel_builder_takes_the_frame_and_every_part_uses_the_element_prefix(self):
        import inspect
        self.assertGreaterEqual(len(inspect.signature(mt.BUILDERS['lit_panel']).parameters), 5, 'it takes the Frame as its fifth argument')
        spec, report = self.build(lit(params={'niche': dict(LIT_NICHE)}))
        self.assertEqual(report['status'], 'ok', report)
        self.assertFalse([w for w in report['warnings'] if 'COPLANAR' in w], report['warnings'])      # no two parts z-fight
        self.assertTrue(validate(spec, source_size=[320, 200])['valid'])
        parts = lit_parts(spec)
        self.assertLessEqual(len(parts), 20)
        for c in parts:
            self.assertTrue(c['id'].startswith('wallpanel_'), c['id'])
            self.assertEqual(c['scope_id'], 'shell')


class CanopyLeftovers(unittest.TestCase):
    """Three loose ends the canopy_frame package left for the next package of chain A."""

    def test_a_default_z_m_at_the_lower_limit_read_back_is_accepted(self):
        cases = ((0.5, 0.5, 0.114), (0.46, 0.33, 0.4), (0.2, 0.57, 0.39), (0.215, 0.6, 0.465), (0.42, 0.39, 0.4), (0.42, 0.41, 0.4))
        for zf, h, drop in cases:
            with self.subTest(floor=zf, height=h, drop=drop):
                H = zf + 0.3 + drop + h + 0.1               # the default puts the lowest point exactly 0.3 m above the floor
                sign = {'corner': 'front_left', 'along_m': [1.6, 1.0], 'drop_m': drop, 'text_chars': 0}
                el = canopy(size_m=[5.0, 3.0, h], params={'corner_radius_m': 0.3, 'sign': sign})
                d = island(el, hanging=True, height=H)
                d['layout'] = dict(d['layout'], platform_height_m=zf)
                spec, report, _, _ = compile_(d)
                self.assertEqual(report['dropped'], [])
                z = next(a['value'] for a in report['assumed'] if a['id'] == 'crown' and a['what'] == 'z_m')
                again = copy.deepcopy(el)
                again['z_m'] = z
                d2 = island(again, hanging=True, height=H)
                d2['layout'] = dict(d2['layout'], platform_height_m=zf)
                self.assertEqual(dc.validate_design(d2), [], 'the z_m an agent reads back must be accepted')
                spec2, report2, _, _ = compile_(d2)
                self.assertEqual(report2['dropped'], [])
                for c2, c1 in zip(pieces(spec2), pieces(spec)):
                    self.assertAlmostEqual(c2['location'][2], c1['location'][2], delta=1.5e-4)

    def test_default_canopy_z_m_is_rounded_inwards_when_read_back(self):
        cases = ((0.33, 0.33335, 0.5), (0.33, 0.33333, 0.5), (0.5, 0.33335, 0.3), (0.33, 0.41115, 0.4), (0.5, 0.12345, 0.5))
        for zf, drop, h in cases:
            with self.subTest(floor=zf, drop=drop, height=h):
                H = round(zf + 0.3 + drop + h + 0.1, 5)               # the default puts the lowest point exactly 0.3 m above the floor
                self.assertLess(round(H - 0.1 - h - zf, 4), 0.3 + drop - 1e-6, 'plain rounding to 4 decimals would fall below the limit')
                sign = {'corner': 'front_left', 'along_m': [1.6, 1.0], 'drop_m': drop, 'text_chars': 0}
                el = canopy(size_m=[5.0, 3.0, h], params={'corner_radius_m': 0.3, 'sign': sign})
                d = island(el, hanging=True, height=H)
                d['layout'] = dict(d['layout'], platform_height_m=zf)
                self.assertEqual(dc.validate_design(d), [])
                spec, report, _, _ = compile_(d)
                self.assertEqual(report['dropped'], [])
                z = next(a['value'] for a in report['assumed'] if a['id'] == 'crown' and a['what'] == 'z_m')
                self.assertGreaterEqual(z, 0.3 + drop - 1e-6)
                again = copy.deepcopy(el)
                again['z_m'] = z
                d2 = island(again, hanging=True, height=H)
                d2['layout'] = dict(d2['layout'], platform_height_m=zf)
                self.assertEqual(dc.validate_design(d2), [], 'the z_m an agent reads back must be accepted')
                spec2, report2, _, _ = compile_(d2)
                self.assertEqual(report2['dropped'], [])

    def test_end_pieces_meet_the_frame_exactly_when_a_value_sits_on_a_rounding_tie(self):
        cases = (
            (5.6596649999999995, 3.70936, -0.073695, -0.08229, 1.6207, 1.219835,
             {'front': 0.291975, 'back': 0.36760499999999996, 'left': 0.393565, 'right': 0.44706}, 'back_right'),
            (5.486285, 2.87004, -0.03168, -0.08273499999999999, 1.754215, 1.365435,
             {'front': 0.25519499999999995, 'back': 0.33292, 'left': 0.29195, 'right': 0.360735}, 'front_left'),
            (5.2068449999999995, 3.41997, 0.04495, -0.03499, 1.70915, 1.20473,
             {'front': 0.430355, 'back': 0.37306, 'left': 0.28476, 'right': 0.40926}, 'front_right'),
            (3.39129, 3.3458349999999997, -0.04914, -0.05733, 1.40947, 0.988615,
             {'front': 0.321565, 'back': 0.259395, 'left': 0.31323, 'right': 0.425585}, 'front_left'),
            (3.3215, 3.3999, 0.063005, 0.09271, 1.35998, 1.259485,
             {'front': 0.32673, 'back': 0.4167, 'left': 0.334155, 'right': 0.33869}, 'front_left'))
        for ow, od, cx, cy, a1, a2, bands, corner in cases:
            with self.subTest(corner=corner, size=(ow, od)):
                sign = {'corner': corner, 'along_m': [a1, a2], 'drop_m': 0.3, 'end_radius_m': 0.12, 'text_chars': 0}
                el = canopy(size_m=[ow, od, 0.3], centre_m=[cx, cy], params={'corner_radius_m': 0.3, 'band_m': bands, 'sign': sign})
                spec, report, _, _ = compile_(island(el, hanging=True))
                self.assertEqual(report['dropped'], [])
                parts = pieces(spec)
                ends = [c for c in parts if c['rotation_deg'] != [-90, 0, 0]]
                vertices = [p for c in parts if c['rotation_deg'] == [-90, 0, 0] for p in plan_polygon(c)]
                self.assertEqual(len(ends), 2)
                for c in ends:
                    poly = plan_footprint(c)
                    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
                    for corner_point in ((min(xs), min(ys)), (max(xs), min(ys)), (max(xs), max(ys)), (min(xs), max(ys))):
                        self.assertLess(min(math.dist(corner_point, v) for v in vertices), 1e-9, (c['id'], corner_point))

    def test_recipes_warn_that_a_pattern_runs_differently_on_the_sign_ends(self):
        text = (ROOT / 'references' / 'component-recipes.md').read_text(encoding='utf-8')
        self.assertIn('端头件的图案', text, 'the recipes say the end pieces carry a pattern in another direction')


# ------------------------------------------------------------------ prop_block: a block with the right size and place for a small object
PROP_LABELS = ('laptop', 'tablet', 'monitor', 'phone', 'headphone', 'plant', 'robot_arm', 'robot', 'other')


def prop(**kw):
    """A laptop-sized block standing on a table top 0.75 m above the floor, in the middle of an island."""
    el = {'id': 'laptop_one', 'kind': 'prop_block', 'side': 'centre', 'centre_m': [0.0, 0.0], 'size_m': [0.36, 0.25, 0.22],
          'z_m': 0.75, 'params': {'label': 'laptop'}}
    el.update(kw)
    return el


def floor_top(spec):
    return max(world_bounds(c)[1][2] for c in spec['components'] if c['role'] == 'floor')


class PropBlock(unittest.TestCase):
    def test_prop_block_is_one_box_with_label_in_note(self):
        for label in PROP_LABELS:
            with self.subTest(label=label):
                spec, report, _, _ = compile_(island(prop(params={'label': label})))
                self.assertEqual(report['dropped'], [])
                parts = [c for c in spec['components'] if c.get('group') == 'laptop_one']
                self.assertEqual([c['id'] for c in parts], ['laptop_one'], 'one element, one box')
                c = parts[0]
                self.assertEqual((c['kind'], c['role'], c['scope_id']), ('box', 'prop_block', 'furniture'))
                self.assertEqual(c['params']['size'], [0.36, 0.25, 0.22])
                self.assertIn(f'label: {label}', c['evidence']['note'])
                self.assertTrue(validate(spec, source_size=[320, 200])['valid'])
        # many objects are many elements, each with its own block
        two = [prop(id='tablet_one', centre_m=[-1.0, 0.0], params={'label': 'tablet'}), prop(id='phone_one', centre_m=[1.0, 0.0],
                                                                                              params={'label': 'phone'})]
        spec, report, _, _ = compile_(island(*two))
        self.assertEqual(report['dropped'], [])
        self.assertEqual(sorted(c['id'] for c in spec['components'] if c['role'] == 'prop_block'), ['phone_one', 'tablet_one'])

    def test_prop_block_label_is_closed_enum(self):
        self.assertEqual(tuple(mt.PROP_LABELS), PROP_LABELS)
        schema = dc.load_schema()
        label = schema['$defs']['element']['properties']['params']['properties']['label']
        self.assertEqual(tuple(x for x in label['enum'] if x is not None), PROP_LABELS)
        for name in PROP_LABELS:
            self.assertEqual(dc.validate_design(island(prop(params={'label': name}))), [], name)
        bad_labels = ('desk_items', 'Laptop', 'robot arm', '', 'sofa', 7, True, ['laptop'])
        for bad in bad_labels:
            with self.subTest(bad=bad):
                self.assertTrue(dc.validate_design(island(prop(params={'label': bad}))))
        # the parameter check does not lean on the schema
        form = dict(island(prop())['form'], platform_height_m=0.1)
        self.assertEqual(mt.PARAM_CHECKS['prop_block'](prop(), form), [])
        for bad in bad_labels:
            with self.subTest(check=bad):
                problems = mt.PARAM_CHECKS['prop_block'](prop(params={'label': bad}), form)
                self.assertTrue(any(path == 'params.label' for path, _ in problems), problems)
        # nor does the builder: a design that was never validated drops the element
        comp = dc.DesignCompiler(island(prop(params={'label': 'sofa'})), dc.canvas_png(320, 200), (320, 200))
        comp.build()
        self.assertEqual([x['id'] for x in comp.report['dropped']], ['laptop_one'])
        self.assertIn('label', comp.report['dropped'][0]['reason'])
        # no label asks for 'other', and the label is a plain value of params (no other field carries it)
        spec, report, _, _ = compile_(island(prop(params=None)))
        self.assertIn('label: other', next(c for c in spec['components'] if c['id'] == 'laptop_one')['evidence']['note'])

    def test_prop_block_sits_on_z_and_keeps_its_size(self):
        cases = ((0.0, [0.4, 0.3, 0.2], 0.0), (0.45, [0.6, 0.4, 0.05], 0.0), (0.75, [0.36, 0.25, 0.22], 30.0),
                 (1.1, [0.2, 0.2, 0.5], 90.0), (0.75, [0.36, 0.25, 0.22], -35.0))
        for z, size, yaw in cases:
            with self.subTest(z=z, size=size, yaw=yaw):
                spec, report, _, _ = compile_(island(prop(size_m=size, z_m=z, yaw_deg=yaw, centre_m=[1.2, -0.7])))
                self.assertEqual(report['dropped'], [])
                c = next(c for c in spec['components'] if c['id'] == 'laptop_one')
                us, ns, zs = zip(*panel_vertices(c, (1.2, -0.7), yaw))
                zf = floor_top(spec)
                self.assertAlmostEqual(min(zs), zf + z, places=4, msg='the block stands on z_m above the floor')
                self.assertAlmostEqual(max(zs), zf + z + size[2], places=4)
                self.assertAlmostEqual(max(us) - min(us), size[0], places=4)
                self.assertAlmostEqual(max(ns) - min(ns), size[1], places=4)
                self.assertAlmostEqual(c['location'][0], 1.2, places=3)
                self.assertAlmostEqual(c['location'][1], -0.7, places=3)
        # against a wall: the block stands on z_m there too, and keeps its size
        d = design()
        d['elements'] = [prop(side='back', along=0.3, centre_m=None, z_m=0.5)]
        d['requirements'] = []
        spec, report, _, _ = compile_(d)
        self.assertEqual(report['dropped'], [])
        c = next(c for c in spec['components'] if c['id'] == 'laptop_one')
        lo, hi = world_bounds(c)
        self.assertAlmostEqual(lo[2], floor_top(spec) + 0.5, places=4)
        self.assertAlmostEqual(hi[0] - lo[0], 0.36, places=4)
        self.assertAlmostEqual(hi[1] - lo[1], 0.25, places=4)

    def test_prop_block_rejects_bad_params(self):
        def found(el, **kw):
            return dc.validate_design(island(el, **kw))

        def param_error(el, path_part, **kw):
            errors = found(el, **kw)
            self.assertTrue(any(e['code'] == 'PARAM' and path_part in e['where'] for e in errors), (path_part, errors))
        self.assertEqual(found(prop()), [])
        self.assertEqual(found(prop(size_m=[0.3, None, None])), [], 'a missing value takes its default')
        for bad in ([0.0, 0.25, 0.22], [0.36, 0.0, 0.22], [0.36, 0.25, 0.0], [-0.1, 0.25, 0.22], [0.36, -0.25, 0.22], [0.36, 0.25, -1.0]):
            with self.subTest(size=bad):
                param_error(prop(size_m=bad), 'size_m')
        param_error(prop(z_m=-0.1), 'z_m')
        param_error(prop(z_m=4.5), 'z_m')                   # the base is above the 4.5 m limit
        param_error(prop(z_m=4.395), 'z_m')                 # under 0.01 m between the base and the limit
        param_error(prop(size_m=[9.0, 0.25, 0.22]), 'size_m')              # wider than the 8 x 6 m footprint
        param_error(prop(size_m=[0.4, 7.0, 0.2]), 'size_m')
        param_error(prop(size_m=[2.0, 0.3, 0.2], yaw_deg=45.0), 'size_m', footprint=(3.0, 1.5))     # turned, it needs 1.63 m
        self.assertEqual(found(prop(size_m=[2.0, 0.3, 0.2], yaw_deg=0.0), footprint=(3.0, 1.5)), [])
        errors = found(prop(params={'label': 'laptop', 'angle_deg': 30}))
        self.assertTrue(errors and errors[0]['code'] == 'SCHEMA', errors)
        errors = found(prop(size_m=[0.3, 0.3]))
        self.assertTrue(errors and errors[0]['code'] == 'SCHEMA', errors)
        with self.assertRaises(dc.DesignError) as ctx:
            compile_(island(prop(size_m=[0.0, 0.25, 0.22])))
        self.assertEqual(ctx.exception.errors[0]['code'], 'PARAM')
        # a block that reaches above the limit is cut to it and the cut is recorded; it is not refused
        el = prop(size_m=[0.4, 0.3, 6.0], z_m=0.75)
        self.assertEqual(found(el), [])
        spec, report, _, _ = compile_(island(el))
        self.assertEqual(report['dropped'], [])
        c = next(c for c in spec['components'] if c['id'] == 'laptop_one')
        self.assertAlmostEqual(world_bounds(c)[1][2], 4.5, places=4)
        cut = [a for a in report['adjustments'] if a['id'] == 'laptop_one']
        self.assertTrue(cut and 'height' in cut[0]['what'], report['adjustments'])
        # a block that is not cut records nothing
        _, report, _, _ = compile_(island(prop()))
        self.assertEqual([a for a in report['adjustments'] if a['id'] == 'laptop_one'], [])

    def test_bare_prop_block_compiles_with_assumed_defaults(self):
        bare_island = {'id': 'thing', 'kind': 'prop_block', 'side': 'centre', 'centre_m': [0.0, 0.0]}
        bare_inline = {'id': 'thing', 'kind': 'prop_block', 'side': 'back'}
        inline = design()
        inline['elements'] = [bare_inline]
        inline['requirements'] = []
        for name, el, d in (('island', bare_island, island(bare_island)), ('inline', bare_inline, inline)):
            with self.subTest(name):
                spec, report, _, _ = compile_(d)
                self.assertEqual(report['dropped'], [])
                self.assertIn('thing', {e['id'] for e in report['elements']})
                self.assertTrue(validate(spec, source_size=[320, 200])['valid'])
                parts = [c for c in spec['components'] if c.get('group') == 'thing']
                self.assertEqual([c['role'] for c in parts], ['prop_block'])
                self.assertIn('label: other', parts[0]['evidence']['note'])
                values = {a['what']: a['value'] for a in report['assumed'] if a['id'] == 'thing'}
                self.assertTrue({'label', 'width_m', 'depth_m', 'height_m', 'z_m'} <= set(values), values)
                self.assertEqual(values['label'], 'other')
                self.assertEqual([values[k] for k in ('width_m', 'depth_m', 'height_m')], list(mt.DEFAULT_SIZE['prop_block'][0]))
                # the values an agent reads back from the report are accepted and build the same block
                again = copy.deepcopy(el)
                again['size_m'] = [values[k] for k in ('width_m', 'depth_m', 'height_m')]
                again['z_m'] = values['z_m']
                again['params'] = {'label': values['label']}
                d2 = copy.deepcopy(d)
                d2['elements'] = [again]
                self.assertEqual(dc.validate_design(d2), [])
                spec2, _, _, _ = compile_(d2)
                self.assertEqual(spec2['components'], spec['components'])

    def test_every_kind_covers_prop_block(self):
        import inspect
        schema = dc.load_schema()
        enum = schema['$defs']['element']['properties']['kind']['enum']
        self.assertIn('prop_block', enum)
        self.assertEqual(set(enum), set(mt.KINDS))
        self.assertIn('prop_block', mt.FLOOR_KINDS)
        self.assertIn('prop_block', mt.BUILDERS)
        self.assertNotIn('prop_block', mt.OVERHEAD_BUILDERS)
        self.assertIn('prop_block', mt.PARAM_CHECKS)
        self.assertEqual(mt.SCOPE['prop_block'], 'furniture')
        self.assertIn('prop_block', mt.ROLE)
        self.assertNotIn('prop_block', dc.ROLE, 'the colour role of a new kind lives in motifs.ROLE')
        self.assertIn('prop_block', mt.DEFAULT_SIZE)
        self.assertGreaterEqual(len(inspect.signature(mt.BUILDERS['prop_block']).parameters), 5, 'it takes the Frame as its fifth argument')
        self.assertIn('label', schema['$defs']['element']['properties']['params']['properties'])
        d = island(prop())
        d['requirements'] = [{'id': 'r1', 'priority': 'must', 'kind': 'must_have', 'element_kind': 'prop_block'},
                             {'id': 'r2', 'priority': 'must', 'kind': 'count', 'element_kind': 'prop_block', 'count': 1}]
        _, report, _, _ = compile_(d)
        self.assertEqual(report['brief']['status'], 'satisfied', report['brief'])
        # the colour comes from the element's material, not from a new field
        el = prop(material={'color_hex': '#336699'})
        spec, _, _, _ = compile_(island(el))
        c = next(c for c in spec['components'] if c['id'] == 'laptop_one')
        self.assertEqual([round(v, 3) for v in c['material']['color'][:3]], [round(v / 255, 3) for v in (0x33, 0x66, 0x99)])


# ------------------------------------------------------------------ what the lit_panel and canopy_frame packages left behind
def arch_outline(a, zb, zt, r, n=12):
    """The open outline of an arch as the builder draws it (documented: each rounded corner in 12 segments): from (-a, zb) up the left
    side, over the two rounded top corners and down to (a, zb). Written out here again so the tests do not lean on motifs."""
    r = min(r, a, zt - zb)
    pts = [(-a, zb)]
    if r < 0.001:
        pts += [(-a, zt), (a, zt)]
    else:
        for s in (-1, 1):
            for k in range(n + 1):
                t = math.pi / 2 * k / n
                ang = math.pi - t if s < 0 else math.pi / 2 - t
                pts.append((s * (a - r) + r * math.cos(ang), zt - r + r * math.sin(ang)))
    pts.append((a, zb))
    return pts


def depth_inside(points, outline):
    """How far the points lie inside the convex arch outline (the closing bottom edge is not part of the path): the least distance to
    the lines of its edges, negative when a point is outside."""
    worst = math.inf
    for px, pz in points:
        for (x0, z0), (x1, z1) in zip(outline, outline[1:]):
            length = math.hypot(x1 - x0, z1 - z0)
            if length > 1e-12:
                worst = min(worst, ((px - x0) * (z1 - z0) - (pz - z0) * (x1 - x0)) / length)
    return worst


def niche_clearance(W, H, p, segments=12):
    """Least distance of the niche ring from the panel core's edge, the core drawn with `segments` per corner."""
    ci = p['inset_m'] + p['groove_width_m']
    n = p['niche']
    a, rw, bn = n['width_m'] / 2, n['ring_width_m'], n['bottom_m']
    ring = arch_outline(a + rw, bn - rw, bn + n['height_m'] + rw, n['corner_radius_m'] + rw)
    core = arch_outline(W / 2 - ci, bn - rw, H - ci, max(0.0, p['corner_radius_m'] - ci), segments)
    return depth_inside(ring, core)


# panels whose niche ring keeps 0.005 m from the exact arc of the core but not from the chords the core is built from
CHORD_CASES = (
    (2.0, 2.0, {'corner_radius_m': 0.565, 'inset_m': 0.088, 'groove_width_m': 0.077,
                'niche': {'width_m': 1.354, 'height_m': 1.373, 'bottom_m': 0.366, 'corner_radius_m': 0.171, 'ring_width_m': 0.073,
                          'depth_m': 0.1}}),
    (1.5, 2.4, {'corner_radius_m': 0.547, 'inset_m': 0.015, 'groove_width_m': 0.048,
                'niche': {'width_m': 1.004, 'height_m': 1.333, 'bottom_m': 0.89, 'corner_radius_m': 0.032, 'ring_width_m': 0.016,
                          'depth_m': 0.1}}),
    (2.4, 2.0, {'corner_radius_m': 1.07, 'inset_m': 0.083, 'groove_width_m': 0.077,
                'niche': {'width_m': 1.331, 'height_m': 1.232, 'bottom_m': 0.41, 'corner_radius_m': 0.152, 'ring_width_m': 0.073,
                          'depth_m': 0.1}}),
)


class LitPanelLeftovers(unittest.TestCase):
    """What the lit_panel package left for the next package of chain A: validation and build must agree."""

    def test_a_turned_lit_panel_that_does_not_fit_is_refused_at_validation(self):
        tight = (3.0, 1.5)

        def found(el):
            return dc.validate_design(island(el, footprint=tight))

        def refused(el):
            errors = found(el)
            self.assertTrue(any(e['code'] == 'PARAM' and 'size_m' in e['where'] for e in errors), (el.get('yaw_deg'), errors))
            return [e['message'] for e in errors if e['code'] == 'PARAM'][0]
        base = lit(size_m=[2.4, 0.15, 2.0])
        for yaw in (0.0, 30.0, -20.0, 180.0):
            with self.subTest(fits=yaw):
                el = dict(base, yaw_deg=yaw)
                self.assertEqual(found(el), [])
                spec, report, _, _ = compile_(island(el, footprint=tight))
                self.assertEqual(report['dropped'], [], yaw)
        for yaw in (45.0, 90.0, -60.0, 135.0, 225.0):
            with self.subTest(refused=yaw):
                refused(dict(base, yaw_deg=yaw))
        with self.assertRaises(dc.DesignError) as ctx:
            compile_(island(dict(base, yaw_deg=90.0), footprint=tight))
        self.assertEqual(ctx.exception.errors[0]['code'], 'PARAM')
        # against a side the turn also counts; with no turn given the side decides (the panel is cut to the side)
        refused(dict(base, side='back', centre_m=None, yaw_deg=90.0))
        el = dict(base, side='left', centre_m=None)
        self.assertEqual(found(el), [])
        _, report, _, _ = compile_(island(el, footprint=tight))
        self.assertEqual(report['dropped'], [])
        # the message gives the widest panel that fits, and that width is accepted while a little more is not
        message = refused(dict(base, yaw_deg=45.0))
        m = re.search(r'at most ([0-9.]+) m', message)
        self.assertTrue(m, message)
        widest = float(m.group(1))
        self.assertEqual(found(dict(base, size_m=[widest, 0.15, 2.0], yaw_deg=45.0)), [])
        _, report, _, _ = compile_(island(dict(base, size_m=[widest, 0.15, 2.0], yaw_deg=45.0), footprint=tight))
        self.assertEqual(report['dropped'], [])
        refused(dict(base, size_m=[widest + 0.01, 0.15, 2.0], yaw_deg=45.0))

    def test_lit_panel_niche_containment_follows_the_built_arc_chords(self):
        def found(W, H, params):
            return dc.validate_design(island(lit(size_m=[W, 0.15, H], params=params)))

        def niche_refused(errors):
            return any(e['code'] == 'PARAM' and 'niche' in e['where'] for e in errors)
        for W, H, params in CHORD_CASES:
            with self.subTest(W=W, H=H):
                self.assertGreaterEqual(niche_clearance(W, H, params, segments=4000), 0.005 - 1e-6, 'it clears the exact arc')
                self.assertLess(niche_clearance(W, H, params), 0.005, 'but not the chords it is built from')
                self.assertTrue(niche_refused(found(W, H, params)), found(W, H, params))
        # the limit is the chord clearance: sweep the niche width across it and compare with the independent measure
        W, H, params = CHORD_CASES[0]
        seen = {True: 0, False: 0}
        for step in range(-14, 15):
            width = round(1.354 + step * 0.0005, 4)
            p = dict(params, niche=dict(params['niche'], width_m=width))
            clearance = niche_clearance(W, H, p)
            if abs(clearance - 0.005) < 2e-6:
                continue
            with self.subTest(width=width):
                errors = found(W, H, p)
                self.assertEqual(not niche_refused(errors), clearance >= 0.005, (clearance, errors))
                seen[clearance >= 0.005] += 1
                if clearance >= 0.005 and step % 4 == 0:
                    _, report, _, _ = compile_(island(lit(size_m=[W, 0.15, H], params=p)))
                    self.assertEqual(report['dropped'], [])
        self.assertTrue(seen[True] >= 5 and seen[False] >= 5, seen)

    def test_valid_lit_panel_designs_with_a_height_build_without_drops(self):
        rng = random.Random(20261023)

        def number(lo, hi, digits=3):
            return round(rng.uniform(lo, hi), digits)

        def random_design():
            W, T, H = number(0.6, 3.5, 2), number(0.03, 0.4), number(0.15, 5.5, 2)
            params = {}
            if rng.random() < 0.7:
                params['corner_radius_m'] = number(0.0, W / 2 * 1.1)
            if rng.random() < 0.6:
                params['inset_m'] = number(0.003, 0.3)
            if rng.random() < 0.6:
                params['groove_width_m'] = number(0.003, 0.2)
            if rng.random() < 0.5:
                params['groove_depth_m'] = number(0.003, T)
            if rng.random() < 0.65:
                niche = {}
                for key, lo, hi in (('width_m', 0.03, W), ('height_m', 0.03, 2.0), ('bottom_m', 0.02, 2.0), ('depth_m', 0.003, T),
                                    ('corner_radius_m', 0.0, 0.8), ('ring_width_m', 0.003, 0.15)):
                    if rng.random() < 0.7:
                        niche[key] = number(lo, hi)
                params['niche'] = niche
            el = {'id': 'sweep', 'kind': 'lit_panel', 'size_m': [W, T, H], 'params': params}
            if rng.random() < 0.75:
                el.update(side='centre', centre_m=[number(-3.5, 3.5, 2), number(-2.5, 2.5, 2)])
                if rng.random() < 0.7:
                    el['yaw_deg'] = number(-180, 180, 1)
            else:
                el.update(side=rng.choice(['back', 'left', 'front', 'right']), along=number(0, 1, 2))
                if rng.random() < 0.4:
                    el['yaw_deg'] = number(-180, 180, 1)
            if rng.random() < 0.4:
                el['z_m'] = number(0.0, 1.5)
            footprint = (number(2.0, 8.0, 1), number(1.0, 6.0, 1))
            return island(el, footprint=footprint, height=number(2.0, 6.0, 1)), el
        valid = []
        for _ in range(900):
            d, el = random_design()
            if not dc.validate_design(d):
                valid.append((d, el))
        self.assertGreaterEqual(len(valid), 60, 'the sweep must reach valid designs')
        chosen = valid[:50]
        with_niche = sum(1 for _, el in chosen if 'niche' in el['params'])
        turned = sum(1 for _, el in chosen if el.get('yaw_deg') not in (None, 0.0))
        self.assertTrue(with_niche >= 10 and turned >= 10, (with_niche, turned))
        for d, el in chosen:
            with self.subTest(element=el):
                _, report, _, _ = compile_(d)
                self.assertEqual(report['dropped'], [], el)

    def test_groove_depth_description_is_per_kind(self):
        props = dc.load_schema()['$defs']['element']['properties']['params']['properties']
        text = props['groove_depth_m']['description']
        self.assertIn('lit_panel:', text, 'the lit_panel meaning is named')
        self.assertIn('kind', text.lower(), 'the description is read per kind')
        self.assertIn('component-recipes', text, 'other kinds that read the key explain it in the recipes')


# ------------------------------------------------------------------ validation and build must agree (layout and entrance included)
BOOTHS = (('inline', ['front']), ('inline', ['back']), ('inline', ['left']), ('corner', ['front', 'right']), ('corner', ['back', 'left']),
          ('peninsula', ['front', 'left', 'right']), ('peninsula', ['back', 'front', 'left']), ('island', list(SIDES4)))


def booth(kind, opened, footprint, *elements, entrance=None, wall=None, platform=None, height=4.5):
    """A design of the given booth type with the layout values the checks used to guess (None = not written, the compiler's default)."""
    d = design(booth_type=kind)
    d['form'] = dict(d['form'], footprint_m=list(footprint), height_limit_m=height, open_sides=list(opened), hanging_allowed=True)
    d['layout'] = dict(d['layout'], entrance_side=entrance, wall_thickness_m=wall, platform_height_m=platform)
    d['elements'] = [copy.deepcopy(e) for e in elements]
    d['requirements'] = []
    return d


def build_directly(d):
    """The compile report of a design that was never validated: what the build alone does with it."""
    comp = dc.DesignCompiler(copy.deepcopy(d), dc.canvas_png(320, 200), (320, 200))
    comp.build()
    return comp.report


class ValidationBuildAgreement(unittest.TestCase):
    def agree(self, d, expect=None, msg=''):
        """Validation passes and nothing is dropped, or validation refuses (PARAM) and the build alone could not make the element."""
        errors = dc.validate_design(d)
        ids = [e['id'] for e in d['elements']]
        if not errors:
            _, report, _, _ = compile_(d)
            self.assertEqual(report['dropped'], [], msg)
            ok = True
        else:
            self.assertEqual({e['code'] for e in errors}, {'PARAM'}, (msg, errors))
            dropped = {x['id'] for x in build_directly(d)['dropped']}
            self.assertTrue(dropped & set(ids), (msg, 'refused, yet the build makes it', errors))
            ok = False
        if expect is not None:
            self.assertEqual(ok, expect, (msg, errors))

    def test_a_centred_lit_panel_without_yaw_follows_the_real_entrance(self):
        el = {'id': 'p', 'kind': 'lit_panel', 'side': 'centre', 'centre_m': [0.0, 0.0], 'size_m': [3.0, 0.15, 2.4]}
        # front or back entrance faces the panel along X (3.0 m of 2.0): dropped at build, so refused here
        self.agree(booth('island', SIDES4, (2.0, 5.0), el, entrance='front'), False, 'island 2 x 5, entrance front')
        self.agree(booth('island', SIDES4, (2.0, 5.0), el, entrance='right'), True, 'island 2 x 5, entrance right: turned to fit')
        self.agree(booth('island', SIDES4, (2.0, 5.0), el), False, 'island 2 x 5, no entrance written: the compiler takes the first open side')
        self.agree(booth('corner', ['front', 'right'], (2.0, 5.0), el), False, 'corner front+right, default entrance front')
        self.agree(booth('corner', ['front', 'right'], (2.0, 5.0), el, entrance='right'), True, 'corner front+right, entrance right')
        self.agree(booth('island', SIDES4, (4.0, 2.0), dict(el, size_m=[2.6, 0.15, 2.4]), entrance='right'), False, 'island 4 x 2, entrance right')
        self.agree(booth('island', SIDES4, (4.0, 2.0), dict(el, size_m=[2.6, 0.15, 2.4]), entrance='front'), True, 'island 4 x 2, entrance front')

    def test_the_layout_wall_thickness_decides_the_cut_width(self):
        lit_el = {'id': 'p', 'kind': 'lit_panel', 'side': 'back', 'along': 0.5, 'size_m': [3.0, 0.15, 2.0], 'yaw_deg': 30.0}
        prop_el = {'id': 'p', 'kind': 'prop_block', 'side': 'back', 'along': 0.5, 'size_m': [3.0, 0.15, 0.5], 'yaw_deg': 30.0}
        for wall in (None, 0.01, 0.05, 0.15, 0.3, 0.5):
            for el in (lit_el, prop_el):
                self.agree(booth('peninsula', ['front', 'left', 'right'], (3.0, 1.5), el, wall=wall), None,
                           f'{el["kind"]}, back wall {wall}')
        # the cut width is 2.7 m at wall 0.15 and 2.0 m at wall 0.5: at 30 degrees both fit the room between the walls (1.55 and 1.2 m deep);
        # the uncut 3.0 m would need 1.63 m and not fit
        self.agree(booth('inline', ['front'], (3.0, 1.7), lit_el, wall=0.15), True, 'lit wall 0.15')
        self.agree(booth('inline', ['front'], (3.0, 1.7), lit_el, wall=0.5), True, 'lit wall 0.5')
        self.agree(booth('inline', ['front'], (3.0, 1.7), prop_el, wall=0.15), True, 'prop wall 0.15')
        self.agree(booth('inline', ['front'], (3.0, 1.7), prop_el, wall=0.5), True, 'prop wall 0.5')
        # the other way: a thin wall leaves a longer side, and the turned block no longer fits
        blk = {'id': 'p', 'kind': 'prop_block', 'side': 'right', 'along': 0.5, 'size_m': [5.4, 1.04, 0.5], 'yaw_deg': 86.6}
        for wall in (None, 0.01, 0.03, 0.05, 0.1, 0.3):
            self.agree(booth('inline', ['left'], (3.1, 1.9), blk, wall=wall), None, f'block against the right side, wall {wall}')
        # the right side's room is bounded by the inner faces of the front, back and right walls: 1.9 less two walls deep, and the turned
        # block needs 1.94 m (wall 0.01) or 1.90 m (wall 0.03) of it, so neither fits; a shorter block fits
        self.agree(booth('inline', ['left'], (3.1, 1.9), blk, wall=0.01), False, 'block, wall 0.01')
        self.agree(booth('inline', ['left'], (3.1, 1.9), blk, wall=0.03), False, 'block, wall 0.03')
        self.agree(booth('inline', ['left'], (3.1, 2.1), dict(blk, size_m=[1.5, 1.04, 0.5]), wall=0.03), True, 'shorter block, wall 0.03')

    def test_a_panel_against_a_side_and_wider_than_the_footprint_is_cut_not_refused(self):
        el = {'id': 'p', 'kind': 'lit_panel', 'side': 'back', 'along': 0.5, 'size_m': [5.0, 0.15, 2.4], 'yaw_deg': 90.0}
        for wall in (0.03, 0.1):
            self.agree(booth('inline', ['front'], (4.0, 6.0), el, wall=wall), True, f'wall {wall}')
            # the cut width (3.94 or 3.8 m) turned 90 degrees must still fit between the back wall and the open front
            self.agree(booth('inline', ['front'], (4.0, 3.5), el, wall=wall), False, f'too shallow, wall {wall}')
        # in the middle of the booth the width is not cut: still refused
        self.agree(booth('island', SIDES4, (4.0, 3.95), dict(el, side='centre', centre_m=[0.0, 0.0])), False, 'centre')

    def test_a_panel_wider_than_the_footprint_fits_when_turned_across_it(self):
        el = {'id': 'p', 'kind': 'lit_panel', 'side': 'left', 'centre_m': [1.81, -0.14], 'size_m': [4.21, 0.346, 3.97], 'yaw_deg': -30.7}
        self.agree(booth('inline', ['left'], (3.8, 4.1), el), True, 'turned across the diagonal')
        self.agree(booth('inline', ['left'], (3.8, 4.1), dict(el, yaw_deg=0.0)), False, 'not turned')

    def test_a_turned_block_near_the_edge_is_moved_once_from_its_turned_outline(self):
        el = {'id': 'p', 'kind': 'prop_block', 'side': 'centre', 'centre_m': [3.3, 0.0], 'size_m': [2.0, 0.2, 0.5], 'yaw_deg': 45.0}
        for kind in ('prop_block', 'lit_panel'):
            with self.subTest(kind=kind):
                e = dict(el, kind=kind, size_m=[2.0, 0.2, 0.5 if kind == 'prop_block' else 2.0])
                spec, report, _, _ = compile_(island(e))
                self.assertEqual(report['dropped'], [])
                half = (math.cos(math.radians(45)) * 2.0 + math.sin(math.radians(45)) * 0.2) / 2
                hi = [max(world_bounds(c)[1][0] for c in spec['components'] if c.get('group') == 'p')]
                self.assertAlmostEqual(hi[0], 4.0, places=3, msg='the turned outline touches the edge, it is not pushed further in')
                moves = [a for a in report['adjustments'] if a['id'] == 'p']
                self.assertEqual(len(moves), 1, moves)
                self.assertIn(f'to ({4.0 - half:.2f}, 0.00)', moves[0]['what'])
                self.assertIn('from (3.30, 0.00)', moves[0]['what'], 'the record starts where the part asked to stand')
        # a block that is not near the edge is not moved and records nothing
        _, report, _, _ = compile_(island(dict(el, centre_m=[1.0, 0.0])))
        self.assertEqual([a for a in report['adjustments'] if a['id'] == 'p'], [])

    def test_validation_and_build_agree_across_booth_types_entrances_and_walls(self):
        walls = (None, 0.01, 0.03, 0.05, 0.1, 0.15, 0.3, 0.5)
        counts = {'valid': 0, 'refused': 0}
        per_booth = {}
        for seed in (1, 2, 3):
            rng = random.Random(seed)

            def number(lo, hi, digits=3):
                return round(rng.uniform(lo, hi), digits)

            for _ in range(70):
                kind, opened = rng.choice(BOOTHS)
                footprint = (number(2.0, 8.0, 1), number(1.5, 6.0, 1))
                kind_of_element = rng.choice(['lit_panel', 'prop_block'])
                W, T = (number(0.5, 4.5, 2), number(0.04, 0.4)) if kind_of_element == 'lit_panel' else (number(0.05, 6.0, 2), number(0.05, 2.0, 2))
                H = number(0.3, 5.5, 2) if kind_of_element == 'lit_panel' else rng.choice([number(0.001, 0.02, 4), number(0.05, 3.0, 2)])   # a phone lying flat is a few mm thick
                el = {'id': 'sweep', 'kind': kind_of_element, 'size_m': [W, T, H]}
                if kind_of_element == 'lit_panel':
                    params = {}
                    if rng.random() < 0.4:
                        params['corner_radius_m'] = number(0.0, W / 2 * 1.05)
                    if rng.random() < 0.4:
                        params['niche'] = {k: number(lo, hi) for k, lo, hi in (('width_m', 0.1, W), ('height_m', 0.1, 1.5), ('bottom_m', 0.1, 1.5))
                                           if rng.random() < 0.8}
                    el['params'] = params
                else:
                    el['params'] = {'label': rng.choice(PROP_LABELS)}
                    if rng.random() < 0.6:
                        el['z_m'] = number(0.0, 1.6)
                roll = rng.random()
                if roll < 0.55:
                    el.update(side='centre', centre_m=[number(-2.5, 2.5, 2), number(-2.0, 2.0, 2)])
                elif roll < 0.65:
                    el.update(side='centre', along=number(0, 1, 2))
                else:
                    el.update(side=rng.choice(SIDES4), along=number(0, 1, 2))
                    if rng.random() < 0.2:
                        el['centre_m'] = [number(-2.0, 2.0, 2), number(-1.5, 1.5, 2)]
                if rng.random() < 0.55:
                    el['yaw_deg'] = number(-180, 180, 1)
                d = booth(kind, opened, footprint, el, entrance=rng.choice(opened + [None]), wall=rng.choice(walls),
                          platform=rng.choice([None, 0.0, 0.15, 0.33]), height=number(2.0, 6.0, 1))
                msg = (seed, kind, opened, footprint, d['layout'], el)
                errors = dc.validate_design(d)
                if errors:
                    self.assertEqual({e['code'] for e in errors}, {'PARAM'}, (msg, errors))
                    self.assertTrue(build_directly(d)['dropped'], (msg, 'refused, yet the build makes it', errors))
                    counts['refused'] += 1
                else:
                    _, report, _, _ = compile_(d)
                    self.assertEqual(report['dropped'], [], msg)
                    counts['valid'] += 1
                    per_booth[kind] = per_booth.get(kind, 0) + 1
        self.assertGreaterEqual(counts['valid'], 60, counts)
        self.assertGreaterEqual(counts['refused'], 25, counts)
        self.assertEqual(set(per_booth), {'inline', 'corner', 'peninsula', 'island'}, per_booth)


MOVE = re.compile(r'moved inside the footprint from \((-?[0-9.]+), (-?[0-9.]+)\) to \((-?[0-9.]+), (-?[0-9.]+)\)')


def centre_of(spec, eid):
    """Centre in plan of the bounds of all parts of an element."""
    bounds = [world_bounds(c) for c in spec['components'] if c.get('group') == eid]
    return ((min(b[0][0] for b in bounds) + max(b[1][0] for b in bounds)) / 2, (min(b[0][1] for b in bounds) + max(b[1][1] for b in bounds)) / 2)


def moves_of(report, eid):
    return [a['what'] for a in report['adjustments'] if a['id'] == eid and a['what'].startswith('moved inside')]


class SelfPlacingParts(unittest.TestCase):
    """A lit_panel and a prop_block place themselves: one move into the footprint, from where they asked to stand, recorded once."""

    def check_one_move(self, d, eid, asked, msg=''):
        spec, report, _, _ = compile_(d)
        self.assertEqual(report['dropped'], [], msg)
        got = centre_of(spec, eid)
        moves = moves_of(report, eid)
        self.assertEqual(len(moves), 1, (msg, moves, got))
        x0, y0, x1, y1 = (float(v) for v in MOVE.search(moves[0]).groups())
        self.assertAlmostEqual(x0, asked[0], delta=0.006, msg=msg)
        self.assertAlmostEqual(y0, asked[1], delta=0.006, msg=msg)
        self.assertAlmostEqual(x1, got[0], delta=0.006, msg=msg)
        self.assertAlmostEqual(y1, got[1], delta=0.006, msg=msg)
        self.assertGreater(math.hypot(x1 - x0, y1 - y0), 0.005)

    def test_a_part_placed_against_a_side_and_pushed_out_records_its_move_once(self):
        # inline 6 x 4 open at the front, the part against the back wall asks for 5 m off it (the wall's inner face is at y = 1.9)
        for kind, size in (('lit_panel', [2.0, 0.15, 2.4]), ('prop_block', [0.4, 0.3, 0.4])):
            for yaw in (None, 30.0):
                el = {'id': 'p', 'kind': kind, 'side': 'back', 'along': 0.5, 'offset_m': 5.0, 'size_m': size}
                if yaw is not None:
                    el['yaw_deg'] = yaw
                self.check_one_move(booth('inline', ['front'], (6.0, 4.0), el), 'p', (0.0, 1.9 - 5.0 - size[1] / 2), f'{kind} yaw {yaw}')
        # inline 4 x 2: a deep part against the front, 1.8 m off it
        for kind, size, off in (('lit_panel', [2.0, 0.4, 2.0], 1.8), ('prop_block', [1.0, 1.9, 0.5], 0.3)):
            el = {'id': 'p', 'kind': kind, 'side': 'front', 'along': 0.5, 'offset_m': off, 'size_m': size}
            self.check_one_move(island(el, footprint=(4.0, 2.0)), 'p', (0.0, -1.0 + off + size[1] / 2), kind)

    def test_a_part_placed_at_the_centre_records_its_move_once_from_the_asked_position(self):
        for kind, size in (('lit_panel', [2.0, 0.2, 2.0]), ('prop_block', [2.0, 0.2, 0.5])):
            for yaw in (None, 0.0, 30.0, 45.0):
                with self.subTest(kind=kind, yaw=yaw):
                    el = {'id': 'p', 'kind': kind, 'side': 'centre', 'centre_m': [3.6, 1.0], 'size_m': size}
                    if yaw is not None:
                        el['yaw_deg'] = yaw
                    self.check_one_move(island(el), 'p', (3.6, 1.0), f'{kind} yaw {yaw}')
            with self.subTest(kind=kind, along=0.99):
                el = {'id': 'p', 'kind': kind, 'side': 'centre', 'along': 0.99, 'size_m': size, 'yaw_deg': 30.0}
                self.check_one_move(island(el), 'p', (-4.0 + 0.99 * 8.0, 0.0), f'{kind} along')

    def test_a_part_that_does_not_move_records_nothing_and_the_old_kinds_are_not_touched(self):
        for kind, size in (('lit_panel', [2.0, 0.2, 2.0]), ('prop_block', [2.0, 0.2, 0.5])):
            el = {'id': 'p', 'kind': kind, 'side': 'back', 'along': 0.5, 'offset_m': 0.3, 'size_m': size}
            _, report, _, _ = compile_(island(el))
            self.assertEqual(moves_of(report, 'p'), [], kind)
        self.assertEqual(sorted(mt.SELF_PLACING_KINDS), ['lit_panel', 'prop_block'])
        # a kind that is not self-placing is still moved and recorded by the compiler
        _, report, _, _ = compile_(island({'id': 'p', 'kind': 'box', 'side': 'back', 'along': 0.5, 'offset_m': 9.0, 'size_m': [0.5, 0.5, 0.5]}))
        self.assertEqual(len(moves_of(report, 'p')), 1)

    def check_side_case(self, el, fw, fd, seen, kind='island', opened=SIDES4, wall=None):
        """A part placed against a side must fit, by its turned outline, inside the room bounded by the inner faces of the closed walls
        (an open side: the footprint edge). If it does not, validation refuses it and the build alone drops it. If it does, it stands
        where the side and its along/offset put it, moved once into the room from that asked position, and that move is recorded once."""
        d = booth(kind, opened, (fw, fd), el, wall=wall)
        t = 0.1 if wall is None else wall
        closed = [q for q in SIDES4 if q not in opened]

        def thick(q):
            return t if q in closed else 0.0
        x0, x1 = -fw / 2 + thick('left'), fw / 2 - thick('right')
        y0, y1 = -fd / 2 + thick('front'), fd / 2 - thick('back')
        side, W, T = el['side'], el['size_m'][0], el['size_m'][1]
        lo, hi = (x0, x1) if side in ('front', 'back') else (y0, y1)
        w = min(W, hi - lo)
        u = lo + el['along'] * (hi - lo)
        off = el['offset_m'] + T / 2
        asked = {'front': (u, y0 + off), 'back': (u, y1 - off), 'left': (x0 + off, u), 'right': (x1 - off, u)}[side]
        yaw = el.get('yaw_deg', {'front': 0.0, 'back': 180.0, 'left': 90.0, 'right': -90.0}[side])
        ex, ey = (v / 2 for v in mt._turned_extent(w, T, yaw))
        errors = dc.validate_design(d)
        if 2 * ex > x1 - x0 + 1e-6 or 2 * ey > y1 - y0 + 1e-6:
            seen['refused'] = seen.get('refused', 0) + 1
            if abs(2 * ex - (x1 - x0)) > 1e-4 and abs(2 * ey - (y1 - y0)) > 1e-4:       # not on the edge of the tolerance
                self.assertTrue(errors and {e['code'] for e in errors} == {'PARAM'}, (el, kind, wall, 'does not fit the room, yet valid'))
                self.assertTrue(build_directly(d)['dropped'], (el, kind, wall, 'refused, yet the build makes it'))
            return
        self.assertEqual(errors, [], (el, kind, wall))
        spec, report, _, _ = compile_(d)
        self.assertEqual(report['dropped'], [], el)
        want = (min(max(asked[0], x0 + ex), x1 - ex), min(max(asked[1], y0 + ey), y1 - ey))
        got = centre_of(spec, 'p')
        self.assertAlmostEqual(got[0], want[0], delta=0.01, msg=str(el))
        self.assertAlmostEqual(got[1], want[1], delta=0.01, msg=str(el))
        self.assertEqual([w_ for w_ in report['warnings'] if 'overlap' in w_], [], (el, kind, wall))
        bounds = [world_bounds(c) for c in spec['components'] if c.get('group') == 'p' and c['kind'] != 'text']
        self.assertGreaterEqual(min(b_[0][0] for b_ in bounds), x0 - 0.002, el)
        self.assertLessEqual(max(b_[1][0] for b_ in bounds), x1 + 0.002, el)
        self.assertGreaterEqual(min(b_[0][1] for b_ in bounds), y0 - 0.002, el)
        self.assertLessEqual(max(b_[1][1] for b_ in bounds), y1 + 0.002, el)
        moves = moves_of(report, 'p')
        if math.hypot(want[0] - asked[0], want[1] - asked[1]) > 0.005:
            seen['moved'] += 1
            self.assertEqual(len(moves), 1, (el, moves))
            mx0, my0, mx1, my1 = (float(v) for v in MOVE.search(moves[0]).groups())
            self.assertAlmostEqual(mx0, asked[0], delta=0.006, msg=str(el))
            self.assertAlmostEqual(my0, asked[1], delta=0.006, msg=str(el))
            self.assertAlmostEqual(mx1, got[0], delta=0.006, msg=str(el))
            self.assertAlmostEqual(my1, got[1], delta=0.006, msg=str(el))
        else:
            seen['still'] += 1
            self.assertEqual(moves, [], el)

    def test_every_actual_move_of_a_side_placed_part_has_exactly_one_record(self):
        rng = random.Random(77)
        seen = {'moved': 0, 'still': 0}
        for _ in range(160):
            kind = rng.choice(['lit_panel', 'prop_block'])
            fw, fd = round(rng.uniform(3.0, 8.0), 1), round(rng.uniform(2.0, 6.0), 1)
            W = round(rng.uniform(0.3, 2.5), 2)
            T = round(rng.uniform(0.05 if kind == 'prop_block' else 0.06, 1.2 if kind == 'prop_block' else 0.4), 2)
            el = {'id': 'p', 'kind': kind, 'side': rng.choice(SIDES4), 'along': round(rng.random(), 2),
                  'offset_m': round(rng.uniform(0, 6.0), 2), 'size_m': [W, T, 0.5 if kind == 'prop_block' else 2.0]}
            if rng.random() < 0.5:
                el['yaw_deg'] = round(rng.uniform(-180, 180), 1)
            self.check_side_case(el, fw, fd, seen)
        self.assertTrue(seen['moved'] >= 15 and seen['still'] >= 15, seen)

    def test_a_turned_part_against_a_side_is_not_pushed_in_by_its_unturned_width_first(self):
        # inline 6 x 4 open at the front; the back wall's inner face is at y = 1.9 and the end walls' inner faces at x = +-2.9.
        # along 1.0 asks for x = 2.9. Turned 90 degrees the part is only 0.2 m wide in X: it stops at x = 2.8, flush with the end wall's inner face at 2.9 (and is moved in Y, where it
        # is 2.0 m long: from y = 1.8 to 0.9, flush with the back wall's inner face at 1.9), and the one record starts at the asked position.
        for kind, height in (('prop_block', 0.5), ('lit_panel', 2.0)):
            el = {'id': 'p', 'kind': kind, 'side': 'back', 'along': 1.0, 'offset_m': 0.0, 'size_m': [2.0, 0.2, height], 'yaw_deg': 90.0}
            spec, report, _, _ = compile_(booth('inline', ['front'], (6.0, 4.0), el))
            self.assertEqual(report['dropped'], [])
            self.assertAlmostEqual(centre_of(spec, 'p')[0], 2.8, delta=0.005, msg=kind)
            moves = moves_of(report, 'p')
            self.assertEqual(len(moves), 1, (kind, moves))
            x0, y0, x1, y1 = (float(v) for v in MOVE.search(moves[0]).groups())
            self.assertEqual((round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)), (2.9, 1.8, 2.8, 0.9), kind)
            # turned 30 degrees it needs 0.916 m each way from its centre: one move, from the asked x = 2.9
            el['yaw_deg'] = 30.0
            spec, report, _, _ = compile_(booth('inline', ['front'], (6.0, 4.0), el))
            self.assertEqual(report['dropped'], [])
            moves = moves_of(report, 'p')
            self.assertEqual(len(moves), 1, (kind, moves))
            x0, y0, x1, y1 = (float(v) for v in MOVE.search(moves[0]).groups())
            self.assertAlmostEqual(x0, 2.9, delta=0.006)
            self.assertAlmostEqual(x1, centre_of(spec, 'p')[0], delta=0.006)
            self.assertAlmostEqual(x1, 2.9 - (math.cos(math.radians(30)) * 2.0 + math.sin(math.radians(30)) * 0.2) / 2, delta=0.006)

    def test_side_placed_parts_with_along_at_the_ends_and_several_turns_record_one_move_from_the_asked_position(self):
        # all four sides, along at both ends and in the middle, turns from none to 120 degrees, booths with closed end walls and an island,
        # sizes from a small part to one longer than the room between the end walls and one deep enough to cross its own wall when turned
        sizes = ((1.6, 0.2), (1.6, 0.2), (3.0, 1.8), (5.7, 0.6), (7.0, 0.8))
        combos = [(kind, side, along, yaw, size) for kind in ('prop_block', 'lit_panel') for side in SIDES4 for along in (0.0, 0.02, 0.5, 0.98, 1.0)
                  for yaw in (None, 0.0, 30.0, 45.0, 90.0, -90.0, 120.0) for size in sizes]
        random.Random(4).shuffle(combos)
        booths = (('inline', ['front'], (6.0, 4.0), None), ('corner', ['front', 'right'], (6.0, 4.0), 0.15),
                  ('peninsula', ['front', 'left', 'right'], (5.0, 3.5), 0.3), ('island', list(SIDES4), (6.0, 4.0), None),
                  ('inline', ['back'], (4.0, 3.0), 0.05))
        seen = {'moved': 0, 'still': 0, 'refused': 0}
        for n, (kind, side, along, yaw, (sw, st)) in enumerate(combos[:200]):
            btype, opened, (fw, fd), wall = booths[n % len(booths)]
            el = {'id': 'p', 'kind': kind, 'side': side, 'along': along, 'offset_m': 0.2, 'size_m': [sw, st, 0.5 if kind == 'prop_block' else 2.0]}
            if yaw is not None:
                el['yaw_deg'] = yaw
            self.check_side_case(el, fw, fd, seen, btype, opened, wall)
        # 200 fixed picks reach 152 moves, 15 parts that stay where they were asked (only along 0.5 can, and only when the turn and the
        # offset leave the outline in the room) and 33 parts that do not fit their room and are refused. The bars are what the picks reach.
        self.assertTrue(seen['moved'] >= 100 and seen['still'] >= 10 and seen['refused'] >= 20, seen)

    def test_a_turned_part_longer_than_its_room_is_refused_and_dropped_not_centred_into_the_walls(self):
        def refused(d, msg):
            errors = dc.validate_design(d)
            self.assertTrue(errors and {e['code'] for e in errors} == {'PARAM'}, (msg, errors))
            self.assertIn('size_m', errors[0]['where'])
            dropped = build_directly(d)['dropped']
            self.assertEqual([x['id'] for x in dropped], ['p'], (msg, dropped))
            self.assertTrue(dropped[0]['reason'], msg)
            return errors[0]['message']

        def el(kind, side, along, size, yaw):
            return {'id': 'p', 'kind': kind, 'side': side, 'along': along, 'size_m': size, 'yaw_deg': yaw}
        refused(booth('inline', ['front'], (6.0, 4.0), el('prop_block', 'back', 0.5, [7.0, 0.8, 0.5], 10.0)), 'prop 7.0 yaw 10')
        refused(booth('inline', ['front'], (6.0, 4.0), el('lit_panel', 'back', 0.5, [6.5, 0.4, 2.0], 5.0)), 'lit 6.5 yaw 5')
        for along in (0.5, 1.0):
            refused(booth('inline', ['front'], (6.0, 4.0), el('prop_block', 'back', along, [6.0, 1.5, 0.5], 12.0)), f'prop 6.0 yaw 12 along {along}')
        for along in (0.5, 0.0):
            refused(booth('peninsula', ['front', 'left', 'right'], (5.0, 3.5), el('prop_block', 'left', along, [3.5, 1.0, 0.5], 80.0), wall=0.3),
                    f'peninsula left along {along}')
        refused(booth('corner', ['front', 'right'], (6.0, 4.0), el('lit_panel', 'back', 0.98, [6.0, 0.6, 2.0], 6.0), wall=0.15), 'corner back')
        refused(booth('inline', ['back'], (3.0, 3.0), el('prop_block', 'left', 0.5, [2.7, 1.3, 0.5], 60.0), wall=0.3), 'inline back open, left')
        # the message gives the widest part that fits, and that width is accepted, stands flush inside the walls and is built
        message = refused(booth('inline', ['front'], (6.0, 4.0), el('prop_block', 'back', 0.5, [7.0, 0.8, 0.5], 10.0)), 'message')
        widest = float(re.search(r'at most ([0-9.]+) m', message).group(1))
        d = booth('inline', ['front'], (6.0, 4.0), el('prop_block', 'back', 0.5, [widest, 0.8, 0.5], 10.0))
        self.assertEqual(dc.validate_design(d), [])
        spec, report, _, _ = compile_(d)
        self.assertEqual(report['dropped'], [])
        self.assertEqual([w for w in report['warnings'] if 'overlap' in w], [])
        lo, hi = world_bounds(next(c for c in spec['components'] if c['id'] == 'p'))
        self.assertGreaterEqual(lo[0], -2.9 - 0.002)
        self.assertLessEqual(hi[0], 2.9 + 0.002)

    def test_a_turned_part_against_a_side_stays_inside_the_inner_face_of_its_own_wall(self):
        # inline 6 x 4 open at the front: the back wall's inner face is at y = 1.9
        for kind, size in (('prop_block', [3.0, 0.8, 0.5]), ('lit_panel', [3.0, 0.4, 2.0])):
            for yaw in (5.0, 90.0, 30.0):
                with self.subTest(kind=kind, yaw=yaw):
                    el = {'id': 'p', 'kind': kind, 'side': 'back', 'along': 0.5, 'offset_m': 0.0, 'size_m': size, 'yaw_deg': yaw}
                    spec, report, _, _ = compile_(booth('inline', ['front'], (6.0, 4.0), el))
                    self.assertEqual(report['dropped'], [])
                    self.assertEqual([w for w in report['warnings'] if 'overlap' in w], [], report['warnings'])
                    hi = max(world_bounds(c)[1][1] for c in spec['components'] if c.get('group') == 'p' and c['kind'] != 'text')
                    self.assertLessEqual(hi, 1.9 + 0.002)
                    moves = moves_of(report, 'p')
                    self.assertEqual(len(moves), 1, moves)
                    x0, y0, x1, y1 = (float(v) for v in MOVE.search(moves[0]).groups())
                    self.assertAlmostEqual(y0, 1.9 - size[1] / 2, delta=0.006)
                    self.assertAlmostEqual(y1, centre_of(spec, 'p')[1], delta=0.006)

    def test_a_self_placing_part_ends_flush_with_a_closed_end_wall_like_a_box(self):
        # inline 6 x 4 open at the front, walls 0.10 thick: the usable length of the back side is x = -2.9 .. 2.9
        for along, size in ((0.0, [2.0, 0.15, 2.0]), (0.02, [0.4, 0.3, 0.4]), (1.0, [2.0, 0.2, 0.5])):
            cases = {}
            for kind in ('box', 'lit_panel', 'prop_block'):
                el = {'id': 'p', 'kind': kind, 'side': 'back', 'along': along, 'offset_m': 0.0, 'size_m': list(size)}
                spec, report, _, _ = compile_(booth('inline', ['front'], (6.0, 4.0), el))
                self.assertEqual(report['dropped'], [])
                self.assertEqual([w for w in report['warnings'] if 'overlap' in w], [], (kind, along, report['warnings']))
                bounds = [world_bounds(c) for c in spec['components'] if c.get('group') == 'p' and c['kind'] != 'text']
                cases[kind] = (min(b[0][0] for b in bounds), max(b[1][0] for b in bounds))
            self.assertAlmostEqual(cases['lit_panel'][0], cases['box'][0], delta=0.002, msg=str(along))
            self.assertAlmostEqual(cases['lit_panel'][1], cases['box'][1], delta=0.002, msg=str(along))
            self.assertAlmostEqual(cases['prop_block'][0], cases['box'][0], delta=0.002, msg=str(along))
            self.assertAlmostEqual(cases['prop_block'][1], cases['box'][1], delta=0.002, msg=str(along))
            self.assertGreaterEqual(min(cases['box'][0], cases['lit_panel'][0], cases['prop_block'][0]), -2.9 - 0.002)
            self.assertLessEqual(max(cases['box'][1], cases['lit_panel'][1], cases['prop_block'][1]), 2.9 + 0.002)
        # the first repro: a 2.0 m panel with along 0.0 stands at x -2.9 .. -0.9 and its one record starts at the asked centre x = -2.9
        el = {'id': 'p', 'kind': 'lit_panel', 'side': 'back', 'along': 0.0, 'size_m': [2.0, 0.15, 2.0]}
        spec, report, _, _ = compile_(booth('inline', ['front'], (6.0, 4.0), el))
        moves = moves_of(report, 'p')
        self.assertEqual(len(moves), 1, moves)
        x0, y0, x1, y1 = (float(v) for v in MOVE.search(moves[0]).groups())
        self.assertEqual((round(x0, 2), round(x1, 2)), (-2.9, -1.9))
        # a part next to a closed end wall on the left side of a booth open at the back: it stops at the front wall's inner face
        el = {'id': 'p', 'kind': 'lit_panel', 'side': 'left', 'along': 0.39, 'size_m': [2.33, 0.15, 2.0]}
        spec, report, _, _ = compile_(booth('inline', ['back'], (5.0, 2.25), el))
        self.assertEqual(report['dropped'], [])
        self.assertEqual([w for w in report['warnings'] if 'overlap' in w], [], report['warnings'])

    def test_thin_blocks_build_when_they_have_room_above_their_base(self):
        # a phone or a tablet lying flat is a few millimetres thick; what must be left is 0.01 m between the base and the height limit
        for height in (0.001, 0.002, 0.005, 0.008, 0.0099, 0.01, 0.02):
            for name, d in (('template', None), ('island', island(prop(size_m=[0.25, 0.17, height], z_m=0.75, params={'label': 'tablet'})))):
                with self.subTest(height=height, booth=name):
                    if d is None:
                        d = design()
                        d['elements'] = [prop(size_m=[0.075, 0.155, height], z_m=0.75, params={'label': 'phone'}, centre_m=[0.5, 0.0])]
                        d['requirements'] = []
                    self.assertEqual(dc.validate_design(d), [])
                    spec, report, _, _ = compile_(d)
                    self.assertEqual(report['dropped'], [])
                    c = next(c for c in spec['components'] if c['id'] == 'laptop_one')
                    lo, hi = world_bounds(c)
                    self.assertAlmostEqual(hi[2] - lo[2], height, places=4)
                    self.assertTrue(validate(spec, source_size=[320, 200])['valid'])
        # the room above the base is what counts, not the block: 0.005 m of room is refused by validation and by the builder
        el = prop(size_m=[0.25, 0.17, 0.5], z_m=4.395)
        self.assertTrue(dc.validate_design(island(el)))
        self.assertTrue(build_directly(island(el))['dropped'])

    def test_a_prop_block_with_a_negative_z_or_a_tiny_size_is_dropped_by_the_builder_too(self):
        for what, el in (('z_m', prop(z_m=-0.5)), ('size', prop(size_m=[0.0, 0.25, 0.22])), ('size', prop(size_m=[0.36, 0.25, 0.0005])),
                         ('size', prop(size_m=[-0.3, 0.25, 0.22]))):
            with self.subTest(what=what, el=el):
                self.assertTrue(dc.validate_design(island(el)), 'validation refuses it')
                dropped = build_directly(island(el))['dropped']
                self.assertEqual([x['id'] for x in dropped], ['laptop_one'], dropped)
                self.assertIn(what, dropped[0]['reason'])


@unittest.skipUnless(os.environ.get('BLENDER_EXE'), 'Set BLENDER_EXE for the native design build')
class NativeDesignBuild(unittest.TestCase):
    def test_design_builds_renders_and_reopens(self):
        with tempfile.TemporaryDirectory() as tmp:
            dc.compile_design(TEMPLATE, tmp, (96, 60))
            spec, out = Path(tmp) / 'spec.json', Path(tmp) / 'final'
            blender = [os.environ['BLENDER_EXE'], '--background', '--factory-startup', '--disable-autoexec', '--python-exit-code', '1',
                       '--python']
            r = subprocess.run(blender + [str(ROOT / 'scripts' / 'blender_build.py'), '--', '--spec', str(spec), '--out', str(out),
                                          '--render', '--samples', '2'], capture_output=True, text=True, timeout=900)
            self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-1500:])
            self.assertTrue((out / 'client_render.png').is_file())
            r = subprocess.run(blender + [str(ROOT / 'scripts' / 'blender_verify.py'), '--', '--spec', str(spec), '--blend',
                                          str(out / 'booth.blend'), '--report', str(out / 'verify.json')],
                               capture_output=True, text=True, timeout=600)
            self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-1500:])
            report = json.loads((out / 'verify.json').read_text(encoding='utf-8'))
            self.assertTrue(report['geometry_verified'], report.get('errors'))


if __name__ == '__main__':
    unittest.main()
