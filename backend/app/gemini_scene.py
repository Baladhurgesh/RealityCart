"""gemini-4-hydrogen: multimodal scene parser (photo + RoomPlan → SceneAnalysis).

Division of labour
------------------
* ``scene_geometry`` (no LLM) owns every number that came from LiDAR:
  room footprint, object sizes, surface heights.
* Hydrogen owns semantics: what kind of room, style/palette, which measured
  objects are visible and what they look like, small items LiDAR misses
  (plants, lamps, cups), free regions to place things, and image problems.

Hydrogen may only reference surface ids we hand it; anything else is remapped
and logged in ``scene.warnings`` (Element 119: show where it breaks).
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import time
from typing import Literal

from google.genai import types
from pydantic import BaseModel, Field, ValidationError

from app.config import get_settings
from app.gemini_client import get_genai_client
from app.scene_geometry import (
    FLOOR_ID,
    MeasuredRoom,
    describe_for_prompt,
    measure_room,
    measured_scene_objects,
)
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

MAX_IMAGE_SIDE = 1536
Region = Literal["left", "right", "center", "back", "front"]


# --------------------------------------------------------------------------
# Structured-output schema Hydrogen must follow (enforced by Gemini).
# --------------------------------------------------------------------------


class HMeasuredObjectNote(BaseModel):
    ref: str = Field(description="Exact ref from the measured-objects list, e.g. rp_1")
    visible_in_photo: bool
    color: str | None = None
    material: str | None = None
    keep: bool = Field(description="False only if it is clutter or clearly unwanted")
    notes: str | None = None


class HExtraObject(BaseModel):
    type: str = Field(description="snake_case noun, e.g. plant, floor_lamp, cup, rug, curtain")
    label: str
    surface_id: str = Field(description="One of the allowed surface ids")
    location: str | None = Field(default=None, description="e.g. 'on coffee table, center'")
    color: str | None = None
    approx_width_m: float | None = None
    approx_height_m: float | None = None
    keep: bool = True


class HPosition(BaseModel):
    surface_id: str = Field(description="One of the allowed surface ids")
    region: Region
    description: str
    estimated_clearance_m: float | None = None
    suggested_categories: list[str] = Field(default_factory=list)


class HSurfaceEstimate(BaseModel):
    id: str
    label: str
    approx_width_m: float | None = None
    approx_depth_m: float | None = None
    approx_height_m: float | None = None


class HydrogenOutput(BaseModel):
    scene_type: Literal[
        "living_room", "bedroom", "kitchen", "home_office", "dining_room",
        "studio", "bathroom", "hallway", "unknown",
    ]
    style_cues: list[str] = Field(description="2-5 short style words")
    palette: list[str] = Field(description="Dominant colors, e.g. mustard, charcoal, white")
    lighting: Literal["bright", "dim", "mixed", "natural", "unknown"]
    clutter_level: Literal["low", "medium", "high"]
    image_issues: list[str] = Field(
        default_factory=list,
        description="Problems limiting analysis: dim, blurry, partial_view, cluttered, occluded, odd_angle",
    )
    # Field order matters: Gemini emits in schema order, so surface ids are
    # defined before anything references them. Lists below are *required*
    # (no default) — optional lists get silently omitted by the model.
    surface_estimates: list[HSurfaceEstimate] = Field(
        description="ONLY when no measured surfaces are given: rough surfaces you can see; else []",
    )
    measured_objects: list[HMeasuredObjectNote]
    extra_objects: list[HExtraObject]
    available_positions: list[HPosition]
    notes: str | None = None


PROMPT = """You are the scene parser for RealityCart, a shopping redesign app.
Analyze the photo of an indoor space (any room type — do not assume a desk).

Trusted LiDAR measurements (RoomPlan) for this same room:
{measurements}

Allowed surface ids: {surface_ids}

Rules:
- NEVER invent or change measured sizes; LiDAR numbers are ground truth.
- measured_objects: one entry per measured ref above. visible_in_photo=false if it is
  outside the photo (the scan covers the whole room, the photo may not).
- extra_objects: visible items LiDAR does not measure (plants, lamps, decor, rugs,
  curtains, small electronics). Use only allowed surface ids.
- available_positions: 2-5 open spots where a new item could realistically go,
  each on an allowed surface id. Prefer spots visible in the photo.
- image_issues: be honest about dim light, blur, clutter or partial views.
{mode_rules}{intent}"""

NO_LIDAR_RULES = """
NO LIDAR SCAN for this request, so:
- First fill surface_estimates (always include id "floor", plus visible tables,
  shelves, counters) with rough sizes. These ids are now the allowed surface ids.
- extra_objects must list ALL visible items INCLUDING major furniture (sofa, bed,
  table, TV...), each referencing one of your surface_estimates ids.
- available_positions are still required (2-5), on your surface_estimates ids.
"""


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def prepare_image(data: bytes, mime_type: str = "image/jpeg", max_side: int = MAX_IMAGE_SIDE) -> tuple[bytes, str]:
    """EXIF-rotate and downscale so uploads are fast and orientation is right."""
    try:
        from PIL import Image, ImageOps

        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img).convert("RGB")
        if max(img.size) > max_side:
            img.thumbnail((max_side, max_side))
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=85)
        return out.getvalue(), "image/jpeg"
    except Exception as exc:  # noqa: BLE001 — unknown format: send as-is
        logger.warning("Image preprocessing skipped: %s", exc)
        return data, mime_type


def _calibrated(surfaces: list[Surface], calibration: SurfaceCalibration | None) -> list[Surface]:
    if not calibration or not calibration.width_m:
        return surfaces
    update = {
        "width_m": calibration.width_m,
        "depth_m": calibration.depth_m,
        "height_m": calibration.height_m,
        "dimensions_source": DimensionsSource.user_measurement,
        "confidence": 0.95,
    }
    for i, s in enumerate(surfaces):
        if s.id == calibration.surface_id:
            surfaces[i] = s.model_copy(update={k: v for k, v in update.items() if v is not None})
            return surfaces
    return [
        Surface(id=calibration.surface_id, label="calibrated_surface", **{**update, "height_m": calibration.height_m or 0.0}),
        *surfaces,
    ]


def build_scene(
    hydro: HydrogenOutput | None,
    room: MeasuredRoom,
    calibration: SurfaceCalibration | None,
    *,
    model: str,
    warnings: list[str] | None = None,
) -> SceneAnalysis:
    """Merge trusted geometry with Hydrogen semantics. Pure function — unit tested."""
    warnings = list(room.warnings) + list(warnings or [])
    surfaces = list(room.surfaces)

    if not surfaces and hydro and hydro.surface_estimates:
        for est in hydro.surface_estimates:
            surfaces.append(
                Surface(
                    id=est.id,
                    label=est.label,
                    width_m=est.approx_width_m,
                    depth_m=est.approx_depth_m,
                    height_m=0.0 if est.id == FLOOR_ID else est.approx_height_m,
                    dimensions_source=DimensionsSource.approximate,
                    confidence=0.3,
                )
            )
    surfaces = _calibrated(surfaces, calibration)
    if not surfaces:
        surfaces = [Surface(id=FLOOR_ID, label="floor", height_m=0.0, confidence=0.1)]
        warnings.append("No measurements at all — surface size unknown.")

    valid_ids = {s.id for s in surfaces}
    default_sid = FLOOR_ID if FLOOR_ID in valid_ids else surfaces[0].id

    def fix_sid(sid: str | None, what: str) -> str:
        if sid in valid_ids:
            return sid  # type: ignore[return-value]
        warnings.append(f"Hydrogen referenced unknown surface '{sid}' for {what}; used '{default_sid}'.")
        return default_sid

    # Measured objects enriched with what Hydrogen saw.
    objects = measured_scene_objects(room)
    by_ref = {o.id: o for o in objects}
    if hydro:
        seen = set()
        for note in hydro.measured_objects:
            obj = by_ref.get(note.ref)
            if not obj:
                warnings.append(f"Hydrogen described unknown measured object '{note.ref}'.")
                continue
            seen.add(note.ref)
            obj.visible_in_photo = note.visible_in_photo
            obj.color, obj.material, obj.keep = note.color, note.material, note.keep
            obj.notes = "; ".join(x for x in (note.notes, obj.notes) if x) or None
        missing = [r for r in by_ref if r not in seen]
        if missing:
            warnings.append(f"Hydrogen skipped measured objects: {', '.join(missing)}.")

        counts: dict[str, int] = {}
        for extra in hydro.extra_objects:
            counts[extra.type] = counts.get(extra.type, 0) + 1
            objects.append(
                SceneObject(
                    id=f"seen_{extra.type}_{counts[extra.type]}",
                    type=extra.type,
                    label=extra.label,
                    location=extra.location,
                    surface_id=fix_sid(extra.surface_id, extra.label),
                    width_m=extra.approx_width_m,
                    height_m=extra.approx_height_m,
                    keep=extra.keep,
                    color=extra.color,
                    visible_in_photo=True,
                    dimensions_source=DimensionsSource.approximate,
                )
            )

    positions: list[AvailablePosition] = []
    if hydro:
        for i, p in enumerate(hydro.available_positions, start=1):
            positions.append(
                AvailablePosition(
                    id=f"slot_{i}_{p.region}",
                    surface_id=fix_sid(p.surface_id, f"position {i}"),
                    region=p.region,
                    description=p.description,
                    estimated_clearance_m=(
                        p.estimated_clearance_m
                        if p.estimated_clearance_m is None or p.estimated_clearance_m >= 0
                        else None
                    ),
                    suggested_categories=p.suggested_categories,
                )
            )
    if not positions:
        positions = [
            AvailablePosition(id="slot_left", surface_id=default_sid, region="left",
                              description="Open area on the left (default)"),
            AvailablePosition(id="slot_right", surface_id=default_sid, region="right",
                              description="Open area on the right (default)"),
        ]
        if hydro:
            warnings.append("Hydrogen proposed no open positions; using defaults.")

    if not room.objects:
        warnings.append("No RoomPlan scan — sizes are approximate. Scan with LiDAR for real scale.")

    return SceneAnalysis(
        scene_type=hydro.scene_type if hydro else "unknown",
        style_cues=hydro.style_cues if hydro else [],
        palette=hydro.palette if hydro else [],
        surfaces=surfaces,
        objects=objects,
        available_positions=positions,
        clutter_level=hydro.clutter_level if hydro else "medium",
        lighting=hydro.lighting if hydro else "unknown",
        image_issues=hydro.image_issues if hydro else [],
        notes=(hydro.notes if hydro else "Fallback scene — Hydrogen unavailable; geometry only."),
        warnings=warnings,
        model=model,
    )


async def _call_hydrogen(
    images: list[tuple[bytes, str]],
    room: MeasuredRoom,
    intent_hint: str | None,
) -> HydrogenOutput:
    settings = get_settings()
    client = get_genai_client()
    surface_ids = room.surface_ids or ["(none — define them in surface_estimates)"]
    prompt = PROMPT.format(
        measurements=describe_for_prompt(room),
        surface_ids=", ".join(surface_ids),
        mode_rules="" if room.surfaces else NO_LIDAR_RULES,
        intent=f"\nUser intent (context only, do not plan purchases): {intent_hint}" if intent_hint else "",
    )
    parts = [types.Part.from_bytes(data=b, mime_type=m) for b, m in images]
    parts.append(types.Part.from_text(text=prompt))

    response = await client.aio.models.generate_content(
        model=settings.scene_model,
        contents=parts,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=HydrogenOutput,
            temperature=0.2,
            thinking_config=types.ThinkingConfig(thinking_level="low"),
        ),
    )
    if isinstance(response.parsed, HydrogenOutput):
        return response.parsed
    return HydrogenOutput.model_validate(json.loads(response.text or "{}"))


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def _fallback_scene(
    calibration: SurfaceCalibration | None,
    room_plan: RoomPlanPayload | None,
    reason: str | None = None,
) -> SceneAnalysis:
    return build_scene(
        None,
        measure_room(room_plan),
        calibration,
        model="fallback",
        warnings=[f"Hydrogen failed: {reason}"] if reason else None,
    )


async def analyze_scene(
    image_bytes: bytes,
    *,
    mime_type: str = "image/jpeg",
    room_plan: RoomPlanPayload | None = None,
    calibration: SurfaceCalibration | None = None,
    intent_hint: str | None = None,
    extra_images: list[tuple[bytes, str]] | None = None,
    attempts: int = 2,
) -> SceneAnalysis:
    settings = get_settings()
    started = time.perf_counter()
    room = measure_room(room_plan)
    images = [prepare_image(image_bytes, mime_type)]
    images += [prepare_image(b, m) for b, m in (extra_images or [])][:3]

    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            hydro = await asyncio.wait_for(
                _call_hydrogen(images, room, intent_hint), timeout=settings.scene_timeout_s
            )
            scene = build_scene(hydro, room, calibration, model=settings.scene_model)
            if attempt > 1:
                scene.warnings.append(f"Hydrogen succeeded on retry {attempt} (first error: {last_error}).")
            scene.latency_ms = int((time.perf_counter() - started) * 1000)
            logger.info(
                "Hydrogen ok in %dms: type=%s objects=%d positions=%d warnings=%d",
                scene.latency_ms, scene.scene_type, len(scene.objects),
                len(scene.available_positions), len(scene.warnings),
            )
            return scene
        except (ValidationError, json.JSONDecodeError) as exc:
            last_error = exc
            logger.warning("Hydrogen attempt %d returned invalid JSON: %s", attempt, exc)
        except TimeoutError:
            last_error = TimeoutError(f"Hydrogen timed out after {settings.scene_timeout_s:.0f}s")
            logger.warning("Hydrogen attempt %d timed out", attempt)
        except Exception as exc:  # noqa: BLE001 — network / auth / quota
            last_error = exc
            logger.warning("Hydrogen attempt %d failed: %s", attempt, exc)
            if "GEMINI_API_KEY" in str(exc):
                break

    scene = _fallback_scene(calibration, room_plan, reason=str(last_error)[:300])
    scene.latency_ms = int((time.perf_counter() - started) * 1000)
    return scene
