#!/usr/bin/env python3
"""
Register or update the project-level Composio webhook subscription and persist secrets.

Usage:
  uv run python scripts/register_composio_webhook_subscription.py \
    --webhook-url "https://your-host/api/v1/hooks/composio"
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

BASE_URL = "https://backend.composio.dev/api/v3/webhook_subscriptions"


def _request(
    api_key: str,
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    headers = {"x-api-key": api_key, "content-type": "application/json"}
    response = requests.request(method, url, headers=headers, json=payload, timeout=timeout)
    if response.status_code >= 300:
        raise RuntimeError(f"{method} {url} failed ({response.status_code}): {response.text[:600]}")
    if not response.text.strip():
        return {}
    return response.json()


def _upsert_env_values(env_path: Path, values: dict[str, str]) -> None:
    lines = env_path.read_text(encoding="utf-8", errors="replace").splitlines() if env_path.exists() else []
    remaining = set(values.keys())
    out: list[str] = []
    for line in lines:
        replaced = False
        for key in list(remaining):
            if line.startswith(key + "="):
                out.append(f"{key}={values[key]}")
                remaining.remove(key)
                replaced = True
                break
        if not replaced:
            out.append(line)
    for key in sorted(remaining):
        out.append(f"{key}={values[key]}")
    env_path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Register/update Composio webhook subscription.")
    parser.add_argument("--webhook-url", required=True, help="Public URL ending in /api/v1/hooks/composio")
    parser.add_argument(
        "--events",
        default="composio.trigger.message",
        help="Comma-separated event types (default: composio.trigger.message)",
    )
    parser.add_argument(
        "--replace-existing",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Replace existing project subscription if one exists (default: true).",
    )
    parser.add_argument(
        "--rotate-secret",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Rotate secret after replace to obtain a fresh full secret (default: true).",
    )
    parser.add_argument(
        "--write-env",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Persist COMPOSIO_WEBHOOK_SECRET and metadata to env file (default: true).",
    )
    parser.add_argument("--env-file", default=".env", help="Environment file path (default: .env)")
    args = parser.parse_args()

    load_dotenv(args.env_file)
    api_key = os.getenv("COMPOSIO_API_KEY")
    if not api_key:
        raise SystemExit("Missing COMPOSIO_API_KEY in environment/.env")

    events = [e.strip() for e in args.events.split(",") if e.strip()]
    if not events:
        raise SystemExit("At least one event type is required.")

    existing = _request(api_key, "GET", BASE_URL).get("items", [])
    if not isinstance(existing, list):
        existing = []

    sub_id = ""
    secret = ""
    action = ""

    if existing:
        if not args.replace_existing:
            raise SystemExit(
                f"Existing subscription found ({existing[0].get('id')}). "
                "Use --replace-existing to update it."
            )
        sub_id = str(existing[0].get("id") or "")
        if not sub_id:
            raise SystemExit("Existing subscription missing id.")
        action = "updated"
        _request(
            api_key,
            "PATCH",
            f"{BASE_URL}/{sub_id}",
            {"webhook_url": args.webhook_url, "enabled_events": events},
        )
        if args.rotate_secret:
            rotated = _request(api_key, "POST", f"{BASE_URL}/{sub_id}/rotate_secret", {})
            secret = str(rotated.get("secret") or "")
    else:
        action = "created"
        created = _request(
            api_key,
            "POST",
            BASE_URL,
            {"webhook_url": args.webhook_url, "enabled_events": events},
        )
        sub_id = str(created.get("id") or "")
        secret = str(created.get("secret") or "")

    if not sub_id:
        raise SystemExit("Failed to resolve subscription id from API response.")

    env_updates: dict[str, str] = {
        "COMPOSIO_WEBHOOK_URL": args.webhook_url,
        "COMPOSIO_WEBHOOK_SUBSCRIPTION_ID": sub_id,
    }
    if secret:
        env_updates["COMPOSIO_WEBHOOK_SECRET"] = secret

    if args.write_env:
        _upsert_env_values(Path(args.env_file), env_updates)

    print(json.dumps(
        {
            "status": action,
            "subscription_id": sub_id,
            "webhook_url": args.webhook_url,
            "enabled_events": events,
            "secret_saved": bool(secret) and args.write_env,
            "env_file": args.env_file if args.write_env else None,
        },
        indent=2,
    ))
    if not secret:
        print("WARNING: No full secret returned by API. Use --rotate-secret to force new secret.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
