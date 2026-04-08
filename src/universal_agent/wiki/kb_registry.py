"""
Knowledge Base Registry

JSON-backed local registry mapping knowledge base slugs to NotebookLM notebook IDs.
"""

import json
from pathlib import Path
from typing import Any

from universal_agent.artifacts import resolve_artifacts_dir
from universal_agent.wiki.core import _now_iso

def get_registry_path() -> Path:
    registry_dir = Path(resolve_artifacts_dir()) / "knowledge-bases"
    registry_dir.mkdir(parents=True, exist_ok=True)
    return registry_dir / "kb_registry.json"

def _load_registry() -> dict[str, Any]:
    path = get_registry_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

def _save_registry(registry: dict[str, Any]) -> None:
    path = get_registry_path()
    path.write_text(json.dumps(registry, indent=2), encoding="utf-8")

def register_kb(slug: str, notebook_id: str, title: str, tags: list[str] | None = None) -> dict[str, Any]:
    registry = _load_registry()
    entry = {
        "notebook_id": notebook_id,
        "title": title,
        "created_at": _now_iso(),
        "source_count": 0,
        "last_queried": None,
        "tags": tags or [],
    }
    # Preserve existing dates if overwriting
    if slug in registry:
        entry["created_at"] = registry[slug].get("created_at", entry["created_at"])
        entry["source_count"] = registry[slug].get("source_count", 0)
        entry["last_queried"] = registry[slug].get("last_queried", None)

    registry[slug] = entry
    _save_registry(registry)
    return entry

def get_kb(slug: str) -> dict[str, Any] | None:
    return _load_registry().get(slug)

def list_kbs() -> list[dict[str, Any]]:
    registry = _load_registry()
    kbs = []
    for slug, data in registry.items():
        kb = {"slug": slug}
        kb.update(data)
        kbs.append(kb)
    return sorted(kbs, key=lambda x: x.get("created_at", ""), reverse=True)

def update_kb(slug: str, **kwargs: Any) -> dict[str, Any]:
    registry = _load_registry()
    if slug not in registry:
        raise ValueError(f"Knowledge base '{slug}' not found in registry.")
    
    registry[slug].update(kwargs)
    _save_registry(registry)
    return registry[slug]

def remove_kb(slug: str) -> bool:
    registry = _load_registry()
    if slug in registry:
        del registry[slug]
        _save_registry(registry)
        return True
    return False
