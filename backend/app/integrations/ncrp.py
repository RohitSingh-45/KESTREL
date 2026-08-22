"""NCRP / SAHYOG adapters.

Neither system publishes a public API. This module is the documented seam:
inbound complaints arrive as a webhook (or the mock pusher), and outbound
requests are the SAHYOG JSON package produced by the report builder. Swapping
in a real endpoint later is a config change, not a rewrite of case intake.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.core.enums import CasePriority, CaseStatus, FraudCategory
from app.db.models import Case, CaseAddress


class NcrpComplaint(BaseModel):
    """The inbound shape we accept from NCRP-style systems and the mock pusher."""

    acknowledgement_no: str | None = Field(default=None, alias="ncrp_ack_no")
    case_number: str | None = None
    title: str | None = None
    description: str | None = None
    category: str | None = None
    victim_name: str | None = None
    victim_contact: str | None = None
    loss_amount_inr: float | None = None
    incident_date: datetime | None = None
    police_station: str | None = None
    state: str | None = None
    addresses: list[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


CATEGORY_KEYWORDS: dict[FraudCategory, tuple[str, ...]] = {
    FraudCategory.INVESTMENT_SCAM: ("investment", "trading", "ponzi", "fake exchange"),
    FraudCategory.TASK_BASED_FRAUD: ("task", "part time", "part-time", "job fraud"),
    FraudCategory.SEXTORTION: ("sextortion", "extortion", "nude", "blackmail"),
    FraudCategory.RANSOMWARE: ("ransom", "encrypt", "locker"),
    FraudCategory.PHISHING: ("phishing", "otp", "kyc update", "impersonat"),
    FraudCategory.DARKNET: ("darknet", "dark web", "narcotic", "drug"),
    FraudCategory.ORGANISED_CYBER_CRIME: ("organised", "organized", "syndicate", "gang"),
}


def classify_category(text: str | None) -> FraudCategory:
    if not text:
        return FraudCategory.OTHER
    lowered = text.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            return category
    return FraudCategory.OTHER


def complaint_to_case(
    complaint: NcrpComplaint,
    *,
    case_number: str,
    assigned_to_id: str | None,
    intake_source: str,
    resolved_addresses: list[tuple[Any, str]],
) -> Case:
    title = complaint.title or f"NCRP complaint {complaint.acknowledgement_no or case_number}"
    case = Case(
        case_number=case_number,
        ncrp_ack_no=complaint.acknowledgement_no,
        title=title,
        description=complaint.description,
        fraud_category=classify_category(
            " ".join(filter(None, [complaint.category, complaint.title, complaint.description]))
        ),
        status=CaseStatus.OPEN,
        priority=CasePriority.HIGH if (complaint.loss_amount_inr or 0) >= 1_000_000 else CasePriority.NORMAL,
        victim_name=complaint.victim_name,
        victim_contact=complaint.victim_contact,
        loss_amount_inr=complaint.loss_amount_inr,
        incident_date=complaint.incident_date,
        reported_date=datetime.now(UTC),
        police_station=complaint.police_station,
        state=complaint.state,
        intake_source=intake_source,
        assigned_to_id=assigned_to_id,
        details={"ncrp": complaint.model_dump(mode="json")},
    )
    for chain, address in resolved_addresses:
        case.addresses.append(
            CaseAddress(
                chain=chain,
                address=address,
                role="reported",
                note="Received via NCRP connector",
            )
        )
    return case


def sample_complaint() -> dict[str, Any]:
    """A realistic mock payload the pusher can send so the webhook is demonstrable."""
    return {
        "ncrp_ack_no": f"NCRP{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}",
        "title": "Investment scam — victim paid USDT-TRC20 to a fake trading desk",
        "category": "investment fraud",
        "victim_name": "Mock Complainant",
        "victim_contact": "mock.victim@example.in",
        "loss_amount_inr": 425000,
        "police_station": "Cyber Crime PS",
        "state": "Delhi",
        "description": "Pushed by the ChainWatch mock NCRP service for a live-intake demo.",
        "addresses": ["TGMkKG6YRdJ6Cgnt8czbtrMXAQWQcV7Whq"],
    }
