"""P6 tests for the synthetic self-play bench: placement error in metres, per-part IoU from the instance mask, perturbation and the curve.

Everything is built here from small programmatic fixtures: no Blender, no real picture.
"""
from __future__ import annotations
import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0]))        # tools/poc, so that `loop` is the package

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402
from loop import selfplay  # noqa: E402

W_IMG, H_IMG = 100, 80


def make_case():
    return {'case_id': 'synth_fixture', 'dims_m': {'W': 6.0, 'D': 3.0, 'H': 3.0}, 'floor_z': 0.0,
            'parts': [{'id': 'counter_1', 'type': 'counter', 'center_m': [1.0, 0.5]},
                      {'id': 'box_2', 'type': 'box', 'center_m': [-1.5, -0.5]},
                      {'id': 'planter_3', 'type': 'planter', 'center_m': [2.0, -1.0]}]}


def _box(cid, x, y, color=(0.5, 0.5, 0.5, 1)):
    return {'id': cid, 'kind': 'box', 'location': [x, y, 0.5], 'rotation_deg': [0, 0, 0], 'params': {'size': [1.0, 0.6, 1.0]},
            'scope_id': 'furniture', 'material': {'color': list(color)}}


def make_spec(case, offsets=None, drop=()):
    """A spec whose parts stand exactly at their true centres (plus optional offsets); each part is two pieces named <id>_<piece>."""
    offsets = offsets or {}
    comps = [{'id': 'wall_back', 'kind': 'box', 'location': [0, 1.5, 1.5], 'rotation_deg': [0, 0, 0], 'params': {'size': [6, 0.05, 3]},
              'scope_id': 'shell', 'material': {'color': [0.9, 0.9, 0.9, 1]}}]
    for p in case['parts']:
        if p['id'] in drop:
            continue
        dx, dy = offsets.get(p['id'], (0.0, 0.0))
        x, y = p['center_m'][0] + dx, p['center_m'][1] + dy
        comps.append(_box(p['id'] + '_body', x, y))
        comps.append(_box(p['id'] + '_top', x, y))
    return {'schema_version': '1.0', 'components': comps}


def write_json(path, value):
    Path(path).write_text(json.dumps(value), encoding='utf-8')


def encode_ids(ids):
    """int array -> RGB image with id = R + 256 * G, the way synth_render writes mask.png."""
    rgb = np.zeros(ids.shape + (3,), np.uint8)
    rgb[..., 0] = ids & 255
    rgb[..., 1] = ids >> 8
    return Image.fromarray(rgb, 'RGB')


def make_mask_fixture(folder):
    """mask.png + mask_index.json. counter_1 owns instance ids 7 and 300 (300 needs the G channel), box_2 owns id 2 and
    planter_3 id 5. Object ids are NOT in part order on purpose."""
    ids = np.zeros((H_IMG, W_IMG), np.uint32)
    ids[10:30, 10:40] = 7          # counter_1, piece one
    ids[30:40, 10:40] = 300        # counter_1, piece two (id above 255)
    ids[50:70, 50:80] = 2          # box_2
    ids[5:15, 80:95] = 5           # planter_3
    encode_ids(ids).save(Path(folder) / 'mask.png')
    objects = [{'id': 5, 'kind': 'component', 'component_id': 'planter_3_body', 'part_id': 'planter_3'},
               {'id': 2, 'kind': 'component', 'component_id': 'box_2_body', 'part_id': 'box_2'},
               {'id': 300, 'kind': 'component', 'component_id': 'counter_1_top', 'part_id': 'counter_1'},
               {'id': 7, 'kind': 'component', 'component_id': 'counter_1_body', 'part_id': 'counter_1'},
               {'id': 9, 'kind': 'component', 'component_id': 'wall_back', 'part_id': None}]
    write_json(Path(folder) / 'mask_index.json', {'objects': objects})
    return ids


def save_part_mask(folder, part_id, region):
    arr = np.zeros((H_IMG, W_IMG), np.uint8)
    y0, y1, x0, x1 = region
    arr[y0:y1, x0:x1] = 255
    Image.fromarray(arr, 'L').save(Path(folder) / f'{part_id}.png')


class PlacementTests(unittest.TestCase):
    def test_truth_candidate_has_zero_placement_error(self):
        case = make_case()
        rep = selfplay.placement_report(case, make_spec(case))
        self.assertEqual(rep['missing'], 0)
        self.assertAlmostEqual(rep['median_m'], 0.0, places=9)
        self.assertAlmostEqual(rep['max_m'], 0.0, places=9)
        for pid, row in rep['per_part'].items():
            self.assertEqual(row['status'], 'ok', pid)
            self.assertAlmostEqual(row['error_m'], 0.0, places=9)

    def test_moved_part_error_is_reported_in_metres(self):
        case = make_case()
        spec = make_spec(case, offsets={'box_2': (0.3, 0.0)})
        rep = selfplay.placement_report(case, spec)
        self.assertAlmostEqual(rep['per_part']['box_2']['error_m'], 0.3, delta=0.01)    # metres, not centimetres or millimetres
        self.assertAlmostEqual(rep['max_m'], 0.3, delta=0.01)
        self.assertAlmostEqual(rep['per_part']['counter_1']['error_m'], 0.0, places=9)
        diag = selfplay.placement_report(case, make_spec(case, offsets={'box_2': (0.3, 0.4)}))
        self.assertAlmostEqual(diag['per_part']['box_2']['error_m'], 0.5, delta=0.01)

    def test_part_missing_from_candidate_is_missing_not_zero(self):
        case = make_case()
        rep = selfplay.placement_report(case, make_spec(case, drop=('box_2',)))
        self.assertEqual(rep['missing'], 1)
        row = rep['per_part']['box_2']
        self.assertEqual(row['status'], 'missing')
        self.assertIsNone(row['error_m'])
        self.assertEqual(rep['per_part']['counter_1']['status'], 'ok')

    def test_every_part_missing_gives_no_median(self):
        case = make_case()
        rep = selfplay.placement_report(case, make_spec(case, drop=('counter_1', 'box_2', 'planter_3')))
        self.assertEqual(rep['missing'], 3)
        self.assertIsNone(rep['median_m'])


class MaskIouTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.render, self.masks = Path(self._tmp.name) / 'render', Path(self._tmp.name) / 'part_masks'
        self.render.mkdir()
        self.masks.mkdir()
        make_mask_fixture(self.render)
        self.case = make_case()
        self.assignment = selfplay.assign_parts(self.case, make_spec(self.case))

    def test_mask_iou_uses_instance_ids_from_mask_index(self):
        # a full build writes one mask per COMPONENT; a part is the union of its components
        save_part_mask(self.masks, 'counter_1_body', (10, 30, 10, 40))      # truth id 7
        save_part_mask(self.masks, 'counter_1_top', (30, 40, 10, 40))       # truth id 300, above 255, so the G channel must be decoded
        save_part_mask(self.masks, 'box_2_body', (50, 70, 50, 80))          # truth id 2
        save_part_mask(self.masks, 'box_2_top', (50, 70, 50, 80))
        save_part_mask(self.masks, 'planter_3_body', (5, 15, 0, 40))        # wrong place: no overlap with id 5 at x 80..95
        rep = selfplay.iou_report(self.case, self.render, self.masks, self.assignment)
        self.assertAlmostEqual(rep['per_part']['counter_1']['iou'], 1.0, places=6)
        self.assertAlmostEqual(rep['per_part']['box_2']['iou'], 1.0, places=6)
        self.assertAlmostEqual(rep['per_part']['planter_3']['iou'], 0.0, places=6)
        self.assertAlmostEqual(rep['mean'], 2.0 / 3.0, places=6)
        self.assertEqual(rep['per_part']['counter_1']['components_with_mask'], ['counter_1_body', 'counter_1_top'])

    def test_a_multi_component_part_is_not_missing_because_its_id_has_no_file(self):
        save_part_mask(self.masks, 'counter_1_body', (10, 30, 10, 40))
        rep = selfplay.iou_report(self.case, self.render, self.masks, self.assignment)
        row = rep['per_part']['counter_1']
        self.assertEqual(row['status'], 'ok')                               # no counter_1.png exists, only component masks
        self.assertEqual(row['components_without_mask'], ['counter_1_top'])
        self.assertAlmostEqual(row['iou'], 600.0 / 900.0, places=6)         # the body alone covers 20 x 30 of 30 x 30

    def test_partial_overlap_gives_fractional_iou(self):
        save_part_mask(self.masks, 'box_2_body', (50, 70, 50, 65))          # half of the 30 x 20 truth box
        rep = selfplay.iou_report(self.case, self.render, self.masks, self.assignment)
        self.assertAlmostEqual(rep['per_part']['box_2']['iou'], 0.5, places=6)

    def test_part_without_any_mask_file_or_component_scores_zero_and_is_marked(self):
        save_part_mask(self.masks, 'counter_1_body', (10, 30, 10, 40))
        save_part_mask(self.masks, 'counter_1_top', (30, 40, 10, 40))
        rep = selfplay.iou_report(self.case, self.render, self.masks, self.assignment)
        self.assertEqual(rep['per_part']['box_2']['status'], 'missing_candidate_mask')
        self.assertIsNone(rep['per_part']['box_2']['iou'])
        self.assertAlmostEqual(rep['mean'], 1.0 / 3.0, places=6)
        gone = selfplay.assign_parts(self.case, make_spec(self.case, drop=('planter_3',)))
        rep = selfplay.iou_report(self.case, self.render, self.masks, gone)
        self.assertEqual(rep['per_part']['planter_3']['status'], 'missing_part')

    def test_evaluate_end_to_end_from_a_case_folder(self):
        tmp = Path(self._tmp.name)
        write_json(tmp / 'case.json', self.case)
        make_mask_fixture(tmp)
        save_part_mask(self.masks, 'counter_1_body', (10, 30, 10, 40))
        save_part_mask(self.masks, 'counter_1_top', (30, 40, 10, 40))
        write_json(tmp / 'cand.json', make_spec(self.case, offsets={'planter_3': (0.0, 0.2)}))
        result = selfplay.evaluate(tmp, tmp / 'cand.json', masks_dir=self.masks)
        self.assertEqual(result['schema_version'], 'selfplay-eval.v1')
        self.assertNotIn('schema', result)
        self.assertEqual(result['case_id'], 'synth_fixture')
        self.assertAlmostEqual(result['placement']['per_part']['planter_3']['error_m'], 0.2, delta=0.01)
        self.assertAlmostEqual(result['iou']['per_part']['counter_1']['iou'], 1.0, places=6)
        self.assertIsNone(selfplay.evaluate(tmp, tmp / 'cand.json')['iou'])      # no --masks, no IoU


class MasksFolderTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.case = make_case()
        write_json(self.dir / 'case.json', self.case)
        make_mask_fixture(self.dir)
        write_json(self.dir / 'cand.json', make_spec(self.case))
        self.masks = self.dir / 'part_masks'

    def run_cli(self, out):
        return selfplay.main(['evaluate', '--case', str(self.dir), '--candidate', str(self.dir / 'cand.json'),
                              '--masks', str(self.masks), '--out', str(out)])

    def test_missing_masks_folder_is_an_error_and_writes_nothing(self):
        out = self.dir / 'evaluate.json'
        with self.assertRaisesRegex(ValueError, 'MASKS_MISSING'):
            selfplay.evaluate(self.dir, self.dir / 'cand.json', masks_dir=self.masks)
        self.assertEqual(self.run_cli(out), 2)
        self.assertFalse(out.exists())

    def test_empty_folder_or_folder_without_any_component_mask_is_an_error(self):
        self.masks.mkdir()
        out = self.dir / 'evaluate.json'
        self.assertEqual(self.run_cli(out), 2)
        save_part_mask(self.masks, 'some_other_thing', (0, 5, 0, 5))                    # a mask, but of no component of the candidate
        self.assertEqual(self.run_cli(out), 2)
        self.assertFalse(out.exists())
        with self.assertRaisesRegex(ValueError, 'MASKS_MISSING'):
            selfplay.evaluate(self.dir, self.dir / 'cand.json', masks_dir=self.masks)

    def test_some_missing_component_masks_are_marked_incomplete_not_failed(self):
        self.masks.mkdir()
        save_part_mask(self.masks, 'counter_1_body', (10, 30, 10, 40))
        out = self.dir / 'evaluate.json'
        self.assertEqual(self.run_cli(out), 0)
        result = json.loads(out.read_text(encoding='utf-8'))
        self.assertTrue(result['masks_incomplete'])
        self.assertTrue(result['iou']['masks_incomplete'])
        self.assertEqual(result['iou']['per_part']['box_2']['status'], 'missing_candidate_mask')

    def test_all_component_masks_present_is_complete(self):
        self.masks.mkdir()
        for c in make_spec(self.case)['components']:
            save_part_mask(self.masks, c['id'], (0, 5, 0, 5))
        result = selfplay.evaluate(self.dir, self.dir / 'cand.json', masks_dir=self.masks)
        self.assertFalse(result['masks_incomplete'])
        self.assertFalse(result['iou']['masks_incomplete'])
        self.assertFalse(selfplay.evaluate(self.dir, self.dir / 'cand.json')['masks_incomplete'])      # no masks asked for: not incomplete


def group_spec(case, groups):
    """A candidate whose names are not the true ids. groups: {group name: (x, y)}; each group is two boxes at that spot."""
    comps = [{'id': 'wall_back', 'kind': 'box', 'location': [0, 1.5, 1.5], 'rotation_deg': [0, 0, 0], 'params': {'size': [6, 0.05, 3]},
              'scope_id': 'shell', 'material': {'color': [0.9, 0.9, 0.9, 1]}}]
    for name, (x, y) in groups.items():
        comps.append(dict(_box(name + '_a', x, y), group=name))
        comps.append(dict(_box(name + '_b', x, y), group=name))
    return {'schema_version': '1.0', 'components': comps}


class MatchingTests(unittest.TestCase):
    def test_default_rule_matches_id_group_and_prefix(self):
        case = make_case()
        spec = make_spec(case)
        for c in spec['components']:
            c.pop('group', None)                                          # prefix only
        got = selfplay.assign_parts(case, spec)
        self.assertEqual(got['counter_1']['components'], ['counter_1_body', 'counter_1_top'])
        self.assertEqual(got['counter_1']['how'], 'exact')
        spec2 = group_spec(case, {'counter_1': (1.0, 0.5)})                # group name equals the part id
        self.assertEqual(selfplay.assign_parts(case, spec2)['counter_1']['components'], ['counter_1_a', 'counter_1_b'])

    def test_map_names_components_or_groups_and_drives_placement_and_iou(self):
        case = make_case()
        spec = group_spec(case, {'desk': (1.3, 0.5), 'crate': (-1.5, -0.5)})
        mapping = {'counter_1': 'desk', 'box_2': ['crate_a', 'crate_b']}
        got = selfplay.assign_parts(case, spec, mapping)
        self.assertEqual(got['counter_1']['components'], ['desk_a', 'desk_b'])
        self.assertEqual(got['box_2']['components'], ['crate_a', 'crate_b'])
        self.assertEqual(got['counter_1']['how'], 'map')
        self.assertEqual(got['planter_3']['components'], [])               # not in the map, no id/prefix hit: missing
        rep = selfplay.placement_report(case, spec, got)
        self.assertAlmostEqual(rep['per_part']['counter_1']['error_m'], 0.3, delta=0.01)
        self.assertAlmostEqual(rep['per_part']['box_2']['error_m'], 0.0, places=9)
        self.assertEqual(rep['per_part']['planter_3']['status'], 'missing')
        with tempfile.TemporaryDirectory() as tmp:
            render, masks = Path(tmp) / 'render', Path(tmp) / 'masks'
            render.mkdir()
            masks.mkdir()
            make_mask_fixture(render)
            save_part_mask(masks, 'crate_a', (50, 70, 50, 80))
            save_part_mask(masks, 'desk_a', (10, 40, 10, 40))
            rep = selfplay.iou_report(case, render, masks, got)
            self.assertAlmostEqual(rep['per_part']['box_2']['iou'], 1.0, places=6)
            self.assertAlmostEqual(rep['per_part']['counter_1']['iou'], 1.0, places=6)

    def test_a_map_key_that_is_not_a_true_part_id_is_an_error(self):
        case = make_case()
        spec = group_spec(case, {'desk': (1.3, 0.5)})
        with self.assertRaisesRegex(ValueError, 'MAP.*counter_9'):
            selfplay.assign_parts(case, spec, {'counter_9': ['desk']})                    # a typo for counter_1
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            write_json(tmp / 'case.json', case)
            write_json(tmp / 'cand.json', spec)
            write_json(tmp / 'map.json', {'counter_1': 'desk', 'counter_9': 'desk'})
            out = tmp / 'evaluate.json'
            args = ['evaluate', '--case', str(tmp), '--candidate', str(tmp / 'cand.json'), '--map', str(tmp / 'map.json'), '--out', str(out)]
            self.assertEqual(selfplay.main(args), 2)
            self.assertFalse(out.exists())
            write_json(tmp / 'map.json', {'counter_1': 'desk'})
            self.assertEqual(selfplay.main(args), 0)

    def test_load_map_accepts_strings_and_lists_and_rejects_the_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_json(Path(tmp) / 'm.json', {'a': 'x', 'b': ['y', 'z']})
            self.assertEqual(selfplay.load_map(Path(tmp) / 'm.json'), {'a': ['x'], 'b': ['y', 'z']})
            write_json(Path(tmp) / 'bad.json', {'a': 3})
            with self.assertRaises(ValueError):
                selfplay.load_map(Path(tmp) / 'bad.json')

    def test_nearest_matches_one_to_one_by_distance_and_records_the_components(self):
        case = make_case()
        spec = group_spec(case, {'north': (1.1, 0.5), 'west': (-1.5, 0.3), 'far': (5.0, 5.0)})
        got = selfplay.assign_parts(case, spec, match='nearest')
        self.assertEqual(got['counter_1']['components'], ['north_a', 'north_b'])
        self.assertAlmostEqual(got['counter_1']['distance_m'], 0.1, delta=0.01)
        self.assertEqual(got['box_2']['components'], ['west_a', 'west_b'])
        self.assertEqual(got['box_2']['how'], 'nearest')
        self.assertEqual(got['planter_3']['components'], [])               # 'far' is more than 1 m from it
        rep = selfplay.placement_report(case, spec, got)
        self.assertEqual(rep['per_part']['planter_3']['status'], 'missing')
        self.assertEqual(rep['missing'], 1)
        self.assertAlmostEqual(rep['per_part']['counter_1']['error_m'], 0.1, delta=0.01)
        self.assertEqual(rep['per_part']['counter_1']['matched'], ['north_a', 'north_b'])

    def test_nearest_beyond_one_metre_is_missing_and_a_group_is_used_once(self):
        case = make_case()
        spec = group_spec(case, {'west': (-1.5, 0.6)})                     # 1.1 m from box_2
        self.assertEqual(selfplay.assign_parts(case, spec, match='nearest')['box_2']['components'], [])
        spec = group_spec(case, {'only': (1.0, 0.0)})                      # 0.5 m from counter_1, 2.5 m from the others
        got = selfplay.assign_parts(case, spec, match='nearest')
        self.assertEqual([p for p, a in got.items() if a['components']], ['counter_1'])
        case2 = make_case()
        case2['parts'][1]['center_m'] = [1.2, 0.5]                          # two true parts compete for one group
        got = selfplay.assign_parts(case2, group_spec(case2, {'one': (1.1, 0.5)}), match='nearest')
        self.assertEqual(sum(1 for a in got.values() if a['components']), 1)

    def test_nearest_does_not_hand_a_graphic_panel_to_a_part_the_reader_missed(self):
        case = make_case()
        spec = group_spec(case, {'north': (1.1, 0.5), 'west': (-1.5, 0.3)})          # the reader found no planter
        logo = _box('logo_panel', 2.0, -0.12)                                          # 0.88 m from planter_3
        spec['components'].append(dict(logo, scope_id='graphics', role='graphic'))
        got = selfplay.assign_parts(case, spec, match='nearest')
        self.assertEqual(got['planter_3']['components'], [])
        rep = selfplay.placement_report(case, spec, got)
        self.assertEqual(rep['per_part']['planter_3']['status'], 'missing')
        self.assertEqual(rep['missing'], 1)

    def test_nearest_candidates_are_furniture_only(self):
        case = make_case()
        for scope in ('shell', 'booth_floor', 'media', 'lighting', 'graphics'):
            spec = group_spec(case, {})
            spec['components'].append(dict(_box('thing', 2.0, -1.0), scope_id=scope))   # right on top of planter_3
            self.assertEqual(selfplay.assign_parts(case, spec, match='nearest')['planter_3']['components'], [], scope)
        spec = group_spec(case, {})
        spec['components'].append(_box('thing', 2.0, -1.0))                              # furniture scope
        self.assertEqual(selfplay.assign_parts(case, spec, match='nearest')['planter_3']['components'], ['thing'])

    def test_nearest_never_offers_the_shell_to_a_part(self):
        case = make_case()
        case['parts'][0]['center_m'] = [0.0, 1.4]                           # right next to the back wall at y = 1.5
        got = selfplay.assign_parts(case, group_spec(case, {}), match='nearest')
        self.assertEqual(got['counter_1']['components'], [])


class MaskSpecAndTruthSheetTests(unittest.TestCase):
    """Uses the skill's compiler (pure Python) on a generated smoke case; no Blender. The mask is drawn here from the projected boxes."""
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        selfplay.prepare(cls.root, cases=3, seed=1)
        cls.cases = {}
        for name in ('synth_0000', 'synth_0001', 'synth_0002'):
            folder = cls.root / name
            selfplay._synth_render().build_truth_spec(folder, folder, selfplay.SKILL_DIR)
            case = json.loads((folder / 'case.json').read_text(encoding='utf-8'))
            spec = json.loads((folder / 'spec' / 'spec.json').read_text(encoding='utf-8'))
            fake_render(folder, case, spec)
            cls.cases[name] = (folder, case, spec)
        cls.case_dir, cls.case, cls.spec = cls.cases['synth_0002']

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_mask_spec_marks_every_component_critical_and_stays_valid(self):
        self.assertTrue(any(not c['critical'] for c in self.spec['components']))       # the compiler leaves furniture non-critical
        masked = selfplay.mask_spec(self.spec)
        self.assertTrue(all(c['critical'] is True for c in masked['components']))
        before = json.loads(json.dumps(self.spec))
        for c in before['components']:
            c['critical'] = True
        self.assertEqual(masked, before)                                  # nothing else changed
        self.assertTrue(any(not c['critical'] for c in self.spec['components']))       # and the input is untouched
        out = self.root / 'mask_spec.json'
        self.assertEqual(selfplay.main(['mask-spec', '--candidate', str(self.case_dir / 'spec' / 'spec.json'), '--out', str(out)]), 0)
        run = subprocess.run([sys.executable, str(selfplay.SKILL_DIR / 'scripts' / 'validate_spec.py'), '--spec', str(out)],
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertTrue(json.loads(run.stdout)['valid'])

    def test_truth_sheet_passes_the_score_validator(self):
        out = self.case_dir / 'truth'
        self.assertEqual(selfplay.main(['truth-sheet', '--case', str(self.case_dir), '--out', str(out)]), 0)
        run = subprocess.run([sys.executable, str(selfplay.POC_DIR / 'score' / 'score.py'), '--validate-truth', str(out)],
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        inv = json.loads((out / 'inventory.json').read_text(encoding='utf-8'))
        hold = json.loads((out / 'holdout_px.json').read_text(encoding='utf-8'))
        sw = json.loads((out / 'swatches.json').read_text(encoding='utf-8'))
        self.assertTrue(8 <= len(inv['items']) <= 15)
        self.assertTrue(set(p['id'] for p in self.case['parts']) <= {it['id'] for it in inv['items']})
        heroes = [it['id'] for it in inv['items'] if it['hero']]
        self.assertTrue(2 <= len(heroes) <= 3)
        self.assertTrue(set(heroes) <= {p['id'] for p in self.case['parts']})
        self.assertEqual(len(hold['points']), 4)
        w, h = self.case['image']['width'], self.case['image']['height']
        for p in hold['points']:
            self.assertTrue(0 <= p['uv_px'][0] <= w and 0 <= p['uv_px'][1] <= h)
        self.assertEqual([s['role'] for s in sw['swatches']], ['dominant', 'secondary', 'accent'])
        self.assertEqual(inv['picture_id'], self.case['case_id'])

    def test_truth_sheet_works_for_all_three_seed_1_cases(self):
        cam = selfplay._camera_math()
        for name, (folder, case, spec) in self.cases.items():
            out = folder / 'truth'
            self.assertEqual(selfplay.main(['truth-sheet', '--case', str(folder), '--out', str(out)]), 0, name)
            run = subprocess.run([sys.executable, str(selfplay.POC_DIR / 'score' / 'score.py'), '--validate-truth', str(out)],
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, name + run.stdout + run.stderr)
            hold = json.loads((out / 'holdout_px.json').read_text(encoding='utf-8'))
            inv = {it['id'] for it in json.loads((out / 'inventory.json').read_text(encoding='utf-8'))['items']}
            self.assertTrue({p['part_id'] for p in hold['points']} <= inv, name)
            self.assertFalse([p for p in hold['points'] if p['part_id'].startswith('round_table')], name)     # a cylinder's corner is in the air
            w, h = case['image']['width'], case['image']['height']
            depths = []
            for p in hold['points']:
                world = selfplay._score_metrics().corner_point(selfplay._part_bounds(spec, p['part_id']), p['corner'], case['camera'])
                depths.append(cam.project(world, case['camera'], w, h)[2])
            self.assertGreaterEqual(max(depths) - min(depths), 0.25, name)      # not all at one depth; no pairwise gap is asked for

    def test_holdout_choice_rejects_four_points_at_nearly_one_depth(self):
        def pt(name, depth, order, is_part=True):
            return {'id': name, 'corner': 'top_front_left', 'is_part': is_part, 'uv': (100.0 * (order + 1), 50.0), 'depth': depth,
                    'near': 9, 'rank': order, 'order': order}
        flat = [pt('a', 5.00, 0), pt('b', 5.10, 1), pt('c', 5.20, 2), pt('d', 5.05, 3)]      # spread 0.20 m: not allowed
        self.assertIsNone(selfplay._choose_holdout(flat))
        self.assertIsNotNone(selfplay._choose_holdout([pt('a', 5.00, 0), pt('b', 5.10, 1), pt('c', 5.20, 2), pt('d', 5.25, 3)]))   # 0.25 m
        # the earliest four in priority order are flat; the deep shell corner has to be taken instead of the last flat part
        pool = flat + [pt('floor', 5.60, 4, is_part=False)]
        chosen = selfplay._choose_holdout(pool)
        self.assertIsNotNone(chosen)
        self.assertIn('floor', {r['id'] for r in chosen})
        self.assertGreaterEqual(max(r['depth'] for r in chosen) - min(r['depth'] for r in chosen), 0.25)
        # no pairwise gap is asked for: three points 0.05 m apart are fine while one is 0.3 m deeper
        near = [pt('a', 5.00, 0), pt('b', 5.05, 1), pt('c', 5.10, 2), pt('d', 5.30, 3)]
        self.assertEqual(len(selfplay._choose_holdout(near)), 4)

    def test_truth_sheet_with_fewer_than_four_furniture_parts_takes_shell_corners(self):
        folder, case, spec = self.cases['synth_0000']                          # planter_1 and round_table_2 only
        self.assertEqual([p['id'] for p in case['parts']], ['planter_1', 'round_table_2'])
        selfplay.truth_sheet(folder, folder / 'truth_shell')
        hold = json.loads((folder / 'truth_shell' / 'holdout_px.json').read_text(encoding='utf-8'))
        ids = [p['part_id'] for p in hold['points']]
        self.assertEqual(len(ids), 4)
        self.assertTrue(any(i not in ('planter_1', 'round_table_2') for i in ids))
        inv = {it['id'] for it in json.loads((folder / 'truth_shell' / 'inventory.json').read_text(encoding='utf-8'))['items']}
        self.assertTrue(set(ids) <= inv)

    def test_truth_sheet_holdout_corners_are_projected_with_the_truth_camera(self):
        out = self.root / 'truth_check'
        selfplay.truth_sheet(self.case_dir, out)
        hold = json.loads((out / 'holdout_px.json').read_text(encoding='utf-8'))
        cam = selfplay._camera_math()
        s = selfplay._score_metrics()
        w, h = self.case['image']['width'], self.case['image']['height']
        for p in hold['points']:
            world = s.corner_point(selfplay._part_bounds(self.spec, p['part_id']), p['corner'], self.case['camera'])
            u, v, _ = cam.project(world, self.case['camera'], w, h)
            self.assertAlmostEqual(p['uv_px'][0], u, delta=0.06)
            self.assertAlmostEqual(p['uv_px'][1], v, delta=0.06)

    def test_truth_sheet_corner_names_are_read_from_the_camera_and_round_trip(self):
        s = selfplay._score_metrics()
        for name, (folder, case, spec) in self.cases.items():
            out = folder / 'truth_names'
            selfplay.truth_sheet(folder, out)
            hold = json.loads((out / 'holdout_px.json').read_text(encoding='utf-8'))
            w, h = case['image']['width'], case['image']['height']
            for p in hold['points']:
                bounds = selfplay._part_bounds(spec, p['part_id'])
                world = s.corner_point(bounds, p['corner'], case['camera'], w, h)
                self.assertIn(tuple(world), {tuple(c) for c in s.box_corners(bounds)}, (name, p))             # a real corner
                words = p['corner'].split('_')
                if len(words) == 3:
                    self.assertEqual(s.corner_name(world, bounds, case['camera']), p['corner'], (name, p))    # and the name returns
                u, v, _ = selfplay._camera_math().project(world, case['camera'], w, h)
                self.assertAlmostEqual(p['uv_px'][0], u, delta=0.06)
                self.assertAlmostEqual(p['uv_px'][1], v, delta=0.06)
                # left and right are the picture's: the corner lies on the named side of the box centre in the picture ...
                cm = selfplay._camera_math()
                centre = [(bounds[0][i] + bounds[1][i]) / 2 for i in range(3)]
                cu = cm.project(centre, case['camera'], w, h)[0]
                self.assertTrue((u <= cu + 0.5) if 'left' in words else (u >= cu - 0.5) if 'right' in words else True, (name, p, u, cu))
                # ... and, for three words, along the camera's right vector in space
                if len(words) == 3:
                    _, right, _ = cm.axes(case['camera'])
                    side = sum((world[i] - centre[i]) * right[i] for i in range(3))
                    self.assertTrue(side < 0 if 'left' in words else side > 0, (name, p, side))

    def test_truth_sheet_names_stay_the_same_when_the_camera_turns_a_little(self):
        for name, (folder, case, spec) in self.cases.items():
            assert_names_stable(self, name, folder, case, spec)

    def test_the_refusal_message_uses_the_margin_wording_of_03_section_2(self):
        folder, case, spec = self.cases['synth_0001']
        m = selfplay._score_metrics()
        real = m.name_margin
        m.name_margin = lambda *a, **k: False                              # no name keeps its corner
        try:
            with self.assertRaises(ValueError) as ctx:
                selfplay.truth_sheet(folder, folder / 'truth_nomargin')
        finally:
            m.name_margin = real
        text = str(ctx.exception)
        self.assertIn('orbits 5 degrees about the vertical axis through its target', text)
        self.assertIn('elevation', text)
        self.assertNotIn('turned by 5 degrees', text)

    def test_truth_sheet_refuses_a_case_too_small_for_a_valid_sheet(self):
        folder = self.root / 'tiny'
        folder.mkdir()
        case = dict(self.case, parts=self.case['parts'][:1])
        spec = dict(self.spec, components=[c for c in self.spec['components'] if c['id'].startswith(case['parts'][0]['id']) or c['id'] == 'frame'])
        write_json(folder / 'case.json', case)
        (folder / 'spec').mkdir()
        write_json(folder / 'spec' / 'spec.json', spec)
        write_json(folder / 'truth_fit.json', {'camera': self.case['camera']})
        fake_render(folder, case, spec)
        with self.assertRaises(ValueError):
            selfplay.truth_sheet(folder, folder / 'truth')


def perturbed_cameras(camera, yaw_deg=5.0, pitch_deg=2.0):
    """The camera turned about the vertical axis through its target by +-yaw, and lifted or lowered by +-pitch on the same sphere."""
    t = camera['target']
    v = [camera['position'][i] - t[i] for i in range(3)]
    out = []
    for dyaw, dpitch in ((yaw_deg, 0.0), (-yaw_deg, 0.0), (0.0, pitch_deg), (0.0, -pitch_deg)):
        r = math.hypot(v[0], v[1])
        elev = math.atan2(v[2], r) + math.radians(dpitch)
        horizontal = math.atan2(v[1], v[0]) + math.radians(dyaw)
        length = math.sqrt(sum(c * c for c in v))
        new = [t[0] + length * math.cos(elev) * math.cos(horizontal), t[1] + length * math.cos(elev) * math.sin(horizontal),
               t[2] + length * math.sin(elev)]
        out.append(dict(camera, position=new))
    return out


def assert_names_stable(test, label, folder, case, spec):
    """03 section 6 step 3: every held-out name keeps its corner when the camera yaws +-5 or pitches +-2 degrees."""
    s = selfplay._score_metrics()
    out = folder / 'truth_margin'
    selfplay.truth_sheet(folder, out)
    hold = json.loads((out / 'holdout_px.json').read_text(encoding='utf-8'))
    test.assertEqual(len(hold['points']), 4, label)
    w, h = case['image']['width'], case['image']['height']
    for p in hold['points']:
        bounds = selfplay._part_bounds(spec, p['part_id'])
        world = s.corner_point(bounds, p['corner'], case['camera'], w, h)
        for cam in perturbed_cameras(case['camera']):
            test.assertEqual(s.corner_point(bounds, p['corner'], cam, w, h), world, (label, p['id'], p['part_id'], p['corner']))
    return hold


class TruthSheetMarginSeed3Tests(unittest.TestCase):
    """The case that exposed the problem: a floor corner flipped by hundreds of pixels at a yaw of -4 degrees."""
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        selfplay.prepare(cls.root, cases=3, seed=3)
        cls.cases = {}
        for name in ('synth_0000', 'synth_0001', 'synth_0002'):
            folder = cls.root / name
            selfplay._synth_render().build_truth_spec(folder, folder, selfplay.SKILL_DIR)
            case = json.loads((folder / 'case.json').read_text(encoding='utf-8'))
            spec = json.loads((folder / 'spec' / 'spec.json').read_text(encoding='utf-8'))
            fake_render(folder, case, spec)
            cls.cases[name] = (folder, case, spec)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_names_stay_the_same_under_yaw_and_pitch_changes(self):
        for name in ('synth_0000', 'synth_0001', 'synth_0002'):
            folder, case, spec = self.cases[name]
            assert_names_stable(self, 'seed3 ' + name, folder, case, spec)

    def test_a_camera_within_five_degrees_of_45_gets_a_sheet_with_one_horizontal_word_names(self):
        # synth_0002 looks at the booth 3.8 degrees away from the diagonal, where every three-word name flips; the one-horizontal-word
        # names (top_left, bottom_front, ...) keep their corner there, so the sheet is written with those.
        folder, case, spec = self.cases['synth_0002']
        cam = case['camera']
        v = [cam['position'][i] - cam['target'][i] for i in range(3)]
        self.assertLess(abs(abs(math.degrees(math.atan2(v[1], v[0])) % 90.0) - 45.0), 5.0)
        hold = assert_names_stable(self, 'seed3 synth_0002', folder, case, spec)
        self.assertTrue(all(len(p['corner'].split('_')) == 2 for p in hold['points']), [p['corner'] for p in hold['points']])


def fake_render(folder, case, spec):
    """mask.png and mask_index.json as synth_render would write them, but each component is its projected bounding rectangle,
    painted in spec order. Ids start at 251 so that the G channel is used."""
    import numpy as np
    s = selfplay._score_metrics()
    cam = selfplay._camera_math()
    w, h = case['image']['width'], case['image']['height']
    ids = np.zeros((h, w), np.uint32)
    objects, boxes = [], []
    for n, c in enumerate(spec['components'], start=251):
        uv = []
        for pt in s.component_points(c):
            try:
                uv.append(cam.project(pt, case['camera'], w, h)[:2])
            except ValueError:
                pass
        if uv:
            x0, x1 = int(max(0, min(u for u, _ in uv))), int(min(w, max(u for u, _ in uv)))
            y0, y1 = int(max(0, min(v for _, v in uv))), int(min(h, max(v for _, v in uv)))
            boxes.append((0 if c['id'] == 'frame' else 1, n, (y0, y1, x0, x1)))      # the frame is a thin ring: it goes under the rest
        part = next((p['id'] for p in case['parts'] if c['id'] == p['id'] or c.get('group') == p['id']), None)
        objects.append({'id': n, 'name': c['id'], 'kind': 'component', 'component_id': c['id'], 'part_id': part})
    for _, n, (y0, y1, x0, x1) in sorted(boxes, key=lambda b: (b[0], b[1])):
        ids[y0:y1, x0:x1] = n
    encode_ids(ids).save(Path(folder) / 'mask.png')
    write_json(Path(folder) / 'mask_index.json', {'objects': objects})
    write_json(Path(folder) / 'truth_fit.json', {'camera': case['camera']})


class PerturbTests(unittest.TestCase):
    def test_perturbation_moves_deletes_recolours_and_records_it(self):
        case = make_case()
        spec = make_spec(case)
        out, record = selfplay.perturb_spec(case, spec, seed=1)
        self.assertEqual(spec, make_spec(case), 'the truth spec must not be modified in place')
        self.assertIsNotNone(record['deleted'])
        ids = {c['id'] for c in out['components']}
        self.assertNotIn(record['deleted'] + '_body', ids)
        self.assertIn('wall_back', ids)                                        # walls are never deleted
        self.assertEqual(len(record['moved']), 2)
        for pid, mv in record['moved'].items():
            self.assertGreaterEqual(mv['distance_m'], 0.1 - 1e-9)
            self.assertLessEqual(mv['distance_m'], 0.5 + 1e-9)
        rep = selfplay.placement_report(case, out)
        self.assertEqual(rep['per_part'][record['deleted']]['status'], 'missing')
        for pid, mv in record['moved'].items():
            self.assertAlmostEqual(rep['per_part'][pid]['error_m'], mv['distance_m'], delta=0.01)
        (pid, change), = record['recoloured'].items()
        self.assertNotEqual(change['from'], change['to'])

    def test_same_seed_gives_the_same_perturbation(self):
        case = make_case()
        a = selfplay.perturb_spec(case, make_spec(case), seed=3)
        b = selfplay.perturb_spec(case, make_spec(case), seed=3)
        c = selfplay.perturb_spec(case, make_spec(case), seed=4)
        self.assertEqual(a, b)
        self.assertNotEqual(a[1], c[1])

    def test_single_part_case_is_not_emptied(self):
        case = make_case()
        case['parts'] = case['parts'][:1]
        out, record = selfplay.perturb_spec(case, make_spec(case), seed=1)
        self.assertIsNone(record['deleted'])
        self.assertTrue(any(c['id'].startswith('counter_1') for c in out['components']))


def round_result(median, missing, iou):
    return {'case_id': 'synth_fixture',
            'placement': {'median_m': median, 'missing': missing},
            'iou': None if iou is None else {'mean': iou}}


def round_parts(errors, iou):
    """An evaluate-like round result from per-part errors in metres (None = the part is missing)."""
    ok = sorted(v for v in errors.values() if v is not None)
    median = None if not ok else (ok[len(ok) // 2] if len(ok) % 2 else (ok[len(ok) // 2 - 1] + ok[len(ok) // 2]) / 2)
    return {'case_id': 'synth_fixture',
            'placement': {'median_m': median, 'missing': sum(1 for v in errors.values() if v is None),
                          'per_part': {k: {'status': 'missing' if v is None else 'ok', 'error_m': v} for k, v in errors.items()}},
            'iou': None if iou is None else {'mean': iou}}


class CurveTests(unittest.TestCase):
    def test_curve_marks_improvement_only_when_both_metrics_improve(self):
        good = [round_result(0.40, 0, 0.30), round_result(0.30, 0, 0.45), round_result(0.20, 0, 0.60), round_result(0.10, 0, 0.80)]
        self.assertIs(selfplay.judge_curve(good)['improved'], True)
        iou_worse = [round_result(0.40, 0, 0.60), round_result(0.30, 0, 0.58), round_result(0.20, 0, 0.56), round_result(0.10, 0, 0.55)]
        self.assertIs(selfplay.judge_curve(iou_worse)['improved'], False)
        place_worse = [round_result(0.10, 0, 0.30), round_result(0.10, 0, 0.45), round_result(0.10, 0, 0.60), round_result(0.12, 0, 0.80)]
        self.assertIs(selfplay.judge_curve(place_worse)['improved'], False)

    def test_one_bad_step_of_more_than_ten_percent_breaks_the_curve(self):
        bumpy = [round_result(0.40, 0, 0.30), round_result(0.20, 0, 0.60), round_result(0.30, 0, 0.60), round_result(0.10, 0, 0.80)]
        v = selfplay.judge_curve(bumpy)
        self.assertIs(v['improved'], False)
        self.assertFalse(v['checks']['no_regression']['placement'])
        small = [round_result(0.40, 0, 0.30), round_result(0.20, 0, 0.60), round_result(0.215, 0, 0.58), round_result(0.10, 0, 0.80)]
        self.assertIs(selfplay.judge_curve(small)['improved'], True)         # 7.5 % worse in one step is tolerated

    def test_losing_a_part_is_a_regression_even_if_the_median_shrinks(self):
        lost = [round_result(0.40, 0, 0.30), round_result(0.30, 0, 0.45), round_result(0.20, 1, 0.60), round_result(0.10, 1, 0.80)]
        self.assertIs(selfplay.judge_curve(lost)['improved'], False)

    def test_a_part_lost_in_one_step_and_found_again_still_breaks_the_curve(self):
        # round 4 has no more missing parts than round 1, so only the step check can see the dip in round 2
        dip = [round_result(0.40, 0, 0.30), round_result(0.30, 1, 0.45), round_result(0.20, 0, 0.60), round_result(0.10, 0, 0.80)]
        v = selfplay.judge_curve(dip)
        self.assertTrue(v['checks']['round4_better_than_round1']['placement'])
        self.assertFalse(v['checks']['no_regression']['placement'])
        self.assertIs(v['improved'], False)

    def test_an_iou_step_down_of_more_than_ten_percent_breaks_the_curve(self):
        # round 4 is better than round 1 in both metrics; only the 0.60 -> 0.50 step (-17 %) is wrong
        dip = [round_result(0.40, 0, 0.30), round_result(0.30, 0, 0.60), round_result(0.20, 0, 0.50), round_result(0.10, 0, 0.80)]
        v = selfplay.judge_curve(dip)
        self.assertTrue(v['checks']['round4_better_than_round1']['iou'])
        self.assertFalse(v['checks']['no_regression']['iou'])
        self.assertIs(v['improved'], False)
        ok = [round_result(0.40, 0, 0.30), round_result(0.30, 0, 0.60), round_result(0.20, 0, 0.55), round_result(0.10, 0, 0.80)]
        self.assertIs(selfplay.judge_curve(ok)['improved'], True)             # -8 % is tolerated

    def test_rounds_are_known_by_number_not_by_position(self):
        r = {2: round_result(0.30, 0, 0.45), 3: round_result(0.20, 0, 0.60), 4: round_result(0.10, 0, 0.80), 5: round_result(0.09, 0, 0.85)}
        v = selfplay.judge_curve(r)                                          # round 1 is missing: not the first listed one
        self.assertIsNone(v['improved'])
        self.assertIn('round 1', v['reason'])
        r = {1: round_result(0.40, 0, 0.30), 2: round_result(0.30, 0, 0.45), 3: round_result(0.20, 0, 0.60), 5: round_result(0.10, 0, 0.80)}
        v = selfplay.judge_curve(r)                                          # four rounds, but the fourth one is not there
        self.assertIsNone(v['improved'])
        self.assertIn('round 4', v['reason'])

    def test_a_missing_middle_round_is_undecided_and_never_skipped(self):
        # rounds 1, 2 and 4: 0.40 -> 0.15 -> 0.30. Skipping the absent round 3 used to read this as an improvement.
        r = {1: round_result(0.40, 0, 0.30), 2: round_result(0.15, 0, 0.60), 4: round_result(0.30, 0, 0.62)}
        v = selfplay.judge_curve(r)
        self.assertIsNone(v['improved'])
        self.assertIn('round 3', v['reason'])
        r = {1: round_result(0.40, 0, 0.30), 3: round_result(0.20, 0, 0.60), 4: round_result(0.10, 0, 0.80)}
        v = selfplay.judge_curve(r)
        self.assertIsNone(v['improved'])
        self.assertIn('round 2', v['reason'])
        r = {n: round_result(0.40 / n, 0, 0.30 + 0.1 * n) for n in (1, 2, 3, 4, 6)}           # a gap after round 4 is not skipped either
        v = selfplay.judge_curve(r)
        self.assertIsNone(v['improved'])
        self.assertIn('round 5', v['reason'])
        r[5] = round_result(0.40 / 5, 0, 0.85)
        self.assertIs(selfplay.judge_curve(r)['improved'], True)

    def test_finding_a_missed_part_with_a_large_error_is_not_a_step_back(self):
        def rnd(errors, iou):
            return round_parts(errors, iou)
        r1 = rnd({'a': 0.20, 'b': 0.20, 'c': None, 'd': 0.20}, 0.30)             # median 0.20, one part missing
        r2 = rnd({'a': 0.20, 'b': 0.20, 'c': 0.90, 'd': 0.40}, 0.40)             # the part is found, badly placed; median of all 0.30
        r3 = rnd({'a': 0.20, 'b': 0.20, 'c': 0.30, 'd': 0.30}, 0.50)             # 0.25
        r4 = rnd({'a': 0.15, 'b': 0.15, 'c': 0.15, 'd': 0.15}, 0.80)             # 0.15
        self.assertGreater(r2['placement']['median_m'], 1.1 * r1['placement']['median_m'])      # the plain medians would call step 1 -> 2 a regression
        v = selfplay.judge_curve({1: r1, 2: r2, 3: r3, 4: r4})
        self.assertTrue(v['checks']['no_regression']['placement'])
        self.assertIs(v['improved'], True)

    def test_the_comparison_uses_only_parts_both_rounds_matched_and_missing_still_may_not_grow(self):
        r1 = round_parts({'a': 0.20, 'b': 0.20, 'c': 0.20}, 0.30)
        r2 = round_parts({'a': 0.30, 'b': 0.30, 'c': None}, 0.40)               # the common parts a and b got worse by 50 %, and c is lost
        r3 = round_parts({'a': 0.20, 'b': 0.20, 'c': 0.20}, 0.50)
        r4 = round_parts({'a': 0.10, 'b': 0.10, 'c': 0.10}, 0.80)
        v = selfplay.judge_curve({1: r1, 2: r2, 3: r3, 4: r4})
        self.assertIs(v['improved'], False)
        # a worse common median alone is also a regression, with nothing missing
        r2 = round_parts({'a': 0.30, 'b': 0.30, 'c': 0.20}, 0.40)
        self.assertIs(selfplay.judge_curve({1: r1, 2: r2, 3: r3, 4: r4})['improved'], False)


    def test_a_round_with_incomplete_masks_is_not_measured_and_is_not_a_step_back(self):
        def incomplete(median, iou):
            r = round_result(median, 0, iou)
            r['masks_incomplete'] = True
            r['iou']['masks_incomplete'] = True
            return r
        # the incomplete round's IoU (counting absent masks as 0) looks like a collapse; it must not be judged
        rounds = [round_result(0.40, 0, 0.30), incomplete(0.30, 0.05), round_result(0.20, 0, 0.60), round_result(0.10, 0, 0.80)]
        v = selfplay.judge_curve(rounds)
        self.assertIsNone(v['improved'])
        self.assertIsNone(v['checks']['no_regression']['iou'])
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            write_json(tmp / 'case.json', make_case())
            (tmp / 'rounds').mkdir()
            for n, r in enumerate(rounds, start=1):
                write_json(tmp / 'rounds' / f'round_{n}.json', r)
            curve = selfplay.make_curve(tmp, tmp / 'rounds', tmp / 'curve.json')
            self.assertIsNone(curve['improved'])
            self.assertIsNone(curve['rounds'][1]['iou_mean'])
            self.assertTrue(curve['rounds'][1]['masks_incomplete'])
        worse = [round_result(0.40, 0, 0.30), incomplete(0.50, 0.05), round_result(0.20, 0, 0.60), round_result(0.10, 0, 0.80)]
        self.assertIs(selfplay.judge_curve(worse)['improved'], False)           # a placement step back is still a step back

    def test_unmeasured_iou_or_short_run_is_undecided_not_improved(self):
        no_iou = [round_result(0.40, 0, None), round_result(0.30, 0, None), round_result(0.20, 0, None), round_result(0.10, 0, None)]
        self.assertIsNone(selfplay.judge_curve(no_iou)['improved'])
        self.assertIsNone(selfplay.judge_curve(no_iou[:3])['improved'])

    def test_curve_files_are_read_in_round_order_and_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            write_json(tmp / 'case.json', make_case())
            rounds = tmp / 'rounds'
            rounds.mkdir()
            data = {n: round_result(0.40 / n, 0, 0.30 + 0.05 * n) for n in range(1, 11)}
            for i in (10, 2, 1, 3, 4, 9, 8, 7, 6, 5):                   # round_10 must sort after round_9
                write_json(rounds / f'round_{i}.json', data[i])
            out = tmp / 'curve.json'
            curve = selfplay.make_curve(tmp, rounds, out)
            self.assertEqual([r['round'] for r in curve['rounds']], list(range(1, 11)))
            self.assertIs(curve['improved'], True)
            self.assertTrue(out.is_file())
            self.assertTrue(out.with_suffix('.png').is_file())
            self.assertEqual(json.loads(out.read_text(encoding='utf-8'))['case_id'], 'synth_fixture')


    def test_curve_reads_the_loop_layout_round_dirs_and_writes_schema_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            write_json(tmp / 'case.json', make_case())
            loop = tmp / 'loop'
            for n, r in {1: round_result(0.40, 0, 0.30), 2: round_result(0.30, 0, 0.45), 3: round_result(0.20, 0, 0.60),
                         4: round_result(0.10, 0, 0.80)}.items():
                (loop / f'round_{n}').mkdir(parents=True)
                write_json(loop / f'round_{n}' / 'evaluate.json', r)
                write_json(loop / f'round_{n}' / 'round.json', {'winner': 'x'})          # the loop's other files are ignored
            curve = selfplay.make_curve(tmp, loop, tmp / 'curve.json')
            self.assertIs(curve['improved'], True)
            self.assertEqual(curve['schema_version'], 'selfplay-curve.v1')
            self.assertNotIn('schema', curve)
            self.assertEqual(selfplay.main(['curve', '--case', str(tmp), '--rounds', str(loop), '--out', str(tmp / 'c2.json')]), 0)

class WrapperTests(unittest.TestCase):
    def test_prepare_writes_the_smoke_suite_cases(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = selfplay.prepare(tmp, cases=3, seed=1)
            self.assertEqual(len(manifest['cases']), 3)
            for entry in manifest['cases']:
                self.assertTrue((Path(tmp) / entry['case_id'] / 'case.json').is_file())
                self.assertTrue((Path(tmp) / entry['case_id'] / 'scene.json').is_file())

    def test_render_hands_the_case_to_synth_render_without_starting_blender(self):
        calls = []

        class Fake:
            @staticmethod
            def render_case(case_dir, out_dir, skill_dir, blender=None, samples=16, clutter=False, post=None, seed=0, overlay=False, timeout=3600):
                calls.append((str(case_dir), str(out_dir), blender, samples))
                return {'ok': True}

        original = selfplay._synth_render
        selfplay._synth_render = lambda: Fake
        try:
            self.assertEqual(selfplay.render('c', 'o', blender='b', samples=4), {'ok': True})
        finally:
            selfplay._synth_render = original
        self.assertEqual(calls, [('c', 'o', 'b', 4)])


if __name__ == '__main__':
    unittest.main()
