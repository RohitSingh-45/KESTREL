"""Module 20 — AI-security research filter (no fake findings)."""

from __future__ import annotations

from datetime import datetime, timezone

from app.services.ai_security import (
    AI_SECURITY_NO_FINDING_REASON,
    AISecurityFilter,
)
from app.services.research.models import ResearchItem


def _item(title: str, summary: str = "", url: str = "https://example.com/a") -> ResearchItem:
    return ResearchItem(
        title=title,
        summary=summary,
        url=url,
        source_name="fixture",
        discovered_at=datetime(2026, 8, 8, tzinfo=timezone.utc),
        content_hash=f"h-{title[:40]}",
    )


def test_accepts_prompt_injection() -> None:
    result = AISecurityFilter().filter(
        [
            _item(
                "Indirect prompt injection against LLM agents",
                "Attackers manipulate tool-using AI agents via malicious documents.",
                url="https://example.com/pi",
            )
        ]
    )
    assert len(result.accepted) == 1
    assert result.accepted[0].ai_security_domain == "prompt_injection"
    assert result.reason is None


def test_accepts_domain_signal_without_extra_ai_anchor() -> None:
    """Domain phrases like 'prompt injection' are inherently AI-security."""
    result = AISecurityFilter().filter(
        [_item("Prompt injection CVE", "Critical agent exploit.", url="https://example.com/cve")]
    )
    assert len(result.accepted) == 1
    assert result.accepted[0].ai_security_domain == "prompt_injection"


def test_accepts_jailbreak_and_supply_chain() -> None:
    result = AISecurityFilter().filter(
        [
            _item(
                "New LLM jailbreak bypasses safety refusals",
                "Researchers demonstrate jailbreak attacks on frontier models.",
                url="https://example.com/jb",
            ),
            _item(
                "Malicious model on Hugging Face supply chain",
                "AI supply chain risk: poisoned model package in a model repository.",
                url="https://example.com/sc",
            ),
        ]
    )
    domains = {i.ai_security_domain for i in result.accepted}
    assert domains == {"jailbreak", "ai_supply_chain"}


def test_rejects_generic_ransomware_and_phishing() -> None:
    result = AISecurityFilter().filter(
        [
            _item(
                "Hospital network hit by ransomware",
                "Classic ransomware campaign encrypts hospital files.",
                url="https://example.com/ransom",
            ),
            _item(
                "Phishing email campaign steals passwords",
                "Credential harvesting via phishing emails.",
                url="https://example.com/phish",
            ),
        ]
    )
    assert result.accepted == []
    assert result.rejected_count == 2
    assert result.reason == AI_SECURITY_NO_FINDING_REASON


def test_rejects_ai_mentioned_with_only_generic_cyber() -> None:
    result = AISecurityFilter().filter(
        [
            _item(
                "AI startup hit by ransomware",
                "Ransomware encrypts office laptops; payroll systems offline.",
                url="https://example.com/ai-ransom",
            )
        ]
    )
    assert result.accepted == []
    assert result.reason == AI_SECURITY_NO_FINDING_REASON


def test_does_not_invent_cve_or_sources() -> None:
    item = _item("Prompt injection in RAG pipelines", "Documented LLM vulnerability.")
    result = AISecurityFilter().filter([item])
    assert result.accepted[0].url == item.url
    assert "cve" not in (result.accepted[0].summary or "").lower() or "cve" in item.summary.lower()
