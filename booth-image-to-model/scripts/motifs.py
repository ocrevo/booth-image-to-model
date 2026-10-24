"""Design elements for the design route: one builder per element kind, composed from the existing primitives and macros.

Each builder gets a placed element (metres, already resolved by design_compile) and adds its parts through the
SpecBuilder. All parts of one element carry the element id as their editing group, so an editor moves it as one unit.
Defaults are generic priors (components.PRIOR, schemas/design-priors.v1.json); the compiler records every default it uses.
Convention: metres, footprint centre at the origin, X right, Y back, Z up; yaw 0 faces -Y, positive turns anticlockwise.
"""
from __future__ import annotations
import math
import re
from dataclasses import dataclass
from typing import Any, Callable

import components as cm

WALL_KINDS = ('feature_wall', 'graphic_panel', 'tv', 'led_wall', 'wall')        # must hang on or be a closed side
OVERHEAD_KINDS = ('header_band', 'ring', 'spotlights', 'canopy_frame')
FLOOR_KINDS = ('counter', 'round_table', 'chair', 'seating_group', 'planter', 'display_plinth', 'storage_block',
               'partition', 'lit_panel', 'prop_block', 'box')
KINDS = WALL_KINDS + OVERHEAD_KINDS + FLOOR_KINDS
SELF_PLACING_KINDS = frozenset({'lit_panel', 'prop_block'})     # design_compile.place_floor does not clamp or record these: the builder does, once

# default size [along the side, away from the side, height] and where the default comes from
DEFAULT_SIZE = {
    'feature_wall': ([None, 0.08, None], 'choice'),        # None = full wall length / wall height
    'graphic_panel': ([1.2, 0.04, 0.8], 'choice'),
    'led_wall': ([3.0, 0.1, 2.0], 'choice'),
    'counter': ([1.8, cm.PRIOR['counter_depth'], cm.PRIOR['counter_height']], 'prior'),
    'display_plinth': ([0.5, 0.5, 1.0], 'prior'),
    'storage_block': ([1.5, 1.2, None], 'choice'),         # None = wall height
    'partition': ([2.0, cm.PRIOR['wall_thickness'], None], 'choice'),
    'lit_panel': ([2.4, 0.15, None], 'choice'),            # None = wall height
    'prop_block': ([0.4, 0.4, 0.4], 'prior'),              # a stand-in for an object that is not modelled: recorded as assumed
    'box': ([0.6, 0.6, 0.6], 'choice'),
    'planter': ([0.4, 0.4, 0.45], 'prior'),
}
FINISH = {   # roughness, metallic, alpha
    'matte': (0.8, 0.0, 1.0), 'satin': (0.5, 0.0, 1.0), 'gloss': (0.2, 0.0, 1.0), 'metal': (0.3, 0.85, 1.0),
    'glass': (0.08, 0.0, 0.35), 'wood': (0.6, 0.0, 1.0), 'fabric': (0.95, 0.0, 1.0),
}
FACE = {'back': '-y', 'front': '+y', 'left': '+x', 'right': '-x'}        # which way a wall-mounted part looks (into the booth)
YAW_IN = {'back': 0.0, 'front': 180.0, 'left': 90.0, 'right': -90.0}     # yaw of something facing into the booth from that side
YAW_OUT = {'back': 180.0, 'front': 0.0, 'left': -90.0, 'right': 90.0}    # yaw of something facing out of the booth through that side


def yaw_vector(yaw_deg):
    """Facing direction in plan for a yaw (0 faces -Y)."""
    r = math.radians(yaw_deg)
    return math.sin(r), -math.cos(r)


class Placed:
    """An element after placement: plan rectangle, height range, facing and resolved look."""

    def __init__(self, el, x, y, w_x, d_y, z0, z1, yaw, mat, side, wall_face=None, axis=None, sign=None):
        self.el, self.id = el, el['id']
        self.x, self.y, self.wx, self.dy, self.z0, self.z1 = x, y, w_x, d_y, z0, z1
        self.yaw, self.mat, self.side = yaw, mat, side
        self.wall_face, self.axis, self.sign = wall_face, axis, sign       # wall-mounted parts: plane of the wall face
        self.frame = None                                                  # the read only Frame (assume, adjust, warn); set by design_compile
        self.span = None       # self-placing parts against a side: (x0, x1, y0, y1), the room inside the inner faces of the closed walls; else None

    @property
    def xr(self):
        return self.x - self.wx / 2, self.x + self.wx / 2

    @property
    def yr(self):
        return self.y - self.dy / 2, self.y + self.dy / 2


def _mat(m):
    return {'roughness': m['roughness'], 'metallic': m['metallic'], 'emission': m['emission']}


def add_text(b, cid, scope, body, size, centre, yaw, color, note):
    """Upright text facing `yaw` (Blender text lies in its local XY plane, so it is stood up by 90 degrees about X)."""
    b.add(cid, scope, 'text', 'sign_text', centre, {'body': body, 'size': round(size, 4), 'extrude': 0.005}, color,
          rot=(90, 0, yaw), inferred=True, note=note, roughness=0.4)


def text_on_face(b, p, scope, centre_face, yaw, size_default, note):
    """Text from the brief on the face of a carrier. centre_face = point on the face; the text stands 1 cm in front of it."""
    t = p.el.get('text') or {}
    body = (t.get('body') or '').strip()
    if not body:
        return
    fx, fy = yaw_vector(yaw)
    size = t.get('size_m') or size_default
    x, y, z = centre_face
    add_text(b, f'{p.id}_text', scope, body, size, [x + fx * 0.012, y + fy * 0.012, z], yaw, p.mat['text_color'], note)


# ------------------------------------------------------------------ wall-mounted
def _wall_box(b, cid, scope, role, p, n0, n1, a0, a1, z0, z1, color, **kw):
    """Box on a wall face: (n0, n1) across the wall normal, (a0, a1) along the wall."""
    if p.axis == 'y':
        return b.box(cid, scope, role, (a0, a1), tuple(sorted((n0, n1))), (z0, z1), color, **kw)
    return b.box(cid, scope, role, tuple(sorted((n0, n1))), (a0, a1), (z0, z1), color, **kw)


def _along(p):
    return (p.xr if p.axis == 'y' else p.yr)


def feature_wall(b, p, scope, note):
    a0, a1 = _along(p)
    face, s = p.wall_face, p.sign
    variant = (p.el.get('params') or {}).get('variant') or 'slats'
    depth = p.wx if p.axis == 'x' else p.dy
    if variant == 'panel':
        _wall_box(b, f'{p.id}_panel', scope, 'feature_wall', p, face, face + s * depth, a0, a1, p.z0, p.z1, p.mat['color'],
                  inferred=True, note=note, **_mat(p.mat))
        text_on_face(b, p, scope, _face_centre(p, face + s * depth), _in_yaw(p), 0.3, note)
        return
    back = 0.03
    _wall_box(b, f'{p.id}_backing', scope, 'feature_wall', p, face, face + s * back, a0, a1, p.z0, p.z1, p.mat['backing_color'],
              inferred=True, note=note, roughness=0.7)
    params = p.el.get('params') or {}
    slat_w = params.get('slat_width_m') or 0.04
    pitch = max(params.get('pitch_m') or 0.10, slat_w + 0.01)
    count = max(1, int((a1 - a0 - slat_w) / pitch) + 1)
    first = a0 + ((a1 - a0) - (count - 1) * pitch) / 2
    for i in range(count):
        c = first + i * pitch
        _wall_box(b, f'{p.id}_slat_{i}', scope, 'slat', p, face + s * back, face + s * depth, c - slat_w / 2, c + slat_w / 2,
                  p.z0, p.z1, p.mat['color'], inferred=True, note=note, **_mat(p.mat))


def _face_centre(p, n):
    a = (p.x if p.axis == 'y' else p.y)
    zc = (p.z0 + p.z1) / 2
    return [a, n, zc] if p.axis == 'y' else [n, a, zc]


def _in_yaw(p):
    return YAW_IN[p.side]


def graphic_panel(b, p, scope, note):
    a0, a1 = _along(p)
    depth = p.wx if p.axis == 'x' else p.dy
    face, s = p.wall_face, p.sign
    _wall_box(b, f'{p.id}_panel', scope, 'lightbox' if p.mat['emission'] else 'graphic', p, face, face + s * depth, a0, a1,
              p.z0, p.z1, p.mat['color'], inferred=True, note=note, **_mat(p.mat))
    text_on_face(b, p, scope, _face_centre(p, face + s * depth), _in_yaw(p), min(0.25, (p.z1 - p.z0) * 0.4), note)


def tv(b, p, scope, note):
    inch = int((p.el.get('params') or {}).get('inch') or 55)
    centre = p.x if p.axis == 'y' else p.y
    cm.tv(b, p.id, scope, p.wall_face, centre, inch, bezel=0.02, depth=0.05, centre_z=(p.z0 + p.z1) / 2, face=FACE[p.side],
          frame_color=cm.METAL_DARK, inferred=True)


def led_wall(b, p, scope, note):
    params = p.el.get('params') or {}
    module = params.get('module_m') or 0.5
    a0, a1 = _along(p)
    cols, rows = max(1, round((a1 - a0) / module)), max(1, round((p.z1 - p.z0) / module))
    depth = 0.1
    if p.side == 'back':
        cm.led_wall(b, p.id, scope, p.wall_face, (a0 + a1) / 2 - cols * module / 2, p.z0, cols=cols, rows=rows, module=module,
                    depth=depth, face_color=p.mat['color'], inferred=True)
        return
    w, h = cols * module, rows * module
    coords = 'xz' if p.axis == 'y' else 'yz'
    pattern = {'type': 'grid', 'spacing': [module, module], 'line_width': min(0.003, module / 10),
               'line_color': [0.32, 0.32, 0.35], 'coordinates': coords,
               'offset': [round((w / 2) % module, 9), round((h / 2) % module, 9)]}
    mid = (a0 + a1) / 2
    with b.grouped(p.id):
        _wall_box(b, f'{p.id}_panel', scope, 'led_wall', p, p.wall_face, p.wall_face + p.sign * depth, mid - w / 2, mid + w / 2,
                  p.z0, p.z0 + h, p.mat['color'], inferred=True, roughness=0.25, emission=0.35, pattern=pattern,
                  note=f'LED wall of {cols} x {rows} modules of {module} m; media content neutralised.')


# ------------------------------------------------------------------ floor-standing
def counter(b, p, scope, note):
    params = p.el.get('params') or {}
    plinth = 0.12 if params.get('floating') else 0.0
    cm.counter(b, p.id, scope, p.xr, p.yr, floor_z=p.z0, height=p.z1 - p.z0, body_color=p.mat['color'],
               top_color=p.mat['top_color'], plinth=plinth, plinth_color=cm.METAL_DARK if plinth else None, inferred=True)
    fx, fy = yaw_vector(p.yaw)
    # the counter's front face (towards the visitors) carries the text from the brief
    front = [p.x + fx * p.wx / 2, p.y + fy * p.dy / 2, p.z0 + (p.z1 - p.z0) * 0.55]
    text_on_face(b, p, scope, front, p.yaw, 0.12, note)


def round_table(b, p, scope, note):
    cm.round_table(b, p.id, scope, p.x, p.y, floor_z=p.z0, radius=p.wx / 2, height=p.z1 - p.z0, color=p.mat['color'],
                   inferred=True)


def chair(b, p, scope, note):
    cm.shell_chair(b, p.id, scope, p.x, p.y, p.yaw, floor_z=p.z0, color=p.mat['color'], inferred=True)


def seating_group(b, p, scope, note):
    n = int(p.el.get('count') or 2)
    radius = cm.PRIOR['table_radius_4p'] if n > 2 else 0.35
    with b.grouped(p.id):
        cm.round_table(b, f'{p.id}_table', scope, p.x, p.y, floor_z=p.z0, radius=radius, color=p.mat['color'], inferred=True)
        ring = radius + 0.38
        for i in range(n):
            th = math.radians(p.yaw + 90 + 360 * i / n)
            cx, cy = p.x + ring * math.cos(th), p.y + ring * math.sin(th)
            cm.shell_chair(b, f'{p.id}_chair_{i}', scope, cx, cy, math.degrees(th) - 90, floor_z=p.z0, color=p.mat['chair_color'],
                           inferred=True)


def planter(b, p, scope, note):
    n = int(p.el.get('count') or 1)
    size = p.el.get('_unit') or [0.4, 0.4, 0.45]
    pitch = p.el.get('pitch_m') or size[0] + 0.2
    along_y = p.axis == 'x'
    for i in range(n):
        off = (i - (n - 1) / 2) * pitch
        cx, cy = (p.x, p.y + off) if along_y else (p.x + off, p.y)
        hw, hd = (size[1] / 2, size[0] / 2) if along_y else (size[0] / 2, size[1] / 2)
        cm.planter(b, f'{p.id}_{i}' if n > 1 else p.id, scope, (cx - hw, cx + hw), (cy - hd, cy + hd), floor_z=p.z0,
                   height=size[2], color=p.mat['color'], seed=7 + i, inferred=True)


def display_plinth(b, p, scope, note):
    with b.grouped(p.id):
        b.box(f'{p.id}_body', scope, 'plinth', p.xr, p.yr, (p.z0, p.z1 - 0.02), p.mat['color'], inferred=True, note=note,
              bevel=(0.01, 'vertical'), **_mat(p.mat))
        b.box(f'{p.id}_top', scope, 'plinth', p.xr, p.yr, (p.z1 - 0.02, p.z1), p.mat['top_color'], inferred=True, note=note,
              roughness=0.3)


def storage_block(b, p, scope, note):
    with b.grouped(p.id):
        b.box(p.id, scope, 'cabinet', p.xr, p.yr, (p.z0, p.z1), p.mat['color'], inferred=True,
              note=note + ' Door not modelled.', **_mat(p.mat))
    fx, fy = yaw_vector(p.yaw)
    text_on_face(b, p, scope, [p.x + fx * p.wx / 2, p.y + fy * p.dy / 2, p.z0 + (p.z1 - p.z0) * 0.7], p.yaw, 0.2, note)


def partition(b, p, scope, note):
    with b.grouped(p.id):
        b.box(p.id, scope, 'partition', p.xr, p.yr, (p.z0, p.z1), p.mat['color'], inferred=True, note=note, **_mat(p.mat))
    fx, fy = yaw_vector(p.yaw)
    text_on_face(b, p, scope, [p.x + fx * p.wx / 2, p.y + fy * p.dy / 2, (p.z0 + p.z1) / 2], p.yaw, 0.25, note)


def box(b, p, scope, note):
    role = (p.el.get('params') or {}).get('role') or 'box'
    with b.grouped(p.id):
        b.box(p.id, scope, role, p.xr, p.yr, (p.z0, p.z1), p.mat['color'], inferred=True, note=note, **_mat(p.mat))


BUILDERS = {'feature_wall': feature_wall, 'graphic_panel': graphic_panel, 'tv': tv, 'led_wall': led_wall,
            'counter': counter, 'round_table': round_table, 'chair': chair, 'seating_group': seating_group,
            'planter': planter, 'display_plinth': display_plinth, 'storage_block': storage_block, 'partition': partition,
            'box': box}
SCOPE = {'feature_wall': 'shell', 'graphic_panel': 'graphics', 'tv': 'media', 'led_wall': 'media', 'counter': 'furniture',
         'round_table': 'furniture', 'chair': 'furniture', 'seating_group': 'furniture', 'planter': 'furniture',
         'display_plinth': 'furniture', 'storage_block': 'furniture', 'partition': 'shell', 'box': 'furniture',
         'header_band': 'shell', 'ring': 'shell', 'spotlights': 'lighting', 'wall': 'shell', 'canopy_frame': 'shell',
         'lit_panel': 'shell', 'prop_block': 'furniture'}


# ------------------------------------------------------------------ overhead elements (the hook for new kinds)
@dataclass(frozen=True)
class Frame:
    """What design_compile hands to an overhead builder: the footprint, the vertical limits, the open and closed sides, and the three
    ways to leave a trace in compile_report.json (assume: a default; adjust: a change made to the element; warn: a remark).
    Read only: a builder adds parts through the SpecBuilder it gets and never changes the frame."""
    X0: float
    X1: float
    Y0: float
    Y1: float
    zf: float                  # top of the floor
    H: float                   # height limit above the hall floor
    wall_top: float
    open_sides: tuple
    closed_sides: tuple
    hanging_allowed: Any       # True, False or None (not stated)
    assume: Callable           # assume(element_id, what, value, source='prior') -> value
    adjust: Callable           # adjust(element_id, what)
    warn: Callable             # warn(message)
    brief: str = ''            # the designer's brief text (a builder must never copy words from it into the model)
    entrance: str = 'front'


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


# ------------------------------------------------------------------ canopy_frame
# A hollow rounded frame at the top, a different band width on each side (so the opening is off centre) and, optionally, one
# deeper sign band around a corner whose outer face is the frame's own outer face. profile_extrusion has no inner hole, so the
# frame is cut into two pieces by planes through the opening (the piece next to the sign corner is the deeper one); the pieces share
# their cut faces and their outer points, so nothing shows at the joints.
CANOPY_CORNERS = ('front_left', 'front_right', 'back_left', 'back_right')
CANOPY_RADIUS = 0.3            # default outer corner radius (m), like the ring
CANOPY_HEIGHT = 0.3            # default fascia height (m), like the ring
CANOPY_INSET = 0.5             # default distance of the outer edge from each footprint edge (m), like the ring
CANOPY_BAND = 0.4              # default band width (m), shrunk when the frame is small
CANOPY_OPENING = 0.05          # least size of the opening in each direction (m)
CANOPY_CUT_CLEAR = 0.001       # a sign cut must clear the opening's corner by this much (m)
CANOPY_DROP = 0.25             # default extra depth of the sign (m)
CANOPY_CHARS = 4               # default number of placeholder letters
CANOPY_MAX_CHARS = 40
CANOPY_MAX_DROP = 2.0
CANOPY_POST = 0.15             # post size (m)
CANOPY_ARC = 12                # segments per corner arc
CANOPY_GLYPH = 0.7             # width of one capital letter as a fraction of its height (a generous estimate)
CANOPY_SIDES = ('front', 'back', 'left', 'right')
PLACEHOLDER_MARK = 'Placeholder letters'      # in the note of every text part made of placeholder letters (design_compile looks for it)


def _canopy_plan(el, ext, vertical=None):
    """Resolve every value of a canopy_frame element against the footprint ext = (X0, X1, Y0, Y1).
    -> (plan, defaults, problems): defaults = [(what, value)] that the builder records as assumed; problems = [(path, message)]."""
    X0, X1, Y0, Y1 = ext
    W, D = X1 - X0, Y1 - Y0
    params = el.get('params') or {}
    problems, defaults = [], []
    # vertical = (height limit, floor top) when the vertical fit is to be checked too

    def bad(path, message):
        problems.append((path, message))
    plan = {'bands': {}, 'sign': None}
    # -- size, centre, radius
    given = (list(el.get('size_m') or []) + [None] * 3)[:3]
    free = (W - 2 * CANOPY_INSET, D - 2 * CANOPY_INSET, CANOPY_HEIGHT)
    size = []
    for v, f, name in zip(given, free, ('width', 'depth', 'height')):
        if v is None:
            size.append(f)
            defaults.append((f'{name}_m', round(f, 4)))      # only the values that are missing are assumed
        elif _is_num(v) and v > 0:
            size.append(float(v))
        else:
            bad('size_m', f'the {name} of the frame must be a positive number of metres')
            size.append(f)
    ow, od, h = size
    if ow < 0.2 or od < 0.2:
        bad('size_m', 'the footprint is too small for the default frame (each side set in by '
                      f'{CANOPY_INSET:g} m): give size_m')
    if ow > W + 1e-6 or od > D + 1e-6:
        bad('size_m', f'the outer edge {ow:g} x {od:g} m is larger than the footprint {W:g} x {D:g} m')
    plan.update(ow=ow, od=od, h=h)
    text_size = (el.get('text') or {}).get('size_m')
    if text_size is not None and not (_is_num(text_size) and text_size > 0):
        bad('text.size_m', 'the letter size must be a positive number of metres')
        text_size = None
    plan['text_size'] = None if text_size is None else float(text_size)
    centre = el.get('centre_m')
    if centre:
        plan['centre'] = (float(centre[0]), float(centre[1]))
    else:
        plan['centre'] = ((X0 + X1) / 2, (Y0 + Y1) / 2)
        defaults.append(('centre_m', [round(plan['centre'][0], 4), round(plan['centre'][1], 4)]))
    rmax = max(0.0, min(ow, od) / 2)
    r = params.get('corner_radius_m')
    if r is None:
        r = round(min(CANOPY_RADIUS, max(0.0, rmax - 0.05)), 4)
        defaults.append(('corner_radius_m', r))
    elif not _is_num(r) or r < 0 or r > rmax + 1e-9:
        bad('params.corner_radius_m', f'the corner radius must lie between 0 and half the shorter outer side ({rmax:g} m)')
        r = 0.0
    plan['radius'] = float(r)
    # -- band widths
    base = round(min(CANOPY_BAND, 0.25 * min(ow, od)), 4)
    given_bands = params.get('band_m') or {}
    ok = True
    for side in CANOPY_SIDES:
        v = given_bands.get(side)
        if v is None:
            plan['bands'][side] = base
            defaults.append((f'band_m.{side}', base))
        elif _is_num(v) and v > 0:
            plan['bands'][side] = float(v)
        else:
            bad(f'params.band_m.{side}', 'a band width must be a positive number of metres')
            plan['bands'][side] = base
            ok = False
    b = plan['bands']
    if ok:
        if b['front'] + b['back'] > od - CANOPY_OPENING:
            bad('params.band_m', f"the front and back bands ({b['front']:g} + {b['back']:g} m) leave no opening in the {od:g} m depth")
        if b['left'] + b['right'] > ow - CANOPY_OPENING:
            bad('params.band_m', f"the left and right bands ({b['left']:g} + {b['right']:g} m) leave no opening in the {ow:g} m width")
    # -- the sign
    sg = params.get('sign')
    if sg is not None:
        corner = sg.get('corner')
        if corner is None:
            corner = 'front_left'
            defaults.append(('sign.corner', corner))
        if corner not in CANOPY_CORNERS:
            bad('params.sign.corner', f'the corner must be one of {", ".join(CANOPY_CORNERS)}')
            corner = 'front_left'
        edge1, lateral = corner.split('_')                   # front_left: the first edge is the front, the second the left
        opposite = 'right' if lateral == 'left' else 'left'
        other = 'back' if edge1 == 'front' else 'front'
        # a cut must fall on the straight part of the outer outline and of the opening: past the corner arcs at its own corner and
        # short of the arcs at the far corner (a cut on an arc cannot be ordered by corner name and wraps the whole frame)
        iw, idp = ow - b['left'] - b['right'], od - b['front'] - b['back']
        if iw > 0 and idp > 0:
            ri = _inner_radii(ow, od, plan['radius'], b)
            near, far_x, far_y = _CORNER_INDEX[(edge1, lateral)], _CORNER_INDEX[(edge1, opposite)], _CORNER_INDEX[(other, lateral)]
            r_out = plan['radius']
            lo = (max(r_out, b[lateral] + ri[near]) + CANOPY_CUT_CLEAR, max(r_out, b[edge1] + ri[near]) + CANOPY_CUT_CLEAR)
            hi = (ow - max(r_out, b[opposite] + ri[far_x]) - CANOPY_CUT_CLEAR,
                  od - max(r_out, b[other] + ri[far_y]) - CANOPY_CUT_CLEAR)
        else:
            lo, hi = (0.0, 0.0), (0.0, 0.0)
        end_raw = sg.get('end_radius_m')
        end_pos = float(end_raw) if _is_num(end_raw) and end_raw > 0 else 0.0
        lo = (lo[0] + end_pos, lo[1] + end_pos)             # the fillet needs its own length of straight edge before the cut
        length = (ow, od)
        names = (f'{edge1} edge ({ow:g} m)', f'{lateral} edge ({od:g} m)')
        along = sg.get('along_m')
        if along is None:
            along = [_round_into(min(max(0.4 * ow, lo[0] + 0.3), hi[0]), lo[0], hi[0]),
                     _round_into(min(max(0.3 * od, lo[1] + 0.3), hi[1]), lo[1], hi[1])]
            defaults.append(('sign.along_m', along))
        if not (isinstance(along, (list, tuple)) and len(along) == 2 and all(_is_num(v) and v > 0 for v in along)):
            bad('params.sign.along_m', 'along_m needs two positive lengths: along the first edge and along the second edge')
            along = [lo[0], lo[1]]
        else:
            for i in (0, 1):
                if along[i] > length[i] + 1e-9:
                    bad('params.sign.along_m', f'{along[i]:g} m is longer than the {names[i]} it runs along')
                elif not lo[i] - CANOPY_TOL <= along[i] <= hi[i] + CANOPY_TOL:
                    bad('params.sign.along_m', f'{along[i]:g} m must stay between {lo[i]:.3f} and {hi[i]:.3f} m so that the cut falls '
                                               'on the straight part of the outer edge and of the opening, clear of the corner arcs')
        drop = sg.get('drop_m')
        if drop is None:
            drop = CANOPY_DROP
            defaults.append(('sign.drop_m', drop))
        if not _is_num(drop) or not 0 < drop <= CANOPY_MAX_DROP:
            bad('params.sign.drop_m', f'drop_m must be above 0 and at most {CANOPY_MAX_DROP:g} m')
            drop = CANOPY_DROP
        end = sg.get('end_radius_m')
        if end is None:
            end = 0.0
        elif not _is_num(end) or end < 0 or end > drop + 1e-9:
            bad('params.sign.end_radius_m', f'end_radius_m must lie between 0 and drop_m ({drop:g} m)')
            end = 0.0
        elif 0 < end < CANOPY_MIN_END_RADIUS:
            bad('params.sign.end_radius_m', f'end_radius_m below {CANOPY_MIN_END_RADIUS:g} m is not a fillet: use 0 for a square end')
            end = 0.0
        chars = sg.get('text_chars')
        if chars is None:
            chars = CANOPY_CHARS
            defaults.append(('sign.text_chars', chars))
        if _is_num(chars) and float(chars).is_integer():
            chars = int(chars)
        if not isinstance(chars, int) or isinstance(chars, bool) or not 0 <= chars <= CANOPY_MAX_CHARS:
            bad('params.sign.text_chars', f'text_chars must be a whole number from 0 to {CANOPY_MAX_CHARS}')
            chars = 0
        plan['sign'] = {'corner': corner, 'along': [float(along[0]), float(along[1])], 'drop': float(drop), 'chars': chars,
                        'end': float(end)}
    if vertical is not None:
        problems.extend(_canopy_vertical(h, plan['sign']['drop'] if plan['sign'] else 0.0, el.get('z_m'), *vertical)[2])
    if not problems:
        try:
            _canopy_geometry(plan)
        except ValueError as ex:
            bad('params.corner_radius_m' if 'radius' in str(ex) else 'params', str(ex))
    return plan, defaults, problems


def _rounded_loop(x0, x1, y0, y1, radii, n=CANOPY_ARC):
    """Counter-clockwise outline of a rectangle with its own radius at each corner (front-left, front-right, back-right, back-left).
    A corner of radius 0 is one point."""
    fl, fr, br, bl = radii
    pts = []
    for (cx, cy), r, a0 in (((x1 - fr, y0 + fr), fr, -90.0), ((x1 - br, y1 - br), br, 0.0),
                            ((x0 + bl, y1 - bl), bl, 90.0), ((x0 + fl, y0 + fl), fl, 180.0)):
        if r <= 0:
            pts.append((cx, cy))
            continue
        for k in range(n + 1):
            a = math.radians(a0 + 90.0 * k / n)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    out = []
    for p in pts:
        if not out or math.dist(out[-1], p) > 1e-9:
            out.append(p)
    if len(out) > 1 and math.dist(out[0], out[-1]) <= 1e-9:
        out.pop()
    return out


def _round_into(v, lo, hi):
    """v rounded to 4 decimals, but never outside [lo, hi] because of the rounding (round inwards)."""
    r = round(v, 4)
    if lo - CANOPY_TOL <= r <= hi + CANOPY_TOL:
        return r
    for candidate in (math.floor(v * 1e4) / 1e4, math.ceil(v * 1e4) / 1e4):       # a floor of v * 1e4 can still come out above v
        if lo - CANOPY_TOL <= candidate <= hi + CANOPY_TOL:
            return candidate
    return min(max(v, lo), hi)                                                    # the range is narrower than 0.0001 m


CANOPY_TOL = 1e-6               # tolerance when a value is compared with its limit (a value read back from the report is accepted)
CANOPY_MIN_END_RADIUS = 0.001  # least vertical end radius that is built as a fillet (m)
CANOPY_END_ARC = 12            # segments in the quarter round of a sign end
CANOPY_CLEARANCE = 0.3         # least height of the lowest point of the frame above the floor (m)
CANOPY_UNDER_LIMIT = 0.1       # default distance of the top of the frame below the height limit (m)


def _canopy_vertical(h, drop, z_m, H, zf):
    """Where the frame stands vertically. -> (bottom, top, problems [(path, message)]).
    z_m given: the bottom is zf + z_m; otherwise the top is 0.1 m under the limit H. The top may not pass H and the lowest point
    (the bottom, or the bottom of the deeper sign) must stay 0.3 m above the floor top zf."""
    problems = []
    lo_z, hi_z = CANOPY_CLEARANCE + drop, H - zf - h                # the z_m that fit (the bottom's height above the floor)
    deeper = f' and its sign {drop:g} m deeper' if drop else ''
    if _is_num(z_m):
        bottom = zf + float(z_m)
        top = bottom + h
        if top > H + CANOPY_TOL or bottom - drop < zf + CANOPY_CLEARANCE - CANOPY_TOL:      # the same tolerance on both limits
            if lo_z <= hi_z + 1e-9:
                problems.append(('z_m', f'z_m {z_m:g} m must lie between {lo_z:.3f} and {hi_z:.3f} m: the frame is {h:g} m high{deeper}, '
                                        f'its top may not pass the height limit {H:g} m and its lowest point stays '
                                        f'{CANOPY_CLEARANCE:g} m above the floor'))
            else:
                problems.append(('z_m', f'no z_m fits: the frame ({h:g} m high{deeper}) needs {h + drop:.3f} m between '
                                        f'{CANOPY_CLEARANCE:g} m above the floor and the height limit {H:g} m, which leaves '
                                        f'{H - zf - CANOPY_CLEARANCE:.3f} m'))
    else:
        top = H - CANOPY_UNDER_LIMIT
        bottom = top - h
        if bottom - drop < zf + CANOPY_CLEARANCE - CANOPY_TOL:
            room = H - zf - CANOPY_CLEARANCE - CANOPY_UNDER_LIMIT
            path = 'params.sign.drop_m' if drop and h <= room else 'size_m'
            hint = f'; or give z_m between {lo_z:.3f} and {hi_z:.3f} m' if lo_z <= hi_z + 1e-9 else ''
            problems.append((path, f'with the top {CANOPY_UNDER_LIMIT:g} m under the height limit {H:g} m the frame height plus the sign '
                                   f'drop may be at most {room:.3f} m ({h:g} + {drop:g} m asked){hint}'))
    return bottom, top, problems


_CORNER_INDEX = {('front', 'left'): 0, ('front', 'right'): 1, ('back', 'right'): 2, ('back', 'left'): 3}


def _inner_radii(ow, od, r, b):
    """Corner radii of the opening (front-left, front-right, back-right, back-left): the outer radius less the larger of the two
    bands at that corner, not below 0 and not above half of the opening's shorter side."""
    iw, idp = ow - b['left'] - b['right'], od - b['front'] - b['back']
    return [min(max(0.0, r - max(x, y)), iw / 2, idp / 2) for x, y in
            ((b['front'], b['left']), (b['front'], b['right']), (b['back'], b['right']), (b['back'], b['left']))]


def _canopy_loops(plan):
    """-> (outer loop, inner loop, outer rect, inner rect): counter-clockwise plan outlines in metres."""
    cx, cy = plan['centre']
    ow, od, r, b = plan['ow'], plan['od'], plan['radius'], plan['bands']
    outer = (cx - ow / 2, cx + ow / 2, cy - od / 2, cy + od / 2)
    inner = (outer[0] + b['left'], outer[1] - b['right'], outer[2] + b['front'], outer[3] - b['back'])
    iw, idp = inner[1] - inner[0], inner[3] - inner[2]
    if iw <= 0 or idp <= 0:
        raise ValueError('the bands leave no opening')
    inner_radii = _inner_radii(ow, od, r, b)
    outer_loop = _rounded_loop(*outer, (r, r, r, r))
    inner_loop = _rounded_loop(*inner, inner_radii)
    # the opening must lie inside the outer outline (it can poke out when the corner radius is large for thin bands)
    for p in inner_loop:
        for i in range(len(outer_loop)):
            a, c = outer_loop[i], outer_loop[(i + 1) % len(outer_loop)]
            if (c[0] - a[0]) * (p[1] - a[1]) - (c[1] - a[1]) * (p[0] - a[0]) < 0.002 * math.dist(a, c):
                raise ValueError('the corner radius is too large for the band widths: the opening would reach the outer edge')
    return outer_loop, inner_loop, outer, inner


def _hits(loop, value, axis):
    """Where the line coordinate[axis] == value crosses the closed loop: [(other coordinate, segment index, point or None)];
    point is None when the line goes through vertex `index`."""
    other = 1 - axis
    out = []
    n = len(loop)
    for i in range(n):
        a, c = loop[i], loop[(i + 1) % n]
        da, dc = a[axis] - value, c[axis] - value
        if abs(da) < 1e-9:
            out.append((a[other], i, None))
        elif abs(dc) >= 1e-9 and da * dc < 0:
            t = da / (da - dc)
            pt = [0.0, 0.0]
            pt[axis], pt[other] = value, a[other] + t * (c[other] - a[other])
            out.append((pt[other], i, tuple(pt)))
    return out


def _cut(loop, value, axis, pick):
    """Put a vertex on the loop where the line crosses it at the end `pick` (min or max of the other coordinate); -> that vertex."""
    hits = _hits(loop, value, axis)
    if not hits:
        raise ValueError('a cut does not cross the frame')
    _, i, pt = pick(hits, key=lambda h: h[0])
    if pt is None:
        return loop[i]
    loop.insert(i + 1, pt)
    return pt


def _walk(loop, i, j, step):
    out = [loop[i]]
    while i != j:
        i = (i + step) % len(loop)
        out.append(loop[i])
    return out


def _canopy_geometry(plan):
    """-> (frame polygons, sign polygon or None) in world plan coordinates (counter-clockwise or not: profile_extrusion takes both).
    With a sign end radius the sign polygon stops one radius short of each free end; the stretch in between is a slab
    (_canopy_slabs) with the quarter round in its profile, and the frame polygons are cut where the sign ends."""
    frame, sign_poly = _canopy_pieces(plan, None)
    sign = plan['sign']
    if sign is not None and sign['end'] > 0:
        sign_poly = _canopy_pieces(plan, (sign['along'][0] - sign['end'], sign['along'][1] - sign['end']))[1]
    return frame, sign_poly


def _canopy_slabs(plan):
    """The two end stretches of a sign with end_radius_m, as plan rectangles on the straight parts of the band:
    [{'axis': 'x' or 'y' (the edge they run along), 'cut': coordinate of the free end, 'dir': +1 or -1 (the sign lies toward
    smaller coordinates when +1), 'rect': (x0, x1, y0, y1)}]. Empty without an end radius."""
    sign = plan['sign']
    if sign is None or sign['end'] <= 0:
        return []
    orect, irect = _canopy_loops(plan)[2:]
    first, second = sign['corner'].split('_')
    left, front = second == 'left', first == 'front'
    a1, a2 = sign['along']
    e = sign['end']
    x_cut = orect[0] + a1 if left else orect[1] - a1
    y_cut = orect[2] + a2 if front else orect[3] - a2
    xr = (x_cut - e, x_cut) if left else (x_cut, x_cut + e)
    yr = (y_cut - e, y_cut) if front else (y_cut, y_cut + e)
    band_y = (orect[2], irect[2]) if front else (irect[3], orect[3])      # the band of the first edge, across it
    band_x = (orect[0], irect[0]) if left else (irect[1], orect[1])       # the band of the second edge, across it
    return [{'axis': 'x', 'cut': x_cut, 'dir': 1 if left else -1, 'rect': (xr[0], xr[1], band_y[0], band_y[1])},
            {'axis': 'y', 'cut': y_cut, 'dir': 1 if front else -1, 'rect': (band_x[0], band_x[1], yr[0], yr[1])}]


def _canopy_pieces(plan, along):
    """The ring cut into its pieces; along overrides the sign's cut positions. Every polygon is a piece of the ring between two
    straight cuts, so pieces share cut faces and outer points."""
    outer, inner, orect, irect = _canopy_loops(plan)
    sign = plan['sign']
    if sign is None:
        xs = (irect[0] + irect[1]) / 2
        cuts = [(_cut(outer, xs, 0, min), _cut(inner, xs, 0, min)), (_cut(outer, xs, 0, max), _cut(inner, xs, 0, max))]
        (f_o, f_i), (k_o, k_i) = cuts
        pieces = [(f_o, f_i, k_o, k_i), (k_o, k_i, f_o, f_i)]          # right half, left half
        sign_piece = None
        rest = pieces
    else:
        corner, (a1, a2) = sign['corner'], along or sign['along']
        first, second = corner.split('_')
        left = second == 'left'
        x_cut = orect[0] + a1 if left else orect[1] - a1
        y_cut = orect[2] + a2 if first == 'front' else orect[3] - a2
        c1 = (_cut(outer, x_cut, 0, min if first == 'front' else max), _cut(inner, x_cut, 0, min if first == 'front' else max))
        c2 = (_cut(outer, y_cut, 1, min if left else max), _cut(inner, y_cut, 1, min if left else max))
        # counter-clockwise the outer outline runs front (left to right), right, back (right to left), left (back to front)
        a, b = (c2, c1) if corner in ('front_left', 'back_right') else (c1, c2)
        sign_piece = (a[0], a[1], b[0], b[1])
        rest = [(b[0], b[1], a[0], a[1])]
    out = []
    for (oa, ia, ob, ib) in ([sign_piece] if sign_piece else []) + list(rest):
        poly = _walk(outer, outer.index(oa), outer.index(ob), 1) + _walk(inner, inner.index(ib), inner.index(ia), -1)
        out.append(poly)
    # This only catches pieces that do not partition the ring (a gap or an overlap). It does NOT catch a wrong cut order: pieces made
    # from the wrong side of the cuts still add up to the ring. What keeps the order right is that _canopy_plan lets a cut fall only
    # on the straight part of the outer edge and of the opening (the along_m limits), where the corner name fixes the order.
    ring = _area(outer) - _area(inner)
    if abs(sum(_area(q) for q in out) - ring) > 1e-6 * max(1.0, ring):
        raise ValueError('the cuts do not split the frame into clean pieces: move sign.along_m')
    return (out[1:], out[0]) if sign_piece else (out, None)


def _area(poly):
    return abs(sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1] for i in range(len(poly)))) / 2


def _clip_area(poly, rect):
    """Area of a polygon inside the rectangle (x0, x1, y0, y1), by clipping one edge of the rectangle after the other."""
    x0, x1, y0, y1 = rect
    for axis, value, keep_above in ((0, x0, True), (0, x1, False), (1, y0, True), (1, y1, False)):
        out = []
        for i, p in enumerate(poly):
            q = poly[i - 1]
            p_in = p[axis] >= value if keep_above else p[axis] <= value
            q_in = q[axis] >= value if keep_above else q[axis] <= value
            if p_in != q_in:
                t = (value - q[axis]) / (p[axis] - q[axis])
                out.append((q[0] + t * (p[0] - q[0]), q[1] + t * (p[1] - q[1])))
            if p_in:
                out.append(p)
        poly = out
        if not poly:
            return 0.0
    return _area(poly)


def _canopy_problems(el, ext, vertical=None):
    return _canopy_plan(el, ext, vertical)[2]


def check_canopy_frame(el, form):
    """PARAM_CHECKS entry: [(path inside the element, message)] for a canopy_frame element and the design's form."""
    W, D = form['footprint_m']
    return _canopy_problems(el, (-W / 2, W / 2, -D / 2, D / 2), (form['height_limit_m'], form.get('platform_height_m') or 0.0))


def _placeholder(n, brief):
    """n capital letters that are not a piece of the brief (the model never carries words from the brief or from the picture)."""
    up = (brief or '').upper()
    for letter in 'XQZJKWVYUHOMNABCDEFGILPRST':
        if letter * n not in up:
            return letter * n
    return 'X' * n


def _grid5(d):
    """d on the 5 decimal grid the pieces of a frame share. A value that sits on a half step (x.xxxxx5) must round the same way
    wherever it comes from, but two ways of computing the same coordinate differ by 1e-16 and round half even to different
    neighbours: first drop that noise (9 decimals), then round."""
    return round(round(d, 9), 5)


def _local(poly, centre):
    out = []
    for x, y in poly:
        p = (_grid5(x - centre[0]), _grid5(y - centre[1]))
        if not out or math.dist(out[-1], p) > 1e-6:
            out.append(p)
    if len(out) > 1 and math.dist(out[0], out[-1]) <= 1e-6:
        out.pop()
    return [list(p) for p in out]


def canopy_frame(b, frame, el, look, note):
    eid = el['id']
    note = note.replace('; text brief', '')           # no text of the brief is built here: the letters are placeholders
    plan, defaults, problems = _canopy_plan(el, (frame.X0, frame.X1, frame.Y0, frame.Y1), (frame.H, frame.zf))
    if problems:
        raise ValueError('; '.join(f'{path}: {message}' for path, message in problems))
    for what, value in defaults:
        frame.assume(eid, what, value)
    ow, od, h, radius, bands, sign = plan['ow'], plan['od'], plan['h'], plan['radius'], plan['bands'], plan['sign']
    # the frame stays inside the footprint
    cx, cy = plan['centre']
    nx = min(max(cx, frame.X0 + ow / 2), frame.X1 - ow / 2)
    ny = min(max(cy, frame.Y0 + od / 2), frame.Y1 - od / 2)
    if math.hypot(nx - cx, ny - cy) > 0.005:
        frame.adjust(eid, f'moved inside the footprint from ({cx:.2f}, {cy:.2f}) to ({nx:.2f}, {ny:.2f})')
    plan['centre'] = (round(nx, 4), round(ny, 4))
    cx, cy = plan['centre']
    # height
    bottom, top, _ = _canopy_vertical(h, sign['drop'] if sign else 0.0, el.get('z_m'), frame.H, frame.zf)   # the fit was checked above
    if not _is_num(el.get('z_m')):
        # recorded to 4 decimals, rounded inwards at the limits: the value read back is accepted by the same check with the same tolerance
        frame.assume(eid, 'z_m', _round_into(bottom - frame.zf, CANOPY_CLEARANCE + (sign['drop'] if sign else 0.0),
                                             frame.H - frame.zf - h))
    if ((el.get('text') or {}).get('body') or '').strip():
        frame.warn(f'{eid}: a canopy_frame carries placeholder letters only; its text.body is not built')
    ignored = [k for k in ('side', 'along', 'yaw_deg') if el.get(k) is not None and not (k == 'side' and el[k] == 'centre')]
    if ignored:
        frame.warn(f"{eid}: {', '.join(ignored)} given but not used: a canopy_frame is placed by centre_m and size_m")
    if (el.get('text') or {}).get('size_m') is not None and sign is None:
        frame.warn(f'{eid}: text.size_m is ignored because the frame has no sign to carry letters')
    frame_polys, sign_poly = _canopy_geometry(plan)
    centre = (cx, cy)
    mat = look
    extra = dict(roughness=mat['roughness'], metallic=mat['metallic'], emission=mat['emission'])
    for k, poly in enumerate(frame_polys):
        cid = f'{eid}_frame' if len(frame_polys) == 1 else f'{eid}_frame_{k}'
        b.add(cid, 'shell', 'profile_extrusion', 'canopy_frame', [cx, cy, round((bottom + top) / 2, 4)],
              {'profile': _local(poly, centre), 'depth': round(h, 4)}, mat['color'], rot=(-90, 0, 0), inferred=True, note=note, **extra)
    if sign_poly is not None:
        depth = h + sign['drop']
        b.add(f'{eid}_sign', 'shell', 'profile_extrusion', 'canopy_sign', [cx, cy, round(top - depth / 2, 4)],
              {'profile': _local(sign_poly, centre), 'depth': round(depth, 4)}, mat['color'], rot=(-90, 0, 0), inferred=True,
              note=note + ' Deeper sign band: its outer face is the frame\'s own outer face.', **extra)
        mid_z = round(top - depth / 2, 4)                       # the middle piece as stored: the end pieces take their heights from it
        _sign_ends(b, eid, plan, mat, note, mid_z - round(depth, 4) / 2, mid_z + round(depth, 4) / 2, extra)
        _sign_text(b, frame, eid, plan, mat, note, bottom - sign['drop'], top)
    if frame.hanging_allowed is not True:
        post_sizes = []
        orect, irect = _canopy_loops(plan)[2:]
        k = 1 - 1 / math.sqrt(2)
        ri = _inner_radii(ow, od, radius, bands)
        below = [(poly, bottom) for poly in frame_polys] + ([(sign_poly, bottom - sign['drop'])] if sign_poly is not None else [])
        for slab in _canopy_slabs(plan):                       # conservative: the end stretch counts as reaching the sign bottom
            x0, x1, y0, y1 = slab['rect']
            below.append(([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], bottom - sign['drop']))
        for j, ((cxo, cyo, cxi, cyi, dx, dy), rad_i) in enumerate(zip(
                ((orect[0], orect[2], irect[0], irect[2], 1, 1), (orect[1], orect[2], irect[1], irect[2], -1, 1),
                 (orect[1], orect[3], irect[1], irect[3], -1, -1), (orect[0], orect[3], irect[0], irect[3], 1, -1)), ri)):
            ox, oy = cxo + dx * radius * k, cyo + dy * radius * k
            ix, iy = cxi + dx * rad_i * k, cyi + dy * rad_i * k
            px, py = (ox + ix) / 2, (oy + iy) / 2
            size = min(CANOPY_POST, 0.6 * math.dist((ox, oy), (ix, iy)))
            post_sizes.append(size)
            square = (px - size / 2, px + size / 2, py - size / 2, py + size / 2)
            # the post stops at the bottom of the lowest piece above its footprint (the deeper sign, where it stands under it)
            post_top = min([low_z for poly, low_z in below if _clip_area(poly, square) > 1e-6] or [bottom])
            b.box(f'{eid}_post_{j}', 'shell', 'header_post', (square[0], square[1]), (square[2], square[3]),
                  (frame.zf, post_top), mat['color'], inferred=True, note=note + ' Post to the frame (hanging is not stated as allowed).')
        frame.assume(eid, 'posts', 'four posts, because hanging is not stated as allowed', 'choice')
        if min(post_sizes) < CANOPY_POST - 1e-9:
            frame.warn(f'{eid}: the posts are only {min(post_sizes):.3f} m wide (usual {CANOPY_POST:g} m) because the bands are thin')


def _sign_ends(b, eid, plan, mat, note, sign_bottom, top, extra):
    """The two end stretches of the deeper sign: profile_extrusion slabs whose profile is the sign's elevation, the bottom falling
    along a quarter circle (centre one radius above the sign bottom and one radius in from the free end) to the sign bottom."""
    e = plan['sign']['end']
    cx, cy = plan['centre']

    def snap(v, c):
        """v on the same 5 decimal grid the frame pieces use (their vertices are the centre plus a local value rounded to 5 decimals)."""
        return c + _grid5(v - c)
    zref = (sign_bottom + top) / 2
    for k, slab in enumerate(_canopy_slabs(plan)):
        x0, x1, y0, y1 = slab['rect']
        along_x = slab['axis'] == 'x'
        gx = (snap(x0, cx), snap(x1, cx))
        gy = (snap(y0, cy), snap(y1, cy))
        c_along, c_other = (cx, cy) if along_x else (cy, cx)
        near = snap(slab['cut'], c_along)                                 # the free end: where the frame piece starts
        far = snap(slab['cut'] - slab['dir'] * e, c_along)                # where the middle piece ends
        # the location is the middle of the slab on that grid, so every joint is on a grid value: no rounding of the location or the
        # depth moves a face away from the frame piece or the middle piece it meets
        loc_along = (near + far) / 2
        other = gy if along_x else gx
        loc_other = (other[0] + other[1]) / 2
        arc = []
        for i in range(CANOPY_END_ARC + 1):
            theta = math.pi / 2 * i / CANOPY_END_ARC             # equal steps of angle: the arc is steep at the free end
            w = near if i == 0 else far if i == CANOPY_END_ARC else slab['cut'] - slab['dir'] * e * (1 - math.cos(theta))
            arc.append((w - loc_along, sign_bottom + e - e * math.sin(theta) - zref))
        pts = arc + [(far - loc_along, top - zref), (near - loc_along, top - zref)]
        profile = []
        for u, z in pts:
            q = [round(u, 6), round(z, 6)]
            if not profile or math.dist(profile[-1], q) > 1e-7:
                profile.append(q)
        depth = other[1] - other[0]
        location = [loc_along, loc_other, zref] if along_x else [loc_other, loc_along, zref]
        part = b.add(f'{eid}_sign_end_{k}', 'shell', 'profile_extrusion', 'canopy_sign', location,
                     {'profile': profile, 'depth': round(depth, 6)}, mat['color'], rot=(0, 0, 0 if along_x else 90), inferred=True,
                     note=note + f' Sign end {k + 1}: a quarter round of {e:g} m in elevation, down to the sign bottom.', **extra)
        part['location'] = [round(v, 6) for v in location]               # add() rounds to 4 decimals: keep the grid values


def _sign_text(b, frame, eid, plan, mat, note, sign_bottom, top):
    """Placeholder capital letters on the sign's outer face along its first edge."""
    sign = plan['sign']
    n = sign['chars']
    if n <= 0:
        return
    first, second = sign['corner'].split('_')
    cx, cy = plan['centre']
    ow, od, radius = plan['ow'], plan['od'], plan['radius']
    a1 = sign['along'][0]
    left = second == 'left'
    x_corner = cx - ow / 2 if left else cx + ow / 2
    sx = 1 if left else -1
    end = sign['end']                                          # the fillet at the free end is not a place for letters
    straight = a1 - end - radius
    height = top - sign_bottom
    fits = round(min(0.5 * height, 0.9 * straight / (CANOPY_GLYPH * n)), 4)       # the largest size that fits the sign face
    if straight < 0.15 or fits < 0.04:
        frame.warn(f'{eid}: the sign is too short for {n} letters; no placeholder text was added')
        return
    size = fits
    if plan['text_size'] is not None:
        size = round(min(plan['text_size'], fits), 4)
        if plan['text_size'] > fits + 1e-9:
            frame.warn(f"{eid}: text size {plan['text_size']:g} m does not fit the sign face; {size:g} m was built")
    body = _placeholder(n, frame.brief)
    frame.assume(eid, 'sign_text', body, 'choice')
    front = first == 'front'
    y_face = cy - od / 2 if front else cy + od / 2
    yaw = YAW_OUT['front'] if front else YAW_OUT['back']
    u = x_corner + sx * (radius + a1 - end) / 2
    y = y_face + (-0.012 if front else 0.012)
    add_text(b, f'{eid}_text', 'graphics', body, size, [round(u, 4), round(y, 4), round((sign_bottom + top) / 2, 4)], yaw, mat['text_color'],
             note + f' {PLACEHOLDER_MARK}: text choice; nothing is read from the brief or from the picture.')


OVERHEAD_BUILDERS = {'canopy_frame': canopy_frame}
PARAM_CHECKS = {'canopy_frame': check_canopy_frame}
ROLE = {'canopy_frame': 'secondary'}       # colour role of a kind that design_compile.ROLE does not list


# ------------------------------------------------------------------ lit_panel
# A free-standing wall panel, an upside down U in elevation (the top corners are rounded), with a light groove let into its face and,
# when asked, an arched niche with a back wall, a floor and a ring of light round the opening. profile_extrusion has no inner hole, so
# every layer is cut into pieces that share their cut faces and their outline points:
#   frame         the panel outline minus the groove outline (an inverted U open at the bottom), full thickness
#   groove_base   the groove band, from the groove depth to the back of the panel; the groove floor is its front face
#   groove_light  the glowing band, a 0.5 mm plate lying on the groove floor (it does not reach into the groove base: same facing faces
#                 of two parts in one plane would z-fight)
#   core          inside the groove: one piece, or (with a niche) core_base under the ring and core round it
#   ring pieces   a glowing front layer and a backing layer, each an inverted U round the opening plus a bar under it
#   niche_back    the wall behind the opening, from the niche depth to the back of the panel
# Everything is computed from the panel size and the parameters; no outline is typed in.
LIT_RADIUS = 0.4               # default radius of the two top corners (m), at most half the width
LIT_INSET = 0.08               # default distance of the groove from the panel edge (m)
LIT_GROOVE_WIDTH = 0.03        # default groove width (m)
LIT_GROOVE_DEPTH = 0.02        # default groove depth (m)
LIT_LIGHT_HEX = '#ffe2a8'      # default colour of the light: warm white
LIT_RING_WIDTH = 0.03          # default width of the ring of light round a niche (m)
LIT_EMISSION = 1.5             # glow of a light part: the value design_compile.look gives an emissive element
LIT_MIN_THICK = 0.04           # least panel thickness (m)
LIT_MIN_SLAB = 0.02            # least material left behind the groove floor (m)
LIT_MIN_WALL = 0.01            # least material left behind a niche (m)
LIT_MIN_FEATURE = 0.005        # least groove width, groove depth, inset, ring width and niche depth (m)
LIT_MIN_CORE = 0.05            # least width and height of the panel core inside the groove (m)
LIT_MIN_OPENING = 0.05         # least width and height of a niche opening (m)
LIT_MIN_BASE = 0.02            # least solid height under the ring of a niche (m)
LIT_CLEAR = 0.005              # the niche ring keeps this far from the groove (m)
LIT_RING_DEPTH = 0.02          # depth of the glowing layer of the ring (m)
LIT_PROUD = 0.0005             # the light band lies on the groove floor and is this thick (m)
LIT_ARC = 12                   # segments per rounded corner
LIT_SQUARE = 0.001             # a corner radius below this is built as a square corner
LIT_NICHE_KEYS = ('width_m', 'height_m', 'bottom_m', 'depth_m', 'corner_radius_m', 'ring_width_m')
_HEX = re.compile(r'^#[0-9a-fA-F]{6}$')


def _down4(v):
    """v rounded down to 4 decimals: a recorded default that is the largest value a limit allows must not exceed the limit."""
    return math.floor(v * 1e4 + 1e-9) / 1e4


def _depth_inside(points, outline):
    """How far the points lie inside a convex open outline (an arch from _arch_path; its closing bottom edge is not a side): the
    least distance to the lines of its edges, negative when a point is outside. The outline is what is built, with its chords."""
    worst = math.inf
    for px, pz in points:
        for (x0, z0), (x1, z1) in zip(outline, outline[1:]):
            length = math.hypot(x1 - x0, z1 - z0)
            if length > 1e-12:
                worst = min(worst, ((px - x0) * (z1 - z0) - (pz - z0) * (x1 - x0)) / length)
    return worst


def _dedupe(points, tol=1e-9):
    out = []
    for q in points:
        if not out or math.dist(out[-1], q) > tol:
            out.append(q)
    if len(out) > 1 and math.dist(out[0], out[-1]) <= tol:
        out.pop()
    return out


def _arch_path(a, zb, zt, r, n=LIT_ARC):
    """The open outline of an arch: from (-a, zb) up the left side, over the two top corners (radius r) and down to (a, zb)."""
    r = min(r, a, zt - zb)
    pts = [(-a, zb)]
    if r < LIT_SQUARE:
        pts += [(-a, zt), (a, zt)]
    else:
        for sign in (-1, 1):
            for k in range(n + 1):
                ang = math.pi - math.pi / 2 * k / n if sign < 0 else math.pi / 2 - math.pi / 2 * k / n
                pts.append((sign * (a - r) + r * math.cos(ang), zt - r + r * math.sin(ang)))
    pts.append((a, zb))
    out = []
    for q in pts:
        if not out or math.dist(out[-1], q) > 1e-9:
            out.append(q)
    return out


def _band(outer, inner):
    """The strip between two nested arch outlines that start and end at the same height: an inverted U open at the bottom (or an
    arch with a notch cut up from its bottom edge). One simple outline, no hole."""
    return _dedupe(list(inner) + list(reversed(outer)))


def _lit_plan(params, W, T, H):
    """Resolve every value of a lit_panel (width W, thickness T, height H; None when the height is not known yet).
    -> (plan, defaults [(what, value)], problems [(path, message)]): the builder records the defaults as assumed."""
    problems, defaults = [], []
    plan = {'W': W, 'T': T, 'H': H, 'niche': None}

    def bad(path, message):
        problems.append((path, message))

    def take(source, key, default, ok, message, path, name=None):
        v = source.get(key)
        if v is None:
            if default is not None:
                defaults.append((name or key, default))
            return default
        if not _is_num(v) or not ok(float(v)):
            bad(path, message)
            return default
        return float(v)
    a0 = W / 2
    cap = f' and not above the panel height ({H:g} m)' if H is not None else ''
    r = take(params, 'corner_radius_m', _down4(min(LIT_RADIUS, a0, H if H is not None else a0)),
             lambda v: 0 <= v <= a0 + 1e-9 and (H is None or v <= H + 1e-9),
             f'the corner radius must lie between 0 and half the panel width ({a0:g} m){cap}', 'params.corner_radius_m')
    inset = take(params, 'inset_m', LIT_INSET, lambda v: v >= LIT_MIN_FEATURE,
                 f"inset_m (the groove's distance from the panel edge) must be at least {LIT_MIN_FEATURE:g} m", 'params.inset_m')
    gw = take(params, 'groove_width_m', LIT_GROOVE_WIDTH, lambda v: v >= LIT_MIN_FEATURE,
              f'groove_width_m must be at least {LIT_MIN_FEATURE:g} m', 'params.groove_width_m')
    gd = take(params, 'groove_depth_m', LIT_GROOVE_DEPTH, lambda v: LIT_MIN_FEATURE <= v <= T - LIT_MIN_SLAB + 1e-9,
              f'groove_depth_m must lie between {LIT_MIN_FEATURE:g} m and the panel thickness less {LIT_MIN_SLAB:g} m ({T - LIT_MIN_SLAB:g} m): '
              'the groove has a floor and does not go through', 'params.groove_depth_m')
    light = params.get('light_hex')
    if light is None:
        light = LIT_LIGHT_HEX
        defaults.append(('light_hex', light))
    elif not isinstance(light, str) or not _HEX.match(light):
        bad('params.light_hex', 'light_hex must be a colour like #ffe2a8')
        light = LIT_LIGHT_HEX
    ci = inset + gw
    core_ok = True
    if ci > a0 - LIT_MIN_CORE / 2 + 1e-9:
        bad('params.inset_m', f'inset_m + groove_width_m ({ci:g} m) must stay under half the panel width ({a0:g} m) by at least '
                              f'{LIT_MIN_CORE / 2:g} m: the groove would leave no panel core')
        core_ok = False
    elif H is not None and H - ci < LIT_MIN_CORE:
        bad('params.inset_m', f'inset_m + groove_width_m ({ci:g} m) leave no panel core under the top of the {H:g} m panel')
        core_ok = False
    plan.update(radius=r, inset=inset, gw=gw, gd=gd, light=light, ci=ci)
    niche = params.get('niche')
    if niche is None or not core_ok:
        return plan, defaults, problems
    ac = a0 - ci
    ztc = None if H is None else H - ci
    rc = max(0.0, r - ci)
    plan.update(ac=ac, ztc=ztc, rc=rc)
    rw = take(niche, 'ring_width_m', LIT_RING_WIDTH, lambda v: v >= LIT_MIN_FEATURE,
              f'ring_width_m must be at least {LIT_MIN_FEATURE:g} m', 'params.niche.ring_width_m', 'niche.ring_width_m')
    width = take(niche, 'width_m', _down4(min(0.9, ac)), lambda v: v >= LIT_MIN_OPENING,
                 f'width_m must be at least {LIT_MIN_OPENING:g} m', 'params.niche.width_m', 'niche.width_m')
    hn = take(niche, 'height_m', None if ztc is None else _down4(min(1.4, 0.45 * ztc)), lambda v: v >= LIT_MIN_OPENING,
              f'height_m must be at least {LIT_MIN_OPENING:g} m', 'params.niche.height_m', 'niche.height_m')
    bn = take(niche, 'bottom_m', None if H is None else round(max(0.3 * H, rw + LIT_MIN_BASE + 0.01), 4), lambda v: v > 0,
              'bottom_m must be a positive number of metres', 'params.niche.bottom_m', 'niche.bottom_m')
    depth = take(niche, 'depth_m', _down4(min(0.10, T - 2 * LIT_MIN_WALL)), lambda v: LIT_MIN_FEATURE <= v <= T - LIT_MIN_WALL + 1e-9,
                 f'depth_m must lie between {LIT_MIN_FEATURE:g} m and the panel thickness less {LIT_MIN_WALL:g} m ({T - LIT_MIN_WALL:g} m): '
                 'the niche has a back wall and does not go through the panel', 'params.niche.depth_m', 'niche.depth_m')
    nr = take(niche, 'corner_radius_m', _down4(min(width / 2, hn if hn is not None else width / 2)),
              lambda v: 0 <= v <= width / 2 + 1e-9 and (hn is None or v <= hn + 1e-9),
              f'the niche corner radius must lie between 0 and half its width ({width / 2:g} m) and not above its height',
              'params.niche.corner_radius_m', 'niche.corner_radius_m')
    for key in niche:
        if key not in LIT_NICHE_KEYS:
            bad(f'params.niche.{key}', f'unknown niche field; use {", ".join(LIT_NICHE_KEYS)}')
    if not problems:
        a = width / 2
        if a + rw + LIT_CLEAR > ac + 1e-9:
            bad('params.niche.width_m', f'the niche and its ring ({2 * (a + rw):g} m wide) must lie inside the panel core ({2 * ac:g} m wide, '
                                        f'inside the groove) with {LIT_CLEAR:g} m to spare')
        if bn is not None and bn - rw < LIT_MIN_BASE - 1e-9:
            bad('params.niche.bottom_m', f'bottom_m must be at least ring_width_m + {LIT_MIN_BASE:g} m ({rw + LIT_MIN_BASE:g} m): '
                                         'the niche stands on a solid base')
        if not problems and hn is not None and bn is not None and ztc is not None:
            top = bn + hn + rw
            if bn - rw > ztc - rc + 1e-9:
                bad('params.niche.bottom_m', 'the niche must start below the curved top of the panel core')
            else:
                # the core is built as a polyline (LIT_ARC chords per corner), so the ring is measured against those chords, not
                # against the exact arcs the chords are cut from
                worst = _depth_inside(_arch_path(a + rw, bn - rw, top, nr + rw), _arch_path(ac, bn - rw, ztc, rc))
                if worst < LIT_CLEAR - 1e-9:
                    bad('params.niche.height_m', f'the niche and its ring reach {top:g} m: they must lie inside the panel core '
                                                 f'(its top is {ztc:g} m) with {LIT_CLEAR:g} m to spare')
    plan['niche'] = dict(width=width, height=hn, bottom=bn, depth=depth, radius=nr, ring=rw)
    return plan, defaults, problems


FIT_TOL = 1e-6                 # how far a turned part may reach past the footprint before the builders refuse it (m)
SIDE_NAMES = ('front', 'back', 'left', 'right')


def _turned_extent(W, T, yaw_deg):
    """Full extent along X and along Y of a W x T rectangle turned by yaw_deg about its centre."""
    th = math.radians(yaw_deg)
    c, s = abs(math.cos(th)), abs(math.sin(th))
    return c * W + s * T, s * W + c * T


def _room(frame, p):
    """(x0, x1, y0, y1) a self-placing part may stand in: the footprint, or for a part placed against a side the space inside the inner
    faces of the closed walls (p.span, handed over by place_floor)."""
    return p.span or (frame.X0, frame.X1, frame.Y0, frame.Y1)


def _place_once(frame, p, ex, ey):
    """Centre of a part with half extents ex, ey (its turned outline), moved into its room once from where the compiler put it (the
    requested position: SELF_PLACING_KINDS are handed over unclamped) and recorded once through Frame.adjust. The caller has checked
    that the outline fits the room (_room); a part against a side stays inside the closed walls, flush with them like a box."""
    x0, x1, y0, y1 = _room(frame, p)
    lo_x, hi_x, lo_y, hi_y = x0 + ex, x1 - ex, y0 + ey, y1 - ey
    cx = (lo_x + hi_x) / 2 if lo_x > hi_x else min(max(p.x, lo_x), hi_x)       # only a rounding difference at the limit can be left
    cy = (lo_y + hi_y) / 2 if lo_y > hi_y else min(max(p.y, lo_y), hi_y)
    if math.hypot(cx - p.x, cy - p.y) > 0.005:
        frame.adjust(p.id, f'moved inside the footprint from ({p.x:.2f}, {p.y:.2f}) to ({cx:.2f}, {cy:.2f})')
    return cx, cy


def _widest_turned(T, yaw_deg, fw, fd):
    """Widest W so that a W x T rectangle turned by yaw_deg fits a fw x fd footprint, floored to 3 decimals; None when none does."""
    th = math.radians(yaw_deg)
    c, s = abs(math.cos(th)), abs(math.sin(th))
    best = math.inf
    for limit, along, across in ((fw, c, s), (fd, s, c)):
        room = limit - across * T
        if room < -FIT_TOL:
            return None
        if along > 1e-9:
            best = min(best, room / along)
    if not math.isfinite(best):
        return None
    best = math.floor(best * 1000) / 1000
    return best if best > 0 else None


def _is_cut_to_side(el):
    """A part placed against a side (no centre_m) is cut to the length of that side."""
    return el.get('side') in SIDE_NAMES and not el.get('centre_m')


def _placed_width(W, el, form):
    """The width the compiler leaves a part: placed against a side it is cut to the length of that side between the end walls, which
    stand on the closed sides (form: wall_thickness_m, the layout's value or the compiler's default)."""
    if not _is_cut_to_side(el):
        return W
    side = el.get('side')
    fw, fd = (round(float(v), 4) for v in form['footprint_m'])
    ends = ('left', 'right') if side in ('front', 'back') else ('front', 'back')
    closed = [e for e in ends if e not in (form.get('open_sides') or [])]
    wall = form.get('wall_thickness_m')
    wall = float(wall) if _is_num(wall) else cm.PRIOR['wall_thickness']
    return min(W, (fw if side in ('front', 'back') else fd) - len(closed) * wall)


def _room_dims(el, form):
    """Width and depth of the room a part may stand in: the footprint, or for a part placed against a side (no centre_m) the space inside
    the inner faces of the closed walls (an open side is bounded by the footprint edge)."""
    fw, fd = (round(float(v), 4) for v in form['footprint_m'])
    if not _is_cut_to_side(el):
        return fw, fd
    wall = form.get('wall_thickness_m')
    wall = float(wall) if _is_num(wall) else cm.PRIOR['wall_thickness']
    closed = [q for q in SIDE_NAMES if q not in (form.get('open_sides') or [])]
    return fw - wall * sum(q in closed for q in ('left', 'right')), fd - wall * sum(q in closed for q in ('front', 'back'))


def _fit_problem(noun, W, T, el, form, yaws):
    """None, or the message that a W x T part, turned the way the compiler will turn it, cannot stand inside its room (_room_dims).
    yaws: the turns the compiler can give it. It is refused only when none of them fits; the builder drops a part that does not fit
    and the check must refuse exactly what the build would drop."""
    fw, fd = _room_dims(el, form)
    room = 'room between the walls' if _is_cut_to_side(el) else 'footprint'
    first = None
    for yaw in yaws:
        ex, ey = _turned_extent(W, T, yaw)
        if ex <= fw + FIT_TOL and ey <= fd + FIT_TOL:
            return None
        first = first or (yaw, ex, ey)
    yaw, ex, ey = first
    widest = _widest_turned(T, yaw, fw, fd)
    turn = f' turned {yaw:g} degrees' if yaw % 180 else ''
    fix = (f'; at most {widest:.3f} m wide fits, or turn it less' if widest
           else f'; not even a narrower {noun} fits, so make it thinner or turn it less')
    return f'the {W:g} x {T:g} m {noun}{turn} needs {ex:.3f} x {ey:.3f} m but the {room} is {fw:g} x {fd:g} m{fix}'


def _lit_yaws(el, form):
    """The turn the compiler gives a lit_panel: its yaw_deg, or the facing it derives (against a side or with centre_m on a side: out
    of an open side, into a closed one; at the centre: out of the entrance side, which the form carries)."""
    yaw = el.get('yaw_deg')
    if _is_num(yaw):
        return [float(yaw)]
    side = el.get('side')
    if side in SIDE_NAMES:
        return [YAW_OUT[side] if side in (form.get('open_sides') or []) else YAW_IN[side]]
    entrance = form.get('entrance_side')
    if entrance not in SIDE_NAMES:
        opened = [s for s in SIDE_NAMES if s in (form.get('open_sides') or [])]
        entrance = opened[0] if opened else 'front'
    return [YAW_OUT[entrance]]


def check_lit_panel(el, form):
    """PARAM_CHECKS entry: [(path inside the element, message)] for a lit_panel element and the design's form."""
    default = DEFAULT_SIZE['lit_panel'][0]
    given = (list(el.get('size_m') or []) + [None] * 3)[:3]
    problems = []
    size = []
    for v, d, name in zip(given, default, ('width', 'thickness', 'height')):
        if v is None:
            size.append(d)
        elif _is_num(v) and v > 0:
            size.append(float(v))
        else:
            problems.append(('size_m', f'the {name} of the panel must be a positive number of metres'))
            size.append(d)
    if problems:
        return problems
    W, T, H = size
    if T < LIT_MIN_THICK:
        problems.append(('size_m', f'the panel must be at least {LIT_MIN_THICK:g} m thick to hold a groove'))
    W = _placed_width(W, el, form)                   # against a side the build first cuts the width to the side
    message = _fit_problem('panel', W, T, el, form, _lit_yaws(el, form))      # the build drops a panel that does not fit, turned as it is
    if message:
        problems.append(('size_m', message))
    if H is not None:
        room = form['height_limit_m'] - (form.get('platform_height_m') or 0.0) - (el['z_m'] if _is_num(el.get('z_m')) else 0.0)
        H = min(H, room)             # a panel above the limit is cut to it (and recorded); the rest is checked at that height
        if H < 0.2:
            problems.append(('size_m', 'the panel has less than 0.2 m between its base and the height limit'))
    if problems:
        return problems
    return _lit_plan(el.get('params') or {}, W, T, H)[2]


def _hex_rgba(hex_color):
    h = hex_color.lstrip('#')
    return [round(int(h[i:i + 2], 16) / 255, 4) for i in (0, 2, 4)] + [1.0]


def lit_panel(b, p, scope, note, frame):
    eid, el = p.id, p.el
    params = el.get('params') or {}
    W = p.wx if p.axis == 'y' else p.dy
    T = p.dy if p.axis == 'y' else p.wx
    h = p.z1 - p.z0
    if h < 0.2:
        raise ValueError('a lit_panel needs at least 0.2 m between its base and the height limit')
    plan, defaults, problems = _lit_plan(params, W, T, h)
    if T < LIT_MIN_THICK:
        problems.append(('size_m', f'the panel must be at least {LIT_MIN_THICK:g} m thick to hold a groove'))
    if problems:
        raise ValueError('; '.join(f'{path}: {message}' for path, message in problems))
    # the panel stays inside the footprint also when it is turned; it is moved in once, from where it was asked to stand
    th = math.radians(p.yaw)
    ex = (abs(math.cos(th)) * W + abs(math.sin(th)) * T) / 2
    ey = (abs(math.sin(th)) * W + abs(math.cos(th)) * T) / 2
    x0, x1, y0, y1 = _room(frame, p)
    if 2 * ex > x1 - x0 + FIT_TOL or 2 * ey > y1 - y0 + FIT_TOL:
        raise ValueError(f'the panel ({W:g} x {T:g} m, turned {p.yaw:g} degrees) needs {2 * ex:.3f} x {2 * ey:.3f} m and does not fit its room '
                         f'({x1 - x0:.3f} x {y1 - y0:.3f} m)')
    cx, cy = _place_once(frame, p, ex, ey)
    sin, cos = math.sin(th), math.cos(th)
    for what, value in defaults:
        frame.assume(eid, what, value)
    asked = (list(el.get('size_m') or []) + [None] * 3)[2]
    if not _is_num(asked) or asked <= 0:
        frame.assume(eid, 'height_m', round(h, 4), 'choice')
    elif asked > h + 1e-6:
        frame.adjust(eid, f'height cut from {asked:g} m to {h:.3f} m by the height limit')
    if ((el.get('text') or {}).get('body') or '').strip():
        frame.warn(f'{eid}: a lit_panel carries no text; its text.body is not built')
    body = dict(roughness=p.mat['roughness'], metallic=p.mat['metallic'], emission=p.mat['emission'])
    light_color = _hex_rgba(plan['light'])
    glow = dict(roughness=0.4, metallic=0.0, emission=LIT_EMISSION)

    def part(suffix, role, poly, d0, d1, color, **material):
        """A prism whose outline is `poly` (u across the panel, z above its base) and which reaches from d0 to d1 behind the panel face."""
        nm = -T / 2 + (d0 + d1) / 2
        loc = [cx - nm * sin, cy + nm * cos, p.z0 + h / 2]
        profile = [[round(u, 6), round(z - h / 2, 6)] for u, z in _dedupe(poly)]
        c = b.add(f'{eid}_{suffix}', scope, 'profile_extrusion', role, loc, {'profile': profile, 'depth': round(d1 - d0, 6)}, color,
                  rot=(0, 0, p.yaw), inferred=True, note=note, **material)
        c['location'] = [round(v, 6) for v in loc]               # add() rounds to 4 decimals: keep the joints exact
        return c
    a0, R, i, gw, gd = W / 2, plan['radius'], plan['inset'], plan['gw'], plan['gd']
    outline = _arch_path(a0, 0.0, h, R)
    groove_out = _arch_path(a0 - i, 0.0, h - i, max(0.0, R - i))
    groove_in = _arch_path(a0 - i - gw, 0.0, h - i - gw, max(0.0, R - i - gw))
    color = p.mat['color']
    with b.grouped(eid):
        part('frame', 'lit_panel', _band(outline, groove_out), 0.0, T, color, **body)
        part('groove_base', 'lit_panel', _band(groove_out, groove_in), gd, T, color, **body)
        part('groove_light', 'lit_groove', _band(groove_out, groove_in), gd - LIT_PROUD, gd, light_color, **glow)
        n = plan['niche']
        if n is None:
            part('core', 'lit_panel', groove_in, 0.0, T, color, **body)
            return
        ac, ztc, rc = plan['ac'], plan['ztc'], plan['rc']
        a, hn, bn, D, nr, rw = n['width'] / 2, n['height'], n['bottom'], n['depth'], n['radius'], n['ring']
        zc = bn - rw                                            # the ring bar starts here: below it the core is one block
        rd = min(LIT_RING_DEPTH, D / 2)
        opening = _arch_path(a, bn, bn + hn, nr)
        ring_clip = _arch_path(a + rw, bn, bn + hn + rw, nr + rw)
        ring_full = _arch_path(a + rw, zc, bn + hn + rw, nr + rw)
        core = _arch_path(ac, zc, ztc, rc)
        bar = [(-(a + rw), zc), (a + rw, zc), (a + rw, bn), (-(a + rw), bn)]
        part('core_base', 'lit_panel', [(-ac, 0.0), (ac, 0.0), (ac, zc), (-ac, zc)], 0.0, T, color, **body)
        part('core', 'lit_panel', _band(core, ring_full), 0.0, T, color, **body)
        part('ring_bar_light', 'lit_niche_light', bar, 0.0, rd, light_color, **glow)
        part('ring_light', 'lit_niche_light', _band(ring_clip, opening), 0.0, rd, light_color, **glow)
        part('ring_bar_back', 'lit_panel', bar, rd, T, color, **body)
        part('ring_back', 'lit_panel', _band(ring_clip, opening), rd, T, color, **body)
        part('niche_back', 'lit_niche', opening, D, T, color, **body)


BUILDERS['lit_panel'] = lit_panel
PARAM_CHECKS['lit_panel'] = check_lit_panel
ROLE['lit_panel'] = 'dominant'


# ------------------------------------------------------------------ prop_block
# A box that stands in for an object that is not modelled (a laptop on a table, a plant, a robot arm): the right size in the right
# place, nothing more. One element is one object and one box; the label (a closed list) only goes into the part's note. No shape is
# drawn: no open lid, no stand, no leaves. A reconstruction scores such a block by its place and size, never by its outline.
PROP_LABELS = ('laptop', 'tablet', 'monitor', 'phone', 'headphone', 'plant', 'robot_arm', 'robot', 'other')
PROP_LABEL_DEFAULT = 'other'
PROP_MIN_SIZE = 0.001          # least width, depth and height of a block (m)
PROP_MIN_ROOM = 0.01           # least height of a block between its base and the height limit (m)


def _prop_yaws(el):
    """The turn of a block: its yaw_deg, or none (an axis aligned box: the width runs along the side, along X at the centre)."""
    if _is_num(el.get('yaw_deg')):
        return [float(el['yaw_deg'])]
    return [90.0 if el.get('side') in ('left', 'right') else 0.0]


def check_prop_block(el, form):
    """PARAM_CHECKS entry: [(path inside the element, message)] for a prop_block element and the design's form."""
    problems = []
    label = (el.get('params') or {}).get('label')
    if label is not None and not (isinstance(label, str) and label in PROP_LABELS):
        problems.append(('params.label', f'label must be one of {", ".join(PROP_LABELS)}'))
    given = (list(el.get('size_m') or []) + [None] * 3)[:3]
    size = []
    for v, d, name in zip(given, DEFAULT_SIZE['prop_block'][0], ('width', 'depth', 'height')):
        if v is None:
            size.append(d)
        elif _is_num(v) and v >= PROP_MIN_SIZE:
            size.append(float(v))
        else:
            problems.append(('size_m', f'the {name} of the block must be at least {PROP_MIN_SIZE:g} m'))
            size.append(d)
    z = el.get('z_m')
    base = form.get('platform_height_m') or 0.0
    if z is not None and not (_is_num(z) and z >= 0):
        problems.append(('z_m', 'z_m (the height of the block above the floor, on a table top or a shelf) must be zero or more'))
    else:
        room = form['height_limit_m'] - base - (z or 0.0)
        if room < PROP_MIN_ROOM - 1e-9:
            problems.append(('z_m', f'z_m {(z or 0.0):g} m leaves {room:.3f} m between the base of the block and the height limit '
                                    f'{form["height_limit_m"]:g} m; z_m may be at most {form["height_limit_m"] - base - PROP_MIN_ROOM:.3f} m'))
    if not problems:
        message = _fit_problem('block', _placed_width(size[0], el, form), size[1], el, form, _prop_yaws(el))
        if message:
            problems.append(('size_m', message))
    return problems


def prop_block(b, p, scope, note, frame):
    eid, el = p.id, p.el
    label = (el.get('params') or {}).get('label')
    if label is not None and not (isinstance(label, str) and label in PROP_LABELS):
        raise ValueError(f'params.label: label must be one of {", ".join(PROP_LABELS)}')
    # the same refusals as check_prop_block: the compiler turns a missing or non-positive size into the default, so the element is read
    z = el.get('z_m')
    if z is not None and not (_is_num(z) and z >= 0):
        raise ValueError('z_m (the height of the block above the floor) must be zero or more')
    given = (list(el.get('size_m') or []) + [None] * 3)[:3]
    for v, name in zip(given, ('width', 'depth', 'height')):
        if v is not None and not (_is_num(v) and v >= PROP_MIN_SIZE):
            raise ValueError(f'size_m: the {name} of the block must be at least {PROP_MIN_SIZE:g} m')
    h = p.z1 - p.z0                                    # the block's own height, cut to the limit
    if frame.H - p.z0 < PROP_MIN_ROOM - 1e-9:          # the room above its base, not its own height: a phone lying flat is a few mm thick
        raise ValueError(f'a prop_block needs at least {PROP_MIN_ROOM:g} m between its base and the height limit')
    if _is_num(el.get('yaw_deg')):                     # turned: the width runs along the block's own X axis
        sx, sy = (p.wx, p.dy) if p.axis == 'y' else (p.dy, p.wx)
        turn = float(el['yaw_deg'])
        ex, ey = (v / 2 for v in _turned_extent(sx, sy, turn))
    else:                                              # not turned: an axis aligned box
        sx, sy, turn = p.wx, p.dy, 0.0
        ex, ey = sx / 2, sy / 2
    x0, x1, y0, y1 = _room(frame, p)
    if 2 * ex > x1 - x0 + FIT_TOL or 2 * ey > y1 - y0 + FIT_TOL:
        raise ValueError(f'the block ({sx:g} x {sy:g} m, turned {turn:g} degrees) needs {2 * ex:.3f} x {2 * ey:.3f} m and does not fit its room '
                         f'({x1 - x0:.3f} x {y1 - y0:.3f} m)')
    cx, cy = _place_once(frame, p, ex, ey)             # moved in once, from where it was asked to stand
    if label is None:
        label = frame.assume(eid, 'label', PROP_LABEL_DEFAULT)
    if not _is_num(z):
        frame.assume(eid, 'z_m', 0.0)
    asked = given[2]
    if not _is_num(asked) or asked <= 0:
        asked = DEFAULT_SIZE['prop_block'][0][2]
    if asked > h + 1e-6:
        frame.adjust(eid, f'height cut from {asked:g} m to {h:.3f} m by the height limit')
    if ((el.get('text') or {}).get('body') or '').strip():
        frame.warn(f'{eid}: a prop_block carries no text; its text.body is not built')
    if (el.get('count') or 1) > 1:
        frame.warn(f'{eid}: a prop_block is one object; its count is not used (one element for each object)')
    text = (f'{note} Prop block (label: {label}): stands in for an object that is not modelled; only its size and place count, '
            'not its shape.')
    with b.grouped(eid):
        b.add(eid, scope, 'box', 'prop_block', [cx, cy, p.z0 + h / 2], {'size': [round(sx, 4), round(sy, 4), round(h, 4)]}, p.mat['color'],
              rot=(0, 0, turn), inferred=True, note=text, **_mat(p.mat))


BUILDERS['prop_block'] = prop_block
PARAM_CHECKS['prop_block'] = check_prop_block
ROLE['prop_block'] = 'secondary'
