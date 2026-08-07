"""LLM client protocol and shared types."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field, field_validator


class CandidateTopic(BaseModel):
    title: str = Field(..., min_length=1, max_length=512)
    angle: str = Field(..., min_length=1)
    why_now: str = Field(..., min_length=1)
    relevance_to_persona: str = Field(..., min_length=1)
    source_urls: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("confidence", mode="before")
    @classmethod
    def clamp_confidence(cls, value: object) -> float:
        try:
            number = float(value)  # type: ignore[arg-type]
        except Exception as exc:
            raise ValueError("confidence must be numeric") from exc
        return max(0.0, min(1.0, number))


class CandidateTopicList(BaseModel):
    candidates: list[CandidateTopic] = Field(default_factory=list)


class LLMClient(Protocol):
    """Provider-agnostic LLM interface used by Kestrel services."""

    @property
    def available(self) -> bool: ...

    async def chat(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
    ) -> str: ...

    async def structured_output(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: type[BaseModel],
        temperature: float = 0.2,
    ) -> Any: ...


class UnavailableLLMClient:
    """Null client used when no API key is configured."""

    @property
    def available(self) -> bool:
        return False

    async def chat(self, **_kwargs: object) -> str:
        raise RuntimeError("LLM is unavailable: OPENAI_API_KEY is not configured")

    async def structured_output(self, **_kwargs: object) -> Any:
        raise RuntimeError("LLM is unavailable: OPENAI_API_KEY is not configured")
