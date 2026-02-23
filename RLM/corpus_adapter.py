from __future__ import annotations

import re
from pathlib import Path

from .types import CorpusBundle, CorpusDocument

_ALLOWED_SUFFIXES = {".md", ".txt", ".markdown"}


def _estimate_tokens(total_chars: int) -> int:
    # Practical approximation for planning/gating.
    return max(1, total_chars // 4)


def _task_candidates(workspace: Path, task_name: str) -> list[Path]:
    return [
        workspace / "tasks" / task_name / "filtered_corpus",
        workspace / "tasks" / task_name / "refined_corpus.md",
    ]


def resolve_source(source: str | None, workspace: str | None, task_name: str | None) -> Path:
    if source:
        source_path = Path(source).expanduser().resolve()
        if source_path.exists():
            return source_path
        raise FileNotFoundError(f"Source path not found: {source_path}")

    if workspace and task_name:
        ws = Path(workspace).expanduser().resolve()
        if not ws.exists():
            raise FileNotFoundError(f"Workspace not found: {ws}")
        for candidate in _task_candidates(ws, task_name):
            if candidate.exists():
                return candidate.resolve()
        raise FileNotFoundError(
            "No task corpus found. Expected one of: "
            + ", ".join(str(path) for path in _task_candidates(ws, task_name))
        )

    raise ValueError("Provide either --source, or --workspace with --task-name")


def _list_docs_from_dir(source_path: Path) -> list[Path]:
    docs: list[Path] = []
    for path in sorted(source_path.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in _ALLOWED_SUFFIXES:
            continue
        docs.append(path)
    return docs


def _safe_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _to_doc(path: Path, root: Path) -> CorpusDocument:
    text = _safe_text(path)
    words = re.findall(r"\S+", text)
    return CorpusDocument(
        path=path,
        rel_path=str(path.relative_to(root)) if path != root else path.name,
        char_count=len(text),
        word_count=len(words),
    )


def build_corpus_bundle(source_path: Path) -> CorpusBundle:
    source_path = source_path.resolve()
    if source_path.is_file():
        docs = [_to_doc(source_path, source_path.parent)]
    elif source_path.is_dir():
        paths = _list_docs_from_dir(source_path)
        if not paths:
            raise ValueError(f"No supported documents found under: {source_path}")
        docs = [_to_doc(path, source_path) for path in paths]
    else:
        raise ValueError(f"Unsupported source path type: {source_path}")

    total_chars = sum(item.char_count for item in docs)
    total_words = sum(item.word_count for item in docs)

    return CorpusBundle(
        source_path=source_path,
        documents=docs,
        total_chars=total_chars,
        total_words=total_words,
        estimated_tokens=_estimate_tokens(total_chars),
    )
