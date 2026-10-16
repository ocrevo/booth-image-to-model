"""Automatic reading with OpenAI: one booth image -> a scene description (see references/scene-description.md).

Standard library only (urllib). Three request styles, chosen by Config.style:
  responses  the OpenAI Responses API (POST {base_url}/responses; input_text / input_image parts; text.format json_schema) - the default
  chat       Chat Completions (POST {base_url}/chat/completions; image_url parts; response_format json_schema; max_completion_tokens)
             for services that accept a strict schema
  chat-json  Chat Completions for services that only offer JSON mode (DeepSeek, many local servers): response_format json_object,
             max_tokens, and the schema written into the instructions; the local validator and the repair turns do the enforcing
Whatever comes back is checked locally with the same validate_scene the compiler uses; problems go back to the model for at most
two repair turns. The key is only ever used as a bearer header; nothing written to the job folder contains it or the images.
"""
from __future__ import annotations
import copy
import http.client
import json
import re
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path

import engine
import grid
from safety import redact

PROMPT = Path(__file__).resolve().parent / 'prompts' / 'read_scene.md'
SCHEMA_NAME = 'booth_scene'
MAX_REPAIRS = 2
RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_WAIT = 60                      # never make the page wait longer than this between two tries


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """An API endpoint that redirects is misconfigured; following it would send the key to wherever it points."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


class ReaderError(RuntimeError):
    def __init__(self, message, detail=None):
        super().__init__(message)
        self.detail = detail or {}


def provider_schema():
    """The scene-description schema as providers want it: without the root $schema/title keywords."""
    schema = copy.deepcopy(engine.scene_schema())
    for key in ('$schema', 'title'):
        schema.pop(key, None)
    return schema


# ------------------------------------------------------------------ pixels
def scale_scene_pixels(scene, factor, width, height):
    """Coordinates read on the sent picture -> the original picture. Returns a new scene."""
    out = copy.deepcopy(scene)

    def pair(v):
        return [round(v[0] * factor, 2), round(v[1] * factor, 2)] if isinstance(v, list) and len(v) == 2 else v

    def quad(v):
        return [round(x * factor, 2) for x in v] if isinstance(v, list) else v
    out['image'] = {'width': width, 'height': height}
    for p in out['corner_points']:
        p['uv_px'] = pair(p['uv_px'])
    for s in out['scale_checks']:
        s['base_px'], s['top_px'] = pair(s['base_px']), pair(s['top_px'])
    for c in out['components']:
        c['roi_px'], c['wall_rect_px'] = quad(c['roi_px']), quad(c['wall_rect_px'])
        c['base_px'], c['base_px_2'] = pair(c['base_px']), pair(c['base_px_2'])
        if c['points_px']:
            c['points_px'] = [pair(p) for p in c['points_px']]
    return out


def parse_json(text):
    """The model's reply as an object; tolerates a code fence around it."""
    body = text.strip()
    fenced = re.match(r'^```(?:json)?\s*(.*?)\s*```$', body, re.S)
    if fenced:
        body = fenced.group(1)
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError('the reply is JSON but not an object')
    return value


# ------------------------------------------------------------------ the two wire formats
def _retry_wait(config, attempt, headers=None):
    """Seconds to wait before the next try: the server's Retry-After when it sends one, else a short growing pause."""
    if not config.retry_wait:
        return 0
    try:
        asked = float((headers or {}).get('Retry-After') or 0)
    except (TypeError, ValueError):
        asked = 0
    return min(MAX_WAIT, max(asked, config.retry_wait * (attempt + 1)))


def _post(config, path, body, timeout):
    url = f'{config.base_url}{path}'
    payload = json.dumps(body).encode('utf-8')
    last = None
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, data=payload, method='POST',
                                             headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {config.api_key}'})
            with _OPENER.open(request, timeout=timeout) as response:
                return json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as ex:
            text = ex.read().decode('utf-8', 'replace')
            try:
                message = json.loads(text).get('error', {}).get('message') or text[:300]
            except (ValueError, AttributeError):
                message = text[:300]
            message = redact(str(message), config.api_key)
            if 300 <= ex.code < 400:
                raise ReaderError(f'{config.base_url} redirected the request (HTTP {ex.code}); stopped so the key is not sent elsewhere. Set OPENAI_BASE_URL to the final address', {'http': ex.code}) from ex
            if ex.code in (401, 403):
                raise ReaderError(f'OpenAI rejected this key (HTTP {ex.code}): {message}', {'http': ex.code}) from ex
            hint = ''
            if ex.code == 400 and re.search(r'max.{0,12}tokens|token.{0,12}limit', message, re.I):
                hint = ' (the output limit was rejected: lower it with OPENAI_MAX_OUTPUT_TOKENS or --openai-max-output-tokens)'
            last = ReaderError(f'OpenAI returned HTTP {ex.code}: {message}{hint}', {'http': ex.code})
            if ex.code not in RETRY_STATUS:
                raise last from ex
            wait = _retry_wait(config, attempt, ex.headers)
            if wait and attempt < 2:
                time.sleep(wait)
        except (socket.timeout, TimeoutError) as ex:
            raise ReaderError(f'Waited {timeout} s and {config.base_url} still has not answered; not retrying (a retry would be billed twice)', {'timeout': timeout}) from ex
        except (urllib.error.URLError, ConnectionError, http.client.HTTPException, json.JSONDecodeError) as ex:
            if isinstance(getattr(ex, 'reason', None), (socket.timeout, TimeoutError)):
                raise ReaderError(f'Waited {timeout} s and {config.base_url} still has not answered; not retrying (a retry would be billed twice)', {'timeout': timeout}) from ex
            last = ReaderError(redact(f'Cannot connect to {config.base_url}: {ex}', config.api_key))
            wait = _retry_wait(config, attempt)
            if wait and attempt < 2:
                time.sleep(wait)
        except (ValueError, UnicodeError) as ex:                  # e.g. a header value Python refuses; its text would contain the key
            raise ReaderError('The request could not be sent: the key or the address contains characters that cannot go into an HTTP header') from ex
    raise last


def _call_responses(config, instructions, turns, schema, timeout):
    body = {'model': config.model, 'instructions': instructions, 'input': turns, 'store': False,
            'text': {'format': {'type': 'json_schema', 'name': SCHEMA_NAME, 'schema': schema, 'strict': True}},
            'max_output_tokens': config.max_output_tokens}
    if config.effort:
        body['reasoning'] = {'effort': config.effort}
    data = _post(config, '/responses', body, timeout)
    if data.get('error'):
        raise ReaderError(f"OpenAI reported an error: {data['error'].get('message') or data['error']}", {'error': data['error']})
    text, refusal = '', ''
    for item in data.get('output') or []:
        if item.get('type') == 'message':
            for part in item.get('content') or []:
                if part.get('type') == 'output_text':
                    text += part.get('text', '')
                elif part.get('type') == 'refusal':
                    refusal += part.get('refusal', '')
    if refusal:
        raise ReaderError(f'The model refused this request: {refusal[:300]}', {'refusal': refusal})
    if data.get('status') == 'incomplete':
        reason = (data.get('incomplete_details') or {}).get('reason')
        raise ReaderError(f'The model did not finish its answer ({reason}); raise max_output_tokens or use another model', {'incomplete': reason})
    usage = data.get('usage') or {}
    return {'text': text, 'usage': {'input_tokens': usage.get('input_tokens', 0), 'output_tokens': usage.get('output_tokens', 0)}, 'raw': data}


def _chat_messages(instructions, turns):
    messages = [{'role': 'system', 'content': instructions}]
    for turn in turns:
        if isinstance(turn['content'], str):
            messages.append(turn)
            continue
        parts = []
        for part in turn['content']:
            if part['type'] == 'input_text':
                parts.append({'type': 'text', 'text': part['text']})
            else:
                parts.append({'type': 'image_url', 'image_url': {'url': part['image_url'], 'detail': part.get('detail', 'high')}})
        messages.append({'role': turn['role'], 'content': parts})
    return messages


def _schema_block(schema):
    return ('\n\n## Output format (JSON)\nReply with exactly one JSON object and nothing else (no code fence, no commentary). It must validate against '
            'this JSON Schema; every key is required, write null where a field does not apply:\n' + json.dumps(schema, separators=(',', ':')) + '\n')


def _call_chat(config, instructions, turns, schema, timeout):
    if config.style == 'chat-json':
        body = {'model': config.model, 'messages': _chat_messages(instructions + _schema_block(schema), turns), 'max_tokens': config.max_output_tokens,
                'response_format': {'type': 'json_object'}}
    else:
        body = {'model': config.model, 'messages': _chat_messages(instructions, turns), 'max_completion_tokens': config.max_output_tokens,
                'response_format': {'type': 'json_schema', 'json_schema': {'name': SCHEMA_NAME, 'strict': True, 'schema': schema}}}
    if config.effort:
        body['reasoning_effort'] = config.effort
    data = _post(config, '/chat/completions', body, timeout)
    choice = (data.get('choices') or [{}])[0]
    message = choice.get('message') or {}
    if message.get('refusal'):
        raise ReaderError(f"The model refused this request: {message['refusal'][:300]}", {'refusal': message['refusal']})
    if choice.get('finish_reason') == 'length':
        raise ReaderError('The model answer was cut off by the length limit; raise max_output_tokens or use another model', {'incomplete': 'length'})
    usage = data.get('usage') or {}
    return {'text': message.get('content') or '', 'usage': {'input_tokens': usage.get('prompt_tokens', 0), 'output_tokens': usage.get('completion_tokens', 0)}, 'raw': data}


# ------------------------------------------------------------------ the reading step
def _task_text(sent):
    return (f'The two pictures are {sent[0]} x {sent[1]} pixels. Describe this booth as a scene description. '
            f'Put {{"width": {sent[0]}, "height": {sent[1]}}} in `image` and read all pixel coordinates on the grid copy.')


def _redacted(turns):
    """What gets written to disk: the turns without the pictures' bytes."""
    out = []
    for turn in turns:
        if isinstance(turn['content'], str):
            out.append(turn)
        else:
            out.append({'role': turn['role'], 'content': [p if p['type'] == 'input_text' else {'type': p['type'], 'image_url': f"<{len(p['image_url'])} chars>"}
                                                          for p in turn['content']]})
    return out


def read_scene(image_path, config, work_dir, timeout=900):
    started = time.time()
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    images = grid.prepare_images(image_path)
    sent, original = images['sent'], images['original']
    schema = provider_schema()
    instructions = PROMPT.read_text(encoding='utf-8')
    call = _call_chat if config.style in ('chat', 'chat-json') else _call_responses
    turns = [{'role': 'user', 'content': [
        {'type': 'input_text', 'text': _task_text(sent)},
        {'type': 'input_image', 'image_url': grid.data_url(images['plain']), 'detail': 'high'},
        {'type': 'input_image', 'image_url': grid.data_url(images['grid']), 'detail': 'high'}]}]
    usage = {'input_tokens': 0, 'output_tokens': 0}
    log = []
    scene, errors = None, []
    for attempt in range(1 + MAX_REPAIRS):
        reply = call(config, instructions, turns, schema, timeout)
        usage = {k: usage[k] + reply['usage'][k] for k in usage}
        (work / f'response_{attempt + 1}.json').write_bytes(json.dumps(reply['raw'], ensure_ascii=False, indent=2).encode('utf-8'))
        scene, errors = None, []
        try:
            scene = parse_json(reply['text'])
        except (ValueError, json.JSONDecodeError) as ex:
            errors = [{'code': 'JSON', 'where': '(reply)', 'message': f'the reply is not a JSON object: {ex}'}]
        if scene is not None:
            scene['image'] = {'width': sent[0], 'height': sent[1]}                # the size is known; do not trust the model with it
            errors = engine.validate_scene(scene)
        log.append({'attempt': attempt + 1, 'errors': len(errors), 'usage': reply['usage']})
        if not errors:
            break
        turns = turns + [{'role': 'assistant', 'content': reply['text'] or '{}'},
                         {'role': 'user', 'content': 'The scene description has problems. Fix all of them and return the complete corrected JSON object, nothing else.\n'
                          + '\n'.join(f"- {e['code']} {e['where']}: {e['message']}" for e in errors[:25])}]
    (work / 'request.json').write_bytes(json.dumps({'style': config.style, 'model': config.model, 'base_url': config.base_url, 'instructions': instructions,
                                                    'turns': _redacted(turns), 'sent': sent, 'original': original, 'attempts': log}, ensure_ascii=False, indent=2).encode('utf-8'))
    if errors:
        raise ReaderError(f'Read {len(log)} times and the scene description still has {len(errors)} problems; the raw replies are in the reader/ folder of the job', {'errors': errors[:30], 'attempts': log})
    scene = scale_scene_pixels(scene, images['scale'], *original) if images['scale'] != 1 else {**scene, 'image': {'width': original[0], 'height': original[1]}}
    return {'scene': scene, 'usage': usage, 'attempts': len(log), 'seconds': round(time.time() - started, 1)}
