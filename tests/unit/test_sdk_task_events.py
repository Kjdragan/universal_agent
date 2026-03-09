from __future__ import annotations

from universal_agent.sdk.task_events import extract_typed_task_payload


class TaskStartedMessage:
    def __init__(self):
        self.task_id = "task_1"
        self.description = "Boot task"
        self.uuid = "u1"
        self.session_id = "s1"
        self.tool_use_id = "tu1"
        self.task_type = "background"
        self.data = {"percent": 0}
        self.subtype = "task_started"


class TaskProgressMessage:
    def __init__(self):
        self.task_id = "task_1"
        self.description = "Halfway"
        self.uuid = "u2"
        self.session_id = "s1"
        self.tool_use_id = "tu1"
        self.usage = {"input_tokens": 10, "output_tokens": 5}
        self.last_tool_name = "Read"
        self.data = {"percent": 50}
        self.subtype = "task_progress"


class TaskNotificationMessage:
    def __init__(self):
        self.task_id = "task_1"
        self.status = "completed"
        self.output_file = "/tmp/out.md"
        self.summary = "Done"
        self.uuid = "u3"
        self.session_id = "s1"
        self.tool_use_id = "tu1"
        self.usage = {"input_tokens": 30, "output_tokens": 15}
        self.data = {"percent": 100}
        self.subtype = "task_notification"


def test_extract_typed_task_payload_started():
    msg = TaskStartedMessage()
    payload = extract_typed_task_payload(msg)
    assert payload is not None
    assert payload["task_lifecycle"] == "started"
    assert payload["task_id"] == "task_1"
    assert payload["session_id"] == "s1"


def test_extract_typed_task_payload_progress():
    msg = TaskProgressMessage()
    payload = extract_typed_task_payload(msg)
    assert payload is not None
    assert payload["task_lifecycle"] == "progress"
    assert payload["last_tool_name"] == "Read"
    assert payload["usage"]["input_tokens"] == 10


def test_extract_typed_task_payload_notification():
    msg = TaskNotificationMessage()
    payload = extract_typed_task_payload(msg)
    assert payload is not None
    assert payload["task_lifecycle"] == "notification"
    assert payload["status"] == "completed"
    assert payload["summary"] == "Done"


def test_extract_typed_task_payload_unknown_returns_none():
    class SomethingElse:
        pass

    assert extract_typed_task_payload(SomethingElse()) is None
