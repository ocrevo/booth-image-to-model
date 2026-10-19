# I1 final results: the honest fit against the frozen baseline

> **Status (2026-10-19).** This is the final measurement of I1 and the document that carries its verdict. I1 is closed with one known
> limitation: valid input is sent to review in 14.1 % of the runs, 0.5 percentage point above the limit that was set before the
> measurement (criterion 3), and highest on cropped pictures. The owner decided to close I1 with the current results and with the
> limitation written down (see [Known limitation](#known-limitation)). The two earlier documents are rounds on earlier code:
> [RESULTS-I1.md](RESULTS-I1.md) (the first design, two corners held out of the fit, against the original criteria) and
> [RESULTS-I1b-arms.md](RESULTS-I1b-arms.md) (the round-1 code with and without held-out corners).

Read [README.md](README.md) ("What a synthetic set cannot show") before quoting any number. Every scene is a perfect pinhole box
built from our own blocks, the corners are the true ones plus a model of a reader's mistakes, and 32 scenes stand behind each
interval. **No real picture has been measured, and no reader (a vision model) took part: the harness feeds the skill corner pixels
directly.**

**Short answer.** On this synthetic suite, with a reader that makes mistakes, the final fit is clearly more accurate than the
frozen baseline: over all 288 runs the median corner error falls from 0.34 % to 0.10 % of the image diagonal, the camera rotation error
from 1.26 to 0.40 degrees, the depth/height error (absolute ln) from 0.0152 to 0.00308 and the floor-part placement error from 0.075 m to
0.025 m. One displaced corner of 30 px is named in 29 of 30 runs that have 8 or more fitted corners. Clean and lightly noisy input is not
clearly changed, and really bad runs are still flagged. Three of the four revised acceptance criteria are met. The fourth (valid input
flagged for review at most the baseline plus 5 points) is missed by 0.5 point: 14.1 % against 8.6 %, with an interval that includes
zero.

## What was run

| Item | A: baseline | B: final (this document) |
| --- | --- | --- |
| Skill | the frozen copy of commit `b377476` (`output/_eval/baseline/booth-image-to-model`), version 2.8.0-rc1 | a plain copy of the working tree taken on 2026-10-19 before the I1 commit (`output/_eval/current2/booth-image-to-model`); its `SKILL.md` still read 2.8.0-rc1, the version bump comes with the commit |
| Code fingerprint (scripts and schemas, first 16) | `117671dca65f2c48` | `b91477c450ed8ca0` (the round-1 code of `RESULTS-I1b-arms.md` was `58ffee64e790b8aa`) |
| Harness, suite, seed | 1.1.0, `oracle-v1`, 20261019 | the same; suite fingerprint `e29d9cff4dbf9908` on both sides |
| Scenes and runs | 32 scenes x 9 perturbations = 288 runs; 0 runs without a usable fit | the same |
| Arm (corners held out of the fit) | 2 (not used: the baseline has no such flag and fits every corner) | 0: every corner is fitted. No check corner is withheld |
| Stages | `compile` only (`--stages compile`), one process at a time (`--jobs 1`) | `fit` and `compile`, `--jobs 4` |
| Scale check handed to the skill | no | no |
| Machine | a Windows 10 laptop, 4-core CPU, Python 3.12.3, numpy 1.26.4; other programs were running | the same machine, **idle** during the run |
| Wall time | 3926 s | 1123.7 s (18 min 44 s) |
| Results (git-ignored) | `output/_eval/runs/i1b-baseline` | `output/_eval/runs/i1c-check0` (`report.md` holds every table of the run) |

Commands, from the repository root:

```
python tools/eval/run_eval.py oracle --skill-dir output/_eval/current2/booth-image-to-model --suite oracle-v1 --seed 20261019 --out output/_eval/runs/i1c-check0 --check-corners 0 --jobs 4 --title "I1 round 2, no withheld corners"
python tools/eval/compare_results.py --a output/_eval/runs/i1b-baseline --b output/_eval/runs/i1c-check0 --out output/_eval/compare-i1c.md
```

* Side A is the compile-only re-run of the frozen baseline that was made for the arm comparison. It is the same code as the recorded
  baseline in [BASELINE.md](BASELINE.md) (fingerprint `117671dca65f2c48`). Checked on 2026-10-19 against the recorded baseline run, row by
  row: 0 differences in 2,880 values (corner error, field-of-view error, both ln proportion errors, the flag, the fit and compile
  statuses and the fitted W, D and H of all 288 runs). Only the seconds differ, because of the machine load.
* The comparison is paired by scene and perturbation. `B minus A` is the paired difference with a 95 % percentile interval from 1000
  resamples of whole scenes. Marks: `+` clear gain, `-` clear loss, `=` no clear change (the interval contains zero).
* The code state is the working tree of 2026-10-19 before the I1 commit. Edits after that copy are not covered by these numbers.

## All runs

Medians over the 288 runs unless the metric says otherwise; rates are means over scenes. Copied from the comparison file.

| Metric | Scenes | A baseline | B final | B minus A | Mark |
| --- | --- | --- | --- | --- | --- |
| Corner error, share of image diagonal (median) | 32 | 0.34% | 0.10% | -0.25% [-0.28%, -0.20%] | + |
| Camera rotation error, degrees (median) | 32 | 1.26 | 0.40 | -0.86 [-1.12, -0.39] | + |
| Field-of-view error, degrees (median) | 32 | 0.96 | 0.38 | -0.58 [-0.83, -0.40] | + |
| Absolute ln error of W/H (median) | 32 | 0.0113 | 0.00252 | -0.00874 [-0.0111, -0.0066] | + |
| Absolute ln error of D/H (median) | 32 | 0.0152 | 0.00308 | -0.0121 [-0.0191, -0.00891] | + |
| Absolute error of W, percent of the true W (median) | 32 | 0.06% | 0.05% | -0.02% [-0.36%, 0.02%] | = |
| Absolute error of D, percent of the true D (median) | 32 | 0.31% | 0.09% | -0.22% [-0.81%, 0.00%] | = |
| Absolute error of H, percent of the true H (median) | 32 | 0.05% | 0.02% | -0.03% [-0.62%, 0.00%] | = |
| Floor-part placement error, metres (median of runs) | 32 | 0.0752 | 0.0251 | -0.0501 [-0.0622, -0.0313] | + |
| Flag agrees with truth | 32 | 67.0% | 55.6% | -11.5% [-16.0%, -6.94%] | - |
| Really bad runs that are flagged | 29 | 98.5% | 97.9% | -0.66% [-2.37%, 0.00%] | = |
| Flagged runs whose fit is really bad (precision) | 32 | 42.7% | 26.6% | -16.1% [-22.2%, -9.71%] | - |
| Good runs that are flagged | 32 | 43.3% | 52.7% | 9.38% [4.96%, 14.3%] | - |
| Runs with an injected bad corner that are flagged | 32 | 95.3% | 96.9% | 1.56% [0.00%, 3.91%] | = |
| Runs with valid input (no injected error, right dimensions) that are flagged | 32 | 8.59% | 14.1% | 5.47% [-0.78%, 13.3%] | = |
| Runs with no usable fit | 32 | 0.00% | 0.00% | 0.00% [0.00%, 0.00%] | = |
| Compile path seconds (mean) | 32 | 13.17 | 7.97 | -5.20 [-6.68, -3.96] | not comparable |

Reading the table:

* **Accuracy.** Corner error, camera rotation, field of view, both proportions and the part placement are clearly better. The absolute
  W, D and H errors were already small at the median and show no clear change (intervals include zero).
* **The decision rows are worse, and the reason matters.** Agreement of the flag with the outcome (67.0 % to 55.6 %), precision (42.7 % to
  26.6 %) and good runs that are flagged (43.3 % to 52.7 %) are all measured against the harness's outcome label ("really bad": corner error
  above 1.5 % of the diagonal, or a proportion off by more than 0.10 in absolute ln). The fits got better, so the number of really bad runs fell from 71 to 47
  of 288, while the gate still sends runs with a defective input to review once the robust loss has repaired the fit: of the 127 runs that
  are good by outcome and flagged, 111 have a defective input (an injected bad corner or guessed dimensions; baseline: 83 of 94) and 16 have valid input. So these three
  rows punish a gate that stays careful after the damage is repaired. Against input defects the flag keeps its reliability: 96.9 % of the
  runs with a defective input are flagged (baseline 95.6 %) and 89.6 % of the flagged runs have a defective input (baseline 93.3 %). Of the three,
  only the rate on valid input is an acceptance criterion; the other rows are disclosed here so that nobody reads the table selectively.
  About 64 % of the fall in agreement (about 7 of the 11.5 points) comes from the `mislabel` runs, where the fit is now right and the run is still flagged.
* **Really bad runs.** Per run, 70 of 71 were flagged before and 46 of 47 now; it is the same single run in both (one scene at 3 px noise: the fit looks fine in pixels, the gate passes it, and D/H is off by 0.139 in ln, baseline 0.142).
  The 98.5 % in the table averages over scenes.
* **Time.** Not comparable: side A ran with other programs on the machine (its compile mean was 13.17 s, against 9.48 s for the same code in its
  recorded run), side B on an idle machine. No gain or loss in speed is claimed. See [What these numbers do not show](#what-these-numbers-do-not-show).

## By perturbation

Medians, A to B, with the mark of the paired interval. "Flagged" is the share of runs sent to review (A from `BASELINE.md`, B from the run report). Part
placement is in metres, `abs ln` is the absolute natural-log error of a proportion, the corner error is a share of the image diagonal.

| Perturbation | Corner error (% diag) | Rotation (deg) | abs ln W/H | abs ln D/H | Placement (m) | Flagged |
| --- | --- | --- | --- | --- | --- | --- |
| clean (exact corners) | 0.00 to 0.00 = | 0.00 to 0.00 = | 0 to 0 = | 0 to 0 = | 5.55e-17 to 5.55e-17 = | 3.12% to 9.38% |
| noise_1 (1 px) | 0.07 to 0.07 = | 0.18 to 0.18 = | 0.00207 to 0.00221 = | 0.00257 to 0.00232 = | 0.0285 to 0.0286 = | 3.12% to 9.38% |
| noise_3 (3 px) | 0.19 to 0.20 = | 0.51 to 0.59 = | 0.00929 to 0.0106 = | 0.00881 to 0.011 = | 0.0742 to 0.0708 = | 21.9% to 31.2% |
| all_exact (W, D, H stated, exact) | 0.03 to 0.03 = | 0.07 to 0.07 = | 0 to 0 = | 0 to 0 = | 0.0109 to 0.0109 = | 6.25% to 6.25% |
| outlier_1 (one corner 15 to 60 px off) | 0.43 to 0.04 + | 1.25 to 0.12 + | 0.0145 to 0.00252 + | 0.0265 to 0.00253 + | 0.0971 to 0.0182 + | 81.2% to 87.5% |
| outlier_2 (two corners 15 to 60 px off) | 0.76 to 0.11 + | 1.97 to 0.69 + | 0.0256 to 0.00571 + | 0.0418 to 0.00664 + | 0.126 to 0.0248 + | 100% to 100% |
| outlier_30 (one corner 30 px off) | 0.41 to 0.04 + | 0.89 to 0.09 + | 0.0166 to 0.00172 + | 0.0237 to 0.00234 + | 0.0933 to 0.0141 + | 100% to 100% |
| mislabel (two corner labels swapped) | 9.19 to 0.13 + | 24.56 to 1.13 + | 0.505 to 0.00463 + | 0.496 to 0.00463 + | 1.47 to 0.0312 + | 100% to 100% |
| all_guess (W, D, H all assumed, about 12 % off) | 1.30 to 1.37 = | 4.49 to 5.11 - | 0.144 to 0.144 = | 0.182 to 0.182 = | 0.305 to 0.300 = | 96.9% to 96.9% |

### clean, noise_1 and noise_3

* `clean` and `noise_1`: no clear change in any accuracy metric. In `noise_1` the proportions differ in the fourth digit (W/H 0.00207 to 0.00221, D/H
  0.00257 to 0.00232), the corner error is 0.07 % on both sides.
* `noise_3` (intervals are those of the paired difference): corner error 0.19 % to 0.20 % [-0.02, +0.05]; rotation 0.51 to 0.59 degrees [-0.03, +0.17]; field of view 0.68 to 0.78 [-0.07, +0.48];
  W/H 0.00929 to 0.0106 [-0.00186, +0.00444]; D/H 0.00881 to 0.011 [-0.000735, +0.00398]; absolute D error 0.41 % to 0.78 % [-0.14, +0.79]; part placement 0.0742 to 0.0708 m
  [-0.0096, +0.0107]. Every interval includes zero, so the criterion holds as written. The point estimates of the two proportions are 14 % and 25 % higher, and the D/H interval
  ends just short of excluding zero: with 32 scenes a small loss at 3 px noise cannot be ruled out, and we did not look for its cause.
* More of these runs go to review: `clean` 3.12 % to 9.38 %, `noise_1` 3.12 % to 9.38 %, `noise_3` 21.9 % to 31.2 % (a rise of 9.4 points whose interval, [-3.1, +21.9], includes zero).
  This is the valid-input rate of criterion 3, see below.

### outlier_30 and the other displaced corners

* `outlier_30`, paired differences: corner error -0.37 points [-0.45, -0.32]; rotation -0.79 degrees [-1.28, -0.58]; field of view -1.50 [-2.26, -0.80]; W/H -0.0149
  [-0.0208, -0.0106]; D/H -0.0214 [-0.0302, -0.0137]; part placement -0.0792 m [-0.0939, -0.0453]. Everything accuracy-related is a clear gain, and the corner error and
  the rotation fall by about a factor of ten.
* `outlier_1` and `outlier_2` improve in the same way (see the table); the two-outlier case is the weakest (rotation 1.97 to 0.69 degrees).
* The flag rate stays high: `outlier_1` 81.2 % to 87.5 % (no clear change, interval [0, +15.6] points), `outlier_2` and `outlier_30` 100 % on both sides.

### mislabel (two corners with swapped labels)

* The swapped labels no longer wreck the camera: corner error 9.19 % to 0.13 % (paired difference -9.06 points, interval -9.99 to -7.36), rotation 24.56 to 1.13 degrees [-32.89, -17.07],
  field of view 42.91 to 0.67, W/H 0.505 to 0.00463, D/H 0.496 to 0.00463, part placement 1.47 to 0.0312 m. In the round-1 code the same case (all corners fitted) gave a rotation error of 2.39 degrees; the
  held-out arm of round 1 gave 21.17.
* Every one of the 32 runs is still flagged for review, and 65.6 % of the swapped corners are named (90.5 % with 9 or more fitted corners, 22.2 % with 8, 0 % with 7; see below). Because the fit is right while the run is flagged, the
  harness counts agreement with the outcome as 31.2 % here (baseline 96.9 %, where the fit was wrong and flagged).

### all_guess (three assumed dimensions)

* Clear losses on the camera numbers: rotation 4.49 to 5.11 degrees (+0.62 [0.21, 1.53]) and field of view 2.96 to 6.41 (+3.45 [0.55, 6.05]); corner error and the proportions show no clear change (the
  proportions are fixed by the assumed values for any engine, see `README.md`). The reader's prompt now asks for at most one assumed dimension, which is outside what the oracle mode can test. This loss was
  already present in round 1 and is not part of the acceptance criteria.

## Naming an outlier: by number of fitted points

Leave-one-out predicts each fitted corner from the others, which needs spare equations. The rule in `box_fit.py` keeps 5 spare equations after holding one point out, which with one stated dimension (9 unknowns)
means 8 fitted corners; with all three dimensions stated 7 are enough. Below that, `box_fit.py` reports no leave-one-out numbers and names nothing, and the quality gate says that there is no independent check. In the suite, 2 of 32 scenes have 7 fitted
corners, 9 have 8 and 21 have 9 or 10.

Injected corners that were fitted and that the skill names, by the number of fitted points of the run (counts are corners; `outlier_2` and `mislabel` inject two corners per run, the others one):

| Perturbation | all | 7 fitted | 8 fitted | 9+ fitted |
| --- | --- | --- | --- | --- |
| outlier_30 (one corner) | 90.6 % (29 of 32) | 0 of 2 | 9 of 9 (100 %) | 20 of 21 (95.2 %) |
| outlier_1 (one corner) | 78.1 % (25 of 32) | 0 of 2 | 7 of 9 (77.8 %) | 18 of 21 (85.7 %) |
| outlier_2 (two corners) | 60.9 % (39 of 64) | 0 of 4 | 5 of 18 (27.8 %) | 34 of 42 (81.0 %) |
| mislabel (two corners) | 65.6 % (42 of 64) | 0 of 4 | 4 of 18 (22.2 %) | 38 of 42 (90.5 %) |
| all four pooled | 70.3 % (135 of 192) | 0 of 12 | 25 of 54 (46.3 %) | 110 of 126 (87.3 %) |

* With **8 or more fitted corners**, the single 30 px outlier is named in **29 of 30** runs. This is a small count: a Wilson 95 % interval for 29 of 30 runs
  is about 83 % to 99 %, so the lower end is below the 90 % target and "met" is a statement about the point estimate on 30 scenes.
* With 7 fitted corners nothing is named by design (leave-one-out was computed in only 4 of those 18 runs, the ones that state all three dimensions).
* Naming is harder when two corners are wrong or two labels are swapped, and clearly worse at 8 fitted corners (22 % to 28 %) than at 9 or more (81 % to 91 %). The prompt of the web reader asks for 8 to 10 corners.
* Good corners that get named: 0.88 % of all good corners; 10 of the 160 runs without an injected error (6.25 % [2.50 %, 10.6 %]) name a corner. In 93.8 % of the runs with an injected corner the worst-fitting fitted point is an injected one (baseline 89.1 %).

## Pictures: full frame and cropped

The suite crops 11 of its 32 scenes so that the principal point is off the centre of the picture; the skill fits a centred camera, so this is a model mismatch on purpose. 34 % of the scenes
being cropped is a property of the suite, not a statistic about real pictures. Medians and rates over all perturbations (final run, `report.md`); the baseline columns come from `BASELINE.md`.

| Picture | Valid input flagged, baseline | Valid input flagged, final | Corner error (% diag), baseline to final | abs ln D/H, baseline to final | Outliers named, final | Leave-one-out computed, final |
| --- | --- | --- | --- | --- | --- | --- |
| Full frame (21 scenes) | 2.38 % (2 of 84 runs) | 5.95 % (5 of 84 runs) | 0.27 to 0.04 | 0.0115 to 0.00165 | 77.0 % | 96.3 % |
| Cropped (11 scenes) | 20.5 % (9 of 44 runs) | 29.5 % (13 of 44 runs) | 0.41 to 0.21 | 0.0246 to 0.0132 | 57.6 % | 92.9 % |

* Cropped pictures are less accurate and are flagged more often than full-frame pictures, before and after I1. The change raises both rates. The likely cause is the centred principal point, but we did not test it (for example by fitting the principal point).
* Bad runs flagged: 100 % of the cropped runs and 96.6 % of the full-frame ones (baseline 100 % and 97.8 %).

## Verdict on the revised acceptance criteria

The criteria are those of [BASELINE.md](BASELINE.md) ("What a better version has to show", rows 1 to 4), which replaced the original ones after round 1 (see [RESULTS-I1.md](RESULTS-I1.md)).

| # | Criterion | Result | Verdict |
| --- | --- | --- | --- |
| 1 | One 30 px outlier on a fitted corner with 8 or more fitted corners: naming at least 90 %, accuracy not worse | Named in 29 of 30 runs (96.7 %; 9 of 9 at 8 fitted, 20 of 21 at 9 or more). Accuracy over all 32 `outlier_30` scenes is clearly better: corner error 0.41 % to 0.04 %, rotation 0.89 to 0.09 degrees, W/H 0.0166 to 0.00172, D/H 0.0237 to 0.00234, part placement 0.0933 to 0.0141 m (all marked `+`) | **Met** |
| 2 | Clean and lightly noisy input (1 to 3 px): accuracy not worse (the paired interval is not clearly worse) | No `-` mark on any accuracy metric of `clean`, `noise_1`, `noise_3`; all intervals include zero. At 3 px the point estimates of W/H and D/H are 14 % and 25 % higher | **Met** as defined; low power |
| 3 | Valid input flagged at most the baseline plus 5 points | 8.59 % to 14.1 % (+5.47 points, interval -0.78 to +13.3). 11 to 18 of the 128 valid-input runs; the limit, 13.59 %, is 17.4 runs. The interval includes zero | **Not met, by 0.5 point** |
| 4 | Really bad runs: recall not lower than the baseline | 98.5 % to 97.9 % by scene (-0.66 points, interval -2.37 to 0.00); per run 70 of 71 to 46 of 47, the same single missed run | **Met** (no clear change) |

Two more checks in `BASELINE.md` are not acceptance criteria of I1 as revised: the contract check (5) is **met**, `box_fit.py` prints exactly one JSON object in 288 of 288 runs (baseline: 0) and the fit inside `compile` agrees
with the standalone fit in 288 of 288; "not slower" (6) is **not shown**, because no like-for-like timing exists.

## Known limitation

**Valid input is sent to review too often, especially on cropped pictures.** In the 128 runs with valid input (`clean`, `noise_1`, `noise_3`, `all_exact`), the quality gate holds 18 for review against 11 in the baseline, 14.1 % against
8.6 %. The limit set beforehand was 13.6 %. The paired interval for the 5.5-point difference runs from -0.8 to +13.3, so it is not a clear loss, but the point estimate does not meet the criterion.
The owner accepted closing I1 with this missed by 0.5 point.

What the runs show (from the stored rows of the two sides; the causes below are read from the gate's own reasons, not from a separate experiment):

* **Where.** 13 of the 18 flagged valid-input runs are on cropped pictures (29.5 % of the cropped runs against 5.95 % of the full-frame ones). The baseline already flagged cropped pictures more often (20.5 % against 2.38 %). The increase over the
  baseline is +4 runs on cropped pictures and +3 on full-frame pictures (8 runs newly flagged, 1 no longer flagged), so the level is concentrated in cropped pictures and the increase is not.
* **Why, for 6 of the 8 newly flagged runs.** They come from two scenes, one cropped and one full frame, that have only 7 fitted corners with one stated dimension. There leave-one-out cannot be computed, and the gate sends the run
  to review with the reason "there is no independent check of the fit". Each of the two scenes appears in three valid-input perturbations (`clean`, `noise_1`, `noise_3`).
* **Why, for the other 2.** Two `noise_3` runs (one cropped, one full frame) end as `high_residual` with an rms of 4.1 and 4.2 px, just over the fit's rms limit.
* **What was not tried.** Asking the reader for more corners, fitting the principal point of a cropped picture, and loosening a limit were not tried in I1, and we do not know how they would change the rates. They would have to be
  measured against this table.

## What these numbers do not show

* **Synthetic only.** Perfect pinhole cameras, our own building blocks, one render style, corners that are the truth plus a model of a reader's mistakes. The mix of mistakes in the suite is not the mix in practice. **No real-picture accuracy has been measured.**
* **No reader in the loop.** The oracle mode feeds corner pixels and stated dimensions directly. Whether a real vision model follows the new prompt (8 to 10 corners, at most one assumed dimension, no corner held out) is untested. Live mode needs the
  owner's go-ahead, an API key and a cost confirmation, and has not been run.
* **Speed is not shown, either way.** The baseline timing was taken on a busy machine and the final one on an idle machine. The compile mean of 13.2 s against 8.0 s must not be read as a speed gain. The rule "time no worse" in the
  [design review](../../docs/design-review-2026-10.md) is therefore not demonstrated for I1. To decide it, run both skills at the same time on the same machine.
* **A wrong stated dimension is not measured on the final code.** The `wrong_anchor_*` perturbations have a recorded run for the baseline only; there is no result for the final code, so the flag rate for a wrong assumed dimension and the effect of a scale check are unknown.
* **Thin statistics.** 32 scenes, so many "no clear change" marks are low power, and the rates for single perturbations rest on 32 runs (a step of 3.1 points per run).
* **No attribution of gains.** The final code differs from the round-1 code in `scripts/` (fingerprints `58ffee64e790b8aa` and `b91477c450ed8ca0`), and the I1 changes (robust loss, leave-one-out, outlier dropping, the quality gate)
  were measured together. This document does not say which change produced which part of the gain.
* **Cause of the cropped-picture rate** is a hypothesis (centred principal point), see above.

## The arms at a glance (point estimates, no intervals)

How the same suite and seed moved across the code states. Round-1 columns come from the compile-only runs of `RESULTS-I1b-arms.md`; they are not a paired comparison with the final run.

| Metric | Baseline | Round 1, two corners held out | Round 1, all corners fitted | Final, all corners fitted |
| --- | --- | --- | --- | --- |
| Corner error (% diag, median) | 0.34 | 0.25 | 0.14 | 0.10 |
| Camera rotation (deg, median) | 1.26 | 1.21 | 0.79 | 0.40 |
| abs ln W/H (median) | 0.0113 | 0.0101 | 0.00474 | 0.00252 |
| abs ln D/H (median) | 0.0152 | 0.0109 | 0.00749 | 0.00308 |
| Part placement (m, median) | 0.0752 | 0.0423 | 0.0337 | 0.0251 |
| Valid input flagged | 8.59 % | 41.4 % | 28.9 % | 14.1 % |
| Really bad runs flagged (per run) | 98.6 % (70 of 71) | 98.7 % (74 of 75) | 97.9 % (47 of 48) | 97.9 % (46 of 47) |
| Injected corners named (fitted ones) | - | 35.1 % | 73.4 % | 70.3 % |
| `outlier_30` corners named | - | 44.8 % (n=29) | 87.5 % | 90.6 % |

## Reproduce

Run the two commands above on the same suite and seed. Side B needs a copy of the skill at the state measured here (the working tree of 2026-10-19 before the I1 commit); a later state of the repository has a different fingerprint and will give
somewhat different numbers. The run folders are git-ignored and are not part of the repository. The harness itself is described in [README.md](README.md).
