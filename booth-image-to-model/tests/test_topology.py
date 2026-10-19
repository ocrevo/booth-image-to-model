"""Booth grammar (scene description 1.2): booth_type, open_sides and cutaway_sides, and the checks the compiler makes with them.

The grammar words: open sides = the sides of the footprint with no wall, where visitors come in (inline = 1 open side, corner = 2
adjacent open sides, peninsula = 3, island = 4, other = anything else); a cutaway side only has its wall (or the roof) left out in
the picture to show the inside, it is not an opening. Sides use the corner-token words: front -D/2 (-Y), back D/2 (+Y), left -W/2 (-X),
right W/2 (+X). null = not stated: no check runs and the file compiles exactly as before.

The camera checks run on hand-made camera positions; the compile tests use the fixture booth of test_draft_compile, whose fitted camera
stands in front of and to the right of it (x 8, y -9).
"""
from __future__ import annotations
import copy
import itertools
import json
from pathlib import Path
import sys
import unittest

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import draft_compile as dc
import test_draft_compile as t
import test_quality_gate as q

SIDES = ('front', 'back', 'left', 'right')
FOOT = {'W': 6.0, 'D': 4.0, 'H': 3.0}                      # a footprint of x -3..3 and y -2..2 for the hand-made cameras
REASON_TYPE = 'the booth type does not match the open sides'
REASON_WALL = 'a wall was placed on an open side and left out'
REASON_CAMERA = 'the camera looks in through closed sides: front/back or left/right may be swapped'
NULL_GRAMMAR = {'booth_type': None, 'open_sides': None, 'cutaway_sides': None}
# open sides -> the type they make
TYPE_TABLE = [(['front'], 'inline'), (['back'], 'inline'), (['left'], 'inline'), (['right'], 'inline'),
              (['front', 'left'], 'corner'), (['front', 'right'], 'corner'), (['back', 'left'], 'corner'), (['back', 'right'], 'corner'),
              (['front', 'back'], 'other'), (['left', 'right'], 'other'),
              (['front', 'left', 'right'], 'peninsula'), (['back', 'left', 'right'], 'peninsula'),
              (['front', 'back', 'left'], 'peninsula'), (['front', 'back', 'right'], 'peninsula'),
              (list(SIDES), 'island'), ([], 'other')]
# hand-made camera positions (x, y) outside the footprint, and the sides each one is beyond
CAMERAS = {'front': ((0.0, -8.0), {'front'}), 'back': ((0.0, 8.0), {'back'}), 'left': ((-9.0, 0.0), {'left'}),
           'right': ((9.0, 0.0), {'right'}), 'front_left': ((-9.0, -8.0), {'front', 'left'}),
           'front_right': ((9.0, -8.0), {'front', 'right'}), 'back_right': ((9.0, 8.0), {'back', 'right'})}


def fit_at(x, y, z=3.0):
    return {'camera': {'position': [x, y, z]}, 'dims_m': dict(FOOT)}


def grammar(booth_type=None, open_sides=None, cutaway=None, components=()):
    """The smallest scene dict the topology functions read."""
    return {'booth_type': booth_type, 'open_sides': open_sides, 'cutaway_sides': cutaway, 'components': list(components)}


def statuses(report):
    return {c['id']: c['status'] for c in report['checks']}


def with_grammar(booth_type, open_sides, cutaway=None, version='1.2'):
    s = t.scene()
    s.update(schema_version=version, booth_type=booth_type, open_sides=open_sides, cutaway_sides=cutaway)
    return s


class TypeTable(unittest.TestCase):
    def test_open_sides_add_up_to_a_type(self):
        for sides, expected in TYPE_TABLE:
            self.assertEqual(dc.expected_booth_type(sides), expected, sides)
            self.assertEqual(dc.expected_booth_type(list(reversed(sides))), expected, sides)       # the order does not matter

    def test_a_side_listed_twice_counts_once(self):
        self.assertEqual(dc.expected_booth_type(['front', 'front']), 'inline')

    def test_every_declared_type_against_every_set_of_open_sides(self):
        for declared in dc.BOOTH_TYPES:
            for sides, expected in TYPE_TABLE:
                got = statuses(dc.topology_report(grammar(declared, sides), fit_at(0, -8)))['type_matches_sides']
                want = 'ok' if declared in ('other', expected) else 'fail'                     # other fits anything
                self.assertEqual(got, want, (declared, sides))

    def test_two_opposite_sides_are_not_a_corner(self):
        self.assertEqual(statuses(dc.topology_report(grammar('corner', ['front', 'back']), None))['type_matches_sides'], 'fail')
        self.assertEqual(statuses(dc.topology_report(grammar('corner', ['front', 'left']), None))['type_matches_sides'], 'ok')

    def test_the_vocabulary_is_the_documented_one(self):
        self.assertEqual(dc.BOOTH_TYPES, ('inline', 'corner', 'peninsula', 'island', 'other'))
        self.assertEqual(dc.SIDES, SIDES)


class NotStated(unittest.TestCase):
    def test_nulls_give_not_checked_and_leave_the_gate_alone(self):
        rep = dc.topology_report(grammar(), fit_at(0, -8))
        self.assertEqual([c['id'] for c in rep['checks']], ['type_matches_sides', 'wall_on_open_side', 'closed_side_without_wall', 'camera_side'])
        self.assertEqual(set(statuses(rep).values()), {'not_checked'})
        self.assertTrue(all(isinstance(c['message'], str) and c['message'] for c in rep['checks']))
        self.assertEqual({k: rep[k] for k in dc.TOPOLOGY_KEYS}, NULL_GRAMMAR)
        gate = {'status': 'pass', 'reasons': []}
        self.assertIs(dc.apply_topology(gate, rep), gate)                                       # the very same gate

    def test_nothing_runs_without_the_open_sides(self):
        for kw in ({'booth_type': 'inline'}, {'cutaway': ['left']}, {'booth_type': 'island', 'cutaway': ['back']}):
            rep = dc.topology_report(grammar(**kw), fit_at(0, 8))
            self.assertEqual(set(statuses(rep).values()), {'not_checked'}, kw)
            self.assertEqual(rep['open_sides'], None)

    def test_the_sides_without_a_type_still_check_everything_but_the_type(self):
        rep = dc.topology_report(grammar(None, ['front']), fit_at(0, -8))
        got = statuses(rep)
        self.assertEqual(got['type_matches_sides'], 'not_checked')
        self.assertEqual((got['wall_on_open_side'], got['camera_side']), ('ok', 'ok'))
        self.assertEqual(statuses(dc.topology_report(grammar(None, ['front']), fit_at(0, 8)))['camera_side'], 'fail')

    def test_cutaway_null_counts_as_no_cutaway_not_as_a_reason_to_skip(self):
        self.assertEqual(statuses(dc.topology_report(grammar('inline', ['front'], None), fit_at(-9, 0)))['camera_side'], 'fail')
        self.assertEqual(statuses(dc.topology_report(grammar('inline', ['front'], []), fit_at(-9, 0)))['camera_side'], 'fail')

    def test_a_scene_that_is_not_a_dict_states_nothing(self):
        self.assertEqual(dc.topology_declaration(None), (None, None, None))
        self.assertEqual(dc.topology_declaration({'booth_type': 3, 'open_sides': 'front'}), (None, None, None))


class CameraSide(unittest.TestCase):
    """The fitted camera must stand outside at least one open or cutaway side; with front -Y and back +Y, left -X and right +X."""

    def camera(self, where, open_sides, cutaway=None, booth_type='other'):
        x, y = CAMERAS[where][0] if where in CAMERAS else where
        return statuses(dc.topology_report(grammar(booth_type, open_sides, cutaway), fit_at(x, y)))['camera_side']

    def test_a_camera_on_each_side_against_each_single_open_side(self):
        for name, (_, beyond) in CAMERAS.items():
            for side in SIDES:
                self.assertEqual(self.camera(name, [side]), 'ok' if side in beyond else 'fail', (name, side))

    def test_the_camera_may_be_outside_any_one_of_several_open_sides(self):
        self.assertEqual(self.camera('front_right', ['front', 'left']), 'ok')                  # outside the front, which is open
        self.assertEqual(self.camera('front_right', ['back', 'left']), 'fail')                 # outside the front and the right, both closed
        self.assertEqual(self.camera('left', ['front', 'left', 'right']), 'ok')                # a peninsula seen from the side

    def test_a_cutaway_side_counts_for_the_camera_but_is_not_open(self):
        self.assertEqual(self.camera('left', ['front'], ['left'], 'inline'), 'ok')
        self.assertEqual(self.camera('left', ['front'], None, 'inline'), 'fail')
        self.assertEqual(self.camera('back', ['front'], ['back'], 'inline'), 'ok')

    def test_a_camera_close_to_the_edge_is_not_outside_it(self):
        self.assertEqual(self.camera((0.0, -2.2), ['back']), 'warn')                           # inside the margin of every edge: cannot tell
        self.assertEqual(self.camera((0.0, -2.2), ['front']), 'warn')
        self.assertEqual(self.camera((0.0, -2.0 - dc.CAMERA_MARGIN_M - 0.05), ['front']), 'ok')
        self.assertEqual(self.camera((0.0, 0.0), ['front']), 'warn')                           # inside the footprint

    def test_warn_never_sends_the_gate_to_review(self):
        rep = dc.topology_report(grammar('inline', ['front']), fit_at(0, 0))
        self.assertEqual(statuses(rep)['camera_side'], 'warn')
        gate = {'status': 'pass', 'reasons': []}
        self.assertIs(dc.apply_topology(gate, rep), gate)

    def test_the_fit_must_say_where_the_camera_is(self):
        for fit in (None, {}, {'camera': {}}, {'camera': {'position': [1.0]}, 'dims_m': FOOT}, {'camera': {'position': [float('nan'), 0, 1]}, 'dims_m': FOOT},
                    {'camera': {'position': [0, -8, 1]}}, {'camera': {'position': [0, -8, 1]}, 'dims_m': {'W': 6.0}}):
            self.assertEqual(statuses(dc.topology_report(grammar('inline', ['front']), fit))['camera_side'], 'not_checked', fit)

    def test_front_back_mirroring_is_caught_when_the_only_open_side_is_the_front(self):
        scene = grammar('inline', ['front'])
        self.assertEqual(statuses(dc.topology_report(scene, fit_at(2.0, -8.0)))['camera_side'], 'ok')
        mirrored = dc.topology_report(scene, fit_at(2.0, 8.0))                                  # every front/back token swapped: the camera moves to +Y
        self.assertEqual(statuses(mirrored)['camera_side'], 'fail')
        self.assertIn('mirrored', mirrored['checks'][3]['message'])

    def test_left_right_mirroring_is_not_seen_by_the_camera_when_it_stays_outside_an_open_side(self):
        """The honest limit: a camera in front of an open front is outside it whether or not left and right are swapped."""
        scene = grammar('inline', ['front'])
        self.assertEqual(statuses(dc.topology_report(scene, fit_at(5.0, -8.0)))['camera_side'], 'ok')
        self.assertEqual(statuses(dc.topology_report(scene, fit_at(-5.0, -8.0)))['camera_side'], 'ok')
        # but it is seen when the open side is the left or the right one
        self.assertEqual(statuses(dc.topology_report(grammar('inline', ['right']), fit_at(-9.0, 0.0)))['camera_side'], 'fail')

    def test_sides_beyond(self):
        self.assertEqual(dc.sides_beyond(0, 0, 6, 4), [])
        self.assertEqual(dc.sides_beyond(0, -8, 6, 4), ['front'])
        self.assertEqual(dc.sides_beyond(9, 8, 6, 4), ['back', 'right'])
        self.assertEqual(dc.sides_beyond(-9, -8, 6, 4), ['front', 'left'])
        self.assertEqual(dc.sides_beyond(3.1, 0, 6, 4), [])                                    # beyond the edge but inside the margin
        self.assertEqual(dc.sides_beyond(3.1, 0, 6, 4, margin=0.0), ['right'])


class WallsAndClosedSides(unittest.TestCase):
    def wall(self, plane, cid=None, kind='wall'):
        return {'id': cid or f'{kind}_{plane}', 'type': kind, 'plane': plane}

    def test_a_wall_on_an_open_side_fails_and_is_named(self):
        scene = grammar('inline', ['front'], None, [self.wall('front'), self.wall('back')])
        rep = dc.topology_report(scene, fit_at(0, -8))
        self.assertEqual(statuses(rep)['wall_on_open_side'], 'fail')
        self.assertIn('wall_front', rep['checks'][1]['message'])
        self.assertNotIn('wall_back', rep['checks'][1]['message'])
        self.assertEqual(dc.walls_on_open_sides(scene), ['wall_front'])

    def test_a_wall_on_a_cutaway_side_is_fine(self):
        scene = grammar('inline', ['front'], ['back'], [self.wall('back')])
        self.assertEqual(statuses(dc.topology_report(scene, fit_at(0, -8)))['wall_on_open_side'], 'ok')
        self.assertEqual(dc.walls_on_open_sides(scene), [])

    def test_only_a_wall_component_counts_as_a_wall_on_an_open_side(self):
        scene = grammar('inline', ['front'], None, [self.wall('front', 'screen', 'tv'), self.wall('front', 'panel', 'wall_panel')])
        self.assertEqual(dc.walls_on_open_sides(scene), [])
        self.assertEqual(dc.walls_on_open_sides(grammar()), [])                                # nothing stated: no wall is in the way

    def test_closed_sides_without_a_wall_are_information_only(self):
        scene = grammar('inline', ['front'], None, [self.wall('back')])
        rep = dc.topology_report(scene, fit_at(0, -8))
        self.assertEqual(statuses(rep)['closed_side_without_wall'], 'warn')
        self.assertIn('left, right', rep['checks'][2]['message'])
        gate = {'status': 'pass', 'reasons': []}
        self.assertIs(dc.apply_topology(gate, rep), gate)

    def test_a_cutaway_side_needs_no_wall_and_every_flat_part_counts_as_one(self):
        scene = grammar('inline', ['front'], ['left'], [self.wall('back'), self.wall('right', 'telly', 'tv')])
        self.assertEqual(statuses(dc.topology_report(scene, fit_at(0, -8)))['closed_side_without_wall'], 'ok')
        for kind in ('wall', 'wall_panel', 'led_wall', 'tv'):
            scene = grammar('island', list(SIDES[:3]), None, [self.wall('right', 'part', kind)])
            self.assertEqual(statuses(dc.topology_report(scene, fit_at(0, -8)))['closed_side_without_wall'], 'ok', kind)

    def test_a_booth_open_on_all_sides_has_no_closed_side_to_wall(self):
        self.assertEqual(statuses(dc.topology_report(grammar('island', list(SIDES)), fit_at(0, -8)))['closed_side_without_wall'], 'ok')


class GateReasons(unittest.TestCase):
    PASS = {'status': 'pass', 'reasons': []}

    def test_each_failed_check_gives_its_plain_sentence(self):
        for cid, reason in (('type_matches_sides', REASON_TYPE), ('wall_on_open_side', REASON_WALL), ('camera_side', REASON_CAMERA)):
            rep = {'checks': [{'id': cid, 'status': 'fail', 'message': 'x'}]}
            gate = dc.apply_topology(self.PASS, rep)
            self.assertEqual(gate, {'status': 'review', 'reasons': [reason]}, cid)
        self.assertEqual(dc.TOPOLOGY_REASONS, {'type_matches_sides': REASON_TYPE, 'wall_on_open_side': REASON_WALL, 'camera_side': REASON_CAMERA})

    def test_the_reasons_use_no_engineering_words(self):
        for reason in dc.TOPOLOGY_REASONS.values():
            for word in ('topology', 'footprint', 'plane', 'spec', 'json', 'null', 'fit'):
                self.assertNotIn(word, reason.lower())

    def test_the_gate_keeps_its_own_reasons_first_and_is_not_edited(self):
        before = {'status': 'review', 'reasons': ['the corner fit leaves 9.0 px of error (rms)']}
        rep = {'checks': [{'id': 'camera_side', 'status': 'fail', 'message': 'x'}]}
        gate = dc.apply_topology(before, rep)
        self.assertEqual(gate['reasons'], before['reasons'] + [REASON_CAMERA])
        self.assertEqual(len(before['reasons']), 1)

    def test_ok_warn_and_not_checked_add_nothing(self):
        for status in ('ok', 'warn', 'not_checked'):
            rep = {'checks': [{'id': cid, 'status': status, 'message': 'x'} for cid in dc.TOPOLOGY_CHECKS]}
            self.assertIs(dc.apply_topology(self.PASS, rep), self.PASS, status)

    def test_the_quality_gate_function_is_untouched(self):
        import inspect
        self.assertEqual(list(inspect.signature(dc.quality_gate).parameters), ['fit'])
        self.assertEqual([n for n in dir(dc) if n.startswith('GATE_')], [])


class SchemaAndUpgrade(unittest.TestCase):
    def raw_valid(self, s):
        return Draft202012Validator(dc.load_schema()).is_valid(s)

    def full(self, **kw):
        s = dc.upgrade_scene(t.scene())
        s.update(schema_version='1.2', **kw)
        return s

    def test_a_1_2_scene_validates_and_so_do_the_older_versions_with_and_without_the_keys(self):
        for version in ('1.2', '1.1', '1.0', 'scene-description.v1'):
            self.assertEqual(dc.validate_scene(with_grammar('inline', ['front'], [], version)), [], version)
            old = t.scene()
            old['schema_version'] = version
            self.assertEqual(dc.validate_scene(old), [], version)
        self.assertEqual({e['code'] for e in dc.validate_scene(with_grammar('inline', ['front'], [], '1.3'))}, {'SCHEMA'})

    def test_every_type_and_every_set_of_sides_is_accepted(self):
        for booth_type in (*dc.BOOTH_TYPES, None):
            self.assertEqual(dc.validate_scene(with_grammar(booth_type, ['front'], None)), [], booth_type)
        for n in range(5):
            for sides in itertools.combinations(SIDES, n):
                self.assertEqual(dc.validate_scene(with_grammar('other', list(sides), None)), [], sides)
        self.assertEqual(dc.validate_scene(with_grammar(None, None, None)), [])

    def test_the_vocabulary_is_enforced_by_the_schema(self):
        bad = [{'booth_type': 'tower'}, {'booth_type': 'Inline'}, {'booth_type': 1}, {'booth_type': ['inline']},
               {'open_sides': 'front'}, {'open_sides': ['top']}, {'open_sides': ['front', None]}, {'open_sides': [1]}, {'open_sides': {'front': True}},
               {'cutaway_sides': 'left'}, {'cutaway_sides': ['Left']}, {'cutaway_sides': [['left']]}]
        for change in bad:
            s = self.full(**NULL_GRAMMAR)
            s.update(change)
            self.assertEqual({e['code'] for e in dc.validate_scene(s)}, {'SCHEMA'}, change)

    def test_the_strict_schema_requires_every_key_and_allows_null(self):
        schema = dc.load_schema()
        props = schema['properties']
        for key in dc.TOPOLOGY_KEYS:
            self.assertIn(key, props)
            self.assertIn(key, schema['required'])
            self.assertIn('null', props[key]['type'])
        self.assertEqual(props['booth_type']['type'], ['string', 'null'])
        self.assertEqual(props['booth_type']['enum'], [*dc.BOOTH_TYPES, None])
        for key in ('open_sides', 'cutaway_sides'):
            self.assertEqual(props[key]['type'], ['array', 'null'])
            self.assertEqual(props[key]['items'], {'type': 'string', 'enum': list(SIDES)})
        self.assertIn('1.2', props['schema_version']['enum'])
        self.assertIn('1.1', props['schema_version']['enum'])                                  # the web reader test still looks for it
        keys = list(props)
        self.assertLess(keys.index('booth_type'), keys.index('image'))                         # a model that writes keys in order states the grammar first
        self.assertLess(keys.index('open_sides'), keys.index('corner_points'))
        s = self.full(**NULL_GRAMMAR)
        self.assertTrue(self.raw_valid(s))
        for key in dc.TOPOLOGY_KEYS:
            without = {k: v for k, v in s.items() if k != key}
            self.assertFalse(self.raw_valid(without), key)

    def test_only_keywords_every_strict_provider_knows_are_used(self):
        banned = ('pattern', 'minItems', 'maxItems', 'uniqueItems', 'minimum', 'maximum', 'format', '$ref', 'anyOf', 'oneOf', 'allOf', 'if', 'then')
        seen = []

        def walk(node, where):
            if isinstance(node, dict):
                if node.get('type') == 'object' and 'properties' in node:
                    seen.append(where)
                    self.assertIs(node.get('additionalProperties'), False, where)
                    self.assertEqual(sorted(node['required']), sorted(node['properties']), where)      # what web/tests/test_reader.py demands
                for key, value in node.items():
                    if key not in ('properties', 'enum', 'required'):
                        self.assertNotIn(key, banned, f'{where}.{key}')
                    walk(value, f'{where}.{key}')
            elif isinstance(node, list):
                for i, value in enumerate(node):
                    walk(value, f'{where}[{i}]')
        walk(dc.load_schema(), 'scene')
        self.assertGreater(len(seen), 5)

    def test_upgrade_fills_the_booth_grammar_with_null_and_never_edits_the_input(self):
        old = t.scene()
        self.assertTrue(all(k not in old for k in dc.TOPOLOGY_KEYS))
        up = dc.upgrade_scene(old)
        self.assertIsNot(up, old)
        self.assertTrue(all(k not in old for k in dc.TOPOLOGY_KEYS))                           # the caller's dict is untouched
        self.assertEqual({k: up[k] for k in dc.TOPOLOGY_KEYS}, NULL_GRAMMAR)
        self.assertIs(dc.upgrade_scene(up), up)                                                # nothing left to fill
        self.assertTrue(self.raw_valid(up))
        self.assertEqual(json.dumps({k: v for k, v in up.items() if k not in dc.TOPOLOGY_KEYS}), json.dumps(old))   # nothing else changed

    def test_upgrade_keeps_what_is_stated_and_fills_only_what_is_missing(self):
        s = t.scene()
        s['booth_type'] = 'corner'
        up = dc.upgrade_scene(s)
        self.assertEqual((up['booth_type'], up['open_sides'], up['cutaway_sides']), ('corner', None, None))
        self.assertNotIn('open_sides', s)
        both = dc.upgrade_scene(self.full(booth_type='island', open_sides=list(SIDES), cutaway_sides=[]))
        self.assertEqual((both['booth_type'], both['open_sides'], both['cutaway_sides']), ('island', list(SIDES), []))

    def test_upgrade_does_both_shims_on_the_oldest_files(self):
        old = t.scene()
        old['schema_version'] = 'scene-description.v1'
        for p in old['corner_points']:
            del p['fit']
        up = dc.upgrade_scene(old)
        self.assertTrue(all(p['fit'] is None for p in up['corner_points']))
        self.assertEqual({k: up[k] for k in dc.TOPOLOGY_KEYS}, NULL_GRAMMAR)
        self.assertTrue(all('fit' not in p for p in old['corner_points']))
        self.assertEqual(dc.validate_scene(old), [])
        self.assertEqual(dc.upgrade_scene('not a dict'), 'not a dict')                         # the schema reports that, not the shim

    def test_a_side_listed_twice_or_both_open_and_cut_away_is_refused(self):
        errors = dc.validate_scene(with_grammar('inline', ['front', 'front'], None))
        self.assertEqual([(e['code'], e['where']) for e in errors], [('TOPOLOGY', 'open_sides')])
        errors = dc.validate_scene(with_grammar('inline', ['front'], ['back', 'back']))
        self.assertEqual([(e['code'], e['where']) for e in errors], [('TOPOLOGY', 'cutaway_sides')])
        errors = dc.validate_scene(with_grammar('inline', ['front', 'left'], ['left']))
        self.assertEqual([(e['code'], e['where']) for e in errors], [('TOPOLOGY', 'cutaway_sides')])
        self.assertIn("'left'", errors[0]['message'])

    def test_a_type_that_disagrees_with_the_sides_is_not_a_scene_error_but_a_problem_a_reader_can_use(self):
        s = with_grammar('inline', ['front', 'left', 'right'])
        self.assertEqual(dc.validate_scene(s), [])                                             # it compiles; the gate says review
        problems = dc.topology_problems(s)
        self.assertEqual([(p['code'], p['where']) for p in problems], [('TOPOLOGY', 'booth_type')])
        self.assertIn('peninsula', problems[0]['message'])
        self.assertEqual(dc.topology_problems(s, include_type=False), [])
        self.assertEqual(dc.topology_problems(with_grammar('peninsula', ['front', 'left', 'right'])), [])
        self.assertEqual(dc.topology_problems(with_grammar('other', ['front'])), [])           # other fits anything
        self.assertEqual(dc.topology_problems(with_grammar('inline', None)), [])               # nothing stated about the sides
        self.assertEqual(dc.topology_problems(with_grammar(None, ['front', 'front']))[0]['where'], 'open_sides')


class Compile(t.Base):
    """The whole compile on the fixture booth: back wall on +Y, tv and led wall on the back, camera at x 8, y -9."""

    def build(self, scene, name, fit=None):
        return dc.compile_scene(scene, self.image, self.dir / name, fit=copy.deepcopy(fit or q.fit_complete(self.fit)))

    def spec_bytes(self, name):
        return (self.dir / name / 'spec.json').read_bytes()

    def ids(self, spec):
        return {c['id'] for c in spec['components']}

    def test_the_fixture_camera_is_where_the_tests_assume(self):
        x, y, _ = self.fit['camera']['position']
        self.assertEqual(dc.sides_beyond(x, y, t.W, t.D), ['front', 'right'])

    def test_old_scene_files_compile_byte_identically(self):
        old = t.scene()                                                                        # 1.1, no booth grammar at all
        new = with_grammar(None, None, None)                                                   # 1.2 with every key null
        oldest = t.scene()
        oldest['schema_version'] = 'scene-description.v1'
        for p in oldest['corner_points']:
            del p['fit']
        for name, s in (('old', old), ('new', new), ('oldest', oldest)):
            spec, report = self.build(s, name)
            self.assertTrue(report['valid'], name)
            self.assertEqual(report['status'], 'ok', name)
        for other in ('new', 'oldest'):
            self.assertEqual(self.spec_bytes(other), self.spec_bytes('old'), other)
            self.assertEqual((self.dir / other / 'box_fit.json').read_bytes(), (self.dir / 'old' / 'box_fit.json').read_bytes())
            self.assertEqual((self.dir / other / 'compile_report.json').read_bytes(), (self.dir / 'old' / 'compile_report.json').read_bytes())
        for word in (*dc.TOPOLOGY_KEYS, 'topology'):                                           # the grammar is a check, it is not written into the spec
            self.assertNotIn(word, self.spec_bytes('old').decode('utf-8'))

    def test_a_scene_without_a_grammar_gets_exactly_the_gate_it_got_before(self):
        fit = q.fit_complete(self.fit)
        _, report = self.build(t.scene(), 'nogrammar', fit)
        self.assertEqual(report['quality_gate'], dc.quality_gate(fit))
        self.assertEqual(report['quality_gate'], {'status': 'pass', 'reasons': []})
        self.assertEqual(set(statuses(report['topology']).values()), {'not_checked'})
        _, report = self.build(t.scene(), 'nogrammar_realfit', self.fit)                       # a real fit with no independent check: review as before
        self.assertEqual(report['quality_gate'], dc.quality_gate(self.fit))

    def test_a_consistent_grammar_that_drops_nothing_leaves_the_spec_unchanged(self):
        self.build(t.scene(), 'plain')
        for name, s in (('inline', with_grammar('inline', ['front'])), ('peninsula', with_grammar('peninsula', ['front', 'left', 'right'], [])),
                        ('cut', with_grammar('inline', ['front'], ['left']))):
            _, report = self.build(s, name)
            self.assertEqual(report['dropped'], [], name)
            self.assertEqual(self.spec_bytes(name), self.spec_bytes('plain'), name)
            self.assertEqual(report['quality_gate'], {'status': 'pass', 'reasons': []}, name)
            self.assertNotEqual(set(statuses(report['topology']).values()), {'not_checked'}, name)

    def test_a_wall_on_an_open_side_is_dropped_with_a_reason(self):
        spec, report = self.build(with_grammar('other', ['front', 'back']), 'wall_open')
        self.assertNotIn('back_wall', self.ids(spec))
        self.assertEqual(report['dropped'], [{'id': 'back_wall', 'reason': 'wall on an open side'}])
        self.assertEqual(report['status'], 'partial')
        self.assertTrue(report['valid'], report['validation_errors'])
        self.assertEqual(statuses(report['topology'])['wall_on_open_side'], 'fail')
        self.assertIn('back_wall', report['topology']['checks'][1]['message'])
        self.assertEqual(report['quality_gate'], {'status': 'review', 'reasons': [REASON_WALL]})
        self.assertTrue(any(i.startswith('led') for i in self.ids(spec)) and any(i.startswith('tv') for i in self.ids(spec)))    # parts on that plane stay

    def test_a_wall_on_a_cutaway_side_is_kept(self):
        spec, report = self.build(with_grammar('inline', ['front'], ['back']), 'wall_cut')
        self.assertIn('back_wall', self.ids(spec))
        self.assertEqual(report['dropped'], [])
        self.assertEqual(report['status'], 'ok')
        self.assertEqual(statuses(report['topology'])['wall_on_open_side'], 'ok')
        self.assertEqual(report['quality_gate']['status'], 'pass')

    def test_the_spec_has_no_wall_on_a_declared_open_side(self):
        for sides in (['back'], ['back', 'front'], ['back', 'left', 'right'], list(SIDES)):
            spec, report = self.build(with_grammar('other', sides), 'nowall_' + '_'.join(sides))
            self.assertNotIn('back_wall', self.ids(spec), sides)
            self.assertEqual([d['id'] for d in report['dropped']], ['back_wall'], sides)

    def test_a_type_that_does_not_match_gives_review_but_still_builds(self):
        spec, report = self.build(with_grammar('corner', ['front']), 'type_bad')
        self.assertTrue(report['valid'])
        self.assertEqual(report['dropped'], [])
        self.assertIn('back_wall', self.ids(spec))
        self.assertEqual(statuses(report['topology'])['type_matches_sides'], 'fail')
        self.assertEqual(report['quality_gate'], {'status': 'review', 'reasons': [REASON_TYPE]})

    def test_a_camera_that_looks_in_through_closed_sides_gives_review(self):
        _, report = self.build(with_grammar('inline', ['left']), 'cam_bad')                    # the camera stands front and right: both closed here
        self.assertEqual(statuses(report['topology'])['camera_side'], 'fail')
        self.assertEqual(report['quality_gate'], {'status': 'review', 'reasons': [REASON_CAMERA]})
        _, report = self.build(with_grammar('corner', ['front', 'right']), 'cam_good')         # the same camera, the open sides it faces
        self.assertEqual(statuses(report['topology'])['camera_side'], 'ok')
        self.assertEqual(report['quality_gate']['status'], 'pass')
        _, report = self.build(with_grammar('inline', ['left'], ['right']), 'cam_cut')         # looking in through a cutaway side is fine
        self.assertEqual(statuses(report['topology'])['camera_side'], 'ok')

    def test_several_failures_give_several_reasons_in_a_fixed_order(self):
        _, report = self.build(with_grammar('island', ['back']), 'all_bad')
        self.assertEqual(report['quality_gate'], {'status': 'review', 'reasons': [REASON_TYPE, REASON_WALL, REASON_CAMERA]})
        self.assertEqual([c['status'] for c in report['topology']['checks']], ['fail', 'fail', 'warn', 'fail'])

    def test_topology_reasons_come_after_the_fit_reasons(self):
        _, report = self.build(with_grammar('corner', ['front']), 'after_fit', self.fit)       # the real fit has no independent check
        reasons = report['quality_gate']['reasons']
        self.assertIn('no independent check', reasons[0])
        self.assertEqual(reasons[-1], REASON_TYPE)

    def test_the_report_carries_the_topology_unchanged_on_disk_and_in_the_summary(self):
        scene_file = self.dir / 'scene_topo.json'
        fit_file = self.dir / 'fit_topo.json'
        scene_file.write_text(json.dumps(with_grammar('island', ['back'])), encoding='utf-8')
        fit_file.write_text(json.dumps(q.fit_complete(self.fit)), encoding='utf-8')
        code, summary = dc.run(scene_file, self.image, self.dir / 'run_topo', fit_file)
        self.assertEqual(code, 0)
        written = json.loads((self.dir / 'run_topo' / 'compile_report.json').read_text(encoding='utf-8'))
        self.assertEqual(summary['topology'], written['topology'])
        self.assertEqual(set(written['topology']), {'booth_type', 'open_sides', 'cutaway_sides', 'checks'})
        self.assertEqual((written['topology']['booth_type'], written['topology']['open_sides'], written['topology']['cutaway_sides']),
                         ('island', ['back'], None))
        self.assertEqual(summary['quality_gate'], written['quality_gate'])
        for c in written['topology']['checks']:
            self.assertEqual(set(c), {'id', 'status', 'message'})
            self.assertIn(c['status'], ('ok', 'warn', 'fail', 'not_checked'))

    def test_a_refused_grammar_writes_nothing(self):
        out = self.dir / 'refused'
        with self.assertRaises(dc.SceneError) as ctx:
            dc.compile_scene(with_grammar('inline', ['front', 'front']), self.image, out, fit=copy.deepcopy(q.fit_complete(self.fit)))
        self.assertEqual(ctx.exception.errors[0]['code'], 'TOPOLOGY')
        self.assertFalse(out.exists())


if __name__ == '__main__':
    unittest.main()
