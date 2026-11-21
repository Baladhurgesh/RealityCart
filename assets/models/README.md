# 3D proxy models

The iOS MVP builds **procedural RealityKit proxies** (lamp, plant, organizer, shelf, monitor stand, cable tray) so the demo works without binary GLB assets.

To swap in real GLB/USDZ files later:

1. Drop files here and into `ios/RealityCart/RealityCart/Resources/Models/`
2. Load with `Entity.loadModel(named:)` or `model-viewer`
3. Prefer Shopify Catalog 3D media when `has_3d` is true on a product

Approximate proxies are labeled in the Plan and Preview tabs.
