---
id: P8a
title: 闭环骨架：编排脚本、修改目录、桩角色、停止规则
goal: 不接模型、不开 Blender 也能把闭环的每一步跑通并测试
depends_on: [P1, P7-fit-camera]
model: sonnet
lane: worker
allowed_files:
  - tools/poc/loop/orchestrator.py
  - tools/poc/loop/edits.py
  - tools/poc/loop/stubs.py
  - tools/poc/loop/renderer.py
  - tools/poc/tests/test_loop.py
  - tools/poc/tests/test_edits.py
forbidden_files:
  - booth-image-to-model/**
  - web/**
inputs:
  - docs/poc/02-architecture-and-contracts.md 第 1、4、6 节
  - tools/poc/score/score.py 的命令行（P1）
  - booth-image-to-model/schemas/booth-design.v1.json（只读）
deliverables:
  - tools/poc/loop/orchestrator.py：--picture --fit --truth --design-init --out [--map <json>] [--max-rounds 4] [--max-minutes 10] [--stub]
  - tools/poc/loop/edits.py：fixer.v1 的每个 op 的实现，只接受保持 schema 合法的结果
  - tools/poc/loop/stubs.py：桩读图器（原样返回 design-init）、桩批评器（按两个 design 的差异生成 findings）、桩裁判（按 score.json 比较）
  - tools/poc/loop/renderer.py：真实模式调 boothctl.py design-compile --fit <box_fit.json> --image <原图>（两个参数必须同时给）和 preview（每个候选一个 --out-root，先拿令牌）；桩模式复制一张夹具图
tests_first:
  - test_every_edit_op_keeps_design_schema_valid
  - test_invalid_edit_is_rejected_and_logged_not_applied
  - test_loop_stops_on_rounds_minutes_and_plateau
  - test_best_candidate_is_kept_not_the_last
  - test_round_folder_layout_matches_contract
acceptance_cmds:
  - python -m unittest discover -s tools/poc/tests -p "test_loop*.py"
  - python -m unittest discover -s tools/poc/tests -p "test_edits*.py"
  - python tools/poc/loop/orchestrator.py --help
  - python tools/check_docs.py
  - python tools/check_lf.py
effort_h: 8
day: 5-6
stop_rule: 第 6 天末骨架必须能在桩模式下跑完 4 轮；真实渲染模式可以留一个 TODO 给 P8c 前的半天
---

# P8a 闭环骨架

## 目标

实现 [02 第 1 节](../02-architecture-and-contracts.md#1-闭环) 的八步，把模型角色和 Blender 都做成可替换的接口：`--stub` 时用桩，真实模式时按 [02 第 6 节](../02-architecture-and-contracts.md#6-模型角色作为请求与响应文件) 写请求文件并等待响应。

## 做法

1. 每轮在 `<out>/round_N/` 写：`reader.request.json`（第 1 轮）、候选们 `candidates/<id>/design.json`、编译和渲染结果、`score.json`、`critic.request.json`、`critic.response.json`、`fixer.v1` 的编辑、`judge.*`、`round.json`（本轮胜者、分数、用时）。
2. `edits.py` 对 `design.json` 做 `move`、`resize`、`recolour`、`add`、`remove`、`replace_kind`、`set_count`、`set_text`；每次编辑后用 `jsonschema` 校验，失败就跳过并记日志。
3. 候选数最多 3 加原样 1；先比 `score.json` 的 `aux.edge_distance_diag`，小的胜；两者相差不到 0.2 % 对角线时才写破平局请求（03 第 4 节第 6 条；`total` 在闭环里只含代码分，只记录不作主判据；多个候选同时平局时按 02 第 1 节第 7 步）（`judge_p1.request.json` 和 `judge_p2.request.json`，见 02 第 6 节第 4 条）。
4. 停止：分数不再上升（连续两轮无提升）、满 `--max-rounds`、超过 `--max-minutes`；结束时把最佳候选复制到 `<out>/best/`。
5. 裁片：按候选 spec 每个部件的网格顶点（文字按字高和字数估一个矩形）经 `camera_math.project` 投影得到像素框（比 8 个包围盒角更紧，落在其内），四周加 max(6 px, 框尺寸的 25 %) 并裁到画面内，裁出原图、渲染和叠加图三张到 `critic_inputs/el_<element_id>/`，请求里附裁片在原图上的位置和缩放（02 第 2 节；2026-10-23 改）。
6. 打分：每个候选在自己的文件夹里调 `tools/poc/score/score.py`（它把 `sheet.png`、`panel_view.png`、`score_detail.json` 写在 `score.json` 旁边，固定文件名，所以一个文件夹只放一个候选），`--candidate-id` 必填，取候选 id；给了 `--map` 就原样传给 `score.py --map`（清单 id 到部件 id 的映射由指挥官在评分时手写，闭环角色看不到它）。

## 允许改的文件

前言 `allowed_files`。只 `import` skill 的 `camera_math` 和 `validate_spec`，只 `subprocess` 调用 `boothctl.py`、`tools/poc/score/score.py` 和 `tools/poc/orchestration/blender_token.py`（每次渲染前 `acquire --wait <秒> --holder <名> --purpose <做什么>`，渲染后 `release --holder <名>`；测试里只断言命令行，不真跑）。

## 禁止改的文件

skill 本体、`web/`。

## 验收命令

前言 `acceptance_cmds`。

## 完成的定义

桩模式下 4 轮跑完，文件布局符合契约，`best/` 存在；真实模式的渲染接口能构造正确的命令行（用测试断言命令字符串，不真跑）。

## 验证者清单

1. 白名单。
2. 先红后绿。
3. 变异：把"保留最佳候选"改成"保留最后一个"，`test_best_candidate_is_kept_not_the_last` 应变红。
4. 破坏性输入：响应文件是非法 JSON 时本轮按"无法判断"继续，不崩溃；`--max-minutes 0` 必须立刻停。
