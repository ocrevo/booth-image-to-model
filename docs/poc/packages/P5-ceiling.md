---
id: P5
title: 天花板 C：慢 agent 用今天的积木建到建不动
goal: 回答"引擎的词汇表够不够"：不限时、不限轮，用现有基元和宏建出最好的模型，并列出表达不了的部件
depends_on: [PT, P4, P1, P2]
model: opus
lane: blender
allowed_files:
  - output/poc/ceiling/**
forbidden_files:
  - "*"
inputs:
  - output/poc/baseline/<pic>/box_fit.json（相机）；拟合差时可重点角点重拟合
  - booth-image-to-model/templates/build_spec_template.py、references/component-recipes.md、references/data-contract.md
  - input/poc/pic_A 到 pic_C
deliverables:
  - output/poc/ceiling/<pic>/build_spec.py、spec.json、previews/、timing.json
  - output/poc/ceiling/missing-vocabulary.md
  - output/poc/ceiling/<pic>/score.json（指挥官跑）
tests_first: []
acceptance_cmds:
  - python booth-image-to-model/scripts/validate_spec.py --help
effort_h: 12
day: 3-4
stop_rule: 连续两轮预览没有肉眼可见的改进就停；第 4 天中午前必须交 pic_A，B 和 C 可以粗一些
---

# P5 天花板 C

## 目标

一个慢 agent（Opus）用今天的全部基元（box、cylinder、rounded_ring、rounded_portal、text、swept_strip、profile_extrusion、arc_panel）和宏，不限时间、不限轮数，把目标图建到建不动。它的结果 C 是后面每一步要追的伪真值；它建不出来的东西就是第 3 步的范围。

## 步骤

1. 从 P4 的 `box_fit.json` 取相机；拟合误差大就重点角点重拟合，记录原因。
2. 复制 `build_spec_template.py` 到 `output/poc/ceiling/<pic>/build_spec.py`，用 `SpecBuilder` 和宏写 spec；文字用占位字（同位同尺寸，不抄原文）；道具用占位块。
3. 每轮：拿令牌，`boothctl.py preview --client-look`，释放令牌，看叠图，改 spec。每轮用时记进 `timing.json`。
4. 每遇到一个造不出的东西，立刻写进 `missing-vocabulary.md` 一行：特征、现在用什么凑的、差在哪、建议的家族名、哪张图需要。表头：`| 特征 | 现在的凑法 | 差在哪 | 建议家族 | 图 |`。
5. 停止后，指挥官用面板和 `score.py` 打分；G2 由决策人在 `pic_A` 上做校准。

## 规则

- 不读 `output/poc/truth/`，不读评委提示词。
- 不改 skill 的文件；想改就写进 `missing-vocabulary.md`。
- 每次只持令牌一次预览的时间；每天给全量回归留 30 分钟窗口。

## 完成的定义

三张图各有 C 和预览；`missing-vocabulary.md` 每行都有建议家族；`pic_A` 的 `score.json` 在 G2 前可用。

## 验证者清单

指挥官检查：spec 通过 `validate_spec.py --check-source`；没有新的 `.py` 文件进 skill；`missing-vocabulary.md` 的每一行都能对应到原图上的一个可见特征。
