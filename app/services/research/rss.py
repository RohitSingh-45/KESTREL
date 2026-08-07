"""RSS/Atom source connector."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from time import mktime
from typing import Any

import feedparser
import httpx

from app.logging import get_logger
from app.services.research.base import (
    content_hash_for,
    normalize_url,
    normalize_whitespace,
    utc_now,
)
from app.services.research.models import ResearchItem

logger = get_logger(__name__)


class RSSConnector:
    """Fetches and normalizes one or more RSS/Atom feeds."""

    name = "rss"

    def __init__(
        self,
        feed_urls: list[str],
        *,
        timeout_seconds: float = 20.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._feed_urls = feed_urls
        self._timeout = timeout_seconds
        self._client = client

    async def fetch(
        self,
        query: str | None = None,
        limit: int = 10,
    ) -> list[ResearchItem]:
        if not self._feed_urls:
            return []

        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self._timeout, follow_redirects=True)
        items: list[ResearchItem] = []
        try:
            for feed_url in self._feed_urls:
                try:
                    items.extend(await self._fetch_one(client, feed_url, limit=limit))
                except Exception:
                    logger.exception(
                        "research_connector_failed",
                        connector=self.name,
                        feed_url=feed_url,
                    )
        finally:
            if owns_client:
                await client.aclose()

        if query:
            q = query.lower()
            items = [
                item
                for item in items
                if q in item.title.lower() or q in item.summary.lower()
            ]
        return items[:limit]

    async def _fetch_one(
        self,
        client: httpx.AsyncClient,
        feed_url: str,
        *,
        limit: int,
    ) -> list[ResearchItem]:
        logger.info(
            "research_connector_started",
            connector=self.name,
            feed_url=feed_url,
        )
        response = await client.get(feed_url)
        response.raise_for_status()
        parsed = await asyncio.to_thread(feedparser.parse, response.text)
        source_name = normalize_whitespace(getattr(parsed.feed, "title", None)) or feed_url
        discovered = utc_now()
        results: list[ResearchItem] = []

        for entry in parsed.entries[: max(limit * 2, limit)]:
            try:
                item = self._normalize_entry(entry, source_name=source_name, discovered_at=discovered)
            except Exception:
                logger.info(
                    "research_item_skipped",
                    connector=self.name,
                    feed_url=feed_url,
                    reason="malformed_entry",
                )
                continue
            if item is not None:
                results.append(item)

        logger.info(
            "research_connector_success",
            connector=self.name,
            feed_url=feed_url,
            item_count=len(results),
        )
        return results[:limit]

    def _normalize_entry(
        self,
        entry: Any,
        *,
        source_name: str,
        discovered_at: datetime,
    ) -> ResearchItem | None:
        title = normalize_whitespace(getattr(entry, "title", None))
        link = normalize_url(getattr(entry, "link", None))
        if not title or not link:
            return None

        summary = normalize_whitespace(
            getattr(entry, "summary", None)
            or getattr(entry, "description", None)
            or ""
        )
        author = normalize_whitespace(getattr(entry, "author", None)) or None
        published_at = _parse_entry_datetime(entry)

        return ResearchItem(
            title=title,
            summary=summary,
            url=link,
            source_name=source_name,
            published_at=published_at,
            discovered_at=discovered_at,
            author=author,
            tags=["rss"],
            content_hash=content_hash_for(title=title, url=link, summary=summary),
        )


def _parse_entry_datetime(entry: Any) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, attr, None)
        if parsed:
            try:
                return datetime.fromtimestamp(mktime(parsed), tz=timezone.utc)
            except Exception:
                pass
    for attr in ("published", "updated"):
        raw = getattr(entry, attr, None)
        if not raw:
            continue
        try:
            dt = parsedate_to_datetime(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            continue
    return None


def normalize_rss_entries(
    entries: list[dict[str, Any]],
    *,
    source_name: str = "fixture-rss",
) -> list[ResearchItem]:
    """Pure helper for unit tests — no network."""
    connector = RSSConnector(feed_urls=[])
    discovered = utc_now()
    items: list[ResearchItem] = []
    for entry in entries:
        try:
            item = connector._normalize_entry(  # noqa: SLF001 — test helper
                _DictEntry(entry),
                source_name=source_name,
                discovered_at=discovered,
            )
        except Exception:
            continue
        if item is not None:
            items.append(item)
    return items


class _DictEntry:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def __getattr__(self, item: str) -> Any:
        return self._data.get(item)
