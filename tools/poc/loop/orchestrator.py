"""The closed loop of the POC (docs/poc/02-architecture-and-contracts.md section 1): read once, render, compare, criticise, edit,
judge, stop. The three model roles and Blender sit behind small interfaces, so the whole loop also runs with stand-ins.

    python tools/poc/loop/orchestrator.py --picture <png> --fit <box_fit.json> --truth <truth dir> --design-init <design.json|none>
                                          --out <folder> [--map <json>] [--max-rounds 4] [--max-minutes 10] [--stub]
                                          [--catalog <dir>]

--stub uses stubs.py (no model, no Blender, one fixture picture copy per render; it needs a --design-init), through
CheckedStubRoles, which brings the stand-in answers up to the checks of roles.py. Without it the roles
are files (roles.py): the loop writes `<role>.request.json` plus only the pictures and files that role may see into
`<out>/round_N/<role>_inputs/`, then waits for `<role>.response.json`, which the commander gets written by a fresh sub-agent with
the prompt tools/poc/prompts/<role>.md (section 6). A response counts only when it is not older than its request. A missing,
unreadable, invalid or too-old response means "cannot tell": the loop logs `no response` or `invalid` with
the reason and goes on. Waiting is capped by the time left of --max-minutes. The reader's folder also gets the family catalogue:
--catalog <dir> is a folder written by catalog.py (catalog.json, designs/, thumbnails); without it the text catalogue is built
from the schema. `--design-init none` (real mode) lets the reader make
the first design; if its answer is invalid there is nothing to go on: stop_reason reader_invalid, exit code 3, no best/.
Rendering is renderer.BoothRenderer (design-compile --fit --image, then preview under the Blender token); scoring is
tools/poc/score/score.py, one candidate per folder, --map passed through unchanged.

The output folder must be empty or absent (exit code 2, nothing written): a run is never repeated into the same folder.

Layout of <out> (candidate ids are r<round>_c<k>; c0 is the unchanged candidate):
  loop.json, loop.log
  _keys/round_N_judge.json    which candidate was A or B in each judge request (never inside a folder a role can see)
  best/                       the best round winner, ready for `boothctl.py build --spec best/spec.json`: the whole compile folder
                              (spec.json, source.<ext>, compile_report.json, design_context.json), design.json, score.json,
                              best.json (+ preview_render.png, sheet.png)
  round_N/
    reader.request.json, reader.response.json, reader_inputs/{picture.<ext>, catalog/}      round 1 only
    frame_check.json          round 1 only: the reader's frame against its own evidence boxes (edits.frame_check, 02 section 4.1): the mean
                              IoU of each of the four turns, whether it compiled, the decision (keep or turn) and the reason. It runs
                              after the fitted footprint is put on the design and before the first compile of a candidate; a turn changes
                              the design (reader.response.json stays as the reader gave it). Also logged ("frame_check: ...").
    candidates/<id>/          design.json, score.json, (fixer.json, edit_log.json | base.json), compile/, previews/ (or preview/)
    judge_p1.request.json, judge_p1_inputs/{original,A,B}.png, judge_p1.response.json, and the same for p2 (A and B swapped);
                              only when the scores cannot decide. The two passes are two requests (judge_id j1 and j2), each
                              answered by one judge.v1 object. Comparing the winner with the best of earlier rounds uses judge_best_p1 / judge_best_p2.
    critic.request.json, critic.response.json, critic_inputs/el_<element_id>/{original,render,overlay}.png   not after the last round;
                              crops that cannot be written are logged ("crops failed") and the critic gets a request without them
    fixer_skipped.json        [{finding_id, element_id, reason}]: the critic's findings of this round that did not become an edit of any
                              candidate (skipped by the rules, dropped by the measurement); each one is also a line of loop.log,
                              "skipped (finding fN): <reason>"; written in every round that has a critic answer, whatever the verdict
                              (accept, give_up, fix) and also when the critic gave none or an invalid one: an empty list when there is
                              nothing to list; findings that come with give_up are listed ("the critic gave up; not edited"). The
                              last round has no critic and so no file. Every loop.log entry is one line (newlines in an error text
                              are folded); the json files keep the full text.
    round.json                the winner, the scores, the time, the critic verdict, and judge_outcome: how the two passes of the judge that
                              broke a tie among the candidates ended (agree: both name the same candidate; both_same: both cannot tell;
                              split: one names a candidate, the other cannot tell; conflict: they name different candidates; null: no
                              judge was asked); a pass without a valid answer counts as "cannot tell"

Round N: candidates (round 1: the reader's design; later: the unchanged winner of the last round, whose score is reused, plus up
to 3 edited ones) -> render -> score -> winner (02 section 1 step 7: the smallest aux.edge_distance_diag leads; all within 0.002,
i.e. 0.2 % of the diagonal, of the leader are tied, the gap taken to 6 decimals as score.py gives the distance, so a gap of
exactly 0.002 is not a tie. One candidate in the tie: it wins. The unchanged candidate in the tie: the leader
(or, if the leader is the unchanged candidate, the next smallest) is judged against it and "cannot tell" keeps the unchanged one.
The unchanged candidate not in the tie: the two smallest are judged and "cannot tell" takes the smaller. A candidate without an
edge distance cannot win, unless none has one: then the unchanged candidate stays. A judge needs both passes to agree. `total`
holds only the code parts inside the loop, so it is recorded in round.json and never decides) -> the winner is compared with the
best so far by the same rule (the best is the "unchanged" one) and replaces it when it wins (an edge distance smaller by 0.002 or more, or a judge win: that is an "improvement" for the
plateau rule) -> stop test -> critic on the
winner's crops (the picture's, the render's, and the picture's with the render outline drawn on it; the request gives each crop's
top left corner in the picture and its enlargement) -> edits for round N+1: geometry only (moves and sizes, sized from the critic's
bbox_px against the render's box), colour only, and all of them; a design that was a candidate or was proposed before is not
proposed again. Every geometry edit (move, resize, count) is measured on its own with a design-compile (no Blender), applied alone
to the winner's design: an edit with no effect, or whose element is lost, is dropped and logged ("<op> dropped") and takes no
slot, a move's delta_m is recorded as measured, a count is judged by the built units. The edits that stay are composed and
compiled again: a design that does not compile is not proposed, and a resize that went against the critic's direction is
dropped. The crops have a
margin of a quarter of the box (at least 6 px) on each side. Stops: plateau (two rounds in a row without an improvement), --max-rounds,
--max-minutes (checked before every round, so 0 stops at once). round.json records the state of every role response of the
round (ok, no_response or invalid) and the real spec path of the winner.

The stop rules are only the three of 02 section 1 step 8: no better score (two rounds in a row; `--no-plateau` switches this one
off), the round limit, the time limit. A critic verdict of accept or give_up does not stop the loop: that round simply has no
edited candidates.

Exit code 0 when the loop ran (whatever the stop reason); 2 for unusable input; 3 when the reader was invalid and there was no
initial design.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import math
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

try:
    from . import catalog as catmod
    from . import edits, stubs
    from . import renderer as rmod
    from . import roles as rolemod
except ImportError:                  # run as a script: tools/poc/loop is sys.path[0]
    import catalog as catmod
    import edits
    import stubs
    import renderer as rmod
    import roles as rolemod

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.append(str(ROOT / 'booth-image-to-model' / 'scripts'))
sys.path.append(str(HERE.parent))                                  # tools/poc, for score.metrics
import camera_math  # noqa: E402  (the skill's projection, read only)
from score import metrics  # noqa: E402  (render_edges: the outline the overlay draws)

EDGE_TIE = 0.002                     # 0.2 % of the diagonal: smaller edge-distance differences do not decide
EDGE_DECIMALS = 6                    # score.py gives aux.edge_distance_diag to 6 decimals; the gap is compared at that precision
PLATEAU_ROUNDS = 2
MAX_EDITED = 3
CROP_PAD_PX = 6                      # the smallest margin round a crop ...
CROP_MARGIN_FRACTION = 0.25          # ... or a quarter of the box, so that a 30 px offset of a 120 px box is still in the crop
CROP_MIN_SIDE = 192                  # a crop whose longer side is shorter is enlarged by a whole factor (at most CROP_MAX_SCALE)
CROP_MAX_SCALE = 4
OVERLAY_COLOUR = (255, 0, 255)       # the render outline on the picture crop
RESPONSE_KEYS = rolemod.REQUEST_NAMES
PICTURE_SUFFIXES = ('.png', '.jpg', '.jpeg')

# The answers of the model roles are checked in roles.py (contract schema plus the ids of the request); these names stay here.
Unreadable = rolemod.Unreadable
reject_constant = rolemod.reject_constant
reader_problems = rolemod.reader_problems
critic_problems = rolemod.critic_problems
judge_problems = rolemod.judge_problems
check = rolemod.check


class InputError(ValueError):
    """Unusable input: reported on stderr, exit code 2."""


@dataclass
class Config:
    picture: Path
    fit: Path
    truth: Path
    design_init: Path | None
    out: Path
    map: Path | None = None
    max_rounds: int = 4
    max_minutes: float = 10.0
    stub: bool = False
    picture_id: str | None = None
    blender: str | None = None
    response_timeout: float = 900.0
    token_wait: float = 600.0
    stub_target: Path | None = None
    plateau: bool = True
    catalog: Path | None = None


# -- small helpers

def jread(path):
    try:
        doc = json.loads(Path(path).read_text(encoding='utf-8-sig'), parse_constant=reject_constant)
        json.dumps(doc, ensure_ascii=False, allow_nan=False).encode('utf-8')      # no 1e999, no lone surrogate: it is written back later
        return doc
    except FileNotFoundError:
        raise InputError(f'FILE_MISSING: {path}')
    except (OSError, ValueError, RecursionError) as ex:         # UnicodeEncodeError is a ValueError
        raise InputError(f'BAD_JSON: {path}: {type(ex).__name__}: {ex}')


def jwrite(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(obj, indent=2, ensure_ascii=False) + '\n').encode('utf-8'))


def png_bytes(image):
    buf = io.BytesIO()
    image.save(buf, format='PNG')
    return buf.getvalue()


def flat_png(src):
    """A picture as RGB PNG bytes; transparent render pixels become mid grey."""
    with Image.open(src) as im:
        rgba = im.convert('RGBA')
        base = Image.new('RGBA', rgba.size, (128, 128, 128, 255))
        base.alpha_composite(rgba)
        return png_bytes(base.convert('RGB'))


def picture_input(picture):
    """The original picture for the reader's folder under a neutral name: (name, bytes or path). PNG and JPEG are copied as they
    are; any other format is re-encoded as PNG."""
    suffix = Path(picture).suffix.lower()
    if suffix in PICTURE_SUFFIXES:
        return f'picture{suffix}', Path(picture)
    with Image.open(picture) as im:
        return 'picture.png', png_bytes(im.convert('RGB'))


class Log:
    def __init__(self, path, clock, start):
        self.path, self.clock, self.start = Path(path), clock, start

    def line(self, text):
        """One entry, one line: newlines and runs of white space in the text (the pretty-printed JSON of a failed design-compile, say)
        become single spaces. The json files keep the full text."""
        text = ' '.join(str(text).split())
        with open(self.path, 'ab') as f:
            f.write(f'[+{self.clock() - self.start:7.1f}s] {text}\n'.encode('utf-8', errors='backslashreplace'))


def apply_fit_footprint(design, fit):
    """The footprint comes from the corner fit, never from the reader (02 section 4.1)."""
    out = copy.deepcopy(design)
    dims = fit.get('dims_m') or {}
    if all(isinstance(dims.get(k), (int, float)) and dims[k] > 0 for k in 'WD'):
        out['form']['footprint_m'] = [round(float(dims['W']), 4), round(float(dims['D']), 4)]
    return out


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


# -- the roles as files

class FileRoles:
    """Real mode: the request files are already written; wait for the response file and read it. None means "cannot tell".

    A response file older than its request is ignored (it belongs to an earlier run). The wait is `timeout_s` at most, and at most
    the `remaining_s` the loop passes in (the time left of --max-minutes); roles.wait_response does the polling."""

    def __init__(self, timeout_s=900.0, sleep=time.sleep, clock=time.monotonic, poll_s=2.0):
        self.timeout_s, self.sleep, self.clock, self.poll_s = timeout_s, sleep, clock, poll_s

    def wait_json(self, path, newer_than=None, remaining_s=None):
        return rolemod.wait_response(path, self.timeout_s, newer_than=newer_than, remaining_s=remaining_s, sleep=self.sleep,
                                     clock=self.clock, poll_s=self.poll_s)

    def _wait(self, ctx, name):
        return self.wait_json(Path(ctx['rdir']) / name, newer_than=ctx.get('request_path'), remaining_s=ctx.get('remaining_s'))

    def read(self, ctx):
        return self._wait(ctx, 'reader.response.json')

    def critic(self, ctx):
        return self._wait(ctx, 'critic.response.json')

    def judge(self, ctx):
        return self._wait(ctx, ctx['response_name'])


def stub_parts(cfg):
    fit = jread(cfg.fit)
    if cfg.stub_target:
        target = jread(cfg.stub_target)
        if not isinstance(target, dict):
            raise InputError(f'{cfg.stub_target}: the stub target must be a booth-design.v1 object')
        problems = edits.validate_design(target)
        if problems:
            raise InputError(f'{cfg.stub_target}: the stub target is not a valid booth-design.v1: {problems[0]}')
    else:
        if cfg.design_init is None:
            raise InputError('--stub needs a --design-init (the stand-in reader returns it)')
        target = stubs.make_target(apply_fit_footprint(jread(cfg.design_init), fit))
    pid = cfg.picture_id or Path(cfg.truth).name or 'pic_stub'
    img = fit.get('image') or {}
    size = (img['width'], img['height']) if img.get('width') and img.get('height') else None
    return {'roles': CheckedStubRoles(target, fit.get('camera'), size), 'renderer': stubs.StubRenderer(cfg.picture, fit),
            'scorer': stubs.StubScorer(target, pid)}


class CheckedStubRoles(stubs.StubRoles):
    """The stand-in roles of stubs.py, brought up to the checks of roles.py: the reader gives a box for every element (the whole
    picture, low confidence), the critic speaks only of the elements it was shown (findings renumbered, the verdict following
    them), the judge leaves items null in tie-break mode."""

    def read(self, ctx):
        doc = super().read(ctx)
        img = ctx['request']['box_fit'].get('image') or {}
        box = [0, 0, img.get('width', 1), img.get('height', 1)]
        doc['evidence'] = {e['id']: {'bbox_px': list(box), 'confidence': 'low'} for e in doc['design']['elements']}
        return doc

    def critic(self, ctx):
        doc = super().critic(ctx)
        shown = {e['element_id'] for e in (ctx.get('request') or {}).get('elements', [])}
        kept = [f for f in doc['findings'] if f['element_id'] is None or f['element_id'] in shown]
        for k, f in enumerate(kept, start=1):
            f['id'] = f'f{k}'
        doc['findings'], doc['verdict'] = kept, 'fix' if kept else 'accept'
        return doc

    def judge(self, ctx):
        doc = super().judge(ctx)
        doc['items'] = None
        return doc


def real_parts(cfg):
    pid = cfg.picture_id or Path(cfg.truth).name or 'pic'
    return {'roles': FileRoles(timeout_s=cfg.response_timeout),
            'renderer': rmod.BoothRenderer(cfg.fit, cfg.picture, holder=f'loop-{pid}', blender=cfg.blender, wait_s=cfg.token_wait),
            'scorer': rmod.RealScorer(cfg.picture, cfg.truth, cfg.map)}


# -- picture crops

component_points = edits.component_points


def crop_margin(width, height):
    """(x, y) margin in pixels round a box of this size: a quarter of it, at least CROP_PAD_PX."""
    return (max(CROP_PAD_PX, round(CROP_MARGIN_FRACTION * width)), max(CROP_PAD_PX, round(CROP_MARGIN_FRACTION * height)))


def element_pixel_boxes(element_ids, spec):
    """{element_id: [x0, y0, x1, y1]} in picture pixels: all projected mesh vertices of every component of the element. A component
    belongs to the element named by its `group` (design_compile sets it to the element id); without a group, to the element with
    the longest id that is its id or a prefix up to '_'. Elements whose components are all behind the camera or outside the picture
    are left out."""
    cam, src = spec['camera'], spec['source']
    width, height = int(src['width']), int(src['height'])
    known = set(element_ids)
    ids = sorted(known, key=len, reverse=True)
    pts = {}
    for comp in spec.get('components', []):
        owner = edits.component_owner(comp, known, ids)
        if owner is None:
            continue
        for point in component_points(comp):
            try:
                u, v, _ = camera_math.project(list(point), cam, width, height)
            except ValueError:
                continue
            pts.setdefault(owner, []).append((u, v))
    boxes = {}
    for eid, uv in pts.items():
        u0, u1 = int(math.floor(min(u for u, _ in uv))), int(math.ceil(max(u for u, _ in uv)))
        v0, v1 = int(math.floor(min(v for _, v in uv))), int(math.ceil(max(v for _, v in uv)))
        mx, my = crop_margin(u1 - u0, v1 - v0)
        x0, y0, x1, y1 = max(0, u0 - mx), max(0, v0 - my), min(width, u1 + mx), min(height, v1 + my)
        if x1 - x0 >= 4 and y1 - y0 >= 4:
            boxes[eid] = [x0, y0, x1, y1]
    return boxes


def crop_pairs(picture, render, boxes):
    """The three pictures of every box as PNG bytes and the scale they were enlarged by.
    -> {element_id: {'original': bytes, 'render': bytes, 'overlay': bytes, 'scale': int}}.

    original: the picture's crop; render: the render's crop (empty parts mid grey); overlay: the picture's crop with the outline of
    the render drawn in one conspicuous colour (the render edges as score.metrics.render_edges finds them). A crop whose longer side
    is under CROP_MIN_SIDE pixels is enlarged by a whole factor of at most CROP_MAX_SCALE (edges stay sharp), so that a few
    centimetres of offset are visible; the three pictures of an element have the same size."""
    made = {}
    with Image.open(picture) as pic, Image.open(render) as ren:
        if ren.size != pic.size:
            raise InputError(f'the render is {ren.size[0]}x{ren.size[1]} but the picture is {pic.size[0]}x{pic.size[1]}: no crops')
        pic = pic.convert('RGB')
        ren_rgba = ren.convert('RGBA')
        edges = metrics.render_edges(np.asarray(ren_rgba, np.uint8))
        flat = Image.new('RGBA', ren_rgba.size, (128, 128, 128, 255))
        flat.alpha_composite(ren_rgba)
        flat = flat.convert('RGB')
        for eid, box in sorted(boxes.items()):
            x0, y0, x1, y1 = box
            w, h = x1 - x0, y1 - y0
            scale = min(CROP_MAX_SCALE, max(1, -(-CROP_MIN_SIDE // max(w, h))))
            o, r = pic.crop(tuple(box)), flat.crop(tuple(box))
            m = Image.fromarray(edges[y0:y1, x0:x1].astype(np.uint8) * 255)
            if scale > 1:
                size = (w * scale, h * scale)
                o, r, m = o.resize(size, Image.LANCZOS), r.resize(size, Image.LANCZOS), m.resize(size, Image.NEAREST)
            drawn = np.array(o)
            drawn[np.asarray(m) > 0] = OVERLAY_COLOUR
            made[eid] = {'original': png_bytes(o), 'render': png_bytes(r), 'overlay': png_bytes(Image.fromarray(drawn)), 'scale': scale}
    return made


# -- the loop

class Candidate:
    def __init__(self, cid, order, design, directory):
        self.id, self.order, self.design, self.dir = cid, order, design, Path(directory)
        self.score = None
        self.preview = None
        self.compile_dir = None
        self.error = None
        self.reused_from = None
        self.edit_info = None

    @property
    def origin(self):
        """The candidate whose render and score this one stands for (an unchanged candidate reuses its predecessor's)."""
        return self.reused_from.origin if self.reused_from else self.id

    @property
    def total(self):
        return float(self.score['total'])

    @property
    def edge(self):
        v = ((self.score or {}).get('aux') or {}).get('edge_distance_diag')
        return float(v) if isinstance(v, (int, float)) else None


def cand_row(c):
    return {'id': c.id, 'total': None if c.score is None else c.total, 'edge_distance_diag': c.edge,
            'status': None if c.score is None else c.score.get('status'), 'error': c.error,
            'reused_from': c.reused_from.id if c.reused_from else None,
            'edits_applied': len(c.edit_info['report']['applied']) if c.edit_info else 0,
            'edits_rejected': len(c.edit_info['report']['rejected']) if c.edit_info else 0}


class Loop:
    def __init__(self, cfg, clock, start, roles, renderer, scorer, fit, design_init, catalog_files=None):
        self.cfg, self.clock, self.start = cfg, clock, start
        self.roles, self.renderer, self.scorer = roles, renderer, scorer
        self.fit, self.design_init = fit, design_init
        self.catalog_files = catalog_files if catalog_files is not None else catmod.reader_files()
        self.out = cfg.out
        self.picture_id = cfg.picture_id or cfg.truth.name or 'pic'
        self.log = Log(self.out / 'loop.log', clock, start)
        self.states = {}                  # role response states of the current round
        self.verdicts = {}                # judged pairs of the current round: {frozenset of origins: winning origin or None}
        self.outcomes = {}                # how the two passes ended, per judged pair: agree, both_same, split or conflict
        self.round_outcome = None         # the outcome of the judge that picked this round's winner (None: no judge was asked)
        self.reader_design = None
        self.reader_state = None
        self.tried = set()                # hashes of every design that was a candidate or has been proposed: none is proposed twice

    def remaining_s(self):
        return max(0.0, self.cfg.max_minutes * 60.0 - (self.clock() - self.start))

    def over(self):
        return (self.clock() - self.start) / 60.0 >= self.cfg.max_minutes

    # -- one answer from a role: checked strictly (roles.validate); no response and invalid are logged apart
    def answer(self, label, raw, name, request):
        """-> (the answer or None for "cannot tell", status ok, no_response or invalid); the status goes into round.json."""
        result = rolemod.validate(name, raw, request)
        if result.status == 'no_response':
            self.log.line(f'{label}: no response')
        elif not result.ok:
            self.log.line(f'{label}: invalid response: {result.reasons[0]}')
        self.states[name] = result.status
        if name == 'reader':
            self.reader_state = result.status
        return (result.value if result.ok else None), result.status

    def run(self):
        cfg, out = self.cfg, self.out
        self.log.line(f"start picture_id={self.picture_id} stub={cfg.stub} max_rounds={cfg.max_rounds} max_minutes={cfg.max_minutes}")
        best, best_round = None, None
        no_improve, rnd, stop = 0, 0, None
        carried, pending, details = None, [], []
        while True:
            if self.over():
                stop = 'minutes'
                break
            if rnd >= cfg.max_rounds:
                stop = 'rounds'
                break
            rnd += 1
            t_round = self.clock()
            self.verdicts, self.outcomes, self.round_outcome = {}, {}, None
            if rnd > 1:
                self.states = {}
            rdir = out / f'round_{rnd}'
            (rdir / 'candidates').mkdir(parents=True, exist_ok=True)
            cands = []
            if rnd == 1:
                design = self.reader_step(rdir)
                if design is None:
                    stop = 'reader_invalid'
                    jwrite(rdir / 'round.json', self.blank_row(rnd, [], no_improve, best, t_round))
                    break
                self.tried.add(edits.design_hash(design))
                cands.append(Candidate('r1_c0', 0, design, rdir / 'candidates' / 'r1_c0'))
            else:
                base = Candidate(f'r{rnd}_c0', 0, carried.design, rdir / 'candidates' / f'r{rnd}_c0')
                base.reused_from = carried
                cands.append(base)
                for k, p in enumerate(pending, start=1):
                    c = Candidate(f'r{rnd}_c{k}', k, p['design'], rdir / 'candidates' / f'r{rnd}_c{k}')
                    c.edit_info = p
                    cands.append(c)
            for c in cands:
                if c.order > 0 and self.over():
                    self.log.line(f'{c.id} skipped: out of time')
                    c.error = 'skipped: out of time'
                    continue
                self.evaluate(c)
            scored = [c for c in cands if c.score is not None]
            if not scored:
                self.log.line('no candidate could be scored')
                stop = 'no_candidates'
                jwrite(rdir / 'round.json', self.blank_row(rnd, cands, no_improve, best, t_round))
                break
            winner, decided_by = self.pick_winner(rdir, rnd, scored)
            if best is None:
                improved, vs_best = True, 'first'
            else:
                improved, vs_best = self.beats(rdir, rnd, winner, best)
            if improved:
                best, best_round = winner, rnd
                self.log.line(f'new best {winner.id} total={winner.total}')
            no_improve = 0 if improved else no_improve + 1
            carried = winner
            row = self.blank_row(rnd, cands, no_improve, best, t_round)
            row.update(winner=winner.id, winner_total=winner.total, decided_by=decided_by, improved=improved, vs_best=vs_best,
                       judge_outcome=self.round_outcome)
            if cfg.plateau and no_improve >= PLATEAU_ROUNDS:
                stop = 'plateau'
            elif rnd >= cfg.max_rounds:
                stop = 'rounds'
            elif self.over():
                stop = 'minutes'
            if stop is None:
                doc, info = self.critic_step(rdir, rnd, winner)
                row['critic'] = info
                # accept and give_up do not stop the loop; that round just has no edited candidates
                if info['verdict'] == 'fix':
                    pending = self.next_candidates(winner, doc, rdir)
                else:
                    pending = []
                    self.record_unedited(rdir, doc, info['verdict'])
            row['responses'] = {k: self.states.get(k) for k in RESPONSE_KEYS}
            row['winner_spec'] = self.spec_path(winner)
            row['seconds'] = round(self.clock() - t_round, 3)
            jwrite(rdir / 'round.json', row)
            details.append({'round': rnd, 'winner': winner.id, 'winner_total': winner.total})
            if stop:
                break
        self.log.line(f'stop: {stop} after {len(details)} round(s)')
        if stop != 'reader_invalid':
            fallback = self.reader_design or (apply_fit_footprint(self.design_init, self.fit) if self.design_init is not None else None)
            finalize_best(out, best, best_round, fallback)
        summary = {'picture_id': self.picture_id, 'stub': bool(cfg.stub), 'rounds': len(details), 'stop_reason': stop,
                   'reader': self.reader_state, 'elapsed_s': round(self.clock() - self.start, 3),
                   'best': {'candidate_id': best.id, 'round': best_round, 'total': best.total, 'edge_distance_diag': best.edge} if best else None,
                   'winners': details}
        jwrite(out / 'loop.json', summary)
        return summary

    def reader_step(self, rdir):
        """-> the first design, or None when the reader's answer is unusable and there is no initial design."""
        pic_name, pic_source = picture_input(self.cfg.picture)
        inputs = {pic_name: pic_source, **{f'catalog/{rel}': src for rel, src in self.catalog_files.items()}}
        dims, img = self.fit['dims_m'], self.fit['image']
        request = {'role': 'reader', 'picture_id': self.picture_id, 'picture': f'reader_inputs/{pic_name}',
                   'box_fit': {'dims_m': {k: dims[k] for k in ('W', 'D', 'H') if _is_num(dims.get(k)) and dims[k] > 0},
                               'image': {'width': img['width'], 'height': img['height']}},
                   'catalog': 'reader_inputs/catalog/catalog.json', 'response': 'reader.response.json'}
        path = rolemod.write_request('reader', rdir, request, inputs)
        raw = self.roles.read({'rdir': rdir, 'picture_id': self.picture_id, 'design_init': copy.deepcopy(self.design_init),
                               'request': request, 'request_path': path, 'remaining_s': self.remaining_s()})
        doc, status = self.answer('reader', raw, 'reader', request)
        if doc is None:
            if self.design_init is None:
                self.log.line('reader_invalid: no usable answer and no initial design')
                return None
            self.log.line(f'reader_fallback: {status}; the initial design is used')
            design = self.design_init
        else:
            design = doc['design']
            if not (rdir / 'reader.response.json').exists():
                jwrite(rdir / 'reader.response.json', doc)
        self.reader_design = self.frame_step(rdir, apply_fit_footprint(design, self.fit), None if doc is None else doc['evidence'])
        return self.reader_design

    def frame_step(self, rdir, design, evidence):
        """The reader's frame against its own evidence boxes (edits.frame_check): the design to go on with. Writes frame_check.json and
        logs it; it never stops the loop (what cannot be checked leaves the design as it is)."""
        compile_fn = self.recompile if getattr(self.renderer, 'compile_design', None) else None
        img = self.fit['image']
        try:
            checked, report = edits.frame_check(design, evidence, compile_fn, camera=self.fit['camera'], size=(img['width'], img['height']),
                                                log=self.log.line)
        except Exception as ex:                       # a check that fails is a check that did not happen
            checked = design
            try:
                report = edits.frame_report(design, evidence, (img['width'], img['height']))
            except Exception:
                report = {'decision': 'keep', 'quarter_turns': 0, 'reason': '', 'elements': len(design.get('elements') or []),
                          'evidence_elements': None, 'gates': {}, 'rotations': []}
            report['reason'] = f'frame unverified: the check failed ({type(ex).__name__}: {ex})'
            self.log.line(f"frame_check: keep 0 degrees: {report['reason']}")
        jwrite(rdir / 'frame_check.json', report)
        return checked

    def blank_row(self, rnd, cands, no_improve, best, t_round):
        """A round.json entry with every field present (null where the round got no further)."""
        return {'round': rnd, 'candidates': [cand_row(c) for c in cands], 'winner': None, 'winner_total': None, 'decided_by': None,
                'improved': False, 'vs_best': None, 'no_improve_rounds': no_improve, 'best_so_far': best.id if best else None,
                'judge_outcome': None, 'critic': None, 'responses': {k: self.states.get(k) for k in RESPONSE_KEYS}, 'winner_spec': None,
                'seconds': round(self.clock() - t_round, 3)}

    def spec_path(self, c):
        """The real spec.json of a candidate (an unchanged candidate resolves to the one it stands for), relative to the output folder."""
        for d, name in ((c.compile_dir, 'spec.json'), (c.preview, 'spec.json')):
            if d is not None and (Path(d) / name).is_file():
                try:
                    return (Path(d) / name).resolve().relative_to(self.out.resolve()).as_posix()
                except ValueError:
                    return str(Path(d) / name)
        return None

    def evaluate(self, c):
        c.dir.mkdir(parents=True, exist_ok=True)
        jwrite(c.dir / 'design.json', c.design)
        if c.edit_info:
            jwrite(c.dir / 'fixer.json', edits.make_fixer(c.edit_info['report']['applied']))
            jwrite(c.dir / 'edit_log.json', {'variant': c.edit_info['variant'], 'applied': len(c.edit_info['report']['applied']),
                                             'rejected': c.edit_info['report']['rejected'], 'skipped_findings': c.edit_info['skipped']})
        if c.reused_from is not None:             # the unchanged candidate was scored last round: same design, same score
            jwrite(c.dir / 'base.json', {'reused_from': c.reused_from.id})
            c.score, c.preview, c.compile_dir = copy.deepcopy(c.reused_from.score), c.reused_from.preview, c.reused_from.compile_dir
            jwrite(c.dir / 'score.json', c.score)
            return
        try:
            c.preview = Path(self.renderer.render(c.dir, c.design, c.id, time_left_s=self.remaining_s()))
            c.score = self.scorer.score(c.dir, c.preview, c.id, c.design)
        except rmod.RenderError as ex:
            c.error, c.score = str(ex), None
            self.log.line(f'{c.id} dropped: {ex}')
            return
        c.compile_dir = c.dir / 'compile' if (c.dir / 'compile' / 'spec.json').is_file() else None
        if not (c.dir / 'score.json').exists():
            jwrite(c.dir / 'score.json', c.score)

    # -- ordering: edge distance, then the judge (02 section 1 step 7)
    def ask(self, rdir, rnd, a, b, tag):
        """Judge a against b (two passes). -> (chosen candidate or None, how). A pair the judge has already ruled on this round
        is not asked again."""
        pair = frozenset((a.origin, b.origin))
        if pair in self.verdicts:
            won = self.verdicts[pair]
            return next((c for c in (a, b) if c.origin == won), None), 'judge_reused'
        chosen = self.judge_step(rdir, rnd, a, b, tag)
        if tag == '':
            self.round_outcome = self.outcomes.get(pair)
        return chosen, ('judge' if chosen is not None else 'judge_unavailable')

    def decide(self, cands, original, rdir, rnd, tag):
        """The rule of 02 section 1 step 7 for several candidates; `original` is the unchanged candidate (or the best so far when
        comparing with it). -> (winner, how)."""
        withedge = sorted((c for c in cands if c.edge is not None), key=lambda c: (c.edge, c is not original, c.order))
        if not withedge:
            return (original or sorted(cands, key=lambda c: c.order)[0]), 'no_edge'
        lead = withedge[0]
        tied = [c for c in withedge if round(c.edge - lead.edge, EDGE_DECIMALS) < EDGE_TIE]
        if len(tied) == 1:
            return lead, 'edge'
        if original in tied:
            other = tied[1] if lead is original else lead
            chosen, how = self.ask(rdir, rnd, original, other, tag)
            return (chosen if chosen is not None else original), how
        a, b = tied[0], tied[1]                                        # the two smallest edge distances
        chosen, how = self.ask(rdir, rnd, a, b, tag)
        return (chosen if chosen is not None else a), how

    def pick_winner(self, rdir, rnd, scored):
        if len(scored) == 1:
            return scored[0], 'only'
        original = next((c for c in scored if c.order == 0), None)
        return self.decide(scored, original, rdir, rnd, '')

    def beats(self, rdir, rnd, winner, best):
        """Does the round winner replace the best so far? The best is the unchanged candidate. -> (bool, how)."""
        if winner.origin == best.origin:
            return False, 'same_candidate'
        chosen, how = self.decide([best, winner], best, rdir, rnd, '_best')
        return chosen is winner, how

    def judge_step(self, rdir, rnd, a, b, tag):
        """Two independent requests (pass 1 and pass 2, A/B swapped), each answered by one judge.v1 object. A candidate wins only
        when both passes name it; anything else (a pass missing, invalid, 'same' or the other candidate) -> None."""
        if self.over():
            self.log.line('judge: out of time, no request sent')
            return None
        pair_id = 'pair_' + hashlib.sha256(f'{self.out}:{rnd}{tag}:{a.id}:{b.id}'.encode()).hexdigest()[:10]
        first, second = (a, b) if int(pair_id[-1], 16) % 2 == 0 else (b, a)      # a fixed pseudo-random A/B order
        key, requests = {}, {}
        original = flat_png(self.cfg.picture)
        renders = {c.id: flat_png(c.preview / 'preview_render.png') for c in (a, b)}
        for p, (x, y) in ((1, (first, second)), (2, (second, first))):
            stem = f'judge{tag}_p{p}'
            request = {'role': 'judge', 'round': rnd, 'pair_id': pair_id, 'pass': p, 'judge_id': f'j{p}',
                       'question': 'Which render, A or B, is closer to the original picture?',
                       'original': f'{stem}_inputs/original.png', 'A': f'{stem}_inputs/A.png', 'B': f'{stem}_inputs/B.png',
                       'response': f'{stem}.response.json'}
            path = rolemod.write_request(stem, rdir, request, {'original.png': original, 'A.png': renders[x.id], 'B.png': renders[y.id]})
            key[p] = {'A': x.id, 'B': y.id}
            requests[p] = (stem, request, path)
        jwrite(self.out / '_keys' / f'round_{rnd}_judge{tag}.json', {'pair_id': pair_id, 'pass1': key[1], 'pass2': key[2]})
        votes = {}
        for p in (1, 2):
            stem, request, path = requests[p]
            raw = self.roles.judge({'rdir': rdir, 'request': request, 'request_path': path, 'pass': p,
                                    'response_name': f'{stem}.response.json', 'pair_id': pair_id, 'key': key[p],
                                    'scores': {a.id: a.total, b.id: b.total}, 'order': [a.id, b.id],
                                    'remaining_s': self.remaining_s()})
            doc, _ = self.answer(f'judge pass {p}', raw, stem, request)
            if doc is not None and not (rdir / f'{stem}.response.json').exists():
                jwrite(rdir / f'{stem}.response.json', doc)
            votes[p] = None if doc is None or doc['better'] == 'same' else key[p][doc['better']]
        chosen = None
        if votes[1] is not None and votes[1] == votes[2]:
            chosen = a if votes[1] == a.id else b
            outcome = 'agree'
        else:
            self.log.line('judge: the two passes do not both name the same candidate')
            named = [v for v in votes.values() if v is not None]
            outcome = 'both_same' if not named else 'split' if len(named) == 1 else 'conflict'      # no answer = "cannot tell"
        self.verdicts[frozenset((a.origin, b.origin))] = chosen.origin if chosen is not None else None
        self.outcomes[frozenset((a.origin, b.origin))] = outcome
        return chosen

    def critic_step(self, rdir, rnd, winner):
        """-> (critic.v1 document or None, round.json entry). The critic sees only cropped picture pairs, ids and family names."""
        boxes, made = {}, {}
        try:
            spec = jread(winner.preview / 'spec.json')
            boxes = element_pixel_boxes([e['id'] for e in winner.design['elements']], spec)
            made = crop_pairs(self.cfg.picture, winner.preview / 'preview_render.png', boxes)
        except (InputError, KeyError, TypeError, OSError, ValueError, IndexError) as ex:
            self.log.line(f'crops failed: {ex}')
        kinds = {e['id']: e['kind'] for e in winner.design['elements']}
        for eid in [e for e in made if not rolemod.ID_RE.fullmatch(e) or len(e) > rolemod.ID_MAX]:
            self.log.line(f'crop of {eid[:rolemod.ID_MAX]}... left out: the id is longer than {rolemod.ID_MAX} characters')
            del made[eid]
        def folder(eid):
            return f'critic_inputs/{rolemod.crop_folder(eid)}'
        request = {'role': 'critic', 'round': rnd, 'candidate_id': winner.id, 'response': 'critic.response.json',
                   'elements': [{'element_id': eid, 'family': kinds[eid], 'original_crop': f'{folder(eid)}/original.png',
                                 'render_crop': f'{folder(eid)}/render.png', 'bbox_px': boxes[eid],
                                 'overlay_crop': f'{folder(eid)}/overlay.png', 'crop_origin_px': [boxes[eid][0], boxes[eid][1]],
                                 'crop_scale': made[eid]['scale']} for eid in made]}
        inputs = {f'{rolemod.crop_folder(eid)}/{which}.png': made[eid][which] for eid in made for which in ('original', 'render', 'overlay')}
        try:
            path = rolemod.write_request('critic', rdir, request, inputs)
        except OSError as ex:                     # a folder or file that cannot be made: this round has no crops
            self.log.line(f'crops failed: {type(ex).__name__}: {ex}')
            request = dict(request, elements=[])
            try:
                path = rolemod.write_request('critic', rdir, request, {})
            except OSError as ex2:
                self.log.line(f'critic: the request could not be written: {type(ex2).__name__}: {ex2}')
                self.states['critic'] = 'no_response'
                return None, {'verdict': None, 'findings': 0, 'valid': False, 'status': 'no_response'}
        raw = self.roles.critic({'rdir': rdir, 'round': rnd, 'candidate_id': winner.id, 'design': copy.deepcopy(winner.design),
                                 'request': request, 'request_path': path, 'remaining_s': self.remaining_s()})
        doc, status = self.answer('critic', raw, 'critic', request)
        if doc is None:
            return None, {'verdict': None, 'findings': 0, 'valid': False, 'status': status}
        if not (rdir / 'critic.response.json').exists():
            jwrite(rdir / 'critic.response.json', doc)
        img = self.fit.get('image') or {}
        if img.get('width') and img.get('height'):         # a box outside the picture is a box that cannot be right: taken as null
            for f in doc['findings']:
                if f['bbox_px'] is not None and not rolemod.box_inside(f['bbox_px'], (img['width'], img['height'])):
                    self.log.line(f"critic: finding {f['id']}: bbox_px {f['bbox_px']} is outside the picture ({img['width']}x{img['height']}), "
                                  'taken as null: the small step')
                    f['bbox_px'] = None
        return doc, {'verdict': doc['verdict'], 'findings': len(doc['findings']), 'valid': True, 'status': 'ok'}

    def spec_view(self, c):
        """What the compiler built for a candidate (its spec), or None."""
        for d in (c.compile_dir, c.preview):
            if d is not None and (Path(d) / 'spec.json').is_file():
                try:
                    return edits.SpecView(jread(Path(d) / 'spec.json'), [e['id'] for e in c.design['elements']])
                except (InputError, KeyError, TypeError, ValueError) as ex:
                    self.log.line(f'{c.id}: the spec could not be read for the edits ({type(ex).__name__}: {ex})')
        return None

    def recompile(self, design):
        """The compiled spec of a design (no Blender), or None when the renderer cannot compile; RenderError when it does not compile."""
        compile_design = getattr(self.renderer, 'compile_design', None)
        return compile_design(design) if compile_design else None

    def record_unedited(self, rdir, critic_doc, verdict):
        """A round whose critic did not say fix (accept, give_up, no answer, an invalid answer): fixer_skipped.json lists the findings
        it still carries - nobody acts on them - with the reason, and is an empty list when there are none."""
        why = {'give_up': 'the critic gave up; not edited', 'accept': 'the critic accepted; not edited'}.get(verdict, 'the critic gave no usable verdict; not edited')
        rows = [{'finding_id': f['id'], 'element_id': f['element_id'], 'reason': why} for f in (critic_doc or {}).get('findings') or []]
        for row in rows:
            self.log.line(f"skipped (finding {row['finding_id']}): {row['reason']}")
        jwrite(Path(rdir) / 'fixer_skipped.json', rows)

    def next_candidates(self, winner, critic_doc, rdir=None):
        """Up to 3 edited designs from the critic's findings, by the kind of edit: geometry only (moves and sizes), colour only, and
        all of them. A design that was a candidate or was proposed before is not proposed again.

        Every geometry edit (move, resize, count) is measured on its own with a design-compile (no Blender): applied alone to the
        winner's design, compiled, and compared with the winner's compile. An edit with no effect, or whose element is lost, is
        dropped and logged and takes no slot; a move's delta_m is recorded as measured; a count is judged by the built units. The
        edits that stay are composed into a candidate, which is compiled again: one that does not compile is not proposed, and a resize
        in it that went against the critic's direction is dropped (logged as "resize dropped").

        Every finding that did not become an edit of any candidate - skipped by the rules of findings_to_edits, dropped by the
        measurement or the direction check, or already tried - is written to `<rdir>/fixer_skipped.json` with its reason and logged as
        "skipped (finding fN): <reason>", whatever candidates the round got (02 section 4.3, sixth note)."""
        if not critic_doc or not critic_doc['findings']:
            if rdir is not None:
                jwrite(Path(rdir) / 'fixer_skipped.json', [])
            return []
        view = self.spec_view(winner)
        camera = self.fit.get('camera')
        findings = edits.sort_findings(critic_doc['findings'])
        ids = [e['id'] for e in winner.design['elements']]
        made, cache, logged, unedited = [], {}, set(), {}

        def note(items):
            for item in items:
                unedited.setdefault(item['finding_id'], item['reason'])
        for name, only in (('geometry', edits.GEOMETRY_TYPES), ('colour', edits.COLOUR_TYPES), ('all', None)):
            edit_list, skipped = edits.findings_to_edits(winner.design, findings, camera=camera, spec=view, only=only)
            note(skipped)
            if not edit_list:
                continue
            recorded, dropped = {}, []
            if view is not None and getattr(self.renderer, 'compile_design', None):
                edit_list, recorded, dropped = edits.measure_edits(winner.design, view, edit_list, findings, self.recompile, cache)
                for d in dropped:
                    if d['finding_id'] not in logged:
                        logged.add(d['finding_id'])
                        self.log.line(f"{d['op']} dropped (finding {d['finding_id']}): {d['reason']}")
                skipped = skipped + dropped
                note(dropped)
                if not edit_list:
                    continue
            new, report = edits.apply_edits(winner.design, edit_list, log=self.log.line, view=view)
            note({'finding_id': r['edit']['finding_id'], 'reason': r['reason']} for r in report['rejected'])
            if not report['applied']:
                continue
            try:
                spec = self.recompile(new)
            except rmod.RenderError as ex:
                self.log.line(f'{name}: not proposed, the design does not compile ({str(ex)[:200]})')
                note({'finding_id': e['finding_id'], 'reason': f'the design with this edit does not compile ({str(ex)})'}
                     for e in report['applied'])
                continue
            if spec is not None and view is not None:        # the composed candidate: the direction of its resizes, once more
                try:
                    _, wrong = edits.check_resizes(winner.design, view, edits.SpecView(spec, ids),
                                                   [e for e in report['applied'] if e['op'] == 'resize'], findings)
                except (KeyError, TypeError, ValueError, IndexError) as ex:
                    self.log.line(f'{name}: the recompiled spec could not be read ({type(ex).__name__}: {ex})')
                    wrong = []
                for d in wrong:
                    self.log.line(f"resize dropped (finding {d['finding_id']}): {d['reason']}")
                note(wrong)
                if wrong:
                    gone = {d['finding_id'] for d in wrong}
                    new, report = edits.apply_edits(winner.design, [e for e in report['applied'] if e['finding_id'] not in gone],
                                                    log=self.log.line, view=view)
                    skipped = skipped + wrong
                    if not report['applied']:
                        continue
            report['applied'] = [recorded.get(e['finding_id'], e) for e in report['applied']]       # the measured values are recorded
            key = edits.design_hash(new)
            if key in self.tried:
                note({'finding_id': e['finding_id'], 'reason': 'the design with this edit was already a candidate or proposed before'}
                     for e in report['applied'])
                continue
            self.tried.add(key)
            made.append({'design': new, 'report': report, 'skipped': skipped, 'variant': name})
            if len(made) == MAX_EDITED:
                break
        used = {e['finding_id'] for m in made for e in m['report']['applied']}
        element_of = {f.get('id'): f.get('element_id') for f in findings}
        rows = [{'finding_id': fid, 'element_id': element_of.get(fid), 'reason': why} for fid, why in unedited.items() if fid not in used]
        for row in rows:
            self.log.line(f"skipped (finding {row['finding_id']}): {row['reason']}")
        if rdir is not None:
            jwrite(Path(rdir) / 'fixer_skipped.json', rows)
        return made


def finalize_best(out, best, best_round, design_init):
    """best/ holds what `boothctl.py build --spec best/spec.json` needs: the whole compile folder of the best candidate."""
    if best is None and design_init is None:
        return
    d = out / 'best'
    d.mkdir(parents=True, exist_ok=True)
    if best is None:
        jwrite(d / 'design.json', design_init)
        jwrite(d / 'best.json', {'candidate_id': None, 'round': None, 'total': None, 'scored': False})
        return
    if best.compile_dir is not None:
        shutil.copytree(best.compile_dir, d, dirs_exist_ok=True)          # spec.json, source.<ext>, compile_report.json, ...
    elif best.preview and (best.preview / 'spec.json').is_file():          # the stand-in has no compile folder
        shutil.copyfile(best.preview / 'spec.json', d / 'spec.json')
    shutil.copyfile(best.dir / 'design.json', d / 'design.json')
    shutil.copyfile(best.dir / 'score.json', d / 'score.json')
    if best.preview and (best.preview / 'preview_render.png').is_file():
        shutil.copyfile(best.preview / 'preview_render.png', d / 'preview_render.png')
    if (best.dir / 'sheet.png').is_file():
        shutil.copyfile(best.dir / 'sheet.png', d / 'sheet.png')
    jwrite(d / 'best.json', {'candidate_id': best.id, 'round': best_round, 'total': best.total, 'scored': True,
                             'from': f'round_{best_round}/candidates/{best.id}'})


def check_picture_and_fit(cfg):
    """Operator mistakes in --picture and --fit are InputError (exit code 2), never a traceback."""
    fit = jread(cfg.fit)
    if not isinstance(fit, dict):
        raise InputError(f'{cfg.fit}: the fit must be a JSON object (a box_fit.json)')
    if fit.get('status') != 'fitted':
        raise InputError(f"{cfg.fit}: the corner fit must have status 'fitted', not {fit.get('status')!r}")
    dims = fit.get('dims_m')
    if not isinstance(dims, dict) or not all(_is_num(dims.get(k)) and dims[k] > 0 for k in 'WD'):
        raise InputError(f'{cfg.fit}: dims_m must be an object with positive numbers W and D')
    cam = fit.get('camera')
    try:
        if not isinstance(cam, dict):
            raise ValueError('the fit has no camera object')
        for key in ('lens_mm', 'sensor_width_mm'):
            if not (_is_num(cam.get(key)) and cam[key] > 0):
                raise ValueError(f'camera.{key} must be a number above 0')
        for key in ('position', 'target'):
            if not (isinstance(cam.get(key), list) and len(cam[key]) == 3 and all(_is_num(v) for v in cam[key])):
                raise ValueError(f'camera.{key} must be three numbers')
        pp = cam.get('principal_point', [0.5, 0.5])
        if not (isinstance(pp, list) and len(pp) == 2 and all(_is_num(v) for v in pp)):
            raise ValueError('camera.principal_point must be two numbers')
        if 'roll_deg' in cam and not _is_num(cam['roll_deg']):
            raise ValueError('camera.roll_deg must be a number')
        camera_math.project(cam['target'], cam, 100, 100)            # the point the camera looks at must project
    except (KeyError, TypeError, ValueError, IndexError, ZeroDivisionError, OverflowError) as ex:
        raise InputError(f'{cfg.fit}: the camera is unusable ({type(ex).__name__}: {ex})')
    img = fit.get('image')
    if not isinstance(img, dict) or not all(_is_int(img.get(k)) and img[k] > 0 for k in ('width', 'height')):
        raise InputError(f'{cfg.fit}: image must be an object with whole numbers width and height')
    try:
        with Image.open(cfg.picture) as im:
            im.load()
            size = im.size
    except FileNotFoundError:
        raise InputError(f'FILE_MISSING: {cfg.picture}')
    except Exception as ex:                             # not an image, truncated, too large ...
        raise InputError(f'{cfg.picture} is not a readable picture ({type(ex).__name__}: {ex})')
    if list(size) != [img['width'], img['height']]:
        raise InputError(f"--picture is {size[0]}x{size[1]} but the fit is for a {img['width']}x{img['height']} picture")
    if not cfg.stub and not Path(cfg.truth).is_dir():
        raise InputError(f'--truth must be an answer-sheet folder: {cfg.truth}')


def run(cfg, clock=time.monotonic, roles=None, renderer=None, scorer=None):
    """Run the loop. -> the summary written to loop.json. Raises InputError (nothing written) for unusable input."""
    start = clock()
    cfg = copy.copy(cfg)
    cfg.picture, cfg.fit, cfg.truth, cfg.out = (Path(p) for p in (cfg.picture, cfg.fit, cfg.truth, cfg.out))
    cfg.design_init = None if cfg.design_init is None else Path(cfg.design_init)
    for name, value, kind in (('--max-minutes', cfg.max_minutes, float), ('--response-timeout', cfg.response_timeout, float),
                              ('--token-wait', cfg.token_wait, float)):
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
            raise InputError(f'{name} must be a finite number >= 0, not {value!r}')
    if not isinstance(cfg.max_rounds, int) or isinstance(cfg.max_rounds, bool) or cfg.max_rounds < 0:
        raise InputError(f'--max-rounds must be a whole number >= 0, not {cfg.max_rounds!r}')
    pid = cfg.picture_id or cfg.truth.name or 'pic'
    if not rolemod.name_ok(pid):
        raise InputError(f'the picture id {pid!r} must be letters, digits, _ . - (at most {rolemod.NAME_MAX}); give --picture-id')
    check_picture_and_fit(cfg)
    fit = jread(cfg.fit)
    design_init = None
    if cfg.design_init is not None:
        design_init = jread(cfg.design_init)
        problems = edits.validate_design(design_init)
        if problems:
            raise InputError(f'the initial design is not a valid booth-design.v1: {problems[0]}')
    if not cfg.picture.is_file():
        raise InputError(f'FILE_MISSING: {cfg.picture}')
    cfg.catalog = None if cfg.catalog is None else Path(cfg.catalog)
    try:
        catalog_files = catmod.reader_files(cfg.catalog)
    except (catmod.CatalogError, OSError) as ex:
        raise InputError(f'--catalog: {ex}')
    if cfg.out.exists() and (not cfg.out.is_dir() or any(cfg.out.iterdir())):
        raise InputError(f'the output folder is not empty: {cfg.out} (a run is never repeated into the same folder)')
    if not (roles and renderer and scorer):
        parts = stub_parts(cfg) if cfg.stub else real_parts(cfg)
        roles, renderer, scorer = roles or parts['roles'], renderer or parts['renderer'], scorer or parts['scorer']
    cfg.out.mkdir(parents=True, exist_ok=True)
    return Loop(cfg, clock, start, roles, renderer, scorer, fit, design_init, catalog_files).run()


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--picture', required=True, help='the original picture (png or jpg)')
    ap.add_argument('--fit', required=True, help='box_fit.json (status fitted) made on the same picture')
    ap.add_argument('--truth', required=True, help='answer-sheet folder (passed to score.py)')
    ap.add_argument('--design-init', required=True,
                    help="the starting design.json (booth-design.v1), or 'none' (real mode) to let the reader make the first design")
    ap.add_argument('--out', required=True, help='output folder, e.g. output/poc/loop/pic_A; must be empty or absent')
    ap.add_argument('--map', help='inventory-id to component-id map for score.py (passed through unchanged)')
    ap.add_argument('--max-rounds', type=int, default=4)
    ap.add_argument('--max-minutes', type=float, default=10.0, help='finite and >= 0; 0 stops before the first round')
    ap.add_argument('--no-plateau', action='store_true', help='do not stop after two rounds without a better score (the round limit and the time limit still apply)')
    ap.add_argument('--stub', action='store_true', help='use the stand-ins: no model, no Blender')
    ap.add_argument('--picture-id', help='default: the name of the --truth folder')
    ap.add_argument('--blender', help='Blender executable for preview (default: boothctl finds it)')
    ap.add_argument('--response-timeout', type=float, default=900.0, help='seconds to wait for each role response file (real mode)')
    ap.add_argument('--token-wait', type=float, default=600.0, help='seconds to wait for the Blender token (real mode)')
    ap.add_argument('--stub-target', help='stub mode: a design.json the stand-in critic and scorer compare with (default: derived)')
    ap.add_argument('--catalog', help='family catalogue folder written by catalog.py (catalog.json, designs/, thumbnails) for the '
                                      "reader's folder; default: the text catalogue built from the schema")
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    init = None if a.design_init.lower() == 'none' else Path(a.design_init)
    cfg = Config(picture=Path(a.picture), fit=Path(a.fit), truth=Path(a.truth), design_init=init, out=Path(a.out),
                 map=Path(a.map) if a.map else None, max_rounds=a.max_rounds, max_minutes=a.max_minutes, stub=a.stub,
                 picture_id=a.picture_id, blender=a.blender, response_timeout=a.response_timeout, token_wait=a.token_wait,
                 stub_target=Path(a.stub_target) if a.stub_target else None, plateau=not a.no_plateau,
                 catalog=Path(a.catalog) if a.catalog else None)
    try:
        summary = run(cfg)
    except (InputError, OSError) as ex:
        print(f'orchestrator: {ex}', file=sys.stderr)
        return 2
    print(json.dumps({k: summary[k] for k in ('rounds', 'stop_reason', 'best')}, indent=2))
    return 3 if summary['stop_reason'] == 'reader_invalid' else 0


if __name__ == '__main__':
    sys.exit(main())
