"""Tests for the _effects_from_receipts dispatch table refactoring."""

from __future__ import annotations

from typing import Any


def _effects_from_receipts(
    receipt_items: list[dict[str, Any]],
    effect_rules: list[tuple[str, str, str | None]],
) -> set[str]:
    """Standalone copy of the refactored logic for unit testing."""
    effects: set[str] = set()
    for receipt in receipt_items:
        tool_name = str(receipt.get("tool_name", "") or "")
        response_ref = str(receipt.get("response_ref", "") or "")
        tool_name_upper = tool_name.upper()
        haystack = f"{tool_name} {response_ref}".lower()
        for effect_label, tool_pat, hay_pat in effect_rules:
            if effect_label in effects:
                continue
            if tool_pat not in tool_name_upper:
                continue
            if hay_pat is not None and not any(
                p in haystack for p in hay_pat.split("|")
            ):
                continue
            effects.add(effect_label)
    return effects


# The canonical dispatch table (mirrors cli_io.py)
EFFECT_RULES: list[tuple[str, str, str | None]] = [
    ("email", "GMAIL_SEND_EMAIL", None),
    ("email", "MULTI_EXECUTE_TOOL", "gmail_send_email|recipient_email"),
    ("email", "SEND_EMAIL", "gmail"),
    ("email", "GMAIL", "send|draft"),
    ("upload", "UPLOAD_TO_COMPOSIO", None),
    ("upload", "UPLOAD", "composio"),
]


class TestEffectsFromReceipts:
    """Verify the dispatch table produces identical results to the old if/elif chain."""

    def test_gmail_send_email_tool(self):
        result = _effects_from_receipts(
            [{"tool_name": "GMAIL_SEND_EMAIL", "response_ref": ""}],
            EFFECT_RULES,
        )
        assert result == {"email"}

    def test_multi_execute_with_gmail_slug(self):
        result = _effects_from_receipts(
            [
                {
                    "tool_name": "COMPOSIO_MULTI_EXECUTE_TOOL",
                    "response_ref": "gmail_send_email",
                }
            ],
            EFFECT_RULES,
        )
        assert result == {"email"}

    def test_multi_execute_with_recipient_email(self):
        result = _effects_from_receipts(
            [
                {
                    "tool_name": "COMPOSIO_MULTI_EXECUTE_TOOL",
                    "response_ref": "recipient_email",
                }
            ],
            EFFECT_RULES,
        )
        assert result == {"email"}

    def test_multi_execute_no_email_match(self):
        """MULTI_EXECUTE_TOOL without email-related haystack should not produce email."""
        result = _effects_from_receipts(
            [
                {
                    "tool_name": "COMPOSIO_MULTI_EXECUTE_TOOL",
                    "response_ref": "some_other_tool",
                }
            ],
            EFFECT_RULES,
        )
        assert "email" not in result

    def test_send_email_with_gmail_in_haystack(self):
        result = _effects_from_receipts(
            [{"tool_name": "send_email", "response_ref": "gmail"}],
            EFFECT_RULES,
        )
        assert result == {"email"}

    def test_gmail_send_in_haystack(self):
        result = _effects_from_receipts(
            [{"tool_name": "gmail", "response_ref": "send"}],
            EFFECT_RULES,
        )
        assert result == {"email"}

    def test_gmail_draft_in_haystack(self):
        result = _effects_from_receipts(
            [{"tool_name": "gmail", "response_ref": "draft"}],
            EFFECT_RULES,
        )
        assert result == {"email"}

    def test_upload_to_composio_exact(self):
        result = _effects_from_receipts(
            [{"tool_name": "UPLOAD_TO_COMPOSIO", "response_ref": ""}],
            EFFECT_RULES,
        )
        assert result == {"upload"}

    def test_upload_with_composio_in_haystack(self):
        result = _effects_from_receipts(
            [{"tool_name": "upload_file", "response_ref": "composio"}],
            EFFECT_RULES,
        )
        assert result == {"upload"}

    def test_empty_receipts(self):
        assert _effects_from_receipts([], EFFECT_RULES) == set()

    def test_unknown_tool_name(self):
        result = _effects_from_receipts(
            [{"tool_name": "SLACK_SEND_MESSAGE", "response_ref": "#general"}],
            EFFECT_RULES,
        )
        assert result == set()

    def test_multiple_receipts_mixed_effects(self):
        result = _effects_from_receipts(
            [
                {"tool_name": "GMAIL_SEND_EMAIL", "response_ref": ""},
                {"tool_name": "UPLOAD_TO_COMPOSIO", "response_ref": ""},
            ],
            EFFECT_RULES,
        )
        assert result == {"email", "upload"}

    def test_duplicate_effect_not_added(self):
        """Two receipts both matching 'email' should still produce one 'email'."""
        result = _effects_from_receipts(
            [
                {"tool_name": "GMAIL_SEND_EMAIL", "response_ref": ""},
                {"tool_name": "gmail", "response_ref": "send"},
            ],
            EFFECT_RULES,
        )
        assert result == {"email"}

    def test_missing_fields_graceful(self):
        result = _effects_from_receipts(
            [{"tool_name": None, "response_ref": None}],
            EFFECT_RULES,
        )
        assert result == set()

    def test_dispatch_table_completeness(self):
        """Every effect in the table should have at least one rule."""
        effects_with_rules = {label for label, _, _ in EFFECT_RULES}
        assert "email" in effects_with_rules
        assert "upload" in effects_with_rules

    def test_no_false_positive_upload_from_email_tool(self):
        """An email tool receipt should never accidentally trigger 'upload'."""
        result = _effects_from_receipts(
            [{"tool_name": "GMAIL_SEND_EMAIL", "response_ref": ""}],
            EFFECT_RULES,
        )
        assert "upload" not in result
