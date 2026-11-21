"""Shared Pydantic models — keep fields aligned with iOS Codable types."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class DimensionsSource(str, Enum):
    roomplan = "roomplan"
    user_measurement = "user_measurement"
    inferred = "inferred"
    approximate = "approximate"


class Surface(BaseModel):
    id: str
    type: str = "horizontal_plane"
    label: str | None = None
    width_m: float | None = None
    depth_m: float | None = None
    height_m: float | None = None
    dimensions_source: DimensionsSource = DimensionsSource.approximate
    confidence: float | None = None


class SceneObject(BaseModel):
    id: str
    type: str
    label: str | None = None
    location: str | None = None
    surface_id: str | None = None
    width_m: float | None = None
    depth_m: float | None = None
    height_m: float | None = None
    keep: bool = True
    notes: str | None = None


class AvailablePosition(BaseModel):
    id: str
    surface_id: str
    region: str
    description: str | None = None
    estimated_clearance_m: float | None = None


class SceneAnalysis(BaseModel):
    scene_type: str = "unknown"
    style_cues: list[str] = Field(default_factory=list)
    surfaces: list[Surface] = Field(default_factory=list)
    objects: list[SceneObject] = Field(default_factory=list)
    available_positions: list[AvailablePosition] = Field(default_factory=list)
    clutter_level: Literal["low", "medium", "high"] = "medium"
    lighting: Literal["bright", "dim", "mixed", "unknown"] = "unknown"
    notes: str | None = None
    model: str = "gemini-4-hydrogen"


class SurfaceCalibration(BaseModel):
    surface_id: str = "primary_surface"
    width_m: float | None = None
    depth_m: float | None = None
    height_m: float | None = None
    corner_pixels: list[list[float]] | None = None


class RoomPlanPayload(BaseModel):
    """Subset of CapturedRoom-derived JSON from the iOS client."""

    raw: dict[str, Any] | None = None
    walls: list[dict[str, Any]] = Field(default_factory=list)
    objects: list[dict[str, Any]] = Field(default_factory=list)
    floors: list[dict[str, Any]] = Field(default_factory=list)


class PlacementSlot(BaseModel):
    id: str
    category: str
    surface_id: str
    region: str
    search_query: str
    max_price_usd: float | None = None
    preferred_color: str | None = None
    max_width_m: float | None = None
    max_depth_m: float | None = None
    max_height_m: float | None = None
    reason: str | None = None
    proxy_model: str | None = None


class DesignPlan(BaseModel):
    summary: str
    style: str
    budget_usd: float
    keep_object_ids: list[str] = Field(default_factory=list)
    remove_object_ids: list[str] = Field(default_factory=list)
    slots: list[PlacementSlot] = Field(default_factory=list)
    model: str = "gemini-4-carbon"


class ProductOffer(BaseModel):
    id: str
    title: str
    description: str | None = None
    price_usd: float | None = None
    currency: str = "USD"
    image_url: str | None = None
    product_url: str | None = None
    vendor: str | None = None
    has_3d: bool = False
    dimensions_hint: str | None = None
    raw: dict[str, Any] | None = None


class DecisionResult(BaseModel):
    fits: bool
    style_match: bool
    within_budget: bool
    fits_score: float
    style_score: float
    budget_score: float
    confidence: float
    accepted: bool
    reason: str


class PlacedProduct(BaseModel):
    slot_id: str
    category: str
    product: ProductOffer
    decision: DecisionResult
    proxy_model: str
    position: dict[str, float] = Field(
        default_factory=lambda: {"x": 0.0, "y": 0.0, "z": 0.0}
    )
    scale: float = 1.0
    rejected_alternatives: list[ProductOffer] = Field(default_factory=list)


class PipelineStep(BaseModel):
    name: str
    status: Literal["ok", "warn", "error", "skipped"]
    detail: str | None = None
    model: str | None = None


class RedesignResponse(BaseModel):
    scene: SceneAnalysis
    plan: DesignPlan
    placements: list[PlacedProduct] = Field(default_factory=list)
    total_usd: float = 0.0
    remaining_budget_usd: float = 0.0
    steps: list[PipelineStep] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str
    gemini_configured: bool
    shopify_profile: str
    models: dict[str, str]
