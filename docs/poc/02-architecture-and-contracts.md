# 02 架构与契约

本文定义 POC 要证明的闭环、每个角色能看到什么、`output/poc/` 的文件布局、代码与模型之间的全部 JSON 契约，以及 Blender 令牌。读者：架构师、写工作包的工人、验证者。回到[总览](README.md)。

## 1. 闭环

POC 证明的不是"模型更会看图"，而是一个流程：读图一次，渲染出来和原图比，差在哪里改哪里，改完再比，直到分数不再上升。模型只在三个固定位置被调用，输入是代码准备好的图和表格，输出必须是封闭选项的 JSON，其余全由代码决定。

| 步 | 谁 | 输入 | 输出 | 实现 |
|---|---|---|---|---|
| 1 读图 | 模型 | 原图、部件家族目录（每个家族一张引擎渲染的缩略图加参数说明）、拟合结果 `box_fit.json` | `reader.v1`（见第 4 节） | P8b 的提示词，P8a 的请求文件 |
| 2 编译 | 代码 | `reader.v1` 里的 `design` 加 `box_fit.json` | `spec.json`；第 1 轮编译前先用读图器的 `evidence` 核对坐标系（第 4.1 节），结果写 `round_1/frame_check.json` | `boothctl.py design-compile --fit`（P7-fit-camera） |
| 3 渲染 | 代码 | `spec.json` | `preview_NNN/preview_render.png` 等 | `boothctl.py preview`，Blender 通道 |
| 4 对比 | 代码 | 原图、渲染、答案卷、这张图的拟合文件（`score.py --fit`，03 第 3.1 节第 3 条） | `score.json`、`sheet.png`、`panel_view.png`；逐件裁片对由闭环代码裁出 | `tools/poc/score/score.py`（P1）；裁片在 `tools/poc/loop/`（P8a） |
| 5 批评 | 模型 | 逐件裁片（原图裁片、渲染裁片、渲染边缘叠在原图上的叠加图，以及裁片在原图上的位置和缩放，见第 2 节），看不到读图器写过什么 | `critic.v1` | P8b |
| 6 修改 | 代码 | `critic.v1` 加修改目录 | 最多 3 个候选 `design.json` 加原样 1 个 | `tools/poc/loop/edits.py`（P8a） |
| 7 裁判 | 代码优先，模型破平局 | 候选们的 `score.json`；平局时两张渲染盲比 | 胜者 id | 代码指标先判：边缘距离（`aux.edge_distance_diag`）小的胜，两者相差不到 0.2 % 对角线时交给 `judge.v1` 盲投（03 第 4 节第 6 条）。多个候选时：边缘距离最小者为领先，和它相差不到 0.002 的都算平局；原样候选在平局里，就让领先者和原样候选比，裁判说不清保留原样候选；原样候选不在平局里，就让平局里边缘距离最小的两个比，裁判说不清取边缘距离小的；没有边缘距离的候选不能胜出；闭环里没有面板，`total` 只含代码分，不作为主判据。（2026-10-23 补）`round.json` 的 `judge_outcome` 记两遍的结果：`agree`（两遍指同一个候选）、`both_same`（两遍都说分不出）、`split`（一遍指候选、一遍说分不出）、`conflict`（两遍指不同的候选）；`decided_by` 的取值和选胜者的规则都不变。（同日补，来自 P8-fix2 的第 2 次验证）没有问裁判时记 null；某一遍没有有效回答，这一遍按'分不出'算；和历史最好比较的那次裁判（`judge_best_p1`、`judge_best_p2`）不记进 `judge_outcome`。 |
| 8 停止 | 代码 | 轮次、用时、分数走势 | 继续或停 | 分数不再上升、满 4 轮或超过 10 分钟就停 |

裁片对的来源：预览渲染没有逐件掩码（`--part-masks` 只在完整 build 里有），所以逐件裁片按 spec 里每个部件的网格顶点（文字按字高和字数估一个矩形）经 `camera_math.project` 投影得到的像素框，四周各加 max(6 px, 框尺寸的 25 %) 的边距并裁到画面内，裁出原图、渲染和叠加图三张，放在 `critic_inputs/el_<element_id>/`（2026-10-23 改，与第 2 节和 P8-fix 的实现一致）。完整 build 的掩码只在 G4、G5 的最终评分里用。

## 2. 角色的信息视野

| 角色 | 看得到 | 看不到 | 产出 |
|---|---|---|---|
| 读图器 | 原图、家族目录、`box_fit.json` 的尺寸 | 任何以前轮次的文字、评分 | `reader.v1` |
| 批评器 | 逐件裁片对（原图裁片、渲染裁片，以及把渲染轮廓画在原图裁片上的叠加图）、每张裁片在全图里的位置、部件 id 和家族名 | 读图器的 JSON、分数、原图全图 | `critic.v1` |
| 修改器（代码） | `critic.v1`、当前 `design.json` | 无 | 候选 `design.json` 们 |
| 裁判（模型，仅破平局） | 两张渲染加原图，随机 A/B 顺序，问两遍 | 候选来源、分数、任何文字 | `judge.v1` |
| 评委面板（评分） | 答案卷清单、盲化后的叠图 | 建模过程的任何文字、其他评委的答案 | `judge.v1`（评分模式） |
| 慢 agent（P5、P5b） | 原图、全部工具、不限轮数 | 评委的提示词、评分脚本的内部阈值 | spec 或 `design.json` 加 `missing-vocabulary.md` |
| 工人 | 任务卡、白名单内的文件、输入副本 | 图片、`output/`、其他包的分支 | 代码、测试、`worker.v1` |
| 验证者 | 任务卡、diff、基线提交 | 作者的解释 | 验证结论 |
| 证据核对（第 3 步） | 清单的一行、原图上这一行的裁图、证据预览、任务卡里这一行的可见判据 | 代码、diff、工人的回报、建模过程的文字 | `verdict.json`（05 第 3 节） |

## 3. `output/poc/` 布局

```text
output/poc/
  board.json                看板（契约 board.v1）
  gates.json                关口状态（契约 gates.v1）
  .blender.token            Blender 令牌（第 5 节）
  truth/<pic>/              答案卷：inventory.json、holdout_px.json、swatches.json
  baseline/<pic>/           P4：points.json、box_fit.json、spec_work/spec.json、previews/、score.json
  ceiling/<pic>/            P5：build_spec.py、spec.json、previews/、score.json
  ceiling2/<pic>/           P5b：design.json、v00N/、score.json
  ceiling2/evidence/<行号>/  第 3 步每行的证据：design.json、v001/、previews/、crops/、verdict.json（05 第 3 节）
  anchor1/<pic>/B1..B6/     P3：六个变体的 spec、预览、score.json，加 rank_check.json
  anchor2/<case>/           P6、P8c：picture.png、truth、每轮的候选和误差曲线
  loop/<pic>/round_N/       P8：reader.request.json、reader.response.json、frame_check.json（第 1 轮）、candidates/、critic.*、judge.*、
                            fixer_skipped.json、round.json（含 judge_outcome：agree、both_same、split、conflict）、score.json
  digests/day_NN.md         日报
  reports/<package>.json    工人回报（契约 worker.v1）
  memo/                     决策备忘
```

`<pic>` 取 `pic_A` 到 `pic_E`。所有路径相对仓库根；`output/` 不入库。

## 4. 契约

每个契约一个 `schema_version` 字符串；必填字段缺失或出现未列出的字段即无效，不猜；标为可选的字段可以是 null。枚举值区分大小写，全小写。

### 4.1 reader.v1（读图器输出）

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema_version` | string | `"reader.v1"` |
| `picture_id` | string | `pic_A` 等 |
| `design` | object | 一个完整的 `booth-design.v1` 对象（`schemas/booth-design.v1.json`）；`form.footprint_m` 由代码从 `box_fit.json` 的 `dims_m`（W、D）覆盖，读图器写的值不生效；`box_fit.json` 没有开放边，`form.open_sides` 用读图器写的值。（2026-10-23 补，来自锚点二第三次跑的诊断：一份读图答案把整个展台转了 90°，转错的墙挡住了三件家具）读图答案通过校验、套上拟合占地之后、第一次编译之前，代码用读图器自己的 `evidence.bbox_px` 核对坐标系：把设计整体转 0、90、180、270 度（`side` 换名，`along` 按新边的方向需要时改成 1 − along，`centre_m` 和 `yaw_deg` 跟着转，`open_sides` 和按边写的参数的键一起换名），每种转法编译一次，编译不过的跳过；用拟合相机投影各元素的主体（第 4.3 节的主体），和 `evidence.bbox_px` 求平均 IoU。只有原样低于 0.3、最好的转法不低于 0.5、并且比原样高出至少 0.3 时，才换成那种转法，否则保持原样；有 `evidence` 的元素不到一半时保持原样，记 `frame unverified`。每种转法的 IoU 和决定写进 `round_1/frame_check.json` 和 `loop.log`。这是核对读图结果，不是修改器的编辑：修改器仍然不碰 `form`。三个门槛是看着锚点二前三次的 9 份读图答案定的，事先登记，看到重跑结果后不改。（2026-10-23 同日补，来自 P8-fix2 的第 1 次验证）原样编译不过、某种转法编译得过时，原样的 IoU 按 0 算，照同样的门槛决定（否则闭环在第一次渲染就停下）；转的时候，没写 `yaw_deg` 的件按编译器的缺省朝向先写出明确的 `yaw_deg` 再转，顶框标识楣板没写的 `corner`、`along_m` 先写成编译器的缺省值再转，保证转出来的设计和'整个展台绕占地中心转'一致（几何一致为准：入口在左右两侧时，柜台、展台、储物柜、隔断这类没写 `yaw_deg` 的件，编译器按宽沿 x 摆、朝向跟入口，没法用一个 `yaw_deg` 同时保住形状和朝向，这时保住形状，朝向可以不同，只影响件上文字落在哪一面；墙板 `lit_panel` 这类按缺省朝向整体转的件，缺省 `yaw_deg` 就是入口的朝向，形状和朝向都保住；2026-10-24 补：转之前先把每一件改写成编译器实际建出的摆法——例如按 `along` 摆在中线上的件，编译器不按写的 `yaw_deg` 定形状，改写成 `centre_m` 时朝向要按编译器实际的形状写——再整体转；2026-10-24 再补，来自 P8-fix2 的第 4 次验证：按写法逐类改写总有漏掉的摆法（什么都没写的居中件、被编译器往里挪过的件），所以落地件一律从原样设计的编译结果取：每件主体的实际中心写成 `centre_m`，实际的长边方向（会按入口转的件取实际朝向）写成 `yaw_deg`，再整体转；转完编译后自检：原样编译出的每件主体框绕占地中心转过去，凡是转过去仍在占地以内的件，和转后编译出的框相差都不得超过 0.01 m，否则这个转法记 `inexact`（列出不符的件）、不参与选择，绝不采用一个不能还原几何的转法；（同日三补，来自 P8-fix2 的第 5 次验证）自检比较的件：所有落地件、`ring`、`canopy_frame`；挂墙件只在占地正好是正方形时比（W 和 D 不同时它们的 along 是边长的比例，换到另一条边位置本来就变，属于上面说的'挤变形'）；`header_band` 和 `spotlights` 不比（编译器在墙角截断门头、灯挂在门头上）。长边方向：不随入口转的件写实际的长边方向（0 或 90 度这一类），不写正好 45 度这种编译器分界上的角。失败一律从严：取摆法失败、自检本身出错时，这个转法当作 `inexact`、不采用，并写进 `loop.log` 和 `frame_check.json`；原样编译不过时没有可对照的编译结果，转法记 `unchecked`，按门槛照常决定（原样 IoU 按 0））；占地 W 和 D 不同时，转 90 或 270 度多半仍能编译，只是布局被挤变形，靠门槛把关；`loop.log` 里每条记录只占一行（编译报错的多行文字压成一行，完整原文留在 json 文件里）。（2026-10-24 补，来自 P7-fam-counter 的第 1 次验证：闭环的 kind 表不认识链 A 后加的 kind，转法自检会把它们一律判 `inexact`）按自己的转角整体摆放的件（`motifs.SELF_PLACING_KINDS`：`lit_panel`、`prop_block`、`shaped_counter`）都和 `lit_panel` 一样处理：缺省 `yaw_deg` 是入口的朝向，形状和朝向一起转，摆法从编译结果取实际中心和实际转角；`prop_block` 和 `shaped_counter` 是落地件，照落地件参加自检。`sweep_member` 转的时候 `params.path_m` 的每个点（平面坐标以占地中心为原点）跟着绕占地中心转，`repeat_axis` 在转 90 或 270 度时 x 和 y 互换，自检比较的件加上 `sweep_member`（主体是整根构件，`count` 复制出的各根都算）。 |
| `evidence` | object | `{element_id: {bbox_px: [x0, y0, x1, y1], confidence: high 或 medium 或 low}}`，每个元素在原图上的像素框 |
| `unexpressible` | array | `[{what, nearest_kind（family 名或 null）, why}]`，图里有但家族目录造不出的东西 |

`booth-design.v1` 是封闭 schema（`additionalProperties: false`），所以证据和不可表达项放在包装对象里，不放进 `design`。

### 4.2 critic.v1（批评器输出）

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema_version` | string | `"critic.v1"` |
| `round` | integer | 轮次，从 1 起 |
| `candidate_id` | string | 被批评的候选 |
| `findings` | array | 每条：`id`、`element_id`（或 null，表示整体）、`type`（`missing`、`extra`、`misplaced`、`wrong_size`、`wrong_colour`、`wrong_kind`、`wrong_count`、`camera_off`）、`severity`（`high`、`medium`、`low`）、`direction`（`left`、`right`、`up`、`down`、`forward`、`back`、`bigger`、`smaller`、`darker`、`lighter` 或 null；`left`、`right`、`up`、`down` 指画面里的方向，`forward`、`back` 指朝向或远离相机，代码用拟合相机把它们换算成展台坐标）、`bbox_px`（或 null；`misplaced` 和 `wrong_size` 时应给出这个元素在原图上实际所在的框，用全图像素坐标，代码拿它和渲染里该元素的框比较，经相机换算出移动或缩放的量；给不出时为 null，代码用小步）。方向的意思：`bigger`、`smaller` 指渲染里的这个元素应该变大还是变小（原图里的比渲染里的大就是 `bigger`），`darker`、`lighter` 指渲染应该变暗还是变亮，`left` 等指渲染里的元素应该往哪边移 |
| `verdict` | string | `accept`、`fix`、`give_up` |

### 4.3 fixer.v1（修改器的编辑列表，代码生成）

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema_version` | string | `"fixer.v1"` |
| `edits` | array | 移动量优先由批评器的 `bbox_px` 和渲染框的像素差经相机换算（深度 Z、像素焦距 f，`d = px × Z / f`），没有 `bbox_px` 时用 0.05 m 和 0.1 m 两档小步（严重性 high 取 0.1 m，medium 和 low 取 0.05 m）；上下移动从元素当前的实际高度起算。比较前先把渲染框裁到画面 [0, W]×[0, H]，因为批评器只能框画面里看得见的部分；只比两边都没被画面截断的边，四边都被截断的方向不按像素换算，用小步。像素差带正负：和批评器给的方向相反时，不按像素换算，用小步。单次移动上限 0.5 m、下限 0.01 m；上下移动另外除以相机上向量的竖直分量（不小于 0.2）。改尺寸时，元素没写 `size_m` 就从编译出的范围取，但只量编译器的 `size_m` 实际管的那部分（例如盆栽的 `size_m[2]` 是盆高，量盆体，不量植物）；编辑后重新编译，量出来的变化方向必须和批评器说的一致，否则这条编辑作废并记日志。（2026-10-23 补充裁定，来自 P8-fix 的验证）按像素比例改尺寸时，比的两个框必须是同一部分：渲染这边只取 `size_m` 管的那几个组件的框（例如环形吊顶只取环带，不含落地立柱），并且只对框的宽或高确实由这一维决定的尺寸用比例（环带的带宽不决定外框宽度，所以带宽用小步）；某一维的比例和批评器的方向相反或不可用时，这一维用小步，不是不变。同一候选里同一元素既改数量又改尺寸时，按新设计里的单个件量尺寸。`delta_m` 记实际的移动量（米，上下就是实际高度的变化），不是字段值的变化。（2026-10-23 再补，来自 P8-fix 的第 2 次验证）移动也比同一部分：渲染这边的框取元素的'主体'组件（环形吊顶只取环带，不含落地立柱；任何元素都不含文字和立柱；盆栽移动取整盆，改尺寸只量盆，见下文第四次补充）。像素比例只用于框的宽或高确实由它决定的那一维：宽度比只改 `size_m[0]`，进深 `size_m[1]` 不由框宽决定，按小步改；比例和方向一致就照用，不设'接近 1 就当看不出'的死区；比例正好是 1 或与方向相反时按小步。同一轮里同一元素还有数量的发现时，宽度不用像素比例（框宽同时随件数变），按小步。编辑后重新编译核对方向时，变化小于 5 mm 的维视为没变，不算方向相反。沿墙放的元素（墙、电视、LED 墙、画面板、造型墙）往前往后只能沿墙平移，`delta_m` 记实际做的那部分；上下移动只对编译器读 `z_m` 的元素做（电视、LED 墙、画面板、造型墙读，壳体的 `wall` 不读），编译器不读的跳过并写明原因，不提出空候选（同日更正：原先写成整类墙都跳过，与 `design_compile.place_wall` 不符）。批评器的 `bbox_px`：看得出元素实际在哪时给，看不准时写 null，不许编一个框。（2026-10-23 第三次补，来自 P8-fix 的第 3 次验证）每个几何编辑都在重新编译之后实测，以实测为准，不靠预测：元素主体框的变化小于 5 mm 的编辑作废（编译器不读这个字段，例如壳体 `wall` 的水平移动），元素在编译后消失的编辑作废（例如挂墙件被抬过墙顶或门头底），都记进日志、不占候选位；`delta_m` 写编译后实测的主体框变化（水平取中心，上下取底面），不写预测值。主体框：环形吊顶取环带，门头取带体，其余元素不含立柱和文字；盆栽改尺寸只量盆，移动量整盆（批评器框的是整盆）。批评器框在原图之外的 `bbox_px` 当作没给（写 null 并记日志）；比例'正好是 1'按 1e-9 的容差判。批评器提示词要求只框元素的主体，不框立柱、桌腿和文字。（2026-10-23 第四次补，来自 P8-fix 的第 4 次验证）实测按单个编辑做：每个几何编辑单独套在胜者的设计上编译一次，量它自己的效果，判'没效果'或'元素消失'、算 `delta_m` 都按这一次；留下来的编辑再一起编成候选。改数量的效果看编译出的件数（元素建出的单元个数）是否正好按要求变（多变、少变、变反都作废），不看主体框（筒灯这类在固定跨度里排开的元素，件数变了框不变）。`fixer.json` 里记的就是这些逐个实测的值。（2026-10-23 第五次补，来自 P8-fix 的第 5 次验证：同一元素上的几个编辑一起量，会让壳体墙的空移动跟着改尺寸留下、记的还是预测值）（2026-10-23 第六次补，来自锚点二第三次跑的诊断：修改器拿整张桌子去比批评器只框了桌面的框，把桌子往后挪了 0.49 m、往上抬了 0.33 m；顶框的改尺寸被以不实的理由跳过）带立柱或底座的家具（圆桌等）移动时，主体取桌面组件（组件 id 以 `_top` 结尾），和批评器提示词一致；在移动方向那一轴上，批评器框和渲染主体框的长度比不在 [0.7, 1.43] 以内时，换另一种主体再比（桌子换成整件含桌腿，盆栽在整盆和只取盆之间换），都比不上就不移动，记'两个框大小不一致：这是尺寸问题，不是位置问题'（2026-10-24 补：只有桌子和盆栽有第二种主体；其余 kind 比不上就直接按尺寸问题跳过，不退回到含立柱和文字的整件）；只有一条边可用、算不出长度比时按原规则。编译器放在地面上的家具（`round_table`、`counter`、`planter`、`box`、`display_plinth`、`storage_block`、`seating_group`、`chair`），设计里没写 `z_m` 并且编译后底面在地台面上时，不接受上下移动，跳过并写'落地家具不上下移'；挂墙件和写了 `z_m` 的元素照旧。顶框 `canopy_frame` 可以改尺寸：宽按框体（不含立柱和文字）的宽度比改，进深仍按小步，结果截到占地以内（顶框不得超出占地）。（2026-10-24 补）墙板 `lit_panel` 改尺寸：宽按面板主体框的宽度比改（任意转角下都按面板自己的宽、厚、高量），厚和高按小步，宽截到编译器的全部限制（转后放得进占地，并且不超过占地较长的一边）并向下取整；`fixer_skipped.json` 列出这一轮所有没有变成编辑的发现（颜色发现也在内），每条带理由。（2026-10-24 补，链 A 后加的 kind）`shaped_counter` 和 `prop_block` 算编译器放在地面上的家具（条件同上：没写 `z_m`、编译后底面在地台面上），不接受上下移动；它们的主体是整件（柜台取芯、片和台面，占位块取块本身），改尺寸时没写 `size_m` 就从编译出的范围取，任意转角下都按件自己的宽、深、高量（同 `lit_panel`），宽按主体框的宽度比改，深和高按小步，结果截到编译器的限制以内（放得进占地和封闭墙围出的房间，不超过限高），截不进去就作废并记理由。`sweep_member` 的水平移动把 `path_m` 的所有点一起平移；它有点落在地面上（z 为 0，即有落地腿）时不接受上下移动，跳过并写明；它不改尺寸，跳过并写"路径定义的构件没有改尺寸规则"。每轮没有变成编辑的几何发现（跳过的、作废的）连同理由写进 `round_N/fixer_skipped.json`，`loop.log` 里每条一行 `skipped (finding fN): 理由`，不管最后有没有候选带着它。每条：`op`（`move`、`resize`、`recolour`、`add`、`remove`、`replace_kind`、`set_count`、`set_text`）、`element_id`、`kind`（或 null）、`delta_m`（[dx, dy, dz] 或 null）、`size_m`（或 null）、`color_hex`（或 null）、`side`（或 null）、`along`（或 null）、`finding_id` |

代码只应用保持 `design.json` 通过 schema 校验的编辑；无效的编辑记录进日志并跳过。每轮最多 3 个候选，每个候选是一组编辑；原样不动的候选永远在列。

### 4.4 judge.v1（评委和裁判输出）

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema_version` | string | `"judge.v1"` |
| `pair_id` | string | 盲化后的对 id；评分模式下是被评候选的盲化 id，同一候选的所有请求和两遍共用 |
| `judge_id` | string | `j1`、`j2`、`j3` |
| `pass` | integer | 1 或 2（同一对问两遍） |
| `better` | string | `A`、`B`、`same`；评分模式下填 `same` |
| `scores` | object | `{L2: 0..20, L3: 0..20, L4: 0..10}`，评分模式填；破平局模式为 null |
| `items` | object | 评分模式：键 `L1:<清单 id>` 取 `present` 或 `missing`；键 `L2:<清单 id>` 取 `ok`、`off` 或 `wrong`，只对判为 `present` 的项给。评委看不到 spec 的部件 id，逐件判断的“件”就是答案卷清单项，两种判断用前缀分开，不会撞键 |
| `extra_parts` | integer 或 null | 评分模式：模型里多出的、原图没有的部件数（>= 0），供 L1 扣分；破平局模式为 null。汇总取有效评委的中位数，遇到小数向上取整 |
| `confidence` | string | `low`、`medium`、`high` |

### 4.5 score.json

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema_version` | string | `"score.v1"` |
| `picture_id`、`candidate_id`、`spec_sha256` | string | 候选的身份 |
| `levels` | object | `L1: {panel}`、`L2: {code, panel}`、`L3: {panel}`、`L4: {code, panel}`，分值上限见 [03](03-scoring-spec.md) |
| `total` | number | 0 到 100 |
| `aux` | object | `edge_distance_diag`（对角线的比例）、`edge_distance_px`、`holdout_median_diag`、`colour_de_mean`、`booth_pixel_fraction`、`second_view_ok`（bool 或 null） |
| `status` | string | `measured`、`blocked` |
| `reason` | string 或 null | `blocked` 的原因 |

`candidate_id` 由 `score.py --candidate-id` 给出（预览文件夹总叫 `preview_NNN`，不能当身份）。`score.py` 在 `score.json` 同一文件夹另写 `score_detail.json`（逐点、逐块、逐项的明细）、`sheet.png`（给决策人和指挥官，见 [03 第 7 节](03-scoring-spec.md#7-输出格式)）和 `panel_view.png`（给评委：原图、渲染、叠加三格，没有任何文字、分数或标记）。

### 4.6 worker.v1（工人回报）

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema_version` | string | `"worker.v1"` |
| `package` | string | 工作包 id |
| `branch`、`commit` | string | 分支名、最终提交 |
| `status` | string | `done`、`blocked`、`failed` |
| `files_changed` | array | 相对仓库根的路径 |
| `tests_added` | array | 测试名 |
| `red_commit`、`green_commit` | string | 先红后绿的两个提交 |
| `mutation` | object 或 null | 可选：工人交时为 null，验证者填 `{file, line_hint, test_failed}` |
| `acceptance` | array | `[{cmd, exit_code, seconds}]` |
| `notes` | string | 一段话 |

### 4.7 任务卡前言（每张 `packages/P*.md` 顶部）

字段：`id`、`title`、`goal`、`depends_on[]`、`model`（`sonnet`、`opus`）、`lane`（`worker`、`blender`、`owner`）、`allowed_files[]`、`forbidden_files[]`、`inputs[]`、`deliverables[]`、`tests_first[]`、`acceptance_cmds[]`、`effort_h`、`day`、`stop_rule`。模板在 [templates/task-card.md](templates/task-card.md)。

### 4.8 board.v1（`output/poc/board.json`）

| 字段 | 说明 |
|---|---|
| `schema_version` | `"board.v1"` |
| `updated` | ISO 时间 |
| `day` | 第几天 |
| `packages` | `{id: {status, owner, branch, worktree, verifier, started, finished, report_path}}`，`status` 取 `todo`、`leased`、`in_progress`、`verify`、`merged`、`blocked`、`dropped` |
| `leases` | `{file: package}`，热点文件当前租给谁 |
| `blender_token` | `{holder, since, purpose}`，与令牌文件一致 |
| `packages.<id>.note` | 可选，一句话 |
| `packages.<id>.blocked_from` | 可选，进入 `blocked` 前的状态；`blocked` 只能回到它、`todo` 或 `dropped` |

### 4.9 gates.v1（`output/poc/gates.json`）

`{schema_version: gates.v1, G1: {...}, ..., G5: {...}}`，每个关口：`status`（`pending`、`pass`、`fail`、`waived`）、`value`、`threshold`、`measured_at`、`evidence_path`、`memo_path`，另有可选的 `decided_by`（`owner` 或 `commander`）和 `decided_at`，由 `decide` 写。`status` 用 `python tools/poc/orchestration/gates.py decide <G> pass|fail|waived --by owner|commander` 写：G1 允许 `--by commander`；G2 到 G5 只有决策人说了之后由指挥官以 `--by owner` 写并在日报注明"决策人口头"，决策人也可以直接改文件；`waived` 只能 `--by owner`，并在 `memo_path` 里说明。G4 的例外（决策人 2026-10-23 决定）：由决策人授权的独立 agent 判定：指挥官先测量并写 `value`，再开三个全新上下文的 Opus 子 agent，它们没参与建设，只看 01 的 G4 行、05 第 4 节、`gates.json` 的 G4 条目、`output/poc/anchor2/` 的曲线和 `summary.md`、P8d 的逐轮分数和用时，各自写出 `pass` 或 `fail` 和理由；多数为准，三份结论存进 `output/poc/memo/G4_independent.json` 作为 `memo_path`，指挥官按多数以 `--by owner` 写入，日报注明"决策人授权独立 agent 判定"。决策人看曲线后仍可推翻。

### 4.10 tally.v1（面板汇总）

`panel.py tally`（P2）写，`score.py --panel`（P1）读。

| 字段 | 说明 |
|---|---|
| `schema_version` | `"tally.v1"` |
| `candidates` | `{candidate_id: {levels: {L1, L2_panel, L3, L4_panel}, items: {"L1:<清单 id>": present、missing 或 undecided, "L2:<清单 id>": ok、off、wrong 或 undecided}, extra_parts: 整数或 null}}`；分值超出 03 的上限、缺字段或多字段都无效 |
| `judges` | `{judge_id: {self_consistency: 0..1}}` |
| `pairwise_agreement` | `{"j1-j2": 0..1, "j1-j3": 0..1, "j2-j3": 0..1}` |

逐项多数和四个面板分只有一套实现：`tools/poc/score/` 里公开的聚合函数（P1），`panel.py tally`（P2）调用它，不另写。同一评委两遍不一致的项记 `undecided`；缺一遍的评委整体 `undecided`。

## 5. Blender 令牌

同一时刻只有一个进程能启动 Blender（内存和时间都只够一个）。仓库里没有全局锁：`blender_build.py` 的 `.build.lock` 只锁一个输出文件夹，`verify` 不加锁，`preview` 的编号在 Blender 启动前就选好，所以两个同时的预览会撞车。令牌是 POC 自己的约定：

1. 令牌文件 `output/poc/.blender.token`，内容 `{holder, since, purpose, pid}`。
2. 启动任何 `preview`、`build`、`verify`、`synth_render.py`、`run_tests.py --require-native` 之前，先运行 `python tools/poc/orchestration/blender_token.py acquire --holder <name> --purpose <text>`；没拿到就等，不并行。
3. 结束后 `release`。持有超过 30 分钟且进程已不在，`status` 命令会标记为过期，指挥官可以 `release --force` 并写进日报。`pid` 默认是调用 `acquire` 的父进程，一条命令一个 shell 时它很快就不在了，所以令牌只围着一次 Blender 启动拿和放（`acquire --wait <秒>` 拿到后立刻跑那一次 `preview` 或 `build`，跑完立刻 `release`），不跨着看图、写 JSON 等步骤持有。
4. 工人永远不持令牌：工人只跑不碰 Blender 的测试。
5. 同一个 `--out-root` 不并行跑两个 `preview`；闭环里每个候选用自己的 `--out-root`。

## 6. 模型角色作为请求与响应文件

代码和模型之间没有函数调用，只有文件：

1. 代码把请求写到 `output/poc/loop/<pic>/round_N/<role>.request.json`，把该角色允许看到的图放到同级的 `<role>_inputs/`。
2. 指挥官用 Agent 工具开一个全新上下文的子 agent，brief 只有三行：角色提示词文件（`tools/poc/prompts/<role>.md`）、请求文件路径、响应文件路径。
3. 子 agent 写 `<role>.response.json`。为了隔离，指挥官把请求复制到闭环之外的隔离文件夹让子 agent 作答，再由指挥官自己的进程把响应复制回闭环（子 agent 不往闭环文件夹写东西；2026-10-23 补：让子 agent 去搬，会被自动模式的权限检查当作外泄拦下）。代码按契约校验；不合法的响应按"无法判断"处理，记录后继续，不重试提示词。
4. 同一轮里三个角色各自全新上下文，互不传递文字。破平局的两遍是两个独立请求（`judge_p1.request.json`、`judge_p2.request.json`，各自的 `judge_p1_inputs/`、`judge_p2_inputs/`，第二遍 A/B 互换），各由一个全新上下文的子 agent 回答一个 `judge.v1` 对象；A/B 到候选的对照表不放在评委看得到的文件夹里。
5. 同一个 `--out` 不重跑：闭环拒绝非空的输出文件夹，比请求文件旧的响应文件不算数。
