"""Pictures for the reading step: the image itself, and a copy with a labelled pixel grid (it helps a model read coordinates).

Both are JPEGs of the same size. Large images are scaled down (long edge <= max_edge) to keep the request small; the scale is
returned so coordinates read on the sent picture can be converted back to the original pixels.
"""
from __future__ import annotations
import base64
import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def _font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:                                  # Pillow older than 10.1 has a fixed-size default font
        return ImageFont.load_default()


def grid_step(longest):
    return 50 if longest <= 1000 else 100 if longest <= 2000 else 200


INTERIOR_EVERY = 3                    # an x,y label sits at every third line crossing, in both directions
YELLOW = (255, 255, 0, 255)
BLACK = (0, 0, 0, 255)


def _text(d, xy, text, font, anchor_right=False, anchor_bottom=False, limit=None):
    """Yellow text with a black outline at xy (top-left; right/bottom edges at xy on request). Skipped when it would leave the
    picture: a clipped number reads as a different number."""
    x0, y0, x1, y1 = d.textbbox((0, 0), text, font=font, stroke_width=2)
    x = xy[0] - x1 if anchor_right else xy[0]
    y = xy[1] - y1 if anchor_bottom else xy[1]
    if limit and (x + x0 < 0 or y + y0 < 0 or x + x1 > limit[0] or y + y1 > limit[1]):
        return False
    d.text((x, y), text, fill=YELLOW, font=font, stroke_width=2, stroke_fill=BLACK)
    return True


def draw_grid(image):
    """Thin cyan lines every `grid_step` px. Numbers (yellow, outlined) on all four edges, and an `x,y` label with a small tick at
    every third crossing inside the picture, so a coordinate can be read near the detail it belongs to."""
    out = image.convert('RGB').copy()
    w, h = out.size
    step = grid_step(max(w, h))
    layer = Image.new('RGBA', out.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    font = _font(max(11, step // 4))
    small = _font(max(10, step // 5))
    for x in range(0, w, step):
        d.line([(x, 0), (x, h)], fill=(0, 255, 255, 150 if x % (2 * step) == 0 else 90), width=1)
    for y in range(0, h, step):
        d.line([(0, y), (w, y)], fill=(0, 255, 255, 150 if y % (2 * step) == 0 else 90), width=1)
    for x in range(0, w, step):                                                  # x along the top and the bottom edge
        _text(d, (x + 2, 1), str(x), font, limit=(w, h))
        _text(d, (x + 2, h - 1), str(x), font, anchor_bottom=True, limit=(w, h))
    band = d.textbbox((0, 0), '0', font=font, stroke_width=2)[3] + 4             # the strip along the bottom edge that the x numbers take
    for y in range(step, h - band, step):                                         # y down the left and the right edge
        _text(d, (2, y + 1), str(y), font, limit=(w, h - band))
        _text(d, (w - 2, y + 1), str(y), font, anchor_right=True, limit=(w, h - band))
    spacing = INTERIOR_EVERY * step
    edge = d.textbbox((0, 0), str(h), font=font, stroke_width=2)[2] + 6          # the room the numbers on the right edge take
    for y in range(spacing, h - band, spacing):
        for x in range(spacing, w, spacing):
            label = f'{x},{y}'
            if x + 5 + d.textbbox((0, 0), label, font=small, stroke_width=2)[2] > w - edge:   # no room on the right: write it to the left
                drawn = _text(d, (x - 5, y + 3), label, small, anchor_right=True, limit=(w, h - band))
            else:
                drawn = _text(d, (x + 5, y + 3), label, small, limit=(w, h - band))
            if drawn:                                                            # the tick marks the exact crossing the label names
                d.line([(x - 4, y), (x + 4, y)], fill=YELLOW, width=1)
                d.line([(x, y - 4), (x, y + 4)], fill=YELLOW, width=1)
    return Image.alpha_composite(out.convert('RGBA'), layer).convert('RGB')


def _jpeg(image):
    buf = io.BytesIO()
    image.save(buf, 'JPEG', quality=92)
    return buf.getvalue()


def data_url(data, mime='image/jpeg'):
    return f'data:{mime};base64,{base64.b64encode(data).decode("ascii")}'


def prepare_images(path, max_edge=2048):
    """-> {'plain': bytes, 'grid': bytes, 'sent': (w, h), 'original': (w, h), 'scale': original / sent}"""
    with Image.open(Path(path)) as im:
        original = im.size
        rgb = im.convert('RGB')
    longest = max(original)
    if longest > max_edge:
        factor = max_edge / longest
        rgb = rgb.resize((max(1, round(original[0] * factor)), max(1, round(original[1] * factor))), Image.LANCZOS)
    sent = rgb.size
    return {'plain': _jpeg(rgb), 'grid': _jpeg(draw_grid(rgb)), 'sent': sent, 'original': original, 'scale': original[0] / sent[0]}
