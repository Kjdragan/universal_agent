"""Daily Phase 0 sweep: detect drift in Anthropic-adjacent packages.

Runs `uv pip list --outdated --format json`, `npm outdated --json`, and
`claude --version`, then writes infrastructure pages into the configured
vault. Pure observation — does not edit any manifest or trigger an
upgrade. The actuator is PR 6b.

Exit code 0 = sweep completed (drift may or may not exist).
Exit code 2 = subprocess invocation failed in a way that breaks the report.

See docs/proactive_signals/claudedevs_intel_v2_design.md §5.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess as sp
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from universal_agent.artifacts import resolve_artifacts_dir
from universal_agent.infisical_loader import initialize_runtime_secrets
from universal_agent.services.dependency_currency import (
    assemble_sweep_report,
    write_sweep_artifacts,
)
from universal_agent.services.intel_lanes import CLAUDE_CODE_LANE_KEY, get_lane

logger = logging.getLogger(__name__)


def _vault_path_for_lane(lane_slug: str) -> Path:
    """Resolve the vault dir for a lane slug.

    Mirrors the convention used by claude_code_intel_replay:
      <UA_ARTIFACTS_DIR>/knowledge-vaults/<vault_slug>/
    """
    lane = get_lane(lane_slug)
    return resolve_artifacts_dir() / "knowledge-vaults" / lane.vault_slug


def _run(cmd: list[str], *, timeout: int = 120) -> tuple[int, str, str]:
    """Run a subprocess; never raise. Return (returncode, stdout, stderr)."""
    try:
        completed = sp.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return completed.returncode, completed.stdout or "", completed.stderr or ""
    except FileNotFoundError as exc:
        return 127, "", f"binary_not_found: {exc}"
    except sp.TimeoutExpired:
        return 124, "", "timeout"
    except Exception as exc:
        return 1, "", f"unexpected_error: {exc}"


def collect_uv_outdated() -> str:
    """Return raw JSON from `uv pip list --outdated --format json` (or empty string)."""
    if shutil.which("uv") is None:
        logger.warning("uv binary not on PATH; skipping pypi sweep")
        return ""
    rc, out, err = _run(["uv", "pip", "list", "--outdated", "--format", "json"], timeout=180)
    if rc != 0:
        logger.warning("uv pip list --outdated failed (rc=%s): %s", rc, err.strip()[:400])
        return ""
    return out


def collect_npm_outdated(npm_cwd: Path | None = None) -> str:
    """Return raw JSON from `npm outdated --json` for the configured directory."""
    if shutil.which("npm") is None:
        return ""
    cwd = npm_cwd or Path.cwd()
    rc, out, err = _run(
        ["npm", "outdated", "--json", "--prefix", str(cwd)],
        timeout=180,
    )
    # `npm outdated` exits with rc=1 when drift exists. Treat 0 and 1 as "ok".
    if rc not in (0, 1):
        logger.warning("npm outdated failed (rc=%s): %s", rc, err.strip()[:400])
        return ""
    return out


def collect_claude_version() -> str:
    """Return raw stdout from `claude --version`."""
    if shutil.which("claude") is None:
        return ""
    rc, out, _ = _run(["claude", "--version"], timeout=20)
    if rc != 0:
        return ""
    return out


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lane",
        default=CLAUDE_CODE_LANE_KEY,
        help="Lane slug whose vault should receive the report. Defaults to claude-code-intelligence.",
    )
    parser.add_argument(
        "--npm-cwd",
        default="",
        help="Working directory for `npm outdated`. Defaults to the current directory.",
    )
    parser.add_argument(
        "--profile",
        default="",
        help="Deployment profile for Infisical secret loading.",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Print the report JSON to stdout but do not write vault pages.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    initialize_runtime_secrets(profile=args.profile or None)

    started_at = datetime.now(timezone.utc).isoformat()
    uv_json = collect_uv_outdated()
    npm_cwd = Path(args.npm_cwd) if args.npm_cwd else None
    npm_json = collect_npm_outdated(npm_cwd=npm_cwd)
    claude_text = collect_claude_version()

    report = assemble_sweep_report(
        uv_outdated_json=uv_json,
        npm_outdated_json=npm_json,
        claude_version_stdout=claude_text,
        sweep_started_at=started_at,
    )

    payload: dict[str, Any] = report.to_dict()

    if args.no_write:
        print(json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True))
        return 0

    vault_path = _vault_path_for_lane(args.lane)
    vault_path.mkdir(parents=True, exist_ok=True)
    write_paths = write_sweep_artifacts(report, vault_path=vault_path)
    payload["vault_paths"] = write_paths

    print(json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
