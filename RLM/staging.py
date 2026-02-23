from __future__ import annotations

import re
from pathlib import Path

from .types import CorpusBundle


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()
    return cleaned or "document"


def materialize_markdown_corpus(bundle: CorpusBundle, target_dir: Path) -> Path:
    """
    Convert source corpus documents into normalized markdown files with lightweight frontmatter.

    This guarantees compatibility with ROM-style runners that expect markdown input.
    """
    target_dir.mkdir(parents=True, exist_ok=True)

    for idx, item in enumerate(bundle.documents, start=1):
        source_text = item.path.read_text(encoding="utf-8", errors="ignore")
        title = item.path.stem.replace("_", " ").strip() or item.path.name
        slug = _slugify(item.rel_path)
        out_path = target_dir / f"doc_{idx:04d}_{slug}.md"

        frontmatter = [
            "---",
            f'title: "{title}"',
            f'source_path: "{item.path}"',
            f'rel_path: "{item.rel_path}"',
            f"word_count: {item.word_count}",
            "---",
            "",
        ]
        out_path.write_text("\n".join(frontmatter) + source_text, encoding="utf-8")

    return target_dir
