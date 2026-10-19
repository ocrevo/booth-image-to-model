"""Seeded generator of synthetic booth cases with exact ground truth.

    python tools/eval/synth_generate.py --suite oracle-v1 --seed 20261019 --out output/_eval/cases

Each case is a folder with
  case.json   the truth: booth type, footprint W x D and top height H, the camera (position, yaw, pitch, roll, lens, optional
              principal point off centre after a crop), 6-10 corner tokens with their exact pixel coordinates, which two are
              held out as check corners, and a few floor-standing parts with their true floor positions;
  scene.json  a scene description in the original (v1) format, built from the same numbers with no noise, that any version of
              the skill's compiler accepts. The oracle runner adds perturbations and adapts it to the skill under test.
The same suite name and seed give byte-identical files. Standard library only; the maths is the harness's own (camgeom.py), so the
truth does not depend on the skill version being measured.

The synthetic world uses our own building blocks (box walls, a roof frame, counters, tables, planters) and a perfect pinhole
camera. It says nothing about lens distortion, clutter or how a model reads a real picture; see README.md.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import camgeom

GENERATOR_VERSION = 1
BOOTH_TYPES = ('inline', 'corner', 'peninsula', 'island')
# (W, D, H) in metres: metric modules plus a few that are not. Generic sizes; none comes from a client picture.
SIZES = {
    'inline': [(3, 3, 2.5), (6, 3, 3.0), (9, 3, 3.0), (4.5, 3.5, 2.7), (6, 3, 4.0), (12, 3, 3.0)],
    'corner': [(3, 3, 2.5), (6, 3, 3.0), (6, 6, 3.5), (7.2, 4.8, 3.0), (9, 3, 2.5), (6, 4.5, 4.0)],
    'peninsula': [(6, 6, 3.0), (9, 6, 4.0), (6, 3, 3.5), (5.5, 5.5, 3.0), (9, 6, 3.0), (12, 6, 4.5)],
    'island': [(6, 6, 3.0), (9, 9, 4.0), (12, 9, 4.5), (7.5, 7.5, 3.5), (6, 6, 4.0), (10, 6, 3.2)],
}
# Walls on these edges (+Y is the back); every other edge is open. Every booth also has an overhead frame at the top height H, so
# the top corners are physical points a reader can click. Camera positions are drawn from the open side(s).
WALLS = {'inline': ['back'], 'corner': ['back', 'left'], 'peninsula': ['back'], 'island': []}
# Azimuth of the camera position seen from the booth centre, degrees: -90 is straight in front (-Y), 0 is +X.
AZIMUTH = {'inline': (-135.0, -45.0), 'corner': (-85.0, -10.0), 'peninsula': (-165.0, -15.0), 'island': (-180.0, 180.0)}
IMAGE_SIZES = [(1600, 1000), (1280, 720), (1500, 1000), (1200, 900), (1920, 1080), (1024, 768)]
WALL_T = 0.05
PALETTE = ['#d9d9d9', '#3a6ea5', '#e07b39', '#5a9e6f', '#b04a5a', '#f2c14e', '#7d5ba6', '#2b2b2b']
PART_TYPES = ('counter', 'box', 'round_table', 'planter')
MIN_VISIBLE = 6
MAX_POINTS = 10

SUITES = {
    # 32 scenes (8 per booth type) x 9 perturbations = 288 runs; the baseline suite.
    'oracle-v1': {'cases': 32, 'perturbations': ['clean', 'noise_1', 'noise_3', 'outlier_30', 'outlier_1', 'outlier_2', 'mislabel', 'all_exact',
                                                'all_guess']},
    # Tiny suite for tests and CI.
    'smoke': {'cases': 4, 'perturbations': ['noise_1', 'outlier_1']},
}


def derive_seed(*parts):
    digest = hashlib.sha256('|'.join(str(p) for p in parts).encode('utf-8')).digest()
    return int.from_bytes(digest[:8], 'big')


def lds(index, k):
    """Low-discrepancy value in [0, 1) for case `index`, stream k: spreads cameras evenly over a suite without randomness."""
    return ((index + 1) * (0.6180339887498949, 0.4142135623730951, 0.7320508075688772, 0.2360679774997897, 0.3204)[k % 5]) % 1.0


def r6(v):
    return round(float(v), 6)


def pool_points(W, D, H, zf):
    """Candidate clickable points: the 8 box corners and two edge midpoints. -> [(id, tokens, world)]."""
    pts = []
    for z_name, z_tok, z in (('floor', 'F', zf), ('top', 'H', H)):
        for y_name, ys in (('front', -1), ('back', 1)):
            for x_name, xs in (('left', -1), ('right', 1)):
                pts.append((f'{z_name}_{y_name}_{x_name}', [('-' if xs < 0 else '') + 'W/2', ('-' if ys < 0 else '') + 'D/2', z_tok],
                            [xs * W / 2, ys * D / 2, z]))
    pts.append(('top_back_mid', ['0', 'D/2', 'H'], [0.0, D / 2, H]))
    pts.append(('floor_front_mid', ['0', '-D/2', 'F'], [0.0, -D / 2, zf]))
    return pts


def _view(aim, yaw, pitch, roll, f_px, w, h, dist):
    """Camera looking at `aim` from `dist` metres away (unrounded, only for the framing search)."""
    fwd = camgeom.forward(yaw, pitch)
    pos = [aim[i] - fwd[i] * dist for i in range(3)]
    return camgeom.make_camera(pos, yaw, pitch, roll, f_px, w, h, distance=dist, digits=9)


def _framed(cam, pts, w, h, margin):
    mx, my = margin * w, margin * h
    for p in pts:
        uvz = camgeom.project_or_none(p, cam, w, h)
        if uvz is None or uvz[2] < 0.3 or not (mx <= uvz[0] <= w - mx and my <= uvz[1] <= h - my):
            return False
    return True


def frame_distance(aim, yaw, pitch, roll, f_px, w, h, pts, margin):
    """Smallest camera distance at which all of `pts` project inside the frame (inset by `margin`, a fraction); None if never."""
    if not _framed(_view(aim, yaw, pitch, roll, f_px, w, h, 300.0), pts, w, h, margin):
        return None
    lo, hi = 0.5, 300.0
    for _ in range(46):
        mid = (lo + hi) / 2
        if _framed(_view(aim, yaw, pitch, roll, f_px, w, h, mid), pts, w, h, margin):
            hi = mid
        else:
            lo = mid
    return hi


def make_camera_for(rng, index, btype, W, D, H, zf, attempt):
    """Draw a camera for the case. -> dict with the camera, image size and the pool points' pixels, or None."""
    w0, h0 = IMAGE_SIZES[(index + attempt // 40) % len(IMAGE_SIZES)]
    shift = attempt * 0.037
    az_lo, az_hi = AZIMUTH[btype]
    alpha = math.radians(az_lo + ((lds(index, 0) + shift * 0.5) % 1.0) * (az_hi - az_lo))
    pitch = math.radians(5.0 + ((lds(index, 1) + shift) % 1.0) * 40.0)
    f_mm = 18.0 + ((lds(index, 2) + shift) % 1.0) * 32.0
    roll = max(-2.0, min(2.0, rng.gauss(0.0, 0.7))) * math.pi / 180
    yaw = math.atan2(math.cos(alpha), -math.sin(alpha)) + math.radians(rng.uniform(-6.0, 6.0))
    aim = [rng.uniform(-0.08, 0.08) * W, rng.uniform(-0.08, 0.08) * D, zf + 0.45 * (H - zf)]
    f_px = f_mm / camgeom.SENSOR_MM * w0
    corners = [p[2] for p in pool_points(W, D, H, zf)[:8]]
    partial = rng.random() < 0.2
    margin = rng.uniform(-0.05, 0.0) if partial else rng.uniform(0.02, 0.10)
    dist = frame_distance(aim, yaw, pitch, roll, f_px, w0, h0, corners, margin)
    if dist is None:
        return None
    for _ in range(40):                                      # the camera stays outside the booth with a little room
        fwd = camgeom.forward(yaw, pitch)
        pos = [aim[i] - fwd[i] * dist for i in range(3)]
        if abs(pos[0]) > W / 2 + 0.6 or abs(pos[1]) > D / 2 + 0.6:
            break
        dist *= 1.15
    fwd = camgeom.forward(yaw, pitch)
    pos = [aim[i] - fwd[i] * dist for i in range(3)]
    w, h, pp = w0, h0, None
    if rng.random() < 0.3:                                   # crop: the principal point moves off centre
        for _ in range(20):
            cf = rng.uniform(0.82, 0.95)
            cw, ch = int(round(w0 * cf)), int(round(h0 * rng.uniform(0.82, 0.95)))
            x0, y0 = rng.randint(0, w0 - cw), rng.randint(0, h0 - ch)
            full = _view(aim, yaw, pitch, roll, f_px, w0, h0, dist)
            inside = sum(1 for p in pool_points(W, D, H, zf)
                         if (uvz := camgeom.project_or_none(p[2], full, w0, h0)) and uvz[2] > 0.3
                         and x0 + 0.5 <= uvz[0] <= x0 + cw - 0.5 and y0 + 0.5 <= uvz[1] <= y0 + ch - 0.5)
            if inside >= MIN_VISIBLE + 1:
                w, h, pp = cw, ch, (w0 / 2 - x0, h0 / 2 - y0)
                break
    cam = camgeom.make_camera(pos, yaw, pitch, roll, f_px, w, h, principal_px=pp, distance=dist)
    return {'camera': cam, 'width': w, 'height': h, 'cropped': pp is not None, 'azimuth_deg': r6(math.degrees(alpha)),
            'pitch_deg': r6(math.degrees(pitch)), 'yaw_deg': r6(math.degrees(yaw)), 'focal_px': r6(f_px),
            'full_frame': {'width': w0, 'height': h0}}


def project_pool(cam, w, h, W, D, H, zf):
    rows = []
    for pid, tokens, world in pool_points(W, D, H, zf):
        uvz = camgeom.project_or_none(world, cam, w, h)
        if uvz is None:
            rows.append({'id': pid, 'xyz': tokens, 'world_m': [r6(v) for v in world], 'uv_px': None, 'in_image': False})
            continue
        u, v, z = uvz
        inside = 0.5 <= u <= w - 0.5 and 0.5 <= v <= h - 0.5 and z > 0.3
        rows.append({'id': pid, 'xyz': tokens, 'world_m': [r6(c) for c in world], 'uv_px': [round(u, 3), round(v, 3)], 'in_image': bool(inside)})
    return rows


def choose_points(rng, rows):
    """Pick 6-10 of the visible pool points (at least two on the floor and two on top) and the check corners among them."""
    vis = [r for r in rows if r['in_image']]
    if len(vis) < MIN_VISIBLE:
        return None
    low = 8 if (len(vis) >= 8 and rng.random() < 0.85) else MIN_VISIBLE     # most readers click 8-10 points
    n = rng.randint(low, min(MAX_POINTS, len(vis)))
    order = sorted(range(len(vis)), key=lambda i: rng.random())
    chosen = [vis[i] for i in sorted(order[:n])]
    floor = [r for r in chosen if r['xyz'][2] == 'F']
    top = [r for r in chosen if r['xyz'][2] == 'H']
    if len(floor) < 2 or len(top) < 2:
        return None
    n_check = 2 if n >= 8 else (1 if n == 7 else 0)
    checks = []
    if n_check == 2 and len(floor) >= 3 and len(top) >= 3:
        checks = [floor[int(rng.random() * len(floor))]['id'], top[int(rng.random() * len(top))]['id']]
    elif n_check >= 1:
        pool = floor if len(floor) >= len(top) else top
        if len(pool) >= 3:
            checks = [pool[int(rng.random() * len(pool))]['id']]
    return chosen, checks


def place_parts(rng, index, btype, W, D, H, zf, cam, w, h):
    walls = WALLS[btype]
    n_target = 2 + (index % 3)
    parts, circles = [], []
    for attempt in range(80):
        if len(parts) >= n_target:
            break
        ptype = PART_TYPES[int(rng.random() * len(PART_TYPES))]
        color = PALETTE[int(rng.random() * len(PALETTE))]
        if ptype == 'counter':
            size = [round(rng.uniform(1.2, 2.4), 2), 0.6, 1.0]
            half = (size[0] / 2 + 0.02, size[1] / 2 + 0.02)
        elif ptype == 'box':
            size = [round(rng.uniform(0.6, 1.4), 2), round(rng.uniform(0.6, 1.2), 2), round(rng.uniform(0.5, 1.2), 2)]
            half = (size[0] / 2, size[1] / 2)
        elif ptype == 'round_table':
            radius = round(rng.uniform(0.35, 0.6), 2)
            size = [radius, radius, 0.74]
            half = (radius, radius)
        else:
            size = [0.5, 0.5, 0.45]
            half = (0.25, 0.25)
        lo_x = -W / 2 + half[0] + 0.3 + (WALL_T + 0.1 if 'left' in walls else 0)
        hi_x = W / 2 - half[0] - 0.3
        lo_y = -D / 2 + half[1] + 0.3
        hi_y = D / 2 - half[1] - 0.3 - (WALL_T + 0.1 if 'back' in walls else 0)
        if hi_x <= lo_x or hi_y <= lo_y:
            continue
        x, y = round(rng.uniform(lo_x, hi_x), 4), round(rng.uniform(lo_y, hi_y), 4)       # the pixel is projected from the stored position
        reach = max(half)
        if any(math.hypot(x - cx, y - cy) < reach + cr + 0.2 for cx, cy, cr in circles):
            continue
        base = camgeom.project_or_none([x, y, zf], cam, w, h)
        if base is None or not (4 <= base[0] <= w - 4 and 4 <= base[1] <= h - 4):
            continue
        top_z = zf + size[2]
        xs = (x - half[0], x + half[0]) if ptype != 'round_table' else (x - size[0], x + size[0])
        ys = (y - half[1], y + half[1]) if ptype != 'round_table' else (y - size[0], y + size[0])
        box_px = [camgeom.project_or_none([a, b, c], cam, w, h) for a in xs for b in ys for c in (zf, top_z)]
        roi = None
        if all(box_px):
            u0, v0 = min(p[0] for p in box_px), min(p[1] for p in box_px)
            u1, v1 = max(p[0] for p in box_px), max(p[1] for p in box_px)
            roi = [round(max(0.0, u0), 1), round(max(0.0, v0), 1), round(min(float(w), u1), 1), round(min(float(h), v1), 1)]
            if not (roi[2] - roi[0] > 3 and roi[3] - roi[1] > 3):
                roi = None
        pid = f'{ptype}_{len(parts) + 1}'
        parts.append({'id': pid, 'type': ptype, 'center_m': [x, y], 'floor_z': zf, 'size_m': size,
                      'color_hex': color, 'base_px': [round(base[0], 3), round(base[1], 3)], 'roi_px': roi})
        circles.append((x, y, reach))
    return parts


def build_case(suite_seed, index):
    """-> (case, scene) for scene number `index`. Deterministic in (suite_seed, index)."""
    btype = BOOTH_TYPES[index % 4]
    W, D, H = SIZES[btype][(index // 4) % len(SIZES[btype])]
    zf = 0.1 if index % 5 == 4 else 0.0
    rng = random.Random(derive_seed(suite_seed, 'case', index))
    for attempt in range(400):
        cam_info = make_camera_for(rng, index, btype, W, D, H, zf, attempt)
        if cam_info is None:
            continue
        w, h = cam_info['width'], cam_info['height']
        rows = project_pool(cam_info['camera'], w, h, W, D, H, zf)
        picked = choose_points(rng, rows)
        if picked is None:
            continue
        chosen, checks = picked
        break
    else:
        raise RuntimeError(f'no usable camera for scene {index}')
    cam = cam_info['camera']
    parts = place_parts(rng, index, btype, W, D, H, zf, cam, w, h)
    anchor = 'WDH'[int(rng.random() * 3)]
    dims = {'W': W, 'D': D, 'H': H}
    walls = list(WALLS[btype])
    open_sides = [s for s in ('back', 'front', 'left', 'right') if s not in walls]
    case = {
        'schema': 'booth-eval-case.v1', 'generator_version': GENERATOR_VERSION, 'case_id': f'synth_{index:04d}', 'index': index,
        'suite_seed': suite_seed, 'booth_type': btype, 'dims_m': {k: float(v) for k, v in dims.items()}, 'floor_z': zf,
        'walls': [{'plane': p, 'thickness_m': WALL_T, 'z_range_m': [zf, float(H)]} for p in walls],
        'open_sides': open_sides, 'roof_frame': True,
        'image': {'width': w, 'height': h, 'cropped': cam_info['cropped'], 'full_frame': cam_info['full_frame']},
        'camera': cam,
        'camera_truth': {k: cam_info[k] for k in ('azimuth_deg', 'pitch_deg', 'yaw_deg', 'focal_px')} | {'hfov_deg': r6(camgeom.hfov_deg(cam))},
        'anchor': anchor,
        'corners': [{'id': r['id'], 'xyz': r['xyz'], 'world_m': r['world_m'], 'uv_px': r['uv_px'], 'role': 'check' if r['id'] in checks else 'fit'}
                    for r in chosen],
        'unused_corners': [{'id': r['id'], 'visible': r['in_image']} for r in rows if r['id'] not in {c['id'] for c in chosen}],
        'check_ids': checks,
        'parts': parts,
    }
    return case, scene_for(case)


def _blank_component(keys):
    return {k: None for k in keys}


COMPONENT_KEYS = ['id', 'type', 'scope', 'group', 'role', 'evidence', 'roi_px', 'color_hex', 'accent_hex', 'base_px', 'base_px_2', 'plane',
                  'wall_rect_px', 'points_px', 'size_m', 'radius_m', 'height_m', 'width_m', 'thickness_m', 'depth_m', 'z_range_m', 'yaw_deg',
                  'count', 'rows', 'module_m', 'inch', 'bezel_m', 'bevel_m', 'bevel_mode', 'inset_m', 'critical', 'note']


def scene_for(case):
    """The clean scene description (original v1 format, no `fit` key) that goes with a case."""
    W, D, H = (case['dims_m'][k] for k in 'WDH')
    zf = case['floor_z']
    comps = []
    for wall in case['walls']:
        c = _blank_component(COMPONENT_KEYS)
        c.update(id=f"wall_{wall['plane']}", type='wall', scope='shell', evidence='inferred', plane=wall['plane'],
                 z_range_m=[zf, float(H)], thickness_m=wall['thickness_m'], color_hex='#e8e8e6', note='Synthetic wall.')
        comps.append(c)
    if case['roof_frame']:
        c = _blank_component(COMPONENT_KEYS)
        c.update(id='frame', type='roof_frame', scope='shell', evidence='inferred', height_m=0.3, width_m=0.3, radius_m=0.1,
                 color_hex='#2b2b2b', note='Synthetic overhead frame.')
        comps.append(c)
    for p in case['parts']:
        c = _blank_component(COMPONENT_KEYS)
        c.update(id=p['id'], type=p['type'], scope='furniture', base_px=list(p['base_px']), color_hex=p['color_hex'],
                 evidence='observed' if p['roi_px'] else 'inferred', roi_px=list(p['roi_px']) if p['roi_px'] else None)
        if p['type'] == 'counter':
            c['size_m'] = list(p['size_m'])
        elif p['type'] == 'box':
            c['size_m'] = [p['size_m'][0], p['size_m'][1], None]
            c['height_m'] = p['size_m'][2]
        elif p['type'] == 'round_table':
            c['radius_m'], c['height_m'] = p['size_m'][0], p['size_m'][2]
        else:
            c['size_m'] = list(p['size_m'])
        comps.append(c)
    anchor = case['anchor']
    return {
        'schema_version': 'scene-description.v1', 'project_id': case['case_id'], 'image': {'width': case['image']['width'], 'height': case['image']['height']},
        'floor_z': zf, 'known': {k: (float(case['dims_m'][k]) if k == anchor else None) for k in 'WDH'}, 'assumed': [],
        'corner_points': [{'id': c['id'], 'uv_px': list(c['uv_px']), 'xyz': list(c['xyz'])} for c in case['corners']],
        'scale_checks': [],
        'scopes': [{'id': 'shell', 'decision': 'include', 'owner': 'booth', 'evidence': 'Synthetic walls and frame.'},
                   {'id': 'furniture', 'decision': 'include', 'owner': 'booth', 'evidence': 'Synthetic floor-standing parts.'}],
        'components': comps, 'uncertainties': [],
        'scale_notes': 'Synthetic ground-truth case; the dimension named in known is exact, the others are to be fitted.',
    }


def dumps(obj):
    return (json.dumps(obj, indent=2, ensure_ascii=False) + '\n').encode('utf-8')


def generate_suite(name, seed, cases=None):
    """-> [(case, scene)] for the first `cases` scenes of a named suite."""
    if name not in SUITES:
        raise ValueError(f'unknown suite {name!r}; choose one of {sorted(SUITES)}')
    n = SUITES[name]['cases'] if cases is None else int(cases)
    return [build_case(seed, i) for i in range(n)]


def suite_files(name, seed, cases=None):
    """-> (manifest, {relative path: bytes}) of a suite: every case.json and scene.json and the suite.json that hashes them."""
    files, entries = {}, []
    for case, scene in generate_suite(name, seed, cases):
        pair = {'case.json': dumps(case), 'scene.json': dumps(scene)}
        for fname, data in pair.items():
            files[f"{case['case_id']}/{fname}"] = data
        entries.append({'case_id': case['case_id'], 'booth_type': case['booth_type'], 'dims_m': case['dims_m'],
                        'sha256': {k: hashlib.sha256(v).hexdigest() for k, v in pair.items()}})
    manifest = {'suite': name, 'seed': seed, 'generator_version': GENERATOR_VERSION, 'perturbations': SUITES[name]['perturbations'],
                'cases': entries}
    files['suite.json'] = dumps(manifest)
    return manifest, files


def suite_fingerprint(name, seed, cases=None):
    """sha256 of suite.json: changes when any case or scene of the suite changes."""
    return hashlib.sha256(suite_files(name, seed, cases)[1]['suite.json']).hexdigest()


def write_suite(name, seed, out_dir, cases=None):
    """Write case folders and suite.json; returns the manifest."""
    manifest, files = suite_files(name, seed, cases)
    for rel, data in files.items():
        target = Path(out_dir) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return manifest


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    p.add_argument('--suite', default='oracle-v1', choices=sorted(SUITES))
    p.add_argument('--seed', type=int, default=20261019)
    p.add_argument('--out', required=True)
    p.add_argument('--cases', type=int, help='Only the first N scenes of the suite')
    a = p.parse_args(argv)
    manifest = write_suite(a.suite, a.seed, a.out, a.cases)
    print(json.dumps({'suite': a.suite, 'seed': a.seed, 'cases': len(manifest['cases']), 'out': str(a.out)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
