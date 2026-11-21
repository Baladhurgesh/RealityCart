"""RealityCart FastAPI entrypoint."""

from __future__ import annotations

import json
import logging
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.config import get_settings
from app.gemini_plan import create_design_plan
from app.gemini_scene import analyze_scene
from app.meshy import ensure_model, get_model, usdz_path
from app.pipeline import run_redesign
from app.schemas import (
    CartCheckoutItem,
    CartCheckoutResult,
    CatalogPage,
    CheckoutSession,
    DesignPlan,
    HealthResponse,
    PlanRequest,
    ProductOffer,
    RedesignResponse,
    RoomPlanPayload,
    SceneAnalysis,
    SurfaceCalibration,
)
from app.shopify_mcp import create_shop_checkout, get_product, search_catalog_page

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()
app = FastAPI(
    title="RealityCart API",
    description="Element 119 hybrid redesign: Gemini + Decisions + Shopify Catalog MCP",
    version="0.1.0",
)

origins = (
    ["*"]
    if settings.cors_origins.strip() == "*"
    else [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        gemini_configured=bool(settings.gemini_api_key),
        shopify_profile=settings.shopify_agent_profile,
        shopify_transport=settings.shopify_transport,
        models={
            "scene": settings.scene_model,
            "plan": settings.plan_model,
            "decision": settings.decision_model,
        },
    )


@app.get("/v1/shopify/search", response_model=CatalogPage)
async def shopify_search(
    q: str,
    max_price: float | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 5,
    cursor: str | None = None,
    country: str | None = None,
    currency: str | None = None,
    transport: Annotated[str | None, Query(pattern="^(shop|catalog|auto)$")] = None,
) -> CatalogPage:
    """Paginated catalog search (Shop MCP / Catalog MCP). Pass `cursor` for the next page."""
    try:
        return await search_catalog_page(
            q,
            max_price_usd=max_price,
            limit=limit,
            cursor=cursor,
            country=country,
            currency=currency,
            transport=transport,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/v1/shopify/products/{product_id:path}", response_model=ProductOffer)
async def shopify_product(
    product_id: str,
    country: str | None = None,
    currency: str | None = None,
    transport: Annotated[str | None, Query(pattern="^(shop|catalog|auto)$")] = None,
) -> ProductOffer:
    """Full product detail (all variants / images) via get_product."""
    try:
        return await get_product(
            product_id, country=country, currency=currency, transport=transport
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class CheckoutRequest(BaseModel):
    shop: str
    variant_id: str
    quantity: int = 1


class CartCheckoutRequest(BaseModel):
    items: list[CartCheckoutItem]


@app.post("/v1/checkout/cart", response_model=CartCheckoutResult)
async def checkout_cart(body: CartCheckoutRequest) -> CartCheckoutResult:
    """One Shop checkout per merchant, with every cart line for that store."""
    if not body.items:
        raise HTTPException(status_code=400, detail="Cart is empty")
    grouped: dict[str, list[tuple[str, int]]] = {}
    for item in body.items:
        if not item.shop or not item.variant_id:
            raise HTTPException(status_code=400, detail="Each item needs shop and variant_id")
        grouped.setdefault(item.shop, []).append((item.variant_id, item.quantity))
    sessions = []
    try:
        for shop, lines in grouped.items():
            payload = await create_shop_checkout(shop=shop, lines=lines)
            sessions.append(CheckoutSession.model_validate(payload))
    except Exception as exc:
        logger.exception("Cart checkout failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return CartCheckoutResult(checkouts=sessions)


@app.post("/v1/checkout", response_model=CheckoutSession)
async def checkout(body: CheckoutRequest) -> CheckoutSession:
    """Open a Shop MCP checkout for one variant. Payment happens on the merchant page."""
    if not body.shop or not body.variant_id:
        raise HTTPException(status_code=400, detail="shop and variant_id are required")
    try:
        payload = await create_shop_checkout(
            shop=body.shop,
            variant_id=body.variant_id,
            quantity=body.quantity,
        )
        return CheckoutSession.model_validate(payload)
    except Exception as exc:
        logger.exception("Checkout failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class ModelGenerateRequest(BaseModel):
    product_id: str
    image_urls: list[str] = Field(default_factory=list)


@app.post("/v1/models")
async def start_model(body: ModelGenerateRequest):
    """Start (or reuse) a Meshy USDZ for a product that has no merchant 3D file."""
    return await ensure_model(body.product_id, body.image_urls)


@app.get("/v1/models/{key}")
async def model_status(key: str):
    job = get_model(key)
    if job is None:
        raise HTTPException(status_code=404, detail="No model job for that product")
    return job


@app.get("/v1/models/{key}/file")
async def model_file(key: str):
    path = usdz_path(key)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Model is not ready")
    return FileResponse(path, media_type="model/vnd.usdz+zip", filename="model.usdz")


@app.post("/v1/redesign", response_model=RedesignResponse)
async def redesign(
    image: Annotated[UploadFile, File(description="Room or space capture")],
    intent_text: Annotated[str, Form()] = "Minimalist room redesign",
    budget_usd: Annotated[float, Form()] = 600.0,
    room_plan_json: Annotated[str | None, Form()] = None,
    calibration_json: Annotated[str | None, Form()] = None,
) -> RedesignResponse:
    raw = await image.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty image upload")

    mime = image.content_type or "image/jpeg"
    room_plan = None
    calibration = None
    try:
        if room_plan_json:
            room_plan = RoomPlanPayload.model_validate(json.loads(room_plan_json))
        if calibration_json:
            calibration = SurfaceCalibration.model_validate(json.loads(calibration_json))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON form field: {exc}") from exc

    try:
        return await run_redesign(
            image_bytes=raw,
            mime_type=mime,
            intent_text=intent_text,
            budget_usd=budget_usd,
            room_plan=room_plan,
            calibration=calibration,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Redesign failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/v1/plan", response_model=DesignPlan)
async def plan_only(req: PlanRequest) -> DesignPlan:
    """Carbon design plan from a scene JSON (e.g. a saved /v1/scene result). For testing step 3."""
    return await create_design_plan(req.scene, intent_text=req.intent_text, budget_usd=req.budget_usd)


@app.post("/v1/scene", response_model=SceneAnalysis)
async def scene_only(
    image: Annotated[UploadFile, File(description="Primary room photo")],
    room_plan_json: Annotated[str | None, Form()] = None,
    calibration_json: Annotated[str | None, Form()] = None,
    intent_text: Annotated[str | None, Form()] = None,
    extra_images: Annotated[list[UploadFile] | None, File(description="Optional extra keyframes (max 3)")] = None,
) -> SceneAnalysis:
    """Hydrogen scene parse only (no planning / shopping). For testing step 2."""
    raw = await image.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty image upload")
    try:
        room_plan = RoomPlanPayload.model_validate(json.loads(room_plan_json)) if room_plan_json else None
        calibration = (
            SurfaceCalibration.model_validate(json.loads(calibration_json)) if calibration_json else None
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON form field: {exc}") from exc

    extras = []
    for f in (extra_images or [])[:3]:
        data = await f.read()
        if data:
            extras.append((data, f.content_type or "image/jpeg"))

    return await analyze_scene(
        raw,
        mime_type=image.content_type or "image/jpeg",
        room_plan=room_plan,
        calibration=calibration,
        intent_hint=intent_text,
        extra_images=extras,
    )
