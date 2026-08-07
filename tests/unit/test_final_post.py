"""Unit tests for GeneratedPost schema and final generation helpers."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.final_post.generator import FinalPostGenerator
from app.services.final_post.models import FinalPostContext, GeneratedPost
from app.services.final_post.prompts import build_repair_user_prompt


def test_valid_generated_post_schema() -> None:
    post = GeneratedPost.model_validate(
        {
            "text": "Prompt injection remains a meaningful AI Security risk for agents.",
            "rationale": (
                "Selected for AI Security relevance with grounded evidence over weaker alternatives."
            ),
            "source_urls": ["https://example.com/research"],
        }
    )
    assert "Prompt injection" in post.text
    assert post.source_urls == ["https://example.com/research"]


def test_empty_text_rejected() -> None:
    with pytest.raises(ValidationError):
        GeneratedPost.model_validate(
            {
                "text": "   ",
                "rationale": "Selected with evidence for AI Security relevance.",
                "source_urls": ["https://example.com/a"],
            }
        )


def test_empty_rationale_rejected() -> None:
    with pytest.raises(ValidationError):
        GeneratedPost.model_validate(
            {
                "text": "AI Security update on prompt injection.",
                "rationale": "",
                "source_urls": ["https://example.com/a"],
            }
        )


def test_invalid_url_rejected() -> None:
    with pytest.raises(ValidationError):
        GeneratedPost.model_validate(
            {
                "text": "AI Security note.",
                "rationale": "Selected with evidence for security relevance.",
                "source_urls": ["not-a-url"],
            }
        )


def test_malformed_llm_response_rejected() -> None:
    with pytest.raises(ValidationError):
        GeneratedPost.model_validate({"text": "only text"})


@pytest.mark.asyncio
async def test_generator_skips_when_llm_unavailable() -> None:
    class _Down:
        available = False

        async def chat(self, **_k: object) -> str:
            raise RuntimeError("down")

        async def structured_output(self, **_k: object) -> object:
            raise RuntimeError("down")

    gen = FinalPostGenerator(_Down())  # type: ignore[arg-type]
    outcome = await gen.generate(
        FinalPostContext(
            persona_name="Kestrel",
            persona_domain="AI Security",
            topic={"title": "x"},
            debate={},
            consistency={},
            grounded_urls=["https://example.com/a"],
        )
    )
    assert outcome.status == "skipped"
    assert outcome.error == "llm_unavailable"
    assert gen.call_count == 0


def test_repair_request_construction() -> None:
    context = FinalPostContext(
        persona_name="Kestrel",
        persona_domain="AI Security",
        topic={"title": "Prompt injection"},
        debate={"decision": "proceed"},
        consistency={"decision": "approve"},
        grounded_urls=["https://example.com/a"],
    )
    prompt = build_repair_user_prompt(
        context,
        previous={"text": "hype!!!", "rationale": "x", "source_urls": []},
        validation_errors=["excessive_hype", "no_grounded_source"],
    )
    assert "Validation issues" in prompt
    assert "excessive_hype" in prompt
    assert "do NOT invent new evidence" in prompt
