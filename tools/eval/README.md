# Evaluation harness (`tools/eval/`)

This folder measures the skill. It answers one question for every change to the engine: is the model more accurate, and is it
as fast or faster, than before? It lives at the repository level and is not shipped with the skill. The harness itself imports the
standard library, numpy and Pillow only (scipy is the skill's dependency and jsonschema is used by two tests; neither is imported by
a harness module). It calls the skill only through its command-line tools, in subprocesses, so two versions of the skill never
share a Python process and one set of cases can be run against both.

Status (2026-10-22): the iterations I3 to I7 are paused; the harness is reused by the proof of concept as its second
anchor (see [docs/poc/03-scoring-spec.md](../../docs/poc/03-scoring-spec.md), section 6).

Numbers from this harness describe **synthetic** booths. Read [What a synthetic set cannot show](#what-a-synthetic-set-cannot-show)
before quoting any of them. The recorded baseline of the current engine is in [BASELINE.md](BASELINE.md); the first comparison
with it is [RESULTS-I1.md](RESULTS-I1.md) (round 1, superseded), the question "does holding corners out of the fit cost accuracy?" is
answered in [RESULTS-I1b-arms.md](RESULTS-I1b-arms.md) (round-1 code), and the final measurement of I1 with its verdict and known
limitation is [RESULTS-I1c.md](RESULTS-I1c.md).

Current harness version: **1.2.0** (see [Harness versions](#harness-versions)). Iteration I2 (the booth grammar: type, open sides,
cutaways) added the section [Booth grammar (I2)](#booth-grammar-i2) and the opt-in perturbations `mirror_fb`, `mirror_lr`, `swap_fb_lr`
and `wrong_open_sides`.

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
| `grammar.py` | The booth grammar in the harness's own words: type rule, truth of a case, token mirroring, where the compiled walls stand, which gate reasons are topology reasons |
| `synth_render.py` | Renders the picture of a case in Blender, with an instance-ID mask (needs Blender; run by hand) |
| `annotations.light.v1.schema.json` | Schema for light annotations of real pictures, for a future real set (schema only) |
| `camgeom.py` | The harness's own pinhole-camera maths, so the truth never depends on the skill version under test |
| `tests/` | Unit tests; `python -m unittest discover -s tools/eval/tests` (no Blender) |
| `BASELINE.md` | The recorded baseline and the exact command that made it |
| `RESULTS-I1.md`, `RESULTS-I1b-arms.md`, `RESULTS-I1c.md` | Measured comparisons with the baseline: round 1 of I1, the check-corner arms (round-1 code), and the final result of I1 |
| `RESULTS-I2.md` | The measured result of I2 (booth type and open sides): grammar perturbations against the baseline and the default suite against the I1 final run |

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

* whether the schema knows all three booth grammar keys (`booth_type`, `open_sides`, `cutaway_sides`; schema version `1.2`), and whether
  `draft_compile.py` writes a `topology` block.

The canonical `scene.json` of a case is in the original (v1) format. For an older skill it is used as it is (any `fit` key is
removed, so every corner is fitted); for a newer one the harness adds `fit: false` to the check corners of arm 2 and `fit: null` to the
others and picks the newest accepted `schema_version`. The same is done to the `points.json` of `box_fit.py`. A skill whose schema knows
the booth grammar keys is sent the topology that the run's reader declares (null where nothing is declared) in schema version `1.2`; any
other skill gets the three keys removed (its schema refuses unknown keys and it would ignore them). Metrics a version does not produce
(for example outlier names, or the topology checks) show `-`.

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

The same suite name and seed give byte-identical `case.json`, `scene.json` and `suite.json` (a test checks the bytes). `case.json` holds
the truth of the topology (`booth_type`, `open_sides`, `walls`). The stored `scene.json` does not carry the booth grammar keys, because adding
them would move the pinned suite fingerprint that every stored comparison rests on: the topology of a run is added when the run's scene is
made (`perturb.scene_for_run`). `synth_generate.py --grammar` writes the scenes with the keys (schema 1.2, the true booth type and open
sides, no cutaway) for replay mode or for trying the skill by hand; the suite file then says `"grammar": true` and has its own fingerprint. Cameras are
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
| `mirror_fb`, `mirror_lr`, `swap_fb_lr`, `wrong_open_sides` | sigma 0.5 px | one anchor; the **topology** is what is wrong, see [Booth grammar (I2)](#booth-grammar-i2) (not in the `oracle-v1` suite; run with `--perturbations`) |

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
| topology | (harness 1.2, [Booth grammar (I2)](#booth-grammar-i2)) Whether the gate flags runs whose corner tokens are mirrored or whose declared open sides are wrong, with a topology reason (recall), how much of that the topology adds to the old gate, how often it raises a topology reason on runs whose declaration is right (false alarms), the status of each of the four topology checks, and walls in the compiled spec on a side the reader declared open |
| runtime | Wall seconds of `box_fit.py` and of `boothctl compile` (which includes the fit; this is the production path), both with interpreter and import start-up, on the machine that ran them |
| contract | `box_fit.py` prints exactly one JSON object on stdout; the standalone fit and the fit inside compile agree |

A metric of a layer that does not apply to a run (for example the corner error of a run with no usable fit) is left out of that
aggregate; its `n` shows how many runs counted.

The report also cuts a few metrics by the number of fitted points and by cropped against full-frame pictures (the principal point of a
crop is off centre, which the skill's centred camera cannot model: read headline medians together with that cut).

## Booth grammar (I2)

Iteration I2 lets the reader state the booth type and the open sides first, and the compiler uses that as a check. The words are fixed:
**open sides** are the sides of the footprint with no wall, where visitors come in (`front` is -Y, `back` +Y, `left` -X, `right` +X, the same
words as the corner tokens); a **cutaway** is a side whose wall or the roof is only left out in the picture to show the inside. The **booth
type** follows from the open sides: `inline` 1, `corner` 2 adjacent, `peninsula` 3, `island` 4, `other` anything else (two opposite sides,
none). The compile writes `report.topology` with four checks (`type_matches_sides`, `wall_on_open_side`, `closed_side_without_wall`,
`camera_side`; each `ok`, `warn`, `fail` or `not_checked`) and adds a plain reason to the quality gate for each `fail`. `grammar.py` keeps the
harness's own copy of the type rule, so a bug in the skill's rule cannot hide itself.

**What every run declares.** The topology of a run is the truth of its case, as a reader that got it right would state it: `booth_type`,
`open_sides` and `cutaway_sides` (always empty: the generator has no cutaway scenes). The generator's own label stays in `case.json` and in the
`booth_type` column of the rows, but its `inline` scenes have a back wall only, so three sides are open and by the vocabulary they are
peninsulas; the rows carry the vocabulary's type in `grammar_type`. The suite therefore has no one-open-side booth, and the inline rule of the
type check is not exercised by the right declarations.

**The four opt-in perturbations** (never in a suite's own list; run with `--perturbations`; 0.5 px noise and one exact anchor dimension, so
they differ from an ordinary reader in the topology only):

| Perturbation | What is wrong | What the skill should do |
| --- | --- | --- |
| `mirror_fb` | Every front/back corner token is swapped (`D/2` and `-D/2`); the declared sides and the walls are right | Nothing to see in the topology: a single swap is a reflection that no camera can reproduce, so the fit is left with a large residual and the old gate is expected to flag it already (`topology_flag_any` against `topology_added_flag` shows whether it does) |
| `mirror_lr` | The same for left/right | The same |
| `swap_fb_lr` | Both swaps together, which turns the booth half way round; the declared sides are right | A box looks the same after a half turn, so the fit is perfect and the camera lands on the far side of the booth. Only `camera_side` can notice, and only when the camera ends up outside closed sides only |
| `wrong_open_sides` | One side is declared the wrong way round and the type is changed to fit the declared sides (a fair coin per scene picks the mistake: `open_as_closed` or `closed_as_open`; an island has no closed side and always gets the first); the wall components are unchanged | `closed_as_open`: the wall on the side declared open is left out and the gate says so. `open_as_closed`: nothing is wrong in the model, and a closed side without a wall is information only; it is noticed only when the camera stands outside the wrongly closed side alone |

**Reading the numbers (the metrics of the `topology` layer).** A run of these four counts as really bad whatever its fit residual says
(`bad_fit` is true: it must be reviewed), and as a defective input.

* `topology_flag_rate`: of those runs, the share flagged **with a topology reason**; `topology_flag_any`: flagged for any reason (what a skill
  without the feature can reach, through the fit alone); `topology_added_flag`: flagged **only** by topology reasons, i.e. what the
  topology adds to the old gate. Cut by perturbation, by kind of mistake (`open_as_closed`, `closed_as_open`, `mirror_fb`, ...) and by true type.
* `topology_catchable`: the ceiling to read the recall against. Of those runs, the share the topology checks could catch at all, in the best
  case (a perfect fit, the true camera, the skill's 0.25 m camera margin; `grammar.best_case_catch`). It is a property of the suite, not of the
  skill. Not defined for the single mirrors, whose fit has no best-case camera.
* `topology_false_alarm`: of all other runs (the declaration is right), the share flagged with a topology reason; `topology_added_false_alarm`:
  flagged only by topology reasons, i.e. the extra review flags the topology causes. This is the number to hold against the false-alarm
  limit; the older `false_alarm_rate` also moves with every other change of the skill.
* `topo_*_rate`: how often each check says `fail` (or `warn`), `topo_not_checked_rate`: runs in which nothing was checked,
  `topology_fail_in_gate`: runs with a failed check whose gate also names a topology reason (should be 100 %).
* `walls_on_open_declared_rate`: runs whose compiled spec holds one of the scene's walls on a side the reader declared open (read from
  `spec.json`: the wall box that stands on the footprint edge). It must be 0 for a skill that uses the declaration, and is above 0 for one that
  ignores it on a `closed_as_open` run. `walls_on_open_truth_rate` does the same against the truly open sides (0 by construction while walls are
  built on their own plane); `walls_dropped_rate`: runs in which the compiler reports that it left a wall out.

**What this cannot show.**

* The synthetic cameras stand outside an open side in every scene (a test checks it, with at least 0.6 m of room on that axis). The false
  alarms of `camera_side` are therefore measured only on pictures taken from the open side. A real picture from inside the hall, from above, or
  from a closed side through a cutaway may be treated differently; nothing here measures that.
* A reader that mirrors the corner tokens **and** the declared sides together describes a mirror-image booth that no topology check can tell
  from a right reading, so it is not modelled. The perturbations model a reader whose corner tokens and stated sides disagree.
* The ceilings of `oracle-v1`, seed 20261019 (a test pins them): of its 32 scenes at most 13 can be caught for `swap_fb_lr` and 14 for
  `wrong_open_sides` (all 13 `closed_as_open` ones through the wall check, and 1 of 19 `open_as_closed` through the camera). A recall of 100 % is
  impossible by construction, and so is 80 % for `swap_fb_lr` alone.
* An island has four open sides, so the camera is always outside one: `camera_side` cannot catch a turned island. The suite has no
  one-open-side booth and no cutaway scene, so neither the inline rule nor the cutaway allowance is exercised.
* The mix of mistakes (a fair coin per scene) and the share of runs per mistake are choices of the harness, not statistics of real readers;
  read the numbers per kind of mistake, not pooled.
* No reader (model) took part: the declaration is given, not read from a picture.

## Harness versions

* **1.2.0.** The booth grammar (I2): `grammar.py`; the opt-in perturbations `mirror_fb`, `mirror_lr`, `swap_fb_lr` and `wrong_open_sides`; the
  run scene states the declared topology for skills that know the keys (`skill_caps` detects them and strips them for older skills); the
  `topology` metric layer and report section; `rescore --to` writes to another folder; `compare_results.py` no longer warns for 1.1.0
  against 1.2.0. Only metrics and perturbations were added: the suite fingerprint, `oracle-v1` and the 13 older perturbations are unchanged
  (a test pins what they hand to the skill), and a 1.1.0 run rescored with 1.2.0 reproduced every 1.1.0 row field of the three stored runs
  checked (24,192 values each, 0 differences). The truth label `bad_fit` and the input labels `valid_input` and `defective_input` change only
  for the four new perturbations.
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

## Measuring I2

The three groups of runs behind the I2 acceptance criteria, from the repository root. Use plain copies of the skills (the frozen baseline,
and a snapshot of the working tree taken before the runs start) and run one group at a time on an otherwise idle machine (timings and the
wall time of a run depend on what else the machine is doing). Scenes and seed are those of the suite, so everything pairs by scene.

```
# snapshot of the skill under test (a plain copy; edits made later do not matter)
python -c "import shutil; shutil.copytree('booth-image-to-model', 'output/_eval/i2/booth-image-to-model', ignore=shutil.ignore_patterns('__pycache__'))"

# A. the frozen baseline on the grammar perturbations (compile is what the baseline's gate and spec come from)
python tools/eval/run_eval.py oracle --skill-dir output/_eval/baseline/booth-image-to-model --suite oracle-v1 --seed 20261019 \
    --perturbations mirror_fb mirror_lr swap_fb_lr wrong_open_sides --check-corners 0 --stages compile --jobs 4 \
    --out output/_eval/runs/i2-baseline-grammar --title "I2 grammar perturbations, frozen baseline"

# B. the snapshot on the same runs
python tools/eval/run_eval.py oracle --skill-dir output/_eval/i2/booth-image-to-model --suite oracle-v1 --seed 20261019 \
    --perturbations mirror_fb mirror_lr swap_fb_lr wrong_open_sides --check-corners 0 --stages compile --jobs 4 \
    --out output/_eval/runs/i2-grammar --title "I2 grammar perturbations, snapshot"

# C. the snapshot on the default suite, to hold against the I1 final run (the same arm: no corner held out)
python tools/eval/run_eval.py oracle --skill-dir output/_eval/i2/booth-image-to-model --suite oracle-v1 --seed 20261019 \
    --check-corners 0 --jobs 4 --out output/_eval/runs/i2-check0 --title "I2 default suite, no withheld corners"

python tools/eval/compare_results.py --a output/_eval/runs/i2-baseline-grammar --b output/_eval/runs/i2-grammar --out output/_eval/compare-i2-grammar.md
python tools/eval/compare_results.py --a output/_eval/runs/i1c-check0 --b output/_eval/runs/i2-check0 --out output/_eval/compare-i2-default.md
# optional: give the I1 run the topology columns (all zero: it never produced a topology reason); the stored results are not touched
python tools/eval/run_eval.py rescore --dir output/_eval/runs/i1c-check0 --to output/_eval/runs/i1c-check0-h12
```

The "Booth grammar" tables (by perturbation, by kind of mistake, by true type) are in the `report.md` of A and B. In the comparison of C with
the I1 run, `false_alarm_rate` and `flag_on_valid_input` sit next to the accuracy medians; the `report.md` of C holds
`topology_added_false_alarm`, the extra review flags that the topology checks alone cause on runs whose declaration is right.

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
* The booth grammar has its own list of limits above ([What this cannot show](#booth-grammar-i2)).

So an accuracy claim about real booths needs a real, annotated set; a gain on this set shows only that the engine behaves better on
the situations the generator can make.

## Data hygiene

Everything here is generic: no client names, picture names or dimensions from a client picture. Sizes are common metric modules plus a
few that are not. Generated cases, renders and results live in `output/`, which git ignores.
