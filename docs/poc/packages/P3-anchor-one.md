---
id: P3
title: 锚点一：六个弄坏的变体和排序检验
goal: 从一个基线 spec 造出六个已知好坏顺序的变体，检验评分卡和边缘距离的排序相关性
depends_on: [P1, P4, PT]
model: sonnet
lane: worker
allowed_files:
  - tools/poc/score/perturb_spec.py
  - tools/poc/score/rank_check.py
  - tools/poc/tests/test_perturb_spec.py
  - tools/poc/tests/test_rank_check.py
  - docs/poc/templates/anchor1-expected.json
forbidden_files:
  - booth-image-to-model/**
  - web/**
inputs:
  - docs/poc/03-scoring-spec.md 第 5 节
  - booth-image-to-model/scripts/camera_math.py（project 和相机轴）
  - 基线 spec 和 box_fit.json 的格式（booth-image-to-model/references/data-contract.md）
deliverables:
  - tools/poc/score/perturb_spec.py：--spec --fit --truth --map --out，写 B1 到 B6 六个 spec.json 和 expected_order.json
  - tools/poc/score/rank_check.py：--scores --expected --out，写 rank_check.json（两个 Spearman 值和排反的对）
tests_first:
  - test_px_to_metres_round_trips_through_project_within_2px
  - test_removed_parts_are_absent_and_others_untouched
  - test_variants_validate_with_validate_spec
  - test_spearman_known_orders
acceptance_cmds:
  - python -m unittest discover -s tools/poc/tests -p "test_perturb_spec*.py"
  - python -m unittest discover -s tools/poc/tests -p "test_rank_check*.py"
  - python tools/poc/score/perturb_spec.py --help
  - python tools/poc/score/rank_check.py --help
  - python tools/check_docs.py
  - python tools/check_lf.py
effort_h: 5
day: 2
stop_rule: 代码第 2 天中午交；渲染和打分由 Blender 通道在第 2 天下午和夜里完成
---

# P3 锚点一

## 目标

实现 [03 第 5 节](../03-scoring-spec.md#5-锚点一弄坏的渲染)。代码部分是工人做的；六个变体的渲染和打分由 Blender 通道在主工作区做，不属于本卡。

## 输入

- 基线 spec：第 2 天用 P4 的 `output/poc/baseline/pic_A/spec_work/spec.json`，第 4 天用 P5 的 C。工人测试时用 `booth-image-to-model/tests` 里已有的 spec 夹具或程序生成的最小 spec。
- `--map <json>`：答案卷清单 id 到 spec 部件 id 的映射，由 Blender 通道在跑之前手写，格式与 `score.py --map` 完全相同（`{"inventory_id": 名字或名字列表}`，名字是 spec 部件 id 或 group 名），同一个文件也传给 `score.py --map`；`hero` 标记来自答案卷。
- `--fit`：`box_fit.json`，取相机。

## 交付物

`perturb_spec.py` 按 03 的表生成 B1 到 B6：B1 换一个 `accent` 色块对应部件的颜色（ΔE 约 15）；B2 一个非主件移动 20 px；B3 一个主件移动 60 px；B4 删一个非主件；B5 删一个主件；B6 删一个主件加两个非主件并交换主色和辅色。像素到米：部件中心沿相机前向轴的深度 Z，像素焦距 f_px，`d_m = px × Z / f_px`，沿投影最接近图像 x 方向的地面轴移动。每个变体写到 `<out>/B<n>/spec.json`，另写 `expected_order.json`（`["B1","B2","B3","B4","B5","B6"]`）。

`rank_check.py` 读 `<scores>/B<n>/score.json`（只收 `status` 为 `measured` 的；任何一个变体不是 `measured` 就退出码 2 并写出是哪个，不拿只含代码分的总分算相关），对 `total` 和 `aux.edge_distance_diag` 各算与预期顺序的 Spearman（边缘距离越小越好，取负号后比较），写 `rank_check.json`：两个系数、是否都 >= 0.8、排反的对。

`docs/poc/templates/anchor1-expected.json`：预期顺序和每个变体的一句说明。

## 允许改的文件

前言 `allowed_files`。

## 禁止改的文件

skill 本体（只 `import` 它的 `camera_math` 和 `validate_spec`）。

## 先写的测试

前言四个测试先提交。`validate_spec` 对六个变体都必须通过。

## 验收命令

前言 `acceptance_cmds`。

## 完成的定义

对夹具 spec 生成六个合法变体，位移经重新投影在 20 ± 2 px 内；`rank_check.py` 对手写的分数夹具给出正确系数。

## 验证者清单

1. 白名单。
2. 先红后绿。
3. 变异：把 `d_m` 公式里的 Z 去掉，`test_px_to_metres_*` 应变红。
4. 破坏性输入：映射里指向不存在的部件必须报错退出码 2；主件少于 1 个时拒绝生成 B5、B6 并说明。
