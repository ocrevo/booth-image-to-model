import collections
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import grammar
import perturb
import synth_generate as sg

ROOT = Path(__file__).resolve().parents[3]
CASES = [sg.build_case(20261019, i) for i in range(32)]


class TypeRule(unittest.TestCase):
    def test_the_booth_type_follows_the_number_and_position_of_the_open_sides(self):
        self.assertEqual(grammar.classify(['front']), 'inline')
        self.assertEqual(grammar.classify(['front', 'right']), 'corner')
        self.assertEqual(grammar.classify(['back', 'left']), 'corner')
        self.assertEqual(grammar.classify(['front', 'left', 'right']), 'peninsula')
        self.assertEqual(grammar.classify(list(grammar.SIDES)), 'island')

    def test_anything_else_is_other(self):
        self.assertEqual(grammar.classify(['front', 'back']), 'other')            # two opposite sides
        self.assertEqual(grammar.classify(['left', 'right']), 'other')
        self.assertEqual(grammar.classify([]), 'other')                           # closed on all four sides

    def test_a_side_listed_twice_counts_once(self):
        self.assertEqual(grammar.classify(['front', 'front']), 'inline')

    def test_sides_come_back_in_one_order(self):
        self.assertEqual(grammar.sort_sides(['right', 'front', 'left', 'front']), ['front', 'left', 'right'])

    def test_toggle_side_names_the_mistake(self):
        self.assertEqual(grammar.toggle_side(['front', 'left', 'right'], 'left'), (['front', 'right'], 'open_as_closed'))
        self.assertEqual(grammar.toggle_side(['front', 'right'], 'back'), (['front', 'back', 'right'], 'closed_as_open'))


class Truth(unittest.TestCase):
    def test_the_generators_inline_scenes_are_peninsulas_by_the_vocabulary(self):
        # a back wall only leaves three sides open; the old label stays in case.json (it is part of the pinned suite fingerprint)
        seen = {}
        for i in range(8):
            case, _ = sg.build_case(20261019, i)
            truth = grammar.truth_topology(case)
            seen.setdefault(case['booth_type'], set()).add(truth['booth_type'])
            self.assertEqual(sorted(truth['open_sides'] + [w['plane'] for w in case['walls']]), sorted(grammar.SIDES))
            self.assertEqual(truth['cutaway_sides'], [])
            self.assertEqual(truth['booth_type'], grammar.classify(truth['open_sides']))
        self.assertEqual(seen, {'inline': {'peninsula'}, 'corner': {'corner'}, 'peninsula': {'peninsula'}, 'island': {'island'}})

    def test_the_cameras_of_the_suite_stand_beyond_an_open_side(self):
        # the property the false-alarm numbers rest on (README, I2): the synthetic camera is outside at least one open side, with room to spare
        for i in range(32):
            case, _ = sg.build_case(20261019, i)
            x, y = case['camera']['position'][:2]
            W, D = case['dims_m']['W'], case['dims_m']['D']
            room = 0.59                                                            # the generator leaves 0.6 m (positions are rounded)
            beyond = {s for s, out in (('front', y < -D / 2 - room), ('back', y > D / 2 + room), ('left', x < -W / 2 - room), ('right', x > W / 2 + room)) if out}
            self.assertTrue(beyond & set(case['open_sides']), (case['case_id'], beyond))


class Ceiling(unittest.TestCase):
    """What the camera and wall checks can catch at best, from the geometry of the suite (a perfect fit, the true camera, a 0.25 m margin)."""

    def counts(self, name):
        got = collections.Counter()
        for case, _scene in CASES:
            got[grammar.best_case_catch(case, perturb.apply(case, name)['topology'])] += 1
        return got

    def test_the_ceilings_of_the_oracle_suite_are_pinned(self):
        # Islands can never be caught by the camera (all four sides are open) and a camera that stands outside an open side as well as a closed
        # one is not caught either: this is why the recall of the topology checks is read against this column, not against 100 %.
        self.assertEqual(self.counts('swap_fb_lr'), collections.Counter({True: 13, False: 19}))
        self.assertEqual(self.counts('wrong_open_sides'), collections.Counter({True: 14, False: 18}))
        self.assertEqual(self.counts('mirror_fb'), collections.Counter({None: 32}))               # a reflection has no best-case camera
        self.assertEqual(self.counts('clean'), collections.Counter({False: 32}))

    def test_islands_are_never_catchable_by_the_camera(self):
        for case, _scene in CASES:
            if grammar.truth_topology(case)['booth_type'] == 'island':
                self.assertFalse(grammar.best_case_catch(case, perturb.apply(case, 'swap_fb_lr')['topology']))

    def test_a_wall_declared_open_is_always_catchable(self):
        for case, _scene in CASES:
            topo = perturb.apply(case, 'wrong_open_sides')['topology']
            if topo['detail'] == 'closed_as_open':
                self.assertTrue(grammar.best_case_catch(case, topo))

    def test_sides_beyond(self):
        self.assertEqual(grammar.sides_beyond(0, -5, 6, 3), {'front'})
        self.assertEqual(grammar.sides_beyond(4, -5, 6, 3), {'front', 'right'})
        self.assertEqual(grammar.sides_beyond(0, -1.6, 6, 3), set())                                # within the margin
        self.assertEqual(grammar.sides_beyond(0, -1.6, 6, 3, margin=0.05), {'front'})


class Tokens(unittest.TestCase):
    def test_flip_token(self):
        self.assertEqual(grammar.flip_token('D/2'), '-D/2')
        self.assertEqual(grammar.flip_token('-D/2'), 'D/2')
        self.assertEqual(grammar.flip_token('0'), '0')

    def test_mirror_xyz_flips_the_named_axes_only(self):
        xyz = ['-W/2', 'D/2', 'H']
        self.assertEqual(grammar.mirror_xyz(xyz, 'mirror_fb'), ['-W/2', '-D/2', 'H'])
        self.assertEqual(grammar.mirror_xyz(xyz, 'mirror_lr'), ['W/2', 'D/2', 'H'])
        self.assertEqual(grammar.mirror_xyz(xyz, 'swap_fb_lr'), ['W/2', '-D/2', 'H'])
        self.assertEqual(xyz, ['-W/2', 'D/2', 'H'])                                # the input is not modified
        self.assertEqual(grammar.mirror_xyz(['0', 'D/2', 'F'], 'mirror_lr'), ['0', 'D/2', 'F'])        # the middle stays in the middle
        for kind in grammar.MIRROR_AXES:
            self.assertEqual(grammar.mirror_xyz(grammar.mirror_xyz(xyz, kind), kind), xyz)


def spec_with(floor=(6.0, 3.0), walls=()):
    comps = [{'id': 'floor', 'kind': 'box', 'location': [0, 0, 0], 'rotation_deg': [0, 0, 0], 'params': {'size': [floor[0], floor[1], 0.05]}}]
    for wid, loc, size in walls:
        comps.append({'id': wid, 'kind': 'box', 'location': loc, 'rotation_deg': [0, 0, 0], 'params': {'size': size}})
    return {'components': comps}


class WallsInTheSpec(unittest.TestCase):
    WALLS = [('wall_back', [0, 1.474, 1.5], [6.0, 0.048, 3.0]), ('wall_front', [0, -1.474, 1.5], [6.0, 0.048, 3.0]),
             ('wall_left', [-2.974, 0, 1.5], [0.048, 3.0, 3.0]), ('wall_right', [2.974, 0, 1.5], [0.048, 3.0, 3.0])]

    def test_each_wall_is_found_on_its_own_edge(self):
        # a front or back wall spans the whole width, so its ends touch the left and right edges too: the thin direction decides
        found = grammar.spec_wall_sides(spec_with(walls=self.WALLS), [w[0] for w in self.WALLS])
        self.assertEqual(found, {'wall_back': 'back', 'wall_front': 'front', 'wall_left': 'left', 'wall_right': 'right'})

    def test_walls_that_are_absent_are_not_listed_and_the_count_follows_the_sides(self):
        spec = spec_with(walls=self.WALLS[:1])
        self.assertEqual(grammar.spec_wall_sides(spec, ['wall_back', 'wall_left']), {'wall_back': 'back'})
        self.assertEqual(grammar.walls_on(spec, ['wall_back', 'wall_left'], ['back', 'front']), 1)
        self.assertEqual(grammar.walls_on(spec, ['wall_back', 'wall_left'], ['front', 'left', 'right']), 0)
        self.assertEqual(grammar.walls_on(spec, ['wall_back'], []), 0)

    def test_a_rotated_wall_and_an_offset_floor_are_handled(self):
        spec = spec_with(walls=[('wall_left', [-2.974, 0, 1.5], [3.0, 0.048, 3.0])])
        spec['components'][1]['rotation_deg'] = [0, 0, 90]
        self.assertEqual(grammar.spec_wall_sides(spec, ['wall_left']), {'wall_left': 'left'})

    def test_no_spec_or_no_floor_is_not_a_guess(self):
        self.assertIsNone(grammar.walls_on(None, ['wall_back'], ['back']))
        self.assertIsNone(grammar.walls_on({}, ['wall_back'], ['back']))
        spec = {'components': [{'id': 'wall_back', 'kind': 'box', 'location': [0, 1.4, 1], 'params': {'size': [6, 0.05, 3]}}]}
        self.assertEqual(grammar.spec_wall_sides(spec, ['wall_back']), {'wall_back': None})
        self.assertEqual(grammar.walls_on(spec, ['wall_back'], list(grammar.SIDES)), 0)
        far = spec_with(walls=[('wall_back', [0, 5.0, 1.5], [6.0, 0.05, 3.0])])           # nowhere near an edge
        self.assertEqual(grammar.spec_wall_sides(far, ['wall_back']), {'wall_back': None})


class ReadingTheReport(unittest.TestCase):
    TOPOLOGY = {'booth_type': 'corner', 'open_sides': ['front', 'right'], 'cutaway_sides': [],
                'checks': [{'id': 'type_matches_sides', 'status': 'ok', 'message': ''}, {'id': 'camera_side', 'status': 'fail', 'message': 'x'}]}

    def test_checks_and_declared(self):
        self.assertEqual(grammar.topology_checks({'topology': self.TOPOLOGY}), {'type_matches_sides': 'ok', 'camera_side': 'fail'})
        self.assertIsNone(grammar.topology_checks({'status': 'ok'}))
        self.assertIsNone(grammar.topology_checks(None))
        self.assertTrue(grammar.topology_declared({'topology': self.TOPOLOGY}))
        self.assertFalse(grammar.topology_declared({'topology': {'booth_type': None, 'open_sides': None, 'cutaway_sides': None, 'checks': []}}))
        self.assertIsNone(grammar.topology_declared({}))

    def test_the_three_topology_reasons_are_recognised(self):
        reasons = ['the booth type does not match the open sides', 'a wall was placed on an open side and left out',
                   'the camera looks in through closed sides: front/back or left/right may be swapped']
        self.assertEqual(grammar.topology_reasons(reasons), reasons)
        self.assertEqual(grammar.topology_reasons(['The Booth Type does not match the open sides (corner, 3 open)']), ['The Booth Type does not match the open sides (corner, 3 open)'])

    def test_the_reasons_of_the_fit_are_not_taken_for_topology(self):
        fit_reasons = ['the corner fit leaves 10.4 px of error (rms): some clicked corners disagree with a box seen in perspective',
                       'the clicked corners do not pin down the size (W, H): several different boxes fit equally well',
                       'corners that disagree with the rest of the fit (possible mis-clicks): floor_front_left',
                       'leaving one corner out moves it by 6.2 px on median (limit 6 px): the corners do not agree with each other',
                       'a size check failed: cafe_table measures 0.50 m where about 0.74 m is expected: the assumed size, the clicks or the expected size is wrong',
                       "there is no independent check of the fit: there are too few corners to predict each one from the others; add well-spread corners, or add extra corners as check corners (fit false)",
                       'the check corners are off by 9.0 px on median (limit 6 px): the fit does not predict corners it was not fitted to']
        self.assertEqual(grammar.topology_reasons(fit_reasons), [])
        self.assertEqual(grammar.topology_reasons(None), [])

    def test_no_reason_stored_by_the_i1_final_run_is_taken_for_topology(self):
        path = ROOT / 'output' / '_eval' / 'runs' / 'i1c-check0' / 'results.json'            # git-ignored; skipped on a clean checkout
        if not path.is_file():
            self.skipTest('the stored I1 final run is not here')
        rows = json.loads(path.read_text(encoding='utf-8'))['rows']
        reasons = [t for r in rows for t in (r.get('gate_reasons') or [])]
        self.assertGreater(len(reasons), 50)
        self.assertEqual(grammar.topology_reasons(reasons), [])


if __name__ == '__main__':
    unittest.main()
