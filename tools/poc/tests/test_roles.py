"""P8b tests for the model roles: request folders that hold only what a role may see, strict response checks that leave no room
for free text, the family catalogue.

No model, no Blender: the loop runs with the stand-ins (stubs.py through orchestrator.CheckedStubRoles).
"""
from __future__ import annotations
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parents[0]))        # tools/poc, so that `loop` is a package

from loop import catalog, edits, orchestrator, roles  # noqa: E402

sys.path.append(str(ROOT / 'booth-image-to-model' / 'scripts'))
import design_compile  # noqa: E402  (read only: the minimal designs must compile)

SCHEMA = ROOT / 'booth-image-to-model' / 'schemas' / 'booth-design.v1.json'
TEMPLATE = ROOT / 'booth-image-to-model' / 'templates' / 'design_template.json'
CATALOG_SCRIPT = HERE.parents[0] / 'loop' / 'catalog.py'
PROMPTS = HERE.parents[0] / 'prompts'
W, H = 320, 200
FIT = {'status': 'fitted', 'dims_m': {'W': 6.0, 'D': 4.0}, 'image': {'width': W, 'height': H}, 'floor_z': 0.0,
       'camera': {'projection': 'perspective', 'position': [3.5, -7.6, 1.9], 'target': [0.0, 0.0, 1.4], 'lens_mm': 35.0,
                  'sensor_width_mm': 36.0, 'principal_point': [0.5, 0.5], 'roll_deg': 0.0, 'pixel_aspect': [1, 1], 'fit_status': 'landmark_fitted'}}
DROP = object()


def jread(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def schema_kinds():
    return jread(SCHEMA)['$defs']['element']['properties']['kind']['enum']


def png_bytes(colour=(10, 20, 30), size=(8, 6), fmt='PNG'):
    buf = io.BytesIO()
    Image.new('RGB', size, colour).save(buf, format=fmt)
    return buf.getvalue()


def listing(folder):
    return sorted(p.relative_to(folder).as_posix() for p in Path(folder).rglob('*') if p.is_file())


def reader_design(design=None):
    """The template as a reader may write it: placeholder text only, no free-text fields."""
    d = json.loads(json.dumps(design if design is not None else jread(TEMPLATE)))
    for key in ('notes', 'requirements', 'uncertainties', 'reference'):
        d.pop(key, None)
    for key in ('mood', 'industry'):
        d['style'].pop(key, None)
    d['brief'] = {'text': 'XXXX XXXXXX', 'language': 'en'}
    for e in d['elements']:
        e.pop('note', None)
        if (e.get('text') or {}).get('body'):
            e['text']['body'] = 'XXXX XXXXXX'
    return d


def changed(doc, path, value):
    """A deep copy of doc with doc[path[0]][path[1]]... set to value (or removed when value is DROP)."""
    out = json.loads(json.dumps(doc))
    node = out
    for key in path[:-1]:
        node = node[key]
    if value is DROP:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return out


def reader_request():
    return {'role': 'reader', 'picture_id': 'pic_T', 'picture': 'reader_inputs/picture.png',
            'box_fit': {'dims_m': {'W': 6.0, 'D': 4.0}, 'image': {'width': W, 'height': H}},
            'catalog': 'reader_inputs/catalog/catalog.json', 'response': 'reader.response.json'}


def critic_request():
    return {'role': 'critic', 'round': 2, 'candidate_id': 'r2_c1', 'response': 'critic.response.json',
            'elements': [{'element_id': 'hero_wall', 'family': 'feature_wall', 'original_crop': 'critic_inputs/el_hero_wall/original.png',
                          'render_crop': 'critic_inputs/el_hero_wall/render.png', 'bbox_px': [1, 2, 30, 40],
                          'overlay_crop': 'critic_inputs/el_hero_wall/overlay.png', 'crop_origin_px': [1, 2], 'crop_scale': 3}]}


def judge_request(name='judge_p1', p=1):
    return {'role': 'judge', 'round': 2, 'pair_id': 'pair_abc', 'pass': p, 'judge_id': f'j{p}',
            'question': 'Which render, A or B, is closer to the original picture?',
            'original': f'{name}_inputs/original.png', 'A': f'{name}_inputs/A.png', 'B': f'{name}_inputs/B.png',
            'response': f'{name}.response.json'}


def good_reader():
    design = reader_design()
    evidence = {e['id']: {'bbox_px': [0, 0, W, H], 'confidence': 'low'} for e in design['elements']}
    evidence['hero_wall'] = {'bbox_px': [1, 2, 30, 40], 'confidence': 'high'}
    return {'schema_version': 'reader.v1', 'picture_id': 'pic_T', 'design': design, 'evidence': evidence,
            'unexpressible': [{'what': 'a robot arm', 'nearest_kind': 'box', 'why': 'no family builds it'}]}


GOOD_FINDING = {'id': 'f1', 'element_id': 'hero_wall', 'type': 'misplaced', 'severity': 'high', 'direction': 'left', 'bbox_px': [1, 2, 3, 4]}


def good_critic():
    return {'schema_version': 'critic.v1', 'round': 2, 'candidate_id': 'r2_c1', 'findings': [dict(GOOD_FINDING)], 'verdict': 'fix'}


def good_judge(p=1):
    return {'schema_version': 'judge.v1', 'pair_id': 'pair_abc', 'judge_id': f'j{p}', 'pass': p, 'better': 'A', 'scores': None,
            'items': None, 'extra_parts': None, 'confidence': 'medium'}


def element_index(design, eid):
    return next(i for i, e in enumerate(design['elements']) if e['id'] == eid)


class ScoreTable:
    """Every candidate gets the same total and edge distance: every round ends in a tie, so the judge is asked."""

    def score(self, cand_dir, preview_dir, candidate_id, design):
        return {'schema_version': 'score.v1', 'picture_id': 'pic_T', 'candidate_id': candidate_id, 'spec_sha256': '0' * 64,
                'levels': {'L1': {'panel': None}, 'L2': {'code': None, 'panel': None}, 'L3': {'panel': None},
                           'L4': {'code': None, 'panel': None}},
                'total': 50.0, 'aux': {'edge_distance_diag': 0.05, 'edge_distance_px': 1.0, 'holdout_median_diag': None,
                                       'colour_de_mean': None, 'booth_pixel_fraction': 0.5, 'second_view_ok': None},
                'status': 'blocked', 'reason': 'panel_missing'}


class Clock:
    def __init__(self, step_s):
        self.t, self.step = 0.0, step_s

    def __call__(self):
        self.t += self.step
        return self.t


class Tmp(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def loop_inputs(self, design=None):
        """picture, fit, truth folder and initial design of a stub run."""
        picture = self.tmp / 'picture.png'
        if not picture.exists():
            Image.fromarray(np.random.default_rng(5).integers(0, 255, (H, W, 3), dtype=np.uint8)).save(picture)
        fit = self.tmp / 'box_fit.json'
        fit.write_text(json.dumps(FIT), encoding='utf-8')
        truth = self.tmp / 'truth' / 'pic_T'
        truth.mkdir(parents=True, exist_ok=True)
        path = self.tmp / 'design.json'
        path.write_text(json.dumps(design if design is not None else reader_design(), indent=2), encoding='utf-8')
        return picture, fit, truth, path

    def stub_cfg(self, out_name='out', **kw):
        picture, fit, truth, design = self.loop_inputs(kw.pop('design', None))
        return orchestrator.Config(picture=picture, fit=fit, truth=truth, design_init=design, out=self.tmp / out_name, stub=True, **kw)


# -- the three tests_first

class ValidateCase(Tmp):
    def test_response_must_match_schema_or_is_undecided(self):
        bad_reader = [
            ('no unexpressible', changed(good_reader(), ['unexpressible'], DROP)),
            ('extra field', dict(good_reader(), notes='free text')),
            ('schema_version', dict(good_reader(), schema_version='reader.v2')),
            ('design without elements', changed(good_reader(), ['design', 'elements'], DROP)),
            ('kind not in the catalogue', changed(good_reader(), ['design', 'elements', 0, 'kind'], 'robot_arm')),
            ('confidence', changed(good_reader(), ['evidence', 'hero_wall', 'confidence'], 'sure')),
            ('bbox of three', changed(good_reader(), ['evidence', 'hero_wall', 'bbox_px'], [1, 2, 3])),
            ('unexpressible without why', changed(good_reader(), ['unexpressible', 0, 'why'], DROP)),
            ('another picture', dict(good_reader(), picture_id='pic_X')),
        ]
        bad_critic = [
            ('finding type', changed(good_critic(), ['findings', 0, 'type'], 'bogus')),
            ('severity as a list', changed(good_critic(), ['findings', 0, 'severity'], ['high'])),
            ('direction', changed(good_critic(), ['findings', 0, 'direction'], 'north')),
            ('free text in a finding', changed(good_critic(), ['findings', 0, 'note'], 'looks off')),
            ('bbox with a bool', changed(good_critic(), ['findings', 0, 'bbox_px'], [1, 2, True, 4])),
            ('verdict', dict(good_critic(), verdict='maybe')),
            ('free text', dict(good_critic(), comment='overall fine')),
            ('findings not a list', dict(good_critic(), findings={'f1': GOOD_FINDING})),
            ('another round', dict(good_critic(), round=3)),
            ('round as bool', dict(good_critic(), round=True)),
            ('round as float', dict(good_critic(), round=2.0)),
            ('another candidate', dict(good_critic(), candidate_id='r2_c0')),
        ]
        bad_judge = [
            ('better', dict(good_judge(), better='C')),
            ('scores in a tie-break', dict(good_judge(), scores={'L2': 1, 'L3': 1, 'L4': 1})),
            ('items in a tie-break', dict(good_judge(), items={})),
            ('item answers in a tie-break', dict(good_judge(), items={'L1:a': 'present'})),
            ('extra_parts in a tie-break', dict(good_judge(), extra_parts=0)),
            ('confidence', dict(good_judge(), confidence='sure')),
            ('free text', dict(good_judge(), reason='A has the right header')),
            ('no confidence', changed(good_judge(), ['confidence'], DROP)),
            ('another pair', dict(good_judge(), pair_id='pair_other')),
            ('another pass', dict(good_judge(), **{'pass': 2})),
            ('pass as float', dict(good_judge(), **{'pass': 1.0})),
            ('pass as bool', dict(good_judge(), **{'pass': True})),
            ('another judge', dict(good_judge(), judge_id='j2')),
        ]
        for role, request, good, bads in (('reader', reader_request(), good_reader(), bad_reader),
                                          ('critic', critic_request(), good_critic(), bad_critic),
                                          ('judge_p1', judge_request(), good_judge(), bad_judge)):
            result = roles.validate(role, good, request)
            self.assertEqual((result.status, result.value), ('ok', good), (role, result.reasons))
            self.assertFalse(result.reasons)
            for label, bad in bads:
                with self.subTest(role=role, case=label):
                    result = roles.validate(role, bad, request)
                    self.assertEqual((result.value, result.status), (roles.UNDECIDED, 'invalid'))
                    self.assertTrue(result.reasons and all(isinstance(r, str) and r for r in result.reasons))
            for label, bad in (('not an object', [good]), ('text', 'A'), ('NaN', changed(good, ['schema_version'], float('nan'))),
                               ('lone surrogate', changed(good, ['schema_version'], 'x\udc00'))):
                with self.subTest(role=role, case=label):
                    self.assertEqual(roles.validate(role, bad, request)[:2], (roles.UNDECIDED, 'invalid'))
        # the second pass of the comparison with the best so far is checked against its own request
        second = judge_request('judge_best_p2', 2)
        self.assertEqual(roles.validate('judge_best_p2', good_judge(2), second).status, 'ok')
        self.assertEqual(roles.validate('judge_best_p2', good_judge(1), second).status, 'invalid')
        # no response at all, and a file that is not JSON
        result = roles.validate('critic', None, critic_request())
        self.assertEqual((result.value, result.status), (roles.UNDECIDED, 'no_response'))
        result = roles.validate('critic', roles.Unreadable('the file is not JSON'), critic_request())
        self.assertEqual((result.value, result.status), (roles.UNDECIDED, 'invalid'))
        self.assertIn('not JSON', ' '.join(result.reasons))
        with self.assertRaises(ValueError):
            roles.validate('painter', good_judge(), judge_request())

    def test_reader_answers_leave_no_room_for_free_text_and_must_compile(self):
        g = good_reader()
        header, reception = element_index(g['design'], 'header'), element_index(g['design'], 'reception')
        screen = element_index(g['design'], 'screen')
        long_text = 'x' * (roles.FREE_TEXT_MAX + 1)
        cases = [
            # placeholder text: only X and spaces, in the elements and in the brief
            ('text copied from the picture', changed(g, ['design', 'elements', header, 'text', 'body'], 'DEMO COFFEE')),
            ('lower-case placeholder', changed(g, ['design', 'elements', header, 'text', 'body'], 'xxxx xxxxxx')),
            ('free brief', changed(g, ['design', 'brief', 'text'], 'A warm coffee stand by DEMO COFFEE')),
            ('brief without the placeholder', changed(g, ['design', 'brief', 'text'], roles.READER_BRIEF)),
            ('brief language', changed(g, ['design', 'brief', 'language'], 'a description')),
            # free-text fields
            ('design notes', changed(g, ['design', 'notes'], 'the logo reads DEMO')),
            ('element note', changed(g, ['design', 'elements', 0, 'note'], 'brand wall')),
            ('style mood', changed(g, ['design', 'style', 'mood'], ['warm'])),
            ('style industry', changed(g, ['design', 'style', 'industry'], 'coffee machines')),
            ('reference', changed(g, ['design', 'reference'], {'image': 'client.png', 'card': None, 'sha256': None})),
            ('requirements', changed(g, ['design', 'requirements'], [{'id': 'r1', 'kind': 'text', 'text': 'DEMO'}])),
            ('uncertainty text', changed(g, ['design', 'uncertainties'], [{'id': 'brand', 'description': 'the brand is DEMO'}])),
            ('provenance key', changed(g, ['design', 'style', 'provenance'], {'palette of the DEMO brand': 'reference'})),
            ('long id', changed(g, ['design', 'elements', screen, 'id'], 'screen_' + 'a' * roles.ID_MAX)),
            ('long role', changed(g, ['design', 'elements', screen, 'params'], {'role': 'r' * (roles.ID_MAX + 1)})),
            # unexpressible
            ('long what', changed(g, ['unexpressible', 0, 'what'], long_text)),
            ('long why', changed(g, ['unexpressible', 0, 'why'], long_text)),
            ('nearest kind not in the catalogue', changed(g, ['unexpressible', 0, 'nearest_kind'], 'robot')),
            # evidence
            ('evidence missing an element', changed(g, ['evidence', 'screen'], DROP)),
            ('evidence for no element', changed(g, ['evidence', 'ghost'], {'bbox_px': [0, 0, 5, 5], 'confidence': 'low'})),
            ('box turned over', changed(g, ['evidence', 'hero_wall', 'bbox_px'], [30, 2, 1, 40])),
            ('box of no width', changed(g, ['evidence', 'hero_wall', 'bbox_px'], [5, 2, 5, 40])),
            ('box outside the picture', changed(g, ['evidence', 'hero_wall', 'bbox_px'], [1, 2, W + 1, 40])),
            ('box below zero', changed(g, ['evidence', 'hero_wall', 'bbox_px'], [-1, 2, 30, 40])),
            # the compiler's checks
            ('type against open sides', changed(g, ['design', 'booth_type'], 'island')),
            ('wall kind on an open side', changed(g, ['design', 'elements', screen, 'side'], 'front')),
            ('duplicate ids', changed(g, ['design', 'elements', reception, 'id'], 'header')),
            ('id prefix', changed(changed(g, ['design', 'elements', screen, 'id'], 'header_screen'), ['evidence', 'header_screen'],
                                  {'bbox_px': [0, 0, 5, 5], 'confidence': 'low'})),
        ]
        request = reader_request()
        for label, doc in cases:
            with self.subTest(case=label):
                result = roles.validate('reader', doc, request)
                self.assertEqual((result.value, result.status), (roles.UNDECIDED, 'invalid'))
        # what is allowed: no text at all with the fixed sentence, the height note, an empty unexpressible list
        plain = good_reader()
        for e in plain['design']['elements']:
            e.pop('text', None)
        plain['design']['brief']['text'] = roles.READER_BRIEF
        plain['design']['uncertainties'] = [dict(roles.HEIGHT_NOTE, critical=False)]
        plain['unexpressible'] = []
        self.assertEqual(roles.validate('reader', plain, request).reasons, ())
        # the footprint the code uses is the fitted one, so a reader footprint out of range does not matter
        self.assertTrue(roles.validate('reader', changed(g, ['design', 'form', 'footprint_m'], [0.1, 0.1]), request).ok)

    def test_critic_answers_follow_the_type_table_and_the_request(self):
        g = good_critic()

        def finding(**kw):
            return dict(GOOD_FINDING, **kw)
        bad = [
            ('id not f<n>', [finding(id='F1')]),
            ('id with a word', [finding(id='finding_1')]),
            ('repeated id', [finding(), finding(type='wrong_size', direction='bigger')]),
            ('element not in the request', [finding(element_id='ghost')]),
            ('camera_off on an element', [finding(type='camera_off', direction=None)]),
            ('misplaced without a move', [finding(direction=None)]),
            ('misplaced bigger', [finding(direction='bigger')]),
            ('wrong_size left', [finding(type='wrong_size', direction='left')]),
            ('wrong_colour bigger', [finding(type='wrong_colour', direction='bigger')]),
            ('wrong_count darker', [finding(type='wrong_count', direction='darker')]),
            ('wrong_kind with a direction', [finding(type='wrong_kind', direction='left')]),
            ('extra with a direction', [finding(type='extra', direction='left')]),
            ('missing with a direction', [finding(type='missing', direction='up')]),
            ('camera_off with a direction', [finding(type='camera_off', element_id=None, direction='left')]),
            ('box below zero', [finding(bbox_px=[-1, 0, 5, 5])]),
            ('box of no width', [finding(bbox_px=[5, 0, 5, 5])]),
            ('box turned over', [finding(bbox_px=[0, 9, 5, 3])]),
        ]
        for label, findings in bad:
            with self.subTest(case=label):
                self.assertEqual(roles.validate('critic', dict(g, findings=findings), critic_request()).status, 'invalid')
        self.assertEqual(roles.validate('critic', dict(g, verdict='accept'), critic_request()).status, 'invalid', 'accept with findings')
        self.assertEqual(roles.validate('critic', dict(g, findings=[]), critic_request()).status, 'invalid', 'fix without findings')
        good = [
            ('accept, nothing found', dict(g, verdict='accept', findings=[])),
            ('give up', dict(g, verdict='give_up', findings=[])),
            ('whole picture', dict(g, findings=[finding(element_id=None, type='camera_off', direction=None, bbox_px=None)])),
            ('every type with its direction', dict(g, findings=[
                finding(id='f1'), finding(id='f2', type='wrong_size', direction='smaller'),
                finding(id='f3', type='wrong_colour', direction='darker'), finding(id='f4', type='wrong_count', direction='bigger'),
                finding(id='f5', type='wrong_kind', direction=None), finding(id='f6', type='extra', direction=None),
                finding(id='f7', type='missing', direction=None, element_id=None)])),
        ]
        for label, doc in good:
            with self.subTest(case=label):
                self.assertEqual(roles.validate('critic', doc, critic_request()).reasons, ())

    def test_wait_response_ignores_an_older_answer_and_never_waits_past_the_time_left(self):
        response, request = self.tmp / 'critic.response.json', self.tmp / 'critic.request.json'
        response.write_text('{"a": 1}', encoding='utf-8')
        request.write_text('{}', encoding='utf-8')
        old = request.stat().st_mtime - 100
        os.utime(response, (old, old))
        sleeps = []
        self.assertIsNone(roles.wait_response(response, 1000.0, newer_than=request, remaining_s=0.0, sleep=sleeps.append, clock=Clock(1.0)))
        self.assertEqual(sleeps, [], 'no time left: no wait at all')
        self.assertIsNone(roles.wait_response(response, 1000.0, newer_than=request, remaining_s=4.0, sleep=sleeps.append, clock=Clock(1.0)))
        self.assertLessEqual(len(sleeps), 5)
        new = request.stat().st_mtime + 1
        os.utime(response, (new, new))
        self.assertEqual(roles.wait_response(response, 0.0, newer_than=request, sleep=lambda s: None), {'a': 1})
        response.write_text('{oops', encoding='utf-8')
        self.assertIsInstance(roles.wait_response(response, 0.0, sleep=lambda s: None), roles.Unreadable)
        self.assertIs(orchestrator.Unreadable, roles.Unreadable, 'one notion of an unreadable answer')


class DietCase(Tmp):
    def inputs_for(self, name):
        pic = self.tmp / 'picture.png'
        if not pic.exists():
            Image.new('RGB', (W, H), (90, 90, 90)).save(pic)
        if name == 'reader':
            return {'picture.png': pic, 'catalog/catalog.json': b'{"schema_version": "catalog.v1"}', 'catalog/booth-design.v1.json': SCHEMA,
                    'catalog/designs/tv.json': b'{}', 'catalog/tv.png': png_bytes()}
        if name == 'critic':
            return {'el_hero_wall/original.png': png_bytes(), 'el_hero_wall/render.png': png_bytes((1, 1, 1)),
                    'el_hero_wall/overlay.png': png_bytes((255, 0, 255))}
        return {'original.png': pic, 'A.png': png_bytes(), 'B.png': png_bytes((1, 1, 1))}

    def request_for(self, name):
        if name == 'reader':
            return reader_request()
        if name == 'critic':
            return critic_request()
        return judge_request(name, int(name[-1]))

    def refused(self, name, payload, inputs, label):
        rd = self.tmp / f'refused_{re.sub(r"[^a-z0-9]+", "_", label.lower())}'
        with self.assertRaises(roles.RoleError):
            roles.write_request(name, rd, payload, inputs)
        self.assertFalse((rd / f'{name}_inputs').exists() or (rd / f'{name}.request.json').exists(), 'nothing written')

    def test_each_role_inputs_folder_contains_only_its_diet(self):
        rdir = self.tmp / 'round_2'
        for name in ('reader', 'critic', 'judge_p1', 'judge_p2', 'judge_best_p1', 'judge_best_p2'):
            with self.subTest(request=name):
                inputs = self.inputs_for(name)
                path = roles.write_request(name, rdir, self.request_for(name), inputs)
                self.assertEqual(path, rdir / f'{name}.request.json')
                self.assertEqual(jread(path), self.request_for(name))
                self.assertEqual(listing(rdir / f'{name}_inputs'), sorted(inputs))
        self.assertEqual((rdir / 'judge_p1_inputs' / 'original.png').read_bytes(), (self.tmp / 'picture.png').read_bytes())
        # anything outside a role's diet is refused before a single file is written
        refused = [
            ('reader', reader_request(), {'score.json': b'{}'}),
            ('reader', reader_request(), {'picture_2.png': png_bytes()}),                      # one picture only
            ('reader', reader_request(), {'catalog/../score.json': b'{}'}),
            ('reader', reader_request(), {'round_1/critic.response.json': b'{}'}),
            ('critic', critic_request(), {'el_hero_wall/design.json': b'{}'}),
            ('critic', critic_request(), {'score.json': b'{}'}),
            ('critic', critic_request(), {'picture.png': png_bytes()}),                        # the whole picture
            ('critic', critic_request(), {'hero_wall/original.png': png_bytes()}),             # crops live in el_<id>
            ('judge_p2', judge_request('judge_p2', 2), {'mapping.secret.json': b'{}'}),
            ('judge_p2', judge_request('judge_p2', 2), {'C.png': png_bytes()}),
            ('judge_p2', judge_request('judge_p2', 2), {'r2_c1.png': png_bytes()}),
        ]
        for k, (name, payload, extra) in enumerate(refused):
            with self.subTest(refused=list(extra)):
                self.refused(name, payload, {**self.inputs_for(name), **extra}, f'file {k}')
        # request fields outside the diet are refused too: no design, no score, no candidate source, no camera
        critic_el = critic_request()
        critic_el['elements'][0]['score'] = 61.5
        reader_cam = reader_request()
        reader_cam['box_fit']['camera'] = FIT['camera']
        judge_bad = judge_request()
        judge_bad['A'] = 'judge_p1_inputs/C.png'                           # names a file that is not given
        bad_payloads = [
            ('critic', dict(critic_request(), design={'elements': []})),
            ('critic', dict(critic_request(), score={'total': 61.5})),
            ('critic', critic_el),
            ('judge_p1', dict(judge_request(), candidates=['r2_c0', 'r2_c1'])),
            ('judge_p1', judge_bad),
            ('judge_p1', dict(judge_request(), response='judge_p2.response.json')),
            ('judge_p1', dict(judge_request(), role='critic')),
            ('reader', reader_cam),
            ('reader', dict(reader_request(), design_init={'elements': []})),
            ('reader', dict(reader_request(), previous_rounds=['round_1'])),
        ]
        for k, (name, payload) in enumerate(bad_payloads):
            with self.subTest(payload=k):
                self.refused(name, payload, self.inputs_for(name), f'payload {k}')
        with self.assertRaises(roles.RoleError):
            roles.write_request('critic', rdir, critic_request(), self.inputs_for('critic'))       # a request is never written twice

    def test_request_values_are_checked_not_only_their_names(self):
        def reader_with(path, value):
            return changed(reader_request(), path, value)

        def critic_with(path, value):
            return changed(critic_request(), path, value)

        def judge_with(key, value):
            return dict(judge_request(), **{key: value})
        cases = [
            ('reader', reader_with(['box_fit', 'dims_m', 'X'], 1.0)),
            ('reader', reader_with(['box_fit', 'dims_m', 'W'], -6.0)),
            ('reader', reader_with(['box_fit', 'dims_m', 'W'], '6 m')),
            ('reader', reader_with(['box_fit', 'dims_m', 'D'], DROP)),
            ('reader', reader_with(['box_fit', 'dims_m', 'H'], True)),
            ('reader', reader_with(['box_fit', 'image', 'width'], 0)),
            ('reader', reader_with(['box_fit', 'image', 'width'], 320.5)),
            ('reader', reader_with(['box_fit', 'image', 'depth'], 3)),
            ('reader', reader_with(['box_fit', 'image'], 'a 320 x 200 picture')),
            ('reader', reader_with(['picture_id'], 'p' * (roles.NAME_MAX + 1))),
            ('reader', reader_with(['picture_id'], 'the picture of the DEMO stand')),
            ('reader', reader_with(['catalog'], 'reader_inputs/catalog/other.json')),
            ('critic', critic_with(['elements', 0, 'bbox_px'], [1, 2, 3])),
            ('critic', critic_with(['elements', 0, 'bbox_px'], [30, 2, 1, 40])),
            ('critic', critic_with(['elements', 0, 'family'], 'robot_arm')),
            ('critic', critic_with(['elements', 0, 'family'], ['tv'])),
            ('critic', critic_with(['elements', 0, 'element_id'], 'Hero Wall')),
            ('critic', critic_with(['elements', 0, 'original_crop'], 'critic_inputs/hero_wall/original.png')),
            ('critic', critic_with(['elements', 0, 'overlay_crop'], 'critic_inputs/el_hero_wall/render.png')),
            ('critic', critic_with(['elements', 0, 'overlay_crop'], DROP)),
            ('critic', critic_with(['elements', 0, 'crop_origin_px'], [1])),
            ('critic', critic_with(['elements', 0, 'crop_origin_px'], [-1, 2])),
            ('critic', critic_with(['elements', 0, 'crop_origin_px'], [1, True])),
            ('critic', critic_with(['elements', 0, 'crop_origin_px'], DROP)),
            ('critic', critic_with(['elements', 0, 'crop_scale'], 0)),
            ('critic', critic_with(['elements', 0, 'crop_scale'], 2.5)),
            ('critic', critic_with(['elements', 0, 'crop_scale'], True)),
            ('critic', critic_with(['elements', 0, 'crop_scale'], DROP)),
            ('critic', critic_with(['elements'], critic_request()['elements'] * 2)),
            ('critic', critic_with(['candidate_id'], 'c' * (roles.NAME_MAX + 1))),
            ('critic', critic_with(['round'], 0)),
            ('judge_p1', judge_with('question', 'q' * (roles.QUESTION_MAX + 1))),
            ('judge_p1', judge_with('question', ['Which?'])),
            ('judge_p1', judge_with('pair_id', 'pair with spaces')),
            ('judge_p1', judge_with('pass', 3)),
            ('judge_p1', judge_with('judge_id', 'j9')),
            ('judge_p1', judge_with('original', 'judge_p1_inputs/A.png')),
        ]
        for k, (name, payload) in enumerate(cases):
            with self.subTest(case=k, request=name):
                self.refused(name, payload, self.inputs_for(name), f'value {k}')

    def test_write_request_reads_everything_first_and_leaves_nothing_half_written(self):
        rdir = self.tmp / 'round_1'
        inputs = self.inputs_for('judge_p1')
        missing = dict(inputs, **{'B.png': self.tmp / 'no_such_render.png'})
        with self.assertRaises(OSError):
            roles.write_request('judge_p1', rdir, judge_request(), missing)
        self.assertFalse((rdir / 'judge_p1_inputs').exists() or (rdir / 'judge_p1.request.json').exists(), 'nothing written')
        roles.write_request('judge_p1', rdir, judge_request(), inputs)                           # the same request again: fine
        self.assertEqual(listing(rdir / 'judge_p1_inputs'), ['A.png', 'B.png', 'original.png'])
        # a write that fails half way leaves nothing behind either
        real, calls = Path.write_bytes, []

        def flaky(self_, data):
            calls.append(self_)
            if len(calls) == 2:
                raise OSError('the disk is full')
            return real(self_, data)
        with mock.patch.object(Path, 'write_bytes', flaky), self.assertRaises(OSError):
            roles.write_request('judge_p2', rdir, judge_request('judge_p2', 2), self.inputs_for('judge_p2'))
        self.assertEqual(len(calls), 2)
        self.assertFalse((rdir / 'judge_p2_inputs').exists() or (rdir / 'judge_p2.request.json').exists(), 'no half-written request')
        roles.write_request('judge_p2', rdir, judge_request('judge_p2', 2), self.inputs_for('judge_p2'))
        self.assertTrue((rdir / 'judge_p2.request.json').is_file())

    def test_the_critic_diet_allows_the_overlay_and_nothing_else_new(self):
        rdir = self.tmp / 'round_diet'
        roles.write_request('critic', rdir, critic_request(), self.inputs_for('critic'))
        self.assertEqual(listing(rdir / 'critic_inputs'), ['el_hero_wall/original.png', 'el_hero_wall/overlay.png', 'el_hero_wall/render.png'])
        for extra in ('el_hero_wall/whole.png', 'picture.png', 'el_hero_wall/overlay2.png', 'el_hero_wall/mask.png'):
            with self.subTest(extra=extra):
                self.refused('critic', critic_request(), dict(self.inputs_for('critic'), **{extra: png_bytes()}), extra)

    def test_the_loop_writes_every_role_folder_with_only_its_diet(self):
        cat = self.tmp / 'catalog'
        cat.mkdir()
        (cat / 'tv.png').write_bytes(png_bytes((200, 10, 10), (40, 30)))          # one thumbnail made in the Blender lane
        catalog.write(cat)
        cfg = self.stub_cfg(max_rounds=2, catalog=cat)
        parts = orchestrator.stub_parts(cfg)
        parts['scorer'] = ScoreTable()
        orchestrator.run(cfg, clock=Clock(0.001), **parts)
        out = cfg.out
        folders = sorted(out.rglob('*_inputs'))
        names = {p.name[:-len('_inputs')] for p in folders}
        self.assertTrue({'reader', 'critic', 'judge_p1', 'judge_p2'} <= names, names)
        kinds = schema_kinds()
        for folder in folders:
            name, files = folder.name[:-len('_inputs')], listing(folder)
            with self.subTest(folder=folder.relative_to(out).as_posix()):
                if name == 'reader':
                    want = {'picture.png', 'catalog/catalog.json', 'catalog/booth-design.v1.json', 'catalog/tv.png'}
                    want |= {f'catalog/designs/{k}.json' for k in kinds}
                    self.assertEqual(set(files), want)
                elif name == 'critic':
                    self.assertTrue(files)
                    for f in files:
                        self.assertRegex(f, r'^el_[a-z][a-z0-9_]*/(original|render|overlay)\.png$')
                else:
                    self.assertEqual(files, ['A.png', 'B.png', 'original.png'])
        copied = jread(out / 'round_1' / 'reader_inputs' / 'catalog' / 'catalog.json')
        self.assertEqual([e['kind'] for e in copied['kinds']], kinds)
        self.assertEqual({e['kind']: e['thumbnail'] for e in copied['kinds'] if e['thumbnail']}, {'tv': 'tv.png'})
        req = jread(out / 'round_1' / 'reader.request.json')
        self.assertEqual(req['catalog'], 'reader_inputs/catalog/catalog.json')
        self.assertEqual(set(req['box_fit']), {'dims_m', 'image'}, 'sizes only: no camera, no open sides')
        crit = (out / 'round_1' / 'critic.request.json').read_text(encoding='utf-8')
        self.assertNotIn('score', crit.lower())
        self.assertNotIn('"design"', crit)
        for p in (1, 2):
            self.assertNotIn('r2_c', (out / 'round_2' / f'judge_p{p}.request.json').read_text(encoding='utf-8'))
        r1 = jread(out / 'round_1' / 'round.json')['responses']
        self.assertEqual((r1['reader'], r1['critic']), ('ok', 'ok'), 'the stand-ins pass the checks')
        self.assertEqual(jread(out / 'round_2' / 'round.json')['responses']['judge_p1'], 'ok')

    def test_without_a_catalog_folder_the_reader_gets_the_text_catalogue(self):
        picture, fit, truth, design = self.loop_inputs()
        argv = ['--picture', str(picture), '--fit', str(fit), '--truth', str(truth), '--design-init', str(design), '--stub', '--max-rounds', '1']
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(orchestrator.main(argv + ['--out', str(self.tmp / 'plain')]), 0)
        copied = jread(self.tmp / 'plain' / 'round_1' / 'reader_inputs' / 'catalog' / 'catalog.json')
        self.assertEqual([e['kind'] for e in copied['kinds']], schema_kinds())
        self.assertTrue(all(e['thumbnail'] is None for e in copied['kinds']))
        for extra, out in ((['--catalog', str(self.tmp / 'no_such_catalog')], 'nocat'), (['--picture-id', 'pic T'], 'badid')):
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                code = orchestrator.main(argv + ['--out', str(self.tmp / out)] + extra)
            self.assertEqual(code, 2, extra)
            self.assertTrue(err.getvalue().startswith('orchestrator:'))
            self.assertFalse((self.tmp / out).exists(), 'nothing is written')

    def test_an_element_id_windows_reserves_does_not_crash_the_loop(self):
        design = reader_design()
        for old, new in (('screen', 'aux'), ('meeting', 'con'), ('greens', 'nul')):
            design['elements'][element_index(design, old)]['id'] = new
        cfg = self.stub_cfg(max_rounds=2, design=design)
        s = orchestrator.run(cfg, clock=Clock(0.001), **orchestrator.stub_parts(cfg))
        self.assertEqual(s['rounds'], 2)
        req = jread(cfg.out / 'round_1' / 'critic.request.json')
        by_id = {e['element_id']: e for e in req['elements']}
        self.assertTrue({'aux', 'con', 'nul'} <= set(by_id), sorted(by_id))
        self.assertEqual(by_id['aux']['original_crop'], 'critic_inputs/el_aux/original.png')
        self.assertTrue((cfg.out / 'round_1' / 'critic_inputs' / 'el_aux' / 'render.png').is_file())

    def test_crops_that_cannot_be_written_are_logged_and_the_round_goes_on(self):
        cfg = self.stub_cfg(max_rounds=2)
        real = orchestrator.rolemod.write_request

        def no_crops(name, round_dir, payload, inputs):
            if name == 'critic' and inputs:
                raise OSError('cannot make the crop folder')
            return real(name, round_dir, payload, inputs)
        with mock.patch.object(orchestrator.rolemod, 'write_request', no_crops):
            s = orchestrator.run(cfg, clock=Clock(0.001), **orchestrator.stub_parts(cfg))
        self.assertEqual(s['rounds'], 2)
        self.assertIn('crops failed', (cfg.out / 'loop.log').read_text(encoding='utf-8'))
        self.assertEqual(jread(cfg.out / 'round_1' / 'critic.request.json')['elements'], [])
        self.assertEqual(jread(cfg.out / 'round_1' / 'round.json')['critic']['status'], 'ok')

        def nothing(name, round_dir, payload, inputs):
            if name == 'critic':
                raise OSError('the disk is full')
            return real(name, round_dir, payload, inputs)
        cfg = self.stub_cfg('out_full', max_rounds=2)
        with mock.patch.object(orchestrator.rolemod, 'write_request', nothing):
            s = orchestrator.run(cfg, clock=Clock(0.001), **orchestrator.stub_parts(cfg))
        self.assertEqual(s['rounds'], 2)
        self.assertEqual(jread(cfg.out / 'round_1' / 'round.json')['critic']['status'], 'no_response')


class CatalogCase(Tmp):
    def test_catalog_covers_every_kind_in_schema(self):
        kinds = schema_kinds()
        out = self.tmp / 'cat'
        doc = catalog.write(out)
        self.assertEqual(jread(out / 'catalog.json'), doc)
        self.assertEqual(doc['schema_version'], 'catalog.v1')
        self.assertEqual([e['kind'] for e in doc['kinds']], kinds, 'every kind of the schema, in its order')
        for entry in doc['kinds']:
            kind = entry['kind']
            with self.subTest(kind=kind):
                self.assertTrue(isinstance(entry['about'], str) and entry['about'].strip() and '\n' not in entry['about'])
                self.assertIsNone(entry['thumbnail'])
                design = jread(out / entry['design'])
                self.assertEqual(edits.validate_design(design), [])
                self.assertEqual(design_compile.validate_design(design), [], 'it passes the compiler checks too')
                self.assertIn(kind, [e['kind'] for e in design['elements']])
                _, report = design_compile.compile_design(design, self.tmp / 'compiled' / kind, canvas=(320, 200))
                self.assertTrue(report['valid'])
                built = {e['id'] for e in report['elements'] if e.get('built')}
                ids = {e['id'] for e in design['elements'] if e['kind'] == kind}
                self.assertTrue(ids <= built, report['dropped'])
                # the examples are written the way the reader must write
                answer = {'schema_version': 'reader.v1', 'picture_id': 'pic_T', 'design': design, 'unexpressible': [],
                          'evidence': {e['id']: {'bbox_px': [0, 0, W, H], 'confidence': 'low'} for e in design['elements']}}
                request = changed(reader_request(), ['box_fit', 'dims_m'], {'W': 4.0, 'D': 3.0})
                self.assertEqual(roles.validate('reader', answer, request).reasons, ())
        self.assertNotIn(b'\r\n', (out / 'catalog.json').read_bytes())

    def test_thumbnails_are_collected_from_the_render_root(self):
        out, renders = self.tmp / 'cat', self.tmp / 'renders'
        for n, colour in ((1, (1, 1, 1)), (2, (200, 0, 0))):
            d = renders / 'counter' / f'preview_00{n}'
            d.mkdir(parents=True)
            Image.new('RGBA', (40, 30), colour + (255,)).save(d / 'preview_render.png')
        doc = catalog.write(out, render_root=renders)
        entry = next(e for e in doc['kinds'] if e['kind'] == 'counter')
        self.assertEqual(entry['thumbnail'], 'counter.png')
        with Image.open(out / 'counter.png') as im:
            self.assertEqual(im.convert('RGB').getpixel((0, 0)), (200, 0, 0), 'the newest preview')
        loaded, files = catalog.load(out)
        self.assertEqual(loaded, doc)
        self.assertIn('counter.png', files)
        self.assertIn('booth-design.v1.json', files)
        (out / 'counter.png').unlink()
        loaded, files = catalog.load(out)
        self.assertIsNone(next(e for e in loaded['kinds'] if e['kind'] == 'counter')['thumbnail'], 'a missing thumbnail is not offered')
        self.assertNotIn('counter.png', files)

    def test_a_catalogue_folder_is_checked_before_the_run(self):
        good = self.tmp / 'good'
        catalog.write(good)

        def variant(label, edit):
            folder = self.tmp / label
            catalog.write(folder)
            doc = jread(folder / 'catalog.json')
            edit(folder, doc)
            (folder / 'catalog.json').write_text(json.dumps(doc, indent=2), encoding='utf-8')
            return folder

        def tv(doc):
            return next(e for e in doc['kinds'] if e['kind'] == 'tv')
        broken = {
            'missing_kind': lambda f, d: d['kinds'].pop(),
            'kind_twice': lambda f, d: d['kinds'].append(dict(d['kinds'][0])),
            'unknown_kind': lambda f, d: d['kinds'].append({'kind': 'robot_arm', 'about': 'x', 'design': 'designs/robot_arm.json',
                                                             'thumbnail': None}),
            'design_not_json': lambda f, d: (f / 'designs' / 'tv.json').write_text('{oops', encoding='utf-8'),
            'design_not_a_design': lambda f, d: (f / 'designs' / 'tv.json').write_text('[]', encoding='utf-8'),
            'design_of_another_kind': lambda f, d: (f / 'designs' / 'tv.json').write_bytes((f / 'designs' / 'chair.json').read_bytes()),
            'thumbnail_jpeg': lambda f, d: ((f / 'tv.png').write_bytes(png_bytes(fmt='JPEG')), tv(d).update(thumbnail='tv.png')),
            'thumbnail_text': lambda f, d: ((f / 'tv.png').write_text('not a picture', encoding='utf-8'), tv(d).update(thumbnail='tv.png')),
            'thumbnail_of_another_kind': lambda f, d: ((f / 'chair.png').write_bytes(png_bytes()), tv(d).update(thumbnail='chair.png')),
            'about_on_two_lines': lambda f, d: tv(d).update(about='one\ntwo'),
        }
        for label, edit in broken.items():
            with self.subTest(case=label):
                with self.assertRaises(catalog.CatalogError):
                    catalog.load(variant(label, edit))
        self.assertTrue(catalog.load(good)[0]['kinds'])
        # through the loop: exit code 2, nothing written
        picture, fit, truth, design = self.loop_inputs()
        argv = ['--picture', str(picture), '--fit', str(fit), '--truth', str(truth), '--design-init', str(design), '--stub', '--max-rounds', '1']
        for label in ('missing_kind', 'thumbnail_jpeg', 'design_not_json'):
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                code = orchestrator.main(argv + ['--out', str(self.tmp / f'run_{label}'), '--catalog', str(self.tmp / label)])
            self.assertEqual(code, 2, label)
            self.assertIn('--catalog', err.getvalue())
            self.assertFalse((self.tmp / f'run_{label}').exists())

    def test_catalog_cli(self):
        done = subprocess.run([sys.executable, str(CATALOG_SCRIPT), '--help'], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        for flag in ('--out', '--render-root', '--commands'):
            self.assertIn(flag, done.stdout)
        out = self.tmp / 'cli'
        done = subprocess.run([sys.executable, str(CATALOG_SCRIPT), '--out', str(out), '--render-root', str(self.tmp / 'r'), '--commands'],
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual([e['kind'] for e in jread(out / 'catalog.json')['kinds']], schema_kinds())
        for kind in schema_kinds():
            self.assertTrue(re.search(rf'design-compile .*designs[\\/]{kind}\.json', done.stdout), kind)
        self.assertIn(' preview ', done.stdout)
        self.assertIn('acquire', done.stdout)
        self.assertIn('release', done.stdout)


class PromptCase(unittest.TestCase):
    def text(self, name):
        return (PROMPTS / name).read_text(encoding='utf-8')

    def examples(self, name):
        return [json.loads(block) for block in re.findall(r'```json\n(.*?)```', self.text(name), re.S)]

    def test_prompts_name_their_contract_and_never_the_panel_folder(self):
        for name, words in (('reader.md', ('reader.v1', 'catalog.json', 'unexpressible', 'bbox_px', 'confidence', 'open_sides')),
                            ('critic.md', ('critic.v1', 'original.png', 'render.png', 'verdict', 'camera_off', 'el_<element_id>')),
                            ('judge.md', ('judge.v1', 'original', 'better', 'same', 'judge_p1', 'respond_to'))):
            with self.subTest(prompt=name):
                text = self.text(name)
                for w in words + ('brief 有三行', '代码会' if name != 'judge.md' else '作废'):
                    self.assertIn(w, text)
                self.assertNotIn('\r', text)
                self.assertNotIn('mapping.secret', text)
                self.assertNotIn('面板文件夹', text, 'every role writes to the full response path of its brief')
                self.assertNotIn('两个路径', text)
                self.assertLessEqual(len(text.splitlines()), 80, 'one page')
        self.assertIn('相对于 `catalog.json` 所在的文件夹', self.text('reader.md'))

    def test_the_critic_prompt_explains_the_overlay_and_the_box_in_picture_pixels(self):
        text = self.text('critic.md')
        for words in ('overlay.png', 'crop_origin_px', 'crop_scale', 'misplaced', 'wrong_size', 'bbox_px', '全图'):
            self.assertIn(words, text)
        self.assertIn('不要把错位报成', text)
        self.assertLessEqual(len(text.splitlines()), 80)

    def test_the_critic_prompt_spells_the_three_crop_paths_the_same_way(self):
        text = self.text('critic.md')
        spelled = re.findall(r'`((?:critic_inputs/)?el_<element_id>/(?:original|render|overlay)\.png)`', text)
        self.assertEqual(len(spelled), 3, spelled)
        self.assertEqual({p.split('el_<element_id>')[0] for p in spelled}, {'critic_inputs/'}, 'the same prefix as the request uses')

    def test_the_critic_prompt_says_what_the_directions_mean(self):
        text = self.text('critic.md')
        for words in ('原图里的东西比渲染里的大，就写 `bigger`', '比渲染里的小，就写 `smaller`', '原图比渲染暗，就写 `darker`',
                      '比渲染亮，就写 `lighter`', '渲染里的元素该往哪边移'):
            self.assertIn(words, text)
        table = text.index('| `type` |')
        self.assertLess(text.index('原图里的东西比渲染里的大'), table, 'said before the table, so that no row can be read the other way round')
        row = next(line for line in text.splitlines() if line.startswith('| `wrong_size`'))
        self.assertIn('渲染里的大小和原图不一样', row)
        self.assertNotIn('整体比原图', row)
        self.assertLessEqual(len(text.splitlines()), 80)

    def test_the_critic_prompt_asks_for_the_main_body_only(self):
        text = self.text('critic.md')
        self.assertIn('只框元素的主体', text)
        for word in ('立柱', '桌腿', '文字'):
            self.assertIn(word, text)
        self.assertLessEqual(len(text.splitlines()), 80)

    def test_the_critic_prompt_makes_the_box_optional(self):
        text = self.text('critic.md')
        self.assertIn('看得出', text)
        self.assertIn('看不准就写 `null`', text)
        self.assertIn('不要编', text)
        self.assertNotIn('必须给', text)
        self.assertIn('原图里的东西比渲染里的大，就写 `bigger`', text, 'the direction text stays')

    def test_the_judge_prompt_keeps_the_modes_apart_and_does_not_say_how_passes_are_counted(self):
        text = self.text('judge.md')
        rate, tie = text.index('## 评分模式'), text.index('## 破平局模式')
        self.assertLess(rate, tie)
        for words in ('互换', '不一致', '另一个裁判', '另一个评委'):
            self.assertNotIn(words, text)
        rating, tiebreak = self.examples('judge.md')
        self.assertEqual(rating['better'], 'same')
        self.assertTrue(rating['scores'] and rating['items'] is not None)
        self.assertEqual((tiebreak['scores'], tiebreak['items'], tiebreak['extra_parts']), (None, None, None))
        tiebreak.update(pair_id='pair_abc', judge_id='j1')
        self.assertEqual(roles.validate('judge_p1', tiebreak, judge_request()).reasons, ())

    def test_the_examples_in_the_prompts_pass_the_checks(self):
        (reader,) = self.examples('reader.md')
        reader['picture_id'] = 'pic_T'
        request = changed(reader_request(), ['box_fit', 'image'], {'width': 1000, 'height': 700})
        self.assertEqual(roles.validate('reader', reader, request).reasons, ())
        (critic,) = self.examples('critic.md')
        critic['candidate_id'], critic['findings'][0]['element_id'] = 'r1_c0', 'hero_wall'
        self.assertEqual(roles.validate('critic', critic, dict(critic_request(), round=1, candidate_id='r1_c0')).reasons, ())


if __name__ == '__main__':
    unittest.main()
