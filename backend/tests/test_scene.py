"""Hydrogen scene parse — offline tests (no Gemini calls).

Run: python -m pytest -q
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app import gemini_scene
from app.gemini_scene import HydrogenOutput, analyze_scene, build_scene
from app.scene_geometry import FLOOR_ID, measure_room, normalize_category
from app.schemas import RoomPlanPayload, SurfaceCalibration

FIXTURE = Path(__file__).parent / "fixtures" / "roomplan_living_room.json"


@pytest.fixture
def room_plan() -> RoomPlanPayload:
    return RoomPlanPayload.model_validate(json.loads(FIXTURE.read_text()))


def hydro(**overrides) -> HydrogenOutput:
    base = {
        "scene_type": "living_room",
        "style_cues": ["cozy", "modern"],
        "palette": ["mustard", "charcoal", "white"],
        "lighting": "natural",
        "clutter_level": "low",
        "surface_estimates": [],
        "measured_objects": [
            {"ref": "rp_1", "visible_in_photo": True, "color": "mustard", "material": "velvet", "keep": True},
            {"ref": "rp_2", "visible_in_photo": True, "color": "black", "keep": True},
            {"ref": "rp_3", "visible_in_photo": False, "keep": True},
            {"ref": "rp_4", "visible_in_photo": False, "keep": True},
            {"ref": "rp_5", "visible_in_photo": False, "keep": True},
        ],
        "extra_objects": [
            {"type": "plant", "label": "potted plant", "surface_id": "table_1_top"},
            {"type": "curtain", "label": "grey curtain", "surface_id": FLOOR_ID},
        ],
        "available_positions": [
            {"surface_id": FLOOR_ID, "region": "left", "description": "Beside the sofa",
             "suggested_categories": ["floor_lamp"]},
            {"surface_id": "storage_1_top", "region": "center", "description": "TV console top"},
        ],
    }
    base.update(overrides)
    return HydrogenOutput.model_validate(base)


# --------------------------------------------------------------- geometry


def test_room_footprint_from_walls(room_plan):
    room = measure_room(room_plan)
    floor = room.surfaces[0]
    assert floor.id == FLOOR_ID
    assert (floor.width_m, floor.depth_m) == (4.2, 3.8)
    assert floor.dimensions_source.value == "roomplan"
    assert room.ceiling_height_m == 2.55


def test_floor_level_and_surface_heights(room_plan):
    room = measure_room(room_plan)
    assert room.floor_y == pytest.approx(-1.45)
    tops = {s.id: s for s in room.surfaces}
    assert tops["table_1_top"].height_m == pytest.approx(0.45, abs=0.01)
    assert tops["storage_1_top"].height_m == pytest.approx(0.52, abs=0.01)
    assert tops["table_1_top"].width_m == 1.05


def test_chair_and_sofa_are_not_primary_surface(room_plan):
    """Regression: the old merge let the last RoomPlan item (a chair) become surfaces[0]."""
    room = measure_room(room_plan)
    ids = room.surface_ids
    assert ids[0] == FLOOR_ID
    assert not any(i.startswith(("chair", "sofa", "television")) for i in ids)


def test_category_normalization():
    assert normalize_category("Category.table") == "table"
    assert normalize_category("washerDryer") == "washerdryer"


def test_partial_scan_warns():
    rp = RoomPlanPayload(walls=[{"dimensions": {"width": 3.0, "height": 2.4}, "transform": {"tx": 0, "ty": 0, "tz": -1}}])
    room = measure_room(rp)
    assert room.room_width_m == 3.0
    assert any("partial" in w for w in room.warnings)


# --------------------------------------------------------------- merge


def test_merge_keeps_lidar_numbers_and_enriches(room_plan):
    scene = build_scene(hydro(), measure_room(room_plan), None, model="test")
    sofa = next(o for o in scene.objects if o.id == "rp_1")
    assert (sofa.width_m, sofa.color, sofa.material) == (2.12, "mustard", "velvet")
    assert sofa.dimensions_source.value == "roomplan"
    assert sofa.roomplan_id.endswith("5E01")
    assert sofa.position_m["y"] == pytest.approx(0.0, abs=0.01)
    tv = next(o for o in scene.objects if o.id == "rp_4")
    assert tv.visible_in_photo is False
    plant = next(o for o in scene.objects if o.type == "plant")
    assert plant.surface_id == "table_1_top"
    assert plant.dimensions_source.value == "approximate"
    assert scene.lighting == "natural"
    assert scene.warnings == []


def test_merge_remaps_hallucinated_surface_ids(room_plan):
    h = hydro(
        available_positions=[{"surface_id": "desk_surface", "region": "right", "description": "?"}],
        extra_objects=[{"type": "lamp", "label": "lamp", "surface_id": "shelf_9"}],
    )
    scene = build_scene(h, measure_room(room_plan), None, model="test")
    assert scene.available_positions[0].surface_id == FLOOR_ID
    assert next(o for o in scene.objects if o.type == "lamp").surface_id == FLOOR_ID
    assert sum("unknown surface" in w for w in scene.warnings) == 2


def test_merge_flags_skipped_and_unknown_refs(room_plan):
    h = hydro(measured_objects=[
        {"ref": "rp_1", "visible_in_photo": True, "keep": True},
        {"ref": "rp_99", "visible_in_photo": True, "keep": True},
    ])
    scene = build_scene(h, measure_room(room_plan), None, model="test")
    assert any("rp_99" in w for w in scene.warnings)
    assert any("skipped" in w and "rp_2" in w for w in scene.warnings)


def test_no_duplicate_object_ids(room_plan):
    h = hydro(extra_objects=[{"type": "cup", "label": "cup", "surface_id": "table_1_top"}] * 2)
    scene = build_scene(h, measure_room(room_plan), None, model="test")
    ids = [o.id for o in scene.objects]
    assert len(ids) == len(set(ids))


def test_calibration_without_roomplan():
    cal = SurfaceCalibration(surface_id="desk", width_m=1.2, depth_m=0.6, height_m=0.74)
    scene = build_scene(hydro(measured_objects=[], available_positions=[]), measure_room(None), cal, model="t")
    assert scene.surfaces[0].id == "desk"
    assert scene.surfaces[0].dimensions_source.value == "user_measurement"
    assert any("No RoomPlan" in w for w in scene.warnings)


def test_surface_estimates_used_only_without_lidar(room_plan):
    est = [{"id": "guess", "label": "floor", "approx_width_m": 9.0}]
    with_lidar = build_scene(hydro(surface_estimates=est), measure_room(room_plan), None, model="t")
    assert "guess" not in [s.id for s in with_lidar.surfaces]
    no_lidar = build_scene(hydro(surface_estimates=est, available_positions=[]), measure_room(None), None, model="t")
    assert no_lidar.surfaces[0].id == "guess"
    assert no_lidar.surfaces[0].dimensions_source.value == "approximate"


# --------------------------------------------------------------- analyze_scene (mocked model)


def _tiny_jpeg() -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (3000, 2000), "white").save(buf, format="JPEG")
    return buf.getvalue()


def test_analyze_scene_retries_then_succeeds(monkeypatch, room_plan):
    calls = {"n": 0}

    async def fake(images, room, intent):
        calls["n"] += 1
        assert max(__import__("PIL.Image", fromlist=["Image"]).open(
            __import__("io").BytesIO(images[0][0])).size) <= gemini_scene.MAX_IMAGE_SIDE
        if calls["n"] == 1:
            raise json.JSONDecodeError("bad", "x", 0)
        return hydro()

    monkeypatch.setattr(gemini_scene, "_call_hydrogen", fake)
    scene = asyncio.run(analyze_scene(_tiny_jpeg(), room_plan=room_plan))
    assert calls["n"] == 2
    assert scene.model != "fallback"
    assert any("retry" in w for w in scene.warnings)


def test_analyze_scene_fallback_keeps_geometry(monkeypatch, room_plan):
    async def boom(*_):
        raise RuntimeError("quota exceeded")

    monkeypatch.setattr(gemini_scene, "_call_hydrogen", boom)
    scene = asyncio.run(analyze_scene(_tiny_jpeg(), room_plan=room_plan))
    assert scene.model == "fallback"
    assert scene.surfaces[0].width_m == 4.2  # measurements survive model failure
    assert len([o for o in scene.objects if o.id.startswith("rp_")]) == 5
    assert any("quota exceeded" in w for w in scene.warnings)


def test_pipeline_uses_slot_surface_height(room_plan):
    """Regression: every placement used surfaces[0] height, so table-top items sat on the floor."""
    from app.pipeline import _surface_height

    scene = build_scene(hydro(), measure_room(room_plan), None, model="t")
    assert _surface_height(scene, "table_1_top") == pytest.approx(0.45, abs=0.01)
    assert _surface_height(scene, "floor") == 0.0
    assert _surface_height(scene, "missing") == 0.0
