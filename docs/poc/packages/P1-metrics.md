---
id: P1
title: 评分指标：边缘距离、色差、留出点投影、score.py
goal: 把一个预览文件夹、原图和答案卷变成 score.json 和 sheet.png，代码指标可复现
depends_on: []
model: sonnet
lane: worker
allowed_files:
  - tools/poc/score/__init__.py
  - tools/poc/score/metrics.py
  - tools/poc/score/score.py
  - tools/poc/score/reader_schema.json
  - tools/poc/tests/test_metrics.py
  - tools/poc/tests/test_score.py
  - tools/poc/tests/fixtures/**
forbidden_files:
  - booth-image-to-model/**
  - web/**
  - tools/eval/**
inputs:
  - docs/poc/03-scoring-spec.md 第 1 到 3 节和第 7 节
  - docs/poc/02-architecture-and-contracts.md 第 4.1 和 4.5 节
  - booth-image-to-model/scripts/compare.py（复用 compare_masks 的距离变换做法）
  - booth-image-to-model/scripts/preview_compare.py（参照 sheet() 里轮廓和内边的做法，自行实现；它没有可导入的函数）
  - booth-image-to-model/scripts/camera_math.py（project）
deliverables:
  - tools/poc/score/metrics.py：edge_distance、holdout_errors、colour_de、booth_mask
  - tools/poc/score/score.py：--run --photo --truth --panel --out，另有 --validate-truth
  - tools/poc/score/reader_schema.json：reader.v1 的 JSON Schema，design 字段引用 booth-design.v1
  - tools/poc/tests/fixtures/：测试内生成的小图，不含任何真实图片
tests_first:
  - test_identical_render_scores_zero_edge_distance
  - test_shift_increases_edge_distance_monotonically
  - test_colour_de_detects_swatch_change
  - test_holdout_uses_project_and_reports_missing_part_as_zero
  - test_score_json_matches_contract_and_blocks_without_panel
  - test_reader_schema_rejects_extra_fields_inside_design
acceptance_cmds:
  - python -m unittest discover -s tools/poc/tests -p "test_metrics*.py"
  - python -m unittest discover -s tools/poc/tests -p "test_score*.py"
  - python tools/poc/score/score.py --help
  - python tools/check_docs.py
  - python tools/check_lf.py
effort_h: 8
day: 1-2
stop_rule: 第 2 天中午前边缘距离和留出点必须可用；色差可以推到第 2 天下午
---

# P1 评分指标与 score.py

## 目标

实现 [03 第 3 节](../03-scoring-spec.md#3-代码指标) 的三个代码指标和汇总脚本。复用可导入的函数（`compare.compare_masks`、`camera_math.project`），用 `import`；`preview_compare.py` 没有可导入的边缘函数，按它 `sheet()` 的做法（alpha 与平移副本异或、亮度梯度 > 28）在 `metrics.py` 里实现。依赖只有 numpy、scipy、Pillow、cv2（都已装）。

## 输入

- 预览文件夹：`preview_render.png`（源图尺寸的 RGBA）、`spec.json`、`projected_landmarks.json`，由 `boothctl.py preview` 产生；测试里用程序画出的小图代替。
- 原图：`--photo <path>`。
- 答案卷：`--truth <folder>`，见 [03 第 2 节](../03-scoring-spec.md#2-答案卷)。
- 面板响应：`--panel <folder>`，里面是 `judge.v1` 的 JSON；没有就 `status: blocked`。

## 交付物

`metrics.py` 暴露四个函数，`score.py` 是命令行入口：

```bash
python tools/poc/score/score.py --run <preview_dir> --photo <picture> --truth <truth_dir> --fit <box_fit.json> [--panel <panel_dir>] --out <score.json>
python tools/poc/score/score.py --validate-truth <truth_dir>
```

输出 `score.json`（契约 [02 第 4.5 节](../02-architecture-and-contracts.md#45-scorejson)）和同名的 `sheet.png`。

## 允许改的文件

前言 `allowed_files`。`reader_schema.json` 用 `$ref` 指向 `booth-image-to-model/schemas/booth-design.v1.json`，不复制它。

## 禁止改的文件

skill 本体、`web/`、`tools/eval/`。

## 先写的测试

前言的六个测试先提交。夹具在测试里生成：一张 320×200 的"原图"（几个有边的矩形）和一张同尺寸 RGBA"渲染"，平移后边缘距离必须单调增大。

## 验收命令

前言 `acceptance_cmds`，全部退出码 0。

## 完成的定义

三个指标对程序夹具给出预期的数；`score.json` 通过契约校验；没有面板时 `status` 为 `blocked` 且代码部分照常写出；`--validate-truth` 能拒绝缺字段的答案卷。

## 验证者清单

1. 白名单。
2. 先红后绿。
3. 变异：把 Canny 的高阈值改成 0，`test_shift_increases_edge_distance_monotonically` 应变红；把 `project` 的调用换成返回常量，`test_holdout_*` 应变红。
4. 破坏性输入：渲染和原图尺寸不同时必须报错退出码 2，不悄悄缩放；alpha 全透明的渲染必须 `blocked`。
