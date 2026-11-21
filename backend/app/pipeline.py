"""End-to-end redesign orchestration."""

from __future__ import annotations

import logging
import time

from app.decisions import evaluate_candidate
from app.gemini_plan import create_design_plan, replan_after_failures, resolve_proxy_model
from app.gemini_scene import analyze_scene
from app.meshy import ensure_model as ensure_meshy_model
from app.schemas import (
    PipelineStep,
    PlacedProduct,
    RedesignResponse,
    RoomPlanPayload,
    SurfaceCalibration,
)
from app.shopify_mcp import search_catalog

logger = logging.getLogger(__name__)

# Used only for axes the listing never states.
_BOX_FALLBACK = {
    "lamp": (0.28, 0.28, 1.55),
    "sideboard": (1.10, 0.40, 0.70),
    "tray": (0.22, 0.16, 0.04),
    "organizer": (0.20, 0.15, 0.12),
    "plant": (0.22, 0.22, 0.45),
    "shelf": (0.80, 0.25, 0.08),
}


def _resolve_box(product, proxy: str) -> tuple[float, float, float, str]:
    """Width, depth, height in meters. Listing measurements win; typical size fills gaps."""
    fallback = _BOX_FALLBACK.get(proxy, (0.30, 0.30, 0.30))
    product_vals = (product.width_m, product.depth_m, product.height_m)
    resolved = []
    sources = []
    for product_val, fallback_val in zip(product_vals, fallback):
        if product_val and product_val > 0.02:
            resolved.append(float(product_val))
            sources.append("product")
        else:
            resolved.append(float(fallback_val))
            sources.append("estimate")
    if all(s == "product" for s in sources):
        origin = "product"
    elif "product" in sources:
        origin = "mixed"
    else:
        origin = "estimated"
    return resolved[0], resolved[1], resolved[2], origin


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
    return 0.0


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
    max_retries: int = 0,
) -> RedesignResponse:
    steps: list[PipelineStep] = []
    warnings: list[str] = []
    t0 = time.perf_counter()

    if not image_bytes:
        raise ValueError("An image capture is required (Element 119 — media-central).")

    t = time.perf_counter()
    scene = await analyze_scene(
        image_bytes,
        mime_type=mime_type,
        room_plan=room_plan,
        calibration=calibration,
        intent_hint=intent_text,
    )
    logger.info("step scene_parse %.1fs", time.perf_counter() - t)
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

    if scene.surfaces and scene.surfaces[0].dimensions_source.value in {
        "approximate",
        "inferred",
    }:
        warnings.append(
            "Surface dimensions are approximate. Re-run with RoomPlan LiDAR for real scale."
        )

    t = time.perf_counter()
    plan = await create_design_plan(
        scene, intent_text=intent_text, budget_usd=budget_usd
    )
    logger.info("step design_plan %.1fs (%d slots)", time.perf_counter() - t, len(plan.slots))
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
            t_slot = time.perf_counter()
            try:
                candidates = await search_catalog(
                    query,
                    max_price_usd=slot.max_price_usd or remaining,
                    limit=3,
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
            # Evaluate at most 2 candidates per slot for latency.
            for candidate in candidates[:2]:
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

            logger.info(
                "slot %s shopify+decisions %.1fs candidates=%d accepted=%s",
                slot.id,
                time.perf_counter() - t_slot,
                len(candidates),
                bool(chosen),
            )

            if not chosen or not chosen_decision:
                continue

            price = chosen.price_usd or 0.0
            remaining -= price
            # Re-resolve using the *chosen product title* so a ceramic tray
            # doesn't render as the same tall box as a sideboard cabinet.
            proxy = resolve_proxy_model(
                category=slot.category,
                proxy_model=slot.proxy_model,
                search_query=slot.search_query,
                product_title=chosen.title,
            )
            box_w, box_d, box_h, box_origin = _resolve_box(chosen, proxy)
            chosen = chosen.model_copy(
                update={"width_m": box_w, "depth_m": box_d, "height_m": box_h}
            )
            logger.info(
                "placement %s → %r proxy=%s has_3d=%s model=%s box=%.2fx%.2fx%.2fm (%s) price=%s",
                slot.id,
                chosen.title,
                proxy,
                bool(chosen.model_url),
                chosen.model_format or "-",
                box_w,
                box_d,
                box_h,
                box_origin,
                price,
            )
            filled.append(
                PlacedProduct(
                    slot_id=slot.id,
                    category=slot.category,
                    product=chosen,
                    decision=chosen_decision,
                    proxy_model=proxy,
                    position=_placement_position(slot.region, height),
                    scale=1.0,
                    rejected_alternatives=rejected[:3],
                )
            )
            if not chosen.model_url:
                photos = [chosen.image_url] if chosen.image_url else []
                photos.extend(chosen.image_urls or [])
                try:
                    job = await ensure_meshy_model(chosen.id, photos)
                    logger.info("meshy kickoff %s status=%s", chosen.id[:24], job.status)
                except Exception:
                    logger.exception("meshy kickoff failed for %s", chosen.id)
        return filled

    t = time.perf_counter()
    placements = await fill_slots(plan)
    logger.info("step shopify+decisions %.1fs", time.perf_counter() - t)
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

    if len(placements) == 0 and max_retries > 0:
        warnings.append("No accepted products — asking Carbon to re-plan.")
        t = time.perf_counter()
        plan = await replan_after_failures(
            scene,
            plan,
            failures=failures[:12],
            intent_text=intent_text,
            budget_usd=budget_usd,
        )
        placements = await fill_slots(plan)
        logger.info("step replan %.1fs", time.perf_counter() - t)
        steps.append(
            PipelineStep(
                name="replan",
                status="ok",
                detail=f"Re-planned; now {len(placements)} placements",
                model=plan.model,
            )
        )

    total = sum(p.product.price_usd or 0.0 for p in placements)
    logger.info("redesign total %.1fs", time.perf_counter() - t0)
    return RedesignResponse(
        scene=scene,
        plan=plan,
        placements=placements,
        total_usd=round(total, 2),
        remaining_budget_usd=round(budget_usd - total, 2),
        steps=steps,
        warnings=warnings,
    )
