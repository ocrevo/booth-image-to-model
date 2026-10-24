---
id: P7-fam-sweep
title: 家族 sweep_member：沿三维路径的连续构件，矩形或圆截面
goal: 设计路线能表达清单第 6 行（平面转角和竖向弯折连成一根的顶框）和第 7 行（一排圆截面弯管）
depends_on: [P7-fam-counter, P7-fit-camera, P5]
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
  - output/poc/ceiling/missing-vocabulary.md 第 6、7 行的"特征"和"差在哪"（指挥官贴进 brief）
  - references/component-recipes.md 里 P7-fam-canopy 写的"链 A 怎么加新 kind"（头顶件和三维路径钩子 OVERHEAD_BUILDERS）
  - geometry.py 的 box、cylinder、arc_panel、swept_strip（swept_strip 只在一个竖直面里弯）
deliverables:
  - 新 kind sweep_member（走 OVERHEAD_BUILDERS 钩子）：params path_m（点列 [x, y, z]，平面以占地中心为原点，z 从地面算）、radii_m（每个内点一个转弯半径）、section（rect 或 round）、section_m（rect 给 [宽, 高]，round 给 [直径]）、repeat_axis（x 或 y）；元素的 count 和 pitch_m 沿 repeat_axis 复制整根
  - 每个内点的转弯必须落在水平面或竖直面里，否则拒绝；路径点超出占地或限高时拒绝
  - 矩形截面：直段用 box，水平面里的转弯用 arc_panel，竖直面里的弯用 swept_strip；相邻段的端截面重合
  - 圆截面：直段用 cylinder，弯段用短 cylinder 串成，每段转角不超过 5 度，圆周不少于 24 段，每段两端各加长 r·tan(步角/2)，弯的外侧不留缝
  - motifs.py 的 builder 和 OVERHEAD_BUILDERS、PARAM_CHECKS、ROLE、SCOPE、KINDS 条目；schema 的 kind 枚举和 params；references/component-recipes.md 一节
tests_first:
  - test_sweep_member_consecutive_pieces_share_end_sections
  - test_sweep_member_plan_corner_and_vertical_bend_in_one_member
  - test_sweep_member_round_bends_step_at_most_5_degrees_without_outer_gaps
  - test_sweep_member_count_and_pitch_copy_the_whole_path
  - test_sweep_member_rejects_twisted_corners_and_points_outside
  - test_sweep_member_rejects_bad_params
  - test_bare_sweep_member_compiles_with_assumed_defaults
  - test_every_kind_covers_sweep_member
  - test_design_check_runs_on_a_spec_with_sweep_member
  - test_existing_kinds_output_unchanged
acceptance_cmds:
  - BLENDER_EXE= python -m unittest discover -s booth-image-to-model/tests -p "test_design_compile.py"
  - BLENDER_EXE= python -m unittest discover -s booth-image-to-model/tests -p "test_design_check.py"
  - BLENDER_EXE= python -m unittest discover -s booth-image-to-model/tests -p "test_studio_macros.py"
  - python -m unittest discover -s tools/poc/tests -p "test_roles*.py"
  - python tools/check_docs.py
  - python tools/check_lf.py
effort_h: 7
day: 7
stop_rule: 不降级：不得用几个元素拼顶框、不得用矩形窄条冒充圆管；做不完就交已通过的测试和 notes，第 6、7 行在还原记录里保持"计划"，指挥官报决策人（按决策人 2026-10-23 的决定 G3 推到第 8 天，不砍这两行）
---

# P7-fam-sweep 三维路径构件

## 目标

补清单第 6、7 行，只有一张图需要，所以排在链 A 最后。第 6 行：顶框的一根梁在平面上转大圆角接前梁，前梁末端再向下弯成外倾的落地腿；今天 `swept_strip` 只能在一个竖直面里弯，只好用三件拼，接缝看得见。第 7 行：从前梁垂下的一排弯管，先 S 形弯再垂直落地，顶端还向后弯进梁底，截面是圆的；今天只有矩形截面的窄条。

本包是新卡（原来的十张预写卡里没有它）。决策人 2026-10-23 决定：第 3 步做不完时 G3 从第 7 天推到第 8 天，而不是砍掉这两行。

时机：G2 通过前只写 `tests_first` 的测试并提交到分支，不实现、不合并；G2 通过后再实现、验证、出证据、合并。

## 输入

前言 `inputs`。

## 交付物

`sweep_member` 是一个元素（同一 `group`；`count` 大于 1 时每根一个子组前缀，整排仍是一个元素），只用现有基元，走 [P7-fam-canopy](P7-fam-canopy.md) 加的 `OVERHEAD_BUILDERS` 钩子，不改 `design_compile.py`。

1. 路径：`path_m` 至少两个点；`radii_m` 的长度等于内点数；每个内点前后两段张成的平面必须是水平面或竖直面（夹角容差 0.5 度），否则拒绝。路径点超出占地或高于限高时拒绝。
2. 矩形截面（`section: rect`，`section_m: [宽, 高]`）：直段是转到段方向的 `box`；水平面里的转弯是竖轴的 `arc_panel`（径向厚度等于截面宽，高等于截面高）；竖直面里的弯是 `swept_strip`。每一段的起点截面和上一段的终点截面重合（中心差小于 0.001 m，方向差小于 0.5 度）。
3. 圆截面（`section: round`，`section_m: [直径]`）：直段是转到段方向的 `cylinder`；弯段由短 `cylinder` 串成，每段转角不超过 5 度，圆周不少于 24 段；每段两端各加长 r·tan(步角/2)（r 是管半径），让弯的外侧不留缝。
4. 复制：`count` 大于 1 时，整根沿 `repeat_axis` 每隔 `pitch_m` 复制一次，以路径为第一根。
5. 只有 `id` 和 `kind` 的元素也能编译：缺省是一根贴在限高下 0.1 m 的 1 m 长矩形直段，截面 0.1 × 0.1 m，都记进 `assumed`（闭环的家族目录要用）。
   链 A 的共同要求（2026-10-23，来自 P7-fam-canopy 的验证）：本包的新角色加进 `design_check.py` 的 `GENERIC_ONLY_ROLES`，让它们出现在 `not_checked` 里；参数缺省值经 builder 拿到的 `Frame`（第五个参数或 `placed.frame`）的 `assume` 记进 `assumed`，夹掉的尺寸（例如高度超过限高）用 `Frame.adjust` 记，不悄悄削；部件的子组只能写成部件 id 的前缀（编译器会把 group 统一改成元素 id）；PARAM 检查拿到的 `form` 带 `platform_height_m`（有 `--fit` 时是拟合的地面高度），自 P7-fam-propblock 起还带 `wall_thickness_m` 和编译器实际用的入口边 `entrance_side`。会按自己的转角摆放的落地件要登记进 `motifs.SELF_PLACING_KINDS`：`place_floor` 不夹、不记它们（包括沿边的 along 位置），builder 从请求的位置按转后外廓只夹一次、用 `Frame.adjust` 记一次；靠边摆放的件只能落在封闭墙内表面围起来的空间里（`place_floor` 经 `Placed.span` 交出这块矩形 `(x0, x1, y0, y1)`；见 recipes 链 A 第 7 条），放不进就校验拒绝、builder 丢掉，不居中伸进墙（P7-fam-propblock 卡片交付物 9）；登记新 kind 时更新钉住这个集合的测试。
6. `design_check.py`：含 `sweep_member` 的 spec 跑 `design-check` 不抛异常。
7. `references/component-recipes.md` 一节：参数、缺省值、一张示意（平面转角加竖向弯折）。notes 里记一根带 S 弯的圆管的部件数和证据预览的用时。

## 允许改的文件

前言 `allowed_files`。链 A 租约。

## 禁止改的文件

前言 `forbidden_files`。不加新基元：真正的三维扫掠基元要改 `geometry.py` 和网页端的移植，不在本轮（见还原记录（`06-fidelity-ledger.md`，只在本地）第 6、7 行的"以后怎么升级"）。

## 先写的测试

前言 `tests_first`，先提交（提交信息 `P7-fam-sweep: tests first`），在基线提交上必须失败。

## 验收命令

前言 `acceptance_cmds`，全部退出码 0。

## 完成的定义

1. 先写的测试和验收命令全部通过。
2. 证据：按 [05 第 3 节第 2 步](../05-gates-and-closing.md#3-g3-引擎够用止损点二第-7-天)，合并前由 Blender 通道用本包分支的代码出 Cycles 预览，第 6 行放进 `output/poc/ceiling2/evidence/6/`，第 7 行放进 `output/poc/ceiling2/evidence/7/`；独立核对 agent 逐条判，全为"是"才写 `pass`。
   - 第 6 行：顶框是一根连续的构件，梁在平面上转大圆角接前梁，前梁末端向下弯成外倾的腿，一路下来连接处看不到缝、台阶或错位；轮廓和原图这一块对得上。
   - 第 7 行：管是圆截面，两侧轮廓平行，高光是沿管走的一条亮线；先 S 形弯再垂直落地，顶端向后弯进梁底；弯段看不到折角或缝；一排管由 `count` 和 `pitch_m` 一次放出。
3. 不得用降级办法凑数，例如几个元素拼成顶框、矩形窄条冒充圆管、弯段留着看得见的折角；用了就判 `fail`。
4. 合并后指挥官把还原记录（`06-fidelity-ledger.md`，只在本地）第 6、7 行的状态改为"已验证"。

## 验证者清单

1. 白名单；链 A 租约只在本包手里；diff 里没有 `design_compile.py`。
2. 先红后绿。
3. 变异：把弯段的步角上限改成 30 度，`test_sweep_member_round_bends_step_at_most_5_degrees_without_outer_gaps` 应变红；把段的加长去掉，同一个测试应变红；把水平转弯的 `arc_panel` 半径加 0.01 m，`test_sweep_member_consecutive_pieces_share_end_sections` 应变红。
4. 破坏性输入：两个相邻点重合必须拒绝；转弯半径大到放不下相邻直段必须拒绝；一个转弯既不在水平面也不在竖直面里必须拒绝。
5. 证据由 Blender 通道出、核对 agent 判，不由本验证者判；两行的 `verdict.json` 都为 `pass` 之前不合并。
