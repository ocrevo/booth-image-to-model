---
id: P7-fam-canopy
title: 家族 canopy_frame：各边带宽可设的空心顶框加一段更深的标识楣板；链 A 的通用钩子
goal: 设计路线能表达清单第 3 行的顶框；design_compile.py 只在本包改一次，之后的链 A 包只往 motifs.py 的表里加条目
depends_on: [P7-fit-camera, P5]
model: sonnet
lane: worker
allowed_files:
  - booth-image-to-model/scripts/design_compile.py
  - booth-image-to-model/scripts/motifs.py
  - booth-image-to-model/schemas/booth-design.v1.json
  - booth-image-to-model/scripts/design_check.py
  - booth-image-to-model/tests/test_design_compile.py
  - booth-image-to-model/tests/test_design_check.py
  - booth-image-to-model/references/component-recipes.md
forbidden_files:
  - booth-image-to-model/scripts/components.py
  - booth-image-to-model/scripts/geometry.py
  - booth-image-to-model/scripts/draft_compile.py
  - booth-image-to-model/CHANGELOG.md
  - web/**
  - tools/poc/**
inputs:
  - output/poc/ceiling/missing-vocabulary.md 第 3 行的"特征"和"差在哪"两格（指挥官贴进 brief；worktree 里没有 output/）
  - design_compile.py 的构建循环（头顶件的 if/elif 和排序）、ROLE、validate_design、look、floor、ring
  - motifs.py 的 OVERHEAD_KINDS、KINDS、BUILDERS、SCOPE、DEFAULT_SIZE；components.py 的 header_band 用法
  - geometry.py 的 profile_extrusion（一条外轮廓，可带开到边上的缺口，不能有内孔）
deliverables:
  - design_compile.py 的通用钩子：kind 在 mt.OVERHEAD_BUILDERS 里时走头顶件和三维路径分支，和 header_band、ring 一起排在落地件之前；ROLE 查不到时查 mt.ROLE；validate_design 对每个元素调 mt.PARAM_CHECKS
  - design_compile.py 的图案透传：元素的 material.pattern 原样复制到它的非文字、非自发光部件，style.floor_pattern 原样复制到地面部件，由 validate_spec 校验
  - motifs.py：Frame（交给头顶件 builder 的只读上下文）、OVERHEAD_BUILDERS、PARAM_CHECKS、ROLE 三张表，canopy_frame 的 builder 和参数检查
  - 新 kind canopy_frame：size_m（外缘宽、外缘深、楣高）、centre_m、z_m，params corner_radius_m、band_m（front、back、left、right 四个带宽）、sign（corner、along_m、drop_m、text_chars、end_radius_m）；end_radius_m 是加深段两端从框底到楣板底的竖向圆角半径，缺省 0（方端，即原来的做法），不超过 drop_m（2026-10-23 补：第 3 行证据第二次核对发现方台阶对不上，端头要能圆顺地弯下去）
  - schema：kind 枚举加 canopy_frame；params 加 band_m 和 sign；material 加 pattern；style 加 floor_pattern
  - references/component-recipes.md 一节：参数、默认值、一张 ASCII 示意，加"链 A 怎么加新 kind"
tests_first:
  - test_overhead_hook_dispatches_new_kinds_and_keeps_old_ones
  - test_role_and_param_checks_come_from_motifs_tables
  - test_design_pattern_is_copied_to_parts_and_validated
  - test_canopy_frame_band_widths_follow_each_side
  - test_canopy_frame_sign_is_flush_with_the_frame
  - test_canopy_frame_placeholder_text_never_copies_brief_text
  - test_canopy_frame_rejects_bad_params
  - test_bare_canopy_frame_compiles_with_assumed_defaults
  - test_every_kind_covers_canopy_frame
  - test_design_check_runs_on_a_spec_with_canopy_frame
  - test_existing_kinds_output_unchanged
acceptance_cmds:
  - BLENDER_EXE= python -m unittest discover -s booth-image-to-model/tests -p "test_design_compile.py"
  - BLENDER_EXE= python -m unittest discover -s booth-image-to-model/tests -p "test_design_check.py"
  - BLENDER_EXE= python -m unittest discover -s booth-image-to-model/tests -p "test_studio_macros.py"
  - python -m unittest discover -s tools/poc/tests -p "test_roles*.py"
  - python tools/check_docs.py
  - python tools/check_lf.py
effort_h: 6.5
day: 5
stop_rule: 不降级：各边带宽、偏心内孔、无台阶的加深段缺一样都不算完成；做不完就交已通过的测试和 notes，第 3 行在还原记录里保持"计划"，指挥官报决策人（G3 可能推到第 8 天）
---

# P7-fam-canopy 顶框和链 A 的钩子

## 目标

链 A 的第一个包，做两件事。

1. 补清单第 3 行：顶上的空心圆角框架，各边带宽不一样（内孔不居中），一个角上接一段更深的标识楣板。今天只有 `rounded_ring`（内孔居中、四边同宽），楣板只能另拼一块，接缝处有台阶。
2. 给链 A 开一个通用钩子。今天头顶件（`header_band`、`ring`、`spotlights`）在 `design_compile.py` 里写死成 if/elif，别的 kind 只能按落地件或墙面件摆放，顶框和三维路径构件（[P7-fam-sweep](P7-fam-sweep.md)）都放不进去。本包把这几处改成查 `motifs.py` 的表，之后的链 A 包只往表里加条目，不再改 `design_compile.py`。同时把设计里的材质图案透传到 spec，链 B 的新图案（[P7-mat-pattern](P7-mat-pattern.md)）才能经设计路线用上。

时机：G2 通过前只写 `tests_first` 的测试并提交到分支，不实现、不合并；G2 通过后再实现、验证、出证据、合并。

## 输入

前言 `inputs`。

## 交付物

1. 钩子（`design_compile.py` 只改这三处，旧 kind 的输出逐字节不变）：
   - 构建循环：`kind in mt.OVERHEAD_BUILDERS` 时调 `mt.OVERHEAD_BUILDERS[kind](b, frame, el, look, note)`。`frame` 是 `mt.Frame`：占地范围 `X0`、`X1`、`Y0`、`Y1`，地面高 `zf`，限高 `H`，墙顶高 `wall_top`，开放边和封闭边，`hanging_allowed`，以及编译器的 `assume`、`adjust`、`warn`（写进 `compile_report`）。排序：这些 kind 和 `header_band`、`ring` 一起排在落地件之前。`header_band`、`ring`、`spotlights` 原样不动。落地件和墙面件的新 kind（走 `mt.BUILDERS`）也要拿得到同一组 `assume`、`adjust`、`warn`，用来把参数缺省值记进 `compile_report` 的 `assumed`、把调整和警告写进报告：链 A 后面的包只往 `motifs.py` 的表里加条目，不许改 `design_compile.py`，所以这个口子在本包里开好（例如给 `BUILDERS` 的调用多传一个只读上下文，旧 kind 的输出逐字节不变；2026-10-23 补，来自第 5 轮验证）。
   - 颜色角色：`ROLE.get(kind)` 查不到时查 `mt.ROLE`。
   - 参数校验：`validate_design` 对每个元素调 `mt.PARAM_CHECKS.get(kind)`，它返回 `(路径, 信息)` 的列表，按 `PARAM` 错误报出。
2. 图案透传（`design_compile.py` 和 schema）：元素的 `material.pattern`（对象）原样复制到这个元素的非文字、非自发光部件，覆盖部件原有的图案；`style.floor_pattern`（对象或 null）原样复制到地面部件。编译器不校验图案内容，由 `validate_spec` 校验；未知类型让编译以退出码 2 结束。
3. `canopy_frame`（`motifs.py`，走钩子）：
   - 楣板端头的圆角是凸圆（像牛鼻边，不是凹的内圆角）：端面在框底高度处是竖直的，往下弯，到楣板底时是水平的；`end_radius_m` 缺省 0 时不记进 `assumed`（为了已有输出逐字节不变，这是唯一不记的缺省值）。内孔的四个内圆角半径还要夹到内孔短边的一半，这是公式之外的一条独立规则（2026-10-23 补）。
   - 外缘是平面圆角矩形：`size_m` 的前两个值是外缘宽和深（缺省时同 `ring`：占地四边各内缩 0.5 m），`centre_m` 是平面中心（缺省为占地中心），`params.corner_radius_m` 是外角半径；第三个值是楣高，`z_m` 是框底离地高（缺省时顶面在限高下 0.1 m，同 `ring`）。缺省值都记进 `assumed`。
   - `params.band_m`：`{front, back, left, right}` 四个带宽（平面上从外缘往里量），内孔由此偏心；内角半径取外角半径减相邻两带宽中较大的那个，不小于 0。
   - `params.sign`：`{corner: front_left|front_right|back_left|back_right, along_m: [沿第一条边的长度, 沿第二条边的长度], drop_m: 比框底再往下加深的高度, text_chars: 占位字数}`；`sign` 为 null 时没有加深段。加深段的外立面和框的外立面在同一个面上，连接处没有台阶、缝或错位。占位字贴在加深段外立面，内容是同样多的大写占位字母，不抄 brief 或原图的任何文字，`provenance.text` 固定为 `choice`。
   - 只用现有基元（`profile_extrusion`、`box`、`text`）。`profile_extrusion` 不能有内孔，所以框可以由几段组成，但所有段属于同一元素（同一 `group`），外立面共面、端面对齐，渲染里看不出分段。
   - 支撑：`hanging_allowed` 不为真时按 `ring` 的现有规则加立柱。
4. `design_check.py`：含 `canopy_frame` 的 spec 跑 `design-check` 不抛异常；认不得的部件角色写进 `not_checked`。
5. schema：`kind` 枚举加 `canopy_frame`；`params` 加 `band_m`、`sign`；`material` 加 `pattern`；`style` 加 `floor_pattern`。
6. `references/component-recipes.md` 一节：参数、缺省值、一张 ASCII 示意；再写一小段"链 A 怎么加新 kind"：往 `OVERHEAD_BUILDERS` 或 `BUILDERS`、`PARAM_CHECKS`、`ROLE`、`DEFAULT_SIZE`、`SCOPE`、`KINDS` 里加条目。

只有 `id` 和 `kind` 的 `canopy_frame` 元素也必须能编译（缺的值用缺省值并记进 `assumed`），否则闭环的家族目录（`tools/poc/loop/catalog.py` 为 schema 的每个 kind 生成最小设计）会坏。

## 允许改的文件

前言 `allowed_files`。链 A 租约，含 `design_compile.py`（只有本包改它）。`components.py` 和 `geometry.py` 不动：不加新基元。

## 禁止改的文件

前言 `forbidden_files`。CHANGELOG 和版本号由指挥官在合并时写。

## 先写的测试

前言 `tests_first`，先提交（提交信息 `P7-fam-canopy: tests first`），在基线提交上必须失败。`test_existing_kinds_output_unchanged` 在基线上先算模板设计和 `EveryKind` 设计（不含新 kind 的元素）的 `spec.json` 哈希写进常量，分支上必须相等。

## 验收命令

前言 `acceptance_cmds`，全部退出码 0。`test_roles*.py` 检查闭环的家族目录仍能为 schema 的每个 kind 生成能编译的最小设计。

## 完成的定义

1. 先写的测试和验收命令全部通过。
2. 证据：清单第 3 行"差在哪"写的几处在一张预览里看得见地消失。按 [05 第 3 节第 2 步](../05-gates-and-closing.md#3-g3-引擎够用止损点二第-7-天)，合并前由 Blender 通道用本包分支的代码出 Cycles 预览，放进 `output/poc/ceiling2/evidence/3/`；一个独立核对 agent 逐条判下面三条，全为"是"才写 `pass`：
   - 框的四边带宽看得出不同，内孔不居中，和原图这一块对得上；
   - 加深的标识楣板和框的外立面连成一片，连接处看不到缝或错位；加深段的起止处是圆顺弯下去的过渡，证据里不能是方台阶；
   - 加深段上有占位字，位置和大小与原图的标识相当。
3. 不得用降级办法凑数，例如居中的 `rounded_ring` 加另挂的 `box`、只在贴图上画出不同带宽；用了就判 `fail`。
4. 合并后指挥官把还原记录（`06-fidelity-ledger.md`，只在本地）第 3 行的状态改为"已验证"。

## 验证者清单

1. 白名单；链 A 租约（含 `design_compile.py`）只在本包手里。
2. 先红后绿。
3. 变异：把 `band_m` 的四个值都换成 `front` 的值，`test_canopy_frame_band_widths_follow_each_side` 应变红；把钩子的排序改成落地件在前，`test_overhead_hook_dispatches_new_kinds_and_keeps_old_ones` 应变红。
4. 破坏性输入：两条对边的带宽之和不小于外缘对应尺寸必须拒绝；`sign.along_m` 超过所在边长必须拒绝；`text_chars` 为负必须拒绝。
5. 证据由 Blender 通道出、核对 agent 判（见"完成的定义"第 2 条），不由本验证者判；`verdict.json` 为 `pass` 之前不合并。
