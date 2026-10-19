# I1b: does holding corners out of the fit cost accuracy?

> **Status (2026-10-19): round-1 code.** Everything on this page was measured on the round-1 code of I1 (code fingerprint
> `58ffee64e790b8aa`), not on the final code. It is kept because it is the evidence for a decision: the default independent check
> changed from "hold two corners out" to "fit every corner and use leave-one-out". The final measurement, on the code that was
> committed, is in [RESULTS-I1c.md](RESULTS-I1c.md); the first round is in [RESULTS-I1.md](RESULTS-I1.md).

Read [README.md](README.md) ("What a synthetic set cannot show") before quoting any number: synthetic scenes, perfect pinhole cameras, corners
that are the truth plus a model of a reader's mistakes, 32 scenes behind each interval, and no real picture measured.

**Short answer.** Yes, holding two corners out of the fit cost accuracy and raised the false review flags. On the same code, the arm that fits every
corner was clearly more accurate than the arm that holds two out (median corner error 0.25 % to 0.14 % of the image diagonal, camera
rotation 1.21 to 0.79 degrees, part placement 0.042 to 0.034 m), it flagged valid input less often (41.4 % to 28.9 %, against 8.59 % for the baseline), and it named
the injected corner about twice as often (35.1 % to 73.4 %). At 1 and 3 px of noise the held-out arm was clearly worse than the baseline, and the all-fitted arm was not. That is why the default was changed.
The all-fitted arm still flagged valid input well above the baseline plus 5 points (28.9 %), which is what round 2 worked on (final result: 14.1 %, see `RESULTS-I1c.md`).

## What was run

Three oracle runs on `oracle-v1`, seed 20261019, harness 1.1.0, 32 scenes x 9 perturbations = 288 runs each, 0 runs without a usable fit.
The suite fingerprint is `e29d9cff4dbf9908` on all three. They differ in the skill and in the arm switch `--check-corners`
(see [README.md](README.md), "Modes"):

| Side | Skill | Corners held out of the fit | Results (git-ignored) |
| --- | --- | --- | --- |
| baseline | frozen copy of commit `b377476`, fingerprint `117671dca65f2c48` | 2 (not used: the baseline fits every corner it is given) | `output/_eval/runs/i1b-baseline` |
| held-out-2 | round-1 code (copy of the working tree, version string still 2.8.0-rc1), fingerprint `58ffee64e790b8aa` | 2: the case's own check corners are sent with `fit: false` | `output/_eval/runs/i1b-check2` |
| all-fitted | the same round-1 code | 0: every corner is fitted | `output/_eval/runs/i1b-check0` |

```
python tools/eval/run_eval.py oracle --skill-dir output/_eval/baseline/booth-image-to-model --suite oracle-v1 --seed 20261019 --jobs 1 --check-corners 2 --stages compile --out output/_eval/runs/i1b-baseline
python tools/eval/run_eval.py oracle --skill-dir output/_eval/current/booth-image-to-model  --suite oracle-v1 --seed 20261019 --jobs 1 --check-corners 2 --stages compile --out output/_eval/runs/i1b-check2
python tools/eval/run_eval.py oracle --skill-dir output/_eval/current/booth-image-to-model  --suite oracle-v1 --seed 20261019 --jobs 1 --check-corners 0 --stages compile --out output/_eval/runs/i1b-check0
python tools/eval/arms_report.py --side baseline=output/_eval/runs/i1b-baseline --side held-out-2=output/_eval/runs/i1b-check2 --side all-fitted=output/_eval/runs/i1b-check0 --out output/_eval/arms-tables.md
python tools/eval/compare_results.py --a output/_eval/runs/i1b-baseline --b output/_eval/runs/i1b-check0 --out output/_eval/compare-check0.md
python tools/eval/compare_results.py --a output/_eval/runs/i1b-baseline --b output/_eval/runs/i1b-check2 --out output/_eval/compare-check2.md
```

* Only the `compile` stage was run (`--stages compile`), one process at a time (`--jobs 1`), so there are no `box_fit.py` timings or contract checks for these arms. The accuracy, flag and naming
  numbers come from the fit inside `compile`, which is the production path.
* The harness hands the same perturbed pixels, injected errors and stated dimensions to all three sides (the random stream does not depend on the arm), so the sides are paired by scene and perturbation.
* The baseline side reproduces the recorded baseline of [BASELINE.md](BASELINE.md): 0 differences in 2,880 values (corner error, field-of-view error, both ln proportion errors, the flag, the fit and compile
  statuses and the fitted W, D and H of all 288 runs). Only the seconds differ.
* Marks: `+` clear gain, `-` clear loss, `=` no clear change (the paired 95 % interval of the later side minus the earlier one contains zero), blank: no better direction.

## All runs, three sides

Medians over all 288 runs (decision rows: rates over scenes).

| Metric | baseline | held-out-2 | all-fitted | held-out-2 minus baseline | all-fitted minus baseline | all-fitted minus held-out-2 |
| --- | --- | --- | --- | --- | --- | --- |
| Corner error, share of image diagonal (median) | 0.34% | 0.25% | 0.14% | -0.09% [-0.13%, -0.01%] + | -0.20% [-0.23%, -0.15%] + | -0.11% [-0.16%, -0.07%] + |
| Camera rotation error, degrees (median) | 1.26 | 1.21 | 0.79 | -0.06 [-0.37, 0.19] = | -0.47 [-0.75, -0.25] + | -0.42 [-0.71, -0.17] + |
| Field-of-view error, degrees (median) | 0.96 | 1.24 | 0.63 | 0.28 [-0.13, 0.76] = | -0.33 [-0.53, -0.19] + | -0.61 [-1.13, -0.23] + |
| Absolute ln error of W/H (median) | 0.0113 | 0.0101 | 0.00474 | -0.00116 [-0.00413, 0.000686] = | -0.00652 [-0.00892, -0.0048] + | -0.00535 [-0.0074, -0.00287] + |
| Absolute ln error of D/H (median) | 0.0152 | 0.0109 | 0.00749 | -0.00429 [-0.0106, -0.000575] + | -0.00774 [-0.014, -0.00489] + | -0.00345 [-0.00817, 5.07e-05] = |
| Floor-part placement error, metres (median of runs) | 0.0752 | 0.0423 (n=287) | 0.0337 | -0.0329 [-0.0417, -0.0104] + | -0.0415 [-0.0514, -0.0243] + | -0.00863 [-0.0269, -0.00228] + |
| Runs flagged for review | 56.9% | 74.0% | 68.4% | 17.0% [12.5%, 21.5%] | 11.5% [7.99%, 14.9%] | -5.56% [-9.03%, -2.43%] |
| Really bad runs that are flagged | 98.6% (n=71) | 98.7% (n=75) | 97.9% (n=48) | 0.12% [-0.27%, 0.60%] = | -0.63% [-2.11%, 0.00%] = | -0.76% [-2.34%, 0.00%] = |
| Flagged runs whose fit is really bad (precision) | 42.7% (n=164) | 34.7% (n=213) | 23.9% (n=197) | -7.94% [-15.3%, 0.28%] = | -18.8% [-23.1%, -15.0%] - | -10.9% [-18.9%, -4.36%] - |
| Runs with valid input (no injected error, right dimensions) that are flagged | 8.59% (n=128) | 41.4% (n=128) | 28.9% (n=128) | 32.8% [22.7%, 43.0%] - | 20.3% [13.3%, 28.1%] - | -12.5% [-20.3%, -5.47%] + |
| Runs with an injected bad corner that are flagged | 95.3% (n=128) | 100.0% (n=128) | 100.0% (n=128) | 4.69% [1.56%, 8.59%] + | 4.69% [1.56%, 8.59%] + | 0.00% [0.00%, 0.00%] = |
| Injected bad corners that were fitted and that the skill names (recall) | - | 35.1% (n=118) | 73.4% (n=128) | - | - | 38.4% [29.2%, 47.6%] + |
| Compile path seconds (mean) | 13.17 | 17.15 | 16.55 | 3.98 [1.75, 5.92] | 3.37 [1.56, 5.50] | -0.61 [-2.25, 1.08] |

The seconds are not comparable (see "What this does not show"). The "n" of the recall row is the number of runs that contain an injected corner that was fitted in that arm: in the held-out arm, 10 of the 128 runs
with an injected error had every injected corner held out as a check corner (leave-one-out cannot name a corner it never fitted), so they are left out of the recall.

## By perturbation

Medians; each cell reads `baseline / held-out-2 / all-fitted`, and the two marks are the held-out-2 side against the baseline and the all-fitted side against the baseline (paired intervals). The `-` on the clean rotation error is a median of
0.00007 against 0.00010 degrees, a numerical difference (see `RESULTS-I1.md`).

| Perturbation | Corner error (% diag) | Rotation (deg) | abs ln W/H | abs ln D/H | Flagged for review |
| --- | --- | --- | --- | --- | --- |
| clean | 0.00 / 0.00 / 0.00 = = | 0.00 / 0.00 / 0.00 - = | 0 / 0 / 0 = = | 0 / 0 / 0 = = | 3.12% / 21.9% / 9.38% |
| noise_1 (1 px) | 0.07 / 0.11 / 0.07 - = | 0.18 / 0.25 / 0.18 = = | 0.00207 / 0.0044 / 0.00221 - = | 0.00257 / 0.00383 / 0.00232 = = | 3.12% / 37.5% / 21.9% |
| noise_3 (3 px) | 0.19 / 0.28 / 0.20 - = | 0.51 / 0.70 / 0.58 = = | 0.00929 / 0.0135 / 0.00902 - = | 0.00881 / 0.012 / 0.011 = = | 21.9% / 84.4% / 68.8% |
| outlier_1 | 0.43 / 0.25 / 0.10 + + | 1.25 / 0.87 / 0.42 = + | 0.0145 / 0.00717 / 0.00347 + + | 0.0265 / 0.00971 / 0.00504 + + | 81.2% / 100% / 100% |
| outlier_2 | 0.76 / 0.38 / 0.36 = + | 1.97 / 1.57 / 1.24 = = | 0.0256 / 0.0237 / 0.0145 = = | 0.0418 / 0.0394 / 0.0235 = = | 100% / 100% / 100% |
| outlier_30 | 0.41 / 0.32 / 0.15 = + | 0.89 / 1.20 / 0.53 = = | 0.0166 / 0.0112 / 0.00527 + + | 0.0237 / 0.0099 / 0.00737 = + | 100% / 100% / 100% |
| mislabel | 9.19 / 7.22 / 0.43 = + | 24.56 / 21.17 / 2.39 = + | 0.505 / 0.338 / 0.0175 = + | 0.496 / 0.214 / 0.0214 = + | 100% / 100% / 100% |

Naming of the one 30 px outlier (`outlier_30`), share of the injected corners that were fitted and that the skill names, by the number of fitted points of each side (the held-out side has 2 fewer points than the all-fitted side for the
same scene):

| Side | 6 or fewer fitted | 7 fitted | 8 fitted | 9 or more fitted | All |
| --- | --- | --- | --- | --- | --- |
| held-out-2 | 9.09 % (n=11) | 44.4 % (n=9) | 88.9 % (n=9) | none in the suite | 44.8 % (n=29) |
| all-fitted | none in the suite | 0.00 % (n=2) | 88.9 % (n=9) | 95.2 % (n=21) | 87.5 % (n=32) |

## Reading

* **Holding corners out costs accuracy.** On the same code, fitting every corner beats holding two out in the all-runs medians (corner error, rotation, field of view, W/H and part placement are clear gains, D/H is a gain whose interval
  touches zero). The likely mechanism is that with 8 clicked corners only 6 are fitted, so the same unknowns are fitted from fewer points; we did not test it separately.
* **It was worse than the baseline on lightly noisy input.** With two corners held out, `noise_1` and `noise_3` have a clearly higher corner error and W/H error than the baseline (marks `-`). With every corner fitted
  the loss is gone (marks `=`). This was the most important result: a change must not cost accuracy where the input is already good.
* **Held-out corners raised the false review flags.** Valid input flagged: 8.59 % (baseline), 41.4 % (held-out-2), 28.9 % (all-fitted); by perturbation, `noise_3` 21.9 %, 84.4 %, 68.8 %. Fitting every corner removes
  about 12.5 points of the false flags (interval -20.3 to -5.5) but is still 20.3 points above the baseline. Round 2 worked on that.
* **Naming improves a lot.** The injected corner is named in 73.4 % of the runs of the all-fitted arm against 35.1 % for the held-out arm. Two things work together and we did not separate them: leave-one-out has more equations left when
  nothing is held out, and a corner that is held out cannot be named (in the held-out arm 38 of the 128 runs with an injected error had at least one injected corner held out, 10 of them their only one). Those 38 runs were flagged every time, but only 9 ended as `check_failed`
  (23.7 %). Naming still depended on the number of fitted points: in the all-fitted arm 88.9 % with 8 fitted corners and 95.2 % with 9 or more for `outlier_30`, but 0 of 2 with 7. The final code computes leave-one-out only when
  enough equations are left.
* **On bad input the all-fitted arm gains much more than the held-out arm.** Against the baseline, the all-fitted arm is clearly better in corner error for `outlier_1`, `outlier_2`, `outlier_30` and `mislabel`; the held-out arm is clearly better only for the
  corner error of `outlier_1` and for W/H of `outlier_1` and `outlier_30`, and shows no clear change in the rest. The largest single effect is the swapped labels (rotation 24.56 degrees in the baseline, 21.17 held-out, 2.39 all-fitted).
* **Really bad runs stay flagged** in all three (98.6 %, 98.7 %, 97.9 %; no clear change).
* **Decision taken.** The independent check by held-out corners became an optional feature (`fit: false` stays in the engine), and the default became "fit every corner, check with leave-one-out under a robust loss, drop named outliers". The reader's prompt
  and the route in `SKILL.md` no longer ask for check corners.

## What this does not show

* **Round-1 code only.** The final code differs (fingerprint `b91477c450ed8ca0`); read `RESULTS-I1c.md` for the state that was committed.
* **Seconds are not comparable.** The three runs were made one at a time on a busy machine whose load was not controlled (the baseline's compile mean was 13.17 s here against 9.48 s in its recorded run). Whether the
  held-out or the all-fitted arm is faster is not decided (all-fitted minus held-out-2: -0.61 s, interval -2.25 to +1.08).
* **Synthetic only, 32 scenes.** Several `=` marks are low power. The mix of mistakes in the suite is not the mix in practice, and the cropped scenes (principal point off centre, 11 of 32) are part of every number above.
* **A wrong stated dimension is not part of these three runs.** Its flag rate and the effect of a scale check are unmeasured on the round-1 code and on the final code.
