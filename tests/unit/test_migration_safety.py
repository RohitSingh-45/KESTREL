"""Alembic migration safety checks (Module 11)."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from app.config import get_settings

ROOT = Path(__file__).resolve().parents[2]


def test_alembic_heads_single_and_upgrade_path() -> None:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert len(heads) == 1
    assert heads[0] == "b7c8d9e0f1a2"


def test_alembic_upgrade_head_preserves_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Upgrade a clean DB, insert Module-10-shaped rows, re-upgrade — data remains."""
    from alembic import command

    db_path = tmp_path / "migrate.db"
    # env.py reads DATABASE_URL via get_settings(); override for isolation.
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    get_settings.cache_clear()

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(cfg, "head")

    sync_url = f"sqlite:///{db_path.as_posix()}"
    engine = create_engine(sync_url, poolclass=StaticPool)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO agents (id, name, domain, status, created_at, updated_at) "
                "VALUES ('mig-agent', 'Kestrel', 'AI Security', 'running', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO posts (id, agent_id, created_at, text, rationale, sources_json, "
                "topic, content_hash) VALUES ("
                "'mig-post', 'mig-agent', CURRENT_TIMESTAMP, 'hello', 'why', '[]', "
                "'topic', 'hash1')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO topics (id, agent_id, title, status, created_at) VALUES ("
                "'mig-topic', 'mig-agent', 't', 'selected', CURRENT_TIMESTAMP)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO memory_items (id, agent_id, kind, key, value_json, salience, "
                "created_at, updated_at) VALUES ("
                "'mig-mem', 'mig-agent', 'theme', 'k', '{}', 0.5, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )

    # Idempotent second upgrade must not destroy data.
    command.upgrade(cfg, "head")

    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM agents")).scalar() == 1
        assert conn.execute(text("SELECT count(*) FROM posts")).scalar() == 1
        assert conn.execute(text("SELECT count(*) FROM topics")).scalar() == 1
        assert conn.execute(text("SELECT count(*) FROM memory_items")).scalar() == 1
        assert (
            conn.execute(text("SELECT name FROM agents WHERE id='mig-agent'")).scalar()
            == "Kestrel"
        )
    engine.dispose()
    get_settings.cache_clear()
