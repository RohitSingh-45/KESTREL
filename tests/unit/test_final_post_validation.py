"""Unit tests for deterministic final post validation."""

from __future__ import annotations

from app.services.final_post.models import GeneratedPost
from app.services.final_post.validator import FinalPostValidator
from app.services.research.base import normalize_url


def _post(**overrides: object) -> GeneratedPost:
    base = {
        "text": (
            "Prompt injection against AI agents remains a concrete AI Security concern "
            "with current research implications for defensive monitoring."
        ),
        "rationale": (
            "Selected for AI Security relevance and recent evidence. Preferred over "
            "alternatives that were less timely or less security-specific."
        ),
        "source_urls": ["https://example.com/research"],
    }
    base.update(overrides)
    return GeneratedPost.model_validate(base)


def test_source_grounding_passes() -> None:
    validator = FinalPostValidator()
    errors = validator.validate(
        _post(),
        grounded_urls={"https://example.com/research"},
    )
    assert errors == []


def test_unknown_source_url_rejected() -> None:
    validator = FinalPostValidator()
    errors = validator.validate(
        _post(source_urls=["https://fake-source.com/x"]),
        grounded_urls={"https://example.com/research"},
    )
    assert "unknown_source_url" in errors or "no_grounded_source" in errors


def test_wrong_domain_content_rejected() -> None:
    validator = FinalPostValidator()
    errors = validator.validate(
        _post(
            text="Best cooking recipes and fashion tips for weekend entertainment.",
            rationale="Selected because cooking is fun.",
            source_urls=["https://example.com/research"],
        ),
        grounded_urls={"https://example.com/research"},
    )
    assert "wrong_domain" in errors


def test_hype_detection() -> None:
    validator = FinalPostValidator()
    errors = validator.validate(
        _post(
            text="THIS IS ABSOLUTELY THE WORST AI THREAT EVER!!! GAME CHANGER!!!",
            rationale="Selected with evidence for AI Security relevance over alternatives.",
        ),
        grounded_urls={"https://example.com/research"},
    )
    assert "excessive_hype" in errors


def test_unsupported_certainty_detection() -> None:
    validator = FinalPostValidator()
    errors = validator.validate(
        _post(
            text="This disclosure definitely proves prompt injection is 100% guaranteed.",
            rationale="Selected with evidence for AI Security relevance over alternatives.",
        ),
        grounded_urls={"https://example.com/research"},
    )
    assert "unsupported_certainty" in errors


def test_excessive_repetition_detection() -> None:
    validator = FinalPostValidator()
    title = "Prompt injection against AI agents remains a concrete AI Security concern"
    errors = validator.validate(
        _post(text=title + " with current research implications for defensive monitoring."),
        grounded_urls={"https://example.com/research"},
        previous_titles=[title],
    )
    assert "excessive_repetition" in errors


def test_valid_final_post_passes() -> None:
    validator = FinalPostValidator()
    errors = validator.validate(
        _post(),
        grounded_urls={"https://example.com/research"},
        previous_titles=["Unrelated older title about model poisoning"],
    )
    assert errors == []


def test_rationale_validation() -> None:
    validator = FinalPostValidator()
    errors = validator.validate(
        _post(rationale="Because it felt interesting."),
        grounded_urls={"https://example.com/research"},
    )
    assert "rationale_incomplete" in errors


def test_ground_urls_helper() -> None:
    validator = FinalPostValidator()
    key = normalize_url("https://example.com/research") or "https://example.com/research"
    cleaned = validator.ground_urls(
        ["https://example.com/research", "https://invented.example/x"],
        {"https://example.com/research"},
        {key: "https://example.com/research"},
    )
    assert cleaned == ["https://example.com/research"]
