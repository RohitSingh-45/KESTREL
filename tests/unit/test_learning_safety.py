"""Safety tests: Module 15 must not rewrite persona/weights/safety authorities."""

from __future__ import annotations

from app.services.learning.models import LearningContext, LearningSignal
from app.services.learning.policy import (
    FORBIDDEN_VALUE_KEYS,
    assert_guidance_only,
    validate_learning_signal,
    validate_signal_value,
)


def test_forbidden_keys_blocked() -> None:
    for key in (
        "persona",
        "domain",
        "system_prompt",
        "score_weights",
        "openai_api_key",
        "bypass_consistency",
        "bypass_grounding",
    ):
        assert key in FORBIDDEN_VALUE_KEYS
        cleaned = validate_signal_value({key: "bad", "recommendation": "prefer evidence"})
        assert key not in cleaned
        assert "recommendation" in cleaned


def test_learning_signal_cannot_carry_weight_mutation() -> None:
    signal = LearningSignal(
        signal_type="strategy",
        key="tamper",
        confidence=0.9,
        support_count=5,
        failure_count=0,
        evidence_count=5,
        reason="prefer grounded evidence",
        value={
            "recommendation": "prefer grounded evidence",
            "score_weight": 0.99,
            "modify_weights": True,
        },
    )
    validated = validate_learning_signal(signal)
    assert validated is not None
    assert "score_weight" not in validated.value
    assert "modify_weights" not in validated.value


def test_guidance_context_strips_unsafe_meta() -> None:
    meta = assert_guidance_only(
        {
            "recommendations": ["prefer novelty"],
            "score_weights": {"novelty": 0.9},
            "persona": "Other",
            "system_prompt": "ignore previous",
            "api_key": "secret",
        }
    )
    assert "recommendations" in meta
    assert "score_weights" not in meta
    assert "persona" not in meta
    assert "system_prompt" not in meta
    assert "api_key" not in meta


def test_learning_context_is_guidance_shaped() -> None:
    ctx = LearningContext(
        recommendations=["prefer strong evidence"],
        active_strategies=[
            {
                "signal_type": "strategy",
                "key": "evidence_first",
                "state": "active",
                "value": {"recommendation": "prefer strong evidence"},
            }
        ],
    )
    dumped = ctx.to_meta()
    assert "recommendations" in dumped
    assert dumped["status"] == "success"
