# I2 results: booth type and open sides, read first and used as checks

> **Status (2026-10-19).** This is the final measurement of I2 and the document that carries its verdict. I2 is closed with a
> modest measured effect. With I2 the gate sends 50.0 % of the runs with a wrong open side to review (the frozen baseline: 3.1 %) and
> 46.9 % of the runs with a booth turned half way round (3.1 %); the topology checks alone account for 40.6 and 37.5 points of those
> differences. They add no review flag to correct input in 288 runs and change no accuracy number. The 100 % on mirrored pictures is not
> new: the old fit already flagged all of them. Acceptance criterion 1 was worded loosely and has two readings; one of them is met and
> one is not (see [the verdict](#verdict-on-the-four-acceptance-criteria)).

Read [README.md](README.md) ("What a synthetic set cannot show" and "Booth grammar (I2)") before quoting any number. Every scene is a
perfect pinhole box built from our own blocks, the corners are the true ones plus a model of a reader's mistakes, and 32 scenes stand
behind each interval. **No real picture has been measured, and no reader (a vision model) took part: the harness hands the skill the
booth type and the open sides directly, as a reader that got them right or wrong in a known way would.**

**Words used here.** The *gate* is the quality gate of the compiler: it ends in `pass` or `review`. A run is *flagged* when the gate
says `review`. A *topology reason* is a reason in the gate that comes from the booth type and the open sides (a wall on an open side, a
type that does not fit the sides, a camera that looks in through closed sides). The *best possible* catch of a perturbation is the
share of its runs that the topology checks could catch at all, even with a perfect fit and the true camera; it is a property of the
suite, not of the skill.

**Short answer.** In I2 the reader states the booth type and the open sides first, and the compiler checks the rest against them.
On this synthetic suite:

* A side wrongly declared open or closed (`wrong_open_sides`) is flagged in 16 of 32 runs, against 1 of 32 for the frozen baseline.
  The topology checks alone add 13 of those (40.6 points, interval 25.0 to 59.4). All 13 are cases where a closed side was declared
  open and the scene draws a wall there. The other direction (an open side declared closed) is almost never noticed: 1 of 19, and
  that run was flagged anyway.
* A booth turned half way round (`swap_fb_lr`, front and back and left and right swapped together) is flagged in 15 of 32 runs, against
  1 of 32. The topology checks alone add 12. The fit cannot see this mistake, because the turned labels fit the picture as well as the
  right ones.
* Mirrored pictures (front and back swapped alone, or left and right alone) were already flagged in all 64 runs by the fit, before I2.
  The topology adds nothing there.
* On correct input the topology raised no review flag in 288 runs, and every accuracy median is exactly as in the I1 final run.
* A wall on a side the reader declared open is gone from the compiled spec: 13 of 128 runs before, 0 now.

Net effect: modest. About half of the runs with a wrong open side, and a similar share of the half-turned ones, are now sent to review,
at no measured cost in false alarms or accuracy. Whether a real reader makes these mistakes, or states the open sides correctly, is not
known.

## What was run

Three groups of runs on one machine, one after the other, from one script (`output/_eval/run_i2_measure.sh`), after the unit-test suites
and before the I2 commit:

| Group | Skill | Perturbations | Runs | Stages | Wall time | Results (git-ignored) |
| --- | --- | --- | --- | --- | --- | --- |
| A | the frozen baseline (commit `b377476`, version 2.8.0-rc1, code fingerprint `117671dca65f2c48`) | `mirror_fb`, `mirror_lr`, `swap_fb_lr`, `wrong_open_sides` | 128 | `compile` | 403.2 s | `output/_eval/runs/i2-baseline-grammar` |
| B | the I2 snapshot (below) | the same four | 128 | `compile` | 465.8 s | `output/_eval/runs/i2-grammar` |
| C | the I2 snapshot | the nine default perturbations of `oracle-v1` | 288 | `fit` and `compile` | 1267.9 s | `output/_eval/runs/i2-check0` |

For the comparison with C there is the I1 final run (`output/_eval/runs/i1c-check0`, 288 runs, 1123.7 s, harness 1.1.0), which is
the run behind [RESULTS-I1c.md](RESULTS-I1c.md).

| Item | Value |
| --- | --- |
| Suite, seed | `oracle-v1`, 20261019; 32 scenes; suite fingerprint `e29d9cff4dbf9908` on every side |
| Harness | 1.2.0 for A, B and C. The I1 final run was made with 1.1.0; its stored results were not touched. `compare_results.py` accepts that pair, because 1.2.0 only added metrics and perturbations (README, "Harness versions"; not re-checked for this document) |
| Arm | 0: every corner is fitted, no check corner withheld (as in the I1 final run); `--jobs 4`; no scale check |
| I2 snapshot | A plain copy of the working tree taken on 2026-10-19 before the I2 commit (`output/_eval/i2/booth-image-to-model`), without its `tests` and `validation` folders. Code fingerprint (scripts and schemas, first 16) `99dc5b7fbdd9d7c7`; its version string still read 2.9.0-rc1 (any bump comes with the commit) |
| I1 final side | `output/_eval/current2/booth-image-to-model`, fingerprint `b91477c450ed8ca0`, the side B of [RESULTS-I1c.md](RESULTS-I1c.md) |
| What differs between the two | Exactly two files in `scripts/` and `schemas/`, compared by hash: `scripts/draft_compile.py` and `schemas/scene-description.v1.json`. `box_fit.py` is byte for byte the same. After the runs the working tree's `scripts/` and `schemas/` were again identical to the snapshot (checked on 2026-10-19), so these numbers describe the repository's current engine |
| Machine | A Windows 10 laptop, 4-core CPU, Python 3.12.3, numpy 1.26.4. The load on the machine during the runs was not recorded (the I1 final run was made on an idle machine). The I2 changes and the harness 1.2.0 code were uncommitted (working tree) at the time |

Commands, from the repository root (the same as in `run_i2_measure.sh` and in the README section "Measuring I2", except that the
script leaves out the `tests` and `validation` folders when it copies the skill):

```
python -c "import shutil; shutil.copytree('booth-image-to-model', 'output/_eval/i2/booth-image-to-model', ignore=shutil.ignore_patterns('__pycache__','tests','validation'))"
python tools/eval/run_eval.py oracle --skill-dir output/_eval/baseline/booth-image-to-model --suite oracle-v1 --seed 20261019 --perturbations mirror_fb mirror_lr swap_fb_lr wrong_open_sides --check-corners 0 --stages compile --jobs 4 --out output/_eval/runs/i2-baseline-grammar
python tools/eval/run_eval.py oracle --skill-dir output/_eval/i2/booth-image-to-model --suite oracle-v1 --seed 20261019 --perturbations mirror_fb mirror_lr swap_fb_lr wrong_open_sides --check-corners 0 --stages compile --jobs 4 --out output/_eval/runs/i2-grammar
python tools/eval/run_eval.py oracle --skill-dir output/_eval/i2/booth-image-to-model --suite oracle-v1 --seed 20261019 --check-corners 0 --jobs 4 --out output/_eval/runs/i2-check0
python tools/eval/compare_results.py --a output/_eval/runs/i2-baseline-grammar --b output/_eval/runs/i2-grammar --out output/_eval/compare-i2-grammar.md
python tools/eval/compare_results.py --a output/_eval/runs/i1c-check0 --b output/_eval/runs/i2-check0 --out output/_eval/compare-i2-default.md
```

The comparisons pair the rows by scene and perturbation. `B minus A` is the paired difference with a 95 % percentile interval from
1000 resamples of whole scenes. Marks: `+` clear gain, `-` clear loss, `=` no clear change.

**How to read the comparison with the baseline.** Side A of the grammar comparison is the frozen baseline (2.8.0-rc1), which does not have
the I1 changes either. So a difference in "flagged for any reason" mixes I1 and I2. To isolate I2, this document uses the number of runs
**flagged only by topology reasons**: runs that the old gate would have passed and that only a topology reason sent to review. The
rows of the two groups are in the stored `results.json` files; counts below that are not in the harness tables were taken from those
rows for this document.

## The grammar perturbations (groups A and B)

Four ways in which the topology is wrong, all with 0.5 px of corner noise and one exact anchor dimension (README, "Booth grammar (I2)"):
`mirror_fb` (front and back swapped in every corner label), `mirror_lr` (left and right swapped), `swap_fb_lr` (both together, a half turn)
and `wrong_open_sides` (one side declared the wrong way round, with the booth type changed to match). On these runs the harness counts
every run as one that must be reviewed, so there is no false-alarm number here; false alarms are measured on the default suite below.
`swap_fb_lr` is not one of the three perturbations of criterion 1: it was added during I2, after a first finding by the skill work, and is
shown apart.

| Perturbation | Runs | Baseline flagged | I2 flagged | I2: with a topology reason | I2: only by topology | Best possible for topology |
| --- | --- | --- | --- | --- | --- | --- |
| `mirror_fb` | 32 | 32 (100 %) | 32 (100 %) | 0 | 0 | not defined |
| `mirror_lr` | 32 | 32 (100 %) | 32 (100 %) | 2 (6.25 %) | 0 | not defined |
| `wrong_open_sides` | 32 | 1 (3.12 %) | 16 (50.0 %) | 14 (43.8 %) | 13 (40.6 %) | 14 (43.8 %) |
| the three of criterion 1, pooled | 96 | 65 (67.7 %), interval 66.7 to 69.8 | 80 (83.3 %), interval 77.1 to 89.6 | 16 (16.7 %) | 13 (13.5 %), interval 8.3 to 19.8 | |
| `swap_fb_lr` (added during I2) | 32 | 1 (3.12 %) | 15 (46.9 %) | 13 (40.6 %) | 12 (37.5 %) | 13 (40.6 %) |
| all four | 128 | 66 (51.6 %) | 95 (74.2 %), interval 66.4 to 82.0 | 29 (22.7 %) | 25 (19.5 %) | |

Paired differences "flagged for any reason", I2 minus baseline (from `compare-i2-grammar.md`): `wrong_open_sides` +46.9 points (28.1 to 65.6),
`swap_fb_lr` +43.8 (25.0 to 62.5), all four +22.7 (14.8 to 30.5), the single mirrors 0. Added by topology alone: `wrong_open_sides` +40.6
points (25.0 to 59.4), `swap_fb_lr` +37.5 (21.9 to 53.1). The pooled rows for the three perturbations are not printed by
`compare_results.py`; they were computed for this document from the stored rows with the harness's own `stats.py` (the same seed and
1000 scene resamples), and the paired difference is +15.6 points (9.4 to 21.9).

What each row says:

* **`mirror_fb`, `mirror_lr`.** The baseline and the I2 code flag all 32 runs of each, through the fit: the fit status is `high_residual` or
  `ambiguous` in all 64 runs and the fit's rms is 31 to 149 px (median about 69 px; the other 64 runs of the table have at most 6.5 px).
  The camera check says `fail` in 2 of the `mirror_lr` runs and in none of the `mirror_fb` runs, and both runs were flagged by the fit
  anyway. So the mirror catch is the fit's, not the topology's, and the topology adds 0.
* **`swap_fb_lr`.** The fit is perfect (median corner error 0.04 % of the diagonal, camera rotation 179.9 degrees: the camera ends up on the
  far side of the booth). The baseline flags 1 of 32. With I2, 13 runs get a topology reason (the camera stands outside closed sides
  only), 12 of them flagged by nothing else. Two more runs are flagged for another reason, the I1 rule "there is no independent check of
  the fit", which applies to scenes with 7 fitted corners (`synth_0003`, `synth_0023`).
* **`wrong_open_sides`.** The baseline flags 1 of 32. With I2, 14 runs get a topology reason and 13 of those are flagged by nothing else.
  The same two 7-corner scenes are flagged by the I1 rule. No accuracy number changes (below).
* **Accuracy on these runs.** Every accuracy metric of every perturbation is marked `=` in the comparison: the topology checks do not
  touch the fit.

### The two kinds of wrong open side

A fair coin per scene picks the mistake (an island has no closed side, so it always gets "an open side declared closed"). The split is a
choice of the harness, not a statistic about readers.

| Mistake | Scenes | Baseline flagged | I2 flagged | With a topology reason | Only by topology | Best possible | Walls on a declared-open side in the spec, baseline to I2 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| a closed side declared open (`closed_as_open`) | 13 | 0 | 13 (100 %) | 13 | 13 | 13 | 13 to 0 |
| an open side declared closed (`open_as_closed`) | 19 | 1 (5.3 %) | 3 (15.8 %) | 1 (5.3 %) | 0 | 1 | 0 to 0 |

* A closed side declared open is caught every time, by the wall check: the scene draws the wall, the declaration says the side is open, the
  compiler leaves the wall out and the gate says so. In those 13 runs the wall that really stands there is **missing from the model**; the
  run goes to review, which is the safeguard, but the model itself is less right than the baseline's.
* An open side declared closed leaves the scene consistent: no wall is drawn there, the corner labels are right, and the camera is outside
  that side only rarely. The only catch is one island scene, and that run was flagged for another reason as well. The information-only
  warning "closed side without a wall" appears in all 19 of these runs and never changes the gate.

### Best possible catch, and what the checks reached

The topology checks reached their best possible catch in both perturbations: 13 of 13 for `swap_fb_lr` and 14 of 14 for
`wrong_open_sides`. The gap to 100 % is therefore not a shortfall of the code; it is what the checks can see on this suite. Counts per
true type of the scene (the generator's own `inline` scenes have three open sides and count as peninsula here), from the stored rows of
group B:

| True type | Scenes | `swap_fb_lr`: best possible / caught | `wrong_open_sides`: best possible / caught |
| --- | --- | --- | --- |
| corner | 8 | 8 / 8 | 6 / 6 |
| peninsula (the generator's `peninsula` and `inline` scenes) | 16 | 5 / 5 | 7 / 7 |
| island | 8 | 0 / 0 | 1 / 1 |
| all | 32 | 13 / 13 | 14 / 14 |

An island has four open sides, so the camera is always outside an open side and a half turn cannot be seen. A peninsula has three, so
most half turns still leave the camera outside one. A half-turned corner booth, which has two open sides, is caught every time (8 of 8).
Pooled over both perturbations the best possible is 27 of 64 runs (42.2 %, interval 28.1 to 56.2), so a recall of 80 % was not reachable
by topology alone for either of them.

## The default suite against the I1 final run (groups C and the I1 final run)

The same 288 runs, correct topology declared in every run. Copied from `compare-i2-default.md`; `B minus A` is I2 minus the I1 final run.

| Metric | Scenes | I1 final | I2 | B minus A | Mark |
| --- | --- | --- | --- | --- | --- |
| Runs with valid input that are flagged | 32 | 14.1 % (18 of 128) | 14.1 % (18 of 128) | 0.00 [0.00, 0.00] | = |
| Good runs that are flagged | 32 | 52.7 % | 52.7 % | 0.00 [0.00, 0.00] | = |
| Really bad runs that are flagged | 29 | 97.9 % | 97.9 % | 0.00 [0.00, 0.00] | = |
| Runs with an injected bad corner that are flagged | 32 | 96.9 % | 96.9 % | 0.00 [0.00, 0.00] | = |
| Corner error, share of the diagonal (median) | 32 | 0.10 % | 0.10 % | 0.00 [0.00, 0.00] | = |
| Camera rotation error, degrees (median) | 32 | 0.40 | 0.40 | 0.00 [0.00, 0.00] | = |
| Absolute ln error of D/H (median) | 32 | 0.00308 | 0.00308 | 0 [0, 0] | = |
| Floor-part placement error, metres (median) | 32 | 0.0251 | 0.0251 | 0 [0, 0] | = |
| Injected bad corners named (recall) | 32 | 70.3 % | 70.3 % | 0.00 [0.00, 0.00] | = |
| Compile path seconds (mean) | 32 | 7.97 | 8.91 | +0.93 [0.61, 1.27] | `-` |

Not all rows are shown. All 168 rows of the comparison file other than seconds (19 in the "All runs" table and the rest in the nine
perturbation tables) are identical on the two sides, with a difference of exactly zero. The only marks that are not `=` are the 10
seconds rows.

From the report of group C (`output/_eval/runs/i2-check0/report.md`), on runs whose declaration is correct:

* Runs flagged with a topology reason: **0 of 288**. Runs flagged *only* by a topology reason, the extra review flags the topology causes:
  **0 of 288**. The check `camera_side` failed in 0 runs and said "cannot tell" (a warning, which never changes the gate) in 1 run (a
  `mislabel` run of `synth_0020`, whose fitted camera was not outside any side of the footprint by more than the 0.25 m margin).
  `wall_on_open_side` and `type_matches_sides` failed in 0 runs, and no wall was left out.
* A zero count on 32 scenes does not rule out a small rate. The harness interval for a zero rate is [0, 0] and says nothing; a
  one-sided exact 95 % upper bound for 0 events in 32 scenes is about 9 % (our calculation, not a harness output). The criterion's limit
  is 2 points.
* **Seconds.** The compile path mean rose from 7.97 s to 8.91 s (+0.93 s, interval 0.61 to 1.27, about 12 %), and the harness marks it as a
  loss. No cause was established. The two code states differ only in `draft_compile.py` and the scene schema, and the topology check is a
  few comparisons. `box_fit.py`, which is byte for byte the same on both sides, also took longer: its median rose from 5.72 s to 6.29 s
  (about 10 %), and the whole run from 1123.7 s to 1267.9 s. That points to the machine rather than to the code, but it does not prove it:
  the load during the I2 runs was not recorded and no like-for-like timing exists. No speed gain or loss is claimed. The rule in the
  [design review](../../docs/design-review-2026-10.md) ("time no worse") is therefore not demonstrated for I2; to decide it, run both
  skills back to back or at the same time on the same machine.

## Verdict on the four acceptance criteria

The criteria were fixed on 2026-10-19 before any measurement and are quoted from the
[design review](../../docs/design-review-2026-10.md#i2-type-and-open-sides).

| # | Criterion | Result | Verdict |
| --- | --- | --- | --- |
| 1 | On the `mirror_fb`, `mirror_lr` and `wrong_open_sides` runs the gate flags at least 80 %; the baseline's rate is reported next to it | Pooled over the three: 83.3 % (80 of 96; interval 77.1 to 89.6) against 67.7 % (65 of 96) for the baseline. Per perturbation: `mirror_fb` 100 % (baseline 100 %), `mirror_lr` 100 % (100 %), `wrong_open_sides` 50.0 % (3.1 %) | **Met if pooled; not met per perturbation.** We do not count it as a clean pass |
| 2 | On correct runs the false-alarm rate rises by at most 2 points against the I1 final run, and no accuracy median is clearly worse | Valid input flagged 14.1 % to 14.1 % (18 of 128 runs on both sides); 0 of 288 runs flagged by a topology reason; all 168 non-time rows of the comparison identical (no accuracy median differs) | **Met.** Time is not shown either way (see above) |
| 3 | With `open_sides` declared, the compiled spec has 0 walls on open sides | Baseline code: 13 of 128 runs (10.2 %), all of them in the 13 `closed_as_open` runs, which is 40.6 % of the `wrong_open_sides` runs. I2: 0 of 128, and 0 of 288 in group C | **Met**, by construction (the compiler drops such a wall); it says the code does what it states, not that declarations are right |
| 4 | Old scene files (the three keys absent) compile byte for byte as before, and the skill and web test suites pass | On the fixture scene, compared with the compiler before I2: `spec.json` and `box_fit.json` identical, `compile_report.json` differs only by the new `topology` key. The unit suites pass (list below). Full native regression of the skill (Blender 5.2.2, `--require-native`): 417 tests, 0 failures, 0 errors, 0 skipped | **Met** on the checks listed below. The skipped tests are not counted as passed; the browser smoke runs are not part of this document |

Criterion 4 in detail:

* The byte comparison is the skill group's one-off check against the pre-I2 compiler. The unit test
  `test_old_scene_files_compile_byte_identically` keeps an old file, the oldest format and a file with every new key null identical to
  each other, and keeps the new words out of the spec.
* The suites ran before the snapshot was taken (`output/_eval/i2_measure.log`): web `npm test` 90 of 90; web Python 165 tests OK, 2
  skipped; harness 206 tests OK; six skill test modules (draft compile, compile rules, quality gate, topology, fast path, box-fit check)
  249 tests OK, 8 skipped; `check_docs.py` 203 files, 0 problems; all text files LF. The reasons for the skips are not in the log.
* The full native regression of the skill (`run_tests.py --require-native`, no failures and no skips needed for a release): 417 tests run, 417 passed, 0 failures, 0 errors, 0 skipped

**About the wording of criterion 1.** The criterion says that on the three kinds of run "the gate flags at least 80 %". It does not say
whether that is one pooled rate or a rate for each. Both readings are given because they differ:

* **Pooled** over the 96 runs the rate is 83.3 % and the criterion reads as met. But 64 of the 96 runs are the two single mirrors, which
  the baseline already flagged at 100 % through the fit. They could show no gain and they lift the pooled number; the lower end of the
  pooled interval, 77.1 %, is below 80 %. The pooled figure says little about the new checks (the pooled gain over the baseline is 15.6
  points, and the topology alone adds 13 of 96 runs).
* **Per perturbation** the single mirrors are at 100 % and `wrong_open_sides`, the one the topology was written for, is at 50.0 %, which
  is 30 points short. For that perturbation 80 % was also out of reach by design on this suite: the best possible topology catch is
  14 of 32 (43.8 %), and the criterion did not take that ceiling into account.

The real gain is `wrong_open_sides` from 3.1 % to 50.0 % flagged (the topology alone adds 13 of 32 runs) and, outside the criterion,
`swap_fb_lr` from 3.1 % to 46.9 %.

## Limits and what the numbers do not show

* **Synthetic only.** Perfect pinhole cameras, our own building blocks, one render style, corners that are the truth plus a model of a
  reader's mistakes. **No real-picture accuracy and no real-picture false-alarm rate has been measured.**
* **No reader in the loop.** The booth type and the open sides are given to the skill as the truth or as a known wrong value. Whether a
  real vision model states them correctly, whether it follows the new reading prompt, and how often it makes each of these mistakes are
  untested. The mix of mistakes (a fair coin per scene) is a choice of the harness. A live run needs the owner's go-ahead, an API key and a
  cost confirmation, and has not been made.
* **`camera_side` false alarms are measured only on pictures taken from an open side.** The synthetic cameras always stand outside an open
  side with at least 0.6 m of room. A real picture from inside the hall, from above, or from a closed side through a cutaway may be treated
  differently, and nothing here measures that.
* **Several rules are not exercised by the suite.** The generator's `inline` scenes have three open sides (a peninsula in this vocabulary),
  so the suite has no booth with one open side and no cutaway scene: the inline rule and the cutaway allowance are covered by unit tests
  only. No perturbation declares a type that contradicts the sides (`wrong_open_sides` changes the type to match), so `type_matches_sides`
  never failed in any run; it too is covered by unit tests only.
* **A reader that mirrors the labels and the declared sides together** describes a mirror-image booth that no topology check can tell from
  a right reading; it is not modelled.
* **Hidden walls.** A real reader may leave out a wall that is hidden in the picture. The check "closed side without a wall" is
  information only for that reason (19 of 128 runs of group B show it, all in `open_as_closed` runs; 0 of 288 in group C).
* **A dropped wall can be a real wall.** When a closed side is declared open, the compiler trusts the declaration and leaves the wall out;
  the gate asks for review. The model of those runs lacks a wall that stands in the booth.
* **Thin statistics.** 32 scenes, one run per scene per perturbation, so a step of one run is 3.1 points and most intervals are wide.
* **The baseline comparison mixes I1 and I2** (see "How to read the comparison with the baseline"). The seconds in `compare-i2-grammar.md`
  (for example +3.2 s on `mirror_fb`) compare the frozen baseline with a code state that also has I1, and are not an I2 cost.
* **Speed is not shown, either way** (see above).
* **Not covered here:** the browser smoke runs, the studio text as a person sees it, and whether the plain reasons help a non-expert.

## Reproduce

Run the commands above on the same suite and seed. Group B and C need a copy of the skill at the state measured here (the working tree of
2026-10-19 before the I2 commit, fingerprint `99dc5b7fbdd9d7c7`); a later state of the repository has a different fingerprint and may give
somewhat different numbers. Group A needs the frozen baseline copy (see [README.md](README.md), "Quick start"). The run folders are
git-ignored and are not part of the repository. The harness is described in [README.md](README.md).
