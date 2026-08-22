"""Turns trace findings into alerts with SLA clocks.

An alert exists to make someone act before a window closes, so each one carries
the deadline that matters: the VASP's own stated response SLA for a freeze
request, a short fuse when funds are still moving, and no clock at all for
findings that are informational.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AlertType, Chain, RiskLevel, Typology
from app.db.models import Alert, Vasp
from app.logging_config import get_logger
from app.risk.scorer import RECOMMENDED_ACTION, RiskAssessment
from app.risk.typologies import TypologyHit
from app.tracing.crosschain import CrossChainLink
from app.tracing.models import TerminalReason, TraceGraph

logger = get_logger(__name__)

# Funds move fast; an alert about active movement is worthless a day later.
RAPID_MOVEMENT_SLA_HOURS = 6
SANCTIONS_SLA_HOURS = 12
DEFAULT_SLA_HOURS = 48

HIGH_VALUE_USD = 100_000


@dataclass(slots=True)
class DraftAlert:
    alert_type: AlertType
    severity: RiskLevel
    title: str
    message: str
    recommended_action: str
    chain: Chain | None = None
    address: str | None = None
    vasp_slug: str | None = None
    sla_hours: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)


async def _vasp_directory(session: AsyncSession) -> dict[str, Vasp]:
    rows = (await session.execute(select(Vasp))).scalars().all()
    return {row.slug: row for row in rows}


def _severity_for_share(taint: float) -> RiskLevel:
    if taint >= 0.5:
        return RiskLevel.CRITICAL
    if taint >= 0.2:
        return RiskLevel.HIGH
    if taint >= 0.05:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def build_alerts(
    graph: TraceGraph,
    hits: list[TypologyHit],
    risk: RiskAssessment,
    links: list[CrossChainLink],
    vasps: dict[str, Vasp],
) -> list[DraftAlert]:
    drafts: list[DraftAlert] = []

    # --- an exchange was identified: the actionable one -------------------
    for node in sorted(graph.vasp_endpoints(), key=lambda n: n.taint, reverse=True)[:5]:
        vasp = vasps.get(node.vasp_slug or "")
        name = vasp.name if vasp else (node.entity_name or node.vasp_slug or "an exchange")
        sla = vasp.response_sla_hours if vasp else DEFAULT_SLA_HOURS
        emergency = (
            " This VASP accepts emergency freeze requests."
            if vasp and vasp.accepts_emergency_freeze
            else ""
        )
        contact = (
            f" Compliance contact: {vasp.compliance_email}."
            if vasp and vasp.compliance_email
            else ""
        )
        drafts.append(
            DraftAlert(
                alert_type=AlertType.VASP_IDENTIFIED,
                severity=_severity_for_share(node.taint),
                title=f"{name} deposit address identified at hop {node.hop}",
                message=(
                    f"{node.taint:.0%} of the traced value reached {node.address}, "
                    f"attributed to {name} with {node.attribution_confidence:.0%} "
                    f"confidence.{contact}{emergency}"
                ),
                recommended_action=(
                    f"Serve a Section 94 BNSS information request on {name} for the KYC "
                    f"record and account statement behind {node.address}, and request an "
                    "immediate hold."
                ),
                chain=node.chain,
                address=node.address,
                vasp_slug=node.vasp_slug,
                sla_hours=sla,
                payload={
                    "hop": node.hop,
                    "taint": round(node.taint, 6),
                    "value_usd": round(node.value_in_usd, 2),
                    "confidence": round(node.attribution_confidence, 4),
                    "fiu_ind_registered": bool(vasp and vasp.fiu_ind_registered),
                },
            )
        )

    # --- sanctions --------------------------------------------------------
    sanctions_hit = next(
        (h for h in hits if h.typology is Typology.SANCTIONS_EXPOSURE), None
    )
    if sanctions_hit:
        drafts.append(
            DraftAlert(
                alert_type=AlertType.SANCTIONS_HIT,
                severity=RiskLevel.CRITICAL,
                title="Sanctioned address in the fund flow",
                message=sanctions_hit.description,
                recommended_action=RECOMMENDED_ACTION[RiskLevel.CRITICAL],
                chain=graph.chain,
                address=sanctions_hit.addresses[0] if sanctions_hit.addresses else None,
                sla_hours=SANCTIONS_SLA_HOURS,
                payload=sanctions_hit.to_dict(),
            )
        )

    # --- obfuscation ------------------------------------------------------
    mixer_hit = next((h for h in hits if h.typology is Typology.MIXER_INTERACTION), None)
    if mixer_hit:
        drafts.append(
            DraftAlert(
                alert_type=AlertType.MIXER_DETECTED,
                severity=RiskLevel.HIGH,
                title="Funds entered a mixer",
                message=(
                    f"{mixer_hit.description} On-chain linkage cannot be proven beyond "
                    "this point."
                ),
                recommended_action=(
                    "Record the break in the chain of custody. Any attribution past the "
                    "mixer must be presented as a timing and value inference, not proof."
                ),
                chain=graph.chain,
                address=mixer_hit.addresses[0] if mixer_hit.addresses else None,
                payload=mixer_hit.to_dict(),
            )
        )

    for link in links[:3]:
        if not link.matched:
            continue
        drafts.append(
            DraftAlert(
                alert_type=AlertType.BRIDGE_DETECTED,
                severity=RiskLevel.HIGH,
                title=(
                    f"Probable cross-chain hop via {link.bridge_name} to "
                    f"{link.dst_chain.display_name}"
                ),
                message=(
                    f"A payout of about {link.value_usd:,.0f} USD left the bridge on "
                    f"{link.dst_chain.display_name} {link.time_delta_seconds}s after the "
                    f"deposit. Confidence {link.confidence:.0%} - this is a heuristic "
                    "correlation, not a proven link."
                ),
                recommended_action=(
                    f"Open a follow-on trace from {link.dst_address} on "
                    f"{link.dst_chain.display_name} and corroborate before relying on it."
                ),
                chain=link.dst_chain,
                address=link.dst_address,
                payload=link.to_dict(),
            )
        )

    # --- movement and value ----------------------------------------------
    rapid_hit = next((h for h in hits if h.typology is Typology.RAPID_RELAY), None)
    still_moving = any(
        n.terminal_reason == TerminalReason.MAX_HOPS and n.taint >= 0.1
        for n in graph.nodes.values()
    )
    if rapid_hit and still_moving:
        drafts.append(
            DraftAlert(
                alert_type=AlertType.RAPID_MOVEMENT,
                severity=RiskLevel.HIGH,
                title="Funds are still moving rapidly",
                message=(
                    f"{rapid_hit.description} Branches were still active when the trace "
                    "reached its hop limit, so the money is likely to have moved again."
                ),
                recommended_action=(
                    "Re-run the trace with a higher hop limit now; every hour reduces the "
                    "chance of a successful freeze."
                ),
                chain=graph.chain,
                sla_hours=RAPID_MOVEMENT_SLA_HOURS,
                payload=rapid_hit.to_dict(),
            )
        )

    total_value = sum(
        n.value_in_usd for n in graph.nodes.values() if n.hop == 1
    )
    if total_value >= HIGH_VALUE_USD:
        drafts.append(
            DraftAlert(
                alert_type=AlertType.HIGH_VALUE_FLOW,
                severity=RiskLevel.HIGH if total_value >= 5 * HIGH_VALUE_USD else RiskLevel.MEDIUM,
                title=f"High-value flow: {total_value:,.0f} USD traced",
                message=(
                    f"The first hop out of the reported address carried about "
                    f"{total_value:,.0f} USD. Case risk is {risk.level.value}."
                ),
                recommended_action=RECOMMENDED_ACTION[risk.level],
                chain=graph.chain,
                address=graph.seed,
                payload={"value_usd": round(total_value, 2), "risk": risk.to_dict()},
            )
        )

    return drafts


async def persist_alerts(
    session: AsyncSession,
    drafts: list[DraftAlert],
    *,
    case_id: str | None,
    job_id: str,
) -> list[Alert]:
    """Write alerts, skipping ones already raised for the same case and address."""
    now = datetime.now(UTC)

    existing_keys: set[tuple[str, str | None, str | None]] = set()
    if case_id:
        rows = (
            await session.execute(
                select(Alert.alert_type, Alert.address, Alert.vasp_slug).where(
                    Alert.case_id == case_id
                )
            )
        ).all()
        existing_keys = {(str(t), a, v) for t, a, v in rows}

    created: list[Alert] = []
    for draft in drafts:
        key = (draft.alert_type.value, draft.address, draft.vasp_slug)
        if key in existing_keys:
            continue
        existing_keys.add(key)
        alert = Alert(
            case_id=case_id,
            job_id=job_id,
            alert_type=draft.alert_type,
            severity=draft.severity,
            title=draft.title,
            message=draft.message,
            chain=draft.chain,
            address=draft.address,
            vasp_slug=draft.vasp_slug,
            recommended_action=draft.recommended_action,
            sla_due_at=(
                now + timedelta(hours=draft.sla_hours) if draft.sla_hours else None
            ),
            payload=draft.payload,
        )
        session.add(alert)
        created.append(alert)

    if created:
        logger.info("alerts_created", job_id=job_id, count=len(created))
    return created


async def generate_alerts(
    session: AsyncSession,
    graph: TraceGraph,
    hits: list[TypologyHit],
    risk: RiskAssessment,
    links: list[CrossChainLink],
    *,
    case_id: str | None,
    job_id: str,
) -> list[Alert]:
    vasps = await _vasp_directory(session)
    drafts = build_alerts(graph, hits, risk, links, vasps)
    return await persist_alerts(session, drafts, case_id=case_id, job_id=job_id)
