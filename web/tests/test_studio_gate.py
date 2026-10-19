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
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / 'web' / 'server'))

import engine                                       # noqa: E402
import scene_fixture as fixtures                    # noqa: E402
import steps                                        # noqa: E402
from test_server import StudioCase                  # noqa: E402


class Compiles(StudioCase):
    """A job per scene description: upload it, compile it, and read what the compile step reported and wrote."""

    def compile(self, scene):
        job = self.new_job()
        self.assertEqual(self.api.call('PUT', f'/api/jobs/{job}/scene', scene)[0], 200)
        self.assertEqual(self.api.call('POST', f'/api/jobs/{job}/compile', {})[0], 202)
        st = self.wait(job)
        self.assertEqual(st['steps']['compile']['state'], 'done', st['steps']['compile'])
        return job, st

    def report_on_disk(self, job):
        return json.loads((self.studio.jobs.path(job) / 'compile' / 'compile_report.json').read_text(encoding='utf-8'))


class CompileReportsTheQualityCheck(Compiles):
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


LAYOUT_CHECKS = {'type_matches_sides', 'wall_on_open_side', 'closed_side_without_wall', 'camera_side'}


class CompileReportsTheBoothLayout(Compiles):
    """The engine checks what the reading said about the open sides against the corner fit and the parts (these tests run the real
    engine). The gate says review when a check fails, and the server still never blocks."""

    @staticmethod
    def statuses(detail):
        return {c['id']: c['status'] for c in detail['topology']['checks']}

    def spec_ids(self, job):
        status, spec = self.api.call('GET', f'/api/jobs/{job}/spec')
        self.assertEqual(status, 200)
        return [c['id'] for c in spec['components']]

    def test_a_reading_that_agrees_with_its_corners_and_parts_passes_every_check(self):
        job, st = self.compile(fixtures.scene())
        detail = st['steps']['compile']['detail']
        topology = detail['topology']
        self.assertEqual(topology, self.report_on_disk(job)['topology'])                          # passed on unchanged
        self.assertEqual(set(topology), {'booth_type', 'open_sides', 'cutaway_sides', 'checks'})
        self.assertEqual((topology['booth_type'], topology['open_sides'], topology['cutaway_sides']), ('peninsula', ['front', 'left', 'right'], []))
        self.assertEqual({c['id'] for c in topology['checks']}, LAYOUT_CHECKS)
        self.assertTrue(all(set(c) >= {'id', 'status', 'message'} and c['message'] for c in topology['checks']))
        statuses = self.statuses(detail)
        self.assertEqual([statuses[k] for k in ('type_matches_sides', 'wall_on_open_side', 'camera_side')], ['ok'] * 3)
        self.assertIn(statuses['closed_side_without_wall'], ('ok', 'warn'))                        # information only
        self.assertEqual(detail['quality_gate'], {'status': 'pass', 'reasons': []})
        self.assertEqual(detail['dropped'], [])

    def test_a_type_the_open_sides_cannot_make_says_review(self):
        _, st = self.compile(fixtures.topology_scene('type'))
        detail = st['steps']['compile']['detail']
        self.assertEqual(self.statuses(detail)['type_matches_sides'], 'fail')
        self.assertEqual(detail['quality_gate']['status'], 'review')
        self.assertTrue(any('booth type does not match the open sides' in r for r in detail['quality_gate']['reasons']), detail['quality_gate'])
        self.assertEqual(detail['fit']['status'], 'fitted')                                         # the corners are fine; only the layout is doubtful
        self.assertEqual(detail['topology']['booth_type'], 'inline')                                # what the reading said travels with the verdict

    def test_a_wall_on_an_open_side_is_left_out_and_says_review(self):
        job, st = self.compile(fixtures.topology_scene('wall'))
        detail = st['steps']['compile']['detail']
        self.assertEqual(self.statuses(detail)['wall_on_open_side'], 'fail')
        self.assertEqual(self.statuses(detail)['type_matches_sides'], 'ok')                         # four open sides do make an island
        self.assertEqual(detail['quality_gate']['status'], 'review')
        self.assertTrue(any('wall was placed on an open side and left out' in r for r in detail['quality_gate']['reasons']), detail['quality_gate'])
        self.assertIn({'id': 'back_wall', 'reason': 'wall on an open side'}, detail['dropped'])
        self.assertFalse([i for i in self.spec_ids(job) if i.startswith('back_wall')])             # no wall on an open side in the model
        self.assertEqual((detail['status'], detail['valid']), ('partial', True))                     # a part was left out, and what is left is a valid model

    def test_a_cutaway_side_is_not_an_open_side(self):
        """A side whose wall is only left out of the picture: a wall on it is allowed, it is not counted in the type, and the camera may
        look in through it."""
        job, st = self.compile(fixtures.with_layout(fixtures.scene(), 'inline', ['front'], ['back']))     # the wall's side is cut away
        detail = st['steps']['compile']['detail']
        statuses = self.statuses(detail)
        self.assertEqual((statuses['wall_on_open_side'], statuses['type_matches_sides'], statuses['camera_side']), ('ok', 'ok', 'ok'))
        self.assertEqual(detail['dropped'], [])
        self.assertTrue([i for i in self.spec_ids(job) if i.startswith('back_wall')])                      # the wall is in the model
        self.assertEqual(detail['quality_gate'], {'status': 'pass', 'reasons': []})                        # closed sides without a wall are information only
        _, st = self.compile(fixtures.with_layout(fixtures.scene(), 'other', [], ['front']))               # no real opening: the camera looks in through the cutaway
        detail = st['steps']['compile']['detail']
        self.assertEqual(self.statuses(detail)['camera_side'], 'ok')
        self.assertEqual(detail['quality_gate']['status'], 'pass')

    def test_a_camera_that_only_sees_closed_sides_says_the_picture_may_be_mirrored(self):
        job, st = self.compile(fixtures.topology_scene('camera'))
        detail = st['steps']['compile']['detail']
        statuses = self.statuses(detail)
        self.assertEqual((statuses['camera_side'], statuses['type_matches_sides'], statuses['wall_on_open_side']), ('fail', 'ok', 'ok'))
        self.assertEqual(detail['quality_gate']['status'], 'review')
        self.assertTrue(any('front/back or left/right may be swapped' in r for r in detail['quality_gate']['reasons']), detail['quality_gate'])
        self.assertEqual(detail['fit']['status'], 'fitted')                                         # a consistent swap cannot be seen in the fit: that is the point
        self.assertEqual((detail['status'], detail['valid']), ('ok', True))                         # the model is complete and usable
        self.assertEqual(st['spec']['origin'], 'compiled')

    def test_the_server_never_blocks_on_a_layout_verdict(self):
        job = self.new_job()
        self.assertEqual(self.api.call('PUT', f'/api/jobs/{job}/scene', fixtures.topology_scene('camera'))[0], 200)
        status, body = self.api.call('POST', f'/api/jobs/{job}/run', {'steps': ['compile', 'glb']})
        self.assertEqual((status, body['started']), (202, ['compile', 'glb']))
        st = self.wait(job)
        self.assertEqual(st['steps']['compile']['detail']['quality_gate']['status'], 'review')
        self.assertEqual([st['steps'][s]['state'] for s in ('compile', 'glb')], ['done', 'done'])   # the step after the review verdict ran

    def test_a_file_without_the_layout_still_compiles_and_runs_no_layout_check(self):
        """A 1.1 file, as the previous reader wrote it (the file from before 1.1 is in CompileReportsTheQualityCheck)."""
        with_layout = self.spec_ids(self.compile(fixtures.scene())[0])
        job, st = self.compile(fixtures.v11_scene())
        detail = st['steps']['compile']['detail']
        self.assertEqual(detail['quality_gate'], {'status': 'pass', 'reasons': []})                 # the same clicks, the same verdict
        topology = detail['topology']
        self.assertEqual((topology['booth_type'], topology['open_sides'], topology['cutaway_sides']), (None, None, None))
        self.assertEqual(set(self.statuses(detail).values()), {'not_checked'})                       # nothing stated: nothing was checked
        self.assertEqual(self.spec_ids(job), with_layout)                                            # and the model has the parts the same reading with a layout makes


class TheLayoutIsPassedOnWhateverTheEngineSays(Compiles):
    """The compile step passes the engine's layout through and words nothing itself. A stand-in for the engine's report, so these tests
    do not depend on which engine is installed."""
    FAKE = {'booth_type': 'inline', 'open_sides': ['front'], 'cutaway_sides': [],
            'checks': [{'id': 'type_matches_sides', 'status': 'ok', 'message': 'one open side'},
                       {'id': 'camera_side', 'status': 'fail', 'message': 'the camera is outside the closed sides only'}]}

    def compile_with(self, change):
        real = engine.compile_scene

        def patched(scene, image, out):
            spec, report = real(scene, image, out)
            return spec, change(dict(report))
        with mock.patch.object(engine, 'compile_scene', patched):
            return self.compile(fixtures.v11_scene())                       # valid for any engine; the stand-in adds what a newer one reports

    def test_the_report_is_passed_on_unchanged(self):
        gate = {'status': 'review', 'reasons': ['the camera looks in through closed sides: front/back or left/right may be swapped']}
        _, st = self.compile_with(lambda report: {**report, 'topology': self.FAKE, 'quality_gate': gate})
        detail = st['steps']['compile']['detail']
        self.assertEqual(detail['topology'], self.FAKE)
        self.assertEqual(detail['quality_gate'], gate)

    def test_no_layout_in_the_report_is_none_not_an_empty_one(self):
        _, st = self.compile_with(lambda report: {k: v for k, v in report.items() if k != 'topology'})
        self.assertIsNone(st['steps']['compile']['detail']['topology'])

    def test_only_an_object_is_a_layout(self):
        report = {'topology': self.FAKE}
        self.assertIs(steps._topology(report), self.FAKE)
        for report in ({}, {'topology': None}, {'topology': []}, {'topology': 'x'}):
            self.assertIsNone(steps._topology(report))


class ThePageWordsEveryCheckThatCanFail(unittest.TestCase):
    """studio_logic.js names the engine's layout checks by id. A check the engine can fail without a plain sentence in the page would
    fall back to the generic line, so the ids are pinned across the two languages."""

    def test_the_ids_the_page_knows_are_the_ids_that_can_send_the_gate_to_review(self):
        import re
        import draft_compile
        js = REPO / 'web' / 'app' / 'js'
        block = re.search(r'const TOPOLOGY_NOTES = \{(.*?)\};', (js / 'studio_logic.js').read_text(encoding='utf-8'), re.S).group(1)
        words = dict(re.findall(r"(\w+):\s*'(\w+)'", block))
        self.assertEqual(set(words), set(draft_compile.TOPOLOGY_REASONS))                       # the checks that add a reason to the gate
        self.assertEqual(set(words), set(draft_compile.TOPOLOGY_CHECKS) - {'closed_side_without_wall'})   # information only: never worded
        copy = (js / 'studio_copy.js').read_text(encoding='utf-8')
        for key in words.values():
            self.assertRegex(copy, rf'\b{key}:', key)


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
