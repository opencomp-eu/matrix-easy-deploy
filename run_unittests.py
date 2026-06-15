#!/usr/bin/env python3
"""Run unit tests via discovery (safe alternative to `python -m unittest tests/*`)."""

from __future__ import annotations

import os
import shutil
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ["PYTHONPYCACHEPREFIX"] = str(ROOT / ".pycache")

stale_cache = ROOT / "tests" / "__pycache__"
if stale_cache.is_dir():
    shutil.rmtree(stale_cache)


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if args and args[0] == "discover":
        args = args[1:]
    program_args = ["discover", "-s", "tests", "-p", "test_*.py", *args]
    return unittest.main(module=None, argv=[sys.argv[0], *program_args], exit=False)


if __name__ == "__main__":
    raise SystemExit(main())
