"""Unit tests for the centralized VP outbound email directive helper.

Verifies that ``services/vp_email_directive.build_vp_outbound_email_directive``
produces consistent, correctly-shaped prompt text that callers can embed
into VP mission briefings.

Per PRD § 5.4 (Step 3 of VP /goal + Failure-Rescue PRD).
"""

from __future__ import annotations

import pytest

from universal_agent.services.vp_email_directive import (
    KEVIN_EMAIL,
    SIMONE_INBOX,
    VP_MAILBOX,
    build_vp_outbound_email_directive,
    vp_display_name,
)


class TestVpDisplayName:
    def test_known_vp_ids_return_friendly_names(self):
        assert vp_display_name("vp.coder.primary") == "Cody"
        assert vp_display_name("vp.general.primary") == "Atlas"

    def test_unknown_vp_id_returns_input_as_fallback(self):
        assert vp_display_name("vp.unknown.future") == "vp.unknown.future"

    def test_empty_vp_id_returns_generic_label(self):
        assert vp_display_name("") == "VP"
        assert vp_display_name(None) == "VP"  # type: ignore[arg-type]

    def test_whitespace_is_stripped(self):
        assert vp_display_name("  vp.coder.primary  ") == "Cody"


class TestBuildVpOutboundEmailDirective:
    def test_basic_invocation_includes_canonical_addresses(self):
        text = build_vp_outbound_email_directive(vp_id="vp.coder.primary")
        assert KEVIN_EMAIL in text
        assert SIMONE_INBOX in text
        assert VP_MAILBOX in text

    def test_includes_subject_prefix(self):
        text = build_vp_outbound_email_directive(vp_id="vp.coder.primary")
        assert "[VP Status]" in text

    def test_custom_subject_prefix(self):
        text = build_vp_outbound_email_directive(
            vp_id="vp.general.primary",
            subject_prefix="[Intel]",
        )
        assert "[Intel]" in text
        # Default isn't present when overridden.
        assert "[VP Status]" not in text

    def test_body_header_uses_friendly_vp_name(self):
        text = build_vp_outbound_email_directive(vp_id="vp.coder.primary")
        assert "Cody" in text
        assert "vp.coder.primary" in text  # agent_id still appears alongside

    def test_atlas_directive_uses_atlas_name(self):
        text = build_vp_outbound_email_directive(vp_id="vp.general.primary")
        assert "Atlas" in text
        assert "vp.general.primary" in text

    def test_audience_hint_requestor_uses_reply_language(self):
        text = build_vp_outbound_email_directive(
            vp_id="vp.coder.primary",
            audience_hint="requestor",
        )
        assert "reply to the original sender" in text
        assert "the requestor" in text

    def test_audience_hint_kevin_uses_send_language(self):
        text = build_vp_outbound_email_directive(
            vp_id="vp.coder.primary",
            audience_hint="kevin",
        )
        assert f"send an email to {KEVIN_EMAIL}" in text
        assert "directly to Kevin" in text

    def test_failure_path_note_included_by_default(self):
        text = build_vp_outbound_email_directive(vp_id="vp.coder.primary")
        assert "Failure path" in text
        assert "do NOT email Kevin a failure" in text
        assert "vp_mission_failure" in text or "failure-rescue" in text
        assert "escalate_vp_failure_to_operator" in text

    def test_failure_path_note_can_be_disabled(self):
        text = build_vp_outbound_email_directive(
            vp_id="vp.coder.primary",
            include_failure_path_note=False,
        )
        assert "Failure path" not in text
        assert "vp_mission_failure" not in text

    def test_output_is_valid_multiline_string(self):
        text = build_vp_outbound_email_directive(vp_id="vp.coder.primary")
        assert isinstance(text, str)
        assert "\n" in text
        # Starts and ends with newlines for clean concatenation.
        assert text.startswith("\n")
        assert text.endswith("\n")

    def test_override_addresses_for_testing(self):
        text = build_vp_outbound_email_directive(
            vp_id="vp.coder.primary",
            kevin_email="test-kevin@example.com",
            simone_inbox="test-simone@example.com",
            vp_mailbox="test-vp@example.com",
        )
        assert "test-kevin@example.com" in text
        assert "test-simone@example.com" in text
        assert "test-vp@example.com" in text
        # Default addresses not present when overridden.
        assert KEVIN_EMAIL not in text
        assert SIMONE_INBOX not in text


class TestProactiveCodieIntegration:
    """Smoke-test that proactive_codie's task description now uses the helper."""

    def test_cleanup_description_includes_canonical_email_directive(self):
        from universal_agent.services.proactive_codie import (
            _cleanup_task_description,
        )

        desc = _cleanup_task_description(chosen_theme="dead-code removal")
        # Helper output is present (key markers from the canonical directive).
        assert "[VP Status]" in desc
        assert SIMONE_INBOX in desc
        assert VP_MAILBOX in desc
        assert "Cody (vp.coder.primary)" in desc
        # Failure-path note also present.
        assert "Failure path" in desc

    def test_cleanup_description_no_longer_contains_legacy_codie_name(self):
        """Legacy 'CODIE'/'Codie' branding in the description body should be Cody now."""
        from universal_agent.services.proactive_codie import (
            _cleanup_task_description,
        )

        desc = _cleanup_task_description(chosen_theme="any theme")
        # The body header (intro) was updated from "CODIE should..." to "Cody should..."
        assert desc.startswith("Cody should proactively improve")
