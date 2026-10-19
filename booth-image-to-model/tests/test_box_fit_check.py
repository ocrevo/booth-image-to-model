"""box_fit: robust loss, check points (fit=false), leave-one-out errors and when they can be trusted, outlier naming and dropping,
the limits it reports, the check_failed status and the one-JSON CLI output.

Synthetic clicks only: a known camera projects the corners of an invented 6.4 x 3.2 x 3.1 m box, so the truth is known.
"""
from __future__ import annotations
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import box_fit
import camera_math
from box_fit import solve

CAM = {'projection': 'perspective', 'position': [8.0, -12.0, 7.0], 'target': [0.0, 0.0, 1.4], 'lens_mm': 48.0, 'sensor_width_mm': 36.0,
       'principal_point': [0.5, 0.5], 'pixel_aspect': [1, 1], 'fit_status': 'unfitted', 'roll_deg': 2.0}
W, D, H, YF = 6.4, 3.2, 3.1, -0.6
SIZE = (738, 415)
SYM = {'W/2': W / 2, '-W/2': -W / 2, 'D/2': D / 2, '-D/2': -D / 2, 'H': H, 'F': 0.0, '?yf': YF, 0.0: 0.0}
CORNERS = [('tfl', ['-W/2', '-D/2', 'H']), ('tbl', ['-W/2', 'D/2', 'H']), ('tbr', ['W/2', 'D/2', 'H']),
           ('ffl', ['-W/2', '-D/2', 'F']), ('fbr', ['W/2', 'D/2', 'F']), ('tfr', ['W/2', '-D/2', 'H']),
           ('wall_bottom', ['W/2', '?yf', 'F']), ('wall_top', ['W/2', '?yf', 'H']),
           ('fbl', ['-W/2', 'D/2', 'F']), ('mid_top_front', [0.0, '-D/2', 'H']),
           ('mid_floor_back', [0.0, 'D/2', 'F']), ('mid_top_back', [0.0, 'D/2', 'H'])]
# The corners that need no ?name: with D given the fit has 9 unknowns (7 camera + W + H), the common case of 8 to 10 corners.
PLAIN_CORNERS = [c for c in CORNERS if '?yf' not in c[1]]
SEEDS = 12


def clicks(n=12, cam=CAM, noise=0.0, seed=0, size=SIZE, corners=CORNERS):
    """The first n corners as clicks (pixel noise in px, deterministic), D given."""
    rng = np.random.default_rng(seed)
    pts = []
    for name, toks in corners[:n]:
        u, v, _ = camera_math.project([SYM[t] for t in toks], cam, *size)
        pts.append({'id': name, 'uv_px': [u + rng.normal(0, noise) if noise else u, v + rng.normal(0, noise) if noise else v], 'xyz': list(toks)})
    return {'image': {'width': size[0], 'height': size[1]}, 'known': {'D': D}, 'points': pts}


def move(data, point_id, du, dv):
    for p in data['points']:
        if p['id'] == point_id:
            p['uv_px'] = [p['uv_px'][0] + du, p['uv_px'][1] + dv]
    return data


def flag(data, *ids, value=False):
    for p in data['points']:
        if p['id'] in ids:
            p['fit'] = value
    return data


def camera_error(res):
    """(position error in m, lens error in mm) against the camera that made the clicks."""
    c = res['camera']
    return float(np.linalg.norm(np.array(c['position']) - np.array(CAM['position']))), abs(c['lens_mm'] - CAM['lens_mm'])


class RobustLossTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.noisy = clicks(noise=0.6, seed=4)
        cls.soft = solve(cls.noisy, seeds=SEEDS)
        cls.plain = solve(cls.noisy, seeds=SEEDS, loss='linear')
        cls.clean = solve(clicks(), seeds=SEEDS)

    def test_clean_data_is_recovered_exactly_and_reports_the_loss(self):
        self.assertEqual(self.clean['loss'], 'soft_l1')
        self.assertEqual(self.plain['loss'], 'linear')
        self.assertEqual(self.clean['status'], 'fitted')
        self.assertLess(self.clean['rms_px'], 0.05)
        self.assertAlmostEqual(self.clean['dims_m']['W'], W, places=3)
        self.assertAlmostEqual(self.clean['dims_m']['H'], H, places=3)
        self.assertAlmostEqual(self.clean['free']['yf'], YF, places=3)
        self.assertLess(camera_error(self.clean)[0], 0.01)

    def test_robust_loss_agrees_with_plain_least_squares_on_noisy_but_outlier_free_clicks(self):
        # Residuals of about 1 px sit below the 2 px knee, so the two losses must agree to well within the noise.
        (ps, ls), (pp, lp) = camera_error(self.soft), camera_error(self.plain)
        self.assertLess(abs(ps - pp), 0.02)
        self.assertLess(abs(ls - lp), 0.05)
        for d in 'WH':
            self.assertLess(abs(self.soft['dims_m'][d] - self.plain['dims_m'][d]), 0.01)
        self.assertEqual(self.soft['status'], self.plain['status'])
        self.assertEqual(self.soft['status'], 'fitted')

    def test_an_unknown_loss_is_refused_not_silently_replaced(self):
        with self.assertRaisesRegex(ValueError, 'unknown loss'):
            solve(clicks(), seeds=2, loss='nonsense')

    def test_a_thirty_pixel_outlier_is_flagged_and_pulls_the_camera_less_than_plain_least_squares(self):
        data = move(clicks(noise=0.6, seed=4), 'tbr', 24.0, -18.0)                  # a 30 px mis-click
        soft = solve(data, seeds=SEEDS)
        plain = solve(data, seeds=SEEDS, loss='linear')
        self.assertEqual(soft['outliers'], ['tbr'])                                # named, and no innocent corner is blamed with it
        self.assertEqual(soft['worst_points'][0]['id'], 'tbr')
        self.assertGreater(soft['loo_errors_px']['tbr'], 20)
        (ps, ls), (pp, lp) = camera_error(soft), camera_error(plain)
        self.assertLess(ps, pp)
        self.assertLess(ls, lp)
        self.assertLess(ps, 0.7 * pp)
        # The click stays a large residual (it is named and left out, not absorbed), so the fit is not called good.
        self.assertGreater([q for q in soft['points'] if q['id'] == 'tbr'][0]['err_px'], 15)
        self.assertNotEqual(soft['status'], 'fitted')
        self.assertEqual(plain['dropped'], [])                                     # the plain path is the old fit, nothing dropped

    def test_the_named_click_is_left_out_of_the_final_fit(self):
        data = move(clicks(noise=0.6, seed=4), 'tbr', 24.0, -18.0)
        dropped = solve(data, seeds=SEEDS)
        kept = solve(data, seeds=SEEDS, drop_outliers=False)
        self.assertEqual(dropped['dropped'], ['tbr'])
        self.assertEqual(kept['dropped'], [])
        self.assertEqual(kept['outliers'], ['tbr'])                                # named either way
        self.assertTrue({q['id']: q['dropped'] for q in dropped['points']}['tbr'])
        self.assertFalse({q['id']: q['dropped'] for q in dropped['points']}['tfl'])
        self.assertLess(dropped['rms_kept_px'], 1.5)                               # the other clicks agree with the refit camera
        self.assertGreater(dropped['rms_px'], 4.0)                                 # rms_px still counts the dropped click
        self.assertLess(camera_error(dropped)[0], camera_error(kept)[0])
        self.assertTrue(any('refitted without tbr' in w for w in dropped['warnings']))


class FitFlagTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = flag(clicks(), 'mid_top_front', 'mid_floor_back', 'mid_top_back')
        cls.res = solve(cls.data, seeds=SEEDS)
        cls.without = solve({**clicks(), 'points': clicks()['points'][:9]}, seeds=SEEDS)

    def test_check_points_are_reported_not_fitted(self):
        r = self.res
        self.assertEqual((r['n_fit'], r['n_check']), (9, 3))
        self.assertEqual([c['id'] for c in r['check_points']], ['mid_top_front', 'mid_floor_back', 'mid_top_back'])
        self.assertTrue(all(c['err_px'] < 0.05 for c in r['check_points']))
        self.assertLess(r['check_median_px'], 0.05)
        self.assertLess(r['check_max_px'], 0.05)
        self.assertEqual(r['status'], 'fitted')
        self.assertEqual({q['id']: q['fit'] for q in r['points']}['mid_top_front'], False)
        self.assertEqual({q['id']: q['fit'] for q in r['points']}['tfl'], True)

    def test_check_points_do_not_enter_the_fit(self):
        # Same camera as a fit that never saw them, even though the clicks are present.
        a, b = self.res['camera'], self.without['camera']
        np.testing.assert_allclose(a['position'], b['position'], atol=1e-3)
        self.assertAlmostEqual(a['lens_mm'], b['lens_mm'], places=2)
        self.assertEqual(self.without['n_check'], 0)

    def test_a_bad_check_click_fails_the_check_but_leaves_the_fit_alone(self):
        data = move(copy.deepcopy(self.data), 'mid_top_back', 25.0, 10.0)
        r = solve(data, seeds=SEEDS)
        self.assertEqual(r['status'], 'check_failed')
        self.assertEqual(r['exit_code'], 3)
        self.assertLess(r['rms_px'], 0.05)                                       # the fit itself is fine
        self.assertGreater(r['check_max_px'], 20)
        self.assertEqual(max(r['check_points'], key=lambda c: c['err_px'])['id'], 'mid_top_back')
        self.assertTrue(any('mid_top_back' in w for w in r['warnings']))
        self.assertTrue(r['hint'])
        self.assertIsNone(r['ambiguity_hint'])
        np.testing.assert_allclose(r['camera']['position'], self.res['camera']['position'], atol=1e-3)
        # rms_px, max_px and worst_points describe the fitted points only.
        self.assertEqual(r['max_px'], self.res['max_px'])
        self.assertNotIn('mid_top_back', [w['id'] for w in r['worst_points']])

    def test_a_bad_fit_stays_high_residual_even_with_a_bad_check_point(self):
        data = move(move(copy.deepcopy(self.data), 'tfl', 60.0, -45.0), 'mid_top_back', 25.0, 10.0)
        r = solve(data, seeds=SEEDS)
        self.assertEqual(r['status'], 'high_residual')
        self.assertEqual(r['exit_code'], 3)

    def test_fit_flag_accepts_true_null_and_absence_identically(self):
        base = clicks(8)
        variants = []
        for value in ('absent', None, True):
            d = copy.deepcopy(base)
            if value != 'absent':
                for p in d['points']:
                    p['fit'] = value
            variants.append(solve(d, seeds=8))
        for r in variants[1:]:
            self.assertEqual(r['camera'], variants[0]['camera'])
            self.assertEqual(r['rms_px'], variants[0]['rms_px'])
            self.assertEqual(r['n_fit'], 8)
            self.assertEqual(r['n_check'], 0)

    def test_fit_flag_must_be_boolean_or_null(self):
        d = clicks(8)
        d['points'][0]['fit'] = 'no'
        with self.assertRaisesRegex(ValueError, 'fit must be true, false or null'):
            solve(d, seeds=2)

    def test_check_points_do_not_count_towards_the_minimum(self):
        d = flag(clicks(8), 'tbr', 'fbr', 'tfr', 'wall_top')                    # 4 fitted points left
        with self.assertRaisesRegex(ValueError, 'UNDERDETERMINED'):
            solve(d, seeds=2)
        d = flag(clicks(8), 'tbr', 'fbr', 'tfr', 'wall_top', 'wall_bottom')       # 3 fitted points left
        with self.assertRaisesRegex(ValueError, 'at least 4 fitted points'):
            solve(d, seeds=2)

    def test_a_check_point_cannot_anchor_the_scale(self):
        d = clicks(8)
        d['known'] = {}
        d['points'][3]['xyz'] = ['-W/2', '-D/2', 0.5]                            # numeric z on a fitted point would anchor it
        d['points'][3]['fit'] = False
        with self.assertRaisesRegex(ValueError, 'NO_SCALE_ANCHOR'):
            solve(d, seeds=2)

    def test_check_point_on_a_name_no_fitted_point_constrains_is_skipped_with_a_warning(self):
        d = clicks(12)
        u, v, _ = camera_math.project([0.5, 0.2, 1.05], CAM, *SIZE)
        d['points'].append({'id': 'counter_top', 'uv_px': [u, v], 'xyz': ['?cx', '?cy', 1.05], 'fit': False})
        r = solve(d, seeds=SEEDS)
        self.assertEqual(r['n_check'], 0)
        self.assertEqual(r['check_points'], [])
        self.assertTrue(any('counter_top' in w and 'no fitted point constrains' in w for w in r['warnings']))
        self.assertNotIn('counter_top', [q['id'] for q in r['points']])


class LeaveOneOutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.noisy = solve(clicks(noise=0.6, seed=9), seeds=SEEDS)
        cls.clean = solve(clicks(), seeds=SEEDS)

    def test_every_fitted_point_has_a_held_out_error(self):
        r = self.noisy
        self.assertEqual(sorted(r['loo_errors_px']), sorted(n for n, _ in CORNERS))
        self.assertAlmostEqual(r['loo_median_px'], float(np.median(list(r['loo_errors_px'].values()))), places=2)
        self.assertAlmostEqual(r['loo_max_px'], max(r['loo_errors_px'].values()), places=2)
        # Out of sample, so never better than the in-sample error by construction of the noise, and still small here.
        self.assertGreater(r['loo_median_px'], r['rms_px'] * 0.5)
        self.assertLess(r['loo_max_px'], 4.0)
        self.assertEqual(r['outliers'], [])
        self.assertEqual(r['status'], 'fitted')

    def test_noise_free_clicks_are_predicted_exactly(self):
        self.assertLess(self.clean['loo_max_px'], 0.05)
        self.assertEqual(self.clean['outliers'], [])

    def test_skipped_when_too_few_corners_remain_and_says_why(self):
        # 6 fitted points = 12 equations for 10 unknowns: a held-out point would be ill-conditioned, so nothing is reported.
        r = solve(flag(clicks(8), 'tbr', 'fbr'), seeds=SEEDS)
        self.assertEqual((r['n_fit'], r['n_unknown']), (6, 10))
        self.assertFalse(r['loo_reliable'])
        self.assertIsNone(r['loo_errors_px'])
        self.assertIsNone(r['loo_median_px'])
        self.assertIsNone(r['loo_max_px'])
        self.assertEqual(r['outliers'], [])
        self.assertEqual(r['status'], 'fitted')
        self.assertTrue(any('leave-one-out is skipped' in w and 'at least' in w for w in r['warnings']), r['warnings'])

    def test_no_check_points_gives_null_check_statistics(self):
        self.assertEqual(self.clean['n_check'], 0)
        self.assertEqual(self.clean['check_points'], [])
        self.assertIsNone(self.clean['check_median_px'])
        self.assertIsNone(self.clean['check_max_px'])

    def test_a_point_that_alone_fixes_a_free_value_is_not_held_out(self):
        d = clicks(12)
        u, v, _ = camera_math.project([0.5, 0.2, 1.05], CAM, *SIZE)
        d['points'].append({'id': 'counter_top', 'uv_px': [u, v], 'xyz': ['?cx', '?cy', 1.05]})
        r = solve(d, seeds=SEEDS)
        self.assertEqual(r['status'], 'fitted')
        self.assertNotIn('counter_top', r['loo_errors_px'])
        self.assertEqual(len(r['loo_errors_px']), 12)

    def test_a_point_that_alone_anchors_the_scale_is_not_held_out(self):
        d = clicks(12)
        d['known'] = {}                                                          # D is now fitted too
        u, v, _ = camera_math.project([-W / 2, -D / 2, 0.5], CAM, *SIZE)
        d['points'][3].update(uv_px=[u, v], xyz=['-W/2', '-D/2', 0.5])           # the only numeric coordinate: it fixes the scale
        r = solve(d, seeds=SEEDS)
        self.assertNotIn('ffl', r['loo_errors_px'])
        self.assertEqual(len(r['loo_errors_px']), 11)
        self.assertAlmostEqual(r['dims_m']['D'], D, delta=0.05)

    def test_outlier_rule(self):
        """detect_outliers on stubbed hold-out results: {id: (error of the held-out point, rms of the points that were fitted)}."""
        class Pb:
            fit_idx = [0, 1, 2, 3, 4]
            points = [{'id': k} for k in 'abcde']

        def run(first, *confirm):
            with mock.patch.object(box_fit, 'hold_out_each', side_effect=list(confirm)):
                return box_fit.detect_outliers(Pb, None, 'soft_l1', 2.0, first)
        quiet = {'a': (1.0, 2.0), 'b': (2.0, 2.0), 'c': (1.5, 2.0), 'd': (2.5, 2.0), 'e': (1.2, 2.0)}
        self.assertEqual(run(quiet), [])                                              # nothing above the floor: nothing to confirm
        # One bad click. Its victims have large errors too, but a larger rms of the rest; the click is the suspect, and with it out
        # the others are predicted well, which confirms it.
        first = {'c': (30.0, 1.0), 'a': (12.0, 9.0), 'b': (13.0, 9.0), 'd': (14.0, 9.0), 'e': (7.0, 9.0)}
        after = {'a': (1.0, 1.0), 'b': (1.5, 1.0), 'd': (1.2, 1.0), 'e': (1.1, 1.0)}
        self.assertEqual(run(first, after), ['c'])
        # Errors that are large for everybody (noise, a model that does not fit): the suspect is not confirmed, nobody is named.
        noisy = {'a': (12.0, 5.0), 'b': (14.0, 5.0), 'c': (16.0, 5.0), 'd': (15.0, 5.0), 'e': (13.0, 5.0)}
        self.assertEqual(run(noisy, {'a': (13.0, 5.0), 'b': (15.0, 5.0), 'd': (14.0, 5.0), 'e': (12.0, 5.0)}), [])
        # Too few equations left to hold anybody out again: the suspect cannot be confirmed, so it is not named.
        self.assertEqual(run(first, {}), [])
        # Two bad clicks are found one after the other, the one that leaves the others agreeing best first.
        two = {'a': (40.0, 20.0), 'b': (35.0, 18.0), 'c': (9.0, 25.0), 'd': (10.5, 25.0), 'e': (9.5, 25.0)}
        step1 = {'a': (40.0, 1.0), 'c': (9.0, 20.0), 'd': (8.0, 20.0), 'e': (9.5, 20.0)}
        step2 = {'c': (1.0, 1.0), 'd': (1.4, 1.0), 'e': (1.1, 1.0)}
        self.assertEqual(run(two, step1, step2), ['b', 'a'])

    def test_the_floor_and_the_factor_apply_to_the_suspect(self):
        class Pb:
            fit_idx = [0, 1, 2, 3]
            points = [{'id': k} for k in 'abcd']
        err = box_fit.OUTLIER_MIN_PX + 1.0
        first = {'a': (err, 0.5), 'b': (3.0, 9.0), 'c': (3.0, 9.0), 'd': (3.0, 9.0)}

        def run(first, scale):
            others = {k: (scale, 0.5) for k in 'bcd'}
            with mock.patch.object(box_fit, 'hold_out_each', side_effect=[others, others]):
                return box_fit.detect_outliers(Pb, None, 'soft_l1', 2.0, first)
        self.assertEqual(run(first, 0.5 * err / box_fit.OUTLIER_FACTOR), ['a'])           # far above what the others show
        self.assertEqual(run(first, 1.1 * err / box_fit.OUTLIER_FACTOR), [])              # not OUTLIER_FACTOR times the others' median
        self.assertEqual(run(dict(first, a=(box_fit.OUTLIER_MIN_PX - 0.5, 0.5)), 0.1), [])    # below the floor, however quiet the rest


class ConditioningTests(unittest.TestCase):
    """Leave-one-out is computed, and a culprit named, only when enough equations are left over."""

    def test_reliability_follows_the_spare_equation_rule(self):
        for n in (6, 7, 8, 9, 10):
            r = solve(clicks(n), seeds=SEEDS)
            expected = 2 * (n - 1) >= r['n_unknown'] + box_fit.LOO_MARGIN
            self.assertEqual(r['loo_reliable'], expected, (n, r['n_unknown']))
            self.assertEqual(n >= r['limits']['loo_min_fit'], expected)
            self.assertEqual(r['limits']['loo_min_fit'], box_fit.loo_min_fit(r['n_unknown']))
            if expected:
                self.assertEqual(len(r['loo_errors_px']), n)
            else:
                self.assertEqual((r['loo_errors_px'], r['loo_median_px'], r['loo_max_px'], r['outliers']), (None, None, None, []))
                self.assertTrue(any('leave-one-out is skipped' in w and 'at least %d' % r['limits']['loo_min_fit'] in w for w in r['warnings']),
                                r['warnings'])
            if n == 10:
                self.assertEqual(r['n_unknown'], 10)                                   # D given, W and H fitted, one ?name

    def test_eight_corners_are_enough_when_no_name_is_free(self):
        # D given: 7 camera unknowns + W + H = 9, so 8 corners leave 5 spare equations after one is held out.
        r8 = solve(clicks(8, corners=PLAIN_CORNERS, noise=0.5, seed=1), seeds=SEEDS)
        r7 = solve(clicks(7, corners=PLAIN_CORNERS, noise=0.5, seed=1), seeds=SEEDS)
        self.assertEqual((r8['n_unknown'], r8['loo_reliable'], r7['loo_reliable']), (9, True, False))
        self.assertEqual(len(r8['loo_errors_px']), 8)
        self.assertEqual(box_fit.loo_min_fit(9), 8)

    def test_the_margin_is_the_knob(self):
        data = clicks(7, corners=PLAIN_CORNERS, noise=0.5, seed=1)
        with mock.patch.object(box_fit, 'LOO_MARGIN', 2):                              # 12 equations for 9 unknowns would be accepted
            r = solve(data, seeds=SEEDS)
        self.assertTrue(r['loo_reliable'])
        self.assertEqual(r['limits']['loo_margin_equations'], 2)
        self.assertFalse(solve(data, seeds=SEEDS)['loo_reliable'])

    def test_a_mis_click_with_too_few_corners_is_not_called_fitted_and_nobody_is_named(self):
        r = solve(move(clicks(7, corners=PLAIN_CORNERS, noise=0.3, seed=2), 'tbr', 24.0, -18.0), seeds=SEEDS)
        self.assertFalse(r['loo_reliable'])
        self.assertEqual(r['outliers'], [])
        self.assertEqual(r['dropped'], [])
        self.assertIsNone(r['loo_errors_px'])
        self.assertNotEqual(r['status'], 'fitted')                                     # the residual still shows it
        self.assertTrue(any('leave-one-out is skipped' in w for w in r['warnings']))

    def test_a_thirty_pixel_mis_click_among_eight_corners_is_named_and_innocents_are_not(self):
        hits, false = 0, 0
        for k, (name, _) in enumerate(PLAIN_CORNERS[:8]):
            r = solve(move(clicks(8, corners=PLAIN_CORNERS, noise=0.6, seed=20 + k), name, 24.0, -18.0), seeds=SEEDS)
            self.assertTrue(r['loo_reliable'])
            hits += r['outliers'] == [name]
            false += len(set(r['outliers']) - {name})
        self.assertGreaterEqual(hits, 7, hits)                                         # 8 culprits: at least 90 % named exactly
        self.assertEqual(false, 0)

    def test_noise_alone_names_nobody(self):
        for seed in range(4):
            r = solve(clicks(8, corners=PLAIN_CORNERS, noise=0.8, seed=40 + seed), seeds=SEEDS)
            self.assertEqual(r['outliers'], [], seed)
            self.assertEqual(r['status'], 'fitted', seed)


class LimitsTests(unittest.TestCase):
    """One place for the limits: the constants, and the output carries the ones it used."""

    def test_limits_match_the_module_constants(self):
        r = solve(clicks(10), seeds=SEEDS)
        lim = r['limits']
        for key, const in (('rms_px', 'RMS_LIMIT_PX'), ('check_median_px', 'CHECK_MEDIAN_LIMIT_PX'), ('check_max_px', 'CHECK_MAX_LIMIT_PX'),
                           ('loo_median_px', 'LOO_MEDIAN_LIMIT_PX'), ('loo_max_px', 'LOO_MAX_LIMIT_PX'), ('outlier_min_px', 'OUTLIER_MIN_PX')):
            self.assertAlmostEqual(lim[key], getattr(box_fit, const), msg=key)
        self.assertEqual(lim['size_scale'], 1.0)
        self.assertEqual(lim['ref_long_side_px'], box_fit.REF_LONG_SIDE_PX)
        self.assertEqual(lim['outlier_factor'], box_fit.OUTLIER_FACTOR)
        self.assertEqual(lim['loo_margin_equations'], box_fit.LOO_MARGIN)
        self.assertEqual(lim['scale_ratio_tolerance'], box_fit.SCALE_TOLERANCE)
        self.assertEqual(lim['n_unknown'], r['n_unknown'])

    def test_limits_grow_with_a_large_picture_and_never_shrink(self):
        small = solve(clicks(10, size=(400, 225)), seeds=SEEDS)['limits']
        base = solve(clicks(10), seeds=SEEDS)['limits']
        big = solve(clicks(10, size=(3200, 1800)), seeds=SEEDS)['limits']
        self.assertEqual(small, dict(base))                                            # a small picture does not tighten anything
        self.assertEqual(big['size_scale'], 2.0)
        for key in ('rms_px', 'check_median_px', 'check_max_px', 'loo_median_px', 'loo_max_px', 'outlier_min_px'):
            self.assertAlmostEqual(big[key], 2 * base[key], msg=key)
        self.assertEqual(big['outlier_factor'], base['outlier_factor'])
        self.assertEqual(box_fit.size_scale(1600, 900), 1.0)
        self.assertEqual(box_fit.size_scale(900, 3200), 2.0)                           # the long side counts, whichever it is

    def test_a_click_error_that_is_ordinary_in_a_large_picture_is_not_named_in_a_small_one_it_is(self):
        # 15 px off: a gross error in a 738 px picture, about 3.5 px worth in a 3200 px one (limits scale with the long side).
        small = solve(move(clicks(12, noise=0.3, seed=2), 'tbr', 12.0, -9.0), seeds=SEEDS)
        scale = 3200 / SIZE[0]
        big = solve(move(clicks(12, noise=0.3 * scale, seed=2, size=(3200, 1800)), 'tbr', 12.0, -9.0), seeds=SEEDS)
        self.assertEqual(small['outliers'], ['tbr'])
        self.assertNotEqual(small['status'], 'fitted')
        self.assertEqual(big['outliers'], [])
        self.assertEqual(big['status'], 'fitted')

    def test_scale_checks_are_structured_and_keep_their_tolerance(self):
        data = clicks(10)
        u0, v0, _ = camera_math.project([1.0, -0.5, 0.0], CAM, *SIZE)
        u1, v1, _ = camera_math.project([1.0, -0.5, 0.9], CAM, *SIZE)
        data['scale_checks'] = [{'id': 'stool', 'base_px': [u0, v0], 'top_px': [u1, v1], 'expected_height_m': 0.9, 'tolerance': 0.05},
                                {'id': 'wrong', 'base_px': [u0, v0], 'top_px': [u1, v1], 'expected_height_m': 0.6}]
        r = solve(data, seeds=SEEDS)
        good, wrong = r['scale_checks']
        self.assertEqual(sorted(good), sorted(['id', 'expected_m', 'measured_m', 'ratio', 'tolerance', 'miss_m', 'ok']))
        self.assertEqual((good['id'], good['expected_m'], good['tolerance'], good['ok']), ('stool', 0.9, 0.05, True))
        self.assertAlmostEqual(good['measured_m'], 0.9, delta=0.02)
        self.assertEqual((wrong['id'], wrong['ok'], wrong['tolerance']), ('wrong', False, r['limits']['scale_ratio_tolerance']))
        self.assertGreater(wrong['ratio'], 1.4)
        self.assertTrue(any('scale check wrong' in w for w in r['warnings']))
        self.assertEqual(r['status'], 'fitted')                                        # a wrong scale check is a warning, not a fit status


# Exact corners of a synthetic wide-angle picture that was cropped (principal point 2 % of the width off the centre), 8 clicks,
# W given. The centred camera fits them to about 3 px; the extreme top corner is then predicted 24 px off by the others.
OFF_CENTRE = {"image": {"width": 1167, "height": 595}, "known": {"W": 3.0}, "assumed": [], "floor_z": 0.0, "points": [
    {"id": "floor_front_left", "uv_px": [334.29, 538.52], "xyz": ["-W/2", "-D/2", "F"]},
    {"id": "floor_front_right", "uv_px": [821.75, 578.16], "xyz": ["W/2", "-D/2", "F"]},
    {"id": "floor_back_left", "uv_px": [459.96, 264.84], "xyz": ["-W/2", "D/2", "F"]},
    {"id": "top_front_left", "uv_px": [192.07, 183.66], "xyz": ["-W/2", "-D/2", "H"]},
    {"id": "top_front_right", "uv_px": [944.42, 217.59], "xyz": ["W/2", "-D/2", "H"]},
    {"id": "top_back_right", "uv_px": [816.31, 8.28], "xyz": ["W/2", "D/2", "H"]},
    {"id": "top_back_mid", "uv_px": [613.77, 3.23], "xyz": ["0", "D/2", "H"]},
    {"id": "floor_front_mid", "uv_px": [571.99, 557.85], "xyz": ["0", "-D/2", "F"]}]}


class ModelMismatchTests(unittest.TestCase):
    """The principal point stays at the centre of the picture; a small offset must not look like bad clicks."""

    def test_an_extreme_corner_that_the_others_predict_loosely_is_not_blamed_for_it(self):
        r = solve(copy.deepcopy(OFF_CENTRE), seeds=SEEDS)
        raw = {k: v * r['loo_inflation'][k] for k, v in r['loo_errors_px'].items()}
        self.assertGreater(raw['top_front_right'], 15)                                 # predicted far off ...
        self.assertGreater(r['loo_inflation']['top_front_right'], 2.5)                 # ... because it lies where the others predict poorly
        self.assertLess(r['loo_errors_px']['top_front_right'], box_fit.OUTLIER_MIN_PX)           # scaled by that, it is under the floor
        self.assertEqual(r['outliers'], [])
        self.assertEqual(r['status'], 'fitted')
        self.assertGreaterEqual(min(r['loo_inflation'].values()), 1.0)

    def test_a_one_to_two_percent_principal_point_offset_is_not_flagged(self):
        cam = dict(CAM, principal_point=[0.52, 0.51])
        for n, corners, seed in ((10, CORNERS, 1), (12, CORNERS, 2), (8, PLAIN_CORNERS, 3)):
            r = solve(clicks(n, cam=cam, noise=0.5, seed=seed, corners=corners), seeds=SEEDS)
            self.assertEqual(r['status'], 'fitted', (n, r['rms_px'], r['loo_median_px'], r['outliers']))
            self.assertEqual(r['outliers'], [], n)
            self.assertLess(r['rms_px'], 0.5 * r['limits']['rms_px'], n)


class CliTests(unittest.TestCase):
    def run_cli(self, data, *extra):
        with tempfile.TemporaryDirectory() as d:
            points, out = Path(d) / 'points.json', Path(d) / 'box_fit.json'
            points.write_text(json.dumps(data), encoding='utf-8')
            r = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts' / 'box_fit.py'), '--points', str(points), '--output', str(out),
                                '--seeds', '8', *extra], capture_output=True, text=True, timeout=300)
            return r, json.loads(out.read_text(encoding='utf-8'))

    def test_stdout_is_one_json_object_equal_to_the_output_file(self):
        r, written = self.run_cli(clicks(10, noise=0.4, seed=2))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads(r.stdout), written)                              # nothing else on stdout
        self.assertEqual(len(r.stdout.strip().splitlines()), 1)
        self.assertEqual(written['status'], 'fitted')
        self.assertEqual(written['exit_code'], 0)
        for key in ('loss', 'n_fit', 'n_check', 'n_unknown', 'check_points', 'check_median_px', 'check_max_px', 'loo_reliable', 'loo_errors_px',
                    'loo_median_px', 'loo_max_px', 'outliers', 'dropped', 'limits', 'scale_checks', 'warnings', 'hint', 'ambiguity_hint',
                    'camera', 'dims_m'):
            self.assertIn(key, written)
        self.assertTrue(written['loo_reliable'])
        self.assertEqual(written['limits']['size_scale'], 1.0)
        self.assertEqual(written['scale_checks'], [])

    def test_warnings_and_hints_travel_inside_the_json_and_the_exit_code_says_needs_attention(self):
        r, written = self.run_cli(move(clicks(10), 'tfl', 60.0, -45.0))
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertEqual(json.loads(r.stdout), written)
        self.assertEqual(written['status'], 'high_residual')
        self.assertEqual(written['exit_code'], 3)
        self.assertTrue(written['warnings'])
        self.assertNotIn('WARNING:', r.stdout)
        self.assertNotIn('HINT:', r.stdout)

    def test_check_failed_exits_with_three(self):
        r, written = self.run_cli(move(flag(clicks(12), 'mid_top_back'), 'mid_top_back', 25.0, 10.0))
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertEqual(written['status'], 'check_failed')
        self.assertEqual(written['n_check'], 1)

    def test_blocked_input_is_still_one_json_object(self):
        data = clicks(8)
        data['known'] = {}
        r, written = self.run_cli(data)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertEqual(json.loads(r.stdout), written)
        self.assertEqual(written['status'], 'blocked')
        self.assertIn('NO_SCALE_ANCHOR', written['reason'])
        self.assertEqual(written['exit_code'], 2)

    def test_named_clicks_are_dropped_unless_asked_otherwise(self):
        data = move(clicks(12, noise=0.4, seed=2), 'tbr', 24.0, -18.0)
        r, written = self.run_cli(data)
        self.assertEqual((written['outliers'], written['dropped']), (['tbr'], ['tbr']))
        r, kept = self.run_cli(data, '--keep-outliers')
        self.assertEqual((kept['outliers'], kept['dropped']), (['tbr'], []))
        r, plain = self.run_cli(data, '--loss', 'linear')
        self.assertEqual(plain['dropped'], [])                                           # the plain path is the old fit

    def test_plain_loss_can_be_chosen_on_the_command_line(self):
        r, written = self.run_cli(clicks(8), '--loss', 'linear')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(written['loss'], 'linear')


class StatusLadderTests(unittest.TestCase):
    def test_thresholds_are_named_constants_with_the_documented_ordering(self):
        self.assertEqual(box_fit.LOSS, 'soft_l1')
        self.assertGreater(box_fit.OUTLIER_MIN_PX, box_fit.F_SCALE_PX)
        self.assertGreaterEqual(box_fit.CHECK_MAX_LIMIT_PX, box_fit.CHECK_MEDIAN_LIMIT_PX)
        self.assertGreaterEqual(box_fit.LOO_MEDIAN_LIMIT_PX, box_fit.RMS_LIMIT_PX)
        self.assertGreaterEqual(box_fit.LOO_MAX_LIMIT_PX, box_fit.LOO_MEDIAN_LIMIT_PX)
        self.assertGreater(box_fit.LOO_MARGIN, box_fit.LOO_MIN_SPARE)
        self.assertGreaterEqual(box_fit.OUTLIER_MIN_PX, box_fit.CHECK_MEDIAN_LIMIT_PX)

    def test_ambiguous_wins_over_check_failed(self):
        data = move(flag(clicks(12), 'mid_top_back'), 'mid_top_back', 25.0, 10.0)
        self.assertEqual(solve(data, seeds=SEEDS)['status'], 'check_failed')
        with mock.patch.object(box_fit, 'ambiguous_dims', return_value=['W']):
            r = solve(data, seeds=SEEDS)
        self.assertEqual(r['status'], 'ambiguous')
        self.assertEqual(r['exit_code'], 3)
        self.assertEqual(r['ambiguous_dims'], ['W'])
        self.assertEqual(r['hint'], r['ambiguity_hint'])
        self.assertIn('W', r['hint'])

    def test_a_fit_with_only_mild_check_errors_is_still_fitted(self):
        # A 4 px check error is ordinary click noise, well below the limits.
        r = solve(move(flag(clicks(12), 'mid_top_back'), 'mid_top_back', 3.0, 2.5), seeds=SEEDS)
        self.assertEqual(r['status'], 'fitted')
        self.assertAlmostEqual(r['check_points'][0]['err_px'], 3.9, delta=0.3)
        self.assertEqual(r['warnings'], [])
        self.assertIsNone(r['hint'])


if __name__ == '__main__':
    unittest.main()
