"""Recent briefs index — single source of truth for Atlas prior-verdict awareness.

The index is a structured markdown document maintained at the path configured by
``UA_RECENT_BRIEFS_INDEX_PATH`` (default ``{WORKSPACES_DIR}/recent_briefs_index.md``).

It contains ``[SHIP]`` / ``[SKIP]`` / ``[DEFER]`` blocks representing recent
Atlas verdicts. Ship blocks are sourced from ``proactive_artifacts`` rows with
``verdict='ship'``; skip/defer blocks come from ``convergence_candidates`` rows
with non-ship verdicts.

Used by:
- Atlas's ``/evaluate-and-author-intel-brief`` skill (PR C) — reads the index
  to ground its evaluation in prior verdicts.
- The gateway feedback endpoint (PR B) — updates the ``operator_rating`` line
  on individual ship blocks when Kevin clicks thumbs-up/down.

This module is pure infrastructure: it does not call any LLM, does not write
artifact rows, and does not enforce schema migrations on other modules. It
only reads ``proactive_artifacts`` and ``convergence_candidates``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)


# ── Env helpers ────────────────────────────────────────────────────────


def _default_index_path() -> Path:
    """Return the default index path.

    Resolves ``UA_RECENT_BRIEFS_INDEX_PATH`` first; if unset, falls back to
    ``{WORKSPACES_DIR}/recent_briefs_index.md`` where ``WORKSPACES_DIR`` is
    resolved the same way ``gateway_server.py`` resolves it.
    """
    override = os.getenv("UA_RECENT_BRIEFS_INDEX_PATH", "").strip()
    if override:
        return Path(override).expanduser().resolve()

    workspaces_env = os.getenv("UA_WORKSPACES_DIR", "").strip()
    if workspaces_env:
        return (Path(workspaces_env).expanduser().resolve() / "recent_briefs_index.md")

    # Mirror gateway_server.py:284 — BASE_DIR / AGENT_RUN_WORKSPACES.
    base_dir = Path(__file__).resolve().parents[3]
    return base_dir / "AGENT_RUN_WORKSPACES" / "recent_briefs_index.md"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(text: str) -> Optional[datetime]:
    if not text:
        return None
    cleaned = text.strip()
    if not cleaned:
        return None
    try:
        # Accept "Z" suffix and other ISO-8601 shapes.
        if cleaned.endswith("Z"):
            cleaned = cleaned[:-1] + "+00:00"
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _json_loads(raw: Any, default: Any) -> Any:
    if isinstance(raw, (list, dict)):
        return raw
    if not raw:
        return default
    try:
        parsed = json.loads(raw)
        return parsed
    except (json.JSONDecodeError, TypeError, ValueError):
        return default


def _clean_entity_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError, ValueError):
            parsed = None
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
        # Bare CSV fallback.
        return [part.strip() for part in text.split(",") if part.strip()]
    return []


def _format_entities(entities: Iterable[str]) -> str:
    items = [e for e in entities if e]
    if not items:
        return "[]"
    quoted = [json.dumps(item, ensure_ascii=False) for item in items]
    return "[" + ", ".join(quoted) + "]"


# ── Block format ───────────────────────────────────────────────────────

_VALID_VERDICTS = {"ship", "skip", "defer"}

# Markdown separator between verdict blocks AND between header and first block.
_SEPARATOR = "---"


def _render_block(
    *,
    verdict: str,
    title: str,
    artifact_id: str,
    candidate_id: str,
    decided_at: str,
    thesis: str,
    key_entities: Iterable[str],
    ship_reasoning: str,
    operator_rating: Optional[int],
) -> str:
    """Render a single ``## [VERDICT] <title>`` block.

    Format matches §5.2 of the spec exactly so the markdown is both
    human-readable and grep-friendly.
    """
    verdict_token = verdict.lower().strip()
    if verdict_token not in _VALID_VERDICTS:
        verdict_token = "skip"
    tag = f"[{verdict_token.upper()}]"
    clean_title = (title or "").strip() or "(untitled)"
    rating_line = (
        "null" if operator_rating is None else str(int(operator_rating))
    )
    lines = [
        f"## {tag} {clean_title}",
        f"candidate_id: {candidate_id}",
    ]
    # Ship blocks include artifact_id; skip/defer blocks omit it (or render empty).
    if verdict_token == "ship":
        lines.append(f"artifact_id: {artifact_id}")
    lines.extend(
        [
            f"decided_at: {decided_at}",
            f"thesis: {(thesis or '').strip()}",
            f"key_entities: {_format_entities(key_entities)}",
            f"ship_reasoning: {(ship_reasoning or '').strip()}",
            f"operator_rating: {rating_line}",
        ]
    )
    return "\n".join(lines)


def _render_header(lookback_hours: int) -> str:
    return "\n".join(
        [
            "# Recent Intel Briefs Index",
            f"Last updated: {_now_iso()}",
            f"Lookback: {lookback_hours} hours",
        ]
    )


# ── DB read helpers ────────────────────────────────────────────────────


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
            (table,),
        ).fetchone()
        return row is not None
    except sqlite3.Error:
        return False


def _select_ship_artifacts(
    conn: sqlite3.Connection, *, since_iso: str, limit: int
) -> list[dict[str, Any]]:
    if not _table_exists(conn, "proactive_artifacts"):
        return []
    # Be defensive — verdict column was added by PR B's schema migration but
    # this function must not crash on a pre-migration DB.
    try:
        rows = conn.execute(
            """
            SELECT artifact_id, title, summary, metadata_json, feedback_json,
                   verdict, verdict_reasoning, updated_at, created_at
            FROM proactive_artifacts
            WHERE verdict = 'ship'
              AND updated_at >= ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (since_iso, max(1, int(limit))),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            row_dict = dict(row)
        except TypeError:
            row_dict = {k: row[k] for k in row.keys()} if hasattr(row, "keys") else {}
        out.append(row_dict)
    return out


def _select_skip_defer_candidates(
    conn: sqlite3.Connection, *, since_iso: str, limit: int
) -> list[dict[str, Any]]:
    if not _table_exists(conn, "convergence_candidates"):
        return []
    try:
        rows = conn.execute(
            """
            SELECT candidate_id, verdict, verdict_reasoning, artifact_id,
                   primary_topics_json, channel_names_json, evaluated_at,
                   detected_at, updated_at, metadata_json
            FROM convergence_candidates
            WHERE verdict IN ('skip', 'defer')
              AND COALESCE(NULLIF(evaluated_at, ''), updated_at) >= ?
            ORDER BY COALESCE(NULLIF(evaluated_at, ''), updated_at) DESC
            LIMIT ?
            """,
            (since_iso, max(1, int(limit))),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            row_dict = dict(row)
        except TypeError:
            row_dict = {k: row[k] for k in row.keys()} if hasattr(row, "keys") else {}
        out.append(row_dict)
    return out


def _ship_block_from_artifact(row: dict[str, Any]) -> str:
    metadata = _json_loads(row.get("metadata_json"), {}) or {}
    feedback = _json_loads(row.get("feedback_json"), {}) or {}
    thesis = str(metadata.get("thesis") or row.get("summary") or "").strip()
    key_entities = _clean_entity_list(metadata.get("key_entities"))
    ship_reasoning = str(
        metadata.get("ship_reasoning")
        or row.get("verdict_reasoning")
        or ""
    ).strip()
    last_score = feedback.get("last_score") if isinstance(feedback, dict) else None
    try:
        rating = int(last_score) if last_score is not None else None
    except (TypeError, ValueError):
        rating = None
    decided_at = str(metadata.get("decided_at") or row.get("updated_at") or "")
    return _render_block(
        verdict="ship",
        title=str(row.get("title") or "(untitled)"),
        artifact_id=str(row.get("artifact_id") or ""),
        candidate_id=str(metadata.get("candidate_id") or ""),
        decided_at=decided_at,
        thesis=thesis,
        key_entities=key_entities,
        ship_reasoning=ship_reasoning,
        operator_rating=rating,
    )


def _candidate_block(row: dict[str, Any]) -> str:
    metadata = _json_loads(row.get("metadata_json"), {}) or {}
    primary_topics = _json_loads(row.get("primary_topics_json"), []) or []
    channels = _json_loads(row.get("channel_names_json"), []) or []
    title_parts = list(primary_topics)[:2] or list(channels)[:2]
    title = " · ".join(str(p) for p in title_parts) or str(metadata.get("title") or "(unnamed cluster)")
    thesis = str(metadata.get("thesis") or "").strip()
    key_entities = _clean_entity_list(metadata.get("key_entities") or channels)
    ship_reasoning = str(row.get("verdict_reasoning") or metadata.get("ship_reasoning") or "").strip()
    decided_at = str(row.get("evaluated_at") or row.get("updated_at") or "")
    return _render_block(
        verdict=str(row.get("verdict") or "skip"),
        title=title,
        artifact_id=str(row.get("artifact_id") or ""),
        candidate_id=str(row.get("candidate_id") or ""),
        decided_at=decided_at,
        thesis=thesis,
        key_entities=key_entities,
        ship_reasoning=ship_reasoning,
        operator_rating=None,
    )


# ── Public API ─────────────────────────────────────────────────────────


def build_recent_briefs_index(
    conn: sqlite3.Connection,
    *,
    lookback_hours: int = 48,
    limit: int = 200,
) -> str:
    """Build the markdown index string from DB rows. Does not write to disk.

    Includes only entries with ``updated_at`` (or ``evaluated_at``) within the
    last ``lookback_hours`` and caps total entries at ``limit`` across both
    ship and skip/defer sources.
    """
    horizon = datetime.now(timezone.utc) - timedelta(hours=max(1, int(lookback_hours)))
    since_iso = horizon.isoformat()
    cap = max(1, int(limit))
    ship_rows = _select_ship_artifacts(conn, since_iso=since_iso, limit=cap)
    candidate_rows = _select_skip_defer_candidates(conn, since_iso=since_iso, limit=cap)

    blocks: list[tuple[str, str]] = []
    for r in ship_rows:
        decided = str(r.get("updated_at") or r.get("created_at") or "")
        blocks.append((decided, _ship_block_from_artifact(r)))
    for r in candidate_rows:
        decided = str(r.get("evaluated_at") or r.get("updated_at") or "")
        blocks.append((decided, _candidate_block(r)))

    # Sort newest decisions first, then cap.
    blocks.sort(key=lambda pair: pair[0], reverse=True)
    blocks = blocks[:cap]

    parts: list[str] = [_render_header(lookback_hours), "", _SEPARATOR, ""]
    if not blocks:
        parts.append("_No verdicts in the lookback window._")
        parts.append("")
    else:
        for _, block in blocks:
            parts.append(block)
            parts.append("")
            parts.append(_SEPARATOR)
            parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def write_recent_briefs_index(
    conn: sqlite3.Connection,
    *,
    index_path: Optional[Path] = None,
    lookback_hours: int = 48,
    limit: int = 200,
) -> Path:
    """Build and atomically write the index file. Returns the final path.

    Atomic via ``tmp file + os.replace`` so a concurrent reader either sees
    the previous full file or the new full file — never a partial write.
    """
    path = (index_path or _default_index_path()).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = build_recent_briefs_index(conn, lookback_hours=lookback_hours, limit=limit)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return path


def _max_index_entries() -> int:
    """Hard cap on the number of verdict blocks kept on disk.

    Env-overridable via ``UA_RECENT_BRIEFS_INDEX_MAX_ENTRIES`` (default 60,
    roughly the ~48h volume the lookback consumers actually want). A value
    <= 0 disables self-pruning (legacy append-forever behavior).
    """
    try:
        return int(os.getenv("UA_RECENT_BRIEFS_INDEX_MAX_ENTRIES", "60") or "60")
    except (TypeError, ValueError):
        return 60


def _prune_index_text(text: str, *, max_entries: int) -> str:
    """Keep only the most-recent ``max_entries`` verdict blocks.

    Blocks are ordered oldest->newest in the file (Atlas appends newest last),
    so we keep the tail. Re-renders a fresh header (fixing the stale
    ``Last updated`` line) and re-emits the standard separator framing. Returns
    the *same* object when already within budget or when pruning is disabled,
    so callers can identity-check to keep the cheap-append fast path.

    Hardened against an embedded ``---`` inside a block body: we keep whole
    inter-separator segments that contain ``## [`` (mirroring
    ``update_operator_rating_in_index``'s separator-preserving rewrite) rather
    than reconstructing from a single fragment, so a stray separator inside a
    block body cannot drop that block's trailing fields.
    """
    if max_entries <= 0:
        return text
    segments = re.split(r"(?m)^---\s*$", text)
    block_segs = [seg for seg in segments if "## [" in seg]
    if len(block_segs) <= max_entries:
        return text
    kept = block_segs[-max_entries:]
    parts: list[str] = [_render_header(48), "", _SEPARATOR, ""]
    for seg in kept:
        parts.append(seg.strip())
        parts.append("")
        parts.append(_SEPARATOR)
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def append_verdict_to_index(
    *,
    index_path: Optional[Path] = None,
    artifact_id: str,
    candidate_id: str,
    verdict: str,
    thesis: str,
    key_entities: Iterable[str],
    ship_reasoning: str,
    operator_rating: Optional[int],
    decided_at: str,
    title: str = "",
) -> None:
    """Append a single verdict block to the index file.

    Fast incremental update used by Atlas after each ship/skip/defer decision.
    Creates the index file (with a fresh header) if it does not exist. After
    appending, self-prunes to the most-recent
    ``UA_RECENT_BRIEFS_INDEX_MAX_ENTRIES`` blocks (default 60) so the file
    cannot grow without bound. The prune fires on *every* append whenever the
    file is over budget (not only when this append crossed it), so a
    pre-bloated file self-heals on the next write.
    """
    path = (index_path or _default_index_path()).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    block = _render_block(
        verdict=verdict,
        title=title or "(untitled)",
        artifact_id=artifact_id,
        candidate_id=candidate_id,
        decided_at=decided_at or _now_iso(),
        thesis=thesis,
        key_entities=list(key_entities or []),
        ship_reasoning=ship_reasoning,
        operator_rating=operator_rating,
    )

    if not path.exists():
        header = _render_header(48)
        body = "\n".join([header, "", _SEPARATOR, "", block, "", _SEPARATOR, ""])
        path.write_text(body, encoding="utf-8")
        return

    existing = path.read_text(encoding="utf-8")
    if not existing.rstrip().endswith(_SEPARATOR):
        # Defensive — guarantee a separator before the new block.
        addition = "\n" + _SEPARATOR + "\n\n" + block + "\n\n" + _SEPARATOR + "\n"
    else:
        addition = "\n" + block + "\n\n" + _SEPARATOR + "\n"

    new_text = existing + addition
    pruned = _prune_index_text(new_text, max_entries=_max_index_entries())
    if pruned is new_text:
        # Within budget — keep the cheap append (no full rewrite).
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(addition)
        return
    # Over budget — atomically rewrite the pruned (header-refreshed) file.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(pruned, encoding="utf-8")
    os.replace(tmp, path)


def _looks_corrupted(text: str) -> bool:
    """Heuristic check that the index has the expected structure."""
    if not text or not text.strip():
        return True
    if "# Recent Intel Briefs Index" not in text:
        return True
    if _SEPARATOR not in text:
        return True
    return False


def read_index_or_fallback(
    conn: sqlite3.Connection,
    *,
    index_path: Optional[Path] = None,
    lookback_hours: int = 48,
    limit: int = 200,
) -> str:
    """Read the index file. Rebuild from DB on missing/corrupted file.

    Never raises — corrupted/IO errors fall through to a DB-sourced rebuild.
    """
    path = (index_path or _default_index_path()).expanduser()
    try:
        if path.exists():
            text = path.read_text(encoding="utf-8")
            if not _looks_corrupted(text):
                return text
            logger.warning(
                "recent_briefs_index: %s looks corrupted; rebuilding from DB", path
            )
        else:
            logger.info(
                "recent_briefs_index: %s missing; rebuilding from DB", path
            )
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning(
            "recent_briefs_index: failed to read %s (%s); rebuilding from DB",
            path,
            exc,
        )
    return build_recent_briefs_index(
        conn, lookback_hours=lookback_hours, limit=limit
    )


_OPERATOR_RATING_LINE_RE = re.compile(
    r"^(operator_rating:\s*).*$", re.MULTILINE
)


def update_operator_rating_in_index(
    *,
    index_path: Optional[Path] = None,
    artifact_id: str,
    rating: int,
) -> None:
    """Replace the ``operator_rating`` line on the SHIP block matching ``artifact_id``.

    Idempotent. If the artifact_id is not found in any ``[SHIP]`` block, logs
    a warning and no-ops.
    """
    path = (index_path or _default_index_path()).expanduser()
    if not path.exists():
        logger.warning(
            "recent_briefs_index: cannot update rating, %s missing", path
        )
        return
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning(
            "recent_briefs_index: failed to read %s for rating update (%s)",
            path,
            exc,
        )
        return

    target = str(artifact_id or "").strip()
    if not target:
        return

    # Split into blocks delimited by lines that are exactly the separator.
    # We surgically rewrite only the matching ship block's operator_rating
    # line — leaving everything else byte-identical.
    blocks = re.split(r"(?m)^---\s*$", text)
    changed = False
    for idx, block in enumerate(blocks):
        if "[SHIP]" not in block:
            continue
        if f"artifact_id: {target}" not in block:
            continue
        replacement_value = str(int(rating))
        new_block, n_subs = _OPERATOR_RATING_LINE_RE.subn(
            lambda m: m.group(1) + replacement_value, block, count=1
        )
        if n_subs > 0:
            blocks[idx] = new_block
            changed = True
            break

    if not changed:
        logger.warning(
            "recent_briefs_index: artifact_id=%s not found in any SHIP block of %s",
            target,
            path,
        )
        return

    new_text = "---".join(blocks)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(new_text, encoding="utf-8")
    os.replace(tmp, path)


__all__ = [
    "append_verdict_to_index",
    "build_recent_briefs_index",
    "read_index_or_fallback",
    "update_operator_rating_in_index",
    "write_recent_briefs_index",
]
