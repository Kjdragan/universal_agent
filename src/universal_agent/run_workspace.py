from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from typing import Any, Optional

_ATTEMPT_SNAPSHOT_STATUSES = {
    "completed",
    "failed",
    "cancelled",
    "paused",
    "waiting_for_human",
}
_ROOT_EVIDENCE_FILENAMES = (
    "run.log",
    "trace.json",
    "transcript.md",
    "run_checkpoint.json",
    "run_checkpoint.md",
    "session_checkpoint.json",
    "session_checkpoint.md",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def attempt_subdir_name(attempt_number: int) -> str:
    return f"{int(attempt_number):03d}"


def attempt_dir_path(workspace_dir: str | Path, attempt_number: int) -> Path:
    return Path(workspace_dir).resolve() / "attempts" / attempt_subdir_name(attempt_number)


def _sync_attempt_evidence(workspace: Path, attempt_dir: Path) -> dict[str, Any]:
    copied_files: list[str] = []
    copied_dirs: list[str] = []

    for filename in _ROOT_EVIDENCE_FILENAMES:
        source = workspace / filename
        if not source.is_file():
            continue
        destination = attempt_dir / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied_files.append(filename)

    source_work_products = workspace / "work_products"
    if source_work_products.exists():
        destination_work_products = attempt_dir / "work_products"
        shutil.copytree(source_work_products, destination_work_products, dirs_exist_ok=True)
        copied_dirs.append("work_products")

    return {
        "copied_files": copied_files,
        "copied_dirs": copied_dirs,
        "artifact_snapshot_at": _now_iso(),
    }


def ensure_run_workspace_scaffold(
    *,
    workspace_dir: str | Path,
    run_id: str,
    attempt_id: Optional[str] = None,
    attempt_number: Optional[int] = None,
    status: Optional[str] = None,
    run_kind: Optional[str] = None,
    trigger_source: Optional[str] = None,
) -> dict[str, Any]:
    workspace = Path(workspace_dir).resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    manifest_path = workspace / "run_manifest.json"
    activity_path = workspace / "activity.jsonl"
    attempts_root = workspace / "attempts"
    attempts_root.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                manifest = payload
        except Exception:
            manifest = {}

    manifest["run_id"] = run_id
    manifest["workspace_dir"] = str(workspace)
    manifest["updated_at"] = _now_iso()
    if run_kind:
        manifest["run_kind"] = run_kind
    if trigger_source:
        manifest["trigger_source"] = trigger_source
    if status:
        manifest["status"] = status

    created_attempt = False
    attempt_meta_path: Optional[Path] = None
    attempt_dir: Optional[Path] = None
    if attempt_id and attempt_number is not None:
        attempt_dir = attempt_dir_path(workspace, attempt_number)
        attempt_dir.mkdir(parents=True, exist_ok=True)
        attempt_meta_path = attempt_dir / "attempt_meta.json"
        created_attempt = not attempt_meta_path.exists()
        attempt_meta: dict[str, Any] = {
            "run_id": run_id,
            "attempt_id": attempt_id,
            "attempt_number": int(attempt_number),
            "status": status or manifest.get("status") or "unknown",
            "workspace_dir": str(workspace),
            "attempt_dir": str(attempt_dir),
            "updated_at": _now_iso(),
        }
        if attempt_meta_path.exists():
            try:
                payload = json.loads(attempt_meta_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    attempt_meta = {**payload, **attempt_meta}
            except Exception:
                pass
        else:
            attempt_meta["created_at"] = _now_iso()
        normalized_status = str(attempt_meta.get("status") or "").strip().lower()
        if normalized_status in _ATTEMPT_SNAPSHOT_STATUSES:
            attempt_meta.update(_sync_attempt_evidence(workspace, attempt_dir))
            if normalized_status == "completed":
                manifest["canonical_attempt_id"] = attempt_id
                manifest["canonical_attempt_number"] = int(attempt_number)
                manifest["canonical_attempt_status"] = attempt_meta["status"]
            elif not manifest.get("canonical_attempt_id"):
                manifest["canonical_attempt_id"] = attempt_id
                manifest["canonical_attempt_number"] = int(attempt_number)
                manifest["canonical_attempt_status"] = attempt_meta["status"]
        attempt_meta_path.write_text(
            json.dumps(attempt_meta, indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        manifest["latest_attempt_id"] = attempt_id
        manifest["latest_attempt_number"] = int(attempt_number)
        manifest["attempt_count"] = max(int(manifest.get("attempt_count") or 0), int(attempt_number))
        manifest["latest_attempt_status"] = attempt_meta["status"]

    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )

    if created_attempt and attempt_id and attempt_number is not None:
        event = {
            "timestamp": _now_iso(),
            "event": "attempt_created",
            "run_id": run_id,
            "attempt_id": attempt_id,
            "attempt_number": int(attempt_number),
            "status": status or "unknown",
        }
        with activity_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=True) + "\n")

    return {
        "workspace_dir": str(workspace),
        "manifest_path": str(manifest_path),
        "activity_path": str(activity_path),
        "attempt_dir": str(attempt_dir) if attempt_dir else None,
        "attempt_meta_path": str(attempt_meta_path) if attempt_meta_path else None,
        "created_attempt": created_attempt,
    }
