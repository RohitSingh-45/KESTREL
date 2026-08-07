"""Build production ResearchHub instances from settings."""

from __future__ import annotations

from app.config import Settings
from app.services.research.arxiv import ArxivConnector
from app.services.research.hub import ResearchHub
from app.services.research.rss import RSSConnector


def build_research_hub(settings: Settings) -> ResearchHub:
    connectors: list[object] = []
    if settings.rss_url_list:
        connectors.append(
            RSSConnector(
                settings.rss_url_list,
                timeout_seconds=settings.research_http_timeout_seconds,
            )
        )
    if settings.arxiv_enabled:
        connectors.append(
            ArxivConnector(
                default_query=settings.arxiv_query,
                max_results=settings.arxiv_max_results,
                timeout_seconds=settings.research_http_timeout_seconds,
            )
        )
    # In automated tests, avoid live network by default.
    if settings.app_env == "test":
        connectors = []
    return ResearchHub(connectors=connectors, max_items=settings.research_max_items)  # type: ignore[arg-type]
