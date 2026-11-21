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

_(Add entries as you stress the system.)_

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
