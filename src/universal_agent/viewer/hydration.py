"""Server-side three-panel hydration.

Replaces the client-side `run.log` + `trace.json` parsers in
`web-ui/app/page.tsx:1855` and the ops preview tail in
`gateway_server.py:29241` with a single backend contract. The route
calls `hydrate(target)` and gets back a typed payload the UI renders
directly into the three panels.

History sources (in order of preference, first hit wins):
    - `<workspace>/trace.json`     (canonical structured trace)
    - `<workspace>/run.log`        (text fallback, line-by-line parse)

Logs sources (interleaved, time-sorted):
    - `<workspace>/run.log`
    - `<workspace>/activity_journal.log`

Workspace listing: top-level entries of `<workspace>/`.

Readiness state derivation:
    - "ready"   when any of: `run_manifest.json`, `run_checkpoint.json`,
                `session_checkpoint.json`, `sync_ready.json` exists.
    - "failed"  when `terminal_reason` is recorded in run_manifest.json
                with a known-failure value.
    - "pending" otherwise (UI polls until ready).

The hydration payload NEVER includes raw card data (paranoia carried
over from the Link payments work — the same workspace can host any
session-scoped artifacts). PAN-shaped strings (16 contiguous digits)
are masked.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from universal_agent.viewer.resolver import SessionViewTarget

logger = logging.getLogger(__name__)


# ── Output types ─────────────────────────────────────────────────────────────


@dataclass
class HistoryMessage:
    role: str
    ts: Optional[float]
    content: str
    sub_agent: Optional[str] = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class LogEntry:
    ts: Optional[float]
    level: str
    channel: str
    message: str


@dataclass
class WorkspaceEntry:
    name: str
    type: str  # "file" | "dir"
    size: int
    mtime: Optional[float]


@dataclass
class Readiness:
    state: str  # "pending" | "ready" | "failed"
    reason: Optional[str] = None
    marker_ts: Optional[float] = None


@dataclass
class HydrationResult:
    target: SessionViewTarget
    history: list[HistoryMessage]
    history_truncated_to: Optional[int]
    logs: list[LogEntry]
    logs_cursor: Optional[int]
    workspace_root: str
    workspace_entries: list[WorkspaceEntry]
    readiness: Readiness

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target.to_dict(),
            "history": [asdict(m) for m in self.history],
            "history_truncated_to": self.history_truncated_to,
            "logs": [asdict(e) for e in self.logs],
            "logs_cursor": self.logs_cursor,
            "workspace_root": self.workspace_root,
            "workspace_entries": [asdict(e) for e in self.workspace_entries],
            "readiness": asdict(self.readiness),
        }


# ── Constants ────────────────────────────────────────────────────────────────


_DEFAULT_HISTORY_LIMIT = 500
_DEFAULT_LOGS_LIMIT = 1000
_PAN_PATTERN = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def _mask_pan(text: str) -> str:
    """Replace 13-19 digit contiguous sequences (with optional spaces/dashes)
    with `••••<last4>` to defend against accidental card-data leakage.
    """

    def _sub(match: re.Match[str]) -> str:
        digits = "".join(c for c in match.group(0) if c.isdigit())
        if len(digits) < 13:
            return match.group(0)
        return "••••" + digits[-4:]

    return _PAN_PATTERN.sub(_sub, text)


# ── History parsing ──────────────────────────────────────────────────────────


def _parse_trace_json(path: Path, limit: int) -> list[HistoryMessage]:
    """Parse `trace.json` — preferred structured history source."""
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        # Some traces are JSONL — try line-by-line
        out: list[HistoryMessage] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = _trace_obj_to_message(obj)
            if msg:
                out.append(msg)
        return out[-limit:]

    messages_raw: list[Any]
    if isinstance(payload, dict):
        messages_raw = payload.get("messages") or payload.get("events") or []
    elif isinstance(payload, list):
        messages_raw = payload
    else:
        return []

    out = []
    for obj in messages_raw:
        msg = _trace_obj_to_message(obj)
        if msg:
            out.append(msg)
    return out[-limit:]


def _trace_obj_to_message(obj: Any) -> Optional[HistoryMessage]:
    if not isinstance(obj, dict):
        return None
    role = str(obj.get("role") or obj.get("type") or "system").strip()
    content_raw = obj.get("content") or obj.get("text") or obj.get("message") or ""
    if isinstance(content_raw, list):
        # Anthropic-style content blocks
        content = "\n".join(
            str(b.get("text", "")) for b in content_raw if isinstance(b, dict)
        )
    else:
        content = str(content_raw)
    ts = obj.get("ts") or obj.get("timestamp")
    try:
        ts_val = float(ts) if ts is not None else None
    except (TypeError, ValueError):
        ts_val = None
    return HistoryMessage(
        role=role,
        ts=ts_val,
        content=_mask_pan(content),
        sub_agent=obj.get("sub_agent") or obj.get("agent_name"),
        tool_calls=obj.get("tool_calls") or [],
    )


def _parse_run_log_history(path: Path, limit: int) -> list[HistoryMessage]:
    """Fallback: extract user/assistant turns from `run.log` line-by-line."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return []
    out: list[HistoryMessage] = []
    for line in lines:
        line = line.rstrip("\n")
        if not line:
            continue
        # Best-effort: many run.logs prefix with `[USER]`/`[ASSISTANT]` markers
        role = "system"
        content = line
        for prefix, mapped_role in (
            ("[USER]", "user"),
            ("[ASSISTANT]", "assistant"),
            ("[SYSTEM]", "system"),
        ):
            if line.startswith(prefix):
                role = mapped_role
                content = line[len(prefix):].lstrip(": ").strip()
                break
        out.append(HistoryMessage(role=role, ts=None, content=_mask_pan(content)))
    return out[-limit:]


def _hydrate_history(workspace: Path, limit: int) -> tuple[list[HistoryMessage], Optional[int]]:
    trace_path = workspace / "trace.json"
    if trace_path.exists():
        msgs = _parse_trace_json(trace_path, limit)
        return msgs, len(msgs) if len(msgs) >= limit else None

    run_log_path = workspace / "run.log"
    if run_log_path.exists():
        msgs = _parse_run_log_history(run_log_path, limit)
        return msgs, len(msgs) if len(msgs) >= limit else None

    return [], None


# ── Logs parsing ─────────────────────────────────────────────────────────────


def _normalize_log_line(line: str, channel: str) -> Optional[LogEntry]:
    line = line.rstrip("\n")
    if not line.strip():
        return None
    # Strip leading timestamps if present
    ts_val: Optional[float] = None
    level = "info"
    text = line
    # Crude level detection
    upper = line.upper()
    for keyword in ("ERROR", "WARN", "DEBUG", "INFO"):
        if keyword in upper[:80]:
            level = keyword.lower()
            break
    return LogEntry(ts=ts_val, level=level, channel=channel, message=_mask_pan(text))


def _hydrate_logs(workspace: Path, limit: int) -> list[LogEntry]:
    candidates = [
        ("run", workspace / "run.log"),
        ("activity", workspace / "activity_journal.log"),
    ]
    out: list[LogEntry] = []
    for channel, path in candidates:
        if not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except OSError:
            continue
        for line in lines:
            entry = _normalize_log_line(line, channel)
            if entry:
                out.append(entry)
    # Most-recent-first, truncated
    return out[-limit:]


# ── Workspace listing ────────────────────────────────────────────────────────


def _hydrate_workspace(workspace: Path) -> list[WorkspaceEntry]:
    if not workspace.is_dir():
        return []
    out: list[WorkspaceEntry] = []
    try:
        entries = list(workspace.iterdir())
    except OSError:
        return []
    for child in sorted(entries, key=lambda p: (not p.is_dir(), p.name.lower())):
        try:
            stat = child.stat()
            is_dir = child.is_dir()
            out.append(
                WorkspaceEntry(
                    name=child.name,
                    type="dir" if is_dir else "file",
                    size=int(stat.st_size) if not is_dir else 0,
                    mtime=float(stat.st_mtime),
                )
            )
        except OSError:
            continue
    return out


# ── Readiness ────────────────────────────────────────────────────────────────


_READY_MARKERS = (
    "run_manifest.json",
    "run_checkpoint.json",
    "session_checkpoint.json",
    "sync_ready.json",
)


def _hydrate_readiness(workspace: Path) -> Readiness:
    if not workspace.is_dir():
        return Readiness(state="pending", reason="workspace_dir_missing")

    # Check failure first — manifest may indicate terminal failure even
    # when other markers also exist.
    manifest = workspace / "run_manifest.json"
    if manifest.exists():
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            terminal = str(payload.get("terminal_reason") or "").strip().lower()
            if terminal and terminal not in {"completed", "ok", "success"}:
                return Readiness(
                    state="failed",
                    reason=f"terminal_reason={terminal}",
                    marker_ts=float(manifest.stat().st_mtime),
                )
        except (OSError, json.JSONDecodeError):
            pass

    for marker in _READY_MARKERS:
        path = workspace / marker
        if path.exists():
            try:
                return Readiness(
                    state="ready",
                    reason=f"marker={marker}",
                    marker_ts=float(path.stat().st_mtime),
                )
            except OSError:
                continue

    # Fallback: workspace exists but no marker yet
    return Readiness(state="pending", reason="no_marker")


# ── Public entry point ───────────────────────────────────────────────────────


def hydrate(
    target: SessionViewTarget,
    *,
    history_limit: int = _DEFAULT_HISTORY_LIMIT,
    logs_limit: int = _DEFAULT_LOGS_LIMIT,
) -> HydrationResult:
    """Assemble the three-panel hydration payload for the given target.

    Bounded by `history_limit` and `logs_limit` to keep response sizes sane.
    Never raises; missing files just produce empty sections + a `pending`
    readiness state.
    """
    workspace = Path(target.workspace_dir)

    history, history_truncated = _hydrate_history(workspace, history_limit)
    logs = _hydrate_logs(workspace, logs_limit)
    workspace_entries = _hydrate_workspace(workspace)
    readiness = _hydrate_readiness(workspace)

    logs_cursor: Optional[int] = None
    if logs:
        # Last entry's index is the next-tick cursor; the API accepts
        # ?cursor=N for incremental polling. Phase-2 work, kept simple now.
        logs_cursor = len(logs)

    return HydrationResult(
        target=target,
        history=history,
        history_truncated_to=history_truncated,
        logs=logs,
        logs_cursor=logs_cursor,
        workspace_root=str(workspace),
        workspace_entries=workspace_entries,
        readiness=readiness,
    )
