"""Case intake and management - where a complaint becomes an investigation."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime

from fastapi import APIRouter, File, Query, Request, UploadFile, status
from sqlalchemy import desc, func, select

from app.api.deps import CurrentUser, SessionDep, audit_request
from app.api.schemas.cases import (
    BulkIntakeResult,
    BulkIntakeRow,
    CaseAddressOut,
    CaseCreate,
    CaseOut,
    CaseSummary,
    CaseUpdate,
)
from app.api.schemas.common import Page
from app.api.schemas.trace import GraphEdge, GraphNode, TraceGraphOut
from app.chains.resolver import resolve
from app.core.enums import (
    AlertStatus,
    CasePriority,
    CaseStatus,
    Chain,
    FraudCategory,
    JobStatus,
)
from app.core.errors import NotFoundError, ValidationError
from app.db.models import Alert, Case, CaseAddress, TraceJob, TraceNode, User
from app.db.models import TraceEdge as TraceEdgeRow
from app.logging_config import get_logger
from app.tracing.models import TERMINAL_EXPLANATION
from app.tracing.paths import read_attribution, read_risk

logger = get_logger(__name__)

router = APIRouter(prefix="/cases", tags=["cases"])

MAX_CSV_BYTES = 5 * 1024 * 1024
MAX_CSV_ROWS = 2000

# NCRP exports are not a fixed schema in practice, so accept the column names
# that actually turn up rather than demanding one spelling.
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "address": ("address", "wallet", "wallet_address", "suspect_wallet", "crypto_address", "upi_or_wallet"),
    "chain": ("chain", "network", "blockchain", "coin"),
    "case_number": ("case_number", "case_no", "fir_no", "fir_number", "complaint_id"),
    "ncrp_ack_no": ("ncrp_ack_no", "ack_no", "acknowledgement_no", "ncrp_id"),
    "title": ("title", "subject", "complaint_title"),
    "category": ("category", "fraud_category", "crime_type", "complaint_type"),
    "victim_name": ("victim_name", "complainant", "complainant_name", "victim"),
    "victim_contact": ("victim_contact", "contact", "mobile", "phone", "email"),
    "loss_amount_inr": ("loss_amount_inr", "loss_amount", "amount", "amount_inr", "fraud_amount"),
    "amount_sent_inr": ("amount_sent_inr", "transaction_amount", "amount_transferred"),
    "incident_date": ("incident_date", "date_of_incident", "transaction_date", "date"),
    "police_station": ("police_station", "ps", "station"),
    "state": ("state", "state_ut"),
    "description": ("description", "remarks", "details", "narration"),
}

CATEGORY_KEYWORDS: dict[FraudCategory, tuple[str, ...]] = {
    FraudCategory.INVESTMENT_SCAM: ("investment", "trading", "ponzi", "fake exchange"),
    FraudCategory.TASK_BASED_FRAUD: ("task", "part time", "part-time", "job fraud"),
    FraudCategory.SEXTORTION: ("sextortion", "extortion", "nude", "blackmail"),
    FraudCategory.RANSOMWARE: ("ransom", "encrypt", "locker"),
    FraudCategory.PHISHING: ("phishing", "otp", "kyc update", "impersonat"),
    FraudCategory.DARKNET: ("darknet", "dark web", "narcotic", "drug"),
    FraudCategory.ORGANISED_CYBER_CRIME: ("organised", "organized", "syndicate", "gang"),
}


def _classify_category(text: str | None) -> FraudCategory:
    if not text:
        return FraudCategory.OTHER
    lowered = text.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            return category
    return FraudCategory.OTHER


def _pick(row: dict[str, str], field: str) -> str | None:
    for alias in COLUMN_ALIASES[field]:
        value = row.get(alias)
        if value and value.strip():
            return value.strip()
    return None


def _parse_float(value: str | None) -> float | None:
    if not value:
        return None
    cleaned = value.replace(",", "").replace("₹", "").replace("Rs.", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(value.strip(), fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


async def _next_case_number(session: SessionDep) -> str:
    """Sequential per-year reference, e.g. CFA-2026-0007."""
    year = datetime.now(UTC).year
    prefix = f"CFA-{year}-"
    used = (
        await session.execute(
            select(Case.case_number).where(Case.case_number.like(f"{prefix}%"))
        )
    ).scalars().all()
    highest = 0
    for number in used:
        tail = number.rsplit("-", 1)[-1]
        if tail.isdigit():
            highest = max(highest, int(tail))
    return f"{prefix}{highest + 1:04d}"


async def _load_case(session: SessionDep, case_id: str) -> Case:
    case = await session.get(Case, case_id)
    if case is None:
        raise NotFoundError(f"Case {case_id} not found")
    return case


async def _enrich_addresses(
    session: SessionDep, case: Case
) -> list[CaseAddressOut]:
    """Attach the latest trace outcome to each reported address."""
    out: list[CaseAddressOut] = []
    jobs = (
        await session.execute(
            select(TraceJob)
            .where(TraceJob.case_id == case.id)
            .order_by(desc(TraceJob.created_at))
        )
    ).scalars().all()

    latest: dict[tuple[str, str], TraceJob] = {}
    for job in jobs:
        latest.setdefault((str(job.chain), job.seed_address), job)

    for row in case.addresses:
        job = latest.get((str(row.chain), row.address))
        summary = (job.summary or {}) if job else {}
        finding = read_attribution(summary)
        out.append(
            CaseAddressOut(
                id=row.id,
                chain=Chain(row.chain),
                address=row.address,
                role=row.role,
                amount_sent_inr=row.amount_sent_inr,
                note=row.note,
                last_job_id=job.id if job else None,
                last_job_status=str(job.status) if job else None,
                attributed_vasp=finding.get("entity_name"),
                risk_level=read_risk(summary).get("level"),
            )
        )
    return out


async def _case_out(session: SessionDep, case: Case) -> CaseOut:
    alert_rows = (
        await session.execute(
            select(Alert.status, func.count()).where(Alert.case_id == case.id).group_by(Alert.status)
        )
    ).all()
    total_alerts = sum(count for _, count in alert_rows)
    open_alerts = sum(
        count for alert_status, count in alert_rows if alert_status == AlertStatus.NEW
    )
    addresses = await _enrich_addresses(session, case)
    return CaseOut(
        id=case.id,
        case_number=case.case_number,
        ncrp_ack_no=case.ncrp_ack_no,
        title=case.title,
        description=case.description,
        fraud_category=FraudCategory(case.fraud_category),
        status=CaseStatus(case.status),
        priority=CasePriority(case.priority),
        victim_name=case.victim_name,
        victim_contact=case.victim_contact,
        loss_amount_inr=case.loss_amount_inr,
        incident_date=case.incident_date,
        reported_date=case.reported_date,
        police_station=case.police_station,
        state=case.state,
        intake_source=case.intake_source,
        assigned_to_name=case.assigned_to.full_name if case.assigned_to else None,
        created_at=case.created_at,
        updated_at=case.updated_at,
        addresses=addresses,
        address_count=len(addresses),
        alert_count=total_alerts,
        open_alert_count=open_alerts,
    )


@router.post("", response_model=CaseOut, status_code=status.HTTP_201_CREATED)
async def create_case(
    payload: CaseCreate,
    session: SessionDep,
    user: CurrentUser,
    request: Request,
) -> CaseOut:
    """Open a case from a complaint, auto-detecting the chain of each address."""
    case_number = payload.case_number or await _next_case_number(session)
    existing = (
        await session.execute(select(Case).where(Case.case_number == case_number))
    ).scalar_one_or_none()
    if existing is not None:
        raise ValidationError(f"Case number {case_number} already exists")

    case = Case(
        case_number=case_number,
        ncrp_ack_no=payload.ncrp_ack_no,
        title=payload.title,
        description=payload.description,
        fraud_category=payload.fraud_category,
        priority=payload.priority,
        status=CaseStatus.OPEN,
        victim_name=payload.victim_name,
        victim_contact=payload.victim_contact,
        loss_amount_inr=payload.loss_amount_inr,
        incident_date=payload.incident_date,
        reported_date=datetime.now(UTC),
        police_station=payload.police_station,
        state=payload.state,
        intake_source=payload.intake_source,
        assigned_to_id=user.id,
    )
    session.add(case)
    await session.flush()

    seen: set[tuple[str, str]] = set()
    for entry in payload.addresses:
        resolved = resolve(entry.address, entry.chain)
        key = (resolved.chain.value, resolved.address)
        if key in seen:
            continue
        seen.add(key)
        session.add(
            CaseAddress(
                case_id=case.id,
                chain=resolved.chain,
                address=resolved.address,
                role=entry.role,
                amount_sent_inr=entry.amount_sent_inr,
                note=entry.note,
            )
        )

    await audit_request(
        session,
        request,
        user,
        action="case.create",
        resource_type="case",
        resource_id=case.id,
        details={"case_number": case_number, "addresses": len(seen)},
    )
    await session.commit()
    await session.refresh(case)
    return await _case_out(session, case)


@router.get("", response_model=Page[CaseSummary])
async def list_cases(
    session: SessionDep,
    user: CurrentUser,
    case_status: CaseStatus | None = Query(default=None, alias="status"),
    category: FraudCategory | None = None,
    priority: CasePriority | None = None,
    q: str | None = Query(default=None, description="Match case number, title or address"),
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[CaseSummary]:
    query = select(Case)
    if case_status:
        query = query.where(Case.status == case_status)
    if category:
        query = query.where(Case.fraud_category == category)
    if priority:
        query = query.where(Case.priority == priority)
    if q:
        term = f"%{q.strip()}%"
        matching_cases = select(CaseAddress.case_id).where(CaseAddress.address.ilike(term))
        query = query.where(
            Case.case_number.ilike(term)
            | Case.title.ilike(term)
            | Case.ncrp_ack_no.ilike(term)
            | Case.id.in_(matching_cases)
        )

    total = (
        await session.execute(select(func.count()).select_from(query.subquery()))
    ).scalar_one()
    rows = (
        await session.execute(
            query.order_by(desc(Case.created_at)).limit(limit).offset(offset)
        )
    ).scalars().all()

    case_ids = [row.id for row in rows]
    findings = await _latest_findings(session, case_ids)
    items = [
        CaseSummary(
            id=row.id,
            case_number=row.case_number,
            title=row.title,
            fraud_category=FraudCategory(row.fraud_category),
            status=CaseStatus(row.status),
            priority=CasePriority(row.priority),
            loss_amount_inr=row.loss_amount_inr,
            state=row.state,
            created_at=row.created_at,
            address_count=len(row.addresses),
            top_vasp=findings.get(row.id, {}).get("vasp"),
            highest_risk=findings.get(row.id, {}).get("risk"),
        )
        for row in rows
    ]
    return Page(items=items, total=total, limit=limit, offset=offset)


async def _latest_findings(
    session: SessionDep, case_ids: list[str]
) -> dict[str, dict[str, str | None]]:
    """Best attribution and worst risk seen across each case's traces."""
    if not case_ids:
        return {}
    order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    jobs = (
        await session.execute(
            select(TraceJob)
            .where(TraceJob.case_id.in_(case_ids), TraceJob.status == JobStatus.COMPLETED)
            .order_by(desc(TraceJob.created_at))
        )
    ).scalars().all()

    out: dict[str, dict[str, str | None]] = {}
    for job in jobs:
        if not job.case_id:
            continue
        summary = job.summary or {}
        entry = out.setdefault(job.case_id, {"vasp": None, "risk": None})
        finding = read_attribution(summary)
        if entry["vasp"] is None and finding.get("entity_name"):
            entry["vasp"] = finding["entity_name"]
        level = read_risk(summary).get("level")
        if level and order.get(level, 0) > order.get(entry["risk"] or "low", -1):
            entry["risk"] = level
    return out


@router.get("/{case_id}", response_model=CaseOut)
async def get_case(
    case_id: str, session: SessionDep, user: CurrentUser, request: Request
) -> CaseOut:
    case = await _load_case(session, case_id)
    await audit_request(
        session, request, user, action="case.read", resource_type="case", resource_id=case.id
    )
    await session.commit()
    return await _case_out(session, case)


@router.patch("/{case_id}", response_model=CaseOut)
async def update_case(
    case_id: str,
    payload: CaseUpdate,
    session: SessionDep,
    user: CurrentUser,
    request: Request,
) -> CaseOut:
    case = await _load_case(session, case_id)
    changes = payload.model_dump(exclude_unset=True)
    if "assigned_to_id" in changes and changes["assigned_to_id"]:
        if await session.get(User, changes["assigned_to_id"]) is None:
            raise ValidationError("Assignee does not exist")
    for field, value in changes.items():
        setattr(case, field, value)

    await audit_request(
        session,
        request,
        user,
        action="case.update",
        resource_type="case",
        resource_id=case.id,
        details=changes,
    )
    await session.commit()
    await session.refresh(case)
    return await _case_out(session, case)


@router.post("/{case_id}/addresses", response_model=CaseOut)
async def add_case_address(
    case_id: str,
    payload: list[str],
    session: SessionDep,
    user: CurrentUser,
    request: Request,
) -> CaseOut:
    case = await _load_case(session, case_id)
    known = {(str(row.chain), row.address) for row in case.addresses}
    added = 0
    for raw in payload:
        resolved = resolve(raw, None)
        key = (resolved.chain.value, resolved.address)
        if key in known:
            continue
        known.add(key)
        session.add(
            CaseAddress(case_id=case.id, chain=resolved.chain, address=resolved.address)
        )
        added += 1

    await audit_request(
        session,
        request,
        user,
        action="case.address.add",
        resource_type="case",
        resource_id=case.id,
        details={"added": added},
    )
    await session.commit()
    await session.refresh(case)
    return await _case_out(session, case)


@router.get("/{case_id}/graph", response_model=TraceGraphOut)
async def get_case_graph(
    case_id: str,
    session: SessionDep,
    user: CurrentUser,
    min_taint: float = Query(default=0.0, ge=0.0, le=1.0),
) -> TraceGraphOut:
    """Every trace on the case merged into one canvas.

    Cases regularly have several reported addresses that turn out to converge on
    the same launderer, and that convergence is only visible once the separate
    traces are drawn together.
    """
    case = await _load_case(session, case_id)
    jobs = (
        await session.execute(
            select(TraceJob)
            .where(TraceJob.case_id == case_id, TraceJob.status == JobStatus.COMPLETED)
            .order_by(TraceJob.created_at)
        )
    ).scalars().all()
    if not jobs:
        raise NotFoundError("No completed traces for this case yet")

    job_ids = [job.id for job in jobs]
    node_query = select(TraceNode).where(TraceNode.job_id.in_(job_ids))
    if min_taint > 0:
        node_query = node_query.where(TraceNode.taint >= min_taint)
    node_rows = (await session.execute(node_query)).scalars().all()
    edge_rows = (
        await session.execute(select(TraceEdgeRow).where(TraceEdgeRow.job_id.in_(job_ids)))
    ).scalars().all()

    # The same address can appear in two traces; keep the strongest view of it.
    best: dict[str, TraceNode] = {}
    for row in node_rows:
        key = f"{row.chain}:{row.address}"
        current = best.get(key)
        if current is None or row.taint > current.taint:
            best[key] = row

    nodes = [
        GraphNode(
            id=f"{row.chain}:{row.address}",
            address=row.address,
            chain=Chain(row.chain),
            hop=row.hop,
            taint=row.taint,
            entity_type=str(row.entity_type),
            entity_name=row.entity_name,
            vasp_slug=row.vasp_slug,
            attribution_method=row.attribution_method,
            attribution_confidence=row.attribution_confidence,
            risk_score=row.risk_score,
            risk_level=str(row.risk_level),
            typologies=row.typologies or [],
            value_in_usd=row.value_in_usd,
            value_out_usd=row.value_out_usd,
            is_seed=row.is_seed,
            is_terminal=row.is_terminal,
            is_sanctioned=bool((row.details or {}).get("is_sanctioned")),
            terminal_reason=(row.details or {}).get("terminal_reason"),
            terminal_explanation=TERMINAL_EXPLANATION.get(
                (row.details or {}).get("terminal_reason") or ""
            ),
            notes=(row.details or {}).get("notes") or [],
            first_seen=row.first_seen,
        )
        for row in best.values()
    ]
    present = set(best)
    seen_edges: set[str] = set()
    edges: list[GraphEdge] = []
    for row in edge_rows:
        source = f"{row.chain}:{row.src_address}"
        target = f"{row.chain}:{row.dst_address}"
        if source not in present or target not in present:
            continue
        key = f"{row.tx_hash}:{source}:{target}"
        if key in seen_edges:
            continue
        seen_edges.add(key)
        edges.append(
            GraphEdge(
                id=row.id,
                source=source,
                target=target,
                chain=Chain(row.chain),
                tx_hash=row.tx_hash,
                ts=row.ts,
                block_number=row.block_number,
                asset=row.asset,
                amount=str(row.amount),
                amount_usd=row.amount_usd,
                hop=row.hop,
                taint_share=row.taint_share,
                is_on_primary_path=row.is_on_primary_path,
                edge_kind=row.edge_kind,
            )
        )

    return TraceGraphOut(
        job_id=job_ids[-1],
        chain=Chain(jobs[0].chain),
        seed_address=case.addresses[0].address if case.addresses else jobs[0].seed_address,
        status=JobStatus.COMPLETED,
        nodes=nodes,
        edges=edges,
        summary={"merged_jobs": job_ids},
        data_source=jobs[-1].data_source,
    )


@router.post("/import", response_model=BulkIntakeResult)
async def import_cases_csv(
    session: SessionDep,
    user: CurrentUser,
    request: Request,
    file: UploadFile = File(...),
    dry_run: bool = Query(default=False, description="Validate without writing"),
    group_by_case: bool = Query(
        default=True, description="Group rows sharing a case number into one case"
    ),
) -> BulkIntakeResult:
    """Bulk intake from an NCRP-style CSV export.

    Rows are validated one at a time and a bad row never sinks the batch - the
    caller gets a per-row verdict so a clerk can fix ten typos rather than
    re-uploading four hundred records.
    """
    raw = await file.read()
    if len(raw) > MAX_CSV_BYTES:
        raise ValidationError("CSV is larger than the 5 MB limit")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValidationError("CSV has no header row")
    reader.fieldnames = [(name or "").strip().lower().replace(" ", "_") for name in reader.fieldnames]

    results: list[BulkIntakeRow] = []
    grouped: dict[str, dict] = {}
    total = 0

    for index, raw_row in enumerate(reader, start=2):
        total += 1
        if total > MAX_CSV_ROWS:
            raise ValidationError(f"CSV exceeds the {MAX_CSV_ROWS} row limit")
        row = {k: (v or "") for k, v in raw_row.items() if k}
        address = _pick(row, "address")
        if not address:
            results.append(BulkIntakeRow(row=index, ok=False, error="No wallet address column found"))
            continue
        try:
            resolved = resolve(address, _pick(row, "chain"))
        except Exception as exc:  # noqa: BLE001 - one bad row must not fail the batch
            results.append(
                BulkIntakeRow(row=index, ok=False, address=address, error=str(exc))
            )
            continue

        case_number = _pick(row, "case_number")
        title = _pick(row, "title")
        description = _pick(row, "description")
        key = case_number if (group_by_case and case_number) else f"__row_{index}"
        bucket = grouped.setdefault(
            key,
            {
                "case_number": case_number,
                "ncrp_ack_no": _pick(row, "ncrp_ack_no"),
                "title": title or f"Complaint {case_number or index}",
                "description": description,
                "category": _classify_category(
                    " ".join(filter(None, [_pick(row, "category"), title, description]))
                ),
                "victim_name": _pick(row, "victim_name"),
                "victim_contact": _pick(row, "victim_contact"),
                "loss_amount_inr": _parse_float(_pick(row, "loss_amount_inr")),
                "incident_date": _parse_date(_pick(row, "incident_date")),
                "police_station": _pick(row, "police_station"),
                "state": _pick(row, "state"),
                "addresses": [],
            },
        )
        bucket["addresses"].append(
            (resolved.chain, resolved.address, _parse_float(_pick(row, "amount_sent_inr")))
        )
        results.append(
            BulkIntakeRow(
                row=index,
                ok=True,
                address=resolved.address,
                chain=resolved.chain,
                case_number=case_number,
                title=bucket["title"],
            )
        )

    accepted = sum(1 for row in results if row.ok)
    if dry_run:
        return BulkIntakeResult(
            filename=file.filename or "upload.csv",
            total_rows=total,
            accepted=accepted,
            rejected=total - accepted,
            cases_created=0,
            addresses_added=0,
            rows=results,
            dry_run=True,
        )

    created_ids: list[str] = []
    addresses_added = 0
    for bucket in grouped.values():
        case_number = bucket["case_number"] or await _next_case_number(session)
        case = (
            await session.execute(select(Case).where(Case.case_number == case_number))
        ).scalar_one_or_none()
        if case is None:
            case = Case(
                case_number=case_number,
                ncrp_ack_no=bucket["ncrp_ack_no"],
                title=bucket["title"],
                description=bucket["description"],
                fraud_category=bucket["category"],
                status=CaseStatus.OPEN,
                priority=CasePriority.NORMAL,
                victim_name=bucket["victim_name"],
                victim_contact=bucket["victim_contact"],
                loss_amount_inr=bucket["loss_amount_inr"],
                incident_date=bucket["incident_date"],
                reported_date=datetime.now(UTC),
                police_station=bucket["police_station"],
                state=bucket["state"],
                intake_source="ncrp_csv",
                assigned_to_id=user.id,
            )
            session.add(case)
            await session.flush()
            created_ids.append(case.id)

        known = {
            (str(existing.chain), existing.address)
            for existing in (
                await session.execute(
                    select(CaseAddress).where(CaseAddress.case_id == case.id)
                )
            ).scalars().all()
        }
        for chain, address, amount in bucket["addresses"]:
            if (chain.value, address) in known:
                continue
            known.add((chain.value, address))
            session.add(
                CaseAddress(
                    case_id=case.id,
                    chain=chain,
                    address=address,
                    amount_sent_inr=amount,
                    note="Imported from NCRP CSV",
                )
            )
            addresses_added += 1

    await audit_request(
        session,
        request,
        user,
        action="case.import",
        resource_type="case",
        details={
            "filename": file.filename,
            "rows": total,
            "cases": len(created_ids),
            "addresses": addresses_added,
        },
    )
    await session.commit()

    logger.info(
        "csv_intake_complete",
        rows=total,
        accepted=accepted,
        cases=len(created_ids),
        addresses=addresses_added,
    )
    return BulkIntakeResult(
        filename=file.filename or "upload.csv",
        total_rows=total,
        accepted=accepted,
        rejected=total - accepted,
        cases_created=len(created_ids),
        addresses_added=addresses_added,
        case_ids=created_ids,
        rows=results,
    )


@router.post("/import/ncrp-csv", response_model=BulkIntakeResult, include_in_schema=False)
async def import_cases_csv_alias(
    session: SessionDep,
    user: CurrentUser,
    request: Request,
    file: UploadFile = File(...),
    dry_run: bool = Query(default=False),
    group_by_case: bool = Query(default=True),
) -> BulkIntakeResult:
    return await import_cases_csv(
        session, user, request, file=file, dry_run=dry_run, group_by_case=group_by_case
    )
