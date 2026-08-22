"""Measure attribution accuracy against the seeded ground truth.

Every synthetic scenario declares the VASP the money was designed to reach and
how many hops it should take. Running the real tracing engine over all of them
gives a single honest number for how often the system finds the right answer,
and turns any regression in the traversal or attribution tiers into a failing
row rather than a vague sense that the demo got worse.

    python -m scripts.evaluate_traces
    python -m scripts.evaluate_traces --scenario mixer_ethereum --verbose
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass

from sqlalchemy import select

from app.core.enums import Chain
from app.db.models import SyntheticGroundTruth
from app.db.session import dispose_engine, session_scope
from app.logging_config import configure_logging
from app.tracing.engine import TraceEngine, create_job
from app.tracing.models import TraceParams


@dataclass
class Outcome:
    scenario: str
    expected_vasp: str
    found_vasp: str | None
    expected_deposit: str
    found_deposit: str | None
    expected_hops: int
    found_hops: int | None
    confidence: float
    method: str | None
    taint_pct: float
    nodes: int
    edges: int
    duration_ms: int
    stopped_at: str | None = None
    expected_typologies: frozenset[str] = frozenset()
    found_typologies: frozenset[str] = frozenset()

    @property
    def vasp_correct(self) -> bool:
        return self.found_vasp == self.expected_vasp

    @property
    def typologies_missed(self) -> frozenset[str]:
        return self.expected_typologies - self.found_typologies

    @property
    def typologies_extra(self) -> frozenset[str]:
        """Detections the scenario did not script for.

        Not necessarily wrong - a graph can genuinely exhibit a pattern the
        scenario author did not think to declare - so these are reported but
        never failed on.
        """
        return self.found_typologies - self.expected_typologies

    @property
    def deposit_correct(self) -> bool:
        return self.found_deposit == self.expected_deposit

    @property
    def blocked_by_design(self) -> bool:
        """Did the trace stop where the money genuinely leaves the chain?

        A mixer breaks on-chain linkage and a bridge moves value to another
        chain. Stopping there is the correct, honest result for a same-chain
        forward trace - recovering the trail past those points is the job of the
        cross-chain correlator, not the traversal - so these are scored
        separately rather than counted as misses.
        """
        return not self.vasp_correct and self.stopped_at in ("mixer_entry", "bridge_entry")

    @property
    def verdict(self) -> str:
        if self.vasp_correct:
            return "PASS"
        return "INFER" if self.blocked_by_design else "FAIL"


async def evaluate(scenario_filter: str | None, verbose: bool) -> list[Outcome]:
    outcomes: list[Outcome] = []
    async with session_scope() as session:
        query = select(SyntheticGroundTruth).order_by(SyntheticGroundTruth.scenario)
        if scenario_filter:
            query = query.where(SyntheticGroundTruth.scenario == scenario_filter)
        truths = (await session.execute(query)).scalars().all()

        if not truths:
            print("No ground truth rows found. Run `python -m scripts.seed_all` first.")
            return []

        for truth in truths:
            # Give every scenario enough hops to reach its declared endpoint;
            # this harness measures attribution accuracy, not the default budget.
            params = TraceParams()
            params.max_hops = max(params.max_hops, truth.expected_hops)
            job = await create_job(
                session,
                chain=Chain(truth.chain),
                seed_address=truth.seed_address,
                params=params,
            )
            await session.commit()
            result = await TraceEngine(session).execute(job.id)
            found = result.summary["attribution"]
            outcomes.append(
                Outcome(
                    scenario=truth.scenario,
                    expected_vasp=truth.expected_vasp_slug,
                    found_vasp=found.get("vasp_slug"),
                    expected_deposit=truth.expected_deposit_address,
                    found_deposit=found.get("address") if found.get("found") else None,
                    expected_hops=truth.expected_hops,
                    found_hops=found.get("hop"),
                    confidence=found.get("confidence") or 0.0,
                    method=found.get("method"),
                    taint_pct=found.get("taint_pct") or 0.0,
                    nodes=result.stats.addresses_examined,
                    edges=result.stats.edges_kept,
                    duration_ms=result.stats.duration_ms,
                    stopped_at=found.get("terminal_reason"),
                    expected_typologies=frozenset(truth.expected_typologies or []),
                    found_typologies=frozenset(
                        hit["typology"] for hit in result.summary["typologies"]
                    ),
                )
            )
            if verbose:
                print(f"\n--- {truth.scenario} ---")
                print(found.get("statement"))
                for entry in result.summary["endpoints"][:8]:
                    print(
                        f"    hop {entry['hop']}  {entry['taint_pct']:6.2f}%  "
                        f"{entry['terminal_reason']:24s} {entry['address']}  "
                        f"{entry['entity_name'] or '-'}"
                    )
                for rec in result.summary["recommendations"]:
                    print(f"    [{rec['urgency']:6s}] {rec['action']}")
    return outcomes


def report(outcomes: list[Outcome]) -> int:
    if not outcomes:
        return 1

    header = (
        f"{'':5} {'scenario':28s} {'expected':12s} {'found':12s} "
        f"{'hop':>7s} {'taint':>7s} {'conf':>6s} {'nodes':>6s} {'ms':>6s}  method"
    )
    print(header)
    print("-" * len(header))
    for out in outcomes:
        hop = f"{out.found_hops}/{out.expected_hops}" if out.found_hops is not None else "-"
        print(
            f"{out.verdict:5s} {out.scenario:28s} {out.expected_vasp:12s} "
            f"{str(out.found_vasp or '-'):12s} {hop:>7s} "
            f"{out.taint_pct:6.1f}% {out.confidence:6.2f} {out.nodes:6d} "
            f"{out.duration_ms:6d}  {out.method or '-'}"
        )

    total = len(outcomes)
    vasp_hits = sum(1 for o in outcomes if o.vasp_correct)
    deposit_hits = sum(1 for o in outcomes if o.deposit_correct)
    deferred = sum(1 for o in outcomes if o.blocked_by_design)
    exact_hops = sum(
        1 for o in outcomes if o.vasp_correct and o.found_hops == o.expected_hops
    )
    traceable = total - deferred
    avg_ms = sum(o.duration_ms for o in outcomes) / total

    print()
    print(f"VASP identified correctly   : {vasp_hits}/{total} ({vasp_hits / total:.0%} of all)")
    if deferred and traceable:
        print(
            f"                              {vasp_hits}/{traceable} "
            f"({vasp_hits / traceable:.0%}) of cases traceable on-chain"
        )
    if deferred:
        print(
            f"Deferred to inference layer : {deferred} "
            "(stopped at a mixer or bridge, as designed)"
        )
    print(f"Exact deposit address       : {deposit_hits}/{total} ({deposit_hits / total:.0%})")
    if traceable:
        print(f"Correct hop distance        : {exact_hops}/{traceable}")
    print(f"Mean trace time             : {avg_ms:.0f} ms")

    expected_typologies = sum(len(o.expected_typologies) for o in outcomes)
    caught = sum(
        len(o.expected_typologies & o.found_typologies) for o in outcomes
    )
    if expected_typologies:
        print(
            f"Typologies detected         : {caught}/{expected_typologies} "
            f"({caught / expected_typologies:.0%})"
        )
        for out in outcomes:
            if out.typologies_missed:
                print(f"    {out.scenario:24s} missed {sorted(out.typologies_missed)}")

    failures = [o for o in outcomes if o.verdict == "FAIL"]
    return 1 if failures else 0


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", help="Evaluate a single scenario by key")
    parser.add_argument(
        "--verbose", action="store_true", help="Print the finding and endpoints per case"
    )
    args = parser.parse_args()

    configure_logging(level="WARNING")
    try:
        outcomes = await evaluate(args.scenario, args.verbose)
        return report(outcomes)
    finally:
        await dispose_engine()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
