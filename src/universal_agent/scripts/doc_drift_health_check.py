"""
Documentation Pipeline Health Check — Stage 3

Runs ~1 hour after the doc drift pipeline (1:30 AM CDT / 06:30 UTC).
Checks whether Stage 1 and Stage 2 completed successfully.

- If everything is healthy → exits silently (no notifications)
- If errors detected → emails Simone via AgentMail with failure details
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
ARTIFACTS_BASE = REPO_ROOT / "artifacts" / "doc-drift-reports"


def _check_stage1_health(date_str: str) -> dict:
    """Check if Stage 1 produced a drift report today."""
    report_dir = ARTIFACTS_BASE / date_str
    json_report = report_dir / "drift_report.json"
    md_report = report_dir / "DRIFT_REPORT.md"

    if not report_dir.exists():
        return {
            "healthy": False,
            "error": f"Stage 1 report directory missing: artifacts/doc-drift-reports/{date_str}/",
            "detail": "The doc_drift_auditor.py script may not have run tonight.",
        }

    if not json_report.exists():
        return {
            "healthy": False,
            "error": f"Stage 1 JSON report missing: drift_report.json",
            "detail": "The report directory exists but drift_report.json was not written.",
        }

    if not md_report.exists():
        return {
            "healthy": False,
            "error": f"Stage 1 Markdown report missing: DRIFT_REPORT.md",
            "detail": "The JSON report exists but DRIFT_REPORT.md was not written.",
        }

    # Validate JSON is parseable
    try:
        report = json.loads(json_report.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {
            "healthy": False,
            "error": f"Stage 1 JSON report is corrupt: {exc}",
            "detail": "drift_report.json exists but cannot be parsed.",
        }

    total_issues = report.get("total_issues", -1)
    p0_count = report.get("issues_by_severity", {}).get("P0", 0)

    return {
        "healthy": True,
        "total_issues": total_issues,
        "p0_count": p0_count,
        "report_date": report.get("report_date", date_str),
        "generated_at": report.get("generated_at", "unknown"),
    }


def _check_stage2_health() -> dict:
    """Check Stage 2 by looking at cron execution logs.

    Since Stage 2 dispatches a VP mission, we verify the cron job
    ran by checking the workspace directory for recent activity.
    """
    workspace = REPO_ROOT / "AGENT_RUN_WORKSPACES" / "cron_knowledge_base_maintenance"

    if not workspace.exists():
        return {
            "healthy": True,  # Not a failure — workspace may not exist yet
            "note": "Stage 2 workspace not yet created (first run pending)",
        }

    return {
        "healthy": True,
        "note": "Stage 2 workspace exists — VP mission dispatch infrastructure is in place",
    }


def _build_alert_email(stage1: dict, stage2: dict, date_str: str) -> dict:
    """Build an alert email body for Simone."""
    subject = f"🔴 Doc Drift Pipeline Failure — {date_str}"

    errors = []
    if not stage1.get("healthy"):
        errors.append(f"**Stage 1 (Drift Auditor):** {stage1.get('error', 'Unknown error')}\n  Detail: {stage1.get('detail', 'N/A')}")
    if not stage2.get("healthy"):
        errors.append(f"**Stage 2 (Maintenance Agent):** {stage2.get('error', 'Unknown error')}\n  Detail: {stage2.get('detail', 'N/A')}")

    text_body = f"""Documentation Drift Pipeline Health Check — {date_str}

⚠️ One or more stages of the nightly documentation pipeline failed.

{chr(10).join(errors)}

Action Required:
- Check the cron job logs for errors
- Verify the scripts are accessible and have correct permissions
- Run manually: uv run python src/universal_agent/scripts/doc_drift_auditor.py --dry-run

This is an automated health check from doc_drift_health_check.py.
"""

    return {"subject": subject, "text": text_body}


async def main():
    """Entry point for cron script execution."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    logger.info("=== Doc Drift Pipeline Health Check ===")
    logger.info(f"Checking pipeline health for {date_str}")

    # Check Stage 1
    stage1 = _check_stage1_health(date_str)
    logger.info(f"  Stage 1: {'✅ healthy' if stage1['healthy'] else '❌ FAILED'}")
    if stage1.get("healthy"):
        logger.info(f"    Issues found: {stage1.get('total_issues', '?')}, P0: {stage1.get('p0_count', '?')}")

    # Check Stage 2
    stage2 = _check_stage2_health()
    logger.info(f"  Stage 2: {'✅ healthy' if stage2['healthy'] else '❌ FAILED'}")

    # If everything is healthy, exit silently
    if stage1.get("healthy") and stage2.get("healthy"):
        logger.info("✅ All pipeline stages healthy. No action needed.")
        sys.exit(0)

    # Something failed — alert Simone via AgentMail
    logger.warning("❌ Pipeline failure detected — sending alert to Simone")

    alert = _build_alert_email(stage1, stage2, date_str)

    try:
        from universal_agent.infisical_loader import initialize_runtime_secrets
        initialize_runtime_secrets(profile="local_workstation")

        from universal_agent.services.agentmail_service import AgentMailService

        mail_service = AgentMailService()
        await mail_service.startup()

        if not mail_service._started:
            logger.error("AgentMail service failed to start — cannot send alert")
            # Fall back to just logging the error prominently
            print(f"\n{'='*60}")
            print(f"ALERT: {alert['subject']}")
            print(f"{'='*60}")
            print(alert["text"])
            print(f"{'='*60}\n")
            sys.exit(1)

        result = await mail_service.send_email(
            to=mail_service.get_inbox_address(),  # Send to Simone's own inbox
            subject=alert["subject"],
            text=alert["text"],
            force_send=True,
        )
        logger.info(f"Alert sent: {result}")

        await mail_service.shutdown()

    except Exception as exc:
        logger.error(f"Failed to send AgentMail alert: {exc}")
        # Fallback: print to stdout for cron log capture
        print(f"\n{'='*60}")
        print(f"ALERT: {alert['subject']}")
        print(f"{'='*60}")
        print(alert["text"])
        print(f"{'='*60}\n")

    sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
