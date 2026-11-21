from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    gemini_api_key: str = ""
    shopify_client_id: str = ""
    shopify_client_secret: str = ""
    shopify_agent_profile: str = (
        "https://shopify.dev/ucp/agent-profiles/examples/2026-08-25/"
        "valid-with-capabilities.json"
    )
    shopify_mcp_url: str = "https://catalog.shopify.com/api/ucp/mcp"
    # Shop MCP (from ShopMCPDemo): stateless, keyless JSON-RPC.
    shop_mcp_url: str = "https://mcp.shop.com/"
    shop_mcp_protocol_version: str = "2026-07-28"
    # shop | catalog | auto (shop first, catalog fallback)
    shopify_transport: str = "auto"
    shopify_country: str = "US"
    shopify_currency: str = "USD"
    cors_origins: str = "*"
    host: str = "0.0.0.0"
    port: int = 8000

    # Model IDs
    scene_model: str = "gemini-4-hydrogen"
    plan_model: str = "gemini-4-neon"
    decision_model: str = "gemini-decision-1"
    meshy_api_key: str = ""
    # meshy-6-lite is the fast textured preview used for the phone overlay.
    meshy_model: str = "meshy-6-lite"

    # Carbon planner
    plan_max_slots: int = 3  # each slot = 1 Shopify search + Decisions calls
    plan_thinking_level: str = "low"
    # Per-call model timeouts (s); a hung call is cancelled and retried once.
    scene_timeout_s: float = 45.0
    plan_timeout_s: float = 40.0

    # Decisions gate
    decision_timeout_s: float = 12.0
    decision_candidates_per_slot: int = 3
    decision_size_tolerance: float = 1.15  # product may exceed slot max by 15 % before precheck rejects
    decision_use_product_image: bool = True
    decision_use_room_image: bool = True
    decision_room_image_side: int = 768
    redesign_replan_rounds: int = 1  # Carbon re-plans *only* unfilled slots; 0 disables

    decision_fit_threshold: float = 0.55
    decision_style_threshold: float = 0.50
    decision_budget_threshold: float = 0.60


@lru_cache
def get_settings() -> Settings:
    return Settings()
