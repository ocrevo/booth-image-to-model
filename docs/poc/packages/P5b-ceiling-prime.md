---
id: P5b
title: 天花板 C′：慢 agent 用新词汇建
goal: 用补齐后的引擎（新家族、表面图案、投影、暗场）通过 design.json 把目标图建到最好，证明引擎够用；顺带出清单第 9 行（玻璃）的证据
depends_on: [P7-fam-sweep, P7-mat-pattern]
model: opus
lane: blender
allowed_files:
  - output/poc/ceiling2/**
forbidden_files:
  - "*"
inputs:
  - output/poc/ceiling/<pic>/box_fit.json（或 P4 的）
  - output/poc/ceiling/missing-vocabulary.md
  - output/poc/ceiling2/evidence/（各行的证据设计，可作起点）
  - booth-image-to-model/prompts/booth_design.md、templates/design_template.json、references/component-recipes.md
deliverables:
  - output/poc/ceiling2/<pic>/design_00N.json、v00N/（spec.json、compile_report.json）、previews/、timing.json
  - output/poc/ceiling2/evidence/9/（玻璃的证据设计和 Cycles 预览）
  - output/poc/ceiling2/missing-after.md（建 C′ 时仍表达不了的东西）
  - output/poc/ceiling2/<pic>/score.json（指挥官跑）
tests_first: []
acceptance_cmds:
  - python booth-image-to-model/scripts/boothctl.py design-compile --help
effort_h: 10
day: 7-8
stop_rule: 连续两轮没有肉眼可见改进就停；先交 pic_A（G3 只看它的分数）；两条链按时做完则 G3 在第 7 天，做不完按决策人 2026-10-23 的决定推到第 8 天，pic_A 最晚在 G3 那天中午交
---

# P5b 天花板 C′

## 目标

同 [P5](P5-ceiling.md)，但走设计路线：慢 agent 手写 `design.json`（用新家族），`design-compile --fit --image` 编译，`preview --look dark_hall` 加 `--material-set projected` 看投影版，中性版照常。C′ 是闭环要追的目标；G3 看它是否 >= 85。

依赖两条链各自的最后一个包（链 A 的 [P7-fam-sweep](P7-fam-sweep.md)、链 B 的 [P7-mat-pattern](P7-mat-pattern.md)），也就是第 3 步的八个包全部合并后才开始。

（2026-10-24 指挥官裁定）pic_A 用到的清单行都已合并并有核对通过的证据，pic_A 的 C′ 先做，不等链 A 剩下的包：代码用冻结快照（poc/integration f3e01fe 的 `booth-image-to-model/`，skill 3.1.0-rc7），整轮都用它。G3 只看 pic_A 的分数。pic_B、pic_C 和第 9 行玻璃的证据仍等 sweep 合并后做，那时用当时的冻结快照。起因：决策人看了对照图，觉得整套展台和以前没什么差别；新部件到现在只出过单件证据，还没拼进整套模型。

## 步骤

1. 从 `missing-vocabulary.md` 出发，每一行用还原记录（`06-fidelity-ledger.md`，只在本地）里写的家族或做法；其余元素用原有家族；文字用占位字；道具和小件电子产品用 `prop_block`。`evidence/` 里各行的证据设计可以拿来作起点。
2. 每轮：改 `design_00N.json`，`boothctl.py design-compile --design ... --fit <box_fit.json> --image input/poc/<pic>.<ext> --out output/poc/ceiling2/<pic>/v00N`，读 `compile_report.json` 的 `dropped` 和 `warnings`；拿令牌，`preview --client-look`，释放；看叠图。对比时另出 `--look dark_hall` 和 `--material-set projected` 的预览。
3. 玻璃（清单第 9 行）不开包：引擎已能做半透明（设计里 `material.finish` 取 `glass`，alpha 0.35）。在 `evidence/9/` 写一个只含玻璃台面的证据设计（取需要第 9 行的那张图上的一块玻璃台面），尺寸按原图这一块目测，用该图的 `box_fit.json` 编译；出一张 `preview --engine cycles`，玻璃后面的东西要看得见；再出一张加 `--material-set projected` 的，玻璃仍半透明。指挥官另派核对 agent 按 [05 第 3 节第 2 步](../05-gates-and-closing.md#3-g3-引擎够用止损点二第-7-天)判，判据：台面是透明的，透过它看得见后面的东西；投影版里仍是。
4. 写 `missing-after.md`：逐行写建 C′ 时用了什么、效果如何、还差什么；清单外新发现的缺口也写进去（指挥官把它们作为"暂缺"补进还原记录）。它是观察记录；"已可表达"不由本 agent 标，由 05 第 3 节第 2 步的证据核对决定。
5. 停止后指挥官用面板和 `score.py` 打分（带 `--fit`，用编译 C′ 时给 `design-compile` 的那个拟合文件，03 第 3.1 节第 3 条）。

## 规则

同 P5：不读答案卷，不改 skill，令牌只持预览的时间。`evidence/` 下只写 `9/`，不改别的行的证据。

## 完成的定义

三张图各有 C′；`evidence/9/` 有两张 Cycles 预览和证据设计，核对 agent 的 `verdict.json` 已写（`pass` 或 `fail`，`fail` 时指挥官报决策人）；`missing-after.md` 覆盖 `missing-vocabulary.md` 的每一行；`pic_A` 的 `score.json` 在 G3 前可用。

## 验证者清单

指挥官检查：每个 `v00N/compile_report.json` 没有未解释的 `dropped`；`design.json` 通过 schema；文字都是占位字；`evidence/` 下只有 `9/` 是本包写的。
