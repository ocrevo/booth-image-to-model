# Reading a reference picture into a reference card

You look at ONE exhibition-booth picture that a designer gave as a reference. You do not rebuild it. You write down what a
designer would take from it: the kind of booth, how it is laid out, its style. The designer's brief decides what changes;
the form gives the real size. So the card holds no measurements and no pixels.

Write `reference_card.json` with exactly these keys (null where you cannot tell), then tell the designer in three to six
plain lines what you took from the picture, so they can correct it before the design is made.

```json
{
  "booth_type": "inline | corner | peninsula | island | other | null",
  "open_sides": ["front", "left"],
  "structure": {
    "header": "none | straight | l | u | frame | ring",
    "platform": true,
    "hero": "what the main visual is and where (for example: a backlit logo wall on the back)",
    "counter": "where the reception counter stands, if any",
    "seating": "meeting tables, bar, lounge, none",
    "storage": "a closed storeroom or cabinet block, where",
    "density": "sparse | balanced | dense"
  },
  "style": {
    "dominant_hex": "#rrggbb  (about 60 % of what you see: walls, floor)",
    "secondary_hex": "#rrggbb (about 30 %: counters, feature surfaces)",
    "accent_hex": "#rrggbb    (about 10 %: lights, trims, details)",
    "materials": ["painted panel", "timber slats", "metal", "glass", "fabric", "LED", "greenery"],
    "floor": "carpet | timber | vinyl | tile | platform",
    "lighting": "bright and even | accented | backlit | dramatic",
    "mood": ["warm", "minimal", "premium", "technical", "playful", "natural", "bold"],
    "industry_guess": "what the booth seems to sell, or null"
  },
  "elements": [
    {"kind": "feature_wall", "side": "back", "note": "vertical oak slats over the full wall"}
  ],
  "text_or_logo_present": true,
  "uncertainties": ["anything you could not tell"]
}
```

Rules:
- Sides: `front` faces the main aisle (usually the side nearest the camera), `back` is opposite, `left` and `right` as
  seen from the front. An open side has no wall; a side cut away only to show the inside of a render is not open.
- `elements[].kind` uses the element words of `prompts/booth_design.md` (feature_wall, graphic_panel, tv, led_wall, wall,
  header_band, ring, spotlights, counter, round_table, chair, seating_group, planter, display_plinth, storage_block,
  partition, box). Something that fits none of them: the nearest word plus a note.
- Colours are what you see, as hex. Pick the three roles by area, not by brightness.
- **Never transcribe text, logos, brand names or slogans from the picture.** Only say that a sign or logo is there
  (`text_or_logo_present`). Text on the new booth comes from the brief only.
- No sizes and no pixel positions. The new booth gets its size from the designer's form.
- Everything on the card counts as provenance `reference` when it is used in the design.
