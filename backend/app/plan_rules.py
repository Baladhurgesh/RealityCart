"""Deterministic guard-rails for Carbon's design plan — no model calls.

Carbon is good at taste (what to buy, how to search for it). These rules make
sure the plan is *executable*:

* every slot sits on a real Hydrogen position → real surface_id + region
* keep/remove ids exist in the scene
* price caps fit the budget (with headroom) — low-priority slots are dropped first
* size limits never exceed the surface / measured clearance
* proxy_model is one the iOS preview can draw
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.schemas import AvailablePosition, PlacementSlot, SceneAnalysis, Surface

BUDGET_HEADROOM = 0.95  # leave 5 % for tax / price drift
MIN_SLOT_CAP_USD = 15.0  # below this, Shopify returns junk
VALID_REGIONS = {"left", "right", "center", "back", "front"}

# --- Proxy mapping (from upstream gemini_plan, kept verbatim) ---------------
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
    "nightstand": "sideboard",
    "side_table": "sideboard",
    "end_table": "sideboard",
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
            "nightstand",
            "night stand",
            "bedside table",
            "side table",
            "end table",
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


COLOR_WORDS = [
    "black", "white", "grey", "gray", "beige", "cream", "brown", "walnut", "oak", "natural",
    "wood", "brass", "gold", "silver", "chrome", "green", "sage", "olive", "blue", "navy",
    "red", "terracotta", "pink", "yellow", "mustard", "orange", "charcoal", "matte black",
]


def proxy_for(category: str, search_query: str | None = None) -> str:
    """iOS-drawable proxy for a slot (lamp | sideboard | tray | organizer | plant | shelf …)."""
    return resolve_proxy_model(category=category, search_query=search_query)


def colors_in(text: str | None) -> list[str]:
    t = (text or "").lower()
    found = [c for c in COLOR_WORDS if re.search(rf"\b{re.escape(c)}\b", t)]
    # "matte black" makes the bare "black" redundant
    return [c for c in found if not any(c != o and c in o for o in found)]


SIZE_WORDS = {"narrow", "slim", "small", "compact", "mini", "petite", "skinny", "tall", "short", "low", "large", "wide"}
MATERIAL_WORDS = {
    "wood", "wooden", "metal", "steel", "iron", "velvet", "linen", "leather", "faux", "glass",
    "ceramic", "marble", "rattan", "bamboo", "fabric", "plastic", "acrylic", "stone", "concrete",
    "solid", "engineered", "upholstered", "woven", "plush",
}
FILLER_WORDS = {"modern", "minimalist", "minimal", "decorative", "stylish", "contemporary", "luxury", "premium", "elegant"}


def size_word(proxy: str, max_width_m: float | None) -> str | None:
    """A shopper's size word when the slot needs something smaller than typical."""
    if not max_width_m:
        return None
    if proxy in ("sideboard", "shelf") and max_width_m < 0.9:
        return "narrow" if max_width_m >= 0.5 else "small"
    if proxy in ("tray", "organizer") and max_width_m < 0.35:
        return "small"
    return None


def with_size_word(query: str, proxy: str, max_width_m: float | None) -> str:
    if any(w in SIZE_WORDS for w in query.lower().split()):
        return query
    word = size_word(proxy, max_width_m)
    return f"{word} {query}" if word else query


def loosen_query(slot_query: str, category: str, color: str | None, *, size: str | None = None) -> str:
    """Cheap retry: keep color + size + item noun, drop materials and filler adjectives."""
    item = category.replace("_", " ")
    kept = [w for w in slot_query.lower().split()
            if w not in MATERIAL_WORDS and w not in FILLER_WORDS and w not in SIZE_WORDS]
    nouns = [w for w in kept if w in item.split()] or item.split()
    parts = [color.lower() if color else None, size] + nouns
    seen, out = set(), []
    for w in parts:
        if w and w not in seen:
            seen.add(w)
            out.append(w)
    return " ".join(out)


def clean_query(query: str, max_words: int = 8) -> str:
    q = re.sub(r"\$\s?\d+(\.\d+)?|\bunder\b|\bbudget\b|\bcheap\b", " ", query, flags=re.I)
    q = re.sub(r"[^\w\s\-&']", " ", q)
    words = q.split()
    return " ".join(words[:max_words]).strip()


@dataclass
class SlotDraft:
    """What Carbon (or the fallback) proposes before guard-rails."""

    position_id: str
    category: str
    search_query: str
    max_price_usd: float
    priority: int = 2
    preferred_color: str | None = None
    max_width_m: float | None = None
    max_depth_m: float | None = None
    max_height_m: float | None = None
    reason: str | None = None


def _surface(scene: SceneAnalysis, sid: str) -> Surface | None:
    return next((s for s in scene.surfaces if s.id == sid), None)


def _best_position(
    scene: SceneAnalysis, category: str, used: dict[str, int]
) -> AvailablePosition | None:
    """Pick a position whose suggested categories match, preferring unused ones."""
    if not scene.available_positions:
        return None
    proxy = proxy_for(category)

    def score(p: AvailablePosition) -> tuple[int, int]:
        cats = [c.lower() for c in p.suggested_categories]
        match = category.lower() in cats or any(proxy_for(c) == proxy != "generic" for c in cats)
        return (0 if match else 1, used.get(p.id, 0))

    return min(scene.available_positions, key=score)


REPLACE_PREFIX = "replace:"
REPLACE_SIZE_MARGIN = 1.2  # successor may be up to 20 % bigger than what it replaces


def replace_target(position_id: str, scene: SceneAnalysis) -> str | None:
    """'replace:rp_1' (or a bare object id used as a position) → 'rp_1'."""
    oid = position_id.removeprefix(REPLACE_PREFIX)
    return oid if any(o.id == oid for o in scene.objects) else None


def replacement_positions(scene: SceneAnalysis, remove_ids: list[str]) -> dict[str, AvailablePosition]:
    """The footprint of each removed object becomes a placeable position."""
    out = {}
    default_sid = scene.surfaces[0].id if scene.surfaces else "floor"
    for o in scene.objects:
        if o.id not in remove_ids:
            continue
        x = (o.position_m or {}).get("x", 0.0)
        region = "left" if x < -0.3 else "right" if x > 0.3 else "center"
        pid = f"{REPLACE_PREFIX}{o.id}"
        out[pid] = AvailablePosition(
            id=pid,
            surface_id=o.surface_id or default_sid,
            region=region,
            description=f"Footprint of the {o.type} being replaced",
            estimated_clearance_m=round(o.width_m * REPLACE_SIZE_MARGIN, 2) if o.width_m else None,
            suggested_categories=[o.type],
        )
    return out


def _clamp(value: float | None, limit: float | None) -> float | None:
    if limit is None or limit <= 0:
        return value
    return limit if value is None else min(value, limit)


def allocate_budget(
    drafts: list[SlotDraft], budget_usd: float, warnings: list[str]
) -> list[SlotDraft]:
    """Scale caps to fit budget×headroom; drop lowest-priority slots that become too cheap."""
    limit = budget_usd * BUDGET_HEADROOM
    kept = sorted(drafts, key=lambda d: d.priority)
    while kept:
        total = sum(max(d.max_price_usd, 0) for d in kept)
        factor = min(1.0, limit / total) if total > 0 else 1.0
        if factor < 1.0 and any(d.max_price_usd * factor < MIN_SLOT_CAP_USD for d in kept) and len(kept) > 1:
            dropped = kept.pop()  # highest priority number = least important
            warnings.append(
                f"Dropped '{dropped.category}' (priority {dropped.priority}) to fit ${budget_usd:.0f} budget."
            )
            continue
        if factor < 1.0:
            warnings.append(f"Scaled price caps by {factor:.2f} to stay under ${limit:.0f}.")
        for d in kept:
            d.max_price_usd = round(max(d.max_price_usd * factor, 0.0), 2)
        break
    return kept


def finalize_slots(
    drafts: list[SlotDraft],
    scene: SceneAnalysis,
    budget_usd: float,
    *,
    max_slots: int,
    warnings: list[str],
    remove_ids: list[str] | None = None,
) -> list[PlacementSlot]:
    valid_positions = {p.id: p for p in scene.available_positions}
    valid_positions.update(replacement_positions(scene, remove_ids or []))
    used: dict[str, int] = {}
    placed: list[tuple[SlotDraft, AvailablePosition | None]] = []

    for d in drafts:
        d.category = re.sub(r"[^a-z0-9_]+", "_", d.category.lower()).strip("_") or "decor"
        target = replace_target(d.position_id, scene)
        if target and f"{REPLACE_PREFIX}{target}" in valid_positions:
            d.position_id = f"{REPLACE_PREFIX}{target}"
        pos = valid_positions.get(d.position_id)
        if pos is None:
            pos = _best_position(scene, d.category, used)
            warnings.append(
                f"Carbon used unknown position '{d.position_id}' for {d.category}; "
                f"moved to '{pos.id if pos else 'none'}'."
            )
        if pos is not None:
            used[pos.id] = used.get(pos.id, 0) + 1
        placed.append((d, pos))

    # Deduplicate identical categories at the same spot (model repeats itself sometimes).
    seen, unique = set(), []
    for d, pos in placed:
        key = (d.category, pos.id if pos else None)
        if key in seen:
            warnings.append(f"Removed duplicate slot '{d.category}' at '{key[1]}'.")
            continue
        seen.add(key)
        unique.append((d, pos))

    unique.sort(key=lambda t: t[0].priority)
    if len(unique) > max_slots:
        cut = [d.category for d, _ in unique[max_slots:]]
        warnings.append(f"Kept top {max_slots} slots for latency; deferred: {', '.join(cut)}.")
        unique = unique[:max_slots]

    pos_by_draft = {id(d): pos for d, pos in unique}
    budgeted = allocate_budget([d for d, _ in unique], budget_usd, warnings)

    slots: list[PlacementSlot] = []
    default_surface = scene.surfaces[0].id if scene.surfaces else "floor"
    for i, d in enumerate(budgeted, start=1):
        pos = pos_by_draft[id(d)]
        surface_id = pos.surface_id if pos else default_surface
        region = pos.region if pos and pos.region in VALID_REGIONS else "center"
        surf = _surface(scene, surface_id)
        # A raised surface (table top) really bounds the item; the floor's size is the whole
        # room, so it must never become a slot limit (that let a 0.88 m cabinet "rotate" into
        # a 0.5 m gap because depth was 3.8 m).
        elevated = bool(surf and surf.height_m and surf.height_m > 0.05)
        max_w = _clamp(d.max_width_m, surf.width_m if surf else None)
        if max_w is not None and not elevated and d.max_width_m is None:
            max_w = None
        max_w = _clamp(max_w, pos.estimated_clearance_m if pos else None)
        max_d = _clamp(d.max_depth_m, surf.depth_m if surf else None)
        if max_d is not None and not elevated and d.max_depth_m is None:
            max_d = None
        query = clean_query(d.search_query) or d.category.replace("_", " ")
        query = with_size_word(query, proxy_for(d.category, query), max_w)
        color = (d.preferred_color or "").strip().lower() or None
        slots.append(
            PlacementSlot(
                id=f"slot_{i}_{d.category}",
                category=d.category,
                surface_id=surface_id,
                region=region,
                position_id=pos.id if pos else None,
                search_query=query,
                max_price_usd=d.max_price_usd,
                preferred_color=color,
                max_width_m=max_w,
                max_depth_m=max_d,
                max_height_m=d.max_height_m,
                reason=d.reason,
                proxy_model=proxy_for(d.category, query),
                priority=d.priority,
            )
        )
    return slots


def validate_object_ids(
    scene: SceneAnalysis, keep: list[str], remove: list[str], warnings: list[str]
) -> tuple[list[str], list[str]]:
    known = {o.id for o in scene.objects}
    bad = [i for i in keep + remove if i not in known]
    if bad:
        warnings.append(f"Carbon referenced unknown objects: {', '.join(sorted(set(bad)))}.")
    remove_ok = [i for i in dict.fromkeys(remove) if i in known]
    keep_ok = [i for i in dict.fromkeys(keep) if i in known and i not in remove_ok]
    # Anything Carbon didn't mention keeps Hydrogen's default.
    mentioned = set(keep_ok) | set(remove_ok)
    keep_ok += [o.id for o in scene.objects if o.keep and o.id not in mentioned]
    return keep_ok, remove_ok


def fallback_drafts(scene: SceneAnalysis, intent_text: str, budget_usd: float) -> list[SlotDraft]:
    """Rule-based plan from Hydrogen's suggested categories (used when Carbon fails)."""
    color = (colors_in(intent_text) or [None])[0]
    style = (scene.style_cues or ["modern"])[0]
    drafts: list[SlotDraft] = []
    seen_proxies: set[str] = set()
    shares = [0.45, 0.35, 0.2]
    for pos in scene.available_positions:
        options = pos.suggested_categories or (
            ["plant", "floor_lamp"] if pos.surface_id == "floor" else ["organizer", "table_lamp"]
        )
        cat = next((c for c in options if proxy_for(c) not in seen_proxies), options[0])
        seen_proxies.add(proxy_for(cat))
        n = len(drafts)
        drafts.append(
            SlotDraft(
                position_id=pos.id,
                category=cat,
                search_query=" ".join(x for x in (color, style, cat.replace("_", " ")) if x),
                max_price_usd=budget_usd * shares[min(n, len(shares) - 1)],
                priority=min(n + 1, 3),
                preferred_color=color,
                reason=f"Fills open spot: {pos.description or pos.region}",
            )
        )
        if len(drafts) == 3:
            break
    return drafts
