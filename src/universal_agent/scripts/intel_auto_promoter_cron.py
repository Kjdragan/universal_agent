"""Cron entry point: auto-promote top-scored CSI demo-triage candidates.

Fires after `csi_demo_triage_rank` so freshly-scored candidates flow
through the same UTC day. Gated by `UA_INTEL_AUTO_PROMOTE_*` env vars
(see `services/intel_auto_promoter.py`).

Operator gate: `UA_INTEL_AUTO_PROMOTE_ENABLED=0` to disable without a
redeploy. Daily-cap default 2 prevents Cody from being buried under a
backlog spike.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import sys
from typing import Any

from universal_agent.infisical_loader import initialize_runtime_secrets
from universal_agent.services.intel_auto_promoter import promote_top_candidates


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="", help="Deployment profile for Infisical secret loading.")
    parser.add_argument("--dry-run", action="store_true", help="Report what would be promoted, write nothing.")
    return parser.parse_args()


def _emit(payload: dict[str, Any], *, code: int = 0) -> int:
    print(json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True))
    return code


def main() -> int:
    args = _parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    initialize_runtime_secrets(profile=args.profile or None)

    if os.getenv("UA_INTEL_AUTO_PROMOTE_ENABLED", "1").strip().lower() not in {"1", "true", "yes", "on"}:
        return _emit({"ok": True, "skipped": "disabled_by_env"})

    result = promote_top_candidates(dry_run=True if args.dry_run else None)
    payload: dict[str, Any] = {"ok": result.error is None, **dataclasses.asdict(result)}
    return _emit(payload, code=0 if result.error is None else 1)


if __name__ == "__main__":
    sys.exit(main())
