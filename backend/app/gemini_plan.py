"""gemini-4-carbon: design planner that proposes shopping slots."""

from __future__ import annotations

import json
import logging

from google.genai import types
from pydantic import ValidationError

from app.config import get_settings
from app.gemini_client import get_genai_client
from app.schemas import DesignPlan, PlacementSlot, SceneAnalysis

logger = logging.getLogger(__name__)

PLAN_SCHEMA_HINT = """
Return ONLY valid JSON:
{
  "summary": "Minimalist black developer desk under $600",
  "style": "minimalist black",
  "budget_usd": 600,
  "keep_object_ids": ["monitor_1"],
  "remove_object_ids": [],
  "slots": [
    {
      "id": "slot_lamp",
      "category": "desk_lamp",
      "surface_id": "desk_surface",
      "region": "left",
      "search_query": "minimalist black LED desk lamp",
      "max_price_usd": 80,
      "preferred_color": "black",
      "max_width_m": 0.2,
      "max_depth_m": 0.2,
      "max_height_m": 0.55,
      "reason": "Task lighting without crowding the monitor",
      "proxy_model": "lamp"
    }
  ]
}
Rules:
- Honor keep_object_ids for monitors/laptops the user wants to keep.
- Propose 2–4 purchasable slots that fit available_positions and remaining budget.
- Categories should map to proxies: lamp, organizer, plant, monitor_stand, shelf, cable_tray.
- search_query must be concrete for Shopify catalog search.
- Leave headroom so sum(max_price_usd) <= budget_usd * 0.95.
"""

CATEGORY_PROXY = {
    "desk_lamp": "lamp",
    "lamp": "lamp",
    "organizer": "organizer",
    "storage": "organizer",
    "plant": "plant",
    "monitor_stand": "monitor_stand",
    "monitor_arm": "monitor_stand",
    "shelf": "shelf",
    "wall_shelf": "shelf",
    "cable_tray": "cable_tray",
    "cable_management": "cable_tray",
}


def _fallback_plan(scene: SceneAnalysis, budget_usd: float, intent: str) -> DesignPlan:
    surface_id = scene.surfaces[0].id if scene.surfaces else "desk_surface"
    keep = [o.id for o in scene.objects if o.keep]
    positions = scene.available_positions or []
    region_a = positions[0].region if positions else "left"
    region_b = positions[1].region if len(positions) > 1 else "right"

    slots = [
        PlacementSlot(
            id="slot_lamp",
            category="desk_lamp",
            surface_id=surface_id,
            region=region_a,
            search_query="minimalist black LED desk lamp",
            max_price_usd=min(90, budget_usd * 0.2),
            preferred_color="black",
            max_width_m=0.2,
            max_depth_m=0.2,
            max_height_m=0.55,
            reason="Task lighting",
            proxy_model="lamp",
        ),
        PlacementSlot(
            id="slot_storage",
            category="organizer",
            surface_id=surface_id,
            region=region_b,
            search_query="black desk organizer storage",
            max_price_usd=min(120, budget_usd * 0.25),
            preferred_color="black",
            max_width_m=0.3,
            max_depth_m=0.2,
            max_height_m=0.25,
            reason="Desktop storage",
            proxy_model="organizer",
        ),
        PlacementSlot(
            id="slot_plant",
            category="plant",
            surface_id=surface_id,
            region="back",
            search_query="small desk plant pot modern",
            max_price_usd=min(40, budget_usd * 0.1),
            preferred_color=None,
            max_width_m=0.15,
            max_depth_m=0.15,
            max_height_m=0.3,
            reason="Softening the workspace",
            proxy_model="plant",
        ),
    ]
    return DesignPlan(
        summary=intent or "Workspace redesign within budget",
        style="minimalist",
        budget_usd=budget_usd,
        keep_object_ids=keep,
        remove_object_ids=[],
        slots=slots,
        model="fallback",
    )


def _normalize_proxies(plan: DesignPlan) -> DesignPlan:
    slots = []
    for slot in plan.slots:
        proxy = slot.proxy_model or CATEGORY_PROXY.get(slot.category, slot.category)
        slots.append(slot.model_copy(update={"proxy_model": proxy}))
    return plan.model_copy(update={"slots": slots})


async def create_design_plan(
    scene: SceneAnalysis,
    *,
    intent_text: str,
    budget_usd: float,
) -> DesignPlan:
    settings = get_settings()

    prompt = (
        "You are RealityCart's design planner. Given a structured scene and user intent, "
        "propose a shoppable redesign plan.\n"
        f"{PLAN_SCHEMA_HINT}\n\n"
        f"Budget USD: {budget_usd}\n"
        f"User intent: {intent_text}\n"
        f"Scene JSON:\n{scene.model_dump_json()}\n"
    )

    try:
        client = get_genai_client()
        response = client.models.generate_content(
            model=settings.plan_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.4,
            ),
        )
        data = json.loads(response.text or "{}")
        plan = DesignPlan.model_validate(data)
        plan.budget_usd = budget_usd
        plan.model = settings.plan_model
        if not plan.slots:
            raise ValueError("Empty slots")
        return _normalize_proxies(plan)
    except (ValidationError, json.JSONDecodeError, Exception) as exc:
        logger.exception("Carbon plan failed: %s", exc)
        return _normalize_proxies(_fallback_plan(scene, budget_usd, intent_text))


async def replan_after_failures(
    scene: SceneAnalysis,
    plan: DesignPlan,
    *,
    failures: list[str],
    intent_text: str,
    budget_usd: float,
) -> DesignPlan:
    """Ask Carbon to adjust slots after Decision API rejections."""
    settings = get_settings()
    client = get_genai_client()
    prompt = (
        "Previous shopping plan failed decision gates. Re-plan with different products/"
        "placements. Return the same JSON schema as before.\n"
        f"Failures:\n- " + "\n- ".join(failures) + "\n"
        f"Budget USD: {budget_usd}\nIntent: {intent_text}\n"
        f"Scene: {scene.model_dump_json()}\n"
        f"Old plan: {plan.model_dump_json()}\n"
        f"{PLAN_SCHEMA_HINT}"
    )
    try:
        response = client.models.generate_content(
            model=settings.plan_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.5,
            ),
        )
        data = json.loads(response.text or "{}")
        new_plan = DesignPlan.model_validate(data)
        new_plan.budget_usd = budget_usd
        new_plan.model = settings.plan_model
        return _normalize_proxies(new_plan)
    except Exception as exc:
        logger.warning("Replan failed, keeping original: %s", exc)
        return plan
