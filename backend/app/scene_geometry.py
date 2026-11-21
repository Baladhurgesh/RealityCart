"""Deterministic geometry from RoomPlan LiDAR — the trusted half of the scene.

Hydrogen (Gemini) is good at *what* is in a room; RoomPlan is good at *how big*
and *where*. This module turns the iOS CapturedRoom export into measured
surfaces and objects without any model call, so it is fast, testable and never
hallucinates meters.

iOS export shape (see RoomPlanScanView.exportRoom):
    objects[]: {id, category, dimensions{width,height,depth}, transform{tx,ty,tz}, confidence}
    walls[]:   {id, category:"wall", dimensions{width,height,depth}, transform{tx,ty,tz}}
RoomPlan positions are object *centers* in world space (meters, y up). The
world origin is wherever the scan started, so heights are re-based to the floor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.schemas import DimensionsSource, RoomPlanPayload, SceneObject, Surface

# RoomPlan categories whose top is a usable placement surface.
TOP_SURFACE_CATEGORIES = {"table", "storage", "desk", "dresser", "nightstand", "counter"}

FLOOR_ID = "floor"


@dataclass
class MeasuredObject:
    ref: str  # short id we show Gemini, e.g. "rp_1"
    roomplan_id: str
    category: str
    width_m: float | None
    height_m: float | None
    depth_m: float | None
    center: tuple[float, float, float] | None  # world tx, ty, tz
    confidence: str | None


@dataclass
class MeasuredRoom:
    objects: list[MeasuredObject] = field(default_factory=list)
    surfaces: list[Surface] = field(default_factory=list)
    floor_y: float | None = None
    room_width_m: float | None = None
    room_depth_m: float | None = None
    ceiling_height_m: float | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def surface_ids(self) -> list[str]:
        return [s.id for s in self.surfaces]


def _num(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _dims(entry: dict[str, Any]) -> tuple[float | None, float | None, float | None]:
    d = entry.get("dimensions") or {}
    return (
        _num(d.get("width", d.get("x"))),
        _num(d.get("height", d.get("y"))),
        _num(d.get("depth", d.get("z"))),
    )


def _center(entry: dict[str, Any]) -> tuple[float, float, float] | None:
    t = entry.get("transform") or {}
    xyz = (_num(t.get("tx")), _num(t.get("ty")), _num(t.get("tz")))
    return None if any(v is None for v in xyz) else xyz  # type: ignore[return-value]


def normalize_category(raw: str) -> str:
    """RoomPlan's String(describing:) can yield 'table' or 'Category.table'."""
    return raw.split(".")[-1].strip().lower().replace(" ", "_") or "furniture"


def measure_room(room_plan: RoomPlanPayload | None) -> MeasuredRoom:
    room = MeasuredRoom()
    if room_plan is None:
        return room

    # --- objects ------------------------------------------------------------
    for i, entry in enumerate(room_plan.objects, start=1):
        w, h, d = _dims(entry)
        room.objects.append(
            MeasuredObject(
                ref=f"rp_{i}",
                roomplan_id=str(entry.get("id") or f"rp_{i}"),
                category=normalize_category(str(entry.get("category") or entry.get("type") or "furniture")),
                width_m=w,
                height_m=h,
                depth_m=d,
                center=_center(entry),
                confidence=(str(entry["confidence"]).split(".")[-1] if entry.get("confidence") else None),
            )
        )

    # --- floor level --------------------------------------------------------
    bottoms = [o.center[1] - o.height_m / 2 for o in room.objects if o.center and o.height_m]
    wall_bottoms = []
    wall_heights = []
    for wall in room_plan.walls:
        _, wh, _ = _dims(wall)
        c = _center(wall)
        if wh:
            wall_heights.append(wh)
            if c:
                wall_bottoms.append(c[1] - wh / 2)
    candidates = bottoms + wall_bottoms
    room.floor_y = min(candidates) if candidates else None
    room.ceiling_height_m = round(max(wall_heights), 2) if wall_heights else None

    # --- floor footprint from wall centers ------------------------------------
    wall_centers = [c for c in (_center(w) for w in room_plan.walls) if c]
    wall_widths = [w for w, _, _ in (_dims(x) for x in room_plan.walls) if w]
    if len(wall_centers) >= 3:
        xs = [c[0] for c in wall_centers]
        zs = [c[2] for c in wall_centers]
        span_x, span_z = max(xs) - min(xs), max(zs) - min(zs)
        if span_x > 0.5 and span_z > 0.5:
            room.room_width_m, room.room_depth_m = round(span_x, 2), round(span_z, 2)
    if room.room_width_m is None and wall_widths:
        # Partial scan: longest wall is a lower bound on one side.
        room.room_width_m = round(max(wall_widths), 2)
        room.warnings.append("Room footprint estimated from a partial wall scan.")

    if room.room_width_m:
        room.surfaces.append(
            Surface(
                id=FLOOR_ID,
                type="horizontal_plane",
                label="floor",
                width_m=room.room_width_m,
                depth_m=room.room_depth_m,
                height_m=0.0,
                dimensions_source=DimensionsSource.roomplan,
                confidence=0.85 if room.room_depth_m else 0.5,
            )
        )
    elif room_plan.objects:
        room.warnings.append("No usable walls in RoomPlan scan; floor size unknown.")

    # --- top surfaces of tables / storage --------------------------------------
    counts: dict[str, int] = {}
    for o in room.objects:
        if o.category not in TOP_SURFACE_CATEGORIES or not o.width_m:
            continue
        counts[o.category] = counts.get(o.category, 0) + 1
        top = None
        if o.center and o.height_m is not None and room.floor_y is not None:
            top = round(o.center[1] + o.height_m / 2 - room.floor_y, 3)
        elif o.height_m is not None:
            top = o.height_m
        room.surfaces.append(
            Surface(
                id=f"{o.category}_{counts[o.category]}_top",
                type="horizontal_plane",
                label=f"top of {o.category} ({o.ref})",
                width_m=o.width_m,
                depth_m=o.depth_m,
                height_m=top,
                dimensions_source=DimensionsSource.roomplan,
                confidence=0.9 if o.confidence == "high" else 0.75,
            )
        )
    return room


def measured_scene_objects(room: MeasuredRoom) -> list[SceneObject]:
    """RoomPlan objects as SceneObjects (ids = short refs, dims trusted)."""
    out = []
    for o in room.objects:
        pos = None
        if o.center and room.floor_y is not None:
            base = o.center[1] - (o.height_m or 0) / 2 - room.floor_y
            pos = {"x": round(o.center[0], 3), "y": round(max(base, 0.0), 3), "z": round(o.center[2], 3)}
        out.append(
            SceneObject(
                id=o.ref,
                type=o.category,
                label=o.category,
                surface_id=FLOOR_ID if any(s.id == FLOOR_ID for s in room.surfaces) else None,
                width_m=o.width_m,
                depth_m=o.depth_m,
                height_m=o.height_m,
                keep=True,
                dimensions_source=DimensionsSource.roomplan,
                roomplan_id=o.roomplan_id,
                position_m=pos,
                notes=f"RoomPlan confidence: {o.confidence}" if o.confidence else None,
            )
        )
    return out


def describe_for_prompt(room: MeasuredRoom) -> str:
    """Compact, model-friendly summary of the trusted measurements."""
    if not room.objects and not room.surfaces:
        return "No LiDAR measurements available."
    lines = []
    if room.room_width_m:
        lines.append(
            f"Room footprint ≈ {room.room_width_m} m × {room.room_depth_m or '?'} m, "
            f"ceiling ≈ {room.ceiling_height_m or '?'} m."
        )
    lines.append("Measured objects (ref | category | W×D×H m | scan confidence):")
    for o in room.objects:
        dims = "×".join(f"{v:.2f}" if v is not None else "?" for v in (o.width_m, o.depth_m, o.height_m))
        lines.append(f"- {o.ref} | {o.category} | {dims} | {o.confidence or '?'}")
    lines.append("Placement surfaces you may reference (surface_id | what | W×D m | height above floor m):")
    for s in room.surfaces:
        lines.append(f"- {s.id} | {s.label} | {s.width_m}×{s.depth_m} | {s.height_m}")
    return "\n".join(lines)
