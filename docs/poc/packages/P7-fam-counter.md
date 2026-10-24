---
id: P7-fam-counter
title: 家族 shaped_counter：平面圆头柜台，柜身带几何上的横向凹槽
goal: 设计路线能表达清单第 10 行：柜台圆头端面上的横向凹槽是真的槽，侧光下看得见
depends_on: [P7-fam-propblock, P7-fit-camera, P5]
model: sonnet
lane: worker
allowed_files:
  - booth-image-to-model/scripts/motifs.py
  - booth-image-to-model/schemas/booth-design.v1.json
  - booth-image-to-model/scripts/design_check.py
  - booth-image-to-model/tests/test_design_compile.py
  - booth-image-to-model/tests/test_design_check.py
  - booth-image-to-model/references/component-recipes.md
forbidden_files:
  - booth-image-to-model/scripts/design_compile.py
  - booth-image-to-model/scripts/components.py
  - booth-image-to-model/scripts/geometry.py
  - booth-image-to-model/CHANGELOG.md
  - web/**
  - tools/poc/**
inputs:
  - output/poc/ceiling/missing-vocabulary.md 第 10 行的"特征"和"差在哪"（指挥官贴进 brief）
  - references/component-recipes.md 里 P7-fam-canopy 写的"链 A 怎么加新 kind"
  - motifs.py 的 counter builder；geometry.py 的 profile_extrusion
deliverables:
  - 新 kind shaped_counter（落地件）：size_m（长、深、高），params round_ends（left、right 的子集，圆头半径等于深度的一半）、groove_pitch_m、groove_gap_m、groove_depth_m、top_m
  - 槽是几何：柜身由一根平面轮廓内缩 groove_depth_m 的芯和一摞完整轮廓的片组成，片厚等于 groove_pitch_m 减 groove_gap_m，片间留 groove_gap_m 的空，芯在空里露出成为槽底；顶上一块 top_m 厚的台面
  - motifs.py 的 builder 和 BUILDERS、PARAM_CHECKS、ROLE、DEFAULT_SIZE、SCOPE、KINDS 条目；schema 的 kind 枚举和 params；references/component-recipes.md 一节
tests_first:
  - test_shaped_counter_round_end_follows_half_depth
  - test_shaped_counter_grooves_are_gaps_between_slabs_over_a_core
  - test_shaped_counter_uses_no_material_pattern_for_grooves
  - test_shaped_counter_part_count_is_slabs_plus_core_and_top
  - test_shaped_counter_rejects_bad_params
  - test_bare_shaped_counter_compiles_with_assumed_defaults
  - test_every_kind_covers_shaped_counter
  - test_design_check_runs_on_a_spec_with_shaped_counter
  - test_existing_kinds_output_unchanged
  - test_a_lit_panel_without_height_too_close_to_the_limit_is_refused_at_validation
  - test_a_lit_panel_corner_radius_above_the_default_height_is_refused_at_validation
  - test_a_dropped_oversize_part_names_the_largest_size_that_fits
  - test_a_thick_lit_panel_is_never_told_a_width_below_its_minimum
acceptance_cmds:
  - BLENDER_EXE= python -m unittest discover -s booth-image-to-model/tests -p "test_design_compile.py"
  - BLENDER_EXE= python -m unittest discover -s booth-image-to-model/tests -p "test_design_check.py"
  - BLENDER_EXE= python -m unittest discover -s booth-image-to-model/tests -p "test_studio_macros.py"
  - python -m unittest discover -s tools/poc/tests -p "test_roles*.py"
  - python tools/check_docs.py
  - python tools/check_lf.py
effort_h: 4
day: 6-7
stop_rule: 不降级：不得退回材质条纹或贴图线条；切片做不稳就交已通过的测试和 notes，第 10 行在还原记录里保持"计划"，指挥官报决策人（G3 可能推到第 8 天）
---

# P7-fam-counter 圆头柜台和真凹槽

## 目标

补清单第 10 行：接待台圆头端面上的横向凹槽。今天柜台只有方角的光面实体；`material.pattern` 只能画表面颜色线，侧光下看不出槽。本包让柜台在平面上可以是圆头，柜身的槽是几何上的空隙。原卡里另外几样柜台细节都去掉：玻璃台面用设计里的 `finish: glass`（清单第 9 行，由 P5b 出证据），其余不在清单里。

时机：G2 通过前只写 `tests_first` 的测试并提交到分支，不实现、不合并；G2 通过后再实现、验证、出证据、合并。

## 输入

前言 `inputs`。

## 交付物

`shaped_counter` 是一个元素（同一 `group`），只用现有基元，按 [P7-fam-canopy](P7-fam-canopy.md) 写在 recipes 里的办法往 `motifs.py` 的表里加条目。

1. 平面轮廓：长 × 深的矩形，`round_ends` 里列出的一端换成半径为深度一半的半圆。
2. 柜身：芯是平面轮廓向内缩 `groove_depth_m` 后竖直挤出的整块（`profile_extrusion`）；片是完整轮廓挤出的薄片，从地面往上按 `groove_pitch_m` 一片一片叠，片厚 `groove_pitch_m` 减 `groove_gap_m`；片间的空露出芯，就是槽。槽沿整圈轮廓走，所以圆头端面上也有。（2026-10-24 指挥官裁定，来自第 1 轮验证：柜身高度扣掉台面后不正好是 n 个槽距减一道槽时，余数并进最上面一片，台面压在它上面，每道槽都正好是 `groove_gap_m`；柜身至少要有两片、也就是至少一道槽，做不到时校验拒绝、builder 丢掉，理由一致，不悄悄建成没有槽的实体。缺省值和写出的值过同一套范围检查：没写 `groove_depth_m` 时缺省的 0.02 m 照样按进深核对，进深太小就在 `size_m` 处拒绝。`count` 大于 1 时像 `prop_block` 一样给警告，不悄悄忽略。）
3. 台面：顶上一块完整轮廓、厚 `top_m` 的片，颜色用 `look` 的 `top_color`。
4. 槽不用任何 `material.pattern`。
5. 只有 `id` 和 `kind` 的元素也能编译，缺的值用缺省值并记进 `assumed`（闭环的家族目录要用）。
   链 A 的共同要求（2026-10-23，来自 P7-fam-canopy 的验证）：本包的新角色加进 `design_check.py` 的 `GENERIC_ONLY_ROLES`，让它们出现在 `not_checked` 里；参数缺省值经 builder 拿到的 `Frame`（第五个参数或 `placed.frame`）的 `assume` 记进 `assumed`，夹掉的尺寸（例如高度超过限高）用 `Frame.adjust` 记，不悄悄削；部件的子组只能写成部件 id 的前缀（编译器会把 group 统一改成元素 id）；PARAM 检查拿到的 `form` 带 `platform_height_m`（有 `--fit` 时是拟合的地面高度），自 P7-fam-propblock 起还带 `wall_thickness_m` 和编译器实际用的入口边 `entrance_side`。会按自己的转角摆放的落地件要登记进 `motifs.SELF_PLACING_KINDS`：`place_floor` 不夹、不记它们（包括沿边的 along 位置），builder 从请求的位置按转后外廓只夹一次、用 `Frame.adjust` 记一次；靠边摆放的件只能落在封闭墙内表面围起来的空间里（`place_floor` 经 `Placed.span` 交出这块矩形 `(x0, x1, y0, y1)`；见 recipes 链 A 第 7 条），放不进就校验拒绝、builder 丢掉，不居中伸进墙（P7-fam-propblock 卡片交付物 9）；登记新 kind 时更新钉住这个集合的测试。
6. `design_check.py`：含 `shaped_counter` 的 spec 跑 `design-check` 不抛异常；柜身部件用 `counter` 角色，现有的柜台检查照常适用。
7. `references/component-recipes.md` 一节：参数、缺省值、一张剖面示意；部件数等于片数加 2（芯和台面），notes 里记一个 1.1 m 高、槽距 0.05 m 的柜台的部件数。
8. 接手 propblock 合并时留下的几处（2026-10-24，来自 P7-fam-propblock 的第 6 次验证；只改 `motifs.py`、recipes 和测试）：没写高度的 `lit_panel`，`z_m` 让块底离限高不到 0.2 m 时校验也要拒绝（现在校验放行、构建时丢）；没写高度时 `corner_radius_m` 超过缺省板高（墙高）同样在校验时拒绝；builder 丢掉放不进房间的件时，理由里也给出能放下的最大尺寸（和校验的信息一致）；极厚的 `lit_panel` 给出的'最大宽度'不得小于它带槽时的最小宽度（两条限制都放不下时说清楚）；recipes 里 `prop_block` 一节按链 A 第 7 条的房间规则改写（靠边件以封闭墙内表面为界，不再写'摆放同 box'），并写明 `Placed.span` 的元组顺序 `(x0, x1, y0, y1)`。本包的柜台如果会按自己的转角摆放，登记进 `SELF_PLACING_KINDS`。（2026-10-24 指挥官裁定，来自第 1 轮验证：没写高度的 `lit_panel` 校验时用编译器的缺省墙高；`layout.wall_height_m` 写了时校验看不到它，因为交给 PARAM 检查的 form 不带墙高，而本包不改 `design_compile.py`，这时校验和构建可能不一致，照 recipes 里的说明保留，绕法是在 `size_m` 里写高度，留给以后持有 `design_compile.py` 的包。缺省墙高下的"离限高不到 0.2 m"要按构建的同一条件判，即缺省墙顶减地面高度和限高下的空间取小的那个，不到 0.2 m 时校验也要拒绝（例如限高不超过 3.1 m 时，地面离限高不到 0.3 m）。）

## 允许改的文件

前言 `allowed_files`。链 A 租约。

## 禁止改的文件

前言 `forbidden_files`。

## 先写的测试

前言 `tests_first`，先提交（提交信息 `P7-fam-counter: tests first`），在基线提交上必须失败。`test_shaped_counter_grooves_are_gaps_between_slabs_over_a_core` 断言相邻两片之间的空等于 `groove_gap_m`，芯的外缘比片的外缘缩进 `groove_depth_m`。

## 验收命令

前言 `acceptance_cmds`，全部退出码 0。

## 完成的定义

1. 先写的测试和验收命令全部通过。
2. 证据：按 [05 第 3 节第 2 步](../05-gates-and-closing.md#3-g3-引擎够用止损点二第-7-天)，合并前由 Blender 通道用本包分支的代码出 Cycles 预览，放进 `output/poc/ceiling2/evidence/10/`；独立核对 agent 逐条判，全为"是"才写 `pass`：
   - 圆头端面在平面上是圆的；
   - 端面上看得见一道道横向的槽：槽里比槽间暗，端面剪影的边缘有一进一出的齿；
   - 和原图这一块的槽的方向、疏密对得上。
3. 不得用降级办法凑数：用平面线条冒充凹槽（`material.pattern` 的条纹、贴图上的线）一律判 `fail`。
4. 合并后指挥官把还原记录（`06-fidelity-ledger.md`，只在本地）第 10 行的状态改为"已验证"。

## 验证者清单

1. 白名单；链 A 租约只在本包手里；diff 里没有 `design_compile.py`。
2. 先红后绿。
3. 变异：把芯的内缩改成 0（芯和片一样大），`test_shaped_counter_grooves_are_gaps_between_slabs_over_a_core` 应变红；把槽换成 `material.pattern`，`test_shaped_counter_uses_no_material_pattern_for_grooves` 应变红。
4. 破坏性输入：`groove_gap_m` 不小于 `groove_pitch_m` 必须拒绝；`groove_depth_m` 不小于深度的一半必须拒绝；两端都是圆头而长度小于深度必须拒绝。
5. 证据由 Blender 通道出、核对 agent 判，不由本验证者判；`verdict.json` 为 `pass` 之前不合并。
