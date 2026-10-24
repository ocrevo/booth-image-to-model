"""The Blender token (docs/poc/02-architecture-and-contracts.md section 5): one process at a time may start Blender.

    python tools/poc/orchestration/blender_token.py acquire --holder blender-lane --purpose "preview pic_A" [--wait 600]
    python tools/poc/orchestration/blender_token.py status [--json]
    python tools/poc/orchestration/blender_token.py release [--holder blender-lane]
    python tools/poc/orchestration/blender_token.py release --force      # the commander clears a stale token

The token is the file output/poc/.blender.token (``--token`` for another path), created with O_CREAT | O_EXCL so that of
two processes acquiring at once exactly one succeeds. It holds ``{holder, since, purpose, pid}``; ``pid`` is the process
that ran ``acquire``'s parent (the shell or agent that will start Blender) unless ``--pid`` names another one.

- ``acquire`` exits 3 when the token is held (also when it is stale or unreadable: such a token is cleared only by
  ``release --force``). With ``--wait <seconds>`` it retries once a second and exits 3 only when the time is up.
- ``status`` prints free, held, stale or unreadable. A token is stale when it has been held for more than 30 minutes and
  its process no longer exists; unreadable when its content is not UTF-8 JSON (or the path is not a file). ``--json``
  prints ``{"state": ..., "token": {...}, "age_minutes": ...}``. It always exits 0.
- ``release`` removes the token. With ``--holder`` it refuses (exit 3) when someone else holds it; ``--force`` removes it
  whoever holds it and prints what it removed, for the daily digest. Releasing a free token is harmless (exit 0); a token
  path that cannot be removed (a folder, for example) exits 1.

When a board file exists next to the token (or at ``--board``), acquire and release mirror ``{holder, since, purpose}`` into
its ``blender_token`` field under the board's lock (board.py); a failure there is reported but does not undo the token.

Standard library only. Workers never hold the token; only the Blender lane and the commander do.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

try:  # imported as part of the tools.poc.orchestration package
    from . import board as _board
except ImportError:  # run as a script, or with this folder on sys.path
    import board as _board

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_TOKEN = ROOT / 'output' / 'poc' / '.blender.token'
STALE_AFTER = timedelta(minutes=30)
WAIT_POLL_S = 1.0

EXIT_BAD = 1
EXIT_HELD = 3

pid_alive = _board.pid_alive


class TokenHeld(Exception):
    """Someone else holds the token (exit 3)."""


class TokenError(Exception):
    """The token file cannot be used (exit 1)."""


def now() -> datetime:
    return datetime.now().astimezone()


def parse_time(text):
    """An ISO time as an aware datetime (a naive time is read as local time), or None."""
    if not isinstance(text, str):
        return None
    try:
        when = datetime.fromisoformat(text.strip())
    except ValueError:
        return None
    return when.astimezone() if when.tzinfo is None else when


def read_token(path: Path):
    """The token's content as a dict, or None when there is no token.

    Content that cannot be read (not UTF-8, not a JSON object, a folder in its place) gives ``{'unreadable': True}``.
    """
    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError:
        return None
    except OSError:
        return {'unreadable': True}
    try:
        data = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        return {'unreadable': True}
    return data if isinstance(data, dict) else {'unreadable': True}


def state(path: Path, when: datetime | None = None, alive=pid_alive) -> dict:
    """``{'state': free|held|stale|unreadable, 'token': {...} or None, 'age_minutes': float or None}``."""
    token = read_token(path)
    if token is None:
        return {'state': 'free', 'token': None, 'age_minutes': None}
    since = None if token.get('unreadable') else parse_time(token.get('since'))
    if since is None:  # hand-written, half-written or unreadable token: fall back to the file's time
        try:
            since = datetime.fromtimestamp(Path(path).stat().st_mtime).astimezone()
        except (OSError, ValueError):
            since = None
    when = when or now()
    age = (when - since) if since else None
    age_minutes = round(age.total_seconds() / 60, 1) if age is not None else None
    if token.get('unreadable'):
        return {'state': 'unreadable', 'token': None, 'age_minutes': age_minutes}
    stale = age is not None and age > STALE_AFTER and not alive(token.get('pid'))
    return {'state': 'stale' if stale else 'held', 'token': token, 'age_minutes': age_minutes}


def acquire(path: Path, holder: str, purpose: str, pid: int | None = None):
    """Create the token. Return the token dict, or None when it is already held. Raise TokenError when it cannot."""
    path = Path(path)
    token = {'holder': holder, 'since': now().isoformat(timespec='seconds'), 'purpose': purpose,
             'pid': pid if pid is not None else os.getppid()}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_BINARY', 0), 0o644)
    except FileExistsError:
        return None
    except PermissionError:
        if path.exists():  # Windows reports a folder, or a token being deleted, as access denied
            return None
        raise TokenError(f'cannot create {path} (access denied)') from None
    except OSError as exc:
        raise TokenError(f'cannot create {path} ({exc})') from None
    with os.fdopen(fd, 'wb') as handle:
        handle.write((json.dumps(token, ensure_ascii=False) + '\n').encode('utf-8'))
    return token


def acquire_waiting(path: Path, holder: str, purpose: str, pid: int | None = None, wait_s: float = 0.0,
                    poll_s: float = WAIT_POLL_S):
    """Like acquire, but retry every ``poll_s`` seconds for up to ``wait_s`` seconds. Return the token or None."""
    deadline = time.monotonic() + max(wait_s, 0.0)
    while True:
        token = acquire(path, holder, purpose, pid)
        remaining = deadline - time.monotonic()
        if token is not None or remaining <= 0:
            return token
        time.sleep(min(poll_s, remaining))


def release(path: Path, holder: str | None = None, force: bool = False):
    """Remove the token and return what it held (None when it was free).

    Raise TokenHeld when ``holder`` is given, does not match and ``force`` is off; TokenError when it cannot be removed.
    """
    token = read_token(path)
    if token is None:
        return None
    if holder is not None and not force and token.get('holder') != holder:
        who = 'an unreadable token' if token.get('unreadable') else repr(token.get('holder'))
        raise TokenHeld(f'the token is held by {who}, not {holder!r}; use --force to clear it')
    path = Path(path)
    if path.is_dir():
        raise TokenError(f'{path} is a folder, not a token file; remove it by hand if that is right')
    try:
        os.unlink(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise TokenError(f'cannot remove {path} ({exc})') from None
    return token


def mirror(board_path: Path, token) -> str | None:
    """Copy {holder, since, purpose} into the board's blender_token under the board's lock (when the board exists).

    Return a warning or None.
    """
    board_path = Path(board_path)
    if not board_path.is_file():
        return None
    try:
        with _board.locked(board_path):
            board = _board.load(board_path)
            board['blender_token'] = {k: (token or {}).get(k) for k in ('holder', 'since', 'purpose')}
            _board.save(board_path, board)
    except (_board.BoardError, OSError) as exc:
        return f'blender_token not mirrored into the board: {exc}'
    return None


def describe(token) -> str:
    if not token:
        return 'nobody'
    if token.get('unreadable'):
        return 'an unreadable token'
    return f'{token.get("holder")} since {token.get("since")} for {token.get("purpose")!r} (pid {token.get("pid")})'


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='The POC Blender token: acquire before starting Blender (preview, build, verify, synth_render.py, '
                    'run_tests.py --require-native), release after. acquire exits 3 when the token is held (--wait '
                    'retries once a second); status marks a token stale after 30 minutes when its process is gone; '
                    'release --force clears it.')
    sub = parser.add_subparsers(dest='command', required=True)

    def common(p):
        p.add_argument('--token', type=Path, default=DEFAULT_TOKEN,
                       help='token file (default: output/poc/.blender.token)')
        p.add_argument('--board', type=Path, default=None,
                       help='board to mirror into (default: board.json next to the token, when it exists)')
        return p

    p = common(sub.add_parser('acquire', help='take the token (exit 3 when it is held)'))
    p.add_argument('--holder', required=True, help='who holds it, e.g. blender-lane')
    p.add_argument('--purpose', required=True, help='what Blender will do')
    p.add_argument('--pid', type=int, default=None, help='process that holds the token (default: the parent process)')
    p.add_argument('--wait', type=float, default=0.0, metavar='SECONDS',
                   help='when held, retry once a second for up to this many seconds before exiting 3 (default: 0, '
                        'exit 3 at once)')

    p = common(sub.add_parser('release', help='give the token back'))
    p.add_argument('--holder', default=None, help='refuse unless this is the holder')
    p.add_argument('--force', action='store_true', help='remove it whoever holds it (note it in the digest)')

    p = common(sub.add_parser('status', help='print free, held, stale or unreadable'))
    p.add_argument('--json', action='store_true', help='print the state as JSON')
    return parser


def run(args) -> int:
    board = args.board if args.board is not None else Path(args.token).parent / 'board.json'
    if args.command == 'acquire':
        if args.wait < 0:
            raise TokenError('--wait must be >= 0')
        token = acquire_waiting(args.token, args.holder, args.purpose, args.pid, args.wait)
        if token is None:
            current = state(args.token)
            waited = f' after waiting {args.wait:g} s' if args.wait > 0 else ''
            raise TokenHeld(f'the token is {current["state"]}, held by {describe(current["token"])}{waited}; wait, '
                            f'do not run Blender in parallel')
        warning = mirror(board, token)
        if warning:
            print(f'warning: {warning}', file=sys.stderr)
        print(f'acquired by {describe(token)}')
        return 0
    if args.command == 'release':
        removed = release(args.token, args.holder, args.force)
        if removed is None:
            print('the token was free; nothing to release')
            return 0
        warning = mirror(board, None)
        if warning:
            print(f'warning: {warning}', file=sys.stderr)
        print(('force-released' if args.force else 'released') + f': {describe(removed)}')
        return 0
    current = state(args.token)
    if args.json:
        print(json.dumps(current, ensure_ascii=False))
    elif current['state'] == 'free':
        print('free')
    elif current['state'] == 'unreadable':
        print(f'unreadable: {args.token} is not a readable token; the commander may "release --force" and note it '
              f'in the digest')
    else:
        print(f'{current["state"]}: {describe(current["token"])}, held {current["age_minutes"]} min'
              + (' and its process is gone; the commander may "release --force" and note it in the digest'
                 if current['state'] == 'stale' else ''))
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except TokenHeld as exc:
        print(f'held: {exc}', file=sys.stderr)
        return EXIT_HELD
    except (TokenError, OSError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return EXIT_BAD


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    raise SystemExit(main())
