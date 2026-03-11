#!/usr/bin/env python3
"""Phase-2 Threads publish smoke helper (defaults to dry-run)."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
from typing import Any

import httpx

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from csi_ingester.adapters.threads_publishing import (
    ThreadsPublishingDisabledError,
    ThreadsPublishingGovernanceError,
    ThreadsPublishingInterface,
)
from csi_ingester.config import load_config


def _scope_set(raw_scopes: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(raw_scopes, list):
        for item in raw_scopes:
            value = str(item or "").strip()
            if value:
                out.add(value)
    return out


def _is_permission_denial_error(body_text: str) -> bool:
    body = str(body_text or "")
    lowered = body.lower()
    if "permission" in lowered:
        return True
    try:
        payload = json.loads(body)
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False
    error_obj = payload.get("error")
    if not isinstance(error_obj, dict):
        return False
    try:
        return int(error_obj.get("code")) == 10
    except Exception:
        return False


def _extract_threads_error_code(body_text: str) -> int | None:
    body = str(body_text or "")
    try:
        payload = json.loads(body)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    error_obj = payload.get("error")
    if not isinstance(error_obj, dict):
        return None
    try:
        return int(error_obj.get("code"))
    except Exception:
        return None


async def _run_publish_preflight(
    *,
    required_scopes: list[str],
    timeout_seconds: int,
    allow_unverified_scopes: bool,
) -> dict[str, Any]:
    app_id = str(os.getenv("THREADS_APP_ID") or "").strip()
    app_secret = str(os.getenv("THREADS_APP_SECRET") or "").strip()
    user_id = str(os.getenv("THREADS_USER_ID") or "").strip()
    access_token = str(os.getenv("THREADS_ACCESS_TOKEN") or "").strip()
    token_expires_at = str(os.getenv("THREADS_TOKEN_EXPIRES_AT") or "").strip()

    missing_env = [k for k, v in {
        "THREADS_APP_ID": app_id,
        "THREADS_APP_SECRET": app_secret,
        "THREADS_USER_ID": user_id,
        "THREADS_ACCESS_TOKEN": access_token,
    }.items() if not v]
    if missing_env:
        return {
            "ok": False,
            "reason": "missing_env",
            "missing_env": missing_env,
            "required_scopes": required_scopes,
        }

    result: dict[str, Any] = {
        "ok": False,
        "required_scopes": required_scopes,
        "token_expires_at": token_expires_at,
        "me_ok": False,
        "debug_token_ok": False,
        "token_valid": None,
        "granted_scopes": [],
        "missing_scopes": [],
        "user_match": None,
    }

    timeout = max(5, int(timeout_seconds))
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        me_resp = await client.get(
            "https://graph.threads.net/v1.0/me",
            params={"fields": "id,username", "access_token": access_token},
        )
        me_payload = me_resp.json() if me_resp.content else {}
        me_id = str(me_payload.get("id") or "").strip() if isinstance(me_payload, dict) else ""
        result["me_ok"] = me_resp.status_code < 400 and bool(me_id)
        result["me_status"] = int(me_resp.status_code)
        result["me_id"] = me_id
        result["me_username"] = str(me_payload.get("username") or "").strip() if isinstance(me_payload, dict) else ""
        result["user_match"] = bool(me_id and user_id and me_id == user_id)
        if not result["me_ok"]:
            result["reason"] = "threads_me_failed"
            result["detail"] = (me_resp.text or "")[:400]
            return result

        app_access_token = f"{app_id}|{app_secret}"
        debug_resp = await client.get(
            "https://graph.facebook.com/debug_token",
            params={"input_token": access_token, "access_token": app_access_token},
        )
        if debug_resp.status_code >= 400:
            result["debug_token_ok"] = False
            result["debug_token_status"] = int(debug_resp.status_code)
            result["debug_token_detail"] = (debug_resp.text or "")[:400]
            # Fallback: non-destructive write-capability probe.
            # We intentionally omit text on media_type=TEXT so no valid container is created.
            probe_resp = await client.post(
                f"https://graph.threads.net/v1.0/{user_id}/threads",
                data={"media_type": "TEXT", "access_token": access_token},
            )
            result["write_probe_status"] = int(probe_resp.status_code)
            result["write_probe_detail"] = (probe_resp.text or "")[:400]
            if probe_resp.status_code >= 400:
                body_text = str(probe_resp.text or "")
                error_code = _extract_threads_error_code(body_text)
                if error_code is not None:
                    result["write_probe_error_code"] = int(error_code)
                if _is_permission_denial_error(body_text):
                    result["reason"] = "missing_write_permission"
                    return result
                # Code 100 (missing required field) confirms endpoint-level write access.
                if error_code == 100:
                    result["ok"] = True
                    result["reason"] = "write_probe_verified"
                    result["scope_verification"] = "write_probe_fallback"
                    return result
            # If write probe does not show permission denial, optionally allow continuation.
            if allow_unverified_scopes:
                result["ok"] = True
                result["reason"] = "debug_token_failed_write_probe_non_blocking"
                result["scope_verification"] = "write_probe_fallback"
                return result
            result["reason"] = "debug_token_failed"
            return result

        debug_payload = debug_resp.json() if debug_resp.content else {}
        data = debug_payload.get("data") if isinstance(debug_payload, dict) else {}
        if not isinstance(data, dict):
            result["reason"] = "debug_token_invalid_payload"
            if allow_unverified_scopes:
                result["ok"] = True
                result["scope_verification"] = "skipped_on_invalid_payload"
                return result
            return result

        result["debug_token_ok"] = True
        result["token_valid"] = bool(data.get("is_valid"))
        granted = _scope_set(data.get("scopes"))
        granular = data.get("granular_scopes")
        if isinstance(granular, list):
            for item in granular:
                if not isinstance(item, dict):
                    continue
                scope = str(item.get("scope") or "").strip()
                if scope:
                    granted.add(scope)
        missing_scopes = sorted([scope for scope in required_scopes if scope not in granted])
        result["granted_scopes"] = sorted(granted)
        result["missing_scopes"] = missing_scopes

        if not result["token_valid"]:
            result["reason"] = "token_invalid"
            return result
        if missing_scopes:
            result["reason"] = "missing_required_scopes"
            return result
        if result["user_match"] is False:
            result["reason"] = "threads_user_id_mismatch"
            return result

        result["ok"] = True
        result["reason"] = "ok"
        return result


async def _run(args: argparse.Namespace) -> int:
    cfg = load_config(args.config_path)
    sources = cfg.raw.get("sources") if isinstance(cfg.raw, dict) else {}
    source_cfg = sources.get("threads_owned") if isinstance(sources.get("threads_owned"), dict) else {}

    iface = ThreadsPublishingInterface(
        source_config=source_cfg,
        enabled=None,
        dry_run=None,
        approval_mode=None,
        state_path=str(args.state_path),
    )

    if not bool(args.skip_preflight):
        required_scopes = [scope.strip() for scope in str(args.required_scopes or "").split(",") if scope.strip()]
        preflight = await _run_publish_preflight(
            required_scopes=required_scopes,
            timeout_seconds=max(5, int(args.preflight_timeout_seconds)),
            allow_unverified_scopes=bool(args.allow_unverified_scopes),
        )
        print("THREADS_PUBLISH_PREFLIGHT=" + json.dumps(preflight, sort_keys=True))
        if not bool(preflight.get("ok")):
            print(f"ERROR=threads_publish_preflight_failed:{str(preflight.get('reason') or 'unknown')}")
            return 5

    result: dict[str, Any]
    payload: dict[str, Any] = {}
    if args.approval_id.strip():
        payload["approval_id"] = args.approval_id.strip()
    if args.reply_control.strip():
        payload["reply_control"] = args.reply_control.strip()
    if args.audit_actor.strip():
        payload["audit_actor"] = args.audit_actor.strip()
    if args.audit_reason.strip():
        payload["audit_reason"] = args.audit_reason.strip()

    if args.operation == "create":
        payload["media_type"] = args.media_type.strip().upper() or "TEXT"
        if args.text.strip():
            payload["text"] = args.text.strip()
        if args.image_url.strip():
            payload["image_url"] = args.image_url.strip()
        if args.video_url.strip():
            payload["video_url"] = args.video_url.strip()
        if args.reply_to_id.strip():
            payload["reply_to_id"] = args.reply_to_id.strip()
        result = await iface.create_container(payload)
    elif args.operation == "publish":
        result = await iface.publish_container(args.creation_id)
    else:
        payload["text"] = args.text.strip()
        result = await iface.reply_to_post(args.reply_to_id, payload)

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-path", default="config/config.yaml")
    parser.add_argument(
        "--state-path",
        default="/var/lib/universal-agent/csi/threads_publishing_state.json",
        help="Governance state file path (daily caps)",
    )
    parser.add_argument("--operation", choices=["create", "publish", "reply"], default="create")
    parser.add_argument("--approval-id", default="", help="Required in manual_confirm mode")
    parser.add_argument("--media-type", default="TEXT", help="create only: TEXT|IMAGE|VIDEO|CAROUSEL")
    parser.add_argument("--text", default="", help="create/reply text")
    parser.add_argument("--image-url", default="", help="create image URL")
    parser.add_argument("--video-url", default="", help="create video URL")
    parser.add_argument("--reply-to-id", default="", help="create/reply target media id")
    parser.add_argument("--reply-control", default="", help="optional Threads reply_control")
    parser.add_argument("--audit-actor", default="", help="optional audit actor label")
    parser.add_argument("--audit-reason", default="", help="optional audit reason/context")
    parser.add_argument("--creation-id", default="", help="publish only")
    parser.add_argument("--skip-preflight", action="store_true", help="Skip token/scope preflight gate")
    parser.add_argument(
        "--required-scopes",
        default="threads_basic,threads_content_publish",
        help="Comma-separated scopes required for live publish canary",
    )
    parser.add_argument(
        "--preflight-timeout-seconds",
        type=int,
        default=20,
        help="HTTP timeout for preflight checks",
    )
    parser.add_argument(
        "--allow-unverified-scopes",
        action="store_true",
        help="Allow run when debug_token scope verification cannot be completed",
    )
    args = parser.parse_args()

    if args.operation == "publish" and not str(args.creation_id).strip():
        print("ERROR=creation_id_required_for_publish")
        return 2
    if args.operation == "reply":
        if not str(args.reply_to_id).strip():
            print("ERROR=reply_to_id_required_for_reply")
            return 2
        if not str(args.text).strip():
            print("ERROR=text_required_for_reply")
            return 2

    try:
        return asyncio.run(_run(args))
    except ThreadsPublishingDisabledError as exc:
        print(f"ERROR={exc}")
        return 3
    except ThreadsPublishingGovernanceError as exc:
        print(f"ERROR={exc}")
        return 4
    except Exception as exc:  # pragma: no cover
        print(f"ERROR=threads_publish_smoke_failed:{type(exc).__name__}:{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
