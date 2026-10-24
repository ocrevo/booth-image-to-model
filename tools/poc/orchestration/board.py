"""The POC board (contract board.v1, docs/poc/02-architecture-and-contracts.md section 4.8): package states and file leases.

    python tools/poc/orchestration/board.py init --out output/poc/board.json [--cards docs/poc/packages] [--day 1]
    python tools/poc/orchestration/board.py show [--json]
    python tools/poc/orchestration/board.py set P1 leased --owner worker-p1 --branch poc/p1 --worktree <path>
    python tools/poc/orchestration/board.py lease booth-image-to-model/scripts/motifs.py P7-fam-fascia
    python tools/poc/orchestration/board.py unlease booth-image-to-model/scripts/motifs.py
    python tools/poc/orchestration/board.py day 2

A package moves forward one step at a time, todo -> leased -> in_progress -> verify -> merged; a verifier may send it back
from verify to in_progress. Any open state may go to blocked or dropped; blocked goes back to the state it came from (or
to todo, or to dropped). merged and dropped are final. A package id that is not on the board can only enter as todo.
A refused transition or a lease held by another package exits with code 2 and leaves the board untouched; a bad board
file or argument exits with code 1. Every write is atomic (a temporary file, then a rename, retried a few times when
Windows holds the target open).

Concurrent writers are serialised by a lock file next to the board (``board.json.lock``, created with O_CREAT | O_EXCL):
every command that changes the board loads, changes and saves it while holding the lock. A waiting process retries for
up to 15 seconds and then exits 1. A lock whose process is gone (or that is older than two minutes) is reclaimed.
blender_token.py takes the same lock when it mirrors the token into the board.

Standard library only. The board lives in output/poc/ (not tracked); the default path is output/poc/board.json.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_BOARD = ROOT / 'output' / 'poc' / 'board.json'
DEFAULT_CARDS = ROOT / 'docs' / 'poc' / 'packages'

SCHEMA = 'board.v1'
STATUSES = ('todo', 'leased', 'in_progress', 'verify', 'merged', 'blocked', 'dropped')
FORWARD = ('todo', 'leased', 'in_progress', 'verify', 'merged')
OPEN = ('todo', 'leased', 'in_progress', 'verify')
FINAL = ('merged', 'dropped')
PACKAGE_FIELDS = ('status', 'owner', 'branch', 'worktree', 'verifier', 'started', 'finished', 'report_path', 'note',
                  'blocked_from')
TOP_FIELDS = ('schema_version', 'updated', 'day', 'packages', 'leases', 'blender_token')
TOKEN_FIELDS = ('holder', 'since', 'purpose')

EXIT_BAD = 1
EXIT_REFUSED = 2

LOCK_TIMEOUT_S = 15.0      # how long a writer waits for the lock
LOCK_POLL_S = 0.05
LOCK_MAX_AGE_S = 120.0     # a lock this old is left over: board commands hold it for milliseconds
LOCK_EMPTY_GRACE_S = 5.0   # a lock without readable content this old is left over by a crash
REPLACE_TRIES = 40         # os.replace retries (Windows refuses while another process has the target open)
REPLACE_PAUSE_S = 0.05


class BoardError(Exception):
    """The board file or an argument is wrong, or the board cannot be locked or written (exit 1)."""


class Refused(Exception):
    """The change is not allowed (exit 2)."""


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec='seconds')


def pid_alive(pid) -> bool:
    """True when a process with this id exists. Never signals the process (os.kill would end it on Windows)."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        process_query_limited_information, still_active, access_denied = 0x1000, 259, 5
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return ctypes.get_last_error() == access_denied
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _read_bytes(path: Path):
    try:
        return Path(path).read_bytes()
    except OSError:
        return None


def _reclaim(lock: Path) -> bool:
    """Remove a left-over lock (its process is gone, it is too old, or it stayed empty). Return True when removed."""
    first = _read_bytes(lock)
    if first is None:
        return False
    try:
        age = time.time() - lock.stat().st_mtime
    except OSError:
        return False
    try:
        pid = json.loads(first.decode('utf-8')).get('pid')
    except (ValueError, UnicodeDecodeError, AttributeError):
        pid = None
    if pid is None:
        left_over = age > LOCK_EMPTY_GRACE_S
    else:
        left_over = age > LOCK_MAX_AGE_S or not pid_alive(pid)
    if not left_over or _read_bytes(lock) != first:  # changed meanwhile: someone else's live lock
        return False
    try:
        os.unlink(lock)
    except OSError:
        return False
    print(f'note: reclaimed a left-over lock {lock.name} (pid {pid})', file=sys.stderr)
    return True


@contextmanager
def locked(path: Path, timeout: float = LOCK_TIMEOUT_S):
    """Hold ``<path>.lock`` (O_CREAT | O_EXCL) for the duration of the block; raise BoardError after ``timeout``."""
    lock = Path(str(path) + '.lock')
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_BINARY', 0), 0o644)
            break
        except FileExistsError:
            if _reclaim(lock):
                continue
        except PermissionError:
            pass  # Windows: the lock is being deleted by its holder; try again
        except OSError as exc:
            raise BoardError(f'{lock}: cannot create the lock ({exc})') from None
        if time.monotonic() >= deadline:
            holder = _read_bytes(lock)
            raise BoardError(f'{path} is locked by another process ({lock.name}: '
                             f'{holder.decode("utf-8", "replace").strip() if holder else "unreadable"}); '
                             f'waited {timeout:g} s. If no board command is running, delete {lock}.')
        time.sleep(LOCK_POLL_S)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(json.dumps({'pid': os.getpid(), 'since': now()}).encode('utf-8'))
        yield lock
    finally:
        try:
            os.unlink(lock)
        except OSError:
            pass


def empty_package() -> dict:
    return {'status': 'todo', 'owner': None, 'branch': None, 'worktree': None, 'verifier': None, 'started': None,
            'finished': None, 'report_path': None, 'note': None}


def new_board(package_ids, day: int = 1) -> dict:
    return {'schema_version': SCHEMA, 'updated': now(), 'day': day,
            'packages': {pid: empty_package() for pid in package_ids}, 'leases': {},
            'blender_token': {'holder': None, 'since': None, 'purpose': None}}


def validate(board) -> list[str]:
    """Problems with a board.v1 object (empty when valid)."""
    if not isinstance(board, dict):
        return ['the board is not a JSON object']
    problems = [f'missing field {k}' for k in TOP_FIELDS if k not in board]
    problems += [f'unknown field {k}' for k in board if k not in TOP_FIELDS]
    if board.get('schema_version') != SCHEMA:
        problems.append(f'schema_version is not {SCHEMA}')
    if 'updated' in board and not isinstance(board['updated'], str):
        problems.append('updated is not a string')
    day = board.get('day')
    if 'day' in board and (isinstance(day, bool) or not isinstance(day, int) or day < 0):
        problems.append('day is not a whole number >= 0')
    packages = board.get('packages')
    if not isinstance(packages, dict):
        problems.append('packages is not an object')
        packages = {}
    for pid, entry in packages.items():
        if not isinstance(entry, dict):
            problems.append(f'package {pid} is not an object')
            continue
        if entry.get('status') not in STATUSES:
            problems.append(f'package {pid} has status {entry.get("status")!r}')
        problems += [f'package {pid} has unknown field {k}' for k in entry if k not in PACKAGE_FIELDS]
        problems += [f'package {pid} field {k} is not a string or null' for k in PACKAGE_FIELDS
                     if k != 'status' and entry.get(k) is not None and not isinstance(entry.get(k), str)]
    leases = board.get('leases')
    if not isinstance(leases, dict) or not all(isinstance(v, str) for v in leases.values()):
        problems.append('leases is not an object of file -> package')
    token = board.get('blender_token')
    if not isinstance(token, dict) or set(token) != set(TOKEN_FIELDS):
        problems.append('blender_token must have exactly holder, since, purpose')
    return problems


def read_text(path: Path) -> str:
    """Read a text file, retrying while Windows refuses access during another process's rename."""
    for attempt in range(REPLACE_TRIES):
        try:
            return Path(path).read_text(encoding='utf-8')
        except PermissionError:
            if attempt == REPLACE_TRIES - 1:
                raise
            time.sleep(REPLACE_PAUSE_S)
    raise AssertionError('unreachable')


def load(path: Path) -> dict:
    try:
        board = json.loads(read_text(path))
    except FileNotFoundError:
        raise BoardError(f'{path}: no board (run "board.py init --out {path}" first)') from None
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BoardError(f'{path}: not valid JSON ({exc})') from None
    except OSError as exc:
        raise BoardError(f'{path}: cannot read ({exc})') from None
    problems = validate(board)
    if problems:
        raise BoardError(f'{path}: not a valid {SCHEMA} board: ' + '; '.join(problems))
    return board


def write_json(path: Path, data) -> None:
    """Write JSON with LF line ends atomically: a temporary file in the same folder, then a rename.

    The rename is retried a few times (Windows refuses it while another process has the target open); when it still
    fails, the temporary file is removed and BoardError is raised.
    """
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    except OSError as exc:
        raise BoardError(f'{path}: cannot write ({exc})') from None
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
        for attempt in range(REPLACE_TRIES):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == REPLACE_TRIES - 1:
                    raise
                time.sleep(REPLACE_PAUSE_S)
    except OSError as exc:
        _discard(tmp)
        raise BoardError(f'{path}: cannot write ({exc})') from None
    except BaseException:
        _discard(tmp)
        raise


def _discard(tmp) -> None:
    try:
        os.unlink(tmp)
    except OSError:
        pass


def save(path: Path, board: dict) -> None:
    board['updated'] = now()
    write_json(path, board)


def card_ids(folder: Path) -> list[str]:
    """Ids from the front matter of every P*.md card in a folder (only the id line is read)."""
    ids = []
    for card in sorted(Path(folder).glob('P*.md')):
        lines = card.read_text(encoding='utf-8').replace('\r\n', '\n').split('\n')
        if not lines or lines[0].strip() != '---':
            continue
        for line in lines[1:]:
            if line.strip() == '---':
                break
            match = re.fullmatch(r'id:\s*["\']?([^"\'\s]+)["\']?\s*', line)
            if match:
                ids.append(match.group(1))
                break
    return ids


def check_transition(old: str | None, new: str, blocked_from: str | None = None) -> None:
    """Raise Refused unless ``old -> new`` is legal. ``old`` is None for a package not on the board."""
    if new not in STATUSES:
        raise Refused(f'unknown status {new!r}')
    if old is None:
        if new != 'todo':
            raise Refused(f'a package not on the board can only be added as todo, not {new}')
        return
    if old == new:
        return  # same status: only the other fields change
    if old in FINAL:
        raise Refused(f'{old} is final; it cannot become {new}')
    if new in ('blocked', 'dropped'):
        return
    if old == 'blocked':
        allowed = {'todo'} | ({blocked_from} if blocked_from in OPEN else set())
        if new not in allowed:
            raise Refused(f'blocked can only return to {" or ".join(sorted(allowed))}, not {new}')
        return
    if old == 'verify' and new == 'in_progress':
        return  # sent back by the verifier
    if FORWARD.index(new) != FORWARD.index(old) + 1:
        raise Refused(f'{old} -> {new} is not allowed (one step at a time: {" -> ".join(FORWARD)})')


def set_status(board: dict, package: str, status: str, **fields) -> dict:
    """Apply a status change and optional field updates to ``board`` in place; return the package entry."""
    packages = board['packages']
    entry = packages.get(package)
    old = entry['status'] if entry else None
    check_transition(old, status, entry.get('blocked_from') if entry else None)
    if entry is None:
        entry = packages[package] = empty_package()
    stamp = now()
    if status == 'blocked' and old != 'blocked':
        entry['blocked_from'] = old
    elif status != 'blocked':
        entry.pop('blocked_from', None)
    entry['status'] = status
    if status == 'in_progress' and not entry.get('started'):
        entry['started'] = stamp
    if status in FINAL and old != status:
        entry['finished'] = stamp
    for key in ('owner', 'branch', 'worktree', 'verifier', 'report_path', 'note'):
        if fields.get(key) is not None:
            entry[key] = fields[key] or None  # an empty string clears the field
    return entry


def norm_file(path: str) -> str:
    path = path.strip().replace('\\', '/')
    while path.startswith('./'):
        path = path[2:]
    return path


def lease(board: dict, file: str, package: str) -> bool:
    """Lease ``file`` to ``package``. Return False when it already was; raise Refused when another package holds it."""
    if package not in board['packages']:
        raise BoardError(f'{package} is not on the board')
    file = norm_file(file)
    holder = board['leases'].get(file)
    if holder == package:
        return False
    if holder is not None:
        raise Refused(f'{file} is leased to {holder}; unlease it first')
    board['leases'][file] = package
    return True


def unlease(board: dict, file: str):
    """Drop the lease on ``file``; return the package that held it, or None."""
    return board['leases'].pop(norm_file(file), None)


def show(board: dict) -> str:
    rows = [('id', 'status', 'owner', 'branch', 'verifier', 'note')]
    for pid, entry in board['packages'].items():
        status = entry['status'] + (f' (from {entry["blocked_from"]})' if entry.get('blocked_from') else '')
        rows.append((pid, status, entry.get('owner') or '-', entry.get('branch') or '-', entry.get('verifier') or '-',
                     entry.get('note') or ''))
    widths = [max(len(str(row[i])) for row in rows) for i in range(len(rows[0]) - 1)]
    lines = [f'board day {board["day"]}, updated {board["updated"]}', '']
    for row in rows:
        lines.append('  '.join(str(cell).ljust(widths[i]) for i, cell in enumerate(row[:-1])) + '  ' + row[-1])
    counts = {s: sum(1 for e in board['packages'].values() if e['status'] == s) for s in STATUSES}
    lines += ['', 'counts: ' + ', '.join(f'{s} {n}' for s, n in counts.items() if n)]
    lines += ['', 'leases:' + ('' if board['leases'] else ' none')]
    lines += [f'  {file} -> {pid}' for file, pid in sorted(board['leases'].items())]
    token = board['blender_token']
    lines += ['', 'blender token: ' + (f'{token["holder"]} since {token["since"]} ({token["purpose"]})'
                                       if token.get('holder') else 'free')]
    return '\n'.join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Maintain the POC board (board.v1): package states and hot-file leases. A refused transition or a '
                    'lease held by another package exits 2; a bad board or argument exits 1.')
    sub = parser.add_subparsers(dest='command', required=True)

    def with_board(p):
        p.add_argument('--board', type=Path, default=DEFAULT_BOARD, help='board file (default: output/poc/board.json)')
        return p

    p = sub.add_parser('init', help='write a new board with every card as todo')
    p.add_argument('--out', type=Path, required=True, help='board file to write, e.g. output/poc/board.json')
    p.add_argument('--cards', type=Path, default=DEFAULT_CARDS, help='folder with the P*.md cards (default: '
                   'docs/poc/packages)')
    p.add_argument('--day', type=int, default=1, help='current day (default 1)')
    p.add_argument('--force', action='store_true', help='overwrite an existing board')

    p = with_board(sub.add_parser('show', help='print the board'))
    p.add_argument('--json', action='store_true', help='print the raw JSON')

    p = with_board(sub.add_parser('set', help='change a package status (one step at a time) and its fields'))
    p.add_argument('id', help='package id, e.g. P1')
    p.add_argument('status', choices=STATUSES)
    p.add_argument('--owner')
    p.add_argument('--branch')
    p.add_argument('--worktree')
    p.add_argument('--verifier')
    p.add_argument('--report', dest='report_path', help='path of the worker.v1 report')
    p.add_argument('--note', help='one sentence')

    p = with_board(sub.add_parser('lease', help='lease a hot file to a package (exit 2 if another package holds it)'))
    p.add_argument('file', help='path relative to the repository root')
    p.add_argument('id', help='package id')

    p = with_board(sub.add_parser('unlease', help='release the lease on a file'))
    p.add_argument('file')

    p = with_board(sub.add_parser('day', help='set the current day'))
    p.add_argument('day', type=int)
    return parser


def run(args) -> str:
    """Carry out one command; return the line to print. Commands that change the board run under its lock."""
    if args.command == 'init':
        if args.day < 0:
            raise BoardError('--day must be >= 0')
        ids = card_ids(args.cards)
        if not ids:
            raise BoardError(f'{args.cards}: no task cards with an id')
        with locked(args.out):
            if args.out.exists() and not args.force:
                raise BoardError(f'{args.out} exists; use --force to overwrite it')
            write_json(args.out, new_board(ids, args.day))
        return f'board written: {args.out} ({len(ids)} packages)'
    if args.command == 'show':
        board = load(args.board)
        return json.dumps(board, ensure_ascii=False, indent=2) if args.json else show(board)
    if args.command == 'day' and args.day < 0:
        raise BoardError('day must be >= 0')
    with locked(args.board):
        board = load(args.board)
        if args.command == 'set':
            old = board['packages'].get(args.id, {}).get('status')
            set_status(board, args.id, args.status, owner=args.owner, branch=args.branch, worktree=args.worktree,
                       verifier=args.verifier, report_path=args.report_path, note=args.note)
            save(args.board, board)
            return f'{args.id}: {old or "new"} -> {args.status}'
        if args.command == 'lease':
            if not lease(board, args.file, args.id):
                return f'{norm_file(args.file)} was already leased to {args.id}'
            save(args.board, board)
            return f'{norm_file(args.file)} leased to {args.id}'
        if args.command == 'unlease':
            holder = unlease(board, args.file)
            if holder is None:
                return f'{norm_file(args.file)} was not leased'
            save(args.board, board)
            return f'{norm_file(args.file)} released by {holder}'
        if args.command == 'day':
            board['day'] = args.day
            save(args.board, board)
            return f'day {args.day}'
    raise BoardError(f'unknown command {args.command}')


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        print(run(args))
        return 0
    except Refused as exc:
        print(f'refused: {exc}', file=sys.stderr)
        return EXIT_REFUSED
    except (BoardError, OSError, UnicodeDecodeError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return EXIT_BAD


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    raise SystemExit(main())
