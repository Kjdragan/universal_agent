"""The desktop guards must stay wired from TRACKED settings.

Both guards used to be wired only from the gitignored
``.claude/settings.local.json``. git never copies that file into a
``git worktree add`` tree, so the guards were silently absent in exactly
the workflow CLAUDE.md tells agents to use. Wiring now lives in the
tracked ``.claude/settings.json``; desktop-only scoping comes from each
hook's ``/opt/universal_agent`` runtime-host short-circuit instead.

This test fails if either half regresses.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SETTINGS = REPO_ROOT / ".claude" / "settings.json"
GUARDS = ("guard-fresh-branch.sh", "guard-no-timer-install.sh")


def _pretooluse_commands() -> str:
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    return json.dumps(settings.get("hooks", {}).get("PreToolUse", []))


@pytest.mark.parametrize("guard", GUARDS)
def test_guard_wired_in_tracked_settings(guard: str) -> None:
    assert guard in _pretooluse_commands(), (
        f"{guard} is not wired in tracked .claude/settings.json — it would be "
        "absent in every git worktree."
    )


@pytest.mark.parametrize("guard", GUARDS)
def test_guard_has_runtime_host_short_circuit(guard: str) -> None:
    hook = REPO_ROOT / ".claude" / "hooks" / guard
    assert hook.exists(), f"{guard} is missing"
    body = hook.read_text(encoding="utf-8")
    assert "/opt/universal_agent" in body, (
        f"{guard} lost its runtime-host short-circuit; it would now fire on the "
        "VPS, where these operations are correct."
    )
