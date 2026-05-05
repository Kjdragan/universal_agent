"""CLI for the Phase 0 dependency upgrade actuator.

Bumps a single Anthropic-adjacent dep, runs both smokes, emails the result,
and writes a failure record into the vault when needed. Performs no git
operations — the bumped pyproject.toml is left in the working tree for the
operator to commit and ship.

Usage:
    PYTHONPATH=src uv run python -m universal_agent.scripts.dependency_upgrade \\
        --package claude-agent-sdk \\
        --target-version 0.5.1 \\
        --email-to kevinjdragan@gmail.com

Exit codes:
    0 — both smokes passed, bump is ready to ship
    1 — at least one smoke failed (rolled back); email was sent on the way out
    2 — programmer error (package not in pyproject, etc.); no email sent
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from universal_agent.artifacts import resolve_artifacts_dir
from universal_agent.infisical_loader import initialize_runtime_secrets
from universal_agent.services.agentmail_service import AgentMailService
from universal_agent.services.dependency_upgrade import (
    apply_upgrade,
    build_upgrade_email,
    write_upgrade_failure_record,
)
from universal_agent.services.intel_lanes import CLAUDE_CODE_LANE_KEY, get_lane

logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True, help="Package name as it appears in pyproject.toml dependencies.")
    parser.add_argument("--target-version", required=True, help="Version to pin (lower bound preserved as comparator).")
    parser.add_argument("--lane", default=CLAUDE_CODE_LANE_KEY, help="Lane slug whose vault should receive failure records.")
    parser.add_argument("--email-to", default="", help="Recipient. Defaults to env UA_DEPENDENCY_UPGRADE_EMAIL_TO or kevinjdragan@gmail.com.")
    parser.add_argument("--no-email", action="store_true", help="Skip the email send (still writes the failure record).")
    parser.add_argument("--profile", default="", help="Deployment profile for Infisical secret loading.")
    parser.add_argument("--dry-run", action="store_true", help="Compute the bump but do not apply it. Prints what would change.")
    return parser.parse_args()


def _resolved_email_target(args: argparse.Namespace) -> str:
    explicit = str(args.email_to or "").strip()
    if explicit:
        return explicit
    env_target = str(os.getenv("UA_DEPENDENCY_UPGRADE_EMAIL_TO") or "").strip()
    if env_target:
        return env_target
    return "kevinjdragan@gmail.com"


async def _send_email(*, recipient: str, outcome) -> dict[str, object]:
    subject, text, html = build_upgrade_email(outcome)
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
    return dict(result or {})


def _vault_path_for_lane(lane_slug: str) -> Path:
    lane = get_lane(lane_slug)
    return resolve_artifacts_dir() / "knowledge-vaults" / lane.vault_slug


def _dry_run(args: argparse.Namespace) -> int:
    """Print what the bump would do without touching the working tree."""
    from universal_agent.services.dependency_upgrade import (
        _ua_repo_root,
        find_pyproject_dep,
    )

    pyproject = _ua_repo_root() / "pyproject.toml"
    if not pyproject.exists():
        print(json.dumps({"ok": False, "reason": "pyproject_missing", "path": str(pyproject)}, indent=2), file=sys.stderr)
        return 2
    text = pyproject.read_text(encoding="utf-8")
    found = find_pyproject_dep(text, args.package)
    if not found:
        print(json.dumps({"ok": False, "reason": "package_not_found", "package": args.package}, indent=2), file=sys.stderr)
        return 2
    current_spec, current_version = found
    print(
        json.dumps(
            {
                "ok": True,
                "dry_run": True,
                "package": args.package,
                "current_spec": current_spec,
                "current_version": current_version,
                "target_version": args.target_version,
                "would_change": current_version != args.target_version,
            },
            indent=2,
        )
    )
    return 0


async def main() -> int:
    args = _parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    initialize_runtime_secrets(profile=args.profile or None)

    if args.dry_run:
        return _dry_run(args)

    try:
        outcome = apply_upgrade(
            package=args.package,
            target_version=args.target_version,
        )
    except FileNotFoundError as exc:
        print(json.dumps({"ok": False, "reason": "pyproject_missing", "error": str(exc)}, indent=2), file=sys.stderr)
        return 2
    except KeyError as exc:
        print(json.dumps({"ok": False, "reason": "package_not_found", "package": args.package, "error": str(exc)}, indent=2), file=sys.stderr)
        return 2

    payload = outcome.to_dict()

    # Write a failure record into the vault when the upgrade didn't succeed.
    if not outcome.overall_ok:
        try:
            vault_path = _vault_path_for_lane(args.lane)
            vault_path.mkdir(parents=True, exist_ok=True)
            failure_path = write_upgrade_failure_record(outcome, vault_path=vault_path)
            payload["failure_record_path"] = str(failure_path) if failure_path else ""
        except Exception:
            logger.exception("failed to write upgrade failure record")

    # Email Kevin (success or failure — both are operational signal).
    email_result: dict[str, object] = {}
    if not args.no_email:
        recipient = _resolved_email_target(args)
        try:
            email_result = await _send_email(recipient=recipient, outcome=outcome)
            payload["email"] = {"recipient": recipient, "result": email_result}
        except Exception as exc:
            logger.exception("failed to send upgrade email")
            payload["email"] = {"recipient": recipient, "error": str(exc)[:300]}

    print(json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True))
    return 0 if outcome.overall_ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
