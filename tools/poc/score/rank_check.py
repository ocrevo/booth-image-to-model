"""Anchor one, part two: do the score card and the edge distance rank the broken variants in the expected order?

    python tools/poc/score/rank_check.py --scores <dir> --expected <file> --out <rank_check.json>

<dir> holds one folder per variant (B1 .. B6), each with the score.json that score.py wrote for that variant's preview. --expected is
expected_order.json (a list of variant names, best to worst) or docs/poc/templates/anchor1-expected.json (an object whose
expected_order_best_to_worst is that list). Only score.json files with status "measured" count, and its spec_sha256 must equal the SHA-256 of that folder's spec.json (no stale scores); a score card with no panel (code points
only) must never be correlated, because the panel part of the total is what the broken variants are supposed to move. If any variant is
missing, not measured, or has no number to compare, the check stops with exit code 2, names the variant on stderr and writes
rank_check.json with status "blocked" and the list of blocked variants (no coefficient).

Otherwise it writes rank_check.json (rank_check.v1): the Spearman coefficient of the expected order with the total and with the edge
distance (aux.edge_distance_diag, smaller is better, so it is compared with its sign flipped), whether each is >= 0.8, whether both are,
and the pairs that are the wrong way round (a variant that should be better has the worse value) for each measure. A computed result
exits 0 whether or not it passes: the verdict is in the file. See docs/poc/03-scoring-spec.md section 5.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

THRESHOLD = 0.8
SCHEMA = 'rank_check.v1'
ORDER_KEYS = ('expected_order_best_to_worst', 'expected_order')


class InputError(ValueError):
    """Unusable input: reported on stderr, exit code 2."""


def _ranks(values):
    """1-based ranks, ties share the average of the places they cover."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(xs, ys):
    """Spearman rank correlation (Pearson correlation of the average ranks); None when one side is constant."""
    if len(xs) != len(ys) or len(xs) < 2:
        raise ValueError('two sequences of the same length (at least 2) are needed')
    rx, ry = _ranks(list(xs)), _ranks(list(ys))
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    if sxx == 0 or syy == 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(rx, ry)) / math.sqrt(sxx * syy)


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise InputError(f'FILE_MISSING: {path}')
    except (OSError, ValueError) as e:
        raise InputError(f'BAD_JSON: {path}: {e}')


def read_expected(path):
    doc = _read(path)
    if isinstance(doc, dict):
        key = next((k for k in ORDER_KEYS if k in doc), None)
        if key is None:
            raise InputError(f'BAD_EXPECTED: {path}: expected a list, or an object with {ORDER_KEYS[0]}')
        doc = doc[key]
    if not (isinstance(doc, list) and len(doc) >= 3 and all(isinstance(v, str) and v for v in doc) and len(set(doc)) == len(doc)):
        raise InputError(f'BAD_EXPECTED: {path}: at least 3 different variant names, best first')
    return doc


def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def spec_hashes(path):
    """SHA-256 of a spec.json as it is, and as it is after the preview copied it.

    score.py hashes the spec.json that sits in the preview folder, which blender_build.py rewrites through common.write_json in text
    mode: on Windows its line ends become CRLF, while perturb_spec.py writes LF. The same JSON therefore has two byte forms, and a
    score is the variant's if it carries either hash. A spec that differs in content matches neither.
    """
    raw = Path(path).read_bytes()
    lf = raw.replace(b'\r\n', b'\n')
    crlf = lf.replace(b'\n', b'\r\n')
    return {hashlib.sha256(b).hexdigest() for b in (raw, lf, crlf)}


def read_scores(folder, order):
    """({variant: (total, edge_distance_diag)}, [blocked entries]) for the variants in `order`."""
    values, blocked = {}, []
    for v in order:
        path = Path(folder) / v / 'score.json'
        if not path.is_file():
            blocked.append({'variant': v, 'reason': f'score.json missing ({path})'})
            continue
        try:
            doc = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as e:
            blocked.append({'variant': v, 'reason': f'score.json unreadable: {e}'})
            continue
        if not isinstance(doc, dict) or doc.get('status') != 'measured':
            status = doc.get('status') if isinstance(doc, dict) else None
            why = doc.get('reason') if isinstance(doc, dict) else None
            blocked.append({'variant': v, 'reason': f'status is {status!r}, not measured' + (f' ({why})' if why else '')})
            continue
        spec_file = Path(folder) / v / 'spec.json'
        if not spec_file.is_file():
            blocked.append({'variant': v, 'reason': f'spec.json missing ({spec_file}), so the score cannot be tied to this variant'})
            continue
        if doc.get('spec_sha256') not in spec_hashes(spec_file):
            blocked.append({'variant': v, 'reason': 'spec_sha256 in score.json does not match spec.json (an old score of another spec?)'})
            continue
        total = doc.get('total')
        edge = (doc.get('aux') or {}).get('edge_distance_diag') if isinstance(doc.get('aux'), dict) else None
        if not _number(total):
            blocked.append({'variant': v, 'reason': 'total is not a number'})
        elif not _number(edge):
            blocked.append({'variant': v, 'reason': 'aux.edge_distance_diag is not a number'})
        else:
            values[v] = (total, edge)
    return values, blocked


def _inverted(order, vals, measure, worse_is_larger):
    """Pairs (i before j in the expected order) whose values put i strictly behind j; and the tied pairs."""
    wrong, tied = [], []
    for a in range(len(order)):
        for b in range(a + 1, len(order)):
            x, y = vals[order[a]], vals[order[b]]
            if x == y:
                tied.append({'measure': measure, 'a': order[a], 'b': order[b], 'value': x})
            elif (x > y) == worse_is_larger:
                wrong.append({'measure': measure, 'should_be_better': order[a], 'should_be_worse': order[b],
                              'should_be_better_value': x, 'should_be_worse_value': y})
    return wrong, tied


def check(scores_dir, expected_path):
    """The rank_check.v1 document; raises InputError for an unusable --expected file."""
    order = read_expected(expected_path)
    values, blocked = read_scores(scores_dir, order)
    if blocked:
        return {'schema_version': SCHEMA, 'status': 'blocked', 'expected_order': order, 'blocked': blocked}
    position = list(range(1, len(order) + 1))                         # 1 = best; the worse the variant, the larger
    totals = {v: values[v][0] for v in order}
    edges = {v: values[v][1] for v in order}
    rho_total = spearman(position, [-totals[v] for v in order])        # a higher total is better: flip the sign
    rho_edge = spearman(position, [edges[v] for v in order])           # a smaller edge distance is better
    pass_total = rho_total is not None and rho_total >= THRESHOLD
    pass_edge = rho_edge is not None and rho_edge >= THRESHOLD
    wrong_t, tied_t = _inverted(order, totals, 'total', worse_is_larger=False)
    wrong_e, tied_e = _inverted(order, edges, 'edge_distance_diag', worse_is_larger=True)
    return {'schema_version': SCHEMA, 'status': 'measured', 'expected_order': order, 'threshold': THRESHOLD,
            'variants': {v: {'total': totals[v], 'edge_distance_diag': edges[v]} for v in order},
            'spearman_total': rho_total, 'spearman_edge': rho_edge,
            'pass_total': pass_total, 'pass_edge': pass_edge, 'pass': pass_total and pass_edge,
            'inverted_pairs': wrong_t + wrong_e, 'tied_pairs': tied_t + tied_e}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--scores', required=True, help='folder with one sub-folder per variant (B1 .. B6), each holding score.json')
    ap.add_argument('--expected', required=True, help='expected_order.json (a list) or docs/poc/templates/anchor1-expected.json')
    ap.add_argument('--out', required=True, help='rank_check.json to write')
    args = ap.parse_args(argv)
    try:
        result = check(args.scores, args.expected)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
            f.write('\n')
    except (InputError, OSError) as e:
        print('ERROR: ' + (str(e) if isinstance(e, InputError) else f'{type(e).__name__}: {e}'), file=sys.stderr)
        return 2
    if result['status'] == 'blocked':
        print('ERROR: not every variant is measured: ' + '; '.join(f"{b['variant']}: {b['reason']}" for b in result['blocked']),
              file=sys.stderr)
        return 2
    print(json.dumps({k: result[k] for k in ('spearman_total', 'spearman_edge', 'pass')}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
