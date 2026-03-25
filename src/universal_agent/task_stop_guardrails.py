from __future__ import annotations

import re
from typing import Any, Optional

_TASK_STOP_PLACEHOLDER_IDS = {
    "",
    "*",
    "all",
    "any",
    "every",
    "none",
    "null",
    "n/a",
    "na",
    "unknown",
    "task",
    "taskstop",
    "task-stop",
    "dummy",
    "dummy-stop",
    "placeholder",
    "example",
    "test",
    "all-tasks",
    "stop-all",
    "cancel-all",
}

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12}$"
)
_OPAQUE_TASK_PREFIX_RE = re.compile(r"^(?:task|bg|background)_[0-9A-Z]{10,}$")
_TOOLU_RE = re.compile(r"^toolu_[A-Za-z0-9_]{16,}$")


def extract_task_stop_id(tool_input: dict[str, Any]) -> str:
    for key in ("task_id", "id", "target_task_id"):
        value = tool_input.get(key)
        if value is None:
            continue
        return str(value).strip()
    return ""


def task_stop_rejection_reason(task_id: str) -> Optional[str]:
    clean_id = str(task_id or "").strip()
    if not clean_id:
        return "Missing `task_id`."

    lowered = clean_id.lower()
    if "," in clean_id:
        return "Multiple task IDs are not supported in a single call."
    if lowered in _TASK_STOP_PLACEHOLDER_IDS:
        return f"Invalid placeholder `task_id` ({clean_id!r})."
    if lowered.startswith(("session_", "run_")):
        return f"Invalid session/run identifier used as task_id ({clean_id!r})."

    if _UUID_RE.fullmatch(clean_id):
        return None
    if _OPAQUE_TASK_PREFIX_RE.fullmatch(clean_id):
        return None
    if _TOOLU_RE.fullmatch(clean_id):
        return None

    body = clean_id
    for prefix in ("task_", "bg_", "background_"):
        if lowered.startswith(prefix):
            body = clean_id[len(prefix) :]
            break

    if len(body) < 10:
        return (
            f"Untrusted `task_id` ({clean_id!r}): too short. "
            "Real SDK task IDs are opaque tokens."
        )

    if re.fullmatch(r"[a-z]+(?:_[a-z0-9]+)+", body.lower()):
        if re.search(r"[a-z]+_[a-z]+", body.lower()):
            return (
                f"Untrusted `task_id` ({clean_id!r}): human-readable, human-composed. "
                "Real SDK task IDs are opaque tokens, not word-based labels."
            )
        return (
            f"Untrusted `task_id` ({clean_id!r}): human-readable. "
            "Real SDK task IDs are opaque tokens, not descriptive names."
        )

    if re.search(r"[a-z]{3,}", body):
        return (
            f"Untrusted `task_id` ({clean_id!r}): human-readable. "
            "Real SDK task IDs are opaque tokens, not descriptive names."
        )

    return None
