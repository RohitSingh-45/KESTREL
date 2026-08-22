"""Cache layer with a Redis backend and an in-process fallback.

The prototype must run on a laptop with no Redis, so every cache call degrades
to a bounded in-memory dict rather than failing the request.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)

try:  # pragma: no cover - import guard
    from redis.asyncio import Redis
except ImportError:  # pragma: no cover
    Redis = None  # type: ignore[assignment]


class _MemoryCache:
    """Small TTL dict used when Redis is unavailable."""

    def __init__(self, max_entries: int = 20_000) -> None:
        self._data: dict[str, tuple[float, str]] = {}
        self._max = max_entries
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> str | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at and expires_at < time.time():
            self._data.pop(key, None)
            return None
        return value

    async def set(self, key: str, value: str, ttl: int | None = None) -> None:
        async with self._lock:
            if len(self._data) >= self._max:
                # Cheap eviction: drop the oldest tenth by expiry.
                doomed = sorted(self._data.items(), key=lambda kv: kv[1][0])[: self._max // 10]
                for k, _ in doomed:
                    self._data.pop(k, None)
            self._data[key] = (time.time() + ttl if ttl else 0.0, value)

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self._data.pop(key, None)

    async def incr_float(self, key: str, amount: float) -> float:
        current = float(await self.get(key) or 0.0) + amount
        await self.set(key, str(current))
        return current

    async def clear_prefix(self, prefix: str) -> int:
        doomed = [k for k in self._data if k.startswith(prefix)]
        for key in doomed:
            self._data.pop(key, None)
        return len(doomed)


class CacheClient:
    def __init__(self) -> None:
        self._redis: Any | None = None
        self._memory = _MemoryCache()
        self._redis_checked = False
        self._list_lock = asyncio.Lock()
        self.backend = "memory"

    async def _client(self) -> Any | None:
        if self._redis_checked:
            return self._redis
        self._redis_checked = True
        if Redis is None:
            return None
        try:
            client = Redis.from_url(settings.redis_url, decode_responses=True)
            await asyncio.wait_for(client.ping(), timeout=2.0)
            self._redis = client
            self.backend = "redis"
            logger.info("cache_backend_selected", backend="redis")
        except Exception as exc:
            if settings.redis_required:
                raise
            logger.warning("redis_unavailable_using_memory", error=str(exc))
            self._redis = None
        return self._redis

    async def ping(self) -> bool:
        client = await self._client()
        if client is None:
            return False
        try:
            return bool(await client.ping())
        except Exception:
            return False

    async def get_json(self, key: str) -> Any | None:
        client = await self._client()
        raw = await client.get(key) if client else await self._memory.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    async def set_json(self, key: str, value: Any, ttl: int | None = None) -> None:
        raw = json.dumps(value, default=str)
        client = await self._client()
        if client:
            await client.set(key, raw, ex=ttl)
        else:
            await self._memory.set(key, raw, ttl)

    async def delete(self, *keys: str) -> None:
        if not keys:
            return
        client = await self._client()
        if client:
            await client.delete(*keys)
        else:
            await self._memory.delete(*keys)

    async def clear_prefix(self, prefix: str) -> int:
        client = await self._client()
        if client is None:
            return await self._memory.clear_prefix(prefix)
        removed = 0
        async for key in client.scan_iter(match=f"{prefix}*", count=500):
            await client.delete(key)
            removed += 1
        return removed

    async def list_append(
        self, key: str, value: Any, *, ttl: int | None = None, max_len: int | None = None
    ) -> int:
        """Append to an ordered log and return its new length.

        Backs the trace progress stream, which the SSE endpoint reads by offset
        so a client that reconnects mid-trace can replay what it missed.
        """
        raw = json.dumps(value, default=str)
        client = await self._client()
        if client:
            async with client.pipeline(transaction=True) as pipe:
                pipe.rpush(key, raw)
                if max_len:
                    pipe.ltrim(key, -max_len, -1)
                if ttl:
                    pipe.expire(key, ttl)
                pipe.llen(key)
                results = await pipe.execute()
            return int(results[-1])

        async with self._list_lock:
            items = json.loads(await self._memory.get(key) or "[]")
            items.append(raw)
            if max_len and len(items) > max_len:
                items = items[-max_len:]
            await self._memory.set(key, json.dumps(items), ttl)
            return len(items)

    async def list_range(self, key: str, start: int = 0) -> list[Any]:
        client = await self._client()
        if client:
            raw_items = await client.lrange(key, start, -1)
        else:
            raw_items = json.loads(await self._memory.get(key) or "[]")[start:]
        decoded: list[Any] = []
        for raw in raw_items:
            try:
                decoded.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
        return decoded

    async def publish(self, channel: str, message: Any) -> None:
        client = await self._client()
        if client:
            await client.publish(channel, json.dumps(message, default=str))

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None
            self._redis_checked = False


cache = CacheClient()
