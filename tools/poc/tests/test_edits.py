"""P8a tests for edits.py: every fixer.v1 op keeps the design valid; an invalid edit is rejected, logged and not applied."""
from __future__ import annotations
import copy
import json
import math
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parents[0]))        # tools/poc, so that `loop` is a package

from loop import edits  # noqa: E402

sys.path.append(str(ROOT / 'booth-image-to-model' / 'scripts'))
import camera_math  # noqa: E402
import design_compile  # noqa: E402

TEMPLATE = ROOT / 'booth-image-to-model' / 'templates' / 'design_template.json'


def base_design():
    return json.loads(TEMPLATE.read_text(encoding='utf-8'))


def view_components(view):
    """The components a SpecView was built from (id only is needed)."""
    return [dict(c) for parts in view.parts.values() for c, _ in parts]


def predicted_edit(found, finding_id):
    return next(e for e in found if e['finding_id'] == finding_id)


def element(design, eid):
    return next(e for e in design['elements'] if e['id'] == eid)


class EditOpsCase(unittest.TestCase):
    def setUp(self):
        self.design = base_design()
        self.before = copy.deepcopy(self.design)

    def valid_edit(self, op):
        mk = edits.make_edit
        return {
            'move': mk('move', 'reception', delta_m=[0.3, 0.0, 0.0], finding_id='f1'),
            'resize': mk('resize', 'reception', size_m=[2.0, 0.6, 1.1], finding_id='f2'),
            'recolour': mk('recolour', 'reception', color_hex='#336699', finding_id='f3'),
            'add': mk('add', 'extra_plinth', kind='display_plinth', side='right', along=0.3, size_m=[0.5, 0.5, 1.0],
                      color_hex='#ffffff', finding_id='f4'),
            'remove': mk('remove', 'screen', finding_id='f5'),
            'replace_kind': mk('replace_kind', 'meeting', kind='round_table', finding_id='f6'),
            'set_count': mk('set_count', 'greens', delta_m=[1, 0, 0], finding_id='f7'),
            'set_text': mk('set_text', 'header', kind='NEW NAME', finding_id='f8'),
        }[op]

    def test_every_edit_op_keeps_design_schema_valid(self):
        self.assertEqual(set(edits.EDIT_OPS), {'move', 'resize', 'recolour', 'add', 'remove', 'replace_kind', 'set_count', 'set_text'})
        self.assertEqual(edits.validate_design(self.design), [])
        for op in edits.EDIT_OPS:
            with self.subTest(op=op):
                new = edits.apply_edit(self.design, self.valid_edit(op))
                self.assertEqual(edits.validate_design(new), [], op)
                self.assertNotEqual(new, self.design, f'{op} changed nothing')
                self.assertEqual(self.design, self.before, f'{op} modified its input')
                self.assertEqual(new['form'], self.before['form'], 'the form (facts from the designer) never changes')

    def test_each_op_does_what_it_says(self):
        d = self.design
        new = edits.apply_edit(d, self.valid_edit('move'))
        self.assertAlmostEqual(element(new, 'reception')['along'], element(d, 'reception')['along'] + 0.3 / 6.0, places=3)
        new = edits.apply_edit(d, edits.make_edit('move', 'meeting', delta_m=[0.5, -0.25, 0.1], finding_id='m'))
        self.assertEqual(element(new, 'meeting')['centre_m'], [-1.1, -0.05])
        self.assertAlmostEqual(element(new, 'meeting')['z_m'], 0.1)
        self.assertEqual(element(edits.apply_edit(d, self.valid_edit('resize')), 'reception')['size_m'], [2.0, 0.6, 1.1])
        self.assertEqual(element(edits.apply_edit(d, self.valid_edit('recolour')), 'reception')['material']['color_hex'], '#336699')
        new = edits.apply_edit(d, self.valid_edit('add'))
        self.assertEqual(element(new, 'extra_plinth')['kind'], 'display_plinth')
        self.assertEqual(len(new['elements']), len(d['elements']) + 1)
        self.assertNotIn('screen', [e['id'] for e in edits.apply_edit(d, self.valid_edit('remove'))['elements']])
        self.assertEqual(element(edits.apply_edit(d, self.valid_edit('replace_kind')), 'meeting')['kind'], 'round_table')
        self.assertEqual(element(edits.apply_edit(d, self.valid_edit('set_count')), 'greens')['count'], 3)
        self.assertEqual(element(edits.apply_edit(d, self.valid_edit('set_text')), 'header')['text']['body'], 'NEW NAME')

    def test_invalid_edit_is_rejected_and_logged_not_applied(self):
        mk = edits.make_edit
        bad = {
            'unknown_element': mk('move', 'no_such_element', delta_m=[0.1, 0, 0], finding_id='b1'),
            'along_beyond_side': mk('move', 'reception', delta_m=[20.0, 0, 0], finding_id='b2'),
            'not_a_colour': mk('recolour', 'reception', color_hex='red', finding_id='b3'),
            'count_below_one': mk('set_count', 'greens', delta_m=[-5, 0, 0], finding_id='b4'),
            'unknown_kind': mk('replace_kind', 'meeting', kind='sofa', finding_id='b5'),
            'duplicate_id': mk('add', 'screen', kind='tv', side='left', finding_id='b6'),
            'add_without_kind': mk('add', 'new_one', finding_id='b7'),
            'text_too_long': mk('set_text', 'header', kind='X' * 61, finding_id='b8'),
            'resize_zero': mk('resize', 'reception', size_m=[0.0, 0.5, 1.0], finding_id='b9'),
        }
        unknown_op = dict(mk('move', 'reception', delta_m=[0.1, 0, 0], finding_id='b10'), op='teleport')
        extra_field = dict(mk('move', 'reception', delta_m=[0.1, 0, 0], finding_id='b11'), colour='red')
        bad.update(unknown_op=unknown_op, extra_field=extra_field)
        for name, e in bad.items():
            with self.subTest(case=name):
                with self.assertRaises(edits.EditError):
                    edits.apply_edit(self.design, e)
                self.assertEqual(self.design, self.before)

        good1 = mk('move', 'reception', delta_m=[0.3, 0, 0], finding_id='g1')
        good2 = mk('recolour', 'screen', color_hex='#111111', finding_id='g2')
        lines = []
        new, report = edits.apply_edits(self.design, [good1, bad['along_beyond_side'], good2, bad['not_a_colour']], log=lines.append)
        self.assertEqual([a['finding_id'] for a in report['applied']], ['g1', 'g2'])
        self.assertEqual([r['edit']['finding_id'] for r in report['rejected']], ['b2', 'b3'])
        self.assertTrue(all(r['reason'] for r in report['rejected']))
        self.assertEqual(len(lines), 2, 'each rejected edit is logged once')
        self.assertEqual(edits.validate_design(new), [])
        self.assertAlmostEqual(element(new, 'reception')['along'], element(self.design, 'reception')['along'] + 0.05, places=3)
        self.assertEqual(element(new, 'screen')['material']['color_hex'], '#111111')

    def test_fixer_document_is_strict(self):
        doc = edits.make_fixer([self.valid_edit('move')])
        self.assertEqual(doc['schema_version'], 'fixer.v1')
        self.assertEqual(edits.validate_fixer(doc), [])
        self.assertTrue(edits.validate_fixer(dict(doc, extra=1)))
        self.assertTrue(edits.validate_fixer({'schema_version': 'fixer.v1'}))
        broken = copy.deepcopy(doc)
        del broken['edits'][0]['finding_id']
        self.assertTrue(edits.validate_fixer(broken))


class FindingsCase(unittest.TestCase):
    def setUp(self):
        self.design = base_design()

    def finding(self, fid, eid, typ, direction):
        return {'id': fid, 'element_id': eid, 'type': typ, 'severity': 'high', 'direction': direction, 'bbox_px': None}

    def test_findings_become_valid_edits(self):
        findings = [
            self.finding('a', 'reception', 'misplaced', 'right'),
            self.finding('b', 'screen', 'extra', None),
            self.finding('c', 'reception', 'wrong_size', 'bigger'),
            self.finding('d', 'greens', 'wrong_count', 'bigger'),
            self.finding('e', 'header', 'wrong_colour', 'darker'),
            self.finding('f', 'meeting', 'missing', None),            # no kind is known: skipped, not guessed
            self.finding('g', None, 'camera_off', None),              # the design cannot change the camera
        ]
        found, skipped = edits.findings_to_edits(self.design, findings, step_m=0.3)
        self.assertEqual([e['op'] for e in found], ['move', 'remove', 'resize', 'set_count', 'recolour'])
        self.assertEqual({s['finding_id'] for s in skipped}, {'f', 'g'})
        self.assertEqual(found[0]['delta_m'], [0.3, 0.0, 0.0])
        new, report = edits.apply_edits(self.design, found)
        self.assertEqual(report['rejected'], [])
        self.assertEqual(edits.validate_design(new), [])

    def test_directions_map_to_axes(self):
        for direction, want in (('left', [-0.3, 0, 0]), ('right', [0.3, 0, 0]), ('forward', [0, -0.3, 0]), ('back', [0, 0.3, 0])):
            found, _ = edits.findings_to_edits(self.design, [self.finding('x', 'meeting', 'misplaced', direction)], step_m=0.3)
            self.assertEqual([round(v, 6) for v in found[0]['delta_m']], want, direction)

    def test_up_and_down_are_skipped_without_the_compiled_spec(self):
        # a height is only known from what the compiler built: no spec, no guess from zero
        for direction in ('up', 'down'):
            found, skipped = edits.findings_to_edits(self.design, [self.finding('x', 'meeting', 'misplaced', direction)], step_m=0.3)
            self.assertEqual(found, [])
            self.assertIn('spec', skipped[0]['reason'])

    def test_picture_directions_follow_the_fitted_camera(self):
        cam_y = {'position': [3.5, -7.6, 1.9], 'target': [0.0, 0.0, 1.4], 'lens_mm': 35.0, 'sensor_width_mm': 36.0}
        cam_x = {'position': [7.6, 0.0, 1.9], 'target': [0.0, 0.0, 1.4], 'lens_mm': 35.0, 'sensor_width_mm': 36.0}   # turned 90 degrees
        plain = {'position': [0.0, -7.6, 1.9], 'target': [0.0, 0.0, 1.4], 'lens_mm': 35.0, 'sensor_width_mm': 36.0}

        def near(got, want):
            return all(abs(g - w) < 1e-4 for g, w in zip(got, want))
        self.assertTrue(near(edits.direction_delta('right', 0.3, plain), [0.3, 0, 0]))
        self.assertTrue(near(edits.direction_delta('forward', 0.3, plain), [0, -0.3, 0]), 'forward is towards the camera')
        self.assertTrue(near(edits.direction_delta('right', 0.3, cam_x), [0, 0.3, 0]), 'right turns with the camera')
        self.assertTrue(near(edits.direction_delta('left', 0.3, cam_x), [0, -0.3, 0]))
        self.assertTrue(near(edits.direction_delta('forward', 0.3, cam_x), [0.3, 0, 0]))
        self.assertTrue(near(edits.direction_delta('back', 0.3, cam_x), [-0.3, 0, 0]))
        self.assertTrue(near(edits.direction_delta('up', 0.3, cam_x), [0, 0, 0.3]))
        self.assertTrue(near(edits.direction_delta('down', 0.3, cam_x), [0, 0, -0.3]))
        d = edits.direction_delta('right', 0.3, cam_y)                    # an oblique camera: a unit step along the floor projection
        self.assertAlmostEqual(d[0] ** 2 + d[1] ** 2, 0.09, places=4)
        self.assertGreater(d[1], 0.01)
        self.assertEqual(edits.direction_of(0.0, 1.0, cam_x), 'right')
        self.assertEqual(edits.direction_of(1.0, 0.0, plain), 'right')
        finding = {'id': 'x', 'element_id': 'meeting', 'type': 'misplaced', 'severity': 'high', 'direction': 'right', 'bbox_px': None}
        found, _ = edits.findings_to_edits(self.design, [finding], step_m=0.3, camera=cam_x)
        self.assertTrue(near(found[0]['delta_m'], [0, 0.3, 0]))


class SpecCase(unittest.TestCase):
    """Edits that need what the compiler built: heights, pixel offsets, sizes. The design is compiled for real (no Blender)."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        cls.design = base_design()
        cls.design['elements'].append({'id': 'canopy', 'kind': 'ring', 'params': {'inset_m': 0.5}})
        cls.design['elements'].append({'id': 'table1', 'kind': 'round_table', 'side': 'centre', 'centre_m': [1.0, 0.5],
                                       'params': {'radius_m': 0.5}})
        cls.design['elements'].append({'id': 'pot', 'kind': 'planter', 'side': 'centre', 'centre_m': [1.5, -0.8]})
        cls.design['elements'].append({'id': 'desk', 'kind': 'counter', 'side': 'centre', 'centre_m': [-1.0, -0.9]})
        cls.design['elements'].append({'id': 'shell1', 'kind': 'wall', 'side': 'left'})
        cls.design['elements'].append({'id': 'banner', 'kind': 'graphic_panel', 'side': 'left', 'along': 0.6, 'size_m': [1.0, 0.04, 0.8], 'z_m': 2.8})
        cls.design['elements'].append({'id': 'poster', 'kind': 'graphic_panel', 'side': 'left', 'along': 0.2, 'size_m': [1.0, 0.04, 0.8]})
        cls.design['elements'].append({'id': 'led', 'kind': 'led_wall', 'side': 'right', 'along': 0.5, 'size_m': [3.0, 0.1, 2.0],
                                       'params': {'module_m': 1.0}})
        cls.spec = cls.compile(cls.design, 'base')
        cls.ids = [e['id'] for e in cls.design['elements']]
        cls.view = edits.SpecView(cls.spec, cls.ids)
        cls.camera = cls.spec['camera']

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    @classmethod
    def compile(cls, design, name):
        path = cls.tmp / f'{name}.json'
        path.write_text(json.dumps(design), encoding='utf-8')
        script = ROOT / 'booth-image-to-model' / 'scripts' / 'boothctl.py'
        done = subprocess.run([sys.executable, str(script), 'design-compile', '--design', str(path), '--out', str(cls.tmp / f'{name}_out')],
                              capture_output=True, text=True)
        assert done.returncode == 0, done.stdout[-300:]
        return json.loads((cls.tmp / f'{name}_out' / 'spec.json').read_text(encoding='utf-8'))

    def finding(self, eid, typ, direction, bbox=None, severity='high', fid='f1'):
        return {'id': fid, 'element_id': eid, 'type': typ, 'severity': severity, 'direction': direction, 'bbox_px': bbox}

    def edits_for(self, finding, **kw):
        return edits.findings_to_edits(self.design, [finding], camera=self.camera, spec=self.view, **kw)

    def z_of(self, spec, cid):
        return next(c for c in spec['components'] if c['id'] == cid)['location'][2]

    # -- 1: heights start from where the element really is
    def test_a_ring_without_z_m_goes_up_from_its_real_height(self):
        self.assertNotIn('z_m', element(self.design, 'canopy'))
        found, skipped = self.edits_for(self.finding('canopy', 'misplaced', 'up', severity='low'))        # the small step, 0.05 m
        self.assertEqual(skipped, [])
        bottom = self.view.bottom_rel('canopy')
        self.assertAlmostEqual(bottom, 3.0, delta=0.02, msg='the ring hangs 0.3 m high under H - 0.1')
        self.assertAlmostEqual(found[0]['delta_m'][2], 0.05, places=4, msg='delta_m is the real move in metres, not the z_m change')
        new = edits.apply_edit(self.design, found[0], view=self.view)
        self.assertAlmostEqual(element(new, 'canopy')['z_m'], bottom + 0.05, places=2, msg='not 0 + 0.05')
        spec2 = self.compile(new, 'ring_up')
        self.assertGreater(self.z_of(spec2, 'canopy_ring'), self.z_of(self.spec, 'canopy_ring'), 'the ring got higher')
        self.assertAlmostEqual(self.z_of(spec2, 'canopy_ring') - self.z_of(self.spec, 'canopy_ring'), 0.05, delta=0.01)

    def test_a_ring_down_is_a_small_step_down_not_a_drop_to_the_floor(self):
        found, _ = self.edits_for(self.finding('canopy', 'misplaced', 'down', severity='low'))
        self.assertAlmostEqual(found[0]['delta_m'][2], -0.05, places=4, msg='a real move of -0.05 m, not +2.95 (the new z_m)')
        new = edits.apply_edit(self.design, found[0], view=self.view)
        spec2 = self.compile(new, 'ring_down')
        self.assertAlmostEqual(self.z_of(self.spec, 'canopy_ring') - self.z_of(spec2, 'canopy_ring'), 0.05, delta=0.01)

    def test_a_vertical_move_that_leaves_the_booth_is_refused(self):
        found, skipped = self.edits_for(self.finding('canopy', 'misplaced', 'up'), step_m=0.3)            # the top would pass 3.5 m
        self.assertEqual(found, [])
        self.assertIn('height limit', skipped[0]['reason'])
        found, skipped = self.edits_for(self.finding('table1', 'misplaced', 'down'), step_m=0.2)          # it stands on the floor already
        self.assertEqual(found, [])
        self.assertIn('floor', skipped[0]['reason'])
        with self.assertRaises(edits.EditError):
            edits.apply_edit(self.design, edits.make_edit('move', 'table1', delta_m=[0, 0, -0.5], finding_id='x'))

    def test_a_hanging_light_is_measured_from_its_top(self):
        found, _ = self.edits_for(self.finding('downlights', 'misplaced', 'down', severity='low'))
        self.assertAlmostEqual(found[0]['delta_m'][2], -0.05, places=4)
        new = edits.apply_edit(self.design, found[0], view=self.view)
        top_rel = self.view.top('downlights') - self.view.zf
        self.assertAlmostEqual(element(new, 'downlights')['z_m'], top_rel - 0.05, places=2)

    # -- 2: the step comes from the pixel offset
    def test_a_pixel_offset_sizes_the_move_through_the_camera(self):
        box = self.view.box_px('reception')
        z, f = self.view.depth('reception'), camera_math.focal_px(self.camera, self.view.width)
        shifted = [box[0] + 20, box[1], box[2] + 20, box[3]]
        found, _ = self.edits_for(self.finding('reception', 'misplaced', 'right', shifted))
        dx, dy, dz = found[0]['delta_m']
        self.assertAlmostEqual(math.hypot(dx, dy), 20 * z / f, delta=0.02 * 20 * z / f, msg='d = px * Z / f')
        self.assertEqual(dz, 0.0)
        right = edits.ground_axes(self.camera)[0]
        self.assertGreater(dx * right[0] + dy * right[1], 0.0, 'to the picture\'s right')
        found, _ = self.edits_for(self.finding('reception', 'misplaced', 'left', [box[0] - 20, box[1], box[2] - 20, box[3]]))
        self.assertAlmostEqual(math.hypot(*found[0]['delta_m'][:2]), 20 * z / f, delta=0.02 * 20 * z / f)

    def test_a_few_pixels_are_a_few_centimetres(self):
        box = self.view.box_px('reception')
        z, f = self.view.depth('reception'), camera_math.focal_px(self.camera, self.view.width)
        found, _ = self.edits_for(self.finding('reception', 'misplaced', 'right', [box[0] + 3, box[1], box[2] + 3, box[3]]))
        step = math.hypot(*found[0]['delta_m'][:2])
        self.assertAlmostEqual(step, 3 * z / f, delta=0.003)
        self.assertLess(step, 0.06, 'a 3 px offset is not a 30 cm move')

    def test_the_move_is_capped_and_vertical_offsets_become_heights(self):
        box = self.view.box_px('reception')
        found, _ = self.edits_for(self.finding('reception', 'misplaced', 'right', [box[0] + 3000, box[1], box[2] + 3000, box[3]]))
        self.assertAlmostEqual(math.hypot(*found[0]['delta_m'][:2]), 0.5, places=3)
        sbox = self.view.box_px('screen')
        z, f = self.view.depth('screen'), camera_math.focal_px(self.camera, self.view.width)
        up_z = camera_math.axes(self.camera)[2][2]
        found, skipped = self.edits_for(self.finding('screen', 'misplaced', 'up', [sbox[0], sbox[1] - 30, sbox[2], sbox[3] - 30]))
        self.assertEqual(skipped, [])
        new = edits.apply_edit(self.design, found[0], view=self.view)
        self.assertAlmostEqual(element(new, 'screen')['z_m'] - self.view.bottom_rel('screen'), 30 * z / f / up_z, delta=0.01)

    def test_forward_is_sized_from_the_vertical_pixel_offset_on_the_floor(self):
        box = self.view.box_px('reception')
        found, _ = self.edits_for(self.finding('reception', 'misplaced', 'forward', [box[0], box[1] + 10, box[2], box[3] + 10]))
        dx, dy, _ = found[0]['delta_m']
        c = self.view.centroid('reception')
        v0 = camera_math.project(c, self.camera, self.view.width, self.view.height)[1]
        v1 = camera_math.project([c[0] + dx, c[1] + dy, c[2]], self.camera, self.view.width, self.view.height)[1]
        self.assertAlmostEqual(v1 - v0, 10.0, delta=1.5, msg='the element moves about 10 px down the picture')
        toward = edits.ground_axes(self.camera)[1]
        self.assertGreater(dx * toward[0] + dy * toward[1], 0.0, 'towards the camera')

    def test_without_a_box_the_steps_are_five_and_ten_centimetres(self):
        for severity, want in (('high', 0.1), ('medium', 0.05), ('low', 0.05)):
            found, _ = self.edits_for(self.finding('reception', 'misplaced', 'right', severity=severity))
            self.assertAlmostEqual(math.hypot(*found[0]['delta_m'][:2]), want, places=4, msg=severity)

    # -- 3: sizes without a size_m
    def sized_after(self, finding, name):
        """-> (the element, its controlled size before, after the edit, the design after) for a wrong_size finding, edit recompiled."""
        eid = finding['element_id']
        before = edits.spec_size(element(self.design, eid), self.view)
        found, skipped = self.edits_for(finding)
        self.assertEqual(skipped, [], eid)
        self.assertEqual(found[0]['op'], 'resize')
        new = edits.apply_edit(self.design, found[0])
        self.assertEqual(edits.validate_design(new), [])
        view2 = edits.SpecView(self.compile(new, name), self.ids)
        return before, edits.spec_size(element(new, eid), view2), new, view2

    def test_a_planter_is_resized_by_its_pot_in_both_directions(self):
        self.assertNotIn('size_m', element(self.design, 'pot'))
        pot = edits.spec_size(element(self.design, 'pot'), self.view)
        self.assertAlmostEqual(pot[2], 0.45, places=2, msg='size_m[2] of a planter is the pot, not the plant on it (0.99 m)')
        self.assertAlmostEqual(pot[0], 0.4, places=2)
        self.assertAlmostEqual(edits.spec_size(element(self.design, 'greens'), self.view)[0], 0.4, places=2, msg='one pot of a row of two')
        total = self.view.dims('pot')[2]
        for direction, ratio in (('bigger', 1.1), ('smaller', 1 / 1.1)):
            with self.subTest(direction=direction):
                before, after, new, view2 = self.sized_after(self.finding('pot', 'wrong_size', direction), f'pot_{direction}')
                self.assertAlmostEqual(after[2] / before[2], ratio, delta=0.01, msg='pot height')
                self.assertAlmostEqual(after[1] / before[1], ratio, delta=0.01, msg='pot depth')
                self.assertAlmostEqual(after[0] / before[0], ratio, delta=0.01, msg='pot width')
                grown = view2.dims('pot')[2] > total
                self.assertEqual(grown, direction == 'bigger', 'the whole planter, plants included, goes the way the critic said')

    def test_a_counter_is_resized_by_its_body_not_its_overhanging_top(self):
        self.assertNotIn('size_m', element(self.design, 'desk'))
        for direction, ratio in (('bigger', 1.1), ('smaller', 1 / 1.1)):
            with self.subTest(direction=direction):
                before, after, _, _ = self.sized_after(self.finding('desk', 'wrong_size', direction), f'desk_{direction}')
                self.assertAlmostEqual(before[0], 1.8, places=2, msg='the body, without the 2 cm of top overhang')
                for i, name in enumerate(('width', 'depth', 'height')):
                    self.assertAlmostEqual(after[i] / before[i], ratio, delta=0.01, msg=name)

    def test_a_resize_whose_compiled_change_goes_the_wrong_way_or_nowhere_is_dropped(self):
        pot = self.finding('pot', 'wrong_size', 'bigger', fid='p1')
        found, _ = self.edits_for(pot)
        smaller, _ = self.edits_for(self.finding('pot', 'wrong_size', 'smaller'))
        right = edits.SpecView(self.compile(edits.apply_edit(self.design, found[0]), 'chk_right'), self.ids)
        wrong = edits.SpecView(self.compile(edits.apply_edit(self.design, smaller[0]), 'chk_wrong'), self.ids)
        kept, dropped = edits.check_resizes(self.design, self.view, right, found, [pot])
        self.assertEqual((kept, dropped), (found, []))
        kept, dropped = edits.check_resizes(self.design, self.view, wrong, found, [pot])
        self.assertEqual(kept, [])
        self.assertEqual(dropped[0]['finding_id'], 'p1')
        self.assertIn('direction', dropped[0]['reason'])
        kept, dropped = edits.check_resizes(self.design, self.view, self.view, found, [pot])
        self.assertEqual(kept, [])
        self.assertIn('unchanged', dropped[0]['reason'])
        other = edits.make_edit('move', 'pot', delta_m=[0.1, 0.0, 0.0], finding_id='m1')
        kept, dropped = edits.check_resizes(self.design, self.view, wrong, [other], [])
        self.assertEqual((kept, dropped), ([other], []), 'only resizes are measured')

    def test_an_led_wall_resize_that_snaps_back_to_the_same_modules_is_dropped(self):
        f = self.finding('led', 'wrong_size', 'smaller', fid='l1')
        found, skipped = self.edits_for(f)
        self.assertEqual(skipped, [])
        view2 = edits.SpecView(self.compile(edits.apply_edit(self.design, found[0]), 'led_small'), self.ids)
        kept, dropped = edits.check_resizes(self.design, self.view, view2, found, [f])
        self.assertEqual(kept, [], 'the 3 x 2 modules are still 3 x 2: no candidate slot for it')
        self.assertIn('unchanged', dropped[0]['reason'])

    def test_a_round_table_is_resized_through_its_radius(self):
        found, skipped = self.edits_for(self.finding('table1', 'wrong_size', 'bigger'))
        self.assertEqual(skipped, [])
        new = edits.apply_edit(self.design, found[0])
        self.assertGreater(element(new, 'table1')['params']['radius_m'], 0.5)
        found, _ = self.edits_for(self.finding('table1', 'wrong_size', 'smaller'))
        self.assertLess(element(edits.apply_edit(self.design, found[0]), 'table1')['params']['radius_m'], 0.5)
        spec2 = self.compile(edits.apply_edit(self.design, self.edits_for(self.finding('table1', 'wrong_size', 'bigger'))[0][0]), 'table_big')
        self.assertGreater(edits.SpecView(spec2, self.ids).dims('table1')[0], self.view.dims('table1')[0])

    def test_a_box_that_is_bigger_in_the_picture_scales_the_size_by_the_pixel_ratio(self):
        box = self.view.box_px('reception')
        cx, w = (box[0] + box[2]) / 2, box[2] - box[0]
        found, _ = self.edits_for(self.finding('reception', 'wrong_size', 'bigger', [cx - w * 0.75, box[1], cx + w * 0.75, box[3]]))
        old = element(self.design, 'reception')['size_m']
        new = found[0]['size_m']
        self.assertAlmostEqual(new[0] / old[0], 1.5, delta=0.03)
        self.assertAlmostEqual(new[2] / old[2], 1.1, delta=0.01, msg='a height ratio of 1 does not agree with "bigger": the small step, not 1.0')

    def test_kinds_whose_size_the_compiler_fixes_are_skipped_with_a_reason(self):
        bare = copy.deepcopy(self.design)
        element(bare, 'header').pop('size_m')                     # a header band with no size_m: nothing to start from
        reasons = {}
        for eid in ('meeting', 'screen', 'downlights', 'header'):
            found, skipped = edits.findings_to_edits(bare, [self.finding(eid, 'wrong_size', 'bigger')], camera=self.camera, spec=self.view)
            self.assertEqual(found, [], eid)
            self.assertTrue(skipped[0]['reason'], eid)
            reasons[eid] = skipped[0]['reason']
        self.assertIn('inch', reasons['screen'], 'a tv follows params.inch')
        self.assertNotIn('inch', reasons['header'])
        self.assertNotEqual(reasons['screen'], reasons['meeting'])

    def test_a_ring_is_resized_through_its_band_and_its_height(self):
        ring = edits.spec_size(element(self.design, 'canopy'), self.view)
        self.assertEqual(ring[0], None)
        self.assertAlmostEqual(ring[1], 0.4, places=2, msg='size_m[1] of a ring is its band, not its depth')
        self.assertAlmostEqual(ring[2], 0.3, places=2)
        for direction, ratio in (('smaller', 1 / 1.1), ('bigger', 1.1)):
            with self.subTest(direction=direction):
                before, after, new, _ = self.sized_after(self.finding('canopy', 'wrong_size', direction), f'ring_{direction}')
                self.assertIsNone(element(new, 'canopy')['size_m'][0])
                self.assertAlmostEqual(after[1] / before[1], ratio, delta=0.01)
                self.assertAlmostEqual(after[2] / before[2], ratio, delta=0.01)

    # -- 4: a design that was tried is recognised
    def test_design_hash_ignores_key_order_and_float_noise(self):
        a = base_design()
        b = json.loads(json.dumps(a, sort_keys=True))
        self.assertEqual(edits.design_hash(a), edits.design_hash(b))
        moved = edits.apply_edit(a, edits.make_edit('move', 'meeting', delta_m=[0.2, 0.0, 0.0], finding_id='m'))
        self.assertNotEqual(edits.design_hash(a), edits.design_hash(moved))
        noisy = copy.deepcopy(moved)
        noisy['elements'][4]['centre_m'][0] += 1e-7
        self.assertEqual(edits.design_hash(moved), edits.design_hash(noisy))

    def test_findings_can_be_limited_to_geometry_or_colour(self):
        fs = [self.finding('reception', 'misplaced', 'right', fid='a'), self.finding('reception', 'wrong_size', 'bigger', fid='b'),
              self.finding('hero_wall', 'wrong_colour', 'darker', fid='c'), self.finding('greens', 'wrong_count', 'bigger', fid='d')]
        geo, _ = edits.findings_to_edits(self.design, fs, camera=self.camera, spec=self.view, only=edits.GEOMETRY_TYPES)
        col, _ = edits.findings_to_edits(self.design, fs, camera=self.camera, spec=self.view, only=edits.COLOUR_TYPES)
        everything, _ = edits.findings_to_edits(self.design, fs, camera=self.camera, spec=self.view)
        self.assertEqual({e['op'] for e in geo}, {'move', 'resize'})
        self.assertEqual({e['op'] for e in col}, {'recolour'})
        self.assertEqual({e['op'] for e in everything}, {'move', 'resize', 'recolour', 'set_count'})

    # -- clipping: the critic can only box what is inside the picture
    CAM2 = {'position': [1.0, -3.2, 1.6], 'target': [0.0, 0.5, 1.5], 'lens_mm': 28.0}

    @property
    def cut_view(self):
        spec = copy.deepcopy(self.spec)
        spec['camera'] = dict(self.camera, **self.CAM2)
        return edits.SpecView(spec, self.ids)

    @property
    def cut_camera(self):
        return dict(self.camera, **self.CAM2)

    def cut_edits(self, finding):
        return edits.findings_to_edits(self.design, [finding], camera=self.cut_camera, spec=self.cut_view)

    def test_the_render_box_is_clipped_to_the_picture(self):
        raw, box = self.cut_view.box_px_raw('header'), self.cut_view.box_px('header')
        self.assertLess(raw[0], -500)
        self.assertGreater(raw[2], self.cut_view.width)
        self.assertEqual(box[0], 0.0)
        self.assertEqual(box[2], float(self.cut_view.width))
        self.assertEqual((box[1], box[3] <= self.cut_view.height), (0.0, True))

    def test_a_three_pixel_offset_of_a_box_cut_by_the_frame_is_not_half_a_metre(self):
        box = self.cut_view.box_px('header')
        for direction, shifted in (('right', [box[0] + 3, box[1], box[2] + 3, box[3]]), ('up', [box[0], box[1] - 3, box[2], box[3] - 3])):
            found, skipped = self.cut_edits(self.finding('header', 'misplaced', direction, shifted))
            moved = found[0]['delta_m']
            if direction == 'right':
                self.assertAlmostEqual(math.hypot(*moved), 0.1, places=4, msg='both side edges are cut: the small step (high severity)')
            else:       # the lower edge of the band is inside the picture: a real 3 px offset is a centimetre or two, not half a metre
                step = moved[2]                          # delta_m is the real move
                self.assertLess(step, 0.05)
                self.assertGreater(step, 0.0)

    def half_cut(self):
        """A view with one box cut by the frame on its left edge only."""
        comp = {'id': 'a', 'kind': 'box', 'rotation_deg': [0, 0, 0], 'params': {'size': [2.0, 1.0, 1.0]}, 'role': 'box'}
        for x in [-2.0 - 0.25 * k for k in range(60)]:
            spec = {'camera': self.camera, 'source': {'width': 640, 'height': 400}, 'components': [dict(comp, location=[x, 0.0, 1.0])]}
            view = edits.SpecView(spec, ['a'])
            raw = view.box_px_raw('a')
            if raw and raw[0] < 0 < raw[2] < 640 and 0 < raw[1] and raw[3] < 400:
                return view
        self.fail('no position found where the left edge is cut')

    def test_only_the_edges_that_neither_box_has_cut_are_compared(self):
        view = self.half_cut()
        raw = view.box_px_raw('a')
        z, f = view.depth('a'), camera_math.focal_px(view.camera, view.width)
        critic = [0.0, raw[1], raw[2] + 6, raw[3]]               # the critic's left edge is the frame; the right edge is 6 px off
        found, _ = edits.findings_to_edits({'form': {'footprint_m': [6, 4], 'height_limit_m': 3.5}, 'elements': [
            {'id': 'a', 'kind': 'box'}]}, [self.finding('a', 'misplaced', 'right', critic)], camera=view.camera, spec=view)
        self.assertAlmostEqual(math.hypot(*found[0]['delta_m']), 6 * z / f, delta=0.03 * 6 * z / f, msg='the right edge only')

    def test_a_box_cut_by_the_frame_is_not_resized_by_a_clipped_ratio(self):
        raw = self.cut_view.box_px_raw('reception')
        self.assertTrue(raw[2] > self.cut_view.width or raw[3] > self.cut_view.height or raw[0] < 0, 'the box is cut here')
        cx, cy = (raw[0] + raw[2]) / 2, (raw[1] + raw[3]) / 2
        w, h = (raw[2] - raw[0]) * 1.05, (raw[3] - raw[1]) * 1.05
        critic = [max(0.0, cx - w / 2), max(0.0, cy - h / 2), min(float(self.cut_view.width), cx + w / 2), min(float(self.cut_view.height), cy + h / 2)]
        found, _ = self.cut_edits(self.finding('reception', 'wrong_size', 'bigger', critic))
        old = element(self.design, 'reception')['size_m']
        for new, was in zip(found[0]['size_m'], old):
            self.assertGreaterEqual(new / was, 1.0 - 1e-9)
            self.assertLessEqual(new / was, 1.15, 'a clipped ratio would have halved or doubled it')

    def test_a_pixel_offset_against_the_direction_gives_the_small_step(self):
        box = self.view.box_px('reception')
        found, _ = self.edits_for(self.finding('reception', 'misplaced', 'right', [box[0] - 20, box[1], box[2] - 20, box[3]], severity='medium'))
        self.assertAlmostEqual(math.hypot(*found[0]['delta_m'][:2]), 0.05, places=4, msg='20 px to the left, "right" said')
        found, _ = self.edits_for(self.finding('reception', 'misplaced', 'left', [box[0] + 20, box[1], box[2] + 20, box[3]]))
        self.assertAlmostEqual(math.hypot(*found[0]['delta_m'][:2]), 0.1, places=4)
        sbox = self.view.box_px('screen')
        found, _ = self.edits_for(self.finding('screen', 'misplaced', 'up', [sbox[0], sbox[1] + 25, sbox[2], sbox[3] + 25]))
        new = edits.apply_edit(self.design, found[0], view=self.view)
        self.assertAlmostEqual(element(new, 'screen')['z_m'] - self.view.bottom_rel('screen'), 0.1, places=3, msg='25 px down, "up" said')
        found, _ = self.edits_for(self.finding('reception', 'misplaced', 'forward', [box[0], box[1] - 15, box[2], box[3] - 15]))
        self.assertAlmostEqual(math.hypot(*found[0]['delta_m'][:2]), 0.1, places=4, msg='15 px up the picture, "forward" said')

    def steep(self, pitch_target):
        spec = copy.deepcopy(self.spec)
        spec['camera'] = dict(self.camera, position=[0.0, -2.0, 9.0], target=pitch_target)
        return spec['camera'], edits.SpecView(spec, self.ids)

    def test_the_vertical_offset_is_divided_by_the_camera_up_vector(self):
        for target, floor in (([0.0, 0.0, 0.0], False), ([0.0, -1.95, 0.0], True)):
            camera, view = self.steep(target)
            up_z = camera_math.axes(camera)[2][2]
            self.assertLess(up_z, 0.8 if not floor else 0.25)
            box = view.box_px('screen')
            z, f = view.depth('screen'), camera_math.focal_px(camera, view.width)
            found, skipped = edits.findings_to_edits(self.design, [self.finding('screen', 'misplaced', 'up', [box[0], box[1] - 30, box[2], box[3] - 30])],
                                                     camera=camera, spec=view)
            self.assertEqual(skipped, [])
            new = edits.apply_edit(self.design, found[0], view=view)
            moved = element(new, 'screen')['z_m'] - view.bottom_rel('screen')
            self.assertAlmostEqual(moved, min(0.5, 30 * z / f / max(up_z, 0.2)), delta=0.01, msg=f'up_z {up_z:.2f}')

    # -- P8-fix round 3
    def band_box(self):
        """The ring's band only (no posts), as the critic would box it, and the ring's whole render box."""
        view = self.view
        raw = view.box_px_raw('canopy', lambda c: c.get('role') == 'ring')
        return raw, view.box_px_raw('canopy')

    def test_the_ring_resize_compares_the_band_not_the_ring_with_its_posts(self):
        band, whole = self.band_box()
        self.assertGreater(whole[3] - whole[1], band[3] - band[1] + 20, 'the posts make the whole box taller than the band')
        cx, w = (band[0] + band[2]) / 2, (band[2] - band[0]) * 0.95
        critic = [cx - w / 2, band[1], cx + w / 2, band[3]]                 # a band 5 % narrower, the same height
        found, skipped = self.edits_for(self.finding('canopy', 'wrong_size', 'smaller', critic))
        self.assertEqual(skipped, [])
        size = found[0]['size_m']
        self.assertIsNone(size[0])
        small = 1 / 1.1
        self.assertAlmostEqual(size[1], 0.4 * small, places=3, msg='band width: the small step, a ratio on it would mean nothing')
        self.assertAlmostEqual(size[2], 0.3 * small, places=3, msg='not halved by the posts')

    def test_a_planter_takes_a_width_ratio_from_its_pot_and_a_small_step_for_the_height(self):
        pot = self.view.box_px_raw('pot', lambda c: c.get('role') == 'planter')
        whole = self.view.box_px_raw('pot')
        self.assertGreater(whole[3] - whole[1], pot[3] - pot[1] + 20, 'the plants make the box taller')
        cx, w = (pot[0] + pot[2]) / 2, (pot[2] - pot[0]) * 1.2
        critic = [cx - w / 2, whole[1], cx + w / 2, whole[3]]               # the critic boxes pot and plants
        found, _ = self.edits_for(self.finding('pot', 'wrong_size', 'bigger', critic))
        size = found[0]['size_m']
        before = edits.spec_size(element(self.design, 'pot'), self.view)
        self.assertAlmostEqual(size[0] / before[0], 1.2, delta=0.03, msg='the width is set by the pot')
        self.assertAlmostEqual(size[2] / before[2], 1.1, delta=0.01, msg='the height of the box is set by the plants: small step')

    def test_an_axis_whose_ratio_contradicts_the_direction_takes_the_small_step(self):
        box = self.view.box_px('reception')
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        w, h = (box[2] - box[0]) * 1.2, (box[3] - box[1]) * 0.9                # wider, but lower: "bigger"
        found, _ = self.edits_for(self.finding('reception', 'wrong_size', 'bigger', [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]))
        old = element(self.design, 'reception')['size_m']
        size = found[0]['size_m']
        self.assertAlmostEqual(size[0] / old[0], 1.2, delta=0.03)
        self.assertAlmostEqual(size[2] / old[2], 1.1, delta=0.005, msg='the height ratio contradicts the direction: the small step, not 1.0')
        found, _ = self.edits_for(self.finding('reception', 'wrong_size', 'smaller', [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]))
        size = found[0]['size_m']
        self.assertAlmostEqual(size[0] / old[0], 1 / 1.1, delta=0.005, msg='wider contradicts smaller')
        self.assertAlmostEqual(size[2] / old[2], 0.9, delta=0.03)

    def test_a_resize_with_a_count_change_is_measured_on_one_unit(self):
        f_count = self.finding('greens', 'wrong_count', 'bigger', fid='c1')
        f_size = self.finding('greens', 'wrong_size', 'smaller', fid='s1')
        found, skipped = edits.findings_to_edits(self.design, [f_count, f_size], camera=self.camera, spec=self.view)
        self.assertEqual(sorted(e['op'] for e in found), ['resize', 'set_count'])
        new, report = edits.apply_edits(self.design, found, view=self.view)
        self.assertEqual(report['rejected'], [])
        self.assertEqual(element(new, 'greens')['count'], 3)
        view2 = edits.SpecView(self.compile(new, 'greens_three_small'), self.ids)
        kept, dropped = edits.check_resizes(self.design, self.view, view2, found, [f_count, f_size])
        self.assertEqual(dropped, [], 'one more pot must not make a valid "smaller" look bigger')
        self.assertEqual(sorted(e['op'] for e in kept), ['resize', 'set_count'])

    def test_the_up_vector_floor_applies_below_point_two(self):
        for target in ([0.0, -0.65, 0.0], [0.0, -1.95, 0.0]):
            camera, view = self.steep(target)
            up_z = camera_math.axes(camera)[2][2]
            self.assertLess(up_z, 0.2)
            box = view.box_px('screen')
            z, f = view.depth('screen'), camera_math.focal_px(camera, view.width)
            found, _ = edits.findings_to_edits(self.design, [self.finding('screen', 'misplaced', 'up', [box[0], box[1] - 4, box[2], box[3] - 4])],
                                               camera=camera, spec=view)
            want = 4 * z / f / 0.2
            self.assertLess(want, 0.5, 'the cap is not what is tested here')
            self.assertAlmostEqual(found[0]['delta_m'][2], want, delta=0.01, msg=f'up_z {up_z:.3f}: divided by 0.2, not by up_z')

    # -- P8-fix round 4: moves compare the main body too, count findings, depth, walls
    def ring_view(self, name, **changes):
        """A canopy ring with the given design changes, compiled: (design, view)."""
        design = copy.deepcopy(self.design)
        element(design, 'canopy').update(changes.pop('element', {}))
        design['form'].update(changes)
        view = edits.SpecView(self.compile(design, name), self.ids)
        return design, view

    def band_shift(self, view, dx=0.0, dy=0.0):
        band = view.box_px_raw('canopy', lambda c: c.get('role') == 'ring')
        return [band[0] + dx, band[1] + dy, band[2] + dx, band[3] + dy]

    def test_a_ring_move_compares_the_band_not_the_posts(self):
        design, view = self.ring_view('ring_z', element={'z_m': 2.4})
        self.assertIn('canopy_post_0', {c['id'] for c in view_components(view)}, 'the ring has its four posts')
        z, f = view.depth('canopy'), camera_math.focal_px(view.camera, view.width)
        up_z = camera_math.axes(view.camera)[2][2]
        for px in (6, 2):
            found, skipped = edits.findings_to_edits(design, [self.finding('canopy', 'misplaced', 'up', self.band_shift(view, dy=-px))],
                                                     camera=view.camera, spec=view)
            self.assertEqual(skipped, [], px)
            want = max(0.01, px * z / f / up_z)
            self.assertAlmostEqual(found[0]['delta_m'][2], want, delta=0.1 * want + 0.003, msg=f'{px} px up: about {want:.3f} m, not the 0.5 m cap')
            self.assertLess(found[0]['delta_m'][2], 0.1)
        found, _ = edits.findings_to_edits(design, [self.finding('canopy', 'misplaced', 'forward', self.band_shift(view, dy=4))],
                                           camera=view.camera, spec=view)
        self.assertLess(math.hypot(*found[0]['delta_m'][:2]), 0.25, '4 px forward is not the 0.5 m cap')

    def test_a_ring_without_z_m_gets_the_right_small_up_move_not_a_refusal(self):
        z, f = self.view.depth('canopy'), camera_math.focal_px(self.camera, self.view.width)
        up_z = camera_math.axes(self.camera)[2][2]
        found, skipped = self.edits_for(self.finding('canopy', 'misplaced', 'up', self.band_shift(self.view, dy=-6)))
        self.assertEqual(skipped, [], 'the 0.5 m cap would have broken the height limit')
        self.assertAlmostEqual(found[0]['delta_m'][2], 6 * z / f / up_z, delta=0.006)

    def test_a_ring_without_posts_gives_the_same_move(self):
        design, view = self.ring_view('ring_free', hanging_allowed=True)
        self.assertFalse([c for c in view_components(view) if c['id'].startswith('canopy_post')], 'hanging is allowed: no posts')
        z, f = view.depth('canopy'), camera_math.focal_px(view.camera, view.width)
        up_z = camera_math.axes(view.camera)[2][2]
        found, _ = edits.findings_to_edits(design, [self.finding('canopy', 'misplaced', 'up', self.band_shift(view, dy=-4))],
                                           camera=view.camera, spec=view)
        self.assertAlmostEqual(found[0]['delta_m'][2], 4 * z / f / up_z, delta=0.004)
        self.assertLess(found[0]['delta_m'][2], 0.05)

    def test_a_count_finding_on_the_same_element_takes_the_width_from_the_small_step(self):
        pot = self.view.box_px_raw('pot', lambda c: c.get('role') == 'planter')
        whole = self.view.box_px_raw('pot')
        cx, w = (pot[0] + pot[2]) / 2, (pot[2] - pot[0]) * 2.0                  # two pots in the picture, one in the design
        critic = [cx - w / 2, whole[1], cx + w / 2, whole[3]]
        size = edits.spec_size(element(self.design, 'pot'), self.view)
        alone, _ = self.edits_for(self.finding('pot', 'wrong_size', 'bigger', critic))
        self.assertAlmostEqual(alone[0]['size_m'][0] / size[0], 2.0, delta=0.05, msg='without a count finding the ratio is used')
        both, _ = edits.findings_to_edits(self.design, [self.finding('pot', 'wrong_count', 'bigger', fid='c'),
                                                        self.finding('pot', 'wrong_size', 'bigger', critic, fid='s')],
                                          camera=self.camera, spec=self.view)
        resize = next(e for e in both if e['op'] == 'resize')
        self.assertAlmostEqual(resize['size_m'][0] / size[0], 1.1, delta=0.005, msg='the box is wider because there are more pots')
        geometry, _ = edits.findings_to_edits(self.design, [self.finding('pot', 'wrong_count', 'bigger', fid='c'),
                                                            self.finding('pot', 'wrong_size', 'bigger', critic, fid='s')],
                                              camera=self.camera, spec=self.view, only=edits.GEOMETRY_TYPES)
        self.assertAlmostEqual(geometry[0]['size_m'][0] / size[0], 1.1, delta=0.005, msg='also in the geometry-only candidate')

    def test_a_width_ratio_changes_the_width_only_and_the_depth_takes_the_small_step(self):
        box = self.view.box_px('reception')
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        w, h = (box[2] - box[0]) * 1.5, (box[3] - box[1]) * 1.5
        found, _ = self.edits_for(self.finding('reception', 'wrong_size', 'bigger', [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]))
        old = element(self.design, 'reception')['size_m']
        size = found[0]['size_m']
        self.assertAlmostEqual(size[0] / old[0], 1.5, delta=0.04, msg='width: the ratio')
        self.assertAlmostEqual(size[1] / old[1], 1.1, delta=0.005, msg='depth: the small step, 0.605 not 0.825')
        self.assertAlmostEqual(size[2] / old[2], 1.5, delta=0.04, msg='height: its own ratio')
        hbox = self.view.box_px_raw('header')
        hc, hw = (hbox[0] + hbox[2]) / 2, (hbox[2] - hbox[0]) * 1.3
        found, _ = self.edits_for(self.finding('header', 'wrong_size', 'bigger', [hc - hw / 2, hbox[1], hc + hw / 2, hbox[3]]))
        self.assertAlmostEqual(found[0]['size_m'][1], 0.4 * 1.1, places=3, msg='header depth 0.44, not 0.52')

    def test_a_ratio_that_agrees_with_the_direction_is_used_as_it_is(self):
        box = self.view.box_px('reception')
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        w, h = (box[2] - box[0]) * 1.015, (box[3] - box[1]) * 1.0
        found, _ = self.edits_for(self.finding('reception', 'wrong_size', 'bigger', [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]))
        old = element(self.design, 'reception')['size_m']
        self.assertAlmostEqual(found[0]['size_m'][0] / old[0], 1.015, delta=0.003, msg='no dead zone near 1')
        self.assertAlmostEqual(found[0]['size_m'][2] / old[2], 1.1, delta=0.005, msg='a ratio of exactly 1 is no agreement: the small step')

    def test_a_few_millimetres_of_trimming_do_not_drop_a_resize(self):
        cam = {'projection': 'perspective', 'position': [0, -6, 1.5], 'target': [0, 0, 1.2], 'lens_mm': 35.0, 'sensor_width_mm': 36.0,
               'principal_point': [0.5, 0.5], 'pixel_aspect': [1, 1], 'fit_status': 'unfitted', 'roll_deg': 0.0}

        def spec(w, d, h):
            comp = {'id': 'panel', 'kind': 'box', 'role': 'graphic', 'location': [0, 1.9, 1.2], 'rotation_deg': [0, 0, 0], 'params': {'size': [w, d, h]}}
            return edits.SpecView({'camera': cam, 'source': {'width': 640, 'height': 400}, 'components': [comp]}, ['panel'])
        design = {'form': {'footprint_m': [6, 4], 'height_limit_m': 3.5}, 'elements': [{'id': 'panel', 'kind': 'graphic_panel', 'side': 'back'}]}
        finding = {'id': 'g', 'element_id': 'panel', 'type': 'wrong_size', 'severity': 'high', 'direction': 'bigger', 'bbox_px': None}
        edit = edits.make_edit('resize', 'panel', size_m=[1.44, 0.04, 0.96], finding_id='g')
        kept, dropped = edits.check_resizes(design, spec(1.2, 0.04, 0.8), spec(1.44, 0.038, 0.96), [edit], [finding])
        self.assertEqual((kept, dropped), ([edit], []), '2 mm of trimmed depth is not the wrong direction')
        kept, dropped = edits.check_resizes(design, spec(1.2, 0.04, 0.8), spec(1.203, 0.04, 0.8), [edit], [finding])
        self.assertEqual(kept, [], 'under 5 mm is no change')
        self.assertIn('unchanged', dropped[0]['reason'])

    def test_a_wall_element_moves_only_along_its_wall_and_not_up_or_down(self):
        right = edits.ground_axes(self.camera)[0]
        toward = edits.ground_axes(self.camera)[1]
        for eid, along_x in (('hero_wall', True), ('screen', False)):          # back wall: along x; left wall: along y
            found, skipped = self.edits_for(self.finding(eid, 'misplaced', 'forward', severity='medium'))
            dx, dy, dz = found[0]['delta_m']
            applied = dx if along_x else dy
            other = dy if along_x else dx
            self.assertEqual(other, 0.0, f'{eid}: nothing is recorded that was not done')
            self.assertAlmostEqual(applied, 0.05 * (toward[0] if along_x else toward[1]), places=4)
        self.assertTrue(self.view.parts.get('shell1'), 'the shell wall is compiled (it is the perimeter wall)')
        for direction in ('up', 'down'):      # the shell wall kind: the compiler ignores its z_m
            found, skipped = self.edits_for(self.finding('shell1', 'misplaced', direction))
            self.assertEqual(found, [], f'wall {direction}: no empty candidate')
            self.assertIn('wall', skipped[0]['reason'])
            self.assertIn('up or down', skipped[0]['reason'])
        found, _ = self.edits_for(self.finding('hero_wall', 'misplaced', 'right', severity='medium'))
        self.assertAlmostEqual(found[0]['delta_m'][0], 0.05 * right[0], places=4)

    def test_a_wall_mounted_tv_and_graphic_panel_still_move_up_and_down_from_their_real_height(self):
        for eid, direction, sign in (('screen', 'up', 1), ('poster', 'down', -1)):
            with self.subTest(eid=eid):
                self.assertNotIn('z_m', element(self.design, eid))
                found, skipped = self.edits_for(self.finding(eid, 'misplaced', direction, severity='low'))
                self.assertEqual(skipped, [])
                self.assertAlmostEqual(found[0]['delta_m'][2], sign * 0.05, places=4, msg='delta_m is the real move in metres')
                self.assertEqual(found[0]['delta_m'][:2], [0.0, 0.0])
                new = edits.apply_edit(self.design, found[0], view=self.view)
                self.assertAlmostEqual(element(new, eid)['z_m'], self.view.bottom_rel(eid) + sign * 0.05, places=3,
                                       msg='from the real height, not from 0')
                view2 = edits.SpecView(self.compile(new, f'wall_{eid}'), self.ids)
                self.assertAlmostEqual(view2.bottom(eid) - self.view.bottom(eid), sign * 0.05, delta=0.005,
                                       msg='the recompiled element moved the asked way')

    # -- P8-fix round 5: every geometry edit is measured after the recompile
    def measured(self, findings, name):
        """Findings -> edits -> candidate design -> compile -> check_geometry. -> (kept, dropped, edits, new design, view of the candidate)."""
        found, skipped = edits.findings_to_edits(self.design, findings, camera=self.camera, spec=self.view)
        new, report = edits.apply_edits(self.design, found, view=self.view)
        view2 = edits.SpecView(self.compile(new, name), self.ids)
        kept, dropped = edits.check_geometry(self.design, self.view, view2, report['applied'], findings)
        return kept, dropped, report['applied'], new, view2

    def test_a_shell_wall_move_has_no_effect_and_is_dropped(self):
        for n, direction in enumerate(('forward', 'left')):
            f = self.finding('shell1', 'misplaced', direction, fid=f'w{n}')
            kept, dropped, proposed, _, _ = self.measured([f], f'shell_{direction}')
            self.assertTrue(proposed, 'the fixer proposes it (it cannot know) ...')
            self.assertEqual(kept, [], f'{direction}: ... and the measurement drops it: no empty candidate')
            self.assertIn('no effect', dropped[0]['reason'])
            self.assertEqual(dropped[0]['finding_id'], f'w{n}')

    def test_a_wall_panel_lifted_over_the_wall_top_is_lost_and_dropped(self):
        f = self.finding('banner', 'misplaced', 'up', fid='b1')                 # 2.8 m + 0.1 m (high severity, no box): the compiler drops it
        kept, dropped, proposed, new, view2 = self.measured([f], 'banner_up')
        self.assertEqual(proposed[0]['op'], 'move')
        self.assertFalse(view2.parts.get('banner'), 'the compiler built nothing for it')
        self.assertEqual(kept, [])
        self.assertIn('element lost', dropped[0]['reason'])

    def test_an_along_wall_move_records_what_the_compiler_built(self):
        f = self.finding('led', 'misplaced', 'forward', severity='medium', fid='l1')
        kept, dropped, proposed, new, view2 = self.measured([f], 'led_forward')
        self.assertEqual(dropped, [])
        old, now = edits.main_body_box(self.view, element(self.design, 'led')), edits.main_body_box(view2, element(new, 'led'))
        want = [round((now[0][i] + now[1][i]) / 2 - (old[0][i] + old[1][i]) / 2, 4) for i in (0, 1)] + [round(now[0][2] - old[0][2], 4)]
        self.assertEqual(kept[0]['delta_m'], [w + 0.0 for w in want], 'the measured change of the main body, horizontal by its centre')
        self.assertEqual(kept[0]['op'], 'move')

    def test_a_move_that_works_is_kept_with_the_measured_change(self):
        f = self.finding('reception', 'misplaced', 'right', severity='medium', fid='r1')
        kept, dropped, proposed, new, view2 = self.measured([f], 'reception_right')
        self.assertEqual(dropped, [])
        self.assertAlmostEqual(math.hypot(*kept[0]['delta_m'][:2]), 0.05, delta=0.005)
        self.assertEqual(kept[0]['finding_id'], 'r1')
        # a vertical move records the change of the bottom
        f = self.finding('screen', 'misplaced', 'up', severity='low', fid='s1')
        kept, dropped, _, _, _ = self.measured([f], 'screen_up_measured')
        self.assertEqual(dropped, [])
        self.assertAlmostEqual(kept[0]['delta_m'][2], 0.05, delta=0.005)

    def test_a_count_change_that_builds_nothing_new_is_dropped_and_one_that_does_is_kept(self):
        f = self.finding('greens', 'wrong_count', 'bigger', fid='c1')
        kept, dropped, _, _, _ = self.measured([f], 'greens_more')
        self.assertEqual((len(kept), dropped), (1, []))
        view_same = self.view
        edit = edits.make_edit('set_count', 'greens', delta_m=[1, 0, 0], finding_id='c1')
        kept, dropped = edits.check_geometry(self.design, self.view, view_same, [edit], [f])
        self.assertEqual(kept, [])
        self.assertIn('no effect', dropped[0]['reason'])

    def test_exactly_one_is_judged_with_a_tolerance_of_1e_minus_9(self):
        self.assertFalse(edits.ratio_agrees(0.9999999999999997, 1))
        self.assertFalse(edits.ratio_agrees(1.0000000000000004, 1))
        self.assertFalse(edits.ratio_agrees(1.0000000000000004, -1))
        self.assertTrue(edits.ratio_agrees(1.015, 1))
        self.assertTrue(edits.ratio_agrees(0.985, -1))
        self.assertFalse(edits.ratio_agrees(0.985, 1))

    # -- P8-fix round 6: each geometry edit is measured on its own
    def measure(self, findings, name):
        """Findings -> edits -> measure_edits with the real design-compile (one compile per geometry edit).
        -> (edits, survivors, recorded {finding_id: edit}, dropped, number of compiles)."""
        found, _ = edits.findings_to_edits(self.design, findings, camera=self.camera, spec=self.view)
        calls = [0]

        def compile_fn(design):
            calls[0] += 1
            return self.compile(design, f'{name}_{calls[0]}')
        survivors, recorded, dropped = edits.measure_edits(self.design, self.view, found, findings, compile_fn)
        return found, survivors, recorded, dropped, calls[0]

    def alone(self, edit, name):
        """The measured horizontal centre shift and bottom change of one edit applied alone (computed here, the long way)."""
        new = edits.apply_edit(self.design, edit, view=self.view)
        el_old, el_new = edits.find_element(self.design, edit['element_id']), edits.find_element(new, edit['element_id'])
        old, now = edits.main_body_box(self.view, el_old), edits.main_body_box(edits.SpecView(self.compile(new, name), self.ids), el_new)
        return [round((now[0][i] + now[1][i]) / 2 - (old[0][i] + old[1][i]) / 2, 4) + 0.0 for i in (0, 1)] + [round(now[0][2] - old[0][2], 4) + 0.0]

    def test_a_move_beside_a_resize_that_snaps_records_its_own_measured_change(self):
        move = self.finding('led', 'misplaced', 'forward', severity='medium', fid='m1')
        size = self.finding('led', 'wrong_size', 'bigger', fid='s1')           # the 3 x 2 modules snap back: no effect
        found, survivors, recorded, dropped, compiles = self.measure([move, size], 'led_pair')
        self.assertEqual(compiles, 2, 'one compile for each geometry edit')
        self.assertEqual([d['finding_id'] for d in dropped], ['s1'])
        self.assertIn('no effect', dropped[0]['reason'])
        self.assertEqual([e['finding_id'] for e in survivors], ['m1'])
        predicted = next(e for e in found if e['finding_id'] == 'm1')['delta_m']
        self.assertEqual(recorded['m1']['delta_m'], self.alone(predicted_edit(found, 'm1'), 'led_alone'), 'the change of the move alone')
        self.assertNotEqual(recorded['m1']['delta_m'], predicted, 'not the predicted value')

    def test_a_no_effect_move_does_not_survive_beside_a_resize_that_changes_the_box(self):
        move = self.finding('shell1', 'misplaced', 'forward', fid='m1')
        size = self.finding('shell1', 'wrong_size', 'bigger', fid='s1')
        found, survivors, recorded, dropped, compiles = self.measure([move, size], 'shell_pair')
        self.assertIn('m1', [d['finding_id'] for d in dropped], 'the compiler does not read a shell wall move')
        self.assertNotIn('m1', recorded)
        self.assertNotIn('m1', [e['finding_id'] for e in survivors])
        self.assertIn('no effect', next(d for d in dropped if d['finding_id'] == 'm1')['reason'])

    def test_a_count_change_is_judged_by_the_number_of_built_units(self):
        for eid, direction, name in (('downlights', 'bigger', 'dl_5'), ('downlights', 'smaller', 'dl_3'), ('meeting', 'bigger', 'chairs_3'),
                                     ('greens', 'bigger', 'pots_3'), ('greens', 'smaller', 'pots_1')):
            with self.subTest(element=eid, direction=direction):
                f = self.finding(eid, 'wrong_count', direction, fid='c1')
                found, survivors, recorded, dropped, _ = self.measure([f], name)
                self.assertEqual(dropped, [], 'the units changed as asked (the box of a light row does not change)')
                self.assertEqual([e['op'] for e in survivors], ['set_count'])
        units = edits.built_units
        el = element(self.design, 'downlights')
        self.assertEqual(units(self.view, el), 4)
        self.assertEqual(units(self.view, element(self.design, 'greens')), 2)
        self.assertEqual(units(self.view, element(self.design, 'meeting')), 2, 'chairs round the table')

    def test_a_count_the_compiler_does_not_read_has_no_effect(self):
        f = self.finding('reception', 'wrong_count', 'bigger', fid='c1')                # a counter has no count
        found, survivors, recorded, dropped, _ = self.measure([f], 'counter_count')
        self.assertEqual(survivors, [])
        self.assertIn('no effect', dropped[0]['reason'])

    # -- P8-fix2: the move compares the same part, a floor-standing piece is not lifted
    def far(self, spec=None, ids=None, factor=1.8):
        """The same compiled spec seen from farther away (everything smaller, so that a box can grow by half without leaving the picture):
        -> (camera, view)."""
        spec = copy.deepcopy(spec or self.spec)
        cam = spec['camera']
        eye = [t + factor * (p - t) for p, t in zip(cam['position'], cam['target'])]
        spec['camera'] = dict(cam, position=eye)
        return spec['camera'], edits.SpecView(spec, ids or self.ids)

    @staticmethod
    def shifted(box, dx=0.0, dy=0.0):
        return [box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy]

    @staticmethod
    def toward_dot(delta, camera):
        """Positive when a floor move goes towards the camera."""
        toward = edits.ground_axes(camera)[1]
        return delta[0] * toward[0] + delta[1] * toward[1]

    def move_edit(self, design, view, camera, finding):
        found, skipped = edits.findings_to_edits(design, [finding], camera=camera, spec=view)
        return found, skipped

    def test_a_table_move_compares_the_top_with_a_top_only_box(self):
        camera, view = self.far()
        top = view.box_px_raw('table1', lambda c: str(c.get('id', '')).endswith('_top'))
        whole = view.box_px_raw('table1')
        self.assertGreater(whole[3] - whole[1], 2.0 * (top[3] - top[1]), 'the stem and the base make the whole table much taller than its top')
        per_m = (view.project([view.centroid('table1')[0] + 0.1 * edits.ground_axes(camera)[1][0],
                               view.centroid('table1')[1] + 0.1 * edits.ground_axes(camera)[1][1], view.centroid('table1')[2]])[1]
                 - view.project(view.centroid('table1'))[1]) / 0.1               # pixels down the picture per metre towards the camera
        self.assertGreater(per_m, 2.0)

        # the critic boxed the top, 6 px lower than the render's top, and said "back" (away from the camera = up the picture): against
        # the direction, so the small step (medium: 0.05 m), and certainly not the 0.5 m that the whole table box gave
        found, skipped = self.move_edit(self.design, view, camera, self.finding('table1', 'misplaced', 'back', self.shifted(top, dy=6.0), severity='medium'))
        self.assertEqual(skipped, [])
        self.assertLessEqual(math.hypot(*found[0]['delta_m'][:2]), 0.06)
        self.assertLess(self.toward_dot(found[0]['delta_m'], camera), 0.0, 'back is away from the camera')

        # the same top box 2 px higher agrees with "back": the move is the 2 px worth of floor (a pixel is several centimetres here)
        found, skipped = self.move_edit(self.design, view, camera, self.finding('table1', 'misplaced', 'back', self.shifted(top, dy=-2.0)))
        self.assertEqual(skipped, [])
        self.assertAlmostEqual(math.hypot(*found[0]['delta_m'][:2]), 2.0 / per_m, delta=0.12 * 2.0 / per_m)
        self.assertLess(self.toward_dot(found[0]['delta_m'], camera), 0.0)

        # a critic who boxed the whole table (top, stem and base) is compared with the whole table, not with the top alone
        found, skipped = self.move_edit(self.design, view, camera, self.finding('table1', 'misplaced', 'forward', self.shifted(whole, dy=2.0)))
        self.assertEqual(skipped, [])
        self.assertAlmostEqual(math.hypot(*found[0]['delta_m'][:2]), 2.0 / per_m, delta=0.12 * 2.0 / per_m,
                               msg='a box three times taller than the top is a box of the whole table')
        self.assertGreater(self.toward_dot(found[0]['delta_m'], camera), 0.0)

        # a planter boxed by its pot alone moves as usual, whatever the plants make of the whole box
        pot = view.box_px_raw('pot', lambda c: c.get('role') == 'planter')
        z, f = view.depth('pot'), camera_math.focal_px(camera, view.width)
        found, skipped = self.move_edit(self.design, view, camera, self.finding('pot', 'misplaced', 'left', self.shifted(pot, dx=-20.0)))
        self.assertEqual(skipped, [])
        right = edits.ground_axes(camera)[0]
        self.assertLess(found[0]['delta_m'][0] * right[0] + found[0]['delta_m'][1] * right[1], 0.0, 'to the left of the picture')
        self.assertAlmostEqual(math.hypot(*found[0]['delta_m'][:2]), 20 * z / f, delta=0.4 * 20 * z / f)

    def test_a_box_much_wider_than_the_body_is_a_size_problem_not_a_move(self):
        camera, view = self.far()
        band = view.box_px_raw('canopy', lambda c: c.get('role') == 'ring')
        cx, w = (band[0] + band[2]) / 2, (band[2] - band[0]) * 1.5
        wide = [cx - w / 2, band[1], cx + w / 2, band[3]]               # 1.5 times as wide, the same centre
        self.assertGreater(wide[0], 1.0)
        self.assertLess(wide[2], view.width - 1.0)
        found, skipped = self.move_edit(self.design, view, camera, self.finding('canopy', 'misplaced', 'left', wide))
        self.assertEqual(found, [], 'no edit: the picture shows a bigger ring, not a ring somewhere else')
        self.assertEqual([s['finding_id'] for s in skipped], ['f1'])
        self.assertIn('size', skipped[0]['reason'])
        self.assertIn('not a position problem', skipped[0]['reason'])
        # the same ring boxed at its own width, 8 px to the left, is a move
        found, skipped = self.move_edit(self.design, view, camera, self.finding('canopy', 'misplaced', 'left', self.shifted(band, dx=-8.0)))
        self.assertEqual(skipped, [])
        self.assertTrue(found and found[0]['op'] == 'move')
        # a box 1.3 times as wide is inside the range [0.7, 1.43]: still a move
        w2 = (band[2] - band[0]) * 1.3
        found, skipped = self.move_edit(self.design, view, camera, self.finding('canopy', 'misplaced', 'left', [cx - w2 / 2, band[1], cx + w2 / 2, band[3]]))
        self.assertEqual(skipped, [])
        self.assertTrue(found and found[0]['op'] == 'move')
        # a box only half as wide: the other side of the range
        w3 = (band[2] - band[0]) * 0.5
        found, skipped = self.move_edit(self.design, view, camera, self.finding('canopy', 'misplaced', 'left', [cx - w3 / 2, band[1], cx + w3 / 2, band[3]]))
        self.assertEqual(found, [])
        self.assertIn('size', skipped[0]['reason'])

    def test_a_kind_other_than_a_table_or_a_planter_has_no_second_move_body(self):
        camera, view = self.far()
        band = view.box_px_raw('canopy', lambda c: c.get('role') == 'ring')
        whole = view.box_px_raw('canopy')
        self.assertGreater(whole[3] - whole[1], 2.0 * (band[3] - band[1]), 'the posts make the whole ring much taller than its band')
        # a critic who boxed the ring with its four posts is not comparing the band: a size problem, not a move, whatever the whole
        # ring (band and posts) would give
        found, skipped = self.move_edit(self.design, view, camera, self.finding('canopy', 'misplaced', 'forward', self.shifted(whole, dy=3.0)))
        self.assertEqual(found, [])
        self.assertIn('not a position problem', skipped[0]['reason'])
        # the band alone is fine
        found, skipped = self.move_edit(self.design, view, camera, self.finding('canopy', 'misplaced', 'forward', self.shifted(band, dy=3.0)))
        self.assertEqual(skipped, [])
        self.assertTrue(found)
        # a table and a planter keep their second body
        self.assertEqual([label for label, _ in edits.move_bodies(element(self.design, 'table1'))], ['top', 'whole table'])
        self.assertEqual([label for label, _ in edits.move_bodies(element(self.design, 'pot'))], ['pot and plants', 'pot'])
        for eid in ('canopy', 'desk', 'poster', 'reception', 'header'):
            self.assertEqual(len(edits.move_bodies(element(self.design, eid))), 1, eid)

    def test_the_move_length_ratio_range_is_zero_point_seven_to_one_point_four_three(self):
        self.assertEqual(edits.MOVE_RATIO_RANGE, (0.7, 1.43))

    def test_a_floor_standing_table_is_not_moved_up_or_down(self):
        self.assertNotIn('z_m', element(self.design, 'table1'))
        self.assertLess(self.view.bottom_rel('table1'), 0.01, 'the compiled table stands on the platform')
        box = self.view.box_px('table1')
        for direction in ('up', 'down'):
            for bbox in (None, self.shifted(box, dy=-12.0 if direction == 'up' else 12.0)):
                with self.subTest(direction=direction, boxed=bbox is not None):
                    found, skipped = self.edits_for(self.finding('table1', 'misplaced', direction, bbox))
                    self.assertEqual(found, [])
                    self.assertIn('floor-standing', skipped[0]['reason'])
                    self.assertIn('not moved up or down', skipped[0]['reason'])
        for eid in ('desk', 'pot'):         # a counter and a planter without z_m stand on the floor too
            found, skipped = self.edits_for(self.finding(eid, 'misplaced', 'up'))
            self.assertEqual(found, [], eid)
            self.assertIn('floor-standing', skipped[0]['reason'])
        # a wall panel without z_m still goes up, from its real height
        found, skipped = self.edits_for(self.finding('poster', 'misplaced', 'up', severity='low'))
        self.assertEqual(skipped, [])
        self.assertAlmostEqual(found[0]['delta_m'][2], 0.05, places=4)
        # a counter with a z_m is a raised counter: allowed
        design = copy.deepcopy(self.design)
        design['elements'].append({'id': 'shelf', 'kind': 'counter', 'side': 'centre', 'centre_m': [0.3, -1.6], 'z_m': 0.4,
                                   'size_m': [1.0, 0.5, 0.8]})
        ids = [e['id'] for e in design['elements']]
        view = edits.SpecView(self.compile(design, 'with_shelf'), ids)
        found, skipped = edits.findings_to_edits(design, [self.finding('shelf', 'misplaced', 'up', severity='low')], camera=self.camera, spec=view)
        self.assertEqual(skipped, [])
        self.assertAlmostEqual(found[0]['delta_m'][2], 0.05, places=4)
        # without the compiled spec nothing is known about where it stands: the old reason (no height to start from) still holds
        found, skipped = edits.findings_to_edits(self.design, [self.finding('table1', 'misplaced', 'up')], camera=self.camera)
        self.assertEqual(found, [])
        self.assertIn('spec', skipped[0]['reason'])


class FrameCase(unittest.TestCase):
    """The reader's frame check (02 section 4.1): a design the reader turned by a quarter turn is turned back before the first compile."""
    CAMERA = {'projection': 'perspective', 'position': [8.5, -9.5, 2.6], 'target': [0.0, 0.0, 0.9], 'lens_mm': 28.0, 'sensor_width_mm': 36.0,
              'principal_point': [0.5, 0.5], 'roll_deg': 0.0, 'pixel_aspect': [1, 1]}
    SIZE = (1280, 800)

    @staticmethod
    def compile_fn(design):
        with tempfile.TemporaryDirectory() as tmp:
            spec, report = design_compile.compile_design(copy.deepcopy(design), tmp)
        if report.get('status') == 'invalid':
            raise ValueError('the design does not compile')
        return spec

    @staticmethod
    def scene():
        """A 6 x 6 m stand open to the front with one wall at the back, two counters, a planter and a round table (all different)."""
        d = base_design()
        d['form']['footprint_m'] = [6.0, 6.0]
        d['requirements'] = [r for r in d['requirements'] if r['id'] == 'r1']
        d['elements'] = [
            {'id': 'back_wall', 'kind': 'feature_wall', 'hero': True, 'side': 'back', 'along': 0.5, 'size_m': [4.2, 0.08, None]},
            {'id': 'desk_a', 'kind': 'counter', 'side': 'front', 'along': 0.2, 'offset_m': 0.6, 'size_m': [1.8, 0.6, 1.05]},
            {'id': 'desk_b', 'kind': 'counter', 'side': 'left', 'along': 0.7, 'offset_m': 0.4, 'size_m': [1.5, 0.6, 1.05]},
            {'id': 'pot', 'kind': 'planter', 'side': 'centre', 'centre_m': [1.6, -1.2], 'yaw_deg': 0.0},
            {'id': 'table', 'kind': 'round_table', 'side': 'centre', 'centre_m': [-1.2, 0.8], 'params': {'radius_m': 0.6}},
        ]
        return d

    def projected_evidence(self, design, spec=None, only=None):
        """The picture boxes of the main bodies of the design, as a reader who saw the truth would draw them."""
        spec = spec or self.compile_fn(design)
        view = edits.SpecView(spec, [e['id'] for e in design['elements']])
        view.camera, (view.width, view.height) = self.CAMERA, self.SIZE
        out = {}
        for el in design['elements']:
            if only is not None and el['id'] not in only:
                continue
            raw = view.box_px_raw(el['id'], edits.main_body(el))
            box = [max(0.0, raw[0]), max(0.0, raw[1]), min(float(self.SIZE[0]), raw[2]), min(float(self.SIZE[1]), raw[3])]
            out[el['id']] = {'bbox_px': [round(v, 1) for v in box], 'confidence': 'high'}
        return out

    def centres(self, design):
        """{element_id: sorted centres (x, y) of the bounding boxes of its compiled parts}."""
        spec = self.compile_fn(design)
        view = edits.SpecView(spec, [e['id'] for e in design['elements']])
        out = {}
        for eid, parts in view.parts.items():
            out[eid] = sorted((round((min(p[0] for p in pts) + max(p[0] for p in pts)) / 2, 4),
                               round((min(p[1] for p in pts) + max(p[1] for p in pts)) / 2, 4))
                              for comp, pts in parts if comp.get('role') not in ('sign_text', 'grass_blade'))   # letters stay on a sign's first edge; blades are random
        return out

    def assert_same_places(self, got, want, tol=0.01, msg=''):
        self.assertEqual(sorted(got), sorted(want), msg)
        for eid in want:
            left = list(got[eid])
            for wx, wy in want[eid]:
                near = [c for c in left if math.hypot(c[0] - wx, c[1] - wy) <= tol]
                self.assertTrue(near, f'{msg} {eid}: nothing near ({wx:.3f}, {wy:.3f}); parts at {left[:6]}')
                left.remove(near[0])

    @staticmethod
    def rich():
        """A 6 x 6 stand with every kind of side word the rotation has to rename (walls, floor pieces by side and by centre, a header,
        lights, a canopy with bands and a sign, entrance, requirement)."""
        d = base_design()
        d['form']['footprint_m'] = [6.0, 6.0]
        d['layout']['entrance_side'] = 'front'
        d['elements'] = [
            {'id': 'wall_back', 'kind': 'feature_wall', 'side': 'back', 'along': 0.3, 'size_m': [3.0, 0.08, None]},
            {'id': 'tv_left', 'kind': 'tv', 'side': 'left', 'along': 0.7, 'params': {'inch': 55}},
            {'id': 'panel_right', 'kind': 'graphic_panel', 'side': 'right', 'along': 0.2, 'size_m': [1.2, 0.04, 0.8], 'z_m': 1.0},
            {'id': 'header', 'kind': 'header_band', 'size_m': [None, 0.4, 0.5], 'params': {'sides': ['front', 'right']},
             'text': {'body': 'DEMO COFFEE', 'size_m': 0.2}, 'provenance': {'text': 'brief'}},
            {'id': 'counter_f', 'kind': 'counter', 'side': 'front', 'along': 0.78, 'offset_m': 0.5, 'size_m': [1.8, 0.55, 1.05]},
            {'id': 'plants', 'kind': 'planter', 'side': 'right', 'along': 0.75, 'count': 2, 'pitch_m': 0.9},
            {'id': 'lights', 'kind': 'spotlights', 'side': 'front', 'count': 4},
            {'id': 'seats', 'kind': 'seating_group', 'side': 'centre', 'centre_m': [-1.6, 0.2], 'count': 2, 'yaw_deg': 30.0},
            {'id': 'stand', 'kind': 'display_plinth', 'side': 'centre', 'centre_m': [1.4, 0.9], 'yaw_deg': 0.0, 'size_m': [0.6, 0.4, 1.0]},
            {'id': 'table', 'kind': 'round_table', 'side': 'centre', 'centre_m': [0.5, -1.3], 'params': {'radius_m': 0.5}},
            {'id': 'lid', 'kind': 'canopy_frame', 'centre_m': [0.3, 0.2], 'size_m': [4.4, 4.0, 0.3],
             'params': {'band_m': {'front': 0.3, 'back': 0.5, 'left': 0.4, 'right': 0.6},
                        'sign': {'corner': 'front_left', 'along_m': [2.0, 1.5], 'drop_m': 0.2}}},
        ]
        return d

    def test_four_quarter_turns_give_back_the_same_design(self):
        d = self.rich()
        before = copy.deepcopy(d)
        once = edits.rotate_design(d, 1)
        self.assertEqual(d, before, 'the input is not changed')
        self.assertNotEqual(once, d)
        self.assertEqual(edits.validate_design(once), [])
        twice = edits.rotate_design(once, 1)
        self.assertEqual(edits.rotate_design(twice, 1), edits.rotate_design(d, 3))
        self.assertEqual(edits.rotate_design(edits.rotate_design(twice, 1), 1), d)
        for k in (1, 2, 3, 5, -1):
            with self.subTest(turns=k):
                e = edits.rotate_design(d, k)
                self.assertEqual(edits.validate_design(e), [])
        self.assertEqual(edits.rotate_design(d, 4), d)
        self.assertEqual(edits.rotate_design(d, 0), d)
        self.assertEqual(edits.rotate_design(d, -1), edits.rotate_design(d, 3), 'a quarter turn the other way is three the usual way')
        # what a quarter turn (counter-clockwise, as yaw) does to the side words and the along of the elements
        el = {e['id']: e for e in once['elements']}
        self.assertEqual((el['wall_back']['side'], el['wall_back']['along']), ('left', 0.3))
        self.assertEqual((el['tv_left']['side'], round(el['tv_left']['along'], 6)), ('front', 0.3), 'the left wall seen from the front runs the other way')
        self.assertEqual((el['panel_right']['side'], round(el['panel_right']['along'], 6)), ('back', 0.8))
        self.assertEqual((el['counter_f']['side'], el['counter_f']['along'], el['counter_f']['offset_m']), ('right', 0.78, 0.5))
        self.assertEqual(el['header']['params']['sides'], ['right', 'back'])
        self.assertEqual(el['lights']['side'], 'right')
        self.assertEqual(once['form']['open_sides'], ['right'])
        self.assertEqual(once['layout']['entrance_side'], 'right')
        self.assertEqual(once['requirements'][0]['side'], 'left')
        self.assertEqual(el['seats']['centre_m'], [-0.2, -1.6])
        self.assertEqual(el['seats']['yaw_deg'], 120.0)
        self.assertEqual(el['lid']['centre_m'], [-0.2, 0.3])
        self.assertEqual(el['lid']['size_m'], [4.0, 4.4, 0.3], 'width and depth of the frame change places')
        self.assertEqual(el['lid']['params']['band_m'], {'right': 0.3, 'left': 0.5, 'front': 0.4, 'back': 0.6})
        self.assertEqual(el['lid']['params']['sign']['corner'], 'front_right')
        self.assertEqual(el['lid']['params']['sign']['along_m'], [1.5, 2.0])
        # a piece with a centre and no yaw gets one when the quarter turn changes the way it lies
        bare = copy.deepcopy(d)
        next(e for e in bare['elements'] if e['id'] == 'stand').pop('yaw_deg')
        stand = next(e for e in edits.rotate_design(bare, 1)['elements'] if e['id'] == 'stand')
        self.assertEqual(stand['centre_m'], [-0.9, 1.4])
        self.assertAlmostEqual(abs(stand['yaw_deg']) % 180, 90.0)

    def test_a_quarter_turn_moves_every_component_centre_with_the_frame(self):
        d = self.rich()
        d['elements'] = [e for e in d['elements'] if e['id'] != 'header']       # the compiler cuts a two-sided header at a corner, not turn-proof
        base = self.centres(d)
        self.assertGreater(len(base['lights']), 3, 'the spotlights are built')
        turned = self.centres(edits.rotate_design(d, 1))
        want = {eid: [(-y, x) for x, y in cs] for eid, cs in base.items()}
        self.assert_same_places(turned, want, msg='a quarter turn counter-clockwise:')
        half = self.centres(edits.rotate_design(d, 2))
        self.assert_same_places(half, {eid: [(-x, -y) for x, y in cs] for eid, cs in base.items()}, msg='a half turn:')

    def read_a_quarter_off(self, off=-1):
        truth = self.scene()
        read = edits.rotate_design(truth, off)
        return truth, read, self.projected_evidence(truth)

    def test_frame_check_turns_back_a_design_read_a_quarter_turn_off(self):
        truth, read, evidence = self.read_a_quarter_off(-1)
        calls = []

        def compile_fn(design):
            calls.append(1)
            return self.compile_fn(design)
        lines = []
        fixed, report = edits.frame_check(read, evidence, compile_fn, camera=self.CAMERA, size=self.SIZE, log=lines.append)
        self.assertEqual(len(calls), 4, 'each of the four turns is compiled once')
        self.assertEqual((report['decision'], report['quarter_turns']), ('turn', 1))
        by_turn = {r['degrees']: r for r in report['rotations']}
        self.assertEqual(sorted(by_turn), [0, 90, 180, 270])
        self.assertLess(by_turn[0]['mean_iou'], 0.3, 'as read: the walls are on the wrong sides')
        self.assertGreater(by_turn[90]['mean_iou'], 0.99, 'turned back: the boxes are the truth')
        self.assertTrue(all(r['compiled'] for r in report['rotations']))
        self.assertTrue(report['reason'])
        self.assertTrue(lines)
        self.assert_same_places(self.centres(fixed), self.centres(truth), msg='after the turn back:')
        self.assertEqual(fixed['form']['footprint_m'], read['form']['footprint_m'])
        # the other three quarters are found as well
        for off, expect in ((1, 3), (2, 2)):
            truth, read, evidence = self.read_a_quarter_off(off)
            fixed, report = edits.frame_check(read, evidence, self.compile_fn, camera=self.CAMERA, size=self.SIZE)
            self.assertEqual((report['decision'], report['quarter_turns']), ('turn', expect), off)
            self.assert_same_places(self.centres(fixed), self.centres(truth), msg=f'off by {off}:')

    def test_frame_check_keeps_a_design_whose_frame_is_right(self):
        truth = self.scene()
        evidence = self.projected_evidence(truth)
        fixed, report = edits.frame_check(truth, evidence, self.compile_fn, camera=self.CAMERA, size=self.SIZE)
        self.assertEqual((report['decision'], report['quarter_turns']), ('keep', 0))
        self.assertEqual(fixed, truth)
        by_turn = {r['degrees']: r['mean_iou'] for r in report['rotations']}
        self.assertGreater(by_turn[0], 0.99)
        self.assertTrue(all(by_turn[k] < by_turn[0] for k in (90, 180, 270)))
        # a frame that is only a little off still passes: the original is not below 0.3
        evidence['pot']['bbox_px'] = [v + 6 for v in evidence['pot']['bbox_px']]
        fixed, report = edits.frame_check(truth, evidence, self.compile_fn, camera=self.CAMERA, size=self.SIZE)
        self.assertEqual(report['decision'], 'keep')
        self.assertEqual(fixed, truth)

    def test_the_three_gates_of_the_frame_check_are_zero_point_three_zero_point_five_and_zero_point_three(self):
        decide = edits.decide_frame
        turned = lambda ious: decide(ious)[0]            # noqa: E731
        self.assertEqual(turned({0: 0.10, 1: 0.80, 2: 0.05, 3: 0.02}), 1)
        self.assertEqual(turned({0: 0.299, 1: 0.60, 2: 0.0, 3: 0.0}), 1)
        self.assertEqual(turned({0: 0.30, 1: 0.90, 2: 0.0, 3: 0.0}), 0, 'the original is not below 0.3')
        self.assertEqual(turned({0: 0.20, 1: 0.499, 2: 0.0, 3: 0.0}), 0, 'the best is below 0.5 (the gain is below 0.3 too)')
        self.assertEqual(turned({0: 0.10, 1: 0.45, 2: 0.0, 3: 0.0}), 0, 'only the best gate stops this: gain 0.35, original 0.1, best 0.45')
        self.assertEqual(turned({0: 0.0, 1: 0.31, 2: 0.0, 3: 0.0}), 0, 'only the best gate stops this: gain 0.31, original 0')
        self.assertEqual(turned({0: 0.0, 1: 0.499, 2: 0.0, 3: 0.0}), 0, 'only the best gate stops this: just under 0.5')
        self.assertEqual(turned({0: 0.0, 1: 0.50, 2: 0.0, 3: 0.0}), 1, 'exactly 0.5 passes')
        self.assertIn('0.499', decide(({0: 0.0, 1: 0.499, 2: 0.0, 3: 0.0}))[1], 'the reason never says 0.50 for a best that is below 0.5')
        self.assertEqual(turned({0: 0.20, 1: 0.50, 2: 0.0, 3: 0.0}), 1, 'a best of exactly 0.5 and a gain of exactly 0.3 pass')
        self.assertEqual(turned({0: 0.21, 1: 0.50, 2: 0.0, 3: 0.0}), 0, 'a gain of 0.29 is not enough')
        self.assertEqual(turned({0: 0.25, 1: 0.50, 2: 0.0, 3: 0.0}), 0, 'a gain of 0.25 is not enough')
        self.assertEqual(turned({0: 0.0, 1: 0.55, 2: 0.0, 3: 0.60}), 3, 'the best of the turns')
        self.assertEqual(turned({0: 0.0, 1: 0.60, 2: 0.60, 3: 0.0}), 1, 'a tie: the smaller turn')
        self.assertEqual(turned({0: None, 1: 0.9, 2: 0.0, 3: 0.0}), 1, 'the design as read does not compile: its IoU counts as 0')
        self.assertEqual(turned({0: None, 1: 0.499, 2: 0.0, 3: 0.0}), 0, 'a design that cannot be built still needs a turn of at least 0.5')
        self.assertEqual(turned({0: None, 1: 0.5, 2: 0.2, 3: None}), 1, 'a best of exactly 0.5 passes the gain of 0.3 over 0')
        self.assertEqual(turned({0: None, 1: None, 2: None, 3: None}), 0, 'nothing compiles: nothing to do')
        self.assertEqual(turned({0: 0.1, 1: None, 2: None, 3: 0.7}), 3, 'turns that do not compile are skipped')
        self.assertEqual(turned({0: 0.1, 1: None, 2: None, 3: None}), 0)
        quarter, reason = decide({0: 0.10, 1: 0.80, 2: 0.05, 3: 0.02})
        self.assertTrue(reason)

    def test_frame_check_keeps_a_symmetric_layout_and_an_unverified_one(self):
        # a layout that looks the same from every quarter: nothing is clearly better, nothing is changed
        d = self.scene()
        d['elements'] = [{'id': 'table', 'kind': 'round_table', 'side': 'centre', 'centre_m': [0.0, 0.0], 'params': {'radius_m': 0.8}},
                         {'id': 'ring', 'kind': 'ring', 'params': {'inset_m': 0.6}}]
        evidence = self.projected_evidence(d)
        fixed, report = edits.frame_check(d, evidence, self.compile_fn, camera=self.CAMERA, size=self.SIZE)
        self.assertEqual((report['decision'], report['quarter_turns']), ('keep', 0))
        self.assertEqual(fixed, d)
        ious = [r['mean_iou'] for r in report['rotations']]
        self.assertAlmostEqual(max(ious), min(ious), places=6, msg='all four turns give the same picture')
        # fewer than half of the elements have evidence: the frame is not verified
        truth = self.scene()
        evidence = self.projected_evidence(truth, only={'back_wall', 'pot'})
        read = edits.rotate_design(truth, -1)
        calls = []
        fixed, report = edits.frame_check(read, evidence, lambda d_: calls.append(1) or self.compile_fn(d_), camera=self.CAMERA, size=self.SIZE)
        self.assertEqual((report['decision'], report['quarter_turns']), ('keep', 0))
        self.assertIn('frame unverified', report['reason'])
        self.assertEqual(fixed, read)
        # three of five elements are enough to verify
        evidence = self.projected_evidence(truth, only={'back_wall', 'pot', 'desk_a'})
        fixed, report = edits.frame_check(read, evidence, self.compile_fn, camera=self.CAMERA, size=self.SIZE)
        self.assertNotIn('frame unverified', report['reason'])
        # no evidence at all, and evidence that does not make a box
        for bad in (None, {}, {'back_wall': 'x', 'pot': {'bbox_px': [1, 2]}, 'table': {'bbox_px': [5, 5, 5, 9]}}):
            fixed, report = edits.frame_check(read, bad, self.compile_fn, camera=self.CAMERA, size=self.SIZE)
            self.assertEqual(report['decision'], 'keep')
            self.assertIn('frame unverified', report['reason'])
            self.assertEqual(fixed, read)

    # -- P8-fix2 polish: the turn follows the compiler's own defaults
    @staticmethod
    def turned_box(box, turns=1):
        for _ in range(turns):
            box = [-box[3], box[0], -box[1], box[2]]          # (x, y) -> (-y, x)
        return box

    def part_boxes(self, design, skip=('sign_text', 'grass_blade'), only=None):
        """{element_id: [x0, y0, x1, y1] of all its compiled parts}, letters and blades left out; only: just the parts with these roles."""
        spec = self.compile_fn(design)
        view = edits.SpecView(spec, [e['id'] for e in design['elements']])
        out = {}
        for eid, parts in view.parts.items():
            pts = [p for comp, ps in parts if comp.get('role') not in skip and (only is None or comp.get('role') in only) for p in ps]
            out[eid] = [min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)]
        return out

    def assert_boxes(self, got, want, msg=''):
        self.assertEqual(sorted(got), sorted(want), msg)
        for eid in want:
            for g, w in zip(got[eid], want[eid]):
                self.assertAlmostEqual(g, w, delta=0.01, msg=f'{msg} {eid}: {[round(v, 3) for v in got[eid]]} != {[round(v, 3) for v in want[eid]]}')

    @staticmethod
    def piece_design(entrance, footprint):
        d = base_design()
        d['form'].update(footprint_m=list(footprint), open_sides=[entrance])
        d['booth_type'] = 'inline'
        d['layout']['entrance_side'] = entrance
        d['requirements'] = []
        d['elements'] = [
            {'id': 'bar', 'kind': 'counter', 'side': 'centre', 'centre_m': [1.0, 0.5], 'size_m': [2.0, 0.5, 1.0]},
            {'id': 'stand', 'kind': 'display_plinth', 'side': 'centre', 'along': 0.3, 'size_m': [0.6, 0.4, 1.0]},
            {'id': 'seats', 'kind': 'seating_group', 'side': 'centre', 'centre_m': [-1.2, -0.4], 'count': 2},
            {'id': 'pots', 'kind': 'planter', 'side': 'centre', 'centre_m': [0.4, -0.9], 'count': 2},
            {'id': 'table', 'kind': 'round_table', 'side': 'centre', 'centre_m': [-0.6, 0.9], 'params': {'radius_m': 0.4}},
        ]
        return d

    def test_a_piece_with_no_yaw_turns_the_way_the_compiler_lays_it(self):
        for entrance in ('front', 'back', 'left', 'right'):
            for footprint in ((6.0, 6.0), (8.0, 4.0)):
                d = self.piece_design(entrance, footprint)
                base = self.part_boxes(d)
                for turns in (1, 2, 3):
                    with self.subTest(entrance=entrance, footprint=footprint, turns=turns):
                        got = self.part_boxes(edits.rotate_design(d, turns))
                        self.assert_boxes(got, {eid: self.turned_box(b, turns) for eid, b in base.items()}, msg='no yaw:')
        # the compiler's default is written out: the counter of the measured case, entrance left
        d = self.piece_design('left', (6.0, 6.0))
        bar = next(e for e in edits.rotate_design(d, 1)['elements'] if e['id'] == 'bar')
        self.assertEqual(bar['centre_m'], [-0.5, 1.0])
        self.assertAlmostEqual(abs(bar['yaw_deg']) % 180, 90.0, msg='lying along y after the turn')
        self.assertNotIn('yaw_deg', next(e for e in d['elements'] if e['id'] == 'bar'), 'the design given is not changed')

    def test_a_piece_placed_by_along_on_the_centre_line_turns_with_the_frame(self):
        for entrance in ('front', 'back', 'left', 'right'):
            for footprint in ((6.0, 6.0), (8.0, 4.0)):
                for along in (0.2, 0.8):
                    d = self.piece_design(entrance, footprint)
                    d['elements'] = [{'id': 'stand', 'kind': 'display_plinth', 'side': 'centre', 'along': along, 'size_m': [0.6, 0.4, 1.0]},
                                     {'id': 'bar', 'kind': 'counter', 'along': 1.0 - along, 'size_m': [1.4, 0.5, 1.0]}]
                    base = self.part_boxes(d)
                    x = -footprint[0] / 2 + along * footprint[0]
                    self.assertAlmostEqual((base['stand'][0] + base['stand'][2]) / 2, x, delta=0.01, msg='placed on the line y = 0')
                    for turns in (1, 2, 3):
                        with self.subTest(entrance=entrance, footprint=footprint, along=along, turns=turns):
                            turned = edits.rotate_design(d, turns)
                            stand = next(e for e in turned['elements'] if e['id'] == 'stand')
                            self.assertIn('centre_m', stand)
                            self.assertNotIn('along', stand)
                            got = self.part_boxes(turned)
                            self.assert_boxes(got, {eid: self.turned_box(b, turns) for eid, b in base.items()}, msg='by along:')
        d = self.piece_design('front', (6.0, 6.0))
        d['elements'] = [{'id': 'stand', 'kind': 'display_plinth', 'side': 'centre', 'along': 0.2, 'size_m': [0.6, 0.4, 1.0]}]
        self.assertEqual(edits.rotate_design(d, 1)['elements'][0]['centre_m'], [0.0, -1.8], 'x = -3 + 0.2 * 6 = -1.8 goes to y')

    def test_a_lit_panel_with_no_yaw_turns_with_its_shape_and_its_facing(self):
        for entrance in ('front', 'back', 'left', 'right'):
            for footprint in ((6.0, 6.0), (8.0, 4.0)):
                for placed in ({'centre_m': [0.5, 0.5]}, {'along': 0.3}):
                    d = self.piece_design(entrance, footprint)
                    d['elements'] = [dict({'id': 'glow', 'kind': 'lit_panel', 'side': 'centre', 'size_m': [2.4, 0.15, 2.2]}, **placed),
                                     {'id': 'table', 'kind': 'round_table', 'side': 'centre', 'centre_m': [-1.0, -0.8], 'params': {'radius_m': 0.4}}]
                    base = self.part_boxes(d)
                    for turns in (1, 2, 3):
                        with self.subTest(entrance=entrance, footprint=footprint, placed=placed, turns=turns):
                            got = self.part_boxes(edits.rotate_design(d, turns))
                            self.assert_boxes(got, {eid: self.turned_box(b, turns) for eid, b in base.items()}, msg='lit panel:')

    def test_a_reader_who_turned_a_booth_with_a_lit_panel_a_quarter_off_gets_the_panel_back_where_it_was(self):
        truth = self.scene()
        truth['elements'] = [
            {'id': 'back_wall', 'kind': 'feature_wall', 'side': 'back', 'along': 0.5, 'size_m': [4.2, 0.08, None]},
            {'id': 'glow', 'kind': 'lit_panel', 'side': 'centre', 'centre_m': [1.0, 0.5], 'size_m': [2.4, 0.15, 2.2]},
            {'id': 'table', 'kind': 'round_table', 'side': 'centre', 'centre_m': [-1.2, 0.8], 'params': {'radius_m': 0.6}},
            {'id': 'pot', 'kind': 'planter', 'side': 'centre', 'centre_m': [1.6, -1.2]},
        ]
        # the same booth as a reader saw it turned clockwise by a quarter (written by hand: (x, y) -> (y, -x), front to left, back to right)
        read = copy.deepcopy(truth)
        read['form']['open_sides'] = ['left']
        read['layout']['entrance_side'] = 'left'
        read['elements'] = [
            {'id': 'back_wall', 'kind': 'feature_wall', 'side': 'right', 'along': 0.5, 'size_m': [4.2, 0.08, None]},
            {'id': 'glow', 'kind': 'lit_panel', 'side': 'centre', 'centre_m': [0.5, -1.0], 'size_m': [2.4, 0.15, 2.2]},
            {'id': 'table', 'kind': 'round_table', 'side': 'centre', 'centre_m': [0.8, 1.2], 'params': {'radius_m': 0.6}},
            {'id': 'pot', 'kind': 'planter', 'side': 'centre', 'centre_m': [-1.2, -1.6]},
        ]
        evidence = self.projected_evidence(truth)
        fixed, report = edits.frame_check(read, evidence, self.compile_fn, camera=self.CAMERA, size=self.SIZE)
        self.assertEqual((report['decision'], report['quarter_turns']), ('turn', 1))
        want = self.part_boxes(truth)
        self.assertAlmostEqual(want['glow'][0], -0.2, delta=0.01)
        self.assert_boxes(self.part_boxes(fixed), want, msg='turned back:')
        glow = next(e for e in fixed['elements'] if e['id'] == 'glow')
        self.assertEqual(fixed['form']['open_sides'], ['front'])

    def test_a_piece_placed_by_along_with_a_yaw_is_turned_as_the_compiler_built_it(self):
        # the compiler lays a piece placed by along alone with its width along x whatever the yaw says (the yaw is only its facing)
        for entrance in ('front', 'left'):
            for yaw in (90.0, -90.0, 75.0, -120.0, 20.0):
                for turns in (1, 2, 3):
                    with self.subTest(entrance=entrance, yaw=yaw, turns=turns):
                        d = self.piece_design(entrance, (6.0, 6.0))
                        d['elements'] = [{'id': 'bar', 'kind': 'counter', 'side': 'centre', 'along': 0.3, 'yaw_deg': yaw, 'size_m': [2.0, 0.5, 1.0]}]
                        base = self.part_boxes(d)
                        got = self.part_boxes(edits.rotate_design(d, turns))
                        self.assert_boxes(got, {eid: self.turned_box(b, turns) for eid, b in base.items()}, msg='along with a yaw:')

    def test_a_sweep_of_pieces_placed_by_along_or_by_centre_with_a_yaw_turns_with_the_frame(self):
        import random
        rng = random.Random(8)
        kinds = ({'kind': 'counter', 'size_m': [1.6, 0.5, 1.0]}, {'kind': 'display_plinth', 'size_m': [0.9, 0.4, 1.0]},
                 {'kind': 'box', 'size_m': [1.2, 0.5, 0.8]}, {'kind': 'storage_block', 'size_m': [1.4, 0.6, 2.0]},
                 {'kind': 'partition', 'size_m': [1.5, 0.1, 2.0]})
        done = 0
        while done < 14:
            yaw = round(rng.uniform(-180.0, 180.0), 1)
            if min(abs(abs(yaw) % 180 - 45), abs(abs(yaw) % 180 - 135)) < 2.0:       # the compiler's own boundary
                continue
            kind = dict(rng.choice(kinds))
            entrance = rng.choice(('front', 'back', 'left', 'right'))
            footprint = rng.choice(((6.0, 6.0), (7.0, 5.0)))
            piece = dict(kind, id='piece', side='centre', yaw_deg=yaw)
            if rng.random() < 0.5:
                piece['along'] = round(rng.uniform(0.25, 0.75), 2)
            else:
                piece['centre_m'] = [round(rng.uniform(-1.2, 1.2), 2), round(rng.uniform(-0.8, 0.8), 2)]
            d = self.piece_design(entrance, footprint)
            d['elements'] = [piece]
            turns = rng.choice((1, 2, 3))
            base = self.part_boxes(d)
            with self.subTest(piece=piece, entrance=entrance, footprint=footprint, turns=turns):
                got = self.part_boxes(edits.rotate_design(d, turns))
                self.assert_boxes(got, {eid: self.turned_box(b, turns) for eid, b in base.items()}, msg='sweep:')
            done += 1

    # -- P8-fix2 polish 5: floor pieces are turned from their compiled placement (02 section 4.1, 2026-10-24)
    def turned_from_compile(self, design, turns):
        """The design turned by `turns` quarters from the placements the compiler actually built (rotate_design with the compile)."""
        return edits.rotate_design(design, turns, spec=self.compile_fn(design))

    def test_a_piece_with_neither_along_nor_centre_turns_as_the_compiler_built_it(self):
        d = self.piece_design('front', (6.0, 6.0))
        d['elements'] = [{'id': 'bar', 'kind': 'counter', 'side': 'centre', 'size_m': [2.0, 0.5, 1.0]}]
        base = self.part_boxes(d)
        self.assertAlmostEqual(base['bar'][0], -1.02, delta=0.01)
        self.assertAlmostEqual(base['bar'][3], 0.27, delta=0.01)
        got = self.part_boxes(self.turned_from_compile(d, 1))
        self.assert_boxes(got, {'bar': [-0.27, -1.02, 0.27, 1.02]}, msg='neither along nor centre:')
        for kind, size in (('counter', [2.0, 0.5, 1.0]), ('partition', [1.5, 0.1, 2.0]), ('storage_block', [1.4, 0.6, 2.0]),
                           ('box', [1.2, 0.5, 0.8]), ('display_plinth', [0.9, 0.4, 1.0])):
            for turns in (1, 2, 3):
                with self.subTest(kind=kind, turns=turns):
                    d['elements'] = [{'id': 'bar', 'kind': kind, 'side': 'centre', 'size_m': size}]
                    base = self.part_boxes(d)
                    self.assert_boxes(self.part_boxes(self.turned_from_compile(d, turns)),
                                      {eid: self.turned_box(b, turns) for eid, b in base.items()}, msg=kind)
        d['elements'] = [{'id': 'bar', 'kind': 'planter', 'side': 'centre', 'count': 2}]
        base = self.part_boxes(d)
        self.assert_boxes(self.part_boxes(self.turned_from_compile(d, 1)), {eid: self.turned_box(b) for eid, b in base.items()}, msg='planter row')

    def test_a_lit_panel_pulled_in_by_the_compiler_is_turned_from_where_it_really_is(self):
        d = self.piece_design('front', (6.0, 6.0))
        d['elements'] = [{'id': 'glow', 'kind': 'lit_panel', 'side': 'centre', 'along': 0.1, 'yaw_deg': 90.0, 'size_m': [2.4, 0.15, 2.2]}]
        base = self.part_boxes(d)               # wherever the compiler puts it: the reference is what it built, not what the words say
        got = self.part_boxes(self.turned_from_compile(d, 1))
        self.assert_boxes(got, {'glow': self.turned_box(base['glow'])}, msg='where it really stands:')
        self.assertAlmostEqual(got['glow'][1], base['glow'][0], delta=0.01)
        self.assertAlmostEqual(got['glow'][3], base['glow'][2], delta=0.01)
        # a kind the compiler still pulls in by its width: a counter placed by along near an end
        d['elements'] = [{'id': 'bar', 'kind': 'counter', 'side': 'centre', 'along': 0.02, 'size_m': [2.0, 0.5, 1.0]}]
        base = self.part_boxes(d)
        self.assertAlmostEqual((base['bar'][0] + base['bar'][2]) / 2, -2.0, delta=0.05, msg='pulled in by half its width (-3 + 1 m)')
        got = self.part_boxes(self.turned_from_compile(d, 1))
        self.assert_boxes(got, {'bar': self.turned_box(base['bar'])}, msg='pulled in:')

    def test_a_sweep_over_every_floor_kind_and_placement_turns_exactly(self):
        import random
        rng = random.Random(15)
        kinds = (('counter', {'size_m': [1.6, 0.5, 1.0]}), ('partition', {'size_m': [1.5, 0.1, 2.0]}), ('storage_block', {'size_m': [1.4, 0.6, 2.0]}),
                 ('box', {'size_m': [1.2, 0.5, 0.8]}), ('display_plinth', {'size_m': [0.9, 0.4, 1.0]}), ('planter', {'count': 2}),
                 ('round_table', {'params': {'radius_m': 0.4}}), ('seating_group', {'count': 2}), ('chair', {}),
                 ('lit_panel', {'size_m': [2.4, 0.15, 2.2]}))
        done = 0
        while done < 60:
            kind, extra = rng.choice(kinds)
            piece = dict(extra, id='piece', kind=kind)
            yaw = round(rng.uniform(-180.0, 180.0), 1)
            if min(abs(abs(yaw) % 180 - 45), abs(abs(yaw) % 180 - 135)) < 2.0:
                continue
            if rng.random() < 0.5:
                piece['yaw_deg'] = yaw
            placement = rng.choice(('along', 'centre', 'neither', 'side'))
            if placement == 'along':
                piece.update(side=rng.choice(('centre', None)), along=round(rng.uniform(0.05, 0.95), 2))
            elif placement == 'centre':
                piece.update(side=rng.choice(('centre', rng.choice(('front', 'back', 'left', 'right')))),
                             centre_m=[round(rng.uniform(-1.2, 1.2), 2), round(rng.uniform(-0.8, 0.8), 2)])
            elif placement == 'neither':
                piece.update(side=rng.choice(('centre', None)))
            else:
                piece.update(side=rng.choice(('front', 'back', 'left', 'right')), along=round(rng.uniform(0.2, 0.8), 2),
                             offset_m=round(rng.uniform(0.0, 0.6), 2))
            piece = {k: v for k, v in piece.items() if v is not None}
            entrance = rng.choice(('front', 'back', 'left', 'right'))
            footprint = rng.choice(((6.0, 6.0), (7.0, 5.0), (5.0, 7.0), (8.0, 4.0)))
            d = self.piece_design(entrance, footprint)
            d['elements'] = [piece]
            if piece['kind'] == 'lit_panel' and piece.get('side') in ('left', 'right') and min(footprint) < 4.5:
                continue
            try:
                base = self.part_boxes(d)
            except Exception:                                    # a design the compiler does not take is not a case
                continue
            if 'piece' not in base:
                continue
            turns = rng.choice((1, 2, 3))
            with self.subTest(piece=piece, entrance=entrance, footprint=footprint, turns=turns):
                got = self.part_boxes(self.turned_from_compile(d, turns))
                self.assert_boxes(got, {eid: self.turned_box(b, turns) for eid, b in base.items()}, msg='sweep:')
            done += 1

    def test_the_self_check_marks_a_turn_that_does_not_give_the_geometry_back_inexact_and_never_chooses_it(self):
        truth = self.scene()
        read = edits.rotate_design(truth, -1)
        evidence = self.projected_evidence(truth, only={'back_wall', 'desk_a', 'desk_b'})        # the planter is not witnessed
        fixed, report = edits.frame_check(read, evidence, self.compile_fn, camera=self.CAMERA, size=self.SIZE)
        self.assertEqual((report['decision'], report['quarter_turns']), ('turn', 1))
        by_turn = {r['degrees']: r for r in report['rotations']}
        for r in report['rotations']:
            self.assertEqual(r['inexact'], [] if r['degrees'] else None, 'the design as read has nothing to be checked against')
        real = edits.rotate_design

        def broken(design, quarter_turns, footprint=None, spec=None):
            out = real(design, quarter_turns, footprint, spec)
            was = {e['id']: e for e in design['elements']}
            for e in out['elements']:
                if e['id'] == 'pot' and was['pot'].get('centre_m') and quarter_turns:
                    e['centre_m'] = list(was['pot']['centre_m'])             # the planter stays where it was
            return out
        with unittest.mock.patch.object(edits, 'rotate_design', broken):
            fixed, report = edits.frame_check(read, evidence, self.compile_fn, camera=self.CAMERA, size=self.SIZE)
        by_turn = {r['degrees']: r for r in report['rotations']}
        self.assertIn('pot', by_turn[90]['inexact'])
        self.assertGreater(by_turn[90]['mean_iou'], 0.5, 'the witnessed pieces are right: the scores alone would turn')
        self.assertEqual((report['decision'], report['quarter_turns']), ('keep', 0), 'an inexact turn is never chosen')
        self.assertEqual(fixed, read)
        self.assertIn('inexact', report['reason'] + json.dumps(report['rotations']))

    def test_exactly_half_of_the_elements_with_evidence_is_enough_to_verify_the_frame(self):
        truth = self.scene()
        truth['elements'] = truth['elements'][:4]                       # four elements
        read = edits.rotate_design(truth, -1)
        names = [e['id'] for e in truth['elements']]
        evidence = self.projected_evidence(truth, only=set(names[:2]))
        fixed, report = edits.frame_check(read, evidence, self.compile_fn, camera=self.CAMERA, size=self.SIZE)
        self.assertNotIn('frame unverified', report['reason'], 'two of four is half: verified')
        self.assertEqual(report['evidence_elements'], 2)
        evidence = self.projected_evidence(truth, only=set(names[:1]))
        fixed, report = edits.frame_check(read, evidence, self.compile_fn, camera=self.CAMERA, size=self.SIZE)
        self.assertIn('frame unverified', report['reason'], 'one of four is less than half')

    def shifted_rotation(self, dx, who='pot'):
        """A rotate_design that leaves `who` dx metres off in x after every turn."""
        real = edits.rotate_design

        def shifted(design, quarter_turns, footprint=None, spec=None):
            out = real(design, quarter_turns, footprint, spec)
            if quarter_turns % 4:
                for e in out['elements']:
                    if e['id'] == who and e.get('centre_m'):
                        e['centre_m'] = [e['centre_m'][0] + dx, e['centre_m'][1]]
            return out
        return shifted

    def test_a_turn_off_by_five_millimetres_passes_the_self_check_and_one_off_by_two_centimetres_does_not(self):
        truth = self.scene()
        read = edits.rotate_design(truth, -1)
        evidence = self.projected_evidence(truth, only={'back_wall', 'desk_a', 'desk_b'})
        with unittest.mock.patch.object(edits, 'rotate_design', self.shifted_rotation(0.005)):
            fixed, report = edits.frame_check(read, evidence, self.compile_fn, camera=self.CAMERA, size=self.SIZE)
        self.assertEqual({r['degrees']: r['inexact'] for r in report['rotations'] if r['degrees']}, {90: [], 180: [], 270: []})
        self.assertEqual((report['decision'], report['quarter_turns']), ('turn', 1))
        with unittest.mock.patch.object(edits, 'rotate_design', self.shifted_rotation(0.02)):
            fixed, report = edits.frame_check(read, evidence, self.compile_fn, camera=self.CAMERA, size=self.SIZE)
        by_turn = {r['degrees']: r for r in report['rotations']}
        self.assertEqual(by_turn[90]['inexact'], ['pot'])
        self.assertGreater(by_turn[90]['mean_iou'], 0.5)
        self.assertEqual((report['decision'], report['quarter_turns']), ('keep', 0))
        self.assertEqual(fixed, read)

    def needs_normalising(self):
        """A reader answer whose pieces the compiler places in its own way: entrance left, the back wall on the right, counters by side
        and by centre, a round table, a 2.6 m partition with neither along nor centre_m, a lit panel by along near an end with a yaw."""
        d = self.piece_design('left', (6.0, 6.0))
        d['elements'] = [
            {'id': 'wall_r', 'kind': 'feature_wall', 'side': 'right', 'along': 0.5, 'size_m': [4.2, 0.08, None]},
            {'id': 'c1', 'kind': 'counter', 'side': 'back', 'along': 0.3, 'offset_m': 0.5, 'size_m': [1.6, 0.5, 1.0]},
            {'id': 'c2', 'kind': 'counter', 'side': 'centre', 'centre_m': [1.5, -1.4], 'yaw_deg': 20.0, 'size_m': [1.4, 0.5, 1.0]},
            {'id': 'table', 'kind': 'round_table', 'side': 'centre', 'centre_m': [-1.8, 1.6], 'params': {'radius_m': 0.5}},
            {'id': 'part', 'kind': 'partition', 'side': 'centre', 'size_m': [2.6, 0.1, 2.0]},
            {'id': 'glow', 'kind': 'lit_panel', 'side': 'centre', 'along': 0.1, 'yaw_deg': 90.0, 'size_m': [2.4, 0.15, 2.2]},
            {'id': 'table2', 'kind': 'round_table', 'side': 'centre', 'centre_m': [1.8, 1.7], 'params': {'radius_m': 0.4}},
            {'id': 'c3', 'kind': 'counter', 'side': 'centre', 'centre_m': [-1.7, -1.6], 'yaw_deg': 0.0, 'size_m': [1.2, 0.5, 1.0]},
        ]
        return d

    def test_a_reader_answer_that_needs_its_placements_normalised_is_turned_back_exactly(self):
        answer = self.needs_normalising()
        spec = self.compile_fn(answer)
        truth = edits.rotate_design(answer, 1, spec=spec)
        base = self.part_boxes(answer)
        self.assert_boxes(self.part_boxes(truth), {eid: self.turned_box(b) for eid, b in base.items()}, msg='the truth is the answer turned:')
        evidence = self.projected_evidence(truth)
        fixed, report = edits.frame_check(answer, evidence, self.compile_fn, camera=self.CAMERA, size=self.SIZE)
        self.assertEqual((report['decision'], report['quarter_turns']), ('turn', 1), report['reason'])
        self.assertEqual(report['rotations'][1]['inexact'], [])
        self.assert_boxes(self.part_boxes(fixed), self.part_boxes(truth), msg='turned back, piece by piece:')

    def test_a_piece_on_the_compilers_45_degree_boundary_is_written_clear_of_it_and_turns_exactly(self):
        for kind, size in (('counter', [2.0, 0.5, 1.0]), ('partition', [1.5, 0.1, 2.0]), ('box', [1.2, 0.5, 0.8])):
            for yaw in (45.0, 135.0, -45.0, -135.0):
                for placement in ({'centre_m': [1.0, 0.5]}, {'along': 0.3}):
                    d = self.piece_design('front', (6.0, 6.0))
                    d['elements'] = [dict({'id': 'bar', 'kind': kind, 'side': 'centre', 'yaw_deg': yaw, 'size_m': size}, **placement)]
                    spec = self.compile_fn(d)
                    written = edits.normalise_placements(d, spec)['elements'][0]['yaw_deg']
                    self.assertGreater(min(abs(abs(written) % 180 - 45), abs(abs(written) % 180 - 135)), 1.0, 'not on the boundary')
                    base = self.part_boxes(d)
                    for turns in (1, 2, 3):
                        with self.subTest(kind=kind, yaw=yaw, placement=placement, turns=turns):
                            got = self.part_boxes(edits.rotate_design(d, turns, spec=spec))
                            self.assert_boxes(got, {eid: self.turned_box(b, turns) for eid, b in base.items()}, msg='boundary:')

    def test_a_failure_to_read_the_placements_or_to_check_a_turn_makes_the_turn_inexact_and_is_never_adopted(self):
        truth = self.scene()
        read = edits.rotate_design(truth, -1)
        evidence = self.projected_evidence(truth)
        for name, broken in (('normalise_placements', RuntimeError('cannot read the placements')),
                             ('turn_mismatches', RuntimeError('the check broke'))):
            lines = []

            def fail(*args, **kw):
                raise broken
            with unittest.mock.patch.object(edits, name, fail):
                fixed, report = edits.frame_check(read, evidence, self.compile_fn, camera=self.CAMERA, size=self.SIZE, log=lines.append)
            with self.subTest(failing=name):
                self.assertEqual((report['decision'], report['quarter_turns']), ('keep', 0), 'the right turn is there, but it is not verified')
                self.assertEqual(fixed, read)
                for r in report['rotations'][1:]:
                    self.assertTrue(r['inexact'], f"{r['degrees']}: inexact, not null, not unchecked")
                    self.assertIsInstance(r['inexact'], list)
                    self.assertIn(str(broken), r['error'])
                self.assertTrue(any(str(broken) in ln for ln in lines), 'said in the log')
                self.assertNotIn('no other turn compiles', report['reason'])
                self.assertIn('inexact', report['reason'])
                for key in ('elements', 'evidence_elements', 'gates'):
                    self.assertIn(key, report)

    def test_the_report_skeleton_has_the_same_fields_on_every_path(self):
        truth = self.scene()
        evidence = self.projected_evidence(truth)
        skeleton = edits.frame_report(truth, evidence, self.SIZE)
        _, ok = edits.frame_check(truth, evidence, self.compile_fn, camera=self.CAMERA, size=self.SIZE)
        _, unverified = edits.frame_check(truth, None, self.compile_fn, camera=self.CAMERA, size=self.SIZE)
        for report in (skeleton, ok, unverified):
            for key in ('decision', 'quarter_turns', 'reason', 'elements', 'evidence_elements', 'gates', 'rotations'):
                self.assertIn(key, report)
        self.assertEqual((skeleton['elements'], skeleton['evidence_elements']), (5, 5))

    @staticmethod
    def canopy_design(footprint, size, sign):
        d = base_design()
        d['form'].update(footprint_m=list(footprint))
        d['requirements'] = []
        d['elements'] = [{'id': 'lid', 'kind': 'canopy_frame', 'size_m': size, 'params': {'sign': sign}}]
        return d

    def test_a_canopy_sign_without_a_corner_or_an_along_turns_with_the_compiler_defaults(self):
        for footprint, size in (((6.0, 6.0), [5.0, 5.0, 0.3]), ((8.0, 5.0), [7.0, 4.0, 0.3])):
            for sign in ({'corner': 'front_left', 'drop_m': 0.2}, {'drop_m': 0.2}, {}, {'corner': 'back_right', 'drop_m': 0.2}):
                d = self.canopy_design(footprint, size, sign)
                base = self.part_boxes(d, only=('canopy_sign',))
                for turns in (1, 2, 3):
                    with self.subTest(footprint=footprint, sign=sign, turns=turns):
                        turned = edits.rotate_design(d, turns)
                        got = self.part_boxes(turned, only=('canopy_sign',))
                        self.assert_boxes(got, {eid: self.turned_box(b, turns) for eid, b in base.items()}, msg='sign:')
                        out = turned['elements'][0]['params']['sign']
                        self.assertIn(out['corner'], ('front_left', 'front_right', 'back_left', 'back_right'))
                        self.assertEqual(len(out['along_m']), 2)
        d = self.canopy_design((6.0, 6.0), [5.0, 5.0, 0.3], {'drop_m': 0.2})
        self.assertEqual(d['elements'][0]['params']['sign'], {'drop_m': 0.2}, 'the design given is not changed')

    def test_a_design_as_read_that_cannot_be_built_is_turned_when_a_turn_can(self):
        truth = self.scene()
        truth['form']['footprint_m'] = [8.0, 4.0]
        truth['elements'].append({'id': 'lid', 'kind': 'canopy_frame', 'centre_m': [0.0, 0.0], 'size_m': [7.0, 3.0, 0.3]})
        evidence = self.projected_evidence(truth)
        read = edits.rotate_design(truth, 1)
        read['form']['footprint_m'] = [8.0, 4.0]                     # the fitted footprint: the 3 x 7 m frame does not fit it
        with self.assertRaises(ValueError):
            self.compile_fn(read)
        fixed, report = edits.frame_check(read, evidence, self.compile_fn, camera=self.CAMERA, size=self.SIZE)
        by_turn = {r['degrees']: r for r in report['rotations']}
        self.assertFalse(by_turn[0]['compiled'])
        for degrees in (90, 180, 270):
            self.assertEqual(by_turn[degrees]['inexact'], 'unchecked', 'no compile of the design as read to check a turn against')
        self.assertEqual((report['decision'], report['quarter_turns']), ('turn', 3))
        self.assertGreater(by_turn[270]['mean_iou'], 0.99)
        self.assert_same_places(self.centres(fixed), self.centres(truth), msg='turned back:')

    def test_frame_check_does_not_break_on_what_it_cannot_see(self):
        truth = self.scene()
        evidence = self.projected_evidence(truth)
        # evidence boxes outside the picture are not evidence
        outside = {eid: {'bbox_px': [2000, 2000, 2100, 2100], 'confidence': 'low'} for eid in evidence}
        fixed, report = edits.frame_check(truth, outside, self.compile_fn, camera=self.CAMERA, size=self.SIZE)
        self.assertEqual(report['decision'], 'keep')
        self.assertIn('frame unverified', report['reason'])
        # a camera that looks away: nothing of the booth is in the picture, so no turn is better
        away = dict(self.CAMERA, position=[0.0, -9.0, 1.5], target=[0.0, -20.0, 1.5])
        fixed, report = edits.frame_check(truth, evidence, self.compile_fn, camera=away, size=self.SIZE)
        self.assertEqual(report['decision'], 'keep')
        self.assertEqual(fixed, truth)
        # a design that does not compile at all, and one that compiles only as it was read
        def broken(design):
            raise ValueError('the design does not compile')
        fixed, report = edits.frame_check(truth, evidence, broken, camera=self.CAMERA, size=self.SIZE)
        self.assertEqual(report['decision'], 'keep')
        self.assertFalse(any(r['compiled'] for r in report['rotations']))
        self.assertEqual(fixed, truth)
        only_zero = lambda design: self.compile_fn(design) if design['elements'][0].get('side') == 'back' else broken(design)    # noqa: E731
        fixed, report = edits.frame_check(truth, evidence, only_zero, camera=self.CAMERA, size=self.SIZE)
        self.assertEqual(report['decision'], 'keep')
        self.assertEqual([r['compiled'] for r in report['rotations']], [True, False, False, False])
        # no compile function: the frame cannot be checked
        fixed, report = edits.frame_check(truth, evidence, None, camera=self.CAMERA, size=self.SIZE)
        self.assertEqual(report['decision'], 'keep')
        self.assertIn('frame unverified', report['reason'])
        # a footprint that is not square: turns that do not compile are skipped, nothing is raised
        wide = self.scene()
        wide['form']['footprint_m'] = [8.0, 4.0]
        wide['elements'].append({'id': 'lid', 'kind': 'canopy_frame', 'centre_m': [0.0, 0.0], 'size_m': [7.0, 3.0, 0.3]})
        ev = self.projected_evidence(wide)
        fixed, report = edits.frame_check(wide, ev, self.compile_fn, camera=self.CAMERA, size=self.SIZE)
        self.assertEqual(len(report['rotations']), 4)
        by_turn = {r['degrees']: r for r in report['rotations']}
        self.assertTrue(by_turn[0]['compiled'])
        self.assertFalse(by_turn[90]['compiled'], 'a 7 x 3 m frame does not fit a quarter turn of the 8 x 4 m stand')
        self.assertEqual(report['decision'], 'keep')


class CanopyCase(unittest.TestCase):
    """A canopy_frame can be resized: the width from the box of the frame body, the depth by the small step, inside the footprint."""

    @classmethod
    def setUpClass(cls):
        d = base_design()
        d['form']['footprint_m'] = [6.0, 6.0]
        d['requirements'] = []
        d['elements'] = [{'id': 'lid', 'kind': 'canopy_frame'}]
        cls.design = d
        cls.spec = cls.compile(d)
        cls.ids = ['lid']
        cam = cls.spec['camera']
        eye = [t + 1.8 * (p - t) for p, t in zip(cam['position'], cam['target'])]
        cls.camera = dict(cam, position=eye)
        far = copy.deepcopy(cls.spec)
        far['camera'] = cls.camera
        cls.view = edits.SpecView(far, cls.ids)

    @staticmethod
    def compile(design):
        with tempfile.TemporaryDirectory() as tmp:
            spec, report = design_compile.compile_design(copy.deepcopy(design), tmp)
        assert report['status'] != 'invalid', report['validation_errors']
        return spec

    def finding(self, direction, bbox, fid='f1'):
        return {'id': fid, 'element_id': 'lid', 'type': 'wrong_size', 'severity': 'high', 'direction': direction, 'bbox_px': bbox}

    def frame_pred(self):
        return lambda c: c.get('role') in ('canopy_frame', 'canopy_sign')

    def test_a_canopy_frame_is_resized_from_its_frame_body_and_clamped_to_the_footprint(self):
        spec_size = edits.spec_size(self.design['elements'][0], self.view)
        self.assertAlmostEqual(spec_size[0], 5.0, places=2)
        self.assertAlmostEqual(spec_size[1], 5.0, places=2)
        self.assertAlmostEqual(spec_size[2], 0.3, places=2)
        roles = {c.get('role') for c in self.spec['components']}
        self.assertIn('header_post', roles, 'the frame has its four posts')
        # the box the ratio is taken from is the frame body: never the posts, never letters
        raw, use_w, use_h = edits.ratio_box(self.design['elements'][0], self.view)
        body = self.view.box_px_raw('lid', self.frame_pred())
        whole = self.view.box_px_raw('lid')
        self.assertEqual(raw, body)
        self.assertGreater(whole[3] - whole[1], body[3] - body[1] + 20, 'the posts make the whole box much taller than the frame')
        self.assertEqual((use_w, use_h), (True, False), 'the width is set by the box width; the height is not set by the box height')
        # a critic who sees a 30 % wider frame: 5.0 * 1.3 is 6.5 m, cut to the 6.0 m footprint; the depth takes the small step
        cx, w = (body[0] + body[2]) / 2, (body[2] - body[0]) * 1.3
        found, skipped = edits.findings_to_edits(self.design, [self.finding('bigger', [cx - w / 2, body[1], cx + w / 2, body[3]])],
                                                 camera=self.camera, spec=self.view)
        self.assertEqual(skipped, [])
        size = found[0]['size_m']
        self.assertEqual(found[0]['op'], 'resize')
        self.assertEqual(size[0], 6.0, 'cut to the footprint, not 6.5')
        self.assertAlmostEqual(size[1], 5.5, places=3, msg='the depth is the small step 1.1 and not the width ratio')
        self.assertAlmostEqual(size[2], 0.33, places=3)
        new = edits.apply_edit(self.design, found[0])
        spec = self.compile(new)
        built = edits.spec_size(new['elements'][0], edits.SpecView(spec, self.ids))
        self.assertAlmostEqual(built[0], 6.0, places=2)
        self.assertAlmostEqual(built[1], 5.5, places=2)
        # a ratio inside the footprint is used as it is: a frame 10 % wider than the render is 5.5 m
        w2 = (body[2] - body[0]) * 1.1
        found, _ = edits.findings_to_edits(self.design, [self.finding('bigger', [cx - w2 / 2, body[1], cx + w2 / 2, body[3]])],
                                           camera=self.camera, spec=self.view)
        self.assertAlmostEqual(found[0]['size_m'][0], 5.5, delta=0.02)
        # a frame that is already as wide as the footprint stays 6.0 and the depth still grows
        wide = copy.deepcopy(self.design)
        wide['elements'][0]['size_m'] = [6.0, 5.0, 0.3]
        wspec = self.compile(wide)
        wview = edits.SpecView(dict(wspec, camera=self.camera), self.ids)
        wbody = wview.box_px_raw('lid', self.frame_pred())
        found, skipped = edits.findings_to_edits(wide, [self.finding('bigger', [wbody[0] - 3, wbody[1], wbody[2] + 3, wbody[3]])],
                                                 camera=self.camera, spec=wview)
        self.assertEqual(skipped, [])
        self.assertEqual(found[0]['size_m'][0], 6.0)
        # smaller works the other way
        found, _ = edits.findings_to_edits(self.design, [self.finding('smaller', None)], camera=self.camera, spec=self.view)
        self.assertAlmostEqual(found[0]['size_m'][0], 5.0 / 1.1, places=3)
        self.assertAlmostEqual(found[0]['size_m'][1], 5.0 / 1.1, places=3)

    def test_the_depth_of_a_canopy_frame_is_clamped_to_the_footprint_too(self):
        design = copy.deepcopy(self.design)
        design['elements'][0]['size_m'] = [5.0, 5.8, 0.3]
        spec = dict(self.compile(design), camera=self.camera)
        view = edits.SpecView(spec, self.ids)
        found, skipped = edits.findings_to_edits(design, [self.finding('bigger', None)], camera=self.camera, spec=view)
        self.assertEqual(skipped, [])
        size = found[0]['size_m']
        self.assertEqual(size[1], 6.0, '5.8 * 1.1 is 6.38 m: cut to the 6.0 m depth of the footprint')
        self.assertAlmostEqual(size[0], 5.5, places=3)
        new = edits.apply_edit(design, found[0])
        self.compile(new)                                     # it compiles: the frame is inside the footprint

    def test_the_untrue_reason_for_skipping_a_canopy_frame_is_gone(self):
        found, skipped = edits.findings_to_edits(self.design, [self.finding('bigger', None)], camera=self.camera, spec=self.view)
        self.assertEqual(skipped, [])
        self.assertEqual(found[0]['op'], 'resize')
        found, skipped = edits.findings_to_edits(self.design, [self.finding('bigger', None)], camera=self.camera, spec=None)
        for s in skipped:
            self.assertNotIn('not read from size_m', s['reason'])

    def test_a_resized_canopy_is_measured_after_the_recompile(self):
        found, _ = edits.findings_to_edits(self.design, [self.finding('bigger', None)], camera=self.camera, spec=self.view)
        survivors, recorded, dropped = edits.measure_edits(self.design, self.view, found, [self.finding('bigger', None)],
                                                           lambda design: self.compile(design))
        self.assertEqual(dropped, [])
        self.assertEqual([e['finding_id'] for e in survivors], ['f1'])


class LitPanelCase(unittest.TestCase):
    """A lit_panel is placed like a partition: its width follows the box ratio of its panel, thickness and height the small step."""

    @staticmethod
    def make(footprint, entrance, size):
        d = base_design()
        d['form'].update(footprint_m=list(footprint), open_sides=[entrance])
        d['booth_type'] = 'inline'
        d['layout']['entrance_side'] = entrance
        d['requirements'] = []
        d['elements'] = [{'id': 'glow', 'kind': 'lit_panel', 'side': 'centre', 'centre_m': [0.0, 0.5], 'size_m': size}]
        return d

    @staticmethod
    def compile(design):
        with tempfile.TemporaryDirectory() as tmp:
            spec, report = design_compile.compile_design(copy.deepcopy(design), tmp)
        assert report['status'] != 'invalid', report['validation_errors']
        return spec

    def seen(self, design):
        spec = self.compile(design)
        cam = spec['camera']
        far = dict(cam, position=[t + 1.8 * (p - t) for p, t in zip(cam['position'], cam['target'])])
        return far, edits.SpecView(dict(spec, camera=far), ['glow'])

    @staticmethod
    def finding(direction, bbox):
        return {'id': 'f1', 'element_id': 'glow', 'type': 'wrong_size', 'severity': 'high', 'direction': direction, 'bbox_px': bbox}

    def test_a_lit_panel_without_a_size_is_resized_from_what_was_built(self):
        d = self.make((6.0, 6.0), 'front', None)
        camera, view = self.seen(d)
        built = edits.spec_size(d['elements'][0], view)
        self.assertAlmostEqual(built[0], 2.4, places=2)
        self.assertAlmostEqual(built[1], 0.15, places=2)
        found, skipped = edits.findings_to_edits(d, [self.finding('bigger', None)], camera=camera, spec=view)
        self.assertEqual(skipped, [])
        self.assertEqual(found[0]['op'], 'resize')
        for got, was in zip(found[0]['size_m'], built):
            self.assertAlmostEqual(got / was, 1.1, delta=0.005)

    def test_a_lit_panel_takes_its_width_from_the_box_ratio_and_thickness_and_height_by_the_small_step(self):
        d = self.make((6.0, 6.0), 'front', [2.4, 0.15, 2.2])
        camera, view = self.seen(d)
        body = view.box_px_raw('glow', edits.main_body(d['elements'][0]))
        cx, w = (body[0] + body[2]) / 2, (body[2] - body[0]) * 1.25
        found, skipped = edits.findings_to_edits(d, [self.finding('bigger', [cx - w / 2, body[1], cx + w / 2, body[3]])], camera=camera, spec=view)
        self.assertEqual(skipped, [])
        size = found[0]['size_m']
        self.assertAlmostEqual(size[0], 2.4 * 1.25, places=3)
        self.assertAlmostEqual(size[1], 0.15 * 1.1, places=3)
        self.assertAlmostEqual(size[2], 2.2 * 1.1, places=3)
        new = edits.apply_edit(d, found[0])
        built = edits.spec_size(new['elements'][0], edits.SpecView(self.compile(new), ['glow']))
        self.assertAlmostEqual(built[0], 3.0, places=2)

    def test_a_lit_panel_resize_stays_inside_the_footprint_whichever_way_the_panel_lies(self):
        # entrance front: the panel lies along x, 5.5 m of the 6 m width; 1.3 times that is cut to 6.0
        d = self.make((6.0, 6.0), 'front', [5.5, 0.15, 2.2])
        camera, view = self.seen(d)
        body = view.box_px_raw('glow', edits.main_body(d['elements'][0]))
        cx, w = (body[0] + body[2]) / 2, (body[2] - body[0]) * 1.3
        found, _ = edits.findings_to_edits(d, [self.finding('bigger', [cx - w / 2, body[1], cx + w / 2, body[3]])], camera=camera, spec=view)
        self.assertEqual(found[0]['size_m'][0], 6.0)
        self.compile(edits.apply_edit(d, found[0]))
        # entrance left in an 8 x 4 m stand: the panel is turned, lies along y, and may be 4.0 m long at most
        d = self.make((8.0, 4.0), 'left', [3.5, 0.15, 2.2])
        camera, view = self.seen(d)
        built = edits.spec_size(d['elements'][0], view)
        self.assertAlmostEqual(built[0], 3.5, places=2, msg='the size is the panel\'s own, not the box it fills in x and y')
        self.assertAlmostEqual(built[1], 0.15, places=2)
        body = view.box_px_raw('glow', edits.main_body(d['elements'][0]))
        cx, w = (body[0] + body[2]) / 2, (body[2] - body[0]) * 1.3
        found, _ = edits.findings_to_edits(d, [self.finding('bigger', [cx - w / 2, body[1], cx + w / 2, body[3]])], camera=camera, spec=view)
        self.assertEqual(found[0]['size_m'][0], 4.0, 'cut to the 4 m depth of the footprint')
        self.compile(edits.apply_edit(d, found[0]))

    @staticmethod
    def make_yaw(yaw, size, footprint=(6.0, 6.0)):
        d = LitPanelCase.make(footprint, 'front', size)
        d['elements'][0]['yaw_deg'] = yaw
        return d

    def test_a_turned_lit_panel_reports_its_own_width_thickness_and_height(self):
        reference = edits.spec_size(self.make((6.0, 6.0), 'front', None)['elements'][0], self.seen(self.make((6.0, 6.0), 'front', None))[1])
        for yaw in (30.0, 45.0, -60.0, 120.0, 90.0, -90.0, 0.0):
            for size in (None, [2.4, 0.15, 2.2], [2.4, None, 2.2]):
                with self.subTest(yaw=yaw, size=size):
                    d = self.make_yaw(yaw, size)
                    camera, view = self.seen(d)
                    built = edits.spec_size(d['elements'][0], view)
                    want = [2.4, 0.15, 2.2 if size and size[2] else reference[2]]
                    for got, w in zip(built, want):
                        self.assertAlmostEqual(got, w, delta=0.005)
                    for direction, factor in (('bigger', 1.1), ('smaller', 1 / 1.1)):
                        found, skipped = edits.findings_to_edits(d, [self.finding(direction, None)], camera=camera, spec=view)
                        self.assertEqual(skipped, [])
                        new = found[0]['size_m']
                        for k, (got, w) in enumerate(zip(new, want)):
                            if got is None:
                                self.assertIsNone((size or [None] * 3)[k])
                            else:
                                self.assertAlmostEqual(got, w * factor, delta=0.005, msg=f'{direction} {k}')
                        survivors, recorded, dropped = edits.measure_edits(d, view, found, [self.finding(direction, None)], self.compile)
                        self.assertEqual(dropped, [], f'{direction}: the recompiled panel goes the way asked')

    def test_a_lit_panel_width_obeys_the_compilers_own_limits_at_an_oblique_yaw_and_is_rounded_down(self):
        d = self.make_yaw(30.0, [5.5, 0.15, 2.2])
        camera, view = self.seen(d)
        body = view.box_px_raw('glow', edits.main_body(d['elements'][0]))
        cx, w = (body[0] + body[2]) / 2, (body[2] - body[0]) * 1.5
        found, _ = edits.findings_to_edits(d, [self.finding('bigger', [cx - w / 2, body[1], cx + w / 2, body[3]])], camera=camera, spec=view)
        self.assertLessEqual(found[0]['size_m'][0], 6.0, 'a panel is never wider than the longer side of the footprint')
        self.compile(edits.apply_edit(d, found[0]))
        # a footprint that is not a round number: the limit is rounded down, never up past the compiler's tolerance
        d = self.make((5.12345, 6.0), 'front', [5.0, 0.15, 2.2])
        camera, view = self.seen(d)
        body = view.box_px_raw('glow', edits.main_body(d['elements'][0]))
        cx, w = (body[0] + body[2]) / 2, (body[2] - body[0]) * 1.3
        found, _ = edits.findings_to_edits(d, [self.finding('bigger', [cx - w / 2, body[1], cx + w / 2, body[3]])], camera=camera, spec=view)
        self.assertEqual(found[0]['size_m'][0], 5.1234)
        self.compile(edits.apply_edit(d, found[0]))

    def built(self, design):
        with tempfile.TemporaryDirectory() as tmp:
            spec, report = design_compile.compile_design(copy.deepcopy(design), tmp)
        return report['status'] == 'ok' and 'glow' not in {d['id'] for d in report['dropped']} and any(c.get('group') == 'glow' for c in spec['components'])

    def test_the_thickness_of_a_lit_panel_limits_its_width_at_an_oblique_yaw_and_the_clamped_panel_is_built(self):
        import math as m
        for footprint, yaw, thickness in (((6.0, 3.5), -89.1, 0.4), ((6.0, 4.0), 70.0, 0.4), ((6.0, 4.0), 70.0, 0.15), ((3.5, 6.0), 0.9, 0.4)):
            with self.subTest(footprint=footprint, yaw=yaw, thickness=thickness):
                d = self.make_yaw(yaw, [min(footprint) - 0.6, thickness, 2.2], footprint)
                self.assertTrue(self.built(d), 'the starting panel is built')
                camera, view = self.seen(d)
                body = view.box_px_raw('glow', edits.main_body(d['elements'][0]))
                cx, w = (body[0] + body[2]) / 2, (body[2] - body[0]) * 1.9
                found, _ = edits.findings_to_edits(d, [self.finding('bigger', [cx - w / 2, body[1], cx + w / 2, body[3]])], camera=camera, spec=view)
                size = found[0]['size_m']
                c, s = abs(m.cos(m.radians(yaw))), abs(m.sin(m.radians(yaw)))
                t = size[1]
                exact = min(max(footprint), (footprint[0] - s * t) / c if c > 1e-6 else 1e9, (footprint[1] - c * t) / s if s > 1e-6 else 1e9)
                self.assertLessEqual(size[0], exact + 1e-9, 'inside every limit of the compiler')
                self.assertGreater(size[0], exact - 1e-3, 'and as wide as they allow')
                self.assertTrue(self.built(edits.apply_edit(d, found[0])), 'the clamped panel is built, not dropped')

    def test_a_lit_panels_thickness_and_height_follow_the_small_step_not_the_box_height(self):
        d = self.make((6.0, 6.0), 'front', [2.4, 0.15, 2.2])
        camera, view = self.seen(d)
        body = view.box_px_raw('glow', edits.main_body(d['elements'][0]))
        cx, cy = (body[0] + body[2]) / 2, (body[1] + body[3]) / 2
        w, h = body[2] - body[0], body[3] - body[1]
        for direction, factor, k in (('bigger', 1.1, 1.4), ('bigger', 1.1, 0.7), ('smaller', 1 / 1.1, 0.7), ('smaller', 1 / 1.1, 1.4)):
            with self.subTest(direction=direction, box_height=k):
                box = [cx - w / 2, cy - k * h / 2, cx + w / 2, cy + k * h / 2]
                found, skipped = edits.findings_to_edits(d, [self.finding(direction, box)], camera=camera, spec=view)
                self.assertEqual(skipped, [])
                size = found[0]['size_m']
                self.assertAlmostEqual(size[1], 0.15 * factor, places=3)
                self.assertAlmostEqual(size[2], 2.2 * factor, places=3, msg='the height of the box does not set the height of the panel')

    def test_a_kind_without_a_resize_rule_gets_a_true_reason(self):
        d = self.make((6.0, 6.0), 'front', None)
        _, view = self.seen(d)
        without = tuple(k for k in edits.SIZE_FROM_SPEC if k != 'lit_panel')
        with unittest.mock.patch.object(edits, 'SIZE_FROM_SPEC', without):
            found, skipped = edits.findings_to_edits(d, [self.finding('bigger', None)], camera=view.camera, spec=view)
        self.assertEqual(found, [])
        self.assertIn('no resize rule for kind lit_panel', skipped[0]['reason'])
        for kind in ('lit_panel', 'canopy_frame'):
            self.assertIn(kind, edits.SIZE_FROM_SPEC)


if __name__ == '__main__':
    unittest.main()
