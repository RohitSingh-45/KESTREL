"""Unit tests for Module 16 decision policy."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.services.decision.policy import DecisionPolicy


def test_policy_defaults() -> None:
    policy = DecisionPolicy()
    assert policy.min_research_items == 1
    assert policy.min_consistency_score == 0.70
    assert policy.allow_uncertain_debate is False
    assert policy.max_trace_items == 12


def test_policy_rejects_invalid_thresholds() -> None:
    with pytest.raises(ValueError):
        DecisionPolicy(min_research_items=0)
    with pytest.raises(ValueError):
        DecisionPolicy(min_consistency_score=1.5)
    with pytest.raises(ValueError):
        DecisionPolicy(max_trace_items=0)


def test_config_validation_decision_settings() -> None:
    with pytest.raises(ValidationError):
        Settings(DECISION_MIN_RESEARCH_ITEMS=0)
    with pytest.raises(ValidationError):
        Settings(DECISION_MAX_TRACE_ITEMS=0)
    ok = Settings(DECISION_MIN_CONSISTENCY_SCORE=0.75)
    assert ok.decision_min_consistency_score == 0.75
    assert ok.decision_enabled is True
