"""Ingest external address-label datasets into the label store.

Supported sources:
  eth-labels  - dawsbot/eth-labels public API (MIT), ~169k labelled EVM addresses
  ofac        - OFAC SDN list, the authoritative source for sanctioned addresses
  mbal        - MBAL / Codatta style CSV, supplied as a local file

Each source writes with its own `source` tag so entries can be refreshed
independently and their provenance is visible in the evidence trail.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chains.addresses import detect_address
from app.core.enums import Chain, EntityType
from app.core.errors import UnsupportedChainError
from app.db.models import AddressLabel, Vasp
from app.logging_config import get_logger

logger = get_logger(__name__)

ETH_LABELS_URL = "https://eth-labels.com/accounts"
OFAC_SDN_URLS = [
    "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.XML",
    "https://www.treasury.gov/ofac/downloads/sdn.xml",
]

ETH_LABELS_CHAINS: dict[int, Chain] = {
    1: Chain.ETHEREUM,
    56: Chain.BSC,
    137: Chain.POLYGON,
    42161: Chain.ARBITRUM,
    8453: Chain.BASE,
}

# Labels that carry no investigative value.
NOISE_LABELS = {"burn", "blocked", "genesis", "null", "spam", "dead-address"}

KEYWORD_ENTITY_TYPES: list[tuple[re.Pattern[str], EntityType]] = [
    (re.compile(r"mixer|tumbl|tornado|coinjoin|wasabi|samourai"), EntityType.MIXER),
    (re.compile(r"bridge|wormhole|stargate|multichain|cbridge|thorchain"), EntityType.BRIDGE),
    (re.compile(r"ransom"), EntityType.RANSOMWARE),
    (re.compile(r"darknet|darkmarket|hydra|silk.?road"), EntityType.DARKNET),
    (re.compile(r"phish|hack|heist|exploit|scam|fake|drainer|rug"), EntityType.SCAM),
    (re.compile(r"sanction|ofac|sdn"), EntityType.SANCTIONED),
    (re.compile(r"gambl|casino|betting|dice"), EntityType.GAMBLING),
    (re.compile(r"exchange|cex"), EntityType.CEX_HOT_WALLET),
    (re.compile(r"token.?contract|erc.?20|erc.?721|stablecoin"), EntityType.TOKEN_CONTRACT),
    (re.compile(r"defi|dex|amm|lending|staking|router|swap"), EntityType.DEFI_CONTRACT),
    (re.compile(r"p2p|otc|merchant"), EntityType.P2P_MERCHANT),
]


@dataclass
class LabelRecord:
    chain: Chain
    address: str
    label: str
    entity_name: str | None
    entity_type: EntityType
    category: str | None
    confidence: float
    is_sanctioned: bool
    vasp_slug: str | None
    details: dict | None = None


class VaspMatcher:
    """Maps a free-text entity string onto a VASP in the directory."""

    def __init__(self, vasps: Iterable[Vasp]) -> None:
        self._by_alias: dict[str, str] = {}
        for vasp in vasps:
            names = [vasp.name, vasp.slug, *(vasp.aliases or [])]
            for name in names:
                if name:
                    self._by_alias[name.lower().strip()] = vasp.slug

    def match(self, text: str | None) -> str | None:
        if not text:
            return None
        cleaned = text.lower().strip()
        if cleaned in self._by_alias:
            return self._by_alias[cleaned]
        # "Binance 14", "Binance: Hot Wallet" -> binance
        for alias, slug in self._by_alias.items():
            if len(alias) >= 4 and cleaned.startswith(alias):
                return slug
        return None


def classify(label: str, name_tag: str | None) -> EntityType:
    haystack = f"{label} {name_tag or ''}".lower()
    for pattern, entity_type in KEYWORD_ENTITY_TYPES:
        if pattern.search(haystack):
            return entity_type
    return EntityType.UNKNOWN


def _entity_name_from_tag(name_tag: str | None) -> str | None:
    """"Binance 14" / "Binance: Hot Wallet" -> "Binance"."""
    if not name_tag:
        return None
    head = re.split(r"[:|]", name_tag)[0].strip()
    head = re.sub(r"\s+\d+$", "", head).strip()
    return head or None


# ---------------------------------------------------------------------------
# eth-labels
# ---------------------------------------------------------------------------


async def fetch_eth_labels(
    chain: Chain, *, limit: int | None = None, timeout: float = 120.0
) -> list[dict]:
    chain_id = next((cid for cid, c in ETH_LABELS_CHAINS.items() if c is chain), None)
    if chain_id is None:
        raise ValueError(f"eth-labels does not cover {chain.value}")
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(
            ETH_LABELS_URL,
            params={"chainId": chain_id},
            headers={"User-Agent": "ChainWatch/0.1 (law-enforcement prototype)"},
        )
        response.raise_for_status()
        rows = response.json()
    if limit:
        rows = rows[:limit]
    return rows


def parse_eth_labels(rows: Iterable[dict], matcher: VaspMatcher) -> Iterator[LabelRecord]:
    for row in rows:
        address = (row.get("address") or "").lower()
        if not re.fullmatch(r"0x[0-9a-f]{40}", address):
            continue
        label = (row.get("label") or "").strip()
        if not label or label.lower() in NOISE_LABELS:
            continue
        chain = ETH_LABELS_CHAINS.get(int(row.get("chainId") or 1), Chain.ETHEREUM)
        name_tag = (row.get("nameTag") or "").strip() or None
        entity_name = _entity_name_from_tag(name_tag) or label.title()

        vasp_slug = matcher.match(entity_name) or matcher.match(label)
        entity_type = classify(label, name_tag)
        if vasp_slug:
            entity_type = (
                EntityType.CEX_DEPOSIT
                if name_tag and "deposit" in name_tag.lower()
                else EntityType.CEX_HOT_WALLET
            )

        yield LabelRecord(
            chain=chain,
            address=address,
            label=name_tag or label,
            entity_name=entity_name,
            entity_type=entity_type,
            category=label,
            confidence=0.92 if vasp_slug else 0.82,
            is_sanctioned=False,
            vasp_slug=vasp_slug,
            details={"raw_label": label, "name_tag": name_tag},
        )


# ---------------------------------------------------------------------------
# OFAC SDN
# ---------------------------------------------------------------------------

DIGITAL_CURRENCY_ID = re.compile(r"^Digital Currency Address\s*-\s*([A-Z0-9]+)$", re.IGNORECASE)


async def fetch_ofac_sdn(timeout: float = 180.0) -> bytes:
    last_error: Exception | None = None
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for url in OFAC_SDN_URLS:
            try:
                response = await client.get(
                    url, headers={"User-Agent": "ChainWatch/0.1 (law-enforcement prototype)"}
                )
                response.raise_for_status()
                return response.content
            except Exception as exc:  # try the next mirror
                last_error = exc
                logger.warning("ofac_fetch_failed", url=url, error=str(exc))
    raise RuntimeError(f"Could not download the OFAC SDN list: {last_error}")


def parse_ofac_sdn(xml_bytes: bytes) -> Iterator[LabelRecord]:
    """Pull every 'Digital Currency Address - X' identifier out of the SDN list."""
    root = ET.fromstring(xml_bytes)
    namespace = ""
    if root.tag.startswith("{"):
        namespace = root.tag[: root.tag.index("}") + 1]

    def tag(name: str) -> str:
        return f"{namespace}{name}"

    for entry in root.iter(tag("sdnEntry")):
        last_name = (entry.findtext(tag("lastName")) or "").strip()
        first_name = (entry.findtext(tag("firstName")) or "").strip()
        entity_name = " ".join(part for part in (first_name, last_name) if part) or "SDN Entity"
        uid = (entry.findtext(tag("uid")) or "").strip()
        programs = [
            (node.text or "").strip()
            for node in entry.iter(tag("program"))
            if (node.text or "").strip()
        ]

        id_list = entry.find(tag("idList"))
        if id_list is None:
            continue
        for id_node in id_list.iter(tag("id")):
            id_type = (id_node.findtext(tag("idType")) or "").strip()
            match = DIGITAL_CURRENCY_ID.match(id_type)
            if not match:
                continue
            address = (id_node.findtext(tag("idNumber")) or "").strip()
            if not address:
                continue
            try:
                info = detect_address(address)
            except UnsupportedChainError:
                # SDN carries assets we do not index (XMR, ZEC, LTC, ...).
                continue

            yield LabelRecord(
                chain=info.primary,
                address=info.address,
                label=f"OFAC SDN: {entity_name}",
                entity_name=entity_name,
                entity_type=EntityType.SANCTIONED,
                category=match.group(1).upper(),
                confidence=1.0,
                is_sanctioned=True,
                vasp_slug=None,
                details={"uid": uid, "programs": programs, "id_type": id_type},
            )


# ---------------------------------------------------------------------------
# MBAL / Codatta style CSV
# ---------------------------------------------------------------------------

MBAL_CHAIN_ALIASES: dict[str, Chain] = {
    "bitcoin": Chain.BITCOIN,
    "bitcoin_mainnet": Chain.BITCOIN,
    "btc": Chain.BITCOIN,
    "ethereum": Chain.ETHEREUM,
    "ethereum_mainnet": Chain.ETHEREUM,
    "eth": Chain.ETHEREUM,
    "bnb_chain_mainnet": Chain.BSC,
    "bsc": Chain.BSC,
    "polygon_mainnet": Chain.POLYGON,
    "polygon": Chain.POLYGON,
    "tron": Chain.TRON,
    "tron_mainnet": Chain.TRON,
}


def parse_mbal_csv(text: str, matcher: VaspMatcher, limit: int | None = None) -> Iterator[LabelRecord]:
    reader = csv.DictReader(io.StringIO(text))
    count = 0
    for row in reader:
        if limit and count >= limit:
            return
        raw_chain = (row.get("chain") or row.get("blockchain") or "").strip().lower()
        chain = MBAL_CHAIN_ALIASES.get(raw_chain)
        address = (row.get("address") or "").strip()
        if not chain or not address:
            continue
        if chain.is_evm:
            address = address.lower()

        categories = (row.get("categories") or row.get("category") or "").strip()
        entity = (row.get("entity") or row.get("name") or "").strip() or None
        primary_category = categories.split(",")[0].strip() if categories else ""
        entity_type = classify(primary_category, entity)
        vasp_slug = matcher.match(entity)
        if vasp_slug and entity_type is EntityType.UNKNOWN:
            entity_type = EntityType.CEX_HOT_WALLET

        source_kind = (row.get("source") or "").strip().lower()
        confidence = {"ground_truth": 0.95, "heuristic": 0.7, "external": 0.8}.get(source_kind, 0.8)

        yield LabelRecord(
            chain=chain,
            address=address,
            label=f"{entity or primary_category or 'labelled address'}",
            entity_name=entity,
            entity_type=entity_type,
            category=primary_category or None,
            confidence=confidence,
            is_sanctioned="sanction" in categories.lower(),
            vasp_slug=vasp_slug,
            details={"categories": categories, "origin": source_kind} if categories else None,
        )
        count += 1


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


async def load_matcher(session: AsyncSession) -> VaspMatcher:
    vasps = (await session.execute(select(Vasp))).scalars().all()
    return VaspMatcher(vasps)


async def store_labels(
    session: AsyncSession,
    records: Iterable[LabelRecord],
    *,
    source: str,
    replace: bool = True,
    batch_size: int = 2_000,
) -> dict[str, int]:
    if replace:
        await session.execute(delete(AddressLabel).where(AddressLabel.source == source))
        await session.flush()

    seen: set[tuple[str, str, str]] = set()
    rows: list[dict] = []
    stats = {"seen": 0, "stored": 0, "duplicates": 0, "sanctioned": 0}

    async def flush(pending: list[dict]) -> None:
        if not pending:
            return
        session.add_all([AddressLabel(**row) for row in pending])
        await session.flush()
        pending.clear()

    for record in records:
        stats["seen"] += 1
        key = (record.chain.value, record.address, record.label[:255])
        if key in seen:
            stats["duplicates"] += 1
            continue
        seen.add(key)
        if record.is_sanctioned:
            stats["sanctioned"] += 1
        rows.append(
            {
                "chain": record.chain,
                "address": record.address,
                "label": record.label[:255],
                "entity_name": (record.entity_name or None) and record.entity_name[:160],
                "entity_type": record.entity_type,
                "category": (record.category or None) and record.category[:80],
                "source": source,
                "confidence": record.confidence,
                "is_sanctioned": record.is_sanctioned,
                "vasp_slug": record.vasp_slug,
                "details": record.details,
            }
        )
        stats["stored"] += 1
        if len(rows) >= batch_size:
            await flush(rows)

    await flush(rows)
    logger.info("labels_ingested", source=source, **stats)
    return stats


async def ingest_eth_labels(
    session: AsyncSession, chains: list[Chain], limit: int | None = None
) -> dict[str, int]:
    matcher = await load_matcher(session)
    all_rows: list[dict] = []
    for chain in chains:
        rows = await fetch_eth_labels(chain, limit=limit)
        logger.info("eth_labels_fetched", chain=chain.value, rows=len(rows))
        all_rows.extend(rows)
    return await store_labels(
        session, parse_eth_labels(all_rows, matcher), source="eth-labels"
    )


async def ingest_ofac(session: AsyncSession, cached_path: Path | None = None) -> dict[str, int]:
    if cached_path and cached_path.exists():
        payload = cached_path.read_bytes()
    else:
        payload = await fetch_ofac_sdn()
        if cached_path:
            cached_path.parent.mkdir(parents=True, exist_ok=True)
            cached_path.write_bytes(payload)
    stats = await store_labels(session, parse_ofac_sdn(payload), source="ofac-sdn")
    stats["published"] = 1
    return stats


async def ingest_mbal(
    session: AsyncSession, path: Path, limit: int | None = None
) -> dict[str, int]:
    matcher = await load_matcher(session)
    text = path.read_text(encoding="utf-8", errors="replace")
    return await store_labels(
        session, parse_mbal_csv(text, matcher, limit), source=f"mbal:{path.stem}"
    )


async def label_source_stats(session: AsyncSession) -> list[dict]:
    from sqlalchemy import func

    rows = await session.execute(
        select(
            AddressLabel.source,
            func.count().label("total"),
            func.sum(func.cast(AddressLabel.is_sanctioned, __import__("sqlalchemy").Integer)),
        ).group_by(AddressLabel.source)
    )
    return [
        {
            "source": source,
            "labels": int(total or 0),
            "sanctioned": int(sanctioned or 0),
            "last_updated": None,
        }
        for source, total, sanctioned in rows.all()
    ]


def utc_now_iso() -> str:
    return datetime.utcnow().isoformat()
