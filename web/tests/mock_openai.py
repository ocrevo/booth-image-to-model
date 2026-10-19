"""A stand-in for the OpenAI API on 127.0.0.1, for tests and offline demos. It records every request and answers from a script.

    python web/tests/mock_openai.py --scene scene.json [--port 0]        prints the port; Ctrl+C stops it

Behaviours (one per request, the last one repeats): good, bad_scene, not_json, refusal, http401 (echoes a masked key, like the
real one), http400 (output limit refused), http500, redirect (302 to another path), hang (answers after 3 s), incomplete,
no_fit_keys (the scene without corner_points[].fit, as a service that ignores the schema might write it), few_corners (the scene with
only five corner points), review (the scene with one corner clicked far from where it belongs, as scene_fixture.review_scene() writes
it: the compiled quality gate says "review"), no_layout_keys (the scene without booth_type, open_sides and cutaway_sides),
bad_layout (booth_type "inline" with three open sides), twice_listed (a side listed twice in open_sides), topology (a reading that
contradicts nothing itself but whose open side the engine's layout check fails, as scene_fixture.topology_scene('camera') writes it:
the compiled quality gate says "review").
Both wire formats are served: POST /v1/responses and POST /v1/chat/completions.
"""
from __future__ import annotations
import argparse
import copy
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

USAGE = {'input_tokens': 4321, 'output_tokens': 987}


class MockOpenAI:
    def __init__(self, scene, script=('good',)):
        self.scene = scene
        self.script = list(script)
        self.requests = []
        self.lock = threading.Lock()
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)).decode('utf-8'))
                with mock.lock:
                    mock.requests.append({'path': self.path, 'headers': {k.lower(): v for k, v in self.headers.items()}, 'body': body})
                    behaviour = mock.script.pop(0) if len(mock.script) > 1 else mock.script[0]
                    if 'reject' in (self.headers.get('Authorization') or ''):          # a key with "reject" in it is a key the service does not accept
                        behaviour = 'http401'
                if behaviour == 'hang':
                    time.sleep(3)
                status, payload = mock.answer(self.path, behaviour)
                extra = payload.pop('_headers', {}) if isinstance(payload, dict) else {}
                data = json.dumps(payload).encode('utf-8')
                self.send_response(status)
                for k, v in extra.items():
                    self.send_header(k, v)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except ConnectionError:                       # the client gave up (a test of timeouts)
                    pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self):
        return f'http://127.0.0.1:{self.server.server_address[1]}/v1'

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def text_for(self, behaviour):
        scene = copy.deepcopy(self.scene)
        if behaviour == 'bad_scene':
            scene['components'][0]['scope'] = 'nowhere'
        if behaviour == 'not_json':
            return 'Sorry, I cannot produce that.'
        if behaviour == 'no_fit_keys':
            for point in scene['corner_points']:
                point.pop('fit', None)
        if behaviour == 'few_corners':
            scene['corner_points'] = scene['corner_points'][:5]
        if behaviour == 'no_layout_keys':
            for key in ('booth_type', 'open_sides', 'cutaway_sides'):
                scene.pop(key, None)
        if behaviour == 'bad_layout':
            scene['booth_type'] = 'inline'
        if behaviour == 'twice_listed':
            scene['open_sides'] = ['front', 'left', 'left', 'right']
        if behaviour == 'topology':
            import scene_fixture                              # imported late like the review scene: it pulls in the skill's test helpers
            scene = scene_fixture.topology_scene('camera', scene)
        if behaviour == 'review':
            import scene_fixture                              # the one definition of that scene; imported late, it pulls in the skill's test helpers
            scene = scene_fixture.review_scene(scene)
        return json.dumps(scene)

    def answer(self, path, behaviour):
        chat = path.endswith('/chat/completions')
        if behaviour == 'http401':
            return 401, {'error': {'message': 'Incorrect API key provided: sk-proj-********wXyZ. You can find your API key at the usual place.',
                                   'type': 'invalid_request_error'}}
        if behaviour == 'http400':
            return 400, {'error': {'message': 'max_output_tokens is too large: 32000. This model supports at most 16384 completion tokens.',
                                   'type': 'invalid_request_error'}}
        if behaviour == 'redirect':
            return 302, {'_headers': {'Location': '/v1/elsewhere'}}
        if behaviour == 'http500':
            return 500, {'error': {'message': 'The server had an error', 'type': 'server_error'}}
        text = self.text_for(behaviour)
        if chat:
            message = {'role': 'assistant', 'content': text}
            finish = 'stop'
            if behaviour == 'refusal':
                message = {'role': 'assistant', 'content': None, 'refusal': 'I cannot help with that.'}
            if behaviour == 'incomplete':
                finish = 'length'
            return 200, {'id': 'chatcmpl-mock', 'choices': [{'index': 0, 'message': message, 'finish_reason': finish}],
                         'usage': {'prompt_tokens': USAGE['input_tokens'], 'completion_tokens': USAGE['output_tokens']}}
        parts = [{'type': 'output_text', 'text': text, 'annotations': []}]
        if behaviour == 'refusal':
            parts = [{'type': 'refusal', 'refusal': 'I cannot help with that.'}]
        payload = {'id': 'resp_mock', 'status': 'completed', 'error': None, 'incomplete_details': None, 'usage': {**USAGE, 'total_tokens': sum(USAGE.values())},
                   'output': [{'type': 'message', 'id': 'msg_mock', 'role': 'assistant', 'status': 'completed', 'content': parts}]}
        if behaviour == 'incomplete':
            payload.update(status='incomplete', incomplete_details={'reason': 'max_output_tokens'})
        return 200, payload


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    p.add_argument('--scene', required=True)
    p.add_argument('--port', type=int, default=0)
    p.add_argument('--script', default='good', help='comma separated behaviours')
    a = p.parse_args()
    mock = MockOpenAI(json.loads(Path(a.scene).read_text(encoding='utf-8')), a.script.split(','))
    mock.server.server_close()
    mock.server = ThreadingHTTPServer(('127.0.0.1', a.port), mock.server.RequestHandlerClass)
    mock.thread = threading.Thread(target=mock.server.serve_forever, daemon=True)
    mock.start()
    print(f'mock openai at {mock.base_url}', flush=True)
    try:
        mock.thread.join()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
