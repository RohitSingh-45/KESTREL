"""Structured logging configuration using structlog."""

from __future__ import annotations

import logging
import sys
from typing import Any, MutableMapping

import structlog

from app.services.reliability.secrets import redact_mapping, sanitize_text


def redact_secrets(
    _logger: logging.Logger,
    _method_name: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """Structlog processor: never emit API keys / auth material."""
    redacted = redact_mapping(dict(event_dict))
    event_dict.clear()
    event_dict.update(redacted)
    # Exception info rendered as text may still contain secrets.
    if "exception" in event_dict and isinstance(event_dict["exception"], str):
        event_dict["exception"] = sanitize_text(event_dict["exception"])
    if "exc_info" in event_dict and isinstance(event_dict["exc_info"], str):
        event_dict["exc_info"] = sanitize_text(event_dict["exc_info"])
    return event_dict


def configure_logging(log_level: str = "INFO") -> None:
    """Configure structlog + stdlib logging for the application."""
    level = getattr(logging, log_level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
        force=True,
    )

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        redact_secrets,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
