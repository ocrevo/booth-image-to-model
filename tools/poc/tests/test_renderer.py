"""P1-edge tests for the loop's renderer and scorer command lines: the scorer always gets the loop's own fit file."""
from __future__ import annotations
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0]))        # tools/poc, so that `loop` is a package

from loop import renderer  # noqa: E402


class Runner:
    """Stands in for subprocess: records every command line; the preview step leaves a preview folder, the score step a score.json."""

    def __init__(self, tmp):
        self.tmp, self.calls = Path(tmp), []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if cmd[1] == str(renderer.BOOTHCTL) and cmd[2] == 'design-compile':
            out = Path(cmd[cmd.index('--out') + 1])
            out.mkdir(parents=True, exist_ok=True)
            (out / 'spec.json').write_text('{}', encoding='utf-8')
        if cmd[1] == str(renderer.BOOTHCTL) and cmd[2] == 'preview':
            folder = Path(cmd[cmd.index('--out-root') + 1]) / 'preview_001'
            folder.mkdir(parents=True, exist_ok=True)
            (folder / 'preview_render.png').write_bytes(b'x')
            (folder / 'spec.json').write_text('{}', encoding='utf-8')
        if cmd[1] == str(renderer.SCORE_SCRIPT):
            Path(cmd[cmd.index('--out') + 1]).write_text(json.dumps({'total': 1.0}), encoding='utf-8')
        return renderer.Completed(0, '', '')


class ScoreCommandTests(unittest.TestCase):
    def test_score_cmd_carries_the_fit_file(self):
        tmp = Path('x')
        cmd = renderer.score_cmd(tmp / 'prev', tmp / 'pic.png', tmp / 'truth', tmp / 'c' / 'score.json', 'r1_c0', fit=tmp / 'box_fit.json')
        self.assertEqual(cmd[cmd.index('--fit') + 1], str(tmp / 'box_fit.json'))
        self.assertEqual(cmd[cmd.index('--candidate-id') + 1], 'r1_c0')


class LoopRendererFitTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.fit = self.tmp / 'loop_fit.json'
        self.fit.write_text(json.dumps({'dims_m': {'W': 1, 'D': 1}}), encoding='utf-8')
        self.cand = self.tmp / 'cand'
        self.cand.mkdir()
        (self.cand / 'design.json').write_text('{}', encoding='utf-8')

    def score_calls(self, runner):
        return [c for c in runner.calls if c[1] == str(renderer.SCORE_SCRIPT)]

    def test_the_loop_renderer_passes_the_fit_to_score(self):
        runner = Runner(self.tmp)
        r = renderer.BoothRenderer(fit=self.fit, image=self.tmp / 'p.png', holder='t', runner=runner, wait_s=1)
        preview = r.render(self.cand, {}, 'r1_c0')
        compile_cmd = next(c for c in runner.calls if c[2:3] == ['design-compile'])
        scorer = renderer.RealScorer(self.tmp / 'p.png', self.tmp / 'truth', runner=runner)         # built the way the loop builds it
        score = scorer.score(self.cand, preview, 'r1_c0', {})
        self.assertEqual(score, {'total': 1.0})
        (call,) = self.score_calls(runner)
        passed = Path(call[call.index('--fit') + 1])
        self.assertEqual(json.loads(passed.read_text(encoding='utf-8')), json.loads(self.fit.read_text(encoding='utf-8')),
                         'the fit given to score.py is the one design-compile used')
        self.assertEqual(compile_cmd[compile_cmd.index('--fit') + 1], str(self.fit))

    def test_an_explicit_fit_wins_and_a_scorer_without_any_fit_refuses(self):
        runner = Runner(self.tmp)
        other = self.tmp / 'other_fit.json'
        other.write_text('{}', encoding='utf-8')
        scorer = renderer.RealScorer(self.tmp / 'p.png', self.tmp / 'truth', runner=runner, fit=other)
        scorer.score(self.cand, self.cand / 'previews', 'r1_c0', {})
        (call,) = self.score_calls(runner)
        self.assertEqual(call[call.index('--fit') + 1], str(other))
        runner = Runner(self.tmp)
        bare = renderer.RealScorer(self.tmp / 'p.png', self.tmp / 'truth', runner=runner)
        with self.assertRaises(renderer.RenderError) as ctx:
            bare.score(self.tmp / 'nowhere', self.cand / 'previews', 'r1_c0', {})
        self.assertIn('fit', str(ctx.exception))
        self.assertEqual(self.score_calls(runner), [], 'no score.py call without a fit')


if __name__ == '__main__':
    unittest.main()
