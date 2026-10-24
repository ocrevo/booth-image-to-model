---
id: P1-edge
title: 边缘距离改用与候选无关的展台区域（G1 第一次重跑）
goal: 让删掉轮廓上部件的变体不再因为比较区域变小而显得更好，按事先登记的计划重跑 G1
depends_on: [P1, P8a]
model: sonnet
lane: worker
allowed_files:
  - tools/poc/score/metrics.py
  - tools/poc/score/score.py
  - tools/poc/loop/renderer.py
  - tools/poc/tests/test_metrics.py
  - tools/poc/tests/test_score.py
  - tools/poc/tests/test_renderer.py
  - tools/poc/tests/fixtures/**
forbidden_files:
  - tools/poc/loop/orchestrator.py
  - tools/poc/loop/edits.py
  - tools/poc/loop/selfplay.py
  - tools/poc/loop/roles.py
  - tools/poc/prompts/**
  - tools/poc/score/rank_check.py
  - tools/poc/score/panel.py
  - booth-image-to-model/**
  - docs/**
  - web/**
inputs:
  - docs/poc/03-scoring-spec.md 第 3.1 节（第 3 条是这次的裁定）
  - tools/poc/score/metrics.py 的 booth_mask 和 edge_distance；tools/poc/score/score.py
  - tools/poc/loop/renderer.py 调 score.py 的地方
  - booth-image-to-model/scripts/camera_math.py（project）
  - 拟合文件的格式：booth-image-to-model 的 box_fit 输出（dims_m、dim_source、floor_z、camera、image）
deliverables:
  - metrics.py：fit_region(fit, width, height, dilate_diag=0.02) 由拟合给出展台区域 M；edge_distance 接受这个区域，不再用候选的 alpha 膨胀
  - score.py：--fit <拟合文件> 必填；缺少或拟合相机与 spec 相机不同时退出码 2；score_detail 的 edge 一节报 render_outside_region 和区域面积
  - renderer.py：闭环调 score.py 时把它的拟合文件传给 --fit
tests_first:
  - test_fit_region_is_the_projected_hull_of_the_fitted_box
  - test_fit_region_reaches_the_top_of_the_picture_when_the_height_is_not_fitted
  - test_fit_region_does_not_depend_on_the_candidate
  - test_deleting_a_part_on_the_outline_does_not_improve_the_edge_distance
  - test_score_requires_fit_and_refuses_a_camera_mismatch
  - test_score_detail_reports_render_outside_region
  - test_the_loop_renderer_passes_the_fit_to_score
acceptance_cmds:
  - python -m unittest discover -s tools/poc/tests -p "test_metrics*.py"
  - python -m unittest discover -s tools/poc/tests -p "test_score*.py"
  - python -m unittest discover -s tools/poc/tests -p "test_renderer*.py"
  - python -m unittest discover -s tools/poc/tests -p "test_loop*.py"
  - python tools/check_docs.py
  - python tools/check_lf.py
effort_h: 3
day: 2
stop_rule: 做不完时先交 metrics.fit_region、edge_distance 和 score.py --fit，renderer.py 写进 notes
---

# P1-edge 边缘距离的固定区域

## 目标

G1 正式第一次跑（2026-10-23）：总分 Spearman 0.943 过了，边缘距离 Spearman −0.543 没过。排反的全是"删了主件的 B5、B6 边缘距离最好"，原因在 03 第 3.1 节第 3 条的旧规则：比较区域 M 由候选自己的 alpha 膨胀得到，删掉轮廓上的部件，原图那一块的边缘也一起被移出比较，距离反而变小。按事先登记的计划（`output/poc/memo/g1_plan.md`"第一次重跑"），M 改成与候选无关、由这张图的角点拟合给出的区域。规则原文在 03 第 3.1 节第 3 条，以它为准。

## 输入

前言 `inputs`。worktree 里没有 `output/`，测试全部用测试里生成的小图和编造的拟合、相机，不读真实图片。

## 交付物

1. `metrics.fit_region(fit, width, height, dilate_diag=0.02)`：占地 W×D 以原点为中心、从 `floor_z` 起到高度 H 的长方体，8 个角用拟合相机投影（和 `camera_math.project` 一致），取凸包，再膨胀 2 % 对角线，返回布尔掩码。H 不是已知值时（`dim_source` 里 H 是 `assumed`，或没有 H；`fitted` 和 `given` 都算已知，dc2de3d），顶面沿竖直方向延伸到图像上边：用顶面四角的投影向上延到 y = 0 再取凸包。长方体有一部分在相机平面之后时按 03 第 3.1 节第 3 条用近平面裁开（dc2de3d 和之后的补充以 03 为准）。区域只由拟合和图像大小决定，和候选无关。
2. `metrics.edge_distance(photo, render, region)`：两张边缘图只在 region 内计算，其余步骤（Canny、轮廓加内边、双向平均距离、除以对角线）不变。旧的"候选 alpha 膨胀"不再作为区域；可以保留一个只给测试和对照用的函数，但 `score.py` 不得回退。
3. `score.py --fit <拟合文件>`：必填；缺少时退出码 2；拟合相机和 spec 相机（position、target、lens_mm、sensor_width_mm、principal_point、roll_deg，容差 1e-6）不同时退出码 2，都不写分数文件；图像大小和拟合的 `image` 不同时也退出码 2。`score_detail.json` 的 `edge` 一节加 `region_px`（区域像素数）和 `render_outside_region`（渲染 alpha 落在区域外的像素占 alpha 的比例）。`--validate-truth` 模式不受影响。
4. `renderer.py`：闭环调 `score.py` 时加 `--fit`，用闭环自己的拟合文件（就是 `design-compile --fit` 用的那个）；闭环的命令行不变。

## 允许改的文件

前言 `allowed_files`。`orchestrator.py`、`edits.py` 正由 P8-fix2 改着，本包不碰。

## 禁止改的文件

前言 `forbidden_files`。

## 先写的测试

前言 `tests_first`，先提交（提交信息 `P1-edge: tests first`），在基线提交上必须失败。用测试里画的小图：一个编造的相机和拟合，两份渲染（完整的，和删掉轮廓上一块的），原图就用完整渲染加一点噪声。断言：区域是拟合长方体投影的凸包加膨胀（H 不是拟合值时到图像上边）；两份渲染算出的区域一样；删掉轮廓上那块的渲染，边缘距离不比完整的小；`score.py` 没有 `--fit`、相机不同、图像大小不同时退出码 2 且不写文件；`score_detail` 有 `render_outside_region`；闭环的 renderer 调 `score.py` 时带 `--fit`。已有测试按新规则需要改的，只改调用方式（加 `--fit`），不放松断言。

## 验收命令

前言 `acceptance_cmds`，全部退出码 0。

## 完成的定义

1. 先写的测试和验收命令全部通过；`git diff` 只含 `allowed_files`。
2. 指挥官合并前做两件事（不由工人做）：用本包的 `score.py --fit output/poc/ceiling/pic_A/box_fit.json` 重算 `output/poc/anchor1/pic_A_ceiling_official/` 的六个变体（复用渲染和面板），跑 `rank_check.py`，这是 G1 第一次重跑；用 `truth_fit.json` 按新规则重评锚点二已有各轮的候选，任何一轮胜者变了就按计划第 (5) 条报决策人。
3. 锚点二第四次跑的代码快照在本包合并之前取（第四次跑的评分必须和第三次相同）。

## 验证者清单

1. 白名单；`orchestrator.py`、`edits.py`、`selfplay.py`、`rank_check.py`、`panel.py` 都没动。
2. 先红后绿。
3. 变异：把区域换回候选 alpha 膨胀，`test_deleting_a_part_on_the_outline_does_not_improve_the_edge_distance` 和 `test_fit_region_does_not_depend_on_the_candidate` 应变红；去掉相机比较，`test_score_requires_fit_and_refuses_a_camera_mismatch` 应变红；renderer 不传 `--fit`，`test_the_loop_renderer_passes_the_fit_to_score` 应变红。
4. 破坏性输入：拟合缺 `dims_m`、H 为 null、相机在长方体里面、凸包退化（所有角投影到一点或一条线）时不崩，按规则退出码 2 或给出合理区域并写明。
5. 用主工作区的 `output/poc/ceiling/pic_A/box_fit.json` 和 `output/poc/anchor1/pic_A_ceiling_official/B1/` 只读地试算一次，确认能出分；不写 `output/`。
