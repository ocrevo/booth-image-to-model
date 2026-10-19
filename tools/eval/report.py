"""Markdown rendering of an evaluation result. Standard library only."""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics

BY_KEY = {m[0]: m for m in metrics.METRICS}
ACCURACY_COLUMNS = ['corner_err_diag_med', 'rot_err_deg_med', 'fov_err_deg_med', 'abs_log_wh_med', 'abs_log_dh_med', 'placement_m_med']
DECISION_COLUMNS = ['flag_rate', 'decision_accuracy', 'sensitivity', 'gate_precision', 'false_alarm_rate', 'failure_rate', 'outlier_recall',
                    'worst_hit_rate', 'total_s_mean']
DIMENSION_COLUMNS = ['abs_w_err_med', 'abs_d_err_med', 'abs_h_err_med', 'abs_dim_err_max_med', 'abs_log_wh_med', 'abs_log_dh_med', 'flag_rate']
CUT_COLUMNS = ['outlier_recall', 'outlier_recall_computed', 'outlier_false_alarm', 'loo_computed_rate', 'flag_rate', 'flag_on_valid_input',
               'sensitivity', 'gate_precision', 'corner_err_diag_med', 'abs_log_dh_med']
TOPOLOGY_COLUMNS = ['topology_flag_rate', 'topology_catchable', 'topology_flag_any', 'topology_added_flag', 'topology_false_alarm', 'topology_added_false_alarm', 'topo_type_fail_rate', 'topo_wall_fail_rate',
                    'topo_camera_fail_rate', 'topo_closed_warn_rate', 'topo_not_checked_rate', 'walls_on_open_declared_rate', 'walls_dropped_rate']
SHORT = {
    'topology_flag_rate': 'Wrong runs flagged, topology reason', 'topology_catchable': 'Wrong runs catchable at all (ceiling)', 'topology_flag_any': 'Wrong runs flagged, any reason',
    'topology_added_flag': 'Wrong runs flagged only by topology', 'topology_added_false_alarm': 'Right runs flagged only by topology',
    'topology_false_alarm': 'Right runs flagged, topology reason', 'topo_type_fail_rate': 'type check: fail', 'topo_wall_fail_rate': 'wall check: fail',
    'topo_camera_fail_rate': 'camera check: fail', 'topo_closed_warn_rate': 'closed side, no wall: warn', 'topo_not_checked_rate': 'nothing checked',
    'walls_on_open_declared_rate': 'Wall on a declared open side', 'walls_dropped_rate': 'Wall left out (open side)',
    'corner_err_diag_med': 'Corner err (% diag)', 'rot_err_deg_med': 'Rotation err (deg)', 'fov_err_deg_med': 'FOV err (deg)',
    'abs_log_wh_med': 'abs ln W/H', 'abs_log_dh_med': 'abs ln D/H', 'placement_m_med': 'Placement (m)',
    'flag_rate': 'Flagged', 'decision_accuracy': 'Flag = truth', 'sensitivity': 'Bad runs flagged', 'false_alarm_rate': 'Good runs flagged',
    'failure_rate': 'No fit', 'outlier_recall': 'Outliers named', 'worst_hit_rate': 'Worst point is bad', 'total_s_mean': 'Seconds (mean)',
    'gate_precision': 'Flagged runs that are bad', 'abs_w_err_med': 'abs W err', 'abs_d_err_med': 'abs D err', 'abs_h_err_med': 'abs H err',
    'abs_dim_err_max_med': 'largest of W, D, H', 'outlier_recall_computed': 'Named (LOO computed)', 'outlier_false_alarm': 'Good corners named',
    'loo_computed_rate': 'LOO computed', 'flag_on_valid_input': 'Valid input flagged',
}


def number(x, unit):
    if unit == 'percent':
        return f'{x * 100:.2f}%' if abs(x) < 0.1 else f'{x * 100:.1f}%'
    if unit == 'deg':
        return f'{x:.2f}'
    if unit == 's':
        return f'{x:.2f}'
    return f'{x:.3g}'


def arm_text(meta, caps):
    arm = meta.get('check_corners')
    if arm is None:
        return 'n/a'
    if arm and caps and not caps.get('box_fit_fit_flag'):
        return f'{arm} (not used: this skill has no check corners and fits every corner it is given)'
    return f'{arm} (the check corners of each scene are held out)' if arm else '0 (every corner is fitted)'


def cell(summary, unit, with_ci=True):
    if not summary or summary.get('value') is None:
        return '-'
    text = number(summary['value'], unit)
    if with_ci and summary.get('lo') is not None:
        text += f" [{number(summary['lo'], unit)}, {number(summary['hi'], unit)}]"
    return text


def table(header, body):
    lines = ['| ' + ' | '.join(header) + ' |', '|' + '|'.join(' --- ' for _ in header) + '|']
    lines += ['| ' + ' | '.join(str(c) for c in row) + ' |' for row in body]
    return '\n'.join(lines)


def _n(summary):
    """' (n=..)' after a cell whose count is small enough to matter."""
    return f" (n={summary['n']})" if summary and summary.get('n') and summary['n'] < 40 else ''


def render(title, meta, aggregates):
    """aggregates: {'all': {...}, 'by_pert': {name: {...}}, 'by_booth_type': {...}} as produced by metrics.aggregate(_by)."""
    out = [f'# {title}', '']
    caps = meta.get('capabilities') or {}
    out += ['## Run', '', table(['Item', 'Value'], [
        ['Harness version', meta.get('harness_version')], ['Mode', meta.get('mode')], ['Suite', meta.get('suite')], ['Seed', meta.get('seed')],
        ['Suite fingerprint (sha256 of suite.json, first 16)', (meta.get('suite_sha256') or '')[:16]],
        ['Arm: corners held out of the fit (check corners)', arm_text(meta, caps)],
        ['Scale check (cafe table) handed to the skill', {True: 'yes', False: 'no'}.get(meta.get('scale_check'), 'n/a')],
        ['Generator version', meta.get('generator_version')],
        ['Scenes / runs', f"{meta.get('n_cases')} / {meta.get('n_runs')}"], ['Skill folder', meta.get('skill_label')],
        ['Skill version', meta.get('skill_version')], ['Skill commit', meta.get('skill_commit')],
        ['Skill code fingerprint (sha256, first 16)', (meta.get('skill_fingerprint') or '')[:16]],
        ['Capabilities', ', '.join(k for k, v in sorted(caps.items()) if v is True) or 'none of the newer ones'],
        ['box_fit seeds', meta.get('fit_seeds')], ['Stages', meta.get('stages')],
        ['Bootstrap', f"{meta.get('resamples')} resamples of whole scenes, 95 % percentile interval"],
        ['Python / numpy', f"{meta.get('python')} / {meta.get('numpy')}"], ['Command', f"`{meta.get('command')}`"],
    ]), '']
    overall = aggregates['all']
    out += ['## Headline (all runs)', '',
            table(['Metric', 'n', 'Value [95% CI]'], [[BY_KEY[k][2], overall[k]['n'], cell(overall[k], BY_KEY[k][5])] for k in metrics.HEADLINE]), '']
    for layer in metrics.LAYER_ORDER:
        rows = [m for m in metrics.METRICS if m[1] == layer]
        out += [f'## Layer: {layer}', '', table(['Metric', 'n', 'Value [95% CI]'], [[m[2], overall[m[0]]['n'], cell(overall[m[0]], m[5])] for m in rows]), '']
    by_pert = aggregates.get('by_pert') or {}
    if by_pert:
        names = [n for n in by_pert if n != 'all']
        out += ['## By perturbation: accuracy', '', table(['Perturbation'] + [SHORT[k] for k in ACCURACY_COLUMNS],
                [[n] + [cell(by_pert[n][k], BY_KEY[k][5]) for k in ACCURACY_COLUMNS] for n in names]), '']
        out += ['## By perturbation: decisions, outliers, time', '', table(['Perturbation'] + [SHORT[k] for k in DECISION_COLUMNS],
                [[n] + [cell(by_pert[n][k], BY_KEY[k][5]) for k in DECISION_COLUMNS] for n in names]), '']
        out += ['## By perturbation: absolute dimension errors (percent of the true value; a wrong assumed dimension shows up here, not in the ratios)',
                '', table(['Perturbation'] + [SHORT[k] for k in DIMENSION_COLUMNS],
                [[n] + [cell(by_pert[n][k], BY_KEY[k][5]) for k in DIMENSION_COLUMNS] for n in names]), '']
    if aggregates.get('by_grammar_type') or aggregates.get('by_topology_detail'):
        out += ['## Booth grammar (topology): what the gate does with mirrored labels, wrong open sides and right declarations', '',
                '"Wrong runs" are the runs of the grammar perturbations (mirror_fb, mirror_lr, swap_fb_lr, wrong_open_sides); "right runs" are all other runs, '
                'where the reader declares the true booth type and open sides. A column shows `-` when no run counted or when the skill has no such check '
                '(a skill without the booth grammar shows 0 % in the flag columns and `-` in the check columns).', '']
        names = [n for n in by_pert if n != 'all']
        out += ['### By perturbation', '', table(['Perturbation'] + [SHORT[k] for k in TOPOLOGY_COLUMNS],
                [[n] + [cell(by_pert[n].get(k), BY_KEY[k][5], with_ci=False) for k in TOPOLOGY_COLUMNS] for n in names]), '']
        by_detail = aggregates.get('by_topology_detail') or {}
        if by_detail:
            kinds = [n for n in by_detail if n != 'all']
            out += ['### By kind of mistake (wrong runs only; open_as_closed = an open side declared closed, closed_as_open = a closed side declared open)', '',
                    table(['Mistake'] + [SHORT[k] for k in TOPOLOGY_COLUMNS],
                          [[n] + [cell(by_detail[n].get(k), BY_KEY[k][5], with_ci=False) + _n(by_detail[n].get(k)) for k in TOPOLOGY_COLUMNS]
                           for n in kinds]), '']
        by_type = aggregates.get('by_grammar_type') or {}
        if by_type:
            kinds = [n for n in by_type if n != 'all']
            out += ['### By true booth type (the generator\'s own "inline" scenes have three open sides and count as peninsula here)', '',
                    table(['True type'] + [SHORT[k] for k in TOPOLOGY_COLUMNS],
                          [[n] + [cell(by_type[n].get(k), BY_KEY[k][5], with_ci=False) for k in TOPOLOGY_COLUMNS] for n in kinds]), '']
    by_fit = aggregates.get('by_n_fit') or {}
    if by_fit:
        names = [n for n in metrics.N_FIT_BINS if n in by_fit]
        out += ['## By number of fitted points (leave-one-out needs spare equations)', '',
                table(['Fitted points'] + [SHORT[k] for k in CUT_COLUMNS],
                      [[n] + [cell(by_fit[n][k], BY_KEY[k][5]) for k in CUT_COLUMNS] for n in names]), '']
    recall = aggregates.get('recall_by_n_fit') or {}
    if recall:
        out += ['## Injected corners that were fitted and that the skill names, by perturbation and number of fitted points', '',
                table(['Perturbation', 'all'] + [f'{b} fitted' for b in metrics.N_FIT_BINS],
                      [[n] + [cell(recall[n][b], 'percent') + (f" (n={recall[n][b]['n']})" if recall[n][b].get('n') else '')
                              for b in ['all'] + list(metrics.N_FIT_BINS)] for n in recall]), '']
    by_crop = aggregates.get('by_crop') or {}
    if by_crop:
        names = [n for n in by_crop if n != 'all']
        out += ['## By picture (the principal point of a cropped picture is off centre; the skill fits a centred camera)', '',
                table(['Picture'] + [SHORT[k] for k in CUT_COLUMNS],
                      [[n] + [cell(by_crop[n][k], BY_KEY[k][5], with_ci=False) for k in CUT_COLUMNS] for n in names]), '']
    by_type = aggregates.get('by_booth_type') or {}
    if by_type:
        names = [n for n in by_type if n != 'all']
        out += ['## By booth type (medians over all perturbations)', '', table(['Booth type'] + [SHORT[k] for k in ACCURACY_COLUMNS[:5]],
                [[n] + [cell(by_type[n][k], BY_KEY[k][5], with_ci=False) for k in ACCURACY_COLUMNS[:5]] for n in names]), '']
    out += ['## Reading these numbers', '',
            '- Intervals resample whole scenes (the rows of one scene are not independent).',
            '- A metric that a skill version does not produce (for example outlier names in a version without leave-one-out) shows `-`.',
            '- Outlier recall (harness 1.1) counts only injected corners that were fitted in that run: a corner held out as a check corner cannot be named '
            'by leave-one-out. Those runs are reported apart (flagged / check_failed). The harness 1.0 definition is the column "all injected" in results.json '
            '(`outlier_recall_all_injected`).',
            '- Absolute W, D and H errors are what a wrong stated dimension looks like: the ratios stay right and all three metres are off by about the same share.',
            '- "Really bad" is the harness convention in `tools/eval/metrics.py`: corner error above 1.5 % of the image diagonal, or W/H or D/H '
            'off by more than 0.10 in absolute ln. The flag is the quality gate of newer skills, or the box_fit status of older ones.',
            '- Booth grammar (harness 1.2): a run whose corner tokens are mirrored or whose declared open sides are wrong counts as really bad '
            '(it must be reviewed) whatever its fit residual; the topology reason columns count a flag only when the gate names a topology reason.',
            '- Seconds are wall time of the two command-line calls, including interpreter and import start-up, on the machine that ran them.',
            '- Synthetic scenes use a perfect pinhole camera and our own building blocks; see `tools/eval/README.md` for what that cannot show.', '']
    return '\n'.join(out)
