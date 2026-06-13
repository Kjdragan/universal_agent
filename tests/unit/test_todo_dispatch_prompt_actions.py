"""P6 guard: TODO_DISPATCH_PROMPT only instructs bridge-exposed lifecycle actions.

The 2026-06-10 live failure: the prompt told Simone to use ``redirect_to``,
which ``task_hub.VALID_ACTIONS`` accepts but the tool she actually holds —
``tools/task_hub_bridge.task_hub_task_action`` — rejects ("unsupported
action"), and ``mission_guardrails.py`` never accepted as a lifecycle
mutation. Fixed to ``delegate`` (bridge-exposed, guardrail-accepted, sets
``delegated`` so the row can't be re-claimed mid-mission). This test pins
prompt↔tool-surface agreement mechanically so the next prompt edit can't
reintroduce an un-callable action.
"""

from __future__ import annotations

import re

from universal_agent.services.todo_dispatch_service import TODO_DISPATCH_PROMPT
from universal_agent.tools.task_hub_bridge import _LIFECYCLE_ACTIONS


def test_redirect_to_purged_from_prompt():
    assert "redirect_to" not in TODO_DISPATCH_PROMPT


def test_delegate_is_the_instructed_release_action():
    assert 'action="delegate"' in TODO_DISPATCH_PROMPT


def test_every_action_literal_is_bridge_exposed():
    literals = re.findall(r'action="([a-z_]+)"', TODO_DISPATCH_PROMPT)
    assert literals, "prompt should carry at least one action=\"...\" literal"
    for action in literals:
        assert action in _LIFECYCLE_ACTIONS, (
            f"TODO_DISPATCH_PROMPT instructs action={action!r} which "
            f"task_hub_task_action does not expose ({sorted(_LIFECYCLE_ACTIONS)})"
        )


def test_every_action_list_member_is_bridge_exposed():
    # Also pin the `<one of: a, b, c>` list forms the prompt uses.
    for list_body in re.findall(r"action=<one of: ([^>]+)>", TODO_DISPATCH_PROMPT):
        for action in (token.strip().strip("`") for token in list_body.split(",")):
            assert action in _LIFECYCLE_ACTIONS, (
                f"TODO_DISPATCH_PROMPT lists action {action!r} which "
                f"task_hub_task_action does not expose"
            )
