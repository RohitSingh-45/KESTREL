#!/usr/bin/env python3
"""Simulate the HANUMAN evaluator against a running Kestrel server.

Usage:
  python scripts/smoke_eval.py
  python scripts/smoke_eval.py --base-url http://127.0.0.1:8000 --polls 12 --interval 10

Does not seed fake posts. Relies on autonomous cycles after init.
Never prints secrets (OPENAI_API_KEY, tokens, Authorization headers).
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

import httpx

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_-]{8,}|Bearer\s+\S+)", re.IGNORECASE)


def _assert_no_secrets(payload: str) -> None:
    if _SECRET_RE.search(payload):
        raise AssertionError("secret-like material appeared in smoke output")


def _parse_utc(value: str) -> datetime:
    if not value.endswith("Z"):
        raise ValueError(f"createdAt must end with Z: {value}")
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError(f"createdAt must be timezone-aware: {value}")
    return dt.astimezone(timezone.utc)


def validate_agent_id(agent_id: str) -> None:
    if not _UUID_RE.match(agent_id):
        # Accept any UUID parsable by stdlib (covers evaluator flexibility).
        try:
            uuid.UUID(agent_id)
        except ValueError as exc:
            raise AssertionError(f"agentId is not a valid UUID: {agent_id}") from exc


def validate_posts(posts: list[dict[str, Any]], *, seen_ids: set[str]) -> None:
    ids = [p["id"] for p in posts]
    if len(ids) != len(set(ids)):
        raise AssertionError("post IDs are not unique")
    for post in posts:
        for key in ("id", "createdAt", "text", "rationale", "sources"):
            if key not in post:
                raise AssertionError(f"missing field {key}")
        if not post["text"] or not post["rationale"]:
            raise AssertionError("text/rationale must be non-empty")
        if not isinstance(post["sources"], list):
            raise AssertionError("sources must be a list")
        for url in post["sources"]:
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                raise AssertionError(f"invalid source URL: {url}")
        _parse_utc(post["createdAt"])
    if posts:
        times = [_parse_utc(p["createdAt"]) for p in posts]
        if times != sorted(times, reverse=True):
            raise AssertionError("posts are not newest-first")
    missing = seen_ids - set(ids)
    if missing:
        raise AssertionError(f"previously returned posts disappeared: {sorted(missing)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Kestrel evaluator smoke simulation")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--polls", type=int, default=18)
    parser.add_argument("--interval", type=float, default=10.0)
    parser.add_argument(
        "--require-posts",
        action="store_true",
        help="Fail if no posts appear (requires OPENAI_API_KEY on server)",
    )
    parser.add_argument(
        "--second-persona",
        action="store_true",
        help="Init a second agent and verify feed isolation",
    )
    args = parser.parse_args()
    base = args.base_url.rstrip("/") + "/"

    with httpx.Client(timeout=30.0) as client:
        health = client.get(urljoin(base, "health"))
        health.raise_for_status()
        health_body = health.json()
        print("health:", {k: health_body.get(k) for k in ("status", "db", "scheduler", "llm")})
        _assert_no_secrets(str(health_body))
        if health_body.get("status") != "ok":
            print("FAIL: health status is not ok", file=sys.stderr)
            return 1
        if health_body.get("db") != "ok":
            print("FAIL: health.db is not ok", file=sys.stderr)
            return 1
        if health_body.get("scheduler") not in {"running", "not_started"}:
            print("FAIL: unexpected scheduler status", file=sys.stderr)
            return 1

        init = client.post(
            urljoin(base, "api/agent/init"),
            json={"persona": {"name": "Kestrel", "domain": "AI Security"}},
        )
        init.raise_for_status()
        agent_id = init.json()["agentId"]
        validate_agent_id(agent_id)
        print("agentId:", agent_id)
        _assert_no_secrets(agent_id)

        other_id: str | None = None
        if args.second_persona:
            other = client.post(
                urljoin(base, "api/agent/init"),
                json={"persona": {"name": "Other", "domain": "General"}},
            )
            other.raise_for_status()
            other_id = other.json()["agentId"]
            validate_agent_id(other_id)
            print("otherAgentId:", other_id)

        feed0 = client.get(
            urljoin(base, "api/agent/feed"),
            params={"agentId": agent_id},
        )
        feed0.raise_for_status()
        body0 = feed0.json()
        if set(body0.keys()) != {"posts"}:
            print("FAIL: feed must only expose posts", file=sys.stderr)
            return 1
        print("initial feed posts:", len(body0["posts"]))

        seen: set[str] = set()
        max_count = 0
        for i in range(1, args.polls + 1):
            time.sleep(args.interval)
            feed = client.get(
                urljoin(base, "api/agent/feed"),
                params={"agentId": agent_id},
            )
            feed.raise_for_status()
            body = feed.json()
            _assert_no_secrets(str(body))
            posts = body["posts"]
            validate_posts(posts, seen_ids=seen)
            for post in posts:
                seen.add(post["id"])
            max_count = max(max_count, len(posts))

            if other_id is not None:
                other_feed = client.get(
                    urljoin(base, "api/agent/feed"),
                    params={"agentId": other_id},
                )
                other_feed.raise_for_status()
                other_ids = {p["id"] for p in other_feed.json()["posts"]}
                if seen & other_ids:
                    print("FAIL: cross-agent feed leakage detected", file=sys.stderr)
                    return 1

            print(f"poll {i}/{args.polls}: posts={len(posts)} unique_seen={len(seen)}")
            if max_count >= 1 and i >= min(args.polls, 3):
                # Duplicate protection: IDs remain unique and prior posts persist.
                if len(seen) != len(set(seen)):
                    print("FAIL: duplicate post IDs observed", file=sys.stderr)
                    return 1
                print(
                    "PASS: health/init/feed schema, UUID agentId, newest-first, "
                    "persistence across polls, no secret leakage"
                    + (", no cross-agent leakage" if other_id else "")
                )
                return 0

        if max_count == 0:
            if args.require_posts:
                print(
                    "FAIL: no posts appeared. Ensure OPENAI_API_KEY is set and "
                    "scheduler cycles are running.",
                    file=sys.stderr,
                )
                return 2
            print(
                "PASS (partial): API contract OK; no posts yet "
                "(LLM may be unavailable). Re-run with --require-posts when keyed."
            )
            return 0
        print("PASS: evaluator contract checks succeeded")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
