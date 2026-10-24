"""Score one preview against its picture and answer sheet; write score.json (score.v1), score_detail.json, sheet.png and panel_view.png.

    python tools/poc/score/score.py --run <preview_dir> --photo <picture> --truth <truth_dir> --fit <box_fit.json> [--panel <panel>]
                                    --out <score.json> --candidate-id <id> [--map <map.json>]
    python tools/poc/score/score.py --validate-truth <truth_dir> [--camera <spec.json> [--map <map.json>]]

The preview folder holds preview_render.png (RGBA, source size) and spec.json. The answer sheet is the three truth.v1 files of
docs/poc/03-scoring-spec.md section 2. --panel is either a folder of judge.v1 scoring responses for this one candidate (a
second pair_id in it is an error, never mixed) or a tally.v1 file, from which the entry of --candidate-id is used. --candidate-id is
required: preview folder names are not identities. --map {"inventory_id": "component id or group" or [names]} tells which spec components make up a part
of the answer sheet; a part that the map does not list, or any part without --map, is found by exact id or group only (no guessing by prefix).
A mapped name that the scored spec lacks makes the point missing (missing_reason mapped_name_absent), not an error; a map key that is not an
inventory id is an error.
--fit is required: the corner fit of the picture (box_fit.json, or truth_fit.json for the second anchor). Its box, projected with its camera, is the
booth region the edge distance is computed in (03 section 3.1 rule 3), the same for every candidate of the picture; score_detail.json reports
edge.region_px and edge.render_outside_region. A missing --fit, a fit camera that differs from the spec's by more than 1e-6, or a fit made for another
picture size is an error (exit 2, nothing written); there is no fallback to a region taken from the render.
Without a panel the status is blocked (reason panel_missing) and the code part is still written. Exit code 0 when a score file
was written (measured or blocked), 2 for unusable input (different canvas sizes, an invalid answer sheet, mixed panel, a map key that is not an
inventory id, a render without alpha, a malformed tally); nothing is rescaled or guessed. sheet.png is for people; panel_view.png (no text, no boxes) is what judges see.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

try:
    from . import metrics
except ImportError:                  # run as a script: tools/poc/score is sys.path[0]
    import metrics

HERE = Path(__file__).resolve().parent
TRUTH_FILES = ('inventory.json', 'holdout_px.json', 'swatches.json')
ROLES = ('dominant', 'secondary', 'accent', 'screen')
INV_VALUES = ('missing', 'present')                      # ordered worst to best
EL_VALUES = ('wrong', 'off', 'ok')
EL_POINTS = {'ok': 2, 'off': 1, 'wrong': 0}
EL_COLOUR = {'ok': (0, 200, 0), 'off': (255, 200, 0), 'wrong': (230, 0, 0), None: (160, 160, 160)}
JUDGE_FIELDS = ('schema_version', 'pair_id', 'judge_id', 'pass', 'better', 'scores', 'items', 'confidence', 'extra_parts')
SCORE_MAX = {'L2': 20.0, 'L3': 20.0, 'L4': 10.0}
LEVEL_MAX = {'L1': 30.0, 'L2': 20.0, 'L3': 20.0, 'L4': 10.0}
ID = re.compile(r'^[a-z][a-z0-9_]*$')


class InputError(ValueError):
    """Unusable input: reported on stderr, exit code 2."""


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise InputError(f'FILE_MISSING: {path}')
    except (OSError, ValueError) as e:
        raise InputError(f'BAD_JSON: {path}: {e}')


def write_json(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write('\n')


# ---- answer sheet --------------------------------------------------------------------------------------------------

def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _text(v):
    return isinstance(v, str) and bool(v)


def _keys(obj, required, where, errors):
    if not isinstance(obj, dict):
        errors.append(f'{where}: expected an object')
        return False
    for k in sorted(set(required) - set(obj)):
        errors.append(f'{where}.{k}: required field missing')
    for k in sorted(set(obj) - set(required)):
        errors.append(f'{where}.{k}: field not in the contract')
    return True


def validate_truth(folder):
    """Check the three answer-sheet files. Returns {valid, errors, warnings}; any missing or mistyped field makes it invalid."""
    folder = Path(folder)
    errors, warnings, docs = [], [], {}
    for name in TRUTH_FILES:
        try:
            docs[name] = read_json(folder / name)
        except InputError as e:
            errors.append(str(e))
    pics = set()
    for name, doc in docs.items():
        if not isinstance(doc, dict):
            errors.append(f'{name}: expected an object')
            continue
        if doc.get('schema_version') != 'truth.v1':
            errors.append(f'{name}.schema_version: expected truth.v1')
        if not _text(doc.get('picture_id')):
            errors.append(f'{name}.picture_id: a non-empty string is required')
        else:
            pics.add(doc['picture_id'])
    if len(pics) > 1:
        errors.append(f'picture_id differs between files: {sorted(pics)}')

    inv_ids = set()
    inv = docs.get('inventory.json')
    if isinstance(inv, dict):
        items = inv.get('items')
        _keys(inv, ('schema_version', 'picture_id', 'items'), 'inventory.json', errors)
        if not isinstance(items, list) or not 8 <= len(items) <= 15:
            errors.append('inventory.json.items: 8 to 15 parts are required')
        else:
            for n, it in enumerate(items):
                where = f'inventory.json.items[{n}]'
                if not _keys(it, ('id', 'name', 'kind_hint', 'hero'), where, errors):
                    continue
                if 'id' in it:
                    if not (isinstance(it['id'], str) and ID.match(it['id'])):
                        errors.append(f'{where}.id: a lowercase identifier string is required')
                    elif it['id'] in inv_ids:
                        errors.append(f'{where}.id: duplicate {it["id"]}')
                    else:
                        inv_ids.add(it['id'])
                for k in ('name', 'kind_hint'):
                    if k in it and not _text(it[k]):
                        errors.append(f'{where}.{k}: a non-empty string is required')
                if 'hero' in it and not isinstance(it['hero'], bool):
                    errors.append(f'{where}.hero: true or false required')
            heroes = sum(1 for it in items if isinstance(it, dict) and it.get('hero') is True)
            if not 2 <= heroes <= 3:
                errors.append(f'inventory.json: 2 to 3 hero parts are required, found {heroes}')

    hold = docs.get('holdout_px.json')
    if isinstance(hold, dict):
        _keys(hold, ('schema_version', 'picture_id', 'points'), 'holdout_px.json', errors)
        pts = hold.get('points')
        if not isinstance(pts, list) or len(pts) != 4:
            errors.append('holdout_px.json.points: exactly 4 held-out points are required')
        else:
            seen, parts = set(), []
            for n, p in enumerate(pts):
                where = f'holdout_px.json.points[{n}]'
                if not _keys(p, ('id', 'part_id', 'corner', 'uv_px', 'note'), where, errors):
                    continue
                if 'id' in p:
                    if not _text(p['id']):
                        errors.append(f'{where}.id: a non-empty string is required')
                    elif p['id'] in seen:
                        errors.append(f'{where}.id: duplicate {p["id"]}')
                    else:
                        seen.add(p['id'])
                if 'part_id' in p:
                    if not _text(p['part_id']):
                        errors.append(f'{where}.part_id: a non-empty string is required')
                    else:
                        if inv_ids and p['part_id'] not in inv_ids:
                            errors.append(f'{where}.part_id: {p["part_id"]!r} is not in inventory.json')
                        parts.append(p['part_id'])
                if 'corner' in p and metrics.corner_words(p['corner']) is None:
                    errors.append(f'{where}.corner: {p["corner"]!r} is not valid; {metrics.CORNER_NAME_HELP}')
                if 'uv_px' in p and not (isinstance(p['uv_px'], list) and len(p['uv_px']) == 2 and all(_num(v) for v in p['uv_px'])):
                    errors.append(f'{where}.uv_px: two numbers required')
                if 'note' in p and not isinstance(p['note'], str):
                    errors.append(f'{where}.note: a string is required')
            if len(set(parts)) < len(parts):
                warnings.append('holdout_px.json: two points share a part; spread them over different parts and depths')

    sw = docs.get('swatches.json')
    if isinstance(sw, dict):
        _keys(sw, ('schema_version', 'picture_id', 'swatches'), 'swatches.json', errors)
        rows = sw.get('swatches')
        if not isinstance(rows, list) or not 3 <= len(rows) <= 5:
            errors.append('swatches.json.swatches: 3 to 5 swatches are required')
        else:
            seen = set()
            for n, s in enumerate(rows):
                where = f'swatches.json.swatches[{n}]'
                if not _keys(s, ('id', 'bbox_px', 'role'), where, errors):
                    continue
                if 'id' in s:
                    if not _text(s['id']):
                        errors.append(f'{where}.id: a non-empty string is required')
                    elif s['id'] in seen:
                        errors.append(f'{where}.id: duplicate {s["id"]}')
                    else:
                        seen.add(s['id'])
                b = s.get('bbox_px')
                if 'bbox_px' in s and not (isinstance(b, list) and len(b) == 4 and all(_num(v) for v in b)
                                           and b[0] >= 0 and b[1] >= 0 and b[2] > b[0] and b[3] > b[1]):
                    errors.append(f'{where}.bbox_px: [x0, y0, x1, y1] with x1 > x0 >= 0 and y1 > y0 >= 0 required')
                if 'role' in s and not (isinstance(s['role'], str) and s['role'] in ROLES):
                    errors.append(f'{where}.role: one of {list(ROLES)} required')
    return {'valid': not errors, 'errors': errors, 'warnings': warnings}


def _finite_list(v, n):
    return isinstance(v, list) and len(v) == n and all(_num(x) for x in v)


def read_camera_spec(path):
    """(spec, camera, width, height) from a spec.json that holds the fitted camera, the components and the source size.

    Anything the picture maths cannot use is BAD_CAMERA (exit 2): missing or malformed numbers, a lens or sensor of zero, the
    position at the target, a camera looking straight up or down, an orthographic camera, a source size of zero."""
    spec = read_json(path)
    cam = spec.get('camera') if isinstance(spec, dict) else None
    src = spec.get('source') if isinstance(spec, dict) else None

    def bad(why):
        raise InputError(f'BAD_CAMERA: {path}: {why}')

    if not isinstance(cam, dict):
        bad('no fitted camera')
    if not _finite_list(cam.get('position'), 3) or not _finite_list(cam.get('target'), 3):
        bad('position and target need three numbers each')
    if not (_num(cam.get('lens_mm')) and cam['lens_mm'] > 0 and _num(cam.get('sensor_width_mm')) and cam['sensor_width_mm'] > 0):
        bad('lens_mm and sensor_width_mm must be numbers above zero')
    if cam.get('projection', 'perspective') != 'perspective':
        bad('only perspective cameras are supported')
    if 'principal_point' in cam and not _finite_list(cam['principal_point'], 2):
        bad('principal_point needs two numbers')
    if 'roll_deg' in cam and not _num(cam['roll_deg']):
        bad('roll_deg must be a number')
    if cam['position'] == cam['target']:
        bad('the camera position equals its target')
    try:
        metrics.camera_math.axes(cam)
    except ValueError as e:
        bad(str(e))
    if not (isinstance(src, dict) and _num(src.get('width')) and _num(src.get('height')) and src['width'] > 0 and src['height'] > 0):
        bad('source needs a width and a height above zero (the picture pixels)')
    if not isinstance(spec.get('components'), list):
        bad('no components, so the part boxes cannot be found')
    return spec, cam, float(src['width']), float(src['height'])


FIT_CAMERA_TOL = 1e-6
FIT_CAMERA_DEFAULTS = {'principal_point': [0.5, 0.5], 'roll_deg': 0.0}


def _camera_values(camera):
    """The numbers that decide where a point lands in the picture, defaults filled in; None when one is missing or not a number."""
    flat = []
    for key in ('position', 'target', 'lens_mm', 'sensor_width_mm', 'principal_point', 'roll_deg'):
        v = camera.get(key, FIT_CAMERA_DEFAULTS.get(key))
        for x in (v if isinstance(v, list) else [v]):
            if not _num(x):
                return None
            flat.append(float(x))
    return flat


def read_fit(path, spec_camera, width, height):
    """The corner fit (box_fit.json) that fixes the booth region, checked against the spec being scored and the picture.

    InputError (exit 2) when the file is missing or not an object, has no camera or image size, when its camera differs from the
    spec's by more than 1e-6 in position, target, lens_mm, sensor_width_mm, principal_point (default 0.5, 0.5) or roll_deg (default 0),
    or when its image size is not the picture's. There is no fallback to a region taken from the render."""
    fit = read_json(path)
    if not isinstance(fit, dict):
        raise InputError(f'BAD_FIT: {path}: not a JSON object')
    cam, image = fit.get('camera'), fit.get('image')
    if not isinstance(cam, dict):
        raise InputError(f'BAD_FIT: {path}: no camera')
    if not (isinstance(image, dict) and _num(image.get('width')) and _num(image.get('height'))):
        raise InputError(f'BAD_FIT: {path}: no image width and height')
    if (image['width'], image['height']) != (width, height):
        raise InputError(f'FIT_IMAGE_MISMATCH: the fit is for a {image["width"]}x{image["height"]} picture, the picture is {width}x{height}')
    a, b = _camera_values(cam), _camera_values(spec_camera) if isinstance(spec_camera, dict) else None
    if a is None or b is None:
        raise InputError(f'BAD_FIT: {path}: the fit camera or the spec camera lacks numbers (position, target, lens_mm, sensor_width_mm)')
    if len(a) != len(b) or max(abs(x - y) for x, y in zip(a, b)) > FIT_CAMERA_TOL:
        raise InputError(f'FIT_CAMERA_MISMATCH: {path}: the fit was made with another camera than the spec being scored; '
                         'score with the fit that belongs to this camera (nothing is guessed)')
    return fit


def _px(point, camera, w, h):
    u, v, _ = metrics.camera_math.project(point, camera, w, h)
    return [round(u, 2), round(v, 2)]


FAR_FROM_UV_DIAG = 0.02              # a name landing further than this share of the diagonal from uv_px draws a warning


def _name_entry(family, name, bounds, camera, w, h, uv):
    """One name of the full list: the corner it picks, its pixel, the distance to uv_px and whether it keeps its corner."""
    entry = {'family': family, 'name': name, 'corner_world': None, 'px': None, 'distance_px': None, 'has_margin': False,
             'missing_reason': None}
    try:
        world = metrics.corner_point(bounds, name, camera, w, h)
        entry['corner_world'] = [round(x, 6) for x in world]
        entry['px'] = _px(world, camera, w, h)
        entry['distance_px'] = round(math.hypot(entry['px'][0] - uv[0], entry['px'][1] - uv[1]), 2)
        entry['has_margin'] = metrics.name_margin(name, bounds, camera, w, h)
    except metrics.CornerBehindCamera:
        entry['missing_reason'] = 'corner_behind_camera'
    except ValueError:
        entry['missing_reason'] = 'behind_camera' if entry['corner_world'] else 'camera_unusable'
    return entry


def draft_report(folder, spec_path, map_path=None):
    """validate_truth plus, for every hold-out point, where its name lands and whether the name keeps its corner (03 section 2).

    Each row echoes the point's note and gives the corner its name picks, the pixel, the distance to uv_px and has_margin (the name
    picks the same corner for the four cameras of metrics.turned_cameras: orbit +-5 degrees about the vertical axis through the
    target, elevation +-2 degrees, varied separately). `names` lists every valid name of both families, ordered by family
    (three_word, then one_horizontal) and name, never by distance, each with the corner it picks, its pixel, the distance to uv_px
    and has_margin, so that the name that matches the note can be found. Written for names that fail too. Errors (exit 2): a name
    without margin, a name that cannot be placed (invalid, corner behind the camera, unusable camera), or nothing that could be
    checked. Warnings: a part missing from the spec, a name landing more than 2 % of the diagonal from uv_px.
    """
    result = validate_truth(folder)
    spec, camera, w, h = read_camera_spec(spec_path)
    try:
        inventory = read_json(Path(folder) / 'inventory.json')
        inv_ids = {i['id'] for i in inventory['items'] if isinstance(i, dict) and isinstance(i.get('id'), str)}
        points = read_json(Path(folder) / 'holdout_px.json')['points']
    except (InputError, KeyError, TypeError):
        inv_ids, points = set(), []
    mapping = read_map(map_path, inv_ids) if map_path else {}
    diag = math.hypot(w, h)
    rows, checked = [], 0
    for n, p in enumerate(points if isinstance(points, list) else []):
        ok = (isinstance(p, dict) and _text(p.get('id')) and _text(p.get('part_id')) and isinstance(p.get('corner'), str)
              and isinstance(p.get('uv_px'), list) and len(p['uv_px']) == 2 and all(_num(v) for v in p['uv_px']))
        if not ok:
            continue
        canonical = metrics.canonical_corner(p['corner'])
        row = {'id': p['id'], 'part_id': p['part_id'], 'corner': p['corner'], 'canonical_corner': canonical,
               'note': p.get('note'), 'uv_px': p['uv_px'],
               'matched_ids': [], 'missing_reason': None, 'picked_world': None, 'picked_px': None, 'distance_px': None,
               'has_margin': None, 'names': []}
        comps = metrics.matching_components(spec, p['part_id'], mapping.get(p['part_id']))
        row['matched_ids'] = [c.get('id') for c in comps]
        where = f'holdout_px.json.points[{n}]'
        rows.append(row)
        if not comps:
            row['missing_reason'] = 'mapped_name_absent' if p['part_id'] in mapping else 'part_missing'
            result['warnings'].append(f'{where}: no component of the spec belongs to {p["part_id"]!r}; its name cannot be checked')
            continue
        bounds = metrics.points_bounds([q for c in comps for q in metrics.component_points(c)])
        row['names'] = sorted((_name_entry(fam, nm, bounds, camera, w, h, p['uv_px'])
                               for fam, names in (('three_word', metrics.THREE_WORD_NAMES), ('one_horizontal', metrics.ONE_HORIZONTAL_NAMES))
                               for nm in names), key=lambda e: (0 if e['family'] == 'three_word' else 1, e['name']))
        broken = [e for e in row['names'] if e['missing_reason'] == 'camera_unusable']
        own = next((e for e in row['names'] if e['name'] == canonical), None)
        if own is None:                                          # a name that is not valid in either family
            row['missing_reason'], row['has_margin'] = 'invalid_name', False
        else:
            row.update(missing_reason=own['missing_reason'], picked_world=own['corner_world'], picked_px=own['px'],
                       distance_px=own['distance_px'], has_margin=own['has_margin'])
        if broken and row['missing_reason'] is None:
            row['missing_reason'], row['has_margin'] = 'camera_unusable', False
            result['errors'].append(f'{where}: the camera could not place {len(broken)} name(s) of the list (camera_unusable)')
        if row['missing_reason'] is not None:
            row['has_margin'] = False
            result['errors'].append(f'{where}.corner: {p["corner"]!r} cannot be placed ({row["missing_reason"]})')
            continue
        checked += 1
        if not row['has_margin']:
            result['errors'].append(f'{where}.corner: {p["corner"]!r} loses its corner when the camera orbits {metrics.MARGIN_YAW_DEG:g} degrees '
                                    f'or its elevation changes by {metrics.MARGIN_PITCH_DEG:g}; use a name of the other family or another '
                                    'corner (see names)')
        if row['distance_px'] is not None and row['distance_px'] > FAR_FROM_UV_DIAG * diag:
            result['warnings'].append(f'{where}.corner: {p["corner"]!r} lands {row["distance_px"]:g} px from uv_px; '
                                      'check that it says what the note says')
    if rows and not checked and not any(r['missing_reason'] not in (None, 'part_missing', 'mapped_name_absent') for r in rows):
        result['errors'].append('no hold-out point could be checked: every part is missing from the spec')
    result['valid'] = not result['errors']
    result['points'] = rows
    return result


# ---- panel ---------------------------------------------------------------------------------------------------------

def _item_key(key, inventory_ids):
    """('L1' or 'L2', inventory id) for a key like 'L1:<id>', else None."""
    if isinstance(key, str) and key[:3] in ('L1:', 'L2:') and key[3:] in inventory_ids:
        return key[:2], key[3:]
    return None


def validate_judge(d, inventory_ids):
    """Strict judge.v1 check for a scoring-mode response (02 section 4.4). Returns a list of problems.

    items keys are 'L1:<inventory id>' (present or missing) and 'L2:<inventory id>' (ok, off or wrong).
    """
    errors = []
    if not _keys(d, JUDGE_FIELDS, 'response', errors):
        return errors
    if d.get('schema_version') != 'judge.v1':
        errors.append('schema_version: expected judge.v1')
    if 'pair_id' in d and not _text(d['pair_id']):
        errors.append('pair_id: a non-empty string is required')
    if 'judge_id' in d and d['judge_id'] not in ('j1', 'j2', 'j3'):
        errors.append('judge_id: j1, j2 or j3 required')
    if 'pass' in d and not (d['pass'] in (1, 2) and not isinstance(d['pass'], bool)):
        errors.append('pass: 1 or 2 required')
    if 'better' in d and d['better'] not in ('A', 'B', 'same'):
        errors.append('better: A, B or same required')
    if 'confidence' in d and d['confidence'] not in ('low', 'medium', 'high'):
        errors.append('confidence: low, medium or high required')
    if d.get('items') is None:
        errors.append('items: null means a tie-break response; a scoring response is required here')
    elif not isinstance(d['items'], dict):
        errors.append('items: an object is required')
    else:
        for k, v in d['items'].items():
            parsed = _item_key(k, inventory_ids)
            if parsed is None:
                errors.append(f'items.{k}: the key must be L1:<inventory id> or L2:<inventory id> with an id from the inventory')
            elif not (isinstance(v, str) and v in (INV_VALUES if parsed[0] == 'L1' else EL_VALUES)):
                errors.append(f'items.{k}: ' + ('present or missing' if parsed[0] == 'L1' else 'ok, off or wrong') + ' required')
    sc = d.get('scores')
    if 'scores' in d and d.get('items') is not None:
        if not (isinstance(sc, dict) and set(sc) == set(SCORE_MAX)):
            errors.append('scores: {L2, L3, L4} required in scoring mode')
        else:
            for k, top in SCORE_MAX.items():
                if not (_num(sc[k]) and 0 <= sc[k] <= top):
                    errors.append(f'scores.{k}: a number from 0 to {top:g} required')
        ep = d.get('extra_parts')
        if 'extra_parts' in d and not (isinstance(ep, int) and not isinstance(ep, bool) and ep >= 0):
            errors.append('extra_parts: an integer >= 0 required in scoring mode')
    return errors


def _majority(votes):
    """The answer that at least two judges gave, else None (undecided). One vote, or all different, is no majority."""
    if not votes:
        return None
    answer, count = Counter(votes).most_common(1)[0]
    return answer if count >= 2 else None


def panel_level_scores(l1, l2, l3_median, l4_median, n_items, extra):
    """Level scores from decided items: L1 = 30 x present share - min(5, extra); L2 = 20 x points / (2 x items).

    An undecided or missing item has no L1 'present' and no L2 points, so it counts as missing and scores 0. L2 has no other
    source: with no L2 majority anywhere it is 0.
    """
    present = sum(1 for v in l1.values() if v == 'present')
    deduction = min(5.0, float(extra)) if extra is not None else 0.0
    points = sum(EL_POINTS[v] for i, v in l2.items() if l1.get(i) == 'present')
    return {'L1': max(0.0, 30.0 * present / n_items - deduction), 'L2': 20.0 * points / (2.0 * n_items),
            'L3': l3_median, 'L4': l4_median}


def aggregate_panel(responses, inventory_ids, names=None):
    """The one place where judge.v1 scoring responses of ONE candidate become panel scores (P2's tally imports this).

    responses: parsed JSON of each response file; inventory_ids: the ids of the answer sheet's inventory; names: optional file
    names for the report. Invalid responses (strict judge.v1, see validate_judge) are listed under panel_ignored and not
    counted. More than one pair_id among the valid ones raises InputError PANEL_MIXED (pair_id is the blinded id of the
    candidate, shared by all its requests and both passes). A judge that lacks pass 1 or pass 2 (or has one twice) is undecided
    as a whole. For the rest, an item whose two passes disagree is undecided for that judge; across judges an answer needs at least two judges
    giving it (majority); otherwise the item is undecided (L1 counts it as missing, L2 as 0 points). extra_parts: the median over the judges whose two passes agree, rounded up.

    Returns {judges, undecided_judges, panel_ignored, l1, l2, undecided_items, unstable, scores, extra_parts, extra_parts_undecided, levels};
    l1 maps inventory id to present or missing, l2 to ok, off or wrong (items without a decided vote are absent); levels are
    L1 (30), L2 (20, panel part), L3 (20), L4 (10, panel part), all None when no judge is usable.
    """
    inventory_ids = list(inventory_ids)
    ids = set(inventory_ids)
    valid, ignored = [], []
    for i, d in enumerate(responses):
        problems = validate_judge(d, ids)
        if problems:
            ignored.append({'source': names[i] if names else f'#{i}', 'errors': problems})
        else:
            valid.append(d)
    pairs = sorted({d['pair_id'] for d in valid})
    if len(pairs) > 1:
        raise InputError(f'PANEL_MIXED: the folder holds responses for {len(pairs)} different pair_id values {pairs}; '
                         'give one candidate per folder')
    by_judge = {}
    for d in valid:
        by_judge.setdefault(d['judge_id'], []).append(d)
    undecided = sorted(j for j, ps in by_judge.items() if sorted(p['pass'] for p in ps) != [1, 2])
    usable = {j: ps for j, ps in by_judge.items() if j not in undecided}
    votes = {'L1': {}, 'L2': {}}
    unstable, extras = {}, []
    scores = {k: [] for k in SCORE_MAX}
    for judge, passes in sorted(usable.items()):
        for key in sorted(set().union(*(set(p['items']) for p in passes))):
            vals = [p['items'][key] for p in passes if key in p['items']]
            if len(vals) < 2 or vals[0] != vals[1]:
                unstable.setdefault(judge, []).append(key)
            else:
                level, ident = _item_key(key, ids)
                votes[level].setdefault(ident, []).append(vals[0])
        for k in SCORE_MAX:
            scores[k].append(sum(p['scores'][k] for p in passes) / 2.0)
        if passes[0]['extra_parts'] == passes[1]['extra_parts']:
            extras.append(passes[0]['extra_parts'])
        else:
            unstable.setdefault(judge, []).append('extra_parts')
    decided = {lv: {k: _majority(v) for k, v in votes[lv].items()} for lv in votes}
    l1 = {k: v for k, v in decided['L1'].items() if v is not None}
    l2 = {k: v for k, v in decided['L2'].items() if v is not None}
    undecided_items = sorted([f'{lv}:{k}' for lv in decided for k, v in decided[lv].items() if v is None])
    median = {k: (float(statistics.median(v)) if v else None) for k, v in scores.items()}
    extra = int(math.ceil(statistics.median(extras))) if extras else None
    levels = {'L1': None, 'L2': None, 'L3': None, 'L4': None}
    if usable:
        levels = panel_level_scores(l1, l2, median['L3'], median['L4'], len(inventory_ids), extra)
    return {'judges': sorted(usable), 'undecided_judges': undecided, 'panel_ignored': ignored, 'l1': l1, 'l2': l2,
            'undecided_items': undecided_items, 'unstable': unstable, 'scores': median, 'extra_parts': extra,
            'extra_parts_undecided': bool(usable) and extra is None, 'levels': levels}


def read_panel_folder(folder):
    """Parsed JSON of every *.json file in the folder (unreadable files come back as None) and their names."""
    docs, names = [], []
    for f in sorted(Path(folder).rglob('*.json')):
        try:
            docs.append(json.loads(f.read_text(encoding='utf-8')))
        except (OSError, ValueError):
            docs.append(None)
        names.append(f.name)
    return docs, names


def _bad_tally(path, why):
    raise InputError(f'BAD_TALLY: {path}: {why}')


def read_tally(path, candidate_id, inventory_ids):
    """The levels and items of one candidate from a tally.v1 file (02 section 4.10), checked strictly; nothing is clipped."""
    doc = read_json(path)
    inv = set(inventory_ids)
    if not isinstance(doc, dict):
        _bad_tally(path, 'expected an object')
    for k in sorted(set(doc) ^ {'schema_version', 'candidates', 'judges', 'pairwise_agreement'}):
        _bad_tally(path, f'top-level field {k!r} is missing or not in the contract')
    if doc['schema_version'] != 'tally.v1':
        _bad_tally(path, 'schema_version must be tally.v1')
    judges, pairs = doc['judges'], doc['pairwise_agreement']
    if not (isinstance(judges, dict) and all(isinstance(v, dict) and set(v) == {'self_consistency'} and _num(v['self_consistency'])
                                              and 0 <= v['self_consistency'] <= 1 for v in judges.values())):
        _bad_tally(path, 'judges must map judge ids to {self_consistency: 0..1}')
    if not (isinstance(pairs, dict) and set(pairs) == {'j1-j2', 'j1-j3', 'j2-j3'}
            and all(_num(v) and 0 <= v <= 1 for v in pairs.values())):
        _bad_tally(path, 'pairwise_agreement must hold j1-j2, j1-j3 and j2-j3 as numbers from 0 to 1')
    cands = doc['candidates']
    if not isinstance(cands, dict):
        _bad_tally(path, 'candidates must be an object')
    for cid, entry in cands.items():
        where = f'candidates.{cid}'
        if not (isinstance(entry, dict) and set(entry) == {'levels', 'items', 'extra_parts'}):
            _bad_tally(path, f'{where} needs exactly levels, items and extra_parts')
        lv = entry['levels']
        if not (isinstance(lv, dict) and set(lv) == {'L1', 'L2_panel', 'L3', 'L4_panel'}):
            _bad_tally(path, f'{where}.levels needs exactly L1, L2_panel, L3 and L4_panel')
        for name, top in (('L1', 30), ('L2_panel', 20), ('L3', 20), ('L4_panel', 10)):
            if lv[name] is not None and not (_num(lv[name]) and 0 <= lv[name] <= top):
                _bad_tally(path, f'{where}.levels.{name} must be a number from 0 to {top} (or null)')
        if not isinstance(entry['items'], dict):
            _bad_tally(path, f'{where}.items must be an object')
        for k, v in entry['items'].items():
            parsed = _item_key(k, inv)
            allowed = None if parsed is None else (INV_VALUES if parsed[0] == 'L1' else EL_VALUES) + ('undecided',)
            if allowed is None or not (isinstance(v, str) and v in allowed):
                _bad_tally(path, f'{where}.items.{k}: key must be L1:<inventory id> or L2:<inventory id> with a valid value or undecided')
        ep = entry['extra_parts']
        if ep is not None and not (isinstance(ep, int) and not isinstance(ep, bool) and ep >= 0):
            _bad_tally(path, f'{where}.extra_parts must be an integer >= 0 or null')
    entry = cands.get(candidate_id)
    if entry is None:
        raise InputError(f'TALLY_CANDIDATE_MISSING: no entry for {candidate_id!r} in {path}; known: {sorted(cands)}')
    lv = entry['levels']
    levels = {k: (None if lv[n] is None else float(lv[n])) for k, n in (('L1', 'L1'), ('L2', 'L2_panel'), ('L3', 'L3'), ('L4', 'L4_panel'))}
    l1 = {k[3:]: v for k, v in entry['items'].items() if k.startswith('L1:') and v in INV_VALUES}
    l2 = {k[3:]: v for k, v in entry['items'].items() if k.startswith('L2:') and v in EL_VALUES}
    return {'source': 'tally', 'levels': levels, 'l1': l1, 'l2': l2, 'extra_parts': entry['extra_parts'],
            'judges': sorted(judges), 'undecided_judges': [], 'panel_ignored': [], 'unstable': {},
            'extra_parts_undecided': entry['extra_parts'] is None}


# ---- sheets --------------------------------------------------------------------------------------------------------

def _element_boxes(spec, width, height):
    boxes = {}
    for c in spec.get('components', []):
        lo, hi = metrics.points_bounds(metrics.component_points(c))
        px = []
        for x in (lo[0], hi[0]):
            for y in (lo[1], hi[1]):
                for z in (lo[2], hi[2]):
                    try:
                        px.append(metrics.camera_math.project([x, y, z], spec['camera'], width, height)[:2])
                    except ValueError:
                        pass
        if px:
            boxes[c['id']] = (min(p[0] for p in px), min(p[1] for p in px), max(p[0] for p in px), max(p[1] for p in px))
    return boxes


def _views(photo, render):
    flat = Image.new('RGBA', photo.shape[1::-1], (128, 128, 128, 255))
    flat.alpha_composite(Image.fromarray(render, 'RGBA'))
    src = Image.fromarray(photo, 'RGB')
    return src, flat.convert('RGB'), Image.blend(src, flat.convert('RGB'), 0.5)


def make_panel_view(photo, render):
    """Original | render | 50 % overlay side by side, without any text, id, score, box or point: what judges may see."""
    src, flat, blend = _views(photo, render)
    w, h = src.size
    canvas = Image.new('RGB', (3 * w, h))
    for n, im in enumerate((src, flat, blend)):
        canvas.paste(im, (n * w, 0))
    return canvas


def make_sheet(photo, render, spec, candidate, total_text, elements, holdout_rows):
    """The four-cell sheet of 03 section 7, for the decision maker (titles, element boxes and held-out points)."""
    h, w = photo.shape[:2]
    bar = 18
    src, flat, blend = _views(photo, render)
    marks = src.copy()
    d = ImageDraw.Draw(marks)
    for ident, (x0, y0, x1, y1) in _element_boxes(spec, w, h).items():
        colour = EL_COLOUR[elements.get(ident)]
        d.rectangle([x0, y0, x1, y1], outline=colour, width=2)
        d.text((x0 + 3, y0 + 2), ident, fill=colour)
    for r in holdout_rows:
        tx, ty = r['target_px']
        d.ellipse([tx - 4, ty - 4, tx + 4, ty + 4], outline=(0, 255, 0))
        if r['projected_px']:
            rx, ry = r['projected_px']
            d.line([(tx, ty), (rx, ry)], fill=(255, 0, 0))
    canvas = Image.new('RGB', (2 * w, 2 * (h + bar)), (0, 0, 0))
    cells = (('original', src, (0, 0)), ('render', flat, (w, 0)), ('overlay', blend, (0, h + bar)), ('elements', marks, (w, h + bar)))
    cd = ImageDraw.Draw(canvas)
    for label, im, (x, y) in cells:
        cd.text((x + 4, y + 3), f'{candidate} | {label} | {total_text}', fill=(255, 255, 255))
        canvas.paste(im, (x, y + bar))
    return canvas


# ---- score ---------------------------------------------------------------------------------------------------------

def reader_validator():
    """Draft 2020-12 validator for reader.v1; the $ref to booth-design.v1 is resolved from the schema folder."""
    import jsonschema
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012

    def retrieve(uri):
        return Resource(contents=json.loads((HERE / uri).resolve().read_text(encoding='utf-8')), specification=DRAFT202012)

    schema = json.loads((HERE / 'reader_schema.json').read_text(encoding='utf-8'))
    return jsonschema.Draft202012Validator(schema, registry=Registry(retrieve=retrieve))


def _round(v, n=4):
    return None if v is None else round(float(v), n)


def read_map(path, inventory_ids):
    """{inventory_id: name or [names]}. The file must be well formed and every key an inventory id (else the map is invalid).

    Names are not checked against the spec: a variant may have deleted that part on purpose, and the point then counts as
    missing (missing_reason mapped_name_absent).
    """
    raw = read_json(path)
    if not isinstance(raw, dict):
        raise InputError('BAD_MAP: expected an object {inventory_id: component id or group, or a list of them}')
    out = {}
    for part, value in raw.items():
        if part not in inventory_ids:
            raise InputError(f'MAP_UNKNOWN_KEY: {part!r} is not an inventory id of the answer sheet')
        names = [value] if isinstance(value, str) else value
        if not (isinstance(names, list) and names and all(isinstance(n, str) and n for n in names)):
            raise InputError(f'BAD_MAP: {part!r} must map to a name or a non-empty list of names')
        out[part] = names
    return out


def _l2_colours(spec, inv_ids, mapping, l2):
    """Verdict of the L2 majority for each spec component that belongs to an inventory item (exact match or --map)."""
    out = {}
    for i in inv_ids:
        for c in metrics.matching_components(spec, i, (mapping or {}).get(i)):
            if i in l2:
                out[c['id']] = l2[i]
    return out


def score_run(run, photo_path, truth, panel=None, out=None, candidate_id=None, map_path=None, fit_path=None):
    run, out = Path(run), Path(out)
    if not fit_path:
        raise InputError('FIT_REQUIRED: give --fit, the corner fit (box_fit.json) of this picture; the edge distance compares inside the booth region it gives')
    if not candidate_id:
        raise InputError('CANDIDATE_ID_REQUIRED: give --candidate-id; the preview folder name is not an identity')
    candidate = candidate_id
    check = validate_truth(truth)
    if not check['valid']:
        raise InputError('INVALID_TRUTH: ' + '; '.join(check['errors']))
    inventory = read_json(Path(truth) / 'inventory.json')
    holdout = read_json(Path(truth) / 'holdout_px.json')['points']
    swatches = read_json(Path(truth) / 'swatches.json')['swatches']
    for name in ('preview_render.png', 'spec.json'):
        if not (run / name).is_file():
            raise InputError(f'FILE_MISSING: {run / name}')
    spec_bytes = (run / 'spec.json').read_bytes()
    spec = json.loads(spec_bytes.decode('utf-8'))
    mapping = read_map(map_path, {i['id'] for i in inventory['items']}) if map_path else None
    try:
        photo = np.asarray(Image.open(photo_path).convert('RGB'))
        with Image.open(run / 'preview_render.png') as im:
            if im.mode != 'RGBA':
                raise InputError(f'BAD_RENDER: preview_render.png is {im.mode}, not RGBA; without an alpha channel there is no booth area')
            render = np.asarray(im)
    except OSError as e:
        raise InputError(f'BAD_IMAGE: {e}')
    if photo.shape[:2] != render.shape[:2]:
        raise InputError(f'IMAGE_SHAPE_MISMATCH: render {render.shape[1]}x{render.shape[0]} vs picture '
                         f'{photo.shape[1]}x{photo.shape[0]}; previews use the source canvas and are never rescaled')
    h, w = photo.shape[:2]
    fit = read_fit(fit_path, spec.get('camera'), w, h)
    region = metrics.fit_region(fit, w, h)
    inv_ids = [i['id'] for i in inventory['items']]

    levels = {'L1': {'panel': None}, 'L2': {'code': None, 'panel': None}, 'L3': {'panel': None}, 'L4': {'code': None, 'panel': None}}
    aux = {'edge_distance_diag': None, 'edge_distance_px': None, 'holdout_median_diag': None, 'colour_de_mean': None,
           'booth_pixel_fraction': 0.0, 'second_view_ok': None}
    detail = {'candidate_id': candidate, 'map': mapping, 'holdout': [], 'swatches': [], 'panel': None}
    status, reason = 'measured', None

    agg = None
    if panel is not None:                          # read the panel first: a mixed or unknown panel is an input error
        if Path(panel).is_file():
            agg = read_tally(panel, candidate, inv_ids)
        elif Path(panel).is_dir():
            docs, names = read_panel_folder(panel)
            agg = aggregate_panel(docs, inv_ids, names)
        else:
            raise InputError(f'FILE_MISSING: {panel}')
        detail['panel'] = agg

    if not metrics.booth_mask(render).any():
        status, reason = 'blocked', 'render_empty'
    else:
        edge = metrics.edge_distance(photo, render, region)
        rows = metrics.holdout_errors(spec, holdout, w, h, mapping)
        median = metrics.holdout_median(rows)
        col = metrics.colour_de(photo, render, swatches)
        levels['L2']['code'] = _round(metrics.holdout_points(median), 2)
        levels['L4']['code'] = _round(col['points'], 2)
        aux.update(edge_distance_diag=_round(edge['edge_distance_diag'], 6), edge_distance_px=_round(edge['edge_distance_px'], 3),
                   holdout_median_diag=_round(median, 6), colour_de_mean=_round(col['colour_de_mean'], 3),
                   booth_pixel_fraction=_round(edge['booth_pixel_fraction'], 4))
        detail.update(holdout=rows, swatches=col['swatches'], edge=dict(edge))
        if panel is None:
            status, reason = 'blocked', 'panel_missing'
        elif not agg['judges'] and agg.get('source') != 'tally':
            status, reason = 'blocked', 'panel_invalid'
        else:
            lv = agg['levels']
            levels['L1']['panel'] = _round(lv['L1'], 2)
            levels['L2']['panel'] = _round(lv['L2'], 2)
            levels['L3']['panel'] = _round(lv['L3'], 2)
            levels['L4']['panel'] = _round(lv['L4'], 2)
            if None in lv.values():
                status, reason = 'blocked', 'panel_invalid'
    parts = [levels['L1']['panel'], levels['L2']['code'], levels['L2']['panel'], levels['L3']['panel'], levels['L4']['code'],
             levels['L4']['panel']]
    total = round(sum(p for p in parts if p is not None), 2)
    score = {'schema_version': 'score.v1', 'picture_id': inventory['picture_id'], 'candidate_id': candidate,
             'spec_sha256': hashlib.sha256(spec_bytes).hexdigest(), 'levels': levels, 'total': total, 'aux': aux,
             'status': status, 'reason': reason}
    elements = _l2_colours(spec, inv_ids, mapping, (detail['panel'] or {}).get('l2', {}))
    text = f'total {total:g}' + ('' if status == 'measured' else f' ({reason})')
    # Build every output in memory first, so that a failure leaves nothing behind; score.json is written last.
    sheet = make_sheet(photo, render, spec, candidate, text, elements, detail['holdout'])
    view = make_panel_view(photo, render)
    written = []
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        for path, write in ((out.parent / 'sheet.png', sheet.save), (out.parent / 'panel_view.png', view.save),
                            (out.parent / 'score_detail.json', lambda p: write_json(p, detail)), (out, lambda p: write_json(p, score))):
            written.append(path)
            write(path)
    except Exception:
        for path in written:
            path.unlink(missing_ok=True)
        raise
    return score


def _ignored_options(args):
    """A message when an option would be silently ignored in this mode, else None."""
    if args.validate_truth:
        extra = [f'--{n.replace(chr(95), chr(45))}' for n in ('run', 'photo', 'truth', 'panel', 'out', 'candidate_id', 'fit') if getattr(args, n)]
        if extra:
            return f'{", ".join(extra)} would be ignored with --validate-truth; use it on its own, with --camera and --map'
        if args.map and not args.camera:
            return '--map would be ignored without --camera; --validate-truth takes --map only together with --camera'
    elif args.camera:
        return '--camera would be ignored without --validate-truth; it belongs to the drafting check'
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--run', help='preview folder (preview_render.png, spec.json)')
    ap.add_argument('--photo', help='the original picture')
    ap.add_argument('--truth', help='answer-sheet folder (inventory.json, holdout_px.json, swatches.json)')
    ap.add_argument('--panel', help='folder of judge.v1 responses for this candidate, or a tally.v1 file; without it the status is blocked')
    ap.add_argument('--out', help='score.json to write; sheet.png, panel_view.png and score_detail.json go next to it')
    ap.add_argument('--candidate-id', help='required: the candidate identity for score.json and the tally entry (preview folder names are not identities)')
    ap.add_argument('--map', help='JSON {inventory_id: component id or group, or a list}: which spec components make up a part')
    ap.add_argument('--fit', help='required: the corner fit (box_fit.json, or truth_fit.json) of this picture; its box projected with its camera is the '
                    'booth region the edge distance is computed in; exit 2 when it is missing, has another camera than the spec or another picture size')
    ap.add_argument('--validate-truth', metavar='TRUTH_DIR', help='check an answer sheet and exit (0 valid, 2 invalid)')
    ap.add_argument('--camera', metavar='SPEC_JSON', help='with --validate-truth: a spec.json with the fitted camera and the components; '
                    'echoes the note of each point and reports the corner its name picks, its pixel, the distance to uv_px and whether the name has '
                    'margin (same corner for an orbit of +-5 degrees about the vertical axis through the target and an elevation change '
                    'of +-2, varied separately), then lists every valid name of both families with the same four facts; exit 2 when a '
                    'name has no margin or cannot be placed; --map says which components make up a part')
    for stream in (sys.stdout, sys.stderr):                       # the report echoes notes in any language: never fail on cp1252
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    args = ap.parse_args(argv)
    try:
        ignored = _ignored_options(args)
        if ignored:
            print('ERROR: ' + ignored, file=sys.stderr)
            return 2
        if args.validate_truth:
            result = (draft_report(args.validate_truth, args.camera, args.map) if args.camera
                      else validate_truth(args.validate_truth))
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0 if result['valid'] else 2
        if not args.validate_truth and not args.fit:
            print('ERROR: FIT_REQUIRED: give --fit, the corner fit (box_fit.json) of this picture; the booth region of the edge distance comes from it', file=sys.stderr)
            return 2
        missing = [f'--{n.replace(chr(95), chr(45))}' for n in ('run', 'photo', 'truth', 'out', 'candidate_id') if not getattr(args, n)]
        if missing:
            ap.error('required: ' + ', '.join(missing) + ' (or --validate-truth)')
        score = score_run(args.run, args.photo, args.truth, args.panel, args.out, args.candidate_id, args.map, args.fit)
        print(json.dumps({'out': str(args.out), 'status': score['status'], 'reason': score['reason'], 'total': score['total']}))
        return 0
    except (InputError, ValueError, KeyError, TypeError, IndexError, ZeroDivisionError, OverflowError) as e:
        print(f'ERROR: {type(e).__name__}: {e}' if not isinstance(e, InputError) else 'ERROR: ' + str(e), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
