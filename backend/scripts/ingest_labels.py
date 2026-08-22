"""Download and load external address-label datasets.

    python -m scripts.ingest_labels --source ofac
    python -m scripts.ingest_labels --source eth-labels --chains ethereum,bsc
    python -m scripts.ingest_labels --source mbal --file data/mbal_sample.csv --limit 50000
    python -m scripts.ingest_labels --source all
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from app.config import DATA_DIR
from app.core.enums import Chain
from app.db.bootstrap import init_database
from app.db.session import dispose_engine, session_scope
from app.logging_config import configure_logging
from app.seeds.label_ingest import ingest_eth_labels, ingest_mbal, ingest_ofac

CACHE_DIR = Path(DATA_DIR) / "labels_cache"


async def run(args: argparse.Namespace) -> int:
    configure_logging()
    await init_database()
    results: dict[str, dict] = {}
    failures: dict[str, str] = {}

    sources = (
        ["ofac", "eth-labels"] if args.source == "all" else [args.source]
    )
    chains = [Chain(c.strip()) for c in args.chains.split(",") if c.strip()]

    try:
        for source in sources:
            async with session_scope() as session:
                try:
                    if source == "ofac":
                        results[source] = await ingest_ofac(
                            session,
                            cached_path=None if args.no_cache else CACHE_DIR / "sdn.xml",
                        )
                    elif source == "eth-labels":
                        results[source] = await ingest_eth_labels(session, chains, args.limit)
                    elif source == "mbal":
                        if not args.file:
                            raise SystemExit("--file is required for the mbal source")
                        results[source] = await ingest_mbal(
                            session, Path(args.file), args.limit
                        )
                    else:
                        raise SystemExit(f"Unknown source: {source}")
                except Exception as exc:
                    failures[source] = f"{type(exc).__name__}: {exc}"
    finally:
        await dispose_engine()

    print("\nIngestion summary")
    print("-" * 46)
    for source, stats in results.items():
        detail = "  ".join(f"{k}={v:,}" for k, v in stats.items())
        print(f"{source:<14} {detail}")
    for source, error in failures.items():
        print(f"{source:<14} FAILED  {error}")
    print(
        "\nNote: the prototype runs fine without these datasets - the curated seed "
        "and synthetic labels already cover the demo scenarios."
    )
    return 1 if failures and not results else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest address label datasets")
    parser.add_argument(
        "--source", default="all", choices=["all", "ofac", "eth-labels", "mbal"]
    )
    parser.add_argument(
        "--chains",
        default="ethereum,bsc,polygon,arbitrum,base",
        help="comma-separated chains for eth-labels",
    )
    parser.add_argument("--file", help="local CSV path for the mbal source")
    parser.add_argument("--limit", type=int, help="cap rows per source")
    parser.add_argument("--no-cache", action="store_true", help="skip the local OFAC cache")
    sys.exit(asyncio.run(run(parser.parse_args())))
