#!/usr/bin/env python3
"""Production entrypoint: migrate then serve on 0.0.0.0:$PORT."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _ensure_data_dirs() -> None:
    """Create common SQLite parent dirs before Alembic runs."""
    Path("data").mkdir(parents=True, exist_ok=True)
    # Docker/Render mount — skip on Windows local shells.
    if os.name != "nt":
        Path("/data").mkdir(parents=True, exist_ok=True)


def main() -> int:
    port = os.environ.get("PORT", "8000")
    host = os.environ.get("HOST", "0.0.0.0")
    _ensure_data_dirs()

    print(f"[kestrel] running migrations (host={host} port={port})", flush=True)
    migrate = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=False,
    )
    if migrate.returncode != 0:
        print(
            "[kestrel] alembic upgrade failed — refusing to start "
            "(fix DATABASE_URL / disk mounts)",
            flush=True,
        )
        return migrate.returncode

    print(f"[kestrel] starting uvicorn on {host}:{port}", flush=True)
    return subprocess.call(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            host,
            "--port",
            str(port),
            "--proxy-headers",
            "--forwarded-allow-ips",
            "127.0.0.1" if os.name == "nt" else "*",
            "--timeout-keep-alive",
            "30",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
