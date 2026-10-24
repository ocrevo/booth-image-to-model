# Knowledge notes / 展台知识库

Reference material for choosing parameters, booth types and assets. No client pictures here (they belong in git-ignored `input/`;
results in `output/`).

| Folder | Content | Status |
|---|---|---|
| [`research/`](research/) | Notes collected from public sources, each row with a link and an access date | **Not reviewed by a designer.** Not defaults; check the source before use |

Values only become defaults of the tool (`booth-image-to-model/references/design-priors.md`, `schemas/design-priors.v1.json`) after
a person has reviewed them.

| Note | Covers |
|---|---|
| [`dimensions-reference.md`](research/dimensions-reference.md) | System stands, boards, TVs and LED panels, furniture, ergonomics; standard parts that anchor scale |
| [`reconstruction-tooling.md`](research/reconstruction-tooling.md) | Line and vanishing-point, monocular depth and segmentation tools for single-image reconstruction; faster CPU rendering |

Local, private material (a studio's own rules, past projects, venue manuals, licensed assets) can be kept next to this folder in
`studio/`, `projects/`, `venues/` and `assets/`; these folders are git-ignored so they are never published by accident.

---

中文：这里是查参数、选展位类型和挑资产时用的参考资料，不放客户原图。`research/` 的数值来自公开来源，**未经设计师审核**，不是工具默认值；经人审核后才会进入 `design-priors`。工作室自己的规则、历史项目、展馆手册和资产请放在 `studio/`、`projects/`、`venues/`、`assets/`，这些目录已被 git 忽略，不会被误发布。
