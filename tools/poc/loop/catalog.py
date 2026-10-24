"""The family catalogue the reader chooses from (docs/poc/02-architecture-and-contracts.md section 1 step 1): one entry per element
kind of booth-design.v1, each with a one-line description, a minimal design.json that compiles, and a thumbnail rendered by the engine.

    python tools/poc/loop/catalog.py [--out output/poc/catalog] [--render-root output/poc/catalog_render] [--commands]

Writes <out>/catalog.json (catalog.v1) and <out>/designs/<kind>.json. The kinds are read from the schema's `kind` enum, so a new
family is in the catalogue as soon as it is in the schema (with a generic line and placement until one is written here). Every
minimal design is also a valid reader design (roles.py): placeholder text only, no free-text fields.
Thumbnails come from the Blender lane (one Blender at a time, under the token): --commands prints, for every kind, the
design-compile and preview command lines with --out-root <render-root>/<kind>. After they ran, run this script again: the newest
<render-root>/<kind>/preview_NNN/preview_render.png becomes <out>/<kind>.png and the thumbnail of that entry. A kind without a
thumbnail has thumbnail null; the text catalogue works on its own.

The loop gives the reader a copy (orchestrator.py --catalog <out>, through load()): catalog.json with only the thumbnails that
exist, the designs, the thumbnails and the current booth-design.v1.json. load() refuses (CatalogError, exit code 2 in the loop) a
catalogue that does not list every kind of the schema exactly once, whose designs are not valid booth-design.v1 JSON with their
kind, or whose thumbnails are not PNG. Without --catalog the loop uses build(): the same catalogue in memory, without
thumbnails. Exit code 0, or 2 when the catalogue cannot be written.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import shutil
import sys
from pathlib import Path

from PIL import Image

try:
    from . import edits
    from . import roles
except ImportError:                  # run as a script: tools/poc/loop is sys.path[0]
    import edits
    import roles

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SCHEMA_PATH = ROOT / 'booth-image-to-model' / 'schemas' / 'booth-design.v1.json'
BOOTHCTL = ROOT / 'booth-image-to-model' / 'scripts' / 'boothctl.py'
TOKEN_SCRIPT = ROOT / 'tools' / 'poc' / 'orchestration' / 'blender_token.py'
DEFAULT_OUT = ROOT / 'output' / 'poc' / 'catalog'
DEFAULT_RENDER_ROOT = ROOT / 'output' / 'poc' / 'catalog_render'
CANVAS = '640x400'
PLACEHOLDER = 'XXXX'
WALL_KINDS = ('feature_wall', 'graphic_panel', 'tv', 'led_wall', 'wall')       # these hang on a closed side
STYLE = {'dominant_hex': '#e9e6df', 'secondary_hex': '#8c6b4f', 'accent_hex': '#2f5d8a'}

# One line per kind (from booth-image-to-model/prompts/booth_design.md) and the placement of its minimal design.
ABOUT = {
    'feature_wall': 'The main wall surface on a closed side: params.variant slats (vertical slats on a backing) or panel; '
                    'full wall length and height unless size_m says.',
    'graphic_panel': 'A graphic board on a closed side, 1.2 x 0.8 m centred at 1.6 m; material.emissive true makes it a lightbox.',
    'tv': 'A screen on a closed side, params.inch 43, 50, 55, 60, 65, 75 or 85, centred at 1.6 m; its content stays neutral.',
    'led_wall': 'An LED wall of whole modules (params.module_m 0.5) on a closed side, 3 x 2 m from 0.5 m up unless size_m says.',
    'wall': 'Restyles the perimeter wall of its side (colour, finish, height in size_m[2]); dropped on an open side.',
    'header_band': 'A fascia band at the top along params.sides (default: the open sides), about 0.5 m high and 0.4 m deep; '
                   'text goes on its outer face; material.emissive true = backlit.',
    'ring': 'A suspended frame inside the footprint (inset 0.5, band 0.4, 0.3 m high); posts are added unless form.hanging_allowed is true.',
    'spotlights': 'count downlights along side, hung under the header band or the ring of that side.',
    'counter': 'A reception counter, 1.8 x 0.55 x 1.05 m, facing the aisle on an open side; params.floating true = recessed dark plinth.',
    'round_table': 'One round table, radius 0.45 m, 0.74 m high.',
    'chair': 'One chair; yaw_deg turns it (0 faces the front).',
    'seating_group': 'A round table with count chairs around it (default 2).',
    'planter': 'count planter boxes in a row along side, pitch_m apart, 0.4 x 0.4 x 0.45 m each.',
    'display_plinth': 'A product plinth with a top, 0.5 x 0.5 x 1.0 m.',
    'storage_block': 'A closed storeroom or cabinet block, 1.5 x 1.2 m, up to 2.5 m high.',
    'partition': 'A free-standing wall parallel to its side, 2.0 x 0.1 m, wall height.',
    'box': 'Anything else as a plain box named by params.role, a 0.6 m cube unless size_m says; the stand-in block for props.',
}
_CENTRE = {'side': 'centre', 'centre_m': [0.0, 0.0]}
ELEMENT = {
    'feature_wall': {'side': 'back', 'material': {'role': 'secondary', 'finish': 'wood'}, 'params': {'variant': 'slats'}},
    'graphic_panel': {'side': 'back'},
    'tv': {'side': 'back', 'params': {'inch': 55}},
    'led_wall': {'side': 'back'},
    'wall': {'side': 'back', 'material': {'role': 'accent'}},
    'header_band': {'params': {'sides': ['front']}, 'text': {'body': PLACEHOLDER, 'size_m': 0.25}, 'provenance': {'text': 'brief'}},
    'ring': {},
    'spotlights': {'side': 'front', 'count': 3},
    'counter': {'side': 'front', 'along': 0.7, 'offset_m': 0.5},
    'round_table': dict(_CENTRE),
    'chair': dict(_CENTRE),
    'seating_group': dict(_CENTRE, count=2),
    'planter': {'side': 'right', 'count': 2, 'pitch_m': 0.9},
    'display_plinth': dict(_CENTRE),
    'storage_block': {'side': 'back', 'along': 0.8},
    'partition': dict(_CENTRE),
    'box': dict(_CENTRE, params={'role': 'prop'}),
}
SUPPORT = {'spotlights': [{'id': 'support_header', 'kind': 'header_band', 'params': {'sides': ['front']}}]}     # what it hangs from
DESIGN_PATH = re.compile(r'designs/([a-z][a-z0-9_]*)\.json')
THUMB_PATH = re.compile(r'([a-z][a-z0-9_]*)\.png')


class CatalogError(ValueError):
    """A catalogue folder that cannot be used."""


def _dump(obj):
    return (json.dumps(obj, indent=2, ensure_ascii=False) + '\n').encode('utf-8')


def kinds(schema_path=SCHEMA_PATH):
    """The element kinds of booth-design.v1, in the schema's order."""
    schema = json.loads(Path(schema_path).read_text(encoding='utf-8'))
    return list(schema['$defs']['element']['properties']['kind']['enum'])


def minimal_design(kind):
    """A complete booth-design.v1 with one element of this kind on a 4 x 3 m stand open to the front (spotlights also get the
    header band they hang from). It is written the way the reader must write: the brief is the placeholder text, or the fixed
    sentence when there is no text."""
    placement = ELEMENT.get(kind) or ({'side': 'back'} if kind in WALL_KINDS else dict(_CENTRE))
    element = {'id': kind, 'kind': kind, 'hero': True, **json.loads(json.dumps(placement))}
    support = json.loads(json.dumps(SUPPORT.get(kind, [])))
    brief = PLACEHOLDER if (element.get('text') or {}).get('body') else roles.READER_BRIEF
    return {'schema_version': 'booth-design.v1', 'project_id': f'catalog_{kind}', 'brief': {'text': brief, 'language': 'en'},
            'form': {'footprint_m': [4.0, 3.0], 'height_limit_m': 3.5, 'open_sides': ['front'], 'hanging_allowed': None},
            'booth_type': 'inline', 'style': dict(STYLE), 'elements': support + [element]}


def build(thumbnails=()):
    """The catalogue in memory. -> (catalog.v1 document, {relative path: bytes}) without thumbnails or the schema file; `thumbnails`
    are the kinds that have one."""
    entries, files = [], {}
    for kind in kinds():
        design = minimal_design(kind)
        problems = edits.validate_design(design)
        if problems:
            raise CatalogError(f'the minimal design of {kind} is not a valid booth-design.v1: {problems[0]}')
        files[f'designs/{kind}.json'] = _dump(design)
        entries.append({'kind': kind, 'about': ABOUT.get(kind, f'A {kind} element; see its minimal design for the fields it uses.'),
                        'design': f'designs/{kind}.json', 'thumbnail': f'{kind}.png' if kind in thumbnails else None})
    doc = {'schema_version': 'catalog.v1', 'design_schema': SCHEMA_PATH.name, 'placeholder_text': PLACEHOLDER, 'kinds': entries}
    files['catalog.json'] = _dump(doc)
    return doc, files


def collect_thumbnails(out, render_root):
    """Copy the newest <render_root>/<kind>/preview_NNN/preview_render.png to <out>/<kind>.png. -> the kinds copied."""
    copied = []
    for kind in kinds():
        found = sorted(Path(render_root).glob(f'{kind}/preview_*/preview_render.png'), key=lambda p: p.parent.name)
        if found:
            shutil.copyfile(found[-1], Path(out) / f'{kind}.png')
            copied.append(kind)
    return copied


def write(out, render_root=None):
    """Write <out>/catalog.json and <out>/designs/<kind>.json (thumbnails collected from render_root first). -> the document."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    if render_root is not None and Path(render_root).is_dir():
        collect_thumbnails(out, render_root)
    doc, files = build({k for k in kinds() if (out / f'{k}.png').is_file()})
    for rel, data in files.items():
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        (out / rel).write_bytes(data)
    return doc


def load(folder):
    """The reader's copy of a catalogue folder. -> (document, {relative path: bytes or the schema path}) with catalog.json
    (thumbnails that do not exist set to null), every design, the existing thumbnails and booth-design.v1.json.

    CatalogError when the folder is unusable: catalog.json missing or not catalog.v1; a schema kind missing, a kind listed twice or a
    kind the schema does not have; an entry with other fields, an about that is not one short line, a design that is not
    designs/<kind>.json, not JSON or not a valid booth-design.v1 holding its kind; a thumbnail that is not <kind>.png or not PNG."""
    folder = Path(folder)
    where = folder / 'catalog.json'
    try:
        doc = json.loads(where.read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise CatalogError(f'no catalog.json in {folder} (run tools/poc/loop/catalog.py --out {folder})')
    except (OSError, ValueError) as ex:
        raise CatalogError(f'{where} is not readable JSON: {ex}')
    if not (isinstance(doc, dict) and doc.get('schema_version') == 'catalog.v1' and isinstance(doc.get('kinds'), list)):
        raise CatalogError(f'{where} is not a catalog.v1 document with a list of kinds')
    listed = [e.get('kind') if isinstance(e, dict) else None for e in doc['kinds']]
    if len(set(map(str, listed))) != len(listed):
        raise CatalogError(f'{where}: a kind is listed twice')
    if set(map(str, listed)) != set(kinds()):
        missing, extra = sorted(set(kinds()) - set(map(str, listed))), sorted(set(map(str, listed)) - set(kinds()))
        raise CatalogError(f'{where} must list every kind of the schema exactly once (missing {missing}, not in the schema {extra}); '
                           'run catalog.py again')
    files = {'booth-design.v1.json': SCHEMA_PATH}
    for e in doc['kinds']:
        kind = e['kind']
        if set(e) != {'kind', 'about', 'design', 'thumbnail'}:
            raise CatalogError(f'{where}: the entry of {kind} needs exactly kind, about, design, thumbnail')
        if not (isinstance(e['about'], str) and e['about'].strip() and '\n' not in e['about'] and len(e['about']) <= 300):
            raise CatalogError(f'{where}: the about of {kind} must be one line of at most 300 characters')
        m = DESIGN_PATH.fullmatch(str(e['design']))
        if not m or m.group(1) != kind:
            raise CatalogError(f'{where}: the design of {kind} must be designs/{kind}.json')
        try:
            blob = (folder / e['design']).read_bytes()
            design = json.loads(blob.decode('utf-8'))
        except (OSError, ValueError) as ex:
            raise CatalogError(f'{folder / e["design"]} is not a readable JSON file: {ex}')
        problems = edits.validate_design(design) if isinstance(design, dict) else ['not an object']
        if problems or kind not in [x.get('kind') for x in design['elements']]:
            raise CatalogError(f'{folder / e["design"]} must be a valid booth-design.v1 with a {kind} element '
                               f'({problems[0] if problems else "no such element"})')
        files[e['design']] = blob
        t = e['thumbnail']
        if t is None:
            continue
        if THUMB_PATH.fullmatch(str(t)) is None or THUMB_PATH.fullmatch(str(t)).group(1) != kind:
            raise CatalogError(f'{where}: the thumbnail of {kind} must be {kind}.png or null')
        if not (folder / t).is_file():
            e['thumbnail'] = None                     # listed but gone: the text entry still works
            continue
        blob = (folder / t).read_bytes()
        try:
            with Image.open(io.BytesIO(blob)) as im:
                fmt = im.format
                im.verify()
        except Exception as ex:                         # PIL raises many kinds of errors for broken files
            raise CatalogError(f'{folder / t} is not a readable picture: {ex}')
        if fmt != 'PNG':
            raise CatalogError(f'{folder / t} must be a PNG, not {fmt}')
        files[t] = blob
    files['catalog.json'] = _dump(doc)
    return doc, files


def reader_files(folder=None):
    """What the reader gets under catalog/: the files of load(folder), or the text catalogue built in memory plus the schema."""
    if folder is not None:
        return load(folder)[1]
    return {**build()[1], 'booth-design.v1.json': SCHEMA_PATH}


def _shown(p):
    p = Path(p)
    try:
        text = p.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        text = str(p)
    return f'"{text}"' if ' ' in text else text


def commands(out, render_root):
    """The command lines that render every thumbnail in the Blender lane (the token is taken around each preview only)."""
    lines = []
    for kind in kinds():
        work = Path(render_root) / kind
        lines += [f'python {_shown(BOOTHCTL)} design-compile --design {_shown(Path(out) / "designs" / f"{kind}.json")} '
                  f'--out {_shown(work / "compile")} --canvas {CANVAS}',
                  f'python {_shown(TOKEN_SCRIPT)} acquire --wait 600 --holder catalog --purpose "catalog thumbnail {kind}"',
                  f'python {_shown(BOOTHCTL)} preview --spec {_shown(work / "compile" / "spec.json")} --out-root {_shown(work)}',
                  f'python {_shown(TOKEN_SCRIPT)} release --holder catalog']
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', default=str(DEFAULT_OUT), help='catalogue folder (default output/poc/catalog, git-ignored)')
    ap.add_argument('--render-root', default=str(DEFAULT_RENDER_ROOT),
                    help='where the thumbnail previews are rendered and collected from (default output/poc/catalog_render)')
    ap.add_argument('--commands', action='store_true', help='also print the command lines that render the thumbnails')
    a = ap.parse_args(argv)
    try:
        doc = write(a.out, a.render_root)
    except (CatalogError, OSError) as ex:
        print(f'catalog: {ex}', file=sys.stderr)
        return 2
    if a.commands:
        print('\n'.join(commands(a.out, a.render_root)))
    print(json.dumps({'out': _shown(a.out), 'kinds': len(doc['kinds']),
                      'thumbnails': [e['kind'] for e in doc['kinds'] if e['thumbnail']]}, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
