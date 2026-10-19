import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import perturb
import synth_generate as sg

CASE, SCENE = sg.build_case(20261019, 6)


def shift(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


class Perturbations(unittest.TestCase):
    def test_deterministic_and_independent_of_other_runs(self):
        a = perturb.apply(CASE, 'noise_3')
        perturb.apply(CASE, 'outlier_2')                 # another run in between changes nothing
        b = perturb.apply(CASE, 'noise_3')
        self.assertEqual(a, b)

    def test_clean_keeps_the_truth_pixels(self):
        pert = perturb.apply(CASE, 'clean')
        for p, c in zip(pert['points'], CASE['corners']):
            self.assertEqual(p['uv_px'], [round(c['uv_px'][0], 3), round(c['uv_px'][1], 3)])
        self.assertEqual(pert['injected']['bad_ids'], [])

    def test_noise_has_the_requested_spread(self):
        shifts = []
        for i in range(30):
            case, _ = sg.build_case(1, i)
            pert = perturb.apply(case, 'noise_3')
            shifts += [shift(p['uv_px'], c['uv_px']) for p, c in zip(pert['points'], case['corners'])]
        rms = math.sqrt(sum(s * s for s in shifts) / len(shifts) / 2)       # per-axis sigma
        self.assertTrue(2.5 < rms < 3.5, rms)

    def test_outliers_are_displaced_by_15_to_60_px_and_listed(self):
        for name, k in (('outlier_1', 1), ('outlier_2', 2)):
            for i in range(10):
                case, _ = sg.build_case(2, i)
                pert = perturb.apply(case, name)
                self.assertEqual(len(pert['injected']['outlier_ids']), k)
                truth = {c['id']: c['uv_px'] for c in case['corners']}
                for p in pert['points']:
                    d = shift(p['uv_px'], truth[p['id']])
                    if p['id'] in pert['injected']['outlier_ids']:
                        self.assertTrue(15 - 3 <= d <= 60 + 3, d)           # plus the small pixel noise
                    else:
                        self.assertLess(d, 6.0)

    def test_outlier_30_is_exactly_one_30_px_outlier(self):
        pert = perturb.apply(CASE, 'outlier_30')
        (pid,) = pert['injected']['outlier_ids']
        truth = next(c['uv_px'] for c in CASE['corners'] if c['id'] == pid)
        got = next(p['uv_px'] for p in pert['points'] if p['id'] == pid)
        self.assertTrue(26 <= shift(got, truth) <= 34)
        self.assertEqual(pert['injected']['outlier_px'][pid], 30.0)

    def test_mislabel_swaps_two_tokens_and_lists_both(self):
        pert = perturb.apply(CASE, 'mislabel')
        ids = pert['injected']['mislabel_ids']
        self.assertEqual(len(ids), 2)
        old = {c['id']: c['xyz'] for c in CASE['corners']}
        new = {p['id']: p['xyz'] for p in pert['points']}
        a, b = ids
        self.assertEqual(new[a], old[b])
        self.assertEqual(new[b], old[a])
        self.assertNotEqual(old[a], old[b])
        self.assertTrue(all(new[i] == old[i] for i in old if i not in ids))

    def test_dimension_variants(self):
        dims = CASE['dims_m']
        one = perturb.apply(CASE, 'noise_1')
        self.assertEqual({k: v for k, v in one['known'].items() if v is not None}, {CASE['anchor']: dims[CASE['anchor']]})
        self.assertEqual(one['assumed'], [])
        exact = perturb.apply(CASE, 'all_exact')
        self.assertEqual(exact['known'], dims)
        self.assertEqual(exact['assumed'], ['W', 'D', 'H'])
        guess = perturb.apply(CASE, 'all_guess')
        self.assertEqual(guess['assumed'], ['W', 'D', 'H'])
        self.assertTrue(all(v >= 1.0 and abs(v * 2 - round(v * 2)) < 1e-9 for v in guess['known'].values()))
        cases = [sg.build_case(3, i)[0] for i in range(20)]               # a guess may be right by luck; over many scenes it is not
        off = sum(1 for c in cases for k in 'WDH' if perturb.apply(c, 'all_guess')['known'][k] != c['dims_m'][k])
        self.assertGreater(off, 10)

    def test_check_corners_are_flagged_unless_switched_off(self):
        self.assertEqual(set(perturb.apply(CASE, 'noise_1')['fit_flags']), set(CASE['check_ids']))
        self.assertEqual(perturb.apply(CASE, 'noise_1', 2)['check_corners'], 2)
        arm0 = perturb.apply(CASE, 'noise_1', 0)
        self.assertEqual(arm0['fit_flags'], {})
        self.assertEqual(arm0['check_corners'], 0)
        for bad in (1, 3, 2.5, None):
            with self.assertRaises(ValueError):
                perturb.apply(CASE, 'noise_1', bad)

    def test_both_arms_see_exactly_the_same_input(self):
        # only fit_flags may differ, so that the two arms can be paired scene by scene
        for name in perturb.PERTURBATIONS:
            for i in (0, 3, 6, 9):
                case, _ = sg.build_case(20261019, i)
                a, b = perturb.apply(case, name, 2), perturb.apply(case, name, 0)
                strip = lambda p: {k: v for k, v in p.items() if k not in ('fit_flags', 'check_corners', 'injected')}
                self.assertEqual(strip(a), strip(b), (name, case['case_id']))
                self.assertEqual(a['injected']['bad_ids'], b['injected']['bad_ids'])

    def test_injected_corners_are_split_by_whether_they_were_held_out(self):
        for i in range(12):
            case, _ = sg.build_case(2, i)
            arm2, arm0 = perturb.apply(case, 'outlier_2', 2), perturb.apply(case, 'outlier_2', 0)
            inj = arm2['injected']
            self.assertEqual(set(inj['bad_check_ids']), set(inj['bad_ids']) & set(case['check_ids']))
            self.assertEqual(set(inj['bad_fitted_ids']) | set(inj['bad_check_ids']), set(inj['bad_ids']))
            self.assertEqual(arm0['injected']['bad_check_ids'], [])
            self.assertEqual(arm0['injected']['bad_fitted_ids'], arm0['injected']['bad_ids'])

    def test_wrong_anchor_states_one_wrong_dimension_as_assumed(self):
        seen = set()
        for name, err in (('wrong_anchor_m25', -0.25), ('wrong_anchor_m10', -0.10), ('wrong_anchor_p10', 0.10), ('wrong_anchor_p25', 0.25)):
            pert = perturb.apply(CASE, name)
            known = {k: v for k, v in pert['known'].items() if v is not None}
            self.assertEqual(list(known), [CASE['anchor']])
            self.assertAlmostEqual(known[CASE['anchor']], CASE['dims_m'][CASE['anchor']] * (1 + err), places=3)
            self.assertEqual(pert['assumed'], [CASE['anchor']])
            self.assertEqual(pert['dims_mode'], 'wrong_one')
            self.assertEqual(pert['anchor_err'], err)
            self.assertEqual(pert['injected']['bad_ids'], [])             # the corners themselves are fine
            seen.add(known[CASE['anchor']])
        self.assertEqual(len(seen), 4)
        self.assertIsNone(perturb.apply(CASE, 'noise_1')['anchor_err'])

    def test_scale_check_is_optional_and_moves_nothing_else(self):
        tables = 0
        for i in range(10):
            case, scene = sg.build_case(20261019, i)
            plain, with_sc = perturb.apply(case, 'noise_3'), perturb.apply(case, 'noise_3', 2, True)
            self.assertEqual(plain['scale_checks'], [])
            self.assertEqual({k: v for k, v in plain.items() if k != 'scale_checks'}, {k: v for k, v in with_sc.items() if k != 'scale_checks'})
            if with_sc['scale_checks']:
                tables += 1
                (check,) = with_sc['scale_checks']
                self.assertEqual((check['expected_height_m'], check['tolerance']), (0.74, 0.15))
                self.assertLess(check['top_px'][1], check['base_px'][1])           # the top of the table is above its foot in the picture
                self.assertEqual(perturb.scene_for_run(scene, case, with_sc)['scale_checks'], with_sc['scale_checks'])
                self.assertEqual(perturb.points_for_run(case, with_sc)['scale_checks'], with_sc['scale_checks'])
                self.assertEqual(scene['scale_checks'], [])                       # the canonical scene is not modified
            else:
                self.assertNotIn('scale_checks', perturb.points_for_run(case, with_sc))
        self.assertGreater(tables, 0)

    def test_scene_and_points_for_a_run(self):
        pert = perturb.apply(CASE, 'noise_3')
        scene = perturb.scene_for_run(SCENE, CASE, pert)
        self.assertEqual([p['uv_px'] for p in scene['corner_points']], [p['uv_px'] for p in pert['points']])
        self.assertNotEqual(scene['components'][-1]['base_px'], SCENE['components'][-1]['base_px'])
        self.assertEqual(SCENE['corner_points'][0]['uv_px'], CASE['corners'][0]['uv_px'])        # the canonical scene is not modified
        points = perturb.points_for_run(CASE, pert)
        self.assertEqual(points['image'], {'width': CASE['image']['width'], 'height': CASE['image']['height']})
        self.assertTrue(all(v is not None for v in points['known'].values()))

    def test_unknown_perturbation_is_refused(self):
        with self.assertRaises(ValueError):
            perturb.apply(CASE, 'nope')


if __name__ == '__main__':
    unittest.main()
