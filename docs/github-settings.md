# GitHub settings (for the maintainer)

Text to paste into the repository settings; these cannot be set from a commit.

**Description** (≤ 350 characters):

> booth-image-to-model turns one exhibition-booth photo or render into an editable parametric 3D model (a JSON spec), a Blender scene and clean client renders — as an AI-agent skill or a local web studio. Works with Claude Code, Codex, Cursor or any OpenAI-compatible vision model. Apache-2.0.

The first sentence is the project's canonical definition; `tools/check_docs.py` checks that it matches `README.md` and `llms.txt`.

**Topics:** `exhibition-booth` `trade-show` `booth-design` `image-to-3d` `3d-reconstruction` `parametric-modeling` `blender`
`agent-skill` `claude-skills` `llm` `three-js` `gltf`

**Website:** leave empty until a public page exists.

**Social preview:** `docs/images/studio-demo.png` (synthetic), cropped to 1280 × 640.

**Security:** enable *Private vulnerability reporting* (Settings → Code security), which `SECURITY.md` points to.

**Actions:** allow GitHub Actions so `.github/workflows/ci.yml` runs on pushes and pull requests.

**Repository name:** `booth-image-to-model` (renamed from `Booth2CAD`; GitHub redirects the old URL). The URLs in `README.md`,
`README.zh-CN.md`, `llms.txt`, `CITATION.cff`, `CONTRIBUTING.md`, `web/package.json`, both skill READMEs and
`.github/ISSUE_TEMPLATE/config.yml` use the new name; `tools/check_docs.py` checks the `llms.txt` links against it.
