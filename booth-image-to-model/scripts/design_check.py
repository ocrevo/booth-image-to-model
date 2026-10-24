"""Advisory design checks on a v2 spec; standard library only.

The source image may itself differ from usual stand practice, so every result is a
prompt for a human decision, never a verdict: wording is "outside the usual range"
or "no modelled support path", never compliant or non-compliant. Numeric limits
come from schemas/design-priors.v1.json (copied from references/design-priors.md,
reviewed=false until a designer signs them off).

A context file supplies facts the spec does not carry. Missing context means the
check is reported as not_checked with the reason; there is no silent fallback to
default site facts.

    design_check.py --spec spec.json [--context context.json] [--output report.json]

context.json (strict; unknown keys are refused; every key optional):
  {"height_limit_m": 4.0, "hanging_allowed": false, "open_sides": ["front"],
   "led_module_m": 0.5, "footprint_tolerance_m": 0.02}
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
from common import read_json, write_json
from geometry import mesh_for, transform_point
from validate_spec import validate

PRIORS_PATH = Path(__file__).resolve().parents[1] / 'schemas' / 'design-priors.v1.json'
CONTEXT_KEYS = {'height_limit_m': 'number', 'hanging_allowed': 'bool', 'open_sides': 'sides', 'led_module_m': 'number',
                'footprint_tolerance_m': 'number'}
SIDES = {'front', 'back', 'left', 'right'}
CONTACT_TOL = 0.005
EDGE_TRIM_ROLES = {'edge_trim'}
NARROW_GAP_ROLES = {'counter', 'cabinet', 'planter', 'table', 'chair'}
GENERIC_ONLY_ROLES = {'canopy_frame', 'canopy_sign', 'lit_panel', 'lit_groove', 'lit_niche', 'lit_niche_light',
                      'prop_block'}    # no role-specific check yet: reported as not checked
NOTE = ('Advisory only. The reference image may legitimately differ from usual stand practice; these results are '
        'prompts for review, not a statement about venue, safety or any other approval.')


def load_priors(path=PRIORS_PATH):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    return {e['id']: e for e in data['entries']}


def check_context(context):
    ctx = dict(context or {})
    for key in ctx:
        if key not in CONTEXT_KEYS:
            raise ValueError(f'unknown context key {key!r}; allowed: {sorted(CONTEXT_KEYS)}')
    for key, kind in CONTEXT_KEYS.items():
        v = ctx.get(key)
        if v is None:
            continue
        if kind == 'number' and not (isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0):
            raise ValueError(f'context {key} must be a positive number')
        if kind == 'bool' and not isinstance(v, bool):
            raise ValueError(f'context {key} must be true or false')
        if kind == 'sides' and not (isinstance(v, list) and v and set(v) <= SIDES):
            raise ValueError(f'context {key} must be a non-empty list drawn from {sorted(SIDES)}')
    return ctx


def world_bounds(component):
    """Axis-aligned world bounds ((x0, y0, z0), (x1, y1, z1)) of a component's real mesh; text has none."""
    verts, _ = mesh_for(component)
    pts = [transform_point(v, component['location'], component['rotation_deg']) for v in verts]
    return [min(p[i] for p in pts) for i in range(3)], [max(p[i] for p in pts) for i in range(3)]


SEATING_ROLES = {'chair', 'table'}
SEATING_JOIN_M = 0.6      # chairs around a table form one seating group


def _touching(a, b, tol=CONTACT_TOL):
    return all(a[0][i] - tol <= b[1][i] and b[0][i] - tol <= a[1][i] for i in range(3))


def _clusters(ids, bounds, tol=CONTACT_TOL):
    """Connected groups of parts whose bounds touch or overlap (within tol)."""
    parent = {i: i for i in ids}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    ids = list(ids)
    for x in range(len(ids)):
        for y in range(x + 1, len(ids)):
            if _touching(bounds[ids[x]], bounds[ids[y]], tol):
                parent[find(ids[x])] = find(ids[y])
    groups = {}
    for i in ids:
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def _span(ids, bounds):
    lo = [min(bounds[i][0][k] for i in ids) for k in range(3)]
    hi = [max(bounds[i][1][k] for i in ids) for k in range(3)]
    return lo, hi


def _rng(priors, key):
    e = priors[key]
    return e['min'], e['max']


def run(spec, context=None, priors=None):
    report = validate(spec)
    if not report['valid']:
        raise ValueError('invalid spec: ' + json.dumps(report['errors'][:3], ensure_ascii=False))
    ctx = check_context(context)
    priors = priors or load_priors()
    comps = {c['id']: c for c in spec['components'] if c['kind'] != 'text'}
    bounds = {i: world_bounds(c) for i, c in comps.items()}
    findings, not_checked, ran = [], [], []

    def finding(check, level, ids, message, value=None, limit=None):
        f = {'check': check, 'level': level, 'ids': ids, 'message': message}
        if value is not None:
            f['value'] = value
        if limit is not None:
            f['limit'] = limit
        findings.append(f)

    floors = [c for c in comps.values() if c.get('role') == 'floor']
    floor_top = bounds[floors[0]['id']][1][2] if floors else 0.0

    # ---- parts outside the footprint (the floor part defines it; it need not be centred on the origin)
    W, D = spec['scale']['footprint_m']
    if floors:
        fx0, fy0 = bounds[floors[0]['id']][0][0], bounds[floors[0]['id']][0][1]
        fx1, fy1 = bounds[floors[0]['id']][1][0], bounds[floors[0]['id']][1][1]
    else:
        fx0, fx1, fy0, fy1 = -W / 2, W / 2, -D / 2, D / 2
    tol = ctx.get('footprint_tolerance_m', 0.02)
    ran.append('inside_footprint')
    for i, (lo, hi) in sorted(bounds.items()):
        if comps[i].get('role') in EDGE_TRIM_ROLES or comps[i].get('role') == 'floor':
            continue
        over = max(fx0 - lo[0], hi[0] - fx1, fy0 - lo[1], hi[1] - fy1)
        if over > tol:
            finding('inside_footprint', 'advisory', [i], f'part reaches {over:.3f} m outside the floor rectangle', round(over, 3), tol)

    # ---- height limit
    if ctx.get('height_limit_m') is None:
        not_checked.append({'check': 'below_height_limit', 'reason': 'context height_limit_m is missing'})
    else:
        ran.append('below_height_limit')
        for i, (lo, hi) in sorted(bounds.items()):
            if hi[2] > ctx['height_limit_m'] + 1e-6:
                finding('below_height_limit', 'advisory', [i], f'top is {hi[2]:.3f} m, above the stated limit', round(hi[2], 3), ctx['height_limit_m'])

    # ---- modelled support path from the floor
    ran.append('support_path')
    supported = set()
    queue = [i for i, (lo, hi) in bounds.items() if abs(lo[2] - floor_top) <= CONTACT_TOL or comps[i].get('role') == 'floor']
    supported.update(queue)
    while queue:
        cur = queue.pop()
        for j in bounds:
            if j not in supported and _touching(bounds[cur], bounds[j]):
                supported.add(j)
                queue.append(j)
    hanging = ctx.get('hanging_allowed')
    for i in sorted(set(bounds) - supported):
        if hanging is True:
            finding('support_path', 'info', [i], 'no modelled contact path to the floor; hanging is allowed for this booth, so confirm the hanging connection')
        else:
            tail = 'hanging_allowed is unknown' if hanging is None else 'hanging is not allowed'
            finding('support_path', 'advisory', [i], f'no modelled contact path to the floor ({tail}); model a support or confirm an allowed hanging connection')

    # ---- header band
    bands = [i for i, c in comps.items() if c.get('role') == 'header_band']
    if bands:
        ran.append('header_band')
        hmin, hmax = _rng(priors, 'header_band_height')
        dmin, dmax = _rng(priors, 'header_band_depth')
        bottom_min = priors['header_band_bottom_min']['value']
        bottom_abs = priors['header_band_bottom_absolute_min']['value']
        margin = priors['header_band_top_margin']['value']
        for i in bands:
            lo, hi = bounds[i]
            h = hi[2] - lo[2]
            if not hmin - 1e-6 <= h <= hmax + 1e-6:
                finding('header_band_height', 'advisory', [i], f'band height {h:.2f} m is outside the usual {hmin}-{hmax} m', round(h, 3), [hmin, hmax])
            if comps[i]['kind'] == 'box':
                depth = min(hi[0] - lo[0], hi[1] - lo[1])
                if not dmin - 1e-6 <= depth <= dmax + 1e-6:
                    finding('header_band_depth', 'advisory', [i], f'band depth {depth:.2f} m is outside the usual {dmin}-{dmax} m', round(depth, 3), [dmin, dmax])
            else:
                not_checked.append({'check': 'header_band_depth', 'reason': f"{i} is a {comps[i]['kind']}; depth is only checked on straight box runs"})
            if lo[2] < bottom_abs - 1e-6:
                finding('header_band_bottom_min', 'advisory', [i], f'band bottom {lo[2]:.2f} m is below even the lowest usual value {bottom_abs} m', round(lo[2], 3), bottom_abs)
            elif lo[2] < bottom_min - 1e-6:
                finding('header_band_bottom_min', 'advisory', [i], f'band bottom {lo[2]:.2f} m is below the usual {bottom_min} m', round(lo[2], 3), bottom_min)
            if ctx.get('height_limit_m') is not None and hi[2] > ctx['height_limit_m'] - margin + 1e-6:
                finding('header_band_top_margin', 'advisory', [i], f'band top {hi[2]:.2f} m leaves less than {margin} m below the stated limit', round(hi[2], 3), ctx['height_limit_m'] - margin)

    # ---- counters (touching parts form one counter)
    counters = [i for i, c in comps.items() if c.get('role') == 'counter']
    if counters:
        ran.append('counter_dimensions')
        lmin, lmax = _rng(priors, 'counter_length')
        dmin, dmax = _rng(priors, 'counter_depth')
        hmin, hmax = _rng(priors, 'counter_height_standing')
        seated = priors['counter_height_seated']['value']
        for group in _clusters(counters, bounds):
            lo, hi = _span(group, bounds)
            dx, dy, top = hi[0] - lo[0], hi[1] - lo[1], hi[2] - floor_top
            length, depth = max(dx, dy), min(dx, dy)
            tag = sorted(group)
            if not (lmin * 0.95 <= length <= lmax * 1.05):
                finding('counter_length', 'advisory', tag, f'counter length {length:.2f} m is outside the usual {lmin}-{lmax} m', round(length, 3), [lmin, lmax])
            if not (dmin * 0.95 <= depth <= dmax * 1.05):
                finding('counter_depth', 'advisory', tag, f'counter depth {depth:.2f} m is outside the usual {dmin}-{dmax} m', round(depth, 3), [dmin, dmax])
            if not (hmin - 0.03 <= top <= hmax + 0.03 or abs(top - seated) <= 0.03):
                finding('counter_height', 'advisory', tag, f'counter top {top:.2f} m is neither a usual standing ({hmin}-{hmax} m) nor seated ({seated} m) height', round(top, 3), [hmin, hmax, seated])
            if ctx.get('open_sides') is None:
                if not any(n['check'] == 'counter_edge_clearance' for n in not_checked):
                    not_checked.append({'check': 'counter_edge_clearance', 'reason': 'context open_sides is missing'})
            else:
                edge = {'front': lo[1] - fy0, 'back': fy1 - hi[1], 'left': lo[0] - fx0, 'right': fx1 - hi[0]}
                need = priors['counter_edge_clearance_min']['value']
                for side in ctx['open_sides']:
                    if edge[side] < need - 1e-6:
                        finding('counter_edge_clearance', 'advisory', tag, f'counter is {max(edge[side], 0):.2f} m from the open {side} edge, less than the usual {need} m', round(edge[side], 3), need)

    # ---- displays
    displays = [i for i, c in comps.items() if c.get('role') == 'display']
    if displays:
        ran.append('display_size')
        tvs = {k: tuple(v) for k, v in priors['tv_picture_sizes']['sizes_m'].items()}
        cmin, cmax = _rng(priors, 'tv_centre_height')
        for i in displays:
            lo, hi = bounds[i]
            dims = sorted([hi[0] - lo[0], hi[1] - lo[1]], reverse=True)[0], hi[2] - lo[2]
            w, h = dims
            centre = (lo[2] + hi[2]) / 2
            if abs(w / h - 16 / 9) > 0.03 * 16 / 9:
                finding('display_aspect', 'advisory', [i], f'display is {w:.2f} x {h:.2f} m (ratio {w / h:.2f}), not 16:9', round(w / h, 3), round(16 / 9, 3))
            else:
                cls = min(tvs, key=lambda k: abs(tvs[k][0] - w))
                if abs(tvs[cls][0] - w) > 0.03 * tvs[cls][0]:
                    finding('display_size_class', 'info', [i], f'16:9 display {w:.2f} x {h:.2f} m does not match a listed {sorted(tvs)} inch class', round(w, 3))
            if not cmin - 1e-6 <= centre <= cmax + 1e-6:
                finding('display_centre_height', 'advisory', [i], f'display centre {centre:.2f} m is outside the usual {cmin}-{cmax} m', round(centre, 3), [cmin, cmax])

    # ---- LED walls in whole modules
    leds = [i for i, c in comps.items() if c.get('role') == 'led_wall']
    if leds:
        if ctx.get('led_module_m') is None:
            not_checked.append({'check': 'led_whole_modules', 'reason': 'context led_module_m is missing'})
        else:
            ran.append('led_whole_modules')
            m = ctx['led_module_m']
            for i in leds:
                lo, hi = bounds[i]
                for label, size in (('width', max(hi[0] - lo[0], hi[1] - lo[1])), ('height', hi[2] - lo[2])):
                    n = size / m
                    if abs(n - round(n)) > 0.01 * max(1, round(n)) or round(n) < 1:
                        finding('led_whole_modules', 'advisory', [i], f'LED {label} {size:.3f} m is {n:.2f} modules of {m} m, not a whole number', round(n, 3), m)

    # ---- ramps
    ramps = [i for i, c in comps.items() if c.get('role') == 'ramp']
    if ramps:
        ran.append('ramp')
        smax = priors['ramp_slope_max']['value']
        wmin = priors['ramp_width_min']['value']
        for i in ramps:
            lo, hi = bounds[i]
            rise = hi[2] - lo[2]
            run, width = sorted([hi[0] - lo[0], hi[1] - lo[1]], reverse=True)
            if run > 0 and rise / run > smax + 1e-6:
                finding('ramp_slope', 'advisory', [i], f'ramp rises {rise:.3f} m over {run:.2f} m (1:{run / rise:.1f}), steeper than the usual 1:{1 / smax:.0f}', round(rise / run, 4), smax)
            if width < wmin - 1e-6:
                finding('ramp_width', 'advisory', [i], f'ramp width {width:.2f} m is below the usual {wmin} m', round(width, 3), wmin)

    # ---- graphics
    graphics = [i for i, c in comps.items() if c.get('role') in ('graphic', 'lightbox')]
    if graphics:
        ran.append('graphic_bottom_min')
        gmin = priors['graphic_bottom_min']['value']
        for i in graphics:
            if bounds[i][0][2] - floor_top < gmin - 1e-6:
                finding('graphic_bottom_min', 'advisory', [i], f'graphic bottom is {bounds[i][0][2] - floor_top:.2f} m above the floor, below the usual {gmin} m', round(bounds[i][0][2] - floor_top, 3), gmin)

    # ---- narrow gaps between floor-standing furniture groups (information only)
    furn = [i for i, c in comps.items() if c.get('role') in NARROW_GAP_ROLES and bounds[i][0][2] - floor_top < 0.3]
    secondary = priors['path_secondary_min']['value']
    seating = [i for i in furn if comps[i].get('role') in SEATING_ROLES]
    groups = _clusters(seating, bounds, SEATING_JOIN_M) + _clusters([i for i in furn if i not in set(seating)], bounds)
    ran.append('narrow_gap')
    gaps = []
    for x in range(len(groups)):
        for y in range(x + 1, len(groups)):
            a, b = _span(groups[x], bounds), _span(groups[y], bounds)
            gx = max(a[0][0] - b[1][0], b[0][0] - a[1][0], 0.0)
            gy = max(a[0][1] - b[1][1], b[0][1] - a[1][1], 0.0)
            gap = math.hypot(gx, gy)
            if 0 < gap < secondary:
                gaps.append((gap, sorted(groups[x]) + sorted(groups[y])))
    for gap, ids in sorted(gaps)[:8]:
        finding('narrow_gap', 'info', ids, f'{gap:.2f} m between two furniture groups, less than the usual {secondary} m walkway', round(gap, 3), secondary)

    # ---- roles that only the generic checks above can look at
    generic = sorted({c['role'] for c in comps.values() if c.get('role') in GENERIC_ONLY_ROLES})
    if generic:
        not_checked.append({'check': 'role_specific_checks',
                            'reason': f"no role-specific check for {', '.join(generic)}; only the footprint, height limit and "
                                      'support path checks ran on those parts'})

    levels = [f['level'] for f in findings]
    return {'status': 'advisory_findings' if 'advisory' in levels else ('info_only' if findings else 'no_findings'),
            'counts': {k: levels.count(k) for k in ('advisory', 'info')}, 'findings': findings, 'not_checked': not_checked,
            'checks_run': ran, 'context': ctx, 'priors_reviewed': all(e.get('reviewed') for e in priors.values()), 'note': NOTE}


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    p.add_argument('--spec', required=True)
    p.add_argument('--context')
    p.add_argument('--output')
    a = p.parse_args()
    try:
        ctx = read_json(a.context) if a.context else {}
        result = run(read_json(a.spec), ctx)
    except (ValueError, KeyError, OSError) as ex:
        print(str(ex))
        return 2
    if a.output:
        write_json(a.output, result)
    print(json.dumps({k: result[k] for k in ('status', 'counts', 'checks_run')}, ensure_ascii=False))
    for f in result['findings'][:20]:
        print(f"  [{f['level']}] {f['check']}: {', '.join(f['ids'][:3])} - {f['message']}")
    for n in result['not_checked']:
        print(f"  [not_checked] {n['check']}: {n['reason']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
