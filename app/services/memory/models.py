"""Provider-independent memory and belief domain models."""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

MemoryKind = Literal["belief", "entity", "theme", "stance", "note"]


class MemoryKindEnum(str, Enum):
    BELIEF = "belief"
    ENTITY = "entity"
    THEME = "theme"
    STANCE = "stance"
    NOTE = "note"


_KEY_CLEAN_RE = re.compile(r"[^a-z0-9\s]+")
_SPACE_RE = re.compile(r"\s+")


def normalize_memory_key(raw: str) -> str:
    """Normalize keys for deduplication without collapsing unrelated concepts."""
    text = raw.strip().lower().replace("-", " ").replace("_", " ")
    text = _KEY_CLEAN_RE.sub(" ", text)
    text = _SPACE_RE.sub(" ", text).strip()
    return text[:255]


def clamp_unit(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


class Belief(BaseModel):
    subject: str = Field(..., min_length=1)
    predicate: str = Field(..., min_length=1)
    object: str = Field(..., min_length=1)
    stance: str = Field(default="")
    confidence: float = 0.5
    supporting_source_urls: list[str] = Field(default_factory=list)
    contradicting_source_urls: list[str] = Field(default_factory=list)
    evidence_count: int = Field(default=0, ge=0)
    last_updated: datetime | None = None
    history: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, value: object) -> float:
        try:
            return clamp_unit(float(value))  # type: ignore[arg-type]
        except Exception:
            return 0.0


class MemoryValue(BaseModel):
    """Structured payload stored in memory_items.value_json."""

    version: int = 1
    kind: MemoryKindEnum
    text: str = ""
    subject: str | None = None
    predicate: str | None = None
    object: str | None = None
    stance: str | None = None
    confidence: float = 0.5
    supporting_source_urls: list[str] = Field(default_factory=list)
    contradicting_source_urls: list[str] = Field(default_factory=list)
    evidence_count: int = Field(default=0, ge=0)
    history: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, value: object) -> float:
        try:
            return clamp_unit(float(value))  # type: ignore[arg-type]
        except Exception:
            return 0.0


class MemoryRecord(BaseModel):
    """Application-level memory item (not an ORM object)."""

    id: str
    agent_id: str
    kind: MemoryKindEnum
    key: str
    value: MemoryValue
    salience: float = 0.5
    confidence: float = 0.5
    created_at: datetime
    updated_at: datetime

    @field_validator("salience", "confidence", mode="before")
    @classmethod
    def _unit(cls, value: object) -> float:
        try:
            return clamp_unit(float(value))  # type: ignore[arg-type]
        except Exception:
            return 0.0


class MemoryCandidate(BaseModel):
    """Structured LLM extraction candidate before grounding/upsert."""

    kind: MemoryKindEnum
    key: str = Field(..., min_length=1)
    value: str = Field(..., min_length=1)
    stance: str | None = None
    confidence: float = 0.5
    source_urls: list[str] = Field(default_factory=list)
    relation: Literal["supporting", "contradicting", "neutral"] = "supporting"
    subject: str | None = None
    predicate: str | None = None
    object: str | None = None

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, value: object) -> float:
        try:
            return clamp_unit(float(value))  # type: ignore[arg-type]
        except Exception:
            return 0.0


class MemoryExtractionResponse(BaseModel):
    memories: list[MemoryCandidate] = Field(default_factory=list)


def memories_for_prompt(records: list[MemoryRecord]) -> list[dict[str, Any]]:
    """Serialize memories for debate prompts (historical context only)."""
    payload: list[dict[str, Any]] = []
    for record in records:
        payload.append(
            {
                "kind": record.kind.value,
                "key": record.key,
                "confidence": record.confidence,
                "salience": record.salience,
                "text": record.value.text,
                "stance": record.value.stance,
                "subject": record.value.subject,
                "predicate": record.value.predicate,
                "object": record.value.object,
                "supporting_source_urls": record.value.supporting_source_urls[:5],
                "contradicting_source_urls": record.value.contradicting_source_urls[:5],
                "evidence_count": record.value.evidence_count,
            }
        )
    return payload
