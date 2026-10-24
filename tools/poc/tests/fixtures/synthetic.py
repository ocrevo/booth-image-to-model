"""Small programmatic fixtures for the scoring tests. No real pictures: everything is drawn here.

The picture is 320 x 200: a dark background, a light "booth" rectangle and three coloured blocks inside it.
The render is the same drawing as RGBA whose alpha covers only the booth rectangle; `dx`, `dy` shift it.
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
from PIL import Image

W, H = 320, 200
BOOTH = (20, 20, 300, 180)                    # x0, y0, x1, y1
BLOCKS = {
    'a': ((60, 50, 120, 110), (200, 40, 40)),
    'b': ((160, 60, 220, 130), (40, 60, 200)),
    'c': ((240, 100, 290, 170), (40, 160, 60)),
}
EXTRA_BOX = (130, 140, 180, 175)               # a fourth block that only one of the two images has
BG = (40, 40, 40)
FLOOR = (200, 200, 200)


def draw(dx=0, dy=0, recolour=None, extra=False):
    """RGB drawing of the scene shifted by (dx, dy); `recolour` maps block name -> rgb."""
    img = np.zeros((H, W, 3), np.uint8)
    img[:] = BG
    alpha = np.zeros((H, W), np.uint8)

    def rect(x0, y0, x1, y1, rgb):
        x0, x1, y0, y1 = x0 + dx, x1 + dx, y0 + dy, y1 + dy
        sl = (slice(max(y0, 0), min(y1, H)), slice(max(x0, 0), min(x1, W)))
        img[sl] = rgb
        return sl

    sl = rect(*BOOTH, FLOOR)
    alpha[sl] = 255
    for name, (box, rgb) in BLOCKS.items():
        rect(*box, (recolour or {}).get(name, rgb))
    if extra:
        rect(*EXTRA_BOX, (230, 120, 20))
    return img, alpha


def _noise(dx=0, dy=0):
    """Faint seeded texture (+-4 levels) that moves with the shift."""
    base = np.random.default_rng(7).integers(-4, 5, size=(H + 400, W + 400, 1))
    return base[200 - dy:200 - dy + H, 200 - dx:200 - dx + W]


def photo(extra=False):
    """The scene with faint noise and isolated low-contrast streaks (20 levels): too weak for Canny's high threshold of 150,
    so they are no edge, but a high threshold lowered to zero turns them into edges, and the render has nothing there."""
    img = draw(extra=extra)[0].astype(int)
    for y, x0, x1 in ((80, 130, 160), (40, 100, 290), (175, 30, 290)):
        img[y:y + 2, x0:x1] -= 20
    return np.clip(img + _noise(), 0, 255).astype(np.uint8)


def render(dx=0, dy=0, recolour=None, transparent=False, extra=False):
    img, alpha = draw(dx, dy, recolour, extra)
    img = np.clip(img.astype(int) + _noise(dx, dy), 0, 255).astype(np.uint8)
    if transparent:
        alpha[:] = 0
    return np.dstack([img, alpha])


def camera():
    """Camera at (0, -10, 0) looking along +Y; 36 mm lens on a 36 mm sensor, so f = width = 320 px."""
    return {'position': [0.0, -10.0, 0.0], 'target': [0.0, 0.0, 0.0], 'lens_mm': 36.0, 'sensor_width_mm': 36.0,
            'projection': 'perspective', 'principal_point': [0.5, 0.5], 'roll_deg': 0}


def component(ident, size, location, group=None):
    c = {'id': ident, 'kind': 'box', 'location': list(location), 'rotation_deg': [0, 0, 0], 'params': {'size': list(size)}}
    if group:
        c['group'] = group
    return c


def spec():
    """Reception box (2 x 1 x 2 m) at the origin and a thin screen further back and to the right."""
    return {'schema_version': 'booth-spec.v2', 'source': {'width': W, 'height': H},
            'camera': camera(),
            'components': [component('reception_body', (2, 1, 2), (0, 0, 0), group='reception'),
                           component('screen_a_panel', (1, 0.1, 1), (3, 2, 0.5), group='screen_a')]}


def inventory(n=10):
    items = [{'id': f'part_{i}', 'name': f'part {i}', 'kind_hint': 'box', 'hero': i < 2} for i in range(n)]
    items[0]['id'] = 'reception'
    items[1]['id'] = 'screen_a'
    return {'schema_version': 'truth.v1', 'picture_id': 'pic_T', 'items': items}


def holdout(uv=((193.68, 66.32), (200, 80), (210, 90), (220, 100))):
    parts = ['reception', 'screen_a', 'reception', 'screen_a']
    corners = ['top_front_right', 'top_left', 'bottom_front_left', 'bottom_right']
    return {'schema_version': 'truth.v1', 'picture_id': 'pic_T',
            'points': [{'id': f'h{i + 1}', 'part_id': parts[i], 'corner': corners[i], 'uv_px': list(uv[i]), 'note': 'fixture'}
                       for i in range(4)]}


def swatches():
    return {'schema_version': 'truth.v1', 'picture_id': 'pic_T',
            'swatches': [{'id': 's_a', 'bbox_px': [65, 55, 115, 105], 'role': 'dominant'},
                         {'id': 's_b', 'bbox_px': [165, 65, 215, 125], 'role': 'secondary'},
                         {'id': 's_c', 'bbox_px': [245, 105, 285, 165], 'role': 'accent'}]}


def write_truth(folder, **override):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    data = {'inventory.json': inventory(), 'holdout_px.json': holdout(), 'swatches.json': swatches()}
    data.update(override)
    for name, obj in data.items():
        if obj is None:
            continue
        (folder / name).write_text(json.dumps(obj), encoding='utf-8')
    return folder


def write_preview(folder, dx=0, recolour=None, transparent=False, extra=False):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    Image.fromarray(render(dx=dx, recolour=recolour, transparent=transparent, extra=extra), 'RGBA').save(folder / 'preview_render.png')
    (folder / 'spec.json').write_text(json.dumps(spec()), encoding='utf-8')
    (folder / 'projected_landmarks.json').write_text('[]', encoding='utf-8')
    return folder


def write_photo(path):
    Image.fromarray(photo(), 'RGB').save(path)
    return path


def judge(judge_id, pass_no, inv_present=None, l2=None, scores=None, pair_id='pair01', extra_parts=0):
    """Scoring response: L1:<id> present/missing for every inventory item, L2:<id> ok/off/wrong for the present ones
    (default all ok except screen_a, which is off); `l2` overrides single items."""
    items = {}
    verdict = {i['id']: ('off' if i['id'] == 'screen_a' else 'ok') for i in inventory()['items']}
    verdict.update(l2 or {})
    for i in inventory()['items']:
        here = inv_present is None or i['id'] in inv_present
        items['L1:' + i['id']] = 'present' if here else 'missing'
        if here:
            items['L2:' + i['id']] = verdict[i['id']]
    return {'schema_version': 'judge.v1', 'pair_id': pair_id, 'judge_id': judge_id, 'pass': pass_no, 'better': 'same',
            'scores': scores or {'L2': 14, 'L3': 12, 'L4': 6}, 'items': items, 'confidence': 'medium', 'extra_parts': extra_parts}


def write_panel(folder, extra_by_judge=None, **kw):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for j in ('j1', 'j2', 'j3'):
        for p in (1, 2):
            extra = (extra_by_judge or {}).get(j, 0)
            (folder / f'{j}_{p}.json').write_text(json.dumps(judge(j, p, extra_parts=extra, **kw)), encoding='utf-8')
    return folder


def fit(camera_=None, **over):
    """A made-up corner fit (the box_fit.json fields the scorer reads) for the synthetic camera.

    The box is 7.875 x 2 m on the floor and reaches from z = -2.25 to z = 2.25 (H is the top z, as in box_fit), so the
    front face projects exactly onto the booth rectangle of the drawing (20, 20, 300, 180).
    """
    doc = {'status': 'fitted', 'dims_m': {'W': 7.875, 'D': 2.0, 'H': 2.25},
           'dim_source': {'W': 'fitted', 'D': 'fitted', 'H': 'fitted'}, 'floor_z': -2.25,
           'image': {'width': W, 'height': H}, 'camera': camera_ or camera()}
    doc.update(over)
    return doc


def write_fit(path, **kw):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fit(**kw)), encoding='utf-8')
    return path
