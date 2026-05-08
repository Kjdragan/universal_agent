"""CSI Intelligence Persistence — VaultDelta → memex_apply_action bridge.

Pure plumbing layer between the LLM-driven CSI intelligence pass and the
existing Memex vault primitives. The LLM (in
``services/csi_intelligence_pass.py``) decides *what* to write; this
module decides *where the file goes* and *how the markdown body is laid
out*. No meaning judgment lives here — only structural concerns.

Responsibilities:

- Translate each ``VaultAction`` into a single ``memex_apply_action`` call.
- Handle the realistic mismatch between the LLM's intent (op="create")
  and the vault's current state (page already exists from a prior
  packet) — auto-downgrade CREATE→EXTEND or upgrade EXTEND→CREATE so
  callers can ship the LLM's output without pre-checking the vault.
- Compose markdown body content from the structured ``VaultAction``
  fields (summary + key_facts + source provenance + aliases).
- Persist relations as a separate `relations.jsonl` append-only log
  inside the vault root (tracked separately from per-entity pages so
  cross-entity edges don't get fragmented).

What this module does NOT do:

- LLM calls (zero — fully deterministic given a ``VaultDelta`` input).
- Entity-name canonicalization beyond a single light rule (Memory
  surface-form normalization, see ``_canonicalize_name``). Heavy
  fuzzy-matching belongs in a separate post-pass.
- Slug computation (defers to ``wiki/core.py:_slugify`` via
  ``memex_apply_action``).

Architecture spec:
  ``docs/proactive_signals/knowledge_extraction_redesign_context_2026-05-07.md``

Phase plan (this is Phase E):
  ``docs/proactive_signals/csi_intelligence_pass_implementation_plan_2026-05-07.md``
"""

from __future__ import annotations

import logging
from pathlib import Path
import re
from typing import Any

from universal_agent.services.csi_intelligence_pass import VaultAction, VaultDelta
from universal_agent.wiki.core import (
    ACTION_CREATE,
    ACTION_EXTEND,
    ACTION_REVISE,
    memex_apply_action,
    memex_page_exists,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Kind taxonomy bridge — LLM kinds → Memex kinds
# ---------------------------------------------------------------------------

# The CSI LLM emits a fine-grained taxonomy:
#   product / feature / concept / person / event
#
# The Memex vault layout (wiki/core.py) has only two directories:
#   entities/   — anything thing-shaped (products, features, people, events)
#   concepts/   — abstract patterns / techniques / theories
#
# Translate LLM kinds → memex kind, preserving the LLM's richer taxonomy
# as a frontmatter tag (`kind:<llm_kind>`). Downstream filtering /
# capability-library indexing keeps full taxonomy via the tag.
_LLM_KIND_TO_MEMEX_KIND = {
    "product": "entity",
    "feature": "entity",
    "concept": "concept",
    "person": "entity",
    "event": "entity",
}


def _translate_kind(llm_kind: str) -> str:
    """Map an LLM kind to the Memex vault's entity-or-concept dichotomy."""
    return _LLM_KIND_TO_MEMEX_KIND.get(
        str(llm_kind or "").strip().lower(), "entity"
    )


# ---------------------------------------------------------------------------
# Light canonicalization — the only "structural normalization" allowed
# ---------------------------------------------------------------------------

# Phase D Step 1 surfaced one bounded surface-form drift: the model
# emits "Memory (Claude Managed Agents)" sometimes and
# "Claude Managed Agents Memory" other times. They slugify to
# different filenames (memory-claude-managed-agents vs
# claude-managed-agents-memory). The first form is awkward for a
# vault filename — convert to the second.
_PARENTHETICAL_PATTERN = re.compile(r"^([^()]+?)\s*\(([^()]+)\)\s*$")


def _canonicalize_name(name: str) -> str:
    """Apply structural-only name normalization.

    Rules applied:
      1. Strip surrounding whitespace.
      2. ``"Memory (Claude Managed Agents)"`` →
         ``"Claude Managed Agents Memory"``.
         More generally, ``"<X> (<Y>)"`` collapses to ``"<Y> <X>"``
         when X is a single word ≤16 chars (avoids over-rewriting
         genuinely parenthetical disambiguation like
         ``"Mistral (the company)"``).

    Returns the canonicalized name; never raises.
    """
    cleaned = name.strip()
    if not cleaned:
        return cleaned

    m = _PARENTHETICAL_PATTERN.match(cleaned)
    if m:
        prefix, inner = m.group(1).strip(), m.group(2).strip()
        # Conservative rewrite — only when prefix is a single short word
        # (≤16 chars, no spaces). That captures "Memory (X)" / "Beta (X)" /
        # "API (X)" but leaves "Mistral (the company)" / longer
        # disambiguations alone.
        if prefix and " " not in prefix and len(prefix) <= 16:
            cleaned = f"{inner} {prefix}"

    return cleaned


# ---------------------------------------------------------------------------
# Body composition — structural-only markdown assembly
# ---------------------------------------------------------------------------


def _x_post_url(post_id: str, handle: str = "") -> str:
    """Build a public x.com URL for a post id."""
    pid = str(post_id or "").strip()
    if not pid:
        return ""
    if handle:
        return f"https://x.com/{handle}/status/{pid}"
    # Without a handle, the canonical "/i/web/status/" form still works
    return f"https://x.com/i/web/status/{pid}"


def _build_create_body(action: VaultAction, *, packet_id: str, handle: str) -> str:
    """Render the CREATE body for a freshly-minted entity page."""
    parts: list[str] = []

    # Lede summary (always present per Pydantic schema)
    parts.extend([action.summary.strip(), ""])

    if action.aliases:
        parts.extend(
            [
                "## Aliases",
                "",
                ", ".join(f"`{alias}`" for alias in action.aliases if alias.strip()),
                "",
            ]
        )

    if action.key_facts:
        parts.extend(["## Key facts", ""])
        for fact in action.key_facts:
            cleaned = fact.strip()
            if cleaned:
                parts.append(f"- {cleaned}")
        parts.append("")

    if action.source_post_ids:
        parts.extend(["## Source posts", ""])
        for pid in action.source_post_ids:
            url = _x_post_url(pid, handle=handle)
            if url:
                parts.append(f"- [post `{pid}`]({url})")
            else:
                parts.append(f"- post `{pid}`")
        parts.append("")

    if action.source_doc_urls:
        parts.extend(["## Source documents", ""])
        for url in action.source_doc_urls:
            cleaned = (url or "").strip()
            if cleaned:
                parts.append(f"- [{cleaned}]({cleaned})")
        parts.append("")

    if packet_id:
        parts.extend(["## Provenance", "", f"- packet: `{packet_id}`", ""])

    # Trailing newline cleanup happens in memex_create_page (`body.rstrip() + "\n"`)
    return "\n".join(parts)


def _build_extend_body(action: VaultAction, *, packet_id: str, handle: str) -> str:
    """Render the EXTEND body — short dated update appended to the existing page."""
    parts: list[str] = []

    parts.extend([action.summary.strip(), ""])

    if action.key_facts:
        parts.append("New key facts:")
        for fact in action.key_facts:
            cleaned = fact.strip()
            if cleaned:
                parts.append(f"- {cleaned}")
        parts.append("")

    if action.source_post_ids:
        parts.append("Sources:")
        for pid in action.source_post_ids:
            url = _x_post_url(pid, handle=handle)
            parts.append(f"- post `{pid}` — {url}" if url else f"- post `{pid}`")

    if action.source_doc_urls:
        for url in action.source_doc_urls:
            cleaned = (url or "").strip()
            if cleaned:
                parts.append(f"- [{cleaned}]({cleaned})")

    if packet_id:
        parts.append(f"- packet: `{packet_id}`")

    return "\n".join(parts).rstrip() + "\n"


def _build_revise_body(action: VaultAction, *, packet_id: str, handle: str) -> str:
    """Render the REVISE body — full replacement of the page's main content.

    REVISE is the only Memex op that overwrites prior content; the prior
    version is snapshotted into ``_history/`` automatically by
    ``memex_revise_page``. So the body shape is "the new authoritative
    page content" rather than "an addition".
    """
    return _build_create_body(action, packet_id=packet_id, handle=handle)


# ---------------------------------------------------------------------------
# Op resolution — handle realistic CREATE/EXTEND/REVISE mismatches
# ---------------------------------------------------------------------------


def _resolve_effective_op(
    action: VaultAction, *, vault_path: Path
) -> tuple[str, str, str]:
    """Decide what op to actually apply, and what name + slug to use.

    The LLM emits an intended op (``create`` / ``extend`` / ``revise``). The
    runtime vault may not match that intent (e.g. CREATE for an entity
    that actually already exists from a prior packet). This function
    reconciles, with these rules:

      - **op=create + page already exists** → downgrade to EXTEND. The
        prior content is preserved; the new content gets dated-section
        appended. Logged as a downgrade.
      - **op=extend or op=revise + page does not exist** → upgrade to
        CREATE. The LLM's ``existing_slug`` was wrong (or referred to a
        v1 vault we no longer trust). Logged as an upgrade.
      - Otherwise the LLM's intent is honored as-is.

    Returns ``(effective_op, name, log_note)`` where ``effective_op`` is
    one of the wiki/core ACTION_* constants. ``log_note`` is human-
    readable text describing any downgrade/upgrade for audit.
    """
    canonical_name = _canonicalize_name(action.name)
    memex_kind = _translate_kind(action.kind)
    intended = action.op.lower().strip()

    if intended == "create":
        if memex_page_exists(vault_path, memex_kind, canonical_name):
            return (
                ACTION_EXTEND,
                canonical_name,
                f"downgrade CREATE→EXTEND (page already exists: "
                f"memex_kind={memex_kind!r}, name={canonical_name!r})",
            )
        return (ACTION_CREATE, canonical_name, "")

    if intended in {"extend", "revise"}:
        # Honor the LLM's existing_slug if present and the page exists
        # under that name, else fall back to canonical_name.
        candidate_name = action.existing_slug or canonical_name
        if memex_page_exists(vault_path, memex_kind, candidate_name):
            actual_op = ACTION_EXTEND if intended == "extend" else ACTION_REVISE
            return (actual_op, candidate_name, "")
        # The LLM intended to extend/revise something that doesn't exist —
        # treat as a fresh CREATE with the canonical name.
        return (
            ACTION_CREATE,
            canonical_name,
            f"upgrade {intended.upper()}→CREATE (page does not exist: "
            f"memex_kind={memex_kind!r}, existing_slug={action.existing_slug!r}, "
            f"canonical_name={canonical_name!r})",
        )

    # Unknown op — defensive: treat as CREATE with the canonical name.
    return (
        ACTION_CREATE,
        canonical_name,
        f"unknown op {action.op!r} treated as CREATE",
    )


# ---------------------------------------------------------------------------
# Relation persistence — append-only log inside the vault root
# ---------------------------------------------------------------------------


def _append_relations_log(
    vault_path: Path,
    delta: VaultDelta,
    *,
    packet_id: str,
) -> int:
    """Append each relation to ``relations.jsonl`` under the vault root.

    Relations are tracked separately from per-entity pages so cross-entity
    edges don't get fragmented or duplicated. Each line is a self-
    contained JSON record.
    """
    if not delta.relations:
        return 0

    import json  # local import — only needed when relations are non-empty

    log_path = vault_path / "relations.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    with log_path.open("a", encoding="utf-8") as fh:
        for rel in delta.relations:
            record = {
                "from_slug": rel.from_slug,
                "to_slug": rel.to_slug,
                "kind": rel.kind,
                "packet_id": packet_id,
                "post_summary": delta.post_summary,
            }
            fh.write(json.dumps(record, ensure_ascii=False))
            fh.write("\n")
            written += 1
    return written


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def apply_vault_delta_to_vault(
    delta: VaultDelta,
    *,
    vault_path: Path,
    packet_id: str = "",
    handle: str = "",
) -> dict[str, Any]:
    """Persist a ``VaultDelta`` to a Memex vault.

    For each ``VaultAction`` in ``delta.vault_actions``:
      1. Canonicalize the entity name (light surface-form normalization).
      2. Resolve the effective op based on current vault state
         (auto-downgrade CREATE→EXTEND when page already exists;
         auto-upgrade EXTEND/REVISE→CREATE when target page is missing).
      3. Build the markdown body content from structured fields.
      4. Call ``memex_apply_action`` (which dispatches to the appropriate
         create/extend/revise primitive and appends to ``log.md``).

    Then append every ``VaultRelation`` to ``relations.jsonl``.

    Args:
        delta: The structured analysis emitted by ``analyze_action``.
        vault_path: Path to the vault root directory (must already exist;
            create it via ``ensure_vault`` if needed).
        packet_id: Optional packet identifier woven into provenance. Useful
            for tracing which CSI poll surfaced the entity.
        handle: Optional X handle (e.g. ``"ClaudeDevs"``) used to build
            full ``https://x.com/<handle>/status/<id>`` URLs.

    Returns:
        Dict summary of what happened, suitable for logging:

        .. code-block:: python

           {
             "applied": [
                {"op": "CREATE", "name": "Claude Code", "kind": "product",
                 "page_rel_path": "entities/claude-code.md", "log_note": ""},
                ...
             ],
             "errors": [
                {"action_index": 2, "name": "...", "error": "..."}
             ],
             "counts": {"create": 3, "extend": 1, "revise": 0},
             "relations_written": 4,
             "skipped_empty": 0,
           }

    Never raises on a single VaultAction error — collects per-action
    errors in ``errors`` so the caller can decide whether the partial
    write is acceptable.
    """
    applied: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    counts: dict[str, int] = {"create": 0, "extend": 0, "revise": 0}
    skipped_empty = 0

    for idx, va in enumerate(delta.vault_actions):
        if not va.name.strip():
            skipped_empty += 1
            continue

        try:
            effective_op, effective_name, log_note = _resolve_effective_op(
                va, vault_path=vault_path
            )

            if effective_op == ACTION_CREATE:
                body = _build_create_body(va, packet_id=packet_id, handle=handle)
            elif effective_op == ACTION_EXTEND:
                body = _build_extend_body(va, packet_id=packet_id, handle=handle)
            else:  # REVISE
                body = _build_revise_body(va, packet_id=packet_id, handle=handle)

            # Tags surfaced into page frontmatter for downstream filtering /
            # capability-library indexing. Includes the kind, the source
            # provenance bucket, and the LLM's confidence rating.
            tags = [
                f"kind:{va.kind}",
                "source:csi-claude-code",
                f"confidence:{va.confidence}",
            ]
            if packet_id:
                tags.append(f"packet:{packet_id}")

            primary_post_id = (va.source_post_ids or [""])[0]
            primary_doc_url = (va.source_doc_urls or [""])[0]
            source_id = primary_post_id or primary_doc_url
            source_title = va.summary[:200] if va.summary else effective_name

            section_label = ""
            if effective_op == ACTION_EXTEND:
                section_label = packet_id or primary_post_id or "Update"

            reason = ""
            if effective_op == ACTION_REVISE:
                reason = (
                    "Updated content per CSI intelligence pass on "
                    f"packet {packet_id or '(unknown)'}"
                )

            memex_kind = _translate_kind(va.kind)
            result = memex_apply_action(
                vault_path,
                action=effective_op,
                kind=memex_kind,
                name=effective_name,
                body=body,
                source_id=source_id,
                source_title=source_title,
                reason=reason,
                tags=tags,
                confidence=va.confidence,
                section_label=section_label,
            )

            applied.append(
                {
                    "op": result["action"],
                    "name": effective_name,
                    "llm_kind": va.kind,         # the rich taxonomy tag
                    "memex_kind": memex_kind,    # the directory kind
                    "page_rel_path": result["page_rel_path"],
                    "snapshot_path": result.get("snapshot_path"),
                    "log_note": log_note,
                }
            )
            counts[effective_op.lower()] = counts.get(effective_op.lower(), 0) + 1
            if log_note:
                logger.info(
                    "csi_persistence: %s — name=%r kind=%r → %s",
                    log_note,
                    effective_name,
                    va.kind,
                    result["page_rel_path"],
                )
        except Exception as exc:  # noqa: BLE001 — collect, don't crash the whole batch
            errors.append(
                {
                    "action_index": idx,
                    "name": va.name,
                    "kind": va.kind,
                    "intended_op": va.op,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            logger.exception(
                "csi_persistence: failed to apply VaultAction[%d] (name=%r, kind=%r)",
                idx,
                va.name,
                va.kind,
            )

    relations_written = _append_relations_log(vault_path, delta, packet_id=packet_id)

    return {
        "applied": applied,
        "errors": errors,
        "counts": counts,
        "relations_written": relations_written,
        "skipped_empty": skipped_empty,
    }


__all__ = [
    "apply_vault_delta_to_vault",
]
