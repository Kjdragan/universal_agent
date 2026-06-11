"""Deterministic rescue policy for failed proactive_wiki missions.

The card->wiki loop dispatches a ``proactive_wiki`` VP mission to ATLAS
(``vp.general.primary``). Today, when such a mission ends ``failed``/``cancelled``
the only remediation path is ``vp_failure_rescue`` surfacing a task to Simone,
who is a *rescue-evaluator* — and empirically (0 of 152 production failures)
never invokes a rescue verb, so failures rot (auto-parked) and no wiki is ever
retried. See the 2026-06-10 card->wiki audit.

This module is the **pure, side-effect-free decision core** of a deterministic
rescue driver that replaces that dead LLM-discretion gate. It maps a failed
mission's ``(mission_type, failure_mode, failure_count, cody_available)`` to a
single ``RescueDecision``. The caller (the finalize chokepoint) executes the
decision using the existing ``vp_dispatch_mission_redispatch_fresh`` /
``escalate_vp_failure_to_operator`` tools.

Policy (bounded — never bangs away):
  - ``operator_cancel`` (deliberate)                      -> SKIP
  - transient infra failure & ATLAS retries remain        -> RETRY on ATLAS (fresh workspace)
  - structural failure OR ATLAS retries exhausted:
        - Cody free                                        -> HANDOFF to Cody (diagnose & fix)
        - Cody busy                                        -> ATLAS fallback (within budget)
  - total rescue budget exhausted                         -> ESCALATE to operator, then stop

Scope is intentionally narrow (proactive_wiki only) to prove the loop before
generalizing; ``RESCUABLE_MISSION_TYPES`` is the one knob to widen it.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- Scope -----------------------------------------------------------------
# Narrow to proactive_wiki first; widen here (+ the enable flag) once proven.
WIKI_MISSION_TYPE = "proactive_wiki"
RESCUABLE_MISSION_TYPES = frozenset({WIKI_MISSION_TYPE})

# --- Principals ------------------------------------------------------------
ATLAS_VP = "vp.general.primary"  # owns proactive_wiki; ZAI inference
CODY_VP = "vp.coder.primary"  # Anthropic Max — the "fix things" principal

# --- Failure-mode classification (from worker_loop::_classify_outcome_failure_mode) ---
# Deliberate: a human stopped it on purpose — never rescue.
DELIBERATE_MODES = frozenset({"operator_cancel"})
# Transient infra hiccups a fresh retry on the same VP can clear (a ZAI blip, a
# subprocess crash, a deploy-restart SIGTERM recovered as a stale claim).
TRANSIENT_MODES = frozenset({"timeout", "subprocess_crash", "stale_claim_expired"})
# Everything else non-deliberate (workspace_guard, goal_cap_hit,
# missing_completion_attestation, auth_failure, vp_self_reported, unspecified)
# is treated as *structural*: a blind same-VP retry is unlikely to help, so it
# goes to Cody (who can diagnose / fix a root cause) rather than looping ATLAS.

# --- Budget (bounded) ------------------------------------------------------
MAX_ATLAS_RETRIES = 2  # transient: redispatch_fresh on ATLAS up to twice...
MAX_TOTAL_RESCUES = 3  # ...then 1 Cody handoff; failure_count beyond this -> escalate

# --- Actions ---------------------------------------------------------------
ACTION_SKIP = "skip"
ACTION_RETRY_ATLAS = "retry_atlas"
ACTION_HANDOFF_CODY = "handoff_cody"
ACTION_ESCALATE = "escalate"


@dataclass(frozen=True)
class RescueDecision:
    """A single deterministic rescue verdict.

    ``action`` is one of the ``ACTION_*`` constants. ``target_vp`` is the VP the
    redispatch should run on (None for skip/escalate). ``reason`` is a short
    human/audit string recorded to rescue_log.
    """

    action: str
    target_vp: str | None
    reason: str


def _is_transient(mode: str) -> bool:
    # `stale_<reason>` reconcile variants (e.g. stale_claim_expired) are all
    # deploy/crash recovery artifacts — transient by construction.
    return mode in TRANSIENT_MODES or mode.startswith("stale_")


def decide_wiki_rescue(
    *,
    mission_type: str,
    failure_mode: str,
    failure_count: int,
    cody_available: bool,
) -> RescueDecision:
    """Return the deterministic rescue action for a failed mission.

    Pure: no I/O, no clock, no randomness — fully unit-testable. ``failure_count``
    is the rescue-chain attempt number (1 = the original failure).
    """
    if (mission_type or "") not in RESCUABLE_MISSION_TYPES:
        return RescueDecision(ACTION_SKIP, None, "mission_type out of rescue scope")

    mode = (failure_mode or "").strip().lower()
    # Empty mode = a bare/operator cancel with no classified failure — never a
    # rescuable failure. Deliberate operator_cancel likewise. Skip both.
    if not mode or mode in DELIBERATE_MODES:
        return RescueDecision(ACTION_SKIP, None, f"deliberate/empty mode '{mode or '<none>'}' — no rescue")

    # Budget exhausted -> surface to the operator and stop banging away.
    if failure_count > MAX_TOTAL_RESCUES:
        return RescueDecision(
            ACTION_ESCALATE,
            None,
            f"rescue budget exhausted (failure_count={failure_count} > {MAX_TOTAL_RESCUES}) — escalate",
        )

    transient = _is_transient(mode)

    # Step 1 — transient infra failure with ATLAS retries remaining.
    if transient and failure_count <= MAX_ATLAS_RETRIES:
        return RescueDecision(
            ACTION_RETRY_ATLAS,
            ATLAS_VP,
            f"transient '{mode}', ATLAS retry {failure_count}/{MAX_ATLAS_RETRIES}",
        )

    # Step 2 — structural failure, or ATLAS retries exhausted.
    if cody_available:
        why = (
            f"structural '{mode}' — hand to Cody to diagnose & fix"
            if not transient
            else "ATLAS retries exhausted — hand to Cody to diagnose & fix"
        )
        return RescueDecision(ACTION_HANDOFF_CODY, CODY_VP, why)

    # Cody busy -> fall back to one ATLAS attempt (still inside the total budget).
    return RescueDecision(
        ACTION_RETRY_ATLAS,
        ATLAS_VP,
        f"Cody busy — ATLAS fallback attempt ({failure_count}/{MAX_TOTAL_RESCUES})",
    )
