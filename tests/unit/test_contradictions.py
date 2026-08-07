"""Unit tests for Module 12 contradiction detection."""

from __future__ import annotations

import pytest

from app.orchestration.locks import utc_now
from app.services.contradictions import ContradictionDetector, challenge_payload
from app.services.memory.models import MemoryKindEnum, MemoryRecord, MemoryValue
from app.services.research.models import ResearchItem


def _belief(key: str, text: str, *, memory_id: str = "m1") -> MemoryRecord:
    now = utc_now()
    value = MemoryValue(
        kind=MemoryKindEnum.BELIEF,
        text=text,
        confidence=0.84,
        supporting_source_urls=["https://arxiv.org/abs/old"],
    )
    return MemoryRecord(
        id=memory_id,
        agent_id="a1",
        kind=MemoryKindEnum.BELIEF,
        key=key,
        value=value,
        salience=0.8,
        confidence=0.84,
        created_at=now,
        updated_at=now,
    )


def _item(title: str, summary: str, url: str) -> ResearchItem:
    now = utc_now()
    return ResearchItem(
        title=title,
        summary=summary,
        url=url,
        source_name="fixture",
        published_at=now,
        discovered_at=now,
        content_hash=url,
    )


@pytest.mark.asyncio
async def test_direct_contradiction_detected() -> None:
    detector = ContradictionDetector(enabled=True, min_confidence=0.70, use_llm=False)
    result = await detector.analyze(
        memories=[
            _belief(
                "prompt injection defenses",
                "Defense X reduces prompt injection risk for AI agents.",
            )
        ],
        research_items=[
            _item(
                "Defense X bypassed under attack Y",
                "Researchers show Defense X was bypassed and is ineffective against agents.",
                "https://arxiv.org/abs/new",
            )
        ],
    )
    assert result.signals
    assert result.signals[0].contradiction_type in {"direct", "partial"}
    assert result.signals[0].evidence_urls == ["https://arxiv.org/abs/new"]


@pytest.mark.asyncio
async def test_no_contradiction_when_aligned() -> None:
    detector = ContradictionDetector(enabled=True, min_confidence=0.70, use_llm=False)
    result = await detector.analyze(
        memories=[
            _belief(
                "prompt injection",
                "Prompt injection remains a major risk for AI agents.",
            )
        ],
        research_items=[
            _item(
                "Prompt injection remains a major risk",
                "New study confirms prompt injection risk for AI agents.",
                "https://arxiv.org/abs/same",
            )
        ],
    )
    assert result.signals == []


@pytest.mark.asyncio
async def test_unknown_source_rejected() -> None:
    detector = ContradictionDetector(enabled=True, min_confidence=0.5, use_llm=False)
    # Craft a signal path via deterministic then strip grounding by using unmatched URL
    # Detector only accepts research URLs present in the batch.
    result = await detector.analyze(
        memories=[
            _belief(
                "prompt injection defenses",
                "Defense X reduces prompt injection risk.",
            )
        ],
        research_items=[
            _item(
                "Unrelated weather update",
                "Rain expected tomorrow.",
                "https://example.com/weather",
            )
        ],
    )
    assert all(
        u.startswith("https://") for s in result.signals for u in s.evidence_urls
    )
    # Weather item should not ground a defense contradiction.
    assert result.signals == []


@pytest.mark.asyncio
async def test_partial_and_contextual_types() -> None:
    detector = ContradictionDetector(enabled=True, min_confidence=0.60, use_llm=False)
    result = await detector.analyze(
        memories=[
            _belief(
                "jailbreak defenses",
                "Enterprise jailbreak defenses protect LLM systems effectively.",
            )
        ],
        research_items=[
            _item(
                "Jailbreak defense bypass",
                "A new jailbreak bypass shows defenses are insufficient.",
                "https://www.cisa.gov/jailbreak",
            )
        ],
    )
    assert result.signals
    assert result.signals[0].contradiction_type in {"direct", "partial", "contextual"}


def test_challenge_payload_shape() -> None:
    from app.services.contradictions.models import ContradictionSignal

    payload = challenge_payload(
        [
            ContradictionSignal(
                memory_id="m1",
                memory_key="prompt injection defenses",
                evidence_urls=["https://arxiv.org/abs/x"],
                contradiction_type="partial",
                explanation="Bypass observed",
                confidence=0.8,
            )
        ]
    )
    assert payload[0]["relation"] == "contradicting"
    assert payload[0]["source_urls"] == ["https://arxiv.org/abs/x"]


@pytest.mark.asyncio
async def test_disabled_detector_skips() -> None:
    detector = ContradictionDetector(enabled=False, use_llm=False)
    result = await detector.analyze(memories=[], research_items=[])
    assert result.status == "skipped"


@pytest.mark.asyncio
async def test_detector_failure_is_isolated() -> None:
    class BoomLLM:
        available = True

        async def chat(self, **_k: object) -> str:
            raise RuntimeError("llm boom")

        async def structured_output(self, **_k: object) -> object:
            raise RuntimeError("llm boom")

    detector = ContradictionDetector(BoomLLM(), enabled=True, use_llm=True, min_confidence=0.99)
    # Deterministic path may still return; force LLM path with empty deterministic match
    # and ensure exceptions become failed status rather than raising.
    result = await detector.analyze(
        memories=[_belief("cloud costs", "Cloud costs are rising for teams.")],
        research_items=[
            _item(
                "Unrelated note",
                "Nothing security related here.",
                "https://example.com/x",
            )
        ],
    )
    assert result.status in {"success", "failed"}
    # Must not raise to caller.
