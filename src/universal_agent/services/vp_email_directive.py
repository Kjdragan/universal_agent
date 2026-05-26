"""Centralized VP outbound email directive — replaces duplicated CC-Simone text.

Today the "Kevin direct + CC Simone" email pattern is duplicated across:
- ``services/proactive_codie.py`` (proactive cleanup directive)
- ``services/todo_dispatch_service.py`` (VP-targeted dispatch directive)
- ``services/claude_code_intel.py`` (ClaudeDevs intel directive)

When one caller updates the directive (e.g. switches Kevin's email or
tweaks the header), the others drift. This module is the single source of
truth: callers compose the canonical directive into their own prompt
context instead of hand-rolling copies.

Per the PRD § 5.4 (Step 3 of the VP /goal + Failure-Rescue PRD,
``docs/01_Architecture/12_VP_Goal_Integration_And_Failure_Rescue_PRD.md``).
"""

from __future__ import annotations

from typing import Optional

# Canonical mailbox addresses. Update HERE only — every caller picks up
# changes automatically.
KEVIN_EMAIL = "kevinjdragan@gmail.com"
SIMONE_INBOX = "oddcity216@agentmail.to"
VP_MAILBOX = "vp.agents@agentmail.to"

# Human-readable VP names for the body header. Add new VPs here as they're
# introduced. Keep agent_ids stable; humans only see the friendly name.
_VP_DISPLAY_NAMES = {
    "vp.coder.primary": "Cody",
    "vp.general.primary": "Atlas",
}


def vp_display_name(vp_id: str) -> str:
    """Return the human-readable name for a VP id (e.g. "Cody" for "vp.coder.primary")."""
    return _VP_DISPLAY_NAMES.get((vp_id or "").strip(), vp_id or "VP")


def build_vp_outbound_email_directive(
    *,
    vp_id: str,
    subject_prefix: str = "[VP Status]",
    audience_hint: str = "kevin",
    kevin_email: str = KEVIN_EMAIL,
    simone_inbox: str = SIMONE_INBOX,
    vp_mailbox: str = VP_MAILBOX,
    include_failure_path_note: bool = True,
) -> str:
    """Return prompt text instructing the VP to email Kevin and CC Simone.

    Args:
        vp_id: Stable agent identifier (e.g. ``"vp.coder.primary"``).
        subject_prefix: What to prepend to the subject line. Defaults to
            ``"[VP Status]"``. Callers may pass ``"[Intel]"`` etc. for
            domain-specific surfaces.
        audience_hint: Free-form description of who the email is for
            (e.g. ``"kevin"`` for proactive work, ``"requestor"`` for VP-targeted
            replies). Used in the body header.
        kevin_email: Override Kevin's address (e.g. for testing).
        simone_inbox: Override Simone's inbox (e.g. for testing).
        vp_mailbox: Override the VP outbound mailbox (e.g. for testing).
        include_failure_path_note: When True (default), reminds the VP NOT
            to email Kevin on failure — failures route to Simone via the
            vp_mission_failure task hub item per the failure-rescue
            architecture.

    Returns:
        Multi-line string suitable for embedding in a VP prompt's
        "outbound delivery" section. Begins and ends with single newlines
        so callers can concatenate cleanly.
    """
    vp_name = vp_display_name(vp_id)
    audience_label = (audience_hint or "kevin").strip().lower()
    if audience_label == "kevin":
        send_to_clause = f"send an email to {kevin_email}"
        cc_clause = (
            f"CC Simone's inbox ({simone_inbox}) for situational awareness — "
            "this is informational, no action required from her."
        )
        header_target = "Kevin"
    elif audience_label == "requestor":
        send_to_clause = "reply to the original sender"
        cc_clause = f"CC Simone's inbox ({simone_inbox}) on the reply for situational awareness."
        header_target = "the requestor"
    else:
        # Fallback — generic
        send_to_clause = f"send an email to {kevin_email}"
        cc_clause = f"CC Simone's inbox ({simone_inbox}) for situational awareness."
        header_target = "Kevin"

    lines: list[str] = [
        "",
        "### Outbound delivery directive (CANONICAL — do not deviate)",
        "",
        f"After completing the work AND writing COMPLETION.md, "
        f"{send_to_clause} from the shared VP mailbox {vp_mailbox}.",
        "",
        f"- **Subject prefix:** `{subject_prefix}` followed by a concise summary.",
        f"- **CC:** {cc_clause}",
        "- **Body MUST begin with this exact header block:**",
        "",
        "    ── VP Status Update (FYI — no action required) ──",
        f"    This reply was sent by {vp_name} ({vp_id}) directly to {header_target}.",
        "    Simone is CC'd for situational awareness only. No action is needed from her.",
        "    ────────────────────────────────────────────────",
        "",
        "- Then a natural-language summary of what was produced, where artifacts live, "
        "and a pointer to COMPLETION.md if relevant.",
        "- Attachment routing: use `agentmail_send_with_local_attachments` for PDFs / "
        "any binary / anything ≥ 24 KB / multiple attachments. Use "
        "`prepare_agentmail_attachment` + `mcp__agentmail__send_message` only for "
        "single text files < 24 KB.",
    ]

    if include_failure_path_note:
        lines.extend([
            "",
            "**Failure path:** If the mission failed and you cannot deliver the "
            "intended output, do NOT email Kevin a failure notification. Instead, "
            "call `finalize_vp_mission(failed)` with a clear `transcript_tail` — "
            "the failure-rescue system will route the failure to Simone, who may "
            "retry, redispatch fresh, or escalate to Kevin via "
            "`escalate_vp_failure_to_operator`. Duplicate failure emails from the "
            "VP would be noise.",
        ])

    return "\n".join(lines) + "\n"


__all__ = [
    "KEVIN_EMAIL",
    "SIMONE_INBOX",
    "VP_MAILBOX",
    "vp_display_name",
    "build_vp_outbound_email_directive",
]
