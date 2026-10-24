---
id: P8c
title: 锚点二完整跑：合成自博弈接真实角色
goal: 在三个真值已知的合成场景上跑完整闭环，拿到逐轮误差曲线
depends_on: [P6, P8a, P8b]
model: opus
lane: blender
allowed_files:
  - output/poc/anchor2/**
forbidden_files:
  - "*"
inputs:
  - docs/poc/03-scoring-spec.md 第 6 节
  - tools/poc/loop/selfplay.py、orchestrator.py、roles.py
deliverables:
  - output/poc/anchor2/<case>/picture.png、truth、loop/round_N/、curve.json、curve.png
  - output/poc/anchor2/summary.md（三个 case 的曲线和判定）
tests_first: []
acceptance_cmds:
  - python tools/poc/loop/selfplay.py --help
  - python tools/poc/loop/orchestrator.py --help
effort_h: 6
day: 8
stop_rule: 一个 case 跑不收敛先记录，继续下一个；三个都跑完再判
---

# P8c 锚点二完整跑

## 步骤

0. 家族目录（只做一次，P8d 共用）：`python tools/poc/loop/catalog.py --out output/poc/catalog --render-root output/poc/catalog_render --commands`，按它打印的命令在 Blender 通道逐个渲染缩略图（每次预览前后拿放令牌），再跑一次 `catalog.py --out output/poc/catalog --render-root output/poc/catalog_render` 把缩略图收进目录；目录放 `output/`，不入库。
1. `python tools/poc/loop/selfplay.py prepare --out output/poc/anchor2 --cases 3 --seed 1`。
2. 每个 case：拿令牌，`selfplay.py render --case output/poc/anchor2/<case> --out output/poc/anchor2/<case>`，释放；再 `selfplay.py truth-sheet --case output/poc/anchor2/<case> --out output/poc/anchor2/<case>/truth`。
3. 每个 case：`orchestrator.py --picture <case>/picture.png --fit <case>/truth_fit.json --truth <case>/truth --design-init none --picture-id <case 名> --no-plateau --max-rounds 4 --max-minutes 150 --catalog output/poc/catalog --out <case>/loop`，相机直接取真值；关掉平台期规则、放宽时间，保证每个 case 都有第 1 到第 4 轮（03 第 6 节第 5 条）；读图器在第 1 轮就无效又没有初始设计（退出码 3）时，换一个新的 `--out` 文件夹、换一个全新的读图子 agent 重跑一次；再失败，这个 case 记"无法判断"并写进 `summary.md`；每轮的渲染拿令牌；角色请求由指挥官按 [02 第 6 节](../02-architecture-and-contracts.md#6-模型角色作为请求与响应文件) 服务。
4. 每轮胜者（路径取 `round.json` 的 `winner_spec`，它相对 `<case>/loop`）：`selfplay.py mask-spec` 写一份所有部件都标 `critical` 的 spec 副本，副本放在胜者 spec 的同一个文件夹（例如 `compile/spec_masks.json`），这样 spec 里原图的相对路径仍然有效，拿令牌做一次带 `--part-masks` 的完整 build（夜里），释放；再 `selfplay.py evaluate --case <case> --candidate <胜者 spec> --masks <part_masks 文件夹> --match nearest --out <case>/loop/round_N/evaluate.json`。最后 `selfplay.py curve --case <case> --rounds <case>/loop --out <case>/curve.json`。
5. 写 `summary.md`：三条曲线、每条是否"第 4 轮优于第 1 轮且没有一轮退步超过 10 %"、总用时。注明曲线图画的是每轮自己的中位误差，判定用的是两轮都匹配上的部件的中位数（03 第 6 节第 5 条），两者可能不同。

## 完成的定义

三个 case 都有曲线；`summary.md` 给出 G4 合成部分的判定。

## 验证者清单

指挥官检查：每轮的 `round.json` 都有胜者和分数；角色响应在 `round.json` 的 `responses` 里是 `ok`，或是 `no_response`、`invalid`（这两种这一轮按无法判断继续，日志里有原因；2026-10-23 改，与代码的状态名一致）。
