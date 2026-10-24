"""P2 tests for panel.py: blinding, request files, tally and the closed judge.v1 answer. Synthetic data in temporary folders only."""
from __future__ import annotations
import contextlib
import io
import itertools
import json
import re
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0]))        # tools/poc, so that `score` is the package
sys.path.insert(0, str(HERE / 'fixtures'))

from PIL import Image, PngImagePlugin  # noqa: E402
import synthetic as fx  # noqa: E402
import score  # noqa: E402
from score import panel  # noqa: E402
from score import score as score_cli  # noqa: E402

CANDS = ('cand_alpha', 'cand_beta', 'cand_gamma', 'cand_delta')
HEX8 = re.compile(r'^[0-9a-f]{8}$')


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = panel.main([str(a) for a in argv])
        except SystemExit as e:               # argparse errors
            code = e.code
    return code, out.getvalue(), err.getvalue()


def tree_files(folder):
    return sorted(p for p in Path(folder).rglob('*') if p.is_file())


class PanelCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.truth = fx.write_truth(self.tmp / 'truth')
        self.inv_ids = [i['id'] for i in fx.inventory()['items']]
        self.cands = self.tmp / 'cands'
        for n, cid in enumerate(CANDS):
            d = self.cands / cid
            d.mkdir(parents=True)
            Image.new('RGB', (48, 16), (30 + 50 * n, 90, 200 - 40 * n)).save(d / 'panel_view.png')
            Image.new('RGB', (48, 16), (255, 255, 255)).save(d / 'sheet.png')
            (d / 'score.json').write_text(json.dumps({'schema_version': 'score.v1', 'picture_id': 'pic_T', 'total': 61.5 + n, 'candidate_id': cid}), encoding='utf-8')

    def prepare(self, name='panel', seed=7, pairwise=False, cands=None):
        out = self.tmp / name
        args = ['prepare', '--candidates', cands or self.cands, '--truth', self.truth, '--out', out, '--seed', seed]
        if pairwise:
            args.append('--pairwise')
        code, _, err = run_cli(*args)
        self.assertEqual(code, 0, err)
        return out

    def mapping(self, panel_dir):
        return json.loads((Path(panel_dir) / 'mapping.secret.json').read_text(encoding='utf-8'))

    def respond(self, panel_dir, cid, judge, pass_no, **kw):
        """Write one judge.v1 scoring response for candidate cid (keyword arguments as in fixtures.judge)."""
        blind = self.mapping(panel_dir)['candidates'][cid]['blind']
        doc = fx.judge(judge, pass_no, pair_id=blind, **kw)
        path = Path(panel_dir) / 'responses' / judge / f'{judge}_{blind}_p{pass_no}.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc), encoding='utf-8')
        return doc, path

    def full_panel(self, panel_dir, cid, **per_judge):
        """All three judges, both passes, identical answers unless per_judge[judge] overrides keyword arguments."""
        for j in ('j1', 'j2', 'j3'):
            for p in (1, 2):
                self.respond(panel_dir, cid, j, p, **per_judge.get(j, {}))

    def tally(self, panel_dir, name='tally.json'):
        out = self.tmp / name
        code, _, err = run_cli('tally', '--panel', panel_dir, '--out', out)
        self.assertEqual(code, 0, err)
        return json.loads(out.read_text(encoding='utf-8'))


class BlindingTests(PanelCase):
    def test_pairs_are_blinded_and_shuffled_with_seed(self):
        a = self.prepare('a', seed=7, pairwise=True)
        b = self.prepare('b', seed=7, pairwise=True)
        c = self.prepare('c', seed=8, pairwise=True)
        ma, mb, mc = self.mapping(a), self.mapping(b), self.mapping(c)
        self.assertEqual(ma, mb)                                  # same seed: same mapping
        self.assertNotEqual(ma['candidates'], mc['candidates'])   # other seed: other blinding
        self.assertEqual([p.relative_to(a) for p in tree_files(a)], [p.relative_to(b) for p in tree_files(b)])
        blinds = [v['blind'] for v in ma['candidates'].values()]
        self.assertEqual(len(set(blinds)), len(CANDS))
        for blind in blinds:
            self.assertRegex(blind, HEX8)
        self.assertEqual(len(ma['pairs']), 6)                      # 4 candidates, every pair once
        self.assertEqual(sorted(tuple(sorted(p['candidates'])) for p in ma['pairs'].values()),
                         sorted(itertools.combinations(sorted(CANDS), 2)))
        for pair_id in ma['pairs']:
            self.assertRegex(pair_id, HEX8)
            self.assertNotIn(pair_id, blinds)
        # random A/B: both orders occur among the six pairs
        firsts = [p['candidates'][0] < p['candidates'][1] for p in ma['pairs'].values()]
        self.assertTrue(any(firsts) and not all(firsts))
        # two passes per pair, the second with A and B swapped
        for pair_id, pair in ma['pairs'].items():
            reqs = {}
            for p in (1, 2):
                folder = a / 'requests' / 'j1' / f'{pair_id}_p{p}'
                reqs[p] = json.loads((folder / 'request.json').read_text(encoding='utf-8'))
                self.assertEqual(reqs[p]['pass'], p)
                self.assertEqual(reqs[p]['pair_id'], pair_id)
                self.assertTrue((folder / reqs[p]['A']).is_file())
                self.assertTrue((folder / reqs[p]['B']).is_file())
            self.assertEqual((reqs[1]['A'], reqs[1]['B']), (reqs[2]['B'], reqs[2]['A']))
            first, second = pair['candidates']
            self.assertEqual(reqs[1]['A'], ma['candidates'][first]['blind'] + '.png')
            self.assertEqual(reqs[1]['B'], ma['candidates'][second]['blind'] + '.png')
        # the picture is a re-encoded copy (same pixels, no metadata) under the random name
        cid = CANDS[0]
        copies = [p for p in tree_files(a / 'requests') if p.name == ma['candidates'][cid]['blind'] + '.png']
        self.assertTrue(copies)
        for p in copies:
            self.assertEqual(Image.open(p).tobytes(), Image.open(self.cands / cid / 'panel_view.png').tobytes())

    def test_scoring_requests_three_judges_two_passes_swapped_order(self):
        panel_dir = self.prepare('p')
        m = self.mapping(panel_dir)
        self.assertEqual(m['inventory_ids'], self.inv_ids)
        self.assertEqual(sorted(p.name for p in (panel_dir / 'requests').iterdir()), ['j1', 'j2', 'j3'])
        for cid, entry in m['candidates'].items():
            self.assertRegex(entry['blind'], HEX8)
            for j in ('j1', 'j2', 'j3'):
                r1 = json.loads((panel_dir / 'requests' / j / f"{entry['blind']}_p1" / 'request.json').read_text(encoding='utf-8'))
                r2 = json.loads((panel_dir / 'requests' / j / f"{entry['blind']}_p2" / 'request.json').read_text(encoding='utf-8'))
                self.assertEqual(r1['pair_id'], entry['blind'])
                self.assertEqual(r2['pair_id'], entry['blind'])
                self.assertEqual([i['id'] for i in r1['items']], self.inv_ids)
                self.assertEqual([i['id'] for i in r2['items']], self.inv_ids[::-1])
                self.assertEqual(r1['judge_id'], j)
                self.assertEqual((r1['pass'], r2['pass']), (1, 2))

    def test_judge_inputs_contain_no_mapping_or_scores(self):
        panel_dir = self.prepare('p')
        files = tree_files(panel_dir / 'requests')
        self.assertTrue(files)
        for p in files:
            rel = str(p.relative_to(panel_dir / 'requests')).lower()
            self.assertNotIn('mapping', rel)
            self.assertNotIn('score', rel)
            self.assertNotIn('sheet', rel)
            for cid in CANDS:
                self.assertNotIn(cid, rel)
            if p.suffix == '.json':
                text = p.read_text(encoding='utf-8').lower()
                for cid in CANDS:
                    self.assertNotIn(cid, text)
                for word in ('score', 'total', 'holdout', 'swatch', 'uv_px', '61.5', 'mapping'):
                    self.assertNotIn(word, text)
            else:
                self.assertEqual(p.suffix, '.png')
                self.assertRegex(p.stem, HEX8)
        # the only place that knows the real names
        self.assertTrue((panel_dir / 'mapping.secret.json').is_file())
        for p in files:
            self.assertNotEqual(p.name, 'mapping.secret.json')
        # sheet.png (it carries scores) is never copied
        sheet = (self.cands / CANDS[0] / 'sheet.png').read_bytes()
        self.assertFalse(any(p.read_bytes() == sheet for p in files))
        # the inventory shows names and ids only
        r = json.loads(next(p for p in files if p.name == 'request.json').read_text(encoding='utf-8'))
        for item in r['items']:
            self.assertEqual(set(item), {'id', 'name'})


class TallyTests(PanelCase):
    def test_tally_majority_and_flags_inconsistent_judge(self):
        panel_dir = self.prepare('p')
        cid = 'cand_beta'
        # j3 says part_5 is missing in pass 1 and present in pass 2 (inconsistent) and calls part_6 wrong in both passes.
        # j1 calls part_7 wrong while j2 and j3 call it ok: a "take the first judge" shortcut would get that wrong.
        self.full_panel(panel_dir, cid)
        for p in (1, 2):
            self.respond(panel_dir, cid, 'j1', p, l2={'part_7': 'wrong'})
            present = [i for i in self.inv_ids if not (i == 'part_5' and p == 1)]
            self.respond(panel_dir, cid, 'j3', p, inv_present=present, l2={'part_6': 'wrong'})
        t = self.tally(panel_dir)
        self.assertEqual(set(t), {'schema_version', 'candidates', 'judges', 'pairwise_agreement'})
        self.assertEqual(t['schema_version'], 'tally.v1')
        entry = t['candidates'][cid]
        self.assertEqual(set(entry), {'levels', 'items', 'extra_parts'})
        self.assertEqual(set(entry['levels']), {'L1', 'L2_panel', 'L3', 'L4_panel'})
        self.assertEqual(entry['items']['L2:part_7'], 'ok')           # j2, j3 say ok, j1 says wrong
        self.assertEqual(entry['items']['L2:part_6'], 'ok')           # j1, j2 ok, j3 wrong: still ok
        self.assertEqual(entry['items']['L1:part_5'], 'present')      # j3 unstable, j1 and j2 present
        self.assertEqual(entry['items']['L2:screen_a'], 'off')
        self.assertEqual(entry['items']['L1:reception'], 'present')
        self.assertEqual(sorted(entry['items']), sorted([f'L1:{i}' for i in self.inv_ids] + [f'L2:{i}' for i in self.inv_ids]))
        self.assertEqual(entry['extra_parts'], 0)
        # the other candidates have no responses: everything undecided, no scores
        other = t['candidates']['cand_alpha']
        self.assertTrue(all(v == 'undecided' for v in other['items'].values()))
        self.assertEqual(other['levels'], {'L1': None, 'L2_panel': None, 'L3': None, 'L4_panel': None})
        # self-consistency: j3 disagreed with itself on L1:part_5 and L2:part_5, two of 20 answers
        sc = {j: v['self_consistency'] for j, v in t['judges'].items()}
        self.assertEqual(sc['j1'], 1.0)
        self.assertEqual(sc['j2'], 1.0)
        self.assertAlmostEqual(sc['j3'], 18 / 20)
        # pairwise agreement: share of answers, among those both judges gave consistently, that are equal
        self.assertEqual(set(t['pairwise_agreement']), {'j1-j2', 'j1-j3', 'j2-j3'})
        self.assertAlmostEqual(t['pairwise_agreement']['j1-j2'], 19 / 20)
        self.assertAlmostEqual(t['pairwise_agreement']['j1-j3'], 16 / 18)
        self.assertAlmostEqual(t['pairwise_agreement']['j2-j3'], 17 / 18)
        # levels agree with the one aggregation function of score.py
        responses = [json.loads(p.read_text(encoding='utf-8')) for p in sorted((panel_dir / 'responses').rglob('*.json'))]
        agg = score.aggregate_panel(responses, self.inv_ids)
        self.assertEqual(entry['levels']['L1'], agg['levels']['L1'])
        self.assertEqual(entry['levels']['L2_panel'], agg['levels']['L2'])
        self.assertEqual(entry['levels']['L3'], agg['levels']['L3'])
        self.assertEqual(entry['levels']['L4_panel'], agg['levels']['L4'])

    def test_tally_is_readable_by_score_py(self):
        panel_dir = self.prepare('p')
        self.full_panel(panel_dir, 'cand_gamma', j1={'extra_parts': 2}, j2={'extra_parts': 2}, j3={'extra_parts': 2})
        out = self.tmp / 'tally.json'
        self.assertEqual(run_cli('tally', '--panel', panel_dir, '--out', out)[0], 0)
        got = score_cli.read_tally(out, 'cand_gamma', self.inv_ids)
        self.assertEqual(got['extra_parts'], 2)
        self.assertEqual(got['l1']['reception'], 'present')
        self.assertEqual(got['l2']['screen_a'], 'off')
        self.assertEqual(got['judges'], ['j1', 'j2', 'j3'])
        self.assertIsNotNone(got['levels']['L1'])

    def test_judge_missing_a_pass_is_undecided_as_a_whole(self):
        panel_dir = self.prepare('p')
        cid = 'cand_alpha'
        self.respond(panel_dir, cid, 'j1', 1)                        # j1 never answers pass 2
        for j in ('j2', 'j3'):
            for p in (1, 2):
                self.respond(panel_dir, cid, j, p, l2={'part_6': 'wrong'})
        t = self.tally(panel_dir)                                     # must not crash
        self.assertEqual(t['candidates'][cid]['items']['L2:part_6'], 'wrong')
        self.assertEqual(t['judges']['j1']['self_consistency'], 0.0)
        self.assertEqual(t['judges']['j2']['self_consistency'], 1.0)
        # j1 is out of every majority and of the agreement figures
        self.assertEqual(t['pairwise_agreement']['j2-j3'], 1.0)
        self.assertEqual(t['pairwise_agreement']['j1-j2'], 0.0)

    def test_one_usable_judge_gives_no_majority(self):
        panel_dir = self.prepare('p')
        for p in (1, 2):
            self.respond(panel_dir, 'cand_alpha', 'j1', p)
        t = self.tally(panel_dir)
        self.assertTrue(all(v == 'undecided' for v in t['candidates']['cand_alpha']['items'].values()))

    def test_unknown_pair_id_and_junk_files_are_ignored(self):
        panel_dir = self.prepare('p')
        self.full_panel(panel_dir, 'cand_delta')
        stray = fx.judge('j1', 1, pair_id='deadbeef')
        (panel_dir / 'responses' / 'j1' / 'stray.json').write_text(json.dumps(stray), encoding='utf-8')
        (panel_dir / 'responses' / 'j1' / 'broken.json').write_text('{not json', encoding='utf-8')
        t = self.tally(panel_dir)
        self.assertEqual(t['candidates']['cand_delta']['items']['L1:reception'], 'present')
        self.assertEqual(sorted(t['candidates']), sorted(CANDS))

    def test_tally_without_any_response_is_an_error(self):
        panel_dir = self.prepare('p')
        code, _, err = run_cli('tally', '--panel', panel_dir, '--out', self.tmp / 't.json')
        self.assertEqual(code, 2)
        self.assertIn('ERROR', err)
        self.assertFalse((self.tmp / 't.json').exists())


class VerdictSchemaTests(PanelCase):
    def good(self):
        return fx.judge('j1', 1, pair_id='abcdef01')

    def test_verdict_schema_rejects_free_text_and_extra_fields(self):
        self.assertEqual(panel.validate_response(self.good(), self.inv_ids), [])
        for field, value in (('comment', 'looks fine to me'), ('explanation', 'x'), ('notes', ['a'])):
            d = self.good()
            d[field] = value
            self.assertTrue(panel.validate_response(d, self.inv_ids), field)
        d = self.good()
        d['items']['L1:reception'] = 'looks present, maybe'
        self.assertTrue(panel.validate_response(d, self.inv_ids))
        d = self.good()
        d['items']['L3:reception'] = 'ok'
        self.assertTrue(panel.validate_response(d, self.inv_ids))
        d = self.good()
        d['items']['L1:not_a_part'] = 'present'
        self.assertTrue(panel.validate_response(d, self.inv_ids))
        d = self.good()
        d['confidence'] = 'very high'
        self.assertTrue(panel.validate_response(d, self.inv_ids))

    def test_l2_for_a_missing_part_is_invalid(self):
        d = self.good()
        d['items']['L1:reception'] = 'missing'                       # but L2:reception is still given
        problems = panel.validate_response(d, self.inv_ids)
        self.assertTrue(any('L2:reception' in p for p in problems), problems)

    def test_extra_field_response_is_not_counted(self):
        panel_dir = self.prepare('p')
        cid = 'cand_alpha'
        self.full_panel(panel_dir, cid)
        # j3 pass 2 gets a free-text field: that response is invalid, so j3 lacks a pass and is undecided as a whole
        doc, path = self.respond(panel_dir, cid, 'j3', 2, l2={'part_6': 'wrong'})
        doc['comment'] = 'the counter is slightly too long'
        path.write_text(json.dumps(doc), encoding='utf-8')
        t = self.tally(panel_dir)
        self.assertEqual(t['judges']['j3']['self_consistency'], 0.0)
        self.assertEqual(t['candidates'][cid]['items']['L2:part_6'], 'ok')

    def test_l2_for_missing_part_response_is_not_counted(self):
        panel_dir = self.prepare('p')
        cid = 'cand_alpha'
        self.full_panel(panel_dir, cid)
        doc, path = self.respond(panel_dir, cid, 'j2', 1)
        doc['items']['L1:part_4'] = 'missing'                         # L2:part_4 still there
        path.write_text(json.dumps(doc), encoding='utf-8')
        t = self.tally(panel_dir)
        self.assertEqual(t['judges']['j2']['self_consistency'], 0.0)


def png_chunks(path):
    data = Path(path).read_bytes()
    assert data[:8] == b'\x89PNG\r\n\x1a\n'
    pos, out = 8, []
    while pos < len(data):
        size, kind = struct.unpack('>I4s', data[pos:pos + 8])
        out.append(kind.decode('ascii'))
        pos += 12 + size
    return out


class HardeningTests(PanelCase):
    NAMES = ('B1', 'B2', 'B3', 'B4', 'B5', 'B6')

    def make_cands(self, name='bcands', picture_id='pic_T'):
        root = self.tmp / name
        for n, cid in enumerate(self.NAMES):
            d = root / cid
            d.mkdir(parents=True)
            Image.new('RGB', (32, 12), (20 * n, 80, 160)).save(d / 'panel_view.png')
            (d / 'score.json').write_text(json.dumps({'schema_version': 'score.v1', 'picture_id': picture_id}), encoding='utf-8')
        return root

    def write_order(self, name, seed, pairwise=False):
        """The candidates (rating) or pairs (pairwise) in the order their first request folder was created."""
        root = self.make_cands(name + '_in')
        real = panel._write_request
        seen = []

        def spy(folder, copies, body):
            seen.append(body['pair_id'])
            return real(folder, copies, body)

        with mock.patch.object(panel, '_write_request', spy):
            out = self.prepare(name, seed=seed, pairwise=pairwise, cands=root)
        m = self.mapping(out)
        order = []
        for pair_id in seen:
            if pair_id not in order:
                order.append(pair_id)
        if pairwise:
            return [tuple(sorted(m['pairs'][i]['candidates'])) for i in order]
        back = {v['blind']: c for c, v in m['candidates'].items()}
        return [back[i] for i in order]

    def test_request_folders_are_written_in_shuffled_order(self):
        orders = [self.write_order(f'r{s}', s) for s in (1, 2, 3, 4, 5)]
        for o in orders:
            self.assertEqual(sorted(o), list(self.NAMES))
        self.assertTrue(any(o != list(self.NAMES) for o in orders))
        self.assertGreater(len({tuple(o) for o in orders}), 1)       # depends on the seed
        self.assertEqual(self.write_order('again', 3), orders[2])    # and only on the seed
        pairs = [self.write_order(f'q{s}', s, pairwise=True) for s in (1, 2, 3)]
        for o in pairs:
            self.assertEqual(len(o), 15)
        self.assertTrue(all(o != sorted(o) for o in pairs))

    def test_request_tree_has_one_modification_time(self):
        for pairwise in (False, True):
            out = self.prepare(f'm{int(pairwise)}', cands=self.make_cands(f'mc{int(pairwise)}'), pairwise=pairwise)
            entries = [out / 'requests', *(out / 'requests').rglob('*')]
            self.assertGreater(len(entries), 20)
            self.assertEqual(len({p.stat().st_mtime_ns for p in entries}), 1)

    def test_pictures_are_copied_without_metadata(self):
        root = self.make_cands()
        info = PngImagePlugin.PngInfo()
        info.add_text('candidate', 'B3 total=88.8')
        info.add_text('zip', 'B3 total=88.8', zip=True)
        info.add_itxt('note', 'B3 total=88.8', lang='en', tkey='note')
        exif = Image.Exif()
        exif[270] = 'B3 total=88.8'
        for cid in self.NAMES:
            Image.open(root / cid / 'panel_view.png').convert('RGB').save(root / cid / 'panel_view.png', pnginfo=info, exif=exif)
        before = png_chunks(root / 'B3' / 'panel_view.png')
        for kind in ('tEXt', 'zTXt', 'iTXt', 'eXIf'):
            self.assertIn(kind, before)
        out = self.prepare('p', cands=root)
        copies = [p for p in tree_files(out / 'requests') if p.suffix == '.png']
        self.assertEqual(len(copies), 6 * 3 * 2)
        for p in copies:
            self.assertTrue(set(png_chunks(p)) <= {'IHDR', 'PLTE', 'tRNS', 'IDAT', 'IEND'}, png_chunks(p))
            self.assertNotIn(b'88.8', p.read_bytes())
            self.assertNotIn(b'B3', p.read_bytes())
        back = {v['blind']: c for c, v in self.mapping(out)['candidates'].items()}
        for p in copies:
            src = root / back[p.stem] / 'panel_view.png'
            self.assertEqual(Image.open(p).tobytes(), Image.open(src).tobytes())

    def test_candidate_without_score_json_stops_prepare(self):
        (self.cands / 'cand_gamma' / 'score.json').unlink()
        code, _, err = run_cli('prepare', '--candidates', self.cands, '--truth', self.truth, '--out', self.tmp / 'x', '--seed', 1)
        self.assertEqual(code, 2)
        self.assertIn('cand_gamma', err)
        self.assertFalse((self.tmp / 'x').exists())

    def test_other_picture_stops_prepare_and_writes_nothing(self):
        doc = json.loads((self.cands / 'cand_beta' / 'score.json').read_text(encoding='utf-8'))
        doc['picture_id'] = 'pic_other'
        (self.cands / 'cand_beta' / 'score.json').write_text(json.dumps(doc), encoding='utf-8')
        for extra in ([], ['--pairwise']):
            code, _, err = run_cli('prepare', '--candidates', self.cands, '--truth', self.truth, '--out', self.tmp / 'x', '--seed', 1, *extra)
            self.assertEqual(code, 2)
            self.assertIn('cand_beta', err)
            self.assertIn('pic_other', err)
            self.assertFalse((self.tmp / 'x').exists())

    def test_score_json_without_picture_id_or_unreadable_stops_prepare(self):
        (self.cands / 'cand_alpha' / 'score.json').write_text(json.dumps({'schema_version': 'score.v1'}), encoding='utf-8')
        code, _, err = run_cli('prepare', '--candidates', self.cands, '--truth', self.truth, '--out', self.tmp / 'x', '--seed', 1)
        self.assertEqual(code, 2)
        self.assertIn('cand_alpha', err)
        (self.cands / 'cand_alpha' / 'score.json').write_text('{oops', encoding='utf-8')
        code, _, err = run_cli('prepare', '--candidates', self.cands, '--truth', self.truth, '--out', self.tmp / 'x', '--seed', 1)
        self.assertEqual(code, 2)
        self.assertFalse((self.tmp / 'x').exists())


class CliTests(PanelCase):
    def test_calibration_form(self):
        out = self.tmp / 'form.md'
        code, _, err = run_cli('calibration-form', '--candidate', self.cands / 'cand_alpha', '--truth', self.truth, '--out', out)
        self.assertEqual(code, 0, err)
        text = out.read_text(encoding='utf-8')
        for i in fx.inventory()['items']:
            self.assertIn(i['id'], text)
            self.assertIn(i['name'], text)
        for word in ('present', 'missing', 'ok', 'off', 'wrong'):
            self.assertIn(word, text)
        self.assertNotIn('61.5', text)                                # no score from score.json
        self.assertNotIn('\r', text)

    def test_prepare_refuses_a_candidate_without_panel_view(self):
        (self.cands / 'cand_beta' / 'panel_view.png').unlink()
        code, _, err = run_cli('prepare', '--candidates', self.cands, '--truth', self.truth, '--out', self.tmp / 'x', '--seed', 1)
        self.assertEqual(code, 2)
        self.assertIn('panel_view.png', err)

    def test_prepare_does_not_overwrite_a_panel(self):
        self.prepare('p')
        code, _, err = run_cli('prepare', '--candidates', self.cands, '--truth', self.truth, '--out', self.tmp / 'p', '--seed', 1)
        self.assertEqual(code, 2)

    def test_prompt_file_exists_and_lists_the_closed_answers(self):
        text = (HERE.parents[0] / 'prompts' / 'judge.md').read_text(encoding='utf-8')
        for word in ('judge.v1', 'present', 'missing', 'ok', 'off', 'wrong', 'extra_parts', 'pair_id', 'respond_to'):
            self.assertIn(word, text)


if __name__ == '__main__':
    unittest.main()
