#!/usr/bin/env python3
"""Smoke test Threads webhook endpoints (verification + signed ingest)."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import random
import string
from typing import Any

import httpx


def _now_epoch() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _rand_id(prefix: str = "smoke") -> str:
    suffix = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(10))
    return f"{prefix}-{suffix}"


def _build_payload(*, user_id: str, media_id: str, field: str, text: str) -> dict[str, Any]:
    return {
        "object": "threads",
        "entry": [
            {
                "id": user_id,
                "time": _now_epoch(),
                "changes": [
                    {
                        "field": field,
                        "value": {
                            "id": media_id,
                            "text": text,
                            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        },
                    }
                ],
            }
        ],
    }


def _sign(raw_body: bytes, app_secret: str) -> str:
    digest = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8091")
    parser.add_argument("--verify", action="store_true", help="Run GET verification challenge check")
    parser.add_argument("--ingest", action="store_true", help="Run signed POST ingest check")
    parser.add_argument("--verify-token", default="", help="Override THREADS_WEBHOOK_VERIFY_TOKEN")
    parser.add_argument("--app-secret", default="", help="Override THREADS_APP_SECRET")
    parser.add_argument("--user-id", default="", help="Override THREADS_USER_ID for payload entry id")
    parser.add_argument("--field", default="mentions", help="Webhook field name for sample change")
    parser.add_argument("--media-id", default="", help="Override sample media id")
    parser.add_argument("--text", default="threads webhook smoke test")
    parser.add_argument("--timeout-seconds", type=int, default=20)
    args = parser.parse_args()

    run_verify = bool(args.verify) or (not args.verify and not args.ingest)
    run_ingest = bool(args.ingest) or (not args.verify and not args.ingest)

    verify_token = str(args.verify_token or os.getenv("THREADS_WEBHOOK_VERIFY_TOKEN") or "").strip()
    app_secret = str(args.app_secret or os.getenv("THREADS_APP_SECRET") or "").strip()
    user_id = str(args.user_id or os.getenv("THREADS_USER_ID") or "threads-smoke-user").strip()
    media_id = str(args.media_id or f"1800{_now_epoch()}").strip()
    base_url = str(args.base_url or "").rstrip("/")

    timeout = max(5, int(args.timeout_seconds))
    with httpx.Client(timeout=timeout) as client:
        if run_verify:
            if not verify_token:
                print("ERROR=missing_verify_token")
                return 2
            challenge = _rand_id("challenge")
            resp = client.get(
                f"{base_url}/webhooks/threads",
                params={
                    "hub.mode": "subscribe",
                    "hub.verify_token": verify_token,
                    "hub.challenge": challenge,
                },
            )
            print(f"VERIFY_STATUS={resp.status_code}")
            print(f"VERIFY_BODY={resp.text[:400]}")
            if resp.status_code != 200 or str(resp.text or "").strip() != challenge:
                print("ERROR=verify_failed")
                return 1

        if run_ingest:
            if not app_secret:
                print("ERROR=missing_app_secret")
                return 2
            payload = _build_payload(user_id=user_id, media_id=media_id, field=args.field, text=args.text)
            raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
            sig = _sign(raw, app_secret)
            resp = client.post(
                f"{base_url}/webhooks/threads",
                headers={"x-hub-signature-256": sig, "content-type": "application/json"},
                content=raw,
            )
            print(f"INGEST_STATUS={resp.status_code}")
            print(f"INGEST_BODY={resp.text[:800]}")
            if resp.status_code >= 400:
                print("ERROR=ingest_failed")
                return 1

    print("THREADS_WEBHOOK_SMOKE_OK=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
