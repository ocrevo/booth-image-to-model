"""Several evaluation results side by side, paired by scene: arms of one skill, or an older skill against a newer one.

    python tools/eval/arms_report.py --side baseline=output/_eval/runs/i1b-baseline --side held-out=output/_eval/runs/i1b-check2 \
        --side all-fitted=output/_eval/runs/i1b-check0 --out output/_eval/arms-tables.md

The first --side is the reference. Every table shows each side's value and, for every pair of sides (the later one minus the earlier one),
the paired difference with a bootstrap 95 % interval over scenes and a mark: `+` clear gain, `-` clear loss, `=` no clear change, blank when
the metric has no better direction (the same marks as compare_results.py). The sides must come from the same suite and seed (a warning
is printed otherwise). Standard library and numpy only.

Sections: all runs; per perturbation for the core metrics; the decision metrics (flag rate, recall, precision, false alarms, valid input);
how often injected corners are named, by perturbation and by the number of fitted points; time.
"""
from __future__ import annotations
import argparse
import itertools
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compare_results as cr
import metrics
import report
import stats

BY_KEY = metrics.BY_KEY
ACCURACY = ['corner_err_diag_med', 'rot_err_deg_med', 'fov_err_deg_med', 'abs_log_wh_med', 'abs_log_dh_med', 'abs_w_err_med', 'abs_d_err_med',
            'abs_h_err_med', 'placement_m_med']
DECISION = ['flag_rate', 'sensitivity', 'gate_precision', 'false_alarm_rate', 'flag_on_valid_input', 'flag_on_injected', 'flag_on_defective',
            'defect_precision', 'decision_accuracy', 'failure_rate']
NAMING = ['outlier_recall', 'outlier_recall_common', 'outlier_recall_all_injected', 'outlier_false_alarm', 'clean_outlier_report',
          'check_inj_flagged', 'check_inj_check_failed', 'loo_computed_rate']
TIME = ['fit_s_med', 'compile_s_med', 'total_s_mean']
PER_PERT = ['corner_err_diag_med', 'rot_err_deg_med', 'abs_log_wh_med', 'abs_log_dh_med', 'abs_dim_err_max_med', 'flag_rate']
RECALL_PERTS = ('outlier_30', 'outlier_1', 'outlier_2', 'mislabel')


def parse_side(spec):
    name, sep, path = spec.partition('=')
    if not sep or not name or not path:
        raise ValueError(f'--side needs NAME=PATH, not {spec!r}')
    return name, cr.load(path)


def scored(res):
    return [r for r in res['rows'] if 'flagged' in r]


def unit_of(key):
    return BY_KEY[key][5]


def diff_cell(d, key):
    if d['delta'] is None:
        return '-'
    unit = unit_of(key)
    ci = '' if d['lo'] is None else f" [{report.number(d['lo'], unit)}, {report.number(d['hi'], unit)}]"
    mk = cr.mark(cr.DIRECTION.get(key, 0), d)
    return f"{report.number(d['delta'], unit)}{ci} {mk}".rstrip()


def pairs_of(names):
    return list(itertools.combinations(range(len(names)), 2))


def _compare(rows_by_side, i, j, keys, resamples):
    return cr.compare_rows(rows_by_side[i], rows_by_side[j], keys, resamples)


def metric_table(names, rows_by_side, keys, resamples, filt=None):
    """Rows = metrics, columns = each side and each pair's paired difference."""
    sub = [[r for r in rows if filt is None or filt(r)] for rows in rows_by_side]
    prs = pairs_of(names)
    diffs = {p: _compare(sub, p[0], p[1], keys, resamples) for p in prs}
    header = ['Metric'] + names + [f'{names[j]} minus {names[i]}' for i, j in prs]
    body = []
    for key in keys:
        cells = []
        for rows in sub:
            s = stats.summarize(rows, BY_KEY[key][4], BY_KEY[key][3], resamples)
            cells.append(report.cell(s, unit_of(key), with_ci=False) + (f" (n={s['n']})" if s['n'] and s['n'] < len(rows) else ''))
        body.append([BY_KEY[key][2]] + cells + [diff_cell(diffs[p][key], key) for p in prs])
    return report.table(header, body)


def by_pert_table(names, rows_by_side, key, perts, resamples):
    prs = pairs_of(names)
    header = ['Perturbation'] + names + [f'{names[j]} minus {names[i]}' for i, j in prs]
    body = []
    for pert in ['all'] + list(perts):
        sub = [[r for r in rows if pert == 'all' or r['pert'] == pert] for rows in rows_by_side]
        cells = [report.cell(stats.summarize(rows, BY_KEY[key][4], BY_KEY[key][3], resamples), unit_of(key), with_ci=False) for rows in sub]
        body.append([pert] + cells + [diff_cell(cr.compare_rows(sub[i], sub[j], [key], resamples)[key], key) for i, j in prs])
    return report.table(header, body)


def naming_table(name, rows, key, resamples):
    """Rows = perturbations with something to name, columns = all and the number of fitted points (that side's own count)."""
    table = metrics.recall_by_bin(rows, key, resamples)
    header = ['Perturbation', 'all'] + [f'{b} fitted' for b in metrics.N_FIT_BINS]
    body = []
    for pert, cols in table.items():
        body.append([pert] + [(report.cell(cols[b], 'percent', with_ci=False) + f" (n={cols[b]['n']})") if cols[b]['n'] else '-'
                              for b in ['all'] + list(metrics.N_FIT_BINS)])
    return report.table(header, body) if body else '_nothing to name in these runs_'


def render(sides, resamples, warnings=()):
    names = [n for n, _ in sides]
    rows_by_side = [scored(res) for _, res in sides]
    perts = sorted({r['pert'] for rows in rows_by_side for r in rows}, key=str)
    out = ['# Result sides, paired by scene', '']
    for n, res in sides:
        m = res['meta']
        arm = m.get('check_corners')
        out.append(f"- **{n}**: {m.get('skill_label')} (version {m.get('skill_version')}, code {(m.get('skill_fingerprint') or '')[:12]}), "
                   f"{m.get('n_runs')} runs, stages {m.get('stages')}, corners held out of the fit: "
                   f"{'n/a' if arm is None else arm}{' (this skill has no such flag)' if not (m.get('capabilities') or {}).get('box_fit_fit_flag') else ''}, "
                   f"harness {m.get('harness_version')}")
    if warnings:
        out += ['', '**Warnings**', ''] + [f'- {w}' for w in warnings]
    out += ['', 'Marks: `+` clear gain, `-` clear loss, `=` no clear change (paired 95 % interval of the later side minus the earlier one, scenes resampled), '
            'blank: the metric has no better direction. `(n=...)` shows how many runs counted when a metric does not apply to all of them.', '']
    out += ['## Accuracy, all runs', '', metric_table(names, rows_by_side, ACCURACY, resamples), '']
    for key in PER_PERT:
        out += [f'## {BY_KEY[key][2]}, by perturbation', '', by_pert_table(names, rows_by_side, key, perts, resamples), '']
    out += ['## Decisions, all runs', '', metric_table(names, rows_by_side, DECISION, resamples), '']
    out += ['## Naming of injected corners, all runs', '', metric_table(names, rows_by_side, NAMING, resamples), '']
    for n, rows in zip(names, rows_by_side):
        out += [f'## {n}: injected corners that were fitted and that the skill names, by number of fitted points (that side\'s own count)', '',
                naming_table(n, rows, 'outlier_recall', resamples), '']
    for n, rows in zip(names, rows_by_side):
        out += [f'## {n}: the same, only runs in which leave-one-out was computed', '', naming_table(n, rows, 'outlier_recall_computed', resamples), '']
    for pert in [p for p in RECALL_PERTS if p in perts]:
        out += [f'## Decisions on {pert}', '', metric_table(names, rows_by_side, ['flag_rate', 'outlier_recall', 'outlier_recall_common', 'outlier_false_alarm',
                                                                                'check_inj_flagged', 'check_inj_check_failed', 'corner_err_diag_med', 'rot_err_deg_med',
                                                                                'abs_log_wh_med', 'abs_log_dh_med', 'placement_m_med'],
                                                      resamples, lambda r, p=pert: r['pert'] == p), '']
    out += ['## Time', '', metric_table(names, rows_by_side, TIME, resamples), '']
    return '\n'.join(out)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    p.add_argument('--side', action='append', required=True, metavar='NAME=PATH', help='A results.json (or its folder); the first is the reference')
    p.add_argument('--out', help='Write the markdown here (default: print)')
    p.add_argument('--resamples', type=int, default=stats.DEFAULT_RESAMPLES)
    a = p.parse_args(argv)
    if len(a.side) < 2:
        p.error('at least two --side arguments are needed')
    sides = [parse_side(s) for s in a.side]
    warnings = []
    for (na, ra), (nb, rb) in zip(sides, sides[1:]):
        warnings += [f'{na} / {nb}: {w}' for w in cr.meta_warnings(ra['meta'], rb['meta'])]
    for w in warnings:
        print('warning: ' + w, file=sys.stderr)
    text = render(sides, a.resamples, warnings)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_bytes(text.encode('utf-8'))
    else:
        print(text)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
