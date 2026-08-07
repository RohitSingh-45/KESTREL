"""Research hub: fan-out, normalize, dedupe, bound context."""

from __future__ import annotations

import time
from collections.abc import Sequence

from app.logging import get_logger
from app.services.research.base import (
    SourceConnector,
    is_valid_research_item,
    normalize_url,
    utc_now,
)
from app.services.research.models import ResearchItem

logger = get_logger(__name__)


class ResearchHub:
    """Coordinates source connectors and returns a bounded, deduped item set."""

    def __init__(
        self,
        connectors: Sequence[SourceConnector],
        *,
        max_items: int = 20,
    ) -> None:
        self._connectors = list(connectors)
        self._max_items = max_items

    async def gather(
        self,
        *,
        query: str | None = None,
        agent_id: str | None = None,
        cycle_id: str | None = None,
        per_connector_limit: int = 10,
    ) -> list[ResearchItem]:
        started = time.perf_counter()
        logger.info(
            "research_started",
            agent_id=agent_id,
            cycle_id=cycle_id,
            connector_count=len(self._connectors),
        )

        collected: list[ResearchItem] = []
        for connector in self._connectors:
            connector_name = getattr(connector, "name", connector.__class__.__name__)
            logger.info(
                "research_connector_started",
                agent_id=agent_id,
                cycle_id=cycle_id,
                connector=connector_name,
            )
            try:
                items = await connector.fetch(query=query, limit=per_connector_limit)
                collected.extend(items)
                logger.info(
                    "research_connector_success",
                    agent_id=agent_id,
                    cycle_id=cycle_id,
                    connector=connector_name,
                    item_count=len(items),
                )
            except Exception:
                logger.exception(
                    "research_connector_failed",
                    agent_id=agent_id,
                    cycle_id=cycle_id,
                    connector=connector_name,
                )

        before = len(collected)
        normalized = [item for item in collected if is_valid_research_item(item)]
        logger.info(
            "research_items_normalized",
            agent_id=agent_id,
            cycle_id=cycle_id,
            item_count=len(normalized),
            rejected=before - len(normalized),
        )

        deduped = self._dedupe(normalized)
        logger.info(
            "research_items_deduplicated",
            agent_id=agent_id,
            cycle_id=cycle_id,
            item_count=len(deduped),
            removed=len(normalized) - len(deduped),
        )

        ranked = self._rank(deduped)
        bounded = ranked[: self._max_items]
        logger.info(
            "research_completed",
            agent_id=agent_id,
            cycle_id=cycle_id,
            item_count=len(bounded),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        return bounded

    @staticmethod
    def _dedupe(items: list[ResearchItem]) -> list[ResearchItem]:
        seen_urls: set[str] = set()
        seen_hashes: set[str] = set()
        result: list[ResearchItem] = []
        for item in items:
            url_key = normalize_url(item.url) or item.url
            if url_key in seen_urls:
                continue
            if item.content_hash in seen_hashes:
                continue
            seen_urls.add(url_key)
            seen_hashes.add(item.content_hash)
            result.append(item)
        return result

    @staticmethod
    def _rank(items: list[ResearchItem]) -> list[ResearchItem]:
        now = utc_now()

        def sort_key(item: ResearchItem) -> tuple:
            ts = item.published_at or item.discovered_at or now
            completeness = int(bool(item.summary)) + int(bool(item.author))
            # Newer first, then more complete.
            return (-ts.timestamp(), -completeness)

        return sorted(items, key=sort_key)
