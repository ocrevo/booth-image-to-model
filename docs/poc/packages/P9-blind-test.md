---
id: P9
title: 盲测：两张没见过的图
goal: 证明结果不是只为前三张图调的
depends_on: [P8d]
model: sonnet
lane: blender
allowed_files:
  - output/poc/truth/pic_D/**
  - output/poc/truth/pic_E/**
  - output/poc/loop/pic_D/**
  - output/poc/loop/pic_E/**
  - output/poc/memo/**
forbidden_files:
  - "*"
inputs:
  - input/poc/pic_D、pic_E（第 10 天才打开）
deliverables:
  - 两张图的答案卷（当天起草，决策人确认）、闭环结果、score.json
  - output/poc/memo/blind-test.md：中位数、最低分、并排图路径
tests_first: []
acceptance_cmds:
  - python tools/poc/loop/orchestrator.py --help
effort_h: 4
day: 10
stop_rule: 一张图卡住就先出另一张的结果；日报里说明
---

# P9 盲测

## 步骤

1. 打开 `pic_D`、`pic_E`，按 [PT](PT-truth-sheets.md) 起草答案卷，决策人确认。
2. 点角点、`box_fit.py` 拟合（和 P4 一样的做法），然后按 [P8d](P8d-real-pictures.md) 的命令跑闭环。
3. 完整 build 加 `--part-masks` 留作记录；打分用预览：中性版给 `score.py --run`（必须同时给这张图的 `--fit`，03 第 3.1 节第 3 条），投影版给 `--projected-run`（03 第 4 节第 2 条）；`--look dark_hall` 只出对比预览，不进打分（2026-10-23 改：暗场只限预览，完整 build 的渲染 score.py 读不了）。
4. 并排图：原图、投影版渲染、中性版渲染三张并排，两组，给决策人。
5. `blind-test.md`：两张总分、中位数、最低分、用时。

## 完成的定义

`gates.json` 的 G5 有 `value` 和证据路径；决策人看过并排图。

## 验证者清单

指挥官检查：答案卷在闭环开始之前写好并确认；两张图在第 10 天前没有被任何人或 agent 打开（看板里记打开时间）。
