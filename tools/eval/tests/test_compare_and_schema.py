import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import compare_results as cr
import metrics

HERE = Path(__file__).resolve().parents[1]


def rows_for(pert, errors, flagged=None):
    rows = []
    for i, e in enumerate(errors):
        rows.append({'case_id': f'c{i}', 'pert': pert, 'booth_type': 'inline', 'corner_err_diag': e, 'abs_log_wh': e, 'abs_log_dh': e,
                     'flagged': bool(flagged[i]) if flagged else False, 'bad_fit': e > 0.015, 'failed': False, 'n_injected': 0, 'valid_input': True,
                     'total_s': 5.0})
    return rows


def results_file(folder, rows):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    meta = {'skill_label': 'x', 'skill_version': '1', 'skill_commit': 'abc', 'skill_fingerprint': 'f' * 64, 'suite': 'smoke', 'seed': 1, 'n_runs': len(rows)}
    (folder / 'results.json').write_text(json.dumps({'meta': meta, 'rows': rows}), encoding='utf-8')
    return folder


class Compare(unittest.TestCase):
    def test_a_clear_gain_is_marked_and_written(self):
        with tempfile.TemporaryDirectory() as d:
            a = results_file(Path(d) / 'a', rows_for('outlier_30', [0.04 + 0.002 * (i % 5) for i in range(20)]))
            b = results_file(Path(d) / 'b', rows_for('outlier_30', [0.01 + 0.001 * (i % 5) for i in range(20)]))
            out = Path(d) / 'cmp.md'
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cr.main(['--a', str(a), '--b', str(b), '--out', str(out), '--resamples', '100']), 0)
            text = out.read_text(encoding='utf-8')
            self.assertIn('## Perturbation outlier_30', text)
            line = next(l for l in text.splitlines() if l.startswith('| Corner error, share of image diagonal (median)'))
            self.assertTrue(line.rstrip().endswith('| + |'), line)

    def test_unchanged_runs_are_marked_equal_and_pairs_cross_perturbations(self):
        with tempfile.TemporaryDirectory() as d:
            same = [0.01 + 0.001 * (i % 4) for i in range(16)]
            a = results_file(Path(d) / 'a', rows_for('clean', same) + rows_for('all_guess', [0.05] * 16))
            b = results_file(Path(d) / 'b', rows_for('clean', same) + rows_for('noise_1', [0.01] * 16))
            out = Path(d) / 'cmp.md'
            with contextlib.redirect_stdout(io.StringIO()):
                cr.main(['--a', str(a), '--b', str(b), '--out', str(out), '--pair', 'all_guess:noise_1', '--resamples', '100'])
            text = out.read_text(encoding='utf-8')
            clean = text.split('## Perturbation clean')[1].split('##')[0]
            self.assertIn('| = |', clean)
            self.assertIn('## A all_guess against B noise_1', text)
            pair = text.split('## A all_guess against B noise_1')[1]
            self.assertIn('| + |', pair)

    def test_every_compared_metric_has_a_direction_entry(self):
        self.assertTrue(set(cr.TABLE_KEYS) <= set(cr.DIRECTION))
        self.assertTrue(set(cr.DIRECTION) <= {m[0] for m in metrics.METRICS})


class Warnings(unittest.TestCase):
    META = {'suite': 'oracle-v1', 'seed': 1, 'suite_sha256': 'a' * 64, 'generator_version': 1, 'harness_version': '1.1.0', 'n_cases': 32}

    def test_equal_metas_give_no_warning_and_the_arm_may_differ(self):
        self.assertEqual(cr.meta_warnings(self.META, dict(self.META)), [])
        self.assertEqual(cr.meta_warnings(dict(self.META, check_corners=2), dict(self.META, check_corners=0)), [])

    def test_harness_1_1_and_1_2_rows_mean_the_same_but_1_0_is_still_flagged(self):
        # 1.2.0 only added metrics and perturbations; the topology metrics are simply absent on the older side
        self.assertEqual(cr.meta_warnings(self.META, dict(self.META, harness_version='1.2.0')), [])
        self.assertEqual(cr.meta_warnings(dict(self.META, harness_version='1.2.0'), self.META), [])
        self.assertEqual(len(cr.meta_warnings(dict(self.META, harness_version='1.0.0'), dict(self.META, harness_version='1.2.0'))), 1)

    def test_topology_rows_are_compared_only_where_both_sides_have_them(self):
        old = [{'case_id': f'c{i}', 'pert': 'swap_fb_lr', 'flagged': False, 'bad_fit': True, 'failed': False, 'n_injected': 0, 'valid_input': False}
               for i in range(6)]
        new = [dict(r, flagged=i < 4, topology_defect=True, topology_flagged=i < 4) for i, r in enumerate(old)]
        groups = [('All runs', cr.compare_rows(old, new, cr.TABLE_KEYS, 50))]
        text = cr.render({}, {}, groups, cr.TABLE_KEYS)
        self.assertNotIn('Runs with a right topology', text)                    # no data on either side: no row
        flag = cr.compare_rows(old, [dict(r, topology_defect=True, topology_flagged=r['flagged']) for r in new], ['topology_flag_rate'], 50)['topology_flag_rate']
        self.assertEqual(flag['a'] is None, True)                              # the older side has no topology rows: nothing to subtract
        both = cr.compare_rows([dict(r, topology_defect=True, topology_flagged=False) for r in old], new, ['topology_flag_rate'], 50)['topology_flag_rate']
        self.assertAlmostEqual(both['delta'], 4 / 6)

    def test_each_difference_is_named(self):
        for key, value, needle in (('suite', 'smoke', 'suite differs'), ('seed', 2, 'seed differs'), ('suite_sha256', 'b' * 64, 'suite fingerprint differs'),
                                   ('generator_version', 2, 'generator version differs'), ('harness_version', '1.0.0', 'harness version differs'),
                                   ('n_cases', 8, 'number of scenes differs')):
            warnings = cr.meta_warnings(self.META, dict(self.META, **{key: value}))
            self.assertEqual(len(warnings), 1, key)
            self.assertIn(needle, warnings[0])

    def test_a_side_without_the_field_counts_as_different(self):
        old = {k: v for k, v in self.META.items() if k not in ('suite_sha256', 'generator_version', 'harness_version')}
        joined = ' '.join(cr.meta_warnings(old, self.META))
        for needle in ('suite fingerprint', 'generator version', 'harness version'):
            self.assertIn(needle, joined)
        self.assertIn('not recorded', joined)

    def test_warnings_are_printed_and_written_into_the_markdown(self):
        with tempfile.TemporaryDirectory() as d:
            a = results_file(Path(d) / 'a', rows_for('clean', [0.01] * 4))
            b = results_file(Path(d) / 'b', rows_for('clean', [0.01] * 4))
            for side, meta in (('a', dict(self.META)), ('b', dict(self.META, suite_sha256='c' * 64, check_corners=0))):
                path = Path(d) / side / 'results.json'
                data = json.loads(path.read_text(encoding='utf-8'))
                data['meta'].update(meta)
                path.write_text(json.dumps(data), encoding='utf-8')
            out, err = Path(d) / 'cmp.md', io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                cr.main(['--a', str(a), '--b', str(b), '--out', str(out), '--resamples', '20'])
            text = out.read_text(encoding='utf-8')
            self.assertIn('suite fingerprint differs', err.getvalue())
            self.assertIn('**Warnings**', text)
            self.assertIn('suite fingerprint differs', text)
            self.assertIn('check corners held out: 0', text)


@unittest.skipUnless(importlib.util.find_spec('jsonschema'), 'jsonschema is not installed')
class AnnotationSchema(unittest.TestCase):
    def setUp(self):
        from jsonschema import Draft202012Validator
        schema = json.loads((HERE / 'annotations.light.v1.schema.json').read_text(encoding='utf-8'))
        Draft202012Validator.check_schema(schema)
        self.validator = Draft202012Validator(schema)

    def example(self):
        return {
            'schema_version': 'annotations.light.v1', 'image': {'id': 'sample-0001', 'width': 1600, 'height': 1000, 'kind': 'render', 'cropped': False},
            'booth_type': 'corner', 'open_sides': ['front', 'right'],
            'inventory': [{'kind': 'wall', 'count': 2}, {'kind': 'counter', 'count': 1, 'note': None}],
            'scope': {'booth_polygon_px': [[100, 900], [900, 950], [1200, 700], [300, 650]], 'excluded': ['neighbour_booth', 'people']},
            'corners': [{'id': 'floor_back_left', 'uv_px': [310, 655], 'xyz': ['-W/2', 'D/2', 'F'], 'check': False, 'visible': True},
                        {'id': 'top_front_right', 'uv_px': [1190, 140], 'xyz': ['W/2', '-D/2', 'H'], 'check': True}],
            'part_boxes': [{'id': 'counter_1', 'kind': 'counter', 'bbox_px': [500, 600, 700, 800], 'floor_contact_px': [600, 790]}],
            'printed_dimensions': [{'applies_to': 'W', 'value_m': 6.0, 'text': '6 m', 'location_px': [800, 100]}],
            'reviewer': {'annotator': 'rev-01', 'second_reviewer': None, 'date': '2026-10-19'},
            'licence': {'picture_licence': 'CC0-1.0', 'annotations_licence': 'CC0-1.0', 'may_publish_picture': True, 'source': None},
        }

    def test_a_complete_example_is_valid(self):
        self.assertEqual([e.message for e in self.validator.iter_errors(self.example())], [])

    def test_missing_licence_and_stray_keys_are_refused(self):
        doc = self.example()
        del doc['licence']
        doc['client_name'] = 'x'
        messages = ' '.join(e.message for e in self.validator.iter_errors(doc))
        self.assertIn('licence', messages)
        self.assertIn('client_name', messages)

    def test_bad_values_are_refused(self):
        doc = self.example()
        doc['booth_type'] = 'stage'
        doc['corners'][0]['xyz'] = ['0', '0']
        doc['printed_dimensions'][0]['value_m'] = -1
        doc['reviewer']['date'] = 'yesterday'
        self.assertGreaterEqual(len(list(self.validator.iter_errors(doc))), 4)


if __name__ == '__main__':
    unittest.main()
