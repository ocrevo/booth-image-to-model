**English** · [简体中文](README_CN.md)

# booth-image-to-model (agent skill) · v2.10.0-rc1

An agent skill that turns one exhibition-booth photo or render into an editable parametric 3D model (a JSON spec), a Blender scene
and clean client renders without the hall, brands or people. The agent looks at the picture and decides what belongs to the booth;
the scripts fit the camera, build the geometry, validate it and render. Dimensions are estimates from one picture, not measurements.

[`SKILL.md`](SKILL.md) is what the agent follows. This page is for the person installing it.

## Requirements

| | |
|---|---|
| An agent that can look at images and run shell commands | Claude Code, the Claude Agent SDK, OpenAI Codex, Cursor or any agent that reads a `SKILL.md`-style instruction file |
| Python 3.10+ (3.12 tested) | the packages in `requirements.txt` (numpy, Pillow, SciPy, jsonschema, trimesh), installed in a virtual environment (see Install) |
| Blender 4.0+ (5.2.2 tested), for `.blend` files and renders | found through `--blender`, `BLENDER_EXE`, `PATH`, or the usual install folders |

Without Blender the skill still produces the observations, the spec, a GLB and code; renders and `.blend` files wait for Blender.

## Install

Get the folder from a clone of the repository (once releases are published, a runtime-only zip without tests and evidence will be attached to each):

```bash
git clone https://github.com/ocrevo/booth-image-to-model.git
```

**Claude Code**: copy (or symlink) the folder into a skills directory: `~/.claude/skills/` for all your projects, or
`<project>/.claude/skills/` for one project.

```bash
mkdir -p ~/.claude/skills                                   # one project instead: mkdir -p .claude/skills
cp -r booth-image-to-model/booth-image-to-model ~/.claude/skills/
python3 -m venv .venv && . .venv/bin/activate              # Windows: py -3 -m venv .venv ; .venv\Scripts\activate
python -m pip install -r ~/.claude/skills/booth-image-to-model/requirements.txt
```

Start the agent from a shell where this venv is active (or tell it which `python` to use), so the skill's commands find the
packages.

**Other agents** (Codex, Cursor, an Agent SDK app, your own): put the folder anywhere and point the agent at
`booth-image-to-model/SKILL.md`, for example in your `AGENTS.md`: *"For booth pictures, follow `<path>/booth-image-to-model/SKILL.md`."*

**Without an agent**: every step is a plain command (`scripts/boothctl.py --help`); a person can follow `SKILL.md` too.

## Check the install

```bash
python ~/.claude/skills/booth-image-to-model/scripts/boothctl.py doctor
```

It lists the Python packages and the Blender it found, and prints what to do next when something is missing. Then ask your agent,
from the folder where your picture is: *"Turn input/booth.jpg into a 3D model and a clean render."* Results go to `output/<job>/` in
that folder.

## Environment variables

| Variable | Use |
|---|---|
| `BLENDER_EXE` | Path of the Blender executable (or a macOS `Blender.app`) |
| `BOOTH_SKILL` | Path of this folder, for spec scripts copied out of `templates/` |

## What is in the folder

| Path | What |
|---|---|
| `SKILL.md` | The instructions the agent follows |
| `scripts/` | The engine: `boothctl.py` (doctor, compile, preview, build, verify, glb, overlay, design-check), `box_fit.py`, `camera_math.py`, `components.py`, `validate_spec.py`, Blender scripts |
| `schemas/` | JSON schemas: the spec (`booth-spec-v2`), the scene description, design priors |
| `references/` | Detailed rules and formats (Chinese): observation protocol, data contract, scene description, component recipes, design priors, QA |
| `templates/` | A spec script template |
| `tests/`, `examples/`, `validation/`, `FILE_MANIFEST.json`, `release_status.json` | Tests, a synthetic example and release evidence (not needed at run time; the runtime zip leaves them out except `release_status.json` and `examples/regression/annotations.template.json`) |

Releases and their evidence: [CHANGELOG.md](CHANGELOG.md). License: [Apache-2.0](LICENSE).
