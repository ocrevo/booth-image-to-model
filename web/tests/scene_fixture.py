"""The synthetic scene the web tests read, upload and compile, written the way a reader writes one now (scene description 1.2).

It starts from the skill's own synthetic scene (booth-image-to-model/tests/test_draft_compile.py: parts, scopes, camera, pixel
helpers) and replaces what the reading prompt asks for:
- eight corner points, all used in the fit, every point carrying the `fit` key as null (the engine checks them by leave-one-out);
- one assumed dimension (the height); the footprint is left to the fit;
- the booth layout stated first: a wall stands along the back edge and the other three sides are open, so a peninsula (booth_type
  "peninsula", open_sides front, left and right, no cutaway side), seen by a camera in front of and to the right of the booth.
The clicks are exact projections of known world points, so the quality gate of the compiled scene is "pass".

Variants, each built from this one definition:
- `review_scene()`: one fitted corner clicked far from where it belongs, so the gate is "review". The mock reader serves it too.
- `held_out_scene()`: the optional feature, two corners marked `fit: false` (held out of the fit and only measured), the way a
  person may write a file by hand. Not what a reader is asked for.
- `scale_check_scene()`: the same scene with one scale check (a table-height part), which agrees or, given another expected height, does not.
- `topology_scene(kind)`: the same scene with a booth layout that the engine's layout checks fail, one failure per kind: `type`
  (a type that the open sides cannot make), `wall` (a wall on a side declared open) and `camera` (the reading calls the back the only
  open side, so the camera looks in through closed sides: front and back probably swapped).
- `v11_scene()`: a file written by the previous reader (version 1.1, no layout keys); `old_scene()`: a file written before 1.1 (the old
  version spelling, no `fit` keys and no layout keys). Both must still load; their layout checks are not run.
"""
from __future__ import annotations
import copy
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / 'booth-image-to-model' / 'tests'))
import test_draft_compile as base   # noqa: E402

IMG_W, IMG_H = base.IMG_W, base.IMG_H
CAM, px = base.CAM, base.px
W, D, H, ZF = base.W, base.D, base.H, base.ZF

# id, world point, coordinate tokens
CORNERS = [
    ('floor_bl', (-W / 2, -D / 2, ZF), ['-W/2', '-D/2', 'F']),
    ('floor_br', (W / 2, -D / 2, ZF), ['W/2', '-D/2', 'F']),
    ('floor_fr', (W / 2, D / 2, ZF), ['W/2', 'D/2', 'F']),
    ('floor_fl', (-W / 2, D / 2, ZF), ['-W/2', 'D/2', 'F']),
    ('top_bl', (-W / 2, -D / 2, H), ['-W/2', '-D/2', 'H']),
    ('top_br', (W / 2, -D / 2, H), ['W/2', '-D/2', 'H']),
    ('top_fr', (W / 2, D / 2, H), ['W/2', 'D/2', 'H']),
    ('top_fl', (-W / 2, D / 2, H), ['-W/2', 'D/2', 'H']),
]
LAYOUT_KEYS = ('booth_type', 'open_sides', 'cutaway_sides')
LAYOUT = ('peninsula', ['front', 'left', 'right'], [])       # the layout of scene(): the back edge has the wall
TOPOLOGY_KINDS = ('type', 'wall', 'camera')
HELD_OUT_IDS = ['floor_fl', 'top_br']          # the two corners held_out_scene() marks fit:false (far apart: one floor, one top)
MISCLICKED_ID = 'floor_fr'                     # the corner review_scene() clicks in the wrong place
MISCLICK_PX = (45, -35)                        # ... by this many pixels (about 57 px): far beyond any honest click error


def with_layout(s, booth_type, open_sides, cutaway_sides):
    """`s` with the layout keys, written first (right after the version and the project id) the way the reading is asked to; the
    version becomes 1.2."""
    layout = {'booth_type': booth_type, 'open_sides': copy.deepcopy(open_sides), 'cutaway_sides': copy.deepcopy(cutaway_sides)}
    out = {}
    for key, value in s.items():
        if key in LAYOUT_KEYS:
            continue
        out[key] = '1.2' if key == 'schema_version' else value
        if key == 'project_id':
            out.update(layout)
    if 'booth_type' not in out:
        raise ValueError('a scene description has a project_id')
    return out


def camera_outer_sides():
    """The sides of the footprint that the camera is beyond (front is -Y, back +Y, left -X, right +X), for this fixture's camera."""
    x, y = CAM['position'][:2]
    return [name for name, beyond in (('front', y < -D / 2), ('back', y > D / 2), ('left', x < -W / 2), ('right', x > W / 2)) if beyond]


def scene():
    s = copy.deepcopy(base.scene())
    s['known'] = {'W': None, 'D': None, 'H': H}
    s['assumed'] = ['H']
    s['corner_points'] = [{'id': i, 'uv_px': px(*xyz), 'xyz': tok, 'fit': None} for i, xyz, tok in CORNERS]
    s['scale_notes'] = 'The height is assumed; width and depth are worked out from the corner points. Synthetic dimensions.'
    return with_layout(s, *LAYOUT)


def review_scene(scene_in=None):
    """`scene_in` (the fixture scene by default) with one fitted corner clicked far from where it belongs: the clicks disagree with a
    box seen in perspective, so the engine's quality gate says "review". The only definition of that scene (the mock reader uses it)."""
    s = copy.deepcopy(scene_in) if scene_in is not None else scene()
    moved = [p for p in s['corner_points'] if p['id'] == MISCLICKED_ID]
    if not moved:
        raise ValueError(f'the scene has no corner point {MISCLICKED_ID!r} to click in the wrong place')
    moved[0]['uv_px'] = [round(moved[0]['uv_px'][0] + MISCLICK_PX[0], 2), round(moved[0]['uv_px'][1] + MISCLICK_PX[1], 2)]
    return s


def scale_check_scene(expected_height_m=0.74, tolerance=0.15):
    """The fixture scene with one scale check: a cafe-table-sized upright part whose base and top are exact projections of a 0.74 m
    column. With `expected_height_m` 0.74 the check agrees; with another value (the reader assumed the wrong dimension, or the table
    is not a standard one) it does not."""
    s = scene()
    x, y = -1.2, 0.4
    s['scale_checks'] = [{'id': 'table_height', 'base_px': px(x, y, ZF), 'top_px': px(x, y, ZF + 0.74),
                          'expected_height_m': expected_height_m, 'tolerance': tolerance}]
    return s


def held_out_scene(ids=HELD_OUT_IDS):
    """The optional feature: the corners in `ids` are marked `fit: false`, projected with the fitted camera and measured but not fitted."""
    s = scene()
    for p in s['corner_points']:
        if p['id'] in ids:
            p['fit'] = False
    return s


def topology_scene(kind, scene_in=None):
    """`scene_in` (the fixture scene by default) with a booth layout that one of the engine's layout checks fails. The clicks and the
    parts are untouched, so the corner fit is still good: only the layout makes the quality gate say "review".
    - `type`: booth_type "inline" but three open sides (a reading that says this is asked again by the reader; a file uploaded by hand is
      not, and the engine's type check fails it);
    - `wall`: four open sides ("island") while the back wall stands on the back edge: the wall is left out of the model;
    - `camera`: the back is the only open side ("inline") and there is no wall: the camera is in front of and to the right of the booth,
      outside two closed sides, so front and back (or left and right) were probably swapped. The reader cannot see this; the engine can."""
    if kind not in TOPOLOGY_KINDS:
        raise ValueError(f'kind must be one of {TOPOLOGY_KINDS}, not {kind!r}')
    s = copy.deepcopy(scene_in) if scene_in is not None else scene()
    if kind == 'type':
        return with_layout(s, 'inline', ['front', 'left', 'right'], [])
    if kind == 'wall':
        return with_layout(s, 'island', ['front', 'back', 'left', 'right'], [])
    s['components'] = [c for c in s['components'] if c['type'] != 'wall']
    return with_layout(s, 'inline', ['back'], [])


def v11_scene():
    """A file written by the previous reader: version 1.1, the check flags but no booth layout keys."""
    s = scene()
    s['schema_version'] = '1.1'
    for key in LAYOUT_KEYS:
        del s[key]
    return s


def old_scene():
    """A scene file written before schema 1.1: the old version spelling, no `fit` key on the corner points and no booth layout keys."""
    s = v11_scene()
    s['schema_version'] = 'scene-description.v1'
    for p in s['corner_points']:
        del p['fit']
    return s
