#!/usr/bin/env python3
"""Run exactly one autonomous generation cycle against a live API.

Requires OPENAI_API_KEY in the API process environment (not faked).

Usage:
  # Terminal A — API with key:
  #   $env:OPENAI_API_KEY="sk-..."
  #   python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
  #
  # Terminal B:
  python scripts/run_one_cycle.py --base-url http://127.0.0.1:8000 --wait-seconds 180
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any
import httpx

REQUIRED_DONE_OR_SKIPPED = {
    "research",
    "candidate",
    "selection",
    "debate",
    "memory",
    "consistency",
    "intelligence",
    "adaptive",
    "learning",
    "decision",
    "generation",
    "publishing",
    "evaluation",
}


def _get(client: httpx.Client, path: str) -> dict[str, Any]:
    res = client.get(path)
    res.raise_for_status()
    return res.json()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--wait-seconds", type=int, default=180)
    parser.add_argument("--poll-interval", type=float, default=5.0)
    args = parser.parse_args()

    with httpx.Client(base_url=args.base_url, timeout=60.0) as client:
        health = _get(client, "/health")
        print("health:", json.dumps(health))
        if health.get("llm") == "unavailable":
            print(
                "BLOCKER: API reports llm=unavailable. "
                "Set OPENAI_API_KEY on the API process and restart uvicorn.",
                file=sys.stderr,
            )
            return 2

        init = client.post(
            "/api/agent/init",
            json={"persona": {"name": "Kestrel", "domain": "AI Security"}},
        )
        init.raise_for_status()
        agent_id = init.json()["agentId"]
        print("agentId:", agent_id)

        deadline = time.time() + args.wait_seconds
        last: dict[str, Any] | None = None
        while time.time() < deadline:
            state = _get(client, f"/api/dashboard/state?agentId={agent_id}")["state"]
            last = state
            run = state.get("latestRun") or {}
            pipeline = {n["id"]: n["status"] for n in state.get("pipeline") or []}
            posts = state.get("posts") or []
            print(
                f"poll status={run.get('status')} stage={run.get('stage')} "
                f"posts={len(posts)} llm={state.get('hero', {}).get('llmStatus')} "
                f"pipeline={pipeline}"
            )
            if run.get("status") in {"success", "skipped", "failed"} and run.get(
                "finishedAt"
            ):
                break
            # Also accept success when a post appears (cycle finished publishing).
            if posts and all(
                pipeline.get(k) in {"done", "skipped", "blocked"}
                for k in REQUIRED_DONE_OR_SKIPPED
            ):
                break
            time.sleep(args.poll_interval)

        if last is None:
            print("no dashboard state", file=sys.stderr)
            return 1

        run = last.get("latestRun") or {}
        pipeline = {n["id"]: n["status"] for n in last.get("pipeline") or []}
        posts = last.get("posts") or []
        hero = last.get("hero") or {}

        print("--- RESULT ---")
        print("llmStatus:", hero.get("llmStatus"))
        print("run:", json.dumps(run))
        print("pipeline:", json.dumps(pipeline, indent=2))
        print("posts:", len(posts))
        if posts:
            print("latest_post_topic:", posts[0].get("topic"))
            print("latest_post_text_len:", len(posts[0].get("text") or ""))

        if hero.get("llmStatus") in {"unavailable", "unstable"}:
            print("LLM not healthy — cycle incomplete.", file=sys.stderr)
            return 3
        if run.get("status") != "success":
            print(f"cycle status={run.get('status')} (want success)", file=sys.stderr)
            return 4
        if not posts:
            print("no published posts after cycle", file=sys.stderr)
            return 5
        # Core LLM stages must have actually completed (done), not skipped.
        for key in ("research", "candidate", "generation", "publishing", "evaluation"):
            if pipeline.get(key) != "done":
                print(f"stage {key}={pipeline.get(key)} (want done)", file=sys.stderr)
                return 6
        print("ONE COMPLETE CYCLE VERIFIED")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
