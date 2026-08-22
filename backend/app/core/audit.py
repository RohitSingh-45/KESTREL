"""Audit trail writer.

Every read of a wallet or case and every state change is recorded, because a
crypto-tracing tool used by law enforcement must be able to answer "who looked
at what, and when".
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.db.models import AuditLog, User
from app.logging_config import get_logger

logger = get_logger(__name__)


async def record_audit(
    session: AsyncSession,
    *,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    actor: User | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditLog:
    entry = AuditLog(
        ts=utcnow(),
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else None,
        actor_role=str(actor.role) if actor else None,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        ip_address=ip_address,
        user_agent=(user_agent or "")[:255] or None,
        details=details,
    )
    session.add(entry)
    logger.info(
        "audit",
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        actor=entry.actor_email,
    )
    return entry
