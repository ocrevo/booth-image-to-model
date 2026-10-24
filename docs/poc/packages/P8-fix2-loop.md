---
id: P8-fix2
title: 闭环修复二：读图坐标系核对、移动比同一部分、落地家具不上下、跳过的发现留记录、顶框改尺寸
goal: 让闭环产出的几何候选是对的，读图把展台转错方向时在编译前纠正，为锚点二第四次跑和 P8d 做准备
depends_on: [P8a, P8b, P8c, P7-fam-canopy]
model: sonnet
lane: worker
allowed_files:
  - tools/poc/loop/orchestrator.py
  - tools/poc/loop/edits.py
  - tools/poc/loop/stubs.py
  - tools/poc/tests/test_edits.py
  - tools/poc/tests/test_loop.py
forbidden_files:
  - tools/poc/prompts/**
  - tools/poc/loop/roles.py
  - tools/poc/loop/selfplay.py
  - tools/poc/loop/catalog.py
  - tools/poc/score/**
  - booth-image-to-model/**
  - docs/**
  - web/**
inputs:
  - docs/poc/02-architecture-and-contracts.md 第 1 节第 2、7 步，第 3 节，第 4.1 节 design 一行，第 4.3 节第六次补充
  - docs/poc/03-scoring-spec.md 第 6 节
  - tools/poc/loop/edits.py 的 SpecView、main_body、pixel_move_m、findings_to_edits、SIZE_FROM_SPEC
  - tools/poc/loop/orchestrator.py 的 reader_step、next_candidates、judge_step、decide
deliverables:
  - tools/poc/loop/edits.py：纯函数 rotate_design（整体转 1 到 3 个直角）和 frame_check（用拟合相机和 evidence 比 IoU，按 02 第 4.1 节的三个门槛决定转不转）；移动时的主体选择和长度比检查；落地家具不上下移；canopy_frame 改尺寸；跳过理由如实
  - tools/poc/loop/orchestrator.py：reader_step 在套上拟合占地之后、第一次编译之前调 frame_check，写 round_1/frame_check.json 和 loop.log；next_candidates 写 round_N/fixer_skipped.json 和每条一行日志；round.json 加 judge_outcome；文件头的布局说明同步
  - tools/poc/loop/stubs.py：桩裁判能按测试给定的回答作答（只在测试需要时改）
tests_first:
  - test_four_quarter_turns_give_back_the_same_design
  - test_a_quarter_turn_moves_every_component_centre_with_the_frame
  - test_frame_check_turns_back_a_design_read_a_quarter_turn_off
  - test_frame_check_keeps_a_design_whose_frame_is_right
  - test_frame_check_keeps_a_symmetric_layout_and_an_unverified_one
  - test_a_table_move_compares_the_top_with_a_top_only_box
  - test_a_box_much_wider_than_the_body_is_a_size_problem_not_a_move
  - test_a_floor_standing_table_is_not_moved_up_or_down
  - test_a_canopy_frame_is_resized_from_its_frame_body_and_clamped_to_the_footprint
  - test_skipped_geometry_findings_are_written_even_when_only_colour_candidates_remain
  - test_judge_outcome_names_agree_both_same_split_and_conflict
  - test_the_reader_frame_check_runs_before_the_first_compile_and_is_logged
acceptance_cmds:
  - python -m unittest discover -s tools/poc/tests -p "test_edits*.py"
  - python -m unittest discover -s tools/poc/tests -p "test_loop*.py"
  - python -m unittest discover -s tools/poc/tests -p "test_roles*.py"
  - python tools/check_docs.py
  - python tools/check_lf.py
effort_h: 6
day: 8
stop_rule: 做不完时先交第 1 到 3 项（坐标系核对、移动比同一部分、落地家具不上下），第 4、5 项写进 notes
---

# P8-fix2 闭环修复二

## 目标

锚点二第三次跑（P8-fix 之后）三个合成场景只有一个改善。三个视角独立诊断加一个挑刺的汇总人的结论（指挥官核过数字）：选胜者和裁判不是瓶颈，按真值，每轮胜者已是该轮给出的最好几何；毛病在候选本身。

1. 一份读图答案把整个展台转了 90°（后墙当成右墙）。各件转回去离真值只有 0.08 到 0.26 m；转错的墙挡在相机和三件家具之间，之后怎么挪都在渲染里看不出来。读图器给的 `evidence.bbox_px` 本来是对的，代码从没拿它核对。
2. 修改器移动圆桌时，拿整张桌子（桌面、立柱、底座）去比批评器只框了桌面的框，竖直方向的长度比只有 0.37，于是把桌子往后挪了 0.49 m；吊顶的框比渲染框宽 1.57 倍，本是尺寸问题，却被当成了移动。
3. 一张落地的桌子被往上抬了 0.33 m。
4. 一个场景三轮都只有改颜色的候选：批评器报的顶框尺寸问题被以不实的理由（"not read from size_m"）跳过，而且连跳过记录都没有；裁判的三种"没有一致结论"都记成同一个值，小结因此写错了。
5. 顶框 `canopy_frame` 的尺寸改不了。

本包只改闭环本身。选胜者的规则（02 第 1 节第 7 步）、真值评估（`selfplay.py`）、评分（`tools/poc/score/`）、三个角色的提示词和请求字段都不改：第四次跑要原样重放第三次的读图答案，读图请求必须逐字节不变。

## 输入

前言 `inputs`。规则以 02 的原文为准（第 4.1 节 design 一行的坐标系核对，第 4.3 节的第六次补充，第 1 节第 7 步的 `judge_outcome`，第 3 节的两份新文件）。工人的 worktree 里没有 `output/`，所有测试都用编造的设计和编造的相机，不读 `output/`。

## 交付物

前言 `deliverables`。要点：

1. 坐标系核对。`rotate_design(design, quarter_turns, footprint)`：整体绕占地中心转 1 到 3 个直角；沿边放的元素 `side` 换名，`along` 在新边方向相反时改成 1 − along；用 `centre_m` 放的元素中心跟着转、`yaw_deg` 加 90 的倍数；`form.open_sides` 和按边写的参数（例如顶框的 `band_m`）的键一起换名；W 不等于 D 时转 90 或 270 度多半仍能编译，只是布局被挤变形，靠门槛把关；编译不过的转法跳过，不能崩（以 02 第 4.1 节为准，含 2026-10-24 的'从编译结果取摆法、转后自检'）。`frame_check(...)`：0、90、180、270 度各编译一次，用拟合相机（和 `camera_math.project` 一致）投影每个元素的主体（第 4.3 节的主体），和该元素的 `evidence.bbox_px` 求 IoU，再对有证据的元素取平均；按 02 第 4.1 节的门槛决定；有证据的元素不到一半时保持原样并记 `frame unverified`。`round_1/frame_check.json` 记每种转法的平均 IoU、编译是否通过、决定和理由。
2. 移动比同一部分（02 第 4.3 节第六次补充）：桌子类的主体取 `_top` 组件；长度比不在 [0.7, 1.43] 时换另一种主体（整件，或盆栽的整盆和只取盆互换）；都不在范围内就不移动，理由照 02 原文。
3. 落地家具不上下移：名单和条件照 02 原文。
4. 记录：`round_N/fixer_skipped.json`（`[{finding_id, element_id, reason}]`），每条在 `loop.log` 里一行 `skipped (finding fN): <理由>`；`round.json` 的 `judge_outcome` 照 02 第 1 节第 7 步，`decided_by` 的取值不变。
5. 顶框改尺寸：`canopy_frame` 进可改尺寸的名单；宽按框体（不含立柱和文字）的宽度比，进深按小步，结果截到占地以内；删掉不实的跳过理由。

## 允许改的文件

前言 `allowed_files`。

## 禁止改的文件

前言 `forbidden_files`。规则文字由指挥官写在 02 里，工人不改文档。

## 先写的测试

前言 `tests_first`，先提交（提交信息 `P8-fix2: tests first`），在基线提交上必须失败。全部用编造的设计和相机：

- 四个直角转回原样；转一个直角后每个组件中心落在原中心转过去的位置（0.01 m 以内）。
- 编一个真设计（墙在 back，两张柜台、一个盆栽、一张圆桌），配一台从右前方斜拍的编造相机，把各件主体投影出来当 evidence：把真设计转 −90 度交给核对，应判出 +90 度并转回（位置误差 0.01 m 以内）；直接交真设计应保持 0 度；180 度对称的布局因为差距不够保持原样；大多数元素没有 evidence 时保持原样并记 `frame unverified`。
- 圆桌的批评器框取桌面的投影、往下平移 6 px、方向 back：移动量不超过 0.06 m、方向对。吊顶的框比渲染框宽 1.5 倍、中心不变、方向 left：不产生编辑，理由是大小不一致。盆栽只框盆、方向 left：照常移动。
- 没写 `z_m` 的落地圆桌收到 up（有框、无框各一次）：跳过并给出理由；画面板收到 up 照常移动；写了 `z_m` 的柜台收到 up 照常允许。
- 6 × 6 的占地，顶框不写 `size_m`，批评器报 bigger、框取 6.0 宽框体的投影：`size_m[0]` 截到 6.0，`size_m[1]` 按小步，能编译；算比例的框不含立柱。
- 桩闭环里批评器只报顶框尺寸、一条 `wrong_kind` 和一条颜色发现：即使只做出颜色候选，`fixer_skipped.json` 也列出两条几何发现和理由。桩裁判依次回答 same/same、A/same、两遍都答 A（第二遍 A、B 对调）：`judge_outcome` 依次是 `both_same`、`split`、`conflict`，`decided_by` 不变。
- 坐标系核对在第一次编译之前跑，写 `frame_check.json` 和日志；读图请求文件和改动前逐字节相同。

已有测试一个都不能削弱。

## 验收命令

前言 `acceptance_cmds`，全部退出码 0。

## 完成的定义

1. 先写的测试和验收命令全部通过；`git diff` 只含 `allowed_files`；已有测试的断言没有放松。
2. 读图请求逐字节不变（`roles.py` 和 `tools/poc/prompts/` 没动）；`Loop.decide` 选胜者的规则不变。
3. 离线预检由指挥官在合并前做（不启动 Blender，只做 design-compile，用 `selfplay.py` 的 `assign_parts` 和 `placement_report`），对上事先登记的预测才合并：锚点二前三次的 9 份第 1 轮读图答案过坐标系核对，只转 synth_0002 第三次那一份（转 90 度），其余 8 份不动；转回去的那份编译后位置中位约 0.10 m、缺 0；第三次跑的几何发现在当时的胜者上过一遍新修改器：synth_0000 第 2 轮那条圆桌移动不超过 0.06 m（第三次是 0.5 m），第 3 轮吊顶的移动被跳过，第 1 轮盆栽的移动和第三次相差 1 mm 以内，第 1 轮圆桌改尺寸仍是 0.7926，第 3 轮圆桌上抬被跳过。

## 验证者清单

1. 白名单；`roles.py`、提示词、`selfplay.py`、`tools/poc/score/` 都没动。
2. 先红后绿。
3. 变异：`rotate_design` 不转 `yaw_deg`、`along` 不翻、门槛写错一个，相应测试应变红；去掉长度比检查，圆桌测试应变红；去掉落地判断，上下测试应变红。
4. 可以用主工作区 `output/poc/anchor2/` 下第三次跑的材料只读地复核"完成的定义"第 3 条的预测，但不启动 Blender、不写 `output/`。
5. `frame_check` 在相机看不到某些元素、evidence 框在画面外、W 不等于 D 时不崩，按规则保持原样。
