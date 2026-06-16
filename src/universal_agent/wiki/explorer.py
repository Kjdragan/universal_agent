"""Read-only enumeration + graph extraction for the Wiki Vault Explorer (Spec B).

Backs the ``/api/v1/wiki/vaults*`` dashboard endpoints. Everything here is
read-only and — deliberately — LLM-free: it reuses ``wiki.core`` parsing helpers
but NOT ``_scan_page_records`` (which calls ``generate_summary`` for any page
lacking a summary). ``_light_records`` reads frontmatter only, so opening the
explorer never burns tokens.

Vaults live under TWO roots (there is no single built-in enumerator):
``<shared_memory_workspace>/memory/wiki/*`` and
``<artifacts_dir>/nightly_wikis/*``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from universal_agent.artifacts import resolve_artifacts_dir
from universal_agent.memory.paths import resolve_shared_memory_workspace
from universal_agent.wiki.core import (
    CATEGORY_KIND,
    COMMON_MD_EXCLUDES,
    _extract_wikilinks,
    _frontmatter_and_body,
    _relative,
    _resolve_record_target,
)

VAULT_MANIFEST = "vault_manifest.json"
# Directories that hold operational chrome / snapshots, not graph pages.
_SKIP_PREFIXES = ("raw/", "evidence/", "lint/", "_history/", "assets/")


def _vault_roots() -> list[Path]:
    """Every root a vault can live under. Best-effort; a broken resolver is skipped."""
    roots: list[Path] = []
    try:
        roots.append(Path(resolve_shared_memory_workspace()) / "memory" / "wiki")
    except Exception:
        pass
    try:
        roots.append(Path(resolve_artifacts_dir()) / "nightly_wikis")
    except Exception:
        pass
    return roots


def _is_vault_dir(path: Path) -> bool:
    return path.is_dir() and (path / VAULT_MANIFEST).is_file()


def _iter_vault_dirs() -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for root in _vault_roots():
        if not root.exists():
            continue
        for child in sorted(root.iterdir()):
            if not _is_vault_dir(child):
                continue
            key = str(child.resolve())
            if key not in seen:
                seen.add(key)
                out.append(child)
    return out


def _light_records(vault_path: Path) -> list[dict[str, Any]]:
    """Graph-page records (path/title/kind/body/tags) WITHOUT any LLM summary call."""
    records: list[dict[str, Any]] = []
    for path in sorted(vault_path.rglob("*.md")):
        rel = _relative(path, vault_path)
        if rel in COMMON_MD_EXCLUDES:
            continue
        if any(rel.startswith(prefix) for prefix in _SKIP_PREFIXES):
            continue
        meta, body = _frontmatter_and_body(path)
        category = Path(rel).parts[0]
        records.append(
            {
                "path": rel,
                "title": str(meta.get("title") or path.stem.replace("-", " ").title()),
                "summary": str(meta.get("summary") or "").strip(),
                "kind": str(meta.get("kind") or CATEGORY_KIND.get(category, category.rstrip("s"))),
                "category": category,
                "body": body,
                "tags": list(meta.get("tags") or []),
            }
        )
    return records


def _read_manifest(vault_path: Path) -> dict[str, Any]:
    try:
        payload = json.loads((vault_path / VAULT_MANIFEST).read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


# `ensure_vault` defaults the manifest title to a generic "External Vault" /
# "Internal Vault" (the nightly ingest never passes a topic title), so the vault
# list rendered every vault identically. Derive a meaningful display title instead.
_GENERIC_TITLES = {"", "external vault", "internal vault", "vault"}


def _display_title(manifest: dict[str, Any], vault_path: Path, recs: list[dict[str, Any]]) -> str:
    """A meaningful vault title for the list: the manifest title when it's real,
    else the topic — the (longest, most descriptive) source page title, else a
    titleized slug."""
    raw = str(manifest.get("title") or "").strip()
    if raw.lower() not in _GENERIC_TITLES:
        return raw
    src_titles = [str(r.get("title") or "").strip() for r in recs if r.get("kind") == "source"]
    src_titles = [t for t in src_titles if t]
    if src_titles:
        return max(src_titles, key=len)
    slug = str(manifest.get("vault_slug") or vault_path.name)
    return slug.replace("-", " ").replace("_", " ").strip().title() or slug


def _vault_meta(vault_path: Path, *, records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    manifest = _read_manifest(vault_path)
    recs = records if records is not None else _light_records(vault_path)
    counts: dict[str, int] = {}
    for r in recs:
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1
    return {
        "slug": str(manifest.get("vault_slug") or vault_path.name),
        "title": _display_title(manifest, vault_path, recs),
        "kind": str(manifest.get("vault_kind") or "external"),
        # parent dir name distinguishes nightly_wikis vs memory/wiki at a glance.
        "root": vault_path.parent.name,
        "path": str(vault_path),
        "created_at": str(manifest.get("created_at") or ""),
        "updated_at": str(manifest.get("updated_at") or ""),
        "page_count": len(recs),
        "source_count": counts.get("source", 0),
        "entity_count": counts.get("entity", 0),
        "concept_count": counts.get("concept", 0),
    }


def list_vaults() -> list[dict[str, Any]]:
    """All vaults across both roots, newest-updated first."""
    vaults = [_vault_meta(p) for p in _iter_vault_dirs()]
    vaults.sort(key=lambda v: v.get("updated_at") or "", reverse=True)
    return vaults


def resolve_vault_by_slug(slug: str) -> Path | None:
    """Find a vault dir by manifest slug or directory name (newest wins on collision)."""
    slug = str(slug or "").strip()
    if not slug:
        return None
    matches: list[Path] = []
    for p in _iter_vault_dirs():
        manifest = _read_manifest(p)
        if str(manifest.get("vault_slug") or p.name) == slug or p.name == slug:
            matches.append(p)
    if not matches:
        return None
    matches.sort(key=lambda p: (p / VAULT_MANIFEST).stat().st_mtime, reverse=True)
    return matches[0]


def load_vault_graph(vault_path: Path, *, records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Nodes (pages) + edges (resolved wikilinks) for the force-directed graph."""
    recs = records if records is not None else _light_records(vault_path)
    record_paths = {r["path"] for r in recs}
    title_to_path = {r["title"]: r["path"] for r in recs}
    nodes = [{"id": r["path"], "title": r["title"], "kind": r["kind"]} for r in recs]
    edges: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for r in recs:
        for link in _extract_wikilinks(r["body"]):
            target = _resolve_record_target(link, title_to_path, record_paths)
            if not target or target == r["path"]:
                continue
            key = (r["path"], target)
            if key not in seen:
                seen.add(key)
                edges.append({"source": r["path"], "target": target})
    return {"nodes": nodes, "edges": edges}


def vault_detail(slug: str) -> dict[str, Any] | None:
    vault_path = resolve_vault_by_slug(slug)
    if vault_path is None:
        return None
    recs = _light_records(vault_path)
    graph = load_vault_graph(vault_path, records=recs)
    pages = [{"path": r["path"], "title": r["title"], "kind": r["kind"]} for r in recs]
    pages.sort(key=lambda p: (p["kind"], p["title"].lower()))
    return {"vault": _vault_meta(vault_path, records=recs), "graph": graph, "pages": pages}


def read_vault_page(slug: str, rel_path: str) -> dict[str, Any] | None:
    """Read one page's markdown + backlinks. Path is sanitized against traversal."""
    vault_path = resolve_vault_by_slug(slug)
    if vault_path is None:
        return None
    target = (vault_path / str(rel_path or "")).resolve()
    try:
        target.relative_to(vault_path.resolve())
    except ValueError:
        return None  # traversal attempt — outside the vault
    if not target.is_file() or target.suffix != ".md":
        return None
    meta, body = _frontmatter_and_body(target)
    rel = _relative(target, vault_path)

    recs = _light_records(vault_path)
    record_paths = {r["path"] for r in recs}
    title_to_path = {r["title"]: r["path"] for r in recs}
    backlinks: list[dict[str, str]] = []
    for r in recs:
        if r["path"] == rel:
            continue
        for link in _extract_wikilinks(r["body"]):
            if _resolve_record_target(link, title_to_path, record_paths) == rel:
                backlinks.append({"path": r["path"], "title": r["title"], "kind": r["kind"]})
                break
    return {
        "path": rel,
        "title": str(meta.get("title") or target.stem),
        "kind": str(meta.get("kind") or ""),
        "summary": str(meta.get("summary") or ""),
        "tags": list(meta.get("tags") or []),
        "content": body,
        "backlinks": backlinks,
    }
