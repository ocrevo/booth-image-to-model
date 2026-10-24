---
id: P8-catalog
title: 家族目录补齐链 A 的新 kind：每个一行说明、一份放对地方的最小设计
goal: P8d 的读图器看到的家族目录里，canopy_frame、lit_panel、prop_block、shaped_counter、sweep_member 都有如实的参数说明和能编译的最小设计，缩略图由 Blender 通道补
depends_on: [P7-fam-sweep, P8b]
model: sonnet
lane: worker
allowed_files:
  - tools/poc/loop/catalog.py
  - tools/poc/tests/test_roles.py
forbidden_files:
  - tools/poc/prompts/**
  - tools/poc/loop/roles.py
  - tools/poc/loop/orchestrator.py
  - tools/poc/loop/edits.py
  - tools/poc/loop/selfplay.py
  - tools/poc/score/**
  - booth-image-to-model/**
  - docs/**
  - web/**
inputs:
  - docs/poc/02-architecture-and-contracts.md 第 1 节第 1 步（家族目录 = 每个家族一张引擎渲染的缩略图加参数说明）
  - booth-image-to-model/references/component-recipes.md 的"设计路线"各节（canopy_frame、lit_panel、prop_block，以及 counter、sweep 两个包加的节）
  - booth-image-to-model/schemas/booth-design.v1.json 里这五个 kind 的 params 和缺省值
  - tools/poc/loop/catalog.py 的 ABOUT、ELEMENT、minimal_design、build
deliverables:
  - catalog.py：ABOUT 给 schema 里每个 kind 一行自己的说明，不再有任何 kind 落到"see its minimal design"那句兜底话；新五行照 recipes 和 schema 如实写（尺寸、缺省值、放在哪、主要 params），不写引擎做不到的事
  - catalog.py：需要时给新 kind 加 ELEMENT 摆法，让最小设计把它放在该放的地方（挂墙的放在封闭边上，落地的放在地上，头顶的照 recipes），只写 id、kind 和摆法需要的字段，缺的值由编译器按缺省值补并记 assumed
  - 兜底话保留给以后新加、还没写说明的 kind，但测试钉住"当前 schema 的每个 kind 都有自己的一行"，以后加 kind 不补说明会变红
tests_first:
  - test_every_kind_in_the_schema_has_its_own_about_line
  - test_about_lists_no_kind_outside_the_schema
  - test_new_kinds_minimal_designs_compile_and_build_where_they_belong
acceptance_cmds:
  - python -m unittest discover -s tools/poc/tests -p "test_roles*.py"
  - python -m unittest discover -s tools/poc/tests -p "test_loop*.py"
  - python tools/check_docs.py
  - python tools/check_lf.py
effort_h: 1.5
day: 8
stop_rule: 做不完时先交五行说明和覆盖测试，摆法写进 notes
---

# P8-catalog 家族目录补齐

## 目标

闭环给读图器的家族目录（`catalog.py`）是 P8b 时写的。链 A 之后加了五个 kind：`canopy_frame`、`lit_panel`、`prop_block`、`shaped_counter`、`sweep_member`。`ABOUT` 里没有它们的说明，目录只给一句"A canopy_frame element; see its minimal design for the fields it uses."；它们也还没有缩略图。02 第 1 节第 1 步要求每个家族有参数说明和引擎渲染的缩略图。锚点二第三、四次跑用的是冻结目录 `output/poc/catalog_r3/`，不受本包影响；本包是给 P8d 用的新目录。

## 输入

前言 `inputs`。说明以 recipes 和 schema 为准；两者不一致时照 schema 写，并在 notes 里记下不一致的地方，不改 skill。

## 交付物

前言 `deliverables`。要点：

1. 一行一个 kind，英文，和现有各行的写法一致（尺寸、缺省值、放在哪、关键 params）。不写品牌、不写来自客户图的尺寸。
2. 最小设计照现有的规矩：4 × 3 m、只开前面、占位文字或固定句子；`build()` 现有的检查（`edits.validate_design`、编译器检查、编译后该 kind 的元素都建出来）对新 kind 同样成立。
3. 缩略图不由本包出。合并后指挥官重建目录，再由 Blender 通道按 `catalog.py --commands` 打印的命令补五张缩略图（每张预览拿令牌）。

## 允许改的文件

前言 `allowed_files`。

## 禁止改的文件

前言 `forbidden_files`。读图请求的字段和提示词不改；目录内容变了，读图请求随之变，这是预期的。

## 先写的测试

前言 `tests_first`，先提交（提交信息 `P8-catalog: tests first`），在基线提交上必须失败。断言：schema 的每个 kind 在 `ABOUT` 里有自己的一行，目录里没有任何条目是兜底话；`ABOUT` 的键都在 schema 里；五个新 kind 的最小设计能编译、该 kind 的元素建出来，而且放的位置对（挂墙件在封闭边上，落地件底面在地面，头顶件在占地之内、高度不超过限高）。已有测试一个都不削弱。

## 验收命令

前言 `acceptance_cmds`，全部退出码 0。

## 完成的定义

1. 先写的测试和验收命令全部通过；`git diff` 只含 `allowed_files`。
2. 五行说明逐条能在 recipes 或 schema 里找到依据（验证者核对）。
3. 合并后指挥官重建 `output/poc/catalog/`、补缩略图，P8d 才用它。

## 验证者清单

1. 白名单；提示词、`roles.py`、`orchestrator.py` 没动。
2. 先红后绿。
3. 变异：删掉任一新 kind 的说明，覆盖测试应变红；把一个挂墙件的摆法改成居中，位置测试应变红。
4. 逐行对照 recipes 和 schema：尺寸、缺省值、参数名都对得上，没有写引擎做不到的事。
