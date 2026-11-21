"""Decisions gate + slot filling — offline tests (Decisions API and Shopify mocked).

Run: python -m pytest -q
"""

from __future__ import annotations

import asyncio

import pytest

from app import decisions, gemini_plan, pipeline
from app.decisions import budget_check, evaluate_candidate, pick_best, size_check, slot_digest
from app.gemini_plan import CarbonOutput, assemble_plan
from app.plan_rules import loosen_query, with_size_word
from app.schemas import CandidateEvaluation, PlacementSlot, ProductOffer
from app.shopify_mcp import parse_dimensions_m


def slot(**kw) -> PlacementSlot:
    base = dict(id="slot_1_storage", category="storage_cabinet", surface_id="floor", region="right",
                position_id="slot_3_right", search_query="matte black storage cabinet", max_price_usd=320,
                preferred_color="black", max_width_m=0.5, max_depth_m=0.45, max_height_m=1.2,
                proxy_model="sideboard", priority=1)
    base.update(kw)
    return PlacementSlot(**base)


def product(title="2 Door Storage Cabinet - Black", price=199.0, desc="", img="https://cdn.example/p.jpg", **kw):
    w, d, h, hint = parse_dimensions_m(title, desc)
    base = dict(id=f"gid://{title}", title=title, price_usd=price, description=desc, image_url=img,
                width_m=w, depth_m=d, height_m=h, dimensions_hint=hint)
    base.update(kw)
    return ProductOffer(**base)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def api(monkeypatch):
    """Fake Decisions API: scores keyed by product title substring; records calls."""
    state = {"calls": [], "scores": {}, "fail": 0}

    async def fake(parts, settings):
        state["calls"].append(parts)
        if state["fail"] > 0:
            state["fail"] -= 1
            raise TimeoutError()
        text = parts[0]["text"]
        for key, scores in state["scores"].items():
            if key in text:
                return scores
        return {"fits_surface": (0.9, 0.9), "style_match": (0.9, 0.9)}

    monkeypatch.setattr(decisions, "_call_api", fake)
    return state


# --------------------------------------------------------------- deterministic checks


def test_budget_is_code_not_model():
    assert budget_check(slot(), product(price=319.99)) is None
    assert "over slot cap" in budget_check(slot(), product(price=330))
    assert budget_check(slot(), product(price=None)) == "no price listed"


def test_size_precheck_exact_reason_for_real_listing():
    """The cabinet that triggered this work: 34.6'' wide in a 0.50 m slot."""
    reasons, known = size_check(slot(), product("Cas Black Storage Cabinet, 34.6'' Wide"), 1.15)
    assert known and reasons == ["too wide: 0.88 m vs 0.50 m max"]


def test_size_precheck_is_orientation_free_and_tolerant():
    s = slot(max_width_m=0.85, max_depth_m=0.45)
    rotated = product("Cabinet", desc='Dimensions: 15.7"W x 31.5"D x 35.4"H')  # W/D swapped in listing
    assert size_check(s, rotated, 1.15)[0] == []
    assert size_check(slot(max_width_m=0.5), product("Cabinet 21'' Wide"), 1.15)[0] == []  # 0.53 m ≤ +15 %
    tall = product("Bookcase", desc="Overall: 20 in W x 12 in D x 72 in H")
    assert size_check(slot(), tall, 1.15)[0] == ["too tall: 1.83 m vs 1.20 m max"]


def test_digest_is_small_and_slot_specific(living_room_scene):
    s = slot(surface_id="table_1_top", position_id="slot_2_front", category="tray", max_width_m=0.4)
    text = slot_digest(living_room_scene, s, product("Walnut Tray"), style="warm minimalist",
                       intent_text="black accents", color_direction=["black"])
    assert len(text) < 1500
    assert "Front of coffee table" in text and "plant" in text  # neighbour on the same table
    assert "sofa" not in text  # objects elsewhere are left out
    assert "rp_1" not in text  # the sofa (another surface) is not described


# --------------------------------------------------------------- API path


def test_precheck_rejects_without_calling_api(api, living_room_scene):
    d = run(evaluate_candidate(living_room_scene, slot(), product("Cas Black Storage Cabinet, 34.6'' Wide"),
                               style="minimal", intent_text="x"))
    assert not d.accepted and d.source == "precheck" and api["calls"] == []
    assert d.reasons == ["too wide: 0.88 m vs 0.50 m max"]
    over = run(evaluate_candidate(living_room_scene, slot(), product(price=999), style="m", intent_text="x"))
    assert over.source == "precheck" and not over.within_budget and "over slot cap" in over.reason


def test_api_sends_product_and_room_images_and_explains(api, living_room_scene):
    api["scores"]["Walnut"] = {"fits_surface": (0.8, 0.8), "style_match": (0.2, 0.8)}
    d = run(evaluate_candidate(living_room_scene, slot(), product("Walnut Cabinet"), style="black minimal",
                               intent_text="x", color_direction=["black"], room_image=(b"jpg", "image/jpeg")))
    parts = api["calls"][0]
    assert [p["type"] for p in parts].count("image") == 2
    assert d.used_images == ["product", "room"]
    assert not d.accepted and d.source == "decisions_api"
    assert d.reasons == ["style mismatch (0.20): wanted black"]
    assert d.budget_score == 1.0 and d.rank_score is None


def test_image_failure_retries_text_only(api, living_room_scene):
    api["fail"] = 1
    d = run(evaluate_candidate(living_room_scene, slot(), product(), style="m", intent_text="x",
                               room_image=(b"jpg", "image/jpeg")))
    assert len(api["calls"]) == 2
    assert not any(p["type"] == "image" for p in api["calls"][1])
    assert d.source == "decisions_api" and d.used_images == [] and d.accepted


def test_heuristic_fallback_is_honest(api, living_room_scene):
    api["fail"] = 2
    walnut = run(evaluate_candidate(living_room_scene, slot(), product("Walnut Wood Cabinet"),
                                    style="m", intent_text="x", color_direction=["black"]))
    assert walnut.source == "heuristic" and not walnut.accepted
    assert any("listing says walnut" in r for r in walnut.reasons)
    api["fail"] = 2
    black = run(evaluate_candidate(living_room_scene, slot(), product("Matte Black Cabinet"),
                                   style="m", intent_text="x", color_direction=["black"]))
    assert black.source == "heuristic" and black.accepted and black.confidence == 0.4


def test_pick_best_ranks_instead_of_first(api, living_room_scene):
    api["scores"]["Okay"] = {"fits_surface": (0.6, 0.6), "style_match": (0.6, 0.6)}
    api["scores"]["Great"] = {"fits_surface": (0.95, 0.9), "style_match": (0.9, 0.9)}
    evals = run(decisions.evaluate_candidates(
        living_room_scene, slot(), [product("Okay Cabinet"), product("Great Cabinet")],
        query="q", style="m", intent_text="x"))
    assert pick_best(evals).product.title == "Great Cabinet"
    assert pick_best([CandidateEvaluation(product=e.product, decision=e.decision.model_copy(update={"accepted": False}),
                                          query="q") for e in evals]) is None


# --------------------------------------------------------------- queries


def test_size_words_and_loosened_query():
    assert with_size_word("matte black storage cabinet", "sideboard", 0.5) == "narrow matte black storage cabinet"
    assert with_size_word("slim black cabinet", "sideboard", 0.5) == "slim black cabinet"
    assert with_size_word("black floor lamp", "lamp", 0.3) == "black floor lamp"
    assert loosen_query("modern matte black solid wood storage cabinet", "storage_cabinet", "black", size="narrow") \
        == "black narrow storage cabinet"


def test_carbon_slots_get_size_words(living_room_scene):
    out = CarbonOutput.model_validate({
        "summary": "s", "style": "m", "color_direction": ["black"], "object_decisions": [],
        "slots": [{"position_id": "slot_3_right", "category": "storage_cabinet",
                   "search_query": "matte black storage cabinet", "priority": 1, "max_price_usd": 200,
                   "max_width_m": 0.6, "reason": "r"}],
    })
    plan = assemble_plan(out, living_room_scene, "", 600, model="t")
    assert plan.slots[0].search_query == "narrow matte black storage cabinet"


# --------------------------------------------------------------- slot filling


@pytest.fixture
def shop(monkeypatch):
    """Fake Shopify: products per query substring; records queries."""
    state = {"queries": [], "catalog": {}}

    async def fake(query, **kw):
        state["queries"].append(query)
        for key, items in state["catalog"].items():
            if key in query:
                return items
        return []

    monkeypatch.setattr(pipeline, "search_catalog", fake)
    return state


def test_cheap_retry_with_looser_query_fills_slot(api, shop, living_room_scene):
    shop["catalog"]["matte black storage cabinet"] = [product("Cas Black Storage Cabinet, 34.6'' Wide", price=250)]
    shop["catalog"]["black narrow storage cabinet"] = [product("Slim Black Storage Cabinet 18'' Wide", price=140)]
    s = slot(search_query="matte black storage cabinet", max_width_m=0.5)
    plan = assemble_plan(None, living_room_scene, "black", 600, model="t")
    out = run(pipeline.fill_slot(s, scene=living_room_scene, plan=plan, intent_text="x", room_image=None))
    assert shop["queries"] == ["matte black storage cabinet", "black narrow storage cabinet"]
    assert out.placed and out.placed.product.title.startswith("Slim Black")
    assert out.placed.queries_tried == shop["queries"]
    assert len(out.placed.evaluated) == 2 and out.placed.rejected_alternatives[0].title.startswith("Cas")


def test_unfilled_slot_keeps_readable_reasons(api, shop, living_room_scene):
    shop["catalog"]["cabinet"] = [product("Cas Black Storage Cabinet, 34.6'' Wide", price=250)]
    plan = assemble_plan(None, living_room_scene, "black", 600, model="t")
    out = run(pipeline.fill_slot(slot(), scene=living_room_scene, plan=plan, intent_text="x", room_image=None))
    u = out.unfilled()
    assert out.placed is None and len(u.queries_tried) == 2
    assert any("too wide: 0.88 m vs 0.50 m max" in r for r in u.reasons)


def test_total_budget_drops_lowest_priority(living_room_scene):
    from app.schemas import DecisionResult, PlacedProduct
    dec = DecisionResult(fits=True, style_match=True, within_budget=True, fits_score=1, style_score=1,
                         budget_score=1, confidence=1, accepted=True, reason="ok")
    slots = {"a": slot(id="a", priority=1), "b": slot(id="b", priority=3)}
    placed = [PlacedProduct(slot_id=k, category="c", product=product(price=300), decision=dec, proxy_model="x")
              for k in slots]
    warnings: list[str] = []
    kept, dropped = pipeline.enforce_total_budget(placed, slots, 500, warnings)
    assert [p.slot_id for p in kept] == ["a"] and dropped[0].slot.id == "b" and warnings


def test_redesign_replans_only_empty_slots(monkeypatch, api, shop, living_room_scene):
    """Lamp fills, cabinet fails twice → Carbon gets only the cabinet, the leftover budget and the lamp as fixed."""
    out = CarbonOutput.model_validate({
        "summary": "s", "style": "black minimal", "color_direction": ["black"], "object_decisions": [],
        "slots": [
            {"position_id": "slot_1_left", "category": "floor_lamp", "search_query": "black floor lamp",
             "priority": 1, "max_price_usd": 150, "reason": "r"},
            {"position_id": "slot_3_right", "category": "storage_cabinet", "search_query": "black storage cabinet",
             "priority": 1, "max_price_usd": 300, "max_width_m": 0.5, "reason": "r"},
        ],
    })
    plan = assemble_plan(out, living_room_scene, "black", 600, model="gemini-4-carbon")
    shop["catalog"]["floor lamp"] = [product("Black Floor Lamp 65 Inch", price=100)]
    shop["catalog"]["cabinet"] = [product("Cas Black Storage Cabinet, 34.6'' Wide", price=250)]
    shop["catalog"]["wall shelf"] = [product("Black Floating Wall Shelf 24'' Wide", price=60)]
    seen = {}

    async def fake_scene(*a, **k):
        return living_room_scene

    async def fake_plan(*a, **k):
        return plan

    async def fake_replan(scene, failed_plan, *, failures, intent_text, budget_usd, fixed_items, max_slots):
        seen.update(slots=[s.category for s in failed_plan.slots], budget=budget_usd, fixed=fixed_items,
                    failures=failures, max_slots=max_slots)
        new = CarbonOutput.model_validate({
            "summary": "s", "style": "black minimal", "color_direction": ["black"], "object_decisions": [],
            "slots": [{"position_id": "slot_3_right", "category": "wall_shelf", "search_query": "black wall shelf",
                       "priority": 1, "max_price_usd": 120, "max_width_m": 0.7, "reason": "r"}],
        })
        return assemble_plan(new, scene, intent_text, budget_usd, model="gemini-4-carbon", max_slots=max_slots)

    async def no_meshy(*a, **k):
        return None

    monkeypatch.setattr(pipeline, "analyze_scene", fake_scene)
    monkeypatch.setattr(pipeline, "create_design_plan", fake_plan)
    monkeypatch.setattr(pipeline, "replan_after_failures", fake_replan)
    monkeypatch.setattr(pipeline, "_kickoff_meshy", no_meshy)

    res = run(pipeline.run_redesign(image_bytes=b"not-an-image", intent_text="black", budget_usd=600))
    assert seen["slots"] == ["storage_cabinet"] and seen["max_slots"] == 1
    assert seen["budget"] == pytest.approx(500) and "Black Floor Lamp" in seen["fixed"][0]
    assert any("too wide" in f for f in seen["failures"])
    assert sorted(p.category for p in res.placements) == ["floor_lamp", "wall_shelf"]
    assert res.unfilled_slots == [] and res.total_usd == 160
    names = [s.name for s in res.steps]
    assert len(names) == len(set(names)), names  # iOS ForEach keys steps by name
    assert "replan_1" in names and "decisions_replan_1" in names
    assert any(w.startswith("replanned storage_cabinet: ") for w in res.warnings)


def test_floor_slots_do_not_inherit_room_size_as_limits(living_room_scene):
    """Regression: floor depth (3.8 m room) became max_depth, so a 0.88 m cabinet 'rotated' into a 0.5 m gap."""
    out = CarbonOutput.model_validate({
        "summary": "s", "style": "m", "color_direction": [], "object_decisions": [],
        "slots": [{"position_id": "slot_3_right", "category": "storage_cabinet", "search_query": "cabinet",
                   "priority": 1, "max_price_usd": 200, "max_width_m": 0.5, "reason": "r"},
                  {"position_id": "slot_2_front", "category": "tray", "search_query": "tray",
                   "priority": 2, "max_price_usd": 30, "reason": "r"}],
    })
    cab, tray = assemble_plan(out, living_room_scene, "", 600, model="t").slots
    assert (cab.max_width_m, cab.max_depth_m) == (0.5, None)
    assert (tray.max_width_m, tray.max_depth_m) == (1.05, 0.55)  # table top still bounds the tray
    reasons, _ = size_check(cab, product("Cas Black Storage Cabinet, 34.6'' Wide"), 1.15)
    assert reasons == ["too wide: 0.88 m vs 0.50 m max"]
