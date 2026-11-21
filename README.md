# RealityCart

**Element 119 hackathon project** — scan any indoor space with RoomPlan LiDAR and turn it into a budgeted, shoppable redesign. Gemini 4 reads the room, Neon picks what to buy, Shopify finds real products, and the phone shows them in 3D.

> “Make this living room feel minimalist for $600. Keep my sofa, add a floor lamp and storage, mostly black.”

## Architecture

**Interactive diagram:** [docs/architecture/index.html](docs/architecture/index.html) replays one real run (26.8 s, 3 of 3 items, $131.21 of $600) and shows the code check behind each AI step. Open it in a browser, or via GitHub Pages once enabled (`/docs` on `main`).

| Layer | Stack |
| --- | --- |
| iOS client | SwiftUI + RoomPlan LiDAR + RealityKit / SceneKit + Speech |
| Backend | FastAPI |
| Scene parse | `gemini-4-hydrogen` (semantics) + RoomPlan measurements |
| Design plan | `gemini-4-neon` (at most 3 products) |
| Gates | `gemini-decision-1`, plus price and size checks in code |
| Commerce | Shop MCP (`mcp.shop.com`) with Shopify Catalog MCP fallback |
| Product 3D | Merchant USDZ when the listing has one; otherwise a size box, then Meshy (`meshy-6-lite`) |

```
iOS capture (photo + RoomPlan)
        │
        ▼
FastAPI  POST /v1/redesign
        │
        ├─ Hydrogen + LiDAR → SceneAnalysis
        ├─ Neon             → DesignPlan (up to 3 slots)
        ├─ Shopify          → real products
        └─ Decisions        → fit / style / budget
        │
        ▼
iOS Plan (cart + history) and 3D preview
        │
        └─ Meshy jobs run in the background and replace size boxes when ready
```

## Model jobs

| Model | Job |
| --- | --- |
| `gemini-4-hydrogen` | What the room is: type, style, objects LiDAR misses, open places to put things |
| RoomPlan | Measured sizes and surface heights. These numbers are not taken from the model |
| `gemini-4-neon` | Shopping plan, search queries, and a re-plan of slots that stayed empty. Thinking level stays `low` |
| `gemini-decision-1` | Fit and style, using the product photo and a smaller room photo. Price and obvious size mismatches are rejected in code first |
| Shopify Catalog / Shop MCP | Live products, prices, variant ids, and Shop checkout |
| Meshy `meshy-6-lite` | Textured USDZ from product photos when the merchant has no 3D file. One job per product, in parallel |

## Quick start

### 1. Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Set GEMINI_API_KEY. Set MESHY_API_KEY to generate product meshes.
python -m app
```

The server listens on `0.0.0.0:8000` so a phone on the same network can reach it.

- API: `http://127.0.0.1:8000`
- Docs: `http://127.0.0.1:8000/docs`
- Health: `GET /health` (reports the scene, plan, and decision model ids)
- Shopify smoke: `python scripts/smoke_shopify.py "black desk lamp" --transport auto`
- Catalog explorer: `pip install -r requirements-dev.txt && streamlit run tools/catalog_explorer.py`

`GEMINI_API_KEY` needs the paid Gemini tier. `gemini-4-neon` is not on the free tier.

### 2. iOS app

```bash
cd ios/RealityCart
open RealityCart.xcodeproj
```

1. Select your Team under Signing.
2. Run on a **LiDAR** iPhone or iPad Pro. RoomPlan is required.
3. Settings → set the API base URL to `http://<your-mac-lan-ip>:8000`.

The shared Run scheme builds **Release** and does not attach the debugger, so launch stays closer to a home-screen open. Switch that scheme’s Run configuration back to Debug when you need breakpoints.

### 3. App flow

1. **Capture** — RoomPlan scan. The clock opens scans and designs saved on the phone.
2. **Intent** — text or voice brief, plus a budget (presets or the slider).
3. **Plan** — up to three products, a budget meter, and **Add to cart**. The cart badge keeps its count across tabs and after the app is killed. Opening an older design does not clear the cart. **Pay cart** opens Shop’s payment page (one page per store). **3D ready** appears when that product’s mesh is available.
4. **3D** — photo overlay, or live camera. Products sit on the detected floor. Drag to move, twist with two fingers to rotate. A merchant USDZ is used when the listing has one; otherwise a box at the listing size, replaced by the Meshy mesh when it finishes.

## API

`POST /v1/redesign` multipart:

- `image` (required) — JPEG/PNG
- `intent_text` — shopping brief
- `budget_usd` — e.g. `600`
- `room_plan_json` — CapturedRoom export. The iOS app always sends this
- `calibration_json` — optional `{ "width_m": 1.2, "depth_m": 0.6, "height_m": 0.74 }`

`POST /v1/scene` and `POST /v1/plan` run those steps on their own.

`GET /v1/shopify/search?q=desk+lamp&max_price=80&limit=20[&cursor=…&transport=shop|catalog|auto]`
→ `CatalogPage { query, count, total_count, has_next_page, cursor, source, products[] }`

`GET /v1/shopify/products/{id}` → full `ProductOffer` via `get_product`

`POST /v1/checkout/cart` — body `{ "items": [{ "shop", "variant_id", "quantity" }] }`. Returns one Shop checkout per merchant, including `continue_url`. The app opens that URL. It does not call `complete_checkout`.

`POST /v1/models` — body `{ "product_id", "image_urls" }`. Starts or reuses a Meshy job.  
`GET /v1/models/{key}` — status.  
`GET /v1/models/{key}/file` — USDZ when the job is ready. Cached under `backend/data/meshy/` (gitignored).

### Commerce MCP transports

The client in `backend/app/shopify_mcp.py` uses Shop MCP first and Catalog MCP as a fallback:

| Transport | Endpoint | Auth | Notes |
| --- | --- | --- | --- |
| `shop` | `https://mcp.shop.com/` | none | Stateless JSON-RPC `tools/call` |
| `catalog` | `https://catalog.shopify.com/api/ucp/mcp` | UCP agent profile (+ optional client creds) | Global Catalog endpoint |
| `auto` (default) | shop → catalog | — | Falls back if one endpoint errors; `source` says which answered |

Prices arrive as integer minor units and are stored as `price_usd`.

## On the phone

Saved designs live in the app’s Documents folder (`DesignHistory/`): the photo, the plan, the RoomPlan JSON, and a copy of the scan USDZ. The list keeps the newest 40. The cart is stored in UserDefaults.

## Element 119 stress

See [eval/stress_cases.md](eval/stress_cases.md). Drop real captures into `eval/captures/`.

## Repo layout

```
RealityCart/
  backend/           FastAPI + Gemini + Shopify MCP + Meshy
  ios/RealityCart/   Xcode SwiftUI app
  assets/models/     Notes for optional GLB/USDZ stand-ins
  eval/              Stress cases + captures
```

## Non-goals

- Depth Anything 3 / SAM 2
- Charging a card inside the app (`complete_checkout` is never called)
- Photoreal occlusion
- Next.js twin UI

## License

Hackathon prototype — use at your own risk.
