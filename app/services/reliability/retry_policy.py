"""Central retry/timeout budgets for external calls.

Effective maximum external calls (defaults):

LLM transport (chat / single request):
  OPENAI_MAX_RETRIES attempts (default 3), exponential backoff capped at 8s.
  OpenAI SDK internal retries are disabled (max_retries=0) to avoid nesting.

LLM structured_output:
  1 primary request path + at most 1 schema-repair request path.
  Each path uses the transport budget above.
  Worst case: OPENAI_MAX_RETRIES * 2 = 6 HTTP calls (default).

Research connectors (RSS / arXiv):
  Exactly 1 attempt per connector per cycle (no automatic retry).
  Failures are isolated; other connectors continue.

Nested retry explosion is intentionally prevented by disabling SDK retries
and forbidding research retries.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RetryBudget:
    """Documented retry limits for one external call class."""

    max_attempts: int
    structured_repair_attempts: int = 0
    backoff_cap_seconds: float = 8.0


# Defaults aligned with Settings.
LLM_TRANSPORT = RetryBudget(max_attempts=3, backoff_cap_seconds=8.0)
LLM_STRUCTURED = RetryBudget(
    max_attempts=3,
    structured_repair_attempts=1,
    backoff_cap_seconds=8.0,
)
RESEARCH_CONNECTOR = RetryBudget(max_attempts=1)


def llm_transport_max_attempts(configured_max_retries: int) -> int:
    return max(1, configured_max_retries)


def llm_structured_max_http_calls(configured_max_retries: int) -> int:
    """Worst-case HTTP calls for one structured_output invocation."""
    return llm_transport_max_attempts(configured_max_retries) * (
        1 + LLM_STRUCTURED.structured_repair_attempts
    )


def research_connector_max_attempts() -> int:
    return RESEARCH_CONNECTOR.max_attempts


def backoff_seconds(attempt: int, *, cap: float = 8.0) -> float:
    """Exponential backoff for 1-indexed attempt number."""
    return min(float(2 ** max(0, attempt - 1)), cap)
