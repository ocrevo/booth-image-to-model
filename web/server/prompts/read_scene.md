You read ONE exhibition-booth render and describe what you see as a "scene description": a JSON object made only of pixel positions and plain numbers. A program turns it into a 3D model, so accuracy of positions matters more than detail. You are given two pictures of the same size: the picture itself and a copy with a labelled pixel grid (thin cyan lines every 50 pixels, or every 100 or 200 pixels on large pictures; yellow numbers are pixel coordinates, written along all four edges and, as `x,y`, at a few line crossings inside the picture, each marked with a small tick). Read every coordinate on the grid copy. Pixel coordinates are in the pictures you are given: origin top-left, x to the right, y downwards. Put those pictures' width and height in `image`.

Reply with the JSON object only, matching the response schema. Every key is required; write null where a field does not apply. Write "1.2" in `schema_version`.

## Booth type and open sides (decide this first)
Before you place any corner or part, look at which sides of the booth floor have no wall and write three keys FIRST in the JSON object, right after `schema_version` and `project_id`: `booth_type`, `open_sides`, `cutaway_sides`. A program later checks your corner points and parts against them. A wrong answer is found out, and a guess the picture does not support is worse than `null`.
- The four sides use the axes of the next section: `front` is the -Y side, `back` +Y, `left` -X, `right` +X. The corner points use the same words: a front corner has -D/2, a back corner D/2, a left corner -W/2, a right corner W/2. In a picture taken from the visitor side the front is usually the side nearest the camera. Choose the axes first if you have to, then name the sides with them.
- An open side is a side of the floor rectangle with no wall, where visitors walk in. A side with a wall, a solid panel or a screen wall along it is closed.
- `open_sides` lists the open sides, each side at most once. `booth_type` follows from how many are open:
  - `inline`: 1 open side.
  - `corner`: 2 open sides that meet at a corner (front and left, for example).
  - `peninsula`: 3 open sides.
  - `island`: 4 open sides.
  - `other`: anything else, such as 2 opposite sides open (front and back), no open side at all, or an irregular outline.
- A cutaway side is a side whose wall (or the roof) is only left out of the picture to show the inside, as in a cut-away render. It is not a real opening. List it in `cutaway_sides` and NOT in `open_sides`; `booth_type` counts only real openings. A wall on a cutaway side is allowed, and the picture may look in through it.
- Telling a cutaway from an opening: in a cutaway the walls beside the missing side end in a flat cut, the thickness of the floor or the walls shows at the cut, the roof or a ceiling band is sliced off or missing above an otherwise closed room, and nothing marks an entrance. A real opening has finished wall ends, often an entrance sign or a beam above it, a floor that carries on into the aisle, and people or the aisle visible through it. When you cannot tell which it is, say so in `uncertainties`.
- Write `[]` in `cutaway_sides` when there is none. Write `null` in `booth_type` and `open_sides` only when you really cannot tell (most of the booth is hidden, for example) and say why in `uncertainties`; the program then skips these checks. Never choose a type just to make the numbers agree.
- A `wall` component on a side that is listed in `open_sides` is left out of the model, so do not describe one there.

## Booth axes and dimensions
- The booth footprint is a rectangle centred on the origin. X runs to the right, Y towards the back, Z up (metres). W is the size along X, D along Y, H the height of the highest part (the top of the frame or walls).
- `known` holds W, D, H in metres. Put a dimension there only when the image states it (dimension lines, labels); that is a fact, so it does not go into `assumed`. Every dimension the image does not state stays null: the program works it out from your corner points.
- The program needs ONE metric anchor to get absolute size. If the image states no dimension, put exactly ONE plausible value into `known` and list its name in `assumed`. Prefer H (the top of the frame or walls; for example 3 for a booth of this look). At most ONE dimension is ever assumed: never W and D together, and never all three, because the fit would only confirm your own guesses.
- `floor_z` is the height of the walking surface in metres (0 for a carpet or plain floor, about 0.1 for a raised platform).
- Choose the axes so that the open front of the booth faces -Y when you can tell which side is the front. Otherwise call the nearest floor corner (W/2, -D/2) and name each dimension after the floor edge it runs along.

## corner_points (8 to 10)
- Pick clearly visible OUTER corners of the booth footprint box and of its top: for example the visible floor corners and the top corners of the frame above them. Prefer points far apart, near and far, and at least two at the top. Never invent a corner that is hidden: a missing point is better than a wrong one. If the picture shows fewer than 8 clear corners, give the clear ones, but at least 6: with fewer the program cannot check the camera. All of your points are used in the fit.
- `xyz` is three tokens, each one of: a number, `W/2`, `-W/2`, `D/2`, `-D/2`, `H`, `F` (the floor height). Example: a floor corner at the front right is ["W/2", "-D/2", "F"], the top corner above it ["W/2", "-D/2", "H"]. Do not use `?name` tokens.
- Together the points must use W/2 (or -W/2), D/2 (or -D/2) and H at least once each, so the program can work out every dimension you left null.
- `fit` is required on every point; write `null` (it means the point is used in the fit). Never write `false`: that would hold the point out of the fit.
- The program tests your clicks itself: it leaves each corner out in turn, predicts it from the others and compares. So spread the corners over the whole booth (near and far, floor and top, left and right), and never click the same corner twice: a repeated click would cover for its own mistake.
- Only use a point if it really is at that corner of the box. A point in the wrong place makes the model look wrong; place every point with the same care.

## scale_checks
- Add one for every upright standard part whose floor contact and top you can both see: a cafe table, a standing counter, a chair seat. `base_px` is the pixel where it meets the floor, `top_px` its highest point straight above, `expected_height_m` a typical height (cafe table 0.74, standing counter 1.05, chair seat 0.45), `tolerance` 0.15 (null uses the default); the id must differ from every corner point id. They do not change the fit; the program compares each with the size the model gives it, which tells whether your one assumed dimension is right.
- A `tv` hangs on a wall, so it is not a scale check: give its `inch` size class with care, it is compared with the marked rectangle. Leave `scale_checks` empty when no such part is visible.

## scopes
Use these scope ids: `shell` (walls, columns, roof frame, ribs), `furniture` (counters, tables, chairs, stools, planters), `media` (screens, signs, LED walls), `lighting` (downlights); all included, owner booth, with a one-line `evidence`. Add excluded scopes for what must NOT be built: `venue` (hall, aisle, neighbours, backdrop), `people` (owner neighbor), `brand_text` (logos, lettering, watermarks, dimension arrows; the carriers such as a sign panel or a wall ARE built). Leave a scope out of `components` rather than using it for nothing.

## components
Describe the main structures first, then furniture, then lights. Fewer, correct parts beat many guesses.
- `id`: lowercase letters, digits and underscores, unique. A macro type (counter, round_table, shell_chair, planter, louvre_wall, tv, led_wall, spotlights) builds several parts named `<id>_...`, so no other id may start with `<id>_`.
- `evidence`: "observed" when the part is visible (then `roi_px` = [x0, y0, x1, y1] around it), "inferred" for hidden or assumed parts (roi_px null).
- `color_hex`: the main colour read from the picture (#rrggbb).
- `base_px`: the pixel where the part meets the floor, at its centre. For `louvre_wall` and `wall`, `base_px` and `base_px_2` are the two ends of the row or wall on the floor.
- Floor-standing: counter (`size_m` [width, depth or null, height or null]), round_table (`radius_m` or null), shell_chair (`yaw_deg`, 0 faces -Y), planter, box and cylinder (`size_m` / `radius_m`, `z_range_m` or `height_m`), louvre_wall (`count`, `width_m`, height).
- On a booth edge (`plane` back/front/left/right): `wall` (`z_range_m` or height, `thickness_m`), `wall_panel` (`wall_rect_px` top-left then bottom-right, `thickness_m`), `tv` (`wall_rect_px` of the whole frame, `inch` one of 43/50/55/60/65/75/85), `led_wall` (back edge only; `wall_rect_px`, `count` columns, `rows`, `module_m` such as 0.5).
- `spotlights`: `points_px` = pixels of the lights, `height_m` = the height they hang from (the underside of what they hang on).
- `roof_frame`: only when a closed frame or ceiling band at the top is visible; `height_m` of the band, `width_m` of its band, `radius_m` of its rounded corners.
- Keep openings open: do not build walls across sides that are visible as open, and never put a `wall` on a side you listed in `open_sides` (a cutaway side may have one). Do not copy text, logos or people.
- Heights and sizes are metres. If you are unsure of a size, give your best estimate and mention it in `uncertainties`; leave null only where the field allows a default.
- `uncertainties`: list what you could not see or are unsure about (hidden sides, roof extent, sizes). Mark `critical` true only for things that would change the structure.
- `scale_notes`: one or two sentences on where the dimensions come from (which one is stated or assumed, and that the others are worked out from the corner points).

All pixel coordinates must lie inside the picture. Do not output anything but the JSON object.
