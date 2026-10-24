# 04 执行手册

本文是指挥官照着做的运行手册：十天日程、每日循环、派活与合并、验证协议、Blender 通道、日报、关口等待、绝不做的事、中断恢复和风险。读者：指挥官、工人、验证者、Blender 通道。回到[总览](README.md)。工作包的定义在 [packages/README.md](packages/README.md)，关口的测量在 [05](05-gates-and-closing.md)。

## 1. 十天日程

表里的"天"是顺序和依赖的编号，不是日历。某个包提前完成就立刻派下一个可派的包；一天的活做完了就做第二天的；只有关口要等决策人，而且等的时候不依赖该关口的包继续。10 个工作日是上限，不是目标。

| 天 | 工人通道（并行，worktree） | Blender 通道（串行） | 决策人 | 关口 |
|---|---|---|---|---|
| 1 | P0（Opus）、P1 | P4 基线（pic_A、B、C） | 确认 PT 答案卷（10 分钟） | |
| 2 | P2、P3 代码 | P3 六个变体渲染并打分；P4 打分 | | G1 |
| 3 | P7-fit-camera | P5 慢 agent 建 C（Opus） | | |
| 4 | P6 | P5 继续；C 打分；锚点一对 C 重跑 | G2 校准打分（10 分钟）并拍板 | G2 |
| 5 | P7-fam-canopy、P7-fam-litpanel（链 A 串行）；P7-mat-projected（链 B）；P8a | 每个 P7 包合并前的证据预览；夜里：链 A/B 合并后的全量回归 | | |
| 6 | P7-fam-propblock、P7-fam-counter；P7-render-dark；P8a 收尾 | 每个 P7 包合并前的证据预览 | | |
| 7 | P7-fam-sweep；P7-mat-pattern；P8b | P5b 建 C′ 并打分（两条链做完后开始） | 拍板 | G3（第 3 步做不完推到第 8 天） |
| 8 | 修 P8 的问题 | P8c 合成自博弈 | | |
| 9 | 修 P8 的问题 | P8d 三张真实图 | 看曲线 | G4 |
| 10 | 决策备忘草稿 | P9 两张盲测图 | 看并排图并拍板 | G5 |
| 11 | 决策备忘定稿 | | 收到备忘 | |

并行数不设硬上限，按限额和机器负载自适应：限额告急先停新派活，让在跑的收尾；决策人白天用电脑时 Blender 通道只跑预览，高清和全量回归放夜里。

## 2. 每日循环

1. 早上：读 `output/poc/board.json` 和 `gates.json`；读昨晚 Blender 通道的结果；把今天的包按 [packages/README.md](packages/README.md) 的依赖派出去；更新看板。
2. 白天：每个工人回报后立刻验证（第 4 节），通过就合并进 `poc/integration`（第 3.3 节）；租约到期的热点文件释放给下一个包。
3. 傍晚：跑不用 Blender 的检查；排好夜里的 Blender 队列（预览、锚点渲染、全量回归、高清按这个顺序）；写日报（第 6 节）。
4. 夜里：Blender 通道按队列跑；评委面板对白天产出的候选打分；指挥官只做验证和合并，不写业务代码。
5. 关口日：按 [05](05-gates-and-closing.md) 测量，把数字和证据路径写进 `gates.json`，等决策人。

## 3. 派活、工人分支与合并

### 3.1 派一个工人

用 Agent 工具，`subagent_type` 取 `general-purpose`，`isolation` 取 `worktree`，`model` 取任务卡的 `model`，后台运行。brief 只写这七行，其余都在任务卡里（卡片文件名见 [packages/README.md](packages/README.md) 的表）：

```text
你是工作包 <id> 的工人。仓库根是你的工作目录；它是一个 git worktree。
1. 读 docs/poc/packages/<卡片文件名>，只做卡上写的，只改 allowed_files。
2. git checkout -b poc/<id>；先写 tests_first 里的测试并提交（提交信息 "<id>: tests first"），再实现并提交。
3. 依次跑 acceptance_cmds，全部退出码为 0 才算完成；不要跑 run_tests.py，不要碰 Blender，不要安装任何东西。
4. 最后一条消息只放一个 worker.v1 JSON（docs/poc/02-architecture-and-contracts.md 第 4.6 节），不放解释。
5. 卡上没写的决定，记在 notes 里，不要问。
6. 提交信息不加任何署名行（不加 Co-Authored-By 之类）。
```

指挥官把工人最后一条消息存为 `output/poc/reports/<id>.json`。worktree 里没有 `input/` 和 `output/`，所以任务卡给工人的输入都是仓库内的测试夹具或卡里写明的小文件。

### 3.2 工人的分支规则

- 分支名 `poc/<id>`，小写；一个包一个分支一个 worktree。
- 只改 `allowed_files`；`git diff --name-only <基线>...poc/<id>` 超出白名单，验证者直接退回。基线是分支开出的地方：第 1 天从 `main` 开出的包用 `main`，之后从 `poc/integration` 开出的包用 `poc/integration`。
- CHANGELOG、版本号、README 由指挥官在合并时统一改，工人不碰。
- 热点文件（`design_compile.py`、`motifs.py`、`booth-design.v1.json`、`design_check.py`、`test_design_compile.py`、`blender_build.py`、`material_patterns.py`、`validate_spec.py`、`test_studio_macros.py`、`boothctl.py`）一次只租给一个包；租约写在 `board.json` 的 `leases`。

### 3.3 合并

1. 第一次：在主工作区 `git branch poc/integration main`。
2. 每个通过验证的包：`git checkout poc/integration`，`git merge --no-ff poc/<id>`，冲突时退回给工人在自己的 worktree 上 `git rebase poc/integration` 后重交。
3. 合并后跑 `python tools/check_lf.py`、`python tools/check_docs.py`、`python -m unittest discover -s tools/poc/tests`，以及被改动的 skill 测试文件：`python -m unittest discover -s booth-image-to-model/tests -p <文件名>`（`BLENDER_EXE` 为空）。
4. 夜里 Blender 通道在 `poc/integration` 上跑 `python booth-image-to-model/tests/run_tests.py --require-native --output output/_validation/poc-day<NN>`，要求零失败零跳过。
5. `main` 在 POC 期间不动；只在关口通过且决策人同意时把 `poc/integration` 快进到 `main`。永不 push。
6. 合并 skill 代码（P7 系列）时由指挥官按 AGENTS.md 的规则写 CHANGELOG 条目并升版本号；`tools/poc/` 不属于 skill，不需要。

## 4. 验证协议

每个包合并前由一个与作者不同模型的验证者做，验证者只拿任务卡和 diff：

1. 白名单：`git diff --name-only <基线>...poc/<id>`（基线同 3.2 节）的每个文件都在 `allowed_files` 里。
2. 先红后绿：在 `red_commit` 上跑该包的测试必须失败，在 `green_commit` 上必须通过。命令：`git checkout <red_commit>` 后跑卡上的测试命令，再 `git checkout poc/<id>` 重跑。
3. 变异检查：验证者在自己的临时 worktree 里故意改坏实现里的一行关键逻辑（例如把一个阈值改成 0，或把一个返回值取反），跑该包的测试；至少一条测试必须变红，否则判为空测试，退回。改动不提交。
4. 验收命令：逐条重跑 `acceptance_cmds`，记录退出码和秒数，不信工人口头报告。
5. 破坏性阅读：验证者读 diff 时的任务是"把它弄坏"，写出三个最可能的失败输入并试一个。
6. 结论写进 `output/poc/reports/<id>.json` 的 `mutation` 和 `notes`（`worker.v1` 的 `status` 不改）；看板状态由指挥官改：通过则 `verify` 到 `merged`，退回则回到 `in_progress`。每一轮验证的三份完整结果和随后指挥官的返工说明存进 `output/poc/verifications/<id>/`（每轮一个 `roundN_<run>.json`，加一份按时间排的 `review_log.md`）；工人每轮的回报另存为 `reports/<id>.roundN.json`，不覆盖。
7. 复验：返工后的验证必须带上前几轮的全部发现（复测清单），逐条实测并写明是否修好；修复时每个发现都要变成一条没有修复就会失败的测试。发现揭出文档空缺或矛盾时，指挥官在 `docs/poc` 里写一条带日期的裁定，并在提交信息里写明来自哪次验证。
8. 常见漏洞（2026-10-22 到 23 日的验证里反复出现，验证者逐条对照）：
   - 测试只查源码文字、不真跑，改坏实现照样全绿；要端到端跑一遍。
   - 先写的测试在红提交上就是绿的（实现早已存在）；这种测试只算变异守卫，要用变异证明它有效。
   - 比较的两个量不是同一部分：整件的框对子部件的框、被画框截断的框对没截断的框、字段值的变化对实际的移动。
   - 方向和符号的约定没写清：`bigger` 指谁变大，`delta` 是哪个量的变化。
   - 规则在分界附近不稳（45° 对角线、平局）；要有余量规则和平局规则，测试要覆盖分界两侧。
   - 校验放行、构建却失败或结果荒谬；要有'校验通过就一定能建出合理结果'的参数扫描测试。
   - 比较的区域或分母随候选变化（例如只在候选自己占的区域里量），删东西反而得分。
   - 汇总规则不单调（例如'没有多数记 0'），答得更好反而分更低；要有单调性测试。
   - 静默丢弃或静默改写（空对象被丢、参数被忽略、引擎被悄悄换掉）；要报错或警告。
   - 缺省行为要逐字节不变：比较完整的命令行或输出的哈希，不只比较结果。

## 5. Blender 通道

Blender 通道是唯一能启动 Blender 的角色：一个专用 Sonnet 子 agent（在主工作区，不在 worktree），夜间队列可由指挥官本人跑。P0 落地前令牌手工管理（见总览第一小时第 6 步）。协议在 [02 第 5 节](02-architecture-and-contracts.md#5-blender-令牌)。

1. 每次启动前 `python tools/poc/orchestration/blender_token.py acquire --holder <name> --purpose "<做什么>"`，结束后 `release`。`acquire` 的退出码不是 0 就停下，不许在没拿到令牌时启动 Blender；脚本里每一步都检查退出码（2026-10-23 一个通道 agent 的脚本在 `acquire` 报错后继续跑了 Blender）。
2. 队列顺序：白天只跑预览（Workbench 每次 6 到 10 秒；加 `--client-look` 走 Cycles，约 20 到 40 秒）和锚点渲染；夜里跑全量回归（约 5 到 6 分钟）、完整 build 加 `--part-masks`、高清渲染。
3. 启动后把 Blender 进程优先级调低，机器给决策人留着：PowerShell 一行 `Get-Process blender -ErrorAction SilentlyContinue | ForEach-Object { $_.PriorityClass = 'BelowNormal' }`。
4. P5 和 P5b 的慢 agent 会长时间占用通道：每天给全量回归留一个 30 分钟窗口，慢 agent 在窗口内不启动新渲染。
5. 两个 `preview` 不共用一个 `--out-root`；闭环里每个候选一个文件夹。要互相比较的一组渲染（锚点一的六个变体和基线、同一面板里的候选）用同一个引擎，在命令里显式写 `--engine`，不靠 `preview` 自己回落（同日：Workbench 时好时坏，同一组里混进了两种引擎）。
6. 失败的渲染（退出码非 0、超时 600 秒）记进日报，换 `--engine cycles` 重试一次，再失败就标记候选为 `blocked`。

## 6. 日报

每天固定时间一份，`output/poc/digests/day_NN.md`，模板在 [templates/daily-digest.md](templates/daily-digest.md)。五段：做了什么（包和状态）、数字（当天的分数、关口值）、卡在哪、明天做什么、需要决策人定什么（最多 3 个是/否问题）。关口日附并排图的路径。日报是决策人唯一需要读的东西。

## 7. 关口等待与停止规则

1. 到了关口，指挥官测量、写 `gates.json`、发日报，然后只暂停依赖该关口的包；不依赖的包继续，能提前做的后续包（例如关口前只写测试不合并的 P7）也继续。
2. 等待超过一个工作日：再发一次只含三行的提醒，不自己拍板。
3. 没有可做的包时干净停下：更新看板，写日报，结束会话；不空转耗限额。
4. 限额告急：先停新派活，让在跑的包收尾并回报；看板里标 `blocked: rate_limit`。
5. 闭环的停止规则（代码执行）：分数不再上升、满 4 轮或超过 10 分钟。

## 8. 绝不做的事

没有决策人明确同意，永远不做：`git push` 或任何形式的推送；合并进 `main`；任何 `pip install`、`npm install`、下载模型或素材；用真实密钥调用付费 API；把图片交给远程或云端 agent；改 `AGENTS.md`、`CLAUDE.md`、`.claude/settings.json`；手改 `release_status.json`、`FILE_MANIFEST.json`、`validation/`；替决策人把 `gates.json` 改成 `pass`、`fail` 或 `waived`；改 [01](01-scope-and-acceptance.md) 的阈值。提交信息不加任何署名行。任何 agent 都不按窗口标题、通配符或进程名批量结束进程（例如 `taskkill /FI "WINDOWTITLE eq *"`），只按 PID 结束自己启动的进程（2026-10-23 一个子 agent 误发过一条，事后核对未造成损害）。

## 9. 中断恢复

会话被压缩或重开后：

1. 读 `output/poc/board.json`、`gates.json`、最近一份日报。
2. `git worktree list` 和 `git branch --list 'poc/*'`，对照看板里的 `packages` 补齐状态。
3. `python tools/poc/orchestration/blender_token.py status`，过期的令牌 `release --force` 并记日报。
4. 对每个 `in_progress` 的包，用 Agent 工具的 SendMessage 继续原工人；联系不上就按 3.1 重新派，brief 里加一句"分支已存在，先 `git log` 看进度"。
5. 不重做已 `merged` 的包。

## 10. 风险与对策

| 风险 | 对策 |
|---|---|
| AI 面板同意自己 | 两个真值锚点不过不许用面板；三评委独立上下文；盲对、问两遍；边缘距离作为不依赖模型的独立一票 |
| 第 3 步部件库越做越大 | 封顶 8 个家族，只做 `missing-vocabulary.md` 里的；多一个家族要写明哪张图需要 |
| 两个 Blender 同时跑 | 令牌制；工人不跑 `run_tests.py` 和 smoke |
| 多人改同一文件 | 白名单加热点租约；链 A、链 B 串行 |
| 空测试 | 先红后绿由指挥官机械检查；变异检查由验证者做 |
| 拿到假设就去证实 | 验证者只拿 diff，任务是"把它弄坏" |
| 指挥官上下文爆掉 | 状态都在 `board.json`，第 9 节恢复 |
| 半夜卡在权限弹窗 | 决策人事先设好允许和拦截；遇到弹窗的命令记日报，不绕过 |
| 客户数据进 git | `tools/poc/tests` 只用合成夹具；真图结果只在 `output/poc/`；`check_docs.py` 每次合并都跑 |
| 第 4 天止损导致白干 | 除 P7-fit-camera 外，P7 在 G2 前只写测试，不合并 |
| 机器被占满 | 白天只跑预览；Blender 进程低优先级；限额告急先停派活 |
