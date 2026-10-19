"""Scene description 1.1: corner_points[].fit, the compat shim for older files, the landmarks' fit flags and the quality gate.

The gate is tested on fit results written by hand (the shape box_fit emits), so these tests do not depend on how fast or how far
box_fit has come; one test also runs the real fit and only asserts what holds for any version of it.
"""
from __future__ import annotations
import copy
import inspect
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest import mock
import unittest

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import box_fit
import draft_compile as dc
import test_draft_compile as t

LIMITS = {'rms_px': 4.0, 'check_median_px': 6.0, 'check_max_px': 12.0, 'loo_median_px': 6.0}      # the shape box_fit reports under "limits"
GOOD = {'status': 'fitted', 'rms_px': 1.2, 'loss': 'soft_l1', 'n_fit': 8, 'n_check': 2,
        'check_points': [{'id': 'floor_bl', 'err_px': 2.0}, {'id': 'top_fr', 'err_px': 3.5}], 'check_median_px': 2.75, 'check_max_px': 3.5,
        'loo_reliable': True, 'loo_errors_px': {'floor_br': 1.5, 'floor_fr': 2.5, 'floor_fl': 2.0}, 'loo_median_px': 2.0, 'loo_max_px': 2.5,
        'outliers': [], 'scale_checks': [], 'limits': LIMITS}
NO_LOO = {'loo_reliable': False, 'loo_errors_px': {}, 'loo_median_px': None, 'loo_max_px': None, 'outliers': []}      # what box_fit reports
NO_CHECKS = {'n_check': 0, 'check_points': [], 'check_median_px': None, 'check_max_px': None}                          # when it cannot name a corner


def fit_with(**kw):
    out = copy.deepcopy(GOOD)
    out.update(kw)
    return out


def scene_with_checks(*ids):
    """The fixture booth with eight clicked corners (the two far-from-the-fixture ones added); the ids given are check corners."""
    s = t.scene()
    for i, xyz, tok in (('top_bl', (-t.W / 2, -t.D / 2, t.H), ['-W/2', '-D/2', 'H']), ('top_br', (t.W / 2, -t.D / 2, t.H), ['W/2', '-D/2', 'H'])):
        s['corner_points'].append({'id': i, 'uv_px': t.px(*xyz), 'xyz': tok, 'fit': None})
    for p in s['corner_points']:
        p['fit'] = False if p['id'] in ids else None
    return s


class GateTests(unittest.TestCase):
    def review(self, fit):
        gate = dc.quality_gate(fit)
        self.assertEqual(gate['status'], 'review', gate)
        self.assertTrue(gate['reasons'] and all(isinstance(r, str) and r for r in gate['reasons']))
        return ' | '.join(gate['reasons'])

    def passes(self, fit):
        self.assertEqual(dc.quality_gate(fit), {'status': 'pass', 'reasons': []})

    def test_a_clean_fit_with_independent_checks_passes(self):
        self.passes(GOOD)

    def test_a_reliable_clean_leave_one_out_is_enough_without_check_corners(self):
        self.passes(fit_with(**NO_CHECKS))
        self.passes(fit_with(n_check=0, check_points=None, check_median_px=None, check_max_px=None))       # keys missing or null

    def test_check_corners_alone_are_an_independent_check(self):
        self.passes(fit_with(**NO_LOO))

    def test_every_status_other_than_fitted_needs_a_review(self):
        self.assertIn('error', self.review(fit_with(status='high_residual', rms_px=7.3)))
        text = self.review(fit_with(status='ambiguous', ambiguous_dims=['W', 'D']))
        self.assertIn('W, D', text)
        self.assertIn('not predict', self.review(fit_with(status='check_failed')))
        self.assertIn('blocked', self.review(fit_with(status='blocked')))

    def test_the_limits_are_the_ones_box_fit_reports(self):
        fit = fit_with(check_median_px=7.0, check_max_px=9.0)
        self.assertIn('limit 6 px', self.review(fit))
        self.passes(dict(fit, limits=dict(LIMITS, check_median_px=8.0)))                 # the same miss is fine under a looser limit
        loose = dict(fit)
        del loose['limits']                                                              # no limits reported: the gate invents none
        self.passes(loose)
        self.assertIn('limit 12 px', self.review(fit_with(check_median_px=5.0, check_max_px=30.0)))
        self.assertIn('limit 6 px', self.review(fit_with(loo_median_px=9.0, loo_max_px=12.0)))
        self.passes(fit_with(loo_median_px=9.0, loo_max_px=12.0, limits=dict(LIMITS, loo_median_px=10.0)))

    def test_the_gate_has_no_limits_or_picture_scaling_of_its_own(self):
        self.assertEqual([n for n in dir(dc) if n.startswith('GATE_')], [])
        self.assertEqual(list(inspect.signature(dc.quality_gate).parameters), ['fit'])
        big = fit_with(check_median_px=7.0)                                              # the same numbers, whatever the picture size
        self.assertEqual(dc.quality_gate(big), dc.quality_gate(copy.deepcopy(big)))
        self.assertEqual(dc.quality_gate(big)['status'], 'review')

    def test_missing_or_malformed_limits_judge_nothing(self):
        for junk in (None, [], 'wide', {}, {'check_median_px': 'x', 'check_max_px': -1, 'loo_median_px': None}):
            self.passes(fit_with(check_median_px=40.0, check_max_px=40.0, loo_median_px=40.0, loo_max_px=40.0, limits=junk))

    def test_check_corners_that_could_not_be_measured_need_a_review(self):
        self.assertIn('could not be measured', self.review(fit_with(check_median_px=None, check_max_px=None)))
        self.assertIn('could not be measured', self.review(fit_with(check_median_px=float('nan'))))

    def test_outliers_are_named_when_leave_one_out_is_reliable(self):
        text = self.review(fit_with(outliers=['top_fl', 'floor_br']))
        self.assertIn('top_fl, floor_br', text)
        self.assertIn('top_fl', self.review(fit_with(**NO_CHECKS, outliers=['top_fl'])))

    def test_clicks_box_fit_dropped_from_the_camera_are_said_so(self):
        text = self.review(fit_with(outliers=['top_fl', 'floor_br'], dropped=['top_fl', 'floor_br']))
        self.assertIn('top_fl, floor_br; the camera was refitted without them', text)
        self.assertNotIn('refitted', self.review(fit_with(outliers=['top_fl'])))
        self.assertIn('refitted without the clicks top_fl', dc.fit_note(fit_with(dropped=['top_fl'])))
        self.assertNotIn('refitted', dc.fit_note(GOOD))

    def test_an_unreliable_leave_one_out_names_nobody_and_does_not_count(self):
        shaky = fit_with(loo_reliable=False, loo_errors_px={'a': 40.0, 'b': 3.0}, loo_median_px=21.0, loo_max_px=40.0, outliers=['a'])
        self.passes(shaky)                                                               # the check corners are clean; the guesses are ignored
        text = self.review(dict(shaky, **NO_CHECKS))
        self.assertIn('no independent check', text)
        self.assertNotIn('disagree', text)
        self.assertNotIn('mis-click', text)
        self.assertNotIn('leaving one corner out', text)

    def test_no_independent_signal_at_all_needs_a_review(self):
        text = self.review(fit_with(**NO_CHECKS, **NO_LOO))
        self.assertIn('no independent check', text)
        self.assertIn('check corners', text)                                             # and it says how to get one

    def test_a_fit_from_before_the_checks_existed_needs_a_review(self):
        old = {'status': 'fitted', 'rms_px': 1.0, 'max_px': 2.0, 'dims_m': {'W': 6, 'D': 5, 'H': 3}}
        self.assertIn('no independent check', self.review(old))

    def test_a_fit_without_loo_reliable_is_trusted_as_far_as_it_has_numbers(self):
        fit = fit_with(**NO_CHECKS)
        del fit['loo_reliable']
        self.passes(fit)
        self.assertIn('leaving one corner out', self.review(dict(fit, loo_median_px=9.0)))
        del fit['loo_median_px']
        self.assertIn('no independent check', self.review(fit))

    def test_large_leave_one_out_errors_need_a_review(self):
        self.assertIn('leaving one corner out', self.review(fit_with(loo_median_px=9.0, loo_max_px=12.0)))
        self.assertIn('mis-clicked', self.review(fit_with(loo_median_px=3.0, loo_max_px=40.0, limits=dict(LIMITS, loo_max_px=20.0))))

    def test_a_failed_size_check_needs_a_review(self):
        failed = {'id': 'table', 'expected_m': 0.74, 'measured_m': 1.18, 'ratio': 1.59, 'ok': False}
        text = self.review(fit_with(scale_checks=[failed]))
        self.assertIn('a size check failed', text)
        self.assertIn('table', text)
        self.assertIn('1.18', text)
        self.assertIn('0.74', text)
        self.passes(fit_with(scale_checks=[dict(failed, measured_m=0.75, ratio=1.01, ok=True)]))
        # box_fit before the names were shortened, and a check that could not be measured at all
        legacy = {'id': 'seat', 'measured_height_m': 0.2, 'expected_height_m': 0.45, 'ratio': 0.44, 'tolerance': 0.15, 'ok': False}
        self.assertIn('0.20 m', self.review(fit_with(scale_checks=[legacy])))
        text = self.review(fit_with(scale_checks=[{'id': 'stool', 'ok': False, 'error': 'the top is above the horizon'}]))
        self.assertIn('stool could not be measured (the top is above the horizon)', text)

    def test_a_size_check_is_independent_of_the_other_signals(self):
        failed = {'id': 'table', 'expected_m': 0.74, 'measured_m': 1.18, 'ratio': 1.59, 'ok': False}
        text = self.review(fit_with(**NO_CHECKS, **NO_LOO, scale_checks=[failed]))
        self.assertIn('a size check failed', text)
        self.assertIn('no independent check', text)
        self.assertIn('a size check failed', self.review(fit_with(scale_checks=[{'id': 'a', 'ok': True}, failed])))

    def test_a_check_failed_status_is_explained_by_its_numbers_when_there_are_some(self):
        gate = dc.quality_gate(fit_with(status='check_failed', check_median_px=14.0, check_max_px=19.0, outliers=['top_fl']))
        self.assertEqual(gate['status'], 'review')
        self.assertEqual(len(gate['reasons']), 2, gate)
        self.assertFalse([r for r in gate['reasons'] if 'looks fine' in r])
        self.assertEqual(len(dc.quality_gate(fit_with(status='check_failed'))['reasons']), 1)       # no numbers: the status says it

    def test_several_problems_give_several_reasons(self):
        gate = dc.quality_gate(fit_with(status='high_residual', rms_px=9.0, check_median_px=20.0, outliers=['a'],
                                        scale_checks=[{'id': 'table', 'expected_m': 0.74, 'measured_m': 1.2, 'ratio': 1.6, 'ok': False}]))
        self.assertGreaterEqual(len(gate['reasons']), 4)


class SchemaTests(unittest.TestCase):
    def raw_valid(self, s):
        return Draft202012Validator(dc.load_schema()).is_valid(s)

    def test_a_1_1_scene_with_and_without_check_corners_validates(self):
        for s in (t.scene(), scene_with_checks('floor_bl', 'top_fr')):
            self.assertEqual(s['schema_version'], '1.1')
            self.assertTrue(self.raw_valid(s))
            self.assertEqual(dc.validate_scene(s), [])

    def test_fit_is_required_and_may_be_a_boolean_or_null_only(self):
        props = dc.load_schema()['properties']['corner_points']['items']
        self.assertIn('fit', props['required'])
        self.assertEqual(props['properties']['fit']['type'], ['boolean', 'null'])
        for value in (True, False, None):
            s = t.scene()
            s['corner_points'][0]['fit'] = value
            self.assertTrue(self.raw_valid(s), value)
        for value in ('no', 0, 1, [False]):
            s = t.scene()
            s['corner_points'][0]['fit'] = value
            self.assertFalse(self.raw_valid(s), value)
            self.assertEqual({e['code'] for e in dc.validate_scene(s)}, {'SCHEMA'}, value)

    def test_schema_versions(self):
        for version in ('1.1', '1.0', 'scene-description.v1'):
            s = t.scene()
            s['schema_version'] = version
            self.assertEqual(dc.validate_scene(s), [], version)
        for version in ('2.0', '1', 1.1, None):
            s = t.scene()
            s['schema_version'] = version
            self.assertEqual({e['code'] for e in dc.validate_scene(s)}, {'SCHEMA'}, version)


class OlderScenes(t.Base):
    @staticmethod
    def old_scene():
        s = t.scene()
        s['schema_version'] = 'scene-description.v1'
        for p in s['corner_points']:
            del p['fit']
        return s

    def test_a_scene_without_fit_still_validates_and_is_not_edited(self):
        old = self.old_scene()
        self.assertFalse(Draft202012Validator(t.SCHEMA).is_valid(old))             # the strict schema alone would refuse it
        self.assertEqual(dc.validate_scene(old), [])
        self.assertTrue(all('fit' not in p for p in old['corner_points']))          # the caller's dict is untouched
        up = dc.upgrade_scene(old)
        self.assertIsNot(up, old)
        self.assertTrue(all(p['fit'] is None for p in up['corner_points']))
        full = t.scene()
        self.assertIs(dc.upgrade_scene(full), full)                                 # nothing to fill: the same object comes back

    def test_the_shim_leaves_other_problems_to_the_schema(self):
        s = self.old_scene()
        s['corner_points'][0]['surprise'] = 1
        s['corner_points'][1] = 'not an object'
        self.assertEqual({e['code'] for e in dc.validate_scene(s)}, {'SCHEMA'})
        s = self.old_scene()
        s['corner_points'] = 'none'
        self.assertEqual({e['code'] for e in dc.validate_scene(s)}, {'SCHEMA'})

    def test_a_scene_without_fit_compiles_with_every_corner_in_the_fit(self):
        old = self.old_scene()
        spec, report = dc.compile_scene(old, self.image, self.dir / 'old_scene', fit=fit_complete(self.fit))
        self.assertTrue(report['valid'])
        self.assertEqual(len(spec['landmarks']), 4)
        self.assertTrue(all(lm['fit'] for lm in spec['landmarks']))
        self.assertTrue(all('fit' not in p for p in old['corner_points']))

    def test_older_scene_goes_to_box_fit_without_any_fit_flag(self):
        seen = {}

        def capture(data, seeds=40):
            seen.update(data)
            return fit_complete(self.fit)
        with mock.patch.object(box_fit, 'solve', side_effect=capture):
            dc.fit_camera(self.old_scene())
        self.assertEqual(len(seen['points']), 6)
        self.assertTrue(all('fit' not in p for p in seen['points']))


def legacy_fit(base):
    """A box_fit result from before the checks existed: none of the 1.1 numbers, only status and rms."""
    return {k: v for k, v in base.items() if k not in GOOD or k in ('status', 'rms_px')}


def fit_complete(base, **kw):
    """The real fit of the fixture scene with the 1.1 numbers added by hand (so the tests do not depend on box_fit's version)."""
    out = copy.deepcopy(base)
    out.update(copy.deepcopy(GOOD))
    out.update(kw)
    out['status'] = kw.get('status', 'fitted')
    return out


class FitFlags(t.Base):
    def test_check_corners_are_marked_fit_false_in_the_points_for_box_fit(self):
        seen = {}

        def capture(data, seeds=40):
            seen.update(data)
            return fit_complete(self.fit)
        with mock.patch.object(box_fit, 'solve', side_effect=capture):
            dc.fit_camera(scene_with_checks('floor_bl', 'top_fr'))
        flags = {p['id']: p.get('fit', True) for p in seen['points']}
        self.assertEqual(flags, {'floor_bl': False, 'floor_br': True, 'floor_fr': True, 'floor_fl': True, 'top_fl': True, 'top_fr': False,
                                 'top_bl': True, 'top_br': True})
        self.assertEqual([p for p in seen['points'] if p['id'] == 'floor_br'][0],
                         {'id': 'floor_br', 'uv_px': t.px(t.W / 2, -t.D / 2, t.ZF), 'xyz': ['W/2', '-D/2', 'F']})      # no key unless false

    def test_landmarks_carry_the_fit_flag_and_only_floor_corners_become_landmarks(self):
        s = scene_with_checks('floor_bl', 'floor_fr', 'top_fr')
        spec, report = dc.compile_scene(s, self.image, self.dir / 'flags', fit=fit_complete(self.fit, check_points=[
            {'id': i, 'err_px': 2.0} for i in ('floor_bl', 'floor_fr', 'top_fr')], n_check=3))
        flags = {lm['id']: lm['fit'] for lm in spec['landmarks']}
        self.assertEqual(flags, {'floor_bl': False, 'floor_br': True, 'floor_fr': False, 'floor_fl': True})   # top_fr: no part to attach to
        self.assertTrue(report['valid'], report['validation_errors'])
        self.assertEqual([c['id'] for c in report['fit']['check_points']], ['floor_bl', 'floor_fr', 'top_fr'])   # still reported
        self.assertFalse([w for w in report['warnings'] if 'check_points' in w])
        written = json.loads((self.dir / 'flags' / 'spec.json').read_text(encoding='utf-8'))
        self.assertEqual(sorted(lm['id'] for lm in written['landmarks'] if not lm['fit']), ['floor_bl', 'floor_fr'])

    def test_a_check_corner_the_fit_did_not_report_is_a_warning(self):
        s = scene_with_checks('floor_bl')
        stale = fit_complete(self.fit, check_points=[], n_check=0)
        spec, report = dc.compile_scene(s, self.image, self.dir / 'stale', fit=stale)
        self.assertTrue(any('floor_bl' in w and 'check_points' in w for w in report['warnings']), report['warnings'])
        self.assertFalse([lm for lm in spec['landmarks'] if lm['id'] == 'floor_bl'][0]['fit'])
        legacy = legacy_fit(self.fit)
        spec, report = dc.compile_scene(s, self.image, self.dir / 'legacy', fit=legacy)
        self.assertTrue(any('floor_bl' in w and 'check_points' in w for w in report['warnings']), report['warnings'])

    def test_a_check_corner_box_fit_left_out_on_purpose_is_not_called_stale(self):
        s = scene_with_checks('floor_bl', 'top_fr')
        s['corner_points'].append({'id': 'wall_edge', 'uv_px': t.px(t.W / 2, 1.0, t.ZF), 'xyz': ['W/2', '?yf', 'F'], 'fit': False})   # ?yf: no fitted corner uses it
        self.assertEqual(dc.unconstrained_checks(s), ['wall_edge'])
        fit = fit_complete(self.fit, n_check=2, check_points=[{'id': 'floor_bl', 'err_px': 2.0}, {'id': 'top_fr', 'err_px': 3.0}])
        spec, report = dc.compile_scene(s, self.image, self.dir / 'purpose', fit=fit)
        self.assertFalse([w for w in report['warnings'] if 'not listed' in w or 'refit' in w], report['warnings'])
        # the same corner flagged but constrained, and missing from the fit, is still a stale-fit warning
        s['corner_points'][-1]['xyz'] = ['W/2', 'D/2', 'F']
        self.assertEqual(dc.unconstrained_checks(s), [])
        spec, report = dc.compile_scene(s, self.image, self.dir / 'purpose_stale', fit=fit)
        self.assertTrue(any('wall_edge' in w and 'check_points' in w for w in report['warnings']), report['warnings'])
        # box_fit may also name what it skipped
        fit = fit_complete(self.fit, check_points=[], n_check=0, skipped_checks=['floor_bl'])
        spec, report = dc.compile_scene(scene_with_checks('floor_bl'), self.image, self.dir / 'purpose_named', fit=fit)
        self.assertFalse([w for w in report['warnings'] if 'not listed' in w], report['warnings'])

    def test_a_click_dropped_from_the_camera_is_a_held_out_landmark(self):
        fit = fit_complete(self.fit, outliers=['floor_br'], dropped=['floor_br'])
        spec, report = dc.compile_scene(t.scene(), self.image, self.dir / 'dropped', fit=fit)
        self.assertEqual({lm['id']: lm['fit'] for lm in spec['landmarks']},
                         {'floor_bl': True, 'floor_br': False, 'floor_fr': True, 'floor_fl': True})
        self.assertTrue(report['valid'])
        self.assertEqual(report['fit']['dropped'], ['floor_br'])
        self.assertIn('refitted without the clicks floor_br', spec['scale']['notes'])
        self.assertEqual(report['quality_gate']['status'], 'review')

    def test_unconstrained_checks_follow_boxfits_rule(self):
        s = t.scene()
        for p in s['corner_points']:
            p['fit'] = None
        self.assertEqual(dc.unconstrained_checks(s), [])
        by_id = {p['id']: p for p in s['corner_points']}
        by_id['top_fl']['fit'] = False
        self.assertEqual(dc.unconstrained_checks(s), [])                         # H is known
        s['known']['H'] = None
        self.assertEqual(dc.unconstrained_checks(s), [])                         # top_fr still fixes H
        by_id['top_fr']['fit'] = False
        self.assertEqual(sorted(dc.unconstrained_checks(s)), ['top_fl', 'top_fr'])      # nothing fitted fixes H any more
        by_id['top_fl']['xyz'] = ['bad token', 'D/2', 'H']
        self.assertEqual(dc.unconstrained_checks(s), ['top_fr'])                 # a bad token is box_fit's error to report, not ours

    def test_a_check_corner_outside_the_canvas_is_skipped_like_any_other_landmark(self):
        s = scene_with_checks('floor_bl')
        s['corner_points'][0]['uv_px'] = [-30.0, 300.0]
        spec, report = dc.compile_scene(s, self.image, self.dir / 'outside', fit=fit_complete(self.fit))
        self.assertNotIn('floor_bl', {lm['id'] for lm in spec['landmarks']})
        self.assertTrue(report['valid'])


class GateInTheReport(t.Base):
    def compile_with(self, fit, name, s=None):
        return dc.compile_scene(s or scene_with_checks('floor_bl', 'top_fr'), self.image, self.dir / name, fit=fit)

    def test_numbers_are_copied_and_the_gate_passes(self):
        fit = fit_complete(self.fit)
        spec, report = self.compile_with(fit, 'gate_pass')
        for key in ('status', 'loss', 'n_fit', 'n_check', 'check_points', 'check_median_px', 'check_max_px', 'loo_reliable', 'loo_errors_px',
                    'loo_median_px', 'loo_max_px', 'outliers', 'scale_checks', 'limits'):
            self.assertEqual(report['fit'][key], fit[key], key)
        self.assertEqual(report['quality_gate'], {'status': 'pass', 'reasons': []})
        written = json.loads((self.dir / 'gate_pass' / 'compile_report.json').read_text(encoding='utf-8'))
        self.assertEqual(written['quality_gate'], {'status': 'pass', 'reasons': []})
        self.assertEqual(written['fit']['n_fit'], 8)
        self.assertEqual(written['fit']['limits'], LIMITS)
        self.assertIn('Independent checks: 2 check corners off by 2.8 px', spec['scale']['notes'])

    def test_a_clean_leave_one_out_alone_passes_in_a_compile(self):
        spec, report = self.compile_with(fit_complete(self.fit, **NO_CHECKS), 'gate_loo_only', t.scene())
        self.assertEqual(report['quality_gate'], {'status': 'pass', 'reasons': []})
        self.assertIn('Independent checks: leave-one-out error 2.0 px', spec['scale']['notes'])

    def test_no_independent_signal_is_a_review_in_a_compile(self):
        spec, report = self.compile_with(fit_complete(self.fit, **NO_CHECKS, **NO_LOO), 'gate_nothing', t.scene())
        self.assertTrue(report['valid'])
        self.assertEqual(report['quality_gate']['status'], 'review')
        self.assertIn('no independent check', report['quality_gate']['reasons'][0])
        self.assertIn('No independent check', spec['scale']['notes'])
        self.assertFalse(report['fit']['loo_reliable'])

    def test_a_review_carries_its_reasons_but_still_compiles(self):
        spec, report = self.compile_with(fit_complete(self.fit, status='check_failed', check_median_px=14.0, check_max_px=19.0,
                                                      outliers=['top_fl']), 'gate_review')
        self.assertTrue(report['valid'])
        self.assertEqual(report['status'], 'ok')
        self.assertEqual(report['quality_gate']['status'], 'review')
        self.assertGreaterEqual(len(report['quality_gate']['reasons']), 2)
        self.assertIn('top_fl', ' '.join(report['quality_gate']['reasons']))

    def test_a_failed_size_check_reaches_the_report_and_the_gate(self):
        failed = [{'id': 'table', 'expected_m': 0.74, 'measured_m': 1.18, 'ratio': 1.59, 'ok': False}]
        spec, report = self.compile_with(fit_complete(self.fit, scale_checks=failed), 'gate_scale')
        self.assertEqual(report['fit']['scale_checks'], failed)
        self.assertEqual(report['quality_gate']['status'], 'review')
        self.assertTrue(any(r.startswith('a size check failed: table') for r in report['quality_gate']['reasons']), report['quality_gate'])
        s = scene_with_checks('floor_bl', 'top_fr')                              # results in a given fit are not "not evaluated"
        s['scale_checks'] = [{'id': 'table', 'base_px': t.px(1.0, -1.2, t.ZF), 'top_px': t.px(1.0, -1.2, t.ZF + 0.74),
                              'expected_height_m': 0.74, 'tolerance': None}]
        spec, report = self.compile_with(fit_complete(self.fit, scale_checks=failed), 'gate_scale_done', s)
        self.assertFalse([w for w in report['warnings'] if 'not evaluated' in w], report['warnings'])
        spec, report = self.compile_with(fit_complete(self.fit), 'gate_scale_missing', s)
        self.assertTrue([w for w in report['warnings'] if 'not evaluated' in w], report['warnings'])

    def test_a_fit_without_the_new_keys_gives_nulls_and_a_review(self):
        legacy = legacy_fit(self.fit)
        spec, report = self.compile_with(legacy, 'gate_legacy', t.scene())
        self.assertTrue(report['valid'])
        for key in ('n_fit', 'n_check', 'check_median_px', 'loo_median_px', 'outliers', 'loss', 'scale_checks', 'limits', 'loo_reliable', 'hint'):
            self.assertIsNone(report['fit'][key], key)
        self.assertEqual(report['quality_gate']['status'], 'review')
        self.assertIn('no independent check', report['quality_gate']['reasons'][0])
        self.assertIn('No independent check', spec['scale']['notes'])

    def test_the_ambiguity_hint_and_the_reclick_hint_stay_apart(self):
        reclick = 'Re-click or remove the clicks named above.'
        spec, report = self.compile_with(fit_complete(self.fit, status='check_failed', ambiguity_hint=None, hint=reclick,
                                                      outliers=['top_fl']), 'gate_hint_reclick')
        self.assertIsNone(report['fit']['ambiguity_hint'])
        self.assertEqual(report['fit']['hint'], reclick)
        self.assertIn('fit: ' + reclick, report['warnings'])
        add = 'Add a point whose z is numeric.'
        spec, report = self.compile_with(fit_complete(self.fit, status='ambiguous', ambiguous_dims=['H'], ambiguity_hint=add, hint=add),
                                         'gate_hint_amb')
        self.assertEqual((report['fit']['ambiguity_hint'], report['fit']['hint']), (add, add))
        self.assertEqual(report['warnings'].count('fit: ' + add), 1)             # said once, not twice
        self.assertIn('(H)', ' '.join(report['quality_gate']['reasons']))

    def test_a_fit_that_only_has_the_old_hint_key_still_gives_the_ambiguity_hint(self):
        fit = fit_complete(self.fit, status='ambiguous', ambiguous_dims=['H'], hint='Add a point whose z is numeric.')
        fit.pop('ambiguity_hint', None)
        spec, report = self.compile_with(fit, 'gate_hint_old')
        self.assertEqual(report['fit']['ambiguity_hint'], 'Add a point whose z is numeric.')
        fit = fit_complete(self.fit, status='fitted', hint='something else')
        fit.pop('ambiguity_hint', None)
        spec, report = self.compile_with(fit, 'gate_hint_other')
        self.assertIsNone(report['fit']['ambiguity_hint'])

    def test_the_real_fit_with_check_corners_reports_whatever_it_can(self):
        s = scene_with_checks('floor_bl', 'top_fr')
        spec, report = dc.compile_scene(s, self.image, self.dir / 'gate_real')
        self.assertTrue(report['valid'])
        self.assertEqual(report['quality_gate'], dc.quality_gate(report['fit']))
        self.assertEqual(report['fit']['status'], 'fitted')
        self.assertEqual([lm['fit'] for lm in spec['landmarks'] if lm['id'] in ('floor_bl',)], [False])
        if report['fit']['n_check'] is not None:                 # a box_fit that knows check corners (fit=false) must have used them as such
            self.assertEqual(report['fit']['n_check'], 2)
            self.assertEqual(sorted(c['id'] for c in report['fit']['check_points']), ['floor_bl', 'top_fr'])
            self.assertLess(report['fit']['check_median_px'], 1.0)          # exact synthetic corners predict each other
            self.assertEqual(report['quality_gate']['status'], 'pass', report['quality_gate'])

    def test_the_real_fit_without_check_corners_is_judged_by_leave_one_out(self):
        spec, report = dc.compile_scene(scene_with_checks(), self.image, self.dir / 'gate_real_loo')       # eight corners, all in the fit
        fit = report['fit']
        self.assertTrue(report['valid'])
        self.assertEqual(report['quality_gate'], dc.quality_gate(fit))
        self.assertFalse([lm for lm in spec['landmarks'] if not lm['fit']])
        if fit.get('loo_reliable') is not None:                  # a box_fit that says whether leave-one-out can be trusted
            self.assertTrue(fit['loo_reliable'])                 # eight exact corners, seven unknowns: plenty of spare equations
            self.assertEqual(fit['outliers'], [])
            self.assertEqual(report['quality_gate'], {'status': 'pass', 'reasons': []})


class OverlayTests(unittest.TestCase):
    """overlay.py on a fit written by hand: check corners look different and stay out of the fit's maximum."""
    FITTED, CHECK, RING = (30, 40), (90, 100), (30, 40)

    @staticmethod
    def fit(**kw):
        points = [{'id': 'a', 'uv_px': [30, 40], 'fitted_px': [32, 41], 'err_px': 2.2, 'fit': True},
                  {'id': 'b', 'uv_px': [90, 100], 'fitted_px': [100, 110], 'err_px': 14.1, 'fit': False},
                  {'id': 'c', 'uv_px': [150, 60], 'fitted_px': [151, 60], 'err_px': 1.0}]           # no key: an older box_fit, a fitted corner
        out = {'camera': t.CAM, 'image': {'width': 200, 'height': 120}, 'dims_m': {'W': 6.0, 'D': 5.0, 'H': 3.0}, 'floor_z': 0.0,
               'status': 'fitted', 'rms_px': 1.7, 'points': points}
        out.update(kw)
        return out

    def draw(self, fit):
        from PIL import Image
        import overlay
        with tempfile.TemporaryDirectory() as d:
            src, out = Path(d) / 's.png', Path(d) / 'o.png'
            Image.new('RGB', (200, 120), (90, 90, 90)).save(src)
            summary = overlay.draw_overlay(fit, src, out)
            return summary, Image.open(out).convert('RGB')

    def test_check_corners_are_orange_squares_and_stay_out_of_the_fit_maximum(self):
        import overlay
        summary, im = self.draw(self.fit())
        self.assertEqual(summary['max_point_err_px'], 2.2)                       # the fitted corners only
        self.assertEqual((summary['check_points'], summary['max_check_err_px']), (1, 14.1))
        self.assertEqual(im.getpixel((95, 100)), overlay.CHECK_COLOR)           # the square around the clicked check corner
        self.assertEqual(im.getpixel((34, 40)), (0, 255, 0))                     # a fitted corner keeps its green circle
        self.assertNotEqual(im.getpixel((94, 100)), (0, 255, 0))

    def test_without_check_corners_nothing_changes(self):
        fit = self.fit()
        fit['points'] = [p for p in fit['points'] if p['id'] != 'b']
        summary, _ = self.draw(fit)
        self.assertEqual(summary['max_point_err_px'], 2.2)
        self.assertEqual((summary['check_points'], summary['max_check_err_px']), (0, None))
        only_checks = self.fit()
        only_checks['points'] = [p for p in only_checks['points'] if p['id'] == 'b']
        summary, _ = self.draw(only_checks)
        self.assertEqual((summary['max_point_err_px'], summary['max_check_err_px']), (None, 14.1))

    def test_named_outliers_get_a_ring_unless_leave_one_out_is_unreliable(self):
        import overlay
        _, plain = self.draw(self.fit())
        self.assertNotEqual(plain.getpixel((39, 40)), overlay.OUTLIER_COLOR)
        _, ringed = self.draw(self.fit(outliers=['a'], loo_reliable=True))
        self.assertEqual(ringed.getpixel((39, 40)), overlay.OUTLIER_COLOR)
        _, unsure = self.draw(self.fit(outliers=['a'], loo_reliable=False))
        self.assertNotEqual(unsure.getpixel((39, 40)), overlay.OUTLIER_COLOR)


class CommandLine(t.Base):
    def test_boothctl_compile_prints_the_gate(self):
        scene_file, fit_file = self.dir / 'gate_scene.json', self.dir / 'gate_fit.json'
        scene_file.write_text(json.dumps(scene_with_checks('floor_bl', 'top_fr')), encoding='utf-8')
        for name, fit in (('pass', fit_complete(self.fit)), ('review', fit_complete(self.fit, status='high_residual', rms_px=9.0))):
            fit_file.write_text(json.dumps(fit), encoding='utf-8')
            out = self.dir / ('cli_gate_' + name)
            r = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'boothctl.py'), 'compile', '--scene', str(scene_file), '--image', str(self.image),
                                '--out', str(out), '--fit', str(fit_file)], capture_output=True, text=True, check=False)
            self.assertEqual(r.returncode, 0, r.stderr)
            summary = json.loads(r.stdout)
            self.assertEqual(summary['quality_gate']['status'], name)
            self.assertEqual(summary['fit_check']['n_check'], 2)
            self.assertEqual(summary['fit_check']['loo_median_px'], 2.0)
            self.assertIs(summary['fit_check']['loo_reliable'], True)
            self.assertEqual(summary['fit_check']['outliers'], [])
            report = json.loads((out / 'compile_report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['quality_gate'], summary['quality_gate'])
        self.assertTrue(summary['quality_gate']['reasons'])


if __name__ == '__main__':
    unittest.main()
