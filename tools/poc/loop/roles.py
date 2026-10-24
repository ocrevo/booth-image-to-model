"""The three model roles as files (docs/poc/02-architecture-and-contracts.md sections 2, 4 and 6): write a request with only what
the role may see, wait for its answer, check the answer against the contract.

    path = write_request('critic', round_dir, payload, {'el_hero_wall/original.png': png_bytes, ...})
    raw = wait_response(round_dir / 'critic.response.json', timeout_s, newer_than=path, remaining_s=left)
    result = validate('critic', raw, payload)          # Result(value, status, reasons); value is the answer or 'undecided'

Request names and layout (P8a): `round_N/<name>.request.json` plus `round_N/<name>_inputs/`, the answer in
`round_N/<name>.response.json`. Names: reader, critic, judge_p1, judge_p2 (the tie-break), judge_best_p1, judge_best_p2 (the
round winner against the best so far). Every judge request is one pass answered by one judge.v1 object.

What each role may see (its diet), relative to its inputs folder:
  reader   picture.png (or .jpg), catalog/catalog.json, catalog/booth-design.v1.json, catalog/designs/<kind>.json, catalog/<kind>.png
  critic   el_<element_id>/original.png, render.png and overlay.png (cropped, never the whole picture; overlay.png is the picture's
           crop with the outline of the render drawn on it; the el_ prefix keeps ids such as aux or con, which Windows reserves,
           usable as folder names)
  judge    original.png, A.png, B.png
The request fields are a closed list per role, with checked values: the reader's box_fit holds only the sizes (dims_m W, D, H and
the picture's width and height), a critic element only its id, its family (a catalogue kind), its three crops, the pixel box of the crop in the picture, the crop's
top left corner in the picture and the factor it was enlarged by, a
judge request only ids, the pass, a short question and its three pictures. Every `<name>_inputs/...` path in a request must name a
file that is given. write_request reads every input first and writes nothing when anything is wrong (RoleError) or a source
cannot be read (OSError); a write that fails half way is cleaned up, so the same request can be written again.

An answer is "ok" only when it is plain JSON, matches the contract schema (reader.v1 from tools/poc/score/reader_schema.json,
critic.v1 and judge.v1 in tie-break mode below), names the same picture, round, candidate, pair, pass and judge as its request,
and keeps the rules that close free text:
  reader  text.body only placeholder X and spaces; brief.text that or READER_BRIEF; no notes, note, mood, industry, reference or
          requirements; uncertainties only HEIGHT_NOTE; ids and names at most 40 characters; unexpressible what and why at most 80
          characters and nearest_kind a catalogue kind or null; evidence for exactly the element ids, with boxes inside the
          picture; and the skill's design checks (design_compile.validate_design: booth type against open sides, walls on closed
          sides, text from the brief ...) on the design with the fitted footprint.
  critic  finding ids f1, f2 ... without repeats; element_id null or an element of the request (null for camera_off); the
          direction that goes with the type; boxes with 0 <= x0 < x1 and 0 <= y0 < y1; accept without findings, fix with some.
  judge   tie-break mode: scores, items and extra_parts all null.
Otherwise the value is UNDECIDED with the reasons, and the status says why: no_response (no file in time) or invalid.
"""
from __future__ import annotations

import copy
import json
import math
import re
import shutil
import sys
import time
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SCORE_DIR = HERE.parent / 'score'
SCHEMA_PATH = ROOT / 'booth-image-to-model' / 'schemas' / 'booth-design.v1.json'
SKILL_SCRIPTS = ROOT / 'booth-image-to-model' / 'scripts'

UNDECIDED = 'undecided'
REQUEST_NAMES = ('reader', 'critic', 'judge_p1', 'judge_p2', 'judge_best_p1', 'judge_best_p2')
FINDING_TYPES = ('missing', 'extra', 'misplaced', 'wrong_size', 'wrong_colour', 'wrong_kind', 'wrong_count', 'camera_off')
MOVES = ('left', 'right', 'up', 'down', 'forward', 'back')
DIRECTIONS = MOVES + ('bigger', 'smaller', 'darker', 'lighter')
SEVERITIES = ('high', 'medium', 'low')
DIRECTION_BY_TYPE = {'misplaced': MOVES, 'wrong_size': ('bigger', 'smaller'), 'wrong_colour': ('darker', 'lighter'),
                     'wrong_count': ('bigger', 'smaller'), 'wrong_kind': (None,), 'extra': (None,), 'missing': (None,),
                     'camera_off': (None,)}

ID_MAX = 40                          # element ids, params.role, project_id: generic part names, never words from the picture
NAME_MAX = 64                        # picture, candidate and pair ids in a request
QUESTION_MAX = 200
FREE_TEXT_MAX = 80                   # unexpressible: what and why
READER_BRIEF = 'Read from the picture.'
HEIGHT_NOTE = {'id': 'height_estimated', 'description': 'The height limit is estimated from the picture.'}
PLACEHOLDER = re.compile(r'[X ]*X[X ]*')
FINDING_ID = re.compile(r'f[0-9]+')
_ID = r'[a-z][a-z0-9_]*'
ID_RE = re.compile(_ID)
NAME_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]*')

DIET = {
    'reader': [re.compile(p) for p in (r'picture\.(png|jpe?g)', r'catalog/catalog\.json', r'catalog/booth-design\.v1\.json',
                                       rf'catalog/designs/{_ID}\.json', rf'catalog/{_ID}\.png')],
    'critic': [re.compile(rf'el_{_ID}/(original|render|overlay)\.png')],
    'judge': [re.compile(r'(original|A|B)\.png')],
}
PAYLOAD = {     # role: (required fields, allowed fields)
    'reader': ({'role', 'picture_id', 'picture', 'box_fit', 'response'}, {'role', 'picture_id', 'picture', 'box_fit', 'catalog', 'response'}),
    'critic': ({'role', 'round', 'candidate_id', 'elements', 'response'}, {'role', 'round', 'candidate_id', 'elements', 'response'}),
    'judge': ({'role', 'round', 'pair_id', 'pass', 'judge_id', 'original', 'A', 'B', 'response'},
              {'role', 'round', 'pair_id', 'pass', 'judge_id', 'question', 'original', 'A', 'B', 'response'}),
}
BOX_FIT_FIELDS = {'dims_m', 'image'}                 # the reader gets the sizes of the fit, not its camera
CRITIC_ELEMENT_FIELDS = {'element_id', 'family', 'original_crop', 'render_crop', 'overlay_crop', 'bbox_px', 'crop_origin_px', 'crop_scale'}
CROP_SCALE_MAX = 8

_FOUR_NUMBERS = {'type': 'array', 'items': {'type': 'number'}, 'minItems': 4, 'maxItems': 4}
CRITIC_SCHEMA = {
    '$schema': 'https://json-schema.org/draft/2020-12/schema', 'title': 'critic.v1 (02 section 4.2)',
    'type': 'object', 'additionalProperties': False, 'required': ['schema_version', 'round', 'candidate_id', 'findings', 'verdict'],
    'properties': {
        'schema_version': {'const': 'critic.v1'},
        'round': {'type': 'integer', 'minimum': 1},
        'candidate_id': {'type': 'string', 'minLength': 1},
        'findings': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False,
            'required': ['id', 'element_id', 'type', 'severity', 'direction', 'bbox_px'],
            'properties': {
                'id': {'type': 'string', 'minLength': 1},
                'element_id': {'type': ['string', 'null']},
                'type': {'enum': list(FINDING_TYPES)},
                'severity': {'enum': list(SEVERITIES)},
                'direction': {'enum': list(DIRECTIONS) + [None]},
                'bbox_px': {'anyOf': [{'type': 'null'}, _FOUR_NUMBERS]}}}},
        'verdict': {'enum': ['accept', 'fix', 'give_up']}}}
JUDGE_SCHEMA = {
    '$schema': 'https://json-schema.org/draft/2020-12/schema', 'title': 'judge.v1 in tie-break mode (02 section 4.4)',
    'type': 'object', 'additionalProperties': False,
    'required': ['schema_version', 'pair_id', 'judge_id', 'pass', 'better', 'scores', 'items', 'extra_parts', 'confidence'],
    'properties': {
        'schema_version': {'const': 'judge.v1'},
        'pair_id': {'type': 'string', 'minLength': 1},
        'judge_id': {'enum': ['j1', 'j2', 'j3']},
        'pass': {'type': 'integer', 'minimum': 1, 'maximum': 2},
        'better': {'enum': ['A', 'B', 'same']},
        'scores': {'type': 'null'},
        'items': {'type': 'null'},
        'extra_parts': {'type': 'null'},
        'confidence': {'enum': ['low', 'medium', 'high']}}}


class RoleError(ValueError):
    """A request that would show a role more than its diet, or an unknown role."""


class Unreadable:
    """A response file that exists but is not JSON (as opposed to None: no response at all)."""

    def __init__(self, reason):
        self.reason = reason


class Result(NamedTuple):
    value: object                    # the checked answer, or UNDECIDED
    status: str                      # ok, no_response, invalid
    reasons: tuple

    @property
    def ok(self):
        return self.status == 'ok'


@lru_cache(maxsize=1)
def kinds():
    """The element kinds of booth-design.v1: the family catalogue."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
    return tuple(schema['$defs']['element']['properties']['kind']['enum'])


def base_role(name):
    """reader, critic or judge for a request name (judge_p1, judge_best_p2 ... are judge requests)."""
    if name in ('reader', 'critic'):
        return name
    if name == 'judge' or name in REQUEST_NAMES:
        return 'judge'
    raise RoleError(f'unknown role {name!r}')


def crop_folder(element_id):
    """The folder of an element's cropped pair under critic_inputs/ (the id itself may be a name Windows reserves, such as aux)."""
    return f'el_{element_id}'


def request_path(round_dir, name):
    return Path(round_dir) / f'{name}.request.json'


def response_path(round_dir, name):
    return Path(round_dir) / f'{name}.response.json'


def inputs_dir(round_dir, name):
    return Path(round_dir) / f'{name}_inputs'


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _positive(v):
    return _is_num(v) and v > 0


def box_ok(box, width=None, height=None):
    """[x0, y0, x1, y1] of finite numbers with 0 <= x0 < x1 (<= width) and 0 <= y0 < y1 (<= height)."""
    if not (isinstance(box, list) and len(box) == 4 and all(_is_num(v) for v in box)):
        return False
    x0, y0, x1, y1 = box
    if not (0 <= x0 < x1 and 0 <= y0 < y1):
        return False
    return (width is None or x1 <= width) and (height is None or y1 <= height)


def name_ok(v, pattern=NAME_RE, limit=NAME_MAX):
    return isinstance(v, str) and len(v) <= limit and bool(pattern.fullmatch(v))


# -- writing a request

def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)


def _value_problems(role, name, payload):
    """The values of the allowed request fields: types, ranges, lengths and the exact input paths."""
    out, prefix = [], f'{name}_inputs/'
    if role == 'reader':
        if not name_ok(payload.get('picture_id')):
            out.append(f'reader: picture_id must be a short id (at most {NAME_MAX} characters)')
        if not (isinstance(payload.get('picture'), str) and re.fullmatch(re.escape(prefix) + r'picture\.(png|jpe?g)', payload['picture'])):
            out.append(f'reader: picture must be {prefix}picture.png (or .jpg)')
        box = payload.get('box_fit')
        if not (isinstance(box, dict) and set(box) == BOX_FIT_FIELDS):
            out.append('reader: box_fit must hold exactly dims_m and image')
        else:
            dims, img = box['dims_m'], box['image']
            if not (isinstance(dims, dict) and {'W', 'D'} <= set(dims) <= {'W', 'D', 'H'} and all(_positive(v) for v in dims.values())):
                out.append('reader: box_fit.dims_m must hold positive numbers W and D (and H), nothing else')
            if not (isinstance(img, dict) and set(img) == {'width', 'height'} and all(_is_int(v) and v > 0 for v in img.values())):
                out.append('reader: box_fit.image must hold whole numbers width and height above 0, nothing else')
        if payload.get('catalog') not in (None, prefix + 'catalog/catalog.json'):
            out.append(f'reader: catalog must be {prefix}catalog/catalog.json or null')
        return out
    if not (_is_int(payload.get('round')) and payload['round'] >= 1):
        out.append(f'{name}: round must be a whole number from 1')
    if role == 'critic':
        if not name_ok(payload.get('candidate_id')):
            out.append(f'critic: candidate_id must be a short id (at most {NAME_MAX} characters)')
        els = payload.get('elements')
        if not isinstance(els, list):
            return out + ['critic: elements must be a list']
        seen = set()
        for i, e in enumerate(els):
            where = f'critic: elements[{i}]'
            if not (isinstance(e, dict) and set(e) == CRITIC_ELEMENT_FIELDS):
                out.append(f'{where} must hold exactly {sorted(CRITIC_ELEMENT_FIELDS)}')
                continue
            eid = e['element_id']
            if not name_ok(eid, ID_RE, ID_MAX) or eid in seen:
                out.append(f'{where}.element_id must be a design id (at most {ID_MAX} characters) that is not repeated')
                continue
            seen.add(eid)
            if not (isinstance(e['family'], str) and e['family'] in kinds()):
                out.append(f'{where}.family must be a kind of the catalogue')
            for key, which in (('original_crop', 'original'), ('render_crop', 'render'), ('overlay_crop', 'overlay')):
                if e[key] != f'{prefix}{crop_folder(eid)}/{which}.png':
                    out.append(f'{where}.{key} must be {prefix}{crop_folder(eid)}/{which}.png')
            if not box_ok(e['bbox_px']):
                out.append(f'{where}.bbox_px must be four numbers with 0 <= x0 < x1 and 0 <= y0 < y1')
            origin = e['crop_origin_px']
            if not (isinstance(origin, list) and len(origin) == 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                                                          and math.isfinite(v) and v >= 0 for v in origin)):
                out.append(f'{where}.crop_origin_px must be two numbers from 0 (top left corner of the crop in the picture)')
            if not (_is_int(e['crop_scale']) and 1 <= e['crop_scale'] <= CROP_SCALE_MAX):
                out.append(f'{where}.crop_scale must be a whole number from 1 to {CROP_SCALE_MAX}')
        return out
    if not name_ok(payload.get('pair_id')):
        out.append(f'{name}: pair_id must be a short id (at most {NAME_MAX} characters)')
    if not (_is_int(payload.get('pass')) and payload['pass'] in (1, 2)):
        out.append(f'{name}: pass must be 1 or 2')
    if payload.get('judge_id') not in ('j1', 'j2', 'j3'):
        out.append(f'{name}: judge_id must be j1, j2 or j3')
    q = payload.get('question')
    if q is not None and not (isinstance(q, str) and len(q) <= QUESTION_MAX):
        out.append(f'{name}: question must be a text of at most {QUESTION_MAX} characters')
    for key in ('original', 'A', 'B'):
        if payload.get(key) != f'{prefix}{key}.png':
            out.append(f'{name}: {key} must be {prefix}{key}.png')
    return out


def diet_problems(name, payload, inputs):
    """Why this request would show the role more than it may see (empty when it is fine)."""
    role = base_role(name)
    if not isinstance(payload, dict):
        return ['the request must be an object']
    out = []
    required, allowed = PAYLOAD[role]
    if set(payload) - allowed:
        out.append(f'{name}: fields outside the diet: {sorted(set(payload) - allowed)}')
    if required - set(payload):
        out.append(f'{name}: missing fields {sorted(required - set(payload))}')
    if payload.get('role') != role:
        out.append(f"{name}: role must be {role!r}")
    if payload.get('response') != f'{name}.response.json':
        out.append(f'{name}: response must be {name}.response.json')
    out += check(_value_problems, role, name, payload)
    names = []
    for rel in inputs:
        rel = str(rel)
        parts = rel.split('/')
        if '\\' in rel or rel.startswith('/') or any(p in ('', '.', '..') for p in parts) or not any(p.fullmatch(rel) for p in DIET[role]):
            out.append(f'{name}: {rel!r} is outside the diet of the {role}')
        names.append(rel)
    if role == 'reader' and sum('/' not in n for n in names) != 1:
        out.append('reader: exactly one picture')
    if role == 'judge' and sorted(names) != ['A.png', 'B.png', 'original.png']:
        out.append('judge: exactly original.png, A.png and B.png')
    prefix = f'{name}_inputs/'
    for s in _strings(payload):
        if s.startswith(prefix) and s[len(prefix):] not in names:
            out.append(f'{name}: the request names {s!r}, which is not given')
    return out


def write_request(role, round_dir, payload, inputs):
    """Write `<round_dir>/<role>_inputs/` (inputs: {relative path: source file path or bytes}) and then `<role>.request.json`
    (last, so that an answer is never older than its request). -> the request path.

    Every source is read before anything is written. RoleError, with nothing written, when the request or an input is outside the
    role's diet or the request was already written; OSError, with nothing left behind, when a source cannot be read or a file
    cannot be written (the inputs folder and the request file are removed again)."""
    name = role
    if name not in REQUEST_NAMES:
        raise RoleError(f'unknown request {name!r}: one of {", ".join(REQUEST_NAMES)}')
    inputs = dict(inputs or {})
    problems = diet_problems(name, payload, inputs)
    rdir = Path(round_dir)
    folder, path = inputs_dir(rdir, name), request_path(rdir, name)
    if path.exists() or (folder.exists() and any(folder.iterdir())):
        problems.append(f'{name}: the request was already written in {rdir}')
    try:
        text = (json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
    except (TypeError, ValueError) as ex:
        problems.append(f'{name}: the request is not plain JSON ({ex})')
    if problems:
        raise RoleError('; '.join(problems))
    data = {rel: bytes(src) if isinstance(src, (bytes, bytearray)) else Path(src).read_bytes() for rel, src in sorted(inputs.items())}
    try:
        folder.mkdir(parents=True, exist_ok=True)
        for rel, blob in data.items():
            dst = folder / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(blob)
        path.write_bytes(text)
    except OSError:
        shutil.rmtree(folder, ignore_errors=True)
        try:
            path.unlink()
        except OSError:
            pass
        raise
    return path


# -- waiting for an answer

def reject_constant(name):
    raise ValueError(f'{name} is not allowed in JSON here')


def wait_response(path, timeout_s, newer_than=None, remaining_s=None, sleep=time.sleep, clock=time.monotonic, poll_s=2.0):
    """Poll for a response file. -> the parsed JSON, None (nothing in time) or Unreadable (a file that is not JSON).

    A file older than `newer_than` (the request) does not count: it belongs to an earlier run. The wait is `timeout_s` at most and
    never longer than `remaining_s` (the time left of --max-minutes). A file that is not JSON is read again until it stops changing
    (it may be half written), then it is Unreadable."""
    path = Path(path)
    limit = timeout_s if remaining_s is None else min(timeout_s, max(0.0, remaining_s))
    deadline = clock() + limit
    floor = Path(newer_than).stat().st_mtime_ns if newer_than and Path(newer_than).is_file() else None
    last_bad = None
    while True:
        if path.is_file() and (floor is None or path.stat().st_mtime_ns >= floor):
            raw = path.read_bytes()
            try:
                doc = json.loads(raw.decode('utf-8-sig'), parse_constant=reject_constant)      # PowerShell 5.1 writes a BOM
                return Unreadable('the file contains null') if doc is None else doc
            except (ValueError, RecursionError) as ex:         # not JSON, not UTF-8, NaN or Infinity, or nested too deep
                if isinstance(ex, ValueError) and 'not allowed' in str(ex):
                    return Unreadable(str(ex))                  # a complete file that says NaN: no point in waiting
                if raw == last_bad:          # unchanged since the last look: not a half-written file, give up
                    return Unreadable('the file is not JSON')
                last_bad = raw
        if clock() >= deadline:
            return None if last_bad is None else Unreadable('the file is not JSON')
        sleep(poll_s)


# -- checking an answer

_validators = {}


def _validator(role):
    if role not in _validators:
        import jsonschema
        if role == 'reader':
            from referencing import Registry, Resource
            from referencing.jsonschema import DRAFT202012

            def retrieve(uri):
                return Resource(contents=json.loads((SCORE_DIR / uri).resolve().read_text(encoding='utf-8')), specification=DRAFT202012)
            schema = json.loads((SCORE_DIR / 'reader_schema.json').read_text(encoding='utf-8'))
            _validators[role] = jsonschema.Draft202012Validator(schema, registry=Registry(retrieve=retrieve))
        else:
            _validators[role] = jsonschema.Draft202012Validator(CRITIC_SCHEMA if role == 'critic' else JUDGE_SCHEMA)
    return _validators[role]


def schema_problems(role, doc):
    """The contract schema of the role (reader.v1, critic.v1, judge.v1 in tie-break mode): the first three problems, or []."""
    errors = sorted(_validator(role).iter_errors(doc), key=lambda e: list(map(str, e.absolute_path)))
    return [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in errors[:3]]


def _same(doc, request, key, out, what=None):
    want, got = request.get(key), doc.get(key) if isinstance(doc, dict) else None
    if key in ('round', 'pass'):
        ok = _is_int(got) and got == want
    else:
        ok = isinstance(got, str) and got == want
    if not ok:
        out.append(what or f'{key} does not match the request ({want!r})')


def request_problems(role, doc, request):
    """The answer must name what its request named: picture (reader), round and candidate (critic), pair, pass and judge (judge)."""
    if request is None:
        return []
    out = []
    if role == 'reader':
        _same(doc, request, 'picture_id', out)
    elif role == 'critic':
        _same(doc, request, 'round', out)
        _same(doc, request, 'candidate_id', out)
    else:
        _same(doc, request, 'pair_id', out)
        _same(doc, request, 'pass', out, f"pass must be the integer {request.get('pass')!r} as in the request")
        _same(doc, request, 'judge_id', out, f"judge_id must be {request.get('judge_id')!r} as in the request")
    return out


@lru_cache(maxsize=1)
def _design_compile():
    if str(SKILL_SCRIPTS) not in sys.path:
        sys.path.append(str(SKILL_SCRIPTS))
    import design_compile                    # the skill's compiler, imported read only for its design checks
    return design_compile


def reader_rules(doc, request=None):
    """The reader's answer beyond the schema: placeholder text only, no free-text fields, short names, evidence for exactly the
    elements inside the picture, and the skill's own design checks."""
    out = []
    d = doc['design']
    els = d['elements']
    ids = [e['id'] for e in els]
    brief = d['brief']['text']
    if brief != READER_BRIEF and not PLACEHOLDER.fullmatch(brief):
        out.append(f'design.brief.text must be {READER_BRIEF!r} or the placeholder text: only X and spaces')
    if d['brief'].get('language') not in (None, 'en'):
        out.append("design.brief.language must be 'en' or null")
    for key in ('notes', 'reference'):
        if d.get(key) is not None:
            out.append(f'design.{key} must be null or absent (no free text)')
    if d.get('requirements'):
        out.append('design.requirements must be empty, null or absent (there is no brief to split)')
    for key in ('mood', 'industry'):
        if d['style'].get(key) is not None:
            out.append(f'design.style.{key} must be null or absent (no free text)')
    for u in d.get('uncertainties') or []:
        if {k: v for k, v in u.items() if k != 'critical'} != HEIGHT_NOTE or u.get('critical') not in (None, False):
            out.append(f'design.uncertainties may only hold {HEIGHT_NOTE}')
    for block in ('style', 'layout'):
        for key in ((d.get(block) or {}).get('provenance') or {}):
            if not name_ok(key, ID_RE, ID_MAX):
                out.append(f'design.{block}.provenance keys must be short ids')
    if len(d['project_id']) > ID_MAX:
        out.append(f'design.project_id must be at most {ID_MAX} characters')
    for e in els:
        if len(e['id']) > ID_MAX or len(((e.get('params') or {}).get('role')) or '') > ID_MAX:
            out.append(f"element {e['id'][:ID_MAX]}: id and params.role must be at most {ID_MAX} characters")
        if e.get('note') is not None:
            out.append(f"element {e['id']}: note must be null or absent (no free text)")
        body = (e.get('text') or {}).get('body')
        if body is not None and not PLACEHOLDER.fullmatch(body):
            out.append(f"element {e['id']}: text.body must be placeholder text: only X and spaces, as many as the picture's text")
    for i, u in enumerate(doc['unexpressible']):
        if len(u['what']) > FREE_TEXT_MAX or len(u['why']) > FREE_TEXT_MAX:
            out.append(f'unexpressible[{i}]: what and why must be at most {FREE_TEXT_MAX} characters')
        if u['nearest_kind'] is not None and u['nearest_kind'] not in kinds():
            out.append(f'unexpressible[{i}]: nearest_kind must be a catalogue kind or null')
    if set(doc['evidence']) != set(ids):
        out.append('evidence must have exactly one entry per element id, and no other')
    box_fit = (request or {}).get('box_fit') or {}
    img = box_fit.get('image') or {}
    for eid, ev in sorted(doc['evidence'].items()):
        if not box_ok(ev['bbox_px'], img.get('width'), img.get('height')):
            out.append(f'evidence {eid}: bbox_px must be [x0, y0, x1, y1] with 0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height')
    probe = copy.deepcopy(d)
    dims = box_fit.get('dims_m') or {}
    if _positive(dims.get('W')) and _positive(dims.get('D')):
        probe['form']['footprint_m'] = [float(dims['W']), float(dims['D'])]       # the code uses the fitted footprint
    compiler = _design_compile()
    for p in compiler.validate_design(probe)[:3]:
        out.append(f"design {p['where']}: {p['message']} ({p['code']})")
    opened = set(d['form']['open_sides'])
    for e in els:                     # the compiler would drop these without a word in validate_design: a wall kind on an open side
        if e['kind'] in compiler.mt.WALL_KINDS and e.get('side') in opened:
            out.append(f"element {e['id']}: a {e['kind']} needs a wall, but the {e['side']} side is open")
    return out


def box_inside(box, size):
    """Is a pixel box [x0, y0, x1, y1] inside a picture of size (width, height)? (A critic's bbox_px is in whole-picture pixels.)"""
    return bool(box_ok(box) and box[0] >= 0 and box[1] >= 0 and box[2] <= size[0] and box[3] <= size[1])


def critic_rules(doc, request=None):
    """The critic's answer beyond the schema: finding ids, elements of the request, the direction that goes with the type, boxes,
    and a verdict that agrees with the findings."""
    out = []
    shown = None
    if isinstance((request or {}).get('elements'), list):
        shown = {e.get('element_id') for e in request['elements'] if isinstance(e, dict)}
    seen = set()
    for i, f in enumerate(doc['findings']):
        where = f'findings[{i}]'
        if not FINDING_ID.fullmatch(f['id']) or f['id'] in seen:
            out.append(f'{where}.id must be f1, f2 ... and not repeated')
        seen.add(f['id'])
        if shown is not None and f['element_id'] is not None and f['element_id'] not in shown:
            out.append(f'{where}.element_id must be null or an element of the request')
        if f['type'] == 'camera_off' and f['element_id'] is not None:
            out.append(f'{where}: camera_off is about the whole picture, element_id must be null')
        if f['direction'] not in DIRECTION_BY_TYPE[f['type']]:
            out.append(f"{where}.direction {f['direction']!r} does not go with type {f['type']}")
        if f['bbox_px'] is not None and not box_ok(f['bbox_px']):
            out.append(f'{where}.bbox_px must be null or four numbers with 0 <= x0 < x1 and 0 <= y0 < y1')
    if doc['verdict'] == 'accept' and doc['findings']:
        out.append('verdict accept goes with no findings')
    if doc['verdict'] == 'fix' and not doc['findings']:
        out.append('verdict fix needs at least one finding')
    return out


def check(fn, *args):
    """Run a checker; whatever it raises is a problem, not a crash."""
    try:
        return fn(*args)
    except Exception as ex:
        return [f'checker error: {type(ex).__name__}: {ex}']


def problems(role, doc, request=None):
    """Every reason why `doc` is not a valid answer of `role` to `request` (empty when it is)."""
    try:          # first: whatever is accepted is written back later, so it must be plain JSON text (no NaN, Infinity, lone surrogate)
        json.dumps(doc, ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (UnicodeEncodeError, RecursionError, ValueError, TypeError) as ex:
        return [f'cannot be written as plain UTF-8 JSON ({type(ex).__name__}: NaN, Infinity or a lone surrogate?)']
    found = check(schema_problems, role, doc)
    if found:
        return found
    found = check(request_problems, role, doc, request)
    if role == 'reader':
        found += check(reader_rules, doc, request)
    elif role == 'critic':
        found += check(critic_rules, doc, request)
    return found


def validate(role, response, request):
    """Check one answer. role: a request name (reader, critic, judge_p1, judge_best_p2 ...); response: what wait_response gave
    (None, Unreadable or the parsed JSON); request: the request payload it answers. -> Result(value, status, reasons), with value
    UNDECIDED unless status is ok."""
    base = base_role(role)
    if response is None:
        return Result(UNDECIDED, 'no_response', ('no response',))
    if isinstance(response, Unreadable):
        return Result(UNDECIDED, 'invalid', (str(response.reason),))
    found = problems(base, response, request)
    if found:
        return Result(UNDECIDED, 'invalid', tuple(str(p) for p in found))
    return Result(response, 'ok', ())


# -- the checkers by name (P8a's signatures)

def reader_problems(doc, request=None):
    return problems('reader', doc, request)


def critic_problems(doc, rnd, candidate_id):
    return problems('critic', doc, {'round': rnd, 'candidate_id': candidate_id})


def judge_problems(doc, pair_id, pass_no, judge_id):
    """judge.v1 in tie-break mode: scores, items and extra_parts are null; the pass and the pair id are those of the request."""
    return problems('judge', doc, {'pair_id': pair_id, 'pass': pass_no, 'judge_id': judge_id})
