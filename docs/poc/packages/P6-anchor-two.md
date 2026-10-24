---
id: P6
title: 锚点二台架：合成自博弈
goal: 在真值精确已知的合成场景上，能自动算出一个候选 spec 离真值多远，并画出逐轮曲线
depends_on: [P1]
model: sonnet
lane: worker
allowed_files:
  - tools/poc/loop/__init__.py
  - tools/poc/loop/selfplay.py
  - tools/poc/tests/test_selfplay.py
forbidden_files:
  - booth-image-to-model/**
  - web/**
  - tools/eval/**
inputs:
  - docs/poc/03-scoring-spec.md 第 6 节
  - tools/eval/synth_generate.py、synth_render.py、metrics.py（只调用，不改）
  - booth-image-to-model/scripts/compare.py（compare_masks）
deliverables:
  - tools/poc/loop/selfplay.py：prepare、render、perturb、evaluate（--map、--match nearest）、curve，以及 03 第 6 节裁定后加的 mask-spec、truth-sheet
tests_first:
  - test_truth_candidate_has_zero_placement_error
  - test_moved_part_error_is_reported_in_metres
  - test_mask_iou_uses_instance_ids_from_mask_index
  - test_curve_marks_improvement_only_when_both_metrics_improve
acceptance_cmds:
  - python -m unittest discover -s tools/poc/tests -p "test_selfplay*.py"
  - python tools/poc/loop/selfplay.py --help
  - python tools/check_docs.py
  - python tools/check_lf.py
effort_h: 5
day: 4
stop_rule: render 子命令只是 synth_render.py 的包装，实在来不及可以留给 Blender 通道直接调用原脚本
---

# P6 锚点二台架

## 目标

实现 [03 第 6 节](../03-scoring-spec.md#6-锚点二合成自博弈) 的台架。闭环本身是 P8a；本包只提供场景、真值、误差和曲线。

## 交付物

```bash
python tools/poc/loop/selfplay.py prepare --out <dir> --cases 3 --seed 1      # 调 synth_generate --suite smoke
python tools/poc/loop/selfplay.py render --case <case_dir> --out <dir>        # 调 synth_render（Blender 通道）
python tools/poc/loop/selfplay.py perturb --case <case_dir> --seed 1 --out <spec.json>   # 真值 spec 加随机扰动，只用来验证台架本身（03 第 6 节第 6 条：不跑无读图器的扰动闭环）
python tools/poc/loop/selfplay.py evaluate --case <case_dir> --candidate <spec.json> [--masks <part_masks_dir>] --out <json>
python tools/poc/loop/selfplay.py curve --case <case_dir> --rounds <dir> --out <curve.json>   # 另写 curve.png
```

- `evaluate`：部件中心误差用 `tools/eval/metrics.py` 的 `placement_errors`；有 `--masks` 时逐件 IoU 用 `compare.py` 的 `compare_masks`，真值掩码按 `mask_index.json` 从 `mask.png`（id = R + 256 × G）解出。
- `perturb`：对真值 spec 的每个可动部件随机移动 0.1 到 0.5 米、随机删一个非墙部件、随机换一个颜色；写 `perturbation.json` 记录做了什么。
- `curve`：读每轮的 evaluate 结果，判断"第 4 轮优于第 1 轮，且没有一轮比前一轮差超过 10 %"。

## 允许改的文件

前言 `allowed_files`。只 `import` 或 `subprocess` 调用 `tools/eval` 和 skill 的脚本。

## 禁止改的文件

skill 本体、`tools/eval/`、`web/`。

## 先写的测试

前言四个测试先提交；`mask.png` 用程序生成的小图。

## 验收命令

前言 `acceptance_cmds`。

## 完成的定义

对程序夹具：真值候选误差为 0；移动 0.3 米的部件报出约 0.3 米；曲线判定正确。

## 验证者清单

1. 白名单。
2. 先红后绿。
3. 变异：把 `evaluate` 里的米换成厘米，`test_moved_part_*` 应变红。
4. 破坏性输入：候选缺一个真值部件时误差必须记为"缺失"而不是 0。
