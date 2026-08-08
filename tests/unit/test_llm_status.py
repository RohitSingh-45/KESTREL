"""Module 21 — LLM status classification (no live network)."""

from __future__ import annotations

from unittest.mock import MagicMock

from openai import APIStatusError, RateLimitError

from app.services.dashboard.service import DashboardService
from app.services.llm.status import classify_llm_exception


def test_classify_auth_error() -> None:
    class AuthBoom(Exception):
        status_code = 401

    assert classify_llm_exception(AuthBoom("invalid api key")) == "authentication_failed"


def test_classify_rate_limit() -> None:
    response = MagicMock()
    response.status_code = 429
    response.headers = {}
    exc = RateLimitError(message="slow down", response=response, body=None)
    assert classify_llm_exception(exc) == "unstable"


def test_classify_server_error() -> None:
    response = MagicMock()
    response.status_code = 503
    response.headers = {}
    exc = APIStatusError(message="down", response=response, body=None)
    assert classify_llm_exception(exc) == "unstable"


def test_dashboard_ready_only_when_probed() -> None:
    assert (
        DashboardService._llm_status("sk-test", {}, "success", None, probed_status=None)
        == "unavailable"
    )
    assert (
        DashboardService._llm_status("sk-test", {}, "success", None, probed_status="ready")
        == "ready"
    )
    assert (
        DashboardService._llm_status(
            "sk-test", {}, "failed", None, probed_status="authentication_failed"
        )
        == "authentication_failed"
    )


def test_dashboard_missing_key() -> None:
    assert (
        DashboardService._llm_status(None, {}, None, None, probed_status="ready")
        == "unavailable"
    )
