"""The edit catalogue of the loop (docs/poc/02-architecture-and-contracts.md section 4.3, fixer.v1).

    from loop import edits
    new_design = edits.apply_edit(design, edits.make_edit('move', 'reception', delta_m=[0.3, 0, 0], finding_id='f1'))
    new_design, report = edits.apply_edits(design, edit_list, log=print)

Eight ops work on a booth-design.v1 object: move, resize, recolour, add, remove, replace_kind, set_count, set_text. Every edit is
applied to a copy and the copy is checked against schemas/booth-design.v1.json; an edit that cannot be applied or whose result is
not schema-valid is rejected (EditError), recorded in the report and logged, and the design is left as it was. The designer's
facts (`form`) are never touched.

fixer.v1 has no field for a count or a text body, so two ops reuse existing fields (decisions recorded in the P8a report):
  set_count   `delta_m[0]` is the integer change of the count (the result must stay >= 1);
  set_text    `kind` carries the new text body (at most 60 characters), null clears the text.
`move` takes metres in the booth frame (x to the right, y from the front towards the back, z up). An element placed by side and
`along` has its `along` shifted by the move along that side and, if it stands on the floor, its `offset_m` by the move away from
or towards the wall; an element placed by `centre_m` has the centre moved.

Also here: findings_to_edits, which turns critic.v1 findings into edits by fixed rules (no model, no guessing), and SpecView, the
view of what the compiler built (heights, pixel boxes, sizes of one element) that those rules measure against:
  a move is sized from the critic's `bbox_px` (where the element really is in the picture) against the element's box in the
  render: the centre offset in pixels becomes metres by d = px * Z / f (Z the depth of the element, f the focal length in pixels),
  at most 0.5 m; without a box the steps are 0.05 m (medium, low) and 0.1 m (high). Up and down start from the element's real
  height in the compiled spec (the design usually has no z_m), never from 0.
  a resize with `bbox_px` takes the pixel ratio of the two boxes; without a size_m it starts from the compiled size of the part
  that size_m controls (the pot of a planter, the body of a counter, band and height of a ring, not the plants, the overhanging
  top, the posts), and a round table is resized through params.radius_m (size_m[0] of a resize edit is then its diameter).
  Moves compare the same main body as resizes (a ring: the band, never its posts; no element: its text). The width ratio changes
  size_m[0] only; the depth takes the small step; a count finding on the same element in the round also sends the width to the
  small step (the box is wider because there are more pieces). A ratio that agrees with the direction is used as it is (no dead
  zone near 1); exactly 1, or against the direction, is the small step. A wall element (feature_wall, graphic_panel, tv, led_wall,
  wall on a side) moves only along its wall, delta_m records that part only, and up or down works from its real height (the compiler reads z_m); only the shell kind wall, whose z_m the compiler ignores, skips up or down.
  Every geometry edit (move, resize, set_count) is measured on its own, after a recompile, not predicted (measure_edits): the edit is
  applied alone to the winner's design and compiled, and the main-body box of the element in the winner's spec is compared with
  that compile (check_single). A change under 5 mm drops the edit ('no effect': the compiler does not read the field, a snapped LED
  wall, the shell wall), an element that is missing after the compile drops it ('element lost': a panel lifted over the wall top),
  and a move's delta_m becomes the measured change (horizontal: the centre of the box, vertical: its bottom). A count change is
  judged by the number of built units (built_units: fixtures, pots, chairs), not by the box, which a row of lights does not
  change. A dropped edit takes no candidate slot; the edits that stay are composed into a candidate, compiled again, and the
  resizes in it are checked once more for direction.
  The pixel ratio compares the same parts: the render side is the box of the components that size_m controls (a ring: the band, not
  its posts; a planter: the pot, not the plants), and a ratio is used only for a dimension that sets that box's width or height
  (a ring's band width does not; a planter's pot height does not set the height of a box that includes the plants): the others, and
  any axis whose ratio is unusable or contradicts the critic's direction, take the small step (x1.1) in the critic's direction.
  A move's delta_m is the real move in metres; for up and down apply_edit(design, edit, view=...) starts from the real height of the
  compiled element (the design usually has no z_m).
  The render box is clipped to the picture, as the critic can only box what it sees; only edges that neither box has cut are
  compared (a direction with no such edge, or a pixel offset against the critic's direction, gives the small step). check_resizes
  compares the sizes before and after a recompile: a resize that changed nothing, or went the wrong way, is dropped.
  A move compares the critic's box with the part of the element the critic was told to box (move_bodies: a round table's top, the
  whole pot of a planter with its plants, otherwise the main body); along the axis of the move the length of the critic's box over the
  length of that part must lie in MOVE_RATIO_RANGE, or the next part is tried (a table: the whole table; a planter: the pot alone;
  no other kind has a second part), and if none fits the finding is a size problem and no move is made (choose_move_body). Furniture the compiler
  stands on the floor (FLOOR_STANDING_KINDS), with no z_m in the design, is not moved up or down (floor_standing). A canopy_frame is
  resized by the width ratio of its frame body (frame and sign, no posts, no letters), the depth and height by the small step, and
  its width and depth stop at the footprint.
  The reader's frame (rotate_design, decide_frame, frame_check, 02 section 4.1): the design is turned by 0, 90, 180 and 270 degrees,
  each turn compiled, the main bodies projected with the fitted camera and compared by IoU with the reader's own evidence boxes; the
  design is turned only when the one as read scores below 0.3, the best turn at least 0.5 and at least 0.3 more. The orchestrator
  runs it before the first compile and writes round_1/frame_check.json.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import sys
from pathlib import Path

import jsonschema

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.append(str(ROOT / 'booth-image-to-model' / 'scripts'))      # camera_math, validate_spec: the skill's code, read only
import camera_math  # noqa: E402
import motifs  # noqa: E402  (the canopy plan: the compiler's own defaults for a sign)
import validate_spec  # noqa: E402

SCHEMA_PATH = ROOT / 'booth-image-to-model' / 'schemas' / 'booth-design.v1.json'

EDIT_OPS = ('move', 'resize', 'recolour', 'add', 'remove', 'replace_kind', 'set_count', 'set_text')
EDIT_FIELDS = ('op', 'element_id', 'kind', 'delta_m', 'size_m', 'color_hex', 'side', 'along', 'finding_id')
SIDES = ('front', 'back', 'left', 'right')
WALL_KINDS = ('feature_wall', 'graphic_panel', 'tv', 'led_wall', 'wall')
DIRECTIONS_MOVE = ('left', 'right', 'forward', 'back', 'up', 'down')
SEVERITY_RANK = {'high': 0, 'medium': 1, 'low': 2}
SIZE_STEP = 1.2
SHADE_STEP = 0.2
GEOMETRY_TYPES = ('misplaced', 'wrong_size')
COLOUR_TYPES = ('wrong_colour',)
SMALL_STEP_M, LARGE_STEP_M = 0.05, 0.1           # moves without a box in the picture: medium and low, high severity
MIN_MOVE_M, MAX_MOVE_M = 0.01, 0.5
FALLBACK_SIZE_STEP = 1.1
SIZE_RATIO_RANGE = (0.5, 2.0)
BODY_SKIP_ROLES = ('header_post', 'floor')       # posts to the floor and the floor itself do not tell where an element hangs
HANGING_KINDS = ('spotlights',)                  # their z_m is the top of the fixtures, the others' the bottom
SIZE_FROM_SPEC = ('feature_wall', 'graphic_panel', 'led_wall', 'wall', 'counter', 'display_plinth', 'storage_block', 'partition',
                  'box', 'planter', 'ring', 'canopy_frame', 'lit_panel')
NO_SIZE_REASON = {      # kinds whose size cannot be taken from the built parts when the design has no size_m
    'tv': 'the size of a tv follows params.inch, not size_m',
    'seating_group': 'a seating group has a fixed size (its table and chairs)',
    'chair': 'a chair has a fixed size',
    'spotlights': 'spotlights have no size_m',
    'header_band': 'its depth and height cannot be read back from the built boxes, which run along several sides',
}
TEXT_ROLES = ('sign_text',)
GEOMETRY_OPS = ('move', 'resize', 'set_count')
RATIO_EXACT = 1e-9                                    # a ratio this close to 1 is exactly 1: no information
SIZE_TOLERANCE_M = 0.005                              # a built size that changes by less than this has not changed
EDGE_MARGIN_PX = 0.5                                  # a critic box edge this close to the frame is the frame's cut, not the element's
MOVE_RATIO_RANGE = (0.7, 1.43)                        # critic box length over render body length that still reads as a move (02 4.3, sixth note)
SIZE_PROBLEM = 'the two boxes differ in size: this is a size problem, not a position problem'
FLOOR_STANDING_KINDS = ('round_table', 'counter', 'planter', 'box', 'display_plinth', 'storage_block', 'seating_group', 'chair')
FLOOR_STANDING_TOLERANCE_M = 0.01                     # a bottom this close to the platform top stands on it
FLOOR_STANDING = 'floor-standing furniture is not moved up or down'

_validator = None


class EditError(ValueError):
    """An edit that is malformed, names something that does not exist, or leaves the design schema-invalid."""


def _schema_validator():
    global _validator
    if _validator is None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
        _validator = jsonschema.Draft202012Validator(schema)
    return _validator


def validate_design(design):
    """-> list of error strings (empty when the design satisfies booth-design.v1)."""
    errors = sorted(_schema_validator().iter_errors(design), key=lambda e: list(map(str, e.absolute_path)))
    return [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in errors]


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def make_edit(op, element_id, finding_id='manual', **fields):
    """A complete fixer.v1 edit: every listed field present, the unused ones null."""
    unknown = set(fields) - set(EDIT_FIELDS)
    if unknown:
        raise EditError(f'BAD_EDIT: unknown field(s) {sorted(unknown)}')
    edit = {k: None for k in EDIT_FIELDS}
    edit.update(op=op, element_id=element_id, finding_id=finding_id)
    edit.update(fields)
    return edit


def make_fixer(edit_list):
    return {'schema_version': 'fixer.v1', 'edits': list(edit_list)}


def edit_problems(edit):
    """Shape problems of one edit (fixer.v1 strictness: exactly the nine fields; null where unused)."""
    if not isinstance(edit, dict):
        return ['the edit is not an object']
    out = []
    missing, extra = set(EDIT_FIELDS) - set(edit), set(edit) - set(EDIT_FIELDS)
    if missing:
        out.append(f'missing field(s) {sorted(missing)}')
    if extra:
        out.append(f'unlisted field(s) {sorted(extra)}')
    if out:
        return out
    if edit['op'] not in EDIT_OPS:
        out.append(f"unknown op {edit['op']!r}")
    for k in ('element_id', 'finding_id'):
        if not (isinstance(edit[k], str) and edit[k]):
            out.append(f'{k} must be a non-empty string')
    for k in ('kind', 'color_hex'):
        if edit[k] is not None and not isinstance(edit[k], str):
            out.append(f'{k} must be a string or null')
    if edit['delta_m'] is not None and not (isinstance(edit['delta_m'], list) and len(edit['delta_m']) == 3
                                            and all(_num(v) for v in edit['delta_m'])):
        out.append('delta_m must be three numbers or null')
    if edit['size_m'] is not None and not (isinstance(edit['size_m'], list) and len(edit['size_m']) == 3
                                           and all(v is None or _num(v) for v in edit['size_m'])):
        out.append('size_m must be three numbers (or nulls) or null')
    if edit['side'] is not None and edit['side'] not in SIDES + ('centre',):
        out.append(f"side {edit['side']!r} is not front, back, left, right or centre")
    if edit['along'] is not None and not _num(edit['along']):
        out.append('along must be a number or null')
    return out


def validate_fixer(doc):
    """-> list of error strings for a fixer.v1 document."""
    if not isinstance(doc, dict):
        return ['the fixer document is not an object']
    out = []
    if set(doc) != {'schema_version', 'edits'}:
        out.append('a fixer.v1 document has exactly schema_version and edits')
        if 'edits' not in doc:
            return out
    if doc.get('schema_version') != 'fixer.v1':
        out.append("schema_version must be 'fixer.v1'")
    if not isinstance(doc.get('edits'), list):
        return out + ['edits must be an array']
    for i, e in enumerate(doc['edits']):
        out += [f'edits[{i}]: {p}' for p in edit_problems(e)]
    return out


# -- geometry helpers (also used by the stand-ins)

def footprint(design):
    w, d = design['form']['footprint_m']
    return float(w), float(d)


def find_element(design, element_id):
    for el in design['elements']:
        if el['id'] == element_id:
            return el
    raise EditError(f'NO_SUCH_ELEMENT: {element_id!r}')


def element_centre(design, el):
    """Approximate (x, y, z_base) in metres of an element, from centre_m or side + along + offset_m."""
    w, d = footprint(design)
    z = float(el['z_m']) if _num(el.get('z_m')) else 0.0
    if el.get('centre_m'):
        return float(el['centre_m'][0]), float(el['centre_m'][1]), z
    side = el.get('side')
    along = float(el['along']) if _num(el.get('along')) else 0.5
    off = 0.0 if el['kind'] in WALL_KINDS else (float(el['offset_m']) if _num(el.get('offset_m')) else 0.0)
    if side == 'front':
        return -w / 2 + along * w, -d / 2 + off, z
    if side == 'back':
        return -w / 2 + along * w, d / 2 - off, z
    if side == 'left':
        return -w / 2 + off, -d / 2 + along * d, z
    if side == 'right':
        return w / 2 - off, -d / 2 + along * d, z
    return -w / 2 + along * w, 0.0, z


def _move(design, el, delta, base_z=None):
    dx, dy, dz = (float(v) for v in delta)
    w, d = footprint(design)
    side = el.get('side')
    if el.get('centre_m'):
        cx, cy = el['centre_m']
        el['centre_m'] = [round(cx + dx, 4), round(cy + dy, 4)]
    elif side in SIDES:
        span = w if side in ('front', 'back') else d
        along = float(el['along']) if _num(el.get('along')) else 0.5
        shift = dx if side in ('front', 'back') else dy
        el['along'] = round(along + shift / span, 4)
        if el['kind'] not in WALL_KINDS:
            inward = {'front': dy, 'back': -dy, 'left': dx, 'right': -dx}[side]
            if inward:
                el['offset_m'] = round((float(el['offset_m']) if _num(el.get('offset_m')) else 0.0) + inward, 4)
    else:
        x, _, _ = element_centre(design, el)
        el['centre_m'] = [round(x + dx, 4), round(dy, 4)]
    if dz:       # from the real height of the compiled element when it is known, else from z_m (0 when the design has none)
        base = base_z if base_z is not None else (float(el['z_m']) if _num(el.get('z_m')) else 0.0)
        el['z_m'] = round(base + dz, 4) + 0.0


def apply_edit(design, edit, view=None):
    """-> a new design with the edit applied. Raises EditError and leaves `design` untouched when the edit cannot be applied.
    view: the SpecView of the compiled design; a move up or down then starts from the element's real height."""
    problems = edit_problems(edit)
    if problems:
        raise EditError('BAD_EDIT: ' + '; '.join(problems))
    new = copy.deepcopy(design)
    op, eid = edit['op'], edit['element_id']
    if op == 'add':
        if any(e['id'] == eid for e in new['elements']):
            raise EditError(f'DUPLICATE_ID: {eid!r} already exists')
        if not edit['kind']:
            raise EditError('BAD_EDIT: add needs a kind')
        el = {'id': eid, 'kind': edit['kind']}
        if edit['side'] is not None:
            el['side'] = edit['side']
        if edit['along'] is not None:
            el['along'] = edit['along']
        if edit['size_m'] is not None:
            el['size_m'] = edit['size_m']
        if edit['color_hex'] is not None:
            el['material'] = {'color_hex': edit['color_hex']}
        new['elements'].append(el)
    else:
        el = find_element(new, eid)
        if op == 'move':
            if edit['delta_m'] is None:
                raise EditError('BAD_EDIT: move needs delta_m')
            base_z = view.level_rel(eid, el['kind']) if view is not None and edit['delta_m'][2] else None
            _move(new, el, edit['delta_m'], base_z)
            if _num(el.get('z_m')) and el['z_m'] < -1e-9:
                raise EditError(f"BAD_EDIT: z_m would be {el['z_m']:g}, below the floor")
        elif op == 'resize':
            if edit['size_m'] is None:
                raise EditError('BAD_EDIT: resize needs size_m')
            if any(v is not None and v <= 0 for v in edit['size_m']):
                raise EditError('BAD_EDIT: a size must be positive')
            if el['kind'] == 'round_table':       # a round table has a radius: size_m[0] is its diameter, size_m[2] its height
                if edit['size_m'][0] is not None:
                    el['params'] = dict(el.get('params') or {}, radius_m=round(edit['size_m'][0] / 2, 4))
                if edit['size_m'][2] is not None:
                    el['size_m'] = [None, None, edit['size_m'][2]]
            else:
                el['size_m'] = list(edit['size_m'])
        elif op == 'recolour':
            if edit['color_hex'] is None:
                raise EditError('BAD_EDIT: recolour needs color_hex')
            el['material'] = dict(el.get('material') or {}, color_hex=edit['color_hex'])
        elif op == 'remove':
            new['elements'] = [e for e in new['elements'] if e['id'] != eid]
        elif op == 'replace_kind':
            if not edit['kind']:
                raise EditError('BAD_EDIT: replace_kind needs a kind')
            el['kind'] = edit['kind']
        elif op == 'set_count':
            step = (edit['delta_m'] or [None])[0]
            if not _num(step) or step != int(step) or int(step) == 0:
                raise EditError('BAD_EDIT: set_count needs a non-zero whole number in delta_m[0]')
            el['count'] = int(el.get('count') or 1) + int(step)
        elif op == 'set_text':
            if edit['kind'] is None:
                el['text'] = None
            else:
                el['text'] = {'body': edit['kind'], 'size_m': (el.get('text') or {}).get('size_m')}
    errors = validate_design(new)
    if errors:
        raise EditError('SCHEMA_INVALID: ' + errors[0] + (f' (+{len(errors) - 1} more)' if len(errors) > 1 else ''))
    return new


def apply_edits(design, edit_list, log=None, view=None):
    """Apply edits in order, skipping (and logging) those that fail.

    -> (new design, {'applied': [edit...], 'rejected': [{'edit': edit, 'reason': str}...]}). `log` is a callable taking one line.
    """
    applied, rejected, current = [], [], design
    for edit in edit_list:
        try:
            current = apply_edit(current, edit, view=view)
        except EditError as ex:
            rejected.append({'edit': edit, 'reason': str(ex)})
            if log:
                fid = edit.get('finding_id') if isinstance(edit, dict) else None
                log(f'edit rejected (finding {fid}): {ex}')
        else:
            applied.append(edit)
    return current, {'applied': applied, 'rejected': rejected}


# -- critic findings -> edits

def _hex_to_rgb(h):
    return [int(h[i:i + 2], 16) for i in (1, 3, 5)]


def shade(hex_colour, direction, step=SHADE_STEP):
    """A darker (towards black) or lighter (towards white) hex colour."""
    rgb = _hex_to_rgb(hex_colour)
    out = [round(c * (1 - step)) for c in rgb] if direction == 'darker' else [round(c + (255 - c) * step) for c in rgb]
    return '#%02x%02x%02x' % tuple(out)


def current_colour(design, el):
    mat = el.get('material') or {}
    if mat.get('color_hex'):
        return mat['color_hex']
    return design['style'].get(f"{mat.get('role') or 'dominant'}_hex")


def ground_axes(camera):
    """-> (right, toward) unit vectors on the floor (x, y) for a camera dict: `right` is the picture's right, `toward` points at
    the camera. None when the camera is missing or looks straight down (then the fixed booth axes are used)."""
    if not camera:
        return None
    try:
        fwd, right, _ = camera_math.axes(camera)
    except (ValueError, KeyError, TypeError):
        return None
    ra, fa = math.hypot(right[0], right[1]), math.hypot(fwd[0], fwd[1])
    if ra < 1e-6 or fa < 1e-6:
        return None
    return (right[0] / ra, right[1] / ra), (-fwd[0] / fa, -fwd[1] / fa)


def direction_delta(direction, step, camera=None):
    """[dx, dy, dz] in booth metres for a picture direction (02 section 4.2): left and right along the camera's right vector on the
    floor, forward (towards the camera) and back along the view direction on the floor, up and down along z. Without a usable
    camera the booth axes are used: right +x, forward -y."""
    if direction in ('up', 'down'):
        return [0.0, 0.0, round(step if direction == 'up' else -step, 6)]
    axes = ground_axes(camera) or ((1.0, 0.0), (0.0, -1.0))
    (rx, ry), (tx, ty) = axes
    sign = {'right': 1.0, 'left': -1.0, 'forward': 1.0, 'back': -1.0}[direction]
    ux, uy = (rx, ry) if direction in ('left', 'right') else (tx, ty)
    return [round(sign * step * ux, 6) + 0.0, round(sign * step * uy, 6) + 0.0, 0.0]


def direction_of(dx, dy, camera=None):
    """The picture direction (left, right, forward, back) that best matches a booth displacement on the floor."""
    (rx, ry), (tx, ty) = ground_axes(camera) or ((1.0, 0.0), (0.0, -1.0))
    r, t = dx * rx + dy * ry, dx * tx + dy * ty
    if abs(r) >= abs(t):
        return 'right' if r > 0 else 'left'
    return 'forward' if t > 0 else 'back'


# -- what the compiler built

def text_points(comp, rot):
    """The corners of a text component, which has no mesh: a centred rectangle in the local XY plane (Blender text), as wide as
    about 0.6 x height x number of characters and as high as the font size, turned by the component's rotation."""
    try:
        p = comp['params']
        size, n = float(p['size']), max(1, len(str(p.get('body') or '')))
        extrude = float(p.get('extrude') or 0.0)
        if not (math.isfinite(size) and size > 0 and math.isfinite(extrude)):
            raise ValueError('bad text size')
        w, h = 0.6 * size * n, size
        return [validate_spec.transform_point((sx * w / 2, sy * h / 2, z), comp['location'], rot)
                for sx in (-1, 1) for sy in (-1, 1) for z in (0.0, extrude)]
    except (KeyError, TypeError, ValueError, OverflowError):
        return [[float(v) for v in comp['location']]]


def component_points(comp):
    """World points (metres) of a component: its real mesh vertices with the full rotation; one point where there is no mesh
    (text) or the mesh cannot be built."""
    rot = comp.get('rotation_deg') or [0.0, 0.0, 0.0]
    if comp.get('kind') == 'text':
        return text_points(comp, rot)
    try:
        verts, _ = validate_spec.mesh_for(comp)
        return [validate_spec.transform_point(v, comp['location'], rot) for v in verts]
    except (KeyError, TypeError, ValueError, OverflowError):
        return [[float(v) for v in comp['location']]]


def component_owner(comp, known, longest_first):
    """The element a component belongs to: its `group` when that is an element id (design_compile sets it), else the element with
    the longest id that is the component's id or a prefix of it up to '_'."""
    if comp.get('group') in known:
        return comp['group']
    return next((e for e in longest_first if comp['id'] == e or comp['id'].startswith(e + '_')), None)


class SpecView:
    """What the compiler built for each element of a design, measured in the compiled spec (metres, and pixels through the spec's
    camera). `all_points` are all mesh points of the element (what the picture shows); `body_points` leave out posts and floor
    (what tells the element's own height)."""

    def __init__(self, spec, element_ids):
        self.camera = spec['camera']
        self.width, self.height = int(spec['source']['width']), int(spec['source']['height'])
        known = set(element_ids)
        longest = sorted(known, key=len, reverse=True)
        self.all_points, self.body_points, self.parts, self.zf = {}, {}, {}, 0.0
        for comp in spec.get('components', []):
            if comp.get('role') == 'floor':
                try:
                    self.zf = max(self.zf, float(comp['location'][2]) + float(comp['params']['size'][2]) / 2)
                except (KeyError, TypeError, ValueError, IndexError):
                    pass
            owner = component_owner(comp, known, longest)
            if owner is None:
                continue
            pts = component_points(comp)
            self.parts.setdefault(owner, []).append((comp, pts))
            self.all_points.setdefault(owner, []).extend(pts)
            if comp.get('role') not in BODY_SKIP_ROLES:
                self.body_points.setdefault(owner, []).extend(pts)

    def _body(self, eid):
        return self.body_points.get(eid) or self.all_points.get(eid)

    def project(self, point):
        try:
            return camera_math.project(list(point), self.camera, self.width, self.height)
        except (ValueError, ZeroDivisionError):
            return None

    def box_px_raw(self, eid, pred=None):
        """[x0, y0, x1, y1] of the element in the render, in picture pixels, not clipped (it may reach far outside), or None.
        pred(component) limits it to some of the element's components (those size_m controls, say)."""
        pts = self.all_points.get(eid, ()) if pred is None else [pt for c, ps in self.parts.get(eid, ()) if pred(c) for pt in ps]
        uv = [uvz for uvz in (self.project(p) for p in pts) if uvz is not None]
        if not uv:
            return None
        return [min(u for u, _, _ in uv), min(v for _, v, _ in uv), max(u for u, _, _ in uv), max(v for _, v, _ in uv)]

    def box_px(self, eid):
        """The render box clipped to the picture [0, W] x [0, H] (what a critic can box), or None when nothing of it is in the picture."""
        raw = self.box_px_raw(eid)
        if raw is None:
            return None
        box = [min(max(raw[0], 0.0), self.width), min(max(raw[1], 0.0), self.height),
               min(max(raw[2], 0.0), self.width), min(max(raw[3], 0.0), self.height)]
        return box if box[2] > box[0] and box[3] > box[1] else None

    def centroid(self, eid):
        pts = self._body(eid)
        if not pts:
            return None
        return [sum(p[i] for p in pts) / len(pts) for i in range(3)]

    def depth(self, eid):
        c = self.centroid(eid)
        uvz = self.project(c) if c else None
        return None if uvz is None else uvz[2]

    def top(self, eid):
        pts = self._body(eid)
        return max(p[2] for p in pts) if pts else None

    def bottom(self, eid):
        pts = self._body(eid)
        return min(p[2] for p in pts) if pts else None

    def bottom_rel(self, eid):
        """The element's lowest point above the floor of the booth (the platform top), the zero of z_m."""
        b = self.bottom(eid)
        return None if b is None else b - self.zf

    def level_rel(self, eid, kind):
        """What z_m of the element means, as built: the top of the fixtures of a hanging kind, the bottom of everything else."""
        z = self.top(eid) if kind in HANGING_KINDS else self.bottom(eid)
        return None if z is None else z - self.zf

    def dims(self, eid):
        """(x, y, z) extents of the element in the booth frame, or None."""
        pts = self._body(eid)
        if not pts:
            return None
        return tuple(max(p[i] for p in pts) - min(p[i] for p in pts) for i in range(3))


def _valid_box(box):
    return (isinstance(box, (list, tuple)) and len(box) == 4 and all(_num(v) for v in box) and box[2] > box[0] and box[3] > box[1])


def _usable_edges(view, eid, bbox, axis, raw=None):
    """[(render edge, critic edge)] of the two edges across `axis` ('x' or 'y') that neither box has cut at the frame.
    raw: the render box to use (default: the whole element's)."""
    raw = raw if raw is not None else view.box_px_raw(eid)
    if raw is None:
        return []
    size = view.width if axis == 'x' else view.height
    lo, hi = (0, 2) if axis == 'x' else (1, 3)
    pairs = []
    if 0.0 <= raw[lo] <= size and bbox[lo] > EDGE_MARGIN_PX:
        pairs.append((raw[lo], bbox[lo]))
    if 0.0 <= raw[hi] <= size and bbox[hi] < size - EDGE_MARGIN_PX:
        pairs.append((raw[hi], bbox[hi]))
    return pairs


def pixel_move_m(view, eid, direction, bbox, camera, pred=None):
    """Metres to move the element in `direction` so that its render box meets `bbox`, from the offset of the edges in pixels:
    d = px * Z / f. Only edges that neither the render box (clipped by the picture) nor the critic's box has cut are compared; the
    offset is the mean over the edges that are left. Forward and back move the element along the floor, so their vertical offset is
    converted through the camera (the pixels one metre of that move is worth); up and down are divided by the camera up vector's
    vertical part (not below 0.2). None, meaning the small step, when no edge is left, when the offset points against `direction`,
    or when there is no usable depth."""
    z = view.depth(eid)
    raw = view.box_px_raw(eid, pred)
    if z is None or z <= 0 or raw is None:
        return None
    f = camera_math.focal_px(camera, view.width)
    axis = 'x' if direction in ('left', 'right') else 'y'
    pairs = _usable_edges(view, eid, bbox, axis, raw)
    if not pairs:
        return None
    px = sum(c - r for r, c in pairs) / len(pairs)               # + to the right / down the picture
    if direction in ('left', 'right'):
        expected = 1.0 if direction == 'right' else -1.0
        per_px = z / f
    elif direction in ('up', 'down'):
        expected = -1.0 if direction == 'up' else 1.0
        per_px = z / f / max(abs(camera_math.axes(camera)[2][2]), 0.2)
    else:
        toward = (ground_axes(camera) or ((1.0, 0.0), (0.0, -1.0)))[1]
        c = view.centroid(eid)
        p0, p1 = view.project(c), view.project([c[0] + 0.1 * toward[0], c[1] + 0.1 * toward[1], c[2]])
        if p0 is None or p1 is None:
            return None
        per_m = (p1[1] - p0[1]) / 0.1                              # pixels down the picture per metre towards the camera
        if abs(per_m) < 2.0:
            return None
        expected = (1.0 if per_m > 0 else -1.0) * (1.0 if direction == 'forward' else -1.0)
        per_px = 1.0 / abs(per_m)
    if px * expected < 0:
        return None
    return abs(px) * per_px


def vertical_delta(design, el, view, dz):
    """-> (dz, None) when the element can move by dz from where it really is, or (None, reason): not below the floor, not above the
    height limit, and the compiled spec must show where it is."""
    eid = el['id']
    if view is None:
        return None, 'a vertical move needs the compiled spec (the height it starts from)'
    level, top = view.level_rel(eid, el['kind']), view.top(eid)
    if level is None or top is None:
        return None, 'the compiler built nothing for this element: no height to start from'
    target = level + dz
    if target < -1e-6:
        return None, f'moving by {dz:+.2f} m would put the element below the floor'
    limit = design['form'].get('height_limit_m')
    if _num(limit) and top + dz > limit + 1e-6:
        return None, f'moving by {dz:+.2f} m would pass the height limit {limit:g} m'
    return round(dz, 4) + 0.0, None


def ratio_agrees(k, sign):
    """Does a size ratio agree with the critic's direction (sign +1 bigger, -1 smaller)? Used as it is when it does (no dead zone);
    exactly 1 (within RATIO_EXACT) or the wrong side is no information."""
    return k > 1.0 + RATIO_EXACT if sign > 0 else k < 1.0 - RATIO_EXACT


def _clamp_ratio(k):
    return min(max(k, SIZE_RATIO_RANGE[0]), SIZE_RATIO_RANGE[1])


def _extents(points):
    return tuple(max(p[i] for p in points) - min(p[i] for p in points) for i in range(3)) if points else None


def _turned(el):
    yaw = el.get('yaw_deg')
    return el.get('side') in ('left', 'right') or (el.get('side') in (None, 'centre') and _num(yaw) and abs(abs(yaw) % 180 - 90) < 45)


def _lit_panel_yaw(el, view):
    """The yaw the compiler turned the lit_panel by (read from its parts; the design's yaw_deg when nothing was built)."""
    for comp, _ in (view.parts.get(el['id']) or []) if view is not None else []:
        if comp.get('role') == 'lit_panel':
            return float((comp.get('rotation_deg') or [0.0, 0.0, 0.0])[2])
    return float(el['yaw_deg']) if _num(el.get('yaw_deg')) else 0.0


def _clamp_lit_panel(design, el, view, new):
    """A lit_panel must fit the footprint when turned (the compiler refuses it otherwise): its width stops where the turned panel
    would touch the footprint, the thickness being new[1] (0 when it is not set)."""
    if new[0] is None or view is None:
        return new
    fw, fd = footprint(design)
    th = math.radians(_lit_panel_yaw(el, view))
    c, s = abs(math.cos(th)), abs(math.sin(th))
    t = new[1] or 0.0
    limit = max(fw, fd)                       # check_lit_panel: a panel is never wider than the longer side of the footprint
    if c > 1e-6:                              # motifs.lit_panel: the turned panel must lie inside the footprint
        limit = min(limit, (fw - s * t) / c)
    if s > 1e-6:
        limit = min(limit, (fd - c * t) / s)
    limit = math.floor(limit * 1e4 + 1e-6) / 1e4          # rounded down: never past the compiler's tolerance
    return [min(new[0], limit)] + list(new[1:])


def spec_size(el, view):
    """What the element's size_m controls, as built, in the order of size_m: [width, depth, height] (a ring: [None, band, height]).
    It is measured on the parts that size_m governs: the pot of a planter (not the plants), the body of a counter (not its
    overhanging top), the ring without its posts, text left out. None for a dimension that cannot be told (the width of a row of
    pots), None when nothing was built."""
    eid, kind = el['id'], el['kind']
    parts = view.parts.get(eid) if view else None
    if not parts:
        return None
    if kind == 'ring':
        for comp, _ in parts:
            if comp.get('role') == 'ring':
                prm = comp.get('params') or {}
                try:
                    return [None, round((prm['outer_size'][0] - prm['inner_size'][0]) / 2, 4), round(float(prm['height']), 4)]
                except (KeyError, TypeError, IndexError):
                    return None
        return None
    skip = BODY_SKIP_ROLES + TEXT_ROLES
    ex = None
    if kind == 'lit_panel':      # the panel's own width, thickness and height, whichever way it is turned (the compiler turns the whole prism)
        pts = [pt for c, ps in parts if c.get('role') == 'lit_panel' for pt in ps]
        if not pts:
            return None
        th = math.radians(_lit_panel_yaw(el, view))
        c, s = math.cos(th), math.sin(th)
        # in the panel's own frame (turned back by the yaw): u across the panel, n through it, z up
        across = [p[0] * c + p[1] * s for p in pts]
        through = [-p[0] * s + p[1] * c for p in pts]
        height = [p[2] for p in pts]
        return [round(max(v) - min(v), 4) for v in (across, through, height)]
    if kind == 'canopy_frame':   # the frame itself (width, depth, fascia height); not the deeper sign, the posts or the letters
        ex = _extents([pt for c, pts in parts if c.get('role') == 'canopy_frame' for pt in pts])
        return None if ex is None else [round(v, 4) for v in ex]
    if kind == 'planter':        # one pot of the row: a row of two pots has the size of one
        pots = sorted(((str(c.get('id', '')), pts) for c, pts in parts if c.get('role') == 'planter'), key=lambda t: t[0])
        ex = _extents(pots[0][1]) if pots else None
    elif kind == 'counter':
        bx = _extents([pt for c, pts in parts if str(c.get('id', '')).endswith('_body') for pt in pts])
        ev = _extents([pt for c, pts in parts if c.get('role') == 'counter' for pt in pts])
        ex = (bx[0], bx[1], ev[2]) if bx and ev else None
    if ex is None:                               # any other kind, or parts without the roles the compiler gives them
        ex = _extents([pt for c, pts in parts if c.get('role') not in skip for pt in pts]) or _extents(view._body(eid))
    if ex is None:
        return None
    wx, wy, wz = ex
    w, dep = (wy, wx) if _turned(el) else (wx, wy)
    return [round(w, 4), round(dep, 4), round(wz, 4)]


def main_body(el):
    """The predicate of the components that are the element's main body: a ring's band (never its posts), otherwise everything but
    posts, floor and text."""
    if el['kind'] == 'ring':
        return lambda c: c.get('role') == 'ring'
    return lambda c: c.get('role') not in BODY_SKIP_ROLES + TEXT_ROLES


def move_bodies(el):
    """The parts of an element that a move may be measured on, the preferred one first (02 4.3, sixth note): a round table moves by
    its top (the critic is told to box the top), then by the whole table; a planter by the whole pot with its plants, then by the pot
    alone; any other element by its main body only (never all its parts, with their posts and letters). A predicate of None means all parts."""
    kind = el['kind']
    if kind == 'round_table':
        return [('top', lambda c: str(c.get('id', '')).endswith('_top')), ('whole table', main_body(el))]
    if kind == 'planter':
        return [('pot and plants', main_body(el)), ('pot', lambda c: c.get('role') == 'planter')]
    return [('main body', main_body(el))]               # no fallback to all parts (posts, letters): a box that size is a size problem


def choose_move_body(view, el, direction, bbox):
    """Which part of the element the critic's box is compared with for a move in `direction`. -> (True, predicate) or (False, reason).
    The length of the critic's box over the length of the render part, along the axis of the move (x for left and right, y for the
    others), must lie in MOVE_RATIO_RANGE; if it does not, the next part is tried; if no part fits, the boxes are of different sizes and
    the finding is a size problem, not a move. Where no length ratio can be told (an edge cut by the picture) the first part is used
    as it always was."""
    eid = el['id']
    axis = 'x' if direction in ('left', 'right') else 'y'
    seen = []
    for _, pred in move_bodies(el):
        raw = view.box_px_raw(eid, pred)
        if raw is None:
            continue
        k = _axis_ratio(view, eid, bbox, axis, raw, clamp=False)
        if k is None or MOVE_RATIO_RANGE[0] <= k <= MOVE_RATIO_RANGE[1]:
            return True, pred
        seen.append(k)
    if seen:
        nearest = min(seen, key=lambda k: abs(math.log(k)))
        return False, f'{SIZE_PROBLEM} (the critic box is {nearest:.2f} times the render box along the move)'
    return True, main_body(el)


def floor_standing(el, view):
    """Does the compiler stand this element on the floor? A kind that stands on the floor, with no z_m in the design, whose compiled
    bottom is on the platform top."""
    if el['kind'] not in FLOOR_STANDING_KINDS or _num(el.get('z_m')) or view is None:
        return False
    bottom = view.bottom_rel(el['id'])
    return bottom is not None and bottom <= FLOOR_STANDING_TOLERANCE_M


def ratio_box(el, view):
    """-> (render box of the parts size_m controls, ratio of the width usable, ratio of the height usable). The critic's box shows the
    element; only a dimension that sets the width (or height) of the box compared may be scaled by the ratio of the two boxes."""
    eid, kind = el['id'], el['kind']
    if kind == 'ring':           # the band without its posts; the band width does not set the width of the ring, its height not the box's
        return view.box_px_raw(eid, lambda c: c.get('role') == 'ring'), False, False
    if kind == 'planter':        # the pot; the plants make the critic's box taller, so the pot height is not read from it
        return view.box_px_raw(eid, lambda c: c.get('role') == 'planter'), int(el.get('count') or 1) == 1, False
    if kind in ('canopy_frame', 'lit_panel'):   # canopy: the frame with its sign, never the posts or letters; the fascia height does not set
        return view.box_px_raw(eid, main_body(el)), True, False     # the box height. A lit panel is placed like a partition: width by ratio
    return view.box_px_raw(eid, main_body(el)), True, True


def _axis_ratio(view, eid, bbox, axis, raw=None, clamp=True):
    """Width (axis 'x') or height ('y') of the critic's box over the render box, or None when an edge of either is cut by the frame.
    clamp=False gives the ratio as it is (the move check looks at it, a resize uses it clamped)."""
    pairs = _usable_edges(view, eid, bbox, axis, raw)
    if len(pairs) < 2:
        return None
    (r0, c0), (r1, c1) = pairs
    if not (r1 - r0 > 2 and c1 - c0 > 0):
        return None
    k = (c1 - c0) / (r1 - r0)
    return _clamp_ratio(k) if clamp else k


def resize_edit(design, el, fid, direction, bbox, view, scale, count_changed=False):
    """-> (edit, None) or (None, reason)."""
    eid, kind = el['id'], el['kind']
    sign = 1 if direction == 'bigger' else -1
    fallback = FALLBACK_SIZE_STEP ** (sign * scale)
    kh = kv = None
    raw, use_w, use_h = ratio_box(el, view) if view is not None else (None, False, False)
    boxed = raw is not None and _valid_box(bbox)
    if boxed:
        kh = _axis_ratio(view, eid, bbox, 'x', raw) if (use_w and not count_changed) else None
        kv = _axis_ratio(view, eid, bbox, 'y', raw) if use_h else None

    def pick(k):      # a ratio only when it agrees with the critic's direction; any other axis takes the small step in that direction
        return k if (k is not None and ratio_agrees(k, sign)) else fallback
    kh, kv = pick(kh), pick(kv)
    if kind == 'round_table':
        r = (el.get('params') or {}).get('radius_m')
        if not _num(r) and view is not None and view.dims(eid):
            r = view.dims(eid)[0] / 2
        if not _num(r) or r <= 0:
            return None, 'a round table whose radius is not known'
        return make_edit('resize', eid, size_m=[round(2 * r * kh, 4), None, None], finding_id=fid), None
    size = list(el.get('size_m') or [None, None, None])
    if kind == 'ring':
        built = spec_size(el, view) if view is not None else None
        if built is None and all(v is None for v in size):
            return None, 'a ring without size_m and without a compiled spec to take band and height from'
        size = [None] + [size[i] if size[i] is not None else (built[i] if built else None) for i in (1, 2)]
        factors = (1.0, kh, kv)
    else:
        if all(v is None for v in size):
            if kind not in SIZE_FROM_SPEC:
                return None, NO_SIZE_REASON.get(kind, f'no resize rule for kind {kind}')
            size = spec_size(el, view)
            if size is None:
                return None, 'wrong_size without a size_m and without a compiled spec to take the size from'
        elif view is not None and kind in SIZE_FROM_SPEC and any(v is None for v in size):
            built = spec_size(el, view)
            if built:
                size = [built[i] if v is None else v for i, v in enumerate(size)]
        factors = (kh, fallback, kv)           # the width ratio changes size_m[0] only; the depth is not set by the box width
    new = [None if v is None else (round(v * k, 4) if k != 1.0 else v) for v, k in zip(size, factors)]
    if kind == 'lit_panel':
        new = _clamp_lit_panel(design, el, view, new)
    if kind == 'canopy_frame':             # the frame never leaves the footprint: its width and depth stop at the footprint's
        limits = footprint(design)
        new = [n if (n is None or i > 1) else min(n, limits[i]) for i, n in enumerate(new)]
    given = list(el.get('size_m') or [None, None, None])
    new = [None if (g is None and k == 1.0) else n for g, k, n in zip(given, factors, new)]
    if all(n is None for n in new):
        return None, 'no size to scale'
    return make_edit('resize', eid, size_m=new, finding_id=fid), None


def check_resizes(design, old_view, new_view, edit_list, findings):
    """Compare the controlled sizes of the resized elements before (old_view) and after (new_view, the recompiled new design).
    -> (kept edits, dropped = [{finding_id, reason}]). A resize is dropped when the built size did not change (a snapped LED wall,
    a size the compiler ignores) or changed against the direction the critic gave. Other edits are kept without a look."""
    by_id = {f.get('id'): f for f in findings}
    kept, dropped = [], []
    for e in edit_list:
        if e['op'] != 'resize':
            kept.append(e)
            continue
        el = find_element(design, e['element_id'])
        f = by_id.get(e['finding_id']) or {}
        old = spec_size(el, old_view) if old_view is not None else None
        new = spec_size(el, new_view) if new_view is not None else None
        if old is None:
            kept.append(e)
            continue
        if new is None:
            dropped.append({'finding_id': e['finding_id'], 'reason': f"resize of {el['id']}: the recompiled design has nothing built for it"})
            continue
        if el['kind'] == 'round_table':
            old, new = old[:1], new[:1]
        deltas = [n - o for o, n in zip(old, new) if o is not None and n is not None]
        want = {'bigger': 1.0, 'smaller': -1.0}.get(f.get('direction'))
        if not any(abs(d) > SIZE_TOLERANCE_M for d in deltas):
            dropped.append({'finding_id': e['finding_id'], 'reason': f"resize of {el['id']}: no effect, the recompiled geometry is unchanged"})
        elif want is not None and (any(d * want < -SIZE_TOLERANCE_M for d in deltas) or not any(d * want > SIZE_TOLERANCE_M for d in deltas)):
            dropped.append({'finding_id': e['finding_id'], 'reason': f"resize of {el['id']}: the recompiled size changed the wrong "
                                                                      f"direction ({f.get('direction')} was asked)"})
        else:
            kept.append(e)
    return kept, dropped


def main_body_box(view, el):
    """((x0, y0, z0), (x1, y1, z1)) of the element's main body in the compiled spec (a ring: the band; a header: the band body;
    otherwise everything but posts, floor and text), or None when nothing was built."""
    parts = view.parts.get(el['id']) if view is not None else None
    if not parts:
        return None
    pred = main_body(el)
    pts = [pt for c, ps in parts if pred(c) for pt in ps] or view.body_points.get(el['id']) or view.all_points.get(el['id'])
    if not pts:
        return None
    return tuple(min(p[i] for p in pts) for i in range(3)), tuple(max(p[i] for p in pts) for i in range(3))


def built_units(view, el):
    """How many units the compiler built for an element whose count sets them: the fixtures of spotlights, the pots of a planter, the
    chairs of a seating group. None for other kinds (they do not read a count) or when the parts carry no roles (the stand-in)."""
    parts = view.parts.get(el['id']) if view is not None else None
    if not parts:
        return None
    kind, eid = el['kind'], el['id']
    if kind == 'spotlights':
        n = sum(1 for c, _ in parts if c.get('role') == 'spotlight')
    elif kind == 'planter':
        n = sum(1 for c, _ in parts if c.get('role') == 'planter')
    elif kind == 'seating_group':
        found = {m.group(1) for c, _ in parts for m in [re.match(re.escape(eid) + r'_chair_(\d+)_', str(c.get('id', '')))] if m}
        n = len(found)
    else:
        return None
    return n or None


def check_single(design, old_view, new_view, edit, finding):
    """Judge one geometry edit on the compile of the winner's design with just that edit (new_view) against the winner's (old_view).
    -> (the edit as it is recorded, None) or (None, {finding_id, op, reason}). A move comes back with delta_m = the measured change
    of the main body: horizontal the centre of its box, vertical its bottom."""
    if edit['op'] not in GEOMETRY_OPS:
        return edit, None
    el = find_element(design, edit['element_id'])
    eid = el['id']

    def drop(why):
        return None, {'finding_id': edit['finding_id'], 'op': edit['op'], 'reason': f'{eid}: {why}'}
    old, new = main_body_box(old_view, el), main_body_box(new_view, el)
    if old is None:
        return drop("no effect, nothing was built for it in the winner's compiled design")
    if new is None:
        return drop('element lost, the compiler built nothing for it after the edit')
    if edit['op'] == 'set_count':
        before, after = built_units(old_view, el), built_units(new_view, el)
        if before is not None and after is not None:
            want = edit['delta_m'][0]
            if after == before:
                return drop(f'no effect, the compiler built {after} unit(s) before and after')
            if (after - before) * want < 0:
                return drop(f'the compiled count went the wrong way ({before} to {after}, {want:+g} was asked)')
            return edit, None
    change = max(abs(n - o) for n, o in zip(new[0] + new[1], old[0] + old[1]))
    if change < SIZE_TOLERANCE_M:
        return drop('no effect, the recompiled main body is unchanged')
    if edit['op'] == 'resize':
        _, wrong = check_resizes(design, old_view, new_view, [edit], [finding] if finding else [])
        return (None, dict(wrong[0], op='resize')) if wrong else (edit, None)
    if edit['op'] == 'move':
        centre = lambda box: [(box[0][i] + box[1][i]) / 2 for i in (0, 1)]       # noqa: E731
        (ox, oy), (nx, ny) = centre(old), centre(new)
        return dict(edit, delta_m=[round(nx - ox, 4) + 0.0, round(ny - oy, 4) + 0.0, round(new[0][2] - old[0][2], 4) + 0.0]), None
    return edit, None


def check_geometry(design, old_view, new_view, edit_list, findings):
    """check_single for each edit of a list against one pair of views (new_view compiled from just those edits, or from one edit).
    -> (kept, dropped)."""
    by_id = {f.get('id'): f for f in findings}
    kept, dropped = [], []
    for e in edit_list:
        rec, drop = check_single(design, old_view, new_view, e, by_id.get(e['finding_id']))
        (dropped if drop else kept).append(drop or rec)
    return kept, dropped


def measure_edits(design, old_view, edit_list, findings, compile_fn, cache=None):
    """Measure every geometry edit on its own: apply it alone to `design`, compile once (compile_fn(design) -> spec, may raise), and
    judge it with check_single. -> (survivors, recorded, dropped): survivors are the edits to compose into a candidate (as the fixer
    made them), recorded {finding_id: the edit with its measured delta_m}, dropped [{finding_id, op, reason}]. Edits of other kinds
    pass. cache: results kept between calls for the same design (a variant shares its edits with another). A compile_fn that
    returns None cannot measure: the edit stays."""
    by_id = {f.get('id'): f for f in findings}
    cache = {} if cache is None else cache
    survivors, recorded, dropped = [], {}, []
    ids = [e['id'] for e in design['elements']]
    for e in edit_list:
        if e['op'] not in GEOMETRY_OPS:
            survivors.append(e)
            continue
        key = json.dumps(e, sort_keys=True)
        if key not in cache:
            try:
                alone = apply_edit(design, e, view=old_view)
            except EditError as ex:
                cache[key] = (None, {'finding_id': e['finding_id'], 'op': e['op'], 'reason': f"{e['element_id']}: rejected: {ex}"})
            else:
                try:
                    spec = compile_fn(alone)
                except Exception as ex:                         # whatever the compiler says: this edit alone does not build
                    cache[key] = (None, {'finding_id': e['finding_id'], 'op': e['op'],
                                         'reason': f"{e['element_id']}: the design does not compile with it ({str(ex)})"})
                else:
                    cache[key] = (e, None) if spec is None else check_single(design, old_view, SpecView(spec, ids), e, by_id.get(e['finding_id']))
        rec, drop = cache[key]
        if drop:
            dropped.append(drop)
        else:
            survivors.append(e)
            recorded[e['finding_id']] = rec
    return survivors, recorded, dropped


def findings_to_edits(design, findings, step_m=None, scale=1.0, camera=None, spec=None, only=None):
    """Turn critic.v1 findings into fixer.v1 edits. -> (edits, skipped) with skipped = [{finding_id, reason}].

    spec: the compiled spec of the design (a dict, or a SpecView) - without it heights, pixel boxes and compiled sizes are unknown.
    only: a set of finding types to handle (GEOMETRY_TYPES, COLOUR_TYPES); the others are left alone, not skipped.
    misplaced -> move: sized from bbox_px against the render box (see the module text), else step_m * scale if given, else 0.05 m
    (medium, low) or 0.1 m (high); left, right, forward and back are converted with the camera (direction_delta); up and down
    start from the real height. extra -> remove; wrong_size -> resize (pixel ratio, else 1.1 per step); wrong_colour -> recolour
    darker or lighter; wrong_count -> set_count +1 or -1. missing and wrong_kind carry no kind in critic.v1, so no edit is made for
    them; camera_off cannot be fixed in the design."""
    found, skipped = [], []
    counted = {f.get('element_id') for f in findings if f.get('type') == 'wrong_count'}
    view = spec if (spec is None or isinstance(spec, SpecView)) else SpecView(spec, [e['id'] for e in design['elements']])
    camera = camera or (view.camera if view is not None else None)

    def skip(f, why):
        skipped.append({'finding_id': f.get('id'), 'reason': why})

    for f in findings:
        fid, eid, typ, direction = f.get('id') or 'finding', f.get('element_id'), f.get('type'), f.get('direction')
        if only is not None and typ not in only:
            continue
        try:
            el = find_element(design, eid) if eid else None
        except EditError:
            skip(f, f'no element {eid!r}')
            continue
        if typ in ('missing', 'wrong_kind'):
            skip(f, f'{typ}: the finding names no kind to add or switch to')
        elif typ == 'camera_off' or el is None:
            skip(f, 'not an element edit')
        elif typ == 'extra':
            found.append(make_edit('remove', eid, finding_id=fid))
        elif typ == 'misplaced':
            if direction not in DIRECTIONS_MOVE:
                skip(f, 'misplaced without a usable direction')
                continue
            if direction in ('up', 'down') and floor_standing(el, view):
                skip(f, FLOOR_STANDING)
                continue
            bbox = f.get('bbox_px')
            m = None
            if view is not None and _valid_box(bbox) and camera is not None:
                try:
                    ok, pred = choose_move_body(view, el, direction, bbox)
                    if not ok:
                        skip(f, pred)
                        continue
                    m = pixel_move_m(view, eid, direction, bbox, camera, pred)
                except (ValueError, KeyError, TypeError, ZeroDivisionError):
                    m = None
            if m is None:
                m = (step_m if step_m is not None else (LARGE_STEP_M if f.get('severity') == 'high' else SMALL_STEP_M)) * scale
            else:
                m = min(max(m, MIN_MOVE_M), MAX_MOVE_M)
            wall = el['kind'] in WALL_KINDS and el.get('side') in SIDES and not el.get('centre_m')
            if el['kind'] == 'wall' and direction in ('up', 'down'):      # the shell wall: the compiler ignores its z_m
                skip(f, 'a shell wall is placed by the compiler from its side: it does not move it up or down')
                continue
            if direction in ('up', 'down'):
                dz, why = vertical_delta(design, el, view, m if direction == 'up' else -m)
                if why:
                    skip(f, why)
                    continue
                delta = [0.0, 0.0, dz]       # the real move in metres; apply_edit(view=...) turns it into z_m from the real height
            else:
                delta = direction_delta(direction, m, camera)
                if wall:        # a wall element only slides along its wall: record that part, nothing that is not done
                    keep = 0 if el['side'] in ('front', 'back') else 1
                    delta = [delta[i] if i == keep else 0.0 for i in range(3)]
                    if abs(delta[keep]) < 1e-9:
                        skip(f, f"a {el['kind']} on a wall only slides along it, and this direction is across the wall")
                        continue
            found.append(make_edit('move', eid, delta_m=delta, finding_id=fid))
        elif typ == 'wrong_size':
            if direction not in ('bigger', 'smaller'):
                skip(f, 'wrong_size without a direction')
                continue
            edit, why = resize_edit(design, el, fid, direction, f.get('bbox_px'), view, scale, count_changed=eid in counted)
            if why:
                skip(f, why)
            else:
                found.append(edit)
        elif typ == 'wrong_colour':
            colour = current_colour(design, el)
            if direction not in ('darker', 'lighter') or not colour:
                skip(f, 'wrong_colour without a direction or without a known colour')
                continue
            found.append(make_edit('recolour', eid, color_hex=shade(colour, direction, SHADE_STEP * scale), finding_id=fid))
        elif typ == 'wrong_count':
            if direction not in ('bigger', 'smaller'):
                skip(f, 'wrong_count without a direction')
                continue
            found.append(make_edit('set_count', eid, delta_m=[1 if direction == 'bigger' else -1, 0, 0], finding_id=fid))
        else:
            skip(f, f'unknown finding type {typ!r}')
    return found, skipped


def _round_floats(obj):
    if isinstance(obj, float):
        return round(obj, 4) + 0.0
    if isinstance(obj, dict):
        return {k: _round_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_round_floats(v) for v in obj]
    return obj


def design_hash(design):
    """A hash of a design that ignores the order of keys and float noise below 0.1 mm: two designs with the same hash are the same."""
    text = json.dumps(_round_floats(design), sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def sort_findings(findings):
    """High severity first, then by id: the order in which candidates take findings."""
    return sorted(findings, key=lambda f: (SEVERITY_RANK.get(f.get('severity'), 3), str(f.get('id'))))


# -- the reader's frame (02 section 4.1): is the whole stand turned a quarter off?

TURN_SIDE = {'back': 'left', 'left': 'front', 'front': 'right', 'right': 'back'}      # one quarter turn counter-clockwise (as yaw)
YAW_OUT = {'back': 180.0, 'front': 0.0, 'left': -90.0, 'right': 90.0}                 # yaw of a piece facing out through that side
OVERHEAD = ('header_band', 'ring', 'spotlights', 'canopy_frame')
FRAME_ORIGINAL_BELOW = 0.3          # the design as read must score below this ...
FRAME_BEST_AT_LEAST = 0.5           # ... the best turn at least this ...
FRAME_GAIN_AT_LEAST = 0.3           # ... and at least this much above it (set beforehand from the nine first answers of anchor two)
FRAME_EVIDENCE_SHARE = 0.5          # at least this share of the elements needs a usable evidence box
FRAME_TOLERANCE = 1e-9


def _norm_yaw(deg):
    y = (float(deg) + 180.0) % 360.0 - 180.0
    return round(180.0 if y == -180.0 else y, 6) + 0.0


def _turn_corner(corner):
    first, second = (TURN_SIDE[part] for part in corner.split('_'))
    return f'{first}_{second}' if first in ('front', 'back') else f'{second}_{first}'


SQUARE_KINDS = ('seating_group', 'chair')       # their footprint is square: only the facing matters, never the way they lie
FACING_KINDS = SQUARE_KINDS + ('lit_panel',)     # their builder turns the whole piece by the yaw (default: the entrance's), shape and facing together


def _canopy_sign_defaults(el, width, depth):
    """(corner, along_m) of the sign of a canopy_frame as the compiler resolves them (the defaults it fills in), or None."""
    try:
        sign = motifs._canopy_plan(el, (-width / 2, width / 2, -depth / 2, depth / 2))[0]['sign']
        return (sign['corner'], [sign['along'][0], sign['along'][1]]) if sign else None
    except Exception:                                   # a frame that does not plan is left as it is
        return None


def _turn_once(design, width, depth):
    """One quarter turn counter-clockwise about the footprint centre of a design whose footprint is width x depth (metres)."""
    new = copy.deepcopy(design)
    form = new['form']
    old_open = [s for s in SIDES if s in form['open_sides']]
    layout = new.get('layout')
    entrance = (layout or {}).get('entrance_side')
    effective = entrance if entrance in old_open else (old_open[0] if old_open else 'front')
    form['footprint_m'] = [form['footprint_m'][1], form['footprint_m'][0]]
    form['open_sides'] = [TURN_SIDE[s] for s in form['open_sides']]
    if entrance is not None or len(set(old_open)) != 1:        # the compiler's default entrance depends on the order of the sides: say it
        new['layout'] = dict(layout or {}, entrance_side=TURN_SIDE[effective])
    for el in new['elements']:
        kind, side = el['kind'], el.get('side')
        by_centre = kind not in WALL_KINDS and kind not in OVERHEAD and side not in SIDES
        sign_defaults = None
        if kind == 'canopy_frame' and (el.get('params') or {}).get('sign') is not None:
            sign_defaults = _canopy_sign_defaults(el, width, depth)     # before the frame is turned: the defaults of the design as given
        if by_centre and not el.get('centre_m') and kind not in FACING_KINDS and kind != 'round_table' and _num(el.get('yaw_deg')):
            # Placed by along alone (or by nothing), the compiler lays the piece with its width along x whatever the yaw says: the yaw
            # is only its facing. Rewrite it into the placement the compiler built - a yaw that lays it along x too (90 degrees less
            # for a yaw that would lay it along y) - and only then turn it.
            yaw = float(el['yaw_deg'])
            if abs(abs(yaw) % 180 - 90) < 45:
                el['yaw_deg'] = _norm_yaw(yaw - 90.0 if yaw > 0 else yaw + 90.0)
        if side in SIDES:
            el['side'] = TURN_SIDE[side]
            if _num(el.get('along')) and side in ('left', 'right'):     # the left and right walls run front to back, the front and back left to right
                el['along'] = round(1.0 - el['along'], 6) + 0.0
        if el.get('centre_m'):
            cx, cy = el['centre_m']
            el['centre_m'] = [-cy + 0.0, cx + 0.0]
        elif by_centre and _num(el.get('along')):                       # placed by along on the line through the middle: now by its centre
            el['centre_m'] = [0.0, round(-width / 2 + el['along'] * width, 6) + 0.0]
            el.pop('along')
        if _num(el.get('yaw_deg')):
            el['yaw_deg'] = _norm_yaw(el['yaw_deg'] + 90.0)
        elif by_centre and kind != 'round_table':
            # The compiler lays a piece with no yaw with its width along x, facing the entrance. Write that out, then turn it. A yaw of
            # +-90 (an entrance to the left or right) would lay the piece along y, so a piece that is not square takes 0 there: its
            # shape is kept, its facing is not (nothing can say "along x, facing sideways"). Square pieces keep their facing, and so does a
            # lit_panel, whose builder turns the whole prism by the yaw (default: the entrance's): shape and facing turn together.
            base = YAW_OUT[effective] if (kind in FACING_KINDS or effective in ('front', 'back')) else 0.0
            el['yaw_deg'] = _norm_yaw(base + 90.0)
        if kind == 'canopy_frame' and el.get('size_m'):
            w, d, h = (list(el['size_m']) + [None] * 3)[:3]
            el['size_m'] = [d, w, h]
        params = el.get('params')
        if params:
            if params.get('sides'):
                params['sides'] = [TURN_SIDE[s] for s in params['sides']]
            if params.get('band_m'):
                params['band_m'] = {TURN_SIDE[k]: v for k, v in params['band_m'].items()}
            sign = params.get('sign')
            if sign is not None:
                if sign_defaults:                                   # the compiler's defaults, written out before they are turned
                    if sign.get('corner') is None:
                        sign['corner'] = sign_defaults[0]
                    if sign.get('along_m') is None:
                        sign['along_m'] = list(sign_defaults[1])
                if sign.get('corner'):
                    sign['corner'] = _turn_corner(sign['corner'])
                if sign.get('along_m'):
                    sign['along_m'] = [sign['along_m'][1], sign['along_m'][0]]
    for req in new.get('requirements') or []:
        if req.get('side') in SIDES:
            req['side'] = TURN_SIDE[req['side']]
    return new


YAW_IN = {'back': 0.0, 'front': 180.0, 'left': 90.0, 'right': -90.0}                  # yaw of a piece facing into the stand from that side
CHECKED_WITH_WALLS = WALL_KINDS                      # wall-attached kinds are only checked in a square stand (their along is a fraction)
CHECKED_OVERHEAD = ('ring', 'canopy_frame')          # a header band and the lights hang from one another: not turn-proof, not checked
TURN_TOLERANCE_M = 0.01


def _is_floor(el):
    return el['kind'] not in WALL_KINDS and el['kind'] not in OVERHEAD


def _entrance_of(design):
    opened = [s for s in SIDES if s in design['form']['open_sides']]
    entrance = ((design.get('layout') or {}).get('entrance_side'))
    return opened, (entrance if entrance in opened else (opened[0] if opened else 'front'))


def _axis_facing(el, opened, entrance):
    """(axis, facing) of a floor piece as design_compile.place_floor decides them: the axis 'y' lays the width along x, 'x' along y; the
    facing is the yaw it is built with."""
    side, yaw = el.get('side'), el.get('yaw_deg')
    if side in SIDES:
        axis = 'y' if side in ('front', 'back') else 'x'
        facing = YAW_OUT[side] if side in opened else YAW_IN[side]
    elif el.get('centre_m'):
        facing = YAW_OUT[entrance]
        axis = 'x' if _num(yaw) and abs(abs(yaw) % 180 - 90) < 45 else 'y'
    else:
        axis, facing = 'y', YAW_OUT[entrance]            # no side, no centre: along the middle line, width along x whatever the yaw
    if _num(yaw):
        facing = float(yaw)
    return axis, facing


def _anchor_xy(view, el):
    """The (x, y) the compiler placed a floor piece at, read from what it built (so a piece it moved inside the footprint is where it
    really is): the centre of the table top, the seat, or the box of the main body (of the pots of a planter)."""
    parts = view.parts.get(el['id']) or []
    suffix = {'round_table': '_top', 'seating_group': '_table_top', 'chair': '_seat'}.get(el['kind'])
    if suffix:
        for comp, _ in parts:
            if str(comp.get('id', '')).endswith(suffix):
                return float(comp['location'][0]), float(comp['location'][1])
        return None
    pred = (lambda c: c.get('role') == 'planter') if el['kind'] == 'planter' else main_body(el)
    pts = [pt for c, ps in parts if pred(c) for pt in ps]
    if not pts:
        return None
    return ((min(p[0] for p in pts) + max(p[0] for p in pts)) / 2, (min(p[1] for p in pts) + max(p[1] for p in pts)) / 2)


def normalise_placements(design, spec):
    """-> a copy of the design in which every floor piece the compiler built is written as what it built (02 section 4.1, 2026-10-24):
    centre_m is the place it really stands at (the compiler may have moved it inside the footprint, laid it by its side, or put it on
    the middle line), side is 'centre', and yaw_deg is the yaw that lays it as it was laid (the facing of a lit panel, a seating group
    or a chair, which the compiler turns by the yaw; for any other piece the facing, or the facing less 90 degrees where the facing
    would lay it along the other axis - nothing can say "width along x, facing sideways"). along and offset_m are dropped. Wall-attached
    and overhead kinds, and pieces the compiler did not build, are left as they are. `spec` is the compile of this design."""
    out = copy.deepcopy(design)
    view = SpecView(spec, [e['id'] for e in out['elements']])
    opened, entrance = _entrance_of(out)
    for el in out['elements']:
        if not _is_floor(el):
            continue
        at = _anchor_xy(view, el)
        if at is None:
            continue
        axis, facing = _axis_facing(el, opened, entrance)
        el['centre_m'] = [round(at[0], 6) + 0.0, round(at[1], 6) + 0.0]
        el['side'] = 'centre'
        el.pop('along', None)
        el.pop('offset_m', None)
        if el['kind'] == 'round_table':
            continue
        lies = 'x' if abs(abs(facing) % 180 - 90) < 45 else 'y'
        if el['kind'] in FACING_KINDS or lies == axis:
            el['yaw_deg'] = _norm_yaw(facing)
            if lies != axis:        # the yaw would give the piece the other axis, which the compiler keeps the piece inside the footprint by
                el['side'] = 'front' if axis == 'y' else 'left'          # (the side only fixes the axis: the yaw is the facing)
        else:
            el['yaw_deg'] = _norm_yaw(facing - 90.0 if facing > 0 else facing + 90.0)
        if el['kind'] not in FACING_KINDS and min(abs(abs(el['yaw_deg']) % 180 - 45), abs(abs(el['yaw_deg']) % 180 - 135)) < 1.0:
            # a piece that does not turn with the entrance is written by the direction of its long side (0 or 90), never by an angle on
            # the compiler's own 45-degree boundary, where a turn by 90 degrees would not change the axis the compiler lays it on
            el['yaw_deg'] = 0.0 if axis == 'y' else 90.0
    return out


def rotate_design(design, quarter_turns, footprint=None, spec=None):
    """-> a copy of the design turned by `quarter_turns` quarter turns counter-clockwise (the direction of yaw) about the centre of the
    footprint; a negative or large number is taken modulo 4. footprint: (width, depth) of the design given, default its own.

    spec: the compile of the design given. With it, every floor piece is first written as the compiler actually built it
    (normalise_placements: centre_m and yaw_deg), which makes the turn exact whatever the placement words were; without it the words
    are turned as they stand (the cases the compiler reads its own way - a piece placed by along with a yaw, a piece with neither
    along nor centre, one the compiler moved inside the footprint - then turn wrongly).

    Sides are renamed (back to left to front to right to back, in `side`, `params.sides`, the keys of `params.band_m`, the corner of
    `params.sign`, `form.open_sides`, `layout.entrance_side`, `requirements[].side`); `along` becomes 1 - along when the new side
    runs the other way (the front and back run left to right, the left and right front to back); centre_m turns with the frame and
    yaw_deg gains 90 degrees. A canopy frame swaps its width and depth, and the form's footprint swaps too. A piece placed by a
    centre and no yaw gets one when the turn changes the way it lies, and a piece placed by `along` alone in the middle of the stand
    gets a centre_m: these two changes do not undo themselves, the others do (four quarter turns are the design again).
    The facts of the designer (height limit, hanging) are not touched."""
    if isinstance(quarter_turns, bool) or not isinstance(quarter_turns, int):
        raise ValueError(f'quarter_turns must be a whole number, not {quarter_turns!r}')
    width, depth = (float(v) for v in (footprint or design['form']['footprint_m']))
    out = normalise_placements(design, spec) if spec is not None else copy.deepcopy(design)
    for _ in range(quarter_turns % 4):
        out = _turn_once(out, width, depth)
        width, depth = depth, width
    return out


def _check_box(view, el):
    """[x0, y0, x1, y1] of the piece in plan as the turn check compares it (the pots of a planter: the blades are random), or None."""
    if el['kind'] == 'planter':
        pts = [pt for c, ps in (view.parts.get(el['id']) or []) if c.get('role') == 'planter' for pt in ps]
        return [min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)] if pts else None
    box = main_body_box(view, el)
    return None if box is None else [box[0][0], box[0][1], box[1][0], box[1][1]]


def turn_mismatches(design, spec_read, spec_turned, quarter_turns, footprint):
    """The self-check of a turn (02 section 4.1, 2026-10-24): the ids of the pieces whose box in the compile of the design as read, turned
    about the centre of the footprint, still lies inside the footprint but is not (within 0.01 m) where the compile of the turned design
    puts it. Floor pieces are always checked, a ring and a canopy frame too, wall-attached pieces only in a square footprint (their
    along is a fraction of the side). [] means the turn gives the geometry back."""
    ids = [e['id'] for e in design['elements']]
    before, after = SpecView(spec_read, ids), SpecView(spec_turned, ids)
    width, depth = (float(v) for v in footprint)
    square = abs(width - depth) < 1e-6
    bad = []
    for el in design['elements']:
        kind = el['kind']
        if not (_is_floor(el) or kind in CHECKED_OVERHEAD or (kind in CHECKED_WITH_WALLS and square)):
            continue
        box = _check_box(before, el)
        if box is None:
            continue
        for _ in range(quarter_turns % 4):
            box = [-box[3], box[0], -box[1], box[2]]
        if box[0] < -width / 2 - 1e-6 or box[2] > width / 2 + 1e-6 or box[1] < -depth / 2 - 1e-6 or box[3] > depth / 2 + 1e-6:
            continue
        got = _check_box(after, el)
        if got is None or any(abs(a - b) > TURN_TOLERANCE_M for a, b in zip(box, got)):
            bad.append(el['id'])
    return bad


def decide_frame(mean_iou):
    """The three gates of 02 section 4.1. mean_iou: {quarter turns: mean IoU of the element boxes, or None when that turn does not
    compile}. -> (quarter turns to apply, reason). A turn is chosen only when the design as read is below FRAME_ORIGINAL_BELOW, the best
    turn is at least FRAME_BEST_AT_LEAST, and it is at least FRAME_GAIN_AT_LEAST above the original; a tie goes to the smaller turn."""
    original = mean_iou.get(0)
    turns = {k: v for k, v in mean_iou.items() if k != 0 and v is not None}
    unbuildable = original is None
    if not turns:
        return 0, ('the design as read does not compile and no turn does: kept as it is' if unbuildable
                   else 'no other turn compiles: kept as it is')
    if unbuildable:          # a design that cannot be built scores 0 (02 section 4.1, ruling of the first P8-fix2 verification)
        original = 0.0
    best_k = max(sorted(turns), key=lambda k: turns[k])
    best = turns[best_k]
    if not original < FRAME_ORIGINAL_BELOW:
        return 0, f'kept: the design as read has a mean IoU of {original:.9g}, not below {FRAME_ORIGINAL_BELOW:g}'
    if best < FRAME_BEST_AT_LEAST - FRAME_TOLERANCE:
        return 0, f'kept: the best turn ({90 * best_k} degrees) reaches {best:.9g}, below {FRAME_BEST_AT_LEAST:g}'
    if best - original < FRAME_GAIN_AT_LEAST - FRAME_TOLERANCE:
        return 0, (f'kept: the best turn ({90 * best_k} degrees) is only {best - original:.9g} above the design as read '
                   f'({original:.9g}), less than {FRAME_GAIN_AT_LEAST:g}')
    as_read = 'the design as read does not compile (counted as 0)' if unbuildable else f'mean IoU {original:.9g} as read'
    return best_k, f'turned by {90 * best_k} degrees: {as_read}, {best:.9g} turned'


def _evidence_box(entry, size):
    """The evidence box of one element as four floats clipped to the picture, or None when it is not a usable box."""
    box = entry.get('bbox_px') if isinstance(entry, dict) else None
    if not (isinstance(box, (list, tuple)) and len(box) == 4 and all(_num(v) for v in box) and box[2] > box[0] and box[3] > box[1]):
        return None
    box = [float(v) for v in box]
    if size is not None:
        box = _clip(box, size[0], size[1])
        if not (box[2] > box[0] and box[3] > box[1]):
            return None
    return box


def _clip(box, width, height):
    return [min(max(box[0], 0.0), width), min(max(box[1], 0.0), height), min(max(box[2], 0.0), width), min(max(box[3], 0.0), height)]


def _iou(a, b):
    w, h = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    inter = w * h
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)


def frame_report(design, evidence, size=None):
    """The report skeleton of a frame check: every path of the check, the failing ones too, writes these fields."""
    ids = [e['id'] for e in design['elements']]
    usable = [eid for eid in ids if _evidence_box(evidence.get(eid) if isinstance(evidence, dict) else None, size) is not None]
    return {'decision': 'keep', 'quarter_turns': 0, 'reason': '', 'elements': len(ids), 'evidence_elements': len(usable),
            'gates': {'original_below': FRAME_ORIGINAL_BELOW, 'best_at_least': FRAME_BEST_AT_LEAST, 'gain_at_least': FRAME_GAIN_AT_LEAST,
                      'evidence_share': FRAME_EVIDENCE_SHARE},
            'rotations': []}


def _blocked(row):
    """Is a rotation row inexact (a list of the pieces, or of what failed, that is not empty)? 'unchecked' and None are not."""
    return isinstance(row['inexact'], list) and bool(row['inexact'])


def frame_check(design, evidence, compile_fn, camera=None, size=None, log=None):
    """Is the design the reader gave turned a quarter off the picture? (02 section 4.1; the reader's own evidence boxes are the witness.)

    The design as read is compiled first; from that compile every floor piece is written as the compiler built it
    (normalise_placements) and the normalised design is turned by 90, 180 and 270 degrees (rotate_design). Each turn is compiled with
    `compile_fn(design) -> spec` (it may raise; such a turn is skipped) and self-checked against the compile of the design as read
    (turn_mismatches): a turn that does not give the geometry back is `inexact` and never chosen. The main body of every element
    (main_body, as in 4.3) is projected with `camera` into a picture of `size` (width, height) - both default to the compiled spec's -
    and compared by IoU with the element's `evidence[id]['bbox_px']`; the mean over the elements with a usable box is that turn's score.
    decide_frame decides. The turned designs keep the footprint of the design given (the fitted one). With fewer than half of the
    elements having a usable box, or no compile function, nothing is done and the reason starts with 'frame unverified'.
    Failing closed: if the placements cannot be read, or the self-check itself raises, every turn is inexact with the error written
    down. If the design as read does not compile there is nothing to check a turn against: the turns are 'unchecked' and the gates
    decide, the original scoring 0.

    -> (the design to go on with, report) with report = {decision keep or turn, quarter_turns, reason, elements, evidence_elements,
    gates, rotations: [{degrees, compiled, mean_iou, per_element, error, inexact}]}; inexact is None for the design as read, a list
    (empty when exact) for a turn, or 'unchecked'. Never raises for what it cannot see."""
    say = log or (lambda line: None)
    ids = [e['id'] for e in design['elements']]
    boxes = {}
    for eid in ids:
        box = _evidence_box(evidence.get(eid) if isinstance(evidence, dict) else None, size)
        if box is not None:
            boxes[eid] = box
    report = frame_report(design, evidence, size)

    def finish(why, result):
        report['reason'] = why
        say(f"frame_check: {report['decision']} {90 * report['quarter_turns']} degrees: {why}")
        return result, report
    if compile_fn is None:
        return finish('frame unverified: the renderer cannot compile a design here', design)
    if not boxes or len(boxes) < FRAME_EVIDENCE_SHARE * len(ids) - FRAME_TOLERANCE:
        return finish(f'frame unverified: {len(boxes)} of {len(ids)} elements have an evidence box in the picture '
                      f'(at least {FRAME_EVIDENCE_SHARE:.0%} are needed)', design)
    footprint = tuple(design['form']['footprint_m'])
    score, spec_read, base, base_error = {}, None, design, None
    for k in range(4):
        row = {'degrees': 90 * k, 'compiled': False, 'mean_iou': None, 'per_element': None, 'error': None, 'inexact': None}
        report['rotations'].append(row)
        if k and spec_read is None:
            row['inexact'] = 'unchecked'               # no compile of the design as read to check this turn against
        elif k and base_error:
            row['inexact'] = ['placements']             # fail closed: the placements could not be read
            row['error'] = base_error
        try:
            turned = rotate_design(design if (k == 0 or base_error) else base, k, footprint)
            turned['form']['footprint_m'] = list(footprint)         # the fitted footprint, whatever the turn did to it
            spec = compile_fn(turned)
            if spec is None:
                raise ValueError('the renderer gave no spec')
            if k == 0:
                spec_read = spec
                try:                                                # the pieces as the compiler built them: what the turns turn
                    base = normalise_placements(design, spec)
                except Exception as ex:
                    base, base_error = design, f'the placements could not be read: {type(ex).__name__}: {ex}'
            view = SpecView(spec, ids)
            if camera is not None:
                view.camera = camera
            if size is not None:
                view.width, view.height = int(size[0]), int(size[1])
            per = {}
            for el in turned['elements']:
                if el['id'] not in boxes:
                    continue
                raw = view.box_px_raw(el['id'], main_body(el))
                evidence_box = boxes[el['id']] if size is not None else _clip(boxes[el['id']], view.width, view.height)
                per[el['id']] = 0.0 if raw is None else round(_iou(_clip(raw, view.width, view.height), evidence_box), 4)
            row.update(compiled=True, per_element=per, mean_iou=round(sum(per.values()) / len(per), 4))
            if k and spec_read is not None and not base_error:
                try:
                    row['inexact'] = turn_mismatches(design, spec_read, spec, k, footprint)
                except Exception as ex:                             # fail closed: a turn that cannot be checked is not adopted
                    row['inexact'] = ['self-check']
                    row['error'] = f'the turn could not be checked: {type(ex).__name__}: {ex}'
        except Exception as ex:                                           # a turn that cannot be built or read is skipped
            row['error'] = f'{type(ex).__name__}: {str(ex)}'
        score[k] = row['mean_iou'] if (row['compiled'] and not _blocked(row)) else None
        say(f"frame_check: turn {row['degrees']} degrees: "
            + (f"mean IoU {row['mean_iou']}" if row['compiled'] else f"does not compile ({row['error']})")
            + (f"; inexact: {', '.join(row['inexact'])}" + (f" ({row['error']})" if row['error'] and row['compiled'] else '') if _blocked(row) else '')
            + ('; unchecked' if row['inexact'] == 'unchecked' else ''))
    turns, why = decide_frame(score)
    blocked = [r['degrees'] for r in report['rotations'] if _blocked(r)]
    if blocked:
        if not turns and all(score[k] is None for k in (1, 2, 3)):
            why = f"kept: every turn that compiles is inexact ({', '.join(str(d) for d in blocked)} degrees), none can be adopted"
        else:
            why += f" (turns marked inexact are never chosen: {', '.join(str(d) for d in blocked)} degrees)"
    report['quarter_turns'] = turns
    result = design
    if turns:
        report['decision'] = 'turn'
        result = rotate_design(base if not base_error else design, turns, footprint)
        result['form']['footprint_m'] = list(footprint)
    return finish(why, result)
