"""Run the evaluation: ground-truth cases -> a skill folder's command-line tools -> metrics with confidence intervals.

    python tools/eval/run_eval.py oracle --skill-dir booth-image-to-model --suite oracle-v1 --seed 20261019 --out output/_eval/runs/now
    python tools/eval/run_eval.py oracle ... --check-corners 0      # the arm that holds no corner out of the fit (default 2: the case's own check corners)
    python tools/eval/run_eval.py replay --skill-dir booth-image-to-model --replies saved_replies/ --cases output/_eval/cases --out output/_eval/runs/replay
    python tools/eval/run_eval.py rescore --dir output/_eval/runs/now          # score stored raw outputs again, skill not called
    python tools/eval/run_eval.py live   ...                      # not implemented in this round

Modes
  oracle  ground-truth corners, perturbed (pixel noise, displaced outliers, swapped labels, "assume all of W/D/H", a wrong assumed
          dimension) -> box_fit.py and `boothctl.py compile` in subprocesses. No model, no Blender, no network; safe for CI.
          --check-corners {0,2} is the arm switch: 2 (default, round-1 behaviour) sends the case's check corners with `fit: false`,
          0 sends every corner as a fitted one; the perturbed points are identical, so two arms can be paired scene by scene.
  replay  re-compile reader replies that were saved earlier: DIR/<case id>/scene.json (and optionally image.png). With --cases pointing
          at generated cases (DIR/<case id>/case.json) the replies are scored against the truth; without, only the compile outcome,
          the fit checks and the gate are reported.
  rescore scores the stored raw outputs of an oracle run again with the current metrics code, without calling the skill.
  live    would call a model on rendered pictures. Needs the owner's go-ahead, an API key and a cost confirmation, so it only says so.

The harness never imports the skill: two versions can be measured one after the other and never share a Python process. What a
skill version supports is detected (skill_caps.py) and the same cases are adapted to it. Outputs: results.json, report.md.
"""
from __future__ import annotations
import argparse
import concurrent.futures
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics
import perturb
import report
import skill_caps
import stats
import synth_generate

HARNESS_VERSION = '1.1.0'
LIVE_MESSAGE = ("live mode is not implemented in this round. It would call a model on rendered pictures, which needs the owner's "
                "go-ahead, an API key and a cost confirmation first. Nothing was called.")


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes((json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode('utf-8'))


def parse_stdout_object(text):
    """The whole of stdout as one JSON object, or None."""
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def call(cmd, cwd, timeout):
    """Run a command; -> (returncode or None on timeout, stdout, stderr, seconds)."""
    # One thread per tool call: the matrices are tiny, and parallel runs must not fight over BLAS threads.
    env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1', OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1',
               MKL_NUM_THREADS='1')
    started = time.perf_counter()
    try:
        p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout, env=env)
    except subprocess.TimeoutExpired as ex:
        return None, '', f'timeout after {ex.timeout} s', time.perf_counter() - started
    return p.returncode, p.stdout or '', p.stderr or '', time.perf_counter() - started


class Context:
    def __init__(self, skill_dir, work, fit_seeds=40, stages=('fit', 'compile'), timeout=300, resume=False, check_corners=2, scale_check=False):
        self.skill_dir = Path(skill_dir).resolve()
        self.caps = skill_caps.detect(self.skill_dir)
        self.work = Path(work).resolve()
        self.fit_seeds, self.stages, self.timeout, self.resume = fit_seeds, tuple(stages), timeout, resume
        self.check_corners, self.scale_check = check_corners, scale_check
        self.python = sys.executable
        self.box_fit = self.skill_dir / 'scripts' / 'box_fit.py'
        self.boothctl = self.skill_dir / 'scripts' / 'boothctl.py'

    def image(self, width, height):
        """A flat grey picture of the right size: the compiler only checks that the file exists and has the declared size.

        Written to a temporary name and renamed into place, so that with --jobs > 1 no worker can hand a half-written PNG to compile."""
        from PIL import Image
        path = self.work / '_images' / f'blank_{width}x{height}.png'
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=path.stem + '.', suffix='.tmp', dir=str(path.parent))
            os.close(fd)
            try:
                Image.new('RGB', (width, height), (128, 128, 128)).save(tmp, format='PNG')
                try:
                    os.replace(tmp, path)                  # atomic on the same volume; a concurrent writer simply wins with identical bytes
                except OSError:
                    if not path.is_file():                 # Windows refuses to replace a file another worker is reading: it is complete then
                        raise
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        return path


def points_from_scene(scene):
    """The points.json that box_fit.py reads, taken from a scene description (the fit flag is kept as written)."""
    return {'image': dict(scene['image']), 'known': {k: v for k, v in scene['known'].items() if v is not None},
            'assumed': list(scene['assumed']), 'floor_z': scene['floor_z'],
            'points': [{'id': p['id'], 'uv_px': p['uv_px'], 'xyz': p['xyz']} for p in scene['corner_points']]}


STALE = ('fit.json', 'points.json', 'scene.json', 'raw.json', 'row.json')


def clear_stale(folder, stages):
    """Remove what an earlier call left in a run folder, so a timeout or crash can never be scored from the previous run's outputs."""
    for name in STALE:
        if name == 'fit.json' and 'fit' not in stages:
            continue
        try:
            (folder / name).unlink()
        except FileNotFoundError:
            pass
    if 'compile' in stages:
        shutil.rmtree(folder / 'compile', ignore_errors=True)


def run_skill(ctx, folder, points_doc, scene_doc, fit_flags, image_path):
    """Call box_fit.py and boothctl compile for one input. -> dict of raw outputs (parsed JSON, return codes, seconds)."""
    folder.mkdir(parents=True, exist_ok=True)
    clear_stale(folder, ctx.stages)
    out = {'fit': None, 'fit_rc': None, 'fit_s': None, 'fit_stdout_single_json': None, 'fit_error': None,
           'report': None, 'spec': None, 'compile_fit': None, 'compile_rc': None, 'compile_s': None, 'compile_summary': None, 'compile_error': None}
    if 'fit' in ctx.stages:
        points = skill_caps.adapt_points(points_doc, ctx.caps, fit_flags)
        write_json(folder / 'points.json', points)
        rc, stdout, stderr, secs = call([ctx.python, str(ctx.box_fit), '--points', 'points.json', '--output', 'fit.json', '--seeds', str(ctx.fit_seeds)],
                                        folder, ctx.timeout)
        out.update(fit_rc=rc, fit_s=secs, fit=read_json(folder / 'fit.json'), fit_stdout_single_json=parse_stdout_object(stdout) is not None)
        if rc is None or (out['fit'] is None):
            out['fit_error'] = (stderr or stdout)[-300:]
    if 'compile' in ctx.stages:
        scene = skill_caps.adapt_scene(scene_doc, ctx.caps, fit_flags)
        write_json(folder / 'scene.json', scene)
        rc, stdout, stderr, secs = call([ctx.python, str(ctx.boothctl), 'compile', '--scene', 'scene.json', '--image', str(image_path),
                                         '--out', 'compile', '--seeds', str(ctx.fit_seeds)], folder, ctx.timeout)
        summary = parse_stdout_object(stdout)
        out.update(compile_rc=rc, compile_s=secs, compile_summary=summary, report=read_json(folder / 'compile' / 'compile_report.json'),
                   spec=read_json(folder / 'compile' / 'spec.json'), compile_fit=read_json(folder / 'compile' / 'box_fit.json'))
        if rc is None or rc != 0:
            out['compile_error'] = json.dumps(summary)[:400] if summary else (stderr or stdout)[-300:]
    write_json(folder / 'raw.json', {k: out[k] for k in RAW_KEYS})
    return out


RAW_KEYS = ('fit_rc', 'fit_s', 'fit_stdout_single_json', 'fit_error', 'compile_rc', 'compile_s', 'compile_summary', 'compile_error')


def load_raw(folder):
    """The raw outputs of an earlier run_skill call, read back from its folder (for rescoring without running the skill)."""
    folder = Path(folder)
    raw = dict(read_json(folder / 'raw.json') or {})
    for k in RAW_KEYS:
        raw.setdefault(k, None)
    raw.update(fit=read_json(folder / 'fit.json'), report=read_json(folder / 'compile' / 'compile_report.json'),
               spec=read_json(folder / 'compile' / 'spec.json'), compile_fit=read_json(folder / 'compile' / 'box_fit.json'))
    return raw


def dims_agree(a, b, tol=1e-3):
    if not (isinstance(a, dict) and isinstance(b, dict)):
        return None
    da, db = a.get('dims_m'), b.get('dims_m')
    if not (isinstance(da, dict) and isinstance(db, dict)) or any(k not in da or k not in db for k in 'WDH'):
        return None
    return all(abs(da[k] - db[k]) <= tol * max(1.0, abs(da[k])) for k in 'WDH')


def failure_text(raw, fit):
    if fit is None:
        return raw.get('fit_error') or raw.get('compile_error') or 'no fit was produced'
    if fit.get('status') == 'blocked':
        return 'box_fit blocked: ' + str(fit.get('reason'))[:200]
    if raw.get('compile_error'):
        return 'compile: ' + str(raw['compile_error'])[:200]
    return None


def score(case, pert, raw):
    """Metrics row from the raw outputs of one run."""
    fit = raw['fit'] if raw['fit'] is not None else raw['compile_fit']
    row = metrics.evaluate_run(case, pert, fit, raw['report'], raw['spec'], {'fit_s': raw['fit_s'], 'compile_s': raw['compile_s']},
                               failure_text(raw, fit))
    row['fit_rc'], row['compile_rc'] = raw['fit_rc'], raw['compile_rc']
    row['stdout_single_json'] = None if raw['fit_stdout_single_json'] is None else (1.0 if raw['fit_stdout_single_json'] else 0.0)
    agree = dims_agree(raw['fit'], raw['compile_fit'])
    row['fit_consistent'] = None if agree is None else (1.0 if agree else 0.0)
    row['compile_valid'] = (raw['report'] or {}).get('valid') if raw['report'] else None
    row['compile_summary_status'] = (raw['compile_summary'] or {}).get('status')
    row['fit_warnings'] = len((fit or {}).get('warnings') or [])
    return row


def oracle_one(ctx, case, scene, pert_name):
    pert = perturb.apply(case, pert_name, ctx.check_corners, ctx.scale_check)
    folder = ctx.work / 'runs' / case['case_id'] / pert_name
    if ctx.resume and (folder / 'row.json').is_file() and (folder / 'raw.json').is_file():     # finished by an earlier, interrupted call
        return score(case, pert, load_raw(folder))
    image = ctx.image(case['image']['width'], case['image']['height'])
    raw = run_skill(ctx, folder, perturb.points_for_run(case, pert), perturb.scene_for_run(scene, case, pert), pert['fit_flags'], image)
    row = score(case, pert, raw)
    write_json(folder / 'row.json', row)
    return row


def run_oracle(ctx, suite, seed, n_cases, pert_names, jobs):
    cases = synth_generate.generate_suite(suite, seed, n_cases)
    tasks = [(case, scene, name) for case, scene in cases for name in pert_names]
    if jobs <= 1:
        rows = [oracle_one(ctx, *t) for t in tasks]
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
            rows = list(pool.map(lambda t: oracle_one(ctx, *t), tasks))
    return rows, len(cases)


def find_image(folder):
    for name in ('image.png', 'image.jpg', 'image.jpeg', 'source.png', 'source.jpg'):
        if (folder / name).is_file():
            return folder / name
    return None


def replay_one(ctx, folder, truth_dir):
    scene = read_json(folder / 'scene.json')
    if not isinstance(scene, dict):
        raise ValueError(f'{folder}: scene.json is missing or is not a JSON object')
    flags = {p['id']: False for p in scene.get('corner_points', []) if p.get('fit') is False}
    image = find_image(folder) or ctx.image(scene['image']['width'], scene['image']['height'])
    run_dir = ctx.work / 'replay' / folder.name
    raw = run_skill(ctx, run_dir, points_from_scene(scene), scene, flags, image)
    case_path = Path(truth_dir) / folder.name / 'case.json' if truth_dir else None
    case = read_json(case_path) if case_path and case_path.is_file() else None
    if case is not None:
        row = score(case, {'name': 'replay', 'dims_mode': 'reply', 'sigma_px': None, 'injected': {'bad_ids': []}}, raw)
        row['truth'] = True
    else:
        fit = raw['fit'] if raw['fit'] is not None else raw['compile_fit']
        gate = (raw['report'] or {}).get('quality_gate')
        row = {'case_id': folder.name, 'pert': 'replay', 'truth': False, 'fit_status': (fit or {}).get('status'),
               'compile_status': (raw['report'] or {}).get('status'), 'gate_status': gate.get('status') if isinstance(gate, dict) else None,
               'rms_px': (fit or {}).get('rms_px'), 'n_fit': (fit or {}).get('n_fit'), 'n_check': (fit or {}).get('n_check'),
               'loo_median_px': (fit or {}).get('loo_median_px'), 'fit_s': raw['fit_s'], 'compile_s': raw['compile_s'],
               'failed': fit is None, 'failure': failure_text(raw, fit)}
    write_json(run_dir / 'row.json', row)
    return row


def run_replay(ctx, replies, truth_dir):
    folders = sorted(p for p in Path(replies).iterdir() if p.is_dir() and (p / 'scene.json').is_file())
    if not folders:
        raise ValueError(f'no <case id>/scene.json found in {replies}')
    return [replay_one(ctx, f, truth_dir) for f in folders]


def rescore(directory, resamples):
    """Score the stored raw outputs of an oracle run again with the current metrics code; the skill is not called."""
    out = Path(directory).resolve()
    old = read_json(out / 'results.json')
    if not old or old['meta'].get('mode') != 'oracle':
        raise ValueError(f'{out}: results.json of an oracle run is needed')
    meta = dict(old['meta'])
    cases = {c['case_id']: (c, s) for c, s in synth_generate.generate_suite(meta['suite'], meta['seed'], meta['n_cases'])}
    arm, scale_check = meta.get('check_corners', 2), bool(meta.get('scale_check'))     # older runs always held the check corners out
    rows = []
    for r in old['rows']:
        case, _scene = cases[r['case_id']]
        rows.append(score(case, perturb.apply(case, r['pert'], arm, scale_check), load_raw(out / 'work' / 'runs' / r['case_id'] / r['pert'])))
    meta.update(harness_version=HARNESS_VERSION, generator_version=synth_generate.GENERATOR_VERSION, check_corners=arm, scale_check=scale_check, rescored=True,
                resamples=resamples, suite_sha256=synth_generate.suite_fingerprint(meta['suite'], meta['seed'], meta['n_cases']))
    agg = summarize_rows(rows, resamples)
    write_json(out / 'results.json', {'meta': meta, 'aggregate': agg, 'rows': rows})
    (out / 'report.md').write_bytes(report.render(f"Oracle evaluation: {meta['suite']}", meta, agg).encode('utf-8'))
    return meta, agg, rows


def make_meta(ctx, args, mode, rows, n_cases):
    import numpy
    skill = ctx.skill_dir
    try:
        label = skill.relative_to(Path(__file__).resolve().parents[2]).as_posix()      # repository-relative, never an absolute path
    except ValueError:
        label = f'{skill.parent.name}/{skill.name}'
    suite = getattr(args, 'suite', None)
    return {'harness_version': HARNESS_VERSION, 'generator_version': synth_generate.GENERATOR_VERSION, 'mode': mode, 'suite': suite,
            'seed': getattr(args, 'seed', None), 'check_corners': ctx.check_corners if mode == 'oracle' else None,
            'scale_check': ctx.scale_check if mode == 'oracle' else None,
            'perturbations': sorted({r['pert'] for r in rows if r.get('pert')}),
            'suite_sha256': synth_generate.suite_fingerprint(suite, args.seed, n_cases) if mode == 'oracle' else None,
            'n_cases': n_cases, 'n_runs': len(rows), 'skill_label': label, 'skill_version': skill_caps.skill_version(skill),
            'skill_commit': skill_caps.baseline_commit(skill), 'skill_fingerprint': skill_caps.fingerprint(skill),
            'capabilities': ctx.caps, 'fit_seeds': ctx.fit_seeds, 'stages': '+'.join(ctx.stages), 'resamples': getattr(args, 'resamples', None),
            'python': platform.python_version(), 'numpy': numpy.__version__, 'platform': platform.system(),
            'command': 'python tools/eval/run_eval.py ' + ' '.join(args.argv)}


def summarize_rows(rows, resamples):
    scored = [r for r in rows if r.get('truth', True) and 'flagged' in r]
    if not scored:
        return {'all': {}}
    agg = {'all': metrics.aggregate(scored, resamples)}
    agg['by_pert'] = metrics.aggregate_by(scored, 'pert', resamples)
    agg['by_booth_type'] = metrics.aggregate_by(scored, 'booth_type', resamples)
    agg['by_n_fit'] = metrics.aggregate_by(scored, 'n_fit_bin', resamples, keys=metrics.CUT_KEYS)
    agg['by_crop'] = metrics.aggregate_by(scored, 'crop', resamples, keys=metrics.CUT_KEYS)
    agg['recall_by_n_fit'] = metrics.recall_by_bin(scored, 'outlier_recall', resamples)
    agg['recall_computed_by_n_fit'] = metrics.recall_by_bin(scored, 'outlier_recall_computed', resamples)
    return agg


def finish(ctx, args, mode, rows, n_cases, out_dir, title):
    meta = make_meta(ctx, args, mode, rows, n_cases)
    agg = summarize_rows(rows, args.resamples)
    write_json(out_dir / 'results.json', {'meta': meta, 'aggregate': agg, 'rows': rows})
    if agg.get('by_pert'):
        text = report.render(title, meta, agg)
    else:
        text = f'# {title}\n\n{len(rows)} replies compiled; no ground truth was given, so only outcomes are listed in results.json.\n'
    (out_dir / 'report.md').write_bytes(text.encode('utf-8'))
    if args.markdown:
        Path(args.markdown).parent.mkdir(parents=True, exist_ok=True)
        Path(args.markdown).write_bytes(text.encode('utf-8'))
    return meta, agg


def build_parser():
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    sub = p.add_subparsers(dest='mode', required=True)

    def common(s):
        s.add_argument('--skill-dir', required=True, help='Skill folder to measure (the one that holds scripts/ and schemas/)')
        s.add_argument('--out', required=True, help='Output folder (results.json, report.md, per-run files)')
        s.add_argument('--seeds', type=int, default=40, help='box_fit seeds passed to both tools (the skill default is 40)')
        s.add_argument('--stages', choices=('both', 'fit', 'compile'), default='both')
        s.add_argument('--timeout', type=int, default=300)
        s.add_argument('--resamples', type=int, default=stats.DEFAULT_RESAMPLES)
        s.add_argument('--markdown', help='Also write the markdown report to this path')
        s.add_argument('--title', default=None)
        s.add_argument('--resume', action='store_true', help='Reuse the finished runs in --out (after an interrupted call)')
    o = sub.add_parser('oracle', help='Ground-truth corners with perturbations; no model, no Blender')
    common(o)
    o.add_argument('--suite', default='oracle-v1', choices=sorted(synth_generate.SUITES))
    o.add_argument('--seed', type=int, default=20261019)
    o.add_argument('--cases', type=int, help='Only the first N scenes of the suite')
    o.add_argument('--perturbations', nargs='*', choices=sorted(perturb.PERTURBATIONS),
                   help="Perturbations to run instead of the suite's own list (any registered one, e.g. wrong_anchor_m25), in order")
    o.add_argument('--check-corners', type=int, choices=perturb.CHECK_CORNER_ARMS, default=2,
                   help="Arm: 2 (default) sends the case's check corners with fit:false; 0 sends every corner as a fitted one")
    o.add_argument('--jobs', type=int, default=1, help='Parallel subprocesses (timings get noisier above 1)')
    o.add_argument('--scale-check', action='store_true',
                   help='Also hand the skill a cafe-table scale check (0.74 m) in every scene that has a round table')
    r = sub.add_parser('replay', help='Re-compile saved reader replies')
    common(r)
    r.add_argument('--replies', required=True, help='Folder with <case id>/scene.json')
    r.add_argument('--cases', help='Folder with <case id>/case.json (truth) to score the replies')
    r.set_defaults(seed=None, suite=None)
    rs = sub.add_parser('rescore', help='Score the stored raw outputs of an oracle run again (the skill is not called)')
    rs.add_argument('--dir', required=True, help='The --out folder of an earlier oracle run')
    rs.add_argument('--resamples', type=int, default=stats.DEFAULT_RESAMPLES)
    live = sub.add_parser('live', help='Not implemented in this round')
    live.add_argument('rest', nargs='*')
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(argv)
    args.argv = argv
    if args.mode == 'live':
        print(LIVE_MESSAGE, file=sys.stderr)
        return 2
    if args.mode == 'rescore':
        meta, _agg, rows = rescore(args.dir, args.resamples)
        print(json.dumps({'mode': 'rescore', 'runs': len(rows), 'results': str(Path(args.dir).resolve() / 'results.json')}))
        return 0
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    stages = ('fit', 'compile') if args.stages == 'both' else (args.stages,)
    ctx = Context(args.skill_dir, out_dir / 'work', args.seeds, stages, args.timeout, args.resume, getattr(args, 'check_corners', 2),
                  getattr(args, 'scale_check', False))
    if args.mode == 'oracle':
        names = args.perturbations or synth_generate.SUITES[args.suite]['perturbations']
        started = time.perf_counter()
        rows, n_cases = run_oracle(ctx, args.suite, args.seed, args.cases, names, args.jobs)
        title = args.title or f'Oracle evaluation: {args.suite}'
    else:
        started = time.perf_counter()
        rows = run_replay(ctx, args.replies, args.cases)
        n_cases = len(rows)
        title = args.title or 'Replay evaluation'
    meta, agg = finish(ctx, args, args.mode, rows, n_cases, out_dir, title)
    print(json.dumps({'mode': args.mode, 'runs': len(rows), 'seconds': round(time.perf_counter() - started, 1),
                      'results': str(out_dir / 'results.json'), 'report': str(out_dir / 'report.md')}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
