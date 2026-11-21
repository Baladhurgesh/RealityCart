"""Shared Google GenAI client helpers."""

from __future__ import annotations

from functools import lru_cache

from google import genai

from app.config import get_settings


@lru_cache
def get_genai_client() -> genai.Client:
    settings = get_settings()
    if not settings.gemini_api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Copy backend/.env.example to backend/.env"
        )
    return genai.Client(
        api_key=settings.gemini_api_key,
        http_options={"api_version": "v1beta"},
    )


@lru_cache
def get_decision_client() -> genai.Client:
    """Decisions API requires v1beta."""
    return get_genai_client()
