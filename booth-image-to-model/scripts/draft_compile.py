"""Compile a scene description (what a reader sees in one booth image) into a booth-spec-v2 spec.

    python draft_compile.py --scene scene.json --image source.jpg --out out_dir [--fit box_fit.json]

The scene description is plain numbers: pixels, a few stated dimensions and a list of building blocks. Nothing in it is
trusted geometry. This script fits the camera from the clicked corners (box_fit), places every block by projecting its
pixels onto the floor or onto a booth edge (camera_math), builds it with the component macros (components), nudges
coplanar faces apart, validates the result and writes spec.json, box_fit.json and compile_report.json.
Nothing is written to the output folder until the whole compile has run. Needs numpy, scipy, Pillow and jsonschema like
the rest of the skill. No model call happens here.
"""
from __future__ import annotations
import argparse
import json
import math
import re
import shutil
import sys
from pathlib import Path

import camera_math as cmath
import components as cm
from geometry import mesh_for
from validate_spec import coplanar_overlaps, validate

SCHEMA_PATH = Path(__file__).resolve().parents[1] / 'schemas' / 'scene-description.v1.json'
ID_RE = re.compile(r'^[a-z][a-z0-9_]*$')
HEX_RE = re.compile(r'^#[0-9a-fA-F]{6}$')
CARPET = [0.30, 0.30, 0.31, 1]
FLOOR_T = 0.05                       # slab thickness below the walking surface
NUDGE = 0.002                        # coplanar faces are pulled apart by this much (m)
DUP_RADIUS = 0.25                    # downlights closer than this are one light
MAX_COMPONENTS, MAX_COUNT, MAX_ROWS, MAX_POINTS, MAX_DIM = 500, 200, 100, 200, 100.0
# What box_fit reports about the fit and the independent checks; report["fit"] copies these (absent keys become null).
# The gate has no pixel limits of its own: box_fit reports the ones it judged by under "limits", and the gate reads those.
FIT_KEYS = ('status', 'rms_px', 'max_px', 'dims_m', 'dim_source', 'worst_points', 'warnings', 'ambiguity_hint', 'hint',
            'loss', 'n_fit', 'n_check', 'check_points', 'check_median_px', 'check_max_px',
            'loo_reliable', 'loo_errors_px', 'loo_median_px', 'loo_max_px', 'outliers', 'dropped', 'rms_kept_px', 'n_unknown',
            'scale_checks', 'limits')
MACROS = {'counter', 'round_table', 'shell_chair', 'planter', 'louvre_wall', 'tv', 'led_wall', 'spotlights'}
COMMON = {'id', 'type', 'scope', 'evidence', 'roi_px', 'color_hex'}
# Fields each type reads. A field the scene fills but the type does not read is reported, never silently dropped.
USED = {
    'counter': COMMON | {'base_px', 'size_m', 'height_m', 'yaw_deg', 'accent_hex'},
    'round_table': COMMON | {'base_px', 'radius_m', 'height_m', 'accent_hex'},
    'shell_chair': COMMON | {'base_px', 'yaw_deg', 'accent_hex'},
    'planter': COMMON | {'base_px', 'size_m', 'height_m'},
    'box': COMMON | {'group', 'role', 'critical', 'note', 'base_px', 'size_m', 'height_m', 'z_range_m', 'yaw_deg', 'bevel_m', 'bevel_mode'},
    'cylinder': COMMON | {'group', 'role', 'critical', 'note', 'base_px', 'radius_m', 'size_m', 'height_m', 'z_range_m'},
    'louvre_wall': COMMON | {'base_px', 'base_px_2', 'count', 'width_m', 'thickness_m', 'size_m', 'height_m', 'z_range_m'},
    'wall': COMMON | {'group', 'role', 'critical', 'note', 'plane', 'base_px', 'base_px_2', 'thickness_m', 'inset_m', 'size_m', 'height_m', 'z_range_m'},
    'wall_panel': COMMON | {'group', 'role', 'critical', 'note', 'plane', 'wall_rect_px', 'thickness_m', 'inset_m'},
    'tv': COMMON | {'plane', 'wall_rect_px', 'inch', 'bezel_m', 'depth_m', 'inset_m', 'accent_hex'},
    'led_wall': COMMON | {'plane', 'wall_rect_px', 'count', 'rows', 'module_m', 'depth_m', 'inset_m'},
    'spotlights': COMMON | {'points_px', 'height_m'},
    'roof_frame': COMMON | {'group', 'role', 'critical', 'note', 'size_m', 'height_m', 'z_range_m', 'width_m', 'radius_m', 'inset_m'},
}


class SceneError(ValueError):
    """The scene description cannot be compiled. `.errors` lists {code, where, message}."""

    def __init__(self, errors):
        self.errors = errors
        super().__init__('; '.join(f"{e['code']} {e['where']}: {e['message']}" for e in errors[:6]))


# ------------------------------------------------------------------ validation of the scene description
def load_schema():
    return json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _int(v):
    return (isinstance(v, int) and not isinstance(v, bool)) or (isinstance(v, float) and math.isfinite(v) and v.is_integer())


def upgrade_scene(scene):
    """Scene files written before schema 1.1 have no corner_points[].fit: fill null (= used in the fit) so the strict schema,
    which requires the key, accepts them. Returns the same object when nothing is missing, otherwise a copy; never edits the input."""
    points = scene.get('corner_points') if isinstance(scene, dict) else None
    if not isinstance(points, list) or all(not isinstance(p, dict) or 'fit' in p for p in points):
        return scene
    out = dict(scene)
    out['corner_points'] = [dict(p, fit=None) if isinstance(p, dict) and 'fit' not in p else p for p in points]
    return out


def validate_scene(scene):
    """Structure (JSON Schema) first, then the rules a schema cannot say. Returns a list of {code, where, message}."""
    from jsonschema import Draft202012Validator
    scene = upgrade_scene(scene)
    errors = [{'code': 'SCHEMA', 'where': '/'.join(str(p) for p in e.absolute_path) or '(root)', 'message': e.message}
              for e in sorted(Draft202012Validator(load_schema()).iter_errors(scene), key=lambda e: list(map(str, e.absolute_path)))]
    if errors:
        return errors

    def bad(code, where, message):
        errors.append({'code': code, 'where': where, 'message': message})

    w, h = scene['image']['width'], scene['image']['height']
    if not (_int(w) and _int(h)) or w < 16 or h < 16:
        bad('IMAGE', 'image', 'width and height must be whole numbers of at least 16 px')
    if not ID_RE.fullmatch(scene['project_id']):
        bad('ID', 'project_id', 'use [a-z][a-z0-9_]*')
    if not _num(scene['floor_z']) or abs(scene['floor_z']) > MAX_DIM:
        bad('DIMENSION', 'floor_z', 'must be a finite number of metres')
    if len(scene['components']) > MAX_COMPONENTS:
        bad('LIMIT', 'components', f'at most {MAX_COMPONENTS} components')
    scopes = {}
    for s in scene['scopes']:
        if not s['id'] or not s['evidence'].strip():
            bad('SCOPE', f"scopes.{s['id']}", 'id and evidence must not be empty')
        if s['id'] in scopes:
            bad('SCOPE', f"scopes.{s['id']}", 'duplicate scope id')
        scopes[s['id']] = s
        if s['decision'] == 'include' and s['owner'] != 'booth':
            bad('SCOPE', f"scopes.{s['id']}", 'only booth-owned scopes can be included')
    if not any(s['decision'] == 'include' for s in scopes.values()):
        bad('SCOPE', 'scopes', 'at least one included scope is needed')
    if 'booth_floor' in scopes and (scopes['booth_floor']['decision'] != 'include' or scopes['booth_floor']['owner'] != 'booth'):
        bad('SCOPE', 'scopes.booth_floor', 'the floor lives in the scope "booth_floor"; it must be an included booth scope')
    seen = set()
    for p in scene['corner_points']:
        if not p['id'] or p['id'] in seen:
            bad('POINT', f"corner_points.{p['id']}", 'point ids must be non-empty and unique')
        seen.add(p['id'])
        if len(p['uv_px']) != 2 or not all(_num(v) for v in p['uv_px']):
            bad('POINT', f"corner_points.{p['id']}", 'uv_px must be [u, v]')
        if len(p['xyz']) != 3:
            bad('POINT', f"corner_points.{p['id']}", 'xyz needs three tokens')
    for s in scene['scale_checks']:
        if not s['id'] or s['id'] in seen:
            bad('POINT', f"scale_checks.{s['id']}", 'ids must be non-empty and unique across corner_points and scale_checks')
        seen.add(s['id'])
    for k in ('W', 'D', 'H'):
        v = scene['known'][k]
        if v is not None and not (_num(v) and 0 < v <= MAX_DIM):
            bad('DIMENSION', f'known.{k}', f'must be a number between 0 and {MAX_DIM:g} m')
    for k in scene['assumed']:
        if scene['known'][k] is None:
            bad('DIMENSION', f'assumed.{k}', 'assumed names must have a value in known')
    for u in scene['uncertainties']:
        if not u['id'].strip() or not u['description'].strip():
            bad('UNCERTAINTY', f"uncertainties.{u['id']}", 'id and description must not be empty')

    def pair(c, key, n, where):
        v = c[key]
        if v is None:
            return None
        if len(v) != n or not all(_num(x) for x in v):
            bad('SHAPE', where + '.' + key, f'must be {n} numbers')
            return None
        return v

    def inside(point, where):
        if point and not (-0.05 * w <= point[0] <= 1.05 * w and -0.05 * h <= point[1] <= 1.05 * h):
            bad('PIXEL', where, f'pixel {point} is outside the {w}x{h} image')

    ids, macro_ids = set(), set()
    for c in scene['components']:
        where = f"components.{c['id']}"
        t = c['type']
        if not ID_RE.fullmatch(c['id']) or c['id'] == 'floor':
            bad('ID', where, 'use [a-z][a-z0-9_]* and not "floor"')
        if c['id'] in ids:
            bad('ID', where, 'duplicate component id')
        ids.add(c['id'])
        if t in MACROS:
            macro_ids.add(c['id'])
        if c['scope'] not in scopes or scopes[c['scope']]['decision'] != 'include':
            bad('SCOPE', where, f"scope {c['scope']!r} is not an included scope")
        if c['group'] is not None and not ID_RE.fullmatch(c['group']):
            bad('ID', where + '.group', 'use [a-z][a-z0-9_]*')
        if c['role'] is not None and not c['role'].strip():
            bad('ID', where + '.role', 'must not be empty')
        for key in ('color_hex', 'accent_hex'):
            if c[key] is not None and not HEX_RE.fullmatch(c[key]):
                bad('COLOR', f'{where}.{key}', 'use #rrggbb')
        roi = pair(c, 'roi_px', 4, where)
        if roi:
            if not (roi[0] < roi[2] and roi[1] < roi[3]):
                bad('SHAPE', where + '.roi_px', 'must be [x0, y0, x1, y1] with x0 < x1 and y0 < y1')
            elif roi[2] <= 0 or roi[3] <= 0 or roi[0] >= w or roi[1] >= h:
                bad('PIXEL', where + '.roi_px', f'the region lies outside the {w}x{h} image')
        if c['evidence'] == 'observed' and roi is None:
            bad('EVIDENCE', where, 'observed parts need roi_px (use evidence "inferred" for hidden or assumed parts)')
        for key in ('base_px', 'base_px_2'):
            inside(pair(c, key, 2, where), f'{where}.{key}')
        rect = pair(c, 'wall_rect_px', 4, where)
        if rect:
            inside(rect[:2], where + '.wall_rect_px')
            inside(rect[2:], where + '.wall_rect_px')
            if not (rect[0] < rect[2] and rect[1] < rect[3]):
                bad('SHAPE', where + '.wall_rect_px', 'must be top-left then bottom-right')
        size = c['size_m']
        if size is not None and (len(size) != 3 or any(v is not None and not (_num(v) and 0 < v <= MAX_DIM) for v in size)):
            bad('SHAPE', where + '.size_m', 'must be [width, depth, height] of positive numbers or nulls')
            size = None
        zr = pair(c, 'z_range_m', 2, where)
        if zr and not (zr[0] < zr[1] and abs(zr[0]) <= MAX_DIM and abs(zr[1]) <= MAX_DIM):
            bad('SHAPE', where + '.z_range_m', 'must be [bottom, top] with bottom < top')
        pts = c['points_px']
        if pts is not None:
            if len(pts) > MAX_POINTS:
                bad('LIMIT', where + '.points_px', f'at most {MAX_POINTS} points')
            for p in pts:
                if len(p) != 2 or not all(_num(x) for x in p):
                    bad('SHAPE', where + '.points_px', 'each point must be [u, v]')
                else:
                    inside(p, where + '.points_px')
        for key in ('radius_m', 'height_m', 'width_m', 'thickness_m', 'depth_m', 'module_m', 'bezel_m'):
            v = c[key]
            if v is not None and not (_num(v) and 0 < v <= MAX_DIM):
                bad('SHAPE', f'{where}.{key}', f'must be a number between 0 and {MAX_DIM:g}')
        for key in ('bevel_m', 'inset_m'):
            v = c[key]
            if v is not None and not (_num(v) and 0 <= v <= MAX_DIM):
                bad('SHAPE', f'{where}.{key}', f'must be a number between 0 and {MAX_DIM:g}')
        if c['yaw_deg'] is not None and not (_num(c['yaw_deg']) and abs(c['yaw_deg']) <= 3600):
            bad('SHAPE', where + '.yaw_deg', 'must be a finite number of degrees')
        for key, limit in (('count', MAX_COUNT), ('rows', MAX_ROWS), ('inch', 200)):
            v = c[key]
            if v is not None and not (_int(v) and 1 <= v <= limit):
                bad('SHAPE', f'{where}.{key}', f'must be a whole number between 1 and {limit}')

        def need(*keys):
            for key in keys:
                if c[key] is None:
                    bad('MISSING', f'{where}.{key}', f'{t} needs {key}')

        def need_height():
            if c['height_m'] is None and zr is None and not (size and size[2]):
                bad('MISSING', where, f'{t} needs height_m, z_range_m or size_m[2]')
        if t == 'counter':
            need('base_px')
            if not (size and size[0]):
                bad('MISSING', where + '.size_m', 'counter needs a width in size_m[0]')
        elif t in ('round_table', 'shell_chair', 'planter'):
            need('base_px')
        elif t == 'box':
            need('base_px')
            if not (size and size[0] and size[1]):
                bad('MISSING', where + '.size_m', 'box needs width and depth in size_m')
            need_height()
        elif t == 'cylinder':
            need('base_px', 'radius_m')
            need_height()
        elif t == 'louvre_wall':
            need('base_px', 'base_px_2', 'count', 'width_m')
            need_height()
        elif t == 'wall':
            need('plane')
            need_height()
            if (c['base_px'] is None) != (c['base_px_2'] is None):
                bad('MISSING', where, 'give both base_px and base_px_2 to limit the wall, or neither for the whole edge')
        elif t == 'wall_panel':
            need('plane', 'wall_rect_px')
        elif t == 'tv':
            need('plane', 'wall_rect_px', 'inch')
            if c['inch'] is not None and c['inch'] not in cm.TV:
                bad('MISSING', where + '.inch', f'unknown size class; choose one of {sorted(cm.TV)}')
        elif t == 'led_wall':
            need('plane', 'wall_rect_px', 'count', 'rows')
            if c['plane'] not in (None, 'back'):
                bad('UNSUPPORTED', where + '.plane', 'led_wall is only supported on the back edge')
        elif t == 'spotlights':
            need('height_m')
            if not pts:
                bad('MISSING', where + '.points_px', 'spotlights need at least one point')
        elif t == 'roof_frame':
            need_height()
    for a in macro_ids:
        for other in ids - {a}:
            if other.startswith(a + '_'):
                bad('ID', f'components.{other}', f'collides with the parts of macro {a!r} (they are named {a}_...)')
    return errors


def ignored_fields(scene):
    """Warnings for fields the scene fills but the component's type does not read."""
    out = []
    for c in scene['components']:
        used = USED[c['type']]
        for key, value in c.items():
            if key not in used and value is not None:
                out.append(f"{c['id']}: {key} is not used by type {c['type']} and was ignored")
    return out


# ------------------------------------------------------------------ camera fit
def unconstrained_checks(scene):
    """Ids of check corners (fit false) that box_fit leaves out on purpose: they use a dimension or a ?name that no fitted corner
    constrains, so they cannot be placed with the fitted camera. They were never in the fit, so they are not a stale-fit problem."""
    import box_fit
    rows = []
    for p in scene['corner_points']:
        try:
            rows.append((p, [box_fit.parse_token(t) for t in p['xyz']]))
        except ValueError:
            continue                                                      # a bad token is box_fit's error to report
    fitted = [tokens for p, tokens in rows if p.get('fit') is not False]
    known = {k for k, v in scene['known'].items() if v is not None}
    dims = known | {t[1] for tokens in fitted for t in tokens if t[0] in ('half', 'dim')}
    frees = {t[1] for tokens in fitted for t in tokens if t[0] == 'free'}
    return [p['id'] for p, tokens in rows if p.get('fit') is False
            and not all(t[1] in frees if t[0] == 'free' else (t[1] in dims if t[0] in ('half', 'dim') else True) for t in tokens)]


def fit_camera(scene, seeds=40):
    import box_fit
    data = {'image': dict(scene['image']), 'floor_z': scene['floor_z'],
            'known': {k: v for k, v in scene['known'].items() if v is not None}, 'assumed': list(scene['assumed']),
            'points': [dict({'id': p['id'], 'uv_px': p['uv_px'], 'xyz': p['xyz']}, **({'fit': False} if p.get('fit') is False else {}))
                       for p in scene['corner_points']]}
    checks = [{k: v for k, v in s.items() if not (k == 'tolerance' and v is None)} for s in scene['scale_checks']]
    if checks:
        data['scale_checks'] = checks
    try:
        return box_fit.solve(data, seeds)
    except (ValueError, KeyError) as ex:
        raise SceneError([{'code': 'FIT_FAILED', 'where': 'corner_points', 'message': str(ex)}]) from ex


def check_fit(scene, fit, supplied):
    """A fit (given or just computed) must be complete and agree with the scene."""
    errors = []

    def bad(message):
        errors.append({'code': 'FIT_MISMATCH' if supplied else 'DIMENSION', 'where': 'fit', 'message': message})
    if not isinstance(fit, dict) or fit.get('status') == 'blocked':
        raise SceneError([{'code': 'FIT_FAILED', 'where': 'fit', 'message': str((fit or {}).get('reason', 'the fit is empty or blocked'))}])
    cam = fit.get('camera')
    if not isinstance(cam, dict) or any(k not in cam for k in ('position', 'target', 'lens_mm', 'sensor_width_mm')):
        bad('the fit has no usable camera')
    dims = fit.get('dims_m')
    if not isinstance(dims, dict):
        bad('the fit has no dims_m')
        dims = {}
    for k in 'WDH':
        if not _num(dims.get(k)):
            bad(f'{k} is unknown: give known.{k} or add a corner point that fixes it')
        elif scene['known'][k] is not None and abs(dims[k] - scene['known'][k]) > 1e-4 and supplied:
            bad(f"known.{k}={scene['known'][k]:g} but the given fit has {k}={dims[k]:g}; refit after editing the scene")
    if supplied:
        img = fit.get('image') or {}
        if (img.get('width'), img.get('height')) != (scene['image']['width'], scene['image']['height']):
            bad(f"the given fit is for a {img.get('width')}x{img.get('height')} image, the scene says {scene['image']['width']}x{scene['image']['height']}")
        if fit.get('floor_z') is not None and abs(fit['floor_z'] - scene['floor_z']) > 1e-6:
            bad(f"floor_z={scene['floor_z']:g} but the given fit has {fit['floor_z']:g}")
    if errors:
        raise SceneError(errors)


# ------------------------------------------------------------------ quality gate
def _limit(limits, *names):
    """A pixel limit from the "limits" box_fit reported, or None when it did not report that one (the gate then applies none)."""
    if isinstance(limits, dict):
        for name in names:
            value = limits.get(name)
            if _num(value) and value > 0:
                return float(value)
    return None


def _loo_numbers(fit):
    """-> (median, worst, outliers) of the leave-one-out check, or (None, None, []) when box_fit says it is not reliable.

    box_fit reports "loo_reliable": false (and null numbers) when too few equations remain after holding a corner out, because the
    names such a check gives are guesses. A fit that does not say (older files, hand-written ones) is trusted as far as it has numbers."""
    if fit.get('loo_reliable') is False:
        return None, None, []
    median, worst = fit.get('loo_median_px'), fit.get('loo_max_px')
    return (median if _num(median) else None), (worst if _num(worst) else None), [str(i) for i in (fit.get('outliers') or [])]


def _scale_check_reasons(fit):
    reasons = []
    for c in fit.get('scale_checks') or []:
        if not isinstance(c, dict) or c.get('ok') is not False:
            continue
        name = c.get('id') or 'a standard part'
        measured = c.get('measured_m', c.get('measured_height_m'))
        expected = c.get('expected_m', c.get('expected_height_m'))
        ratio = c.get('ratio')
        if c.get('error') or not (_num(measured) and _num(expected)):
            reasons.append(f"a size check failed: {name} could not be measured" + (f" ({c['error']})" if c.get('error') else '')
                           + ', so the scale has no cross-check')
        else:
            reasons.append(f"a size check failed: {name} measures {measured:.2f} m where about {expected:.2f} m is expected"
                           + (f' (ratio {ratio:.2f})' if _num(ratio) else '')
                           + ': the assumed size, the clicks or the expected size is wrong')
    return reasons


def quality_gate(fit):
    """-> {"status": "pass" | "review", "reasons": [plain-English strings]}.

    "review" means the camera and scale are not trustworthy enough to render the final picture without a person looking:
    the fit itself is poor (status), clicks the others cannot predict are named, the independent errors (check corners,
    leave-one-out) are above the limits box_fit reports, a size check on a standard part failed, or there is no independent
    signal at all. A clean fit with a reliable leave-one-out check is a pass; check corners (fit false) are optional.
    Reads only box_fit's output; every number that is absent is treated as not measured and no limit that box_fit did not
    report is made up."""
    reasons = []
    status = fit.get('status')
    if status == 'high_residual':
        rms = fit.get('rms_px')
        reasons.append(f"the corner fit leaves {rms:.1f} px of error (rms): some clicked corners disagree with a box seen in perspective"
                       if _num(rms) else 'the corner fit leaves a high error: some clicked corners disagree with a box seen in perspective')
    elif status == 'ambiguous':
        names = ', '.join(fit.get('ambiguous_dims') or [])
        reasons.append('the clicked corners do not pin down the size' + (f' ({names})' if names else '') + ': several different boxes fit equally well')
    elif status not in ('fitted', 'check_failed'):
        reasons.append(f'the fit status is {status!r}, not fitted')

    limits = fit.get('limits')
    specific = []                                       # what makes a check_failed status concrete
    check_points = fit.get('check_points')
    n_check = fit.get('n_check')
    if not _int(n_check) or isinstance(n_check, bool):
        n_check = len(check_points) if isinstance(check_points, list) else 0
    median, worst = fit.get('check_median_px'), fit.get('check_max_px')
    have_check = _num(median)
    if n_check and not have_check:
        specific.append('the check corners could not be measured')
    elif have_check:
        median_limit, worst_limit = _limit(limits, 'check_median_px', 'check_median'), _limit(limits, 'check_max_px', 'check_max')
        if median_limit is not None and median > median_limit:
            specific.append(f'the check corners are off by {median:.1f} px on median (limit {median_limit:g} px): '
                            'the fit does not predict corners it was not fitted to')
        elif worst_limit is not None and _num(worst) and worst > worst_limit:
            specific.append(f'a check corner is off by {worst:.1f} px (limit {worst_limit:g} px): the fit does not predict it')

    loo_median, loo_worst, outliers = _loo_numbers(fit)
    have_loo = loo_median is not None
    if have_loo:
        median_limit, worst_limit = _limit(limits, 'loo_median_px', 'loo_median'), _limit(limits, 'loo_max_px', 'loo_max')
        if median_limit is not None and loo_median > median_limit:
            specific.append(f'leaving one corner out moves it by {loo_median:.1f} px on median (limit {median_limit:g} px): '
                            'the corners do not agree with each other')
        elif worst_limit is not None and loo_worst is not None and loo_worst > worst_limit:
            specific.append(f'leaving one corner out moves it by {loo_worst:.1f} px (limit {worst_limit:g} px): one corner may be mis-clicked')
    if outliers:
        dropped = set(str(i) for i in (fit.get('dropped') or []))
        specific.append('corners that disagree with the rest of the fit (possible mis-clicks): ' + ', '.join(outliers)
                        + ('; the camera was refitted without ' + ('them' if dropped >= set(outliers) else ', '.join(sorted(dropped))) if dropped else ''))
    if status == 'check_failed' and not specific:
        specific.append('the fit looks fine but it does not predict the corners it was not fitted to (check or left-out corners)')
    reasons += specific
    reasons += _scale_check_reasons(fit)
    if not have_check and not have_loo and not n_check:
        reasons.append('there is no independent check of the fit: there are too few corners to predict each one from the others; '
                       'add well-spread corners (the fit\'s limits.loo_min_fit says how many are needed), '
                       'or add extra corners as check corners (fit false)')
    return {'status': 'review' if reasons else 'pass', 'reasons': reasons}


def fit_note(fit):
    """One short sentence on the independent checks for scale.notes."""
    parts = []
    if _num(fit.get('check_median_px')):
        parts.append(f"{fit.get('n_check') or len(fit.get('check_points') or [])} check corners off by {fit['check_median_px']:.1f} px (median)")
    loo_median = _loo_numbers(fit)[0]
    if loo_median is not None:
        parts.append(f"leave-one-out error {loo_median:.1f} px (median)")
    note = ('Independent checks: ' + ', '.join(parts) + '.') if parts else 'No independent check of the fit.'
    dropped = [str(i) for i in (fit.get('dropped') or [])]
    return note + (' The camera was refitted without the clicks ' + ', '.join(dropped) + '.' if dropped else '')


# ------------------------------------------------------------------ compiler
def _rgba(hex_color, default):
    if not hex_color:
        return list(default)
    h = hex_color.lstrip('#')
    return [round(int(h[i:i + 2], 16) / 255, 4) for i in (0, 2, 4)] + [1]


class _Group:
    """`with` block that groups the parts added inside it, or does nothing when there is no group name."""

    def __init__(self, builder, name):
        self.cm = builder.grouped(name) if name else None

    def __enter__(self):
        return self.cm.__enter__() if self.cm else None

    def __exit__(self, *exc):
        return self.cm.__exit__(*exc) if self.cm else False


class Compiler:
    def __init__(self, scene, fit, source_image):
        self.scene, self.fit = scene, fit
        self.cam = fit['camera']
        self.dims = fit['dims_m']
        self.W, self.D, self.H = (self.dims[k] for k in 'WDH')
        self.X0, self.X1, self.Y0, self.Y1 = -self.W / 2, self.W / 2, -self.D / 2, self.D / 2
        self.zf = scene['floor_z']
        self.iw, self.ih = scene['image']['width'], scene['image']['height']
        self.b = cm.SpecBuilder(source_image, self.iw, self.ih, scene['project_id'])
        self.report = {'components': [], 'assumed': [], 'adjustments': [], 'dropped': [], 'warnings': ignored_fields(scene)}

    # -- helpers
    def assume(self, cid, what, value):
        self.report['assumed'].append({'id': cid, 'what': what, 'value': value})

    def adjust(self, cid, what, **extra):
        self.report['adjustments'].append({'id': cid, 'what': what, **extra})

    def warn(self, message):
        self.report['warnings'].append(message)

    def evidence(self, c):
        roi = tuple(c['roi_px']) if c['roi_px'] else None
        observed = c['evidence'] == 'observed' and roi is not None
        return (roi if observed else None), not observed

    def floor_xy(self, pixel):
        p = cmath.floor_point(pixel[0], pixel[1], self.cam, self.iw, self.ih, self.zf)
        return p[0], p[1]

    def inside(self, cid, x, y, hw, hd):
        """Keep a part of half extents (hw, hd) inside the footprint; a part larger than the booth is reported."""
        if hw * 2 > self.W + 1e-9 or hd * 2 > self.D + 1e-9:
            self.warn(f'{cid}: {2 * hw:.2f} x {2 * hd:.2f} m is larger than the booth ({self.W:g} x {self.D:g} m); centred')
        nx = 0.0 if hw * 2 >= self.W else min(max(x, self.X0 + hw), self.X1 - hw)
        ny = 0.0 if hd * 2 >= self.D else min(max(y, self.Y0 + hd), self.Y1 - hd)
        if math.hypot(nx - x, ny - y) > 0.005:
            self.adjust(cid, 'moved inside the footprint', frm=[round(x, 3), round(y, 3)], to=[round(nx, 3), round(ny, 3)])
        return nx, ny

    def edge(self, plane, inset):
        """-> (axis, coordinate of the wall face, inward sign, (along_lo, along_hi), booth extent across the wall)."""
        if plane == 'back':
            return 'y', self.Y1 - inset, -1, (self.X0, self.X1), self.D
        if plane == 'front':
            return 'y', self.Y0 + inset, 1, (self.X0, self.X1), self.D
        if plane == 'left':
            return 'x', self.X0 + inset, 1, (self.Y0, self.Y1), self.W
        return 'x', self.X1 - inset, -1, (self.Y0, self.Y1), self.W

    def face(self, cid, plane, inset, thickness=0.0):
        axis, face, sign, along, across = self.edge(plane, inset)
        if inset + thickness > across:
            raise ValueError(f'inset {inset:g} m (plus {thickness:g} m thick) does not fit across the booth ({across:g} m)')
        return axis, face, sign, along

    def zrange(self, c):
        if c['z_range_m']:
            return tuple(c['z_range_m'])
        height = c['height_m'] if c['height_m'] is not None else c['size_m'][2]
        return self.zf, self.zf + height

    def on_plane(self, c, axis, value):
        a, z = cmath.rect_on_plane(tuple(c['wall_rect_px'][:2]), tuple(c['wall_rect_px'][2:]), self.cam, self.iw, self.ih, axis, value)
        return a, z

    def fit_along(self, cid, a0, a1, lo, hi, what):
        """Slide a part of fixed width along a booth edge until it fits; refuse when it is wider than the edge."""
        if a1 <= lo or a0 >= hi:
            raise ValueError(f'{what} lies outside the booth edge ({a0:.2f} to {a1:.2f} m, the edge runs {lo:.2f} to {hi:.2f} m)')
        if a1 - a0 > hi - lo + 1e-9:
            raise ValueError(f'{what} ({a1 - a0:.2f} m) is wider than the booth edge ({hi - lo:.2f} m)')
        shift = lo - a0 if a0 < lo else (hi - a1 if a1 > hi else 0.0)
        if abs(shift) > 0.005:
            self.adjust(cid, f'moved along the wall by {shift:+.3f} m to stay within the booth edge')
        return a0 + shift, a1 + shift

    # -- one handler per type; each adds its parts through the builder
    def counter(self, c):
        x, y = self.floor_xy(c['base_px'])
        w, d, hgt = c['size_m']
        if d is None:
            d = cm.PRIOR['counter_depth']
            self.assume(c['id'], 'depth_m', d)
        hgt = hgt or c['height_m']
        if hgt is None:
            hgt = cm.PRIOR['counter_height']
            self.assume(c['id'], 'height_m', hgt)
        yaw = c['yaw_deg'] or 0.0
        k = round(yaw / 90)
        if abs(yaw - 90 * k) > 5:
            self.warn(f"{c['id']}: counter yaw {yaw} is not a multiple of 90 and was ignored")
        elif k % 2:
            w, d = d, w
        x, y = self.inside(c['id'], x, y, w / 2 + 0.02, d / 2 + 0.02)       # the top slab overhangs by 2 cm
        px, inferred = self.evidence(c)
        cm.counter(self.b, c['id'], c['scope'], (x - w / 2, x + w / 2), (y - d / 2, y + d / 2), floor_z=self.zf, height=hgt,
                   body_color=_rgba(c['color_hex'], cm.WHITE), top_color=_rgba(c['accent_hex'], cm.WHITE) if c['accent_hex'] else None,
                   px=px, inferred=inferred)

    def round_table(self, c):
        x, y = self.floor_xy(c['base_px'])
        r, hgt = c['radius_m'], c['height_m']
        if r is None:
            r = cm.PRIOR['table_radius_4p']
            self.assume(c['id'], 'radius_m', r)
        if hgt is None:
            hgt = cm.PRIOR['table_height']
            self.assume(c['id'], 'height_m', hgt)
        x, y = self.inside(c['id'], x, y, r, r)
        px, inferred = self.evidence(c)
        extra = {'stem_color': _rgba(c['accent_hex'], cm.METAL_DARK)} if c['accent_hex'] else {}
        cm.round_table(self.b, c['id'], c['scope'], x, y, floor_z=self.zf, radius=r, height=hgt,
                       color=_rgba(c['color_hex'], cm.WHITE), px=px, inferred=inferred, **extra)

    def shell_chair(self, c):
        x, y = self.floor_xy(c['base_px'])
        x, y = self.inside(c['id'], x, y, 0.3, 0.3)
        self.assume(c['id'], 'seat_height_m', cm.PRIOR['seat_height'])
        px, inferred = self.evidence(c)
        extra = {'leg_color': _rgba(c['accent_hex'], cm.WOOD)} if c['accent_hex'] else {}
        cm.shell_chair(self.b, c['id'], c['scope'], x, y, c['yaw_deg'] or 0.0, floor_z=self.zf,
                       color=_rgba(c['color_hex'], cm.WHITE), px=px, inferred=inferred, **extra)

    def planter(self, c):
        x, y = self.floor_xy(c['base_px'])
        w, d, hgt = c['size_m'] or [None, None, None]
        if w is None or d is None:
            w, d = w or 0.4, d or 0.4
            self.assume(c['id'], 'width/depth_m', [w, d])
        hgt = hgt or c['height_m']
        if hgt is None:
            hgt = 0.45
            self.assume(c['id'], 'height_m', hgt)
        x, y = self.inside(c['id'], x, y, w / 2, d / 2)
        px, inferred = self.evidence(c)
        cm.planter(self.b, c['id'], c['scope'], (x - w / 2, x + w / 2), (y - d / 2, y + d / 2), floor_z=self.zf, height=hgt,
                   color=_rgba(c['color_hex'], cm.WHITE), px=px, inferred=inferred)

    def box(self, c):
        x, y = self.floor_xy(c['base_px'])
        w, d, _ = c['size_m']
        yaw = c['yaw_deg'] or 0.0
        a = math.radians(yaw)
        hw = abs(w / 2 * math.cos(a)) + abs(d / 2 * math.sin(a))              # half extents of the turned footprint
        hd = abs(w / 2 * math.sin(a)) + abs(d / 2 * math.cos(a))
        x, y = self.inside(c['id'], x, y, hw, hd)
        px, inferred = self.evidence(c)
        extra = {}
        if c['bevel_m']:
            extra['bevel'] = (c['bevel_m'], c['bevel_mode'] or 'all')
        if yaw:
            extra['rot'] = (0, 0, yaw)
        with _Group(self.b, c['group']):
            self.b.box(c['id'], c['scope'], c['role'] or 'box', (x - w / 2, x + w / 2), (y - d / 2, y + d / 2), self.zrange(c),
                       _rgba(c['color_hex'], cm.WHITE), critical=bool(c['critical']), px=px, inferred=inferred, note=c['note'], **extra)

    def cylinder(self, c):
        x, y = self.floor_xy(c['base_px'])
        r = c['radius_m']
        x, y = self.inside(c['id'], x, y, r, r)
        px, inferred = self.evidence(c)
        with _Group(self.b, c['group']):
            self.b.cylinder(c['id'], c['scope'], c['role'] or 'cylinder', x, y, self.zrange(c), r, _rgba(c['color_hex'], cm.WHITE),
                            critical=bool(c['critical']), px=px, inferred=inferred, note=c['note'])

    def louvre_wall(self, c):
        (x1, y1), (x2, y2) = self.floor_xy(c['base_px']), self.floor_xy(c['base_px_2'])
        dx, dy = abs(x2 - x1), abs(y2 - y1)
        along = 'y' if dy > dx else 'x'
        tilt = math.degrees(math.atan2(min(dx, dy), max(dx, dy)))
        if tilt > 15:
            self.warn(f"{c['id']}: the marked row is {tilt:.0f} degrees off the nearest booth axis; built along {along}")
        a1, a2, other = (y1, y2, (x1 + x2) / 2) if along == 'y' else (x1, x2, (y1 + y2) / 2)
        t = c['thickness_m']
        if t is None:
            t = 0.06
            self.assume(c['id'], 'thickness_m', t)
        lo_b, hi_b = (self.Y0, self.Y1) if along == 'y' else (self.X0, self.X1)
        lo_o, hi_o = (self.X0, self.X1) if along == 'y' else (self.Y0, self.Y1)
        lo, hi = max(min(a1, a2), lo_b), min(max(a1, a2), hi_b)
        other = min(max(other, lo_o + t / 2), hi_o - t / 2)
        if (lo, hi) != (min(a1, a2), max(a1, a2)):
            self.adjust(c['id'], 'row trimmed to the booth edge', frm=[round(min(a1, a2), 3), round(max(a1, a2), 3)], to=[round(lo, 3), round(hi, 3)])
        width, count = c['width_m'], int(c['count'])
        if hi - lo < width:
            raise ValueError(f'the marked row ({hi - lo:.2f} m) is shorter than one fin ({width:g} m)')
        if count > 1 and count * width > hi - lo + 1e-9:
            raise ValueError(f'{count} fins of {width:g} m do not fit in the marked {hi - lo:.2f} m row')
        span = (lo, hi) if count > 1 else ((lo + hi) / 2 - width / 2, (lo + hi) / 2 + width / 2)
        px, inferred = self.evidence(c)
        cm.louvre_wall(self.b, c['id'], c['scope'], span, (other - t / 2, other + t / 2), self.zrange(c), count, width,
                       _rgba(c['color_hex'], cm.WHITE), along=along, px=px, inferred=inferred)

    def wall(self, c):
        t = c['thickness_m']
        if t is None:
            t = 0.1
            self.assume(c['id'], 'thickness_m', t)
        axis, face, sign, (lo, hi) = self.face(c['id'], c['plane'], c['inset_m'] or 0.0, t)
        if c['base_px'] and c['base_px_2']:
            ends = [self.floor_xy(c['base_px']), self.floor_xy(c['base_px_2'])]
            k = 0 if axis == 'y' else 1
            lo, hi = max(lo, min(e[k] for e in ends)), min(hi, max(e[k] for e in ends))
        normal = tuple(sorted((face, face + sign * t)))
        xr, yr = ((lo, hi), normal) if axis == 'y' else (normal, (lo, hi))
        px, inferred = self.evidence(c)
        role = c['role'] or ('back_wall' if c['plane'] == 'back' else 'end_wall')
        with _Group(self.b, c['group']):
            self.b.box(c['id'], c['scope'], role, xr, yr, self.zrange(c), _rgba(c['color_hex'], cm.WHITE),
                       critical=True if c['critical'] is None else c['critical'], px=px, inferred=inferred, note=c['note'])

    def wall_panel(self, c):
        inset = 0.1 if c['inset_m'] is None else c['inset_m']
        t = c['thickness_m']
        if t is None:
            t = 0.05
            self.assume(c['id'], 'thickness_m', t)
        axis, face, sign, (lo, hi) = self.face(c['id'], c['plane'], inset, t)
        (a0, a1), zr = self.on_plane(c, axis, face)
        a0, a1 = max(a0, lo), min(a1, hi)
        if not a0 < a1:
            raise ValueError('the rectangle lies outside the booth edge')
        normal = tuple(sorted((face, face + sign * t)))
        xr, yr = ((a0, a1), normal) if axis == 'y' else (normal, (a0, a1))
        px, inferred = self.evidence(c)
        with _Group(self.b, c['group']):
            self.b.box(c['id'], c['scope'], c['role'] or 'wall_panel', xr, yr, zr, _rgba(c['color_hex'], cm.WHITE),
                       critical=bool(c['critical']), px=px, inferred=inferred, note=c['note'])

    def tv(self, c):
        inset = 0.1 if c['inset_m'] is None else c['inset_m']
        axis, face, sign, (lo, hi) = self.face(c['id'], c['plane'], inset)
        (a0, a1), (z0, z1) = self.on_plane(c, axis, face)
        bezel, depth = c['bezel_m'], c['depth_m']
        if bezel is None:
            bezel = 0.02
            self.assume(c['id'], 'bezel_m', bezel)
        if depth is None:
            depth = 0.05
            self.assume(c['id'], 'depth_m', depth)
        pw, ph = cm.TV[int(c['inch'])]
        if abs((a1 - a0) - (pw + 2 * bezel)) > 0.25 * (pw + 2 * bezel) or abs((z1 - z0) - (ph + 2 * bezel)) > 0.25 * (ph + 2 * bezel):
            self.warn(f"{c['id']}: a {c['inch']}-inch class tv is about {pw + 2 * bezel:.2f} x {ph + 2 * bezel:.2f} m but the marked rectangle is "
                      f"{a1 - a0:.2f} x {z1 - z0:.2f} m; built at the class size")
        centre = (a0 + a1) / 2
        n0, n1 = self.fit_along(c['id'], centre - pw / 2 - bezel, centre + pw / 2 + bezel, lo, hi, 'the tv')
        looking = {'back': '-y', 'front': '+y', 'left': '+x', 'right': '-x'}[c['plane']]
        px, inferred = self.evidence(c)
        extra = {'frame_color': _rgba(c['color_hex'], cm.METAL_DARK)} if c['color_hex'] else {}
        if c['accent_hex']:
            extra['screen_color'] = _rgba(c['accent_hex'], cm.DARK_SCREEN)
        cm.tv(self.b, c['id'], c['scope'], face, (n0 + n1) / 2, int(c['inch']), bezel=bezel, depth=depth, centre_z=(z0 + z1) / 2,
              face=looking, px=px, inferred=inferred, **extra)

    def led_wall(self, c):
        inset = 0.1 if c['inset_m'] is None else c['inset_m']
        axis, face, sign, (lo, hi) = self.face(c['id'], 'back', inset)
        (a0, a1), (z0, z1) = self.on_plane(c, axis, face)
        module, depth = c['module_m'], c['depth_m']
        if module is None:
            module = 0.5
            self.assume(c['id'], 'module_m', module)
        if depth is None:
            depth = 0.1
            self.assume(c['id'], 'depth_m', depth)
        cols, rows = int(c['count']), int(c['rows'])
        if abs(cols * module - (a1 - a0)) > 0.15 * (a1 - a0) or abs(rows * module - (z1 - z0)) > 0.15 * (z1 - z0):
            self.warn(f"{c['id']}: {cols}x{rows} modules of {module} m are {cols * module:.2f}x{rows * module:.2f} m but the marked rectangle is "
                      f"{a1 - a0:.2f}x{z1 - z0:.2f} m; built from whole modules")
        x_left, _ = self.fit_along(c['id'], a0, a0 + cols * module, lo, hi, 'the led wall')
        px, inferred = self.evidence(c)
        cm.led_wall(self.b, c['id'], c['scope'], face, x_left, z0, cols=cols, rows=rows, module=module, depth=depth,
                    face_color=_rgba(c['color_hex'], cm.DARK_SCREEN), px=px, inferred=inferred)

    def spotlights(self, c):
        z_top = c['height_m']
        kept = []
        for i, (u, v) in enumerate(c['points_px']):
            try:
                p = cmath.unproject(u, v, self.cam, self.iw, self.ih, 'z', z_top)
            except ValueError as ex:
                self.adjust(c['id'], f'light {i} dropped', why=str(ex))
                continue
            x, y = self.inside(c['id'], p[0], p[1], 0.3, 0.3)
            if any(math.hypot(x - kx, y - ky) < DUP_RADIUS for kx, ky in kept):
                self.adjust(c['id'], f'light {i} dropped as a duplicate position', at=[round(x, 3), round(y, 3)])
                continue
            kept.append((round(x, 3), round(y, 3)))
        if not kept:
            raise ValueError('no light position could be placed')
        self.assume(c['id'], 'fixture body', 'radius 0.04 m, length 0.12 m')
        px, inferred = self.evidence(c)
        cm.spotlights(self.b, c['id'], c['scope'], kept, z_top, px=px, inferred=inferred)

    def roof_frame(self, c):
        band, radius = c['width_m'], c['radius_m']
        if band is None:
            band = 0.6
            self.assume(c['id'], 'width_m', band)
        if radius is None:
            radius = 0.8
            self.assume(c['id'], 'radius_m', radius)
        inset = c['inset_m'] or 0.0
        ow, od = self.W - 2 * inset, self.D - 2 * inset
        iw_, id_ = ow - 2 * band, od - 2 * band
        if min(iw_, id_) <= 0.1:
            raise ValueError(f'a {band:g} m band leaves no opening in a {ow:.2f} x {od:.2f} m frame')
        radius = min(radius, min(ow, od) / 2 - 0.05)
        inner_radius = min(max(0.05, radius - band / 2), min(iw_, id_) / 2 - 0.02)
        z0, z1 = c['z_range_m'] if c['z_range_m'] else (self.H - (c['height_m'] if c['height_m'] is not None else c['size_m'][2]), self.H)
        params = {'outer_size': [round(ow, 4), round(od, 4)], 'inner_size': [round(iw_, 4), round(id_, 4)],
                  'outer_radius': round(radius, 4), 'inner_radius': round(inner_radius, 4),
                  'height': round(z1 - z0, 4), 'segments_per_corner': 16}
        px, inferred = self.evidence(c)
        with _Group(self.b, c['group']):
            self.b.add(c['id'], c['scope'], 'rounded_ring', c['role'] or 'roof_frame', [0.0, 0.0, (z0 + z1) / 2], params,
                       _rgba(c['color_hex'], cm.WHITE), critical=True if c['critical'] is None else c['critical'],
                       px=px, inferred=inferred, note=c['note'])

    # -- the whole scene
    def build(self):
        sc, b, rep = self.scene, self.b, self.report
        for s in sc['scopes']:
            b.scope(s['id'], s['evidence'], owner=s['owner'], decision=s['decision'])
        if 'booth_floor' not in b.scopes:
            b.scope('booth_floor', 'Visible floor of the booth.')
        floor_pts = [p['uv_px'] for p in sc['corner_points'] if str(p['xyz'][2]).strip() == 'F']
        px = None
        if len(floor_pts) >= 3:
            us, vs = [p[0] for p in floor_pts], [p[1] for p in floor_pts]
            px = (min(us), min(vs), max(us), max(vs))
        b.box('floor', 'booth_floor', 'floor', (self.X0, self.X1), (self.Y0, self.Y1), (self.zf - FLOOR_T, self.zf), CARPET,
              critical=True, px=px, inferred=px is None, note='Floor from the stated footprint; colour is a placeholder.')
        for c in sc['components']:
            before = len(b.components)
            marks = {k: len(rep[k]) for k in ('assumed', 'adjustments', 'warnings')}
            try:
                getattr(self, c['type'])(c)
                for added in b.components[before:]:                     # the builder's own refusals become a dropped part, not a crash
                    try:
                        mesh_for(added)
                    except ValueError as ex:
                        raise ValueError(f"{added['id']}: {ex}") from ex
            except (ValueError, KeyError, ZeroDivisionError) as ex:
                gone = {x['id'] for x in b.components[before:]}
                del b.components[before:]
                b._ids -= gone
                b.requirements = [r for r in b.requirements if r['component_id'] not in gone]
                for k, n in marks.items():                              # nothing the failed part recorded may survive it
                    del rep[k][n:]
                rep['dropped'].append({'id': c['id'], 'reason': str(ex)})
                continue
            made = [x['id'] for x in b.components[before:]]
            rep['components'].append({'id': c['id'], 'type': c['type'], 'parts': len(made), 'ids': made[:3] + (['...'] if len(made) > 3 else [])})
        for sid in [k for k, v in b.scopes.items() if v['decision'] == 'include' and not any(x['scope_id'] == k for x in b.components)]:
            del b.scopes[sid]
            self.adjust(sid, 'scope removed: it ended up without geometry')
        b.camera(self.cam)
        self.landmarks()
        sources = ', '.join(f"{k}={self.dims[k]:g} ({'assumed' if k in sc['assumed'] else self.fit['dim_source'].get(k, 'fitted')})" for k in 'WDH')
        b.scale((self.W, self.D), (f"Footprint and height: {sources}. Camera from a corner fit ({self.fit['status']}, rms {self.fit['rms_px']} px). "
                                   f"{fit_note(self.fit)} {sc['scale_notes'] or ''} Compiled from a scene description; not measured.").replace('  ', ' '))
        for u in sc['uncertainties']:
            b.uncertain(u['id'], u['description'], critical=u['critical'])
        b.uncertain('metric_dimensions', 'Single-image estimate; confirm before any BOM or construction use.')
        if rep['assumed']:
            items = [f"{a['id']}.{a['what']}={a['value']}" for a in rep['assumed']]
            shown = items[:25] + ([f'and {len(items) - 25} more (see compile_report.json)'] if len(items) > 25 else [])
            b.uncertain('compile_assumed_values', 'Values assumed by the compiler: ' + '; '.join(shown))
        return b.build()

    def landmarks(self):
        """Floor corners whose x and y resolve become landmarks on the floor; check corners (fit false) and the clicks box_fit
        dropped from the final camera ("dropped") are held-out ones. Corners off the floor have no part to attach to; the error of
        a check corner is reported in report["fit"]["check_points"] only."""
        import box_fit
        flagged = [p['id'] for p in self.scene['corner_points'] if p.get('fit') is False]
        reported = self.fit.get('check_points')
        listed = {c.get('id') for c in (reported if isinstance(reported, list) else []) if isinstance(c, dict)}
        skipped = set(self.fit.get('skipped_checks') or []) | set(unconstrained_checks(self.scene))   # left out on purpose, never fitted
        unlisted = [i for i in flagged if i not in listed and i not in skipped]
        if unlisted:
            self.warn('check corners ' + ', '.join(unlisted) + " are not listed in the fit's check_points: "
                      'they may have been used in the fit (refit with the current box_fit)')
        dropped = {str(i) for i in (self.fit.get('dropped') or [])}      # clicks box_fit left out of the final camera: not used in the fit
        for p in self.scene['corner_points']:
            try:
                tx, ty, tz = (box_fit.parse_token(t) for t in p['xyz'])
            except ValueError:
                continue
            if tz[0] == 'floor' and tx[0] == 'half' and ty[0] == 'half':
                world = [tx[2] * self.dims[tx[1]] / 2, ty[2] * self.dims[ty[1]] / 2, self.zf]       # x and y each follow their own token
                try:
                    self.b.landmark(p['id'], 'floor', world, p['uv_px'], fit=p.get('fit') is not False and p['id'] not in dropped)
                except ValueError:
                    pass


def separate_coplanar(spec, report, limit=8):
    """Pull coplanar same-facing faces of later boxes inward by 2 mm; whatever cannot be fixed is returned.
    The floor is never moved: its size is the footprint."""
    order = {c['id']: i for i, c in enumerate(spec['components'])}
    by_id = {c['id']: c for c in spec['components']}

    def scan():
        meshes = {c['id']: mesh_for(c) for c in spec['components'] if c['kind'] != 'text'}
        return coplanar_overlaps(spec['components'], meshes)
    for _ in range(limit):
        found = scan()
        if not found:
            return []
        changed = False
        for w in found:
            sign = 1 if w['plane'][0] == '+' else -1
            axis = 'xyz'.index(w['plane'][1])
            for cid in sorted(w['ids'], key=lambda i: -order[i]):
                c = by_id[cid]
                if (c['role'] == 'floor' or c['kind'] != 'box' or any(c['rotation_deg']) or 'bevel_radius' in c['params']
                        or c['params']['size'][axis] <= 4 * NUDGE):
                    continue
                c['params']['size'][axis] = round(c['params']['size'][axis] - NUDGE, 4)
                c['location'][axis] = round(c['location'][axis] - sign * NUDGE / 2, 4)
                other = [i for i in w['ids'] if i != cid][0]
                report['adjustments'].append({'id': cid, 'what': f"face {w['plane']} pulled in by {NUDGE * 1000:g} mm to avoid z-fighting with {other}"})
                changed = True
                break
        if not changed:
            break
    return scan()


def outside_footprint(spec, tolerance=0.02):
    """Parts whose bounding box leaves the footprint (the roof frame and the floor itself are expected to match it)."""
    from geometry import transform_point
    x0, x1 = -spec['scale']['footprint_m'][0] / 2, spec['scale']['footprint_m'][0] / 2
    y0, y1 = -spec['scale']['footprint_m'][1] / 2, spec['scale']['footprint_m'][1] / 2
    out = []
    for c in spec['components']:
        if c['kind'] == 'text' or c['role'] == 'floor':
            continue
        verts, _ = mesh_for(c)
        world = [transform_point(v, c['location'], c['rotation_deg']) for v in verts]
        over = max(x0 - min(p[0] for p in world), max(p[0] for p in world) - x1, y0 - min(p[1] for p in world), max(p[1] for p in world) - y1)
        if over > tolerance:
            out.append((c['id'], round(over, 3)))
    return out


def _write(path, data):
    path.write_bytes((json.dumps(data, indent=2, ensure_ascii=False) + '\n').encode('utf-8'))


def compile_scene(scene, image_path, out_dir, fit=None, seeds=40):
    """-> (spec, report). Writes spec.json, box_fit.json, compile_report.json and a copy of the image into out_dir,
    but only after the whole compile has run, so a failure never leaves a half-updated folder."""
    from PIL import Image
    scene = upgrade_scene(scene)
    errors = validate_scene(scene)
    if not errors:
        try:
            with Image.open(image_path) as im:
                size = im.size
        except (OSError, ValueError) as ex:
            raise SceneError([{'code': 'IMAGE', 'where': 'image', 'message': f'cannot read {image_path}: {ex}'}]) from ex
        if size != (scene['image']['width'], scene['image']['height']):
            errors.append({'code': 'IMAGE', 'where': 'image', 'message':
                           f"scene says {scene['image']['width']}x{scene['image']['height']} but the file is {size[0]}x{size[1]}"})
    if errors:
        raise SceneError(errors)
    supplied = fit is not None
    fit = fit if supplied else fit_camera(scene, seeds)
    check_fit(scene, fit, supplied)
    source = Path(image_path).resolve()
    comp = Compiler(scene, fit, source)
    spec = comp.build()
    report = comp.report
    if supplied:
        done = {c.get('id') for c in (fit.get('scale_checks') or []) if isinstance(c, dict)}       # a given fit may carry its own results
        if any(s['id'] not in done for s in scene['scale_checks']):
            report['warnings'].append('scale_checks were not evaluated: a given fit was used (drop --fit to run them)')
    leftover = separate_coplanar(spec, report)
    for cid, over in outside_footprint(spec):
        report['warnings'].append(f'{cid}: extends {over:.2f} m outside the footprint')
    spec['source']['image'] = source.name
    check = validate(spec, source.parent, source_size=[scene['image']['width'], scene['image']['height']])
    final_name = 'source' + source.suffix.lower()
    spec['source']['image'] = final_name
    if check['valid']:
        report['warnings'] += [f"{w['code']} {w.get('ids') or w.get('id') or ''} {w.get('plane', '')}".strip() for w in check['warnings']]
    report['warnings'] += ['fit: ' + w for w in (fit.get('warnings') or [])]
    ambiguity_hint = fit.get('ambiguity_hint')          # only a real ambiguity hint; box_fit's "hint" can also be re-click advice
    if ambiguity_hint is None and 'ambiguity_hint' not in fit and fit.get('status') == 'ambiguous':
        ambiguity_hint = fit.get('hint')                # a fit written before the two keys were kept apart
    for advice in dict.fromkeys(str(h) for h in (ambiguity_hint, fit.get('hint')) if h):
        report['warnings'].append('fit: ' + advice)
    report['fit'] = {k: fit.get(k) for k in FIT_KEYS}
    report['fit']['ambiguity_hint'] = ambiguity_hint
    report['quality_gate'] = quality_gate(fit)
    report['valid'] = check['valid']
    report['validation_errors'] = check['errors'][:20]
    report['coplanar_unresolved'] = [{'ids': w['ids'], 'plane': w['plane']} for w in leftover]
    report['status'] = 'invalid' if not check['valid'] else ('partial' if report['dropped'] else 'ok')
    report['components_built'] = len(spec['components'])
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob('source.*'):
        if old.name != final_name:
            old.unlink()
    target = out / final_name
    if not (target.exists() and target.samefile(source)):
        shutil.copyfile(source, target)
    for name, data in (('spec.json', spec), ('box_fit.json', fit), ('compile_report.json', report)):
        _write(out / name, data)
    return spec, report


def run(scene_path, image_path, out_dir, fit_path=None, seeds=40):
    """Files in, files out. Returns (exit_code, summary); every expected failure is a summary, not a traceback."""
    try:
        scene = json.loads(Path(scene_path).read_text(encoding='utf-8'))
        fit = json.loads(Path(fit_path).read_text(encoding='utf-8')) if fit_path else None
        spec, report = compile_scene(scene, image_path, out_dir, fit, seeds)
    except SceneError as ex:
        return 2, {'status': 'scene_invalid', 'errors': ex.errors[:30]}
    except ImportError as ex:
        return 2, {'status': 'missing_dependency', 'message': f'{ex}; install the packages in requirements.txt'}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as ex:
        return 2, {'status': 'input_error', 'message': f'{type(ex).__name__}: {ex}'}
    summary = {k: report[k] for k in ('status', 'components_built', 'valid')}
    summary.update(fit=report['fit']['status'], rms_px=report['fit']['rms_px'], fit_warnings=(report['fit']['warnings'] or [])[:3],
                   fit_check={k: report['fit'][k] for k in ('n_fit', 'n_check', 'check_median_px', 'check_max_px', 'loo_reliable',
                                                           'loo_median_px', 'loo_max_px', 'outliers')},
                   quality_gate=report['quality_gate'], dropped=report['dropped'], adjustments=len(report['adjustments']), assumed=len(report['assumed']),
                   warnings=report['warnings'][:10], validation_errors=report['validation_errors'][:5], spec=str(Path(out_dir) / 'spec.json'))
    return (0 if report['valid'] else 2), summary


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    p.add_argument('--scene', required=True)
    p.add_argument('--image', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--fit', help='Existing box_fit.json to use instead of fitting the corner points again')
    p.add_argument('--seeds', type=int, default=40)
    a = p.parse_args()
    code, summary = run(a.scene, a.image, a.out, a.fit, a.seeds)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
