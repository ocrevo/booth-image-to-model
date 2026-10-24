"""Judge panel files for the POC: blind the candidates, write the judges' request files, collect and tally their judge.v1 answers.

    python tools/poc/score/panel.py prepare --candidates <dir> --truth <truth_dir> --out <panel_dir> --seed <n> [--pairwise]
    python tools/poc/score/panel.py tally --panel <panel_dir> --out <tally.json>
    python tools/poc/score/panel.py calibration-form --candidate <dir> --truth <truth_dir> --out <form.md>

prepare: <candidates> holds one folder per candidate (the folder name is the candidate id) with panel_view.png. Every picture is copied to
a random 8-hex name. Rating mode writes, for each candidate, three judges (j1, j2, j3) x two passes (the second lists the parts in
reverse order) under <panel_dir>/requests/<judge>/<pair_id>_p<pass>/ : request.json and the picture only. --pairwise instead writes,
for every pair of candidates, a random A/B and two passes (the second with A and B swapped) for one referee (j1). The real names are in
<panel_dir>/mapping.secret.json, which judges never get. Judges write their answers to <panel_dir>/responses/<judge>/<request_id>.json.
Blinding details: the requests are written in a seeded random order and every file and folder under requests/ gets one modification time;
pictures are re-encoded from their pixels, so no PNG text or EXIF chunk reaches a judge; each candidate folder needs a score.json whose
picture_id equals the one in the answer sheet's inventory.json (otherwise exit 2 naming the candidate, nothing written).
tally: reads every *.json under <panel_dir>/responses (rating mode), assigns each answer to its candidate through pair_id and writes
tally.v1 (docs/poc/02-architecture-and-contracts.md section 4.10). The majorities and the four panel scores come from
score.aggregate_panel (one implementation); an answer that is not strict judge.v1, or that gives L2:<id> for a part it did not judge
present, is not counted (so that judge lacks a pass and is undecided as a whole for that candidate). Warnings go to stderr.
calibration-form: a Markdown sheet with empty boxes for the decision maker to rate one candidate the way the judges do.
Exit code 0 on success, 2 for unusable input (the message starts with ERROR).
"""
from __future__ import annotations
import argparse
import itertools
import io
import json
import os
import random
import sys
from pathlib import Path

from PIL import Image

try:
    from . import score as scoring
except ImportError:                  # run as a script: tools/poc/score is sys.path[0]
    import score as scoring

InputError = scoring.InputError
JUDGES = ('j1', 'j2', 'j3')
VIEW = 'panel_view.png'
MAPPING = 'mapping.secret.json'
SAME_TIME = 946684800                 # every file and folder under requests/ gets this modification time


# ---- helpers -------------------------------------------------------------------------------------------------------

def _inventory(truth):
    result = scoring.validate_truth(truth)
    if not result['valid']:
        raise InputError('BAD_TRUTH: ' + '; '.join(result['errors'][:5]))
    doc = scoring.read_json(Path(truth) / 'inventory.json')
    return [{'id': i['id'], 'name': i['name']} for i in doc['items']], doc['picture_id']


def _hex_ids(rng, count, taken=()):
    out, seen = [], set(taken)
    while len(out) < count:
        h = f'{rng.getrandbits(32):08x}'
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


def _clean_png(path):
    """The picture re-encoded from its pixels only: no text chunk, EXIF, colour profile or any other metadata survives."""
    try:
        with Image.open(path) as im:
            im.load()
            if im.mode not in ('RGB', 'RGBA', 'L', 'LA'):
                im = im.convert('RGBA' if 'transparency' in im.info or 'A' in im.getbands() else 'RGB')
            clean = Image.frombytes(im.mode, im.size, im.tobytes())
    except (OSError, ValueError) as e:
        raise InputError(f'BAD_IMAGE: {path}: {e}')
    buf = io.BytesIO()
    clean.save(buf, format='PNG')
    return buf.getvalue()


def _check_pictures(cdir, names, picture_id):
    """One panel is for one picture: every candidate's score.json must name the picture of the answer sheet."""
    for n in names:
        path = cdir / n / 'score.json'
        if not path.is_file():
            raise InputError(f'FILE_MISSING: candidate {n} has no score.json ({path}); its picture cannot be checked')
        try:
            doc = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as e:
            raise InputError(f'BAD_JSON: candidate {n}: {path}: {e}')
        got = doc.get('picture_id') if isinstance(doc, dict) else None
        if got != picture_id:
            raise InputError(f'PICTURE_MISMATCH: candidate {n} is for picture {got!r}, the answer sheet is for {picture_id!r}; '
                             'one panel is for one picture')


def _write_request(folder, picture_copies, body):
    folder.mkdir(parents=True, exist_ok=True)
    for data, name in picture_copies:
        (folder / name).write_bytes(data)
    scoring.write_json(folder / 'request.json', body)


def _same_time(root):
    """Give every file and folder under root (root included) one modification time, so that the order in which the requests were
    written, which would follow the real names, cannot be read from the file system."""
    paths = sorted((p for p in Path(root).rglob('*')), key=lambda p: len(p.parts), reverse=True) + [Path(root)]
    for p in paths:
        os.utime(p, (SAME_TIME, SAME_TIME))


# ---- prepare -------------------------------------------------------------------------------------------------------

def prepare(candidates, truth, out, seed, pairwise=False):
    inventory, picture_id = _inventory(truth)
    cdir, out = Path(candidates), Path(out)
    if not cdir.is_dir():
        raise InputError(f'FILE_MISSING: candidates folder {cdir}')
    names = sorted(p.name for p in cdir.iterdir() if p.is_dir())
    if not names:
        raise InputError(f'NO_CANDIDATES: {cdir} holds no candidate folders')
    for n in names:
        if not (cdir / n / VIEW).is_file():
            raise InputError(f'FILE_MISSING: {cdir / n / VIEW}')
    _check_pictures(cdir, names, picture_id)
    pictures = {n: _clean_png(cdir / n / VIEW) for n in names}
    if pairwise and len(names) < 2:
        raise InputError('NO_PAIRS: --pairwise needs at least two candidates')
    if out.exists() and any(out.iterdir()):
        raise InputError(f'OUT_EXISTS: {out} is not empty; a panel is never overwritten')
    rng = random.Random(seed)
    blind = dict(zip(names, _hex_ids(rng, len(names))))
    mapping = {'schema_version': 'panel_mapping.v1', 'seed': seed, 'mode': 'compare' if pairwise else 'rate',
               'inventory_ids': [i['id'] for i in inventory],
               'candidates': {n: {'blind': blind[n]} for n in names}}
    req = out / 'requests'
    if pairwise:
        pairs = list(itertools.combinations(names, 2))
        ids = _hex_ids(rng, len(pairs), taken=blind.values())
        jobs = list(zip(ids, pairs))
        rng.shuffle(jobs)                      # written in a seeded random order, not in the order of the real names
        mapping['pairs'] = {}
        for pair_id, pair in jobs:
            first, second = list(pair)
            if rng.random() < 0.5:
                first, second = second, first
            mapping['pairs'][pair_id] = {'candidates': [first, second]}
            for p in (1, 2):
                a, b = (first, second) if p == 1 else (second, first)
                request_id = f'{pair_id}_p{p}'
                _write_request(req / 'j1' / request_id,
                               [(pictures[a], blind[a] + '.png'), (pictures[b], blind[b] + '.png')],
                               {'schema_version': 'judge_request.v1', 'mode': 'compare', 'judge_id': 'j1', 'request_id': request_id,
                                'pair_id': pair_id, 'pass': p, 'A': blind[a] + '.png', 'B': blind[b] + '.png',
                                'respond_to': f'responses/j1/{request_id}.json'})
    else:
        order = list(names)
        rng.shuffle(order)                     # written in a seeded random order, not in the order of the real names
        for n in order:
            for j in JUDGES:
                for p in (1, 2):
                    request_id = f'{blind[n]}_p{p}'
                    _write_request(req / j / request_id, [(pictures[n], blind[n] + '.png')],
                                   {'schema_version': 'judge_request.v1', 'mode': 'rate', 'judge_id': j, 'request_id': request_id,
                                    'pair_id': blind[n], 'pass': p, 'image': blind[n] + '.png',
                                    'items': inventory if p == 1 else inventory[::-1],
                                    'respond_to': f'responses/{j}/{request_id}.json'})
    _same_time(req)
    scoring.write_json(out / MAPPING, mapping)
    (out / 'responses').mkdir(exist_ok=True)
    return {'out': str(out), 'mode': mapping['mode'], 'candidates': len(names),
            'requests': sum(1 for _ in req.rglob('request.json'))}


# ---- tally ---------------------------------------------------------------------------------------------------------

def validate_response(doc, inventory_ids):
    """Problems of one scoring-mode answer: strict judge.v1 (scoring.validate_judge) plus L2:<id> only for a part judged present."""
    if not isinstance(doc, dict):
        return ['response: expected an object']
    problems = scoring.validate_judge(doc, set(inventory_ids))
    if not problems:
        for key in doc['items']:
            if key.startswith('L2:') and doc['items'].get('L1:' + key[3:]) != 'present':
                problems.append(f'items.{key}: L2 is only given for a part judged present (L1:{key[3:]} is not present)')
    return problems


def _stable_answers(passes):
    """({key: answer} where the two passes agree, number of keys seen) for one judge's valid answers of one candidate."""
    keys = set()
    for d in passes:
        keys |= set(d['items'])
    if sorted(d['pass'] for d in passes) != [1, 2]:
        return {}, len(keys)
    a, b = passes[0]['items'], passes[1]['items']
    return {k: a[k] for k in keys if k in a and k in b and a[k] == b[k]}, len(keys)


def tally(panel_dir, out):
    panel_dir = Path(panel_dir)
    mapping = scoring.read_json(panel_dir / MAPPING)
    if mapping.get('mode') != 'rate':
        raise InputError('NOT_RATING_MODE: tally handles rating panels only; this panel was prepared with --pairwise')
    inv_ids = list(mapping['inventory_ids'])
    folder = panel_dir / 'responses'
    if not any(folder.rglob('*.json')):
        raise InputError(f'NO_RESPONSES: no *.json file under {folder}')
    cand_of = {v['blind']: c for c, v in mapping['candidates'].items()}
    by_cand = {c: [] for c in mapping['candidates']}
    names = {c: [] for c in mapping['candidates']}
    for f in sorted(folder.rglob('*.json')):
        try:
            doc = json.loads(f.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            print(f'WARNING: {f.name}: not readable JSON, not counted', file=sys.stderr)
            continue
        cid = cand_of.get(doc.get('pair_id')) if isinstance(doc, dict) and isinstance(doc.get('pair_id'), str) else None
        if cid is None:
            print(f'WARNING: {f.name}: pair_id does not belong to this panel, not counted', file=sys.stderr)
            continue
        problems = validate_response(doc, inv_ids)
        if problems:
            print(f'WARNING: {f.name}: not counted ({problems[0]}' + (f' and {len(problems) - 1} more' if len(problems) > 1 else '') + ')',
                  file=sys.stderr)
            continue
        by_cand[cid].append(doc)
        names[cid].append(f.name)

    candidates = {}
    stable = {}                                    # candidate -> judge -> {key: answer both passes agree on}
    seen = {j: [0, 0] for j in JUDGES}             # judge -> [consistent answers, answers seen], complete or not
    for cid, docs in by_cand.items():
        agg = scoring.aggregate_panel(docs, inv_ids, names[cid])
        items = {}
        for i in inv_ids:
            items['L1:' + i] = agg['l1'].get(i, 'undecided')
        for i in inv_ids:
            items['L2:' + i] = agg['l2'].get(i, 'undecided')
        lv = agg['levels']
        candidates[cid] = {'levels': {'L1': lv['L1'], 'L2_panel': lv['L2'], 'L3': lv['L3'], 'L4_panel': lv['L4']},
                           'items': items, 'extra_parts': agg['extra_parts']}
        per_judge = {}
        for d in docs:
            per_judge.setdefault(d['judge_id'], []).append(d)
        stable[cid] = {}
        for j, passes in per_judge.items():
            answers, n_keys = _stable_answers(passes)
            seen[j][0] += len(answers)
            seen[j][1] += n_keys
            if sorted(d['pass'] for d in passes) == [1, 2]:
                stable[cid][j] = answers
    judges = {j: {'self_consistency': (seen[j][0] / seen[j][1] if seen[j][1] else 0.0)} for j in JUDGES}
    agreement = {}
    for a, b in itertools.combinations(JUDGES, 2):
        same = total = 0
        for cid in stable:
            if a in stable[cid] and b in stable[cid]:
                for k in set(stable[cid][a]) & set(stable[cid][b]):
                    total += 1
                    same += stable[cid][a][k] == stable[cid][b][k]
        agreement[f'{a}-{b}'] = same / total if total else 0.0
    doc = {'schema_version': 'tally.v1', 'candidates': candidates, 'judges': judges, 'pairwise_agreement': agreement}
    scoring.write_json(out, doc)
    return {'out': str(out), 'candidates': len(candidates), 'answers_counted': sum(len(v) for v in by_cand.values())}


# ---- calibration form ----------------------------------------------------------------------------------------------

def calibration_form(candidate, truth, out):
    inventory, _ = _inventory(truth)
    cdir = Path(candidate)
    if not (cdir / VIEW).is_file():
        raise InputError(f'FILE_MISSING: {cdir / VIEW}')
    rows = ['| id | 部件 | L1 有没有 | L2 位置和外形（只对判为 present 的项） |', '|---|---|---|---|']
    for i in inventory:
        name = i['name'].replace('|', '/')
        rows.append(f"| {i['id']} | {name} | [ ] present &nbsp; [ ] missing | [ ] ok &nbsp; [ ] off &nbsp; [ ] wrong |")
    text = '\n'.join([
        f'# 校准表：{cdir.name}',
        '',
        f'只看这张图，不要看任何分数：`{cdir.name}/{VIEW}`（三格：原图、渲染、叠加）。逐项在方框里打 x。',
        '',
        '- L1：渲染里能认出这个部件（种类对、位置大致对）填 present，否则填 missing。',
        '- L2：只对 present 的项填。ok = 位置和大小看着对；off = 明显偏了或大小不对，但还是这个部件；wrong = 位置或形状明显错，或和别的部件混了。',
        '',
        *rows,
        '',
        '渲染里多出的、原图没有的部件个数：______',
        '',
    ])
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    return {'out': str(path), 'items': len(inventory)}


# ---- command line --------------------------------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('prepare', help='blind the candidates and write the judges\' request files')
    p.add_argument('--candidates', required=True, help='folder with one sub-folder per candidate, each with panel_view.png')
    p.add_argument('--truth', required=True, help='answer-sheet folder (inventory.json is used)')
    p.add_argument('--out', required=True, help='panel folder to create (must be empty or absent)')
    p.add_argument('--seed', required=True, type=int, help='random seed: same seed, same blinding')
    p.add_argument('--pairwise', action='store_true', help='tie-break requests for every pair of candidates instead of rating requests')
    t = sub.add_parser('tally', help='majority per item and the panel scores, written as tally.v1')
    t.add_argument('--panel', required=True, help='panel folder made by prepare, with responses/ filled in')
    t.add_argument('--out', required=True, help='tally.json to write')
    c = sub.add_parser('calibration-form', help='Markdown sheet for the decision maker to rate one candidate')
    c.add_argument('--candidate', required=True, help='candidate folder with panel_view.png')
    c.add_argument('--truth', required=True, help='answer-sheet folder')
    c.add_argument('--out', required=True, help='Markdown file to write')
    args = ap.parse_args(argv)
    try:
        if args.cmd == 'prepare':
            result = prepare(args.candidates, args.truth, args.out, args.seed, args.pairwise)
        elif args.cmd == 'tally':
            result = tally(args.panel, args.out)
        else:
            result = calibration_form(args.candidate, args.truth, args.out)
        print(json.dumps(result))
        return 0
    except (InputError, OSError, ValueError, KeyError, TypeError) as e:
        print('ERROR: ' + (str(e) if isinstance(e, InputError) else f'{type(e).__name__}: {e}'), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
