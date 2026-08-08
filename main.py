#!/usr/bin/env python3
"""Root main.py runner for Kestrel.

Delegates commands to run.py.

Usage:
  python main.py          # Start production server
  python main.py dev      # Start dev server (--reload)
  python main.py test     # Run test suite
  python main.py verify   # Probe LLM key status
  python main.py doctor   # Run full system diagnostics
"""

from run import main

if __name__ == "__main__":
    raise SystemExit(main())
