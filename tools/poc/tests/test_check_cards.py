"""The task-card checker: front matter parses, ids are unique, dependencies form a DAG, allowed and forbidden files do not
overlap, and every acceptance command is a python command whose script exists or is delivered by the card or a card it
depends on (transitively).

Fixture cards and a fixture repository root live in a temporary folder.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CHECK = ROOT / 'tools' / 'poc' / 'orchestration' / 'check_cards.py'


def card_text(card_id, depends_on=(), allowed=('tools/poc/a.py',), forbidden=('web/**',), deliverables=(),
              acceptance=('python tools/check_lf.py',), extra='', drop=()):
    def block(key, items):
        if not items:
            return f'{key}: []\n'
        return f'{key}:\n' + ''.join(f'  - {item}\n' for item in items)

    fields = [
        f'id: {card_id}\n', 'title: 一个标题：带全角冒号\n', 'goal: g\n',
        'depends_on: [' + ', '.join(depends_on) + ']\n', 'model: sonnet\n', 'lane: worker\n',
        block('allowed_files', allowed), block('forbidden_files', forbidden), block('inputs', ['docs/x.md 第 1 节']),
        block('deliverables', deliverables), block('tests_first', ['test_something']),
        block('acceptance_cmds', acceptance), 'effort_h: 2\n', 'day: 1-2\n', 'stop_rule: s\n',
    ]
    fields = [f for f in fields if f.split(':', 1)[0] not in drop]
    return '---\n' + ''.join(fields) + extra + f'---\n\n# {card_id}\n'


class CardsCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'repo'
        self.cards = self.root / 'docs' / 'poc' / 'packages'
        self.cards.mkdir(parents=True)
        (self.root / 'tools').mkdir()
        (self.root / 'tools' / 'check_lf.py').write_text('', encoding='utf-8')
        (self.root / 'tools' / 'tests').mkdir()
        (self.cards / 'README.md').write_text('# not a card\n', encoding='utf-8')

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text):
        (self.cards / name).write_text(text, encoding='utf-8', newline='\n')

    def check(self):
        return subprocess.run([sys.executable, str(CHECK), str(self.cards), '--root', str(self.root)],
                              capture_output=True, text=True, encoding='utf-8', cwd=ROOT)

    def assert_ok(self):
        done = self.check()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return done

    def assert_problem(self, *fragments):
        done = self.check()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        for fragment in fragments:
            self.assertIn(fragment, done.stdout)
        return done


class FrontMatter(CardsCase):
    def test_cards_front_matter_parses_ids_unique_dag_acyclic(self):
        self.write('P1-a.md', card_text('P1'))
        self.write('P2-b.md', card_text('P2', depends_on=['P1']))
        self.write('P3-c.md', card_text('P3', depends_on=['P1', 'P2'], forbidden=['"*"']))
        self.assert_ok()

        # The parser reads scalars, inline and block lists, quoted items and full-width punctuation.
        sys.path.insert(0, str(CHECK.parent))
        try:
            import check_cards
        finally:
            sys.path.remove(str(CHECK.parent))
        card = check_cards.parse_front_matter((self.cards / 'P3-c.md').read_text(encoding='utf-8'))
        self.assertEqual(card['id'], 'P3')
        self.assertEqual(card['depends_on'], ['P1', 'P2'])
        self.assertEqual(card['forbidden_files'], ['*'])
        self.assertEqual(card['title'], '一个标题：带全角冒号')
        self.assertEqual(card['effort_h'], 2)
        self.assertEqual(card['day'], '1-2')
        self.assertEqual(card['inputs'], ['docs/x.md 第 1 节'])

        # Duplicate id.
        self.write('P1-dup.md', card_text('P1'))
        self.assert_problem('duplicate id P1')
        (self.cards / 'P1-dup.md').unlink()

        # A dependency on a card that does not exist.
        self.write('P4-d.md', card_text('P4', depends_on=['P404']))
        self.assert_problem('P404')
        (self.cards / 'P4-d.md').unlink()

        # A cycle P1 -> P3 -> P1 (P3 already depends on P1).
        self.write('P1-a.md', card_text('P1', depends_on=['P3']))
        self.assert_problem('cycle')
        self.write('P1-a.md', card_text('P1'))
        self.assert_ok()

        # Missing front matter, a missing field, an unknown field, a bad enum.
        self.write('P5-e.md', '# no front matter\n')
        self.assert_problem('P5-e.md')
        self.write('P5-e.md', card_text('P5', drop=('stop_rule',)))
        self.assert_problem('stop_rule')
        self.write('P5-e.md', card_text('P5', extra='colour: blue\n'))
        self.assert_problem('colour')
        self.write('P5-e.md', card_text('P5').replace('model: sonnet', 'model: gpt'))
        self.assert_problem('model')
        self.write('P5-e.md', card_text('P5'))
        self.assert_ok()

    def test_cards_allowed_and_forbidden_disjoint(self):
        self.write('P1-a.md', card_text('P1', allowed=['booth-image-to-model/scripts/motifs.py'],
                                        forbidden=['booth-image-to-model/**']))
        self.assert_problem('P1', 'booth-image-to-model/scripts/motifs.py', 'booth-image-to-model/**')
        # "*" means "everything else" and never overlaps.
        self.write('P1-a.md', card_text('P1', allowed=['output/poc/baseline/**'], forbidden=['"*"']))
        self.assert_ok()
        # A single star does not cross a slash.
        self.write('P1-a.md', card_text('P1', allowed=['docs/poc/templates/board.schema.json'],
                                        forbidden=['docs/poc/*.md']))
        self.assert_ok()
        self.write('P1-a.md', card_text('P1', allowed=['docs/poc/README.md'], forbidden=['docs/poc/*.md']))
        self.assert_problem('docs/poc/README.md')
        # Two patterns overlap when one path matches both.
        self.write('P1-a.md', card_text('P1', allowed=['tools/poc/tests/fixtures/**'], forbidden=['tools/**/*.png']))
        self.assert_problem('tools/**/*.png')
        self.write('P1-a.md', card_text('P1', allowed=['tools/poc/tests/fixtures/**'], forbidden=['web/**', 'tools/eval/**']))
        self.assert_ok()

    def test_acceptance_cmds_are_python_with_a_known_script(self):
        # Prefix: python or "BLENDER_EXE= python".
        self.write('P1-a.md', card_text('P1', acceptance=['pip install numpy']))
        self.assert_problem('pip install numpy')
        self.write('P1-a.md', card_text('P1', acceptance=['BLENDER_EXE= python -m unittest discover -s tools/tests -p "t*.py"',
                                                          'python -m json.tool output/poc/truth/pic_A/inventory.json']))
        self.assert_ok()
        # A script that neither exists nor is delivered.
        self.write('P1-a.md', card_text('P1', acceptance=['python tools/poc/score/score.py --help']))
        self.assert_problem('tools/poc/score/score.py')
        # Delivered by the card itself (path followed by full-width punctuation).
        self.write('P1-a.md', card_text('P1', acceptance=['python tools/poc/score/score.py --help'],
                                        deliverables=['tools/poc/score/score.py：--run --out']))
        self.assert_ok()
        # Delivered by a card two dependency steps away; not by an unrelated card.
        self.write('P2-b.md', card_text('P2', depends_on=['P1']))
        self.write('P3-c.md', card_text('P3', depends_on=['P2'], acceptance=['python tools/poc/score/score.py --help']))
        self.assert_ok()
        self.write('P3-c.md', card_text('P3', acceptance=['python tools/poc/score/score.py --help']))
        self.assert_problem('P3', 'tools/poc/score/score.py')
        (self.cards / 'P3-c.md').unlink()
        # The start folder of a unittest discovery must exist or be delivered.
        self.write('P2-b.md', card_text('P2', acceptance=['python -m unittest discover -s tools/poc/nowhere -p "t*.py"']))
        self.assert_problem('tools/poc/nowhere')
        self.write('P2-b.md', card_text('P2', acceptance=['python -m unittest discover -s tools/poc/nowhere -p "t*.py"'],
                                        deliverables=['tools/poc/nowhere/fixtures/：小图']))
        self.assert_ok()

    def test_json_report_lists_every_card(self):
        self.write('P1-a.md', card_text('P1'))
        self.write('P2-b.md', card_text('P2', depends_on=['P1']))
        done = subprocess.run([sys.executable, str(CHECK), str(self.cards), '--root', str(self.root), '--json'],
                              capture_output=True, text=True, encoding='utf-8', cwd=ROOT)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        report = json.loads(done.stdout)
        self.assertEqual(sorted(report['cards']), ['P1', 'P2'])
        self.assertEqual(report['problems'], [])


if __name__ == '__main__':
    unittest.main()
