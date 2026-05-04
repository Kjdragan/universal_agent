"""Mission Control — investigation-prompt generator (Phase 4).

Builds a self-contained prompt from a single Mission Control card for:
  - operator copy-paste into an external AI coder (zero side effects), or
  - dispatch as a Task Hub mission to vp.coder.primary (Send-to-Codie).

Per docs/02_Subsystems/Mission_Control_Intelligence_System.md §5.2,
the prompt MUST include:
  - card narrative + why_it_matters + recommended_next_step
  - evidence_refs as clickable URLs
  - the full evidence_payload (so the AI doesn't have to re-collect)
  - subject metadata: kind, id, recurrence_count, first_observed_at
  - prior synthesis_history summaries (recurring subjects only)
  - subject-kind-specific framing tail
  - codebase root path for repo-grounded callers

The prompt is content-only — it describes the situation. Whether it's
copied to an external coder or dispatched to Codie, the framing is the
same; only the "what to do with it" instruction changes per delivery
mode.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Subject-kind-specific framing tails ──────────────────────────────────
# Each tail anchors the AI on what kind of investigation/action makes
# sense for the subject. We deliberately don't embed agent-specific
# instructions (Codie vs external) here — the delivery-mode wrapper
# handles that.

_FRAMING_TAILS: dict[str, str] = {
    "task": (
        "This card describes a Task Hub item that needs operator-aware "
        "investigation. Inspect the task's lifecycle, retry history, "
        "and any associated session traces. Identify what specifically "
        "is blocking it (configuration issue, transient failure, missing "
        "credential, design flaw, etc.) and propose a concrete fix. "
        "If the fix requires code changes, draft them; if it's an "
        "operator decision, surface that explicitly."
    ),
    "run": (
        "This card describes a single execution run worth examining. "
        "Pull the run trace, identify what produced this card "
        "(unusual duration, unusual output, error pattern, etc.), and "
        "propose follow-up: a code fix, a documentation update, or an "
        "operator note explaining what was learned."
    ),
    "mission": (
        "This card describes a VP mission in flight. Inspect the mission's "
        "current state, the dispatch metadata, and any partial outputs. "
        "Determine whether it should continue, be cancelled, be re-queued "
        "with different parameters, or escalated to operator review."
    ),
    "artifact": (
        "This card describes a noteworthy work product. Open the artifact, "
        "summarize what was produced, and propose what (if anything) the "
        "operator should do with it: review, file under a topic, share "
        "externally, archive, or extend."
    ),
    "failure_pattern": (
        "This card describes a recurring failure pattern, not a single "
        "incident. Identify the COMMON root cause across the listed "
        "instances. Propose a structural fix that addresses all of them, "
        "not a one-off patch. Tests that pin the pattern as a regression "
        "are a high-value deliverable here."
    ),
    "infrastructure": (
        "This card describes a system component health issue surfaced by "
        "a tier-0 health tile. Diagnose the underlying cause using logs, "
        "metrics, and recent code changes. Propose a remediation. DO NOT "
        "execute the remediation yourself unless the operator explicitly "
        "confirms — these subjects have higher blast-radius."
    ),
    "idea": (
        "This card captures an observation worth keeping for later, not "
        "an actionable issue. Treat it as design / planning input. "
        "Summarize the idea and propose what a future implementation "
        "would look like — but do NOT start building unless the operator "
        "explicitly asks."
    ),
}


# Delivery-mode preambles — wrap the same investigation prompt with a
# few different framings depending on who/where the prompt is heading.

_PREAMBLES: dict[str, str] = {
    "external_ai_coder": (
        "You are an AI coding assistant working with the Universal Agent "
        "operator. The operator is sharing this Mission Control investigation "
        "card with you so you can dig in independently. Read the situation "
        "below, follow the instructions in the Investigation Framing, and "
        "report back with a concrete diagnosis and proposed fix."
    ),
    "codie": (
        "You are Codie (vp.coder.primary), the Universal Agent code-change "
        "VP. The operator dispatched this Mission Control card to you for "
        "investigation and remediation. Read the situation below, follow "
        "the instructions in the Investigation Framing, and execute the "
        "appropriate fix. Standard external_effect_policy applies "
        "(allow_pr=true, allow_merge=false, allow_main_push=false, "
        "allow_deploy=false). Open a PR for review, do not merge."
    ),
}


@dataclass
class GeneratedPrompt:
    """Output of `build_prompt`. The text is what an AI consumes; the
    metadata is what we persist in dispatch_history for audit.
    """

    text: str
    delivery_mode: str
    card_id: str
    subject_kind: str
    subject_id: str
    generated_at_utc: str


def build_prompt(
    card: dict[str, Any],
    *,
    delivery_mode: str = "external_ai_coder",
    operator_steering_text: str | None = None,
    codebase_root: str | None = None,
) -> GeneratedPrompt:
    """Construct the investigation prompt for a card.

    `delivery_mode` ∈ {"external_ai_coder", "codie"}:
      - external_ai_coder: framing assumes operator will copy-paste
        the prompt to a separate AI coder (Claude Code, Codex, etc.)
      - codie: framing assumes the prompt is dispatched as a Task Hub
        mission to vp.coder.primary

    `operator_steering_text` (Send-to-Codie only): operator's append-only
    additional instructions. Surfaced verbatim in a clearly-labeled
    section so Codie can use it as steering signal AND so the audit
    trail shows exactly what Kevin added.

    `codebase_root`: when provided, included in the prompt as the
    repo path so an external AI coder lands in the right working
    directory. Defaults to the project's approved codebase root.
    """
    if not isinstance(card, dict):
        raise ValueError("card must be a dict")
    subject_kind = str(card.get("subject_kind") or "").strip()
    subject_id = str(card.get("subject_id") or "").strip()
    card_id = str(card.get("card_id") or "").strip()
    if not subject_kind or not subject_id or not card_id:
        raise ValueError("card missing subject_kind / subject_id / card_id")
    if delivery_mode not in _PREAMBLES:
        raise ValueError(f"delivery_mode must be one of {sorted(_PREAMBLES)!s}")

    # Resolve codebase root if not provided.
    if codebase_root is None:
        try:
            from universal_agent.codebase_policy import (
                DEFAULT_APPROVED_CODEBASE_ROOT,
                approved_codebase_roots_from_env,
            )
            roots = approved_codebase_roots_from_env()
            codebase_root = roots[0] if roots else DEFAULT_APPROVED_CODEBASE_ROOT
        except Exception:
            codebase_root = "/opt/universal_agent"

    framing = _FRAMING_TAILS.get(subject_kind, _FRAMING_TAILS["task"])
    preamble = _PREAMBLES[delivery_mode]

    sections: list[str] = []

    sections.append(preamble)
    sections.append("")
    sections.append("─── Card Subject ────────────────────────────────")
    sections.append(f"  card_id:           {card_id}")
    sections.append(f"  subject_kind:      {subject_kind}")
    sections.append(f"  subject_id:        {subject_id}")
    sections.append(f"  severity:          {card.get('severity', '?')}")
    sections.append(f"  recurrence_count:  {card.get('recurrence_count', 1)}")
    sections.append(f"  first_observed_at: {card.get('first_observed_at', '?')}")
    sections.append(f"  last_synthesized:  {card.get('last_synthesized_at', '?')}")
    sections.append(f"  codebase_root:     {codebase_root}")
    sections.append("")

    sections.append("─── Title ───────────────────────────────────────")
    sections.append(str(card.get("title") or "").strip())
    sections.append("")

    sections.append("─── Narrative ───────────────────────────────────")
    sections.append(str(card.get("narrative") or "").strip() or "(no narrative)")
    sections.append("")

    why = str(card.get("why_it_matters") or "").strip()
    if why:
        sections.append("─── Why It Matters ──────────────────────────────")
        sections.append(why)
        sections.append("")

    next_step = str(card.get("recommended_next_step") or "").strip()
    if next_step:
        sections.append("─── Recommended Next Step (LLM suggestion) ──────")
        sections.append(next_step)
        sections.append("")

    # Evidence refs — clickable URLs the AI can fetch
    evidence_refs = card.get("evidence_refs") or []
    if isinstance(evidence_refs, list) and evidence_refs:
        sections.append("─── Evidence References ─────────────────────────")
        for ref in evidence_refs:
            if not isinstance(ref, dict):
                continue
            label = str(ref.get("label") or ref.get("kind") or "").strip()
            uri = str(ref.get("uri") or "").strip()
            ident = str(ref.get("id") or "").strip()
            line = f"  - {label or '(unlabeled)'}"
            if ident:
                line += f" [{ident}]"
            if uri:
                line += f" → {uri}"
            sections.append(line)
        sections.append("")

    # Recurrence context — for cards that have surfaced before, include
    # the prior synthesis history so the AI can spot patterns.
    history = card.get("synthesis_history") or []
    if isinstance(history, list) and history and (card.get("recurrence_count") or 0) > 1:
        sections.append("─── Prior Synthesis History (recurring subject) ─")
        for entry in history[:5]:
            if not isinstance(entry, dict):
                continue
            ts = str(entry.get("ts") or "?")
            prior = str(entry.get("narrative") or "").strip()
            if prior:
                sections.append(f"  {ts}: {prior[:400]}{'...' if len(prior) > 400 else ''}")
        sections.append("")

    # Full evidence payload — gives the AI the raw data it'd otherwise
    # have to re-collect itself.
    payload = card.get("evidence_payload") or card.get("evidence_payload_json")
    if payload:
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (TypeError, json.JSONDecodeError):
                pass
        sections.append("─── Full Evidence Payload (use as ground truth) ─")
        try:
            payload_text = json.dumps(payload, indent=2, default=str, ensure_ascii=False)
        except Exception:
            payload_text = str(payload)
        sections.append(payload_text)
        sections.append("")

    if operator_steering_text:
        sections.append("─── Operator Steering (provided at dispatch) ────")
        sections.append(operator_steering_text.strip())
        sections.append("")

    sections.append("─── Investigation Framing ───────────────────────")
    sections.append(framing)
    sections.append("")

    if delivery_mode == "codie":
        sections.append("─── Execution Constraints ───────────────────────")
        sections.append("- Open a PR for any code changes; do NOT merge.")
        sections.append("- Do NOT push to main, deploy, or modify production data.")
        sections.append("- If the fix requires Class A operational action (service")
        sections.append("  restart, etc.), surface it as a recommendation; do not act.")
        sections.append("")

    return GeneratedPrompt(
        text="\n".join(sections),
        delivery_mode=delivery_mode,
        card_id=card_id,
        subject_kind=subject_kind,
        subject_id=subject_id,
        generated_at_utc=_utc_now_iso(),
    )
