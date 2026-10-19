**English** · [简体中文](README.zh-CN.md)

# booth-image-to-model

**booth-image-to-model turns one exhibition-booth photo or render into an editable parametric 3D model (a JSON spec), a Blender scene
and clean client renders — as an AI-agent skill or a local web studio.**

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)
![Blender 4.0+](https://img.shields.io/badge/blender-4.0%2B-orange.svg)

![The web studio with its demo booth: on the left a synthetic picture rendered from the demo model, on the right the same editable 3D model at that camera](docs/images/studio-demo.png)

*The studio's demo booth: the synthetic picture (left of the line) is rendered from the demo model at its own camera, so the editable model
(right) lines up exactly. With a real picture the camera and sizes are an estimated fit. (Synthetic pictures made by this project.)*

## What it does

- **In:** one picture of a trade-show stand (a designer's render or a photo).
- **Out:** a parametric model in metres (`spec.json`: floor, walls, fascias, portals, counters, furniture, screens, lights, each
  part editable), a `.blend` file, a clean client render without the hall, neighbouring stands, logos or people, a second view and a
  GLB for any 3D viewer.
- **How:** an agent or a large model reads the picture (corner points, parts, colours); the code fits the camera and scale from the
  corners, unprojects every part into metres, validates the model and renders it in Blender. You then adjust parts in a browser
  editor and render again.
- **Not CAD:** sizes are estimates from one picture, every assumption is recorded, and nothing is construction-grade.

## Two ways to use it

| | **A. Agent skill** | **B. Web studio** |
|---|---|---|
| For | people who work with a coding agent (Claude Code, Codex, Cursor, an Agent SDK app) | designers and sales staff who want a window and buttons |
| What reads the picture | your agent, following [`SKILL.md`](booth-image-to-model/SKILL.md) | any OpenAI-compatible model you configure, or a file you upload |
| Needs | Python 3.10+, the packages, Blender 4.0+ for renders | Python 3.10+, the packages, a browser; Blender 4.0+ for renders; a key for automatic reading |
| Read | [booth-image-to-model/README.md](booth-image-to-model/README.md) | [web/README.md](web/README.md) |

### A. Use the skill in your agent

```bash
git clone https://github.com/ocrevo/booth-image-to-model.git
python3 -m venv .venv && . .venv/bin/activate              # Windows: py -3 -m venv .venv ; .venv\Scripts\activate
python -m pip install -r booth-image-to-model/booth-image-to-model/requirements.txt
mkdir -p ~/.claude/skills
cp -r booth-image-to-model/booth-image-to-model ~/.claude/skills/          # Claude Code; other agents: point them at SKILL.md
python ~/.claude/skills/booth-image-to-model/scripts/boothctl.py doctor   # lists packages and the Blender it found
```

Start the agent from a shell where this venv is active (or tell it which `python` to use), so the skill's commands find the
packages. Then, in the folder with your picture, ask your agent: *"Turn input/booth.jpg into a 3D model and a clean render."*

### B. Run the web studio

```bash
git clone https://github.com/ocrevo/booth-image-to-model.git && cd booth-image-to-model
python3 -m venv .venv && . .venv/bin/activate              # Windows: py -3 -m venv .venv ; .venv\Scripts\activate
python -m pip install -r booth-image-to-model/requirements.txt
python web/server/run.py                                  # opens http://127.0.0.1:8800/
```

No key yet? Click **Try the demo booth** on the empty stage: a synthetic booth and its model load at once. For automatic
reading set `OPENAI_API_KEY` (OpenAI, or `--provider deepseek` for DeepSeek, or any OpenAI-compatible service). The studio and the standalone editor
(`python web/tools/serve.py`) are in English.

## Requirements

| | Version | Needed for |
|---|---|---|
| Python | 3.10+ (3.12 tested) | everything |
| numpy, Pillow, SciPy, jsonschema, trimesh | see [requirements.txt](booth-image-to-model/requirements.txt) | everything |
| Blender | 4.0+ (5.2.2 tested) | `.blend` files and renders; found through `BLENDER_EXE`, `PATH` or the usual install folders |
| A browser | current Chrome, Edge, Firefox or Safari | the studio and the editor |
| A key for an OpenAI-compatible service that reads images | optional | automatic reading in the studio |
| Node.js | 22.15+ | only the web tests |

Tested on Windows 10. The code supports Linux and macOS (paths, Blender lookup, Python launcher), and a CI workflow runs the tests
there without Blender; full native runs on Linux and macOS are not yet part of the release evidence.

## How it works

```
picture ──► scene description ──► camera + scale fit ──► parametric spec ──► Blender build ──► client render, .blend, second view
 (agent or       (pixels and          (corner points,        (metres, parts,      (fresh process,     + GLB, + browser editor
  large model)    plain numbers)       box_fit)               validated)           reopened to check)
```

The spec ([booth-spec-v2 schema](booth-image-to-model/schemas/booth-spec-v2.schema.json)) is the single source of truth; the browser
editor, the GLB export and the Blender build all read it. The scene description
([schema](booth-image-to-model/schemas/scene-description.v1.json)) is the hand-off between whatever reads the picture and the
compiler, so any model, agent, script or person can do the reading.

## Limits

- One picture: hidden sides, exact sizes and materials are estimated and marked as such.
- Reading quality depends on the reader (agent or model); the camera fit and the overlay show when a reading is off.
- No drawings yet (plans and elevations are planned). Text and grid patterns are drawn schematically in the browser.
- Pictures over 5 MP or with an EXIF rotation tag are refused by the studio.

## FAQ

**Is this a CAD tool?** No. It rebuilds a booth's shape and look from one picture. Dimensions are estimates, recorded as assumptions,
and not suitable for construction or a bill of materials without checking.

**Which AI models work?** For the skill: any coding agent that can look at images and run commands (tested with Claude Code). For the
studio: any OpenAI-compatible service whose model accepts images; presets exist for OpenAI and DeepSeek, and base URL and model are
configurable.

**Do I need Blender?** Only for `.blend` files and renders. Reading, compiling the model, the browser editor and the GLB export work
without it.

**Is my picture uploaded anywhere?** The skill makes no network calls. The studio sends a picture only to the large-model service you
configured, only after you tick the consent box for that picture; everything else stays on your computer. The standalone editor
uploads nothing. See [SECURITY.md](SECURITY.md).

**Can I use it with Claude Code, Codex or Cursor?** Yes. Copy `booth-image-to-model/` into Claude Code's skills folder, or point any
other agent at its `SKILL.md`.

**What license?** Apache-2.0. Third-party components are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Documentation

| I want to… | Read |
|---|---|
| install the skill in an agent | [booth-image-to-model/README.md](booth-image-to-model/README.md) |
| know what the agent does, step by step | [booth-image-to-model/SKILL.md](booth-image-to-model/SKILL.md) |
| run the studio or the editor, configure a model | [web/README.md](web/README.md) |
| write a scene description by hand | [references/scene-description.md](booth-image-to-model/references/scene-description.md) (Chinese) |
| understand the spec format | [references/data-contract.md](booth-image-to-model/references/data-contract.md) (Chinese) and the [schema](booth-image-to-model/schemas/booth-spec-v2.schema.json) |
| understand the design and the plan to make reading more accurate and faster | [docs/design-review-2026-10.md](docs/design-review-2026-10.md) |
| measure a version of the skill against the recorded baseline | [tools/eval/README.md](tools/eval/README.md), [BASELINE.md](tools/eval/BASELINE.md) |
| contribute | [CONTRIBUTING.md](CONTRIBUTING.md), [AGENTS.md](AGENTS.md) for coding agents |
| report a security problem | [SECURITY.md](SECURITY.md) |
| see what changed | [CHANGELOG.md](booth-image-to-model/CHANGELOG.md) (Chinese) |

**Glossary.** *Skill*: the folder `booth-image-to-model/` an agent follows. *Studio*: the local web app (`web/server/run.py` +
`studio.html`). *Editor*: the standalone browser editor (`web/app/index.html`). *Spec*: the parametric model (`spec.json`).
*Scene description*: what was read from the picture, in pixels and plain numbers (`scene.json`).

## Project

The repository was renamed from `Booth2CAD` to `booth-image-to-model`; old links redirect.
Version 2.10.0-rc1. Contributions are welcome under the [Apache License 2.0](LICENSE); please read [CONTRIBUTING.md](CONTRIBUTING.md)
first and never attach client pictures. To cite the project, use [CITATION.cff](CITATION.cff).
