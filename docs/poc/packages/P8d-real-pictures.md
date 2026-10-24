---
id: P8d
title: 三张真实图过闭环
goal: 不靠人工，闭环在 pic_A 到 pic_C 上逼近 C′
depends_on: [P8c, P5b, P8-catalog, P8-kinds]
model: sonnet
lane: blender
allowed_files:
  - output/poc/loop/**
forbidden_files:
  - "*"
inputs:
  - output/poc/ceiling/<pic>/box_fit.json（相机；P5 用过的拟合，P5 没重拟合时由指挥官从 output/poc/baseline/<pic>/ 复制过来）
  - output/poc/truth/<pic>/
  - output/poc/ceiling2/<pic>/score.json（C′ 的分数）
deliverables:
  - output/poc/loop/<pic>/round_N/、best/、score.json、timing.json（由指挥官从 loop.json 的 elapsed_s 和每个 round.json 的 seconds 写出）
  - output/poc/loop/summary.md（每张图终分 / C′ 分数、用时）
tests_first: []
acceptance_cmds:
  - python tools/poc/loop/orchestrator.py --help
effort_h: 6
day: 8-9
stop_rule: 闭环自己按 `--max-minutes 10` 停；连同读图和最后的预览，每张图墙钟上限 15 分钟（01 的 G5 门槛）；超时就取当前最佳
---

# P8d 三张真实图

## 步骤

1. 每张图：`orchestrator.py --picture input/poc/<pic>.<ext> --fit output/poc/ceiling/<pic>/box_fit.json --truth output/poc/truth/<pic> --design-init none --catalog output/poc/catalog --out output/poc/loop/<pic> --max-rounds 4 --max-minutes 10`。读图器从家族目录填 `reader.v1`；读图器在第 1 轮就无效又没有初始设计（退出码 3）时，换一个新的 `--out` 文件夹、换一个全新的读图子 agent 重跑一次；再失败，这个图记为闭环失败，终分按 0 计入 G4；角色请求由指挥官服务；每轮渲染拿令牌。
2. 结束后对 `best/` 做一次完整 build 加 `--part-masks` 和 `--material-set projected`（夜里，留作记录）。打分用两张预览，不用完整 build 的渲染：中性版预览给 `score.py --run`（代码分和 L1 到 L3 的面板叠图；2026-10-23 起必须同时给 `--fit output/poc/ceiling/<pic>/box_fit.json`，见 03 第 3.1 节第 3 条），投影版预览（`preview --material-set projected`）给 `score.py --projected-run`（L4 的面板叠图，见 03 第 4 节第 2 条）；L4 的代码分只按中性版算。面板打分，`score.py` 写 `score.json`（2026-10-23 补充：完整 build 只写 `reference_render.png` 和 `client_render.png`，`score.py` 读不了，按投影版算色差又会让分数虚高）。
3. `summary.md`：每张图终分、C′ 分数、比值、用时、轮数。

## 完成的定义

三张图都有 `best/` 和 `score.json`；`summary.md` 给出 G4 真实部分的 `value`（三张比值的最小值）和最大用时。

## 验证者清单

指挥官检查：闭环没有读答案卷（`score.py` 以外的进程没有打开 `truth/`）；用时按墙钟记录。
