import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import camgeom


def sample_camera(yaw=0.3, pitch=0.35, roll=0.02, f_px=1200.0, w=1600, h=1000, pos=(2.0, -9.0, 3.0), pp=None):
    return camgeom.make_camera(pos, yaw, pitch, roll, f_px, w, h, principal_px=pp, distance=10.0)


class CameraGeometry(unittest.TestCase):
    def test_project_then_unproject_returns_the_point(self):
        cam = sample_camera()
        for point in ([0.0, 0.0, 0.0], [1.5, 2.0, 0.0], [-2.0, 1.0, 0.0]):
            u, v, _ = camgeom.project(point, cam, 1600, 1000)
            back = camgeom.unproject_to_z(u, v, cam, 1600, 1000, 0.0)
            for a, b in zip(point, back):
                self.assertAlmostEqual(a, b, places=5)

    def test_principal_point_offset_moves_the_image_centre(self):
        centred = sample_camera()
        shifted = sample_camera(pp=(700.0, 520.0))
        aim = [shifted['position'][i] + (shifted['target'][i] - shifted['position'][i]) * 0.5 for i in range(3)]
        u, v, _ = camgeom.project(aim, shifted, 1600, 1000)
        self.assertAlmostEqual(u, 700.0, places=2)
        self.assertAlmostEqual(v, 520.0, places=2)
        u0, v0, _ = camgeom.project(aim, centred, 1600, 1000)
        self.assertAlmostEqual(u0, 800.0, places=2)
        self.assertAlmostEqual(v0, 500.0, places=2)

    def test_point_behind_the_camera_is_refused(self):
        cam = sample_camera()
        with self.assertRaises(ValueError):
            camgeom.project([2.0, -40.0, 3.0], cam, 1600, 1000)
        self.assertIsNone(camgeom.project_or_none([2.0, -40.0, 3.0], cam, 1600, 1000))

    def test_horizontal_fov_follows_the_lens(self):
        cam = sample_camera(f_px=1600.0)            # f = width: 36 mm lens on a 36 mm sensor
        self.assertAlmostEqual(cam['lens_mm'], 36.0, places=4)
        self.assertAlmostEqual(camgeom.hfov_deg(cam), 53.13, places=1)

    def test_rotation_angle_between_cameras(self):
        a = sample_camera(yaw=0.3, pitch=0.35, roll=0.0)
        self.assertAlmostEqual(camgeom.rotation_angle_deg(a, a), 0.0, places=4)
        b = sample_camera(yaw=0.3 + math.radians(5), pitch=0.35, roll=0.0)
        self.assertAlmostEqual(camgeom.rotation_angle_deg(a, b), 5.0, delta=0.05)
        c = sample_camera(yaw=0.3, pitch=0.35 + math.radians(3), roll=0.0)
        self.assertAlmostEqual(camgeom.rotation_angle_deg(a, c), 3.0, delta=0.05)

    def test_convention_matches_the_skills_box_fit_forward_vector(self):
        # yaw 0, pitch 0 looks along +Y; positive pitch looks down
        f = camgeom.forward(0.0, 0.0)
        self.assertEqual([round(v, 9) for v in f], [0.0, 1.0, 0.0])
        self.assertLess(camgeom.forward(0.0, 0.3)[2], 0.0)


class AgreesWithTheSkill(unittest.TestCase):
    """The truth must use the skill's convention: compare with scripts/camera_math.py of the repository skill."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        path = Path(__file__).resolve().parents[3] / 'booth-image-to-model' / 'scripts' / 'camera_math.py'
        spec = importlib.util.spec_from_file_location('skill_camera_math_for_parity_test', path)
        cls.skill = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.skill)

    def test_projection_unprojection_and_axes_agree(self):
        import random
        rng = random.Random(4)
        for _ in range(40):
            w, h = rng.choice([(1600, 1000), (1280, 720), (1024, 768)])
            cam = camgeom.make_camera([rng.uniform(-6, 6), rng.uniform(-14, -4), rng.uniform(1, 6)], rng.uniform(-0.6, 0.6), rng.uniform(0.1, 0.8),
                                      rng.uniform(-0.03, 0.03), rng.uniform(0.6, 1.5) * w, w, h,
                                      principal_px=(w * rng.uniform(0.4, 0.6), h * rng.uniform(0.4, 0.6)), distance=rng.uniform(5, 15))
            point = [rng.uniform(-3, 3), rng.uniform(-3, 3), rng.uniform(0, 3)]
            mine = camgeom.project(point, cam, w, h)
            theirs = self.skill.project(point, cam, w, h)
            for a, b in zip(mine, theirs):
                self.assertAlmostEqual(a, b, places=7)
            back_a = camgeom.unproject_to_z(mine[0], mine[1], cam, w, h, point[2])
            back_b = self.skill.unproject(mine[0], mine[1], cam, w, h, 'z', point[2])
            for a, b in zip(back_a, back_b):
                self.assertAlmostEqual(a, b, places=6)
            for a, b in zip(camgeom.axes(cam), self.skill.axes(cam)):
                for x, y in zip(a, b):
                    self.assertAlmostEqual(x, y, places=9)


if __name__ == '__main__':
    unittest.main()
