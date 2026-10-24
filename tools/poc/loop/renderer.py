"""The real renderer and scorer of the loop: command lines and subprocess calls. Nothing here starts Blender by itself in tests.

Per candidate (docs/poc/packages/P8a-loop-skeleton.md):
  1. boothctl.py design-compile --design <design.json> --out <cand>/compile --fit <box_fit.json> --image <picture>
     (--fit and --image go together; no Blender)
  2. blender_token.py acquire --wait <s> --holder <name> --purpose <text>     (the token is held only around the next step; <s> is
     at most the time left of --max-minutes)
  3. boothctl.py preview --spec <cand>/compile/spec.json --out-root <cand>/previews [--blender <exe>]   (one out-root per candidate)
  4. blender_token.py release --holder <name>                                  (always, also when the preview failed)
  5. score.py --run <preview folder> --photo <picture> --truth <truth> --fit <fit file> --out <cand>/score.json --candidate-id <id> [--map <json>]
     (<fit file> is the fit design-compile used: step 1 leaves a copy of it in the candidate folder as fit.json, and the scorer passes it on)

TODO (before P8c): run the real mode once against Blender by hand; the command lines are asserted by tests, the run itself is not.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from collections import namedtuple
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BOOTHCTL = ROOT / 'booth-image-to-model' / 'scripts' / 'boothctl.py'
TOKEN_SCRIPT = ROOT / 'tools' / 'poc' / 'orchestration' / 'blender_token.py'
SCORE_SCRIPT = ROOT / 'tools' / 'poc' / 'score' / 'score.py'

FIT_COPY = 'fit.json'          # the loop's own fit file, copied into each candidate folder for score.py --fit

Completed = namedtuple('Completed', 'returncode stdout stderr')


class RenderError(RuntimeError):
    """A compile, preview or score step failed; the candidate is dropped from the round."""


def compile_cmd(design_path, out_dir, fit=None, image=None):
    if (fit is None) != (image is None):
        raise ValueError('--fit and --image must be given together')
    cmd = [sys.executable, str(BOOTHCTL), 'design-compile', '--design', str(design_path), '--out', str(out_dir)]
    if fit is not None:
        cmd += ['--fit', str(fit), '--image', str(image)]
    return cmd


def preview_cmd(spec, out_root, blender=None):
    cmd = [sys.executable, str(BOOTHCTL), 'preview', '--spec', str(spec), '--out-root', str(out_root)]
    if blender:
        cmd += ['--blender', str(blender)]
    return cmd


def token_cmd(action, holder, purpose=None, wait=None):
    cmd = [sys.executable, str(TOKEN_SCRIPT), action]
    if action == 'acquire':
        if wait is not None:
            cmd += ['--wait', f'{wait:g}']
        cmd += ['--holder', holder, '--purpose', purpose or 'preview']
    else:
        cmd += ['--holder', holder]
    return cmd


def score_cmd(run_dir, photo, truth, out, candidate_id, map_path=None, fit=None):
    """score.py needs --fit (it exits 2 without); RealScorer never builds a command without one."""
    cmd = [sys.executable, str(SCORE_SCRIPT), '--run', str(run_dir), '--photo', str(photo), '--truth', str(truth),
           '--out', str(out), '--candidate-id', candidate_id]
    if fit:
        cmd += ['--fit', str(fit)]
    if map_path:
        cmd += ['--map', str(map_path)]
    return cmd


def _run(cmd, **kw):
    p = subprocess.run(cmd, capture_output=True, text=True, **kw)
    return Completed(p.returncode, p.stdout, p.stderr)


def _tail(proc, n=400):
    return (proc.stderr or proc.stdout or '').strip()[-n:]


class BoothRenderer:
    """design.json -> compiled spec -> preview folder (preview_render.png, spec.json). `runner(cmd)` returns a Completed."""

    def __init__(self, fit, image, holder='loop', blender=None, wait_s=600.0, runner=_run):
        self.fit, self.image, self.holder, self.blender, self.wait_s, self.runner = fit, image, holder, blender, wait_s, runner

    def compile_design(self, design):
        """design-compile (no Blender, no token) in a scratch folder. -> the spec; RenderError when the design does not compile."""
        with tempfile.TemporaryDirectory(prefix='p8_compile_') as tmp:
            folder = Path(tmp)
            (folder / 'design.json').write_bytes((json.dumps(design, indent=2, ensure_ascii=False) + '\n').encode('utf-8'))
            done = self.runner(compile_cmd(folder / 'design.json', folder / 'compile', self.fit, self.image))
            if done.returncode != 0 or not (folder / 'compile' / 'spec.json').is_file():
                raise RenderError(f'design-compile failed (exit {done.returncode}): {_tail(done)}')
            return json.loads((folder / 'compile' / 'spec.json').read_text(encoding='utf-8'))

    def render(self, cand_dir, design, candidate_id, time_left_s=None):
        cand_dir = Path(cand_dir)
        compiled, previews = cand_dir / 'compile', cand_dir / 'previews'
        done = self.runner(compile_cmd(cand_dir / 'design.json', compiled, self.fit, self.image))
        if done.returncode != 0 or not (compiled / 'spec.json').is_file():
            raise RenderError(f'design-compile failed for {candidate_id} (exit {done.returncode}): {_tail(done)}')
        if Path(self.fit).is_file():                      # the scorer finds the fit of this candidate here (RealScorer)
            shutil.copyfile(self.fit, cand_dir / FIT_COPY)
        wait = self.wait_s if time_left_s is None else min(self.wait_s, max(0.0, float(time_left_s)))     # never past --max-minutes
        got = self.runner(token_cmd('acquire', self.holder, f'preview {candidate_id}', wait))
        if got.returncode != 0:
            raise RenderError(f'the Blender token was not free for {candidate_id}: {_tail(got)}')
        try:
            done = self.runner(preview_cmd(compiled / 'spec.json', previews, self.blender))
        finally:
            self.runner(token_cmd('release', self.holder))
        if done.returncode != 0:
            raise RenderError(f'preview failed for {candidate_id} (exit {done.returncode}): {_tail(done)}')
        found = sorted(p for p in previews.glob('preview_*') if (p / 'preview_render.png').is_file())
        if not found:
            raise RenderError(f'preview left no preview_render.png for {candidate_id}')
        return found[-1]


class RealScorer:
    """Runs tools/poc/score/score.py in the candidate's own folder (it writes sheet.png and friends next to score.json).

    score.py needs the picture's corner fit (--fit): `fit` when given, else the copy of the loop's own fit that BoothRenderer.render left
    in the candidate folder (fit.json). With neither the candidate is dropped (RenderError), score.py is not called."""

    def __init__(self, photo, truth, map_path=None, runner=_run, fit=None):
        self.photo, self.truth, self.map_path, self.runner, self.fit = photo, truth, map_path, runner, fit

    def score(self, cand_dir, preview_dir, candidate_id, design):
        cand_dir = Path(cand_dir)
        out = cand_dir / 'score.json'
        fit = self.fit if self.fit else (cand_dir / FIT_COPY if (cand_dir / FIT_COPY).is_file() else None)
        if fit is None:
            raise RenderError(f'no fit file for {candidate_id}: score.py needs --fit, and the renderer left no {FIT_COPY} in {cand_dir}')
        done = self.runner(score_cmd(preview_dir, self.photo, self.truth, out, candidate_id, self.map_path, fit=fit))
        if done.returncode != 0 or not out.is_file():
            raise RenderError(f'score.py failed for {candidate_id} (exit {done.returncode}): {_tail(done)}')
        return json.loads(out.read_text(encoding='utf-8'))
