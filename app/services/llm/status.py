"""Truthful LLM connectivity status — never logs credentials."""

from __future__ import annotations

from typing import Literal

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    RateLimitError,
)

from app.logging import get_logger
from app.services.llm.base import LLMClient
from app.services.reliability.secrets import sanitize_error

logger = get_logger(__name__)

LLMStatus = Literal[
    "ready",
    "unavailable",
    "authentication_failed",
    "unstable",
]

# Human labels for dashboard (Module 21).
LLM_STATUS_LABELS: dict[str, str] = {
    "ready": "LLM READY",
    "unavailable": "LLM UNAVAILABLE",
    "authentication_failed": "LLM AUTHENTICATION FAILED",
    "unstable": "LLM UNSTABLE",
}


def classify_llm_exception(exc: BaseException) -> LLMStatus:
    """Map provider errors to operator-facing status (no secret leakage)."""
    if isinstance(exc, AuthenticationError):
        return "authentication_failed"
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and status in {401, 403}:
        return "authentication_failed"
    name = type(exc).__name__.lower()
    msg = sanitize_error(exc).lower()
    if "auth" in name or "invalid api key" in msg or "incorrect api key" in msg:
        return "authentication_failed"
    if isinstance(exc, RateLimitError) or status == 429:
        return "unstable"
    if isinstance(exc, (APITimeoutError, APIConnectionError, TimeoutError)):
        return "unstable"
    if isinstance(exc, APIStatusError) and isinstance(status, int) and status >= 500:
        return "unstable"
    if "timeout" in name or "connection" in name or "429" in msg:
        return "unstable"
    return "unavailable"


async def probe_llm(client: LLMClient) -> LLMStatus:
    """One minimal real request. Never fabricates success."""
    if not getattr(client, "available", False):
        return "unavailable"
    try:
        text = await client.chat(
            system_prompt="You are a connectivity probe for Kestrel.",
            user_prompt="Reply with exactly the word: ok",
            temperature=0.0,
        )
    except Exception as exc:
        status = classify_llm_exception(exc)
        logger.info(
            "llm_probe_failed",
            status=status,
            error=sanitize_error(exc),
        )
        return status
    if not (text or "").strip():
        return "unavailable"
    logger.info("llm_probe_ok", status="ready")
    return "ready"
