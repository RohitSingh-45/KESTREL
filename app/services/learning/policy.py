"""Safety policy for controlled learning (Module 15).

Learning may only emit bounded guidance. It must never rewrite identity,
scoring weights, prompts, credentials, or safety authorities.
"""

from __future__ import annotations

from typing import Any

from app.services.learning.models import ALLOWED_SIGNAL_TYPES, LearningSignal

FORBIDDEN_VALUE_KEYS = frozenset(
    {
        "persona",
        "persona_name",
        "domain",
        "system_prompt",
        "score_weight",
        "score_weights",
        "openai_api_key",
        "api_key",
        "authorization",
        "bypass_consistency",
        "bypass_grounding",
        "bypass_publishing",
        "rewrite_prompt",
        "modify_weights",
        "fine_tune",
        "reinforcement_learning",
    }
)

FORBIDDEN_RECOMMENDATION_PHRASES = (
    "change persona",
    "rewrite identity",
    "modify score weight",
    "bypass consistency",
    "bypass grounding",
    "disable safety",
    "change domain",
    "expose api key",
)


def is_allowed_signal_type(signal_type: str) -> bool:
    return str(signal_type or "").strip().lower() in ALLOWED_SIGNAL_TYPES


def sanitize_recommendation(text: str) -> str | None:
    cleaned = " ".join(str(text or "").split()).strip()
    if not cleaned:
        return None
    lower = cleaned.lower()
    if any(phrase in lower for phrase in FORBIDDEN_RECOMMENDATION_PHRASES):
        return None
    return cleaned[:240]


def validate_signal_value(value: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(value or {})
    cleaned: dict[str, Any] = {}
    for key, item in raw.items():
        key_s = str(key).strip().lower()
        if key_s in FORBIDDEN_VALUE_KEYS:
            continue
        if key_s.endswith("_weight") or key_s.endswith("_api_key"):
            continue
        if key_s == "recommendation" and isinstance(item, str):
            rec = sanitize_recommendation(item)
            if rec:
                cleaned["recommendation"] = rec
            continue
        if isinstance(item, (str, int, float, bool)) or item is None:
            cleaned[str(key)[:64]] = item
        elif isinstance(item, list):
            cleaned[str(key)[:64]] = [
                str(x)[:120] for x in item[:8] if isinstance(x, (str, int, float))
            ]
    return cleaned


def validate_learning_signal(signal: LearningSignal) -> LearningSignal | None:
    """Return a sanitized signal or None if unsafe/invalid."""
    if not is_allowed_signal_type(signal.signal_type):
        return None
    key = str(signal.key or "").strip().lower().replace(" ", "_")[:80]
    if not key:
        return None
    value = validate_signal_value(signal.value)
    reason = sanitize_recommendation(signal.reason) or "derived_from_historical_evidence"
    refs = [str(r)[:160] for r in (signal.evidence_refs or [])[:8] if str(r).strip()]
    return signal.model_copy(
        update={
            "key": key,
            "value": value,
            "reason": reason,
            "evidence_refs": refs,
        }
    )


def assert_guidance_only(context_meta: dict[str, Any]) -> dict[str, Any]:
    """Strip any accidental unsafe keys from learning context meta."""
    banned = {"score_weights", "persona", "system_prompt", "api_key"}
    return {k: v for k, v in context_meta.items() if k not in banned}
