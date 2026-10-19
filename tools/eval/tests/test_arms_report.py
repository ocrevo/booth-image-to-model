import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import arms_report as ar


def rows_for(pert, errors, flagged=None, recall=None, n_fit=8):
    rows = []
    for i, e in enumerate(errors):
        row = {'case_id': f'c{i}', 'pert': pert, 'booth_type': 'inline', 'corner_err_diag': e, 'abs_log_wh': e, 'abs_log_dh': e,
               'flagged': bool(flagged[i]) if flagged else False, 'bad_fit': e > 0.015, 'failed': False, 'n_injected': 0, 'valid_input': True,
               'total_s': 5.0, 'n_fit_bin': ar.metrics.n_fit_bin(n_fit)}
        if recall is not None:
            row['outlier_recall'] = (recall[i], 1)
            row['outlier_recall_common'] = (recall[i], 1)
        rows.append(row)
    return rows


def write_side(folder, rows, arm, name='x'):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    meta = {'skill_label': name, 'skill_version': '1', 'skill_fingerprint': 'f' * 64, 'suite': 'smoke', 'seed': 1, 'n_runs': len(rows),
            'check_corners': arm, 'harness_version': '1.1.0', 'capabilities': {'box_fit_fit_flag': True}, 'stages': 'compile'}
    (folder / 'results.json').write_text(json.dumps({'meta': meta, 'rows': rows}), encoding='utf-8')
    return folder


class ArmsReport(unittest.TestCase):
    def test_three_sides_give_pairwise_differences_marks_and_naming_tables(self):
        with tempfile.TemporaryDirectory() as d:
            same = [0.01 + 0.001 * (i % 4) for i in range(16)]
            base = write_side(Path(d) / 'a', rows_for('outlier_30', [0.05 + 0.002 * (i % 4) for i in range(16)], recall=[0] * 16), 2)
            arm2 = write_side(Path(d) / 'b', rows_for('outlier_30', same, recall=[1, 0] * 8, n_fit=7), 2)
            arm0 = write_side(Path(d) / 'c', rows_for('outlier_30', same, recall=[1] * 16, n_fit=9), 0)
            out = Path(d) / 'tables.md'
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                code = ar.main(['--side', f'baseline={base}', '--side', f'held-out={arm2}', '--side', f'all-fitted={arm0}', '--out', str(out), '--resamples', '60'])
            self.assertEqual(code, 0)
            text = out.read_text(encoding='utf-8')
            for needle in ('held-out minus baseline', 'all-fitted minus baseline', 'all-fitted minus held-out', '## Accuracy, all runs',
                           '## Decisions on outlier_30', '## Time', 'corners held out of the fit: 0'):
                self.assertIn(needle, text)
            line = next(l for l in text.splitlines() if l.startswith('| Corner error, share of image diagonal (median)'))
            cells = [c.strip() for c in line.strip('|').split('|')]
            self.assertTrue(cells[4].endswith('+'), cells)               # held-out is clearly better than the baseline
            self.assertTrue(cells[6].endswith('=') or cells[6] == '0.00% [0.00%, 0.00%] =', cells)     # the two arms are the same here
            naming = text.split('## held-out: injected corners that were fitted and that the skill names')[1].split('##')[0]
            self.assertIn('outlier_30', naming)
            self.assertIn('50.0% (n=', naming)
            self.assertNotIn('Warnings', text)
            self.assertEqual(err.getvalue(), '')

    def test_a_different_suite_is_a_warning_and_two_sides_are_the_minimum(self):
        with tempfile.TemporaryDirectory() as d:
            a = write_side(Path(d) / 'a', rows_for('clean', [0.01] * 4), 2)
            b = write_side(Path(d) / 'b', rows_for('clean', [0.01] * 4), 2)
            data = json.loads((b / 'results.json').read_text(encoding='utf-8'))
            data['meta']['seed'] = 9
            (b / 'results.json').write_text(json.dumps(data), encoding='utf-8')
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(err):
                ar.main(['--side', f'a={a}', '--side', f'b={b}', '--resamples', '20'])
            self.assertIn('seed differs', err.getvalue())
            self.assertIn('**Warnings**', out.getvalue())
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                ar.main(['--side', f'a={a}'])
        with self.assertRaises(ValueError):
            ar.parse_side('nopath')


if __name__ == '__main__':
    unittest.main()
