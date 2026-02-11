#!/usr/bin/env python3
"""
Bootstrap hooks config for:
1) Composio trigger ingress (/api/v1/hooks/composio)
2) Manual YouTube URL ingress (/api/v1/hooks/youtube/manual)

This script updates ops_config.json in-place while preserving unrelated config.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from universal_agent.ops_config import load_ops_config, resolve_ops_config_path, write_ops_config


def _upsert_mapping(mappings: list[dict[str, Any]], mapping: dict[str, Any]) -> list[dict[str, Any]]:
    mapping_id = mapping.get("id")
    if not mapping_id:
        return mappings + [mapping]
    replaced = False
    updated: list[dict[str, Any]] = []
    for existing in mappings:
        if existing.get("id") == mapping_id:
            updated.append(mapping)
            replaced = True
        else:
            updated.append(existing)
    if not replaced:
        updated.append(mapping)
    return updated


def build_mappings() -> list[dict[str, Any]]:
    composio_mapping = {
        "id": "composio-youtube-trigger",
        "match": {
            "path": "composio",
        },
        "action": "agent",
        "auth": {
            "strategy": "composio_hmac",
            "secret_env": "COMPOSIO_WEBHOOK_SECRET",
            "timestamp_tolerance_seconds": 300,
            "replay_window_seconds": 600,
        },
        "transform": {
            "module": "composio_youtube_transform.py",
            "export": "transform",
        },
    }

    manual_mapping = {
        "id": "youtube-manual-url",
        "match": {
            "path": "youtube/manual",
        },
        "action": "agent",
        "auth": {
            "strategy": "token",
        },
        "transform": {
            "module": "manual_youtube_transform.py",
            "export": "transform",
        },
    }

    return [composio_mapping, manual_mapping]


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap Composio + manual YouTube hook mappings.")
    parser.add_argument("--write", action="store_true", help="Write config to ops_config.json.")
    parser.add_argument(
        "--enable-hooks",
        action="store_true",
        help="Set hooks.enabled=true in config.",
    )
    parser.add_argument(
        "--set-token-from-env",
        action="store_true",
        help="Load UA_HOOKS_TOKEN from environment for optional persistence.",
    )
    parser.add_argument(
        "--token",
        default="",
        help="Explicit hooks token value (used only with --persist-token).",
    )
    parser.add_argument(
        "--persist-token",
        action="store_true",
        help="Persist hooks.token into ops_config.json (not recommended; prefer UA_HOOKS_TOKEN env).",
    )
    parser.add_argument(
        "--transforms-dir",
        default="../webhook_transforms",
        help="Path stored in hooks.transforms_dir relative to ops_config.json directory.",
    )
    args = parser.parse_args()

    config = load_ops_config()
    hooks = config.get("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}

    hooks.setdefault("max_body_bytes", 1024 * 1024)
    hooks["transforms_dir"] = args.transforms_dir

    mappings = hooks.get("mappings", [])
    if not isinstance(mappings, list):
        mappings = []

    for mapping in build_mappings():
        mappings = _upsert_mapping(mappings, mapping)
    hooks["mappings"] = mappings

    if args.enable_hooks:
        hooks["enabled"] = True

    token_to_persist = ""
    if args.token:
        token_to_persist = args.token
    elif args.set_token_from_env:
        import os

        token_to_persist = os.getenv("UA_HOOKS_TOKEN", "")

    if args.persist_token and token_to_persist:
        hooks["token"] = token_to_persist
    else:
        # Security default: avoid storing manual auth token in plaintext config.
        hooks.pop("token", None)

    config["hooks"] = hooks

    if not args.write:
        print("# Dry run (no write). Use --write to persist.")
        print(json.dumps(config, indent=2))
        return 0

    path = write_ops_config(config)
    print(f"Updated hooks config: {path}")
    print("Mappings ensured:")
    print("- composio-youtube-trigger -> /api/v1/hooks/composio")
    print("- youtube-manual-url -> /api/v1/hooks/youtube/manual")
    if args.persist_token:
        print("Token persistence: enabled (hooks.token written)")
    else:
        print("Token persistence: disabled (use UA_HOOKS_TOKEN env at runtime)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
