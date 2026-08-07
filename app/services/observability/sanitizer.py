"""Sanitize observability payloads — never persist secrets, prompts, or raw LLM text."""

from __future__ import annotations

from typing import Any

from app.services.reliability.secrets import REDACTED, redact_mapping, sanitize_text

_FORBIDDEN_KEY_FRAGMENTS = (
    "prompt",
    "system_prompt",
    "user_prompt",
    "raw_llm",
    "llm_response",
    "completion_text",
    "chat_messages",
    "openai_api_key",
    "authorization",
    "access_token",
    "password",
    "dotenv",
)


def is_forbidden_key(key: str) -> bool:
    lowered = key.lower()
    return any(fragment in lowered for fragment in _FORBIDDEN_KEY_FRAGMENTS)


def sanitize_value(
    value: Any,
    *,
    max_str: int = 300,
    max_list: int = 100,
) -> Any:
    if isinstance(value, str):
        return sanitize_text(value)[:max_str]
    if isinstance(value, dict):
        return sanitize_mapping(value, max_str=max_str, max_list=max_list)
    if isinstance(value, list):
        return [
            sanitize_value(item, max_str=max_str, max_list=max_list)
            for item in value[:max_list]
        ]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return sanitize_text(str(value))[:max_str]


def sanitize_mapping(
    data: dict[str, Any],
    *,
    max_str: int = 300,
    max_list: int = 100,
) -> dict[str, Any]:
    """Redact secrets and drop prompt / raw-LLM field contents."""
    redacted = redact_mapping(data)
    out: dict[str, Any] = {}
    for key, value in list(redacted.items())[:80]:
        k = str(key)[:64]
        if is_forbidden_key(k):
            out[k] = REDACTED
            continue
        out[k] = sanitize_value(value, max_str=max_str, max_list=max_list)
    return out
