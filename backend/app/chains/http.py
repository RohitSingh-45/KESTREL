"""Shared HTTP plumbing for upstream explorer APIs."""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

import httpx

from app.config import settings
from app.core.cache import cache
from app.core.errors import UpstreamError
from app.core.ratelimit import RateLimitExceeded, TokenBucket
from app.logging_config import get_logger

logger = get_logger(__name__)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def cache_key(provider: str, url: str, params: dict[str, Any]) -> str:
    canonical = json.dumps({"u": url, "p": params}, sort_keys=True, default=str)
    digest = hashlib.sha256(canonical.encode()).hexdigest()[:32]
    return f"upstream:{provider}:{digest}"


class UpstreamClient:
    """Rate-limited, cached, retrying JSON client for one provider."""

    def __init__(
        self,
        provider: str,
        bucket: TokenBucket,
        *,
        base_url: str = "",
        headers: dict[str, str] | None = None,
        cache_ttl: int | None = None,
        max_retries: int = 3,
    ) -> None:
        self.provider = provider
        self.bucket = bucket
        self.base_url = base_url
        self.cache_ttl = cache_ttl if cache_ttl is not None else settings.upstream_cache_ttl_seconds
        self.max_retries = max_retries
        self._headers = {
            "User-Agent": "ChainWatch/0.1 (law-enforcement prototype)",
            "Accept": "application/json",
            **(headers or {}),
        }
        self._client: httpx.AsyncClient | None = None
        self.cache_hits = 0
        self.upstream_calls = 0

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=settings.http_timeout_seconds,
                follow_redirects=True,
                headers=self._headers,
            )
        return self._client

    async def get_json(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        use_cache: bool = True,
        max_wait: float | None = 20.0,
    ) -> tuple[Any, bool]:
        """Fetch JSON. Returns (payload, served_from_cache)."""
        params = {k: v for k, v in (params or {}).items() if v is not None}
        key = cache_key(self.provider, f"{self.base_url}{path}", params)

        if use_cache:
            cached = await cache.get_json(key)
            if cached is not None:
                self.cache_hits += 1
                return cached, True

        try:
            await self.bucket.acquire(max_wait=max_wait)
        except RateLimitExceeded as exc:
            raise UpstreamError(
                f"{self.provider} rate limit reached", details={"reason": str(exc)}
            ) from exc

        client = await self._http()
        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                self.upstream_calls += 1
                response = await client.get(path, params=params)
                if response.status_code in RETRYABLE_STATUS:
                    raise httpx.HTTPStatusError(
                        f"HTTP {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                payload = response.json()
                if use_cache:
                    await cache.set_json(key, payload, ttl=self.cache_ttl)
                return payload, False
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt == self.max_retries:
                    break
                backoff = min(2 ** (attempt - 1) * 0.75, 6.0)
                logger.warning(
                    "upstream_retry",
                    provider=self.provider,
                    attempt=attempt,
                    backoff=backoff,
                    error=str(exc)[:160],
                )
                await asyncio.sleep(backoff)

        raise UpstreamError(
            f"{self.provider} request failed after {self.max_retries} attempts",
            details={"path": path, "error": str(last_error)[:200]},
        )

    def stats(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "upstream_calls": self.upstream_calls,
            "cache_hits": self.cache_hits,
            **self.bucket.snapshot(),
        }

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
