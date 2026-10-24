"""The POC gates (contract gates.v1, docs/poc/02-architecture-and-contracts.md section 4.9): measurements and decisions.

    python tools/poc/orchestration/gates.py init --out output/poc/gates.json
    python tools/poc/orchestration/gates.py show [--json]
    python tools/poc/orchestration/gates.py measure G1 --value 0.83 --threshold "spearman_total >= 0.8 ..." \\
        --evidence output/poc/anchor1/pic_A/rank_check.json
    python tools/poc/orchestration/gates.py decide G1 pass --by commander
    python tools/poc/orchestration/gates.py decide G3 waived --by owner --memo output/poc/memo/g3.md

``init`` copies docs/poc/templates/gates.example.json, so the thresholds have one source. ``measure`` writes only
``value``, ``threshold``, ``measured_at`` and ``evidence_path``; the status stays as it is. ``decide`` writes ``status`` and
records who decided in ``decided_by`` (with ``decided_at``): G1 may be decided by the commander or the owner, G2 to G5 only
``--by owner`` (the commander writes it after the owner said so, and notes "decided by the owner orally" in the digest),
and ``waived`` only ``--by owner`` with a memo. pass and fail need a measurement first. A refused decision exits 2; a bad
file or argument exits 1. Writes are atomic and run under the same kind of lock file as the board (``gates.json.lock``,
see board.py), so two commands never lose each other's change.

Standard library only. The default file is output/poc/gates.json (not tracked).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

try:  # imported as part of the tools.poc.orchestration package
    from . import board as _board
except ImportError:  # run as a script, or with this folder on sys.path
    import board as _board

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_GATES = ROOT / 'output' / 'poc' / 'gates.json'
TEMPLATE = ROOT / 'docs' / 'poc' / 'templates' / 'gates.example.json'

SCHEMA = 'gates.v1'
GATES = ('G1', 'G2', 'G3', 'G4', 'G5')
STATUSES = ('pending', 'pass', 'fail', 'waived')
DECISIONS = ('pass', 'fail', 'waived')
DECIDERS = ('owner', 'commander')
COMMANDER_MAY_DECIDE = ('G1',)
REQUIRED = ('status', 'value', 'threshold', 'measured_at', 'evidence_path', 'memo_path')
OPTIONAL = ('decided_by', 'decided_at')

EXIT_BAD = 1
EXIT_REFUSED = 2


class GatesError(Exception):
    """The gates file or an argument is wrong (exit 1)."""


class Refused(Exception):
    """The decision is not allowed (exit 2)."""


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec='seconds')


def validate(gates) -> list[str]:
    if not isinstance(gates, dict):
        return ['the gates file is not a JSON object']
    problems = []
    if gates.get('schema_version') != SCHEMA:
        problems.append(f'schema_version is not {SCHEMA}')
    problems += [f'unknown field {k}' for k in gates if k not in GATES and k != 'schema_version']
    for gate in GATES:
        entry = gates.get(gate)
        if not isinstance(entry, dict):
            problems.append(f'{gate} is missing or not an object')
            continue
        problems += [f'{gate} misses field {k}' for k in REQUIRED if k not in entry]
        problems += [f'{gate} has unknown field {k}' for k in entry if k not in REQUIRED + OPTIONAL]
        if entry.get('status') not in STATUSES:
            problems.append(f'{gate} has status {entry.get("status")!r}')
        if entry.get('decided_by') not in (None,) + DECIDERS:
            problems.append(f'{gate} has decided_by {entry.get("decided_by")!r}')
        for key in ('measured_at', 'evidence_path', 'memo_path', 'decided_at'):
            if entry.get(key) is not None and not isinstance(entry[key], str):
                problems.append(f'{gate} field {key} is not a string or null')
    return problems


def read_json(path: Path, what: str):
    try:
        return json.loads(_board.read_text(path))
    except FileNotFoundError:
        raise GatesError(f'{path}: no {what}') from None
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GatesError(f'{path}: not valid JSON ({exc})') from None
    except OSError as exc:
        raise GatesError(f'{path}: cannot read ({exc})') from None


def load(path: Path) -> dict:
    gates = read_json(path, 'gates file (run "gates.py init --out <path>" first)')
    problems = validate(gates)
    if problems:
        raise GatesError(f'{path}: not a valid {SCHEMA} file: ' + '; '.join(problems))
    return gates


def write_json(path: Path, data) -> None:
    """Atomic LF write with retried rename (board.write_json); a failure raises GatesError."""
    try:
        _board.write_json(path, data)
    except _board.BoardError as exc:
        raise GatesError(str(exc)) from None


def parse_value(text: str):
    """A number, boolean, null, list or object when the text is JSON; otherwise the text itself."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def measure(gates: dict, gate: str, value, threshold: str, evidence: str, when: str | None = None) -> dict:
    entry = gates[gate]
    entry['value'] = value
    entry['threshold'] = threshold
    entry['measured_at'] = when or now()
    entry['evidence_path'] = evidence
    return entry


def decide(gates: dict, gate: str, status: str, by: str, memo: str | None = None, when: str | None = None) -> dict:
    """Write a decision; raise Refused when ``by`` may not make it or it lacks a measurement or memo."""
    if status not in DECISIONS:
        raise Refused(f'unknown decision {status!r}')
    if by not in DECIDERS:
        raise Refused(f'unknown decider {by!r}')
    entry = gates[gate]
    if status == 'waived':
        if by != 'owner':
            raise Refused('only the owner waives a gate (--by owner)')
        if not (memo or entry.get('memo_path')):
            raise Refused('a waiver needs a memo explaining it (--memo <path>)')
    else:
        if by == 'commander' and gate not in COMMANDER_MAY_DECIDE:
            raise Refused(f'{gate} is decided by the owner; write it with --by owner only after the owner said so')
        if not entry.get('measured_at'):
            raise Refused(f'{gate} has no measurement yet; run "gates.py measure {gate} ..." first')
    entry['status'] = status
    entry['decided_by'] = by
    entry['decided_at'] = when or now()
    if memo:
        entry['memo_path'] = memo
    return entry


def show(gates: dict) -> str:
    lines = []
    for gate in GATES:
        e = gates[gate]
        decided = f' by {e["decided_by"]}' if e.get('decided_by') else ''
        lines.append(f'{gate}  {e["status"]}{decided}')
        lines.append(f'    threshold  {e["threshold"]}')
        if e.get('measured_at'):
            lines.append(f'    value      {json.dumps(e["value"], ensure_ascii=False)}  (measured {e["measured_at"]})')
            lines.append(f'    evidence   {e["evidence_path"]}')
        if e.get('memo_path'):
            lines.append(f'    memo       {e["memo_path"]}')
    return '\n'.join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Maintain the POC gates (gates.v1): measurements and decisions. A refused decision exits 2; a bad '
                    'file or argument exits 1.')
    sub = parser.add_subparsers(dest='command', required=True)

    def with_gates(p):
        p.add_argument('--gates', type=Path, default=DEFAULT_GATES, help='gates file (default: output/poc/gates.json)')
        return p

    p = sub.add_parser('init', help='write a new gates file from docs/poc/templates/gates.example.json')
    p.add_argument('--out', type=Path, required=True, help='gates file to write, e.g. output/poc/gates.json')
    p.add_argument('--template', type=Path, default=TEMPLATE, help=argparse.SUPPRESS)
    p.add_argument('--force', action='store_true', help='overwrite an existing gates file')

    p = with_gates(sub.add_parser('show', help='print the gates'))
    p.add_argument('--json', action='store_true', help='print the raw JSON')

    p = with_gates(sub.add_parser('measure', help='record a measurement (never changes the status)'))
    p.add_argument('gate', choices=GATES)
    p.add_argument('--value', required=True, help='the measured value; JSON (number, object) or plain text')
    p.add_argument('--threshold', required=True, help='the threshold it is compared with, as text')
    p.add_argument('--evidence', required=True, help='path of the evidence file, relative to the repository root')

    p = with_gates(sub.add_parser('decide', help='record a decision: G1 by owner or commander, G2-G5 and waived by '
                                                 'owner only'))
    p.add_argument('gate', choices=GATES)
    p.add_argument('status', choices=DECISIONS)
    p.add_argument('--by', required=True, choices=DECIDERS)
    p.add_argument('--memo', help='memo path (required for waived)')
    return parser


def run(args) -> str:
    """Carry out one command; return the line to print. Commands that write run under the file's lock."""
    if args.command == 'init':
        gates = read_json(args.template, 'gates template')
        problems = validate(gates)
        if problems:
            raise GatesError(f'{args.template}: not a valid {SCHEMA} template: ' + '; '.join(problems))
        with _board.locked(args.out):
            if args.out.exists() and not args.force:
                raise GatesError(f'{args.out} exists; use --force to overwrite it')
            write_json(args.out, gates)
        return f'gates written: {args.out}'
    if args.command == 'show':
        gates = load(args.gates)
        return json.dumps(gates, ensure_ascii=False, indent=2) if args.json else show(gates)
    with _board.locked(args.gates):
        gates = load(args.gates)
        if args.command == 'measure':
            measure(gates, args.gate, parse_value(args.value), args.threshold, args.evidence)
            write_json(args.gates, gates)
            if not (ROOT / args.evidence).exists() and not Path(args.evidence).exists():
                print(f'note: evidence {args.evidence} does not exist yet', file=sys.stderr)
            return f'{args.gate}: value {args.value} (threshold {args.threshold}); status {gates[args.gate]["status"]}'
        if args.command == 'decide':
            decide(gates, args.gate, args.status, args.by, args.memo)
            write_json(args.gates, gates)
            return f'{args.gate}: {args.status} by {args.by}'
    raise GatesError(f'unknown command {args.command}')


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        print(run(args))
        return 0
    except Refused as exc:
        print(f'refused: {exc}', file=sys.stderr)
        return EXIT_REFUSED
    except (GatesError, _board.BoardError, OSError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return EXIT_BAD


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    raise SystemExit(main())
