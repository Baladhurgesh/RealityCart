"""Shopify commerce MCP client (search_catalog / get_product).

Two transports speak the same UCP catalog schema:

* ``shop``    — Shop MCP (``https://mcp.shop.com/``), ported from ShopMCPDemo.
                Stateless JSON-RPC ``tools/call``; no session, no API key.
* ``catalog`` — Shopify Global Catalog MCP (``catalog.shopify.com/api/ucp/mcp``)
                with a UCP agent profile and optional client-credentials token.

``SHOPIFY_TRANSPORT=auto`` (default) tries ``shop`` first and falls back to
``catalog`` so a single endpoint outage never kills the demo.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import Settings, get_settings
from app.schemas import CatalogPage, ProductOffer, ProductVariant

logger = logging.getLogger(__name__)

CLIENT_INFO = {"name": "realitycart-backend", "version": "0.1.0"}
ZERO_DECIMAL = {"JPY", "KRW", "VND", "CLP", "ISK", "HUF", "TWD", "UGX"}

_token_cache: dict[str, Any] = {"token": None, "expires_at": 0.0}


class CatalogError(RuntimeError):
    """Raised when every configured transport fails."""


# --------------------------------------------------------------------------- auth


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
        token = response.json().get("access_token")
        if not token:
            return None
        _token_cache["token"] = token
        _token_cache["expires_at"] = now + 50 * 60  # tokens last ~60 min
        return token


# ---------------------------------------------------------------------- transport


@dataclass
class _Request:
    url: str
    headers: dict[str, str]
    payload: dict[str, Any] = field(default_factory=dict)


async def _build_shop_request(name: str, arguments: dict[str, Any], settings: Settings) -> _Request:
    version = settings.shop_mcp_protocol_version
    return _Request(
        url=settings.shop_mcp_url,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": version,
            "Mcp-Method": "tools/call",
            "Mcp-Name": name,
        },
        payload={
            "jsonrpc": "2.0",
            "id": random.randint(1, 1_000_000),
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": arguments,
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": version,
                    "io.modelcontextprotocol/clientInfo": CLIENT_INFO,
                    "io.modelcontextprotocol/clientCapabilities": {},
                },
            },
        },
    )


async def _build_catalog_request(name: str, arguments: dict[str, Any], settings: Settings) -> _Request:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    token = await _get_bearer_token(settings)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return _Request(
        url=settings.shopify_mcp_url,
        headers=headers,
        payload={
            "jsonrpc": "2.0",
            "id": random.randint(1, 1_000_000),
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": {
                    "meta": {"ucp-agent": {"profile": settings.shopify_agent_profile}},
                    **arguments,
                },
            },
        },
    )


_BUILDERS = {"shop": _build_shop_request, "catalog": _build_catalog_request}


def _parse_body(response: httpx.Response) -> dict[str, Any]:
    """Accept plain JSON or a single-message SSE stream."""
    ctype = response.headers.get("content-type", "")
    if "text/event-stream" in ctype:
        for line in response.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        raise RuntimeError("Empty SSE response")
    return response.json()


def _extract_structured(envelope: dict[str, Any]) -> dict[str, Any]:
    if envelope.get("error"):
        err = envelope["error"]
        raise RuntimeError(f"RPC error: {err.get('message') or err}")
    result = envelope.get("result") or {}
    content = result.get("content") or []
    if result.get("isError"):
        msg = content[0].get("text") if content else "Unknown tool error"
        raise RuntimeError(f"Tool error: {msg}")

    data = result.get("structuredContent")
    if data is None and content and content[0].get("text"):
        data = json.loads(content[0]["text"])
    if not isinstance(data, dict):
        raise RuntimeError("Response missing structuredContent")

    if (data.get("ucp") or {}).get("status") == "error":
        messages = data.get("messages") or []
        detail = "; ".join(m.get("content", "") for m in messages) or "Catalog error"
        raise RuntimeError(detail)
    return data


async def _post(req: _Request) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=45) as client:
        response = await client.post(req.url, headers=req.headers, json=req.payload)
        if response.status_code == 429:
            retry_after = float(response.headers.get("Retry-After", "2"))
            logger.warning("Commerce MCP rate limited; sleeping %.1fs", retry_after)
            await asyncio.sleep(min(retry_after, 10))
            response = await client.post(req.url, headers=req.headers, json=req.payload)
        if response.is_error:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:200]}")
        return _extract_structured(_parse_body(response))


def _transport_order(settings: Settings, transport: str | None) -> list[str]:
    choice = (transport or settings.shopify_transport).lower()
    if choice == "auto":
        return ["shop", "catalog"]
    if choice not in _BUILDERS:
        raise ValueError(f"Unknown Shopify transport '{choice}' (use shop|catalog|auto)")
    return [choice]


async def call_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    settings: Settings | None = None,
    transport: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Call an MCP tool; returns (structuredContent, transport_used)."""
    settings = settings or get_settings()
    errors: list[str] = []
    for kind in _transport_order(settings, transport):
        try:
            req = await _BUILDERS[kind](name, arguments, settings)
            return await _post(req), kind
        except Exception as exc:  # noqa: BLE001 — try next transport
            logger.warning("%s via %s failed: %s", name, kind, exc)
            errors.append(f"{kind}: {exc}")
    raise CatalogError("; ".join(errors))


# -------------------------------------------------------------------- normalize


def _money_to_float(money: Any) -> float | None:
    """UCP money is ``{amount: <int minor units>, currency}``."""
    if isinstance(money, dict):
        amount = money.get("amount")
        if amount is None:
            return None
        digits = 0 if (money.get("currency") or "").upper() in ZERO_DECIMAL else 2
        try:
            return round(float(amount) / 10**digits, 2)
        except (TypeError, ValueError):
            return None
    if isinstance(money, (int, float)):
        return float(money)
    return None


def _currency(product: dict[str, Any]) -> str:
    pr = product.get("price_range") or {}
    for m in (pr.get("min"), pr.get("max")):
        if isinstance(m, dict) and m.get("currency"):
            return m["currency"]
    return "USD"


def _image_urls(product: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for m in product.get("media") or []:
        if not isinstance(m, dict):
            continue
        u = m.get("url") if m.get("type") == "image" else (m.get("preview") or {}).get("url")
        if u and u not in urls:
            urls.append(u)
    return urls


_MODEL_TYPES = {"model", "3d", "model3d", "model_3d"}


def _model_asset(product: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return (url, format) for the first merchant 3D model. Prefer USDZ over GLB."""
    found: tuple[str, str] | None = None
    for item in product.get("media") or []:
        if not isinstance(item, dict):
            continue
        url = item.get("url") or ""
        if not isinstance(url, str) or not url.startswith("http"):
            continue
        path = url.lower().split("?")[0]
        mime = (item.get("mime_type") or item.get("content_type") or "").lower()
        kind = (item.get("type") or "").lower()
        is_model = kind in _MODEL_TYPES or "model" in mime or path.endswith(
            (".usdz", ".glb", ".gltf", ".reality")
        )
        if not is_model:
            continue
        if path.endswith((".usdz", ".reality")) or "usd" in mime:
            return url, "usdz"
        if found is None:
            found = (url, "glb" if path.endswith((".glb", ".gltf")) or "gltf" in mime else "model")
    return found if found else (None, None)


def _has_3d(product: dict[str, Any]) -> bool:
    url, _ = _model_asset(product)
    return url is not None


def clean_description(raw: Any) -> str | None:
    text = raw.get("plain") if isinstance(raw, dict) else raw
    if not isinstance(text, str) or not text.strip():
        return None
    text = text.replace("<li", "\n• <li")
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    return text.strip()[:2000] or None


_UNIT = r"(?:in(?:ch(?:es)?)?|cm|mm|ft|m|\"|''|')"
_NUM = r"(\d+(?:\.\d+)?)"
# "62.99 in W" or 31.5"W
_LABELED_RE = re.compile(
    rf"{_NUM}\s*({_UNIT})\s*([WwDdHhLl])\b",
    re.IGNORECASE,
)
_TRIPLE_RE = re.compile(
    rf"{_NUM}\s*({_UNIT})?\s*[x×]\s*{_NUM}\s*({_UNIT})?\s*[x×]\s*{_NUM}\s*({_UNIT})",
    re.IGNORECASE,
)
_PAIR_RE = re.compile(
    rf"{_NUM}\s*({_UNIT})?\s*[x×]\s*{_NUM}\s*({_UNIT})",
    re.IGNORECASE,
)
_SINGLE_RE = re.compile(
    rf"{_NUM}\s*({_UNIT})(?![A-Za-z0-9])",
    re.IGNORECASE,
)


def _unit_to_m(value: float, unit: str | None, *, fallback: str | None = None) -> float | None:
    raw = (unit or fallback or "").lower().strip()
    if raw in {'"', "''", "in", "inch", "inches"}:
        meters = value * 0.0254
    elif raw == "cm":
        meters = value * 0.01
    elif raw == "mm":
        meters = value * 0.001
    elif raw in {"ft", "feet", "foot", "'"}:
        meters = value * 0.3048
    elif raw == "m":
        meters = value
    elif value > 3:
        meters = value * 0.0254  # bare furniture numbers are almost always inches
    else:
        meters = value
    if meters < 0.015 or meters > 4.5:
        return None
    return round(meters, 3)


def parse_dimensions_m(
    title: str,
    description: str | None,
) -> tuple[float | None, float | None, float | None, str | None]:
    """Pull width, depth, height in meters from a listing title + description.

    Prefers labeled axes (62.99 in W x 14.96 in D x 23.62 in H), then a
    W x D x H triple, then a pair, then one overall dimension (lamp height
    or furniture width).
    """
    text = f"{title}\n{description or ''}"
    axes: dict[str, float] = {}
    for match in _LABELED_RE.finditer(text):
        meters = _unit_to_m(float(match.group(1)), match.group(2))
        if meters is None:
            continue
        letter = match.group(3).upper()
        key = {"W": "w", "D": "d", "H": "h", "L": "l"}[letter]
        axes.setdefault(key, meters)
    if "l" in axes and "w" not in axes:
        axes["w"] = axes["l"]
    elif "l" in axes and "d" not in axes:
        axes["d"] = axes["w"]
        axes["w"] = axes["l"]
    if sum(k in axes for k in ("w", "d", "h")) >= 2:
        hint = " × ".join(
            f"{axes[k]:.2f} m {k.upper()}" for k in ("w", "d", "h") if k in axes
        )
        return axes.get("w"), axes.get("d"), axes.get("h"), hint

    triple = _TRIPLE_RE.search(text)
    if triple:
        units = [triple.group(i) for i in (2, 4, 6)]
        fallback = next((u for u in units if u), None)
        dims = []
        for num, unit in ((triple.group(1), triple.group(2)), (triple.group(3), triple.group(4)), (triple.group(5), triple.group(6))):
            dims.append(_unit_to_m(float(num), unit, fallback=fallback))
        if all(dims):
            hint = triple.group(0).strip()
            return dims[0], dims[1], dims[2], hint

    pair = _PAIR_RE.search(text)
    if pair:
        fallback = pair.group(4) or pair.group(2)
        w = _unit_to_m(float(pair.group(1)), pair.group(2), fallback=fallback)
        d = _unit_to_m(float(pair.group(3)), pair.group(4), fallback=fallback)
        if w and d:
            return w, d, None, pair.group(0).strip()

    singles = []
    for match in _SINGLE_RE.finditer(title):
        meters = _unit_to_m(float(match.group(1)), match.group(2))
        if meters:
            singles.append(meters)
    if singles:
        overall = max(singles)
        blob = title.lower()
        if any(word in blob for word in ("lamp", "light", "sconce")):
            return None, None, overall, f"{overall:.2f} m H"
        return overall, None, None, f"{overall:.2f} m W"
    return None, None, None, None


def _dimensions_hint(product: dict[str, Any], description: str | None) -> str | None:
    if product.get("dimensions"):
        return str(product["dimensions"])
    _, _, _, hint = parse_dimensions_m(str(product.get("title") or ""), description)
    return hint


def _variant(v: dict[str, Any]) -> ProductVariant:
    opts = " / ".join(o["label"] for o in v.get("options") or [] if isinstance(o, dict) and o.get("label"))
    return ProductVariant(
        id=str(v.get("id") or ""),
        title=opts or v.get("title") or "Default",
        price_usd=_money_to_float(v.get("price")),
        available=(v.get("availability") or {}).get("available"),
        url=v.get("url"),
    )


def normalize_product(product: dict[str, Any]) -> ProductOffer:
    variants = [v for v in product.get("variants") or [] if isinstance(v, dict)]
    v0 = variants[0] if variants else {}
    seller = v0.get("seller") or {}
    pr = product.get("price_range") or {}
    price_min = _money_to_float(pr.get("min")) or _money_to_float(v0.get("price"))
    price_max = _money_to_float(pr.get("max")) or price_min
    rating = product.get("rating") or {}
    images = _image_urls(product)
    model_url, model_format = _model_asset(product)
    description = clean_description(product.get("description"))
    title = str(product.get("title") or "Untitled")
    width_m, depth_m, height_m, parsed_hint = parse_dimensions_m(title, description)
    available = None
    if variants:
        flags = [(v.get("availability") or {}).get("available") for v in variants]
        available = any(f is not False for f in flags)

    return ProductOffer(
        id=str(product.get("id") or "unknown"),
        title=title,
        description=description,
        price_usd=price_min,
        price_max_usd=price_max,
        currency=_currency(product),
        image_url=images[0] if images else None,
        image_urls=images[:8],
        product_url=v0.get("url") or v0.get("checkout_url") or seller.get("url"),
        vendor=seller.get("name"),
        seller_domain=seller.get("domain"),
        variant_id=str(v0["id"]) if v0.get("id") else None,
        rating=rating.get("value"),
        review_count=rating.get("count"),
        available=available,
        has_3d=model_url is not None,
        model_url=model_url,
        model_format=model_format,
        dimensions_hint=parsed_hint or _dimensions_hint(product, description),
        width_m=width_m,
        depth_m=depth_m,
        height_m=height_m,
        variants=[_variant(v) for v in variants[:25]],
        raw=product,
    )


# ------------------------------------------------------------------------- tools


async def search_catalog_page(
    query: str,
    *,
    max_price_usd: float | None = None,
    country: str | None = None,
    currency: str | None = None,
    limit: int = 5,
    cursor: str | None = None,
    like_image_url: str | None = None,
    settings: Settings | None = None,
    transport: str | None = None,
) -> CatalogPage:
    settings = settings or get_settings()
    pagination: dict[str, Any] = {"limit": max(1, min(limit, 50))}
    if cursor:
        pagination["cursor"] = cursor
    catalog: dict[str, Any] = {
        "query": query,
        "context": {
            "address_country": country or settings.shopify_country,
            "currency": currency or settings.shopify_currency,
        },
        "pagination": pagination,
    }
    if max_price_usd is not None and max_price_usd > 0:
        catalog["filters"] = {"price": {"max": int(round(max_price_usd * 100))}}
    if like_image_url:
        catalog["like"] = [{"type": "image", "url": like_image_url}]

    data, used = await call_tool("search_catalog", {"catalog": catalog}, settings=settings, transport=transport)
    products = [normalize_product(p) for p in data.get("products") or [] if isinstance(p, dict)]
    pag = data.get("pagination") or {}
    return CatalogPage(
        query=query,
        count=len(products),
        total_count=pag.get("total_count"),
        has_next_page=bool(pag.get("has_next_page")),
        cursor=pag.get("cursor"),
        source=used,
        products=products,
    )


async def search_catalog(query: str, **kwargs: Any) -> list[ProductOffer]:
    """Convenience wrapper used by the pipeline — products only."""
    return (await search_catalog_page(query, **kwargs)).products


def _minor_display(amount: Any, currency: str) -> str:
    digits = 0 if (currency or "").upper() in ZERO_DECIMAL else 2
    try:
        value = float(amount) / 10**digits
    except (TypeError, ValueError):
        return str(amount)
    if (currency or "").upper() == "USD":
        return f"${value:.{digits}f}"
    return f"{currency} {value:.{digits}f}"


def _link_label(kind: str) -> str:
    return {
        "refund_policy": "Refund policy",
        "terms_of_service": "Terms of service",
        "privacy_policy": "Privacy policy",
    }.get(kind, kind.replace("_", " ").title())


async def create_shop_checkout(
    *,
    shop: str,
    variant_id: str,
    quantity: int = 1,
    country: str | None = None,
) -> dict[str, Any]:
    """Open a Shop MCP checkout. Does not place or pay for the order."""
    settings = get_settings()
    data, _ = await call_tool(
        "create_checkout",
        {
            "shop": shop,
            "checkout": {
                "line_items": [
                    {"item": {"id": variant_id}, "quantity": max(1, quantity)}
                ],
                "context": {"address_country": country or settings.shopify_country},
            },
        },
        transport="shop",
    )
    currency = data.get("currency") or "USD"
    totals = []
    for entry in data.get("totals") or []:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("type") or "total")
        totals.append(
            {
                "type": kind,
                "label": str(entry.get("display_text") or kind.replace("_", " ").title()),
                "amount_display": _minor_display(entry.get("amount"), currency),
            }
        )
    messages = []
    for message in data.get("messages") or []:
        if isinstance(message, dict) and message.get("content"):
            messages.append(str(message["content"]))
        elif isinstance(message, str):
            messages.append(message)
    links = []
    for link in data.get("links") or []:
        if isinstance(link, dict) and link.get("url"):
            links.append(
                {
                    "type": _link_label(str(link.get("type") or "link")),
                    "url": link["url"],
                }
            )
    logger.info("checkout created status=%s shop=%s", data.get("status"), shop)
    return {
        "id": str(data.get("id") or ""),
        "status": str(data.get("status") or "incomplete"),
        "currency": currency,
        "shop": shop,
        "continue_url": data.get("continue_url"),
        "totals": totals,
        "messages": messages,
        "links": links,
    }


async def get_product(
    product_id: str,
    *,
    country: str | None = None,
    currency: str | None = None,
    settings: Settings | None = None,
    transport: str | None = None,
) -> ProductOffer:
    settings = settings or get_settings()
    catalog = {
        "id": product_id,
        "context": {
            "address_country": country or settings.shopify_country,
            "currency": currency or settings.shopify_currency,
        },
    }
    data, _ = await call_tool("get_product", {"catalog": catalog}, settings=settings, transport=transport)
    product = data.get("product")
    if not isinstance(product, dict):
        products = data.get("products") or []
        product = products[0] if products and isinstance(products[0], dict) else None
    if not isinstance(product, dict):
        raise CatalogError(f"get_product returned no product for {product_id}")
    return normalize_product(product)
