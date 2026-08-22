"""Shared FastAPI dependencies: sessions, current user, role gates."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.audit import record_audit
from app.core.enums import UserRole
from app.core.errors import AuthError, PermissionError_
from app.core.security import decode_access_token
from app.db.models import User
from app.db.session import get_session

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.api_prefix}/auth/token", auto_error=False)

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_current_user(
    session: SessionDep,
    token: Annotated[str | None, Depends(oauth2_scheme)] = None,
) -> User:
    if not token:
        raise AuthError("Authentication required")
    payload = decode_access_token(token)
    user_id = payload.get("sub")
    if not user_id:
        raise AuthError("Malformed token")
    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise AuthError("User no longer active")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_role(minimum: UserRole) -> Callable[..., object]:
    """Dependency factory enforcing a minimum role rank."""

    async def _guard(user: CurrentUser) -> User:
        if UserRole(user.role).rank < minimum.rank:
            raise PermissionError_(
                f"This action requires the {minimum.value} role or higher"
            )
        return user

    return _guard


RequireSupervisor = Annotated[User, Depends(require_role(UserRole.SUPERVISOR))]
RequireAdmin = Annotated[User, Depends(require_role(UserRole.ADMIN))]


async def audit_request(
    session: AsyncSession,
    request: Request,
    user: User | None,
    *,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    details: dict | None = None,
) -> None:
    await record_audit(
        session,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        actor=user,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        details=details,
    )


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == email.lower().strip()))
    return result.scalar_one_or_none()
