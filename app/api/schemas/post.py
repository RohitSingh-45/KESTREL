"""Feed / post response schemas."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_serializer


def to_utc_z(value: datetime) -> str:
    """Serialize a datetime as ISO-8601 UTC with a trailing Z."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


class PostResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    created_at: datetime = Field(..., serialization_alias="createdAt")
    text: str
    rationale: str
    sources: list[str]

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> str:
        return to_utc_z(value)


class FeedResponse(BaseModel):
    posts: list[PostResponse]
