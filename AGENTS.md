# AGENTS.md

Rules for coding agents working in this repository (humans: see [CONTRIBUTING.md](CONTRIBUTING.md)). If a git-ignored
`AGENTS.local.md` exists, read it too: it holds the owner's local machine and preferences.

## What this is

`booth-image-to-model` turns one exhibition-booth photo or render into an editable parametric 3D model (a JSON spec), a Blender
scene and clean client renders. Its dimensions are estimates and it makes no drawings yet, so never call it CAD. Two products:

- **The skill** `booth-image-to-model/`: what an agent follows (`SKILL.md`), plus the engine scripts, schemas, references and tests.
  It must stay self-contained and must not depend on `web/`.
- **The web product** `web/`: `app/` (the static site: the studio page and the standalone editor), `server/` (the local studio
  server), `tests/`, `tools/`. The server reaches the engine only through `web/server/engine.py`.

Client pictures and every result stay in git-ignored `input/` and `output/`. The repository is public: anything tracked is public.

## Git

- Commit only when the person you work for asked for it or approved a plan that includes it. Never push, force-push, rewrite
  published history, delete branches, publish, enable hosting, rename the repository or change its visibility without their explicit
  go-ahead for that action.
- One batch of related changes per commit, with a message that says what changed and why. Run the checks first.

## Environment

- Blender is found through `--blender`, the `BLENDER_EXE` environment variable, `PATH`, then the usual install locations. Run only
  one Blender process at a time (memory, timings). Headless Workbench can crash on integrated GPUs; `boothctl.py preview` falls back
  to Cycles by itself.
- High-definition frames are capped at 5 MP (`--hd-max-mp`): a 6.9 MP frame made the denoiser run out of memory on a 16 GB laptop.
- No installs or downloads (Python or npm packages, models, asset packs, Git LFS) and no third-party image-model route without
  approval.

## Data hygiene

- Tracked files are generic: no client project names, picture file names or paths, brands, or dimensions that came from a client
  picture. Write "a local client render" instead.
- Never invent dimensions, brands, text or certifications. Every assumed value is recorded as assumed (`--assume`, the `assumed`
  list, `scale.notes`, `uncertainties`). Estimates are not BOM-grade.
- Rules or text from a private studio specification stay out of tracked files; use neutral descriptions.
- Numbers in `knowledge/research/` are sourced but not designer-reviewed. They do not become defaults
  (`references/design-priors.md`, `schemas/design-priors.v1.json`) until a person approves them.

## Changing the skill

1. Keep text files LF and check with `python tools/check_lf.py` (Windows tools may write CRLF; release evidence hashes the bytes).
   `python tools/check_docs.py` checks links, alt text, private traces and that versions agree.
2. Do not edit `scripts/`, `schemas/` or `tests/` while a regression runs.
3. Run the skill tests: `python booth-image-to-model/tests/run_tests.py --output output/_validation/<new-name>` (add
   `--blender "$BLENDER_EXE" --require-native` when Blender is available; a release needs zero failures and zero skips).
4. Never edit `release_status.json`, `FILE_MANIFEST.json` or `validation/` by hand; the release routine in
   [docs/maintainers.md](docs/maintainers.md) regenerates them.
5. Update `CHANGELOG.md` and the version in `SKILL.md` and `README_CN.md` for every code batch.

## Working on a booth

Follow the fast route in `booth-image-to-model/SKILL.md` (corner fit, scene description and compile or a spec script from
`templates/`, `boothctl.py preview`, one final build, an independent reopen with `blender_verify.py`). A reconstruction stays
`review_required` unless the strict QA in `references/reconstruction-qa.md` was done: no IoU or restoration percentage without
reviewed masks.

## Web (`web/`)

- `web/app/` is the deployable static site and nothing else. `web/server/`, `web/tests/` and `web/tools/` stay outside it so they are
  never served. The standalone editor (`index.html`) makes no network calls except loading its synthetic demo from the same origin.
- The studio (`python web/server/run.py`, 127.0.0.1 only) runs the flow in one window (`studio.html`: 1 Choose picture, 2 Make model, 3 Adjust, 4 Render),
  hosts the editor (`createEditor`) in its stage and autosaves. The main path uses plain English words only (`studio_copy.js` `main`, scanned
  by a test for engineering terms); terms belong in the Advanced view drawer. The whole web UI is English only (no language switch):
  new labels and server messages shown in it are written in English. Jobs are written to git-ignored `output/studio/`.
- Large-model reading (`web/server/reader_openai.py`, standard-library HTTP, any OpenAI-compatible service): the key comes from
  `OPENAI_API_KEY` or the page and is kept in memory only (never in a file, a job or a log); a picture is sent only after the per-job
  consent box is ticked; tests use the stand-in `web/tests/mock_openai.py`, never a real key. Do not add a vendor SDK.
- `js/geometry.js` and `js/camera.js` are ports of `scripts/geometry.py` and `scripts/camera_math.py`. After changing either Python
  file, run `python web/tests/make_geometry_reference.py` and fix any parity failure. `spec.js` `PARAM_KEYS` must match
  `validate_spec.PARAM_KEYS` (a test checks).
- Checks: `cd web && npm test`, `npm run test:server` (set `BLENDER_EXE` to include preview and build), `npm run smoke`,
  `npm run smoke:studio`.
- three.js is vendored at a pinned version (`web/app/vendor/three/VERSION.txt`); updating it is a download and needs approval.
- Tests live next to the code they test; deployable or bundled output never contains them.

## Roadmap

Done: the standalone editor, the one-window studio, the evaluation harness with its baseline (I0, in `tools/eval/`) and the honest
camera fit (I1). I1 was measured on synthetic scenes only; its known limitation is that valid input from cropped pictures is held for
review too often ([tools/eval/RESULTS-I1c.md](tools/eval/RESULTS-I1c.md)). There is one list of priorities, in this order unless the
owner reorders it:

1. The accuracy iterations I2 to I7, one measured step at a time, each confirmed with the owner before the next starts. I2 is in
   progress: the reader decides the booth type and the open sides first, then reads the details, and the compiler checks them. The
   plan and the progress table are in [docs/design-review-2026-10.md](docs/design-review-2026-10.md).
2. After them: corner-point correction in the studio, drawings (plan and elevations, SVG and DXF) from the same spec, and a
   furniture library as prefab JSON built from existing primitives.

The showcase and demo pictures are synthetic (made from the invented demo booth) and used as they are; they can be swapped later
([docs/sample-images.md](docs/sample-images.md)).
