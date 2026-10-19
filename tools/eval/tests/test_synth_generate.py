import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import camgeom
import skill_caps
import synth_generate as sg

ROOT = Path(__file__).resolve().parents[3]
SKILL = ROOT / 'booth-image-to-model'
ORACLE_V1_FINGERPRINT = 'e29d9cff4dbf9908fe5a7af5f6a0b542ef9775ea698c5209b10a82c4c9e350e4'      # sha256 of suite.json, as in BASELINE.md


def tree_hashes(folder):
    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(folder).rglob('*.json'))}


class Determinism(unittest.TestCase):
    def test_same_seed_gives_byte_identical_files(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            sg.write_suite('smoke', 123, a)
            sg.write_suite('smoke', 123, b)
            self.assertEqual(tree_hashes(a), tree_hashes(b))
            self.assertGreaterEqual(len(tree_hashes(a)), 9)          # four cases x two files + suite.json

    def test_the_baseline_suite_is_pinned(self):
        # BASELINE.md and every stored comparison rest on these exact scenes. A change here means the ground truth moved: bump
        # GENERATOR_VERSION, rerun the baseline and update this pin together with BASELINE.md, never this line alone.
        self.assertEqual(sg.suite_fingerprint('oracle-v1', 20261019), ORACLE_V1_FINGERPRINT)
        self.assertEqual(sg.GENERATOR_VERSION, 1)
        self.assertEqual(sg.SUITES['oracle-v1']['perturbations'],
                         ['clean', 'noise_1', 'noise_3', 'outlier_30', 'outlier_1', 'outlier_2', 'mislabel', 'all_exact', 'all_guess'])

    def test_extra_perturbations_do_not_change_the_suites(self):
        import perturb
        self.assertTrue({'wrong_anchor_m25', 'wrong_anchor_m10', 'wrong_anchor_p10', 'wrong_anchor_p25'} <= set(perturb.PERTURBATIONS))
        for suite in sg.SUITES.values():
            self.assertTrue(set(suite['perturbations']) <= set(perturb.PERTURBATIONS))

    def test_a_different_seed_gives_different_cameras(self):
        a = sg.build_case(1, 0)[0]['camera']
        b = sg.build_case(2, 0)[0]['camera']
        self.assertNotEqual(a['position'], b['position'])

    def test_suite_fingerprint_follows_seed_and_content(self):
        a = sg.suite_fingerprint('smoke', 1)
        self.assertEqual(a, sg.suite_fingerprint('smoke', 1))
        self.assertNotEqual(a, sg.suite_fingerprint('smoke', 2))
        self.assertNotEqual(a, sg.suite_fingerprint('smoke', 1, 3))
        manifest, files = sg.suite_files('smoke', 1)
        self.assertEqual(hashlib.sha256(files['suite.json']).hexdigest(), a)
        self.assertEqual([c['case_id'] for c in manifest['cases']], [f'synth_{i:04d}' for i in range(4)])

    def test_the_grammar_variant_adds_the_true_topology_to_the_scene_only(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            sg.write_suite('smoke', 7, a)
            manifest = sg.write_suite('smoke', 7, b, grammar_keys=True)
            self.assertTrue(manifest['grammar'])
            self.assertNotIn('grammar', json.loads((Path(a) / 'suite.json').read_text(encoding='utf-8')))
            for i in range(4):
                name = f'synth_{i:04d}'
                self.assertEqual((Path(a) / name / 'case.json').read_bytes(), (Path(b) / name / 'case.json').read_bytes())     # the truth is the same file
                plain = json.loads((Path(a) / name / 'scene.json').read_text(encoding='utf-8'))
                rich = json.loads((Path(b) / name / 'scene.json').read_text(encoding='utf-8'))
                case = json.loads((Path(b) / name / 'case.json').read_text(encoding='utf-8'))
                self.assertEqual(rich['schema_version'], '1.2')
                self.assertEqual({k: v for k, v in rich.items() if k not in ('schema_version', 'booth_type', 'open_sides', 'cutaway_sides')},
                                 {k: v for k, v in plain.items() if k != 'schema_version'})
                self.assertEqual(sorted(rich['open_sides'] + [w['plane'] for w in case['walls']]), ['back', 'front', 'left', 'right'])
                self.assertEqual(rich['cutaway_sides'], [])
            self.assertNotEqual(sg.suite_fingerprint('smoke', 7), sg.suite_fingerprint('smoke', 7, grammar_keys=True))
            self.assertEqual(sg.suite_fingerprint('oracle-v1', 20261019), ORACLE_V1_FINGERPRINT)       # the default is untouched

    @unittest.skipUnless(__import__('importlib').util.find_spec('jsonschema'), 'jsonschema is not installed')
    def test_the_grammar_scene_passes_the_schema_of_a_skill_that_has_the_keys(self):
        from jsonschema import Draft202012Validator
        schema = json.loads((SKILL / 'schemas' / 'scene-description.v1.json').read_text(encoding='utf-8'))
        if 'booth_type' not in schema['properties']:
            self.skipTest('the repository skill does not have the booth grammar keys yet')
        caps = skill_caps.detect(SKILL)
        for i in range(8):
            case, scene = sg.build_case(7, i)
            adapted = skill_caps.adapt_scene(sg.with_grammar(case, scene), caps, {pid: False for pid in case['check_ids']})
            self.assertEqual([e.message for e in Draft202012Validator(schema).iter_errors(adapted)], [], case['case_id'])

    def test_files_use_lf_line_endings(self):
        with tempfile.TemporaryDirectory() as d:
            sg.write_suite('smoke', 5, d)
            for p in Path(d).rglob('*.json'):
                self.assertNotIn(b'\r', p.read_bytes(), p.name)


class CaseTruth(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = [sg.build_case(20261019, i) for i in range(12)]

    def test_booth_types_and_sizes_are_covered(self):
        types = {c['booth_type'] for c, _ in self.cases}
        self.assertEqual(types, set(sg.BOOTH_TYPES))
        self.assertGreater(len({tuple(c['dims_m'].values()) for c, _ in self.cases}), 6)

    def test_every_corner_pixel_is_the_projection_of_its_world_point(self):
        for case, _ in self.cases:
            w, h = case['image']['width'], case['image']['height']
            for corner in case['corners']:
                u, v, depth = camgeom.project(corner['world_m'], case['camera'], w, h)
                self.assertAlmostEqual(u, corner['uv_px'][0], delta=0.002)
                self.assertAlmostEqual(v, corner['uv_px'][1], delta=0.002)
                self.assertTrue(0 <= u <= w and 0 <= v <= h, (case['case_id'], corner['id']))
                self.assertGreater(depth, 0.3)

    def test_six_to_ten_corners_with_floor_and_top_points(self):
        for case, _ in self.cases:
            tokens = case['corners']
            self.assertTrue(sg.MIN_VISIBLE <= len(tokens) <= sg.MAX_POINTS, case['case_id'])
            self.assertGreaterEqual(sum(1 for c in tokens if c['xyz'][2] == 'F'), 2)
            self.assertGreaterEqual(sum(1 for c in tokens if c['xyz'][2] == 'H'), 2)
            self.assertLessEqual(len(case['check_ids']), 2)
            self.assertTrue(set(case['check_ids']) <= {c['id'] for c in tokens})

    def test_camera_parameters_stay_in_the_stated_ranges(self):
        for case, _ in self.cases:
            t = case['camera_truth']
            self.assertTrue(5.0 <= t['pitch_deg'] <= 45.0, t)
            self.assertLessEqual(abs(case['camera']['roll_deg']), 2.0 + 1e-6)
            full_f_mm = t['focal_px'] / case['image']['full_frame']['width'] * camgeom.SENSOR_MM
            self.assertTrue(18.0 - 1e-3 <= full_f_mm <= 50.0 + 1e-3, full_f_mm)

    def test_some_cases_are_cropped_with_the_principal_point_off_centre(self):
        cropped = [c for c, _ in (sg.build_case(20261019, i) for i in range(32)) if c['image']['cropped']]
        self.assertTrue(cropped)
        for case in cropped:
            pp = case['camera']['principal_point']
            self.assertTrue(abs(pp[0] - 0.5) > 1e-4 or abs(pp[1] - 0.5) > 1e-4)

    def test_parts_stand_inside_the_footprint_and_their_pixels_match(self):
        for case, _ in self.cases:
            W, D = case['dims_m']['W'], case['dims_m']['D']
            w, h = case['image']['width'], case['image']['height']
            for part in case['parts']:
                x, y = part['center_m']
                self.assertTrue(abs(x) < W / 2 and abs(y) < D / 2, (case['case_id'], part['id']))
                u, v, _ = camgeom.project([x, y, case['floor_z']], case['camera'], w, h)
                self.assertAlmostEqual(u, part['base_px'][0], delta=0.002)
                self.assertAlmostEqual(v, part['base_px'][1], delta=0.002)

    def test_walls_follow_the_booth_type(self):
        for case, _ in self.cases:
            walls = [w['plane'] for w in case['walls']]
            self.assertEqual(walls, sg.WALLS[case['booth_type']])
            self.assertEqual(sorted(walls + case['open_sides']), ['back', 'front', 'left', 'right'])


class SceneFile(unittest.TestCase):
    def test_scene_is_in_the_original_format_with_the_anchor_only(self):
        case, scene = sg.build_case(20261019, 3)
        self.assertEqual(scene['schema_version'], 'scene-description.v1')
        self.assertTrue(all('fit' not in p for p in scene['corner_points']))
        known = {k: v for k, v in scene['known'].items() if v is not None}
        self.assertEqual(list(known), [case['anchor']])
        self.assertEqual(scene['assumed'], [])
        self.assertEqual(len(scene['corner_points']), len(case['corners']))

    @unittest.skipUnless(__import__('importlib').util.find_spec('jsonschema'), 'jsonschema is not installed')
    def test_scene_passes_the_schema_of_the_repository_skill_after_adaptation(self):
        from jsonschema import Draft202012Validator
        schema = json.loads((SKILL / 'schemas' / 'scene-description.v1.json').read_text(encoding='utf-8'))
        caps = skill_caps.detect(SKILL)
        for i in range(8):
            case, scene = sg.build_case(7, i)
            adapted = skill_caps.adapt_scene(scene, caps, {pid: False for pid in case['check_ids']})
            errors = [e.message for e in Draft202012Validator(schema).iter_errors(adapted)]
            self.assertEqual(errors, [], case['case_id'])


if __name__ == '__main__':
    unittest.main()
