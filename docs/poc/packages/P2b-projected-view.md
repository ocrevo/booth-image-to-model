---
id: P2b
title: 评委看投影版：L4 的面板项用投影版渲染的叠图
goal: score.py 和 panel.py 能把投影版预览做成第二张无字叠图，评委只在 L4 的 5 项上看它，L1 到 L3 仍看中性版
depends_on: [P1, P2]
model: sonnet
lane: worker
allowed_files:
  - tools/poc/score/score.py
  - tools/poc/score/panel.py
  - tools/poc/prompts/judge.md
  - tools/poc/tests/test_score.py
  - tools/poc/tests/test_panel.py
forbidden_files:
  - booth-image-to-model/**
  - web/**
  - tools/poc/loop/**
inputs:
  - docs/poc/03-scoring-spec.md 第 1 节（L4 外观一行）和第 4 节第 2 条（2026-10-23 补充裁定）
  - docs/poc/02-architecture-and-contracts.md 第 4.4、4.5、4.10 节
  - tools/poc/score/score.py 的 panel_view.png 生成和 --run 的读取；panel.py prepare 的请求格式
deliverables:
  - score.py --projected-run <预览文件夹>：可选；两个文件夹的 spec.json 字节相同、画布相同才接受，否则退出码 2；写 panel_view_projected.png（同样三格、无字）；score.json 的 aux 记 l4_panel_render（projected 或 neutral）
  - panel.py prepare：候选文件夹里有 panel_view_projected.png 时，评分请求多带一张盲化后的图，请求里写明 L4 的 5 项看这张；没有时照旧，请求不变
  - judge.md：L1 到 L3 看第一张，L4 的 5 项看第二张（有的话）；仍只允许 judge.v1 的封闭答案
  - tally 不变；没有 --projected-run 时所有输出与今天逐字节相同
tests_first:
  - test_projected_run_must_have_the_same_spec_and_canvas
  - test_projected_run_writes_a_second_panel_view_without_text
  - test_without_projected_run_outputs_are_unchanged
  - test_prepare_adds_the_projected_view_only_for_l4_items
acceptance_cmds:
  - python -m unittest discover -s tools/poc/tests -p "test_score*.py"
  - python -m unittest discover -s tools/poc/tests -p "test_panel*.py"
  - python tools/poc/score/score.py --help
  - python tools/check_docs.py
  - python tools/check_lf.py
effort_h: 3
day: 6
stop_rule: 做不完就交已通过的测试和 notes；G3 的 C′ 打分前必须合并，否则 L4 面板仍看中性版并在关口记录里注明
---

# P2b 评委看投影版

## 目标

03 第 1 节规定 L4 的面板分看投影版渲染，可评分工具只读一个预览文件夹，给评委的叠图来自中性版。本包补上第二张叠图。投影版的 spec 文件和中性版逐字节相同（投影只在内存里改），所以两个预览文件夹的 `spec.json` 必须相同，这是"同一个模型"的检查。

时机：和 G1 的评分方法改动动的是同一批文件，指挥官在那些改动合并之后再派；G3 的 C′ 打分之前合并。

## 输入

前言 `inputs`。

## 交付物

1. `score.py --projected-run <dir>`：可选参数。读 `<dir>/preview_render.png` 和 `<dir>/spec.json`；`spec.json` 与 `--run` 的字节不同，或画布尺寸不同，退出码 2，不写任何分数。合格时在 `panel_view.png` 旁边写 `panel_view_projected.png`，做法与 `panel_view.png` 相同（原图、渲染、叠加三格，不带字、分数和标记）。代码分（含 L4 的色差）只按 `--run` 的中性版算。`score.json` 的 `aux.l4_panel_render` 写 `projected` 或 `neutral`。
2. `panel.py prepare`：候选文件夹里有 `panel_view_projected.png` 时，评分模式的每份请求多一张盲化后的图（随机名，与第一张的名字无关），请求里写明 L4 的 5 项看它；没有时请求与今天完全相同。
3. `judge.md`：说清两张图各看哪几项；其余不变。
4. 不加 `--projected-run`、候选文件夹里没有第二张图时，所有输出与今天逐字节相同。

## 允许改的文件

前言 `allowed_files`。

## 禁止改的文件

skill 本体、`web/`、闭环代码。

## 先写的测试

前言四个测试先提交。测试用临时目录和程序生成的小图。

## 验收命令

前言 `acceptance_cmds`。

## 完成的定义

四个测试通过，验收命令退出码 0，`worker.v1` 回报。

## 验证者清单

1. 白名单、先红后绿。
2. 变异：把 spec 字节比较去掉，必须有测试变红；把 L4 色差改成按投影版算，必须有测试变红。
3. 盲化：第二张图的文件名、元数据里没有候选名、`projected` 字样或分数。
4. 不带新参数时，`score.json`、`panel_view.png`、请求文件与改动前逐字节相同。
