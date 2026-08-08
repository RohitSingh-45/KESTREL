"""OpenAI-compatible LLM adapter."""

from __future__ import annotations

import asyncio
import json
from typing import Any, TypeVar

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    RateLimitError,
)
from pydantic import BaseModel, ValidationError

from app.logging import get_logger
from app.services.llm.base import UnavailableLLMClient
from app.services.llm.prompts import CORRECTION_PROMPT
from app.services.reliability.retry_policy import backoff_seconds, llm_transport_max_attempts
from app.services.reliability.secrets import sanitize_error

logger = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


class OpenAIClient:
    """LLMClient implementation backed by the OpenAI SDK."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float = 45.0,
        max_retries: int = 3,
        base_url: str | None = None,
    ) -> None:
        self._model = model
        # Application owns retries; disable nested SDK retries.
        self._max_retries = llm_transport_max_attempts(max_retries)
        self._client = AsyncOpenAI(
            api_key=api_key,
            timeout=timeout_seconds,
            max_retries=0,
            **(dict(base_url=base_url) if base_url else {}),
        )

    @property
    def available(self) -> bool:
        return True

    async def chat(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
    ) -> str:
        return await self._request_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
        )

    async def structured_output(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: type[T],
        temperature: float = 0.2,
    ) -> T:
        text = await self._request_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            json_mode=True,
        )
        try:
            return schema.model_validate_json(_extract_json(text))
        except ValidationError:
            logger.info("llm_structured_output_retry", reason="validation_error")
            retry_text = await self._request_text(
                system_prompt=system_prompt,
                user_prompt=f"{user_prompt}\n\n{CORRECTION_PROMPT}",
                temperature=0.0,
                json_mode=True,
            )
            return schema.model_validate_json(_extract_json(retry_text))

    async def _request_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        json_mode: bool = False,
    ) -> str:
        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                kwargs: dict[str, Any] = {
                    "model": self._model,
                    "temperature": temperature,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                }
                if json_mode:
                    kwargs["response_format"] = {"type": "json_object"}
                response = await self._client.chat.completions.create(**kwargs)
                content = response.choices[0].message.content
                if not content:
                    raise RuntimeError("Empty LLM response")
                return content
            except AuthenticationError as exc:
                # Invalid/missing key — never retry; never fabricate a response.
                logger.info(
                    "llm_auth_failed",
                    error=sanitize_error(exc),
                    attempt=attempt,
                )
                raise
            except (
                RateLimitError,
                APIConnectionError,
                APITimeoutError,
                APIStatusError,
                TimeoutError,
                asyncio.TimeoutError,
            ) as exc:
                last_error = exc
                status = getattr(exc, "status_code", None)
                # Auth-like 4xx (except 429) must not burn the retry budget.
                retryable = (
                    isinstance(exc, (APIConnectionError, APITimeoutError, TimeoutError, asyncio.TimeoutError))
                    or status is None
                    or status == 429
                    or (isinstance(status, int) and status >= 500)
                )
                if isinstance(exc, APIStatusError) and status is not None and status < 500 and status != 429:
                    retryable = False
                if not retryable or attempt >= self._max_retries:
                    logger.info(
                        "llm_request_failed",
                        error=sanitize_error(exc),
                        attempt=attempt,
                        status=status,
                        retryable=retryable,
                    )
                    raise
                delay = backoff_seconds(attempt)
                logger.info(
                    "llm_retry",
                    attempt=attempt,
                    max_attempts=self._max_retries,
                    delay_seconds=delay,
                    status=status,
                    error=sanitize_error(exc),
                )
                await asyncio.sleep(delay)
            except Exception as exc:
                logger.info(
                    "llm_request_failed",
                    error=sanitize_error(exc),
                    attempt=attempt,
                )
                raise
        assert last_error is not None
        raise last_error


def build_llm_client(
    *,
    api_key: str | None,
    model: str,
    timeout_seconds: float = 45.0,
    max_retries: int = 3,
) -> OpenAIClient | UnavailableLLMClient:
    """Legacy OpenAI builder. Prefer ``build_llm_from_settings`` for new code."""
    if not api_key:
        return UnavailableLLMClient()
    return OpenAIClient(
        api_key=api_key,
        model=model,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
    )


def _extract_json(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    # Validate it parses as JSON object/array.
    json.loads(cleaned)
    return cleaned
