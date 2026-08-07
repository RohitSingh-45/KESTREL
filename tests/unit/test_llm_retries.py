"""LLM retry budget and timeout isolation tests."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from openai import APIStatusError, RateLimitError

from app.services.llm.openai_client import OpenAIClient


class _FakeCompletions:
    def __init__(self, errors: list[Exception], success_text: str = '{"ok": true}') -> None:
        self.errors = list(errors)
        self.calls = 0
        self.success_text = success_text

    async def create(self, **_kwargs: Any) -> MagicMock:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = self.success_text
        return response


def _client_with_fake(fake: _FakeCompletions, *, max_retries: int = 3) -> OpenAIClient:
    client = OpenAIClient(
        api_key="sk-test-key-not-real-000000000000",
        model="gpt-4o-mini",
        timeout_seconds=1.0,
        max_retries=max_retries,
    )
    # Ensure SDK nesting is disabled.
    assert client._client.max_retries == 0
    client._client.chat.completions = fake  # type: ignore[assignment]
    return client


def _status_error(code: int) -> APIStatusError:
    request = MagicMock()
    response = MagicMock()
    response.status_code = code
    response.headers = {}
    return APIStatusError(message=f"status {code}", response=response, body=None)


def _rate_limit_error() -> RateLimitError:
    response = MagicMock()
    response.status_code = 429
    response.headers = {}
    return RateLimitError(message="rate limited", response=response, body=None)


@pytest.mark.asyncio
async def test_llm_retries_429_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    fake = _FakeCompletions([_rate_limit_error(), _rate_limit_error()])
    client = _client_with_fake(fake, max_retries=3)
    text = await client.chat(system_prompt="s", user_prompt="u")
    assert text == '{"ok": true}'
    assert fake.calls == 3


@pytest.mark.asyncio
async def test_llm_retry_limit_on_500(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    fake = _FakeCompletions(
        [_status_error(500), _status_error(500), _status_error(500), _status_error(500)]
    )
    client = _client_with_fake(fake, max_retries=3)
    with pytest.raises(APIStatusError):
        await client.chat(system_prompt="s", user_prompt="u")
    assert fake.calls == 3


@pytest.mark.asyncio
async def test_llm_timeout_bounded_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    fake = _FakeCompletions([TimeoutError("timeout"), TimeoutError("timeout"), TimeoutError("t")])
    client = _client_with_fake(fake, max_retries=3)
    with pytest.raises(TimeoutError):
        await client.chat(system_prompt="s", user_prompt="u")
    assert fake.calls == 3


@pytest.mark.asyncio
async def test_llm_sdk_max_retries_disabled() -> None:
    client = OpenAIClient(
        api_key="sk-test-key-not-real-000000000000",
        model="gpt-4o-mini",
        max_retries=3,
    )
    assert client._client.max_retries == 0
    assert client._max_retries == 3
