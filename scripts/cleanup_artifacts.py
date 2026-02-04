#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def artifacts_root() -> Path:
    raw = (os.getenv("UA_ARTIFACTS_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (repo_root() / "artifacts").resolve()


def iter_manifests(root: Path) -> Iterable[Path]:
    # Avoid scanning enormous trees accidentally; manifests are small and sparse.
    yield from root.rglob("manifest.json")


def _is_within(root: Path, target: Path) -> bool:
    try:
        target = target.resolve()
        root = root.resolve()
        return str(target).startswith(str(root))
    except Exception:
        return False


def load_manifest(path: Path) -> Dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def retention_deletes(manifest: Dict[str, Any]) -> List[str]:
    """
    Return relative paths marked as retention=temp in manifest.

    Expected shape (PRD):
      retention: { default: "keep", "<relative_path>": "temp", ... }
    """
    retention = manifest.get("retention")
    if not isinstance(retention, dict):
        return []
    out: List[str] = []
    for k, v in retention.items():
        if k == "default":
            continue
        if isinstance(v, str) and v.strip().lower() == "temp":
            if isinstance(k, str) and k.strip():
                out.append(k.strip())
    return out


def delete_paths(
    *,
    root: Path,
    manifest_path: Path,
    rel_paths: List[str],
    dry_run: bool,
) -> Tuple[int, List[str]]:
    """
    Delete rel_paths under manifest dir.
    Returns (deleted_count, messages).
    """
    messages: List[str] = []
    deleted = 0
    base = manifest_path.parent

    for rel in rel_paths:
        target = (base / rel)
        if not _is_within(root, target):
            messages.append(f"SKIP (outside root): {target}")
            continue
        if not target.exists():
            messages.append(f"SKIP (missing): {target}")
            continue
        if dry_run:
            messages.append(f"DRY-RUN delete: {target}")
            continue
        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            deleted += 1
            messages.append(f"Deleted: {target}")
        except Exception as e:
            messages.append(f"ERROR deleting {target}: {e}")

    return deleted, messages


def main() -> int:
    parser = argparse.ArgumentParser(description="Delete artifact files/dirs marked retention=temp in manifests.")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be deleted without deleting.")
    parser.add_argument("--root", default=None, help="Artifacts root override (defaults to UA_ARTIFACTS_DIR or ./artifacts).")
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve() if args.root else artifacts_root()
    if not root.exists():
        print(f"Artifacts root does not exist: {root}")
        return 1

    total_deleted = 0
    total_manifests = 0

    for manifest_path in iter_manifests(root):
        total_manifests += 1
        manifest = load_manifest(manifest_path)
        if not manifest:
            continue
        rels = retention_deletes(manifest)
        if not rels:
            continue
        deleted, msgs = delete_paths(root=root, manifest_path=manifest_path, rel_paths=rels, dry_run=args.dry_run)
        total_deleted += deleted
        for m in msgs:
            print(m)

    print(f"Manifests scanned: {total_manifests}")
    action = "Would delete" if args.dry_run else "Deleted"
    print(f"{action} entries: {total_deleted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
