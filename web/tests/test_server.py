"""Studio server: every step of the flow on its own, through the real HTTP API, using the synthetic fixtures of the skill tests.

    python -m unittest discover -s web/tests -p "test_*.py"        (from the repository root)

The Blender steps run only when BLENDER_EXE is set (the regression runner and the smoke test set it).
"""
from __future__ import annotations
import http.client
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / 'web' / 'server'))
sys.path.insert(0, str(REPO / 'booth-image-to-model' / 'tests'))
from PIL import Image

import engine                                        # noqa: E402
import scene_fixture as fixtures                      # noqa: E402  (the synthetic scene as a reader writes it now; camera and pixel helpers)
from server import Config, create_server             # noqa: E402


def png_bytes(size=(fixtures.IMG_W, fixtures.IMG_H)):
    buf = io.BytesIO()
    Image.new('RGB', size, (120, 120, 120)).save(buf, 'PNG')
    return buf.getvalue()


class Api:
    def __init__(self, base):
        self.base = base

    def call(self, method, path, body=None, raw=None, headers=None):
        data = raw if raw is not None else (json.dumps(body).encode('utf-8') if body is not None else None)
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers or ({'Content-Type': 'application/json'} if body is not None else {}))
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                payload = r.read()
                kind = r.headers.get('Content-Type', '')
                return r.status, (json.loads(payload) if 'json' in kind else payload)
        except urllib.error.HTTPError as ex:
            payload = ex.read()
            return ex.code, (json.loads(payload) if payload.startswith(b'{') else payload)


class StudioCase(unittest.TestCase):
    key = None

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.config = Config('http://127.0.0.1:9/v1', 'test-model', cls.key)
        cls.server = create_server(Path(cls._tmp.name) / 'jobs', cls.config, port=0)
        cls.studio = cls.server.studio
        cls.api = Api(f'http://127.0.0.1:{cls.server.server_address[1]}')
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.fit = None

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls._tmp.cleanup()

    # -- helpers
    def new_job(self):
        status, job = self.api.call('POST', '/api/jobs?name=booth.png', raw=png_bytes(), headers={'Content-Type': 'image/png'})
        self.assertEqual(status, 201, job)
        return job['id']

    def wait(self, job_id, timeout=300):
        end = time.time() + timeout
        while time.time() < end:
            _, st = self.api.call('GET', f'/api/jobs/{job_id}')
            if not st['busy'] and not any(s['state'] in ('running', 'queued') for s in st['steps'].values()):
                return st
            time.sleep(0.25)
        self.fail('the job did not finish in time')

    def compiled_job(self):
        job = self.new_job()
        self.assertEqual(self.api.call('PUT', f'/api/jobs/{job}/scene', fixtures.scene())[0], 200)
        self.assertEqual(self.api.call('POST', f'/api/jobs/{job}/compile', {})[0], 202)
        st = self.wait(job)
        self.assertEqual(st['steps']['compile']['state'], 'done', st['steps']['compile'])
        return job, st


class FlowSteps(StudioCase):
    def test_health_has_no_secrets(self):
        status, h = self.api.call('GET', '/api/health')
        self.assertEqual(status, 200)
        self.assertTrue(h['studio'])
        self.assertEqual(h['openai'], {'configured': False, 'key_source': 'none', 'model': 'test-model',
                                       'base_url': 'http://127.0.0.1:9/v1', 'style': 'responses'})
        self.assertNotIn('api_key', json.dumps(h))
        self.assertEqual(h['python_missing'], [])                     # every package the engine needs is importable here
        self.assertEqual(h['demo_picture'], (REPO / 'web' / 'app' / 'demo' / 'demo_source.png').is_file())   # docs/sample-images.md

    def test_upload_checks_the_image(self):
        for raw, why in ((b'', 'empty'), (b'not an image', 'text'), (png_bytes((32, 32)), 'tiny')):
            status, body = self.api.call('POST', '/api/jobs?name=x.png', raw=raw, headers={'Content-Type': 'image/png'})
            self.assertEqual(status, 422, why)
            self.assertIn('error', body)
        buf = io.BytesIO()
        Image.new('RGB', (200, 200)).save(buf, 'GIF')
        self.assertEqual(self.api.call('POST', '/api/jobs?name=x.gif', raw=buf.getvalue(), headers={'Content-Type': 'image/gif'})[0], 422)
        job = self.new_job()
        status, st = self.api.call('GET', f'/api/jobs/{job}')
        self.assertEqual((st['image']['width'], st['image']['height'], st['image']['name']), (fixtures.IMG_W, fixtures.IMG_H, 'source.png'))
        self.assertEqual({s['state'] for s in st['steps'].values()}, {'idle'})
        self.assertIn(job, [j['id'] for j in self.api.call('GET', '/api/jobs')[1]['jobs']])
        self.assertEqual(self.api.call('GET', '/api/jobs/20200101-000000-abcd')[0], 404)
        self.assertEqual(self.api.call('GET', '/api/jobs/not-a-job')[0], 404)

    def test_a_scene_is_checked_before_it_is_stored(self):
        job = self.new_job()
        bad = fixtures.scene()
        bad['components'][0]['scope'] = 'nowhere'
        status, body = self.api.call('PUT', f'/api/jobs/{job}/scene', bad)
        self.assertEqual(status, 422)
        self.assertTrue(any(e['code'] == 'SCOPE' and 'back_wall' in e['where'] for e in body['errors']), body)
        wrong_size = fixtures.scene()
        wrong_size['image'] = {'width': 640, 'height': 400}
        status, body = self.api.call('PUT', f'/api/jobs/{job}/scene', wrong_size)
        self.assertEqual(status, 422)
        self.assertIn('image', json.dumps(body['errors']))
        self.assertEqual(self.api.call('PUT', f'/api/jobs/{job}/scene', raw=b'[1, 2]', headers={'Content-Type': 'application/json'})[0], 422)
        self.assertEqual(self.api.call('PUT', f'/api/jobs/{job}/scene', raw=b'{broken', headers={'Content-Type': 'application/json'})[0], 422)
        self.assertFalse((self.studio.jobs.path(job) / 'scene.json').exists())
        status, ok = self.api.call('PUT', f'/api/jobs/{job}/scene', fixtures.scene())
        self.assertEqual((status, ok['components']), (200, len(fixtures.scene()['components'])))
        _, st = self.api.call('GET', f'/api/jobs/{job}')
        self.assertEqual(st['steps']['scene']['state'], 'done')

    def test_compile_makes_a_spec_a_report_and_an_overlay(self):
        job, st = self.compiled_job()
        detail = st['steps']['compile']['detail']
        self.assertEqual((detail['status'], detail['valid'], detail['fit']['status']), ('ok', True, 'fitted'))
        self.assertGreater(detail['components'], 50)
        self.assertEqual(detail['overlay'], 'overlay.png')
        self.assertEqual(st['spec']['origin'], 'compiled')
        status, spec = self.api.call('GET', f'/api/jobs/{job}/spec')
        self.assertEqual((status, spec['source']['image']), (200, 'source.png'))
        status, overlay = self.api.call('GET', f'/api/jobs/{job}/files/overlay.png')
        self.assertEqual((status, overlay[:4]), (200, b'\x89PNG'))
        self.assertTrue(any(p['id'] == 'floor_bl' for p in detail['points']))

    def test_a_step_without_its_input_is_refused_up_front_and_changes_nothing(self):
        job = self.new_job()
        for action in ('compile', 'auto'):
            status, body = self.api.call('POST', f'/api/jobs/{job}/{action}', {})
            self.assertEqual(status, 422, action)
            self.assertIn('Read the picture', body['error'])
        for action in ('preview', 'build', 'glb'):
            status, body = self.api.call('POST', f'/api/jobs/{job}/{action}', {})
            self.assertEqual(status, 422, action)
            self.assertIn('spec', body['error'])
        st = self.api.call('GET', f'/api/jobs/{job}')[1]
        self.assertEqual({s['state'] for s in st['steps'].values()}, {'idle'})                    # no step was started, none marked failed
        self.assertEqual(st['files'], {'scene': False, 'spec': False})

    def test_an_uploaded_spec_must_be_for_this_image(self):
        job, _ = self.compiled_job()
        spec = self.api.call('GET', f'/api/jobs/{job}/spec')[1]
        other = self.new_job()
        different = json.loads(json.dumps(spec))
        different['source']['sha256'] = '0' * 64
        status, body = self.api.call('PUT', f'/api/jobs/{other}/spec?origin=uploaded', different)
        self.assertEqual(status, 422)
        self.assertIn('sha256', body['error'])
        self.assertFalse((self.studio.jobs.path(other) / 'spec.json').exists())
        renamed = json.loads(json.dumps(spec))
        renamed['source']['image'] = 'whatever_name.jpg'                      # the name is the uploader's business; the bytes are what count
        status, body = self.api.call('PUT', f'/api/jobs/{other}/spec?origin=uploaded', renamed)
        self.assertEqual(status, 200, body)
        _, st = self.api.call('GET', f'/api/jobs/{other}')
        self.assertEqual((st['spec']['origin'], st['steps']['scene']['state'], st['steps']['compile']['state']), ('uploaded', 'skipped', 'skipped'))
        self.assertEqual(self.api.call('GET', f'/api/jobs/{other}/spec')[1]['source']['image'], 'source.png')
        self.assertEqual(self.api.call('PUT', f'/api/jobs/{other}/spec?origin=bogus', spec)[0], 422)
        self.assertEqual(self.api.call('PUT', f'/api/jobs/{other}/spec', {'hello': 1})[0], 422)

    def test_saving_from_the_editor_validates_and_marks_later_steps_stale(self):
        job, _ = self.compiled_job()
        for step in ('preview', 'build', 'glb'):
            self.studio.jobs.set_step(job, step, 'done', message='pretend')
        spec = self.api.call('GET', f'/api/jobs/{job}/spec')[1]
        before = (self.studio.jobs.path(job) / 'spec.json').read_bytes()
        broken = json.loads(json.dumps(spec))
        broken['components'][3]['params']['size'] = [-1, 1, 1]
        status, body = self.api.call('PUT', f'/api/jobs/{job}/spec?origin=edited', broken)
        self.assertEqual(status, 422)
        self.assertTrue(body['errors'])
        self.assertEqual((self.studio.jobs.path(job) / 'spec.json').read_bytes(), before)       # a refused save changes nothing
        edited = json.loads(json.dumps(spec))
        edited['components'][3]['location'][0] += 0.25
        status, body = self.api.call('PUT', f'/api/jobs/{job}/spec?origin=edited', edited)
        self.assertEqual(status, 200, body)
        _, st = self.api.call('GET', f'/api/jobs/{job}')
        self.assertEqual(st['spec']['origin'], 'edited')
        self.assertEqual({st['steps'][s]['state'] for s in ('preview', 'build', 'glb')}, {'stale'})
        self.assertEqual(self.api.call('GET', f'/api/jobs/{job}/spec')[1]['components'][3]['location'][0], edited['components'][3]['location'][0])

    def test_glb_export_needs_no_blender(self):
        job, _ = self.compiled_job()
        self.assertEqual(self.api.call('POST', f'/api/jobs/{job}/glb', {})[0], 202)
        st = self.wait(job)
        self.assertEqual(st['steps']['glb']['state'], 'done', st['steps']['glb'])
        status, glb = self.api.call('GET', f'/api/jobs/{job}/files/exports/booth.glb')
        self.assertEqual((status, glb[:4]), (200, b'glTF'))
        self.assertGreater(st['steps']['glb']['detail']['exported'], 50)

    def test_only_one_step_runs_per_job(self):
        job = self.new_job()
        self.api.call('PUT', f'/api/jobs/{job}/scene', fixtures.scene())
        self.assertEqual(self.api.call('POST', f'/api/jobs/{job}/compile', {})[0], 202)
        status, body = self.api.call('POST', f'/api/jobs/{job}/glb', {})
        self.assertEqual(status, 409)
        self.wait(job)

    def test_files_stay_inside_the_job(self):
        job, _ = self.compiled_job()
        for bad in ('../../status.json', '..%2F..%2Fserver%2Frun.py', '.hidden', 'compile/spec.json.tmp', 'nope.png'):
            self.assertEqual(self.api.call('GET', f'/api/jobs/{job}/files/{bad}')[0], 404, bad)
        self.assertEqual(self.api.call('GET', f'/api/jobs/{job}/files/source.png')[0], 200)
        for bad in ('/../server/run.py', '/..%2Fserver%2Frun.py', '/js/../../server/run.py', '/nope.html'):
            self.assertEqual(self.api.call('GET', bad)[0], 404, bad)

    def test_reading_with_openai_needs_a_key_and_consent(self):
        job = self.new_job()
        status, body = self.api.call('POST', f'/api/jobs/{job}/read', {'mode': 'openai', 'consent': True})
        self.assertEqual(status, 422)
        self.assertIn('OPENAI_API_KEY', body['error'])
        self.assertEqual(self.api.call('POST', f'/api/jobs/{job}/auto', {'mode': 'openai', 'consent': True})[0], 422)

    def test_other_web_pages_cannot_drive_the_server(self):
        port = self.server.server_address[1]

        def send(method, path, headers, body=None):
            conn = http.client.HTTPConnection('127.0.0.1', port, timeout=30)
            try:
                conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
                for k, v in headers.items():
                    conn.putheader(k, v)
                conn.putheader('Content-Length', str(len(body or b'')))
                conn.endheaders(body)
                return conn.getresponse().status
            finally:
                conn.close()
        own = f'127.0.0.1:{port}'
        key = json.dumps({'openai_api_key': 'sk-evil'}).encode()
        self.assertEqual(send('GET', '/api/health', {'Host': 'evil.example:80'}), 403)                         # DNS rebinding
        self.assertEqual(send('POST', '/api/config', {'Host': own, 'Origin': 'http://evil.example', 'Content-Type': 'text/plain'}, key), 403)
        self.assertEqual(send('POST', '/api/config', {'Host': own, 'Origin': 'null'}, key), 403)
        self.assertFalse(self.studio.config.public()['configured'])
        self.assertEqual(send('GET', '/api/health', {'Host': own, 'Origin': 'http://evil.example'}), 200)       # reading is blocked by the browser itself
        self.assertEqual(send('POST', '/api/config', {'Host': own, 'Origin': f'http://{own}'}, b'{}'), 200)       # the page's own origin
        self.assertEqual(send('POST', '/api/config', {'Host': f'localhost:{port}'}, b'{}'), 200)                  # no Origin: scripts and tests

    def test_run_takes_the_steps_a_page_asks_for_in_order_and_checks_them(self):
        job = self.new_job()
        self.assertEqual(self.api.call('PUT', f'/api/jobs/{job}/scene', fixtures.scene())[0], 200)
        for bad in (None, 'compile', [], [3], ['nonsense'], ['glb', 'compile'], ['compile', 'compile'], ['compile', 'scene']):
            status, body = self.api.call('POST', f'/api/jobs/{job}/run', {'steps': bad})
            self.assertEqual(status, 422, bad)
        status, body = self.api.call('POST', f'/api/jobs/{job}/run', {'steps': ['scene', 'compile']})                    # reading needs the mode
        self.assertEqual(status, 422)
        status, body = self.api.call('POST', f'/api/jobs/{job}/run', {'steps': ['scene', 'compile'], 'mode': 'openai', 'consent': True})
        self.assertEqual(status, 422)                                                                                       # and a key
        self.assertIn('OPENAI_API_KEY', body['error'])
        status, body = self.api.call('POST', f'/api/jobs/{job}/run', {'steps': ['preview']})                                # a step without its input
        self.assertEqual(status, 422)
        self.assertIn('spec', body['error'])
        self.assertEqual({s['state'] for s in self.api.call('GET', f'/api/jobs/{job}')[1]['steps'].values()} - {'done'}, {'idle'})       # refusals changed nothing
        status, body = self.api.call('POST', f'/api/jobs/{job}/run', {'steps': ['compile', 'glb']})
        self.assertEqual((status, body['started']), (202, ['compile', 'glb']))
        st = self.wait(job)
        self.assertEqual([st['steps'][s]['state'] for s in ('compile', 'glb')], ['done', 'done'])

    def test_the_job_list_names_the_picture_file_for_thumbnails(self):
        job = self.new_job()
        entry = next(j for j in self.api.call('GET', '/api/jobs')[1]['jobs'] if j['id'] == job)
        self.assertEqual(entry['file'], 'source.png')
        self.assertEqual(self.api.call('GET', f"/api/jobs/{job}/files/{entry['file']}")[0], 200)

    def test_the_static_site_is_served(self):
        for path, marker in (('/', b'studio.js'), ('/studio.html', b'studio.js'), ('/index.html', b'Booth Spec Editor'), ('/js/app.js', b'Viewport'),
                             ('/js/studio.js', b'createEditor'), ('/css/studio.css', b'.st-')):
            status, body = self.api.call('GET', path)
            self.assertEqual(status, 200, path)
            self.assertIn(marker, body, path)


class KeyHandling(StudioCase):
    def test_a_pasted_key_is_kept_in_memory_and_never_echoed(self):
        status, h = self.api.call('POST', '/api/config', {'openai_api_key': 'sk-test-secret'})
        self.assertEqual((status, h['openai']['configured'], h['openai']['key_source']), (200, True, 'memory'))
        self.assertNotIn('sk-test-secret', json.dumps(self.api.call('GET', '/api/health')[1]))
        job = self.new_job()
        status, body = self.api.call('POST', f'/api/jobs/{job}/read', {'mode': 'openai', 'consent': False})
        self.assertEqual(status, 422)
        self.assertIn('I agree', body['error'])
        self.assertEqual(self.api.call('POST', '/api/config', {'openai_api_key': ''})[1]['openai']['configured'], False)
        self.assertFalse(any('sk-test-secret' in p.read_text(errors='ignore') for p in Path(self.studio.jobs.root).rglob('*.json')))


@unittest.skipUnless(os.environ.get('BLENDER_EXE'), 'Set BLENDER_EXE for the Blender steps')
class BlenderSteps(StudioCase):
    def test_preview_build_and_open(self):
        job, _ = self.compiled_job()
        self.assertEqual(self.api.call('POST', f'/api/jobs/{job}/preview', {})[0], 202)
        st = self.wait(job, 600)
        self.assertEqual(st['steps']['preview']['state'], 'done', st['steps']['preview'])
        status, sheet = self.api.call('GET', f"/api/jobs/{job}/files/{st['steps']['preview']['detail']['sheet']}")
        self.assertEqual((status, sheet[:4]), (200, b'\x89PNG'))
        status, render = self.api.call('GET', f"/api/jobs/{job}/files/{st['steps']['preview']['detail']['render']}")             # the render alone, for before/after
        self.assertEqual((status, render[:4]), (200, b'\x89PNG'))
        self.assertIn('not_checked', st['steps']['preview']['detail'])
        self.assertEqual(self.api.call('POST', f'/api/jobs/{job}/open-blender', {})[0], 422)         # nothing built yet
        self.assertEqual(self.api.call('POST', f'/api/jobs/{job}/build', {})[0], 202)
        st = self.wait(job, 1800)
        self.assertEqual(st['steps']['build']['state'], 'done', st['steps']['build'])
        files = st['steps']['build']['detail']['files']
        for key in ('blend', 'client'):
            self.assertEqual(self.api.call('GET', f'/api/jobs/{job}/files/{files[key]}')[0], 200, key)
        with mock.patch.object(engine, 'open_in_blender', return_value={'launched': 'x'}) as launch:
            status, body = self.api.call('POST', f'/api/jobs/{job}/open-blender', {})
        self.assertEqual(status, 200, body)
        self.assertTrue(str(launch.call_args[0][0]).endswith('booth.blend'))


if __name__ == '__main__':
    unittest.main()
