---
id: P4
title: 基线：今天的管线跑三张图并打分
goal: 用今天的 skill，严格按 SKILL.md 的快速路线，给 pic_A、pic_B、pic_C 各出一个 spec 和预览，作为所有后续分数的起点
depends_on: []
model: sonnet
lane: blender
allowed_files:
  - output/poc/baseline/**
forbidden_files:
  - "*"
inputs:
  - booth-image-to-model/SKILL.md 第 0 节（快速路线）
  - input/poc/pic_A 到 pic_C
  - docs/poc/02-architecture-and-contracts.md 第 5 节（令牌）
deliverables:
  - output/poc/baseline/<pic>/benchmark/、points.json、box_fit.json、overlay.png、scene.json 或 build_spec.py、spec_work/spec.json、previews/preview_001/、timing.json
  - output/poc/baseline/<pic>/score.json（P1 合并后由指挥官跑）
tests_first: []
acceptance_cmds:
  - python booth-image-to-model/scripts/boothctl.py doctor
  - python booth-image-to-model/scripts/validate_spec.py --help
effort_h: 6
day: 1
stop_rule: pic_A 第 1 天必须完成（P3 第 2 天要它）；pic_B、pic_C 可以到第 2 天上午
---

# P4 基线

## 目标

记录"今天的管线"在三张图上的分数。由一个专用 Sonnet 子 agent 在主工作区执行（它就是第 1 天的 Blender 通道），指挥官不亲手建模。做法和一个按说明书办事的用户一样：只用 skill 的命令加手写 JSON，不写临时代码，不看答案卷。答案卷（PT）须在本包打分之前确认。

## 步骤（每张图）

1. 令牌只围着每一次 `preview` 拿和放（[02 第 5 节](../02-architecture-and-contracts.md#5-blender-令牌)），第 2 到 6 步不用 Blender、不持令牌。
2. `python booth-image-to-model/scripts/benchmark.py prepare --image input/poc/<pic>.<ext> --out output/poc/baseline/<pic>/benchmark`（输出目录必须是新的）。
3. 看图，写 `points.json`（8 到 10 个外角点，至少一个尺度锚），按 `box_fit.py` 文件头的格式。
4. `python booth-image-to-model/scripts/box_fit.py --points output/poc/baseline/<pic>/points.json --output output/poc/baseline/<pic>/box_fit.json`，要求 `status` 为 `fitted`；不是就按 SKILL.md 的提示重点角点，最多两次。
5. `python booth-image-to-model/scripts/boothctl.py overlay --fit ... --image ... --out output/poc/baseline/<pic>/overlay.png`，看一眼。
6. 写场景描述 `scene.json` 并 `boothctl.py compile --scene ... --image ... --out output/poc/baseline/<pic>/spec_work`，或复制 `templates/build_spec_template.py` 到 `output/poc/baseline/<pic>/build_spec.py` 填好后运行它。两条路线任选，记录选了哪条。
7. 每轮先 `python tools/poc/orchestration/blender_token.py acquire --wait 600 --holder P4 --purpose "baseline <pic>"`，再 `python booth-image-to-model/scripts/boothctl.py preview --spec output/poc/baseline/<pic>/spec_work/spec.json --out-root output/poc/baseline/<pic>/previews --client-look`，最多三轮，按 SKILL.md 修改。
8. 每次 `preview` 结束立刻 `python tools/poc/orchestration/blender_token.py release --holder P4`。每步的墙钟秒数写进 `timing.json`。
9. P1 合并后，指挥官跑 `score.py` 得 `score.json`（本卡不打分）。

## 规则

- 不读 `output/poc/truth/`。
- 不写 Python 代码，`build_spec.py` 只填模板里的数字和宏调用。
- 不改 skill 的任何文件；发现 bug 记日报。

## 完成的定义

三张图各有 `spec.json` 和至少一个预览文件夹；`timing.json` 齐全；`validate_spec.py --check-source` 对三个 spec 通过。

## 验证者清单

指挥官检查：`box_fit.json` 的 `status` 为 `fitted`；预览文件夹里有 `compare_sheet.png`；没有任何临时 `.py` 文件除了 `build_spec.py`。
