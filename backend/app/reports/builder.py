"""Build LEA-ready investigation packets, Section 94 BNSS letters and SAHYOG packages.

Every generated artefact is hashed (SHA-256) and written with a chain-of-custody
log so the file an investigator later produces in court is the same file this
system emitted.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.config import settings
from app.core.enums import ReportKind
from app.db.models import Case, Report, TraceEdge, TraceJob, Vasp
from app.tracing.paths import read_attribution, read_risk, read_stats

INK = colors.HexColor("#0f172a")
MUTED = colors.HexColor("#475569")
LINE = colors.HexColor("#cbd5e1")
BRAND = colors.HexColor("#0369a1")
PAPER = colors.HexColor("#f8fafc")
WARN = colors.HexColor("#9a3412")

ALL_SECTIONS = [
    "summary",
    "attribution",
    "risk",
    "annexure",
    "vasp_request",
    "custody",
]


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "TitleCW",
            parent=base["Title"],
            fontName="Times-Bold",
            fontSize=16,
            textColor=INK,
            alignment=TA_CENTER,
            spaceAfter=4,
        ),
        "subtitle": ParagraphStyle(
            "SubtitleCW",
            parent=base["Normal"],
            fontName="Times-Italic",
            fontSize=10,
            textColor=MUTED,
            alignment=TA_CENTER,
            spaceAfter=14,
        ),
        "h1": ParagraphStyle(
            "H1CW",
            parent=base["Heading1"],
            fontName="Times-Bold",
            fontSize=13,
            textColor=BRAND,
            spaceBefore=12,
            spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "BodyCW",
            parent=base["Normal"],
            fontName="Times-Roman",
            fontSize=10,
            leading=14,
            textColor=INK,
            alignment=TA_JUSTIFY,
            spaceAfter=6,
        ),
        "left": ParagraphStyle(
            "LeftCW",
            parent=base["Normal"],
            fontName="Times-Roman",
            fontSize=10,
            leading=14,
            textColor=INK,
            alignment=TA_LEFT,
            spaceAfter=4,
        ),
        "mono": ParagraphStyle(
            "MonoCW",
            parent=base["Normal"],
            fontName="Courier",
            fontSize=8,
            leading=11,
            textColor=INK,
        ),
        "small": ParagraphStyle(
            "SmallCW",
            parent=base["Normal"],
            fontName="Times-Roman",
            fontSize=8,
            textColor=MUTED,
            alignment=TA_CENTER,
        ),
        "warn": ParagraphStyle(
            "WarnCW",
            parent=base["Normal"],
            fontName="Times-Italic",
            fontSize=9,
            textColor=WARN,
            spaceAfter=8,
        ),
    }


def _kv_table(rows: list[tuple[str, str]], styles: dict[str, ParagraphStyle]) -> Table:
    data = [
        [Paragraph(f"<b>{k}</b>", styles["left"]), Paragraph(v or "—", styles["left"])]
        for k, v in rows
    ]
    table = Table(data, colWidths=[48 * mm, 122 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), PAPER),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("GRID", (0, 0), (-1, -1), 0.3, LINE),
            ]
        )
    )
    return table


def _header_footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFillColor(BRAND)
    canvas.rect(0, A4[1] - 12 * mm, A4[0], 12 * mm, fill=1, stroke=0)
    canvas.setFillColor(colors.white)
    canvas.setFont("Times-Bold", 9)
    canvas.drawString(18 * mm, A4[1] - 8 * mm, "ChainWatch  ·  Investigation Packet")
    canvas.setFont("Times-Roman", 8)
    canvas.drawRightString(A4[0] - 18 * mm, A4[1] - 8 * mm, "CONFIDENTIAL — Law Enforcement Use Only")
    canvas.setFillColor(MUTED)
    canvas.setFont("Times-Roman", 8)
    canvas.drawString(18 * mm, 10 * mm, f"Page {doc.page}")
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, "Not for public disclosure")
    canvas.restoreState()


def _fmt_inr(value: float | None) -> str:
    if value is None:
        return "—"
    return f"INR {value:,.0f}"


def _fmt_usd(value: float | None) -> str:
    if value is None:
        return "—"
    return f"USD {value:,.2f}"


def _annexure_from_job(edges: list[TraceEdge]) -> list[dict[str, Any]]:
    path = [e for e in edges if e.is_on_primary_path]
    path.sort(key=lambda e: (e.hop, e.ts))
    rows = []
    for index, edge in enumerate(path, start=1):
        rows.append(
            {
                "hop": index,
                "from": edge.src_address,
                "to": edge.dst_address,
                "chain": str(edge.chain),
                "tx_hash": edge.tx_hash,
                "block": edge.block_number,
                "timestamp_utc": edge.ts.isoformat() if edge.ts else "",
                "asset": edge.asset,
                "amount": str(edge.amount),
                "amount_usd": round(edge.amount_usd or 0.0, 2),
                "share_of_victim_funds_pct": round((edge.taint_share or 0.0) * 100, 2),
            }
        )
    return rows


def _bnss_letter(
    case: Case,
    finding: dict[str, Any],
    vasp: Vasp | None,
    generated_at: datetime,
    officer: str,
) -> list[str]:
    addressee = (
        f"{vasp.grievance_officer or 'Head of Compliance'}, {vasp.legal_name or vasp.name}"
        if vasp
        else "The Compliance Officer, the concerned Virtual Asset Service Provider"
    )
    email = vasp.compliance_email if vasp else "[compliance contact]"
    deposit = finding.get("address") or "[deposit address]"
    chain = (finding.get("chain") or "").upper()
    return [
        f"To\n{addressee}\n{email}",
        (
            "Subject: Request for information and preservation of records under "
            "Section 94 of the Bharatiya Nagarik Suraksha Sanhita, 2023 — "
            f"Case {case.case_number}."
        ),
        (
            "Sir/Madam,\n\nThis Investigating Agency is inquiring into a complaint of "
            f"{str(case.fraud_category).replace('_', ' ')} registered as "
            f"{case.case_number}"
            + (f" (NCRP acknowledgement {case.ncrp_ack_no})" if case.ncrp_ack_no else "")
            + f". The complainant reports a loss of {_fmt_inr(case.loss_amount_inr)}."
        ),
        (
            "Blockchain analysis of the victim-reported wallet has identified the "
            f"following address as a deposit address associated with "
            f"{finding.get('entity_name') or 'your platform'} on {chain or 'the relevant chain'}:\n\n"
            f"    {deposit}\n\n"
            f"Traced victim value reaching this address: {finding.get('taint_pct', 0):.0f}% "
            f"({_fmt_usd(finding.get('value_usd'))}). Attribution confidence: "
            f"{(finding.get('confidence') or 0) * 100:.0f}%."
        ),
        (
            "You are therefore requested, under Section 94 of the Bharatiya Nagarik "
            "Suraksha Sanhita, 2023, to:\n"
            "  1. Immediately preserve and, where your policy so permits, freeze the "
            "balance standing to the credit of the above address and any linked "
            "customer account;\n"
            "  2. Furnish KYC / CDD records of the account holder, including name, "
            "date of birth / incorporation, official identification, registered "
            "address, mobile number and email;\n"
            "  3. Furnish the deposit and withdrawal history of the account from the "
            "date of first credit of the traced funds, including fiat off-ramps, "
            "linked bank accounts and UPI / IMPS / NEFT identifiers;\n"
            "  4. Confirm whether any part of the funds has already been withdrawn "
            "or transferred, and to whom;\n"
            "  5. Nominate a single point of contact for this matter."
        ),
        (
            f"An emergency freeze is{' ' if (vasp and vasp.accepts_emergency_freeze) else ' not '} "
            "indicated as available on your published law-enforcement channel. "
            f"Your stated response SLA is {vasp.response_sla_hours if vasp else 72} hours. "
            "This request is made in connection with a cognizable offence and is "
            "required for the purposes of investigation."
        ),
        (
            f"Issued at {generated_at.strftime('%d %B %Y, %H:%M UTC')} by {officer}.\n\n"
            "This communication is generated by ChainWatch. The SHA-256 integrity "
            "hash of the accompanying investigation packet is recorded in the "
            "chain-of-custody log."
        ),
    ]


def build_investigation_pdf(
    *,
    case: Case,
    job: TraceJob | None,
    vasp: Vasp | None,
    annexure: list[dict[str, Any]],
    officer: str,
    sections: list[str],
    sha_placeholder: str,
) -> bytes:
    styles = _styles()
    summary = (job.summary if job else None) or {}
    finding = read_attribution(summary)
    risk = read_risk(summary)
    stats = read_stats(summary)
    generated_at = datetime.now(UTC)

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=20 * mm,
        bottomMargin=18 * mm,
        title=f"Investigation Packet {case.case_number}",
        author=officer,
    )
    story: list = []

    story.append(Paragraph("INVESTIGATION PACKET", styles["title"]))
    story.append(
        Paragraph(
            "Real-Time Crypto Fraud Attribution System  ·  For production to the "
            "Investigating Officer / Court",
            styles["subtitle"],
        )
    )

    if "summary" in sections:
        story.append(Paragraph("1. Case summary", styles["h1"]))
        story.append(
            _kv_table(
                [
                    ("Case number", case.case_number),
                    ("NCRP acknowledgement", case.ncrp_ack_no or "—"),
                    ("Title", case.title),
                    ("Category", str(case.fraud_category).replace("_", " ").title()),
                    ("Status", str(case.status).replace("_", " ").title()),
                    ("Victim", case.victim_name or "—"),
                    ("Loss (INR)", _fmt_inr(case.loss_amount_inr)),
                    ("Police station", case.police_station or "—"),
                    ("State / UT", case.state or "—"),
                    ("Intake source", case.intake_source),
                    ("Trace job", job.id if job else "—"),
                    ("Data source", str(job.data_source) if job else "—"),
                    ("Generated", generated_at.strftime("%d %B %Y, %H:%M UTC")),
                    ("Generated by", officer),
                ],
                styles,
            )
        )
        if case.description:
            story.append(Spacer(1, 6))
            story.append(Paragraph(case.description.replace("\n", "<br/>"), styles["body"]))

    if "attribution" in sections:
        story.append(Paragraph("2. Attribution finding", styles["h1"]))
        statement = finding.get("statement") or (
            "No custodial endpoint was identified on the traced graph."
        )
        story.append(Paragraph(statement, styles["body"]))
        story.append(Spacer(1, 4))
        story.append(
            _kv_table(
                [
                    ("Entity", finding.get("entity_name") or "Unidentified"),
                    ("VASP slug", finding.get("vasp_slug") or "—"),
                    ("Deposit address", finding.get("address") or "—"),
                    ("Chain", (finding.get("chain") or "—").upper()),
                    ("Hop", str(finding.get("hop") if finding.get("hop") is not None else "—")),
                    ("Share of traced value", f"{finding.get('taint_pct', 0):.1f}%"),
                    ("Value (USD)", _fmt_usd(finding.get("value_usd"))),
                    ("Confidence", f"{(finding.get('confidence') or 0) * 100:.0f}%"),
                    ("Method", str(finding.get("method") or "—").replace("_", " ")),
                    ("Time to reach", finding.get("elapsed_human") or "—"),
                    ("Addresses examined", str(stats.get("addresses_examined") or "—")),
                    ("Edges kept", str(stats.get("edges_kept") or "—")),
                ],
                styles,
            )
        )
        if vasp:
            story.append(Spacer(1, 6))
            story.append(Paragraph("Identified VASP — compliance contact", styles["left"]))
            story.append(
                _kv_table(
                    [
                        ("Legal name", vasp.legal_name or vasp.name),
                        ("Jurisdiction", vasp.jurisdiction),
                        ("FIU-IND registered", "Yes" if vasp.fiu_ind_registered else "No"),
                        ("Registration no.", vasp.fiu_ind_reg_no or "—"),
                        ("Compliance email", vasp.compliance_email or "—"),
                        ("Grievance officer", vasp.grievance_officer or "—"),
                        ("LE portal", vasp.law_enforcement_portal or "—"),
                        ("Response SLA", f"{vasp.response_sla_hours} hours"),
                        (
                            "Emergency freeze",
                            "Accepted" if vasp.accepts_emergency_freeze else "Not indicated",
                        ),
                    ],
                    styles,
                )
            )

    if "risk" in sections:
        story.append(Paragraph("3. Risk assessment", styles["h1"]))
        story.append(
            _kv_table(
                [
                    ("Risk score", f"{risk.get('score', 0):.0f} / 100"),
                    ("Bucket", str(risk.get("level") or "—").upper()),
                    (
                        "Recommended action",
                        risk.get("recommended_action") or "Continue investigation.",
                    ),
                    (
                        "Sanctions override",
                        "Yes — treat as Critical" if risk.get("sanctions_override") else "No",
                    ),
                ],
                styles,
            )
        )
        for reason in risk.get("reasons") or []:
            story.append(Paragraph(f"• {reason}", styles["left"]))
        for hit in (summary.get("typologies") or [])[:12]:
            if isinstance(hit, dict):
                label = hit.get("label") or hit.get("typology") or "pattern"
                story.append(
                    Paragraph(
                        f"• Typology: {label} "
                        f"(confidence {(hit.get('confidence') or 0) * 100:.0f}%)",
                        styles["left"],
                    )
                )

    if "annexure" in sections:
        story.append(Paragraph("4. Hop-by-hop annexure", styles["h1"]))
        story.append(
            Paragraph(
                "Each row is a value transfer on the primary seed-to-endpoint path. "
                "Transaction hashes and UTC timestamps are taken from the chain data "
                "used for this trace. Share-of-funds is the proportional taint "
                "(haircut method) of the victim payment.",
                styles["body"],
            )
        )
        if not annexure:
            story.append(Paragraph("No primary path was recorded for this case.", styles["warn"]))
        else:
            header = [
                Paragraph("<b>Hop</b>", styles["small"]),
                Paragraph("<b>From → To</b>", styles["small"]),
                Paragraph("<b>Tx / Block / UTC</b>", styles["small"]),
                Paragraph("<b>Amount</b>", styles["small"]),
            ]
            data = [header]
            for row in annexure:
                data.append(
                    [
                        Paragraph(str(row["hop"]), styles["mono"]),
                        Paragraph(
                            f"{row['from']}<br/>→ {row['to']}",
                            styles["mono"],
                        ),
                        Paragraph(
                            f"{row['tx_hash']}<br/>blk {row['block']}  {row['timestamp_utc']}",
                            styles["mono"],
                        ),
                        Paragraph(
                            f"{row['amount']} {row['asset']}<br/>"
                            f"{_fmt_usd(row['amount_usd'])}  "
                            f"({row['share_of_victim_funds_pct']:.1f}%)",
                            styles["mono"],
                        ),
                    ]
                )
            table = Table(data, colWidths=[12 * mm, 62 * mm, 62 * mm, 34 * mm])
            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), PAPER),
                        ("GRID", (0, 0), (-1, -1), 0.3, LINE),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 3),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                        ("TOPPADDING", (0, 0), (-1, -1), 3),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    ]
                )
            )
            story.append(table)

    if "vasp_request" in sections:
        story.append(PageBreak())
        story.append(Paragraph("5. Section 94 BNSS information request", styles["h1"]))
        story.append(
            Paragraph(
                "Pre-filled request addressed to the identified VASP. Serve through "
                "the official law-enforcement channel. This is a draft for the "
                "Investigating Officer to sign and issue.",
                styles["warn"],
            )
        )
        for para in _bnss_letter(case, finding, vasp, generated_at, officer):
            story.append(Paragraph(para.replace("\n", "<br/>"), styles["body"]))
            story.append(Spacer(1, 4))

    if "custody" in sections:
        story.append(Paragraph("6. Integrity and chain of custody", styles["h1"]))
        story.append(
            Paragraph(
                "The SHA-256 digest below is computed over the bytes of this file "
                "after generation. Any subsequent alteration of the PDF will change "
                "the digest. Downloads are appended to the custody log stored with "
                "the case record.",
                styles["body"],
            )
        )
        story.append(
            _kv_table(
                [
                    ("Algorithm", "SHA-256"),
                    ("Digest", sha_placeholder),
                    ("Producing system", "ChainWatch 0.1.0"),
                    ("Officer", officer),
                    ("Generated (UTC)", generated_at.isoformat()),
                ],
                styles,
            )
        )

    doc.build(story, onFirstPage=_header_footer, onLaterPages=_header_footer)
    return buf.getvalue()


def build_bnss_pdf(
    *,
    case: Case,
    job: TraceJob | None,
    vasp: Vasp | None,
    officer: str,
    sha_placeholder: str,
) -> bytes:
    styles = _styles()
    finding = read_attribution(job.summary if job else None)
    generated_at = datetime.now(UTC)
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=18 * mm,
        title=f"Section 94 BNSS Request {case.case_number}",
        author=officer,
    )
    story = [
        Paragraph("SECTION 94 BNSS — INFORMATION REQUEST", styles["title"]),
        Paragraph(
            "Bharatiya Nagarik Suraksha Sanhita, 2023  ·  Draft for Investigating Officer",
            styles["subtitle"],
        ),
    ]
    for para in _bnss_letter(case, finding, vasp, generated_at, officer):
        story.append(Paragraph(para.replace("\n", "<br/>"), styles["body"]))
        story.append(Spacer(1, 6))
    story.append(
        Paragraph(
            f"Integrity digest (SHA-256): {sha_placeholder}",
            styles["small"],
        )
    )
    doc.build(story, onFirstPage=_header_footer, onLaterPages=_header_footer)
    return buf.getvalue()


def build_sahyog_package(
    *,
    case: Case,
    job: TraceJob | None,
    vasp: Vasp | None,
    annexure: list[dict[str, Any]],
    officer: str,
) -> bytes:
    """A documented JSON package an officer can attach to a SAHYOG request.

    SAHYOG has no public API. This export is the swap-in point: the shape is
    stable so a real endpoint can be added later as a config change.
    """
    finding = read_attribution(job.summary if job else None)
    risk = read_risk(job.summary if job else None)
    payload = {
        "schema": "chainwatch.sahyog.v1",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "generated_by": officer,
        "case": {
            "case_number": case.case_number,
            "ncrp_ack_no": case.ncrp_ack_no,
            "title": case.title,
            "fraud_category": str(case.fraud_category),
            "loss_amount_inr": case.loss_amount_inr,
            "victim_name": case.victim_name,
            "police_station": case.police_station,
            "state": case.state,
            "reported_addresses": [
                {"chain": str(row.chain), "address": row.address, "role": row.role}
                for row in case.addresses
            ],
        },
        "attribution": finding,
        "risk": risk,
        "vasp": None
        if vasp is None
        else {
            "slug": vasp.slug,
            "name": vasp.name,
            "legal_name": vasp.legal_name,
            "jurisdiction": vasp.jurisdiction,
            "fiu_ind_registered": vasp.fiu_ind_registered,
            "compliance_email": vasp.compliance_email,
            "grievance_officer": vasp.grievance_officer,
            "law_enforcement_portal": vasp.law_enforcement_portal,
            "response_sla_hours": vasp.response_sla_hours,
            "accepts_emergency_freeze": vasp.accepts_emergency_freeze,
        },
        "hop_annexure": annexure,
        "request": {
            "instrument": "Section 94 BNSS information and preservation request",
            "actions_sought": [
                "preserve_and_freeze",
                "produce_kyc",
                "produce_transaction_history",
                "nominate_spoc",
            ],
        },
        "disclaimer": (
            "This package is a structured export for SAHYOG / inter-agency "
            "transmission. It is not itself a court order."
        ),
    }
    return json.dumps(payload, indent=2, default=str).encode("utf-8")


def _hash_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def write_artifact(payload: bytes, filename: str) -> Path:
    settings.ensure_dirs()
    path = settings.reports_dir / filename
    path.write_bytes(payload)
    return path


def generate_report_bytes(
    *,
    kind: ReportKind,
    case: Case,
    job: TraceJob | None,
    vasp: Vasp | None,
    annexure: list[dict[str, Any]],
    officer: str,
    sections: list[str],
) -> tuple[bytes, str, str]:
    # Self-hashing a PDF is circular (changing the printed digest changes the
    # digest). We print a stable note in the packet and store the SHA-256 of
    # the exact bytes written to disk — that is the value an officer verifies.
    note = "see custody log — hash is of these exact bytes"
    if kind is ReportKind.SAHYOG_PACKAGE:
        payload = build_sahyog_package(
            case=case, job=job, vasp=vasp, annexure=annexure, officer=officer
        )
        return payload, _hash_bytes(payload), "application/json"

    if kind is ReportKind.BNSS_94_REQUEST:
        payload = build_bnss_pdf(
            case=case, job=job, vasp=vasp, officer=officer, sha_placeholder=note
        )
        return payload, _hash_bytes(payload), "application/pdf"

    payload = build_investigation_pdf(
        case=case,
        job=job,
        vasp=vasp,
        annexure=annexure,
        officer=officer,
        sections=sections,
        sha_placeholder=note,
    )
    return payload, _hash_bytes(payload), "application/pdf"


def generate_report(
    *,
    kind: ReportKind,
    case: Case,
    job: TraceJob | None,
    vasp: Vasp | None,
    edges: list[TraceEdge],
    officer: str,
    officer_id: str | None,
    sections: list[str] | None,
) -> Report:
    chosen = [s for s in (sections or ALL_SECTIONS) if s in ALL_SECTIONS] or list(ALL_SECTIONS)
    annexure = _annexure_from_job(edges)
    payload, digest, content_type = generate_report_bytes(
        kind=kind,
        case=case,
        job=job,
        vasp=vasp,
        annexure=annexure,
        officer=officer,
        sections=chosen,
    )
    ext = "json" if kind is ReportKind.SAHYOG_PACKAGE else "pdf"
    filename = f"{case.case_number.replace('/', '-')}_{kind.value}_{digest[:10]}.{ext}"
    path = write_artifact(payload, filename)
    now = datetime.now(UTC)
    return Report(
        case_id=case.id,
        job_id=job.id if job else None,
        kind=kind,
        filename=filename,
        storage_path=str(path),
        content_type=content_type,
        sha256=digest,
        size_bytes=len(payload),
        generated_by_id=officer_id,
        generated_by_name=officer,
        sections=chosen,
        custody_log=[
            {
                "action": "generated",
                "actor": officer,
                "at": now.isoformat(),
                "detail": f"{kind.value} artefact written ({len(payload)} bytes)",
            }
        ],
    )
