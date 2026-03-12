#!/usr/bin/env python3
"""
Local/VPS readiness checker for YouTube trigger ingress.

Checks:
1) Required env vars for Composio + manual ingress
2) Hook mappings present in ops_config.json
3) Basic security posture (no plaintext hooks.token)
4) Optional live ingest probe against /api/v1/youtube/ingest
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

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


def _load_local_env() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv()
        return
    except Exception:
        pass

    env_path = os.path.join(os.getcwd(), ".env")
    try:
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#") or "=" not in stripped:
                        continue
                    key, value = stripped.split("=", 1)
                    key = key.strip()
                    if not key:
                        continue
                    os.environ.setdefault(key, value.strip())
    except Exception:
        pass


def _first_ingest_url() -> str:
    urls_raw = str(os.getenv("UA_HOOKS_YOUTUBE_INGEST_URLS") or "").strip()
    if urls_raw:
        for item in urls_raw.split(","):
            value = item.strip()
            if value.startswith(("http://", "https://")):
                return value
    return str(os.getenv("UA_HOOKS_YOUTUBE_INGEST_URL") or "").strip()


def _probe_ingest(*, ingest_url: str, video_id: str, timeout_seconds: int) -> tuple[dict[str, Any], int]:
    body = json.dumps(
        {
            "video_id": video_id,
            "timeout_seconds": max(5, min(int(timeout_seconds or 120), 600)),
            "max_chars": 12000,
            "min_chars": 20,
            "request_id": "readiness_probe",
        }
    ).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    token = (
        str(os.getenv("UA_YOUTUBE_INGEST_TOKEN") or "").strip()
        or str(os.getenv("UA_HOOKS_YOUTUBE_INGEST_TOKEN") or "").strip()
    )
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(ingest_url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=max(5, int(timeout_seconds or 120))) as response:
            payload = response.read().decode("utf-8", errors="replace")
            data = json.loads(payload) if payload else {}
            if not isinstance(data, dict):
                data = {"raw": data}
            return (
                {
                    "ingest_url": ingest_url,
                    "http_status": int(response.status),
                    "ok": bool(data.get("ok")),
                    "status": str(data.get("status") or ""),
                    "error": str(data.get("error") or ""),
                    "failure_class": str(data.get("failure_class") or ""),
                    "proxy_mode": str(data.get("proxy_mode") or ""),
                },
                0 if response.status == 200 and bool(data.get("ok")) else 1,
            )
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        detail = payload
        try:
            parsed = json.loads(payload) if payload else {}
            if isinstance(parsed, dict):
                detail = str(parsed.get("detail") or parsed)
        except Exception:
            pass
        return (
            {
                "ingest_url": ingest_url,
                "http_status": int(exc.code),
                "ok": False,
                "status": "http_error",
                "error": detail,
                "failure_class": "",
                "proxy_mode": "",
            },
            1,
        )
    except Exception as exc:
        return (
            {
                "ingest_url": ingest_url,
                "http_status": 0,
                "ok": False,
                "status": "request_failed",
                "error": str(exc),
                "failure_class": "",
                "proxy_mode": "",
            },
            1,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Check YouTube ingress readiness")
    parser.add_argument("--probe-video-id", default="", help="Optional public YouTube video ID for a live ingest probe")
    parser.add_argument("--ingest-url", default="", help="Override ingest endpoint for probe mode")
    parser.add_argument("--timeout-seconds", type=int, default=120, help="Probe timeout in seconds")
    parser.add_argument("--json", action="store_true", help="Reserved for compatibility; output is always JSON")
    args = parser.parse_args()

    _load_local_env()

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
        "UA_HOOKS_YOUTUBE_INGEST_MODE",
        "UA_HOOKS_YOUTUBE_INGEST_URL",
        "UA_HOOKS_YOUTUBE_INGEST_TOKEN",
        "UA_YOUTUBE_INGEST_TOKEN",
    ]

    present_required = [k for k in required_env if os.getenv(k)]
    missing_required = [k for k in required_env if not os.getenv(k)]
    present_recommended = [k for k in recommended_env if os.getenv(k)]
    missing_recommended = [k for k in recommended_env if not os.getenv(k)]

    composio_mapping = mapping_by_id.get("composio-youtube-trigger", {})
    manual_mapping = mapping_by_id.get("youtube-manual-url", {})

    hooks_enabled = bool(hooks.get("enabled"))
    plaintext_token_in_config = bool(hooks.get("token"))

    report: dict[str, Any] = {
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
        "local_worker_ingest": {
            "mode": str(os.getenv("UA_HOOKS_YOUTUBE_INGEST_MODE") or "").strip().lower() or "disabled",
            "url": bool(os.getenv("UA_HOOKS_YOUTUBE_INGEST_URL")),
            "urls": [item.strip() for item in str(os.getenv("UA_HOOKS_YOUTUBE_INGEST_URLS") or "").split(",") if item.strip()],
            "vps_token_present": bool(os.getenv("UA_HOOKS_YOUTUBE_INGEST_TOKEN")),
            "local_token_present": bool(os.getenv("UA_YOUTUBE_INGEST_TOKEN")),
        },
    }

    exit_code = 0
    if str(args.probe_video_id or "").strip():
        ingest_url = str(args.ingest_url or "").strip() or _first_ingest_url()
        if not ingest_url:
            report["probe"] = {
                "ok": False,
                "status": "missing_ingest_url",
                "error": "No ingest URL configured or provided",
            }
            exit_code = 1
        else:
            probe, probe_exit = _probe_ingest(
                ingest_url=ingest_url,
                video_id=str(args.probe_video_id).strip(),
                timeout_seconds=int(args.timeout_seconds or 120),
            )
            report["probe"] = probe
            exit_code = probe_exit

    print(json.dumps(report, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
