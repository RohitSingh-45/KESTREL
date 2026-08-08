"""Provider factory — OpenAI today; pluggable for future providers."""

from __future__ import annotations

from app.config import Settings, get_settings
from app.logging import get_logger
from app.services.llm.base import LLMClient, UnavailableLLMClient
from app.services.llm.openai_client import OpenAIClient

logger = get_logger(__name__)


def build_llm_from_settings(settings: Settings | None = None) -> LLMClient:
    """Build the active LLM client from settings. Never logs secrets."""
    settings = settings or get_settings()
    provider = (settings.llm_provider or "openai").strip().lower()

    if provider != "openai":
        logger.warning(
            "llm_provider_unsupported",
            provider=provider,
            fallback="unavailable",
        )
        return UnavailableLLMClient()

    api_key = settings.openai_api_key
    if not api_key:
        logger.info("llm_client_unavailable", provider=provider, reason="missing_api_key")
        return UnavailableLLMClient()

    logger.info(
        "llm_client_built",
        provider=provider,
        model=settings.openai_model,
        timeout_seconds=settings.openai_timeout_seconds,
        max_retries=settings.openai_max_retries,
    )
    return OpenAIClient(
        api_key=api_key,
        model=settings.openai_model,
        timeout_seconds=settings.openai_timeout_seconds,
        max_retries=settings.openai_max_retries,
    )
