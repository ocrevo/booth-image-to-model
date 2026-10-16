"""Build the synthetic demo booth: web/app/demo/demo-booth.spec.json and its "source picture" demo_source.png.

Everything here is invented (a generic 6 x 5 m open-front stand made from the skill's macros); no client image, brand, or
measured dimension is involved. The demo picture is a Blender render of this same synthetic booth from the spec's camera, so the
studio's before/after lines up exactly; it is marked "synthetic demo". Its use and how to swap it are in
docs/sample-images.md; if it is missing, the studio shows the demo model on a plain stand-in. Re-run after changing the
macros:

    python web/tools/make_demo.py                  # spec only; keeps an existing demo_source.png (or writes a plain stand-in)
    python web/tools/make_demo.py --render         # also renders demo_source.png with Blender (BLENDER_EXE or --blender)
"""
from __future__ import annotations
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / 'app' / 'demo'      # the deployable site folder; tools/ and tests/ are not served
sys.path.insert(0, str(HERE.parents[1] / 'booth-image-to-model' / 'scripts'))
import components as cm  # noqa: E402

ZF = 0.1                     # platform top
W, H = 960, 600              # placeholder "source" frame; only its aspect matters to the editor


def placeholder(src):
    img = Image.new('RGB', (W, H), (214, 218, 222))
    ImageDraw.Draw(img).text((24, 24), 'synthetic demo - not a real booth photo', fill=(90, 96, 104))
    img.save(src)


def build_spec(src):
    b = cm.SpecBuilder(str(src), W, H, 'web_editor_demo')
    b.source['image'] = src.name            # relative to the spec, so the folder can move
    for sid, why in (('shell', 'Synthetic platform, back wall, header and screens.'),
                     ('media', 'Synthetic wall screens.'),
                     ('furniture', 'Synthetic counter, tables, chairs and planter.'),
                     ('lighting', 'Synthetic spotlight bodies.')):
        b.scope(sid, why)

    b.box('floor', 'shell', 'floor', (-3.0, 3.0), (-2.5, 2.5), (0.0, ZF), cm.WOOD, critical=True, inferred=True,
          note='Synthetic platform.')
    b.box('back_wall', 'shell', 'back_wall', (-3.0, 3.0), (2.4, 2.5), (ZF, 3.0), cm.WHITE, critical=True, inferred=True,
          note='Synthetic back wall.')
    cm.louvre_wall(b, 'louvres', 'shell', (-1.6, 1.4), (2.78, 2.9), (ZF, 2.6), 8, 0.08, [0.55, 0.40, 0.28, 1], along='y',
                   inferred=True)
    cm.header_band(b, 'header', 'shell', [(-2.6, 1.9), (-2.6, -2.1), (2.6, -2.1), (2.6, 1.9)], 0.4, (2.5, 3.1),
                   posts=[(-2.6, -2.1), (2.6, -2.1)], floor_z=ZF, inferred=True)
    cm.tv(b, 'tv', 'media', 2.4, -2.0, 55, bezel=0.02, depth=0.05, inferred=True)
    cm.led_wall(b, 'led', 'media', 2.4, -1.0, 0.7, cols=4, rows=3, module=0.5, depth=0.12, inferred=True)
    cm.spotlights(b, 'spot', 'lighting', [(-1.5, -2.1), (0.0, -2.1), (1.5, -2.1)], 2.5, inferred=True)
    cm.counter(b, 'counter', 'furniture', (-0.9, 0.9), (-1.9, -1.35), floor_z=ZF, inferred=True)
    for name, tx in (('table_a', 1.7), ('table_b', -1.7)):
        cm.round_table(b, name, 'furniture', tx, 0.3, floor_z=ZF, inferred=True)
    for name, cx, cy, yaw in (('chair_a1', 2.4, 0.3, 90), ('chair_a2', 1.0, 0.3, -90),
                              ('chair_b1', -1.0, 0.3, 90), ('chair_b2', -2.4, 0.3, -90)):
        cm.shell_chair(b, name, 'furniture', cx, cy, yaw, floor_z=ZF, inferred=True)
    cm.planter(b, 'planter', 'furniture', (2.2, 2.8), (-2.0, -1.4), floor_z=ZF, color=cm.WHITE, inferred=True)

    b.scale((6.0, 5.0), 'Synthetic dimensions invented for the editor demo; not measured from anything.')
    b.camera({'projection': 'perspective', 'position': [7.5, -9.0, 4.8], 'target': [0.0, 0.0, 1.2], 'lens_mm': 36.0,
              'sensor_width_mm': 36.0, 'principal_point': [0.5, 0.5], 'pixel_aspect': [1, 1],
              'fit_status': 'unfitted', 'roll_deg': 0.0})
    b.uncertain('synthetic_demo', 'Everything in this file is synthetic test content.')
    b.write(OUT / 'demo-booth.spec.json')
    path = OUT / 'demo-booth.spec.json'
    path.write_bytes(path.read_bytes().replace(b'\r\n', b'\n'))      # the repository stores LF
    print('wrote', path)
    return path


def render_picture(spec_path, src, blender):
    """Render the demo booth from its own camera and use that render (marked synthetic) as the demo picture."""
    skill = HERE.parents[1] / 'booth-image-to-model' / 'scripts'
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [blender, '--background', '--factory-startup', '--disable-autoexec', '--python-exit-code', '1',
               '--python', str(skill / 'blender_build.py'), '--', '--spec', str(spec_path), '--out', tmp, '--render', '--samples', '32']
        subprocess.run(cmd, check=True, capture_output=True)
        with Image.open(Path(tmp) / 'client_render.png') as im:
            img = im.convert('RGB')
    ImageDraw.Draw(img).text((16, H - 24), 'synthetic demo - rendered from the demo spec, not a real booth', fill=(70, 74, 80))
    img.save(src)


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    p.add_argument('--render', action='store_true', help='re-render demo_source.png with Blender')
    p.add_argument('--blender', default=os.environ.get('BLENDER_EXE') or shutil.which('blender'))
    a = p.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    src = OUT / 'demo_source.png'
    if a.render or not src.is_file():
        placeholder(src)
    spec = build_spec(src)
    if a.render:
        if not a.blender:
            raise SystemExit('--render needs Blender: pass --blender or set BLENDER_EXE')
        render_picture(spec, src, a.blender)
        build_spec(src)                     # the spec records the new picture's checksum
        print('rendered', src)


if __name__ == '__main__':
    main()
