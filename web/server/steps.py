"""What each step of the flow does, written against engine.py. Steps return {'message', 'detail'} or raise StepError."""
from __future__ import annotations
import json
import os
from pathlib import Path

import engine
from jobs import BadRequest, Busy, DOWNSTREAM, STEPS, StepError, write_json


def _rel(base, path):
    return str(Path(path).resolve().relative_to(Path(base).resolve())).replace('\\', '/')


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _write(path, data):
    write_json(path, data)                                         # atomic: a reader never sees half a file


def _replace_with_copy(src, dst):
    """dst becomes a copy of src in one atomic step."""
    tmp = Path(str(dst) + '.tmp')
    tmp.write_bytes(Path(src).read_bytes())
    os.replace(tmp, dst)


# ------------------------------------------------------------------ inputs that are not steps
def put_scene(jobs, job_id, scene):
    """Manual reading: a scene description written by a person, a script or another tool."""
    status = jobs.status(job_id)
    errors = engine.validate_scene(scene) if isinstance(scene, dict) else [{'code': 'SCHEMA', 'where': '(root)', 'message': 'a scene description is a JSON object'}]
    image = status['image']
    if not errors and (scene['image']['width'], scene['image']['height']) != (image['width'], image['height']):
        errors = [{'code': 'IMAGE', 'where': 'image', 'message': f"the scene says {scene['image']['width']}x{scene['image']['height']} "
                                                                 f"but this job's image is {image['width']}x{image['height']}"}]
    if errors:
        raise BadRequest('The scene description has errors', {'errors': errors[:40]})
    with jobs.exclusive(job_id):                                  # not while a step reads or builds from the job
        _write(jobs.path(job_id) / 'scene.json', scene)
        jobs.invalidate(job_id, DOWNSTREAM['scene'])
        jobs.set_step(job_id, 'scene', 'done', message=f"Scene description uploaded by hand ({len(scene['components'])} parts)")
    return {'components': len(scene['components'])}


def put_spec(jobs, job_id, spec, origin):
    """A spec from outside (origin 'uploaded') or from the editor (origin 'edited'). It must be for this job's image."""
    if origin not in ('uploaded', 'edited'):
        raise BadRequest('origin must be "uploaded" or "edited"')
    status = jobs.status(job_id)
    image = status['image']
    d = jobs.path(job_id)
    if not isinstance(spec, dict) or not isinstance(spec.get('source'), dict) or 'components' not in spec:
        raise BadRequest('This is not a booth-spec-v2 spec (source or components is missing)')
    if spec['source'].get('sha256') != image['sha256']:
        raise BadRequest('The source picture of this spec is not the picture of this job (sha256 differs)',
                         {'spec_sha256': spec['source'].get('sha256'), 'job_sha256': image['sha256']})
    spec = json.loads(json.dumps(spec))
    spec['source']['image'] = image['name']
    check = engine.validate_spec(spec, d, [image['width'], image['height']])
    if not check['valid']:
        raise BadRequest('The spec did not pass validation', {'errors': check['errors'][:30]})
    with jobs.exclusive(job_id):                                  # an editor save must not land in the middle of a build
        _write(d / 'spec.json', spec)
        jobs.set_spec(job_id, origin)
        jobs.invalidate(job_id, DOWNSTREAM['edit'])
        if origin == 'uploaded':
            jobs.set_step(job_id, 'scene', 'skipped', message='A spec was provided directly; no reading needed')
            jobs.set_step(job_id, 'compile', 'skipped', message='A spec was provided directly; no compiling needed')
    return {'components': len(spec['components']), 'warnings': check['warnings'][:20]}


# ------------------------------------------------------------------ steps
def read_step(jobs, job_id, config):
    import reader_openai
    d, status = jobs.path(job_id), jobs.status(job_id)
    try:
        result = reader_openai.read_scene(d / status['image']['name'], config, d / 'reader')
    except reader_openai.ReaderError as ex:
        raise StepError(str(ex), ex.detail) from ex                # a failed reading changes nothing downstream
    scene = result['scene']
    _write(d / 'scene.json', scene)
    jobs.invalidate(job_id, DOWNSTREAM['scene'])
    usage = result['usage']
    return {'message': f"{config.model} read {len(scene['components'])} parts ({result['attempts']} calls, {usage['input_tokens']} tokens in / {usage['output_tokens']} tokens out)",
            'detail': {'model': config.model, 'attempts': result['attempts'], 'usage': usage, 'seconds': result['seconds']}}


def compile_step(jobs, job_id):
    d, status = jobs.path(job_id), jobs.status(job_id)
    if not (d / 'scene.json').is_file():
        raise StepError('Finish "Read the picture" first: upload a scene description, or read the picture with the large model')
    scene = _read(d / 'scene.json')
    image = d / status['image']['name']
    try:
        spec, report = engine.compile_scene(scene, image, d / 'compile')
    except engine.scene_error() as ex:
        raise StepError('The scene description did not pass the check before compiling', {'errors': ex.errors[:40]}) from ex
    fit = _read(d / 'compile' / 'box_fit.json')
    overlay_note = None
    try:
        engine.draw_overlay(fit, image, d / 'overlay.png')
    except Exception as ex:                                       # the overlay is a check aid; its failure must not hide the compile
        overlay_note = f'{type(ex).__name__}: {ex}'
    detail = {'status': report['status'], 'components': report['components_built'], 'valid': report['valid'],
              'fit': {k: report['fit'].get(k) for k in ('status', 'rms_px', 'max_px', 'worst_points', 'warnings', 'ambiguity_hint')},
              'points': [{'id': p['id'], 'err_px': p['err_px']} for p in fit.get('points', [])],
              'dropped': report['dropped'], 'adjustments': report['adjustments'][:40], 'adjustments_total': len(report['adjustments']),
              'assumed': report['assumed'][:60], 'assumed_total': len(report['assumed']), 'warnings': report['warnings'][:40],
              'overlay': 'overlay.png' if (d / 'overlay.png').is_file() else None, 'overlay_error': overlay_note}
    if not report['valid']:
        raise StepError('The compiled spec did not pass validation', {**detail, 'errors': report['validation_errors']})
    _replace_with_copy(d / 'compile' / 'spec.json', d / 'spec.json')
    jobs.set_spec(job_id, 'compiled')
    jobs.invalidate(job_id, DOWNSTREAM['compile'])               # only now: a failed compile leaves the earlier products valid
    return {'message': f"{report['components_built']} parts; fit {report['fit']['status']} (residual {report['fit']['rms_px']} px)"
                       + (f"; {len(report['dropped'])} dropped" if report['dropped'] else ''), 'detail': detail}


def _need_spec(d):
    spec = d / 'spec.json'
    if not spec.is_file():
        raise StepError('There is no spec yet: compile the scene description first, or upload spec.json')
    return spec


def preview_step(jobs, job_id):
    d = jobs.path(job_id)
    result = engine.preview(_need_spec(d), d / 'preview')
    if result['status'] != 'preview_ready':
        raise StepError('The preview was not produced', {'status': result['status'], 'errors': result.get('errors'), 'log_tail': result.get('log_tail')})
    design = result.get('design_findings') or {}
    run = _rel(d, result['run'])
    return {'message': f"Corner error: median {result['landmark_median_px']} px, max {result['landmark_max_px']} px",
            'detail': {'sheet': _rel(d, result['sheet']), 'run': run, 'engine': result.get('engine_used'),
                       'render': f'{run}/preview_render.png',                      # the render alone, transparent, at the source camera and size
                       'landmark_median_px': result['landmark_median_px'], 'landmark_max_px': result['landmark_max_px'],
                       'design_counts': design.get('counts'), 'design_findings': (design.get('findings') or [])[:10],
                       'not_checked': design.get('not_checked'), 'warnings': (result.get('warnings') or [])[:20], 'seconds': result['seconds']}}


def build_step(jobs, job_id, hd=False):
    hd_requested = bool(hd)
    d = jobs.path(job_id)
    spec = _need_spec(d)
    numbers = [int(p.name.split('_')[-1]) for p in d.glob('build_*') if p.name.split('_')[-1].isdigit()]
    out = d / f'build_{1 + max(numbers + [0]):03d}'
    result = engine.build(spec, out, samples=16, hd_scale=2 if hd else 1)
    if result['returncode'] != 0:
        raise StepError('The Blender build failed', {'log_tail': result['log_tail']})
    names = {'blend': 'booth.blend', 'client': 'client_render.png', 'reference': 'reference_render.png', 'inspection': 'inspection_render.png',
             'client_hd': 'client_render_hd.png'}
    files = {key: _rel(d, out / name) for key, name in names.items() if (out / name).is_file()}
    if 'blend' not in files or 'client' not in files:
        raise StepError('Blender finished, but no .blend or client picture was produced', {'log_tail': result['log_tail'], 'files': files})
    report = result.get('report') or {}
    hd = report.get('hd') or None
    note = ''
    if hd_requested and 'client_hd' not in files:
        note = f"; the HD picture was not made: 2x enlargement would pass the 5 MP limit (effective factor {hd.get('effective') if hd else '?'})"
    return {'message': f"Made the .blend and the client picture ({result['seconds']} s){note}",
            'detail': {'dir': _rel(d, out), 'files': files, 'seconds': result['seconds'], 'stage_seconds': report.get('stage_seconds'), 'hd': hd,
                       'hd_requested': hd_requested}}


def glb_step(jobs, job_id):
    d = jobs.path(job_id)
    code, report = engine.export_glb(_need_spec(d), d / 'exports' / 'booth.glb')
    if code != 0:
        raise StepError('The GLB export failed', {'report': report})
    return {'message': f"{report['exported']} parts, {round(report['bytes'] / 1024)} KB",
            'detail': {'glb': 'exports/booth.glb', 'bytes': report['bytes'], 'exported': report['exported'], 'groups': len(report['groups']),
                       'skipped_text': report['skipped_text'], 'pattern_omitted': report['pattern_omitted'], 'emission_clamped': report['emission_clamped']}}


def open_blender(jobs, job_id):
    status = jobs.status(job_id)
    build = status['steps']['build']
    blend = (build.get('detail') or {}).get('files', {}).get('blend')
    if build['state'] not in ('done', 'stale') or not blend:
        raise BadRequest('There is no .blend yet: click "Render" first')
    path = jobs.path(job_id) / blend
    if not path.is_file():
        raise BadRequest('The .blend file is missing; please generate it again')
    try:
        return engine.open_in_blender(path)
    except RuntimeError as ex:
        raise BadRequest(str(ex)) from ex


# ------------------------------------------------------------------ plans
def auto_names(jobs, job_id, openai):
    """What "run everything" means from the job's current state. A finished compile is never redone: that would overwrite the
    spec the user edited in the editor (or uploaded), and a job that has a spec but no scene description (an uploaded spec) has
    nothing to compile from, whatever state an earlier click left on the compile step."""
    status = jobs.status(job_id)
    has_spec = status['spec']['origin'] is not None
    compile_possible = status['files']['scene'] or not has_spec
    names = ['scene'] if openai else []
    if openai or (status['steps']['compile']['state'] not in ('done', 'skipped') and compile_possible):
        names.append('compile')
    return names + ['preview', 'build', 'glb']


def run_names(requested):
    """The steps a page asks for, checked: a non-empty list of known steps in pipeline order, no repeats."""
    if not isinstance(requested, list) or not requested or not all(isinstance(n, str) for n in requested):
        raise BadRequest('steps must be a non-empty list of step names')
    unknown = [n for n in requested if n not in STEPS]
    if unknown:
        raise BadRequest(f'unknown steps: {", ".join(unknown)}')
    order = [STEPS.index(n) for n in requested]
    if order != sorted(set(order)):
        raise BadRequest(f'steps must follow the order {", ".join(STEPS)} without repeats')
    return list(requested)


def check_ready(jobs, job_id, names):
    """The first step of a plan needs its input. Say so before anything starts (HTTP 422) instead of starting a step that is bound
    to fail and marking it failed: a refused click changes nothing."""
    status = jobs.status(job_id)
    if status['busy']:                                           # a running step is the more useful thing to hear about
        raise Busy(f"The '{status['busy']}' step of this job is still running. Wait for it to finish.")
    first = names[0]
    if first == 'compile' and not status['files']['scene']:
        if status['spec']['origin']:
            raise BadRequest('The spec of this job was provided directly, so there is no scene description to compile; to compile again, read the picture or upload a scene description in step 2 first')
        raise BadRequest('Finish "Read the picture" first: upload a scene description, or read the picture with the large model')
    if first in ('preview', 'build', 'glb') and not status['files']['spec']:
        raise BadRequest('There is no spec yet: compile the scene description first, or upload spec.json')


def plan(jobs, job_id, names, config=None, hd=False):
    table = {'scene': lambda: read_step(jobs, job_id, config), 'compile': lambda: compile_step(jobs, job_id),
             'preview': lambda: preview_step(jobs, job_id), 'build': lambda: build_step(jobs, job_id, hd), 'glb': lambda: glb_step(jobs, job_id)}
    uses_blender = {'preview', 'build'}
    return [(name, table[name], name in uses_blender) for name in names]
