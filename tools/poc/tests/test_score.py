"""P1 tests for score.py: the score.json contract, the panel gate, input checks and the reader.v1 schema."""
from __future__ import annotations
import contextlib
import copy
import io
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parents[0]))        # tools/poc, so that `score` is the package
sys.path.insert(0, str(HERE / 'fixtures'))

import jsonschema  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402
import synthetic as fx  # noqa: E402
import score  # noqa: E402
from score import score as cli  # noqa: E402

TOP = {'schema_version', 'picture_id', 'candidate_id', 'spec_sha256', 'levels', 'total', 'aux', 'status', 'reason'}
AUX = {'edge_distance_diag', 'edge_distance_px', 'holdout_median_diag', 'colour_de_mean', 'booth_pixel_fraction', 'second_view_ok'}


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main([str(a) for a in argv])
    return code, out.getvalue(), err.getvalue()


class ScoreCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.truth = fx.write_truth(self.tmp / 'truth')
        self.photo = fx.write_photo(self.tmp / 'photo.png')
        self.run_dir = fx.write_preview(self.tmp / 'preview_001')
        self.fit = fx.write_fit(self.tmp / 'fit.json')

    def score(self, panel=None, run=None, name='score.json', extra_args=(), candidate='cand_1', fit=True):
        out = self.tmp / name
        args = ['--run', run or self.run_dir, '--photo', self.photo, '--truth', self.truth, '--out', out, *extra_args]
        if fit:
            args += ['--fit', fit if fit is not True else self.fit]
        if candidate:
            args += ['--candidate-id', candidate]
        if panel:
            args += ['--panel', panel]
        code, _, err = run_cli(*args)
        return code, out, err


class ScoreJsonTests(ScoreCase):
    def check_contract(self, d):
        self.assertEqual(set(d), TOP)
        self.assertEqual(d['schema_version'], 'score.v1')
        self.assertEqual(set(d['aux']), AUX)
        self.assertIn(d['status'], ('measured', 'blocked'))
        self.assertTrue(isinstance(d['reason'], str) or d['reason'] is None)
        self.assertEqual(set(d['levels']), {'L1', 'L2', 'L3', 'L4'})
        self.assertEqual(set(d['levels']['L1']), {'panel'})
        self.assertEqual(set(d['levels']['L2']), {'code', 'panel'})
        self.assertEqual(set(d['levels']['L3']), {'panel'})
        self.assertEqual(set(d['levels']['L4']), {'code', 'panel'})
        self.assertTrue(0 <= d['total'] <= 100)
        self.assertRegex(d['spec_sha256'], '^[0-9a-f]{64}$')

    def test_score_json_matches_contract_and_blocks_without_panel(self):
        code, out, err = self.score()
        self.assertEqual(code, 0, err)
        d = json.loads(out.read_text(encoding='utf-8'))
        self.check_contract(d)
        self.assertEqual(d['status'], 'blocked')
        self.assertEqual(d['reason'], 'panel_missing')
        self.assertEqual(d['picture_id'], 'pic_T')
        self.assertEqual(d['candidate_id'], 'cand_1')
        # the code part is still written
        self.assertIsNotNone(d['aux']['edge_distance_diag'])
        self.assertIsNotNone(d['aux']['colour_de_mean'])
        self.assertIsNotNone(d['levels']['L4']['code'])
        self.assertIsNotNone(d['levels']['L2']['code'])
        self.assertIsNone(d['levels']['L1']['panel'])
        self.assertIsNone(d['levels']['L3']['panel'])
        self.assertIsNone(d['aux']['second_view_ok'])
        self.assertTrue((out.parent / 'sheet.png').is_file())
        self.assertTrue((out.parent / 'score_detail.json').is_file())

    def test_panel_makes_a_measured_total(self):
        panel = fx.write_panel(self.tmp / 'panel')
        code, out, err = self.score(panel=panel)
        self.assertEqual(code, 0, err)
        d = json.loads(out.read_text(encoding='utf-8'))
        self.check_contract(d)
        self.assertEqual(d['status'], 'measured')
        self.assertIsNone(d['reason'])
        lv = d['levels']
        self.assertAlmostEqual(lv['L1']['panel'], 30.0)                  # all 10 inventory items present
        self.assertAlmostEqual(lv['L2']['panel'], 20 * (9 * 2 + 1) / 20)  # nine ok and one off over ten inventory items
        self.assertAlmostEqual(lv['L3']['panel'], 12.0)
        self.assertAlmostEqual(lv['L4']['panel'], 6.0)
        parts = [lv['L1']['panel'], lv['L2']['code'], lv['L2']['panel'], lv['L3']['panel'], lv['L4']['code'], lv['L4']['panel']]
        self.assertAlmostEqual(d['total'], sum(parts), places=1)

    def test_a_part_that_is_both_inventory_id_and_component_id_does_not_clash(self):
        # 'reception' is an inventory id and also the group of a component; L1 and L2 votes use different key prefixes
        panel = fx.write_panel(self.tmp / 'panel')
        out = self.score(panel=panel)[1]
        self.assertAlmostEqual(json.loads(out.read_text(encoding='utf-8'))['levels']['L1']['panel'], 30.0)

    def test_judges_who_contradict_themselves_do_not_count(self):
        panel = fx.write_panel(self.tmp / 'panel')
        # j1 changes its mind about the reception on pass 2 and about nothing else
        bad = fx.judge('j1', 2, l2={'reception': 'wrong'})
        (panel / 'j1_2.json').write_text(json.dumps(bad), encoding='utf-8')
        code, out, err = self.score(panel=panel)
        self.assertEqual(code, 0, err)
        detail = json.loads((out.parent / 'score_detail.json').read_text(encoding='utf-8'))
        self.assertEqual(detail['panel']['l2']['reception'], 'ok')   # j2 and j3 still agree
        self.assertIn('L2:reception', detail['panel']['unstable']['j1'])

    def test_missing_inventory_item_lowers_l1(self):
        panel = fx.write_panel(self.tmp / 'panel', inv_present={f'part_{i}' for i in range(2, 10)})   # reception and screen_a missing
        code, out, _ = self.score(panel=panel)
        d = json.loads(out.read_text(encoding='utf-8'))
        self.assertAlmostEqual(d['levels']['L1']['panel'], 30 * 8 / 10)
        self.assertAlmostEqual(d['levels']['L2']['panel'], 20 * (8 * 2) / 20)    # missing items score 0 on L2

    def test_size_mismatch_is_an_error_not_a_rescale(self):
        Image.new('RGB', (160, 100), (10, 10, 10)).save(self.photo)
        code, out, err = self.score()
        self.assertEqual(code, 2)
        self.assertIn('IMAGE_SHAPE_MISMATCH', err)
        self.assertFalse(out.exists())

    def test_render_without_alpha_is_an_error(self):
        Image.fromarray(fx.render()[..., :3], 'RGB').save(self.run_dir / 'preview_render.png')
        code, out, err = self.score()
        self.assertEqual(code, 2)
        self.assertIn('BAD_RENDER', err)
        self.assertFalse(out.exists())

    def test_transparent_render_is_blocked(self):
        run = fx.write_preview(self.tmp / 'preview_002', transparent=True)
        code, out, err = self.score(panel=fx.write_panel(self.tmp / 'panel'), run=run)
        self.assertEqual(code, 0, err)
        d = json.loads(out.read_text(encoding='utf-8'))
        self.check_contract(d)
        self.assertEqual(d['status'], 'blocked')
        self.assertEqual(d['reason'], 'render_empty')
        self.assertEqual(d['total'], 0)
        self.assertEqual(d['aux']['booth_pixel_fraction'], 0.0)

    def test_shifted_render_scores_lower_on_code_parts(self):
        panel = fx.write_panel(self.tmp / 'panel')
        _, good, _ = self.score(panel=panel)
        shifted = fx.write_preview(self.tmp / 'preview_003', dx=12)
        _, bad, _ = self.score(panel=panel, run=shifted, name='score_shifted.json')
        g, b = (json.loads(p.read_text(encoding='utf-8')) for p in (good, bad))
        self.assertGreater(b['aux']['edge_distance_diag'], g['aux']['edge_distance_diag'])


class FitTests(ScoreCase):
    def detail(self, out):
        return json.loads((out.parent / 'score_detail.json').read_text(encoding='utf-8'))

    def refused(self, **kw):
        code, out, err = self.score(**kw)
        self.assertEqual(code, 2, err)
        self.assertFalse(out.exists())
        self.assertFalse((out.parent / 'score_detail.json').exists())
        return err

    def test_score_requires_fit_and_refuses_a_camera_mismatch(self):
        self.assertIn('--fit', self.refused(fit=False))
        self.assertIn('FILE_MISSING', self.refused(fit=self.tmp / 'nowhere.json'))
        cam = fx.camera()
        for key, value in (('lens_mm', 36.001), ('sensor_width_mm', 35.0), ('position', [0.001, -10.0, 0.0]), ('target', [0.0, 0.001, 0.0]),
                           ('principal_point', [0.51, 0.5]), ('roll_deg', 0.5)):
            fit = fx.write_fit(self.tmp / f'fit_{key}.json', camera_=dict(cam, **{key: value}))
            self.assertIn('FIT_CAMERA_MISMATCH', self.refused(fit=fit), key)
        for key, value in (('lens_mm', 36.00001), ('sensor_width_mm', 36.00001), ('position', [0.0, -10.00001, 0.0]),
                           ('target', [0.00001, 0.0, 0.0]), ('principal_point', [0.5, 0.50001]), ('roll_deg', 0.00001)):
            fit = fx.write_fit(self.tmp / f'fit_tiny_{key}.json', camera_=dict(cam, **{key: value}))     # about 1e-5 off: still another camera
            self.assertIn('FIT_CAMERA_MISMATCH', self.refused(fit=fit), key)
        # within 1e-6 is the same camera; a missing principal point or roll is the default 0.5, 0.5 and 0
        near = fx.write_fit(self.tmp / 'fit_near.json', camera_=dict(cam, lens_mm=36.0 + 5e-7, position=[5e-7, -10.0, 0.0]))
        self.assertEqual(self.score(fit=near, name='near.json')[0], 0)
        bare = {k: v for k, v in cam.items() if k not in ('principal_point', 'roll_deg')}
        self.assertEqual(self.score(fit=fx.write_fit(self.tmp / 'fit_bare.json', camera_=bare), name='bare.json')[0], 0)
        self.assertEqual(self.score(name='good.json')[0], 0)

    def test_score_refuses_a_fit_of_another_picture_size_or_an_unusable_fit(self):
        other = fx.write_fit(self.tmp / 'fit_size.json', image={'width': 640, 'height': 400})
        self.assertIn('FIT_IMAGE_MISMATCH', self.refused(fit=other))
        no_image = fx.fit()
        del no_image['image']
        (self.tmp / 'fit_noimage.json').write_text(json.dumps(no_image), encoding='utf-8')
        self.assertIn('BAD_FIT', self.refused(fit=self.tmp / 'fit_noimage.json'))
        no_dims = fx.fit()
        del no_dims['dims_m']
        (self.tmp / 'fit_nodims.json').write_text(json.dumps(no_dims), encoding='utf-8')
        self.assertIn('BAD_FIT', self.refused(fit=self.tmp / 'fit_nodims.json'))
        (self.tmp / 'fit_broken.json').write_text('{not json', encoding='utf-8')
        self.refused(fit=self.tmp / 'fit_broken.json')
        (self.tmp / 'fit_list.json').write_text('[]', encoding='utf-8')
        self.refused(fit=self.tmp / 'fit_list.json')

    def test_score_detail_reports_render_outside_region(self):
        code, out, err = self.score()
        self.assertEqual(code, 0, err)
        edge = self.detail(out)['edge']
        self.assertEqual(edge['render_outside_region'], 0.0)
        self.assertGreater(edge['region_px'], 40000)
        shifted = fx.write_preview(self.tmp / 'preview_shift', dx=40)
        code, out2, err = self.score(run=shifted, name='shift/score.json')
        self.assertEqual(code, 0, err)
        edge2 = self.detail(out2)['edge']
        self.assertGreater(edge2['render_outside_region'], 0.03)
        self.assertEqual(edge2['region_px'], edge['region_px'], "the region is the fit's, the same for every candidate")

    def test_the_region_is_the_fits_not_the_candidates(self):
        # a render with a part deleted must not look better: the old rule cut the region down to the render itself
        wide = self.score(name='a/score.json')[1]
        narrow = fx.write_fit(self.tmp / 'fit_small.json', dims_m={'W': 4.0, 'D': 2.0, 'H': 2.25})
        code, out, err = self.score(fit=narrow, name='b/score.json')
        self.assertEqual(code, 0, err)
        self.assertLess(self.detail(out)['edge']['region_px'], self.detail(wide)['edge']['region_px'])
        self.assertGreater(self.detail(out)['edge']['render_outside_region'], 0.0)

    def test_fit_is_not_taken_with_validate_truth(self):
        code, _, err = run_cli('--validate-truth', self.truth, '--fit', self.fit)
        self.assertEqual(code, 2)
        self.assertIn('--fit', err)
        self.assertEqual(run_cli('--validate-truth', self.truth)[0], 0)


class PanelTests(ScoreCase):
    INV = [i['id'] for i in fx.inventory()['items']]

    def detail(self, out):
        return json.loads((out.parent / 'score_detail.json').read_text(encoding='utf-8'))

    def level(self, out, key):
        return json.loads(out.read_text(encoding='utf-8'))['levels'][key]['panel']

    def test_a_second_pair_id_is_refused_not_mixed(self):
        panel = fx.write_panel(self.tmp / 'panel')
        other = fx.judge('j1', 1, pair_id='pair99', l2={'reception': 'wrong', 'screen_a': 'wrong'})
        (panel / 'other.json').write_text(json.dumps(other), encoding='utf-8')
        code, out, err = self.score(panel=panel)
        self.assertEqual(code, 2)
        self.assertIn('PANEL_MIXED', err)
        self.assertFalse(out.exists())

    def test_invalid_responses_are_ignored_and_listed(self):
        panel = fx.write_panel(self.tmp / 'panel')
        bad = fx.judge('j1', 1)
        bad['surprise'] = 1                                       # a field outside the contract
        (panel / 'j1_1.json').write_text(json.dumps(bad), encoding='utf-8')
        tie = fx.judge('j2', 1)
        tie['items'], tie['scores'], tie['extra_parts'] = None, None, None    # tie-break mode is not a scoring response
        (panel / 'j2_1.json').write_text(json.dumps(tie), encoding='utf-8')
        (panel / 'broken.json').write_text('{not json', encoding='utf-8')
        code, out, err = self.score(panel=panel)
        self.assertEqual(code, 0, err)
        panel_d = self.detail(out)['panel']
        self.assertEqual({e['source'] for e in panel_d['panel_ignored']}, {'j1_1.json', 'j2_1.json', 'broken.json'})
        # j1 and j2 each lost one pass, so both are undecided as a whole; only j3 counts
        self.assertEqual(panel_d['undecided_judges'], ['j1', 'j2'])
        self.assertEqual(panel_d['judges'], ['j3'])

    def test_keys_without_prefix_or_with_a_foreign_id_make_a_response_invalid(self):
        for n, key in enumerate(('reception', 'L1:not_in_inventory', 'L3:reception', 'L2:')):
            doc = fx.judge('j1', 1)
            doc['items'][key] = 'ok'
            self.assertTrue(cli.validate_judge(doc, set(self.INV)), key)
        doc = fx.judge('j1', 1)
        doc['items']['L1:reception'] = 'ok'                       # wrong value for an L1 key
        self.assertTrue(cli.validate_judge(doc, set(self.INV)))
        doc = fx.judge('j1', 1)
        doc['items']['L2:reception'] = 'present'                  # wrong value for an L2 key
        self.assertTrue(cli.validate_judge(doc, set(self.INV)))
        self.assertEqual(cli.validate_judge(fx.judge('j1', 1), set(self.INV)), [])

    def test_extra_parts_lower_l1_by_the_median_capped_at_five(self):
        out = self.score(panel=fx.write_panel(self.tmp / 'p1', extra_by_judge={'j1': 2, 'j2': 3, 'j3': 9}))[1]
        self.assertAlmostEqual(self.level(out, 'L1'), 27.0)                       # median 3
        out = self.score(panel=fx.write_panel(self.tmp / 'p2', extra_by_judge={'j1': 9, 'j2': 8, 'j3': 7}), name='s2.json')[1]
        self.assertAlmostEqual(self.level(out, 'L1'), 25.0)                       # capped at 5

    def test_extra_parts_median_rounds_up_to_an_integer(self):
        docs = [fx.judge('j1', p, extra_parts=1) for p in (1, 2)] + [fx.judge('j2', p, extra_parts=2) for p in (1, 2)]
        agg = cli.aggregate_panel(docs, self.INV)
        self.assertEqual(agg['extra_parts'], 2)
        self.assertIsInstance(agg['extra_parts'], int)
        self.assertAlmostEqual(agg['levels']['L1'], 28.0)

    def test_extra_parts_that_a_judge_contradicts_are_undecided(self):
        panel = fx.write_panel(self.tmp / 'panel', extra_by_judge={'j1': 4, 'j2': 4, 'j3': 4})
        for j in ('j1', 'j2', 'j3'):                              # every judge changes the number on pass 2
            (panel / f'{j}_2.json').write_text(json.dumps(fx.judge(j, 2, extra_parts=1)), encoding='utf-8')
        code, out, err = self.score(panel=panel)
        self.assertEqual(code, 0, err)
        self.assertAlmostEqual(self.level(out, 'L1'), 30.0)                       # no deduction
        self.assertTrue(self.detail(out)['panel']['extra_parts_undecided'])

    def test_majority_decides_not_the_first_vote(self):
        # j1 (the first judge) says screen_a is missing and wrong; j2 and j3 say present and ok
        docs = [fx.judge('j1', p, inv_present={i for i in self.INV if i != 'screen_a'}) for p in (1, 2)]
        docs += [fx.judge(j, p, l2={'screen_a': 'ok'}) for j in ('j2', 'j3') for p in (1, 2)]
        agg = cli.aggregate_panel(docs, self.INV)
        self.assertEqual(agg['l1']['screen_a'], 'present')
        self.assertEqual(agg['l2']['screen_a'], 'ok')
        self.assertAlmostEqual(agg['levels']['L1'], 30.0)
        self.assertEqual(cli._majority(['missing', 'present', 'present']), 'present')
        self.assertEqual(cli._majority(['wrong', 'ok', 'ok']), 'ok')

    def test_a_majority_needs_two_judges_with_the_same_answer(self):
        self.assertEqual(cli._majority(['ok', 'ok']), 'ok')
        for votes in (['ok', 'off', 'wrong'], ['ok', 'off'], ['ok'], []):
            self.assertIsNone(cli._majority(votes), votes)

    def judges_with(self, per_judge, judges=('j1', 'j2', 'j3')):
        """Both passes of each listed judge, with that judge's L2 answer for the screen_a item."""
        return [fx.judge(j, p, l2={'screen_a': per_judge[j]}) for j in judges for p in (1, 2)]

    def test_items_without_a_majority_are_undecided_and_score_nothing(self):
        full = cli.aggregate_panel(self.judges_with({'j1': 'ok', 'j2': 'ok', 'j3': 'ok'}), self.INV)['levels']['L2']
        cases = {'ok/off/wrong, one vote each': self.judges_with({'j1': 'ok', 'j2': 'off', 'j3': 'wrong'}),
                 'two judges left, different': self.judges_with({'j1': 'ok', 'j2': 'off'}, judges=('j1', 'j2')),
                 'one judge left': self.judges_with({'j1': 'ok'}, judges=('j1',))}
        for name, docs in cases.items():
            agg = cli.aggregate_panel(docs, self.INV)
            self.assertNotIn('screen_a', agg['l2'], name)
            self.assertIn('L2:screen_a', agg['undecided_items'], name)
            if name != 'one judge left':
                self.assertAlmostEqual(agg['levels']['L2'], 20 * (9 * 2) / 20, msg=name)     # screen_a adds 0 instead of its points
            self.assertLess(agg['levels']['L2'], full, name)
        # a lone judge decides nothing at all: every L1 item is undecided and counts as missing
        lone = cli.aggregate_panel(cases['one judge left'], self.INV)
        self.assertEqual(lone['l1'], {})
        self.assertEqual(lone['levels']['L1'], 0.0)
        self.assertEqual(lone['levels']['L2'], 0.0)
        # L1 undecided counts as missing
        docs = [fx.judge('j1', p) for p in (1, 2)] + [fx.judge('j2', p, inv_present={i for i in self.INV if i != 'reception'}) for p in (1, 2)]
        self.assertAlmostEqual(cli.aggregate_panel(docs, self.INV)['levels']['L1'], 30 * 9 / 10)

    def test_l2_panel_has_no_fallback_to_the_judges_own_l2_score(self):
        # every judge calls every item missing: L1 is 0 and so is L2, although the judges gave scores.L2 = 14
        everything_missing = [fx.judge(j, p, inv_present=set()) for j in ('j1', 'j2', 'j3') for p in (1, 2)]
        agg = cli.aggregate_panel(everything_missing, self.INV)
        self.assertEqual(agg['levels']['L1'], 0.0)
        self.assertEqual(agg['levels']['L2'], 0.0)
        self.assertEqual(agg['scores']['L2'], 14.0)
        # every judge changes every L2 answer between the two passes: nothing is decided, L2 is 0
        docs = []
        for j in ('j1', 'j2', 'j3'):
            docs.append(fx.judge(j, 1, l2={i: 'ok' for i in self.INV}))
            docs.append(fx.judge(j, 2, l2={i: 'wrong' for i in self.INV}))
        agg = cli.aggregate_panel(docs, self.INV)
        self.assertEqual(agg['l2'], {})
        self.assertEqual(agg['levels']['L2'], 0.0)
        self.assertAlmostEqual(agg['levels']['L1'], 30.0)

    def test_a_failure_leaves_no_output_files(self):
        spec = json.loads((self.run_dir / 'spec.json').read_text(encoding='utf-8'))
        del spec['components'][0]['id']                           # makes the sheet fail after the numbers are computed
        (self.run_dir / 'spec.json').write_text(json.dumps(spec), encoding='utf-8')
        out_dir = self.tmp / 'result'
        code, _, err = run_cli('--run', self.run_dir, '--photo', self.photo, '--truth', self.truth, '--out', out_dir / 'score.json',
                               '--candidate-id', 'c1', '--panel', fx.write_panel(self.tmp / 'panel'), '--fit', self.fit)
        self.assertEqual(code, 2, err)
        self.assertEqual(list(out_dir.glob('*')) if out_dir.exists() else [], [])

    def test_l2_votes_for_an_item_judged_missing_score_nothing(self):
        docs = [fx.judge(j, p, inv_present={i for i in self.INV if i != 'reception'}) for j in ('j1', 'j2', 'j3') for p in (1, 2)]
        for d in docs:
            d['items']['L2:reception'] = 'ok'
        agg = cli.aggregate_panel(docs, self.INV)
        self.assertEqual(agg['l1']['reception'], 'missing')
        self.assertAlmostEqual(agg['levels']['L2'], 20 * (8 * 2 + 1) / 20)    # eight ok plus screen_a off; the missing item adds 0

    def test_aggregate_panel_is_public_and_returns_the_four_levels(self):
        docs = [fx.judge(j, p) for j in ('j1', 'j2', 'j3') for p in (1, 2)]
        agg = cli.aggregate_panel(docs, self.INV)
        self.assertEqual(set(agg['levels']), {'L1', 'L2', 'L3', 'L4'})
        self.assertEqual(agg['l2']['screen_a'], 'off')
        self.assertEqual(agg['extra_parts'], 0)
        self.assertIs(score.aggregate_panel, cli.aggregate_panel)

    def tally(self, mutate=None, **entry):
        item = {'levels': {'L1': 25, 'L2_panel': 12, 'L3': 10, 'L4_panel': 5},
                'items': {'L1:reception': 'present', 'L2:reception': 'ok', 'L1:screen_a': 'present', 'L2:screen_a': 'wrong',
                          'L2:part_2': 'undecided'}, 'extra_parts': 1}
        item.update(entry)
        doc = {'schema_version': 'tally.v1', 'candidates': {'cand_x': item, 'cand_1': item},
               'judges': {'j1': {'self_consistency': 1}}, 'pairwise_agreement': {'j1-j2': 1, 'j1-j3': 1, 'j2-j3': 1}}
        if mutate:
            mutate(doc)
        path = self.tmp / 'tally.json'
        path.write_text(json.dumps(doc), encoding='utf-8')
        return path

    def test_tally_file_with_candidate_id(self):
        code, out, err = self.score(panel=self.tally(), candidate='cand_x')
        self.assertEqual(code, 0, err)
        d = json.loads(out.read_text(encoding='utf-8'))
        self.assertEqual(d['candidate_id'], 'cand_x')
        self.assertEqual(d['status'], 'measured')
        self.assertEqual([self.level(out, k) for k in ('L1', 'L2', 'L3', 'L4')], [25, 12, 10, 5])
        self.assertEqual(self.detail(out)['panel']['l2']['screen_a'], 'wrong')

    def test_tally_entry_missing_is_an_error(self):
        code, out, err = self.score(panel=self.tally(), candidate='nobody')
        self.assertEqual(code, 2)
        self.assertIn('TALLY_CANDIDATE_MISSING', err)

    def test_tally_is_checked_strictly(self):
        def entry(doc):
            return doc['candidates']['cand_1']

        mutations = {
            'no judges': lambda d: d.pop('judges'),
            'no pairwise': lambda d: d.pop('pairwise_agreement'),
            'extra top field': lambda d: d.update(note='x'),
            'extra entry field': lambda d: entry(d).update(note='x'),
            'L1 above 30': lambda d: entry(d)['levels'].update(L1=31),
            'L2 above 20': lambda d: entry(d)['levels'].update(L2_panel=21),
            'L3 above 20': lambda d: entry(d)['levels'].update(L3=20.5),
            'L4 above 10': lambda d: entry(d)['levels'].update(L4_panel=11),
            'negative level': lambda d: entry(d)['levels'].update(L3=-1),
            'extra_parts float': lambda d: entry(d).update(extra_parts=1.5),
            'extra_parts negative': lambda d: entry(d).update(extra_parts=-1),
            'unprefixed item': lambda d: entry(d)['items'].update(reception='ok'),
            'foreign item id': lambda d: entry(d)['items'].update({'L1:nobody': 'present'}),
            'bad item value': lambda d: entry(d)['items'].update({'L1:reception': 'ok'}),
            'agreement above 1': lambda d: d['pairwise_agreement'].update({'j1-j2': 1.5}),
        }
        for name, mutate in mutations.items():
            code, out, err = self.score(panel=self.tally(mutate=mutate), name='t.json')
            self.assertEqual(code, 2, name)
            self.assertIn('BAD_TALLY', err, name)
            self.assertFalse(out.exists(), name)

    def test_candidate_id_is_required_and_names_the_candidate(self):
        with self.assertRaises(SystemExit) as ctx:                # argparse-style usage error
            self.score(candidate=None)
        self.assertEqual(ctx.exception.code, 2)
        self.assertFalse((self.tmp / 'score.json').exists())
        # two previews that both live in a folder called preview_001 stay distinguishable
        a = self.score(name='a/score.json', candidate='cand_a')[1]
        b = self.score(name='b/score.json', candidate='cand_b')[1]
        self.assertEqual(json.loads(a.read_text(encoding='utf-8'))['candidate_id'], 'cand_a')
        self.assertEqual(json.loads(b.read_text(encoding='utf-8'))['candidate_id'], 'cand_b')


class MapTests(ScoreCase):
    def write_map(self, obj):
        path = self.tmp / 'map.json'
        path.write_text(json.dumps(obj), encoding='utf-8')
        return path

    @staticmethod
    def holdout_rows(out):
        detail = json.loads((out.parent / 'score_detail.json').read_text(encoding='utf-8'))
        return {r['id']: r for r in detail['holdout']}

    def test_map_chooses_components_and_detail_lists_them(self):
        hold = fx.holdout()
        hold['points'][1]['part_id'] = 'part_5'                   # no spec component is called part_5
        fx.write_truth(self.truth, **{'holdout_px.json': hold})
        rows = self.holdout_rows(self.score()[1])
        self.assertTrue(rows['h2']['missing'])
        self.assertEqual(rows['h2']['missing_reason'], 'part_missing')
        self.assertEqual(rows['h1']['matched_ids'], ['reception_body'])
        code, out, err = self.score(name='mapped.json', extra_args=['--map', self.write_map({'part_5': 'screen_a_panel'})])
        self.assertEqual(code, 0, err)
        rows = self.holdout_rows(out)
        self.assertFalse(rows['h2']['missing'])
        self.assertEqual(rows['h2']['matched_ids'], ['screen_a_panel'])
        self.assertEqual(rows['h1']['matched_ids'], ['reception_body'])    # not in the map: exact match as before

    def test_a_mapped_name_missing_from_the_spec_is_a_missing_point_not_an_error(self):
        # a deleted part (an anchor variant) must still be scored
        code, out, err = self.score(extra_args=['--map', self.write_map({'reception': ['reception_body', 'ghost'], 'screen_a': 'deleted'})])
        self.assertEqual(code, 0, err)
        rows = self.holdout_rows(out)
        self.assertEqual(rows['h2']['missing_reason'], 'mapped_name_absent')      # screen_a: nothing left in the spec
        self.assertEqual(rows['h1']['matched_ids'], ['reception_body'])           # reception: one of two names is there
        self.assertEqual(json.loads(out.read_text(encoding='utf-8'))['status'], 'blocked')

    def test_a_map_key_that_is_not_an_inventory_id_is_an_error(self):
        code, out, err = self.score(extra_args=['--map', self.write_map({'recepton': 'reception_body'})])
        self.assertEqual(code, 2)
        self.assertIn('MAP_UNKNOWN_KEY', err)
        self.assertFalse(out.exists())
        code, out, err = self.score(extra_args=['--map', self.write_map(['reception_body'])], name='x.json')
        self.assertEqual(code, 2)
        self.assertIn('BAD_MAP', err)


class OutputFilesTests(ScoreCase):
    def test_panel_view_has_three_plain_cells_and_sheet_has_four(self):
        out = self.score()[1]
        with Image.open(out.parent / 'panel_view.png') as im:
            view = np.asarray(im.convert('RGB'))
        self.assertEqual(view.shape, (fx.H, 3 * fx.W, 3))
        self.assertTrue(np.array_equal(view[:, :fx.W], fx.photo()))              # untouched original: no text, box or point
        grey = Image.new('RGBA', (fx.W, fx.H), (128, 128, 128, 255))
        grey.alpha_composite(Image.fromarray(fx.render(), 'RGBA'))
        self.assertTrue(np.array_equal(view[:, fx.W:2 * fx.W], np.asarray(grey.convert('RGB'))))
        with Image.open(out.parent / 'sheet.png') as sheet:
            self.assertEqual(sheet.size[0], 2 * fx.W)

    def test_holdout_score_uses_the_median_of_the_four_points(self):
        u, v = 160 + 320 / 9.5, 100 - 320 / 9.5
        hold = fx.holdout(uv=((u, v), (u, v), (u, v), (u + 150, v)))
        for p in hold['points']:
            p.update(part_id='reception', corner='top_front_right')
        fx.write_truth(self.truth, **{'holdout_px.json': hold})
        d = json.loads(self.score()[1].read_text(encoding='utf-8'))
        self.assertLess(d['aux']['holdout_median_diag'], 0.001)      # a mean over the four would be about 0.1 of the diagonal
        self.assertEqual(d['levels']['L2']['code'], 10.0)


class ValidateTruthTests(ScoreCase):
    def test_a_good_answer_sheet_passes(self):
        code, out, err = run_cli('--validate-truth', self.truth)
        self.assertEqual(code, 0, out + err)
        self.assertTrue(json.loads(out)['valid'])

    def test_missing_field_is_rejected(self):
        inv = fx.inventory()
        del inv['items'][0]['hero']
        folder = fx.write_truth(self.tmp / 'bad1', **{'inventory.json': inv})
        code, out, _ = run_cli('--validate-truth', folder)
        self.assertEqual(code, 2)
        self.assertFalse(json.loads(out)['valid'])

    def test_missing_file_wrong_counts_and_unknown_part_are_rejected(self):
        folder = fx.write_truth(self.tmp / 'bad2', **{'swatches.json': None})
        (folder / 'swatches.json').unlink(missing_ok=True)
        self.assertEqual(run_cli('--validate-truth', folder)[0], 2)
        short = fx.inventory(5)
        self.assertEqual(run_cli('--validate-truth', fx.write_truth(self.tmp / 'bad3', **{'inventory.json': short}))[0], 2)
        hold = fx.holdout()
        hold['points'][0]['part_id'] = 'no_such_part'
        self.assertEqual(run_cli('--validate-truth', fx.write_truth(self.tmp / 'bad4', **{'holdout_px.json': hold}))[0], 2)
        sw = fx.swatches()
        sw['swatches'][0]['role'] = 'purple'
        self.assertEqual(run_cli('--validate-truth', fx.write_truth(self.tmp / 'bad5', **{'swatches.json': sw}))[0], 2)

    def test_unknown_or_contradictory_corner_words_are_invalid_and_the_two_families_are_described(self):
        for bad in ('near_end_first', 'top_bottom', 'left', 'top', 'front_left', 'top_left_right'):
            hold = fx.holdout()
            hold['points'][0]['corner'] = bad
            folder = fx.write_truth(self.tmp / f'c_{bad}', **{'holdout_px.json': hold})
            code, out, _ = run_cli('--validate-truth', folder)
            self.assertEqual(code, 2, bad)
            text = ' '.join(json.loads(out)['errors'])
            for word in ('top', 'bottom', 'front', 'back', 'left', 'right'):
                self.assertIn(word, text)
            self.assertNotIn('middle', text)                                      # the old "an axis not named is the middle" is gone
            self.assertIn('three', text)                                          # the three-word family ...
            self.assertIn('one horizontal', text)                                 # ... and the one-horizontal-word family
        for good in ('top_left', 'bottom_front', 'top_front_left'):
            hold = fx.holdout()
            hold['points'][0]['corner'] = good
            self.assertEqual(run_cli('--validate-truth', fx.write_truth(self.tmp / f'ok_{good}', **{'holdout_px.json': hold}))[0], 0, good)

    def test_wrongly_typed_fields_are_invalid_not_a_traceback(self):
        inv = fx.inventory()
        inv['items'][0]['id'] = ['a', 'b']
        hold = fx.holdout()
        hold['points'][0]['part_id'] = ['reception']
        sw = fx.swatches()
        sw['swatches'][0]['id'] = {'x': 1}
        hold2 = fx.holdout()
        hold2['points'][1]['id'] = [1]
        pic = fx.inventory()
        pic['picture_id'] = ['pic']
        for n, doc in enumerate(({'inventory.json': inv}, {'holdout_px.json': hold}, {'swatches.json': sw},
                                 {'holdout_px.json': hold2}, {'inventory.json': pic})):
            code, out, _ = run_cli('--validate-truth', fx.write_truth(self.tmp / f't{n}', **doc))
            self.assertEqual(code, 2, n)
            self.assertFalse(json.loads(out)['valid'])

    def test_run_refuses_an_invalid_answer_sheet(self):
        hold = fx.holdout()
        del hold['points'][0]['uv_px']
        fx.write_truth(self.truth, **{'holdout_px.json': hold})
        code, out, err = self.score()
        self.assertEqual(code, 2)
        self.assertFalse(out.exists())


def orbit(yaw_deg, height=3.0, radius=10.0):
    a = math.radians(yaw_deg)
    return {'position': [radius * math.sin(a), -radius * math.cos(a), height], 'target': [0.0, 0.0, 1.0], 'lens_mm': 36.0,
            'sensor_width_mm': 36.0, 'projection': 'perspective', 'principal_point': [0.5, 0.5], 'roll_deg': 0}


class ValidateTruthWithCameraTests(ScoreCase):
    """--validate-truth <dir> --camera <spec.json> [--map m.json]: a drafting aid that reports where each name lands."""

    def draft(self, yaw, corners, uv=None, components=None, camera=None, notes=None):
        spec = fx.spec()
        spec['camera'] = camera or orbit(yaw)
        spec['components'] = components or [fx.component('reception_body', (2, 2, 2), (0, 0, 1), group='reception'),
                                            fx.component('screen_a_panel', (2, 2, 2), (6, 4, 1), group='screen_a')]
        path = self.tmp / f'spec_{yaw}.json'
        path.write_text(json.dumps(spec), encoding='utf-8')
        hold = fx.holdout()
        for p, c in zip(hold['points'], corners):
            p['corner'] = c
            p['part_id'] = 'reception'
            p['uv_px'] = (uv or {}).get(p['id'], [100.0, 100.0])
            p['note'] = (notes or {}).get(p['id'], 'a note')
        folder = fx.write_truth(self.tmp / f'truth_{yaw}', **{'holdout_px.json': hold})
        return folder, path

    def report(self, yaw, corners, uv=None, extra=(), **kw):
        folder, spec = self.draft(yaw, corners, uv, **kw)
        code, out, err = run_cli('--validate-truth', folder, '--camera', spec, *extra)
        return code, json.loads(out), err

    def px(self, point, yaw):
        import score.metrics as m
        return m.camera_math.project(point, orbit(yaw), fx.W, fx.H)[:2]

    def test_without_a_camera_nothing_changes(self):
        code, out, _ = run_cli('--validate-truth', self.truth)
        self.assertEqual(code, 0)
        self.assertNotIn('points', json.loads(out))

    def test_every_valid_name_of_both_families_is_listed_for_every_point(self):
        corners = ['top_front_left', 'bottom_back_right', 'top_left', 'top_front']
        target = self.px([-1.0, -1.0, 2.0], 20.0)
        code, rep, err = self.report(20.0, corners, uv={'h1': [target[0] + 3.0, target[1] + 4.0]}, notes={'h1': 'the near left top corner'})
        self.assertEqual(code, 0, rep.get('errors'))
        self.assertTrue(rep['valid'])
        row = rep['points'][0]
        self.assertEqual([p['id'] for p in rep['points']], ['h1', 'h2', 'h3', 'h4'])
        self.assertEqual(row['note'], 'the near left top corner')
        self.assertEqual(row['picked_world'], [-1.0, -1.0, 2.0])
        self.assertAlmostEqual(row['picked_px'][0], target[0], delta=0.01)
        self.assertAlmostEqual(row['distance_px'], 5.0, delta=0.02)
        self.assertTrue(row['has_margin'])
        self.assertEqual(row['matched_ids'], ['reception_body'])
        names = row['names']
        self.assertEqual(len(names), 16)
        order = [(0 if n['family'] == 'three_word' else 1, n['name']) for n in names]
        self.assertEqual(order, sorted(order))                                  # by family, then by name, not by distance
        self.assertEqual({n['family'] for n in names}, {'three_word', 'one_horizontal'})
        by = {n['name']: n for n in names}
        import score.metrics as m
        for name, entry in by.items():
            world = m.corner_point(([-1.0, -1.0, 0.0], [1.0, 1.0, 2.0]), name, orbit(20.0), fx.W, fx.H)
            self.assertEqual(entry['corner_world'], world, name)
            px = self.px(world, 20.0)
            self.assertAlmostEqual(entry['px'][0], px[0], delta=0.01)
            self.assertAlmostEqual(entry['distance_px'], math.hypot(px[0] - (target[0] + 3.0), px[1] - (target[1] + 4.0)), delta=0.02)
            self.assertIsInstance(entry['has_margin'], bool)
        self.assertTrue(by['top_front_left']['has_margin'])
        self.assertEqual(by['top_front_left']['corner_world'], row['picked_world'])

    def test_a_name_that_matches_the_note_is_listed_even_when_it_is_not_the_nearest_corner(self):
        # a thin part seen from a corner: the nearest box corner to uv_px is not the one the note describes
        thin = [fx.component('reception_body', (6, 0.2, 2), (0, 0, 1), group='reception'),
                fx.component('screen_a_panel', (2, 2, 2), (6, 4, 1), group='screen_a')]
        near_corner = [3.0, -0.1, 2.0]
        code, rep, err = self.report(40.0, ['top_left', 'top_front', 'top_right', 'bottom_back'], components=thin,
                                     uv={'h1': self.px(near_corner, 40.0)})
        row = rep['points'][0]
        by = {n['name']: n for n in row['names']}
        self.assertEqual(len(by), 16)
        self.assertIn('top_left', by)
        self.assertEqual(by['top_left']['corner_world'], row['picked_world'])
        self.assertIsNotNone(by['top_left']['px'])
        self.assertNotEqual(by['top_left']['corner_world'], near_corner)         # not the corner nearest to uv_px, still listed

    def test_a_name_far_from_uv_px_gives_a_warning_not_an_error(self):
        corners = ['top_front_left', 'top_left', 'top_front', 'bottom_right']
        target = self.px([-1.0, -1.0, 2.0], 20.0)
        code, rep, _ = self.report(20.0, corners, uv={'h1': target})
        self.assertFalse([w for w in rep['warnings'] if 'points[0]' in w and 'px from uv_px' in w])
        code, rep, _ = self.report(20.0, corners, uv={'h1': [target[0] + 60.0, target[1]]})
        self.assertEqual(code, 0, rep['errors'])
        far = [w for w in rep['warnings'] if 'points[0]' in w and 'px from uv_px' in w]
        self.assertEqual(len(far), 1, rep['warnings'])
        self.assertIn('check that it says what the note says', far[0])

    def test_a_name_without_margin_exits_2_but_the_report_is_still_written(self):
        # 44 degrees: three words flip, the left word does not
        corners = ['top_front_left', 'top_left', 'top_front', 'bottom_right']
        target = self.px([-1.0, -1.0, 2.0], 44.0)
        code, rep, err = self.report(44.0, corners, uv={'h1': target})
        self.assertEqual(code, 2)
        self.assertFalse(rep['points'][0]['has_margin'])
        self.assertTrue(rep['points'][1]['has_margin'])
        self.assertTrue(any('margin' in e or 'keep' in e or 'loses' in e for e in rep['errors']))
        by = {n['name']: n for n in rep['points'][0]['names']}
        self.assertFalse(by['top_front_left']['has_margin'])
        self.assertTrue(by['top_left']['has_margin'])
        self.assertEqual(by['top_left']['corner_world'], [-1.0, -1.0, 2.0])

    def test_map_chooses_the_components_and_a_missing_part_is_a_warning_naming_it(self):
        folder, spec = self.draft(20.0, ['top_front_left', 'top_left', 'top_front', 'bottom_back'])
        hold = json.loads((folder / 'holdout_px.json').read_text(encoding='utf-8'))
        hold['points'][1]['part_id'] = 'part_5'
        hold['points'][2]['part_id'] = 'part_6'
        fx.write_truth(folder, **{'holdout_px.json': hold})
        m = self.tmp / 'map.json'
        m.write_text(json.dumps({'part_5': 'screen_a_panel', 'part_6': 'deleted_thing'}), encoding='utf-8')
        code, out, _ = run_cli('--validate-truth', folder, '--camera', spec, '--map', m)
        rep = json.loads(out)
        self.assertEqual(code, 0, rep['errors'])                                   # a missing part is reported, not fatal
        self.assertEqual(rep['points'][1]['matched_ids'], ['screen_a_panel'])
        self.assertIsNotNone(rep['points'][1]['picked_px'])
        self.assertEqual(rep['points'][2]['missing_reason'], 'mapped_name_absent')
        self.assertIsNone(rep['points'][2]['has_margin'])
        self.assertTrue(any('part_6' in w for w in rep['warnings']), rep['warnings'])

    def test_nothing_checked_is_not_valid(self):
        folder, spec = self.draft(20.0, ['top_front_left', 'top_left', 'top_front', 'bottom_back'])
        hold = json.loads((folder / 'holdout_px.json').read_text(encoding='utf-8'))
        for p in hold['points']:
            p['part_id'] = 'part_7'                                               # no component belongs to it
        fx.write_truth(folder, **{'holdout_px.json': hold})
        code, out, _ = run_cli('--validate-truth', folder, '--camera', spec)
        rep = json.loads(out)
        self.assertEqual(code, 2)
        self.assertFalse(rep['valid'])
        self.assertTrue(any('could be checked' in e for e in rep['errors']), rep['errors'])

    def test_a_name_that_cannot_be_placed_is_an_error(self):
        # a 6 x 8 m deck with the camera standing on it: the two near corners are behind the camera and top_left compares the far two;
        # a camera beyond the deck has all four behind it, so top_left cannot be placed
        cam = dict(orbit(0.0), position=[0.5, -3.0, 1.6], target=[0.5, 2.0, 1.0])
        deck = [fx.component('reception_body', (6, 8, 0.1), (0, 0, 0.05), group='reception')]
        code, rep, _ = self.report(0.0, ['top_back_left', 'top_left', 'top_back_right', 'bottom_back_left'], components=deck, camera=cam)
        self.assertEqual(rep['points'][0]['missing_reason'], None)                # top_back_left is placeable
        self.assertEqual(rep['points'][1]['picked_world'], rep['points'][0]['picked_world'])    # top_left picks the same visible far-left corner
        code, rep, _ = self.report(0.0, ['top_left', 'top_left', 'top_left', 'top_left'], components=deck,
                                   camera=dict(cam, position=[0.5, 4.5, 1.6], target=[0.5, 9.0, 1.0]))
        self.assertEqual(code, 2)
        self.assertTrue(all(p['missing_reason'] == 'corner_behind_camera' for p in rep['points']), [p['missing_reason'] for p in rep['points']])
        self.assertTrue(all(p['has_margin'] is False for p in rep['points']))
        self.assertEqual(len([e for e in rep['errors'] if 'cannot be placed' in e]), 4, rep['errors'])

    def test_the_report_survives_a_stdout_that_is_not_utf8(self):
        # a Windows console or a pipe may use cp1252: the report echoes the Chinese note, so it must be written as UTF-8
        notes = {'h1': '门头左上角', 'h2': '地台前右角'}
        folder, spec = self.draft(20.0, ['top_front_left', 'top_left', 'top_front', 'bottom_back'], notes=notes)
        env = dict(os.environ, PYTHONIOENCODING='cp1252')
        run = subprocess.run([sys.executable, str(ROOT / 'tools' / 'poc' / 'score' / 'score.py'), '--validate-truth', str(folder),
                              '--camera', str(spec)], capture_output=True, env=env)
        self.assertEqual(run.returncode, 0, run.stderr.decode('utf-8', 'replace'))
        rep = json.loads(run.stdout.decode('utf-8'))
        self.assertEqual(rep['points'][0]['note'], '门头左上角')
        self.assertEqual(rep['points'][1]['note'], '地台前右角')

    def test_every_spelling_the_validator_accepts_is_accepted_and_shown_in_canonical_form(self):
        spellings = {'left_top': 'top_left', 'top_left_front': 'top_front_left', 'front_bottom': 'bottom_front', 'right_back_top': 'top_back_right'}
        folder, spec = self.draft(20.0, list(spellings))
        self.assertEqual(run_cli('--validate-truth', folder)[0], 0)                 # the plain validator accepts them
        code, out, err = run_cli('--validate-truth', folder, '--camera', spec)
        rep = json.loads(out)
        self.assertEqual(code, 0, rep['errors'])
        for row, (written, canonical) in zip(rep['points'], spellings.items()):
            self.assertEqual(row['corner'], written)
            self.assertEqual(row['canonical_corner'], canonical)
            self.assertNotEqual(row['missing_reason'], 'invalid_name')
            self.assertIsNotNone(row['picked_px'])
            self.assertEqual(row['picked_world'], next(n for n in row['names'] if n['name'] == canonical)['corner_world'])

    def test_options_that_would_be_ignored_are_usage_errors(self):
        m = self.tmp / 'm.json'
        m.write_text('{}', encoding='utf-8')
        code, _, err = run_cli('--validate-truth', self.truth, '--map', m)
        self.assertEqual(code, 2)
        self.assertIn('--camera', err)
        spec = self.tmp / 's.json'
        spec.write_text('{}', encoding='utf-8')
        code, _, err = run_cli('--run', self.run_dir, '--photo', self.photo, '--truth', self.truth, '--out', self.tmp / 'x' / 'score.json',
                               '--candidate-id', 'c', '--camera', spec)
        self.assertEqual(code, 2)
        self.assertIn('--camera', err)
        self.assertFalse((self.tmp / 'x').exists())
        code, _, err = run_cli('--validate-truth', self.truth, '--panel', self.tmp)
        self.assertEqual(code, 2)
        self.assertIn('--panel', err)

    def test_the_two_percent_warning_threshold_is_pinned(self):
        diag = math.hypot(fx.W, fx.H)
        target = self.px([-1.0, -1.0, 2.0], 20.0)
        corners = ['top_front_left', 'top_left', 'top_front', 'bottom_back']
        for share, expect in ((0.019, False), (0.021, True)):
            code, rep, _ = self.report(20.0, corners, uv={'h1': [target[0] + share * diag, target[1]]})
            self.assertEqual(code, 0, rep['errors'])
            warned = [w for w in rep['warnings'] if 'points[0]' in w and 'px from uv_px' in w]
            self.assertEqual(bool(warned), expect, (share, rep['warnings']))

    def test_a_failure_while_listing_the_names_still_counts(self):
        import score.metrics as m
        real = m.corner_point

        def flaky(bounds, name, camera, width=1000.0, height=1000.0):
            if name == 'bottom_left':
                raise ValueError('boom')
            return real(bounds, name, camera, width, height)

        m.corner_point = flaky
        try:
            code, rep, _ = self.report(20.0, ['top_front_left', 'top_left', 'top_front', 'bottom_back'])
        finally:
            m.corner_point = real
        self.assertEqual(code, 2)
        self.assertTrue(any('camera_unusable' in e for e in rep['errors']), rep['errors'])

    def test_broken_camera_files_are_an_input_error_not_a_traceback(self):
        good = fx.spec()
        good['camera'] = orbit(20.0)
        cases = {
            'no camera': {'components': []},
            'position with two numbers': dict(good, camera=dict(good['camera'], position=[1.0, 2.0])),
            'zero sensor width': dict(good, camera=dict(good['camera'], sensor_width_mm=0)),
            'zero lens': dict(good, camera=dict(good['camera'], lens_mm=0)),
            'position equals target': dict(good, camera=dict(good['camera'], position=[0.0, 0.0, 1.0])),
            'straight down': dict(good, camera=dict(good['camera'], position=[0.0, 0.0, 11.0])),
            'orthographic': dict(good, camera=dict(good['camera'], projection='orthographic')),
            'text numbers': dict(good, camera=dict(good['camera'], position=['a', 'b', 'c'])),
            'no source size': {k: v for k, v in good.items() if k != 'source'},
            'zero source size': dict(good, source={'width': 0, 'height': 200}),
        }
        for label, spec in cases.items():
            bad = self.tmp / 'broken.json'
            bad.write_text(json.dumps(spec), encoding='utf-8')
            code, out, err = run_cli('--validate-truth', self.truth, '--camera', bad)
            self.assertEqual(code, 2, label)
            self.assertIn('BAD_CAMERA', err, label)
            self.assertNotIn('Traceback', err, label)


class ReaderSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validator = cli.reader_validator()
        design = json.loads((ROOT / 'booth-image-to-model' / 'templates' / 'design_template.json').read_text(encoding='utf-8'))
        cls.good = {
            'schema_version': 'reader.v1', 'picture_id': 'pic_A', 'design': design,
            'evidence': {'hero_wall': {'bbox_px': [10, 20, 300, 200], 'confidence': 'high'}},
            'unexpressible': [{'what': 'a spiral stair', 'nearest_kind': None, 'why': 'no family builds it'}],
        }

    def errors(self, doc):
        return list(self.validator.iter_errors(doc))

    def test_a_complete_reader_document_is_valid(self):
        self.assertEqual(self.errors(self.good), [])

    def test_reader_schema_rejects_extra_fields_inside_design(self):
        doc = copy.deepcopy(self.good)
        doc['design']['surprise'] = 1
        self.assertTrue(self.errors(doc))
        doc = copy.deepcopy(self.good)
        doc['design']['form']['extra'] = True
        self.assertTrue(self.errors(doc))

    def test_reader_schema_rejects_extra_top_level_or_missing_fields(self):
        doc = copy.deepcopy(self.good)
        doc['notes'] = 'free text'
        self.assertTrue(self.errors(doc))
        for key in ('evidence', 'unexpressible', 'design', 'picture_id'):
            doc = copy.deepcopy(self.good)
            del doc[key]
            self.assertTrue(self.errors(doc), key)
        doc = copy.deepcopy(self.good)
        doc['evidence']['hero_wall']['confidence'] = 'certain'
        self.assertTrue(self.errors(doc))
        doc = copy.deepcopy(self.good)
        doc['evidence']['hero_wall']['bbox_px'] = [1, 2, 3]
        self.assertTrue(self.errors(doc))


if __name__ == '__main__':
    unittest.main()
