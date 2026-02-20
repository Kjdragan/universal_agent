#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import tarfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from universal_agent.memory.orchestrator import get_memory_orchestrator
from universal_agent.memory.paths import resolve_shared_memory_workspace


@dataclass
class MigrationStats:
    scanned_session_roots: int = 0
    scanned_memory_files: int = 0
    scanned_transcripts: int = 0
    inserted_long_term: int = 0
    skipped_long_term: int = 0
    indexed_sessions: int = 0
    skipped_sessions: int = 0
    cleanup_candidates: int = 0
    cleanup_deleted: int = 0


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _session_roots(workspaces_root: Path) -> list[Path]:
    if not workspaces_root.exists():
        return []
    roots: list[Path] = []
    for path in workspaces_root.iterdir():
        if not path.is_dir():
            continue
        name = path.name.lower()
        if name.startswith(("session_", "tg_", "api_", "cron_", "vp_", "session_hook_")):
            roots.append(path)
    roots.sort(key=lambda p: p.name)
    return roots


def _collect_memory_files(session_root: Path) -> list[Path]:
    files: list[Path] = []
    top_memory = session_root / "MEMORY.md"
    if top_memory.exists():
        files.append(top_memory)
    mem_dir = session_root / "memory"
    if mem_dir.exists():
        for path in mem_dir.rglob("*.md"):
            if path.is_file():
                files.append(path)
    return files


def _collect_cleanup_targets(session_root: Path) -> list[Path]:
    targets: list[Path] = []
    for rel in ("MEMORY.md", "memory", "Memory_System_Data", "agent_core.db", "chroma_db"):
        path = session_root / rel
        if path.exists():
            targets.append(path)
    # nested accidental paths
    for nested in session_root.rglob("Memory_System_Data"):
        if nested.is_dir():
            targets.append(nested)
    deduped: list[Path] = []
    seen: set[str] = set()
    for item in targets:
        resolved = str(item.resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped.append(item)
    return deduped


def _archive_paths(paths: list[Path], archive_path: Path) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "w:gz") as tar:
        for path in paths:
            if not path.exists():
                continue
            arcname = str(path.resolve().relative_to(REPO_ROOT.resolve())) if str(path.resolve()).startswith(str(REPO_ROOT.resolve())) else path.name
            tar.add(path, arcname=arcname)


def _delete_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        for child in sorted(path.iterdir(), reverse=True):
            _delete_path(child)
        path.rmdir()
        return
    path.unlink(missing_ok=True)


def run(args: argparse.Namespace) -> dict[str, Any]:
    workspaces_root = Path(args.workspaces_root).resolve()
    shared_override = (args.shared_root or "").strip()
    if shared_override:
        override_path = Path(shared_override).expanduser()
        if not override_path.is_absolute():
            override_path = (REPO_ROOT / override_path).resolve()
        shared_root = override_path.resolve()
        shared_root.mkdir(parents=True, exist_ok=True)
    else:
        shared_root = Path(resolve_shared_memory_workspace()).resolve()
    broker = get_memory_orchestrator(workspace_dir=str(shared_root))

    stats = MigrationStats()
    session_roots = _session_roots(workspaces_root)
    stats.scanned_session_roots = len(session_roots)

    cleanup_targets: list[Path] = []
    memory_file_report: list[dict[str, Any]] = []
    transcript_report: list[dict[str, Any]] = []

    for session_root in session_roots:
        session_id = session_root.name
        memory_files = _collect_memory_files(session_root)
        transcript_path = session_root / "transcript.md"
        cleanup_targets.extend(_collect_cleanup_targets(session_root))

        for memory_file in memory_files:
            stats.scanned_memory_files += 1
            try:
                text = memory_file.read_text(encoding="utf-8", errors="replace").strip()
            except Exception as exc:
                memory_file_report.append(
                    {"path": str(memory_file), "session_id": session_id, "status": "error", "error": str(exc)}
                )
                continue
            if not text:
                stats.skipped_long_term += 1
                memory_file_report.append(
                    {"path": str(memory_file), "session_id": session_id, "status": "skipped", "reason": "empty"}
                )
                continue
            if args.dry_run:
                stats.inserted_long_term += 1
                memory_file_report.append(
                    {"path": str(memory_file), "session_id": session_id, "status": "would_insert"}
                )
                continue
            entry = broker.write(
                content=text,
                source="hard_cut_migration",
                session_id=session_id,
                tags=["migration", "long_term"],
                memory_class="long_term",
                importance=1.0,
            )
            if entry is None:
                stats.skipped_long_term += 1
                memory_file_report.append(
                    {"path": str(memory_file), "session_id": session_id, "status": "skipped", "reason": "dedupe_or_policy"}
                )
            else:
                stats.inserted_long_term += 1
                memory_file_report.append(
                    {"path": str(memory_file), "session_id": session_id, "status": "inserted"}
                )

        if transcript_path.exists():
            stats.scanned_transcripts += 1
            if args.dry_run:
                stats.indexed_sessions += 1
                transcript_report.append(
                    {"path": str(transcript_path), "session_id": session_id, "status": "would_index"}
                )
            else:
                result = broker.sync_session(
                    session_id=session_id,
                    transcript_path=str(transcript_path),
                    force=True,
                )
                if bool(result.get("indexed")):
                    stats.indexed_sessions += 1
                    status = "indexed"
                else:
                    stats.skipped_sessions += 1
                    status = "skipped"
                transcript_report.append(
                    {
                        "path": str(transcript_path),
                        "session_id": session_id,
                        "status": status,
                        "result": result,
                    }
                )

    # cleanup target dedupe and guard shared root
    deduped_targets: list[Path] = []
    seen_targets: set[str] = set()
    for target in cleanup_targets:
        resolved = str(target.resolve())
        if resolved in seen_targets:
            continue
        if resolved.startswith(str(shared_root)):
            continue
        seen_targets.add(resolved)
        deduped_targets.append(target)
    stats.cleanup_candidates = len(deduped_targets)

    archive_path = Path(args.archive_dir).resolve() / f"memory_hard_cut_archive_{_now_utc()}.tar.gz"
    if deduped_targets and not args.dry_run:
        _archive_paths(deduped_targets, archive_path)

    if deduped_targets and not args.dry_run and args.delete_legacy:
        for target in deduped_targets:
            if not target.exists():
                continue
            _delete_path(target)
            stats.cleanup_deleted += 1

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dry_run": bool(args.dry_run),
        "delete_legacy": bool(args.delete_legacy),
        "workspaces_root": str(workspaces_root),
        "shared_memory_root": str(shared_root),
        "archive_path": str(archive_path) if (not args.dry_run and deduped_targets) else None,
        "stats": asdict(stats),
        "memory_files": memory_file_report,
        "transcripts": transcript_report,
        "cleanup_targets": [str(path) for path in deduped_targets],
    }
    report_path = Path(args.report_json).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding="utf-8")
    report["report_json"] = str(report_path)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Hard-cut memory migration: backfill session/legacy memory into canonical shared memory.",
    )
    parser.add_argument(
        "--workspaces-root",
        default=str(REPO_ROOT / "AGENT_RUN_WORKSPACES"),
        help="Session workspaces root to scan.",
    )
    parser.add_argument(
        "--shared-root",
        default="",
        help="Shared canonical memory workspace override (defaults via resolve_shared_memory_workspace).",
    )
    parser.add_argument(
        "--archive-dir",
        default=str(REPO_ROOT / "Memory_System" / "archives"),
        help="Archive directory for one-time legacy snapshot tarball.",
    )
    parser.add_argument(
        "--report-json",
        default=str(REPO_ROOT / "tmp" / "memory_hard_cut_migration_report.json"),
        help="Report JSON output path.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview only; do not mutate files.")
    parser.add_argument(
        "--delete-legacy",
        action="store_true",
        default=False,
        help="After archival, delete legacy paths from session workspaces.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = run(args)
    stats = report.get("stats", {})
    print(
        json.dumps(
            {
                "dry_run": report.get("dry_run"),
                "report_json": report.get("report_json"),
                "inserted_long_term": stats.get("inserted_long_term", 0),
                "indexed_sessions": stats.get("indexed_sessions", 0),
                "cleanup_candidates": stats.get("cleanup_candidates", 0),
                "cleanup_deleted": stats.get("cleanup_deleted", 0),
            },
            indent=2,
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
