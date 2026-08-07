"""Reliability helpers: recovery, retry budgets, secret redaction.

Import submodules directly to avoid circular imports with app.logging:
  from app.services.reliability.secrets import sanitize_text
  from app.services.reliability.retry_policy import llm_transport_max_attempts
  from app.services.reliability.recovery import recover_stale_runtime_state
"""

__all__ = [
    "REDACTED",
    "LLM_STRUCTURED",
    "LLM_TRANSPORT",
    "RESEARCH_CONNECTOR",
    "backoff_seconds",
    "llm_structured_max_http_calls",
    "llm_transport_max_attempts",
    "recover_stale_runtime_state",
    "redact_mapping",
    "research_connector_max_attempts",
    "sanitize_error",
    "sanitize_text",
]


def __getattr__(name: str):
    if name in {
        "REDACTED",
        "redact_mapping",
        "sanitize_error",
        "sanitize_text",
    }:
        from app.services.reliability import secrets as _secrets

        return getattr(_secrets, name)
    if name in {
        "LLM_STRUCTURED",
        "LLM_TRANSPORT",
        "RESEARCH_CONNECTOR",
        "backoff_seconds",
        "llm_structured_max_http_calls",
        "llm_transport_max_attempts",
        "research_connector_max_attempts",
    }:
        from app.services.reliability import retry_policy as _retry

        return getattr(_retry, name)
    if name == "recover_stale_runtime_state":
        from app.services.reliability.recovery import recover_stale_runtime_state

        return recover_stale_runtime_state
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
