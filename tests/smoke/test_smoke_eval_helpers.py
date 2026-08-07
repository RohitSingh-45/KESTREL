"""Unit coverage for smoke_eval contract helpers (no live server)."""

from __future__ import annotations

import pytest

from scripts.smoke_eval import validate_agent_id, validate_posts, _assert_no_secrets


def test_validate_agent_id_accepts_uuid() -> None:
    validate_agent_id("123e4567-e89b-12d3-a456-426614174000")


def test_validate_agent_id_rejects_garbage() -> None:
    with pytest.raises(AssertionError):
        validate_agent_id("not-a-uuid")


def test_validate_posts_newest_first_and_persistence() -> None:
    posts = [
        {
            "id": "p2",
            "createdAt": "2026-08-08T12:00:00Z",
            "text": "newer",
            "rationale": "why",
            "sources": ["https://example.com/a"],
        },
        {
            "id": "p1",
            "createdAt": "2026-08-08T11:00:00Z",
            "text": "older",
            "rationale": "why",
            "sources": ["https://example.com/b"],
        },
    ]
    validate_posts(posts, seen_ids={"p1"})
    with pytest.raises(AssertionError):
        validate_posts(posts[:1], seen_ids={"p1", "p2"})


def test_assert_no_secrets_blocks_api_key_material() -> None:
    with pytest.raises(AssertionError):
        _assert_no_secrets("leak sk-abcdefghijklmnopqrstuvwxyz0123456789")
