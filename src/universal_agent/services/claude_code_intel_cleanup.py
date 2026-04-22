"""Cleanup helpers for historically polluted Claude Code Intel cron workspaces."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_HEARTBEAT_ARTIFACT_REL_PATHS = (
    "heartbeat_state.json",
    "work_products/heartbeat_state.json",
    "work_products/heartbeat_findings_latest.json",
    "work_products/system_health_latest.md",
)


@dataclass(frozen=True)
class ClaudeCodeIntelCleanupResult:
    workspace_dir: str
    polluted: bool
    dry_run: bool
    archived_paths: list[str]
    missing_paths: list[str]
    cleanup_dir: str
    cleanup_manifest_path: str
    note_path: str


def cleanup_historical_cron_workspace(
    *,
    workspace_dir: Path,
    dry_run: bool = True,
) -> ClaudeCodeIntelCleanupResult:
    workspace = workspace_dir.expanduser().resolve()
    cleanup_dir = workspace / "archive" / f"claude_code_intel_cleanup_{_timestamp_slug()}"
    archived_paths: list[str] = []
    missing_paths: list[str] = []

    for rel_path in _HEARTBEAT_ARTIFACT_REL_PATHS:
        target = workspace / rel_path
        if not target.exists():
            missing_paths.append(rel_path)
            continue
        archived_paths.append(rel_path)
        if not dry_run:
            destination = cleanup_dir / rel_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(target), str(destination))

    polluted = bool(archived_paths)
    note_path = cleanup_dir / "README.md"
    manifest_path = cleanup_dir / "cleanup_manifest.json"
    if not dry_run and polluted:
        cleanup_dir.mkdir(parents=True, exist_ok=True)
        note_path.write_text(_cleanup_note(workspace, archived_paths, missing_paths), encoding="utf-8")
        manifest_path.write_text(
            json.dumps(
                {
                    "workspace_dir": str(workspace),
                    "cleaned_at": _now_iso(),
                    "archived_paths": archived_paths,
                    "missing_paths": missing_paths,
                    "warning": (
                        "Only clearly heartbeat-specific artifacts were moved. "
                        "Mixed transcript.md / trace.json / run.log remain in place for forensic integrity."
                    ),
                },
                indent=2,
                ensure_ascii=True,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    return ClaudeCodeIntelCleanupResult(
        workspace_dir=str(workspace),
        polluted=polluted,
        dry_run=dry_run,
        archived_paths=archived_paths,
        missing_paths=missing_paths,
        cleanup_dir=str(cleanup_dir),
        cleanup_manifest_path=str(manifest_path),
        note_path=str(note_path),
    )


def _cleanup_note(workspace: Path, archived_paths: list[str], missing_paths: list[str]) -> str:
    lines = [
        "# Claude Code Intel Historical Workspace Cleanup",
        "",
        f"- Workspace: `{workspace}`",
        f"- Cleaned at: `{_now_iso()}`",
        "",
        "The following files were archived because they are heartbeat follow-up artifacts that polluted the original Claude Code Intel cron workspace:",
        "",
    ]
    for rel_path in archived_paths:
        lines.append(f"- `{rel_path}`")
    if missing_paths:
        lines.extend(["", "Expected but already absent:", ""])
        for rel_path in missing_paths:
            lines.append(f"- `{rel_path}`")
    lines.extend(
        [
            "",
            "Important:",
            "",
            "- `transcript.md`, `trace.json`, and `run.log` were not rewritten or modified.",
            "- Those files may still contain historical mixed cron + heartbeat evidence and should be interpreted with that context.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
