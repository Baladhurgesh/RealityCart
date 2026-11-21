# RealityCart demo script (≈3 minutes)

## Setup (before judges)

1. `cd backend && source .venv/bin/activate && cp -n .env.example .env` — set `GEMINI_API_KEY`
2. `python -m app` — confirm `GET /health` shows `gemini_configured: true`
3. Open `ios/RealityCart/RealityCart.xcodeproj`, set Team, run on device/simulator
4. Settings → API URL (`127.0.0.1` for simulator, Mac LAN IP for device)
5. Have a real messy desk photo ready (or RoomPlan on LiDAR iPhone)

## Live beat

1. **Capture** — show photo or LiDAR RoomPlan; set desk width in meters.
2. **Intent** (voice or paste):  
   *Make this a minimalist developer setup for $600. Keep my existing monitor, add a lamp and storage, and make everything black.*
3. **Plan** — walk Hydrogen scene → Carbon slots → Shopify products → Decision reasons → budget meter.
4. **3D** — proxies on calibrated plane; call out “approximate 3D, real commerce.”
5. **Break story** — open a stress case (clutter or tight budget) and show a Decision rejection / re-plan.

## Closing line

> Media in, meters from RoomPlan or user width, two Gemini 4 models with clear jobs, Decisions API as the gate, and Shopify Catalog MCP for real products you can buy.
