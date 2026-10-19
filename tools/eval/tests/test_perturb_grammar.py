"""The booth grammar perturbations (I2) and the guarantee that the older ones did not move."""
import collections
import hashlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import grammar
import perturb
import synth_generate as sg

OLD = ['clean', 'noise_1', 'noise_3', 'outlier_30', 'outlier_1', 'outlier_2', 'mislabel', 'all_exact', 'all_guess',
       'wrong_anchor_m25', 'wrong_anchor_m10', 'wrong_anchor_p10', 'wrong_anchor_p25']
NEW = ['mirror_fb', 'mirror_lr', 'wrong_open_sides', 'swap_fb_lr']
# sha256 over what the 13 older perturbations hand to the skill (apply() without the new topology block, both arms, with the scale check,
# the run scene without the three new keys, points.json), for six scenes of the oracle-v1 suite. Made from the harness as it was before I2.
OLD_INPUT_DIGEST = 'e75442cd1f950a8fe6adfd9f6b49b681a35fda197798cfa571782ad60996f5ed'


def old_input_digest():
    h = hashlib.sha256()
    for i in (0, 3, 6, 9, 13, 20):
        case, scene = sg.build_case(20261019, i)
        for name in OLD:
            for arm in (2, 0):
                p = perturb.apply(case, name, arm, True)
                p.pop('topology')
                h.update(json.dumps(p, sort_keys=True).encode('utf-8'))
            run_scene = perturb.scene_for_run(scene, case, perturb.apply(case, name))
            for key in ('booth_type', 'open_sides', 'cutaway_sides'):
                run_scene.pop(key)
            h.update(json.dumps(run_scene, sort_keys=True).encode('utf-8'))
            h.update(json.dumps(perturb.points_for_run(case, perturb.apply(case, name)), sort_keys=True).encode('utf-8'))
    return h.hexdigest()


CASES = [sg.build_case(20261019, i) for i in range(32)]


class OlderPerturbationsDidNotMove(unittest.TestCase):
    def test_their_definitions_and_inputs_are_pinned(self):
        self.assertEqual(old_input_digest(), OLD_INPUT_DIGEST)

    def test_the_suites_and_their_lists_are_unchanged(self):
        self.assertEqual(sg.SUITES['oracle-v1']['perturbations'], ['clean', 'noise_1', 'noise_3', 'outlier_30', 'outlier_1', 'outlier_2', 'mislabel',
                                                                  'all_exact', 'all_guess'])
        self.assertEqual(sg.SUITES['smoke']['perturbations'], ['noise_1', 'outlier_1'])
        for name in NEW:
            self.assertIn(name, perturb.PERTURBATIONS)
            self.assertTrue(all(name not in suite['perturbations'] for suite in sg.SUITES.values()), name)       # opt-in only
        self.assertEqual(set(perturb.TOPOLOGY_PERTURBATIONS), set(NEW))
        self.assertEqual(set(perturb.PERTURBATIONS), set(OLD) | set(NEW))

    def test_an_older_perturbation_declares_the_truth_and_is_not_a_defect(self):
        for case, _scene in CASES[:8]:
            for name in OLD:
                topo = perturb.apply(case, name)['topology']
                self.assertFalse(topo['defect'], name)
                self.assertIsNone(topo['kind'])
                self.assertEqual(topo['declared'], topo['truth'])


class MirrorsAndHalfTurn(unittest.TestCase):
    def test_every_token_is_swapped_on_the_named_axes_and_the_declaration_stays_true(self):
        for kind, axes in (('mirror_fb', (1,)), ('mirror_lr', (0,)), ('swap_fb_lr', (0, 1))):
            for case, _scene in CASES[:10]:
                pert = perturb.apply(case, kind)
                truth = {c['id']: c['xyz'] for c in case['corners']}
                for p in pert['points']:
                    for axis in range(3):
                        expect = grammar.flip_token(truth[p['id']][axis]) if axis in axes else truth[p['id']][axis]
                        self.assertEqual(p['xyz'][axis], expect, (kind, case['case_id'], p['id']))
                topo = pert['topology']
                self.assertTrue(topo['defect'])
                self.assertEqual((topo['kind'], topo['detail']), (kind, kind))
                self.assertEqual(topo['declared'], topo['truth'])              # the reader states the sides right; only its corner tokens are turned
                self.assertEqual(pert['injected']['bad_ids'], [])             # not an outlier or a mislabelled pair: nothing for leave-one-out to name
                self.assertEqual(pert['dims_mode'], 'one')

    def test_pixels_noise_and_dimensions_are_those_of_an_ordinary_reader(self):
        case = CASES[5][0]
        pert = perturb.apply(case, 'mirror_fb')
        truth = {c['id']: c['uv_px'] for c in case['corners']}
        for p in pert['points']:
            self.assertLess(abs(p['uv_px'][0] - truth[p['id']][0]) + abs(p['uv_px'][1] - truth[p['id']][1]), 6.0)
        self.assertEqual({k: v for k, v in pert['known'].items() if v is not None}, {case['anchor']: case['dims_m'][case['anchor']]})
        self.assertEqual(pert['assumed'], [])

    def test_points_json_and_scene_carry_the_turned_tokens(self):
        case, scene = CASES[2]
        pert = perturb.apply(case, 'swap_fb_lr')
        self.assertEqual([p['xyz'] for p in perturb.points_for_run(case, pert)['points']], [p['xyz'] for p in pert['points']])
        self.assertEqual([p['xyz'] for p in perturb.scene_for_run(scene, case, pert)['corner_points']], [p['xyz'] for p in pert['points']])


class WrongOpenSides(unittest.TestCase):
    def test_exactly_one_side_is_wrong_and_the_type_follows_the_declared_sides(self):
        for case, _scene in CASES:
            topo = perturb.apply(case, 'wrong_open_sides')['topology']
            truth, declared = set(topo['truth']['open_sides']), set(topo['declared']['open_sides'])
            self.assertEqual(truth ^ declared, {topo['toggled_side']}, case['case_id'])
            self.assertEqual(topo['declared']['booth_type'], grammar.classify(declared))
            self.assertEqual(topo['detail'], 'open_as_closed' if topo['toggled_side'] in truth else 'closed_as_open')
            self.assertEqual(topo['declared']['cutaway_sides'], [])
            self.assertTrue(topo['defect'])

    def test_both_mistakes_occur_and_an_island_can_only_lose_an_opening(self):
        kinds = collections.Counter(perturb.apply(case, 'wrong_open_sides')['topology']['detail'] for case, _ in CASES)
        self.assertGreaterEqual(kinds['open_as_closed'], 8)
        self.assertGreaterEqual(kinds['closed_as_open'], 8)
        for case, _scene in CASES:
            if grammar.truth_topology(case)['booth_type'] == 'island':
                self.assertEqual(perturb.apply(case, 'wrong_open_sides')['topology']['detail'], 'open_as_closed')

    def test_the_wall_components_and_the_tokens_are_left_alone(self):
        case, scene = CASES[4]
        pert = perturb.apply(case, 'wrong_open_sides')
        run_scene = perturb.scene_for_run(scene, case, pert)
        self.assertEqual([c for c in run_scene['components'] if c['type'] == 'wall'], [c for c in scene['components'] if c['type'] == 'wall'])
        self.assertEqual([p['xyz'] for p in pert['points']], [c['xyz'] for c in case['corners']])

    def test_it_is_deterministic_and_draws_from_its_own_stream(self):
        case = CASES[6][0]
        a = perturb.apply(case, 'wrong_open_sides')
        perturb.apply(case, 'noise_3')
        perturb.apply(case, 'mirror_fb')
        self.assertEqual(a, perturb.apply(case, 'wrong_open_sides'))
        # the pixels do not depend on the topology draw: the same case, name and arm give the same points whatever else ran
        self.assertEqual(a['points'], perturb.apply(case, 'wrong_open_sides', 0)['points'])


class WhatTheSkillIsGiven(unittest.TestCase):
    def test_the_run_scene_states_the_declared_topology_and_the_canonical_scene_does_not(self):
        case, scene = CASES[1]
        pert = perturb.apply(case, 'wrong_open_sides')
        run_scene = perturb.scene_for_run(scene, case, pert)
        declared = pert['topology']['declared']
        self.assertEqual((run_scene['booth_type'], run_scene['open_sides'], run_scene['cutaway_sides']),
                         (declared['booth_type'], declared['open_sides'], declared['cutaway_sides']))
        for key in ('booth_type', 'open_sides', 'cutaway_sides'):
            self.assertNotIn(key, scene)                                           # the stored scene.json (and the suite fingerprint) is untouched
        self.assertEqual(scene['schema_version'], 'scene-description.v1')

    def test_both_arms_see_the_same_topology(self):
        for name in NEW:
            for case, _scene in CASES[:6]:
                self.assertEqual(perturb.apply(case, name, 2)['topology'], perturb.apply(case, name, 0)['topology'])


if __name__ == '__main__':
    unittest.main()
