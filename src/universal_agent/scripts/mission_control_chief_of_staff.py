"""Generate or inspect the Mission Control Chief-of-Staff readout."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from universal_agent.services.mission_control_chief_of_staff import (
    generate_and_store_readout,
    get_latest_readout,
    get_recent_journal,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="Generate and store a fresh readout.")
    parser.add_argument("--latest", action="store_true", help="Print the latest stored readout.")
    parser.add_argument("--journal", action="store_true", help="Print recent journal entries.")
    parser.add_argument("--include-evidence", action="store_true", help="Include raw bounded evidence in latest output.")
    parser.add_argument("--journal-limit", type=int, default=10)
    return parser.parse_args()


async def main_async() -> int:
    args = parse_args()
    if args.refresh:
        payload = await generate_and_store_readout()
    elif args.journal:
        payload = {"journal": get_recent_journal(limit=args.journal_limit)}
    else:
        payload = get_latest_readout(include_evidence=args.include_evidence)
        if payload is None:
            payload = {"status": "empty", "message": "No Mission Control Chief-of-Staff readout has been generated yet."}

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    try:
        return asyncio.run(main_async())
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"mission_control_chief_of_staff failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
