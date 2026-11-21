#!/usr/bin/env python3
"""Offline smoke: schemas + fallback scene/plan without Gemini key."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.gemini_plan import _fallback_plan
from app.gemini_scene import _fallback_scene
from app.schemas import SurfaceCalibration


def main() -> None:
    cal = SurfaceCalibration(width_m=1.2, depth_m=0.6, height_m=0.74)
    scene = _fallback_scene(cal, None)
    plan = _fallback_plan(scene, 600, "minimalist black desk")
    assert scene.surfaces[0].width_m == 1.2
    assert scene.surfaces[0].dimensions_source.value == "user_measurement"
    assert len(plan.slots) >= 2
    print("offline pipeline OK")
    print("scene:", scene.scene_type, "surfaces:", len(scene.surfaces))
    print("plan slots:", [s.category for s in plan.slots])


if __name__ == "__main__":
    main()
