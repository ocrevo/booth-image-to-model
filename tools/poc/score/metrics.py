"""Code metrics of the POC scoring card (docs/poc/03-scoring-spec.md section 3).

edge_distance  how far the render's edges are from the picture's edges inside the fitted booth region (3.1)
fit_region     that region: the corner fit's box projected, as a convex hull (3.1 rule 3)
holdout_errors where hand-read corner points land when the spec is projected with the fitted camera (3.2)
colour_de      mean Lab difference over a few colour swatches (3.3)
booth_mask     the booth area of a render, from its alpha channel

Requires numpy, scipy and cv2 (Canny and Lab). Images come in as uint8 arrays: the picture is HxWx3 RGB, the
render HxWx4 RGBA on the same canvas. Nothing is resized or registered to hide a mismatch.
"""
from __future__ import annotations
import itertools
import math
import sys
from pathlib import Path

import cv2
import numpy as np
from scipy.ndimage import distance_transform_edt

ENGINE = Path(__file__).resolve().parents[3] / 'booth-image-to-model' / 'scripts'
if str(ENGINE) not in sys.path:
    sys.path.append(str(ENGINE))
import camera_math  # noqa: E402  (engine pinhole camera, the same one the renderer uses)
import geometry  # noqa: E402

CANNY_LOW, CANNY_HIGH = 60, 150      # 03 section 3.1
GRADIENT_MIN = 28                    # inner edges of the render: luminance step above this, as in preview_compare.sheet()
BOOTH_DILATE_DIAG = 0.02             # the fitted booth region is grown by 2 % of the diagonal
ALPHA_MIN = 127                      # alpha above this belongs to the booth (as compare.load_mask)
MISSING_DIAG = 1.0                   # held-out point whose part is absent: a full diagonal, so it scores nothing and drags the median
SWATCH_OUTSIDE_DE = 40.0             # swatch not covered by the render counts as the worst case
SWATCH_MIN_COVER = 0.5

CORNER_WORDS = {'top', 'bottom', 'front', 'back', 'left', 'right'}   # read from the camera's side, see corner_point


def diagonal(width, height):
    return math.hypot(width, height)


def _check_pair(photo, render):
    photo, render = np.asarray(photo), np.asarray(render)
    if photo.ndim != 3 or photo.shape[2] != 3:
        raise ValueError('BAD_PHOTO: expected an HxWx3 RGB array')
    if render.ndim != 3 or render.shape[2] != 4:
        raise ValueError('BAD_RENDER: expected an HxWx4 RGBA array')
    if photo.shape[:2] != render.shape[:2]:
        raise ValueError(f'IMAGE_SHAPE_MISMATCH: render {render.shape[1]}x{render.shape[0]} vs picture '
                         f'{photo.shape[1]}x{photo.shape[0]}; previews use the source canvas and are never rescaled')
    return photo.astype(np.uint8), render.astype(np.uint8)


def booth_mask(render_rgba, dilate_diag=0.0):
    """Boolean mask of the render's booth area (alpha > 127), optionally grown by a share of the image diagonal."""
    alpha = np.asarray(render_rgba)[..., 3] > ALPHA_MIN
    if dilate_diag <= 0 or not alpha.any():
        return alpha
    h, w = alpha.shape
    r = max(1, int(math.ceil(dilate_diag * diagonal(w, h))))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    return cv2.dilate(alpha.astype(np.uint8), kernel).astype(bool)


def photo_edges(photo_rgb):
    """Canny edges of the picture: grey, 3x3 Gaussian, thresholds 60 and 150."""
    grey = cv2.cvtColor(np.asarray(photo_rgb, np.uint8), cv2.COLOR_RGB2GRAY)
    return cv2.Canny(cv2.GaussianBlur(grey, (3, 3), 0), CANNY_LOW, CANNY_HIGH) > 0


def render_edges(render_rgba):
    """Outline of the alpha mask plus inner luminance edges, drawn the way preview_compare.sheet() does it."""
    rgba = np.asarray(render_rgba, np.uint8)
    alpha = rgba[..., 3] > ALPHA_MIN
    padded = np.pad(alpha, 1, mode='edge')
    outline = (alpha ^ padded[1:-1, :-2]) | (alpha ^ padded[:-2, 1:-1])
    a = rgba[..., 3:4].astype(float) / 255.0
    flat = np.rint(rgba[..., :3] * a + 128.0 * (1.0 - a)).astype(np.uint8)       # composited on mid grey
    lum = cv2.cvtColor(flat, cv2.COLOR_RGB2GRAY).astype(float)
    grad = np.zeros_like(lum)
    grad[:, 1:] = np.abs(np.diff(lum, axis=1))
    grad[1:, :] = np.maximum(grad[1:, :], np.abs(np.diff(lum, axis=0)))
    return outline | ((grad > GRADIENT_MIN) & alpha)


SUBPIXEL_BITS = 4
NEAR_M = 0.05                        # a box that reaches behind the camera is cut at this depth in front of it (03 section 3.1 rule 3)


def _bad_fit(why):
    return ValueError(f'BAD_FIT: {why}')


def _finite(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _convex_hull(points):
    """Vertices of the convex hull of float64 points (Andrew's monotone chain), counter-clockwise on a y-up plane; a point or a
    segment gives one or two vertices."""
    pts = sorted({(float(x), float(y)) for x, y in points})
    if len(pts) <= 2:
        return pts

    def half(seq):
        chain = []
        for q in seq:
            while len(chain) >= 2 and ((chain[-1][0] - chain[-2][0]) * (q[1] - chain[-2][1])
                                       - (chain[-1][1] - chain[-2][1]) * (q[0] - chain[-2][0])) <= 0:
                chain.pop()
            chain.append(q)
        return chain[:-1]
    return half(pts) + half(pts[::-1]) or pts[:1]


def _clip_to_rect(poly, x0, y0, x1, y1):
    """A convex polygon (list of points) clipped to the rectangle, Sutherland-Hodgman; the hull itself is cut, not its coordinates."""
    def clip(points, inside, cross):
        out = []
        for k, a in enumerate(points):
            b = points[(k + 1) % len(points)]
            if inside(a):
                out.append(a)
            if inside(a) != inside(b):
                out.append(cross(a, b))
        return out
    sides = [(lambda q: q[0] >= x0, lambda a, b: (x0, a[1] + (b[1] - a[1]) * (x0 - a[0]) / (b[0] - a[0]))),
             (lambda q: q[0] <= x1, lambda a, b: (x1, a[1] + (b[1] - a[1]) * (x1 - a[0]) / (b[0] - a[0]))),
             (lambda q: q[1] >= y0, lambda a, b: (a[0] + (b[0] - a[0]) * (y0 - a[1]) / (b[1] - a[1]), y0)),
             (lambda q: q[1] <= y1, lambda a, b: (a[0] + (b[0] - a[0]) * (y1 - a[1]) / (b[1] - a[1]), y1))]
    for inside, cross in sides:
        if not poly:
            break
        poly = clip(poly, inside, cross)
    return poly


def _front_points(corner_loop, camera):
    """The points of a closed loop of 3D corners that lie in front of the camera's near plane (depth along the view axis >= NEAR_M):
    the corners in front, and where an edge between a corner in front and one behind crosses the plane. Empty when none is in front."""
    fwd, _, _ = camera_math.axes(camera)
    eye = camera['position']
    depth = [sum((c[i] - eye[i]) * fwd[i] for i in range(3)) for c in corner_loop]
    out = []
    for k, (a, da) in enumerate(zip(corner_loop, depth)):
        b, db = corner_loop[(k + 1) % len(corner_loop)], depth[(k + 1) % len(corner_loop)]
        if da >= NEAR_M:
            out.append(a)
        if (da >= NEAR_M) != (db >= NEAR_M):
            t = (NEAR_M - da) / (db - da)
            out.append([a[i] + t * (b[i] - a[i]) for i in range(3)])
    return out


def fit_region(fit, width, height, dilate_diag=BOOTH_DILATE_DIAG):
    """The booth region M of a picture, from its corner fit and not from any candidate (03 section 3.1, rule 3).

    `fit` is a box_fit.json: dims_m {W, D, H}, dim_source, floor_z, camera. The footprint W x D, centred on the origin, rises from
    floor_z to H (box_fit's H is the z of the top). The eight corners are projected with the fitted camera (camera_math.project)
    and their convex hull is filled, then grown by `dilate_diag` of the image diagonal (an ellipse of that radius, at least 1 px).
    H is known when dim_source says fitted or given. When it is assumed, or H is missing or null (or there is no dim_source), the top is
    not known: the top face, at H when one is given and at floor_z when not, is extended straight up the picture to row 0 before the
    hull is taken. A box that reaches behind the camera plane is cut at a near plane NEAR_M in front of the camera and only the part
    in front is projected (the extension too).
    Returns a bool array (height, width). Raises ValueError BAD_FIT for a fit that cannot give a region: no W or D above zero, a
    height at or below the floor, no usable perspective camera, nothing of the box in front of the camera, or a picture size of
    zero. A hull that collapses to a point or a line still gives a thin region.
    """
    if not isinstance(fit, dict):
        raise _bad_fit('the fit is not an object')
    if not (isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0):
        raise _bad_fit(f'the picture size {width}x{height} must be whole pixels above zero')
    dims = fit.get('dims_m')
    if not isinstance(dims, dict):
        raise _bad_fit('no dims_m')
    w_m, d_m = dims.get('W'), dims.get('D')
    if not (_finite(w_m) and _finite(d_m) and w_m > 0 and d_m > 0):
        raise _bad_fit('dims_m needs W and D above zero')
    floor_z = fit.get('floor_z', 0.0)
    if not _finite(floor_z):
        raise _bad_fit('floor_z must be a number')
    top = dims.get('H')
    if top is not None and not _finite(top):
        raise _bad_fit('dims_m.H must be a number or null')
    sources = fit.get('dim_source')
    h_known = top is not None and isinstance(sources, dict) and sources.get('H') in ('fitted', 'given')
    if top is not None and top <= floor_z:
        raise _bad_fit(f'the top (H = {top}) is not above the floor (floor_z = {floor_z})')
    camera = fit.get('camera')
    if not isinstance(camera, dict):
        raise _bad_fit('no camera')
    top_z = top if top is not None else floor_z                  # a missing H: only the footprint is known
    hw, hd = w_m / 2.0, d_m / 2.0
    loop = [(-hw, -hd), (hw, -hd), (hw, hd), (-hw, hd)]           # the footprint corners in order round the box
    points = []
    try:
        for z in ([floor_z, top] if top is not None else [floor_z]):
            points += [camera_math.project(c, camera, width, height)[:2] for c in _front_points([[x, y, z] for x, y in loop], camera)]
        if len(points) == 0:
            raise _bad_fit('nothing of the box is in front of the camera')
        if top is not None:                                       # the walls: the loops at floor and top are joined by vertical edges
            for x, y in loop:
                points += [camera_math.project(c, camera, width, height)[:2]
                           for c in _front_points([[x, y, floor_z], [x, y, top]], camera) if c[2] not in (floor_z, top)]
        if not h_known:
            points += [(camera_math.project(c, camera, width, height)[0], 0.0)
                       for c in _front_points([[x, y, top_z] for x, y in loop], camera)]
    except (ValueError, KeyError, TypeError, IndexError, ZeroDivisionError, OverflowError) as e:
        if str(e).startswith('BAD_FIT'):
            raise
        raise _bad_fit(f'the box cannot be projected with the fitted camera ({e})')
    pts = np.asarray(points, np.float64)
    if not np.isfinite(pts).all():
        raise _bad_fit('a corner projects to a non-finite pixel')
    r = max(1, int(math.ceil(dilate_diag * diagonal(width, height)))) if dilate_diag > 0 else 0
    margin = r + 2                                                # the part of the hull that can still grow into the picture
    hull = _clip_to_rect(_convex_hull(pts), -margin, -margin, width - 1 + margin, height - 1 + margin)
    mask = np.zeros((height + 2 * margin, width + 2 * margin), np.uint8)
    if hull:
        fixed = np.rint((np.asarray(hull) + margin) * (1 << SUBPIXEL_BITS)).astype(np.int32).reshape(-1, 1, 2)
        cv2.fillConvexPoly(mask, fixed, 1, lineType=cv2.LINE_8, shift=SUBPIXEL_BITS)
        cv2.polylines(mask, [fixed], True, 1, lineType=cv2.LINE_8, shift=SUBPIXEL_BITS)   # a point or a line has no interior to fill
    if r > 0:
        mask = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
    return mask[margin:margin + height, margin:margin + width].astype(bool)


def edge_distance(photo_rgb, render_rgba, region):
    """Symmetric mean distance between the picture's and the render's edges inside `region`, the fixed booth region of the picture
    (fit_region). The region does not depend on the render: a part deleted from the render cannot take its edges out of the comparison.

    Returns edge_distance_px, edge_distance_diag (share of the image diagonal), booth_pixel_fraction (render alpha share of the
    canvas), the edge pixel counts, region_px (pixels in the region) and render_outside_region (share of the render's alpha pixels
    that lie outside the region).
    Raises ValueError IMAGE_SHAPE_MISMATCH for different canvases, REGION_SHAPE_MISMATCH for a region of another size,
    REGION_EMPTY for a region without pixels and RENDER_EMPTY for a fully transparent render.
    """
    photo, render = _check_pair(photo_rgb, render_rgba)
    h, w = photo.shape[:2]
    diag = diagonal(w, h)
    region = np.asarray(region)
    if region.shape != (h, w):
        raise ValueError(f'REGION_SHAPE_MISMATCH: region {"x".join(str(n) for n in reversed(region.shape))} vs picture {w}x{h}')
    region = region.astype(bool)
    if not region.any():
        raise ValueError('REGION_EMPTY: the fitted box lies outside the picture, so there is nothing to compare')
    alpha = booth_mask(render)
    if not alpha.any():
        raise ValueError('RENDER_EMPTY: the render has no booth pixels (alpha is fully transparent)')
    e_photo, e_render = photo_edges(photo) & region, render_edges(render) & region
    n_photo, n_render = int(e_photo.sum()), int(e_render.sum())
    if n_photo == 0 and n_render == 0:
        px = 0.0
    elif n_photo == 0 or n_render == 0:
        px = diag                                       # one side has no edges at all: worst case, as compare_masks does
    else:
        d_photo = distance_transform_edt(~e_photo)      # distance to the nearest picture edge
        d_render = distance_transform_edt(~e_render)    # distance to the nearest render edge
        px = float((d_photo[e_render].mean() + d_render[e_photo].mean()) / 2.0)
    return {'edge_distance_px': px, 'edge_distance_diag': px / diag, 'booth_pixel_fraction': float(alpha.mean()),
            'photo_edge_pixels': n_photo, 'render_edge_pixels': n_render, 'region_px': int(region.sum()),
            'render_outside_region': float((alpha & ~region).sum() / alpha.sum())}


# ---- held-out points ----------------------------------------------------------------------------------------------

def component_points(component):
    """World vertices of one component's real mesh; a component without a mesh (text) gives its location."""
    loc, rot = component.get('location', [0, 0, 0]), component.get('rotation_deg', [0, 0, 0])
    try:
        verts, _ = geometry.mesh_for(component)
        return [geometry.transform_point(v, loc, rot) for v in verts]
    except (ValueError, KeyError, TypeError):
        return [[float(x) for x in loc]]


def points_bounds(pts):
    return [min(p[i] for p in pts) for i in range(3)], [max(p[i] for p in pts) for i in range(3)]


def matching_components(spec, part_id, names=None):
    """Components of the spec that make up one part of the answer sheet, by exact names only.

    With `names` (a --map entry: component ids or group names) the components whose id or group is one of the names. Without,
    the components whose id or group equals part_id. There is no guessing by name prefix.
    """
    names = {part_id} if names is None else set(names)
    return [c for c in spec.get('components', []) if isinstance(c, dict) and (c.get('id') in names or c.get('group') in names)]


def part_bounds(spec, part_id, names=None):
    """World bounding box ((x0, y0, z0), (x1, y1, z1)) of the matching components, or None when none match."""
    pts = []
    for c in matching_components(spec, part_id, names):
        pts.extend(component_points(c))
    return points_bounds(pts) if pts else None


HORIZONTAL_WORDS = ('left', 'right', 'front', 'back')
THREE_WORD_NAMES = ['_'.join(w) for w in itertools.product(('top', 'bottom'), ('front', 'back'), ('left', 'right'))]
ONE_HORIZONTAL_NAMES = ['_'.join(w) for w in itertools.product(('top', 'bottom'), HORIZONTAL_WORDS)]
MARGIN_YAW_DEG = 5.0                 # a name keeps its corner when the camera orbits this much about the vertical axis ...
MARGIN_PITCH_DEG = 2.0               # ... or changes its elevation by this much (03 section 2 and section 6 step 3)
CORNER_NAME_HELP = ('a corner name is top or bottom plus one or two horizontal words, joined by underscores, in lower case: '
                    'three-word names (one of top/bottom, one of front/back, one of left/right, e.g. top_front_left) or '
                    'one horizontal word names (top or bottom with one of left/right/front/back, e.g. top_left); '
                    'no bare words and no other words')


def corner_words(corner):
    """The words of a corner name, or None when it is not a valid name.

    Valid: exactly one of top/bottom plus either one horizontal word (left, right, front or back) or one of front/back and one of
    left/right, lower case, joined by underscores, in any order. Bare words ('left', 'top') and anything else are invalid."""
    if not isinstance(corner, str) or not corner:
        return None
    words = corner.split('_')
    if not set(words) <= CORNER_WORDS or len(set(words)) != len(words):
        return None
    for a, b in (('top', 'bottom'), ('front', 'back'), ('left', 'right')):
        if a in words and b in words:
            return None
    if len([w for w in words if w in ('top', 'bottom')]) != 1 or not 1 <= len([w for w in words if w in HORIZONTAL_WORDS]) <= 2:
        return None
    return set(words)


def canonical_corner(corner):
    """The canonical spelling of a valid corner name: top or bottom, then front or back, then left or right (top_front_left;
    one horizontal word gives top_left, bottom_front, ...). None for a name corner_words rejects."""
    words = corner_words(corner)
    if words is None:
        return None
    return '_'.join(w for w in ('top', 'bottom', 'front', 'back', 'left', 'right') if w in words)


def box_corners(bounds):
    """The eight corners of a bounding box, as world points."""
    lo, hi = bounds
    return [[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]


def _horizontal_unit(v):
    """The vector with its vertical part removed, scaled to length 1."""
    h = math.hypot(v[0], v[1])
    if h < 1e-9:
        raise ValueError('the camera axis has no horizontal part')
    return [v[0] / h, v[1] / h]


def _footprint(bounds, z):
    lo, hi = bounds
    return [[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1])]


def _three_word_corner(bounds, corner_words_3, camera):
    """The box corner named by one word from each of top/bottom, front/back and left/right, read from the camera's side.

    z comes from top/bottom alone (the world's up and down; the camera's pitch and roll never enter). The footprint corner is
    the one of the four with the largest a*(r.d) + b*(f.d): d is the corner's horizontal position from the footprint centre, r and
    f are the camera's right vector (with its roll) and viewing direction with their vertical part removed and scaled to length 1,
    a is -1 for left and 1 for right, b is -1 for front (nearer the camera) and 1 for back, both with the same weight. An exact
    tie goes to the corner horizontally nearer the camera. It suits a part that faces the camera roughly squarely.
    """
    lo, hi = bounds
    fwd, right, _ = camera_math.axes(camera)
    r, f = _horizontal_unit(right), _horizontal_unit(fwd)
    a = 1.0 if 'right' in corner_words_3 else -1.0
    b = 1.0 if 'back' in corner_words_3 else -1.0
    cx, cy = (lo[0] + hi[0]) / 2.0, (lo[1] + hi[1]) / 2.0
    cam = camera['position']
    best, best_key = None, None
    for x in (lo[0], hi[0]):
        for y in (lo[1], hi[1]):
            d = (x - cx, y - cy)
            score = a * (r[0] * d[0] + r[1] * d[1]) + b * (f[0] * d[0] + f[1] * d[1])
            near = math.hypot(x - cam[0], y - cam[1])
            if best is None or score > best_key[0] + 1e-9 or (abs(score - best_key[0]) <= 1e-9 and near < best_key[1]):
                best, best_key = [x, y, hi[2] if 'top' in corner_words_3 else lo[2]], (score, near)
    return best


class CornerBehindCamera(ValueError):
    """A one-horizontal-word left/right name cannot be placed: fewer than two footprint corners are in front of the camera."""


def _one_horizontal_corner(bounds, words, camera, width, height):
    """The footprint corner named by top/bottom and one horizontal word, for a part seen from a corner of the booth.

    left/right: the corner whose projection into the picture is left-most/right-most (projected u, camera_math.project; the
    order does not depend on the picture size). Only corners in front of the camera take part; with fewer than two of them the
    name cannot be placed and CornerBehindCamera is raised. front/back: the corner horizontally nearest to/furthest from the
    camera. A tie goes to the corner horizontally nearer the camera for left/right and to the first corner in x, y order for
    front/back.
    """
    z = bounds[1][2] if 'top' in words else bounds[0][2]
    cam = camera['position']
    horizontal = 'left' in words or 'right' in words
    if horizontal:
        camera_math.axes(camera)                                  # a camera that looks straight up or down, or an orthographic one,
        camera_math.focal_px(camera, width)                       # raises here, so that only "behind the camera" is caught below
    best, best_key, in_front = None, None, 0
    for c in _footprint(bounds, z):
        dist = math.hypot(c[0] - cam[0], c[1] - cam[1])
        if horizontal:
            try:
                u = camera_math.project(c, camera, width, height)[0]
            except ValueError:
                continue                                          # behind the camera: no picture position to compare
            in_front += 1
            key = (u if 'left' in words else -u, dist)
        else:
            key = (dist if 'front' in words else -dist, 0.0)
        if best is None or key[0] < best_key[0] - 1e-9 or (abs(key[0] - best_key[0]) <= 1e-9 and key[1] < best_key[1]):
            best, best_key = c, key
    if horizontal and in_front < 2:
        raise CornerBehindCamera(f'only {in_front} footprint corner(s) are in front of the camera; left and right need at least two')
    return best


def corner_point(bounds, corner, camera, width=1000.0, height=1000.0):
    """World point for a corner name, read from the camera's side (the answer sheet is written by a person looking at the picture).

    Two families (03 section 2, ruling of 2026-10-23): a three-word name (top_front_left) weighs left/right and front/back equally
    on the floor and suits a part facing the camera (see _three_word_corner); a name with one horizontal word (top_left,
    bottom_front) takes the footprint corner that is left-most or right-most in the picture, or nearest or furthest from the
    camera (see _one_horizontal_corner) and suits a part seen from a corner. top/bottom are always the world's up and down.
    width and height only serve camera_math.project, whose left-right order does not depend on them. left/right compare only
    the footprint corners in front of the camera.

    Returns None for a name that corner_words rejects. Raises CornerBehindCamera (a ValueError) when fewer than two footprint
    corners are in front of the camera for a left/right name, and ValueError for a camera that looks straight up or down or is
    not a perspective camera.
    """
    words = corner_words(corner)
    if words is None:
        return None
    if len([w for w in words if w in HORIZONTAL_WORDS]) == 2:
        return _three_word_corner(bounds, words, camera)
    return _one_horizontal_corner(bounds, words, camera, width, height)


def corner_name(corner_world, bounds, camera):
    """The one three-word name whose corner_point is this box corner.

    Returns None when no name reaches the point (it is not a corner, or a box seen nearly end-on leaves it out) or when more than
    one name reaches it (a box with no height); callers skip such a corner. Raises ValueError for a camera looking straight up or down.
    """
    hits = [n for n in THREE_WORD_NAMES if math.dist(corner_point(bounds, n, camera), corner_world) < 1e-7]
    return hits[0] if len(hits) == 1 else None


def names_reaching(corner_world, bounds, camera, width=1000.0, height=1000.0):
    """The names of both families whose corner_point is this box corner: {'three_word': [...], 'one_horizontal': [...]}."""
    out = {'three_word': [], 'one_horizontal': []}
    for key, names in (('three_word', THREE_WORD_NAMES), ('one_horizontal', ONE_HORIZONTAL_NAMES)):
        for n in names:
            try:
                if math.dist(corner_point(bounds, n, camera, width, height), corner_world) < 1e-7:
                    out[key].append(n)
            except CornerBehindCamera:
                pass                                              # a name that cannot be placed reaches no corner
    return out


def turned_cameras(camera, yaw_deg=MARGIN_YAW_DEG, pitch_deg=MARGIN_PITCH_DEG):
    """The four margin cameras: the camera orbited about the vertical axis through its target by +-yaw degrees, and the camera
    with its elevation above the target changed by +-pitch degrees (distance to the target kept). Yaw and elevation are varied
    separately, so there are four cameras, not eight; target and roll stay as they are."""
    t = camera['target']
    v = [camera['position'][i] - t[i] for i in range(3)]
    length = math.sqrt(sum(c * c for c in v))
    elev, azim = math.atan2(v[2], math.hypot(v[0], v[1])), math.atan2(v[1], v[0])
    out = []
    for dyaw, dpitch in ((yaw_deg, 0.0), (-yaw_deg, 0.0), (0.0, pitch_deg), (0.0, -pitch_deg)):
        e, a = elev + math.radians(dpitch), azim + math.radians(dyaw)
        out.append(dict(camera, position=[t[0] + length * math.cos(e) * math.cos(a), t[1] + length * math.cos(e) * math.sin(a),
                                          t[2] + length * math.sin(e)]))
    return out


def name_margin(name, bounds, camera, width=1000.0, height=1000.0):
    """True when the name still picks the same corner for each of the four turned_cameras: the camera orbited +-5 degrees about
    the vertical axis through its target, and its elevation changed by +-2 degrees (varied separately). False for an invalid
    name or when a turned camera cannot place the corner."""
    try:
        base = corner_point(bounds, name, camera, width, height)
        if base is None:
            return False
        return all(math.dist(corner_point(bounds, name, c, width, height), base) < 1e-7 for c in turned_cameras(camera))
    except ValueError:
        return False


def holdout_errors(spec, points, width, height, mapping=None):
    """Projection error of each held-out point, in pixels and as a share of the diagonal.

    `mapping` is {part_id: component id or group name, or a list of them}. A point is never skipped when it cannot be placed: it
    is reported with missing=True, a missing_reason (part_missing, mapped_name_absent, unknown_corner, corner_behind_camera, camera_unusable when the
    camera looks straight up or down, or behind_camera) and error_diag = MISSING_DIAG, which scores nothing. Every row lists the component ids it matched.
    """
    diag = diagonal(width, height)
    mapping = mapping or {}
    rows = []
    for p in points:
        row = {'id': p['id'], 'part_id': p['part_id'], 'corner': p['corner'], 'target_px': [float(v) for v in p['uv_px']],
               'matched_ids': [], 'projected_px': None, 'error_px': None, 'error_diag': MISSING_DIAG, 'missing': True,
               'missing_reason': None}
        names = mapping.get(p['part_id'])
        if isinstance(names, str):
            names = [names]
        comps = matching_components(spec, p['part_id'], names)
        row['matched_ids'] = [c.get('id') for c in comps]
        if not comps:
            row['missing_reason'] = 'mapped_name_absent' if names is not None else 'part_missing'
        else:
            pts = []
            for c in comps:
                pts.extend(component_points(c))
            try:
                world = corner_point(points_bounds(pts), p['corner'], spec['camera'], width, height)
            except CornerBehindCamera:
                world, row['missing_reason'] = None, 'corner_behind_camera'
            except ValueError:
                world, row['missing_reason'] = None, 'camera_unusable'
            else:
                if world is None:
                    row['missing_reason'] = 'unknown_corner'
            if world is not None:
                try:
                    u, v, _ = camera_math.project(world, spec['camera'], width, height)
                except ValueError:
                    row['missing_reason'] = 'behind_camera'
                else:
                    err = math.hypot(u - p['uv_px'][0], v - p['uv_px'][1])
                    row.update(projected_px=[u, v], error_px=err, error_diag=err / diag, missing=False)
        rows.append(row)
    return rows


def holdout_median(rows):
    return float(np.median([r['error_diag'] for r in rows])) if rows else None


def holdout_points(median_diag):
    """L2 code part (max 10): median <= 1 % of the diagonal 10, <= 2 % 7, <= 4 % 3, else 0."""
    if median_diag is None:
        return 0.0
    for limit, pts in ((0.01, 10.0), (0.02, 7.0), (0.04, 3.0)):
        if median_diag <= limit + 1e-12:
            return pts
    return 0.0


# ---- colour -------------------------------------------------------------------------------------------------------

def _lab(rgb_pixels):
    """Mean CIELAB (D65, sRGB input) of an Nx3 uint8 array."""
    arr = (np.asarray(rgb_pixels, np.float32) / 255.0).reshape(-1, 1, 3)
    return cv2.cvtColor(arr, cv2.COLOR_RGB2Lab).reshape(-1, 3).mean(axis=0)


def colour_points(de_mean):
    """L4 code part (max 10): delta E <= 10 gives 10, >= 40 gives 0, linear in between."""
    return float(min(10.0, max(0.0, 10.0 * (40.0 - de_mean) / 30.0)))


def colour_de(photo_rgb, render_rgba, swatches):
    """Delta E 76 between the picture and the neutral render inside each swatch box; mean and points.

    Only render pixels that belong to the booth (alpha > 127) are averaged; a swatch less than half covered by the booth
    counts as 40 (no points).
    """
    photo, render = _check_pair(photo_rgb, render_rgba)
    h, w = photo.shape[:2]
    rows = []
    for s in swatches:
        x0, y0, x1, y1 = (int(round(v)) for v in s['bbox_px'])
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
        if x1 <= x0 or y1 <= y0:
            raise ValueError(f"BAD_SWATCH: {s.get('id')} lies outside the picture")
        inside = render[y0:y1, x0:x1, 3] > ALPHA_MIN
        cover = float(inside.mean())
        lab_photo = _lab(photo[y0:y1, x0:x1])
        if cover < SWATCH_MIN_COVER:
            rows.append({'id': s['id'], 'role': s.get('role'), 'de': SWATCH_OUTSIDE_DE, 'coverage': cover, 'inside_render': False,
                         'photo_lab': lab_photo.tolist(), 'render_lab': None})
            continue
        lab_render = _lab(render[y0:y1, x0:x1, :3][inside])
        de = float(np.linalg.norm(lab_photo - lab_render))
        rows.append({'id': s['id'], 'role': s.get('role'), 'de': de, 'coverage': cover, 'inside_render': True,
                     'photo_lab': lab_photo.tolist(), 'render_lab': lab_render.tolist()})
    mean = float(np.mean([r['de'] for r in rows])) if rows else SWATCH_OUTSIDE_DE
    return {'colour_de_mean': mean, 'points': colour_points(mean), 'swatches': rows}
