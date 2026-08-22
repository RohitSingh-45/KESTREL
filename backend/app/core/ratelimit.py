"""Token-bucket rate limiting for upstream blockchain APIs.

Etherscan's free tier allows 3 requests per second across every chain sharing
the key, so a trace that fans out across hops will trip it within a second
unless requests are paced. Limiting is mandatory, not defensive.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from app.logging_config import get_logger

logger = get_logger(__name__)


class RateLimitExceeded(Exception):
    """Raised when a caller is unwilling to wait for a token."""


@dataclass
class BucketState:
    tokens: float
    updated_at: float


class TokenBucket:
    """Async token bucket, one per upstream provider.

    Deliberately process-local: a single API instance owns its share of the
    upstream quota, and a distributed limiter would be more moving parts than
    the prototype needs.
    """

    def __init__(self, name: str, rate_per_sec: float, burst: float | None = None) -> None:
        self.name = name
        self.rate = max(0.1, rate_per_sec)
        self.capacity = burst if burst is not None else max(1.0, rate_per_sec)
        self._state = BucketState(tokens=self.capacity, updated_at=time.monotonic())
        self._lock = asyncio.Lock()
        self.total_waited_seconds = 0.0
        self.total_acquisitions = 0

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._state.updated_at
        if elapsed > 0:
            self._state.tokens = min(self.capacity, self._state.tokens + elapsed * self.rate)
            self._state.updated_at = now

    async def acquire(self, tokens: float = 1.0, *, max_wait: float | None = None) -> float:
        """Wait for capacity. Returns how long we waited, in seconds."""
        waited = 0.0
        while True:
            async with self._lock:
                self._refill()
                if self._state.tokens >= tokens:
                    self._state.tokens -= tokens
                    self.total_acquisitions += 1
                    self.total_waited_seconds += waited
                    return waited
                deficit = tokens - self._state.tokens
                sleep_for = deficit / self.rate

            if max_wait is not None and waited + sleep_for > max_wait:
                raise RateLimitExceeded(
                    f"{self.name}: would need to wait {waited + sleep_for:.1f}s for capacity"
                )
            await asyncio.sleep(min(sleep_for, 1.0))
            waited += min(sleep_for, 1.0)

    def snapshot(self) -> dict:
        self._refill()
        return {
            "name": self.name,
            "rate_per_sec": self.rate,
            "available_tokens": round(self._state.tokens, 2),
            "acquisitions": self.total_acquisitions,
            "total_waited_seconds": round(self.total_waited_seconds, 2),
        }


class RateLimiterRegistry:
    def __init__(self) -> None:
        self._buckets: dict[str, TokenBucket] = {}

    def get(self, name: str, rate_per_sec: float) -> TokenBucket:
        bucket = self._buckets.get(name)
        if bucket is None:
            bucket = TokenBucket(name, rate_per_sec)
            self._buckets[name] = bucket
        return bucket

    def snapshot(self) -> list[dict]:
        return [bucket.snapshot() for bucket in self._buckets.values()]


limiters = RateLimiterRegistry()
