"""
Documentation Maintenance Agent — Stage 2

Consumes the structured drift report produced by Stage 1 (doc_drift_auditor.py)
and dispatches a VP agent mission to fix the identified issues.

The VP agent will:
  1. Create a feature branch docs/nightly-drift-fix-{date}
  2. Make documentation fixes
  3. Commit with a descriptive message
  4. Open a self-approving PR against develop
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from universal_agent.infisical_loader import initialize_runtime_secrets

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]  # src/universal_agent/scripts -> repo root
ARTIFACTS_BASE = REPO_ROOT / "artifacts" / "doc-drift-reports"


def _find_todays_report() -> Path | None:
    """Locate the most recent drift report JSON."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    report_path = ARTIFACTS_BASE / today / "drift_report.json"
    if report_path.exists():
        return report_path

    # Fallback: check if there's a report from the last few hours
    # (in case Stage 1 ran just before midnight UTC)
    for subdir in sorted(ARTIFACTS_BASE.iterdir(), reverse=True):
        candidate = subdir / "drift_report.json"
        if candidate.exists():
            return candidate

    return None


def _build_mission_objective(report: dict) -> str:
    """Build a structured VP mission objective from the drift report."""
    date = report.get("report_date", "unknown")
    total = report.get("total_issues", 0)
    issues = report.get("issues", [])

    if total == 0:
        return ""

    # Group issues by category for clearer instructions
    by_category: dict[str, list[dict]] = {}
    for issue in issues:
        cat = issue["category"]
        by_category.setdefault(cat, []).append(issue)

    objective_parts = [
        f"You are the Documentation Maintenance Agent. Today is {date}.",
        f"The nightly drift auditor found {total} documentation issues that need fixing.",
        "",
        "## Your Mission",
        "",
        "Fix all the issues listed below, then commit and push.",
        "",
        "## Process",
        "",
        f"1. Create and checkout branch: `docs/nightly-drift-fix-{date}`",
        "2. Fix each issue below",
        "3. Commit with message: `docs: nightly drift fix {date} — {total} issues`",
        "4. Push the branch and open a PR against `develop`",
        "5. The PR description should include the full issue list and what was fixed",
        "",
        "## Rules",
        "",
        "- All documentation MUST reside within `docs/`",
        "- When adding new docs, you MUST update BOTH `docs/README.md` AND `docs/Documentation_Status.md`",
        "- Update existing docs rather than creating new ones where possible",
        "- Follow the existing documentation style and tone (professional engineer reference docs)",
        "- Do NOT delete any documentation files without strong justification",
        "- For glossary candidates (P2), only add terms that are genuinely project-specific",
        "",
        "## Issues to Fix",
        "",
    ]

    severity_order = ["P0", "P1", "P2"]
    category_instructions = {
        "index_orphan": "Add the orphan file to both docs/README.md and docs/Documentation_Status.md in the appropriate section.",
        "index_dead_entry": "Remove the stale entry from the index files. If the file was moved, update the link to point to the new location.",
        "broken_link": "Fix the broken link — either correct the path or remove the link if the target was intentionally deleted.",
        "glossary_candidate": "If the term is genuinely project-specific, add it to docs/Glossary.md with a concise definition. Skip generic industry terms.",
        "deploy_cochange_violation": "Review the deployment workflow changes and update docs/deployment/ to reflect the current deployment behavior.",
        "agentic_drift": "Review the agent code changes and determine if AGENTS.md, workflow files, or SKILL.md files need updating.",
        "code_doc_drift": "Read the changed code and the corresponding documentation. Update the docs to accurately reflect current code behavior.",
    }

    for severity in severity_order:
        sev_issues = [i for i in issues if i["severity"] == severity]
        if not sev_issues:
            continue

        objective_parts.append(f"### {severity} Issues")
        objective_parts.append("")

        for issue in sev_issues:
            cat = issue["category"]
            instruction = category_instructions.get(cat, issue.get("suggested_action", "Review and fix."))
            objective_parts.append(f"- **[{cat}] {issue['file']}**: {issue['description']}")
            objective_parts.append(f"  - *How to fix:* {instruction}")
        objective_parts.append("")

    return "\n".join(objective_parts)


async def main():
    """Entry point for cron script execution."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Initialize runtime secrets
    initialize_runtime_secrets(profile="local_workstation")

    api_key = os.getenv("UA_OPS_TOKEN", "")
    if not api_key:
        logger.error("UA_OPS_TOKEN is required for autonomous operations.")
        sys.exit(1)

    # Find today's drift report
    report_path = _find_todays_report()
    if not report_path:
        logger.info("No drift report found. Stage 1 may not have run yet. Exiting.")
        sys.exit(0)

    logger.info(f"Loading drift report from {report_path}")
    report = json.loads(report_path.read_text(encoding="utf-8"))

    total_issues = report.get("total_issues", 0)
    if total_issues == 0:
        logger.info("✅ Drift report shows zero issues. All documentation is in sync!")
        sys.exit(0)

    logger.info(f"Found {total_issues} issues — dispatching VP maintenance mission")

    # Build scoped objective
    objective = _build_mission_objective(report)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H-%M")

    from universal_agent.tools.vp_orchestration import _vp_dispatch_mission_impl

    result = await _vp_dispatch_mission_impl({
        "vp_id": "vp.coder.primary",
        "objective": objective,
        "mission_type": "doc-maintenance",
        "idempotency_key": f"doc-maintenance-{today}",
        "execution_mode": "sdk",
    })

    if result.get("content", [{}])[0].get("text"):
        res_data = json.loads(result["content"][0]["text"])
        if res_data.get("ok"):
            logger.info(f"✅ Successfully dispatched doc maintenance mission: {res_data.get('mission_id')}")
        else:
            logger.error(f"❌ Failed to dispatch mission: {res_data}")
            sys.exit(1)
    else:
        logger.error(f"❌ Unexpected result format: {result}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
