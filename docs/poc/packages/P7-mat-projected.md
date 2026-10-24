---
id: P7-mat-projected
title: 材质 pattern projected 和按类型分发图案：原图按拟合相机投影到模型
goal: 投影版材质能出图，用于对比还原度；材质图案改成按类型分发，链 B 后面的图案只加条目；preview 能透传 --material-set
depends_on: [P7-fit-camera, P5]
model: sonnet
lane: worker
allowed_files:
  - booth-image-to-model/scripts/material_patterns.py
  - booth-image-to-model/scripts/validate_spec.py
  - booth-image-to-model/scripts/blender_build.py
  - booth-image-to-model/scripts/boothctl.py
  - booth-image-to-model/schemas/booth-spec-v2.schema.json
  - booth-image-to-model/references/data-contract.md
  - booth-image-to-model/tests/test_material_patterns.py
forbidden_files:
  - booth-image-to-model/scripts/components.py
  - booth-image-to-model/scripts/motifs.py
  - booth-image-to-model/scripts/design_compile.py
  - booth-image-to-model/scripts/geometry.py
  - booth-image-to-model/CHANGELOG.md
  - web/**
  - tools/poc/**
inputs:
  - material_patterns.py 的 grid_parameters、apply_grid、grid_graph_signature；validate_spec.py 和 blender_build.py 里调用它们的地方
  - blender_build.py 里 source 图片的加载和打包（它已把原图打包进 .blend 但不渲染）；spec.camera 的字段（references/data-contract.md）
  - boothctl.py 的 preview 函数（拼 Blender 命令行）和 build 子命令（build 把 -- 之后的参数透传给 blender_build.py）
deliverables:
  - material_patterns.PATTERNS：{type: (参数函数, 节点函数)}；validate_spec 和 blender_build 都经它分发；grid 走原路径，节点签名不变；未知类型的错误信息列出已知类型
  - pattern 新类型 {type projected}（无参数）：颜色来自 spec.source 的图片，UV 来自以 spec 相机为投影器的 UV Project 修改器；部件颜色的 alpha 保留（玻璃仍半透明）
  - validate_spec：projected 只在 camera.fit_status 不是 unfitted 时允许；schema 的 pattern 加 projected 分支
  - blender_build.py --material-set neutral|projected（缺省 neutral，即原行为）；build_report.json 记 material_set
  - material_patterns.material_set(spec, name)：纯函数，返回改写后的 spec 副本，projected 时每个部件的 pattern 换成 projected，neutral 时原样返回；不改 spec 文件
  - boothctl.py preview 加 --material-set 并透传；build 照旧用 -- 之后的参数透传
tests_first:
  - test_pattern_dispatch_by_type_keeps_grid_signature
  - test_unknown_pattern_type_lists_known_types
  - test_projected_requires_fitted_camera
  - test_material_set_projected_rewrites_every_part_in_memory_only
  - test_material_set_neutral_returns_the_spec_unchanged
  - test_projected_keeps_part_alpha
  - test_boothctl_preview_passes_material_set
acceptance_cmds:
  - BLENDER_EXE= python -m unittest discover -s booth-image-to-model/tests -p "test_material_patterns.py"
  - BLENDER_EXE= python -m unittest discover -s booth-image-to-model/tests -p "test_reusable_geometry.py"
  - BLENDER_EXE= python -m unittest discover -s booth-image-to-model/tests -p "test_group_glb.py"
  - BLENDER_EXE= python -m unittest discover -s booth-image-to-model/tests -p "test_quality_gate.py"
  - python booth-image-to-model/scripts/boothctl.py preview --help
  - python tools/check_docs.py
  - python tools/check_lf.py
effort_h: 7
day: 5
stop_rule: 不降级：不得退回只对渲染相机正确的 Window 坐标；UV Project 做不稳就交已通过的测试和 notes，指挥官报决策人
---

# P7-mat-projected 投影版材质和图案分发

## 目标

链 B 的第一个包。决策人定的两套材质之一（01 第 2 节"图形内容"）：投影版把原图按拟合相机投到模型上，用于对比还原度；正视角很像，第二视角里被遮挡的面会拉伸或缺失，接受。它不对应清单的任何一行，在还原记录（`06-fidelity-ledger.md`，只在本地）里是一行"替代"。

本包还做链 B 的分发：今天 `material_patterns.grid_parameters` 只认 `grid`，别的类型直接报错。改成按 `type` 查表后，[P7-mat-pattern](P7-mat-pattern.md) 只往表里加条目。另外把 `--material-set` 接进 `boothctl.py preview`，P5b 和闭环才能在预览里看投影版。

时机：G2 通过前只写 `tests_first` 的测试并提交到分支，不实现、不合并；G2 通过后再实现、验证、出证据、合并。

## 输入

前言 `inputs`。

## 交付物

1. 分发：`material_patterns.PATTERNS = {type: (参数函数, 节点函数)}`；`validate_spec.py` 的图案校验和 `blender_build.py` 的材质构建都经它分发。`grid` 的参数函数和节点函数就是今天的 `grid_parameters` 和 `apply_grid`，节点签名（`grid_graph_signature`）不变。未知类型报错，信息里列出已知类型。
2. `projected`：`{type: projected}`，无其他字段。`blender_build.py` 给每个网格部件加一个 UV Project 修改器（文字部件先转成网格再同样投影；转不了的隐藏，并以 `{id, reason}` 列进 `build_report.json` 的 `projected_uv_skipped`。2026-10-23 改：起初文字部件不投影，第二次独立核对发现它们取到原图一角的单个像素，成了深色平块），投影器是 spec 相机对象，宽高比按图片尺寸；Image Texture 节点接 `spec.source` 的图片。颜色的 alpha 照抄部件原来的 alpha（`finish: glass` 的部件仍半透明，清单第 9 行要用）。背面和被遮挡面得到错误像素，不处理。投影版只用于预览对比，过不了 `blender_verify.py` 的独立复核（文字变成网格、多了修改器），交付和复核都用中性版；`blender_verify.py` 怎样对待投影版构建由 P7-mat-pattern 交付物 5 处理。
3. `validate_spec.py` 和 schema：`pattern` 加 `projected` 分支；`camera.fit_status` 为 `unfitted` 时报错。
4. `--material-set neutral|projected`：`blender_build.py` 加参数，缺省 `neutral`；构建材质前调 `material_patterns.material_set(spec, name)` 得到内存里的副本，spec 文件不变；`build_report.json` 记 `material_set`。`material_set` 是纯函数，无 `bpy` 可测。投影的准备（加载 source 图片、UV 层、UV Project 修改器）按内存副本里有没有 `projected` 部件决定，不按材质套的名字，所以 spec 文件里自己写了 `projected` 的部件在缺省构建下也能建；`preview --material-set projected` 没给 `--engine` 时用 Cycles（Workbench 只显示部件的平色，看不到贴图），给了 `workbench` 时拒绝并说明原因；缺省 neutral 时传给 Blender 的命令行与原来逐字节相同（不追加 `--material-set neutral`）。（2026-10-23 补充，来自本包的验证）
5. `boothctl.py`：`preview` 加 `--material-set` 并放进 Blender 命令行；`build` 照旧用 `--` 之后的参数透传，不改。
6. 渲染效果由 Blender 通道出证据时看（见"完成的定义"）。

## 允许改的文件

前言 `allowed_files`。链 B 租约，含 `boothctl.py`。

## 禁止改的文件

前言 `forbidden_files`。`web/app/js/mesh.js` 忽略 `pattern`，不改。

## 先写的测试

前言 `tests_first`，先提交（提交信息 `P7-mat-projected: tests first`），在基线提交上必须失败。`test_boothctl_preview_passes_material_set` 替换 `subprocess.run`，只断言命令行，不启动 Blender。`blender_build.py` 在模块顶部导入 `bpy`，不要在测试里导入它；要测的逻辑放进 `material_patterns.py` 的纯函数。

## 验收命令

前言 `acceptance_cmds`，全部退出码 0。`test_reusable_geometry.py`、`test_group_glb.py`、`test_quality_gate.py` 是现有覆盖 `validate_spec` 和 `pattern` 的测试文件。

## 完成的定义

1. 先写的测试和验收命令全部通过；`neutral` 路径的 spec 和 `build_report.json` 的设置字段与以前一致（`test_material_set_neutral_returns_the_spec_unchanged`）。
2. 证据：本包不对应清单的行，证据放 `output/poc/ceiling2/evidence/projected/`。合并前由 Blender 通道用本包分支的代码，对目标图 P5 的 spec 的一个副本（用 `boothctl.py patch` 把一个台面部件颜色的 alpha 改成 0.35）出 `preview --engine cycles --material-set projected`；独立核对 agent 逐条判，全为"是"才写 `pass`：
   - 正视角的模型表面显示原图对应的像素，墙、门头、柜台和屏幕的位置与原图对得上；
   - 投影版和中性版的模型轮廓一致，原图像素只出现在模型的面上，轮廓外不是原图；
   - （原有一条'alpha 改成 0.35 的那个部件仍看得见后面的东西'于 2026-10-23 删去：投影器就是渲染相机，同一条视线上透明部件和它后面的面取到的是同一个原图像素，任何正确的实现在投影版正视角里都看不出透明，第一次独立核对因此判 `fail`，记录在 `evidence/projected/verdict_round1.json`。alpha 保留由 `test_projected_keeps_part_alpha` 和三轮验证用假 bpy 查过；看得见的透明由清单第 9 行玻璃的证据在中性版里验。）
3. 不得用降级办法凑数，例如把原图贴成整张背景、只对渲染相机正确的屏幕坐标；用了就判 `fail`。
4. 合并后指挥官把还原记录（`06-fidelity-ledger.md`，只在本地）"两套材质中的投影版"一行的状态改为"已验证"。

## 验证者清单

1. 白名单；链 B 租约（含 `boothctl.py`）只在本包手里。
2. 先红后绿。
3. 变异：把 `fit_status` 检查删掉，`test_projected_requires_fitted_camera` 应变红；把 alpha 固定成 1，`test_projected_keeps_part_alpha` 应变红；把分发表里 `grid` 的节点函数换掉，`test_pattern_dispatch_by_type_keeps_grid_signature` 应变红。
4. 破坏性输入：`source` 图片缺失时 build 必须以退出码 2 结束，而不是渲染成黑；`--material-set` 给未知值必须被 argparse 拒绝。
5. 证据由 Blender 通道出、核对 agent 判，不由本验证者判；`verdict.json` 为 `pass` 之前不合并。
