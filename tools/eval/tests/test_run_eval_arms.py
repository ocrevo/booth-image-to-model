"""The arm switch (--check-corners), the scale-check option, stale outputs and the blank-picture write, against stand-in skills."""
import concurrent.futures
import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_eval
import stubskill
import synth_generate as sg


def run_main(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = run_eval.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


class Arms(unittest.TestCase):
    """Three small oracle runs against a stand-in skill, made once: the default arm, the zero arm and the scale-check option."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.skill = stubskill.make_stub_skill(cls.tmp / 'new', new=True)
        cls.arm2 = cls.oracle('arm2')
        cls.arm0 = cls.oracle('arm0', '--check-corners', '0')
        cls.sc = cls.oracle('sc', '--scale-check', perts=('noise_1',))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    @classmethod
    def oracle(cls, name, *extra, perts=('noise_1', 'wrong_anchor_m25')):
        out = cls.tmp / name
        code, _, err = run_main('oracle', '--skill-dir', str(cls.skill), '--suite', 'smoke', '--seed', '1', '--out', str(out), '--cases', '2',
                                '--perturbations', *perts, '--resamples', '20', *extra)
        assert code == 0, err
        return out, read(out / 'results.json')

    def fit_flags(self, out, row):
        scene = read(out / 'work' / 'runs' / row['case_id'] / row['pert'] / 'scene.json')
        points = read(out / 'work' / 'runs' / row['case_id'] / row['pert'] / 'points.json')
        return ({p['id'] for p in scene['corner_points'] if p.get('fit') is False}, {p['id'] for p in points['points'] if p.get('fit') is False})

    def test_default_arm_holds_the_case_check_corners_out(self):
        out, res = self.arm2
        self.assertEqual(res['meta']['check_corners'], 2)
        for row in res['rows']:
            case, _ = sg.build_case(1, int(row['case_id'][-4:]))
            self.assertEqual(self.fit_flags(out, row), (set(case['check_ids']), set(case['check_ids'])))
            self.assertEqual(row['check_corners_arm'], 2)
            self.assertEqual(row['n_withheld'], len(case['check_ids']))

    def test_arm_zero_sends_every_corner_as_a_fitted_one(self):
        out, res = self.arm0
        self.assertEqual(res['meta']['check_corners'], 0)
        self.assertIn('| Arm: corners held out of the fit (check corners) | 0 (every corner is fitted) |', (out / 'report.md').read_text(encoding='utf-8'))
        for row in res['rows']:
            self.assertEqual(self.fit_flags(out, row), (set(), set()))
            self.assertEqual((row['check_corners_arm'], row['n_withheld'], row['n_check']), (0, 0, 0))

    def test_the_two_arms_are_paired_on_identical_inputs(self):
        (out2, res2), (out0, res0) = self.arm2, self.arm0
        self.assertEqual([(r['case_id'], r['pert']) for r in res2['rows']], [(r['case_id'], r['pert']) for r in res0['rows']])
        for r in res2['rows']:
            f2 = out2 / 'work' / 'runs' / r['case_id'] / r['pert']
            f0 = out0 / 'work' / 'runs' / r['case_id'] / r['pert']
            strip = lambda doc: [{k: v for k, v in p.items() if k != 'fit'} for p in doc['points']]
            self.assertEqual(strip(read(f2 / 'points.json')), strip(read(f0 / 'points.json')))
            self.assertEqual(read(f2 / 'points.json')['known'], read(f0 / 'points.json')['known'])

    def test_rescore_keeps_the_arm(self):
        work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, work, True)
        copy = work / 'arm0'
        shutil.copytree(self.arm0[0], copy)
        code, _, _ = run_main('rescore', '--dir', str(copy), '--resamples', '20')
        self.assertEqual(code, 0)
        second = read(copy / 'results.json')
        self.assertEqual(second['meta']['check_corners'], 0)
        self.assertEqual(second['rows'], self.arm0[1]['rows'])

    def test_rescoring_a_run_from_before_the_switch_assumes_the_check_corners(self):
        work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, work, True)
        copy = work / 'old'
        shutil.copytree(self.arm2[0], copy)
        data = read(copy / 'results.json')
        for key in ('check_corners', 'scale_check', 'generator_version'):
            data['meta'].pop(key, None)
        (copy / 'results.json').write_text(json.dumps(data), encoding='utf-8')
        run_main('rescore', '--dir', str(copy), '--resamples', '20')
        meta = read(copy / 'results.json')['meta']
        self.assertEqual((meta['check_corners'], meta['scale_check'], meta['generator_version']), (2, False, sg.GENERATOR_VERSION))
        self.assertEqual(meta['harness_version'], run_eval.HARNESS_VERSION)
        self.assertEqual(read(copy / 'results.json')['rows'], self.arm2[1]['rows'])

    def test_scale_check_option_reaches_the_skill_input(self):
        out, res = self.sc
        self.assertTrue(res['meta']['scale_check'])
        for row in res['rows']:
            case, _ = sg.build_case(1, int(row['case_id'][-4:]))
            points = read(out / 'work' / 'runs' / row['case_id'] / row['pert'] / 'points.json')
            self.assertEqual('scale_checks' in points, any(p['type'] == 'round_table' for p in case['parts']))
        out2, res2 = self.arm2
        self.assertFalse(res2['meta']['scale_check'])
        self.assertTrue(all('scale_checks' not in read(out2 / 'work' / 'runs' / r['case_id'] / r['pert'] / 'points.json') for r in res2['rows']))

    def test_any_registered_perturbation_can_be_run_and_is_recorded(self):
        _out, res = self.arm2
        self.assertEqual(res['meta']['perturbations'], ['noise_1', 'wrong_anchor_m25'])
        self.assertEqual(res['meta']['generator_version'], sg.GENERATOR_VERSION)
        self.assertEqual({r['dims_mode'] for r in res['rows'] if r['pert'] == 'wrong_anchor_m25'}, {'wrong_one'})
        self.assertEqual(res['aggregate']['by_pert']['wrong_anchor_m25']['flag_on_wrong_anchor']['n'], 2)
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            run_main('oracle', '--skill-dir', str(self.skill), '--out', str(self.tmp / 'x'), '--perturbations', 'nope')


class StaleOutputsAndImages(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_clear_stale_removes_the_previous_runs_files(self):
        folder = self.tmp / 'run'
        (folder / 'compile').mkdir(parents=True)
        for name in run_eval.STALE:
            (folder / name).write_text('{}', encoding='utf-8')
        (folder / 'compile' / 'compile_report.json').write_text('{}', encoding='utf-8')
        run_eval.clear_stale(folder, ('fit', 'compile'))
        self.assertEqual(sorted(p.name for p in folder.iterdir()), [])
        # a compile-only call leaves a fit.json alone: it belongs to a different stage
        (folder / 'fit.json').write_text('{}', encoding='utf-8')
        run_eval.clear_stale(folder, ('compile',))
        self.assertTrue((folder / 'fit.json').is_file())

    def test_a_crashed_second_call_is_not_scored_from_the_first(self):
        good = stubskill.make_stub_skill(self.tmp / 'good', new=True)
        ctx = run_eval.Context(good, self.tmp / 'work', 12, ('fit', 'compile'))
        case, scene = sg.build_case(1, 0)
        import perturb
        pert = perturb.apply(case, 'noise_1')
        image = ctx.image(case['image']['width'], case['image']['height'])
        folder = ctx.work / 'runs' / case['case_id'] / 'noise_1'
        args = (folder, perturb.points_for_run(case, pert), perturb.scene_for_run(scene, case, pert), pert['fit_flags'], image)
        first = run_eval.run_skill(ctx, *args)
        self.assertEqual(first['fit']['status'], 'fitted')
        self.assertIsNotNone(first['report'])
        blocked = stubskill.make_stub_skill(self.tmp / 'blocked', new=True, mode='blocked')
        ctx2 = run_eval.Context(blocked, self.tmp / 'work', 12, ('fit', 'compile'))
        second = run_eval.run_skill(ctx2, *args)
        self.assertEqual(second['fit']['status'], 'blocked')                   # the new fit.json, not the old one
        self.assertIsNone(second['report'])                                    # the old compile report is gone
        self.assertIsNone(second['spec'])
        self.assertFalse((folder / 'compile').exists())

    def test_blank_pictures_are_written_atomically_under_parallel_workers(self):
        ctx = run_eval.Context(stubskill.make_stub_skill(self.tmp / 's', new=True), self.tmp / 'work')
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            paths = list(pool.map(lambda _: ctx.image(1600, 1000), range(24)))
        self.assertEqual(len(set(paths)), 1)
        with Image.open(paths[0]) as im:
            im.load()
            self.assertEqual(im.size, (1600, 1000))
        self.assertEqual([p.name for p in paths[0].parent.iterdir()], [paths[0].name])         # no temporary file is left behind


if __name__ == '__main__':
    unittest.main()
