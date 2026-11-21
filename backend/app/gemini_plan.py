"""gemini-4-neon: design planner (scene + intent + budget → shoppable slots).

Carbon owns taste: style direction, what to keep/remove/replace, which items to
buy and how to search for them. ``plan_rules`` then makes the plan executable
(real positions/surfaces, budget-safe caps, size limits, drawable proxies).
Every correction is recorded in ``plan.warnings``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Literal

from google.genai import types
from pydantic import BaseModel, Field, ValidationError

from app.config import get_settings
from app.gemini_client import get_genai_client
from app.plan_rules import (
    BUDGET_HEADROOM,
    SlotDraft,
    colors_in,
    fallback_drafts,
    finalize_slots,
    resolve_proxy_model,  # noqa: F401 — re-exported for pipeline
    replace_target,
    validate_object_ids,
)
from app.schemas import DesignPlan, SceneAnalysis

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Structured-output schema Carbon must follow.
# --------------------------------------------------------------------------


class CObjectDecision(BaseModel):
    object_id: str = Field(description="Exact id from the scene objects list")
    action: Literal["keep", "remove", "replace"]
    reason: str


class CSlot(BaseModel):
    position_id: str = Field(description="Exact id from the open positions list")
    category: str = Field(description="snake_case product type, e.g. floor_lamp, side_table, wall_shelf")
    search_query: str = Field(description="3-8 word shopping query: color + material + item. No prices.")
    preferred_color: str | None = None
    priority: int = Field(description="1 = must-have for the intent, 2 = strong, 3 = nice-to-have")
    max_price_usd: float = Field(description="Price cap for this item")
    max_width_m: float | None = None
    max_depth_m: float | None = None
    max_height_m: float | None = None
    reason: str = Field(description="One sentence: why this item, why here")


class CarbonOutput(BaseModel):
    # Required lists — optional ones get silently omitted by Gemini.
    summary: str = Field(description="One-line pitch of the redesign")
    style: str = Field(description="2-4 word style name, e.g. 'warm minimalist'")
    color_direction: list[str] = Field(description="2-4 target colors/finishes")
    object_decisions: list[CObjectDecision]
    slots: list[CSlot]


PROMPT = """You are Neon, RealityCart's interior design planner.
Turn the user's request into a short shopping plan for THIS room, using only
real open positions from the scene analysis.

USER REQUEST: {intent}
TOTAL BUDGET: ${budget:.0f} (plan caps should add up to at most ${cap:.0f})
{color_hint}
ROOM: {scene_type}; style now: {style}; palette now: {palette};
lighting: {lighting}; clutter: {clutter}; image issues: {issues}

EXISTING OBJECTS (id | type | size W×D×H m | look | default keep):
{objects}

OPEN POSITIONS (position_id | surface | surface size & height | region | clearance | ideas):
{positions}

Rules:
- Propose {min_slots}-{max_slots} slots, most important first. Each slot uses an exact position_id.
- Satisfy explicit asks in the request first (priority 1); add complementary items only if budget allows.
- Size every item for its surface: a table-top item must fit the surface; floor items
  must fit the clearance. Give max_width_m / max_depth_m / max_height_m in meters.
- search_query is what a shopper types into a store: color + material + item, 3-8 words.
  Do not put prices or the word "budget" in queries. If the item must be smaller than
  usual (e.g. storage under 0.9 m wide, a tray under 0.35 m), include a size word such as
  narrow, slim, small or compact — otherwise the store returns full-size items.
- object_decisions: one entry per existing object. Keep what the user says to keep and
  anything large unless the request implies replacing it.
- To REPLACE an object: mark it "replace" AND add a slot with position_id "replace:<object_id>"
  (e.g. "replace:rp_1"). That slot goes in the old object's footprint, sized like the old object.
- Respect the room type: no desk gear in a bedroom unless asked.
{replan}"""

REPLAN_BLOCK = """
THIS IS A RE-PLAN. The previous plan failed these checks:
{failures}
Previous slots: {previous}
{fixed}
Change what failed: broaden or rephrase queries that found nothing, raise caps only if
budget allows, choose a different position or smaller item if fit failed, a different
color/material if style failed. Keep slots that did not fail."""


def _fmt(v: float | None) -> str:
    return "?" if v is None else f"{v:.2f}"


def _scene_digest(scene: SceneAnalysis) -> dict[str, str]:
    surfaces = {s.id: s for s in scene.surfaces}
    objects = "\n".join(
        f"- {o.id} | {o.type} | {_fmt(o.width_m)}×{_fmt(o.depth_m)}×{_fmt(o.height_m)} | "
        f"{' '.join(x for x in (o.color, o.material) if x) or '-'}"
        f"{'' if o.visible_in_photo is not False else ' (not in photo)'} | {'keep' if o.keep else 'drop?'}"
        for o in scene.objects
    ) or "- (none — empty room)"
    lines = []
    for p in scene.available_positions:
        s = surfaces.get(p.surface_id)
        size = f"{_fmt(s.width_m)}×{_fmt(s.depth_m)} m @h{_fmt(s.height_m)} [{s.dimensions_source.value}]" if s else "?"
        lines.append(
            f"- {p.id} | {p.surface_id} | {size} | {p.region} | {_fmt(p.estimated_clearance_m)} m | "
            f"{', '.join(p.suggested_categories) or '-'} — {p.description or ''}"
        )
    return {
        "scene_type": scene.scene_type,
        "style": ", ".join(scene.style_cues) or "?",
        "palette": ", ".join(scene.palette) or "?",
        "lighting": scene.lighting,
        "clutter": scene.clutter_level,
        "issues": ", ".join(scene.image_issues) or "none",
        "objects": objects,
        "positions": "\n".join(lines) or "- (none)",
    }


def build_prompt(
    scene: SceneAnalysis,
    intent_text: str,
    budget_usd: float,
    *,
    failures: list[str] | None = None,
    previous: DesignPlan | None = None,
    fixed_items: list[str] | None = None,
    max_slots: int | None = None,
) -> str:
    settings = get_settings()
    colors = colors_in(intent_text)
    replan = ""
    if failures:
        prev = [
            {"slot": s.id, "category": s.category, "position_id": s.position_id,
             "query": s.search_query, "cap": s.max_price_usd}
            for s in (previous.slots if previous else [])
        ]
        fixed = (
            "ALREADY BOUGHT (do not re-plan these, do not reuse their positions): "
            + "; ".join(fixed_items)
            + f"\nPlan ONLY replacements for the failed slots above, at most {max_slots or len(prev) or 1}."
        ) if fixed_items else ""
        replan = REPLAN_BLOCK.format(
            failures="\n".join(f"- {f}" for f in failures[:12]), previous=json.dumps(prev), fixed=fixed
        )
    return PROMPT.format(
        intent=intent_text.strip() or "Refresh this room",
        budget=budget_usd,
        cap=budget_usd * BUDGET_HEADROOM,
        color_hint=f"COLORS THE USER ASKED FOR: {', '.join(colors)}" if colors else "",
        min_slots=min(2, max_slots or settings.plan_max_slots) if not fixed_items else 1,
        max_slots=max_slots or settings.plan_max_slots,
        replan=replan,
        **_scene_digest(scene),
    )


def assemble_plan(
    out: CarbonOutput | None,
    scene: SceneAnalysis,
    intent_text: str,
    budget_usd: float,
    *,
    model: str,
    warnings: list[str] | None = None,
    max_slots: int | None = None,
) -> DesignPlan:
    """Carbon output (or None → fallback) + guard-rails → DesignPlan. Pure; unit tested."""
    settings = get_settings()
    max_slots = max_slots or settings.plan_max_slots
    warnings = list(warnings or [])
    if out is not None and out.slots:
        drafts = [
            SlotDraft(
                position_id=s.position_id,
                category=s.category,
                search_query=s.search_query,
                max_price_usd=max(s.max_price_usd, 0.0),
                priority=min(max(s.priority, 1), 3),
                preferred_color=s.preferred_color,
                max_width_m=s.max_width_m,
                max_depth_m=s.max_depth_m,
                max_height_m=s.max_height_m,
                reason=s.reason,
            )
            for s in out.slots
        ]
        keep = [d.object_id for d in out.object_decisions if d.action == "keep"]
        remove = [d.object_id for d in out.object_decisions if d.action in ("remove", "replace")]
        summary, style, colors = out.summary, out.style, out.color_direction
    else:
        if out is not None:
            warnings.append("Carbon returned no slots; using rule-based plan.")
            model = "fallback"
        drafts = fallback_drafts(scene, intent_text, budget_usd)
        keep, remove = [], []
        colors = colors_in(intent_text)
        style = ", ".join(scene.style_cues[:2]) or "modern"
        summary = intent_text.strip() or f"{scene.scene_type.replace('_', ' ').title()} refresh"

    # A slot that replaces an object implies removing that object.
    for d in drafts:
        target = replace_target(d.position_id, scene)
        if target and target not in remove:
            remove.append(target)
            warnings.append(f"Slot '{d.category}' replaces '{target}', so it was marked for removal.")
    keep, remove = validate_object_ids(scene, keep, remove, warnings)
    slots = finalize_slots(
        drafts, scene, budget_usd, max_slots=max_slots, warnings=warnings, remove_ids=remove
    )
    if not slots:
        warnings.append("No placeable slots — scene has no open positions.")
    return DesignPlan(
        summary=summary,
        style=style,
        budget_usd=budget_usd,
        keep_object_ids=keep,
        remove_object_ids=remove,
        slots=slots,
        color_direction=colors,
        allocated_usd=round(sum(s.max_price_usd or 0 for s in slots), 2),
        warnings=warnings,
        model=model,
    )


async def _call_carbon(prompt: str, *, temperature: float) -> CarbonOutput:
    settings = get_settings()
    response = await get_genai_client().aio.models.generate_content(
        model=settings.plan_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=CarbonOutput,
            temperature=temperature,
            thinking_config=types.ThinkingConfig(thinking_level=settings.plan_thinking_level),
        ),
    )
    if isinstance(response.parsed, CarbonOutput):
        return response.parsed
    return CarbonOutput.model_validate(json.loads(response.text or "{}"))


async def _plan(
    scene: SceneAnalysis,
    intent_text: str,
    budget_usd: float,
    *,
    failures: list[str] | None = None,
    previous: DesignPlan | None = None,
    fixed_items: list[str] | None = None,
    max_slots: int | None = None,
    attempts: int = 2,
) -> DesignPlan | None:
    settings = get_settings()
    started = time.perf_counter()
    prompt = build_prompt(
        scene, intent_text, budget_usd, failures=failures, previous=previous,
        fixed_items=fixed_items, max_slots=max_slots,
    )
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            out = await asyncio.wait_for(
                _call_carbon(prompt, temperature=0.4 if failures else 0.3),
                timeout=settings.plan_timeout_s,
            )
            warnings = [f"Carbon succeeded on retry {attempt} (first error: {last_error})."] if attempt > 1 else []
            plan = assemble_plan(
                out, scene, intent_text, budget_usd, model=settings.plan_model,
                warnings=warnings, max_slots=max_slots,
            )
            plan.latency_ms = int((time.perf_counter() - started) * 1000)
            logger.info(
                "Carbon %s ok in %dms: %d slots, $%.0f allocated, %d warnings",
                "replan" if failures else "plan", plan.latency_ms, len(plan.slots),
                plan.allocated_usd, len(plan.warnings),
            )
            return plan
        except (ValidationError, json.JSONDecodeError) as exc:
            last_error = exc
            logger.warning("Carbon attempt %d returned invalid JSON: %s", attempt, exc)
        except TimeoutError:
            last_error = TimeoutError(f"Carbon timed out after {settings.plan_timeout_s:.0f}s")
            logger.warning("Carbon attempt %d timed out", attempt)
        except Exception as exc:  # noqa: BLE001 — network / auth / quota
            last_error = exc
            logger.warning("Carbon attempt %d failed: %s", attempt, exc)
            if "GEMINI_API_KEY" in str(exc):
                break
    logger.error("Carbon failed after %d attempts: %s", attempts, last_error)
    return None if previous else _fallback(scene, intent_text, budget_usd, started, last_error)


def _fallback(scene, intent_text, budget_usd, started, error) -> DesignPlan:
    plan = assemble_plan(
        None, scene, intent_text, budget_usd, model="fallback",
        warnings=[f"Carbon failed: {str(error)[:300]}"] if error else None,
    )
    plan.latency_ms = int((time.perf_counter() - started) * 1000)
    return plan


def _fallback_plan(scene: SceneAnalysis, budget_usd: float, intent: str) -> DesignPlan:
    """Kept for scripts/smoke_pipeline_offline.py."""
    return assemble_plan(None, scene, intent, budget_usd, model="fallback")


async def create_design_plan(
    scene: SceneAnalysis,
    *,
    intent_text: str,
    budget_usd: float,
) -> DesignPlan:
    plan = await _plan(scene, intent_text, budget_usd)
    assert plan is not None  # first plan always falls back instead of None
    return plan


async def replan_after_failures(
    scene: SceneAnalysis,
    plan: DesignPlan,
    *,
    failures: list[str],
    intent_text: str,
    budget_usd: float,
    fixed_items: list[str] | None = None,
    max_slots: int | None = None,
) -> DesignPlan:
    """Ask Carbon to fix what Shopify/Decisions rejected; keep the old plan if that fails.

    With ``fixed_items`` it re-plans only the failed slots (``plan`` should then contain
    just those) and ``budget_usd`` should be what is left after the fixed purchases.
    """
    new_plan = await _plan(
        scene, intent_text, budget_usd, failures=failures, previous=plan,
        fixed_items=fixed_items, max_slots=max_slots,
    )
    if new_plan is None:
        plan.warnings.append("Re-plan failed; kept the previous plan.")
        return plan
    return new_plan
