"""Shared fixtures: a realistic living-room scene built from the sample RoomPlan scan."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.gemini_scene import HydrogenOutput, build_scene
from app.scene_geometry import FLOOR_ID, measure_room
from app.schemas import RoomPlanPayload, SceneAnalysis

FIXTURES = Path(__file__).parent / "fixtures"

LIVING_ROOM_HYDROGEN = {
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
         "estimated_clearance_m": 0.6, "suggested_categories": ["floor_lamp", "plant", "side_table"]},
        {"surface_id": "table_1_top", "region": "front", "description": "Front of coffee table",
         "suggested_categories": ["tray", "book"]},
        {"surface_id": FLOOR_ID, "region": "right", "description": "Right of sofa near window",
         "estimated_clearance_m": 0.8, "suggested_categories": ["plant", "floor_lamp"]},
    ],
}


@pytest.fixture
def room_plan() -> RoomPlanPayload:
    return RoomPlanPayload.model_validate(json.loads((FIXTURES / "roomplan_living_room.json").read_text()))


@pytest.fixture
def living_room_scene(room_plan) -> SceneAnalysis:
    return build_scene(
        HydrogenOutput.model_validate(LIVING_ROOM_HYDROGEN), measure_room(room_plan), None, model="test"
    )


@pytest.fixture
def empty_room_scene() -> SceneAnalysis:
    return SceneAnalysis.model_validate(json.loads((FIXTURES / "scene_empty_room.json").read_text()))
