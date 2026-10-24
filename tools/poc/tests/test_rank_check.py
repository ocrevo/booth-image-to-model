"""P3 tests for rank_check.py: Spearman correlation of measured scores with the expected order of the six broken variants."""
from __future__ import annotations
import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parents[0]))        # tools/poc, so that `score` is the package

from score import rank_check as rc  # noqa: E402

ORDER = ['B1', 'B2', 'B3', 'B4', 'B5', 'B6']
TOTALS = [90, 80, 70, 60, 50, 40]               # best to worst, higher is better
EDGES = [0.01, 0.02, 0.03, 0.04, 0.05, 0.06]    # best to worst, lower is better


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = rc.main([str(a) for a in argv])
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


class Spearman(unittest.TestCase):
    def test_spearman_known_orders(self):
        base = [1, 2, 3, 4, 5, 6]
        self.assertAlmostEqual(rc.spearman(base, base), 1.0)
        self.assertAlmostEqual(rc.spearman(base, base[::-1]), -1.0)
        self.assertAlmostEqual(rc.spearman(base, [2, 1, 4, 3, 6, 5]), 1 - 6 * 6 / 210)       # three adjacent swaps: 0.829
        self.assertAlmostEqual(rc.spearman(base, [3, 2, 1, 4, 5, 6]), 1 - 6 * 8 / 210)       # one swap across two places: 0.771
        self.assertGreaterEqual(rc.spearman(base, [2, 1, 4, 3, 6, 5]), 0.8)
        self.assertLess(rc.spearman(base, [3, 2, 1, 4, 5, 6]), 0.8)

    def test_spearman_uses_the_values_not_only_their_order(self):
        self.assertAlmostEqual(rc.spearman([1, 2, 3, 4], [10.5, 20.5, 31.0, 400.0]), 1.0)

    def test_spearman_ties_get_average_ranks(self):
        # ranks of [1, 1, 2, 3] are [1.5, 1.5, 3, 4]; Pearson of that with [1, 2, 3, 4] is 0.9487
        self.assertAlmostEqual(rc.spearman([1, 2, 3, 4], [1, 1, 2, 3]), 0.9486832980505138)

    def test_spearman_of_a_constant_is_none_not_a_crash(self):
        self.assertIsNone(rc.spearman([1, 2, 3, 4], [5, 5, 5, 5]))


class Check(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.scores = self.tmp / 'scores'
        self.expected = self.tmp / 'expected.json'
        self.expected.write_text(json.dumps(ORDER), encoding='utf-8')
        self.out = self.tmp / 'out' / 'rank_check.json'

    def write(self, totals=TOTALS, edges=EDGES, status=None, drop=()):
        for n, v in enumerate(ORDER):
            if v in drop:
                continue
            folder = self.scores / v
            folder.mkdir(parents=True, exist_ok=True)
            st = (status or {}).get(v, 'measured')
            spec_bytes = json.dumps({'variant': v}).encode('utf-8')
            (folder / 'spec.json').write_bytes(spec_bytes)
            doc = {'schema_version': 'score.v1', 'picture_id': 'pic_T', 'candidate_id': f'cand_{v}',
                   'spec_sha256': hashlib.sha256(spec_bytes).hexdigest(),
                   'levels': {}, 'total': totals[n], 'status': st, 'reason': None if st == 'measured' else 'panel_missing',
                   'aux': {'edge_distance_diag': edges[n]}}
            (folder / 'score.json').write_text(json.dumps(doc), encoding='utf-8')

    def run_check(self, expected=None):
        return run_cli('--scores', self.scores, '--expected', expected or self.expected, '--out', self.out)

    def result(self):
        return json.loads(self.out.read_text(encoding='utf-8'))

    def test_perfect_order_gives_one_and_passes(self):
        self.write()
        code, out, err = self.run_check()
        self.assertEqual(code, 0, err)
        r = self.result()
        self.assertAlmostEqual(r['spearman_total'], 1.0)
        self.assertAlmostEqual(r['spearman_edge'], 1.0)
        self.assertTrue(r['pass'])
        self.assertEqual(r['inverted_pairs'], [])
        self.assertEqual(r['expected_order'], ORDER)

    def test_edge_distance_is_better_when_smaller(self):
        self.write(edges=EDGES[::-1])                  # the worst variant has the smallest distance: perfectly wrong
        self.assertEqual(self.run_check()[0], 0)
        r = self.result()
        self.assertAlmostEqual(r['spearman_edge'], -1.0)
        self.assertAlmostEqual(r['spearman_total'], 1.0)
        self.assertFalse(r['pass'])

    def test_swapped_pair_is_listed_with_its_values(self):
        totals = list(TOTALS)
        totals[2], totals[3] = totals[3], totals[2]   # B4 now beats B3
        self.write(totals=totals)
        self.assertEqual(self.run_check()[0], 0)
        r = self.result()
        self.assertAlmostEqual(r['spearman_total'], 1 - 6 * 2 / 210)
        self.assertTrue(r['pass'])
        pairs = [(p['measure'], p['should_be_better'], p['should_be_worse']) for p in r['inverted_pairs']]
        self.assertEqual(pairs, [('total', 'B3', 'B4')])
        p = r['inverted_pairs'][0]
        self.assertEqual((p['should_be_better_value'], p['should_be_worse_value']), (60, 70))

    def test_three_adjacent_swaps_still_pass_and_one_wide_swap_fails(self):
        self.write(totals=[80, 90, 60, 70, 40, 50])
        self.run_check()
        r = self.result()
        self.assertAlmostEqual(r['spearman_total'], 0.8285714, places=6)
        self.assertTrue(r['pass'])
        self.write(totals=[70, 80, 90, 60, 50, 40])
        self.run_check()
        r = self.result()
        self.assertAlmostEqual(r['spearman_total'], 0.7714286, places=6)
        self.assertFalse(r['pass'])

    def test_both_coefficients_are_needed_to_pass(self):
        self.write(edges=[0.03, 0.04, 0.01, 0.02, 0.06, 0.05])
        self.run_check()
        r = self.result()
        self.assertTrue(r['pass_total'])
        self.assertFalse(r['pass_edge'])
        self.assertFalse(r['pass'])

    def test_exit_code_is_zero_when_computed_even_if_the_check_fails(self):
        self.write(totals=TOTALS[::-1])
        self.assertEqual(self.run_check()[0], 0)
        self.assertFalse(self.result()['pass'])

    def test_a_variant_that_is_not_measured_stops_the_check_and_is_named(self):
        self.write(status={'B4': 'blocked'})
        code, out, err = self.run_check()
        self.assertEqual(code, 2)
        self.assertIn('B4', err)
        self.assertIn('blocked', err)
        r = self.result()
        self.assertEqual(r['status'], 'blocked')
        self.assertEqual([b['variant'] for b in r['blocked']], ['B4'])
        self.assertNotIn('spearman_total', r)

    def test_a_missing_variant_stops_the_check_and_is_named(self):
        self.write(drop=('B6',))
        code, out, err = self.run_check()
        self.assertEqual(code, 2)
        self.assertIn('B6', err)
        self.assertEqual([b['variant'] for b in self.result()['blocked']], ['B6'])

    def test_a_measured_variant_without_an_edge_distance_stops_the_check(self):
        self.write()
        p = self.scores / 'B2' / 'score.json'
        doc = json.loads(p.read_text(encoding='utf-8'))
        doc['aux']['edge_distance_diag'] = None
        p.write_text(json.dumps(doc), encoding='utf-8')
        code, out, err = self.run_check()
        self.assertEqual(code, 2)
        self.assertIn('B2', err)
        self.assertIn('edge_distance_diag', err)

    def test_a_score_of_another_spec_stops_the_check_and_is_named(self):
        self.write()
        (self.scores / 'B3' / 'spec.json').write_text(json.dumps({'variant': 'B3', 'edited': True}), encoding='utf-8')
        code, out, err = self.run_check()
        self.assertEqual(code, 2)
        self.assertIn('B3', err)
        self.assertIn('spec_sha256', err)
        r = self.result()
        self.assertEqual(r['status'], 'blocked')
        self.assertEqual([b['variant'] for b in r['blocked']], ['B3'])

    def test_a_preview_copy_with_windows_line_ends_is_the_same_spec(self):
        # blender_build.py rewrites spec.json through common.write_json in text mode: CRLF on Windows, LF in B<n>/spec.json
        self.write()
        sys.path.insert(0, str(ROOT / 'booth-image-to-model' / 'scripts'))
        try:
            import common
        finally:
            sys.path.pop(0)
        for v in ORDER:
            doc = json.loads((self.scores / v / 'spec.json').read_text(encoding='utf-8'))
            preview = self.tmp / 'preview' / v
            common.write_json(preview / 'spec.json', doc)             # the engine's own writer, whatever the platform gives
            crlf = (json.dumps(doc, ensure_ascii=False, indent=2) + '\n').replace('\n', '\r\n').encode('utf-8')
            for raw in (preview.joinpath('spec.json').read_bytes(), crlf):
                score = json.loads((self.scores / v / 'score.json').read_text(encoding='utf-8'))
                score['spec_sha256'] = hashlib.sha256(raw).hexdigest()
                (self.scores / v / 'score.json').write_text(json.dumps(score), encoding='utf-8')
                # the variant's own spec.json is the LF form, as perturb_spec.py writes it
                (self.scores / v / 'spec.json').write_bytes((json.dumps(doc, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
                self.assertIn(score['spec_sha256'], rc.spec_hashes(self.scores / v / 'spec.json'))
        code, out, err = self.run_check()
        self.assertEqual(code, 0, err)

    def test_crlf_does_not_hide_a_real_difference(self):
        self.write()
        doc = {'variant': 'B2', 'changed': True}
        crlf = (json.dumps(doc, ensure_ascii=False, indent=2) + '\n').replace('\n', '\r\n').encode('utf-8')
        p = self.scores / 'B2' / 'score.json'
        score = json.loads(p.read_text(encoding='utf-8'))
        score['spec_sha256'] = hashlib.sha256(crlf).hexdigest()           # a CRLF copy of a different spec
        p.write_text(json.dumps(score), encoding='utf-8')
        code, out, err = self.run_check()
        self.assertEqual(code, 2)
        self.assertIn('B2', err)
        self.assertIn('spec_sha256', err)

    def test_a_variant_folder_without_spec_json_stops_the_check(self):
        self.write()
        (self.scores / 'B5' / 'spec.json').unlink()
        code, out, err = self.run_check()
        self.assertEqual(code, 2)
        self.assertIn('B5', err)
        self.assertIn('spec.json', err)

    def test_template_object_is_accepted_as_the_expected_file(self):
        self.write()
        template = ROOT / 'docs' / 'poc' / 'templates' / 'anchor1-expected.json'
        code, out, err = self.run_check(expected=template)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.result()['expected_order'], ORDER)

    def test_bad_expected_file_is_refused(self):
        self.write()
        self.expected.write_text(json.dumps(['B1', 'B2', 'B2']), encoding='utf-8')
        code, out, err = self.run_check()
        self.assertEqual(code, 2)
        self.assertTrue(err.startswith('ERROR'))

    def test_help_names_the_options(self):
        code, out, err = run_cli('--help')
        self.assertEqual(code, 0)
        for opt in ('--scores', '--expected', '--out'):
            self.assertIn(opt, out)


if __name__ == '__main__':
    unittest.main()
