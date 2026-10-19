# Priors review table

A table for the owner. Nothing in it is used by the tool yet. Iteration I4 of the [design review](design-review-2026-10.md) wants to
score common booth footprints, module grids, height levels and standard part sizes as fit candidates, and the rules of this
repository say a person reviews every discrete spec prior before it is used. This is that review.

**Status: unreviewed.** Every row below is a proposal. The "Owner decision" and "Owner notes" columns are empty on purpose.
`booth-image-to-model/schemas/design-priors.v1.json` is not touched by this document: all its entries keep `reviewed: false` until
you decide.

## How to review

1. Go row by row. In **Owner decision** write `approve`, `change` or `reject`.
2. `approve` means: the value or range, and the proposed use in the "Proposed use" column, are fine as written.
3. `change` means: write the value you want, or a different use, in **Owner notes**.
4. `reject` means: never use this row.
5. A row left empty is not approved and is not used.
6. When you are done, an agent turns each approved row into an entry of `design-priors.v1.json` with `reviewed: true` (a new `id`
   where the row is not in the file yet) and writes down your changes. Rows that came from `design-priors.v1.json` carry its id in
   the "Prior" column (`DJ: <id>`).

Look at the [points to check first](#points-to-check-first) before you start; they are the rows where the sources disagree with each
other or where a value has no source in the research notes.

## What the columns mean

| Column | Meaning |
|---|---|
| id | Row id for this review (not a spec id) |
| Prior | What the value is |
| Value / range | Copied from the notes with its unit. Studio-list rows (DP, DJ) are in metres, the research notes in millimetres. `(derived)` means the notes say it is their own arithmetic |
| Region | `metric`, `US` or `both`. Rows from the studio list say no region; `metric` there only means the values are in metres |
| Source | File, section and the original source the note names (see the key below) |
| Confidence | As recorded in the notes. The research notes use High / Medium / Low; the studio list records none |
| Proposed use | One of the four labels below |
| Owner decision | Yours: approve / change / reject |
| Owner notes | Yours |

**Proposed use** follows the notes' own wording:

| Label | When it is proposed |
|---|---|
| candidate for fitting | A standardised size or grid that the notes rank as a reliable anchor or that a maker publishes. It would be scored as a soft candidate and dropped when the picture disagrees |
| default when nothing else is known | The notes list it as a suggested default (dimensions-reference §6c, venue-regulations §7), or it is in the studio list of defaults for parts that are not visible |
| cross-check only | Varies by product, venue or show, or the notes call it a sanity check or secondary check. It could warn about a fit but would not drive one |
| do not use | The notes call the anchor unreliable and give no way to use it (for example "Do not use for scale"), or the value has no source in the notes |

For rows that the notes list as unreliable anchors (dimensions-reference §6b), "do not use" means do not use them as a scale anchor
or fit candidate. A default for an unseen part of the same kind is a separate row.

Venue and organiser rules differ by show, hall and edition. They are listed as `cross-check only` or as a default the notes suggest,
and the tool should write them as "to verify" in its outputs, never as "forbidden" or "compliant" (see
`booth-image-to-model/references/design-priors.md`).

## Source key

| Short name | File | Notes |
|---|---|---|
| DR | `knowledge/research/dimensions-reference.md` | Public sources, accessed 2026-10-14. Confidence High / Medium / Low |
| VR | `knowledge/research/venue-regulations.md` | Venue and organiser manuals, accessed 2026-10-14. Confidence high / medium / low. Every value is venue- and edition-specific |
| GF | `knowledge/research/gap-fill-2026-10-14.md` | Follow-up for gaps in DR. Source tags: OFFICIAL, 3RD (third-party copy), EXTRACT (search-engine extract only) |
| DP | `booth-image-to-model/references/design-priors.md` | Generic sizes and conversions from an internal studio specification (not public); the file names no original source per row. No confidence recorded |
| DJ | `booth-image-to-model/schemas/design-priors.v1.json` | The same studio values as machine-readable entries; all `reviewed: false` |

Section names are the headings of those files. Chinese headings of DP are quoted as they are.

## 1. Booth footprints and modules

| id | Prior | Value / range | Region | Source | Confidence | Proposed use | Owner decision | Owner notes |
|---|---|---|---|---|---|---|---|---|
| FP-01 | Common booth sizes | 3×3, 6×3, 9×3, 6×6, 9×6, 12×6 m ("etc.") | metric | DP "单图定尺度：锚点优先级", item 4 | not recorded | candidate for fitting (DP: only to check that the fit lands near a common size, never the only source of scale) | | |
| FP-02 | Fallback booth when nothing is known | 6 × 3 m, height limit 3 m; recorded with `--assume`; if it contradicts the image proportions the image wins and the default fixes only one side | metric | DP "单图定尺度：锚点优先级", item 5 | not recorded | default when nothing else is known | | |
| FP-03 | Row-booth width on the wall-system axis | A "3 m" booth is 3 × 990 = 2970 mm on axis (derived) | metric | DR §0 "Corrections to the brief's working assumptions" (from OCTANORM brochure AW 630); DP item 2 says 2.97 m | High (derived) | candidate for fitting | | |
| FP-04 | Chinese standard shell booth | 3000 × 3000 × 2500 mm; also 3 × 6 × 2.5 m; 3.0 × 3.0 × 2.5 m in the "990 series"; 3 × 3 × 3.5 m; fascia raised to 4 m on upgraded booths | metric | DR §1c "Chinese aluminium shell system" (Foshan Lixin articles, 2020-06-28, and product LX-BT02; Foshan Guanhe product GH-BT001) | Medium | candidate for fitting | | |
| FP-05 | Shell-scheme booth at a Chinese import fair | 3000 × 3000 (medical zone), other zones 2890 × 2970; overall height 3500; wall 2500; fascia 350 high, lowest point 2850 mm | metric | VR §4.1 "National Exhibition and Convention Center (Shanghai)" and §7 D1 (CIIE 2025 exhibitor manual, §3.2) | high (event rule) | default when nothing else is known (VR D1; China only) | | |
| FP-06 | Standard booth unit at the Canton Fair | 9 m², inferred from "72 m² = 8 standard booths" | metric | VR §4.2 "Canton Fair Complex" (exhibitor handbook, 138th session, §(五)1) | medium (derived) | cross-check only | | |
| FP-07 | Shell partition panel at a German venue | About 1000 mm wide × 2500 high (or 3000 high), white plastic surface | metric | VR §3.4 "Koelnmesse" (Technical Guidelines, July 2024, §4.7.6) | high | cross-check only (width is approximate) | | |
| FP-08 | Organiser kit with 1000 mm panels | Walls 1000 W × 2500 H × 3 D; folding door 1000 × 2000; backdrop height 2.5 m. DR: plain 1000 mm panels appear in some organiser and Chinese kits | metric | DR §1c (Beyond Expo, Standard Booth Guide, 2026) and §6a rank 1 | High | candidate for fitting (only when the system is identified as a 1000 kit) | | |
| FP-09 | Booth type by number of open sides | 1 open side: back and both side walls complete, no overhead structure by default. 2 adjacent: L-shaped wall, light column or main graphic at the open corner. 3: only the back wall closed, a U-shaped header or square frame needs posts. 4: solid block (storage or meeting) at the centre or rear, no perimeter walls, hanging structure only where allowed. A cutaway edge is not a real opening | both | DP "展位类型与读图" | not recorded | candidate for fitting (the type choice in I2) | | |
| FP-10 | US island and peninsula size | Island and peninsula booths: 400 sq ft or larger and 20 × 20 ft or larger; island and peninsula booths of 400 sq ft or more may hang signs, linear and perimeter booths may not; split-island graphics set back 10 ft (3.05 m) from adjacent booths | US | GF §5a "CES (Las Vegas Convention Center main floors)" (Freeman, CES LVCC Exhibitor Manual, CES 2027) [OFFICIAL] | High | cross-check only | | |

## 2. Heights

### 2a. Booth height limits and levels

| id | Prior | Value / range | Region | Source | Confidence | Proposed use | Owner decision | Owner notes |
|---|---|---|---|---|---|---|---|---|
| HT-01 | Shell-scheme wall height | 2500 mm. Range found 2438–3000; 3000 on request at two German venues; US shows 8 ft (2438) back drape | both | VR §2 "Cross-venue summary", row "Shell-scheme wall height", and §7 D1 | high to medium by venue | default when nothing else is known | | |
| HT-02 | System wall and frame heights | OCTAwall and beMatrix frame 2480; shell overall 2500 (also the Chinese standard booth); seen panel 2340; clear height under the shell beam 2430. Confusing seen panel 2340 with overall 2500 is a ±7% error (DR) | metric | DR §6a "Reliable anchors" rank 2; §1a (OCTANORM brochure AW 7970; Showlite shell scheme package, 2024) | High | candidate for fitting | | |
| HT-03 | beMatrix frame heights | 2418, 2480, 2976 (product codes 0992, 2418, 2480); largest frame on a standard trolley 992 × 2976 | metric | DR §1b "beMatrix (BE): b62 frame system" (beMatrix straight-frames catalogue page and product page) | Medium–High (codes); Medium (2976) | candidate for fitting | | |
| HT-04 | Height above which a wall facing a neighbour is neutral | 2500 mm: plain, white, grey or beige, no graphics. In China every exposed back is a finished, ad-free surface. At a US show: plain finished back wall at any height | both | VR §2 and §7 D4 (technical guidelines of five German venues; CIIE, Canton Fair, CIEC manuals; CONEXPO-CON/AGG 2026 rules) | high (three venues); medium (two venues) | default when nothing else is known | | |
| HT-05 | Single-storey stand height when the venue is unknown | 4000 mm in Europe and unknown regions; 4500 mm in mainland China | metric | VR §7 D2 (Messe Frankfurt height tables; CBME 2025; Canton Fair 138th; PCIM Asia Shenzhen 2026; toy fair 2026) | high (event rules) | default when nothing else is known | | |
| HT-06 | Single-storey maximum by venue (examples) | Overall range 2000–8000 mm. Messe Frankfurt: 4000 for stands up to 30 m² in most halls, up to 8000 for stands from 31 m². Messe Düsseldorf: 2000–8000 by hall. Messe München: standard 3000. Koelnmesse: per event (one show: 4500, and 6500 in halls 1 and 6–9). Deutsche Messe: 6500 (Hall 8: 6000, Hall 18: 4000). Shanghai: 6000 (CIIE 2025), 4000 (CBME 2025). Canton Fair: 4500. Shenzhen World events: 3500–5000. CIEC Shunyi: 5000 (4500 near gate 11). DWTC concourse: 4000 | metric | VR §2 row "Single-storey max height (custom)" | high to medium by venue | cross-check only (limits, not typical heights) | | |
| HT-07 | US height limits | LVCC (CONEXPO-CON/AGG 2026): 10 ft (3048) for stands under 20 ft deep, 16 ft (4877) otherwise. CES 2027 main floors by hall, including truss and hanging signs: West 30 ft, North 30 ft, Central 1–2 20 ft, Central 3–5 30 ft, South 1–2 22 ft. Linear and corner booths 8 ft (2.44 m); perimeter booth 12 ft (3.66 m) | US | VR §5.2 "Las Vegas Convention Center (CES venue)" and GF §5a (Freeman, CES LVCC Exhibitor Manual, CES 2027). VR §5.2 says CES-specific values were not verified; GF §5a later read the CES 2027 manual | High (GF §5a, event rule); high (VR §5.2, event) | cross-check only | | |
| HT-08 | Heights that trigger drawing approval | Messe Frankfurt above 4000; Messe München above 3000; CIIE from 4500 (structural review and fee); DWTC system stands above 3 m. Area triggers: above 100 m² at Messe Frankfurt, Messe München and Deutsche Messe | metric | VR §2 row "Height that triggers drawing approval" and §7 D3 | high to medium | cross-check only | | |
| HT-09 | Double-deck heights | Overall maximum 5000–8500 by venue (CIIE 2025 8500; Canton Fair, PCIM 2026 and CIMES 2024 6000; ISH 2026 5000; LVCC 20 ft = 6096; Deutsche Messe within 6500 total and upper floor at most 3500). Suggested: overall at most 6000, clear height 2500 per level, upper floor at most 50% of the stand area, stand at least 72 m² and open on at least 3 sides. Minimum clear height 2300–2500 (Messe Frankfurt, Koelnmesse, Deutsche Messe 2300; Messe München 2400; Messe Düsseldorf 2500) | both | VR §2 and §7 D8 | high to medium | cross-check only (double-deck stands are out of scope in DP "构造常识（判断看不到的部分）") | | |

### 2b. Header band, fascia and signs

| id | Prior | Value / range | Region | Source | Confidence | Proposed use | Owner decision | Owner notes |
|---|---|---|---|---|---|---|---|---|
| HT-10 | Header band height (`DJ: header_band_height`) | 0.4–0.8 m | metric | DP "常用尺寸默认（m）", row 门头楣带 | not recorded | default when nothing else is known | | |
| HT-11 | Header band depth (`DJ: header_band_depth`) | 0.3–0.6 m | metric | DP "常用尺寸默认（m）", row 门头楣带 | not recorded | default when nothing else is known | | |
| HT-12 | Header band bottom (`DJ: header_band_bottom_min`, `header_band_bottom_absolute_min`) | At least 2.4 m; never below 2.0 m | metric | DP "常用尺寸默认（m）", row 门头楣带 | not recorded | default when nothing else is known | | |
| HT-13 | Header band top margin (`DJ: header_band_top_margin`) | Top at most the height limit minus 0.1 m | metric | DP "常用尺寸默认（m）", row 门头楣带 | not recorded | default when nothing else is known | | |
| HT-14 | Header post section (`DJ: header_post_section`) | 0.15–0.3 m square | metric | DP "常用尺寸默认（m）", row 门头楣带 | not recorded | default when nothing else is known | | |
| HT-15 | Fascia and ceiling beam of a shell scheme | Octanorm-based shell scheme: fascia 300, ceiling beams 70 high × 16 wide, overall 2500, clear height under the ceiling beam 2430. London shell scheme: fascia 175, ceiling beams 70 high. CIIE 2025: fascia 350 high, lowest point 2850 | metric | DR §1a "Octanorm-based shell scheme" (Showlite, Farnborough 2024 shell scheme package; Business Design Centre, Stand Brochure v2, 2026); VR §4.1 | High | cross-check only | | |
| HT-16 | Hanging banner band | Height 0.8–1.2 m, bottom at least 3.0–3.5 m; only where hanging is allowed | metric | DP "常用尺寸默认（m）", row 吊挂布带 | not recorded | default when nothing else is known (only where hanging is allowed) | | |
| HT-17 | Logo light column | Section 0.6–1.0 m, height 3.0–4.5 m, not above the limit | metric | DP "常用尺寸默认（m）", row Logo 灯柱 | not recorded | default when nothing else is known | | |
| HT-18 | Graphic panel | Centre 1.5–1.6 m; bottom edge at least 0.8 m (`DJ: graphic_bottom_min`) | metric | DP "常用尺寸默认（m）", row 画面 | not recorded | default when nothing else is known | | |

### 2c. Display centre and eye height

| id | Prior | Value / range | Region | Source | Confidence | Proposed use | Owner decision | Owner notes |
|---|---|---|---|---|---|---|---|---|
| HT-20 | TV centre height (`DJ: tv_centre_height`) | 1.5–1.7 m, default 1.6 m | metric | DP "常用尺寸默认（m）", row 电视 | not recorded | default when nothing else is known | | |
| HT-21 | Exhibit centre height | 1.2–1.6 m | metric | DP "常用尺寸默认（m）", row 展柜 / 高柜 | not recorded | default when nothing else is known | | |
| HT-22 | Eye height of the render camera (`DJ: render_eye_height`) | 1.6 m. DR: plausible; standing eye height in DIN 33402-2 data runs from 1430 mm (women P5) to 1735 mm (men P95); one rendering vendor recommends 1.5–1.8 m | both | DP "常用尺寸默认（m）", row 渲染视高; DR §0, §4 "Human factors and building basics" (IBA, Body measurements; D5 Render camera tips) and §6c | Medium (IBA); Low–Medium (render vendor) | default when nothing else is known (DR §6c) | | |

### 2d. Counters, tables and seats

| id | Prior | Value / range | Region | Source | Confidence | Proposed use | Owner decision | Owner notes |
|---|---|---|---|---|---|---|---|---|
| HT-30 | Standing and seated counter height (`DJ: counter_height_standing`, `counter_height_seated`) | Standing 1.05–1.10 m; seated 0.75 m. DP: styles vary about ±12–15% | metric | DP "常用尺寸默认（m）", row 前台 | not recorded | default when nothing else is known | | |
| HT-31 | Counter top for a system counter | 1000 mm (sourced range 900–1150) | metric | DR §6c "Suggested defaults when the brief gives nothing" | not recorded for the default; the range rows are High | default when nothing else is known | | |
| HT-32 | Counter heights as a scale anchor | Octanorm counter Q 400: 902 W × 965 H × 379 D; Q 800: 910 × 912 × 446; Macau info counter 1200 × 450 × 900 H; counters matched to stools 950–1000 and 1100–1150; vendor guide: standing transaction top 1067–1219, staff surface 711–762. Spread ±12–15% | both | DR §3d "Counters" (OCTANORM Technics 08/26 AW 630 T; Beyond Expo, Standard Booth Guide; Vitra factsheet; Find Office Furniture guide) and §6b | High; Low (vendor guide) | do not use (as a scale anchor; DR §6b lists reception and info counters as unreliable) | | |
| HT-33 | Accessible counters and work surfaces (US) | Accessible section of a sales or service counter at most 915 mm high, at least 915 long (parallel approach) or 760 long (forward approach). Dining and work surfaces 710–865. DP also mentions ADA 0.915 m | US | DR §0, §3b, §3d (US Access Board, 2010 ADA Standards §904.4 and §902.3); DP row 前台 | High | cross-check only | | |
| HT-34 | Lowered counter section (Australia) | 830–870 mm (AS 1428.2, as summarised) | metric | DR §3d (Sydney Access Consultants summary) | Medium | cross-check only | | |
| HT-40 | Round table (`DJ: round_table_height`) | Height 0.74 m. Diameter 0.8 m (2 people), 0.9–1.0 m (4), 1.2–1.4 m (6). Square table 1.2 × 0.8 m (4 people) | metric | DP "常用尺寸默认（m）", rows 圆桌 and 方桌 | not recorded | default when nothing else is known | | |
| HT-41 | Bistro and meeting table | Bistro table Ø700 × H760 (seated); meeting table height 720–750 | metric | DR §6c; §3b "Tables" (Rent.Group datasheet 60659) | Medium (bistro) | default when nothing else is known (DR §6c) | | |
| HT-42 | Seated table or desk height | DP: tabletop 0.72–0.75 m. DR: typical products 718–760 (±3%); round table in a shell-scheme package Ø800 × H720; ADA dining and work surfaces 710–865; low tables differ (coffee 387, side 349–508) | both | DP "单图定尺度：锚点优先级", item 3; DR §3b (Beyond Expo, Standard Booth Guide; US Access Board) and §6b | not recorded (DP); High (shell-scheme table, ADA) | cross-check only (DR §6b: good secondary check) | | |
| HT-43 | Bar or standing table (`DJ: bar_height`) | DJ bar height 1.10 m; DP bar table 1.07–1.10 m. EU rental Ø800 × H1100 (also 800 × 800 × H1100); US Ø762 (30 in) × H1067 (42 in). DR default: Ø800 × 1100 (EU) or Ø762 × 1067 (US). Spread ±2% | both | DP rows 吧台 and item 3; DR §3b (Rent.Group datasheets 1133, 60074, 60714; Classic Exhibits CEBT-037), §6b, §6c | High (datasheets, with disclaimer) | default when nothing else is known | | |
| HT-44 | Designer round, side and coffee tables (only if the model is identified) | Round dining table Ø908 / 1073 / 1200 / 1372 / 1524 with H718; side table Ø413 or 508 with H508; coffee table Ø908 with H387 | metric (converted from inches) | DR §3b (Knoll product pages) and §6a rank 7 | High (one maker source; look-alikes exist) | cross-check only | | |
| HT-50 | Chair seat height (`DJ: seat_height`) | 0.43–0.47 m; example overall chair height 0.83 m | metric | DP "单图定尺度：锚点优先级", item 3 | not recorded | cross-check only (DP: varies; stools and sofas about ±12%) | | |
| HT-51 | Plastic side chair and armchair (only if identified) | Side chair W465 × D550 × H830, seat 430; armchair W625 × D600 × H830, seat 430, armrest 680. Seat height is a loaded value (EN 1335-1:2000) and bases were raised about 2 cm in 2015, so use overall height 830 | metric | DR §3a "Chairs and stools" (Vitra factsheet, 2025) and §6a rank 6 | High; Medium for the seat-height note | cross-check only | | |
| HT-52 | Stools | One family: seat front edge 700 (counter 950–1000) and 820 (counters and tables 1100–1150). Another: low H750 with seat 640–650, high H850 with seat 740–750. DP: bar stool seat about 0.75 m | metric | DR §3a (Vitra factsheet; HAY data on two retailer pages), §6b; DP row 吧台 | High (first family); Medium (second) | do not use (DR §6b: unreliable, ±12%) | | |
| HT-53 | Sofas | Seat 380–480, height 645–810, width 1360–2380 across four listed models | metric | DR §3c "Lounge furniture" and §6b | High to Medium | do not use (DR §6b: unreliable, ±12% seat, ±11% height) | | |

## 3. Standard parts

### 3a. Wall systems and boards

| id | Prior | Value / range | Region | Source | Confidence | Proposed use | Owner decision | Owner notes |
|---|---|---|---|---|---|---|---|---|
| SP-01 | Octanorm wall-system pitch | Axis 990 (half module 495); other beams 700 and 1400 | metric | DR §1a "Octanorm (DE)" and §6a rank 1 (OCTANORM octaclassic brochure AW 630, 11/2025; Showlite shell scheme package, 2024) | High | candidate for fitting (DR: up to ±4% error if the system is guessed wrong) | | |
| SP-02 | Octanorm visible panel and upright | Seen panel 950 × 2340; half panel 455 × 2340; upright 40 × 40; London shell scheme: 40 mm diameter poles, 950 inside poles | metric | DR §1a (Showlite; OCTANORM S 100 catalogue; Business Design Centre, Stand Brochure v2, 2026) | High | candidate for fitting | | |
| SP-03 | Octanorm infill panels and beams | Octaclassic infill 968 × 2360, 3–4 thick; above-door panel 968 × 420; panel 968 × 280. Shell-scheme infill 970 × 2354, half 475 × 2354. Beam Z 425 950 × 50; Z 430 1360 × 50 | metric | DR §1a (OCTANORM brochure AW 630; Showlite) | High | cross-check only | | |
| SP-04 | OCTAwall 40 frame | 990 × 2480 × 40; also 950 × 2480 × 40 and 455 × 2480 × 40; door element 950 × 2500 × 40 with leaf 855 × 2021; sliding door leaf 990 × 2480 × 40 | metric | DR §1a (OCTANORM brochure AW 7970) | High | candidate for fitting | | |
| SP-05 | beMatrix grid | 62 × 62 mm; all stand widths, lengths and heights are multiples of 62; profile depth 62 | metric | DR §1b (beMatrix, The difference; Olympus Group SEG wiki for the depth) | High (grid); Medium (depth) | candidate for fitting | | |
| SP-06 | beMatrix standard frame | 992 × 2480 (16 × 62 by 40 × 62); infill panel = frame minus 7 (985 × 2473) | metric | DR §1b (beMatrix, How to determine panel dimensions for b62 frames) | High | candidate for fitting | | |
| SP-07 | beMatrix frame widths in use | 310, 434, 496, 558, 744, 992, 1178, 1426, 1984, 2418, 2976 | US (list is in inches, converted) | DR §1b (Olympus Group SEG wiki) | Medium | candidate for fitting | | |
| SP-08 | beMatrix doors | Single door frame width 992; double door 1984 | metric | DR §1b (beMatrix How-to PDF, pp. 11–12) | High | candidate for fitting | | |
| SP-09 | Chinese aluminium shell system | "International 990 series", 990 module spacing in a fair's drawing; octagonal upright 40 and 60 across flats; upright lengths 300, 500, 700, 1000, 1500, 2000, 2480; panel 3 (lauan core, PVC faces). The notes give no millimetre size for the flat beams | metric | DR §1c, and "Gaps" (Foshan Lixin article; Foshan Guanhe GH-BT001; China International Furniture Fair, Service Manual) | Medium | cross-check only (DR: no formal drawing or spec sheet) | | |
| SP-10 | Board sheet | 1220 × 2440 (MDF, plywood); DP: 1.22 × 2.44 m. DR: size comes from an EPD and trade practice, not from the GB/T 9846 text | metric | DR §1d "Sheet materials" (MEDITE PREMIER; EPD-IES-0015672) and "Gaps"; DP item 2 | High | candidate for fitting (DR §6a rank 5: nominal, rarely seen uncut) | | |
| SP-11 | Large-format MDF (EU) | 2800 × 2070; thicknesses 15, 18, 19, 25 | metric | DR §1d (EGGER support page, search extract) | Medium | cross-check only | | |
| SP-12 | Board thicknesses | MDF 3, 4, 5, 6, 9, 12, 15, 16, 18, 22, 25, 30, 32, 36, 38; plywood 2.4–34 | metric | DR §1d | High | cross-check only | | |
| SP-13 | Octanorm floor tiles | 998 × 998, 998 × 498, 498 × 498 | metric | DR §4 "Human factors and building basics", row "Octanorm floor systems" (OCTANORM OCTAfloor brochure AW 883) | High | cross-check only | | |
| SP-14 | Square truss, 290 mm class | Eurotruss FD34 290 × 290, main tube 50 × 2, braces 20 × 2, lengths 25 (adapter), 50, 75, 100, 150, 200, 250, 300, 350, 400 cm; Global Truss F34 edge 290; Litec QX30SA side 290. Prolyte X30 and H30 about 290 from dealer titles | metric | GF §1 "Exhibition truss sizes" (maker pages) [OFFICIAL]; Litec dealer page [3RD]; Prolyte dealers [EXTRACT] | High (Eurotruss, Global); Medium (Litec); Low–medium (Prolyte overall size) | cross-check only (GF: no source says which sections are used on exhibition stands; makers cannot be told apart from the section) | | |
| SP-15 | Square truss, 400 mm class | Eurotruss HD44 400 × 400, 50 × 3 and 25 × 2; Global Truss F44 outer 400, 50 × 2 and 25 × 3; Prolyte H40 about 400 from dealer titles; Litec QX40SA side 400 | metric | GF §1 (maker pages [OFFICIAL]; dealer pages [3RD] and [EXTRACT]) | High (Eurotruss, Global); Low (Litec QX40SA); Low–medium (Prolyte) | cross-check only | | |
| SP-16 | Triangle and ladder truss | Eurotruss FD33 triangle height 258, width 290; FD32 ladder height 290, width 50; Global Truss F33 outer 290 (height not stated) | metric | GF §1 (maker pages) [OFFICIAL] | High | cross-check only | | |

### 3b. LED cabinets

| id | Prior | Value / range | Region | Source | Confidence | Proposed use | Owner decision | Owner notes |
|---|---|---|---|---|---|---|---|---|
| SP-20 | LED cabinet 500 × 500 and 500 × 1000 (`DJ: led_module_sizes`, value 0.5) | 500 × 500 × 90, pitch 2.84 (rental model); 500 × 500 and 500 × 1000, indoor pitch 1.9 / 2.5 / 2.9 / 3.9 (rental series). DP: common in China at 0.5 m, also 0.5 × 1.0 | both | DR §2b "Indoor LED cabinets" (ROE Visual, Black Pearl 2V2; Absen PL V2 series) and §6a rank 3; DP item 2 | High (ROE Visual); Medium (Absen) | candidate for fitting | | |
| SP-21 | LED module 0.498 m (`DJ: led_module_sizes`, value 0.498) | DP: 0.498 m for Europe and the US | metric | DP "单图定尺度：锚点优先级", item 2. No matching value in the three research notes; the rental cabinet in DR §2b is 500 × 500 | not recorded | do not use (no source in the research notes; see the points to check) | | |
| SP-22 | LED cabinet 16:9 | 600 × 337.5 × 95, pitch 0.9375 (640 × 360 px). The same 600 × 337.5 format exists at pitch 0.78, 1.25 and 1.5625 | metric | DR §2b (Planar, DirectLight DLX-0.9 datasheet; Planar DLX family and Unilumin listings) | High; Medium (other pitches) | candidate for fitting | | |
| SP-23 | LED cabinet 4:3 | 640 × 480 × 42, pitch 4 (module 320 × 160). Another: 640 × 480 × 58.6, pitch 2.5 (discontinued). DP: 0.64 × 0.48 m | metric | DR §2b (Dahua PHSIA4-SC; Hikvision DS-D4025CS-1F); DP item 2 | High (Dahua); Medium (Hikvision) | candidate for fitting | | |
| SP-24 | LED cabinet 1:1, outdoor size | 960 × 960 × 138, pitch 10 or 8. DP: 0.96 × 0.96 m. DR: a 1:1 cabinet is ambiguous between 500 (indoor rental) and 960 (outdoor); use the pitch and the indoor context | metric | DR §2b (Hikvision outdoor 960 × 960) and §6a rank 3; DP item 2 | Medium | candidate for fitting | | |
| SP-25 | Rear space behind an LED wall (`DJ: led_rear_depth_min`) | At least 0.8 m (preferably 1.2 m) for maintenance or storage | metric | DP "常用尺寸默认（m）", row LED 后部维护 / 储物 | not recorded | default when nothing else is known | | |

### 3c. TV widths per inch

| id | Prior | Value / range | Region | Source | Confidence | Proposed use | Owner decision | Owner notes |
|---|---|---|---|---|---|---|---|---|
| SP-30 | 16:9 active area from the diagonal d in inches | W = 22.138 × d mm, H = 12.453 × d mm (derived). DP: about 22.14 and 12.45 mm per inch. Use the measured diagonal where known (a "43-inch class" panel measures 42.5 in). Outline = active area + 2 × border; border 9–17 mm per side. If the class cannot be decided from the image, flag the image | both | DR §2a "16:9 flat panels" and §6c; GF §2 (border range); DP item 2 | High (derived) | candidate for fitting | | |
| SP-31 | TV, 43 in class | Nominal active 951.9 × 535.5. Samsung QMC: active 941.2 × 529.4 (42.5 in), outline 969.5 × 557.8 × 28.5. LG 43UH5N-M: 963.0 × 556.2 × 29.7. Philips 43BDL3650Q: 973.0 × 561.2 × 63.5 | both | DR §2a (Samsung QMC leaflet); GF §2 (LG Business, Philips pages) [OFFICIAL] | High (GF did not re-check the Samsung 43 in on a maker page) | candidate for fitting | | |
| SP-32 | TV, 50 in class (`DJ: tv_picture_sizes`, 50) | Nominal active 1106.9 × 622.6. Samsung QMC: active 1095.8 × 616.4 (implies 49.5 in; the leaflet prints 48.5, probably a typo), outline 1124.1 × 644.8 × 28.5. DJ: 1.11 × 0.625 m | both | DR §2a (Samsung QMC leaflet); GF §2 says the 43 in and 50 in Samsung sizes were not re-checked on a maker page; DP row 电视 | High (DR); not recorded (DJ) | candidate for fitting | | |
| SP-33 | TV, 55 in class (`DJ: tv_picture_sizes`, 55) | Nominal active 1217.6 × 684.9. Samsung QMC: active 1209.6 × 680.4 (54.6 in), outline 1237.9 × 708.8 × 28.5. LG 55UH5N-M: 1231.4 × 707.2 × 29.7. Philips 55BDL3650Q: 1241.8 × 712.6 × 63.6. Consumer 55 in example: 1232.9 × 709.2 × 25.7 without stand. Makers differ by 0.84% at this class (GF). DJ: 1.23 × 0.69 m | both | DR §2a (Samsung QMC leaflet; EE device help for the consumer example); GF §2 [OFFICIAL]; DP row 电视 | High; Medium (consumer example); not recorded (DJ) | candidate for fitting (see the points to check about the DJ value) | | |
| SP-34 | TV, 60 in class (`DJ: tv_picture_sizes`, 60) | DJ: 1.33 × 0.75 m. The research notes have no 60 in row; the formula in SP-30 applies | both | DP row 电视 | not recorded | candidate for fitting (only the formula is sourced) | | |
| SP-35 | TV, 65 in class | Nominal active 1439.0 × 809.4. Samsung QMC: active 1428.5 × 803.5 (64.5 in), outline 1456.8 × 831.9 × 28.5. LG 65UH5N-M: 1450.3 × 830.3 × 29.7. Philips 65BDL3650Q: 1462.3 × 837.3 × 68.9. Makers differ by 0.83% (GF) | both | DR §2a; GF §2 [OFFICIAL] | High | candidate for fitting | | |
| SP-36 | TV, 75 in class | Nominal active 1660.4 × 933.9. Samsung QMC: active 1650.2 × 928.3 (74.5 in), outline 1682.3 × 960.4 × 28.5. LG 75UH5N-M: 1679.5 × 958.7 × 29.7. Philips 75BDL3650Q: 1683.5 × 961.7 × 69.5. Active from the Philips pitch 1649.7 × 927.9 (derived) | both | DR §2a; GF §2 [OFFICIAL] | High | candidate for fitting | | |
| SP-37 | TV, 85 and 86 in class | Nominal active (85 in) 1881.7 × 1058.5. Samsung QMC 85 in: active 1872.0 × 1053.0 (84.5 in), outline 1904.3 × 1085.3 × 28.5. LG 86UH5N-M: 1928.8 × 1099.8 × 29.7. Philips 86BDL3650Q: 1929.0 × 1100.0 × 69.5 (measured 85.6 in). The 85 in and 86 in outlines differ by 1.3% (GF) | both | DR §2a; GF §2 [OFFICIAL] | High | candidate for fitting | | |

## 4. Wall thickness and platform heights

| id | Prior | Value / range | Region | Source | Confidence | Proposed use | Owner decision | Owner notes |
|---|---|---|---|---|---|---|---|---|
| WP-01 | New wall thickness (`DJ: wall_thickness`) | 0.10 m. DP: sculpted walls and deep niches are thickened to match the picture; renderings often show 0.1–0.5 m | metric | DP "常用尺寸默认（m）", row 新建墙厚 | not recorded | default when nothing else is known | | |
| WP-02 | Wall depth of a wall system | OCTAwall frame depth 40; octaclassic upright 40 × 40; beMatrix profile depth 62; Macau shell wall panel 3 (walls 1000 × 2500 × 3) | metric | DR §1a, §1b, §1c (OCTANORM AW 7970; Olympus Group SEG wiki; Beyond Expo, Standard Booth Guide) | High; Medium (beMatrix depth) | candidate for fitting (when the system is identified) | | |
| WP-03 | Infill panel thickness | 3 for shell scheme (foam PVC) and for beMatrix; 4 for a London shell scheme; 3–4 for octaclassic; OCTAwall frame panel 2 recommended, 3 maximum; Chinese shell panel 3 (lauan core, PVC faces) | metric | DR §1a, §1b, §1c and §6c ("3 for shell scheme and beMatrix infills; 2 for OCTAwall fabric frames") | High; Medium (Chinese panel) | default when nothing else is known (DR §6c) | | |
| WP-04 | Built wall thickness in China (venue minimum) | 120 mm wall footprint (60 mm minimum with a steel core); 300 mm if a single back wall stands alone. Another fair asks at least 150 | metric | VR §7 D14 and §4.1, §4.2, §4.4 (CIIE 2025, CIMES 2024, Canton Fair 138th, ISH China 2026) | high (event rules) | default when nothing else is known (China only; see the points to check) | | |
| WP-05 | Stand dividing wall at a German venue | 2500 high with 0.12 m feet; for stands open on 1 or 2 sides, 50 mm is deducted from the confirmed width to give the clear width between wall faces | metric | VR §3.1 "Messe Frankfurt" (Technical Guidelines, Sept 2025, §4.7.6) | high | cross-check only | | |
| WP-10 | Platform height (default) | 0.05 or 0.10 m (also seen 0.04 and 0.043); 0.10 is the default when a ramp is built; some fairs limit platforms to 0.12 m | metric | DP "常用尺寸默认（m）", row 地台 | not recorded | default when nothing else is known | | |
| WP-11 | Platform heights in systems | 50 including carpet (Koelnmesse modular stand); 40 (Messe Stuttgart modular stand); 43 fixed, stackable in 57 steps to 214, and 100 with one panel plus adapter (OCTAeco); octaclassic floor 95–225; standard raised floor 32 (Perth) | metric | DR §4 (Koelnmesse; Messe Stuttgart; OCTANORM OCTAfloor brochure AW 883; Perth Convention and Exhibition Centre, Space only information) | High | cross-check only (DR §6b: a bias check on the floor line, not a primary scale) | | |
| WP-12 | Platform height default in the notes | 50 mm, with a ramp at 1:12 | metric | DR §6c | not recorded for the default; the rows it draws on are High | default when nothing else is known | | |
| WP-13 | Platform limits set by organisers | Ramp needed above 20; under 50 a sloped edge at least 1 m wide; 50–100 a ramp at least 1 m wide; 101–120 a ramp at least 2 m wide; maximum 120 (in-cosmetics, Dutch venue). Perth: 32–115 edge 1:8 recommended, from 115 a wheelchair ramp. Canton Fair and China Hi-Tech Fair: above 100 a ramped transition. DWTC: platforms above 300 need structural documents. Single step at most 200 (Messe Frankfurt, Düsseldorf, München, Deutsche Messe), 190 (Koelnmesse) | metric | DR §4 (in-cosmetics Global, Stand Build Regulations; Perth exhibitor services); VR §2 row "Single-step platform height / ramp trigger" | High | cross-check only | | |
| WP-14 | Platform default by region | 100 mm with a ramped or bevelled edge (China); at most 200 mm single step (Europe) | metric | VR §7 D10 | high (event rules) | default when nothing else is known | | |
| WP-15 | Ramp slope (`DJ: ramp_slope_max`) | At most 1:12 (0.0833). ADA: 1:12, rise per run at most 760, landing at least 1525. GB 50763: 1:12 allows a rise of 750 over a run of 9000. Europe: ramps in escape routes at most 6% | both | DP "常用尺寸默认（m）", row 地台; DR §4 (US Access Board 2010 ADA Standards §405; GB 50763-2012 table 3.4.4); VR §2 | High (ADA, GB); not recorded (DJ) | default when nothing else is known | | |
| WP-16 | Ramp width (`DJ: ramp_width_min`) | At least 1.0 m. ADA at least 915; GB 50763 at least 1000 (1200 at accessible entrances); Deutsche Messe at least 1200; DWTC 1000 for stands 3 m or more deep or wide | both | DP row 地台; DR §4; VR §2 row "Ramp gradient / width" | High; high (event rules) | default when nothing else is known | | |
| WP-17 | Stairs | ADA: riser 100–180, tread at least 280, handrail top 865–965. GB 50763: tread at least 280, riser at most 160 (public buildings); steps tread at least 300, riser 100–150. DIN 18065: 2 × riser + tread = 590–650, comfortable pair 170 / 290 | both | DR §4 (US Access Board ch. 5; GB 50763-2012; Baunetz Wissen) and §6a rank 9 | High; Medium (DIN 18065) | cross-check only (DR: a constraint check, not a primary scale) | | |

## 5. Other priors in the notes

| id | Prior | Value / range | Region | Source | Confidence | Proposed use | Owner decision | Owner notes |
|---|---|---|---|---|---|---|---|---|
| OT-01 | Aisle widths inside the booth (`DJ: path_main_min`, `path_secondary_min`) | Main at least 1.2 m, secondary at least 0.9 m, measured from the real edges of obstacles. No matching row in the research notes | metric | DP "常用尺寸默认（m）", row 主 / 次通道 | not recorded | default when nothing else is known | | |
| OT-02 | Counter footprint and clearances (`DJ: counter_length`, `counter_depth`, `counter_edge_clearance_min`, `counter_staff_space_min`) | Length 1.2–2.5 m, depth 0.5–0.6 m; at least 0.3 m from the open edge; at least 0.9 m behind for staff | metric | DP "常用尺寸默认（m）", row 前台. Cross-reference: DR §3d lists counters of 902 × 379, 910 × 446 and 1200 × 450 | not recorded | default when nothing else is known | | |
| OT-03 | Bar counter length | 2.0–4.0 m | metric | DP "常用尺寸默认（m）", row 吧台 | not recorded | default when nothing else is known | | |
| OT-04 | Display cabinet and plinth | Display cabinet height 0.9–1.05 m (low) or 1.8–2.4 m (tall), depth 0.4–0.5 m; plinth side 0.4–1.0 m, height 0.9–1.1 m | metric | DP "常用尺寸默认（m）", rows 展柜 / 高柜 and 展台基座 | not recorded | default when nothing else is known | | |
| OT-05 | Storeroom | At least 1.0 × 1.0 m; common 1.0 × 1.5 to 2.0 × 2.0 m; door 0.8–0.9 m, not facing the main aisle | metric | DP "常用尺寸默认（m）", row 储物间 | not recorded | default when nothing else is known | | |
| OT-06 | Meeting room | 4 people at least 2.0 × 2.5 m; 6 people at least 3.0 × 3.0 m; door 0.9 m; frosted glass by default | metric | DP "常用尺寸默认（m）", row 会议室 | not recorded | default when nothing else is known | | |
| OT-07 | Spotlight spacing (`DJ: spotlight_spacing`) | 1.5–2.0 m | metric | DP "常用尺寸默认（m）", row 射灯间距 | not recorded | default when nothing else is known | | |
| OT-08 | Door sizes | DP: opening width 0.8–0.9 m, height 2.0–2.1 m. DIN 18101 leaves 610 / 735 / 860 / 985 × 1985 (and 1110 × 2110), nominal openings 635 / 760 / 885 / 1010 × 2010. ADA clear width at least 815 (915 if the doorway is deeper than 24 in). GB 50763 at least 800, preferably 900, automatic doors 1000. System doors: OCTAwall leaf 855 × 2021, beMatrix frame 992 wide, Macau shell folding door 1000 × 2000. Spread ±3.5% | both | DP item 3; DR §4 and §6a rank 8 (Baunetz Wissen; US Access Board ch. 4; GB 50763-2012) | Medium–High (DIN); High (ADA, GB) | cross-check only (DR: booth doors follow the stand system, not building standards) | | |
| OT-09 | People | Stature 1535–1855 (P5 women to P95 men); standing eye height 1430–1735; seated eye height 705–855. Spread about ±9–10% | metric | DR §4 and §6b (IBA, Body measurements, DIN 33402-2) | Medium | cross-check only (DR: useful only as a sanity check) | | |
| OT-10 | Planters | 300 × 300 × 560 up to 395 × 395 × 1050; rental pots H 350–900; foliage trough width 1 m, height about 60 cm; planter box 100 × 20 × 100 cm. Spread ±30% or more | metric | DR §5 "Planters and potted plants" and §6b; GF §3 | Medium; Low–medium | do not use (DR §6b: do not use for scale) | | |
| OT-11 | Potted plants | Kentia overall 950–2500 mm; order forms list 100–120 up to 170–180 cm (Frankfurt) and 150, 180, 210, 250, 300 cm (Munich). Spread ±40% or more | metric | DR §5 and §6b; GF §3 | Medium; High for single listings | do not use (DR §6b: do not use for scale) | | |
| OT-12 | Aisle frontage openness | Keep at least 70% of each aisle side open (glass and open counters count as open); never a long closed wall. Messe Frankfurt target 70%; Düsseldorf at most about 30% closed; PCIM 2026 at least 75%; CIMES 2024 and Light + Building at least 50%; CIIE main aisles by zone 50 / 70 / 80% | metric | VR §7 D6 and §2 row "Aisle frontage openness" | high to medium | cross-check only | | |
| OT-13 | Logo setback from a neighbour | At least 1000 mm (2000 is safer). Messe Frankfurt and Deutsche Messe 1000; München 2000; Düsseldorf 3000; PCIM 2026 500 | metric | VR §7 D5 and §2 | high; medium (Düsseldorf) | cross-check only | | |
| OT-14 | Closed ceiling | At most 30 m² and at most 50% of the stand area; otherwise open grid or sprinkler mesh; never roof the upper level of a double-deck. Canton Fair: no closed ceiling on single-storey stands | metric | VR §7 D7 | high to medium | cross-check only | | |
| OT-15 | Lowest edge of hung elements | At least 2500 mm above any floor (Deutsche Messe); budget at most 50 kg per point unless the venue is known | metric | VR §7 D11 | high | cross-check only | | |
| OT-16 | Guard rails and stairs of a double-deck | Rail height 1100 (Europe), 1200 (China), 1500 (Shenzhen events); stair clear width 1400 (China) or 1200 (Europe); riser at most 175, tread at least 260 | metric | VR §7 D9 and §2 | high | cross-check only (double-deck is out of scope in DP) | | |

## Points to check first

These are places where the sources disagree with each other, or where a value has no source in the research notes. They are
restated from the notes, not decided.

1. **LED 0.498 m (SP-21).** DP says Europe and the US use 0.498 m and DJ lists it. None of the three research notes has this
   value; the rental cabinet they list is 500 × 500. Proposed as `do not use` until a source is given.
2. **TV 55 in picture size (SP-33).** DJ says 1.23 × 0.69 m. The notes give 1217.6 × 684.9 mm as the nominal active area
   (formula), 1209.6 × 680.4 mm measured, and maker outlines of 1231.4–1241.8 × 707.2–712.6 mm. The DJ width looks closer to an
   outline than to a picture area. The 50 in value (1.11 × 0.625 m) is close to the nominal figure. The 60 in value has no row in
   the notes.
3. **Wall thickness (WP-01 and WP-04).** The studio default is 0.10 m. The venue rules in the notes ask for a 120 mm wall
   footprint (60 mm with a steel core) at several Chinese fairs and 150 mm at one.
4. **Header band bottom (HT-12).** DP says at least 2.4 m and never below 2.0 m. No row in the research notes states a minimum
   fascia bottom; the nearest rows are a clear height under a shell beam of 2430 mm and a fascia lowest point of 2850 mm at one fair.
5. **Counter heights (HT-30, HT-31, HT-32).** The studio default (1.05–1.10 m) is a default for an unseen part. The notes list
   counters as an unreliable scale anchor (±12–15%), suggest 1000 mm as a default, and cap accessible service counters at 915 mm.
6. **Platform heights (WP-10, WP-11, WP-12).** The studio default is 0.05 or 0.10 m; the notes list 32, 40, 43, 50, 100 and a
   maximum of 120 mm, and suggest 50 mm as a default. DR §6b says to use platform heights only as a bias check on the floor line.
7. **Aisle widths inside the booth (OT-01).** No research row supports 1.2 m and 0.9 m.
8. **Differences inside the notes.** GF §2 corrects DR: the spread between makers at the 55 in and 65 in classes is about 0.8%
   (DR says about 0.5%), and the 85 in Samsung VESA pattern is 600 × 400 (not used here). GF §5a read the CES 2027 manual, while
   VR §5.2 says CES-specific values were not verified; HT-07 follows GF §5a for CES. The Samsung 50 in leaflet value is
   internally inconsistent (DR: probably a typo).
9. **Venue values are not typical sizes.** Rows HT-05 to HT-09 are limits and suggested defaults that depend on the show, the hall
   and the edition. They are marked `cross-check only` or `default when nothing else is known` as the notes suggest.
10. **A decimal that is a ratio (WP-15).** The 0.0833 in the ramp slope row is 1/12 written as a decimal (the DJ entry stores
    0.08333). It stands for the 1:12 slope that DP, the ADA standards and GB 50763 name; it is not a separate figure from a source.

## Not included

The research notes hold more than priors for modelling. These parts were left out on purpose:

- Venue rules that are not geometry: fire classes, electrical rules, rigging loads and point counts, deposits and fees, drawing
  deadlines, noise limits, exits and escape distances, structural loads.
- Pixel pitch against viewing distance (DR §2c).
- The Chinese stand standards and the GITEX Global rules: the notes could not read a numeric clause or find a public rule
  (GF §4 and §5b), so there is nothing to copy.
- The licence text for a model marketplace (GF §6).
- Items that the notes list under "Gaps" or "Could not verify": no number is given for them there, and none is given here.
- `reconstruction-tooling.md` and `asset-sources.md`: they are not sources of dimensions.
