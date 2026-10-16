"""Regression tests for the defects an independent review found in the first studio version: secrets in stored errors, odd file
names, stuck or lost job state, edits racing a running step, a one-click run that overwrote the user's edits, and so on.

    python -m unittest discover -s web/tests -p "test_*.py"        (from the repository root)
"""
from __future__ import annotations
import contextlib
import http.client
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / 'web' / 'server'))
sys.path.insert(0, str(REPO / 'booth-image-to-model' / 'tests'))
from PIL import Image

import engine                                        # noqa: E402
import reader_openai as reader                       # noqa: E402
import run                                           # noqa: E402
import steps                                         # noqa: E402
import test_draft_compile as fixtures                # noqa: E402
from jobs import Busy, Jobs, StepError               # noqa: E402
from mock_openai import MockOpenAI                   # noqa: E402
from safety import clean_key, clean_relative, redact  # noqa: E402
from server import Config, create_server             # noqa: E402
from test_server import StudioCase, png_bytes        # noqa: E402

KEY = 'sk-live-ABCDEFGHIJKLMNOP0123'


def wait_until(check, what, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        if check():
            return
        time.sleep(0.05)
    raise AssertionError(f'timed out waiting for {what}')


class SafetyHelpers(unittest.TestCase):
    def test_keys_are_trimmed_and_anything_unsendable_is_refused_without_echo(self):
        self.assertEqual(clean_key('  sk-abc123\r\n'), 'sk-abc123')
        self.assertIsNone(clean_key(''))
        self.assertIsNone(clean_key(None))
        self.assertIsNone(clean_key('   \n'))
        for bad in ('sk-abc\ndef', 'sk-abc def', 'sk-äbc', 'sk-abc\x00'):
            with self.assertRaises(ValueError) as ctx:
                clean_key(bad)
            self.assertNotIn('sk-', str(ctx.exception))

    def test_redact_removes_the_key_and_key_like_text(self):
        self.assertEqual(redact(f'Bearer {KEY} rejected', KEY), 'Bearer *** rejected')
        self.assertEqual(redact('Incorrect API key provided: sk-proj-********wXyZ.'), 'Incorrect API key provided: sk-….')
        self.assertEqual(redact('nothing to hide: ask-me'), 'nothing to hide: ask-me')

    def test_relative_names_cannot_leave_their_folder_or_name_a_share(self):
        for bad in ('', '..', '../x.png', 'a/../x.png', '\\\\host\\share\\x', 'C:/x.png', 'a:b', '/etc/x', '.hidden', 'a/.hidden/x', 'a//b',
                    'a/b ', 'a/b.', 'x\x00.png', 'a\\b.png'):
            self.assertIsNone(clean_relative(bad), repr(bad))
        for good in ('overlay.png', 'build_001/client_render.png', 'js/app.js', 'vendor/three/three.module.js'):
            self.assertEqual(clean_relative(good), good)


class JobStates(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.jobs = Jobs(self._tmp.name)
        self.job = self.jobs.create('a.png', png_bytes())['id']

    def idle(self):
        return self.jobs.status(self.job)['busy'] is None

    def wait(self):
        wait_until(self.idle, 'the job to stop running')

    def test_a_failure_gives_the_queued_steps_their_earlier_results_back(self):
        files = {'files': {'blend': 'build_001/booth.blend'}}
        for name in ('build', 'glb'):
            self.jobs.set_step(self.job, name, 'done', message='fine', detail=files)
        gate = threading.Event()

        def first():
            gate.wait(10)
            raise StepError('nope', {'errors': []})
        plan = [('compile', first, False), ('build', lambda: {'message': 'x'}, False), ('glb', lambda: {'message': 'y'}, False)]
        self.jobs.start(self.job, plan)
        queued = self.jobs.status(self.job)['steps']['build']
        self.assertEqual((queued['state'], queued['detail']), ('queued', files))                  # waiting, but its result is not thrown away
        gate.set()
        self.wait()
        st = self.jobs.status(self.job)['steps']
        self.assertEqual(st['compile']['state'], 'error')
        for name in ('build', 'glb'):
            self.assertEqual((st[name]['state'], st[name]['message'], st[name]['detail']), ('done', 'fine', files))
            self.assertNotIn('prior', st[name])

    def test_after_a_restart_nothing_is_left_running_or_queued(self):
        path = self.jobs.path(self.job) / 'status.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        data['steps']['preview'] = {'state': 'running', 'message': 'Running', 'updated': 'x'}
        data['steps']['build'] = {'state': 'queued', 'message': 'Waiting for the earlier steps', 'updated': 'x', 'detail': {'files': {}},
                                  'prior': {'state': 'done', 'message': 'fine'}}
        data['steps']['glb'] = {'state': 'queued', 'message': 'Waiting for the earlier steps', 'updated': 'x'}
        path.write_text(json.dumps(data), encoding='utf-8')
        st = self.jobs.status(self.job)['steps']                                                   # no thread runs them: reported settled
        self.assertEqual((st['preview']['state'], st['build']['state'], st['build']['message'], st['glb']['state']), ('error', 'done', 'fine', 'idle'))
        self.assertEqual(st['build']['detail'], {'files': {}})
        self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['steps']['preview']['state'], 'running')       # reading does not write
        Jobs(self._tmp.name)                                                                       # the next server start makes it permanent
        self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['steps']['preview']['state'], 'error')

    def test_a_status_write_that_fails_does_not_strand_the_job(self):
        real = self.jobs.set_step

        def flaky(job_id, step, state, **kw):
            if state in ('done', 'error'):
                raise PermissionError('status.json is locked by something')                      # an indexer, an antivirus, OneDrive
            return real(job_id, step, state, **kw)
        with mock.patch.object(self.jobs, 'set_step', flaky):
            self.jobs.start(self.job, [('compile', lambda: {'message': 'ok'}, False), ('glb', lambda: {'message': 'ok'}, False)])
            self.wait()
        st = self.jobs.status(self.job)
        self.assertIsNone(st['busy'])
        self.assertEqual({st['steps'][s]['state'] for s in ('compile', 'glb')} <= {'error', 'idle'}, True, st['steps'])
        self.jobs.start(self.job, [('compile', lambda: {'message': 'ok'}, False)])                # and the job takes the next request
        self.wait()
        self.assertEqual(self.jobs.status(self.job)['steps']['compile']['state'], 'done')

    def test_a_blender_step_is_timed_from_when_it_gets_blender_not_from_the_queue(self):
        other = self.jobs.create('b.png', png_bytes())['id']
        self.jobs.start(self.job, [('build', lambda: (time.sleep(1.0), {'message': 'slow'})[1], True)])
        time.sleep(0.3)                                                                 # the first one holds Blender now
        self.jobs.start(other, [('build', lambda: (time.sleep(0.2), {'message': 'fast'})[1], True)])
        wait_until(lambda: self.jobs.status(other)['busy'] is None and self.jobs.status(self.job)['busy'] is None, 'both builds')
        seconds = self.jobs.status(other)['steps']['build']['seconds']
        self.assertGreaterEqual(seconds, 0.2)
        self.assertLess(seconds, 0.7, 'the 0.7 s queued behind the first build must not count')

    def test_a_start_that_fails_does_not_leave_the_job_busy(self):
        real = self.jobs._mutate
        calls = []

        def flaky(job_id, fn):
            if not calls:
                calls.append(1)
                raise OSError('disk full')
            return real(job_id, fn)
        with mock.patch.object(self.jobs, '_mutate', flaky):
            with self.assertRaises(OSError):
                self.jobs.start(self.job, [('compile', lambda: {'message': 'ok'}, False)])
        self.assertIsNone(self.jobs.status(self.job)['busy'])
        self.jobs.start(self.job, [('compile', lambda: {'message': 'ok'}, False)])
        self.wait()

    def test_nobody_may_write_to_a_job_while_a_step_runs(self):
        gate = threading.Event()
        self.jobs.start(self.job, [('compile', lambda: gate.wait(10) and {'message': 'ok'}, False)])
        with self.assertRaises(Busy):
            with self.jobs.exclusive(self.job):
                pass
        with self.assertRaises(Busy):
            self.jobs.start(self.job, [('glb', lambda: {'message': 'ok'}, False)])
        gate.set()
        self.wait()
        with self.jobs.exclusive(self.job):
            pass

    def test_a_secret_in_an_error_never_reaches_status_json(self):
        jobs = Jobs(self._tmp.name, redact=lambda text: redact(text, KEY))

        def leaky():
            raise RuntimeError(f'Invalid header value b"Bearer {KEY}\\r\\n"')

        def echo():
            raise StepError('the provider said: Incorrect API key provided: sk-proj-********wXyZ', {'sent': [f'Bearer {KEY}']})
        for fn in (leaky, echo):
            jobs.start(self.job, [('compile', fn, False)])
            wait_until(lambda: jobs.status(self.job)['busy'] is None, 'the step')
            text = (jobs.path(self.job) / 'status.json').read_text(encoding='utf-8')
            self.assertNotIn(KEY, text)
            self.assertNotIn('sk-proj', text)
            self.assertIn('error', json.loads(text)['steps']['compile']['state'])


class Uploads(StudioCase):
    def post(self, data):
        return self.api.call('POST', '/api/jobs?name=photo.jpg', raw=data, headers={'Content-Type': 'image/jpeg'})

    def test_a_rotated_photo_is_refused_with_the_reason(self):
        for orientation, expected in ((1, 201), (6, 422), (8, 422)):
            image = Image.new('RGB', (640, 480), (120, 120, 120))
            exif = Image.Exif()
            exif[0x0112] = orientation
            buf = io.BytesIO()
            image.save(buf, 'JPEG', exif=exif.tobytes())
            status, body = self.post(buf.getvalue())
            self.assertEqual(status, expected, body)
            if expected == 422:
                self.assertIn('EXIF', body['error'])

    def test_a_picture_too_large_to_render_is_refused_before_anything_is_built(self):
        status, body = self.api.call('POST', '/api/jobs?name=big.png', raw=png_bytes((2600, 2000)), headers={'Content-Type': 'image/png'})
        self.assertEqual(status, 422)
        self.assertIn('5 MP', body['error'])
        status, _ = self.api.call('POST', '/api/jobs?name=ok.png', raw=png_bytes((2400, 2000)), headers={'Content-Type': 'image/png'})
        self.assertEqual(status, 201)                                                              # 4.8 MP is fine

    def test_a_phone_jpeg_that_pillow_calls_mpo_is_accepted(self):
        first, second = Image.new('RGB', (640, 480), (120, 120, 120)), Image.new('RGB', (640, 480), (100, 100, 100))
        buf = io.BytesIO()
        try:
            first.save(buf, 'MPO', save_all=True, append_images=[second])
        except Exception as ex:                                                                    # pragma: no cover - old Pillow
            self.skipTest(f'this Pillow cannot write MPO: {ex}')
        with Image.open(io.BytesIO(buf.getvalue())) as im:
            if im.format != 'MPO':
                self.skipTest('Pillow did not read it back as MPO')
        status, body = self.post(buf.getvalue())
        self.assertEqual(status, 201, body)
        self.assertEqual(body['image']['name'], 'source.jpg')


class ServerHardening(StudioCase):
    def raw(self, request, read_to_eof=False):
        port = self.server.server_address[1]
        with socket.create_connection(('127.0.0.1', port), timeout=10) as s:
            s.sendall(request.replace('{host}', f'127.0.0.1:{port}').encode('ascii'))
            data = b''
            while b'\r\n\r\n' not in data:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
            head, _, body = data.partition(b'\r\n\r\n')
            length = int(next((line.split(b':')[1] for line in head.split(b'\r\n') if line.lower().startswith(b'content-length')), b'0'))
            while len(body) < length:
                body += s.recv(4096)
            status = int(head.split(b' ')[1])
            closed = read_to_eof and s.recv(1) == b''
        return status, json.loads(body) if body.startswith(b'{') else body, head, closed

    def test_a_network_share_name_is_refused_without_touching_the_filesystem(self):
        import pathlib
        job, _ = self.compiled_job()
        with mock.patch.object(pathlib.Path, 'resolve', side_effect=AssertionError('the filesystem was touched')):
            for path in ('/%5C%5Chost%5Cshare%5Cx', '/%5C%5Chost%5Cshare%5Cx.png', '/C%3A/x', '/js/..%5C..%5Cserver%5Crun.py', '/.git/config'):
                self.assertEqual(self.api.call('GET', path)[0], 404, path)
        for name in ('%5C%5Chost%5Cshare%5Cx.png', '..%5C..%5Cstatus.json', 'C%3A/x.png', 'overlay.png%00.json', '.hidden.png', '%2F%2Fhost%2Fshare%2Fx.png'):
            self.assertEqual(self.api.call('GET', f'/api/jobs/{job}/files/{name}')[0], 404, name)
        self.assertEqual(self.api.call('GET', f'/api/jobs/{job}/files/overlay.png')[0], 200)

    def test_a_bad_length_is_refused_and_an_unread_body_closes_the_connection(self):
        status, body, _, _ = self.raw('POST /api/config HTTP/1.1\r\nHost: {host}\r\nContent-Length: -1\r\n\r\n')
        self.assertEqual(status, 422)
        status, body, _, _ = self.raw('POST /api/config HTTP/1.1\r\nHost: {host}\r\nContent-Length: abc\r\n\r\n')
        self.assertEqual(status, 422)
        big = 31 * 1024 * 1024
        status, body, _, closed = self.raw(f'POST /api/jobs?name=x.png HTTP/1.1\r\nHost: {{host}}\r\nContent-Length: {big}\r\n\r\n', read_to_eof=True)
        self.assertEqual(status, 422)
        self.assertTrue(closed, 'the body was not read, so the connection must not be reused')

    def test_a_second_server_cannot_take_a_port_that_is_in_use(self):
        with tempfile.TemporaryDirectory() as other:
            with self.assertRaises(OSError):
                create_server(Path(other) / 'jobs', self.config, port=self.server.server_address[1])
            self.assertFalse((Path(other) / 'jobs').exists())                                       # and it did not touch any job folder

    def test_a_key_that_cannot_be_sent_is_refused_and_never_echoed(self):
        status, body = self.api.call('POST', '/api/config', {'openai_api_key': 'sk-secret-part\nsecond-part'})
        self.assertEqual(status, 422)
        self.assertNotIn('sk-secret', json.dumps(body))
        self.assertFalse(self.studio.config.public()['configured'])
        status, body = self.api.call('POST', '/api/config', {'openai_api_key': '  sk-fine-key-1234567  '})
        self.assertEqual((status, body['openai']['configured']), (200, True))
        self.assertEqual(self.studio.config.api_key, 'sk-fine-key-1234567')
        self.api.call('POST', '/api/config', {'openai_api_key': ''})

    def test_a_body_that_is_not_an_object_is_a_refusal_not_a_crash(self):
        job = self.new_job()
        for path in ('/api/config', f'/api/jobs/{job}/run', f'/api/jobs/{job}/compile', f'/api/jobs/{job}/auto'):
            for raw in (b'[1]', b'null', b'"x"', b'7'):
                status, body = self.api.call('POST', path, raw=raw, headers={'Content-Type': 'application/json'})
                self.assertEqual(status, 422, (path, raw, body))
                self.assertIn('JSON', body['error'])
        self.assertEqual(self.api.call('POST', '/api/config', raw=b'', headers={'Content-Type': 'application/json'})[0], 200)   # an empty body is an empty object

    def test_the_environment_key_is_trimmed_and_a_broken_one_is_refused_by_name_only(self):
        self.assertEqual(Config('http://x/v1', 'm', 'sk-from-env-1234\r\n').api_key, 'sk-from-env-1234')
        with self.assertRaises(ValueError) as ctx:
            Config('http://x/v1', 'm', 'sk-from-env\n1234')
        self.assertNotIn('sk-from-env', str(ctx.exception))

    def test_one_click_never_redoes_a_finished_compile(self):
        job = self.new_job()
        names = lambda openai=False: steps.auto_names(self.studio.jobs, job, openai)       # noqa: E731
        self.assertEqual(names(), ['compile', 'preview', 'build', 'glb'])                          # nothing compiled yet
        self.assertEqual(self.api.call('PUT', f'/api/jobs/{job}/scene', fixtures.scene())[0], 200)
        self.assertEqual(names(), ['compile', 'preview', 'build', 'glb'])
        self.assertEqual(self.api.call('POST', f'/api/jobs/{job}/compile', {})[0], 202)
        self.wait(job)
        self.assertEqual(names(), ['preview', 'build', 'glb'])
        spec = self.api.call('GET', f'/api/jobs/{job}/spec')[1]
        spec['components'][3]['location'][0] += 0.25
        self.assertEqual(self.api.call('PUT', f'/api/jobs/{job}/spec?origin=edited', spec)[0], 200)
        self.assertEqual(names(), ['preview', 'build', 'glb'])                                     # the edited spec is not overwritten
        self.assertEqual(names(True), ['scene', 'compile', 'preview', 'build', 'glb'])             # reading again does start over
        self.assertEqual(self.api.call('PUT', f'/api/jobs/{job}/scene', fixtures.scene())[0], 200)
        self.assertEqual(names()[0], 'compile')                                                    # a new scene description: compile again
        other = self.new_job()
        self.assertEqual(self.api.call('PUT', f'/api/jobs/{other}/spec?origin=uploaded', self.api.call('GET', f'/api/jobs/{job}/spec')[1])[0], 200)
        self.assertEqual(steps.auto_names(self.studio.jobs, other, False), ['preview', 'build', 'glb'])   # an uploaded spec has nothing to compile
        status, body = self.api.call('POST', f'/api/jobs/{other}/compile', {})                         # and clicking compile there is refused, not "failed"
        self.assertEqual(status, 422)
        self.assertIn('provided directly', body['error'])
        st = self.api.call('GET', f'/api/jobs/{other}')[1]
        self.assertEqual((st['steps']['scene']['state'], st['steps']['compile']['state']), ('skipped', 'skipped'))
        self.studio.jobs.set_step(other, 'compile', 'error', message='left over from an earlier click')
        self.assertEqual(steps.auto_names(self.studio.jobs, other, False), ['preview', 'build', 'glb'])   # an old failure does not bring compile back

    def test_edits_and_uploads_are_refused_while_a_step_runs(self):
        job, _ = self.compiled_job()
        spec = self.api.call('GET', f'/api/jobs/{job}/spec')[1]
        gate = threading.Event()
        self.studio.jobs.start(job, [('build', lambda: gate.wait(20) and {'message': 'ok'}, False)])
        try:
            before = (self.studio.jobs.path(job) / 'spec.json').read_bytes()
            for method, path, body in (('PUT', f'/api/jobs/{job}/spec?origin=edited', spec), ('PUT', f'/api/jobs/{job}/scene', fixtures.scene()),
                                       ('POST', f'/api/jobs/{job}/compile', {})):
                status, answer = self.api.call(method, path, body)
                self.assertEqual(status, 409, (path, answer))
                self.assertIn('still running', answer['error'])
            self.assertEqual((self.studio.jobs.path(job) / 'spec.json').read_bytes(), before)
        finally:
            gate.set()
        self.wait(job)
        self.assertEqual(self.api.call('PUT', f'/api/jobs/{job}/spec?origin=edited', spec)[0], 200)

    def test_a_failed_compile_leaves_the_earlier_results_valid(self):
        job, _ = self.compiled_job()
        for step in ('preview', 'build', 'glb'):
            self.studio.jobs.set_step(job, step, 'done', message='fine', detail={'files': {'blend': 'x'}})
        broken = fixtures.scene()
        (self.studio.jobs.path(job) / 'scene.json').write_text(json.dumps({**broken, 'components': 'not a list'}), encoding='utf-8')
        self.assertEqual(self.api.call('POST', f'/api/jobs/{job}/compile', {})[0], 202)
        st = self.wait(job)
        self.assertEqual(st['steps']['compile']['state'], 'error')
        self.assertEqual({st['steps'][s]['state'] for s in ('preview', 'build', 'glb')}, {'done'})   # nothing was marked stale or erased

    def test_the_hd_request_is_reported_when_the_cap_ignores_it(self):
        job, _ = self.compiled_job()

        def fake_build(spec, out, samples=16, hd_scale=1, timeout=3600):
            out.mkdir(parents=True)
            (out / 'booth.blend').write_bytes(b'blend')
            (out / 'client_render.png').write_bytes(b'png')
            return {'returncode': 0, 'seconds': 1.0, 'log_tail': '', 'report': {'hd': {'requested': 2, 'effective': 1, 'capped': True}}}
        with mock.patch.object(engine, 'build', side_effect=fake_build):
            capped = steps.build_step(self.studio.jobs, job, hd=True)
            plain = steps.build_step(self.studio.jobs, job, hd=False)
        self.assertIn('HD picture was not made', capped['message'])
        self.assertEqual((capped['detail']['hd_requested'], capped['detail']['hd']['capped']), (True, True))
        self.assertNotIn('HD picture', plain['message'])


class EngineHygiene(unittest.TestCase):
    def test_children_do_not_inherit_the_api_key(self):
        with mock.patch.dict(os.environ, {'OPENAI_API_KEY': KEY, 'KEEP_ME': '1'}):
            env = engine.child_env()
        self.assertNotIn('OPENAI_API_KEY', env)
        self.assertEqual(env.get('KEEP_ME'), '1')

    def test_blender_output_is_decoded_safely_and_gets_the_clean_environment(self):
        done = subprocess.CompletedProcess([], 0, None, None)
        with tempfile.TemporaryDirectory() as t, mock.patch.object(engine, 'blender_path', return_value='blender.exe'), \
                mock.patch.dict(os.environ, {'OPENAI_API_KEY': KEY}), mock.patch.object(engine.subprocess, 'run', return_value=done) as run_:
            result = engine.build(Path(t) / 'spec.json', Path(t) / 'out')
        kwargs = run_.call_args.kwargs
        self.assertEqual((kwargs['encoding'], kwargs['errors']), ('utf-8', 'replace'))
        self.assertNotIn('OPENAI_API_KEY', kwargs['env'])
        self.assertEqual(result['log_tail'], '')


class Launcher(unittest.TestCase):
    def settings(self, argv, env=None):
        args = run.argparse.ArgumentParser()
        for name in ('--provider', '--openai-base-url', '--openai-model', '--openai-style'):
            args.add_argument(name, default=None)
        parsed = args.parse_args(argv)
        parsed.provider = parsed.provider or 'openai'
        return run.llm_settings(parsed, env or {})

    def test_providers_preset_the_url_model_and_style_and_anything_explicit_wins(self):
        self.assertEqual(self.settings([]), {'base_url': 'https://api.openai.com/v1', 'model': 'gpt-5.5', 'style': 'responses'})
        self.assertEqual(self.settings(['--provider', 'deepseek']), {'base_url': 'https://api.deepseek.com', 'model': 'deepseek-flash', 'style': 'chat-json'})
        env = {'OPENAI_MODEL': 'deepseek-v4-pro'}
        self.assertEqual(self.settings(['--provider', 'deepseek'], env)['model'], 'deepseek-v4-pro')                    # environment over preset
        self.assertEqual(self.settings(['--provider', 'deepseek', '--openai-model', 'other'], env)['model'], 'other')    # flag over environment
        self.assertEqual(self.settings(['--provider', 'deepseek'], {'OPENAI_API_STYLE': 'chat'})['style'], 'chat')

    def test_an_unknown_style_in_the_environment_is_refused(self):
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as t, mock.patch.dict(os.environ, {'OPENAI_API_STYLE': 'soap'}), contextlib.redirect_stderr(err):
            self.assertEqual(run.main(['--no-browser', '--port', '0', '--jobs-dir', t]), 2)
        self.assertIn('chat-json', err.getvalue())

    def test_an_unknown_provider_in_the_environment_is_refused_and_case_does_not_matter(self):
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as t, mock.patch.dict(os.environ, {'LLM_PROVIDER': 'acme'}), contextlib.redirect_stderr(err):
            self.assertEqual(run.main(['--no-browser', '--port', '0', '--jobs-dir', t]), 2)
        self.assertIn('LLM_PROVIDER must be one of deepseek, openai', err.getvalue())
        self.assertNotIn('Traceback', err.getvalue())
        with tempfile.TemporaryDirectory() as t, mock.patch.dict(os.environ, {'LLM_PROVIDER': ' DeepSeek '}),                 mock.patch.object(run, 'create_server', side_effect=OSError('stop')), contextlib.redirect_stderr(io.StringIO()) as quiet:
            self.assertEqual(run.main(['--no-browser', '--port', '0', '--jobs-dir', t]), 2)     # got past the provider check
        self.assertIn('cannot listen', quiet.getvalue())

    def test_a_broken_environment_key_stops_the_start_without_printing_it(self):
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as t, mock.patch.dict(os.environ, {'OPENAI_API_KEY': 'sk-bad\nkey'}), contextlib.redirect_stderr(err):
            code = run.main(['--no-browser', '--port', '0', '--jobs-dir', t])
            self.assertNotIn('OPENAI_API_KEY', os.environ)                                         # taken out of the environment for the children
        self.assertEqual(code, 2)
        self.assertIn('OPENAI_API_KEY', err.getvalue())
        self.assertNotIn('sk-bad', err.getvalue())

    def test_missing_python_packages_are_named_with_the_install_command(self):
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as t, mock.patch.object(engine, 'missing_packages', return_value=['scipy', 'trimesh']), \
                contextlib.redirect_stderr(err):
            self.assertEqual(run.main(['--no-browser', '--port', '0', '--jobs-dir', t]), 2)
        self.assertIn('scipy, trimesh', err.getvalue())
        self.assertIn('pip install -r booth-image-to-model/requirements.txt', err.getvalue())
        self.assertIn(os.path.basename(sys.executable), engine.INSTALL_HINT)                    # this interpreter, not a bare 'python'
        self.assertEqual(engine.missing_packages(), [])

    def test_the_detached_blender_survives_the_server_on_every_os(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.object(engine, 'blender_path', return_value='blender'), \
                mock.patch.object(engine.subprocess, 'Popen') as popen:
            engine.open_in_blender(Path(t) / 'booth.blend')
        kwargs = popen.call_args.kwargs
        if os.name == 'nt':
            self.assertTrue(kwargs['creationflags'] & engine.subprocess.DETACHED_PROCESS)
        else:
            self.assertIs(kwargs['start_new_session'], True)
        self.assertNotIn('OPENAI_API_KEY', kwargs['env'])

    def test_a_busy_port_is_explained(self):
        err = io.StringIO()
        with socket.socket() as held, tempfile.TemporaryDirectory() as t, contextlib.redirect_stderr(err):
            held.bind(('127.0.0.1', 0))
            held.listen(1)
            code = run.main(['--no-browser', '--port', str(held.getsockname()[1]), '--jobs-dir', t])
        self.assertEqual(code, 2)
        self.assertIn('--port', err.getvalue())


class ReaderHardening(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        (self.dir / 'photo.png').write_bytes(png_bytes())

    def mock(self, script, **kw):
        mock_ = MockOpenAI(fixtures.scene(), script).start()
        self.addCleanup(mock_.stop)
        return mock_, Config(mock_.base_url, 'test-model', KEY, retry_wait=0, **kw)

    def read(self, config, **kw):
        return reader.read_scene(self.dir / 'photo.png', config, self.dir / 'reader', **kw)

    def test_a_redirect_is_not_followed_so_the_key_goes_nowhere_else(self):
        mock_, config = self.mock(['redirect'])
        with self.assertRaises(reader.ReaderError) as ctx:
            self.read(config)
        self.assertIn('redirected', str(ctx.exception))
        self.assertEqual([r['path'] for r in mock_.requests], ['/v1/responses'])                  # /v1/elsewhere was never asked

    def test_a_timeout_is_reported_once_not_retried_three_times(self):
        mock_, config = self.mock(['hang'])
        started = time.time()
        with self.assertRaises(reader.ReaderError) as ctx:
            self.read(config, timeout=1)
        self.assertIn('not retrying', str(ctx.exception))
        self.assertEqual(len(mock_.requests), 1)
        self.assertLess(time.time() - started, 2.9)

    def test_a_refused_output_limit_says_which_setting_to_change_and_is_not_retried(self):
        mock_, config = self.mock(['http400'])
        with self.assertRaises(reader.ReaderError) as ctx:
            self.read(config)
        self.assertIn('OPENAI_MAX_OUTPUT_TOKENS', str(ctx.exception))
        self.assertEqual(len(mock_.requests), 1)
        mock_, config = self.mock(['good'], max_output_tokens=4096)
        self.read(config)
        self.assertEqual(mock_.requests[0]['body']['max_output_tokens'], 4096)

    def test_the_masked_key_a_provider_echoes_is_not_kept(self):
        _, config = self.mock(['http401'])
        with self.assertRaises(reader.ReaderError) as ctx:
            self.read(config)
        self.assertNotIn('sk-proj', str(ctx.exception))
        self.assertIn('Incorrect API key', str(ctx.exception))

    def test_a_key_python_refuses_to_send_is_reported_without_the_key(self):
        mock_, config = self.mock(['good'])
        config._key = 'sk-broken\nkey'                                                             # cannot happen through Config; the reader must still hold
        with self.assertRaises(reader.ReaderError) as ctx:
            self.read(config)
        self.assertNotIn('sk-broken', str(ctx.exception))
        self.assertEqual(mock_.requests, [])

    def test_the_wait_between_tries_follows_retry_after_but_is_capped(self):
        config = Config('http://x/v1', 'm', KEY, retry_wait=2)
        self.assertEqual(reader._retry_wait(config, 0), 2)
        self.assertEqual(reader._retry_wait(config, 1), 4)
        self.assertEqual(reader._retry_wait(config, 0, {'Retry-After': '7'}), 7)
        self.assertEqual(reader._retry_wait(config, 0, {'Retry-After': '9999'}), reader.MAX_WAIT)
        self.assertEqual(reader._retry_wait(config, 0, {'Retry-After': 'soon'}), 2)
        self.assertEqual(reader._retry_wait(Config('http://x/v1', 'm', KEY, retry_wait=0), 0, {'Retry-After': '7'}), 0)


@unittest.skipUnless(os.environ.get('BLENDER_EXE'), 'Set BLENDER_EXE for the Blender steps')
class OneClickWithBlender(StudioCase):
    def test_one_click_keeps_the_edited_spec_and_builds_from_it(self):
        job, _ = self.compiled_job()
        spec = self.api.call('GET', f'/api/jobs/{job}/spec')[1]
        spec['components'][3]['location'][0] += 0.25
        self.assertEqual(self.api.call('PUT', f'/api/jobs/{job}/spec?origin=edited', spec)[0], 200)
        edited = (self.studio.jobs.path(job) / 'spec.json').read_bytes()
        self.assertEqual(self.api.call('POST', f'/api/jobs/{job}/auto', {})[0], 202)
        st = self.wait(job, 1800)
        self.assertEqual([st['steps'][s]['state'] for s in ('compile', 'preview', 'build', 'glb')], ['done'] * 4, st['steps'])
        self.assertEqual((self.studio.jobs.path(job) / 'spec.json').read_bytes(), edited)         # not recompiled over the edit
        self.assertEqual(st['spec']['origin'], 'edited')


if __name__ == '__main__':
    unittest.main()
