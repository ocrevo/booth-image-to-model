"""What a skill folder can do, and adapters so one set of cases runs against any version of it.

The harness never imports the skill. It reads the schema and the source text of the scripts it will call as subprocesses
and records what it found, so an older skill gets the same cases with the keys it does not know stripped, and a newer skill
gets the new keys. Standard library only.
"""
from __future__ import annotations
import copy
import hashlib
import json
import re
from pathlib import Path

OLD_SCHEMA_VERSION = 'scene-description.v1'
NEW_SCHEMA_VERSION = '1.1'


def _read(path):
    try:
        return Path(path).read_text(encoding='utf-8')
    except OSError:
        return ''


def detect(skill_dir):
    """Capabilities of the skill folder as a plain dict (recorded in every result file)."""
    root = Path(skill_dir)
    scripts = root / 'scripts'
    if not (scripts / 'box_fit.py').is_file() or not (scripts / 'boothctl.py').is_file():
        raise ValueError(f'{skill_dir} is not a skill folder: scripts/box_fit.py or scripts/boothctl.py is missing')
    schema_text = _read(root / 'schemas' / 'scene-description.v1.json')
    schema = json.loads(schema_text) if schema_text else {}
    props = schema.get('properties', {})
    point_props = props.get('corner_points', {}).get('items', {}).get('properties', {})
    versions = props.get('schema_version', {}).get('enum') or [OLD_SCHEMA_VERSION]
    component_keys = list(props.get('components', {}).get('items', {}).get('properties', {}))
    fit_src = _read(scripts / 'box_fit.py')
    compile_src = _read(scripts / 'draft_compile.py')
    return {
        'scene_fit_key': 'fit' in point_props,
        'scene_schema_versions': list(versions),
        'component_keys': component_keys,
        'box_fit_fit_flag': bool(re.search(r"""get\(\s*['"]fit['"]""", fit_src)) or 'n_check' in fit_src,
        'box_fit_leave_one_out': 'loo_errors_px' in fit_src,
        'box_fit_robust_loss': 'soft_l1' in fit_src,
        'box_fit_check_failed': 'check_failed' in fit_src,
        'compile_quality_gate': 'quality_gate' in compile_src,
    }


def fingerprint(skill_dir):
    """sha256 over the .py and .json files under scripts/ and schemas/ of a skill (the code and contracts the harness is about to run).

    Line endings are normalised, __pycache__ is skipped. References, templates, tests and the SKILL.md text are not part of it."""
    root = Path(skill_dir)
    h = hashlib.sha256()
    for sub in ('scripts', 'schemas'):
        for path in sorted((root / sub).rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix in ('.py', '.json'):
                h.update(path.relative_to(root).as_posix().encode('utf-8'))
                h.update(path.read_bytes().replace(b'\r\n', b'\n'))
    return h.hexdigest()


def skill_version(skill_dir):
    match = re.search(r'^\s*version:\s*"?([^"\s]+)"?\s*$', _read(Path(skill_dir) / 'SKILL.md'), re.M)
    return match.group(1) if match else None


def baseline_commit(skill_dir):
    """The commit a frozen copy was made from, when a COMMIT.txt sits next to the copy."""
    text = _read(Path(skill_dir).resolve().parent / 'COMMIT.txt').strip()
    match = re.search(r'\b([0-9a-f]{7,40})\b', text)
    return match.group(1) if match else None


def pick_schema_version(caps):
    versions = caps['scene_schema_versions']
    if caps['scene_fit_key']:
        for want in (NEW_SCHEMA_VERSION, '1.0', OLD_SCHEMA_VERSION):
            if want in versions:
                return want
    else:
        for want in (OLD_SCHEMA_VERSION, '1.0'):
            if want in versions:
                return want
    return versions[-1]


def adapt_scene(scene, caps, fit_flags=None):
    """Return a copy of a canonical (old-format) scene description in the form this skill accepts.

    fit_flags maps a corner id to False for a check corner. A skill that knows the `fit` key gets it on every corner
    (false, or null for 'fitted'); a skill that does not gets the key removed, so it fits every corner."""
    out = copy.deepcopy(scene)
    out['schema_version'] = pick_schema_version(caps)
    for c in out.get('components', []):                    # a newer schema may add keys that older scene files do not have
        for key in caps.get('component_keys', []):
            c.setdefault(key, None)
    for p in out.get('corner_points', []):
        p.pop('fit', None)
        if caps['scene_fit_key']:
            p['fit'] = False if (fit_flags or {}).get(p['id']) is False else None
    return out


def adapt_points(points_doc, caps, fit_flags=None):
    """The same for the points.json that box_fit.py reads: `fit: false` only where the script understands it."""
    out = copy.deepcopy(points_doc)
    for p in out.get('points', []):
        p.pop('fit', None)
        if caps['box_fit_fit_flag'] and (fit_flags or {}).get(p['id']) is False:
            p['fit'] = False
    return out
