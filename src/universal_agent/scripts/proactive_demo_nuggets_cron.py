"""Cron entry point: end-of-day "golden-nuggets" proactive demo judge (Component D).

Fires once at end of day (America/Chicago) AFTER the normal proactive demo-build
lane has run. Critically re-judges the day's REMAINING un-built ``tutorial_build``
candidates and builds 0-2 EXTRA "golden nugget" demos directly via demo_factory's
``build_demo.py`` — never exceeding the 5/day hard ceiling. Dark-factory: builds
none if nothing clears the bar. See
``services/proactive_demo_nuggets.select_and_build_nuggets``.

Operator gate: ``UA_PROACTIVE_DEMO_NUGGETS_ENABLED=0`` (the DEFAULT) makes this a
no-op without a redeploy — we validate the flow before enabling. ``--dry-run``
reports the judge's picks and builds nothing.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

from universal_agent.feature_flags import proactive_demo_nuggets_enabled
from universal_agent.infisical_loader import initialize_runtime_secrets
from universal_agent.services.proactive_demo_nuggets import select_and_build_nuggets


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="", help="Deployment profile for Infisical secret loading.")
    parser.add_argument("--dry-run", action="store_true", help="Report the judge's picks, build nothing.")
    return parser.parse_args()


def _emit(payload: dict[str, Any], *, code: int = 0) -> int:
    print(json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True))
    return code


def main() -> int:
    args = _parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    initialize_runtime_secrets(profile=args.profile or None)

    if not proactive_demo_nuggets_enabled():
        return _emit({"ok": True, "skipped": "disabled_by_env"})

    result = select_and_build_nuggets(dry_run=bool(args.dry_run))
    return _emit({"ok": result.get("error") is None, **result}, code=0 if result.get("error") is None else 1)


if __name__ == "__main__":
    sys.exit(main())
