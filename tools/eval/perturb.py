"""Perturbations for the oracle mode: what a reader's mistakes look like, applied to exact ground-truth corners.

A perturbation never changes the truth in case.json. It produces the points a reader would hand to the skill (pixel noise,
displaced outliers, swapped corner labels), the dimensions the reader states (one anchor, or all of W/D/H, exact or guessed) and
the noisy floor pixels of the parts. Everything is seeded by (suite seed, case id, perturbation name), so a run can be repeated
on its own and does not depend on the order of the other runs. Standard library only.

Arms. `apply(case, name, check_corners)` decides how many of the case's held-out corners are sent with `fit: false`: 2 (the default)
holds out the case's own check corners (`case['check_ids']`: two when the scene has 8 or more clickable points, one with 7, none
with 6); 0 sends every corner as a fitted one. Only `fit_flags` differs between the arms: the perturbed pixels, the injected
errors and the stated dimensions are drawn from the same random stream whatever the arm, so two arms see the same input and
their rows can be paired by scene and perturbation.

Booth grammar (iteration I2). Every run also carries the topology the reader declares (`pert['topology']`): booth type, open sides and
cutaway sides in the words of grammar.py. For the nine perturbations of the `oracle-v1` suite, the `wrong_anchor_*` ones and `clean`
the declaration is the truth of the case (a reader that got the topology right); the three opt-in perturbations of the grammar model
what a reader gets wrong there, and nothing else about them is unusual (0.5 px noise, one exact anchor dimension):

  mirror_fb         every front/back corner token is swapped (D/2 <-> -D/2), consistently on all corners; the declared sides and the walls
                    are right. The reader's corner tokens and its stated sides then use opposite conventions.
  mirror_lr         the same for left/right (W/2 <-> -W/2).
  swap_fb_lr        both together: the box seen from the opposite side. A single swap is a reflection and leaves the fit with a large
                    residual (the old gate already flags it); both together are a half turn of the labels, a box looks the same after
                    it, the fit is perfect and the camera lands on the far side of the booth. Only the topology can reveal that.
  wrong_open_sides  one side is declared the wrong way round and the booth type is changed to the one that fits the declared sides; the
                    wall components stay as they are. A fair coin (case seed) picks the mistake, an open side declared closed
                    ('open_as_closed') or a closed side declared open ('closed_as_open'), then a side of that kind (an island has no
                    closed side, so it always gets the first).

A reader that mirrors the corner tokens and the declared sides together describes a mirror-image booth and cannot be told from a right
reading by any topology check, so it is not modelled. These draws use their own seeded stream, so no other input moves.
"""
from __future__ import annotations
import copy
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import camgeom
import grammar
from synth_generate import derive_seed

# outliers: that many corners are displaced by 15-60 px (or by 'outlier_px' when given) in a random direction.
# mislabel: that many pairs of corner tokens are swapped (left/right or front/back confusion).
# dims: 'one'       the case's anchor dimension is stated (exact); the other two must come from the corners
#       'all_exact' all of W, D, H stated and exact, all recorded as assumed (the web reader's habit, with a lucky guess)
#       'all_guess' all of W, D, H stated as assumed, rounded to 0.5 m after a random error of about 12 %
#       'wrong_one' the case's anchor dimension stated as assumed with a WRONG value: true value times (1 + anchor_err). This is the
#                   reader's real failure once it is asked to assume one dimension only: the corners fix the proportions, so W/H and
#                   D/H stay right while W, D and H are all off by about anchor_err (metrics.py reports the absolute errors).
PERTURBATIONS = {
    'clean':     {'sigma_px': 0.0, 'outliers': 0, 'mislabel': 0, 'dims': 'one'},
    'noise_1':   {'sigma_px': 1.0, 'outliers': 0, 'mislabel': 0, 'dims': 'one'},
    'noise_3':   {'sigma_px': 3.0, 'outliers': 0, 'mislabel': 0, 'dims': 'one'},
    'outlier_30': {'sigma_px': 0.5, 'outliers': 1, 'mislabel': 0, 'dims': 'one', 'outlier_px': 30.0},     # exactly one 30 px outlier
    'outlier_1': {'sigma_px': 0.5, 'outliers': 1, 'mislabel': 0, 'dims': 'one'},
    'outlier_2': {'sigma_px': 0.5, 'outliers': 2, 'mislabel': 0, 'dims': 'one'},
    'mislabel':  {'sigma_px': 0.5, 'outliers': 0, 'mislabel': 1, 'dims': 'one'},
    'all_exact': {'sigma_px': 0.5, 'outliers': 0, 'mislabel': 0, 'dims': 'all_exact'},
    'all_guess': {'sigma_px': 0.5, 'outliers': 0, 'mislabel': 0, 'dims': 'all_guess'},
    'wrong_anchor_m25': {'sigma_px': 0.5, 'outliers': 0, 'mislabel': 0, 'dims': 'wrong_one', 'anchor_err': -0.25},
    'wrong_anchor_m10': {'sigma_px': 0.5, 'outliers': 0, 'mislabel': 0, 'dims': 'wrong_one', 'anchor_err': -0.10},
    'wrong_anchor_p10': {'sigma_px': 0.5, 'outliers': 0, 'mislabel': 0, 'dims': 'wrong_one', 'anchor_err': 0.10},
    'wrong_anchor_p25': {'sigma_px': 0.5, 'outliers': 0, 'mislabel': 0, 'dims': 'wrong_one', 'anchor_err': 0.25},
    # booth grammar (I2); opt-in, never in a suite's own list. 'topology' names how the declared topology or the corner tokens are wrong.
    'mirror_fb': {'sigma_px': 0.5, 'outliers': 0, 'mislabel': 0, 'dims': 'one', 'topology': 'mirror_fb'},
    'mirror_lr': {'sigma_px': 0.5, 'outliers': 0, 'mislabel': 0, 'dims': 'one', 'topology': 'mirror_lr'},
    'wrong_open_sides': {'sigma_px': 0.5, 'outliers': 0, 'mislabel': 0, 'dims': 'one', 'topology': 'wrong_open_sides'},
    'swap_fb_lr': {'sigma_px': 0.5, 'outliers': 0, 'mislabel': 0, 'dims': 'one', 'topology': 'swap_fb_lr'},
}
TOPOLOGY_PERTURBATIONS = tuple(n for n, v in PERTURBATIONS.items() if v.get('topology'))
CHECK_CORNER_ARMS = (0, 2)
# Optional scale check (apply(..., scale_check=True)): the reader also marks a standard cafe table (the case's first round table, whose true
# height is 0.74 m) so that a wrong stated dimension can be caught from the picture. Cases without a round table get none.
SCALE_CHECK_HEIGHT_M, SCALE_CHECK_TOLERANCE = 0.74, 0.15
OUTLIER_MIN_PX, OUTLIER_MAX_PX = 15.0, 60.0
GUESS_LOG_SIGMA = 0.12


def guess_dim(rng, true_value):
    """A reader's rounded guess of a dimension: about 12 % error, then rounded to 0.5 m (at least 1 m)."""
    guess = true_value * math.exp(rng.gauss(0.0, GUESS_LOG_SIGMA))
    return max(1.0, round(guess * 2) / 2)


def _pick(rng, items, k):
    pool = list(items)
    out = []
    for _ in range(min(k, len(pool))):
        out.append(pool.pop(int(rng.random() * len(pool))))
    return out


def topology_for(case, name):
    """The topology the reader declares in this run and what is wrong with it, from the case truth and a stream of its own.

    -> {'kind': None | 'mirror_fb' | 'mirror_lr' | 'wrong_open_sides', 'detail': None | the kind | 'open_as_closed' | 'closed_as_open',
        'defect': bool, 'truth': {...}, 'declared': {...}, 'toggled_side': None | side}"""
    kind = PERTURBATIONS[name].get('topology')
    truth = grammar.truth_topology(case)
    declared = {'booth_type': truth['booth_type'], 'open_sides': list(truth['open_sides']), 'cutaway_sides': list(truth['cutaway_sides'])}
    detail, side = kind, None
    if kind == 'wrong_open_sides':
        rng = random.Random(derive_seed(case['suite_seed'], case['case_id'], 'topology', name))
        open_pool = [s for s in grammar.SIDES if s in truth['open_sides']]
        closed_pool = [s for s in grammar.SIDES if s not in truth['open_sides']]
        pool = (open_pool if rng.random() < 0.5 else closed_pool) or open_pool or closed_pool      # a fair coin between the two mistakes
        side = pool[int(rng.random() * len(pool))]
        declared['open_sides'], detail = grammar.toggle_side(truth['open_sides'], side)
        declared['booth_type'] = grammar.classify(declared['open_sides'])
    return {'kind': kind, 'detail': detail, 'defect': kind is not None, 'truth': truth, 'declared': declared, 'toggled_side': side}


def apply(case, name, check_corners=2, scale_check=False):
    """-> dict describing what the reader hands to the skill for this case under perturbation `name`.

    check_corners is the arm: 2 holds out the case's own check corners (`fit: false`), 0 holds nothing out (see the module doc).
    scale_check adds one cafe-table scale check when the case has a round table (drawn after everything else, so no other input moves)."""
    if name not in PERTURBATIONS:
        raise ValueError(f'unknown perturbation {name!r}; choose one of {sorted(PERTURBATIONS)}')
    if check_corners not in CHECK_CORNER_ARMS:
        raise ValueError(f'check_corners must be one of {CHECK_CORNER_ARMS}, not {check_corners!r}')
    spec = PERTURBATIONS[name]
    rng = random.Random(derive_seed(case['suite_seed'], case['case_id'], 'perturb', name))
    sigma = spec['sigma_px']
    points = []
    for c in case['corners']:
        u, v = c['uv_px']
        if sigma:
            u, v = u + rng.gauss(0.0, sigma), v + rng.gauss(0.0, sigma)
        points.append({'id': c['id'], 'uv_px': [u, v], 'xyz': list(c['xyz']), 'role': c['role']})
    ids = [p['id'] for p in points]
    outlier_ids, displacement = [], {}
    for pid in _pick(rng, ids, spec['outliers']):
        mag, ang = rng.uniform(OUTLIER_MIN_PX, OUTLIER_MAX_PX), rng.uniform(0.0, 2 * math.pi)
        mag = spec.get('outlier_px', mag)
        p = next(q for q in points if q['id'] == pid)
        p['uv_px'] = [p['uv_px'][0] + mag * math.cos(ang), p['uv_px'][1] + mag * math.sin(ang)]
        outlier_ids.append(pid)
        displacement[pid] = round(mag, 2)
    mislabel_ids = []
    if spec['mislabel']:
        levels = {}
        for p in points:
            levels.setdefault(p['xyz'][2], []).append(p)
        same = [group for group in levels.values() if len(group) >= 2]
        group = same[int(rng.random() * len(same))] if same else points
        a, b = _pick(rng, group, 2)
        if a['xyz'] == b['xyz']:
            a, b = _pick(rng, points, 2)
        a['xyz'], b['xyz'] = b['xyz'], a['xyz']
        mislabel_ids = [a['id'], b['id']]
    for p in points:
        p['uv_px'] = [round(p['uv_px'][0], 3), round(p['uv_px'][1], 3)]
    topology = topology_for(case, name)
    if topology['kind'] in grammar.MIRROR_AXES:
        for p in points:
            p['xyz'] = grammar.mirror_xyz(p['xyz'], topology['kind'])
    dims = case['dims_m']
    if spec['dims'] == 'one':
        known, assumed = {k: (dims[k] if k == case['anchor'] else None) for k in 'WDH'}, []
    elif spec['dims'] == 'all_exact':
        known, assumed = {k: dims[k] for k in 'WDH'}, ['W', 'D', 'H']
    elif spec['dims'] == 'wrong_one':
        known, assumed = {k: (round(dims[k] * (1.0 + spec['anchor_err']), 4) if k == case['anchor'] else None) for k in 'WDH'}, [case['anchor']]
    else:
        known, assumed = {k: guess_dim(rng, dims[k]) for k in 'WDH'}, ['W', 'D', 'H']
    part_px = {}
    for part in case['parts']:
        u, v = part['base_px']
        if sigma:
            u, v = u + rng.gauss(0.0, sigma), v + rng.gauss(0.0, sigma)
        part_px[part['id']] = [round(u, 3), round(v, 3)]
    fit_flags = {pid: False for pid in case['check_ids']} if check_corners else {}
    scale_checks = scale_checks_for(case, rng, sigma) if scale_check else []
    bad = set(outlier_ids) | set(mislabel_ids)
    return {'name': name, 'sigma_px': sigma, 'dims_mode': spec['dims'], 'points': points, 'known': known, 'assumed': assumed,
            'check_corners': check_corners, 'fit_flags': fit_flags, 'part_px': part_px, 'scale_checks': scale_checks,
            'anchor_err': spec.get('anchor_err'), 'topology': topology,
            'injected': {'outlier_ids': sorted(outlier_ids), 'outlier_px': displacement, 'mislabel_ids': sorted(mislabel_ids),
                         'bad_ids': sorted(bad),
                         # an injected corner that is held out cannot be named by leave-one-out (it is never in the fit): kept apart
                         'bad_check_ids': sorted(bad & set(fit_flags)), 'bad_fitted_ids': sorted(bad - set(fit_flags))}}


def scale_checks_for(case, rng, sigma):
    """[] or one cafe-table scale check: base and top pixels of the case's first round table (noisy like the corners)."""
    table = next((p for p in case['parts'] if p['type'] == 'round_table'), None)
    if table is None:
        return []
    w, h = case['image']['width'], case['image']['height']
    x, y = table['center_m']
    base = camgeom.project_or_none([x, y, table['floor_z']], case['camera'], w, h)
    top = camgeom.project_or_none([x, y, table['floor_z'] + table['size_m'][2]], case['camera'], w, h)
    if base is None or top is None:
        return []
    jitter = lambda uvz: [round(uvz[0] + (rng.gauss(0.0, sigma) if sigma else 0.0), 3), round(uvz[1] + (rng.gauss(0.0, sigma) if sigma else 0.0), 3)]
    return [{'id': 'cafe_table', 'base_px': jitter(base), 'top_px': jitter(top), 'expected_height_m': SCALE_CHECK_HEIGHT_M,
             'tolerance': SCALE_CHECK_TOLERANCE}]


def scene_for_run(scene, case, pert):
    """The canonical scene of a case with this perturbation applied (original v1 format; skill_caps.adapt_scene adapts it)."""
    out = copy.deepcopy(scene)
    out['corner_points'] = [{'id': p['id'], 'uv_px': p['uv_px'], 'xyz': p['xyz']} for p in pert['points']]
    out['known'] = {k: pert['known'][k] for k in 'WDH'}
    out['assumed'] = list(pert['assumed'])
    if pert.get('topology'):                                  # what a reader of the newest format also states (skill_caps strips it for older skills)
        declared = pert['topology']['declared']
        out.update(booth_type=declared['booth_type'], open_sides=list(declared['open_sides']), cutaway_sides=list(declared['cutaway_sides']))
    if pert.get('scale_checks'):
        out['scale_checks'] = copy.deepcopy(pert['scale_checks'])
    for c in out['components']:
        if c['id'] in pert['part_px']:
            c['base_px'] = list(pert['part_px'][c['id']])
    return out


def points_for_run(case, pert):
    """The points.json that box_fit.py reads for this run (the original format; skill_caps.adapt_points adapts it)."""
    doc = {'image': {'width': case['image']['width'], 'height': case['image']['height']},
            'known': {k: v for k, v in pert['known'].items() if v is not None}, 'assumed': list(pert['assumed']),
            'floor_z': case['floor_z'],
            'points': [{'id': p['id'], 'uv_px': p['uv_px'], 'xyz': p['xyz']} for p in pert['points']]}
    if pert.get('scale_checks'):
        doc['scale_checks'] = copy.deepcopy(pert['scale_checks'])
    return doc
