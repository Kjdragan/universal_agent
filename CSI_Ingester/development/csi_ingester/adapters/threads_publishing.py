"""Threads publishing interface with phase-2 governance gates."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from csi_ingester.adapters.threads_api import ThreadsAPIClient, ThreadsAPIError


class ThreadsPublishingDisabledError(RuntimeError):
    """Raised when publishing operations are called before phase-2 enablement."""


class ThreadsPublishingGovernanceError(RuntimeError):
    """Raised when a publish action is blocked by governance policy."""


@dataclass(slots=True)
class ThreadsPublishingGovernance:
    enabled: bool
    dry_run: bool
    approval_mode: str
    max_daily_posts: int
    max_daily_replies: int
    state_path: Path
    audit_path: Path


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _today_key(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m-%d")


def _bool_env(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "")).strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on"}


class ThreadsPublishingInterface:
    """Internal publishing contract for controlled Threads write-path automation."""

    def __init__(
        self,
        *,
        source_config: dict[str, Any] | None = None,
        enabled: bool | None = None,
        dry_run: bool | None = None,
        approval_mode: str | None = None,
        max_daily_posts: int | None = None,
        max_daily_replies: int | None = None,
        state_path: str | None = None,
        client: ThreadsAPIClient | None = None,
    ) -> None:
        config = source_config if isinstance(source_config, dict) else {}
        resolved_enabled = bool(enabled) if enabled is not None else _bool_env("CSI_THREADS_PUBLISHING_ENABLED", False)
        resolved_dry_run = bool(dry_run) if dry_run is not None else _bool_env("CSI_THREADS_PUBLISH_DRY_RUN", True)
        resolved_approval_mode = str(
            approval_mode
            if approval_mode is not None
            else (os.getenv("CSI_THREADS_PUBLISH_APPROVAL_MODE") or "manual_confirm")
        ).strip().lower() or "manual_confirm"
        resolved_max_daily_posts = (
            int(max_daily_posts)
            if max_daily_posts is not None
            else int(str(os.getenv("CSI_THREADS_PUBLISH_MAX_DAILY_POSTS") or "5").strip() or "5")
        )
        resolved_max_daily_replies = (
            int(max_daily_replies)
            if max_daily_replies is not None
            else int(str(os.getenv("CSI_THREADS_PUBLISH_MAX_DAILY_REPLIES") or "10").strip() or "10")
        )
        resolved_state_path = Path(
            str(
                state_path
                if state_path is not None
                else (
                    os.getenv("CSI_THREADS_PUBLISH_STATE_PATH")
                    or "/var/lib/universal-agent/csi/threads_publishing_state.json"
                )
            )
        ).expanduser()
        resolved_audit_path = Path(
            str(
                os.getenv("CSI_THREADS_PUBLISH_AUDIT_PATH")
                or "/var/lib/universal-agent/csi/threads_publishing_audit.jsonl"
            )
        ).expanduser()

        self.governance = ThreadsPublishingGovernance(
            enabled=resolved_enabled,
            dry_run=resolved_dry_run,
            approval_mode=resolved_approval_mode,
            max_daily_posts=max(0, resolved_max_daily_posts),
            max_daily_replies=max(0, resolved_max_daily_replies),
            state_path=resolved_state_path,
            audit_path=resolved_audit_path,
        )
        self.client = client if client is not None else ThreadsAPIClient.from_config(config, quota_state={})

    def _ensure_enabled(self) -> None:
        if not self.governance.enabled:
            raise ThreadsPublishingDisabledError("threads_publishing_disabled_phase1")

    def _load_state(self) -> dict[str, Any]:
        path = self.governance.state_path
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _save_state(self, payload: dict[str, Any]) -> None:
        path = self.governance.state_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def _enforce_approval(self, *, payload: dict[str, Any]) -> None:
        mode = self.governance.approval_mode
        if mode != "manual_confirm":
            return
        approval_ref = str(payload.get("approval_id") or payload.get("approval_ref") or "").strip()
        if not approval_ref:
            raise ThreadsPublishingGovernanceError("threads_publish_approval_required")

    def _enforce_daily_limit(self, *, action: str) -> dict[str, Any]:
        prepared = self._prepare_daily_limit(action=action)
        return self._commit_daily_limit(action=action, prepared=prepared)

    def _prepare_daily_limit(self, *, action: str) -> dict[str, Any]:
        day_key = _today_key(_utc_now())
        state = self._load_state()
        days = state.get("days")
        if not isinstance(days, dict):
            days = {}
            state["days"] = days
        day_state = days.get(day_key)
        if not isinstance(day_state, dict):
            day_state = {"posts": 0, "replies": 0}
            days[day_key] = day_state

        posts = int(day_state.get("posts") or 0)
        replies = int(day_state.get("replies") or 0)
        if action == "post":
            if self.governance.max_daily_posts > 0 and posts >= self.governance.max_daily_posts:
                raise ThreadsPublishingGovernanceError("threads_publish_daily_post_limit_reached")
            projected_posts = posts + 1
            projected_replies = replies
        elif action == "reply":
            if self.governance.max_daily_replies > 0 and replies >= self.governance.max_daily_replies:
                raise ThreadsPublishingGovernanceError("threads_publish_daily_reply_limit_reached")
            projected_posts = posts
            projected_replies = replies + 1
        else:
            projected_posts = posts
            projected_replies = replies

        return {
            "day": day_key,
            "state": state,
            "day_state": day_state,
            "posts": posts,
            "replies": replies,
            "projected": {"day": day_key, "posts": projected_posts, "replies": projected_replies},
        }

    def _commit_daily_limit(self, *, action: str, prepared: dict[str, Any]) -> dict[str, Any]:
        day_state = prepared.get("day_state")
        state = prepared.get("state")
        if not isinstance(day_state, dict) or not isinstance(state, dict):
            raise ThreadsPublishingGovernanceError("threads_publish_state_invalid")
        posts = int(prepared.get("posts") or 0)
        replies = int(prepared.get("replies") or 0)
        if action == "post":
            day_state["posts"] = posts + 1
            day_state["replies"] = replies
        elif action == "reply":
            day_state["posts"] = posts
            day_state["replies"] = replies + 1
        day_state["updated_at"] = _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
        self._save_state(state)
        return {
            "day": str(prepared.get("day") or _today_key(_utc_now())),
            "posts": int(day_state.get("posts") or 0),
            "replies": int(day_state.get("replies") or 0),
        }

    def _dry_run_payload(self, *, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "dry_run",
            "operation": operation,
            "governance": {
                "enabled": self.governance.enabled,
                "dry_run": self.governance.dry_run,
                "approval_mode": self.governance.approval_mode,
                "max_daily_posts": self.governance.max_daily_posts,
                "max_daily_replies": self.governance.max_daily_replies,
            },
            "payload": payload,
        }

    def _append_audit(
        self,
        *,
        operation: str,
        status: str,
        payload: dict[str, Any] | None = None,
        response: dict[str, Any] | None = None,
        detail: str = "",
    ) -> None:
        body = payload if isinstance(payload, dict) else {}
        resp = response if isinstance(response, dict) else {}
        serialized_payload = json.dumps(body, sort_keys=True, ensure_ascii=True)
        payload_hash = hashlib.sha256(serialized_payload.encode("utf-8")).hexdigest()
        record = {
            "occurred_at_utc": _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "operation": str(operation or "").strip().lower(),
            "status": str(status or "").strip().lower() or "unknown",
            "payload_hash": payload_hash,
            "approval_ref": str(body.get("approval_id") or body.get("approval_ref") or "").strip(),
            "actor": str(body.get("audit_actor") or body.get("actor") or "").strip()[:120],
            "reason": str(body.get("audit_reason") or body.get("reason") or "").strip()[:240],
            "response_id": str(resp.get("id") or resp.get("creation_id") or "").strip(),
            "detail": str(detail or "").strip()[:400],
        }
        path = self.governance.audit_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True))
                handle.write("\n")
        except Exception:
            # Audit trail is best-effort; do not block governed publishing flow on local FS perms.
            return

    async def _wait_for_container_ready(self, creation_id: str) -> dict[str, Any]:
        """Poll container status until ready so publish does not race create propagation."""
        client_status = getattr(self.client, "container_status", None)
        if not callable(client_status):
            return {}

        max_polls = max(1, int(str(os.getenv("CSI_THREADS_REPLY_CONTAINER_MAX_POLLS") or "6").strip() or "6"))
        poll_interval_seconds = max(
            1.0,
            float(str(os.getenv("CSI_THREADS_REPLY_CONTAINER_POLL_INTERVAL_SECONDS") or "2").strip() or "2"),
        )
        last_status: dict[str, Any] = {}
        for _ in range(max_polls):
            try:
                last_status = await client_status(container_id=creation_id)
            except Exception:
                await asyncio.sleep(poll_interval_seconds)
                continue
            status_value = str(last_status.get("status") or "").strip().upper()
            if status_value == "FINISHED":
                return last_status
            if status_value in {"ERROR", "EXPIRED", "FAILED"}:
                error_message = str(last_status.get("error_message") or "").strip()
                raise ThreadsPublishingGovernanceError(
                    f"threads_publish_reply_container_not_ready:{status_value}:{error_message}"
                )
            await asyncio.sleep(poll_interval_seconds)
        return last_status

    async def create_container(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._ensure_enabled()
        body = payload if isinstance(payload, dict) else {}
        self._enforce_approval(payload=body)
        media_type = str(body.get("media_type") or "TEXT").strip().upper()
        if media_type == "TEXT" and str(body.get("reply_to_id") or "").strip():
            action = "reply"
        else:
            action = "post"

        quota_prepared = self._prepare_daily_limit(action=action)
        if self.governance.dry_run:
            out = self._dry_run_payload(operation="create_container", payload=body)
            out["daily_quota_after"] = quota_prepared.get("projected") if isinstance(quota_prepared, dict) else {}
            out["daily_quota_committed"] = False
            self._append_audit(operation="create_container", status="dry_run", payload=body, response=out)
            return out

        try:
            response = await self.client.create_media_container(
                media_type=media_type,
                text=str(body.get("text") or "").strip(),
                image_url=str(body.get("image_url") or "").strip(),
                video_url=str(body.get("video_url") or "").strip(),
                is_carousel_item=bool(body.get("is_carousel_item")),
                children=body.get("children") if isinstance(body.get("children"), list) else None,
                reply_to_id=str(body.get("reply_to_id") or "").strip(),
                reply_control=str(body.get("reply_control") or "").strip(),
                allowlisted_country_codes=(
                    body.get("allowlisted_country_codes")
                    if isinstance(body.get("allowlisted_country_codes"), list)
                    else None
                ),
                alt_text=str(body.get("alt_text") or "").strip(),
                link_attachment=str(body.get("link_attachment") or "").strip(),
                quote_post_id=str(body.get("quote_post_id") or "").strip(),
            )
        except ThreadsAPIError as exc:
            self._append_audit(
                operation="create_container",
                status="error",
                payload=body,
                detail=f"{type(exc).__name__}:{exc}",
            )
            raise ThreadsPublishingGovernanceError(f"threads_publish_create_failed:{exc}") from exc
        quota_state = self._commit_daily_limit(action=action, prepared=quota_prepared)
        self._append_audit(operation="create_container", status="ok", payload=body, response=response)
        return {
            "status": "ok",
            "operation": "create_container",
            "daily_quota_after": quota_state,
            "response": response,
        }

    async def publish_container(self, creation_id: str) -> dict[str, Any]:
        self._ensure_enabled()
        clean_creation_id = str(creation_id or "").strip()
        if not clean_creation_id:
            raise ThreadsPublishingGovernanceError("threads_publish_creation_id_required")

        if self.governance.dry_run:
            out = self._dry_run_payload(
                operation="publish_container",
                payload={"creation_id": clean_creation_id},
            )
            self._append_audit(
                operation="publish_container",
                status="dry_run",
                payload={"creation_id": clean_creation_id},
                response=out,
            )
            return out
        try:
            response = await self.client.publish_media_container(creation_id=clean_creation_id)
        except ThreadsAPIError as exc:
            self._append_audit(
                operation="publish_container",
                status="error",
                payload={"creation_id": clean_creation_id},
                detail=f"{type(exc).__name__}:{exc}",
            )
            raise ThreadsPublishingGovernanceError(f"threads_publish_publish_failed:{exc}") from exc
        self._append_audit(
            operation="publish_container",
            status="ok",
            payload={"creation_id": clean_creation_id},
            response=response,
        )
        return {
            "status": "ok",
            "operation": "publish_container",
            "creation_id": clean_creation_id,
            "response": response,
        }

    async def reply_to_post(self, media_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._ensure_enabled()
        clean_media_id = str(media_id or "").strip()
        if not clean_media_id:
            raise ThreadsPublishingGovernanceError("threads_publish_media_id_required")
        body = payload if isinstance(payload, dict) else {}
        text = str(body.get("text") or "").strip()
        if not text:
            raise ThreadsPublishingGovernanceError("threads_publish_reply_text_required")
        self._enforce_approval(payload=body)

        quota_prepared = self._prepare_daily_limit(action="reply")
        if self.governance.dry_run:
            out = self._dry_run_payload(
                operation="reply_to_post",
                payload={"media_id": clean_media_id, **body},
            )
            out["daily_quota_after"] = quota_prepared.get("projected") if isinstance(quota_prepared, dict) else {}
            out["daily_quota_committed"] = False
            self._append_audit(
                operation="reply_to_post",
                status="dry_run",
                payload={"media_id": clean_media_id, **body},
                response=out,
            )
            return out

        try:
            container = await self.client.create_media_container(
                media_type="TEXT",
                text=text,
                reply_to_id=clean_media_id,
                reply_control=str(body.get("reply_control") or "").strip(),
            )
            creation_id = str(container.get("id") or container.get("creation_id") or "").strip()
            if not creation_id:
                raise ThreadsPublishingGovernanceError("threads_publish_create_missing_creation_id")
            await self._wait_for_container_ready(creation_id)
            publish_resp = await self.client.publish_media_container(creation_id=creation_id)
        except ThreadsAPIError as exc:
            self._append_audit(
                operation="reply_to_post",
                status="error",
                payload={"media_id": clean_media_id, **body},
                detail=f"{type(exc).__name__}:{exc}",
            )
            raise ThreadsPublishingGovernanceError(f"threads_publish_reply_failed:{exc}") from exc
        quota_state = self._commit_daily_limit(action="reply", prepared=quota_prepared)
        self._append_audit(
            operation="reply_to_post",
            status="ok",
            payload={"media_id": clean_media_id, **body},
            response=publish_resp,
        )

        return {
            "status": "ok",
            "operation": "reply_to_post",
            "media_id": clean_media_id,
            "daily_quota_after": quota_state,
            "container_response": container,
            "publish_response": publish_resp,
        }
