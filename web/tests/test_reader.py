"""The OpenAI reading step against a local stand-in for the API (tests/mock_openai.py): requests are checked, so no key and no money are needed.

    python -m unittest discover -s web/tests -p "test_*.py"        (from the repository root)
"""
from __future__ import annotations
import base64
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / 'web' / 'server'))
sys.path.insert(0, str(REPO / 'booth-image-to-model' / 'tests'))
import numpy as np
from PIL import Image

import engine                                        # noqa: E402
import grid                                          # noqa: E402
import reader_openai as reader                       # noqa: E402
import scene_fixture as fixtures                      # noqa: E402
from mock_openai import MockOpenAI                   # noqa: E402
from server import Config, create_server             # noqa: E402
from test_server import Api, png_bytes               # noqa: E402

KEY = 'sk-test-secret-0123456789'


def image_file(folder, size=(fixtures.IMG_W, fixtures.IMG_H)):
    path = Path(folder) / 'photo.png'
    path.write_bytes(png_bytes(size))
    return path


def decode(url):
    head, _, data = url.partition(',')
    assert head == 'data:image/jpeg;base64', head
    return Image.open(io.BytesIO(base64.b64decode(data)))


class Pictures(unittest.TestCase):
    def test_small_pictures_are_sent_as_they_are(self):
        with tempfile.TemporaryDirectory() as t:
            r = grid.prepare_images(image_file(t))
        self.assertEqual((r['sent'], r['original'], r['scale']), ((960, 600), (960, 600), 1.0))
        plain, marked = (Image.open(io.BytesIO(r[k])) for k in ('plain', 'grid'))
        self.assertEqual((plain.size, marked.size), ((960, 600), (960, 600)))
        self.assertNotEqual(r['plain'], r['grid'])

    def test_large_pictures_are_scaled_down_and_the_scale_is_returned(self):
        with tempfile.TemporaryDirectory() as t:
            r = grid.prepare_images(image_file(t, (4096, 2560)))
        self.assertEqual((r['sent'], r['original']), ((2048, 1280), (4096, 2560)))
        self.assertAlmostEqual(r['scale'], 2.0)

    def test_the_grid_marks_the_picture(self):
        im = Image.new('RGB', (400, 300), (60, 60, 60))
        marked = grid.draw_grid(im)
        self.assertEqual(marked.size, im.size)
        self.assertEqual(marked.getpixel((125, 125)), (60, 60, 60))         # between lines (every 50 px here) nothing is drawn
        self.assertNotEqual(marked.getpixel((100, 125)), (60, 60, 60))      # on a grid line something is

    @staticmethod
    def drawn(im, box, colour=None, background=(60, 60, 60)):
        """How many pixels in box = (x0, y0, x1, y1) are `colour` (pure yellow: label text; the lines are cyan), or, without one, are not
        the background."""
        pixels = np.asarray(im.crop(box))
        return int((pixels == colour).all(axis=2).sum() if colour else (pixels != background).any(axis=2).sum())

    def test_numbers_are_on_all_four_edges_and_inside_the_picture(self):
        w, h = 960, 600
        marked = grid.draw_grid(Image.new('RGB', (w, h), (60, 60, 60)))
        yellow = (255, 255, 0)
        self.assertGreater(self.drawn(marked, (0, 0, w, 18), yellow), 30)                # x along the top edge
        self.assertGreater(self.drawn(marked, (0, h - 18, w, h), yellow), 30)            # x along the bottom edge
        self.assertGreater(self.drawn(marked, (0, 40, 30, h - 30), yellow), 15)          # y down the left edge
        self.assertGreater(self.drawn(marked, (w - 30, 40, w, h - 30), yellow), 15)      # y down the right edge
        for x, y in ((150, 150), (450, 300), (750, 450)):                                # x,y labels at every third crossing, with a tick on it
            self.assertGreater(self.drawn(marked, (x - 5, y - 5, x + 5, y + 5), yellow), 5, (x, y))
            self.assertGreater(self.drawn(marked, (x + 5, y + 3, x + 45, y + 18)), 40, (x, y))
        self.assertEqual(self.drawn(marked, (101, 51, 149, 99)), 0)                      # inside a grid cell, away from the lines, the picture is untouched
        self.assertEqual(marked.getpixel((125, 125)), (60, 60, 60))

    def test_a_label_that_does_not_fit_is_left_out_instead_of_being_cut(self):
        for size in ((960, 600), (2048, 1280), (400, 300), (1500, 1000)):
            marked = grid.draw_grid(Image.new('RGB', size, (60, 60, 60)))
            w, h = size
            for box in ((0, 0, w, 1), (0, h - 1, w, h), (0, 0, 1, h), (w - 1, 0, w, h)):    # the picture's own border row and column
                self.assertEqual(self.drawn(marked, box, (255, 255, 0)), 0, (size, box))                  # no number is cut off by it


class Contract(unittest.TestCase):
    def test_the_provider_schema_can_be_used_in_strict_mode(self):
        schema = reader.provider_schema()
        self.assertNotIn('$schema', schema)
        self.assertNotIn('title', schema)
        seen = []

        def walk(node, where):
            if not isinstance(node, dict):
                return
            if node.get('type') == 'object' and 'properties' in node:
                seen.append(where)
                self.assertIs(node.get('additionalProperties'), False, where)
                self.assertEqual(sorted(node['required']), sorted(node['properties']), where)
            for key, value in node.items():
                if key == 'properties':
                    for name, sub in value.items():
                        walk(sub, f'{where}.{name}')
                elif key == 'items':
                    walk(value, f'{where}[]')
        walk(schema, 'scene')
        self.assertGreater(len(seen), 5)
        self.assertEqual(schema['properties']['image']['type'], 'object')
        point = schema['properties']['corner_points']['items']
        self.assertIn('fit', point['required'])                                                  # every point carries the flag ...
        self.assertEqual(sorted(point['properties']['fit']['type']), ['boolean', 'null'])       # ... which may be null (= used in the fit)
        self.assertIn('1.1', schema['properties']['schema_version']['enum'])

    def test_pixels_are_scaled_back_to_the_original_picture(self):
        scene = fixtures.scene()
        out = reader.scale_scene_pixels(scene, 2.0, 1920, 1200)
        self.assertEqual(out['image'], {'width': 1920, 'height': 1200})
        self.assertEqual(out['corner_points'][0]['uv_px'], [round(v * 2, 2) for v in scene['corner_points'][0]['uv_px']])
        self.assertEqual([p['fit'] for p in out['corner_points']], [p['fit'] for p in scene['corner_points']])      # the flags travel with the points
        held = reader.scale_scene_pixels(fixtures.held_out_scene(), 2.0, 1920, 1200)
        self.assertEqual([p['id'] for p in held['corner_points'] if p['fit'] is False], fixtures.HELD_OUT_IDS)
        for before, after in zip(scene['components'], out['components']):
            for key in ('roi_px', 'wall_rect_px'):
                if before[key]:
                    self.assertEqual(after[key], [round(v * 2, 2) for v in before[key]])
            if before['base_px']:
                self.assertEqual(after['base_px'], [round(v * 2, 2) for v in before['base_px']])
            if before['points_px']:
                self.assertEqual(after['points_px'], [[round(v * 2, 2) for v in p] for p in before['points_px']])
        self.assertEqual(scene, fixtures.scene())                             # the input is not touched

    def test_a_code_fence_around_the_json_is_tolerated(self):
        self.assertEqual(reader.parse_json('```json\n{"a": 1}\n```'), {'a': 1})
        self.assertEqual(reader.parse_json(' {"a": 1} '), {'a': 1})
        for bad in ('[1]', 'sorry', ''):
            with self.assertRaises(ValueError):
                reader.parse_json(bad)


class ThePrompt(unittest.TestCase):
    """What the reading prompt asks for, and that the synthetic scene the other tests use is what it asks for."""

    @classmethod
    def setUpClass(cls):
        cls.text = reader.PROMPT.read_text(encoding='utf-8')

    def test_it_asks_for_spread_corners_all_used_one_assumption_and_scale_checks(self):
        text = self.text
        corners = text.split('## corner_points')[1].split('## scale_checks')[0]
        self.assertIn('(8 to 10)', text)
        self.assertIn('`fit` is required on every point; write `null`', corners)                # the key is there on every point, null by default
        self.assertIn('All of your points are used in the fit', corners)
        self.assertIn('leaves each corner out in turn', corners)                                # the model is told how its clicks are checked ...
        self.assertIn('spread the corners', corners)                                            # ... and what that asks of it
        self.assertIn('never click the same corner twice', corners)
        self.assertIn('at least 6', corners)                                                    # the floor the reader enforces
        self.assertIn('exactly ONE plausible value', text)                                      # one assumed dimension, never all of W, D and H
        self.assertIn('At most ONE dimension is ever assumed', text)
        self.assertIn('never W and D together', text)
        self.assertIn('Write "1.1" in `schema_version`', text)
        scale = text.split('## scale_checks')[1].split('## scopes')[0]
        for part in ('cafe table', 'counter', 'chair', '`tv`', 'assumed dimension'):
            self.assertIn(part, scale)

    def test_it_no_longer_asks_for_check_corners(self):
        for gone in ('exactly TWO', 'give 6 or 7', 'only one of them a check', 'independent checks', 'Write `true`'):
            self.assertNotIn(gone, self.text)

    def test_it_no_longer_promises_what_no_code_does(self):
        self.assertNotIn('you will be told', self.text.lower())
        self.assertNotIn('task message', self.text)                                             # nothing sends a "task message" about sizes or held-out points
        self.assertNotIn('assume plausible values', self.text)                                  # the old "assume W, D and H" instruction
        self.assertNotIn('put them in `known`', self.text)

    def test_the_schema_the_model_sees_does_not_contradict_it(self):
        """The schema's descriptions travel to the model with the schema (strict formats and the JSON-mode instructions alike)."""
        point = reader.provider_schema()['properties']['corner_points']
        seen = f"{point.get('description') or ''} {point['items']['properties']['fit'].get('description') or ''}"
        self.assertNotRegex(seen, r'(?i)mark (two|2)\b')                                        # the prompt asks for no check corners ...
        self.assertNotRegex(seen, r'(?i)\b(exactly|two|2) check')
        self.assertIn('used in the fit', seen)                                                   # ... and the flag says what null means

    def test_it_describes_the_grid_that_is_drawn(self):
        self.assertIn('all four edges', self.text)
        self.assertIn('`x,y`', self.text)

    def test_the_synthetic_scene_follows_it(self):
        scene = fixtures.scene()
        points = scene['corner_points']
        self.assertTrue(8 <= len(points) <= 10)
        self.assertTrue(all('fit' in p for p in points))
        self.assertTrue(all(p['fit'] is None for p in points))                                  # the key is there on every point, null: all points are fitted
        self.assertEqual(len({tuple(p['xyz']) for p in points}), len(points))                   # no corner is clicked twice
        stated = [k for k, v in scene['known'].items() if v is not None]
        self.assertEqual(stated, scene['assumed'])                                              # nothing is stated on this picture: the one value is assumed
        self.assertEqual(len(stated), 1)
        self.assertEqual(scene['schema_version'], '1.1')
        used = {t for p in points for t in p['xyz']}
        self.assertTrue({'F', 'H'} <= used and used & {'W/2', '-W/2'} and used & {'D/2', '-D/2'})   # the points fix every dimension
        self.assertEqual(engine.validate_scene(scene), [])


class ReadScene(unittest.TestCase):
    style = 'responses'

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.image = image_file(self.dir)

    def tearDown(self):
        self._tmp.cleanup()

    def run_with(self, script, **options):
        mock = MockOpenAI(fixtures.scene(), script).start()
        self.addCleanup(mock.stop)
        config = Config(mock.base_url, 'test-model', KEY, style=self.style, retry_wait=0, **options)
        return mock, config

    def read(self, config):
        return reader.read_scene(self.image, config, self.dir / 'reader')

    # -- the request
    def user_parts(self, body):
        if self.style != 'responses':
            return body['messages'][1]['content']
        return body['input'][0]['content']

    def test_a_good_reply_becomes_a_scene_and_the_request_is_right(self):
        mock, config = self.run_with(['good'], effort='low')
        result = self.read(config)
        self.assertEqual(result['attempts'], 1)
        self.assertEqual(result['usage'], {'input_tokens': 4321, 'output_tokens': 987})
        self.assertEqual(engine.validate_scene(result['scene']), [])
        self.assertEqual(result['scene']['image'], {'width': 960, 'height': 600})
        self.assertEqual(result['scene']['components'], fixtures.scene()['components'])
        (request,) = mock.requests
        self.assertEqual(request['headers']['authorization'], f'Bearer {KEY}')
        body = request['body']
        self.assertEqual(body['model'], 'test-model')
        parts = self.user_parts(body)
        self.assertEqual(len(parts), 3)
        self.assertIn('960 x 600', parts[0]['text'])
        images = [p['image_url'] if self.style == 'responses' else p['image_url']['url'] for p in parts[1:]]
        plain, marked = (decode(u) for u in images)
        self.assertEqual((plain.size, marked.size), ((960, 600), (960, 600)))
        self.assert_style_fields(request)

    def assert_style_fields(self, request):
        body = request['body']
        self.assertTrue(request['path'].endswith('/responses'))
        self.assertIs(body['store'], False)
        self.assertTrue(body['instructions'].startswith('You read ONE exhibition-booth render'))
        fmt = body['text']['format']
        self.assertEqual((fmt['type'], fmt['name'], fmt['strict']), ('json_schema', 'booth_scene', True))
        self.assertNotIn('$schema', fmt['schema'])
        self.assertNotIn('title', fmt['schema'])
        self.assertEqual(body['reasoning'], {'effort': 'low'})
        self.assertEqual(body['max_output_tokens'], 32000)
        for part in body['input'][0]['content'][1:]:
            self.assertEqual((part['type'], part['detail']), ('input_image', 'high'))

    # -- repairs and errors
    def test_a_bad_reply_is_sent_back_with_the_errors_and_the_second_try_counts(self):
        mock, config = self.run_with(['bad_scene', 'good'])
        result = self.read(config)
        self.assertEqual(result['attempts'], 2)
        self.assertEqual(result['usage']['input_tokens'], 2 * 4321)
        first, second = (r['body'] for r in mock.requests)
        self.assertEqual(len(self.turns(first)), 1)
        turns = self.turns(second)
        self.assertEqual([t['role'] for t in turns], ['user', 'assistant', 'user'])
        self.assertIn('nowhere', turns[1]['content'])                        # the model's own earlier answer
        self.assertIn('SCOPE', turns[2]['content'])                          # the validator's complaint, with the code
        self.assertIn('back_wall', turns[2]['content'])

    def turns(self, body):
        return body['messages'][1:] if self.style != 'responses' else body['input']

    def test_a_reply_without_the_check_flags_is_sent_back_even_though_the_compiler_would_accept_it(self):
        mock, config = self.run_with(['no_fit_keys', 'good'])
        result = self.read(config)
        self.assertEqual(result['attempts'], 2)
        complaint = self.turns(mock.requests[1]['body'])[2]['content']
        self.assertIn("'fit' is a required property", complaint)
        self.assertIn('corner_points.floor_bl.fit', complaint)
        self.assertTrue(all('fit' in p for p in result['scene']['corner_points']))
        old = fixtures.old_scene()
        self.assertEqual(engine.validate_scene(old), [])                                         # the compiler alone would have let such a file through
        self.assertIn('write null', complaint)                                                   # the repair asks for what the prompt asks for
        self.assertNotIn('independent check', complaint)

    def test_a_reading_with_too_few_corners_is_sent_back_with_a_plain_request(self):
        mock, config = self.run_with(['few_corners', 'good'])
        result = self.read(config)
        self.assertEqual(result['attempts'], 2)
        complaint = self.turns(mock.requests[1]['body'])[2]['content']
        self.assertIn('CORNERS corner_points: only 5 corner points were given', complaint)
        self.assertIn('at least 6 clearly visible corners', complaint)
        self.assertGreaterEqual(len(result['scene']['corner_points']), reader.MIN_CORNERS)

    def test_the_corner_rules_are_light_a_count_and_the_key_nothing_about_how_many_are_held_out(self):
        scene = fixtures.scene()
        self.assertEqual((reader.missing_fit_flags(scene), reader.too_few_corners(scene)), ([], []))
        for ids in ([], fixtures.HELD_OUT_IDS, [c[0] for c in fixtures.CORNERS[:6]]):
            held = fixtures.held_out_scene(ids)
            self.assertEqual((reader.missing_fit_flags(held), reader.too_few_corners(held)), ([], []), ids)     # any number of fit:false is not the reader's business
        six = copy.deepcopy(scene)
        six['corner_points'] = six['corner_points'][:reader.MIN_CORNERS]
        self.assertEqual(reader.too_few_corners(six), [])                                        # six is enough
        five = copy.deepcopy(scene)
        five['corner_points'] = five['corner_points'][:reader.MIN_CORNERS - 1]
        (problem,) = reader.too_few_corners(five)
        self.assertEqual((problem['code'], problem['where']), ('CORNERS', 'corner_points'))
        self.assertEqual(reader.too_few_corners({}), [])                                         # not a list: the schema check reports that

    def test_a_reading_that_holds_corners_out_is_not_sent_back(self):
        mock = MockOpenAI(fixtures.held_out_scene(), ['good']).start()
        self.addCleanup(mock.stop)
        config = Config(mock.base_url, 'test-model', KEY, style=self.style, retry_wait=0)
        result = self.read(config)
        self.assertEqual(result['attempts'], 1)
        self.assertEqual([p['id'] for p in result['scene']['corner_points'] if p['fit'] is False], fixtures.HELD_OUT_IDS)

    def test_text_that_is_not_json_is_repaired_the_same_way(self):
        mock, config = self.run_with(['not_json', 'good'])
        self.assertEqual(self.read(config)['attempts'], 2)
        self.assertIn('JSON', self.turns(mock.requests[1]['body'])[2]['content'])

    def test_it_gives_up_after_two_repairs(self):
        mock, config = self.run_with(['bad_scene'])
        with self.assertRaises(reader.ReaderError) as ctx:
            self.read(config)
        self.assertEqual(len(mock.requests), 3)
        self.assertEqual(len(ctx.exception.detail['attempts']), 3)
        self.assertTrue(any(e['code'] == 'SCOPE' for e in ctx.exception.detail['errors']))
        self.assertTrue((self.dir / 'reader' / 'response_3.json').is_file())

    def test_a_refusal_is_reported_and_not_retried(self):
        mock, config = self.run_with(['refusal'])
        with self.assertRaises(reader.ReaderError) as ctx:
            self.read(config)
        self.assertIn('I cannot help with that', str(ctx.exception))
        self.assertEqual(len(mock.requests), 1)

    def test_a_rejected_key_is_reported_and_not_retried(self):
        mock, config = self.run_with(['http401'])
        with self.assertRaises(reader.ReaderError) as ctx:
            self.read(config)
        self.assertIn('key', str(ctx.exception))
        self.assertIn('Incorrect API key', str(ctx.exception))
        self.assertEqual((len(mock.requests), ctx.exception.detail), (1, {'http': 401}))
        self.assertNotIn(KEY, str(ctx.exception))

    def test_a_server_error_is_retried_then_reported(self):
        mock, config = self.run_with(['http500', 'good'])
        self.assertEqual(self.read(config)['attempts'], 1)                   # the retry succeeded
        self.assertEqual(len(mock.requests), 2)
        mock, config = self.run_with(['http500'])
        with self.assertRaises(reader.ReaderError) as ctx:
            self.read(config)
        self.assertEqual((len(mock.requests), ctx.exception.detail), (3, {'http': 500}))

    def test_an_answer_that_was_cut_off_is_reported(self):
        _, config = self.run_with(['incomplete'])
        with self.assertRaises(reader.ReaderError) as ctx:
            self.read(config)
        self.assertIn('max_output_tokens', str(ctx.exception))

    def test_nothing_listening_is_a_clear_error(self):
        config = Config('http://127.0.0.1:9/v1', 'test-model', KEY, style=self.style, retry_wait=0)
        with self.assertRaises(reader.ReaderError) as ctx:
            self.read(config)
        self.assertIn('127.0.0.1:9', str(ctx.exception))

    # -- what stays on disk
    def test_the_log_has_neither_the_key_nor_the_pictures(self):
        _, config = self.run_with(['bad_scene', 'good'])
        self.read(config)
        files = sorted((self.dir / 'reader').iterdir())
        self.assertEqual([f.name for f in files], ['request.json', 'response_1.json', 'response_2.json'])
        for f in files:
            text = f.read_text(encoding='utf-8')
            self.assertNotIn(KEY, text, f.name)
            self.assertNotIn('base64', text, f.name)
        log = json.loads((self.dir / 'reader' / 'request.json').read_text(encoding='utf-8'))
        self.assertEqual((log['model'], log['style'], log['sent'], len(log['attempts'])), ('test-model', self.style, [960, 600], 2))
        self.assertIn('chars>', json.dumps(log['turns']))

    def test_a_large_picture_is_read_small_and_the_result_is_in_original_pixels(self):
        self.image = image_file(self.dir, (4096, 2560))
        mock, config = self.run_with(['good'])
        result = self.read(config)
        sent = decode(self.user_parts(mock.requests[0]['body'])[1]['image_url'] if self.style == 'responses'
                      else self.user_parts(mock.requests[0]['body'])[1]['image_url']['url'])
        self.assertEqual(sent.size, (2048, 1280))
        scene = result['scene']
        self.assertEqual(scene['image'], {'width': 4096, 'height': 2560})
        self.assertEqual(scene['corner_points'][0]['uv_px'], [round(v * 2, 2) for v in fixtures.scene()['corner_points'][0]['uv_px']])


class ReadSceneChat(ReadScene):
    """The Chat Completions wire format, for compatible services."""
    style = 'chat'

    def assert_style_fields(self, request):
        body = request['body']
        self.assertTrue(request['path'].endswith('/chat/completions'))
        self.assertEqual([m['role'] for m in body['messages']], ['system', 'user'])
        self.assertTrue(body['messages'][0]['content'].startswith('You read ONE exhibition-booth render'))
        fmt = body['response_format']
        self.assertEqual(fmt['type'], 'json_schema')
        self.assertEqual((fmt['json_schema']['name'], fmt['json_schema']['strict']), ('booth_scene', True))
        self.assertNotIn('$schema', fmt['json_schema']['schema'])
        self.assertEqual(body['reasoning_effort'], 'low')
        self.assertEqual(body['max_completion_tokens'], 32000)
        for part in body['messages'][1]['content'][1:]:
            self.assertEqual(part['type'], 'image_url')
            self.assertEqual(part['image_url']['detail'], 'high')


class ReadSceneChatJson(ReadScene):
    """Chat Completions for services that only have JSON mode (DeepSeek and the like): no json_schema, the schema goes into the instructions."""
    style = 'chat-json'

    def assert_style_fields(self, request):
        body = request['body']
        self.assertTrue(request['path'].endswith('/chat/completions'))
        self.assertEqual([m['role'] for m in body['messages']], ['system', 'user'])
        system = body['messages'][0]['content']
        self.assertTrue(system.startswith('You read ONE exhibition-booth render'))
        self.assertIn('## Output format (JSON)', system)
        schema_text = system.split('JSON Schema; every key is required, write null where a field does not apply:\n', 1)[1].strip()
        schema = json.loads(schema_text)                                                         # the whole schema, parseable, without the root $schema
        self.assertEqual(schema, reader.provider_schema())
        self.assertEqual(body['response_format'], {'type': 'json_object'})
        self.assertEqual(body['max_tokens'], 32000)
        self.assertNotIn('max_completion_tokens', body)
        self.assertEqual(body['reasoning_effort'], 'low')
        for part in body['messages'][1]['content'][1:]:
            self.assertEqual(part['type'], 'image_url')
            self.assertTrue(part['image_url']['url'].startswith('data:image/jpeg;base64,'))
        self.assertTrue(all(m['role'] == 'user' or isinstance(m['content'], str) for m in body['messages']))      # images only in user messages


class ThroughTheServer(unittest.TestCase):
    """Key and consent in the page -> the server reads -> the scene is stored -> it compiles. Nothing secret reaches a file."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.mock = MockOpenAI(fixtures.scene(), ['good']).start()
        self.config = Config(self.mock.base_url, 'test-model', None, retry_wait=0)
        self.server = create_server(Path(self._tmp.name) / 'jobs', self.config, port=0)
        self.api = Api(f'http://127.0.0.1:{self.server.server_address[1]}')
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.mock.stop()
        self._tmp.cleanup()

    def wait(self, job):
        end = time.time() + 120
        while time.time() < end:
            st = self.api.call('GET', f'/api/jobs/{job}')[1]
            if not any(s['state'] in ('running', 'queued') for s in st['steps'].values()):
                return st
            time.sleep(0.1)
        self.fail('timeout')

    def new_job(self):
        status, job = self.api.call('POST', '/api/jobs?name=booth.png', raw=png_bytes(), headers={'Content-Type': 'image/png'})
        self.assertEqual(status, 201)
        return job['id']

    def test_paste_a_key_read_and_compile(self):
        job = self.new_job()
        self.assertEqual(self.api.call('POST', f'/api/jobs/{job}/read', {'mode': 'openai', 'consent': True})[0], 422)       # no key yet
        self.assertEqual(self.mock.requests, [])
        self.assertEqual(self.api.call('POST', '/api/config', {'openai_api_key': KEY})[0], 200)
        self.assertEqual(self.api.call('POST', f'/api/jobs/{job}/read', {'mode': 'openai'})[0], 422)                          # no consent
        self.assertEqual(self.mock.requests, [])                                                                              # nothing was sent
        self.assertEqual(self.api.call('POST', f'/api/jobs/{job}/read', {'mode': 'openai', 'consent': True})[0], 202)
        st = self.wait(job)
        self.assertEqual(st['steps']['scene']['state'], 'done', st['steps']['scene'])
        detail = st['steps']['scene']['detail']
        self.assertEqual((detail['attempts'], detail['usage']['input_tokens']), (1, 4321))
        self.assertEqual(len(self.mock.requests), 1)
        self.assertEqual(self.mock.requests[0]['headers']['authorization'], f'Bearer {KEY}')
        self.assertEqual(self.api.call('POST', f'/api/jobs/{job}/compile', {})[0], 202)
        st = self.wait(job)
        self.assertEqual(st['steps']['compile']['state'], 'done', st['steps']['compile'])
        self.assertEqual(st['steps']['compile']['detail']['fit']['status'], 'fitted')
        detail = st['steps']['compile']['detail']
        self.assertEqual(detail['quality_gate'], {'status': 'pass', 'reasons': []})          # what the reading asks for (8 corners, all fitted, one assumption) passes
        self.assertEqual((detail['fit']['n_check'], detail['fit']['n_fit']), (0, 8))         # nothing is withheld from the fit
        self.assertEqual(detail['fit']['scale_checks'], [])                                  # the keys the page reads are all there
        scene = json.loads((Path(self._tmp.name) / 'jobs' / job / 'scene.json').read_text(encoding='utf-8'))
        self.assertEqual([p['fit'] for p in scene['corner_points']], [None] * 8)             # the stored scene keeps the key, null on every point
        for path in Path(self._tmp.name).rglob('*'):
            if path.is_file() and path.suffix in ('.json', '.txt', '.md', '.log'):
                self.assertNotIn(KEY, path.read_text(encoding='utf-8', errors='ignore'), str(path))
        self.assertNotIn(KEY, json.dumps(self.api.call('GET', '/api/health')[1]))
        self.assertNotIn(KEY, json.dumps(st))

    def test_a_reading_with_one_corner_far_off_compiles_and_says_review(self):
        self.mock.script[:] = ['review']
        self.api.call('POST', '/api/config', {'openai_api_key': KEY})
        job = self.new_job()
        self.assertEqual(self.api.call('POST', f'/api/jobs/{job}/read', {'mode': 'openai', 'consent': True})[0], 202)
        self.assertEqual(self.wait(job)['steps']['scene']['state'], 'done')
        self.assertEqual(self.api.call('POST', f'/api/jobs/{job}/compile', {})[0], 202)
        st = self.wait(job)
        self.assertEqual(st['steps']['compile']['state'], 'done', st['steps']['compile'])        # the server compiles it; the page decides about the render
        detail = st['steps']['compile']['detail']
        self.assertEqual(detail['quality_gate']['status'], 'review')
        self.assertIn(detail['fit']['status'], ('high_residual', 'check_failed'))             # the engine says which; it is not 'fitted'

    def test_a_failed_reading_is_an_error_on_the_card_and_keeps_the_job_usable(self):
        self.mock.script[:] = ['http401']
        self.api.call('POST', '/api/config', {'openai_api_key': KEY})
        job = self.new_job()
        self.assertEqual(self.api.call('POST', f'/api/jobs/{job}/read', {'mode': 'openai', 'consent': True})[0], 202)
        st = self.wait(job)
        self.assertEqual(st['steps']['scene']['state'], 'error')
        self.assertIn('key', st['steps']['scene']['message'])
        self.assertNotIn(KEY, json.dumps(st))
        self.assertEqual(self.api.call('PUT', f'/api/jobs/{job}/scene', fixtures.scene())[0], 200)       # the manual route still works
        self.assertEqual(self.api.call('POST', f'/api/jobs/{job}/compile', {})[0], 202)
        self.assertEqual(self.wait(job)['steps']['compile']['state'], 'done')

    def test_a_failed_reading_leaves_finished_steps_valid_and_a_good_one_marks_them_stale(self):
        job = self.new_job()
        self.assertEqual(self.api.call('PUT', f'/api/jobs/{job}/scene', fixtures.scene())[0], 200)
        self.assertEqual(self.api.call('POST', f'/api/jobs/{job}/compile', {})[0], 202)
        self.wait(job)
        jobs = self.server.studio.jobs
        for step in ('preview', 'build', 'glb'):
            jobs.set_step(job, step, 'done', message='fine')
        self.api.call('POST', '/api/config', {'openai_api_key': KEY})
        self.mock.script[:] = ['http401']
        self.assertEqual(self.api.call('POST', f'/api/jobs/{job}/read', {'mode': 'openai', 'consent': True})[0], 202)
        st = self.wait(job)
        self.assertEqual(st['steps']['scene']['state'], 'error')
        self.assertEqual([st['steps'][s]['state'] for s in ('compile', 'preview', 'build', 'glb')], ['done'] * 4)        # nothing changed, nothing stale
        self.mock.script[:] = ['good']
        self.assertEqual(self.api.call('POST', f'/api/jobs/{job}/read', {'mode': 'openai', 'consent': True})[0], 202)
        st = self.wait(job)
        self.assertEqual(st['steps']['scene']['state'], 'done')
        self.assertEqual([st['steps'][s]['state'] for s in ('compile', 'preview', 'build', 'glb')], ['stale'] * 4)      # a new scene: the rest is out of date

    def test_the_chat_style_works_through_the_server_too(self):
        self.config.style = 'chat'
        self.api.call('POST', '/api/config', {'openai_api_key': KEY})
        job = self.new_job()
        self.assertEqual(self.api.call('POST', f'/api/jobs/{job}/read', {'mode': 'openai', 'consent': True})[0], 202)
        self.assertEqual(self.wait(job)['steps']['scene']['state'], 'done')
        self.assertTrue(self.mock.requests[0]['path'].endswith('/chat/completions'))


if __name__ == '__main__':
    unittest.main()
