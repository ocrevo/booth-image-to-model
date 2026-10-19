"""Fit-check overlay: draw the fitted booth box and 0.5 m grids over the source image.

Requires Pillow. Input is the box_fit.json written by box_fit.py plus the source
image. It answers one question before modelling starts: does the fitted camera
and box really sit on the booth in the picture? Wrong camera height, a missed
open side or a mis-clicked corner shows up as a wireframe that drifts away from
the walls or floor edges. Standard-library maths only (camera_math).

Outputs a PNG; draws nothing that is not derived from the fit. Fitted corners are green circles; check corners (fit false in
the points file) are orange squares and are left out of "max_point_err_px" (their worst error is "max_check_err_px"); corners that
box_fit names as outliers get a magenta ring.
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
from PIL import Image, ImageDraw
from common import read_json
import camera_math

STEP_M = 0.5
SEGMENT_M = 0.1          # polyline resolution so segments behind the camera can be skipped
CHECK_COLOR = (255, 140, 0)       # check corners (fit false): square outline, error line and cross in orange
OUTLIER_COLOR = (255, 0, 255)     # a fitted click that the others cannot predict gets a ring around its circle


def _project(p, camera, w, h):
    try:
        u, v, _ = camera_math.project(p, camera, w, h)
    except ValueError:
        return None
    return u, v


def _polyline(draw, a, b, camera, w, h, fill, width=1):
    """Draw the 3D segment a-b as short pieces; pieces touching a point behind the camera are dropped."""
    length = math.dist(a, b)
    n = max(1, int(math.ceil(length / SEGMENT_M)))
    prev = _project(a, camera, w, h)
    for i in range(1, n + 1):
        t = i / n
        q = [a[k] + (b[k] - a[k]) * t for k in range(3)]
        cur = _project(q, camera, w, h)
        if prev is not None and cur is not None:
            draw.line([prev, cur], fill=fill, width=width)
        prev = cur


def _frange(lo, hi, step):
    """Grid positions that include 0 (the footprint centre) and stay inside [lo, hi]."""
    first = math.ceil(lo / step) * step
    out, x = [], first
    while x <= hi + 1e-9:
        out.append(round(x, 6))
        x += step
    return out


def grid_segments(dims, floor_z):
    """3D segments of the floor grid and the back/left wall grids (+ the box edges)."""
    W, D, H = dims['W'], dims['D'], dims.get('H')
    x0, x1, y0, y1 = -W / 2, W / 2, -D / 2, D / 2
    z0 = floor_z
    grid = []
    for x in _frange(x0, x1, STEP_M):                       # floor, lines parallel to Y
        grid.append(((x, y0, z0), (x, y1, z0)))
    for y in _frange(y0, y1, STEP_M):                       # floor, lines parallel to X
        grid.append(((x0, y, z0), (x1, y, z0)))
    if H is not None:                                       # walls and the top need a fitted height
        for x in _frange(x0, x1, STEP_M):                   # back wall verticals
            grid.append(((x, y1, z0), (x, y1, H)))
        for z in _frange(z0, H, STEP_M):                    # back wall horizontals
            grid.append(((x0, y1, z), (x1, y1, z)))
        for y in _frange(y0, y1, STEP_M):                   # left wall verticals
            grid.append(((x0, y, z0), (x0, y, H)))
        for z in _frange(z0, H, STEP_M):                    # left wall horizontals
            grid.append(((x0, y0, z), (x0, y1, z)))
    corners = [(x, y, z) for x in (x0, x1) for y in (y0, y1) for z in ((z0, H) if H is not None else (z0,))]
    edges = []
    for a in corners:
        for b in corners:
            if a < b and sum(abs(a[i] - b[i]) > 1e-9 for i in range(3)) == 1:
                edges.append((a, b))
    return grid, edges


def draw_overlay(fit, image_path, out_path):
    missing = [k for k in ('camera', 'image', 'dims_m', 'points', 'status', 'rms_px') if k not in fit]
    for k in ('W', 'D'):
        if k not in fit.get('dims_m', {}):
            missing.append('dims_m.' + k)
    if missing:
        raise ValueError('box_fit result lacks ' + ', '.join(missing) + '; re-run box_fit.py from 2.3.0-rc2 or later (older results carry no image size)')
    camera = fit['camera']
    w, h = fit['image']['width'], fit['image']['height']
    floor_z = float(fit.get('floor_z', 0.0))
    dims = fit['dims_m']
    base = Image.open(image_path).convert('RGB')
    if base.size != (w, h):
        raise ValueError(f'IMAGE_SHAPE_MISMATCH: fit says {w}x{h}, image is {base.size[0]}x{base.size[1]}')
    canvas = base.copy()
    d = ImageDraw.Draw(canvas)
    grid, edges = grid_segments(dims, floor_z)
    for a, b in grid:
        _polyline(d, a, b, camera, w, h, (0, 200, 200))
    for a, b in edges:
        _polyline(d, a, b, camera, w, h, (255, 220, 0), width=2)
    errs, check_errs = [], []
    outliers = set() if fit.get('loo_reliable') is False else {str(i) for i in (fit.get('outliers') or [])}
    for p in fit['points']:
        tx, ty = p['uv_px']
        fx, fy = p['fitted_px']
        if p.get('fit') is False:                           # a check corner: projected with the fitted camera, never in the fit
            d.rectangle([tx - 5, ty - 5, tx + 5, ty + 5], outline=CHECK_COLOR, width=2)
            d.line([(tx, ty), (fx, fy)], fill=CHECK_COLOR, width=2)
            d.line([(fx - 4, fy - 4), (fx + 4, fy + 4)], fill=CHECK_COLOR, width=1)
            d.line([(fx - 4, fy + 4), (fx + 4, fy - 4)], fill=CHECK_COLOR, width=1)
            check_errs.append(p['err_px'])
            continue
        d.ellipse([tx - 4, ty - 4, tx + 4, ty + 4], outline=(0, 255, 0), width=2)
        d.line([(tx, ty), (fx, fy)], fill=(255, 0, 0), width=2)
        d.line([(fx - 4, fy - 4), (fx + 4, fy + 4)], fill=(255, 0, 0), width=1)
        d.line([(fx - 4, fy + 4), (fx + 4, fy - 4)], fill=(255, 0, 0), width=1)
        if str(p.get('id')) in outliers:                    # a click the others cannot predict
            d.ellipse([tx - 9, ty - 9, tx + 9, ty + 9], outline=OUTLIER_COLOR, width=2)
        errs.append(p['err_px'])
    height = f"H {dims['H']}" if 'H' in dims else 'H not fitted (floor only)'
    label = f"fit {fit['status']}  rms {fit['rms_px']} px  W {dims['W']} D {dims['D']} {height} m  grid {STEP_M} m"
    labels = [label]
    if check_errs or outliers:
        legend = []
        if check_errs:
            legend.append(f"orange square = check corner, not fitted (max {max(check_errs):.1f} px)")
        if outliers:
            legend.append('magenta ring = click the others cannot predict')
        labels.append('green circle = fitted corner;  ' + ';  '.join(legend))
    for i, text in enumerate(labels):
        d.rectangle([0, 16 * i, min(w, 8 * len(text) + 8), 16 * i + 16], fill=(0, 0, 0))
        d.text((4, 16 * i + 2), text, fill=(255, 255, 255))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path)
    return {'overlay': str(out_path), 'rms_px': fit['rms_px'], 'max_point_err_px': max(errs) if errs else None,
            'check_points': len(check_errs), 'max_check_err_px': max(check_errs) if check_errs else None,
            'grid_segments': len(grid), 'box_edges': len(edges)}


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    p.add_argument('--fit', required=True, help='box_fit.json')
    p.add_argument('--image', required=True, help='source image (the snapshot used for the fit)')
    p.add_argument('--out', required=True)
    a = p.parse_args()
    try:
        summary = draw_overlay(read_json(a.fit), a.image, a.out)
    except (ValueError, KeyError, OSError) as ex:
        print(str(ex))
        return 2
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
