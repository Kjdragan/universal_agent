"""Cron entry point: rank pending CSI demo-triage candidates with one LLM call.

Registered as the ``csi_demo_triage_rank`` system cron in gateway_server. The
``/api/v1/dashboard/claude-code-intel/triage/rerank`` endpoint also calls
``run_ranking()`` directly for on-demand reranks from the dashboard.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

from universal_agent.infisical_loader import initialize_runtime_secrets
from universal_agent.services.csi_demo_triage_ranker import run_ranking


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="", help="Deployment profile for Infisical secret loading.")
    parser.add_argument(
        "--rescore-after-hours",
        type=float,
        default=24.0,
        help="Re-score rows older than this many hours.",
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=60,
        help="Cap candidates per LLM call (single round-trip).",
    )
    return parser.parse_args()


def _emit(payload: dict[str, Any], *, code: int = 0) -> int:
    print(json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True))
    return code


def main() -> int:
    args = _parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    initialize_runtime_secrets(profile=args.profile or None)

    try:
        result = run_ranking(
            rescore_after_hours=args.rescore_after_hours,
            max_candidates=args.max_candidates,
        )
    except Exception as exc:
        return _emit({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, code=1)

    payload = {"ok": result.error is None, **result.to_dict()}
    return _emit(payload, code=0 if result.error is None else 1)


if __name__ == "__main__":
    sys.exit(main())
