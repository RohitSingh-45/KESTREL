"""Story continuity domain models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


StoryClassification = Literal["new_story", "continuing_story", "uncertain"]


class StoryCluster(BaseModel):
    """In-memory / metadata story cluster (not a DB table)."""

    id: str
    title: str
    canonical_topic: str
    source_urls: list[str] = Field(default_factory=list)
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    update_count: int = 0
    latest_summary: str = ""
    status: str = "active"


class StoryMatchResult(BaseModel):
    classification: StoryClassification
    match_score: float = Field(ge=0.0, le=1.0)
    previous_context: str = ""
    new_evidence: str = ""
    what_changed: str = ""
    story: StoryCluster | None = None
    source_urls: list[str] = Field(default_factory=list)

    @property
    def story_update_strength(self) -> float:
        if self.classification != "continuing_story":
            return 0.0
        # Stronger when match is high and change text is present.
        base = self.match_score
        if self.what_changed.strip():
            base = min(1.0, base + 0.1)
        return base

    def to_meta(self) -> dict:
        payload = {
            "classification": self.classification,
            "match_score": self.match_score,
            "previous_context": self.previous_context,
            "new_evidence": self.new_evidence,
            "what_changed": self.what_changed,
            "story_update_strength": self.story_update_strength,
            "source_urls": self.source_urls,
        }
        if self.story is not None:
            payload["story"] = {
                "id": self.story.id,
                "title": self.story.title,
                "canonical_topic": self.story.canonical_topic,
                "update_count": self.story.update_count,
                "status": self.story.status,
            }
        return payload
