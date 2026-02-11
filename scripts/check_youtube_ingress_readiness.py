#!/usr/bin/env python3
"""
Local readiness checker for YouTube trigger ingress.

Checks:
1) Required env vars for Composio + manual ingress
2) Hook mappings present in ops_config.json
3) Basic security posture (no plaintext hooks.token)
"""

from __future__ import annotations

import json
import os
from typing import Any

from dotenv import load_dotenv

from universal_agent.ops_config import load_ops_config, resolve_ops_config_path


def _mapping_index(mappings: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in mappings:
        if not isinstance(item, dict):
            continue
        key = str(item.get("id") or "").strip()
        if key:
            out[key] = item
    return out


def main() -> int:
    load_dotenv()

    cfg = load_ops_config()
    hooks = cfg.get("hooks", {}) if isinstance(cfg, dict) else {}
    mappings = hooks.get("mappings", []) if isinstance(hooks, dict) else []
    mapping_by_id = _mapping_index(mappings if isinstance(mappings, list) else [])

    required_env = [
        "COMPOSIO_API_KEY",
        "UA_HOOKS_TOKEN",
    ]
    recommended_env = [
        "COMPOSIO_WEBHOOK_SECRET",
        "COMPOSIO_WEBHOOK_URL",
        "COMPOSIO_WEBHOOK_SUBSCRIPTION_ID",
        "UA_GATEWAY_PUBLIC_URL",
    ]

    present_required = [k for k in required_env if os.getenv(k)]
    missing_required = [k for k in required_env if not os.getenv(k)]
    present_recommended = [k for k in recommended_env if os.getenv(k)]
    missing_recommended = [k for k in recommended_env if not os.getenv(k)]

    composio_mapping = mapping_by_id.get("composio-youtube-trigger", {})
    manual_mapping = mapping_by_id.get("youtube-manual-url", {})

    hooks_enabled = bool(hooks.get("enabled"))
    plaintext_token_in_config = bool(hooks.get("token"))

    report = {
        "ops_config_path": str(resolve_ops_config_path()),
        "hooks": {
            "enabled": hooks_enabled,
            "plaintext_token_in_config": plaintext_token_in_config,
            "mappings_present": {
                "composio-youtube-trigger": bool(composio_mapping),
                "youtube-manual-url": bool(manual_mapping),
            },
        },
        "env": {
            "required_present": present_required,
            "required_missing": missing_required,
            "recommended_present": present_recommended,
            "recommended_missing": missing_recommended,
        },
        "ready": bool(
            hooks_enabled
            and not missing_required
            and bool(composio_mapping)
            and bool(manual_mapping)
        ),
    }

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
