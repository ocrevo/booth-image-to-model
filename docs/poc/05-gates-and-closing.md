# 05 关口与结案

本文给每个关口写出输入、测量步骤、拍板人和不通过的去向，给决策人写出 10 分钟校准的做法，最后是决策备忘的写法。阈值只在 [01 第 4 节](01-scope-and-acceptance.md#4-关口)，这里不重复数字。读者：决策人、指挥官。回到[总览](README.md)。

## 0. 共同规则

1. 关口日先测量，再写 `output/poc/gates.json`：`value`、`threshold`、`measured_at`、`evidence_path`；`status` 留 `pending`。一个关口有几个数时，`value` 写成一个 JSON 对象（`gates.py measure --value '{"C_total": 72, "owner_panel_agreement": 0.83}'`），不另加字段（gates.v1 不允许未列出的字段）。
2. 日报里给决策人三行：数字、证据路径、建议（继续或止损）。
3. 决策人把 `status` 改成 `pass` 或 `fail`，或在聊天里说一句，由指挥官用 `python tools/poc/orchestration/gates.py decide <G> pass|fail --by owner` 代填并在日报注明"决策人口头"；G1 由指挥官 `--by commander`。
4. 不通过时按 01 的"不通过怎么办"执行，并在日报里写改后的估算；指挥官不自己降阈值。

## 1. G1 尺子可信（第 2 天）

输入：P1、P2、P3 合并；锚点一文件夹 `<A1>` 里 `B1` 到 `B6` 各有预览和 `score.json`。`<A1>` 是 `output/poc/anchor1/pic_A`（用 P4 基线时）或 `output/poc/anchor1/pic_A_ceiling`（03 第 5 节第 1 条的退路，用 C）；2026-10-23 的试算留在 `pic_A_ceiling/`，正式第一次跑和之后的重跑写到新文件夹 `output/poc/anchor1/pic_A_ceiling_official/`，不覆盖试算（2026-10-23 补）。

1. （评委的 brief 只有三行：`tools/poc/prompts/judge.md`、请求文件、响应文件的完整路径，即 `<panel_dir>/` 加请求里的 `respond_to`。）（2026-10-24 补：`score.py` 必须带 `--fit output/poc/ceiling/pic_A/box_fit.json`，03 第 3.1 节第 3 条；每个变体用的预览固定为 B1、B5 的 `previews/preview_001`，B2、B4、B6 的 `previews/preview_003`，B3 的 `previews/preview_002`，B2、B4、B6 的 001 和 002 是早先的 Workbench 渲染，不用。）对六个变体各跑一次不带面板的 `score.py`（得 `blocked` 的 `score.json` 和评委要看的 `panel_view.png`），再跑面板（`panel.py prepare`、评委、`tally`），最后带 `--panel <tally.json> --candidate-id <变体>` 重跑 `score.py`，得六个 `score.json`。
2. `python tools/poc/score/rank_check.py --scores <A1> --expected <A1>/expected_order.json --out <A1>/rank_check.json`。
3. 看 `rank_check.json` 里的两个 Spearman 值（总分、边缘距离）和排反的对。
4. 拍板人：指挥官。两个值都达标才 `pass`。
5. 不通过：找排反的那一对，判断是指标还是面板的问题，改一处，重跑，重跑次数写进日报；最多重跑两次，仍不过就报决策人并暂停第 2 步。

## 2. G2 天花板，止损点一（第 4 天）

输入：P5 的 C（`output/poc/ceiling/pic_A` 到 `pic_C`）各有预览和面板打分；锚点一对 C 的重跑结果。

1. （2026-10-23 起 `score.py` 必须带这张图的 `--fit`：给 C 和 C′ 打分时用 C 的 spec 编译时用的那个拟合文件，相机必须和 spec 的一致，见 03 第 3.1 节第 3 条。）面板给三张图的 C 打分，每张图一个面板（`panel.py prepare` 只有一个 `--truth`，候选文件夹里只放这张图的候选）（顺序同 G1 第 1 步：先不带面板跑 `score.py`，再面板，再带 `--panel` 重跑），`score.py` 写 `score.json`；取 `pic_A` 的总分为 `value`。
2. 决策人校准：指挥官把 `pic_A` 的叠图和答案卷清单做成一张表（[templates/answer-sheet.example.json](templates/answer-sheet.example.json) 的 `calibration_form` 部分），决策人逐项填 `present`/`missing` 和 `ok`/`off`/`wrong`，约 10 分钟。
3. 一致率 = 决策人与面板多数答案相同的项数 / 比较的项数。比较的项 = 每个清单项的 `L1` 答案，加上决策人和面板多数都判 present 的项的 `L2` 答案；面板多数为 undecided 的项算不一致。和总分一起写进 `gates.json` 的 `value` 对象（见第 0 节第 1 条）。
4. 拍板人：决策人。
5. 不通过：C 不到 70 说明引擎差得远，指挥官给出第 3 步改后的工作量估算，等决策人决定继续或止损；一致率不到 80 % 说明面板规则有问题，改提示词或盲化方式，重新校准一次。

## 3. G3 引擎够用，止损点二（第 7 天）

输入：P7 系列全部合并并通过夜间全量回归；P5b 的 C′ 打分；P5 的 `missing-vocabulary.md`；`output/poc/ceiling2/evidence/<行号>/` 里每行的证据和 `verdict.json`。第 3 步做不完时 G3 推到第 8 天，不砍行（决策人 2026-10-23 决定）。

1. 同 G2 的面板给 C′ 打分；`value` 取 `pic_A` 的总分。
2. 对照 `missing-vocabulary.md` 逐行判"已可表达"或"仍不可"，算比例。判法（决策人 2026-10-23 决定）：
   - 分母是清单的全部 12 行，不因占位块或替代而减少。行号按清单的行序，第 1 行是墙板灯槽。
   - 一行"已可表达"，当且仅当它有一份通过核对的证据：`output/poc/ceiling2/evidence/<行号>/` 里有用设计路线（`design-compile` 加 `preview --engine cycles`）出的预览，这一行"差在哪"写的每一处在预览里看得见地消失，并且一个独立核对 agent 写的 `verdict.json` 为 `pass`。每行要看的可见判据写在对应任务卡的"完成的定义"里。
   - 证据怎么出：Blender 通道 agent 在 `evidence/<行号>/` 写一个只含这一行特征的最小 `design.json`（加必要的墙和地面），尺寸按原图上这一行的像素框目测，用这张图在 P5b 用的同一个 `box_fit.json` 和原图编译（`--fit`、`--image`）。P7 包的证据在合并前出，用该包分支的代码（看板 `worktree` 字段指向的 `boothctl.py`，输出写主工作区的 `output/`）；第 9 行（玻璃）由 P5b 出；拿令牌、出图、放令牌照 [04 第 5 节](04-execution-runbook.md#5-blender-通道)。指挥官把原图上这一行的像素框裁进同一文件夹的 `crops/`。
   - 核对 agent：全新上下文的子 agent，没参与建设（不是该包的工人、验证者、出证据的 Blender 通道 agent 或 P5b 的慢 agent），模型与建它的 agent 不同；只看清单这一行的文字、`crops/`、证据预览和任务卡里这一行的可见判据，不看代码、diff 和回报；逐条答 `yes` 或 `no` 并写一句理由，任何一条 `no` 就是 `fail`。它写 `verdict.json`：`{row, pictures, previews, criteria: [{text, answer, why}], verdict, checker_model, checked_at}`。`fail` 时退回工人，包不合并。
   - 不得用降级办法凑数：用平面线条冒充凹槽（`material.pattern` 的条纹）、另放一层薄片冒充图案、把地面涂亮冒充光斑、几个元素拼起来冒充一根连续构件、不透明的灰板冒充玻璃，一律 `fail`。
   - 占位块：第 5 行（桌上的小件电子产品）和第 8 行（盆栽）按决策人决定，做成尺寸和位置都对的占位块（`prop_block`）就算"已可表达"；证据只判占位块是否落在原图物件的位置上、大小相当，不判外形；在还原记录（`06-fidelity-ledger.md`，只在本地）里标为"替代"。
   - 比例 = `pass` 的行数 / 12，和 C′ 的总分一起写进 `gates.json` 的 `value` 对象（`{"C_prime_total": <分>, "expressible_ratio": <比例>}`），`evidence_path` 指向 `output/poc/ceiling2/evidence/`。
   - 指挥官把每行的结论同步到还原记录的"状态"列；`fail` 的行还原方式改为"暂缺"。
3. 拍板人：决策人。
4. 不通过：降目标到几何为主（L4 不计入关口），先跑第 4 步；外观包留到 POC 之后。

## 4. G4 闭环有效（第 9 天）

输入：P8c 的三个合成 case 的逐轮误差曲线；P8d 三张真实图的逐轮 `score.json` 和用时。

1. 合成：每个 case 画出中心误差和 IoU 随轮次的曲线（`selfplay.py` 自动写 `curve.json` 和 `curve.png`）；判断 3 个中至少 2 个逐轮改善。
2. 真实：每张图的终分 / 该图 C′ 的分数，三张取最小值为 `value`；每张用时取最大值。
3. 拍板人：指挥官测量，决策人看曲线。
4. 不通过：按"部分通过"结案；备忘里写清是机制不收敛（合成也不过）还是只在真实图上不够（合成过、真实不过）。

## 5. G5 盲测（第 10 天）

输入：`input/poc/pic_D`、`pic_E` 第 10 天才打开；答案卷由执行人当天起草，决策人确认后再跑。

1. 两张图各跑完整闭环，面板打分；`value` 取两张总分的中位数，并记录最低分。
2. 并排图：原图、闭环终稿的投影版渲染、中性版渲染，三张图并排；决策人看两组。
3. 拍板人：决策人。
4. 不通过：回第 3 步看缺什么家族，写进备忘。

## 6. 决策备忘（第 11 天）

模板在 [templates/decision-memo.md](templates/decision-memo.md)。备忘只有一页：结局（通过、部分通过、不通过）、五个关口的数字和证据路径、两个锚点的结果、三种结局对应的下一步、本轮没做的事。对外讲的规则：四层分和两个锚点一起报，不单报总分；说"估计尺寸"，不说"测量"或"CAD"。
