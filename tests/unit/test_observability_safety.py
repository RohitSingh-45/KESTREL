"""Security / sanitization tests for Module 17 observability."""

from __future__ import annotations

import json

from app.services.observability import ObservabilityService
from app.services.observability.sanitizer import sanitize_mapping
from app.services.reliability.secrets import REDACTED


def test_secret_sanitization_removes_api_keys() -> None:
    dirty = {
        "note": "key sk-test-1234567890abcdef used",
        "openai_api_key": "sk-test-1234567890abcdef",
        "Authorization": "Bearer secret-token-value",
        "password": "super-secret",
    }
    clean = sanitize_mapping(dirty)
    blob = json.dumps(clean)
    assert "sk-test-1234567890abcdef" not in blob
    assert "secret-token-value" not in blob
    assert "super-secret" not in blob
    assert REDACTED in blob


def test_prompt_and_raw_llm_exclusion() -> None:
    dirty = {
        "system_prompt": "You are Kestrel...",
        "user_prompt": "Write a post about...",
        "raw_llm_response": '{"text":"secret draft"}',
        "safe": "ok",
    }
    clean = sanitize_mapping(dirty)
    assert clean["system_prompt"] == REDACTED
    assert clean["user_prompt"] == REDACTED
    assert clean["raw_llm_response"] == REDACTED
    assert clean["safe"] == "ok"


def test_service_record_sanitizes_secrets() -> None:
    svc = ObservabilityService()
    svc.begin(agent_id="a1", cycle_id="c1")
    event = svc.record(
        "research_completed",
        stage="research",
        reason="fetched with sk-test-9999999999999999",
        evidence={"token": "sk-test-9999999999999999", "prompt": "hidden"},
        metadata={"openai_api_key": "sk-test-9999999999999999"},
    )
    assert event is not None
    blob = json.dumps(event.to_meta())
    assert "sk-test-9999999999999999" not in blob
    assert "You are" not in blob


def test_finalize_never_persists_secrets() -> None:
    svc = ObservabilityService()
    svc.begin(agent_id="a1", cycle_id="c1")
    svc.record(
        "publishing_completed",
        stage="publishing",
        reason="published",
        evidence={"header": "Authorization: Bearer abc.def.ghi"},
    )
    payload = svc.finalize(
        cycle_status="success",
        publication={"status": "published", "reason": "ok sk-test-abcdef0123456789"},
    )
    blob = json.dumps(payload)
    assert "sk-test-abcdef0123456789" not in blob
    assert "Bearer abc.def.ghi" not in blob


def test_safe_record_failure_isolation() -> None:
    svc = ObservabilityService()
    svc.begin(agent_id="a1", cycle_id="c1")

    class Boom(ObservabilityService):
        def record(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("collector boom")

    boom = Boom()
    boom.begin(agent_id="a1", cycle_id="c1")
    assert boom.safe_record("research_started", stage="research") is None
    # Pipeline-facing finalize still returns a payload (or unavailable), never raises.
    payload = boom.meta_payload(cycle_status="success")
    assert "version" in payload or payload.get("status") == "unavailable"
