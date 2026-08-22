"""Generate, list and download investigation packets."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import desc, func, select

from app.api.deps import CurrentUser, SessionDep, audit_request
from app.api.schemas.common import Page
from app.api.schemas.reports import ReportGenerateRequest, ReportOut
from app.core.enums import JobStatus, ReportKind
from app.core.errors import NotFoundError, ValidationError
from app.db.models import Case, Report, TraceEdge, TraceJob, Vasp
from app.reports.builder import generate_report
from app.tracing.paths import read_attribution

router = APIRouter(prefix="/reports", tags=["reports"])


def _download_url(report_id: str) -> str:
    return f"/api/v1/reports/{report_id}/download"


def _out(row: Report) -> ReportOut:
    return ReportOut(
        id=row.id,
        case_id=row.case_id,
        job_id=row.job_id,
        kind=ReportKind(row.kind),
        filename=row.filename,
        content_type=row.content_type,
        sha256=row.sha256,
        size_bytes=row.size_bytes,
        generated_by_name=row.generated_by_name,
        sections=list(row.sections or []),
        custody_log=list(row.custody_log or []),
        created_at=row.created_at,
        download_url=_download_url(row.id),
    )


async def _latest_completed_job(session: SessionDep, case_id: str, job_id: str | None) -> TraceJob | None:
    if job_id:
        job = await session.get(TraceJob, job_id)
        if job is None or job.case_id not in (None, case_id):
            raise ValidationError("Trace job not found for this case")
        return job
    return (
        await session.execute(
            select(TraceJob)
            .where(TraceJob.case_id == case_id, TraceJob.status == JobStatus.COMPLETED)
            .order_by(desc(TraceJob.finished_at))
            .limit(1)
        )
    ).scalar_one_or_none()


@router.get("", response_model=Page[ReportOut])
async def list_reports(
    session: SessionDep,
    user: CurrentUser,
    case_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[ReportOut]:
    query = select(Report)
    if case_id:
        query = query.where(Report.case_id == case_id)
    total = (
        await session.execute(select(func.count()).select_from(query.subquery()))
    ).scalar_one()
    rows = (
        await session.execute(
            query.order_by(desc(Report.created_at)).limit(limit).offset(offset)
        )
    ).scalars().all()
    return Page(items=[_out(row) for row in rows], total=total, limit=limit, offset=offset)


@router.post("/{case_id}/generate", response_model=ReportOut)
async def generate_case_report(
    case_id: str,
    payload: ReportGenerateRequest,
    session: SessionDep,
    user: CurrentUser,
    request: Request,
) -> ReportOut:
    case = await session.get(Case, case_id)
    if case is None:
        raise NotFoundError(f"Case {case_id} not found")

    job = await _latest_completed_job(session, case_id, payload.job_id)
    finding = read_attribution(job.summary if job else None)
    vasp = None
    if finding.get("vasp_slug"):
        vasp = (
            await session.execute(select(Vasp).where(Vasp.slug == finding["vasp_slug"]))
        ).scalar_one_or_none()

    edges: list[TraceEdge] = []
    if job:
        edges = list(
            (
                await session.execute(
                    select(TraceEdge).where(TraceEdge.job_id == job.id)
                )
            ).scalars().all()
        )

    report = generate_report(
        kind=payload.kind,
        case=case,
        job=job,
        vasp=vasp,
        edges=edges,
        officer=user.full_name,
        officer_id=user.id,
        sections=payload.sections,
    )
    session.add(report)
    await session.flush()
    await audit_request(
        session,
        request,
        user,
        action="report.generate",
        resource_type="report",
        resource_id=report.id,
        details={"kind": payload.kind.value, "case_id": case_id, "sha256": report.sha256},
    )
    await session.commit()
    await session.refresh(report)
    return _out(report)


@router.get("/{report_id}", response_model=ReportOut)
async def get_report(report_id: str, session: SessionDep, user: CurrentUser) -> ReportOut:
    report = await session.get(Report, report_id)
    if report is None:
        raise NotFoundError(f"Report {report_id} not found")
    return _out(report)


@router.get("/{report_id}/download")
async def download_report(
    report_id: str,
    session: SessionDep,
    user: CurrentUser,
    request: Request,
) -> FileResponse:
    report = await session.get(Report, report_id)
    if report is None:
        raise NotFoundError(f"Report {report_id} not found")
    path = Path(report.storage_path)
    if not path.exists():
        raise NotFoundError("Report file is no longer on disk")

    log = list(report.custody_log or [])
    log.append(
        {
            "action": "downloaded",
            "actor": user.full_name,
            "at": datetime.now(UTC).isoformat(),
            "detail": f"Downloaded by {user.email}",
        }
    )
    report.custody_log = log
    await audit_request(
        session,
        request,
        user,
        action="report.download",
        resource_type="report",
        resource_id=report.id,
        details={"sha256": report.sha256},
    )
    await session.commit()
    return FileResponse(
        path,
        media_type=report.content_type,
        filename=report.filename,
        headers={"X-Content-SHA256": report.sha256},
    )
