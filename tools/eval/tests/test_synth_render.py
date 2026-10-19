"""The parts of synth_render.py that need no Blender: encoding, decoding, degrading, overlays and the truth fit."""
import sys
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import synth_generate as sg
import synth_render as sr


class MaskCodec(unittest.TestCase):
    def test_ids_round_trip_through_colours(self):
        ids = [1, 2, 255, 256, 257, 1000, 65535]
        rgb = np.array([[sr.id_to_rgb8(i) for i in ids]], dtype=np.uint8)
        decoded, bad = sr.decode_mask(rgb, ids)
        self.assertEqual(decoded.tolist(), [ids])
        self.assertEqual(bad, 0.0)

    def test_pixels_that_match_no_object_become_background(self):
        rgb = np.zeros((2, 2, 3), np.uint8)
        rgb[0, 0] = sr.id_to_rgb8(5)
        rgb[0, 1] = sr.id_to_rgb8(9)                 # not an object
        rgb[1, 0] = (5, 0, 7)                        # blue is never used
        decoded, bad = sr.decode_mask(rgb, [5])
        self.assertEqual(decoded.tolist(), [[5, 0], [0, 0]])
        self.assertAlmostEqual(bad, 0.5)

    def test_id_range_is_checked(self):
        for bad in (0, 65536, -1):
            with self.assertRaises(ValueError):
                sr.id_to_rgb8(bad)

    def test_srgb_to_linear_endpoints(self):
        self.assertEqual(sr.srgb_to_linear(0.0), 0.0)
        self.assertAlmostEqual(sr.srgb_to_linear(1.0), 1.0)
        self.assertLess(sr.srgb_to_linear(0.5), 0.5)


class Degrading(unittest.TestCase):
    def picture(self):
        arr = np.zeros((60, 80, 3), np.uint8)
        arr[:, 40:] = 255
        return Image.fromarray(arr, 'RGB')

    def test_parse_post(self):
        self.assertEqual(sr.parse_post('jpeg=85,noise=2,blur=0.6'), {'jpeg': 85, 'noise': 2.0, 'blur': 0.6})
        self.assertEqual(sr.parse_post(''), {})
        for bad in ('jpeg', 'gamma=2', 'noise='):
            with self.assertRaises(ValueError):
                sr.parse_post(bad)

    def test_no_post_returns_the_same_pixels(self):
        img = self.picture()
        self.assertTrue((np.asarray(sr.degrade(img, {})) == np.asarray(img)).all())

    def test_noise_is_seeded_and_changes_the_picture(self):
        img = self.picture()
        a, b = sr.degrade(img, {'noise': 4.0}, seed=1), sr.degrade(img, {'noise': 4.0}, seed=1)
        c = sr.degrade(img, {'noise': 4.0}, seed=2)
        self.assertTrue((np.asarray(a) == np.asarray(b)).all())
        self.assertFalse((np.asarray(a) == np.asarray(c)).all())
        self.assertGreater(np.abs(np.asarray(a, float) - np.asarray(img, float)).mean(), 0.5)

    def test_blur_softens_an_edge_and_jpeg_keeps_the_size(self):
        img = self.picture()
        soft = np.asarray(sr.degrade(img, {'blur': 2.0}), float)
        self.assertTrue(0 < soft[30, 39, 0] < 255 and 0 < soft[30, 40, 0] < 255)
        out = sr.degrade(img, {'jpeg': 30})
        self.assertEqual(out.size, img.size)
        self.assertEqual(out.mode, 'RGB')


class CaseHelpers(unittest.TestCase):
    CASE, _ = sg.build_case(20261019, 3)

    def test_truth_fit_is_the_true_camera_and_dimensions(self):
        fit = sr.truth_fit(self.CASE)
        self.assertEqual(fit['dims_m'], self.CASE['dims_m'])
        self.assertEqual(fit['camera']['position'], self.CASE['camera']['position'])
        self.assertEqual(fit['camera']['fit_status'], 'landmark_fitted')
        self.assertEqual(fit['image'], {'width': self.CASE['image']['width'], 'height': self.CASE['image']['height']})
        self.assertEqual(fit['floor_z'], self.CASE['floor_z'])
        self.assertNotIn('fit_status', self.CASE['camera'])              # the truth fit does not change the case

    def test_pieces_belong_to_their_part(self):
        pid = self.CASE['parts'][0]['id']
        self.assertEqual(sr.part_of(self.CASE, pid + '_body'), pid)
        self.assertEqual(sr.part_of(self.CASE, pid), pid)
        self.assertIsNone(sr.part_of(self.CASE, 'wall_back'))
        self.assertIsNone(sr.part_of(self.CASE, pid + 'x'))

    def test_overlay_marks_every_corner(self):
        w, h = self.CASE['image']['width'], self.CASE['image']['height']
        img = Image.new('RGB', (w, h), (128, 128, 128))
        marked = sr.draw_overlay(self.CASE, img)
        self.assertEqual(marked.size, img.size)
        arr = np.asarray(marked)
        for c in self.CASE['corners']:
            x, y = c['uv_px']
            ring = arr[max(0, int(round(y)) - 8):int(round(y)) + 9, max(0, int(round(x)) - 8):int(round(x)) + 9]
            self.assertTrue((ring != 128).any(), c['id'])

    def test_blender_lookup_prefers_an_explicit_path(self):
        self.assertIsNone(sr.find_blender('/no/such/blender-executable'))

    def test_render_log_records_which_skill_built_the_picture(self):
        import skill_caps
        skill = Path(__file__).resolve().parents[3] / 'booth-image-to-model'
        record = sr.skill_record(skill)
        self.assertEqual(record['skill_fingerprint'], skill_caps.fingerprint(skill))
        self.assertEqual(record['skill_version'], skill_caps.skill_version(skill))
        self.assertEqual(record['skill_label'], 'booth-image-to-model')
        self.assertEqual(set(record), {'skill_label', 'skill_version', 'skill_commit', 'skill_fingerprint'})
        self.assertFalse(Path(record['skill_label']).is_absolute())
        import tempfile
        with tempfile.TemporaryDirectory() as d:                         # a copy elsewhere gets a label without an absolute path
            (Path(d) / 'scripts').mkdir()
            (Path(d) / 'scripts' / 'a.py').write_text('x = 1\n', encoding='utf-8')
            other = sr.skill_record(Path(d))
        self.assertNotEqual(other['skill_fingerprint'], record['skill_fingerprint'])
        self.assertNotIn(d.replace(chr(92), '/'), other['skill_label'])


if __name__ == '__main__':
    unittest.main()
