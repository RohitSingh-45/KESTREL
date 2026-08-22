"""Portable column types that behave the same on SQLite and PostgreSQL."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import JSON, Numeric, String
from sqlalchemy.types import TypeDecorator


class CryptoAmount(TypeDecorator):
    """Exact token amounts.

    Evidence-grade values must round-trip exactly, so SQLite stores them as
    text rather than going through its float-backed NUMERIC affinity.
    """

    impl = Numeric(38, 18)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(64))
        return dialect.type_descriptor(Numeric(38, 18))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        value = Decimal(str(value))
        if dialect.name == "sqlite":
            return format(value, "f")
        return value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return Decimal(str(value))


def JSONColumn():  # noqa: N802 - factory reads better capitalised
    """JSON column that works on both backends."""
    return JSON()
