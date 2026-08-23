"""End-to-end demo: seed, trace a scripted case, print the finding, write a packet.

    python -m scripts.demo
    python -m scripts.demo --scenario mixer_ethereum
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.core.enums import ReportKind
from app.db.bootstrap import init_database
from app.db.models import Case, SyntheticGroundTruth, TraceEdge, Vasp
from app.db.session import dispose_engine, session_scope
from app.logging_config import configure_logging
from app.reports.builder import generate_report
from app.seeds.runner import is_seeded, seed_all
from app.tracing.engine import TraceEngine, create_job
from app.tracing.models import TraceParams
from app.tracing.paths import read_attribution


async def main(scenario: str | None) -> int:
    configure_logging()
    await init_database()
    try:
        async with session_scope() as session:
            if not await is_seeded(session):
                print("Seeding the demo dataset...")
                await seed_all(session)

            truth = (
                await session.execute(
                    select(SyntheticGroundTruth).where(
                        SyntheticGroundTruth.scenario == (scenario or "direct_tron")
                    )
                )
            ).scalar_one_or_none()
            if truth is None:
                rows = (await session.execute(select(SyntheticGroundTruth))).scalars().all()
                print("Unknown scenario. Available:")
                for row in rows:
                    print(f"  {row.scenario}")
                return 1

            case = (
                await session.execute(
                    select(Case).where(Case.ncrp_ack_no.is_not(None)).limit(1)
                )
            ).scalar_one_or_none()

            print(f"Tracing {truth.seed_address} on {truth.chain} ({truth.scenario})")
            job = await create_job(
                session,
                chain=truth.chain,
                seed_address=truth.seed_address,
                params=TraceParams(),
                case_id=case.id if case else None,
            )
            await session.commit()
            result = await TraceEngine(session).execute(job.id)
            finding = read_attribution(result.summary)
            print()
            print(finding.get("statement") or "No attribution produced.")
            print(
                f"  expected VASP : {truth.expected_vasp_slug}"
                f"   found : {finding.get('vasp_slug')}"
            )
            print(
                f"  nodes {result.stats.addresses_examined}  "
                f"edges {result.stats.edges_kept}  "
                f"{result.stats.duration_ms} ms"
            )

            if case:
                vasp = None
                if finding.get("vasp_slug"):
                    vasp = (
                        await session.execute(
                            select(Vasp).where(Vasp.slug == finding["vasp_slug"])
                        )
                    ).scalar_one_or_none()
                edges = list(
                    (
                        await session.execute(
                            select(TraceEdge).where(TraceEdge.job_id == job.id)
                        )
                    ).scalars().all()
                )
                report = generate_report(
                    kind=ReportKind.INVESTIGATION_PACKET,
                    case=case,
                    job=job,
                    vasp=vasp,
                    edges=edges,
                    officer="Demo Script",
                    officer_id=None,
                    sections=None,
                )
                session.add(report)
                await session.commit()
                print(f"  packet      : {report.storage_path}")
                print(f"  sha256      : {report.sha256}")
    finally:
        await dispose_engine()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a scripted TraceX demo trace")
    parser.add_argument("--scenario", default="direct_tron")
    sys.exit(asyncio.run(main(parser.parse_args().scenario)))
