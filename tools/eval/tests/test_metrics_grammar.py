"""The booth grammar part of a metrics row and its aggregates (I2)."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import grammar
import metrics
import perturb
import synth_generate as sg

CASES = [sg.build_case(20261019, i)[0] for i in range(32)]
CASE = CASES[4]                                          # a back wall only: three open sides, a peninsula by the vocabulary
SENT = {'type_matches_sides': 'the booth type does not match the open sides', 'wall_on_open_side': 'a wall was placed on an open side and left out',
        'camera_side': 'the camera looks in through closed sides: front/back or left/right may be swapped'}


def perfect_fit(case):
    return {'status': 'fitted', 'camera': copy.deepcopy(case['camera']), 'dims_m': dict(case['dims_m']), 'rms_px': 0.1, 'max_px': 0.2,
            'points': [{'id': c['id'], 'err_px': 0.1} for c in case['corners']]}


def spec_for(case, wall_planes=None):
    """A spec with the floor and the case's walls (or only the planes given) on their edges, like the compiler's."""
    W, D = case['dims_m']['W'], case['dims_m']['D']
    comps = [{'id': 'floor', 'kind': 'box', 'location': [0, 0, 0], 'rotation_deg': [0, 0, 0], 'params': {'size': [W, D, 0.05]}}]
    place = {'back': ([0, D / 2 - 0.026, 1.5], [W, 0.048, 3]), 'front': ([0, -D / 2 + 0.026, 1.5], [W, 0.048, 3]),
             'left': ([-W / 2 + 0.026, 0, 1.5], [0.048, D, 3]), 'right': ([W / 2 - 0.026, 0, 1.5], [0.048, D, 3])}
    for w in case['walls']:
        if wall_planes is None or w['plane'] in wall_planes:
            loc, size = place[w['plane']]
            comps.append({'id': 'wall_' + w['plane'], 'kind': 'box', 'location': loc, 'rotation_deg': [0, 0, 0], 'params': {'size': size}})
    return {'components': comps}


def report_for(pert, statuses=None, reasons=(), dropped=()):
    declared = pert['topology']['declared']
    ids = grammar.CHECK_IDS
    checks = [{'id': i, 'status': (statuses or {}).get(i, 'ok'), 'message': ''} for i in ids]
    return {'status': 'ok', 'valid': True, 'dropped': list(dropped),
            'topology': {'booth_type': declared['booth_type'], 'open_sides': declared['open_sides'], 'cutaway_sides': declared['cutaway_sides'], 'checks': checks},
            'quality_gate': {'status': 'review' if reasons else 'pass', 'reasons': list(reasons)}}


def row_for(case, name, report, spec, fit=None):
    pert = perturb.apply(case, name, 0)
    return metrics.evaluate_run(case, pert, fit or perfect_fit(case), report, spec, {'compile_s': 1.0})


def case_with(detail):
    for case in CASES:
        if perturb.apply(case, 'wrong_open_sides')['topology']['detail'] == detail and case['walls']:
            return case
    raise AssertionError(detail)


class RightDeclaration(unittest.TestCase):
    def test_a_clean_run_with_all_checks_ok(self):
        pert = perturb.apply(CASE, 'clean', 0)
        row = metrics.evaluate_run(CASE, pert, perfect_fit(CASE), report_for(pert), spec_for(CASE))
        self.assertIs(row['topology_defect'], False)
        self.assertIsNone(row['topology_kind'])
        self.assertEqual((row['grammar_type'], row['declared_type']), ('peninsula', 'peninsula'))
        self.assertTrue(row['topology_in_report'] and row['topology_declared'])
        self.assertEqual([row[k] for k in ('topo_type', 'topo_wall', 'topo_closed', 'topo_camera')], ['ok'] * 4)
        self.assertEqual(row['topo_n_fail'], 0)
        self.assertFalse(row['topology_flagged'])
        self.assertFalse(row['flagged'])
        self.assertEqual((row['walls_in_spec'], row['walls_on_open_declared'], row['walls_on_open_truth'], row['walls_dropped_open']), (1, 0, 0, 0))
        self.assertTrue(row['valid_input'])
        self.assertFalse(row['bad_fit'])

    def test_a_gate_review_for_a_fit_reason_is_not_a_topology_flag(self):
        pert = perturb.apply(CASE, 'noise_1', 0)
        rep = report_for(pert, reasons=['leaving one corner out moves it by 6.2 px on median (limit 6 px): the corners do not agree with each other'])
        row = metrics.evaluate_run(CASE, pert, perfect_fit(CASE), rep, spec_for(CASE))
        self.assertTrue(row['flagged'])
        self.assertFalse(row['topology_flagged'])
        self.assertFalse(row['topology_only_flagged'])
        self.assertEqual(row['topology_reasons'], [])

    def test_a_topology_reason_on_a_right_declaration_is_a_false_alarm(self):
        pert = perturb.apply(CASE, 'clean', 0)
        rep = report_for(pert, {'camera_side': 'fail'}, [SENT['camera_side']])
        row = metrics.evaluate_run(CASE, pert, perfect_fit(CASE), rep, spec_for(CASE))
        self.assertTrue(row['topology_flagged'] and row['topology_only_flagged'])
        self.assertEqual(row['topo_camera'], 'fail')
        rows = [row]
        self.assertEqual(metrics.BY_KEY['topology_false_alarm'][4](row), (1.0, 1.0))
        self.assertEqual(metrics.BY_KEY['topology_added_false_alarm'][4](row), (1.0, 1.0))
        self.assertIsNone(metrics.BY_KEY['topology_flag_rate'][4](rows[0]))              # only runs with a wrong declaration count there


class WrongDeclaration(unittest.TestCase):
    def test_a_half_turn_caught_by_the_camera_check_alone(self):
        pert = perturb.apply(CASE, 'swap_fb_lr', 0)
        rep = report_for(pert, {'camera_side': 'fail'}, [SENT['camera_side']])
        row = metrics.evaluate_run(CASE, pert, perfect_fit(CASE), rep, spec_for(CASE))
        self.assertIs(row['topology_defect'], True)
        self.assertEqual((row['topology_kind'], row['topology_detail']), ('swap_fb_lr', 'swap_fb_lr'))
        self.assertTrue(row['bad_fit'] and row['flagged'] and row['topology_flagged'] and row['topology_only_flagged'])
        self.assertFalse(row['valid_input'])
        self.assertTrue(row['defective_input'])
        self.assertEqual(metrics.BY_KEY['topology_flag_rate'][4](row), (1.0, 1.0))
        self.assertEqual(metrics.BY_KEY['topology_added_flag'][4](row), (1.0, 1.0))
        self.assertIsNone(metrics.BY_KEY['topology_false_alarm'][4](row))

    def test_the_ceiling_travels_with_the_row(self):
        pert = perturb.apply(CASE, 'swap_fb_lr', 0)
        row = metrics.evaluate_run(CASE, pert, perfect_fit(CASE), report_for(pert), spec_for(CASE))
        self.assertEqual(row['topology_best_case'], grammar.best_case_catch(CASE, pert['topology']))
        self.assertIn(metrics.BY_KEY['topology_catchable'][4](row), ((1.0, 1.0), (0.0, 1.0)))
        mirror = metrics.evaluate_run(CASE, perturb.apply(CASE, 'mirror_fb', 0), perfect_fit(CASE), None, None)
        self.assertIsNone(mirror['topology_best_case'])
        self.assertIsNone(metrics.BY_KEY['topology_catchable'][4](mirror))                           # undefined for single mirrors

    def test_a_mirror_the_fit_already_flags_is_not_credited_to_the_topology(self):
        pert = perturb.apply(CASE, 'mirror_fb', 0)
        fit = perfect_fit(CASE)
        fit['status'] = 'high_residual'
        rep = report_for(pert, {'camera_side': 'fail'}, ['the corner fit leaves 90.0 px of error (rms): some clicked corners disagree with a box seen in perspective',
                                                        SENT['camera_side']])
        row = metrics.evaluate_run(CASE, pert, fit, rep, spec_for(CASE))
        self.assertTrue(row['flagged'] and row['topology_flagged'])
        self.assertFalse(row['topology_only_flagged'])                       # the old gate would have flagged it anyway
        self.assertEqual(metrics.BY_KEY['topology_added_flag'][4](row), (0.0, 1.0))

    def test_a_mirror_that_nothing_flags_is_a_miss(self):
        pert = perturb.apply(CASE, 'mirror_lr', 0)
        row = metrics.evaluate_run(CASE, pert, perfect_fit(CASE), report_for(pert), spec_for(CASE))
        self.assertTrue(row['bad_fit'])                                       # wrong labels are bad whatever the residual says
        self.assertFalse(row['flagged'] or row['topology_flagged'])
        self.assertEqual(metrics.BY_KEY['topology_flag_rate'][4](row), (0.0, 1.0))
        self.assertEqual(metrics.BY_KEY['topology_flag_any'][4](row), (0.0, 1.0))

    def test_a_wall_on_a_side_declared_open_is_counted_unless_the_compiler_left_it_out(self):
        case = case_with('closed_as_open')
        pert = perturb.apply(case, 'wrong_open_sides', 0)
        side = pert['topology']['toggled_side']
        self.assertIn(side, [w['plane'] for w in case['walls']])
        kept = metrics.evaluate_run(case, pert, perfect_fit(case), None, spec_for(case))                       # a skill that ignores the declaration
        self.assertEqual((kept['walls_on_open_declared'], kept['walls_on_open_truth']), (1, 0))
        self.assertEqual(metrics.BY_KEY['walls_on_open_declared_rate'][4](kept), 1.0)
        dropped = [{'id': 'wall_' + side, 'reason': 'wall on an open side'}]
        rep = report_for(pert, {'wall_on_open_side': 'fail'}, [SENT['wall_on_open_side']], dropped)
        left_out = metrics.evaluate_run(case, pert, perfect_fit(case), rep, spec_for(case, [w['plane'] for w in case['walls'] if w['plane'] != side]))
        self.assertEqual((left_out['walls_on_open_declared'], left_out['walls_dropped_open']), (0, 1))
        self.assertTrue(left_out['topology_flagged'])
        self.assertEqual(metrics.BY_KEY['walls_dropped_rate'][4](left_out), 1.0)

    def test_an_open_side_declared_closed_leaves_no_wall_on_an_open_side(self):
        case = case_with('open_as_closed')
        pert = perturb.apply(case, 'wrong_open_sides', 0)
        rep = report_for(pert, {'closed_side_without_wall': 'warn'})
        row = metrics.evaluate_run(case, pert, perfect_fit(case), rep, spec_for(case))
        self.assertEqual((row['walls_on_open_declared'], row['walls_on_open_truth']), (0, 0))
        self.assertEqual(row['topo_closed'], 'warn')
        self.assertFalse(row['topology_flagged'])                              # information only: it never decides the gate
        self.assertEqual(metrics.BY_KEY['topo_closed_warn_rate'][4](row), 1.0)


class SkillsWithoutTheFeature(unittest.TestCase):
    def test_no_topology_block_means_no_topology_numbers_and_no_topology_flag(self):
        pert = perturb.apply(CASE, 'swap_fb_lr', 0)
        row = metrics.evaluate_run(CASE, pert, perfect_fit(CASE), {'status': 'ok', 'valid': True, 'quality_gate': {'status': 'pass', 'reasons': []}},
                                   spec_for(CASE))
        self.assertFalse(row['topology_in_report'])
        self.assertIsNone(row['topology_declared'])
        self.assertIsNone(row['topo_n_fail'])
        self.assertIsNone(row['topo_camera'])
        self.assertIsNone(row['topo_all_not_checked'])
        self.assertFalse(row['topology_flagged'])
        self.assertTrue(row['bad_fit'] and not row['flagged'])                 # a miss: the run is wrong and nothing flags it
        for key in ('topo_declared_rate', 'topo_type_fail_rate', 'topo_camera_fail_rate', 'topo_not_checked_rate', 'topology_fail_in_gate'):
            self.assertIsNone(metrics.BY_KEY[key][4](row), key)
        self.assertEqual(metrics.BY_KEY['topology_flag_rate'][4](row), (0.0, 1.0))

    def test_a_run_that_produced_nothing_has_no_walls_and_no_topology(self):
        pert = perturb.apply(CASE, 'clean', 0)
        row = metrics.evaluate_run(CASE, pert, {'status': 'blocked', 'reason': 'x'}, None, None, failure='blocked')
        self.assertIsNone(row['walls_in_spec'])
        self.assertIsNone(row['walls_on_open_declared'])
        self.assertFalse(row['topology_flagged'])
        self.assertTrue(row['flagged'] and row['failed'])

    def test_a_pert_without_a_topology_block_leaves_the_fields_empty(self):
        row = metrics.evaluate_run(CASE, {'name': 'x', 'dims_mode': 'one', 'injected': {'bad_ids': []}}, perfect_fit(CASE), None, spec_for(CASE))
        self.assertIsNone(row['topology_defect'])
        self.assertIsNone(row['walls_on_open_declared'])
        self.assertTrue(row['valid_input'])
        for key in metrics.TOPOLOGY_KEYS:
            self.assertIsNone(metrics.BY_KEY[key][4](row), key)


class Checks(unittest.TestCase):
    def test_a_failed_check_without_its_gate_reason_is_visible(self):
        pert = perturb.apply(CASE, 'swap_fb_lr', 0)
        rep = report_for(pert, {'camera_side': 'fail'}, ['the clicked corners do not pin down the size (W, H): several different boxes fit equally well'])
        row = metrics.evaluate_run(CASE, pert, perfect_fit(CASE), rep, spec_for(CASE))
        self.assertEqual(metrics.BY_KEY['topology_fail_in_gate'][4](row), (0.0, 1.0))
        self.assertTrue(row['topology_flagged'])                                # a failed check in a flagged run still counts as a topology flag
        self.assertFalse(row['topology_only_flagged'])
        self.assertEqual(metrics.BY_KEY['topo_camera_fail_rate'][4](row), 1.0)

    def test_nothing_checked_when_the_sides_are_not_stated(self):
        pert = perturb.apply(CASE, 'clean', 0)
        rep = {'status': 'ok', 'valid': True, 'topology': {'booth_type': None, 'open_sides': None, 'cutaway_sides': None,
                                                           'checks': [{'id': i, 'status': 'not_checked', 'message': ''} for i in grammar.CHECK_IDS]},
               'quality_gate': {'status': 'pass', 'reasons': []}}
        row = metrics.evaluate_run(CASE, pert, perfect_fit(CASE), rep, spec_for(CASE))
        self.assertFalse(row['topology_declared'])
        self.assertEqual(row['topo_all_not_checked'], 1.0)
        self.assertEqual(metrics.BY_KEY['topo_not_checked_rate'][4](row), 1.0)
        self.assertEqual(metrics.BY_KEY['topo_declared_rate'][4](row), 0.0)


class Aggregates(unittest.TestCase):
    def test_recall_false_alarm_and_what_the_topology_adds(self):
        def mk(i, defect, flagged, topo_flag, only):
            return {'case_id': f'c{i}', 'pert': 'p', 'flagged': flagged, 'bad_fit': defect, 'failed': False, 'n_injected': 0, 'valid_input': not defect,
                    'topology_defect': defect, 'topology_flagged': topo_flag, 'topology_only_flagged': only}
        rows = [mk(0, True, True, True, True), mk(1, True, True, False, False), mk(2, True, False, False, False), mk(3, True, True, True, False),
                mk(4, False, False, False, False), mk(5, False, True, True, True), mk(6, False, False, False, False), mk(7, False, True, False, False)]
        agg = metrics.aggregate(rows, resamples=20)
        self.assertAlmostEqual(agg['topology_flag_rate']['value'], 2 / 4)
        self.assertAlmostEqual(agg['topology_flag_any']['value'], 3 / 4)
        self.assertAlmostEqual(agg['topology_added_flag']['value'], 1 / 4)
        self.assertAlmostEqual(agg['topology_false_alarm']['value'], 1 / 4)
        self.assertAlmostEqual(agg['topology_added_false_alarm']['value'], 1 / 4)
        self.assertEqual(agg['topology_flag_rate']['n'], 4)
        self.assertEqual(agg['topology_false_alarm']['n'], 4)

    def test_every_topology_metric_has_a_layer_a_label_and_a_direction(self):
        import compare_results as cr
        self.assertTrue(metrics.TOPOLOGY_KEYS)
        for key in metrics.TOPOLOGY_KEYS:
            self.assertIn(key, cr.DIRECTION)
        self.assertIn('topology', metrics.LAYER_ORDER)


if __name__ == '__main__':
    unittest.main()
