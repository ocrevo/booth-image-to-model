"""The synthetic scene the web tests read, upload and compile, written the way a reader writes one now (scene description 1.1).

It starts from the skill's own synthetic scene (booth-image-to-model/tests/test_draft_compile.py: parts, scopes, camera, pixel
helpers) and replaces what the reading prompt asks for:
- eight corner points, all used in the fit, every point carrying the `fit` key as null (the engine checks them by leave-one-out);
- one assumed dimension (the height); the footprint is left to the fit.
The clicks are exact projections of known world points, so the quality gate of the compiled scene is "pass".

Variants, each built from this one definition:
- `review_scene()`: one fitted corner clicked far from where it belongs, so the gate is "review". The mock reader serves it too.
- `held_out_scene()`: the optional feature, two corners marked `fit: false` (held out of the fit and only measured), the way a
  person may write a file by hand. Not what a reader is asked for.
- `scale_check_scene()`: the same scene with one scale check (a table-height part), which agrees or, given another expected height, does not.
- `old_scene()`: a file written before 1.1 (the old version spelling, no `fit` keys).
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
HELD_OUT_IDS = ['floor_fl', 'top_br']          # the two corners held_out_scene() marks fit:false (far apart: one floor, one top)
MISCLICKED_ID = 'floor_fr'                     # the corner review_scene() clicks in the wrong place
MISCLICK_PX = (45, -35)                        # ... by this many pixels (about 57 px): far beyond any honest click error


def scene():
    s = copy.deepcopy(base.scene())
    s['schema_version'] = '1.1'
    s['known'] = {'W': None, 'D': None, 'H': H}
    s['assumed'] = ['H']
    s['corner_points'] = [{'id': i, 'uv_px': px(*xyz), 'xyz': tok, 'fit': None} for i, xyz, tok in CORNERS]
    s['scale_notes'] = 'The height is assumed; width and depth are worked out from the corner points. Synthetic dimensions.'
    return s


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


def old_scene():
    """A scene file written before schema 1.1: the old version spelling and no `fit` key on the corner points."""
    s = scene()
    s['schema_version'] = 'scene-description.v1'
    for p in s['corner_points']:
        del p['fit']
    return s
