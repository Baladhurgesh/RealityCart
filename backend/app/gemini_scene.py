"""gemini-4-hydrogen: multimodal space/room scene parser."""

from __future__ import annotations

import json
import logging
from typing import Any

from google.genai import types
from pydantic import ValidationError

from app.config import get_settings
from app.gemini_client import get_genai_client
from app.schemas import (
    AvailablePosition,
    DimensionsSource,
    RoomPlanPayload,
    SceneAnalysis,
    SceneObject,
    Surface,
    SurfaceCalibration,
)

logger = logging.getLogger(__name__)

SCENE_SCHEMA_HINT = """
Return ONLY valid JSON matching this shape:
{
  "scene_type": "living_room",
  "style_cues": ["minimalist", "warm"],
  "surfaces": [
    {
      "id": "primary_surface",
      "type": "horizontal_plane",
      "label": "floor_or_table",
      "width_m": 3.2,
      "depth_m": 2.4,
      "height_m": 0.0,
      "dimensions_source": "inferred",
      "confidence": 0.4
    }
  ],
  "objects": [
    {
      "id": "sofa_1",
      "type": "sofa",
      "label": "sofa",
      "location": "against_wall",
      "surface_id": "primary_surface",
      "keep": true,
      "notes": null
    }
  ],
  "available_positions": [
    {
      "id": "slot_corner",
      "surface_id": "primary_surface",
      "region": "left",
      "description": "Open floor area for a floor lamp or plant",
      "estimated_clearance_m": 0.8
    }
  ],
  "clutter_level": "medium",
  "lighting": "mixed",
  "notes": "short note"
}
Rules:
- The space can be ANY indoor scene: living room, bedroom, kitchen, office, studio, hallway, etc.
- Prefer RoomPlan LiDAR measurements over guessing meters.
- If dimensions are unknown, set dimensions_source to "approximate" and lower confidence.
- Identify empty regions suitable for furniture and decor that match the space type.
- Mark major existing furniture the user would likely keep as keep=true unless clearly disposable clutter.
- Use scene_type values like living_room, bedroom, kitchen, home_office, dining_room, studio, unknown.
"""

# RoomPlan furniture categories that often define a usable primary plane.
_PRIMARY_FURNITURE = {
    "table",
    "desk",
    "diningtable",
    "sofa",
    "couch",
    "bed",
    "storage",
    "chair",
    "refrigerator",
    "oven",
    "stove",
    "dishwasher",
    "washer",
    "dryer",
    "television",
    "fireplace",
}


def _merge_measurements(
    scene: SceneAnalysis,
    calibration: SurfaceCalibration | None,
    room_plan: RoomPlanPayload | None,
) -> SceneAnalysis:
    """Override Gemini guesses with trusted measurements."""
    surfaces = list(scene.surfaces)

    if calibration and calibration.width_m:
        sid = calibration.surface_id or "primary_surface"
        found = False
        for i, s in enumerate(surfaces):
            if s.id == sid or s.type == "horizontal_plane":
                surfaces[i] = s.model_copy(
                    update={
                        "width_m": calibration.width_m,
                        "depth_m": calibration.depth_m or s.depth_m,
                        "height_m": calibration.height_m if calibration.height_m is not None else s.height_m,
                        "dimensions_source": DimensionsSource.user_measurement,
                        "confidence": 0.95,
                    }
                )
                found = True
                break
        if not found:
            surfaces.insert(
                0,
                Surface(
                    id=sid,
                    type="horizontal_plane",
                    label="primary_surface",
                    width_m=calibration.width_m,
                    depth_m=calibration.depth_m,
                    height_m=calibration.height_m or 0.0,
                    dimensions_source=DimensionsSource.user_measurement,
                    confidence=0.95,
                ),
            )

    if room_plan and room_plan.objects:
        for obj in room_plan.objects:
            dims = obj.get("dimensions") or {}
            category = str(obj.get("category") or obj.get("type") or "furniture")
            oid = str(obj.get("id") or category)
            if not any(o.id == oid for o in scene.objects):
                scene.objects.append(
                    SceneObject(
                        id=oid,
                        type=category.lower(),
                        label=category,
                        width_m=_m(dims.get("width") or dims.get("x")),
                        depth_m=_m(dims.get("depth") or dims.get("z")),
                        height_m=_m(dims.get("height") or dims.get("y")),
                        keep=True,
                        notes="from_roomplan",
                    )
                )
            # Promote large RoomPlan objects onto the primary surface when useful.
            if category.lower().replace(" ", "") in _PRIMARY_FURNITURE:
                w = _m(dims.get("width") or dims.get("x"))
                d = _m(dims.get("depth") or dims.get("z"))
                h = _m(dims.get("height") or dims.get("y"))
                if w and surfaces:
                    surfaces[0] = surfaces[0].model_copy(
                        update={
                            "width_m": w,
                            "depth_m": d or surfaces[0].depth_m,
                            "height_m": h if h is not None else surfaces[0].height_m,
                            "dimensions_source": DimensionsSource.roomplan,
                            "confidence": 0.9,
                            "label": surfaces[0].label or category.lower(),
                        }
                    )

    # Floor footprint from RoomPlan walls if we still lack a surface.
    if room_plan and room_plan.walls and (not surfaces or surfaces[0].width_m is None):
        widths = []
        for wall in room_plan.walls:
            dims = wall.get("dimensions") or {}
            w = _m(dims.get("width") or dims.get("x"))
            if w:
                widths.append(w)
        if widths:
            approx = max(widths)
            if not surfaces:
                surfaces = [
                    Surface(
                        id="primary_surface",
                        type="horizontal_plane",
                        label="floor",
                        width_m=approx,
                        depth_m=approx * 0.75,
                        height_m=0.0,
                        dimensions_source=DimensionsSource.roomplan,
                        confidence=0.7,
                    )
                ]
            else:
                surfaces[0] = surfaces[0].model_copy(
                    update={
                        "width_m": approx,
                        "depth_m": surfaces[0].depth_m or approx * 0.75,
                        "dimensions_source": DimensionsSource.roomplan,
                        "confidence": 0.7,
                    }
                )

    scene.surfaces = surfaces
    if not scene.available_positions and surfaces:
        scene.available_positions = [
            AvailablePosition(
                id="slot_left",
                surface_id=surfaces[0].id,
                region="left",
                description="Open area on the left of the primary space",
            ),
            AvailablePosition(
                id="slot_right",
                surface_id=surfaces[0].id,
                region="right",
                description="Open area on the right of the primary space",
            ),
        ]
    return scene


def _m(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fallback_scene(
    calibration: SurfaceCalibration | None,
    room_plan: RoomPlanPayload | None,
) -> SceneAnalysis:
    width = calibration.width_m if calibration else None
    scene = SceneAnalysis(
        scene_type="unknown",
        style_cues=["unknown"],
        surfaces=[
            Surface(
                id="primary_surface",
                type="horizontal_plane",
                label="primary_space",
                width_m=width or 3.0,
                depth_m=(calibration.depth_m if calibration else 2.5),
                height_m=(calibration.height_m if calibration else 0.0),
                dimensions_source=(
                    DimensionsSource.user_measurement
                    if width
                    else DimensionsSource.approximate
                ),
                confidence=0.9 if width else 0.2,
            )
        ],
        objects=[],
        available_positions=[
            AvailablePosition(
                id="slot_left",
                surface_id="primary_surface",
                region="left",
                description="Left open area",
            ),
            AvailablePosition(
                id="slot_right",
                surface_id="primary_surface",
                region="right",
                description="Right open area",
            ),
        ],
        notes="Fallback scene — Gemini unavailable or parse failed",
        model="fallback",
    )
    return _merge_measurements(scene, calibration, room_plan)


async def analyze_scene(
    image_bytes: bytes,
    *,
    mime_type: str = "image/jpeg",
    room_plan: RoomPlanPayload | None = None,
    calibration: SurfaceCalibration | None = None,
    intent_hint: str | None = None,
) -> SceneAnalysis:
    settings = get_settings()

    try:
        client = get_genai_client()
        parts: list[types.Part] = [
            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            types.Part.from_text(
                text=(
                    "Analyze this indoor space (any room or area) for a furniture/decor "
                    "shopping redesign. Do not assume it is a desk.\n"
                    f"{SCENE_SCHEMA_HINT}\n"
                )
            ),
        ]
        if room_plan:
            parts.append(
                types.Part.from_text(
                    text="RoomPlan LiDAR JSON (trusted dimensions):\n"
                    + json.dumps(room_plan.model_dump(), default=str)[:12000]
                )
            )
        if calibration:
            parts.append(
                types.Part.from_text(
                    text="User surface calibration (trusted):\n"
                    + calibration.model_dump_json()
                )
            )
        if intent_hint:
            parts.append(types.Part.from_text(text=f"User intent hint: {intent_hint}"))

        response = client.models.generate_content(
            model=settings.scene_model,
            contents=parts,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.2,
            ),
        )
        text = response.text or "{}"
        data = json.loads(text)
        scene = SceneAnalysis.model_validate(data)
        scene.model = settings.scene_model
        return _merge_measurements(scene, calibration, room_plan)
    except (ValidationError, json.JSONDecodeError, Exception) as exc:
        logger.exception("Hydrogen scene parse failed: %s", exc)
        return _fallback_scene(calibration, room_plan)
