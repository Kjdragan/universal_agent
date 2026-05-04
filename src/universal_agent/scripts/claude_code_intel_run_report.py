"""Run the ClaudeDevs X sync, write an operator report, and optionally email it."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import json
import logging
import os
from pathlib import Path
import sqlite3
from typing import Any

from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
from universal_agent.infisical_loader import initialize_runtime_secrets
from universal_agent.services.agentmail_service import AgentMailService
from universal_agent.services.claude_code_intel import (
    ClaudeCodeIntelConfig,
    emit_csi_activity_event,
    run_sync,
)
from universal_agent.services.claude_code_intel_operator_report import (
    build_operator_email,
    build_operator_report,
)
from universal_agent.services.claude_code_intel_replay import (
    ClaudeCodeIntelReplayConfig,
    replay_packet,
)
from universal_agent.services.claude_code_intel_rollup import build_rolling_assets
from universal_agent.services.proactive_artifacts import (
    record_email_delivery,
    upsert_artifact,
)

logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run ClaudeDevs X sync, write an operator summary, and optionally email results.",
    )
    parser.add_argument("--handle", default="", help="X handle to poll, without @. Defaults to env or ClaudeDevs.")
    parser.add_argument("--max-results", type=int, default=0, help="Maximum posts to fetch, 5-100.")
    parser.add_argument("--profile", default="", help="Deployment profile. Defaults to UA_DEPLOYMENT_PROFILE or local_workstation.")
    parser.add_argument("--no-task-hub", action="store_true", help="Do not queue Task Hub follow-up tasks.")
    parser.add_argument("--no-post-process", action="store_true", help="Skip replay-style post-processing.")
    parser.add_argument("--email-to", default="", help="Send the operator report to this recipient if provided.")
    parser.add_argument(
        "--email-policy",
        default="",
        help="Email policy: never, always, when_new_posts, when_actions, or when_tasks. Defaults to env or auto behavior.",
    )
    parser.add_argument("--no-email", action="store_true", help="Write the report only; do not send email.")
    return parser.parse_args()


def _config_from_args(args: argparse.Namespace) -> ClaudeCodeIntelConfig:
    cfg = ClaudeCodeIntelConfig.from_env()
    if args.handle.strip():
        cfg = ClaudeCodeIntelConfig(
            handle=args.handle.strip().lstrip("@"),
            max_results=cfg.max_results,
            queue_task_hub=cfg.queue_task_hub,
            request_timeout_seconds=cfg.request_timeout_seconds,
            artifacts_root=cfg.artifacts_root,
        )
    if args.max_results:
        cfg = ClaudeCodeIntelConfig(
            handle=cfg.handle,
            max_results=args.max_results,
            queue_task_hub=cfg.queue_task_hub,
            request_timeout_seconds=cfg.request_timeout_seconds,
            artifacts_root=cfg.artifacts_root,
        )
    if args.no_task_hub:
        cfg = ClaudeCodeIntelConfig(
            handle=cfg.handle,
            max_results=cfg.max_results,
            queue_task_hub=False,
            request_timeout_seconds=cfg.request_timeout_seconds,
            artifacts_root=cfg.artifacts_root,
        )
    return cfg


def _resolved_email_target(args: argparse.Namespace) -> str:
    explicit = str(args.email_to or "").strip()
    if explicit:
        return explicit
    env_target = str(os.getenv("UA_CLAUDE_CODE_INTEL_REPORT_EMAIL_TO") or "").strip()
    if env_target:
        return env_target
    if str(os.getenv("UA_DEPLOYMENT_PROFILE") or "").strip().lower() == "vps":
        return "kevinjdragan@gmail.com"
    return ""


def _resolved_email_policy(args: argparse.Namespace) -> str:
    explicit = str(args.email_policy or "").strip().lower()
    if explicit:
        return explicit
    env_policy = str(os.getenv("UA_CLAUDE_CODE_INTEL_REPORT_EMAIL_POLICY") or "").strip().lower()
    if env_policy:
        return env_policy
    if str(args.email_to or "").strip():
        return "always"
    return "when_actions"


def _should_send_email(*, policy: str, payload: dict[str, Any]) -> bool:
    normalized = str(policy or "").strip().lower() or "when_actions"
    if normalized == "never":
        return False
    if normalized == "always":
        return True
    if normalized == "when_new_posts":
        return int(payload.get("new_post_count") or 0) > 0
    if normalized == "when_tasks":
        return int(payload.get("queued_task_count") or 0) > 0
    return int(payload.get("action_count") or 0) > 0


async def _maybe_send_email(
    *,
    recipient: str,
    summary: dict[str, Any],
    conn: sqlite3.Connection,
) -> dict[str, Any]:
    subject, text, html = build_operator_email(summary)
    mail_service = AgentMailService()
    await mail_service.startup()
    if not mail_service._started:
        raise RuntimeError("agentmail_service_not_started")
    try:
        result = await mail_service.send_email(
            to=recipient,
            subject=subject,
            text=text,
            html=html,
            force_send=True,
            require_approval=False,
        )
    finally:
        await mail_service.shutdown()

    report_artifact = upsert_artifact(
        conn,
        artifact_type="claude_code_intel_operator_report",
        source_kind="runtime",
        source_ref=str(summary.get("packet_dir") or ""),
        title=f"ClaudeDevs X intel operator report: @{summary.get('handle') or 'ClaudeDevs'}",
        summary=(
            f"Operator report for packet {summary.get('packet_artifact_id') or summary.get('artifact_id') or ''} "
            f"with {summary.get('new_post_count', 0)} new posts and {summary.get('action_count', 0)} actions."
        ),
        status="surfaced",
        priority=max(1, min(int(summary.get("action_count") or 0) or 1, 4)),
        artifact_uri=str(summary.get("report_markdown_url") or ""),
        artifact_path=str(summary.get("report_markdown_path") or ""),
        topic_tags=["claude-code", "x-api", "claudedevs", "operator-report"],
        metadata={
            "packet_artifact_id": str(summary.get("packet_artifact_id") or ""),
            "packet_dir": str(summary.get("packet_dir") or ""),
            "handle": str(summary.get("handle") or ""),
        },
    )
    message_id = str((result or {}).get("message_id") or "")
    thread_id = str((result or {}).get("thread_id") or "")
    record_email_delivery(
        conn,
        artifact_id=str(report_artifact.get("artifact_id") or ""),
        message_id=message_id,
        thread_id=thread_id,
        subject=subject,
        recipient=recipient,
        metadata={"mail_status": str((result or {}).get("status") or "")},
    )
    packet_artifact_id = str(summary.get("packet_artifact_id") or summary.get("artifact_id") or "").strip()
    if packet_artifact_id:
        record_email_delivery(
            conn,
            artifact_id=packet_artifact_id,
            message_id=message_id,
            thread_id=thread_id,
            subject=subject,
            recipient=recipient,
            metadata={"mail_status": str((result or {}).get("status") or ""), "operator_report": True},
        )
    return dict(result or {})


async def main() -> int:
    args = _parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    initialize_runtime_secrets(profile=args.profile or None)
    cfg = _config_from_args(args)

    # Determine handles: explicit --handle flag → single handle, else all configured handles
    if args.handle.strip():
        handles = [args.handle.strip().lstrip("@")]
    else:
        handles = ClaudeCodeIntelConfig.all_handles_from_env()

    with connect_runtime_db(get_activity_db_path()) as conn:
        conn.row_factory = sqlite3.Row

        all_results: list[dict[str, Any]] = []
        any_ok = False

        for handle in handles:
            handle_cfg = replace(cfg, handle=handle)
            result_cfg = handle_cfg
            if not args.no_post_process and handle_cfg.queue_task_hub:
                result_cfg = replace(handle_cfg, queue_task_hub=False)

            logger.info("Syncing handle: @%s", handle)
            result = run_sync(config=result_cfg, conn=conn)

            post_process: dict[str, Any] = {}
            if result.ok and result.packet_dir and not args.no_post_process:
                post_process = replay_packet(
                    config=ClaudeCodeIntelReplayConfig(
                        packet_dir=Path(result.packet_dir),
                        queue_task_hub=handle_cfg.queue_task_hub,
                        write_vault=True,
                    ),
                    conn=conn,
                )

            handle_payload = {
                "ok": result.ok,
                "generated_at": result.generated_at,
                "handle": result.handle,
                "user_id": result.user_id,
                "packet_dir": result.packet_dir,
                "new_post_count": result.new_post_count,
                "seen_post_count": result.seen_post_count,
                "action_count": result.action_count,
                "queued_task_count": int(post_process.get("queued_task_count") or result.queued_task_count),
                "artifact_id": result.artifact_id,
                "error": result.error,
                "post_process": post_process,
            }
            all_results.append(handle_payload)
            if result.ok:
                any_ok = True
                # Emit CSI activity event for dashboard visibility
                try:
                    emit_csi_activity_event(
                        conn,
                        handle=handle,
                        new_post_count=result.new_post_count,
                        action_count=result.action_count,
                        queued_task_count=int(post_process.get("queued_task_count") or result.queued_task_count),
                        packet_dir=result.packet_dir or "",
                        actions=result.actions,
                    )
                except Exception:
                    logger.warning("Failed to emit CSI activity event for @%s", handle, exc_info=True)
                # Write a per-handle operator report only when there are actual
                # new posts — empty polls don't need operator reports.
                if result.packet_dir and result.new_post_count > 0:
                    try:
                        handle_report = build_operator_report(
                            sync_payload=handle_payload,
                            artifacts_root=cfg.artifacts_root,
                        )
                        handle_report_path = Path(result.packet_dir) / "operator_report.json"
                        handle_report_path.write_text(
                            json.dumps(handle_report, indent=2, ensure_ascii=True, sort_keys=True) + "\n",
                            encoding="utf-8",
                        )
                        logger.info("Wrote per-handle operator report: %s", handle_report_path)
                    except Exception:
                        logger.warning("Failed to write per-handle operator report for @%s", handle, exc_info=True)
            else:
                # Emit failure event so CSI errors also show in the dashboard
                try:
                    emit_csi_activity_event(
                        conn,
                        handle=handle,
                        new_post_count=0,
                        action_count=0,
                        queued_task_count=0,
                        error=result.error or "Unknown sync failure",
                    )
                except Exception:
                    logger.warning("Failed to emit CSI failure event for @%s", handle, exc_info=True)
            logger.info(
                "Handle @%s: ok=%s, new_posts=%d, actions=%d",
                handle, result.ok, result.new_post_count, result.action_count,
            )

        # Use the first successful result with actual content for operator report;
        # aggregate stats across all handles.
        primary_payload = next(
            (r for r in all_results if r["ok"] and r.get("packet_dir")),
            next((r for r in all_results if r["ok"]), all_results[0] if all_results else {}),
        )
        combined_payload = dict(primary_payload)
        combined_payload["handles_synced"] = [r["handle"] for r in all_results]
        combined_payload["handle_results"] = all_results
        combined_payload["new_post_count"] = sum(r.get("new_post_count", 0) for r in all_results)
        combined_payload["action_count"] = sum(r.get("action_count", 0) for r in all_results)
        combined_payload["queued_task_count"] = sum(r.get("queued_task_count", 0) for r in all_results)

        total_new = combined_payload["new_post_count"]
        summary: dict[str, Any] = {}
        if total_new > 0 and combined_payload.get("packet_dir"):
            summary = build_operator_report(sync_payload=combined_payload, artifacts_root=cfg.artifacts_root)
        else:
            logger.info(
                "📡 CSI combined: %d new posts across %d handles — skipping operator report.",
                total_new, len(handles),
            )

        # Rollup runs once — it scans ALL packet dirs across all handles
        rolling: dict[str, Any] = {}
        if any_ok and total_new > 0:
            rolling = build_rolling_assets(artifacts_root=cfg.artifacts_root)

        email_result: dict[str, Any] = {}
        email_to = _resolved_email_target(args)
        email_policy = _resolved_email_policy(args)
        should_email = bool(email_to) and not args.no_email and _should_send_email(policy=email_policy, payload=combined_payload)
        if should_email:
            email_result = await _maybe_send_email(recipient=email_to, summary=summary, conn=conn)

    print(
        json.dumps(
            {
                **combined_payload,
                "email_policy": email_policy,
                "email_to": email_to,
                "operator_report": {
                    "markdown_path": summary.get("report_markdown_path"),
                    "json_path": summary.get("report_json_path"),
                    "markdown_url": summary.get("report_markdown_url"),
                },
                "rolling": rolling,
                "email_result": email_result,
            },
            indent=2,
            ensure_ascii=True,
            sort_keys=True,
        )
    )
    return 0 if any_ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
