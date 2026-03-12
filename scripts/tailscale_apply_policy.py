#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from universal_agent.tailscale_admin import (
    TailscaleAdminClient,
    TailscaleAdminError,
    load_policy_overlay,
    merge_policy_overlay,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate and apply a Tailscale policy overlay.")
    parser.add_argument("--policy-file", required=True, help="Path to a JSON/HuJSON policy overlay file.")
    parser.add_argument("--tailnet", default="", help="Tailnet name. Defaults to TAILSCALE_TAILNET.")
    parser.add_argument("--token-env", default="TAILSCALE_ADMIN_API_TOKEN", help="Env var holding the Tailscale admin API token.")
    parser.add_argument("--dry-run", action="store_true", help="Validate only; do not apply changes.")
    parser.add_argument(
        "--write-merged-json",
        default="",
        help="Optional path to write the merged live policy JSON for inspection.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    tailnet = str(args.tailnet or os.getenv("TAILSCALE_TAILNET") or "").strip()
    token = str(os.getenv(args.token_env) or "").strip()
    if not tailnet:
        raise SystemExit("Missing --tailnet and TAILSCALE_TAILNET is unset.")
    if not token:
        raise SystemExit(f"Missing Tailscale admin token in env var {args.token_env}.")

    policy_path = Path(args.policy_file).expanduser().resolve()
    overlay = load_policy_overlay(policy_path)

    with TailscaleAdminClient(tailnet=tailnet, api_token=token) as client:
        live_acl = client.get_acl()
        merged = merge_policy_overlay(live_acl.policy, overlay)
        client.validate_acl(merged)

        if args.write_merged_json:
            output_path = Path(args.write_merged_json).expanduser().resolve()
            output_path.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(f"WROTE_MERGED_POLICY={output_path}")

        if args.dry_run:
            print(
                "TAILSCALE_POLICY_VALIDATE_OK "
                f"tailnet={tailnet} policy_file={policy_path} etag={live_acl.etag or '<none>'}"
            )
            return 0

        client.apply_acl(merged, etag=live_acl.etag)
        print(
            "TAILSCALE_POLICY_APPLY_OK "
            f"tailnet={tailnet} policy_file={policy_path} etag={live_acl.etag or '<none>'}"
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except TailscaleAdminError as exc:
        raise SystemExit(str(exc))
