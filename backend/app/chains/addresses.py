"""Address format detection, validation and normalisation.

Investigators paste a bare string from a complaint form, so the chain has to be
inferred from the address itself before anything else can happen.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

import base58

from app.core.enums import Chain
from app.core.errors import UnsupportedChainError

EVM_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
TRON_RE = re.compile(r"^T[1-9A-HJ-NP-Za-km-z]{33}$")
BTC_LEGACY_RE = re.compile(r"^[13][1-9A-HJ-NP-Za-km-z]{25,34}$")
BTC_BECH32_RE = re.compile(r"^bc1[02-9ac-hj-np-z]{11,71}$")
TX_HASH_RE = re.compile(r"^(0x)?[0-9a-fA-F]{64}$")

# EVM chains share an address space, so a bare 0x address is ambiguous.
EVM_CANDIDATES = [Chain.ETHEREUM, Chain.BSC, Chain.POLYGON, Chain.ARBITRUM, Chain.BASE]

# Complaint forms name the network however the complainant or clerk knew it -
# "USDT (TRC20)", "BTC", "Binance Smart Chain" - so accept the vernacular
# rather than making a valid report fail on a spelling.
CHAIN_ALIASES: dict[str, Chain] = {
    "btc": Chain.BITCOIN,
    "bitcoin": Chain.BITCOIN,
    "xbt": Chain.BITCOIN,
    "eth": Chain.ETHEREUM,
    "ether": Chain.ETHEREUM,
    "ethereum": Chain.ETHEREUM,
    "erc20": Chain.ETHEREUM,
    "erc-20": Chain.ETHEREUM,
    "mainnet": Chain.ETHEREUM,
    "trx": Chain.TRON,
    "tron": Chain.TRON,
    "trc20": Chain.TRON,
    "trc-20": Chain.TRON,
    "usdttrc20": Chain.TRON,
    "bsc": Chain.BSC,
    "bnb": Chain.BSC,
    "bep20": Chain.BSC,
    "bep-20": Chain.BSC,
    "binancesmartchain": Chain.BSC,
    "bnbsmartchain": Chain.BSC,
    "bnbchain": Chain.BSC,
    "polygon": Chain.POLYGON,
    "matic": Chain.POLYGON,
    "pol": Chain.POLYGON,
    "arbitrum": Chain.ARBITRUM,
    "arb": Chain.ARBITRUM,
    "arbitrumone": Chain.ARBITRUM,
    "base": Chain.BASE,
}


def parse_chain_hint(hint: Chain | str | None) -> Chain | None:
    """Best-effort read of a free-text network name; None when unrecognised."""
    if hint is None or isinstance(hint, Chain):
        return hint
    text = str(hint).strip().lower()
    if not text:
        return None
    try:
        return Chain(text)
    except ValueError:
        pass
    # "USDT (TRC20)" -> "usdttrc20"
    squashed = re.sub(r"[^a-z0-9]", "", text)
    if squashed in CHAIN_ALIASES:
        return CHAIN_ALIASES[squashed]
    for alias, chain in CHAIN_ALIASES.items():
        if alias in squashed:
            return chain
    return None


@dataclass(frozen=True)
class AddressInfo:
    address: str
    candidates: list[Chain]
    primary: Chain
    ambiguous: bool
    format: str

    @property
    def is_evm(self) -> bool:
        return self.primary.is_evm


def _b58check_ok(address: str) -> bool:
    try:
        raw = base58.b58decode(address)
    except ValueError:
        return False
    if len(raw) < 5:
        return False
    payload, checksum = raw[:-4], raw[-4:]
    return hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4] == checksum


def detect_address(raw: str) -> AddressInfo:
    """Work out which chain(s) an address string could belong to."""
    address = (raw or "").strip()
    if not address:
        raise UnsupportedChainError("Address is empty")

    if EVM_RE.match(address):
        return AddressInfo(
            address=address.lower(),
            candidates=list(EVM_CANDIDATES),
            # Ethereum is the conventional default; the caller can override.
            primary=Chain.ETHEREUM,
            ambiguous=True,
            format="evm",
        )

    if TRON_RE.match(address) and _b58check_ok(address):
        return AddressInfo(
            address=address,
            candidates=[Chain.TRON],
            primary=Chain.TRON,
            ambiguous=False,
            format="tron-base58",
        )

    if BTC_BECH32_RE.match(address.lower()):
        return AddressInfo(
            address=address.lower(),
            candidates=[Chain.BITCOIN],
            primary=Chain.BITCOIN,
            ambiguous=False,
            format="btc-bech32",
        )

    if BTC_LEGACY_RE.match(address) and _b58check_ok(address):
        return AddressInfo(
            address=address,
            candidates=[Chain.BITCOIN],
            primary=Chain.BITCOIN,
            ambiguous=False,
            format="btc-base58",
        )

    raise UnsupportedChainError(
        f"'{address[:24]}...' does not match any supported address format",
        details={"supported": ["evm (0x...)", "tron (T...)", "bitcoin (1/3/bc1...)"]},
    )


def resolve_chain(raw: str, hint: Chain | str | None = None) -> tuple[str, Chain, AddressInfo]:
    """Return (normalised address, chain, detection info), honouring a hint."""
    info = detect_address(raw)
    chain = parse_chain_hint(hint)
    if chain is None:
        # An unreadable hint is not worth rejecting a valid address over; the
        # address format is the stronger signal anyway.
        return info.address, info.primary, info

    if chain not in info.candidates:
        raise UnsupportedChainError(
            f"Address format is not valid on {chain.display_name}",
            details={"detected": [c.value for c in info.candidates]},
        )
    return info.address, chain, info


def normalise_address(address: str, chain: Chain) -> str:
    """Canonical storage form: EVM lowercased, others left byte-exact."""
    if chain.is_evm:
        return address.strip().lower()
    return address.strip()


def is_valid_for_chain(address: str, chain: Chain) -> bool:
    try:
        info = detect_address(address)
    except UnsupportedChainError:
        return False
    return chain in info.candidates


def is_tx_hash(value: str) -> bool:
    return bool(TX_HASH_RE.match((value or "").strip()))


def explorer_url(chain: Chain, address: str) -> str:
    base = {
        Chain.BITCOIN: "https://blockchair.com/bitcoin/address/",
        Chain.ETHEREUM: "https://etherscan.io/address/",
        Chain.BSC: "https://bscscan.com/address/",
        Chain.POLYGON: "https://polygonscan.com/address/",
        Chain.ARBITRUM: "https://arbiscan.io/address/",
        Chain.BASE: "https://basescan.org/address/",
        Chain.TRON: "https://tronscan.org/#/address/",
    }[chain]
    return f"{base}{address}"


def tx_explorer_url(chain: Chain, tx_hash: str) -> str:
    base = {
        Chain.BITCOIN: "https://blockchair.com/bitcoin/transaction/",
        Chain.ETHEREUM: "https://etherscan.io/tx/",
        Chain.BSC: "https://bscscan.com/tx/",
        Chain.POLYGON: "https://polygonscan.com/tx/",
        Chain.ARBITRUM: "https://arbiscan.io/tx/",
        Chain.BASE: "https://basescan.org/tx/",
        Chain.TRON: "https://tronscan.org/#/transaction/",
    }[chain]
    return f"{base}{tx_hash}"
