import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import skill_caps

ROOT = Path(__file__).resolve().parents[3]

OLD_BOX_FIT = "def solve(data):\n    return {'status': 'fitted'}\n"
NEW_BOX_FIT = ("def solve(data):\n    flag = p.get('fit')\n    loss = 'soft_l1'\n    return {'status': 'check_failed', 'n_check': 2, 'loo_errors_px': {}}\n")


def make_skill(root, fit_key, schema_versions, new_scripts):
    root = Path(root)
    (root / 'scripts').mkdir(parents=True)
    (root / 'schemas').mkdir()
    (root / 'scripts' / 'box_fit.py').write_text(NEW_BOX_FIT if new_scripts else OLD_BOX_FIT, encoding='utf-8')
    (root / 'scripts' / 'boothctl.py').write_text('print(1)\n', encoding='utf-8')
    (root / 'scripts' / 'draft_compile.py').write_text("report['quality_gate'] = {}\n" if new_scripts else 'pass\n', encoding='utf-8')
    point = {'id': {}, 'uv_px': {}, 'xyz': {}}
    if fit_key:
        point['fit'] = {'type': ['boolean', 'null']}
    schema = {'properties': {'schema_version': {'enum': schema_versions}, 'corner_points': {'items': {'properties': point}}}}
    (root / 'schemas' / 'scene-description.v1.json').write_text(json.dumps(schema), encoding='utf-8')
    (root / 'SKILL.md').write_text('---\nname: x\nmetadata:\n  version: "9.9.9-rc1"\n---\n', encoding='utf-8')
    return root


SCENE = {'schema_version': 'scene-description.v1', 'corner_points': [{'id': 'a', 'uv_px': [1, 2], 'xyz': ['0', '0', 'F']},
                                                                     {'id': 'b', 'uv_px': [3, 4], 'xyz': ['0', '0', 'H']}], 'components': []}
POINTS = {'points': [{'id': 'a', 'uv_px': [1, 2], 'xyz': ['0', '0', 'F']}, {'id': 'b', 'uv_px': [3, 4], 'xyz': ['0', '0', 'H']}]}


class Detection(unittest.TestCase):
    def test_old_skill(self):
        with tempfile.TemporaryDirectory() as d:
            caps = skill_caps.detect(make_skill(Path(d) / 's', False, ['scene-description.v1'], False))
        self.assertFalse(caps['scene_fit_key'])
        self.assertFalse(caps['box_fit_fit_flag'])
        self.assertFalse(caps['box_fit_leave_one_out'])
        self.assertFalse(caps['compile_quality_gate'])
        self.assertEqual(skill_caps.pick_schema_version(caps), 'scene-description.v1')

    def test_new_skill(self):
        with tempfile.TemporaryDirectory() as d:
            caps = skill_caps.detect(make_skill(Path(d) / 's', True, ['scene-description.v1', '1.0', '1.1'], True))
        self.assertTrue(all(caps[k] for k in ('scene_fit_key', 'box_fit_fit_flag', 'box_fit_leave_one_out', 'box_fit_robust_loss',
                                              'box_fit_check_failed', 'compile_quality_gate')))
        self.assertEqual(skill_caps.pick_schema_version(caps), '1.1')

    def test_schema_version_choice(self):
        base = {'scene_fit_key': False}
        self.assertEqual(skill_caps.pick_schema_version(dict(base, scene_schema_versions=['1.0', '1.1'])), '1.0')
        self.assertEqual(skill_caps.pick_schema_version(dict(base, scene_fit_key=True, scene_schema_versions=['1.0', '1.1'])), '1.1')
        self.assertEqual(skill_caps.pick_schema_version(dict(base, scene_fit_key=True, scene_schema_versions=['scene-description.v1'])), 'scene-description.v1')

    def test_a_folder_that_is_not_a_skill_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                skill_caps.detect(d)

    def test_version_fingerprint_and_commit(self):
        with tempfile.TemporaryDirectory() as d:
            skill = make_skill(Path(d) / 'baseline' / 's', False, ['scene-description.v1'], False)
            (Path(d) / 'baseline' / 'COMMIT.txt').write_text('baseline of abc1234\n', encoding='utf-8')
            self.assertEqual(skill_caps.skill_version(skill), '9.9.9-rc1')
            self.assertEqual(skill_caps.baseline_commit(skill), 'abc1234')
            before = skill_caps.fingerprint(skill)
            (skill / 'scripts' / 'box_fit.py').write_text(NEW_BOX_FIT, encoding='utf-8')
            self.assertNotEqual(skill_caps.fingerprint(skill), before)

    def test_fingerprint_ignores_line_endings(self):
        with tempfile.TemporaryDirectory() as d:
            a = make_skill(Path(d) / 'a', False, ['scene-description.v1'], False)
            b = make_skill(Path(d) / 'b', False, ['scene-description.v1'], False)
            text = (b / 'scripts' / 'box_fit.py').read_text(encoding='utf-8')
            (b / 'scripts' / 'box_fit.py').write_bytes(text.replace('\n', '\r\n').encode('utf-8'))
            self.assertEqual(skill_caps.fingerprint(a), skill_caps.fingerprint(b))

    def test_the_repository_skill_can_be_probed(self):
        caps = skill_caps.detect(ROOT / 'booth-image-to-model')
        self.assertIn('scene_schema_versions', caps)
        self.assertTrue(skill_caps.skill_version(ROOT / 'booth-image-to-model'))


class Adapters(unittest.TestCase):
    def test_old_skill_gets_no_fit_keys_and_the_old_version(self):
        caps = {'scene_fit_key': False, 'scene_schema_versions': ['scene-description.v1'], 'box_fit_fit_flag': False}
        scene = skill_caps.adapt_scene(dict(SCENE, corner_points=[dict(p, fit=True) for p in SCENE['corner_points']]), caps, {'a': False})
        self.assertTrue(all('fit' not in p for p in scene['corner_points']))
        self.assertEqual(scene['schema_version'], 'scene-description.v1')
        points = skill_caps.adapt_points(dict(POINTS, points=[dict(p, fit=False) for p in POINTS['points']]), caps, {'a': False})
        self.assertTrue(all('fit' not in p for p in points['points']))

    def test_new_skill_gets_fit_on_every_corner(self):
        caps = {'scene_fit_key': True, 'scene_schema_versions': ['scene-description.v1', '1.0', '1.1'], 'box_fit_fit_flag': True}
        scene = skill_caps.adapt_scene(SCENE, caps, {'a': False})
        self.assertEqual([p['fit'] for p in scene['corner_points']], [False, None])
        self.assertEqual(scene['schema_version'], '1.1')
        points = skill_caps.adapt_points(POINTS, caps, {'a': False})
        self.assertEqual(points['points'][0]['fit'], False)
        self.assertNotIn('fit', points['points'][1])

    def test_component_keys_a_newer_schema_adds_are_filled_with_null(self):
        caps = {'scene_fit_key': False, 'scene_schema_versions': ['1.0'], 'component_keys': ['id', 'type', 'new_key']}
        scene = skill_caps.adapt_scene(dict(SCENE, components=[{'id': 'w', 'type': 'wall'}]), caps)
        self.assertEqual(scene['components'][0], {'id': 'w', 'type': 'wall', 'new_key': None})

    def test_inputs_are_not_modified(self):
        caps = {'scene_fit_key': True, 'scene_schema_versions': ['1.1'], 'box_fit_fit_flag': True}
        skill_caps.adapt_scene(SCENE, caps, {'a': False})
        skill_caps.adapt_points(POINTS, caps, {'a': False})
        self.assertNotIn('fit', SCENE['corner_points'][0])
        self.assertEqual(SCENE['schema_version'], 'scene-description.v1')
        self.assertNotIn('fit', POINTS['points'][0])


if __name__ == '__main__':
    unittest.main()
