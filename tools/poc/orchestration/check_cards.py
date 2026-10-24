"""Check the POC task cards (docs/poc/packages/P*.md) mechanically.

    python tools/poc/orchestration/check_cards.py docs/poc/packages            # exit 1 and one line per problem
    python tools/poc/orchestration/check_cards.py docs/poc/packages --json     # the same as JSON

Checks, per docs/poc/packages/P0-orchestration.md and section 4.7 of docs/poc/02-architecture-and-contracts.md:
- the front matter parses, has every field of section 4.7 and no other, with the right types and enum values;
- ids are unique; every ``depends_on`` names an existing id; the dependency graph has no cycle;
- ``allowed_files`` and ``forbidden_files`` do not overlap (a forbidden entry ``"*"`` means "everything else" and is skipped);
  two glob patterns overlap when one path matches both (``*`` and ``?`` stay inside one folder, ``**`` crosses folders);
- every ``acceptance_cmds`` entry starts with ``python`` or ``BLENDER_EXE= python``, and its script exists under the
  repository root or is delivered by the card or by a card in the transitive closure of its ``depends_on``. For
  ``python -m unittest discover`` the script is the start folder (``-s``); for other ``-m`` modules it is the module's file,
  and standard-library modules always pass.

Standard library only. The front matter is read with a small parser for the subset the cards use (``key: value``, inline
``[a, b]`` lists, block ``- item`` lists, quoted scalars), not a full YAML parser.
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

FIELDS = ('id', 'title', 'goal', 'depends_on', 'model', 'lane', 'allowed_files', 'forbidden_files', 'inputs',
          'deliverables', 'tests_first', 'acceptance_cmds', 'effort_h', 'day', 'stop_rule')
LIST_FIELDS = ('depends_on', 'allowed_files', 'forbidden_files', 'inputs', 'deliverables', 'tests_first',
               'acceptance_cmds')
TEXT_FIELDS = ('id', 'title', 'goal', 'model', 'lane', 'stop_rule')
MODELS = ('sonnet', 'opus')
LANES = ('worker', 'blender', 'owner')
PREFIXES = ('python ', 'BLENDER_EXE= python ')
EVERYTHING_ELSE = '*'


class CardError(ValueError):
    """The front matter cannot be read."""


# --- front matter --------------------------------------------------------------------------------------------------

def _scalar(text: str):
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            value = None
        if isinstance(value, str):
            return value
    if len(text) >= 2 and text[0] == text[-1] == "'" and "'" not in text[1:-1].replace("''", ''):
        return text[1:-1].replace("''", "'")
    if text in ('null', '~'):
        return None
    if text in ('true', 'false'):
        return text == 'true'
    if re.fullmatch(r'-?\d+', text):
        return int(text)
    if re.fullmatch(r'-?\d+\.\d+', text):
        return float(text)
    return text


def _inline_list(text: str):
    inner = text.strip()[1:-1].strip()
    if not inner:
        return []
    return [_scalar(part) for part in inner.split(',')]


def parse_front_matter(text: str) -> dict:
    """Return the front matter of a card as a dict; raise CardError when it cannot be read."""
    lines = text.replace('\r\n', '\n').split('\n')
    if not lines or lines[0].strip() != '---':
        raise CardError('no front matter (the first line must be ---)')
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == '---')
    except StopIteration:
        raise CardError('front matter is not closed with ---') from None
    data: dict = {}
    current = None
    for number, line in enumerate(lines[1:end], 2):
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        item = re.fullmatch(r'\s*-\s+(.*)', line) or re.fullmatch(r'\s*-()', line)
        if item:
            if current is None:
                raise CardError(f'line {number}: list item outside a list')
            data[current].append(_scalar(item.group(1)))
            continue
        key = re.fullmatch(r'([A-Za-z_][A-Za-z0-9_]*):(?:\s+(.*?))?\s*', line)
        if not key:
            raise CardError(f'line {number}: cannot read {line.strip()!r}')
        name, value = key.group(1), key.group(2)
        if name in data:
            raise CardError(f'line {number}: field {name} appears twice')
        if value is None or value == '':
            data[name] = []
            current = name
        elif value.startswith('[') and value.endswith(']'):
            data[name] = _inline_list(value)
            current = None
        else:
            data[name] = _scalar(value)
            current = None
    return data


def card_problems(card: dict) -> list[str]:
    """Field-level problems of one parsed card."""
    problems = []
    for name in FIELDS:
        if name not in card:
            problems.append(f'missing field {name}')
    for name in card:
        if name not in FIELDS:
            problems.append(f'unknown field {name}')
    for name in LIST_FIELDS:
        value = card.get(name)
        if name in card and not (isinstance(value, list) and all(isinstance(v, str) and v for v in value)):
            problems.append(f'{name} must be a list of non-empty strings')
    for name in TEXT_FIELDS:
        value = card.get(name)
        if name in card and not (isinstance(value, str) and value.strip()):
            problems.append(f'{name} must be a non-empty string')
    if 'model' in card and card['model'] not in MODELS:
        problems.append(f'model must be one of {", ".join(MODELS)} (got {card["model"]!r})')
    if 'lane' in card and card['lane'] not in LANES:
        problems.append(f'lane must be one of {", ".join(LANES)} (got {card["lane"]!r})')
    effort = card.get('effort_h')
    if 'effort_h' in card and (isinstance(effort, bool) or not isinstance(effort, (int, float)) or effort <= 0):
        problems.append(f'effort_h must be a positive number (got {effort!r})')
    day = card.get('day')
    if 'day' in card and not (isinstance(day, int) and not isinstance(day, bool) and day >= 0
                              or isinstance(day, str) and re.fullmatch(r'\d+-\d+', day)):
        problems.append(f'day must be a number or a range like 1-2 (got {day!r})')
    return problems


# --- globs ---------------------------------------------------------------------------------------------------------

def _nfa(pattern: str):
    """A small NFA for a path glob: list of states, each (epsilon targets, [(kind, char, target)]); last state accepts.

    kind: 'lit' (exactly char), 'seg' (any character but '/'), 'any' (any character).
    """
    states: list = []

    def new():
        states.append(([], []))
        return len(states) - 1

    cur = new()
    i = 0
    while i < len(pattern):
        if pattern.startswith('**/', i):
            nxt, inner = new(), new()
            states[cur][0].append(nxt)                   # zero folders
            states[cur][1].append(('any', None, inner))  # or some characters ...
            states[inner][1].append(('any', None, inner))
            states[inner][1].append(('lit', '/', nxt))   # ... ending with a slash
            states[cur][1].append(('lit', '/', nxt))
            i += 3
        elif pattern.startswith('**', i):
            nxt = new()
            states[cur][1].append(('any', None, cur))
            states[cur][0].append(nxt)
            i += 2
        elif pattern[i] == '*':
            nxt = new()
            states[cur][1].append(('seg', None, cur))
            states[cur][0].append(nxt)
            i += 1
        elif pattern[i] == '?':
            nxt = new()
            states[cur][1].append(('seg', None, nxt))
            i += 1
        else:
            nxt = new()
            states[cur][1].append(('lit', pattern[i], nxt))
            i += 1
        cur = nxt
    return states, cur


def _closure(states, start):
    seen, todo = {start}, [start]
    while todo:
        for nxt in states[todo.pop()][0]:
            if nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    return seen


def _accepts(kind, char, symbol):
    if kind == 'any':
        return True
    if kind == 'seg':
        return symbol != '/'
    return symbol == char


def _norm(path: str) -> str:
    path = path.strip().replace('\\', '/')
    while path.startswith('./'):
        path = path[2:]
    return path


def globs_overlap(a: str, b: str) -> bool:
    """True when at least one path matches both glob patterns."""
    a, b = _norm(a), _norm(b)
    (sa, fa), (sb, fb) = _nfa(a), _nfa(b)
    other = '\0'  # stands for every character that is neither '/' nor a literal of either pattern
    alphabet = sorted(set(a) | set(b) | {'/', other})
    start = (frozenset(_closure(sa, 0)), frozenset(_closure(sb, 0)))
    seen, todo = {start}, [start]
    while todo:
        xa, xb = todo.pop()
        if fa in xa and fb in xb:
            return True
        for symbol in alphabet:
            na = set()
            for s in xa:
                for kind, char, target in sa[s][1]:
                    if _accepts(kind, char, symbol):
                        na |= _closure(sa, target)
            if not na:
                continue
            nb = set()
            for s in xb:
                for kind, char, target in sb[s][1]:
                    if _accepts(kind, char, symbol):
                        nb |= _closure(sb, target)
            if not nb:
                continue
            pair = (frozenset(na), frozenset(nb))
            if pair not in seen:
                seen.add(pair)
                todo.append(pair)
    return False


# --- deliverables and acceptance commands ----------------------------------------------------------------------------

PATH_TOKEN = re.compile(r'[A-Za-z0-9_.<>*\-]+(?:/[A-Za-z0-9_.<>*\-]*)+')


def deliverable_paths(deliverables) -> list[str]:
    """Path-like tokens (with at least one slash) in deliverable texts; ``<pic>`` style placeholders are kept."""
    paths = []
    for text in deliverables or []:
        paths.extend(m.group(0) for m in PATH_TOKEN.finditer(str(text)))
    return paths


def _segment_matches(pattern: str, segment: str) -> bool:
    regex = ''.join('[^/]+' if part.startswith('<') else ('[^/]*' if part == '*' else re.escape(part))
                    for part in re.split(r'(<[^>]*>|\*+)', pattern) if part)
    return re.fullmatch(regex, segment) is not None


def delivered(path: str, tokens) -> bool:
    """True when ``path`` is a delivered path, a folder that holds one, or lies inside a delivered folder."""
    parts = [p for p in _norm(path).split('/') if p]
    if not parts:
        return False
    for token in tokens:
        is_dir = token.endswith('/')
        tparts = [p for p in _norm(token).split('/') if p]
        if len(parts) <= len(tparts) and all(_segment_matches(t, p) for t, p in zip(tparts, parts)):
            return True
        if is_dir and len(parts) > len(tparts) and all(_segment_matches(t, p) for t, p in zip(tparts, parts)):
            return True
    return False


def command_targets(cmd: str):
    """Return (paths that must exist or be delivered, problem or None) for one acceptance command."""
    if not cmd.startswith(PREFIXES):
        return [], 'does not start with "python" or "BLENDER_EXE= python"'
    try:
        words = shlex.split(cmd)
    except ValueError as exc:
        return [], f'cannot split ({exc})'
    if words and words[0] == 'BLENDER_EXE=':
        words = words[1:]
    words = words[1:]  # python
    while words and words[0].startswith('-') and words[0] not in ('-m', '-c'):
        flag = words.pop(0)
        if flag in ('-X', '-W') and words:
            words.pop(0)
    if not words:
        return [], 'names no script'
    if words[0] == '-c':
        return [], 'runs inline code (-c), not a script'
    if words[0] != '-m':
        return [words[0]], None
    if len(words) < 2:
        return [], 'names no module after -m'
    module, rest = words[1], words[2:]
    if module == 'unittest':
        if rest and rest[0] == 'discover':
            start = '.'
            args = rest[1:]
            for i, word in enumerate(args):
                if word in ('-s', '--start-directory') and i + 1 < len(args):
                    start = args[i + 1]
                elif word.startswith('--start-directory='):
                    start = word.split('=', 1)[1]
            positional = [w for i, w in enumerate(args) if not w.startswith('-')
                          and (i == 0 or args[i - 1] not in ('-s', '-p', '-t', '-k', '--start-directory',
                                                             '--pattern', '--top-level-directory'))]
            if start == '.' and positional:
                start = positional[0]
            return [start], None
        targets = []
        for word in rest:
            if word.startswith('-'):
                continue
            targets.append(word if ('/' in word or word.endswith('.py')) else word.replace('.', '/') + '.py')
        return targets, None
    top = module.split('.')[0]
    if top in getattr(sys, 'stdlib_module_names', ()):
        return [], None
    return [module.replace('.', '/') + '.py'], None


def _module_file_exists(root: Path, target: str) -> bool:
    base = root / target
    if base.exists():
        return True
    if target.endswith('.py'):
        package = root / target[:-3]
        return (package / '__init__.py').exists() or (package / '__main__.py').exists()
    return False


# --- the whole folder ------------------------------------------------------------------------------------------------

def closure(card_id: str, deps: dict) -> list[str]:
    """The card and every card it depends on, transitively (missing ids are skipped)."""
    seen, todo = [], [card_id]
    while todo:
        current = todo.pop()
        if current in seen or current not in deps:
            continue
        seen.append(current)
        todo.extend(deps[current])
    return seen


def find_cycle(deps: dict):
    """One dependency cycle as a list of ids (first id repeated at the end), or None."""
    state: dict = {}
    stack: list = []

    def visit(node):
        state[node] = 'open'
        stack.append(node)
        for nxt in deps.get(node, ()):
            if nxt not in deps:
                continue
            if state.get(nxt) == 'open':
                return stack[stack.index(nxt):] + [nxt]
            if nxt not in state:
                found = visit(nxt)
                if found:
                    return found
        stack.pop()
        state[node] = 'done'
        return None

    for node in sorted(deps):
        if node not in state:
            found = visit(node)
            if found:
                return found
    return None


def check_folder(folder: Path, root: Path = ROOT):
    """Return (cards by id, problems). Problems are strings ``<file>: <message>``."""
    folder, root = Path(folder), Path(root)
    problems: list[str] = []
    cards: dict = {}
    where: dict = {}
    files = sorted(p for p in folder.glob('P*.md') if p.is_file())
    if not files:
        problems.append(f'{folder}: no task cards (P*.md)')
    for path in files:
        try:
            card = parse_front_matter(path.read_text(encoding='utf-8'))
        except (CardError, UnicodeDecodeError) as exc:
            problems.append(f'{path.name}: {exc}')
            continue
        problems.extend(f'{path.name}: {p}' for p in card_problems(card))
        card_id = card.get('id')
        if not isinstance(card_id, str) or not card_id.strip():
            continue
        if card_id in cards:
            problems.append(f'{path.name}: duplicate id {card_id} (also in {where[card_id]})')
            continue
        cards[card_id] = card
        where[card_id] = path.name

    def items(card, name):
        value = card.get(name)
        return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []

    deps = {cid: items(card, 'depends_on') for cid, card in cards.items()}
    for cid, card in cards.items():
        for dep in deps[cid]:
            if dep not in cards:
                problems.append(f'{where[cid]}: {cid} depends on {dep}, which is not a card')
            elif dep == cid:
                problems.append(f'{where[cid]}: {cid} depends on itself')
    cycle = find_cycle({cid: [d for d in ds if d != cid] for cid, ds in deps.items()})
    if cycle:
        problems.append(f'{where[cycle[0]]}: dependency cycle {" -> ".join(cycle)}')

    for cid, card in cards.items():
        for allowed in items(card, 'allowed_files'):
            for forbidden in items(card, 'forbidden_files'):
                if forbidden.strip() == EVERYTHING_ELSE:
                    continue
                if globs_overlap(allowed, forbidden):
                    problems.append(f'{where[cid]}: {cid} allows {allowed} but forbids {forbidden}')

        tokens = []
        for member in closure(cid, deps):
            tokens.extend(deliverable_paths(items(cards[member], 'deliverables')))
        for cmd in items(card, 'acceptance_cmds'):
            targets, problem = command_targets(cmd)
            if problem:
                problems.append(f'{where[cid]}: {cid} acceptance command {cmd!r} {problem}')
                continue
            for target in targets:
                if not (_module_file_exists(root, _norm(target)) or delivered(target, tokens)):
                    problems.append(f'{where[cid]}: {cid} acceptance command {cmd!r}: {target} does not exist and no '
                                    f'card in its depends_on closure delivers it')
    return cards, problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description='Check the POC task cards: front matter, unique ids, an acyclic dependency graph, allowed and '
                    'forbidden files that do not overlap, and acceptance commands that can run. Exit 1 lists every '
                    'problem.')
    parser.add_argument('folder', type=Path, help='folder with the cards, e.g. docs/poc/packages (reads P*.md)')
    parser.add_argument('--root', type=Path, default=ROOT,
                        help='repository root that script paths are relative to (default: this repository)')
    parser.add_argument('--json', action='store_true', help='print {"cards": [...], "problems": [...]} as JSON')
    args = parser.parse_args(argv)
    if not args.folder.is_dir():
        print(f'{args.folder}: not a folder', file=sys.stderr)
        return 1
    cards, problems = check_folder(args.folder, args.root)
    if args.json:
        print(json.dumps({'cards': sorted(cards), 'problems': problems}, ensure_ascii=False, indent=2))
    else:
        for problem in problems:
            print(problem)
        print(f'check_cards: {len(cards)} card(s), {len(problems)} problem(s)', file=sys.stderr)
    return 1 if problems else 0


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
