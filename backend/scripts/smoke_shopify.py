#!/usr/bin/env python3
"""Smoke-test Shopify Catalog MCP search_catalog."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.shopify_mcp import search_catalog


async def main() -> None:
    query = sys.argv[1] if len(sys.argv) > 1 else "minimalist black desk lamp"
    products = await search_catalog(query, max_price_usd=100, limit=3)
    print(f"Query: {query}")
    print(f"Results: {len(products)}")
    for p in products:
        print(f"- {p.title} | ${p.price_usd} | {p.product_url or p.id}")


if __name__ == "__main__":
    asyncio.run(main())
