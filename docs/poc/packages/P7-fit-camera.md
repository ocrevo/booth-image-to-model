---
id: P7-fit-camera
title: design-compile 接受拟合相机：--fit 和 --image
goal: 让设计路线的 spec 用原图和拟合相机，从而能和原图叠图打分；不带新参数时输出逐字节不变
depends_on: []
model: sonnet
lane: worker
allowed_files:
  - booth-image-to-model/scripts/design_compile.py
  - booth-image-to-model/scripts/boothctl.py
  - booth-image-to-model/tests/test_design_compile.py
forbidden_files:
  - booth-image-to-model/scripts/draft_compile.py
  - booth-image-to-model/scripts/motifs.py
  - booth-image-to-model/schemas/**
  - booth-image-to-model/CHANGELOG.md
inputs:
  - booth-image-to-model/scripts/draft_compile.py 里已有的 --fit 处理（读 camera、dims_m、floor_z），照抄其做法
  - booth-image-to-model/references/data-contract.md（spec 的 camera 和 source 字段）
  - docs/poc/02-architecture-and-contracts.md 第 1 节第 2 步
deliverables:
  - boothctl.py design-compile 新增 --fit <box_fit.json> 和 --image <picture>（两者同时给或都不给）
  - design_compile.py：有 --fit 时相机逐字段复制（含 fit_status），form.footprint_m 被 dims_m 的 W、D 覆盖并在 uncertainties 里记为 fitted，floor_z 取 fit 的值，spec.source 指向 --image 的副本（尺寸取 fit.image），不再生成 design_canvas.png
tests_first:
  - test_fit_camera_is_copied_verbatim_including_fit_status
  - test_fit_dims_override_footprint_and_are_recorded_as_fitted
  - test_no_fit_output_is_byte_identical_to_before
  - test_fit_and_image_must_come_together
acceptance_cmds:
  - BLENDER_EXE= python -m unittest discover -s booth-image-to-model/tests -p "test_design_compile.py"
  - python booth-image-to-model/scripts/boothctl.py design-compile --help
  - python tools/check_docs.py
  - python tools/check_lf.py
effort_h: 5
day: 3
stop_rule: 第 3 天内必须合并；它卡住所有 P7 和 P8a
---

# P7-fit-camera

## 目标

今天 `design-compile` 自己生成一个相机和一张 1280×800 的灰画布当 spec 的 source，所以它的 spec 不能和原图比。本包加 `--fit` 和 `--image`，让闭环（读图器填 `design`，代码编译）产出的 spec 能被 `preview` 按原图尺寸渲染、被 `score.py` 打分。这是第 3 步之前唯一允许的引擎改动，没有参数时的行为必须逐字节不变。

## 做法

1. 在 `boothctl.py` 的 `design-compile` 子命令加 `--fit` 和 `--image`，透传给 `design_compile.py`。
2. 在 `design_compile.py`：有 `--fit` 时，读 `camera`、`dims_m`、`floor_z`、`image`，照 `draft_compile.py` 的做法；相机原样复制；`form.footprint_m` 改为 `[W, D]` 并在 spec 的 `uncertainties` 里记一条 `footprint: fitted`；`source` 取 `--image` 的副本（放进 `--out`），`width`、`height` 取 `fit.image`；不写 `design_canvas.png`。
3. `compile_report.json` 加一个 `fit` 字段，记 `box_fit.json` 的路径和 `status`。
4. 不改 `motifs.py`、schema、`draft_compile.py`。

## 允许改的文件

前言 `allowed_files`；`boothctl.py` 只加两个 `add_argument` 和透传。

## 禁止改的文件

前言 `forbidden_files`。CHANGELOG 由指挥官在合并时写。

## 先写的测试

前言四个测试。`test_no_fit_output_is_byte_identical_to_before` 在基线提交上先算一次模板设计的 `spec.json` 哈希写进测试常量，分支上必须相等。

## 验收命令

前言 `acceptance_cmds`。`BLENDER_EXE=` 把变量置空，Native 测试自动跳过；PowerShell 下写 `$env:BLENDER_EXE=''; python ...`。

## 完成的定义

四个测试通过；`design-compile --fit --image` 产出的 spec 能被 `validate_spec.py --check-source` 通过（指挥官在主工作区用 P4 的 `box_fit.json` 验证一次）。

## 验证者清单

1. 白名单。
2. 先红后绿。
3. 变异：把相机复制改成只复制 `position`，`test_fit_camera_is_copied_verbatim_*` 应变红。
4. 破坏性输入：`--fit` 指向 `status` 不是 `fitted` 的文件必须退出码 2；`--image` 尺寸与 `fit.image` 不符必须退出码 2。
