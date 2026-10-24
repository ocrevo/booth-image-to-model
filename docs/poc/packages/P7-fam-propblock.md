---
id: P7-fam-propblock
title: 家族 prop_block：尺寸和位置都对的道具占位块
goal: 设计路线能用占位块代替桌上的小件电子产品、盆栽和大道具，按位置和尺寸算；清单第 5、8 行按决策人决定算已可表达，并在还原记录里标为替代
depends_on: [P7-fam-litpanel, P7-fit-camera, P5]
model: sonnet
lane: worker
allowed_files:
  - booth-image-to-model/scripts/motifs.py
  - booth-image-to-model/schemas/booth-design.v1.json
  - booth-image-to-model/scripts/design_check.py
  - booth-image-to-model/tests/test_design_compile.py
  - booth-image-to-model/tests/test_design_check.py
  - booth-image-to-model/references/component-recipes.md
  - booth-image-to-model/scripts/design_compile.py
forbidden_files:
  - booth-image-to-model/scripts/components.py
  - booth-image-to-model/scripts/geometry.py
  - booth-image-to-model/CHANGELOG.md
  - web/**
  - tools/poc/**
inputs:
  - output/poc/ceiling/missing-vocabulary.md 第 5、8 行的"特征"和"差在哪"（指挥官贴进 brief）
  - references/component-recipes.md 里 P7-fam-canopy 写的"链 A 怎么加新 kind"
  - motifs.py 的 box builder 和 DEFAULT_SIZE
deliverables:
  - 新 kind prop_block（落地件）：一个 box；size_m（宽、深、高）、side 加 along 或 centre_m、z_m（放在桌面或层板上时的离地高）、yaw_deg；params label（封闭枚举：laptop、tablet、monitor、phone、headphone、plant、robot_arm、robot、other），label 写进部件的 evidence.note
  - motifs.py 的 builder 和 BUILDERS、PARAM_CHECKS、ROLE、DEFAULT_SIZE、SCOPE、KINDS 条目；schema 的 kind 枚举和 params.label；references/component-recipes.md 一节
tests_first:
  - test_prop_block_is_one_box_with_label_in_note
  - test_prop_block_label_is_closed_enum
  - test_prop_block_sits_on_z_and_keeps_its_size
  - test_prop_block_rejects_bad_params
  - test_bare_prop_block_compiles_with_assumed_defaults
  - test_every_kind_covers_prop_block
  - test_design_check_runs_on_a_spec_with_prop_block
  - test_existing_kinds_output_unchanged
  - test_a_turned_lit_panel_that_does_not_fit_is_refused_at_validation
  - test_lit_panel_niche_containment_follows_the_built_arc_chords
  - test_valid_lit_panel_designs_with_a_height_build_without_drops
  - test_default_canopy_z_m_is_rounded_inwards_when_read_back
acceptance_cmds:
  - BLENDER_EXE= python -m unittest discover -s booth-image-to-model/tests -p "test_design_compile.py"
  - BLENDER_EXE= python -m unittest discover -s booth-image-to-model/tests -p "test_design_check.py"
  - BLENDER_EXE= python -m unittest discover -s booth-image-to-model/tests -p "test_studio_macros.py"
  - python -m unittest discover -s tools/poc/tests -p "test_roles*.py"
  - python tools/check_docs.py
  - python tools/check_lf.py
effort_h: 2.5
day: 6
stop_rule: 做不完就交已通过的测试和 notes，第 5、8 行在还原记录里保持"计划"，指挥官报决策人；不得用一整块盖住整张桌面代替逐件的占位块
---

# P7-fam-propblock 道具占位块

## 目标

决策人的部件范围（01 第 2 节）：机械臂、机器人、盆栽用尺寸和位置都对的占位块；2026-10-23 又决定桌上的小件电子产品同样处理，并且这样做就算清单第 5 行（电子产品、耳机和支架）和第 8 行（盆栽）"已可表达"，G3 的 12 行分母不变。占位块按位置和尺寸评分，不按外形；在还原记录（`06-fidelity-ledger.md`，只在本地）里这两行标为"替代"。

时机：G2 通过前只写 `tests_first` 的测试并提交到分支，不实现、不合并；G2 通过后再实现、验证、出证据、合并。

## 输入

前言 `inputs`。

## 交付物

1. `prop_block` 是一个 `box`，一件物品一个元素：多少件就是多少个元素，每个一行。`size_m` 给外框，`z_m` 让它坐在桌面或层板上，`yaw_deg` 给朝向。
2. `params.label` 是封闭枚举（前言列出），写进部件的 `evidence.note`；没有外形参数，不做翻盖角度、支架或叶子。
3. 颜色走元素的 `material`（`color_hex`、`emissive`），不加新字段。
4. 只有 `id` 和 `kind` 的元素也能编译：`label` 缺省为 `other`，尺寸缺省进 `DEFAULT_SIZE`，都记进 `assumed`（闭环的家族目录要用）。
5. `design_check.py`：含 `prop_block` 的 spec 跑 `design-check` 不抛异常。
   链 A 的共同要求（2026-10-23，来自 P7-fam-canopy 的验证）：本包的新角色加进 `design_check.py` 的 `GENERIC_ONLY_ROLES`，让它们出现在 `not_checked` 里；参数缺省值经 builder 拿到的 `Frame`（第五个参数或 `placed.frame`）的 `assume` 记进 `assumed`，夹掉的尺寸（例如高度超过限高）用 `Frame.adjust` 记，不悄悄削；部件的子组只能写成部件 id 的前缀（编译器会把 group 统一改成元素 id）；PARAM 检查拿到的 `form` 带 `platform_height_m`（有 `--fit` 时是拟合的地面高度），本包起还带 `wall_thickness_m` 和编译器实际用的入口边 `entrance_side`（交付物 8）。
6. `references/component-recipes.md` 一节：参数、缺省值、"占位块只按位置和尺寸算"的说明。
7. 接手 litpanel 合并时留下的几处（2026-10-23，来自 P7-fam-litpanel 的验证；只改 `motifs.py`、schema 的说明文字、两份测试和 recipes）：
   - 校验和构建要一致：`check_lit_panel` 的占地检查按转过之后的外廓（含厚度）算，转过以后放不进占地的板在校验时就拒绝（PARAM，给出能放下的范围），不要等到构建时才丢掉；凹龛加发光圈是否落在槽内芯里，按构建时实际用的折线圆弧（每个圆角 12 段）判，不按精确圆弧，大圆角的板不再在构建时自交。
   - 加一个参数扫描测试（运行手册第 4 节第 8 条第 6 项）：固定随机种子生成一批写了高度的 `lit_panel` 设计，凡是校验通过的，构建时一个都不进 `dropped`。没写高度时和高度有关的凹龛检查仍推迟到构建，这一点 recipes 已写明。
   - canopy 的缺省 `z_m` 向内取整：P7-fam-litpanel 改了它但没有测试能发现改回去（变异存活）。加一个测试钉住：拟合地面 0.33、限高附近、`drop_m` 0.33335 时，缺省 `z_m` 读回再校验能通过。
   - schema 里 `params.groove_depth_m` 的说明改成按 kind 分别解释（现在只写了 `lit_panel` 的含义；P7-fam-counter 会用同一个键表示别的意思）。
8. 校验和构建一致的裁定（2026-10-23，来自本包的第 1 次验证）：PARAM 检查拿到的 `form` 看不到 `layout.wall_thickness_m` 和入口边，所以转后占地检查用 0.10 m 的墙厚先验和"任一开放边都算入口"去猜，两个方向都会错（误拒建得出的设计，比合并前还退步；放过构建时会丢的设计，例如居中、不写 `yaw_deg` 的墙板按任一开放边算放得下就过，构建却按真正的入口转向后丢掉）。只改 `motifs.py` 修不好，所以本包破例可以改 `design_compile.py`，而且只改一处：交给 PARAM 检查的 `form` 带上 `layout.wall_thickness_m`（没写时用编译器自己的缺省）和编译器实际用的入口边（`layout.entrance_side`，没写时按编译器的规则取），别的不动；已有元素的输出逐字节不变（`test_existing_kinds_output_unchanged` 不放松）。有了这两项：
   - 转后占地检查、靠边件先按边长截宽、居中且不写 `yaw_deg` 的件的朝向，都按构建的同一套规则算：校验通过的设计构建时不丢（没写高度时推迟到构建的凹龛检查除外），校验拒绝的设计构建也建不出来。靠边、比占地还宽的墙板按构建的规则先截到边长再判，不再在校验时误拒。
   - 转过的件靠近占地边时，只按转后的外廓夹一次（现在先按没转的宽度夹一次、再按转后的外廓夹，多往里挪了），`compile_report` 只记实际的那次调整。
   - 扫描测试改成：inline、corner、peninsula、island 四种展台都有，`layout.entrance_side` 和 `layout.wall_thickness_m` 取多种值，有地台和没有，至少三个固定种子，`lit_panel` 和 `prop_block` 都扫，构建全部校验通过的设计（不只前若干个）。断言两件事：除了没写高度的凹龛，零个"校验通过、构建却丢"；零个"校验拒绝、绕过校验却建得出"（后者用编译器的构建直接试）。
9. 只记实际那次调整的裁定（2026-10-23，来自本包的第 2 次验证）：上一轮为了让转过的件只按转后外廓夹一次，builder 经 `frame.adjust.__self__` 去删编译器报告里 `place_floor` 写的记录；这越过了 `Frame` 只读的约定，而且对靠边摆放（没有 `centre_m`）的件删错了，件被挪了几米却没有记录。交付物 8 的"只记一次"在 `design_compile.py` 只准改一处时做不干净，所以本包再破例改一处：`place_floor` 对 `motifs` 声明的"自己按转后外廓摆放"的 kind（`lit_panel`、`prop_block`，用一个集合常量声明）不夹、不记，把请求的位置原样交给 builder；builder 从这个位置按转后外廓只夹一次，用 `Frame.adjust` 记一次（靠边件和居中件都一样）。去掉 `_take_back_placement_move`，builder 不得读写编译器的内部状态。其余 kind 的输出逐字节不变。另：绕过校验直接构建时，`z_m` 小于 0 或尺寸小于 0.001 m 的 `prop_block` 由 builder 拒绝（进 `dropped` 并给理由），和校验一致。（同日补，来自本包的第 3 次验证）"不夹"也包括 `place_floor` 按没转的宽度把沿边的 along 位置夹进边长范围的那一步：自己摆放的 kind 的 along 原样交给 builder，由 builder 按转后外廓一起算、只记一次。builder 的空间检查比的是块底到限高的距离，不是块自己的高：高度不小于 0.001 m、块底到限高还有至少 0.01 m 的块要建出来（平放的手机、平板只有几毫米厚）。（2026-10-24 补，来自本包的第 4 次验证；其中'垂直于边夹在占地之内'已被下面第 5 次验证的'再补'取代）沿边的范围仍然是两道端墙之间：`place_floor` 把请求的 along 位置和这条边可用的范围（`along_span` 算出的两端墙之间的 lo、hi）一起交给 builder，builder 沿边按转后外廓夹在 lo..hi 之内、垂直于边按转后外廓夹在占地之内，只记一次；件不得伸进封闭的端墙（和旧 kind 一样贴墙即止）。（2026-10-24 再补，来自本包的第 5 次验证）靠边摆放的自己摆放的件，按转后外廓只能落在'封闭墙的内表面围起来的空间'里：沿边在两道端墙之间，垂直于边在所靠这面墙（以及对面的墙，若封闭）的内表面之内，开放的一侧以占地边为界；放不进这个空间（例如转过后沿边的外廓比两道端墙之间还长）时，校验按 PARAM 拒绝并给出能放下的最大尺寸，builder 也丢掉并给同样的理由——不得居中后伸进墙或伸出占地。居中摆放的件照旧只夹在占地内，和旧 kind 一致。另把这些约定（form 的 `wall_thickness_m`、`entrance_side`，`SELF_PLACING_KINDS`、`Placed.span`、只夹一次记一次、靠边件的空间）写进 recipes 的'链 A 怎么加新 kind'一节。

## 允许改的文件

前言 `allowed_files`。链 A 租约。

## 禁止改的文件

前言 `forbidden_files`。

## 先写的测试

前言 `tests_first`，先提交（提交信息 `P7-fam-propblock: tests first`），在基线提交上必须失败。

## 验收命令

前言 `acceptance_cmds`，全部退出码 0。

## 完成的定义

1. 先写的测试和验收命令全部通过。
2. 证据：按 [05 第 3 节第 2 步](../05-gates-and-closing.md#3-g3-引擎够用止损点二第-7-天)，合并前由 Blender 通道用本包分支的代码出 Cycles 预览：第 5 行放进 `output/poc/ceiling2/evidence/5/`（需要这一行的每张图各一张），第 8 行放进 `output/poc/ceiling2/evidence/8/`（需要这一行的那张图）；独立核对 agent 逐件判"占位块落在原图这件物品的位置上，外框大小相当"，全为"是"才写 `pass`。不判外形（决策人决定）。（2026-10-23 补，来自第 5 行第一次核对：原图里一件小物品落在相邻一件薄物品的外框之内，真物品挡不住它，外框大小相当的占位块却把它挡住了，预览里看不见它的块）原图里落在相邻物品外框之内、被相邻占位块挡住的小件，另出一张补充图：同一份编译结果、同一台相机，只删掉挡住它的那个占位块的部件，别的不动；核对人在补充图里确认它的块落在原图这件的位置上、外框相当。这是方块代替外形（决策人决定）带来的遮挡，不算降级；补充图只能删挡住小件的块，不能挪动或缩小任何块。
3. 不得用降级办法凑数，例如一整块盖住整张桌面、几件物品合成一个块；用了就判 `fail`。
4. 合并后指挥官把还原记录（`06-fidelity-ledger.md`，只在本地）第 5、8 行的状态改为"已验证"，还原方式保持"替代"；机械臂、机器人那一行（决定项）随本包改为"已做"。

## 验证者清单

1. 白名单；链 A 租约只在本包手里；`design_compile.py` 的改动只有交付物 8 和 9 准许的两处（交给 PARAM 检查的 form 多两项；`place_floor` 不夹自己会夹的件），别的一行不动。
2. 先红后绿。
3. 变异：把 `label` 校验去掉，`test_prop_block_label_is_closed_enum` 应变红；把 `z_m` 忽略掉，`test_prop_block_sits_on_z_and_keeps_its_size` 应变红。
4. 破坏性输入：`size_m` 任一值为 0 或负必须拒绝；顶面高于限高必须按落地件的现有规则削到限高并记 `adjustments`。
5. 证据由 Blender 通道出、核对 agent 判，不由本验证者判；两行的 `verdict.json` 都为 `pass` 之前不合并。
