"""Anchor one, part one: six spec variants with a known order of badness, made from one baseline spec.

    python tools/poc/score/perturb_spec.py --spec <spec.json> --fit <box_fit.json> --truth <truth_dir> [--map <map.json>]
                                           [--exclude <inventory id>=<reason> ...] --out <dir>

Writes <dir>/B1/spec.json .. <dir>/B6/spec.json, <dir>/expected_order.json (["B1", .., "B6"], best first) and <dir>/perturb_report.json
(which part and components each variant touched, and the pixel move that was actually reached). The variants (docs/poc/03-scoring-spec.md
section 5):

    B1  the part that covers the accent swatch gets another colour, delta E 76 of about 15
    B2  one non-hero part moves 20 px
    B3  one hero part moves 60 px
    B4  one non-hero part is removed
    B5  one hero part is removed
    B6  one hero part and two non-hero parts are removed, and the colours of the dominant and secondary parts are swapped

Inputs. --spec is the baseline spec (it must pass validate_spec). --fit is box_fit.json: its camera and image size are used for every
projection. --truth is the answer-sheet folder: inventory.json says which parts are hero parts, swatches.json says where the
dominant, secondary and accent colours are. --map is the same {"inventory_id": component id or group, or a list of them} file that
score.py takes; a part that is not listed is found by the exact id or group of the inventory id. A name in the map that is neither
a component id nor a group of the spec, or a key that is not an inventory id, is an error.

Choices (all deterministic, recorded in perturb_report.json). A part is an inventory item that resolves to at least one component and
holds no floor component (the floor cannot move or vanish: the footprint and the scope depend on it). A swatch belongs to the
component that is most visible inside the swatch box: a small z-buffer image of component ids is drawn from the baseline spec
and the --fit camera (engine meshes, camera_math.project, numpy; no Blender), so a hollow ring, a leaf or a piece of furniture in
front of a wall does not take the wall's swatch. Parts are tried in this order: parts that hold none of the swatch components,
then the part of the accent swatch, then the parts of the dominant and secondary swatches; inside a group the inventory order
decides. B6 never removes a part that holds the dominant or secondary swatch component (it needs them for the colour swap).
A recolour or a swap acts on the whole part: the swatch component and every other component of the same inventory part that has
the same colour (and colour space) change together, so a counter's body and its top stay one colour.
A move shifts every component of the part together along the ground axis (X or Y) that projects closest to the image x direction,
by d_m = px * Z / f_px (Z the depth of the part centre, f_px the focal length in pixels), then corrects d_m a few times with
camera_math.project until the centre has moved by the asked number of pixels (a ground axis is rarely parallel to the image plane).
A removed part takes its components, its requirements and its landmarks with it; a scope decision with no component left becomes
"defer" so that the variant still validates. The swap exchanges the colour and the colour space of the two components. The picture
that spec.source.image names (a path relative to the spec's folder) is copied into every B<n>/ under the same relative path, so that
each variant folder validates with validate_spec --check-source and can be previewed.

--exclude <inventory id>=<reason> (repeatable) keeps a whole inventory part out of every variant: the part is never moved, removed,
recoloured or swapped, and the deterministic choice goes to the next eligible part in the same order. Use it when the visible shape
of a part is not made by its own components (a door opening cut into another part's panel); the reason cites the spec's geometry
only. Exclusions are whole items and are never widened silently. The reason is required; an id that is not in the inventory, a
missing '=', an empty reason or an id given twice is an error (exit 2). So are these two cases, each naming what is involved:
another inventory item that shares a component with an excluded item (exclude it too, with its own reason, or fix the map), and an
excluded item that owns the component a swatch resolves to (the accent swatch for B1, the dominant and secondary swatches for the
B6 swap); an exclusion never changes what B1 recolours or what B6 swaps, and the choice never falls through to the next visible
component. If the exclusions leave a variant without a part, the run stops with TOO_FEW_PARTS naming the variants. perturb_report.json
then lists the exclusions as {id, reason} and, for B2 to B6, the part each would have used without them (part_without_exclusions).
Without --exclude nothing in the output changes.

All six variants are validated with validate_spec before anything is written; if one is not valid, or the inventory has too few
hero parts (B3, B5, B6) or non-hero parts (B2, B4, B6), the tool says so on stderr, exits with 2 and writes nothing.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import math
import shutil
import sys
from pathlib import Path

try:
    from . import metrics, score as scoring
except ImportError:                  # run as a script: tools/poc/score is sys.path[0]
    import metrics
    import score as scoring

import numpy as np
import validate_spec  # noqa: E402  (the engine's scripts folder is on sys.path once metrics is imported)

camera_math = metrics.camera_math
InputError = scoring.InputError
VARIANTS = ('B1', 'B2', 'B3', 'B4', 'B5', 'B6')
MOVE_SMALL_PX, MOVE_BIG_PX = 20, 60
RECOLOUR_DE = 15.0
PX_TOLERANCE = 0.25              # a move is corrected until the centre is this close to the asked pixels
ROLES = ('dominant', 'secondary', 'accent')


# ---- pixels to metres ----------------------------------------------------------------------------------------------

def px_to_metres(px, depth, f_px):
    """Metres that make a point at `depth` metres along the view axis move `px` pixels across the image: d_m = px * Z / f_px."""
    return px * depth / f_px


def _centre(comps):
    pts = []
    for c in comps:
        pts.extend(metrics.component_points(c))
    low, high = metrics.points_bounds(pts)
    return [(a + b) / 2 for a, b in zip(low, high)]


def _ground_axis(centre, camera, width, height):
    """(axis index, sign): the ground axis (0 = X, 1 = Y) whose projection is closest to the image x direction, and the sign that
    makes the part go right in the image."""
    u0, v0, _ = camera_math.project(centre, camera, width, height)
    best = None
    for axis in (0, 1):
        p = list(centre)
        p[axis] += 0.1
        u, v, _ = camera_math.project(p, camera, width, height)
        du, dv = u - u0, v - v0
        share = abs(du) / math.hypot(du, dv) if math.hypot(du, dv) > 0 else 0.0
        if best is None or share > best[0] + 1e-12:
            best = (share, axis, 1 if du >= 0 else -1)
    return best[1], best[2]


def move_part(comps, camera, width, height, px):
    """Copies of `comps` moved together by `px` pixels (measured on the part centre) -> (moved components, info dict)."""
    centre = _centre(comps)
    u0, v0, z = camera_math.project(centre, camera, width, height)
    f_px = camera_math.focal_px(camera, width)
    axis, sign = _ground_axis(centre, camera, width, height)
    if not 0 <= u0 + sign * px <= width:                    # keep the part in the picture
        sign = -sign
    metres = sign * px_to_metres(px, z, f_px)
    achieved = 0.0
    for _ in range(12):
        moved = list(centre)
        moved[axis] += metres
        u1, v1, _ = camera_math.project(moved, camera, width, height)
        achieved = math.hypot(u1 - u0, v1 - v0)
        if abs(achieved - px) <= PX_TOLERANCE or achieved < 1e-9:
            break
        metres *= px / achieved
    out = copy.deepcopy(comps)
    for c in out:
        loc = list(c['location'])
        loc[axis] += metres
        c['location'] = loc
    return out, {'axis': 'xy'[axis], 'metres': metres, 'requested_px': px, 'achieved_px': achieved, 'depth_m': z, 'focal_px': f_px}


def move_components(comps, camera, width, height, px):
    return move_part(comps, camera, width, height, px)[0]


# ---- colour --------------------------------------------------------------------------------------------------------

_WHITE = (0.95047, 1.0, 1.08883)                  # D65, as OpenCV and the scoring card use
_M = ((0.4124564, 0.3575761, 0.1804375), (0.2126729, 0.7151522, 0.0721750), (0.0193339, 0.1191920, 0.9503041))
_MI = ((3.2404542, -1.5371385, -0.4985314), (-0.9692660, 1.8760108, 0.0415560), (0.0556434, -0.2040259, 1.0572252))
_DELTA = 6 / 29


def _to_linear(v):
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def _to_srgb(v):
    return 12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055


def srgb_to_lab(rgb):
    lin = [_to_linear(min(1.0, max(0.0, float(v)))) for v in rgb[:3]]
    xyz = [sum(_M[i][j] * lin[j] for j in range(3)) / _WHITE[i] for i in range(3)]
    f = [t ** (1 / 3) if t > _DELTA ** 3 else t / (3 * _DELTA ** 2) + 4 / 29 for t in xyz]
    return [116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2])]


def lab_to_srgb(lab):
    """sRGB in [0, 1], clipped to the gamut."""
    fy = (lab[0] + 16) / 116
    f = [fy + lab[1] / 500, fy, fy - lab[2] / 200]
    xyz = [(t ** 3 if t > _DELTA else 3 * _DELTA ** 2 * (t - 4 / 29)) * _WHITE[i] for i, t in enumerate(f)]
    lin = [sum(_MI[i][j] * xyz[j] for j in range(3)) for i in range(3)]
    return [min(1.0, max(0.0, _to_srgb(min(1.0, max(0.0, v))))) for v in lin]


def delta_e76(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _directions(lab):
    """Unit directions in Lab to try, a hue turn first (the tangent of the chroma circle), then lightness, then a and b."""
    dirs = []
    chroma = math.hypot(lab[1], lab[2])
    if chroma > 10:
        t = (0.0, -lab[2] / chroma, lab[1] / chroma)
        dirs += [t, (0.0, -t[1], -t[2])]
    away = 1.0 if lab[0] < 50 else -1.0
    dirs += [(away, 0.0, 0.0), (-away, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, 1.0), (0.0, 0.0, -1.0)]
    return dirs


def recolour_srgb(rgb, de=RECOLOUR_DE):
    """Another sRGB colour (3 values in [0, 1]) at CIE76 distance `de` from `rgb`, found by growing the step until the gamut clip
    no longer shortens it; raises ValueError if no direction reaches it."""
    lab = srgb_to_lab(rgb)
    for d in _directions(lab):
        step = de
        for _ in range(40):
            cand = lab_to_srgb([lab[i] + step * d[i] for i in range(3)])
            got = delta_e76(lab, srgb_to_lab(cand))
            if abs(got - de) <= 0.25:
                return cand
            if got < 1e-6 or step > 400:
                break
            step *= de / got
    raise ValueError(f'no colour at delta E {de} from {list(rgb)[:3]}')


def _recolour_component(comp, de=RECOLOUR_DE):
    """Copy of `comp` with another material colour (same colour space and alpha) -> (component, achieved delta E)."""
    out = copy.deepcopy(comp)
    mat = out['material']
    space = mat.get('color_space', 'linear')
    rgb = list(mat['color'][:3])
    display = rgb if space == 'srgb' else [_to_srgb(min(1.0, max(0.0, v))) for v in rgb]      # delta E is judged on the screen colour
    new = recolour_srgb(display, de)
    achieved = delta_e76(srgb_to_lab(display), srgb_to_lab(new))
    mat['color'] = [round(v if space == 'srgb' else _to_linear(v), 6) for v in new] + list(mat['color'][3:])
    return out, achieved


# ---- parts and swatches --------------------------------------------------------------------------------------------

def _bbox_px(comps, camera, width, height):
    """Projected pixel bounding box (x0, y0, x1, y1) of the components, or None when a point is behind the camera."""
    us, vs = [], []
    for c in comps:
        for p in metrics.component_points(c):
            try:
                u, v, _ = camera_math.project(p, camera, width, height)
            except ValueError:
                return None
            us.append(u)
            vs.append(v)
    return (min(us), min(vs), max(us), max(vs)) if us else None


def _read_truth(truth):
    inv = scoring.read_json(Path(truth) / 'inventory.json')
    sw = scoring.read_json(Path(truth) / 'swatches.json')
    items = inv.get('items') if isinstance(inv, dict) else None
    where = Path(truth) / 'inventory.json'
    if not (isinstance(items, list) and items):
        raise InputError(f'BAD_TRUTH: {where}: a non-empty items list is required')
    for n, it in enumerate(items):
        if not (isinstance(it, dict) and isinstance(it.get('id'), str) and it['id']):
            raise InputError(f'BAD_TRUTH: {where}: items[{n}] must be an object with a text id')
        if it['id'] in {o['id'] for o in items[:n] if isinstance(o, dict) and isinstance(o.get('id'), str)}:
            raise InputError(f'BAD_TRUTH: {where}: items[{n}].id {it["id"]!r} appears twice; inventory ids must be unique')
        if 'hero' in it and not isinstance(it['hero'], bool):
            raise InputError(f'BAD_TRUTH: {where}: items[{n}].hero must be true or false')
    swatches = sw.get('swatches') if isinstance(sw, dict) else None
    where = Path(truth) / 'swatches.json'
    if not isinstance(swatches, list):
        raise InputError(f'BAD_TRUTH: {where}: a swatches list is required')
    for n, s in enumerate(swatches):
        if not (isinstance(s, dict) and isinstance(s.get('role'), str)):
            raise InputError(f'BAD_TRUTH: {where}: swatches[{n}] must be an object with a text role')
        box = s.get('bbox_px')
        if not (isinstance(box, list) and len(box) == 4 and all(_is_number(v) for v in box) and box[2] > box[0] and box[3] > box[1]):
            raise InputError(f'BAD_TRUTH: {where}: swatches[{n}].bbox_px must be four numbers [x0, y0, x1, y1] with x1 > x0 and y1 > y0')
    return items, swatches


def _check_map(spec, mapping):
    ids = {c.get('id') for c in spec['components']} | {c.get('group') for c in spec['components'] if c.get('group')}
    for part, names in mapping.items():
        for n in names:
            if n not in ids:
                raise InputError(f'MAP_NAME_ABSENT: {n!r} (for {part!r}) is neither a component id nor a group of the baseline spec')


def _parts(spec, items, mapping, camera, width, height):
    """Inventory items that can be moved or removed, in inventory order: [{id, hero, comps, area}]."""
    parts = []
    for n, it in enumerate(items):
        comps = metrics.matching_components(spec, it['id'], mapping.get(it['id']))
        if not comps or any(c.get('role') == 'floor' for c in comps):
            continue
        box = _bbox_px(comps, camera, width, height)
        if box is None:
            continue
        parts.append({'id': it['id'], 'hero': it.get('hero') is True, 'index': n, 'comps': [c['id'] for c in comps]})
    return parts


def render_ids(spec, camera, width, height, window=None):
    """Nearest component per pixel of `window` (x0, y0, x1, y1 integers, default the whole picture), drawn with a z-buffer.

    -> (ids, idmap): `ids` lists the component ids, idmap is an int array of the window's size holding an index into `ids`, or -1
    where nothing is drawn. Triangles come from the engine's meshes, vertices through camera_math.project; a triangle with a vertex
    behind the camera is left out, and a component without a mesh is not drawn.
    """
    x0, y0, x1, y1 = window or (0, 0, width, height)
    ids, tris, idx = [], [], []
    for c in spec['components']:
        try:
            verts, faces = metrics.geometry.mesh_for(c)
        except (ValueError, KeyError, TypeError):
            continue
        proj = []
        for v in verts:
            world = metrics.geometry.transform_point(v, c.get('location', [0, 0, 0]), c.get('rotation_deg', [0, 0, 0]))
            try:
                proj.append(camera_math.project(world, camera, width, height))
            except ValueError:
                proj.append(None)
        ids.append(c['id'])
        for f in metrics.geometry.triangulate(faces):
            p = [proj[i] for i in f]
            if all(q is not None for q in p):
                tris.append(p)
                idx.append(len(ids) - 1)
    idmap = np.full((max(0, y1 - y0), max(0, x1 - x0)), -1, np.int32)
    if not tris or x1 <= x0 or y1 <= y0:
        return ids, idmap
    tri = np.asarray(tris, float)                                # (n, 3, 3): u, v, depth
    keep = ((tri[:, :, 0].max(1) >= x0) & (tri[:, :, 0].min(1) <= x1) & (tri[:, :, 1].max(1) >= y0) & (tri[:, :, 1].min(1) <= y1))
    zbuf = np.full(idmap.shape, np.inf)
    gx, gy = np.meshgrid(np.arange(x0, x1) + 0.5, np.arange(y0, y1) + 0.5)
    for t, owner in zip(tri[keep], np.asarray(idx)[keep]):
        (ax, ay, az), (bx, by, bz), (cx, cy, cz) = t
        den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(den) < 1e-12:
            continue
        lx0, lx1 = max(x0, int(math.floor(min(ax, bx, cx)))), min(x1, int(math.ceil(max(ax, bx, cx))))
        ly0, ly1 = max(y0, int(math.floor(min(ay, by, cy)))), min(y1, int(math.ceil(max(ay, by, cy))))
        if lx1 <= lx0 or ly1 <= ly0:
            continue
        sl = (slice(ly0 - y0, ly1 - y0), slice(lx0 - x0, lx1 - x0))
        X, Y = gx[sl], gy[sl]
        l1 = ((by - cy) * (X - cx) + (cx - bx) * (Y - cy)) / den
        l2 = ((cy - ay) * (X - cx) + (ax - cx) * (Y - cy)) / den
        l3 = 1 - l1 - l2
        inside = (l1 >= -1e-9) & (l2 >= -1e-9) & (l3 >= -1e-9)
        z = 1.0 / (l1 / az + l2 / bz + l3 / cz)                  # perspective-correct depth
        nearer = inside & (z < zbuf[sl])
        zbuf[sl] = np.where(nearer, z, zbuf[sl])
        idmap[sl] = np.where(nearer, owner, idmap[sl])
    return ids, idmap


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _swatch_components(spec, swatches, camera, width, height):
    """{role: [component ids, the one with most visible pixels inside the swatch box first]} for the roles in ROLES."""
    out = {}
    for role in ROLES:
        sw = next((s for s in swatches if s.get('role') == role), None)
        if sw is None:
            raise InputError(f'NO_SWATCH: swatches.json has no {role} swatch')
        x0, y0, x1, y1 = sw['bbox_px']
        window = (max(0, int(math.floor(x0))), max(0, int(math.floor(y0))), min(width, int(math.ceil(x1))), min(height, int(math.ceil(y1))))
        ids, idmap = render_ids(spec, camera, width, height, window)
        counts = np.bincount(idmap[idmap >= 0], minlength=len(ids))
        seen = sorted(((-int(n), ids[i]) for i, n in enumerate(counts) if n > 0))
        if not seen:
            raise InputError(f'NO_PART_UNDER_SWATCH: the {role} swatch (box {x0:g}, {y0:g}, {x1:g}, {y1:g} px) shows no part of the baseline spec')
        out[role] = [i for _, i in seen]
    return out


# ---- the six variants ----------------------------------------------------------------------------------------------

def _remove(spec, comp_ids):
    """Copy of `spec` without the components, their requirements and landmarks; emptied scope decisions become "defer"."""
    out = copy.deepcopy(spec)
    gone = set(comp_ids)
    out['components'] = [c for c in out['components'] if c['id'] not in gone]
    left = {c.get('scope_id') for c in out['components']}
    for d in out['scope']['decisions']:
        if d.get('decision') == 'include' and d.get('id') not in left:
            d['decision'] = 'defer'
            d['evidence'] = (d.get('evidence') or '') + ' (removed on purpose by perturb_spec.py)'
    out['requirements'] = [r for r in out['requirements'] if r.get('component_id') not in gone]
    out['landmarks'] = [m for m in out['landmarks'] if m.get('component_id') not in gone]
    return out


def _replace(spec, new_comps):
    out = copy.deepcopy(spec)
    by_id = {c['id']: c for c in new_comps}
    out['components'] = [by_id.get(c['id'], c) for c in out['components']]
    return out


def _by_id(spec, ids):
    return [c for c in spec['components'] if c['id'] in set(ids)]


def _validation_errors(spec, source_root=None, source_size=None):
    result = validate_spec.validate(spec, source_root=source_root, source_size=source_size)
    return [f"{e.get('code')} {e.get('path')}" for e in result['errors']] if not result['valid'] else []


def _display_srgb(material):
    """The colour as it appears on screen (sRGB in [0, 1]), whatever colour space the material is written in."""
    rgb = [min(1.0, max(0.0, float(v))) for v in material['color'][:3]]
    return rgb if material.get('color_space', 'linear') == 'srgb' else [_to_srgb(v) for v in rgb]


def _same_colour_group(spec, items, mapping, comp_id):
    """Ids of the components that change with `comp_id`: those of its inventory part (the first item that holds it) whose colour and
    colour space equal its own. Alone when no item holds it."""
    by_id = {c['id']: c for c in spec['components']}
    own = by_id[comp_id]['material']
    for it in items:
        comps = metrics.matching_components(spec, it['id'], mapping.get(it['id']))
        if comp_id in {c['id'] for c in comps}:
            return [c['id'] for c in comps
                    if c['material'].get('color') == own.get('color') and c['material'].get('color_space') == own.get('color_space')]
    return [comp_id]


def _check_exclusions(spec, items, mapping, excluded):
    """{component id: excluded item id}; InputError when another inventory item shares a component with an excluded item."""
    owner = {}
    for it in items:
        if it['id'] in excluded:
            for c in metrics.matching_components(spec, it['id'], mapping.get(it['id'])):
                owner.setdefault(c['id'], it['id'])
    for it in items:
        if it['id'] in excluded:
            continue
        shared = [c['id'] for c in metrics.matching_components(spec, it['id'], mapping.get(it['id'])) if c['id'] in owner]
        if shared:
            raise InputError(f'SHARED_COMPONENT: inventory item {it["id"]!r} shares {", ".join(shared)} with excluded item '
                             f'{owner[shared[0]]!r}; exclude {it["id"]!r} too (with its own reason) or fix the map')
    return owner


def _swap_colours(mat, other):
    """Give `mat` the colour and the colour space of `other` (a colour_space that `other` lacks is removed, i.e. linear)."""
    for key in ('color', 'color_space'):
        v = other.get(key)
        if v is None:
            mat.pop(key, None)
        else:
            mat[key] = copy.deepcopy(v)


def _choose(parts, cover, excluded_ids=frozenset(), owner=None):
    """The parts and swatch components the six variants use -> dict; InputError when a pool is empty or a swatch resolves to a
    component of an excluded part (`owner` maps those components to their excluded item)."""
    owner = owner or {}
    note = ' (after excluding ' + ', '.join(sorted(excluded_ids)) + ')' if excluded_ids else ''
    parts = [p for p in parts if p['id'] not in excluded_ids]
    dom = cover['dominant'][0]
    sec = next((i for i in cover['secondary'] if i != dom), None)
    acc = cover['accent'][0]
    if sec is None:
        raise InputError(f'NO_COLOUR_PARTS: the dominant and the secondary swatch both show only {dom!r}; the colour swap needs two parts')
    for role, comp in (('accent', acc), ('dominant', dom), ('secondary', sec)):
        if comp in owner:
            raise InputError(f'EXCLUDED_SWATCH_PART: the {role} swatch resolves to component {comp!r}, which belongs to excluded item '
                             f'{owner[comp]!r}; B1 and the B6 colour swap would have to use another part, so this exclusion is refused '
                             f'(move the {role} swatch or do not exclude {owner[comp]!r})')

    def tier(p):                                   # 2 holds a swap part, 1 holds the accent part, 0 none of them
        have = set(p['comps'])
        return 2 if have & {dom, sec} else (1 if acc in have else 0)

    def order(group):
        return sorted(group, key=lambda p: (tier(p), p['index']))

    heroes = order([p for p in parts if p['hero']])
    others = order([p for p in parts if not p['hero']])
    cut_heroes = [p for p in heroes if tier(p) < 2]
    cut_others = [p for p in others if tier(p) < 2]
    lacking = []
    if not heroes:
        lacking.append('B3, B5 and B6 need at least one hero part (an inventory item with hero true that is in the spec, is not the '
                       'floor, and is in front of the camera), found none')
    elif not cut_heroes:
        lacking.append('B6 needs one hero part that does not hold the dominant or secondary swatch part, found none')
    if not others:
        lacking.append('B2 and B4 need at least one non-hero part, found none')
    if len(cut_others) < 2:
        lacking.append(f'B6 needs two non-hero parts that do not hold the dominant or secondary swatch part (the accent one may go), '
                       f'found {len(cut_others)} of {len(others)} non-hero parts')
    if lacking:
        raise InputError('TOO_FEW_PARTS: ' + '; '.join(lacking) + note + '; nothing was written')
    return {'hero': heroes[0], 'first': others[0], 'cut_hero': cut_heroes[0], 'cut_a': cut_others[0], 'cut_b': cut_others[1],
            'acc': acc, 'dom': dom, 'sec': sec}


def build_variants(spec, camera, width, height, items, swatches, mapping=None, source_root=None, source_size=None, excluded=None):
    """-> ({'B1': spec, ..}, report). Raises InputError when the inputs cannot give all six.

    `excluded` is {inventory id: reason}: those parts are never used (see the module doc)."""
    mapping = mapping or {}
    excluded = excluded or {}
    parts = _parts(spec, items, mapping, camera, width, height)
    cover = _swatch_components(spec, swatches, camera, width, height)
    gone_ids = frozenset(excluded)
    owner = _check_exclusions(spec, items, mapping, excluded)
    pick = _choose(parts, cover, gone_ids, owner)
    hero, first, cut_hero, cut_a, cut_b = pick['hero'], pick['first'], pick['cut_hero'], pick['cut_a'], pick['cut_b']
    dom, sec, acc = pick['dom'], pick['sec'], pick['acc']
    usual = None
    if excluded:
        try:
            usual = _choose(parts, cover)
        except InputError:
            usual = None
    variants, report = {}, {}

    new, de = _recolour_component(_by_id(spec, [acc])[0])
    group = _same_colour_group(spec, items, mapping, acc)
    recoloured = []
    for c in _by_id(spec, group):
        c = copy.deepcopy(c)
        c['material']['color'] = list(new['material']['color'])
        recoloured.append(c)
    variants['B1'] = _replace(spec, recoloured)
    report['B1'] = {'change': 'recolour the part under the accent swatch', 'part': None, 'components': group, 'swatch_component': acc,
                    'delta_e76': de, 'colour_before': _by_id(spec, [acc])[0]['material']['color'], 'colour_after': new['material']['color']}

    for name, part, px in (('B2', first, MOVE_SMALL_PX), ('B3', hero, MOVE_BIG_PX)):
        moved, info = move_part(_by_id(spec, part['comps']), camera, width, height, px)
        variants[name] = _replace(spec, moved)
        report[name] = dict({'change': f'move one {"hero" if part["hero"] else "non-hero"} part {px} px', 'part': part['id'],
                             'components': part['comps']}, **info)
        if abs(info['achieved_px'] - px) > 2:
            raise InputError(f'MOVE_FAILED: {name}: part {part["id"]!r} moved {info["achieved_px"]:.1f} px, not {px}')

    variants['B4'] = _remove(spec, first['comps'])
    report['B4'] = {'change': 'remove one non-hero part', 'part': first['id'], 'components': first['comps']}
    variants['B5'] = _remove(spec, hero['comps'])
    report['B5'] = {'change': 'remove one hero part', 'part': hero['id'], 'components': hero['comps']}

    gone = cut_hero['comps'] + cut_a['comps'] + cut_b['comps']
    b6 = _remove(spec, gone)
    comps = {c['id']: c for c in b6['components']}
    before = (_display_srgb(comps[dom]['material']), _display_srgb(comps[sec]['material']))
    group_dom = [i for i in _same_colour_group(spec, items, mapping, dom) if i in comps]
    group_sec = [i for i in _same_colour_group(spec, items, mapping, sec) if i in comps and i not in group_dom]
    material_dom, material_sec = copy.deepcopy(comps[dom]['material']), copy.deepcopy(comps[sec]['material'])
    for i in group_dom:
        _swap_colours(comps[i]['material'], {'color': material_sec.get('color'), 'color_space': material_sec.get('color_space')})
    for i in group_sec:
        _swap_colours(comps[i]['material'], {'color': material_dom.get('color'), 'color_space': material_dom.get('color_space')})
    variants['B6'] = b6
    report['B6'] = {'change': 'remove one hero and two non-hero parts, swap the dominant and secondary colours',
                    'part': [cut_hero['id'], cut_a['id'], cut_b['id']], 'components': gone, 'swapped': [dom, sec],
                    'swapped_components': group_dom + group_sec,
                    'swap_delta_e76': delta_e76(srgb_to_lab(before[0]), srgb_to_lab(before[1]))}

    if excluded:                                   # what each variant used, and what it would have used without the exclusions
        was = {'B2': {'part_without_exclusions': usual and usual['first']['id']},
               'B3': {'part_without_exclusions': usual and usual['hero']['id']},
               'B4': {'part_without_exclusions': usual and usual['first']['id']},
               'B5': {'part_without_exclusions': usual and usual['hero']['id']},
               'B6': {'part_without_exclusions': usual and [usual['cut_hero']['id'], usual['cut_a']['id'], usual['cut_b']['id']]}}
        for name in was:
            report[name].update(was[name])
    for name in VARIANTS:
        errors = _validation_errors(variants[name], source_root, source_size)
        if errors:
            raise InputError(f'VARIANT_INVALID: {name} does not validate with validate_spec: ' + '; '.join(errors[:5]))
    return variants, report


def _source_image(spec, spec_dir):
    """(relative path of the picture named by spec.source.image, or None when it is absolute, its pixel size)."""
    image = spec.get('source', {}).get('image') if isinstance(spec.get('source'), dict) else None
    if not isinstance(image, str) or not image:
        raise InputError('BASELINE_INVALID: the baseline spec has no source.image')
    rel = Path(image)
    if rel.is_absolute():
        path, rel = rel, None
    else:
        if '..' in rel.parts:
            raise InputError(f'BAD_SOURCE: source.image {image!r} leaves the spec folder; keep the picture beside the spec')
        path = Path(spec_dir) / rel
    try:
        from PIL import Image
        with Image.open(path) as im:
            size = list(im.size)
    except (OSError, ValueError) as e:
        raise InputError(f'SOURCE_MISSING: the picture {path} named by source.image cannot be read ({e}); a variant needs it to be previewed')
    return rel, size


def _read_fit(fit, where):
    """(camera, width, height) of a box_fit.json, or InputError BAD_FIT."""
    def bad(why):
        return InputError(f'BAD_FIT: {where}: {why}')
    if not (isinstance(fit, dict) and isinstance(fit.get('camera'), dict) and isinstance(fit.get('image'), dict)):
        raise bad('needs an image object (width, height) and a camera object')
    camera, image = fit['camera'], fit['image']
    width, height = image.get('width'), image.get('height')
    if not all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in (width, height)):
        raise bad('image.width and image.height must be positive whole numbers')
    for key in ('position', 'target'):
        v = camera.get(key)
        if not (isinstance(v, list) and len(v) == 3 and all(_is_number(x) for x in v)):
            raise bad(f'camera.{key} must be three numbers')
    for key in ('lens_mm', 'sensor_width_mm'):
        if not (_is_number(camera.get(key)) and camera[key] > 0):
            raise bad(f'camera.{key} must be a positive number')
    try:
        camera_math.project(camera['target'], camera, width, height)
    except (KeyError, TypeError, ValueError, IndexError, ZeroDivisionError) as e:
        raise bad(f'the camera cannot project its own target ({type(e).__name__}: {e})')
    return camera, width, height


def _parse_exclusions(raw):
    """['id=reason', ..] -> {id: reason}; InputError for a missing '=', an empty id or reason, or an id given twice."""
    out = {}
    for text in raw or []:
        ident, eq, reason = str(text).partition('=')
        ident, reason = ident.strip(), reason.strip()
        if not eq or not ident or not reason:
            raise InputError(f'BAD_EXCLUDE: {text!r}: write --exclude <inventory id>=<reason>, both parts non-empty')
        if ident in out:
            raise InputError(f'BAD_EXCLUDE: {ident!r} is excluded twice')
        out[ident] = reason
    return out


def perturb(spec_path, fit_path, truth, map_path, out, exclude=None):
    excluded = _parse_exclusions(exclude)
    spec = scoring.read_json(spec_path)
    fit = scoring.read_json(fit_path)
    if not isinstance(spec, dict):
        raise InputError(f'BASELINE_INVALID: {spec_path}: a spec object is required')
    camera, width, height = _read_fit(fit, fit_path)
    spec_dir = Path(spec_path).resolve().parent
    rel, size = _source_image(spec, spec_dir)
    errors = _validation_errors(spec, spec_dir, size)
    if errors:
        raise InputError('BASELINE_INVALID: the baseline spec does not validate with validate_spec --check-source: ' + '; '.join(errors[:5]))
    items, swatches = _read_truth(truth)
    unknown = sorted(set(excluded) - {i['id'] for i in items})
    if unknown:
        raise InputError(f'BAD_EXCLUDE: {", ".join(repr(u) for u in unknown)} is not an inventory id of the answer sheet')
    mapping = scoring.read_map(map_path, {i['id'] for i in items}) if map_path else {}
    _check_map(spec, mapping)
    # the picture is copied byte for byte under the same relative path, so validating against the baseline folder is the same check
    variants, report = build_variants(spec, camera, width, height, items, swatches, mapping, spec_dir, size, excluded)
    out = Path(out)
    for name in VARIANTS:
        scoring.write_json(out / name / 'spec.json', variants[name])
        if rel is not None:
            (out / name / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(spec_dir / rel, out / name / rel)
    scoring.write_json(out / 'expected_order.json', list(VARIANTS))
    doc = {'schema_version': 'perturb.v1', 'baseline_spec_sha256': hashlib.sha256(Path(spec_path).read_bytes()).hexdigest(),
           'image': {'width': width, 'height': height}, 'camera_matches_spec': spec.get('camera') == camera}
    if excluded:
        doc['exclusions'] = [{'id': k, 'reason': v} for k, v in excluded.items()]
    doc['variants'] = report
    scoring.write_json(out / 'perturb_report.json', doc)
    return {'out': str(out), 'variants': list(VARIANTS), 'expected_order': list(VARIANTS)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--spec', required=True, help='baseline spec.json (its source.image must be readable beside it)')
    ap.add_argument('--fit', required=True, help='box_fit.json (camera and image size)')
    ap.add_argument('--truth', required=True, help='answer-sheet folder (inventory.json and swatches.json are used)')
    ap.add_argument('--map', help='JSON {inventory_id: component id or group, or a list}; the same file score.py --map takes')
    ap.add_argument('--exclude', action='append', default=[], metavar='ID=REASON',
                    help='keep one whole inventory part out of every variant; the reason (required) cites the spec geometry. Repeatable. '
                         'Exit 2 if another item shares a component with it, or if it owns the component an accent, dominant or '
                         'secondary swatch resolves to (B1 and the B6 swap never change)')
    ap.add_argument('--out', required=True, help='folder for B1 .. B6, expected_order.json and perturb_report.json')
    args = ap.parse_args(argv)
    try:
        result = perturb(args.spec, args.fit, args.truth, args.map, args.out, args.exclude)
    except (InputError, OSError, ValueError, KeyError, TypeError, IndexError, ZeroDivisionError) as e:
        print('ERROR: ' + (str(e) if isinstance(e, InputError) else f'{type(e).__name__}: {e}'), file=sys.stderr)
        return 2
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
