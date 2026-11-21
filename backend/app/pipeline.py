"""End-to-end redesign orchestration."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from app.config import get_settings
from app.decisions import RoomImage, evaluate_candidates, pick_best, summarize_rejections
from app.gemini_plan import create_design_plan, replan_after_failures, resolve_proxy_model
from app.gemini_scene import analyze_scene, prepare_image
from app.meshy import ensure_model as ensure_meshy_model
from app.plan_rules import MIN_SLOT_CAP_USD, loosen_query, size_word
from app.schemas import (
    CandidateEvaluation,
    DesignPlan,
    PipelineStep,
    PlacedProduct,
    PlacementSlot,
    RedesignResponse,
    RoomPlanPayload,
    SceneAnalysis,
    SurfaceCalibration,
    UnfilledSlot,
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


def _surface_height(scene, surface_id: str | None = None) -> float:
    """Height of the slot's surface (e.g. coffee-table top), else the primary surface."""
    match = next((s for s in scene.surfaces if s.id == surface_id), None)
    surface = match or (scene.surfaces[0] if scene.surfaces else None)
    if surface and surface.height_m:
        return float(surface.height_m)
    return 0.0


def _placement_position(region: str, height_m: float) -> dict[str, float]:
    base = REGION_OFFSETS.get(region, REGION_OFFSETS["center"]).copy()
    base["y"] = height_m
    return base


@dataclass
class SlotOutcome:
    slot: PlacementSlot
    placed: PlacedProduct | None = None
    evaluated: list[CandidateEvaluation] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def unfilled(self) -> UnfilledSlot:
        return UnfilledSlot(slot=self.slot, reasons=self.reasons, evaluated=self.evaluated, queries_tried=self.queries)


def _primary_query(slot: PlacementSlot) -> str:
    q = slot.search_query
    if slot.preferred_color and slot.preferred_color.lower() not in q.lower():
        q = f"{slot.preferred_color} {q}"
    return q


async def _search_and_judge(
    query: str, slot: PlacementSlot, *, scene: SceneAnalysis, plan: DesignPlan, intent_text: str,
    room_image: RoomImage | None, seen: set[str], out: SlotOutcome,
) -> None:
    n = get_settings().decision_candidates_per_slot
    out.queries.append(query)
    try:
        found = await search_catalog(query, max_price_usd=slot.max_price_usd, limit=n + len(seen))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Shopify search failed for %s: %s", slot.id, exc)
        out.reasons.append(f"catalog error for '{query}': {exc}")
        return
    fresh = [c for c in found if c.available is not False and c.id not in seen][:n]
    if not fresh:
        out.reasons.append(f"no in-stock results for '{query}' under ${slot.max_price_usd or 0:.0f}")
        return
    seen.update(c.id for c in fresh)
    out.evaluated += await evaluate_candidates(
        scene, slot, fresh, query=query, style=plan.style, intent_text=intent_text,
        color_direction=plan.color_direction, room_image=room_image,
    )


async def fill_slot(
    slot: PlacementSlot, *, scene: SceneAnalysis, plan: DesignPlan, intent_text: str,
    room_image: RoomImage | None,
) -> SlotOutcome:
    """Search → judge all candidates in parallel → best accepted; one cheap looser retry."""
    t = time.perf_counter()
    out = SlotOutcome(slot=slot)
    seen: set[str] = set()
    kw = dict(scene=scene, plan=plan, intent_text=intent_text, room_image=room_image, seen=seen, out=out)
    q1 = _primary_query(slot)
    await _search_and_judge(q1, slot, **kw)
    best = pick_best(out.evaluated)

    if best is None:
        fit_failed = any(not e.decision.fits for e in out.evaluated)
        size = size_word(slot.proxy_model or "", slot.max_width_m) or ("small" if fit_failed else None)
        q2 = loosen_query(slot.search_query, slot.category, slot.preferred_color, size=size)
        if q2 and q2 != q1.lower():
            await _search_and_judge(q2, slot, **kw)
            best = pick_best(out.evaluated)

    logger.info(
        "slot %s %.1fs queries=%d candidates=%d accepted=%s",
        slot.id, time.perf_counter() - t, len(out.queries), len(out.evaluated), bool(best),
    )
    if best is None:
        out.reasons += summarize_rejections(out.evaluated)
        return out

    chosen = best.product
    # Re-resolve using the *chosen product title* so a ceramic tray
    # doesn't render as the same tall box as a sideboard cabinet.
    proxy = resolve_proxy_model(
        category=slot.category, proxy_model=slot.proxy_model,
        search_query=slot.search_query, product_title=chosen.title,
    )
    box_w, box_d, box_h, box_origin = _resolve_box(chosen, proxy)
    chosen = chosen.model_copy(update={"width_m": box_w, "depth_m": box_d, "height_m": box_h})
    logger.info(
        "placement %s → %r proxy=%s has_3d=%s box=%.2fx%.2fx%.2fm (%s) price=%s via %s",
        slot.id, chosen.title, proxy, bool(chosen.model_url), box_w, box_d, box_h,
        box_origin, chosen.price_usd, best.decision.source,
    )
    out.placed = PlacedProduct(
        slot_id=slot.id,
        category=slot.category,
        product=chosen,
        decision=best.decision,
        proxy_model=proxy,
        position=_placement_position(slot.region, _surface_height(scene, slot.surface_id)),
        scale=1.0,
        rejected_alternatives=[e.product for e in out.evaluated if not e.decision.accepted][:3],
        evaluated=out.evaluated,
        queries_tried=out.queries,
    )
    return out


async def _kickoff_meshy(placed: PlacedProduct) -> None:
    product = placed.product
    if product.model_url:
        return
    photos = ([product.image_url] if product.image_url else []) + list(product.image_urls or [])
    try:
        job = await ensure_meshy_model(product.id, photos)
        logger.info("meshy kickoff %s status=%s", product.id[:24], job.status)
    except Exception:
        logger.exception("meshy kickoff failed for %s", product.id)


def enforce_total_budget(
    placed: list[PlacedProduct], slots: dict[str, PlacementSlot], budget_usd: float, warnings: list[str],
) -> tuple[list[PlacedProduct], list[UnfilledSlot]]:
    """Drop lowest-priority picks until the cart fits the budget (caps make this rare)."""
    kept = sorted(placed, key=lambda p: (slots[p.slot_id].priority or 2))
    dropped: list[UnfilledSlot] = []
    while kept and sum(p.product.price_usd or 0 for p in kept) > budget_usd + 0.01:
        p = kept.pop()
        warnings.append(f"Dropped {p.category} '{p.product.title[:40]}' to stay within ${budget_usd:.0f}.")
        dropped.append(UnfilledSlot(slot=slots[p.slot_id], reasons=["dropped to stay within total budget"],
                                    evaluated=p.evaluated, queries_tried=p.queries_tried))
    return kept, dropped


async def run_redesign(
    *,
    image_bytes: bytes,
    mime_type: str = "image/jpeg",
    intent_text: str,
    budget_usd: float,
    room_plan: RoomPlanPayload | None = None,
    calibration: SurfaceCalibration | None = None,
    max_retries: int | None = None,
) -> RedesignResponse:
    settings = get_settings()
    replan_rounds = settings.redesign_replan_rounds if max_retries is None else max_retries
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
    # Hydrogen/geometry warnings (unknown surface refs, partial scans, …).
    warnings.extend(f"scene: {w}" for w in scene.warnings)

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
    warnings.extend(f"plan: {w}" for w in plan.warnings)
    steps.append(
        PipelineStep(
            name="design_plan",
            status="ok" if plan.model != "fallback" else "warn",
            detail=f"{len(plan.slots)} slots — {plan.summary}",
            model=plan.model,
        )
    )

    # Room photo for the Decisions style gate (small: it is sent once per candidate).
    room_image = prepare_image(image_bytes, mime_type, max_side=settings.decision_room_image_side)

    async def fill_all(current: DesignPlan) -> list[SlotOutcome]:
        return list(await asyncio.gather(*[
            fill_slot(s, scene=scene, plan=current, intent_text=intent_text, room_image=room_image)
            for s in current.slots
        ]))

    t = time.perf_counter()
    outcomes = await fill_all(plan)
    logger.info("step shopify+decisions %.1fs (parallel)", time.perf_counter() - t)
    all_slots = {s.id: s for s in plan.slots}
    placements = [o.placed for o in outcomes if o.placed]
    unfilled = [o.unfilled() for o in outcomes if not o.placed]
    _record_fill_steps(steps, outcomes, label="")

    # Targeted re-plan: Carbon only replaces the slots that stayed empty.
    for round_no in range(1, replan_rounds + 1):
        if not unfilled:
            break
        spent = sum(p.product.price_usd or 0 for p in placements)
        left = budget_usd - spent
        if left < MIN_SLOT_CAP_USD:
            warnings.append(f"Skipped re-plan: only ${left:.0f} left.")
            break
        t = time.perf_counter()
        failures = [
            f"{u.slot.id} [{u.slot.category}] queries {u.queries_tried}: " + "; ".join(u.reasons[:4])
            for u in unfilled
        ]
        fixed = [f"{p.category} '{p.product.title[:40]}' ${p.product.price_usd} at {p.slot_id}" for p in placements]
        failed_plan = plan.model_copy(update={"slots": [u.slot for u in unfilled]})
        new_plan = await replan_after_failures(
            scene, failed_plan, failures=failures, intent_text=intent_text,
            budget_usd=left, fixed_items=fixed, max_slots=len(unfilled),
        )
        if new_plan is failed_plan:  # Carbon failed; keep what we have
            warnings.extend(f"replan: {w}" for w in new_plan.warnings[-1:])
            break
        for u in unfilled:  # keep why the original slot failed, even once it is replaced
            warnings.append(f"replanned {u.slot.category}: {u.reasons[0] if u.reasons else 'no reason recorded'}")
        new_plan.slots = [s.model_copy(update={"id": f"re{round_no}_{s.id}"}) for s in new_plan.slots]
        warnings.extend(f"replan: {w}" for w in new_plan.warnings)
        all_slots.update({s.id: s for s in new_plan.slots})
        new_outcomes = await fill_all(new_plan)
        new_placed = [o.placed for o in new_outcomes if o.placed]
        placements += new_placed
        unfilled = [o.unfilled() for o in new_outcomes if not o.placed] + (unfilled if not new_plan.slots else [])
        plan = plan.model_copy(update={
            "slots": [s for s in plan.slots if s.id in {p.slot_id for p in placements}] + new_plan.slots,
            "warnings": plan.warnings + new_plan.warnings,
        })
        logger.info("step replan %d %.1fs", round_no, time.perf_counter() - t)
        steps.append(
            PipelineStep(
                name=f"replan_{round_no}",
                status="ok" if new_placed else "warn",
                detail=f"Carbon replaced {len(failed_plan.slots)} empty slot(s); "
                f"{len(new_placed)} filled — {', '.join(s.category for s in new_plan.slots)}",
                model=new_plan.model,
            )
        )
        _record_fill_steps(steps, new_outcomes, label=f" (replan {round_no})", suffix=f"_replan_{round_no}")

    placements, dropped = enforce_total_budget(placements, all_slots, budget_usd, warnings)
    unfilled += dropped
    for u in unfilled:
        warnings.append(f"unfilled {u.slot.category}: {u.reasons[0] if u.reasons else 'no reason recorded'}")

    await asyncio.gather(*[_kickoff_meshy(p) for p in placements])

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
        unfilled_slots=unfilled,
    )


def _record_fill_steps(steps: list[PipelineStep], outcomes: list[SlotOutcome], *, label: str, suffix: str = "") -> None:
    """Step names must stay unique: iOS keys its step list by name (PipelineStepDTO.id)."""
    filled = sum(1 for o in outcomes if o.placed)
    evals = [e for o in outcomes for e in o.evaluated]
    by_source: dict[str, int] = {}
    for e in evals:
        by_source[e.decision.source] = by_source.get(e.decision.source, 0) + 1
    retries = sum(1 for o in outcomes if len(o.queries) > 1)
    steps.append(
        PipelineStep(
            name=f"shopify_search{suffix}",
            status="ok" if filled == len(outcomes) else "warn",
            detail=f"{filled}/{len(outcomes)} slots filled{label}; "
            f"{sum(len(o.queries) for o in outcomes)} searches ({retries} looser retries)",
            model="shopify_catalog_mcp",
        )
    )
    steps.append(
        PipelineStep(
            name=f"decisions{suffix}",
            status="warn" if by_source.get("heuristic") else "ok",
            detail=f"{sum(e.decision.accepted for e in evals)}/{len(evals)} candidates accepted{label}; "
            + ", ".join(f"{k}={v}" for k, v in sorted(by_source.items())),
            model="gemini-decision-1",
        )
    )
