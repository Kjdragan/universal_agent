"""Tests for T1: email triage brief parsing is markdown-blind.

Root cause: ``_TRIAGE_FIELD_RE`` was five hand-copied regexes anchored on
``^\\s*<field>\\s*:`` — but the email-handler LLM emits fields wrapped in
markdown decoration (``**safety_status:** clean``), which ``\\s*`` cannot
cross. Every field silently fell through to manufactured defaults, and
the ``sender_trusted`` default of ``True`` in ``hooks_service.py`` then
silently upgraded unknown-trust rows to ``trusted_execute``.

The two verbatim fixtures below (``REAL_PROD_BRIEF_BLANK_LINE_FORM`` and
``REAL_PROD_BRIEF_COMPACT_FORM``) are recovered read-only from
``activity_events.full_message`` on the production VPS
(``AGENT_RUN_WORKSPACES/activity_state.db``), rows matching
``full_message LIKE '%EMAIL TRIAGE BRIEF%'``. They are real hook_triage
input, not constructed test data — see PR description for the recovery
command. A third recovered row used the same compact bold form as
``REAL_PROD_BRIEF_COMPACT_FORM`` (not included separately); none of the
three recovered rows used a leading ``- `` bullet, so that shape is
covered by a synthetic fixture (``test_bulleted_dash_form_parses``)
per the ticket's documented failure shapes.
"""

from __future__ import annotations

import os
from unittest.mock import patch

from universal_agent.durable.db import connect_runtime_db
from universal_agent.hooks_service import HookAction, HooksService
from universal_agent.services.email_task_bridge import (
    _TRIAGE_FIELDS,
    parse_email_triage_brief,
)
from universal_agent.task_hub import ensure_schema, get_item, upsert_item

# ── Verbatim recovered production briefs ────────────────────────────────
# Recovered via: ssh ua@uaonvps sqlite3 -readonly
#   AGENT_RUN_WORKSPACES/activity_state.db
#   "SELECT full_message FROM activity_events
#    WHERE full_message LIKE '%EMAIL TRIAGE BRIEF%'"
# Fields are separated by blank lines; all values are markdown-bold
# (`**field:** value`) with the colon inside the bold markers — this is
# the shape `^\s*<field>\s*:` cannot match because `\s*` does not span
# the leading `**`.

REAL_PROD_BRIEF_BLANK_LINE_FORM = """\
== EMAIL TRIAGE BRIEF ==

**safety_status:** clean

**routing_decision:** review_required

**classification:** status_update

**priority:** p3 (background)

**subject_summary:** Automated SonosKD diagnostics dump (mint-desktop, last 1h) — service healthy, one transient speaker-rejection warning that self-resolved.

---

**Safety evaluation:**
No prompt injection, social engineering, suspicious links, or attachments detected. The embedded "How to investigate / deploy" shell commands are standard project-context instructions, not injection — they originate from Simone's own AgentMail self-send (`sender_role: self_send`) and are routine diagnostics context. The Tailscale IP (`100.95.187.38`) and UI URL (`http://...:8557`) are internal tailnet addresses, not external phishing vectors. Env vars are explicitly marked `non-secret` and contain only network config — no credentials leaked. Note: `sender_trusted` is `False`, so per triage rules this routes to `review_required` despite the clean self-send origin.

---

**key_details:**
- **Source:** Self-send from Simone's own AgentMail inbox (`oddcity216@agentmail.to`); appears to be an automated periodic health ping from the SonosKD app (`SONOSKD_DIAG_RECIPIENT` env points back to this inbox).
- **Host/repo:** `mint-desktop`, `/home/kjdragan/lrepos/SonosKD`, branch `main`, commit `643bd54` (2026-06-23 17:03:04 -0500). This is Kevin's personal Sonos control app, not a UA core service.
- **Service state:** Operational. `play_favorite` commands succeeding for '2000s Pop' (nr=2) and 'KROQ' (nr=14).
- **One warning (transient):** 2026-06-23 16:51:47 — `play_favorite zone=Kevin's Office nr=6 -> rejected: Couldn't start 'All '90s' (the speaker rejected it)`. The system recovered immediately; '2000s Pop' replayed at 16:58:49 and 'KROQ' at 16:59:27, so this is a single dropped favorite, not a service outage.
- **Deploy context provided for Cody:** git workflow (`git pull`/`uv sync`), dev server on port 8600 (to avoid colliding with live :8557), read-only smoke (`uv run python -m app.sonos`), redeploy (`systemctl --user restart sonoskd`).
- **Timestamp of report:** 2026-06-23T17:03:25.

---

**action_items:**
1. *(Optional / low-value)* Investigate why favorite nr=6 ('All '90s') was rejected by the speaker at 16:51:47.
   - **what:** Confirm whether this is a recurring failure for that favorite (stale favorite ref, speaker offline momentarily, or Sonos-side content issue) vs. a one-off.
   - **depends_on:** Nothing — log inspection only. Correlate against prior SonosKD diagnostics dumps if archived.
   - **suggested_agent:** vp.coder (the email is explicitly "context for Cody"; matches the repo-debug lane).
   - *Note:* This is genuinely optional. The service self-recovered and continued normally. No execution should occur from this triage session regardless.

No other action items. This email is contextual/diagnostic FYI — it does not carry an explicit instruction to execute work. The dedicated ToDo executor should treat it as acknowledged-and-shelved unless Kevin escalates the favorite-nr=6 rejection.

---

**delegation_strategy:** SEQUENTIAL (and effectively no-op) — only one optional, low-priority investigation item exists, and it depends on nothing. No parallel work warranted.

**suggested_response:** None required. This is an automated self-send diagnostics ping, not a message expecting a reply. If a confirmation is desired for the log trail:

> Acknowledged — SonosKD mint-desktop diagnostics received (commit 643bd54). Service healthy; noted the single transient rejection of favorite nr=6 ('All '90s') at 16:51:47, which self-resolved. No action taken. Filing as background unless the nr=6 rejection recurs.

---

End of triage brief. No delegation, task creation, file writes, or replies performed in this session per triage-only constraints.\
"""

# Second recovered row: fields are NOT blank-line separated (compact
# form), same `**field:** value` markdown-bold shape.
REAL_PROD_BRIEF_COMPACT_FORM = """\
== EMAIL TRIAGE BRIEF ==

**safety_status:** clean
**routing_decision:** review_required
**classification:** fyi
**priority:** p3 (background)
**subject_summary:** Unsolicited vendor marketing newsletter from Descope (auth/identity vendor) announcing July product updates, sent via HubSpot.

**action_items:**
1. • what: Decide whether to unsubscribe, ignore, or flag Descope as a vendor of interest (Descope is a CIAM/authentication vendor; no record Kevin currently uses it).
   • depends_on: Kevin's interest in auth tooling for the UA platform / freelance delivery work.
   • suggested_agent: simone
2. • what: No reply warranted — this is a one-way marketing send, not a question or request.
   • depends_on: N/A
   • suggested_agent: N/A

**key_details:**
- Sender: Descope `<team@hi.descope.com>` (external, sender_trusted=False).
- Transport: HubSpot marketing automation (`hubspotemail.net` message-id) — confirms this is a bulk newsletter, not a personal note.
- Inbox: Simone's AgentMail (`oddcity216@agentmail.to`).
- Body content is effectively empty in the materialized payload (subject only; `reply_extracted: False`) — standard product-update newsletter with no extracted instruction.
- No deadline, no request, no attachment flagged in the payload.
- Safety: no prompt injection, no social engineering, no instructions attempting to override system behavior. Only "risk" is that this address got onto a vendor marketing list.

**delegation_strategy:** SEQUENTIAL (trivial — single optional review step, no parallel work needed)

**suggested_response:** None. Newsletter send; do not reply. If Kevin wants to track Descope, surface the thread for his review; otherwise this can be auto-archived/ignored. Simone should not reply to or click links in marketing emails from this inbox without operator direction.

END OF TRIAGE BRIEF\
"""


class TestRealProdBriefsParse:
    """Both verbatim recovered prod briefs must fully parse post-fix.

    Before the fix, all 5 fields were '' for both (matching the prod
    finding: 7/8 hook_triage rows had classification='', priority='',
    subject_summary='').
    """

    def test_blank_line_form_parses_all_fields(self):
        parsed = parse_email_triage_brief(
            REAL_PROD_BRIEF_BLANK_LINE_FORM, sender_trusted=False
        )
        assert parsed["safety_status"] == "clean"
        assert parsed["routing_decision"] == "review_required"
        assert parsed["classification"] == "status_update"
        assert parsed["priority"] == "p3 (background)"
        assert "SonosKD diagnostics dump" in parsed["subject_summary"]
        # Nothing was defaulted -- the model's actual verdict was used.
        assert parsed["_fallback_fields"] == []
        # No residual markdown decoration leaked into any captured value.
        for field in _TRIAGE_FIELDS:
            assert "**" not in parsed[field]

    def test_compact_form_parses_all_fields(self):
        parsed = parse_email_triage_brief(
            REAL_PROD_BRIEF_COMPACT_FORM, sender_trusted=False
        )
        assert parsed["safety_status"] == "clean"
        assert parsed["routing_decision"] == "review_required"
        assert parsed["classification"] == "fyi"
        assert parsed["priority"] == "p3 (background)"
        assert "Descope" in parsed["subject_summary"]
        assert parsed["_fallback_fields"] == []


class TestMarkdownDecorationTolerance:
    """Synthetic coverage for decoration shapes the ticket names explicitly."""

    def test_bulleted_dash_form_parses(self):
        text = (
            "== EMAIL TRIAGE BRIEF ==\n"
            "- **safety_status:** clean\n"
            "- **routing_decision:** trusted_execute\n"
            "- **classification:** instruction\n"
            "- **priority:** p1 (soon)\n"
            "- **subject_summary:** bulleted-dash decoration form\n"
        )
        parsed = parse_email_triage_brief(text, sender_trusted=True)
        assert parsed["safety_status"] == "clean"
        assert parsed["routing_decision"] == "trusted_execute"
        assert parsed["classification"] == "instruction"
        assert parsed["priority"] == "p1 (soon)"
        assert parsed["subject_summary"] == "bulleted-dash decoration form"
        assert parsed["_fallback_fields"] == []

    def test_legacy_plain_form_still_parses(self):
        """The original (never-actually-emitted) plain form keeps working."""
        text = (
            "safety_status: clean\n"
            "routing_decision: trusted_execute\n"
            "classification: instruction\n"
            "priority: p2\n"
            "subject_summary: plain unbolded form\n"
        )
        parsed = parse_email_triage_brief(text, sender_trusted=True)
        assert parsed["safety_status"] == "clean"
        assert parsed["routing_decision"] == "trusted_execute"
        assert parsed["_fallback_fields"] == []


class TestJsonEnvelopePrecedence:
    """Task 4: the fenced ```json envelope is parsed first."""

    def test_json_envelope_wins_over_markdown_fields(self):
        text = (
            "== EMAIL TRIAGE BRIEF ==\n"
            "```json\n"
            '{"safety_status": "clean", "routing_decision": "trusted_execute", '
            '"classification": "instruction", "priority": "p1", '
            '"subject_summary": "from json envelope"}\n'
            "```\n"
            "**safety_status:** quarantine\n"
            "**routing_decision:** quarantine\n"
        )
        parsed = parse_email_triage_brief(text, sender_trusted=True)
        # JSON envelope values win, not the (deliberately contradictory)
        # markdown fields below it.
        assert parsed["safety_status"] == "clean"
        assert parsed["routing_decision"] == "trusted_execute"
        assert parsed["subject_summary"] == "from json envelope"
        assert parsed["_fallback_fields"] == []

    def test_malformed_json_envelope_falls_back_to_regex(self):
        text = (
            "```json\n"
            "{not valid json,,,}\n"
            "```\n"
            "**safety_status:** clean\n"
            "**routing_decision:** trusted_execute\n"
            "**classification:** instruction\n"
            "**priority:** p2\n"
            "**subject_summary:** regex fallback after bad json\n"
        )
        parsed = parse_email_triage_brief(text, sender_trusted=True)
        assert parsed["safety_status"] == "clean"
        assert parsed["routing_decision"] == "trusted_execute"
        assert parsed["subject_summary"] == "regex fallback after bad json"
        assert parsed["_fallback_fields"] == []

    def test_partial_json_envelope_fills_missing_fields_from_regex(self):
        text = (
            "```json\n"
            '{"safety_status": "clean", "routing_decision": "trusted_execute"}\n'
            "```\n"
            "**classification:** instruction\n"
            "**priority:** p2\n"
            "**subject_summary:** classification comes from markdown\n"
        )
        parsed = parse_email_triage_brief(text, sender_trusted=True)
        assert parsed["safety_status"] == "clean"
        assert parsed["routing_decision"] == "trusted_execute"
        assert parsed["classification"] == "instruction"
        assert parsed["subject_summary"] == "classification comes from markdown"
        assert parsed["_fallback_fields"] == []


class TestFallbackFieldsMarker:
    """Task 3: `_fallback_fields` names every defaulted (non-model) value."""

    def test_empty_text_defaults_all_fields_and_flags_them(self):
        parsed = parse_email_triage_brief("", sender_trusted=False)
        assert sorted(parsed["_fallback_fields"]) == sorted(_TRIAGE_FIELDS)
        assert parsed["routing_decision"] == "review_required"

    def test_empty_text_trusted_sender_defaults_to_trusted_execute(self):
        parsed = parse_email_triage_brief("", sender_trusted=True)
        assert parsed["routing_decision"] == "trusted_execute"
        assert "routing_decision" in parsed["_fallback_fields"]

    def test_partial_text_only_flags_missing_fields(self):
        text = "**safety_status:** quarantine\n**routing_decision:** quarantine\n"
        parsed = parse_email_triage_brief(text, sender_trusted=True)
        assert parsed["safety_status"] == "quarantine"
        assert parsed["routing_decision"] == "quarantine"
        # These two were present in the model output -- not fallback.
        assert "safety_status" not in parsed["_fallback_fields"]
        assert "routing_decision" not in parsed["_fallback_fields"]
        # These three were absent -- correctly flagged.
        assert set(parsed["_fallback_fields"]) == {
            "classification",
            "priority",
            "subject_summary",
        }

    def test_invalid_enum_value_is_corrected_and_flagged_as_fallback(self):
        text = "**safety_status:** unclear\n**routing_decision:** maybe_quarantine_this\n"
        parsed = parse_email_triage_brief(text, sender_trusted=True)
        # "unclear" isn't a valid enum value -> corrected to "clean" and
        # flagged, since that corrected value is not a genuine model verdict.
        assert parsed["safety_status"] == "clean"
        assert "safety_status" in parsed["_fallback_fields"]
        assert parsed["routing_decision"] == "quarantine"
        assert "routing_decision" in parsed["_fallback_fields"]


class TestEmailHandlerPromptRequiresJsonEnvelope:
    """Task 4: the prompt built for the email-handler route requires a fenced
    ```json envelope with the 5 routing fields.

    ``_build_email_handler_prompt`` touches no ``self`` state, so it can be
    exercised on a bare receiver (same pattern as the existing
    ``test_invalid_retry_policies_warning_retains_exception_context`` seam in
    ``tests/unit/test_hooks_service_logging_context.py``).
    """

    def test_prompt_includes_fenced_json_schema_with_all_5_fields(self):
        action = HookAction(
            kind="agent",
            name="EmailHook",
            session_key="agentmail_thd_prompt_test",
            to="email-handler",
            message="== INBOUND EMAIL ==\nfrom: kevin@example.com\nsubject: test",
        )
        prompt = HooksService._build_email_handler_prompt(object(), action, action.message)

        assert "```json" in prompt
        for field in _TRIAGE_FIELDS:
            assert f'"{field}"' in prompt
        assert "MUST begin with a fenced" in prompt
        # The markdown fallback fields must still be documented (belt and
        # suspenders per the ticket's "parse envelope FIRST, fall back to
        # regexes" requirement).
        assert "- safety_status: clean | quarantine" in prompt


class TestHooksServiceSenderTrustedDefault:
    """Task 2: the ``sender_trusted`` default in the hook-triage call site
    must be ``False``, not ``True`` -- the key is absent from metadata on
    essentially every real-world row, so the default is what actually
    decides the routing outcome.

    ``_record_email_task_hook_triage`` also touches no other ``self`` state
    (all DB access goes through module-level imports + the
    ``UA_ACTIVITY_DB_PATH`` env var), so it too can run on a bare receiver.
    """

    @staticmethod
    def _make_open_email_task(conn, *, task_id: str, session_key: str, extra_metadata=None):
        metadata = {"session_key": session_key}
        if extra_metadata:
            metadata.update(extra_metadata)
        ensure_schema(conn)
        upsert_item(
            conn,
            {
                "task_id": task_id,
                "source_kind": "email",
                "status": "open",
                "metadata": metadata,
            },
        )

    def test_missing_sender_trusted_key_defaults_to_review_required(self, tmp_path):
        db_path = str(tmp_path / "activity_state.db")
        with patch.dict(os.environ, {"UA_ACTIVITY_DB_PATH": db_path}, clear=False):
            conn = connect_runtime_db(db_path)
            self._make_open_email_task(
                conn, task_id="email:missing-trust", session_key="agentmail_thd_missing_trust"
            )
            conn.close()

            HooksService._record_email_task_hook_triage(
                object(),
                session_key="agentmail_thd_missing_trust",
                session_id="sess-missing-trust",
                execution_summary="",
            )

            conn2 = connect_runtime_db(db_path)
            item = get_item(conn2, "email:missing-trust")
            conn2.close()

        hook_triage = (item or {}).get("metadata", {}).get("hook_triage", {})
        # Before the fix (default sender_trusted=True): "trusted_execute".
        # After the fix (default sender_trusted=False): "review_required".
        assert hook_triage.get("routing_decision") == "review_required"
        assert "routing_decision" in (hook_triage.get("fallback_fields") or [])

    def test_explicit_sender_trusted_true_still_yields_trusted_execute(self, tmp_path):
        db_path = str(tmp_path / "activity_state.db")
        with patch.dict(os.environ, {"UA_ACTIVITY_DB_PATH": db_path}, clear=False):
            conn = connect_runtime_db(db_path)
            self._make_open_email_task(
                conn,
                task_id="email:explicit-trust",
                session_key="agentmail_thd_explicit_trust",
                extra_metadata={"sender_trusted": True},
            )
            conn.close()

            HooksService._record_email_task_hook_triage(
                object(),
                session_key="agentmail_thd_explicit_trust",
                session_id="sess-explicit-trust",
                execution_summary="",
            )

            conn2 = connect_runtime_db(db_path)
            item = get_item(conn2, "email:explicit-trust")
            conn2.close()

        hook_triage = (item or {}).get("metadata", {}).get("hook_triage", {})
        assert hook_triage.get("routing_decision") == "trusted_execute"
