import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_eval
import stubskill
import synth_generate as sg

ROOT = Path(__file__).resolve().parents[3]
SKILL = ROOT / 'booth-image-to-model'


def run_main(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = run_eval.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


class Plumbing(unittest.TestCase):
    """The runner against stand-in skills: what it writes for an old skill and for a new one, without the real engine."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def oracle(self, skill, name='run', perts=('noise_1', 'clean'), extra=()):
        out = self.tmp / name
        code, stdout, stderr = run_main('oracle', '--skill-dir', str(skill), '--suite', 'smoke', '--seed', '1', '--out', str(out), '--cases', '2',
                                        '--perturbations', *perts, '--resamples', '30', *extra)
        self.assertEqual(code, 0, stderr)
        return out, read(out / 'results.json')

    def test_old_skill_gets_the_old_formats(self):
        skill = stubskill.make_stub_skill(self.tmp / 'old', new=False)
        out, res = self.oracle(skill)
        self.assertEqual(len(res['rows']), 4)
        self.assertFalse(res['meta']['capabilities']['scene_fit_key'])
        for row in res['rows']:
            self.assertFalse(row['failed'])
            self.assertEqual(row['stdout_single_json'], 0.0)             # the old script prints a summary line and a camera line
            self.assertIsNone(row['gate_status'])
            self.assertFalse(row['flagged'])                              # fitted -> not flagged without a gate
            folder = out / 'work' / 'runs' / row['case_id'] / row['pert']
            scene, points = read(folder / 'scene.json'), read(folder / 'points.json')
            self.assertEqual(scene['schema_version'], 'scene-description.v1')
            self.assertTrue(all('fit' not in p for p in scene['corner_points']))
            self.assertTrue(all('fit' not in p for p in points['points']))
            self.assertIsNone(row['reported_outliers'])
            self.assertIsNone(row['outlier_recall'])

    def test_new_skill_gets_fit_flags_on_the_check_corners(self):
        skill = stubskill.make_stub_skill(self.tmp / 'new', new=True)
        out, res = self.oracle(skill)
        self.assertTrue(res['meta']['capabilities']['scene_fit_key'])
        case, _ = sg.build_case(1, 0)
        folder = out / 'work' / 'runs' / case['case_id'] / 'noise_1'
        scene, points = read(folder / 'scene.json'), read(folder / 'points.json')
        self.assertEqual(scene['schema_version'], '1.1')
        flags = {p['id']: p['fit'] for p in scene['corner_points']}
        self.assertEqual({k for k, v in flags.items() if v is False}, set(case['check_ids']))
        self.assertTrue(all(v in (False, None) for v in flags.values()))
        self.assertEqual({p['id'] for p in points['points'] if p.get('fit') is False}, set(case['check_ids']))
        for row in res['rows']:
            self.assertEqual(row['stdout_single_json'], 1.0)
            self.assertEqual(row['gate_status'], 'pass')
            self.assertEqual(row['flag_source'], 'quality_gate')
            self.assertEqual(row['reported_outliers'], [])
            self.assertEqual(row['fit_consistent'], 1.0)

    def test_outputs_a_report_with_intervals_and_the_command(self):
        skill = stubskill.make_stub_skill(self.tmp / 'new', new=True)
        out, res = self.oracle(skill, extra=('--markdown', str(self.tmp / 'copy.md')))
        text = (out / 'report.md').read_text(encoding='utf-8')
        self.assertEqual(text, (self.tmp / 'copy.md').read_text(encoding='utf-8'))
        for needle in ('## Headline', '## By perturbation: accuracy', 'noise_1', 'clean', '95% CI', 'Harness version', 'python tools/eval/run_eval.py oracle'):
            self.assertIn(needle, text)
        self.assertEqual(set(res['aggregate']['by_pert']), {'all', 'noise_1', 'clean'})
        self.assertNotIn('\r', text)

    def test_the_report_names_no_absolute_paths(self):
        skill = stubskill.make_stub_skill(self.tmp / 'new', new=True)
        out, res = self.oracle(skill)
        self.assertNotIn(str(self.tmp), (out / 'report.md').read_text(encoding='utf-8').split('| Command |')[0])
        self.assertFalse(Path(res['meta']['skill_label']).is_absolute())

    def test_a_skill_that_refuses_gives_failed_flagged_rows(self):
        skill = stubskill.make_stub_skill(self.tmp / 'blocked', new=True, mode='blocked')
        _out, res = self.oracle(skill, perts=('clean',))
        for row in res['rows']:
            self.assertTrue(row['failed'] and row['flagged'] and row['bad_fit'])
            self.assertIn('blocked', row['failure'])
            self.assertIsNone(row['corner_err_diag'])
        self.assertEqual(res['aggregate']['all']['failure_rate']['value'], 1.0)

    def test_resume_and_rescore_do_not_call_the_skill_again(self):
        skill = stubskill.make_stub_skill(self.tmp / 'new', new=True)
        out, first = self.oracle(skill, name='again')
        shutil.rmtree(skill / 'scripts')                                  # the skill is gone: only stored outputs can be used
        (skill / 'scripts').mkdir()
        code, _, _ = run_main('rescore', '--dir', str(out), '--resamples', '20')
        self.assertEqual(code, 0)
        second = read(out / 'results.json')
        self.assertTrue(second['meta']['rescored'])
        strip = lambda rows: [{k: v for k, v in r.items()} for r in rows]
        self.assertEqual(strip(second['rows']), strip(first['rows']))

    def test_replay_scores_saved_replies_against_the_truth(self):
        skill = stubskill.make_stub_skill(self.tmp / 'new', new=True)
        cases = self.tmp / 'cases'
        sg.write_suite('smoke', 1, cases, 2)
        replies = self.tmp / 'replies'
        for name in ('synth_0000', 'synth_0001'):
            (replies / name).mkdir(parents=True)
            shutil.copyfile(cases / name / 'scene.json', replies / name / 'scene.json')
        scene = read(replies / 'synth_0001' / 'scene.json')
        scene['known']['W'], scene['assumed'] = 6.0, []
        (replies / 'synth_0001' / 'scene.json').write_text(json.dumps(scene), encoding='utf-8')
        out = self.tmp / 'replay'
        code, _, err = run_main('replay', '--skill-dir', str(skill), '--replies', str(replies), '--cases', str(cases), '--out', str(out), '--resamples', '20')
        self.assertEqual(code, 0, err)
        rows = read(out / 'results.json')['rows']
        self.assertEqual([r['case_id'] for r in rows], ['synth_0000', 'synth_0001'])
        self.assertTrue(all(r['truth'] and not r['failed'] for r in rows))

    def test_replay_without_truth_lists_outcomes_only(self):
        skill = stubskill.make_stub_skill(self.tmp / 'new', new=True)
        cases = self.tmp / 'cases'
        sg.write_suite('smoke', 1, cases, 1)
        (self.tmp / 'replies' / 'synth_0000').mkdir(parents=True)
        shutil.copyfile(cases / 'synth_0000' / 'scene.json', self.tmp / 'replies' / 'synth_0000' / 'scene.json')
        out = self.tmp / 'replay2'
        code, _, err = run_main('replay', '--skill-dir', str(skill), '--replies', str(self.tmp / 'replies'), '--out', str(out))
        self.assertEqual(code, 0, err)
        (row,) = read(out / 'results.json')['rows']
        self.assertFalse(row['truth'])
        self.assertEqual(row['gate_status'], 'pass')
        self.assertIn('no ground truth', (out / 'report.md').read_text(encoding='utf-8'))

    def test_live_mode_is_not_implemented_and_says_why(self):
        code, out, err = run_main('live')
        self.assertEqual(code, 2)
        self.assertIn("owner's go-ahead", err)
        self.assertIn('API key', err)
        self.assertIn('cost', err)
        self.assertEqual(out, '')


class SmokeAgainstTheRepositorySkill(unittest.TestCase):
    """Two scenes through the real box_fit.py and `boothctl compile` of this repository's skill (no Blender)."""

    def test_two_scenes_one_perturbation(self):
        with tempfile.TemporaryDirectory() as d:
            code, _, err = run_main('oracle', '--skill-dir', str(SKILL), '--suite', 'smoke', '--seed', '20261019', '--out', d, '--cases', '2',
                                    '--perturbations', 'noise_1', '--seeds', '12', '--resamples', '30', '--jobs', '2')
            self.assertEqual(code, 0, err)
            res = read(Path(d) / 'results.json')
            self.assertEqual(len(res['rows']), 2)
            for row in res['rows']:
                self.assertFalse(row['failed'], row['failure'])
                self.assertIn(row['fit_status'], ('fitted', 'high_residual', 'ambiguous', 'check_failed'))
                self.assertLess(row['corner_err_diag'], 0.05)             # a loose sanity bound; the real numbers are in BASELINE.md
                self.assertEqual(row['compile_status'], 'ok')
                self.assertIsNotNone(row['placement_median_m'])
                self.assertGreater(row['fit_s'], 0)
            self.assertTrue((Path(d) / 'report.md').is_file())


if __name__ == '__main__':
    unittest.main()
