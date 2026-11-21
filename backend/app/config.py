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
    plan_model: str = "gemini-4-carbon"
    decision_model: str = "gemini-decision-1"
    meshy_api_key: str = ""
    # meshy-6-lite is the fast textured preview used for the phone overlay.
    meshy_model: str = "meshy-6-lite"

    decision_fit_threshold: float = 0.55
    decision_style_threshold: float = 0.50
    decision_budget_threshold: float = 0.60


@lru_cache
def get_settings() -> Settings:
    return Settings()
