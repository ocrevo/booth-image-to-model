# 场景描述（scene-description）

把“一张展台图里看到了什么”写成一份只有像素和普通数字的 JSON，由 `scripts/draft_compile.py` 编译成 booth-spec-v2 的 spec。它取代手写 `build_spec.py` 的重复部分：反投影、钳位、去重、分组、证据标记、校验。谁来写这份 JSON 都行：agent、脚本、人，或某个看图的模型；编译器不调用任何模型，也不信任 JSON 里的几何。

## 流程

1. 看图：读出图上标注的尺寸、8–10 个外角（默认全部参与拟合；检查角是可选功能，见下）、每个构件落地（或贴墙）的像素。
2. 写 `scene.json`，格式见 [`schemas/scene-description.v1.json`](../schemas/scene-description.v1.json)。所有键都必填，不适用的写 `null`；schema 只用各家结构化输出都支持的关键字。`schema_version` 新文件写 `"1.2"`（含展位语法 `booth_type`、`open_sides`、`cutaway_sides`，见下文“展位语法”；先写这三个键）；旧文件的 `"1.1"`、`"1.0"` 和 `"scene-description.v1"` 仍可读，旧文件没有 `corner_points[].fit` 或没有展位语法三个键时，编译器在校验前补 `null`（不改你的文件）。
3. `python "<SKILL_DIR>/scripts/boothctl.py" compile --scene scene.json --image <原图> --out <目录> [--fit box_fit.json]`。不给 `--fit` 就用 `corner_points` 现场拟合（同时运行 `scale_checks`）。单独运行 `box_fit.py` 时，标准输出只有**一个** JSON（与 `--output` 相同，警告和 `hint` 都在里面），退出码 0 = `fitted`，3 = 需要处理（`high_residual`、`ambiguous`、`check_failed`），2 = `blocked`。
4. 看 `<目录>/compile_report.json` 和命令输出的摘要：
   - `status`：ok / partial（有构件被丢弃）/ invalid（spec 没通过校验，退出码 2）。
   - `fit`：拟合状态和残差，加上独立检查的数字（`n_fit`、`n_check`、`loo_reliable`、`loo_median_px`、`loo_max_px`、`outliers`、`check_median_px`、`check_max_px`，摘要里是 `fit_check`；完整内容在报告的 `fit` 里，还有 `dropped`、`scale_checks`、`limits`、`hint`）。`ambiguity_hint` 只放真正的歧义提示（尺寸没被约束）；“重新点一下”这类建议在 `hint`。`high_residual` 不阻止编译，但 `fit_warnings` 要看；SKILL.md 第 3 步要求 `fitted`。
   - `quality_gate`：`{"status": "pass" | "review", "reasons": [...]}`。`review` 表示相机和比例还不可信，**先处理或明确接受 `reasons`，再出最终图**（见下“质量门”）。
   - `topology`：展位语法的四项检查和结果（见“展位语法”）；没写 `open_sides` 时四项都是 `not_checked`，质量门不变。
   - `dropped`（没建出来的构件及原因）、`adjustments`（被移动、去重、修剪、拉开 2 mm 的东西）、`assumed`（编译器代填的值）、`warnings`（含校验器的警告和“字段未被使用”的提醒）。
5. `boothctl.py preview --spec <目录>/spec.json ...` 对照原图，需要时改 `scene.json` 再编译；从输出目录再编译也可以（`--image <目录>/source.png`）。

输出：`spec.json`（原图复制为同目录的 `source.*`，路径是相对的）、`box_fit.json`、`compile_report.json`。**编译跑完才写文件**：场景描述被拒绝、拟合失败、读不了图时，输出目录保持原样，不会出现新旧混杂。拒绝场景（`scene_invalid`）、输入文件问题（`input_error`）、缺依赖（`missing_dependency`）、spec 校验失败（`invalid`）退出码均为 2；摘要都打印到标准输出。

依赖：numpy、scipy、Pillow、jsonschema（见 `requirements.txt`）。

## 独立检查与检查角（`corner_points[].fit`）

- `fit` 是 `boolean` 或 `null`，键必填（对严格结构化输出的服务商，必填且可为 `null`）。**默认全部写 `null`，即所有角都参与拟合。** `false` = **检查角**：用拟合出的相机投影并报告误差，但不参与拟合。
- 默认的独立检查是 `box_fit` 的**留一法**（leave-one-out）：每个参与拟合的角依次拿掉，用其余的角预测它，误差写进 `loo_median_px`、`loo_max_px`；其余的角预测不了的点名进 `outliers`，并从最终相机里剔除（`dropped`）。拟合用鲁棒损失，一个点错的角不会拖偏相机。
- 留一法只在拿掉一个角后还剩足够的多余方程时才算（`limits.loo_min_fit` 给出这次拟合至少要几个参与拟合的角；给定一个尺寸时通常是 8 个）。角点太少时 `loo_reliable` 为 `false`，`loo_*` 为 `null`，`outliers` 为空：宁可不报，也不报一个猜出来的名字。
- 检查角（`fit: false`）是可选功能：角点有 11 个以上，或想自己指定留出点时才用，留 2 个，选分散的、位置有把握的，别选唯一的比例锚点。每留出一个角就少一个拟合点：8 个角留 2 个只剩 6 个参与拟合，相机更不稳，留一法也算不了，所以 8–10 个角时不要用。
- 编译时：检查角写进 `box_fit` 的点文件（`"fit": false`）；能解析成地面角的点（`F` 且 x、y 都是 `±W/2`、`±D/2`）成为 `floor` 上的 landmark，检查角和被 `box_fit` 剔除的点（`dropped`，没进最终相机）的 landmark 是 `fit=false`（预览报告里算 `check`）；不在地面上的角没有可挂的构件，只出现在 `report.fit.check_points`。拟合结果没有列出某个被标为检查角的点时（例如用了旧的 `box_fit.json`），报告加一条警告；但 `box_fit` 有意跳过的检查角（它用了没有被拟合点约束的尺寸或 `?名字`，从没进过拟合）不报这条，`box_fit` 自己的警告会写 “was not checked”。

## 质量门

`compile_report.json` 的 `quality_gate` 和命令摘要里的 `quality_gate` 一致。出现下列任一条就是 `review`，每条写进 `reasons`：

- `box_fit` 状态不是 `fitted`（`high_residual`、`ambiguous`、`check_failed` 或别的）。
- 点名了出错的角：`outliers` 非空。只有留一法可靠时才有名字；不可靠时不点名，也不拿它的数字判断。
- 独立误差超过上限：`check_median_px`、`check_max_px`、`loo_median_px`、`loo_max_px` 对照 `box_fit` 在 JSON 里报告的 `limits`。质量门不另设一套阈值，也不自己按图大小缩放：`limits` 已经按图大小放宽（长边 1600 px 以内不变，更大的图线性放宽，从不收紧）。旧版 `box_fit.json` 没有 `limits` 时不比较数字，只看状态。
- 尺寸核对没通过：`scale_checks` 里有 `ok: false` 的项（量不出来的也算），理由以 “a size check failed” 开头，并写出量到的高度、预期高度和比值。假设的那个尺寸错了，像素误差不会变，这是唯一能发现它的信号。
- 没有任何独立信号：没有检查角（`n_check` 为 0），留一法又不可靠或没有数字（角点太少，或旧版 `box_fit.json`）。理由写明怎么补：加分散的角（`limits.loo_min_fit` 给出需要几个），或另外加几个检查角（`fit: false`）。
- 展位语法对不上（只有写了 `open_sides` 才会出现，细节见下一节），理由是下面三句原文，接在上面的理由后面：`the booth type does not match the open sides`、`a wall was placed on an open side and left out`、`the camera looks in through closed sides: front/back or left/right may be swapped`。

留一法可靠且干净（没有点名的角、误差在上限内）就是 `pass`，不需要检查角。质量门只读拟合结果和展位语法的检查，不阻止编译，也不改退出码；它的用途是别在相机和比例存疑时直接出最终图。

## 展位语法（`booth_type`、`open_sides`、`cutaway_sides`）

读图时先写这三个键（schema 1.2 起三个都必填，可为 `null`）：

- **开口边（`open_sides`）**：展位占地上没有墙、观众进入的边。边用和角点记号相同的词：`front` = −D/2（−Y），`back` = D/2（+Y），`left` = −W/2（−X），`right` = W/2（+X），必须和 `corner_points[].xyz` 的写法一致。每条边最多列一次；`[]` = 四边都封闭；`null` = 看不出来。
- **展位类型（`booth_type`）**：`inline` = 1 个开口边，`corner` = 2 个**相邻**的开口边，`peninsula` = 3 个，`island` = 4 个，`other` = 其他情况（例如 2 个相对的边、不规则外形）；`null` = 看不出来。
- **剖切边（`cutaway_sides`）**：效果图里只为展示内部而去掉了墙（或顶）的边。真实展位在这里有墙，**不是**开口，所以不写进 `open_sides`；一条边不能同时在两个列表里。

`null` 是“未知”，不是“没有”。`open_sides` 为 `null` 时不做任何检查，编译结果与没有这三个键的旧文件逐字节相同（旧文件由编译器补 `null`）。`booth_type` 为 `null` 而 `open_sides` 已写时只跳过类型检查；`cutaway_sides` 为 `null` 按“没有剖切边”处理。同一条边列两次、或同时出现在 `open_sides` 和 `cutaway_sides`，场景会被拒绝（`TOPOLOGY`）；类型和开口边数不符不拒绝，由下面的检查报告。

`compile_report.json` 的 `topology`（命令摘要里是同一份）是 `{"booth_type", "open_sides", "cutaway_sides", "checks": [{"id", "status", "message"}]}`。四项检查固定按下表的顺序，`status` 为 `ok`、`warn`、`fail` 或 `not_checked`；只有 `fail` 会让质量门变成 `review`。

| id | 检查什么 | 不通过时 |
|---|---|---|
| `type_matches_sides` | 类型和开口边数相符（`other` 对任何开口边都相符） | `fail`；质量门理由 `the booth type does not match the open sides` |
| `wall_on_open_side` | 场景里 `type` 为 `wall` 的构件，其 `plane` 不在 `open_sides` 里 | 这样的墙**不建**，写进 `dropped`（`reason`: `wall on an open side`），状态 `partial`；`fail`；质量门理由 `a wall was placed on an open side and left out`。剖切边上的墙照建 |
| `closed_side_without_wall` | 封闭边（既不是开口边也不是剖切边）上有 `wall`、`wall_panel`、`led_wall` 或 `tv` | 只是 `warn`，信息性质，**不**进质量门 |
| `camera_side` | 拟合出的相机（展位坐标，X 右、Y 后）至少在一条开口边或剖切边的外侧，即越过该边的占地边缘 0.25 m 以上 | 相机只在封闭边的外侧：`fail`；质量门理由 `the camera looks in through closed sides: front/back or left/right may be swapped`。相机不在任何一边的外侧（在占地上方，或贴着边）：`warn` |

`camera_side` 要抓的是“前后或左右记号被整体对调”：这种错误让所有角点互相一致、残差照样很小，拟合看不出来，只有开口边的位置能暴露。它有边界：相机本来就在某条开口边的外侧时，对调看不出来（例如只有 `front` 开口，相机在前面偏左还是偏右都在 `front` 外侧）。`wall_on_open_side` 抓“把有墙的边误写成开口边”（前提是场景里画了那堵墙）；反过来把开口边误写成封闭边，只有相机刚好只在那条边外侧时才会被 `camera_side` 抓到，`closed_side_without_wall` 只是提示。

## 约定

- 像素是原图像素，原点在左上。`base_px` 是构件和地面接触处的中心像素；`wall_rect_px` 是贴墙平面构件的 `[左上 u, v, 右下 u, v]`，会被反投影到墙面平面上，所以要框的是构件贴着墙的轮廓（电视的边框外缘、LED 墙的外缘）。
- `plane` 是展台的哪条边：`back`（+Y）、`front`（−Y）、`left`（−X）、`right`（+X）。
- `inset_m` 因类型而异：`wall` 是墙**外面**离边缘多远（默认 0）；`tv`、`led_wall`、`wall_panel` 是构件所贴的**墙面**离边缘多远（默认 0.1，即贴在 0.1 m 厚墙的内面）；`roof_frame` 是外框向内缩多少（默认 0）。墙的厚度或位置不是默认值时，贴墙构件的 `inset_m` 要写成同一个墙面位置，否则会埋进墙里。
- 尺寸一律米；`size_m = [宽(X), 深(Y), 高]`，写 `null` 的项在有设计先验时用先验。**每一个由先验或默认值补上的数都会记入 `assumed`**（厚度、台面深度和高度、桌的半径和高度、椅座高、聚光灯体、LED 模组和深度、电视边框等），并汇总成 spec 里的 `compile_assumed_values` 不确定项。
- `z_range_m` 是离 z=0 的高度（不是离地面）。
- `evidence: observed` 必须给 `roi_px`（要和图像有交集）；隐藏或假设的部分写 `inferred`。
- 颜色写 `#rrggbb`（从图上读的 sRGB）；`null` 用类型默认色。
- 宏类型（counter、round_table、shell_chair、planter、louvre_wall、tv、led_wall、spotlights）生成多个零件，`id` 就是前缀，同时是分组名；别的 `id` 不能以“前缀_”开头。`box`、`cylinder`、`wall`、`wall_panel`、`roof_frame` 单件，可用 `group` 指定分组。
- 地面不是场景构件：由占地尺寸自动生成，属于范围 `booth_floor`（没定义时自动创建；定义了就必须是 include、owner=booth）。

## 构件类型

| type | 必填 | 其他输入 / 默认 |
|---|---|---|
| `counter` | `base_px`、`size_m[0]` 宽 | 深默认设计先验；高取 `size_m[2]` 或 `height_m`，默认先验；`yaw_deg` 只认 90 的倍数（其他角度警告并忽略）；`accent_hex` 台面色 |
| `round_table` | `base_px` | `radius_m`、`height_m` 默认设计先验；`accent_hex` 桌柱色 |
| `shell_chair` | `base_px` | `yaw_deg`（0 朝 −Y）；`accent_hex` 椅腿色 |
| `planter` | `base_px` | `size_m` 宽深默认 0.4；高取 `size_m[2]` 或 `height_m`，默认 0.45 |
| `box` | `base_px`、`size_m` 宽深、高（`size_m[2]` / `height_m` / `z_range_m`） | `yaw_deg`、`bevel_m`+`bevel_mode`、`role`、`critical`、`note` |
| `cylinder` | `base_px`、`radius_m`、高（同上） | `role`、`critical`、`note`；没有 yaw 和倒角 |
| `louvre_wall` | `base_px`、`base_px_2`（排的两端）、`count`、`width_m`（片宽）、高 | `thickness_m` 默认 0.06。排向取最接近的 X 或 Y 轴（偏离超过 15° 警告）；超出展台的部分修剪并记录；`count` 片排不下时丢弃；`count=1` 居中 |
| `wall` | `plane`、高 | `base_px`+`base_px_2` 成对给出才限定长度（默认整条边）；`thickness_m` 默认 0.1；默认 critical |
| `wall_panel` | `plane`、`wall_rect_px` | `thickness_m` 默认 0.05；超出边缘的部分修剪 |
| `tv` | `plane`、`wall_rect_px`（含边框）、`inch`（43/50/55/60/65/75/85） | `bezel_m` 默认 0.02，`depth_m` 默认 0.05；按档位尺寸建，矩形差 25% 以上警告；部分超出墙边时沿墙滑回，完全在墙外则丢弃；`accent_hex` 是屏幕色 |
| `led_wall` | `plane`（只支持 back）、`wall_rect_px`、`count`（列）、`rows` | `module_m` 默认 0.5，`depth_m` 默认 0.1；整块模组，和矩形不符时警告；超出墙边时沿墙滑回 |
| `spotlights` | `points_px`、`height_m`（挂点高度） | 点按 z=`height_m` 平面反投影；灯体半径 0.04、长 0.12 记入 `assumed` |
| `roof_frame` | 高（`height_m` / `z_range_m` / `size_m[2]`） | `width_m` 框带宽默认 0.6，`radius_m` 角半径默认 0.8；外形取占地，`inset_m` 向内缩；内圆角自动收到开口短边的一半以内 |

尚不支持：`header_band`、`slats`、`shelves`、`wall_screens` 等其他宏和 `swept_strip` / `profile_extrusion` / `arc_panel`。遇到这些造型，先用 `box` / `cylinder` 近似并在 `uncertainties` 里写明，或在编译后的 spec 上继续用 SpecBuilder 或网页编辑器补。

每个类型只读上表列出的字段。场景里填了而该类型不读的字段（例如宏上的 `critical`、`cylinder` 上的 `bevel_m`）不会被悄悄吞掉：报告 `warnings` 里会写 “X 不被类型 T 使用，已忽略”。

## 编译器的规则

- 相机：`box_fit` 现场拟合，或使用给定的 `box_fit.json`。给定的 fit 必须完整并与场景一致：图像尺寸、`known` 的 W/D/H、`floor_z`、相机都要对得上，否则拒绝（`FIT_MISMATCH`）；高度既没给也没有角点确定时拒绝（`H is unknown`）。给定 fit 时 `scale_checks` 不会运行，报告里会这样提醒。拟合状态、残差、独立检查的结果、拟合警告和歧义提示都写进 `scale.notes`、报告和摘要。
- 地面：由占地尺寸自动生成（critical，下表面在 `floor_z − 0.05`），地面四角里对应 `corner_points` 的点自动成为 landmark，供预览对照（带该角的 `fit` 标记，检查角为 `fit=false`）；x、y 各自按自己的记号（W/2 或 D/2）取值。
- 位置：落地构件钳进占地范围（旋转后的盒子按旋转后的外形，台面按含 2 cm 出挑的外形）；超出的移回并写进 `adjustments`；比展台还大的构件另给警告。聚光灯 0.25 m 内的重复位置只留一个。
- 共面：同向共面的外露面会渲出黑面；后加的盒子（否则前一个）被拉进 2 mm，**地面从不被移动**，拉不开的列在 `coplanar_unresolved`。
- 范围检查：构件包围盒超出占地 2 cm 以上的，报告里给警告（屋顶框和地面除外）。
- 展位语法：写了 `open_sides` 时，开口边上的 `wall` 不建（见“展位语法”）；别的构件不受影响。
- 失败：单个构件建不出来（矩形落到展台外、几何参数被构建器拒绝、排不下等）时整件跳过并写进 `dropped`，它的假设、调整和警告一并撤销，其余照常编译，状态为 `partial`；没有构件的范围会被移除。
- 数值上限：构件 ≤ 500，`count` ≤ 200，`rows` ≤ 100，聚光灯点 ≤ 200，尺寸 ≤ 100 m；`radius_m`、`height_m`、`width_m`、`thickness_m`、`depth_m`、`module_m`、`bezel_m` 必须大于 0；整数字段接受 5.0 这样的整数浮点，不接受 2.5。
- 不做：不猜品牌和文字，不确定的结构不自行补全，不给出合规结论。

## 最小例子

```json
{"id": "counter", "type": "counter", "scope": "furniture", "group": null, "role": null, "evidence": "observed",
 "roi_px": [500, 330, 640, 420], "color_hex": "#F2F0ED", "accent_hex": "#B8D6E0", "base_px": [560, 410], "base_px_2": null,
 "plane": null, "wall_rect_px": null, "points_px": null, "size_m": [1.4, null, null], "radius_m": null, "height_m": null,
 "width_m": null, "thickness_m": null, "depth_m": null, "z_range_m": null, "yaw_deg": null, "count": null, "rows": null,
 "module_m": null, "inch": null, "bezel_m": null, "bevel_m": null, "bevel_mode": null, "inset_m": null, "critical": null, "note": null}
```
