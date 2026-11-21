"""RealityCart FastAPI entrypoint."""

from __future__ import annotations

import json
import logging
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.pipeline import run_redesign
from app.schemas import HealthResponse, RedesignResponse, RoomPlanPayload, SurfaceCalibration
from app.shopify_mcp import search_catalog

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
        models={
            "scene": settings.scene_model,
            "plan": settings.plan_model,
            "decision": settings.decision_model,
        },
    )


@app.get("/v1/shopify/search")
async def shopify_search(
    q: str,
    max_price: float | None = None,
    limit: int = 5,
):
    """Smoke-test endpoint for Catalog MCP."""
    try:
        products = await search_catalog(q, max_price_usd=max_price, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"query": q, "count": len(products), "products": products}


@app.post("/v1/redesign", response_model=RedesignResponse)
async def redesign(
    image: Annotated[UploadFile, File(description="Room or desk photo")],
    intent_text: Annotated[str, Form()] = "Minimalist developer desk setup",
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


@app.post("/v1/scene", response_model=RedesignResponse)
async def scene_only(
    image: Annotated[UploadFile, File()],
    intent_text: Annotated[str, Form()] = "",
    budget_usd: Annotated[float, Form()] = 600.0,
    room_plan_json: Annotated[str | None, Form()] = None,
    calibration_json: Annotated[str | None, Form()] = None,
) -> RedesignResponse:
    """Debug helper: run full pipeline (same as redesign)."""
    return await redesign(
        image=image,
        intent_text=intent_text or "Analyze this workspace",
        budget_usd=budget_usd,
        room_plan_json=room_plan_json,
        calibration_json=calibration_json,
    )
