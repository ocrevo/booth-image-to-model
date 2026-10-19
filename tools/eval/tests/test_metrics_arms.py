"""Harness 1.1 metrics: which injected corners can be named, recall by fitted points, absolute dimension errors, gate precision."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import metrics
import synth_generate as sg

CASE, _SCENE = sg.build_case(20261019, 4)
W, D, H = (CASE['dims_m'][k] for k in 'WDH')


def perfect_fit():
    return {'status': 'fitted', 'camera': copy.deepcopy(CASE['camera']), 'dims_m': dict(CASE['dims_m']), 'rms_px': 0.1, 'max_px': 0.2,
            'points': [{'id': c['id'], 'err_px': 0.1} for c in CASE['corners']]}


def check_fit(check_ids, outliers, worst=None):
    """A perfect fit in the newer format: the check corners are listed with fit: false and in check_points."""
    fit = perfect_fit()
    fit.update(n_check=len(check_ids), n_fit=len(CASE['corners']) - len(check_ids), outliers=list(outliers), loo_median_px=0.4,
               check_points=[{'id': i, 'err_px': 0.2} for i in check_ids])
    for p in fit['points']:
        p['fit'] = p['id'] not in check_ids
    if worst:
        next(p for p in fit['points'] if p['id'] == worst)['err_px'] = 30.0
    return fit


def fake_rows(flagged, bad):
    return [{'case_id': f'c{i}', 'pert': 'x', 'corner_err_diag': 0.0, 'flagged': f, 'bad_fit': b, 'failed': False, 'n_injected': 0,
             'valid_input': True} for i, (f, b) in enumerate(zip(flagged, bad))]


class OutlierNamingDefinitions(unittest.TestCase):
    """Only an injected corner that was fitted can be named by leave-one-out, so only those count in the recall."""

    def setUp(self):
        self.ids = [c['id'] for c in CASE['corners']]
        self.fitted, self.held = self.ids[0], self.ids[-1]

    def pert(self, bad, held):
        return {'name': 'outlier_1', 'dims_mode': 'one', 'sigma_px': 0.5, 'fit_flags': {i: False for i in held},
                'injected': {'bad_ids': [bad], 'outlier_ids': [bad], 'mislabel_ids': []}}

    def test_a_fitted_injected_corner_counts_in_both_definitions(self):
        fit = check_fit([self.held], [self.fitted], worst=self.fitted)
        row = metrics.evaluate_run(CASE, self.pert(self.fitted, [self.held]), fit, None, None)
        self.assertEqual(row['outlier_recall'], (1, 1))
        self.assertEqual(row['outlier_recall_all_injected'], (1, 1))
        self.assertEqual((row['n_injected_fitted'], row['n_injected_check']), (1, 0))
        self.assertIsNone(row['check_inj_flagged'])
        self.assertEqual(row['worst_hit'], 1.0)

    def test_a_held_out_injected_corner_cannot_be_named_and_is_reported_apart(self):
        fit = check_fit([self.held], [], worst=self.held)             # the held-out corner has the large error; nothing is named
        fit['status'] = 'check_failed'
        gate = {'quality_gate': {'status': 'review', 'reasons': ['x']}}
        row = metrics.evaluate_run(CASE, self.pert(self.held, [self.held]), fit, gate, None)
        self.assertIsNone(row['outlier_recall'])                       # nothing to name: not in the denominator at all
        self.assertEqual(row['outlier_recall_all_injected'], (0, 1))   # the harness 1.0 definition counts it as missed
        self.assertEqual((row['n_injected_fitted'], row['n_injected_check']), (0, 1))
        self.assertEqual(row['check_inj_flagged'], (1.0, 1.0))
        self.assertEqual(row['check_inj_check_failed'], (1.0, 1.0))
        self.assertIsNone(row['worst_hit'])                            # the worst of the FITTED points is not an injected one here
        self.assertEqual(row['worst_hit_all_points'], 1.0)             # the legacy definition sees the check corner

    def test_two_injected_corners_one_of_each_kind(self):
        pert = self.pert(self.fitted, [self.held])
        pert['injected'] = {'bad_ids': sorted([self.fitted, self.held]), 'outlier_ids': [self.fitted, self.held], 'mislabel_ids': []}
        row = metrics.evaluate_run(CASE, pert, check_fit([self.held], [self.fitted]), None, None)
        self.assertEqual(row['outlier_recall'], (1, 1))
        self.assertEqual(row['outlier_recall_all_injected'], (1, 2))

    def test_a_skill_without_check_corners_fitted_everything(self):
        # the harness sent fit: false, an older skill ignored it: the injected corner was fitted and counts
        fit = perfect_fit()
        fit['outliers'] = [self.held]
        row = metrics.evaluate_run(CASE, self.pert(self.held, [self.held]), fit, None, None)
        self.assertEqual(row['outlier_recall'], (1, 1))
        self.assertEqual(row['n_withheld'], 0)
        self.assertEqual(row['n_fit_eff'], len(CASE['corners']))

    def test_recall_when_leave_one_out_was_computed(self):
        fit = check_fit([], [])
        fit['loo_median_px'] = None                                     # a round-2 skill reports null when it is not reliable
        fit['loo_reliable'] = False
        row = metrics.evaluate_run(CASE, self.pert(self.fitted, []), fit, None, None)
        self.assertEqual(row['loo_computed'], 0.0)
        self.assertEqual(row['outlier_recall'], (0, 1))                 # nothing named is a miss ...
        self.assertIsNone(row['outlier_recall_computed'])               # ... but it is kept out of the "when computed" recall
        fit.update(loo_median_px=0.4, loo_reliable=True, outliers=[self.fitted])
        row = metrics.evaluate_run(CASE, self.pert(self.fitted, []), fit, None, None)
        self.assertEqual((row['loo_computed'], row['outlier_recall_computed']), (1.0, (1, 1)))
        old = metrics.evaluate_run(CASE, self.pert(self.fitted, []), perfect_fit(), None, None)
        self.assertIsNone(old['loo_computed'])                          # a skill without leave-one-out has no such column

    def test_fitted_point_bins(self):
        self.assertEqual([metrics.n_fit_bin(n) for n in (4, 6, 7, 8, 9, 12)], ['<=6', '<=6', '7', '8', '9+', '9+'])
        self.assertIsNone(metrics.n_fit_bin(None))
        row = metrics.evaluate_run(CASE, None, check_fit(CASE['check_ids'], []), None, None)
        self.assertEqual(row['n_fit_bin'], metrics.n_fit_bin(len(CASE['corners']) - len(CASE['check_ids'])))

    def test_recall_by_bin_cuts_by_perturbation_and_fitted_points(self):
        rows = []
        for i, (n, named) in enumerate([(8, 1), (8, 1), (8, 0), (7, 0), (7, 0)]):
            rows.append({'case_id': f'c{i}', 'pert': 'outlier_30', 'n_fit_bin': metrics.n_fit_bin(n), 'outlier_recall': (named, 1)})
        rows.append({'case_id': 'c9', 'pert': 'clean', 'n_fit_bin': '8', 'outlier_recall': None})
        table = metrics.recall_by_bin(rows, 'outlier_recall', resamples=20)
        self.assertEqual(list(table), ['outlier_30'])                    # only perturbations that have a recall at all
        self.assertAlmostEqual(table['outlier_30']['8']['value'], 2 / 3)
        self.assertAlmostEqual(table['outlier_30']['7']['value'], 0.0)
        self.assertAlmostEqual(table['outlier_30']['all']['value'], 2 / 5)
        self.assertIsNone(table['outlier_30']['9+']['value'])

    def test_the_arm_and_the_crop_are_recorded(self):
        row = metrics.evaluate_run(CASE, {'name': 'clean', 'check_corners': 0, 'dims_mode': 'one', 'injected': {'bad_ids': []}}, perfect_fit(), None, None)
        self.assertEqual(row['check_corners_arm'], 0)
        self.assertIn(row['crop'], ('cropped', 'full frame'))


class AbsoluteDimensionErrors(unittest.TestCase):
    def test_signed_relative_errors(self):
        err = metrics.absolute_errors(CASE, {'W': W * 1.25, 'D': D * 0.9, 'H': H})
        self.assertAlmostEqual(err['W'], 0.25)
        self.assertAlmostEqual(err['D'], -0.10)
        self.assertAlmostEqual(err['H'], 0.0)
        self.assertIsNone(metrics.absolute_errors(CASE, {'W': 3.0}))
        self.assertIsNone(metrics.absolute_errors(CASE, None))

    def test_a_wrong_scale_leaves_the_ratios_alone_but_not_the_metres(self):
        fit = perfect_fit()
        fit['dims_m'] = {k: v * 0.75 for k, v in CASE['dims_m'].items()}          # all three off by -25 %
        pert = {'name': 'wrong_anchor_m25', 'dims_mode': 'wrong_one', 'injected': {'bad_ids': []}}
        row = metrics.evaluate_run(CASE, pert, fit, None, None)
        self.assertAlmostEqual(row['abs_log_wh'], 0.0, places=12)
        self.assertAlmostEqual(row['abs_log_dh'], 0.0, places=12)
        for key in ('abs_w_err', 'abs_d_err', 'abs_h_err', 'abs_dim_err_max'):
            self.assertAlmostEqual(row[key], 0.25, places=9)
        self.assertAlmostEqual(row['w_err'], -0.25, places=9)
        self.assertTrue(row['bad_scale'])
        self.assertFalse(row['valid_input'])
        self.assertTrue(row['defective_input'])

    def test_really_bad_stays_scale_free(self):
        fit = perfect_fit()
        fit['dims_m'] = {k: v * 0.75 for k, v in CASE['dims_m'].items()}
        row = metrics.evaluate_run(CASE, {'name': 'wrong_anchor_m25', 'dims_mode': 'wrong_one', 'injected': {'bad_ids': []}}, fit, None, None)
        # the corner error of a uniformly scaled box seen from the true camera is not zero, but the ratio part of the label is unchanged
        self.assertEqual(row['bad_fit'], row['corner_err_diag'] > metrics.BAD_CORNER_DIAG)

    def test_ten_percent_is_not_yet_bad_scale(self):
        fit = perfect_fit()
        fit['dims_m'] = {k: v * 1.08 for k, v in CASE['dims_m'].items()}
        self.assertFalse(metrics.evaluate_run(CASE, None, fit, None, None)['bad_scale'])

    def test_aggregates_in_percent_and_flags_on_wrong_anchor(self):
        rows = []
        for i, (err, flagged) in enumerate([(0.25, True), (0.10, False), (0.30, True), (0.02, False)]):
            rows.append({'case_id': f'c{i}', 'pert': 'wrong_anchor_p25', 'dims_mode': 'wrong_one', 'abs_w_err': err, 'abs_d_err': err, 'abs_h_err': err,
                         'abs_dim_err_max': err, 'flagged': flagged, 'bad_fit': False, 'bad_scale': err > 0.10, 'defective_input': True,
                         'failed': False, 'n_injected': 0, 'valid_input': False})
        agg = metrics.aggregate(rows, resamples=20)
        self.assertAlmostEqual(agg['abs_w_err_med']['value'], 0.175)
        self.assertAlmostEqual(agg['flag_on_wrong_anchor']['value'], 0.5)
        self.assertAlmostEqual(agg['flag_on_bad_scale']['value'], 1.0)       # both runs that are off by more than 10 % were flagged
        self.assertAlmostEqual(agg['bad_scale_rate']['value'], 0.5)
        self.assertAlmostEqual(agg['flag_on_defective']['value'], 0.5)
        self.assertAlmostEqual(agg['defect_precision']['value'], 1.0)


class ScaleCheckColumns(unittest.TestCase):
    def test_scale_check_result_is_read_from_the_fit(self):
        fit = perfect_fit()
        fit['scale_checks'] = [{'id': 'cafe_table', 'measured_height_m': 0.55, 'expected_height_m': 0.74, 'ratio': 0.74, 'ok': False}]
        row = metrics.evaluate_run(CASE, None, fit, None, None)
        self.assertEqual((row['scale_check_n'], row['scale_check_ok'], row['scale_check_ratio']), (1, 0.0, 0.74))
        fit['scale_checks'] = [{'id': 'cafe_table', 'measured_m': 0.75, 'expected_m': 0.74, 'ratio': 1.01, 'ok': True}]       # the round-2 spelling
        self.assertEqual(metrics.evaluate_run(CASE, None, fit, None, None)['scale_check_ok'], 1.0)
        fit['scale_checks'] = []
        row = metrics.evaluate_run(CASE, None, fit, None, None)
        self.assertEqual(row['scale_check_n'], 0)
        self.assertIsNone(row['scale_check_ok'])


class GatePrecision(unittest.TestCase):
    def test_precision_is_the_share_of_flagged_runs_that_are_really_bad(self):
        rows = fake_rows([True, True, True, False, False, False], [True, False, False, True, False, False])
        self.assertAlmostEqual(metrics.aggregate(rows, resamples=20)['gate_precision']['value'], 1 / 3)

    def test_every_metric_key_is_unique_and_cuts_exist(self):
        keys = [m[0] for m in metrics.METRICS]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertTrue(set(metrics.CUT_KEYS) <= set(keys))
        agg = metrics.aggregate_by(fake_rows([True, False], [True, False]), 'pert', resamples=10, keys=['flag_rate'])
        self.assertEqual(set(agg['x']), {'flag_rate'})


if __name__ == '__main__':
    unittest.main()
