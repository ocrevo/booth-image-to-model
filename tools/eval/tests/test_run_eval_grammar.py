"""The runner and the booth grammar (I2) against stand-in skills: what each kind of skill is sent, and what the results hold."""
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
import grammar
import perturb
import run_eval
import stubskill
import synth_generate as sg

PERTS = ('clean', 'wrong_open_sides', 'swap_fb_lr')


def run_main(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = run_eval.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


class Grammar(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.runs = {}
        for name, kwargs in (('topo', dict(new=True, topology=True)), ('fit_only', dict(new=True)), ('oldest', dict(new=False))):
            skill = stubskill.make_stub_skill(cls.tmp / name, **kwargs)
            out = cls.tmp / ('run_' + name)
            code, _, err = run_main('oracle', '--skill-dir', str(skill), '--suite', 'oracle-v1', '--seed', '20261019', '--out', str(out), '--cases', '6',
                                    '--perturbations', *PERTS, '--check-corners', '0', '--resamples', '30', '--jobs', '2')
            assert code == 0, err
            cls.runs[name] = (out, read(out / 'results.json'))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def folder(self, name, row):
        return self.runs[name][0] / 'work' / 'runs' / row['case_id'] / row['pert']

    def test_a_skill_that_knows_the_keys_is_sent_the_declared_topology_in_schema_1_2(self):
        out, res = self.runs['topo']
        self.assertTrue(res['meta']['capabilities']['scene_topology_keys'])
        self.assertTrue(res['meta']['capabilities']['compile_topology'])
        self.assertEqual(len(res['rows']), 18)
        for row in res['rows']:
            scene = read(self.folder('topo', row) / 'scene.json')
            case, _ = sg.build_case(20261019, int(row['case_id'][-4:]))
            declared = perturb.apply(case, row['pert'], 0)['topology']['declared']
            self.assertEqual(scene['schema_version'], '1.2')
            self.assertEqual((scene['booth_type'], scene['open_sides'], scene['cutaway_sides']),
                             (declared['booth_type'], declared['open_sides'], declared['cutaway_sides']))
            self.assertTrue(row['topology_in_report'] and row['topology_declared'])

    def test_a_skill_without_the_keys_is_never_sent_them(self):
        for name, version in (('fit_only', '1.1'), ('oldest', 'scene-description.v1')):
            out, res = self.runs[name]
            self.assertFalse(res['meta']['capabilities']['scene_topology_keys'])
            for row in res['rows']:
                scene = read(self.folder(name, row) / 'scene.json')
                self.assertEqual(scene['schema_version'], version)
                for key in grammar_keys():
                    self.assertNotIn(key, scene)
                self.assertFalse(row['topology_in_report'])
                self.assertIsNone(row['topology_declared'])
                self.assertFalse(row['topology_flagged'])

    def test_the_turned_tokens_reach_both_tools_of_every_skill(self):
        for name in self.runs:
            for row in self.runs[name][1]['rows']:
                if row['pert'] != 'swap_fb_lr':
                    continue
                case, _ = sg.build_case(20261019, int(row['case_id'][-4:]))
                truth = {c['id']: c['xyz'] for c in case['corners']}
                points = read(self.folder(name, row) / 'points.json')['points']
                self.assertEqual({p['id']: p['xyz'] for p in points}, {k: grammar.mirror_xyz(v, 'swap_fb_lr') for k, v in truth.items()})
                scene = read(self.folder(name, row) / 'scene.json')
                self.assertEqual({p['id']: p['xyz'] for p in scene['corner_points']}, {k: grammar.mirror_xyz(v, 'swap_fb_lr') for k, v in truth.items()})

    def test_rows_carry_the_truth_and_what_the_stand_in_reported(self):
        rows = self.runs['topo'][1]['rows']
        by = {(r['case_id'], r['pert']): r for r in rows}
        self.assertTrue(all(r['topology_defect'] is (r['pert'] != 'clean') for r in rows))
        for r in rows:
            if r['pert'] == 'wrong_open_sides' and r['topology_detail'] == 'closed_as_open':
                self.assertEqual((r['topo_wall'], r['walls_dropped_open'], r['walls_on_open_declared']), ('fail', 1, 0), r['case_id'])
                self.assertTrue(r['topology_flagged'] and r['topology_only_flagged'])
                self.assertIn('a wall was placed on an open side', r['topology_reasons'][0])
            if r['pert'] == 'clean':
                self.assertFalse(r['topology_flagged'], r['case_id'])                       # the stand-in camera is in front, and every scene's front is open
        self.assertTrue({r['topology_detail'] for r in rows if r['pert'] == 'wrong_open_sides'} >= {'closed_as_open'})
        self.assertEqual(len(by), 18)

    def test_a_skill_that_ignores_the_declaration_keeps_the_wall_on_a_side_declared_open(self):
        # the baseline-like stand-in builds every wall of the scene: the harness sees the wall that the new compile leaves out
        seen = 0
        for row in self.runs['fit_only'][1]['rows']:
            self.assertFalse(row['topology_reason_hit'])
            if row['pert'] == 'wrong_open_sides' and row['topology_detail'] == 'closed_as_open':
                seen += 1
                self.assertEqual((row['walls_on_open_declared'], row['walls_on_open_truth']), (1, 0))
            else:
                self.assertEqual(row['walls_on_open_declared'], 0)
        self.assertGreater(seen, 0)
        rows = self.runs['topo'][1]['rows']
        self.assertTrue(all(r['walls_on_open_declared'] == 0 for r in rows))                  # the grammar-aware stand-in leaves such a wall out

    def test_the_report_and_the_aggregates_have_the_grammar_tables(self):
        out, res = self.runs['topo']
        text = (out / 'report.md').read_text(encoding='utf-8')
        for needle in ('## Booth grammar (topology)', '### By perturbation', '### By kind of mistake', '### By true booth type', '## Layer: topology',
                       'swap_fb_lr', 'open_as_closed', 'peninsula'):
            self.assertIn(needle, text)
        self.assertNotIn('\r', text)
        agg = res['aggregate']
        self.assertEqual(set(agg['by_topology_detail']) - {'all'} <= {'swap_fb_lr', 'open_as_closed', 'closed_as_open'}, True)
        self.assertIn('swap_fb_lr', agg['by_topology_detail'])
        self.assertEqual(agg['by_pert']['clean']['topology_false_alarm']['value'], 0.0)
        self.assertEqual(agg['by_pert']['clean']['topology_flag_rate']['n'], 0)
        self.assertEqual(agg['by_pert']['swap_fb_lr']['topology_flag_rate']['n'], 6)
        old_text = (self.runs['oldest'][0] / 'report.md').read_text(encoding='utf-8')
        self.assertIn('## Booth grammar (topology)', old_text)                                  # the section is there, with dashes where the skill has no checks

    def test_a_default_suite_run_has_no_defects_and_no_grammar_cut_by_kind(self):
        skill = stubskill.make_stub_skill(self.tmp / 'topo', new=True, topology=True)
        out = self.tmp / 'default_perts'
        code, _, err = run_main('oracle', '--skill-dir', str(skill), '--suite', 'smoke', '--seed', '1', '--out', str(out), '--cases', '2', '--resamples', '20')
        self.assertEqual(code, 0, err)
        res = read(out / 'results.json')
        self.assertEqual(res['meta']['perturbations'], ['noise_1', 'outlier_1'])
        self.assertTrue(all(r['topology_defect'] is False for r in res['rows']))
        self.assertNotIn('by_topology_detail', res['aggregate'])
        self.assertIn('by_grammar_type', res['aggregate'])

    def test_rescore_can_write_somewhere_else_and_leaves_the_original_alone(self):
        out, first = self.runs['topo']
        before = (out / 'results.json').read_bytes()
        target = self.tmp / 'rescored'
        code, stdout, err = run_main('rescore', '--dir', str(out), '--to', str(target), '--resamples', '20')
        self.assertEqual(code, 0, err)
        self.assertEqual((out / 'results.json').read_bytes(), before)
        self.assertTrue((target / 'results.json').is_file() and (target / 'report.md').is_file())
        second = read(target / 'results.json')
        strip = lambda rows: [{k: v for k, v in r.items()} for r in rows]
        self.assertEqual(strip(second['rows']), strip(first['rows']))
        self.assertTrue(second['meta']['rescored'])
        self.assertEqual(second['meta']['harness_version'], run_eval.HARNESS_VERSION)


def grammar_keys():
    return ('booth_type', 'open_sides', 'cutaway_sides')


if __name__ == '__main__':
    unittest.main()
