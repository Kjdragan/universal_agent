#!/usr/bin/env python3
"""Fail fast if deploy-time service entrypoints cannot import cleanly."""

from __future__ import annotations

import importlib
import sys
import traceback
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


MODULES = (
    "universal_agent.gateway_server",
    "universal_agent.api.server",
    "universal_agent.bot.main",
    "universal_agent.vp.worker_main",
)


def main() -> int:
    failures: list[tuple[str, BaseException]] = []
    for module_name in MODULES:
        try:
            importlib.import_module(module_name)
            print(f"IMPORT_OK {module_name}")
        except BaseException as exc:  # pragma: no cover - deploy-time probe
            failures.append((module_name, exc))
            print(f"IMPORT_FAIL {module_name}: {type(exc).__name__}: {exc}", file=sys.stderr)
            traceback.print_exc()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
