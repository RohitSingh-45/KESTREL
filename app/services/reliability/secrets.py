"""Secret redaction helpers for logs and exception messages."""

from __future__ import annotations

import re
from typing import Any

# Common secret-bearing field names (case-insensitive match on key).
_SENSITIVE_KEY_FRAGMENTS = (
    "api_key",
    "apikey",
    "authorization",
    "auth_token",
    "access_token",
    "refresh_token",
    "password",
    "secret",
    "credential",
    "bearer",
    "openai_key",
    "research_api_key",
)

# OpenAI-style keys, bearer tokens, and generic long tokens.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9_-]{8,}", re.IGNORECASE),
    re.compile(r"Bearer\s+\S+", re.IGNORECASE),
    re.compile(r"(?i)(api[_-]?key|authorization|token|password)\s*[:=]\s*\S+"),
)

REDACTED = "***REDACTED***"


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in {"openai_api_key", "research_api_key", "authorization", "password"}:
        return True
    return any(fragment in lowered for fragment in _SENSITIVE_KEY_FRAGMENTS)


def sanitize_text(value: str, *, extra_secrets: list[str] | None = None) -> str:
    """Remove known secret patterns and explicit secret substrings from text."""
    cleaned = value
    for secret in extra_secrets or []:
        if secret and secret in cleaned:
            cleaned = cleaned.replace(secret, REDACTED)
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub(REDACTED, cleaned)
    return cleaned


def sanitize_error(exc: BaseException, *, extra_secrets: list[str] | None = None) -> str:
    return sanitize_text(str(exc), extra_secrets=extra_secrets)


def redact_mapping(
    data: dict[str, Any],
    *,
    extra_secrets: list[str] | None = None,
) -> dict[str, Any]:
    """Return a shallow-copied mapping with secrets redacted."""
    out: dict[str, Any] = {}
    for key, value in data.items():
        if is_sensitive_key(str(key)):
            out[key] = REDACTED
        elif isinstance(value, str):
            out[key] = sanitize_text(value, extra_secrets=extra_secrets)
        elif isinstance(value, dict):
            out[key] = redact_mapping(value, extra_secrets=extra_secrets)
        elif isinstance(value, list):
            out[key] = [
                sanitize_text(item, extra_secrets=extra_secrets)
                if isinstance(item, str)
                else item
                for item in value
            ]
        else:
            out[key] = value
    return out
