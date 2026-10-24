# 数据和执行约定

## Spec

`schemas/booth-spec-v2.schema.json` 是机器可读格式；`scripts/validate_spec.py` 同时执行嵌套结构、跨字段、scope、尺寸与几何可构造性检查。必填项、未知字段和非法类型会被拒绝；扩展契约时同时更新 schema、运行时校验和测试。
必须同时理解 schema 与语义规则，不能以“JSON 合法”代替建模判断。

`source` 锁定原始图片或其字节不变的输入快照、SHA-256 和画幅。`--check-source` 和原生 builder 同时核对文件 hash 与真实像素尺寸，不能通过伪报分辨率改变画幅。`scale` 声明尺寸依据。`scope` 与 `components` 通过 scope_id 关联。
组件默认位于局部几何中心，placement 是世界坐标。Z 朝上。Euler XYZ 单位为度。
`rounded_portal` 位于 XZ 平面，厚度沿 Y，原点为整体包围盒中心；`rounded_ring` 位于 XY 平面，厚度沿 Z。
环的内外圆角分别驱动轮廓；无孔或者相交的环会被拒绝。门架是一条有两只脚的连续带状实体，不跨底部封口。

当前只支持一个矩形 floor/carpet 对象，尺寸须与 footprint 一致。非矩形展位边界要扩展几何与 schema，不能悄悄近似为矩形并声称完成。
不支持的几何原型会报错；请新增确定性 builder、对应 params schema 和拓扑/回归测试后使用。
box 支持 bevel_radius 和 bevel_mode=all/vertical（默认 all）；vertical 仅圆化四条竖边，上下端保持平切。swept_strip 的 control_points 使用 3n+1 个局部 [x,z] 点，每段 Bezier 的确定性偏移生成连续封闭条带，depth 沿 Y。无新增资产导入器、自动 UV 或孔阵生成器。
`profile_extrusion` 的 profile 是局部 XZ 平面简单闭合轮廓，首点不重复，depth 沿 Y；坐标原点由作者确定，不自动居中。边缘缺口写进外轮廓，不能用它表示带独立内孔的多环面。`arc_panel` 是局部 XY 平面的圆弧带，height 沿 Z；radius 为中心线半径，thickness 为径向厚度，start_deg/end_deg 从 +X 逆时针，取两角之间跨度小于 360° 的区间。原点为圆心与高度中面，不是包围盒中心；同圆心、半径、厚度与标高的相邻角段可用不同材质组成弧形彩带。
material.color 默认 linear；color_space=srgb 将颜色转为 linear 后写入 shader。client_render.png 是中性背景客户图，reference_render.png 仍是透明验收图，不应拿客户图背景做 silhouette。第二视角和灯光从实际变换后几何的包围盒产生。
第二视角与源机位水平角至少相差 35°；根据全部包围盒角点在相机坐标中的投影计算距离，适配横竖画幅，保留取景余量。它只用于检查补全部分与连接，不增加源图测量证据。
可选 material.pattern 格网参数为 `type=grid`、spacing=[横向米间距,纵向米间距]、line_width（米）、line_color（RGB3，继承 color_space）。coordinates=auto 默认弧板用 arc_length_z、其他用 xz；也可选 xy/xz/yz。offset=[米,米] 调整格网原点；弧板的 arc_origin_deg 指定展开角原点，相邻段共用它以保持纹样连续，并避免跨越 ±180° 展开缝。这是表面颜色纹样，不是实体孔洞，不能用它生成穿孔加工 BOM。
material.pattern 另有 `type=projected`（无其他字段）：颜色取自 `spec.source` 的图片，由以 spec 相机为投影器的 UV Project 修改器投到部件上，部件颜色的 alpha 保留（玻璃仍半透明）；`camera.fit_status` 为 `unfitted` 时校验报错。背面和被遮挡面得到错误像素；只要内存副本里有 projected 部件，其中的文字（FONT）部件先转成网格再加 UV 层和 UV Project 修改器，和其他部件一样被投影；中性构建不变，文字仍是 FONT 对象。转换失败（或修改器被拒）的部件不参与渲染（hide_render），并连同原因列入 `build_report.json` 的 `projected_uv_skipped`（`[{id, reason}]`），`projected_uv_note` 给出说明，绝不让它按图片上某一个像素显示成平色。`blender_build.py --material-set neutral|projected`（缺省 neutral）在内存里把每个部件改成 projected，不改 spec 文件，`build_report.json` 记 `material_set`；投影的准备（加载图片、UV 层、修改器）按内存副本里有没有 projected 部件决定，所以 spec 文件里自己写了 projected 的部件在缺省构建下也能建。`boothctl.py preview --material-set projected` 没给 `--engine` 时用 Cycles，给了 workbench 时拒绝（Workbench 只显示平色）；`build` 照旧用 `--` 之后的参数透传。
material.pattern 还有两种画在部件本身上的纹样（都是表面颜色，不是实体孔洞，不能用来生成穿孔加工 BOM；颜色都继承 color_space，不用外部图片）。`type=dots`：spacing=[米,米]、radius_m（米，必须小于较小 spacing 的一半）、dot_color（RGB3），圆点成行列排在格子中心，可选 coordinates（auto、xy、xz、yz、arc_length_z、`box`）、offset=[米,米]、arc_origin_deg（只配 arc_length_z）、fade。`fade` 为 null（缺省，铺满）或 {axis x、y 或 z，from_m，to_m}：沿部件物体坐标的这根轴，圆点半径从 from_m 处的 radius_m 线性缩到 to_m 处的 0，from_m 一侧之外保持全尺寸，to_m 一侧之外没有圆点；from_m 不得等于 to_m；半径随着色点的该轴坐标变化，所以圆点在渐变区会略呈椭圆。坐标模式 `box` 按每个着色点的物体空间法线绝对值最大的轴选平面（x 用 yz，y 用 xz，z 用 xy，并列时先 x 后 y），所以同一个部件的立面和底面都铺得上圆点（圆角环、门架腿、圆展台）；auto 在弧板上是 arc_length_z，其余是 xz。`type=grain`：scale_m（米，颗粒大小）、strength（0 到 1）、grain_color（RGB3），噪声纹理（物体坐标，3D）把部件颜色和 grain_color 按 strength 混合，用在地面时经 `style.floor_pattern`。网页编辑器只按 `grid` 的字段缩放图案，dots 和 grain 在编辑器里缩放时 radius_m、fade、scale_m 不跟着变。
图案按类型分发：`material_patterns.PATTERNS` 是 `{type: (参数函数, 节点函数)}`（grid、projected、dots、grain），`SIGNATURES` 是同样类型的节点签名函数；构建和 `validate_spec.py` 都经 `PATTERNS`。`blender_verify.py` 的独立复核也按它分发：重开 .blend 后，用同一节点函数在新材质里重建该部件类型的图案，比对 `SIGNATURES` 里该类型的节点签名（常量、运算、链接，投影版还有 UV 层名和图片名），不一致报 `material_pattern_mismatch:<id>`；没有图案的部件不得带任何图案节点。复核读 .blend 旁边的 `build_report.json`：`material_set` 是 `projected`（或 spec 里自己写了 `projected` 部件）时，按 `material_patterns.material_set(spec, material_set)` 得到的副本比对；每个非跳过的 projected 网格部件应带且只带一个 `PROJECTED_UV`（UV Project）修改器和 `ProjectedUV` UV 层，这个修改器算预期的修改器，不报 `unreviewed_modifiers`（缺了报 `projected_modifier_missing:<id>`、`projected_uv_missing:<id>`；别的修改器仍报 `unreviewed_modifiers:<id>`）；projected 的文字部件应是转好的网格，`projected_uv_skipped` 里列出的部件应保持隐藏（否则报 `skipped_part_visible:<id>`）；`material_set` 不认识报 `unknown_material_set:<名>`。没有 `build_report.json` 时按中性版比对。

构件可带可选字段 `group`（小写标识符，同 id 规则）：同一个家具的零件共用一个 group，编辑器里一起移动、换件；构件宏自动把宏的前缀写成 group。没有 group 的构件是独立件；旧 spec 不受影响。Blender 构建把它写成对象的自定义属性 `group`，GLB 导出把它作为父节点 `group:<名>`。

`requirements` 是最低结构义务，例如 `top_ring.kind=rounded_ring`。它只能检验已写入的义务是否实现，不能证明写入的义务本身识别正确。
`evidence.status=observed/inferred` 分离观察与补全；observed 必须有源图 ROI。`uncertainties` 中 open 且 critical 的事项阻止严格已验证交付，不阻止低成本候选测试。

`validate` 的 warnings 包含 `COPLANAR_OVERLAP`：两个构件同向的外露面落在同一平面且有重叠面积（凸面精确求交；被第三个构件反向面贴住而看不见的情况不报）。它不阻止构建，但渲染会出现黑面或闪烁，交付前应清零。

## 建议性设计检查

`design_check.py`（仅标准库，不改 `validate()`）读取已通过校验的 spec 与可选 context，输出 findings（level 为 advisory 或 info）、not_checked、checks_run。context 键严格校验：`height_limit_m`、`hanging_allowed`、`open_sides`、`led_module_m`、`footprint_tolerance_m`，未知键直接拒绝。检查项：部件是否超出地台、是否超限高、有无建模的支撑路径（由地面出发的接触关系）、门头楣带尺寸与标高、前台尺寸与距开口边距离、显示面比例/档位/中心高、LED 是否整块模组、坡道坡度与宽度、画面底边、家具组间的窄间隙（仅提示）。按构件角色识别（如 `header_band`、`counter`、`display`、`led_wall`、`ramp`）；自行命名角色的旧 spec 只会得到与角色无关的检查。限值读自 `schemas/design-priors.v1.json`，每条带出处且 `reviewed=false`，直到设计师签认。

## 快速路线的输入与输出

- `box_fit.py`：输入为像素角点和带符号的坐标表达式，输出 `camera`（`fit_status=landmark_fitted`）、W/D/H、自由量、逐点误差和多起点解的离散度；`dim_source` 区分 given、assumed、fitted。它与 `camera_fit.py` 不同：后者在固定几何上精修相机，前者同时求比例，用于建模前。
  - 拟合用鲁棒损失（`loss`）。独立检查默认是留一法：`loo_reliable`、`loo_errors_px`、`loo_median_px`、`loo_max_px`、`outliers`（点名的角会从最终相机里剔除，写在 `dropped`）。拿掉一个角后多余方程不够时，`loo_reliable` 为 `false`，`loo_*` 为 `null`，`outliers` 为空：不点猜出来的名字。
  - 点上的 `fit: false` 是可选功能（角点 ≥ 11 个，或想自己指定留出点）：该点只投影、不参与拟合，误差在 `check_points`、`check_median_px`、`check_max_px`。每个检查点都少一个拟合点，8–10 个角时不用。
  - `limits` 是判断状态用的像素上限（已按图大小放宽，调用方不要另设一套）；`scale_checks` 是标准件的尺寸核对结果（`id`、`expected_m`、`measured_m`、`ratio`、`ok`，量不出来的 `ok` 为 `false` 并带 `error`）；`hint` 是“重新点一下”这类建议，`ambiguity_hint` 只放真正的歧义提示。
  - 标准输出只打印**一个** JSON（与 `--output` 相同，警告和 `hint` 都在里面）。退出码：0 = `fitted`，3 = 需要处理（`high_residual`、`ambiguous`、`check_failed`），2 = `blocked`（JSON 里有 `reason`）。
- `draft_compile.py`（`boothctl.py compile`）：把 `box_fit` 的结果复制进 `compile_report.json` 的 `fit`（缺的键写 `null`），并写 `quality_gate`：`{status: pass | review, reasons: [...]}`。`review` 的条件：状态不是 `fitted`、点名了出错的角（仅留一法可靠时）、独立误差超过 `limits`、`scale_checks` 有没通过的项、或根本没有独立信号。质量门只读 `box_fit` 的 JSON，不阻止编译，也不改退出码。细节见 [scene-description.md](scene-description.md)。
- `overlay.py`：被拟合的角画绿圈；检查点（`fit: false`）画橙色方框，不计入 `max_point_err_px`（另报 `max_check_err_px`）；被点名的 `outliers` 再加一圈品红色环。
- `camera_math.py`：与 blender_build 一致的投影/反投影（水平 sensor fit、principal point、roll），只用标准库。
- `blender_build.py --preview`：只渲染源机位 `preview_render.png`（Workbench 或 Cycles 低采样），状态 `preview_only`，不能作为交付。`--render --hd-scale N` 在同一进程追加 `client_render_hd.png` 与 `inspection_render_hd.png`（中性灰底），并写入 build_report 哈希。
- `boothctl.py preview` 在 `--out-root` 下建新的 `preview_NNN`，生成 `compare_sheet.png` 与 `compare.json`（landmark 像素误差，无相似度分数）。
- `blender_build.py --look neutral|dark_hall`（`boothctl.py preview --look` 透传；缺省 `neutral`，此时世界、台架灯和材质与以前完全一样，命令行不追加 `--look`）。`dark_hall` 是暗场对比预览，**只限 `--preview`**，且只在 Cycles 下有意义：不带 `--preview` 时 `blender_build.py` 直接拒绝；配 Workbench（`--preview-engine workbench`，默认引擎也算）也拒绝，因为 Workbench 不画灯光；但开着 `--preview-client-look` 时这一遍预览本来就走 Cycles，这时不拒绝（`boothctl.py preview` 没给 `--engine` 时自动用 Cycles，给了 workbench 时拒绝）。它做四件事：世界颜色约 0.02；在预览场景里加一块深色（约 0.05）、粗糙度约 0.7 的地面板 `LOOK_FLOOR`（属于渲染台架，不属于展台，不写进 spec；所以 `blender_verify.py` 会因 `rig_contains_geometry` 拒绝它，不能用于交付构建）；台架灯能量乘固定比例；发光材质的强度乘固定倍数，由 Cycles 把发光的真实表面当光源照亮周围的墙和地，不在包围盒的面上另放面光。`role` 为 `spotlight` 且 `emission` 大于 0 的部件另派生一盏向下的聚光灯（`LOOK_*`，固定锥角，强度按 `emission` 和灯体面积换算，只在场景里）。数值见 `material_patterns.dark_hall_settings()` 与常量。预览采样上限由 8 提高到 32，`--samples` 在上限之内起作用。`build_report.json` 记 `look`、`emissive_parts`（`emission` 大于 0 的部件数，含文字）、`derived_lights`（派生灯个数）、`look_unhandled`（`[{id, reason}]`，没能处理的部件及原因，不静默跳过）和 `preview_samples`（Cycles 预览实际用的采样数；不是 Cycles 预览时为 `null`）。`dark_hall` 预览的 `compare.json` 另记 `look` 和 `look_note`：地面板不透明，`booth_pixel_fraction` 和建议曝光与中性版不可比，打分只用中性版预览。

## QA 输入

annotations.json 的 source_sha256 必须对应原图。它引用 booth silhouette、各关键件可见 mask 和有效区域 mask。
模板在 `examples/regression/annotations.template.json`（合成示例）。该模板中的 mask 路径不存在，必须实际制作与审阅后才能评估。
所有 mask 必须和原图具有完全相同的像素宽高。脚本不自动缩放或重对齐。RGBA 的 alpha 被当作 mask；灰度/黑白 PNG 使用像素值。
`part_masks/<component_id>.png` 是匹配相机下、保留其他展台物件遮挡的可见构件图。不是移除所有遮挡之后的孤立模型投影。

投影点格式：`[{id, uv:[u,v], depth}]`。不可见点不评分；缺失可见点不是零误差，而是错误。
`camera_fit.py` 对透视相机拟合位姿、roll、焦距，保持主点和几何固定；透视相机必填 lens_mm/sensor_width_mm，正交相机必填 ortho_scale。拟合范围围绕种子设置，正交相机需手工/视觉复核设置。landmark.weight 必须是有限正数；compare/assess 分别报告全部可见点和保留点的中位数/P90，严格门禁单独检查保留点。

## 证据与真实文件绑定

build_report 绑定 spec、建模代码和输出文件哈希；native_verification 来自另一个原生 Blender 进程。
assess 重新检查输出字节，计算 mask/landmark 指标，检查审核记录绑定的相同 spec、代码和两张渲染。
visual_review 不是数字签名，也不是独立审计认证；其意义是记录具体审核者实际检查了哪些相同版本的图。
篡改所有 unsigned JSON 仍可能伪造证据，本地校验不是对抗恶意运行者的安全系统。

交付状态：
- `blocked_native_blender`：没有能执行/验证 Blender 的环境。
- `draft_unverified` / `built_not_verified`：已有参数或文件，尚未完成质量验证。
- `review_required`：存在缺失证据、错误或关键疑点。
- `verified_visual_reconstruction`：本项目的全部可执行门槛和实际视觉复核通过；不等于实测 CAD 或施工认证。

## 依赖

标准库：geometry、common、spec validator 的结构/语义检查、boothctl（除了 doctor 探测外不加载第三方库）。外部 validator 使用 `--check-source` 时通过 Pillow 读取真实像素尺寸；原生 builder 通过 Blender 图像 API 读取，不需要额外 pip 依赖。
外部 Python：camera_fit/compare/assess 使用 numpy、Pillow、scipy；场景描述编译（`boothctl.py compile`）另外需要 jsonschema；GLB 导出和完整测试需要 trimesh。
Blender 内置 Python：blender_build / blender_verify，不需要往 Blender Python 里安装上述数据科学库。
已锁定测试实际版本在 validation/dependency_versions.json。requirements.txt 是兼容范围，不是对范围内所有组合的测试声明。
