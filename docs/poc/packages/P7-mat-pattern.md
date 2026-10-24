---
id: P7-mat-pattern
title: 材质图案 dots 和 grain：带渐变的穿孔圆点、地毯颗粒
goal: 清单第 12 行（成行或渐变分布的圆点，贴在弧面、立面和底面上）和第 11 行（地毯颗粒）能在 spec 里表达，并经设计路线用上
depends_on: [P7-render-dark, P7-fam-canopy, P7-fit-camera, P5]
model: sonnet
lane: worker
allowed_files:
  - booth-image-to-model/scripts/material_patterns.py
  - booth-image-to-model/scripts/validate_spec.py
  - booth-image-to-model/scripts/blender_build.py
  - booth-image-to-model/schemas/booth-spec-v2.schema.json
  - booth-image-to-model/scripts/blender_verify.py
  - booth-image-to-model/references/data-contract.md
  - booth-image-to-model/tests/test_material_patterns.py
forbidden_files:
  - booth-image-to-model/scripts/components.py
  - booth-image-to-model/scripts/motifs.py
  - booth-image-to-model/scripts/design_compile.py
  - booth-image-to-model/scripts/geometry.py
  - booth-image-to-model/schemas/booth-design.v1.json
  - booth-image-to-model/CHANGELOG.md
  - web/**
  - tools/poc/**
inputs:
  - output/poc/ceiling/missing-vocabulary.md 第 11、12 行的"特征"和"差在哪"（指挥官贴进 brief）
  - material_patterns.PATTERNS（P7-mat-projected 加的分发）和 grid 的坐标模式（auto、xy、xz、yz、arc_length_z）
  - P7-fam-canopy 加的图案透传：设计元素的 material.pattern 和 style.floor_pattern 原样进 spec
deliverables:
  - pattern 新类型 dots：{type dots, spacing [u, v]（米）, radius_m, dot_color [r, g, b], coordinates, offset, fade}；fade 为 {axis x、y 或 z, from_m, to_m}（圆点半径从 from_m 处的 radius_m 线性缩到 to_m 处的 0）或 null（铺满）
  - 坐标模式 box：按每个着色点法线的主轴选平面，一个部件的立面和底面都铺得上圆点；dots 可用 auto、xy、xz、yz、arc_length_z、box
  - pattern 新类型 grain：{type grain, scale_m, strength（0 到 1）, grain_color [r, g, b]}：噪声把部件颜色和 grain_color 混合
  - 两种类型进 material_patterns.PATTERNS；validate_spec 和 schema 的分支；节点描述无 bpy 可测；不用外部图片
tests_first:
  - test_dots_pattern_validates_and_rejects_bad_values
  - test_dots_fade_shrinks_radius_along_axis
  - test_box_coordinates_pick_the_plane_by_normal
  - test_grain_pattern_validates_and_rejects_bad_values
  - test_grain_node_tree_is_built_from_params
  - test_grid_pattern_signature_unchanged
acceptance_cmds:
  - BLENDER_EXE= python -m unittest discover -s booth-image-to-model/tests -p "test_material_patterns.py"
  - BLENDER_EXE= python -m unittest discover -s booth-image-to-model/tests -p "test_reusable_geometry.py"
  - BLENDER_EXE= python -m unittest discover -s booth-image-to-model/tests -p "test_group_glb.py"
  - BLENDER_EXE= python -m unittest discover -s booth-image-to-model/tests -p "test_quality_gate.py"
  - python tools/check_docs.py
  - python tools/check_lf.py
effort_h: 5.5
day: 7
stop_rule: 不降级：不得用网格线、另放的薄片或单色近似圆点和颗粒；做不完就交已通过的测试和 notes，第 11、12 行在还原记录里保持"计划"，指挥官报决策人（G3 可能推到第 8 天）
---

# P7-mat-pattern 圆点和颗粒

## 目标

链 B 的最后一个包，补清单第 11、12 行（只有部分图需要）。第 12 行：吊挂圆角环形顶的外立面和底面、门架腿、圆展台上的穿孔圆点，成行排列，在立面上只出现一段并渐变消失；今天只有网格线，图案铺满整件，只好另放一层薄片。第 11 行：地毯的颗粒；今天地面是一块均匀颜色，大面积看起来像塑料。

原 P7-mat-gradient 卡的单独两色渐变不做：清单里没有哪一行单独要它，渐变变成圆点的 `fade`。条纹也不做（它原是第 10 行的退路，已取消）。

依赖 [P7-fam-canopy](P7-fam-canopy.md)：它把设计里的 `material.pattern` 和 `style.floor_pattern` 透传进 spec，本包的证据要经设计路线出。

时机：G2 通过前只写 `tests_first` 的测试并提交到分支，不实现、不合并；G2 通过后再实现、验证、出证据、合并。

## 输入

前言 `inputs`。

## 交付物

1. `dots`：圆点按 `spacing` 排成行列，半径 `radius_m`，颜色 `dot_color`，其余是部件自己的颜色；`offset` 平移网格；`fade` 不为 null 时，沿 `axis`（部件的物体坐标）从 `from_m` 到 `to_m` 把圆点半径线性缩到 0，范围外的一侧保持全尺寸、另一侧没有圆点。
2. 坐标模式 `box`：每个着色点按法线绝对值最大的轴选平面（法线主轴 x 用 yz，y 用 xz，z 用 xy），这样一个 `rounded_ring` 的立面和底面都铺得上圆点；`arc_length_z` 照 grid 的规则只用于 `arc_panel`。
3. `grain`：Blender 的噪声纹理，尺度 `scale_m`，把部件颜色和 `grain_color` 按 `strength` 混合；用在地面时经 `style.floor_pattern`。
4. 两种类型的参数函数和节点函数进 `material_patterns.PATTERNS`；`validate_spec.py` 和 schema 加分支；节点树构造抽成无 `bpy` 时返回描述对象的函数，测试测参数到描述的映射。
5. `blender_verify.py` 的独立复核按 `PATTERNS` 分发：spec 里出现 `dots`、`grain` 或 `projected` 时不崩，比对各自的节点签名（今天它对每个部件都调 `apply_grid`，碰到非 grid 图案会抛 ValueError；2026-10-23 由 P7-mat-projected 的验证发现）。投影版构建（`build_report.json` 的 `material_set` 是 `projected`，或 spec 里有 `projected` 部件）：复核按 `material_patterns.material_set(spec, build_report 的 material_set)` 得到的副本比对，投影部件上的 UV Project 修改器算预期的修改器，不报 `unreviewed_modifiers`。`PATTERNS` 的条目可以从二元组扩成带签名函数的三元组或命名元组，测试随之改，但不得放宽'grid 签名不变'的断言。`references/data-contract.md` 的 pattern 一节写上新类型和复核规则。
6. 接手 P7-render-dark 合并时留下的两处（2026-10-23）：用假 bpy 端到端测 `build_report.json` 的 `look_unhandled` 非空时确实写出（今天只测了空列表，写死成空列表的变异会漏网）；`references/data-contract.md` 里'dark_hall 配 Workbench 一律拒绝'改准确：开着 `--preview-client-look` 时走 Cycles，这时不拒绝。
7. 已知限制写进 notes（指挥官合并时写进 CHANGELOG）：网页编辑器只按 `grid` 的字段缩放图案，新类型在编辑器里缩放时不跟着变。

## 允许改的文件

前言 `allowed_files`。链 B 租约。设计 schema（`booth-design.v1.json`）属于链 A，本包不碰。

## 禁止改的文件

前言 `forbidden_files`。

## 先写的测试

前言 `tests_first`，先提交（提交信息 `P7-mat-pattern: tests first`），在基线提交上必须失败。

## 验收命令

前言 `acceptance_cmds`，全部退出码 0。

## 完成的定义

1. 先写的测试和验收命令全部通过；`grid` 的签名和已有测试不变。
2. 证据：按 [05 第 3 节第 2 步](../05-gates-and-closing.md#3-g3-引擎够用止损点二第-7-天)，合并前由 Blender 通道用本包分支的代码，经设计路线（`material.pattern`、`style.floor_pattern`）出 Cycles 预览；独立核对 agent 逐条判，全为"是"才写 `pass`。
   - 第 11 行，放进 `output/poc/ceiling2/evidence/11/`（需要这一行的每张图各一张）：地毯近处看得见深浅不一的颗粒，不是一块均匀颜色。
   - 第 12 行，放进 `output/poc/ceiling2/evidence/12/`（需要这一行的那张图）：环的外立面和底面上是成行的圆点，不是网格线；立面上的圆点只在沿长度方向的一段出现，并沿长度方向逐渐变小直到消失，不是整圈都有、只在高度上渐变；门架腿上有圆点；展台本体的侧面上有圆点，和原图一样（没有同形状的家族时，可以用最接近的展台家族替代形状，替代记进还原记录（`06-fidelity-ledger.md`，只在本地））；图案画在部件本身上，没有另放一层薄片。
3. 不得用降级办法凑数，例如网格线、另放的薄片、单色或一张外部图片；用了就判 `fail`。
4. 合并后指挥官把还原记录（`06-fidelity-ledger.md`，只在本地）第 11、12 行的状态改为"已验证"。

## 验证者清单

1. 白名单；链 B 租约只在本包手里。
2. 先红后绿。
3. 变异：把 `fade` 的半径缩放去掉，`test_dots_fade_shrinks_radius_along_axis` 应变红；把 `box` 模式固定成 `xz`，`test_box_coordinates_pick_the_plane_by_normal` 应变红。
4. 破坏性输入：`radius_m` 不小于 `spacing` 较小值的一半必须拒绝；`fade.from_m` 等于 `to_m` 必须拒绝；`strength` 不在 0 到 1 必须拒绝。
5. 证据由 Blender 通道出、核对 agent 判，不由本验证者判；两行的 `verdict.json` 都为 `pass` 之前不合并。
