"""arXiv Atom API connector (no HTML scraping)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

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

_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
_ARXIV_API = "https://export.arxiv.org/api/query"


class ArxivConnector:
    """Fetches recent arXiv papers for AI-security related queries."""

    name = "arxiv"

    def __init__(
        self,
        *,
        default_query: str,
        max_results: int = 8,
        timeout_seconds: float = 20.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._default_query = default_query
        self._max_results = max_results
        self._timeout = timeout_seconds
        self._client = client

    async def fetch(
        self,
        query: str | None = None,
        limit: int = 10,
    ) -> list[ResearchItem]:
        search_query = query or self._default_query
        max_results = min(limit, self._max_results)
        params = urlencode(
            {
                "search_query": f"all:{search_query}",
                "start": 0,
                "max_results": max_results,
                "sortBy": "submittedDate",
                "sortOrder": "descending",
            }
        )
        url = f"{_ARXIV_API}?{params}"

        logger.info(
            "research_connector_started",
            connector=self.name,
            query=search_query,
            max_results=max_results,
        )

        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self._timeout, follow_redirects=True)
        try:
            response = await client.get(url)
            response.raise_for_status()
            items = parse_arxiv_atom(response.text)
            logger.info(
                "research_connector_success",
                connector=self.name,
                item_count=len(items),
            )
            return items[:max_results]
        except Exception:
            logger.exception("research_connector_failed", connector=self.name)
            raise
        finally:
            if owns_client:
                await client.aclose()


def parse_arxiv_atom(xml_text: str) -> list[ResearchItem]:
    """Normalize an arXiv Atom XML payload into ResearchItems."""
    root = ET.fromstring(xml_text)
    discovered = utc_now()
    items: list[ResearchItem] = []

    for entry in root.findall("atom:entry", _ATOM_NS):
        try:
            title = normalize_whitespace(entry.findtext("atom:title", default="", namespaces=_ATOM_NS))
            summary = normalize_whitespace(
                entry.findtext("atom:summary", default="", namespaces=_ATOM_NS)
            )
            link = _best_arxiv_link(entry)
            link = normalize_url(link)
            if not title or not link:
                continue

            published_raw = entry.findtext("atom:published", default="", namespaces=_ATOM_NS)
            published_at = _parse_iso(published_raw)
            author = None
            author_el = entry.find("atom:author/atom:name", _ATOM_NS)
            if author_el is not None and author_el.text:
                author = normalize_whitespace(author_el.text) or None

            items.append(
                ResearchItem(
                    title=title,
                    summary=summary,
                    url=link,
                    source_name="arXiv",
                    published_at=published_at,
                    discovered_at=discovered,
                    author=author,
                    tags=["arxiv", "research"],
                    content_hash=content_hash_for(title=title, url=link, summary=summary),
                )
            )
        except Exception:
            continue
    return items


def _best_arxiv_link(entry: ET.Element) -> str | None:
    html_link = None
    for link in entry.findall("atom:link", _ATOM_NS):
        href = link.attrib.get("href")
        rel = link.attrib.get("rel", "alternate")
        link_type = link.attrib.get("type", "")
        if not href:
            continue
        if rel == "alternate" and "html" in link_type:
            return href
        if rel == "alternate" and html_link is None:
            html_link = href
    return html_link


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None
