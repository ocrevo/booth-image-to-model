"""Compile a booth design (design.json: metres, semantic elements, a style, the designer's brief) into a booth-spec-v2 spec.

    python design_compile.py --design design.json --out out_dir [--canvas 1280x800] [--fit box_fit.json --image picture.jpg]

The design route makes a NEW booth from a reference picture and a brief; nothing is measured from the picture. The facts are
the designer's form (footprint, height limit, open sides). Every other value in design.json says where it comes from:
brief (the designer said so), reference (taken from the picture's style or layout), prior (a generic default) or choice (a
design decision). The compiler builds walls on the closed sides only, places each element from its side / along / offset
words, synthesises a three-quarter hero camera from the entrance, writes a neutral canvas picture as the spec's source (the
renderer needs one; it is not a photograph), validates the spec, runs the advisory design checks and the brief checks, and
writes spec.json, design_canvas.png, compile_report.json and design_context.json. Nothing is written until the whole compile
has run. No model call happens here.

With --fit and --image (always together) the spec is made for a real picture instead: the camera is copied from the corner fit
(box_fit.json, status fitted) field by field, the footprint is the fit's W x D (recorded as an uncertainty), the floor height is the
fit's floor_z, and the spec's source is a copy of the picture (no design_canvas.png), so the render can be laid over the picture.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import inspect
import io
import json
import math
import re
from pathlib import Path

import camera_math as cmath
import components as cm
import motifs as mt
from design_check import run as design_check_run, world_bounds
from draft_compile import expected_booth_type, outside_footprint, separate_coplanar
from geometry import mesh_for
from validate_spec import validate

SCHEMA_PATH = Path(__file__).resolve().parents[1] / 'schemas' / 'booth-design.v1.json'
SIDES = ('front', 'back', 'left', 'right')
ID_RE = re.compile(r'^[a-z][a-z0-9_]*$')
HEX_RE = re.compile(r'^#[0-9a-fA-F]{6}$')
SOURCES = ('brief', 'reference', 'prior', 'choice')
FLOOR_T = 0.05
CARPET = [0.30, 0.30, 0.31, 1]
OUTWARD = {'front': (0.0, -1.0), 'back': (0.0, 1.0), 'left': (-1.0, 0.0), 'right': (1.0, 0.0)}
ROLE = {'feature_wall': 'secondary', 'graphic_panel': 'accent', 'tv': 'secondary', 'led_wall': None, 'counter': 'secondary',
        'round_table': 'dominant', 'chair': 'accent', 'seating_group': 'dominant', 'planter': 'dominant',
        'display_plinth': 'secondary', 'storage_block': 'secondary', 'partition': 'dominant', 'box': 'secondary',
        'header_band': 'secondary', 'ring': 'secondary', 'spotlights': None, 'wall': 'dominant'}
RESERVED = ('floor', 'perimeter_')


class DesignError(ValueError):
    def __init__(self, errors):
        super().__init__('; '.join(f"{e['code']} {e['where']}: {e['message']}" for e in errors[:5]))
        self.errors = errors


def load_schema():
    return json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))


def _rgba(hex_color, alpha=1.0):
    h = hex_color.lstrip('#')
    return [round(int(h[i:i + 2], 16) / 255, 4) for i in (0, 2, 4)] + [alpha]


def _takes_frame(builder):
    """True when a floor or wall builder has room for a fifth positional argument, the read only mt.Frame."""
    params = list(inspect.signature(builder).parameters.values())
    return any(p.kind == p.VAR_POSITIONAL for p in params) or len(
        [p for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]) >= 5


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


# ------------------------------------------------------------------ validation
def validate_design(design):
    """-> [{code, where, message}] (empty when the design can be compiled)."""
    from jsonschema import Draft202012Validator
    errors = [{'code': 'SCHEMA', 'where': '/'.join(map(str, e.absolute_path)) or '(root)', 'message': e.message}
              for e in sorted(Draft202012Validator(load_schema()).iter_errors(design), key=lambda e: list(map(str, e.absolute_path)))]
    if errors:
        return errors

    def err(code, where, message):
        errors.append({'code': code, 'where': where, 'message': message})
    form = design['form']
    W, D = form['footprint_m']
    if W <= 0.5 or D <= 0.5 or W > 60 or D > 60:
        err('FOOTPRINT', 'form.footprint_m', 'width and depth must lie between 0.5 and 60 m')
    if not 1.5 <= form['height_limit_m'] <= 20:
        err('HEIGHT_LIMIT', 'form.height_limit_m', 'the height limit must lie between 1.5 and 20 m')
    opened = form['open_sides']
    if len(set(opened)) != len(opened):
        err('TOPOLOGY', 'form.open_sides', 'list each side at most once')
    bt = design['booth_type']
    if bt != 'other' and expected_booth_type(opened) != bt:
        err('TOPOLOGY', 'booth_type', f'{bt!r} does not match open sides {sorted(set(opened))}: they make a '
                                      f'{expected_booth_type(opened)!r} booth (or say other)')
    brief = design['brief']['text']
    platform = (design.get('layout') or {}).get('platform_height_m')
    layout = design.get('layout') or {}
    wall = layout.get('wall_thickness_m')
    open_in_order = [s for s in SIDES if s in opened]
    entrance = layout.get('entrance_side') if layout.get('entrance_side') in open_in_order else (open_in_order[0] if open_in_order else 'front')
    # what the PARAM_CHECKS see: the form, the floor top, and the wall thickness and entrance side the compiler will use
    checks_form = dict(form, platform_height_m=float(platform) if _num(platform) else 0.0,
                       wall_thickness_m=float(wall) if _num(wall) else cm.PRIOR['wall_thickness'], entrance_side=entrance)
    ids = []
    for i, el in enumerate(design['elements']):
        where = f'elements[{i}]'
        eid = el['id']
        if eid in ids:
            err('ID', where + '.id', f'duplicate element id {eid!r}')
        ids.append(eid)
        if eid == 'floor' or any(eid.startswith(r) for r in RESERVED[1:]):
            err('ID', where + '.id', f'{eid!r} is reserved for the floor and the perimeter walls')
        kind = el['kind']
        side = el.get('side')
        if kind in mt.WALL_KINDS and side not in SIDES:
            err('SIDE', where + '.side', f'a {kind} hangs on a wall: give side front, back, left or right')
        if kind == 'tv' and (el.get('params') or {}).get('inch') not in (None, *cm.TV):
            err('PARAM', where + '.params.inch', f'tv size class must be one of {sorted(cm.TV)}')
        check = mt.PARAM_CHECKS.get(kind)
        if check:
            for path, message in check(el, checks_form):
                err('PARAM', f'{where}.{path}', message)
        prov = el.get('provenance') or {}
        body = ((el.get('text') or {}).get('body') or '').strip()
        if body:
            if prov.get('text') != 'brief':
                err('TEXT_PROVENANCE', where + '.text', 'text may only come from the brief: set provenance.text to "brief"')
            elif body not in brief:
                err('TEXT_PROVENANCE', where + '.text.body', f'{body!r} does not appear in the brief text; text is never invented '
                                                             'or copied from the picture')
    for a in ids:
        for b in ids:
            if a != b and b.startswith(a + '_'):
                err('ID', 'elements', f'{b!r} starts with {a + "_"!r}: the parts of {a!r} use that prefix')
    for i, r in enumerate(design.get('requirements') or []):
        if r['kind'] in ('must_have', 'must_not', 'count') and r.get('element_kind') not in mt.KINDS:
            err('REQUIREMENT', f'requirements[{i}].element_kind', f'use one of the element kinds: {", ".join(mt.KINDS)}')
        if r['kind'] == 'text' and not (r.get('text') or '').strip():
            err('REQUIREMENT', f'requirements[{i}].text', 'a text requirement needs the text')
        if r['kind'] == 'count' and r.get('count') is None:
            err('REQUIREMENT', f'requirements[{i}].count', 'a count requirement needs the count')
    return errors


# ------------------------------------------------------------------ the compiler
class DesignCompiler:
    def __init__(self, design, canvas_png, canvas_size, fit=None, source=None):
        self.d = design
        self.fit = fit
        form = design['form']
        self.W, self.D = round(float(form['footprint_m'][0]), 4), round(float(form['footprint_m'][1]), 4)
        self.H = float(form['height_limit_m'])
        self.X0, self.X1, self.Y0, self.Y1 = -self.W / 2, self.W / 2, -self.D / 2, self.D / 2
        self.open = [s for s in SIDES if s in form['open_sides']]
        self.closed = [s for s in SIDES if s not in self.open]
        self.hanging = form.get('hanging_allowed')
        self.iw, self.ih = canvas_size
        name, digest = source if source else ('design_canvas.png', hashlib.sha256(canvas_png).hexdigest())
        self.b = cm.SpecBuilder(name, self.iw, self.ih, design['project_id'], sha256=digest)
        self.report = {'elements': [], 'assumed': [], 'choices': [], 'adjustments': [], 'dropped': [], 'warnings': []}
        self.placed = {}
        style = design['style']
        self.palette = {'dominant': _rgba(style['dominant_hex']), 'secondary': _rgba(style['secondary_hex']),
                        'accent': _rgba(style['accent_hex'])}

    # -- bookkeeping
    def assume(self, eid, what, value, source='prior'):
        (self.report['assumed'] if source == 'prior' else self.report['choices']).append({'id': eid, 'what': what, 'value': value})
        return value

    def adjust(self, eid, what):
        self.report['adjustments'].append({'id': eid, 'what': what})

    def warn(self, message):
        self.report['warnings'].append(message)

    # -- layout values
    def layout(self):
        lay = self.d.get('layout') or {}
        zf = lay.get('platform_height_m')
        self.zf = float(zf) if _num(zf) else 0.0
        if self.fit:
            self.zf = float(self.fit.get('floor_z') or 0.0)           # the picture's floor level, not a design choice
        t = lay.get('wall_thickness_m')
        self.t = float(t) if _num(t) else self.assume('layout', 'wall_thickness_m', cm.PRIOR['wall_thickness'])
        top = lay.get('wall_height_m')
        if _num(top):
            self.wall_top = min(float(top), self.H)
            if top > self.H:
                self.adjust('layout', f'wall height {top:g} m cut to the height limit {self.H:g} m')
        else:
            self.wall_top = self.assume('layout', 'wall_height_m', round(min(self.H - 0.1, 3.0), 3))
        entrance = lay.get('entrance_side')
        if entrance in self.open:
            self.entrance = entrance
        else:
            self.entrance = self.open[0] if self.open else 'front'
            self.assume('layout', 'entrance_side', self.entrance, 'choice')
        self.walls = {}

    def edge(self, side):
        """-> (plane axis, outer coordinate, inward sign, (along_lo, along_hi) between the walls)."""
        tl = self.t if 'left' in self.closed else 0.0
        tr = self.t if 'right' in self.closed else 0.0
        tf = self.t if 'front' in self.closed else 0.0
        tb = self.t if 'back' in self.closed else 0.0
        return {'back': ('y', self.Y1, -1, (self.X0 + tl, self.X1 - tr)), 'front': ('y', self.Y0, 1, (self.X0 + tl, self.X1 - tr)),
                'left': ('x', self.X0, 1, (self.Y0 + tf, self.Y1 - tb)), 'right': ('x', self.X1, -1, (self.Y0 + tf, self.Y1 - tb))}[side]

    def inner_face(self, side):
        axis, outer, sign, _ = self.edge(side)
        return outer + sign * (self.t if side in self.closed else 0.0)

    # -- materials
    def look(self, el, kind):
        m = el.get('material') or {}
        role = m.get('role') or (ROLE[kind] if kind in ROLE else mt.ROLE.get(kind))
        if m.get('color_hex'):
            color = _rgba(m['color_hex'])
        elif role:
            color = list(self.palette[role])
        else:
            color = list(cm.DARK_SCREEN)
        rough, metal, alpha = mt.FINISH.get(m.get('finish') or 'satin')
        color[3] = alpha
        emission = 1.5 if m.get('emissive') else 0.0
        accent = self.palette['accent']
        text_color = list(self.palette['dominant'] if color[:3] == accent[:3] else accent)
        other = self.palette['dominant'] if role != 'dominant' else self.palette['secondary']
        return {'color': color, 'roughness': rough, 'metallic': metal, 'emission': emission, 'text_color': text_color,
                'top_color': list(other), 'backing_color': list(self.palette['dominant']), 'chair_color': list(accent)}

    # -- floor and walls
    def floor(self):
        style = self.d['style']
        finish = style.get('floor_finish') or 'carpet'
        if style.get('floor_hex'):
            color = _rgba(style['floor_hex'])
        else:
            color = {'carpet': CARPET, 'timber': cm.WOOD, 'vinyl': [0.78, 0.78, 0.77, 1], 'tile': [0.82, 0.81, 0.79, 1],
                     'platform': self.palette['secondary']}.get(finish, CARPET)
        z0 = 0.0 if self.zf > 0 else -FLOOR_T
        part = self.b.box('floor', 'booth_floor', 'floor', (self.X0, self.X1), (self.Y0, self.Y1), (z0, self.zf), list(color),
                          critical=True, inferred=True,
                          note=f'Floor of the stated {self.W:g} x {self.D:g} m footprint (form: brief); finish {finish}.',
                          roughness=0.9 if finish == 'carpet' else 0.5)
        if style.get('floor_pattern') is not None:
            part['material']['pattern'] = copy.deepcopy(style['floor_pattern'])      # as it is, even {}: validate_spec decides

    def build_walls(self, overrides):
        for side in self.closed:
            el = overrides.get(side)
            axis, outer, sign, (lo, hi) = self.edge(side)
            normal = tuple(sorted((outer, outer + sign * self.t)))
            if axis == 'y':
                xr, yr = (self.X0, self.X1), normal
            else:
                yr = (self.Y0 + (self.t if 'front' in self.closed else 0.0), self.Y1 - (self.t if 'back' in self.closed else 0.0))
                xr = normal
            top = self.wall_top
            mat = self.look(el or {}, 'wall')
            if el and _num((el.get('size_m') or [None, None, None])[2]):
                top = min(self.zf + el['size_m'][2], self.H)
            note = f'Wall on the closed {side} side (form: brief).'
            if el:
                note += ' ' + self.provenance_note(el)
            part = self.b.box(f'perimeter_wall_{side}', 'shell', 'back_wall' if side == 'back' else 'end_wall', xr, yr, (self.zf, top),
                              mat['color'], critical=True, inferred=True, note=note, roughness=mat['roughness'],
                              metallic=mat['metallic'], group=el['id'] if el else None)
            wall_pattern = ((el or {}).get('material') or {}).get('pattern')
            if wall_pattern is not None:
                part['material']['pattern'] = copy.deepcopy(wall_pattern)
            self.walls[side] = top

    # -- placement
    def size_of(self, el, kind):
        given = list(el.get('size_m') or [None, None, None]) + [None] * 3
        given = given[:3]
        default, source = mt.DEFAULT_SIZE.get(kind, ([None, None, None], 'choice'))
        out = []
        for i, axis in enumerate(('width_m', 'depth_m', 'height_m')):
            v = given[i]
            if _num(v) and v > 0:
                out.append(float(v))
            else:
                out.append(None if default[i] is None else self.assume(el['id'], axis, default[i], source))
        return out

    def provenance_note(self, el):
        prov = el.get('provenance') or {}
        parts = [f"{k} {prov.get(k) or 'choice'}" for k in ('presence', 'placement', 'size', 'material')]
        if (el.get('text') or {}).get('body'):
            parts.append('text brief')
        return f"Design element {el['id']!r} ({el['kind']}): " + '; '.join(parts) + '. Generated design, not observed.'

    def along_span(self, el, lo, hi, w):
        a = el.get('along')
        if not _num(a):
            a = 0.5
        if w > hi - lo:
            self.adjust(el['id'], f'{w:.2f} m is longer than the side ({hi - lo:.2f} m); cut to fit')
            w = hi - lo
        u = lo + float(a) * (hi - lo)
        u = min(max(u, lo + w / 2), hi - w / 2)
        return u, w

    def place_wall(self, el, kind, size):
        side = el['side']
        axis, outer, sign, (lo, hi) = self.edge(side)
        face = self.inner_face(side)
        w, d, h = size
        top = self.walls.get(side, self.wall_top)
        if kind == 'feature_wall':
            w = w or (hi - lo)
            h = h or (top - self.zf)
            z0 = self.zf + (el.get('z_m') or 0.0)
        elif kind == 'tv':
            inch = int((el.get('params') or {}).get('inch') or self.assume(el['id'], 'inch', 55, 'choice'))
            pw, ph = cm.TV[inch]
            w, d, h = pw + 0.04, 0.05, ph + 0.04
            zc = el.get('z_m')
            centre = self.zf + (zc + h / 2 if _num(zc) else self.assume(el['id'], 'centre_height_m', cm.PRIOR['tv_centre']))
            z0 = centre - h / 2
        elif kind == 'led_wall':
            z0 = self.zf + (el['z_m'] if _num(el.get('z_m')) else self.assume(el['id'], 'bottom_m', 0.5, 'choice'))
        else:   # graphic_panel
            if _num(el.get('z_m')):
                z0 = self.zf + el['z_m']
            else:
                z0 = self.zf + max(0.8, cm.PRIOR['tv_centre'] - h / 2)
                self.assume(el['id'], 'bottom_m', round(z0 - self.zf, 3))
        z1 = z0 + h
        if z1 > top + 1e-6:
            self.adjust(el['id'], f'top cut from {z1:.2f} m to the wall top {top:.2f} m')
            z1 = top
        header = getattr(self, 'header', None)
        if header and z1 > header['bottom'] + 1e-6:
            if side in header['sides']:            # the band runs along this wall: stay under it
                self.adjust(el['id'], f"top cut to {header['bottom']:.2f} m, under the header band")
                z1 = header['bottom']
            ends = {'front': ('left', 'right'), 'back': ('left', 'right'), 'left': ('front', 'back'), 'right': ('front', 'back')}[side]
            if ends[0] in header['sides']:          # the band crosses this wall's end: leave its depth free
                lo += header['depth']
                self.adjust(el['id'], f"kept clear of the header band on the {ends[0]} side")
            if ends[1] in header['sides']:
                hi -= header['depth']
                self.adjust(el['id'], f"kept clear of the header band on the {ends[1]} side")
        if z1 <= z0 + 0.05:
            raise ValueError(f'does not fit under the {side} wall top or the header band')
        u, w = self.along_span(el, lo, hi, w if (el.get('size_m') or [None])[0] else min(w, hi - lo))
        n0, n1 = face, face + sign * d
        nc = (n0 + n1) / 2
        if axis == 'y':
            return mt.Placed(el, u, nc, w, d, z0, z1, mt.YAW_IN[side], self.look(el, kind), side, face, axis, sign)
        return mt.Placed(el, nc, u, d, w, z0, z1, mt.YAW_IN[side], self.look(el, kind), side, face, axis, sign)

    def footprint_of(self, el, kind, size):
        """-> (width along the side, depth away from it, height) for a floor element."""
        if kind == 'round_table':
            r = (el.get('params') or {}).get('radius_m') or self.assume(el['id'], 'radius_m', cm.PRIOR['table_radius_4p'])
            return 2 * r, 2 * r, size[2] or self.assume(el['id'], 'height_m', cm.PRIOR['table_height'])
        if kind == 'chair':
            return 0.56, 0.56, 0.8
        if kind == 'seating_group':
            n = int(el.get('count') or self.assume(el['id'], 'count', 2, 'choice'))
            r = (cm.PRIOR['table_radius_4p'] if n > 2 else 0.35) + 0.38 + 0.3
            return 2 * r, 2 * r, 0.8
        if kind == 'planter':
            n = int(el.get('count') or 1)
            pitch = el.get('pitch_m') or size[0] + 0.2
            el['_unit'] = size
            return (n - 1) * pitch + size[0], size[1], size[2]
        h = size[2] or (min(2.5, self.wall_top - self.zf) if kind == 'storage_block' else self.wall_top - self.zf)
        return size[0], size[1], h

    def place_floor(self, el, kind, size):
        w, d, h = self.footprint_of(el, kind, size)
        side = el.get('side') or 'centre'
        yaw = el.get('yaw_deg')
        z0 = self.zf + (el['z_m'] if _num(el.get('z_m')) else 0.0)
        if el.get('centre_m'):
            x, y = (float(v) for v in el['centre_m'])
            if side in SIDES:
                axis = self.edge(side)[0]
                facing = mt.YAW_OUT[side] if side in self.open else mt.YAW_IN[side]
            else:
                facing = mt.YAW_OUT[self.entrance]
                axis = 'x' if _num(yaw) and abs(abs(yaw) % 180 - 90) < 45 else 'y'
        elif side in SIDES:
            axis, outer, sign, (lo, hi) = self.edge(side)
            off = el.get('offset_m')
            if not _num(off):
                off = self.assume(el['id'], 'offset_m', 0.0 if side in self.closed else 0.3, 'choice')
            u, w = self.along_span(el, lo, hi, w)
            n = self.inner_face(side) + sign * (off + d / 2)
            x, y = (u, n) if axis == 'y' else (n, u)
            facing = mt.YAW_OUT[side] if side in self.open else mt.YAW_IN[side]
        else:
            a = el.get('along')
            x = self.X0 + (float(a) if _num(a) else 0.5) * self.W
            y = 0.0
            axis = 'y'
            facing = mt.YAW_OUT[self.entrance]
        if _num(yaw):
            facing = float(yaw)
        wx, dy = (w, d) if axis == 'y' else (d, w)
        span = None
        if kind in mt.SELF_PLACING_KINDS:         # the builder moves the part into its room once, from its turned outline, and records it
            if side in SIDES and not el.get('centre_m'):         # along_span clamped the along position for the unturned width: hand over the asked one
                u = lo + (float(el['along']) if _num(el.get('along')) else 0.5) * (hi - lo)
                x, y = (u, y) if axis == 'y' else (x, u)
                span = (*self.edge('front')[3], *self.edge('left')[3])      # and its room: inside the inner faces of the closed walls (x0, x1, y0, y1)
            nx, ny = x, y
        else:
            nx = min(max(x, self.X0 + wx / 2), self.X1 - wx / 2) if wx <= self.W else 0.0
            ny = min(max(y, self.Y0 + dy / 2), self.Y1 - dy / 2) if dy <= self.D else 0.0
            if math.hypot(nx - x, ny - y) > 0.005:
                self.adjust(el['id'], f'moved inside the footprint from ({x:.2f}, {y:.2f}) to ({nx:.2f}, {ny:.2f})')
        z1 = min(z0 + h, self.H)
        p = mt.Placed(el, nx, ny, wx, dy, z0, z1, facing, self.look(el, kind), side, axis=axis,
                      sign=self.edge(side)[2] if side in SIDES else 1)
        p.span = span
        return p

    # -- overhead
    def header_band(self, el):
        params = el.get('params') or {}
        sides = [s for s in SIDES if s in (params.get('sides') or [])]
        if not sides:
            sides = list(self.open) or list(SIDES)
            self.assume(el['id'], 'sides', sides, 'choice')
        size = list(el.get('size_m') or [None, None, None]) + [None] * 3
        depth = size[1] if _num(size[1]) else self.assume(el['id'], 'depth_m', 0.4)
        h = size[2] if _num(size[2]) else self.assume(el['id'], 'height_m', round(min(0.8, max(0.4, 0.18 * self.H)) * 20) / 20)
        if _num(el.get('z_m')):
            bottom = self.zf + el['z_m']
            top = bottom + h
        else:
            top = min(self.H - 0.1, max(self.wall_top, cm.PRIOR['header_bottom_min'] + h))
            bottom = top - h
            if bottom < cm.PRIOR['header_bottom_min']:
                bottom = cm.PRIOR['header_bottom_min']
                self.adjust(el['id'], f'band height cut to {top - bottom:.2f} m to keep 2.4 m clear below it')
        if top > self.H + 1e-6 or top - bottom < 0.1:
            raise ValueError(f'the header band ({bottom:.2f} to {top:.2f} m) does not fit under the height limit {self.H:g} m')
        mat = self.look(el, 'header_band')
        note = self.provenance_note(el)
        dd = depth / 2
        if len(sides) == 4:      # a closed frame inside the walls (if any)
            x0, x1 = self.edge('front')[3]
            y0, y1 = self.edge('left')[3]
            ow, od = x1 - x0, y1 - y0
            if min(ow, od) - 2 * depth < 0.4:
                raise ValueError(f'a {depth:g} m deep header leaves no opening in a {ow:.2f} x {od:.2f} m frame')
            self.b.add(f'{el["id"]}_frame', 'shell', 'rounded_ring', 'header_band', [(x0 + x1) / 2, (y0 + y1) / 2, (bottom + top) / 2],
                       {'outer_size': [round(ow, 4), round(od, 4)], 'inner_size': [round(ow - 2 * depth, 4), round(od - 2 * depth, 4)],
                        'outer_radius': 0.05, 'inner_radius': 0.02, 'height': round(top - bottom, 4), 'segments_per_corner': 4},
                       mat['color'], inferred=True, note=note, roughness=mat['roughness'], metallic=mat['metallic'],
                       emission=mat['emission'])
            corners = {('left', 'front'): (x0, y0), ('right', 'front'): (x1, y0), ('left', 'back'): (x0, y1), ('right', 'back'): (x1, y1)}
            j = 0
            for (sx, sy), (px, py) in corners.items():
                if sx in self.open and sy in self.open:
                    px, py = px + (0.1 if px < 0 else -0.1), py + (0.1 if py < 0 else -0.1)
                    self.b.box(f'{el["id"]}_post_{j}', 'shell', 'header_post', (px - 0.1, px + 0.1), (py - 0.1, py + 0.1),
                               (self.zf, bottom), mat['color'], inferred=True, note='Post from the floor to the header frame (support).')
                    j += 1
        else:
            runs = self._runs(sides)
            for k, run in enumerate(runs):
                path, posts = self._run_path(run, dd)
                cm.header_band(self.b, f'{el["id"]}_{k}' if len(runs) > 1 else f'{el["id"]}_band', 'shell', path, depth,
                               (bottom, top), posts=posts, post_size=0.2, floor_z=self.zf, color=mat['color'], inferred=True)
            for c in self.b.components:
                if c.get('group', '').startswith(el['id'] + '_') and c['role'] in ('header_band', 'header_post'):
                    c['material'].update(roughness=mat['roughness'], metallic=mat['metallic'])
                    if mat['emission'] and c['role'] == 'header_band':
                        c['material']['emission'] = mat['emission']
        # text from the brief on the outer face of the entrance side
        body = ((el.get('text') or {}).get('body') or '').strip()
        if body:
            tside = self.entrance if self.entrance in sides else next((s for s in sides if s in self.open), sides[0])
            axis, outer, sign, (lo, hi) = self.edge(tside)
            centre_n = self.band_centre(tside, dd)
            outward = tside in self.open
            n = centre_n - sign * dd if outward else centre_n + sign * dd
            yaw = mt.YAW_OUT[tside] if outward else mt.YAW_IN[tside]
            mid = (lo + hi) / 2
            pos = [mid, n, (bottom + top) / 2] if axis == 'y' else [n, mid, (bottom + top) / 2]
            p = mt.Placed(el, pos[0], pos[1], 0, 0, bottom, top, yaw, mat, tside)
            size_m = (el.get('text') or {}).get('size_m') or round(0.55 * (top - bottom), 3)
            mt.text_on_face(self.b, p, 'graphics', pos, yaw, size_m, note)
        self.header = {'sides': sides, 'bottom': bottom, 'top': top, 'depth': depth}

    def band_centre(self, side, dd):
        axis, outer, sign, _ = self.edge(side)
        return outer + sign * ((self.t if side in self.closed else 0.0) + dd)

    def _runs(self, sides):
        order = ['front', 'right', 'back', 'left']
        chosen = [s in sides for s in order]
        start = next(i for i in range(4) if chosen[i] and not chosen[i - 1])
        runs, cur = [], []
        for k in range(4):
            s = order[(start + k) % 4]
            if s in sides:
                cur.append(s)
            elif cur:
                runs.append(cur)
                cur = []
        if cur:
            runs.append(cur)
        return runs

    def _run_path(self, run, dd):
        """Centre-line path and free-end posts of one connected header run (sides in front, right, back, left order)."""
        ends = {'front': ('left', 'right'), 'right': ('front', 'back'), 'back': ('right', 'left'), 'left': ('back', 'front')}

        def point(side, at):
            c = self.band_centre(side, dd)
            return (at, c) if side in ('front', 'back') else (c, at)

        def end_coord(side, neighbour):
            if neighbour in self.closed:
                return self.inner_face(neighbour)
            return self.edge(neighbour)[1]
        pts, posts = [], []
        first, last = run[0], run[-1]
        start_n, finish_n = ends[first][0], ends[last][1]
        pts.append(point(first, end_coord(first, start_n)))
        for a, b in zip(run, run[1:]):
            pts.append(self._corner(a, b, dd))
            if a in self.open and b in self.open:
                posts.append(self._corner(a, b, dd))
        pts.append(point(last, end_coord(last, finish_n)))
        for (side, nb, p) in ((first, start_n, pts[0]), (last, finish_n, pts[-1])):
            if nb not in self.closed:
                axis = 'x' if side in ('front', 'back') else 'y'
                inward = 1 if (p[0 if axis == 'x' else 1] < 0) else -1
                posts.append((p[0] + inward * 0.1, p[1]) if axis == 'x' else (p[0], p[1] + inward * 0.1))
        return pts, posts

    def _corner(self, a, b, dd):
        xs = {s: self.band_centre(s, dd) for s in (a, b)}
        x = xs[a] if a in ('left', 'right') else xs[b]
        y = xs[a] if a in ('front', 'back') else xs[b]
        return (x, y)

    def ring(self, el):
        params = el.get('params') or {}
        size = list(el.get('size_m') or [None, None, None]) + [None] * 3
        inset = params.get('inset_m') if _num(params.get('inset_m')) else self.assume(el['id'], 'inset_m', 0.5, 'choice')
        band = size[1] if _num(size[1]) else self.assume(el['id'], 'band_m', 0.4, 'choice')
        h = size[2] if _num(size[2]) else self.assume(el['id'], 'height_m', 0.3, 'choice')
        top = self.zf + el['z_m'] + h if _num(el.get('z_m')) else self.H - 0.1
        bottom = top - h
        ow, od = self.W - 2 * inset, self.D - 2 * inset
        if min(ow, od) - 2 * band < 0.4:
            raise ValueError(f'a {band:g} m band leaves no opening in a {ow:.2f} x {od:.2f} m ring')
        r = params.get('corner_radius_m') if _num(params.get('corner_radius_m')) else 0.3
        r = min(r, min(ow, od) / 2 - 0.05)
        mat = self.look(el, 'ring')
        note = self.provenance_note(el)
        self.b.add(f'{el["id"]}_ring', 'shell', 'rounded_ring', 'ring', [0.0, 0.0, (bottom + top) / 2],
                   {'outer_size': [round(ow, 4), round(od, 4)], 'inner_size': [round(ow - 2 * band, 4), round(od - 2 * band, 4)],
                    'outer_radius': round(r, 4), 'inner_radius': round(max(0.05, r - band), 4), 'height': round(h, 4),
                    'segments_per_corner': 12}, mat['color'], inferred=True, note=note, roughness=mat['roughness'],
                   metallic=mat['metallic'], emission=mat['emission'])
        if self.hanging is not True:
            for j, (sx, sy) in enumerate(((-1, -1), (1, -1), (-1, 1), (1, 1))):
                px, py = sx * (ow / 2 - band / 2), sy * (od / 2 - band / 2)
                self.b.box(f'{el["id"]}_post_{j}', 'shell', 'header_post', (px - 0.075, px + 0.075), (py - 0.075, py + 0.075),
                           (self.zf, bottom), mat['color'], inferred=True, note='Post to the ring (hanging is not stated as allowed).')
            self.assume(el['id'], 'posts', 'four posts, because hanging is not stated as allowed', 'choice')
        self.ring_info = {'inset': inset, 'band': band, 'bottom': bottom, 'outer': (ow, od)}

    def spotlights(self, el):
        n = int(el.get('count') or self.assume(el['id'], 'count', 3, 'choice'))
        side = el.get('side') if el.get('side') in SIDES else self.entrance
        header = getattr(self, 'header', None)
        ring = getattr(self, 'ring_info', None)
        axis, outer, sign, (lo, hi) = self.edge(side)
        if header and side in header['sides']:
            z_top = header['bottom']
            line = self.band_centre(side, header['depth'] / 2)
        elif ring:
            z_top = ring['bottom']
            line = outer + sign * (ring['inset'] + ring['band'] / 2)
        else:
            z_top = self.wall_top
            line = self.inner_face(side) + sign * 0.3
            self.warn(f"{el['id']}: no header band or ring on the {side} side to hang from; the lights float at the wall top")
        if _num(el.get('z_m')):
            z_top = self.zf + el['z_m']
        span = (lo + 0.5, hi - 0.5) if hi - lo > 1.2 else (lo, hi)
        pos = []
        for i in range(n):
            u = (span[0] + span[1]) / 2 if n == 1 else span[0] + i * (span[1] - span[0]) / (n - 1)
            pos.append((round(u, 4), round(line, 4)) if axis == 'y' else (round(line, 4), round(u, 4)))
        cm.spotlights(self.b, el['id'], 'lighting', pos, z_top, inferred=True)

    def frame(self):
        """The read only context for the builders in motifs.OVERHEAD_BUILDERS."""
        return mt.Frame(X0=self.X0, X1=self.X1, Y0=self.Y0, Y1=self.Y1, zf=self.zf, H=self.H, wall_top=self.wall_top,
                        open_sides=tuple(self.open), closed_sides=tuple(self.closed), hanging_allowed=self.hanging,
                        assume=self.assume, adjust=self.adjust, warn=self.warn, brief=self.d['brief']['text'],
                        entrance=self.entrance)

    # -- the whole design
    def build(self):
        b, rep = self.b, self.report
        self.layout()
        for sid, why in (('booth_floor', 'Floor of the designed booth.'), ('shell', 'Walls, header, frames and partitions of the design.'),
                         ('furniture', 'Counters, tables, chairs, planters and storage of the design.'),
                         ('media', 'Screens and LED walls of the design (content neutral).'),
                         ('lighting', 'Light fixtures of the design.'), ('graphics', 'Lightboxes, graphic panels and text from the brief.')):
            b.scope(sid, why)
        b.exclude('venue', 'Hall, aisle and neighbours are not part of the design.')
        b.exclude('brand_text', 'No text or logo is copied from the reference picture; only text written in the brief is built.',
                  owner='booth')
        self.floor()
        overrides = {}
        for el in self.d['elements']:
            if el['kind'] == 'wall':
                if el['side'] in self.open:
                    rep['dropped'].append({'id': el['id'], 'reason': f"wall on the open {el['side']} side"})
                else:
                    overrides[el['side']] = el
        self.build_walls(overrides)
        for side, el in overrides.items():
            rep['elements'].append({'id': el['id'], 'kind': 'wall', 'side': side, 'parts': 1, 'built': True})
        order = sorted(self.d['elements'], key=lambda e: (0 if e['kind'] == 'header_band' else
                                                          1 if e['kind'] == 'ring' or e['kind'] in mt.OVERHEAD_BUILDERS else 2))
        for el in order:
            kind = el['kind']
            if kind == 'wall':
                continue
            if kind in mt.WALL_KINDS and el['side'] in self.open:
                rep['dropped'].append({'id': el['id'], 'reason': f"a {kind} needs a wall but the {el['side']} side is open"})
                continue
            before = len(b.components)
            marks = {k: len(rep[k]) for k in ('assumed', 'choices', 'adjustments', 'warnings')}
            try:
                if kind == 'header_band':
                    self.header_band(el)
                elif kind == 'ring':
                    self.ring(el)
                elif kind == 'spotlights':
                    self.spotlights(el)
                elif kind in mt.OVERHEAD_BUILDERS:
                    mt.OVERHEAD_BUILDERS[kind](b, self.frame(), el, self.look(el, kind), self.provenance_note(el))
                else:
                    size = self.size_of(el, kind)
                    p = self.place_wall(el, kind, size) if kind in mt.WALL_KINDS else self.place_floor(el, kind, size)
                    frame = p.frame = self.frame()
                    builder = mt.BUILDERS[kind]
                    if _takes_frame(builder):      # (b, placed, scope, note, frame): the frame reaches compile_report like an overhead builder's
                        builder(b, p, mt.SCOPE[kind], self.provenance_note(el), frame)
                    else:                          # (b, placed, scope, note): the frame is placed.frame
                        builder(b, p, mt.SCOPE[kind], self.provenance_note(el))
                    self.placed[el['id']] = p
                pattern = (el.get('material') or {}).get('pattern')
                for added in b.components[before:]:
                    added['group'] = el['id']                      # the whole element moves as one unit in an editor
                    if pattern is not None and added['kind'] != 'text' and not added['material'].get('emission'):
                        added['material']['pattern'] = copy.deepcopy(pattern)      # checked by validate_spec, not here
                    if added['kind'] != 'text':
                        mesh_for(added)
                    if not added['evidence']['note'].startswith('Design element'):
                        added['evidence']['note'] = self.provenance_note(el) + ' ' + added['evidence']['note']
            except (ValueError, KeyError, ZeroDivisionError) as ex:
                gone = {x['id'] for x in b.components[before:]}
                del b.components[before:]
                b._ids -= gone
                b.requirements = [r for r in b.requirements if r['component_id'] not in gone]
                for k, n in marks.items():
                    del rep[k][n:]
                rep['dropped'].append({'id': el['id'], 'reason': str(ex)})
                continue
            rep['elements'].append({'id': el['id'], 'kind': kind, 'side': el.get('side'), 'parts': len(b.components) - before,
                                    'built': True})
        self.overlaps()
        if any(c['kind'] == 'text' and mt.PLACEHOLDER_MARK in c['evidence']['note'] for c in b.components):
            b.scopes['brand_text']['evidence'] = ('No text or logo is copied from the reference picture; only text written in the brief is '
                                                  'built; placeholder letters are marked as choices.')
        if not any(e.get('hero') for e in self.d['elements']):
            self.warn('no element is marked hero: one clear main visual reads better')
        for sid in [k for k, v in b.scopes.items() if v['decision'] == 'include' and not any(x['scope_id'] == k for x in b.components)]:
            del b.scopes[sid]
        if self.fit:
            b.camera(copy.deepcopy(self.fit['camera']))
            notes = (f"Footprint {self.W:g} x {self.D:g} m from the corner fit (estimate, not a measurement) and height limit {self.H:g} m "
                     f"from the designer's form (brief). Wall top {self.wall_top:g} m, floor at {self.zf:g} m. Camera copied from the fit.")
            b.scale((self.W, self.D), notes, basis='single_image_estimate')
        else:
            b.camera(self.hero_camera())
            notes = (f"Footprint {self.W:g} x {self.D:g} m and height limit {self.H:g} m from the designer's form (brief). "
                     f"Wall top {self.wall_top:g} m, platform {self.zf:g} m. Generated design; nothing is measured from the reference picture.")
            b.scale((self.W, self.D), notes, basis='user_dimensions')
        ref = self.d.get('reference') or {}
        b.uncertain('design_generated', 'A new design generated from a brief and a style reference'
                    + (f" ({ref['sha256'][:12]}...)" if ref.get('sha256') else '') + '; the picture gave no measurements.')
        if self.fit:
            b.uncertain('footprint', f"footprint: fitted. {self.W:g} x {self.D:g} m comes from the corner fit of the picture and replaces "
                        "the footprint of the design form; an estimate, not a measurement.")
        for key, label in (('assumed', 'Generic defaults (prior)'), ('choices', 'Design choices made by the compiler (choice)')):
            items = [f"{a['id']}.{a['what']}={a['value']}" for a in rep[key]]
            if items:
                shown = items[:25] + ([f'and {len(items) - 25} more (see compile_report.json)'] if len(items) > 25 else [])
                b.uncertain('design_' + key, label + ': ' + '; '.join(shown))
        for u in self.d.get('uncertainties') or []:
            b.uncertain(u['id'], u['description'], critical=bool(u.get('critical')))
        return b.build()

    def overlaps(self):
        """Parts of different elements whose boxes intersect (not just touch): one element runs into another."""
        parts = [(c.get('group') or c['id'], world_bounds(c)) for c in self.b.components if c['kind'] != 'text' and c['role'] != 'floor']
        seen = set()
        for i in range(len(parts)):
            ga, (la, ha) = parts[i]
            for j in range(i + 1, len(parts)):
                gb, (lb, hb) = parts[j]
                if ga == gb or (ga, gb) in seen:
                    continue
                o = [min(ha[k], hb[k]) - max(la[k], lb[k]) for k in range(3)]
                if min(o) > 0.01:
                    seen.add((ga, gb))
                    self.warn(f'{ga} and {gb} overlap ({o[0]:.2f} x {o[1]:.2f} x {o[2]:.2f} m): move or resize one '
                              '(along / offset_m / centre_m / size_m)')

    def hero_camera(self):
        """Three-quarter view from outside the entrance at eye height; distance chosen so the whole booth fits the canvas."""
        lo = [min(world_bounds(c)[0][i] for c in self.b.components if c['kind'] != 'text') for i in range(3)]
        hi = [max(world_bounds(c)[1][i] for c in self.b.components if c['kind'] != 'text') for i in range(3)]
        ox, oy = OUTWARD[self.entrance]
        others = [s for s in self.open if s != self.entrance and OUTWARD[s] != (-ox, -oy)]
        tx, ty = OUTWARD[others[0]] if others else ((1.0, 0.0) if ox == 0 else (0.0, -1.0))
        turn = math.radians(30 if others else 25) * (1 if ox * ty - oy * tx > 0 else -1)
        ax, ay = ox * math.cos(turn) - oy * math.sin(turn), ox * math.sin(turn) + oy * math.cos(turn)
        self.assume('camera', 'view', f"from outside the {self.entrance} side, turned {math.degrees(abs(turn)):.0f} degrees", 'choice')
        cx, cy = (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2
        target = [cx, cy, round(min(1.4, max(1.0, (lo[2] + hi[2]) / 2)), 3)]
        eye = self.zf + cm.PRIOR['eye_height']
        corners = [(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]

        def cam(dist):
            return {'projection': 'perspective', 'position': [round(cx + dist * ax, 4), round(cy + dist * ay, 4), round(eye + 0.02 * dist, 4)],
                    'target': target, 'lens_mm': 35.0, 'sensor_width_mm': 36.0, 'principal_point': [0.5, 0.5],
                    'pixel_aspect': [1, 1], 'fit_status': 'unfitted', 'roll_deg': 0.0}

        def fits(dist):
            c = cam(dist)
            m = 0.05
            try:
                for p in corners:
                    u, v, _ = cmath.project(p, c, self.iw, self.ih)
                    if not (m * self.iw <= u <= (1 - m) * self.iw and m * self.ih <= v <= (1 - m) * self.ih):
                        return False
            except ValueError:
                return False
            return True
        near, far = 1.0, 200.0
        if not fits(far):
            self.warn('the booth does not fit the hero view; the camera may crop it')
            return cam(far)
        for _ in range(40):
            mid = (near + far) / 2
            near, far = (near, mid) if fits(mid) else (mid, far)
        return cam(round(far, 3))


# ------------------------------------------------------------------ brief checks
def brief_check(design, spec, report):
    """Basic checks of the parsed brief requirements against what was built: must_have, must_not, count, text."""
    built = {e['id'] for e in report['elements'] if e.get('built')}
    els = [e for e in design['elements'] if e['id'] in built]
    texts = [c['params']['body'].strip() for c in spec['components'] if c['kind'] == 'text']

    def units(e, kind):
        if e['kind'] == kind:
            return int(e.get('count') or 1) if kind in ('planter', 'spotlights') else 1
        if kind == 'chair' and e['kind'] == 'seating_group':
            return int(e.get('count') or 2)
        if kind == 'round_table' and e['kind'] == 'seating_group':
            return 1
        return 0
    items = []
    for r in design.get('requirements') or []:
        kind, side = r.get('element_kind'), r.get('side')
        match = [e for e in els if units(e, kind) and (side is None or e.get('side') == side)]
        if r['kind'] == 'must_have':
            ok = bool(match)
            why = (f"built: {', '.join(e['id'] for e in match)}" if ok else
                   f"no {kind} was built" + (f' on the {side} side' if side else ''))
        elif r['kind'] == 'must_not':
            ok = not match
            why = 'none built' if ok else f"built anyway: {', '.join(e['id'] for e in match)}"
        elif r['kind'] == 'count':
            n = sum(units(e, kind) for e in match)
            ok = n == r['count']
            why = f'{n} built, {r["count"]} asked'
        else:
            want = r['text'].strip()
            ok = want in texts
            why = 'the text is on the model' if ok else f'no text part reads {want!r}'
        items.append({'id': r['id'], 'priority': r.get('priority') or 'must', 'kind': r['kind'], 'quote': r.get('quote'),
                      'status': 'satisfied' if ok else 'unsatisfied', 'reason': why})
    missing = [i for i in items if i['status'] == 'unsatisfied']
    return {'status': 'satisfied' if not missing else ('unsatisfied' if any(i['priority'] == 'must' for i in missing) else 'partial'),
            'total': len(items), 'satisfied': len(items) - len(missing), 'items': items}


# ------------------------------------------------------------------ files
def canvas_png(width, height):
    from PIL import Image, ImageDraw
    img = Image.new('RGB', (width, height), (205, 205, 205))
    ImageDraw.Draw(img).text((24, 24), 'design canvas - generated design, not a photograph', fill=(120, 120, 120))
    buf = io.BytesIO()
    img.save(buf, format='PNG', optimize=False)
    return buf.getvalue()


def design_context(design):
    form = design['form']
    ctx = {'height_limit_m': float(form['height_limit_m']), 'open_sides': list(form['open_sides'])}
    if isinstance(form.get('hanging_allowed'), bool):
        ctx['hanging_allowed'] = form['hanging_allowed']
    return ctx


def _write(path, data):
    path.write_bytes((json.dumps(data, indent=2, ensure_ascii=False) + '\n').encode('utf-8'))


def load_fit(fit, image_path):
    """A box_fit.json dict and its picture -> (image bytes, (width, height), file suffix). ValueError when they cannot be used."""
    status = fit.get('status') if isinstance(fit, dict) else None
    if status != 'fitted':
        raise ValueError(f"--fit needs a corner fit with status 'fitted', not {status!r}")
    cam = fit.get('camera')
    if not isinstance(cam, dict) or any(k not in cam for k in ('position', 'target', 'lens_mm', 'sensor_width_mm')):
        raise ValueError('the fit has no usable camera')
    dims = fit.get('dims_m')
    if not isinstance(dims, dict) or not all(_num(dims.get(k)) and dims[k] > 0 for k in 'WD'):
        raise ValueError('the fit has no usable dims_m (W and D)')
    if fit.get('floor_z') is not None and not _num(fit['floor_z']):
        raise ValueError('the fit has a floor_z that is not a number')
    img = fit.get('image') or {}
    if not all(isinstance(img.get(k), int) and img[k] > 0 for k in ('width', 'height')):
        raise ValueError('the fit has no image width and height')
    from PIL import Image
    data = Path(image_path).read_bytes()
    try:
        with Image.open(io.BytesIO(data)) as im:
            size = im.size
    except (OSError, ValueError) as ex:
        raise ValueError(f'cannot read the picture {image_path}: {ex}') from ex
    if size != (img['width'], img['height']):
        raise ValueError(f"--image is {size[0]}x{size[1]} but the fit is for a {img['width']}x{img['height']} picture")
    return data, size, Path(image_path).suffix.lower() or '.png'


def compile_design(design, out_dir, canvas=(1280, 800), design_path=None, fit=None, image_path=None, fit_path=None):
    """-> (spec, report). Writes the outputs only after the whole compile has run.

    fit (a box_fit.json dict) and image_path go together: the camera, footprint and floor height then come from the fit and the
    spec's source is a copy of the picture instead of the generated canvas."""
    if (fit is None) != (image_path is None):
        raise ValueError('--fit and --image must be given together')
    picture = None
    if fit is not None:
        picture = load_fit(fit, image_path)
        canvas = picture[1]
        design = json.loads(json.dumps(design))
        design.setdefault('form', {})['footprint_m'] = [round(float(fit['dims_m']['W']), 4), round(float(fit['dims_m']['D']), 4)]
        # the build puts the floor at the fit's floor_z: validate (the PARAM checks read it) against the same height
        design['layout'] = dict(design.get('layout') or {}, platform_height_m=float(fit.get('floor_z') or 0.0))
    errors = validate_design(design)
    if errors:
        raise DesignError(errors)
    design = json.loads(json.dumps(design))
    ref = design.get('reference') or {}
    if ref.get('image') and design_path:
        p = (Path(design_path).parent / ref['image']).resolve()
        if p.is_file():
            ref['sha256'] = hashlib.sha256(p.read_bytes()).hexdigest()
            design['reference'] = ref
    if picture:
        png, source = None, ('source' + picture[2], hashlib.sha256(picture[0]).hexdigest())
    else:
        png, source = canvas_png(*canvas), None
    comp = DesignCompiler(design, png, canvas, fit, source)
    spec = comp.build()
    report = comp.report
    leftover = separate_coplanar(spec, report)
    for cid, over in outside_footprint(spec):
        report['warnings'].append(f'{cid}: extends {over:.2f} m outside the footprint')
    check = validate(spec, source_size=list(canvas))
    report['valid'] = check['valid']
    report['validation_errors'] = check['errors'][:20]
    report['warnings'] += [f"{w['code']} {w.get('ids') or w.get('id') or ''} {w.get('plane', '')}".strip() for w in check['warnings']]
    report['coplanar_unresolved'] = [{'ids': w['ids'], 'plane': w['plane']} for w in leftover]
    report['camera'] = spec['camera']
    if fit is not None:
        report['fit'] = {'path': str(fit_path) if fit_path else None, 'status': fit.get('status')}
    report['components_built'] = len(spec['components'])
    if not check['valid']:
        report['status'] = 'invalid'
        return spec, report
    context = design_context(design)
    full = design_check_run(spec, context)
    report['design_check'] = {'status': full['status'], 'counts': full['counts'], 'findings': full['findings'][:15],
                              'not_checked': full['not_checked']}
    report['brief'] = brief_check(design, spec, report)
    report['status'] = 'partial' if report['dropped'] else 'ok'
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if picture:
        for old in out.glob('source.*'):
            if old.name != source[0]:
                old.unlink()
        (out / source[0]).write_bytes(picture[0])
    else:
        (out / 'design_canvas.png').write_bytes(png)
    for name, data in (('spec.json', spec), ('compile_report.json', report), ('design_context.json', context)):
        _write(out / name, data)
    return spec, report


def parse_canvas(text):
    m = re.fullmatch(r'(\d{2,5})x(\d{2,5})', text or '')
    if not m:
        raise ValueError('--canvas must look like 1280x800')
    w, h = int(m.group(1)), int(m.group(2))
    if w * h > 5_000_000:
        raise ValueError('--canvas is capped at 5 MP')
    return w, h


def run(design_path, out_dir, canvas='1280x800', fit_path=None, image_path=None):
    """Files in, files out. Returns (exit_code, summary); every expected failure is a summary, not a traceback."""
    try:
        if bool(fit_path) != bool(image_path):
            raise ValueError('--fit and --image must be given together')
        size = parse_canvas(canvas)
        design = json.loads(Path(design_path).read_text(encoding='utf-8'))
        fit = json.loads(Path(fit_path).read_text(encoding='utf-8')) if fit_path else None
        spec, report = compile_design(design, out_dir, size, design_path, fit, image_path, fit_path)
    except DesignError as ex:
        return 2, {'status': 'design_invalid', 'errors': ex.errors[:30]}
    except ImportError as ex:
        return 2, {'status': 'missing_dependency', 'message': f'{ex}; install the packages in requirements.txt'}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as ex:
        return 2, {'status': 'input_error', 'message': f'{type(ex).__name__}: {ex}'}
    summary = {k: report.get(k) for k in ('status', 'components_built', 'valid', 'dropped', 'brief', 'design_check')}
    summary.update(assumed=len(report['assumed']), choices=len(report['choices']), adjustments=report['adjustments'][:10],
                   warnings=report['warnings'][:12], validation_errors=report['validation_errors'][:5],
                   spec=str(Path(out_dir) / 'spec.json'), context=str(Path(out_dir) / 'design_context.json'))
    if summary.get('brief'):
        summary['brief'] = {k: report['brief'][k] for k in ('status', 'total', 'satisfied')}
        summary['brief']['not_done'] = [f"{i['id']}: {i['reason']}" for i in report['brief']['items'] if i['status'] != 'satisfied']
    return (0 if report['valid'] else 2), summary


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    p.add_argument('--design', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--canvas', default='1280x800', help='render size of the design canvas, WIDTHxHEIGHT (default 1280x800)')
    p.add_argument('--fit', help='box_fit.json (status fitted): copy its camera, footprint and floor height; needs --image')
    p.add_argument('--image', help='the picture the fit was made on; becomes the spec source (needs --fit)')
    a = p.parse_args()
    code, summary = run(a.design, a.out, a.canvas, a.fit, a.image)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
