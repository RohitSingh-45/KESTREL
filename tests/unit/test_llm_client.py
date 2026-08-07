"""Unit tests for LLM schema validation, grounding, and availability."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import get_settings
from app.services.candidates import ground_candidates
from app.services.llm.base import CandidateTopic, CandidateTopicList, UnavailableLLMClient
from app.services.llm.openai_client import build_llm_client


def test_valid_structured_candidate_output_accepted() -> None:
    payload = {
        "candidates": [
            {
                "title": "Prompt injection defenses",
                "angle": "Practical mitigations",
                "why_now": "New exploit class",
                "relevance_to_persona": "Core AI security",
                "source_urls": ["https://example.com/a"],
                "confidence": 1.7,
            }
        ]
    }
    parsed = CandidateTopicList.model_validate(payload)
    assert parsed.candidates[0].confidence == 1.0


def test_malformed_llm_output_rejected() -> None:
    with pytest.raises(ValidationError):
        CandidateTopicList.model_validate({"candidates": [{"title": "only"}]})


def test_missing_api_key_client_unavailable() -> None:
    get_settings.cache_clear()
    client = build_llm_client(api_key=None, model="gpt-4o-mini")
    assert isinstance(client, UnavailableLLMClient)
    assert client.available is False


def test_source_grounding_filters_unknown_urls() -> None:
    candidates = [
        CandidateTopic(
            title="Good",
            angle="a",
            why_now="n",
            relevance_to_persona="r",
            source_urls=["https://example.com/real", "https://evil.example/fake"],
            confidence=0.8,
        ),
        CandidateTopic(
            title="Bad",
            angle="a",
            why_now="n",
            relevance_to_persona="r",
            source_urls=["https://invented.example/x"],
            confidence=0.2,
        ),
    ]
    grounded = ground_candidates(
        candidates,
        {"https://example.com/real"},
        {"https://example.com/real": "https://example.com/real"},
    )
    assert len(grounded) == 1
    assert grounded[0].title == "Good"
    assert grounded[0].source_urls == ["https://example.com/real"]
