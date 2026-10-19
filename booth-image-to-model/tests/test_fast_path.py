"""Fast-path tools: camera maths, box fit, component macros, coplanar lint, preview sheet."""
from __future__ import annotations
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import camera_math
import components as cm
from box_fit import solve, parse_token, ambiguous_dims, ambiguity_hint
from camera_fit import project as fit_project
from geometry import transform_point
from validate_spec import validate, coplanar_overlaps
from geometry import mesh_for
from preview_compare import sheet
from overlay import draw_overlay, grid_segments
from boothctl import resolve_preview_engine
from common import hd_scale_for
from preview_compare import exposure_advice

CAM = {'projection': 'perspective', 'position': [7.0, -11.0, 6.5], 'target': [0.2, 0.3, 1.2], 'lens_mm': 42.0,
       'sensor_width_mm': 36.0, 'principal_point': [0.5, 0.5], 'pixel_aspect': [1, 1], 'fit_status': 'unfitted', 'roll_deg': 3.0}


def only_real_warnings(fit):
    """The fit's warnings without the note that leave-one-out was skipped for lack of corners."""
    return [w for w in fit['warnings'] if 'leave-one-out is skipped' not in w]


def tiny_source(path, size=(64, 48)):
    Image.new('RGB', size, (120, 120, 120)).save(path)
    return path


class CameraMathTests(unittest.TestCase):
    def test_matches_camera_fit_projection_including_principal_point(self):
        rng = np.random.default_rng(3)
        for _ in range(20):
            cam = dict(CAM, position=list(rng.uniform([-12, -15, 2], [12, -6, 9])), roll_deg=float(rng.uniform(-8, 8)),
                       lens_mm=float(rng.uniform(20, 80)), principal_point=list(rng.uniform(0.4, 0.6, 2)))
            pts = rng.uniform([-3, -2, 0], [3, 2, 3], (6, 3))
            uv, _ = fit_project(pts, cam, 800, 450)
            for p, ref in zip(pts, uv):
                u, v, _ = camera_math.project(p.tolist(), cam, 800, 450)
                np.testing.assert_allclose([u / 800, v / 450], ref, atol=1e-9)

    def test_unproject_round_trip_on_each_axis(self):
        for p, axis in (([1.2, -0.7, 0.0], 'z'), ([0.4, 2.0, 1.5], 'y'), ([-3.9, 0.5, 2.2], 'x')):
            u, v, _ = camera_math.project(p, CAM, 738, 415)
            back = camera_math.unproject(u, v, CAM, 738, 415, axis, p['xyz'.index(axis)])
            np.testing.assert_allclose(back, p, atol=1e-9)

    def test_plane_behind_camera_is_refused(self):
        with self.assertRaises(ValueError):
            camera_math.unproject(369, 0, CAM, 738, 415, 'z', 50.0)


class BoxFitTests(unittest.TestCase):
    W, D, H, YF = 6.4, 3.2, 3.1, -0.6

    def clicks(self, cam, w=738, h=415):
        sym = {'W/2': self.W / 2, '-W/2': -self.W / 2, 'D/2': self.D / 2, '-D/2': -self.D / 2, 'H': self.H, 'F': 0.0, '?yf': self.YF}
        corners = [('tfl', ['-W/2', '-D/2', 'H']), ('tbl', ['-W/2', 'D/2', 'H']), ('tbr', ['W/2', 'D/2', 'H']),
                   ('ffl', ['-W/2', '-D/2', 'F']), ('fbr', ['W/2', 'D/2', 'F']), ('tfr', ['W/2', '-D/2', 'H']),
                   ('wall_bottom', ['W/2', '?yf', 'F']), ('wall_top', ['W/2', '?yf', 'H'])]
        pts = []
        for name, toks in corners:
            u, v, _ = camera_math.project([sym[t] for t in toks], cam, w, h)
            pts.append({'id': name, 'uv_px': [u, v], 'xyz': toks})
        return {'image': {'width': w, 'height': h}, 'known': {'D': self.D}, 'points': pts}

    def test_recovers_proportions_free_variable_and_camera(self):
        cam = dict(CAM, position=[8.0, -12.0, 7.0], target=[0.0, 0.0, 1.4], lens_mm=48.0, roll_deg=2.0)
        result = solve(self.clicks(cam), seeds=16)
        self.assertEqual(result['status'], 'fitted')
        self.assertLess(result['rms_px'], 0.05)
        self.assertAlmostEqual(result['dims_m']['W'], self.W, places=3)
        self.assertAlmostEqual(result['dims_m']['H'], self.H, places=3)
        self.assertAlmostEqual(result['free']['yf'], self.YF, places=3)
        self.assertAlmostEqual(result['camera']['lens_mm'], 48.0, places=2)
        self.assertEqual(result['dim_source']['D'], 'given')
        # The emitted camera reproduces the clicks through the Blender-consistent model.
        for p in result['points']:
            self.assertLess(p['err_px'], 0.05)

    def test_scale_must_be_anchored(self):
        data = self.clicks(CAM)
        data['known'] = {}
        with self.assertRaisesRegex(ValueError, 'NO_SCALE_ANCHOR'):
            solve(data, seeds=2)

    def test_too_few_points_is_underdetermined(self):
        data = self.clicks(CAM)
        data['points'] = data['points'][:4]
        with self.assertRaisesRegex(ValueError, 'UNDERDETERMINED'):
            solve(data, seeds=2)

    def test_token_grammar(self):
        self.assertEqual(parse_token('-W/2'), ('half', 'W', -1.0))
        self.assertEqual(parse_token(1.05), ('const', 1.05))
        self.assertEqual(parse_token('?cx'), ('free', 'cx'))
        with self.assertRaises(ValueError):
            parse_token('W/3')


def build_fixture(folder):
    return ComponentMacroTests.build(None, folder)


class ComponentMacroTests(unittest.TestCase):
    def build(self, folder):
        src = tiny_source(Path(folder) / 'source.png')
        b = cm.SpecBuilder(str(src), 64, 48, 'macro_fixture')
        b.source['image'] = 'source.png'
        b.scope('shell', 'Synthetic booth shell.')
        b.scope('furniture', 'Synthetic furniture.')
        b.exclude('hall', 'Synthetic hall.')
        b.box('floor', 'shell', 'floor', (-3, 3), (-1.5, 1.5), (0, 0.1), cm.WOOD, critical=True, px=(0, 20, 64, 48))
        b.box('back_wall', 'shell', 'back_wall', (-3, 3), (1.4, 1.5), (0.1, 3.0), cm.WHITE, critical=True, px=(0, 0, 64, 30))
        cm.counter(b, 'counter', 'furniture', (-1.0, 0.2), (-1.2, -0.6), floor_z=0.1, plinth=0.1, top_color=[0.9, 0.2, 0.3, 1], px=(10, 20, 30, 40))
        cm.round_table(b, 'table', 'furniture', 1.2, 0.3, floor_z=0.1, px=(30, 20, 40, 30))
        cm.shell_chair(b, 'chair', 'furniture', 1.8, 0.3, 90, floor_z=0.1, px=(40, 20, 50, 30))
        cm.louvre_wall(b, 'fin', 'shell', (-1.5, 0.5), (2.0, 2.4), (0.1, 2.8), 5, 0.2, [0.9, 0.15, 0.27, 1], stagger=0.08)
        cm.slats(b, 'slat', 'shell', -2.5, 0.25, 4, (-0.3, -0.2), (0.6, 2.8), 0.1, cm.WOOD)
        cm.planter(b, 'planter', 'furniture', (-2.6, -1.6), (-0.6, -0.1), floor_z=0.1, blades=6)
        cm.wall_screens(b, 'tv', 'furniture', 1.4, ((-1.0, 1.2), (1.1, 2.0)), [((-0.9, 0.0), (1.2, 1.9)), ((0.1, 1.1), (1.2, 1.9))])
        cm.shelves(b, 'shelf', 'furniture', -3.0, (-1.0, 0.0), (1.3, 1.6), backing_color=[0.9, 0.1, 0.2, 1])
        b.scale((6, 3), 'Synthetic fixture.')
        b.camera(CAM)
        b.landmark('corner', 'back_wall', [3.0, 1.4, 3.0], (60, 5))
        b.uncertain('fixture', 'Synthetic only.')
        return b

    def test_macro_booth_is_valid_without_z_fighting(self):
        with tempfile.TemporaryDirectory() as d:
            b = self.build(d)
            spec = b.write(Path(d) / 'spec.json')
            report = validate(spec, d)
            self.assertTrue(report['valid'], report['errors'][:3])
            self.assertEqual([w for w in report['warnings'] if w['code'] == 'COPLANAR_OVERLAP'], [])
            self.assertEqual(len({c['id'] for c in spec['components']}), len(spec['components']))
            self.assertTrue(any(r['component_id'] == 'floor' for r in spec['requirements']))

    def test_landmark_local_coordinates_map_back_to_world(self):
        with tempfile.TemporaryDirectory() as d:
            b = self.build(d)
            b.box('turned', 'shell', 'panel', (0, 1), (0, 0.1), (0, 1), cm.WHITE, rot=(10, 20, 30), px=(0, 0, 10, 10))
            world = [0.3, 0.2, 0.9]
            lm = b.landmark('turned_pt', 'turned', world, (5, 5))
            comp = next(c for c in b.components if c['id'] == 'turned')
            np.testing.assert_allclose(transform_point(lm['local_xyz'], comp['location'], comp['rotation_deg']), world, atol=1e-4)

    def test_tv_table_is_sixteen_by_nine(self):
        w, h = cm.TV[55]
        self.assertAlmostEqual(w / h, 16 / 9, places=2)
        self.assertAlmostEqual(math.hypot(w, h), 55 * 0.0254, places=2)


class CoplanarLintTests(unittest.TestCase):
    @staticmethod
    def box(ident, centre, size, rot=(0, 0, 0)):
        return {'id': ident, 'kind': 'box', 'location': list(centre), 'rotation_deg': list(rot), 'params': {'size': list(size)}}

    def lint(self, comps):
        return coplanar_overlaps(comps, {c['id']: mesh_for(c) for c in comps})

    def test_same_facing_overlap_is_reported(self):
        found = self.lint([self.box('wall', (0, 0, 1.5), (4, 0.2, 3)), self.box('rim', (0, 0.1, 2.9), (4, 0.6, 0.2))])
        self.assertTrue(any(f['plane'] == '+z=3' for f in found))

    def test_adjacent_and_opposite_contact_are_not_reported(self):
        found = self.lint([self.box('a', (0, 0, 0.5), (1, 1, 1)), self.box('b', (1, 0, 0.5), (1, 1, 1)),
                           self.box('c', (0, 0, 1.5), (1, 1, 1))])
        self.assertEqual(found, [])

    def test_overlap_hidden_against_another_part_is_not_reported(self):
        # Two bases overlapping on the floor slab and two backs on one wall are invisible.
        found = self.lint([self.box('floor', (0, 0, 0.05), (4, 4, 0.1)), self.box('a', (0, 0, 0.6), (1, 1, 1)),
                           self.box('b', (0.5, 0, 0.35), (1, 0.5, 0.5))])
        self.assertFalse(any(f['plane'] == '-z=0.1' for f in found))

    def test_rotated_strips_on_one_plane_do_not_false_alarm(self):
        found = self.lint([self.box('s1', (0, 0, 1), (0.02, 0.1, 2), rot=(35, 0, 0)),
                           self.box('s2', (0, 0.6, 1), (0.02, 0.1, 2), rot=(35, 0, 0))])
        self.assertEqual(found, [])


class PreviewSheetTests(unittest.TestCase):
    def test_sheet_reports_landmark_error_in_pixels(self):
        with tempfile.TemporaryDirectory() as d:
            run = Path(d)
            Image.new('RGB', (40, 30), (90, 90, 90)).save(run / 'source_reference.png')
            render = Image.new('RGBA', (40, 30), (0, 0, 0, 0))
            for x in range(10, 30):
                for y in range(5, 25):
                    render.putpixel((x, y), (200, 50, 50, 255))
            render.save(run / 'preview_render.png')
            (run / 'spec.json').write_text(json.dumps({'landmarks': [{'id': 'a', 'uv': [0.5, 0.5], 'visibility': 'visible', 'fit': True}]}))
            (run / 'projected_landmarks.json').write_text(json.dumps([{'id': 'a', 'uv': [0.6, 0.5], 'depth': 3.0}]))
            s = sheet(run)
            self.assertTrue((run / 'compare_sheet.png').is_file())
            self.assertAlmostEqual(s['landmarks'][0]['err_px'], 4.0, places=3)
            self.assertEqual(Image.open(run / 'compare_sheet.png').size, (80, 60))


class CameraHelperTests(unittest.TestCase):
    def test_height_above_round_trip_and_miss_distance(self):
        base, top = [1.0, -0.5, 0.0], [1.0, -0.5, 0.9]
        bu, bv, _ = camera_math.project(base, CAM, 738, 415)
        tu, tv, _ = camera_math.project(top, CAM, 738, 415)
        h, miss = camera_math.height_above((tu, tv), (bu, bv), CAM, 738, 415, 0.0)
        self.assertAlmostEqual(h, 0.9, places=6)
        self.assertLess(miss, 1e-6)
        _, far = camera_math.height_above((tu + 80, tv), (bu, bv), CAM, 738, 415, 0.0)
        self.assertGreater(far, 0.1)

    def test_height_above_uses_the_floor_level(self):
        base, top = [0.3, 0.2, 0.1], [0.3, 0.2, 1.15]
        bu, bv, _ = camera_math.project(base, CAM, 738, 415)
        tu, tv, _ = camera_math.project(top, CAM, 738, 415)
        h, _ = camera_math.height_above((tu, tv), (bu, bv), CAM, 738, 415, 0.1)
        self.assertAlmostEqual(h, 1.05, places=6)

    def test_on_wall_x_round_trip(self):
        p = [-3.9, 0.5, 2.2]
        u, v, _ = camera_math.project(p, CAM, 738, 415)
        np.testing.assert_allclose(camera_math.on_wall_x(u, v, CAM, 738, 415, -3.9), p, atol=1e-9)

    def test_preview_engine_fallback_is_sticky(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(resolve_preview_engine(d, 'workbench'), ('workbench', False))
            (Path(d) / '.preview_engine.json').write_text('{"engine": "cycles"}', encoding='utf-8')
            self.assertEqual(resolve_preview_engine(d, 'workbench'), ('cycles', True))
            self.assertEqual(resolve_preview_engine(d, 'cycles'), ('cycles', False))
            (Path(d) / '.preview_engine.json').write_text('not json', encoding='utf-8')
            self.assertEqual(resolve_preview_engine(d, 'workbench'), ('workbench', False))


class FitOverlayTests(unittest.TestCase):
    CAM2 = dict(CAM, position=[8.0, -12.0, 7.0], target=[0.0, 0.0, 1.4], lens_mm=48.0, roll_deg=2.0)

    def fit(self):
        return solve(BoxFitTests().clicks(self.CAM2), seeds=16)

    def test_fit_result_carries_image_size_and_floor(self):
        res = self.fit()
        self.assertEqual(res['image'], {'width': 738, 'height': 415})
        self.assertEqual(res['floor_z'], 0.0)

    def test_overlay_draws_the_fitted_box_where_the_camera_projects_it(self):
        res = self.fit()
        W, D, H = (res['dims_m'][k] for k in 'WDH')
        with tempfile.TemporaryDirectory() as d:
            src, out = Path(d) / 's.png', Path(d) / 'o.png'
            Image.new('RGB', (738, 415), (60, 60, 60)).save(src)
            summary = draw_overlay(res, src, out)
            self.assertEqual(summary['box_edges'], 12)
            self.assertGreater(summary['grid_segments'], 20)
            im = Image.open(out).convert('RGB')
            mid = [0.0, -D / 2, H]                           # middle of the front-top edge
            u, v, _ = camera_math.project(mid, res['camera'], 738, 415)
            window = [im.getpixel((int(round(u)) + dx, int(round(v)) + dy)) for dx in range(-2, 3) for dy in range(-2, 3)]
            self.assertIn((255, 220, 0), window)

    def test_grid_follows_half_metre_steps_and_includes_the_centre_lines(self):
        grid, edges = grid_segments({'W': 3.0, 'D': 2.0, 'H': 2.5}, 0.0)
        floor_x = sorted({round(a[0], 6) for a, b in grid if a[2] == 0 and b[2] == 0 and a[0] == b[0]})
        self.assertEqual(floor_x, [-1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5])
        self.assertEqual(len(edges), 12)

    def test_segments_behind_the_camera_are_skipped_not_fatal(self):
        cam = dict(CAM, position=[0.0, -0.5, 1.5], target=[0.0, 2.0, 1.5], roll_deg=0.0)
        fit = {'camera': cam, 'image': {'width': 200, 'height': 120}, 'dims_m': {'W': 4.0, 'D': 4.0, 'H': 3.0}, 'floor_z': 0.0,
               'status': 'fitted', 'rms_px': 1.0, 'points': [{'uv_px': [10, 10], 'fitted_px': [12, 11], 'err_px': 2.2}]}
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / 's.png'
            Image.new('RGB', (200, 120), (90, 90, 90)).save(src)
            draw_overlay(fit, src, Path(d) / 'o.png')
            self.assertTrue((Path(d) / 'o.png').is_file())

    def test_result_from_an_older_box_fit_is_explained(self):
        res = self.fit()
        del res['image']
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / 's.png'
            Image.new('RGB', (738, 415), (90, 90, 90)).save(src)
            with self.assertRaisesRegex(ValueError, 'lacks image.*re-run box_fit'):
                draw_overlay(res, src, Path(d) / 'o.png')

    def test_image_size_mismatch_is_refused(self):
        res = self.fit()
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / 's.png'
            Image.new('RGB', (100, 60), (90, 90, 90)).save(src)
            with self.assertRaisesRegex(ValueError, 'IMAGE_SHAPE_MISMATCH'):
                draw_overlay(res, src, Path(d) / 'o.png')


class Batch5Tests(unittest.TestCase):
    """Overlay without a fitted height, scale cross-checks, exposure advice."""

    def test_overlay_works_when_no_point_fixed_the_height(self):
        grid, edges = grid_segments({'W': 3.0, 'D': 2.0}, 0.0)
        self.assertEqual(len(edges), 4)                                  # floor outline only
        self.assertTrue(all(a[2] == 0 and b[2] == 0 for a, b in grid + edges))
        fit = {'camera': FitOverlayTests.CAM2, 'image': {'width': 738, 'height': 415}, 'dims_m': {'W': 6.4, 'D': 3.2}, 'floor_z': 0.0,
               'status': 'ambiguous', 'rms_px': 1.0, 'points': [{'uv_px': [10, 10], 'fitted_px': [12, 11], 'err_px': 2.2}]}
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / 's.png'
            Image.new('RGB', (738, 415), (80, 80, 80)).save(src)
            summary = draw_overlay(fit, src, Path(d) / 'o.png')
            self.assertEqual(summary['box_edges'], 4)

    def test_overlay_without_width_or_depth_is_explained(self):
        fit = {'camera': FitOverlayTests.CAM2, 'image': {'width': 64, 'height': 48}, 'dims_m': {'H': 3.0}, 'status': 'ambiguous',
               'rms_px': 1.0, 'points': []}
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / 's.png'
            Image.new('RGB', (64, 48), (80, 80, 80)).save(src)
            with self.assertRaisesRegex(ValueError, 'lacks .*dims_m.W'):
                draw_overlay(fit, src, Path(d) / 'o.png')

    def checks_data(self, expected):
        cam = FitOverlayTests.CAM2
        base, top = [1.0, -0.5, 0.0], [1.0, -0.5, 0.9]
        bu, bv, _ = camera_math.project(base, cam, 738, 415)
        tu, tv, _ = camera_math.project(top, cam, 738, 415)
        data = BoxFitTests().clicks(cam)
        data['assumed'] = ['D']
        data['scale_checks'] = [{'id': 'stool', 'base_px': [bu, bv], 'top_px': [tu, tv], 'expected_height_m': expected}]
        return data

    def test_scale_check_agrees_when_the_object_has_its_expected_height(self):
        res = solve(self.checks_data(0.9), seeds=16)
        check = res['scale_checks'][0]
        self.assertTrue(check['ok'], check)
        self.assertEqual(check['id'], 'stool')
        self.assertEqual(check['expected_m'], 0.9)
        self.assertAlmostEqual(check['measured_m'], 0.9, delta=0.02)
        self.assertAlmostEqual(check['ratio'], 1.0, delta=0.03)
        self.assertEqual(check['tolerance'], res['limits']['scale_ratio_tolerance'])       # no tolerance given: the default, also in limits
        self.assertEqual(only_real_warnings(res), [])
        self.assertEqual(res['assumed'], ['D'])

    def test_scale_check_flags_a_wrong_scale_assumption(self):
        res = solve(self.checks_data(0.6), seeds=16)           # the booth was fitted at its true size, so 0.6 m is the wrong expectation
        check = res['scale_checks'][0]
        self.assertFalse(check['ok'])
        self.assertTrue(any('scale check stool' in w for w in res['warnings']))     # the text warning stays next to the structured entry
        self.assertGreater(check['ratio'], 1.4)
        self.assertEqual({k: check[k] for k in ('id', 'expected_m')}, {'id': 'stool', 'expected_m': 0.6})
        self.assertAlmostEqual(check['measured_m'], 0.9, delta=0.02)

    def test_scale_check_entries_are_validated(self):
        data = self.checks_data(0.9)
        del data['scale_checks'][0]['top_px']
        with self.assertRaisesRegex(ValueError, "needs 'top_px'"):
            solve(data, seeds=2)
        data = self.checks_data(0.9)
        data['scale_checks'][0]['expected_height_m'] = -1
        with self.assertRaisesRegex(ValueError, 'expected_height_m must be positive'):
            solve(data, seeds=2)

    def test_exposure_advice_steps_down_for_clipped_highlights_and_up_for_dark_images(self):
        self.assertEqual(exposure_advice(None, 0.02, 0.8, -0.5)['suggested_delta_ev'], 0.0)
        self.assertEqual(exposure_advice(None, 0.12, 0.99, -0.5)['suggested_delta_ev'], -0.5)
        self.assertEqual(exposure_advice(None, 0.40, 1.0, -0.5)['suggested_client_exposure_ev'], -2.5)
        self.assertEqual(exposure_advice(None, 1.0, 1.0, -0.5)['suggested_delta_ev'], -2.0)          # floor
        self.assertEqual(exposure_advice(None, 0.0, 0.3, -0.5)['suggested_delta_ev'], 0.5)

    def test_sheet_reports_clipping_of_the_customer_look_preview(self):
        with tempfile.TemporaryDirectory() as d:
            run = Path(d)
            Image.new('RGB', (40, 30), (90, 90, 90)).save(run / 'source_reference.png')
            Image.new('RGBA', (40, 30), (200, 50, 50, 255)).save(run / 'preview_render.png')
            Image.new('RGB', (40, 30), (255, 255, 255)).save(run / 'preview_client_look.png')
            (run / 'spec.json').write_text(json.dumps({'landmarks': []}))
            (run / 'projected_landmarks.json').write_text('[]')
            (run / 'build_report.json').write_text(json.dumps({'client_exposure_ev': -0.5}))
            look = sheet(run)['client_look']
            self.assertEqual(look['clipped_fraction'], 1.0)
            self.assertEqual(look['advice']['suggested_client_exposure_ev'], -2.5)

    def test_sheet_without_a_customer_look_preview_reports_none(self):
        with tempfile.TemporaryDirectory() as d:
            run = Path(d)
            Image.new('RGB', (40, 30), (90, 90, 90)).save(run / 'source_reference.png')
            Image.new('RGBA', (40, 30), (200, 50, 50, 255)).save(run / 'preview_render.png')
            (run / 'spec.json').write_text(json.dumps({'landmarks': []}))
            (run / 'projected_landmarks.json').write_text('[]')
            self.assertIsNone(sheet(run)['client_look'])


class HdCapTests(unittest.TestCase):
    def test_cap_follows_the_measured_memory_bracket(self):
        self.assertEqual(hd_scale_for(736, 736, 3), 3)        # 4.9 MP rendered on a 16 GB machine
        self.assertEqual(hd_scale_for(938, 816, 3), 2)        # 6.9 MP ran the denoiser out of memory
        self.assertEqual(hd_scale_for(1200, 878, 3), 2)
        self.assertEqual(hd_scale_for(446, 214, 3), 3)

    def test_cap_edges(self):
        self.assertEqual(hd_scale_for(4000, 3000, 2), 1)      # even 2x is too big: no HD
        self.assertEqual(hd_scale_for(100, 100, 1), 1)
        self.assertEqual(hd_scale_for(100, 100, 8, max_megapixels=0.05), 2)
        for bad in ((100, 100, 0), (100, 100, 2.5), (100, 100, True)):
            with self.assertRaises(ValueError):
                hd_scale_for(*bad)
        with self.assertRaises(ValueError):
            hd_scale_for(100, 100, 2, max_megapixels=0)


class FitDiagnosticsTests(unittest.TestCase):
    def test_high_residual_fit_is_not_called_fitted_and_lists_the_worst_points(self):
        data = BoxFitTests().clicks(FitOverlayTests.CAM2)
        data['points'][3]['uv_px'] = [data['points'][3]['uv_px'][0] + 60, data['points'][3]['uv_px'][1] - 45]
        res = solve(data, seeds=16)
        self.assertEqual(res['status'], 'high_residual')
        self.assertEqual(len(res['worst_points']), 3)
        self.assertTrue(any('rms' in w and res['worst_points'][0]['id'] in w for w in res['warnings']))
        # 8 corners with a free wall position are 14 equations for 10 unknowns: too few to say which click is wrong (the robust fit
        # may even blame a neighbour), so nobody is named and the output says why.
        self.assertFalse(res['loo_reliable'])
        self.assertEqual(res['outliers'], [])
        self.assertTrue(any('leave-one-out is skipped' in w for w in res['warnings']))

    def test_clean_fit_has_no_warnings(self):
        res = solve(BoxFitTests().clicks(FitOverlayTests.CAM2), seeds=16)
        self.assertEqual(res['status'], 'fitted')
        # These 8 corners (10 unknowns) are too few for a leave-one-out check; that is said once and is the only remark.
        self.assertEqual(len(res['warnings']), 1)
        self.assertIn('leave-one-out is skipped', res['warnings'][0])
        self.assertEqual(only_real_warnings(res), [])

    def test_ambiguity_names_the_unconstrained_dimension(self):
        self.assertEqual(ambiguous_dims({'W': 0.0, 'H': 2.3}, {'W': 8.0, 'H': 4.8}, ['W', 'H']), ['H'])
        self.assertEqual(ambiguous_dims({'W': 0.1}, {'W': 8.0}, ['W']), [])
        hint = ambiguity_hint(['H'])
        self.assertIn('H', hint)
        self.assertIn('numeric', hint)
        self.assertIsNone(ambiguity_hint([]))

    def test_good_fit_reports_no_ambiguous_dimension(self):
        res = solve(BoxFitTests().clicks(FitOverlayTests.CAM2), seeds=16)
        self.assertEqual(res['ambiguous_dims'], [])
        self.assertIsNone(res['ambiguity_hint'])

    def test_preview_sheet_splits_fit_and_check_landmarks(self):
        with tempfile.TemporaryDirectory() as d:
            run = Path(d)
            Image.new('RGB', (40, 30), (90, 90, 90)).save(run / 'source_reference.png')
            Image.new('RGBA', (40, 30), (200, 50, 50, 255)).save(run / 'preview_render.png')
            (run / 'spec.json').write_text(json.dumps({'landmarks': [
                {'id': 'a', 'uv': [0.5, 0.5], 'visibility': 'visible', 'fit': True},
                {'id': 'b', 'uv': [0.25, 0.5], 'visibility': 'visible', 'fit': False}]}))
            (run / 'projected_landmarks.json').write_text(json.dumps([{'id': 'a', 'uv': [0.6, 0.5], 'depth': 3.0},
                                                                      {'id': 'b', 'uv': [0.45, 0.5], 'depth': 3.0}]))
            s = sheet(run)
            self.assertAlmostEqual(s['landmark_median_fit_px'], 4.0, places=3)
            self.assertAlmostEqual(s['landmark_median_check_px'], 8.0, places=3)
            self.assertEqual(s['landmark_check_count'], 1)
            self.assertIn('independent check', s['landmark_note'])


class TemplateTests(unittest.TestCase):
    def job(self, folder, snapshot_size, fit_size):
        job = Path(folder) / 'job'
        (job / 'benchmark' / 'case_x').mkdir(parents=True)
        Image.new('RGB', snapshot_size, (120, 120, 120)).save(job / 'benchmark' / 'case_x' / 'source.png')
        cam = FitOverlayTests.CAM2
        fit = solve(BoxFitTests().clicks(cam, *fit_size), seeds=8)
        (job / 'box_fit.json').write_text(json.dumps(fit), encoding='utf-8')
        shutil.copy(ROOT / 'templates/build_spec_template.py', job / 'build_spec.py')
        return job

    def run_template(self, job):
        env = dict(os.environ, BOOTH_SKILL=str(ROOT))
        return subprocess.run([sys.executable, '-B', str(job / 'build_spec.py'), str(job / 'out')],
                              capture_output=True, text=True, env=env, timeout=120)

    def test_template_builds_a_valid_spec_from_a_synthetic_fit(self):
        with tempfile.TemporaryDirectory() as d:
            job = self.job(d, (738, 415), (738, 415))
            r = self.run_template(job)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            spec = json.loads((job / 'out' / 'spec.json').read_text(encoding='utf-8'))
            report = validate(spec, job / 'out')
            self.assertTrue(report['valid'], report['errors'][:3])
            self.assertEqual((spec['source']['width'], spec['source']['height']), (738, 415))
            self.assertEqual([w for w in report['warnings'] if w['code'] == 'COPLANAR_OVERLAP'], [])

    def test_template_refuses_a_snapshot_of_another_size(self):
        with tempfile.TemporaryDirectory() as d:
            job = self.job(d, (100, 60), (738, 415))
            r = self.run_template(job)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('box_fit.json is for 738x415', r.stdout + r.stderr)

    def test_template_has_no_hard_coded_canvas(self):
        text = (ROOT / 'templates/build_spec_template.py').read_text(encoding='utf-8')
        self.assertNotIn('738', text)
        self.assertNotIn('415', text)


@unittest.skipUnless(os.environ.get('BLENDER_EXE'), 'Set BLENDER_EXE for native preview/HD checks')
class NativeFastPathTests(unittest.TestCase):
    def run_build(self, spec, out, *extra):
        cmd = [os.environ['BLENDER_EXE'], '--background', '--factory-startup', '--disable-autoexec', '--python-exit-code', '1',
               '--python', str(ROOT / 'scripts/blender_build.py'), '--', '--spec', str(spec), '--out', str(out), *extra]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    def fixture(self, d):
        b = build_fixture(d)
        b.camera(dict(CAM, position=[7.0, -9.0, 5.0], target=[0.0, 0.0, 1.0]))
        b.write(Path(d) / 'spec.json')
        return Path(d) / 'spec.json'

    def test_preview_writes_one_matched_render_and_releases_lock(self):
        with tempfile.TemporaryDirectory() as d:
            spec = self.fixture(d)
            out = Path(d) / 'preview'
            r = self.run_build(spec, out, '--preview')
            if r.returncode != 0 and 'EXCEPTION_ACCESS_VIOLATION' in r.stdout + r.stderr:
                r = self.run_build(spec, Path(d) / 'preview_cycles', '--preview', '--preview-engine', 'cycles')
                out = Path(d) / 'preview_cycles'
            self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-1500:])
            self.assertEqual(Image.open(out / 'preview_render.png').size, (64, 48))
            self.assertFalse((out / '.build.lock').exists())
            report = json.loads((out / 'build_report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['status'], 'preview_only')
            self.assertFalse((out / 'client_render.png').exists())
            self.assertEqual(report['client_exposure_ev'], -1.0)                     # default customer exposure
            stages = report['stage_seconds']
            self.assertIn('scene_and_first_save', stages)
            self.assertIn('render:preview_render.png', stages)
            self.assertTrue(all(isinstance(v, (int, float)) and v >= 0 for v in stages.values()))

    def test_render_with_hd_scale_hashes_hd_outputs(self):
        with tempfile.TemporaryDirectory() as d:
            spec = self.fixture(d)
            out = Path(d) / 'final'
            r = self.run_build(spec, out, '--render', '--samples', '2', '--hd-scale', '2', '--hd-samples', '2')
            self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-1500:])
            self.assertEqual(Image.open(out / 'client_render_hd.png').size, (128, 96))
            self.assertEqual(Image.open(out / 'inspection_render_hd.png').size, (128, 96))
            report = json.loads((out / 'build_report.json').read_text(encoding='utf-8'))
            self.assertIn('client_render_hd.png', {a['path'] for a in report['artifacts']})
            self.assertEqual(report['hd_scale'], 2)

    def test_client_look_preview_is_opaque_and_matches_the_canvas(self):
        with tempfile.TemporaryDirectory() as d:
            spec = self.fixture(d)
            out = Path(d) / 'p'
            r = self.run_build(spec, out, '--preview', '--preview-client-look', '--samples', '2')
            self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-1500:])
            with Image.open(out / 'preview_client_look.png') as im:
                self.assertEqual(im.size, (64, 48))
                self.assertEqual(im.convert('RGBA').getchannel('A').getextrema(), (255, 255))
            with Image.open(out / 'preview_render.png') as im:
                self.assertEqual(im.convert('RGBA').getchannel('A').getextrema()[0], 0)       # the matched image stays transparent
            r3 = self.run_build(spec, Path(d) / 'p3', '--render', '--preview-client-look')
            self.assertNotEqual(r3.returncode, 0)

    def test_hd_scale_is_capped_and_the_report_says_so(self):
        with tempfile.TemporaryDirectory() as d:
            spec = self.fixture(d)
            out = Path(d) / 'final'
            r = self.run_build(spec, out, '--render', '--samples', '2', '--hd-scale', '4', '--hd-samples', '2', '--hd-max-mp', '0.02')
            self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-1500:])
            self.assertEqual(Image.open(out / 'client_render_hd.png').size, (128, 96))      # 64x48 at 2x; 4x would be 0.049 MP
            report = json.loads((out / 'build_report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['hd'], {'requested': 4, 'effective': 2, 'capped': True, 'max_megapixels': 0.02})
            self.assertEqual(report['hd_scale'], 2)
            self.assertIs(report['partial'], False)
            self.assertIn('client_render_hd.png', {a['path'] for a in report['artifacts']})

    def test_hd_is_skipped_when_even_double_size_exceeds_the_cap(self):
        with tempfile.TemporaryDirectory() as d:
            spec = self.fixture(d)
            out = Path(d) / 'final'
            r = self.run_build(spec, out, '--render', '--samples', '2', '--hd-scale', '3', '--hd-max-mp', '0.001')
            self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-1500:])
            self.assertFalse((out / 'client_render_hd.png').exists())
            report = json.loads((out / 'build_report.json').read_text(encoding='utf-8'))
            self.assertEqual((report['hd']['effective'], report['hd']['capped']), (1, True))
            self.assertTrue((out / 'client_render.png').is_file())

    def test_hd_views_client_writes_only_the_customer_hd_image(self):
        with tempfile.TemporaryDirectory() as d:
            spec = self.fixture(d)
            out = Path(d) / 'final'
            r = self.run_build(spec, out, '--render', '--samples', '2', '--hd-scale', '2', '--hd-samples', '2', '--hd-views', 'client')
            self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-1500:])
            self.assertTrue((out / 'client_render_hd.png').is_file())
            self.assertFalse((out / 'inspection_render_hd.png').exists())
            self.assertEqual(json.loads((out / 'build_report.json').read_text(encoding='utf-8'))['hd_views'], 'client')

    def test_preview_and_render_flags_are_exclusive(self):
        with tempfile.TemporaryDirectory() as d:
            r = self.run_build(self.fixture(d), Path(d) / 'x', '--preview', '--render')
            self.assertNotEqual(r.returncode, 0)


if __name__ == '__main__':
    unittest.main()
