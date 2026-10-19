import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stats


def rows_of(values, clusters=None):
    return [{'case_id': f'c{i if clusters is None else clusters[i]}', 'v': v} for i, v in enumerate(values)]


class Summaries(unittest.TestCase):
    def test_constant_values_give_a_degenerate_interval(self):
        s = stats.summarize(rows_of([2.0] * 8), lambda r: r['v'], 'mean', 200)
        self.assertEqual((s['value'], s['lo'], s['hi'], s['n']), (2.0, 2.0, 2.0, 8))

    def test_interval_brackets_the_value_and_is_reproducible(self):
        values = [float(i % 7) for i in range(40)]
        a = stats.summarize(rows_of(values), lambda r: r['v'], 'median', 300)
        b = stats.summarize(rows_of(values), lambda r: r['v'], 'median', 300)
        self.assertEqual(a, b)
        self.assertLessEqual(a['lo'], a['value'])
        self.assertGreaterEqual(a['hi'], a['value'])
        self.assertLess(a['lo'], a['hi'])

    def test_more_data_narrows_the_interval(self):
        small = stats.summarize(rows_of([float(i % 5) for i in range(10)]), lambda r: r['v'], 'mean', 400)
        large = stats.summarize(rows_of([float(i % 5) for i in range(200)]), lambda r: r['v'], 'mean', 400)
        self.assertLess(large['hi'] - large['lo'], small['hi'] - small['lo'])

    def test_rate_and_ratio(self):
        rows = rows_of([1.0, 0.0, 1.0, 1.0])
        self.assertAlmostEqual(stats.summarize(rows, lambda r: r['v'], 'rate', 50)['value'], 0.75)
        pairs = stats.summarize(rows, lambda r: (r['v'], 1.0 if r['v'] else 2.0), 'ratio', 50)
        self.assertAlmostEqual(pairs['value'], 3 / 5)

    def test_rows_that_do_not_count_are_ignored(self):
        s = stats.summarize(rows_of([1.0, None, 3.0]), lambda r: r['v'], 'mean', 50)
        self.assertEqual(s['n'], 2)
        self.assertAlmostEqual(s['value'], 2.0)
        empty = stats.summarize(rows_of([None, None]), lambda r: r['v'], 'mean', 50)
        self.assertEqual(empty, {'n': 0, 'value': None, 'lo': None, 'hi': None})

    def test_one_scene_has_no_interval(self):
        s = stats.summarize(rows_of([1.0, 2.0, 3.0], clusters=[0, 0, 0]), lambda r: r['v'], 'mean', 50)
        self.assertEqual(s['value'], 2.0)
        self.assertIsNone(s['lo'])

    def test_whole_scenes_are_resampled(self):
        # two scenes with very different values; every resample is one of the three mixes, so the interval spans both
        rows = rows_of([0.0, 0.0, 10.0, 10.0], clusters=[0, 0, 1, 1])
        s = stats.summarize(rows, lambda r: r['v'], 'mean', 300)
        self.assertEqual((s['lo'], s['hi']), (0.0, 10.0))

    def test_unknown_statistic_is_refused(self):
        with self.assertRaises(ValueError):
            stats.summarize(rows_of([1.0, 2.0]), lambda r: r['v'], 'mode', 10)


class PairedDifference(unittest.TestCase):
    def test_a_consistent_improvement_has_an_interval_below_zero(self):
        a = rows_of([5.0 + (i % 3) for i in range(30)])
        b = rows_of([2.0 + (i % 3) * 0.5 for i in range(30)])
        d = stats.paired_difference(a, b, lambda r: r['v'], 'median', 300)
        self.assertEqual(d['n_scenes'], 30)
        self.assertLess(d['delta'], 0)
        self.assertLess(d['hi'], 0)

    def test_identical_sides_have_a_zero_difference(self):
        a = rows_of([float(i % 4) for i in range(20)])
        d = stats.paired_difference(a, a, lambda r: r['v'], 'mean', 100)
        self.assertEqual((d['delta'], d['lo'], d['hi']), (0.0, 0.0, 0.0))

    def test_only_shared_scenes_count(self):
        a = rows_of([1.0, 1.0, 1.0], clusters=[0, 1, 2])
        b = rows_of([2.0, 2.0, 2.0], clusters=[1, 2, 3])
        d = stats.paired_difference(a, b, lambda r: r['v'], 'mean', 50)
        self.assertEqual(d['n_scenes'], 2)
        self.assertEqual(d['delta'], 1.0)
        none = stats.paired_difference(a, rows_of([1.0], clusters=[9]), lambda r: r['v'], 'mean', 50)
        self.assertIsNone(none['delta'])


if __name__ == '__main__':
    unittest.main()
