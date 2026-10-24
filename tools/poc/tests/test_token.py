"""The Blender token: one holder at a time, a stale mark after 30 minutes when the holding process is gone.

The token file lives in a temporary folder (``--token``); the tests never touch output/poc/.blender.token.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TOKEN = ROOT / 'tools' / 'poc' / 'orchestration' / 'blender_token.py'


def run(*args):
    return subprocess.run([sys.executable, str(TOKEN), *map(str, args)], capture_output=True, text=True,
                          encoding='utf-8', cwd=ROOT)


def finished_process():
    """A process that has exited. The Popen object is kept so its id cannot be reused while the test runs."""
    proc = subprocess.Popen([sys.executable, '-c', 'pass'])
    proc.wait()
    return proc


class Token(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.token = self.dir / 'poc' / '.blender.token'

    def tearDown(self):
        self.tmp.cleanup()

    def status(self):
        done = run('status', '--token', self.token, '--json')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return json.loads(done.stdout)

    def plant(self, minutes_ago, pid):
        """Write a token as acquire would have written it some minutes ago."""
        since = (datetime.now().astimezone() - timedelta(minutes=minutes_ago)).isoformat(timespec='seconds')
        self.token.parent.mkdir(parents=True, exist_ok=True)
        self.token.write_text(json.dumps({'holder': 'lane', 'since': since, 'purpose': 'preview', 'pid': pid}),
                              encoding='utf-8')

    def test_token_is_exclusive_and_expires_after_30_minutes(self):
        self.assertEqual(self.status()['state'], 'free')
        first = run('acquire', '--holder', 'blender-lane', '--purpose', 'preview pic_A', '--token', self.token)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        held = json.loads(self.token.read_text(encoding='utf-8'))
        self.assertEqual((held['holder'], held['purpose']), ('blender-lane', 'preview pic_A'))
        self.assertIsInstance(held['pid'], int)
        self.assertIsNotNone(held['since'])
        second = run('acquire', '--holder', 'other', '--purpose', 'build', '--token', self.token)
        self.assertEqual(second.returncode, 3, 'a held token must not be acquired again')
        self.assertEqual(json.loads(self.token.read_text(encoding='utf-8'))['holder'], 'blender-lane')
        self.assertIn(self.status()['state'], ('held', 'stale'))

        gone = finished_process()
        # Held for 29 minutes by a process that is gone: not stale yet.
        self.plant(29, gone.pid)
        self.assertEqual(self.status()['state'], 'held')
        # Held for 31 minutes by a process that is gone: stale.
        self.plant(31, gone.pid)
        self.assertEqual(self.status()['state'], 'stale')
        # Held for 31 minutes by a live process: still held.
        self.plant(31, os.getpid())
        self.assertEqual(self.status()['state'], 'held')
        # A stale token still blocks acquire; only a release clears it.
        self.plant(31, gone.pid)
        self.assertEqual(run('acquire', '--holder', 'x', '--purpose', 'y', '--token', self.token).returncode, 3)

    def test_release_checks_the_holder_unless_forced(self):
        self.assertEqual(run('acquire', '--holder', 'lane', '--purpose', 'p', '--token', self.token).returncode, 0)
        wrong = run('release', '--holder', 'someone-else', '--token', self.token)
        self.assertNotEqual(wrong.returncode, 0)
        self.assertTrue(self.token.exists())
        forced = run('release', '--force', '--token', self.token)
        self.assertEqual(forced.returncode, 0, forced.stdout + forced.stderr)
        self.assertIn('lane', forced.stdout, 'a forced release says what it removed, for the digest')
        self.assertFalse(self.token.exists())
        self.assertEqual(run('acquire', '--holder', 'lane', '--purpose', 'p', '--token', self.token).returncode, 0)
        self.assertEqual(run('release', '--holder', 'lane', '--token', self.token).returncode, 0)
        self.assertEqual(self.status()['state'], 'free')
        self.assertEqual(run('release', '--token', self.token).returncode, 0, 'releasing a free token is harmless')

    def test_two_processes_acquiring_at_once_only_one_wins(self):
        procs = [subprocess.Popen([sys.executable, str(TOKEN), 'acquire', '--holder', f'h{i}', '--purpose', 'race',
                                   '--token', str(self.token)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  cwd=ROOT) for i in range(6)]
        codes = sorted(p.wait() for p in procs)
        self.assertEqual(codes, [0, 3, 3, 3, 3, 3])

    def test_acquire_and_release_mirror_into_the_board_next_to_the_token(self):
        board = self.token.parent / 'board.json'
        self.token.parent.mkdir(parents=True)
        board.write_text(json.dumps({'schema_version': 'board.v1', 'updated': '2026-01-01T00:00:00', 'day': 1,
                                     'packages': {}, 'leases': {},
                                     'blender_token': {'holder': None, 'since': None, 'purpose': None}}),
                         encoding='utf-8')
        self.assertEqual(run('acquire', '--holder', 'lane', '--purpose', 'p', '--token', self.token).returncode, 0)
        mirrored = json.loads(board.read_text(encoding='utf-8'))['blender_token']
        self.assertEqual((mirrored['holder'], mirrored['purpose']), ('lane', 'p'))
        self.assertIsNotNone(mirrored['since'])
        self.assertEqual(run('release', '--token', self.token).returncode, 0)
        self.assertEqual(json.loads(board.read_text(encoding='utf-8'))['blender_token'],
                         {'holder': None, 'since': None, 'purpose': None})

    def test_unreadable_token_is_reported_and_only_cleared_by_release(self):
        self.token.parent.mkdir(parents=True)
        self.token.write_bytes(b'\xff\xfe\x80 not utf-8 \xc3\x28')
        acquired = run('acquire', '--holder', 'lane', '--purpose', 'p', '--token', self.token)
        self.assertEqual(acquired.returncode, 3, acquired.stdout + acquired.stderr)
        self.assertNotIn('Traceback', acquired.stderr)
        self.assertEqual(self.status()['state'], 'unreadable')
        plain = run('status', '--token', self.token)
        self.assertEqual(plain.returncode, 0, plain.stderr)
        self.assertIn('unreadable', plain.stdout)
        self.assertEqual(run('release', '--holder', 'lane', '--token', self.token).returncode, 3)
        forced = run('release', '--force', '--token', self.token)
        self.assertEqual(forced.returncode, 0, forced.stdout + forced.stderr)
        self.assertFalse(self.token.exists())
        self.assertEqual(self.status()['state'], 'free')

        # A folder where the token should be: no traceback anywhere, and release --force does not remove the folder.
        self.token.mkdir()
        for args in (('status', '--json'), ('acquire', '--holder', 'x', '--purpose', 'y'), ('release', '--force')):
            done = run(*args, '--token', self.token)
            self.assertNotIn('Traceback', done.stderr, args)
        self.assertEqual(self.status()['state'], 'unreadable')
        self.assertNotEqual(run('acquire', '--holder', 'x', '--purpose', 'y', '--token', self.token).returncode, 0)
        self.assertEqual(run('release', '--force', '--token', self.token).returncode, 1)
        self.assertTrue(self.token.is_dir())

    def test_acquire_wait_retries_until_the_token_is_free_or_time_is_up(self):
        self.assertEqual(run('acquire', '--holder', 'first', '--purpose', 'p', '--token', self.token).returncode, 0)
        began = time.monotonic()
        timed_out = run('acquire', '--holder', 'second', '--purpose', 'p', '--wait', '2', '--token', self.token)
        self.assertEqual(timed_out.returncode, 3)
        self.assertGreaterEqual(time.monotonic() - began, 1.8, 'it waited about two seconds')
        waiter = subprocess.Popen([sys.executable, str(TOKEN), 'acquire', '--holder', 'second', '--purpose', 'p',
                                   '--wait', '20', '--token', str(self.token)], stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, encoding='utf-8', cwd=ROOT)
        time.sleep(1.5)
        self.assertIsNone(waiter.poll(), 'still waiting while the token is held')
        self.assertEqual(run('release', '--holder', 'first', '--token', self.token).returncode, 0)
        self.assertEqual(waiter.wait(timeout=30), 0, waiter.communicate())
        self.assertEqual(json.loads(self.token.read_text(encoding='utf-8'))['holder'], 'second')

    def test_mirror_waits_for_the_board_lock(self):
        board = self.token.parent / 'board.json'
        self.token.parent.mkdir(parents=True)
        board.write_text(json.dumps({'schema_version': 'board.v1', 'updated': '2026-01-01T00:00:00', 'day': 1,
                                     'packages': {}, 'leases': {},
                                     'blender_token': {'holder': None, 'since': None, 'purpose': None}}),
                         encoding='utf-8')
        lock = Path(str(board) + '.lock')
        lock.write_text(json.dumps({'pid': os.getpid(), 'since': 'now'}), encoding='utf-8')  # a live holder: this test
        began = time.monotonic()
        proc = subprocess.Popen([sys.executable, str(TOKEN), 'acquire', '--holder', 'lane', '--purpose', 'p',
                                 '--token', str(self.token)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding='utf-8', cwd=ROOT)
        time.sleep(1.5)
        self.assertIsNone(proc.poll(), 'the mirror waits while the board is locked')
        lock.unlink()
        self.assertEqual(proc.wait(timeout=30), 0, proc.communicate())
        self.assertGreaterEqual(time.monotonic() - began, 1.4)
        self.assertEqual(json.loads(board.read_text(encoding='utf-8'))['blender_token']['holder'], 'lane')

    def test_help_names_every_command(self):
        done = run('--help')
        self.assertEqual(done.returncode, 0)
        for word in ('acquire', 'release', 'status'):
            self.assertIn(word, done.stdout)


if __name__ == '__main__':
    unittest.main()
