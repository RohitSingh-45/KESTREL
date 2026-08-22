"""USD valuation for traced amounts.

Deliberately simple: stablecoins are pegged, native assets use an indicative
spot price refreshed from a free endpoint when reachable. Every figure the UI
shows is labelled "indicative", because historical per-block pricing is out of
scope for a prototype and quoting it as exact would be misleading in a report.
"""

from __future__ import annotations

from decimal import Decimal

import httpx

from app.core.cache import cache
from app.core.enums import Chain
from app.logging_config import get_logger

logger = get_logger(__name__)

CACHE_KEY = "prices:spot:v1"
CACHE_TTL = 1800

STABLECOINS = {"USDT", "USDC", "DAI", "BUSD", "TUSD", "FDUSD", "USDD"}

FALLBACK_PRICES: dict[str, float] = {
    "BTC": 64_000.0,
    "ETH": 3_300.0,
    "BNB": 590.0,
    "POL": 0.48,
    "MATIC": 0.48,
    "TRX": 0.14,
}

COINGECKO_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "BNB": "binancecoin",
    "POL": "polygon-ecosystem-token",
    "TRX": "tron",
}

COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"


async def _fetch_spot_prices() -> dict[str, float]:
    params = {"ids": ",".join(COINGECKO_IDS.values()), "vs_currencies": "usd"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(COINGECKO_URL, params=params)
        response.raise_for_status()
        payload = response.json()
    prices: dict[str, float] = {}
    for symbol, gecko_id in COINGECKO_IDS.items():
        value = payload.get(gecko_id, {}).get("usd")
        if isinstance(value, (int, float)) and value > 0:
            prices[symbol] = float(value)
    return prices


async def get_prices() -> dict[str, float]:
    cached = await cache.get_json(CACHE_KEY)
    if isinstance(cached, dict) and cached:
        return {**FALLBACK_PRICES, **cached}
    try:
        live = await _fetch_spot_prices()
        if live:
            await cache.set_json(CACHE_KEY, live, ttl=CACHE_TTL)
            return {**FALLBACK_PRICES, **live}
    except Exception as exc:
        logger.warning("price_fetch_failed_using_fallback", error=str(exc))
    return dict(FALLBACK_PRICES)


async def to_usd(amount: Decimal | float, asset: str) -> float:
    symbol = (asset or "").upper()
    if symbol in STABLECOINS:
        return round(float(amount), 2)
    prices = await get_prices()
    price = prices.get(symbol)
    if price is None:
        return 0.0
    return round(float(amount) * price, 2)


def to_usd_sync(amount: Decimal | float, asset: str, prices: dict[str, float]) -> float:
    symbol = (asset or "").upper()
    if symbol in STABLECOINS:
        return round(float(amount), 2)
    price = prices.get(symbol)
    if price is None:
        return 0.0
    return round(float(amount) * price, 2)


def native_asset(chain: Chain) -> str:
    return chain.native_asset
