---
id: P2
title: 评委面板：盲化、请求文件、汇总
goal: 把候选渲染变成三个评委能盲打的请求，再把 judge.v1 响应汇总成逐项多数和自洽率
depends_on: [P1]
model: sonnet
lane: worker
allowed_files:
  - tools/poc/score/panel.py
  - tools/poc/prompts/judge.md
  - tools/poc/tests/test_panel.py
forbidden_files:
  - booth-image-to-model/**
  - web/**
inputs:
  - docs/poc/03-scoring-spec.md 第 4 节
  - docs/poc/02-architecture-and-contracts.md 第 4.4 节、第 4.10 节和第 6 节
  - tools/poc/score/score.py 的聚合函数和 --panel、--candidate-id（P1）
deliverables:
  - tools/poc/score/panel.py：prepare、tally、calibration-form
  - tools/poc/prompts/judge.md：评委提示词，只允许 judge.v1 的封闭答案
tests_first:
  - test_pairs_are_blinded_and_shuffled_with_seed
  - test_judge_inputs_contain_no_mapping_or_scores
  - test_tally_majority_and_flags_inconsistent_judge
  - test_verdict_schema_rejects_free_text_and_extra_fields
acceptance_cmds:
  - python -m unittest discover -s tools/poc/tests -p "test_panel*.py"
  - python tools/poc/score/panel.py --help
  - python tools/check_docs.py
  - python tools/check_lf.py
effort_h: 5
day: 2
stop_rule: calibration-form 可以推到第 3 天，prepare 和 tally 第 2 天必须可用
---

# P2 评委面板

## 目标

实现 [03 第 4 节](../03-scoring-spec.md#4-ai-面板协议)。评委是指挥官派的三个全新上下文子 agent；本包只做文件：准备它们能看的东西，收它们的 JSON。

## 输入

候选文件夹里每个候选一个子文件夹，含 `panel_view.png`（P1 产出，给评委的无字叠图；`sheet.png` 带分数和留出点，不给评委）和 `score.json`；答案卷的 `inventory.json`。

## 交付物

```bash
python tools/poc/score/panel.py prepare --candidates <dir> --truth <truth_dir> --out <panel_dir> --seed <n> [--pairwise]
python tools/poc/score/panel.py tally --panel <panel_dir> --out <tally.json>
python tools/poc/score/panel.py calibration-form --candidate <dir> --truth <truth_dir> --out <form.md>
```

- `prepare`：把每个候选的 `panel_view.png` 复制为随机 8 位名；评分模式为每个候选生成三份请求（`j1`、`j2`、`j3`），每份两遍（`pass` 1 和 2，清单项顺序互换）；`--pairwise` 时把候选两两配对，随机 A/B，每对两遍、第二遍 A/B 互换。真实名字到随机名的映射写在 `<panel_dir>/mapping.secret.json`；评委只拿到 `<panel_dir>/requests/<judge>/<request_id>/`，里面只有图和清单，没有映射和分数。
- `tally`：读 `responses/`，按 `mapping.secret.json` 把响应分到各候选，逐项多数和四个面板分调用 P1 在 `tools/poc/score/` 里公开的聚合函数（只有一套实现，不另写）；同一评委两遍不一致的项标 `undecided`，不计入该评委；缺一遍的评委整体 `undecided`；输出 `tally.v1`（[02 第 4.10 节](../02-architecture-and-contracts.md#410-tallyv1面板汇总)）：每个评委的自洽率、三人两两一致率、每个候选的 `L1`、`L2_panel`、`L3`、`L4_panel` 分、逐项结果和 `extra_parts`，供 `score.py --panel <tally.json> --candidate-id <id>` 读取。聚合函数的返回值键名是 `L2`、`L4`，逐项结果是 `l1`、`l2` 两个字典加 `undecided_items`；写 `tally.v1` 时改成 `L2_panel`、`L4_panel`，并按清单给每一项补齐 `L1:<id>` 和 `L2:<id>`（没有多数的写 `undecided`）。一份响应给了 `L2:<id>` 却把同一项判为 `L1` missing，按无效响应处理（02 第 4.4 节：`L2` 只对判为 present 的项给）。
- `calibration-form`：给决策人的表，Markdown，逐项 `present`/`missing` 和 `ok`/`off`/`wrong` 的空格。
- `judge.md`：告诉评委只看图和清单，按 `judge.v1` 作答：评分模式下每个清单项给 `L1:<id>`（present 或 missing），判为 present 的再给 `L2:<id>`（ok、off 或 wrong），填 `extra_parts`（模型里多出的、原图没有的部件数）；`pair_id` 是该候选的盲化 id。不解释，不写自由文本。

## 允许改的文件

前言 `allowed_files`。

## 禁止改的文件

skill 本体、`web/`。

## 先写的测试

前言四个测试先提交。测试用临时目录和程序生成的小图。

## 验收命令

前言 `acceptance_cmds`。

## 完成的定义

`prepare` 的输出目录结构固定且评委目录不含映射；`tally` 对手写的响应夹具给出预期的多数和自洽率；`score.py --panel` 能读 `tally.json`。

## 验证者清单

1. 白名单。
2. 先红后绿。
3. 变异：把 `tally` 的多数判定改成"取第一个评委"，`test_tally_majority_*` 应变红。
4. 破坏性输入：响应里多一个字段必须拒绝；缺一遍的评委必须整体标 `undecided`，不崩溃。
