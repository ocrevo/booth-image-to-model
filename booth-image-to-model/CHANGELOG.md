# 3.1.0-rc7 — 2026-10-24

- 设计路线的新元素 `prop_block`（POC 工作包 P7-fam-propblock）：一件物品一个尺寸和位置都对的占位块（一个 `box`），`params.label` 是封闭枚举（laptop、tablet、monitor、phone、headphone、plant、robot_arm、robot、other），写进部件的 `evidence.note`；`size_m` 给外框，`z_m` 让它坐在桌面或层板上，`yaw_deg` 是绝对转角。缺省值（0.4 m 立方、label other、z_m 0）记进 `assumed`；高过限高就削到限高并记进 `adjustments`；几毫米厚的块（平放的手机、平板）只要块底到限高还有 0.01 m 就建出来。按决策人的决定，桌上的小件电子产品、耳机和支架、盆栽、机械臂和机器人用它代替，只按位置和尺寸算。
- 自己摆放的件（`motifs.SELF_PLACING_KINDS`：`lit_panel`、`prop_block`）：`place_floor` 不再先按没转的宽度夹、不再记录，把请求的位置原样交给 builder；靠边摆放时一起交出封闭墙内表面围起来的空间（`Placed.span`）。builder 按转后的外廓只夹一次、用 `Frame.adjust` 只记一次；靠边的件不伸进封闭墙、开放一侧不伸出占地，放不进就拒绝；居中的件和 `box` 一样只夹在占地内。
- 校验和构建一致：PARAM 检查拿到的 `form` 多带 `wall_thickness_m` 和编译器实际用的入口边 `entrance_side`，转后放不放得进、靠边截宽、不写 `yaw_deg` 的居中墙板朝向都按构建的同一套规则算；校验通过的设计构建时不丢（没写高度的凹龛检查除外），校验拒绝的设计构建也建不出，拒绝时给出能放下的最大尺寸。`design_compile.py` 只改了这两处，已有元素的输出逐字节不变（测试钉住模板和全部旧元素的哈希）。
- 接手 litpanel 的收尾：转过的 `lit_panel` 放不进时在校验时就拒绝；凹龛按建出来的 12 段折线圆弧判；参数扫描测试覆盖四种展台、入口、墙厚和地台并构建全部合法设计；canopy 缺省 `z_m` 向内取整有测试守着；`params.groove_depth_m` 的说明按 kind 分别写。
- 已有 `lit_panel` 设计的输出变化：转过的板靠边时只按转后外廓夹一次（位置可能和以前不同），靠边的板原来被悄悄挪动的那一步现在有记录，靠边的板不再伸进封闭墙；少数以前校验通过、构建时被丢的设计现在在校验时拒绝。
- 为什么：POC 的本地客户渲染图里有桌上的小件物品和盆栽这类道具；按决策人的决定这些用尺寸和位置都对的块代替。证据：两行证据核对都已通过，其中一行第三轮才通过（前两轮分别发现一件小物品被相邻的块挡住，于是允许另出只删挡住它的块的补充图；几件很小的物品没有各自的块，按最保守的理解每件都要有）。代码验证六轮，每轮都找到更窄的'校验和构建不一致'或'记录不对'的情形，都已修好并有测试。
- 已知限制：没写高度的 `lit_panel` 在 `z_m` 离限高不到 0.2 m 时校验仍会放行、构建时丢掉（litpanel 带来的，留给链 A 的下一个包）；builder 丢掉过大件时的理由里没有能放下的最大尺寸；极厚的板给出的最大宽度可能小于带槽墙板的最小宽度；闭环的家族目录还没有 `prop_block` 的说明行，`box` 那一行仍写着'道具的占位块'。
- 测试：`tests/test_design_compile.py`、`tests/test_design_check.py` 新增的测试。不带 Blender 的全部 skill 单元测试 597 个通过（16 个跳过，与基线相同）；带 Blender 的回归留给 POC 的夜间队列。

# 3.1.0-rc6 — 2026-10-23

- 设计路线的新元素 `lit_panel`（POC 工作包 P7-fam-litpanel）：圆角墙板，四周内嵌一条发光灯槽。槽沿板的轮廓（含上面两个圆角）走成倒 U 到板底，槽底一层发光带，前表面在板面之后 `groove_depth_m`；参数 `corner_radius_m`、`inset_m`、`groove_width_m`、`groove_depth_m`、`light_hex`。可选 `niche`：板上有底、有后壁、不穿透的凹龛，开口上两角圆、下边方（`corner_radius_m` 等于半宽时是拱形），居中，`bottom_m` 从板底算，四周一圈和板面齐平的发光圈（`ring_width_m`）。整块板由同一组 `profile_extrusion` 拼成，不开洞，各段体积之和等于板减去槽和龛，没有缝和重叠。像 `partition` 一样放置；高度超出限高时截短并记进 `compile_report`，转过的板移进占地并记录，比占地还宽的板拒绝。缺省值逐个记进 `assumed`（宽、厚、高记进 `choices`）。发光强度是常数 1.5。
- 接手 P7-fam-canopy 的三处：`z_m` 下限用和上限一样的容差，缺省 `z_m` 向内取整后记录（读回再校验能通过）；端头件对齐框格时半步取整的平局改用共用的 `_grid5`；recipes 写明端头件上的图案方向和框件不同。
- 已有元素的输出逐字节不变（测试钉住模板和全部旧元素的哈希）；`canopy_frame` 只在取整平局处差 0.01 mm。
- 为什么：POC 需要四周内嵌发光灯槽的墙板和带一圈灯的有底拱形凹龛；以前只能在墙外贴一根手算控制点的凸条，凹龛只能切穿整板再在后面另放一块板。证据：灯槽和凹龛两行各一次独立核对通过；灯槽在原图大小的主预览里很窄，另出了一张同一几何、只收窄相机的近景。代码一轮验证通过（白名单、先红后绿、变异和破坏性输入、独立的体积和几何抽样、契约）。
- 已知限制：没写高度时，和高度有关的凹龛检查推迟到构建，放不下的凹龛在构建时连同理由进 `dropped`；转过的板宽度检查没算转角，非常大的圆角在按 12 段折线建出来时可能自交，这两种也是构建时连同理由丢掉（留给链 A 的下一个包）；`params.groove_depth_m` 的说明目前只写了 `lit_panel` 的含义；闭环的家族目录还没有 `lit_panel` 的说明行；含 `lit_panel` 的设计编译要多 0.2 到 1.5 秒（已有的轮廓三角化在每个部件上跑多次），不含它的设计不变慢。
- 测试：`tests/test_design_compile.py`、`tests/test_design_check.py` 新增 16 个测试。不带 Blender 的全部 skill 单元测试 546 个通过（16 个跳过，与基线相同）；带 Blender 的回归留给 POC 的夜间队列。

# 3.1.0-rc5 — 2026-10-23

- 两种画在部件本身上的材质图案（POC 工作包 P7-mat-pattern），都是表面颜色，不是实体孔洞，不用外部图片。`material.pattern` 的 `type=dots`：`spacing`、`radius_m`（必须小于较小间距的一半）、`dot_color`，圆点成行列排在格子中心；可选 `coordinates`（新增 `box`：按每个着色点物体空间法线绝对值最大的轴选平面，并列时先 x 后 y，同一部件的立面和底面都铺得上圆点）、`offset`、`arc_origin_deg`（只配 `arc_length_z`）、`fade`（沿物体坐标的一根轴，半径从 `from_m` 处的全尺寸线性缩到 `to_m` 处的 0，`from_m` 一侧之外全尺寸，`to_m` 一侧之外没有圆点）。`type=grain`：`scale_m`、`strength`（0 到 1）、`grain_color`，三维噪声把部件颜色和颗粒色按 `strength` 混合；地面经设计的 `style.floor_pattern` 使用。
- 图案按类型分发：`material_patterns.PATTERNS` 和节点签名 `SIGNATURES`（grid、projected、dots、grain），构建和 `validate_spec.py` 都经它。`blender_verify.py` 重开 .blend 后按类型重建并比对签名；读 `build_report.json` 的 `material_set` 比对投影版（UV Project 修改器算预期的修改器，跳过的文字应保持隐藏，不认识的 `material_set` 报错）；没有图案的部件不得带图案节点。
- 接手 P7-render-dark 的两处：`look_unhandled` 有了端到端测试；data-contract 里 `dark_hall` 拒绝 Workbench 的措辞改准（开着 `--preview-client-look` 时这一遍走 Cycles，不拒绝）。
- `grid` 的参数函数、节点函数和签名逐字不变，已有 spec 的构建结果不变。
- 为什么：POC 需要带颗粒的地毯和成行、沿长度渐渐消失的穿孔圆点；以前只有均匀单色和网格线，圆点区还得另放一层薄片。证据：地毯颗粒一行第一次核对通过。圆点一行第一次核对判 pass，但判据比原图宽松；判据按原图改严后重出证据，第二次核对通过（第一次的结果留档）。代码一轮验证通过（白名单、先红后绿、变异和破坏性输入、契约）。
- 已知限制：一个图案只有一根渐变轴，所以同一元素上与渐变轴平行的那一面不能渐变（整面满点）；`canopy_frame` 的部件绕 X 转过，物体坐标的 y 竖直向下，渐变轴要按物体坐标选；`box` 在弧面上于 45° 处换平面，对角附近的圆点会拉长；两段顶框相接处的圆点列会挨得很近；圆点是规则格子，不是不规则的穿孔排布；网页编辑器缩放时只跟着缩 `grid` 的字段，dots 的 `radius_m`、`fade` 和 grain 的 `scale_m` 不缩（缩得太小时 spec 无效，构建会明确拒绝）；`GRAIN_CONTRAST` 只在代码注释里说明。
- 测试：`tests/test_material_patterns.py` 新增 22 个测试。不带 Blender 的全部 skill 单元测试 552 个通过（16 个跳过，与基线相同）；带 Blender 的回归留给 POC 的夜间队列。

# 3.1.0-rc4 — 2026-10-23

- 设计路线的新元素 `canopy_frame`（POC 工作包 P7-fam-canopy）：空心圆角顶框，`params.band_m` 四边带宽各设（内孔可以偏心），`corner_radius_m` 外圆角；`params.sign` 在一个角上接一段更深的标识楣板（`corner`、`along_m` 两条边上的长度、`drop_m` 加深量、`text_chars` 占位字个数、`end_radius_m` 端头从框底弯下到楣板底的凸圆半径，缺省 0 为方端）。框和楣板由同一圈轮廓切成的几段 `profile_extrusion` 组成，外立面共面、切面共享、端头件对齐到框的网格，没有缝和重叠；楣板切口只落在直边上（不在圆角弧上），校验时就拒绝放不下的参数（PARAM，给出真实范围）。没写 `hanging_allowed` 时在四角立柱，立柱停在它上方最低一块的底面。占位字由编译器生成，不抄需求或原图里的字母，来源记为 choice。只有 `id` 和 `kind` 的元素也能编译，缺省值逐个记进 `assumed`（`end_radius_m` 0 是唯一不记的）。
- 链 A 的通用钩子（`design_compile.py` 只改这一次）：`motifs.OVERHEAD_BUILDERS`（头顶件，和 `header_band`、`ring` 一起排在落地件之前）、`PARAM_CHECKS`、`ROLE`；落地件和墙面件的 `BUILDERS` 也拿得到只读的 `Frame`（第五个参数或 `placed.frame`），能把缺省值、调整和警告写进 `compile_report`，builder 失败时回滚。设计元素的 `material.pattern` 和 `style.floor_pattern` 原样复制进 spec，由 `validate_spec` 校验。用 `--fit` 时校验按拟合的地面高度算。
- 已有的元素输出逐字节不变（测试钉住模板和全部旧元素的哈希）。
- 为什么：POC 需要四边带宽不同、内孔偏心、一角接一段更深的标识楣板且端头圆顺弯下的顶框；以前只能用内孔居中、四边同宽的 `rounded_ring` 加另拼的一块。证据经三次独立核对才通过：第一次证据设计的带宽与用拟合相机实测的不符；第二次楣板端头是方台阶（于是加了 `end_radius_m`）；第三次通过。代码经六轮验证（白名单、先红后绿、变异和破坏性输入、契约）。
- 已知限制：占地尺寸超过 4 位小数时，校验和构建对外缘能否放下的判断可能不一致（构建按 4 位小数）；楣板比外圆角半径还短时表达不了；带 `end_radius_m` 的顶框加图案时，端头件的图案方向和框件不同。
- 测试：`tests/test_design_compile.py`、`tests/test_design_check.py` 新增的测试。不带 Blender 的全部 skill 单元测试 530 个通过（16 个跳过，与基线相同）；带 Blender 的回归留给 POC 的夜间队列。

# 3.1.0-rc3 — 2026-10-23

- 暗场预览（POC 工作包 P7-render-dark）：`boothctl.py preview --look neutral|dark_hall`（缺省 `neutral`，命令行和输出与以前逐字节相同）。`dark_hall` 是深灰世界（约 0.02）、一块深色微反射的地面板（约 0.05，粗糙度 0.7）、台架灯降到原能量的一个固定比例；发光部件（`emission` 大于 0，含发光文字）的发光强度乘一个固定倍数，由 Cycles 把发光网格当光源照亮周围的地面和墙，光只从部件真实的发光面发出；`role` 为 `spotlight` 的部件另派生一盏向下的聚光灯（固定锥角，强度按 `emission` 和灯体面积换算），派生灯不写进 spec。
- `dark_hall` 只用于预览：不配 `--preview` 时 `blender_build.py` 拒绝（地面板和派生灯是台架对象，过不了 `blender_verify`）；配 Workbench 时拒绝（Workbench 不画灯光），没给 `--engine` 时用 Cycles；预览采样上限在 `dark_hall` 下提高到 32（中性版仍是 8），要在命令里写 `--samples 32` 才用得上。`build_report.json` 记 `look`、`emissive_parts`、`derived_lights`、`look_unhandled`（没处理的部件和原因）、`preview_samples`；暗场预览的 `compare.json` 写明 `look`，它的展台像素比例和建议曝光与中性版不可比。打分只用中性版预览。
- 为什么：发光件照亮周围地面和墙面的效果（"亮地面"、墙上的光扇），以前只能做出发光细条本身。第一版按"在包围盒最大的一对面上放面光"做，验证发现空心发光环会凭空多出一块铺满内孔的发光天花板，贴墙贴地的灯带会把灯放进墙里地里，于是改成由真实发光面照亮。两轮代码验证（白名单、先红后绿、变异和破坏性输入、契约）和一次 Cycles 证据核对通过：证据图里发光件下方的地面光晕、发光边周围的墙面和筒灯在墙上的扇形光斑，都和中性版对照确认是光照照出来的。
- 已知限制：发光倍数、聚光灯强度系数和采样上限是第一次的估计值，证据图里看着合适；光斑形状不逐像素对齐原图。
- 测试：`tests/test_material_patterns.py` 新增的测试用假 bpy 端到端跑 `blender_build.main()`（世界、地面板、台架灯比例、发光倍数、采样上限、报告字段）。不带 Blender 的全部 skill 单元测试 491 个通过（16 个跳过，与基线相同）；带 Blender 的回归留给 POC 的夜间队列。

# 3.1.0-rc2 — 2026-10-23

- 投影版材质（POC 工作包 P7-mat-projected）：新图案类型 `{type: projected}`，把 spec 的原图按拟合相机投到模型上，只用于和原图对比还原度。每个网格部件加一个以 spec 相机为投影器的 UV Project 修改器，Image Texture 节点接 `spec.source` 的图片；部件颜色的 alpha 照抄（玻璃仍半透明）。文字部件先转成网格再同样投影；转不了的隐藏，并以 `{id, reason}` 列进 `build_report.json` 的 `projected_uv_skipped`。背面和被遮挡面得到错误像素，第二视角里会拉伸；投影器就是渲染相机，所以正视角里看不出透明。
- `blender_build.py --material-set neutral|projected`（缺省 `neutral`）：`material_patterns.material_set(spec, name)` 在内存里给出改写后的副本，run 文件夹里的 `spec.json` 仍是原样；`build_report.json` 记 `material_set`。投影的准备（加载原图、UV 层、修改器）按副本里有没有 `projected` 部件决定，所以 spec 文件自己写了 `projected` 的部件在缺省构建下也能建。`projected` 要求相机已拟合（`camera.fit_status` 不是 `unfitted`），否则 `validate_spec` 报错。
- `boothctl.py preview --material-set`：取 `projected` 或 spec 里有投影部件时，没给 `--engine` 就用 Cycles（Workbench 只显示平色），明确给 `workbench` 时在启动 Blender 前拒绝（含 `--client-look`）。缺省 `neutral` 时传给 Blender 的命令行与以前逐字节相同。
- 图案按类型分发：`material_patterns.PATTERNS = {type: (参数函数, 节点函数)}`，`validate_spec` 和 `blender_build` 都经它分发；`grid` 的节点签名不变；未知类型的错误信息列出已知类型。schema 的 `pattern` 改为 `grid` 和 `projected` 两个分支（`oneOf`）；已有 spec 的校验结果不变。`references/data-contract.md` 写上 `projected` 和 `--material-set`。
- 已知限制：投影版构建过不了 `blender_verify.py`（文字变成网格、多了修改器），交付和复核用中性版；独立复核怎样对待投影版留给后续的图案工作包。
- 为什么：POC 决定用两套材质（中性版和投影版），投影版让面板在同一视角下对比外观。三轮代码验证（白名单、先红后绿、变异和破坏性输入、契约）和一次 Cycles 证据核对通过；证据的前两次核对没过（一次是判据本身在投影版里看不出来，已删；一次是文字部件成了深色平块，已修）。
- 测试：`tests/test_material_patterns.py` 新增 30 个测试（含用假 bpy 端到端跑 `blender_build.main()`）。不带 Blender 的全部 skill 单元测试 472 个通过（16 个跳过，与基线相同）；带 Blender 的回归留给 POC 的夜间队列。

# 3.1.0-rc1 — 2026-10-22

- `design-compile` 接受拟合相机（POC 工作包 P7-fit-camera）：新参数 `--fit <box_fit.json>` 和 `--image <图片>`，两个同时给或都不给，只给一个时退出码 2。有 `--fit` 时：只接受 `status` 为 `fitted` 的拟合，图片必须能打开且尺寸与 `fit.image` 一致，否则退出码 2、不写任何文件；相机从拟合按 spec 的相机字段原样复制（含 `fit_status`），不再自动生成主相机；`form.footprint_m` 在设计校验之前被拟合的 W、D 覆盖（超出 0.5 到 60 m 报 `design_invalid`），不确定项加一条 `footprint: fitted`，`scale.basis` 记为 `single_image_estimate`；地面高度取 `fit.floor_z`（`layout.platform_height_m` 在这种模式下不起作用）；spec 的 source 是复制进 `--out` 的原图，尺寸取 `fit.image`，不写 `design_canvas.png`；`compile_report.json` 多一个 `fit` 字段（路径和状态）。有 `--fit` 时 `--canvas` 不起作用。`design_compile.py` 自己的命令行也有这两个参数。
- 不带这两个参数时输出逐字节不变：测试把基线代码在模板设计上编出的 `spec.json`、`compile_report.json`、`design_context.json` 的哈希写成常量；验证时在基线提交上独立复算一致，默认 1280 × 800 画布的命令行输出在基线和本版之间也逐字节相同。
- 为什么：设计路线的 spec 以前只能对着一张灰画布，不能和原图叠图打分；POC 的闭环（读图器填 design，代码编译）需要它按原图尺寸和拟合相机渲染。用一个合成场景的拟合结果核对过：`validate_spec.py --check-source` 通过，相机与拟合逐字段相同。
- SKILL.md 设计路线第 4 步补一句 `--fit`、`--image` 的用法。
- 测试：`tests/test_design_compile.py` 新增五个测试。不带 Blender 的全部 skill 单元测试 442 个通过（16 个跳过，与基线相同）；带 Blender 的回归留给 POC 的夜间队列。

# 3.0.0-rc1 — 2026-10-20

- **产品方向变更，新增设计路线（预览版）**：设计师给一张参考图和一段需求，生成一个全新的展位模型，而不是还原这张图。参考图提供风格（配色、材质、气质）和结构起点（展位类型、开口面、构成）；需求决定增、删、改什么；表单里的长 × 宽和限高是事实，模型不得改动。图里不取任何尺寸，所以这条路线不用角点拟合和场景描述。原有的还原路线（第 0–3 节、`draft_compile.py`、`box_fit.py` 等）一字未改。
- 新 schema `schemas/booth-design.v1.json`（design.json）：需求原文、表单、展位类型（须与开口面一致）、调色板三色（主色、辅色、强调色）、布局（墙高、墙厚、地台高、入口）、元素清单、需求清单。元素用词语定位：`side`（front/back/left/right/centre）、`along`（0..1）、`offset_m`，或直接给 `centre_m`；尺寸 `size_m` 留空就取默认值。每个元素都标来源：`brief`（设计师说的）、`reference`（从图里取的）、`prior`（通用默认）、`choice`（设计决定），取代原来的“不得编造”一条。
- 新编译器 `scripts/design_compile.py`（命令 `boothctl.py design-compile --design --out [--canvas 1280x800]`）：只在封闭面上建墙（岛型不建）；开口面上的墙面类元素丢弃并说明原因；元素钳进占地和限高之内，平面重叠只报警告；从入口外侧、视高 1.6 m、焦距 35 mm 自动生成三分之二视角的主相机，距离由二分搜索确定，使整个展位入画；按调色板角色和表面（matte、satin、gloss、metal、glass、wood、fabric、自发光）配材质；写一张中性灰画布当作 spec 的 source（渲染器需要，不是照片）。文字只在 `provenance.text` 为 `brief` 且原文确实出现在需求里时才生成，否则拒绝编译（代码规则，不靠提示词）。每个部件都是 `inferred`，备注写明来源；默认值和设计决定分别记进 spec 的不确定项。编译后自动跑 `validate_spec`、`design_check`（上下文取自表单）和基础需求对照（`must_have` 含所在面、`must_not`、`count`、`text`）。全部成功之后才写出 `spec.json`、`design_canvas.png`、`compile_report.json`、`design_context.json`。
- 新元素库 `scripts/motifs.py`，基于现有基元和宏，`components.py` 未改：`feature_wall`（板条或整板）、`graphic_panel`（可自发光成灯箱，可带需求文字）、`tv`（四面墙都可）、`led_wall`、`wall`（改周边墙的颜色或高度）、`header_band`（沿所选各边的门头，自由端自动加立柱，入口一侧外立面可放需求文字，可自发光）、`ring`（吊挂环；没写允许吊挂时自动加四根立柱）、`spotlights`（挂在门头或吊环下）、`counter`（可悬浮底座，正面可带文字）、`round_table`、`chair`、`seating_group`、`planter`、`display_plinth`、`storage_block`、`partition`、`box`。每个元素的所有部件共用一个编辑分组（元素 id）。
- 新指南 `prompts/reference_card.md`（读图卡片：类型、开口面、构成、三色、材质、气质；不转写图上的文字和 logo，不取尺寸）和 `prompts/booth_design.md`（优先级：表单 > 需求 > 卡片 > 默认值；来源标注、文字规则、元素词表、定位方法、构图要点、需求拆解）；示例 `templates/design_template.json`（虚构的 6 × 4 m 一面开口展位）。SKILL.md 新增“D. Design route (preview)”一节。
- 测试：新增 `tests/test_design_compile.py`。打包清单加入 `prompts/`。
- 已知限制：一句话修改目前是由 agent 另存新版本 design.json 再编译，还没有补丁和误伤报告；没有自动避让；渲染沿用还原路线的客户风格；网页工作室还不支持这条路线。

# 2.10.0-rc1 — 2026-10-19

- 展位类型和开口边先读、再当检查用（迭代 I2，建立在 I0 评测工具和 I1 诚实拟合之上）。**效果有限**：见下面的“实测结果”和“已知限制”，它只抓住一类读图错误的一部分，没有测过真实图片。
- 场景描述 1.2：新增三个必填键 `booth_type`（`inline` = 1 个开口边，`corner` = 2 个相邻的开口边，`peninsula` = 3 个，`island` = 4 个，`other` = 其他情况，`null` = 看不出来）、`open_sides`（展位占地上没有墙、观众进入的边）、`cutaway_sides`（效果图里只为展示内部而去掉墙或顶的边，不是真实开口）。边用和角点记号相同的词：`front` = −D/2，`back` = D/2，`left` = −W/2，`right` = W/2。`null` 是“未知”，不是“没有”：`open_sides` 为 `null` 时不做任何检查。`schema_version` 新文件写 `"1.2"`；旧的 `"1.1"`、`"1.0"`、`"scene-description.v1"` 仍可读，没有这三个键时 `draft_compile.upgrade_scene` 在校验前补 `null`（不改原文件）。同一条边在一个列表里写两次、或同时出现在 `open_sides` 和 `cutaway_sides`，场景被拒绝（`TOPOLOGY`）；类型和开口边数对不上不拒绝，由下面的检查报告。schema 的关键字白名单测试加入了 `uniqueItems`（各家结构化输出不都支持），所以“不重复”由校验器保证，不由 schema 保证。
- `draft_compile.py`：`compile_report.json` 新增 `report.topology` = `{booth_type, open_sides, cutaway_sides, checks: [{id, status, message}]}`（命令摘要里是同一份），四项检查按固定顺序，`status` 为 `ok`、`warn`、`fail` 或 `not_checked`：`type_matches_sides`（类型和开口边数相符；`other` 对任何开口边都相符）、`wall_on_open_side`（场景里 `type` 为 `wall` 的构件，其 `plane` 不在 `open_sides` 里）、`closed_side_without_wall`（封闭边上有 `wall`、`wall_panel`、`led_wall` 或 `tv`；没有只是 `warn`，信息性质）、`camera_side`（拟合出的相机至少在一条开口边或剖切边的外侧，即越过该边的占地边缘 0.25 m 以上；只在封闭边外侧为 `fail`，不在任何一边外侧为 `warn`）。`open_sides` 为 `null` 时四项都是 `not_checked`，质量门与此前完全相同；`booth_type` 为 `null` 时只跳过类型检查。
- 质量门：每个 `fail` 往 `reasons` 里加一句原文并把 `status` 变成 `review`：`the booth type does not match the open sides`、`a wall was placed on an open side and left out`、`the camera looks in through closed sides: front/back or left/right may be swapped`。质量门仍然只报告，不阻止编译，也不改退出码。开口边上的 `wall` 不再建出来，写进 `dropped`（`reason`: `wall on an open side`）；剖切边上的墙照建，`wall_panel`、`tv`、`led_wall` 不受这条规则影响。`camera_side` 要抓的是前后或左右记号被整体对调：这种错误让所有角点互相一致，残差照样很小，拟合看不出来，只有开口边的位置能暴露；它的边界是相机本来就在某条开口边外侧时看不出（岛型四边全开，永远看不出）。
- 旧场景文件（没有这三个键）的编译结果：在测试用的场景上核对过，`spec.json`、`box_fit.json` 逐字节相同，`compile_report.json` 只多一个 `topology` 键。
- 网页读图提示词（`web/server/prompts/read_scene.md`）：在放任何角点或构件之前，先写 `booth_type`、`open_sides`、`cutaway_sides`（紧跟 `schema_version` 和 `project_id`）；给出各类型对应的开口数、怎样分辨剖切和真实开口，看不出来就写 `null` 并在 `uncertainties` 里说明原因，不要为了让数字对上而选类型；不要在列入 `open_sides` 的边上描述 `wall`。`schema_version` 要求写 `"1.2"`。
- 网页读图的修复规则（`web/server/reader_openai.py`）：读图结果缺这三个键中的任何一个、类型和自己写的开口边数矛盾（`other` 和 `null` 不算矛盾）、同一条边写了两次、或一条边既开口又剖切，都会连同原因退回让模型重答（最多两次）。这只判断读图结果是否自相矛盾，不判断它对不对。服务器的编译步骤把 `topology` 原样传给页面（`web/server/steps.py`）。
- 工作室（界面是英文）：质量门为 `review` 时仍然暂停一键“生成并出图”，卡片上展位语法的三种失败各有一句通俗的话：展位类型和开口边对不上；开口边上的墙被省略；图似乎被前后或左右读反了，模型可能是镜像的。专业视图的质量检查增加展位布局：读图写的类型、开口边、剖切边，四项检查和各自的状态，以及每个 `fail` 或 `warn` 的原话；读图没写类型或开口边时说明没有做检查。
- 文档：SKILL.md 第 2 步改为先写展位语法，第 4 步说明 `topology` 与质量门的关系；`references/scene-description.md` 新增“展位语法”一节和四项检查表；`references/design-priors.md` 的展位类型表加上场景描述的英文取值，并新增“其他”一行；`web/README.md`、`tools/eval/README.md` 同步。
- 评测工具（`tools/eval/`，不随 skill 发布，harness 1.2.0）：新增 `grammar.py`（工具自己的展位类型规则、真值、记号互换、已编译的墙在哪条边、哪些质量门理由属于展位语法）；新的可选扰动（不在 `oracle-v1` 的默认列表里，用 `--perturbations`）：`mirror_fb`（前后记号互换）、`mirror_lr`（左右记号互换）、`swap_fb_lr`（前后和左右同时互换，相当于把展位转了半圈，完美的拟合也看不出来）、`wrong_open_sides`（一条边的开口和封闭写反，类型随之改）。每次运行默认声明案例真值的展位语法（相当于读对了的读图者）；`skill_caps.py` 检测被测的 skill 认不认得这三个键，认不得的旧版收到的是去掉三个键的场景。新的“展位语法”分层指标（按扰动、按错误种类、按真实类型）：带展位语法理由被标出的比例、任何原因被标出的比例、只靠展位语法被标出的比例、这套场景下最多能抓住的比例（上限）、对的声明被展位语法多标出的比例（误报）、四项检查各自的状态、声明为开口的边上建出的墙；`run_eval.py rescore --to` 可把重新计分的结果写到另一个目录，`synth_generate.py --grammar` 可生成带三个键的场景；套件指纹、`oracle-v1` 和旧的扰动没有变，`compare_results.py` 对 1.1.0 与 1.2.0 不再报版本不同。
- 十个问题：新增 `docs/priors-questions.md`，请所有者或设计师同事确认十个对“从一张图估算宽、深、高”影响最大的数值（每题三选一：同意、不同意、不知道，各附出处）。数值全部照抄 `knowledge/research/` 里已有的整理稿，没有新增或修改；在有人点头之前，没有任何数值成为默认值，代码里也没有任何功能在用它们。
- 测试：skill 新增 `tests/test_topology.py`，更新 `tests/test_draft_compile.py`、`tests/test_quality_gate.py`；网页端更新 `web/tests/test_reader.py`、`web/tests/test_studio_gate.py`、`web/tests/studio_logic.test.mjs`、`web/tests/mock_openai.py`、`web/tests/scene_fixture.py`、`web/tests/make_studio_fixtures.py`；评测工具新增 `tools/eval/tests/test_grammar.py`、`test_metrics_grammar.py`、`test_perturb_grammar.py`、`test_run_eval_grammar.py`，并更新已有测试。测试数以最近一次回归为准：见 `validation/test_results.json`。
- 实测结果（只来自合成数据：套件 `oracle-v1`，种子 20261019，32 个场景；展位语法的四种扰动共 128 次运行，只编译，与提交 `b377476` 的冻结基线 2.8.0-rc1 按场景配对；默认 288 次运行与 I1 的最终结果配对；对比文件在 `output/_eval/`，不入库）。事先定了四条标准：
  - (1) 前后或左右标反、开口说错的运行里，质量门标出至少 80%。按“任何原因被标出”算：`mirror_fb` 100%（基线 100%）、`mirror_lr` 100%（基线 100%）、`wrong_open_sides` 50.0%（基线 3.1%）；三种合并是 83.3%，字面上达到，但**这条标准措辞偏松**：两种镜像本来就被拟合抓住（残差 50–146 px，基线也一样），展位语法在那里的贡献是 0，合并的比例是被它们拉高的；按种类看，`wrong_open_sides` 的 50.0% 低于 80%，**没有达到**。真正的增益是 `wrong_open_sides` 从 3.1% 到 50.0%，其中只靠展位语法多抓到的是 40.6 个百分点（95% 区间 [25.0, 59.4]）。
  - (2) 对的输入不增加误报：输入有效却被判 `review` 的比例 14.1% → 14.1%，没有变化；默认套件上所有准确度中位数与 I1 终版相同。达到。编译路径平均耗时测得 7.97 s → 8.91 s（差 +0.93 s，区间 [0.61, 1.27]），原因没有查明（展位语法检查本身很便宜；两次运行时机器负载可能不同），所以不据此说变快或变慢。
  - (3) 声明为开口的边上建出的墙：基线占 128 次运行的 10.2%（`wrong_open_sides` 里是 40.6%）→ 现在各处都是 0%。达到。
  - (4) 旧场景文件的编译结果不变、单元测试通过：见上面的“旧场景文件”一条和 `validation/test_results.json`。
  - 追加的第四种扰动 `swap_fb_lr`（不在事先定的三种里，在第一轮发现后加的）：任何原因被标出 3.1% → 46.9%（32 个场景中的 15 个），其中带展位语法理由的 40.6%（13 个），只靠展位语法多抓到 37.5 个百分点（区间 [21.9, 53.1]）。在这套场景里 `camera_side` 最多只能抓 13 个（岛型永远抓不到，多数半岛的相机仍在开口外侧）。
  - `wrong_open_sides` 分开看：把封闭边说成开口，场景画了那面墙时能抓住（32 个场景中的 13 个，全部由 `wall_on_open_side` 抓住）；把开口边说成封闭，场景仍然自洽，只有相机检查抓住了 19 个中的 1 个。
  - 单独的前后或左右标反：拟合本来就标出（残差 50–146 px），基线也一样。
  - 总的来说：开口说错约一半能抓住，前后左右一起标反约 41% 能抓住（带展位语法理由；算上其他原因是 46.9%），在合成数据上没有增加误报。
- 已知限制：(1) 只在合成数据上测过。合成相机总在某条开口边的外侧（至少 0.6 m 余量），所以 `camera_side` 的误报只在这样的相机上测过；从展厅里面、从上方，或隔着剖切边往里看的真实图片可能不同，没有测。(2) 生成器的 `inline` 场景有三个开口边（在这套词汇里是半岛），所以套件里没有单开口展位，也没有剖切场景：单开口的类型规则和剖切边的放行规则没有被测试数据用到。(3) 真实的读图器可能漏画被遮挡的墙，所以 `closed_side_without_wall` 只作提示，不进质量门。(4) 检查只能发现读数和自己矛盾，判断不了一个自洽的读数对不对：例如把开口边说成封闭边，场景仍然自洽，多数情况下抓不到。(5) 没有用真实的看图模型或真实图片测过：测试里的“读图者”是给定真值再人为加错，不是从图上读出来的。(6) 编译耗时的 +0.93 s 原因未查明。

- 相机拟合更诚实（迭代 I1，建立在迭代 I0 的评测工具之上）：`box_fit.py` 改用鲁棒损失（`soft_l1`，膝点 `F_SCALE_PX` = 2 px；`--loss linear` 可退回普通最小二乘）：先用普通最小二乘的多起点找到盆地，再用鲁棒损失精修最好的 3 个解，一个点错的角不再把相机拖偏。
- 默认的独立检查改为**逐点留一法**（leave-one-out）：每个参与拟合的角依次拿掉，用其余的角预测它，写进 `loo_reliable`、`loo_errors_px`、`loo_median_px`、`loo_max_px`、`loo_inflation`；其余的角预测不了的点名进 `outliers`（逐个点名：点出嫌疑后拿掉它，再检查其余的，免得被它带偏的无辜点一起被点名），并从最终相机里剔除（`dropped`，`--keep-outliers` 可保留）。点名需要点数条件 `LOO_MARGIN` = 5：拿掉一个角后剩下的方程数要不少于未知数个数加 5，否则 `loo_reliable` 为 `false`、`loo_*` 为 `null`、`outliers` 为空，宁可不报，也不报猜出来的名字（给定一个尺寸时需要 8 个参与拟合的角；这次拟合的具体个数是 `limits.loo_min_fit`）。
- 可选的检查角：点上的 `fit: false` 只用拟合出的相机投影、不参与拟合，误差在 `check_points`、`check_median_px`、`check_max_px`。每个检查角都少一个拟合点，所以只在角点 11 个以上或想自己指定留出点时用；SKILL.md 第 3 步的默认是 8–10 个角**全部**参与拟合，不再建议留 2–4 个角不参与拟合。检查角用到没有被拟合点约束的尺寸或 `?名字` 时跳过并给警告。
- `box_fit.py` 输出：新增状态 `check_failed`（拟合复现得了自己的点，却预测不了独立的点）；像素上限写进输出的 `limits`（长边 1600 px 以内不变，更大的图线性放宽，从不收紧），调用方不再自设一套；标准输出改为只打印**一个** JSON（与 `--output` 相同，警告和 `hint` 都在里面；`hint` 是“重新点一下”这类建议，`ambiguity_hint` 只放真正的歧义提示）；退出码 0 = `fitted`，3 = 需要处理（`high_residual`、`ambiguous`、`check_failed`），2 = `blocked`（JSON 里有 `reason`）。尺度核对（`scale_checks`）的结果改为结构化列表（`id`、`expected_m`、`measured_m`、`ratio`、`tolerance`、`ok`，量不出来的 `ok` 为 `false` 并带 `error`）。
- 场景描述 v1.1：`corner_points[].fit`（必填，`null` 或 `true` = 参与拟合，`false` = 检查角）；`schema_version` 新文件写 `"1.1"`，旧的 `"1.0"` 和 `"scene-description.v1"` 仍可读，旧文件缺 `fit` 时 `draft_compile.upgrade_scene` 在校验前补 `null`（不改原文件）。
- `draft_compile.py`：新增 `quality_gate` = `{status: pass | review, reasons}`，写进 `compile_report.json` 和命令摘要。`review` 的条件：拟合状态不是 `fitted`、点名了出错的角、检查或留一法的误差超过 `box_fit` 报告的 `limits`（质量门不另设阈值）、`scale_checks` 有没通过的项、或根本没有独立信号（角点太少、留一法算不了、又没有检查角）。质量门只读拟合结果，不阻止编译，也不改退出码。`report.fit` 带上检查的数字（`n_fit`、`n_check`、`loo_*`、`outliers`、`dropped`、`scale_checks`、`limits`、`hint`），摘要里是 `fit_check`；`scale.notes` 记一句独立检查的结果；检查角和被剔除的点在地面 landmark 里标 `fit=false`（预览报告里算 `check`）。
- `overlay.py`：被拟合的角画绿圈，检查点（`fit: false`）画橙色方框，被点名的 `outliers` 再加一圈品红色环；检查点不计入 `max_point_err_px`（另报 `check_points`、`max_check_err_px`），图上加一行图例。
- 网页读图提示词（`web/server/prompts/read_scene.md`）：只假设**一个**尺寸（最好是高度，宽和深由角点算出来），已在图上标出的尺寸才写进 `known`；要 8–10 个角点，全部参与拟合（`fit` 每点写 `null`），分散在整个展台，少于 6 个或缺 `fit` 的读数会被退回修正；`scale_checks` 用于看得见的桌子、柜台、椅子，检验那个假设的尺寸；发给模型的网格图四边都有像素刻度，图内每隔三个格线交点标 `x,y` 和小十字。
- 工作室：质量门为 `review` 时暂停一键“生成并出图”（模型建好后、渲染之前停下），卡片用平实的话说明原因，给出 **Render anyway** 和 **Start again**；服务器不拦，只把 `quality_gate` 和背后的数字原样传给页面；专业视图增加质量检查、逐点留一法误差和检查角标记。
- 新的评测工具 `tools/eval/`（不随 skill 发布）：合成场景生成器带精确真值（`synth_generate.py`，套件 `oracle-v1`，32 个场景，固定种子可逐字节复现）；`run_eval.py` 的 `oracle` 模式（真值角点加上读图者错误的模型：像素噪声、错位的角、互换的标签、假设了全部尺寸；不用模型、不用 Blender、不联网）、`replay`（重新编译保存下来的读图结果）、`rescore`，`live` 尚未实现；`compare_results.py` 与固定基线按场景配对并给出 95% 区间；基线与结果文档 `BASELINE.md`、`RESULTS-I1.md`、`RESULTS-I1b-arms.md`。CI 增加这套测试（`python -m unittest discover -s tools/eval/tests`）。
- 测试：新增 `tests/test_box_fit_check.py`、`tests/test_quality_gate.py`，更新 `tests/test_draft_compile.py`、`tests/test_fast_path.py`；网页端新增 `web/tests/test_studio_gate.py`、`web/tests/scene_fixture.py`，更新读图、服务器、加固和工作室逻辑测试。测试数以最近一次回归为准：见 `validation/test_results.json`。
- 评测结果（只来自合成数据：完美针孔相机拍的、由我们自己的构件搭成的盒子，角点 = 真值加一个读图者错误的模型；套件 `oracle-v1`，种子 20261019，32 个场景，每个方案 288 次运行，与提交 `b377476` 的冻结基线按场景配对）。全部运行的中位数，基线 → 现在：角点误差 0.34% → 0.10%（占对角线，差 −0.25% [−0.28, −0.20]）；相机旋转误差 1.26° → 0.40°；视场角误差 0.96° → 0.38°；|ln W/H| 0.0113 → 0.00252；|ln D/H| 0.0152 → 0.00308；落地构件位置误差 0.0752 → 0.0251 m。一个被拉偏 30 px 的拟合点：点数 ≥ 8 时 30 次里点对 29 次，角点误差 0.41% → 0.04%，旋转误差 0.89° → 0.09°。干净输入和 1–3 px 噪声：准确度没有明确变化（区间都含零；3 px 噪声时点估计略差，区间含零）。时间不比较：基线是在有负载的机器上量的，不声称更快。
- 已知限制：(1) 输入有效却被判为 `review` 的比例从 8.6% 升到 14.1%（+5.5 个百分点，区间 −0.8 到 13.3 个百分点，含零），超出事先定的“基线 + 5 个百分点”约 0.5 个点，所有者同意带着这一条关闭 I1；集中在被裁切的图片（画面中心偏离，而相机模型拟合的是居中的）：被裁切的图 29.5%，完整画幅的图 5.95%。合成套件里 34% 的场景是裁切的，这个比例是套件的性质，不代表真实图片。(2) 真正很差的运行仍被标出（98.5% → 97.9%，无明确变化）；按评测工具的定义，标记与真值一致的比例 67.0% → 55.6%，标记的精确率 42.7% → 26.6%，好的运行被标出的比例 43.3% → 52.7%：标记里有不少是对好的运行的误报。(3) 只在合成数据上测过：真实图片的准确率尚未测量，看图模型（真实读图）在新提示词下的表现也未测试（live 模式需要所有者同意、密钥和费用确认）。

# 2.8.0-rc1 — 2026-10-16

- 开源准备：许可证改为 Apache-2.0（本目录附 `LICENSE`、`NOTICE`）。`SKILL.md` 改为英文，frontmatter 增加顶层 `license`、`compatibility` 和中英双语 description（含触发词），只保留运行时说明；维护和发布流程移到仓库的 `docs/maintainers.md`。新增英文 `README.md`（安装到 Claude Code、Codex、Cursor 或其他 agent，检查安装，环境变量），`README_CN.md` 同步改写。
- 可在任何位置使用：所有命令写成 `python "<SKILL_DIR>/scripts/..."`，在 bash 和 PowerShell 下相同；`input/`、`output/` 放在用户的工作目录。`templates/build_spec_template.py` 找不到 skill 时给出 `BOOTH_SKILL` 提示，不再抛 StopIteration。
- 统一的 Blender 查找 `common.find_blender`：显式路径或 `BLENDER_EXE`，再到 `PATH`，再到各系统常见安装位置（Windows 取最新版本，macOS `/Applications/Blender.app`，Linux snap 和 /usr/bin），接受 macOS 的 `.app` 目录；显式路径无效时报告而不悄悄换成别的 Blender。`doctor`、`preview`、测试运行器和网页服务都用它。`doctor` 增加 jsonschema、trimesh 检查、Blender 版本提示（低于 4.0 警告）和 `next_steps`。
- 新增 `boothctl.py build` 与 `boothctl.py verify`，包装原生 Blender 调用（`--` 之后的选项传给 blender_build.py），文档不再需要 PowerShell 的 `& $blenderExe`。Blender 子进程输出按 UTF-8 解码（错误替换），不会因非 ASCII 字符中断。
- 回归示例改为合成数据：`examples/regression/`（由 `tests/make_regression_example.py` 生成，尺寸全部虚构），替换原先带客户品牌的示例；原来的客户回归说明改为通用的 `references/legacy-regression.md`，只保留通用教训；`release_status.json` 中旧案例的键改为 `legacy_case_end_to_end_visual_match`。测试中的相机拟合改用示例自身画幅。示例数值重新虚构，与任何客户案例无关。
- 发布证据只记录相对仓库的路径（测试运行器和打包工具），不再写入本机绝对路径。`tools/package_skill.py --bundle <zip>` 生成只含运行时文件的 zip（不含测试、验证历史和示例，只附带参考文档指向的 `examples/regression/annotations.template.json`），用于发布页附件。`validation/history/`（以前版本的证据）不再纳入版本库和清单，只在维护者本地保留，不纳入版本库也不发布；旧的 2.2 时期验证记录移入本地 `validation/history/legacy`。`release_status.json` 去掉 `audit_evidence` 并更新说明文字。
- `tests/run_tests.py` 的 Blender 探测也统一走 `find_blender`（接受 `Blender.app` 及末尾斜杠），证据中只记录脱敏后的路径。
- 参考文档（重建 QA、场景描述、基准流程）的命令改为在工作目录运行 `python "<SKILL_DIR>/scripts/..."`，原生构建和重开改用 `boothctl.py build` / `boothctl.py verify`。skill 目录的 `NOTICE` 只保留本目录相关内容（three.js 属于仓库的 `web/`，不在本目录）。
- 文档去掉客户案例名称；设计先验注明来源为一份未公开的工作室内部规范，只采用通用尺寸。
- 测试：新增 `tests/test_blender_lookup.py`（查找顺序、显式路径、`.app`、各系统候选、doctor 的下一步、build/verify 参数传递）。

# 2.7.0-rc1 — 2026-10-15

- 新增场景描述契约 `schemas/scene-description.v1.json`：图上读到的东西（尺寸标注、角点、构件落点像素、颜色、证据）写成纯数字 JSON。所有键必填、可空用 null，只用 `$schema/title/description/type/enum/properties/required/items/additionalProperties` 这几个各家结构化输出都支持的关键字（测试强制白名单），方便任何看图模型或人填写。
- 新增 `scripts/draft_compile.py` 与 `boothctl.py compile`：场景描述加原图编译成 spec，不调用任何模型。流程：结构校验和语义校验（逐条给出错位置）→ box_fit 拟合相机（给定 fit 时核对图像尺寸、W/D/H、floor_z 和相机）→ 用 camera_math 把像素反投影到地面或展台边 → 用构件宏和 SpecBuilder 建模 → 钳进占地、聚光灯去重、同向共面的盒子拉开 2 mm（地面从不被移动）→ validate → 写 spec.json、box_fit.json、compile_report.json。支持 counter、round_table、shell_chair、planter、box、cylinder、louvre_wall、wall、wall_panel、tv、led_wall、spotlights、roof_frame。
- 编译跑完才写输出目录：被拒绝、拟合失败或读不了图时目录保持原样；同一目录可反复编译，也可用输出目录里的 source.* 作为输入；换扩展名时旧的 source.* 会被清掉。单个构件失败（矩形在墙外、构建器拒绝参数、排不下）整件跳过，连同它的假设、调整和警告一起撤销，状态为 partial。所有代填的先验值（厚度、台面深高、桌半径和高、椅座高、聚光灯体、LED 模组等）都记入 `assumed` 并完整汇总到 spec 的 `compile_assumed_values`。场景里填了而类型不读的字段会在报告里列出，不会悄悄吞掉。拟合警告和歧义提示进入报告与摘要。
- 一轮独立对抗性审查（三个视角，每条发现由另一个 agent 复现）发现并已修复：带关键不确定项的场景会崩溃（KeyError）；box/cylinder 几何被拒绝时整个编译中断；高度未知时抛 KeyError；`size_m` 太短时 IndexError；整数字段接受 2.5 之类；旋转盒子按外接圆钳位被误移数米；共面修复会缩小地面使 spec 失效；louvre 排不下、斜排、单片偏心、超出展台；电视和 LED 墙只修剪位置却照常超出墙边；浅展台的默认屋顶框被丢弃；先验值未记录；丢弃的构件仍留下假设记录；`--fit` 与场景不一致时被静默采用；重新编译时 SameFileError 和旧文件残留；地面角点地标按 W/2 取值而不看记号；两个入口失败输出通道不一致、I/O 错误是栈回溯；jsonschema 被写成仅测试依赖。
- 测试：`tests/test_draft_compile.py`（22 项，含 1 项原生预览）和 `tests/test_draft_compile_rules.py`（34 项，每项对应一个审查发现，另含四个展台边的 wall/wall_panel/tv 位置与朝向、schema 白名单和 schema/处理器/文档一致性）。对编译器副本做了 23 种故障注入的变异检查（翻转各边内向符号、去掉去重、跳过共面修复、忽略证据、允许移动地面、取消回滚等）：22 种首轮即被测试发现，剩下的“编译途中先写文件”补了一条晚期失败测试后也被发现。
- 新增 `references/scene-description.md`，SKILL.md 快速路线第 4 步增加这条路径，兼容性说明加入 jsonschema（GLB 导出需 trimesh）。
- 用一张本地客户图实测（只在 output/）：同一份读数写成场景描述后编译，99 个构件、校验通过、无警告，与手写 spec 逐件对比位置中位差 0.4 mm、最大尺寸差 4 mm，分组一致；原生预览角点中位误差 2.14 px。

# 2.6.0-rc2 — 2026-10-14

- 仅文档：SKILL.md 与 README_CN.md 不再说网页编辑器“尚未提供”，改为说明仓库根目录 `web/` 的静态编辑器（不属于本 skill 包）及“编辑 spec”的流程：载入、编辑、保存、再校验、再生成；写明改动只回写 spec，改过的零件标为 inferred，相机标为 unfitted。图纸仍未提供。
- 代码、schema 和测试没有变化，回归证据沿用 2.6.0-rc1 的完整原生回归（210 项，0 失败，0 跳过；输入哈希一致）。

# 2.6.0-rc1 — 2026-10-14

- 为网页编辑器做准备：spec 的构件新增可选字段 `group`（schema 与校验器接受小写标识符，缺省即独立件，旧 spec 不变）。构件宏的所有零件自动带宏前缀作 group；`SpecBuilder.grouped(名)` 和 `add(..., group=)` 可手动分组；Blender 构建把 group 写进对象自定义属性。宏输出除多出 `group` 外与此前逐字节一致（测试去掉 group 后比对原指纹）。
- 新增 `scripts/export_glb.py` 与 `boothctl.py glb`：不用 Blender，把 spec 导出为 GLB。Z 向上换成 Y 向上（纯旋转，三角形绕向不变；测试检查每个网格体积为正），颜色按 sRGB 转线性并以精确浮点写入（trimesh 默认存 8 位，暗色会分层，所以重写材质表），每个构件一个节点并带 extras，同组构件挂在 `group:<名>` 下。文字构件、格网纹样不导出、发光强度大于 1 会截断，均列入报告。校验不通过的 spec 不会写出 GLB。trimesh 现在也是 GLB 导出的可选依赖。
- 测试：新增 `tests/test_group_glb.py`（宏分组、可选性、schema/校验器拒绝坏值、GLB 节点层级、extras、坐标轴与绕向、颜色转换、命令行与 boothctl）。

# 2.5.0-rc1 — 2026-10-14

- 改名：skill 与目录由 `rebuild-booth-blender` 改为 `booth-image-to-model`（从一张展台图片生成参数化三维模型和客户渲染图）。新名字不绑定 Blender，也不暗示 CAD 或图纸：当前不输出图纸，尺寸也不是实测值。
- 同步更新 SKILL.md、README、AGENTS.md、模板中的 skill 查找、打包工具的默认路径、schema `$id` 和 `.gitattributes`；打包工具改为从 SKILL.md 读取 `skill_name` 写入发布状态。
- `validation/` 里已归档的审计、历史证据保持当时的旧名字，不改写。GitHub 仓库名尚未更改。

# 2.4.0-rc4 — 2026-10-14

- 客户图默认曝光由 −0.5 EV 改为 −1.0 EV（按所有者确认）。依据：全白展台在 −0.5 EV 下 6%–12% 的展台像素被压平，−1.0 EV 后降到 2.4% 以下；曝光对照图显示 −1.0 EV 能分开墙、地面和柜台。降低曝光会同时压暗灰色背景。需要旧效果时传 `--client-exposure -0.5`。预览的曝光建议以 −1.0 EV 为基准。

# 2.4.0-rc3 — 2026-10-14

- 依据三张本地客户图的新流程计时（20–27 分钟；工具计算只占 2–7 分钟，其余是读图和写 spec）：
- 预览新增 `--client-look`（`boothctl preview --client-look`、`blender_build --preview-client-look`）：同时渲出终版客户画面，报告高光被压平的像素比例，并给出建议的 `--client-exposure`；`--client-exposure` 可传给预览以验证。实测全白展台在默认 −0.5 EV 下 6%–12% 的像素被压平，建议值 −1.0 EV 后降到 2.4% 以下。默认曝光不变。
- box_fit 新增 `scale_checks`（用图中直立标准件的底、顶像素和期望高度核对尺度假设，不参与拟合），结果带 `assumed` 列表，模板把它写进 `scale.notes`。
- 修复：overlay 在没有任何点用到高度时不再 KeyError（只画地面）；宽、深缺失时给出明确提示。
- design_check 以地台构件自己的矩形为基准判断出界和到开口边的距离，不再假定地台以原点为中心。
- `benchmark.py prepare` 的重复目录报错会说明已有的 case 和该怎么做（仍然拒绝覆盖）。

# 2.4.0-rc2 — 2026-10-14

- 修复：`--hd-scale 3` 在一张本地客户图上（3 倍后约 6.9 MP）让 OIDN 降噪器内存不足，Blender 崩溃，高清图损坏且没有 build_report。现在高清帧默认限制在 5 MP（`--hd-max-mp`，4.9 MP 实测可渲出），超出时降低有效倍数，必要时跳过高清；高清阶段前先写一份 `partial` 报告，渲染高清前释放持久数据；build_report 的 `hd` 记录请求倍数、有效倍数和是否被限制。
- box_fit：残差超过 4 px 时状态为 `high_residual`，并点名最差的三个点与警告（此前会报 fitted）。
- design_check：单个挤出的 U 形门头不再按平面范围误报带深（改为 not_checked）；椅子和桌子按 0.6 m 内归为一个座位组，避免椅腿被当成独立家具而产生大量窄间隙提示。
- 依据：一张本地客户图的新流程计时（约 26 分钟，终版因上述崩溃失败）。

# 2.4.0-rc1 — 2026-10-14

- 新增建议性设计检查 `design_check.py` 与 `boothctl design-check`（`preview --context` 同步给出）：部件出界、限高、无建模支撑路径、门头楣带、前台、显示面、LED 整块模组、坡道、画面底边、窄间隙。只用中性措辞；缺少 context 的检查报告为 not_checked，不套默认值；不改 `validate()`。
- 新增 `schemas/design-priors.v1.json`：只收 `design-priors.md` 已有数值，每条带出处且 `reviewed=false`；测试保证宏的默认值、电视档位与它一致，且文档与 JSON 中不出现内部规则编号。
- 新增构件宏 `header_band`（直段/L/U 形，拐角不重叠，落地立柱）、`tv`（16:9 档位，必须给边框与厚度）、`led_wall`（整块模组，对齐板边的接缝）、`spotlights`（自发光灯体，不建光锥）；电视档位表补 60 英寸。已有宏的输出用固定指纹测试保证逐字节不变。
- 新增原生预览测试：含全部新宏的合成展台。

# 2.3.0-rc2 — 2026-10-14

- 依据对一张新图的计时基线（约 27.7 分钟，其中读图与写 spec 约 16 分钟、3 倍高清终版 376 秒）补齐测量与诊断，默认行为不变。
- build_report 新增 `stage_seconds`（校验、建模保存、每张渲染、遮罩、终存）；`boothctl preview` 回报阶段用时和启动开销；`--hd-views client|both` 可只出客户视角高清图（默认仍两张）。
- Workbench 预览崩溃后，同一预览目录的后续预览直接用 Cycles（`.preview_engine.json`）。
- box_fit 结果带画幅和地面高；不确定时点名未被约束的尺寸并给出补点建议（`ambiguous_dims`、`ambiguity_hint`）；文件头示例改为中性数字。
- 新增 `overlay.py` 与 `boothctl overlay`：在原图上画拟合出的展台外框、0.5 m 地面和墙面网格、点击点到拟合点的偏差，建模前检查机位是否贴合。
- camera_math 新增 `on_wall_x`（侧墙反投影）和 `height_above`（由底部与顶部像素估直立物体高度，并报告偏离垂线的距离）。
- 预览对照把 landmark 误差分成参与拟合（循环）与独立检查（fit=false）两类；SKILL 说明高调展台用 Cycles 预览和 `--client-exposure`。
- 项目模板从 box_fit.json 读取画幅，并核对输入快照的真实尺寸，不再含固定画幅和像素坐标；新增运行模板的测试。

# 2.3.0-rc1 — 2026-10-14

- 新增快速路线，按上一次单图重建的耗时复盘设计：先用 6–10 个角点同时拟合相机、焦距与展台比例（box_fit），再建模；尺度来自客户尺寸、标准件或显式标注的假设。
- 新增 camera_math（与 Blender 一致的投影/反投影）和 components 宏库（SpecBuilder、柜台、圆桌、壳椅、百叶、格栅、种植槽、壁挂屏、搁板），项目 spec 改为几十行参数脚本；新增项目模板。
- validate 新增 COPLANAR_OVERLAP 警告：同向共面且重叠的外露面会渲出黑面或闪烁；凸面精确求交，被反向面贴住的情况不报。
- blender_build 新增 `--preview`（Workbench 或低采样 Cycles，只渲源机位）与 `--hd-scale`（同进程高清客户图和第二视角）；Cycles 默认自适应采样、OIDN 和持久数据；材质同步视口颜色；相机近裁剪面按距离设置，修复光栅预览和视口的深度锯齿；同一输出目录加构建锁。
- boothctl 新增 `preview`：新编号目录、单图渲染、四宫格对照与 landmark 像素误差；核显驱动导致 Workbench 失败时自动改用 Cycles。
- 新增设计先验文档：一份工作室内部规范（未公开）的通用尺寸换算为米制、尺度锚点优先级（系统展具 0.99/0.992 m 模块、LED 拼缝、电视档位优先；前台、人、绿植只作核对）、展位类型读图提示，并引用 knowledge/research 中带出处的场馆与尺寸整理稿。
- Cycles 降噪改为 OIDN Fast 预滤波 + Balanced 质量；壳椅默认座高 0.45 m。

# 2.2.0-rc2 — 2026-10-07

- 按 skill-creator 标准进行三路独立 AI 初审，再用合成案例复现并修复；报告区分初审、修复和验证，不冒充外部认证。
- 对齐嵌套 schema 与运行时校验，拒绝未知字段、缺失项、非法类型、文字布尔挤出量和非正数对应点权重；源图 hash 与真实像素画幅同时核对。
- 严格 QA 单独检查保留点的中位数与 P90；原生重开核查文字尺寸、挤出和对齐；透视拟合范围围绕相机种子设置。
- 项目分开 input/ 原始输入与 output/ 任务结果；benchmark 保存不可变输入快照，并支持 Windows 跨盘引用。旧 test/ 链接继续可用。
- 回归 runner 显式配置原生 Blender，结果写入独立目录，保留完整真实日志与代码/schema/测试来源 hash；发布维护工具拒绝跳过、过期或不完整证据。
- 实际回归结果见 validation/test_results.json；包含原生 Blender 构建、重开和失败拒绝。视觉相似性和实测尺寸仍需逐个展台复核。

# 2.2.0-rc1 — 2026-10-07

- 新增通用 profile_extrusion 和 arc_panel：真实凹轮廓、顶部/底部边缘缺口、弧形板及相邻分段；拒绝自交与退化输入。
- 可选米制 procedural grid 材质，弧面按弧长展开并可跨分段连续；明确表面格纹不等于实体穿孔。
- 第二视角与源图水平至少相差 35°，按相机坐标中全部包围盒角点取景，改善横向长展台和正面源图的检查视角。
- 区分平面宣传图和实体屏幕；开口保留真实后方可见结构。样本尺寸、色带数量、布局和照片均留在被忽略的 test/。
- 75 项测试通过且无 skip，包括原生 Blender 5.2.2 LTS 重开、真实负空间与材质图篡改拒绝。视觉对照仍不等于实测复原。

# 2.1.0-rc1 — 2026-10-06

- 默认客户纯展台展示：去展厅、邻展、Logo、文字、人物、水印和尺寸箭头，保留建筑、家具与中性屏幕。
- box 真实全边/竖角圆化，新增连续 Bezier swept_strip；尺寸与布局保存在样本 spec。
- 相机、灯光、裁剪按实际包围盒缩放；透明验收图与中性客户图分别输出，客户曝光可配置。
- sRGB 材质真实转换、JPEG 输入编码为真 PNG、修复独立复开 CLI 并核验 shader 参数。
- 新增输入登记、AI 清洁 prompt、四列对比页及真实文件证据。测试素材和所有派生输出留在 Git 忽略的 test/。
- Blender 5.2.2 LTS 原生运行验证，64 项测试通过且无 skip；三类图片的视觉结果仍保留具体近似和待复核项。

# v2.0.0-rc1

## 可执行代码

- 显式 rounded_ring / rounded_portal 原型；真实内外圆角、连续实体与开口。
- 严格 v2 spec；scope 对照、尺寸/几何可构造性、未知/未使用参数、关键构件义务检查。
- 受控参数修改；禁止借修改尺寸接口改归属、输入来源、关键要求和审核证据。
- 固定几何的有界透视相机拟合，要求可信多深度对应点与保留检查点。
- 可见 masks、边界和关键点误差计算；拒绝画幅不一致或直接把 RGB 照片阈值分割。
- 本地阶段指纹和 complete + 文件哈希校验；不是自动任务服务或构件级场景缓存。
- 原生 Blender 分件构建和独立重新打开验证脚本。
- 真实文件、同版本渲染、语义复核与未解决关键疑点的交付检查。

## 工作流

- 先 scope / 负空间 / 拓扑 / 相机，再比例和细节。
- 背景几何不得混入 booth，也不得伪装成 render_rig。
- 同机位对拍与第二视角分别验收；不把大总览图当参考机位。
- 明确对模型能力、计算预算和质量指标的边界，不写虚构的节省比例和还原分数。
- 主 Skill 精简，细节按需加载，重复创建几何交给固定代码。

## 发布状态

候选版。单元与实际旧输入拒绝测试完成；原生 Blender 集成和完整的旧客户案例对拍尚待验证。
未更新用户账号中的已安装版本。
