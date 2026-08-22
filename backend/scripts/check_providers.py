"""Connectivity check for the live blockchain providers.

    python -m scripts.check_providers

Reports, per provider, whether it is configured and whether a real lookup
succeeds. Safe to run repeatedly; it makes a handful of read-only requests.
"""

from __future__ import annotations

import asyncio
import sys

from app.chains.resolver import registry
from app.core.enums import Chain, Direction
from app.logging_config import configure_logging

# Well-known public addresses used purely as reachability probes.
PROBES: dict[Chain, str] = {
    Chain.BITCOIN: "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa",
    Chain.ETHEREUM: "0x28c6c06298d514db089934071355e5743bf21d60",
    Chain.TRON: "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
}


async def main() -> int:
    configure_logging()
    print(f"{'chain':<10} {'provider':<16} {'configured':<11} result")
    print("-" * 78)
    failures = 0

    for chain, address in PROBES.items():
        adapter = registry.live_adapter_for(chain)
        if adapter is None:
            print(f"{chain.value:<10} {'-':<16} {'-':<11} no adapter registered")
            continue

        configured = adapter.is_configured()
        if not configured:
            print(
                f"{chain.value:<10} {adapter.name:<16} {'no':<11} "
                "skipped - set the API key to enable live mode"
            )
            continue

        try:
            page = await adapter.get_transfers(chain, address, Direction.IN, limit=5)
            sample = page.transfers[0] if page.transfers else None
            detail = (
                f"OK  {len(page.transfers)} inbound transfers"
                + (f", latest {sample.amount} {sample.asset}" if sample else "")
            )
        except Exception as exc:
            failures += 1
            detail = f"FAILED  {type(exc).__name__}: {str(exc)[:70]}"

        print(f"{chain.value:<10} {adapter.name:<16} {'yes':<11} {detail}")

    await registry.close()
    print(
        "\nA failure here is not fatal: in hybrid mode the router falls back to "
        "the seeded dataset."
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
