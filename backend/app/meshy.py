"""Turn Shopify product photos into a USDZ with Meshy, cached on disk.

The redesign response does not wait for this. The phone keeps the dimension
box and swaps in the file when GET /v1/models/{key} reports ready.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path

import httpx
from pydantic import BaseModel

from app.config import get_settings

logger = logging.getLogger(__name__)

API = "https://api.meshy.ai/openapi/v1/multi-image-to-3d"
CACHE = Path(__file__).resolve().parents[1] / "data" / "meshy"

_tasks: dict[str, asyncio.Task] = {}
_state: dict[str, dict] = {}
_lock = asyncio.Lock()


class ModelJob(BaseModel):
    product_id: str
    key: str
    status: str  # pending | ready | failed
    progress: int = 0
    error: str | None = None


def model_key(product_id: str) -> str:
    return hashlib.sha256(product_id.encode()).hexdigest()[:32]


def usdz_path(key: str) -> Path:
    return CACHE / key / "model.usdz"


def _photos(urls: list[str]) -> list[str]:
    chosen: list[str] = []
    for url in urls:
        if not url or not url.startswith("http"):
            continue
        path = url.lower().split("?")[0]
        if not path.endswith((".jpg", ".jpeg", ".png", ".webp")):
            continue
        if url not in chosen:
            chosen.append(url)
        if len(chosen) == 4:
            break
    return chosen


def _job_from_state(key: str) -> ModelJob | None:
    if usdz_path(key).is_file():
        product_id = (_state.get(key) or {}).get("product_id") or key
        return ModelJob(product_id=product_id, key=key, status="ready", progress=100)
    state = _state.get(key)
    if not state:
        return None
    return ModelJob(
        product_id=state["product_id"],
        key=key,
        status=state["status"],
        progress=int(state.get("progress") or 0),
        error=state.get("error"),
    )


async def ensure_model(product_id: str, image_urls: list[str]) -> ModelJob:
    """Start a generation if this product is not already cached or in flight."""
    key = model_key(product_id)
    cached = _job_from_state(key)
    if cached and cached.status in ("ready", "pending"):
        return cached

    settings = get_settings()
    if not settings.meshy_api_key:
        return ModelJob(
            product_id=product_id,
            key=key,
            status="failed",
            error="Meshy API key is not configured",
        )
    photos = _photos(image_urls)
    if not photos:
        return ModelJob(
            product_id=product_id,
            key=key,
            status="failed",
            error="No product photos",
        )

    async with _lock:
        cached = _job_from_state(key)
        if cached and cached.status in ("ready", "pending"):
            return cached
        _state[key] = {
            "product_id": product_id,
            "status": "pending",
            "progress": 0,
            "error": None,
        }
        _tasks[key] = asyncio.create_task(_generate(key, product_id, photos))
    return ModelJob(product_id=product_id, key=key, status="pending", progress=0)


def get_model(key: str) -> ModelJob | None:
    return _job_from_state(key)


async def _generate(key: str, product_id: str, photos: list[str]) -> None:
    settings = get_settings()
    headers = {
        "Authorization": f"Bearer {settings.meshy_api_key}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            created = await client.post(
                API,
                headers=headers,
                json={
                    "image_urls": photos,
                    "ai_model": settings.meshy_model,
                    "should_texture": True,
                    "target_formats": ["usdz", "glb"],
                },
            )
            created.raise_for_status()
            task_id = created.json()["result"]
            logger.info("meshy %s started task=%s photos=%d", key[:8], task_id, len(photos))

            loop = asyncio.get_running_loop()
            deadline = loop.time() + 8 * 60
            while loop.time() < deadline:
                await asyncio.sleep(5)
                poll = await client.get(f"{API}/{task_id}", headers=headers)
                poll.raise_for_status()
                data = poll.json()
                status = data.get("status")
                _state[key]["progress"] = int(data.get("progress") or 0)
                logger.info("meshy %s %s %s%%", key[:8], status, _state[key]["progress"])
                if status == "SUCCEEDED":
                    usdz = (data.get("model_urls") or {}).get("usdz")
                    if not usdz:
                        raise RuntimeError("Meshy finished without a USDZ")
                    download = await client.get(usdz, timeout=120)
                    download.raise_for_status()
                    path = usdz_path(key)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(download.content)
                    _state[key]["status"] = "ready"
                    _state[key]["progress"] = 100
                    logger.info("meshy %s ready %d bytes", key[:8], path.stat().st_size)
                    return
                if status in ("FAILED", "CANCELED"):
                    raise RuntimeError(str(data.get("task_error") or status))
            raise RuntimeError("Meshy timed out")
    except Exception as exc:
        logger.exception("meshy %s failed", key[:8])
        _state[key]["status"] = "failed"
        _state[key]["error"] = str(exc)[:300]
