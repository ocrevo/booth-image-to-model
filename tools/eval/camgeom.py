"""Pinhole camera maths for the evaluation harness; standard library only.

This is the harness's own copy of the convention the skill uses (scripts/camera_math.py and the camera made by
scripts/box_fit.py), so the ground truth never depends on the skill version under test:

* a camera is a dict with position, target, lens_mm, sensor_width_mm, principal_point (normalised, v down), roll_deg;
* it looks from `position` to `target`; the sensor fit is horizontal; pixels are measured from the top-left corner;
* the world is the booth frame: origin at the footprint centre, X right, Y back, Z up, metres.
"""
from __future__ import annotations
import math

SENSOR_MM = 36.0


def _sub(a, b):
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _unit(a):
    n = math.sqrt(_dot(a, a))
    if not math.isfinite(n) or n < 1e-12:
        raise ValueError('degenerate vector')
    return [a[0] / n, a[1] / n, a[2] / n]


def forward(yaw, pitch):
    """View direction for a yaw (0 looks along +Y, positive turns towards -X) and a pitch (positive looks down), radians."""
    return [-math.sin(yaw) * math.cos(pitch), math.cos(yaw) * math.cos(pitch), -math.sin(pitch)]


def make_camera(position, yaw, pitch, roll, focal_px, width, height, principal_px=None, distance=10.0, digits=6):
    """Camera dict for an image of width x height whose principal point is `principal_px` (default the centre).

    Everything is rounded to `digits` decimals first, so numbers derived from the dict later are consistent with the file."""
    fwd = forward(yaw, pitch)
    pos = [float(v) for v in position]
    target = [pos[i] + fwd[i] * distance for i in range(3)]
    cx, cy = principal_px if principal_px is not None else (width / 2.0, height / 2.0)
    return {'projection': 'perspective', 'position': [round(v, digits) for v in pos],
            'target': [round(v, digits) for v in target],
            'lens_mm': round(focal_px / width * SENSOR_MM, digits), 'sensor_width_mm': SENSOR_MM,
            'principal_point': [round(cx / width, digits), round(cy / height, digits)], 'pixel_aspect': [1, 1],
            'roll_deg': round(math.degrees(roll), digits)}


def axes(camera):
    """(forward, right, up) unit vectors in world space."""
    fwd = _unit(_sub(camera['target'], camera['position']))
    if abs(fwd[2]) > 1 - 1e-9:
        raise ValueError('camera looks straight up or down')
    right = _unit(_cross(fwd, [0.0, 0.0, 1.0]))
    up = _cross(right, fwd)
    r = math.radians(camera.get('roll_deg', 0.0) or 0.0)
    c, s = math.cos(r), math.sin(r)
    return fwd, [c * right[i] + s * up[i] for i in range(3)], [c * up[i] - s * right[i] for i in range(3)]


def focal_px(camera, width):
    return camera['lens_mm'] / camera.get('sensor_width_mm', SENSOR_MM) * width


def hfov_deg(camera):
    return math.degrees(2 * math.atan(camera.get('sensor_width_mm', SENSOR_MM) / (2 * camera['lens_mm'])))


def project(point, camera, width, height):
    """World point -> (u, v, depth). Raises ValueError for a point at or behind the camera plane."""
    fwd, right, up = axes(camera)
    d = _sub(point, camera['position'])
    z = _dot(d, fwd)
    if z <= 1e-9:
        raise ValueError('point is behind the camera')
    f = focal_px(camera, width)
    pp = camera.get('principal_point') or [0.5, 0.5]
    return pp[0] * width + f * _dot(d, right) / z, pp[1] * height - f * _dot(d, up) / z, z


def project_or_none(point, camera, width, height):
    try:
        return project(point, camera, width, height)
    except ValueError:
        return None


def unproject_to_z(u, v, camera, width, height, z):
    """Intersect the ray through pixel (u, v) with the plane Z = z. Raises ValueError if it does not hit in front."""
    fwd, right, up = axes(camera)
    f = focal_px(camera, width)
    pp = camera.get('principal_point') or [0.5, 0.5]
    a, b = u - pp[0] * width, -(v - pp[1] * height)
    d = _unit([f * fwd[i] + a * right[i] + b * up[i] for i in range(3)])
    if abs(d[2]) < 1e-12:
        raise ValueError('ray is parallel to the plane')
    t = (z - camera['position'][2]) / d[2]
    if t <= 0:
        raise ValueError('plane is behind the camera along this ray')
    return [camera['position'][i] + t * d[i] for i in range(3)]


def rotation_matrix(camera):
    """Rows are the camera's right, up and backward axes in world space (a world-to-camera rotation)."""
    fwd, right, up = axes(camera)
    return [right, up, [-v for v in fwd]]


def rotation_angle_deg(cam_a, cam_b):
    """Geodesic angle in degrees between the orientations of two cameras."""
    ra, rb = rotation_matrix(cam_a), rotation_matrix(cam_b)
    trace = sum(ra[i][k] * rb[i][k] for i in range(3) for k in range(3))   # trace(Ra^T Rb)
    return math.degrees(math.acos(max(-1.0, min(1.0, (trace - 1.0) / 2.0))))
