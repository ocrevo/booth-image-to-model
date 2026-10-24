# 语义构件 recipes：复用组合，不复用答案

这里是可重复 spec 组合，不新增 counter/screen/chair 等 kind，不是固定样本模板。公开 primitives 为 box、cylinder、rounded_ring、rounded_portal、swept_strip、profile_extrusion、arc_panel、text；精确参数以 [schema](../schemas/booth-spec-v2.schema.json) 为准，坐标/证据见 [data-contract](data-contract.md)。
各子件保留独立 component_id、scope_id、placement、material 和原图依据。组合的尺寸、数量、位置、朝向及是否出现按当前图确定；隐藏连接明确 inferred。

| 语义组合 | primitives/拆分 | 本图必须确定 |
|---|---|---|
| 中空闭合吊楣 | rounded_ring + 可见支撑/灯 | 是否真闭环、内外尺寸、圆角、厚度、标高；不补实心顶板 |
| 连续圆角门架 | rounded_portal；独立面板只按观察添加 | 开口、柱宽、梁高、朝向、接地、连接；底部不封口 |
| 带边缘缺口的墙/异形面板 | profile_extrusion，一个连续外轮廓 | 顶/底/侧敞开方向、圆弧采样、板厚与轮廓；不把敞口补成闭合孔 |
| 弧形楣板/墙板 | arc_panel，相邻角段可分材质 | 半径、弯曲朝向、角跨度、厚高、实际分色；不自动生成后半环 |
| 开放楣框/连续弯肋 | swept_strip，重复实例 | Bezier 路径、宽深、根数、间距；不融合成墙或强套闭环 |
| 圆角柜台 | bevel box + 独立台面/踢脚/面板 | 真实圆角、厚度、悬挑、位置；删品牌仍保留面板 |
| 屏幕墙/立式屏 | box 墙/支承 + 框条 + 中性显示面 | 数量、尺寸、朝向、附着面/凹槽；不删外框 |
| 圆桌/柱状展具 | cylinder 主体/台面 + 可见支腿 | 半径、高度、腿数、接地；圆台有上下盖 |
| 自有灯带/下照灯 | 窄 box/swept_strip/薄 cylinder + emission | 路径、数量、朝向、booth 归属；不从光斑补展馆灯 |
| 座椅/植物等装饰 | 可准确表达的 primitive 组合，否则按范围扩展或用允许资产 | 数量与曲面按图，不默认添加家具或以盒子代曲面 |

## 代码宏（scripts/components.py）

`SpecBuilder` 收集 scope、构件、landmark（世界坐标自动换成局部坐标）、uncertainties，关键构件自动生成 requirements。宏只发出上表的 primitive：

| 宏 | 产出 | 主要参数 |
|---|---|---|
| `box` / `cylinder` | 由 (min, max) 区间生成轴对齐盒或圆柱 | x、y、z 区间；bevel |
| `counter` | 可选退台踢脚 + 柜体 + 台面 | x、y 区间，高度默认 1.05 |
| `round_table` | 台面、立柱、底盘 | 中心、半径、高度 |
| `shell_chair` | 圆座、环绕靠背（arc_panel）、四根外撇腿 | 中心、朝向 yaw（0 朝 −Y） |
| `louvre_wall` | 一排竖向百叶，可隔片后退 | 分布区间、厚度区间、片数、片宽 |
| `slats` | 等距格栅 | 首根中心、间距、根数 |
| `planter` | 种植槽、土面、确定性草叶 | 区间、高度、叶数 |
| `wall_screens` | 墙面框带 + 中性显示面 | 墙面 y、框和屏的 (x, z) 区间 |
| `shelves` | 侧墙搁板与可选背衬条 | 墙面 x、y 区间、各层高度 |
| `header_band` | 轴对齐的直段、L 或 U 形门头楣带（拐角不重叠）和落地立柱 | 路径点、带深、(底, 顶)、立柱位置；角色 `header_band`/`header_post` |
| `tv` | 标准 16:9 档位的电视：框 + 凸出 3 mm 的中性显示面 | 墙面位置、英寸档、边框宽和厚度（必须给出）；可贴 ±X、±Y 四面墙 |
| `led_wall` | 整块模组的 LED 墙，接缝为对齐板边的表面格网 | cols、rows、module（0.5 / 0.498 m 或图中所见）、深度（必须给出） |
| `spotlights` | 自发光小灯体，只建灯体、不建光锥 | 平面位置列表、挂点标高 |

每个宏发出的零件都带 `group=前缀`（`with b.grouped('名字'):` 可给手写零件同样分组，`add(..., group=...)` 可单独指定）。

位置优先用 `camera_math.unproject` / `rect_on_plane` / `floor_point` 从原图像素反投影得到，默认尺寸来自 [设计先验](design-priors.md)。宏的默认值都要按本图覆盖。

## 形状 API

- box：`size=[x,y,z]`，可选 bevel_radius、bevel_mode=all/vertical。all 圆角小于最薄边一半；vertical 约束 XY 最薄边且上下端平切。圆角真实驱动顶点。
- cylinder：radius、height，局部轴向 Z；placement/rotation 决定方向。
- rounded_ring：局部 XY；outer_size、inner_size 为二维尺寸，outer_radius、inner_radius 控制轮廓，height 沿 Z。开口有效，内外轮廓不相交。
- rounded_portal：局部 XZ，depth 沿 Y；width、height、side_width、beam_height、outer_radius、inner_radius。原点为整体包围盒中心，是两脚连续带状实体。
- swept_strip：control_points 为 3n+1 个局部 `[x,z]` 点，每三个新点延续一段 Bezier；width 为路径平面内带宽，depth 沿 Y。局部中心由控制点约定，placement/rotation 决定世界位置。
- profile_extrusion：profile 为局部 `[x,z]` 点，depth 沿 Y。输入单个不自交闭合轮廓，不重复首点；弧线由本图所需的采样点近似。凹轮廓端面三角剖分，侧面连接形成真实实体；开口保留而不是贴图伪装。
- arc_panel：radius、thickness、height、start_deg/end_deg、segments。局部 XY 圆心为原点，厚度跨中心线两侧，高度以 Z=0 对称；角度从 +X 逆时针。相邻分段使用共同圆心及边界，弧面平滑、端面平切。
- text：仅在用户明确要求图文时使用；默认清洁图不实例化商业文字，也不把整张源图贴到模型保留广告。

## 组合关系

柜台主体高度扣除台面/踢脚后定位子件，悬挑和圆角按图，避免穿插；隐藏背板最小补全。
屏框和显示面共享朝向，显示面位于框可见表面内侧；墙、柱、弯肋等支承分别建件并保持遮挡，不用整块亮面替代可见边框。
重复肋条共享生成逻辑，不写死数量/间距：逐根记录位置，遮挡段标 inferred。源图没有出现的组合不实例化。

## 扩展范围

复杂产品、孔阵、曲面家具、植物资产只有属于本次交付范围才扩展；当前没有资产导入器、自动 UV 或现成精细家具系统。关键形状不支持时扩展通用 builder/schema/测试；客户专有 mesh、品牌和固定尺寸留在样本目录。
从照片/sRGB 色板估色时用 `color_space=srgb`。贴图只用可用用户素材或内置内容，不随包分发运行环境字体。
重复细格纹使用 `material.pattern`，其中 `type="grid"`；按局部米制或弧长展开，参数见数据契约。格网只表达表面纹样；看不清是印刷、织物还是穿孔时保留不确定性，不自动生成真实孔阵。

合法的材质片段如下；具体颜色、米制间距和线宽由本图决定，不在 pattern 下再嵌套 grid 字段：

```json
{
  "color": [0.8, 0.8, 0.8, 1],
  "color_space": "srgb",
  "pattern": {
    "type": "grid",
    "spacing": [0.1, 0.1],
    "line_width": 0.005,
    "line_color": [0.2, 0.2, 0.2],
    "coordinates": "auto"
  }
}
```

## 设计路线：canopy_frame（顶框）

设计路线（`design.json`，`design_compile.py`）的头顶件 kind。顶上一圈空心圆角框架，四边带宽可以各不相同（内孔不居中），可选在一个角上接一段更深的标识楣板。只用现有基元：`profile_extrusion` 不能有内孔，所以框由两段组成（沿穿过内孔的平面切开），两段共用切面和外轮廓的点，外立面共面、端面对齐，渲染里看不出分段；两段都属于同一个元素（同一 `group`）。

| 字段 | 含义 | 缺省（记进 `assumed`） |
|---|---|---|
| `size_m` | 外缘宽、外缘深、楣高 | 占地四边各内缩 0.5 m；楣高 0.3 m；只把缺的那一个值记进 `assumed`（`width_m`、`depth_m`、`height_m`） |
| `centre_m` | 平面中心 | 占地中心 |
| `z_m` | 框底离地高 | 顶面在限高下 0.1 m |
| `params.corner_radius_m` | 外角半径 | `min(0.3, 外缘短边的一半 - 0.05)` m，不小于 0；给定值必须在 0 到短边一半之间 |
| `params.band_m` | `front`、`back`、`left`、`right` 四个带宽，从外缘往里量 | 0.4 m，框很小时缩到短边的四分之一 |
| `params.sign` | 加深段；`null` 或不写则没有 | 不写就没有 |
| `sign.corner` | `front_left`、`front_right`、`back_left`、`back_right` | `front_left` |
| `sign.along_m` | `[沿第一条边的长度, 沿第二条边的长度]`，从外缘角点量起；第一条边是 corner 名字里的前后边，第二条是左右边 | 外缘宽的 0.4 倍、深的 0.3 倍（并保证越过角上的带宽） |
| `sign.drop_m` | 比框底再往下加深的高度 | 0.25 m |
| `sign.end_radius_m` | 加深段两个自由端的竖向圆角半径：端头从框底到楣板底沿四分之一圆弧圆顺地弯下去；0 或不写是方端（原来的做法） | 0（方端）；这是唯一不记进 `assumed` 的缺省值，为的是已有输出逐字节不变 |
| `sign.text_chars` | 占位字母个数（0 到 40） | 4 |

内角半径取外角半径减相邻两带宽中较大的那个，不小于 0。此外有一条独立的规则：内角半径再限制在内孔短边的一半以内。这条限制即使在按上面公式本来可行的输入上也会改变结果（内孔的四个圆角都变小），所以内孔的轮廓以限制后的半径为准，不是只在公式不可行时才起作用。内孔不能碰到外缘（圆角半径相对带宽太大时拒绝）。

`along_m` 的界限：切面是一条垂直于边的直线，它必须落在外缘和内孔的直线段上，不能落在圆角弧上。第一个值（沿前后边）必须大于 `max(外角半径, 角侧的带宽 + 该角的内角半径) + 1 mm`，并且小于边长减去 `max(外角半径, 对侧的带宽 + 对角的内角半径) + 1 mm`；第二个值（沿左右边）同理，用前后带宽和对应的角（这里的内角半径是上面限制之后的值）。外角半径比带宽大时，沿边的下限就是外角半径，所以比外角半径更短的加深段（只包住角、沿边不到一个圆角半径）表达不了。不满足时按 `PARAM` 拒绝，信息里写出允许的范围。编译时的最后一道保护只是检查各段面积之和等于框的面积，它发现不了切面顺序错；顺序正确靠的就是这些直线段界限。

`end_radius_m` 的做法：加深段围着角有两个自由端（沿第一条边的一端和沿第二条边的一端），每一端的下缘是真实的竖向圆角，不是贴图也不是倒角修改器。圆心在楣板底之上一个半径、离自由端一个半径处：从端面（高度 `楣板底 + 半径`）起沿四分之一圆弧下降，到离端 `end_radius_m` 处与楣板底相切；这是凸圆（像牛鼻边）：端面在上端是竖直的，到楣板底时弯成水平，不是凹的内圆角。半径等于 `drop_m` 时端头正好从框底弯到楣板底。圆弧按等角度分 12 段。加深段因此由三件组成（同一个 `group`）：中间一件（平面外形，和以前一样）和两个端件（`profile_extrusion`，轮廓画在竖向剖面里，沿所在的条带挤出，不转平）；框的那一件仍在原来的切面处接上，外立面还是同一个面，件与件之间没有缝、台阶或重叠，也没有悬空的件：端件的位置和轮廓取的是框的各件所在的 5 位小数网格上的值（位置是网格值的中点，不再单独四舍五入），端件的上下高度取自中间那件已存下的值，所以不管输入有几位小数，接缝处的坐标都相同（差 0）。角上的圆弧段不加圆角。因为端件落在带的直线段上，`along_m` 的两个下界各加 `end_radius_m`（切面要有一个半径长的直线段）。记进 `assumed` 的 `sign.along_m` 缺省值取 4 位小数，`along_m` 的界限比较留 1e-6 m 的容差，所以从报告里读回来写进设计的值一定被接受。`end_radius_m` 必须在 0 到 `drop_m` 之间（0 以外至少 0.001 m），否则按 `PARAM` 拒绝。占位字不放在圆角上：字的范围缩到外角半径到 `along_m` 减 `end_radius_m` 之间。

参数被拒绝（`PARAM`）的情况：带宽不是正数、对边带宽之和留不出 0.05 m 的内孔、外缘比占地大、圆角半径为负或超过短边的一半、`along_m` 超过所在边长或没有落在直线段上（见上）、`drop_m` 不在 0 到 2 m、`text_chars` 不是 0 到 40 的整数、`text.size_m` 不是正数；竖向放不下也拒绝：给了 `z_m` 时，`z_m` 必须在 `0.3 + drop_m` 到 `限高 - 地面高 - 楣高` 之间（信息里写出这个范围），没给 `z_m` 时楣高加 `drop_m` 至多是 `限高 - 地面高 - 0.4`（顶面在限高下 0.1 m，最低点离地至少 0.3 m）。地面高取设计的 `layout.platform_height_m`（没写按 0）；用 `--fit` 编译时取拟合的 `floor_z`（编译前把它写进检查用的地面高，范围和构建用的一致）。

占位字贴在加深段第一条边的外立面，内容是同样多的同一个大写字母（避开 brief 里出现过的字母串），不抄 brief 或原图的文字；它记在 `compile_report.json` 的 `choices`（`sign_text`，来源 `choice`），占位字部件的依据说明只写 `choice`，spec 的 `brand_text` 排除说明随之写明占位字是 choice。`text.size_m` 有值时按它定字高，但不超过加深段外立面放得下的最大字高（超出时用最大字高并在 `compile_report.json` 的 `warnings` 里记一条）；没有就用自动字高。元素的 `text.body` 不会被建出来。顶框由 `centre_m` 和 `size_m` 摆放，给了 `side`（不是 `centre`）、`along`、`yaw_deg` 或没有加深段时给了 `text.size_m`，这些值不起作用，`compile_report.json` 的 `warnings` 里会记一条；带宽太薄使立柱窄于 0.15 m 时也记一条并写明立柱尺寸。`hanging_allowed` 不为 `true` 时在四角按 `ring` 的规则加立柱，加深角的立柱到加深段的底。

```
 plan (front = bottom)            sign: corner front_left, along_m = [a1, a2]

 +------------------------+       back band    (band_m.back)
 | +--------------------+ |
 | |                    | |       left band    (band_m.left)  and  right band (band_m.right)
 | |      opening       | |       the opening is off centre when the bands differ
 | +---+----------------+ |
 |#####|                  |       #### = sign piece: same outer face as the frame,
 +#####+------------------+       its bottom is drop_m lower; it ends in cuts a1 on
  <-a1->                          the front edge and a2 on the left edge
```

## 设计路线：lit_panel（灯槽墙板和凹龛）

落地件 kind，摆放同 `partition`：靠一条边（`side`、`along`、`offset_m`），或用 `centre_m` 加 `yaw_deg` 斜放（任意角度，板转后仍留在占地内，移动会记进 `adjustments`）。立面是矩形，顶部两角按 `corner_radius_m` 倒圆，成倒 U；`yaw` 为 0 时板面朝 -Y，灯槽和凹龛开在这一面。只用现有基元（`profile_extrusion`），不改 `design_compile.py`。

| 字段 | 含义 | 缺省（记进 `assumed` 或 `choices`） |
|---|---|---|
| `size_m` | 板宽、板厚、板高 | 2.4 m、0.15 m（`choices`）；高是墙高减地面高（`choices` 里的 `height_m`）；高度超过限高时按限高夹掉并记进 `adjustments` |
| `params.corner_radius_m` | 顶部两角半径，0 到板宽一半，不超过板高 | `min(0.4, 板宽一半)` |
| `params.inset_m` | 槽的外缘离板边多远 | 0.08 m |
| `params.groove_width_m` | 槽宽 | 0.03 m |
| `params.groove_depth_m` | 槽凹进板面多深；槽底之后至少还有 0.02 m 板厚 | 0.02 m |
| `params.light_hex` | 槽里发光带和龛口灯圈的颜色 | `#ffe2a8` |
| `params.niche` | 凹龛；`null` 或不写则没有；写 `{}` 则全用缺省 | 没有 |
| `niche.width_m`、`height_m` | 开口宽、高（至少 0.05 m） | `min(0.9, 槽内板心宽的一半)`；板心高（槽内）的 0.45 倍，至多 1.4 m |
| `niche.bottom_m` | 开口下沿（龛底面）离板底的高度；板底就是地面（`z_m` 为 0 时） | `max(0.3 × 板高, ring_width_m + 0.03)` |
| `niche.depth_m` | 开口向板里凹多深；不大于板厚减 0.01 m，所以龛后壁到板背面至少还有 0.01 m，不穿透 | `min(0.10, 板厚 - 0.02)` |
| `niche.corner_radius_m` | 开口顶部两角半径；取宽度的一半就是拱形；下沿两角是直角 | `min(开口宽的一半, 开口高)` |
| `niche.ring_width_m` | 开口四周发光边的宽度 | 0.03 m |

板形一变（`size_m`、`corner_radius_m`），槽和龛自动跟着走；轮廓由板形算出，没有手写的控制点。

```
 正视（yaw = 0，观众在 -Y 侧）                 剖面 A-A（沿龛的中线：左 = 板面，右 = 板背，上 = 上）

      .-------------.   <- 板顶，两角 R            板面                              板背
    .'  .---------.  `.                            |                                 |
   /   /  .-----.  \   \   <- 灯槽（倒 U，         |<------- depth_m ------->|     |
  |   |  /   N   \  |   |     宽 groove_width_m，   |  灯圈（光，和板面共面）   |#####|  <- 龛后壁，之后到
  |   |  |       |  |   |     离板边 inset_m，      |      龛（空）            |#####|     板背还有板
  |   |  |       |  |   |     槽底在板面后          |                          |#####|
  |   |  '-------'  |   |     groove_depth_m）     |==========================+#####|  <- 龛底面
  |   |   ring bar   |   |                         |##########################|#####|     = 下横条的顶面
  |   |              |   |                         |##########################|#####|
  |___|______________|___|  <- 板底（地面）
   W 宽，H 高；N = 凹龛（顶部两角圆，下沿直角）；灯圈 = N 四周宽 ring_width_m 的发光边，和板面共面
```

做法：整块板由几件拼成，都属于同一个元素（同一 `group`），共用切面和外轮廓的点，外立面共面，渲染里看不出分段。`profile_extrusion` 不能有内孔，所以每一层都切成一件件的 U 形或块：

- `frame`（倒 U 边框，槽外）、`core`（槽内的板心；有龛时是 `core_base` 加开了缺口的 `core`），全厚，板面共面；
- `groove_base`：槽的那一带，从槽底到板背；槽底就是它的前表面；
- `groove_light`（角色 `lit_groove`）：槽里的发光带，前表面在板面之后 `groove_depth_m - 0.0005 m`，是躺在槽底上的一块 0.5 mm 厚的板（不嵌进槽底，免得两件同向的面落在同一平面上闪烁）；
- 有龛时：`ring_light`、`ring_bar_light`（角色 `lit_niche_light`，发光，和板面共面，深 `min(0.02, depth_m / 2)`），`ring_back`、`ring_bar_back`（灯圈后面的板），`niche_back`（角色 `lit_niche`，龛后壁，从 `depth_m` 到板背）。灯圈是开口四周一圈：左、右、顶是倒 U，下沿是一条横条。龛底面就是下沿横条的顶面。

发光强度是 1.5，和设计路线的发光件（`material.emissive`）相同；颜色取 `light_hex`。其余部件的颜色角色是 `dominant`（`material.role` 可改）。部件角色 `lit_panel`、`lit_groove`、`lit_niche`、`lit_niche_light` 写在 `design_check.py` 的 `GENERIC_ONLY_ROLES` 里，`not_checked` 里会写明它们只跑了通用检查（占地、限高、支撑路径）。

参数被拒绝（`PARAM`）的情况：`inset_m` 加 `groove_width_m` 不小于板宽的一半减 0.025 m（槽把板心吃光）；`inset_m`、`groove_width_m`、`groove_depth_m` 小于 0.005 m；`groove_depth_m` 大于板厚减 0.02 m（槽底之后要有板）；板厚小于 0.04 m；顶角半径为负、超过板宽一半或超过板高；`niche.depth_m` 大于板厚减 0.01 m（不穿透，后壁至少 0.01 m）或小于 0.005 m；凹龛连灯圈超出槽内板心（左右、顶，各留 5 mm），或 `bottom_m` 不到 `ring_width_m` 加 0.02 m（龛要站在实心底座上），或开口下沿在板心弧顶之上；`niche.corner_radius_m` 大于开口宽的一半或开口高。板高没写时，依赖板高的几条（顶部放得下）在编译时才检查，放不下的元素会进 `dropped`。

```
正视（yaw = 0）：u 向右，z 向上；板宽 W，高 H，顶角半径 R

     +- R -+                         外轮廓：W × H，顶部两角半径 R
    /       \                        槽的外缘：向里 inset_m（W - 2 inset，H - inset，半径 R - inset）
   |  .---.  |                       槽的内缘：再向里 groove_width_m
   |  |   |  |                       有龛：开口宽 niche.width_m，高 niche.height_m，
   |  | N |  |                       下沿离板底 niche.bottom_m；灯圈向外再 ring_width_m
   |  '---'  |
   |         |
   +---------+  <- 板底
```

校验和构建按同一套算法：板转过以后（`yaw_deg`，或由所靠的边定出的朝向；在中心且没写 `yaw_deg` 时朝向由编译器实际用的入口边定出）的外廓含厚度必须放进占地，放不进就在校验时按 `PARAM` 拒绝，信息里写出能放下的最大板宽；靠边摆放（没写 `centre_m`）时板宽先按那条边的长度截断（边长取占地长度减去封闭端墙，墙厚取 `layout.wall_thickness_m`，没写按编译器的缺省 0.10 m），比占地还宽的板也先截再判。PARAM 检查拿到的 `form` 为此带 `wall_thickness_m` 和 `entrance_side`（编译器实际用的值）。`lit_panel` 和 `prop_block` 自己把自己摆进占地（`motifs.SELF_PLACING_KINDS`）：`place_floor` 对它们不夹、不记，把靠边或居中算出的请求位置原样交给 builder；builder 从这个位置按转后的外廓只夹一次，用 `Frame.adjust` 记一次（`moved inside the footprint from (请求位置) to (实际位置)`），靠边摆放和居中摆放都一样；靠边时 `along` 位置也原样交给 builder，不先按没转的宽度夹一次，连同它的空间（`Placed.span`，封闭墙内表面围起来的矩形；开放的一侧以占地边为界）一起交：builder 按转后外廓夹在这个空间里，所以件不会伸进封闭的墙，不转时和 `box` 一样贴墙即止。转后的外廓比这个空间还大（例如沿边比两道端墙之间还长）时，校验按 `PARAM` 拒绝并给出能放下的最大宽度，builder 也丢掉，不会居中后伸进墙或伸出占地。居中摆放（`centre_m`）的件照旧只夹在占地内。凹龛加灯圈是否落在槽内板心里，按构建时实际用的折线圆弧（每个圆角 12 段）判，不按精确圆弧。`params.groove_depth_m` 这个键按 kind 解释：对 `lit_panel` 是槽凹进板面多深（见上表）；以后别的 kind 用同一个键表示别的意思时，在各自一节里写明。

## 设计路线：prop_block（道具占位块）

落地件 kind，摆放同 `box`：靠一条边（`side`、`along`、`offset_m`），或用 `centre_m`，可选 `yaw_deg` 转任意角度（块转后仍须留在占地内）。它是**一个 box**，代表一件不建模的物品（桌上的笔记本、平板、显示器、手机、耳机，盆栽，机械臂，机器人）：尺寸和位置要对，外形不做——没有翻开的屏幕、支架、耳机头梁、宽叶或花。评分按位置和尺寸算，不按外形算；在还原记录里这类行标为"替代"。一件物品一个元素，多少件就是多少个元素，不要把几件合成一个块，也不要用一整块盖住整张桌面。

| 字段 | 含义 | 缺省（记进 `assumed`） |
|---|---|---|
| `size_m` | 宽（沿所靠的边）、深、高，每个至少 0.001 m | 0.4 × 0.4 × 0.4 m（缺哪个记哪个：`width_m`、`depth_m`、`height_m`） |
| `z_m` | 块底离地面（含 `platform_height_m`）的高度，放在桌面或层板上时给桌面高度 | 0（记 `z_m`）；给定值须 ≥ 0，且块底到限高至少留 0.01 m |
| `yaw_deg` | 绝对转角（0 把宽放在世界 X 方向）；不写则是与所靠的墙平行的 box。所以靠左、右边的块一旦写了 `yaw_deg`，宽就不再沿那条边走（要沿边请不写，或写 ±90） | 不写 |
| `params.label` | 封闭枚举：`laptop`、`tablet`、`monitor`、`phone`、`headphone`、`plant`、`robot_arm`、`robot`、`other`；只写进部件的 `evidence.note`（`label: laptop`） | `other`（记 `label`） |
| `material` | 颜色走元素的 `material`（`color_hex`、`role`、`finish`、`emissive`），不加新字段 | 颜色角色 `secondary` |

高度超过限高时削到限高并记进 `adjustments`（不拒绝）；超出占地时移回占地内并记进 `adjustments`。`text.body` 和大于 1 的 `count` 不起作用，`warnings` 里记一条。部件角色 `prop_block` 写在 `design_check.py` 的 `GENERIC_ONLY_ROLES` 里，`not_checked` 会写明它只跑了通用检查（占地、限高、支撑路径）；放在桌面上的块要靠下面的桌面部件接触才算有支撑路径。

参数被拒绝（`PARAM`）的情况：`size_m` 任一值小于 0.001 m、`params.label` 不在枚举里（也会被 schema 拒绝）、`z_m` 为负或块底离限高不足 0.01 m、转过以后放不进占地（信息里写出能放下的最大宽度）。

```
 side view                        plan (yaw_deg = 30)
   ______
  |      |  <- height  (size_m[2])      .----.
  | box  |                             /    /   width x depth (size_m[0], size_m[1]),
  |______|  <- z_m above the floor    '----'    turned about its centre
 =========  table top / shelf
```

## 链 A 怎么加新 kind

头顶件和三维路径构件走 `design_compile.py` 里的通用钩子，加新 kind 不用再改 `design_compile.py`。只往 `scripts/motifs.py` 的表里加条目，再改 schema 和测试：

1. 头顶件（放在限高下、排在落地件之前）：写 `builder(b, frame, el, look, note)`，登记进 `OVERHEAD_BUILDERS`。`frame` 是只读的 `motifs.Frame`（占地范围 `X0 X1 Y0 Y1`、地面高 `zf`、限高 `H`、`wall_top`、开放边和封闭边、`hanging_allowed`、`brief`，以及记进报告的 `assume`、`adjust`、`warn`）。落地件或墙面件仍然写进 `BUILDERS`，按 `Placed` 摆放；它们也拿得到同一个 `Frame`，用来把参数缺省值记进 `compile_report.json` 的 `assumed`、把调整和警告写进报告：builder 写成 `builder(b, p, scope, note, frame)` 时编译器把 `frame` 作为第五个参数传进去，写成原来的四个参数时就从 `p.frame` 取（同一个只读对象）。旧的 builder 不用改，旧 kind 的输出逐字节不变。builder 抛错时它记过的缺省值和警告和它的部件一起撤回。
2. `PARAM_CHECKS[kind] = check(el, form)`：返回 `[(元素内的路径, 信息)]`，编译器按 `PARAM` 错误报出。
3. `ROLE[kind]`：颜色角色（`dominant`、`secondary`、`accent` 或 `None`），`design_compile.ROLE` 里没有这个 kind 时才查它。
4. `SCOPE[kind]`、需要时 `DEFAULT_SIZE[kind]`，以及 `KINDS`（放进 `WALL_KINDS`、`OVERHEAD_KINDS` 或 `FLOOR_KINDS`）。
   新 kind 的部件角色还没有专门的设计检查时，把角色名加进 `scripts/design_check.py` 的 `GENERIC_ONLY_ROLES`，设计检查就会在 `not_checked` 里写明这些部件只跑了通用检查（占地、限高、支撑路径）。
5. `schemas/booth-design.v1.json`：`kind` 枚举和 `params` 里新增的字段。
6. 一个元素的所有部件由编译器统一设成同一个 `group`（builder 里用 `b.grouped(...)` 时子组只能写成部件 id 的前缀，反正会被改成元素 id）；缺省值经 builder 拿到的 `Frame` 的 `assume` 记进 `assumed`，夹掉的尺寸（例如高度超过限高）用 `Frame.adjust` 记，不悄悄削；`PARAM_CHECKS` 拿到的 `form` 带 `platform_height_m`（有 `--fit` 时是拟合的地面高度）；只用现有基元，不改 `components.py` 和 `geometry.py`；`id` 和 `kind` 就够的最小元素也必须能编译（缺的值用缺省并记进 `assumed`）。
7. 落地件的摆放约定（`lit_panel`、`prop_block` 用，以后的家族照此办理）：
   - PARAM 检查拿到的 `form` 除了 `platform_height_m`，还带 `wall_thickness_m`（`layout.wall_thickness_m`，没写是编译器的缺省 0.10 m）和 `entrance_side`（编译器实际用的入口边：`layout.entrance_side` 在开放边里就用它，否则取按 front、back、left、right 排序的第一个开放边，没有开放边是 front）。检查要和构建同一套算法：校验通过的设计构建时不丢，校验拒绝的设计绕过校验也建不出来。
   - 自己摆放的 kind 写进 `motifs.SELF_PLACING_KINDS`（集合常量）。`place_floor` 对它们不夹、不记，把请求的位置原样交给 builder（靠边的件，`along` 也不先按没转的宽度夹），靠边的件另带 `Placed.span`；其余 kind 的输出不受影响。
   - builder 从请求的位置按转后的外廓**只夹一次、只记一次**（共用的 `_place_once`，经 `Frame.adjust` 记 `moved inside the footprint from (请求位置) to (实际位置)`）；builder 不得读写编译器的内部状态（报告列表、`place_floor` 的记录）。
   - 靠边的件的空间：沿边在两道端墙之间，垂直于边在所靠的墙和对面的墙（封闭时）的内表面之内，开放的一侧以占地边为界；放不进（校验给最大尺寸，builder 丢掉并给同样的理由）就不建，不得居中后伸进墙。居中摆放的件只夹在占地内，和旧 kind 一致。
   - 绕过校验直接构建时，builder 对自己的参数（label、`z_m`、尺寸下限）给出和校验一致的拒绝；builder 读元素本身而不是 `size_of` 补过缺省的尺寸，因为编译器会把 0 或负的尺寸悄悄换成缺省。
8. `material.pattern` 和 `style.floor_pattern` 由编译器原样复制到非文字、非自发光的部件和地面（空对象 `{}` 也照抄，由 `validate_spec` 拒绝），不用在 builder 里处理。注意：顶框的部件是 `profile_extrusion`，整体绕 X 轴转了 -90 度，复制来的图案在物体自己的轴上计算，`coordinates: "auto"` 的格网在顶框上躺在平面里（沿平面的两个方向），不是竖在立面上。带 `end_radius_m` 的顶框要留意：端头件的图案方向和框的其他件不同。两个端头件的轮廓画在竖向剖面里，不转平（旋转是 0 度或绕 Z 轴 90 度），`coordinates: "auto"` 的格网在它们身上竖在立面里，而框的其他件是躺平的；所以同一个 `material.pattern` 在端头件和相邻的框上走向不同，接缝处纹路对不上。需要纹路连续时，不要给这样的顶框加方向性图案，或者先看预览。
