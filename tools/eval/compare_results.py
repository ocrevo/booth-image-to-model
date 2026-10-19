"""Compare two evaluation results (for example the frozen baseline and a changed skill) scene by scene.

    python tools/eval/compare_results.py --a output/_eval/runs/baseline-oracle-v1 --b output/_eval/runs/new-oracle-v1 \
        --out output/_eval/compare.md [--pair all_guess:noise_1]

Both sides must come from the same suite and seed, so that scene N is the same scene on both sides (a warning is printed, and
written into the markdown, when the suite, seed, suite fingerprint, generator version or harness version differ; the arm, that is
how many corners were held out of the fit, is shown for each side and is allowed to differ: comparing arms is a use of this tool).
For each metric and each
perturbation the table shows A, B and B minus A with a paired bootstrap 95 % interval over scenes, and marks a clear gain (+),
a clear loss (-) or no clear change (=) when the interval excludes zero in the direction that is better for that metric.
`--pair A_PERT:B_PERT` also compares different perturbations of the same scene, for example a reader that assumed all of W/D/H
(`all_guess` of A) against a reader that gave one dimension (`noise_1` of B). Standard library and numpy only.
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics
import report
import stats

BY_KEY = {m[0]: m for m in metrics.METRICS}
# +1: higher is better, -1: lower is better, 0: no preferred direction (reported, never marked)
DIRECTION = {
    'corner_err_diag_med': -1, 'corner_err_diag_mean': -1, 'fov_err_deg_med': -1, 'rot_err_deg_med': -1, 'abs_log_wh_med': -1,
    'abs_log_dh_med': -1, 'abs_log_wh_mean': -1, 'abs_log_dh_mean': -1, 'focal_rel_err_med': -1, 'placement_m_med': -1,
    'placement_share_w_med': -1, 'flag_rate': 0, 'bad_rate': -1, 'decision_accuracy': 1, 'sensitivity': 1, 'false_alarm_rate': -1,
    'flag_on_injected': 1, 'flag_on_valid_input': -1, 'failure_rate': -1, 'outlier_recall': 1, 'outlier_false_alarm': -1,
    'clean_outlier_report': -1, 'worst_hit_rate': 1, 'fit_s_med': -1, 'compile_s_med': -1, 'total_s_mean': -1,
    'stdout_single_json': 1, 'fit_consistent': 1,
    'abs_w_err_med': -1, 'abs_d_err_med': -1, 'abs_h_err_med': -1, 'abs_dim_err_max_med': -1, 'gate_precision': 1, 'flag_on_defective': 1,
    'defect_precision': 1, 'bad_scale_rate': -1, 'flag_on_bad_scale': 1, 'flag_on_wrong_anchor': 1, 'outlier_recall_computed': 1,
    'outlier_recall_common': 1, 'outlier_recall_all_injected': 1, 'loo_computed_rate': 0, 'check_inj_flagged': 1, 'check_inj_check_failed': 0, 'worst_hit_rate_all_points': 1,
    # booth grammar (harness 1.2): only the recall, the false alarm and the walls have a better direction; the check statuses are reported
    'topology_flag_rate': 1, 'topology_catchable': 0, 'topology_flag_any': 1, 'topology_added_flag': 1, 'topology_false_alarm': -1, 'topology_added_false_alarm': -1, 'topology_fail_in_gate': 1, 'topo_declared_rate': 0,
    'topo_type_fail_rate': 0, 'topo_wall_fail_rate': 0, 'topo_closed_warn_rate': 0, 'topo_camera_fail_rate': 0, 'topo_camera_warn_rate': 0,
    'topo_not_checked_rate': 0, 'walls_on_open_declared_rate': -1, 'walls_on_open_truth_rate': -1, 'walls_dropped_rate': 0,
}
# Harness versions whose rows mean the same: 1.2.0 only added metrics and perturbations (the 1.1.0 fields of an old run, rescored with 1.2.0,
# come out identical); the added topology metrics are simply absent on the older side and are skipped there.
ROW_COMPATIBLE = (frozenset({'1.1.0', '1.2.0'}),)
# meta fields that must agree for two sides to be paired scene by scene (a difference is a warning, never an error)
MUST_MATCH = ('suite', 'seed', 'suite_sha256', 'generator_version', 'harness_version', 'n_cases')


def meta_warnings(meta_a, meta_b):
    """Plain-language warnings for the fields of MUST_MATCH that differ (or that only one side recorded)."""
    out = []
    for key in MUST_MATCH:
        a, b = meta_a.get(key), meta_b.get(key)
        if a == b:
            continue
        if key == 'suite_sha256':
            show = lambda v: 'not recorded' if not v else str(v)[:16]
            out.append(f'suite fingerprint differs (A {show(a)}, B {show(b)}): the two sides were not made from the same ground truth; '
                       'scene N may not be the same scene')
        elif key == 'harness_version':
            if frozenset({a, b}) in ROW_COMPATIBLE:
                continue
            out.append(f'harness version differs (A {a}, B {b}): metric definitions may differ; rescore the older side (run_eval.py rescore) first')
        elif key == 'generator_version':
            out.append(f'generator version differs (A {a if a is not None else "not recorded"}, B {b if b is not None else "not recorded"}): '
                       'the scenes may have changed')
        elif key == 'n_cases':
            out.append(f'number of scenes differs (A {a}, B {b}): only the scenes present on both sides are compared')
        else:
            out.append(f'{key} differs (A {a}, B {b}); scene N is not the same scene on both sides')
    return out
TABLE_KEYS = ['corner_err_diag_med', 'rot_err_deg_med', 'fov_err_deg_med', 'abs_log_wh_med', 'abs_log_dh_med', 'abs_w_err_med',
              'abs_d_err_med', 'abs_h_err_med', 'placement_m_med', 'decision_accuracy', 'sensitivity', 'gate_precision', 'false_alarm_rate',
              'flag_on_injected', 'flag_on_valid_input', 'flag_on_wrong_anchor', 'outlier_recall', 'outlier_recall_computed',
              'outlier_false_alarm', 'failure_rate', 'total_s_mean',
              'topology_flag_rate', 'topology_flag_any', 'topology_added_flag', 'topology_false_alarm', 'topology_added_false_alarm',
              'walls_on_open_declared_rate']


def load(path):
    p = Path(path)
    p = p / 'results.json' if p.is_dir() else p
    return json.loads(p.read_text(encoding='utf-8'))


def mark(direction, d):
    if d['delta'] is None or d['lo'] is None or direction == 0:
        return ''
    if d['lo'] > 0:
        return '+' if direction > 0 else '-'
    if d['hi'] < 0:
        return '-' if direction > 0 else '+'
    return '='


def compare_rows(rows_a, rows_b, keys, resamples):
    out = {}
    for key in keys:
        _k, _layer, _label, kind, get, _unit = BY_KEY[key]
        out[key] = stats.paired_difference(rows_a, rows_b, get, kind, resamples)
    return out


def render(meta_a, meta_b, groups, keys, warnings=()):
    lines = ['# Comparison of two evaluation runs', '']
    for name, meta in (('A', meta_a), ('B', meta_b)):
        arm = meta.get('check_corners')
        lines.append(f"- {name}: {meta.get('skill_label')} (version {meta.get('skill_version')}, commit {meta.get('skill_commit')}, "
                     f"code {(meta.get('skill_fingerprint') or '')[:12]}), suite {meta.get('suite')}, seed {meta.get('seed')}, {meta.get('n_runs')} runs, "
                     f"check corners held out: {arm if arm is not None else 'n/a (older run: the case check corners)'}")
    if warnings:
        lines += ['', '**Warnings**', ''] + [f'- {w}' for w in warnings]
    lines += ['', 'Marks: `+` clear gain, `-` clear loss, `=` no clear change (paired 95 % interval of B minus A, scenes resampled); '
              'a blank mark means the metric has no better direction or too little data.', '']
    for title, rows in groups:
        body = []
        for key in keys:
            d = rows[key]
            if d['a'] is None or d['b'] is None:
                continue
            unit = BY_KEY[key][5]
            ci = '' if d['lo'] is None else f" [{report.number(d['lo'], unit)}, {report.number(d['hi'], unit)}]"
            body.append([BY_KEY[key][2], d['n_scenes'], report.number(d['a'], unit), report.number(d['b'], unit),
                         report.number(d['delta'], unit) + ci, mark(DIRECTION.get(key, 0), d)])
        lines += [f'## {title}', '', report.table(['Metric', 'Scenes', 'A', 'B', 'B minus A', 'Mark'], body) if body else '_no comparable rows_', '']
    return '\n'.join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    p.add_argument('--a', required=True, help='results.json (or its folder) of the reference side, for example the baseline')
    p.add_argument('--b', required=True, help='results.json (or its folder) of the changed side')
    p.add_argument('--out', help='Write the markdown here')
    p.add_argument('--pair', action='append', default=[], metavar='A_PERT:B_PERT', help='Also compare these two perturbations across the sides')
    p.add_argument('--resamples', type=int, default=stats.DEFAULT_RESAMPLES)
    a = p.parse_args(argv)
    ra, rb = load(a.a), load(a.b)
    warnings = meta_warnings(ra['meta'], rb['meta'])
    for w in warnings:
        print('warning: ' + w, file=sys.stderr)
    rows_a = [r for r in ra['rows'] if 'flagged' in r]
    rows_b = [r for r in rb['rows'] if 'flagged' in r]
    groups = [('All runs', compare_rows(rows_a, rows_b, TABLE_KEYS, a.resamples))]
    perts = sorted({r['pert'] for r in rows_a} & {r['pert'] for r in rows_b}, key=str)
    for name in perts:
        groups.append((f'Perturbation {name}', compare_rows([r for r in rows_a if r['pert'] == name], [r for r in rows_b if r['pert'] == name],
                                                           TABLE_KEYS, a.resamples)))
    for pair in a.pair:
        pa, _, pb = pair.partition(':')
        groups.append((f'A {pa} against B {pb}', compare_rows([r for r in rows_a if r['pert'] == pa], [r for r in rows_b if r['pert'] == pb],
                                                             TABLE_KEYS, a.resamples)))
    text = render(ra['meta'], rb['meta'], groups, TABLE_KEYS, warnings)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_bytes(text.encode('utf-8'))
    else:
        print(text)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
