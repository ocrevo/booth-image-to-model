"""Synthetic self-play bench (P6, docs/poc/03-scoring-spec.md section 6): scenes with an exactly known truth, the distance of a
candidate spec from that truth, and the round-by-round curve.

    python tools/poc/loop/selfplay.py prepare     --out DIR --cases 3 --seed 1
    python tools/poc/loop/selfplay.py render      --case CASE_DIR --out DIR            (starts Blender; one process at a time)
    python tools/poc/loop/selfplay.py truth-sheet --case CASE_DIR [--render DIR] --out CASE_DIR/truth
    python tools/poc/loop/selfplay.py perturb     --case CASE_DIR --seed 1 --out SPEC.json [--render DIR | --spec TRUTH_SPEC.json]
    python tools/poc/loop/selfplay.py mask-spec   --candidate SPEC.json --out MASK_SPEC.json
    python tools/poc/loop/selfplay.py evaluate    --case CASE_DIR --candidate SPEC.json [--masks DIR] [--render DIR]
                                                  [--map MAP.json] [--match nearest] --out RESULT.json
    python tools/poc/loop/selfplay.py curve       --case CASE_DIR --rounds DIR --out CURVE.json         (also writes CURVE.png)

The harness in tools/eval and the skill's compare.py are only imported or started, never changed. Errors are in metres. A part the
candidate does not contain is "missing", never an error of 0. Standard library, numpy, Pillow and scipy.

How a true part is matched to the candidate's components (placement error and IoU use the same match):
  --map FILE     {true part id: component id or group name, or a list of them}; exact names only.
  (default)      components whose id or group is the part id, or whose id starts with "<part id>_" (a candidate that kept the true ids).
  --match nearest  one-to-one best match (Hungarian) of true parts to candidate groups by distance between footprint centres; a pair
                 further apart than 1.0 m is a missing part. A candidate "part" is the components sharing a `group`, or one component
                 without a group; only components of the scope 'furniture' are candidates (not walls, floor, frame, screens, lights,
                 graphic panels). Parts named in --map keep their mapping and their components are not offered to the others.
The candidate mask of a part is the union of the part_masks/<component id>.png of its components (what a full build writes).
"""
from __future__ import annotations
import argparse
import copy
import importlib.util
import json
import math
import random
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
EVAL_DIR = REPO / 'tools' / 'eval'
SKILL_DIR = REPO / 'booth-image-to-model'
POC_DIR = REPO / 'tools' / 'poc'
SUITE = 'smoke'
MOVE_RANGE_M = (0.1, 0.5)
REGRESSION_TOLERANCE = 0.10          # a round may be at most 10 % worse than the one before
MIN_ROUNDS = 4
NEAREST_MAX_M = 1.0                  # --match nearest: a pair further apart than this is a missing part
# --match nearest offers only furniture to the true parts. The design compiler (motifs.SCOPE, design_compile.build) writes the scopes
# booth_floor, shell, furniture, media, lighting and graphics; everything except 'furniture' (floor, walls, header, ring, partitions,
# screens, LED walls, lights, lightboxes, graphic panels) is never a candidate, whatever else a scene names its scopes.
CANDIDATE_SCOPES = ('furniture',)
SCHEMA_EVAL = 'selfplay-eval.v1'
SCHEMA_CURVE = 'selfplay-curve.v1'
TRUTH_SCHEMA = 'truth.v1'
CORNERS = ['top_front_left', 'top_front_right', 'top_back_left', 'top_back_right',
           'bottom_front_left', 'bottom_front_right', 'bottom_back_left', 'bottom_back_right']
HOLDOUT_MIN_DEPTH_GAP_M = 0.25      # deepest minus shallowest of the four points
HOLDOUT_POOL_LIMIT = 24
SOLID_CORNER_M = 0.03               # a bounding-box corner counts as on the object when a mesh vertex is this close
SWATCH_ROLES = ('dominant', 'secondary', 'accent')
ID_RE = re.compile(r'^[a-z][a-z0-9_]*$')


# ------------------------------------------------------------------------------------------------ loading the harness
def _load(name, path, extra_paths=()):
    """Import a module from a file under a private name, so that it cannot clash with a module of the same name elsewhere."""
    if name in sys.modules:
        return sys.modules[name]
    for p in extra_paths:
        if str(p) not in sys.path:
            sys.path.append(str(p))
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _eval_metrics():
    return _load('poc_eval_metrics', EVAL_DIR / 'metrics.py')


def _synth_generate():
    return _load('poc_synth_generate', EVAL_DIR / 'synth_generate.py')


def _synth_render():
    return _load('poc_synth_render', EVAL_DIR / 'synth_render.py')


def _compare():
    return _load('poc_skill_compare', SKILL_DIR / 'scripts' / 'compare.py', extra_paths=[SKILL_DIR / 'scripts'])


def _camera_math():
    return _load('poc_skill_camera_math', SKILL_DIR / 'scripts' / 'camera_math.py')


def _score_metrics():
    """tools/poc/score/metrics.py (P1): the part geometry and corner words of the answer sheet. Imported only when it is needed."""
    if str(POC_DIR) not in sys.path:
        sys.path.insert(0, str(POC_DIR))
    from score import metrics
    return metrics


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes((json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode('utf-8'))


def _find(case_dir, render_dir, rel):
    """First existing file among: the render folder, <case>/render, the case folder itself."""
    places = ([Path(render_dir)] if render_dir else []) + [Path(case_dir) / 'render', Path(case_dir)]
    for base in places:
        if (base / rel).is_file():
            return base / rel
    raise FileNotFoundError(f'{rel} not found in any of: ' + ', '.join(str(b) for b in places))


# ------------------------------------------------------------------------------------------------ prepare and render
def prepare(out_dir, cases=3, seed=1):
    """Write `cases` scenes of the smoke suite (case.json and scene.json each) under out_dir. -> the suite manifest."""
    return _synth_generate().write_suite(SUITE, seed, out_dir, cases)


def render(case_dir, out_dir, skill_dir=None, blender=None, samples=16, clutter=False, post=None, seed=0, overlay=False):
    """Render the picture, the instance mask, the truth spec and truth_fit.json of one case (Blender, via synth_render.py)."""
    return _synth_render().render_case(case_dir, out_dir, skill_dir or SKILL_DIR, blender=blender, samples=samples, clutter=clutter,
                                       post=post or {}, seed=seed, overlay=overlay)


# ------------------------------------------------------------------------------------------------ which components are a part
def _owns(part_id, component_id):
    return component_id == part_id or component_id.startswith(part_id + '_')


def _names(value):
    return {value} if isinstance(value, str) else set(value)


def load_map(path):
    """--map file -> {true part id: [names]}; a name is a component id or a group name."""
    raw = read_json(path)
    if not isinstance(raw, dict) or not all(isinstance(v, (str, list)) for v in raw.values()):
        raise ValueError('MAP: expected {part id: component id or group name, or a list of them}')
    out = {}
    for pid, v in raw.items():
        names = [v] if isinstance(v, str) else list(v)
        if not names or not all(isinstance(n, str) and n for n in names):
            raise ValueError(f'MAP: {pid!r} needs one or more names')
        out[pid] = names
    return out


def footprint_centre(components):
    """Footprint centre (x, y) of a set of components (the same rule as tools/eval/metrics.py part_centres), or None."""
    spec = {'components': [dict(c, id=f'g_{i}') for i, c in enumerate(components)]}
    return _eval_metrics().part_centres(spec, ['g']).get('g')


def assign_parts(case, spec, mapping=None, match=None):
    """Which candidate components make up each true part.

    -> {part id: {'components': [component ids], 'how': 'map'|'exact'|'nearest'|None, 'distance_m': float|None}}.
    `mapping` is load_map output; `match` is None or 'nearest' (see the module docstring)."""
    if match not in (None, 'exact', 'nearest'):
        raise ValueError(f'unknown --match {match!r}; use nearest')
    comps = [c for c in spec.get('components', []) if isinstance(c, dict) and isinstance(c.get('id'), str)]
    unknown = sorted(k for k in (mapping or {}) if k not in {p['id'] for p in case.get('parts') or []})
    if unknown:
        raise ValueError('MAP: not a true part id: ' + ', '.join(unknown))
    out = {}
    for p in case.get('parts') or []:
        out[p['id']] = {'components': [], 'how': None, 'distance_m': None}
    used = set()
    for pid in out:
        if mapping and pid in mapping:
            names = _names(mapping[pid])
            out[pid].update(components=[c['id'] for c in comps if c['id'] in names or c.get('group') in names], how='map')
            used.update(out[pid]['components'])
    open_parts = [p for p in case.get('parts') or [] if out[p['id']]['how'] is None]
    if match == 'nearest':
        groups = {}
        for c in comps:
            if c['id'] in used or c.get('scope_id') not in CANDIDATE_SCOPES:
                continue
            groups.setdefault(c.get('group') or c['id'], []).append(c)
        centres = {k: footprint_centre(v) for k, v in groups.items()}
        keys = [k for k in sorted(groups) if centres[k] is not None]
        if open_parts and keys:
            from scipy.optimize import linear_sum_assignment
            big = 1e6
            dist = [[math.hypot(centres[k][0] - p['center_m'][0], centres[k][1] - p['center_m'][1]) for k in keys] for p in open_parts]
            cost = [[d if d <= NEAREST_MAX_M else big for d in row] for row in dist]
            for i, j in zip(*linear_sum_assignment(cost)):
                if cost[i][j] < big:
                    out[open_parts[i]['id']].update(components=[c['id'] for c in groups[keys[j]]], how='nearest', distance_m=dist[i][j])
    else:
        for p in open_parts:
            pid = p['id']
            found = [c['id'] for c in comps if c['id'] == pid or c.get('group') == pid or _owns(pid, c['id'])]
            if found:
                out[pid].update(components=found, how='exact')
    return out


# ------------------------------------------------------------------------------------------------ placement error
def placement_report(case, spec, assignment=None):
    """Distance in metres between each candidate part's footprint centre and its true floor position.

    -> {'median_m', 'max_m', 'share_w', 'missing', 'n_parts', 'per_part': {id: {'status': 'ok'|'missing', 'error_m': float|None,
    'matched': [component ids], 'how', 'match_distance_m'}}}. A part with no matching component (or no usable footprint) is
    'missing' with error None, never 0. The aggregate is tools/eval/metrics.py placement_errors on the matched components."""
    m = _eval_metrics()
    assignment = assignment if assignment is not None else assign_parts(case, spec)
    parts = case.get('parts') or []
    by_id = {c['id']: c for c in spec.get('components', []) if isinstance(c, dict) and 'id' in c}
    alias = {p['id']: f'p{k}' for k, p in enumerate(parts)}                      # 'p1_' is never a prefix of 'p10_'
    rel_case = dict(case, parts=[dict(p, id=alias[p['id']]) for p in parts])
    rel_spec = {'components': [dict(by_id[cid], id=f"{alias[pid]}_{i}") for pid, a in assignment.items() if pid in alias
                               for i, cid in enumerate(a['components']) if cid in by_id]}
    centres = m.part_centres(rel_spec, list(alias.values()))
    per_part = {}
    for p in parts:
        a, c = assignment.get(p['id'], {'components': [], 'how': None, 'distance_m': None}), centres.get(alias[p['id']])
        row = {'matched': list(a['components']), 'how': a['how'], 'match_distance_m': a['distance_m']}
        if c is None:
            row.update(status='missing', error_m=None)
        else:
            row.update(status='ok', error_m=math.hypot(c[0] - p['center_m'][0], c[1] - p['center_m'][1]))
        per_part[p['id']] = row
    agg = m.placement_errors(rel_case, rel_spec) or {'median_m': None, 'max_m': None, 'share_w': None, 'missing': len(parts)}
    return {'median_m': agg['median_m'], 'max_m': agg['max_m'], 'share_w': agg['share_w'], 'missing': agg['missing'],
            'n_parts': len(parts), 'per_part': per_part}


# ------------------------------------------------------------------------------------------------ mask IoU
def _mask_ids(render_dir):
    import numpy as np
    from PIL import Image
    rgb = np.asarray(Image.open(Path(render_dir) / 'mask.png').convert('RGB'))
    return rgb[..., 0].astype(np.uint32) + 256 * rgb[..., 1].astype(np.uint32)       # id = R + 256 * G; 0 is background


def _object_part(case, o):
    """The true part id a mask_index object belongs to, or None (walls, floor, frame)."""
    if 'part_id' in o:
        return o['part_id']
    if o.get('kind') == 'component':
        for p in case.get('parts') or []:
            if _owns(p['id'], str(o.get('component_id'))):
                return p['id']
    return None


def truth_part_masks(case, render_dir):
    """{part id: bool array} from mask.png (id = R + 256 * G) and mask_index.json of a render folder."""
    import numpy as np
    ids = _mask_ids(render_dir)
    objects = read_json(Path(render_dir) / 'mask_index.json')['objects']
    out = {}
    for p in case.get('parts') or []:
        wanted = [int(o['id']) for o in objects if _object_part(case, o) == p['id']]
        out[p['id']] = np.isin(ids, wanted) if wanted else np.zeros(ids.shape, bool)
    return out


def iou_report(case, render_dir, masks_dir, assignment):
    """Per-part IoU of the candidate's part masks against the truth masks of the render folder.

    The candidate mask of a part is the union of <masks_dir>/<component id>.png over the components `assignment` gives it.
    -> {'mean', 'n_scored', 'per_part': {id: {'status', 'iou', 'components_with_mask', 'components_without_mask', ...}}}.
    Statuses: ok; missing_part (no component matched; counts as 0 in the mean); missing_candidate_mask (components matched but none
    has a mask file; counts as 0); no_truth_pixels (the part is not visible in the picture; left out of the mean).
    A canvas mismatch raises ValueError."""
    import numpy as np
    compare = _compare()
    truth = truth_part_masks(case, render_dir)
    per_part, scores = {}, []
    for p in case.get('parts') or []:
        pid = p['id']
        ref = truth[pid]
        comps = list((assignment.get(pid) or {}).get('components') or [])
        row = {'components_with_mask': [], 'components_without_mask': [], 'iou': None}
        per_part[pid] = row
        if not ref.any():
            row['status'] = 'no_truth_pixels'
            continue
        union = np.zeros(ref.shape, bool)
        for cid in comps:
            path = Path(masks_dir) / f'{cid}.png'
            if not path.is_file():
                row['components_without_mask'].append(cid)
                continue
            mask = compare.load_mask(path)
            if mask.shape != ref.shape:
                raise ValueError(f'IMAGE_SHAPE_MISMATCH: {path.name} {mask.shape} vs truth {ref.shape}')
            union |= mask
            row['components_with_mask'].append(cid)
        if not comps or not row['components_with_mask']:
            row['status'] = 'missing_part' if not comps else 'missing_candidate_mask'
            scores.append(0.0)
            continue
        r = compare.compare_masks(ref, union)
        row.update(status='ok', iou=r['iou'], boundary_distance_diag=r['symmetric_boundary_distance_diag'],
                   reference_pixels=r['reference_pixels'], candidate_pixels=r['candidate_pixels'])
        scores.append(r['iou'])
    incomplete = any(r['components_without_mask'] or r.get('status') == 'missing_candidate_mask' for r in per_part.values())
    return {'mean': sum(scores) / len(scores) if scores else None, 'n_scored': len(scores), 'masks_incomplete': bool(incomplete),
            'per_part': per_part}


def check_masks_folder(masks_dir, spec):
    """MASKS_MISSING (ValueError) when the folder does not exist or holds no mask of any component of the candidate; a folder that
    lacks only some masks is accepted (the report marks it masks_incomplete)."""
    folder = Path(masks_dir)
    if not folder.is_dir():
        raise ValueError(f'MASKS_MISSING: {masks_dir} is not a folder')
    wanted = {c['id'] for c in spec.get('components', []) if isinstance(c, dict) and 'id' in c}
    if not any(p.stem in wanted for p in folder.glob('*.png')):
        raise ValueError(f'MASKS_MISSING: {masks_dir} holds no <component id>.png of the candidate (was the build run with --part-masks '
                         'on the mask-spec copy?)')


def evaluate(case_dir, candidate, masks_dir=None, render_dir=None, mapping=None, match=None):
    """How far a candidate spec is from the truth of a case: placement error in metres and, with masks, per-part IoU.

    `mapping` is load_map output, `match` None or 'nearest'; see the module docstring for how true parts meet candidate components."""
    case = read_json(Path(case_dir) / 'case.json')
    spec = read_json(candidate)
    assignment = assign_parts(case, spec, mapping, match)
    result = {'schema_version': SCHEMA_EVAL, 'case_id': case['case_id'], 'candidate': str(candidate),
              'match': match or ('map' if mapping else 'exact'),
              'placement': placement_report(case, spec, assignment), 'iou': None}
    result['masks_incomplete'] = False
    if masks_dir is not None:
        check_masks_folder(masks_dir, spec)                              # before anything is written: an empty folder is not an IoU of 0
        render_root = _find(case_dir, render_dir, 'mask.png').parent
        result['iou'] = iou_report(case, render_root, masks_dir, assignment)
        result['masks_incomplete'] = result['iou']['masks_incomplete']
    return result


# ------------------------------------------------------------------------------------------------ mask build spec
def mask_spec(spec):
    """A copy of the spec with every component marked critical, only to get one mask per component out of a full build
    (blender_build writes part_masks for critical components only). Nothing else changes."""
    out = copy.deepcopy(spec)
    for c in out.get('components', []):
        c['critical'] = True
    return out


# ------------------------------------------------------------------------------------------------ truth sheet
def _components_of(spec, key, is_part):
    """The spec components that make up one truth entity: a part (id, group or '<id>_' prefix) or one shell component."""
    if is_part:
        return [c for c in spec.get('components', []) if _owns(key, c['id']) or c.get('group') == key]
    return [c for c in spec.get('components', []) if c['id'] == key]


def _part_bounds(spec, pid):
    s = _score_metrics()
    pts = []
    for c in _components_of(spec, pid, True):
        pts.extend(s.component_points(c))
    return s.points_bounds(pts) if pts else None


def _hint(component_id, part_id=None):
    stem = component_id[len(part_id) + 1:] if part_id and component_id.startswith(part_id + '_') else component_id
    stem = re.sub(r'(_?\d+)+$', '', stem) or stem
    return 'wall' if stem.startswith('wall') else stem


CORNER_ORDER = CORNERS + ['_'.join(w) for w in (('top', 'left'), ('top', 'right'), ('top', 'front'), ('top', 'back'),
                                                    ('bottom', 'left'), ('bottom', 'right'), ('bottom', 'front'), ('bottom', 'back'))]


def _usable_name(metrics, world, bounds, camera, width, height):
    """The name an automatic held-out corner gets: a three-word name when exactly one reaches the corner and it keeps its corner when
    the camera orbits 5 degrees about the vertical axis through its target and, separately, changes its elevation by 2, else a one-horizontal-word name (top_left, bottom_front, ...) that does. None when no
    name has margin (03 section 2 and section 6 step 3). A name is skipped when its top/bottom twin reaches the same point too
    (a box without height)."""
    reach = metrics.names_reaching(world, bounds, camera, width, height)
    twin = {'top': 'bottom', 'bottom': 'top'}
    both = set(reach['three_word']) | set(reach['one_horizontal'])
    candidates = list(reach['three_word']) if len(reach['three_word']) == 1 else []
    candidates += reach['one_horizontal']
    for name in candidates:
        first, rest = name.split('_', 1)
        if f'{twin[first]}_{rest}' in both:
            continue
        if metrics.name_margin(name, bounds, camera, width, height):
            return name
    return None


def _holdout_pool(entities, spec, camera, width, height):
    """Every usable held-out corner, in priority order: furniture parts (largest first), then shell objects (largest first).

    A corner is usable when it is inside the picture (2 percent margin), when a vertex of the entity's real mesh lies within
    SOLID_CORNER_M of it (a bounding box corner of a cylinder or a round table is in the air, not on the object), and when mask
    pixels of the entity are within 4 px of its projection (it is not hidden). Its name must keep its corner when the camera orbits
    5 degrees about the vertical axis through its target or changes its elevation by 2, separately (03 section 6 step 3; three words or one horizontal word, whichever family has margin), and a
    left (right) corner lies left (right) of the box centre in the picture."""
    cm, s = _camera_math(), _score_metrics()
    mx, my = 0.02 * width, 0.02 * height
    per_entity = []
    for rank, e in enumerate(entities):
        pts = [pt for c in _components_of(spec, e['id'], e['is_part']) for pt in s.component_points(c)]
        if not pts or not e['mask'].any():
            continue
        bounds = s.points_bounds(pts)
        rows = []
        for world in s.box_corners(bounds):
            if min(math.dist(world, q) for q in pts) > SOLID_CORNER_M:
                continue
            try:
                corner = _usable_name(s, world, bounds, camera, width, height)       # as a person looking at the picture reads it
            except ValueError:
                continue
            if corner is None:
                continue
            try:
                u, v, depth = cm.project(world, camera, width, height)
            except ValueError:
                continue
            if not (mx <= u <= width - mx and my <= v <= height - my):
                continue
            try:
                cu = cm.project([(bounds[0][i] + bounds[1][i]) / 2.0 for i in range(3)], camera, width, height)[0]
            except ValueError:
                continue
            if ('left' in corner and u > cu) or ('right' in corner and u < cu):       # a person reads left and right in the picture
                continue
            x0, y0 = int(round(u)), int(round(v))
            near = int(e['mask'][max(0, y0 - 4):y0 + 5, max(0, x0 - 4):x0 + 5].sum())
            if near > 0:
                rows.append({'id': e['id'], 'is_part': e['is_part'], 'corner': corner, 'uv': (u, v), 'depth': depth, 'near': near,
                             'rank': rank})
        rows.sort(key=lambda r: (-r['near'], CORNER_ORDER.index(r['corner'])))
        per_entity.append(rows)
    pool = []                                                       # best corner of every entity first, then the second ones...
    for k in range(len(CORNERS)):
        for rows in per_entity:
            if k < len(rows):
                pool.append(dict(rows[k], order=len(pool)))
    return pool


def _choose_holdout(pool):
    """4 points from the pool. Preferred, in this order: as many different entities as possible (4), then as few shell objects as
    possible, then the earliest in priority order. The four must not sit within 10 px of each other and their depths must not all be
    the same: the deepest and the shallowest differ by at least HOLDOUT_MIN_DEPTH_GAP_M. None when no four qualify."""
    import itertools
    best = None
    for combo in itertools.combinations(pool[:HOLDOUT_POOL_LIMIT], 4):
        if any(a['id'] == b['id'] and a['corner'] == b['corner'] for a, b in itertools.combinations(combo, 2)):
            continue
        if max(r['depth'] for r in combo) - min(r['depth'] for r in combo) < HOLDOUT_MIN_DEPTH_GAP_M:
            continue
        if any(math.hypot(a['uv'][0] - b['uv'][0], a['uv'][1] - b['uv'][1]) < 10 for a, b in itertools.combinations(combo, 2)):
            continue
        key = (-len({r['id'] for r in combo}), sum(1 for r in combo if not r['is_part']), sum(r['order'] for r in combo))
        if best is None or key < best[0]:
            best = (key, combo)
    return list(best[1]) if best else None


def truth_sheet(case_dir, out_dir, render_dir=None):
    """Write inventory.json, holdout_px.json and swatches.json (docs/poc/03-scoring-spec.md section 2) from the truth of a rendered case.

    holdout    4 corners of bounding boxes, projected with the camera of truth_fit.json, on four different true parts when there are
               enough of them, otherwise with corners of shell objects (floor, walls, frame) filling up, and a second corner of a part
               only when nothing else gives four. Corners must be on the solid (not the air corner of a cylinder), visible in the mask,
               and the four depths must not all be equal (deepest minus shallowest at least 0.25 m). Every part named here is in the inventory.
    inventory  the true parts, the shell objects seen in the picture and the objects named by a held-out point, the largest 15 by
               area (named objects are never dropped); when that is fewer than 8 the largest visible pieces of parts are added (a valid
               sheet needs 8). hero = the 2 to 3 largest parts.
    swatches   3 boxes inside the largest visible part masks (the centre of the largest inscribed disc); if there are fewer than 3
               parts, the largest shell objects fill up."""
    import numpy as np
    from scipy.ndimage import distance_transform_edt
    case = read_json(Path(case_dir) / 'case.json')
    render_root = _find(case_dir, render_dir, 'mask.png').parent
    spec = read_json(_find(case_dir, render_dir, 'spec/spec.json'))
    camera = read_json(_find(case_dir, render_dir, 'truth_fit.json'))['camera']
    ids = _mask_ids(render_root)
    height, width = ids.shape
    objects = read_json(render_root / 'mask_index.json')['objects']
    counts = np.bincount(ids.ravel(), minlength=65536)
    parts = {p['id']: p for p in case.get('parts') or []}
    area, objs_of = {}, {}
    for o in objects:
        pid = _object_part(case, o)
        o['_pid'] = pid
        o['_area'] = int(counts[int(o['id'])])
        if pid is not None:
            area[pid] = area.get(pid, 0) + o['_area']
            objs_of.setdefault(pid, []).append(o)
    shell = [o for o in objects if o['_pid'] is None and o.get('kind') == 'component' and o['_area'] > 0 and ID_RE.match(str(o['component_id']))]
    part_mask = {pid: np.isin(ids, [int(o['id']) for o in objs_of.get(pid, [])]) for pid in parts}
    entities = [{'id': pid, 'is_part': True, 'area': area.get(pid, 0), 'mask': part_mask[pid]}
                for pid in sorted(parts, key=lambda q: (-area.get(q, 0), q))]
    entities += [{'id': o['component_id'], 'is_part': False, 'area': o['_area'], 'mask': ids == int(o['id'])}
                 for o in sorted(shell, key=lambda o: (-o['_area'], o['component_id']))]

    # holdout points: priority order is furniture first, then shell
    picked = _choose_holdout(_holdout_pool(entities, spec, camera, width, height))
    if picked is None:
        raise ValueError('TRUTH_SHEET: no four usable held-out corners (on the solid, visible, in the picture, not all at one depth, with a '
                         'name of either family that keeps its corner when the camera orbits 5 degrees about the vertical axis through its target '
                         'and, separately, changes its elevation by 2 degrees)')
    picked.sort(key=lambda r: r['order'])
    pic = case['case_id']
    holdout = {'schema_version': TRUTH_SCHEMA, 'picture_id': pic,
               'points': [{'id': f'h{n + 1}', 'part_id': r['id'], 'corner': r['corner'], 'uv_px': [round(r['uv'][0], 1), round(r['uv'][1], 1)],
                           'note': f"{r['corner'].replace('_', ' ')} corner of the bounding box of {r['id']}"}
                          for n, r in enumerate(picked)]}
    required = {r['id'] for r in picked}

    # inventory
    items = [{'id': pid, 'name': f"{p.get('type', 'part')} {pid}", 'kind_hint': p.get('type', 'part'), 'hero': False, '_area': area.get(pid, 0)}
             for pid, p in parts.items()]
    items += [{'id': o['component_id'], 'name': f"booth shell {o['component_id']}", 'kind_hint': _hint(o['component_id']), 'hero': False,
               '_area': o['_area']} for o in shell]
    items.sort(key=lambda it: (-it['_area'], it['id']))
    items = items[:15]
    for rid in sorted(required - {it['id'] for it in items}):               # a held-out point's object must be in the inventory
        while len(items) >= 15:
            drop = next(it for it in reversed(items) if it['id'] not in required)
            items.remove(drop)
        src = next(o for o in shell if o['component_id'] == rid)
        items.append({'id': rid, 'name': f'booth shell {rid}', 'kind_hint': _hint(rid), 'hero': False, '_area': src['_area']})
    items.sort(key=lambda it: (-it['_area'], it['id']))
    if len(items) < 8:
        pieces = sorted((o for o in objects if o['_pid'] is not None and o['_area'] > 0 and ID_RE.match(str(o['component_id']))
                         and o['component_id'] not in parts),
                        key=lambda o: (-o['_area'], o['component_id']))
        for o in pieces[:8 - len(items)]:
            items.append({'id': o['component_id'], 'name': f"{_hint(o['component_id'], o['_pid'])} piece of {o['_pid']}",
                          'kind_hint': _hint(o['component_id'], o['_pid']), 'hero': False, '_area': o['_area']})
        items.sort(key=lambda it: (-it['_area'], it['id']))
    if len(items) < 8:
        raise ValueError(f'TRUTH_SHEET: only {len(items)} inventory items can be made from this case; a valid sheet needs 8')
    part_items = [it for it in items if it['id'] in parts]
    pool = part_items if len(part_items) >= 2 else items
    for it in pool[:3]:
        it['hero'] = True
    inventory = {'schema_version': TRUTH_SCHEMA, 'picture_id': pic,
                 'items': [{k: v for k, v in it.items() if not k.startswith('_')} for it in items]}

    # swatches: the centre of the largest inscribed disc of the largest visible masks
    order = [e for e in entities if e['is_part']] + [e for e in entities if not e['is_part']]
    boxes = []
    for e in order:
        if len(boxes) == len(SWATCH_ROLES):
            break
        mask = e['mask']
        if not mask.any():
            continue
        dist = distance_transform_edt(np.pad(mask, 1))[1:-1, 1:-1]
        cy, cx = np.unravel_index(int(np.argmax(dist)), dist.shape)
        half = int(math.floor((dist[cy, cx] - 1) / math.sqrt(2)))
        if half < 2:
            continue
        boxes.append([int(cx - half), int(cy - half), int(cx + half), int(cy + half)])
    if len(boxes) < 3:
        raise ValueError(f'TRUTH_SHEET: only {len(boxes)} masks are large enough for a colour swatch; a valid sheet needs 3')
    swatches = {'schema_version': TRUTH_SCHEMA, 'picture_id': pic,
                'swatches': [{'id': f's_{role}', 'bbox_px': box, 'role': role} for role, box in zip(SWATCH_ROLES, boxes)]}
    out = Path(out_dir)
    write_json(out / 'inventory.json', inventory)
    write_json(out / 'holdout_px.json', holdout)
    write_json(out / 'swatches.json', swatches)
    return {'out': str(out), 'items': len(inventory['items']), 'heroes': [it['id'] for it in inventory['items'] if it['hero']],
            'holdout_parts': [p['part_id'] for p in holdout['points']], 'swatches': len(boxes)}


# ------------------------------------------------------------------------------------------------ perturbation
def _rgba(rng):
    return [round(rng.random(), 3) for _ in range(3)] + [1.0]


def perturb_spec(case, spec, seed):
    """Truth spec -> (perturbed copy, record). Every movable part moves 0.1 to 0.5 m in a random direction, one part (when there are
    at least two) is deleted and one other part gets a random colour. Walls and the frame are never touched."""
    rng = random.Random(seed)
    out = copy.deepcopy(spec)
    comps = out['components']
    parts = [p['id'] for p in case.get('parts') or [] if any(_owns(p['id'], c['id']) for c in comps)]
    record = {'seed': seed, 'deleted': None, 'moved': {}, 'recoloured': {}, 'not_in_spec': sorted(
        p['id'] for p in case.get('parts') or [] if p['id'] not in parts)}
    if len(parts) >= 2:
        record['deleted'] = rng.choice(parts)
        out['components'] = comps = [c for c in comps if not _owns(record['deleted'], c['id'])]
    for pid in [p for p in parts if p != record['deleted']]:
        distance, angle = rng.uniform(*MOVE_RANGE_M), rng.uniform(0.0, 2 * math.pi)
        dx, dy = distance * math.cos(angle), distance * math.sin(angle)
        for c in comps:
            if _owns(pid, c['id']):
                c['location'][0] += dx
                c['location'][1] += dy
        record['moved'][pid] = {'dx_m': dx, 'dy_m': dy, 'distance_m': distance}
    left = [p for p in parts if p != record['deleted']]
    if left:
        pid = rng.choice(left)
        old = next(c['material']['color'] for c in comps if _owns(pid, c['id']))
        new = _rgba(rng)
        while new[:3] == list(old)[:3]:
            new = _rgba(rng)
        for c in comps:
            if _owns(pid, c['id']):
                c['material']['color'] = list(new)
        record['recoloured'][pid] = {'from': list(old), 'to': new}
    return out, record


def perturb(case_dir, seed, out_path, render_dir=None, spec_path=None):
    """Write a perturbed copy of the truth spec to out_path and perturbation.json next to it. -> the record."""
    case = read_json(Path(case_dir) / 'case.json')
    truth = Path(spec_path) if spec_path else _find(case_dir, render_dir, 'spec/spec.json')
    out, record = perturb_spec(case, read_json(truth), seed)
    record['truth_spec'] = str(truth)
    write_json(out_path, out)
    write_json(Path(out_path).parent / 'perturbation.json', record)
    return record


# ------------------------------------------------------------------------------------------------ curve
def _error_value(r):
    v = (r.get('placement') or {}).get('median_m')
    return math.inf if v is None else float(v)


def _iou_value(r):
    """Mean IoU of a round, or None when it was not measured. A round with incomplete masks is not measured: its mean would count
    the absent masks as 0 and read as a step back."""
    iou = r.get('iou')
    if not iou or iou.get('mean') is None or r.get('masks_incomplete') or iou.get('masks_incomplete'):
        return None
    return float(iou['mean'])


def _median(values):
    values = sorted(values)
    n = len(values)
    return values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2


def _paired_error(ra, rb):
    """(median error of round a, median error of round b) over the parts that BOTH rounds matched, in metres; None when the rounds
    share no matched part. Rounds that carry no per-part errors (hand-made results) fall back to their own medians."""
    pa, pb = (ra.get('placement') or {}).get('per_part'), (rb.get('placement') or {}).get('per_part')
    if pa is None or pb is None:
        return _error_value(ra), _error_value(rb)
    common = [k for k in pa if k in pb and pa[k].get('error_m') is not None and pb[k].get('error_m') is not None]
    if not common:
        return None
    return _median([pa[k]['error_m'] for k in common]), _median([pb[k]['error_m'] for k in common])


def judge_curve(results):
    """Verdict over the round results. `results` is {round number: evaluate result}, or a list that is numbered 1, 2, 3...

    Rounds are known by their number, not by their position, and none may be skipped: if any round from 1 to the last one (at least
    4) is absent the verdict is undecided and the reason names it. improved is True only when the placement error AND the mean IoU both
    are better in round 4 than in round 1 and no round is more than 10 % worse than the round before it in either. The placement
    error of a comparison (each step, and round 4 against round 1) is the median over the parts that both rounds matched, so that
    finding a part the earlier round missed (usually with a large error) is not a step back; the number of missing parts is compared
    apart and may not grow. False when a check fails; None (undecided, with the reason) when a round is absent or the IoU was not
    measured in every round and nothing else failed."""
    by_round = {i + 1: r for i, r in enumerate(results)} if isinstance(results, (list, tuple)) else {int(k): v for k, v in results.items()}
    numbers = sorted(by_round)
    miss = {k: int((by_round[k].get('placement') or {}).get('missing') or 0) for k in numbers}
    iou = {k: _iou_value(by_round[k]) for k in numbers}
    checks = {'rounds': numbers, 'round4_better_than_round1': {'placement': None, 'iou': None},
              'no_regression': {'placement': None, 'iou': None}}
    last = max(numbers + [MIN_ROUNDS])
    absent = [k for k in range(1, last + 1) if k not in by_round]
    if absent:
        return {'improved': None, 'reason': 'round ' + ', '.join(str(k) for k in absent) + ' missing, cannot judge (rounds are not skipped)',
                'checks': checks}
    tol = 1.0 + REGRESSION_TOLERANCE
    steps = list(zip(numbers, numbers[1:]))

    def not_worse(a, b):
        pair = _paired_error(by_round[a], by_round[b])
        return miss[b] <= miss[a] and (pair is None or pair[1] <= pair[0] * tol + 1e-9)

    pair = _paired_error(by_round[1], by_round[MIN_ROUNDS])
    checks['round4_better_than_round1']['placement'] = bool(pair is not None and pair[1] < pair[0] and miss[MIN_ROUNDS] <= miss[1])
    checks['no_regression']['placement'] = all(not_worse(a, b) for a, b in steps)
    placement_ok = checks['round4_better_than_round1']['placement'] and checks['no_regression']['placement']
    if all(iou[k] is not None for k in numbers):
        checks['round4_better_than_round1']['iou'] = bool(iou[MIN_ROUNDS] > iou[1])
        checks['no_regression']['iou'] = all(iou[b] >= iou[a] * (1.0 - REGRESSION_TOLERANCE) - 1e-9 for a, b in steps)
        iou_ok = checks['round4_better_than_round1']['iou'] and checks['no_regression']['iou']
    else:
        iou_ok = None
    if not placement_ok or iou_ok is False:
        return {'improved': False, 'reason': 'a check failed', 'checks': checks}
    if iou_ok is None:
        return {'improved': None, 'reason': 'the IoU was not measured in every round (no --masks, or masks_incomplete)', 'checks': checks}
    return {'improved': True, 'reason': 'both metrics improved', 'checks': checks}


_ROUND_NAME = re.compile(r'^round[_-]?(\d+)(\.json)?$', re.I)


def load_rounds(rounds_dir):
    """{round number: evaluate result} from <dir>/round_N/evaluate.json (the loop's layout) and flat <dir>/round_N.json files."""
    found = {}
    for path in sorted(Path(rounds_dir).iterdir()):
        m = _ROUND_NAME.match(path.name)
        if not m:
            continue
        n = int(m.group(1))
        if path.is_dir() and (path / 'evaluate.json').is_file():
            found[n] = read_json(path / 'evaluate.json')
        elif path.is_file() and path.suffix.lower() == '.json' and n not in found:
            found[n] = read_json(path)
    if not found:
        raise FileNotFoundError(f'no round_N/evaluate.json or round_N.json in {rounds_dir}')
    return dict(sorted(found.items()))


def draw_curve(rows, verdict, path):
    """A plain two-panel chart (placement error, mean IoU) per round, drawn with Pillow."""
    from PIL import Image, ImageDraw
    W, H, pad = 900, 380, 48
    img = Image.new('RGB', (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    panels = [('Placement error, median (m)', 'placement_median_m', (200, 60, 40)), ('Mean IoU of parts', 'iou_mean', (40, 110, 200))]
    pw = (W - 3 * pad) // 2
    for i, (title, key, colour) in enumerate(panels):
        x0, y0, x1, y1 = pad + i * (pw + pad), 60, pad + i * (pw + pad) + pw, H - pad
        d.rectangle([x0, y0, x1, y1], outline=(120, 120, 120))
        d.text((x0, y0 - 18), title, fill=(0, 0, 0))
        pts = [(r['round'], r[key]) for r in rows if r[key] is not None]
        if not pts:
            d.text((x0 + 8, y0 + 8), 'not measured', fill=(120, 120, 120))
            continue
        lo, hi = min(v for _, v in pts), max(v for _, v in pts)
        if key == 'iou_mean':
            lo, hi = 0.0, max(1.0, hi)
        elif hi - lo < 1e-9:
            lo, hi = 0.0, max(hi, 1e-6)
        rn = [r['round'] for r in rows]
        xs = (lambda n: x0 + 12 + (x1 - x0 - 24) * ((n - min(rn)) / max(1, max(rn) - min(rn))))
        ys = (lambda v: y1 - 12 - (y1 - y0 - 24) * ((v - lo) / (hi - lo)))
        xy = [(xs(n), ys(v)) for n, v in pts]
        if len(xy) > 1:
            d.line(xy, fill=colour, width=2)
        for (px, py), (n, v) in zip(xy, pts):
            d.ellipse([px - 4, py - 4, px + 4, py + 4], fill=colour)
            d.text((px - 6, y1 + 4), str(n), fill=(0, 0, 0))
            d.text((px + 6, py - 14), f'{v:.2f}', fill=(60, 60, 60))
    label = {True: 'improved', False: 'not improved', None: 'undecided'}[verdict['improved']]
    d.text((pad, 14), f"{verdict.get('case_id', '')}  rounds: {len(rows)}  verdict: {label}  ({verdict['reason']})", fill=(0, 0, 0))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


def make_curve(case_dir, rounds_dir, out_path):
    """Read the per-round evaluate results, judge them, write curve.json and curve.png (next to it). -> the curve dict."""
    case = read_json(Path(case_dir) / 'case.json')
    rounds = load_rounds(rounds_dir)
    rows = []
    for n, r in rounds.items():
        pl = r.get('placement') or {}
        rows.append({'round': n, 'placement_median_m': pl.get('median_m'), 'placement_max_m': pl.get('max_m'),
                     'missing': pl.get('missing'), 'iou_mean': _iou_value(r),
                     'masks_incomplete': bool(r.get('masks_incomplete') or (r.get('iou') or {}).get('masks_incomplete'))})
    verdict = judge_curve(rounds)
    curve = {'schema_version': SCHEMA_CURVE, 'case_id': case['case_id'], 'rounds': rows, **verdict}
    write_json(out_path, curve)
    draw_curve(rows, dict(verdict, case_id=case['case_id']), Path(out_path).with_suffix('.png'))
    return curve


# ------------------------------------------------------------------------------------------------ command line
def build_parser():
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    sub = p.add_subparsers(dest='command', required=True)
    a = sub.add_parser('prepare', help='Write smoke-suite cases (case.json and scene.json) with an exactly known truth')
    a.add_argument('--out', required=True)
    a.add_argument('--cases', type=int, default=3)
    a.add_argument('--seed', type=int, default=1)
    a = sub.add_parser('render', help='Render picture, instance mask and truth spec of one case (starts Blender)')
    a.add_argument('--case', required=True)
    a.add_argument('--out', required=True)
    a.add_argument('--skill-dir')
    a.add_argument('--blender')
    a.add_argument('--samples', type=int, default=16)
    a.add_argument('--seed', type=int, default=0)
    a = sub.add_parser('truth-sheet', help='Write the answer sheet (inventory, held-out points, swatches) from the truth of a rendered case')
    a.add_argument('--case', required=True)
    a.add_argument('--render', help='Render folder (default: <case>/render, then the case folder)')
    a.add_argument('--out', required=True, help='Usually <case>/truth')
    a = sub.add_parser('perturb', help='Truth spec plus random moves, one deletion and one colour change')
    a.add_argument('--case', required=True)
    a.add_argument('--seed', type=int, default=1)
    a.add_argument('--out', required=True)
    a.add_argument('--render', help='Render folder holding spec/spec.json (default: <case>/render, then the case folder)')
    a.add_argument('--spec', help='The truth spec itself, instead of looking for it')
    a = sub.add_parser('mask-spec', help='Copy of a spec with every component critical, for the build that writes part_masks')
    a.add_argument('--candidate', required=True)
    a.add_argument('--out', required=True)
    a = sub.add_parser('evaluate', help='Distance of a candidate spec from the truth: metres per part and, with masks, IoU per part')
    a.add_argument('--case', required=True)
    a.add_argument('--candidate', required=True)
    a.add_argument('--masks', help='The part_masks folder of a build: one <component id>.png per component (white = component)')
    a.add_argument('--render', help='Render folder holding mask.png and mask_index.json (default: <case>/render, then the case folder)')
    a.add_argument('--map', help='JSON {true part id: component id or group name, or a list of them}')
    a.add_argument('--match', choices=['nearest'], help='Match true parts to candidate groups one to one by distance (max 1.0 m)')
    a.add_argument('--out', required=True)
    a = sub.add_parser('curve', help='Judge the rounds (<dir>/round_N/evaluate.json or round_N.json) and draw curve.png')
    a.add_argument('--case', required=True)
    a.add_argument('--rounds', required=True, help='Usually <case>/loop')
    a.add_argument('--out', required=True)
    return p


def main(argv=None):
    a = build_parser().parse_args(argv)
    try:
        if a.command == 'prepare':
            result = prepare(a.out, a.cases, a.seed)
            print(json.dumps({'suite': SUITE, 'seed': a.seed, 'cases': [c['case_id'] for c in result['cases']], 'out': str(a.out)}))
        elif a.command == 'render':
            print(json.dumps(render(a.case, a.out, a.skill_dir, a.blender, a.samples, seed=a.seed)))
        elif a.command == 'truth-sheet':
            print(json.dumps(truth_sheet(a.case, a.out, a.render)))
        elif a.command == 'perturb':
            record = perturb(a.case, a.seed, a.out, a.render, a.spec)
            print(json.dumps({'out': str(a.out), 'deleted': record['deleted'], 'moved': sorted(record['moved'])}))
        elif a.command == 'mask-spec':
            write_json(a.out, mask_spec(read_json(a.candidate)))
            print(json.dumps({'out': str(a.out)}))
        elif a.command == 'evaluate':
            result = evaluate(a.case, a.candidate, a.masks, a.render, load_map(a.map) if a.map else None, a.match)
            write_json(a.out, result)
            print(json.dumps({'out': str(a.out), 'median_m': result['placement']['median_m'], 'missing': result['placement']['missing'],
                              'iou_mean': (result['iou'] or {}).get('mean')}))
        else:
            curve = make_curve(a.case, a.rounds, a.out)
            print(json.dumps({'out': str(a.out), 'improved': curve['improved'], 'reason': curve['reason']}))
    except (ValueError, OSError, RuntimeError, KeyError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
