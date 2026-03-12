#!/usr/bin/env python3
from __future__ import annotations

import argparse
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
    find_device_by_hostname,
    load_device_roles,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply canonical Tailscale device tags.")
    parser.add_argument("--roles-file", required=True, help="Path to infrastructure/tailscale/device_roles.json")
    parser.add_argument("--tailnet", default="", help="Tailnet name. Defaults to TAILSCALE_TAILNET.")
    parser.add_argument("--token-env", default="TAILSCALE_ADMIN_API_TOKEN", help="Env var holding the Tailscale admin API token.")
    parser.add_argument("--host", default="", help="Optional single hostname to update.")
    parser.add_argument("--dry-run", action="store_true", help="Show changes only; do not update tags.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    tailnet = str(args.tailnet or os.getenv("TAILSCALE_TAILNET") or "").strip()
    token = str(os.getenv(args.token_env) or "").strip()
    if not tailnet:
        raise SystemExit("Missing --tailnet and TAILSCALE_TAILNET is unset.")
    if not token:
        raise SystemExit(f"Missing Tailscale admin token in env var {args.token_env}.")

    roles_path = Path(args.roles_file).expanduser().resolve()
    roles = load_device_roles(roles_path)
    if args.host:
        wanted = str(args.host).strip()
        roles = {host: tags for host, tags in roles.items() if host == wanted}
        if not roles:
            raise SystemExit(f"Host {wanted!r} not found in {roles_path}")

    with TailscaleAdminClient(tailnet=tailnet, api_token=token) as client:
        devices = client.list_devices()
        for host, desired_tags in roles.items():
            device = find_device_by_hostname(devices, host)
            if device is None:
                raise SystemExit(f"Device {host!r} not found in tailnet {tailnet}.")
            current_tags = sorted({tag for tag in device.tags if tag})
            desired_tags = sorted({tag for tag in desired_tags if tag})
            if current_tags == desired_tags:
                print(f"TAILSCALE_DEVICE_TAGS_OK host={host} tags={','.join(desired_tags) or '<none>'}")
                continue
            print(
                f"TAILSCALE_DEVICE_TAGS_UPDATE host={host} "
                f"current={','.join(current_tags) or '<none>'} "
                f"desired={','.join(desired_tags) or '<none>'}"
            )
            if not args.dry_run:
                client.set_device_tags(device.device_id, desired_tags)
                print(f"TAILSCALE_DEVICE_TAGS_APPLIED host={host}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except TailscaleAdminError as exc:
        raise SystemExit(str(exc))
