"""Advisory design checks: one positive and one negative case per check, plus input discipline."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import components as cm
from design_check import run, check_context

CAM = {'projection': 'perspective', 'position': [7.0, -9.0, 5.0], 'target': [0.0, 0.0, 1.0], 'lens_mm': 42.0,
       'sensor_width_mm': 36.0, 'principal_point': [0.5, 0.5], 'pixel_aspect': [1, 1], 'fit_status': 'unfitted'}
ZF = 0.1
FULL = {'height_limit_m': 4.0, 'hanging_allowed': False, 'open_sides': ['front'], 'led_module_m': 0.5}


def base(folder):
    src = Path(folder) / 'source.png'
    Image.new('RGB', (64, 48), (120, 120, 120)).save(src)
    b = cm.SpecBuilder(str(src), 64, 48, 'design_check_fixture')
    b.scope('s', 'Synthetic fixture.')
    b.box('floor', 's', 'floor', (-3, 3), (-2, 2), (0, ZF), cm.WOOD, critical=True, px=(0, 20, 64, 48))
    b.box('wall', 's', 'back_wall', (-3, 3), (1.9, 2.0), (ZF, 3.0), cm.WHITE, px=(0, 0, 64, 30))
    b.scale((6, 4), 'Synthetic fixture.')
    b.camera(CAM)
    b.uncertain('fixture', 'Synthetic only.')
    return b


def checks(b, ctx=None, level=None):
    result = run(b.build(), FULL if ctx is None else ctx)
    return result, {f['check'] for f in result['findings'] if level in (None, f['level'])}


class DesignCheckTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.b = base(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    # ---- input discipline
    def test_unknown_or_badly_typed_context_is_refused(self):
        with self.assertRaises(ValueError):
            check_context({'height': 4})
        with self.assertRaises(ValueError):
            check_context({'height_limit_m': -1})
        with self.assertRaises(ValueError):
            check_context({'hanging_allowed': 'no'})
        with self.assertRaises(ValueError):
            check_context({'open_sides': ['top']})
        self.assertEqual(check_context(None), {})

    def test_invalid_spec_is_refused(self):
        spec = self.b.build()
        spec['components'][0]['kind'] = 'spline'
        with self.assertRaises(ValueError):
            run(spec, FULL)

    def test_missing_context_is_not_checked_rather_than_assumed(self):
        self.b.box('over', 's', 'wall_panel', (0, 1), (0, 1), (ZF, 9.0), cm.WHITE, px=(1, 1, 5, 5))
        cm.counter(self.b, 'c', 's', (-1, 0.5), (-1.5, -0.95), floor_z=ZF, px=(1, 1, 5, 5))
        self.b.box('led_wall', 's', 'led_wall', (1, 2), (1.8, 1.9), (0.5, 1.5), cm.DARK_SCREEN, px=(1, 1, 5, 5))
        result = run(self.b.build(), {})
        names = {n['check'] for n in result['not_checked']}
        self.assertEqual(names, {'below_height_limit', 'counter_edge_clearance', 'led_whole_modules'})
        self.assertNotIn('below_height_limit', {f['check'] for f in result['findings']})

    def test_wording_never_claims_compliance(self):
        self.b.box('over', 's', 'wall_panel', (2.9, 3.4), (0, 1), (ZF, 5.0), cm.WHITE, px=(1, 1, 5, 5))
        result = run(self.b.build(), FULL)
        text = json.dumps(result, ensure_ascii=False).lower()
        for word in ('compliant', 'non-compliant', 'violation', '合规', '违规', 'approved'):
            self.assertNotIn(word, text.replace('not a statement about venue, safety or any other approval', ''))

    # ---- footprint and height
    def test_part_outside_footprint(self):
        self.b.box('stray', 's', 'wall_panel', (2.9, 3.4), (0, 1), (ZF, 1.0), cm.WHITE, px=(1, 1, 5, 5))
        self.assertIn('inside_footprint', checks(self.b)[1])

    def test_edge_trim_outside_is_not_flagged_and_inside_parts_pass(self):
        self.b.box('trim', 's', 'edge_trim', (-3, 3), (-2.06, -2), (0, ZF), cm.WHITE, px=(1, 1, 5, 5))
        self.assertNotIn('inside_footprint', checks(self.b)[1])

    def test_height_limit(self):
        self.b.box('tall', 's', 'wall_panel', (0, 1), (0, 1), (ZF, 4.5), cm.WHITE, px=(1, 1, 5, 5))
        self.assertIn('below_height_limit', checks(self.b)[1])
        self.assertNotIn('below_height_limit', checks(self.b, {'height_limit_m': 5.0, 'hanging_allowed': False})[1])

    # ---- support
    def test_floating_part_has_no_support_path(self):
        self.b.box('beam', 's', 'wall_panel', (-1, 1), (-1, 1), (2.8, 3.2), cm.WHITE, px=(1, 1, 5, 5))
        result, names = checks(self.b)
        self.assertIn('support_path', names)
        self.assertEqual([f['level'] for f in result['findings'] if f['check'] == 'support_path'], ['advisory'])

    def test_floating_part_is_information_when_hanging_is_allowed(self):
        self.b.box('beam', 's', 'wall_panel', (-1, 1), (-1, 1), (2.8, 3.2), cm.WHITE, px=(1, 1, 5, 5))
        result, _ = checks(self.b, dict(FULL, hanging_allowed=True))
        self.assertEqual([f['level'] for f in result['findings'] if f['check'] == 'support_path'], ['info'])

    def test_part_resting_on_a_supported_part_is_supported(self):
        self.b.box('post', 's', 'header_post', (-1, -0.8), (-1, -0.8), (ZF, 2.8), cm.WHITE, px=(1, 1, 5, 5))
        self.b.box('beam', 's', 'wall_panel', (-1, 1), (-1, -0.8), (2.8, 3.2), cm.WHITE, px=(1, 1, 5, 5))
        self.assertNotIn('support_path', checks(self.b)[1])

    # ---- header band
    def band(self, bottom, top, depth=0.45):
        cm.header_band(self.b, 'hb', 's', [(-2, -1.5), (2, -1.5)], depth, (bottom, top), posts=[(-2, -1.5)], floor_z=ZF, px=(1, 1, 5, 5))

    def test_header_band_in_range_has_no_findings(self):
        self.band(2.5, 3.1)
        self.assertEqual({n for n in checks(self.b)[1] if n.startswith('header_band')}, set())

    def test_header_band_too_low_or_too_tall(self):
        self.band(1.8, 3.1)
        names = checks(self.b)[1]
        self.assertIn('header_band_bottom_min', names)
        self.assertIn('header_band_height', names)

    def test_header_band_top_margin_needs_the_limit(self):
        self.band(3.2, 3.95)
        self.assertIn('header_band_top_margin', checks(self.b)[1])
        self.assertNotIn('header_band_top_margin', checks(self.b, {'hanging_allowed': False})[1])

    # ---- counter
    def test_counter_in_range_passes(self):
        cm.counter(self.b, 'c', 's', (-1.0, 0.8), (-1.5, -0.95), floor_z=ZF, px=(1, 1, 5, 5))
        names = checks(self.b)[1]
        self.assertFalse({n for n in names if n.startswith('counter')}, names)

    def test_counter_too_long_too_low_and_on_the_open_edge(self):
        cm.counter(self.b, 'c', 's', (-2.9, 0.4), (-2.0, -1.55), floor_z=ZF, height=0.9, px=(1, 1, 5, 5))
        names = checks(self.b)[1]
        for expected in ('counter_length', 'counter_height', 'counter_edge_clearance'):
            self.assertIn(expected, names)

    def test_seated_counter_height_is_accepted(self):
        cm.counter(self.b, 'c', 's', (-1.0, 0.8), (-1.5, -0.95), floor_z=ZF, height=0.75, px=(1, 1, 5, 5))
        self.assertNotIn('counter_height', checks(self.b)[1])

    # ---- displays and LED
    def test_display_ratio_and_centre_height(self):
        cm.wall_screens(self.b, 'sc', 's', 1.9, ((-2.0, 2.0), (1.0, 2.0)), [((-1.0, 0.0), (2.0, 2.4))], px=(1, 1, 5, 5))
        names = checks(self.b)[1]
        self.assertIn('display_aspect', names)
        self.assertIn('display_centre_height', names)

    def test_standard_tv_has_no_display_findings(self):
        cm.tv(self.b, 'tv', 's', 1.9, 0.0, 55, bezel=0.02, depth=0.05, px=(1, 1, 5, 5))
        self.assertFalse({n for n in checks(self.b)[1] if n.startswith('display')})

    def test_led_modules(self):
        cm.led_wall(self.b, 'led', 's', 1.9, -1.0, 0.3, cols=3, rows=2, module=0.5, depth=0.1, px=(1, 1, 5, 5))
        self.assertNotIn('led_whole_modules', checks(self.b)[1])
        cm.led_wall(self.b, 'led2', 's', 1.9, 0.5, 0.3, cols=2, rows=2, module=0.45, depth=0.1, px=(1, 1, 5, 5))
        self.assertIn('led_whole_modules', checks(self.b)[1])

    # ---- ramp, graphic, gaps
    def test_ramp_slope(self):
        self.b.box('ramp_ok', 's', 'ramp', (-3, -1.2), (-1.5, -0.5), (ZF, ZF + 0.1), cm.WOOD, px=(1, 1, 5, 5))
        self.assertNotIn('ramp_slope', checks(self.b)[1])
        self.b.box('ramp_steep', 's', 'ramp', (1.0, 1.6), (-1.5, -0.5), (ZF, ZF + 0.1), cm.WOOD, px=(1, 1, 5, 5))
        self.assertIn('ramp_slope', checks(self.b)[1])

    def test_graphic_bottom(self):
        self.b.box('g_low', 's', 'graphic', (-1, 1), (1.85, 1.9), (0.3, 1.0), cm.WHITE, px=(1, 1, 5, 5))
        self.assertIn('graphic_bottom_min', checks(self.b)[1])

    def test_narrow_gap_is_information_only(self):
        self.b.box('cab_a', 's', 'cabinet', (-1.0, -0.2), (-0.5, 0.2), (ZF, 1.1), cm.WHITE, px=(1, 1, 5, 5))
        self.b.box('cab_b', 's', 'cabinet', (0.3, 1.1), (-0.5, 0.2), (ZF, 1.1), cm.WHITE, px=(1, 1, 5, 5))
        result, _ = checks(self.b)
        gaps = [f for f in result['findings'] if f['check'] == 'narrow_gap']
        self.assertEqual([f['level'] for f in gaps], ['info'])
        self.assertAlmostEqual(gaps[0]['value'], 0.5, places=3)
        self.assertEqual(result['status'], 'info_only')

    def test_chair_legs_and_a_table_set_are_one_furniture_group(self):
        cm.round_table(self.b, 't', 's', -1.0, 0.0, floor_z=ZF, px=(1, 1, 5, 5))
        cm.shell_chair(self.b, 'c1', 's', -0.35, 0.0, 90, floor_z=ZF, px=(1, 1, 5, 5))
        cm.shell_chair(self.b, 'c2', 's', -1.65, 0.0, -90, floor_z=ZF, px=(1, 1, 5, 5))
        cm.counter(self.b, 'far', 's', (1.5, 2.8), (-1.5, -0.95), floor_z=ZF, px=(1, 1, 5, 5))
        names = checks(self.b)[1]
        self.assertNotIn('narrow_gap', names)

    def test_chair_beside_a_cabinet_is_still_reported(self):
        cm.shell_chair(self.b, 'c1', 's', 0.0, 0.0, 90, floor_z=ZF, px=(1, 1, 5, 5))
        self.b.box('cab', 's', 'cabinet', (0.55, 1.4), (-0.4, 0.4), (ZF, 1.1), cm.WHITE, px=(1, 1, 5, 5))
        self.assertIn('narrow_gap', checks(self.b)[1])

    def test_header_band_that_is_not_a_box_skips_the_depth_check(self):
        self.b.box('post', 's', 'header_post', (-1, -0.8), (-1, -0.8), (ZF, 2.5), cm.WHITE, px=(1, 1, 5, 5))
        self.b.add('frame', 's', 'profile_extrusion', 'header_band', [0.0, -0.9, 2.7],
                   {'profile': [[-1.0, -0.2], [1.0, -0.2], [1.0, 0.2], [-1.0, 0.2]], 'depth': 5.0}, cm.WHITE, px=(1, 1, 5, 5))
        result, names = checks(self.b)
        self.assertNotIn('header_band_depth', names)
        self.assertIn('header_band_depth', {n['check'] for n in result['not_checked']})

    def test_footprint_is_the_floor_rectangle_even_when_it_is_not_centred(self):
        d = Path(self._tmp.name)
        b = cm.SpecBuilder(str(d / 'source.png'), 64, 48, 'offset_floor')
        b.scope('s', 'Synthetic fixture.')
        b.box('floor', 's', 'floor', (-3, 3), (-1.0, 3.0), (0, ZF), cm.WOOD, critical=True, px=(0, 20, 64, 48))
        b.box('inside', 's', 'wall_panel', (-1, 1), (2.0, 2.9), (ZF, 2.0), cm.WHITE, px=(1, 1, 5, 5))   # outside a centred 6 x 4 footprint
        b.scale((6, 4), 'Synthetic fixture.')
        b.camera(CAM)
        b.uncertain('fixture', 'Synthetic only.')
        result = run(b.build(), FULL)
        self.assertNotIn('inside_footprint', {f['check'] for f in result['findings']})
        b.box('outside', 's', 'wall_panel', (-1, 1), (3.3, 3.6), (ZF, 2.0), cm.WHITE, px=(1, 1, 5, 5))
        result = run(b.build(), FULL)
        self.assertEqual([f['ids'] for f in result['findings'] if f['check'] == 'inside_footprint'], [['outside']])

    def test_clean_booth_reports_no_findings(self):
        cm.counter(self.b, 'c', 's', (-1.0, 0.8), (-1.5, -0.95), floor_z=ZF, px=(1, 1, 5, 5))
        result, _ = checks(self.b)
        self.assertEqual(result['status'], 'no_findings')
        self.assertEqual(result['not_checked'], [])

    def test_design_check_runs_on_a_spec_with_canopy_frame(self):
        import copy
        import design_compile as dc
        template = json.loads((ROOT / 'templates' / 'design_template.json').read_text(encoding='utf-8'))
        for hanging in (None, True):
            with self.subTest(hanging=hanging):
                d = copy.deepcopy(template)
                d['booth_type'] = 'island'
                d['form'] = dict(d['form'], footprint_m=[8.0, 6.0], height_limit_m=4.5, open_sides=['front', 'back', 'left', 'right'],
                                 hanging_allowed=hanging)
                d['elements'] = [{'id': 'crown', 'kind': 'canopy_frame', 'size_m': [5.0, 3.0, 0.3],
                                  'params': {'band_m': {'front': 0.3, 'back': 0.5, 'left': 0.4, 'right': 0.2},
                                             'sign': {'corner': 'front_left', 'along_m': [1.2, 0.8], 'drop_m': 0.3, 'text_chars': 4}}}]
                d['requirements'] = []
                with tempfile.TemporaryDirectory() as tmp:
                    spec, report = dc.compile_design(d, tmp, (320, 200))
                self.assertEqual(report['dropped'], [])
                ctx = {'height_limit_m': 4.5, 'open_sides': ['front', 'back', 'left', 'right']}
                if hanging is not None:
                    ctx['hanging_allowed'] = hanging
                result = run(spec, ctx)
                self.assertIn(result['status'], ('no_findings', 'info_only', 'advisory_findings'))
                roles = {c['role'] for c in spec['components'] if c.get('group') == 'crown'}
                self.assertTrue({'canopy_frame', 'canopy_sign'} <= roles)
                unknown = [n for n in result['not_checked'] if 'canopy_frame' in n['reason'] and 'canopy_sign' in n['reason']]
                self.assertEqual(len(unknown), 1, result['not_checked'])
                self.assertNotIn('inside_footprint', {f['check'] for f in result['findings']})
                self.assertNotIn('below_height_limit', {f['check'] for f in result['findings']})
                support = [f for f in result['findings'] if f['check'] == 'support_path']
                if hanging is True:
                    self.assertTrue(all(f['level'] == 'info' for f in support), support)
                else:
                    self.assertEqual(support, [], 'the frame stands on its posts')

    def test_design_check_runs_on_a_spec_with_lit_panel(self):
        import copy
        import design_compile as dc
        template = json.loads((ROOT / 'templates' / 'design_template.json').read_text(encoding='utf-8'))
        for niche in (None, {'width_m': 0.8, 'height_m': 1.4, 'bottom_m': 0.8, 'depth_m': 0.1, 'corner_radius_m': 0.4, 'ring_width_m': 0.04}):
            with self.subTest(niche=bool(niche)):
                d = copy.deepcopy(template)
                d['booth_type'] = 'island'
                d['form'] = dict(d['form'], footprint_m=[8.0, 6.0], height_limit_m=4.5, open_sides=['front', 'back', 'left', 'right'],
                                 hanging_allowed=True)
                d['elements'] = [{'id': 'glow', 'kind': 'lit_panel', 'side': 'centre', 'centre_m': [0.0, 0.0], 'size_m': [2.4, 0.15, 2.8],
                                  'params': {'corner_radius_m': 0.5, 'niche': niche}}]
                d['requirements'] = []
                with tempfile.TemporaryDirectory() as tmp:
                    spec, report = dc.compile_design(d, tmp, (320, 200))
                self.assertEqual(report['dropped'], [])
                result = run(spec, {'height_limit_m': 4.5, 'hanging_allowed': True, 'open_sides': ['front', 'back', 'left', 'right']})
                self.assertIn(result['status'], ('no_findings', 'info_only', 'advisory_findings'))
                roles = {c['role'] for c in spec['components'] if c.get('group') == 'glow'}
                self.assertTrue({'lit_panel', 'lit_groove'} <= roles)
                if niche:
                    self.assertTrue({'lit_niche', 'lit_niche_light'} <= roles)
                unknown = [n for n in result['not_checked'] if all(r in n['reason'] for r in sorted(roles))]
                self.assertEqual(len(unknown), 1, (roles, result['not_checked']))
                self.assertNotIn('inside_footprint', {f['check'] for f in result['findings']})
                self.assertNotIn('below_height_limit', {f['check'] for f in result['findings']})
                self.assertNotIn('support_path', {f['check'] for f in result['findings'] if f['level'] == 'advisory'})

    def test_design_check_runs_on_a_spec_with_prop_block(self):
        import copy
        import design_compile as dc
        template = json.loads((ROOT / 'templates' / 'design_template.json').read_text(encoding='utf-8'))
        d = copy.deepcopy(template)
        d['booth_type'] = 'island'
        d['form'] = dict(d['form'], footprint_m=[8.0, 6.0], height_limit_m=4.5, open_sides=['front', 'back', 'left', 'right'],
                         hanging_allowed=None)
        # a laptop-sized block on a plinth (supported), a plant block on the floor and a robot block that stands on the floor
        d['elements'] = [
            {'id': 'stand', 'kind': 'display_plinth', 'side': 'centre', 'centre_m': [0.0, 0.0], 'size_m': [0.8, 0.8, 1.0]},
            {'id': 'laptop_one', 'kind': 'prop_block', 'side': 'centre', 'centre_m': [0.0, 0.0], 'size_m': [0.36, 0.25, 0.22], 'z_m': 1.0,
             'params': {'label': 'laptop'}},
            {'id': 'plant_one', 'kind': 'prop_block', 'side': 'centre', 'centre_m': [2.0, 1.0], 'size_m': [0.5, 0.5, 0.9],
             'params': {'label': 'plant'}},
            {'id': 'arm_one', 'kind': 'prop_block', 'side': 'centre', 'centre_m': [-2.0, 1.0], 'size_m': [0.6, 0.6, 1.6],
             'params': {'label': 'robot_arm'}, 'yaw_deg': 35.0},
        ]
        d['requirements'] = []
        with tempfile.TemporaryDirectory() as tmp:
            spec, report = dc.compile_design(d, tmp, (320, 200))
        self.assertEqual(report['dropped'], [])
        result = run(spec, {'height_limit_m': 4.5, 'open_sides': ['front', 'back', 'left', 'right']})
        self.assertIn(result['status'], ('no_findings', 'info_only', 'advisory_findings'))
        self.assertEqual({c['role'] for c in spec['components'] if c['role'] == 'prop_block'}, {'prop_block'})
        unknown = [n for n in result['not_checked'] if 'prop_block' in n['reason']]
        self.assertEqual(len(unknown), 1, result['not_checked'])
        self.assertEqual(unknown[0]['check'], 'role_specific_checks')
        self.assertNotIn('inside_footprint', {f['check'] for f in result['findings']})
        self.assertNotIn('below_height_limit', {f['check'] for f in result['findings']})
        self.assertEqual([f for f in result['findings'] if f['check'] == 'support_path'], [], 'the blocks rest on the plinth or the floor')

    # ---- command line
    def test_cli_round_trip_and_invalid_input_exit_codes(self):
        d = Path(self._tmp.name)
        self.b.box('stray', 's', 'wall_panel', (2.9, 3.4), (0, 1), (ZF, 1.0), cm.WHITE, px=(1, 1, 5, 5))
        self.b.write(d / 'spec.json')
        (d / 'ctx.json').write_text(json.dumps(FULL), encoding='utf-8')
        cmd = [sys.executable, '-B', str(ROOT / 'scripts/design_check.py'), '--spec', str(d / 'spec.json'),
               '--context', str(d / 'ctx.json'), '--output', str(d / 'out.json')]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads((d / 'out.json').read_text(encoding='utf-8'))['status'], 'advisory_findings')
        (d / 'ctx.json').write_text('{"nonsense": 1}', encoding='utf-8')
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 2)
        self.assertIn('unknown context key', r.stdout + r.stderr)


if __name__ == '__main__':
    unittest.main()
