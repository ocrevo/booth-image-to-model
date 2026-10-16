"""Small checks that keep secrets and paths where they belong. Pure functions, no imports from the rest of the server."""
from __future__ import annotations
import re

KEY_CHARS = re.compile(r'^[\x21-\x7e]+$')                      # printable ASCII, no spaces: what fits in an HTTP header
SECRET_LIKE = re.compile(r'sk-[A-Za-z0-9_\-*]{6,}')           # a provider's own (masked) echo of a key is no better than the key


def clean_key(raw):
    """The key without surrounding whitespace, or None for no key. Anything that cannot be sent in a header is refused,
    without repeating the key in the message."""
    key = (raw or '').strip()
    if not key:
        return None
    if not KEY_CHARS.fullmatch(key):
        raise ValueError('The key contains spaces, line breaks or non-ASCII characters and cannot be sent as an HTTP header; check that it was copied completely (remove line breaks)')
    return key


def redact(text, key=None):
    """Text that may be stored or shown, with the key (and anything that looks like one) removed."""
    if key and key in text:
        text = text.replace(key, '***')
    return SECRET_LIKE.sub('sk-…', text)


def clean_relative(name):
    """A relative file name that cannot leave its folder, name a drive or a network share, or reach a hidden file; None otherwise.
    Checked before the path is resolved: resolving a \\\\host\\share name already touches the network on Windows."""
    if not name or '\\' in name or ':' in name or '\x00' in name or name.startswith('/'):
        return None
    parts = name.split('/')
    if any(p in ('', '.', '..') or p.startswith('.') or p != p.rstrip(' .') for p in parts):
        return None
    return '/'.join(parts)
