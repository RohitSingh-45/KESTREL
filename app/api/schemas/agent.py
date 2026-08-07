"""Agent init request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Persona(BaseModel):
    """Evaluator persona payload."""

    name: str = Field(..., min_length=1, max_length=255)
    domain: str = Field(..., min_length=1, max_length=255)

    @field_validator("name", "domain")
    @classmethod
    def strip_and_require_content(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must not be empty or whitespace-only")
        return cleaned


class InitRequest(BaseModel):
    persona: Persona


class InitResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    agent_id: str = Field(..., serialization_alias="agentId")
