"""Aggregates every v1 router."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.routes import (
    alerts,
    analytics,
    attribution,
    auth,
    cases,
    health,
    integrations,
    reports,
    trace,
    vasps,
    wallets,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(cases.router)
api_router.include_router(trace.router)
api_router.include_router(wallets.router)
api_router.include_router(attribution.router)
api_router.include_router(alerts.router)
api_router.include_router(vasps.router)
api_router.include_router(reports.router)
api_router.include_router(analytics.router)
api_router.include_router(integrations.router)
