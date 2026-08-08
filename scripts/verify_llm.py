#!/usr/bin/env python3
"""Safely probe LLM connectivity without printing secrets.

Output is exactly one of:
  LLM ready
  LLM authentication failed
  LLM unavailable
  LLM unstable

Usage:
  python scripts/verify_llm.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Add project root to sys.path when executed directly
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.config import get_settings
from app.services.llm.factory import build_llm_from_settings
from app.services.llm.status import LLM_STATUS_LABELS, probe_llm


async def main() -> int:
    get_settings.cache_clear()
    settings = get_settings()
    client = build_llm_from_settings(settings)
    status = await probe_llm(client)
    print(LLM_STATUS_LABELS.get(status, "LLM UNAVAILABLE"))
    return {
        "ready": 0,
        "authentication_failed": 3,
        "unstable": 4,
        "unavailable": 2,
    }.get(status, 2)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
