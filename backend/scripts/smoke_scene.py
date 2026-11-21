#!/usr/bin/env python3
"""Live Hydrogen scene-parse check on a real photo (needs GEMINI_API_KEY).

Usage:
    python scripts/smoke_scene.py                      # list photos in eval/captures/
    python scripts/smoke_scene.py my_room.jpg          # bare name → eval/captures/my_room.jpg
    python scripts/smoke_scene.py PHOTO.jpg
    python scripts/smoke_scene.py PHOTO.jpg --room-plan scan.json --intent "minimalist black, $600"
    python scripts/smoke_scene.py PHOTO.jpg --no-room-plan        # stress: photo only
    python scripts/smoke_scene.py PHOTO.jpg --out                  # save JSON next to the photo
    python scripts/smoke_scene.py PHOTO.jpg --out result.json      # save JSON to a chosen file

Without --room-plan, the sample living-room scan in tests/fixtures is used.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import mimetypes
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.gemini_scene import analyze_scene  # noqa: E402
from app.schemas import RoomPlanPayload  # noqa: E402

SAMPLE_SCAN = ROOT / "tests" / "fixtures" / "roomplan_living_room.json"
CAPTURES = ROOT.parent / "eval" / "captures"  # gitignored — put real photos here
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".webp"}


def resolve_photo(arg: Path | None) -> Path | None:
    if arg is None:
        photos = sorted(p.name for p in CAPTURES.iterdir() if p.suffix.lower() in IMAGE_EXTS)
        print(f"Photos in {CAPTURES}:")
        print("\n".join(f"  {n}" for n in photos) or "  (none yet — copy some in)")
        return None
    for candidate in (arg, CAPTURES / arg, CAPTURES / arg.name):
        if candidate.is_file():
            return candidate
    raise SystemExit(f"Photo not found: {arg} (also looked in {CAPTURES})")


def fmt(v) -> str:
    return "?" if v is None else f"{v:.2f}"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("photo", type=Path, nargs="?")
    ap.add_argument("--room-plan", type=Path, default=SAMPLE_SCAN)
    ap.add_argument("--no-room-plan", action="store_true")
    ap.add_argument("--intent", default=None)
    ap.add_argument("--out", type=Path, nargs="?", const=Path("-"), default=None,
                    help="Save JSON; with no value → <photo>.scene.json next to the photo")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)
    args.photo = resolve_photo(args.photo)
    if args.photo is None:
        return 0

    room_plan = None
    if not args.no_room_plan:
        room_plan = RoomPlanPayload.model_validate(json.loads(args.room_plan.read_text()))

    mime = mimetypes.guess_type(args.photo.name)[0] or "image/jpeg"
    scene = await analyze_scene(
        args.photo.read_bytes(), mime_type=mime, room_plan=room_plan, intent_hint=args.intent
    )

    print(f"\nmodel={scene.model}  latency={scene.latency_ms}ms  "
          f"scan={'none' if room_plan is None else args.room_plan.name}")
    print(f"scene_type={scene.scene_type}  lighting={scene.lighting}  clutter={scene.clutter_level}")
    print(f"style={scene.style_cues}  palette={scene.palette}  image_issues={scene.image_issues}")

    print("\nSURFACES")
    for s in scene.surfaces:
        print(f"  {s.id:<16} {fmt(s.width_m)}×{fmt(s.depth_m)} m  h={fmt(s.height_m)}  [{s.dimensions_source.value}]")

    print("\nOBJECTS")
    for o in scene.objects:
        src = o.dimensions_source.value if o.dimensions_source else "-"
        seen = {True: "visible", False: "off-photo", None: ""}[o.visible_in_photo]
        look = " ".join(x for x in (o.color, o.material) if x)
        print(f"  {o.id:<16} {o.type:<14} w={fmt(o.width_m)}  on={o.surface_id or '-':<14} "
              f"{'KEEP' if o.keep else 'drop'}  {seen:<9} [{src}] {look}")

    print("\nOPEN POSITIONS")
    for p in scene.available_positions:
        cats = ", ".join(p.suggested_categories)
        print(f"  {p.id:<16} {p.surface_id:<14} {p.region:<7} {p.description}" + (f"  → {cats}" if cats else ""))

    print("\nWARNINGS" if scene.warnings else "\nWARNINGS: none")
    for w in scene.warnings:
        print(f"  ! {w}")
    if scene.notes:
        print(f"\nnotes: {scene.notes}")

    if args.out == Path("-"):
        args.out = args.photo.with_suffix(".scene.json")
    if args.out:
        args.out.write_text(scene.model_dump_json(indent=2))
        print(f"\nSaved → {args.out}")
    return 0 if scene.model != "fallback" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
