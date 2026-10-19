# Design review, October 2026

A review of how `booth-image-to-model` reads a picture and builds a model, and the order in which we will make it more accurate
and faster. It is written for contributors and takes about ten minutes to read. The rules for working in the repository are in
[AGENTS.md](../AGENTS.md); the steps an agent follows are in [SKILL.md](../booth-image-to-model/SKILL.md); the evaluation harness
that the plan below depends on is described in [tools/eval/README.md](../tools/eval/README.md).

**Target.** Editable 3D (estimates, not CAD): independent parts that can be adjusted, a look close to the picture, and dimensions
that are estimates. Nothing here makes the output suitable for construction or a bill of materials.

## 1. Summary

- The architecture is sound. A reader turns the picture into a scene description (pixels and plain numbers), a deterministic
  compiler turns that into a parametric spec, and Blender builds and then independently reopens it. No model writes Blender code.
- The weak spot is the first step, and in particular the camera and scale fit. The code reads no pixels. Before I1 the fit could
  only agree with itself, and a poor fit did not stop the pipeline; I1 (done, with one known limitation) addresses those two (see
  [Progress](#10-progress)).
- We could not prove that any change was an improvement, because there was no labelled evaluation set. I0 built a harness from
  synthetic scenes with known ground truth and recorded the baseline. Conclusions about real pictures still wait for a labelled
  set of real pictures.
- After that we change one thing at a time, measure it, and keep it only if it is measurably better. The first change, I1, is an
  honest fit: a robust loss, a leave-one-out error as the independent check, outlier naming only where the fit is well enough
  conditioned, a quality gate, and an optional per-point `fit: false` flag for explicit check corners. Our first design for the
  independent check did not survive the first measurement; [section 9](#9-what-the-first-measurement-taught-us) says what we learned.
  Measured on the synthetic suite, I1 cut the median corner error from 0.34 % to 0.10 % of the image diagonal and the camera rotation
  error from 1.26 to 0.40 degrees. Its known limitation is that valid input is sent to review more often than before (14.1 % of runs
  against 8.6 %), mostly on cropped pictures; see the [known limitations](#known-limitations-after-i1).
- Exhibition booths follow a small grammar (overhead, floor, elevations, open sides, booth type). Using it as a constraint is the
  most promising route to both accuracy and speed.

## 2. What an external LLM review proposed

We asked for an outside opinion from a large language model: a four-round, conversation-style review of the approach and of a
static read of this repository. Its proposals, in our words:

1. Keep a structured intermediate representation and a deterministic generator; never let the model write the 3D scripts.
2. Separate what was observed from what was inferred and from what was assumed.
3. Do not retouch the picture early; removing a logo is not the same as removing the surface that carries it.
4. Separate 2D localisation from semantics; coordinates read by eye from a grid are unreliable.
5. Use instance segmentation and monocular depth.
6. Treat 2D segmentation and 3D decomposition as different problems and pick the geometry from the structure.
7. Attach a confidence to every object.
8. Add relational constraints (on the floor, against the wall), hard and soft.
9. Classify errors and apply structured corrections after a render comparison.
10. Hold out independent check points.
11. Build a small labelled evaluation set (20 to 30 pictures) before anything else.
12. Build a knowledge base rather than post-training, and make it callable by code.

It also separated three possible targets (a faithful look, an editable model, a measured drawing). We chose the middle one.

## 3. Our assessment

The middle column describes the code with the I1 changes. Where an old gap has been addressed, the row says what it was before I1.

| Proposal | What the code does | Verdict |
|---|---|---|
| 1. IR plus deterministic generator | Already so: `scene.json` goes through `draft_compile.py` to a spec, `blender_build.py` builds it and `blender_verify.py` reopens it independently | Already in place |
| 2. Observed, inferred, assumed | Each component carries `observed` or `inferred` (`schemas/scene-description.v1.json`); assumed values are recorded only in the compile report and one free-text note (`scale.notes`) | Partly adopted: record assumptions per parameter |
| 3. No early retouching | The picture is never retouched; logos are excluded by scope (`brand_text`) while the carrying panel is still built | Already in place |
| 4. Localise apart from semantics | The code reads no pixels. Coordinates, colours and sizes all come from the reader, and the skill gives it no reading aid | Direction adopted, different method: the model proposes, code refines; rectified correction views |
| 5. Segmentation and depth | CPU-only machines; new dependencies need approval; the compiler consumes points and rectangles. In a local trial the absolute scale of a monocular depth model was far from the fitted scale (`knowledge/research/reconstruction-tooling.md`) | Not in the main path |
| 6. Geometry follows structure | The spec has eight primitive kinds (`validate_spec.PARAM_KEYS`); the scene description can reach only a few of them | Adopted, ordered by what the failures show |
| 7. Per-object confidence | No such field | Not adopted: a model's own confidence is not calibrated. Use disagreement between readers and fit residuals instead |
| 8. Relational constraints | None | Deferred; later, only the minimum (on the floor, against a wall) |
| 9. Error classes and structured corrections | No automatic correction; `boothctl.py patch` sets one field to an absolute value | Adopted, later |
| 10. Independent check points | Before I1: the documents asked for them, but the default path did not wire them in, because the compiler marked every corner `fit=true` (`draft_compile.py`). Now: the independent check is leave-one-out under a robust loss (`box_fit.py` holds each fitted corner out in turn and predicts it from the others), computed and used to name outliers only when the fit keeps spare equations. Check corners (`fit: false`, projected but not fitted) remain as an optional per-point feature. `draft_compile.py` writes a `quality_gate` from these checks and from the scale checks | Highest priority; addressed in I1 (see Progress). The review did not notice this one. We first made two check corners mandatory and changed that after measuring ([section 9](#9-what-the-first-measurement-taught-us)) |
| 11. Evaluation set | Before I0: no labelled pictures. Now: synthetic scenes with exact ground truth, a harness and a recorded baseline in `tools/eval/`; still no labelled real pictures | Adopted and first; done for synthetic scenes (I0). We generate scenes with our own renderer, which the review did not suggest |
| 12. Knowledge base or post-training | The component macros are executable knowledge; `knowledge/` is read by no code and is unreviewed | Review the numbers first, then use them. Post-training waits for data |

## 4. The real bottlenecks

These are the findings the outside review did not reach. The first three were addressed in I1 (see [Progress](#10-progress));
each keeps its description from before I1, because that is the motivation for the change.

- **The reader was told to assume every dimension (addressed in I1).** Before I1 the web reading prompt
  (`web/server/prompts/read_scene.md`) let the model assume width, depth and height when the picture carried none. A fit that is
  given all three can only reproduce them, so it proves nothing about scale. Now the prompt asks for at most one assumed
  dimension (preferably the height) and says the rest has to come from the picture. Upright standard parts can be listed as scale
  checks; they stay out of the fit and tell whether the one assumed dimension is plausible.
- **A promise nothing kept (addressed in I1).** Before I1 the same prompt said a poor fit would be reported back to the reader,
  but no code sent that report. The sentence is gone. The verdict on the fit goes to the person through the quality gate, not
  back to the reader; one automatic feedback round is a separate step (I7).
- **A poor fit did not stop the pipeline (addressed in I1).** Before I1, `draft_compile.py` went on after a `high_residual` status
  from `box_fit.py`, and nothing stopped the one-click chain in the studio. Now the compiler still builds the model, because a
  doubtful model is still useful to look at, but it writes a `quality_gate` (`pass` or `review`, with plain-English reasons) into the
  compile report. `web/server/steps.py` passes it on unchanged, and the studio holds the one-click chain on `review` after the
  model step and offers "Render anyway". The skill's route text tells an agent to read the same gate before the final render.
  The gate is a judgement on limits, so how often it holds good input back is itself measured: on the synthetic suite it sends
  14.1 % of valid input to review, against 8.6 % before I1 (see Progress).
- **The agent's time dominates.** On a local benchmark a picture takes about 20-28 minutes, and only 2-7 minutes of that is tool
  compute. Fewer calls and fewer rounds matter more than faster scripts.
- **No cross-agent view.** The review did not consider that the skill must work with agents of very different ability.

## 5. Design principles for a generic skill

The skill must not depend on one vendor or one agent.

1. **The model judges, the code measures.** The model decides what is there, what it belongs to, what type it is and roughly where.
   Code does exact pixels, colour sampling, the camera, consistency checks and error classification.
2. **Contracts are the interface.** The scene description and the spec each have a schema. Anything can read a picture into them:
   a host agent's own vision, an OpenAI-compatible service, or a person.
3. **Layered, one-way dependencies.** Contracts, then a deterministic engine (CLI and Python API from one operation registry), then
   adapters, then the routing text in `SKILL.md`, then optional perception plug-ins.
4. **Capability tiers with graceful fallback.**

   | The agent can | Route |
   |---|---|
   | See images and run commands | The full route |
   | Run commands only | Call a configured vision service (with consent) or ask a person for the scene description |
   | Only reach tools over MCP | Use the MCP route |
   | Only chat | Emit `scene.json` and hand it to the studio |
   | Weak model | Closed multiple-choice questions |

5. **Decisions live in code.** Every command prints `{status, next, ...}`; the documentation only explains the `next` codes.
6. **Accept only on independent signals.** Leave-one-out error, vanishing-point agreement and part-region agreement. Never the fit
   residual alone, which can be driven down without being right. An independent signal that is not reliable must say so: when too
   few equations are left over, the leave-one-out error is reported as unavailable instead of naming corners it cannot judge.
7. **Small, closed tasks.** Enumerations, numbered markers, rectified views.
8. **Few dependencies, clear privacy.** numpy, SciPy and Pillow only. Say plainly that a host agent "looking" at a picture means
   sending it to that agent's provider.
9. **Testable.** A synthetic evaluation set, and contract tests with a scripted stand-in agent.

## 6. Booth grammar

Booths are not arbitrary shapes. A small vocabulary describes almost all of them, and the vocabulary constrains what the reader
may claim.

| Term | Meaning |
|---|---|
| Overhead | Everything above head height: fascia or header band, canopy, ceiling, hanging sign |
| Floor | Walking surface: carpet, raised floor or platform, edge trim, ramp |
| Elevation | A vertical face: back wall, side wall, partition |
| Open sides | The sides of the footprint with no wall, where visitors come in. The four sides are named front, back, left and right, as in the corner tokens of the scene description |
| Inline (row) booth | One open side |
| Corner booth | Two adjacent open sides |
| Peninsula booth | Three open sides; only the back is closed |
| Island booth | Four open sides |
| Other | Anything else, such as two opposite open sides or an irregular footprint |
| Shell scheme | Standard booth built from a modular wall system supplied by the organiser |
| Space-only | Bare floor space; the exhibitor has a custom stand built |
| Plan, elevation, reflected ceiling plan | The three drawings that describe floor, walls and overhead |

Renderings often cut away a front wall or a roof panel to show the inside. A cutaway is a side whose wall, or the roof, is only
left out in the picture to show the inside. It is not a real opening, and the scene description lists it as a cutaway side
(`cutaway_sides`), not as an open side.

**How the grammar constrains reading**, in order of expected benefit:

1. **Decide the type and the open sides first, then read details.** Once the topology is fixed, which sides have walls and which
   corners can be seen follows. This removes mislabelled axes and swapped width and depth.
2. **Rectified views.** After the camera fit, the floor and each closed elevation are warped into straight-on views with a metric
   grid. The reader places things in metres on those views and code maps them back to 3D. This needs no new dependency.
3. **Discrete spec candidates.** Common footprints, module grids, height levels and standard part sizes are scored as candidates
   and then refined. They are soft constraints only: when the picture disagrees, the fit falls back to free fitting and reports
   whether each value came from a standard size, from the fit or from an assumption. They need an owner's review first; the review
   table is [priors-review.md](priors-review.md).
4. **Fill in by surface.** Floor, each elevation, overhead and interior are filled separately, each cell a closed choice.
5. **Repetition and symmetry.** Chairs, spotlights and fins are read as a count and a start and end range; code spaces them.
6. **Construction rules for validation and completion.** For example, an overhead structure needs support; hidden parts take type
   defaults and are marked as assumed.

**Exits.** Free-form surfaces, double decks, non-rectangular footprints and artistic perspectives use the type `freeform` and go
back to the current reading. Regional differences (a metric module versus a 10 ft grid) are a parameter.

## 7. Iteration method

**Rules.**

- One change at a time. Run the evaluation before and after and record both numbers.
- Keep a change only if accuracy is better and time is no worse, or time is better and accuracy is no worse. Otherwise revert it
  or rework it.
- Each step is its own batch of commits, with the changelog and the progress table below updated.
- The next step is chosen from the failure-cause statistics of the last one, and confirmed with the owner.

The order follows expected effect on accuracy and speed, divided by cost, subject to dependencies. Without an evaluation we cannot
show that anything is better, so that comes first.

| Step | Change | Main gain | Needs |
|---|---|---|---|
| I0 | Evaluation baseline on synthetic ground truth | Can prove "better than now" | None |
| I1 | Honest fit: robust loss, leave-one-out error and outlier dropping as the independent check, an optional per-corner `fit: false` flag, a quality gate that also reads the scale checks, at most one assumed dimension | Accuracy: scale and camera, the largest error source | I0 |
| I2 | The reader states the type and the open sides first (`booth_type`, `open_sides`, `cutaway_sides`); the compiler checks the rest against them and puts walls only on closed sides. Overhead and platform are not part of I2 | Accuracy (topology), speed (less rework) | I0 |
| I3 | Rectified views: floor plan and closed elevations | Accuracy: positions and sizes | I1 |
| I4 | Discrete spec candidates | Accuracy and speed (less ambiguity) | Owner review of the priors |
| I5 | Corners entered once; an `iterate` command that returns a summary and `next` | Speed: about half the agent calls | None |
| I6 | Colour sampling and part-region agreement check | Accuracy: colour, and misplaced parts found | I0 |
| I7 | One fit-feedback round, accepted only on independent signals | Accuracy | I1 |
| Later | Surface-by-surface tables, per-elevation reading, render-feedback error classes, wider scene vocabulary, editor structure edits, the generic architecture, MCP adapter, a case library, post-training | Decided from the failure statistics | None |

**Where the product features sit.** Corner-point correction in the studio, drawings (plan and elevations, SVG and DXF, from the
same spec) and a furniture library (prefab JSON built from existing primitives) are product features, not accuracy steps. They are
not in the table above; they come after the accuracy iterations I0 to I7 unless the owner reorders them. This is the one priority
order, and the Roadmap in [AGENTS.md](../AGENTS.md) points here. Drawings in particular wait for numbers that can be defended: a
dimension printed on a drawing reads as a measurement, and ours are estimates.

**Limits of the evaluation.** The synthetic set is built from our own parts, so its scores are optimistic. Conclusions about real
accuracy wait for a labelled set of real pictures.

## 8. What we will not do

- Build an orchestration framework into the skill.
- Put a segmentation or depth model in the main path.
- Turn on multi-model or multi-round critique by default.
- Ask the model for a self-reported confidence, or add fields such as `visible_faces`.
- Keep parallel English and Chinese copies of the references.
- Build a text-chunk retrieval knowledge base.
- Train end to end.
- Make one large change.

## 9. What the first measurement taught us

The first plan for I1 made the independent check explicit. The reader marked two of its 8 to 10 clicked corners `fit: false`; the
fit left them out, projected them with the fitted camera, and their pixel error was the honest test. That is the outside review's
proposal 10, and on paper it looked right. We built it and measured it against the frozen baseline on the synthetic suite
([RESULTS-I1.md](../tools/eval/RESULTS-I1.md)). The harness showed three things that the design had not foreseen:

- **Withholding corners costs accuracy.** With 8 clicked corners only 6 are fitted. Fewer points for the same unknowns make a
  worse-conditioned fit. On lightly noisy input the result was clearly worse than the baseline: at 1 px and 3 px of noise the median
  corner error rose from 0.07 % to 0.11 % and from 0.19 % to 0.28 % of the image diagonal, and the error of the width/height
  ratio (absolute ln) from 0.00207 to 0.0044 and from 0.00929 to 0.0135. The same code with every corner fitted showed no clear
  change at those noise levels ([RESULTS-I1b-arms.md](../tools/eval/RESULTS-I1b-arms.md)).
- **It raised false review flags.** In round 1, valid input (no injected error, right dimensions) was sent to review in 41.4 % of
  the runs, against 8.6 % for the baseline; with every corner fitted the round-1 code still flagged 28.9 %. A gate that holds back
  good input this often would stop the one-click chain too often to be useful. Cropped pictures, whose principal point is off the
  centre while the skill fits a centred camera, were flagged much more often than full-frame pictures from the start (20.5 % against
  2.4 % of valid input for the baseline already).
- **Naming the wrong corner needs spare equations.** Leave-one-out named the injected corner in 40.6 % of the 30 px cases against a
  target of 90 %. (That count included corners that had been held out and so could never be named; counting only fitted corners it
  was 13 of 29.) With 8 fitted corners it mostly worked; with 6 or 7 it mostly did not, and it also named some innocent corners.

What we changed for round 2, and what the final measurement
([RESULTS-I1c.md](../tools/eval/RESULTS-I1c.md)) says about each change:

- **Leave-one-out is the default independent check, with the robust loss and the dropping of named outliers; every corner is
  fitted.** This combination won. It tests every fitted corner and gives up none, so it costs no points. Over all 288 runs the
  median corner error fell from 0.34 % to 0.10 % of the diagonal, the camera rotation error from 1.26 to 0.40 degrees, the
  depth/height error from 0.0152 to 0.00308 and the part placement error from 0.075 m to 0.025 m. With one corner 30 px off, the
  corner error and the rotation error fell by a factor of about ten. Swapped corner labels, which used to wreck the camera (24.6
  degrees of rotation error), now leave 1.1 degrees. On clean and 1 to 3 px noisy input there is no clear change in accuracy.
- **Mandatory check corners are gone.** They are removed from the reader's prompt and from the default route in `SKILL.md`. The
  per-point `fit: false` flag and the `check_points` output stay in the engine as an optional feature, useful with 11 or more
  corners or when a person wants specific corners held out.
- **Leave-one-out is computed, and outliers are named, only when the fit is well conditioned.** The rule keeps 5 spare equations
  after one point is held out: with one assumed dimension that means 8 fitted corners, with all three dimensions stated, 7.
  Otherwise those fields are `null`, the list of outliers is empty and `loo_reliable` is false: no names is better than unreliable
  names. With 8 or more fitted corners the single 30 px outlier was named in 29 of 30 runs; at 8 fitted corners the rate is lower
  when two corners are wrong or two labels are swapped (22 % to 28 % against 81 % to 91 % with 9 or more).
- **The quality gate also reads the scale checks, and had to earn its place on the numbers:** no more false review flags than the
  baseline plus 5 points, and no loss of recall on really bad runs. Recall held (97.9 % against 98.5 %, no clear change). The false
  flags did not quite make it: 14.1 % of valid input against 8.6 %, which is 0.5 point over the limit (see Progress).

What the final measurement added to the picture:

- **Where the extra flags on valid input come from.** Of the 8 valid-input runs that the baseline did not flag and the final code
  does, 6 come from two scenes that have only 7 fitted corners: leave-one-out cannot be computed there, and the gate sends the run to
  review because it has no independent check. The other 2 are 3 px runs whose fit residual is just over its limit. Cropped pictures
  carry most of the level (13 of the 18 flagged valid-input runs) but not most of the increase (+4 runs on cropped pictures, +3 on
  full-frame ones). A gate that asks for review when it has no independent signal is a deliberate choice; the cost is visible here.
- **Outcome-based decision metrics got worse because the fits got better.** Agreement of the flag with the outcome (67.0 % to 55.6 %)
  and precision (42.7 % to 26.6 %) fell, although the really bad runs fell from 71 to 47 of 288. The gate still flags runs whose
  input was defective after the robust loss has repaired the fit, and the outcome label counts those as false alarms. Against input
  defects the gate keeps its reliability (96.9 % of defective input flagged; 89.6 % of flagged runs have a defective input). These
  rows are in the results file so that nobody reads the table selectively.
- **Camera numbers with three assumed dimensions are slightly worse** (rotation +0.6 degrees, field of view +3.5 degrees at the
  median, clear in the paired interval). The proportions are fixed by the assumed values for any engine. The reader's prompt now
  asks for at most one assumed dimension, which the oracle mode cannot test.

What we take from it. A change that follows good advice can still cost accuracy, and the harness found this before the change was
committed. The acceptance criteria were rewritten after the first results. They are listed in Progress, with what changed, so that
nobody reads them as the original plan. Our own recall metric had the same kind of flaw (it counted held-out corners that could not
be named), and the harness now counts only fitted ones. The evidence is thin: 32 synthetic scenes, one render style, wide
intervals, no real picture and no reader in the loop. The effect of stating one dimension instead of three belongs to the reader's
prompt, and the oracle mode cannot test it because it feeds the dimensions directly; only a replay of saved replies or a live run
can. The comparison of the fit variants on the round-1 code (corners held out against none) is in
[RESULTS-I1b-arms.md](../tools/eval/RESULTS-I1b-arms.md), the final measurement in [RESULTS-I1c.md](../tools/eval/RESULTS-I1c.md); for all
numbers see the results files, not this page.

## 10. Progress

This table is updated when a step lands. A step is "done" only when its before and after numbers are recorded.

| Step | Status | Evidence |
|---|---|---|
| I0 Evaluation baseline | Done | The harness is described in [tools/eval/README.md](../tools/eval/README.md); the recorded baseline, its command and the numbers are in [tools/eval/BASELINE.md](../tools/eval/BASELINE.md). The results documents of I1 are its first use |
| I1 Honest fit | Done on 2026-10-19, with one known limitation | Final measurement and verdict: [RESULTS-I1c.md](../tools/eval/RESULTS-I1c.md). Earlier rounds: [RESULTS-I1.md](../tools/eval/RESULTS-I1.md) (first design) and [RESULTS-I1b-arms.md](../tools/eval/RESULTS-I1b-arms.md) (held-out corners against none, round-1 code). Over all 288 synthetic runs the median corner error fell from 0.34 % to 0.10 % of the image diagonal, the camera rotation error from 1.26 to 0.40 degrees and the part placement error from 0.075 m to 0.025 m; one 30 px outlier was named in 29 of 30 runs with 8 or more fitted corners. Valid input is sent to review in 14.1 % of the runs against 8.6 % before (criterion 3 missed by 0.5 point), mostly on cropped pictures |
| I2 Type and open sides | Done on 2026-10-19, with a modest measured effect | Final measurement and verdict: [RESULTS-I2.md](../tools/eval/RESULTS-I2.md); design, checks and acceptance criteria: [the I2 section](#i2-type-and-open-sides). On the synthetic suite a wrong open side is sent to review in 50.0 % of the runs against 3.1 % before (the topology checks alone add 40.6 points), and a booth turned half way round in 46.9 % against 3.1 %. The topology adds no review flag to correct input (0 of 288 runs) and changes no accuracy number. Walls on a declared open side fell from 10.2 % of the runs to 0. Mirrored pictures were already flagged at 100 % by the fit, so the topology adds nothing there. Criterion 1 was worded loosely: met when pooled (83.3 %), not met for the wrong open sides alone (50.0 %). Limits: no real reader and no real picture, synthetic cameras always outside an open side, and the mean compile time was 0.93 s higher than in the I1 run, with no cause established |
| I3 Rectified views | Next | Not started. It is next in the order of section 7, to be confirmed with the owner once the I2 numbers have been seen |
| I4 Discrete spec candidates | Planned | Waits for the owner's review in [priors-review.md](priors-review.md) |
| I5 Corners once, `iterate` | Planned | |
| I6 Colour and region check | Planned | |
| I7 Fit feedback round | Planned | |

**Acceptance criteria for I1** (revised after the round-1 measurement; same suite and seed as the baseline, compared scene by scene
with `tools/eval/compare_results.py`) and what the final measurement found. I1 was to be done when all four held. It was closed on
2026-10-19 with three met and one missed by half a point: the owner decided to close I1 with the measured results and with the
limitation written down below.

| # | Criterion | Result | Verdict |
|---|---|---|---|
| 1 | With one 30 px outlier on a fitted corner and at least 8 fitted corners, leave-one-out and outlier naming finds at least 90 % of the injected corners, and the camera and proportion errors are not worse than the baseline's (better is the aim) | Named in 29 of 30 runs (96.7 %). Over the 32 scenes of that case the corner error fell from 0.41 % to 0.04 %, the rotation error from 0.89 to 0.09 degrees, W/H from 0.0166 to 0.00172, D/H from 0.0237 to 0.00234 and the part placement from 0.0933 to 0.0141 m | Met |
| 2 | On clean input and on lightly noisy input (1 to 3 px), accuracy is not worse than the baseline's: the paired 95 % interval of the difference is not clearly worse | No metric is clearly worse; every interval includes zero. At 3 px the point estimates of the two proportion errors are 14 % and 25 % higher | Met as defined; low power with 32 scenes |
| 3 | Valid input flagged for review: not more than the baseline's rate plus 5 percentage points | 8.59 % to 14.1 % (+5.47 points, interval -0.78 to +13.3, which includes zero); 11 to 18 of 128 runs; the limit was 13.6 % | Not met, by 0.5 point |
| 4 | Really bad runs flagged for review: recall not lower than the baseline's | 98.5 % to 97.9 % by scene (no clear change); per run 70 of 71 to 46 of 47, the same single run missed | Met |

What changed from the first version of the criteria, which was written before any measurement:

- The naming target (1) used to apply at whatever number of corners the reader gave. It now applies from 8 fitted corners, because
  the first measurement showed that fewer cannot be judged reliably. In the same criterion, "camera and proportion errors fall"
  became "not worse than the baseline, better is the aim", which is weaker; (2) to (4) are there so that this cannot be paid for
  with accuracy or with false alarms.
- The false-alarm rate on valid input (3) and the recall on really bad runs (4) are new and explicit. The first version only said that
  the gate should flag bad fits, which a gate that flags everything also satisfies.
- The one-dimension criterion left the list: the oracle mode feeds dimensions directly and cannot test the prompt, so it is
  checked separately with saved or live reader replies.
- Criterion 2 (clean and lightly noisy input) is now defined by the paired 95 % interval, as in the results files.

### Known limitations after I1

- **Valid input from cropped pictures is held for review too often.** The skill fits a centred camera, and the synthetic suite
  crops a third of its scenes (11 of 32) so that the principal point is off the centre. Valid input is flagged in 29.5 % of the
  cropped runs and 5.95 % of the full-frame runs (the baseline: 20.5 % and 2.38 %). Cropped pictures are also less accurate (median
  corner error over all perturbations 0.21 % against 0.04 % of the diagonal) and injected outliers are named less often in them (57.6 % against 77.0 %). The share of
  cropped scenes is a property of the suite, not a statistic about real pictures, and the centred principal point as the cause is a
  hypothesis that was not tested. I1 does not address it.
- **Criterion 3 is missed by 0.5 point** (14.1 % against a limit of 13.6 %; the interval includes zero). Six of the eight extra
  flagged runs come from scenes with only 7 fitted corners, where leave-one-out cannot be computed and the gate asks for review for
  lack of an independent check; two come from 3 px runs whose fit residual is just over its limit. Asking readers for more corners,
  fitting the principal point and loosening a limit are untried ideas, none of them measured.
- **No leave-one-out and no outlier names below 8 fitted corners** when one dimension is assumed (7 when all three are stated). Even
  with 8 fitted corners, two wrong corners or two swapped labels are named only in about a quarter of the cases (22 % to 28 %).
- **Synthetic scenes only.** The suite is built from perfect pinhole boxes made of our own blocks, and the corners are the truth plus
  a model of a reader's mistakes. No real-picture accuracy has been measured, and conclusions about real booths wait for a labelled
  set of real pictures.
- **The reader is not in the loop.** The oracle mode feeds corners and dimensions directly. Whether a real vision model follows the
  new prompt (8 to 10 corners, at most one assumed dimension, no corner held out) is untested. A live run needs the owner's go-ahead,
  an API key and a cost confirmation.
- **A wrong stated dimension is not measured on the final code**, so the flag rate for it and the effect of a scale check are
  unknown. The ratios cannot see such an error; only a scale check or a printed dimension can.
- **Time is not shown.** The rule in section 7 asks for time no worse. The baseline timing came from a busy machine and the final one
  from an idle machine, so the compile mean of 13.2 s against 8.0 s is not a speed comparison, and no gain is claimed. The two skills
  have to be run at the same time on the same machine to decide it.
- **Outcome-based decision metrics look worse** (agreement of the flag with the outcome 67.0 % to 55.6 %, precision 42.7 % to
  26.6 %) because the gate stays careful after the fit is repaired; see section 9.
- **Camera numbers with three assumed dimensions are slightly worse** than the baseline's. The reader's prompt asks for at most one
  assumed dimension, so this case should become rarer, but that has not been tested with a reader.

### I2: type and open sides

**Status.** Done on 2026-10-19. The numbers, the verdict on each criterion and the full list of limits are in
[RESULTS-I2.md](../tools/eval/RESULTS-I2.md); the sections below the criteria say what was measured against them.

**The result in one paragraph.** The effect is modest. On the synthetic suite, 16 of 32 runs with a wrong open side are now sent to
review, against 1 of 32 with the frozen baseline (the topology alone accounts for 13 of the 15 extra runs), and 15 of 32 runs with a
booth turned half way round, against 1 of 32 (12 of the 14 extra). The checks raised no extra review flag on correct input in 288 runs
and changed no accuracy number. The 100 % catch rate on mirrored pictures (all 64 runs) is the fit's, not the topology's: the
baseline already flagged every one of them, and the topology adds nothing there. No real reader and no real picture has been tested,
so we do not know how often readers make these mistakes or whether the checks raise false alarms on real pictures.

**What it does, in plain words.** Until now the reader described walls, counters and screens, but never said which sides of the
booth are open to visitors. That leaves three gaps. A reader can swap front and back, or left and right, in all its labels at once,
and the camera fit cannot reveal this, because the mirrored labels fit the picture as well as the right ones. A render that cuts a
wall away to show the inside can lead to a wall in the model on a side where visitors walk in. And a stated booth type can
contradict the sides that the same reader called open. In I2 the reader first states the booth type, the open sides and the cutaway
sides, before any detail, and the compiler uses these three statements as a check on the rest.

**Words.** *Open sides* are the sides of the footprint with no wall, where visitors come in. *Inline* is one open side, *corner*
two adjacent open sides, *peninsula* three open sides, *island* four open sides, and *other* is anything else, such as two opposite
open sides or an irregular footprint. A *cutaway* is a side whose wall, or the roof, is only left out in the picture to show the
inside; it is not a real opening. Sides are `front`, `back`, `left` and `right`, the same words as the corner tokens used by
`box_fit.py` and the scene description.

**New fields.** The scene description (`schemas/scene-description.v1.json`) gets three top-level keys. All three are required but
may be null, because strict structured-output services need every property listed. Null means unknown, and then no topology check
runs.

| Key | Value |
|---|---|
| `booth_type` | `inline`, `corner`, `peninsula`, `island`, `other`, or null |
| `open_sides` | A list of `front`, `back`, `left`, `right` without repeats, or null |
| `cutaway_sides` | A list of the same words, or null |

New readers write `schema_version` `1.2`. Older files are read as before: `upgrade_scene()` fills the three keys with null, and
they compile to the same spec bytes as before.

**What the compiler checks.** `draft_compile.py` adds a `topology` block to the compile report with the three statements and these
checks. Each check has a status of `ok`, `warn`, `fail` or `not_checked`.

| Check | What it looks at | When it fails or warns |
|---|---|---|
| `type_matches_sides` | Inline needs 1 open side, corner 2 adjacent open sides, peninsula 3, island 4; `other` accepts anything | Mismatch: fail |
| `wall_on_open_side` | A wall in the scene description placed on an open side | The wall is left out of the spec, with a `dropped` entry "wall on an open side", and the status is fail. A wall on a cutaway side is allowed, because a cutaway is not open |
| `closed_side_without_wall` | A closed side that has no wall | Warn only; never a reason for review by itself |
| `camera_side` | Whether the fitted camera stands outside at least one open or cutaway side | If it stands only behind closed sides, the labels are probably mirrored: fail |

`camera_side` is the check that can raise false alarms. It is measured on the synthetic data before it is kept. If it raises false
alarms on correct runs by more than 2 percentage points, it is restricted, and the restriction is written here. **Result:** it raised
none (0 failures in 288 runs with a correct declaration; one "cannot tell" warning, which does not change the gate), so it was kept
without a restriction. The measurement covers only cameras that stand outside an open side, as every synthetic camera does.

**The gate.** Any failed check puts the quality gate on `review` with a plain reason: the booth type does not match the open sides;
a wall was placed on an open side and left out; the camera looks in through closed sides, so front and back, or left and right, may
be swapped. A warning alone changes nothing. When `booth_type` and `open_sides` are null every check is `not_checked` and the gate
is as before. The studio shows these reasons in plain English on the main path and carries the `topology` block unchanged in the
detail view.

**The reader.** The reading prompt (`web/server/prompts/read_scene.md`) asks for the type, the open sides and the cutaway sides
first, with short definitions and a rule for telling a cutaway from an opening. Its repair rule asks again when `open_sides` is
given but `booth_type` contradicts it, or when a side is listed twice.

**How it is measured.** The synthetic generator already knows each scene's booth type and open sides, so the oracle `scene.json`
now carries them (the reader is assumed right). Four new perturbations are opt-in through `--perturbations`: `mirror_fb` swaps
every front and back corner token consistently, `mirror_lr` swaps left and right, `wrong_open_sides` declares the open sides
wrong (one open side declared closed, or one closed side declared open, with the type changed to match), and `swap_fb_lr` swaps both
pairs together, a half turn of the booth that a perfect fit cannot reveal. The fourth was added during I2, after a first finding by
the skill work, so it is not one of the three in criterion 1. Every existing
perturbation, the default `oracle-v1` list and its fingerprint stay as they are. The metrics are the share of runs on the new
perturbations that the gate flags with a topology reason (recall), the false-alarm rate of the topology reasons on runs with correct
topology, the number of walls on open sides in the compiled spec, and the status of each check. The frozen baseline skill ignores
the new keys, so the harness strips them for it, as it already does for older versions.

**Acceptance criteria.** Fixed on 2026-10-19, before any measurement. They apply to the same suite and seed as the I1 final arm
(`oracle-v1`, seed 20261019; the I1 final run is `output/_eval/runs/i1c-check0`).

| # | Criterion |
|---|---|
| 1 | On the `mirror_fb`, `mirror_lr` and `wrong_open_sides` runs the gate flags at least 80 %. The baseline's rate on the same runs is reported next to it |
| 2 | On correct runs the gate's false-alarm rate rises by no more than 2 percentage points against the I1 final arm, and no accuracy median is clearly worse |
| 3 | With `open_sides` declared, the compiled spec has 0 walls on open sides |
| 4 | Old scene files (the three keys absent) compile byte for byte as before, and the skill and web test suites pass |

**What the measurement found** (the details, the intervals and the other limits are in [RESULTS-I2.md](../tools/eval/RESULTS-I2.md)).

| # | Result | Verdict |
|---|---|---|
| 1 | Pooled over the three perturbations the gate flags 83.3 % (80 of 96) against 67.7 % for the baseline. Per perturbation: `mirror_fb` 100 % (baseline 100 %), `mirror_lr` 100 % (100 %), `wrong_open_sides` 50.0 % (3.1 %) | Met if pooled; not met per perturbation |
| 2 | Valid input flagged 14.1 % to 14.1 %; 0 of 288 runs flagged by a topology reason; every accuracy row identical to the I1 final run | Met. Time is not shown either way |
| 3 | Walls on a declared open side: 13 of 128 runs (10.2 %) before, 0 of 128 and 0 of 288 now | Met, by construction |
| 4 | Fixture scene compiles as before apart from the new `topology` key; the unit suites pass; full native regression in RESULTS-I2.md | Met on the checks listed |

Criterion 1 was worded loosely: it does not say whether the 80 % is one pooled rate or a rate for each kind of run. Pooled it is met,
but two of the three kinds are mirrored pictures that the baseline already flagged at 100 % through the fit, so they lift the pooled
number without showing anything about the new checks. For `wrong_open_sides`, the kind the topology was written for, the rate is
50.0 % against 3.1 %, and 80 % was out of reach on this suite anyway: at best 14 of 32 scenes (43.8 %) can be caught from the type and
the sides (an island has no closed side that could be declared open, and a side wrongly called closed leaves the scene consistent). Within
that ceiling the checks caught every run, 14 of 14. The real gain is `wrong_open_sides` from 3.1 % to 50.0 % and, outside the
criterion, `swap_fb_lr` from 3.1 % to 46.9 %.

**What this does not show.** The oracle mode feeds the topology directly, so it measures the code check, not whether a real reader
states the type and the open sides correctly; that needs a replay of saved replies or a live run, which needs the owner's go-ahead.
The scenes are synthetic and come from one generator, so the intervals are wide (32 scenes). The synthetic cameras always stand
outside an open side, so how often `camera_side` raises a false alarm on a picture from inside the hall, from above, or through a
cutaway is unknown. The suite has no booth with one open side and no cutaway scene, and no run declares a type that contradicts the
sides, so those rules are covered by unit tests only. Compile time is not shown to be no worse (+0.93 s on the mean, cause not
established; the unchanged `box_fit.py` also ran about 10 % slower in the same run).
