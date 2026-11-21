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
  "summary": "Warm minimalist living room refresh under $600",
  "style": "minimalist warm",
  "budget_usd": 600,
  "keep_object_ids": ["sofa_1"],
  "remove_object_ids": [],
  "slots": [
    {
      "id": "slot_lamp",
      "category": "floor_lamp",
      "surface_id": "primary_surface",
      "region": "left",
      "search_query": "minimalist black floor lamp",
      "max_price_usd": 120,
      "preferred_color": "black",
      "max_width_m": 0.4,
      "max_depth_m": 0.4,
      "max_height_m": 1.6,
      "reason": "Ambient lighting in an open corner",
      "proxy_model": "lamp"
    }
  ]
}
Rules:
- The space may be any room (living room, bedroom, kitchen, office, studio, etc.). Match products to that space.
- Honor keep_object_ids for furniture/objects the user wants to keep.
- Propose 2–4 purchasable slots that fit available_positions and remaining budget.
- Categories should map to proxies when possible: lamp, sideboard, cabinet, tray, organizer, plant, monitor_stand, shelf, cable_tray, chair, table, sofa, bed, storage.
- proxy_model must match the *shape* of the product (tray ≠ tall cabinet). Prefer: lamp | sideboard | tray | organizer | plant | shelf.
- search_query must be concrete for Shopify catalog search and appropriate to the room type.
- Leave headroom so sum(max_price_usd) <= budget_usd * 0.95.
"""

# Canonical procedural proxies rendered on iOS (not Shopify product meshes).
CATEGORY_PROXY = {
    "desk_lamp": "lamp",
    "floor_lamp": "lamp",
    "table_lamp": "lamp",
    "lamp": "lamp",
    "lighting": "lamp",
    "organizer": "organizer",
    "desk_organizer": "organizer",
    "tabletop_organizer": "organizer",
    "storage": "sideboard",
    "cabinet": "sideboard",
    "sideboard": "sideboard",
    "credenza": "sideboard",
    "console": "sideboard",
    "dresser": "sideboard",
    "media_console": "sideboard",
    "tray": "tray",
    "catchall": "tray",
    "catch_all": "tray",
    "dish": "tray",
    "bowl": "tray",
    "plant": "plant",
    "monitor_stand": "monitor_stand",
    "monitor_arm": "monitor_stand",
    "shelf": "shelf",
    "wall_shelf": "shelf",
    "bookshelf": "shelf",
    "accent_shelf": "shelf",
    "cable_tray": "tray",
    "cable_management": "tray",
    "chair": "organizer",
    "table": "sideboard",
    "sofa": "sideboard",
    "bed": "sideboard",
}

# Keyword → proxy (checked against category + query + product title).
_PROXY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("lamp", ("lamp", "lighting", "sconce", "pendant", "lantern")),
    ("tray", ("tray", "catchall", "catch-all", "catch all", "dish", "bowl", "valet")),
    (
        "sideboard",
        (
            "sideboard",
            "cabinet",
            "credenza",
            "console",
            "dresser",
            "media console",
            "buffet",
            "armoire",
            "wardrobe",
        ),
    ),
    ("plant", ("plant", "planter", "succulent")),
    ("shelf", ("shelf", "bookshelf", "bookcase", "floating shelf")),
    ("organizer", ("organizer", "caddy", "desk tidy", "pen holder")),
]


def resolve_proxy_model(
    *,
    category: str | None = None,
    proxy_model: str | None = None,
    search_query: str | None = None,
    product_title: str | None = None,
) -> str:
    """Pick a procedural proxy that matches the product shape, not just the slot id."""
    known = {
        "lamp",
        "sideboard",
        "tray",
        "organizer",
        "plant",
        "shelf",
        "monitor_stand",
        "cable_tray",
        "storage",
    }
    if proxy_model:
        key = proxy_model.lower().strip().replace(" ", "_").replace("-", "_")
        if key in known:
            return "sideboard" if key == "storage" else ("tray" if key == "cable_tray" else key)
        mapped = CATEGORY_PROXY.get(key)
        if mapped:
            return mapped

    blob = " ".join(
        part for part in (category, search_query, product_title, proxy_model) if part
    ).lower()

    for proxy, keywords in _PROXY_KEYWORDS:
        if any(k in blob for k in keywords):
            return proxy

    if category:
        mapped = CATEGORY_PROXY.get(category.lower().strip().replace(" ", "_"))
        if mapped:
            return mapped

    return "organizer"


def _fallback_plan(scene: SceneAnalysis, budget_usd: float, intent: str) -> DesignPlan:
    surface_id = scene.surfaces[0].id if scene.surfaces else "primary_surface"
    keep = [o.id for o in scene.objects if o.keep]
    positions = scene.available_positions or []
    region_a = positions[0].region if positions else "left"
    region_b = positions[1].region if len(positions) > 1 else "right"
    room = scene.scene_type or "room"

    slots = [
        PlacementSlot(
            id="slot_lamp",
            category="lamp",
            surface_id=surface_id,
            region=region_a,
            search_query=f"minimalist lamp for {room.replace('_', ' ')}",
            max_price_usd=min(120, budget_usd * 0.25),
            preferred_color="black",
            max_width_m=0.4,
            max_depth_m=0.4,
            max_height_m=1.6,
            reason="Lighting for the space",
            proxy_model="lamp",
        ),
        PlacementSlot(
            id="slot_storage",
            category="storage",
            surface_id=surface_id,
            region=region_b,
            search_query=f"modern storage organizer for {room.replace('_', ' ')}",
            max_price_usd=min(180, budget_usd * 0.3),
            preferred_color="black",
            max_width_m=0.6,
            max_depth_m=0.4,
            max_height_m=1.0,
            reason="Storage that fits an open region",
            proxy_model="organizer",
        ),
        PlacementSlot(
            id="slot_plant",
            category="plant",
            surface_id=surface_id,
            region="back",
            search_query="indoor plant pot modern",
            max_price_usd=min(50, budget_usd * 0.1),
            preferred_color=None,
            max_width_m=0.3,
            max_depth_m=0.3,
            max_height_m=0.8,
            reason="Softening the space",
            proxy_model="plant",
        ),
    ]
    return DesignPlan(
        summary=intent or f"{room.replace('_', ' ').title()} redesign within budget",
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
        proxy = resolve_proxy_model(
            category=slot.category,
            proxy_model=slot.proxy_model,
            search_query=slot.search_query,
        )
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
        "You are RealityCart's design planner. Given a structured scene of ANY indoor "
        "space and user intent, propose a shoppable redesign plan.\n"
        f"{PLAN_SCHEMA_HINT}\n\n"
        f"Budget USD: {budget_usd}\n"
        f"User intent: {intent_text}\n"
        f"Scene JSON:\n{scene.model_dump_json()}\n"
    )

    try:
        client = get_genai_client()
        logger.info("Carbon design plan starting…")
        response = client.models.generate_content(
            model=settings.plan_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.3,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=True
                ),
                thinking_config=types.ThinkingConfig(thinking_level="low"),
            ),
        )
        data = json.loads(response.text or "{}")
        plan = DesignPlan.model_validate(data)
        plan.budget_usd = budget_usd
        plan.model = settings.plan_model
        if not plan.slots:
            raise ValueError("Empty slots")
        # Cap slots for latency (Shopify + Decisions per slot).
        if len(plan.slots) > 3:
            plan.slots = plan.slots[:3]
        logger.info("Carbon plan ok: %d slots — %s", len(plan.slots), plan.summary[:80])
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
    prompt = (
        "Previous shopping plan failed decision gates. Re-plan with different products/"
        "placements for this space. Return the same JSON schema as before.\n"
        f"Failures:\n- " + "\n- ".join(failures) + "\n"
        f"Budget USD: {budget_usd}\nIntent: {intent_text}\n"
        f"Scene: {scene.model_dump_json()}\n"
        f"Old plan: {plan.model_dump_json()}\n"
        f"{PLAN_SCHEMA_HINT}"
    )
    try:
        client = get_genai_client()
        response = client.models.generate_content(
            model=settings.plan_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.4,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=True
                ),
                thinking_config=types.ThinkingConfig(thinking_level="low"),
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
