"""Board, gates and digest: the commander's state files (board.v1, gates.v1) and the daily digest skeleton.

Every test works in a temporary folder and drives the scripts through their command line, so exit codes are tested too.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORCH = ROOT / 'tools' / 'poc' / 'orchestration'
BOARD = ORCH / 'board.py'
GATES = ORCH / 'gates.py'
DIGEST = ORCH / 'digest.py'


def run(script, *args):
    return subprocess.run([sys.executable, str(script), *map(str, args)], capture_output=True, text=True,
                          encoding='utf-8', cwd=ROOT)


def write_card(folder, card_id, depends_on=()):
    """A minimal task card: board init only needs the id."""
    deps = '[' + ', '.join(depends_on) + ']'
    text = (f'---\nid: {card_id}\ntitle: t\ngoal: g\ndepends_on: {deps}\nmodel: sonnet\nlane: worker\n'
            f'allowed_files:\n  - tools/poc/x_{card_id}.py\nforbidden_files:\n  - web/**\ninputs: []\n'
            f'deliverables: []\ntests_first: []\nacceptance_cmds:\n  - python tools/check_lf.py\n'
            f'effort_h: 1\nday: 1\nstop_rule: s\n---\n\n# {card_id}\n')
    (Path(folder) / f'{card_id}-card.md').write_text(text, encoding='utf-8', newline='\n')


class BoardCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        cards = self.dir / 'cards'
        cards.mkdir()
        for card_id in ('P0', 'P1', 'P2'):
            write_card(cards, card_id)
        self.board = self.dir / 'board.json'
        init = run(BOARD, 'init', '--out', self.board, '--cards', cards)
        self.assertEqual(init.returncode, 0, init.stdout + init.stderr)

    def tearDown(self):
        self.tmp.cleanup()

    def load(self):
        return json.loads(self.board.read_text(encoding='utf-8'))

    def set(self, package, status, *extra):
        return run(BOARD, 'set', package, status, '--board', self.board, *extra)


class Board(BoardCase):
    def test_init_writes_a_valid_board_with_every_card_as_todo(self):
        board = self.load()
        self.assertEqual(board['schema_version'], 'board.v1')
        self.assertEqual(sorted(board['packages']), ['P0', 'P1', 'P2'])
        self.assertTrue(all(p['status'] == 'todo' for p in board['packages'].values()))
        self.assertEqual(board['leases'], {})
        self.assertEqual(board['blender_token'], {'holder': None, 'since': None, 'purpose': None})
        again = run(BOARD, 'init', '--out', self.board, '--cards', self.dir / 'cards')
        self.assertNotEqual(again.returncode, 0, 'init must not overwrite an existing board without --force')

    def test_board_transitions_reject_illegal_state(self):
        # Forward one step at a time.
        for status in ('leased', 'in_progress', 'verify'):
            done = self.set('P0', status)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        # Skipping a step is illegal.
        skip = self.set('P1', 'in_progress')
        self.assertEqual(skip.returncode, 2, 'todo -> in_progress skips leased')
        before = self.board.read_bytes()
        skip = self.set('P1', 'merged')
        self.assertEqual(skip.returncode, 2)
        self.assertEqual(self.board.read_bytes(), before, 'a refused transition must not touch the board')
        # The verifier sends a package back: verify -> in_progress is allowed.
        self.assertEqual(self.set('P0', 'in_progress').returncode, 0)
        self.assertEqual(self.set('P0', 'verify').returncode, 0)
        merged = self.set('P0', 'merged', '--report', 'output/poc/reports/P0.json')
        self.assertEqual(merged.returncode, 0, merged.stdout + merged.stderr)
        p0 = self.load()['packages']['P0']
        self.assertEqual(p0['status'], 'merged')
        self.assertEqual(p0['report_path'], 'output/poc/reports/P0.json')
        self.assertIsNotNone(p0['started'])
        self.assertIsNotNone(p0['finished'])
        # merged is final.
        for status in ('todo', 'leased', 'in_progress', 'verify', 'blocked', 'dropped'):
            self.assertEqual(self.set('P0', status).returncode, 2, f'merged -> {status} must be refused')
        # blocked and dropped: reachable from any open state; blocked returns only where it came from (or todo).
        self.assertEqual(self.set('P1', 'leased').returncode, 0)
        self.assertEqual(self.set('P1', 'blocked', '--note', 'rate_limit').returncode, 0)
        self.assertEqual(self.set('P1', 'verify').returncode, 2, 'blocked must not jump ahead')
        self.assertEqual(self.set('P1', 'leased').returncode, 0)
        self.assertEqual(self.set('P2', 'dropped').returncode, 0)
        self.assertEqual(self.set('P2', 'todo').returncode, 2, 'dropped is final')
        self.assertEqual(self.set('P9', 'leased').returncode, 2, 'an unknown package can only enter as todo')
        self.assertEqual(self.load()['packages']['P1']['status'], 'leased')

    def test_set_records_owner_branch_worktree_and_verifier(self):
        done = self.set('P1', 'leased', '--owner', 'worker-1', '--branch', 'poc/p1', '--worktree', 'wt-p1',
                        '--verifier', 'verifier-1', '--note', 'one line')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        p1 = self.load()['packages']['P1']
        self.assertEqual((p1['owner'], p1['branch'], p1['worktree'], p1['verifier'], p1['note']),
                         ('worker-1', 'poc/p1', 'wt-p1', 'verifier-1', 'one line'))
        shown = run(BOARD, 'show', '--board', self.board)
        self.assertEqual(shown.returncode, 0)
        self.assertIn('poc/p1', shown.stdout)

    def test_board_lease_is_exclusive(self):
        hot = 'booth-image-to-model/scripts/motifs.py'
        first = run(BOARD, 'lease', hot, 'P1', '--board', self.board)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        second = run(BOARD, 'lease', hot, 'P2', '--board', self.board)
        self.assertEqual(second.returncode, 2, 'a leased file must not be leased to another package')
        self.assertEqual(self.load()['leases'], {hot: 'P1'})
        self.assertEqual(run(BOARD, 'lease', hot, 'P1', '--board', self.board).returncode, 0, 'same holder: no-op')
        self.assertNotEqual(run(BOARD, 'lease', 'web/x.js', 'P9', '--board', self.board).returncode, 0,
                            'leases go to known packages only')
        self.assertEqual(run(BOARD, 'unlease', hot, '--board', self.board).returncode, 0)
        self.assertEqual(self.load()['leases'], {})
        self.assertEqual(run(BOARD, 'lease', hot, 'P2', '--board', self.board).returncode, 0)
        self.assertEqual(self.load()['leases'], {hot: 'P2'})


def start(script, *args):
    return subprocess.Popen([sys.executable, str(script), *map(str, args)], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding='utf-8', cwd=ROOT)


class ConcurrentBoard(unittest.TestCase):
    """Several board.py processes at once: the lock file serialises them, so no change is lost or doubled."""
    COUNT = 12

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        cards = self.dir / 'cards'
        cards.mkdir()
        self.ids = [f'P{i}' for i in range(self.COUNT)]
        for card_id in self.ids:
            write_card(cards, card_id)
        self.board = self.dir / 'board.json'
        self.assertEqual(run(BOARD, 'init', '--out', self.board, '--cards', cards).returncode, 0)

    def tearDown(self):
        self.tmp.cleanup()

    def finish(self, procs):
        results = [(p.wait(timeout=60), *p.communicate()) for p in procs]
        for code, _out, err in results:
            self.assertNotIn('Traceback', err)
        return [code for code, _out, _err in results]

    def test_concurrent_lease_of_one_file_has_exactly_one_winner(self):
        for round_number in range(4):
            hot = f'hot/file_{round_number}.py'
            racers = self.ids[:6]
            codes = self.finish([start(BOARD, 'lease', hot, pid, '--board', self.board) for pid in racers])
            self.assertEqual(sorted(codes), [0, 2, 2, 2, 2, 2], f'round {round_number}: {codes}')
            winner = racers[codes.index(0)]
            leases = json.loads(self.board.read_text(encoding='utf-8'))['leases']
            self.assertEqual(leases.get(hot), winner)
        self.assertFalse(Path(str(self.board) + '.lock').exists(), 'the lock is released')

    def test_concurrent_set_of_different_packages_loses_no_update(self):
        codes = self.finish([start(BOARD, 'set', pid, 'leased', '--board', self.board) for pid in self.ids])
        self.assertEqual(codes, [0] * self.COUNT)
        packages = json.loads(self.board.read_text(encoding='utf-8'))['packages']
        self.assertEqual({pid: packages[pid]['status'] for pid in self.ids}, {pid: 'leased' for pid in self.ids})

    def test_a_lock_left_by_a_dead_process_is_reclaimed(self):
        gone = subprocess.Popen([sys.executable, '-c', 'pass'])
        gone.wait()
        lock = Path(str(self.board) + '.lock')
        lock.write_text(json.dumps({'pid': gone.pid, 'since': '2026-01-01T00:00:00'}), encoding='utf-8')
        done = run(BOARD, 'set', 'P0', 'leased', '--board', self.board)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertFalse(lock.exists())


class Gates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.gates = Path(self.tmp.name) / 'gates.json'
        init = run(GATES, 'init', '--out', self.gates)
        self.assertEqual(init.returncode, 0, init.stdout + init.stderr)

    def tearDown(self):
        self.tmp.cleanup()

    def load(self):
        return json.loads(self.gates.read_text(encoding='utf-8'))

    def test_init_copies_the_template_thresholds(self):
        gates = self.load()
        template = json.loads((ROOT / 'docs/poc/templates/gates.example.json').read_text(encoding='utf-8'))
        self.assertEqual(gates['schema_version'], 'gates.v1')
        for gate in ('G1', 'G2', 'G3', 'G4', 'G5'):
            self.assertEqual(gates[gate]['status'], 'pending')
            self.assertEqual(gates[gate]['threshold'], template[gate]['threshold'])

    def test_measure_writes_only_the_measurement(self):
        done = run(GATES, 'measure', 'G2', '--value', '72.5', '--threshold', 'C_total >= 70',
                   '--evidence', 'output/poc/ceiling/pic_A/score.json', '--gates', self.gates)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        g2 = self.load()['G2']
        self.assertEqual(g2['value'], 72.5)
        self.assertEqual(g2['threshold'], 'C_total >= 70')
        self.assertEqual(g2['evidence_path'], 'output/poc/ceiling/pic_A/score.json')
        self.assertIsNotNone(g2['measured_at'])
        self.assertEqual(g2['status'], 'pending', 'measure never decides')
        self.assertIsNone(g2['memo_path'])

    def test_decide_follows_who_may_decide(self):
        for gate in ('G1', 'G2'):
            run(GATES, 'measure', gate, '--value', '0.9', '--threshold', 't', '--evidence', 'e.json',
                '--gates', self.gates)
        self.assertEqual(run(GATES, 'decide', 'G3', 'pass', '--by', 'owner', '--gates', self.gates).returncode, 2,
                         'no decision before a measurement')
        self.assertEqual(run(GATES, 'decide', 'G2', 'pass', '--by', 'commander', '--gates', self.gates).returncode,
                         2, 'G2 to G5 belong to the owner')
        self.assertEqual(run(GATES, 'decide', 'G1', 'waived', '--by', 'commander', '--memo', 'm.md',
                             '--gates', self.gates).returncode, 2, 'only the owner waives')
        self.assertEqual(run(GATES, 'decide', 'G4', 'waived', '--by', 'owner', '--gates', self.gates).returncode, 2,
                         'a waiver needs a memo')
        self.assertEqual(run(GATES, 'decide', 'G1', 'pass', '--by', 'commander', '--gates', self.gates).returncode, 0)
        self.assertEqual(run(GATES, 'decide', 'G2', 'fail', '--by', 'owner', '--gates', self.gates).returncode, 0)
        self.assertEqual(run(GATES, 'decide', 'G4', 'waived', '--by', 'owner', '--memo', 'output/poc/memo/g4.md',
                             '--gates', self.gates).returncode, 0)
        gates = self.load()
        self.assertEqual((gates['G1']['status'], gates['G1']['decided_by']), ('pass', 'commander'))
        self.assertEqual((gates['G2']['status'], gates['G2']['decided_by']), ('fail', 'owner'))
        self.assertEqual((gates['G4']['status'], gates['G4']['memo_path']), ('waived', 'output/poc/memo/g4.md'))
        self.assertEqual(gates['G3']['status'], 'pending')


class Digest(BoardCase):
    def test_digest_has_the_five_sections_and_leaves_numbers_open(self):
        gates = self.dir / 'gates.json'
        self.assertEqual(run(GATES, 'init', '--out', gates).returncode, 0)
        run(GATES, 'measure', 'G1', '--value', '0.83', '--threshold', 'spearman >= 0.8',
            '--evidence', 'output/poc/anchor1/pic_A/rank_check.json', '--gates', gates)
        self.set('P0', 'leased', '--branch', 'poc/p0')
        self.set('P1', 'leased')
        self.set('P1', 'blocked', '--note', 'rate_limit')
        reports = self.dir / 'reports'
        reports.mkdir()
        (reports / 'P0.json').write_text(json.dumps({'schema_version': 'worker.v1', 'package': 'P0',
                                                     'status': 'done'}), encoding='utf-8')
        out = self.dir / 'day_01.md'
        done = run(DIGEST, '--board', self.board, '--gates', gates, '--reports', reports, '--day', '1', '--out', out)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        text = out.read_bytes().decode('utf-8')
        self.assertNotIn('\r', text)
        self.assertTrue(text.startswith('# 日报 第 1 天'))
        for heading in ('## 做了什么', '## 数字', '## 卡在哪', '## 明天做什么', '## 需要决策人定什么'):
            self.assertIn(heading, text)
        self.assertIn('poc/p0', text)
        self.assertIn('rate_limit', text)
        self.assertIn('output/poc/anchor1/pic_A/rank_check.json', text)
        again = run(DIGEST, '--board', self.board, '--gates', gates, '--reports', reports, '--day', '1', '--out', out)
        self.assertNotEqual(again.returncode, 0, 'a written digest is not overwritten without --force')


if __name__ == '__main__':
    unittest.main()
