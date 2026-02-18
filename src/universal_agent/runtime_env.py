"""Runtime environment normalization helpers."""

from __future__ import annotations

import os
import shutil
from typing import Any

_DEFAULT_PATH_SEGMENTS = (
    "/home/ua/.local/bin",
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
)


def normalize_path(current_path: str | None = None) -> str:
    existing = (current_path if current_path is not None else os.getenv("PATH", "")).strip()
    segments: list[str] = []
    seen: set[str] = set()

    def _add(path: str) -> None:
        value = (path or "").strip()
        if not value or value in seen:
            return
        seen.add(value)
        segments.append(value)

    for path in _DEFAULT_PATH_SEGMENTS:
        _add(path)
    for path in existing.split(":"):
        _add(path)

    return ":".join(segments)


def ensure_runtime_path() -> str:
    normalized = normalize_path()
    os.environ["PATH"] = normalized
    return normalized


def runtime_tool_status(tool_names: tuple[str, ...] = ("uv", "sqlite3")) -> dict[str, Any]:
    status: dict[str, Any] = {}
    for name in tool_names:
        resolved = shutil.which(name)
        status[name] = {
            "available": bool(resolved),
            "path": resolved,
        }
    return status
