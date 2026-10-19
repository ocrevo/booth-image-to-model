"""Write the synthetic inputs of the studio smoke test: a picture, a good scene description and a broken one. (The reading whose
quality gate says "review" is not a file: the stand-in service writes it from scene_fixture.review_scene().)

    python web/tests/make_studio_fixtures.py <out_dir>
"""
from __future__ import annotations
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from PIL import Image, ImageDraw

import scene_fixture as fixtures   # noqa: E402


def write(path, data):
    Path(path).write_bytes((json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))


def main(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    image = Image.new('RGB', (fixtures.IMG_W, fixtures.IMG_H), (150, 150, 150))
    ImageDraw.Draw(image).rectangle([0, fixtures.IMG_H // 2, fixtures.IMG_W, fixtures.IMG_H], fill=(110, 110, 110))
    image.save(out / 'booth.png')
    write(out / 'scene.json', fixtures.scene())
    bad = fixtures.scene()
    bad['components'][0]['scope'] = 'nowhere'
    write(out / 'bad_scene.json', bad)
    print(f'wrote booth.png, scene.json, bad_scene.json to {out}')


if __name__ == '__main__':
    main(sys.argv[1])
