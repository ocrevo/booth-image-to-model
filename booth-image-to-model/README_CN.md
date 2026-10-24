[English](README.md) · **简体中文**

# booth-image-to-model（agent skill）· v3.1.0-rc7

一个 agent skill：从一张展台照片或效果图生成可编辑的参数化三维模型（JSON spec）、Blender 场景，以及去掉展厅、品牌和人物的客户渲染图。
agent 看图并判断哪些属于展台；脚本负责拟合相机、构建几何、校验和渲染。尺寸是从单张图估计的，不是实测值。

agent 按 [`SKILL.md`](SKILL.md) 执行（英文）；本页给安装的人看。详细规则和格式在 `references/`（中文）。

## 需要

| | |
|---|---|
| 能看图、能运行命令的 agent | Claude Code、Claude Agent SDK、OpenAI Codex、Cursor，或任何能读 `SKILL.md` 这类说明文件的 agent |
| Python 3.10+（已测 3.12） | `requirements.txt` 里的依赖包（numpy、Pillow、SciPy、jsonschema、trimesh），装在虚拟环境里（见“安装”） |
| Blender 4.0+（已测 5.2.2），用于 `.blend` 和渲染 | 通过 `--blender`、`BLENDER_EXE`、`PATH` 或常见安装位置查找 |

没有 Blender 时仍可产出观察记录、spec、GLB 和代码；渲染和 `.blend` 等装好 Blender 再做。

## 安装

从克隆的仓库获取本目录（正式发布后，每个版本会附带一个只含运行时文件、不含测试和验证证据的 zip）：

```bash
git clone https://github.com/ocrevo/booth-image-to-model.git
```

**Claude Code**：把目录复制（或软链接）到 skills 目录：所有项目通用放 `~/.claude/skills/`，单个项目放 `<项目>/.claude/skills/`。

```bash
mkdir -p ~/.claude/skills                                   # 单个项目则用：mkdir -p .claude/skills
cp -r booth-image-to-model/booth-image-to-model ~/.claude/skills/
python3 -m venv .venv && . .venv/bin/activate              # Windows：py -3 -m venv .venv ; .venv\Scripts\activate
python -m pip install -r ~/.claude/skills/booth-image-to-model/requirements.txt
```

请在已激活这个 venv 的终端里启动 agent（或告诉 agent 用哪个 `python`），skill 的命令才能找到这些依赖包。

**其他 agent**（Codex、Cursor、Agent SDK 应用或自建 agent）：目录放在任意位置，让 agent 读 `booth-image-to-model/SKILL.md`，例如在
`AGENTS.md` 里写：“处理展台图片时，按 `<路径>/booth-image-to-model/SKILL.md` 执行。”

**不用 agent**：每一步都是普通命令（`scripts/boothctl.py --help`），人也可以照 `SKILL.md` 操作。

## 检查安装

```bash
python ~/.claude/skills/booth-image-to-model/scripts/boothctl.py doctor
```

它列出 Python 包和找到的 Blender，缺什么就提示下一步。然后在图片所在目录对 agent 说：“把 input/booth.jpg 生成三维模型和干净的渲染图。”
结果写到该目录的 `output/<任务>/`。

## 环境变量

| 变量 | 用途 |
|---|---|
| `BLENDER_EXE` | Blender 可执行文件路径（或 macOS 的 `Blender.app`） |
| `BOOTH_SKILL` | 本目录路径，供从 `templates/` 复制出去的 spec 脚本查找 skill |

## 目录内容

| 路径 | 内容 |
|---|---|
| `SKILL.md` | agent 执行的说明 |
| `scripts/` | 引擎：`boothctl.py`（doctor、compile、preview、build、verify、glb、overlay、design-check）、`box_fit.py`、`camera_math.py`、`components.py`、`validate_spec.py`、Blender 脚本 |
| `schemas/` | JSON schema：spec（`booth-spec-v2`）、场景描述、设计先验 |
| `references/` | 详细规则和格式：观察协议、数据契约、场景描述、构件 recipes、设计先验、重建 QA |
| `templates/` | spec 脚本模板和 `design_template.json`（设计路线） |
| `prompts/` | 设计路线：怎样把参考图读成卡片、怎样写新设计 |
| `tests/`、`examples/`、`validation/`、`FILE_MANIFEST.json`、`release_status.json` | 测试、合成示例和发布证据（运行时不需要；运行时 zip 不含它们，只保留 `release_status.json` 和 `examples/regression/annotations.template.json`） |

版本记录见 [CHANGELOG.md](CHANGELOG.md)。许可证：[Apache-2.0](LICENSE)。
