from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from universal_agent.artifacts import resolve_artifacts_dir
from universal_agent.memory.paths import resolve_shared_memory_workspace
from universal_agent.workspace_catalog import list_workspace_summaries

REQUIRED_FRONTMATTER_FIELDS = (
    "title",
    "kind",
    "updated_at",
    "tags",
    "source_ids",
    "provenance_kind",
    "provenance_refs",
    "confidence",
    "status",
)

COMMON_FILES = {"AGENTS.md", "index.md", "log.md", "overview.md", "vault_manifest.json"}
COMMON_MD_EXCLUDES = {"AGENTS.md", "index.md", "log.md", "overview.md"}
SYNC_STATE_FILENAME = "sync_state.json"
SYNC_PROGRESS_FILENAME = "sync_progress.json"
SYNC_PROGRESS_MARKDOWN_FILENAME = "sync_progress.md"
EXTERNAL_DIRS = ("raw", "sources", "entities", "concepts", "analyses", "assets", "lint")
INTERNAL_DIRS = (
    "evidence/memory",
    "evidence/sessions",
    "evidence/checkpoints",
    "decisions",
    "preferences",
    "incidents",
    "projects",
    "threads",
    "analyses",
    "lint",
)
STOPWORDS = {
    "about",
    "after",
    "again",
    "agent",
    "against",
    "because",
    "before",
    "being",
    "between",
    "concept",
    "could",
    "document",
    "external",
    "first",
    "their",
    "there",
    "these",
    "those",
    "through",
    "under",
    "using",
    "vault",
    "where",
    "which",
    "would",
    "wiki",
    "raw",
    "source",
    "sources",
    "immutable",
    "friendly",
    "optional",
    "derivative",
    "maintain",
    "maintains",
    "maintained",
    "pages",
    "page",
    "markdown",
    "persistent",
    "canonical",
    "query",
    "queries",
    "index",
    "memory",
    "knowledge",
    "system",
    "systems",
    "project",
    "projects",
    "universal",
}
ENTITY_STOPWORDS = {
    "The",
    "This",
    "That",
    "These",
    "Those",
    "When",
    "Most",
    "ChatGPT",
    "NotebookLM",
    "Agents",
    "Agent",
    "Summary",
    "Source",
    "Sources",
}
ALLOWED_SINGLE_WORD_ENTITIES = {"Obsidian", "NotebookLM", "ChatGPT", "GitHub", "Slack", "Telegram", "Todoist", "Codex", "OpenAI"}
CONTRADICTION_MARKERS = ("contradict", "conflict", "however", "in contrast", "but ")
MAX_INTERNAL_MEMORY_FILES = 12
MAX_INTERNAL_SESSION_FILES = 12
MAX_INTERNAL_CHECKPOINTS = 12
MAX_EVIDENCE_TEXT_CHARS = 20000
CATEGORY_KIND = {
    "sources": "source",
    "entities": "entity",
    "concepts": "concept",
    "analyses": "analysis",
    "decisions": "decision",
    "preferences": "preference",
    "incidents": "incident",
    "projects": "project",
    "threads": "thread",
}
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VaultContext:
    kind: str
    slug: str
    title: str
    path: Path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _slugify(value: str, fallback: str = "vault") -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return text or fallback


def _titleize_slug(value: str) -> str:
    return " ".join(part.capitalize() for part in value.replace("-", " ").split()).strip() or "Vault"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _fingerprint_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _frontmatter_and_body(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_text(encoding="utf-8")
    if raw.startswith("---\n"):
        parts = raw.split("---\n", 2)
        if len(parts) >= 3:
            try:
                meta = yaml.safe_load(parts[1]) or {}
            except Exception:
                meta = {}
            if not isinstance(meta, dict):
                meta = {}
            return meta, parts[2].lstrip("\n")
    return {}, raw


def _dump_markdown(meta: dict[str, Any], body: str) -> str:
    return f"---\n{yaml.safe_dump(meta, sort_keys=False, allow_unicode=False).strip()}\n---\n\n{body.rstrip()}\n"


def _extract_summary(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        return stripped[:180]
    return ""


def _read_text_limited(path: Path, *, max_chars: int = MAX_EVIDENCE_TEXT_CHARS) -> str:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        content = handle.read(max_chars + 1)
    if len(content) > max_chars:
        content = content[:max_chars].rstrip() + "\n\n[Truncated]\n"
    return content


def _relative(path: Path, root: Path) -> str:
    return str(path.relative_to(root)).replace(os.sep, "/")


def resolve_vault_path(vault_kind: str, vault_slug: str, *, root_override: str | None = None) -> Path:
    kind = str(vault_kind or "").strip().lower()
    slug = _slugify(vault_slug, fallback="default")
    if root_override:
        base = Path(root_override).expanduser().resolve()
        return base / slug if kind == "external" else base
    if kind == "external":
        root = os.getenv("UA_LLM_WIKI_ROOT", "").strip()
        if root:
            return Path(root).expanduser().resolve() / slug
        return Path(resolve_artifacts_dir()) / "knowledge-vaults" / slug
    if kind == "internal":
        return Path(resolve_shared_memory_workspace()) / "memory" / "wiki"
    raise ValueError(f"Unsupported vault kind: {vault_kind}")


def _vault_schema_text(kind: str, slug: str, title: str) -> str:
    if kind == "internal":
        return (
            f"# {title} Schema\n\n"
            "This vault is a derived operational memory projection.\n\n"
            "Rules:\n"
            "- Evidence in `evidence/` is derived from canonical memory, sessions, checkpoints, and run artifacts.\n"
            "- Pages under `decisions/`, `preferences/`, `incidents/`, `projects/`, and `threads/` are compiled views.\n"
            "- Do not use this vault as the source of truth for resumability or runtime state.\n"
            "- Preserve provenance refs on every managed page.\n"
            "- Keep `index.md`, `log.md`, and `overview.md` current after sync and lint operations.\n"
        )
    return (
        f"# {title} Schema\n\n"
        "This vault is a canonical external knowledge base.\n\n"
        "Rules:\n"
        "- `raw/` holds immutable imported source material.\n"
        "- `sources/`, `entities/`, `concepts/`, and `analyses/` are LLM-maintained pages.\n"
        "- Keep `index.md`, `log.md`, and `overview.md` current after ingest, query filing, and lint.\n"
        "- Preserve provenance refs on every managed page.\n"
        "- Query the wiki first; only fall back to raw sources when necessary.\n"
    )


def _default_page_meta(title: str, kind: str, *, provenance_kind: str, provenance_refs: list[str] | None = None, source_ids: list[str] | None = None, tags: list[str] | None = None, status: str = "active", confidence: str = "medium") -> dict[str, Any]:
    return {
        "title": title,
        "kind": kind,
        "updated_at": _now_iso(),
        "tags": tags or [],
        "source_ids": source_ids or [],
        "provenance_kind": provenance_kind,
        "provenance_refs": provenance_refs or [],
        "confidence": confidence,
        "status": status,
    }


def _write_page(path: Path, meta: dict[str, Any], body: str) -> None:
    full_meta = dict(meta)
    for field in REQUIRED_FRONTMATTER_FIELDS:
        full_meta.setdefault(field, [] if field in {"tags", "source_ids", "provenance_refs"} else "")
    full_meta["updated_at"] = _now_iso()
    _write_text(path, _dump_markdown(full_meta, body))


def ensure_vault(vault_kind: str, vault_slug: str, *, title: str | None = None, root_override: str | None = None) -> VaultContext:
    kind = str(vault_kind or "").strip().lower()
    slug = _slugify(vault_slug, fallback="default")
    resolved_title = str(title or "").strip() or _titleize_slug(slug if kind == "external" else "internal-memory-vault")
    vault_path = resolve_vault_path(kind, slug, root_override=root_override)
    vault_path.mkdir(parents=True, exist_ok=True)
    for rel_dir in (EXTERNAL_DIRS if kind == "external" else INTERNAL_DIRS):
        (vault_path / rel_dir).mkdir(parents=True, exist_ok=True)

    manifest_path = vault_path / "vault_manifest.json"
    manifest = _load_json(manifest_path, {})
    manifest.update(
        {
            "schema_version": 1,
            "vault_kind": kind,
            "vault_slug": slug,
            "title": resolved_title,
            "updated_at": _now_iso(),
        }
    )
    manifest.setdefault("created_at", _now_iso())
    _write_json(manifest_path, manifest)

    if not (vault_path / "AGENTS.md").exists():
        _write_text(vault_path / "AGENTS.md", _vault_schema_text(kind, slug, resolved_title))
    if not (vault_path / "log.md").exists():
        _write_text(vault_path / "log.md", f"# {resolved_title} Log\n")
    if not (vault_path / "index.md").exists():
        _write_text(vault_path / "index.md", f"# {resolved_title} Index\n")
    if not (vault_path / "overview.md").exists():
        overview_meta = _default_page_meta(
            resolved_title,
            "overview",
            provenance_kind="system",
            provenance_refs=["vault_manifest.json"],
            tags=["overview", kind],
        )
        _write_page(vault_path / "overview.md", overview_meta, f"# {resolved_title}\n\nVault initialized.\n")

    update_index(vault_path)
    refresh_overview(vault_path)
    return VaultContext(kind=kind, slug=slug, title=resolved_title, path=vault_path)


def _scan_page_records(vault_path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(vault_path.rglob("*.md")):
        rel = _relative(path, vault_path)
        if rel in COMMON_MD_EXCLUDES:
            continue
        if rel.startswith("raw/"):
            continue
        if rel.startswith("evidence/"):
            continue
        meta, body = _frontmatter_and_body(path)
        category = Path(rel).parts[0]
        summary = str(meta.get("summary") or "").strip() or _extract_summary(body)
        records.append(
            {
                "path": rel,
                "title": str(meta.get("title") or path.stem.replace("-", " ").title()),
                "summary": summary,
                "category": category,
                "kind": str(meta.get("kind") or CATEGORY_KIND.get(category, category.rstrip("s"))),
                "source_ids": list(meta.get("source_ids") or []),
                "provenance_refs": list(meta.get("provenance_refs") or []),
                "tags": list(meta.get("tags") or []),
            }
        )
    return records


def update_index(vault_path: Path) -> Path:
    records = _scan_page_records(vault_path)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[record["category"]].append(record)

    title = _load_json(vault_path / "vault_manifest.json", {}).get("title", vault_path.name)
    lines = [f"# {title} Index", ""]
    for category in sorted(groups):
        lines.append(f"## {category}")
        for record in sorted(groups[category], key=lambda item: item["title"].lower()):
            summary = record["summary"] or "No summary available."
            lines.append(f"- [[{record['path']}|{record['title']}]] - {summary}")
        lines.append("")
    _write_text(vault_path / "index.md", "\n".join(lines).rstrip() + "\n")
    return vault_path / "index.md"


def append_log_entry(vault_path: Path, operation: str, title: str, details: str = "") -> Path:
    log_path = vault_path / "log.md"
    heading = f"## [{datetime.now(timezone.utc).strftime('%Y-%m-%d')}] {operation} | {title}"
    body = f"{heading}\n\n{details.strip()}\n\n" if details.strip() else f"{heading}\n\n"
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(body)
    return log_path


def refresh_overview(vault_path: Path) -> Path:
    manifest = _load_json(vault_path / "vault_manifest.json", {})
    records = _scan_page_records(vault_path)
    counts = Counter(record["category"] for record in records)
    log_path = vault_path / "log.md"
    recent_ops = []
    if log_path.exists():
        recent_ops = [line.strip() for line in log_path.read_text(encoding="utf-8").splitlines() if line.startswith("## [")][-5:]
    lines = [
        f"# {manifest.get('title', vault_path.name)}",
        "",
        f"- Vault kind: `{manifest.get('vault_kind', 'unknown')}`",
        f"- Updated: `{_now_iso()}`",
        f"- Managed pages: `{len(records)}`",
        "",
        "## Category Counts",
        "",
    ]
    for category in sorted(counts):
        lines.append(f"- `{category}`: {counts[category]}")
    lines.extend(["", "## Recent Operations", ""])
    if recent_ops:
        lines.extend(f"- {entry}" for entry in recent_ops)
    else:
        lines.append("- No operations recorded.")

    meta = _default_page_meta(
        manifest.get("title", vault_path.name),
        "overview",
        provenance_kind="system",
        provenance_refs=["vault_manifest.json", "log.md", "index.md"],
        tags=["overview", manifest.get("vault_kind", "vault")],
    )
    _write_page(vault_path / "overview.md", meta, "\n".join(lines))
    return vault_path / "overview.md"


def _copy_external_raw(vault_path: Path, *, source_path: str | None, content: str | None, title: str, source_id: str) -> Path:
    raw_dir = vault_path / "raw"
    if source_path:
        src = Path(source_path).expanduser().resolve()
        suffix = src.suffix or ".txt"
        dest = raw_dir / f"{source_id}{suffix}"
        if not dest.exists():
            shutil.copy2(src, dest)
        return dest
    dest = raw_dir / f"{source_id}.md"
    if not dest.exists():
        _write_text(dest, content or "")
    return dest


def _candidate_lines(text: str) -> list[str]:
    lines = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        if stripped.endswith(":"):
            continue
        if stripped.lower() in {"summary", "source details", "excerpt", "mentioned in", "related entities", "related concepts"}:
            continue
        lines.append(stripped)
    return lines


def _extract_entity_candidates(text: str) -> list[str]:
    matches = []
    for line in _candidate_lines(text):
        matches.extend(re.findall(r"\b[A-Z][a-z]+(?: [A-Z][a-z]+){0,2}\b", line))
    filtered = []
    for match in matches:
        candidate = match.strip()
        if not candidate or candidate in ENTITY_STOPWORDS:
            continue
        if len(candidate) <= 3:
            continue
        parts = candidate.split()
        if len(parts) == 1 and candidate not in ALLOWED_SINGLE_WORD_ENTITIES:
            continue
        filtered.append(candidate)
    counts = Counter(filtered)
    return [item for item, _count in counts.most_common(5)]


def _extract_concept_candidates(text: str) -> list[str]:
    words = []
    for line in _candidate_lines(text):
        words.extend(re.findall(r"\b[a-z][a-z]{5,}\b", line.lower()))
    counts = Counter(word for word in words if word not in STOPWORDS)
    concepts = []
    for item, count in counts.most_common(10):
        if count < 2:
            continue
        concepts.append(item)
        if len(concepts) >= 5:
            break
    return concepts


def _upsert_reference_page(vault_path: Path, category: str, name: str, *, source_title: str, source_page_rel: str, source_id: str) -> str:
    kind = CATEGORY_KIND.get(category, category.rstrip("s"))
    slug = _slugify(name, fallback=kind)
    page_path = vault_path / category / f"{slug}.md"
    link_line = f"- [[{source_page_rel}|{source_title}]]"
    if page_path.exists():
        meta, body = _frontmatter_and_body(page_path)
        body = body.rstrip()
        if link_line not in body:
            body += f"\n{link_line}\n"
        source_ids = set(meta.get("source_ids") or [])
        source_ids.add(source_id)
        meta["source_ids"] = sorted(source_ids)
        refs = set(meta.get("provenance_refs") or [])
        refs.add(source_page_rel)
        meta["provenance_refs"] = sorted(refs)
        _write_page(page_path, meta, body)
    else:
        meta = _default_page_meta(
            name,
            kind,
            provenance_kind="derived",
            provenance_refs=[source_page_rel],
            source_ids=[source_id],
            tags=[kind, "auto-generated"],
        )
        body = f"# {name}\n\nAuto-maintained {kind} page.\n\n## Mentioned In\n\n{link_line}\n"
        _write_page(page_path, meta, body)
    return _relative(page_path, vault_path)


def ingest_external_source(
    *,
    vault_slug: str,
    source_path: str | None = None,
    content: str | None = None,
    title: str | None = None,
    source_url: str | None = None,
    root_override: str | None = None,
) -> dict[str, Any]:
    if not source_path and not content:
        raise ValueError("source_path or content is required")
    raw_content = content
    if source_path:
        raw_content = Path(source_path).expanduser().read_text(encoding="utf-8", errors="replace")
    resolved_title = str(title or "").strip()
    if not resolved_title:
        if source_path:
            resolved_title = Path(source_path).stem.replace("-", " ").replace("_", " ").strip().title()
        elif source_url:
            resolved_title = source_url.rstrip("/").split("/")[-1] or "Imported Source"
        else:
            resolved_title = "Imported Source"
    source_id = _sha256_text(raw_content or "")[:12]
    context = ensure_vault("external", vault_slug, root_override=root_override)
    raw_path = _copy_external_raw(
        context.path,
        source_path=source_path,
        content=raw_content,
        title=resolved_title,
        source_id=source_id,
    )
    source_slug = _slugify(resolved_title, fallback=source_id)
    source_page = context.path / "sources" / f"{source_slug}.md"
    excerpt = (raw_content or "").strip()
    if len(excerpt) > 1500:
        excerpt = excerpt[:1500].rstrip() + "..."
    meta = _default_page_meta(
        resolved_title,
        "source",
        provenance_kind="raw_source",
        provenance_refs=[_relative(raw_path, context.path)] + ([source_url] if source_url else []),
        source_ids=[source_id],
        tags=["source", "external"],
        confidence="high",
    )
    if source_url:
        meta["source_url"] = source_url
    related_entities = []
    related_concepts = []

    body = (
        f"# {resolved_title}\n\n"
        f"## Summary\n\n{_extract_summary(raw_content or '') or 'Imported source.'}\n\n"
        f"## Source Details\n\n"
        f"- Raw source: `{_relative(raw_path, context.path)}`\n"
        + (f"- URL: {source_url}\n" if source_url else "")
        + "\n## Excerpt\n\n"
        + excerpt
        + "\n"
    )

    created_entities = []
    created_concepts = []
    for entity in _extract_entity_candidates(raw_content or "")[:3]:
        created_entities.append(_slugify(entity))
    for concept in _extract_concept_candidates(raw_content or "")[:3]:
        created_concepts.append(_slugify(concept.replace("-", " ").title()))

    if created_entities:
        related_entities = [f"- [[entities/{slug}.md|{slug.replace('-', ' ').title()}]]" for slug in created_entities]
        body += "\n## Related Entities\n\n" + "\n".join(related_entities) + "\n"
    if created_concepts:
        related_concepts = [f"- [[concepts/{slug}.md|{slug.replace('-', ' ').title()}]]" for slug in created_concepts]
        body += "\n## Related Concepts\n\n" + "\n".join(related_concepts) + "\n"

    _write_page(source_page, meta, body)
    source_page_rel = _relative(source_page, context.path)

    entity_paths = []
    concept_paths = []
    for slug in created_entities:
        label = slug.replace("-", " ").title()
        entity_paths.append(
            _upsert_reference_page(
                context.path,
                "entities",
                label,
                source_title=resolved_title,
                source_page_rel=source_page_rel,
                source_id=source_id,
            )
        )
    for slug in created_concepts:
        label = slug.replace("-", " ").title()
        concept_paths.append(
            _upsert_reference_page(
                context.path,
                "concepts",
                label,
                source_title=resolved_title,
                source_page_rel=source_page_rel,
                source_id=source_id,
            )
        )

    update_index(context.path)
    append_log_entry(
        context.path,
        "ingest",
        resolved_title,
        details=(
            f"- source_id: `{source_id}`\n"
            f"- source_page: `sources/{source_slug}.md`\n"
            f"- raw_path: `{_relative(raw_path, context.path)}`\n"
        ),
    )
    refresh_overview(context.path)
    return {
        "status": "success",
        "vault_path": str(context.path),
        "source_id": source_id,
        "source_page": source_page_rel,
        "raw_path": _relative(raw_path, context.path),
        "entities": entity_paths,
        "concepts": concept_paths,
    }


def _parse_index_entries(vault_path: Path) -> list[dict[str, str]]:
    index_path = vault_path / "index.md"
    if not index_path.exists():
        return []
    pattern = re.compile(r"^- \[\[(?P<path>[^|\]]+)\|(?P<title>[^\]]+)\]\] - (?P<summary>.+)$")
    entries = []
    for line in index_path.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line.strip())
        if match:
            entries.append(match.groupdict())
    return entries


def _score_text(text: str, terms: list[str]) -> int:
    lowered = text.lower()
    return sum(lowered.count(term) for term in terms)


def _best_snippet(body: str, terms: list[str]) -> str:
    lines = _candidate_lines(body)
    scored = sorted((( _score_text(line, terms), line) for line in lines), key=lambda item: item[0], reverse=True)
    best = next((line for score, line in scored if score > 0), "")
    if best:
        return best[:240]
    return _extract_summary(body)[:240]


def _ensure_section_link(path: Path, section_title: str, link_line: str) -> None:
    meta, body = _frontmatter_and_body(path)
    if link_line in body:
        return
    section_header = f"## {section_title}"
    body = body.rstrip()
    if section_header in body:
        before, after = body.split(section_header, 1)
        body = before.rstrip() + f"\n\n{section_header}\n\n" + after.lstrip().rstrip() + f"\n{link_line}\n"
    else:
        body += f"\n\n{section_header}\n\n{link_line}\n"
    _write_page(path, meta, body)


def query_vault(
    *,
    vault_kind: str,
    vault_slug: str,
    query: str,
    max_results: int = 5,
    save_answer: bool = False,
    answer_title: str | None = None,
    root_override: str | None = None,
) -> dict[str, Any]:
    context = ensure_vault(vault_kind, vault_slug, root_override=root_override)
    terms = [term for term in re.findall(r"[a-z0-9]+", query.lower()) if term]
    index_entries = _parse_index_entries(context.path)
    matches: list[dict[str, Any]] = []
    for entry in index_entries:
        score = _score_text(" ".join(entry.values()), terms)
        if score <= 0:
            continue
        page_path = context.path / entry["path"]
        meta, body = _frontmatter_and_body(page_path)
        snippet = _best_snippet(body, terms)
        matches.append(
            {
                "path": entry["path"],
                "title": entry["title"],
                "summary": entry["summary"],
                "score": score,
                "snippet": snippet,
                "kind": meta.get("kind", ""),
            }
        )
    matches.sort(key=lambda item: (item["score"], item["title"].lower()), reverse=True)
    matches = matches[: max(1, max_results)]
    answer_lines = []
    if matches:
        answer_lines.append(f"Query: {query}")
        answer_lines.append("")
        answer_lines.append("Relevant pages:")
        for match in matches:
            answer_lines.append(f"- [[{match['path']}|{match['title']}]]: {match['snippet']}")
    else:
        answer_lines.append(f"Query: {query}")
        answer_lines.append("")
        answer_lines.append("No relevant pages matched the current index.")
    answer_text = "\n".join(answer_lines)

    saved_path = ""
    if save_answer:
        title_value = str(answer_title or "").strip() or f"Analysis {query[:60]}"
        analysis_slug = _slugify(title_value, fallback="analysis")
        analysis_path = context.path / "analyses" / f"{analysis_slug}.md"
        provenance = [match["path"] for match in matches]
        meta = _default_page_meta(
            title_value,
            "analysis",
            provenance_kind="query_result",
            provenance_refs=provenance,
            source_ids=sorted({source_id for record in _scan_page_records(context.path) for source_id in record.get("source_ids", []) if record["path"] in provenance}),
            tags=["analysis", context.kind],
        )
        body = f"# {title_value}\n\n## Query\n\n{query}\n\n## Answer\n\n{answer_text}\n"
        _write_page(analysis_path, meta, body)
        saved_path = _relative(analysis_path, context.path)
        backlink = f"- [[{saved_path}|{title_value}]]"
        for match in matches:
            target_path = context.path / match["path"]
            if target_path.exists():
                _ensure_section_link(target_path, "Related Analyses", backlink)
        update_index(context.path)
        append_log_entry(context.path, "query", title_value, f"- persisted: `{saved_path}`\n- query: {query}\n")
        refresh_overview(context.path)

    return {
        "status": "success",
        "vault_path": str(context.path),
        "index_path": str(context.path / "index.md"),
        "matches": matches,
        "answer": answer_text,
        "saved_analysis_path": saved_path,
    }


def _extract_wikilinks(text: str) -> list[str]:
    return re.findall(r"\[\[([^\]|#]+)", text)


def _source_content_for_candidate_checks(body: str) -> str:
    if "## Excerpt" in body:
        excerpt = body.split("## Excerpt", 1)[1]
        if "\n## " in excerpt:
            excerpt = excerpt.split("\n## ", 1)[0]
        return excerpt
    return body


def _resolve_record_target(link: str, title_to_path: dict[str, str], record_paths: set[str]) -> str | None:
    cleaned = link.strip()
    if not cleaned:
        return None
    if cleaned in record_paths:
        return cleaned
    if not cleaned.endswith(".md") and f"{cleaned}.md" in record_paths:
        return f"{cleaned}.md"
    by_title = title_to_path.get(cleaned)
    if by_title:
        return by_title
    stem = Path(cleaned).stem.replace("-", " ").title()
    return title_to_path.get(stem)


def lint_vault(*, vault_kind: str, vault_slug: str, root_override: str | None = None) -> dict[str, Any]:
    context = ensure_vault(vault_kind, vault_slug, root_override=root_override)
    records = _scan_page_records(context.path)
    record_paths = {record["path"] for record in records}
    title_to_path = {record["title"]: record["path"] for record in records}
    indexed_paths = {entry["path"] for entry in _parse_index_entries(context.path)}
    inbound: Counter[str] = Counter()
    findings: list[dict[str, str]] = []

    for record in records:
        page_path = context.path / record["path"]
        meta, body = _frontmatter_and_body(page_path)
        missing_fields = [field for field in REQUIRED_FRONTMATTER_FIELDS if field not in meta]
        if missing_fields:
            findings.append({"kind": "malformed_frontmatter", "path": record["path"], "detail": ", ".join(missing_fields)})
        if record["path"] not in indexed_paths:
            findings.append({"kind": "missing_index_entry", "path": record["path"], "detail": "Page is missing from index.md"})
        if record["kind"] != "source" and not meta.get("source_ids"):
            findings.append({"kind": "missing_source_ids", "path": record["path"], "detail": "Managed page has no source_ids"})
        for ref in meta.get("provenance_refs") or []:
            if ref.startswith("http://") or ref.startswith("https://"):
                continue
            if not (context.path / ref).exists():
                findings.append({"kind": "stale_provenance_ref", "path": record["path"], "detail": ref})
        for link in _extract_wikilinks(body):
            target = _resolve_record_target(link, title_to_path, record_paths)
            if target:
                inbound[target] += 1
                continue
            candidate = link if link.endswith(".md") else f"{link}.md"
            if candidate not in record_paths and not (context.path / link).exists() and not (context.path / candidate).exists():
                findings.append({"kind": "broken_wikilink", "path": record["path"], "detail": link})
        for asset in re.findall(r"!\[\[([^\]]+)\]\]", body):
            asset_path = context.path / asset
            if not asset_path.exists():
                findings.append({"kind": "missing_asset_reference", "path": record["path"], "detail": asset})
        lowered_body = body.lower()
        if any(marker in lowered_body for marker in CONTRADICTION_MARKERS):
            findings.append({"kind": "contradiction_candidate", "path": record["path"], "detail": "Review conflicting language markers"})

    for record in records:
        if record["kind"] not in {"source", "overview"} and inbound[record["path"]] == 0:
            findings.append({"kind": "orphan_page", "path": record["path"], "detail": "No inbound wikilinks"})

    existing_entities = {_slugify(Path(record["path"]).stem) for record in records if record["path"].startswith("entities/")}
    entity_tokens = {token for slug in existing_entities for token in slug.split("-")}
    existing_concepts = {_slugify(Path(record["path"]).stem) for record in records if record["path"].startswith("concepts/")}
    for record in records:
        if not record["path"].startswith("sources/"):
            continue
        _meta, body = _frontmatter_and_body(context.path / record["path"])
        candidate_text = _source_content_for_candidate_checks(body)
        for entity in _extract_entity_candidates(candidate_text)[:3]:
            if _slugify(entity) not in existing_entities:
                findings.append({"kind": "missing_entity_page", "path": record["path"], "detail": entity})
        for concept in _extract_concept_candidates(candidate_text)[:3]:
            concept_slug = _slugify(concept)
            if concept_slug in existing_entities or concept_slug in entity_tokens:
                continue
            if concept_slug not in existing_concepts:
                findings.append({"kind": "missing_concept_page", "path": record["path"], "detail": concept})

    lint_dir = context.path / "lint"
    lint_path = lint_dir / f"lint_{_timestamp_slug()}.md"
    lines = [f"# Lint Report for {context.title}", "", f"- Generated: `{_now_iso()}`", ""]
    if findings:
        for finding in findings:
            lines.append(f"- `{finding['kind']}` in `{finding['path']}`: {finding['detail']}")
    else:
        lines.append("- No lint findings.")
    _write_text(lint_path, "\n".join(lines).rstrip() + "\n")
    append_log_entry(context.path, "lint", context.title, f"- findings: `{len(findings)}`\n- report: `{_relative(lint_path, context.path)}`\n")
    refresh_overview(context.path)
    return {
        "status": "success",
        "vault_path": str(context.path),
        "report_path": _relative(lint_path, context.path),
        "finding_count": len(findings),
        "findings": findings,
    }


def _copy_tree_file(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.suffix == ".json":
        payload = _load_json(src, {})
        _write_json(dest, payload)
    else:
        _write_text(dest, _read_text_limited(src))


def _load_sync_state(vault_path: Path) -> dict[str, Any]:
    payload = _load_json(vault_path / SYNC_STATE_FILENAME, {})
    return payload if isinstance(payload, dict) else {}


def _write_sync_state(vault_path: Path, payload: dict[str, Any]) -> None:
    _write_json(vault_path / SYNC_STATE_FILENAME, payload)


def _write_sync_progress(vault_path: Path, payload: dict[str, Any]) -> None:
    _write_json(vault_path / SYNC_PROGRESS_FILENAME, payload)
    lines = [
        "# Sync Progress",
        "",
        f"- Status: `{payload.get('status', 'unknown')}`",
        f"- Trigger: `{payload.get('trigger', '')}`",
        f"- Started at: `{payload.get('started_at', '')}`",
        f"- Updated at: `{payload.get('updated_at', payload.get('started_at', ''))}`",
        f"- Phase: `{payload.get('phase', 'starting')}`",
    ]
    phase_timings = payload.get("phase_timings_ms") or {}
    if isinstance(phase_timings, dict) and phase_timings:
        lines.extend(["", "## Phase Timings (ms)", ""])
        for key in sorted(phase_timings):
            lines.append(f"- `{key}`: `{phase_timings[key]}`")
    copied_counts = payload.get("copied_counts") or {}
    skipped_counts = payload.get("skipped_counts") or {}
    if isinstance(copied_counts, dict) and copied_counts:
        lines.extend(["", "## Copied Counts", ""])
        for key in sorted(copied_counts):
            lines.append(f"- `{key}`: `{copied_counts[key]}`")
    if isinstance(skipped_counts, dict) and skipped_counts:
        lines.extend(["", "## Skipped Counts", ""])
        for key in sorted(skipped_counts):
            lines.append(f"- `{key}`: `{skipped_counts[key]}`")
    if payload.get("total_duration_ms") is not None:
        lines.extend(["", f"- Total duration ms: `{payload['total_duration_ms']}`"])
    _write_text(vault_path / SYNC_PROGRESS_MARKDOWN_FILENAME, "\n".join(lines).rstrip() + "\n")


def _extract_matching_lines(text: str, keywords: tuple[str, ...], *, limit: int = 20) -> list[str]:
    hits = []
    for line in text.splitlines():
        stripped = line.strip().lstrip("-").strip()
        if not stripped:
            continue
        lowered = stripped.lower()
        if any(keyword in lowered for keyword in keywords):
            hits.append(stripped[:240])
        if len(hits) >= limit:
            break
    return hits


def sync_internal_memory_vault(
    *,
    vault_slug: str = "internal-memory",
    trigger: str = "manual",
    root_override: str | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    phase_timings: dict[str, int] = {}
    progress_payload: dict[str, Any] = {"status": "started", "trigger": trigger, "started_at": _now_iso()}
    sync_state = {}
    source_fingerprints: dict[str, Any] = {}
    copied_counts = {"memory": 0, "sessions": 0, "checkpoints": 0}
    skipped_counts = {"memory": 0, "sessions": 0, "checkpoints": 0}

    def mark(name: str, phase_start: float) -> float:
        now = time.perf_counter()
        phase_timings[name] = int((now - phase_start) * 1000)
        progress_payload["phase"] = name
        progress_payload["phase_timings_ms"] = dict(phase_timings)
        progress_payload["updated_at"] = _now_iso()
        _write_sync_progress(context.path, progress_payload)
        return now

    context = ensure_vault("internal", vault_slug, root_override=root_override)
    sync_state = _load_sync_state(context.path)
    source_fingerprints = dict(sync_state.get("source_fingerprints") or {})
    _write_sync_progress(context.path, progress_payload)
    phase_start = time.perf_counter()
    shared_root = Path(resolve_shared_memory_workspace()).resolve()
    memory_root = shared_root / "memory"
    repo_root = _repo_root()
    workspaces_root = repo_root / "AGENT_RUN_WORKSPACES"

    memory_files = []
    memory_md = shared_root / "MEMORY.md"
    if memory_md.exists():
        memory_files.append(memory_md)
    memory_files.extend(
        sorted(
            (p for p in memory_root.glob("*.md") if p.name not in {"index.md", "MEMORY.md"}),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )[:MAX_INTERNAL_MEMORY_FILES]
    )
    session_files = (
        sorted((memory_root / "sessions").glob("*.md"), key=lambda path: path.stat().st_mtime, reverse=True)[:MAX_INTERNAL_SESSION_FILES]
        if (memory_root / "sessions").exists()
        else []
    )
    checkpoint_files: list[Path] = []
    if workspaces_root.exists():
        for summary in list_workspace_summaries(workspaces_root, limit=MAX_INTERNAL_CHECKPOINTS):
            workspace_path = Path(str(summary.get("workspace_path") or "")).resolve()
            for candidate_name in ("run_checkpoint.json", "session_checkpoint.json"):
                candidate = workspace_path / candidate_name
                if candidate.exists():
                    checkpoint_files.append(candidate)
                    break
    logger.info(
        "llm_wiki_internal_sync_sources trigger=%s memory=%d sessions=%d checkpoints=%d",
        trigger,
        len(memory_files),
        len(session_files),
        len(checkpoint_files),
    )
    phase_start = mark("discover_sources_ms", phase_start)

    copied_memory = []
    copied_sessions = []
    copied_checkpoints = []

    for src in memory_files:
        if src.exists():
            dest = context.path / "evidence" / "memory" / src.name
            rel_dest = _relative(dest, context.path)
            fingerprint = _fingerprint_file(src)
            if source_fingerprints.get(rel_dest) != fingerprint or not dest.exists():
                _copy_tree_file(src, dest)
                copied_counts["memory"] += 1
            else:
                skipped_counts["memory"] += 1
            source_fingerprints[rel_dest] = fingerprint
            copied_memory.append(_relative(dest, context.path))
    phase_start = mark("copy_memory_ms", phase_start)
    for src in session_files:
        dest = context.path / "evidence" / "sessions" / src.name
        rel_dest = _relative(dest, context.path)
        fingerprint = _fingerprint_file(src)
        if source_fingerprints.get(rel_dest) != fingerprint or not dest.exists():
            _copy_tree_file(src, dest)
            copied_counts["sessions"] += 1
        else:
            skipped_counts["sessions"] += 1
        source_fingerprints[rel_dest] = fingerprint
        copied_sessions.append(_relative(dest, context.path))
    phase_start = mark("copy_sessions_ms", phase_start)
    for src in checkpoint_files:
        rel = src.relative_to(workspaces_root)
        dest = context.path / "evidence" / "checkpoints" / rel
        rel_dest = _relative(dest, context.path)
        fingerprint = _fingerprint_file(src)
        if source_fingerprints.get(rel_dest) != fingerprint or not dest.exists():
            _copy_tree_file(src, dest)
            copied_counts["checkpoints"] += 1
        else:
            skipped_counts["checkpoints"] += 1
        source_fingerprints[rel_dest] = fingerprint
        copied_checkpoints.append(_relative(dest, context.path))
    phase_start = mark("copy_checkpoints_ms", phase_start)

    decision_hits = []
    preference_hits = []
    incident_hits = []
    thread_hits = []
    for file_path in [*memory_files, *session_files]:
        if not file_path.exists():
            continue
        text = file_path.read_text(encoding="utf-8", errors="replace")
        rel = _relative((context.path / "evidence" / ("sessions" if "sessions" in file_path.parts else "memory") / file_path.name), context.path)
        decision_hits.extend(f"- `{rel}`: {line}" for line in _extract_matching_lines(text, ("decided", "chose", "selected", "using", "switched to")))
        preference_hits.extend(f"- `{rel}`: {line}" for line in _extract_matching_lines(text, ("prefer", "want", "avoid", "like", "need")))
        incident_hits.extend(f"- `{rel}`: {line}" for line in _extract_matching_lines(text, ("incident", "error", "failed", "failure", "broken", "regression", "crash")))
    for checkpoint in checkpoint_files:
        payload = _load_json(checkpoint, {})
        if not isinstance(payload, dict):
            continue
        title = str(payload.get("original_request") or checkpoint.parent.name).strip()[:180]
        rel = _relative(context.path / "evidence" / "checkpoints" / checkpoint.relative_to(workspaces_root), context.path)
        thread_hits.append(f"- `{rel}`: {title}")
        for decision in payload.get("key_decisions") or []:
            decision_hits.append(f"- `{rel}`: {str(decision)[:220]}")
        for failure in payload.get("failed_approaches") or []:
            incident_hits.append(f"- `{rel}`: {str(failure)[:220]}")

    ledgers = {
        "decisions/decision-ledger.md": ("Decision Ledger", decision_hits[:30], "decision"),
        "preferences/preferences-ledger.md": ("Preference Ledger", preference_hits[:30], "preference"),
        "incidents/incidents-ledger.md": ("Incident Ledger", incident_hits[:30], "incident"),
        "threads/recent-threads.md": ("Recent Threads", thread_hits[:30], "thread"),
        "projects/project-memory.md": (
            "Project Memory",
            [
                f"- Memory evidence files: `{len(copied_memory)}`",
                f"- Session evidence files: `{len(copied_sessions)}`",
                f"- Checkpoint evidence files: `{len(copied_checkpoints)}`",
                f"- Trigger: `{trigger}`",
            ],
            "project",
        ),
    }
    generated_pages = []
    for rel_path, (title, items, kind) in ledgers.items():
        path = context.path / rel_path
        body = f"# {title}\n\n"
        if items:
            body += "\n".join(items) + "\n"
        else:
            body += "No entries captured yet.\n"
        meta = _default_page_meta(
            title,
            kind,
            provenance_kind="derived_projection",
            provenance_refs=[*copied_memory[:10], *copied_sessions[:10], *copied_checkpoints[:10]],
            tags=["internal-memory", kind],
        )
        _write_page(path, meta, body)
        generated_pages.append(_relative(path, context.path))
    phase_start = mark("compile_ledgers_ms", phase_start)

    update_index(context.path)
    append_log_entry(
        context.path,
        "sync",
        context.title,
        (
            f"- trigger: `{trigger}`\n"
            f"- memory_files: `{len(copied_memory)}`\n"
            f"- session_files: `{len(copied_sessions)}`\n"
            f"- checkpoint_files: `{len(copied_checkpoints)}`\n"
            f"- timings_ms: `{json.dumps(phase_timings, ensure_ascii=True, sort_keys=True)}`\n"
        ),
    )
    refresh_overview(context.path)
    mark("finalize_ms", phase_start)
    total_duration_ms = int((time.perf_counter() - started) * 1000)
    sync_state = {
        "schema_version": 1,
        "last_trigger": trigger,
        "last_completed_at": _now_iso(),
        "source_fingerprints": source_fingerprints,
        "phase_timings_ms": phase_timings,
        "total_duration_ms": total_duration_ms,
        "copied_counts": copied_counts,
        "skipped_counts": skipped_counts,
    }
    _write_sync_state(context.path, sync_state)
    progress_payload.update(
        {
            "status": "completed",
            "updated_at": _now_iso(),
            "phase_timings_ms": dict(phase_timings),
            "total_duration_ms": total_duration_ms,
            "copied_counts": copied_counts,
            "skipped_counts": skipped_counts,
        }
    )
    _write_sync_progress(context.path, progress_payload)
    logger.info(
        "llm_wiki_internal_sync_complete trigger=%s duration_ms=%d timings=%s generated_pages=%d",
        trigger,
        total_duration_ms,
        json.dumps(phase_timings, ensure_ascii=True, sort_keys=True),
        len(generated_pages),
    )
    return {
        "status": "success",
        "vault_path": str(context.path),
        "memory_files": copied_memory,
        "session_files": copied_sessions,
        "checkpoint_files": copied_checkpoints,
        "generated_pages": generated_pages,
        "timings_ms": phase_timings,
        "total_duration_ms": total_duration_ms,
        "copied_counts": copied_counts,
        "skipped_counts": skipped_counts,
    }
