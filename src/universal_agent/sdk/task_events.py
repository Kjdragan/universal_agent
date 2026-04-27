from __future__ import annotations

from typing import Any, Optional

_TASK_STARTED_CLASS_NAMES = {"TaskStartedMessage", "TaskStarted"}
_TASK_PROGRESS_CLASS_NAMES = {"TaskProgressMessage", "TaskProgress"}
_TASK_NOTIFICATION_CLASS_NAMES = {"TaskNotificationMessage", "TaskNotification"}


def _to_json_compatible(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _to_json_compatible(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_json_compatible(v) for v in value]
    if hasattr(value, "__dict__"):
        raw = getattr(value, "__dict__", {})
        if isinstance(raw, dict):
            return {str(k): _to_json_compatible(v) for k, v in raw.items() if not str(k).startswith("_")}
    return str(value)


def extract_typed_task_payload(msg: Any) -> Optional[dict[str, Any]]:
    class_name = msg.__class__.__name__
    lifecycle: str
    if class_name in _TASK_STARTED_CLASS_NAMES:
        lifecycle = "started"
    elif class_name in _TASK_PROGRESS_CLASS_NAMES:
        lifecycle = "progress"
    elif class_name in _TASK_NOTIFICATION_CLASS_NAMES:
        lifecycle = "notification"
    else:
        return None

    payload: dict[str, Any] = {
        "message_type": class_name,
        "task_lifecycle": lifecycle,
    }
    for key in (
        "task_id",
        "description",
        "status",
        "summary",
        "session_id",
        "tool_use_id",
        "task_type",
        "last_tool_name",
        "output_file",
        "subtype",
        "uuid",
    ):
        value = getattr(msg, key, None)
        if value is not None:
            payload[key] = _to_json_compatible(value)

    data = getattr(msg, "data", None)
    if data is not None:
        payload["data"] = _to_json_compatible(data)
    usage = getattr(msg, "usage", None)
    if usage is not None:
        payload["usage"] = _to_json_compatible(usage)

    return payload
