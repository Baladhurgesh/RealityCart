#!/usr/bin/env python3
"""Live Carbon design-plan check (needs GEMINI_API_KEY).

Takes a scene JSON — e.g. the output of `smoke_scene.py PHOTO --out` — so you can
iterate on the plan without re-running Hydrogen on every try.

Usage:
    python scripts/smoke_plan.py                                   # sample living-room scene
    python scripts/smoke_plan.py empty_living_room1.scene.json     # bare name → eval/captures/
    python scripts/smoke_plan.py SCENE.json --intent "cozy reading corner, all black" --budget 300
    python scripts/smoke_plan.py SCENE.json --search               # also query Shopify per slot
    python scripts/smoke_plan.py SCENE.json --out                  # save plan next to the scene
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.gemini_plan import create_design_plan  # noqa: E402
from app.schemas import SceneAnalysis  # noqa: E402
from app.shopify_mcp import search_catalog  # noqa: E402

SAMPLE_SCENE = ROOT / "tests" / "fixtures" / "scene_living_room.json"
CAPTURES = ROOT.parent / "eval" / "captures"
DEFAULT_INTENT = "Make this minimalist for $600. Keep my sofa, add a floor lamp and some storage, mostly black."


def resolve(arg: Path | None) -> Path:
    if arg is None:
        return SAMPLE_SCENE
    for c in (arg, CAPTURES / arg, CAPTURES / arg.name):
        if c.is_file():
            return c
    raise SystemExit(f"Scene JSON not found: {arg} (also looked in {CAPTURES})")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scene", type=Path, nargs="?")
    ap.add_argument("--intent", default=DEFAULT_INTENT)
    ap.add_argument("--budget", type=float, default=600)
    ap.add_argument("--search", action="store_true", help="run each slot's query against Shopify")
    ap.add_argument("--out", type=Path, nargs="?", const=Path("-"), default=None)
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)

    path = resolve(args.scene)
    scene = SceneAnalysis.model_validate(json.loads(path.read_text()))
    plan = await create_design_plan(scene, intent_text=args.intent, budget_usd=args.budget)

    print(f"\nscene={path.name} ({scene.scene_type}, {len(scene.objects)} objects, "
          f"{len(scene.available_positions)} open positions)")
    print(f"intent: {args.intent}")
    print(f"model={plan.model}  latency={plan.latency_ms}ms")
    print(f"summary: {plan.summary}")
    print(f"style={plan.style}  colors={plan.color_direction}")
    print(f"budget=${plan.budget_usd:.0f}  allocated=${plan.allocated_usd:.2f}")
    print(f"keep={plan.keep_object_ids}\nremove={plan.remove_object_ids}")

    print("\nSLOTS")
    for s in plan.slots:
        size = "×".join("?" if v is None else f"{v:.2f}" for v in (s.max_width_m, s.max_depth_m, s.max_height_m))
        print(f"  P{s.priority} {s.id:<22} ≤${s.max_price_usd:<7.2f} {s.surface_id}/{s.region:<6} "
              f"(pos {s.position_id})  proxy={s.proxy_model}  max {size} m")
        print(f"      query: \"{s.search_query}\"  color={s.preferred_color}")
        print(f"      why:   {s.reason}")
        if args.search:
            q = s.search_query
            if s.preferred_color and s.preferred_color not in q.lower():
                q = f"{s.preferred_color} {q}"
            try:
                hits = await search_catalog(q, max_price_usd=s.max_price_usd, limit=2)
                for h in hits:
                    print(f"      shop:  ${h.price_usd}  {h.title[:70]}")
                if not hits:
                    print("      shop:  (no results under the cap — Carbon re-plan would trigger)")
            except Exception as exc:  # noqa: BLE001
                print(f"      shop:  error {exc}")

    print("\nWARNINGS" if plan.warnings else "\nWARNINGS: none")
    for w in plan.warnings:
        print(f"  ! {w}")

    if args.out == Path("-"):
        args.out = path.with_name(path.name.replace(".scene.json", "").removesuffix(".json") + ".plan.json")
    if args.out:
        args.out.write_text(plan.model_dump_json(indent=2))
        print(f"\nSaved → {args.out}")
    return 0 if plan.model != "fallback" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
