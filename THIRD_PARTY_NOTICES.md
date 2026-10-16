# Third-party notices

booth-image-to-model is licensed under the [Apache License 2.0](LICENSE). It ships one third-party library and uses a few others
that you install yourself.

## Included in this repository

| Component | Version | License | Where | Notes |
|---|---|---|---|---|
| [three.js](https://threejs.org/) | 0.186.1 | MIT | `web/app/vendor/three/` | Copied unmodified from the npm package; its license is in `web/app/vendor/three/LICENSE`, the source and checksum in `VERSION.txt` |

## Installed by you (not included)

| Component | Used for | License |
|---|---|---|
| [NumPy](https://numpy.org/) | geometry, camera fitting | BSD-3-Clause |
| [Pillow](https://python-pillow.org/) | reading and writing pictures | MIT-CMU (HPND) |
| [SciPy](https://scipy.org/) | camera fitting (least squares) | BSD-3-Clause |
| [jsonschema](https://github.com/python-jsonschema/jsonschema) | validating specs and scene descriptions | MIT |
| [trimesh](https://trimesh.org/) | GLB export and mesh checks in the tests | MIT |
| [Blender](https://www.blender.org/) | building `.blend` files and rendering | GPL-2.0-or-later |

Blender is run as a separate program (`blender --background --python <script>`); booth-image-to-model does not include or link
Blender's code. The scripts that run inside Blender use its Python API (`bpy`) and are distributed under the Apache License 2.0
together with the rest of this repository.

The pictures in `docs/images/` and `web/app/demo/demo_source.png` are synthetic: screenshots and a Blender render of the invented demo
booth made by this project, covered by the same Apache License 2.0.

The optional large-model reading step sends a picture to an OpenAI-compatible service that you choose and configure; that
service's own terms apply to what you send.
