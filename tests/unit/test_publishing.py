"""Unit tests for Module 10 PublishingService."""

from __future__ import annotations

from datetime import timezone

import pytest

from app.db.repositories.agents import AgentRepository
from app.db.repositories.posts import PostRepository
from app.db.session import get_session_factory
from app.orchestration.locks import utc_now
from app.services.final_post.models import GeneratedPost
from app.services.publishing import PublishingService, compute_content_hash, normalize_post_text


def _post(**overrides: object) -> GeneratedPost:
    base = {
        "text": "Prompt injection remains a concrete AI Security concern for agents.",
        "rationale": (
            "Selected for AI Security relevance and recent evidence over weaker alternatives."
        ),
        "source_urls": ["https://example.com/research"],
    }
    base.update(overrides)
    return GeneratedPost.model_validate(base)


async def _agent(agent_id: str = "pub-agent") -> str:
    factory = get_session_factory()
    async with factory() as session:
        now = utc_now()
        await AgentRepository(session).create_agent(
            agent_id=agent_id,
            name="Kestrel",
            domain="AI Security",
            status="running",
            created_at=now,
            updated_at=now,
        )
        await session.commit()
    return agent_id


@pytest.mark.asyncio
async def test_publisher_valid_post(engine) -> None:
    agent_id = await _agent("pub-valid")
    factory = get_session_factory()
    async with factory() as session:
        result = await PublishingService(session).publish(
            agent_id=agent_id,
            generated_post=_post(),
            grounded_urls=["https://example.com/research"],
            topic_title="Prompt injection",
            consistency_approved=True,
            final_validation_passed=True,
        )
        await session.commit()
        assert result.status == "published"
        assert result.post is not None
        assert result.post.id
        assert result.post.created_at.tzinfo is not None
        assert result.post.created_at.astimezone(timezone.utc).tzinfo is not None
        rows = await PostRepository(session).list_by_agent_newest_first(agent_id)
        assert len(rows) == 1


def test_content_hash_deterministic() -> None:
    a = compute_content_hash("Hello World", ["https://b.example/x", "https://a.example/y"])
    b = compute_content_hash("  hello   world ", ["https://a.example/y", "https://b.example/x"])
    assert a == b
    assert normalize_post_text("  A  B ") == "a b"


@pytest.mark.asyncio
async def test_unique_ids_and_duplicate_protection(engine) -> None:
    agent_id = await _agent("pub-dup")
    factory = get_session_factory()
    async with factory() as session:
        pub = PublishingService(session)
        first = await pub.publish(
            agent_id=agent_id,
            generated_post=_post(),
            grounded_urls=["https://example.com/research"],
            consistency_approved=True,
            final_validation_passed=True,
        )
        second = await pub.publish(
            agent_id=agent_id,
            generated_post=_post(),
            grounded_urls=["https://example.com/research"],
            consistency_approved=True,
            final_validation_passed=True,
        )
        other = await pub.publish(
            agent_id=agent_id,
            generated_post=_post(text="Different AI Security post about jailbreaks."),
            grounded_urls=["https://example.com/research"],
            consistency_approved=True,
            final_validation_passed=True,
        )
        await session.commit()
        assert first.status == "published"
        assert second.status == "duplicate"
        assert other.status == "published"
        assert first.post_id != other.post_id
        rows = await PostRepository(session).list_by_agent_newest_first(agent_id)
        assert len(rows) == 2


@pytest.mark.asyncio
async def test_invalid_source_and_empty_fields_rejected(engine) -> None:
    agent_id = await _agent("pub-reject")
    factory = get_session_factory()
    async with factory() as session:
        pub = PublishingService(session)
        bad_source = await pub.publish(
            agent_id=agent_id,
            generated_post=_post(source_urls=["https://fake.example/x"]),
            grounded_urls=["https://example.com/research"],
            consistency_approved=True,
            final_validation_passed=True,
        )
        assert bad_source.status == "rejected"
        assert bad_source.reason == "ungrounded_source_url"

        no_consistency = await pub.publish(
            agent_id=agent_id,
            generated_post=_post(),
            grounded_urls=["https://example.com/research"],
            consistency_approved=False,
            final_validation_passed=True,
        )
        assert no_consistency.status == "rejected"
        assert no_consistency.reason == "consistency_not_approved"

        no_final = await pub.publish(
            agent_id=agent_id,
            generated_post=_post(),
            grounded_urls=["https://example.com/research"],
            consistency_approved=True,
            final_validation_passed=False,
        )
        assert no_final.status == "rejected"
        await session.commit()
        assert await PostRepository(session).list_by_agent_newest_first(agent_id) == []


@pytest.mark.asyncio
async def test_empty_text_and_rationale_rejected(engine) -> None:
    agent_id = await _agent("pub-empty")
    factory = get_session_factory()
    async with factory() as session:
        pub = PublishingService(session)
        # Construct via model_construct to bypass pydantic min length for gate testing.
        empty_text = GeneratedPost.model_construct(
            text="",
            rationale="Selected with evidence for AI Security.",
            source_urls=["https://example.com/research"],
        )
        empty_rationale = GeneratedPost.model_construct(
            text="AI Security note",
            rationale="",
            source_urls=["https://example.com/research"],
        )
        r1 = await pub.publish(
            agent_id=agent_id,
            generated_post=empty_text,
            grounded_urls=["https://example.com/research"],
            consistency_approved=True,
            final_validation_passed=True,
        )
        r2 = await pub.publish(
            agent_id=agent_id,
            generated_post=empty_rationale,
            grounded_urls=["https://example.com/research"],
            consistency_approved=True,
            final_validation_passed=True,
        )
        assert r1.status == "rejected" and r1.reason == "empty_text"
        assert r2.status == "rejected" and r2.reason == "empty_rationale"
        await session.commit()


@pytest.mark.asyncio
async def test_agent_isolation(engine) -> None:
    a = await _agent("pub-a")
    b = await _agent("pub-b")
    factory = get_session_factory()
    async with factory() as session:
        pub = PublishingService(session)
        await pub.publish(
            agent_id=a,
            generated_post=_post(),
            grounded_urls=["https://example.com/research"],
            consistency_approved=True,
            final_validation_passed=True,
        )
        await session.commit()
        assert len(await PostRepository(session).list_by_agent_newest_first(a)) == 1
        assert await PostRepository(session).list_by_agent_newest_first(b) == []


@pytest.mark.asyncio
async def test_transaction_rollback_on_db_failure(engine, monkeypatch) -> None:
    agent_id = await _agent("pub-rollback")
    factory = get_session_factory()
    async with factory() as session:
        pub = PublishingService(session)

        async def boom(**_kwargs: object) -> object:
            raise RuntimeError("db write failed")

        monkeypatch.setattr(pub._posts, "create_post", boom)
        result = await pub.publish(
            agent_id=agent_id,
            generated_post=_post(),
            grounded_urls=["https://example.com/research"],
            consistency_approved=True,
            final_validation_passed=True,
        )
        assert result.status == "failed"
        await session.rollback()
        assert await PostRepository(session).list_by_agent_newest_first(agent_id) == []


@pytest.mark.asyncio
async def test_concurrent_publish_same_content(engine) -> None:
    import asyncio

    agent_id = await _agent("pub-concurrent")
    factory = get_session_factory()

    async def attempt() -> str:
        async with factory() as session:
            result = await PublishingService(session).publish(
                agent_id=agent_id,
                generated_post=_post(),
                grounded_urls=["https://example.com/research"],
                consistency_approved=True,
                final_validation_passed=True,
            )
            await session.commit()
            return result.status

    statuses = await asyncio.gather(attempt(), attempt())
    assert "published" in statuses
    assert statuses.count("published") == 1
    assert all(s in {"published", "duplicate"} for s in statuses)
    async with factory() as session:
        assert len(await PostRepository(session).list_by_agent_newest_first(agent_id)) == 1
