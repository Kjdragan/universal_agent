from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from universal_agent.run_catalog import RunCatalogService

_WORKSPACE_PREFIXES = (
    "run_",
    "session_",
    "session-hook_",
    "session_hook_",
    "tg_",
    "api_",
    "vp_",
    "daemon_",
    "cron_",
)

_WORKSPACE_MARKERS = (
    "run.log",
    "trace.json",
    "session_checkpoint.json",
    "sync_ready.json",
    "run_manifest.json",
    "run_checkpoint.json",
    "activity.jsonl",
)


def looks_like_agent_workspace(workspace_dir: Path) -> bool:
    workspace_id = workspace_dir.name.lower()
    if workspace_id.startswith(_WORKSPACE_PREFIXES):
        return True
    return any((workspace_dir / marker_name).exists() for marker_name in _WORKSPACE_MARKERS)


def list_workspace_summaries(
    workspaces_root: Path,
    *,
    limit: int = 100,
    run_catalog: Optional[RunCatalogService] = None,
) -> list[dict[str, Any]]:
    root = Path(workspaces_root).resolve()
    if not root.exists() or not root.is_dir():
        return []

    catalog = run_catalog or RunCatalogService()
    runs = catalog.list_runs_for_workspace_prefix(root, limit=max(limit * 10, 500))
    runs_by_workspace = {
        str(Path(str(run["workspace_dir"])).resolve()): run
        for run in runs
        if str(run.get("workspace_dir") or "").strip()
    }

    def _summary_for_workspace(workspace_dir: Path) -> Optional[dict[str, Any]]:
        if not workspace_dir.is_dir():
            return None
        resolved = str(workspace_dir.resolve())
        run = runs_by_workspace.get(resolved)
        if run is None and not looks_like_agent_workspace(workspace_dir):
            return None

        trace_file = workspace_dir / "trace.json"
        status = str((run or {}).get("status") or ("complete" if trace_file.exists() else "incomplete"))
        mtime = workspace_dir.stat().st_mtime
        payload = {
            "session_id": workspace_dir.name,
            "workspace_path": resolved,
            "workspace_dir": resolved,
            "status": status,
            "timestamp": mtime,
            "last_modified": mtime,
        }
        if run:
            payload.update(
                {
                    "run_id": run.get("run_id"),
                    "run_status": run.get("status"),
                    "run_kind": run.get("run_kind"),
                    "trigger_source": run.get("trigger_source"),
                    "attempt_count": run.get("attempt_count"),
                    "latest_attempt_id": run.get("latest_attempt_id"),
                    "last_success_attempt_id": run.get("last_success_attempt_id"),
                    "canonical_attempt_id": run.get("canonical_attempt_id"),
                    "provider_session_id": run.get("provider_session_id"),
                    "external_origin": run.get("external_origin"),
                    "external_origin_id": run.get("external_origin_id"),
                    "external_correlation_id": run.get("external_correlation_id"),
                    "created_at": run.get("created_at"),
                    "updated_at": run.get("updated_at"),
                }
            )
        return payload

    summaries: list[dict[str, Any]] = []
    for workspace_dir in sorted(root.iterdir(), key=lambda path: path.stat().st_mtime, reverse=True):
        summary = _summary_for_workspace(workspace_dir)
        if summary is not None:
            summaries.append(summary)
        if len(summaries) >= limit:
            break
    return summaries
