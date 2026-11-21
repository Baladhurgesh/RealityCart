"""gemini-decision-1 Decisions API gates for fit / style / budget."""

from __future__ import annotations

import json
import logging

from app.config import Settings, get_settings
from app.gemini_client import get_decision_client
from app.schemas import DecisionResult, PlacementSlot, ProductOffer, SceneAnalysis

logger = logging.getLogger(__name__)


def _heuristic_decision(
    scene: SceneAnalysis,
    slot: PlacementSlot,
    product: ProductOffer,
    *,
    remaining_budget: float,
    style: str,
    settings: Settings,
) -> DecisionResult:
    """Offline / API-failure fallback using simple rules."""
    price = product.price_usd or (slot.max_price_usd or 0)
    within_budget = price <= remaining_budget + 1e-6
    if slot.max_price_usd is not None:
        within_budget = within_budget and price <= slot.max_price_usd + 1e-6

    title = (product.title or "").lower()
    style_l = (style or "").lower()
    preferred = (slot.preferred_color or "").lower()
    style_match = True
    if preferred and preferred not in title and preferred not in (product.description or "").lower():
        # Soft fail if color preference present but not mentioned.
        style_match = "black" not in preferred
    if "minimal" in style_l and any(w in title for w in ("neon", "cartoon", "kids")):
        style_match = False

    fits = True
    if slot.max_width_m and product.dimensions_hint:
        # Without parsed dims, assume fit.
        fits = True

    budget_score = 0.9 if within_budget else 0.2
    style_score = 0.75 if style_match else 0.35
    fits_score = 0.7 if fits else 0.3
    accepted = (
        fits_score >= settings.decision_fit_threshold
        and style_score >= settings.decision_style_threshold
        and budget_score >= settings.decision_budget_threshold
    )
    reason_parts = []
    if not within_budget:
        reason_parts.append("over budget")
    if not style_match:
        reason_parts.append("style mismatch")
    if not fits:
        reason_parts.append("may not fit surface")
    return DecisionResult(
        fits=fits,
        style_match=style_match,
        within_budget=within_budget,
        fits_score=fits_score,
        style_score=style_score,
        budget_score=budget_score,
        confidence=0.4,
        accepted=accepted,
        reason="; ".join(reason_parts) or "heuristic accept",
    )


async def evaluate_candidate(
    scene: SceneAnalysis,
    slot: PlacementSlot,
    product: ProductOffer,
    *,
    remaining_budget: float,
    style: str,
    intent_text: str,
) -> DecisionResult:
    settings = get_settings()
    shared_input = {
        "scene": scene.model_dump(),
        "slot": slot.model_dump(),
        "product": {
            "title": product.title,
            "description": (product.description or "")[:800],
            "price_usd": product.price_usd,
            "vendor": product.vendor,
            "dimensions_hint": product.dimensions_hint,
        },
        "remaining_budget_usd": remaining_budget,
        "style": style,
        "intent": intent_text,
    }

    questions = {
        "fits_surface": {
            "type": "noul",
            "instructions": (
                "Would this product physically fit in the proposed placement slot "
                "without colliding with kept objects, given max dimensions and clearance?"
            ),
            "criteria": {
                "true_description": "Product size is compatible with the slot and surface.",
                "false_description": "Product is too large, wrong form factor, or blocks kept items.",
            },
        },
        "style_match": {
            "type": "noul",
            "instructions": (
                "Does this product match the requested style and color preferences?"
            ),
            "criteria": {
                "true_description": "Style and color align with the redesign intent.",
                "false_description": "Style or color clearly conflicts with the intent.",
            },
        },
        "within_budget": {
            "type": "noul",
            "instructions": (
                "Is the product price acceptable for this slot and remaining overall budget?"
            ),
            "criteria": {
                "true_description": "Price is within slot max and remaining budget.",
                "false_description": "Price exceeds slot or remaining budget.",
            },
        },
    }

    try:
        client = get_decision_client()
        result = client.decisions.create(
            model=settings.decision_model,
            input=json.dumps(shared_input),
            questions=questions,
        )
        answers = result.answers
        fits_score = float(getattr(answers["fits_surface"], "noul", 0.5))
        style_score = float(getattr(answers["style_match"], "noul", 0.5))
        budget_score = float(getattr(answers["within_budget"], "noul", 0.5))
        confidences = [
            float(getattr(answers[k], "confidence", 0.5))
            for k in ("fits_surface", "style_match", "within_budget")
        ]
        confidence = sum(confidences) / len(confidences)
        fits = fits_score >= settings.decision_fit_threshold
        style_match = style_score >= settings.decision_style_threshold
        within_budget = budget_score >= settings.decision_budget_threshold
        # Also enforce hard price check.
        price = product.price_usd
        if price is not None and price > remaining_budget:
            within_budget = False
            budget_score = min(budget_score, 0.2)
        accepted = fits and style_match and within_budget
        reasons = []
        if not fits:
            reasons.append(f"fit={fits_score:.2f}")
        if not style_match:
            reasons.append(f"style={style_score:.2f}")
        if not within_budget:
            reasons.append(f"budget={budget_score:.2f}")
        return DecisionResult(
            fits=fits,
            style_match=style_match,
            within_budget=within_budget,
            fits_score=fits_score,
            style_score=style_score,
            budget_score=budget_score,
            confidence=confidence,
            accepted=accepted,
            reason="; ".join(reasons) or "accepted by Decisions API",
        )
    except Exception as exc:
        logger.warning("Decisions API failed, using heuristic: %s", exc)
        return _heuristic_decision(
            scene,
            slot,
            product,
            remaining_budget=remaining_budget,
            style=style,
            settings=settings,
        )
