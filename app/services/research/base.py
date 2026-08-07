"""Source connector protocol and shared helpers."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

from app.services.research.models import ResearchItem

_WHITESPACE_RE = re.compile(r"\s+")


class SourceConnector(Protocol):
    """Async research source connector."""

    name: str

    async def fetch(
        self,
        query: str | None = None,
        limit: int = 10,
    ) -> list[ResearchItem]: ...


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_whitespace(value: str | None) -> str:
    if not value:
        return ""
    return _WHITESPACE_RE.sub(" ", value).strip()


def normalize_url(url: str | None) -> str | None:
    if not url:
        return None
    cleaned = url.strip()
    if not cleaned:
        return None
    parts = urlsplit(cleaned)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return None
    # Drop fragments; keep query (often meaningful for article IDs).
    normalized = urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, "")
    )
    return normalized.rstrip("/") if parts.path != "/" else normalized


def content_hash_for(*, title: str, url: str, summary: str = "") -> str:
    payload = f"{title.strip().lower()}|{url.strip().lower()}|{summary.strip().lower()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def is_valid_research_item(item: ResearchItem) -> bool:
    if not item.title.strip():
        return False
    if not item.url.strip():
        return False
    if normalize_url(item.url) is None:
        return False
    if not item.content_hash.strip():
        return False
    return True
