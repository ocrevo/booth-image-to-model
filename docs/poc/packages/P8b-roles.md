---
id: P8b
title: 模型角色：读图、批评、裁判的提示词和请求响应协议
goal: 三个模型角色各自只看到该看的东西，按封闭契约作答，响应经代码校验
depends_on: [P8a, P2]
model: opus
lane: worker
allowed_files:
  - tools/poc/prompts/reader.md
  - tools/poc/prompts/critic.md
  - tools/poc/prompts/judge.md
  - tools/poc/loop/roles.py
  - tools/poc/loop/orchestrator.py
  - tools/poc/tests/test_loop.py
  - tools/poc/loop/catalog.py
  - tools/poc/tests/test_roles.py
forbidden_files:
  - booth-image-to-model/**
  - web/**
inputs:
  - docs/poc/02-architecture-and-contracts.md 第 2、4、6 节
  - booth-image-to-model/prompts/booth_design.md（读图器填 design 的写法）
  - booth-image-to-model/schemas/booth-design.v1.json
deliverables:
  - tools/poc/prompts/reader.md、critic.md（judge.md 由 P2 建，本包只补破平局模式）
  - tools/poc/loop/roles.py：write_request、wait_response（超时可配）、validate（按契约 schema）
  - tools/poc/loop/catalog.py：为每个 kind 生成一个最小 design.json 和一行说明，写 catalog.json；缩略图由 Blender 通道用 preview 渲染后放进 tools/poc/prompts/catalog/
tests_first:
  - test_response_must_match_schema_or_is_undecided
  - test_each_role_inputs_folder_contains_only_its_diet
  - test_catalog_covers_every_kind_in_schema
acceptance_cmds:
  - python -m unittest discover -s tools/poc/tests -p "test_roles*.py"
  - python tools/poc/loop/catalog.py --help
  - python tools/check_docs.py
  - python tools/check_lf.py
effort_h: 4
day: 7
stop_rule: 缩略图渲染不完就先用文字目录跑 P8c
---

# P8b 模型角色

## 目标

提示词只做一件事：让一个全新上下文的子 agent 看请求文件夹里的东西，按契约写 JSON。不解释，不聊天。

## 做法

1. `reader.md`：输入是原图、`catalog.json`（加缩略图）、`box_fit.json` 的尺寸（它没有开放边，开放边由读图器自己判断）；输出 `reader.v1`。规则：只能用目录里的 kind；文字只能是占位字；图里有而目录里没有的写进 `unexpressible`；每个元素给 `bbox_px` 和 `confidence`。
2. `critic.md`：输入是 `critic_inputs/el_<element_id>/` 里的原图、渲染和叠加图三张小图（文件名以 P8a 已合并的为准）、裁片在原图上的位置和缩放，加每个元素的 kind；输出 `critic.v1`（2026-10-23 改，与 02 第 2 节一致）。看不到读图器的 JSON 和分数。
3. `judge.md`：补破平局模式（两张渲染加原图，`better` 为 A、B、same），用 P8a 的布局：两遍是两个独立请求 `round_N/judge_p1.request.json`、`judge_p2.request.json`（各自的 `judge_p1_inputs/`、`judge_p2_inputs/`），每个请求由一个全新上下文的子 agent 回答一个 `judge.v1` 对象（02 第 6 节第 4 条），不用 `panel.py --pairwise`；两遍不一致记 `same`。同时改掉评分模式里"答案写到相对于面板文件夹的 respond_to、指挥官会告诉你面板文件夹"的说法：所有角色都只按 brief 给的响应文件完整路径写，提示词里不出现面板文件夹（那里有 `mapping.secret.json` 和其他评委的答案）。
4. `roles.py`：请求文件布局以 P8a 已合并的 `round_N/<role>.request.json` 和 `<role>_inputs/` 为准，不另起一套；`write_request(role, round_dir, payload, inputs)` 只复制该角色允许看的文件；`wait_response(path, timeout_s, newer_than, remaining_s)` 轮询（保留 P8a 已有的两条行为：比请求旧的响应不算、等待不超过 `--max-minutes` 剩余时间），也服务 `judge_best_p1`、`judge_best_p2` 这对请求；`validate(role, response, request)` 按契约，并核对请求里的 round、candidate_id、pair_id、pass、judge_id（P8a 已有的检查不能丢），失败返回 `undecided` 并记录原因；状态沿用 P8a 的 ok、no_response、invalid。
5. `catalog.py`：读 schema 的 `kind` 枚举，每个 kind 写一个最小设计；指挥官在 Blender 通道渲染缩略图。
6. 接线：把 `orchestrator.py` 里手写的请求写出和响应校验换成调用 `roles.py`（行为不变，P8a 的测试照样通过），读图器的请求文件夹加入 `catalog.json` 和缩略图（`orchestrator.py` 新增可选的 `--catalog <dir>`）。只为接线改 `orchestrator.py` 和 `test_loop.py`，不改闭环逻辑；唯一例外：平局判断按 6 位小数比较边缘距离之差（`score.py` 把它取到 6 位，现在恰好相差 0.002 会因浮点误差被算成平局），加一个测试。

## 允许改的文件

前言 `allowed_files`。

## 禁止改的文件

skill 本体、`web/`。

## 验收命令

前言 `acceptance_cmds`。

## 完成的定义

三份提示词各不超过一页；`roles.py` 的测试通过；`catalog.json` 覆盖 schema 的每个 kind。

## 验证者清单

1. 白名单。
2. 先红后绿。
3. 变异：把 `validate` 的 schema 校验去掉，`test_response_must_match_schema_or_is_undecided` 应变红。
4. 破坏性阅读：验证者读三份提示词，各写一个最可能诱导自由文本或泄露视野的漏洞并修掉。
