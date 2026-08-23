"""Deterministic fake-but-well-formed addresses for the synthetic dataset.

Addresses are derived from a label string, so a scenario regenerates to exactly
the same graph and demo links keep working across rebuilds. They are real
formats (correct base58check, correct length) so they pass the same validation
path a genuine victim-reported address would.
"""

from __future__ import annotations

import hashlib

import base58

from app.core.enums import Chain

# Namespaced so synthetic addresses can never collide with a real one an
# investigator might paste in from a live complaint.
NAMESPACE = "chainwatch-synthetic-v1"  # fixed seed for deterministic demo data; not product branding, do not rename


def _digest(label: str, size: int) -> bytes:
    return hashlib.sha256(f"{NAMESPACE}:{label}".encode()).digest()[:size]


def _b58check(version: int, payload: bytes) -> str:
    body = bytes([version]) + payload
    checksum = hashlib.sha256(hashlib.sha256(body).digest()).digest()[:4]
    return base58.b58encode(body + checksum).decode()


def evm_address(label: str) -> str:
    return "0x" + _digest(label, 20).hex()


def tron_address(label: str) -> str:
    """TRON mainnet addresses are base58check over 0x41 + 20-byte hash."""
    return _b58check(0x41, _digest(label, 20))


def btc_address(label: str, p2sh: bool = False) -> str:
    return _b58check(0x05 if p2sh else 0x00, _digest(label, 20))


def make_address(chain: Chain, label: str) -> str:
    if chain is Chain.TRON:
        return tron_address(label)
    if chain is Chain.BITCOIN:
        return btc_address(label)
    return evm_address(label)


def make_tx_hash(chain: Chain, label: str) -> str:
    raw = hashlib.sha256(f"{NAMESPACE}:tx:{label}".encode()).hexdigest()
    return raw if chain is Chain.BITCOIN else f"0x{raw}"
