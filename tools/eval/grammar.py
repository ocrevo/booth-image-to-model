"""Booth grammar for the harness (iteration I2): open sides, booth types and cutaways, in the words the skill uses. Standard library only.

Vocabulary (the same words as the scene description and the corner tokens):

  sides            front (-Y), back (+Y), left (-X), right (+X) of the footprint, in the booth frame of the corner tokens
  open sides       the sides of the footprint with no wall, where visitors come in
  booth type       from the number of open sides: inline = 1, corner = 2 adjacent, peninsula = 3, island = 4,
                   other = anything else (2 opposite sides, none, irregular)
  cutaway          a side whose wall (or the roof) is only left out in the picture to show the inside; it is not an opening

The harness keeps its own copy of the rules (it never imports the skill, so a bug in the skill's rules cannot hide itself): `classify`
applies the type rule, `truth_topology` reads the truth of a generated case, `mirror_xyz` flips corner tokens, `spec_wall_sides` finds
which edge of the compiled footprint a wall stands on, and `topology_reasons` recognises the plain-English gate reasons that come from
the topology checks.
"""
from __future__ import annotations
import re

SIDES = ('front', 'back', 'left', 'right')
BOOTH_TYPES = ('inline', 'corner', 'peninsula', 'island', 'other')
ADJACENT = frozenset(frozenset(p) for p in (('front', 'left'), ('front', 'right'), ('back', 'left'), ('back', 'right')))

# The four checks of report.topology.checks (ids are the skill's contract) and their statuses.
CHECK_IDS = ('type_matches_sides', 'wall_on_open_side', 'closed_side_without_wall', 'camera_side')
CHECK_STATUSES = ('ok', 'warn', 'fail', 'not_checked')

# Fragments of the plain-English quality-gate reasons that come from the topology checks (matched on lower-case text). They are the
# words of the design, 'the booth type does not match the open sides', 'a wall was placed on an open side and left out' and 'the camera
# looks in through closed sides: front/back or left/right may be swapped'. None of them occurs in the reasons of the fit (see the test
# that scans the stored reasons of the I1 final run).
REASON_PATTERNS = (re.compile(r'booth type[^.;]*open sides?'), re.compile(r'wall[^.;]*on an open side'), re.compile(r'through closed sides'),
                   re.compile(r'(front/back|left/right)[^.;]*swapped'))


def sort_sides(sides):
    """The given sides in the canonical order (front, back, left, right), without repeats."""
    wanted = set(sides)
    return [s for s in SIDES if s in wanted]


def classify(open_sides):
    """Booth type of a set of open sides by the I2 rule: 1 inline, 2 adjacent corner, 3 peninsula, 4 island, else other."""
    sides = set(open_sides)
    if len(sides) == 1:
        return 'inline'
    if len(sides) == 2:
        return 'corner' if frozenset(sides) in ADJACENT else 'other'
    if len(sides) == 3:
        return 'peninsula'
    if len(sides) == 4:
        return 'island'
    return 'other'


def truth_topology(case):
    """-> {'booth_type', 'open_sides', 'cutaway_sides'} of a generated case, by the vocabulary above.

    The generator's own label (`case['booth_type']`) keeps its old name: its 'inline' scenes have a back wall only, which leaves three
    sides open, and by this vocabulary that is a peninsula. The type here comes from the open sides, never from the old label."""
    open_sides = sort_sides(case['open_sides'])
    return {'booth_type': classify(open_sides), 'open_sides': open_sides, 'cutaway_sides': sort_sides(case.get('cutaway_sides') or [])}


def flip_token(token):
    """The mirror image of one corner token on its axis: 'D/2' <-> '-D/2'; '0' (the middle) stays."""
    t = str(token).strip()
    if t in ('', '0', '0.0', '-0'):
        return t
    return t[1:] if t.startswith('-') else '-' + t


# Index in the xyz token triple: front/back is Y (1), left/right is X (0). A single flip is a reflection: no camera that turns (and does not
# mirror) can reproduce it, so the fit is left with a large residual. Both flips together are a half turn about the vertical axis: a box is
# unchanged by that, so the fit is perfect and puts the camera on the opposite side of the booth.
MIRROR_AXES = {'mirror_fb': (1,), 'mirror_lr': (0,), 'swap_fb_lr': (0, 1)}


def mirror_xyz(xyz, kind):
    """Corner tokens with every front/back (mirror_fb), left/right (mirror_lr) or both (swap_fb_lr) token swapped; the height token is untouched."""
    out = list(xyz)
    for axis in MIRROR_AXES[kind]:
        out[axis] = flip_token(out[axis])
    return out


def toggle_side(open_sides, side):
    """-> (new open sides, 'open_as_closed' | 'closed_as_open'): the declaration is wrong about `side`."""
    sides = set(open_sides)
    if side in sides:
        sides.discard(side)
        return sort_sides(sides), 'open_as_closed'
    sides.add(side)
    return sort_sides(sides), 'closed_as_open'


# ---------------------------------------------------------------------------------------------------------------- reading outputs
def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def spec_wall_sides(spec, wall_ids, near_m=0.5):
    """-> {wall id: side or None} for the walls of the scene that are present in the compiled spec (absent walls are not listed).

    A wall is a thin box that stands on one edge of the footprint: the thin direction says whether it is a front/back or a left/right wall
    (it spans the whole edge, so its ends touch the neighbouring edges too), and the outer face nearest to an edge of the floor box says which.
    None when there is no floor box to measure against or the wall is not within `near_m` of any edge."""
    comps = {c.get('id'): c for c in (spec or {}).get('components', []) if isinstance(c, dict)}
    floor = comps.get('floor')
    size = ((floor or {}).get('params') or {}).get('size')
    out = {}
    for wid in wall_ids:
        c = comps.get(wid)
        if c is None:
            continue
        loc, wsize = c.get('location'), (c.get('params') or {}).get('size')
        if not (floor and isinstance(size, list) and len(size) >= 2 and isinstance(loc, list) and isinstance(wsize, list) and len(wsize) >= 2
                and all(_num(v) for v in list(size[:2]) + list(loc[:2]) + list(wsize[:2]))):
            out[wid] = None
            continue
        rot = c.get('rotation_deg') or [0, 0, 0]
        sx, sy = wsize[0], wsize[1]
        if rot and _num(rot[2]) and round(abs(rot[2]) / 90) % 2 == 1:
            sx, sy = sy, sx
        W, D = size[0], size[1]
        fx, fy = (floor.get('location') or [0, 0, 0])[:2]
        x, y = loc[0] - fx, loc[1] - fy
        if sy <= sx:                                              # thin in Y: a front or back wall (it spans the width, so X says nothing)
            dist = {'back': abs(y + sy / 2 - D / 2), 'front': abs(y - sy / 2 + D / 2)}
        else:                                                     # thin in X: a left or right wall
            dist = {'right': abs(x + sx / 2 - W / 2), 'left': abs(x - sx / 2 + W / 2)}
        side = min(dist, key=dist.get)
        out[wid] = side if dist[side] <= near_m else None
    return out


def walls_on(spec, wall_ids, sides):
    """Number of the scene's walls that the compiled spec holds on one of `sides` (None when no spec was produced)."""
    if not spec or not isinstance(spec.get('components'), list):
        return None
    found = spec_wall_sides(spec, wall_ids)
    return sum(1 for side in found.values() if side in set(sides))


def topology_checks(report):
    """-> {check id: status} from report.topology.checks, or None when the report has no topology block (a skill without the feature)."""
    topo = (report or {}).get('topology') if isinstance(report, dict) else None
    if not isinstance(topo, dict):
        return None
    out = {}
    for check in topo.get('checks') or []:
        if isinstance(check, dict) and check.get('id') is not None:
            out[str(check['id'])] = check.get('status')
    return out


def topology_declared(report):
    """True when the skill's report shows a declared topology (booth type or open sides not null), False when it shows none, None without a block."""
    topo = (report or {}).get('topology') if isinstance(report, dict) else None
    if not isinstance(topo, dict):
        return None
    return topo.get('booth_type') is not None or topo.get('open_sides') is not None


def topology_reasons(reasons):
    """The quality-gate reasons (list of strings) that come from the topology checks."""
    return [r for r in (reasons or []) if isinstance(r, str) and any(p.search(r.lower()) for p in REASON_PATTERNS)]


# ---------------------------------------------------------------------------------------------------------------- what could be caught
SKILL_CAMERA_MARGIN_M = 0.25        # how far beyond a footprint edge the skill's camera_side check wants the camera (its CAMERA_MARGIN_M, when I2 was built)


def sides_beyond(x, y, width, depth, margin=SKILL_CAMERA_MARGIN_M):
    """The sides of a footprint centred on the origin (X right, Y back) that the point (x, y) lies beyond by more than `margin`."""
    return {side for side, out in (('front', y < -depth / 2 - margin), ('back', y > depth / 2 + margin),
                                   ('left', x < -width / 2 - margin), ('right', x > width / 2 + margin)) if out}


def best_case_catch(case, topo, margin=SKILL_CAMERA_MARGIN_M):
    """Could the topology checks catch this run at all, in the best case: a perfect fit and the true camera? -> True, False, or None.

    The ceiling that the camera and wall checks cannot beat, whatever the skill does. `topo` is the 'topology' block of perturb.apply.
      swap_fb_lr        the fit puts the camera half way round the booth, (x, y) -> (-x, -y); the camera check fails when it is then outside
                        closed sides only
      wrong_open_sides  closed_as_open: the scene has a wall on that side (every closed side of the generator has one), so the wall check
                        fails; open_as_closed: only the camera check can, when the true camera is outside closed sides only
      mirror_fb/_lr     None: a reflection leaves the fit with a large residual, there is no best-case camera
      right declaration False (nothing is wrong)."""
    kind = topo['kind']
    if kind is None:
        return False
    if kind in ('mirror_fb', 'mirror_lr'):
        return None
    x, y = case['camera']['position'][:2]
    W, D = case['dims_m']['W'], case['dims_m']['D']
    seen = set(topo['declared']['open_sides']) | set(topo['declared']['cutaway_sides'])
    if kind == 'swap_fb_lr':
        beyond = sides_beyond(-x, -y, W, D, margin)
    else:
        if topo['detail'] == 'closed_as_open' and topo['toggled_side'] in [w['plane'] for w in case['walls']]:
            return True
        beyond = sides_beyond(x, y, W, D, margin)
    return bool(beyond) and not (beyond & seen)
