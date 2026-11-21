"""Carbon design plan — offline tests (no Gemini calls).

Run: python -m pytest -q
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app import gemini_plan
from app.gemini_plan import CarbonOutput, assemble_plan, build_prompt, create_design_plan, replan_after_failures
from app.plan_rules import (
    BUDGET_HEADROOM,
    SlotDraft,
    allocate_budget,
    clean_query,
    colors_in,
    finalize_slots,
    proxy_for,
    validate_object_ids,
)


def carbon(**overrides) -> CarbonOutput:
    base = {
        "summary": "Warm minimalist refresh with black accents",
        "style": "warm minimalist",
        "color_direction": ["black", "walnut"],
        "object_decisions": [
            {"object_id": "rp_1", "action": "keep", "reason": "user loves the sofa"},
            {"object_id": "seen_plant_1", "action": "remove", "reason": "clutter"},
        ],
        "slots": [
            {"position_id": "slot_1_left", "category": "floor_lamp", "search_query": "black arc floor lamp",
             "preferred_color": "black", "priority": 1, "max_price_usd": 150, "max_width_m": 0.5,
             "max_height_m": 1.8, "reason": "Reading light by the sofa"},
            {"position_id": "slot_2_front", "category": "tray", "search_query": "walnut coffee table tray",
             "priority": 2, "max_price_usd": 40, "max_width_m": 0.5, "reason": "Corral small items"},
            {"position_id": "slot_3_right", "category": "plant", "search_query": "tall faux olive tree black pot",
             "priority": 3, "max_price_usd": 90, "reason": "Soften the corner"},
        ],
    }
    base.update(overrides)
    return CarbonOutput.model_validate(base)


# --------------------------------------------------------------- small rules


@pytest.mark.parametrize("category,proxy", [
    ("floor_lamp", "lamp"), ("pendant_light", "lamp"), ("faux_olive_tree_planter", "plant"),
    ("wall_shelf", "shelf"), ("sideboard", "sideboard"), ("storage_cabinet", "sideboard"),
    ("coffee_table_tray", "tray"), ("side_table", "sideboard"), ("rug", "organizer"),  # unknown → organizer
])
def test_proxy_mapping_matches_ios_shapes(category, proxy):
    assert proxy_for(category) == proxy


def test_colors_and_query_cleanup():
    assert colors_in("All MATTE BLACK with some walnut, $600") == ["walnut", "matte black"]
    assert clean_query("black floor lamp under $120!! budget friendly") == "black floor lamp friendly"
    assert len(clean_query("a b c d e f g h i j k").split()) == 8


def test_budget_scales_caps_down():
    w: list[str] = []
    drafts = [SlotDraft("p", "lamp", "q", 400, 1), SlotDraft("p", "rug", "q", 400, 2)]
    out = allocate_budget(drafts, 600, w)
    assert sum(d.max_price_usd for d in out) == pytest.approx(600 * BUDGET_HEADROOM, abs=0.02)
    assert any("Scaled" in x for x in w)


def test_budget_drops_lowest_priority_when_caps_get_too_small():
    w: list[str] = []
    drafts = [SlotDraft("p", "lamp", "q", 100, 1), SlotDraft("p", "rug", "q", 100, 2),
              SlotDraft("p", "plant", "q", 100, 3)]
    out = allocate_budget(drafts, 25, w)  # $23.75 usable: 3 or 2 slots would each be < $15
    assert [d.category for d in out] == ["lamp"]
    assert any("Dropped 'plant'" in x for x in w) and any("Dropped 'rug'" in x for x in w)


# --------------------------------------------------------------- slot guard-rails


def test_slots_inherit_surface_and_region_from_position(living_room_scene):
    plan = assemble_plan(carbon(), living_room_scene, "black accents", 600, model="t")
    lamp, tray = plan.slots[0], plan.slots[1]
    assert (lamp.surface_id, lamp.region, lamp.position_id) == ("floor", "left", "slot_1_left")
    assert (tray.surface_id, tray.region) == ("table_1_top", "front")
    assert lamp.proxy_model == "lamp" and tray.proxy_model == "tray"
    assert [s.priority for s in plan.slots] == [1, 2, 3]
    assert plan.warnings == []


def test_sizes_clamped_to_clearance_and_surface(living_room_scene):
    plan = assemble_plan(carbon(), living_room_scene, "", 600, model="t")
    lamp = plan.slots[0]
    assert lamp.max_width_m == 0.5  # under the 0.6 m clearance → unchanged
    big = carbon(slots=[{"position_id": "slot_2_front", "category": "tray", "search_query": "huge tray",
                         "priority": 1, "max_price_usd": 30, "max_width_m": 2.0, "max_depth_m": 1.5,
                         "reason": "x"}])
    tray = assemble_plan(big, living_room_scene, "", 600, model="t").slots[0]
    assert (tray.max_width_m, tray.max_depth_m) == (1.05, 0.55)  # coffee-table top


def test_unknown_position_is_remapped_by_category(living_room_scene):
    bad = carbon(slots=[{"position_id": "corner_by_window", "category": "tray", "search_query": "tray",
                         "priority": 1, "max_price_usd": 30, "reason": "x"}])
    plan = assemble_plan(bad, living_room_scene, "", 600, model="t")
    assert plan.slots[0].position_id == "slot_2_front"  # Hydrogen suggested trays there
    assert any("unknown position" in w for w in plan.warnings)


def test_duplicates_removed_and_slot_count_capped(living_room_scene, monkeypatch):
    monkeypatch.setattr(gemini_plan.get_settings(), "plan_max_slots", 2)
    s = {"position_id": "slot_1_left", "category": "floor_lamp", "search_query": "lamp",
         "priority": 1, "max_price_usd": 50, "reason": "x"}
    extra = {**s, "position_id": "slot_3_right", "category": "plant", "priority": 3}
    rug = {**s, "position_id": "slot_3_right", "category": "rug", "priority": 2}
    plan = assemble_plan(carbon(slots=[s, s, extra, rug]), living_room_scene, "", 600, model="t")
    assert [x.category for x in plan.slots] == ["floor_lamp", "rug"]
    assert any("duplicate" in w for w in plan.warnings)
    assert any("deferred: plant" in w for w in plan.warnings)


def test_allocation_never_exceeds_budget(living_room_scene):
    plan = assemble_plan(carbon(), living_room_scene, "", 150, model="t")
    assert plan.allocated_usd <= 150 * BUDGET_HEADROOM + 0.01
    assert all(s.max_price_usd >= 15 for s in plan.slots)


def test_object_decisions_validated(living_room_scene):
    w: list[str] = []
    keep, remove = validate_object_ids(living_room_scene, ["rp_1", "ghost_9"], ["seen_plant_1", "rp_1"], w)
    assert "rp_1" in remove and "rp_1" not in keep  # remove wins
    assert "rp_4" in keep  # unmentioned → Hydrogen default keep
    assert any("ghost_9" in x for x in w)


# --------------------------------------------------------------- fallback


def test_fallback_uses_hydrogen_suggestions_and_intent_color(empty_room_scene):
    plan = assemble_plan(None, empty_room_scene, "cozy reading nook, all black", 400, model="fallback")
    assert plan.model == "fallback"
    assert len(plan.slots) == 3
    assert plan.slots[0].category == "rug"  # first suggestion at slot_1_center
    assert all(s.preferred_color == "black" for s in plan.slots)
    assert len({s.proxy_model for s in plan.slots}) == 3  # varied items, not 3 rugs
    assert plan.allocated_usd <= 400 * BUDGET_HEADROOM + 0.01


def test_prompt_contains_scene_facts(living_room_scene):
    p = build_prompt(living_room_scene, "add a black floor lamp", 600)
    for needle in ("slot_1_left", "table_1_top", "rp_1 | sofa", "mustard", "$570", "COLORS THE USER ASKED FOR: black"):
        assert needle in p
    r = build_prompt(living_room_scene, "x", 600, failures=["slot_1: no results for 'arc lamp'"])
    assert "RE-PLAN" in r and "arc lamp" in r


# --------------------------------------------------------------- async paths (mocked model)


def test_retry_then_success(monkeypatch, living_room_scene):
    calls = {"n": 0}

    async def fake(prompt, *, temperature):
        calls["n"] += 1
        if calls["n"] == 1:
            raise json.JSONDecodeError("bad", "x", 0)
        return carbon()

    monkeypatch.setattr(gemini_plan, "_call_carbon", fake)
    plan = asyncio.run(create_design_plan(living_room_scene, intent_text="black", budget_usd=600))
    assert calls["n"] == 2 and plan.model != "fallback"
    assert any("retry" in w for w in plan.warnings)


def test_model_failure_gives_rule_based_plan(monkeypatch, living_room_scene):
    async def boom(*_, **__):
        raise RuntimeError("quota exceeded")

    monkeypatch.setattr(gemini_plan, "_call_carbon", boom)
    plan = asyncio.run(create_design_plan(living_room_scene, intent_text="black", budget_usd=600))
    assert plan.model == "fallback" and plan.slots
    assert any("quota exceeded" in w for w in plan.warnings)


def test_replan_failure_keeps_previous_plan(monkeypatch, living_room_scene):
    original = assemble_plan(carbon(), living_room_scene, "", 600, model="t")

    async def boom(*_, **__):
        raise RuntimeError("down")

    monkeypatch.setattr(gemini_plan, "_call_carbon", boom)
    out = asyncio.run(replan_after_failures(
        living_room_scene, original, failures=["x"], intent_text="", budget_usd=600))
    assert out is original and any("Re-plan failed" in w for w in out.warnings)


def test_replace_goes_in_old_footprint(living_room_scene):
    """Regression: a replacement sectional was squeezed into a 0.6 m side gap."""
    out = carbon(
        object_decisions=[{"object_id": "rp_1", "action": "replace", "reason": "user wants leather"}],
        slots=[{"position_id": "replace:rp_1", "category": "sectional_sofa", "search_query": "black leather sectional",
                "priority": 1, "max_price_usd": 500, "max_width_m": 2.4, "reason": "x"}],
    )
    plan = assemble_plan(out, living_room_scene, "", 600, model="t")
    sofa = plan.slots[0]
    assert sofa.position_id == "replace:rp_1"
    assert (sofa.surface_id, sofa.region) == ("floor", "center")  # sofa centre x≈0.05
    assert sofa.max_width_m == 2.4  # allowed: ≤ 2.12 m × 1.2
    assert "rp_1" in plan.remove_object_ids and plan.warnings == []


def test_replace_slot_implies_removal_and_accepts_bare_id(living_room_scene):
    out = carbon(
        object_decisions=[{"object_id": "rp_1", "action": "keep", "reason": "?"}],
        slots=[{"position_id": "rp_1", "category": "sofa", "search_query": "sofa",
                "priority": 1, "max_price_usd": 400, "max_width_m": 3.5, "reason": "x"}],
    )
    plan = assemble_plan(out, living_room_scene, "", 600, model="t")
    assert plan.slots[0].position_id == "replace:rp_1"
    assert plan.slots[0].max_width_m == pytest.approx(2.54)  # clamped to footprint × 1.2
    assert "rp_1" in plan.remove_object_ids and "rp_1" not in plan.keep_object_ids
    assert any("marked for removal" in w for w in plan.warnings)


def test_hung_carbon_call_times_out_and_retries(monkeypatch, living_room_scene):
    calls = {"n": 0}

    async def slow_then_ok(prompt, *, temperature):
        calls["n"] += 1
        if calls["n"] == 1:
            await asyncio.sleep(5)
        return carbon()

    monkeypatch.setattr(gemini_plan.get_settings(), "plan_timeout_s", 0.05)
    monkeypatch.setattr(gemini_plan, "_call_carbon", slow_then_ok)
    plan = asyncio.run(create_design_plan(living_room_scene, intent_text="", budget_usd=600))
    assert calls["n"] == 2 and plan.model != "fallback"
    assert any("timed out" in w for w in plan.warnings)
