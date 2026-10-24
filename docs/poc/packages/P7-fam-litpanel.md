---
id: P7-fam-litpanel
title: 家族 lit_panel：带内嵌灯槽的圆角墙板，可开有底的凹龛
goal: 设计路线能表达清单第 1 行（沿板边自动生成、凹进板面的灯槽）和第 2 行（有底、不穿透、四周一圈灯的凹龛）
depends_on: [P7-fam-canopy, P7-fit-camera, P5]
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
  - output/poc/ceiling/missing-vocabulary.md 第 1、2 行的"特征"和"差在哪"（指挥官贴进 brief）
  - references/component-recipes.md 里 P7-fam-canopy 写的"链 A 怎么加新 kind"
  - motifs.py 的 partition builder（落地件的摆放）；geometry.py 的 profile_extrusion 和 rounded_portal
deliverables:
  - 新 kind lit_panel（落地件，摆放同 partition）：size_m（宽、厚、高），params corner_radius_m（顶部两角）、inset_m（槽离板边）、groove_width_m、groove_depth_m、light_hex、niche（width_m、height_m、bottom_m、depth_m、corner_radius_m、ring_width_m，或 null）
  - motifs.py 的 builder 和 BUILDERS、PARAM_CHECKS、ROLE、DEFAULT_SIZE、SCOPE、KINDS 条目；schema 的 kind 枚举和 params；references/component-recipes.md 一节
tests_first:
  - test_lit_panel_groove_is_recessed_behind_the_panel_face
  - test_lit_panel_groove_follows_the_panel_outline_for_two_shapes
  - test_lit_panel_niche_has_back_and_floor_and_does_not_go_through
  - test_lit_panel_niche_ring_of_light_surrounds_the_opening
  - test_lit_panel_rejects_bad_params
  - test_bare_lit_panel_compiles_with_assumed_defaults
  - test_every_kind_covers_lit_panel
  - test_design_check_runs_on_a_spec_with_lit_panel
  - test_existing_kinds_output_unchanged
acceptance_cmds:
  - BLENDER_EXE= python -m unittest discover -s booth-image-to-model/tests -p "test_design_compile.py"
  - BLENDER_EXE= python -m unittest discover -s booth-image-to-model/tests -p "test_design_check.py"
  - BLENDER_EXE= python -m unittest discover -s booth-image-to-model/tests -p "test_studio_macros.py"
  - python -m unittest discover -s tools/poc/tests -p "test_roles*.py"
  - python tools/check_docs.py
  - python tools/check_lf.py
effort_h: 6
day: 5-6
stop_rule: 不降级：灯槽必须凹进板面、凹龛必须有底不穿透；做不完就交已通过的测试和 notes，第 1、2 行在还原记录里保持"计划"，指挥官报决策人（G3 可能推到第 8 天）
---

# P7-fam-litpanel 灯槽墙板和凹龛

## 目标

补清单第 1、2 行。第 1 行：墙板四周内嵌的发光灯槽，沿圆角板边走成倒 U；今天是贴在墙外的凸条，路径要手算控制点，板形一变就要重写。第 2 行：板上的凹龛有底、不穿透、四周一圈灯；今天轮廓挤出只能切穿全厚，龛底和墙是几件拼的。

时机：G2 通过前只写 `tests_first` 的测试并提交到分支，不实现、不合并；G2 通过后再实现、验证、出证据、合并。

## 输入

前言 `inputs`。

## 交付物

`lit_panel` 是一个元素（同一 `group`），只用现有基元，按 [P7-fam-canopy](P7-fam-canopy.md) 写在 recipes 里的办法往 `motifs.py` 的表里加条目，不改 `design_compile.py`。

1. 摆放：落地件，同 `partition`：靠一条边（`side`、`along`、`offset_m`），或用 `centre_m` 加 `yaw_deg` 斜放（例如斜跨墙角的板）。
2. 板形：立面是矩形，顶部两角按 `corner_radius_m` 倒圆，成倒 U。
3. 灯槽：沿板的两条竖边和圆角顶边走一圈倒 U，离板边 `inset_m`，宽 `groove_width_m`，深 `groove_depth_m`；槽底一条发光带，颜色 `light_hex`，发光强度用设计路线发光件的值。槽的轮廓全部由板形参数算出，不手写控制点；改 `size_m` 或 `corner_radius_m` 时槽自动跟着走。做法示例：后层整板，前层分成槽外的倒 U 边框和槽内的板心（`profile_extrusion` 允许开到下边的缺口），槽底放发光带。
4. 凹龛：`niche` 不为 null 时，在板心开一个圆角矩形开口（`corner_radius_m` 取宽度一半时就是拱形），开口下沿离地 `bottom_m`，向板里凹 `depth_m`（不大于板厚减 0.01 m），有后壁和底面，不穿透；开口四周一圈宽 `ring_width_m` 的发光边。龛口、龛底、龛后壁和板面之间共面或对齐，不留缝。
5. 只有 `id` 和 `kind` 的元素也能编译，缺的值用缺省值并记进 `assumed`（闭环的家族目录要用）。
   链 A 的共同要求（2026-10-23，来自 P7-fam-canopy 的验证）：本包的新角色加进 `design_check.py` 的 `GENERIC_ONLY_ROLES`，让它们出现在 `not_checked` 里；参数缺省值经 builder 拿到的 `Frame`（第五个参数或 `placed.frame`）的 `assume` 记进 `assumed`，夹掉的尺寸（例如高度超过限高）用 `Frame.adjust` 记，不悄悄削；部件的子组只能写成部件 id 的前缀（编译器会把 group 统一改成元素 id）；PARAM 检查拿到的 `form` 带 `platform_height_m`（有 `--fit` 时是拟合的地面高度）。另外接手 canopy 合并时留下的三处（只改 `motifs.py` 和 recipes）：canopy 竖向下限上缺省 `z_m` 读回写入时用同样的 1e-6 容差；端头件对齐网格时的半步取整平局（个别输入差 0.01 mm）；recipes 提醒带 `end_radius_m` 的顶框加图案时端头件的图案方向不同。
6. `design_check.py`：含 `lit_panel` 的 spec 跑 `design-check` 不抛异常。
7. `references/component-recipes.md` 一节：参数、缺省值、一张 ASCII 示意（正视和剖面）。

## 允许改的文件

前言 `allowed_files`。链 A 租约。`design_compile.py` 由 P7-fam-canopy 改过，本包不改。

## 禁止改的文件

前言 `forbidden_files`。

## 先写的测试

前言 `tests_first`，先提交（提交信息 `P7-fam-litpanel: tests first`），在基线提交上必须失败。`test_lit_panel_groove_is_recessed_behind_the_panel_face` 断言发光带的前表面在板前表面之后至少 `groove_depth_m` 减 0.001 m；`test_lit_panel_niche_has_back_and_floor_and_does_not_go_through` 断言龛后壁到板背面还有厚度。

## 验收命令

前言 `acceptance_cmds`，全部退出码 0。

## 完成的定义

1. 先写的测试和验收命令全部通过。
2. 证据：按 [05 第 3 节第 2 步](../05-gates-and-closing.md#3-g3-引擎够用止损点二第-7-天)，合并前由 Blender 通道用本包分支的代码出 Cycles 预览，第 1 行放进 `output/poc/ceiling2/evidence/1/`，第 2 行放进 `output/poc/ceiling2/evidence/2/`；独立核对 agent 逐条判，全为"是"才写 `pass`。
   - 第 1 行：灯槽是凹进板面的槽，发光带在板面之后，槽两侧看得见板面的边，光在槽里留下阴影；槽沿圆角板边走成倒 U，和原图这几块板对得上。
   - 第 2 行：凹龛有底面和后壁，看不到板后面的东西；开口四周有一圈灯；龛和板看起来是一个整体，龛口、龛底和板面之间看不到缝或台阶。
3. 不得用降级办法凑数，例如贴在板面外的凸条、切穿整板再在后面另放一块板、用材质线条画出槽；用了就判 `fail`。
4. 合并后指挥官把还原记录（`06-fidelity-ledger.md`，只在本地）第 1、2 行的状态改为"已验证"。

## 验证者清单

1. 白名单；链 A 租约只在本包手里；diff 里没有 `design_compile.py`。
2. 先红后绿。
3. 变异：把发光带的深度改成 0（贴在板面上），`test_lit_panel_groove_is_recessed_behind_the_panel_face` 应变红；把龛深改成板厚，`test_lit_panel_niche_has_back_and_floor_and_does_not_go_through` 应变红。
4. 破坏性输入：`inset_m` 加 `groove_width_m` 不小于板宽的一半必须拒绝；`niche.depth_m` 不小于板厚必须拒绝；凹龛超出板心必须拒绝。
5. 证据由 Blender 通道出、核对 agent 判，不由本验证者判；两行的 `verdict.json` 都为 `pass` 之前不合并。
