"""
OpenClaw Sync Analysis Agent — Stage 2

Consumes the structured release report produced by Stage 1
(openclaw_release_scanner.py) and dispatches a VP coder mission that
analyzes each new OpenClaw feature against our Universal Agent codebase.

Trigger: This script is invoked inline by the GHA workflow
(openclaw-release-sync.yml) via Tailscale SSH to the VPS, immediately
after Stage 1 completes. The GHA workflow is the sole canonical trigger
to guarantee sequencing.

The VP coder agent will:
  1. Read the OpenClaw release report
  2. Scan our Universal Agent codebase for relevant counterpart areas
  3. Produce a structured analysis for each feature with adoption guidance
  4. Save the report to Openclaw Sync Discoveries/<date>/
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
ARTIFACTS_BASE = REPO_ROOT / "artifacts" / "openclaw-sync"
DISCOVERIES_DIR = REPO_ROOT / "Openclaw Sync Discoveries"

# Additional search roots when running on the VPS
_VPS_SEARCH_ROOTS = [
    ARTIFACTS_BASE,
    Path("/opt/universal-agent-staging/artifacts/openclaw-sync"),
    Path("/opt/universal_agent/artifacts/openclaw-sync"),
]

# Gateway URLs to try in order (same pattern as doc_maintenance_agent.py)
_GATEWAY_URLS = [
    "http://localhost:8002",
    "http://localhost:8001",
    "https://app.clearspringcg.com",
]

# Our Universal Agent's key component areas for cross-referencing
UA_COMPONENT_AREAS = """
Universal Agent key directories & components:
- src/universal_agent/gateway.py, gateway_server.py — Gateway (HTTP/WebSocket API)
- src/universal_agent/agent_core.py, agent_setup.py — Agent Runtime & Setup
- src/universal_agent/execution_engine.py — Execution Engine
- src/universal_agent/tools/ — Tool System
- src/universal_agent/memory/ — Memory System
- src/universal_agent/bot/ — Telegram/Channel Integration
- src/universal_agent/cron_service.py — Cron & Scheduling
- src/universal_agent/hooks.py, hooks_service.py — Hooks & Integrations
- src/universal_agent/session/ — Session Management
- src/universal_agent/vp/ — VP (Virtual Personnel) Agent System
- src/universal_agent/delegation/ — Agent Delegation
- src/universal_agent/supervisors/ — Supervisor Agents
- src/universal_agent/guardrails/ — Safety Guardrails
- src/universal_agent/durable/ — Durable Execution
- src/universal_agent/heartbeat_service.py — Heartbeat & Health Monitoring
- src/universal_agent/prompt_builder.py — Prompt Engineering
- src/universal_agent/sdk/ — SDK Layer
- src/universal_agent/services/ — Shared Services
- src/universal_agent/identity/ — Identity & Auth
- src/universal_agent/auth/ — Authentication
- .agents/ — Agent Workflows, Skills, Configuration
- web-ui/ — Next.js Dashboard UI
"""


def _resolve_gateway_url() -> str:
    """Return the gateway base URL, preferring env var, then localhost, then public."""
    from_env = os.getenv("UA_GATEWAY_URL", "").strip()
    if from_env:
        return from_env.rstrip("/")
    dispatch_path = "/api/v1/ops/vp/missions/dispatch"
    for url in _GATEWAY_URLS:
        try:
            req = urllib.request.Request(f"{url}{dispatch_path}", method="POST",
                                         data=b"{}", headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=3)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 422):
                return url.rstrip("/")
        except Exception:
            continue
    return _GATEWAY_URLS[-1].rstrip("/")


def _resolve_auth_token() -> str:
    """Resolve the ops/auth token for gateway API calls."""
    for key in ("UA_OPS_TOKEN", "AUTH_TOKEN"):
        value = os.getenv(key, "").strip()
        if value:
            return value
    return ""


def _find_release_report() -> Path | None:
    """Locate the most recent OpenClaw release report JSON."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for base in _VPS_SEARCH_ROOTS:
        report_path = base / today / "release_report.json"
        if report_path.exists():
            logger.info(f"Found today's release report at {report_path}")
            return report_path

    # Fallback: most-recent report from any date
    for base in _VPS_SEARCH_ROOTS:
        if not base.exists():
            continue
        try:
            for subdir in sorted(base.iterdir(), reverse=True):
                candidate = subdir / "release_report.json"
                if candidate.exists():
                    logger.info(f"Using most-recent fallback report: {candidate}")
                    return candidate
        except PermissionError:
            continue

    return None


def _load_previous_discoveries() -> list[str]:
    """Load list of features previously reported to detect recurring gaps."""
    previous_features: list[str] = []
    if not DISCOVERIES_DIR.exists():
        return previous_features

    for subdir in sorted(DISCOVERIES_DIR.iterdir(), reverse=True):
        analysis_file = subdir / "sync_analysis.json"
        if analysis_file.exists():
            try:
                data = json.loads(analysis_file.read_text(encoding="utf-8"))
                for feature in data.get("features_analyzed", []):
                    if feature.get("recommendation") in ("WATCH", "INVESTIGATE"):
                        previous_features.append(feature.get("name", ""))
            except (json.JSONDecodeError, OSError):
                continue
        # Only look back ~6 recent reports
        if len(previous_features) > 50:
            break

    return previous_features


def _build_sync_objective(report: dict) -> str:
    """Build the VP mission objective from the release report."""
    date = report.get("report_date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    releases = report.get("releases", [])
    release_count = len(releases)

    # Load previous discoveries for recurring gap detection
    previous_watch_features = _load_previous_discoveries()
    recurring_note = ""
    if previous_watch_features:
        feature_list = ", ".join(previous_watch_features[:15])
        recurring_note = f"""
## Recurring Innovation Gap Detection
The following features were flagged as WATCH or INVESTIGATE in previous sync reports:
{feature_list}

If any of these features appear AGAIN in the current releases with significant updates,
ELEVATE their recommendation to ADOPT and flag them as "Recurring Innovation Gap" with
a clear explanation of why we should now prioritize building this capability.
"""

    # Build a compact summary of changes for the objective
    changes_summary_lines = []
    for rel in releases:
        tag = rel.get("tag", "unknown")
        categories = rel.get("changelog_categories", {})
        for category, entries in categories.items():
            for entry in entries[:5]:  # limit per category to keep objective manageable
                changes_summary_lines.append(f"- [{tag}] {category}: {entry}")
        if not categories and rel.get("raw_body"):
            # Truncate raw body
            body = rel["raw_body"][:500]
            changes_summary_lines.append(f"- [{tag}] {body}")

    changes_text = "\n".join(changes_summary_lines[:60])  # hard cap

    discoveries_path = f"Openclaw Sync Discoveries/{date}"

    objective = f"""You are the OpenClaw Sync Analysis Agent. Today is {date}.

You have been given a report of {release_count} new release(s) from the OpenClaw
agent framework (https://github.com/openclaw/openclaw). Your mission is to analyze
each significant change and assess its relevance to our Universal Agent project.

## Our Project Context

{UA_COMPONENT_AREAS}

## New OpenClaw Changes

{changes_text}

{recurring_note}

## Your Task

For each significant new feature or change in the OpenClaw releases:

1. **Understand the feature**: What does it do in OpenClaw?
2. **Find our counterpart**: Where in our codebase would similar functionality live?
3. **Assess relevance**: Rate as HIGH, MEDIUM, LOW, or NOT_APPLICABLE
4. **Make a recommendation**: ADOPT, WATCH, SKIP, or INVESTIGATE
5. **Provide implementation guidance**: How would we emulate this in our architecture?

### Analysis Template (for each feature)

```
Feature: [name]
OpenClaw Component: [which area of OpenClaw]
OpenClaw References: [key files/directories in OpenClaw to study]
Relevance: [HIGH/MEDIUM/LOW/NOT_APPLICABLE]
Recommendation: [ADOPT/WATCH/SKIP/INVESTIGATE]
Our Counterpart: [where this would live in our codebase]
Gap Analysis: [what we have vs what this adds]
Implementation Notes: [how we'd build this, key architectural considerations]
Effort: [S/M/L/XL]
Priority: [if ADOPT — suggested priority 1-5]
```

### Important Rules

- **SKIP** features that are platform-specific to OpenClaw (iOS/macOS/Android native
  clients, Docker-specific features) unless the underlying concept is transferable
- **SKIP** features that modify OpenClaw subsystems we don't have AND don't need
- **Flag clearly** when a feature modifies something OpenClaw has that we DON'T have,
  and assess whether we should consider building that underlying capability
- Focus on **concepts and patterns** we can emulate, not direct code copying
- Be specific about WHERE in our codebase changes would go
- Reference specific OpenClaw files/directories a coding agent could study

## Output

Save your analysis as TWO files:

1. `{discoveries_path}/SYNC_REPORT.md` — Human-readable report with full analysis
2. `{discoveries_path}/sync_analysis.json` — Structured JSON with fields:
   - report_date, openclaw_releases_analyzed, features_analyzed (array of feature objects)

Create the output directory if it doesn't exist: `mkdir -p "{discoveries_path}"`

IMPORTANT: Your report should be detailed enough that a coding agent could take any
ADOPT recommendation and understand WHAT to build, WHERE to build it, and HOW to
approach the implementation without needing to re-analyze the OpenClaw source.

## Version Control

You are operating inside the Universal Agent Git repository. After saving the files, you MUST ensure they are tracked. Run the following commands to commit and push your work:

1. `git add "Openclaw Sync Discoveries/"`
2. `git commit -m "docs(openclaw-sync): openclaw sync analysis for {date} [vp-agent]"`
3. `git push origin HEAD`

**(Since you are an automated process, you may need to configure `git config user.name "VP Analysis Agent"` and `git config user.email "vp-agent@clearspringcg.com"` if it complains about identity.)**
"""

    return objective


def _dispatch_via_gateway(
    gateway_url: str,
    auth_token: str,
    objective: str,
    idempotency_key: str,
) -> dict:
    """Dispatch a VP mission via the gateway HTTP API."""
    payload = json.dumps({
        "vp_id": "vp.coder.primary",
        "objective": objective,
        "mission_type": "openclaw-sync",
        "source_session_id": "openclaw-sync-agent",
        "reply_mode": "async",
        "priority": 80,
        "idempotency_key": idempotency_key,
        "execution_mode": "sdk",
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

    gateway_url = _resolve_gateway_url()
    logger.info(f"Using gateway: {gateway_url}")

    # Find the release report
    report_path = _find_release_report()
    if not report_path:
        logger.info("No release report found. Stage 1 may not have run yet. Exiting.")
        sys.exit(0)

    logger.info(f"Loading release report from {report_path}")
    report = json.loads(report_path.read_text(encoding="utf-8"))

    release_count = report.get("new_releases_count", 0)
    if release_count == 0:
        logger.info("✅ Release report shows zero new releases. Nothing to analyze.")
        sys.exit(0)

    logger.info(f"Found {release_count} new releases — building sync analysis objective")

    objective = _build_sync_objective(report)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H-%M")
    idempotency_key = f"openclaw-sync-{today}"

    logger.info(f"Dispatching VP sync mission: objective={len(objective)} chars, key={idempotency_key}")

    # Retry with backoff
    max_retries = 3
    retry_delays = [30, 60, 120]

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"  Dispatch attempt {attempt}/{max_retries}")
            result = _dispatch_via_gateway(gateway_url, auth_token, objective, idempotency_key)
            mission = result.get("mission", {})
            mission_id = mission.get("mission_id", "unknown")
            status = mission.get("status", "unknown")
            logger.info(f"  ✅ Sync mission dispatched: {mission_id} (status={status})")
            sys.exit(0)
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

    logger.error("❌ All dispatch attempts failed")
    sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
