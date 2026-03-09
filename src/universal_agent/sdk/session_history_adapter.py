from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _safe_getattr(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _to_iso_utc_from_epoch_ms(epoch_ms: Any) -> Optional[str]:
    try:
        ts_ms = int(epoch_ms)
    except Exception:
        return None
    if ts_ms <= 0:
        return None
    dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
    return dt.isoformat()


def _coerce_message_preview(message: Any, *, max_chars: int = 240) -> str:
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict):
                    text = block.get("text")
                    if isinstance(text, str) and text.strip():
                        parts.append(text.strip())
                elif isinstance(block, str) and block.strip():
                    parts.append(block.strip())
            if parts:
                merged = " ".join(parts)
            else:
                merged = str(message)
        else:
            merged = str(content) if content is not None else str(message)
    else:
        merged = str(message)
    compact = " ".join(merged.split())
    if len(compact) > max_chars:
        return f"{compact[: max_chars - 1].rstrip()}…"
    return compact


def sdk_history_available() -> bool:
    try:
        import claude_agent_sdk  # type: ignore

        return bool(
            hasattr(claude_agent_sdk, "list_sessions")
            and hasattr(claude_agent_sdk, "get_session_messages")
        )
    except Exception:
        return False


def list_sessions(
    *,
    directory: Optional[str] = None,
    limit: int = 100,
    include_worktrees: bool = True,
) -> list[dict[str, Any]]:
    if not sdk_history_available():
        return []
    try:
        import claude_agent_sdk  # type: ignore

        entries = claude_agent_sdk.list_sessions(
            directory=directory,
            limit=limit,
            include_worktrees=include_worktrees,
        )
    except Exception as exc:
        logger.warning("sdk_session_history_list_failed: %s", exc)
        return []

    normalized: list[dict[str, Any]] = []
    for row in entries or []:
        session_id = str(_safe_getattr(row, "session_id", "") or "").strip()
        if not session_id:
            continue
        cwd = _safe_getattr(row, "cwd")
        summary = _safe_getattr(row, "summary") or _safe_getattr(row, "first_prompt")
        normalized.append(
            {
                "session_id": session_id,
                "summary": str(summary or "").strip(),
                "first_prompt": str(_safe_getattr(row, "first_prompt", "") or "").strip(),
                "custom_title": str(_safe_getattr(row, "custom_title", "") or "").strip(),
                "git_branch": str(_safe_getattr(row, "git_branch", "") or "").strip(),
                "cwd": str(cwd or "").strip(),
                "file_size": int(_safe_getattr(row, "file_size", 0) or 0),
                "last_modified_epoch_ms": int(_safe_getattr(row, "last_modified", 0) or 0),
                "last_modified": _to_iso_utc_from_epoch_ms(_safe_getattr(row, "last_modified")),
                "workspace_dir": str(cwd or "").strip(),
                "source": "sdk_history",
            }
        )
    return normalized


def get_session_messages(
    session_id: str,
    *,
    directory: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    if not session_id or not sdk_history_available():
        return []
    try:
        import claude_agent_sdk  # type: ignore

        rows = claude_agent_sdk.get_session_messages(
            session_id=session_id,
            directory=directory,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        logger.warning(
            "sdk_session_history_messages_failed session_id=%s error=%s",
            session_id,
            exc,
        )
        return []

    normalized: list[dict[str, Any]] = []
    for row in rows or []:
        message = _safe_getattr(row, "message", {})
        normalized.append(
            {
                "type": str(_safe_getattr(row, "type", "") or ""),
                "uuid": str(_safe_getattr(row, "uuid", "") or ""),
                "session_id": str(_safe_getattr(row, "session_id", session_id) or session_id),
                "parent_tool_use_id": _safe_getattr(row, "parent_tool_use_id"),
                "message": message,
                "preview": _coerce_message_preview(message),
            }
        )
    return normalized


def list_session_summaries_for_workspace(
    workspace_root: str | Path,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    root = Path(workspace_root).resolve()
    summaries = list_sessions(directory=str(root), limit=limit, include_worktrees=True)
    # Keep only sessions that map to this workspace root.
    filtered: list[dict[str, Any]] = []
    for row in summaries:
        cwd = Path(str(row.get("cwd") or "")).resolve() if row.get("cwd") else None
        if cwd is not None:
            try:
                cwd.relative_to(root)
                filtered.append(row)
                continue
            except Exception:
                pass
        # Include sessions with no cwd when query is explicitly rooted.
        if not row.get("cwd"):
            filtered.append(row)
    return filtered
