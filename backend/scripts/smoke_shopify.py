#!/usr/bin/env python3
"""Smoke-test Shopify commerce MCP: search_catalog (+ next page) and get_product.

Usage:
    python scripts/smoke_shopify.py "black desk lamp" [--transport shop|catalog|auto] [--max-price 100]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.shopify_mcp import get_product, search_catalog_page


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="?", default="minimalist black desk lamp")
    ap.add_argument("--transport", default=None, choices=["shop", "catalog", "auto"])
    ap.add_argument("--max-price", type=float, default=100)
    ap.add_argument("--limit", type=int, default=3)
    args = ap.parse_args()

    page = await search_catalog_page(
        args.query, max_price_usd=args.max_price, limit=args.limit, transport=args.transport
    )
    print(f"Query: {page.query}  via={page.source}  total={page.total_count}  next={page.has_next_page}")
    for p in page.products:
        rating = f"★{p.rating:.1f}" if p.rating else ""
        print(f"- {p.title} | ${p.price_usd} | {p.vendor or '?'} {rating} | dims={p.dimensions_hint} | {p.product_url}")
        assert p.price_usd is None or p.price_usd <= args.max_price + 0.01, "price filter ignored"

    if page.has_next_page and page.cursor:
        nxt = await search_catalog_page(
            args.query, max_price_usd=args.max_price, limit=args.limit,
            cursor=page.cursor, transport=args.transport,
        )
        print(f"Page 2: {nxt.count} products (first: {nxt.products[0].title if nxt.products else '-'})")

    if page.products:
        detail = await get_product(page.products[0].id, transport=args.transport)
        print(f"get_product: {detail.title} — {len(detail.variants)} variants, {len(detail.image_urls)} images")


if __name__ == "__main__":
    asyncio.run(main())
