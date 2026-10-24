# POC 实施指南：总览

本文件夹是"展台图片转模型：高还原度 POC"的实施指南，给执行 POC 的指挥官（一个 Claude Code 会话）、工人 agent、验证者和决策人照着做。它不重复执行摘要里的论证；论证和背景在决策人手里的 guidebook（外部文档，不在仓库里）。正文中文，文件名、命令、JSON 字段和枚举值一律英文。

## 这个 POC 一句话

给一张展台效果图，系统在 20 分钟内自动生成一个"一眼认出是同一个展台"的可编辑参数化模型，并且在没见过的图上也成立。用 10 个工作日、五步、五个带数字的关口来证明或证伪；人全程约 30 分钟，其余交给 AI。关口和数字只写在一处：[01-scope-and-acceptance.md](01-scope-and-acceptance.md)。

## 指挥官的第一小时（冷启动）

指挥官是一个新开的 Claude Code 会话，除了这个文件夹和仓库，它不知道任何以前的对话。按顺序做：

1. 读 [01-scope-and-acceptance.md](01-scope-and-acceptance.md)、[04-execution-runbook.md](04-execution-runbook.md)、[packages/README.md](packages/README.md)。[02](02-architecture-and-contracts.md) 和 [03](03-scoring-spec.md) 在派相应工作包时读；[05](05-gates-and-closing.md) 在关口日读。
2. 核对环境，三条命令都要干净：

   ```bash
   python booth-image-to-model/scripts/boothctl.py doctor
   git status --short
   git log --oneline -3
   ```

   `doctor` 必须报告 Blender 可用；工作区除 `?? .claude/`（决策人尚未决定是否入库时）外必须干净；`.claude/settings.json` 必须存在并含拦截规则（push、安装、下载），没有就停下来问决策人。
3. 建立看板：把 [templates/board.schema.json](templates/board.schema.json) 的示例和 [templates/gates.example.json](templates/gates.example.json) 复制为 `output/poc/board.json` 和 `output/poc/gates.json`（P0 完成后改用 `python tools/poc/orchestration/board.py` 维护）。`output/` 不入库。
4. 核对图片：`input/poc/` 下应有 `pic_A`（目标图）、`pic_B`、`pic_C`（同类图，第 1 天到第 9 天用）和 `pic_D`、`pic_E`（盲测，第 10 天才打开）。扩展名 jpg 或 png，命令里按实际写。缺哪张就写进当天日报的"需要决策人"一栏，不要自己找图。
5. 派第一批活（第 1 天上午）：P0 派一个 Opus 子 agent；PT 派一个 Sonnet 子 agent 起草，指挥官只把文件转交决策人、不读内容；P1 派一个 Sonnet 工人（它不等 PT，答案卷格式以模板为准）；P4 派一个 Sonnet 子 agent 在主工作区执行（这就是 Blender 通道），指挥官不亲手建模，跑 P4 的 agent 不读答案卷。派工人用 Agent 工具，`subagent_type` 取 `general-purpose`，`isolation` 取 `worktree`，`model` 取卡片写的模型，brief 的形状见 [04 第 3 节](04-execution-runbook.md#3-派活工人分支与合并)。
6. 持有 Blender 令牌：同一时刻只有一个进程能启动 Blender。令牌文件是 `output/poc/.blender.token`，协议在 [02 第 5 节](02-architecture-and-contracts.md#5-blender-令牌)。P0 落地前令牌手工管理：Blender 通道子 agent 开始前由指挥官创建 `output/poc/.blender.token`（内容 `{holder, since, purpose}`），结束后删除；P0 合并后改用脚本。
7. 第 1 天傍晚：验证并合并当天完成的包（P0；P1 若已完成），跑非 Blender 测试，排 Blender 通道的夜间队列；写第一份日报，模板在 [templates/daily-digest.md](templates/daily-digest.md)。
8. 绝不做的事：push、合并进 `main`、任何安装或下载、付费 API、把图片交给云端 agent、改 `AGENTS.md`/`CLAUDE.md`/settings、手改 `release_status.json`/`FILE_MANIFEST.json`/`validation/`、替决策人判关口。完整清单在 [04 第 8 节](04-execution-runbook.md#8-绝不做的事)。

## 角色与阅读顺序

| 角色 | 是谁 | 先读 | 再读 |
|---|---|---|---|
| 指挥官 | 一个 Opus 会话 | 本文、01、04、packages/README | 02、03、05 |
| 工人 | Sonnet 子 agent，各在自己的 worktree | 自己的任务卡 | 02 里卡片引用的契约、03 里相关小节 |
| 验证者 | 与作者不同模型的子 agent | 任务卡的"验证者清单" | 04 第 4 节 |
| 评委 | 三个全新上下文的子 agent | 03 第 4 节 | 无 |
| 决策人 | 项目负责人 | 01、05 | 日报 |
| Blender 通道 | 一个专用 Sonnet 子 agent（在主工作区）；夜间队列可由指挥官本人跑 | 04 第 5 节 | 相关任务卡 |

## 文件地图

| 文件 | 内容 |
|---|---|
| [README.md](README.md) | 本文：总览、冷启动、角色、约定、状态表 |
| [01-scope-and-acceptance.md](01-scope-and-acceptance.md) | 目标、六个决定、五步、五个关口的数字、不做清单、三种结局 |
| [02-architecture-and-contracts.md](02-architecture-and-contracts.md) | 闭环、角色信息视野、`output/poc/` 布局、全部 JSON 契约、Blender 令牌 |
| [03-scoring-spec.md](03-scoring-spec.md) | 评分卡 v1、答案卷、AI 面板协议、锚点一和锚点二 |
| [04-execution-runbook.md](04-execution-runbook.md) | 十天日程、每日循环、派活与合并、验证协议、Blender 通道、日报、关口等待、风险 |
| [05-gates-and-closing.md](05-gates-and-closing.md) | 五个关口的测量步骤、决策人校准、决策备忘模板 |
| `06-fidelity-ledger.md`（只在本地，不入库） | 还原记录：每个特征是真实还原、替代还是暂缺，现在怎么做，以后怎么升级，状态（活文档） |
| [packages/README.md](packages/README.md) | 工作包总表、依赖边、租约链 |
| `packages/P*.md` | 每个工作包一张任务卡 |
| `templates/` | 任务卡、工人回报、看板、关口、答案卷、日报、决策备忘的模板 |

## 约定

- 路径一律相对仓库根；主工作区的图片和结果写作 `input/poc/...`、`output/poc/...`。worktree 里没有 `input/` 和 `output/`，工人只处理任务卡给的输入副本，不读图片。
- 日期写 `YYYY-MM-DD`；"第 N 天"是顺序号，不是日历：它只规定先后和依赖。提前做完就立刻做下一个可派的包，永远不停下来等时间；只有关口要等决策人，等的时候不依赖该关口的包照常进行。
- 工作包 id：`P0`、`PT`、`P1` 到 `P9`，引擎包 `P7-<area>-<name>`，闭环包 `P8a` 到 `P8d`。分支名 `poc/<id>`，小写。
- 代码根 `tools/poc/`：`score/`、`loop/`、`orchestration/`、`prompts/`、`tests/`。skill 本体 `booth-image-to-model/` 只在 P7 系列里改，且每次只租给一个包。
- 验证者的模型与作者不同：作者是 sonnet 则验证者用 opus，反之用 sonnet。夜间的 Blender 队列和评委由指挥官本人或 Blender 通道子 agent 跑，会话整夜保持打开；决策人事先把电脑设为不休眠并放行所需权限。
- 文档里不写绝对路径、客户名、图片文件名；`python tools/check_docs.py` 会拒绝。
- 数字先写死再跑：关口阈值只在 01 里改，改了要在日报里说明。

## 状态表

指挥官每天更新一次；证据是 `output/poc/` 下的文件路径。

| 步 | 状态 | 证据 |
|---|---|---|
| 1 尺子（P0、PT、P1、P2、P3、P4） | 进行中：各包已合并；G1 试算未过，按 `output/poc/memo/g1_plan.md` 先修输入再正式跑 | `output/poc/board.json`、`output/poc/digests/` |
| 2 天花板（P5） | 完成：G2 通过（决策人 2026-10-23 批准） | `output/poc/gates.json`、`output/poc/gates_evidence/G2.json` |
| 3 补引擎（P7 系列、P5b） | 进行中：P7-fit-camera（skill 3.1.0-rc1）和 P7-mat-projected（3.1.0-rc2）已合并，其余按链 A、链 B 依次做 | `output/poc/board.json`、`output/poc/verifications/` |
| 4 闭环（P6、P8a 到 P8d） | 进行中：P6、P8a、P8b 已合并，P8c 第一次试跑曲线未涨，修复包 P8-fix 在验证 | `output/poc/board.json` |
| 5 盲测（P9） | 未开始 | |
