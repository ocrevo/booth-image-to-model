# 工作包总表

本文是 28 个工作包的总表、依赖边和两条租约链；每个包的任务卡在同一文件夹。id、天数和依赖以本表为准，卡片与本表不一致时改卡片。读者：指挥官。回到[总览](../README.md)。

## 1. 总表

默认：通道 `worker`（不碰 Blender），模型 `sonnet`，禁止改白名单之外的一切，另加 `booth-image-to-model/release_status.json`、`FILE_MANIFEST.json`、`validation/`、`components.py` 里已有的宏、`web/`。每张 `worker` 通道卡的验收命令都以 `python tools/check_docs.py` 和 `python tools/check_lf.py` 结尾（`blender` 和 `owner` 通道卡只写 `output/`，不需要）。

| id | 卡片 | 目标 | 通道 / 模型 | 天 | 依赖 | 工时 |
|---|---|---|---|---|---|---|
| P0 | [P0-orchestration.md](P0-orchestration.md) | 看板、关口、Blender 令牌、卡片检查器、日报生成器 | worker / opus | 1 | 无 | 4 |
| PT | [PT-truth-sheets.md](PT-truth-sheets.md) | 五张图的答案卷：清单、留出点、色块 | owner | 1 | 无 | 2 |
| P1 | [P1-metrics.md](P1-metrics.md) | `score.py`：边缘距离、色差、留出点投影、`score.json`、`sheet.png`、reader.v1 schema | worker | 1 到 2 | 无（答案卷格式以模板为准） | 8 |
| P1-edge | [P1-edge-region.md](P1-edge-region.md) | 边缘距离改用与候选无关的展台区域（G1 第一次重跑，事先登记的计划） | worker | 2 | P1、P8a | 3 |
| P2 | [P2-panel.md](P2-panel.md) | `panel.py`：盲化、请求文件、汇总、自洽率 | worker | 2 | P1 | 5 |
| P2b | [P2b-projected-view.md](P2b-projected-view.md) | 评委在 L4 的 5 项上看投影版叠图（2026-10-23 补） | worker | 6 | P1、P2；G1 的评分改动合并后派 | 3 |
| P3 | [P3-anchor-one.md](P3-anchor-one.md) | `perturb_spec.py` 六个变体、`rank_check.py` | worker + blender | 2 | P1、P4、PT | 5 |
| P4 | [P4-baseline.md](P4-baseline.md) | 今天的快速路线跑 pic_A 到 C，打分 | blender | 1 | 无（PT 须在 P4 打分前确认） | 6 |
| P5 | [P5-ceiling.md](P5-ceiling.md) | 慢 agent 用今天的积木建 C，`missing-vocabulary.md` | blender / opus | 3 到 4 | PT、P4、P1、P2 | 12 |
| P6 | [P6-anchor-two.md](P6-anchor-two.md) | `selfplay.py`：合成 case 的自博弈台架 | worker | 4 | P1 | 5 |
| P7-fit-camera | [P7-fit-camera.md](P7-fit-camera.md) | `design-compile --fit`：用拟合相机、尺寸、地面高度和原图 | worker | 3 | 无 | 5 |
| P7-fam-canopy | [P7-fam-canopy.md](P7-fam-canopy.md) | 家族 `canopy_frame`：各边带宽可设的空心顶框加更深的标识楣板（清单第 3 行）；链 A 的通用钩子和图案透传 | worker | 5 | P7-fit-camera、P5 | 6.5 |
| P7-fam-litpanel | [P7-fam-litpanel.md](P7-fam-litpanel.md) | 家族 `lit_panel`：板边凹槽灯带和有底凹龛（第 1、2 行） | worker | 5 到 6 | canopy | 6 |
| P7-fam-propblock | [P7-fam-propblock.md](P7-fam-propblock.md) | 家族 `prop_block`：尺寸和位置都对的占位块（第 5、8 行，替代） | worker | 6 | litpanel | 2.5 |
| P7-fam-counter | [P7-fam-counter.md](P7-fam-counter.md) | 家族 `shaped_counter`：平面圆头柜台，柜身带真凹槽（第 10 行） | worker | 6 到 7 | propblock | 4 |
| P7-fam-sweep | [P7-fam-sweep.md](P7-fam-sweep.md) | 家族 `sweep_member`：沿三维路径的连续构件，矩形或圆截面（第 6、7 行） | worker | 7 | counter | 7 |
| P7-mat-projected | [P7-mat-projected.md](P7-mat-projected.md) | 材质 `pattern: projected`（原图按相机投影）、图案按类型分发、`preview --material-set` | worker | 5 | P7-fit-camera、P5 | 7 |
| P7-render-dark | [P7-render-dark.md](P7-render-dark.md) | 渲染预设 `--look dark_hall`：发光部件照亮地面和墙（第 4 行） | worker | 6 | projected | 5 |
| P7-mat-pattern | [P7-mat-pattern.md](P7-mat-pattern.md) | 材质图案 `dots`（带渐变）和 `grain`（第 11、12 行） | worker | 7 | render-dark、canopy | 5.5 |
| P5b | [P5b-ceiling-prime.md](P5b-ceiling-prime.md) | 慢 agent 用新词汇建 C′；第 9 行（玻璃）的证据 | blender / opus | 7 到 8 | sweep、pattern（两条链的最后一个包） | 10 |
| P8a | [P8a-loop-skeleton.md](P8a-loop-skeleton.md) | 闭环骨架：`orchestrator.py`、`edits.py`、桩角色、停止规则 | worker | 5 到 6 | P1、P7-fit-camera | 8 |
| P8b | [P8b-roles.md](P8b-roles.md) | 三个角色的提示词和请求响应协议 | worker / opus 写提示词 | 7 | P8a、P2 | 4 |
| P8c | [P8c-anchor-two-run.md](P8c-anchor-two-run.md) | 合成自博弈完整跑 | blender / opus 指挥 | 8 | P6、P8a、P8b | 6 |
| P8-fix2 | [P8-fix2-loop.md](P8-fix2-loop.md) | 闭环修复二：读图坐标系核对、移动比同一部分、落地家具不上下、跳过的发现留记录、顶框改尺寸（锚点二第三次跑的诊断） | worker | 8 | P8a、P8b、P8c、P7-fam-canopy | 6 |
| P8-catalog | [P8-catalog.md](P8-catalog.md) | 家族目录补齐链 A 的五个新 kind 的说明和最小设计（缩略图由 Blender 通道补） | worker | 8 | sweep、P8b | 1.5 |
| P8-kinds | [P8-kinds.md](P8-kinds.md) | 闭环认识链 A 的新 kind：坐标系核对、落地家具、主体和改尺寸（P7-fam-counter 第 1 次验证发现） | worker | 8 | sweep、P8-fix2 | 4 |
| P8d | [P8d-real-pictures.md](P8d-real-pictures.md) | 三张真实图过闭环并打分 | blender | 8 到 9 | P8c、P5b | 6 |
| P9 | [P9-blind-test.md](P9-blind-test.md) | 两张盲测图 | blender + owner | 10 | P8d | 4 |

## 2. 依赖边

P0 → 全部（第 1 天 P0 落地前，令牌手工管理）；PT → P3、P5，且 PT 须在任何打分之前确认；P4 → P3、P5；P1 → P2、P3、P6、P8a；P2 → P5（G2）、P8b；P3 → G1；P5 → 所有 P7（`missing-vocabulary.md` 决定范围）；P7-fit-camera → 所有 P7-fam、所有 P7-mat、P7-render-dark、P5b、P8a；链 A：canopy → litpanel → propblock → counter → sweep；链 B：projected → render-dark → pattern；canopy → pattern（pattern 的证据经设计路线用图案，透传由 canopy 加）；sweep、pattern → P5b（pic_A 的 C′ 用到的部件都已合并，2026-10-24 起先做；pic_B、pic_C 仍等 sweep）；P6、P8a、P8b → P8c → P8d → P9；sweep → P8-catalog → P8d（P8d 用补齐后的家族目录，2026-10-24 补）；sweep → P8-kinds → P8d（闭环的 kind 表认识新 kind，2026-10-24 补；和 P8-catalog 不共用文件，可以并行）。第 3 步的八个包都依赖 P7-fit-camera 和 P5，并且 G2 通过前只写测试、不合并。关口：G1 第 2 天（P3），G2 第 4 天（P5），G3 第 7 天（P5b；第 3 步做不完时推到第 8 天，决策人 2026-10-23 决定），G4 第 9 天（P8d），G5 第 10 天（P9）。

## 3. 租约链

链 A（部件家族）共用 `design_compile.py`、`motifs.py`、`booth-design.v1.json`、`design_check.py`、`test_design_compile.py`、`test_design_check.py`、`references/component-recipes.md`，必须串行；每个包合并进 `poc/integration` 后下一个包才从 `poc/integration` 开分支。`design_compile.py` 只有链 A 的第一个包（P7-fam-canopy）改：它加头顶件和三维路径的分发钩子、颜色角色和参数检查的查表、设计到 spec 的图案透传，之后的链 A 包只改其余几个文件；例外是 P7-fam-propblock（2026-10-23 指挥官裁定，卡片交付物 8、9）：交给 PARAM 检查的 form 多带墙厚和入口边，`place_floor` 不夹 `motifs` 声明的自己按转后外廓摆放的 kind。

链 B（外观）共用 `material_patterns.py`、`validate_spec.py`、`blender_build.py`、`boothctl.py`、`booth-spec-v2.schema.json`、`test_material_patterns.py`、`test_quality_gate.py`，同样串行；链 B 的第一个包（P7-mat-projected）加按类型分发图案，`boothctl.py` 用于 `preview` 透传 `--material-set` 和 `--look`。

两条链之间并行；唯一的跨链依赖是 P7-mat-pattern 要等 P7-fam-canopy 合并。P7-fit-camera 已合并，它持有过的 `design_compile.py` 交给链 A，`boothctl.py` 交给链 B；指挥官按此改 `board.json` 的 `leases`。

每个包的顺序：G2 通过前只写 `tests_first` 的测试（提交到自己的分支），不实现、不合并；G2 通过后实现 → 验证者（[04 第 4 节](../04-execution-runbook.md#4-验证协议)）→ Blender 通道用该包分支的代码出证据预览 → 独立核对 agent 判 → 合并进 `poc/integration` → 指挥官更新还原记录（`06-fidelity-ledger.md`，只在本地） → 下一个包开分支。证据的出法和判法在 [05 第 3 节第 2 步](../05-gates-and-closing.md#3-g3-引擎够用止损点二第-7-天)；核对不通过就退回工人，不合并。

## 4. 怎么读一张卡

卡顶是前言（字段见 [02 第 4.7 节](../02-architecture-and-contracts.md#47-任务卡前言每张-packagespmd-顶部)），正文依次是：目标、输入、交付物、允许改的文件、禁止改的文件、先写的测试、验收命令、完成的定义、验证者清单。工人只做卡上写的；卡上没写的决定记在回报的 `notes` 里。
