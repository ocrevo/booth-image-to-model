"""Scene description -> spec compiler: schema, rules, placement from pixels, and agreement with calling the macros directly."""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from jsonschema import Draft202012Validator
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import camera_math as cmath
import components as cm
import draft_compile as dc
from validate_spec import validate

CAM = {'projection': 'perspective', 'position': [8.0, -9.0, 4.5], 'target': [0.0, 0.0, 1.2], 'lens_mm': 36.0,
       'sensor_width_mm': 36.0, 'principal_point': [0.5, 0.5], 'pixel_aspect': [1, 1], 'fit_status': 'unfitted', 'roll_deg': 0.0}
IMG_W, IMG_H = 960, 600
W, D, H, ZF = 6.0, 5.0, 3.0, 0.05
SCHEMA = dc.load_schema()
NULLS = {k: None for k in SCHEMA['properties']['components']['items']['properties']}


def px(x, y, z):
    u, v, _ = cmath.project([x, y, z], CAM, IMG_W, IMG_H)
    return [round(u, 2), round(v, 2)]


def part(**kw):
    c = dict(NULLS)
    c.update(group=None, evidence='inferred')
    c.update(kw)
    return c


def scene():
    """A synthetic booth seen by CAM; every pixel is the projection of a known world point."""
    y_wall = H - 0.0          # placeholder to keep the arithmetic visible below
    back_face = D / 2 - 0.1   # wall face the led wall and tv hang on (inset 0.1 from the back edge)
    tv_w, tv_h = cm.TV[55]
    bezel = 0.02
    comps = [
        part(id='back_wall', type='wall', scope='shell', plane='back', z_range_m=[ZF, 2.8], color_hex='#f5f5f2',
             evidence='observed', roi_px=[300, 80, 700, 300]),
        part(id='frame', type='roof_frame', scope='shell', height_m=0.5, width_m=0.5, radius_m=0.6, color_hex='#ed6617'),
        part(id='post', type='box', scope='shell', group='post', base_px=px(2.6, -2.0, ZF), size_m=[0.3, 0.3, None],
             z_range_m=[ZF, 2.5], bevel_m=0.05, bevel_mode='vertical', color_hex='#ed6617', role='column', critical=True),
        part(id='counter', type='counter', scope='furniture', base_px=px(1.0, -1.2, ZF), size_m=[1.4, None, None],
             accent_hex='#a6d6e0', evidence='observed', roi_px=[500, 330, 640, 420]),
        part(id='table', type='round_table', scope='furniture', base_px=px(-1.2, 0.4, ZF)),
        part(id='chair', type='shell_chair', scope='furniture', base_px=px(-0.5, 0.4, ZF), yaw_deg=90, color_hex='#ed6617'),
        part(id='plant', type='planter', scope='furniture', base_px=px(-2.5, -1.8, ZF), size_m=[0.4, 0.4, None]),
        part(id='ribs', type='louvre_wall', scope='shell', base_px=px(2.0, -1.0, ZF), base_px_2=px(2.0, 1.0, ZF), count=5, width_m=0.08,
             height_m=2.4),
        part(id='led', type='led_wall', scope='media', plane='back', count=4, rows=2, module_m=0.5, depth_m=0.1,
             wall_rect_px=px(-1.0, back_face, 1.8) + px(1.0, back_face, 0.8)),
        part(id='tv', type='tv', scope='media', plane='back', inch=55, bezel_m=bezel, depth_m=0.05,
             wall_rect_px=px(-2.0 - tv_w / 2 - bezel, back_face, 1.6 + tv_h / 2 + bezel) + px(-2.0 + tv_w / 2 + bezel, back_face, 1.6 - tv_h / 2 - bezel)),
        part(id='lamps', type='spotlights', scope='lighting', height_m=2.8,
             points_px=[px(-1.0, -1.0, 2.8), px(1.0, -1.0, 2.8), px(1.0, -1.0, 2.8), px(4.0, -1.0, 2.8)]),
    ]
    corners = [('floor_bl', (-W / 2, -D / 2, ZF), ['-W/2', '-D/2', 'F']), ('floor_br', (W / 2, -D / 2, ZF), ['W/2', '-D/2', 'F']),
               ('floor_fr', (W / 2, D / 2, ZF), ['W/2', 'D/2', 'F']), ('floor_fl', (-W / 2, D / 2, ZF), ['-W/2', 'D/2', 'F']),
               ('top_fl', (-W / 2, D / 2, H), ['-W/2', 'D/2', 'H']), ('top_fr', (W / 2, D / 2, H), ['W/2', 'D/2', 'H'])]
    return {
        'schema_version': '1.1', 'project_id': 'synthetic_booth', 'image': {'width': IMG_W, 'height': IMG_H},
        'floor_z': ZF, 'known': {'W': W, 'D': D, 'H': H}, 'assumed': [],
        'corner_points': [{'id': i, 'uv_px': px(*xyz), 'xyz': tok, 'fit': None} for i, xyz, tok in corners],
        'scale_checks': [],
        'scopes': [{'id': s, 'decision': 'include', 'owner': 'booth', 'evidence': 'Synthetic fixture.'} for s in ('shell', 'furniture', 'media', 'lighting')]
                  + [{'id': 'venue', 'decision': 'exclude', 'owner': 'venue', 'evidence': 'Surroundings are not booth geometry.'}],
        'components': comps, 'uncertainties': [{'id': 'synthetic', 'description': 'Synthetic test content.', 'critical': False},
                                           {'id': 'hidden_structure', 'description': 'The far side is not visible.', 'critical': True}],
        'scale_notes': 'Synthetic dimensions.',
    }


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls._tmp.name)
        cls.image = cls.dir / 'image.png'
        Image.new('RGB', (IMG_W, IMG_H), (120, 120, 120)).save(cls.image)
        cls.fit = dc.fit_camera(scene())

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def compile(self, s=None, name='out'):
        return dc.compile_scene(s or scene(), self.image, self.dir / name, fit=copy.deepcopy(self.fit))

    @staticmethod
    def codes(s):
        return {e['code'] for e in dc.validate_scene(s)}


class SchemaTests(Base):
    def test_fixture_scene_is_valid_and_the_schema_is_portable(self):
        Draft202012Validator.check_schema(SCHEMA)
        self.assertEqual(dc.validate_scene(scene()), [])

        def walk(node, path='$'):
            if isinstance(node, dict):
                if node.get('type') == 'object' or 'properties' in node:
                    self.assertIs(node.get('additionalProperties'), False, path)
                    self.assertEqual(sorted(node['required']), sorted(node['properties']), path)
                for key in ('pattern', 'minItems', 'maxItems', 'uniqueItems', 'minimum', 'maximum', 'format', '$ref', 'anyOf', 'oneOf', 'allOf'):
                    self.assertNotIn(key, node, f'{path}: {key} is not supported by every provider')
                for k, v in node.items():
                    walk(v, f'{path}.{k}')
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    walk(v, f'{path}[{i}]')
        walk(SCHEMA)

    def test_structural_errors_are_reported_with_a_location(self):
        s = scene()
        del s['components'][0]['plane']
        s['components'][1]['surprise'] = 1
        s['components'][2]['type'] = 'sofa'
        s['image']['width'] = 'wide'
        wheres = {e['where'] for e in dc.validate_scene(s)}
        self.assertIn('components/0', wheres)
        self.assertTrue(any(w.startswith('components/2') for w in wheres) or 'components/2/type' in wheres)
        self.assertIn('image/width', wheres)
        self.assertEqual({e['code'] for e in dc.validate_scene(s)}, {'SCHEMA'})


class RuleTests(Base):
    def mutate(self, index, **kw):
        s = scene()
        s['components'][index].update(kw)
        return s

    def test_semantic_rules(self):
        names = [c['id'] for c in scene()['components']]
        i = names.index
        self.assertIn('EVIDENCE', self.codes(self.mutate(i('counter'), roi_px=None)))
        self.assertIn('SCOPE', self.codes(self.mutate(i('counter'), scope='venue')))
        self.assertIn('SCOPE', self.codes(self.mutate(i('counter'), scope='nowhere')))
        self.assertIn('COLOR', self.codes(self.mutate(i('counter'), color_hex='orange')))
        self.assertIn('MISSING', self.codes(self.mutate(i('counter'), size_m=None)))
        self.assertIn('MISSING', self.codes(self.mutate(i('post'), z_range_m=None)))
        self.assertIn('UNSUPPORTED', self.codes(self.mutate(i('led'), plane='left')))
        self.assertIn('MISSING', self.codes(self.mutate(i('tv'), inch=58)))
        self.assertIn('PIXEL', self.codes(self.mutate(i('table'), base_px=[5000, 20])))
        self.assertIn('SHAPE', self.codes(self.mutate(i('table'), base_px=[10])))
        self.assertIn('SHAPE', self.codes(self.mutate(i('back_wall'), z_range_m=[2.0, 1.0])))
        self.assertIn('ID', self.codes(self.mutate(i('table'), id='Table')))
        self.assertIn('ID', self.codes(self.mutate(i('table'), id='floor')))
        self.assertIn('ID', self.codes(self.mutate(i('table'), id='counter')))          # duplicate
        self.assertIn('MISSING', self.codes(self.mutate(i('lamps'), points_px=[])))

    def test_macro_prefix_collisions_are_refused(self):
        s = scene()
        s['components'].append(part(id='counter_extra', type='box', scope='furniture', base_px=px(0, 0, ZF), size_m=[0.3, 0.3, 0.3]))
        self.assertIn('ID', self.codes(s))

    def test_scope_rules(self):
        s = scene()
        s['scopes'][0]['owner'] = 'venue'
        self.assertIn('SCOPE', self.codes(s))
        s = scene()
        s['scopes'] = [x for x in s['scopes'] if x['decision'] != 'include']
        s['components'] = []
        self.assertEqual([(e['code'], e['where']) for e in dc.validate_scene(s)], [('SCOPE', 'scopes')])    # the rule itself, not a side effect

    def test_assumed_names_need_a_value(self):
        s = scene()
        s['known']['H'] = None
        s['assumed'] = ['H']
        self.assertIn('DIMENSION', self.codes(s))

    def test_image_size_must_match_the_file(self):
        s = scene()
        s['image'] = {'width': 1920, 'height': 1200}
        with self.assertRaises(dc.SceneError) as cm_:
            dc.compile_scene(s, self.image, self.dir / 'size', fit=copy.deepcopy(self.fit))
        self.assertTrue(any(e['code'] == 'IMAGE' for e in cm_.exception.errors))


class CompileTests(Base):
    def setUp(self):
        self.spec, self.report = self.compile()
        self.by_id = {c['id']: c for c in self.spec['components']}

    def test_the_spec_is_valid_and_clean(self):
        self.assertTrue(self.report['valid'], self.report['validation_errors'])
        self.assertEqual(self.report['status'], 'ok', self.report['dropped'])
        self.assertEqual(self.report['coplanar_unresolved'], [])
        out = self.dir / 'out'
        self.assertTrue(validate(json.loads((out / 'spec.json').read_text(encoding='utf-8')), out, source_size=[IMG_W, IMG_H])['valid'])
        for name in ('spec.json', 'box_fit.json', 'compile_report.json', 'source.png'):
            self.assertTrue((out / name).is_file(), name)
        self.assertEqual(self.spec['source']['image'], 'source.png')
        self.assertEqual(self.report['fit']['status'], 'fitted')

    def test_footprint_scope_and_floor(self):
        self.assertEqual(self.spec['scale']['footprint_m'], [W, D])
        floor = self.by_id['floor']
        self.assertEqual(floor['params']['size'][:2], [W, D])
        self.assertAlmostEqual(floor['location'][2] + floor['params']['size'][2] / 2, ZF, places=6)
        self.assertEqual(floor['evidence']['status'], 'observed')
        self.assertEqual({s['id'] for s in self.spec['scope']['decisions'] if s['decision'] == 'exclude'}, {'venue'})
        self.assertEqual(len([lm for lm in self.spec['landmarks']]), 4)      # the four floor corners become landmarks
        self.assertTrue(all(lm['fit'] for lm in self.spec['landmarks']))      # none is flagged as a check corner here

    def test_pixels_become_the_world_positions_they_came_from(self):
        near = lambda got, want: self.assertAlmostEqual(got, want, delta=0.04)
        body = self.by_id['counter_body']
        near(body['location'][0], 1.0)
        near(body['location'][1], -1.2)
        self.assertAlmostEqual(body['params']['size'][0], 1.4, places=3)
        self.assertAlmostEqual(body['params']['size'][1], cm.PRIOR['counter_depth'], places=3)
        top = self.by_id['table_top']
        near(top['location'][0], -1.2)
        near(top['location'][1], 0.4)
        seat = self.by_id['chair_seat']
        near(seat['location'][0], -0.5)
        near(seat['location'][1], 0.4)
        self.assertEqual(seat['rotation_deg'][2], 90)
        post = self.by_id['post']
        near(post['location'][0], 2.6)
        near(post['location'][1], -2.0)
        self.assertEqual(post['group'], 'post')
        self.assertEqual((post['params']['bevel_radius'], post['params']['bevel_mode']), (0.05, 'vertical'))
        led = self.by_id['led_panel']
        near(led['location'][0], 0.0)
        near(led['location'][2], 1.3)
        self.assertAlmostEqual(led['params']['size'][0], 2.0, places=3)
        tv = self.by_id['tv_display']
        near(tv['location'][0], -2.0)
        near(tv['location'][2], 1.6)
        ribs = [c for c in self.spec['components'] if c['id'].startswith('ribs_')]
        self.assertEqual(len(ribs), 5)
        near(min(r['location'][1] - r['params']['size'][1] / 2 for r in ribs), -1.0)
        near(max(r['location'][1] + r['params']['size'][1] / 2 for r in ribs), 1.0)

    def test_walls_frame_and_grouping(self):
        wall = self.by_id['back_wall']
        self.assertEqual((wall['role'], wall['critical']), ('back_wall', True))
        self.assertAlmostEqual(wall['location'][1] + wall['params']['size'][1] / 2, D / 2, delta=0.005)
        ring = self.by_id['frame']
        self.assertEqual((ring['kind'], ring['role']), ('rounded_ring', 'roof_frame'))
        self.assertAlmostEqual(ring['location'][2] + ring['params']['height'] / 2, H, places=3)
        self.assertEqual(ring['params']['outer_size'], [W, D])
        for prefix in ('counter', 'table', 'chair', 'ribs', 'led', 'tv', 'lamps', 'plant'):
            members = [c for c in self.spec['components'] if c['id'].startswith(prefix + '_')]
            self.assertTrue(members, prefix)
            self.assertEqual({c.get('group') for c in members}, {prefix}, prefix)
        self.assertNotIn('group', self.by_id['back_wall'])

    def test_rules_clamp_dedupe_and_record(self):
        lights = sorted(c['id'] for c in self.spec['components'] if c['id'].startswith('lamps_'))
        self.assertEqual(len(lights), 3)                                      # four points, one is a duplicate position
        moved = [c for c in self.spec['components'] if c['id'].startswith('lamps_') and c['location'][0] > 2.5]
        self.assertEqual(len(moved), 1)
        self.assertAlmostEqual(moved[0]['location'][0], W / 2 - 0.3, places=2)
        whats = [a['what'] for a in self.report['adjustments'] if a['id'] == 'lamps']
        self.assertTrue(any('duplicate' in w for w in whats), whats)
        self.assertTrue(any('inside the footprint' in w for w in whats), whats)

    def test_assumed_values_are_recorded_not_hidden(self):
        what = {(a['id'], a['what']) for a in self.report['assumed']}
        self.assertIn(('counter', 'depth_m'), what)
        self.assertIn(('ribs', 'thickness_m'), what)
        self.assertIn('compile_assumed_values', {u['id'] for u in self.spec['uncertainties']})
        self.assertIn('metric_dimensions', {u['id'] for u in self.spec['uncertainties']})
        self.assertIn('Compiled from a scene description', self.spec['scale']['notes'])

    def test_evidence_follows_the_scene(self):
        self.assertEqual(self.by_id['counter_body']['evidence']['status'], 'observed')
        self.assertEqual(self.by_id['table_top']['evidence']['status'], 'inferred')
        self.assertEqual(self.by_id['back_wall']['evidence']['status'], 'observed')

    def test_compiler_matches_calling_the_macros_directly(self):
        body = self.by_id['counter_body']
        b = cm.SpecBuilder(self.image, IMG_W, IMG_H, 'direct')
        b.scope('furniture', 'x')
        x, y, d = body['location'][0], body['location'][1], cm.PRIOR['counter_depth']
        cm.counter(b, 'counter', 'furniture', (x - 0.7, x + 0.7), (y - d / 2, y + d / 2), floor_z=ZF,
                   top_color=[0.651, 0.8392, 0.8784, 1])         # body colour: the scene gave none, so the macro default
        direct = {c['id']: c for c in b.components}
        self.assertEqual(sorted(direct), sorted(i for i in self.by_id if i.startswith('counter_')))
        for cid, c in direct.items():
            for key in ('kind', 'role', 'location', 'params', 'material'):
                self.assertEqual(c[key], self.by_id[cid][key], f'{cid}.{key}')


class PartialAndRepairTests(Base):
    def test_a_part_that_cannot_be_built_is_dropped_with_its_reason(self):
        s = scene()
        a, b = px(-2.9, 3.6, 1.9), px(-2.9, 3.0, 1.4)            # on the left wall plane, but beyond the back end of the booth
        rect = [min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])]
        s['components'].append(part(id='ghost_tv', type='tv', scope='media', plane='left', inch=55, bezel_m=0.02, depth_m=0.05,
                                    wall_rect_px=rect))
        spec, report = self.compile(s, 'partial')
        self.assertEqual(report['status'], 'partial')
        self.assertTrue(report['valid'])
        self.assertEqual([d['id'] for d in report['dropped']], ['ghost_tv'])
        self.assertFalse(any(c['id'].startswith('ghost_tv') for c in spec['components']))

    def test_a_scope_left_without_geometry_is_removed(self):
        s = scene()
        s['components'] = [c for c in s['components'] if c['scope'] != 'lighting']
        spec, report = self.compile(s, 'scope')
        self.assertNotIn('lighting', {d['id'] for d in spec['scope']['decisions']})
        self.assertTrue(report['valid'])
        self.assertTrue(any('lighting' == a['id'] for a in report['adjustments']))

    def test_coplanar_faces_are_pulled_apart(self):
        s = scene()
        s['components'].append(part(id='left_wall', type='wall', scope='shell', plane='left', z_range_m=[ZF, 2.8], color_hex='#f5f5f2'))
        spec, report = self.compile(s, 'coplanar')
        self.assertTrue(report['valid'])
        self.assertEqual(report['coplanar_unresolved'], [])
        self.assertTrue(any('z-fighting' in a['what'] for a in report['adjustments']), report['adjustments'])
        self.assertFalse(any(w.startswith('COPLANAR_OVERLAP') for w in report['warnings']))

    def test_the_fit_is_recomputed_when_none_is_given(self):
        spec, report = dc.compile_scene(scene(), self.image, self.dir / 'refit')
        self.assertEqual(report['fit']['status'], 'fitted')
        for a, b in zip(spec['camera']['position'], CAM['position']):
            self.assertAlmostEqual(a, b, delta=0.15)

    def test_a_scene_without_a_usable_fit_is_refused(self):
        s = scene()
        s['corner_points'] = s['corner_points'][:2]
        with self.assertRaises(dc.SceneError) as ctx:
            dc.compile_scene(s, self.image, self.dir / 'nofit')
        self.assertEqual(ctx.exception.errors[0]['code'], 'FIT_FAILED')


class CommandLineTests(Base):
    def test_script_and_boothctl_compile(self):
        scene_file = self.dir / 'scene.json'
        scene_file.write_text(json.dumps(scene()), encoding='utf-8')
        fit_file = self.dir / 'fit.json'
        fit_file.write_text(json.dumps(self.fit), encoding='utf-8')
        for argv, out in ((['draft_compile.py'], 'cli_a'), (['boothctl.py', 'compile'], 'cli_b')):
            r = subprocess.run([sys.executable, str(ROOT / 'scripts' / argv[0]), *argv[1:], '--scene', str(scene_file), '--image', str(self.image),
                                '--out', str(self.dir / out), '--fit', str(fit_file)], capture_output=True, text=True, check=False)
            self.assertEqual(r.returncode, 0, r.stderr)
            summary = json.loads(r.stdout)
            self.assertEqual((summary['status'], summary['valid']), ('ok', True))
            self.assertTrue((self.dir / out / 'spec.json').is_file())
        bad = json.loads(scene_file.read_text(encoding='utf-8'))
        bad['components'][0]['evidence'] = 'observed'
        bad['components'][0]['roi_px'] = None
        scene_file.write_text(json.dumps(bad), encoding='utf-8')
        r = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'boothctl.py'), 'compile', '--scene', str(scene_file), '--image', str(self.image),
                            '--out', str(self.dir / 'cli_c'), '--fit', str(fit_file)], capture_output=True, text=True, check=False)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(json.loads(r.stdout)['status'], 'scene_invalid')
        self.assertFalse((self.dir / 'cli_c' / 'spec.json').exists())


@unittest.skipUnless(os.environ.get('BLENDER_EXE'), 'Set BLENDER_EXE for the native compiled-scene preview')
class NativeCompileTests(Base):
    @staticmethod
    def build(spec, out, *extra):
        cmd = [os.environ['BLENDER_EXE'], '--background', '--factory-startup', '--disable-autoexec', '--python-exit-code', '1',
               '--python', str(ROOT / 'scripts/blender_build.py'), '--', '--spec', str(spec), '--out', str(out), '--preview', *extra]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    def test_compiled_spec_builds_a_preview(self):
        spec, report = self.compile(name='native')
        self.assertTrue(report['valid'])
        out = self.dir / 'native_preview'
        r = self.build(self.dir / 'native' / 'spec.json', out)
        if r.returncode != 0 and 'EXCEPTION_ACCESS_VIOLATION' in r.stdout + r.stderr:        # integrated-GPU driver crash
            out = self.dir / 'native_preview_cycles'
            r = self.build(self.dir / 'native' / 'spec.json', out, '--preview-engine', 'cycles')
        self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-1500:])
        self.assertEqual(Image.open(out / 'preview_render.png').size, (IMG_W, IMG_H))


if __name__ == '__main__':
    unittest.main()
