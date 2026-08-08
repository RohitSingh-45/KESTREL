"""SQLAlchemy ORM models for Kestrel persistence."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    desc,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    last_cycle_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cycle_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    config_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    posts: Mapped[list[Post]] = relationship(back_populates="agent")
    memory_items: Mapped[list[MemoryItem]] = relationship(back_populates="agent")
    topics: Mapped[list[Topic]] = relationship(back_populates="agent")
    pipeline_runs: Mapped[list[PipelineRun]] = relationship(back_populates="agent")
    generation_lease: Mapped[GenerationLease | None] = relationship(back_populates="agent")


class Post(Base):
    __tablename__ = "posts"
    __table_args__ = (
        UniqueConstraint("agent_id", "content_hash", name="uq_posts_agent_content_hash"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    sources_json: Mapped[str] = mapped_column(Text, nullable=False)
    topic: Mapped[str | None] = mapped_column(String(512), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)

    agent: Mapped[Agent] = relationship(back_populates="posts")


Index(
    "ix_posts_agent_id_created_at",
    Post.agent_id,
    desc(Post.created_at),
)


class MemoryItem(Base):
    __tablename__ = "memory_items"
    __table_args__ = (Index("ix_memory_items_agent_id_kind", "agent_id", "kind"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    value_json: Mapped[str] = mapped_column(Text, nullable=False)
    salience: Mapped[float] = mapped_column(Float, nullable=False, default=0.5, server_default="0.5")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    agent: Mapped[Agent] = relationship(back_populates="memory_items")


class Topic(Base):
    __tablename__ = "topics"
    __table_args__ = (Index("ix_topics_agent_id_status", "agent_id", "status"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    # Module 20 — nullable so existing rows remain valid.
    ai_security_domain: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scores_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    debate_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    consistency_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    agent: Mapped[Agent] = relationship(back_populates="topics")


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    stage: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    agent: Mapped[Agent] = relationship(back_populates="pipeline_runs")


class GenerationLease(Base):
    __tablename__ = "generation_leases"

    agent_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("agents.id", ondelete="CASCADE"),
        primary_key=True,
    )
    lease_owner: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    agent: Mapped[Agent] = relationship(back_populates="generation_lease")
