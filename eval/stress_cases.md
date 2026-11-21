# RealityCart Element 119 — stress cases

Media is the core input. These cases are meant to be **real captures**, not scripted happy-path prompts. Fill the Capture column with photos you take during the hackathon.

| ID | Hard input | What text would lose | Expected break / mitigation | Status |
| --- | --- | --- | --- | --- |
| S1 | Cluttered desk, cables, coffee mug, dual monitors | Footprint of kept monitors, true free regions | Decision rejects lamp in crowded center → Carbon re-plans to side / wall shelf | pending |
| S2 | Dim evening desk photo | Material/color of existing gear | Style gate fails “all black” on light wood organizers → retry darker query | pending |
| S3 | Extreme phone angle on a large room | True width/depth | Approximate scale wrong without RoomPlan → require LiDAR rescan | pending |
| S4 | Voice intent over noisy room | Prosody/noise vs typed brief | Speech partial transcript → user edits before run | pending |
| S5 | Budget $150 with lamp+storage+plant | Bundle constraint | Budget Decision fails; drop plant / cheaper lamp on re-plan | pending |
| S6 | RoomPlan scan of office with sofa + desk | Real cm furniture boxes | Over-detect furniture as keep → Carbon keep list must honor monitor keep | pending |
| S7 | White desk, user asks “industrial black metal” | Surface color vs product finish | Style Decision rejects glossy white accessories | pending |
| S8 | Busy bookshelf (no clear horizontal plane) | Available positions | Hydrogen finds few slots → warnings + fewer placements | pending |
| S9 | Catalog miss for niche query | Inventory gaps | Empty search → failure logged → re-plan broader query | pending |
| S10 | Product with no dimensions / no 3D | Metric fit uncertainty | Decision confidence lower; proxy GLB used with “approximate” label | pending |

## Failure log (update during demo prep)

### Observation template

```
Case: S#
Input: (photo / RoomPlan / voice)
Pipeline step that broke: scene | plan | shopify | decisions | preview
What we saw:
What we changed:
```

### Logged failures

Reproduce any scene-step entry with `python scripts/smoke_scene.py PHOTO [--room-plan scan.json | --no-room-plan]` from `backend/`.
Test photos so far are Wikimedia Commons living-room images plus the sample scan in `backend/tests/fixtures/` — replace them with real captures.

```
Case: H1 — model answer outside the schema
Input: living-room photo + RoomPlan JSON
Pipeline step that broke: scene
What we saw: Hydrogen returned lighting="natural"; validation failed and the whole 14 s parse was
  discarded for the fallback scene (scene_type=unknown), with no visible warning.
What we changed: Hydrogen output is now enforced with a response schema (Gemini structured output);
  "natural" added as a lighting value; one retry, then a fallback that keeps LiDAR geometry and
  records the error in scene.warnings.
```

```
Case: H2 — wrong primary surface from RoomPlan
Input: RoomPlan scan with sofa, table, storage, TV, chair
Pipeline step that broke: scene (geometry merge)
What we saw: every furniture object overwrote surfaces[0]; the last one (a 0.55 m chair) became the
  primary surface. The 4.2 × 3.8 m floor and the coffee-table top were lost.
What we changed: new scene_geometry.py builds the floor from wall positions and one surface per
  table/storage top, with heights re-based to the floor. Chairs, sofas and TVs are never surfaces.
```

```
Case: H3 — photo only, no LiDAR (S3-style)
Input: wide open-plan living-room photo, no scan
Pipeline step that broke: scene
What we saw: Hydrogen estimated surfaces but returned no objects and no open positions. Optional
  list fields in the schema were silently omitted by the model.
What we changed: those lists are now required in the schema and ordered after surface_estimates;
  photo-only mode gets its own prompt rules. Result: 8 objects, 4 positions, all labelled
  "approximate", plus a "scan with LiDAR" warning.
```

```
Case: H4 — dim + blurry capture (S2-style)
Input: same photo darkened to 18 % brightness with blur, plus scan
Pipeline step that broke: none (degrades correctly)
What we saw: image_issues=[dim, blurry, partial_view], lighting=dim; LiDAR geometry unaffected;
  still 2 sensible positions. Hydrogen marked the cups keep=false (clutter).
What we changed: nothing — kept as evidence that media quality is surfaced, not hidden.
```

```
Case: H5 — table-top item placed on the floor
Input: full /v1/redesign with scan; Carbon put an organizer slot on table_1_top
Pipeline step that broke: preview positions
What we saw: placement y=0.0 — pipeline used surfaces[0] height for every slot.
What we changed: placement height now comes from the slot's own surface (0.45 m for the table).
```

## Baseline contrast (Element 119 narrative, light)

Compared to a **single Carbon call** that invents product names without Catalog MCP:

- RealityCart returns **real Shopify titles, prices, and merchant links**
- Decisions API **rejects** fit/style/budget mismatches instead of hallucinating a perfect cart
- RoomPlan / user width supplies **meters** a text description cannot invent reliably

## Demo script

1. Open Capture → RoomPlan scan of any real room/space.
2. Intent (voice or text): *Make this a minimalist developer setup for $600. Keep my existing monitor, add a lamp and storage, and make everything black.*
3. Show Plan tab: Hydrogen scene, Carbon slots, Shopify products, Decision reasons, budget meter.
4. Show 3D tab: proxies on calibrated plane.
5. Call out one stress case (e.g. S1 or S5) and a rejection that triggered re-plan.

```
Case: C1 — "replace my sofa" squeezed into a side gap
Input: living-room scene + "replace the sofa with a black leather sectional"
Pipeline step that broke: plan
What we saw: Carbon marked the sofa "replace" but used the sofa's object id as the position.
  Guard-rails moved the slot to the 0.6 m gap beside the sofa and clamped the sectional to 0.60 m wide.
What we changed: removed/replaced objects now expose a "replace:<object_id>" position in their own
  footprint (size limit = old width × 1.2); Carbon is told to use it; a replace slot implies removal.
  Re-run: sectional placed at replace:rp_1, max 2.20 m wide.
```

```
Case: C2 — tight budget, too many asks (S5-style)
Input: empty-room scene, $150, "armchair, rug, floor lamp, bookshelf and plants"
Pipeline step that broke: none (degrades correctly) — but caps are low
What we saw: Carbon kept 3 must-haves (armchair $75, bookshelf $38, rug $29), dropped lamp + plants
  itself, allocated $142 of $150. Caps that low may return poor Shopify matches.
What we changed: nothing yet — watch Decisions/Shopify results for this case.
```

```
Case: C3 — hung model call
Input: Carbon re-plan after simulated failures
Pipeline step that broke: plan (latency)
What we saw: a single Carbon call took 303 s (no error, just slow). Another took 44 s.
What we changed: per-call timeouts (scene 45 s, plan 40 s) → cancelled and retried once, then fallback.
```

```
Case: C4 — only 1 of 3 slots filled, no re-plan
Input: full /v1/redesign, living room, "keep sofa, add floor lamp and storage, mostly black", $600
Pipeline step that broke: decisions → replan trigger
What we saw: storage cabinet and tray candidates all rejected (4 rejections); re-plan only runs when
  0 products are accepted, and rejection reasons are not returned in the response.
What we changed: nothing yet — next step (Decisions): return reasons, re-plan failed slots only.
```

```
Case: D1 — full-size cabinets for a 0.5 m gap (resolves C4)
Input: living room, "keep sofa, add floor lamp + storage, mostly black", $600
Pipeline step that broke: decisions (correctly) → nothing recovered
What we saw: Decisions rejected every cabinet on fit (0.13–0.30) — they were 0.76–0.91 m wide.
  The gate was right; the search had no size word, reasons were not returned, and re-plan only
  ran when 0 slots filled. Result: 1 of 3 slots.
What we changed: code size precheck with exact reasons ("too wide: 0.88 m vs 0.50 m max", no API
  call); size words in queries ("narrow", "small"); one cheap looser Shopify retry per slot;
  Carbon re-plans ONLY empty slots with the reasons, leftover budget and fixed purchases.
  Re-run: 3 of 3 filled — Carbon swapped the cabinet for a narrow black nightstand.
```

```
Case: D2 — floor size leaked into slot limits
Input: same run, found by an offline test while building D1
Pipeline step that broke: plan rules → decisions
What we saw: with no depth from Carbon, the floor's depth (3.8 m = the room) became the slot's
  max depth, so the orientation-free size check let a 0.88 m cabinet "rotate" into a 0.5 m gap.
What we changed: only raised surfaces (table tops) bound a slot; furniture is not rotated unless
  both width and depth limits are real.
```

```
Case: D3 — style judged from titles only
Input: "mostly black" request, products whose titles do or do not say black
Pipeline step that broke: decisions (weak signal)
What we saw: style was judged from text; a probe showed the room's yellow sofa scored 0.40 from
  text vs 0.86 from the photo.
What we changed: Decisions now receives the product photo (by URL) and the room photo (768 px)
  with a ~800-char slot summary instead of the 4.9k-char scene. If images fail, it retries
  text-only, then a labelled heuristic. Live: 0.5–1.1 s per call, all candidates in parallel.
```
