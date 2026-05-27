"""Self-briefing prompt + artifact helpers for autonomous VP missions.

Implements the Python side of the ``self-brief-and-attest`` skill (see
``.claude/skills/self-brief-and-attest/SKILL.md``). Provides:

- ``build_self_briefing_prompt`` — prepends the self-briefing directive to
  a VP's normal mission prompt; called by the CLI client before launching
  the work subprocess.
- ``is_goal_eligible_mission`` — decides whether a mission gets the full
  /goal artifact set (BRIEF.md + ACCEPTANCE.md + goal_condition.txt) vs
  the minimal set (BRIEF.md only).
- ``vp_goal_enabled`` — reads the ``UA_VP_GOAL_ENABLED`` feature flag.
- ``read_goal_condition`` — reads + validates ``goal_condition.txt`` after
  the briefing turn produces it.
- ``check_completion_attestation`` — verifies ``COMPLETION.md`` exists
  before the worker calls ``finalize_vp_mission(completed)``.

Per PRD § 5.1, 5.2, 5.5.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Hard upper bound for goal_condition.txt per Anthropic /goal docs.
GOAL_CONDITION_MAX_CHARS = 4000

# Source_kinds eligible for /goal-driven completion loops. Per PRD § 3 decision 1.
GOAL_ELIGIBLE_SOURCE_KINDS = frozenset({
    "cody_demo_task",
    "cody_scaffold_request",
    "tutorial_build",
    "tutorial_build_task",
})


def vp_goal_enabled() -> bool:
    """Read the ``UA_VP_GOAL_ENABLED`` feature flag.

    Defaults to False (OFF) so the /goal wiring lands in production without
    affecting any VP missions until the operator explicitly enables it.
    Per PRD Risk #1 — empirical /goal verification gates flipping this ON.
    """
    raw = os.environ.get("UA_VP_GOAL_ENABLED", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def is_goal_eligible_mission(mission: dict[str, Any]) -> bool:
    """Return True when the mission should get the full /goal artifact set.

    Eligibility:
    1. Feature flag ``UA_VP_GOAL_ENABLED`` is ON, AND
    2. Mission's source_kind (or mission_type) is in
       ``GOAL_ELIGIBLE_SOURCE_KINDS``, OR mission carries
       ``metadata.use_goal_loop=True`` (operator-dispatched override).

    Atlas missions are never eligible (only Cody work goes through /goal).
    """
    if not vp_goal_enabled():
        return False
    vp_id = str((mission or {}).get("vp_id") or "").strip()
    if vp_id != "vp.coder.primary":
        return False  # Atlas never gets /goal

    source_kind = str((mission or {}).get("source_kind") or "").strip()
    mission_type = str((mission or {}).get("mission_type") or "").strip()
    if source_kind in GOAL_ELIGIBLE_SOURCE_KINDS or mission_type in GOAL_ELIGIBLE_SOURCE_KINDS:
        return True

    # Explicit per-task override via metadata.use_goal_loop=True.
    payload_meta: dict[str, Any] = {}
    payload_json = (mission or {}).get("payload_json")
    if payload_json:
        try:
            import json
            payload = json.loads(payload_json) if isinstance(payload_json, str) else payload_json
            payload_meta = payload.get("metadata") if isinstance(payload, dict) else {}
            payload_meta = payload_meta if isinstance(payload_meta, dict) else {}
        except Exception:
            payload_meta = {}
    if bool(payload_meta.get("use_goal_loop")):
        return True

    return False


def build_self_briefing_prompt(
    *,
    workspace_dir: Path,
    objective: str,
    is_goal_eligible: bool,
) -> str:
    """Return the briefing-turn prompt that invokes the self-brief-and-attest skill.

    Called by the CLI client to compose the FIRST subprocess prompt for a VP
    mission. The VP reads this, invokes the skill, and writes BRIEF.md
    (always) + ACCEPTANCE.md + goal_condition.txt (when is_goal_eligible).

    Args:
        workspace_dir: Absolute path to the mission workspace. VP writes
            artifacts here.
        objective: The mission's objective string (from vp_missions or
            task_hub_item).
        is_goal_eligible: True when the mission should also produce
            ACCEPTANCE.md + goal_condition.txt.

    Returns:
        Multi-line prompt text suitable as the FIRST `claude --print` prompt.
    """
    artifacts_required = ["BRIEF.md (required, free prose, ≥1 paragraph)"]
    if is_goal_eligible:
        artifacts_required.extend([
            "ACCEPTANCE.md (required for this /goal-eligible mission)",
            "goal_condition.txt (required for this /goal-eligible mission, ≤4000 chars)",
        ])
    required_list = "\n".join(f"  - {item}" for item in artifacts_required)

    eligibility_note = (
        "This mission IS /goal-eligible (Cody, eligible source_kind, and "
        "UA_VP_GOAL_ENABLED=1). After your briefing turn completes, the parent "
        "worker will invoke `claude -p \"/goal <contents of goal_condition.txt>\"` "
        "to drive completion."
        if is_goal_eligible
        else "This mission is NOT /goal-eligible (either UA_VP_GOAL_ENABLED=0, "
             "Atlas mission, or source_kind not in the /goal-eligible set). "
             "You will run the full work in a single subprocess; skip ACCEPTANCE.md "
             "and goal_condition.txt — produce only BRIEF.md in this turn, then "
             "continue with the work, then write COMPLETION.md before finalize."
    )

    return f"""You are starting an autonomous VP mission. Your FIRST action MUST be the
self-briefing protocol — DO NOT skip this step.

## Mission objective (verbatim from the dispatch)

{objective}

## Workspace

CANONICAL MISSION WORKSPACE: {workspace_dir}

ALL of the mission's protocol artifacts (BRIEF.md, ACCEPTANCE.md,
goal_condition.txt, COMPLETION.md) MUST be written to this exact path.
This is the path the parent worker reads to verify the self-attestation
protocol. If you write COMPLETION.md anywhere else — your own cwd, a
/tmp scratch dir, or a path you derived from the operator's BRIEF
scope — the parent worker will not find it and will demote your
successful mission to failed with
``failure_mode="missing_completion_attestation"``.

If the operator's objective tells you to scope your WORK (file edits,
test files, build outputs) to a different directory like ``/tmp/foo``,
honor that for the work — but the four protocol artifacts above
ALWAYS go to the canonical workspace above. The two are independent.

## Self-briefing contract — invoke the skill

Use the `self-brief-and-attest` skill NOW. It will guide you through 5
phases. For THIS TURN, complete phases 1, 2, and (if applicable) 3:

  - Phase 1: Read the task + interrogate codebase/docs (NOT the operator)
  - Phase 2: Write `BRIEF.md` at the workspace root (universal — every mission)
  - Phase 3: Write `ACCEPTANCE.md` and `goal_condition.txt` (only if /goal-eligible)

Required artifacts at end of this turn:
{required_list}

## /goal eligibility (resolved by parent worker)

{eligibility_note}

## What happens next

After this turn:
- If /goal-eligible: the parent worker reads `goal_condition.txt` and
  invokes `claude -p "/goal <condition>"` in a second subprocess to drive
  completion. You'll have a clean context window for the /goal loop.
- If not /goal-eligible: you'll continue the work in this same subprocess
  (or a follow-up turn) without the /goal loop.

In either case, before calling `finalize_vp_mission(completed)`, you MUST
write `COMPLETION.md` per Phase 5 of the skill. The parent worker enforces
this — missing COMPLETION.md will route the mission into the failure-rescue
lane as `failure_mode="missing_completion_attestation"`.

Begin briefing now. Do not skip ahead to the work.
"""


def read_goal_condition(workspace_dir: Path) -> Optional[str]:
    """Read and validate goal_condition.txt produced by the briefing turn.

    Returns the condition string (stripped) or None if the file is missing,
    empty, or exceeds the 4000-char limit. Logs the reason for None return.
    """
    path = workspace_dir / "goal_condition.txt"
    if not path.exists():
        logger.warning("goal_condition.txt missing in %s", workspace_dir)
        return None
    try:
        text = path.read_text(encoding="utf-8").strip()
    except Exception as exc:
        logger.warning("goal_condition.txt unreadable in %s: %s", workspace_dir, exc)
        return None
    if not text:
        logger.warning("goal_condition.txt is empty in %s", workspace_dir)
        return None
    if len(text) > GOAL_CONDITION_MAX_CHARS:
        logger.warning(
            "goal_condition.txt exceeds %d chars in %s (got %d)",
            GOAL_CONDITION_MAX_CHARS, workspace_dir, len(text),
        )
        return None
    return text


def check_completion_attestation(
    workspace_dir: Path,
    *,
    fallback_dirs: Optional[list[Path]] = None,
) -> tuple[bool, Optional[str]]:
    """Verify COMPLETION.md exists in the workspace before finalize(completed).

    Checks ``workspace_dir`` first (canonical mission workspace). If
    missing, walks ``fallback_dirs`` in order — used by the worker_loop
    to also check Cody's actual cwd (captured via
    ``metadata.dispatch.cody_workspace_dir``) when the BRIEF redirected
    Cody to a non-canonical path (e.g. ``/tmp/cody-X``). Without the
    fallback, a successful mission that wrote COMPLETION.md to its cwd
    instead of the canonical workspace gets spuriously demoted to
    failed.

    Returns:
        (True, None) if COMPLETION.md is present and non-empty in any
          checked directory.
        (False, reason) if missing/empty/unreadable in every checked
          directory — caller routes the mission into failure-rescue via
          finalize(failed, failure_mode="missing_completion_attestation").
    """
    candidates = [workspace_dir]
    if fallback_dirs:
        for extra in fallback_dirs:
            if extra and extra not in candidates:
                candidates.append(extra)

    last_reason = "COMPLETION.md was not written; VP did not complete the self-attestation protocol"
    for candidate in candidates:
        path = candidate / "COMPLETION.md"
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8").strip()
        except Exception as exc:
            last_reason = f"COMPLETION.md unreadable at {path}: {exc}"
            continue
        if not text:
            last_reason = f"COMPLETION.md at {path} is empty"
            continue
        return True, None
    return False, last_reason


__all__ = [
    "GOAL_CONDITION_MAX_CHARS",
    "GOAL_ELIGIBLE_SOURCE_KINDS",
    "vp_goal_enabled",
    "is_goal_eligible_mission",
    "build_self_briefing_prompt",
    "read_goal_condition",
    "check_completion_attestation",
]
