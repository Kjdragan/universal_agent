from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .corpus_adapter import build_corpus_bundle


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def stage_session_corpus(session_dir: str | Path, target_root: str | Path = "RLM/corpora") -> dict[str, object]:
    session_path = Path(session_dir).expanduser().resolve()
    if not session_path.exists() or not session_path.is_dir():
        raise FileNotFoundError(f"Session directory not found: {session_path}")

    target_root_path = Path(target_root).expanduser().resolve()
    target_root_path.mkdir(parents=True, exist_ok=True)

    target_dir = target_root_path / session_path.name
    if target_dir.exists() and any(target_dir.iterdir()):
        target_dir = target_root_path / f"{session_path.name}_{_utc_stamp()}"
    target_dir.mkdir(parents=True, exist_ok=True)

    copied_dirs: list[str] = []
    for name in ("search_results", "work_products", "tasks"):
        source = session_path / name
        if not source.exists():
            continue
        shutil.copytree(source, target_dir / name, dirs_exist_ok=True)
        copied_dirs.append(name)

    if not copied_dirs:
        raise ValueError(
            "No expected session subdirectories found. Expected at least one of: "
            "search_results, work_products, tasks"
        )

    bundle = build_corpus_bundle(target_dir)

    manifest = {
        "source_session_dir": str(session_path),
        "target_dir": str(target_dir),
        "copied_subdirs": copied_dirs,
        "document_count": len(bundle.documents),
        "estimated_tokens": bundle.estimated_tokens,
        "total_chars": bundle.total_chars,
        "total_words": bundle.total_words,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }

    manifest_path = target_dir / "rlm_session_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "target_dir": str(target_dir),
        "manifest_json": str(manifest_path),
        "document_count": len(bundle.documents),
        "estimated_tokens": bundle.estimated_tokens,
        "copied_subdirs": copied_dirs,
    }
