"""End-to-end redesign orchestration."""

from __future__ import annotations

import logging

from app.decisions import evaluate_candidate
from app.gemini_plan import create_design_plan, replan_after_failures
from app.gemini_scene import analyze_scene
from app.schemas import (
    PipelineStep,
    PlacedProduct,
    RedesignResponse,
    RoomPlanPayload,
    SurfaceCalibration,
)
from app.shopify_mcp import search_catalog

logger = logging.getLogger(__name__)

REGION_OFFSETS = {
    "left": {"x": -0.35, "y": 0.0, "z": 0.0},
    "right": {"x": 0.35, "y": 0.0, "z": 0.0},
    "center": {"x": 0.0, "y": 0.0, "z": 0.0},
    "back": {"x": 0.0, "y": 0.0, "z": -0.15},
    "front": {"x": 0.0, "y": 0.0, "z": 0.15},
}


def _surface_height(scene) -> float:
    if scene.surfaces and scene.surfaces[0].height_m:
        return float(scene.surfaces[0].height_m)
    return 0.74


def _placement_position(region: str, height_m: float) -> dict[str, float]:
    base = REGION_OFFSETS.get(region, REGION_OFFSETS["center"]).copy()
    base["y"] = height_m
    return base


async def run_redesign(
    *,
    image_bytes: bytes,
    mime_type: str = "image/jpeg",
    intent_text: str,
    budget_usd: float,
    room_plan: RoomPlanPayload | None = None,
    calibration: SurfaceCalibration | None = None,
    max_retries: int = 1,
) -> RedesignResponse:
    steps: list[PipelineStep] = []
    warnings: list[str] = []

    if not image_bytes:
        raise ValueError("An image capture is required (Element 119 — media-central).")

    scene = await analyze_scene(
        image_bytes,
        mime_type=mime_type,
        room_plan=room_plan,
        calibration=calibration,
        intent_hint=intent_text,
    )
    steps.append(
        PipelineStep(
            name="scene_parse",
            status="ok" if scene.model != "fallback" else "warn",
            detail=f"{scene.scene_type}; {len(scene.objects)} objects; "
            f"{len(scene.available_positions)} slots",
            model=scene.model,
        )
    )
    if scene.model == "fallback":
        warnings.append("Scene parser fell back — check GEMINI_API_KEY / image quality.")

    # Prefer RoomPlan dimensions; warn if approximate.
    if scene.surfaces and scene.surfaces[0].dimensions_source.value in {
        "approximate",
        "inferred",
    }:
        warnings.append(
            "Surface dimensions are approximate. Re-run with RoomPlan LiDAR for real scale."
        )

    plan = await create_design_plan(
        scene, intent_text=intent_text, budget_usd=budget_usd
    )
    steps.append(
        PipelineStep(
            name="design_plan",
            status="ok" if plan.model != "fallback" else "warn",
            detail=f"{len(plan.slots)} slots — {plan.summary}",
            model=plan.model,
        )
    )

    placements: list[PlacedProduct] = []
    remaining = budget_usd
    failures: list[str] = []
    height = _surface_height(scene)

    async def fill_slots(current_plan) -> list[PlacedProduct]:
        nonlocal remaining, failures
        filled: list[PlacedProduct] = []
        remaining = budget_usd
        failures = []
        for slot in current_plan.slots:
            query = slot.search_query
            if slot.preferred_color and slot.preferred_color.lower() not in query.lower():
                query = f"{slot.preferred_color} {query}"
            try:
                candidates = await search_catalog(
                    query,
                    max_price_usd=slot.max_price_usd or remaining,
                    limit=5,
                )
            except Exception as exc:
                logger.exception("Shopify search failed for %s", slot.id)
                warnings.append(f"Shopify search failed for {slot.category}: {exc}")
                failures.append(f"{slot.id}: catalog error {exc}")
                continue

            candidates = [c for c in candidates if c.available is not False]
            if not candidates:
                failures.append(f"{slot.id}: no in-stock catalog results for '{query}'")
                continue

            rejected = []
            chosen = None
            chosen_decision = None
            for candidate in candidates:
                decision = await evaluate_candidate(
                    scene,
                    slot,
                    candidate,
                    remaining_budget=remaining,
                    style=current_plan.style,
                    intent_text=intent_text,
                )
                if decision.accepted:
                    chosen = candidate
                    chosen_decision = decision
                    break
                rejected.append(candidate)
                failures.append(
                    f"{slot.id}: rejected '{candidate.title}' ({decision.reason})"
                )

            if not chosen or not chosen_decision:
                continue

            price = chosen.price_usd or 0.0
            remaining -= price
            filled.append(
                PlacedProduct(
                    slot_id=slot.id,
                    category=slot.category,
                    product=chosen,
                    decision=chosen_decision,
                    proxy_model=slot.proxy_model or slot.category,
                    position=_placement_position(slot.region, height),
                    scale=1.0,
                    rejected_alternatives=rejected[:3],
                )
            )
        return filled

    placements = await fill_slots(plan)
    steps.append(
        PipelineStep(
            name="shopify_search",
            status="ok" if placements else "warn",
            detail=f"{len(placements)} products selected",
            model="shopify_catalog_mcp",
        )
    )
    steps.append(
        PipelineStep(
            name="decisions",
            status="ok" if placements else "warn",
            detail=f"{len(failures)} rejections logged",
            model="gemini-decision-1",
        )
    )

    if len(placements) < max(1, len(plan.slots) // 2) and max_retries > 0:
        warnings.append("Too few accepted products — asking Carbon to re-plan.")
        plan = await replan_after_failures(
            scene,
            plan,
            failures=failures[:12],
            intent_text=intent_text,
            budget_usd=budget_usd,
        )
        placements = await fill_slots(plan)
        steps.append(
            PipelineStep(
                name="replan",
                status="ok",
                detail=f"Re-planned; now {len(placements)} placements",
                model=plan.model,
            )
        )

    total = sum(p.product.price_usd or 0.0 for p in placements)
    return RedesignResponse(
        scene=scene,
        plan=plan,
        placements=placements,
        total_usd=round(total, 2),
        remaining_budget_usd=round(budget_usd - total, 2),
        steps=steps,
        warnings=warnings,
    )
