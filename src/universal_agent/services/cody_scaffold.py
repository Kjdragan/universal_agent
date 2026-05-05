"""Cody scaffold builder — bridges vault entity pages to demo workspaces.

This is the Phase 2 mechanical helper for Simone. The actual
"is this entity demo-worthy?" decision and the prose authoring of
BRIEF.md / ACCEPTANCE.md / business_relevance.md happen in the
`cody-scaffold-builder` skill (Simone reads the SKILL.md, decides,
and edits the templates this module writes). This module just does
the structural work: read the vault, copy raw docs, write template
shells, provision the workspace.

Per the v2 design (§7.3), Simone's authored artifacts go to:

    /opt/ua_demos/<demo-id>/
        ├── .claude/settings.json   (vanilla; from PR 7 template)
        ├── BRIEF.md                (template here, Simone fills in)
        ├── ACCEPTANCE.md           (template here, Simone fills in)
        ├── business_relevance.md   (template here, Simone fills in)
        ├── SOURCES/                (raw docs copied from vault)
        └── pyproject.toml          (placeholder for Cody's deps)

See docs/proactive_signals/claudedevs_intel_v2_design.md §7.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import re
import shutil
from pathlib import Path
from typing import Any

import yaml

from universal_agent.services.demo_workspace import (
    WorkspaceProvisionResult,
    provision_demo_workspace,
)

logger = logging.getLogger(__name__)


# ── Vault entity reading ────────────────────────────────────────────────────


@dataclass(frozen=True)
class VaultEntity:
    """A loaded entity page from the vault."""

    slug: str
    path: Path
    title: str
    body: str
    frontmatter: dict[str, Any]

    @property
    def briefing_status(self) -> str:
        return str(self.frontmatter.get("briefing_status") or "").strip().lower()

    @property
    def endpoint_required(self) -> str:
        return str(self.frontmatter.get("endpoint_required") or "anthropic_native").strip()

    @property
    def business_relevance(self) -> str:
        return str(self.frontmatter.get("business_relevance") or "unknown").strip().lower()

    @property
    def source_ids(self) -> list[str]:
        raw = self.frontmatter.get("source_ids") or []
        return [str(s).strip() for s in raw if str(s).strip()]

    @property
    def tags(self) -> list[str]:
        raw = self.frontmatter.get("tags") or []
        return [str(t).strip() for t in raw if str(t).strip()]


def _slugify(value: str, fallback: str = "entity") -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return text or fallback


def find_vault_entity(slug_or_name: str, vault_root: Path) -> Path | None:
    """Locate an entity page. Accepts either a slug or a human title."""
    entities_dir = vault_root / "entities"
    if not entities_dir.exists():
        return None
    raw = str(slug_or_name or "").strip()
    if not raw:
        return None
    direct = entities_dir / f"{raw}.md"
    if direct.exists():
        return direct
    slug = _slugify(raw)
    direct_slug = entities_dir / f"{slug}.md"
    if direct_slug.exists():
        return direct_slug
    return None


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        return {}, text
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except Exception:
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    return meta, parts[2].lstrip("\n")


def read_entity(path: Path) -> VaultEntity:
    if not path.exists():
        raise FileNotFoundError(f"entity page not found: {path}")
    raw = path.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(raw)
    title = str(frontmatter.get("title") or path.stem.replace("-", " ").replace("_", " ").title())
    slug = path.stem
    return VaultEntity(
        slug=slug,
        path=path,
        title=title,
        body=body,
        frontmatter=frontmatter,
    )


# ── Source selection ────────────────────────────────────────────────────────


def select_relevant_sources(
    entity: VaultEntity,
    *,
    vault_root: Path,
    limit: int = 6,
) -> list[Path]:
    """Pick raw/source files to copy into the demo workspace.

    Conservative ordering: source pages whose source_ids are listed on the
    entity, then any raw doc whose stem matches the entity slug, then any
    docs whose stem appears in the entity's tags. Caps at `limit` so a
    very dense entity doesn't drown Cody in 50 docs.
    """
    candidates: list[Path] = []
    seen: set[Path] = set()

    def _add(path: Path) -> None:
        if not path or not path.exists() or not path.is_file():
            return
        resolved = path.resolve()
        if resolved in seen:
            return
        seen.add(resolved)
        candidates.append(path)

    # 1. Sources referenced by source_id in the entity frontmatter.
    sources_dir = vault_root / "sources"
    if sources_dir.exists():
        for source_id in entity.source_ids:
            for path in sources_dir.glob(f"*{source_id[:24]}*.md"):
                _add(path)
                if len(candidates) >= limit:
                    return candidates[:limit]

    # 2. Raw docs whose filename stem mentions the entity slug.
    raw_dir = vault_root / "raw"
    if raw_dir.exists():
        for path in raw_dir.rglob("*.md"):
            if entity.slug.lower() in path.stem.lower():
                _add(path)
                if len(candidates) >= limit:
                    return candidates[:limit]

    # 3. Source pages whose stem matches any entity tag.
    if sources_dir.exists():
        tag_terms = {t.lower() for t in entity.tags}
        for path in sources_dir.glob("*.md"):
            if any(term in path.stem.lower() for term in tag_terms if len(term) > 2):
                _add(path)
                if len(candidates) >= limit:
                    break

    return candidates[:limit]


# ── Template authoring ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class ScaffoldArtifacts:
    """Paths to the artifact files written into the demo workspace."""

    workspace_dir: Path
    brief_path: Path
    acceptance_path: Path
    business_relevance_path: Path
    sources_dir: Path
    sources_copied: tuple[Path, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_dir": str(self.workspace_dir),
            "brief_path": str(self.brief_path),
            "acceptance_path": str(self.acceptance_path),
            "business_relevance_path": str(self.business_relevance_path),
            "sources_dir": str(self.sources_dir),
            "sources_copied": [str(p) for p in self.sources_copied],
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_brief_template(
    *,
    workspace: Path,
    entity: VaultEntity,
    sources_copied: list[Path],
) -> Path:
    """Author a starting BRIEF.md for Simone to refine.

    The template encodes everything the entity page already contains
    (title, summary, source_ids, tags, body excerpt) plus a list of
    the files Simone has to read in SOURCES/. Simone should refine
    the prose; the structure is pre-authored.
    """
    target = workspace / "BRIEF.md"

    summary = str(entity.frontmatter.get("summary") or "").strip()
    body_excerpt = entity.body.strip()
    if len(body_excerpt) > 4000:
        body_excerpt = body_excerpt[:4000] + "\n\n_(truncated; see entity page for full content)_"

    lines: list[str] = [
        f"# Feature Briefing: {entity.title}",
        "",
        f"_Authored from vault entity `{entity.slug}` on {_now_iso()}._",
        "_Simone: refine this prose. Cody reads this first; she trusts what's here._",
        "",
        "## What is this feature?",
        "",
    ]
    if summary:
        lines.extend([summary, ""])
    else:
        lines.extend(["_(Simone: synthesize 1–3 sentences from the entity body below.)_", ""])

    lines.extend([
        "## Why does it matter?",
        "",
        "_(Simone: 1 sentence on client value. See `business_relevance.md` for the longer take.)_",
        "",
        "## Canonical use case",
        "",
        "_(Simone: one sentence describing the textbook example from the official docs in SOURCES/.)_",
        "",
        "## Named API surface",
        "",
        "_(Simone: list the functions, classes, env vars, CLI flags Cody needs. Pull from the entity body and SOURCES/.)_",
        "",
        "## Reference docs in SOURCES/",
        "",
    ])
    if sources_copied:
        for path in sources_copied:
            lines.append(f"- `SOURCES/{path.name}`")
    else:
        lines.append("_(none — research grounding may not have fired yet, or sources are linked but not bundled)_")
    lines.extend([
        "",
        "## Pointers",
        "",
        f"- Vault entity: `{entity.path}`",
        f"- Acceptance contract: `ACCEPTANCE.md`",
        f"- Business relevance: `business_relevance.md`",
        "",
        "## Entity page body (verbatim)",
        "",
        body_excerpt,
        "",
    ])
    target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return target


def write_acceptance_template(
    *,
    workspace: Path,
    entity: VaultEntity,
) -> Path:
    """Author a starting ACCEPTANCE.md for Simone to refine."""
    target = workspace / "ACCEPTANCE.md"

    min_versions = entity.frontmatter.get("min_versions") or {}
    min_versions_block = (
        "\n".join(f"  - `{pkg}`: `{ver}`" for pkg, ver in (min_versions or {}).items())
        if min_versions
        else "  _(none declared — pull from the entity page frontmatter when known)_"
    )

    body = f"""# Acceptance Contract: {entity.title}

_Authored from vault entity `{entity.slug}` on {_now_iso()}._
_Simone: fill in the requirements. Cody MUST satisfy every numbered requirement._

## Requirements

1. _(Simone: one concrete behavior the demo MUST exercise)_
2. _(Simone: one concrete API/CLI surface the demo MUST use, named explicitly)_
3. The demo MUST run end-to-end via `uv run python <entry>.py` (or equivalent runner).
4. The demo's stdout MUST contain a recognizable success token (specify exact string).
5. `manifest.json.endpoint_hit` MUST resolve to `api.anthropic.com` for endpoint_required={entity.endpoint_required}.

## Anti-patterns

- Cody MUST NOT invent API surface. If the docs in SOURCES/ don't show how to do something, document the gap in BUILD_NOTES.md and stop.
- _(Simone: list any specific anti-patterns drawn from the docs.)_

## must_use_examples

- pattern: _(Simone: name the pattern)_
  reference: SOURCES/_(filename)_.md#L_(line range)_

## endpoint_required

`{entity.endpoint_required}`

## min_versions

{min_versions_block}
"""
    target.write_text(body, encoding="utf-8")
    return target


def write_business_relevance_template(
    *,
    workspace: Path,
    entity: VaultEntity,
) -> Path:
    """Author a starting business_relevance.md for Simone to refine."""
    target = workspace / "business_relevance.md"
    priority = entity.business_relevance if entity.business_relevance != "unknown" else "medium"
    body = f"""# Business Relevance: {entity.title}

_Authored from vault entity `{entity.slug}` on {_now_iso()}._
_Simone: this is the Kevin-facing rationale. Be explicit about client applicability._

## Client value

_(Simone: who would buy this, and what problem does it solve for them?)_

## Reference-implementation shape

_(Simone: how should Cody structure the implementation so it can be lifted into a client engagement with minimal rework?)_

## Priority

`{priority}` — drives how much iteration depth Simone is willing to invest before deferring.

## Related demos

_(Simone: list any other demo IDs that share patterns or could be combined into a larger reference.)_
"""
    target.write_text(body, encoding="utf-8")
    return target


def populate_workspace_sources(
    *,
    workspace: Path,
    sources: list[Path],
) -> list[Path]:
    """Copy selected raw/source docs into the workspace's SOURCES/ dir.

    Returns the list of destination paths actually written (skips files
    that fail to copy).
    """
    dest_dir = workspace / "SOURCES"
    dest_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for src in sources:
        if not src.exists() or not src.is_file():
            continue
        dest = dest_dir / src.name
        try:
            shutil.copy2(src, dest)
            written.append(dest)
        except Exception as exc:
            logger.warning("failed to copy source %s → %s: %s", src, dest, exc)
    return written


# ── Orchestration entry point ───────────────────────────────────────────────


def build_demo_scaffold(
    *,
    entity_path: Path,
    demo_id: str,
    vault_root: Path,
    demos_root: Path | None = None,
    overwrite: bool = False,
    source_limit: int = 6,
) -> ScaffoldArtifacts:
    """Full scaffold flow: read entity → provision workspace → copy SOURCES → write templates.

    `demo_id` is the slug under `/opt/ua_demos/`. Caller chooses it (typically
    `<entity-slug>__<short-id>` to avoid collisions across multiple demos
    of the same feature).
    """
    entity = read_entity(entity_path)
    sources = select_relevant_sources(entity, vault_root=vault_root, limit=source_limit)

    provision_result: WorkspaceProvisionResult = provision_demo_workspace(
        demo_id,
        root=demos_root,
        overwrite=overwrite,
    )
    workspace = provision_result.workspace_dir

    sources_written = populate_workspace_sources(workspace=workspace, sources=sources)
    brief_path = write_brief_template(
        workspace=workspace,
        entity=entity,
        sources_copied=sources_written,
    )
    acceptance_path = write_acceptance_template(workspace=workspace, entity=entity)
    business_relevance_path = write_business_relevance_template(
        workspace=workspace,
        entity=entity,
    )

    return ScaffoldArtifacts(
        workspace_dir=workspace,
        brief_path=brief_path,
        acceptance_path=acceptance_path,
        business_relevance_path=business_relevance_path,
        sources_dir=workspace / "SOURCES",
        sources_copied=tuple(sources_written),
    )
