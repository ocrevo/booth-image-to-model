"""Render the picture of a synthetic case, with an instance-ID mask. Runs outside Blender (it starts Blender) and inside it.

    python tools/eval/synth_render.py --case output/_eval/cases/synth_0000 --out output/_eval/renders/synth_0000 \
        --skill-dir output/_eval/baseline/booth-image-to-model --samples 16 [--clutter] [--post jpeg=85,noise=2,blur=0.6] [--overlay]

What it does for each case folder (made by synth_generate.py):
  1. builds the true spec: the skill's `boothctl compile` on the case's scene with a fit that is the true camera and the true W/D/H,
     so every part stands exactly where case.json says and the camera is the true camera;
  2. starts one background Blender (only one at a time) that builds the spec with the skill's own geometry and materials, renders
     the picture with Cycles at low samples (denoised), then renders an instance-ID mask with flat emission colours;
  3. optional clutter that is not part of the booth (hall floor, stand-in people, trusses), seeded;
  4. outside Blender again: decodes the mask (R + 256 G = id, 0 = background), writes mask_index.json, and optionally degrades the
     picture (blur, noise, JPEG) the way a phone photo or a small web image would be, and draws the true corners over it (--overlay).

Pictures and masks are results: keep them under output/ and never track them. Needs Blender 4.0+ and the skill folder's scripts
(geometry.py, common.py, blender_build.py); numpy and Pillow outside Blender.
"""
from __future__ import annotations
import argparse
import glob
import json
import math
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
try:
    import bpy                          # present only inside Blender
    IN_BLENDER = True
except ImportError:
    IN_BLENDER = False
    sys.path.insert(0, str(HERE))       # the harness modules are needed outside Blender only

RENDER_VERSION = 1
PEOPLE_COLORS = [(0.10, 0.10, 0.12), (0.25, 0.20, 0.15), (0.45, 0.10, 0.10), (0.12, 0.20, 0.35), (0.35, 0.35, 0.36)]


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes((json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode('utf-8'))


# ------------------------------------------------------------------------------------------------ pure Python (host side)
def srgb_to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def id_to_rgb8(i):
    """Instance id (1..65535) -> the 8-bit RGB the mask is painted with."""
    if not 0 < i < 65536:
        raise ValueError('instance id out of range')
    return (i & 255, (i >> 8) & 255, 0)


def decode_mask(rgb, known_ids):
    """RGB array (h, w, 3) uint8 -> (ids uint16 (h, w), share of pixels that matched no known id and were set to 0)."""
    import numpy as np
    ids = rgb[..., 0].astype(np.uint32) + 256 * rgb[..., 1].astype(np.uint32)
    known = np.zeros(65536, bool)
    known[list(known_ids) + [0]] = True
    bad = (rgb[..., 2] != 0) | ~known[ids]                  # blue is never used by an id colour
    ids[bad] = 0
    return ids.astype(np.uint16), float(bad.mean())


def parse_post(text):
    """'jpeg=85,noise=2,blur=0.6' -> {'jpeg': 85, 'noise': 2.0, 'blur': 0.6}; empty -> {}."""
    out = {}
    for item in filter(None, (text or '').split(',')):
        key, _, value = item.partition('=')
        if key not in ('jpeg', 'noise', 'blur') or not value:
            raise ValueError(f'bad --post item {item!r}; use jpeg=QUALITY, noise=SIGMA, blur=RADIUS')
        out[key] = int(value) if key == 'jpeg' else float(value)
    return out


def degrade(image, post, seed=0):
    """Blur, then additive gaussian noise (sigma in 8-bit levels), then JPEG round trip; -> a new PIL image (RGB)."""
    import io
    import numpy as np
    from PIL import Image, ImageFilter
    img = image.convert('RGB')
    if post.get('blur'):
        img = img.filter(ImageFilter.GaussianBlur(radius=post['blur']))
    if post.get('noise'):
        rng = np.random.default_rng(seed)
        arr = np.asarray(img, dtype=np.float32) + rng.normal(0.0, post['noise'], (img.height, img.width, 1)) * 0.6 \
            + rng.normal(0.0, post['noise'], (img.height, img.width, 3)) * 0.8
        img = Image.fromarray(np.clip(arr + 0.5, 0, 255).astype(np.uint8), 'RGB')
    if post.get('jpeg'):
        buf = io.BytesIO()
        img.save(buf, 'JPEG', quality=int(post['jpeg']))
        buf.seek(0)
        img = Image.open(buf).convert('RGB')
    return img


def draw_overlay(case, picture):
    """The picture with the true corner pixels (ring: fit corner, filled ring: check corner) and part floor points drawn on it."""
    from PIL import ImageDraw
    img = picture.convert('RGB')
    d = ImageDraw.Draw(img)
    for c in case['corners']:
        x, y = c['uv_px']
        col = (255, 60, 60) if c['role'] == 'fit' else (60, 220, 60)
        d.ellipse([x - 7, y - 7, x + 7, y + 7], outline=col, width=2)
        d.text((x + 9, y - 6), c['id'], fill=col)
    for p in case['parts']:
        x, y = p['base_px']
        d.line([x - 6, y, x + 6, y], fill=(40, 120, 255), width=2)
        d.line([x, y - 6, x, y + 6], fill=(40, 120, 255), width=2)
    return img


def find_blender(explicit=None):
    chosen = explicit or os.environ.get('BLENDER_EXE')
    if chosen:
        return chosen if os.path.isfile(chosen) else shutil.which(chosen)
    found = shutil.which('blender')
    if found:
        return found
    pats = []
    for base in (os.environ.get('ProgramFiles') or 'C:/Program Files', os.environ.get('ProgramFiles(x86)')):
        if base:
            pats.append(os.path.join(base, 'Blender Foundation', 'Blender*', 'blender.exe'))
    pats += ['/Applications/Blender.app/Contents/MacOS/Blender', '/snap/bin/blender', '/usr/bin/blender', '/usr/local/bin/blender']
    for pat in pats:
        hits = sorted(glob.glob(pat), reverse=True)
        if hits:
            return hits[0]
    return None


def truth_fit(case):
    """A box_fit-style result that is the truth: what compile needs to place every part exactly where the case says."""
    cam = dict(case['camera'], fit_status='landmark_fitted')
    return {'status': 'fitted', 'rms_px': 0.0, 'max_px': 0.0, 'worst_points': [], 'warnings': [], 'ambiguity_hint': None,
            'dims_m': dict(case['dims_m']), 'dim_source': {k: 'given' for k in 'WDH'}, 'free': {}, 'assumed': [],
            'image': {'width': case['image']['width'], 'height': case['image']['height']}, 'floor_z': case['floor_z'],
            'scale_checks': [], 'ambiguous_dims': [], 'camera': cam, 'points': [],
            'note': 'Ground truth of a synthetic case, not a fit.'}


def build_truth_spec(case_dir, out_dir, skill_dir):
    """Run the skill's compile with the true camera and dimensions. -> path of spec.json."""
    import skill_caps
    from PIL import Image
    case = read_json(Path(case_dir) / 'case.json')
    scene = read_json(Path(case_dir) / 'scene.json')
    caps = skill_caps.detect(skill_dir)
    scene = skill_caps.adapt_scene(scene, caps)
    scene['known'] = {k: float(case['dims_m'][k]) for k in 'WDH'}
    scene['assumed'] = []
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    image = out / 'blank.png'
    Image.new('RGB', (case['image']['width'], case['image']['height']), (128, 128, 128)).save(image)
    write_json(out / 'truth_scene.json', scene)
    write_json(out / 'truth_fit.json', truth_fit(case))
    cmd = [sys.executable, str(Path(skill_dir) / 'scripts' / 'boothctl.py'), 'compile', '--scene', str(out / 'truth_scene.json'),
           '--image', str(image), '--out', str(out / 'spec'), '--fit', str(out / 'truth_fit.json')]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=300,
                       env=dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1'))
    spec = out / 'spec' / 'spec.json'
    if p.returncode != 0 or not spec.is_file():
        raise RuntimeError('compile of the true scene failed: ' + (p.stdout or p.stderr)[-600:])
    return spec


def part_of(case, component_id):
    for p in case['parts']:
        if component_id == p['id'] or component_id.startswith(p['id'] + '_'):
            return p['id']
    return None


def finish_case(case_dir, out_dir, post, seed, overlay):
    """After Blender: decode the mask, write the index, make the degraded picture and the overlay."""
    import numpy as np
    from PIL import Image
    out = Path(out_dir)
    case = read_json(Path(case_dir) / 'case.json')
    index = read_json(out / 'mask_index.json')
    for row in index['objects']:
        row['part_id'] = part_of(case, row['component_id']) if row['kind'] == 'component' else None
    rgb = np.asarray(Image.open(out / 'mask_raw.png').convert('RGB'))
    ids, unmatched = decode_mask(rgb, [o['id'] for o in index['objects']])
    Image.fromarray(np.stack([(ids & 255).astype(np.uint8), (ids >> 8).astype(np.uint8), np.zeros_like(ids, dtype=np.uint8)], axis=-1), 'RGB').save(out / 'mask.png')
    (out / 'mask_raw.png').unlink()
    palette = np.zeros((65536, 3), np.uint8)                          # a human-readable copy: one colour per id, background black
    for row in index['objects']:
        palette[row['id']] = [(row['id'] * 97) % 200 + 55, (row['id'] * 57) % 200 + 55, (row['id'] * 31) % 200 + 55]
    Image.fromarray(palette[ids], 'RGB').save(out / 'mask_preview.png')
    counts = {int(i): int(n) for i, n in zip(*np.unique(ids, return_counts=True))}
    for row in index['objects']:
        row['pixels'] = counts.get(row['id'], 0)
    if unmatched > 0.01:
        print(f'warning: {unmatched:.1%} of the mask pixels match no object id', file=sys.stderr)
    index.update(unmatched_share=unmatched, background_pixels=counts.get(0, 0), encoding='R + 256*G = id; 0 = background',
                 image={'width': int(ids.shape[1]), 'height': int(ids.shape[0])})
    write_json(out / 'mask_index.json', index)
    picture = Image.open(out / 'picture.png').convert('RGB')
    final = picture
    if post:
        final = degrade(picture, post, seed)
        final.save(out / ('picture_degraded.jpg' if post.get('jpeg') else 'picture_degraded.png'), **({'quality': int(post['jpeg'])} if post.get('jpeg') else {}))
    if overlay:
        draw_overlay(case, final).save(out / 'overlay.png')
    return index


def skill_record(skill_dir):
    """Which skill built the picture: version and code fingerprint (scripts/ and schemas/), recorded in render_log.json.

    The truth spec and the Blender scene come from the skill's own compile and geometry, so a picture made with a later skill can
    differ from an earlier one. Without a record two pictures of the same case cannot be told apart."""
    import skill_caps
    root = Path(skill_dir).resolve()
    try:
        label = root.relative_to(HERE.parents[1]).as_posix()
    except ValueError:
        label = f'{root.parent.name}/{root.name}'
    return {'skill_label': label, 'skill_version': skill_caps.skill_version(root), 'skill_commit': skill_caps.baseline_commit(root),
            'skill_fingerprint': skill_caps.fingerprint(root)}


def render_case(case_dir, out_dir, skill_dir, blender=None, samples=16, clutter=False, post=None, seed=0, overlay=False, timeout=3600):
    """One case end to end. -> render log dict."""
    exe = find_blender(blender)
    if not exe:
        raise RuntimeError('Blender not found: pass --blender or set BLENDER_EXE')
    started = time.perf_counter()
    case_dir, skill_dir = Path(case_dir).resolve(), Path(skill_dir).resolve()
    case = read_json(case_dir / 'case.json')
    out = Path(out_dir).resolve()                       # Blender resolves relative paths against its own folders
    out.mkdir(parents=True, exist_ok=True)
    spec = build_truth_spec(case_dir, out, skill_dir)
    cmd = [exe, '--background', '--factory-startup', '--disable-autoexec', '--python-exit-code', '1', '--python', str(Path(__file__).resolve()), '--',
           '--spec', str(spec), '--case', str(Path(case_dir) / 'case.json'), '--out', str(out), '--skill-dir', str(Path(skill_dir).resolve()),
           '--samples', str(samples), '--seed', str(seed)] + (['--clutter'] if clutter else [])
    t_blender = time.perf_counter()
    p = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout)
    blender_s = time.perf_counter() - t_blender
    if p.returncode != 0 or not (out / 'picture.png').is_file():
        raise RuntimeError('Blender failed: ' + ((p.stdout or '') + (p.stderr or ''))[-1500:])
    index = finish_case(case_dir, out, post or {}, seed, overlay)
    log = {'render_version': RENDER_VERSION, **skill_record(skill_dir), 'case_id': case['case_id'], 'samples': samples, 'clutter': bool(clutter), 'post': post or {},
           'seed': seed, 'blender_seconds': round(blender_s, 1), 'total_seconds': round(time.perf_counter() - started, 1),
           'objects': len(index['objects']), 'unmatched_mask_share': index['unmatched_share'], 'image': index['image']}
    write_json(out / 'render_log.json', log)
    return log


# ------------------------------------------------------------------------------------------------------- inside Blender
def blender_main():
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument('--spec', required=True)
    p.add_argument('--case', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--skill-dir', required=True)
    p.add_argument('--samples', type=int, default=16)
    p.add_argument('--seed', type=int, default=0)
    p.add_argument('--clutter', action='store_true')
    a = p.parse_args(argv)
    if not bpy.app.background:
        raise RuntimeError('Run this in a new background Blender; it clears the scene')
    sys.path.insert(0, str(Path(a.skill_dir) / 'scripts'))
    import geometry
    import blender_build as bb
    from common import render_rig
    from mathutils import Vector
    spec = read_json(a.spec)
    case = read_json(a.case)
    out = Path(a.out)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.unit_settings.system = 'METRIC'
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = max(1, a.samples)
    scene.cycles.use_denoising = True
    scene.cycles.use_adaptive_sampling = True
    try:
        scene.cycles.denoiser = 'OPENIMAGEDENOISE'
    except TypeError:
        pass
    scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = spec['source']['width'], spec['source']['height'], 100
    scene.render.pixel_aspect_x = scene.render.pixel_aspect_y = 1
    scene.render.film_transparent = False
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGB'
    scene.display_settings.display_device = 'sRGB'
    scene.view_settings.view_transform = 'Standard'
    scene.view_settings.look = 'None'
    scene.view_settings.exposure = -1.2
    scene.view_settings.gamma = 1.0
    world = bpy.data.worlds.new('Hall')
    world.use_nodes = True
    scene.world = world
    bg = world.node_tree.nodes.get('Background')
    bg.inputs['Color'].default_value = (0.42, 0.43, 0.45, 1)
    bg.inputs['Strength'].default_value = 0.9
    col = bpy.data.collections.new('SYNTH')
    scene.collection.children.link(col)

    objects = []          # (object, kind, component id)

    def add_mesh(name, verts, faces, location, rotation_deg, mat, kind, component_id, smooth_every=None):
        data = bpy.data.meshes.new(name)
        data.from_pydata(verts, [], faces)
        data.update()
        if smooth_every:
            for i, poly in enumerate(data.polygons):
                poly.use_smooth = (i % smooth_every[0]) < smooth_every[1]
        obj = bpy.data.objects.new(name, data)
        col.objects.link(obj)
        obj.location = location
        obj.rotation_euler = [math.radians(v) for v in rotation_deg]
        obj.data.materials.append(mat)
        objects.append((obj, kind, component_id))
        return obj

    for c in spec['components']:
        if c['kind'] == 'text':
            continue
        v, f = geometry.mesh_for(c)
        smooth = (4, 2) if c['kind'] == 'rounded_ring' else ((3, 1) if c['kind'] == 'cylinder' else None)
        add_mesh(c['id'], v, f, c['location'], c['rotation_deg'], bb.material(c['id'] + '_m', c['material'], component=c), 'component', c['id'], smooth)

    # Clutter that is not part of the booth. Seeded; kept out of the lines of sight to the booth's corners.
    if a.clutter:
        rng = random.Random(a.seed * 7919 + case['index'])
        W, D, H, zf = case['dims_m']['W'], case['dims_m']['D'], case['dims_m']['H'], case['floor_z']
        cam_pos = case['camera']['position']
        hall_z = min(0.0, zf - 0.06)
        mat = bb.material('hall_floor_m', {'color': (0.30, 0.30, 0.31, 1), 'roughness': 0.7})
        add_mesh('hall_floor', [(-80, -80, hall_z), (80, -80, hall_z), (80, 80, hall_z), (-80, 80, hall_z)], [(0, 1, 2, 3)], (0, 0, 0), (0, 0, 0), mat, 'clutter', 'hall_floor')
        beam = bb.material('truss_m', {'color': (0.06, 0.06, 0.07, 1), 'roughness': 0.4, 'metallic': 0.5})
        top = H + 1.8
        for k, (cx, cy, sx, sy) in enumerate([(0, D / 2 + 3.0, W + 8, 0.3), (0, -D / 2 - 3.5, W + 8, 0.3), (-W / 2 - 3.0, 0, 0.3, D + 8)]):
            v, f = geometry.box({'size': [sx, sy, 0.3]})
            add_mesh(f'truss_{k}', v, f, (cx, cy, top), (0, 0, 0), beam, 'clutter', f'truss_{k}')
            for sgn in (-1, 1):
                px, py = (cx + sgn * (sx / 2 - 0.2), cy) if sx > sy else (cx, cy + sgn * (sy / 2 - 0.2))
                v, f = geometry.box({'size': [0.3, 0.3, top - hall_z]})
                add_mesh(f'truss_{k}_post_{sgn}', v, f, (px, py, hall_z + (top - hall_z) / 2), (0, 0, 0), beam, 'clutter', f'truss_{k}')
        corners = [c['world_m'] for c in case['corners']]

        def blocks_sight(x, y):
            for cw in corners:
                ax, ay, bx, by = cam_pos[0], cam_pos[1], cw[0], cw[1]
                t = max(0.0, min(1.0, ((x - ax) * (bx - ax) + (y - ay) * (by - ay)) / max(1e-9, (bx - ax) ** 2 + (by - ay) ** 2)))
                if math.hypot(ax + t * (bx - ax) - x, ay + t * (by - ay) - y) < 0.7:
                    return True
            return False
        placed = 0
        for _ in range(60):
            if placed >= 6:
                break
            ang, rad = rng.uniform(0, 2 * math.pi), rng.uniform(0.0, 5.5)
            x, y = math.cos(ang) * (W / 2 + 1.2 + rad), math.sin(ang) * (D / 2 + 1.2 + rad)
            if blocks_sight(x, y):
                continue
            h = rng.uniform(1.55, 1.88)
            body = bb.material(f'person_{placed}_m', {'color': (*PEOPLE_COLORS[placed % len(PEOPLE_COLORS)], 1), 'roughness': 0.8})
            v, f = geometry.cylinder({'radius': 0.22, 'height': h * 0.82, 'segments': 16})
            add_mesh(f'person_{placed}', v, f, (x, y, hall_z + h * 0.41), (0, 0, 0), body, 'clutter', f'person_{placed}')
            skin = bb.material(f'head_{placed}_m', {'color': (0.55, 0.40, 0.32, 1), 'roughness': 0.6})
            v, f = geometry.cylinder({'radius': 0.1, 'height': h * 0.16, 'segments': 16})
            add_mesh(f'head_{placed}', v, f, (x, y, hall_z + h * 0.9), (0, 0, 0), skin, 'clutter', f'person_{placed}')
            placed += 1

    bpy.context.view_layer.update()
    pts = [o.matrix_world @ Vector(c) for o, kind, _ in objects if kind == 'component' for c in o.bound_box]
    bounds = [[min(v[i] for v in pts) for i in range(3)], [max(v[i] for v in pts) for i in range(3)]]
    rig = render_rig(bounds, scene.render.resolution_x / scene.render.resolution_y, spec['camera'])
    cfg = spec['camera']
    cam_data = bpy.data.cameras.new('CAM')
    cam = bpy.data.objects.new('CAM', cam_data)
    col.objects.link(cam)
    cam.location = cfg['position']
    bb.point_camera(cam, cfg['target'], cfg.get('roll_deg', 0))
    cam_data.type = 'PERSP'
    cam_data.sensor_fit = 'HORIZONTAL'
    cam_data.sensor_width = cfg.get('sensor_width_mm', 36)
    cam_data.lens = cfg['lens_mm']
    cam_data.shift_x = 0.5 - cfg['principal_point'][0]
    cam_data.shift_y = (cfg['principal_point'][1] - 0.5) * scene.render.resolution_y / scene.render.resolution_x
    cam_data.clip_start, cam_data.clip_end = 0.05, 600.0
    scene.camera = cam
    for light in rig['lights']:
        ld = bpy.data.lights.new(light['name'], 'AREA')
        ld.energy, ld.shape, ld.size = light['energy'], 'DISK', light['size']
        lo = bpy.data.objects.new(light['name'], ld)
        col.objects.link(lo)
        lo.location = light['position']
        bb.point_camera(lo, light['target'])

    started = time.perf_counter()
    scene.render.filepath = str(out / 'picture.png')
    bpy.ops.render.render(write_still=True)
    beauty_s = time.perf_counter() - started

    # Instance-ID mask: flat emission colours, nothing else lights the frame, one sample, no denoising.
    ids = []
    for n, (obj, kind, component_id) in enumerate(objects, start=1):
        r8, g8, b8 = id_to_rgb8(n)
        m = bpy.data.materials.new(f'id_{n}')
        m.use_nodes = True
        nodes = m.node_tree.nodes
        nodes.clear()
        emit = nodes.new('ShaderNodeEmission')
        emit.inputs['Color'].default_value = (srgb_to_linear(r8 / 255), srgb_to_linear(g8 / 255), srgb_to_linear(b8 / 255), 1)
        emit.inputs['Strength'].default_value = 1.0
        o = nodes.new('ShaderNodeOutputMaterial')
        m.node_tree.links.new(emit.outputs[0], o.inputs['Surface'])
        obj.data.materials.clear()
        obj.data.materials.append(m)
        ids.append({'id': n, 'name': obj.name, 'kind': kind, 'component_id': component_id})
    bg.inputs['Strength'].default_value = 0.0
    bg.inputs['Color'].default_value = (0, 0, 0, 1)
    scene.view_settings.exposure = 0.0                      # the picture's exposure offset must not touch the ID colours
    scene.render.dither_intensity = 0.0
    scene.cycles.samples = 1
    scene.cycles.use_denoising = False
    scene.cycles.use_adaptive_sampling = False
    scene.render.filter_size = 0.01
    scene.render.filepath = str(out / 'mask_raw.png')
    bpy.ops.render.render(write_still=True)
    write_json(out / 'mask_index.json', {'objects': ids, 'beauty_seconds': round(beauty_s, 1), 'blender': bpy.app.version_string})


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    p.add_argument('--case', required=True, nargs='+', help='Case folder(s) holding case.json and scene.json')
    p.add_argument('--out', required=True, help='Output folder; with several cases, one sub-folder per case id')
    p.add_argument('--skill-dir', default=str(HERE.parents[1] / 'booth-image-to-model'),
                   help='Skill folder whose compile and geometry build the picture (default: the working tree; its version and code '
                        'fingerprint go into render_log.json, so use a frozen copy for pictures that must stay comparable)')
    p.add_argument('--blender')
    p.add_argument('--samples', type=int, default=16)
    p.add_argument('--clutter', action='store_true')
    p.add_argument('--post', default='', help='jpeg=QUALITY,noise=SIGMA,blur=RADIUS applied to a copy of the picture')
    p.add_argument('--seed', type=int, default=0)
    p.add_argument('--overlay', action='store_true', help='Also draw the true corners over the picture (debug aid)')
    a = p.parse_args(argv)
    post = parse_post(a.post)
    for case_dir in a.case:
        sub = Path(a.out) / Path(case_dir).name if len(a.case) > 1 else Path(a.out)
        print(json.dumps(render_case(case_dir, sub, a.skill_dir, a.blender, a.samples, a.clutter, post, a.seed, a.overlay)))
    return 0


if __name__ == '__main__':
    if IN_BLENDER:
        blender_main()
    else:
        raise SystemExit(main())
