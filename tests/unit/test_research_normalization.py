"""Unit tests for research item normalization and filtering."""

from __future__ import annotations

from app.services.research.arxiv import parse_arxiv_atom
from app.services.research.base import is_valid_research_item, normalize_url
from app.services.research.hub import ResearchHub
from app.services.research.models import ResearchItem
from app.services.research.rss import normalize_rss_entries
from app.orchestration.locks import utc_now


def test_rss_normalization_from_fixture() -> None:
    items = normalize_rss_entries(
        [
            {
                "title": "  Prompt Injection Wave  ",
                "link": "https://example.com/a",
                "summary": "Details about LLM attacks",
                "author": "Ada",
                "published": "Tue, 07 Aug 2026 10:00:00 GMT",
            }
        ]
    )
    assert len(items) == 1
    item = items[0]
    assert item.title == "Prompt Injection Wave"
    assert item.url == "https://example.com/a"
    assert item.summary.startswith("Details")
    assert item.source_name == "fixture-rss"
    assert item.content_hash
    assert item.published_at is not None


def test_malformed_rss_entry_skipped() -> None:
    items = normalize_rss_entries(
        [
            {"title": "", "link": "https://example.com/bad"},
            {"title": "Good", "link": "https://example.com/good", "summary": "ok"},
            {"title": "No URL", "link": ""},
        ]
    )
    assert len(items) == 1
    assert items[0].url == "https://example.com/good"


def test_invalid_url_rejected() -> None:
    assert normalize_url("not-a-url") is None
    assert normalize_url("ftp://example.com/x") is None
    now = utc_now()
    try:
        ResearchItem(
            title="x",
            summary="",
            url="not-a-url",
            source_name="t",
            published_at=None,
            discovered_at=now,
            content_hash="abc",
        )
        assert False, "expected validation error"
    except Exception:
        pass


def test_deduplicate_same_url() -> None:
    now = utc_now()
    a = ResearchItem(
        title="One",
        summary="s",
        url="https://example.com/post",
        source_name="a",
        discovered_at=now,
        content_hash="h1",
    )
    b = ResearchItem(
        title="Two",
        summary="s2",
        url="https://example.com/post/",
        source_name="b",
        discovered_at=now,
        content_hash="h2",
    )
    hub = ResearchHub(connectors=[], max_items=20)
    deduped = hub._dedupe([a, b])  # noqa: SLF001
    assert len(deduped) == 1


def test_arxiv_normalization_from_fixture_xml() -> None:
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>Adversarial Attacks on LLMs</title>
        <summary>A paper about AI security.</summary>
        <published>2026-08-01T00:00:00Z</published>
        <author><name>Researcher</name></author>
        <link href="https://arxiv.org/abs/2608.00001" rel="alternate" type="text/html"/>
      </entry>
      <entry>
        <title></title>
        <link href="https://arxiv.org/abs/bad"/>
      </entry>
    </feed>
    """
    items = parse_arxiv_atom(xml)
    assert len(items) == 1
    assert items[0].title == "Adversarial Attacks on LLMs"
    assert items[0].url == "https://arxiv.org/abs/2608.00001"
    assert items[0].source_name == "arXiv"
    assert is_valid_research_item(items[0])
