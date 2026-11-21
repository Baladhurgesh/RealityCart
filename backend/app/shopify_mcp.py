"""Shopify Global Catalog MCP client (search_catalog / get_product)."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.config import Settings, get_settings
from app.schemas import ProductOffer

logger = logging.getLogger(__name__)

_token_cache: dict[str, Any] = {"token": None, "expires_at": 0.0}


async def _get_bearer_token(settings: Settings) -> str | None:
    if not settings.shopify_client_id or not settings.shopify_client_secret:
        return None

    now = time.time()
    if _token_cache["token"] and _token_cache["expires_at"] > now + 60:
        return _token_cache["token"]

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            "https://api.shopify.com/auth/access_token",
            json={
                "client_id": settings.shopify_client_id,
                "client_secret": settings.shopify_client_secret,
                "grant_type": "client_credentials",
            },
        )
        response.raise_for_status()
        data = response.json()
        token = data.get("access_token")
        if not token:
            return None
        # Tokens last ~60 minutes; refresh early.
        _token_cache["token"] = token
        _token_cache["expires_at"] = now + 50 * 60
        return token


def _minor_to_usd(value: float) -> float:
    """Catalog prices are typically integer minor units (cents)."""
    # Whole-dollar floats under ~10 stay as-is; integer cents are usually >= 100.
    if value >= 100 and abs(value - round(value)) < 1e-6:
        return value / 100.0
    return value


def _price_to_usd(product: dict[str, Any]) -> float | None:
    """Best-effort price extraction from UCP catalog product shapes."""
    price_range = product.get("price_range") or product.get("priceRange")
    if isinstance(price_range, dict):
        for key in ("min", "min_value", "lowest", "from", "min_amount"):
            raw = price_range.get(key)
            if isinstance(raw, dict):
                raw = raw.get("amount") or raw.get("value")
            if raw is not None:
                try:
                    return _minor_to_usd(float(raw))
                except (TypeError, ValueError):
                    pass

    variants = product.get("variants") or []
    if variants:
        v0 = variants[0]
        price = v0.get("price")
        if isinstance(price, dict):
            amount = price.get("amount") or price.get("value")
            if amount is not None:
                try:
                    return _minor_to_usd(float(amount))
                except (TypeError, ValueError):
                    pass
        elif price is not None:
            try:
                return _minor_to_usd(float(price))
            except (TypeError, ValueError):
                pass
    return None


def _image_url(product: dict[str, Any]) -> str | None:
    media = product.get("media") or product.get("images") or []
    if isinstance(media, list) and media:
        first = media[0]
        if isinstance(first, str):
            return first
        if isinstance(first, dict):
            return (
                first.get("url")
                or first.get("src")
                or first.get("uri")
                or (first.get("image") or {}).get("url")
            )
    return product.get("image_url") or product.get("featured_image")


def _product_url(product: dict[str, Any]) -> str | None:
    for key in ("url", "product_url", "checkout_url", "online_store_url"):
        if product.get(key):
            return str(product[key])
    variants = product.get("variants") or []
    if variants:
        v0 = variants[0]
        if v0.get("url"):
            return str(v0["url"])
        seller = v0.get("seller") or {}
        if isinstance(seller, dict):
            for key in ("checkout_url", "url", "store_url"):
                if seller.get(key):
                    return str(seller[key])
    return None


def _has_3d(product: dict[str, Any]) -> bool:
    media = product.get("media") or []
    for item in media:
        if not isinstance(item, dict):
            continue
        mime = (item.get("mime_type") or item.get("content_type") or "").lower()
        url = (item.get("url") or "").lower()
        if "model" in mime or url.endswith(".glb") or url.endswith(".usdz"):
            return True
        if item.get("type") in ("model", "3d", "Model3d"):
            return True
    return bool(product.get("has_3d"))


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("plain", "text", "html", "content"):
            if isinstance(value.get(key), str):
                return value[key]
        return str(value)
    return str(value)


def normalize_product(product: dict[str, Any]) -> ProductOffer:
    vendor = product.get("vendor") or product.get("brand")
    if isinstance(vendor, dict):
        vendor = vendor.get("name") or vendor.get("title")
    return ProductOffer(
        id=str(product.get("id") or product.get("upid") or "unknown"),
        title=str(product.get("title") or product.get("name") or "Untitled"),
        description=_as_text(product.get("description")),
        price_usd=_price_to_usd(product),
        currency="USD",
        image_url=_image_url(product),
        product_url=_product_url(product),
        vendor=_as_text(vendor),
        has_3d=_has_3d(product),
        dimensions_hint=_as_text(product.get("dimensions")),
        raw=product,
    )


async def _call_tool(
    name: str,
    catalog: dict[str, Any],
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    token = await _get_bearer_token(settings)
    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": name,
            "arguments": {
                "meta": {
                    "ucp-agent": {
                        "profile": settings.shopify_agent_profile,
                    }
                },
                "catalog": catalog,
            },
        },
    }

    async with httpx.AsyncClient(timeout=45) as client:
        response = await client.post(
            settings.shopify_mcp_url,
            headers=headers,
            json=payload,
        )
        if response.status_code == 429:
            retry_after = float(response.headers.get("Retry-After", "2"))
            logger.warning("Shopify rate limited; sleeping %.1fs", retry_after)
            time.sleep(min(retry_after, 10))
            response = await client.post(
                settings.shopify_mcp_url,
                headers=headers,
                json=payload,
            )
        response.raise_for_status()
        body = response.json()

    if "error" in body:
        raise RuntimeError(body["error"].get("message") or str(body["error"]))

    data = (body.get("result") or {}).get("structuredContent")
    if not data:
        raise RuntimeError("Catalog response missing structuredContent")
    if data.get("ucp", {}).get("status") == "error":
        messages = data.get("messages") or []
        detail = "; ".join(m.get("content", "") for m in messages) or "Catalog error"
        raise RuntimeError(detail)
    return data


async def search_catalog(
    query: str,
    *,
    max_price_usd: float | None = None,
    country: str = "US",
    currency: str = "USD",
    limit: int = 5,
    like_image_url: str | None = None,
    settings: Settings | None = None,
) -> list[ProductOffer]:
    catalog: dict[str, Any] = {
        "query": query,
        "context": {"address_country": country, "currency": currency},
        "pagination": {"limit": min(limit, 50)},
    }
    if max_price_usd is not None:
        catalog["filters"] = {"price": {"max": int(max_price_usd * 100)}}
    if like_image_url:
        catalog["like"] = [{"type": "image", "url": like_image_url}]

    data = await _call_tool("search_catalog", catalog, settings)
    products = data.get("products") or []
    return [normalize_product(p) for p in products if isinstance(p, dict)]


async def get_product(
    product_id: str,
    *,
    country: str = "US",
    currency: str = "USD",
    settings: Settings | None = None,
) -> ProductOffer:
    catalog = {
        "id": product_id,
        "context": {"address_country": country, "currency": currency},
    }
    data = await _call_tool("get_product", catalog, settings)
    product = data.get("product")
    if not isinstance(product, dict):
        raise RuntimeError(f"get_product returned no product for {product_id}")
    return normalize_product(product)
