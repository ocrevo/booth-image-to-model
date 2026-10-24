"""Stand-ins for the three model roles, the renderer and the scorer: the loop runs with no model and no Blender (--stub).

The stand-in world has a hidden target design (what the "picture" shows). The stub reader returns the initial design as it is; the
stub critic compares the candidate it is shown with the target and writes critic.v1 findings; the stub scorer scores a candidate by
its distance to the target; the stub judge compares two candidates by their score.json totals. All of it is deterministic.

    target = make_target(design_init)          # a design that differs from design_init in a few places
    roles, renderer, scorer = StubRoles(target), StubRenderer(picture, fit), StubScorer(target)
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path

from PIL import Image

try:
    from . import edits
except ImportError:                  # run as a script: tools/poc/loop is sys.path[0]
    import edits

TOLERANCE_M = 0.01               # the critic reports a misplaced element from a centimetre on
SHIFT_M = 0.3                   # how far make_target moves elements by default; tests also use 0.04
MISSING_COST = 3.0
COLOUR_THRESHOLD = 20.0


def _hex_rgb(h):
    return [int(h[i:i + 2], 16) for i in (1, 3, 5)]


def make_target(design, shift_m=SHIFT_M):
    """A schema-valid design that differs from `design` by moves of up to three elements and one count (the hidden "truth")."""
    target = copy.deepcopy(design)
    moved = counted = 0
    for el in design['elements']:
        if moved < 3 and (el.get('side') in edits.SIDES or el.get('centre_m')):
            horizontal = el.get('side') in ('front', 'back') or not el.get('side') or el.get('side') == 'centre'
            for sign in (1.0, -1.0):
                delta = [sign * shift_m, 0.0, 0.0] if horizontal else [0.0, sign * shift_m, 0.0]
                try:
                    target = edits.apply_edit(target, edits.make_edit('move', el['id'], delta_m=delta, finding_id='target'))
                except edits.EditError:
                    continue
                moved += 1
                break
        if counted < 1 and el.get('count'):
            target = edits.apply_edit(target, edits.make_edit('set_count', el['id'], delta_m=[1, 0, 0], finding_id='target'))
            counted += 1
    return target


def _by_id(design):
    return {e['id']: e for e in design['elements']}


def _colour(design, el):
    c = edits.current_colour(design, el)
    return _hex_rgb(c) if c else None


def design_distance(a, b):
    """A non-negative number that is 0 for equal designs: position (m), count, colour and presence of every element."""
    ea, eb = _by_id(a), _by_id(b)
    total = MISSING_COST * len(set(ea) ^ set(eb))
    for eid in set(ea) & set(eb):
        x, y = edits.element_centre(a, ea[eid]), edits.element_centre(b, eb[eid])
        total += math.dist(x, y)
        total += 0.5 * abs(int(ea[eid].get('count') or 1) - int(eb[eid].get('count') or 1))
        ca, cb = _colour(a, ea[eid]), _colour(b, eb[eid])
        if ca and cb:
            total += sum(abs(p - q) for p, q in zip(ca, cb)) / 255.0
    return total


class StubRoles:
    """The reader, critic and judge stand-ins. `target` is the hidden design the critic compares with."""

    def __init__(self, target, camera=None, size=None):
        self.target, self.camera, self.size = target, camera, size      # size: (width, height) of the picture, for the boxes

    def read(self, ctx):
        return {'schema_version': 'reader.v1', 'picture_id': ctx['picture_id'], 'design': copy.deepcopy(ctx['design_init']),
                'evidence': {}, 'unexpressible': []}

    def target_box(self, eid):
        """Where the element really is in the picture (the box the stand-in "picture" has for it), in picture pixels, or None."""
        if self.camera is None or self.size is None:
            return None
        comps = [c for c in stub_components(self.target) if c['id'] == eid or c['id'].startswith(eid + '_')]
        if not comps:
            return None
        uv = []
        for pt in (pt for comp in comps for pt in edits.component_points(comp)):
            try:
                uv.append(edits.camera_math.project(pt, self.camera, self.size[0], self.size[1])[:2])
            except ValueError:
                continue
        if not uv:
            return None
        box = [max(0.0, min(u for u, _ in uv)), max(0.0, min(v for _, v in uv)),
               min(float(self.size[0]), max(u for u, _ in uv)), min(float(self.size[1]), max(v for _, v in uv))]
        return [round(v, 2) for v in box] if box[2] > box[0] and box[3] > box[1] else None

    def critic(self, ctx):
        design, target = ctx['design'], self.target
        have, want = _by_id(design), _by_id(target)
        findings = []

        def add(eid, typ, severity, direction):
            findings.append({'id': f'f{len(findings) + 1}', 'element_id': eid, 'type': typ, 'severity': severity,
                             'direction': direction, 'bbox_px': self.target_box(eid) if typ in edits.GEOMETRY_TYPES else None})

        for eid in sorted(set(have) | set(want)):
            if eid not in have:
                add(eid, 'missing', 'high', None)
                continue
            if eid not in want:
                add(eid, 'extra', 'high', None)
                continue
            (x0, y0, _), (x1, y1, _) = edits.element_centre(design, have[eid]), edits.element_centre(target, want[eid])
            dx, dy = x1 - x0, y1 - y0
            miss = math.hypot(dx, dy)
            if miss > TOLERANCE_M:
                direction = edits.direction_of(dx, dy, self.camera)      # picture directions, as a person would say them
                add(eid, 'misplaced', 'high' if miss > 0.8 else 'medium' if miss > 0.4 else 'low', direction)
            dc = int(want[eid].get('count') or 1) - int(have[eid].get('count') or 1)
            if dc:
                add(eid, 'wrong_count', 'medium', 'bigger' if dc > 0 else 'smaller')
            ca, cb = _colour(design, have[eid]), _colour(target, want[eid])
            if ca and cb and sum(abs(p - q) for p, q in zip(ca, cb)) / 3 > COLOUR_THRESHOLD:
                add(eid, 'wrong_colour', 'low', 'lighter' if sum(cb) > sum(ca) else 'darker')
        return {'schema_version': 'critic.v1', 'round': ctx['round'], 'candidate_id': ctx['candidate_id'], 'findings': findings,
                'verdict': 'fix' if findings else 'accept'}

    def judge(self, ctx):
        """One judge.v1 document for one pass: the higher total wins; on a tie the unchanged candidate (first in order) wins."""
        scores, order = ctx['scores'], ctx['order']
        a, b = ctx['key']['A'], ctx['key']['B']
        if scores[a] != scores[b]:
            better = 'A' if scores[a] > scores[b] else 'B'
        else:
            better = 'A' if order.index(a) < order.index(b) else 'B'
        return {'schema_version': 'judge.v1', 'pair_id': ctx['pair_id'], 'judge_id': ctx['request']['judge_id'], 'pass': ctx['pass'], 'better': better,
                'scores': None, 'items': {}, 'extra_parts': None, 'confidence': 'low'}


class StubScorer:
    """score.v1 from the distance to the target: total = 100 / (1 + distance); the edge distance shrinks with the distance too."""

    def __init__(self, target, picture_id='pic_stub'):
        self.target, self.picture_id = target, picture_id

    def score(self, cand_dir, preview_dir, candidate_id, design):
        d = design_distance(design, self.target)
        total = round(100.0 / (1.0 + d), 2)
        # The stand-in puts the whole total on L1 (panel); the real score.py fills the levels from the code and the panel.
        return {'schema_version': 'score.v1', 'picture_id': self.picture_id, 'candidate_id': candidate_id, 'spec_sha256': '0' * 64,
                'levels': {'L1': {'panel': total}, 'L2': {'code': None, 'panel': None}, 'L3': {'panel': None},
                           'L4': {'code': None, 'panel': None}},
                'total': total,
                'aux': {'edge_distance_diag': round(d / 10.0, 5), 'edge_distance_px': round(d * 10.0, 3), 'holdout_median_diag': None,
                        'colour_de_mean': None, 'booth_pixel_fraction': 0.5, 'second_view_ok': None},
                'status': 'measured', 'reason': None}


_DEFAULT_SIZE = {'feature_wall': (3.0, 0.1, 2.5), 'graphic_panel': (1.5, 0.05, 1.0), 'tv': (1.3, 0.05, 0.8), 'led_wall': (2.0, 0.1, 1.5),
                 'wall': (2.0, 0.1, 2.5), 'header_band': (3.0, 0.4, 0.5), 'counter': (1.5, 0.6, 1.0), 'seating_group': (1.5, 1.0, 0.8),
                 'planter': (0.6, 0.6, 0.8)}


def stub_components(design):
    """One box per element: where the stand-in world puts it (also the spec the stand-in render stands for)."""
    components = []
    for el in design['elements']:
        x, y, z0 = edits.element_centre(design, el)
        dims = _DEFAULT_SIZE.get(el['kind'], (0.8, 0.8, 0.8))
        given = el.get('size_m') or [None, None, None]
        sx, sy, sz = (float(g) if g else dims[i] for i, g in enumerate(given))
        n = max(1, int(el.get('count') or 1))
        for i in range(n):              # a count of n is n boxes side by side (so that a count edit changes what is built)
            dx = (i - (n - 1) / 2) * (sx + 0.2)
            components.append({'id': el['id'] if n == 1 else f"{el['id']}_{i}", 'kind': 'box',
                               'location': [round(x + dx, 4), round(y, 4), round(z0 + sz / 2, 4)],
                               'rotation_deg': [0.0, 0.0, 0.0], 'params': {'size': [sx, sy, sz]}})
    return components


class StubRenderer:
    """No Blender: the "render" is the picture copied as RGBA, and spec.json is one box per element (for the picture crops)."""

    def __init__(self, picture, fit):
        self.picture, self.fit = Path(picture), fit

    def compile_design(self, design):
        """The stand-in for design-compile: the spec of a design without a render (used to check edits before they are proposed)."""
        with Image.open(self.picture) as im:
            size = im.size
        return {'schema_version': 'stub-spec', 'source': {'width': size[0], 'height': size[1]}, 'camera': self.fit['camera'],
                'components': stub_components(design)}

    def render(self, cand_dir, design, candidate_id, time_left_s=None):
        cand_dir = Path(cand_dir)
        out = cand_dir / 'preview'
        out.mkdir(parents=True, exist_ok=True)
        with Image.open(self.picture) as im:
            rgba = im.convert('RGBA')
            size = rgba.size
            rgba.save(out / 'preview_render.png')
        spec = {'schema_version': 'stub-spec', 'source': {'width': size[0], 'height': size[1]}, 'camera': self.fit['camera'],
                'components': stub_components(design)}
        (out / 'spec.json').write_bytes((json.dumps(spec, indent=2) + '\n').encode('utf-8'))
        return out
