# RealityCart

**Element 119 hackathon project** — turn a real desk/room capture into a budgeted, shoppable redesign using Gemini 4, the Decisions API, and Shopify Global Catalog MCP.

> “Make this a minimalist developer setup for $600. Keep my monitor, add a lamp and storage, all black.”

## Architecture

| Layer | Stack |
| --- | --- |
| iOS client | SwiftUI + RoomPlan (LiDAR) + RealityKit proxies + Speech |
| Backend | FastAPI |
| Scene parse | `gemini-4-hydrogen` |
| Design plan | `gemini-4-carbon` |
| Gates | `gemini-decision-1` (Decisions API) |
| Commerce | Shopify Catalog MCP (`search_catalog`, `get_product`) |

```
iOS capture (photo / RoomPlan)
        │
        ▼
FastAPI  POST /v1/redesign
        │
        ├─ Hydrogen → SceneAnalysis
        ├─ Carbon   → DesignPlan (slots + queries)
        ├─ Shopify  → real products
        └─ Decisions→ fit / style / budget
        │
        ▼
iOS Plan + 3D preview
```

## Model jobs

| Model | Job |
| --- | --- |
| `gemini-4-hydrogen` | Multimodal scene parse from photo + optional RoomPlan JSON |
| `gemini-4-carbon` | Shopping plan, placement slots, re-plan after Decision failures |
| `gemini-decision-1` | Binary gates: fits surface? style match? within budget? |
| Shopify Catalog MCP | Cross-merchant product discovery with live prices/links |

## Quick start

### 1. Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env and set GEMINI_API_KEY
python -m app
```

API: `http://127.0.0.1:8000`  
Docs: `http://127.0.0.1:8000/docs`  
Health: `GET /health`  
Shopify smoke: `python scripts/smoke_shopify.py "black desk lamp"`

### 2. iOS app

```bash
cd ios/RealityCart
open RealityCart.xcodeproj
```

1. Select your Team under Signing.
2. Run on a simulator (photo + width) or LiDAR device (RoomPlan).
3. Settings → set API base URL:
   - Simulator: `http://127.0.0.1:8000`
   - Physical device: `http://<your-mac-lan-ip>:8000`

### 3. Demo flow

1. **Capture** — camera/library photo, or RoomPlan scan; set desk width/depth.
2. **Intent** — text or voice brief + budget.
3. **Plan** — pipeline steps, scene objects (KEEP badges), Shopify products, budget meter.
4. **3D** — RealityKit category proxies on the calibrated desk plane.

## API

`POST /v1/redesign` multipart:

- `image` (required) — JPEG/PNG
- `intent_text` — shopping brief
- `budget_usd` — e.g. `600`
- `room_plan_json` — optional CapturedRoom-derived JSON
- `calibration_json` — optional `{ "width_m": 1.2, "depth_m": 0.6, "height_m": 0.74 }`

`GET /v1/shopify/search?q=desk+lamp&max_price=80`

## Element 119 stress

See [eval/stress_cases.md](eval/stress_cases.md). Drop real captures into `eval/captures/`.

## Repo layout

```
RealityCart/
  backend/           FastAPI + Gemini + Shopify MCP
  ios/RealityCart/   Xcode SwiftUI app
  assets/models/     Notes for optional GLB/USDZ
  eval/              Stress cases + captures
```

## Non-goals (MVP)

- Depth Anything 3 / SAM 2
- Production Shopify agent profile / checkout cart
- Photoreal occlusion / continuous multi-object live AR
- Next.js twin UI

## License

Hackathon prototype — use at your own risk.
