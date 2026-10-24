# Writing a booth design (design.json)

You design a NEW exhibition booth. Inputs: the reference card (style and layout taken from the designer's reference
picture), the designer's brief, and the form (footprint W x D in metres, height limit, open sides). Output: one
`design.json` that follows `schemas/booth-design.v1.json`. Start from `templates/design_template.json`. A compiler turns
it into the 3D model; you never write coordinates of parts, only elements and where they go.

## Precedence
1. The **form** is fact: `form.footprint_m`, `form.height_limit_m`, `form.open_sides`. Copy it; never change it. When the
   designer says the open sides are "the same as the picture", copy them from the card.
2. The **brief** wins over the picture: what it adds, removes or changes is done exactly.
3. The **reference card** fills everything the brief does not mention: booth layout, header shape, hero, palette,
   materials, mood.
4. **Priors** (generic defaults) only for what neither gives. Leave a size null and the compiler takes the default.

## Provenance (every value says where it comes from)
`brief` = the designer said it; `reference` = taken from the picture; `prior` = a generic default; `choice` = your design
decision. Fill `provenance` on every element (`presence`, `placement`, `size`, `material`, `text`) and on `style` and
`layout`. Defaults the compiler fills are recorded by it as `prior` or `choice` in the spec's uncertainties.

## Text
Only text written in the brief may appear, copied exactly (case and spelling) into `text.body`, with
`provenance.text: "brief"`. The compiler refuses any other text. Never copy text, logos or slogans from the picture, never
invent a name, slogan or certification. No name in the brief: no text at all.

## Axes and placement
Metres; the footprint is centred on the origin; X right, Y towards the back, Z up. `front` is the -Y side (the main aisle),
`back` +Y, `left` -X, `right` +X. Walls are built automatically on every side that is not open; island booths have none.

Each element is placed with words, the compiler does the metres:
- `side`: the side it stands on or hangs on (`centre` for free-standing in the middle).
- `along`: 0..1 along that side. Front and back: 0 = the left (-X) end, 1 = the right end. Left and right: 0 = the front
  end, 1 = the back end. Default 0.5.
- `offset_m`: distance from the wall face (closed side) or from the edge (open side) into the booth. Default 0 at a wall,
  0.3 at an open edge.
- `centre_m`: [x, y] in metres instead, for free-standing things (still give `side` for which way it faces, or `yaw_deg`).
- `z_m`: height of the bottom above the booth floor, for things on walls (default: graphic and TV centred at 1.6 m).
- `size_m`: [along the side, away from the side, height]; null entries take the default.
- Parts are clamped inside the footprint and under the height limit; overlaps are reported, so move one when told.

## Element kinds (`kind`)
| kind | what | notes and defaults |
|---|---|---|
| `feature_wall` | the main wall surface: `params.variant` `slats` (vertical slats on a backing) or `panel` | closed side only; full wall length and height unless `size_m` says; `params.pitch_m` 0.10, `slat_width_m` 0.04 |
| `graphic_panel` | a graphic board; with `material.emissive: true` a lightbox; may carry brief text | closed side; 1.2 x 0.8 m, centred at 1.6 m |
| `tv` | a screen on a wall, `params.inch` 43/50/55/60/65/75/85 | closed side; centred at 1.6 m |
| `led_wall` | LED wall of whole modules, `params.module_m` 0.5 | closed side; 3 x 2 m from 0.5 m up |
| `wall` | restyles the perimeter wall of `side` (colour, finish, height via `size_m[2]`) | ignored on an open side |
| `header_band` | a fascia band at the top along `params.sides` (default: the open sides); brief text goes on its outer face at the entrance; `material.emissive` = backlit | 0.4 m deep, about 0.5-0.6 m high, top at the wall top or the limit; posts are added at free ends |
| `ring` | a suspended frame inside the footprint; posts are added unless `form.hanging_allowed` is true | inset 0.5, band 0.4, 0.3 high, top at the height limit - 0.1 |
| `spotlights` | `count` downlights along `side`, hung under the header band or ring | |
| `counter` | reception counter; `params.floating: true` = recessed dark plinth; may carry brief text on its front | 1.8 x 0.55 x 1.05; faces the aisle when on an open side |
| `round_table`, `chair` | single pieces; `yaw_deg` turns a chair (0 faces the front) | table r 0.45, h 0.74 |
| `seating_group` | a round table with `count` chairs around it | count 2 |
| `planter` | `count` planter boxes in a row along the side, `pitch_m` apart | 0.4 x 0.4 x 0.45 each |
| `display_plinth` | a product plinth with a top | 0.5 x 0.5 x 1.0 |
| `storage_block` | a closed storeroom or cabinet block | 1.5 x 1.2, up to 2.5 m |
| `partition` | a free-standing wall, parallel to its side | 2.0 x 0.1, wall height |
| `box` | anything else as a plain box (`params.role` names it) | 0.6 cube |

`material`: `role` dominant / secondary / accent (the style palette) or `color_hex`; `finish` matte, satin, gloss, metal,
glass, wood, fabric; `emissive` for glowing surfaces. Mark exactly one element `hero: true` (the main visual).

## Composition (what makes it look designed)
- One hero, on the side facing the entrance (usually the back wall), big enough to read from the aisle.
- Palette by area: dominant about 60 % (walls, floor), secondary about 30 % (hero surface, counter), accent about 10 %
  (lights, text, trims). At most three materials.
- Reception counter near the entrance, at least 0.3 m in from the open edge, facing the aisle.
- Keep a clear path of at least 1.2 m from the entrance into the booth; seating and storage away from it, storage in a
  back corner.
- Header band proportion: about 0.5 to 0.6 m high on a 3 m booth; keep 2.4 m clear under it.
- Fewer, well-placed elements beat many: about 6 to 9 on a small stand (around 18 m²), 10 to 14 on a large one (around 50 m²).
- Follow the reference's mood: warm and natural = timber and greenery; premium = darker palette, backlit accents;
  technical = LED and screens, cool palette.

## Requirements (the brief, checked after the compile)
Split the brief into atomic items in `requirements[]`, each with `quote` (the brief's words), `priority` (`must` when the
brief insists, else `should`) and one `kind`:
- `must_have`: `element_kind` (+ `side` when the brief says where), e.g. "LED wall at the back".
- `must_not`: `element_kind` that must not be there, e.g. "no hanging ceiling" -> `ring`.
- `count`: `element_kind` + `count`, e.g. "four chairs" (chairs in a seating group count).
- `text`: the exact text the brief wants on the booth.
The compile report says which are satisfied; fix the design until every `must` is.

## After the compile
Read `compile_report.json` (or the command's summary): `dropped` (an element could not be built, with the reason),
`warnings` (overlaps, nothing to hang from), `brief` (requirements not satisfied), `design_check` (advisory findings),
`assumed` and `choices` (defaults the compiler took). Fix the design and compile again; save each version as
`design_002.json`, `design_003.json`, ... so the designer can go back.
