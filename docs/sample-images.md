# Sample pictures

The showcase screenshots and the demo picture in this repository are **synthetic**: screenshots and a Blender render of the invented
demo booth (`web/tools/make_demo.py`). No client picture, brand or measured dimension is involved, and they are covered by the
repository's Apache License 2.0 (see `THIRD_PARTY_NOTICES.md`). They are used as they are. This note records where each one is used,
so that they can be swapped for other pictures later.

## Where the pictures are used

| File | Used by | What it shows |
|---|---|---|
| `docs/images/studio-demo.png` | `README.md`, `README.zh-CN.md` (top), `web/README.md`, the GitHub social preview (`docs/github-settings.md`) | the studio with the demo booth: picture on the left, model on the right |
| `docs/images/editor-demo.png` | `web/README.md` (standalone editor) | the standalone editor with the demo booth |
| `web/app/demo/demo_source.png` | **Try the demo booth** in the studio | a Blender render of the demo booth from the demo camera |
| `web/app/demo/demo-booth.spec.json` | the demo in the studio and the standalone editor | the synthetic demo model; its `source` (size, sha256) belongs to `demo_source.png` |

If `demo_source.png` is missing, the studio shows the demo model on a plain stand-in picture and the tests make their own stand-in, so
the picture can be removed or replaced without breaking anything. Not affected: `booth-image-to-model/examples/regression/` holds
generated test fixtures (`tests/make_regression_example.py`), not samples.

## If you swap them

- **Licence:** use your own picture with written permission from the stand owner and the photographer, or one under a public licence
  that allows redistribution and modification (CC0, CC BY 4.0, or similar), and record the source, author, licence and date in
  `THIRD_PARTY_NOTICES.md`. Do not use client projects, identifiable people without consent, or pictures copied from exhibitor or
  venue websites.
- **File:** PNG or JPEG, at most 5 MP, no EXIF rotation (the studio refuses both), metadata stripped, descriptive alt text where it is
  shown (`tools/check_docs.py` checks alt text).
- **Showcase:** overwrite the two files in `docs/images/` (or add new ones and update the links) with new screenshots, and update the
  alt text and captions in the READMEs.
- **Demo:** replace `web/app/demo/demo_source.png`, build its model with the studio or the skill (corner fit, scene description,
  compile), check it with `validate_spec.py --check-source`, and save it as `web/app/demo/demo-booth.spec.json`. The studio smoke test
  expects the demo's part count, so update that number too. Then run `python tools/check_docs.py`, `npm test`, `npm run smoke` and
  `npm run smoke:studio`.
