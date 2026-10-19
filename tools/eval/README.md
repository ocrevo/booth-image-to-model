# Evaluation harness (`tools/eval/`)

This folder measures the skill. It answers one question for every change to the engine: is the model more accurate, and is it
as fast or faster, than before? It lives at the repository level and is not shipped with the skill. The harness itself imports the
standard library, numpy and Pillow only (scipy is the skill's dependency and jsonschema is used by two tests; neither is imported by
a harness module). It calls the skill only through its command-line tools, in subprocesses, so two versions of the skill never
share a Python process and one set of cases can be run against both.

Numbers from this harness describe **synthetic** booths. Read [What a synthetic set cannot show](#what-a-synthetic-set-cannot-show)
before quoting any of them. The recorded baseline of the current engine is in [BASELINE.md](BASELINE.md); the first comparison
with it is [RESULTS-I1.md](RESULTS-I1.md) (round 1, superseded), the question "does holding corners out of the fit cost accuracy?" is
answered in [RESULTS-I1b-arms.md](RESULTS-I1b-arms.md) (round-1 code), and the final measurement of I1 with its verdict and known
limitation is [RESULTS-I1c.md](RESULTS-I1c.md).

Current harness version: **1.1.0** (see [Harness versions](#harness-versions)).

## What is here

| File | Purpose |
| --- | --- |
| `synth_generate.py` | Seeded generator of cases with exact ground truth (`case.json`) and a matching scene description (`scene.json`) |
| `perturb.py` | What a reader's mistakes look like: pixel noise, displaced corners, swapped labels, "assume all of W/D/H" |
| `run_eval.py` | The runner: modes `oracle`, `replay`, `rescore`, `live` (the last only explains why it does not run yet); `--check-corners {0,2}` is the arm switch |
| `metrics.py`, `stats.py` | Per-run metrics against the truth, layered aggregates, bootstrap 95 % intervals |
| `report.py` | Markdown tables for a result |
| `compare_results.py` | Two results side by side, scene by scene, with paired intervals; warns when the suite, seed, fingerprint or versions differ |
| `arms_report.py` | Three or more results side by side (a reference and its variants, for example the arms of `--check-corners`), paired by scene |
| `skill_caps.py` | Detects what a skill folder supports and adapts the cases to it |
| `synth_render.py` | Renders the picture of a case in Blender, with an instance-ID mask (needs Blender; run by hand) |
| `annotations.light.v1.schema.json` | Schema for light annotations of real pictures, for a future real set (schema only) |
| `camgeom.py` | The harness's own pinhole-camera maths, so the truth never depends on the skill version under test |
| `tests/` | Unit tests; `python -m unittest discover -s tools/eval/tests` (no Blender) |
| `BASELINE.md` | The recorded baseline and the exact command that made it |
| `RESULTS-I1.md`, `RESULTS-I1b-arms.md`, `RESULTS-I1c.md` | Measured comparisons with the baseline: round 1 of I1, the check-corner arms (round-1 code), and the final result of I1 |

## Quick start

```
# 1. What does the current skill do on the standard suite? About 19 minutes with --jobs 4 on an idle 4-core laptop (RESULTS-I1c.md:
#    1124 s; BASELINE.md: 1309 s with other programs running); 40 minutes or more on a busy machine. --stages compile halves it;
#    --cases 8 is a quick look.
python tools/eval/run_eval.py oracle --skill-dir booth-image-to-model --suite oracle-v1 --seed 20261019 \
    --out output/_eval/runs/now --jobs 4

# 2. Compare it with the recorded baseline (the same suite and seed must have been used; the baseline folder holds the rescored 1.1 results)
python tools/eval/compare_results.py --a output/_eval/runs/baseline-oracle-v1 --b output/_eval/runs/now --out output/_eval/compare.md

# 2b. The same skill with no corner held out of the fit (the arm switch), paired with the run above
python tools/eval/run_eval.py oracle --skill-dir booth-image-to-model --suite oracle-v1 --seed 20261019 \
    --out output/_eval/runs/now-arm0 --check-corners 0 --jobs 4
python tools/eval/compare_results.py --a output/_eval/runs/now --b output/_eval/runs/now-arm0 --out output/_eval/compare-arms.md

# 3. Tests, including one small oracle smoke test against the repository skill (no Blender)
python -m unittest discover -s tools/eval/tests
```

Results go to `--out`: `results.json` (meta, every row, every aggregate), `report.md` (the tables), and a `work/` folder with the
inputs and outputs of every single run, so a surprising number can be traced to the file that produced it. Everything under
`output/` is git-ignored.

To measure an older version, point `--skill-dir` at a copy of it. The frozen baseline is made like this (the commit is recorded in
`COMMIT.txt` next to it):

```
git archive <commit> booth-image-to-model | tar -x -C output/_eval/baseline
```

While you build something, always measure the baseline copy, not the folder you are editing.

## Modes

**oracle** feeds the skill the true corner pixels of a generated case, after a perturbation, and runs two tools in subprocesses:
`scripts/box_fit.py` (the camera and proportion fit) and `scripts/boothctl.py compile` (the production path, which fits the camera
itself, places every part and writes the spec). There is no model and no Blender, no network, so it is safe for CI.
`--stages fit|compile` runs only one of the two (halves the time); `--seeds N` passes a smaller box_fit seed count to both (the
skill default is 40, the CI smoke test uses 12); `--jobs N` runs N subprocesses at once (timings get noisier).
`--resume` reuses finished runs after an interruption. `--perturbations A B ...` runs any registered perturbation (also the ones that are
not in the suite, such as `wrong_anchor_*`) instead of the suite's own list. Every call clears the previous outputs of a run folder first
(`fit.json`, `compile/`, ...), so a crash or a timeout can never be scored from an earlier call's files, and the grey stand-in picture is
written to a temporary name and renamed, so parallel workers never read a half-written PNG.

`--check-corners {0,2}` is the **arm switch**. `2` (the default, which is what the harness did before the switch existed) sends the case's
own check corners with `fit: false` to a skill that knows the flag: two when the scene has 8 or more clickable points, one with 7, none with
6. `0` sends every corner as a fitted one. The perturbed pixels, the injected errors and the stated dimensions are drawn from the same random
stream whatever the arm, so two runs that differ only in the arm see identical input and are paired by scene and perturbation
(`compare_results.py --a <arm 2> --b <arm 0>`). A skill without the flag fits everything in both arms. `--scale-check` additionally hands every
scene that has a round table a cafe-table scale check (0.74 m, tolerance 0.15) in the skill's own `scale_checks` format.

**replay** re-compiles reader replies that were saved earlier: a folder `DIR/<case id>/scene.json`, optionally with the picture next
to it. With `--cases` pointing at generated cases (`DIR/<case id>/case.json`) the replies are scored against the truth with the
same metrics; without, the compile outcome, the fit checks and the gate are listed.

**rescore** scores the stored raw outputs of an oracle run again with the current metrics code, without calling the skill. Use it
when a metric definition changes: the baseline does not have to be re-run. It reads the arm and the scale-check setting from the run's
`results.json` (a run made before the switch existed is read as arm 2). It overwrites `results.json` and `report.md` of that folder;
keep a copy first when the old numbers matter (the 1.0 files of the stored baseline are kept as `results.harness-1.0.json`).

**live** would call a model on rendered pictures. It is not implemented in this round and exits with code 2 and a message: it needs
the owner's go-ahead, an API key and a cost confirmation first.

## How one set of cases fits several versions of the skill

`skill_caps.py` reads the skill folder's schema and script sources and records what it found in `results.json`:

* whether `corner_points[]` of the scene description has the `fit` key, and which `schema_version` values are accepted;
* whether `box_fit.py` understands a per-point `fit` flag, leave-one-out errors, the robust loss and the `check_failed` status;
* whether `draft_compile.py` writes a `quality_gate`.

The canonical `scene.json` of a case is in the original (v1) format. For an older skill it is used as it is (any `fit` key is
removed, so every corner is fitted); for a newer one the harness adds `fit: false` to the check corners of arm 2 and `fit: null` to the
others and picks the newest accepted `schema_version`. The same is done to the `points.json` of `box_fit.py`. Metrics a version
does not produce (for example outlier names) show `-`.

## Cases

`synth_generate.py --suite oracle-v1 --seed 20261019` writes 32 scenes: eight per booth type (inline, corner, peninsula, island),
metric footprints (widths from 3 to 12 m, depths from 3 to 9 m) and top heights from 2.5 to 4.5 m, some of them not modules (for
example 4.5 x 3.5 and 7.2 x 4.8 m). Each scene has:

* walls on the closed edges (`inline`: back; `corner`: back and left; `peninsula`: back; `island`: none) and an overhead frame at the
  top height on every booth, so the top corners are physical points;
* a camera with pitch 5 to 45 degrees, a small roll, a 18 to 50 mm equivalent lens, six image sizes, partly a crop that moves the
  principal point off centre (the skill fits a centred camera, so this is a model mismatch on purpose), the camera always on an open side;
* 6 to 10 clickable points from the 8 box corners and two edge midpoints, with their exact pixels from the true camera, and, when at
  least 7 are visible, one or two check corners (one on the floor, one on top) that a newer skill holds out of the fit in arm 2 (see
  `--check-corners`); the corner set is the same in every arm, only the `fit` flag differs;
* two to four floor-standing parts (`counter`, `box`, `round_table`, `planter`) at true floor positions inside the footprint, whose
  base pixels come from the true camera.

The same suite name and seed give byte-identical `case.json`, `scene.json` and `suite.json` (a test checks the bytes). Cameras are
spread over the suite with a low-discrepancy sequence, everything else is drawn from a seeded Mersenne Twister.

Each oracle run is one scene under one perturbation (`perturb.py`):

| Perturbation | Corner pixels | Stated dimensions |
| --- | --- | --- |
| `clean` | exact | one anchor (the case's anchor dimension, exact) |
| `noise_1`, `noise_3` | gaussian noise, sigma 1 or 3 px | one anchor |
| `outlier_30` | sigma 0.5 px, one corner displaced by exactly 30 px | one anchor |
| `outlier_1`, `outlier_2` | sigma 0.5 px, one or two corners displaced by 15 to 60 px | one anchor |
| `mislabel` | sigma 0.5 px, the tokens of two corners swapped | one anchor |
| `all_exact` | sigma 0.5 px | W, D and H all stated and exact, recorded as assumed |
| `all_guess` | sigma 0.5 px | W, D and H all stated as assumed: about 12 % error, rounded to 0.5 m |
| `wrong_anchor_m25`, `wrong_anchor_m10`, `wrong_anchor_p10`, `wrong_anchor_p25` | sigma 0.5 px | the anchor dimension stated as assumed, **wrong** by -25, -10, +10, +25 % (not in the `oracle-v1` suite; run with `--perturbations`) |

`all_exact` and `all_guess` model the "assume all of W/D/H" habit of the web reader. **The size of the `all_guess` error is a property of
the harness, not a result about the skill:** it is set by the constant `GUESS_LOG_SIGMA` (a 12 % error on each of three dimensions, then
rounding to 0.5 m), and because all three dimensions are then fixed by the guesses, the proportions inherit that error by construction,
for any engine. So "one stated dimension beats three guessed ones" (`--pair all_guess:noise_1`) is true of the input and is not an
acceptance criterion for a skill change. Whether the reader really states one dimension instead of three is a property of its prompt and can
only be checked with replayed or live reader replies.

**Wrong assumed dimension.** Once the reader is asked to assume only one dimension, its real failure is a wrong value for that one. The
corners fix the proportions (W/H, D/H), the stated dimension alone fixes the metres, so the engine reports a consistent box that is scaled by
about the same wrong factor in W, D and H: the scale-free ratio metrics stay near perfect and nothing in the fit looks wrong. That is why the
`wrong_anchor_*` perturbations are scored with the absolute W, D and H errors in percent (and the harness label `bad_scale`: a dimension off
by more than 10 %). Without an independent size cue (a scale check, a printed dimension) such a run cannot be told from a right one; the
`--scale-check` option gives the skill that cue in the scenes that have a round table.

## Metrics

Every row of `results.json` is one run. The aggregates group them by layer; every aggregate has a bootstrap 95 % interval that
resamples whole scenes (the rows of one scene are not independent).

| Layer | What is measured |
| --- | --- |
| camera | Corner error: mean pixel distance of the 8 fitted box corners from the true ones, as a share of the image diagonal, best over the four horizontal symmetric relabellings of the box. Horizontal field-of-view error (degrees). Camera rotation error (geodesic angle, degrees, no relabelling) |
| scale | Absolute ln error of W/H and of D/H (scale-free, signed in the rows), relative focal-length error, and **absolute W, D and H error in percent** of the true value (median; the largest of the three; the share of runs with a dimension more than 10 % off). The ratios cannot see a wrong stated dimension, these can |
| parts | Floor-part placement error in metres (distance from the built part's footprint centre to the true floor position, median over the parts of a run) and as a share of the true W |
| decision | Does the skill flag bad fits? The flag is the `quality_gate` of newer skills or, for older ones, a `box_fit` status other than `fitted`; a run with no usable fit counts as flagged. The truth label "really bad" is a convention in `metrics.py`: corner error above 1.5 % of the diagonal, or W/H or D/H off by more than 0.10 in absolute ln (scale-free on purpose; a wrong absolute scale is the separate label `bad_scale`). Reported: flag rate, share of really bad runs, accuracy, sensitivity (recall), precision, false-alarm rate, flags on runs with an injected bad corner, on defective input (a bad corner, guessed or wrong dimensions) and on valid input, flags on runs with a wrong stated dimension, runs with no usable fit |
| outliers | **Recall: the share of injected bad corners that were fitted in the run and that the skill names** (when it reports `outliers`). An injected corner that the run held out as a check corner can never be named by leave-one-out, so it is not in the denominator; such runs are reported apart (was the run flagged, did it end as `check_failed`). Recall is also cut by the number of fitted points (`<=6`, `7`, `8`, `9+`) because leave-one-out needs spare equations, and given for the runs in which leave-one-out was computed (a newer skill reports null when it is not reliable). Also: good corners named, runs without injected errors in which one is named, and how often the worst-fitting fitted point is an injected one. The harness-1.0 definitions (all injected corners in the denominator; the worst of all listed points, check corners too) stay in the rows and aggregates as `outlier_recall_all_injected` and `worst_hit_rate_all_points`, so older numbers can be reproduced |
| runtime | Wall seconds of `box_fit.py` and of `boothctl compile` (which includes the fit; this is the production path), both with interpreter and import start-up, on the machine that ran them |
| contract | `box_fit.py` prints exactly one JSON object on stdout; the standalone fit and the fit inside compile agree |

A metric of a layer that does not apply to a run (for example the corner error of a run with no usable fit) is left out of that
aggregate; its `n` shows how many runs counted.

The report also cuts a few metrics by the number of fitted points and by cropped against full-frame pictures (the principal point of a
crop is off centre, which the skill's centred camera cannot model: read headline medians together with that cut).

## Harness versions

* **1.1.0.** `outlier_recall` counts only injected corners that were fitted (the 1.0 definition is kept under its own name); recall by
  number of fitted points and when leave-one-out was computed; check-corner injections reported apart; absolute W, D, H errors, the labels
  `bad_scale` and `defective_input`, gate precision; the `wrong_anchor_*` perturbations; the arm switch `--check-corners`, the option
  `--scale-check`, `--perturbations` accepting any registered name; stale outputs cleared and the stand-in picture written atomically;
  `compare_results.py` warns when the suite, seed, suite fingerprint, generator version, harness version or scene count differ; the
  `oracle-v1` suite fingerprint is pinned by a test. The scenes and the 1.0 perturbations are unchanged (same suite fingerprint). A 1.0
  run rescored with 1.1 reproduces every 1.0 row field and aggregate exactly and adds the new fields (checked for the stored baseline).
* **1.0.0.** First version, used for the baseline.

## Comparing two runs

`compare_results.py` pairs the rows of the two sides by scene and reports, for every metric and perturbation, A, B and B minus A with
a paired bootstrap 95 % interval, marking a clear gain (`+`), a clear loss (`-`) or no clear change (`=`) in the direction that is
better for that metric. It warns when the suites or seeds differ. Run both skills back to back on the same machine: seconds depend on
what else the machine is doing, or better, at the same time and with the same `--jobs`.

It warns (on stderr and in the markdown) when the suite, seed, suite fingerprint, generator version, harness version or number of scenes
differ, and shows for each side how many corners were held out of the fit. Two sides of different arms are a legitimate comparison; two
sides of different scenes are not. Rescore an older run first when the harness version differs (`run_eval.py rescore`).

## Pictures for a later live mode

`synth_render.py` renders the picture of a case so that a model can be tested on it later:

```
python tools/eval/synth_render.py --case output/_eval/cases/synth_0003 --out output/_eval/renders/synth_0003 \
    --skill-dir booth-image-to-model --samples 16 --clutter --post jpeg=80,noise=2,blur=0.6 --overlay
```

It builds the true spec with the skill's own compile (true camera and dimensions as the fit), starts one background Blender, renders
with Cycles at low samples, then renders an instance-ID mask (`mask.png`: R + 256 G is the id of a spec component, 0 is background;
`mask_index.json` maps ids to components and parts). `--clutter` adds a hall floor, stand-in people and trusses that are not part of the
booth; `--post` writes a degraded copy (blur, noise, JPEG); `--overlay` draws the true corners over the picture to check alignment.
Run only one Blender process at a time. Pictures are results: keep them under `output/` and never track them.
`mask_preview.png` is a coloured copy of the mask for people. The truth spec and the Blender scene come from the skill's own compile and
geometry, and `--skill-dir` defaults to the working tree, so a picture rendered later can differ from an earlier one: `render_log.json`
records the skill version and the code fingerprint (the `.py` and `.json` files of `scripts/` and `schemas/`) of every render. Use a
frozen copy of the skill (as for the baseline) for pictures that must stay comparable.

## Real pictures later

`annotations.light.v1.schema.json` describes what a person adds to a real picture in a few minutes: inventory, scope, corners with
the same tokens as `box_fit.py` (two of them marked as check corners), part boxes with floor contact points, dimensions printed on the
picture, open sides, the reviewer and the licence. No real picture is annotated yet, and no annotation file belongs in the repository
unless its picture is free to use and the licence block says so.

## What a synthetic set cannot show

* It is built from **our own building blocks** (box walls, a ring frame, four furniture macros), and the skill builds the same blocks.
  Inventory-style scores would look better here than on real booths, and nothing here says how well a model recognises real furniture,
  materials or graphics.
* The camera is a **perfect pinhole**: no lens distortion, no rolling shutter, no rectification of a wide-angle phone picture. Real
  corners are also never exact; the noise levels are a guess at a reader's precision, not a measurement.
* **One render style**: clean surfaces, simple lighting, no reflections, no printed graphics, a plain grey hall. Pictures with heavy
  clutter, crowds or glass are not represented. The optional clutter in `synth_render.py` is light.
* The oracle mode starts from true corners. It measures the **fit and the compile**, not whether a reader finds the right corners. A
  perturbation is a model of a reader's mistakes, and the mix of mistakes in the set is not the mix in practice.
* Footprints and heights are generic modules, and the booth-type mix (a quarter each) is not a market statistic.
* The "really bad" threshold, the gate comparison and the headline list are conventions of this harness.

So an accuracy claim about real booths needs a real, annotated set; a gain on this set shows only that the engine behaves better on
the situations the generator can make.

## Data hygiene

Everything here is generic: no client names, picture names or dimensions from a client picture. Sizes are common metric modules plus a
few that are not. Generated cases, renders and results live in `output/`, which git ignores.
