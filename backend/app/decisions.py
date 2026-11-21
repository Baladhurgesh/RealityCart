"""gemini-decision-1 gates: does this real Shopify product belong in this slot?

Order of checks for each candidate
----------------------------------
1. Budget   — pure code (price ≤ slot cap). No model call.
2. Size     — pure code when the listing states sizes: clearly too big → reject
              with an exact reason, no model call ("precheck").
3. Decisions API — two calibrated yes/no questions answered from the product
              photo, the room photo and a short slot digest:
                fits_surface  — physical fit in this spot
                style_match   — color/finish/style vs request and room
4. Heuristic — only if the API fails twice; labelled source="heuristic".

Candidates for a slot are evaluated concurrently; the best accepted one wins
(rank = mean of fit and style probability).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from typing import Any

from app.config import Settings, get_settings
from app.gemini_client import get_decision_client
from app.plan_rules import COLOR_WORDS, colors_in
from app.schemas import (
    CandidateEvaluation,
    DecisionResult,
    PlacementSlot,
    ProductOffer,
    SceneAnalysis,
)

logger = logging.getLogger(__name__)

RoomImage = tuple[bytes, str]  # (jpeg bytes, mime)


# --------------------------------------------------------------------------
# 1 + 2: deterministic checks
# --------------------------------------------------------------------------


def budget_check(slot: PlacementSlot, product: ProductOffer, remaining_budget: float | None = None) -> str | None:
    """Reason string if the product is over budget, else None."""
    price = product.price_usd
    if price is None:
        return "no price listed"
    if slot.max_price_usd is not None and price > slot.max_price_usd + 0.01:
        return f"over slot cap: ${price:.2f} > ${slot.max_price_usd:.2f}"
    if remaining_budget is not None and price > remaining_budget + 0.01:
        return f"over remaining budget: ${price:.2f} > ${remaining_budget:.2f}"
    return None


def size_check(slot: PlacementSlot, product: ProductOffer, tolerance: float) -> tuple[list[str], bool]:
    """(too-big reasons, all_known). Width/depth are compared orientation-free."""
    reasons: list[str] = []
    w, d, h = product.width_m, product.depth_m, product.height_m
    mw, md = slot.max_width_m, slot.max_depth_m
    if mw and md and w and d:
        # Both sides known on both: allow rotation (largest side vs largest limit, …).
        for got, allowed, word in zip(sorted((w, d), reverse=True), sorted((mw, md), reverse=True), ("wide", "deep")):
            if got > allowed * tolerance:
                reasons.append(f"too {word}: {got:.2f} m vs {allowed:.2f} m max")
    else:
        # Furniture has a front: no rotation unless both limits are known.
        if w and mw and w > mw * tolerance:
            reasons.append(f"too wide: {w:.2f} m vs {mw:.2f} m max")
        if d and md and d > md * tolerance:
            reasons.append(f"too deep: {d:.2f} m vs {md:.2f} m max")
    footprint = [v for v in (w, d) if v]
    if h and slot.max_height_m and h > slot.max_height_m * tolerance:
        reasons.append(f"too tall: {h:.2f} m vs {slot.max_height_m:.2f} m max")
    known = bool(footprint or h)
    return reasons, known


# --------------------------------------------------------------------------
# 3: Decisions API
# --------------------------------------------------------------------------


def _fmt(v: float | None) -> str:
    return "?" if v is None else f"{v:.2f}"


def slot_digest(
    scene: SceneAnalysis,
    slot: PlacementSlot,
    product: ProductOffer,
    *,
    style: str,
    intent_text: str,
    color_direction: list[str] | None = None,
) -> str:
    """~800 chars of what matters for this one decision (not the whole scene)."""
    surface = next((s for s in scene.surfaces if s.id == slot.surface_id), None)
    position = next((p for p in scene.available_positions if p.id == slot.position_id), None)
    neighbours = [
        f"{o.type}{' (' + o.color + ')' if o.color else ''}"
        for o in scene.objects
        if o.surface_id == slot.surface_id and o.keep
    ][:6]
    lines = [
        f"REQUEST: {intent_text.strip()[:200]}",
        f"TARGET STYLE: {style}; colors: {', '.join(color_direction or []) or slot.preferred_color or 'any'}",
        f"ROOM: {scene.scene_type}; current palette: {', '.join(scene.palette) or '?'}",
        f"SLOT: {slot.category} on {slot.surface_id} ({slot.region})"
        + (f" — {position.description}" if position and position.description else ""),
        f"SLOT LIMITS: max {_fmt(slot.max_width_m)} W × {_fmt(slot.max_depth_m)} D × {_fmt(slot.max_height_m)} H m"
        + (f"; clearance {_fmt(position.estimated_clearance_m)} m" if position and position.estimated_clearance_m else ""),
    ]
    if surface:
        lines.append(
            f"SURFACE: {surface.label or surface.id} {_fmt(surface.width_m)}×{_fmt(surface.depth_m)} m, "
            f"height {_fmt(surface.height_m)} m [{surface.dimensions_source.value}]"
        )
    if neighbours:
        lines.append(f"ALREADY ON THIS SURFACE (kept): {', '.join(neighbours)}")
    dims = (
        f"{_fmt(product.width_m)} W × {_fmt(product.depth_m)} D × {_fmt(product.height_m)} H m"
        if any((product.width_m, product.depth_m, product.height_m))
        else "not stated"
    )
    lines += [
        f"PRODUCT: {product.title}",
        f"PRODUCT SIZE (from listing): {dims}",
        f"PRODUCT DETAILS: {(product.description or '')[:350]}",
    ]
    return "\n".join(lines)


QUESTIONS: dict[str, dict[str, Any]] = {
    "fits_surface": {
        "type": "noul",
        "instructions": (
            "Would this product physically fit the SLOT within SLOT LIMITS and clearance, on that "
            "surface, without blocking the kept items? Use PRODUCT SIZE when stated; otherwise judge "
            "the typical size of this kind of product from its photo and title. A product type that "
            "is normally far larger than the limits (e.g. a full cabinet for a 0.5 m gap) does not fit."
        ),
        "criteria": {
            "true_description": "Size and form factor suit the slot.",
            "false_description": "Too large, wrong form factor, or would block kept items.",
        },
    },
    "style_match": {
        "type": "noul",
        "instructions": (
            "Look at the PRODUCT PHOTO. Do its actual color, finish and style match TARGET STYLE and "
            "colors, and would it look right in the ROOM PHOTO? Judge what the photo shows, not just "
            "the title."
        ),
        "criteria": {
            "true_description": "Color/finish/style clearly fit the request and the room.",
            "false_description": "Color or style visibly conflicts with the request or the room.",
        },
    },
}


def build_input(
    digest: str,
    product: ProductOffer,
    room_image: RoomImage | None,
    settings: Settings,
    *,
    with_images: bool = True,
) -> tuple[list[dict[str, Any]], list[str]]:
    parts: list[dict[str, Any]] = [{"type": "text", "text": digest}]
    used: list[str] = []
    if with_images and settings.decision_use_product_image and product.image_url:
        parts += [
            {"type": "text", "text": "PRODUCT PHOTO:"},
            {"type": "image", "uri": product.image_url, "mime_type": "image/jpeg"},
        ]
        used.append("product")
    if with_images and settings.decision_use_room_image and room_image:
        data, mime = room_image
        parts += [
            {"type": "text", "text": "ROOM PHOTO (where it will go):"},
            {"type": "image", "data": base64.b64encode(data).decode(), "mime_type": mime},
        ]
        used.append("room")
    return parts, used


async def _call_api(parts: list[dict[str, Any]], settings: Settings) -> dict[str, tuple[float, float]]:
    client = get_decision_client()
    result = await asyncio.wait_for(
        client.aio.decisions.create(model=settings.decision_model, input=parts, questions=QUESTIONS),
        timeout=settings.decision_timeout_s,
    )
    if result.prompt_feedback and result.prompt_feedback.block_reason:
        raise RuntimeError(f"Decisions blocked input: {result.prompt_feedback.block_reason}")
    answers = result.answers or {}
    return {
        k: (float(getattr(answers[k], "noul", 0.5)), float(getattr(answers[k], "confidence", 0.5)))
        for k in QUESTIONS
        if k in answers
    }


# --------------------------------------------------------------------------
# 4: heuristic fallback
# --------------------------------------------------------------------------

_CONFLICT_GROUPS = [
    {"black", "matte black", "charcoal"},
    {"white", "cream", "beige"},
    {"walnut", "oak", "wood", "natural", "brown"},
    {"brass", "gold"},
    {"silver", "chrome"},
]


def heuristic_style(slot: PlacementSlot, product: ProductOffer, wanted: list[str]) -> tuple[float, str | None]:
    """Score from color words in the listing vs what the plan asked for."""
    text = " ".join(
        [product.title, product.description or ""] + [v.title for v in product.variants]
    )
    found = set(colors_in(text))
    want = set(wanted) | ({slot.preferred_color.lower()} if slot.preferred_color else set())
    if not want:
        return 0.6, None
    if found & want or any(w in text.lower() for w in want):
        return 0.8, None
    # A color from a *different* family is a real conflict; no color words is just unknown.
    want_groups = [g for g in _CONFLICT_GROUPS if g & want]
    conflict = [c for c in found if c in COLOR_WORDS and not any(c in g for g in want_groups)]
    if conflict:
        return 0.3, f"style: listing says {', '.join(sorted(conflict))}, plan wants {', '.join(sorted(want))}"
    return 0.55, None


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def _result(
    *, fits: float, style: float, budget_ok: bool, confidence: float, source: str,
    reasons: list[str], settings: Settings, used_images: list[str] | None = None,
) -> DecisionResult:
    fits_ok = fits >= settings.decision_fit_threshold
    style_ok = style >= settings.decision_style_threshold
    accepted = fits_ok and style_ok and budget_ok
    return DecisionResult(
        fits=fits_ok,
        style_match=style_ok,
        within_budget=budget_ok,
        fits_score=round(fits, 3),
        style_score=round(style, 3),
        budget_score=1.0 if budget_ok else 0.0,
        confidence=round(confidence, 3),
        accepted=accepted,
        reason="; ".join(reasons) or ("accepted" if accepted else "rejected"),
        source=source,  # type: ignore[arg-type]
        reasons=reasons,
        rank_score=round((fits + style) / 2, 3) if accepted else None,
        used_images=used_images or [],
    )


async def evaluate_candidate(
    scene: SceneAnalysis,
    slot: PlacementSlot,
    product: ProductOffer,
    *,
    remaining_budget: float | None = None,
    style: str,
    intent_text: str,
    color_direction: list[str] | None = None,
    room_image: RoomImage | None = None,
) -> DecisionResult:
    settings = get_settings()

    budget_reason = budget_check(slot, product, remaining_budget)
    too_big, size_known = size_check(slot, product, settings.decision_size_tolerance)
    if budget_reason or too_big:
        return _result(
            fits=0.05 if too_big else 0.5, style=0.5, budget_ok=budget_reason is None,
            confidence=0.95, source="precheck",
            reasons=too_big + ([budget_reason] if budget_reason else []), settings=settings,
        )

    digest = slot_digest(scene, slot, product, style=style, intent_text=intent_text, color_direction=color_direction)
    last_error: Exception | None = None
    # Attempt 1 with images; attempt 2 text-only (a bad image URL must not kill the gate).
    for with_images in (True, False):
        parts, used = build_input(digest, product, room_image, settings, with_images=with_images)
        try:
            scores = await _call_api(parts, settings)
            fit, fit_conf = scores.get("fits_surface", (0.5, 0.5))
            sty, sty_conf = scores.get("style_match", (0.5, 0.5))
            reasons = []
            if fit < settings.decision_fit_threshold:
                reasons.append(
                    f"fit check failed ({fit:.2f})"
                    + ("" if size_known else " — size not stated in listing")
                )
            if sty < settings.decision_style_threshold:
                wanted = ", ".join(color_direction or []) or slot.preferred_color or style
                reasons.append(f"style mismatch ({sty:.2f}): wanted {wanted}")
            return _result(
                fits=fit, style=sty, budget_ok=True, confidence=(fit_conf + sty_conf) / 2,
                source="decisions_api", reasons=reasons, settings=settings, used_images=used,
            )
        except Exception as exc:  # noqa: BLE001 — timeout / network / blocked
            last_error = exc
            logger.warning("Decisions %s failed for %r: %s",
                           "with images" if with_images else "text-only", product.title[:40], exc)

    # Heuristic fallback — honest about uncertainty.
    style_score, style_reason = heuristic_style(slot, product, color_direction or [])
    fit_score = 0.6 if not size_known else 0.8  # precheck already rejected the too-big ones
    reasons = [r for r in (style_reason,) if r]
    reasons.append(f"Decisions API unavailable ({type(last_error).__name__}); heuristic used")
    return _result(
        fits=fit_score, style=style_score, budget_ok=True, confidence=0.4,
        source="heuristic", reasons=reasons, settings=settings,
    )


async def evaluate_candidates(
    scene: SceneAnalysis,
    slot: PlacementSlot,
    products: list[ProductOffer],
    *,
    query: str,
    style: str,
    intent_text: str,
    color_direction: list[str] | None = None,
    room_image: RoomImage | None = None,
) -> list[CandidateEvaluation]:
    """Evaluate all candidates concurrently, in the order given."""
    decisions = await asyncio.gather(*[
        evaluate_candidate(
            scene, slot, p, style=style, intent_text=intent_text,
            color_direction=color_direction, room_image=room_image,
        )
        for p in products
    ])
    return [CandidateEvaluation(product=p, decision=d, query=query) for p, d in zip(products, decisions)]


def pick_best(evaluations: list[CandidateEvaluation]) -> CandidateEvaluation | None:
    accepted = [e for e in evaluations if e.decision.accepted]
    return max(accepted, key=lambda e: e.decision.rank_score or 0.0) if accepted else None


def summarize_rejections(evaluations: list[CandidateEvaluation]) -> list[str]:
    """One readable line per rejected product (fed to Carbon re-plan and the client)."""
    out = []
    for e in evaluations:
        if not e.decision.accepted:
            title = re.sub(r"\s+", " ", e.product.title)[:60]
            price = f"${e.product.price_usd:.2f}" if e.product.price_usd is not None else "$?"
            out.append(f"'{title}' ({price}): {e.decision.reason}")
    return out
