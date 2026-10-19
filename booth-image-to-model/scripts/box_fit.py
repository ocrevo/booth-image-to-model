"""Fit the source camera and the booth box proportions from a few clicked corners.

Requires numpy/scipy. Usually the first modelling step: 6-10 reviewed corner
clicks give camera pose, focal length and the booth W/D/H in about a second,
instead of reading the whole layout by eye.

points.json:
{
  "image": {"width": 1280, "height": 720},
  "known": {"D": 3.0},          # any of W (x), D (y), H (top z); measured or assumed
  "assumed": ["D"],             # names in `known` that are assumptions, not client facts
  "floor_z": 0.1,               # z of the walking surface (default 0)
  "points": [
    {"id": "top_front_left", "uv_px": [140, 95], "xyz": ["-W/2", "-D/2", "H"]},
    {"id": "wall_front_bottom", "uv_px": [910, 520], "xyz": ["W/2", "?yf", "F"]},
    {"id": "counter_top", "uv_px": [420, 380], "xyz": ["?cx", "?cy", 1.05]}
  ]
}
Optional "scale_checks": upright standard objects (a cafe table, a chair seat, a counter) used only to
cross-check the scale assumption, never in the fit:
  "scale_checks": [{"id": "table", "base_px": [420, 400], "top_px": [420, 360], "expected_height_m": 0.74, "tolerance": 0.15}]
Each is measured with the fitted camera and reported with its ratio to the expected height.
Coordinate tokens: a number; 0; W/2, -W/2, D/2, -D/2; H; F (floor_z); ?name for
a free unknown shared by every point that uses it. Origin is the footprint
centre, X right, Y back, Z up, metres (the v2 spec convention). Pixel corners
may lie slightly outside the canvas when extrapolated; give them lower weight.

A point may carry "fit": false (default true). Such a check point stays out of the fit but is projected with the fitted
camera and its pixel error is reported as an independent check ("check_points", "check_median_px", "check_max_px"). It is
optional: it costs one fitted corner, which pays off with 11 or more corners or when explicit held-out points are wanted. A check
point may only use dimensions and ?names that the fitted points already constrain; otherwise it is skipped with a warning.

The fit uses a robust loss (soft_l1, knee F_SCALE_PX), so one badly placed click does not drag the camera: the multi-start
finds the basin with plain least squares, then the best ROBUST_STARTS solutions are refined with the robust loss.

The default independent check is leave-one-out: every fitted point is held out in turn (warm start from the full solution) and
predicted from the others: "loo_errors_px", "loo_median_px", "loo_max_px". "outliers" lists the clicks the others cannot predict,
found one at a time (the suspect is dropped and the rest held out again, so innocent points that a bad click disturbed are not
blamed with it). The named clicks are then left out of a final refit ("dropped"), so a mis-click no longer bends the camera.

When leave-one-out can be trusted: every fitted point gives 2 equations (u, v), and the fit has n_unknown unknowns (7 for the
camera: position, yaw, pitch, roll, focal length; plus one for each W/D/H that is not given and one for each ?name). Holding a
point out leaves 2 * (n_fit - 1) equations. The check is computed, and a culprit named, only when 2 * (n_fit - 1) >= n_unknown +
LOO_MARGIN, which keeps LOO_MARGIN spare equations. With one given dimension (n_unknown 9) that is 8 fitted corners; each extra
unknown (a ?name, a dimension that is not given) asks for one more corner per two unknowns. With fewer corners the leave-one-out
numbers are ill-conditioned (an innocent corner is as likely to be blamed as the culprit), so they are not reported:
"loo_reliable" is false, loo_* are null and "outliers" is empty, with a warning that more corners are needed. A bad click can
still make the status high_residual there.

Every pixel limit is for a picture whose long side is REF_LONG_SIDE_PX px and grows linearly with larger pictures (never
shrinks); the limits used are in the output as "limits", so a caller does not redefine them.

Output: stdout is ONE JSON object, the same content as --output (warnings and the hint are inside it). Exit code 0 =
fitted, 3 = fitted but needs attention (status high_residual, ambiguous or check_failed; "exit_code" says so), 2 = blocked.
"""
from __future__ import annotations
import argparse
import json
import math
import re
import numpy as np
from scipy.optimize import least_squares
from common import read_json, write_json
import camera_math

DIMS = ('W', 'D', 'H')
TOKEN = re.compile(r'^\s*([+-]?)\s*(W|D)\s*/\s*2\s*$|^\s*(H|F)\s*$|^\s*\?([A-Za-z_][A-Za-z0-9_]*)\s*$')
SENSOR_MM = 36.0
REF_LONG_SIDE_PX = 1600     # the pixel limits below are for a picture whose long side is this long; larger pictures scale them up
RMS_LIMIT_PX = 4.0          # above this the clicks disagree with a pinhole box; the fit is reported as high_residual
LOSS = 'soft_l1'            # robust loss of the fit; 'linear' is the plain least squares (for comparisons)
LOSSES = ('linear', 'soft_l1', 'huber', 'cauchy', 'arctan')    # scipy's built-in losses
F_SCALE_PX = 2.0            # soft_l1 knee: residuals well below this count as ordinary click noise, larger ones grow only linearly
OUTLIER_FACTOR = 3.0        # a suspect is an outlier when its leave-one-out error exceeds this many times the median error of the
OUTLIER_MIN_PX = 10.0       # ... other points (predicted with the suspect out), and never below this floor (see the docstring)
LOO_MEDIAN_LIMIT_PX = 6.0   # leave-one-out predictions are out of sample, so they get 1.5x the in-sample RMS_LIMIT_PX
LOO_MAX_LIMIT_PX = 12.0     # the worst held-out point, 2x the median limit (as for the check points)
CHECK_MEDIAN_LIMIT_PX = 6.0  # same margin for the median error of the check points (clicked, projected, not fitted)
CHECK_MAX_LIMIT_PX = 12.0   # and 2x for the worst single check point
LOO_MARGIN = 5              # spare equations that leave-one-out needs: 2 * (n_fit - 1) >= n_unknown + LOO_MARGIN (see the docstring)
LOO_MIN_SPARE = 2           # and the points left after the named clicks are dropped keep at least this many spare equations
SCALE_TOLERANCE = 0.15      # default relative tolerance of a scale check (a check may carry its own)
ROBUST_STARTS = 3           # how many of the best plain-loss starts are refined with the robust loss
MAX_OUTLIER_ROUNDS = 3      # suspects named per fit; more than this means the clicks are bad as a whole (status says so)
LOO_MAX_NFEV = 200          # a warm-started solve needs far fewer evaluations than a cold start
UNPROJECTABLE_PX = 1e4      # error reported for a point that falls behind the camera (keeps the JSON finite)


def size_scale(width, height):
    """How much the pixel limits grow for this picture: 1 up to REF_LONG_SIDE_PX on the long side, linear above."""
    return max(1.0, max(width, height) / REF_LONG_SIDE_PX)


def loo_min_fit(n_unknown, margin=None):
    """The fewest fitted points for which leaving one out keeps `margin` spare equations."""
    margin = LOO_MARGIN if margin is None else margin
    return int(math.ceil((n_unknown + margin) / 2.0)) + 1


def parse_scale_checks(items):
    out = []
    for c in items or []:
        for key in ('id', 'base_px', 'top_px', 'expected_height_m'):
            if key not in c:
                raise ValueError(f"scale_checks entry needs {key!r}: {c}")
        for key in ('base_px', 'top_px'):
            if not (isinstance(c[key], list) and len(c[key]) == 2 and all(isinstance(v, (int, float)) and math.isfinite(v) for v in c[key])):
                raise ValueError(f"scale_checks {c['id']}: {key} must be [u, v] pixels")
        if not (isinstance(c['expected_height_m'], (int, float)) and c['expected_height_m'] > 0):
            raise ValueError(f"scale_checks {c['id']}: expected_height_m must be positive")
        tol = c.get('tolerance')
        tol = SCALE_TOLERANCE if tol is None else tol
        if not (isinstance(tol, (int, float)) and 0 < tol <= 1):
            raise ValueError(f"scale_checks {c['id']}: tolerance must be in (0, 1]")
        out.append({'id': c['id'], 'base_px': list(c['base_px']), 'top_px': list(c['top_px']),
                    'expected_height_m': float(c['expected_height_m']), 'tolerance': float(tol)})
    return out


def parse_token(token):
    """-> ('const', v) | ('half', dim, sign) | ('dim', 'H') | ('floor',) | ('free', name)."""
    if isinstance(token, bool):
        raise ValueError('boolean is not a coordinate')
    if isinstance(token, (int, float)):
        if not math.isfinite(token):
            raise ValueError('non-finite coordinate')
        return ('const', float(token))
    if not isinstance(token, str):
        raise ValueError(f'bad coordinate token: {token!r}')
    try:
        return ('const', float(token))
    except ValueError:
        pass
    m = TOKEN.match(token)
    if not m:
        raise ValueError(f'bad coordinate token: {token!r}; use number, W/2, -D/2, H, F or ?name')
    if m.group(2):
        return ('half', m.group(2), -1.0 if m.group(1) == '-' else 1.0)
    if m.group(3) == 'H':
        return ('dim', 'H')
    if m.group(3) == 'F':
        return ('floor',)
    return ('free', m.group(4))


def _forward(yaw, pitch):
    return np.array([-math.sin(yaw) * math.cos(pitch), math.cos(yaw) * math.cos(pitch), -math.sin(pitch)])


def _camera_dict(pos, yaw, pitch, roll, focal, width, distance):
    fwd = _forward(yaw, pitch)
    return {'projection': 'perspective', 'position': [round(float(v), 5) for v in pos],
            'target': [round(float(v), 5) for v in pos + fwd * distance],
            'lens_mm': round(float(focal / width * SENSOR_MM), 4), 'sensor_width_mm': SENSOR_MM,
            'principal_point': [0.5, 0.5], 'pixel_aspect': [1, 1], 'fit_status': 'landmark_fitted',
            'roll_deg': round(math.degrees(roll), 4)}


class Problem:
    def __init__(self, data):
        img = data.get('image') or {}
        self.width, self.height = int(img['width']), int(img['height'])
        if self.width <= 0 or self.height <= 0:
            raise ValueError('image width/height must be positive')
        self.size_scale = size_scale(self.width, self.height)
        self.floor_z = float(data.get('floor_z', 0.0))
        known = data.get('known') or {}
        for k in known:
            if k not in DIMS:
                raise ValueError(f'unknown dimension {k!r}; use W, D or H')
            if not (isinstance(known[k], (int, float)) and known[k] > 0):
                raise ValueError(f'known {k} must be a positive number')
        self.known = {k: float(v) for k, v in known.items()}
        self.assumed = list(data.get('assumed') or [])
        self.scale_checks = parse_scale_checks(data.get('scale_checks'))
        pts = data.get('points') or []
        rows = []
        for p in pts:
            toks = [parse_token(t) for t in p['xyz']]
            if len(toks) != 3:
                raise ValueError(f"{p.get('id')}: xyz needs three tokens")
            w = float(p.get('weight', 1.0))
            if not (math.isfinite(w) and w > 0):
                raise ValueError(f"{p.get('id')}: weight must be positive")
            flag = p.get('fit')                       # false = check point; absent or null = fitted (older files)
            if flag is None:
                flag = True
            if not isinstance(flag, bool):
                raise ValueError(f"{p.get('id')}: fit must be true, false or null")
            rows.append({'id': p['id'], 'uv': np.asarray(p['uv_px'], float), 'tokens': toks, 'weight': w, 'fit': flag})
        fit_rows = [r for r in rows if r['fit']]
        if len(fit_rows) < 4:
            raise ValueError('need at least 4 fitted points; 6-10 spread over near/far and top/bottom is better'
                             + (f' ({len(rows) - len(fit_rows)} check points do not count)' if len(rows) > len(fit_rows) else ''))
        # Only fitted points define the unknowns and the scale anchor.
        frees, numeric_scale = [], False
        for r in fit_rows:
            for t in r['tokens']:
                if t[0] == 'free' and t[1] not in frees:
                    frees.append(t[1])
                if t[0] == 'const' and abs(t[1]) > 1e-9:
                    numeric_scale = True
        self.free_dims = [d for d in DIMS if d not in self.known and any(
            (t[0] == 'half' and t[1] == d) or (t[0] == 'dim' and t[1] == d) for r in fit_rows for t in r['tokens'])]
        self.frees = frees
        if not self.known and not numeric_scale:
            raise ValueError('NO_SCALE_ANCHOR: give a known W/D/H or a point with a measured coordinate '
                             '(e.g. counter top z=1.05); an assumed value must be listed in "assumed"')
        self.n_unknown = 7 + len(self.free_dims) + len(self.frees)
        if 2 * len(fit_rows) < self.n_unknown + 1:
            raise ValueError(f'UNDERDETERMINED: {len(fit_rows)} fitted points for {self.n_unknown} unknowns; add corners')
        self.loo_min_fit = loo_min_fit(self.n_unknown)
        self.loo_reliable = len(fit_rows) >= self.loo_min_fit
        # A check point whose dimension or ?name no fitted point constrains cannot be placed: skip it, say so.
        placeable_dims, placeable_frees = set(self.known) | set(self.free_dims), set(frees)

        def placeable(row):
            return all(t[1] in placeable_frees if t[0] == 'free' else (t[1] in placeable_dims if t[0] in ('half', 'dim') else True)
                       for t in row['tokens'])
        self.skipped_checks = [r['id'] for r in rows if not r['fit'] and not placeable(r)]
        self.points = [r for r in rows if r['fit'] or placeable(r)]
        self.fit_idx = np.array([i for i, r in enumerate(self.points) if r['fit']], dtype=int)
        self.n_fit, self.n_check = len(self.fit_idx), len(self.points) - len(self.fit_idx)
        # What each fitted point alone contributes (used to decide whether it can be held out).
        self.point_vars = [{('dim', t[1]) for t in r['tokens'] if t[0] in ('half', 'dim') and t[1] in self.free_dims}
                           | {('free', t[1]) for t in r['tokens'] if t[0] == 'free'} for r in self.points]
        self.point_anchor = [any(t[0] == 'const' and abs(t[1]) > 1e-9 for t in r['tokens']) for r in self.points]

        # Every coordinate is linear in (W, D, H, free...): world = C @ v + b.
        k = 3 + len(self.frees)
        self.C = np.zeros((len(self.points), 3, k))
        self.b = np.zeros((len(self.points), 3))
        for i, p in enumerate(self.points):
            for j, t in enumerate(p['tokens']):
                if t[0] == 'const':
                    self.b[i, j] = t[1]
                elif t[0] == 'half':
                    self.C[i, j, DIMS.index(t[1])] = t[2] / 2
                elif t[0] == 'dim':
                    self.C[i, j, 2] = 1.0
                elif t[0] == 'floor':
                    self.b[i, j] = self.floor_z
                else:
                    self.C[i, j, 3 + self.frees.index(t[1])] = 1.0
        self.uv = np.asarray([p['uv'] for p in self.points])
        self.sqrt_w = np.sqrt([p['weight'] for p in self.points])[:, None]

    def values(self, x):
        dims = dict(self.known)
        for i, d in enumerate(self.free_dims):
            dims[d] = x[7 + i]
        free = {n: x[7 + len(self.free_dims) + i] for i, n in enumerate(self.frees)}
        return dims, free

    def all_world(self, x):
        dims, free = self.values(x)
        v = np.array([dims.get(d, 0.0) for d in DIMS] + [free[n] for n in self.frees])
        return self.C @ v + self.b

    def project(self, x, world):
        pos, yaw, pitch, roll, f = x[0:3], x[3], x[4], x[5], x[6]
        fwd = _forward(yaw, pitch)
        right = np.cross(fwd, [0, 0, 1.0]); right /= np.linalg.norm(right)
        up = np.cross(right, fwd)
        c, s = math.cos(roll), math.sin(roll)
        r2, u2 = c * right + s * up, c * up - s * right
        d = world - pos
        z = d @ fwd
        zs = np.maximum(z, 1e-3)
        return np.c_[self.width / 2 + f * (d @ r2) / zs, self.height / 2 - f * (d @ u2) / zs], z

    def residual(self, x, rows=None):
        """Weighted pixel residuals of the fitted points (or of `rows`, indices into self.points), plus a behind-camera penalty."""
        idx = self.fit_idx if rows is None else rows
        uv, z = self.project(x, self.all_world(x)[idx])
        return np.r_[((uv - self.uv[idx]) * self.sqrt_w[idx]).ravel(), np.maximum(0, 0.3 - z) * 100]

    def bounds(self):
        lo = [-300, -300, -50, -4 * math.pi, -math.radians(30), -math.radians(45), 0.3 * self.width]
        hi = [300, 300, 300, 4 * math.pi, math.radians(89), math.radians(45), 8.0 * self.width]
        lo += [0.3] * len(self.free_dims) + [-100] * len(self.frees)
        hi += [80] * len(self.free_dims) + [100] * len(self.frees)
        return lo, hi

    def point_error(self, x, i):
        """Pixel distance between point i's click and its projection with the solution x (finite even behind the camera)."""
        uv, z = self.project(x, self.all_world(x)[[i]])
        e = float(np.hypot(*(uv[0] - self.uv[i])))
        return min(e, UNPROJECTABLE_PX) if z[0] > 1e-3 and math.isfinite(e) else UNPROJECTABLE_PX

    def limits(self):
        """Every pixel limit for this picture (grown with its size), plus the rest of the rules the status is decided with."""
        k = self.size_scale
        return {'size_scale': round(k, 4), 'ref_long_side_px': REF_LONG_SIDE_PX,
                'rms_px': round(RMS_LIMIT_PX * k, 3),
                'check_median_px': round(CHECK_MEDIAN_LIMIT_PX * k, 3), 'check_max_px': round(CHECK_MAX_LIMIT_PX * k, 3),
                'loo_median_px': round(LOO_MEDIAN_LIMIT_PX * k, 3), 'loo_max_px': round(LOO_MAX_LIMIT_PX * k, 3),
                'outlier_min_px': round(OUTLIER_MIN_PX * k, 3), 'outlier_factor': OUTLIER_FACTOR,
                'loo_margin_equations': LOO_MARGIN, 'n_unknown': self.n_unknown, 'loo_min_fit': self.loo_min_fit,
                'scale_ratio_tolerance': SCALE_TOLERANCE}

    def can_hold_out(self, i, excluded=()):
        """True when point i can be predicted from the other fitted points that are not `excluded`: the whole fit keeps LOO_MARGIN
        spare equations when one point is held out (the rule in the docstring; clicks already excluded do not change it), the
        remaining points keep LOO_MIN_SPARE, and they still determine every unknown and the scale."""
        others = [j for j in self.fit_idx if j != i and j not in excluded]
        if 2 * (len(others) + len(excluded)) < self.n_unknown + LOO_MARGIN or 2 * len(others) < self.n_unknown + LOO_MIN_SPARE:
            return False
        if not self.point_vars[i] <= set().union(*(self.point_vars[j] for j in others)):
            return False
        return bool(self.known) or any(self.point_anchor[j] for j in others)


def ambiguous_dims(spread, dims, free_dims, tol=0.05):
    """Fitted dimensions whose value varies by more than tol (relative) across equally good solutions."""
    return [d for d in free_dims if spread.get(d, 0.0) > tol * dims[d]]


def ambiguity_hint(names):
    if not names:
        return None
    parts = {'W': 'a point on a known plane at the far left/right end', 'D': 'a known depth or a point on the front/back edge',
             'H': 'a point whose z is numeric (a hidden-base tie, a counter top at a standard height) or a known height'}
    return 'Not constrained by the clicks: ' + ', '.join(names) + '. Add ' + '; '.join(parts[n] for n in names) + ', or give the value as known/assumed.'


def _median(values):
    return float(np.median(values)) if len(values) else None


def _pixel_jacobian(pb, x, rows):
    """Central-difference Jacobian of the weighted pixel residuals of `rows` (2 per point) with respect to every unknown."""
    n = 2 * len(rows)
    jac = np.empty((n, len(x)))
    for j in range(len(x)):
        h = 1e-6 * max(1.0, abs(x[j]))
        xp, xm = x.copy(), x.copy()
        xp[j] += h
        xm[j] -= h
        jac[:, j] = (pb.residual(xp, rows)[:n] - pb.residual(xm, rows)[:n]) / (2 * h)
    return jac


def inflation_factors(pb, x, rows):
    """{row index: sqrt(1 + lambda)} where lambda is the variance of the pixel position predicted for that point from the others,
    in units of the click noise variance: the point's own leverage on the fit. 1.0 = the others pin it down as well as a click;
    a corner at the edge of the picture can sit at 3 or more. math.inf when the others leave an unknown undetermined."""
    jac = _pixel_jacobian(pb, x, rows)
    norm = np.linalg.norm(jac, axis=0)
    norm[norm == 0] = 1.0
    jac = jac / norm
    out = {}
    for k, i in enumerate(rows):
        own = jac[2 * k:2 * k + 2]
        rest = np.delete(jac, [2 * k, 2 * k + 1], axis=0)
        gram = rest.T @ rest
        sv = np.linalg.svd(gram, compute_uv=False)
        if not sv[0] > 0 or sv[-1] < 1e-10 * sv[0]:
            out[i] = math.inf
            continue
        lam = 0.5 * float(np.trace(own @ np.linalg.solve(gram, own.T)))
        out[i] = math.sqrt(1.0 + max(lam, 0.0))
    return out


def hold_out_each(pb, x_start, loss, f_scale, excluded=()):
    """Hold each fitted point (not in `excluded`) out in turn, refit the rest from x_start and return
    {id: (leverage-scaled click error, rms click error of the points that were fitted, inflation)}.

    The scaled error is the click error of the held-out point in px divided by its inflation (inflation_factors), so a corner
    that the others can only predict loosely (an extreme corner of a wide picture) is not blamed for it; for a well-determined
    corner it is the plain pixel error. Points that the others cannot predict (they alone fix a ?name, a dimension or the scale,
    or too few equations remain) are left out of the result.
    """
    lo, hi = pb.bounds()
    rows_all = [i for i in pb.fit_idx if i not in excluded]
    inflation = inflation_factors(pb, x_start, rows_all)
    out = {}
    for i in rows_all:
        if not pb.can_hold_out(i, excluded) or not math.isfinite(inflation[i]):
            continue
        rows = [j for j in rows_all if j != i]
        try:
            r = least_squares(lambda x: pb.residual(x, rows), x_start, bounds=(lo, hi), loss=loss, f_scale=f_scale, max_nfev=LOO_MAX_NFEV)
        except (ValueError, FloatingPointError):
            continue
        rest = math.sqrt(float(np.mean([pb.point_error(r.x, j) ** 2 for j in rows])))
        out[pb.points[i]['id']] = (pb.point_error(r.x, i) / inflation[i], rest, inflation[i])
    return out


def detect_outliers(pb, x_full, loss, f_scale, first_pass, min_px=None):
    """Clicks that the other clicks cannot predict, found one at a time and each confirmed before it is named.

    A mis-click spoils the leave-one-out error of innocent points too (they are predicted by a fit that still contains it), so
    comparing every error with the median of the others lets a bad click hide among the victims it made. Instead: among the
    points above the floor, the one whose removal leaves the other clicks agreeing best is the suspect. It is named only when,
    with the suspect out, the others are predicted far better than the suspect is (its error exceeds OUTLIER_FACTOR times the
    median of the others' new errors, and the floor). Then it is dropped, the others are held out again, and the search goes
    on. At most MAX_OUTLIER_ROUNDS suspects are returned, in the order found. A suspect that cannot be confirmed because too few
    equations are left is not named.
    """
    min_px = OUTLIER_MIN_PX if min_px is None else min_px
    found, current = [], first_pass
    for _ in range(MAX_OUTLIER_ROUNDS):
        candidates = [k for k, v in current.items() if v[0] > min_px]
        if not candidates:
            break
        suspect = min(candidates, key=lambda k: (current[k][1], -current[k][0]))
        excluded = [i for i in pb.fit_idx if pb.points[i]['id'] in found or pb.points[i]['id'] == suspect]
        confirm = hold_out_each(pb, x_full, loss, f_scale, excluded)
        scale = _median([v[0] for v in confirm.values()])
        if scale is None or current[suspect][0] <= max(OUTLIER_FACTOR * scale, min_px):
            break
        found.append(suspect)
        current = confirm
    return found


def _multistart(pb, seeds, rng_seed, loss, f_scale):
    """Find the basin with plain least squares from many starts, then refine the best few with the robust loss.

    -> (best, plain_best, plain solutions, loss that was used, warnings). Plain least squares is several times cheaper per seed
    than the robust loss, which only has to polish a basin that is already found.
    """
    rng = np.random.default_rng(rng_seed)
    size_guess = max(list(pb.known.values()) + [4.0])
    lo, hi = pb.bounds()
    sols = []
    for _ in range(seeds):
        yaw = rng.uniform(-math.pi, math.pi)
        pitch = math.radians(rng.uniform(5, 50))
        dist = size_guess * rng.uniform(1.5, 6)
        pos = -_forward(yaw, pitch) * dist + np.array([0, 0, size_guess * 0.4])
        x0 = list(pos) + [yaw, pitch, 0.0, pb.width * rng.uniform(0.7, 2.5)]
        x0 += [size_guess * rng.uniform(0.5, 2.0) for _ in pb.free_dims] + [rng.uniform(-1, 1) for _ in pb.frees]
        x0 = np.clip(x0, np.array(lo) + 1e-6, np.array(hi) - 1e-6)
        try:
            r = least_squares(pb.residual, x0, bounds=(lo, hi), max_nfev=600)
        except (ValueError, FloatingPointError):
            continue
        sols.append(r)
    if not sols:
        raise ValueError('FIT_FAILED: no seed converged')
    plain_best = min(sols, key=lambda r: r.cost)
    best, warnings = plain_best, []
    if loss != 'linear':
        refined = []
        for start in sorted(sols, key=lambda r: r.cost)[:ROBUST_STARTS]:
            try:
                refined.append(least_squares(pb.residual, start.x, bounds=(lo, hi), loss=loss, f_scale=f_scale, max_nfev=600))
            except (ValueError, FloatingPointError):
                continue
        if refined:
            best = min(refined, key=lambda r: r.cost)
        else:
            warnings.append('the robust refinement did not converge; the plain least-squares solution is reported')
            loss = 'linear'
    return best, plain_best, sols, loss, warnings


def solve(data, seeds=40, rng_seed=1, loss=LOSS, f_scale=None, drop_outliers=None):
    """Fit camera, proportions and free values. `loss` is a scipy least_squares loss ('soft_l1' robust, 'linear' plain).

    f_scale is the robust knee in px (default F_SCALE_PX, grown with the picture size). With drop_outliers the clicks that
    leave-one-out names are left out of a final refit; the default (None) does that for a robust loss and keeps the plain
    least-squares fit (loss='linear') exactly as it was.
    """
    if loss not in LOSSES:
        raise ValueError(f'unknown loss {loss!r}; use one of {", ".join(LOSSES)}')
    pb = Problem(data)
    lim = pb.limits()
    drop_outliers = (loss != 'linear') if drop_outliers is None else drop_outliers
    f_scale = F_SCALE_PX * pb.size_scale if f_scale is None else f_scale
    lo, hi = pb.bounds()
    best, plain_best, sols, loss, warnings = _multistart(pb, seeds, rng_seed, loss, f_scale)
    x = best.x
    for cid in pb.skipped_checks:
        warnings.append(f"check point {cid} uses a dimension or ?name that no fitted point constrains and was not checked")

    # Independent check: every fitted click predicted by the others. Only when enough equations are left over (see the docstring).
    loo_errors, loo_inflation, loo_median, loo_max, outliers = None, None, None, None, []
    if pb.loo_reliable:
        first_pass = hold_out_each(pb, x, loss, f_scale)
        loo_errors = {k: v[0] for k, v in first_pass.items()}
        loo_inflation = {k: v[2] for k, v in first_pass.items()}
        outliers = detect_outliers(pb, x, loss, f_scale, first_pass, lim['outlier_min_px'])
        loo_median, loo_max = _median(list(loo_errors.values())), (max(loo_errors.values()) if loo_errors else None)
    else:
        warnings.append(f'leave-one-out is skipped: {pb.n_fit} fitted corners are too few to name a bad click '
                        f'({pb.n_unknown} unknowns need at least {pb.loo_min_fit}); add well-spread corners')
    dropped = []
    if outliers and drop_outliers:
        kept = [i for i in pb.fit_idx if pb.points[i]['id'] not in outliers]
        try:
            refit = least_squares(lambda v: pb.residual(v, kept), x, bounds=(lo, hi), loss=loss, f_scale=f_scale, max_nfev=600)
            x, dropped = refit.x, list(outliers)
        except (ValueError, FloatingPointError):
            warnings.append('the refit without the named clicks did not converge; the fit that keeps them is reported')
    kept_mask = [pb.points[i]['id'] not in dropped for i in range(len(pb.points))]

    dims, free = pb.values(x)
    world = pb.all_world(x)
    used = [i for i in pb.fit_idx if kept_mask[i]]
    centre = world[used].mean(axis=0)
    distance = float(np.linalg.norm(centre - x[0:3]))
    camera = _camera_dict(x[0:3], x[3], x[4], x[5], x[6], pb.width, distance)
    per_point, errs = [], []
    for i, p in enumerate(pb.points):
        try:
            u, v, _ = camera_math.project(world[i].tolist(), camera, pb.width, pb.height)
            e = float(math.hypot(u - p['uv'][0], v - p['uv'][1]))
        except ValueError:
            u, v = float(p['uv'][0]), float(p['uv'][1])
            e = UNPROJECTABLE_PX
            warnings.append(f"point {p['id']} falls behind the fitted camera")
        errs.append(e)
        per_point.append({'id': p['id'], 'uv_px': p['uv'].tolist(), 'fitted_px': [round(u, 2), round(v, 2)],
                          'err_px': round(e, 2), 'world_xyz': [round(float(c), 4) for c in world[i]], 'fit': bool(p['fit']),
                          'dropped': not kept_mask[i]})
    fit_points = [per_point[i] for i in pb.fit_idx]
    fit_errs = [errs[i] for i in pb.fit_idx]                 # every clicked fitted point, a dropped one included: it still disagrees
    kept_errs = [errs[i] for i in used]
    check_points = [{'id': q['id'], 'err_px': q['err_px']} for q in per_point if not q['fit']]
    check_errs = [errs[i] for i, p in enumerate(pb.points) if not p['fit']]
    check_median, check_max = _median(check_errs), (max(check_errs) if check_errs else None)

    good = [r for r in sols if r.cost <= plain_best.cost * 1.5 + 1.0]
    spread = {}
    for i, d in enumerate(pb.free_dims):
        vals = [r.x[7 + i] for r in good]
        spread[d] = round(float(max(vals) - min(vals)), 4)
    fvals = [r.x[6] for r in good]
    spread['lens_mm'] = round(float((max(fvals) - min(fvals)) / pb.width * SENSOR_MM), 3)
    ambiguous = bool(ambiguous_dims(spread, dims, pb.free_dims)) or len(good) < 3
    rms = float(math.sqrt(np.mean(np.square(fit_errs))))
    rms_kept = float(math.sqrt(np.mean(np.square(kept_errs))))
    worst = sorted(fit_points, key=lambda r: -r['err_px'])[:3]
    scale_checks = []
    for c in pb.scale_checks:
        try:
            height, miss = camera_math.height_above(c['top_px'], c['base_px'], camera, pb.width, pb.height, pb.floor_z)
        except ValueError as ex:
            scale_checks.append({'id': c['id'], 'expected_m': c['expected_height_m'], 'measured_m': None, 'ratio': None,
                                 'tolerance': c['tolerance'], 'miss_m': None, 'ok': False, 'error': str(ex)})
            warnings.append(f"scale check {c['id']} could not be measured: {ex}")
            continue
        ratio = height / c['expected_height_m']
        ok = abs(ratio - 1) <= c['tolerance']
        scale_checks.append({'id': c['id'], 'expected_m': c['expected_height_m'], 'measured_m': round(height, 3), 'ratio': round(ratio, 3),
                             'tolerance': c['tolerance'], 'miss_m': round(miss, 3), 'ok': ok})
        if not ok:
            warnings.append(f"scale check {c['id']}: measured {height:.2f} m against an expected {c['expected_height_m']} m (ratio {ratio:.2f}); "
                            'the scale assumption, the clicks or the expected size is wrong')
    suspects = ', '.join(f"{k} ({loo_errors[k]:.1f} px)" for k in outliers)
    if rms > lim['rms_px']:
        warnings.append(f"rms {rms:.1f} px is above {lim['rms_px']:g} px; check the worst points "
                        + ', '.join(f"{w['id']} ({w['err_px']} px)" for w in worst)
                        + (f' and the clicks the others cannot predict: {suspects}' if outliers else '')
                        + ' for mis-clicks, an under-constrained plane, or a render that is not a true perspective')
    elif outliers:
        warnings.append(f'leave-one-out: {suspects} could not be predicted from the other clicks (possible mis-click or wrong coordinate token)')
    if dropped:
        warnings.append('the camera was refitted without ' + ', '.join(dropped) + f' (the other clicks: rms {rms_kept:.1f} px)')
    check_reasons = []
    if check_median is not None and check_median > lim['check_median_px']:
        check_reasons.append(f"the check points are off by a median of {check_median:.1f} px (limit {lim['check_median_px']:g})")
    if check_max is not None and check_max > lim['check_max_px']:
        worst_check = max(check_points, key=lambda q: q['err_px'])
        check_reasons.append(f"check point {worst_check['id']} is off by {check_max:.1f} px (limit {lim['check_max_px']:g})")
    if loo_median is not None and loo_median > lim['loo_median_px']:
        check_reasons.append(f"leave-one-out prediction error has a median of {loo_median:.1f} px (limit {lim['loo_median_px']:g})")
    elif loo_max is not None and loo_max > lim['loo_max_px'] and not outliers:
        check_reasons.append(f"one held-out corner is predicted {loo_max:.1f} px off (limit {lim['loo_max_px']:g})")
    if outliers:
        check_reasons.append('outlier clicks: ' + ', '.join(outliers))
    # Ladder: ambiguous > high_residual > check_failed (the fit itself is fine but does not predict independent clicks) > fitted.
    status = 'ambiguous' if ambiguous else ('high_residual' if rms > lim['rms_px'] else ('check_failed' if check_reasons else 'fitted'))
    if status == 'check_failed':
        warnings.append(f'the fit reproduces its own clicks (rms {rms:.1f} px) but does not predict independent ones: ' + '; '.join(check_reasons))
    ambiguous_names = ambiguous_dims(spread, dims, pb.free_dims)
    amb_hint = ambiguity_hint(ambiguous_names)
    hint = amb_hint or ('Re-click or remove the clicks named above, or add well-spread corners (near and far, top and bottom) '
                        'so each one can be predicted from the others.' if status == 'check_failed' else None)
    return {
        'status': status, 'exit_code': 0 if status == 'fitted' else 3,
        'worst_points': [{'id': w['id'], 'err_px': w['err_px']} for w in worst], 'warnings': warnings, 'hint': hint,
        'rms_px': round(rms, 3), 'max_px': round(float(max(fit_errs)), 3), 'rms_kept_px': round(rms_kept, 3),
        'loss': loss, 'n_fit': pb.n_fit, 'n_check': pb.n_check, 'n_unknown': pb.n_unknown,
        'check_points': check_points,
        'check_median_px': None if check_median is None else round(check_median, 3),
        'check_max_px': None if check_max is None else round(float(check_max), 3),
        'loo_reliable': pb.loo_reliable,
        'loo_errors_px': None if loo_errors is None else {k: round(v, 3) for k, v in loo_errors.items()},
        'loo_inflation': None if loo_inflation is None else {k: round(v, 3) for k, v in loo_inflation.items()},
        'loo_median_px': None if loo_median is None else round(loo_median, 3),
        'loo_max_px': None if loo_max is None else round(float(loo_max), 3),
        'outliers': outliers, 'dropped': dropped,
        'limits': lim,
        'dims_m': {d: round(float(dims[d]), 4) for d in DIMS if d in dims},
        'dim_source': {d: ('assumed' if d in pb.assumed else 'given') if d in pb.known else 'fitted' for d in DIMS if d in dims},
        'free': {k: round(float(v), 4) for k, v in free.items()},
        'image': {'width': pb.width, 'height': pb.height}, 'floor_z': pb.floor_z,
        'assumed': list(pb.assumed), 'scale_checks': scale_checks,
        'ambiguous_dims': ambiguous_names, 'ambiguity_hint': amb_hint,
        'camera': camera, 'points': per_point,
        'solution_spread': spread, 'converged_seeds': len(good), 'seeds': seeds,
        'note': 'Proportions and pose come from the clicked corners; absolute size only from known/assumed values. '
                'Review the reprojection before building; this is not a measurement.'}


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    p.add_argument('--points', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--assume', action='append', default=[], help='NAME=VALUE for W/D/H; recorded as assumed')
    p.add_argument('--seeds', type=int, default=40)
    p.add_argument('--loss', default=LOSS, choices=('soft_l1', 'linear'), help='soft_l1 (robust, default) or linear (plain least squares)')
    p.add_argument('--keep-outliers', action='store_true', help='do not refit without the clicks that leave-one-out names (always so with --loss linear)')
    a = p.parse_args()
    data = read_json(a.points)
    for item in a.assume:
        name, _, value = item.partition('=')
        data.setdefault('known', {})[name.strip()] = float(value)
        data.setdefault('assumed', []).append(name.strip())
    try:
        result = solve(data, a.seeds, loss=a.loss, drop_outliers=False if a.keep_outliers else None)
    except (ValueError, KeyError) as ex:
        result = {'status': 'blocked', 'reason': str(ex), 'exit_code': 2}
    write_json(a.output, result)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))      # one JSON object, the same as --output
    return result['exit_code']


if __name__ == '__main__':
    raise SystemExit(main())
