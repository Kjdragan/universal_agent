"""
Documentation Maintenance Agent — Stage 2

Consumes the structured drift report produced by Stage 1.

Trigger: This script is invoked inline by the GHA workflow
(nightly-doc-drift-audit.yml) via Tailscale SSH to the VPS, immediately
after Stage 1 completes. There is NO separate UA cron job — the GHA
workflow is the sole canonical trigger to guarantee sequencing.

Stage 1 (doc_drift_auditor.py) runs as a GitHub Actions scheduled workflow at
~3:17 AM CDT (08:17 UTC) and commits the drift report directly to the develop
branch under artifacts/doc-drift-reports/<date>/. The GHA workflow then copies
the report to the VPS via SCP and runs this script.

Dispatch is performed via the gateway HTTP API (POST /api/v1/ops/vp/missions/dispatch)
to ensure the mission is written to the same DB the VP workers poll.  When running
on the VPS, the gateway is at localhost:8000.  A fallback URL is resolved
from UA_GATEWAY_URL or the public endpoint.

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
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]  # src/universal_agent/scripts -> repo root
ARTIFACTS_BASE = REPO_ROOT / "artifacts" / "doc-drift-reports"

# Additional search roots when running on the VPS.  Stage 1 commits the drift
# report to develop via GHA; after the staging/prod deploy pulls the latest
# SHA the report lives under one of these paths.
_VPS_SEARCH_ROOTS = [
    ARTIFACTS_BASE,
    Path("/opt/universal-agent-staging/artifacts/doc-drift-reports"),
    Path("/opt/universal_agent/artifacts/doc-drift-reports"),
]

# Gateway URLs to try in order.  On the VPS the gateway runs on the same host.
# Port 8002 = ops API (dispatch endpoint), 8001 = main gateway (health check).
_GATEWAY_URLS = [
    "http://localhost:8002",
    "http://localhost:8001",
    "https://app.clearspringcg.com",
]


def _resolve_gateway_url() -> str:
    """Return the gateway base URL, preferring env var, then localhost, then public."""
    from_env = os.getenv("UA_GATEWAY_URL", "").strip()
    if from_env:
        return from_env.rstrip("/")
    # On the VPS the gateway runs two workers: port 8002 (ops API with dispatch
    # endpoint) and port 8001 (main gateway).  Probe the actual dispatch endpoint
    # rather than /api/health to find the correct port.
    dispatch_path = "/api/v1/ops/vp/missions/dispatch"
    for url in _GATEWAY_URLS:
        try:
            req = urllib.request.Request(f"{url}{dispatch_path}", method="POST",
                                         data=b"{}", headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=3)
        except urllib.error.HTTPError as exc:
            # 401/422 = endpoint exists (auth required or bad payload) → correct port
            if exc.code in (401, 422):
                return url.rstrip("/")
        except Exception:
            continue
    # Fallback to public endpoint
    return _GATEWAY_URLS[-1].rstrip("/")


def _resolve_auth_token() -> str:
    """Resolve the ops/auth token for gateway API calls."""
    for key in ("UA_OPS_TOKEN", "AUTH_TOKEN"):
        value = os.getenv(key, "").strip()
        if value:
            return value
    return ""


def _find_todays_report() -> Path | None:
    """Locate the most recent drift report JSON.

    Stage 1 runs as a GitHub Actions workflow that commits the report into
    the develop branch.  After the VPS deploy workflow pulls the latest code
    the file is available at:
        artifacts/doc-drift-reports/<YYYY-MM-DD>/drift_report.json

    We search all known repo roots so the script works both locally and on the
    staging / production VPS.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for base in _VPS_SEARCH_ROOTS:
        report_path = base / today / "drift_report.json"
        if report_path.exists():
            logger.info(f"Found today's drift report at {report_path}")
            return report_path

    # Fallback: most-recent report from any date (covers brief UTC day boundary
    # window where today's GHA run hasn't committed yet)
    for base in _VPS_SEARCH_ROOTS:
        if not base.exists():
            continue
        try:
            for subdir in sorted(base.iterdir(), reverse=True):
                candidate = subdir / "drift_report.json"
                if candidate.exists():
                    logger.info(f"Using most-recent fallback report: {candidate}")
                    return candidate
        except PermissionError:
            continue

    return None


def _build_batched_objectives(report: dict) -> list[dict]:
    """Build compact, severity-batched VP mission objectives from the drift report.

    Returns a list of dicts with keys: severity, objective, issue_count.
    Each objective is kept under ~2KB to prevent agent stalling (observed with
    large 30KB+ payloads that contain 75+ issues).
    """
    date = report.get("report_date", "unknown")
    issues = report.get("issues", [])

    if not issues:
        return []

    # Group by severity
    by_severity: dict[str, list[dict]] = {}
    for issue in issues:
        sev = issue.get("severity", "P2")
        by_severity.setdefault(sev, []).append(issue)

    # Instructions per category (compact)
    fix_instructions = {
        "index_dead_entry": "Remove stale entry from both index files. If file was moved, update the link.",
        "index_orphan": "Add to both docs/README.md and docs/Documentation_Status.md in the correct section.",
        "broken_link": "Fix the broken link or remove if target was intentionally deleted.",
        "glossary_candidate": "Only add genuinely project-specific terms to docs/Glossary.md. SKIP generic programming terms (SQL keywords, common infra words, standard library names). A good glossary term is unique to this project (e.g. 'Brain Transplant', 'VP Worker', 'Heartbeat Service').",
        "deploy_cochange_violation": "Update docs/deployment/ to reflect current deployment behavior.",
        "agentic_drift": "Update AGENTS.md or workflow/SKILL.md files to match code changes.",
        "code_doc_drift": "Update docs to accurately reflect current code behavior.",
    }

    MAX_ISSUES_PER_BATCH = 15  # larger batches stall the agent

    batches = []
    for severity in ["P0", "P1", "P2"]:
        sev_issues = by_severity.get(severity, [])
        if not sev_issues:
            continue

        # Chunk into sub-batches of MAX_ISSUES_PER_BATCH
        chunks = [
            sev_issues[i : i + MAX_ISSUES_PER_BATCH]
            for i in range(0, len(sev_issues), MAX_ISSUES_PER_BATCH)
        ]

        for chunk_idx, chunk in enumerate(chunks):
            suffix = f"-{chr(97 + chunk_idx)}" if len(chunks) > 1 else ""
            branch_name = f"docs/{severity.lower()}-fix{suffix}-{date}"
            commit_msg = f"docs: fix {len(chunk)} {severity} issues — nightly drift {date}"

            # Build compact objective — agent only needs to branch, fix, commit.
            # Push + PR + merge is handled by the post-mission hook in worker_loop.py.
            lines = [
                f"You are the Documentation Maintenance Agent. Today is {date}.",
                f"Investigate these {len(chunk)} {severity} issues and fix ONLY those that are genuinely stale.",
                "",
                "## Process",
                f"1. Create and checkout branch: `{branch_name}`",
                "2. For EACH issue: verify before fixing (see rules below)",
                "3. Only commit files you actually changed",
                f"4. Commit message: `{commit_msg}`",
                "5. Do NOT push — the build system handles push and PR creation automatically.",
                "",
                "## Rules",
                "",
                "### Verify Before Fixing (CRITICAL)",
                "This auditing process serves as a backstop. We generally update documentation as we go along, but sometimes we forget specific features or elements.",
                "Therefore, it is CRITICAL that you comprehensively read the existing documentation to understand the 'spirit' and scope of what is already there.",
                "The drift report flags potential issues based on git co-change heuristics, but it does NOT prove the documentation is actually wrong. Before editing any file:",
                "",
                "1. **Read the complete current content** of the referenced doc file to grasp the spirit of what exists.",
                "2. **Read the relevant source code** that the issue refers to.",
                "3. **Compare**: Does the documentation already broadly address the codebase changes?",
                "4. **If YES** — the doc is already correct (likely updated manually). **SKIP** this issue.",
                "   Add a line to the commit body: `Skipped: <file> — already accurate`",
                "5. **If NO** — the doc is genuinely missing important updates. Fix it by filling in the blanks.",
                "   When adding to the document, ensure your additions are in the spirit of the entire codebase changes, not just one disconnected element.",
                "",
                "Do NOT assume a flagged file needs changes. Assess the likelihood that recent changes were already adequately covered.",
                "",
                "### General",
                "- All documentation MUST reside within `docs/`",
                "- Update BOTH `docs/README.md` AND `docs/Documentation_Status.md` when adding entries",
                "- Update existing docs rather than creating new ones",
                "- If ALL issues are already addressed, do not create a commit at all",
                "",
                f"## {severity} Issues",
                "",
            ]

            for i, issue in enumerate(chunk, 1):
                cat = issue["category"]
                instruction = fix_instructions.get(cat, issue.get("suggested_action", "Review and fix."))
                lines.append(f"{i}. **{issue['file']}**: {issue['description']}")
                lines.append(f"   Fix: {instruction}")

            objective = "\n".join(lines)
            batches.append({
                "severity": f"{severity}{suffix}",
                "objective": objective,
                "issue_count": len(chunk),
            })

    return batches


def _dispatch_via_gateway(
    gateway_url: str,
    auth_token: str,
    objective: str,
    idempotency_key: str,
) -> dict:
    """Dispatch a VP mission via the gateway HTTP API.

    Returns the parsed JSON response body on success, raises on failure.
    """
    payload = json.dumps({
        "vp_id": "vp.coder.primary",
        "objective": objective,
        "mission_type": "doc-maintenance",
        "source_session_id": "doc-maintenance-agent",
        "reply_mode": "async",
        "priority": 100,
        "idempotency_key": idempotency_key,
        "execution_mode": "sdk",
        "system_prompt_injection": (
            "## Documentation Maintenance Mission\n\n"
            "You are operating as a **Documentation Maintenance Agent** for this mission.\n\n"
            "### Your Task\n"
            "Verify and fix documentation drift issues identified by an automated audit.\n"
            "This process is a backstop. We usually update docs as we build, but this catches what we miss.\n"
            "Read the complete existing documentation to understand its spirit. Only fill in the blanks if a meaningful change was missed.\n"
            "If you make additions, ensure they align with the spirit of the entire changeset, not just isolated elements.\n"
            "Each issue includes a file path, line reference, and suspected staleness.\n\n"
            "### Fast-Skip Rules (Apply FIRST to Save Time)\n"
            "Some issue categories have high false-positive rates. Check these BEFORE "
            "reading source code:\n\n"
            "- **glossary_candidate**: SKIP any term that is a generic programming keyword "
            "(SQL: SELECT/INSERT/UPDATE/TABLE/COLUMN/WHERE, infra: deploy/staging/service/"
            "config, standard library names). Only add terms genuinely unique to THIS project "
            "(e.g. 'Brain Transplant', 'VP Worker', 'Heartbeat Service', 'CSI Pipeline'). "
            "If the term would appear in any generic software project, skip it immediately.\n"
            "- **code_doc_drift**: Read the CURRENT docs first. If they already accurately "
            "describe code behavior, skip. The audit flags co-change absence, not actual "
            "staleness.\n"
            "- **agentic_drift**: Check if AGENTS.md was recently updated. If the current "
            "content is accurate, skip.\n\n"
            "### Verification Rules\n"
            "1. **Read the source** before making changes — some 'drift' may be a false positive.\n"
            "2. If the documentation is actually correct, skip the issue and note it as verified.\n"
            "3. Prefer updating docs to match code, not code to match docs.\n"
            "4. Keep changes minimal — fix the drift, don't rewrite the document.\n\n"
            "### Commit Discipline\n"
            "- Create a `docs/` branch for your changes.\n"
            "- Make atomic commits per file or logical change.\n"
            "- Do NOT push — the build system handles push/PR/merge automatically.\n"
        ),
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{gateway_url}/api/v1/ops/vp/missions/dispatch",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {auth_token}",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


async def main():
    """Entry point for cron script execution."""
    import time as _time
    _stage2_start = _time.monotonic()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Initialize runtime secrets (Infisical)
    try:
        from universal_agent.infisical_loader import initialize_runtime_secrets
        initialize_runtime_secrets(profile="local_workstation")
    except Exception as exc:
        logger.warning(f"Infisical init skipped: {exc}")

    auth_token = _resolve_auth_token()
    if not auth_token:
        logger.error("No auth token found (UA_OPS_TOKEN or AUTH_TOKEN). Cannot dispatch.")
        sys.exit(1)

    # Resolve the gateway URL (prefers localhost on VPS)
    gateway_url = _resolve_gateway_url()
    logger.info(f"Using gateway: {gateway_url}")

    # Pre-flight: verify gateway is reachable before spending time on report parsing
    try:
        health_req = urllib.request.Request(
            f"{gateway_url}/api/health", method="GET",
        )
        with urllib.request.urlopen(health_req, timeout=5) as resp:
            logger.info(f"Gateway health check: HTTP {resp.status} — OK")
    except Exception as gw_exc:
        logger.warning(
            f"Gateway health pre-check failed ({type(gw_exc).__name__}: {gw_exc}). "
            "Dispatch will retry with backoff if needed."
        )

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

    logger.info(f"Found {total_issues} issues — building batched objectives by severity")

    # Build severity-batched objectives (P0, P1, P2 as separate missions)
    batches = _build_batched_objectives(report)
    if not batches:
        logger.info("No actionable batches produced from drift report.")
        sys.exit(0)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H-%M")
    dispatched = 0
    failed = 0

    for batch in batches:
        severity = batch["severity"]
        objective = batch["objective"]
        issue_count = batch["issue_count"]
        idempotency_key = f"doc-maintenance-{severity.lower()}-{today}"

        logger.info(
            f"Dispatching {severity} batch: {issue_count} issues, "
            f"objective={len(objective)} chars, key={idempotency_key}"
        )

        # Retry with backoff if gateway is unavailable
        max_retries = 3
        retry_delays = [30, 60, 120]
        success = False

        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"  {severity} dispatch attempt {attempt}/{max_retries}")
                result = _dispatch_via_gateway(gateway_url, auth_token, objective, idempotency_key)
                mission = result.get("mission", {})
                mission_id = mission.get("mission_id", "unknown")
                status = mission.get("status", "unknown")
                logger.info(f"  ✅ {severity} mission dispatched: {mission_id} (status={status})")
                dispatched += 1
                success = True
                break
            except urllib.error.HTTPError as exc:
                body = ""
                try:
                    body = exc.read().decode("utf-8")[:500]
                except Exception:
                    pass
                logger.warning(f"  Attempt {attempt} — HTTP {exc.code}: {exc.reason}. Body: {body}")
            except Exception as exc:
                logger.warning(f"  Attempt {attempt} — dispatch failed: {type(exc).__name__}: {exc}")

            if attempt < max_retries:
                delay = retry_delays[attempt - 1]
                logger.info(f"  Retrying in {delay}s...")
                await asyncio.sleep(delay)

        if not success:
            logger.error(f"  ❌ All {max_retries} attempts failed for {severity} batch")
            failed += 1

        # Small delay between batches to avoid queue flooding
        if batch != batches[-1]:
            logger.info("  Waiting 30s before next batch...")
            await asyncio.sleep(30)

    elapsed = _time.monotonic() - _stage2_start
    logger.info(
        f"Dispatch complete in {elapsed:.1f}s: "
        f"{dispatched} batches dispatched, {failed} failed"
    )
    if failed > 0:
        logger.error("❌ Some batches failed to dispatch. Health check will alert Simone.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
