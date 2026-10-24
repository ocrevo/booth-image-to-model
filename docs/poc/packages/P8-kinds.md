---
id: P8-kinds
title: 闭环认识链 A 的新 kind：坐标系核对、落地家具、主体和改尺寸
goal: P8d 的闭环在 prop_block、shaped_counter、sweep_member 上能转、能移、能改尺寸，规则和已有 kind 一致，做不了的如实跳过
depends_on: [P7-fam-sweep, P8-fix2]
model: sonnet
lane: worker
allowed_files:
  - tools/poc/loop/edits.py
  - tools/poc/tests/test_edits.py
  - tools/poc/tests/test_loop.py
forbidden_files:
  - tools/poc/prompts/**
  - tools/poc/loop/roles.py
  - tools/poc/loop/catalog.py
  - tools/poc/loop/selfplay.py
  - tools/poc/loop/orchestrator.py
  - tools/poc/score/**
  - booth-image-to-model/**
  - docs/**
  - web/**
inputs:
  - docs/poc/02-architecture-and-contracts.md 第 4.1 节 design 一行和 edits 一行，各自 2026-10-24 补的"链 A 后加的 kind"
  - booth-image-to-model/references/component-recipes.md 的 prop_block、shaped_counter、sweep_member 三节和"链 A 怎么加新 kind"
  - booth-image-to-model/scripts/motifs.py 的 SELF_PLACING_KINDS（只读）
  - tools/poc/loop/edits.py 的 FACING_KINDS、FLOOR_STANDING_KINDS、SIZE_FROM_SPEC、CHECKED_OVERHEAD、rotate_design、frame_check、normalise_placements、main_body、_clamp_lit_panel
deliverables:
  - edits.py：按 02 的两段补充，把 prop_block、shaped_counter 加进按自己转角整体摆放的一类（和 lit_panel 同样转、同样从编译结果取摆法），加进落地家具；sweep_member 转的时候 path_m 各点绕占地中心转、repeat_axis 在奇数个直角时互换，并参加自检
  - edits.py：主体和改尺寸：shaped_counter、prop_block 的主体是整件；没写 size_m 时从编译出的范围取，任意转角下按件自己的轴量；宽按比例、深和高按小步；结果截到编译器的限制以内，截不进去就作废并记理由
  - edits.py：sweep_member 的水平移动平移 path_m 的所有点；有落地点时不上下移；不改尺寸；跳过的理由照 02 原文
  - 能用一张表管的地方用一张表，并让它和 motifs.SELF_PLACING_KINDS 对得上（测试钉住：motifs 里登记的每个自摆放 kind 在闭环里都有归属）
tests_first:
  - test_every_self_placing_kind_is_known_to_the_loop
  - test_a_turned_design_with_a_shaped_counter_and_a_prop_block_passes_the_self_check
  - test_a_sweep_member_path_turns_with_the_frame_and_passes_the_self_check
  - test_a_floor_standing_shaped_counter_or_prop_block_is_not_moved_up_or_down
  - test_a_turned_shaped_counter_is_resized_along_its_own_length_and_clamped
  - test_a_sweep_member_move_shifts_every_path_point_and_resize_is_skipped
  - test_a_sweep_member_with_legs_is_not_moved_up_or_down
acceptance_cmds:
  - python -m unittest discover -s tools/poc/tests -p "test_edits*.py"
  - python -m unittest discover -s tools/poc/tests -p "test_loop*.py"
  - python -m unittest discover -s tools/poc/tests -p "test_roles*.py"
  - python tools/check_docs.py
  - python tools/check_lf.py
effort_h: 4
day: 8
stop_rule: 做不完时先交坐标系核对和落地家具两项（转错方向在 P8d 上最伤），改尺寸和 sweep_member 的移动写进 notes
---

# P8-kinds 闭环认识链 A 的新 kind

## 目标

P7-fam-counter 的第 1 次验证发现：闭环的修改器和读图坐标系核对（`edits.py`）只认识 P8-fix2 时已有的 kind。`prop_block`、`shaped_counter` 会按自己的转角整体摆放，闭环却把它们当成按轴摆放的件：改写摆法时把一个横着的柜台改成竖着的，转法自检因此把含它们的设计一律判 `inexact`，读图转错方向时就转不回来；它们也不在落地家具里，会被上下移；没写 `size_m` 的改尺寸会以"没有改尺寸规则"跳过。`sweep_member` 的路径点不跟着转。这些都是从严失败、不会悄悄算错，但 P8d 在真实图上会失去这些能力。规则已写在 02（两段 2026-10-24 的补充），本包只照着实现。

## 输入

前言 `inputs`。规则以 02 原文为准。工人的 worktree 里没有 `output/`，测试全部用编造的设计、编造的相机和真的编译器（`design_compile`），不读 `output/`。

## 交付物

前言 `deliverables`。要点：

1. 坐标系核对：自摆放的件按编译结果取实际中心和实际转角（和 `lit_panel` 同一条路；注意 2026-10-24 P7-fam-counter 第 2 次验证的发现：`shaped_counter` 的转角做在平面轮廓里，它所有部件的 `rotation_deg` 都是 [-90, 0, 0]，不能像 `_lit_panel_yaw` 那样读 `rotation_deg[2]`，要从编译出的几何取，例如台面部件平面轮廓的长边方向；测试要覆盖转 30 度和 90 度的柜台），`prop_block` 和 `shaped_counter` 作为落地件参加自检；`sweep_member` 的 `path_m` 绕占地中心转，`repeat_axis` 在转 90、270 度时互换，自检比较它的主体（整根，`count` 复制出的各根都算）。转完编译不过、自检不过的转法照旧不采用。
2. 落地家具：`shaped_counter`、`prop_block` 在没写 `z_m`、编译后底面在地台面上时不上下移；写了 `z_m` 的（例如放在桌上的占位块）照旧。
3. 改尺寸：按件自己的宽、深、高量（任意转角），宽按主体框的宽度比，深和高按小步；截到编译器的限制（占地、封闭墙围出的房间、限高）；截不进去就作废，理由写清楚。
4. `sweep_member`：水平移动平移所有路径点；有 z 为 0 的点时不上下移；不改尺寸。跳过的理由照 02 原文。
5. 读图请求、提示词、选胜者和 `orchestrator.py` 都不改；已有 kind 的行为不变（已有测试一个都不削弱）。

## 允许改的文件

前言 `allowed_files`。

## 禁止改的文件

前言 `forbidden_files`。

## 先写的测试

前言 `tests_first`，先提交（提交信息 `P8-kinds: tests first`），在基线提交上必须失败。断言：`motifs.SELF_PLACING_KINDS` 的每个 kind 在闭环的表里都有归属；一个含转过角的 `shaped_counter` 和一个 `prop_block` 的设计，入口在左侧时转 90 度，自检通过（每件主体框转过去和转后编译的框相差不超过 0.01 m）；一个带 `count` 的 `sweep_member` 转 90 度后自检通过；落地的柜台和占位块收到 up 被跳过，写了 `z_m` 的占位块照常移动；一个转了 30 度的柜台改尺寸沿它自己的长边变，截到房间以内；`sweep_member` 移动后每个路径点都平移同样的量，改尺寸被跳过并写明理由；有落地腿的 `sweep_member` 收到 up 被跳过。

## 验收命令

前言 `acceptance_cmds`，全部退出码 0。

## 完成的定义

1. 先写的测试和验收命令全部通过；`git diff` 只含 `allowed_files`；已有测试的断言没有放松。
2. 读图请求逐字节不变（`roles.py` 和提示词没动）。
3. 锚点二前三次的 9 份第 1 轮读图答案和第四次第二组的 3 份，用本包的代码重做坐标系核对，决定和记录的一样：只有 synth_0002 第三次那一份转 90 度，其余 11 份保持原样（指挥官在合并前只读地复核，不启动 Blender，不写 `output/`）。

## 验证者清单

1. 白名单；`roles.py`、提示词、`orchestrator.py` 没动。
2. 先红后绿。
3. 变异：把 `shaped_counter` 从自摆放的一类里拿掉，转法自检测试应变红；`sweep_member` 的路径点不转，路径测试应变红；去掉落地判断，上下测试应变红。
4. 破坏性输入：转角正好 45 度的柜台、贴着封闭墙的柜台、`path_m` 有点在占地边上的 `sweep_member`、只写了 `id` 和 `kind` 的三种新件，转法和修改都不崩，按规则采用或从严跳过。
