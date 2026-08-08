#!/usr/bin/env python3
"""Unified CLI entrypoint for Kestrel.

Combines server startup, DB migrations, test runner, LLM verification, 
and system diagnostics into a single command interface.

Usage:
  python run.py            # Run migrations & start server (default)
  python run.py start      # Start server (production mode)
  python run.py dev        # Start server with auto-reload (development mode)
  python run.py test       # Run full test suite (pytest)
  python run.py verify     # Probe LLM connectivity & API key status
  python run.py eval       # Run evaluator smoke check against local server
  python run.py doctor     # Run full system diagnostic check
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent


def _get_env() -> dict[str, str]:
    env = os.environ.copy()
    pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{ROOT_DIR}{os.pathsep}{pythonpath}" if pythonpath else str(ROOT_DIR)
    return env


def ensure_data_dirs() -> None:
    """Create data directory for SQLite database."""
    (ROOT_DIR / "data").mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            Path("/data").mkdir(parents=True, exist_ok=True)
        except PermissionError:
            pass


def run_migrations() -> int:
    """Apply Alembic database migrations."""
    print("[kestrel] Running database migrations...", flush=True)
    res = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(ROOT_DIR),
        env=_get_env(),
        check=False,
    )
    if res.returncode != 0:
        print("[kestrel] ERROR: Database migration failed.", flush=True)
    return res.returncode


def cmd_start(args: argparse.Namespace) -> int:
    """Start production server on 0.0.0.0:$PORT."""
    ensure_data_dirs()
    if run_migrations() != 0:
        return 1

    port = os.environ.get("PORT", str(args.port))
    host = os.environ.get("HOST", args.host)
    allowed_ips = "127.0.0.1" if os.name == "nt" else "*"

    print(f"[kestrel] Starting production server on http://{host}:{port}", flush=True)
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
            allowed_ips,
            "--timeout-keep-alive",
            "30",
        ],
        cwd=str(ROOT_DIR),
        env=_get_env(),
    )


def cmd_dev(args: argparse.Namespace) -> int:
    """Start dev server with auto-reload."""
    ensure_data_dirs()
    if run_migrations() != 0:
        return 1

    port = os.environ.get("PORT", str(args.port))
    host = args.host

    print(f"[kestrel] Starting dev server on http://{host}:{port} (--reload)", flush=True)
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
            "--reload",
        ],
        cwd=str(ROOT_DIR),
        env=_get_env(),
    )


def cmd_test(args: argparse.Namespace) -> int:
    """Run full pytest suite."""
    print("[kestrel] Running test suite...", flush=True)
    cmd = [sys.executable, "-m", "pytest", "tests/"]
    if args.verbose:
        cmd.append("-v")
    if args.fast:
        cmd.extend(["-x", "-q"])
    return subprocess.call(cmd, cwd=str(ROOT_DIR), env=_get_env())


def cmd_verify(args: argparse.Namespace) -> int:
    """Probe LLM API key & connectivity."""
    print("[kestrel] Probing LLM connectivity...", flush=True)
    verify_script = ROOT_DIR / "scripts" / "verify_llm.py"
    return subprocess.call([sys.executable, str(verify_script)], cwd=str(ROOT_DIR), env=_get_env())


def cmd_eval(args: argparse.Namespace) -> int:
    """Run evaluator smoke check against live server."""
    print(f"[kestrel] Running smoke evaluation against {args.base_url}...", flush=True)
    smoke_script = ROOT_DIR / "scripts" / "smoke_eval.py"
    return subprocess.call(
        [
            sys.executable,
            str(smoke_script),
            "--base-url",
            args.base_url,
            "--polls",
            str(args.polls),
            "--interval",
            str(args.interval),
        ],
        cwd=str(ROOT_DIR),
        env=_get_env(),
    )


def cmd_doctor(args: argparse.Namespace) -> int:
    """Run comprehensive system diagnostic check."""
    print("=" * 60)
    print(" [KESTREL] SYSTEM DIAGNOSTIC CHECK (DOCTOR)")
    print("=" * 60)

    # 1. Python version
    py_ver = sys.version.split()[0]
    print(f"[1/5] Python Version: {py_ver} ({'OK' if sys.version_info >= (3, 12) else 'WARNING: >=3.12 recommended'})")

    # 2. Environment file
    env_file = ROOT_DIR / ".env"
    print(f"[2/5] Environment (.env): {'FOUND' if env_file.exists() else 'MISSING (.env.example will be used)'}")

    # 3. Database migrations check
    print("[3/5] Database Status:")
    ensure_data_dirs()
    mig_res = run_migrations()
    print(f"      Migrations: {'OK' if mig_res == 0 else 'FAILED'}")

    # 4. LLM Probe
    print("[4/5] LLM Status:")
    verify_res = cmd_verify(args)

    # 5. Quick Test Suite Check
    print("[5/5] Test Suite Check:")
    test_res = subprocess.call(
        [sys.executable, "-m", "pytest", "tests/test_health.py", "-q"],
        cwd=str(ROOT_DIR),
    )
    print(f"      Health Integration Tests: {'PASS' if test_res == 0 else 'FAIL'}")

    print("=" * 60)
    if mig_res == 0 and test_res == 0:
        print(" SUCCESS: System is healthy and ready for execution!")
    else:
        print(" WARNING: Some diagnostic checks flagged issues. See logs above.")
    print("=" * 60)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Kestrel Autonomous AI Security Persona — Master CLI Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # default / start
    start_parser = subparsers.add_parser("start", help="Start production server (default)")
    start_parser.add_argument("--host", default="0.0.0.0", help="Host address (default: 0.0.0.0)")
    start_parser.add_argument("--port", type=int, default=8000, help="Port number (default: 8000)")
    start_parser.set_defaults(func=cmd_start)

    # dev
    dev_parser = subparsers.add_parser("dev", help="Start dev server with auto-reload")
    dev_parser.add_argument("--host", default="127.0.0.1", help="Host address (default: 127.0.0.1)")
    dev_parser.add_argument("--port", type=int, default=8000, help="Port number (default: 8000)")
    dev_parser.set_defaults(func=cmd_dev)

    # test
    test_parser = subparsers.add_parser("test", help="Run full pytest suite")
    test_parser.add_argument("-v", "--verbose", action="store_true", help="Verbose test output")
    test_parser.add_argument("-f", "--fast", action="store_true", help="Stop on first failure (-x -q)")
    test_parser.set_defaults(func=cmd_test)

    # verify
    verify_parser = subparsers.add_parser("verify", help="Probe LLM connectivity & API key status")
    verify_parser.set_defaults(func=cmd_verify)

    # eval
    eval_parser = subparsers.add_parser("eval", help="Run smoke evaluation check")
    eval_parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    eval_parser.add_argument("--polls", type=int, default=18)
    eval_parser.add_argument("--interval", type=float, default=10.0)
    eval_parser.set_defaults(func=cmd_eval)

    # doctor
    doctor_parser = subparsers.add_parser("doctor", help="Run full system diagnostic check")
    doctor_parser.set_defaults(func=cmd_doctor)

    # If no subcommand provided, default to 'start'
    if len(sys.argv) == 1:
        args = parser.parse_args(["start"])
    else:
        args = parser.parse_args()

    if hasattr(args, "func"):
        return args.func(args)
    return cmd_start(args)


if __name__ == "__main__":
    raise SystemExit(main())
