"""Seed reference data, the synthetic fraud dataset and demo cases.

    python -m scripts.seed_all              # idempotent top-up
    python -m scripts.seed_all --reset      # rebuild the synthetic dataset
    python -m scripts.seed_all --if-empty   # no-op when already seeded
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.db.bootstrap import init_database
from app.db.session import dispose_engine, session_scope
from app.logging_config import configure_logging
from app.seeds.runner import is_seeded, seed_all


async def main(reset: bool, if_empty: bool, skip_cases: bool) -> int:
    configure_logging()
    await init_database()
    try:
        async with session_scope() as session:
            if if_empty and await is_seeded(session):
                print("Database already seeded; nothing to do.")
                return 0
            stats = await seed_all(
                session, reset_synthetic=reset, with_cases=not skip_cases
            )
    finally:
        await dispose_engine()

    width = max(len(k) for k in stats)
    print("\nSeed summary")
    print("-" * (width + 12))
    for key, value in stats.items():
        print(f"{key.ljust(width)}  {value:>8,}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed the TraceX database")
    parser.add_argument("--reset", action="store_true", help="rebuild the synthetic dataset")
    parser.add_argument("--if-empty", action="store_true", help="skip when already seeded")
    parser.add_argument("--skip-cases", action="store_true", help="do not create demo cases")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.reset, args.if_empty, args.skip_cases)))
