"""Unit tests for ResearchHub fan-out and resilience."""

from __future__ import annotations

import pytest

from app.orchestration.locks import utc_now
from app.services.research.hub import ResearchHub
from app.services.research.models import ResearchItem


class _OkConnector:
    name = "ok"

    def __init__(self, items: list[ResearchItem]) -> None:
        self._items = items

    async def fetch(self, query: str | None = None, limit: int = 10) -> list[ResearchItem]:
        return self._items[:limit]


class _FailConnector:
    name = "fail"

    async def fetch(self, query: str | None = None, limit: int = 10) -> list[ResearchItem]:
        raise RuntimeError("boom")


def _item(title: str, url: str, content_hash: str) -> ResearchItem:
    return ResearchItem(
        title=title,
        summary="summary",
        url=url,
        source_name="test",
        discovered_at=utc_now(),
        content_hash=content_hash,
    )


@pytest.mark.asyncio
async def test_hub_partial_failure_and_dedupe_and_limit() -> None:
    items = [
        _item("A", "https://example.com/a", "h1"),
        _item("A-dup", "https://example.com/a", "h1b"),
        _item("B", "https://example.com/b", "h2"),
        _item("C", "https://example.com/c", "h3"),
    ]
    hub = ResearchHub(
        connectors=[_FailConnector(), _OkConnector(items)],
        max_items=2,
    )
    result = await hub.gather()
    assert len(result) == 2
    urls = {item.url for item in result}
    # Partial failure must not wipe successful connector results.
    assert urls.issubset(
        {"https://example.com/a", "https://example.com/b", "https://example.com/c"}
    )
    assert len(urls) == 2
