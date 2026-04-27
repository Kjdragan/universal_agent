"""Archive heartbeat artifacts from historically polluted Claude Code Intel cron workspaces."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from universal_agent.services.claude_code_intel_cleanup import (
    cleanup_historical_cron_workspace,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clean up historical Claude Code Intel cron workspace pollution.")
    parser.add_argument("--workspace-dir", required=True, help="Workspace directory to inspect/clean.")
    parser.add_argument("--apply", action="store_true", help="Apply the cleanup. Default is dry-run.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    result = cleanup_historical_cron_workspace(
        workspace_dir=Path(args.workspace_dir),
        dry_run=not args.apply,
    )
    print(
        json.dumps(
            {
                "workspace_dir": result.workspace_dir,
                "polluted": result.polluted,
                "dry_run": result.dry_run,
                "archived_paths": result.archived_paths,
                "missing_paths": result.missing_paths,
                "cleanup_dir": result.cleanup_dir,
                "cleanup_manifest_path": result.cleanup_manifest_path,
                "note_path": result.note_path,
            },
            indent=2,
            ensure_ascii=True,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
