"""Login, token issuance and user administration."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select

from app.api.deps import CurrentUser, RequireAdmin, SessionDep, audit_request, get_user_by_email
from app.api.schemas.auth import LoginRequest, TokenResponse, UserCreate, UserOut
from app.config import settings
from app.core.errors import AuthError, ConflictError
from app.core.security import create_access_token, hash_password, verify_password
from app.db.base import utcnow
from app.db.models import User

router = APIRouter(prefix="/auth", tags=["auth"])


async def _issue_token(session, request: Request, email: str, password: str) -> TokenResponse:
    # #region agent log
    try:
        import json as _json, time as _time
        from pathlib import Path as _Path
        _Path(r"C:\Users\ROHIT\OneDrive\Desktop\HANUMAN\debug-41078f.log").open("a", encoding="utf-8").write(_json.dumps({"sessionId":"41078f","hypothesisId":"C","location":"auth.py:_issue_token","message":"login_enter","data":{"email_present":bool(email),"email_len":len(email or "")},"timestamp":int(_time.time()*1000)})+"\n")
    except Exception:
        pass
    # #endregion
    user = await get_user_by_email(session, email)
    # #region agent log
    try:
        import json as _json, time as _time
        from pathlib import Path as _Path
        _Path(r"C:\Users\ROHIT\OneDrive\Desktop\HANUMAN\debug-41078f.log").open("a", encoding="utf-8").write(_json.dumps({"sessionId":"41078f","hypothesisId":"C","location":"auth.py:_issue_token","message":"user_lookup","data":{"found":user is not None,"active":bool(user.is_active) if user else None},"timestamp":int(_time.time()*1000)})+"\n")
    except Exception:
        pass
    # #endregion
    if user is None or not verify_password(password, user.hashed_password):
        # Same message either way so the endpoint cannot be used to enumerate accounts.
        raise AuthError("Incorrect email or password")
    if not user.is_active:
        raise AuthError("Account is disabled")

    user.last_login_at = utcnow()
    token = create_access_token(user.id, role=str(user.role), extra={"email": user.email})
    await audit_request(
        session, request, user, action="auth.login", resource_type="user", resource_id=user.id
    )
    # #region agent log
    try:
        import json as _json, time as _time
        from pathlib import Path as _Path
        _Path(r"C:\Users\ROHIT\OneDrive\Desktop\HANUMAN\debug-41078f.log").open("a", encoding="utf-8").write(_json.dumps({"sessionId":"41078f","hypothesisId":"C","location":"auth.py:_issue_token","message":"login_success","data":{"user_id":user.id,"role":str(user.role)},"timestamp":int(_time.time()*1000)})+"\n")
    except Exception:
        pass
    # #endregion
    return TokenResponse(
        access_token=token,
        expires_in=settings.access_token_ttl_minutes * 60,
        user=UserOut.model_validate(user),
    )


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, request: Request, session: SessionDep) -> TokenResponse:
    return await _issue_token(session, request, payload.email, payload.password)


@router.post("/token", response_model=TokenResponse, include_in_schema=False)
async def login_form(
    request: Request,
    session: SessionDep,
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
) -> TokenResponse:
    """OAuth2 password flow, so the Swagger "Authorize" button works."""
    return await _issue_token(session, request, form.username, form.password)


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)


@router.get("/users", response_model=list[UserOut])
async def list_users(session: SessionDep, _: RequireAdmin) -> list[UserOut]:
    result = await session.execute(select(User).order_by(User.created_at))
    return [UserOut.model_validate(u) for u in result.scalars().all()]


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate, request: Request, session: SessionDep, admin: RequireAdmin
) -> UserOut:
    if await get_user_by_email(session, payload.email):
        raise ConflictError("A user with that email already exists")
    user = User(
        email=payload.email.lower().strip(),
        full_name=payload.full_name,
        hashed_password=hash_password(payload.password),
        role=payload.role,
        agency=payload.agency,
        badge_number=payload.badge_number,
    )
    session.add(user)
    await session.flush()
    await audit_request(
        session,
        request,
        admin,
        action="user.create",
        resource_type="user",
        resource_id=user.id,
        details={"role": str(user.role)},
    )
    return UserOut.model_validate(user)
