---
id: PT
title: 答案卷：五张图的部件清单、留出点、色块
goal: 在跑任何模型之前，为每张图写死评分用的真值，由决策人确认
depends_on: []
model: sonnet
lane: owner
allowed_files:
  - output/poc/truth/**
forbidden_files:
  - "*"
inputs:
  - input/poc/pic_A 到 pic_C（第 1 天）；pic_D、pic_E（第 10 天当天）
  - docs/poc/03-scoring-spec.md 第 2 节
  - docs/poc/templates/answer-sheet.example.json
deliverables:
  - output/poc/truth/<pic>/inventory.json
  - output/poc/truth/<pic>/holdout_px.json
  - output/poc/truth/<pic>/swatches.json
  - output/poc/truth/<pic>/confirmed.txt（决策人确认的日期和方式）
tests_first: []
acceptance_cmds:
  - python -m json.tool output/poc/truth/pic_A/inventory.json
  - python -m json.tool output/poc/truth/pic_A/holdout_px.json
  - python -m json.tool output/poc/truth/pic_A/swatches.json
effort_h: 2
day: 1
stop_rule: 决策人当天没确认就用执行人的草稿先跑 P4，日报里标"答案卷未确认"，G2 前必须确认
---

# PT 答案卷

## 目标

评分的真值在看到任何输出之前写死：部件清单、4 个留出点、3 到 5 个色块。一个专用 Sonnet 子 agent 起草（不是跑 P4 的那个），指挥官只把文件转交决策人、不读内容；决策人看一眼确认，约 10 分钟一张图。不是数据集标注：没有掩码，没有逐件画框。

## 输入

- 图片在 `input/poc/`，文件名 `pic_A.<ext>` 到 `pic_E.<ext>`；`pic_D`、`pic_E` 第 10 天才打开。
- 格式见 [03 第 2 节](../03-scoring-spec.md#2-答案卷) 和 [templates/answer-sheet.example.json](../templates/answer-sheet.example.json)。

## 步骤

1. 看图，列 8 到 15 个部件：`{id, name, kind_hint, hero}`，`hero` 标 2 到 3 个主件。`kind_hint` 用现有家族名或 `unknown`。
2. 选 4 个留出点：不同部件、不同深度，角点清晰可见；每个写 `part_id`、`corner`、`uv_px`，并各裁一张 80 px 的小图放在同一文件夹（只给决策人看，不给模型）。
3. 选 3 到 5 个色块：主色、辅色、强调色、屏幕各一块，框在原图上颜色均匀的区域。
4. 把三个 JSON 和小图发给决策人，决策人回复"确认"或改动；把确认方式和日期写进 `confirmed.txt`。
5. 这个文件夹只有评分工具（`score.py`、`panel.py`、`perturb_spec.py`、`rank_check.py`）读；P4、P5、P5b 的建模 agent 和 P8 的读图、批评、修改不得读它。

## 允许改的文件

只有 `output/poc/truth/`。

## 禁止改的文件

其他一切。

## 验收命令

三个 JSON 能被 `python -m json.tool` 解析；字段与模板一致（P1 合并后用 `python tools/poc/score/score.py --validate-truth output/poc/truth/pic_A` 再查一次）。角名再用 `--validate-truth output/poc/truth/<pic> --camera output/poc/ceiling/<pic>/spec.json --map output/poc/ceiling/<pic>/truth_map.json` 查余量（03 第 2 节；2026-10-23 补）：名字按 `note` 的意思选，报告只用来确认它有余量、落在 `note` 说的那个角上，不许按误差挑名字。

## 完成的定义

`pic_A`、`pic_B`、`pic_C` 三个文件夹齐全并有 `confirmed.txt`。

## 验证者清单

本包无代码；指挥官检查每张图的留出点是否在不同部件上、色块是否在渲染会覆盖到的区域内。
