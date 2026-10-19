"""The compile step reports the engine's quality check and the numbers behind it, and the server never acts on it.

The one-click chain stops before the render in the page (studio.js, decided by studio_logic.js nextChain and tested in
studio_logic.test.mjs); the server only passes the verdict on. These tests run without Blender.

    python -m unittest discover -s web/tests -p "test_*.py"        (from the repository root)
"""
from __future__ import annotations
import json
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / 'web' / 'server'))

import scene_fixture as fixtures                    # noqa: E402
import steps                                        # noqa: E402
from test_server import StudioCase                  # noqa: E402


class CompileReportsTheQualityCheck(StudioCase):
    def compile(self, scene):
        job = self.new_job()
        self.assertEqual(self.api.call('PUT', f'/api/jobs/{job}/scene', scene)[0], 200)
        self.assertEqual(self.api.call('POST', f'/api/jobs/{job}/compile', {})[0], 202)
        st = self.wait(job)
        self.assertEqual(st['steps']['compile']['state'], 'done', st['steps']['compile'])
        return job, st

    def report_on_disk(self, job):
        return json.loads((self.studio.jobs.path(job) / 'compile' / 'compile_report.json').read_text(encoding='utf-8'))

    def test_a_good_reading_passes_with_every_corner_fitted_and_the_clicks_checked_by_leaving_one_out(self):
        job, st = self.compile(fixtures.scene())
        detail = st['steps']['compile']['detail']
        self.assertEqual(detail['quality_gate'], {'status': 'pass', 'reasons': []})
        self.assertEqual(detail['quality_gate'], self.report_on_disk(job)['quality_gate'])          # passed on unchanged
        fit = detail['fit']
        for key in steps.FIT_KEYS:
            self.assertIn(key, fit)
        self.assertEqual((fit['status'], fit['n_fit'], fit['n_check']), ('fitted', len(fixtures.CORNERS), 0))     # nothing is withheld
        self.assertEqual(fit['check_points'], [])
        self.assertIs(fit['loo_reliable'], True)                                                     # 8 corners and one assumed size are enough to leave one out
        self.assertIsNotNone(fit['loo_median_px'])
        self.assertLess(fit['loo_max_px'], 2)
        self.assertEqual(fit['outliers'], [])
        self.assertEqual(fit['scale_checks'], [])                                                    # none were asked for in this picture
        self.assertIsInstance(fit['limits'], dict)
        self.assertEqual(len(detail['points']), len(fixtures.CORNERS))
        self.assertFalse(any(p['check'] for p in detail['points']))

    def test_a_scale_check_that_agrees_is_passed_on_and_one_that_does_not_sends_the_model_to_review(self):
        _, st = self.compile(fixtures.scale_check_scene())
        detail = st['steps']['compile']['detail']
        (check,) = detail['fit']['scale_checks']
        self.assertEqual((check['id'], check['ok']), ('table_height', True))
        self.assertAlmostEqual(check['measured_m'], 0.74, delta=0.01)
        self.assertEqual(detail['quality_gate']['status'], 'pass')
        _, st = self.compile(fixtures.scale_check_scene(expected_height_m=1.5))                      # the assumed size cannot be right if a table is 0.74 m
        detail = st['steps']['compile']['detail']
        (check,) = detail['fit']['scale_checks']
        self.assertIs(check['ok'], False)
        self.assertAlmostEqual(check['ratio'], 0.49, delta=0.02)
        self.assertEqual(detail['fit']['status'], 'fitted')                                          # the corners are fine; only the scale is doubtful
        self.assertEqual(detail['quality_gate']['status'], 'review')
        self.assertTrue(any('table_height' in r for r in detail['quality_gate']['reasons']))

    def test_corners_held_out_by_hand_are_still_supported_and_named(self):
        job, st = self.compile(fixtures.held_out_scene())
        detail = st['steps']['compile']['detail']
        fit = detail['fit']
        self.assertEqual((fit['status'], fit['n_fit'], fit['n_check']), ('fitted', len(fixtures.CORNERS) - 2, 2))
        self.assertEqual(sorted(c['id'] for c in fit['check_points']), sorted(fixtures.HELD_OUT_IDS))
        self.assertLess(fit['check_max_px'], 2)
        self.assertEqual(detail['quality_gate']['status'], 'pass')                                   # they are an independent check on their own
        self.assertEqual({p['id'] for p in detail['points'] if p['check']}, set(fixtures.HELD_OUT_IDS))   # the table can say which corners were only checks
        self.assertIsNone(fit['loo_median_px'])                                                      # 6 fitted corners: leaving one out is not reliable, so it is not reported ...
        self.assertIs(fit['loo_reliable'], False)
        self.assertEqual(fit['outliers'], [])                                                        # ... and no corner is named

    def test_a_corner_far_off_says_review_and_the_job_is_still_fully_usable(self):
        job, st = self.compile(fixtures.review_scene())
        detail = st['steps']['compile']['detail']
        gate = detail['quality_gate']
        self.assertEqual(gate['status'], 'review')
        self.assertTrue(gate['reasons'] and all(isinstance(r, str) and r for r in gate['reasons']))
        self.assertIn(detail['fit']['status'], ('high_residual', 'check_failed'))
        self.assertIn(fixtures.MISCLICKED_ID, detail['fit']['outliers'])                             # the corner clicked in the wrong place is named
        self.assertEqual((detail['status'], detail['valid']), ('ok', True))                         # the model itself was built
        self.assertEqual(st['spec']['origin'], 'compiled')
        self.assertEqual(self.api.call('GET', f'/api/jobs/{job}/spec')[0], 200)

    def test_the_server_never_blocks_on_the_verdict(self):
        job = self.new_job()
        self.assertEqual(self.api.call('PUT', f'/api/jobs/{job}/scene', fixtures.review_scene())[0], 200)
        status, body = self.api.call('POST', f'/api/jobs/{job}/run', {'steps': ['compile', 'glb']})        # the chain a page may ask for
        self.assertEqual((status, body['started']), (202, ['compile', 'glb']))
        st = self.wait(job)
        self.assertEqual(st['steps']['compile']['detail']['quality_gate']['status'], 'review')
        self.assertEqual([st['steps'][s]['state'] for s in ('compile', 'glb')], ['done', 'done'])       # the step after the review verdict ran

    def test_a_scene_file_from_before_the_check_flags_still_uploads_and_compiles(self):
        old = fixtures.old_scene()
        self.assertTrue(all('fit' not in p for p in old['corner_points']))
        job, st = self.compile(old)
        detail = st['steps']['compile']['detail']
        self.assertEqual(detail['fit']['n_check'], 0)                                                # nothing was marked as held out
        self.assertEqual(detail['fit']['check_points'], [])
        self.assertEqual(detail['quality_gate']['status'], 'pass')                                   # the same clicks as the good reading: the same verdict
        self.assertEqual(sum(1 for p in detail['points'] if p['check']), 0)
        self.assertEqual(json.loads((self.studio.jobs.path(job) / 'scene.json').read_text(encoding='utf-8'))['schema_version'], 'scene-description.v1')


class TheMockServesOneReviewScene(unittest.TestCase):
    def test_the_stand_in_service_writes_the_fixtures_review_scene(self):
        from mock_openai import MockOpenAI
        scene = fixtures.scene()
        mock = MockOpenAI(scene, ['review'])
        self.addCleanup(mock.server.server_close)
        written = json.loads(mock.text_for('review'))
        self.assertEqual(written, fixtures.review_scene())                                           # one definition of that reading
        self.assertEqual(json.loads(mock.text_for('good')), scene)                                   # and the good one is untouched by it
        self.assertEqual(scene, fixtures.scene())
        moved = [p['id'] for p, q in zip(scene['corner_points'], written['corner_points']) if p['uv_px'] != q['uv_px']]
        self.assertEqual(moved, [fixtures.MISCLICKED_ID])


class TheVerdictHelper(unittest.TestCase):
    def test_the_report_wins_then_the_report_file_then_nothing(self):
        gate = {'status': 'review', 'reasons': ['why']}
        with tempfile.TemporaryDirectory() as t:
            file = Path(t) / 'compile_report.json'
            self.assertIsNone(steps._quality_gate({}, file))                                         # an engine without the verdict, no file
            file.write_text(json.dumps({'quality_gate': {'status': 'pass', 'reasons': []}}), encoding='utf-8')
            self.assertEqual(steps._quality_gate({}, file), {'status': 'pass', 'reasons': []})        # the file carries it
            self.assertEqual(steps._quality_gate({'quality_gate': gate}, file), gate)                # the report in hand is the first source
            file.write_text('{broken', encoding='utf-8')
            self.assertIsNone(steps._quality_gate({}, file))                                         # a damaged file is no verdict, not a crash
            file.write_text(json.dumps({'quality_gate': 'pass'}), encoding='utf-8')
            self.assertIsNone(steps._quality_gate({}, file))                                         # only an object is a verdict


class TheFitKeys(unittest.TestCase):
    def test_the_compile_detail_carries_what_the_page_words_the_verdict_from(self):
        for key in ('status', 'n_fit', 'n_check', 'check_points', 'check_median_px', 'check_max_px', 'loo_reliable', 'loo_errors_px',
                    'loo_median_px', 'loo_max_px', 'outliers', 'scale_checks', 'hint', 'limits', 'ambiguity_hint', 'worst_points'):
            self.assertIn(key, steps.FIT_KEYS)
        self.assertEqual(len(steps.FIT_KEYS), len(set(steps.FIT_KEYS)))


if __name__ == '__main__':
    unittest.main()
