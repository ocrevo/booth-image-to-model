"""Pattern dispatch by type, the projected pattern and the material set (pure Python; no Blender, fake bpy only)."""
from __future__ import annotations
import copy
import hashlib
import importlib
import inspect
import json
import math
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import boothctl
import material_patterns as mp
from common import code_hash, digest, read_json, write_json
from geometry import mesh_for
from validate_spec import validate

EXAMPLE = ROOT / 'examples' / 'regression'
GRID = {'type': 'grid', 'spacing': [.14, .11], 'line_width': .015, 'line_color': [.2, .3, .4]}
PROJECTED = {'type': 'projected'}


def base_spec(fit_status='landmark_fitted'):
    spec = read_json(EXAMPLE / 'candidate_spec.json')
    spec['camera']['fit_status'] = fit_status
    return spec


def pattern_errors(result):
    return [e for e in result['errors'] if e['code'] == 'MATERIAL_PATTERN']


class Sockets(dict):
    """Sockets that exist on first use, readable by name or by position."""
    def __init__(self, node, names=()):
        super().__init__()
        self.node = node
        for name in names:
            self[name]

    def __missing__(self, name):
        socket = FakeSocket(self.node, name)
        self[name] = socket
        return socket

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class FakeSocket:
    def __init__(self, node, name):
        self.node, self.name, self.default_value = node, name, None


class FakeNode:
    def __init__(self, kind='', name=''):
        self.name, self.kind = name, kind
        self.inputs = Sockets(self)
        self.outputs = Sockets(self, ('BSDF', 'Emission', 'Color', 'UV', 'Alpha'))
        self.image = None
        self.uv_map = ''


class FakeNodes(list):
    def new(self, kind):
        node = FakeNode(kind)
        self.append(node)
        return node


class FakeLinks:
    def __init__(self):
        self.made = []

    def new(self, source, target):
        self.made.append((source, target))


class FakeMaterial:
    def __init__(self, name):
        self.name = name
        self.node_tree = SimpleNamespace(nodes=FakeNodes(), links=FakeLinks())
        self.use_nodes = False

    def kinds(self):
        return [n.kind for n in self.node_tree.nodes]


class FakeUvLayers:
    def __init__(self):
        self.names = []

    def new(self, name=''):
        self.names.append(name)


class FakeMesh:
    def __init__(self, name):
        self.name, self.materials, self.polygons, self.vertices = name, [], [], []
        self.uv_layers = FakeUvLayers()

    def from_pydata(self, *a):
        pass

    def update(self):
        pass


class FakeCurve:
    def __init__(self, name):
        self.name, self.materials = name, []


class FakeModifier:
    def __init__(self, name, kind):
        self.name, self.kind = name, kind
        self.projectors = [SimpleNamespace(object=None)]
        self.uv_layer = ''


class FakeModifiers(list):
    def new(self, name, kind):
        modifier = FakeModifier(name, kind)
        self.append(modifier)
        return modifier

    def __contains__(self, name):
        return any(m.name == name for m in self)

    def __getitem__(self, key):
        return next(m for m in self if m.name == key) if isinstance(key, str) else super().__getitem__(key)

    def remove(self, modifier):
        super().remove(modifier)


class FakeVector(tuple):
    def __new__(cls, values):
        return tuple.__new__(cls, [float(v) for v in values])

    def __sub__(self, other):
        return FakeVector([a - b for a, b in zip(self, other)])

    @property
    def length(self):
        return sum(v * v for v in self) ** .5

    def to_track_quat(self, *a):
        return FakeQuaternion()


class FakeQuaternion:
    def __init__(self, *a):
        pass

    def __matmul__(self, other):
        return self


class FakeMatrix:
    def __matmul__(self, vector):
        return vector


class FakeObject(dict):
    def __init__(self, name, data):
        super().__init__()
        self.name, self.data, self.modifiers = name, data, FakeModifiers()
        self.type = 'MESH' if isinstance(data, FakeMesh) else 'FONT'
        self.hide_render = False
        self.matrix_world = FakeMatrix()
        self.bound_box = [FakeVector((x, y, z)) for x in (0, 1) for y in (0, 1) for z in (0, 1)]


class FakeObjects:
    def __init__(self):
        self.created = []
        self.removed = []

    def new(self, name, data):
        obj = FakeObject(name, data)
        self.created.append(obj)
        return obj

    def remove(self, obj):
        self.removed.append(obj)

    def alive(self):
        return [o for o in self.created if o not in self.removed]


class FakeMeshes:
    """bpy.data.meshes; new_from_object is the text-to-mesh conversion and can be made to fail."""
    def __init__(self):
        self.fail = None
        self.converted = []

    def new(self, name):
        return FakeMesh(name)

    def new_from_object(self, obj):
        if self.fail:
            raise RuntimeError(self.fail)
        self.converted.append(obj)
        return FakeMesh(obj.name + '_converted')


class FakeCurves:
    def __init__(self):
        self.removed = []

    def new(self, name, kind):
        return FakeCurve(name)

    def remove(self, curve):
        self.removed.append(curve)


class FakeImages(list):
    def load(self, path, check_existing=False):
        image = SimpleNamespace(name=Path(path).name, filepath=str(path), has_data=True, pixels=[0], packed=False, size=(640, 480),
                                filepath_raw='', save=lambda: None)
        image.pack = lambda: setattr(image, 'packed', True)
        self.append(image)
        return image

    def get(self, name):
        return next((i for i in self if i.name == name), None)


def make_fake_bpy():
    data = SimpleNamespace(
        meshes=FakeMeshes(), curves=FakeCurves(),
        objects=FakeObjects(), materials=SimpleNamespace(new=FakeMaterial), images=FakeImages(),
        worlds=mock.MagicMock(), collections=mock.MagicMock(), cameras=mock.MagicMock(), lights=mock.MagicMock(), texts=mock.MagicMock())

    def save(filepath=None, **kw):
        Path(filepath).write_bytes(b'fake blend')

    bpy = types.ModuleType('bpy')
    bpy.data = data
    bpy.app = SimpleNamespace(background=True, version_string='fake')
    bpy.context = mock.MagicMock()
    bpy.ops = SimpleNamespace(wm=SimpleNamespace(read_factory_settings=lambda **kw: None, save_as_mainfile=save))
    return bpy


def fake_modules(bpy):
    mathutils = types.ModuleType('mathutils')
    mathutils.Vector = FakeVector
    mathutils.Quaternion = FakeQuaternion
    extras = types.ModuleType('bpy_extras')
    utils = types.ModuleType('bpy_extras.object_utils')
    utils.world_to_camera_view = lambda *a: SimpleNamespace(x=.5, y=.5, z=1.0)
    extras.object_utils = utils
    return {'bpy': bpy, 'mathutils': mathutils, 'bpy_extras': extras, 'bpy_extras.object_utils': utils}


class FakeBlenderCase(unittest.TestCase):
    """blender_build imported against a fake bpy that records what the builder asks of it."""
    def setUp(self):
        self.bpy = make_fake_bpy()
        patcher = mock.patch.dict(sys.modules, fake_modules(self.bpy))
        patcher.start()
        self.addCleanup(patcher.stop)
        sys.modules.pop('blender_build', None)
        self.bb = importlib.import_module('blender_build')
        self.collections = {n: SimpleNamespace(objects=SimpleNamespace(link=lambda o: None))
                            for n in ('BOOTH_STRUCTURE', 'BOOTH_FURNITURE', 'BOOTH_GRAPHICS', 'RENDER_RIG')}
        self.source = EXAMPLE / 'reference.png'

    def materials_of(self, built):
        return {ident: obj.data.materials[0] for ident, obj in built.items()}

    def build(self, look):
        return self.bb.build_parts(look, 'single_image_estimate', self.collections)

    def spec_with_projected_part(self, ident='rear_wall'):
        spec = base_spec()
        for c in spec['components']:
            if c['id'] == ident:
                c['material']['pattern'] = copy.deepcopy(PROJECTED)
        return spec


class DispatchTests(unittest.TestCase):
    def test_pattern_dispatch_by_type_keeps_grid_signature(self):
        self.assertIn('grid', mp.PATTERNS)
        self.assertIn('projected', mp.PATTERNS)
        parameters, nodes = mp.PATTERNS['grid']
        self.assertIs(parameters, mp.grid_parameters)
        self.assertIs(nodes, mp.apply_grid)
        self.assertEqual(list(inspect.signature(mp.apply_grid).parameters), ['nodes', 'links', 'shader', 'material', 'component'])
        self.assertEqual(list(inspect.signature(mp.grid_parameters).parameters), ['material', 'component'])
        self.assertTrue(callable(mp.grid_graph_signature))
        for kind, (parameters, nodes) in mp.PATTERNS.items():
            with self.subTest(kind=kind):
                self.assertTrue(callable(parameters) and callable(nodes))
                self.assertEqual(list(inspect.signature(nodes).parameters), ['nodes', 'links', 'shader', 'material', 'component'])
        material = {'color': [1, .8, .3, 1], 'pattern': copy.deepcopy(GRID)}
        component = {'kind': 'box', 'params': {}}
        self.assertEqual(mp.pattern_parameters(material, component), mp.grid_parameters(material, component))
        self.assertIsNone(mp.pattern_parameters({'color': [1, 1, 1, 1]}, component))

    def test_apply_pattern_dispatches_through_the_table(self):
        calls = []
        table = {'grid': (mp.grid_parameters, lambda *a: calls.append('grid')),
                 'projected': (mp.projected_parameters, lambda *a: calls.append('projected'))}
        component = {'kind': 'box', 'params': {}}
        with mock.patch.dict(mp.PATTERNS, table):
            mp.apply_pattern(None, None, None, {'color': [1, 1, 1, 1], 'pattern': copy.deepcopy(PROJECTED)}, component)
            mp.apply_pattern(None, None, None, {'color': [1, 1, 1, 1], 'pattern': copy.deepcopy(GRID)}, component)
            mp.apply_pattern(None, None, None, {'color': [1, 1, 1, 1]}, component)
        self.assertEqual(calls, ['projected', 'grid'])

    def test_unknown_pattern_type_lists_known_types(self):
        with self.assertRaises(ValueError) as caught:
            mp.pattern_parameters({'color': [1, 1, 1, 1], 'pattern': {'type': 'bitmap'}}, {'kind': 'box', 'params': {}})
        for kind in mp.PATTERNS:
            self.assertIn(kind, str(caught.exception))
        self.assertIn('grid', str(caught.exception))
        self.assertIn('projected', str(caught.exception))
        spec = base_spec()
        spec['components'][3]['material']['pattern'] = {'type': 'bitmap'}
        result = validate(spec)
        self.assertFalse(result['valid'])
        messages = ' '.join(e['message'] for e in pattern_errors(result))
        self.assertIn('grid', messages)
        self.assertIn('projected', messages)


class ProjectedValidationTests(unittest.TestCase):
    def test_projected_requires_fitted_camera(self):
        schema = read_json(ROOT / 'schemas' / 'booth-spec-v2.schema.json')
        for status in ('landmark_fitted', 'reviewed'):
            spec = base_spec(status)
            spec['components'][3]['material']['pattern'] = copy.deepcopy(PROJECTED)
            with self.subTest(status=status):
                result = validate(spec)
                self.assertTrue(result['valid'], result['errors'])
                jsonschema.Draft202012Validator(schema).validate(spec)
        spec = base_spec('unfitted')
        spec['components'][3]['material']['pattern'] = copy.deepcopy(PROJECTED)
        result = validate(spec)
        self.assertFalse(result['valid'])
        self.assertTrue(pattern_errors(result), result['errors'])
        self.assertIn('fit', pattern_errors(result)[0]['message'])
        # the same spec without the pattern is still fine, so the camera is the only cause
        del spec['components'][3]['material']['pattern']
        self.assertTrue(validate(spec)['valid'])

    def test_projected_takes_no_other_fields(self):
        spec = base_spec()
        spec['components'][3]['material']['pattern'] = {'type': 'projected', 'spacing': [1, 1]}
        self.assertTrue(pattern_errors(validate(spec)))
        schema = read_json(ROOT / 'schemas' / 'booth-spec-v2.schema.json')
        self.assertTrue(list(jsonschema.Draft202012Validator(schema).iter_errors(spec)))

    def test_grid_still_validates_with_an_unfitted_camera(self):
        spec = base_spec('unfitted')
        spec['components'][3]['material']['pattern'] = copy.deepcopy(GRID)
        self.assertTrue(validate(spec)['valid'])


class MaterialSetTests(unittest.TestCase):
    def test_material_set_projected_rewrites_every_part_in_memory_only(self):
        spec = base_spec()
        spec['components'][3]['material']['pattern'] = copy.deepcopy(GRID)
        snapshot = copy.deepcopy(spec)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'spec.json'
            write_json(path, spec)
            before = path.read_bytes()
            result = mp.material_set(spec, 'projected')
            self.assertEqual(path.read_bytes(), before)
        self.assertEqual(spec, snapshot)
        self.assertIsNot(result, spec)
        self.assertEqual(len(result['components']), len(spec['components']))
        for new, old in zip(result['components'], spec['components']):
            self.assertEqual(new['material']['pattern'], PROJECTED)
            self.assertEqual(new['material']['color'], old['material']['color'])
            self.assertEqual(new['id'], old['id'])
        self.assertTrue(validate(result)['valid'])
        with self.assertRaises(ValueError):
            mp.material_set(spec, 'sepia')

    def test_material_set_neutral_returns_the_spec_unchanged(self):
        spec = base_spec('unfitted')
        spec['components'][3]['material']['pattern'] = copy.deepcopy(GRID)
        snapshot = copy.deepcopy(spec)
        self.assertEqual(mp.material_set(spec, 'neutral'), snapshot)
        self.assertEqual(spec, snapshot)

    def test_projected_parts_are_found_whatever_the_set_name(self):
        spec = base_spec()
        self.assertFalse(mp.has_projected(spec))
        self.assertEqual(mp.projected_parts(spec), [])
        spec['components'][3]['material']['pattern'] = copy.deepcopy(PROJECTED)
        spec['components'][4]['material']['pattern'] = copy.deepcopy(GRID)
        self.assertTrue(mp.has_projected(spec))
        self.assertEqual(mp.projected_parts(spec), [spec['components'][3]['id']])
        self.assertTrue(mp.has_projected(mp.material_set(spec, 'projected')))
        self.assertEqual(len(mp.projected_parts(mp.material_set(spec, 'projected'))), len(spec['components']))

    def test_projected_keeps_part_alpha(self):
        spec = base_spec()
        spec['components'][3]['material']['color'] = [.6, .7, .8, .35]
        result = mp.material_set(spec, 'projected')
        self.assertEqual(result['components'][3]['material']['color'], [.6, .7, .8, .35])
        component = result['components'][3]
        params = mp.pattern_parameters(component['material'], component)
        self.assertAlmostEqual(params['alpha'], .35)
        opaque = result['components'][0]
        self.assertAlmostEqual(mp.pattern_parameters(opaque['material'], opaque)['alpha'], 1.0)
        # the node builder sends the picture to the colour sockets and sets the part alpha
        nodes, links, shader = FakeNodes(), FakeLinks(), FakeNode('shader', 'shader')
        shader.inputs['Alpha'].default_value = 1.0
        fake_bpy = types.SimpleNamespace(data=types.SimpleNamespace(images=types.SimpleNamespace(get=lambda name: 'IMG')))
        with mock.patch.dict(sys.modules, {'bpy': fake_bpy}):
            mp.apply_projected(nodes, links, shader, component['material'], component)
        self.assertAlmostEqual(shader.inputs['Alpha'].default_value, .35)
        kinds = [n.kind for n in nodes]
        self.assertIn('ShaderNodeTexImage', kinds)
        self.assertIn('ShaderNodeUVMap', kinds)
        targets = [(to.node.name, to.name) for _, to in links.made]
        self.assertIn(('shader', 'Base Color'), targets)
        self.assertNotIn(('shader', 'Alpha'), targets)


class BuildTests(FakeBlenderCase):
    def test_a_projected_part_written_in_the_spec_file_builds_in_the_default_set(self):
        spec = self.spec_with_projected_part('rear_wall')
        look = mp.material_set(spec, 'neutral')
        self.assertTrue(self.bb.prepare_projection(look, self.source))
        image = self.bpy.data.images.get(mp.SOURCE_IMAGE)
        self.assertIsNotNone(image)
        self.assertTrue(image.packed)
        built = self.build(look)
        materials = self.materials_of(built)
        self.assertIn('ShaderNodeTexImage', materials['rear_wall'].kinds())
        for ident, material in materials.items():
            if ident != 'rear_wall':
                self.assertNotIn('ShaderNodeTexImage', material.kinds(), ident)
        self.assertEqual(built['rear_wall'].data.uv_layers.names, [mp.UV_LAYER])
        self.assertEqual(built['reception'].data.uv_layers.names, [])
        camera = object()
        skipped = self.bb.add_projectors(built, look, camera, (640, 480))
        self.assertEqual(skipped, [])
        with_modifier = [i for i, o in built.items() if len(o.modifiers)]
        self.assertEqual(with_modifier, ['rear_wall'])
        modifier = built['rear_wall'].modifiers[0]
        self.assertEqual(modifier.kind, 'UV_PROJECT')
        self.assertIs(modifier.projectors[0].object, camera)
        self.assertEqual((modifier.aspect_x, modifier.aspect_y), (640.0, 480.0))
        self.assertEqual(modifier.uv_layer, mp.UV_LAYER)

    def test_a_neutral_build_without_projected_parts_prepares_nothing(self):
        spec = base_spec()
        look = mp.material_set(spec, 'neutral')
        self.assertFalse(self.bb.prepare_projection(look, self.source))
        self.assertIsNone(self.bpy.data.images.get(mp.SOURCE_IMAGE))
        self.assertEqual(len(self.bpy.data.images), 0)
        built = self.build(look)
        for obj in built.values():
            self.assertEqual(obj.data.uv_layers.names, [])
        self.assertEqual(self.bb.add_projectors(built, look, object(), (640, 480)), [])
        self.assertTrue(all(len(o.modifiers) == 0 for o in built.values()))

    def test_projected_build_materials_come_from_the_projected_look(self):
        spec = base_spec()
        look = mp.material_set(spec, 'projected')
        self.bb.prepare_projection(look, self.source)
        built = self.build(look)
        self.assertEqual(set(built), {c['id'] for c in spec['components']})
        for ident, material in self.materials_of(built).items():
            with self.subTest(part=ident):
                self.assertIn('ShaderNodeTexImage', material.kinds())
                self.assertFalse([k for k in material.kinds() if k == 'ShaderNodeMath'])
        # the unrewritten spec builds neutral colours, so the look is what makes the difference
        neutral = self.build(spec)
        self.assertTrue(all('ShaderNodeTexImage' not in m.kinds() for m in self.materials_of(neutral).values()))

    def spec_with_text(self, projected=True):
        spec = self.spec_with_projected_part('rear_wall') if projected else base_spec()
        text = copy.deepcopy(spec['components'][3])
        text.update(id='sign_text', kind='text', params={'body': 'A', 'size': .1})
        text['material'].pop('pattern', None)
        if projected:
            text['material']['pattern'] = copy.deepcopy(PROJECTED)
        spec['components'].append(text)
        return spec

    def test_a_projected_text_part_is_converted_to_a_mesh_and_then_projected_like_any_other(self):
        look = mp.material_set(self.spec_with_text(), 'neutral')
        self.bb.prepare_projection(look, self.source)
        built = self.build(look)
        text = built['sign_text']
        self.assertEqual(len(self.bpy.data.meshes.converted), 1)
        self.assertEqual(text.type, 'MESH')
        self.assertEqual(text.name, 'sign_text')
        self.assertEqual(text['primitive_kind'], 'text')
        self.assertEqual(text.data.uv_layers.names, [mp.UV_LAYER])
        self.assertIn('ShaderNodeTexImage', text.data.materials[0].kinds())
        # the temporary text object and the curve it came from are gone, so one object per part is left
        alive = [o.name for o in self.bpy.data.objects.alive() if o.get('ownership') == 'booth']
        self.assertEqual(sorted(alive), sorted(built))
        self.assertEqual(len(self.bpy.data.curves.removed), 1)
        camera = object()
        self.assertEqual(self.bb.add_projectors(built, look, camera, (640, 480)), [])
        self.assertEqual([m.kind for m in text.modifiers], ['UV_PROJECT'])
        self.assertIs(text.modifiers[0].projectors[0].object, camera)
        self.assertEqual(text.modifiers[0].uv_layer, mp.UV_LAYER)
        self.assertFalse(text.hide_render)

    def test_a_failed_text_conversion_hides_the_part_and_records_it_with_the_reason(self):
        look = mp.material_set(self.spec_with_text(), 'neutral')
        self.bb.prepare_projection(look, self.source)
        self.bpy.data.meshes.fail = 'no evaluated mesh for this font'
        built = self.build(look)
        text = built['sign_text']
        self.assertEqual(text.type, 'FONT')
        self.assertEqual(text.data.name, 'sign_text')
        skipped = self.bb.add_projectors(built, look, object(), (640, 480))
        self.assertEqual([s['id'] for s in skipped], ['sign_text'])
        self.assertIn('conversion', skipped[0]['reason'])
        self.assertIn('no evaluated mesh for this font', skipped[0]['reason'])
        self.assertTrue(text.hide_render)
        self.assertEqual(len(text.modifiers), 0)
        # the other projected part is unaffected
        self.assertFalse(built['rear_wall'].hide_render)
        self.assertEqual(len(built['rear_wall'].modifiers), 1)
        self.assertIn('hidden', self.bb.PROJECTED_UV_NOTE)
        # the temporary object is gone; the curve stays because the part still uses it
        self.assertEqual(self.bpy.data.curves.removed, [])
        alive = [o.name for o in self.bpy.data.objects.alive() if o.get('ownership') == 'booth']
        self.assertEqual(sorted(alive), sorted(built))

    def test_a_neutral_build_does_not_convert_text(self):
        look = mp.material_set(self.spec_with_text(projected=False), 'neutral')
        self.assertFalse(self.bb.prepare_projection(look, self.source))
        built = self.build(look)
        self.assertEqual(self.bpy.data.meshes.converted, [])
        self.assertEqual(self.bpy.data.curves.removed, [])
        self.assertEqual(self.bpy.data.objects.removed, [])
        self.assertEqual(built['sign_text'].type, 'FONT')
        self.assertFalse(built['sign_text'].hide_render)
        self.assertEqual(self.bb.add_projectors(built, look, object(), (640, 480)), [])


class MainTests(FakeBlenderCase):
    """blender_build.main() end to end against the fake bpy: temp spec, temp output folder, no Blender."""
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        shutil.copy(EXAMPLE / 'reference.png', self.dir / 'reference.png')
        self.out = self.dir / 'run'

    def run_main(self, spec, *extra):
        write_json(self.dir / 'spec.json', spec)
        argv = ['blender', '--', '--spec', str(self.dir / 'spec.json'), '--out', str(self.out), *extra]
        with mock.patch.object(sys, 'argv', argv):
            self.bb.main()
        return read_json(self.out / 'build_report.json')

    def parts(self):
        return {o.name: o for o in self.bpy.data.objects.created if o.get('ownership') == 'booth'}

    def test_main_neutral_set_without_projected_parts_prepares_no_projection(self):
        report = self.run_main(base_spec('unfitted'))
        self.assertEqual(report['material_set'], 'neutral')
        self.assertNotIn('projected_uv_skipped', report)
        self.assertIsNone(self.bpy.data.images.get(mp.SOURCE_IMAGE))
        parts = self.parts()
        self.assertEqual(len(parts), 8)
        for ident, obj in parts.items():
            with self.subTest(part=ident):
                self.assertEqual(len(obj.modifiers), 0)
                self.assertEqual(obj.data.uv_layers.names, [])
                self.assertNotIn('ShaderNodeTexImage', obj.data.materials[0].kinds())

    def test_main_projected_set_projects_every_mesh_part_and_leaves_the_spec_file_alone(self):
        spec = base_spec()
        report = self.run_main(spec, '--material-set', 'projected')
        self.assertEqual(report['material_set'], 'projected')
        self.assertEqual(report['projected_uv_skipped'], [])
        self.assertTrue(self.bpy.data.images.get(mp.SOURCE_IMAGE).packed)
        parts = self.parts()
        self.assertEqual(len(parts), 8)
        for ident, obj in parts.items():
            with self.subTest(part=ident):
                self.assertIn('ShaderNodeTexImage', obj.data.materials[0].kinds())
                self.assertEqual(obj.data.uv_layers.names, [mp.UV_LAYER])
                self.assertEqual([m.kind for m in obj.modifiers], ['UV_PROJECT'])
                self.assertEqual((obj.modifiers[0].aspect_x, obj.modifiers[0].aspect_y), (640.0, 480.0))
        self.assertEqual(read_json(self.out / 'spec.json'), spec)
        self.assertEqual(read_json(self.dir / 'spec.json'), spec)

    def spec_with_text(self):
        spec = base_spec()
        text = copy.deepcopy(spec['components'][3])
        text.update(id='sign_text', kind='text', params={'body': 'A', 'size': .1})
        spec['components'].append(text)
        return spec

    def test_main_projected_set_converts_text_parts_and_projects_them(self):
        report = self.run_main(self.spec_with_text(), '--material-set', 'projected')
        self.assertEqual(report['projected_uv_skipped'], [])
        text = self.parts()['sign_text']
        self.assertEqual(text.type, 'MESH')
        self.assertEqual(text.data.uv_layers.names, [mp.UV_LAYER])
        self.assertEqual([m.kind for m in text.modifiers], ['UV_PROJECT'])
        self.assertFalse(text.hide_render)

    def test_main_projected_set_hides_a_text_part_that_cannot_be_converted_and_reports_it(self):
        self.bpy.data.meshes.fail = 'cannot convert'
        report = self.run_main(self.spec_with_text(), '--material-set', 'projected')
        self.assertEqual([s['id'] for s in report['projected_uv_skipped']], ['sign_text'])
        self.assertIn('cannot convert', report['projected_uv_skipped'][0]['reason'])
        self.assertIn('hidden', report['projected_uv_note'])
        self.assertTrue(self.parts()['sign_text'].hide_render)

    def test_main_neutral_set_leaves_text_alone(self):
        report = self.run_main(self.spec_with_text())
        self.assertEqual(self.bpy.data.meshes.converted, [])
        self.assertEqual(self.parts()['sign_text'].type, 'FONT')
        self.assertNotIn('projected_uv_skipped', report)

    def test_main_neutral_set_prepares_a_projected_part_written_in_the_spec_file(self):
        report = self.run_main(self.spec_with_projected_part('rear_wall'))
        self.assertEqual(report['material_set'], 'neutral')
        self.assertEqual(report['projected_uv_skipped'], [])
        self.assertTrue(self.bpy.data.images.get(mp.SOURCE_IMAGE).packed)
        for ident, obj in self.parts().items():
            with self.subTest(part=ident):
                projected = ident == 'rear_wall'
                self.assertEqual('ShaderNodeTexImage' in obj.data.materials[0].kinds(), projected)
                self.assertEqual(len(obj.modifiers), 1 if projected else 0)
                self.assertEqual(obj.data.uv_layers.names, [mp.UV_LAYER] if projected else [])

    def test_main_projected_set_with_an_unfitted_camera_is_refused_and_builds_nothing(self):
        with self.assertRaises(ValueError) as caught:
            self.run_main(base_spec('unfitted'), '--material-set', 'projected')
        self.assertIn('MATERIAL_PATTERN', str(caught.exception))
        self.assertEqual(self.bpy.data.objects.created, [])
        self.assertIsNone(self.bpy.data.images.get(mp.SOURCE_IMAGE))
        self.assertFalse((self.out / 'booth.blend').exists())
        self.assertFalse(self.out.exists())


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        shutil.copy(EXAMPLE / 'reference.png', self.dir / 'reference.png')
        self.spec = self.dir / 'spec.json'

    def run_preview(self, fit_status='landmark_fitted', spec=None, **kw):
        write_json(self.spec, spec or base_spec(fit_status))
        seen = []

        def fake_run(cmd, **_):
            seen.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 1, stdout='', stderr='stopped for the test')

        with mock.patch.object(boothctl.subprocess, 'run', fake_run), mock.patch.object(boothctl, 'find_blender', lambda b=None: 'blender-stand-in'):
            result = boothctl.preview(self.spec, self.dir / 'out', **kw)
        return result, seen

    def test_boothctl_preview_passes_material_set(self):
        result, seen = self.run_preview('landmark_fitted', engine='cycles', material_set='projected')
        self.assertEqual(result['status'], 'build_failed', result)
        self.assertEqual(len(seen), 1)
        cmd = seen[0]
        self.assertEqual(cmd[cmd.index('--material-set') + 1], 'projected')
        self.assertGreater(cmd.index('--material-set'), cmd.index('--'))
        # the spec file on disk is what Blender gets; the rewrite happens inside the build
        self.assertEqual(cmd[cmd.index('--spec') + 1], str(self.spec.resolve()))
        # an unfitted camera is refused before Blender starts
        result, seen = self.run_preview('unfitted', engine='cycles', material_set='projected')
        self.assertEqual(result['status'], 'invalid_spec')
        self.assertEqual(seen, [])

    def test_default_preview_command_line_is_unchanged(self):
        for kwargs in ({}, {'material_set': 'neutral'}, {'look': 'neutral'}):
            with self.subTest(kwargs=kwargs):
                _, seen = self.run_preview(engine='cycles', **kwargs)
                run = self.dir / 'out' / 'preview_001'
                self.assertEqual(seen[0], ['blender-stand-in', '--background', '--factory-startup', '--disable-autoexec', '--python-exit-code', '1',
                                           '--python', str(Path(boothctl.__file__).resolve().parent / 'blender_build.py'), '--', '--spec', str(self.spec.resolve()),
                                           '--out', str(run), '--preview', '--preview-engine', 'cycles', '--samples', '8'])
                self.assertNotIn('--material-set', seen[0])
                self.assertNotIn('--look', seen[0])
                shutil.rmtree(self.dir / 'out')

    def test_preview_look_defaults_to_neutral_and_rejects_unknown_values(self):
        script = str(ROOT / 'scripts' / 'boothctl.py')
        help_text = subprocess.run([sys.executable, script, 'preview', '--help'], capture_output=True, text=True, timeout=60).stdout
        self.assertIn('--look', help_text)
        self.assertIn('dark_hall', help_text)
        self.assertIn('or the dark_hall look', ' '.join(help_text.split()))      # the --engine help says the dark look uses Cycles
        proc = subprocess.run([sys.executable, script, 'preview', '--spec', 'a.json', '--out-root', 'b', '--look', 'moody'],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 2)
        self.assertIn('invalid choice', proc.stderr)
        # nothing given means neutral: the function default, and no flag on the Blender command line
        self.assertEqual(mp.LOOKS[0], 'neutral')
        self.assertEqual(inspect.signature(boothctl.preview).parameters['look'].default, 'neutral')
        _, seen = self.run_preview(engine='cycles')
        self.assertNotIn('--look', seen[0])

    def test_boothctl_preview_passes_look(self):
        result, seen = self.run_preview(engine='cycles', look='dark_hall')
        self.assertEqual(result['status'], 'build_failed', result)
        self.assertEqual(len(seen), 1)
        cmd = seen[0]
        self.assertEqual(cmd[cmd.index('--look') + 1], 'dark_hall')
        self.assertGreater(cmd.index('--look'), cmd.index('--'))
        self.assertNotIn('--material-set', cmd)
        # with no engine given the dark look runs Cycles; Workbench cannot show lights and is refused, not switched
        _, seen = self.run_preview(look='dark_hall')
        self.assertEqual(seen[0][seen[0].index('--preview-engine') + 1], 'cycles')
        result, seen = self.run_preview(engine='workbench', look='dark_hall')
        self.assertEqual(seen, [])
        self.assertEqual(result['status'], 'invalid_options')
        self.assertIn('Cycles', result['message'])
        # it combines with the projected material set
        _, seen = self.run_preview(look='dark_hall', material_set='projected')
        self.assertIn('--look', seen[0])
        self.assertIn('--material-set', seen[0])
        # an unknown look given to the function is refused before Blender starts
        result, seen = self.run_preview(engine='cycles', look='moody')
        self.assertEqual(result['status'], 'invalid_options')
        self.assertEqual(seen, [])

    def test_boothctl_main_look_defaults_to_neutral_and_passes_the_flag_on(self):
        for extra, expected in (([], 'neutral'), (['--look', 'dark_hall'], 'dark_hall')):
            with self.subTest(extra=extra):
                seen = {}

                def fake_preview(*a, **kw):
                    seen.update(kw)
                    return {'status': 'preview_ready'}

                argv = ['boothctl', 'preview', '--spec', 'a.json', '--out-root', 'b'] + extra
                with mock.patch.object(boothctl, 'preview', fake_preview), mock.patch.object(sys, 'argv', argv), \
                        mock.patch('builtins.print'):
                    self.assertEqual(boothctl.main(), 0)
                self.assertEqual(seen['look'], expected)

    def test_dark_hall_preview_marks_its_compare_numbers_as_not_comparable(self):
        import preview_compare
        write_json(self.spec, base_spec())

        def fake_run(cmd, **_):
            run = Path(cmd[cmd.index('--out') + 1])
            run.mkdir(parents=True)
            write_json(run / 'build_report.json', {'stage_seconds': {'total_in_script': 1.0}})
            return subprocess.CompletedProcess(cmd, 0, stdout='', stderr='')

        def fake_sheet(run):
            summary = {'sheet': str(run / 'compare_sheet.png'), 'landmark_median_px': 1.0, 'landmark_max_px': 2.0, 'landmarks': [],
                       'booth_pixel_fraction': 0.4, 'client_look': None}
            write_json(Path(run) / 'compare.json', summary)
            return summary

        results = {}
        for look in ('neutral', 'dark_hall'):
            with mock.patch.object(boothctl.subprocess, 'run', fake_run), mock.patch.object(boothctl, 'find_blender', lambda b=None: 'blender-stand-in'), \
                    mock.patch.object(preview_compare, 'sheet', fake_sheet):
                result = boothctl.preview(self.spec, self.dir / ('out_' + look), engine='cycles', look=look)
            self.assertEqual(result['status'], 'preview_ready', result)
            results[look] = (result, read_json(Path(result['run']) / 'compare.json'))
        neutral_compare = results['neutral'][1]
        self.assertNotIn('look', neutral_compare)
        self.assertNotIn('look', results['neutral'][0])
        dark_result, dark_compare = results['dark_hall']
        self.assertEqual(dark_compare['look'], 'dark_hall')
        self.assertIn('not comparable', dark_compare['look_note'])
        self.assertIn('booth_pixel_fraction', dark_compare['look_note'])
        self.assertEqual(dark_result['look'], 'dark_hall')

    def test_build_passes_look_after_the_double_dash_unchanged(self):
        # build forwards everything after -- as before; the look needs no change in boothctl
        captured = []
        with mock.patch.object(boothctl, 'run_blender_script', lambda script, args, blender=None, timeout=0: captured.append((script, list(args))) or (0, '')):
            with mock.patch.object(sys, 'argv', ['boothctl', 'build', '--spec', 's.json', '--out', 'o', '--', '--render', '--look', 'dark_hall']):
                boothctl.main()
        self.assertEqual(captured[0][0], 'blender_build.py')
        self.assertEqual(captured[0][1], ['--spec', 's.json', '--out', 'o', '--render', '--look', 'dark_hall'])

    def test_projected_preview_uses_cycles_without_an_engine_and_refuses_workbench(self):
        _, seen = self.run_preview(material_set='projected')
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][seen[0].index('--preview-engine') + 1], 'cycles')
        result, seen = self.run_preview(engine='workbench', material_set='projected')
        self.assertEqual(seen, [])
        self.assertEqual(result['status'], 'invalid_options')
        self.assertIn('Cycles', result['message'])
        # the customer look does not override an explicit workbench either: it is refused, not switched silently
        result, seen = self.run_preview(engine='workbench', client_look=True, material_set='projected')
        self.assertEqual(seen, [])
        self.assertEqual(result['status'], 'invalid_options')
        # --client-look alone (no engine given) runs Cycles
        _, seen = self.run_preview(client_look=True, material_set='projected')
        self.assertEqual(seen[0][seen[0].index('--preview-engine') + 1], 'cycles')
        # without a projected look the default stays Workbench
        _, seen = self.run_preview()
        self.assertEqual(seen[0][seen[0].index('--preview-engine') + 1], 'workbench')

    def test_a_projected_part_in_the_spec_file_also_previews_with_cycles(self):
        spec = base_spec()
        spec['components'][3]['material']['pattern'] = copy.deepcopy(PROJECTED)
        _, seen = self.run_preview(spec=spec)
        self.assertEqual(seen[0][seen[0].index('--preview-engine') + 1], 'cycles')
        self.assertNotIn('--material-set', seen[0])

    def test_boothctl_preview_rejects_an_unknown_material_set(self):
        script = str(ROOT / 'scripts' / 'boothctl.py')
        proc = subprocess.run([sys.executable, script, 'preview', '--spec', 'a.json', '--out-root', 'b', '--material-set', 'sepia'],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 2)
        self.assertIn('invalid choice', proc.stderr)
        help_text = subprocess.run([sys.executable, script, 'preview', '--help'], capture_output=True, text=True, timeout=60).stdout
        self.assertIn('--material-set', help_text)


def lit_part(ident, kind='box', role='led_strip', params=None, location=(0, 0, 0), rotation=(0, 0, 0), emission=4.0, color=(1, .9, .6, 1)):
    return {'id': ident, 'kind': kind, 'role': role, 'params': params or {'size': [1.0, 0.05, 0.1]}, 'location': list(location),
            'rotation_deg': list(rotation), 'material': {'color': list(color), 'emission': emission}}


def lit_spec(*parts):
    return {'components': list(parts)}


class DarkHallTests(unittest.TestCase):
    def test_dark_hall_settings_are_described_without_bpy(self):
        settings = mp.dark_hall_settings()
        self.assertIsInstance(settings, dict)
        self.assertAlmostEqual(settings['world_color'], 0.02)
        self.assertAlmostEqual(settings['floor_color'], 0.05)
        self.assertAlmostEqual(settings['floor_roughness'], 0.7)      # micro-reflective, not glossy
        self.assertTrue(0 < settings['rig_energy_scale'] < 1)
        self.assertGreater(settings['emission_multiplier'], 1)
        self.assertGreater(settings['preview_sample_cap'], 8)
        self.assertEqual(settings['exposure_offset'], 0)
        # the description is a copy: changing it cannot change the next call
        settings['world_color'] = 1
        self.assertAlmostEqual(mp.dark_hall_settings()['world_color'], 0.02)
        self.assertEqual(mp.look_settings('dark_hall'), mp.dark_hall_settings())

    def test_dark_hall_lights_every_emissive_part_through_its_own_surface(self):
        ring = lit_part('top_ring', kind='rounded_ring', role='fascia',
                        params={'outer_size': [5.0, 3.0], 'inner_size': [4.2, 2.2], 'outer_radius': 0.5, 'inner_radius': 0.3, 'height': 0.2, 'segments_per_corner': 8},
                        location=(0, 0, 3), emission=1.5)
        strip = lit_part('floor_strip', location=(0, 0, 0.025), emission=4.0)
        text = lit_part('sign', kind='text', role='logo', params={'body': 'X', 'size': 0.2}, emission=2.0)
        spec = lit_spec(ring, strip, text, lit_part('dull', emission=0))
        before = copy.deepcopy(spec)
        # emissive parts (text included) are counted; none of them gets an area light, because the glowing surface itself lights the hall
        self.assertEqual(mp.emissive_parts(spec), ['top_ring', 'floor_strip', 'sign'])
        self.assertEqual(mp.dark_hall_lights(spec), [])
        report = mp.look_report(spec, 'dark_hall')
        self.assertEqual(report, {'look': 'dark_hall', 'emissive_parts': 3, 'derived_lights': 0, 'unhandled': []})
        self.assertGreater(mp.dark_hall_settings()['emission_multiplier'], 1)
        self.assertEqual(spec, before)
        # the neutral look reports the same counts and derives nothing
        self.assertEqual(mp.look_report(spec, 'neutral'), {'look': 'neutral', 'emissive_parts': 3, 'derived_lights': 0, 'unhandled': []})
        # no emissive part: still a normal report
        self.assertEqual(mp.look_report(lit_spec(lit_part('plain', emission=0)), 'dark_hall'),
                         {'look': 'dark_hall', 'emissive_parts': 0, 'derived_lights': 0, 'unhandled': []})

    def test_spotlight_parts_get_downward_spot_lights(self):
        spot = lit_part('spot_0', kind='cylinder', role='spotlight', params={'radius': 0.04, 'height': 0.12}, location=(1, 2, 2.94), emission=4.0, color=(1, 1, .95, 1))
        spec = lit_spec(spot, lit_part('strip'))
        lights = mp.dark_hall_lights(spec)
        # one spot for the spotlight part, nothing for the strip
        self.assertEqual([(l['type'], l['part']) for l in lights], [('SPOT', 'spot_0')])
        light = lights[0]
        self.assertEqual(light['direction'], [0, 0, -1])
        self.assertEqual(light['spot_angle_deg'], mp.SPOT_CONE_DEG)
        self.assertAlmostEqual(light['position'][0], 1)
        self.assertAlmostEqual(light['position'][1], 2)
        self.assertLess(light['position'][2], 2.94 - 0.06 + 1e-9)    # at or just under the lower end of the body
        self.assertGreater(light['position'][2], 2.94 - 0.06 - 0.05)
        self.assertEqual(light['color'], [1, 1, .95])
        # power follows emission and the lamp disc area: the emission factor is part of the rule
        self.assertAlmostEqual(light['power_w'], mp.SPOT_WATT_PER_EMISSION_M2 * 4.0 * math.pi * 0.04 ** 2)
        half = mp.dark_hall_lights(lit_spec(lit_part('s', kind='cylinder', role='spotlight', params={'radius': 0.04, 'height': 0.12}, emission=2.0)))[0]
        self.assertAlmostEqual(light['power_w'] / half['power_w'], 2.0)
        wide = mp.dark_hall_lights(lit_spec(lit_part('s', kind='cylinder', role='spotlight', params={'radius': 0.08, 'height': 0.12}, emission=4.0)))[0]
        self.assertAlmostEqual(wide['power_w'] / light['power_w'], 4.0)
        # a spotlight with no emission is not a light
        self.assertEqual(mp.dark_hall_lights(lit_spec(lit_part('s', kind='cylinder', role='spotlight', params={'radius': .04, 'height': .12}, emission=0))), [])
        self.assertEqual(mp.look_report(spec, 'dark_hall')['derived_lights'], 1)
        self.assertEqual(mp.look_report(spec, 'dark_hall')['emissive_parts'], 2)

    def test_a_spotlight_part_that_cannot_become_a_light_is_listed_not_skipped(self):
        broken = lit_part('spot_text', kind='text', role='spotlight', params={'body': 'X', 'size': 0.2})
        report = mp.look_report(lit_spec(broken), 'dark_hall')
        self.assertEqual(report['derived_lights'], 0)
        self.assertEqual([u['id'] for u in report['unhandled']], ['spot_text'])
        self.assertTrue(report['unhandled'][0]['reason'])

    def test_neutral_look_derives_no_lights_and_keeps_the_rig(self):
        spec = lit_spec(lit_part('strip'), lit_part('spot', kind='cylinder', role='spotlight', params={'radius': .04, 'height': .12}))
        self.assertEqual(mp.look_lights(spec, 'neutral'), [])
        self.assertEqual(mp.look_lights(spec, 'dark_hall'), mp.dark_hall_lights(spec))
        self.assertIsNone(mp.look_settings('neutral'))
        self.assertEqual(mp.rig_energy_scale('neutral'), 1.0)
        self.assertEqual(mp.rig_energy_scale('dark_hall'), mp.dark_hall_settings()['rig_energy_scale'])
        self.assertEqual(mp.LOOKS, ('neutral', 'dark_hall'))
        with self.assertRaises(ValueError):
            mp.look_lights(spec, 'moody')
        self.assertEqual(mp.dark_hall_lights(lit_spec()), [])

    def test_floor_plate_extends_past_the_booth_and_sits_at_its_lowest_point(self):
        plate = mp.dark_hall_floor([[-1, -2, 0], [3, 2, 2.5]])
        self.assertLess(plate['location'][2], 0)
        self.assertGreaterEqual(plate['location'][2], -0.01)
        self.assertGreater(plate['size'][0], 4)
        self.assertGreater(plate['size'][1], 4)
        self.assertEqual(plate['location'][:2], [1.0, 0.0])


class FakeLights:
    """bpy.data.lights that remembers every light made."""
    def __init__(self):
        self.made = []

    def new(self, name, kind):
        light = SimpleNamespace(name=name, kind=kind, energy=None, color=None)
        self.made.append(light)
        return light


class DarkHallBuildTests(FakeBlenderCase):
    """blender_build.main() with the dark_hall look, against the fake bpy (no Blender)."""
    run_main = MainTests.run_main
    parts = MainTests.parts

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        shutil.copy(EXAMPLE / 'reference.png', self.dir / 'reference.png')
        self.out = self.dir / 'run'
        self.background = FakeNode('ShaderNodeBackground', 'Background')
        self.background.inputs['Color'].default_value = (0, 0, 0, 1)
        self.background.inputs['Strength'].default_value = 1
        world = SimpleNamespace(use_nodes=False, node_tree=SimpleNamespace(nodes=SimpleNamespace(get=lambda name: self.background)))
        self.bpy.data.worlds = SimpleNamespace(new=lambda name: world)
        self.bpy.data.lights = FakeLights()
        self.shots = []
        scene = self.bpy.context.scene

        def render(write_still=False):
            self.shots.append({'samples': scene.cycles.samples, 'color': tuple(self.background.inputs['Color'].default_value),
                               'strength': self.background.inputs['Strength'].default_value})
        self.bpy.ops.render = SimpleNamespace(render=render)

    def lit_spec(self):
        spec = base_spec()
        ring = next(c for c in spec['components'] if c['id'] == 'top_ring')
        ring['material']['emission'] = 1.5
        spot = copy.deepcopy(next(c for c in spec['components'] if c['id'] == 'round_display'))
        spot.update(id='spot_0', role='spotlight', location=[0, 0, 2.9], critical=False)
        spot['params'] = {'radius': 0.04, 'height': 0.12}
        spot['material'] = {'color': [1, 1, .95, 1], 'roughness': .4, 'metallic': 0, 'emission': 4.0}
        spec['components'].append(spot)
        return spec

    def emission_strength(self, ident):
        material = self.parts()[ident].data.materials[0]
        shader = next(n for n in material.node_tree.nodes if n.kind == 'ShaderNodeBsdfPrincipled')
        return shader.inputs['Emission Strength'].default_value

    def rig_lights(self):
        return [l for l in self.bpy.data.lights.made if l.name in ('Key', 'Fill', 'Rim')]

    def test_main_neutral_preview_is_unchanged(self):
        report = self.run_main(self.lit_spec(), '--preview', '--preview-engine', 'cycles', '--samples', '32')
        self.assertEqual(report['look'], 'neutral')
        self.assertEqual(report['derived_lights'], 0)
        self.assertEqual(report['emissive_parts'], 2)
        self.assertEqual(report['look_unhandled'], [])
        self.assertEqual(self.background.inputs['Color'].default_value, (.25, .25, .25, 1))
        self.assertEqual(self.background.inputs['Strength'].default_value, .5)
        self.assertEqual([l.name for l in self.bpy.data.lights.made], ['Key', 'Fill', 'Rim'])
        for light, rig in zip(self.rig_lights(), report['render_rig']['lights']):
            self.assertEqual(light.energy, rig['energy'])
        self.assertNotIn('LOOK_FLOOR', [o.name for o in self.bpy.data.objects.created])
        self.assertEqual(self.emission_strength('top_ring'), 1.5)
        self.assertEqual(self.emission_strength('spot_0'), 4.0)
        # the preview sample cap stays 8 whatever --samples says
        self.assertEqual(self.shots[0]['samples'], 8)
        self.assertEqual(report['preview_samples'], 8)

    def test_main_dark_hall_preview_darkens_the_hall_and_lights_it(self):
        settings = mp.dark_hall_settings()
        report = self.run_main(self.lit_spec(), '--preview', '--preview-engine', 'cycles', '--samples', '64', '--look', 'dark_hall')
        self.assertEqual(report['look'], 'dark_hall')
        self.assertEqual(report['emissive_parts'], 2)
        self.assertEqual(report['derived_lights'], 1)
        self.assertEqual(report['look_unhandled'], [])
        # dark world
        self.assertEqual(self.background.inputs['Color'].default_value, (settings['world_color'],) * 3 + (1,))
        self.assertEqual(self.background.inputs['Strength'].default_value, settings['world_strength'])
        self.assertEqual(self.shots[0]['color'], (settings['world_color'],) * 3 + (1,))
        # the floor plate is a render-rig object below the booth with the dark rough material
        floor = [o for o in self.bpy.data.objects.created if o.name == 'LOOK_FLOOR']
        self.assertEqual(len(floor), 1)
        self.assertEqual(floor[0]['ownership'], 'render_rig')
        self.assertLess(floor[0].location[2], report['render_rig']['bounds'][0][2])
        shader = next(n for n in floor[0].data.materials[0].node_tree.nodes if n.kind == 'ShaderNodeBsdfPrincipled')
        self.assertEqual(shader.inputs['Base Color'].default_value, [settings['floor_color']] * 3 + [1])
        self.assertEqual(shader.inputs['Roughness'].default_value, settings['floor_roughness'])
        # the rig lights are dimmed by the fixed ratio
        for light, rig in zip(self.rig_lights(), report['render_rig']['lights']):
            self.assertAlmostEqual(light.energy, rig['energy'] * settings['rig_energy_scale'])
        # glowing parts light the hall through their own surface: emission strength x multiplier
        self.assertAlmostEqual(self.emission_strength('top_ring'), 1.5 * settings['emission_multiplier'])
        self.assertAlmostEqual(self.emission_strength('spot_0'), 4.0 * settings['emission_multiplier'])
        self.assertEqual(self.emission_strength('rear_wall'), 0)
        # the spotlight part also gets a derived spot, and only that one derived light exists
        derived = [l for l in self.bpy.data.lights.made if l.name.startswith('LOOK_')]
        self.assertEqual([(l.kind, l.name.split('_')[1]) for l in derived], [('SPOT', 'spot')])
        self.assertAlmostEqual(derived[0].energy, mp.dark_hall_lights(self.lit_spec())[0]['power_w'])
        # the sample cap is raised and --samples works up to it
        self.assertEqual(self.shots[0]['samples'], settings['preview_sample_cap'])
        self.assertEqual(report['preview_samples'], settings['preview_sample_cap'])

    def test_main_dark_hall_preview_uses_samples_below_the_cap(self):
        report = self.run_main(self.lit_spec(), '--preview', '--preview-engine', 'cycles', '--samples', '20', '--look', 'dark_hall')
        self.assertEqual(self.shots[0]['samples'], 20)
        self.assertEqual(report['preview_samples'], 20)

    def test_main_dark_hall_client_look_keeps_the_dark_world(self):
        self.run_main(self.lit_spec(), '--preview', '--preview-engine', 'cycles', '--preview-client-look', '--look', 'dark_hall')
        self.assertEqual(len(self.shots), 2)
        self.assertEqual(self.shots[1]['color'], (mp.dark_hall_settings()['world_color'],) * 3 + (1,))

    def test_main_neutral_client_look_still_lightens_the_backdrop(self):
        self.run_main(self.lit_spec(), '--preview', '--preview-engine', 'cycles', '--preview-client-look')
        self.assertEqual(self.shots[1]['color'], (.8, .8, .8, 1))

    def test_main_dark_hall_without_emissive_parts_still_builds(self):
        report = self.run_main(base_spec(), '--preview', '--preview-engine', 'cycles', '--look', 'dark_hall')
        self.assertEqual((report['look'], report['emissive_parts'], report['derived_lights']), ('dark_hall', 0, 0))
        self.assertEqual([l.name for l in self.bpy.data.lights.made], ['Key', 'Fill', 'Rim'])

    def test_main_dark_hall_writes_look_unhandled_for_a_spotlight_it_cannot_light(self):
        spec = self.lit_spec()
        lamp = copy.deepcopy(next(c for c in spec['components'] if c['id'] == 'top_ring'))
        lamp.update(id='lamp_text', kind='text', role='spotlight', critical=False, params={'body': 'L', 'size': .1})
        lamp['material'] = dict(lamp['material'], emission=2.0)
        spec['components'].append(lamp)
        report = self.run_main(spec, '--preview', '--preview-engine', 'cycles', '--look', 'dark_hall')
        self.assertEqual([u['id'] for u in report['look_unhandled']], ['lamp_text'])
        self.assertIn('no mesh', report['look_unhandled'][0]['reason'])
        self.assertEqual(report['derived_lights'], 1)           # only spot_0 became a light
        self.assertEqual(report['emissive_parts'], 3)
        self.assertEqual(len([l for l in self.bpy.data.lights.made if l.name.startswith('LOOK_')]), 1)
        # the neutral look lists nothing, whatever the spec holds
        shutil.rmtree(self.out)
        self.assertEqual(self.run_main(spec, '--preview', '--preview-engine', 'cycles')['look_unhandled'], [])

    def test_dark_hall_is_preview_only_and_refuses_workbench(self):
        with self.assertRaises(ValueError) as caught:
            self.run_main(self.lit_spec(), '--look', 'dark_hall')
        self.assertIn('with --preview only', str(caught.exception))
        with self.assertRaises(ValueError) as caught:
            self.run_main(self.lit_spec(), '--preview-engine', 'cycles', '--look', 'dark_hall')
        self.assertIn('with --preview only', str(caught.exception))
        self.assertFalse(self.out.exists())
        with self.assertRaises(ValueError) as caught:
            self.run_main(self.lit_spec(), '--render', '--preview-engine', 'cycles', '--look', 'dark_hall')
        self.assertIn('with --preview only', str(caught.exception))
        # Workbench draws no lights: refused (also when the engine is left at its default)
        for extra in (['--preview-engine', 'workbench'], []):
            with self.subTest(extra=extra):
                with self.assertRaises(ValueError) as caught:
                    self.run_main(self.lit_spec(), '--preview', '--look', 'dark_hall', *extra)
                self.assertIn('Cycles', str(caught.exception))
        self.assertFalse(self.out.exists())
        self.assertEqual(self.bpy.data.objects.created, [])
        # the customer look runs Cycles, so that combination is allowed
        report = self.run_main(self.lit_spec(), '--preview', '--preview-client-look', '--look', 'dark_hall')
        self.assertEqual(report['look'], 'dark_hall')
        # neutral keeps the old behaviour on Workbench
        shutil.rmtree(self.out)
        self.assertEqual(self.run_main(self.lit_spec(), '--preview')['look'], 'neutral')


class DocsTests(unittest.TestCase):
    def test_data_contract_describes_the_dark_hall_look(self):
        text = (ROOT / 'references' / 'data-contract.md').read_text(encoding='utf-8')
        for word in ('--look', 'dark_hall', 'derived_lights', 'emissive_parts', 'look_unhandled', 'preview_samples', 'LOOK_FLOOR'):
            self.assertIn(word, text)

    def test_data_contract_describes_projected_and_the_material_set(self):
        text = (ROOT / 'references' / 'data-contract.md').read_text(encoding='utf-8')
        self.assertIn('projected', text)
        self.assertIn('--material-set', text)

    def test_data_contract_describes_dots_grain_and_the_verifier(self):
        text = (ROOT / 'references' / 'data-contract.md').read_text(encoding='utf-8')
        for word in ('dots', 'grain', 'fade', 'dot_color', 'grain_color', 'radius_m', 'scale_m', 'strength', '`box`', 'blender_verify',
                     'unreviewed_modifiers', 'PROJECTED_UV', 'PATTERNS'):
            self.assertIn(word, text)

    def test_data_contract_says_when_dark_hall_is_refused(self):
        text = (ROOT / 'references' / 'data-contract.md').read_text(encoding='utf-8')
        paragraph = next(line for line in text.splitlines() if '--look neutral|dark_hall' in line)
        self.assertIn('--preview-client-look', paragraph)
        self.assertNotIn('一律拒绝', paragraph)

    def test_schema_has_the_dots_and_grain_branches(self):
        schema = read_json(ROOT / 'schemas' / 'booth-spec-v2.schema.json')
        branches = schema['properties']['components']['items']['properties']['material']['properties']['pattern']['oneOf']
        kinds = sorted(b['properties']['type']['const'] for b in branches)
        self.assertEqual(kinds, ['dots', 'grain', 'grid', 'projected'])

    def test_schema_grid_branch_is_indented_inside_the_one_of(self):
        text = (ROOT / 'schemas' / 'booth-spec-v2.schema.json').read_text(encoding='utf-8')
        self.assertIn('\n                  {\n                    "type": "object",\n                    "properties": {\n                      "type": {"const": "grid"},', text)


# --- dots and grain (P7-mat-pattern): parameters, node descriptions without bpy, and the signatures ---
DOTS = {'type': 'dots', 'spacing': [.25, .25], 'radius_m': .1, 'dot_color': [.9, .8, .7]}
GRAIN = {'type': 'grain', 'scale_m': .02, 'strength': .6, 'grain_color': [.2, .25, .3]}
BASE_RGBA = [.2, .4, .6, 1]
DOT_RGBA = [.9, .8, .7, 1]
BOX_PART = {'kind': 'box', 'params': {'size': [1, 1, 1]}}
ARC_PART = {'kind': 'arc_panel', 'params': {'radius': 2.0, 'start_deg': 0, 'end_deg': 60}}


def with_changes(pattern, changes):
    pattern = copy.deepcopy(pattern)
    for key, value in changes.items():
        if value is KeyError:
            pattern.pop(key, None)
        else:
            pattern[key] = value
    return pattern


def dots_material(**changes):
    return {'color': list(BASE_RGBA), 'pattern': with_changes(DOTS, changes)}


def grain_material(**changes):
    return {'color': list(BASE_RGBA), 'pattern': with_changes(GRAIN, changes)}


def evaluate(graph, name, output=0, point=(0, 0, 0), normal=(0, 0, 1), noise=.5):
    """Run a node description (the plain data the builders turn into nodes) at one shading point: the maths nodes, the coordinate
    node, Separate XYZ and the mix; the noise texture returns the value given."""
    nodes = {n['name']: n for n in graph['nodes']}
    incoming = {(to, key): (src, out) for src, out, to, key in graph['links']}
    memo = {}

    def socket(node, key):
        if (node, key) in incoming:
            return out(*incoming[(node, key)])
        return nodes[node]['inputs'].get(key)

    def out(node_name, output_key):
        if (node_name, output_key) in memo:
            return memo[(node_name, output_key)]
        node = nodes[node_name]
        kind = node['type']
        if kind == 'ShaderNodeTexCoord':
            value = point if output_key == 'Object' else normal
        elif kind == 'ShaderNodeSeparateXYZ':
            value = socket(node_name, 0)['XYZ'.index(output_key)]
        elif kind == 'ShaderNodeTexNoise':
            value = noise
        elif kind == 'ShaderNodeMixRGB':
            f, a, b = socket(node_name, 0), socket(node_name, 1), socket(node_name, 2)
            value = [x * (1 - f) + y * f for x, y in zip(a, b)]
        elif kind == 'ShaderNodeMath':
            a, b = socket(node_name, 0), socket(node_name, 1)
            table = {'ADD': lambda: a + b, 'SUBTRACT': lambda: a - b, 'MULTIPLY': lambda: a * b,
                     'FRACT': lambda: a - math.floor(a), 'ABSOLUTE': lambda: abs(a),
                     'GREATER_THAN': lambda: 1.0 if a > b else 0.0, 'LESS_THAN': lambda: 1.0 if a < b else 0.0,
                     'MAXIMUM': lambda: max(a, b), 'MINIMUM': lambda: min(a, b),
                     'SQRT': lambda: math.sqrt(a), 'ARCTAN2': lambda: math.atan2(a, b)}
            value = table[node['operation']]()
        else:
            raise AssertionError('the evaluator does not know ' + kind)
        memo[(node_name, output_key)] = value
        return value
    return out(name, output)


def colour_at(graph, point, normal=(0, 0, 1), noise=.5):
    return evaluate(graph, graph['color'][0], graph['color'][1], point=point, normal=normal, noise=noise)


def is_dot(graph, point, normal=(0, 0, 1)):
    colour = colour_at(graph, point, normal)
    if all(abs(a - b) < 1e-9 for a, b in zip(colour, DOT_RGBA)):
        return True
    assert all(abs(a - b) < 1e-9 for a, b in zip(colour, BASE_RGBA)), colour
    return False


def dots_graph_for(material, component=BOX_PART):
    return mp.dots_graph(mp.dots_parameters(material, component))


class PSocket:
    def __init__(self, node, name):
        self.node, self.name, self.default_value = node, name, None


class LazySockets:
    """Sockets that exist on first use, by name or by position; iterating gives the sockets (as Blender does)."""
    def __init__(self, node):
        self.node, self.items = node, {}

    def __getitem__(self, key):
        if key not in self.items:
            self.items[key] = PSocket(self.node, key if isinstance(key, str) else 'in%d' % key)
        return self.items[key]

    def __iter__(self):
        return iter(list(self.items.values()))


class PNode:
    def __init__(self, kind):
        self.bl_idname, self.name, self.type = kind, kind, ''
        self.operation = self.blend_type = None
        self.inputs, self.outputs = LazySockets(self), LazySockets(self)


class PNodes(list):
    def new(self, kind):
        node = PNode(kind)
        self.append(node)
        return node

    def clear(self):
        del self[:]


class PLink:
    def __init__(self, source, target):
        self.from_node, self.from_socket, self.to_node, self.to_socket = source.node, source, target.node, target


class PLinks(list):
    def new(self, source, target):
        self.append(PLink(source, target))


class PMaterial:
    def __init__(self, name='material'):
        self.name, self.use_nodes = name, False
        self.node_tree = SimpleNamespace(nodes=PNodes(), links=PLinks())


class PMaterials:
    def __init__(self):
        self.removed = []

    def new(self, name):
        return PMaterial(name)

    def remove(self, material):
        self.removed.append(material)


PICTURE = SimpleNamespace(name=mp.SOURCE_IMAGE)


def principled_material(material, component=BOX_PART, name='material'):
    """A material as the builders make it: a Principled node with the part's values plus the pattern nodes, on recording fakes."""
    result = PMaterial(name)
    result.use_nodes = True
    shader = result.node_tree.nodes.new('ShaderNodeBsdfPrincipled')
    shader.name, shader.type = 'Principled BSDF', 'BSDF_PRINCIPLED'
    color = mp.material_rgba(material)
    for key, value in (('Base Color', color), ('Alpha', color[3]), ('Roughness', material.get('roughness', .5)),
                       ('Metallic', material.get('metallic', 0)), ('Emission Color', color), ('Emission Strength', material.get('emission', 0))):
        shader.inputs[key].default_value = value
    fake_bpy = types.SimpleNamespace(data=types.SimpleNamespace(images=types.SimpleNamespace(get=lambda image_name: PICTURE)))
    with mock.patch.dict(sys.modules, {'bpy': fake_bpy}):
        mp.apply_pattern(result.node_tree.nodes, result.node_tree.links, shader, material, component)
    return result


def pattern_tree(material, component=BOX_PART):
    return principled_material(material, component)


GRID_SIGNATURE_SHA256 = {'box': '20f2fd227349f2730548c7f8ff1d9d4277407064ac1dfe415d1941ead608e943', 'arc': '6459c6103157e9f20b1e9380f2cc92a5961a786b8d85a4200a2cd6cc8be43472'}


class DotsTests(unittest.TestCase):
    def test_dots_pattern_validates_and_rejects_bad_values(self):
        schema = read_json(ROOT / 'schemas' / 'booth-spec-v2.schema.json')
        checker = jsonschema.Draft202012Validator(schema)
        self.assertIn('dots', mp.PATTERNS)
        parameters, nodes = mp.PATTERNS['dots']
        self.assertIs(parameters, mp.dots_parameters)
        self.assertEqual(list(inspect.signature(nodes).parameters), ['nodes', 'links', 'shader', 'material', 'component'])
        # a plain pattern: defaults are xz on a box, no shift, no fade; colours are RGBA, radius and spacing in metres
        p = mp.dots_parameters(dots_material(), BOX_PART)
        self.assertEqual((p['coordinates'], p['offset'], p['fade']), ('xz', [0, 0], None))
        self.assertEqual(p['spacing'], [.25, .25])
        self.assertAlmostEqual(p['radius_m'], .1)
        self.assertEqual(len(p['dot_color']), 4)
        self.assertEqual(mp.pattern_parameters(dots_material(), BOX_PART), p)
        self.assertIsNone(mp.pattern_parameters({'color': [1, 1, 1, 1]}, BOX_PART))
        # sRGB dot colours are made linear like the grid line colours
        srgb = dots_material(dot_color=[.5, .5, .5])
        srgb['color_space'] = 'srgb'
        self.assertLess(mp.dots_parameters(srgb, BOX_PART)['dot_color'][0], .5)
        # every coordinate mode, and auto picks the arc length on arc panels only
        for mode in ('auto', 'xy', 'xz', 'yz', 'box'):
            with self.subTest(mode=mode):
                self.assertEqual(mp.dots_parameters(dots_material(coordinates=mode), BOX_PART)['coordinates'], 'xz' if mode == 'auto' else mode)
        self.assertEqual(mp.dots_parameters(dots_material(), ARC_PART)['coordinates'], 'arc_length_z')
        self.assertEqual(mp.dots_parameters(dots_material(coordinates='box'), ARC_PART)['coordinates'], 'box')
        arc = mp.dots_parameters(dots_material(coordinates='arc_length_z', arc_origin_deg=30), ARC_PART)
        self.assertEqual((arc['arc_radius'], arc['arc_origin_deg']), (2.0, 30))
        with self.assertRaises(ValueError):
            mp.dots_parameters(dots_material(coordinates='arc_length_z'), BOX_PART)
        with self.assertRaises(ValueError):
            mp.dots_parameters(dots_material(arc_origin_deg=10), BOX_PART)
        # a fade is an object with an axis and two distinct metre values, or null
        fade = {'axis': 'z', 'from_m': 1.0, 'to_m': 3.0}
        self.assertEqual(mp.dots_parameters(dots_material(fade=dict(fade)), BOX_PART)['fade'], fade)
        self.assertIsNone(mp.dots_parameters(dots_material(fade=None), BOX_PART)['fade'])
        # the same patterns pass the whole-spec validator and the schema
        spec = base_spec()
        spec['components'][3]['material']['pattern'] = with_changes(DOTS, dict(fade=dict(fade), coordinates='box', offset=[.01, .02]))
        result = validate(spec)
        self.assertTrue(result['valid'], result['errors'])
        checker.validate(spec)
        spec['components'][3]['material']['pattern'] = with_changes(DOTS, dict(fade=None))
        checker.validate(spec)
        bad = {
            'radius zero': dict(radius_m=0), 'radius negative': dict(radius_m=-.01), 'radius nan': dict(radius_m=float('nan')),
            'radius bool': dict(radius_m=True), 'radius string': dict(radius_m='.05'), 'radius missing': dict(radius_m=KeyError),
            'radius is half the smaller spacing': dict(radius_m=.125), 'radius over half the smaller spacing': dict(radius_m=.2),
            'radius over half of the smaller of two spacings': dict(spacing=[.5, .1], radius_m=.06),
            'spacing zero': dict(spacing=[.25, 0]), 'spacing negative': dict(spacing=[-.25, .25]), 'spacing short': dict(spacing=[.25]),
            'spacing missing': dict(spacing=KeyError), 'spacing infinite': dict(spacing=[float('inf'), .25]),
            'colour high': dict(dot_color=[1.2, 0, 0]), 'colour short': dict(dot_color=[1, 1]), 'colour four': dict(dot_color=[1, 1, 1, 1]),
            'colour missing': dict(dot_color=KeyError),
            'unknown field': dict(thickness=.1), 'bad mode': dict(coordinates='uv'), 'bad offset': dict(offset=[0]),
            'fade is a list': dict(fade=[1, 2]), 'fade axis w': dict(fade={'axis': 'w', 'from_m': 0, 'to_m': 1}),
            'fade axis missing': dict(fade={'from_m': 0, 'to_m': 1}), 'fade from missing': dict(fade={'axis': 'x', 'to_m': 1}),
            'fade extra key': dict(fade={'axis': 'x', 'from_m': 0, 'to_m': 1, 'curve': 'cubic'}),
            'fade from equals to': dict(fade={'axis': 'x', 'from_m': 1.5, 'to_m': 1.5}),
            'fade not finite': dict(fade={'axis': 'x', 'from_m': 0, 'to_m': float('inf')}),
            'fade bool': dict(fade={'axis': 'x', 'from_m': True, 'to_m': 2}),
        }
        for label, change in bad.items():
            with self.subTest(label):
                with self.assertRaises(ValueError):
                    mp.dots_parameters(dots_material(**change), BOX_PART)
                broken = base_spec()
                broken['components'][3]['material'] = dots_material(**change)
                self.assertTrue(pattern_errors(validate(broken)), label)
        # the schema refuses what it can express
        for label, change in (('no radius', dict(radius_m=KeyError)), ('zero radius', dict(radius_m=0)), ('bad mode', dict(coordinates='uv')),
                              ('colour high', dict(dot_color=[1.2, 0, 0])), ('unknown field', dict(thickness=.1)),
                              ('bad axis', dict(fade={'axis': 'w', 'from_m': 0, 'to_m': 1}))):
            with self.subTest(schema=label):
                broken = base_spec()
                broken['components'][3]['material'] = dots_material(**change)
                self.assertTrue(list(checker.iter_errors(broken)), label)

    def test_dots_fade_shrinks_radius_along_axis(self):
        fade = {'axis': 'z', 'from_m': 1.0, 'to_m': 3.0}
        params = mp.dots_parameters(dots_material(coordinates='xz', fade=fade, radius_m=.1), BOX_PART)
        # the radius is the full one at from_m, falls linearly, is zero at to_m; before from_m it stays full, after to_m there are no dots
        self.assertAlmostEqual(mp.dots_radius(params, 1.0), .1)
        self.assertAlmostEqual(mp.dots_radius(params, 2.0), .05)
        self.assertAlmostEqual(mp.dots_radius(params, 2.5), .025)
        self.assertAlmostEqual(mp.dots_radius(params, 3.0), 0)
        self.assertAlmostEqual(mp.dots_radius(params, 0.2), .1)
        self.assertAlmostEqual(mp.dots_radius(params, 5.0), 0)
        # a fade that runs towards smaller values works the same way
        down = mp.dots_parameters(dots_material(coordinates='xz', fade={'axis': 'x', 'from_m': 3.0, 'to_m': 1.0}), BOX_PART)
        self.assertAlmostEqual(mp.dots_radius(down, 3.0), .1)
        self.assertAlmostEqual(mp.dots_radius(down, 2.0), .05)
        self.assertAlmostEqual(mp.dots_radius(down, 1.0), 0)
        self.assertAlmostEqual(mp.dots_radius(down, 0.0), 0)
        self.assertAlmostEqual(mp.dots_radius(down, 4.0), .1)
        # no fade: the same radius everywhere
        plain = mp.dots_parameters(dots_material(), BOX_PART)
        self.assertEqual({mp.dots_radius(plain, v) for v in (-5, 0, 1, 99)}, {.1})
        # the node description does the same: at a cell centre (x = .125 + .25 k, z = .125 + .25 m) a dot reaches radius(z) from the centre
        graph = mp.dots_graph(params)
        for z in (.875, 1.125, 2.125, 2.875, 3.125, 3.875):
            radius = mp.dots_radius(params, z)
            for delta in (0.0, .02, .04, .06, .08, .098):
                with self.subTest(z=z, delta=delta):
                    self.assertEqual(is_dot(graph, (.625 + delta, 7.0, z)), delta < radius - 1e-9)
        self.assertTrue(is_dot(graph, (.625 + .09, 0, 1.125)))       # r(1.125) = .09375: inside
        self.assertFalse(is_dot(graph, (.625 + .05, 0, 2.125)))      # r(2.125) = .04375: outside
        self.assertTrue(is_dot(graph, (.625 + .04, 0, 2.125)))
        self.assertFalse(is_dot(graph, (.625, 0, 3.125)))            # past to_m: no dots at all
        self.assertTrue(is_dot(graph, (.625 + .099, 0, .875)))       # before from_m: full size
        # the fade axis is an object axis whatever the plane: y here, while the dots lie in the xz plane
        by_y = mp.dots_graph(mp.dots_parameters(dots_material(coordinates='xz', fade={'axis': 'y', 'from_m': 0, 'to_m': 2}), BOX_PART))
        self.assertTrue(is_dot(by_y, (.625 + .09, 0.0, .625)))
        self.assertFalse(is_dot(by_y, (.625 + .06, 1.0, .625)))
        self.assertTrue(is_dot(by_y, (.625 + .04, 1.0, .625)))
        self.assertFalse(is_dot(by_y, (.625, 2.0, .625)))
        # the offset slides the rows
        shifted = mp.dots_graph(mp.dots_parameters(dots_material(coordinates='xz', offset=[.125, 0]), BOX_PART))
        self.assertTrue(is_dot(shifted, (.5, 0, .625)))
        self.assertFalse(is_dot(shifted, (.625, 0, .625)))

    def test_box_coordinates_pick_the_plane_by_normal(self):
        for normal, plane in (((1, 0, 0), 'yz'), ((-1, 0, 0), 'yz'), ((0, 1, 0), 'xz'), ((0, -1, 0), 'xz'), ((0, 0, 1), 'xy'), ((0, 0, -1), 'xy'),
                              ((.6, .5, .1), 'yz'), ((.5, -.6, .1), 'xz'), ((.1, .5, -.6), 'xy'), ((-.7, .7, .1), 'yz'),
                              ((0, .5, .5), 'xz'), ((.5, 0, .5), 'yz'), ((.5, .5, 0), 'yz'), ((.4, .4, .4), 'yz')):
            with self.subTest(normal=normal):
                self.assertEqual(mp.box_plane(normal), plane)
        params = mp.dots_parameters(dots_material(coordinates='box'), BOX_PART)
        self.assertEqual(params['coordinates'], 'box')
        graph = mp.dots_graph(params)
        # cell centres are at .125 + .25 k; a point sits on a centre in exactly one plane (the third coordinate is on a cell edge)
        points = {'yz': (.25, .375, .625), 'xz': (.375, .25, .625), 'xy': (.375, .625, .25)}
        cases = (((1, 0, 0), 'yz'), ((-1, 0, 0), 'yz'), ((0, 1, 0), 'xz'), ((0, -.9, .1), 'xz'), ((0, 0, 1), 'xy'), ((.1, .1, -1), 'xy'))
        for normal, plane in cases:
            with self.subTest(normal=normal):
                self.assertTrue(is_dot(graph, points[plane], normal))
                for other_plane, other_point in points.items():
                    if other_plane != plane:
                        self.assertFalse(is_dot(graph, other_point, normal), (normal, other_plane))
        # the description agrees with box_plane on the awkward normals too (ties included)
        for normal in ((.5, .5, 0), (0, .5, .5), (.5, 0, .5), (.4, .4, .4), (-.3, .8, -.8), (.9, -.1, .1)):
            with self.subTest(tie=normal):
                plane = mp.box_plane(normal)
                self.assertTrue(is_dot(graph, points[plane], normal))
                for other_plane, other_point in points.items():
                    if other_plane != plane:
                        self.assertFalse(is_dot(graph, other_point, normal))
        # a ring: the facade (normal along y) and the underside (normal along -z) both carry dots
        self.assertTrue(is_dot(graph, points['xz'], (0, 1, 0)))
        self.assertTrue(is_dot(graph, points['xy'], (0, 0, -1)))
        # explicit planes ignore the normal
        xz = mp.dots_graph(mp.dots_parameters(dots_material(coordinates='xz'), BOX_PART))
        self.assertTrue(is_dot(xz, points['xz'], (0, 0, 1)))
        self.assertFalse(is_dot(xz, points['xy'], (0, 0, 1)))

    def test_arc_length_dots_follow_the_arc(self):
        params = mp.dots_parameters(dots_material(coordinates='arc_length_z', spacing=[.25, .25], radius_m=.05), ARC_PART)
        graph = mp.dots_graph(params)
        # on a panel of radius 2 m the arc length is radius * angle: a centre at s = .125 is the angle .0625 rad
        angle = .125 / 2.0
        centre = (2.0 * math.cos(angle), 2.0 * math.sin(angle), .125)
        self.assertTrue(is_dot(graph, centre))
        beside = (2.0 * math.cos(angle + .04 / 2.0), 2.0 * math.sin(angle + .04 / 2.0), .125)
        self.assertTrue(is_dot(graph, beside))
        far = (2.0 * math.cos(angle + .1 / 2.0), 2.0 * math.sin(angle + .1 / 2.0), .125)
        self.assertFalse(is_dot(graph, far))

    def test_dots_node_tree_is_built_from_params(self):
        # the node description is plain data with no bpy; the builder turns it into nodes named dots_*
        graph = dots_graph_for(dots_material(fade={'axis': 'z', 'from_m': 1, 'to_m': 3}))
        self.assertTrue(all(n['name'].startswith('dots_') for n in graph['nodes']))
        kinds = {n['type'] for n in graph['nodes']}
        self.assertEqual(kinds, {'ShaderNodeTexCoord', 'ShaderNodeSeparateXYZ', 'ShaderNodeMath', 'ShaderNodeMixRGB'})
        tree = pattern_tree(dots_material(fade={'axis': 'z', 'from_m': 1, 'to_m': 3}))
        names = [n.name for n in tree.node_tree.nodes]
        self.assertEqual(len([n for n in names if n.startswith('dots_')]), len(graph['nodes']))
        targets = [(l.to_node.name, l.to_socket.name) for l in tree.node_tree.links]
        self.assertIn(('Principled BSDF', 'Base Color'), targets)
        self.assertIn(('Principled BSDF', 'Emission Color'), targets)
        self.assertNotIn(('Principled BSDF', 'Alpha'), targets)
        self.assertNotIn('ShaderNodeTexImage', [n.bl_idname for n in tree.node_tree.nodes])
        # part colour and dot colour reach the mix node
        mix = next(n for n in tree.node_tree.nodes if n.bl_idname == 'ShaderNodeMixRGB')
        self.assertEqual(list(mix.inputs[1].default_value), BASE_RGBA)
        self.assertEqual(list(mix.inputs[2].default_value), DOT_RGBA)
        # no pattern, no nodes
        self.assertEqual([n.name for n in pattern_tree({'color': BASE_RGBA}).node_tree.nodes], ['Principled BSDF'])


class GrainTests(unittest.TestCase):
    def test_grain_pattern_validates_and_rejects_bad_values(self):
        schema = read_json(ROOT / 'schemas' / 'booth-spec-v2.schema.json')
        checker = jsonschema.Draft202012Validator(schema)
        self.assertIn('grain', mp.PATTERNS)
        parameters, nodes = mp.PATTERNS['grain']
        self.assertIs(parameters, mp.grain_parameters)
        self.assertEqual(list(inspect.signature(nodes).parameters), ['nodes', 'links', 'shader', 'material', 'component'])
        p = mp.grain_parameters(grain_material(), BOX_PART)
        self.assertAlmostEqual(p['scale_m'], .02)
        self.assertAlmostEqual(p['strength'], .6)
        self.assertEqual(len(p['grain_color']), 4)
        self.assertEqual(mp.pattern_parameters(grain_material(), BOX_PART), p)
        for strength in (0, 1, .35):
            with self.subTest(strength=strength):
                self.assertEqual(mp.grain_parameters(grain_material(strength=strength), BOX_PART)['strength'], strength)
        # it works on any part kind, and on the floor through style.floor_pattern (the same material.pattern field)
        self.assertEqual(mp.grain_parameters(grain_material(), ARC_PART)['scale_m'], .02)
        spec = base_spec()
        spec['components'][0]['material']['pattern'] = copy.deepcopy(GRAIN)
        result = validate(spec)
        self.assertTrue(result['valid'], result['errors'])
        checker.validate(spec)
        bad = {
            'strength above one': dict(strength=1.01), 'strength negative': dict(strength=-.1), 'strength nan': dict(strength=float('nan')),
            'strength bool': dict(strength=True), 'strength string': dict(strength='.5'), 'strength missing': dict(strength=KeyError),
            'scale zero': dict(scale_m=0), 'scale negative': dict(scale_m=-.01), 'scale inf': dict(scale_m=float('inf')),
            'scale bool': dict(scale_m=True), 'scale missing': dict(scale_m=KeyError),
            'colour high': dict(grain_color=[0, 0, 1.1]), 'colour short': dict(grain_color=[.1, .1]), 'colour missing': dict(grain_color=KeyError),
            'unknown field': dict(spacing=[.1, .1]), 'coordinates field': dict(coordinates='xz'),
        }
        for label, change in bad.items():
            with self.subTest(label):
                with self.assertRaises(ValueError):
                    mp.grain_parameters(grain_material(**change), BOX_PART)
                broken = base_spec()
                broken['components'][0]['material'] = grain_material(**change)
                self.assertTrue(pattern_errors(validate(broken)), label)
        for label, change in (('strength high', dict(strength=1.5)), ('no strength', dict(strength=KeyError)), ('zero scale', dict(scale_m=0)),
                              ('unknown field', dict(spacing=[.1, .1])), ('colour high', dict(grain_color=[0, 0, 1.1]))):
            with self.subTest(schema=label):
                broken = base_spec()
                broken['components'][0]['material'] = grain_material(**change)
                self.assertTrue(list(checker.iter_errors(broken)), label)

    def test_grain_node_tree_is_built_from_params(self):
        params = mp.grain_parameters(grain_material(), BOX_PART)
        graph = mp.grain_graph(params)
        self.assertTrue(all(n['name'].startswith('grain_') for n in graph['nodes']))
        noise = next(n for n in graph['nodes'] if n['type'] == 'ShaderNodeTexNoise')
        self.assertAlmostEqual(noise['inputs']['Scale'], 50.0)       # features about scale_m metres wide: frequency = 1 / scale_m
        self.assertEqual(noise['settings']['noise_dimensions'], '3D')
        self.assertIn(('grain_coordinates', 'Object', noise['name'], 'Vector'), [tuple(l) for l in graph['links']])
        coarse = mp.grain_graph(mp.grain_parameters(grain_material(scale_m=.5), BOX_PART))
        self.assertAlmostEqual(next(n for n in coarse['nodes'] if n['type'] == 'ShaderNodeTexNoise')['inputs']['Scale'], 2.0)
        # the mix factor follows the noise and the strength; no noise contrast, no grain; strength zero, no grain
        base, grain = BASE_RGBA, params['grain_color']
        flat = colour_at(graph, (0, 0, 0), noise=0.0)
        self.assertEqual([round(v, 9) for v in flat], base)
        peak = colour_at(graph, (0, 0, 0), noise=1.0)
        for index in range(4):
            self.assertAlmostEqual(peak[index], base[index] * .4 + grain[index] * .6)
        middle = colour_at(graph, (0, 0, 0), noise=.5)
        for index in range(4):
            self.assertAlmostEqual(middle[index], base[index] * .7 + grain[index] * .3)
        none = mp.grain_graph(mp.grain_parameters(grain_material(strength=0), BOX_PART))
        for value in (0.0, .5, 1.0):
            self.assertEqual([round(v, 9) for v in colour_at(none, (0, 0, 0), noise=value)], base)
        full = mp.grain_graph(mp.grain_parameters(grain_material(strength=1), BOX_PART))
        for index in range(4):
            self.assertAlmostEqual(colour_at(full, (0, 0, 0), noise=1.0)[index], grain[index])
        # the builder makes the nodes and links the mix to the colour sockets, with no picture and no alpha change
        tree = pattern_tree(grain_material())
        kinds = [n.bl_idname for n in tree.node_tree.nodes]
        self.assertIn('ShaderNodeTexNoise', kinds)
        self.assertIn('ShaderNodeMixRGB', kinds)
        self.assertNotIn('ShaderNodeTexImage', kinds)
        targets = [(l.to_node.name, l.to_socket.name) for l in tree.node_tree.links]
        self.assertIn(('Principled BSDF', 'Base Color'), targets)
        self.assertIn(('Principled BSDF', 'Emission Color'), targets)
        self.assertNotIn(('Principled BSDF', 'Alpha'), targets)
        mix = next(n for n in tree.node_tree.nodes if n.bl_idname == 'ShaderNodeMixRGB')
        self.assertEqual(list(mix.inputs[1].default_value), BASE_RGBA)
        self.assertEqual(list(mix.inputs[2].default_value), grain)


class SignatureTests(unittest.TestCase):
    def test_grid_pattern_signature_unchanged(self):
        # the table entry is still the original pair and the original signature function
        self.assertIs(mp.PATTERNS['grid'][0], mp.grid_parameters)
        self.assertIs(mp.PATTERNS['grid'][1], mp.apply_grid)
        self.assertIs(mp.SIGNATURES['grid'], mp.grid_graph_signature)
        self.assertEqual(set(mp.SIGNATURES), set(mp.PATTERNS))
        # and the graph a grid makes is byte for byte the one it made before this package (recorded from the baseline)
        material = {'color': [.8, .7, .6, 1], 'roughness': .4, 'pattern': {'type': 'grid', 'spacing': [.14, .11], 'line_width': .015,
                                                                           'line_color': [.2, .3, .4], 'offset': [.02, .01]}}
        arc = {'color': [.5, .5, .5, 1], 'color_space': 'srgb', 'pattern': {'type': 'grid', 'spacing': [.2, .1], 'line_width': .01,
                                                                          'line_color': [.1, .2, .3], 'arc_origin_deg': 20}}
        for label, (mat, comp) in {'box': (material, BOX_PART), 'arc': (arc, ARC_PART)}.items():
            with self.subTest(label):
                signature = mp.grid_graph_signature(pattern_tree(mat, comp).node_tree)
                self.assertEqual(hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest(), GRID_SIGNATURE_SHA256[label])
                self.assertGreater(len(signature['nodes']), 10)

    def test_every_pattern_has_a_signature_that_sees_changes(self):
        for kind, material in (('grid', {'color': BASE_RGBA, 'pattern': copy.deepcopy(GRID)}), ('dots', dots_material()), ('grain', grain_material()),
                               ('projected', {'color': BASE_RGBA, 'pattern': copy.deepcopy(PROJECTED)})):
            with self.subTest(kind=kind):
                same_a, same_b = pattern_tree(material), pattern_tree(material)
                self.assertFalse(mp.pattern_mismatch(same_a.node_tree, same_b.node_tree, kind))
                signature = mp.SIGNATURES[kind](same_a.node_tree)
                self.assertTrue(signature['nodes'])
                self.assertTrue(all(n['name'].startswith(kind + '_') for n in signature['nodes']))
                # change one constant (or the UV map name of the projected node) inside the pattern nodes
                changed = pattern_tree(material)
                if kind == 'projected':
                    next(n for n in changed.node_tree.nodes if n.name == 'projected_uv').uv_map = 'Other'
                else:
                    socket = next(s for n in changed.node_tree.nodes if n.name.startswith(kind + '_') for s in n.inputs
                                  if isinstance(s.default_value, (int, float)) and not isinstance(s.default_value, bool))
                    socket.default_value += 1
                self.assertTrue(mp.pattern_mismatch(changed.node_tree, same_b.node_tree, kind))
                # drop a link
                relinked = pattern_tree(material)
                relinked.node_tree.links.pop()
                self.assertTrue(mp.pattern_mismatch(relinked.node_tree, same_b.node_tree, kind))
        # a part without a pattern must carry no pattern nodes of any family
        plain = pattern_tree({'color': BASE_RGBA})
        self.assertFalse(mp.pattern_mismatch(pattern_tree({'color': BASE_RGBA}).node_tree, plain.node_tree, None))
        for material in (dots_material(), grain_material(), {'color': BASE_RGBA, 'pattern': copy.deepcopy(GRID)}):
            self.assertTrue(mp.pattern_mismatch(pattern_tree(material).node_tree, plain.node_tree, None))
        # a part with a dots pattern must not carry grid nodes beside it
        mixed = pattern_tree(dots_material())
        extra = pattern_tree({'color': BASE_RGBA, 'pattern': copy.deepcopy(GRID)})
        mixed.node_tree.nodes.extend(n for n in extra.node_tree.nodes if n.name.startswith('grid_'))
        self.assertTrue(mp.pattern_mismatch(mixed.node_tree, pattern_tree(dots_material()).node_tree, 'dots'))

    def test_unknown_pattern_type_lists_dots_and_grain(self):
        with self.assertRaises(ValueError) as caught:
            mp.pattern_parameters({'color': [1, 1, 1, 1], 'pattern': {'type': 'stripes'}}, BOX_PART)
        for name in ('grid', 'projected', 'dots', 'grain'):
            self.assertIn(name, str(caught.exception))
        with self.assertRaises(ValueError):
            mp.pattern_mismatch(pattern_tree({'color': BASE_RGBA}).node_tree, pattern_tree({'color': BASE_RGBA}).node_tree, 'stripes')



# --- the independent reopen (blender_verify) against a fake bpy: every pattern type is dispatched, projected builds are understood ---
class SceneObject(dict):
    pass


class UvLayers:
    def __init__(self, names=()):
        self.names = list(names)

    def get(self, name):
        return name if name in self.names else None


class VerifyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.bpy = types.ModuleType('bpy')
        self.scene = SceneObject()
        self.scene.objects = []
        self.bpy.app = SimpleNamespace(background=True, version_string='fake')
        self.bpy.context = SimpleNamespace(scene=self.scene)
        self.bpy.data = SimpleNamespace(materials=PMaterials(), images=SimpleNamespace(get=lambda name: PICTURE))
        self.bpy.ops = SimpleNamespace(wm=SimpleNamespace(open_mainfile=lambda **kw: None))
        patcher = mock.patch.dict(sys.modules, {'bpy': self.bpy})
        patcher.start()
        self.addCleanup(patcher.stop)
        sys.modules.pop('blender_verify', None)
        self.bv = importlib.import_module('blender_verify')

    def spec(self, patterns, text=None):
        spec = base_spec()
        spec['components'] = [c for c in spec['components'] if c['id'] in ('booth_carpet', 'rear_wall', 'reception')]
        for ident, pattern in patterns.items():
            next(c for c in spec['components'] if c['id'] == ident)['material']['pattern'] = copy.deepcopy(pattern)
        if text:
            part = copy.deepcopy(next(c for c in spec['components'] if c['id'] == 'rear_wall'))
            part.update(id='sign_text', kind='text', role='neutral_caption', params={'body': 'A', 'size': .1})
            part['material'].pop('pattern', None)
            part['material']['pattern'] = copy.deepcopy(text)
            spec['components'].append(part)
        return spec

    def saved(self, look, spec, modifiers=None, uv=None, hidden=(), text_as_mesh=False):
        """The reopened scene for the look (the spec as the build saw it): one object per part, materials built by the real builders."""
        digest_of_spec = digest(read_json(self.dir / 'spec.json'))
        self.scene.clear()
        self.scene['spec_sha256'] = digest_of_spec
        self.scene['builder_sha256'] = code_hash()
        self.scene.objects = []
        for c in look['components']:
            ident = c['id']
            mat = principled_material(c['material'], c, name=ident)
            is_text = c['kind'] == 'text'
            projected = isinstance(c['material'].get('pattern'), dict) and c['material']['pattern'].get('type') == 'projected'
            obj = SceneObject(ownership='booth', component_id=ident, scope_id=c['scope_id'])
            obj.name = ident
            obj.location = tuple(c['location'])
            obj.rotation_euler = tuple(math.radians(v) for v in c['rotation_deg'])
            obj.scale = (1, 1, 1)
            obj.hide_render = ident in hidden
            wanted = projected if modifiers is None else ident in modifiers
            obj.modifiers = [SimpleNamespace(name='PROJECTED_UV', type='UV_PROJECT')] if wanted and not (is_text and not text_as_mesh) else []
            layers = UvLayers([mp.UV_LAYER] if (projected if uv is None else ident in uv) else [])
            if is_text and not text_as_mesh:
                obj.type = 'FONT'
                obj.data = SimpleNamespace(materials=[mat], body=c['params']['body'], size=c['params']['size'], extrude=c['params'].get('extrude', .001),
                                           align_x='CENTER', align_y='CENTER', uv_layers=layers)
            else:
                obj.type = 'MESH'
                vertices, faces = ([], []) if is_text else mesh_for(c)
                obj.data = SimpleNamespace(materials=[mat], vertices=[SimpleNamespace(co=tuple(v)) for v in vertices],
                                           polygons=[SimpleNamespace(vertices=tuple(f), use_smooth=False) for f in faces], uv_layers=layers)
            self.scene.objects.append(obj)

    def reopen(self, spec, material_set_name=None, skipped=(), **saved):
        write_json(self.dir / 'spec.json', spec)
        (self.dir / 'booth.blend').write_bytes(b'fake blend')
        report = {} if material_set_name is None else {'material_set': material_set_name, 'projected_uv_skipped': [{'id': i, 'reason': 'x'} for i in skipped]}
        if material_set_name is not None:
            write_json(self.dir / 'build_report.json', report)
        else:
            (self.dir / 'build_report.json').unlink(missing_ok=True)
        look = mp.material_set(read_json(self.dir / 'spec.json'), material_set_name or 'neutral')
        self.saved(look, spec, **saved)
        argv = ['blender', '--', '--spec', str(self.dir / 'spec.json'), '--blend', str(self.dir / 'booth.blend'), '--report', str(self.dir / 'verify.json')]
        with mock.patch.object(sys, 'argv', argv):
            try:
                self.bv.main()
            except RuntimeError:
                pass
        return read_json(self.dir / 'verify.json')

    def test_verify_dispatches_by_pattern_type_and_does_not_crash(self):
        spec = self.spec({'booth_carpet': GRAIN, 'rear_wall': with_changes(DOTS, dict(coordinates='box')), 'reception': GRID})
        result = self.reopen(spec)
        self.assertEqual(result['errors'], [])
        self.assertTrue(result['geometry_verified'])
        self.assertEqual(result['booth_object_count'], 3)
        # a part with no pattern at all still verifies
        self.assertEqual(self.reopen(self.spec({}))['errors'], [])

    def test_verify_sees_a_changed_dots_or_grain_graph(self):
        spec = self.spec({'booth_carpet': GRAIN, 'rear_wall': DOTS})
        for ident, kind in (('rear_wall', 'dots_'), ('booth_carpet', 'grain_')):
            with self.subTest(ident=ident):
                write_json(self.dir / 'spec.json', spec)
                self.reopen(spec)                       # builds the scene once so the objects exist
                obj = next(o for o in self.scene.objects if o['component_id'] == ident)
                socket = next(s for n in obj.data.materials[0].node_tree.nodes if n.name.startswith(kind) for s in n.inputs
                              if isinstance(s.default_value, (int, float)) and not isinstance(s.default_value, bool))
                socket.default_value += 1
                with mock.patch.object(sys, 'argv', ['blender', '--', '--spec', str(self.dir / 'spec.json'), '--blend', str(self.dir / 'booth.blend'),
                                                     '--report', str(self.dir / 'verify.json')]):
                    with self.assertRaises(RuntimeError):
                        self.bv.main()
                self.assertIn('material_pattern_mismatch:' + ident, read_json(self.dir / 'verify.json')['errors'])

    def test_verify_rejects_pattern_nodes_on_a_part_without_a_pattern(self):
        spec = self.spec({'booth_carpet': GRAIN})
        write_json(self.dir / 'spec.json', spec)
        self.reopen(spec)
        obj = next(o for o in self.scene.objects if o['component_id'] == 'reception')
        stray = principled_material({'color': BASE_RGBA, 'pattern': copy.deepcopy(DOTS)}, BOX_PART)
        obj.data.materials[0].node_tree.nodes.extend(n for n in stray.node_tree.nodes if n.name.startswith('dots_'))
        with mock.patch.object(sys, 'argv', ['blender', '--', '--spec', str(self.dir / 'spec.json'), '--blend', str(self.dir / 'booth.blend'),
                                             '--report', str(self.dir / 'verify.json')]):
            with self.assertRaises(RuntimeError):
                self.bv.main()
        self.assertIn('material_pattern_mismatch:reception', read_json(self.dir / 'verify.json')['errors'])

    def test_neutral_build_with_a_modifier_still_reports_unreviewed_modifiers(self):
        spec = self.spec({'rear_wall': DOTS})
        result = self.reopen(spec, modifiers={'rear_wall'})
        self.assertIn('unreviewed_modifiers:rear_wall', result['errors'])
        self.assertFalse(result['geometry_verified'])

    def test_projected_build_expects_the_uv_project_modifier_and_compares_the_projected_set(self):
        spec = self.spec({'rear_wall': DOTS})
        result = self.reopen(spec, 'projected')
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['booth_object_count'], 3)
        for obj in self.scene.objects:
            self.assertEqual([(m.name, m.type) for m in obj.modifiers], [('PROJECTED_UV', 'UV_PROJECT')])
        # a projected part with no modifier, no UV map, an extra modifier or neutral nodes is caught
        result = self.reopen(spec, 'projected', modifiers=set())
        self.assertIn('projected_modifier_missing:rear_wall', result['errors'])
        result = self.reopen(spec, 'projected', uv=set())
        self.assertIn('projected_uv_missing:rear_wall', result['errors'])
        result = self.reopen(spec, 'projected')
        self.assertEqual(result['errors'], [])
        write_json(self.dir / 'spec.json', spec)
        obj = next(o for o in self.scene.objects if o['component_id'] == 'rear_wall')
        obj.modifiers.append(SimpleNamespace(name='Bevel', type='BEVEL'))
        with mock.patch.object(sys, 'argv', ['blender', '--', '--spec', str(self.dir / 'spec.json'), '--blend', str(self.dir / 'booth.blend'),
                                             '--report', str(self.dir / 'verify.json')]):
            with self.assertRaises(RuntimeError):
                self.bv.main()
        self.assertIn('unreviewed_modifiers:rear_wall', read_json(self.dir / 'verify.json')['errors'])
        # the set the report names decides: the neutral scene does not pass as a projected build
        neutral_look = mp.material_set(spec, 'neutral')
        self.saved(neutral_look, spec)
        write_json(self.dir / 'build_report.json', {'material_set': 'projected'})
        with mock.patch.object(sys, 'argv', ['blender', '--', '--spec', str(self.dir / 'spec.json'), '--blend', str(self.dir / 'booth.blend'),
                                             '--report', str(self.dir / 'verify.json')]):
            with self.assertRaises(RuntimeError):
                self.bv.main()
        self.assertTrue(any(e.startswith('material_pattern_mismatch:') for e in read_json(self.dir / 'verify.json')['errors']))

    def test_a_projected_part_written_in_the_spec_file_is_expected_in_a_neutral_build(self):
        spec = self.spec({'rear_wall': PROJECTED})
        result = self.reopen(spec, 'neutral')
        self.assertEqual(result['errors'], [])
        wall = next(o for o in self.scene.objects if o['component_id'] == 'rear_wall')
        self.assertEqual(len(wall.modifiers), 1)
        self.assertEqual(self.reopen(spec)['errors'], [])             # no build_report at all: neutral

    def test_projected_text_is_a_converted_mesh_and_a_skipped_one_stays_hidden_text(self):
        spec = self.spec({}, text=PROJECTED)
        result = self.reopen(spec, 'neutral', text_as_mesh=True)
        self.assertEqual(result['errors'], [])
        text = next(o for o in self.scene.objects if o['component_id'] == 'sign_text')
        self.assertEqual((text.type, len(text.modifiers)), ('MESH', 1))
        # converted text that is not a mesh is an error
        result = self.reopen(spec, 'neutral', text_as_mesh=False, modifiers=set(), uv=set())
        self.assertTrue(any(e.endswith(':sign_text') for e in result['errors']), result['errors'])
        # text the build listed as skipped is hidden text without a modifier
        result = self.reopen(spec, 'neutral', skipped=['sign_text'], text_as_mesh=False, hidden={'sign_text'}, modifiers=set(), uv=set())
        self.assertEqual(result['errors'], [])
        result = self.reopen(spec, 'neutral', skipped=['sign_text'], text_as_mesh=False, modifiers=set(), uv=set())
        self.assertIn('skipped_part_visible:sign_text', result['errors'])

    def test_unknown_material_set_in_the_report_is_an_error_not_a_crash(self):
        spec = self.spec({})
        write_json(self.dir / 'spec.json', spec)
        self.saved(mp.material_set(spec, 'neutral'), spec)
        (self.dir / 'booth.blend').write_bytes(b'fake blend')
        write_json(self.dir / 'build_report.json', {'material_set': 'sepia'})
        with mock.patch.object(sys, 'argv', ['blender', '--', '--spec', str(self.dir / 'spec.json'), '--blend', str(self.dir / 'booth.blend'),
                                             '--report', str(self.dir / 'verify.json')]):
            with self.assertRaises(RuntimeError):
                self.bv.main()
        self.assertIn('unknown_material_set:sepia', read_json(self.dir / 'verify.json')['errors'])


if __name__ == '__main__':
    unittest.main()
