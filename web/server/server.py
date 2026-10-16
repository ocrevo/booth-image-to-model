"""Local studio server (standard library only, 127.0.0.1 only): the static site, plus /api for jobs.

    GET  /api/health                      what the machine can do (Blender, Python packages, OpenAI key) - no secrets
    POST /api/config                      {"openai_api_key": "..."}  kept in memory only
    GET  /api/jobs                        recent jobs
    POST /api/jobs?name=photo.jpg         body = the image bytes -> a new job
    GET  /api/jobs/<id>                   status of every step
    PUT  /api/jobs/<id>/scene             body = a scene description (checked, stored)
    PUT  /api/jobs/<id>/spec?origin=...   body = a spec: uploaded (from outside) or edited (from the editor)
    GET  /api/jobs/<id>/spec              the working spec
    POST /api/jobs/<id>/read              {"mode": "openai", "consent": true}
    POST /api/jobs/<id>/run               {"steps": ["scene","compile",...], "mode": "openai", "consent": true, "hd": false}: those steps in order
    POST /api/jobs/<id>/compile | preview | build | glb      {"hd": false} for build
    POST /api/jobs/<id>/auto              run everything that is still to do (read with openai first when asked) -> compile -> preview -> build -> glb
    POST /api/jobs/<id>/open-blender      start Blender with the latest .blend
    GET  /api/jobs/<id>/files/<path>      a file of the job (images, json, glb, blend)
"""
from __future__ import annotations
import json
import mimetypes
import os
import re
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import engine
import steps
from jobs import Busy, BadRequest, Jobs, NotFound
from safety import clean_key, clean_relative, redact

APP = Path(__file__).resolve().parents[1] / 'app'
MIME = {'.js': 'text/javascript', '.mjs': 'text/javascript', '.json': 'application/json', '.css': 'text/css', '.html': 'text/html',
        '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.glb': 'model/gltf-binary', '.blend': 'application/octet-stream',
        '.txt': 'text/plain; charset=utf-8'}
SERVABLE = set(MIME) - {'.html', '.js', '.mjs', '.css'}
JOB_ROUTE = re.compile(r'^/api/jobs/(?P<id>[^/]+)(?:/(?P<rest>.*))?$')
MAX_JSON = 20 * 1024 * 1024
MAX_UPLOAD = 30 * 1024 * 1024
DRAIN_LIMIT = 1024 * 1024              # an unread request body up to this size is read and dropped after an early answer


class Config:
    """OpenAI settings: base URL and model from the environment (or run.py flags); the key only ever lives in this process."""

    def __init__(self, base_url, model, key, style='responses', effort=None, max_output_tokens=32000, retry_wait=2.0):
        self.base_url, self.model = base_url.rstrip('/'), model
        self.style, self.effort, self.max_output_tokens, self.retry_wait = style, effort, max_output_tokens, retry_wait
        self._key = clean_key(key)
        self.key_source = 'env' if self._key else 'none'
        self.lock = threading.Lock()

    @property
    def api_key(self):
        return self._key

    def set_key(self, key):
        cleaned = clean_key(key)
        with self.lock:
            self._key = cleaned
            self.key_source = 'memory' if cleaned else 'none'

    def public(self):
        return {'configured': bool(self._key), 'key_source': self.key_source, 'model': self.model, 'base_url': self.base_url, 'style': self.style}


class Studio:
    def __init__(self, jobs_dir, openai_config):
        self.config = openai_config
        self.jobs = Jobs(jobs_dir, redact=lambda text: redact(text, self.config.api_key))


def make_handler():
    class Handler(BaseHTTPRequestHandler):
        server_version = 'BoothStudio/1'
        protocol_version = 'HTTP/1.1'

        def log_message(self, fmt, *args):
            if getattr(self.server, 'verbose', False):
                super().log_message(fmt, *args)

        # ------------------------------------------------ plumbing
        def send(self, status, body=b'', content_type='application/json', extra=None):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(body)

        def json(self, status, payload):
            self.send(status, json.dumps(payload, ensure_ascii=False).encode('utf-8'))

        def fail(self, status, message, detail=None):
            self.json(status, {'error': message, **(detail or {})})

        def body(self, limit=MAX_JSON):
            try:
                length = int(self.headers.get('Content-Length') or 0)
            except ValueError:
                length = -1
            if length < 0:
                raise BadRequest('Invalid Content-Length')
            if length > limit:
                raise BadRequest(f'The request body is over {limit // (1024 * 1024)} MB')
            self._body_read = True
            return self.rfile.read(length) if length else b''

        def body_json(self):
            raw = self.body()
            try:
                return json.loads(raw.decode('utf-8')) if raw else {}
            except (UnicodeDecodeError, json.JSONDecodeError) as ex:
                raise BadRequest(f'the body is not valid JSON: {ex}') from ex

        def body_object(self):
            """The JSON body, which must be an object (an empty body is an empty object)."""
            value = self.body_json()
            if not isinstance(value, dict):
                raise BadRequest('the body must be a JSON object')
            return value

        @property
        def studio(self):
            return self.server.studio

        def allowed_hosts(self):
            port = self.server.server_address[1]
            return {f'127.0.0.1:{port}', f'localhost:{port}', f'[::1]:{port}'}

        def foreign(self, method):
            """Another web page must not be able to drive this server through the user's browser (cross-site requests, DNS rebinding)."""
            hosts = self.allowed_hosts()
            if (self.headers.get('Host') or '').lower() not in hosts:
                return 'unexpected Host header'
            origin = self.headers.get('Origin')
            if origin and method not in ('GET', 'HEAD') and origin.lower() not in {f'http://{h}' for h in hosts}:
                return 'request from another origin'
            return None

        def dispatch(self, method):
            self._body_read = False
            try:
                refusal = self.foreign(method)
                if refusal:
                    return self.fail(HTTPStatus.FORBIDDEN, refusal)
                url = urlparse(self.path)
                path, query = unquote(url.path), parse_qs(url.query)
                if path.startswith('/api/'):
                    return self.api(method, path, query)
                if method in ('GET', 'HEAD'):
                    return self.static(path)
                return self.fail(HTTPStatus.METHOD_NOT_ALLOWED, 'method not allowed')
            except NotFound:
                return self.fail(HTTPStatus.NOT_FOUND, 'no such job or file')
            except Busy as ex:
                return self.fail(HTTPStatus.CONFLICT, str(ex))
            except BadRequest as ex:
                return self.fail(HTTPStatus.UNPROCESSABLE_ENTITY, str(ex), ex.detail)
            except (BrokenPipeError, ConnectionResetError):
                return None
            except Exception as ex:                                   # a bug: tell the page, keep serving
                return self.fail(HTTPStatus.INTERNAL_SERVER_ERROR, redact(f'{type(ex).__name__}: {ex}', self.studio.config.api_key))
            finally:
                try:
                    length = int(self.headers.get('Content-Length') or 0)
                except ValueError:
                    length = -1
                if (length > 0 and not self._body_read) or length < 0:
                    # An answer went out without the body being read. A small body is read and dropped (closing with unread data
                    # makes Windows reset the connection, which can wipe the answer the browser has not read yet); a large or
                    # nonsensical one just ends the connection.
                    if 0 < length <= DRAIN_LIMIT:
                        try:
                            self.connection.settimeout(5)
                            self.rfile.read(length)
                            self.connection.settimeout(None)
                        except OSError:
                            self.close_connection = True
                    else:
                        self.close_connection = True

        do_GET = lambda self: self.dispatch('GET')            # noqa: E731
        do_HEAD = lambda self: self.dispatch('HEAD')          # noqa: E731
        do_POST = lambda self: self.dispatch('POST')          # noqa: E731
        do_PUT = lambda self: self.dispatch('PUT')            # noqa: E731

        # ------------------------------------------------ static site
        def static(self, path):
            name = 'studio.html' if path in ('/', '') else clean_relative(path.lstrip('/'))
            if name is None:
                return self.fail(HTTPStatus.NOT_FOUND, 'not found')
            target = (APP / name).resolve()
            if APP.resolve() not in target.parents or not target.is_file():
                return self.fail(HTTPStatus.NOT_FOUND, 'not found')
            self.send(HTTPStatus.OK, target.read_bytes(), MIME.get(target.suffix.lower()) or mimetypes.guess_type(str(target))[0] or 'application/octet-stream')

        # ------------------------------------------------ API
        def api(self, method, path, query):
            studio = self.studio
            jobs, config = studio.jobs, studio.config
            if path == '/api/health' and method == 'GET':
                return self.json(HTTPStatus.OK, {'studio': True, 'blender': engine.blender_path(), 'python_missing': engine.missing_packages(),
                                                 'demo_picture': (APP / 'demo' / 'demo_source.png').is_file(),  # see docs/sample-images.md
                                                 'openai': config.public()})
            if path == '/api/config' and method == 'POST':
                try:
                    config.set_key(str(self.body_object().get('openai_api_key') or ''))
                except ValueError as ex:
                    raise BadRequest(str(ex)) from ex
                return self.json(HTTPStatus.OK, {'openai': config.public()})
            if path == '/api/jobs':
                if method == 'GET':
                    return self.json(HTTPStatus.OK, {'jobs': jobs.list()})
                if method == 'POST':
                    return self.json(HTTPStatus.CREATED, jobs.create((query.get('name') or [''])[0], self.body(MAX_UPLOAD)))
            m = JOB_ROUTE.match(path)
            if not m:
                return self.fail(HTTPStatus.NOT_FOUND, 'unknown API route')
            job_id, rest = m.group('id'), m.group('rest') or ''
            jobs.path(job_id)                                          # NotFound for a bad or unknown id
            if rest == '' and method == 'GET':
                return self.json(HTTPStatus.OK, jobs.status(job_id))
            if rest.startswith('files/') and method in ('GET', 'HEAD'):
                return self.job_file(job_id, rest[len('files/'):])
            if rest == 'scene' and method == 'PUT':
                return self.json(HTTPStatus.OK, steps.put_scene(jobs, job_id, self.body_json()))
            if rest == 'spec' and method == 'PUT':
                return self.json(HTTPStatus.OK, steps.put_spec(jobs, job_id, self.body_json(), (query.get('origin') or ['edited'])[0]))
            if rest == 'spec' and method == 'GET':
                return self.job_file(job_id, 'spec.json')
            if method == 'POST':
                return self.post_action(job_id, rest)
            return self.fail(HTTPStatus.NOT_FOUND, 'unknown API route')

        def job_file(self, job_id, relative):
            root = self.studio.jobs.path(job_id).resolve()
            relative = clean_relative(relative)
            if relative is None:
                return self.fail(HTTPStatus.NOT_FOUND, 'no such file')
            target = (root / relative).resolve()
            if root not in target.parents or not target.is_file() or target.suffix.lower() not in SERVABLE:
                return self.fail(HTTPStatus.NOT_FOUND, 'no such file')
            extra = {'Content-Disposition': f'attachment; filename="{target.name}"'} if target.suffix.lower() in ('.blend', '.glb') else None
            self.send(HTTPStatus.OK, target.read_bytes(), MIME[target.suffix.lower()], extra)

        def post_action(self, job_id, action):
            jobs, config = self.studio.jobs, self.studio.config
            options = self.body_object()
            if action == 'open-blender':
                return self.json(HTTPStatus.OK, steps.open_blender(jobs, job_id))
            if action in ('compile', 'preview', 'build', 'glb'):
                steps.check_ready(jobs, job_id, [action])
                jobs.start(job_id, steps.plan(jobs, job_id, [action], hd=bool(options.get('hd'))))
                return self.json(HTTPStatus.ACCEPTED, {'started': [action]})
            if action == 'run':
                names = steps.run_names(options.get('steps'))
                if 'scene' in names:
                    self.check_openai(options)
            elif action == 'read':
                self.check_openai(options)
                names = ['scene']
            elif action == 'auto':
                openai = options.get('mode') == 'openai'
                if openai:
                    self.check_openai(options)
                names = steps.auto_names(jobs, job_id, openai)
            else:
                names = None
            if names:
                steps.check_ready(jobs, job_id, names)
                jobs.start(job_id, steps.plan(jobs, job_id, names, config=config, hd=bool(options.get('hd'))))
                return self.json(HTTPStatus.ACCEPTED, {'started': names})
            return self.fail(HTTPStatus.NOT_FOUND, 'unknown action')

        def check_openai(self, options):
            if options.get('mode') != 'openai':
                raise BadRequest('mode must be "openai"')
            if not self.studio.config.api_key:
                raise BadRequest('No large-model API key is set: set the environment variable OPENAI_API_KEY (the key of any OpenAI-compatible service goes here, for example DeepSeek), or paste the key on the page')
            if options.get('consent') is not True:
                raise BadRequest('Tick "I agree to send this picture to the configured large-model service" first')

    return Handler


class StudioServer(ThreadingHTTPServer):
    request_queue_size = 128                 # the default of 5 refuses connections when a browser fetches many files at once
    daemon_threads = True
    allow_reuse_address = os.name != 'nt'    # on Windows SO_REUSEADDR lets a second server bind a port that is in use
    verbose = False

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):      # a browser closing a kept-alive connection is not an error
            return
        super().handle_error(request, client_address)


def create_server(jobs_dir, openai_config, port=8800, host='127.0.0.1'):
    server = StudioServer((host, port), make_handler())           # bind first: a busy port must not touch the jobs of the other instance
    server.studio = Studio(jobs_dir, openai_config)
    return server
