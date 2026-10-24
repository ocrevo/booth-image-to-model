"""P8a tests for the loop skeleton: stop rules, best candidate, folder layout, role files, render command lines.

Everything runs with the stand-ins (stubs.py); no model, no Blender, no real picture.
"""
from __future__ import annotations
import contextlib
import io
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parents[0]))        # tools/poc, so that `loop` is a package

from loop import edits, orchestrator, renderer, stubs  # noqa: E402

sys.path.append(str(ROOT / 'booth-image-to-model' / 'scripts'))
import camera_math  # noqa: E402
import design_compile  # noqa: E402
import validate_spec  # noqa: E402

TEMPLATE = ROOT / 'booth-image-to-model' / 'templates' / 'design_template.json'
W, H = 640, 400
FIT = {'status': 'fitted', 'dims_m': {'W': 6.0, 'D': 4.0}, 'image': {'width': W, 'height': H}, 'floor_z': 0.0,
       'camera': {'projection': 'perspective', 'position': [3.5, -7.6, 1.9], 'target': [0.0, 0.0, 1.4], 'lens_mm': 35.0,
                  'sensor_width_mm': 36.0, 'principal_point': [0.5, 0.5], 'roll_deg': 0.0, 'pixel_aspect': [1, 1], 'fit_status': 'landmark_fitted'}}


def jread(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def reader_design(design):
    """The template as a reader may write it (P8b): placeholder text only, no free-text fields."""
    d = json.loads(json.dumps(design))
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


def reader_answer(design, picture_id):
    """A reader.v1 answer with a box for every element."""
    return {'schema_version': 'reader.v1', 'picture_id': picture_id, 'design': design,
            'evidence': {e['id']: {'bbox_px': [0, 0, W, H], 'confidence': 'low'} for e in design['elements']}, 'unexpressible': []}


class FixedScorer:
    """Scores by candidate id from a table; unknown ids get `default`. The edge distance (what the loop chooses by) is derived from
    the total, a higher total meaning a smaller edge distance, unless `edge` is given: equal totals then tie on the edge distance."""

    def __init__(self, table=None, default=50.0, edge=None):
        self.table, self.default, self.edge, self.calls = table or {}, default, edge, []

    def score(self, cand_dir, preview_dir, candidate_id, design):
        self.calls.append(candidate_id)
        total = float(self.table.get(candidate_id, self.default))
        return {'schema_version': 'score.v1', 'picture_id': 'pic_T', 'candidate_id': candidate_id, 'spec_sha256': '0' * 64,
                'levels': {'L1': {'panel': None}, 'L2': {'code': None, 'panel': None}, 'L3': {'panel': None},
                           'L4': {'code': None, 'panel': None}},
                'total': total, 'aux': {'edge_distance_diag': (100.0 - total) / 500.0 if self.edge is None else self.edge, 'edge_distance_px': 1.0, 'holdout_median_diag': None,
                                        'colour_de_mean': None, 'booth_pixel_fraction': 0.5, 'second_view_ok': None},
                'status': 'blocked', 'reason': 'panel_missing'}


class Clock:
    def __init__(self, step_s):
        self.t, self.step = 0.0, step_s

    def __call__(self):
        self.t += self.step
        return self.t


class LoopCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.picture = self.tmp / 'picture.png'
        rng = np.random.default_rng(3)
        Image.fromarray(rng.integers(0, 255, (H, W, 3), dtype=np.uint8)).save(self.picture)
        self.fit = self.tmp / 'box_fit.json'
        self.fit.write_text(json.dumps(FIT), encoding='utf-8')
        self.truth = self.tmp / 'truth' / 'pic_T'
        self.truth.mkdir(parents=True)
        self.design_init = self.tmp / 'design.json'
        self.design_init.write_text(json.dumps(reader_design(jread(TEMPLATE)), indent=2), encoding='utf-8')
        self.n = 0

    def cfg(self, **kw):
        self.n += 1
        base = dict(picture=self.picture, fit=self.fit, truth=self.truth, design_init=self.design_init,
                    out=self.tmp / f'out{self.n}', stub=True)
        base.update(kw)
        return orchestrator.Config(**base)

    def stub_run(self, scorer=None, clock=None, **kw):
        cfg = self.cfg(**kw)
        parts = orchestrator.stub_parts(cfg)
        if scorer is not None:
            parts['scorer'] = scorer
        summary = orchestrator.run(cfg, clock=clock or Clock(0.001), **parts)
        return cfg, summary

    # -- the five tests_first
    def test_loop_stops_on_rounds_minutes_and_plateau(self):
        _, s = self.stub_run(max_rounds=2)
        self.assertEqual((s['rounds'], s['stop_reason']), (2, 'rounds'))

        cfg, s = self.stub_run(max_rounds=4, max_minutes=5.0, clock=Clock(120.0))      # two minutes per clock reading
        self.assertEqual(s['stop_reason'], 'minutes')
        self.assertLess(s['rounds'], 4)

        cfg, s = self.stub_run(max_minutes=0.0)                                          # --max-minutes 0 stops at once
        self.assertEqual((s['rounds'], s['stop_reason']), (0, 'minutes'))
        self.assertTrue((cfg.out / 'best' / 'design.json').is_file())
        self.assertFalse(jread(cfg.out / 'best' / 'best.json')['scored'])

        cfg, s = self.stub_run(scorer=FixedScorer(default=50.0), max_rounds=4)           # nothing ever improves
        self.assertEqual(s['stop_reason'], 'plateau')
        self.assertEqual(s['rounds'], 3, 'round 1 sets the best, rounds 2 and 3 do not improve it')

    def test_best_candidate_is_kept_not_the_last(self):
        table = {'r1_c0': 50.0, 'r2_c1': 80.0, 'r2_c2': 70.0, 'r2_c3': 60.0}
        scorer = FixedScorer(table, default=10.0, edge=0.01)
        cfg, s = self.stub_run(scorer=scorer, max_rounds=4)
        self.assertEqual(s['best']['candidate_id'], 'r2_c1')
        self.assertEqual(s['best']['total'], 80.0)
        last = scorer.calls[-1]
        self.assertLess(table.get(last, 10.0), 80.0, 'the last candidate scored is worse than the best one')
        best = jread(cfg.out / 'best' / 'best.json')
        self.assertEqual((best['candidate_id'], best['round'], best['scored']), ('r2_c1', 2, True))
        self.assertEqual(jread(cfg.out / 'best' / 'design.json'), jread(cfg.out / 'round_2' / 'candidates' / 'r2_c1' / 'design.json'))
        self.assertEqual(jread(cfg.out / 'best' / 'score.json')['total'], 80.0)

    def test_round_folder_layout_matches_contract(self):
        cfg, s = self.stub_run(max_rounds=4, plateau=False)
        out = cfg.out
        self.assertEqual((s['rounds'], s['stop_reason']), (4, 'rounds'), 'the stub runs all four rounds')
        for name in ('loop.json', 'loop.log'):
            self.assertTrue((out / name).is_file(), name)
        for f in ('design.json', 'score.json', 'best.json'):
            self.assertTrue((out / 'best' / f).is_file(), f)
        self.assertTrue((out / 'round_1' / 'reader.request.json').is_file())
        self.assertTrue((out / 'round_1' / 'reader.response.json').is_file())
        self.assertFalse((out / 'round_2' / 'reader.request.json').exists(), 'the reader runs once')
        for n in range(1, 5):
            rd = out / f'round_{n}'
            rj = jread(rd / 'round.json')
            self.assertEqual(rj['round'], n)
            cands = sorted(p.name for p in (rd / 'candidates').iterdir())
            self.assertTrue(1 <= len(cands) <= 4, 'at most 3 edited candidates plus the unchanged one')
            self.assertEqual(cands[0], f'r{n}_c0')
            for c in cands:
                cd = rd / 'candidates' / c
                self.assertTrue((cd / 'design.json').is_file() and (cd / 'score.json').is_file(), c)
                self.assertEqual(edits.validate_design(jread(cd / 'design.json')), [])
                if not c.endswith('_c0'):
                    self.assertEqual(edits.validate_fixer(jread(cd / 'fixer.json')), [])
                    self.assertTrue((cd / 'edit_log.json').is_file())
            self.assertIn(rj['winner'], cands)
            if n < 4:
                self.assertTrue((rd / 'critic.request.json').is_file(), n)
                self.assertTrue((rd / 'critic.response.json').is_file(), n)
                crops = list((rd / 'critic_inputs').glob('*/original.png'))
                self.assertTrue(crops, 'cropped picture pairs')
                for c in crops:
                    self.assertTrue((c.parent / 'render.png').is_file())
            else:
                self.assertFalse((rd / 'critic.request.json').exists(), 'no critic after the last round')
        self.assertGreater(len(list((out / 'round_2' / 'candidates').iterdir())), 1, 'edits produced candidates')
        self.assertEqual(jread(out / 'round_2' / 'candidates' / 'r2_c0' / 'base.json')['reused_from'], 'r1_c0')
        # the picture only reaches the critic as crops, never whole, and the critic never sees the reader's JSON
        req = jread(out / 'round_1' / 'critic.request.json')
        self.assertEqual(req['role'], 'critic')
        self.assertNotIn('picture', req)
        self.assertNotIn('score', json.dumps(req).lower())

    # -- more behaviour
    def test_the_candidate_starts_a_vertical_move_from_the_real_height_of_the_compiled_element(self):
        # in the stand-in world the compiled height equals z_m; here the compiler puts the counter 0.3 m up although the design has no z_m
        def lift(spec, design):
            if 'z_m' not in edits.find_element(design, 'reception'):          # the compiler's own default height, only without z_m
                for comp in spec['components']:
                    if comp['id'] == 'reception':
                        comp['location'][2] += 0.3
            return spec

        class Lifted(stubs.StubRenderer):
            def render(self_, cand_dir, design, candidate_id, time_left_s=None):
                out = super().render(cand_dir, design, candidate_id, time_left_s)
                path = Path(out) / 'spec.json'
                path.write_text(json.dumps(lift(json.loads(path.read_text(encoding='utf-8')), design)), encoding='utf-8')
                return out

            def compile_design(self_, design):
                return lift(super().compile_design(design), design)

        def findings(shown):
            self.assertIn('reception', shown)
            return [self.finding('reception', 'misplaced', 'up', severity='medium')]
        cfg = self.cfg(max_rounds=2)
        parts = orchestrator.stub_parts(cfg)
        parts['roles'] = self.fixed_critic(findings)(parts['roles'].target, parts['roles'].camera, parts['roles'].size)
        parts['renderer'] = Lifted(self.picture, FIT)
        orchestrator.run(cfg, clock=Clock(0.001), **parts)
        cdir = cfg.out / 'round_2' / 'candidates'
        edited = [jread(c / 'design.json') for c in sorted(cdir.iterdir()) if (c / 'fixer.json').is_file()]
        self.assertTrue(edited)
        for design in edited:
            z = edits.find_element(design, 'reception')['z_m']
            self.assertAlmostEqual(z, 0.35, places=3, msg='0.3 m real height + the 0.05 m step, not 0 + 0.05')
        fixer = jread(next(c for c in sorted(cdir.iterdir()) if (c / 'fixer.json').is_file()) / 'fixer.json')
        self.assertAlmostEqual(fixer['edits'][0]['delta_m'][2], 0.05, places=3, msg='delta_m is the real move')

    def test_the_re_applied_candidate_after_a_dropped_resize_still_starts_from_the_real_height(self):
        def lift(spec, design):
            if 'z_m' not in edits.find_element(design, 'reception'):
                for comp in spec['components']:
                    if comp['id'] == 'reception':
                        comp['location'][2] += 0.3
            return spec

        class Lifted(stubs.StubRenderer):
            def render(self_, cand_dir, design, candidate_id, time_left_s=None):
                out = super().render(cand_dir, design, candidate_id, time_left_s)
                path = Path(out) / 'spec.json'
                path.write_text(json.dumps(lift(json.loads(path.read_text(encoding='utf-8')), design)), encoding='utf-8')
                return out

            def compile_design(self_, design):
                d = json.loads(json.dumps(design))
                edits.find_element(d, 'reception').pop('size_m', None)         # the compiler ignores the size: the resize will be dropped
                return lift(super().compile_design(d), design)

        def findings(shown):
            self.assertIn('reception', shown)
            return [self.finding('reception', 'misplaced', 'up', severity='medium'), self.finding('reception', 'wrong_size', 'bigger')]
        cfg = self.cfg(max_rounds=2)
        parts = orchestrator.stub_parts(cfg)
        parts['roles'] = self.fixed_critic(findings)(parts['roles'].target, parts['roles'].camera, parts['roles'].size)
        parts['renderer'] = Lifted(self.picture, FIT)
        orchestrator.run(cfg, clock=Clock(0.001), **parts)
        self.assertIn('resize dropped', (cfg.out / 'loop.log').read_text(encoding='utf-8'))
        cdir = cfg.out / 'round_2' / 'candidates'
        edited = [c for c in sorted(cdir.iterdir()) if (c / 'fixer.json').is_file()]
        self.assertTrue(edited)
        for c in edited:
            self.assertEqual([e['op'] for e in jread(c / 'fixer.json')['edits']], ['move'], 'the resize is gone')
            self.assertAlmostEqual(edits.find_element(jread(c / 'design.json'), 'reception')['z_m'], 0.35, places=3,
                                   msg='0.3 m real height + 0.05 m: the re-applied move starts from the real height too')

    def test_a_critic_box_outside_the_picture_is_taken_as_null_and_logged(self):
        def findings(shown):
            return [dict(self.finding('reception', 'misplaced', 'right', severity='medium'), bbox_px=[W + 50, H + 20, W + 300, H + 200])]
        cfg = self.cfg(max_rounds=2)
        parts = orchestrator.stub_parts(cfg)
        parts['roles'] = self.fixed_critic(findings)(parts['roles'].target, parts['roles'].camera, parts['roles'].size)
        orchestrator.run(cfg, clock=Clock(0.001), **parts)
        self.assertEqual(jread(cfg.out / 'round_1' / 'round.json')['responses']['critic'], 'ok', 'the answer stays valid')
        self.assertIn('outside the picture', (cfg.out / 'loop.log').read_text(encoding='utf-8'))
        fixer = jread(cfg.out / 'round_2' / 'candidates' / 'r2_c1' / 'fixer.json')
        self.assertAlmostEqual(math.hypot(*fixer['edits'][0]['delta_m'][:2]), 0.05, delta=0.01, msg='the small step')
        self.assertTrue(orchestrator.rolemod.box_inside([1, 2, 30, 40], (640, 400)))
        for box in ([-1, 2, 30, 40], [1, 2, 641, 40], [1, 2, 30, 401], [W + 50, H + 20, W + 300, H + 200]):
            self.assertFalse(orchestrator.rolemod.box_inside(box, (640, 400)), box)

    def test_fixer_json_records_the_change_the_compiler_built_not_the_predicted_one(self):
        class Half(stubs.StubRenderer):             # the stand-in compiler builds half of any move of the counter
            def render(self_, cand_dir, design, candidate_id, time_left_s=None):
                self_.base = getattr(self_, 'base', design)
                return super().render(cand_dir, design, candidate_id, time_left_s)

            def compile_design(self_, design):
                spec = super().compile_design(design)
                bx, by, _ = edits.element_centre(self_.base, edits.find_element(self_.base, 'reception'))
                for comp in spec['components']:
                    if comp['id'] == 'reception':
                        comp['location'][0] = bx + (comp['location'][0] - bx) / 2
                        comp['location'][1] = by + (comp['location'][1] - by) / 2
                return spec

        def findings(shown):
            self.assertIn('reception', shown)
            return [self.finding('reception', 'misplaced', 'right', severity='high')]
        cfg = self.cfg(max_rounds=2)
        parts = orchestrator.stub_parts(cfg)
        parts['roles'] = self.fixed_critic(findings)(parts['roles'].target, parts['roles'].camera, parts['roles'].size)
        parts['renderer'] = Half(self.picture, FIT)
        orchestrator.run(cfg, clock=Clock(0.001), **parts)
        cdir = cfg.out / 'round_2' / 'candidates'
        edited = [c for c in sorted(cdir.iterdir()) if (c / 'fixer.json').is_file()]
        self.assertTrue(edited)
        start = jread(cfg.out / 'round_1' / 'candidates' / 'r1_c0' / 'design.json')
        for c in edited:
            design = jread(c / 'design.json')
            x0, y0, _ = edits.element_centre(start, edits.find_element(start, 'reception'))
            x1, y1, _ = edits.element_centre(design, edits.find_element(design, 'reception'))
            recorded = jread(c / 'fixer.json')['edits'][0]['delta_m']
            self.assertAlmostEqual(recorded[0], (x1 - x0) / 2, delta=0.0003, msg='half of the predicted move: what was built')
            self.assertAlmostEqual(recorded[1], (y1 - y0) / 2, delta=0.0003)
            self.assertGreater(math.hypot(x1 - x0, y1 - y0), 0.05, 'the design itself got the whole move')

    def test_the_default_stop_rules_with_the_plateau_rule_on_still_run_four_rounds_in_the_stand_in_world(self):
        cfg, s = self.stub_run(max_rounds=4)                      # plateau on, as every real run
        self.assertEqual((s['rounds'], s['stop_reason']), (4, 'rounds'))
        totals = [jread(cfg.out / f'round_{n}' / 'round.json')['winner_total'] for n in range(1, s['rounds'] + 1)]
        self.assertEqual(totals, sorted(totals))

    def test_scores_improve_with_stub_roles(self):
        cfg, s = self.stub_run(max_rounds=4, plateau=False)
        totals = [jread(cfg.out / f'round_{n}' / 'round.json')['winner_total'] for n in range(1, 5)]
        self.assertEqual(totals, sorted(totals))
        self.assertGreater(totals[-1], totals[0])

    def test_tie_writes_two_independent_judge_requests_and_stub_judge_keeps_the_unchanged_candidate(self):
        cfg, s = self.stub_run(scorer=FixedScorer(default=50.0), max_rounds=2)
        rd = cfg.out / 'round_2'
        self.assertEqual(jread(rd / 'round.json')['winner'], 'r2_c0')
        self.assertEqual(jread(rd / 'round.json')['decided_by'], 'judge')
        self.assertFalse((rd / 'judge.request.json').exists(), 'the two passes are two requests, not one')
        for p in (1, 2):
            req = jread(rd / f'judge_p{p}.request.json')
            self.assertEqual(req['pass'], p)
            self.assertEqual(req['judge_id'], f'j{p}', 'pass 1 is judge j1, pass 2 is judge j2')
            self.assertNotIn('r2_c', json.dumps(req), 'the judge does not see where a render came from')
            self.assertEqual(sorted(x.name for x in (rd / f'judge_p{p}_inputs').iterdir()), ['A.png', 'B.png', 'original.png'])
            doc = jread(rd / f'judge_p{p}.response.json')
            self.assertEqual((doc['schema_version'], doc['pass']), ('judge.v1', p), 'one judge.v1 object, not an array')
        self.assertEqual(jread(rd / 'judge_p1.request.json')['pair_id'], jread(rd / 'judge_p2.request.json')['pair_id'])
        key = jread(cfg.out / '_keys' / 'round_2_judge.json')
        self.assertEqual(key['pass1']['A'], key['pass2']['B'], 'the second pass swaps A and B')
        self.assertEqual(key['pass1']['B'], key['pass2']['A'])

    def test_no_judge_when_the_scores_differ(self):
        cfg, _ = self.stub_run(max_rounds=2)
        self.assertFalse(list((cfg.out / 'round_2').glob('judge*')))
        self.assertEqual(jread(cfg.out / 'round_2' / 'round.json')['decided_by'], 'edge')

    def judge_round(self, mode):
        class Roles(orchestrator.CheckedStubRoles):
            def judge(self_, ctx):
                if mode == 'p1_only' and ctx['pass'] == 2:
                    return None
                target = ctx['order'][-1]                       # the edited candidate, not the unchanged one
                doc = super().judge(ctx)
                doc['better'] = 'A' if ctx['key']['A'] == target else 'B'
                if mode == 'disagree':
                    doc['better'] = 'A'                          # A is a different candidate in each pass
                if mode == 'pass_float':
                    doc['pass'] = float(ctx['pass'])
                if mode == 'pass_bool' and ctx['pass'] == 1:
                    doc['pass'] = True
                if mode == 'wrong_id' and ctx['pass'] == 1:
                    doc['judge_id'] = 'j2'                       # pass 1 is j1 in the request
                return doc
        cfg = self.cfg(max_rounds=2)
        parts = orchestrator.stub_parts(cfg)
        parts['roles'] = Roles(parts['roles'].target, parts['roles'].camera)
        parts['scorer'] = FixedScorer(default=50.0)
        return cfg, orchestrator.run(cfg, clock=Clock(0.001), **parts)

    def test_judge_needs_both_passes_to_name_the_same_candidate(self):
        cfg, _ = self.judge_round('both')
        rj = jread(cfg.out / 'round_2' / 'round.json')
        self.assertEqual((rj['winner'], rj['decided_by']), ('r2_c1', 'judge'))
        for mode in ('p1_only', 'disagree', 'pass_float', 'pass_bool', 'wrong_id'):
            with self.subTest(mode=mode):
                cfg, _ = self.judge_round(mode)
                rj = jread(cfg.out / 'round_2' / 'round.json')
                self.assertEqual((rj['winner'], rj['decided_by']), ('r2_c0', 'judge_unavailable'), 'one pass is not a verdict')

    def test_edge_distance_decides_before_the_judge(self):
        class Scorer(FixedScorer):
            def score(self, cand_dir, preview_dir, candidate_id, design):
                doc = super().score(cand_dir, preview_dir, candidate_id, design)
                doc['aux']['edge_distance_diag'] = {'c0': 0.05, 'c1': 0.01}.get(candidate_id[-2:], 0.03)
                return doc
        cfg, _ = self.stub_run(scorer=Scorer(default=50.0), max_rounds=2)
        rj = jread(cfg.out / 'round_2' / 'round.json')
        self.assertEqual(rj['decided_by'], 'edge')
        self.assertNotEqual(rj['winner'], 'r2_c0')
        self.assertFalse((cfg.out / 'round_2' / 'judge.request.json').exists())

    def test_unusable_response_files_do_not_crash_the_round(self):
        cfg = self.cfg(max_rounds=3)
        bad = {'reader': '[1, 2', 'critic': '{not json'}

        def respond(seconds=0):                                   # the "commander": answers every request with garbage
            for rd in cfg.out.glob('round_*'):
                for role, text in bad.items():
                    if (rd / f'{role}.request.json').exists() and not (rd / f'{role}.response.json').exists():
                        (rd / f'{role}.response.json').write_text(text, encoding='utf-8')

        parts = orchestrator.stub_parts(cfg)
        parts['roles'] = orchestrator.FileRoles(timeout_s=5.0, sleep=respond, clock=Clock(0.001))
        summary = orchestrator.run(cfg, clock=Clock(0.001), **parts)
        self.assertEqual(summary['rounds'], 3)
        crit = jread(cfg.out / 'round_1' / 'round.json')['critic']
        self.assertEqual((crit['valid'], crit['status']), (False, 'invalid'))
        self.assertTrue((cfg.out / 'best' / 'design.json').is_file())
        log = (cfg.out / 'loop.log').read_text(encoding='utf-8')
        self.assertIn('reader_fallback', log)
        self.assertIn('invalid response', log)

    def test_missing_response_is_logged_apart_from_an_invalid_one(self):
        cfg = self.cfg(max_rounds=2)
        parts = orchestrator.stub_parts(cfg)
        parts['roles'] = orchestrator.FileRoles(timeout_s=0.0, sleep=lambda s: None)
        orchestrator.run(cfg, clock=Clock(0.001), **parts)
        log = (cfg.out / 'loop.log').read_text(encoding='utf-8')
        self.assertIn('reader: no response', log)
        self.assertNotIn('invalid response', log)
        self.assertEqual(jread(cfg.out / 'round_1' / 'round.json')['critic']['status'], 'no_response')

    def test_answers_of_the_wrong_type_never_crash_the_loop(self):
        class Roles(orchestrator.CheckedStubRoles):
            def critic(self_, ctx):
                doc = super().critic(ctx)
                doc['findings'][0]['severity'] = ['high']          # a list where a string belongs
                return doc

            def judge(self_, ctx):
                doc = super().judge(ctx)
                doc['pass'] = float(ctx['pass'])
                return doc
        cfg = self.cfg(max_rounds=3)
        parts = orchestrator.stub_parts(cfg)
        parts['roles'] = Roles(parts['roles'].target, parts['roles'].camera)
        parts['scorer'] = FixedScorer(default=50.0)
        summary = orchestrator.run(cfg, clock=Clock(0.001), **parts)
        self.assertEqual(jread(cfg.out / 'round_1' / 'round.json')['critic']['status'], 'invalid')
        self.assertTrue((cfg.out / 'best' / 'best.json').is_file() and (cfg.out / 'loop.json').is_file())
        self.assertIn('rounds', summary)

    def test_response_checkers_are_strict_about_types(self):
        good_finding = {'id': 'f1', 'element_id': 'a', 'type': 'misplaced', 'severity': 'high', 'direction': 'left', 'bbox_px': None}
        good = {'schema_version': 'critic.v1', 'round': 1, 'candidate_id': 'r1_c0', 'findings': [good_finding], 'verdict': 'fix'}
        self.assertEqual(orchestrator.critic_problems(good, 1, 'r1_c0'), [])
        for field, value in (('severity', ['high']), ('severity', {'x': 1}), ('type', ['misplaced']), ('direction', ['left']),
                             ('id', 5), ('element_id', 3), ('bbox_px', [1, 2, 3]), ('bbox_px', [1, 2, True, 4]), ('bbox_px', 'x')):
            doc = dict(good, findings=[dict(good_finding, **{field: value})])
            self.assertTrue(orchestrator.check(orchestrator.critic_problems, doc, 1, 'r1_c0'), (field, value))
        for field, value in (('round', True), ('round', 1.0), ('round', [1]), ('verdict', ['fix']), ('candidate_id', ['r1_c0']),
                             ('findings', {'a': 1})):
            self.assertTrue(orchestrator.check(orchestrator.critic_problems, dict(good, **{field: value}), 1, 'r1_c0'), (field, value))
        self.assertTrue(orchestrator.check(orchestrator.critic_problems, [good], 1, 'r1_c0'))

        j = {'schema_version': 'judge.v1', 'pair_id': 'pair_x', 'judge_id': 'j1', 'pass': 1, 'better': 'A', 'scores': None,
             'items': None, 'extra_parts': None, 'confidence': 'low'}
        self.assertEqual(orchestrator.judge_problems(j, 'pair_x', 1, 'j1'), [])
        for field, value in (('pass', 1.0), ('pass', True), ('pass', [1]), ('pass', 2), ('better', ['A']), ('better', 'C'),
                             ('judge_id', ['j1']), ('judge_id', 'j2'), ('judge_id', 'j3'), ('pair_id', 'pair_y'), ('scores', {'L2': 1}), ('extra_parts', 2),
                             ('confidence', ['low']), ('items', {}), ('items', {'L1:a': 'present'})):
            self.assertTrue(orchestrator.check(orchestrator.judge_problems, dict(j, **{field: value}), 'pair_x', 1, 'j1'), (field, value))
        self.assertTrue(orchestrator.check(orchestrator.judge_problems, [j], 'pair_x', 1, 'j1'))

        reader = reader_answer(reader_design(jread(TEMPLATE)), 'pic_A')
        self.assertEqual(orchestrator.reader_problems(reader), [])
        for bad in (dict(reader, picture_id=['pic_A']), dict(reader, design={'schema_version': 'booth-design.v1'}),
                    dict(reader, evidence={'a': {'bbox_px': [1, 2, 3, 4], 'confidence': ['high']}}), dict(reader, extra=1),
                    dict(reader, unexpressible=[{'what': 'x'}]), [reader], 'text', None):
            self.assertTrue(orchestrator.check(orchestrator.reader_problems, bad))

    def test_a_run_is_never_repeated_into_the_same_folder(self):
        out = self.tmp / 'again'
        argv = ['--picture', str(self.picture), '--fit', str(self.fit), '--truth', str(self.truth),
                '--design-init', str(self.design_init), '--out', str(out), '--stub', '--max-rounds', '1']
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(orchestrator.main(argv), 0)
        before = sorted((str(p.relative_to(out)), p.stat().st_mtime_ns) for p in out.rglob('*'))
        err = io.StringIO()
        target = self.tmp / 'target.json'
        target.write_text(TEMPLATE.read_text(encoding='utf-8'), encoding='utf-8')
        with contextlib.redirect_stderr(err):
            self.assertEqual(orchestrator.main(argv + ['--stub-target', str(target)]), 2)
        self.assertIn('not empty', err.getvalue())
        self.assertEqual(before, sorted((str(p.relative_to(out)), p.stat().st_mtime_ns) for p in out.rglob('*')), 'nothing was written')
        empty = self.tmp / 'empty_dir'
        empty.mkdir()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(orchestrator.main(argv[:-5] + ['--out', str(empty), '--stub', '--max-rounds', '1']), 0)

    # -- second verification round
    def test_a_critic_accept_or_give_up_does_not_stop_the_loop(self):
        class Roles(orchestrator.CheckedStubRoles):
            def critic(self_, ctx):
                doc = super().critic(ctx)
                if ctx['round'] == 1:
                    doc['verdict'], doc['findings'] = 'accept', []      # accept goes with no findings (P8b)
                else:
                    doc['verdict'] = 'give_up'                          # findings stay, but nobody acts on them
                return doc
        cfg = self.cfg(max_rounds=3, plateau=False)
        parts = orchestrator.stub_parts(cfg)
        parts['roles'] = Roles(parts['roles'].target, parts['roles'].camera)
        parts['scorer'] = FixedScorer(default=50.0)
        s = orchestrator.run(cfg, clock=Clock(0.001), **parts)
        self.assertEqual((s['rounds'], s['stop_reason']), (3, 'rounds'))
        self.assertEqual(jread(cfg.out / 'round_1' / 'round.json')['critic']['verdict'], 'accept')
        self.assertEqual([p.name for p in (cfg.out / 'round_2' / 'candidates').iterdir()], ['r2_c0'], 'no edits after accept')
        self.assertEqual([p.name for p in (cfg.out / 'round_3' / 'candidates').iterdir()], ['r3_c0'], 'no edits after give_up')

    def test_no_plateau_runs_every_round(self):
        _, s = self.stub_run(scorer=FixedScorer(default=50.0), max_rounds=4)
        self.assertEqual((s['rounds'], s['stop_reason']), (3, 'plateau'))
        _, s = self.stub_run(scorer=FixedScorer(default=50.0), max_rounds=4, plateau=False)
        self.assertEqual((s['rounds'], s['stop_reason']), (4, 'rounds'))
        out = self.tmp / 'np'
        argv = ['--picture', str(self.picture), '--fit', str(self.fit), '--truth', str(self.truth), '--design-init', str(self.design_init),
                '--out', str(out), '--stub', '--no-plateau']
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(orchestrator.main(argv), 0)
        self.assertEqual(jread(out / 'loop.json')['rounds'], 4)

    def test_max_minutes_must_be_a_finite_number_not_below_zero(self):
        for bad in ('nan', 'inf', '-inf', '-1', '-0.5'):
            with self.subTest(value=bad):
                out = self.tmp / f'mm_{bad}'
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    code = orchestrator.main(['--picture', str(self.picture), '--fit', str(self.fit), '--truth', str(self.truth),
                                              '--design-init', str(self.design_init), '--out', str(out), '--stub',
                                              f'--max-minutes={bad}'])
                self.assertEqual(code, 2)
                self.assertIn('--max-minutes', err.getvalue())
                self.assertFalse(out.exists(), 'nothing is written')

    def test_a_winner_judged_against_the_unchanged_copy_of_the_best_is_not_judged_again(self):
        cfg, _ = self.judge_round('both')
        rd = cfg.out / 'round_2'
        rj = jread(rd / 'round.json')
        self.assertEqual((rj['winner'], rj['decided_by'], rj['improved'], rj['vs_best']), ('r2_c1', 'judge', True, 'judge_reused'))
        self.assertFalse(list(rd.glob('judge_best*')), 'the pair was just judged: no judge_best request')
        self.assertFalse((cfg.out / '_keys' / 'round_2_judge_best.json').exists())
        self.assertEqual(jread(cfg.out / 'best' / 'best.json')['candidate_id'], 'r2_c1')

    def test_round_json_records_response_states_and_the_real_spec_path(self):
        cfg, _ = self.stub_run(scorer=FixedScorer(default=50.0), max_rounds=2)
        r1, r2 = jread(cfg.out / 'round_1' / 'round.json'), jread(cfg.out / 'round_2' / 'round.json')
        self.assertEqual(r1['responses'], {'reader': 'ok', 'critic': 'ok', 'judge_p1': None, 'judge_p2': None,
                                           'judge_best_p1': None, 'judge_best_p2': None})
        self.assertEqual((r2['responses']['reader'], r2['responses']['judge_p1'], r2['responses']['judge_p2']), (None, 'ok', 'ok'))
        self.assertEqual(r1['winner_spec'], 'round_1/candidates/r1_c0/preview/spec.json')
        self.assertEqual(r2['winner'], 'r2_c0')
        self.assertEqual(r2['winner_spec'], 'round_1/candidates/r1_c0/preview/spec.json', 'the unchanged candidate resolves to its source')
        self.assertTrue((cfg.out / r2['winner_spec']).is_file())

    def test_round_json_marks_missing_and_invalid_responses(self):
        cfg = self.cfg(max_rounds=1)
        parts = orchestrator.stub_parts(cfg)
        parts['roles'] = orchestrator.FileRoles(timeout_s=0.0, sleep=lambda s: None)
        orchestrator.run(cfg, clock=Clock(0.001), **parts)
        self.assertEqual(jread(cfg.out / 'round_1' / 'round.json')['responses']['reader'], 'no_response')
        bad = self.cfg(max_rounds=2)
        parts = orchestrator.stub_parts(bad)

        class Roles(orchestrator.CheckedStubRoles):
            def critic(self_, ctx):
                doc = super().critic(ctx)
                doc['round'] = True
                return doc
        parts['roles'] = Roles(parts['roles'].target, parts['roles'].camera)
        orchestrator.run(bad, clock=Clock(0.001), **parts)
        self.assertEqual(jread(bad.out / 'round_1' / 'round.json')['responses']['critic'], 'invalid')

    def test_a_reader_design_survives_when_no_candidate_can_be_rendered(self):
        template = reader_design(jread(TEMPLATE))

        class Failing:
            def render(self_, cand_dir, design, candidate_id, time_left_s=None):
                raise renderer.RenderError('no Blender here')
        roles = self.reader_roles(lambda ctx: reader_answer(template, ctx['picture_id']))(None, None)
        cfg = self.cfg(stub=False, design_init=None, max_rounds=2)
        s = orchestrator.run(cfg, clock=Clock(0.001), roles=roles, renderer=Failing(), scorer=FixedScorer())
        self.assertEqual((s['rounds'], s['stop_reason'], s['best']), (0, 'no_candidates', None))
        best = jread(cfg.out / 'best' / 'best.json')
        self.assertFalse(best['scored'])
        self.assertEqual(jread(cfg.out / 'best' / 'design.json')['form']['footprint_m'], [6.0, 4.0])
        self.assertEqual(edits.validate_design(jread(cfg.out / 'best' / 'design.json')), [])

    def test_a_response_that_cannot_be_written_as_utf8_is_invalid(self):
        class Roles(orchestrator.CheckedStubRoles):
            def critic(self_, ctx):
                doc = super().critic(ctx)
                doc['findings'][0]['id'] = 'f1\udc00'            # a lone surrogate passes every type check
                return doc
        cfg = self.cfg(max_rounds=2)
        parts = orchestrator.stub_parts(cfg)
        parts['roles'] = Roles(parts['roles'].target, parts['roles'].camera)
        s = orchestrator.run(cfg, clock=Clock(0.001), **parts)
        self.assertEqual(jread(cfg.out / 'round_1' / 'round.json')['responses']['critic'], 'invalid')
        self.assertIn('UTF-8', (cfg.out / 'loop.log').read_text(encoding='utf-8'))
        self.assertIn('rounds', s)

    # -- third verification round
    def test_the_smaller_edge_distance_wins_whatever_the_total_says(self):
        class Scorer(FixedScorer):
            def score(self_, cand_dir, preview_dir, candidate_id, design):
                doc = super().score(cand_dir, preview_dir, candidate_id, design)
                doc['total'], doc['aux']['edge_distance_diag'] = {
                    'r1_c0': (60.0, 0.015), 'r2_c1': (40.0, 0.010)}.get(candidate_id, (10.0, 0.5))      # c1: lower total, edge 0.005 better
                return doc
        cfg, s = self.stub_run(scorer=Scorer(), max_rounds=2)
        rj = jread(cfg.out / 'round_2' / 'round.json')
        self.assertEqual((rj['winner'], rj['decided_by'], rj['improved']), ('r2_c1', 'edge', True))
        self.assertEqual(s['best']['candidate_id'], 'r2_c1', 'best follows the edge distance too')
        self.assertFalse(list((cfg.out / 'round_2').glob('judge*')))
        self.assertEqual([c['total'] for c in rj['candidates'] if c['id'] == 'r2_c1'], [40.0], 'the total is still recorded')

    def test_a_small_edge_difference_goes_to_the_judge_not_to_the_total(self):
        class Scorer(FixedScorer):
            def score(self_, cand_dir, preview_dir, candidate_id, design):
                doc = super().score(cand_dir, preview_dir, candidate_id, design)
                doc['total'], doc['aux']['edge_distance_diag'] = {
                    'r1_c0': (60.0, 0.0150), 'r2_c1': (90.0, 0.0145)}.get(candidate_id, (10.0, 0.5))    # 0.0005 apart: a tie
                return doc
        cfg, _ = self.stub_run(scorer=Scorer(), max_rounds=2)
        rd = cfg.out / 'round_2'
        self.assertTrue((rd / 'judge_p1.request.json').is_file(), 'under 0.002 apart the judge is asked, the total does not decide')
        self.assertEqual(jread(rd / 'round.json')['decided_by'], 'judge')

    def test_the_unchanged_winner_is_never_compared_with_its_own_copy(self):
        cfg, _ = self.stub_run(scorer=FixedScorer(default=50.0), max_rounds=2)
        rd = cfg.out / 'round_2'
        rj = jread(rd / 'round.json')
        self.assertEqual((rj['winner'], rj['improved'], rj['vs_best']), ('r2_c0', False, 'same_candidate'))
        self.assertFalse(list(rd.glob('judge_best*')), 'no judge request for a candidate against itself')
        self.assertFalse((cfg.out / '_keys' / 'round_2_judge_best.json').exists())

    def test_nan_and_infinity_are_not_accepted_in_a_response(self):
        template = jread(TEMPLATE)
        for token in ('NaN', 'Infinity', '-Infinity'):
            with self.subTest(token=token):
                path = self.tmp / f'nan_{token}.json'
                path.write_bytes(('{"schema_version": "reader.v1", "x": %s}' % token).encode())
                got = orchestrator.FileRoles(timeout_s=0.0, sleep=lambda s: None).wait_json(path)
                self.assertIsInstance(got, orchestrator.Unreadable)
        # a whole reader answer whose design carries a NaN along: the schema alone would let it through
        template = reader_design(template)
        design = json.loads(json.dumps(template))
        design['elements'][0]['along'] = float('nan')
        reader = reader_answer(design, 'pic_T')
        self.assertEqual(orchestrator.check(orchestrator.reader_problems, dict(reader, design=template)), [])
        cfg = self.cfg(max_rounds=1)
        parts = orchestrator.stub_parts(cfg)
        parts['roles'] = self.reader_roles(lambda ctx: reader)(parts['roles'].target, parts['roles'].camera)
        s = orchestrator.run(cfg, clock=Clock(0.001), **parts)
        self.assertEqual(jread(cfg.out / 'round_1' / 'round.json')['responses']['reader'], 'invalid')
        self.assertEqual(s['rounds'], 1)
        # the same through a file written by a person
        cfg2 = self.cfg(max_rounds=1)

        def respond(seconds=0):
            req = cfg2.out / 'round_1' / 'reader.request.json'
            resp = cfg2.out / 'round_1' / 'reader.response.json'
            if req.exists() and not resp.exists():
                text = json.dumps(dict(reader, design=template)).replace(f'"bbox_px": [0, 0, {W}, {H}]', f'"bbox_px": [0, 0, NaN, {H}]', 1)
                self.assertIn('NaN', text)
                resp.write_text(text, encoding='utf-8')
        parts = orchestrator.stub_parts(cfg2)
        parts['roles'] = orchestrator.FileRoles(timeout_s=5.0, sleep=respond, clock=Clock(0.001))
        orchestrator.run(cfg2, clock=Clock(0.001), **parts)
        self.assertEqual(jread(cfg2.out / 'round_1' / 'round.json')['responses']['reader'], 'invalid')

    def test_a_lone_surrogate_in_a_key_does_not_break_the_log(self):
        template = jread(TEMPLATE)
        reader = {'schema_version': 'reader.v1', 'picture_id': 'pic_A', 'design': template,
                  'evidence': {'e\udc00': {'bbox_px': [0, 0, 1, 1], 'confidence': 'bogus'}}, 'unexpressible': []}
        cfg = self.cfg(max_rounds=1)
        parts = orchestrator.stub_parts(cfg)
        parts['roles'] = self.reader_roles(lambda ctx: reader)(parts['roles'].target, parts['roles'].camera)
        orchestrator.run(cfg, clock=Clock(0.001), **parts)
        self.assertEqual(jread(cfg.out / 'round_1' / 'round.json')['responses']['reader'], 'invalid')
        log = cfg.out / 'loop.log'
        self.assertIn('invalid response', log.read_text(encoding='utf-8'))
        # and the log itself never fails on an odd character, whatever reaches it
        orchestrator.Log(self.tmp / 'x.log', Clock(0.001), 0.0).line('odd \udc00 text')
        self.assertIn('\\udc00', (self.tmp / 'x.log').read_text(encoding='utf-8'))

    def test_operator_mistakes_exit_with_code_two_and_write_nothing(self):
        def run_with(fit=None, picture=None, name='op'):
            out = self.tmp / name
            fit_path = self.tmp / f'{name}_fit.json'
            fit_path.write_text(json.dumps(FIT if fit is None else fit), encoding='utf-8')
            argv = ['--picture', str(picture or self.picture), '--fit', str(fit_path), '--truth', str(self.truth),
                    '--design-init', str(self.design_init), '--out', str(out), '--stub']
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                code = orchestrator.main(argv)
            return code, err.getvalue(), out
        bad_dims = dict(FIT, dims_m=[6.0, 4.0])
        no_camera = {k: v for k, v in FIT.items() if k != 'camera'}
        bad_camera = dict(FIT, camera={'position': [0, 0, 1]})
        wrong_size = dict(FIT, image={'width': 10, 'height': 10})
        not_fitted = dict(FIT, status='check_failed')
        text = self.tmp / 'not_a_picture.png'
        text.write_text('hello', encoding='utf-8')
        cases = {'array': ([1, 2, 3], None), 'dims_list': (bad_dims, None), 'no_camera': (no_camera, None),
                 'bad_camera': (bad_camera, None), 'wrong_size': (wrong_size, None), 'not_fitted': (not_fitted, None),
                 'not_an_image': (None, text), 'no_picture': (None, self.tmp / 'missing.png')}
        for name, (fit, picture) in cases.items():
            with self.subTest(case=name):
                code, err, out = run_with(fit, picture, name)
                self.assertEqual(code, 2, err)
                self.assertTrue(err.startswith('orchestrator:'), err)
                self.assertFalse(out.exists(), 'nothing is written')
        code, err, out = run_with(name='ok')
        self.assertEqual(code, 0)

    # -- fourth verification round
    def edge_round(self, edges, judge=None, extra_scorer=None, max_rounds=2):
        """A run whose candidates get the given edge distances ({id: value or None}, 0.5 for the others)."""
        class Scorer(FixedScorer):
            def score(self_, cand_dir, preview_dir, candidate_id, design):
                doc = super().score(cand_dir, preview_dir, candidate_id, design)
                doc['aux']['edge_distance_diag'] = edges.get(candidate_id, 0.5)
                return doc

        class Roles(orchestrator.CheckedStubRoles):
            def judge(self_, ctx):
                if judge == 'none':
                    return None
                if judge == 'other':
                    doc = super().judge(ctx)
                    doc['better'] = 'A' if ctx['key']['A'] == ctx['order'][-1] else 'B'
                    return doc
                return super().judge(ctx)
        cfg = self.cfg(max_rounds=max_rounds)
        parts = orchestrator.stub_parts(cfg)
        parts['roles'] = Roles(parts['roles'].target, parts['roles'].camera)
        parts['scorer'] = Scorer(default=50.0)
        return cfg, orchestrator.run(cfg, clock=Clock(0.001), **parts)

    def test_the_leader_wins_when_the_unchanged_candidate_is_not_in_the_tie(self):
        # c0 is 0.003 behind the leader; c1 is within 0.002 of it. The judge cannot tell: the smaller of the two judged wins.
        cfg, s = self.edge_round({'r1_c0': 0.1000, 'r2_c1': 0.0985, 'r2_c2': 0.0970}, judge='none')
        rj = jread(cfg.out / 'round_2' / 'round.json')
        self.assertEqual((rj['winner'], rj['decided_by'], rj['improved'], rj['vs_best']), ('r2_c2', 'judge_unavailable', True, 'edge'))
        key = jread(cfg.out / '_keys' / 'round_2_judge.json')
        self.assertEqual({key['pass1']['A'], key['pass1']['B']}, {'r2_c1', 'r2_c2'}, 'the two smallest were judged, not c0')
        self.assertEqual(s['best']['candidate_id'], 'r2_c2')

    def test_the_unchanged_candidate_in_the_tie_stays_when_the_judge_cannot_tell(self):
        cfg, _ = self.edge_round({'r1_c0': 0.1000, 'r2_c1': 0.0990, 'r2_c2': 0.0985}, judge='none')
        rj = jread(cfg.out / 'round_2' / 'round.json')
        self.assertEqual((rj['winner'], rj['decided_by'], rj['improved']), ('r2_c0', 'judge_unavailable', False))
        key = jread(cfg.out / '_keys' / 'round_2_judge.json')
        self.assertEqual({key['pass1']['A'], key['pass1']['B']}, {'r2_c0', 'r2_c2'}, 'the leader is judged against the unchanged one')

    def test_when_the_unchanged_candidate_leads_it_is_judged_against_the_next_smallest(self):
        cfg, _ = self.edge_round({'r1_c0': 0.0980, 'r2_c1': 0.0990, 'r2_c2': 0.0985}, judge='other')
        key = jread(cfg.out / '_keys' / 'round_2_judge.json')
        self.assertEqual({key['pass1']['A'], key['pass1']['B']}, {'r2_c0', 'r2_c2'})
        self.assertEqual(jread(cfg.out / 'round_2' / 'round.json')['winner'], 'r2_c2')

    def test_a_candidate_without_an_edge_distance_cannot_win(self):
        cfg, _ = self.edge_round({'r1_c0': 0.05, 'r2_c1': None, 'r2_c2': 0.05, 'r2_c3': 0.05}, judge='other')
        rj = jread(cfg.out / 'round_2' / 'round.json')
        key = jread(cfg.out / '_keys' / 'round_2_judge.json')
        self.assertNotIn('r2_c1', (key['pass1']['A'], key['pass1']['B']))
        self.assertNotEqual(rj['winner'], 'r2_c1')
        cfg, _ = self.edge_round({'r1_c0': 0.05, 'r2_c1': None, 'r2_c2': 0.04}, judge='none')
        self.assertEqual(jread(cfg.out / 'round_2' / 'round.json')['winner'], 'r2_c2')
        # no candidate has an edge distance: the unchanged candidate stays, nobody is judged
        cfg, _ = self.edge_round({'r1_c0': None, 'r2_c1': None, 'r2_c2': None, 'r2_c3': None}, judge='other')
        rj = jread(cfg.out / 'round_2' / 'round.json')
        self.assertEqual((rj['winner'], rj['decided_by'], rj['improved']), ('r2_c0', 'no_edge', False))
        self.assertFalse(list((cfg.out / 'round_2').glob('judge*')))

    def test_a_winner_without_an_edge_distance_does_not_replace_the_best(self):
        cfg, _ = self.edge_round({'r1_c0': 0.05, 'r2_c0': 0.05, 'r2_c1': None, 'r2_c2': None, 'r2_c3': None}, judge='other')
        self.assertEqual(jread(cfg.out / 'best' / 'best.json')['candidate_id'], 'r1_c0')

    def test_an_edge_difference_of_exactly_0_002_is_not_a_tie(self):
        # 0.003925 - 0.001925 is 0.0019999999999999996 in floating point; score.py gives six decimals, so the gap is 0.002: no tie
        cfg, _ = self.edge_round({'r1_c0': 0.003925, 'r2_c1': 0.001925}, judge='none')
        rj = jread(cfg.out / 'round_2' / 'round.json')
        self.assertEqual((rj['winner'], rj['decided_by'], rj['improved'], rj['vs_best']), ('r2_c1', 'edge', True, 'edge'))
        self.assertFalse(list((cfg.out / 'round_2').glob('judge*')))
        cfg, _ = self.edge_round({'r1_c0': 0.003924, 'r2_c1': 0.001925}, judge='none')       # 0.001999 apart: still a tie
        self.assertTrue((cfg.out / 'round_2' / 'judge_p1.request.json').is_file())
        self.assertEqual(jread(cfg.out / 'round_2' / 'round.json')['winner'], 'r2_c0')

    def test_reader_invalid_still_writes_round_one_and_the_reader_state(self):
        template = jread(TEMPLATE)
        cfg = self.cfg(stub=False, design_init=None, max_rounds=2)
        roles = self.reader_roles(lambda ctx: {'schema_version': 'reader.v1'})(None, None)
        s = orchestrator.run(cfg, clock=Clock(0.001), roles=roles, renderer=stubs.StubRenderer(self.picture, FIT), scorer=FixedScorer())
        rj = jread(cfg.out / 'round_1' / 'round.json')
        self.assertEqual(rj['responses']['reader'], 'invalid')
        for key in ('seconds', 'vs_best', 'no_improve_rounds', 'best_so_far', 'winner_spec', 'critic', 'judge_outcome'):
            self.assertIn(key, rj)
        self.assertEqual(jread(cfg.out / 'loop.json')['reader'], 'invalid')
        self.assertEqual(s['stop_reason'], 'reader_invalid')
        cfg = self.cfg(stub=False, design_init=None, max_rounds=2)
        orchestrator.run(cfg, clock=Clock(0.001), roles=orchestrator.FileRoles(timeout_s=0.0, sleep=lambda s: None),
                         renderer=stubs.StubRenderer(self.picture, FIT), scorer=FixedScorer())
        self.assertEqual(jread(cfg.out / 'round_1' / 'round.json')['responses']['reader'], 'no_response')
        self.assertEqual(jread(cfg.out / 'loop.json')['reader'], 'no_response')

    def test_a_round_without_a_scorable_candidate_has_every_round_field(self):
        class Failing:
            def render(self_, cand_dir, design, candidate_id, time_left_s=None):
                raise renderer.RenderError('no Blender here')
        cfg = self.cfg(stub=False, max_rounds=2)
        orchestrator.run(cfg, clock=Clock(0.001), roles=orchestrator.CheckedStubRoles(stubs.make_target(jread(TEMPLATE)), FIT['camera']),
                         renderer=Failing(), scorer=FixedScorer())
        rj = jread(cfg.out / 'round_1' / 'round.json')
        full = {'round', 'candidates', 'winner', 'winner_total', 'decided_by', 'improved', 'vs_best', 'no_improve_rounds', 'best_so_far',
                'critic', 'responses', 'winner_spec', 'seconds', 'judge_outcome'}
        self.assertEqual(set(rj), full)
        self.assertIsNone(rj['judge_outcome'])
        self.assertEqual((rj['winner'], rj['vs_best'], rj['best_so_far'], rj['no_improve_rounds']), (None, None, None, 0))

    def test_unusable_numbers_and_texts_in_the_inputs_exit_with_code_two(self):
        def run_with(name, fit=None, design_bytes=None, target_bytes=None):
            out = self.tmp / name
            fit_path = self.tmp / f'{name}_fit.json'
            fit_path.write_text(json.dumps(FIT if fit is None else fit), encoding='utf-8')
            design = self.tmp / f'{name}_design.json'
            design.write_bytes(design_bytes if design_bytes is not None else self.design_init.read_bytes())
            argv = ['--picture', str(self.picture), '--fit', str(fit_path), '--truth', str(self.truth), '--design-init', str(design),
                    '--out', str(out), '--stub', '--max-rounds', '1']
            if target_bytes is not None:
                target = self.tmp / f'{name}_target.json'
                target.write_bytes(target_bytes)
                argv += ['--stub-target', str(target)]
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                code = orchestrator.main(argv)
            return code, err.getvalue(), out
        template = self.design_init.read_text(encoding='utf-8')
        cases = {
            'sensor_zero': dict(fit=dict(FIT, camera=dict(FIT['camera'], sensor_width_mm=0))),
            'lens_zero': dict(fit=dict(FIT, camera=dict(FIT['camera'], lens_mm=0.0))),
            'position_text': dict(fit=dict(FIT, camera=dict(FIT['camera'], position='x'))),
            'camera_on_target': dict(fit=dict(FIT, camera=dict(FIT['camera'], position=FIT['camera']['target']))),
            'design_surrogate': dict(design_bytes=template.replace('"XXXX XXXXXX"', '"\\ud800"', 1).encode()),
            'design_infinite': dict(design_bytes=template.replace('"height_limit_m": 3.5', '"height_limit_m": 1e999').encode()),
            'target_null': dict(target_bytes=b'null'),
            'target_list': dict(target_bytes=b'[1, 2]'),
            'target_invalid': dict(target_bytes=b'{"schema_version": "booth-design.v1"}'),
        }
        for name, kw in cases.items():
            with self.subTest(case=name):
                code, err, out = run_with(name, **kw)
                self.assertEqual(code, 2, err)
                self.assertTrue(err.startswith('orchestrator:'), err)
                self.assertFalse(out.exists(), 'nothing is written')

    def test_a_response_file_that_says_null_is_invalid_not_missing(self):
        path = self.tmp / 'null.json'
        path.write_text('null', encoding='utf-8')
        self.assertIsInstance(orchestrator.FileRoles(timeout_s=0.0, sleep=lambda s: None).wait_json(path), orchestrator.Unreadable)
        cfg = self.cfg(max_rounds=1)

        def respond(seconds=0):
            req, resp = cfg.out / 'round_1' / 'reader.request.json', cfg.out / 'round_1' / 'reader.response.json'
            if req.exists() and not resp.exists():
                resp.write_text('null', encoding='utf-8')
        parts = orchestrator.stub_parts(cfg)
        parts['roles'] = orchestrator.FileRoles(timeout_s=5.0, sleep=respond, clock=Clock(0.001))
        orchestrator.run(cfg, clock=Clock(0.001), **parts)
        self.assertEqual(jread(cfg.out / 'round_1' / 'round.json')['responses']['reader'], 'invalid')

    def test_max_minutes_zero_still_gives_the_fitted_footprint(self):
        d = jread(self.design_init)
        d['form']['footprint_m'] = [9.0, 9.0]
        self.design_init.write_text(json.dumps(d), encoding='utf-8')
        cfg, s = self.stub_run(max_minutes=0.0)
        self.assertEqual(s['rounds'], 0)
        self.assertEqual(jread(cfg.out / 'best' / 'design.json')['form']['footprint_m'], [6.0, 4.0])

    # -- P8-fix: candidates by type, remembered designs, the overlay, centimetre offsets
    def fixed_critic(self, make):
        """Stand-in roles whose critic says what `make(shown_element_ids)` returns (a list of findings)."""
        class Roles(stubs.StubRoles):
            def critic(self_, ctx):
                shown = {e['element_id'] for e in ctx['request']['elements']}
                found = make(shown)
                for k, f in enumerate(found, start=1):
                    f['id'] = f'f{k}'
                return {'schema_version': 'critic.v1', 'round': ctx['round'], 'candidate_id': ctx['candidate_id'], 'findings': found,
                        'verdict': 'fix' if found else 'accept'}
        return Roles

    @staticmethod
    def finding(eid, typ, direction, severity='high'):
        return {'id': 'f0', 'element_id': eid, 'type': typ, 'severity': severity, 'direction': direction, 'bbox_px': None}

    def mixed_findings(self, shown):
        need = {'reception', 'hero_wall', 'greens'}
        self.assertTrue(need <= shown, f'{sorted(need - shown)} not in the request')
        return [self.finding('reception', 'misplaced', 'right'), self.finding('reception', 'wrong_size', 'bigger'),
                self.finding('hero_wall', 'wrong_colour', 'darker'), self.finding('greens', 'wrong_count', 'bigger')]

    def test_the_three_candidates_are_geometry_only_colour_only_and_all(self):
        cfg = self.cfg(max_rounds=2)
        parts = orchestrator.stub_parts(cfg)
        parts['roles'] = self.fixed_critic(self.mixed_findings)(parts['roles'].target, parts['roles'].camera, parts['roles'].size)
        orchestrator.run(cfg, clock=Clock(0.001), **parts)
        rd = cfg.out / 'round_2' / 'candidates'
        self.assertEqual(sorted(p.name for p in rd.iterdir()), ['r2_c0', 'r2_c1', 'r2_c2', 'r2_c3'])
        for cid, variant, ops in (('r2_c1', 'geometry', {'move', 'resize'}), ('r2_c2', 'colour', {'recolour'}),
                                  ('r2_c3', 'all', {'move', 'resize', 'recolour', 'set_count'})):
            fixer = jread(rd / cid / 'fixer.json')
            self.assertEqual({e['op'] for e in fixer['edits']}, ops, cid)
            self.assertEqual(jread(rd / cid / 'edit_log.json')['variant'], variant)

    def test_a_design_that_was_tried_is_not_proposed_again(self):
        class Scorer(FixedScorer):          # the unchanged candidate is always the best: every edit loses
            def score(self_, cand_dir, preview_dir, candidate_id, design):
                doc = super().score(cand_dir, preview_dir, candidate_id, design)
                doc['aux']['edge_distance_diag'] = 0.01 if candidate_id.endswith('_c0') else 0.5
                return doc
        cfg = self.cfg(max_rounds=3, plateau=False)
        parts = orchestrator.stub_parts(cfg)
        parts['roles'] = self.fixed_critic(self.mixed_findings)(parts['roles'].target, parts['roles'].camera, parts['roles'].size)
        parts['scorer'] = Scorer()
        orchestrator.run(cfg, clock=Clock(0.001), **parts)
        self.assertEqual(len(list((cfg.out / 'round_2' / 'candidates').iterdir())), 4)
        self.assertEqual([p.name for p in (cfg.out / 'round_3' / 'candidates').iterdir()], ['r3_c0'],
                         'the same three designs came back in round 3')

    def test_the_critic_gets_an_overlay_and_the_place_of_every_crop(self):
        cfg, _ = self.stub_run(max_rounds=2)
        rd = cfg.out / 'round_1'
        request = jread(rd / 'critic.request.json')
        self.assertTrue(request['elements'])
        for e in request['elements']:
            folder = rd / 'critic_inputs' / f"el_{e['element_id']}"
            self.assertEqual(sorted(p.name for p in folder.iterdir()), ['original.png', 'overlay.png', 'render.png'])
            self.assertEqual(e['overlay_crop'], f"critic_inputs/el_{e['element_id']}/overlay.png")
            x0, y0, x1, y1 = e['bbox_px']
            self.assertEqual(e['crop_origin_px'], [x0, y0], 'the top left corner of the crop in the whole picture')
            side = max(x1 - x0, y1 - y0)
            scale = min(orchestrator.CROP_MAX_SCALE, max(1, -(-orchestrator.CROP_MIN_SIDE // side)))
            self.assertEqual(e['crop_scale'], scale)
            with Image.open(folder / 'original.png') as o, Image.open(folder / 'overlay.png') as v, Image.open(folder / 'render.png') as r:
                self.assertEqual(o.size, ((x1 - x0) * scale, (y1 - y0) * scale))
                self.assertEqual((v.size, r.size), (o.size, o.size))
                a, b = np.asarray(o.convert('RGB')), np.asarray(v.convert('RGB'))
                changed = (a != b).any(axis=2)
                self.assertTrue(changed.any(), 'the render outline is drawn on the picture crop')
                self.assertTrue((b[changed] == np.array(orchestrator.OVERLAY_COLOUR)).all(), 'in one conspicuous colour')
        for key in request:
            self.assertNotIn('picture', key)
        whole = {(W, H)}
        for png in (rd / 'critic_inputs').rglob('*.png'):
            with Image.open(png) as im:
                self.assertTrue(im.size not in whole or png.name == 'x', f'{png} is as large as the whole picture')

    def test_a_four_centimetre_offset_gets_smaller_in_one_round_and_is_not_overshot(self):
        template = jread(TEMPLATE)
        target = stubs.make_target(template, shift_m=0.04)
        tpath = self.tmp / 'target4.json'
        tpath.write_text(json.dumps(target), encoding='utf-8')
        cfg = self.cfg(max_rounds=2, plateau=False, stub_target=tpath)
        s = orchestrator.run(cfg, clock=Clock(0.001), **orchestrator.stub_parts(cfg))
        start = jread(cfg.out / 'round_1' / 'candidates' / 'r1_c0' / 'design.json')
        rj = jread(cfg.out / 'round_2' / 'round.json')
        won = jread(cfg.out / 'round_2' / 'candidates' / rj['winner'] / 'design.json')
        moved = [e['id'] for e in target['elements'] if edits.element_centre(target, e) != edits.element_centre(start, edits.find_element(start, e['id']))]
        self.assertTrue(moved)

        def error(design, eid):
            a, b = edits.element_centre(design, edits.find_element(design, eid)), edits.element_centre(target, edits.find_element(target, eid))
            return math.hypot(a[0] - b[0], a[1] - b[1])
        before = sum(error(start, e) for e in moved)
        after = sum(error(won, e) for e in moved)
        self.assertGreater(before, 0.05)
        self.assertLess(after, before, 'one round made the error smaller')
        for e in moved:
            self.assertLessEqual(error(won, e), error(start, e) + 1e-6, f'{e} was moved the wrong way or too far')
        # no candidate overshoots: every edited candidate is closer to the target than the start in total
        for cdir in (cfg.out / 'round_2' / 'candidates').iterdir():
            if cdir.name.endswith('_c0'):
                continue
            d = jread(cdir / 'design.json')
            self.assertLess(sum(error(d, e) for e in moved), before, cdir.name)
        self.assertGreater(s['rounds'], 1)

    # -- P8-fix, second round: margins, small renders, recompiled resizes
    def test_the_crop_margin_is_a_quarter_of_the_box_and_at_least_six_pixels(self):
        self.assertEqual(orchestrator.crop_margin(100, 40), (25, 10))
        self.assertEqual(orchestrator.crop_margin(20, 20), (6, 6))
        self.assertEqual(orchestrator.crop_margin(400, 8), (100, 6))
        comp = {'id': 'slab_0', 'group': 'slab', 'kind': 'box', 'location': [0.0, 0.0, 1.0], 'rotation_deg': [0, 0, 0],
                'params': {'size': [3.0, 1.0, 1.5]}}
        spec = {'camera': FIT['camera'], 'source': {'width': W, 'height': H}, 'components': [comp]}
        tight = [uv for uv in (camera_math.project(list(pt), FIT['camera'], W, H)[:2] for pt in edits.component_points(comp))]
        raw_w = max(u for u, _ in tight) - min(u for u, _ in tight)
        x0, y0, x1, y1 = orchestrator.element_pixel_boxes(['slab'], spec)['slab']
        self.assertGreaterEqual((x1 - x0) - raw_w, 2 * 0.25 * raw_w - 3, 'a quarter of the width on each side')
        self.assertGreaterEqual(min(tight)[0] - x0, 0.25 * raw_w - 2, 'so that a 30 px offset of a 120 px box is still in the crop')

    def test_a_render_smaller_than_the_picture_is_a_handled_error_not_an_index_error(self):
        pic, small = self.tmp / 'pic.png', self.tmp / 'small.png'
        Image.new('RGB', (W, H), (10, 20, 30)).save(pic)
        Image.new('RGBA', (W // 2, H // 2), (10, 20, 30, 255)).save(small)
        with self.assertRaises(orchestrator.InputError):
            orchestrator.crop_pairs(pic, small, {'a': [10, 10, W - 10, H - 10]})

        class Small(stubs.StubRenderer):
            def render(self_, cand_dir, design, candidate_id, time_left_s=None):
                out = super().render(cand_dir, design, candidate_id, time_left_s)
                Image.new('RGBA', (W // 2, H // 2), (10, 20, 30, 255)).save(out / 'preview_render.png')
                return out
        cfg = self.cfg(max_rounds=2)
        parts = orchestrator.stub_parts(cfg)
        parts['renderer'] = Small(self.picture, FIT)
        s = orchestrator.run(cfg, clock=Clock(0.001), **parts)
        self.assertEqual(s['rounds'], 2)
        self.assertIn('crops failed', (cfg.out / 'loop.log').read_text(encoding='utf-8'))
        self.assertEqual(jread(cfg.out / 'round_1' / 'critic.request.json')['elements'], [])

    def resize_run(self, renderer_class=None):
        def findings(shown):
            self.assertIn('reception', shown)
            return [self.finding('reception', 'misplaced', 'right'), self.finding('reception', 'wrong_size', 'bigger')]
        cfg = self.cfg(max_rounds=2)
        parts = orchestrator.stub_parts(cfg)
        parts['roles'] = self.fixed_critic(findings)(parts['roles'].target, parts['roles'].camera, parts['roles'].size)
        if renderer_class:
            parts['renderer'] = renderer_class(self.picture, FIT)
        orchestrator.run(cfg, clock=Clock(0.001), **parts)
        ops = {}
        for cdir in sorted((cfg.out / 'round_2' / 'candidates').iterdir()):
            if (cdir / 'fixer.json').is_file():
                ops[cdir.name] = [e['op'] for e in jread(cdir / 'fixer.json')['edits']]
        return cfg, ops

    def test_a_resize_that_the_recompile_confirms_is_kept(self):
        cfg, ops = self.resize_run()
        self.assertTrue(any('resize' in v for v in ops.values()), ops)
        self.assertTrue(any('move' in v and 'resize' in v for v in ops.values()), ops)
        self.assertNotIn('resize dropped', (cfg.out / 'loop.log').read_text(encoding='utf-8'))

    def test_a_resize_that_the_recompile_shows_going_the_wrong_way_is_dropped_and_logged(self):
        class Opposite(stubs.StubRenderer):                 # whatever is asked, the built thing comes out half the size
            def compile_design(self_, design):
                d = json.loads(json.dumps(design))
                for e in d['elements']:
                    if e.get('size_m'):
                        e['size_m'] = [None if v is None else v / 2 for v in e['size_m']]
                return super().compile_design(d)
        cfg, ops = self.resize_run(Opposite)
        self.assertTrue(ops, 'the move is still proposed')
        self.assertTrue(all('resize' not in v for v in ops.values()), ops)
        log = (cfg.out / 'loop.log').read_text(encoding='utf-8')
        self.assertIn('resize dropped', log)
        self.assertIn('direction', log)

    def test_a_resize_that_the_recompile_shows_unchanged_takes_no_candidate_slot(self):
        class Same(stubs.StubRenderer):                     # the built geometry never changes (a snapped LED wall, say)
            def render(self_, cand_dir, design, candidate_id, time_left_s=None):
                self_.first = getattr(self_, 'first', design)
                return super().render(cand_dir, design, candidate_id, time_left_s)

            def compile_design(self_, design):
                return super().compile_design(self_.first)

        def findings(shown):
            return [self.finding('reception', 'wrong_size', 'bigger')]
        cfg = self.cfg(max_rounds=2)
        parts = orchestrator.stub_parts(cfg)
        parts['roles'] = self.fixed_critic(findings)(parts['roles'].target, parts['roles'].camera, parts['roles'].size)
        parts['renderer'] = Same(self.picture, FIT)
        orchestrator.run(cfg, clock=Clock(0.001), **parts)
        self.assertEqual([p.name for p in (cfg.out / 'round_2' / 'candidates').iterdir()], ['r2_c0'], 'nothing but the resize was asked')
        self.assertIn('unchanged', (cfg.out / 'loop.log').read_text(encoding='utf-8'))

    def test_a_design_that_does_not_compile_is_not_proposed(self):
        class Broken(stubs.StubRenderer):
            def compile_design(self_, design):
                raise renderer.RenderError('design-compile failed: the ring leaves no opening')

        def findings(shown):
            return [self.finding('reception', 'misplaced', 'right')]
        cfg = self.cfg(max_rounds=2)
        parts = orchestrator.stub_parts(cfg)
        parts['roles'] = self.fixed_critic(findings)(parts['roles'].target, parts['roles'].camera, parts['roles'].size)
        parts['renderer'] = Broken(self.picture, FIT)
        orchestrator.run(cfg, clock=Clock(0.001), **parts)
        self.assertEqual([p.name for p in (cfg.out / 'round_2' / 'candidates').iterdir()], ['r2_c0'])
        self.assertIn('does not compile', (cfg.out / 'loop.log').read_text(encoding='utf-8'))

    def reader_roles(self, answer):
        class Roles(orchestrator.CheckedStubRoles):
            def read(self_, ctx):
                return answer(ctx)
        return Roles

    def test_design_init_none_lets_the_reader_make_the_first_design(self):
        template = reader_design(jread(TEMPLATE))
        target = stubs.make_target(template)
        roles = self.reader_roles(lambda ctx: reader_answer(template, ctx['picture_id']))(target, FIT['camera'])
        cfg = self.cfg(stub=False, design_init=None, max_rounds=1)
        summary = orchestrator.run(cfg, clock=Clock(0.001), roles=roles, renderer=stubs.StubRenderer(self.picture, FIT),
                                   scorer=FixedScorer(default=50.0))
        self.assertEqual((summary['rounds'], summary['stop_reason']), (1, 'rounds'))
        self.assertTrue((cfg.out / 'best' / 'design.json').is_file())

    def test_design_init_none_with_an_invalid_reader_stops_with_exit_code_3(self):
        cfg = self.cfg(stub=False, design_init=None, max_rounds=2)
        roles = self.reader_roles(lambda ctx: {'schema_version': 'reader.v1'})(None, None)
        summary = orchestrator.run(cfg, clock=Clock(0.001), roles=roles, renderer=stubs.StubRenderer(self.picture, FIT),
                                   scorer=FixedScorer())
        self.assertEqual((summary['rounds'], summary['stop_reason']), (0, 'reader_invalid'))
        self.assertEqual(jread(cfg.out / 'loop.json')['stop_reason'], 'reader_invalid')
        self.assertFalse((cfg.out / 'best').exists())
        argv = ['--picture', str(self.picture), '--fit', str(self.fit), '--truth', str(self.truth), '--design-init', 'none',
                '--out', str(self.tmp / 'real_none'), '--response-timeout', '0']
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(orchestrator.main(argv), 3)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(orchestrator.main(argv[:-4] + ['--out', str(self.tmp / 'stub_none'), '--stub']), 2)
        self.assertIn('--design-init', err.getvalue())

    def test_best_follows_the_winner_when_totals_tie_and_the_edge_distance_is_better(self):
        class Scorer(FixedScorer):
            def score(self, cand_dir, preview_dir, candidate_id, design):
                doc = super().score(cand_dir, preview_dir, candidate_id, design)
                doc['aux']['edge_distance_diag'] = {'r1_c0': 0.05, 'r2_c1': 0.01}.get(candidate_id, 0.03)
                return doc
        cfg, s = self.stub_run(scorer=Scorer(default=50.0), max_rounds=2)
        rj = jread(cfg.out / 'round_2' / 'round.json')
        self.assertEqual((rj['winner'], rj['decided_by'], rj['improved']), ('r2_c1', 'edge', True))
        self.assertEqual(jread(cfg.out / 'best' / 'best.json')['candidate_id'], 'r2_c1')
        self.assertEqual(jread(cfg.out / 'best' / 'design.json'), jread(cfg.out / 'round_2' / 'candidates' / 'r2_c1' / 'design.json'))
        self.assertEqual(s['best']['candidate_id'], 'r2_c1')

    def test_the_time_limit_also_stops_judge_requests(self):
        now = [0.0]

        class Scorer(FixedScorer):
            def score(self_, cand_dir, preview_dir, candidate_id, design):
                if candidate_id == 'r2_c1':
                    now[0] = 700.0                                # ten minutes are over once the second candidate is scored
                return super().score(cand_dir, preview_dir, candidate_id, design)
        cfg, s = self.stub_run(scorer=Scorer(default=50.0), clock=lambda: now[0], max_rounds=4, max_minutes=10.0)
        self.assertEqual(s['stop_reason'], 'minutes')
        self.assertFalse(list((cfg.out / 'round_2').glob('judge*')), 'no judge request after the time is up')
        self.assertEqual(jread(cfg.out / 'round_2' / 'round.json')['decided_by'], 'judge_unavailable')

    def test_waiting_for_a_role_is_capped_by_the_time_left(self):
        seen = []

        class Roles(orchestrator.CheckedStubRoles):
            def critic(self_, ctx):
                seen.append(ctx['remaining_s'])
                return super().critic(ctx)
        cfg = self.cfg(max_rounds=2, max_minutes=3.0)
        parts = orchestrator.stub_parts(cfg)
        parts['roles'] = Roles(parts['roles'].target, parts['roles'].camera)
        orchestrator.run(cfg, clock=Clock(0.001), **parts)
        self.assertTrue(seen and all(0 <= r <= 180.0 for r in seen))

    def test_best_folder_builds_from_the_real_compile_output(self):
        def runner(cmd, **kw):
            sub = cmd[2]
            if sub == 'design-compile':
                p = subprocess.run(cmd, capture_output=True, text=True)
                return renderer.Completed(p.returncode, p.stdout, p.stderr)
            if sub == 'preview':
                root = Path(cmd[cmd.index('--out-root') + 1]) / 'preview_001'
                root.mkdir(parents=True)
                Image.open(self.picture).convert('RGBA').save(root / 'preview_render.png')
                Path(root / 'spec.json').write_bytes(Path(cmd[cmd.index('--spec') + 1]).read_bytes())
            return renderer.Completed(0, '', '')
        template = jread(TEMPLATE)
        target = stubs.make_target(template)
        cfg = self.cfg(stub=False, max_rounds=2)
        summary = orchestrator.run(cfg, clock=Clock(0.001), roles=orchestrator.CheckedStubRoles(target, FIT['camera']),
                                   renderer=renderer.BoothRenderer(self.fit, self.picture, holder='t', runner=runner, wait_s=1),
                                   scorer=FixedScorer({'r1_c0': 40.0, 'r2_c1': 60.0}, default=30.0))
        self.assertEqual(summary['best']['candidate_id'], 'r2_c1')
        best = cfg.out / 'best'
        for name in ('spec.json', 'source.png', 'design.json', 'score.json', 'best.json'):
            self.assertTrue((best / name).is_file(), name)
        done = subprocess.run([sys.executable, str(ROOT / 'booth-image-to-model' / 'scripts' / 'validate_spec.py'),
                               '--spec', str(best / 'spec.json'), '--check-source'], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stdout[-400:])
        self.assertEqual(jread(best / 'best.json')['candidate_id'], 'r2_c1')
        self.assertEqual(jread(cfg.out / 'round_1' / 'round.json')['winner_spec'], 'round_1/candidates/r1_c0/compile/spec.json')
        # the critic's crops came from the real spec: elements are found through their group
        crops = {p.parent.name for p in (cfg.out / 'round_1' / 'critic_inputs').glob('*/original.png')}
        self.assertIn('el_hero_wall', crops)          # crop folders are el_<id> (P8b)

    def test_reader_footprint_comes_from_the_fit(self):
        d = jread(self.design_init)
        d['form']['footprint_m'] = [9.0, 9.0]
        self.design_init.write_text(json.dumps(d), encoding='utf-8')
        cfg, _ = self.stub_run(max_rounds=1)
        self.assertEqual(jread(cfg.out / 'round_1' / 'candidates' / 'r1_c0' / 'design.json')['form']['footprint_m'], [6.0, 4.0])

    def test_crop_boxes_come_from_projected_component_corners(self):
        spec = {'camera': FIT['camera'], 'source': {'width': W, 'height': H}, 'components': [
            {'id': 'hero_wall_backing', 'kind': 'box', 'location': [0, 1.9, 1.4], 'rotation_deg': [0, 0, 0], 'params': {'size': [4.0, 0.1, 2.0]}},
            {'id': 'hero_wall_slat_0', 'kind': 'box', 'location': [0, 1.8, 1.4], 'rotation_deg': [0, 0, 0], 'params': {'size': [0.1, 0.05, 2.0]}},
            {'id': 'unrelated_thing', 'kind': 'box', 'location': [0, 0, 0.5], 'rotation_deg': [0, 0, 0], 'params': {'size': [1, 1, 1]}},
            {'id': 'behind_camera_box', 'kind': 'box', 'location': [0, -50, 1], 'rotation_deg': [0, 0, 0], 'params': {'size': [1, 1, 1]}}]}
        boxes = orchestrator.element_pixel_boxes(['hero_wall', 'behind'], spec)
        self.assertEqual(set(boxes), {'hero_wall'})
        x0, y0, x1, y1 = boxes['hero_wall']
        self.assertTrue(0 <= x0 < x1 <= W and 0 <= y0 < y1 <= H)
        self.assertGreater(x1 - x0, 50)

    def test_crop_box_of_a_ring_covers_the_ring_not_its_hole(self):
        ring = {'id': 'ceiling_ring_0', 'group': 'ring', 'kind': 'rounded_ring', 'location': [0, 0, 3.0], 'rotation_deg': [0, 0, 0],
                'params': {'outer_size': [5.0, 3.0], 'inner_size': [4.4, 2.4], 'outer_radius': 0.3, 'inner_radius': 0.2, 'height': 0.1}}
        spec = {'camera': FIT['camera'], 'source': {'width': W, 'height': H}, 'components': [ring]}
        x0, y0, x1, y1 = orchestrator.element_pixel_boxes(['ring'], spec)['ring']
        self.assertGreater(x1 - x0, 150, 'the box spans the whole ring')
        for point in ([2.5, 0, 3.0], [-2.5, 0, 3.0], [0, 1.5, 3.0], [0, -1.5, 3.0]):
            u, v, _ = camera_math.project(point, FIT['camera'], W, H)
            self.assertTrue(x0 <= u <= x1 and y0 <= v <= y1, point)

    def test_crop_box_uses_the_full_rotation(self):
        comp = {'id': 'slab_0', 'group': 'slab', 'kind': 'box', 'location': [0.5, 0.5, 1.5], 'rotation_deg': [30.0, 20.0, 40.0],
                'params': {'size': [2.0, 0.4, 1.2]}}
        spec = {'camera': FIT['camera'], 'source': {'width': W, 'height': H}, 'components': [comp]}

        def expected(rotation):
            pts = [validate_spec.transform_point((sx * 1.0, sy * 0.2, sz * 0.6), comp['location'], rotation)
                   for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]
            uv = [camera_math.project(p, FIT['camera'], W, H)[:2] for p in pts]
            u0, u1 = math.floor(min(u for u, _ in uv)), math.ceil(max(u for u, _ in uv))
            v0, v1 = math.floor(min(v for _, v in uv)), math.ceil(max(v for _, v in uv))
            mx, my = orchestrator.crop_margin(u1 - u0, v1 - v0)
            return [max(0, u0 - mx), max(0, v0 - my), min(W, u1 + mx), min(H, v1 + my)]
        got = orchestrator.element_pixel_boxes(['slab'], spec)['slab']
        want, yaw_only = expected(comp['rotation_deg']), expected([0.0, 0.0, 40.0])
        for g, w in zip(got, want):
            self.assertAlmostEqual(g, w, delta=2)
        self.assertGreater(max(abs(g - y) for g, y in zip(got, yaw_only)), 4, 'a yaw-only box would be wrong here')

    def test_crop_box_of_a_text_is_a_rectangle_from_font_size_and_length(self):
        text = {'id': 'header_text', 'group': 'header', 'kind': 'text', 'location': [0.0, -2.0, 2.0], 'rotation_deg': [90.0, 0.0, 0.0],
                'params': {'body': 'DEMO COFFEE', 'size': 0.28, 'extrude': 0.005}}
        spec = {'camera': FIT['camera'], 'source': {'width': W, 'height': H}, 'components': [text]}
        x0, y0, x1, y1 = orchestrator.element_pixel_boxes(['header'], spec)['header']
        half_w = 0.6 * 0.28 * 11 / 2
        for dx, dz in ((-half_w, -0.14), (half_w, -0.14), (-half_w, 0.14), (half_w, 0.14)):
            u, v, _ = camera_math.project([dx, -2.0, 2.0 + dz], FIT['camera'], W, H)
            self.assertTrue(x0 <= u <= x1 and y0 <= v <= y1, (dx, dz))
        self.assertGreater(x1 - x0, 60, 'wider than one point plus padding')
        turned = dict(text, rotation_deg=[90.0, 0.0, 90.0])               # the text now runs along y
        t = orchestrator.element_pixel_boxes(['header'], dict(spec, components=[turned]))['header']
        self.assertNotEqual(t, [x0, y0, x1, y1], 'the rotation turns the rectangle')

    def test_component_belongs_to_its_group_before_its_id_prefix(self):
        comp = {'id': 'ring_extra', 'group': 'other_elem', 'kind': 'box', 'location': [0, 0, 1.0], 'rotation_deg': [0, 0, 0],
                'params': {'size': [1, 1, 1]}}
        spec = {'camera': FIT['camera'], 'source': {'width': W, 'height': H}, 'components': [comp]}
        self.assertEqual(set(orchestrator.element_pixel_boxes(['ring', 'other_elem'], spec)), {'other_elem'})
        del comp['group']
        self.assertEqual(set(orchestrator.element_pixel_boxes(['ring', 'other_elem'], spec)), {'ring'})

    def test_cli_help_and_missing_arguments(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as cm:
            orchestrator.main(['--help'])
        self.assertEqual(cm.exception.code, 0)
        for flag in ('--picture', '--fit', '--truth', '--design-init', '--out', '--map', '--max-rounds', '--max-minutes', '--stub'):
            self.assertIn(flag, out.getvalue())

    def test_main_runs_in_stub_mode(self):
        out = self.tmp / 'cli_out'
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = orchestrator.main(['--picture', str(self.picture), '--fit', str(self.fit), '--truth', str(self.truth),
                                      '--design-init', str(self.design_init), '--out', str(out), '--stub', '--max-rounds', '2'])
        self.assertEqual(code, 0)
        self.assertEqual(jread(out / 'loop.json')['rounds'], 2)

    def test_main_refuses_an_invalid_initial_design(self):
        bad = self.tmp / 'bad.json'
        bad.write_text(json.dumps({'schema_version': 'booth-design.v1'}), encoding='utf-8')
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = orchestrator.main(['--picture', str(self.picture), '--fit', str(self.fit), '--truth', str(self.truth),
                                      '--design-init', str(bad), '--out', str(self.tmp / 'o'), '--stub'])
        self.assertEqual(code, 2)
        self.assertIn('design', err.getvalue())

    # -- P8-fix2: skipped findings are written down, the judge outcome is named, the reader's frame is checked
    def test_skipped_geometry_findings_are_written_even_when_only_colour_candidates_remain(self):
        init = reader_design(jread(TEMPLATE))
        init['elements'].append({'id': 'lid', 'kind': 'canopy_frame'})
        self.design_init.write_text(json.dumps(init), encoding='utf-8')

        def findings(shown):
            self.assertTrue({'lid', 'reception', 'hero_wall'} <= shown, sorted(shown))
            return [dict(self.finding('lid', 'wrong_size', 'bigger'), bbox_px=None),
                    self.finding('reception', 'wrong_kind', None),
                    self.finding('hero_wall', 'wrong_colour', 'darker', severity='low')]
        cfg = self.cfg(max_rounds=2)
        parts = orchestrator.stub_parts(cfg)
        parts['roles'] = self.fixed_critic(findings)(parts['roles'].target, parts['roles'].camera, parts['roles'].size)
        orchestrator.run(cfg, clock=Clock(0.001), **parts)
        cands = sorted(p.name for p in (cfg.out / 'round_2' / 'candidates').iterdir())
        self.assertEqual(cands, ['r2_c0', 'r2_c1'], 'only the colour candidate was made')
        self.assertEqual({e['op'] for e in jread(cfg.out / 'round_2' / 'candidates' / 'r2_c1' / 'fixer.json')['edits']}, {'recolour'})
        skipped = jread(cfg.out / 'round_1' / 'fixer_skipped.json')
        self.assertEqual(sorted(s['finding_id'] for s in skipped), ['f1', 'f2'], 'both geometry findings, not the colour one that became an edit')
        by_id = {s['finding_id']: s for s in skipped}
        for s in skipped:
            self.assertEqual(set(s), {'finding_id', 'element_id', 'reason'})
            self.assertTrue(s['reason'])
        self.assertEqual((by_id['f1']['element_id'], by_id['f2']['element_id']), ('lid', 'reception'))
        self.assertIn('wrong_kind', by_id['f2']['reason'])
        log = (cfg.out / 'loop.log').read_text(encoding='utf-8')
        for s in skipped:
            lines = [ln for ln in log.splitlines() if f"skipped (finding {s['finding_id']}): " in ln]
            self.assertEqual(len(lines), 1, f"one line for {s['finding_id']}")
            self.assertTrue(lines[0].endswith(s['reason']))
        self.assertFalse((cfg.out / 'round_2' / 'fixer_skipped.json').exists(), 'no critic after the last round')

    def test_a_round_without_skipped_findings_writes_an_empty_list(self):
        cfg, _ = self.stub_run(max_rounds=2)
        self.assertEqual(jread(cfg.out / 'round_1' / 'fixer_skipped.json'), [])

    def judge_outcome_round(self, passes):
        """A tied round 2 whose judge answers pass 1 and pass 2 as `passes` says: 'same', 'target' (the edited candidate) or 'A'."""
        class Roles(orchestrator.CheckedStubRoles):
            def judge(self_, ctx):
                doc = super().judge(ctx)
                answer = passes[ctx['pass']]
                if answer == 'target':
                    doc['better'] = 'A' if ctx['key']['A'] == ctx['order'][-1] else 'B'
                else:
                    doc['better'] = answer
                return doc
        cfg = self.cfg(max_rounds=2)
        parts = orchestrator.stub_parts(cfg)
        parts['roles'] = Roles(parts['roles'].target, parts['roles'].camera)
        parts['scorer'] = FixedScorer(default=50.0)
        orchestrator.run(cfg, clock=Clock(0.001), **parts)
        return jread(cfg.out / 'round_2' / 'round.json'), jread(cfg.out / 'round_1' / 'round.json')

    def test_judge_outcome_names_agree_both_same_split_and_conflict(self):
        for passes, outcome, winner, decided in (
                ({1: 'target', 2: 'target'}, 'agree', 'r2_c1', 'judge'),
                ({1: 'same', 2: 'same'}, 'both_same', 'r2_c0', 'judge_unavailable'),
                ({1: 'target', 2: 'same'}, 'split', 'r2_c0', 'judge_unavailable'),
                ({1: 'same', 2: 'target'}, 'split', 'r2_c0', 'judge_unavailable'),
                ({1: 'A', 2: 'A'}, 'conflict', 'r2_c0', 'judge_unavailable')):      # A is another candidate in the second pass
            with self.subTest(passes=passes):
                row, first = self.judge_outcome_round(passes)
                self.assertEqual(row['judge_outcome'], outcome)
                self.assertEqual((row['winner'], row['decided_by']), (winner, decided), 'the rule that picks the winner is the same')
                self.assertIsNone(first['judge_outcome'], 'round 1 had one candidate: no judge')
        cfg, _ = self.stub_run(max_rounds=2)
        self.assertEqual(jread(cfg.out / 'round_2' / 'round.json')['decided_by'], 'edge')
        self.assertIsNone(jread(cfg.out / 'round_2' / 'round.json')['judge_outcome'], 'decided by the edge distance: no judge')

    def test_a_pass_with_no_valid_answer_counts_as_cannot_tell(self):
        class Roles(orchestrator.CheckedStubRoles):
            def judge(self_, ctx):
                return None if ctx['pass'] == 2 else super().judge(ctx)
        cfg = self.cfg(max_rounds=2)
        parts = orchestrator.stub_parts(cfg)
        parts['roles'] = Roles(parts['roles'].target, parts['roles'].camera)
        parts['scorer'] = FixedScorer(default=50.0)
        orchestrator.run(cfg, clock=Clock(0.001), **parts)
        self.assertEqual(jread(cfg.out / 'round_2' / 'round.json')['judge_outcome'], 'split')

    def real_compile(self, design):
        with tempfile.TemporaryDirectory() as tmp:
            spec, report = design_compile.compile_design(json.loads(json.dumps(design)), tmp)
        return spec

    def test_the_reader_frame_check_runs_before_the_first_compile_and_is_logged(self):
        # a 6 x 6 m stand, so that a quarter turn is exact; the stand-in compiler cannot verify a turn, so this one compiles for real
        fit = dict(FIT, dims_m={'W': 6.0, 'D': 6.0})
        fit_path = self.tmp / 'fit66.json'
        fit_path.write_text(json.dumps(fit), encoding='utf-8')
        truth = reader_design(jread(TEMPLATE))
        truth['form']['footprint_m'] = [6.0, 6.0]
        read = edits.rotate_design(truth, -1)               # the reader turned the whole stand a quarter off
        view = edits.SpecView(self.real_compile(truth), [e['id'] for e in truth['elements']])
        view.camera, view.width, view.height = FIT['camera'], W, H
        evidence = {}
        for el in read['elements']:
            raw = view.box_px_raw(el['id'], edits.main_body(el))
            box = None if raw is None else [max(0.0, raw[0]), max(0.0, raw[1]), min(float(W), raw[2]), min(float(H), raw[3])]
            evidence[el['id']] = {'bbox_px': box if box and box[2] > box[0] and box[3] > box[1] else [0, 0, W, H], 'confidence': 'high'}
        answer = {'schema_version': 'reader.v1', 'picture_id': 'pic_T', 'design': read, 'evidence': evidence, 'unexpressible': []}
        roles = self.reader_roles(lambda ctx: json.loads(json.dumps(answer)))(truth, FIT['camera'], (W, H))
        events = []
        real_compile = self.real_compile

        class Spy(stubs.StubRenderer):
            def compile_design(self_, design):
                events.append('compile')
                return real_compile(design)

            def render(self_, cand_dir, design, candidate_id, time_left_s=None):
                events.append('render')
                return super().render(cand_dir, design, candidate_id, time_left_s)
        cfg = self.cfg(stub=False, design_init=None, max_rounds=1, fit=fit_path)
        orchestrator.run(cfg, clock=Clock(0.001), roles=roles, renderer=Spy(self.picture, fit), scorer=FixedScorer(default=50.0))
        self.assertEqual(events[:5], ['compile'] * 4 + ['render'], 'four turns compiled, then the first render')
        rd = cfg.out / 'round_1'
        check = jread(rd / 'frame_check.json')
        self.assertEqual((check['decision'], check['quarter_turns']), ('turn', 1))
        self.assertEqual([r['degrees'] for r in check['rotations']], [0, 90, 180, 270])
        self.assertEqual(check['rotations'][1]['inexact'], [], 'the turn gives the geometry back')
        self.assertTrue(check['reason'])
        log = (cfg.out / 'loop.log').read_text(encoding='utf-8')
        self.assertIn('frame_check', log)
        self.assertLess(log.index('frame_check'), log.index('r1_c0'), 'logged before the first candidate')
        # the first candidate is the turned-back design, with the footprint of the fit: what is built is what the truth builds
        first = jread(rd / 'candidates' / 'r1_c0' / 'design.json')
        self.assertEqual(first['form']['footprint_m'], [6.0, 6.0])
        self.assertEqual(first['form']['open_sides'], truth['form']['open_sides'])
        self.assertEqual(first['layout']['entrance_side'], truth['layout']['entrance_side'])
        ids = [e['id'] for e in truth['elements']]
        got, want = edits.SpecView(self.real_compile(first), ids), edits.SpecView(self.real_compile(truth), ids)
        for el in truth['elements']:
            if el['kind'] in ('header_band', 'spotlights'):
                continue
            a, b = edits.main_body_box(got, el), edits.main_body_box(want, el)
            self.assertIsNotNone(a, el['id'])
            for x, y in zip(a[0][:2] + a[1][:2], b[0][:2] + b[1][:2]):
                self.assertAlmostEqual(x, y, delta=0.02, msg=el['id'])
        # the reader's own answer is kept as the reader gave it
        self.assertEqual(jread(rd / 'reader.response.json')['design'], read)
        # the request to the reader is the same as before, byte for byte
        expected = {'role': 'reader', 'picture_id': 'pic_T', 'picture': 'reader_inputs/picture.png',
                    'box_fit': {'dims_m': {'W': 6.0, 'D': 6.0}, 'image': {'width': W, 'height': H}},
                    'catalog': 'reader_inputs/catalog/catalog.json', 'response': 'reader.response.json'}
        self.assertEqual((rd / 'reader.request.json').read_bytes(), (json.dumps(expected, indent=2, ensure_ascii=False) + '\n').encode('utf-8'))

    def test_a_reader_design_that_is_not_off_is_kept_and_the_check_is_written(self):
        truth = reader_design(jread(TEMPLATE))
        probe = stubs.StubRoles(truth, FIT['camera'], (W, H))
        evidence = {e['id']: {'bbox_px': probe.target_box(e['id']) or [0, 0, W, H], 'confidence': 'high'} for e in truth['elements']}
        answer = {'schema_version': 'reader.v1', 'picture_id': 'pic_T', 'design': truth, 'evidence': evidence, 'unexpressible': []}
        roles = self.reader_roles(lambda ctx: json.loads(json.dumps(answer)))(truth, FIT['camera'], (W, H))
        cfg = self.cfg(stub=False, design_init=None, max_rounds=1)
        orchestrator.run(cfg, clock=Clock(0.001), roles=roles, renderer=stubs.StubRenderer(self.picture, FIT), scorer=FixedScorer(default=50.0))
        check = jread(cfg.out / 'round_1' / 'frame_check.json')
        self.assertEqual((check['decision'], check['quarter_turns']), ('keep', 0))
        self.assertEqual(jread(cfg.out / 'round_1' / 'candidates' / 'r1_c0' / 'design.json'), truth)

    def test_a_frame_check_that_raises_still_writes_the_same_fields(self):
        cfg = self.cfg(max_rounds=1)
        parts = orchestrator.stub_parts(cfg)

        def boom(*args, **kw):
            raise RuntimeError('the check broke')
        with unittest.mock.patch.object(edits, 'frame_check', boom):
            orchestrator.run(cfg, clock=Clock(0.001), **parts)
        check = jread(cfg.out / 'round_1' / 'frame_check.json')
        self.assertEqual(check['decision'], 'keep')
        self.assertIn('frame unverified', check['reason'])
        for key in ('decision', 'quarter_turns', 'reason', 'elements', 'evidence_elements', 'gates', 'rotations'):
            self.assertIn(key, check)
        self.assertEqual(check['elements'], len(jread(cfg.out / 'round_1' / 'candidates' / 'r1_c0' / 'design.json')['elements']))
        log = (cfg.out / 'loop.log').read_text(encoding='utf-8')
        self.assertIn('the check broke', log)

    def test_the_frame_check_never_stops_the_loop(self):
        class NoCompile(stubs.StubRenderer):
            def compile_design(self_, design):
                raise renderer.RenderError('design-compile failed')
        cfg = self.cfg(max_rounds=2)
        parts = orchestrator.stub_parts(cfg)
        parts['renderer'] = NoCompile(self.picture, FIT)
        s = orchestrator.run(cfg, clock=Clock(0.001), **parts)
        self.assertEqual(s['rounds'], 2)
        check = jread(cfg.out / 'round_1' / 'frame_check.json')
        self.assertEqual(check['decision'], 'keep')
        self.assertFalse(any(r['compiled'] for r in check['rotations']))
        # the initial design stands in for a reader that gave nothing: there is no evidence to check it with
        cfg = self.cfg(max_rounds=1)
        parts = orchestrator.stub_parts(cfg)
        parts['roles'] = orchestrator.FileRoles(timeout_s=0.0, sleep=lambda s: None)
        orchestrator.run(cfg, clock=Clock(0.001), **parts)
        check = jread(cfg.out / 'round_1' / 'frame_check.json')
        self.assertEqual(check['decision'], 'keep')
        self.assertIn('frame unverified', check['reason'])


    # -- P8-fix2 polish: one line per log entry, a skipped list for every round with a critic answer
    def test_a_compile_failure_with_a_multi_line_message_is_one_line_in_the_log(self):
        message = 'design-compile failed (exit 2): {\n  "status": "design_invalid",\n  "errors": [\n    {"code": "PARAM"}\n  ]\n}'

        class Broken(stubs.StubRenderer):
            def compile_design(self_, design):
                raise renderer.RenderError(message)

        class Late(stubs.StubRenderer):                     # compiles until the first render, then fails (the edits' compiles)
            def compile_design(self_, design):
                if getattr(self_, 'rendered', False):
                    raise renderer.RenderError(message)
                return super().compile_design(design)

            def render(self_, cand_dir, design, candidate_id, time_left_s=None):
                self_.rendered = True
                return super().render(cand_dir, design, candidate_id, time_left_s)

        def findings(shown):
            return [self.finding('reception', 'misplaced', 'right')]
        for name, renderer_class in (('frame check', Broken), ('edits', Late)):
            with self.subTest(case=name):
                cfg = self.cfg(max_rounds=2)
                parts = orchestrator.stub_parts(cfg)
                if name == 'edits':           # (the stand-in reader of the frame check case gives evidence boxes, this critic's does not)
                    parts['roles'] = self.fixed_critic(findings)(parts['roles'].target, parts['roles'].camera, parts['roles'].size)
                parts['renderer'] = renderer_class(self.picture, FIT)
                orchestrator.run(cfg, clock=Clock(0.001), **parts)
                lines =(cfg.out / 'loop.log').read_text(encoding='utf-8').splitlines()
                self.assertTrue(lines)
                for ln in lines:
                    self.assertRegex(ln, r'^\[\+ *[0-9.]+s\] ', 'every physical line is a log entry')
                text = '\n'.join(lines)
                if name == 'frame check':
                    self.assertEqual(len([ln for ln in lines if 'does not compile' in ln and 'frame_check: turn' in ln]), 4)
                    self.assertIn('\n', jread(cfg.out / 'round_1' / 'frame_check.json')['rotations'][0]['error'], 'the json keeps the full text')
                else:
                    self.assertIn('does not compile', text)
                    skipped = jread(cfg.out / 'round_1' / 'fixer_skipped.json')
                    self.assertTrue(skipped)
                    self.assertIn('\n', skipped[0]['reason'], 'the json keeps the full text')
                    self.assertIn(' '.join(skipped[0]['reason'].split()), text)

    def critic_round(self, make_doc, **kw):
        class Roles(stubs.StubRoles):
            def critic(self_, ctx):
                return make_doc(ctx)
        cfg = self.cfg(max_rounds=3, plateau=False, **kw)
        parts = orchestrator.stub_parts(cfg)
        parts['roles'] = Roles(parts['roles'].target, parts['roles'].camera, parts['roles'].size)
        orchestrator.run(cfg, clock=Clock(0.001), **parts)
        return cfg

    def test_fixer_skipped_is_written_for_every_round_with_a_critic_answer(self):
        def doc(verdict, findings):
            def make(ctx):
                return {'schema_version': 'critic.v1', 'round': ctx['round'], 'candidate_id': ctx['candidate_id'],
                        'findings': [dict(f, id=f'f{k}') for k, f in enumerate(findings, start=1)], 'verdict': verdict}
            return make
        move = self.finding('reception', 'misplaced', 'right')
        size = self.finding('reception', 'wrong_size', 'bigger')
        colour = self.finding('hero_wall', 'wrong_colour', 'darker', severity='low')
        # give_up with findings: nobody acts on them, and the file says so
        cfg = self.critic_round(doc('give_up', [move, size, colour]))
        skipped = jread(cfg.out / 'round_1' / 'fixer_skipped.json')
        self.assertEqual(sorted(s['finding_id'] for s in skipped), ['f1', 'f2', 'f3'])
        for s in skipped:
            self.assertEqual(set(s), {'finding_id', 'element_id', 'reason'})
            self.assertIn('gave up', s['reason'])
        self.assertEqual({s['element_id'] for s in skipped}, {'reception', 'hero_wall'})
        log = (cfg.out / 'loop.log').read_text(encoding='utf-8')
        self.assertTrue(any('skipped (finding f1): ' in ln for ln in log.splitlines()))
        self.assertTrue((cfg.out / 'round_2' / 'fixer_skipped.json').is_file(), 'every round with a critic answer')
        self.assertFalse((cfg.out / 'round_3' / 'fixer_skipped.json').exists(), 'the last round has no critic and no file')
        self.assertEqual([p.name for p in (cfg.out / 'round_2' / 'candidates').iterdir()], ['r2_c0'], 'no edits after give_up')
        # accept: an empty list
        cfg = self.critic_round(doc('accept', []))
        self.assertEqual(jread(cfg.out / 'round_1' / 'fixer_skipped.json'), [])
        self.assertEqual(jread(cfg.out / 'round_2' / 'fixer_skipped.json'), [])
        # fix without findings, and no response at all, and an answer that is not valid
        cfg = self.critic_round(doc('fix', []))
        self.assertIn(jread(cfg.out / 'round_1' / 'round.json')['responses']['critic'], ('ok', 'invalid'))
        self.assertEqual(jread(cfg.out / 'round_1' / 'fixer_skipped.json'), [])
        cfg = self.critic_round(lambda ctx: None)
        self.assertEqual(jread(cfg.out / 'round_1' / 'round.json')['responses']['critic'], 'no_response')
        self.assertEqual(jread(cfg.out / 'round_1' / 'fixer_skipped.json'), [])
        cfg = self.critic_round(lambda ctx: {'schema_version': 'critic.v1'})
        self.assertEqual(jread(cfg.out / 'round_1' / 'round.json')['responses']['critic'], 'invalid')
        self.assertEqual(jread(cfg.out / 'round_1' / 'fixer_skipped.json'), [])


class FileRolesCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_waits_for_the_response_file_and_gives_up_after_the_timeout(self):
        path = self.tmp / 'critic.response.json'
        sleeps = []
        clock = Clock(1.0)
        roles = orchestrator.FileRoles(timeout_s=5.0, sleep=sleeps.append, clock=clock)
        self.assertIsNone(roles.wait_json(path))
        self.assertTrue(sleeps, 'it polled before giving up')
        path.write_text('{"a": 1}', encoding='utf-8')
        self.assertEqual(roles.wait_json(path), {'a': 1})
        path.write_text('{oops', encoding='utf-8')
        bad = orchestrator.FileRoles(timeout_s=0.0, sleep=lambda s: None).wait_json(path)
        self.assertIsInstance(bad, orchestrator.Unreadable)


    def test_a_response_older_than_its_request_does_not_count(self):
        response, request = self.tmp / 'critic.response.json', self.tmp / 'critic.request.json'
        response.write_text('{"a": 1}', encoding='utf-8')
        request.write_text('{}', encoding='utf-8')
        old = request.stat().st_mtime - 100
        os.utime(response, (old, old))
        roles = orchestrator.FileRoles(timeout_s=0.0, sleep=lambda s: None)
        self.assertIsNone(roles.wait_json(response, newer_than=request))
        now = request.stat().st_mtime + 1
        os.utime(response, (now, now))
        self.assertEqual(roles.wait_json(response, newer_than=request), {'a': 1})

    def test_unreadable_responses_never_raise(self):
        roles = orchestrator.FileRoles(timeout_s=0.0, sleep=lambda s: None)
        deep = self.tmp / 'deep.json'
        deep.write_bytes(b'[' * 100000)                                   # json.loads raises RecursionError on this
        self.assertIsInstance(roles.wait_json(deep), orchestrator.Unreadable)
        binary = self.tmp / 'bin.json'
        binary.write_bytes(b'\xff\xfe\x00garbage')
        self.assertIsInstance(roles.wait_json(binary), orchestrator.Unreadable)

    def test_a_response_with_a_byte_order_mark_is_read(self):
        path = self.tmp / 'bom.json'
        path.write_bytes(b'\xef\xbb\xbf{"a": 1}')                      # what PowerShell 5.1 writes
        self.assertEqual(orchestrator.FileRoles(timeout_s=0.0, sleep=lambda s: None).wait_json(path), {'a': 1})

    def test_the_wait_never_exceeds_the_time_left(self):
        sleeps = []
        roles = orchestrator.FileRoles(timeout_s=1000.0, sleep=sleeps.append, clock=Clock(1.0))
        self.assertIsNone(roles.wait_json(self.tmp / 'none.json', remaining_s=0.0))
        self.assertEqual(sleeps, [])
        self.assertIsNone(roles.wait_json(self.tmp / 'none.json', remaining_s=4.0))
        self.assertLessEqual(len(sleeps), 5)


class RendererCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_compile_design_compiles_in_a_scratch_folder_without_the_token(self):
        calls = []

        def runner(cmd, **kw):
            calls.append(cmd)
            out = Path(cmd[cmd.index('--out') + 1])
            if 'bad' not in Path(cmd[cmd.index('--design') + 1]).read_text(encoding='utf-8'):
                out.mkdir(parents=True)
                (out / 'spec.json').write_text('{"components": []}', encoding='utf-8')
                return renderer.Completed(0, '', '')
            return renderer.Completed(2, '', 'the ring leaves no opening')
        r = renderer.BoothRenderer(self.tmp / 'f.json', self.tmp / 'p.png', holder='t', runner=runner)
        self.assertEqual(r.compile_design({'a': 1}), {'components': []})
        self.assertEqual([c[2] for c in calls], ['design-compile'], 'no token, no preview')
        self.assertFalse(Path(calls[0][calls[0].index('--out') + 1]).exists(), 'the scratch folder is gone')
        with self.assertRaises(renderer.RenderError):
            r.compile_design({'bad': 1})

    def test_compile_needs_fit_and_image_together(self):
        with self.assertRaises(ValueError):
            renderer.compile_cmd(self.tmp / 'd.json', self.tmp / 'o', fit=self.tmp / 'f.json', image=None)
        with self.assertRaises(ValueError):
            renderer.compile_cmd(self.tmp / 'd.json', self.tmp / 'o', fit=None, image=self.tmp / 'p.png')
        cmd = renderer.compile_cmd(self.tmp / 'd.json', self.tmp / 'o', fit=self.tmp / 'f.json', image=self.tmp / 'p.png')
        self.assertEqual(cmd[1:3], [str(renderer.BOOTHCTL), 'design-compile'])
        for flag in ('--design', '--out', '--fit', '--image'):
            self.assertIn(flag, cmd)
        self.assertEqual(cmd[cmd.index('--fit') + 1], str(self.tmp / 'f.json'))
        self.assertEqual(cmd[cmd.index('--image') + 1], str(self.tmp / 'p.png'))

    def test_preview_and_token_command_lines(self):
        cmd = renderer.preview_cmd(self.tmp / 'spec.json', self.tmp / 'root', blender='B.exe')
        self.assertEqual(cmd[2], 'preview')
        self.assertEqual(cmd[cmd.index('--out-root') + 1], str(self.tmp / 'root'))
        self.assertEqual(cmd[cmd.index('--blender') + 1], 'B.exe')
        self.assertNotIn('--blender', renderer.preview_cmd(self.tmp / 'spec.json', self.tmp / 'root'))
        acq = renderer.token_cmd('acquire', holder='loop-x', purpose='preview r1_c0', wait=600)
        self.assertEqual(acq[1], str(renderer.TOKEN_SCRIPT))
        self.assertEqual(acq[2:], ['acquire', '--wait', '600', '--holder', 'loop-x', '--purpose', 'preview r1_c0'])
        self.assertEqual(renderer.token_cmd('release', holder='loop-x')[2:], ['release', '--holder', 'loop-x'])

    def test_render_takes_the_token_around_the_preview_only_and_always_releases(self):
        calls = []

        def runner(cmd, **kw):
            calls.append(cmd)
            sub = cmd[2]
            out = self.tmp / 'cand'
            if sub == 'design-compile':
                (out / 'compile').mkdir(parents=True, exist_ok=True)
                (out / 'compile' / 'spec.json').write_text('{}', encoding='utf-8')
            if sub == 'preview':
                p = out / 'previews' / 'preview_001'
                p.mkdir(parents=True)
                (p / 'preview_render.png').write_bytes(b'x')
                (p / 'spec.json').write_text('{}', encoding='utf-8')
            return renderer.Completed(0, '', '')

        r = renderer.BoothRenderer(fit=self.tmp / 'f.json', image=self.tmp / 'p.png', holder='loop-x', runner=runner, wait_s=30)
        cand = self.tmp / 'cand'
        cand.mkdir()
        (cand / 'design.json').write_text('{}', encoding='utf-8')
        preview = r.render(cand, {}, 'r1_c0')
        self.assertEqual(preview, cand / 'previews' / 'preview_001')
        subs = [c[2] for c in calls]
        self.assertEqual(subs, ['design-compile', 'acquire', 'preview', 'release'])

        def failing(cmd, **kw):
            calls.append(cmd)
            return renderer.Completed(2 if cmd[2] == 'preview' else 0, '', 'boom')

        calls.clear()
        r = renderer.BoothRenderer(fit=self.tmp / 'f.json', image=self.tmp / 'p.png', holder='loop-x', runner=failing, wait_s=30)
        (cand / 'compile').mkdir(exist_ok=True)
        (cand / 'compile' / 'spec.json').write_text('{}', encoding='utf-8')
        with self.assertRaises(renderer.RenderError):
            r.render(cand, {}, 'r1_c0')
        self.assertEqual(calls[-1][2], 'release', 'the token is released even when the preview fails')

        calls.clear()
        with self.assertRaises(renderer.RenderError):
            r.render(cand, {}, 'r1_c1', time_left_s=12.0)
        acquire = next(c for c in calls if c[2] == 'acquire')
        self.assertEqual(acquire[acquire.index('--wait') + 1], '12', 'the token wait is capped by the time left')


class ScoreCommandCase(unittest.TestCase):
    def test_score_command_line(self):
        tmp = Path('x')
        cmd = renderer.score_cmd(tmp / 'prev', tmp / 'pic.png', tmp / 'truth', tmp / 'c' / 'score.json', 'r1_c0', map_path=tmp / 'm.json')
        self.assertEqual(cmd[1], str(renderer.SCORE_SCRIPT))
        self.assertEqual(cmd[cmd.index('--candidate-id') + 1], 'r1_c0')
        self.assertEqual(cmd[cmd.index('--map') + 1], str(tmp / 'm.json'))
        self.assertNotIn('--map', renderer.score_cmd(tmp / 'prev', tmp / 'pic.png', tmp / 'truth', tmp / 'c' / 'score.json', 'r1_c0'))
        self.assertNotIn('--panel', cmd)


class StubsCase(unittest.TestCase):
    def test_target_differs_and_distance_is_zero_for_equal_designs(self):
        d = jread(TEMPLATE)
        t = stubs.make_target(d)
        self.assertEqual(edits.validate_design(t), [])
        self.assertEqual(stubs.design_distance(d, d), 0.0)
        self.assertGreater(stubs.design_distance(d, t), 1.0)

    def test_stub_critic_findings_follow_the_difference(self):
        d = jread(TEMPLATE)
        t = stubs.make_target(d)
        doc = stubs.StubRoles(t).critic({'round': 1, 'candidate_id': 'r1_c0', 'design': d})
        self.assertEqual(doc['schema_version'], 'critic.v1')
        self.assertEqual(doc['verdict'], 'fix')
        self.assertTrue(doc['findings'])
        same = stubs.StubRoles(d).critic({'round': 1, 'candidate_id': 'r1_c0', 'design': d})
        self.assertEqual((same['verdict'], same['findings']), ('accept', []))


if __name__ == '__main__':
    unittest.main()
