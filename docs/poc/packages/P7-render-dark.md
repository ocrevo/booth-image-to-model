---
id: P7-render-dark
title: 渲染预设 --look dark_hall：暗场，发光部件照亮地面和墙
goal: 清单第 4 行：暗场渲染里发光部件真的照亮周围的地面和墙，Cycles 预览里看得见地面光晕和墙上的洗墙光；缺省行为不变
depends_on: [P7-mat-projected, P7-fit-camera, P5]
model: sonnet
lane: worker
allowed_files:
  - booth-image-to-model/scripts/blender_build.py
  - booth-image-to-model/scripts/boothctl.py
  - booth-image-to-model/scripts/material_patterns.py
  - booth-image-to-model/tests/test_material_patterns.py
forbidden_files:
  - booth-image-to-model/scripts/components.py
  - booth-image-to-model/scripts/motifs.py
  - booth-image-to-model/scripts/design_compile.py
  - booth-image-to-model/scripts/geometry.py
  - booth-image-to-model/scripts/common.py
  - booth-image-to-model/CHANGELOG.md
  - web/**
  - tools/poc/**
inputs:
  - output/poc/ceiling/missing-vocabulary.md 第 4 行的"特征"和"差在哪"（指挥官贴进 brief）
  - blender_build.py 里 client_look 的世界、背景、曝光，以及渲染台架的灯（common.render_rig 给出的 lights）
  - components.spotlights（只有灯体，没有光锥）；设计路线发光件的 emission 值（design_compile.look）
deliverables:
  - blender_build.py --look neutral|dark_hall（缺省 neutral，即原行为）；build_report.json 记 look 和派生灯的个数
  - dark_hall（只限预览）：深灰世界（约 0.02）、深色微反射的地面板（约 0.05，粗糙度约 0.7）；渲染台架的灯降到原能量的一个固定比例；发光材质的强度乘一个固定倍数，由 Cycles 把发光网格当光源照亮周围；预览采样上限提高；派生灯不写进 spec
  - 派生灯只给 role 为 spotlight 的部件：一盏向下的聚光灯（固定锥角），强度按 emission 和灯体面积换算（2026-10-23 改：不再在包围盒的面上放面光）
  - material_patterns.dark_hall_settings() 和 material_patterns.dark_hall_lights(spec)：纯函数，返回描述，无 bpy 可测
  - boothctl.py preview 加 --look 并透传；build 照旧用 -- 之后的参数透传
tests_first:
  - test_preview_look_defaults_to_neutral_and_rejects_unknown_values
  - test_dark_hall_settings_are_described_without_bpy
  - test_dark_hall_lights_every_emissive_part_through_its_own_surface
  - test_dark_hall_is_preview_only_and_refuses_workbench
  - test_spotlight_parts_get_downward_spot_lights
  - test_neutral_look_derives_no_lights_and_keeps_the_rig
  - test_boothctl_preview_passes_look
acceptance_cmds:
  - BLENDER_EXE= python -m unittest discover -s booth-image-to-model/tests -p "test_material_patterns.py"
  - BLENDER_EXE= python -m unittest discover -s booth-image-to-model/tests -p "test_quality_gate.py"
  - python booth-image-to-model/scripts/boothctl.py preview --help
  - python tools/check_docs.py
  - python tools/check_lf.py
effort_h: 5
day: 6
stop_rule: 不降级：只把背景变暗、发光部件照不亮周围不算完成；做不完就交已通过的测试和 notes，第 4 行在还原记录里保持"计划"，指挥官报决策人（G3 可能推到第 8 天）
---

# P7-render-dark 暗场和光斑

## 目标

补清单第 4 行：发光踢脚、灯带和筒灯照到地面和墙上形成的亮斑。这类图常是暗色展厅里的亮展台；今天的渲染里只有发光体本身，地面和墙不会被照亮，这类图里最显眼的"亮地面"和墙上的光扇造不出。本包加一个暗场预设：背景变暗，发光部件真的照亮周围。它只用于对比（投影版和 P5b 的对照预览）；交付用的中性版缺省不变。

光斑的形状不按原图逐像素对齐（像素级一致在 01 的不做清单里）；在 spec 里记灯具的方向、角度和强度是以后的升级（见还原记录（`06-fidelity-ledger.md`，只在本地）第 4 行）。

时机：G2 通过前只写 `tests_first` 的测试并提交到分支，不实现、不合并；G2 通过后再实现、验证、出证据、合并。

## 输入

前言 `inputs`。

## 交付物

1. `blender_build.py` 加 `--look neutral|dark_hall`，缺省 `neutral`，此时世界、背景、台架灯和材质与以前完全一样，不派生任何灯；缺省时传给 Blender 的命令行与以前逐字节相同（不追加 `--look neutral`），P7-mat-projected 的 `test_default_preview_command_line_is_unchanged` 不得放宽，只能在它的列表里加本包的新情形（2026-10-23 补充）。
2. `dark_hall` 的设置由 `material_patterns.dark_hall_settings()` 给出（一个字典）：世界颜色约 0.02；深色微反射的地面板约 0.05、粗糙度约 0.7（'微反射'，不是亮面）；台架灯的能量乘一个固定比例；发光材质的强度乘一个固定倍数；预览采样上限提高到一个固定值（约 32，CPU 上约几分钟）；曝光不变。`dark_hall` 只用于预览（`--preview`），配 Workbench 引擎时在 `blender_build.py` 里也拒绝（Workbench 不画灯光）。（2026-10-23 改，见第 3 条）
3. 光从哪里来（2026-10-23 改：第 1 轮验证发现'在包围盒最大的一对面上放面光'对空心环会铺满内孔、凭空多出一块发光天花板，对贴墙贴地的灯带会把灯放进墙里地里；所以改成真实发光面的光照）：
   - 发光部件（`emission` 大于 0，含发光文字）不派生面光：`dark_hall` 下它们的发光强度乘第 2 条的倍数，由 Cycles 把发光的网格当作光源，照亮周围的墙和地面。光只从部件真实的发光面发出，空心框、圆弧、贴墙贴地的灯带都按自己的形状发光。
   - `role` 为 `spotlight` 的部件：派生一盏向下的聚光灯，放在灯体下端，锥角固定，打在附近的墙和地上形成光扇；强度按 `emission` 和灯体面积换算，系数是常量。派生灯由 `material_patterns.dark_hall_lights(spec)` 给出（类型、位置、方向、锥角、颜色、强度），只在 Blender 场景里，不写进 spec。
   - `build_report.json` 记 `look`、发光部件个数、派生灯个数，以及没处理的部件和原因（不静默跳过）。
4. `boothctl.py`：`preview` 加 `--look` 并放进 Blender 命令行；`build` 照旧用 `--` 之后的参数透传，不改。
5. 地面的颗粒感（CPU 上 Cycles 预览的噪点）不在本包处理；`dark_hall` 的采样上限见第 2 条，`--samples` 在上限之内起作用。`dark_hall` 预览加了不透明的地面板，它的 `compare.json` 数字（展台像素比例、建议曝光）不可比，写明 `look`；打分只用中性版预览（P8d 第 2 步）。
6. `references/data-contract.md` 写上 `--look`、`build_report` 的新键、只限预览和地面板。

## 允许改的文件

前言 `allowed_files`。链 B 租约，含 `boothctl.py`。`common.py` 不动：台架灯的能量在 `blender_build.py` 里按比例调。

## 禁止改的文件

前言 `forbidden_files`。

## 先写的测试

前言 `tests_first`，先提交（提交信息 `P7-render-dark: tests first`），在基线提交上必须失败。`test_boothctl_preview_passes_look` 替换 `subprocess.run`，只断言命令行。`blender_build.py` 在模块顶部导入 `bpy`，只能用测试文件里已有的假 bpy（`FakeBlenderCase.run_main`）端到端跑 `main()`，不要真的导入 Blender；`add_look`、台架灯比例、发光倍数、采样上限和 `build_report` 的键都要有这样的测试（2026-10-23 改）。

## 验收命令

前言 `acceptance_cmds`，全部退出码 0。

## 完成的定义

1. 先写的测试和验收命令全部通过；`neutral` 不派生灯，设置字典与以前一致。
2. 证据：按 [05 第 3 节第 2 步](../05-gates-and-closing.md#3-g3-引擎够用止损点二第-7-天)，合并前由 Blender 通道用本包分支的代码出 `preview --engine cycles --look dark_hall --samples 32`（`boothctl` 的 `--samples` 缺省是 8，不写就用不上 dark_hall 提高的上限；2026-10-23 补），放进 `output/poc/ceiling2/evidence/4/`：需要这一行的图各一张：第一张看发光条和发光边框，第二张看筒灯。独立核对 agent 逐条判，全为"是"才写 `pass`：
   - 第一张：发光条下方和旁边的地面被照亮，形成离光源越远越暗的光晕；
   - 第一张：发光边框周围的墙面被照亮；
   - 第二张：筒灯下方的墙上有扇形的洗墙光斑。
3. 不得用降级办法凑数，例如把地面局部涂亮、在地上铺一块发光片、凭空加一块原图没有的发光面；光晕必须是光照照出来的（地面和墙上看得见由近到远变暗），只调亮发光体本身而周围不亮不算；用了就判 `fail`。
4. 合并后指挥官把还原记录（`06-fidelity-ledger.md`，只在本地）第 4 行的状态改为"已验证"。

## 验证者清单

1. 白名单；链 B 租约只在本包手里。
2. 先红后绿。
3. 变异：把命令行和函数的缺省值改成 `dark_hall`、跳过 `add_look`、去掉发光倍数或台架灯比例、去掉聚光灯强度里的 `emission`，各自都应有测试变红。
4. 破坏性输入：`--look` 给未知值必须被 argparse 拒绝；spec 里没有发光部件时 `dark_hall` 照常出图、派生灯为 0。
5. 证据由 Blender 通道出、核对 agent 判，不由本验证者判；`verdict.json` 为 `pass` 之前不合并。
