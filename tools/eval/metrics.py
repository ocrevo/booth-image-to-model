"""Per-run metrics against the ground truth, and the layered aggregate table. numpy and the harness's camgeom only.

A run is one scene under one perturbation, handed to a skill folder. `evaluate_run` turns the skill's outputs (the box_fit result,
compile_report.json, spec.json) and the truth (case.json) into one flat row. `aggregate` summarises rows by layer with
bootstrap 95 % intervals (stats.py). Nothing here imports the skill.

Layers:
  camera     corner error as a share of the image diagonal (best over the symmetric relabellings of the box), horizontal
             field-of-view error, camera rotation error
  scale      |ln| error of W/H and of D/H (scale-free), relative focal error; absolute W, D and H error in percent (what a wrong
             assumed dimension does: the corners fix the proportions, only the stated dimension fixes the metres)
  parts      floor-part placement error in metres and as a share of the true W
  decision   does the skill flag bad fits? flag rate, share of runs that really are bad, accuracy, sensitivity (recall), precision,
             false-alarm rate, flags on defective and on valid input
  outliers   share of injected bad corners the skill names, false alarms, how often the worst-fitting point is an injected one
  runtime    seconds in box_fit.py and in boothctl compile (compile fits the camera itself and is the production path)
  contract   box_fit.py output is one JSON object; the standalone fit and the fit inside compile agree

"Really bad" is an evaluation convention, not a specification: corner error above BAD_CORNER_DIAG of the diagonal, or a W/H or
D/H proportion off by more than BAD_RATIO_LOG in |ln|. It is scale-free on purpose (it did not change in harness 1.1); a wrong
absolute scale is a separate label, `bad_scale` (a W, D or H more than BAD_SCALE_ERR off).

Which injected corners can be named (harness 1.1). Leave-one-out only looks at corners that took part in the fit, so an injected
corner that the run held out as a check corner (`fit: false`) can never be named; counting it lowers the recall by construction.
`outlier_recall` therefore counts only injected corners that were fitted in that run; the corners that were held out are reported
apart (`check_inj_*`: was the run flagged, did it end as check_failed). The harness-1.0 definition, all injected corners in the
denominator, is kept as `outlier_recall_all_injected`, and `worst_hit_rate` likewise has a legacy twin `worst_hit_rate_all_points`.
Recall is also cut by the number of fitted points (`n_fit_bin`), because leave-one-out needs spare equations.
"""
from __future__ import annotations
import itertools
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import camgeom
import stats

BAD_CORNER_DIAG = 0.015
BAD_RATIO_LOG = 0.10
BAD_SCALE_ERR = 0.10                                          # an absolute W, D or H more than 10 % off the truth
N_FIT_BINS = ('<=6', '7', '8', '9+')
SYMMETRIES = [(1, 1), (-1, 1), (1, -1), (-1, -1)]
LABELS = list(itertools.product((-1, 1), (-1, 1), (0, 1)))     # (x sign, y sign, top?) of the 8 box corners


def box_corner(dims, floor_z, label, symmetry=(1, 1)):
    a, b, top = label
    return [a * symmetry[0] * dims['W'] / 2, b * symmetry[1] * dims['D'] / 2, dims['H'] if top else floor_z]


def corner_error(case, camera, dims):
    """Mean pixel distance of the 8 fitted box corners from the true ones, best over the four horizontal symmetries.

    -> {'px', 'diag_frac', 'symmetry'} or None when the fit lacks a camera or a dimension."""
    if not camera or not dims or any(not _num(dims.get(k)) or dims[k] <= 0 for k in 'WDH'):
        return None
    w, h = case['image']['width'], case['image']['height']
    diag = math.hypot(w, h)
    true_px = [camgeom.project_or_none(box_corner(case['dims_m'], case['floor_z'], lb), case['camera'], w, h) for lb in LABELS]
    best = None
    for sym in SYMMETRIES:
        total, ok = 0.0, True
        for lb, t in zip(LABELS, true_px):
            f = camgeom.project_or_none(box_corner(dims, case['floor_z'], lb, sym), camera, w, h)
            if f is None or t is None:
                ok = False
                break
            total += math.hypot(f[0] - t[0], f[1] - t[1])
        if ok and (best is None or total / 8 < best[0]):
            best = (total / 8, sym)
    if best is None:
        return None
    return {'px': best[0], 'diag_frac': best[0] / diag, 'symmetry': list(best[1])}


def camera_errors(case, camera):
    if not camera:
        return None
    try:
        w = case['image']['width']
        f_true, f_fit = camgeom.focal_px(case['camera'], w), camgeom.focal_px(camera, w)
        return {'fov_err_deg': abs(camgeom.hfov_deg(camera) - camgeom.hfov_deg(case['camera'])),
                'rot_err_deg': camgeom.rotation_angle_deg(case['camera'], camera),
                'focal_rel_err': abs(f_fit / f_true - 1.0)}
    except (KeyError, ValueError, ZeroDivisionError, TypeError):
        return None


def ratio_errors(case, dims):
    """Signed ln errors of W/H and D/H of the fitted dimensions against the truth; None if a dimension is missing."""
    if not dims or any(not _num(dims.get(k)) or dims[k] <= 0 for k in 'WDH'):
        return None
    t = case['dims_m']
    return {'log_wh': math.log(dims['W'] / dims['H']) - math.log(t['W'] / t['H']),
            'log_dh': math.log(dims['D'] / dims['H']) - math.log(t['D'] / t['H'])}


def part_centres(spec, part_ids):
    """Footprint centre (x, y) of each part: the middle of the union of the boxes and upright cylinders it was built from.

    Parts are the compiler's macros, whose pieces are named <part id>_<piece>. Tilted pieces (grass blades) are skipped."""
    out = {}
    for pid in part_ids:
        xs, ys = [], []
        for c in spec.get('components', []):
            if not (c['id'] == pid or c['id'].startswith(pid + '_')):
                continue
            rot = c.get('rotation_deg') or [0, 0, 0]
            if abs(rot[0]) > 1e-6 or abs(rot[1]) > 1e-6:
                continue
            loc = c['location']
            if c['kind'] == 'box':
                sx, sy = c['params']['size'][0], c['params']['size'][1]
                if round(abs(rot[2]) / 90) % 2 == 1:
                    sx, sy = sy, sx
                hx, hy = sx / 2, sy / 2
            elif c['kind'] == 'cylinder':
                hx = hy = c['params']['radius']
            else:
                continue
            xs += [loc[0] - hx, loc[0] + hx]
            ys += [loc[1] - hy, loc[1] + hy]
        if xs:
            out[pid] = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
    return out


def placement_errors(case, spec):
    """Distance in metres between each built part's centre and its true floor position (parts the compiler dropped count as missing)."""
    if not spec or not case['parts']:
        return None
    centres = part_centres(spec, [p['id'] for p in case['parts']])
    errs = [math.hypot(centres[p['id']][0] - p['center_m'][0], centres[p['id']][1] - p['center_m'][1]) for p in case['parts'] if p['id'] in centres]
    if not errs:
        return {'median_m': None, 'max_m': None, 'share_w': None, 'missing': len(case['parts'])}
    errs.sort()
    med = errs[len(errs) // 2] if len(errs) % 2 else (errs[len(errs) // 2 - 1] + errs[len(errs) // 2]) / 2
    return {'median_m': med, 'max_m': errs[-1], 'share_w': med / case['dims_m']['W'], 'missing': len(case['parts']) - len(errs)}


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def n_fit_bin(n):
    """Label of the number of fitted points for the recall-by-redundancy cut."""
    if not isinstance(n, (int, float)) or isinstance(n, bool):
        return None
    return '<=6' if n <= 6 else ('7' if n == 7 else ('8' if n == 8 else '9+'))


def withheld_ids(fit, pert):
    """Ids of the corners this run kept out of the fit (`fit: false`). Only when the skill honours the flag: a skill without
    check corners (no n_check / check_points in its output) fitted everything it was given, whatever the harness sent."""
    if isinstance(fit, dict) and not ('n_check' in fit or 'check_points' in fit):
        return set()
    return {pid for pid, v in ((pert or {}).get('fit_flags') or {}).items() if v is False}


def absolute_errors(case, dims):
    """Signed relative errors of the fitted W, D and H against the true ones (fit / truth - 1); None if a dimension is missing."""
    if not dims or any(not _num(dims.get(k)) or dims[k] <= 0 for k in 'WDH'):
        return None
    return {k: dims[k] / case['dims_m'][k] - 1.0 for k in 'WDH'}


def gate_decision(fit, report, failed):
    """-> (flagged, source). The skill flags a fit when its quality gate says review (newer skills) or, for older ones, when the
    box_fit status is anything but fitted. A run with no usable fit is a refusal and counts as flagged."""
    if failed:
        return True, 'no_fit'
    if report and isinstance(report.get('quality_gate'), dict):
        return report['quality_gate'].get('status') != 'pass', 'quality_gate'
    return (fit or {}).get('status') != 'fitted', 'fit_status'


def evaluate_run(case, pert, fit, report, spec, timings=None, failure=None):
    """One flat metrics row. `pert` is perturb.apply(...) (or None for no perturbation), `fit` the box_fit result, `report`
    compile_report.json, `spec` spec.json (each may be None)."""
    timings = timings or {}
    pert = pert or {}
    injected = pert.get('injected') or {'bad_ids': [], 'outlier_ids': [], 'mislabel_ids': []}
    camera = (fit or {}).get('camera')
    dims = (fit or {}).get('dims_m')
    usable = bool(camera) and bool(dims) and all(_num((dims or {}).get(k)) for k in 'WDH') and (fit or {}).get('status') != 'blocked'
    failed = not usable
    row = {'case_id': case['case_id'], 'booth_type': case['booth_type'], 'pert': pert.get('name'), 'dims_mode': pert.get('dims_mode'),
           'sigma_px': pert.get('sigma_px'),
           'n_points': len(case['corners']), 'n_truth_check': len(case['check_ids']), 'n_injected': len(injected.get('bad_ids', [])),
           'failed': failed, 'failure': failure, 'fit_status': (fit or {}).get('status'),
           'compile_status': (report or {}).get('status') if report else None}
    for k in ('rms_px', 'max_px', 'n_fit', 'n_check', 'check_median_px', 'check_max_px', 'loo_median_px', 'loo_max_px', 'loo_reliable'):
        row[k] = (fit or {}).get(k)
    n_fit_eff = row['n_fit'] if _num(row['n_fit']) else len(case['corners'])      # a skill without n_fit fitted every corner
    row['n_fit_eff'], row['n_fit_bin'] = n_fit_eff, n_fit_bin(n_fit_eff)
    row['check_corners_arm'] = pert.get('check_corners')
    row['crop'] = 'cropped' if case['image'].get('cropped') else 'full frame'
    # leave-one-out counts as computed when the fit carries numbers for it (round-2 skills report null when it is not reliable)
    row['loo_computed'] = (1.0 if _num(row['loo_median_px']) else 0.0) if (fit and 'loo_median_px' in fit) else None
    gate = (report or {}).get('quality_gate') if report else None
    row['gate_status'] = gate.get('status') if isinstance(gate, dict) else None
    row['gate_reasons'] = list(gate.get('reasons', [])) if isinstance(gate, dict) else None
    reported = (fit or {}).get('outliers')
    row['reported_outliers'] = list(reported) if isinstance(reported, list) else None
    ae = absolute_errors(case, dims) if usable else None
    ce = corner_error(case, camera, dims) if usable else None
    cam = camera_errors(case, camera) if usable else None
    rat = ratio_errors(case, dims) if usable else None
    pl = placement_errors(case, spec) if spec else None
    row.update(corner_err_px=ce['px'] if ce else None, corner_err_diag=ce['diag_frac'] if ce else None,
               symmetry=ce['symmetry'] if ce else None,
               fov_err_deg=cam['fov_err_deg'] if cam else None, rot_err_deg=cam['rot_err_deg'] if cam else None,
               focal_rel_err=cam['focal_rel_err'] if cam else None,
               log_wh=rat['log_wh'] if rat else None, log_dh=rat['log_dh'] if rat else None,
               abs_log_wh=abs(rat['log_wh']) if rat else None, abs_log_dh=abs(rat['log_dh']) if rat else None,
               W_fit=dims.get('W') if usable else None, D_fit=dims.get('D') if usable else None, H_fit=dims.get('H') if usable else None,
               w_err=ae['W'] if ae else None, d_err=ae['D'] if ae else None, h_err=ae['H'] if ae else None,
               abs_w_err=abs(ae['W']) if ae else None, abs_d_err=abs(ae['D']) if ae else None, abs_h_err=abs(ae['H']) if ae else None,
               abs_dim_err_max=max(abs(v) for v in ae.values()) if ae else None,
               placement_median_m=pl['median_m'] if pl else None, placement_max_m=pl['max_m'] if pl else None,
               placement_share_w=pl['share_w'] if pl else None, parts_missing=pl['missing'] if pl else None)
    bad = failed or (ce is None) or ce['diag_frac'] > BAD_CORNER_DIAG or (rat is not None and max(abs(rat['log_wh']), abs(rat['log_dh'])) > BAD_RATIO_LOG)
    flagged, source = gate_decision(fit, report, failed)
    row.update(bad_fit=bool(bad), flagged=bool(flagged), flag_source=source)
    row['bad_scale'] = bool(ae is not None and row['abs_dim_err_max'] > BAD_SCALE_ERR)
    bad_ids = set(injected.get('bad_ids', []))
    held_out = withheld_ids(fit, pert)
    bad_fitted, bad_check = bad_ids - held_out, bad_ids & held_out
    row['n_withheld'], row['n_injected_fitted'], row['n_injected_check'] = len(held_out), len(bad_fitted), len(bad_check)
    checks = [c for c in ((fit or {}).get('scale_checks') or []) if isinstance(c, dict)]
    row['scale_check_n'] = len(checks)
    row['scale_check_ok'] = (1.0 if all(c.get('ok') is True for c in checks) else 0.0) if checks else None
    row['scale_check_ratio'] = checks[0].get('ratio') if checks and _num(checks[0].get('ratio')) else None
    row['valid_input'] = bool(not bad_ids and pert.get('dims_mode') in (None, 'one', 'all_exact', 'reply'))
    row['defective_input'] = bool(bad_ids or pert.get('dims_mode') in ('all_guess', 'wrong_one'))
    all_pts = [p for p in (fit or {}).get('points', []) if isinstance(p, dict) and _num(p.get('err_px'))]
    fitted_pts = [p for p in all_pts if p.get('fit') is not False]               # the check corners of the points list carry fit: false
    row['worst_point'] = max(fitted_pts, key=lambda p: p['err_px'])['id'] if fitted_pts else None
    row['worst_point_all'] = max(all_pts, key=lambda p: p['err_px'])['id'] if all_pts else None
    row['worst_hit'] = (1.0 if row['worst_point'] in bad_fitted else 0.0) if (bad_fitted and fitted_pts) else None
    row['worst_hit_all_points'] = (1.0 if row['worst_point_all'] in bad_ids else 0.0) if (bad_ids and all_pts) else None
    rep = set(row['reported_outliers'] or [])
    known = row['reported_outliers'] is not None
    row['outlier_recall'] = (len(rep & bad_fitted), len(bad_fitted)) if (bad_fitted and known) else None
    row['outlier_recall_all_injected'] = (len(rep & bad_ids), len(bad_ids)) if (bad_ids and known) else None
    # injected corners that are fitted in every arm (the scene's own check corners are the only ones an arm can hold out)
    common = bad_ids - set(case.get('check_ids') or [])
    row['outlier_recall_common'] = (len(rep & common), len(common)) if (common and known) else None
    row['outlier_recall_computed'] = (len(rep & bad_fitted), len(bad_fitted)) if (bad_fitted and known and row['loo_computed'] == 1.0) else None
    row['check_inj_flagged'] = (1.0 if flagged else 0.0, 1.0) if bad_check else None
    row['check_inj_check_failed'] = (1.0 if row['fit_status'] == 'check_failed' else 0.0, 1.0) if bad_check else None
    row['outlier_false_alarm'] = (len(rep - bad_ids), max(1, len(case['corners']) - len(bad_ids))) if row['reported_outliers'] is not None else None
    row['clean_outlier_report'] = ((1.0 if rep else 0.0), 1.0) if (not bad_ids and row['reported_outliers'] is not None) else None
    row['fit_s'], row['compile_s'] = timings.get('fit_s'), timings.get('compile_s')
    # The production path is `compile` (it fits the camera itself); box_fit.py alone is only counted when compile did not run.
    row['total_s'] = row['compile_s'] if row['compile_s'] is not None else row['fit_s']
    return row


# ---------------------------------------------------------------------------------------------------------------- aggregation
def _val(key):
    return lambda r: r.get(key)


def _flag_and(want_bad):
    def get(r):
        if r['bad_fit'] != want_bad:
            return None
        return (1.0 if r['flagged'] else 0.0, 1.0)
    return get


METRICS = [
    # (key, layer, label, statistic, extractor, unit)
    ('corner_err_diag_med', 'camera', 'Corner error, share of image diagonal (median)', 'median', _val('corner_err_diag'), 'percent'),
    ('corner_err_diag_mean', 'camera', 'Corner error, share of image diagonal (mean)', 'mean', _val('corner_err_diag'), 'percent'),
    ('fov_err_deg_med', 'camera', 'Field-of-view error, degrees (median)', 'median', _val('fov_err_deg'), 'deg'),
    ('rot_err_deg_med', 'camera', 'Camera rotation error, degrees (median)', 'median', _val('rot_err_deg'), 'deg'),
    ('abs_log_wh_med', 'scale', 'Absolute ln error of W/H (median)', 'median', _val('abs_log_wh'), 'log'),
    ('abs_log_dh_med', 'scale', 'Absolute ln error of D/H (median)', 'median', _val('abs_log_dh'), 'log'),
    ('abs_log_wh_mean', 'scale', 'Absolute ln error of W/H (mean)', 'mean', _val('abs_log_wh'), 'log'),
    ('abs_log_dh_mean', 'scale', 'Absolute ln error of D/H (mean)', 'mean', _val('abs_log_dh'), 'log'),
    ('focal_rel_err_med', 'scale', 'Focal length error, relative (median)', 'median', _val('focal_rel_err'), 'percent'),
    ('abs_w_err_med', 'scale', 'Absolute error of W, percent of the true W (median)', 'median', _val('abs_w_err'), 'percent'),
    ('abs_d_err_med', 'scale', 'Absolute error of D, percent of the true D (median)', 'median', _val('abs_d_err'), 'percent'),
    ('abs_h_err_med', 'scale', 'Absolute error of H, percent of the true H (median)', 'median', _val('abs_h_err'), 'percent'),
    ('abs_dim_err_max_med', 'scale', 'Largest absolute error of W, D and H, percent (median)', 'median', _val('abs_dim_err_max'), 'percent'),
    ('placement_m_med', 'parts', 'Floor-part placement error, metres (median of runs)', 'median', _val('placement_median_m'), 'm'),
    ('placement_share_w_med', 'parts', 'Floor-part placement error, share of true W (median of runs)', 'median', _val('placement_share_w'), 'percent'),
    ('flag_rate', 'decision', 'Runs flagged for review', 'rate', lambda r: 1.0 if r['flagged'] else 0.0, 'percent'),
    ('bad_rate', 'decision', 'Runs whose fit is really bad (truth)', 'rate', lambda r: 1.0 if r['bad_fit'] else 0.0, 'percent'),
    ('decision_accuracy', 'decision', 'Flag agrees with truth', 'rate', lambda r: 1.0 if r['flagged'] == r['bad_fit'] else 0.0, 'percent'),
    ('sensitivity', 'decision', 'Really bad runs that are flagged', 'ratio', _flag_and(True), 'percent'),
    ('false_alarm_rate', 'decision', 'Good runs that are flagged', 'ratio', _flag_and(False), 'percent'),
    ('flag_on_injected', 'decision', 'Runs with an injected bad corner that are flagged', 'ratio',
     lambda r: (1.0 if r['flagged'] else 0.0, 1.0) if r['n_injected'] else None, 'percent'),
    ('flag_on_valid_input', 'decision', 'Runs with valid input (no injected error, right dimensions) that are flagged', 'ratio',
     lambda r: (1.0 if r['flagged'] else 0.0, 1.0) if (r['valid_input'] and not r['n_injected']) else None, 'percent'),
    ('gate_precision', 'decision', 'Flagged runs whose fit is really bad (precision)', 'ratio',
     lambda r: (1.0 if r['bad_fit'] else 0.0, 1.0) if r['flagged'] else None, 'percent'),
    ('flag_on_defective', 'decision', 'Runs with a defective input (bad corner, guessed or wrong dimensions) that are flagged', 'ratio',
     lambda r: (1.0 if r['flagged'] else 0.0, 1.0) if r.get('defective_input') else None, 'percent'),
    ('defect_precision', 'decision', 'Flagged runs whose input is defective', 'ratio',
     lambda r: (1.0 if r.get('defective_input') else 0.0, 1.0) if r['flagged'] else None, 'percent'),
    ('bad_scale_rate', 'decision', 'Runs whose W, D or H is off by more than 10 % (truth)', 'rate',
     lambda r: None if r.get('bad_scale') is None else (1.0 if r['bad_scale'] else 0.0), 'percent'),
    ('flag_on_bad_scale', 'decision', 'Runs whose W, D or H is off by more than 10 % that are flagged', 'ratio',
     lambda r: (1.0 if r['flagged'] else 0.0, 1.0) if r.get('bad_scale') else None, 'percent'),
    ('scale_check_ok_rate', 'decision', 'Runs whose scale check (cafe table) passes', 'rate', _val('scale_check_ok'), 'percent'),
    ('flag_on_wrong_anchor', 'decision', 'Runs with a wrong assumed dimension that are flagged', 'ratio',
     lambda r: (1.0 if r['flagged'] else 0.0, 1.0) if r.get('dims_mode') == 'wrong_one' else None, 'percent'),
    ('failure_rate', 'decision', 'Runs with no usable fit', 'rate', lambda r: 1.0 if r['failed'] else 0.0, 'percent'),
    ('outlier_recall', 'outliers', 'Injected bad corners that were fitted and that the skill names (recall)', 'ratio', _val('outlier_recall'), 'percent'),
    ('outlier_recall_computed', 'outliers', 'The same, only in runs where leave-one-out was computed', 'ratio', _val('outlier_recall_computed'), 'percent'),
    ('outlier_recall_common', 'outliers', 'Injected corners that are fitted in every arm (not a check corner of the scene) and that the skill names', 'ratio',
     _val('outlier_recall_common'), 'percent'),
    ('outlier_recall_all_injected', 'outliers', 'Harness 1.0 definition: all injected corners in the denominator, held-out ones count as missed',
     'ratio', _val('outlier_recall_all_injected'), 'percent'),
    ('loo_computed_rate', 'outliers', 'Runs in which leave-one-out was computed', 'rate', _val('loo_computed'), 'percent'),
    ('outlier_false_alarm', 'outliers', 'Good corners the skill names', 'ratio', _val('outlier_false_alarm'), 'percent'),
    ('clean_outlier_report', 'outliers', 'Runs without injected errors in which the skill names a corner', 'ratio', _val('clean_outlier_report'), 'percent'),
    ('check_inj_flagged', 'outliers', 'Runs whose injected corner was held out (check corner) that are flagged', 'ratio',
     _val('check_inj_flagged'), 'percent'),
    ('check_inj_check_failed', 'outliers', 'Runs whose injected corner was held out that end as check_failed', 'ratio',
     _val('check_inj_check_failed'), 'percent'),
    ('worst_hit_rate', 'outliers', 'Worst-fitting fitted point is an injected bad corner', 'rate', _val('worst_hit'), 'percent'),
    ('worst_hit_rate_all_points', 'outliers', 'Harness 1.0 definition: the worst of all listed points (check corners too) is an injected one',
     'rate', _val('worst_hit_all_points'), 'percent'),
    ('fit_s_med', 'runtime', 'box_fit.py seconds (median)', 'median', _val('fit_s'), 's'),
    ('compile_s_med', 'runtime', 'boothctl compile seconds (median; it fits the camera itself)', 'median', _val('compile_s'), 's'),
    ('total_s_mean', 'runtime', 'Compile path seconds (mean)', 'mean', _val('total_s'), 's'),
    ('stdout_single_json', 'contract', 'box_fit.py prints exactly one JSON object on stdout', 'rate', _val('stdout_single_json'), 'percent'),
    ('fit_consistent', 'contract', 'box_fit.py and the fit inside compile agree on W, D and H', 'rate', _val('fit_consistent'), 'percent'),
]
BY_KEY = {m[0]: m for m in METRICS}
LAYER_ORDER = ['camera', 'scale', 'parts', 'decision', 'outliers', 'runtime', 'contract']
HEADLINE = ['corner_err_diag_med', 'rot_err_deg_med', 'abs_log_wh_med', 'abs_log_dh_med', 'placement_m_med', 'flag_rate',
            'decision_accuracy', 'sensitivity', 'gate_precision', 'false_alarm_rate', 'flag_on_valid_input', 'outlier_recall', 'total_s_mean']
# metrics cut by the number of fitted points (leave-one-out needs spare equations) and by cropped pictures
CUT_KEYS = ['outlier_recall', 'outlier_recall_computed', 'outlier_false_alarm', 'loo_computed_rate', 'flag_rate', 'flag_on_valid_input',
            'sensitivity', 'gate_precision', 'corner_err_diag_med', 'abs_log_dh_med']


def aggregate(rows, resamples=stats.DEFAULT_RESAMPLES, seed=stats.BOOTSTRAP_SEED, keys=None):
    """{metric key: {'n', 'value', 'lo', 'hi'}} over the given rows (only the metrics in `keys` when given)."""
    return {key: stats.summarize(rows, get, kind, resamples, seed) for key, _layer, _label, kind, get, _unit in METRICS
            if keys is None or key in keys}


def recall_by_bin(rows, key='outlier_recall', resamples=stats.DEFAULT_RESAMPLES, seed=stats.BOOTSTRAP_SEED):
    """{perturbation: {bin label or 'all': summary}} of one ratio metric, cut by the number of fitted points (N_FIT_BINS).

    Only perturbations in which the metric exists at all (they inject an error that can be named) get a line."""
    get = BY_KEY[key][4]
    out = {}
    for name in sorted({r['pert'] for r in rows if r.get('pert') and get(r) is not None}, key=str):
        sub = [r for r in rows if r.get('pert') == name]
        out[name] = {'all': stats.summarize(sub, get, 'ratio', resamples, seed)}
        for label in N_FIT_BINS:
            out[name][label] = stats.summarize([r for r in sub if r.get('n_fit_bin') == label], get, 'ratio', resamples, seed)
    return out


def aggregate_by(rows, field, resamples=stats.DEFAULT_RESAMPLES, seed=stats.BOOTSTRAP_SEED, keys=None):
    """Aggregates per value of a row field (perturbation name, booth type...), plus 'all'. `keys` limits the metrics computed."""
    out = {'all': aggregate(rows, resamples, seed, keys)}
    for value in sorted({r[field] for r in rows if r.get(field) is not None}, key=str):
        out[str(value)] = aggregate([r for r in rows if r.get(field) == value], resamples, seed, keys)
    return out
