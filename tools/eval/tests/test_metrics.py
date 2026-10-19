import copy
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import camgeom
import metrics
import synth_generate as sg

CASE, _SCENE = sg.build_case(20261019, 4)             # inline, 1920 x 1080, a few parts
W, D, H = (CASE['dims_m'][k] for k in 'WDH')


def perfect_fit():
    return {'status': 'fitted', 'camera': copy.deepcopy(CASE['camera']), 'dims_m': dict(CASE['dims_m']), 'rms_px': 0.1, 'max_px': 0.2,
            'points': [{'id': c['id'], 'err_px': 0.1} for c in CASE['corners']]}


def turned_camera(deg):
    """The true camera turned about the vertical axis through the footprint centre."""
    a = math.radians(deg)
    cam = copy.deepcopy(CASE['camera'])
    for key in ('position', 'target'):
        x, y, z = cam[key]
        cam[key] = [x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a), z]
    return cam


class CornerAndCamera(unittest.TestCase):
    def test_a_perfect_fit_has_no_error(self):
        fit = perfect_fit()
        ce = metrics.corner_error(CASE, fit['camera'], fit['dims_m'])
        self.assertAlmostEqual(ce['px'], 0.0, places=6)
        self.assertEqual(ce['symmetry'], [1, 1])
        cam = metrics.camera_errors(CASE, fit['camera'])
        self.assertAlmostEqual(cam['fov_err_deg'], 0.0, places=6)
        self.assertAlmostEqual(cam['rot_err_deg'], 0.0, places=4)
        self.assertAlmostEqual(cam['focal_rel_err'], 0.0, places=9)

    def test_best_symmetric_relabelling_is_used(self):
        cam = turned_camera(180.0)                  # the box looks the same, only the labels are swapped front to back and left to right
        ce = metrics.corner_error(CASE, cam, CASE['dims_m'])
        self.assertAlmostEqual(ce['px'], 0.0, places=4)
        self.assertEqual(ce['symmetry'], [-1, -1])
        self.assertGreater(metrics.camera_errors(CASE, cam)['rot_err_deg'], 170.0)       # the rotation error does not forgive the swap

    def test_error_is_a_share_of_the_image_diagonal(self):
        fit = perfect_fit()
        fit['dims_m']['W'] = W * 1.1
        ce = metrics.corner_error(CASE, fit['camera'], fit['dims_m'])
        diag = math.hypot(CASE['image']['width'], CASE['image']['height'])
        self.assertGreater(ce['px'], 1.0)
        self.assertAlmostEqual(ce['diag_frac'], ce['px'] / diag, places=12)

    def test_field_of_view_and_rotation_errors(self):
        cam = copy.deepcopy(CASE['camera'])
        cam['lens_mm'] *= 1.2
        err = metrics.camera_errors(CASE, cam)
        self.assertAlmostEqual(err['focal_rel_err'], 0.2, places=6)
        self.assertAlmostEqual(err['fov_err_deg'], abs(camgeom.hfov_deg(cam) - camgeom.hfov_deg(CASE['camera'])), places=9)
        self.assertAlmostEqual(metrics.camera_errors(CASE, turned_camera(4.0))['rot_err_deg'], 4.0, delta=0.5)

    def test_missing_pieces_give_none(self):
        self.assertIsNone(metrics.corner_error(CASE, None, CASE['dims_m']))
        self.assertIsNone(metrics.corner_error(CASE, CASE['camera'], {'W': 3.0}))
        self.assertIsNone(metrics.camera_errors(CASE, None))
        self.assertIsNone(metrics.ratio_errors(CASE, {'W': 3.0, 'D': 3.0}))


class ScaleErrors(unittest.TestCase):
    def test_log_ratio_errors_are_signed_and_scale_free(self):
        dims = {'W': W * 1.5, 'D': D * 1.5, 'H': H * 1.5}                      # a pure scale error: proportions are right
        r = metrics.ratio_errors(CASE, dims)
        self.assertAlmostEqual(r['log_wh'], 0.0, places=12)
        self.assertAlmostEqual(r['log_dh'], 0.0, places=12)
        r = metrics.ratio_errors(CASE, {'W': W * 1.1, 'D': D, 'H': H})
        self.assertAlmostEqual(r['log_wh'], math.log(1.1), places=12)
        self.assertAlmostEqual(r['log_dh'], 0.0, places=12)
        r = metrics.ratio_errors(CASE, {'W': W, 'D': D, 'H': H * 1.25})
        self.assertAlmostEqual(r['log_dh'], -math.log(1.25), places=12)


def make_spec(offsets):
    """A spec holding a body box and a top slab for each case part, moved by offsets[part id] metres along x."""
    comps = []
    for p in CASE['parts']:
        x, y = p['center_m']
        x += offsets.get(p['id'], 0.0)
        comps.append({'id': p['id'] + '_body', 'kind': 'box', 'location': [x, y, 0.5], 'rotation_deg': [0, 0, 0], 'params': {'size': [0.6, 0.6, 1.0]}})
        comps.append({'id': p['id'] + '_top', 'kind': 'box', 'location': [x, y, 1.0], 'rotation_deg': [0, 0, 0], 'params': {'size': [0.64, 0.64, 0.04]}})
    comps.append({'id': 'wall_back', 'kind': 'box', 'location': [0, 3, 1], 'rotation_deg': [0, 0, 0], 'params': {'size': [3, 0.1, 2]}})
    return {'components': comps}


class Placement(unittest.TestCase):
    def test_exact_positions_have_no_error(self):
        pl = metrics.placement_errors(CASE, make_spec({}))
        self.assertAlmostEqual(pl['median_m'], 0.0, places=9)
        self.assertEqual(pl['missing'], 0)

    def test_offsets_are_measured_in_metres_and_as_a_share_of_w(self):
        offsets = {p['id']: 0.3 for p in CASE['parts']}
        pl = metrics.placement_errors(CASE, make_spec(offsets))
        self.assertAlmostEqual(pl['median_m'], 0.3, places=9)
        self.assertAlmostEqual(pl['max_m'], 0.3, places=9)
        self.assertAlmostEqual(pl['share_w'], 0.3 / W, places=9)

    def test_a_dropped_part_is_counted_as_missing(self):
        spec = make_spec({})
        spec['components'] = [c for c in spec['components'] if not c['id'].startswith(CASE['parts'][0]['id'])]
        pl = metrics.placement_errors(CASE, spec)
        self.assertEqual(pl['missing'], 1)
        self.assertAlmostEqual(pl['median_m'], 0.0, places=9)

    def test_turned_and_tilted_pieces_are_handled(self):
        pid = CASE['parts'][0]['id']
        x, y = CASE['parts'][0]['center_m']
        spec = {'components': [
            {'id': pid + '_a', 'kind': 'box', 'location': [x, y, 0], 'rotation_deg': [0, 0, 90], 'params': {'size': [1.0, 0.2, 0.1]}},
            {'id': pid + '_grass_01', 'kind': 'cylinder', 'location': [x + 5, y + 5, 0], 'rotation_deg': [10, 0, 0], 'params': {'radius': 0.01, 'height': 0.3}}]}
        centres = metrics.part_centres(spec, [pid])
        self.assertAlmostEqual(centres[pid][0], x, places=9)             # the tilted blade far away is ignored
        self.assertAlmostEqual(centres[pid][1], y, places=9)


class Decision(unittest.TestCase):
    def test_gate_wins_over_status_and_failure_is_a_flag(self):
        fit = {'status': 'fitted'}
        self.assertEqual(metrics.gate_decision(fit, {'quality_gate': {'status': 'review'}}, False), (True, 'quality_gate'))
        self.assertEqual(metrics.gate_decision(fit, {'quality_gate': {'status': 'pass'}}, False), (False, 'quality_gate'))
        self.assertEqual(metrics.gate_decision({'status': 'high_residual'}, {}, False), (True, 'fit_status'))
        self.assertEqual(metrics.gate_decision(fit, None, False), (False, 'fit_status'))
        self.assertEqual(metrics.gate_decision(None, None, True), (True, 'no_fit'))

    def test_a_good_fit_is_not_flagged_and_not_bad(self):
        row = metrics.evaluate_run(CASE, None, perfect_fit(), None, make_spec({}), {'fit_s': 2.0, 'compile_s': 3.0})
        self.assertFalse(row['bad_fit'])
        self.assertFalse(row['flagged'])
        self.assertFalse(row['failed'])
        self.assertEqual(row['total_s'], 3.0)
        self.assertAlmostEqual(row['corner_err_diag'], 0.0, places=9)

    def test_a_wrong_fit_is_bad(self):
        fit = perfect_fit()
        fit['dims_m']['D'] = D * 1.3
        fit['status'] = 'high_residual'
        row = metrics.evaluate_run(CASE, None, fit, None, None)
        self.assertTrue(row['bad_fit'])
        self.assertTrue(row['flagged'])

    def test_no_usable_fit_is_a_failed_flagged_bad_run(self):
        row = metrics.evaluate_run(CASE, None, {'status': 'blocked', 'reason': 'x'}, None, None, failure='blocked')
        self.assertTrue(row['failed'] and row['flagged'] and row['bad_fit'])
        self.assertIsNone(row['corner_err_diag'])

    def test_outlier_naming_scores(self):
        a, b, c = (p['id'] for p in CASE['corners'][:3])
        pert = {'name': 'outlier_1', 'dims_mode': 'one', 'sigma_px': 0.5, 'injected': {'bad_ids': [a], 'outlier_ids': [a], 'mislabel_ids': []}}
        fit = perfect_fit()
        fit['outliers'] = [a, b]
        fit['points'][0]['err_px'] = 30.0
        row = metrics.evaluate_run(CASE, pert, fit, None, None)
        self.assertEqual(row['outlier_recall'], (1, 1))
        self.assertEqual(row['outlier_false_alarm'], (1, len(CASE['corners']) - 1))
        self.assertEqual(row['worst_hit'], 1.0)
        self.assertEqual(row['n_injected'], 1)
        self.assertIsNone(row['clean_outlier_report'])
        fit['outliers'] = []
        self.assertEqual(metrics.evaluate_run(CASE, pert, fit, None, None)['outlier_recall'], (0, 1))

    def test_a_skill_that_names_no_outliers_gets_no_outlier_score(self):
        pert = {'name': 'outlier_1', 'dims_mode': 'one', 'injected': {'bad_ids': [CASE['corners'][0]['id']]}}
        row = metrics.evaluate_run(CASE, pert, perfect_fit(), None, None)
        self.assertIsNone(row['outlier_recall'])
        self.assertIsNone(row['outlier_false_alarm'])

    def test_clean_run_that_names_a_corner_is_a_false_alarm(self):
        fit = perfect_fit()
        fit['outliers'] = [CASE['corners'][1]['id']]
        row = metrics.evaluate_run(CASE, {'name': 'clean', 'dims_mode': 'one', 'injected': {'bad_ids': []}}, fit, None, None)
        self.assertEqual(row['clean_outlier_report'], (1.0, 1.0))


def fake_rows(values, flagged, bad, clusters=None):
    rows = []
    for i, (v, f, b) in enumerate(zip(values, flagged, bad)):
        rows.append({'case_id': f'c{i if clusters is None else clusters[i]}', 'pert': 'x', 'corner_err_diag': v, 'flagged': f, 'bad_fit': b,
                     'failed': False, 'n_injected': 0, 'valid_input': True, 'fov_err_deg': None})
    return rows


class Aggregation(unittest.TestCase):
    def test_decision_ratios(self):
        rows = fake_rows([0.0] * 6, [True, True, False, False, True, False], [True, True, True, False, False, False])
        agg = metrics.aggregate(rows, resamples=50)
        self.assertAlmostEqual(agg['sensitivity']['value'], 2 / 3)
        self.assertAlmostEqual(agg['false_alarm_rate']['value'], 1 / 3)
        self.assertAlmostEqual(agg['decision_accuracy']['value'], 4 / 6)
        self.assertAlmostEqual(agg['flag_rate']['value'], 0.5)
        self.assertAlmostEqual(agg['flag_on_valid_input']['value'], 0.5)

    def test_missing_values_are_left_out(self):
        rows = fake_rows([0.01, None, 0.03], [False] * 3, [False] * 3)
        agg = metrics.aggregate(rows, resamples=50)
        self.assertEqual(agg['corner_err_diag_med']['n'], 2)
        self.assertAlmostEqual(agg['corner_err_diag_med']['value'], 0.02)
        self.assertEqual(agg['fov_err_deg_med']['n'], 0)
        self.assertIsNone(agg['fov_err_deg_med']['value'])

    def test_by_group_includes_all(self):
        rows = fake_rows([0.01, 0.02], [False, False], [False, False])
        rows[1]['pert'] = 'y'
        groups = metrics.aggregate_by(rows, 'pert', resamples=20)
        self.assertEqual(set(groups), {'all', 'x', 'y'})
        self.assertAlmostEqual(groups['y']['corner_err_diag_med']['value'], 0.02)

    def test_every_headline_metric_exists(self):
        keys = {m[0] for m in metrics.METRICS}
        self.assertTrue(set(metrics.HEADLINE) <= keys)
        self.assertTrue({m[1] for m in metrics.METRICS} <= set(metrics.LAYER_ORDER))


if __name__ == '__main__':
    unittest.main()
