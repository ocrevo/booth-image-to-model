"""P1 tests for the code metrics: edge distance, colour difference and held-out points."""
from __future__ import annotations
import itertools
import math
import random
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0]))        # tools/poc, so that `score` is the package
sys.path.insert(0, str(HERE / 'fixtures'))

import numpy as np  # noqa: E402
import synthetic as fx  # noqa: E402
from score import metrics  # noqa: E402


def region(**over):
    """The fixed booth region of the synthetic fit (the picture's fit, not the candidate)."""
    return metrics.fit_region(fx.fit(**over), fx.W, fx.H)


class EdgeDistanceTests(unittest.TestCase):
    def test_identical_render_scores_zero_edge_distance(self):
        r = metrics.edge_distance(fx.photo(), fx.render(), region())
        self.assertLess(r['edge_distance_px'], 1.0)
        self.assertLess(r['edge_distance_diag'], 0.003)
        self.assertGreater(r['booth_pixel_fraction'], 0.3)

    def test_shift_increases_edge_distance_monotonically(self):
        values = [metrics.edge_distance(fx.photo(), fx.render(dx=dx), region())['edge_distance_px'] for dx in (0, 3, 6, 12, 24)]
        self.assertLess(values[0], 1.0, values)          # no shift: only the thin-edge offset is left
        for a, b in zip(values, values[1:]):
            self.assertLess(a, b, values)
        self.assertGreater(values[-1], 5 * max(values[0], 0.2))

    def test_edge_distance_counts_both_directions(self):
        base = metrics.edge_distance(fx.photo(), fx.render(), region())['edge_distance_px']
        render_has_more = metrics.edge_distance(fx.photo(), fx.render(extra=True), region())['edge_distance_px']
        photo_has_more = metrics.edge_distance(fx.photo(extra=True), fx.render(), region())['edge_distance_px']
        # an edge that only one image has must cost the same whichever image it is missing from
        self.assertGreater(render_has_more, base + 0.4)
        self.assertGreater(photo_has_more, base + 0.4)
        self.assertAlmostEqual(render_has_more, photo_has_more, delta=0.25 * max(render_has_more, photo_has_more))

    def test_shape_mismatch_raises(self):
        with self.assertRaises(ValueError) as ctx:
            metrics.edge_distance(fx.photo()[:100], fx.render(), region())
        self.assertIn('IMAGE_SHAPE_MISMATCH', str(ctx.exception))

    def test_empty_alpha_raises(self):
        with self.assertRaises(ValueError) as ctx:
            metrics.edge_distance(fx.photo(), fx.render(transparent=True), region())
        self.assertIn('RENDER_EMPTY', str(ctx.exception))

    def test_booth_mask_dilation_grows_the_mask(self):
        plain = metrics.booth_mask(fx.render())
        grown = metrics.booth_mask(fx.render(), dilate_diag=0.02)
        self.assertTrue(plain.dtype == bool and grown.dtype == bool)
        self.assertTrue(np.all(grown[plain]))
        self.assertGreater(grown.sum(), plain.sum())


def view_fit(dim_source=None, dims=None, floor_z=0.0, camera=None):
    """A fit for a 640 x 400 picture seen from the front-left: a 3 x 2 m box from the floor to z = 2."""
    cam = camera or {'position': [-6.0, -8.0, 3.0], 'target': [0.0, 0.0, 0.5], 'lens_mm': 36.0, 'sensor_width_mm': 36.0,
                     'projection': 'perspective', 'principal_point': [0.5, 0.5], 'roll_deg': 0}
    return {'dims_m': dims or {'W': 3.0, 'D': 2.0, 'H': 2.0}, 'dim_source': dim_source or {'W': 'fitted', 'D': 'fitted', 'H': 'fitted'},
            'floor_z': floor_z, 'image': {'width': 640, 'height': 400}, 'camera': cam}


def box_pixels(fit, width, height):
    d, z0 = fit['dims_m'], fit['floor_z']
    return [metrics.camera_math.project([sx * d['W'] / 2, sy * d['D'] / 2, z], fit['camera'], width, height)[:2]
            for sx in (-1, 1) for sy in (-1, 1) for z in (z0, d['H'])]


def signed_distance_to_hull(points, xs, ys):
    """Exact distance (float64) from each (x, y) to the convex hull of `points`: negative inside, positive outside."""
    from scipy.spatial import ConvexHull
    hull = points[ConvexHull(points).vertices]                               # counter-clockwise
    p = np.stack([xs, ys], axis=1)
    inside = np.ones(len(p), bool)
    dist = np.full(len(p), np.inf)
    depth = np.full(len(p), np.inf)
    for a, b in zip(hull, np.roll(hull, -1, axis=0)):
        e = b - a
        length = math.hypot(*e)
        cross = (e[0] * (p[:, 1] - a[1]) - e[1] * (p[:, 0] - a[0])) / length   # > 0: left of the edge, i.e. inside
        inside &= cross >= 0
        depth = np.minimum(depth, np.abs(cross))
        t = np.clip(((p - a) @ e) / (length * length), 0.0, 1.0)
        dist = np.minimum(dist, np.linalg.norm(p - (a + t[:, None] * e), axis=1))
    return np.where(inside, -depth, dist)


def reference_pixels(fit, width, height):
    """The projected points of the box clipped at the near plane, written edge by edge in float64 (the test's own version of the rule):
    for every box edge the end points in front of depth NEAR_M and the crossing of the plane; when H is not known (assumed, or missing)
    also the top-face points moved up to row 0."""
    cam, d, z0 = fit['camera'], fit['dims_m'], fit.get('floor_z', 0.0)
    top = d.get('H')
    fwd = np.array(metrics.camera_math.axes(cam)[0])
    eye = np.array(cam['position'], float)
    near = metrics.NEAR_M
    known = top is not None and (fit.get('dim_source') or {}).get('H') in ('fitted', 'given')

    def clip_edge(a, b):
        a, b = np.array(a, float), np.array(b, float)
        da, db = float((a - eye) @ fwd), float((b - eye) @ fwd)
        out = [q for q, dq in ((a, da), (b, db)) if dq >= near]
        if (da >= near) != (db >= near):
            out.append(a + (near - da) / (db - da) * (b - a))
        return out

    def px(q):
        return metrics.camera_math.project(list(q), cam, width, height)[:2]

    levels = [z0] if top is None else [z0, top]
    foot = [(sx * d['W'] / 2, sy * d['D'] / 2) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    edges = [([x, y, z], [x2, y2, z]) for z in levels for (x, y), (x2, y2) in zip(foot, foot[1:] + foot[:1])]
    if top is not None:
        edges += [([x, y, z0], [x, y, top]) for x, y in foot]
    pixels = [px(q) for a, b in edges for q in clip_edge(a, b)]
    if not known:
        z = top if top is not None else z0
        pixels += [(px(q)[0], 0.0) for (x, y), (x2, y2) in zip(foot, foot[1:] + foot[:1]) for q in clip_edge([x, y, z], [x2, y2, z])]
    return pixels


def random_close_fit(rng, assumed):
    """A box and a camera standing close to it, often with some of the box behind the camera plane."""
    w_m, d_m, h_m = rng.uniform(4, 14), rng.uniform(2, 5), rng.uniform(2, 4)
    pos = [rng.uniform(-w_m / 2 - 1, w_m / 2 + 1), rng.uniform(-d_m / 2 - 2, d_m / 2 + 2), rng.uniform(0.5, 3.0)]
    yaw, pitch = rng.uniform(0, 2 * math.pi), math.radians(rng.uniform(-30, 10))
    target = [pos[0] + math.cos(yaw) * math.cos(pitch), pos[1] + math.sin(yaw) * math.cos(pitch), pos[2] + math.sin(pitch)]
    cam = {'position': pos, 'target': target, 'lens_mm': rng.uniform(20, 40), 'sensor_width_mm': 36.0, 'projection': 'perspective',
           'principal_point': [0.5, 0.5], 'roll_deg': 0}
    src = 'assumed' if assumed else 'fitted'
    return {'dims_m': {'W': w_m, 'D': d_m, 'H': h_m}, 'dim_source': {'W': 'fitted', 'D': 'fitted', 'H': src}, 'floor_z': 0.0,
            'image': {'width': 1400, 'height': 933}, 'camera': cam}


class FitRegionTests(unittest.TestCase):
    def test_fit_region_is_the_projected_hull_of_the_fitted_box(self):
        fit = view_fit()
        self.assert_region_is_hull(metrics.fit_region(fit, 640, 400), box_pixels(fit, 640, 400), 640, 400)

    def assert_region_is_hull(self, mask, pixels, width, height, step=3, slack=1.5):
        """The mask is the convex hull of `pixels` (float64) grown by 2 % of the diagonal: every sampled pixel whose exact distance
        to the hull is within the radius minus the slack is in, every one beyond the radius plus the slack is out."""
        r = math.ceil(0.02 * math.hypot(width, height))
        self.assertEqual((mask.dtype, mask.shape), (np.dtype(bool), (height, width)))
        ys, xs = np.mgrid[0:height:step, 0:width:step]
        d = signed_distance_to_hull(np.asarray(pixels, np.float64), xs.ravel().astype(float), ys.ravel().astype(float))
        got = mask[ys.ravel(), xs.ravel()]
        must_in, must_out = d <= r - slack, d > r + slack
        bad_in, bad_out = must_in & ~got, must_out & got
        self.assertFalse(bad_in.any(), f'{int(bad_in.sum())} of {int(must_in.sum())} pixels inside the grown hull are missing, e.g. '
                                       f'{(int(xs.ravel()[bad_in][0]), int(ys.ravel()[bad_in][0]), float(d[bad_in][0]))}' if bad_in.any() else '')
        self.assertFalse(bad_out.any(), f'{int(bad_out.sum())} pixels beyond the grown hull are set, e.g. '
                                        f'{(int(xs.ravel()[bad_out][0]), int(ys.ravel()[bad_out][0]), float(d[bad_out][0]))}' if bad_out.any() else '')
        return int(must_in.sum()), int(must_out.sum())

    def test_fit_region_dilates_by_the_given_share_of_the_diagonal(self):
        fit = view_fit()
        plain = metrics.fit_region(fit, 640, 400, dilate_diag=0.0)
        grown = metrics.fit_region(fit, 640, 400)
        wide = metrics.fit_region(fit, 640, 400, dilate_diag=0.05)
        self.assertTrue(np.all(grown[plain]) and np.all(wide[grown]))
        self.assertLess(plain.sum(), grown.sum())
        self.assertLess(grown.sum(), wide.sum())

    def test_fit_region_reaches_the_top_of_the_picture_when_the_height_is_not_fitted(self):
        fitted = metrics.fit_region(fx.fit(), fx.W, fx.H)
        self.assertFalse(fitted[0, 160], 'a fitted height ends at the fitted top face (row 20 grown by 8)')
        for label, fit in (('assumed', fx.fit(dim_source={'W': 'fitted', 'D': 'fitted', 'H': 'assumed'}, dims_m={'W': 7.875, 'D': 2.0, 'H': 1.0})),
                           ('null', fx.fit(dims_m={'W': 7.875, 'D': 2.0, 'H': None}, dim_source={'W': 'fitted', 'D': 'fitted'})),
                           ('absent', fx.fit(dims_m={'W': 7.875, 'D': 2.0}, dim_source={'W': 'fitted', 'D': 'fitted'}))):
            mask = metrics.fit_region(fit, fx.W, fx.H)
            self.assertTrue(mask[0, 160] and mask[0, 40] and mask[0, 280], label)
            self.assertFalse(mask[0, 3], label + ': the extension stays within the box columns')
            self.assertTrue(mask[:180, 160].all(), label + ': the column is filled from the top down to the floor')
            self.assertGreater(mask.sum(), fitted.sum(), label)

    def test_a_box_partly_behind_the_camera_is_clipped_not_refused(self):
        # ruling 2: project only the part in front of a near plane. The camera stands at (3, -2, 1) looking along (-1, 1, 0), so the two
        # corners at x = 5, y = -1 are behind it and the other six are in front.
        cam = {'position': [3.0, -2.0, 1.0], 'target': [2.0, -1.0, 1.0], 'lens_mm': 20.0, 'sensor_width_mm': 36.0,
               'projection': 'perspective', 'principal_point': [0.5, 0.5], 'roll_deg': 0}
        fit = view_fit(dims={'W': 10.0, 'D': 2.0, 'H': 2.0}, camera=cam)
        fwd = np.array([-1.0, 1.0, 0.0]) / math.sqrt(2.0)
        eye = np.array(cam['position'])
        corners = [np.array([sx * 5.0, sy * 1.0, z]) for sx in (-1, 1) for sy in (-1, 1) for z in (0.0, 2.0)]
        depth = [float((c - eye) @ fwd) for c in corners]
        self.assertEqual(sum(d < 0 for d in depth), 2, depth)
        pixels = reference_pixels(fit, 640, 400)
        self.assertGreater(len(set(pixels)), 6)
        mask = metrics.fit_region(fit, 640, 400)
        self.assertTrue(mask.any())
        self.assert_region_is_hull(mask, pixels, 640, 400)
        # a box that is wholly in front is not clipped at all: the same region as before
        front = view_fit()
        self.assert_region_is_hull(metrics.fit_region(front, 640, 400), box_pixels(front, 640, 400), 640, 400)

    def test_the_region_is_the_hull_of_the_clipped_projection_for_random_close_cameras(self):
        # near-plane points project to several 1e5 px; the region must still be the exact hull of the projections (float64), grown
        rng = random.Random(20261023)
        big = nonempty = cases = 0
        while cases < 40:
            fit = random_close_fit(rng, assumed=cases % 2 == 1)
            try:
                pixels = np.asarray(reference_pixels(fit, 1400, 933), np.float64)
                if len(pixels) < 3 or np.ptp(pixels[:, 0]) < 1.0 or np.ptp(pixels[:, 1]) < 1.0:
                    continue
                mask = metrics.fit_region(fit, 1400, 933)
            except ValueError:
                continue                                          # nothing in front of the camera, or a camera looking straight up or down
            cases += 1
            big += int(np.abs(pixels).max() > 2e5)
            self.assert_region_is_hull(mask, pixels, 1400, 933, step=7)
            nonempty += int(mask.any())
        self.assertGreaterEqual(big, 3, 'the cases must include projections above 2e5 px')
        self.assertGreaterEqual(nonempty, 8)

    def test_near_plane_crossings_on_the_vertical_edges_are_projected(self):
        # the footprint loops at floor and top alone leave out the points where the four walls' edges cross the near plane
        fit = {'dims_m': {'W': 12.0, 'D': 3.0, 'H': 3.0}, 'dim_source': {'W': 'fitted', 'D': 'fitted', 'H': 'fitted'}, 'floor_z': 0.0,
               'image': {'width': 1400, 'height': 933},
               'camera': {'position': [4.53, -1.38, 2.33], 'target': [3.08, -1.39, -0.42], 'lens_mm': 28.0, 'sensor_width_mm': 36.0,
                          'projection': 'perspective', 'principal_point': [0.5, 0.5], 'roll_deg': 0}}
        pixels = np.asarray(reference_pixels(fit, 1400, 933), np.float64)
        inside, outside = self.assert_region_is_hull(metrics.fit_region(fit, 1400, 933), pixels, 1400, 933, step=5)
        self.assertGreater(inside, 1000)

    def test_the_upward_extension_starts_from_the_top_face(self):
        # H assumed: the top face (not the floor) is moved up to row 0; with this oblique camera the two give regions hundreds of px apart
        fit = view_fit(dim_source={'W': 'fitted', 'D': 'fitted', 'H': 'assumed'})
        mask = metrics.fit_region(fit, 640, 400)
        inside, outside = self.assert_region_is_hull(mask, reference_pixels(fit, 640, 400), 640, 400)
        self.assertGreater(inside, 1000)
        self.assertGreater(outside, 1000)
        no_h = view_fit(dims={'W': 3.0, 'D': 2.0, 'H': None}, dim_source={'W': 'fitted', 'D': 'fitted'})
        floor_based = metrics.fit_region(no_h, 640, 400)
        self.assertGreater(int((mask ^ floor_based).sum()), 1000, 'extending from the floor corners would be another region')
        self.assert_region_is_hull(floor_based, reference_pixels(no_h, 640, 400), 640, 400)

    def test_a_box_just_outside_the_picture_leaves_a_thin_band_inside_it(self):
        # the growth of the hull reaches into the picture from outside; the picture's own pixels are not all there is to the hull
        shift = 305.0 / fx.W                                          # the front face (u 20..300) moves to u -285..-5
        cam = dict(fx.camera(), principal_point=[0.5 - shift, 0.5])
        fit = fx.fit(camera_=cam)
        mask = metrics.fit_region(fit, fx.W, fx.H)
        self.assertTrue(mask.any(), 'a thin band, not an empty region')
        self.assertTrue(mask[100, 0] and mask[100, 2], 'the growth (8 px here) reaches 3 px into the picture')
        self.assertFalse(mask[100, 8])
        pixels = reference_pixels(fit, fx.W, fx.H)
        self.assertLess(max(p[0] for p in pixels), 0.0)
        self.assert_region_is_hull(mask, pixels, fx.W, fx.H, step=1)

    def test_fixed_steep_and_close_cameras_keep_the_exact_hull(self):
        # Fixed cases found by search: clamping each projected coordinate to +-1e5 px before the hull (which bends its edges) changes the
        # region by 134,633 px for the first fit (a camera inside the box, pitched up and rolled), by 25,487 px for the second
        # (a close camera across a wide box) and by 1,529 px for the third. The exact hull is compared with the test's float64 reference.
        def make(w_m, d_m, h_m, pos, target, lens, roll, source='fitted'):
            return {'dims_m': {'W': w_m, 'D': d_m, 'H': h_m}, 'dim_source': {'W': 'fitted', 'D': 'fitted', 'H': source}, 'floor_z': 0.0,
                    'image': {'width': 1400, 'height': 933},
                    'camera': {'position': pos, 'target': target, 'lens_mm': lens, 'sensor_width_mm': 36.0, 'projection': 'perspective',
                               'principal_point': [0.5, 0.5], 'roll_deg': roll}}
        cases = [make(9.9038, 3.306, 3.6503, [-3.8047, 1.5487, 2.4687], [-3.8158, 2.4314, 2.9386], 21.1285, -16.0748),
                 make(8.5634, 3.0557, 2.7921, [-0.303, -3.4071, 0.8184], [0.1856, -2.544, 0.6908], 37.4322, 0),
                 make(9.3848, 3.8705, 3.2249, [-0.4765, -3.7151, 1.074], [-0.0379, -2.8238, 0.9587], 37.2202, 0)]
        cases += [make(c['dims_m']['W'], c['dims_m']['D'], c['dims_m']['H'], c['camera']['position'], c['camera']['target'],
                       c['camera']['lens_mm'], c['camera']['roll_deg'], source='assumed') for c in cases]
        for k, fit in enumerate(cases):
            pixels = np.asarray(reference_pixels(fit, 1400, 933), np.float64)
            self.assertGreater(float(np.abs(pixels).max()), 1e5, k)           # the case really has projections beyond the clamp
            mask = metrics.fit_region(fit, 1400, 933)
            self.assertTrue(mask.any(), k)
            self.assert_region_is_hull(mask, pixels, 1400, 933, step=5)

    def test_a_missing_dim_source_or_an_unrecognised_source_counts_as_unknown(self):
        # 03 section 3.1 rule 3: only fitted and given make H known; anything else (also no dim_source at all) is unknown
        dims = {'W': 7.875, 'D': 2.0, 'H': 1.0}
        assumed = metrics.fit_region(fx.fit(dims_m=dims, dim_source={'W': 'fitted', 'D': 'fitted', 'H': 'assumed'}), fx.W, fx.H)
        known = metrics.fit_region(fx.fit(dims_m=dims, dim_source={'W': 'fitted', 'D': 'fitted', 'H': 'given'}), fx.W, fx.H)
        self.assertTrue(assumed[0, 160] and not known[0, 160])
        no_sources = fx.fit(dims_m=dims)
        del no_sources['dim_source']
        variants = {'no dim_source': no_sources,
                    'dim_source is not an object': fx.fit(dims_m=dims, dim_source='fitted'),
                    'no H in dim_source': fx.fit(dims_m=dims, dim_source={'W': 'fitted', 'D': 'fitted'}),
                    'measured': fx.fit(dims_m=dims, dim_source={'W': 'fitted', 'D': 'fitted', 'H': 'measured'}),
                    'null': fx.fit(dims_m=dims, dim_source={'W': 'fitted', 'D': 'fitted', 'H': None}),
                    'upper case': fx.fit(dims_m=dims, dim_source={'W': 'fitted', 'D': 'fitted', 'H': 'Fitted'})}
        for label, fit in variants.items():
            mask = metrics.fit_region(fit, fx.W, fx.H)
            self.assertTrue(np.array_equal(mask, assumed), label)
            self.assertTrue(mask[0, 160], label)

    def test_the_near_plane_depth_is_five_centimetres(self):
        self.assertEqual(metrics.NEAR_M, 0.05)                           # named in 03 section 3.1 rule 3; the clipping tests use it too

    def test_a_given_height_is_known_so_the_region_stops_at_the_top_face(self):
        # 03 section 3.1 rule 3 (ruling 1): H is known when dim_source says fitted or given; only assumed (or no H) extends upward
        def region_for(source):
            return metrics.fit_region(fx.fit(dim_source={'W': 'given', 'D': 'given', 'H': source}, dims_m={'W': 7.875, 'D': 2.0, 'H': 1.0}),
                                      fx.W, fx.H)
        given, fitted, assumed = region_for('given'), region_for('fitted'), region_for('assumed')
        self.assertTrue(np.array_equal(given, fitted))
        self.assertFalse(given[0, 160], 'the top face is at z = 1: row 64 minus the growth, not row 0')
        self.assertTrue(assumed[0, 160])
        self.assertLess(given.sum(), assumed.sum())

    def test_fit_region_does_not_depend_on_the_candidate(self):
        import copy
        import inspect
        self.assertEqual(list(inspect.signature(metrics.fit_region).parameters), ['fit', 'width', 'height', 'dilate_diag'])
        fit = fx.fit()
        before = copy.deepcopy(fit)
        a, b = metrics.fit_region(fit, fx.W, fx.H), metrics.fit_region(fit, fx.W, fx.H)
        self.assertEqual(fit, before, 'the fit is read, never changed')
        self.assertTrue(np.array_equal(a, b))
        full, cut = tower_scene(True), tower_scene(False)
        r_full = metrics.edge_distance(full[0], full[1], a)
        r_cut = metrics.edge_distance(full[0], cut[1], b)
        self.assertEqual(r_full['region_px'], r_cut['region_px'])
        self.assertEqual(r_full['region_px'], int(a.sum()))

    def test_deleting_a_part_on_the_outline_does_not_improve_the_edge_distance(self):
        photo, full = tower_scene(True)
        _, cut = tower_scene(False)
        region_ = metrics.fit_region(fx.fit(), fx.W, fx.H)
        whole = metrics.edge_distance(photo, full, region_)['edge_distance_px']
        without = metrics.edge_distance(photo, cut, region_)['edge_distance_px']
        self.assertLess(whole, 1.0)
        self.assertGreater(without, whole + 5.0, (whole, without))
        # the old rule (the candidate's own grown alpha) moves the missing tower's edges out of the comparison: the bug this fixes
        old = metrics.edge_distance(photo, cut, metrics.booth_mask(cut, 0.02))['edge_distance_px']
        self.assertLess(old, without)

    def test_edge_distance_reports_the_region_and_what_the_render_has_outside_it(self):
        region_ = metrics.fit_region(fx.fit(), fx.W, fx.H)
        inside = metrics.edge_distance(fx.photo(), fx.render(), region_)
        self.assertEqual(inside['region_px'], int(region_.sum()))
        self.assertEqual(inside['render_outside_region'], 0.0)
        moved = metrics.edge_distance(fx.photo(), fx.render(dx=40), region_)
        alpha = metrics.booth_mask(fx.render(dx=40))
        self.assertAlmostEqual(moved['render_outside_region'], float((alpha & ~region_).sum() / alpha.sum()), places=9)
        self.assertGreater(moved['render_outside_region'], 0.03)

    def test_edge_distance_refuses_a_region_that_does_not_fit_the_picture_or_is_empty(self):
        with self.assertRaises(ValueError) as ctx:
            metrics.edge_distance(fx.photo(), fx.render(), np.ones((10, 10), bool))
        self.assertIn('REGION_SHAPE_MISMATCH', str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            metrics.edge_distance(fx.photo(), fx.render(), np.zeros((fx.H, fx.W), bool))
        self.assertIn('REGION_EMPTY', str(ctx.exception))

    def test_fit_region_refuses_a_fit_it_cannot_use_and_survives_degenerate_ones(self):
        def bad(fit, word='BAD_FIT'):
            with self.assertRaises(ValueError) as ctx:
                metrics.fit_region(fit, 640, 400)
            self.assertIn(word, str(ctx.exception))
        no_dims = view_fit()
        del no_dims['dims_m']
        bad(no_dims)
        bad(view_fit(dims={'W': 3.0, 'H': 2.0}))                                  # no depth
        bad(view_fit(dims={'W': 0.0, 'D': 2.0, 'H': 2.0}))
        bad(view_fit(dims={'W': 3.0, 'D': 2.0, 'H': -1.0}))                       # top below the floor
        no_camera = view_fit()
        del no_camera['camera']
        bad(no_camera)
        behind = dict(view_fit()['camera'], position=[0.0, 5.0, 1.0], target=[0.0, 10.0, 1.0])
        bad(view_fit(camera=behind))                                              # the whole box is behind the camera
        bad(view_fit(dim_source={'W': 'fitted', 'D': 'fitted', 'H': 'assumed'}, camera=behind))
        for width, height in ((0, 400), (640, 0)):
            with self.assertRaises(ValueError):
                metrics.fit_region(view_fit(), width, height)
        # every corner on one point, and on one line: a region is still returned, with nothing crashing
        tiny = view_fit(dims={'W': 1e-9, 'D': 1e-9, 'H': 1e-9}, floor_z=0.0)
        point = metrics.fit_region(tiny, 640, 400)
        self.assertTrue(point.any())
        line = metrics.fit_region(view_fit(dims={'W': 1e-9, 'D': 1e-9, 'H': 2.0}), 640, 400)
        self.assertTrue(line.any())
        self.assertLess(line.sum(), 640 * 400 // 2)


def tower_scene(tower):
    """(picture, render) of a low block with a tall tower on its right, on the synthetic canvas; with tower=False the render
    has the tower deleted (alpha 0 there) while the picture keeps it. Flat colours: every edge is sharp."""
    def draw(with_tower):
        img = np.zeros((fx.H, fx.W, 3), np.uint8)
        img[:] = fx.BG
        alpha = np.zeros((fx.H, fx.W), np.uint8)
        img[60:180, 20:200], alpha[60:180, 20:200] = fx.FLOOR, 255
        if with_tower:
            img[20:180, 200:300], alpha[20:180, 200:300] = (120, 120, 200), 255
        return img, alpha
    photo = draw(True)[0]
    img, alpha = draw(tower)
    return photo, np.dstack([img, alpha])


class ColourTests(unittest.TestCase):
    def test_colour_de_detects_swatch_change(self):
        sw = fx.swatches()['swatches']
        same = metrics.colour_de(fx.photo(), fx.render(), sw)
        self.assertLess(same['colour_de_mean'], 1.0)
        self.assertAlmostEqual(same['points'], 10.0)
        changed = metrics.colour_de(fx.photo(), fx.render(recolour={'a': (200, 40, 160)}), sw)
        self.assertGreater(changed['colour_de_mean'], 5.0)
        by_id = {s['id']: s for s in changed['swatches']}
        self.assertGreater(by_id['s_a']['de'], 20.0)
        self.assertLess(by_id['s_b']['de'], 1.0)
        self.assertLess(changed['points'], same['points'])

    def test_swatch_outside_alpha_counts_forty(self):
        sw = [{'id': 's_out', 'bbox_px': [2, 2, 12, 12], 'role': 'accent'}]
        r = metrics.colour_de(fx.photo(), fx.render(), sw)
        self.assertEqual(r['swatches'][0]['de'], 40.0)
        self.assertEqual(r['points'], 0.0)

    def test_points_scale_is_linear_between_10_and_40(self):
        self.assertEqual(metrics.colour_points(5), 10.0)
        self.assertEqual(metrics.colour_points(40), 0.0)
        self.assertAlmostEqual(metrics.colour_points(25), 5.0)


class HoldoutTests(unittest.TestCase):
    def test_holdout_uses_project_and_reports_missing_part_as_zero(self):
        # Camera (0,-10,0) looking along +Y with f = 320 px: u = 160 + 320 x / d, v = 100 - 320 z / d, d = y + 10.
        # reception box is 2 x 1 x 2 m at the origin, so its top-front-right corner is (1, -0.5, 1) at depth 9.5.
        u, v = 160 + 320 * 1 / 9.5, 100 - 320 * 1 / 9.5
        points = [
            {'id': 'h1', 'part_id': 'reception', 'corner': 'top_front_right', 'uv_px': [u, v], 'note': ''},
            {'id': 'h2', 'part_id': 'reception', 'corner': 'top_front_right', 'uv_px': [u + 3.2, v], 'note': ''},
            {'id': 'h3', 'part_id': 'ghost', 'corner': 'top_left', 'uv_px': [100, 100], 'note': ''},
            {'id': 'h4', 'part_id': 'screen_a', 'corner': 'top_back_left', 'uv_px': [10, 10], 'note': ''},
        ]
        rows = {r['id']: r for r in metrics.holdout_errors(fx.spec(), points, fx.W, fx.H)}
        diag = (fx.W ** 2 + fx.H ** 2) ** 0.5
        self.assertAlmostEqual(rows['h1']['error_px'], 0.0, places=3)
        self.assertFalse(rows['h1']['missing'])
        self.assertAlmostEqual(rows['h2']['error_px'], 3.2, places=3)
        self.assertAlmostEqual(rows['h2']['error_diag'], 3.2 / diag, places=6)
        # a missing part is never skipped: it is reported, flagged and scores nothing
        self.assertTrue(rows['h3']['missing'])
        self.assertGreater(rows['h3']['error_diag'], 0.04)
        self.assertEqual(metrics.holdout_points(rows['h3']['error_diag']), 0.0)
        self.assertEqual(len(rows), 4)

    def screen_spec(self):
        spec = fx.spec()
        spec['components'] = [
            fx.component('screen_panel', (1, 0.1, 1), (-2, 0, 1), group='screen'),
            fx.component('screen_row_tile_1', (1, 0.1, 1), (1, 0, 1), group='screen_row'),
            fx.component('screen_row_tile_2', (1, 0.1, 1), (3, 0, 1), group='screen_row'),
        ]
        return spec

    def test_part_does_not_swallow_the_tiles_of_a_longer_named_group(self):
        # screen_panel spans x -2.5..-1.5, y -0.05..0.05 and z 0.5..1.5: top_left is the footprint corner that projects furthest
        # left, (-2.5, -0.05, 1.5) (the nearer one is more to the left), at depth 9.95 -> u = 160 - 320 * 2.5 / 9.95.
        pt = [{'id': 'p', 'part_id': 'screen', 'corner': 'top_left', 'uv_px': [160 - 320 * 2.5 / 9.95, 100 - 320 * 1.5 / 9.95], 'note': ''}]
        row = metrics.holdout_errors(self.screen_spec(), pt, fx.W, fx.H)[0]
        self.assertEqual(row['matched_ids'], ['screen_panel'])
        self.assertAlmostEqual(row['error_px'], 0.0, places=3)

    def test_no_guessing_by_name_prefix(self):
        spec = fx.spec()
        spec['components'] = [fx.component('counter_stool', (1, 0.1, 1), (-2, 0, 1)),
                              fx.component('counter_top', (1, 0.1, 1), (2, 0, 1), group='counter_group')]
        pt = [{'id': 'p', 'part_id': 'counter', 'corner': 'top_left', 'uv_px': [0, 0], 'note': ''}]
        row = metrics.holdout_errors(spec, pt, fx.W, fx.H)[0]
        self.assertEqual(row['matched_ids'], [])
        self.assertTrue(row['missing'])
        self.assertEqual(row['missing_reason'], 'part_missing')
        # with a map, only the listed names count; a part the map does not list is still matched exactly, never by prefix
        row = metrics.holdout_errors(spec, pt, fx.W, fx.H, {'other': ['counter_stool']})[0]
        self.assertTrue(row['missing'])
        row = metrics.holdout_errors(spec, pt, fx.W, fx.H, {'counter': ['counter_stool']})[0]
        self.assertEqual(row['matched_ids'], ['counter_stool'])

    def test_half_of_the_points_missing_scores_nothing(self):
        u, v = 160 + 320 / 9.5, 100 - 320 / 9.5
        pts = [{'id': f'h{i}', 'part_id': 'reception' if i < 2 else 'ghost', 'corner': 'top_front_right', 'uv_px': [u, v], 'note': ''}
               for i in range(4)]
        rows = metrics.holdout_errors(fx.spec(), pts, fx.W, fx.H)
        self.assertEqual(sum(r['missing'] for r in rows), 2)
        self.assertEqual(metrics.holdout_points(metrics.holdout_median(rows)), 0.0)

    def test_mapping_names_ids_or_groups_exactly(self):
        pt = [{'id': 'p', 'part_id': 'wall_of_screens', 'corner': 'top_right', 'uv_px': [0, 0], 'note': ''}]
        row = metrics.holdout_errors(self.screen_spec(), pt, fx.W, fx.H, {'wall_of_screens': ['screen_row']})[0]
        self.assertEqual(row['matched_ids'], ['screen_row_tile_1', 'screen_row_tile_2'])
        row = metrics.holdout_errors(self.screen_spec(), pt, fx.W, fx.H, {'wall_of_screens': 'screen_panel'})[0]
        self.assertEqual(row['matched_ids'], ['screen_panel'])
        row = metrics.holdout_errors(self.screen_spec(), pt, fx.W, fx.H, {'wall_of_screens': ['nothing']})[0]
        self.assertTrue(row['missing'])
        self.assertEqual(row['missing_reason'], 'mapped_name_absent')

    def test_corner_names(self):
        self.assertEqual(metrics.corner_words('top_front_left'), {'top', 'front', 'left'})
        self.assertEqual(metrics.corner_words('top_left'), {'top', 'left'})
        for bad in ('near_end_first', 'top_bottom', 'left_left', '', None, ['top'], 'Top_Front_Left', 'top-front-left', ' top'):
            self.assertIsNone(metrics.corner_words(bad), bad)

    def test_holdout_score_uses_the_median_not_the_mean(self):
        rows = [{'error_diag': e} for e in (0.001, 0.002, 0.003, 0.5)]
        self.assertAlmostEqual(metrics.holdout_median(rows), 0.0025)
        self.assertEqual(metrics.holdout_points(metrics.holdout_median(rows)), 10.0)

    def test_unknown_corner_is_reported_not_guessed(self):
        pts = [{'id': 'p', 'part_id': 'reception', 'corner': 'near_end_first', 'uv_px': [0, 0], 'note': ''}]
        row = metrics.holdout_errors(fx.spec(), pts, fx.W, fx.H)[0]
        self.assertTrue(row['missing'])
        self.assertEqual(row['missing_reason'], 'unknown_corner')

    def test_points_thresholds(self):
        self.assertEqual(metrics.holdout_points(0.010), 10.0)
        self.assertEqual(metrics.holdout_points(0.015), 7.0)
        self.assertEqual(metrics.holdout_points(0.030), 3.0)
        self.assertEqual(metrics.holdout_points(0.041), 0.0)


def orbit_camera(yaw_deg, height=3.0, radius=10.0):
    """Camera on a circle around the origin, looking at (0, 0, 1). yaw 0 stands at -Y looking along +Y; yaw 180 stands at +Y."""
    a = math.radians(yaw_deg)
    return {'position': [radius * math.sin(a), -radius * math.cos(a), height], 'target': [0.0, 0.0, 1.0], 'lens_mm': 36.0,
            'sensor_width_mm': 36.0, 'projection': 'perspective', 'principal_point': [0.5, 0.5], 'roll_deg': 0}


BOX = ([-1.0, -1.0, 0.0], [1.0, 1.0, 2.0])          # 2 x 2 x 2 m, centred on x = y = 0


def old_corner(bounds, name):
    """The rule before 2026-10-23: the box's own axes, X right, Y back, Z up; not named is the middle."""
    words, (lo, hi) = set(name.split('_')), bounds
    mid = [(lo[i] + hi[i]) / 2 for i in range(3)]
    return [lo[0] if 'left' in words else hi[0] if 'right' in words else mid[0],
            lo[1] if 'front' in words else hi[1] if 'back' in words else mid[1],
            lo[2] if 'bottom' in words else hi[2] if 'top' in words else mid[2]]


class CornerSeenFromTheCameraTests(unittest.TestCase):
    def view(self, point, camera):
        """(right offset, depth offset) of a world point relative to the box centre, from the camera's own axes."""
        fwd, right, _ = metrics.camera_math.axes(camera)
        c = [0.0, 0.0, 1.0]
        d = [point[i] - c[i] for i in range(3)]
        return sum(d[i] * right[i] for i in range(3)), sum(d[i] * fwd[i] for i in range(3))

    def test_top_front_left_of_a_tilted_box_is_left_near_and_up(self):
        for yaw in (40.0, 50.0, -40.0):
            cam = orbit_camera(yaw)
            p = metrics.corner_point(BOX, 'top_front_left', cam)
            self.assertEqual(p[2], 2.0, yaw)                                  # top is the world's up
            right, depth = self.view(p, cam)
            self.assertLess(right, 0, yaw)                                    # left in the picture
            self.assertLess(depth, 0, yaw)                                    # nearer to the camera than the box centre
            # of the four top corners it is the one with the lowest (right + depth): left and near together
            tops = [q for q in metrics.box_corners(BOX) if q[2] == 2.0]
            self.assertEqual(min(tops, key=lambda q: sum(self.view(q, cam))), p, yaw)

    def test_a_tilted_box_gives_four_different_top_corners_for_the_four_names(self):
        cam = orbit_camera(40.0)
        names = ['top_front_left', 'top_front_right', 'top_back_left', 'top_back_right']
        points = [tuple(metrics.corner_point(BOX, n, cam)) for n in names]
        self.assertEqual(len(set(points)), 4)
        self.assertEqual(points[0], (-1.0, -1.0, 2.0))                        # camera at +X, -Y: the near left corner
        self.assertEqual(points[1], (1.0, -1.0, 2.0))
        self.assertEqual(points[2], (-1.0, 1.0, 2.0))
        self.assertEqual(points[3], (1.0, 1.0, 2.0))

    def test_turning_the_camera_half_way_round_gives_the_opposite_corner(self):
        for name in ('top_front_left', 'bottom_back_right', 'top_back_left'):
            a = metrics.corner_point(BOX, name, orbit_camera(0.0))
            b = metrics.corner_point(BOX, name, orbit_camera(180.0))
            self.assertEqual([b[0], b[1], b[2]], [-a[0], -a[1], a[2]], name)  # x and y swap sides, z stays

    def test_a_box_facing_the_camera_keeps_the_old_rule_for_every_name(self):
        names = ['_'.join(w) for w in itertools.product(('top', 'bottom'), ('front', 'back'), ('left', 'right'))] + ['left_top_back']
        bounds = ([-1.5, -0.4, 0.2], [2.5, 0.6, 1.7])                         # an uneven, thin box
        for height in (1.0, 3.0):                                             # level and looking down: the old rule still holds
            cam = orbit_camera(0.0, height=height)
            for name in names:
                got, want = metrics.corner_point(bounds, name, cam), old_corner(bounds, name)
                for g, w in zip(got, want):
                    self.assertAlmostEqual(g, w, places=9, msg=(name, height))

    def test_corner_name_is_the_inverse_of_corner_point(self):
        for yaw in (0.0, 40.0, 130.0, 180.0, 250.0):
            for height in (1.0, 3.0):                                         # level and looking down: every corner gets its one name
                cam = orbit_camera(yaw, height=height)
                for corner in metrics.box_corners(BOX):
                    name = metrics.corner_name(corner, BOX, cam)
                    self.assertIsNotNone(name, (yaw, height, corner))
                    self.assertEqual(len(name.split('_')), 3)
                    self.assertEqual(metrics.corner_point(BOX, name, cam), list(corner), (yaw, height, corner, name))

    def test_corner_name_round_trips_on_the_reported_views(self):
        views = {
            'steep view of a 2 x 1 x 2 box': ({'position': [0.0, -6.0, 6.0], 'target': [0.0, 0.0, 0.5]},
                                              ([-1.0, -0.5, 0.0], [1.0, 0.5, 2.0])),
            'counter box, yaw 10, 1.6 m up': (orbit_camera(10.0, height=1.6), ([-2.5, -0.4, 0.0], [2.5, 0.4, 1.1])),
            'cube, yaw 40': (orbit_camera(40.0), BOX),
        }
        for label, (cam, bounds) in views.items():
            cam = dict(orbit_camera(0.0), **cam)
            for corner in metrics.box_corners(bounds):
                name = metrics.corner_name(corner, bounds, cam)
                self.assertIsNotNone(name, (label, corner))
                self.assertEqual(metrics.corner_point(bounds, name, cam), list(corner), (label, corner, name))
        self.assertEqual(metrics.corner_name([-1.0, -1.0, 0.0], BOX, orbit_camera(40.0)), 'bottom_front_left')

    def test_corner_name_is_none_only_when_no_name_or_two_names_reach_the_corner(self):
        flat = ([-1.0, -1.0, 0.5], [1.0, 1.0, 0.5])                           # no height: top_* and bottom_* reach the same corner
        self.assertIsNone(metrics.corner_name([-1.0, -1.0, 0.5], flat, orbit_camera(0.0)))
        thin = ([-2.5, -0.01, 0.0], [2.5, 0.01, 1.0])                         # seen end-on, some corners are reached by no name
        cam = orbit_camera(0.0)
        reached = {tuple(metrics.corner_point(thin, n, cam)) for n in
                   ('_'.join(w) for w in itertools.product(('top', 'bottom'), ('front', 'back'), ('left', 'right')))}
        for corner in metrics.box_corners(thin):
            self.assertEqual(metrics.corner_name(corner, thin, cam) is not None, tuple(corner) in reached)
        self.assertIsNone(metrics.corner_name([0.0, 0.0, 1.0], BOX, orbit_camera(40.0)))      # not a corner at all

    def test_a_steep_rolled_camera_never_flips_top_and_bottom(self):
        cam = dict(orbit_camera(0.0), position=[-0.52, 2.95, 12.0], target=[0.0, 0.0, 1.0], roll_deg=8.0)
        for word_z, want in (('top', 2.0), ('bottom', 0.0)):
            for rest in ('front_left', 'front_right', 'back_left', 'back_right', 'left', 'right', 'front', 'back'):
                name = f'{word_z}_{rest}'
                self.assertEqual(metrics.corner_point(BOX, name, cam, fx.W, fx.H)[2], want, name)

    def test_a_high_45_degree_view_still_tells_the_four_top_names_apart(self):
        names = ['top_front_left', 'top_front_right', 'top_back_left', 'top_back_right']
        for height in (3.0, 9.0, 30.0):
            cam = orbit_camera(40.0, height=height)
            points = [tuple(metrics.corner_point(BOX, n, cam)) for n in names]
            self.assertEqual(len(set(points)), 4, (height, points))
            self.assertEqual(points, [(-1.0, -1.0, 2.0), (1.0, -1.0, 2.0), (-1.0, 1.0, 2.0), (1.0, 1.0, 2.0)], height)

    def test_front_back_uses_the_floor_direction_not_the_tilted_viewing_direction(self):
        # The viewing direction with its pitch, instead of its horizontal part, shrinks the front/back weight against left/right.
        # Yaw 15 degrees under a steep camera: the true front-left corner is (-1, -1); a weight of cos(pitch) on front/back moves
        # the choice of top_back_left to the wrong side of the box.
        cam = orbit_camera(15.0, height=40.0)
        self.assertEqual(metrics.corner_point(BOX, 'top_back_left', cam), [-1.0, 1.0, 2.0])
        self.assertEqual(metrics.corner_point(BOX, 'top_front_right', cam), [1.0, -1.0, 2.0])

    def test_a_tie_takes_the_corner_nearer_the_camera(self):
        # yaw exactly 45 degrees: left and front weigh the same, so top_front_left ties between the leftmost and the nearest corner
        # of the footprint; the ruling takes the one horizontally nearer the camera.
        cam = orbit_camera(45.0)
        p = metrics.corner_point(BOX, 'top_front_left', cam)
        others = [c for c in metrics.box_corners(BOX) if c[2] == 2.0 and c[:2] != p[:2]]
        dist = lambda c: math.hypot(c[0] - cam['position'][0], c[1] - cam['position'][1])  # noqa: E731
        self.assertLessEqual(dist(p), min(dist(c) for c in others) + 1e-9)

    def test_corner_name_gives_none_for_a_point_that_is_no_corner(self):
        self.assertIsNone(metrics.corner_name([0.0, 0.0, 1.0], BOX, orbit_camera(40.0)))

    def test_holdout_error_of_a_tilted_box_uses_the_camera_side_names(self):
        cam = orbit_camera(40.0)
        spec = fx.spec()
        spec['camera'] = cam
        spec['components'] = [fx.component('block', (2, 2, 2), (0, 0, 1), group='cube')]
        corner = [-1.0, -1.0, 2.0]                                           # the near left top corner, found by hand above
        u, v, _ = metrics.camera_math.project(corner, cam, fx.W, fx.H)
        pt = [{'id': 'p', 'part_id': 'cube', 'corner': 'top_front_left', 'uv_px': [u, v], 'note': ''}]
        row = metrics.holdout_errors(spec, pt, fx.W, fx.H)[0]
        self.assertFalse(row['missing'])
        self.assertAlmostEqual(row['error_px'], 0.0, places=6)
        # seen from the other side the same name is the opposite corner; the old reading (X right, Y back) would be far off
        spec['camera'] = orbit_camera(180.0)
        u, v, _ = metrics.camera_math.project([1.0, 1.0, 2.0], spec['camera'], fx.W, fx.H)
        pt[0]['uv_px'] = [u, v]
        self.assertAlmostEqual(metrics.holdout_errors(spec, pt, fx.W, fx.H)[0]['error_px'], 0.0, places=6)
        ou, ov, _ = metrics.camera_math.project(old_corner(BOX, 'top_front_left'), spec['camera'], fx.W, fx.H)
        self.assertGreater(math.hypot(ou - u, ov - v), 50)


THREE_WORD = ['_'.join(w) for w in itertools.product(('top', 'bottom'), ('front', 'back'), ('left', 'right'))]
ONE_HORIZONTAL = ['_'.join(w) for w in itertools.product(('top', 'bottom'), ('left', 'right', 'front', 'back'))]


def screen_u(point, camera, w=fx.W, h=fx.H):
    return metrics.camera_math.project(point, camera, w, h)[0]


def horizontal_distance(point, camera):
    return math.hypot(point[0] - camera['position'][0], point[1] - camera['position'][1])


class OneHorizontalWordTests(unittest.TestCase):
    """03 section 2: top_left and friends pick by the picture (left/right) or by the distance to the camera (front/back)."""

    def test_left_and_right_pick_the_footprint_corner_that_projects_furthest_left_or_right(self):
        for yaw in (0.0, 20.0, 40.0, 45.0, 50.0, 130.0, 180.0, 250.0):
            for height in (1.0, 5.0):
                cam = orbit_camera(yaw, height=height)
                for z_word, z in (('top', 2.0), ('bottom', 0.0)):
                    foot = [c for c in metrics.box_corners(BOX) if c[2] == z]
                    for word, pick in (('left', min), ('right', max)):
                        got = metrics.corner_point(BOX, f'{z_word}_{word}', cam, fx.W, fx.H)
                        best = pick(screen_u(c, cam) for c in foot)
                        self.assertEqual(got[2], z)
                        self.assertAlmostEqual(screen_u(got, cam), best, places=6, msg=(yaw, height, z_word, word))

    def test_front_and_back_pick_the_footprint_corner_horizontally_nearest_or_furthest(self):
        for yaw in (0.0, 20.0, 40.0, 130.0, 250.0):
            cam = orbit_camera(yaw, height=3.0)
            foot = [c for c in metrics.box_corners(BOX) if c[2] == 2.0]
            for word, pick in (('front', min), ('back', max)):
                got = metrics.corner_point(BOX, f'top_{word}', cam, fx.W, fx.H)
                self.assertAlmostEqual(horizontal_distance(got, cam), pick(horizontal_distance(c, cam) for c in foot), places=9, msg=(yaw, word))

    def test_a_corner_view_of_a_box_names_the_visible_corners_with_one_word(self):
        # camera at 45 degrees over the near corner (+X, -Y): the footprint diamond has one left, one right, one near and one far vertex
        cam = orbit_camera(45.0)
        want = {'top_left': (-1.0, -1.0, 2.0), 'top_right': (1.0, 1.0, 2.0), 'top_front': (1.0, -1.0, 2.0), 'top_back': (-1.0, 1.0, 2.0)}
        for name, corner in want.items():
            self.assertEqual(tuple(metrics.corner_point(BOX, name, cam, fx.W, fx.H)), corner, name)
            self.assertTrue(metrics.name_margin(name, BOX, cam, fx.W, fx.H), name)       # these keep their corner when the camera turns

    def test_bare_words_and_words_without_top_or_bottom_are_invalid(self):
        for bad in ('left', 'right', 'front', 'back', 'top', 'bottom', 'front_left', 'top_bottom_left', 'top_left_right', 'top_front_back'):
            self.assertIsNone(metrics.corner_words(bad), bad)
        for good in THREE_WORD + ONE_HORIZONTAL:
            self.assertIsNotNone(metrics.corner_words(good), good)
            self.assertIsNotNone(metrics.corner_point(BOX, good, orbit_camera(30.0), fx.W, fx.H), good)

    def test_the_two_families_agree_when_the_box_faces_the_camera_on_the_sides_that_are_not_ambiguous(self):
        cam = orbit_camera(0.0, height=1.0)
        # facing: top_front_left (three words) and top_left (one) both take the left side; the one-word name takes the nearer corner
        self.assertEqual(metrics.corner_point(BOX, 'top_front_left', cam, fx.W, fx.H), [-1.0, -1.0, 2.0])
        self.assertEqual(metrics.corner_point(BOX, 'top_left', cam, fx.W, fx.H), [-1.0, -1.0, 2.0])
        self.assertEqual(metrics.corner_point(BOX, 'top_right', cam, fx.W, fx.H), [1.0, -1.0, 2.0])


class NameMarginTests(unittest.TestCase):
    def test_a_name_has_margin_when_the_camera_can_turn_five_degrees_and_tilt_two(self):
        cam = orbit_camera(20.0, height=3.0)
        self.assertTrue(metrics.name_margin('top_front_left', BOX, cam, fx.W, fx.H))
        # three-word names flip near the diagonal; the one-word names left/right do not
        for yaw in (41.0, 44.0, 45.0, 48.0):
            cam = orbit_camera(yaw, height=3.0)
            self.assertFalse(metrics.name_margin('top_front_left', BOX, cam, fx.W, fx.H), yaw)
            self.assertTrue(metrics.name_margin('top_left', BOX, cam, fx.W, fx.H), yaw)

    def test_the_turned_cameras_are_yaw_plus_minus_five_and_pitch_plus_minus_two_on_the_same_sphere(self):
        cam = orbit_camera(30.0, height=3.0)
        turned = metrics.turned_cameras(cam)
        self.assertEqual(len(turned), 4)
        t = cam['target']
        base = [cam['position'][i] - t[i] for i in range(3)]
        for c in turned:
            v = [c['position'][i] - t[i] for i in range(3)]
            self.assertAlmostEqual(math.dist(v, [0, 0, 0]), math.dist(base, [0, 0, 0]), places=9)
        yaws = sorted(round(math.degrees(math.atan2(c['position'][1] - t[1], c['position'][0] - t[0]) - math.atan2(base[1], base[0])), 6)
                      for c in turned if abs(c['position'][2] - cam['position'][2]) < 1e-9)
        self.assertEqual(yaws, [-5.0, 5.0])
        elev = sorted(round(math.degrees(math.atan2(c['position'][2] - t[2], math.hypot(c['position'][0] - t[0], c['position'][1] - t[1]))
                                         - math.atan2(base[2], math.hypot(base[0], base[1]))), 6)
                      for c in turned if abs(c['position'][2] - cam['position'][2]) > 1e-9)
        self.assertEqual(elev, [-2.0, 2.0])

    def test_names_reaching_lists_both_families(self):
        cam = orbit_camera(45.0)
        reach = metrics.names_reaching([-1.0, -1.0, 2.0], BOX, cam, fx.W, fx.H)
        self.assertIn('top_left', reach['one_horizontal'])
        self.assertTrue(all(metrics.corner_point(BOX, n, cam, fx.W, fx.H) == [-1.0, -1.0, 2.0] for n in reach['three_word'] + reach['one_horizontal']))


class CameraSideWeightTests(unittest.TestCase):
    """The three-word rule weighs left/right and front/back equally; 5 percent more or less on either must move a corner."""

    def test_a_five_percent_weight_change_moves_the_corner_near_the_diagonal(self):
        for height in (1.0, 9.0):                                         # level and steeply down: the rule ignores the pitch
            # yaw 44: just short of the diagonal, the x choice of top_front_left is decided by a 3 percent margin
            self.assertEqual(metrics.corner_point(BOX, 'top_front_left', orbit_camera(44.0, height=height)), [-1.0, -1.0, 2.0])
            # yaw 46: just past it, on the other side
            self.assertEqual(metrics.corner_point(BOX, 'top_front_left', orbit_camera(46.0, height=height)), [1.0, -1.0, 2.0])
            self.assertEqual(metrics.corner_point(BOX, 'top_back_right', orbit_camera(44.0, height=height)), [1.0, 1.0, 2.0])
            self.assertEqual(metrics.corner_point(BOX, 'top_back_right', orbit_camera(46.0, height=height)), [-1.0, 1.0, 2.0])


class RollTests(unittest.TestCase):
    def test_the_cameras_roll_changes_which_corner_is_left_and_right(self):
        base = orbit_camera(40.0, height=8.0)
        rolled = dict(base, roll_deg=70.0)
        for name in ('top_front_left', 'top_back_right', 'bottom_front_right'):
            self.assertNotEqual(metrics.corner_point(BOX, name, base), metrics.corner_point(BOX, name, rolled), name)
        # the picture's left and right corners, read straight from the projection of the rolled camera
        differ = 0
        for name, z, pick in (('top_left', 2.0, min), ('top_right', 2.0, max), ('bottom_left', 0.0, min), ('bottom_right', 0.0, max)):
            foot = [c for c in metrics.box_corners(BOX) if c[2] == z]
            want = pick(foot, key=lambda c: screen_u(c, rolled))
            self.assertEqual(metrics.corner_point(BOX, name, rolled, fx.W, fx.H), want, name)
            differ += metrics.corner_point(BOX, name, base, fx.W, fx.H) != want
        self.assertEqual(differ, 4)                                           # the unrolled camera would name another corner for all four


def projected_u(point, camera, w=fx.W, h=fx.H):
    return metrics.camera_math.project(point, camera, w, h)[0]


class LeftRightByProjectionTests(unittest.TestCase):
    """The one-word left/right names rank corners by projected u, not by the horizontal right vector (the two differ in perspective)."""

    def test_a_few_degrees_off_square_on_the_projection_decides(self):
        cam = orbit_camera(2.0, height=3.0)
        self.assertEqual(metrics.corner_point(BOX, 'top_right', cam, fx.W, fx.H), [1.0, -1.0, 2.0])
        self.assertEqual(metrics.corner_point(BOX, 'top_left', cam, fx.W, fx.H), [-1.0, -1.0, 2.0])
        # the near corners are the outermost ones: the nearer a corner, the further from the picture centre it projects
        right_corner = metrics.corner_point(BOX, 'top_right', cam, fx.W, fx.H)
        far_right = [1.0, 1.0, 2.0]
        self.assertGreater(projected_u(right_corner, cam), projected_u(far_right, cam))

    def test_random_boxes_and_cameras_agree_with_an_independent_projection_definition(self):
        rng = random.Random(20261023)
        checked = 0
        for _ in range(600):
            lo = [rng.uniform(-6, 2), rng.uniform(-6, 2), rng.uniform(0, 1)]
            hi = [lo[0] + rng.uniform(0.3, 6), lo[1] + rng.uniform(0.3, 6), lo[2] + rng.uniform(0.2, 3)]
            yaw, radius, height = rng.uniform(0, 360), rng.uniform(6, 16), rng.uniform(0.8, 7)
            a = math.radians(yaw)
            cam = dict(orbit_camera(0.0), position=[radius * math.sin(a), -radius * math.cos(a), height],
                       target=[rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(0, 1.5)], roll_deg=rng.uniform(-6, 6))
            for z_word, z in (('top', hi[2]), ('bottom', lo[2])):
                foot = [[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1])]
                try:
                    us = [projected_u(c, cam) for c in foot]
                except ValueError:
                    continue                                               # a corner behind the camera: a different rule applies
                for word, pick in (('left', min), ('right', max)):
                    ordered = sorted(us, reverse=(word == 'right'))
                    if abs(ordered[0] - ordered[1]) < 1e-3:
                        continue                                           # a tie within a thousandth of a pixel
                    want = foot[us.index(pick(us))]
                    got = metrics.corner_point((lo, hi), f'{z_word}_{word}', cam, fx.W, fx.H)
                    self.assertEqual(got, want, (lo, hi, cam, z_word, word))
                    checked += 1
        self.assertGreater(checked, 1000)


class BehindCameraTests(unittest.TestCase):
    DECK = ([-3.0, -4.0, 0.0], [3.0, 4.0, 0.1])                            # a 6 x 8 m deck
    CAMERA = dict(orbit_camera(0.0), position=[0.5, -3.0, 1.6], target=[0.5, 2.0, 1.0], lens_mm=24.0)

    def test_left_and_right_compare_only_the_corners_in_front_of_the_camera(self):
        # the camera stands on the deck: the two corners at y = -4 are behind it; the far left top corner is visible
        w, h = 1600, 1000
        far_left = [-3.0, 4.0, 0.1]
        self.assertEqual(metrics.corner_point(self.DECK, 'top_back_left', self.CAMERA, w, h), far_left)
        self.assertEqual(metrics.corner_point(self.DECK, 'top_left', self.CAMERA, w, h), far_left)
        self.assertEqual(metrics.corner_point(self.DECK, 'top_right', self.CAMERA, w, h), [3.0, 4.0, 0.1])
        # and the hold-out error of the visible far-left corner is zero
        u, v, _ = metrics.camera_math.project(far_left, self.CAMERA, w, h)
        spec = {'camera': self.CAMERA, 'components': [fx.component('deck', (6, 8, 0.1), (0, 0, 0.05))]}
        pt = [{'id': 'p', 'part_id': 'deck', 'corner': 'top_left', 'uv_px': [u, v], 'note': ''}]
        row = metrics.holdout_errors(spec, pt, w, h)[0]
        self.assertFalse(row['missing'])
        self.assertAlmostEqual(row['error_px'], 0.0, places=6)

    def in_front(self, cam):
        n = 0
        for c in metrics.box_corners(self.DECK):
            if c[2] == 0.1:
                try:
                    metrics.camera_math.project(c, cam, 1600, 1000)
                    n += 1
                except ValueError:
                    pass
        return n

    def test_fewer_than_two_corners_in_front_makes_the_name_unusable(self):
        w, h = 1600, 1000
        scenarios = {'all four behind': dict(position=[0.5, 4.5, 1.6], target=[0.5, 9.0, 1.0]),
                     'all four behind, other way': dict(position=[0.5, -4.5, 1.6], target=[0.5, -9.0, 1.0]),
                     'one in front': dict(position=[-2.9, -3.9, 1.6], target=[-3.9, -4.9, 1.0])}
        wanted = {'all four behind': 0, 'all four behind, other way': 0, 'one in front': 1}
        for label, kw in scenarios.items():
            cam = dict(self.CAMERA, **kw)
            self.assertEqual(self.in_front(cam), wanted[label], label)
            for word in ('top_left', 'top_right', 'bottom_left', 'bottom_right'):
                with self.assertRaises(metrics.CornerBehindCamera, msg=(label, word)):
                    metrics.corner_point(self.DECK, word, cam, w, h)
            self.assertFalse(metrics.name_margin('top_left', self.DECK, cam, w, h), label)
            spec = {'camera': cam, 'components': [fx.component('deck', (6, 8, 0.1), (0, 0, 0.05))]}
            pt = [{'id': 'p', 'part_id': 'deck', 'corner': 'top_left', 'uv_px': [10, 10], 'note': ''}]
            row = metrics.holdout_errors(spec, pt, w, h)[0]
            self.assertTrue(row['missing'], label)
            self.assertEqual(row['missing_reason'], 'corner_behind_camera', label)
        # two in front is enough
        cam = dict(self.CAMERA)
        self.assertEqual(self.in_front(cam), 2)
        self.assertIsNotNone(metrics.corner_point(self.DECK, 'top_left', cam, w, h))

    def test_three_word_and_front_back_names_do_not_need_the_projection(self):
        cam = dict(self.CAMERA, position=[0.5, 4.5, 1.6], target=[0.5, 9.0, 1.0])
        self.assertIsNotNone(metrics.corner_point(self.DECK, 'top_back_left', cam, 1600, 1000))
        self.assertIsNotNone(metrics.corner_point(self.DECK, 'top_front', cam, 1600, 1000))


if __name__ == '__main__':
    unittest.main()
