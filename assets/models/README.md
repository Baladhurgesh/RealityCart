# 3D proxy models

The iOS MVP builds **procedural category stand-ins** (lamp, sideboard, tray, organizer, plant, shelf) — not the exact Shopify product mesh — so the demo works without binary GLB/USDZ assets. Proxy choice is inferred from slot category + product title.

To swap in real GLB/USDZ files later:

1. Drop files here and into `ios/RealityCart/RealityCart/Resources/Models/`
2. Load with `Entity.loadModel(named:)` or `model-viewer`
3. Prefer Shopify Catalog 3D media when `has_3d` is true on a product

Approximate proxies are labeled in the Plan and Preview tabs.
