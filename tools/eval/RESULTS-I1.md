# I1 results: the honest fit against the frozen baseline

> **Status: round 1, superseded.** This page is the first measurement of I1: the first design (two corners held out of the fit as mandatory
> check corners) against the original acceptance criteria, on the round-1 code. Its verdicts ("Not met", "Met for recall, not for precision")
> belong to that code and those criteria. Later documents supersede them: [RESULTS-I1b-arms.md](RESULTS-I1b-arms.md) measures the round-1 code
> with and without held-out corners, and [RESULTS-I1c.md](RESULTS-I1c.md) is the final measurement of I1 on the revised criteria, with the final verdict and the
> known limitation. The text below is kept as it was.

> **Round 2 note (2026-10-19).** This is the round-1 measurement, kept as it was made with harness 1.0, apart from the notes marked
> "Round 2". Three things changed afterwards. (a) The acceptance criteria below were replaced: withholding two corners as mandatory check
> corners costs accuracy and flags valid input too often, so the independent check is leave-one-out with a robust loss and outlier
> dropping (a held-out `fit: false` corner stays an optional feature); the revised criteria are in `BASELINE.md` ("What a better version has
> to show"). (b) Criterion (2) below is not an acceptance criterion at all: the `all_guess` against `noise_1` gap is true by construction
> (a harness property, see `README.md`). (c) The recall of leave-one-out counted injected corners that were held out as check corners and
> so could never be named; harness 1.1 counts only fitted ones. The 40.6 % below is the harness-1.0 number; the table in criterion (1)
> already shows the fitted-only cut (13 of 29). The check-corner arm that this document asked for is measured in `RESULTS-I1b-arms.md`.

Side-by-side run of the synthetic oracle suite on the engine before I1 (A) and on the engine with the I1 changes in the working
tree (B), made on 2026-10-19 with the harness in this folder. Read `README.md` ("What a synthetic set cannot show") before quoting
any number: every scene is a perfect pinhole box built from our own blocks, the corners are the true ones plus a model of a
reader's mistakes, and only 32 scenes stand behind each interval.

**Short answer.** I1 does what it was built to do on bad input: the output is one JSON object, an independent check exists, a mis-clicked corner
hurts the proportions and the part positions less, and every run with an injected bad corner is sent to review. But two of the four acceptance criteria are **not met** and one is only half met:
leave-one-out names the injected corner in 40.6 % of the runs (target 90 %), accuracy on lightly noisy input is
**worse** (and exact-corner input is flagged far more often), and the gate flags valid input far more often than before (41.4 % against 8.6 %). The details, the causes
we could see in the data and the ones we could not test are below.

## What was run

| Item | A: baseline | B: current |
| --- | --- | --- |
| Skill folder | `output/_eval/baseline/booth-image-to-model` | `output/_eval/current/booth-image-to-model` (a copy of `booth-image-to-model/` taken at 12:53 on 2026-10-19, so that edits by other agents could not change the code during the 42-minute run) |
| State | commit `b377476`, version 2.8.0-rc1 | working tree on top of `b377476`, **not committed**; `SKILL.md` still said 2.8.0-rc1 (no version bump yet) |
| Code fingerprint (scripts and schemas, first 16) | `117671dca65f2c48` (the same as in `BASELINE.md`) | `58ffee64e790b8aa` (the live folder had the same fingerprint when the runs ended at 13:36) |
| Suite and seed | `oracle-v1`, 20261019 | `oracle-v1`, 20261019 |
| Suite fingerprint | `e29d9cff4dbf9908` | `e29d9cff4dbf9908` (equal, and equal to `BASELINE.md`) |
| Scenes / runs | 32 / 288, 0 runs without a usable fit | 32 / 288, 0 runs without a usable fit |
| Capabilities found by `skill_caps.py` | none of the newer ones | scene `fit` key, schema 1.1, per-point `fit`, leave-one-out, robust loss, `check_failed`, `quality_gate` |
| Results | `output/_eval/runs/i1-baseline/results.json` | `output/_eval/runs/i1-current/results.json` |
| Wall time of the run | 2197 s | 2529 s |

Commands (from the repository root):

```
# frozen copy of B (a plain copy of the working tree; the baseline copy already existed, made with git archive b377476)
cp -r booth-image-to-model output/_eval/current/
python tools/eval/run_eval.py oracle --skill-dir output/_eval/baseline/booth-image-to-model --suite oracle-v1 --seed 20261019 --out output/_eval/runs/i1-baseline --jobs 2
python tools/eval/run_eval.py oracle --skill-dir output/_eval/current/booth-image-to-model  --suite oracle-v1 --seed 20261019 --out output/_eval/runs/i1-current  --jobs 2
python tools/eval/compare_results.py --a output/_eval/runs/i1-baseline --b output/_eval/runs/i1-current --out output/_eval/compare-i1.md --pair all_guess:noise_1
```

* The two oracle runs were **started together** with `--jobs 2` each (the documented flow runs them one after the other with
  `--jobs 4`; together they share the same machine load, which is fairer for seconds). The laptop was at 100 % CPU for the whole run because other agents
  were running tests, so all seconds here are indicative.
* Every other table is built from the two `results.json` files with the harness's own `metrics.py`, `stats.py` and `report.py`
  (paired bootstrap over scenes, 2000 resamples, 95 % percentile intervals; `compare_results.py` gives the same kind of table with 1000 resamples).
  The extra cuts (outlier recall by number of fitted points, precision and recall of the gate, principal-point split) come from a throwaway analysis script outside the repository that reads the stored
  rows and `work/` files; the generator is called again only to learn which corners were injected.
* Check that the measurement is sound: the re-run of A reproduced the previously recorded baseline (`output/_eval/runs/baseline-oracle-v1`) row by row
  (0 differences in the accuracy, flag and status fields of all 288 rows).
* The `skill_commit` field of B in `results.json` reads `b377476`; that is only the base commit written in `COMMIT.txt` next to the copy, not a commit of B.

How to read the tables. `A` and `B` are the value of the baseline and of the current skill with its 95 % interval over scenes. `B minus A` is the **paired**
difference (same scene on both sides) with its interval. Mark: `+` clear gain, `-` clear loss, `=` no clear change (the interval of the difference contains zero);
no mark where the metric has no better direction. For the compact table the cell reads `A -> B mark`. Part placement is in metres, `abs ln` is the absolute natural-log error of a proportion, the corner error is a share of the image diagonal.

## Aggregate table, all 288 runs

| Metric | n (A/B) | A baseline | B current | B minus A (paired) | Mark |
| --- | --- | --- | --- | --- | --- |
| Corner error, share of image diagonal (median) | 288/288 | 0.34% [0.28%, 0.38%] | 0.25% [0.18%, 0.35%] | -0.09% [-0.13%, -0.01%] | + |
| Corner error, share of image diagonal (mean) | 288/288 | 1.53% [1.25%, 1.84%] | 1.59% [1.14%, 2.19%] | 0.06% [-0.33%, 0.58%] | = |
| Field-of-view error, degrees (median) | 288/288 | 0.96 [0.79, 1.28] | 1.24 [0.79, 1.86] | 0.28 [-0.14, 0.77] | = |
| Camera rotation error, degrees (median) | 288/288 | 1.26 [0.89, 1.83] | 1.21 [0.68, 1.69] | -0.06 [-0.35, 0.20] | = |
| Absolute ln error of W/H (median) | 288/288 | 0.0113 [0.0089, 0.0144] | 0.0101 [0.00708, 0.0123] | -0.00116 [-0.00413, 0.000696] | = |
| Absolute ln error of D/H (median) | 288/288 | 0.0152 [0.0114, 0.0244] | 0.0109 [0.00629, 0.0176] | -0.00429 [-0.0108, -0.000253] | + |
| Absolute ln error of W/H (mean) | 288/288 | 0.109 [0.0841, 0.137] | 0.123 [0.0738, 0.186] | 0.0142 [-0.0363, 0.0791] | = |
| Absolute ln error of D/H (mean) | 288/288 | 0.112 [0.0891, 0.138] | 0.127 [0.0804, 0.185] | 0.0153 [-0.0355, 0.0752] | = |
| Focal length error, relative (median) | 288/288 | 2.17% [1.60%, 2.69%] | 2.55% [1.72%, 4.44%] | 0.38% [-0.32%, 1.92%] | = |
| Floor-part placement error, metres (median of runs) | 288/287 | 0.0752 [0.0554, 0.0884] | 0.0423 [0.0366, 0.0657] | -0.0329 [-0.042, -0.0106] | + |
| Runs flagged for review | 288/288 | 56.9% [53.8%, 60.8%] | 74.0% [69.4%, 79.2%] | 17.0% [12.8%, 21.9%] |  |
| Flag agrees with truth | 288/288 | 67.0% [62.1%, 71.9%] | 51.4% [45.1%, 57.6%] | -15.6% [-21.9%, -9.03%] | - |
| Really bad runs that are flagged | 71/75 | 98.6% [95.8%, 100.0%] | 98.7% [95.5%, 100.0%] | 0.12% [-0.25%, 0.56%] | = |
| Good runs that are flagged | 217/213 | 43.3% [38.0%, 49.1%] | 65.3% [59.3%, 71.3%] | 21.7% [15.5%, 27.8%] | - |
| Runs with an injected bad corner that are flagged | 128/128 | 95.3% [91.4%, 98.4%] | 100.0% [100.0%, 100.0%] | 4.69% [1.56%, 8.59%] | + |
| Runs with valid input (no injected error, right dimensions) that are flagged | 128/128 | 8.59% [3.12%, 16.4%] | 41.4% [31.2%, 53.1%] | 32.8% [22.7%, 43.8%] | - |
| Runs with no usable fit | 288/288 | 0.00% [0.00%, 0.00%] | 0.00% [0.00%, 0.00%] | 0.00% [0.00%, 0.00%] | = |
| Injected bad corners the skill names | 0/128 | - | 28.1% [19.8%, 37.0%] | - |  |
| Good corners the skill names | 0/288 | - | 4.34% [3.17%, 5.56%] | - |  |
| Runs without injected errors in which the skill names a corner | 0/160 | - | 22.5% [15.0%, 31.2%] | - |  |
| Worst-fitting point is an injected bad corner | 128/128 | 89.1% [83.6%, 94.5%] | 68.8% [57.0%, 79.7%] | -20.3% [-32.0%, -10.2%] | - |
| box_fit.py seconds (median) | 288/288 | 5.85 [5.23, 6.47] | 6.45 [5.54, 7.27] | 0.60 [-0.14, 1.15] | = |
| boothctl compile seconds (median; it fits the camera itself) | 288/288 | 6.80 [6.13, 7.40] | 7.35 [6.49, 8.34] | 0.56 [-0.00, 1.17] | = |
| Compile path seconds (mean) | 288/288 | 7.99 [7.15, 8.87] | 9.18 [7.95, 10.46] | 1.18 [0.28, 2.20] | - |
| box_fit.py prints exactly one JSON object on stdout | 288/288 | 0.00% [0.00%, 0.00%] | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] | + |
| box_fit.py and the fit inside compile agree on W, D and H | 288/288 | 100.0% [100.0%, 100.0%] | 100.0% [100.0%, 100.0%] | 0.00% [0.00%, 0.00%] | = |

What this says in short (all runs, i.e. the mix of the nine perturbations):

* **Improved, clearly:** median corner error 0.34 % to 0.25 %, median D/H error 0.0152 to 0.0109, median part placement 0.075 m to 0.042 m, injected-bad-corner runs flagged 95.3 % to 100 %, `box_fit.py` prints one JSON object (0 % to 100 %). New and useful: independent check numbers exist in every run, and 28.1 % of the injected bad corners are named (nothing was named before), at the price of 4.3 % of the good corners being named too.
* **Not clearly changed:** rotation, field of view and W/H medians, all the means (the tail of very bad runs, mostly swapped labels and wrong assumed dimensions, is as long as before), the share of really bad runs caught (98.6 % to 98.7 %), the fit inside `compile` against the standalone fit (still 100 %), the fit and compile median times (about +0.6 s, interval touching zero).
* **Worse, clearly:** agreement of the flag with the outcome (67.0 % to 51.4 %), good runs that are flagged (43.3 % to 65.3 %), valid input that is flagged (8.6 % to 41.4 %), the mean time of the compile path (+1.2 s), and "worst-fitting point is an injected corner" (89.1 % to 68.8 %; that older metric reads the `points` list, which now also holds the extrapolated check corners, so it is no longer a fair measure of the fit; we did not verify that this is the whole reason).

## By perturbation (medians, A -> B, mark from the paired interval)

| Perturbation | Corner error (% diag) | Rotation error (deg) | abs ln W/H | abs ln D/H | Part placement (m) | Flagged for review | Compile s (median) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| clean | 0.00% -> 0.00% = | 0.00 -> 0.00 - | 0 -> 0 = | 0 -> 0 = | 5.55e-17 -> 5.55e-17 = | 3.12% -> 21.9% | 7.31 -> 7.30 = |
| noise_1 | 0.07% -> 0.11% - | 0.18 -> 0.25 = | 0.00207 -> 0.0044 - | 0.00257 -> 0.00383 = | 0.0285 -> 0.0308 = | 3.12% -> 37.5% | 7.47 -> 7.54 = |
| noise_3 | 0.19% -> 0.28% - | 0.51 -> 0.70 = | 0.00929 -> 0.0135 - | 0.00881 -> 0.012 = | 0.0742 -> 0.093 - | 21.9% -> 84.4% | 7.23 -> 7.72 = |
| all_exact | 0.03% -> 0.03% - | 0.07 -> 0.09 = | 0 -> 0 = | 0 -> 0 = | 0.0109 -> 0.0118 = | 6.25% -> 21.9% | 4.80 -> 4.93 = |
| outlier_30 | 0.41% -> 0.32% = | 0.89 -> 1.20 = | 0.0166 -> 0.0112 + | 0.0237 -> 0.0099 = | 0.0933 -> 0.0437 + | 100.0% -> 100.0% | 7.00 -> 7.90 - |
| outlier_1 | 0.43% -> 0.25% + | 1.25 -> 0.87 = | 0.0145 -> 0.00717 + | 0.0265 -> 0.00971 + | 0.0971 -> 0.0398 + | 81.2% -> 100.0% | 7.02 -> 7.58 = |
| outlier_2 | 0.76% -> 0.38% = | 1.97 -> 1.57 = | 0.0256 -> 0.0237 = | 0.0418 -> 0.0394 = | 0.126 -> 0.111 = | 100.0% -> 100.0% | 6.84 -> 8.40 - |
| mislabel | 9.19% -> 7.22% = | 24.56 -> 21.17 = | 0.505 -> 0.338 = | 0.496 -> 0.214 = | 1.48 -> 0.985 = | 100.0% -> 100.0% | 10.74 -> 17.49 - |
| all_guess | 1.30% -> 1.43% - | 4.49 -> 5.01 - | 0.144 -> 0.144 = | 0.182 -> 0.182 = | 0.305 -> 0.336 = | 96.9% -> 100.0% | 5.04 -> 5.61 - |

Per-perturbation tables with the intervals of A, B and the difference, and with the field-of-view error, the outlier counts and the box_fit seconds, are in `output/_eval/i1-analysis.md`
(a working file; regenerate it from the two result folders if needed).

## Acceptance criteria

### (1) One 30 px outlier: camera and ratio errors fall, and leave-one-out flags it in at least 90 % of the cases. Not met.

`outlier_30` (sigma 0.5 px noise, one corner displaced by exactly 30 px, 32 scenes), medians:

| Metric | A | B | B minus A (95 % interval) | Mark |
| --- | --- | --- | --- | --- |
| Corner error (% diagonal) | 0.41 | 0.32 | -0.09 [-0.24, +0.16] | = |
| Camera rotation error (deg) | 0.89 | 1.20 | +0.31 [-0.40, +0.80] | = |
| Field-of-view error (deg) | 1.60 | 1.87 | +0.27 [-1.08, +1.44] | = |
| abs ln W/H | 0.0166 | 0.0112 | -0.0054 [-0.0120, -0.0006] | + |
| abs ln D/H | 0.0237 | 0.0099 | -0.0138 [-0.0227, +0.0017] | = (point estimate better, interval touches zero) |
| Part placement (m) | 0.0933 | 0.0437 | -0.0496 [-0.0729, -0.0071] | + |

* **Ratio and part errors fall; the camera errors do not clearly.** W/H and the part positions improve with a clear interval, D/H has a better point estimate but an interval that reaches zero, the median corner error is lower
  by a margin the interval cannot separate from zero, and the rotation and field-of-view errors have a *worse* point estimate. The means do not improve either (corner error 0.49 % to 0.63 %, D/H 0.033 to 0.043, rotation 1.40 to 1.89 deg; all intervals contain zero),
  so a tail of runs that the robust loss does not rescue remains. Per scene, B has the lower corner error in 19 of 32 scenes and the lower rotation error in 16 of 32.
  With one displaced corner of 15 to 60 px (`outlier_1`) the picture is better: corner error 0.43 % to 0.25 % (clear), W/H, D/H and part placement clearly better, rotation not clearly different.
  With two displaced corners (`outlier_2`) nothing changes clearly.
* **Leave-one-out names the injected corner in 13 of 32 runs (40.6 %, interval 21.9 % to 59.4 %); the target was 90 %.** For `outlier_1` it is 31.2 % [15.6 %, 46.9 %], for `outlier_2` 17.2 % [7.8 %, 28.1 %] of the injected corners.
  What the data show about why (this is a cut of the stored rows, not a new experiment):

| `outlier_30`, B | Injected corners | Named in `outliers` |
| --- | --- | --- |
| Corner was a fitted one, 6 corners fitted | 11 | 1 |
| Corner was a fitted one, 7 corners fitted | 9 | 4 |
| Corner was a fitted one, 8 corners fitted | 9 | 8 |
| Corner was a check corner (never fitted) | 3 | not nameable by construction; the check error flagged all 3 (over 8 px) |
| All | 32 | 13 |

  Leave-one-out only works with enough corners left over: with 8 fitted corners it names 8 of 9 (89 %, a small count), with 6 or 7 it names 5 of 20. The suite
  gives 6 to 8 fitted corners (7 to 10 clickable points, of which 1 or 2 are check corners), which is also what the web prompt asks for (8 to 10 corners, 2 of them check corners), and `box_fit.py`'s own docstring says that
  with 8 points the check can miss the culprit and that 10 to 12 spread corners name it reliably. The same procedure also names innocent corners: 4.3 % of good corners overall, and
  22.5 % [15.0 %, 31.2 %] of the runs that contain no injected error name at least one corner.
* What does work in this case: the gate sends 32 of 32 `outlier_30` runs to review (the baseline flagged them too, by fit status, without being able to say which corner), and 128 of 128 runs with an injected bad corner (A: 95.3 %).

### (2) Depth/height ratio error with one assumed dimension is lower than with all three assumed. Not an acceptance criterion (Round 2: a harness property, true by construction).

*Round 2: the gap below is set by the harness constant `GUESS_LOG_SIGMA` (about 12 % error on each of three dimensions, rounded to 0.5 m): with all three dimensions fixed by guesses the
proportions inherit the error for any engine, and the baseline gains as much (below). It shows the cost of the reader's habit, not a skill result, and it is removed from the
acceptance list. Only replayed or live reader replies can test whether the changed prompt really gives one stated dimension.*

Same scenes, current code (`all_guess`: W, D, H all stated as assumed values about 12 % off and rounded to 0.5 m, sigma 0.5 px; `noise_1`: one exact dimension, sigma 1 px). In this table the column headed A is `all_guess` and the column headed B is `noise_1`, both from the current skill:

| Metric | n (A/B) | A baseline | B current | B minus A (paired) | Mark |
| --- | --- | --- | --- | --- | --- |
| Absolute ln error of D/H (median) | 32/32 | 0.182 [0.0974, 0.203] | 0.00383 [0.00163, 0.00699] | -0.178 [-0.199, -0.0928] | + |
| Absolute ln error of D/H (mean) | 32/32 | 0.159 [0.122, 0.196] | 0.00877 [0.00442, 0.0145] | -0.151 [-0.189, -0.112] | + |
| Absolute ln error of W/H (median) | 32/32 | 0.144 [0.08, 0.175] | 0.0044 [0.0022, 0.00711] | -0.139 [-0.172, -0.0763] | + |
| Absolute ln error of W/H (mean) | 32/32 | 0.12 [0.0944, 0.145] | 0.00614 [0.00428, 0.00814] | -0.114 [-0.139, -0.0887] | + |
| Corner error, share of image diagonal (median) | 32/32 | 1.43% [1.24%, 1.82%] | 0.11% [0.07%, 0.12%] | -1.32% [-1.72%, -1.13%] | + |
| Camera rotation error, degrees (median) | 32/32 | 5.01 [4.19, 5.98] | 0.25 [0.17, 0.63] | -4.76 [-5.77, -3.82] | + |
| Field-of-view error, degrees (median) | 32/32 | 7.86 [4.05, 10.44] | 0.39 [0.13, 0.56] | -7.46 [-10.16, -3.64] | + |
| Floor-part placement error, metres (median of runs) | 32/32 | 0.336 [0.224, 0.503] | 0.0308 [0.02, 0.0347] | -0.305 [-0.476, -0.195] | + |
| Runs flagged for review | 32/32 | 100.0% [100.0%, 100.0%] | 37.5% [21.9%, 56.2%] | -62.5% [-78.1%, -43.8%] |  |
| Really bad runs that are flagged | 27/1 | 100.0% [100.0%, 100.0%] | 100.0% | 0.00% |  |

* The depth/height error falls from 0.182 to 0.0038 (median) and from 0.159 to 0.0088 (mean), with intervals far from zero; corner error, rotation, scale of the parts and the review rate improve with it.
  `noise_1` has twice the pixel noise of `all_guess`, so the comparison is, if anything, unfair against the one-dimension case.
* Against the baseline as a whole (A `all_guess`, which is what the web reader did, to B `noise_1`) the gain is the same size (D/H 0.182 to 0.0038, interval [-0.199, -0.093]).
* Honest limits: the baseline engine gains just as much when it is fed one dimension (A `all_guess` to A `noise_1`: D/H 0.182 to 0.0026), so this is a property of the input, not of the I1 fit code. The change I1 actually makes here is in
  the reader's prompt and schema (state one dimension, not three), and the oracle mode feeds the dimensions directly and cannot test the reader. A replay or live run is needed to show that the prompt really produces one dimension.
  With three assumed dimensions the B engine is also slightly *worse* than A on camera numbers (`all_guess`: rotation 4.49 to 5.01 deg, +0.52 [0.02, 1.45]; field of view 2.96 to 7.86 deg, +4.90 [0.86, 7.46]; corner error 1.30 % to 1.43 %); the proportions are identical because the assumed values fix them.

### (3) Clean inputs are not worse. Not met.

Medians, A -> B (rest of the columns in the compact table above):

| Perturbation | Corner error (% diag) | abs ln W/H | Part placement (m) | Flagged for review |
| --- | --- | --- | --- | --- |
| `clean` (exact corners) | 0.00 -> 0.00 = | 0 -> 0 = | 0 -> 0 = | 3.1 % -> 21.9 % |
| `all_exact` (three exact dimensions) | 0.03 -> 0.03 - (the difference is at most 0.02 points) | 0 -> 0 = | 0.0109 -> 0.0118 = | 6.3 % -> 21.9 % |
| `noise_1` (1 px) | 0.07 -> 0.11 - | 0.00207 -> 0.00440 - | 0.0285 -> 0.0308 = | 3.1 % -> 37.5 % |
| `noise_3` (3 px) | 0.19 -> 0.28 - | 0.00929 -> 0.0135 - | 0.0742 -> 0.0930 - | 21.9 % -> 84.4 % |

* **Exact corners:** no clear change in any accuracy metric (the `-` mark on the clean rotation error in the per-perturbation tables is a median of 0.00007 against 0.00010 degrees, a numerical difference; one scene differs by up to 0.6 degrees).
  But 7 of 32 exact-corner runs (A: 1) go to review. All 7 are among the 11 scenes whose picture is a crop with the principal point off the centre (7 of 11 cropped scenes flagged, 0 of 21 uncropped): the fit itself has 0.3 to 6 px rms, yet the independent numbers disagree with it (check corners, leave-one-out or named outliers; one scene has a check corner 64 px off at 0.3 px rms),
  which is plausibly the independent check doing its job on a model mismatch that the harness built on purpose (the skill fits a centred camera), but the harness's own outcome label counts those runs as good.
* **Noisy corners:** a clear loss at 1 px and 3 px for the corner error and for W/H (noise_1: corner error +0.04 [0.01, 0.05], W/H +0.0023 [0.00004, 0.0050]; noise_3: +0.09 [0.04, 0.15], +0.0042 [0.0002, 0.0105], placement +0.019 m [0.0007, 0.040]); D/H and rotation are not clearly different.
  A plausible cause that we did **not** test in round 1: B fits 6 to 8 corners where A fits 7 to 10, because two corners are held out, and the harness hands the same noisy points to both. Separating this needs an arm with the check corners fitted. *Round 2: `run_eval.py oracle --check-corners 0` is that arm; see `RESULTS-I1b-arms.md`.*
* **Time:** the median is within noise (fit 5.85 s to 6.45 s, compile 6.80 s to 7.35 s; intervals contain zero), but the mean of the compile path is clearly slower (7.99 s to 9.18 s, +1.18 [0.28, 2.20]) and swapped-label runs cost about 6.8 s more (10.7 s to 17.5 s), as do (less) the displaced-corner runs (+0.9 s for `outlier_30`).
  Seconds were measured with the two sides running together on a loaded machine.

### (4) The quality gate flags bad fits. Met for recall, not for precision.

"Bad" is the harness convention of `metrics.py` (corner error above 1.5 % of the diagonal, or W/H or D/H off by more than 0.10 in absolute ln), decided on the outcome. "Flagged" is the `quality_gate` for B and `box_fit` status other than `fitted` for A.

| Skill | Runs | TP | FP | FN | TN | Precision | Recall | Specificity | Accuracy | F1 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A baseline | 288 | 70 | 94 | 1 | 123 | 42.7% [36.8%, 49.1%] | 98.6% [95.8%, 100.0%] | 56.7% [50.9%, 62.0%] | 67.0% [62.1%, 71.9%] | 0.596 |
| B current | 288 | 74 | 139 | 1 | 74 | 34.7% [27.3%, 43.2%] | 98.7% [95.5%, 100.0%] | 34.7% [28.7%, 40.7%] | 51.4% [45.1%, 57.6%] | 0.514 |

* **Recall (really bad runs that are flagged): 98.7 % [95.5 %, 100 %]**, the same as A (98.6 %). One of 75 bad runs is missed (`synth_0017`, `noise_3`: fitted, check corners 2.9 px off, leave-one-out 3.7 px, yet D/H is off by 0.157 in ln).
* **Precision (flagged runs that are really bad): 34.7 % [27.3 %, 43.2 %]**, lower than A (42.7 % [36.8 %, 49.1 %]). The gate flags 213 of 288 runs (74.0 %; A: 56.9 %). Specificity falls from 56.7 % to 34.7 %.
* The outcome label is unkind to both sides in the `outlier_*` rows: a run with a mis-clicked corner whose fit is nonetheless accurate is "good" by outcome, yet a review is the right answer. Using **input defect** as the label instead
  (an injected bad corner or three assumed dimensions; 160 defective, 128 valid runs):

| Skill | Defective runs | Valid runs | Precision | Recall | Specificity (valid not flagged) |
| --- | --- | --- | --- | --- | --- |
| A baseline | 160 | 128 | 93.3% [88.1%, 97.5%] | 95.6% [92.5%, 98.1%] | 91.4% [83.6%, 96.9%] |
| B current | 160 | 128 | 75.1% [70.2%, 80.0%] | 100.0% [100.0%, 100.0%] | 58.6% [46.9%, 68.8%] |

  Recall rises (95.6 % to 100 %), but precision falls (93.3 % to 75.1 %) and valid input is flagged much more often (specificity 91.4 % to 58.6 %). In plain terms: 41.4 % [31.2 %, 53.1 %] of the valid runs (clean, 1 px, 3 px, exact dimensions) would stop the one-click chain
  and ask for "Render anyway" (A: 8.6 %). By perturbation:

| Perturbation | A flagged | A bad | A both | B flagged | B bad | B both |
| --- | --- | --- | --- | --- | --- | --- |
| clean | 1/32 | 0/32 | 0 | 7/32 | 1/32 | 1 |
| noise_1 | 1/32 | 0/32 | 0 | 12/32 | 1/32 | 1 |
| noise_3 | 7/32 | 1/32 | 0 | 27/32 | 2/32 | 1 |
| outlier_30 | 32/32 | 3/32 | 3 | 32/32 | 5/32 | 5 |
| outlier_1 | 26/32 | 2/32 | 2 | 32/32 | 4/32 | 4 |
| outlier_2 | 32/32 | 7/32 | 7 | 32/32 | 10/32 | 10 |
| mislabel | 32/32 | 31/32 | 31 | 32/32 | 24/32 | 24 |
| all_exact | 2/32 | 0/32 | 0 | 7/32 | 1/32 | 1 |
| all_guess | 31/32 | 27/32 | 27 | 32/32 | 27/32 | 27 |

* Where the reviews come from (B): in 112 runs the gate lists "corners that disagree with the rest of the fit" (the leave-one-out outlier names), 61 runs end as `check_failed` (A has no such status), 139 as `high_residual` (A: 149), 13 as `ambiguous` (A: 15), 75 as `fitted` (A: 124).
  The gate limits (8 px median check/leave-one-out, 15 px and 20 px worst) look tight for fits of only 6 to 8 corners at 1 to 3 px noise; this is our reading of the numbers, not a tested change.

## Harness and I1 code status

* **Harness:** worked as documented. 288 rows per side, 0 runs without a usable fit, `compile` valid in 288 of 288 runs on both sides, reproducible baseline (see above). Weak points to keep in mind: only 32 scenes (wide intervals, several "no clear change" marks are
  simply low power), the outcome-based "really bad" label, the legacy "worst-fitting point" metric that now sees check corners, and no arm that separates "two corners held out" from "robust loss" (see (3)).
* **I1 contract as implemented in the copy measured:** met in the output. `box_fit.py` prints exactly one JSON object in 288 of 288 runs (A: 0), carries `loss`, `n_fit`, `n_check`, `check_points`, check and leave-one-out numbers and `outliers`, has the `check_failed` status, and honours `fit: false`;
  the scene description with `fit` and schema 1.1 compiled in every run; `compile_report.json` carries `quality_gate` in every run (75 pass, 213 review) and the fit inside `compile` agrees with the standalone fit in 100 % of runs.
  Five `mislabel` runs compile as `partial` against three in A; swapped labels do that, and it is not a contract problem.
* **Not complete at the time of the copy:** the version in `SKILL.md` was still 2.8.0-rc1 (the plan names 2.9.0-rc1), and nothing is committed. The web part of the contract (studio stops after the model step on `review`, "Render anyway") is outside this harness and was not measured.

## What to decide, and what to try (hypotheses, none of them tested here)

1. Criterion (1) fails on this suite mainly because leave-one-out has too little redundancy with 6 to 7 fitted corners. Options: ask readers for 10 or more corners (so that 8 or more remain after the two checks), report `outliers` only when enough redundancy remains, or lower the target for 8-corner readers. Check with a suite variant that has more clickable points.
2. Criterion (3) and the false alarms on valid input need a cause before a fix. Cheapest first step: add an arm to `run_eval.py` that sends the check corners as fitted and compare B with it, which separates robust loss from fewer fitted points. *(Done in round 2: `--check-corners 0`, results in `RESULTS-I1b-arms.md`.)* Then look at the gate limits for 6 to 8 fitted corners and at the principal-point mismatch of cropped pictures (7 of 11 cropped exact-corner scenes are flagged).
3. Criterion (2) needs a reader-level test (replay of saved replies or a live run) to show that the changed prompt really gives one stated dimension; the engine alone does not decide it.
4. The cost of the compile path rose by about 1.2 s on average and 6.8 s on swapped labels; if "not slower" is a requirement, leave-one-out is where the time goes (each held-out corner is a refit; up to three rounds for outliers).
