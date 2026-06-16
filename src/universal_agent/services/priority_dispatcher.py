"""Pythonic priority dispatcher (Decision D3).

Replaces ``agent_router.route_all_to_simone`` as the routing brain. Routing is
**deterministic and has no LLM in the hot path** — the cheap sonnet/haiku
classifier (``llm_classifier.classify_agent_route``) is consulted ONLY for the
genuinely ambiguous, untagged tail. VP-bound tasks are dispatched directly via
``tools.vp_orchestration.dispatch_vp_mission`` (Python), so Simone's expensive
~1.25M-token heartbeat turn is NO LONGER the router — it is reserved for genuine
execution and judgment (chat, escalations she chooses to act on).

The whole path is gated behind ``UA_PRIORITY_DISPATCHER_ENABLED`` (**default ON**
as of 2026-06-16, after the D3 path was proven live in prod — PR #1034/#1038 +
controlled smoke dispatch). Set the flag to ``0``/``false``/``no``/``off`` to
disable (the kill switch — falls back to legacy "Simone-First" routing
unchanged). ``prefer-ATLAS`` for research/general work is a second, independent
toggle (``UA_DISPATCHER_PREFER_ATLAS``, **default OFF**) — when off,
general/research falls back to Simone (no degradation; Stage A).

Awareness is preserved: completion is recorded passively via
``proactive_work_recap`` and escalation on VP failure flows through
``vp_failure_rescue.surface_failure_to_simone`` — both pre-existing. Simone is
removed only from the *dispatch decision*, not from awareness.

Deferred-task policy (capacity full): a VP-bound task with no free slot this
tick is left in the Simone residue (it is NOT peeled out), so Simone handles it
this tick exactly as today — no abandonment, no stall, no retry-budget churn.
There is intentionally no "release-on-defer": the only existing release verbs
(``release_stale_assignments`` / ``finalize_assignments``) consume the ToDo
retry budget and would push a backlog of capacity-deferred tasks to
``needs_review``. See the M2 lessons-learned handback for the rationale.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from typing import Any, Optional

from universal_agent.feature_flags import _is_truthy, _read_env_bool
from universal_agent.services.agent_router import (
    AGENT_CODER,
    AGENT_GENERAL,
    AGENT_SIMONE,
)
from universal_agent.services.vp_capacity import (
    _available_agents_for_llm_routing,
    _env_positive_int,
    _vp_active_counts,
)

log = logging.getLogger(__name__)

# Canonical coder-lane source_kinds — the single enforced enum lives in
# vp_orchestration. Import it (do NOT duplicate the literal set, or it drifts
# from the dispatcher that actually links coder-lane claims). The except-clause
# fallback keeps classify_task importable in a minimal env and MUST mirror the
# canonical set; test_priority_dispatcher_classify asserts they stay in sync.
try:  # pragma: no cover - exercised in prod; fallback only for minimal import envs
    from universal_agent.tools.vp_orchestration import (
        _CODER_LANE_SOURCE_KINDS as CODER_LANE_SOURCE_KINDS,
    )
except Exception:  # pragma: no cover
    CODER_LANE_SOURCE_KINDS = frozenset(
        {"tutorial_build", "cody_demo_task", "cody_scaffold_request"}
    )

# Chat source_kinds that address Simone herself. Verified live against
# task_hub_items: ``chat_panel`` (interactive chat-panel intake) and
# ``simone_chat`` (Simone's own chat-session lifecycle) are the ONLY written
# chat source_kinds. NOTE: ``interactive_chat`` is a delivery_mode / run_kind
# value, NEVER a source_kind — do not add it here without a live SELECT hit.
CHAT_SOURCE_KINDS = frozenset({"chat_panel", "simone_chat"})

# Priority tiers (lower = dispatched first within a sweep). P1 is intentionally
# left as a reserved gap (NOT a typo) so a future "interactive but not
# operator-explicit" mid-tier can slot between the two without renumbering. The
# sort key is the raw int, so a gap is free.
P0 = 0  # chat->Simone, operator/homepage explicit target, coding->CODIE
P2 = 2  # intel/proactive/research by preferred_vp tag / classifier tail


@dataclass
class DispatchDecision:
    task_id: str
    agent_id: str  # simone | vp.coder.primary | vp.general.primary
    priority: int  # P0 / P2
    rule: str  # human-readable provenance for audit/logs
    is_vp: bool  # True => candidate for dispatch_vp_mission
    deferred: bool = False  # True => no free slot / dispatch failed; falls through to Simone
    dispatched: bool = False  # True => mission dispatched + task marked delegated
    mission_id: str = ""  # the dispatched VP mission id (when dispatched)
    confidence: str = "deterministic"
    method: str = "priority_dispatcher"


# ---------------------------------------------------------------------------
# Feature flags
# ---------------------------------------------------------------------------


def priority_dispatcher_enabled() -> bool:
    """Master switch — **DEFAULT ON** (D3 proven live in prod 2026-06-16).

    Tri-state: unset/unrecognized -> ON (the new default); an explicit
    ``0``/``false``/``no``/``off`` -> OFF (the kill switch, falls back to legacy
    Simone-First routing); an explicit truthy value -> ON. Roll back instantly by
    setting ``UA_PRIORITY_DISPATCHER_ENABLED=0`` in Infisical prod (no redeploy).
    """
    val = _read_env_bool("UA_PRIORITY_DISPATCHER_ENABLED")
    return True if val is None else val


def _prefer_atlas_for_general() -> bool:
    """Cautious-rollout toggle (default OFF). When ON, research/general work
    prefers ATLAS (the scarce-singleton Simone is freed); when OFF it falls back
    to Simone — current behavior, no degradation. ATLAS already has MCP/skill
    parity via repo directory walk-up, so this is validate-then-flip, not a
    capability gate.
    """
    return _is_truthy(os.getenv("UA_DISPATCHER_PREFER_ATLAS"))


def prefer_atlas_enabled() -> bool:
    """Public read-only accessor for the prefer-ATLAS toggle (default OFF).

    Mirrors :func:`_prefer_atlas_for_general` (the dispatch-path reader) so status
    surfaces — the ZAI Control cockpit — can show the live flag state without
    reaching into a private symbol. Stage A = OFF (general/research falls back to
    Simone); flip ``UA_DISPATCHER_PREFER_ATLAS`` to route it to ATLAS (Stage B).
    """
    return _prefer_atlas_for_general()


# ---------------------------------------------------------------------------
# Task-shape readers (pure)
# ---------------------------------------------------------------------------


def _meta(item: dict[str, Any]) -> dict[str, Any]:
    meta = item.get("metadata")
    return meta if isinstance(meta, dict) else {}


def _explicit_target_agent(item: dict[str, Any]) -> str:
    """Authoritative operator/homepage routing. Two writers store it in two
    places: dashboard_todolist_quick_add -> metadata.workflow_manifest.target_agent;
    dashboard_mission_control_dispatch_to_codie -> ALSO metadata.target_agent.
    Check both.
    """
    meta = _meta(item)
    manifest = meta.get("workflow_manifest") if isinstance(meta.get("workflow_manifest"), dict) else {}
    return str(manifest.get("target_agent") or meta.get("target_agent") or "").strip()


def _preferred_vp_tag(item: dict[str, Any]) -> str:
    """Pre-tagged lane: metadata.preferred_vp (TOP-LEVEL — the path
    proactive_convergence producers and atlas_direct_dispatch use)."""
    return str(_meta(item).get("preferred_vp") or "").strip()


def _is_chat(item: dict[str, Any]) -> bool:
    return str(item.get("source_kind") or "").strip().lower() in CHAT_SOURCE_KINDS


def _is_coding(item: dict[str, Any]) -> bool:
    """Deterministic coding signal: explicit code_change manifest OR a canonical
    coder-lane source_kind (the enforced vp_orchestration enum)."""
    meta = _meta(item)
    manifest = meta.get("workflow_manifest") if isinstance(meta.get("workflow_manifest"), dict) else {}
    if str(manifest.get("workflow_kind") or "").strip() == "code_change":
        return True
    return str(item.get("source_kind") or "").strip().lower() in CODER_LANE_SOURCE_KINDS


# ---------------------------------------------------------------------------
# Classification (pure / unit-testable, no LLM, no I/O)
# ---------------------------------------------------------------------------


def classify_task(item: dict[str, Any]) -> DispatchDecision:
    """Deterministic, priority-ordered routing for ONE task.

    No LLM unless the task is genuinely ambiguous AND untagged (the tail, which
    is resolved later in ``dispatch_claimed`` via the cheap classifier).
    """
    task_id = str(item.get("task_id") or "").strip()

    # P0.1 — explicit operator/homepage target_agent is non-negotiable.
    target = _explicit_target_agent(item)
    if target in {AGENT_CODER, AGENT_GENERAL}:
        return DispatchDecision(task_id, target, P0, "explicit_target_agent", is_vp=True)
    if target == AGENT_SIMONE:
        return DispatchDecision(task_id, AGENT_SIMONE, P0, "explicit_target_simone", is_vp=False)

    # P0.2 — chat addresses Simone herself.
    if _is_chat(item):
        return DispatchDecision(task_id, AGENT_SIMONE, P0, "chat", is_vp=False)

    # P0.3 — coding work -> CODIE.
    if _is_coding(item):
        return DispatchDecision(task_id, AGENT_CODER, P0, "coding_to_codie", is_vp=True)

    # P2.1 — pre-tagged preferred_vp lane.
    pref = _preferred_vp_tag(item)
    if pref == AGENT_GENERAL:
        if _prefer_atlas_for_general():
            return DispatchDecision(task_id, AGENT_GENERAL, P2, "preferred_vp_general", is_vp=True)
        # M1 gate (prefer-ATLAS off): fall back to Simone, current behavior.
        return DispatchDecision(
            task_id, AGENT_SIMONE, P2, "preferred_vp_general_simone_fallback", is_vp=False
        )
    if pref == AGENT_CODER:
        return DispatchDecision(task_id, AGENT_CODER, P2, "preferred_vp_coder", is_vp=True)

    # P2.2 — ambiguous untagged tail -> resolved by the cheap classifier later.
    return DispatchDecision(task_id, AGENT_SIMONE, P2, "ambiguous_tail_pending", is_vp=False)


def _routing_dict(d: DispatchDecision) -> dict[str, Any]:
    """The ``_routing`` shape consumed by build_todo_execution_prompt for the
    Simone-bound residue (so the prompt surfaces the deterministic decision
    instead of falling back to Simone's default-ownership table)."""
    return {
        "agent_id": d.agent_id,
        "confidence": d.confidence,
        "reason": d.rule,
        "method": d.method,
        # A deferred VP task is should_delegate=True so Simone delegates it to
        # the (busy) VP, which queues the mission; a Simone-bound task is False.
        "should_delegate": bool(d.is_vp),
    }


def _already_delegated(conn, task_id: str, mission_id: str) -> bool:
    """True if the dispatch impl already marked this task delegated to mission_id
    (e.g. the cody_demo_task self-delegate inside _vp_dispatch_mission_impl)."""
    if not mission_id:
        return False
    from universal_agent import task_hub

    try:
        cur = task_hub.get_item(conn, task_id)
    except Exception:
        return False
    if not cur or str(cur.get("status") or "") != task_hub.TASK_STATUS_DELEGATED:
        return False
    deleg = (cur.get("metadata") or {}).get("delegation") or {}
    return str(deleg.get("mission_id") or "") == mission_id


def _mark_delegated(conn, task_id: str, *, vp_id: str, mission_id: str) -> bool:
    """Write the `delegated` lifecycle transition (status -> delegated, complete
    the seized assignment, record the delegation block) with one bounded retry.

    Uses the canonical task_hub.perform_task_action delegate verb (NEVER a raw
    UPDATE) so the assignment-completion + delegation metadata + evaluation row
    the execution_missing_lifecycle_mutation guardrail inspects all land.
    Returns True on success, False if it could not be persisted.
    """
    from universal_agent import task_hub

    note = f"mission_id={mission_id}" if mission_id else "priority_dispatcher"
    for attempt in range(2):
        try:
            task_hub.perform_task_action(
                conn,
                task_id=task_id,
                action=task_hub.ACTION_DELEGATE,
                reason=vp_id,  # -> metadata.delegation.delegate_target
                note=note,
                agent_id="priority_dispatcher",
            )
            return True
        except Exception as exc:
            log.warning(
                "priority_dispatcher: delegate-mark attempt %d failed task=%s: %s",
                attempt + 1, task_id, exc,
            )
    return False


# ---------------------------------------------------------------------------
# Async dispatch driver
# ---------------------------------------------------------------------------


async def dispatch_claimed(
    conn,
    claimed: list[dict[str, Any]],
    *,
    active_assignments: Optional[list[dict[str, Any]]] = None,
    dispatch_fn=None,  # injection seam for tests; defaults to dispatch_vp_mission
) -> list[DispatchDecision]:
    """Route + dispatch a batch of already-claimed tasks. Returns the decisions.

    Slot model: a VP decision dispatches only if BOTH (a) the global
    ``capacity_governor`` allows it (not api_down / not in 429 backoff) AND
    (b) the per-agent VP cap has a free slot (``_vp_active_counts`` vs
    ``UA_MAX_CONCURRENT_VP_*``). When a VP decision cannot dispatch it is marked
    ``deferred`` and left in place — the caller keeps it in the Simone residue
    for this tick (no release, no re-claim).
    """
    if not claimed:
        return []

    decisions = [classify_task(item) for item in claimed]
    by_id = {str(it.get("task_id") or ""): it for it in claimed}

    # 1. Resolve the ambiguous untagged tail with the cheap classifier.
    #    This is the ONLY LLM touch, and only for genuinely untagged tasks.
    tail = [d for d in decisions if d.rule == "ambiguous_tail_pending"]
    if tail:
        from universal_agent.services.llm_classifier import classify_agent_route
        from universal_agent.services.todo_dispatch_service import _coerce_labels

        available = _available_agents_for_llm_routing(active_assignments)
        for d in tail:
            item = by_id.get(d.task_id, {})
            try:
                route = await classify_agent_route(
                    title=str(item.get("title") or ""),
                    description=str(item.get("description") or ""),
                    labels=_coerce_labels(item.get("labels")),
                    source_kind=str(item.get("source_kind") or ""),
                    project_key=str(item.get("project_key") or ""),
                    available_agents=available,
                )
            except Exception as exc:  # classify_agent_route already self-fallbacks; belt-and-suspenders
                log.warning("priority_dispatcher: classifier tail failed task=%s: %s", d.task_id, exc)
                route = {"agent_id": AGENT_SIMONE, "method": "fallback", "confidence": "fallback"}
            agent = str(route.get("agent_id") or AGENT_SIMONE)
            # M1 gate also applies to the tail: don't route general to ATLAS
            # until prefer-ATLAS is flipped on.
            if agent == AGENT_GENERAL and not _prefer_atlas_for_general():
                agent = AGENT_SIMONE
            d.agent_id = agent
            d.is_vp = agent in {AGENT_CODER, AGENT_GENERAL}
            d.method = f"classifier:{route.get('method', 'llm')}"
            d.confidence = str(route.get("confidence") or "medium")
            d.rule = "ambiguous_tail"

    # 2. Priority-ordered slot fill (decision only — no I/O yet).
    coder_used, general_used = _vp_active_counts(active_assignments)
    max_coder = _env_positive_int("UA_MAX_CONCURRENT_VP_CODER", 1)
    max_general = _env_positive_int("UA_MAX_CONCURRENT_VP_GENERAL", 2)

    from universal_agent.services.capacity_governor import CapacityGovernor

    governor = CapacityGovernor.get_instance()

    to_dispatch: list[DispatchDecision] = []
    for d in sorted(decisions, key=lambda x: x.priority):
        if not d.is_vp:
            continue
        ok, reason = governor.can_dispatch()
        if not ok:
            d.deferred = True
            log.info(
                "priority_dispatcher: defer task=%s -> %s (governor: %s)",
                d.task_id, d.agent_id, reason,
            )
            continue
        if d.agent_id == AGENT_CODER and coder_used >= max_coder:
            d.deferred = True
            continue
        if d.agent_id == AGENT_GENERAL and general_used >= max_general:
            d.deferred = True
            continue
        # Reserve the slot for the remainder of this sweep.
        if d.agent_id == AGENT_CODER:
            coder_used += 1
        else:
            general_used += 1
        to_dispatch.append(d)

    # 3. Execute the dispatches (await), then record the `delegated` transition.
    if to_dispatch:
        if dispatch_fn is None:
            from universal_agent.tools.vp_orchestration import dispatch_vp_mission

            dispatch_fn = dispatch_vp_mission
        from universal_agent import task_hub

        for d in to_dispatch:
            item = by_id.get(d.task_id, {})
            objective = str(item.get("description") or item.get("title") or "").strip()
            mission_type = "task" if d.agent_id == AGENT_CODER else "proactive_general"
            # Per-attempt idempotency key: include the fresh-per-claim run_id so
            # a task that was dispatched, failed, and got reopened/reclaimed gets
            # a NEW mission rather than colliding with the prior (terminal or
            # still-running) mission id. dispatch_vp_mission returns the EXISTING
            # mission for a repeated key, so a stable per-task key would silently
            # swallow a retry (and could re-queue a running mission).
            run_id = str(item.get("workflow_run_id") or "").strip()
            idem = f"dispatch-{d.task_id}-{run_id}" if run_id else f"dispatch-{d.task_id}"
            try:
                payload = await dispatch_fn(
                    vp_id=d.agent_id,
                    objective=objective,
                    mission_type=mission_type,
                    idempotency_key=idem,
                    source_session_id="priority_dispatcher",
                    task_id=d.task_id,  # explicit task_id => skips link auto-discovery
                )
            except Exception as exc:
                # Dispatch failed -> leave the task in the Simone residue so it
                # is not lost (Simone handles/redelegates it this tick).
                d.deferred = True
                log.warning(
                    "priority_dispatcher: dispatch_vp_mission failed task=%s -> %s: %s",
                    d.task_id, d.agent_id, exc,
                )
                continue
            mid = str((payload or {}).get("mission_id") or "").strip()
            d.mission_id = mid

            # dispatch_vp_mission self-delegates some linked lanes (notably
            # cody_demo_task) inside _vp_dispatch_mission_impl. If the impl
            # already marked THIS task delegated to THIS mission, don't write a
            # duplicate delegate (which would append a redundant evaluation row).
            if _already_delegated(conn, d.task_id, mid):
                d.dispatched = True
                log.info(
                    "priority_dispatcher: task=%s already delegated by dispatch impl "
                    "(-> %s, mission=%s)",
                    d.task_id, d.agent_id, mid,
                )
                continue

            if _mark_delegated(conn, d.task_id, vp_id=d.agent_id, mission_id=mid):
                d.dispatched = True
                log.info(
                    "priority_dispatcher: dispatched task=%s -> %s (rule=%s, prio=%s, mission=%s)",
                    d.task_id, d.agent_id, d.rule, d.priority, mid,
                )
            else:
                # The mission IS dispatched but the delegated-mark write failed
                # after a retry. Mark dispatched so Simone does NOT re-dispatch
                # (the task is peeled from her prompt and the lifecycle enforcer
                # is scoped to the residue, so no false page); the VP mission
                # itself transitions the linked task on completion.
                d.dispatched = True
                log.error(
                    "priority_dispatcher: delegated-mark FAILED after dispatch task=%s "
                    "mission=%s — relying on mission-completion linkage",
                    d.task_id, mid,
                )

    # 4. Annotate the residue (everything not dispatched) with a _routing hint.
    for d in decisions:
        if not d.dispatched:
            item = by_id.get(d.task_id)
            if isinstance(item, dict):
                item["_routing"] = _routing_dict(d)

    return decisions
