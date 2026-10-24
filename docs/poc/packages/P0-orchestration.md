---
id: P0
title: 编排脚手架：看板、关口、Blender 令牌、卡片检查器、日报
goal: 让指挥官第 1 天中午起就能用命令维护看板、关口和令牌，并机械检查任务卡
depends_on: []
model: opus
lane: worker
allowed_files:
  - tools/poc/__init__.py
  - tools/poc/orchestration/__init__.py
  - tools/poc/orchestration/board.py
  - tools/poc/orchestration/gates.py
  - tools/poc/orchestration/blender_token.py
  - tools/poc/orchestration/check_cards.py
  - tools/poc/orchestration/digest.py
  - tools/poc/tests/__init__.py
  - tools/poc/tests/test_board.py
  - tools/poc/tests/test_token.py
  - tools/poc/tests/test_check_cards.py
  - docs/poc/templates/board.schema.json
  - docs/poc/templates/gates.example.json
forbidden_files:
  - booth-image-to-model/**
  - web/**
  - docs/poc/*.md
inputs:
  - docs/poc/02-architecture-and-contracts.md 第 4.7 到 4.9 节和第 5 节
  - docs/poc/templates/task-card.md
  - docs/poc/templates/board.schema.json
deliverables:
  - tools/poc/orchestration/board.py（init、show、set、lease、unlease）
  - tools/poc/orchestration/gates.py（init、show、measure）
  - tools/poc/orchestration/blender_token.py（acquire、release、status，带 --force）
  - tools/poc/orchestration/check_cards.py（检查 docs/poc/packages）
  - tools/poc/orchestration/digest.py（从看板、关口和回报生成日报骨架）
tests_first:
  - test_board_transitions_reject_illegal_state
  - test_board_lease_is_exclusive
  - test_token_is_exclusive_and_expires_after_30_minutes
  - test_cards_front_matter_parses_ids_unique_dag_acyclic
  - test_cards_allowed_and_forbidden_disjoint
acceptance_cmds:
  - python -m unittest discover -s tools/poc/tests -p "test_board*.py"
  - python -m unittest discover -s tools/poc/tests -p "test_token*.py"
  - python -m unittest discover -s tools/poc/tests -p "test_check_cards*.py"
  - python tools/poc/orchestration/check_cards.py docs/poc/packages
  - python tools/poc/orchestration/blender_token.py --help
  - python tools/check_docs.py
  - python tools/check_lf.py
effort_h: 4
day: 1
stop_rule: 超过 6 小时未完成则只交付 board、gates、blender_token 三件，其余转次日
---

# P0 编排脚手架

## 目标

指挥官用五个小脚本维护 POC 的状态，不靠记忆。脚本只读写 `output/poc/` 下的 JSON 和 `docs/poc/packages/` 的卡片，标准库实现，不加依赖。

## 输入

- [02 第 4.7 到 4.9 节](../02-architecture-and-contracts.md#47-任务卡前言每张-packagespmd-顶部)：卡片前言、`board.v1`、`gates.v1` 的字段。
- [02 第 5 节](../02-architecture-and-contracts.md#5-blender-令牌)：令牌协议。
- [templates/board.schema.json](../templates/board.schema.json)、[templates/gates.example.json](../templates/gates.example.json)。

## 交付物

| 脚本 | 子命令 | 行为 |
|---|---|---|
| `board.py` | `init --out <board.json>`；`show`；`set <id> <status> [--owner --branch --worktree --verifier --report]`；`lease <file> <id>`；`unlease <file>` | 状态只能按 `todo → leased → in_progress → verify → merged` 前进，或进入 `blocked`、`dropped`；非法转换退出码 2 |
| `gates.py` | `init --out <gates.json>`；`show`；`measure <G> --value <v> --threshold <t> --evidence <path>`；`decide <G> pass|fail|waived --by owner|commander` | `measure` 只写 `value`、`threshold`、`measured_at`、`evidence_path`；`decide` 写 `status` 并记录 `--by`：G1 允许 `commander`，G2 到 G5 和 `waived` 只允许 `owner` |
| `blender_token.py` | `acquire --holder <name> --purpose <text> [--token <path>]`；`release [--force]`；`status` | 令牌文件 `output/poc/.blender.token`，`O_CREAT` 加 `O_EXCL` 创建；`status` 在持有超过 30 分钟且 `pid` 不存在时报 `stale`；`acquire` 拿不到退出码 3 |
| `check_cards.py` | `<folder>` | 解析每张卡的前言；id 唯一；`depends_on` 指向存在的 id 且无环；`allowed_files` 与 `forbidden_files` 不相交（`forbidden_files` 为 `"*"` 表示"其余一切"，不参与相交检查）；`acceptance_cmds` 每条以 `python` 或 `BLENDER_EXE= python` 开头，且脚本路径存在、或由本卡或其 `depends_on` 传递闭包内某张卡的 `deliverables` 创建；有问题退出码 1 并逐条列出 |
| `digest.py` | `--board --gates --reports <dir> --day <N> --out <md>` | 按 [templates/daily-digest.md](../templates/daily-digest.md) 生成五段骨架，数字留给指挥官填 |

## 允许改的文件

见前言 `allowed_files`。模板 JSON 可以补字段，不能删字段。

## 禁止改的文件

`booth-image-to-model/` 下的一切、`web/`、`docs/poc/*.md`。

## 先写的测试

前言 `tests_first` 的五个测试先写先提交（提交信息 `P0: tests first`），在基线提交上必须失败。测试用临时目录，不写 `output/`。

## 验收命令

前言 `acceptance_cmds`，全部退出码 0。`check_cards.py docs/poc/packages` 在当前的全部卡上必须通过；它报出的卡片问题由指挥官改卡，不由本包改。

## 完成的定义

五个脚本可用，`--help` 齐全，测试通过，`check_cards.py` 对现有卡片通过，`worker.v1` 回报已交。

## 验证者清单

1. 白名单：diff 没有碰 `booth-image-to-model/` 和 `web/`。
2. 先红后绿：在 `red_commit` 上五个测试失败，在 `green_commit` 上通过。
3. 变异：把 `blender_token.py` 里的 30 分钟改成 0 分钟，`test_token_is_exclusive_and_expires_after_30_minutes` 必须变红。
4. 破坏性输入：两个进程同时 `acquire` 只能有一个成功；`board.py set` 从 `merged` 回到 `todo` 必须退出码 2。
