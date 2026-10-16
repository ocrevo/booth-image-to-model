"""Job folders, their status.json, and background steps. One folder per job under the jobs root (output/studio by default).

A job is: the uploaded image, an optional scene.json, the working spec.json, and the products of each step.
status.json is the single record the page polls; every write is atomic. Only one step runs per job at a time and only one
Blender process runs at a time on the machine (a global lock), as the project rules require.
"""
from __future__ import annotations
import hashlib
import io
import json
import os
import re
import secrets
import tempfile
import threading
import time
import traceback
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

JOB_ID = re.compile(r'^\d{8}-\d{6}-[0-9a-f]{4}$')
STEPS = ('scene', 'compile', 'preview', 'build', 'glb')
DOWNSTREAM = {'scene': ('compile', 'preview', 'build', 'glb'), 'compile': ('preview', 'build', 'glb'), 'edit': ('preview', 'build', 'glb')}
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_IMAGE_PIXELS = 5_000_000          # Blender renders three frames at the picture's size; 6.9 MP made the denoiser run out of memory (AGENTS.md)
EXIF_ORIENTATION = 0x0112


class NotFound(KeyError):
    pass


class Busy(RuntimeError):
    pass


class BadRequest(ValueError):
    """A request the user can fix; `.detail` is returned to the page."""

    def __init__(self, message, detail=None):
        super().__init__(message)
        self.detail = detail or {}


class StepError(RuntimeError):
    """A step that failed in an expected way; the message and detail go to the page."""

    def __init__(self, message, detail=None):
        super().__init__(message)
        self.detail = detail or {}


def _now():
    return datetime.now().isoformat(timespec='seconds')


def _scrub(value, redact):
    """Apply redact() to every string inside a JSON-like value."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [_scrub(v, redact) for v in value]
    if isinstance(value, dict):
        return {k: _scrub(v, redact) for k, v in value.items()}
    return value


def _settle(data):
    """A step that says running or queued while no thread runs it (the server stopped, a thread died) is not running.
    running -> error; queued -> what it was before it was queued. Returns True when something changed."""
    changed = False
    for entry in data['steps'].values():
        if entry['state'] == 'running':
            entry.update(state='error', message='This step was interrupted (the server restarted or a thread failed). Please run it again.', updated=_now())
            entry.pop('prior', None)
            changed = True
        elif entry['state'] == 'queued':
            prior = entry.pop('prior', None) or {'state': 'idle', 'message': ''}
            entry.update(state=prior['state'], message=prior['message'], updated=_now())
            changed = True
    return changed


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + '.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.write('\n')
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


_write_json = write_json


class Jobs:
    def __init__(self, root, redact=None):
        """redact(text) -> text is applied to every message and detail before it is stored: a secret must never reach status.json."""
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._running = {}
        self._redact = redact or (lambda text: text)
        self.blender_lock = threading.Lock()
        self._mark_interrupted()

    # ------------------------------------------------------------ lookup
    def path(self, job_id):
        if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
            raise NotFound(job_id)
        d = self.root / job_id
        if not (d / 'status.json').is_file():
            raise NotFound(job_id)
        return d

    def status(self, job_id):
        with self._lock:
            d = self.path(job_id)
            data = json.loads((d / 'status.json').read_text(encoding='utf-8'))
            data['busy'] = self._running.get(job_id)
            data['files'] = {'scene': (d / 'scene.json').is_file(), 'spec': (d / 'spec.json').is_file()}      # the inputs the next steps need
            if job_id not in self._running:
                _settle(data)                       # never show a step as running when nothing runs it
            return data

    @contextmanager
    def exclusive(self, job_id):
        """Hold the job still: raises Busy when a step runs, and no step can start until the block ends (for writes to scene/spec)."""
        with self._lock:
            self.path(job_id)
            if job_id in self._running:
                raise Busy(f'The "{self._running[job_id]}" step of this job is still running. Wait for it to finish.')
            yield

    def list(self):
        out = []
        for d in sorted(self.root.iterdir(), reverse=True):
            if JOB_ID.fullmatch(d.name) and (d / 'status.json').is_file():
                s = self.status(d.name)
                out.append({'id': s['id'], 'created': s['created'], 'image': s['image']['original_name'], 'file': s['image']['name'], 'spec': s['spec']['origin'],
                            'states': {k: v['state'] for k, v in s['steps'].items()}})
        return out

    # ------------------------------------------------------------ writes
    def _mutate(self, job_id, fn):
        with self._lock:
            d = self.path(job_id)
            data = json.loads((d / 'status.json').read_text(encoding='utf-8'))
            fn(data)
            write_json(d / 'status.json', data)
            return data

    def set_step(self, job_id, step, state, message='', detail=None, seconds=None):
        message = self._redact(message)
        detail = _scrub(detail, self._redact) if detail is not None else None

        def apply(data):
            entry = {'state': state, 'message': message, 'updated': _now()}
            if detail is not None:
                entry['detail'] = detail
            if seconds is not None:
                entry['seconds'] = seconds
            if state == 'running':
                entry['started'] = time.time()
            data['steps'][step] = entry
        return self._mutate(job_id, apply)

    def invalidate(self, job_id, steps):
        """Steps that were done become stale (their product no longer matches the spec); idle ones stay idle."""
        def apply(data):
            for step in steps:
                if data['steps'][step]['state'] in ('done', 'skipped'):
                    data['steps'][step]['state'] = 'stale'
        return self._mutate(job_id, apply)

    def set_spec(self, job_id, origin):
        return self._mutate(job_id, lambda data: data['spec'].update(origin=origin, updated=_now()))

    def create(self, original_name, data):
        from PIL import Image
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise BadRequest(f'The picture must be between 1 byte and {MAX_IMAGE_BYTES // (1024 * 1024)} MB')
        try:
            with Image.open(io.BytesIO(data)) as im:
                fmt, size = im.format, im.size
                im.verify()                                     # verify() must come directly after open() and spoils the image
            with Image.open(io.BytesIO(data)) as im:
                orientation = im.getexif().get(EXIF_ORIENTATION, 1)
        except Exception as ex:
            raise BadRequest(f'This is not a picture the studio can read: {ex}') from ex
        ext = {'PNG': '.png', 'JPEG': '.jpg', 'MPO': '.jpg'}.get(fmt)         # MPO is how Pillow names many phone JPEGs
        if not ext:
            raise BadRequest(f'Please use a PNG or JPEG picture (this one is {fmt})')
        if min(size) < 64:
            raise BadRequest('The picture is smaller than 64 pixels')
        if size[0] * size[1] > MAX_IMAGE_PIXELS:
            raise BadRequest(f'The picture is {size[0] * size[1] / 1e6:.1f} MP, over the {MAX_IMAGE_PIXELS // 1_000_000} MP limit (Blender renders at the picture size, and larger pictures run out of memory). '
                             f'Please shrink it to {MAX_IMAGE_PIXELS // 1_000_000} MP or less and upload it again')
        if orientation not in (1, 0, None):
            raise BadRequest('The picture carries EXIF rotation information (common in phone photos): the orientation the browser shows differs from the orientation of the pixels, so reading and compiling would use the wrong direction. '
                             'Please rotate it in an image viewer, save it again (this removes the rotation flag) and upload it again')
        job_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
        d = self.root / job_id
        d.mkdir(parents=True)
        (d / ('source' + ext)).write_bytes(data)
        status = {'id': job_id, 'created': _now(),
                  'image': {'name': 'source' + ext, 'original_name': os.path.basename(original_name or 'image') or 'image', 'width': size[0],
                            'height': size[1], 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)},
                  'spec': {'origin': None, 'updated': None},
                  'steps': {s: {'state': 'idle', 'message': '', 'updated': _now()} for s in STEPS}}
        write_json(d / 'status.json', status)
        return self.status(job_id)

    # ------------------------------------------------------------ background steps
    def start(self, job_id, plan):
        """plan: [(step, callable returning {'message', 'detail'}, uses_blender)]; stops at the first failure.
        The steps after the first are queued but keep their previous results until they run (a failure restores them)."""
        with self._lock:
            self.path(job_id)
            if job_id in self._running:
                raise Busy(f'The "{self._running[job_id]}" step of this job is still running. Wait for it to finish.')
            self._running[job_id] = plan[0][0]
        try:
            def queue(data):
                for index, (step, _, _) in enumerate(plan):
                    old = data['steps'][step]
                    if index == 0:
                        data['steps'][step] = {'state': 'running', 'message': '', 'updated': _now(), 'started': time.time()}
                    else:
                        data['steps'][step] = {**old, 'state': 'queued', 'message': 'Waiting for the earlier steps', 'updated': _now(),
                                               'prior': {'state': old['state'], 'message': old.get('message', '')}}
            self._mutate(job_id, queue)
            threading.Thread(target=self._run_plan, args=(job_id, plan), name=f'job-{job_id}', daemon=True).start()
        except BaseException:
            with self._lock:
                self._running.pop(job_id, None)
            raise

    def _run_plan(self, job_id, plan):
        try:
            for index, (step, fn, uses_blender) in enumerate(plan):
                started = time.time()
                try:
                    with self._lock:
                        self._running[job_id] = step
                    self.set_step(job_id, step, 'running', message='Running')
                    if uses_blender:
                        self.set_step(job_id, step, 'running', message='Waiting for Blender (only one runs at a time)')
                        with self.blender_lock:
                            started = time.time()                          # the time Blender is used, not the time spent queueing for it
                            self.set_step(job_id, step, 'running', message='Running')
                            result = fn()
                    else:
                        result = fn()
                    self.set_step(job_id, step, 'done', message=result.get('message', ''), detail=result.get('detail'),
                                  seconds=round(time.time() - started, 1))
                except StepError as ex:
                    self._fail(job_id, step, str(ex), ex.detail, started, plan[index + 1:])
                    return
                except Exception as ex:                                  # an unexpected error is still shown, with its trace in the detail
                    self._fail(job_id, step, f'{type(ex).__name__}: {ex}', {'trace': traceback.format_exc()[-2500:]}, started, plan[index + 1:])
                    return
        finally:
            with self._lock:
                self._running.pop(job_id, None)

    def _fail(self, job_id, step, message, detail, started, rest):
        """Record the failure and give the queued steps their previous state back. Best effort: if even this write fails,
        status() still reports the step as interrupted because no thread runs it any more."""
        try:
            self.set_step(job_id, step, 'error', message=message, detail=detail, seconds=round(time.time() - started, 1))
            names = [name for name, _, _ in rest]

            def restore(data):
                for name in names:
                    entry = data['steps'][name]
                    if entry['state'] == 'queued':
                        prior = entry.pop('prior', None) or {'state': 'idle', 'message': ''}
                        entry.update(state=prior['state'], message=prior['message'], updated=_now())
            self._mutate(job_id, restore)
        except Exception:
            pass

    def _mark_interrupted(self):
        for d in self.root.iterdir():
            f = d / 'status.json'
            if JOB_ID.fullmatch(d.name) and f.is_file():
                data = json.loads(f.read_text(encoding='utf-8'))
                if _settle(data):
                    write_json(f, data)
