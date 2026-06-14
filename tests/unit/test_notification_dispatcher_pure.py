"""Unit tests for pure helper functions in notification_dispatcher.

Covers the five module-level functions that operate on plain dicts:
  - _delivery_state_for_channel: nested metadata.delivery lookup
  - _row_already_delivered: delivery freshness gate (delivered_at >= updated_at)
  - _channels_list: safe extraction of channels list from a record
  - _format_email_html: HTML email body construction
  - _format_telegram_text: Telegram message formatting with truncation

These are all pure functions with no I/O or async dependencies.
"""
from __future__ import annotations

from universal_agent.services.notification_dispatcher import (
    _channels_list,
    _delivery_state_for_channel,
    _format_email_html,
    _format_telegram_text,
    _row_already_delivered,
)


class TestDeliveryStateForChannel:
    def test_returns_entry_when_present(self):
        record = {"metadata": {"delivery": {"email": {"delivered_at": "2026-01-01T00:00:00Z"}}}}
        result = _delivery_state_for_channel(record, "email")
        assert result == {"delivered_at": "2026-01-01T00:00:00Z"}

    def test_returns_none_for_missing_channel(self):
        record = {"metadata": {"delivery": {"email": {"delivered_at": "ts"}}}}
        assert _delivery_state_for_channel(record, "telegram") is None

    def test_returns_none_when_no_metadata(self):
        assert _delivery_state_for_channel({}, "email") is None
        assert _delivery_state_for_channel({"metadata": None}, "email") is None

    def test_returns_none_when_no_delivery_key(self):
        assert _delivery_state_for_channel({"metadata": {}}, "email") is None

    def test_returns_none_when_entry_is_not_dict(self):
        record = {"metadata": {"delivery": {"email": "not-a-dict"}}}
        assert _delivery_state_for_channel(record, "email") is None


class TestRowAlreadyDelivered:
    def test_true_when_delivered_at_after_updated_at(self):
        record = {
            "updated_at": "2026-01-01T00:00:00Z",
            "metadata": {"delivery": {"email": {"delivered_at": "2026-01-02T00:00:00Z"}}},
        }
        assert _row_already_delivered(record, "email") is True

    def test_true_when_equal(self):
        ts = "2026-01-01T00:00:00Z"
        record = {
            "updated_at": ts,
            "metadata": {"delivery": {"email": {"delivered_at": ts}}},
        }
        assert _row_already_delivered(record, "email") is True

    def test_false_when_delivered_before_updated(self):
        record = {
            "updated_at": "2026-01-02T00:00:00Z",
            "metadata": {"delivery": {"email": {"delivered_at": "2026-01-01T00:00:00Z"}}},
        }
        assert _row_already_delivered(record, "email") is False

    def test_false_when_no_delivery_state(self):
        record = {"updated_at": "2026-01-01T00:00:00Z", "metadata": {}}
        assert _row_already_delivered(record, "email") is False

    def test_false_when_delivered_at_empty(self):
        record = {
            "updated_at": "2026-01-01T00:00:00Z",
            "metadata": {"delivery": {"email": {"delivered_at": ""}}},
        }
        assert _row_already_delivered(record, "email") is False

    def test_true_when_no_updated_at(self):
        record = {
            "metadata": {"delivery": {"email": {"delivered_at": "2026-01-01T00:00:00Z"}}},
        }
        assert _row_already_delivered(record, "email") is True

    def test_uses_lexical_iso_comparison(self):
        record = {
            "updated_at": "2026-12-01T10:00:00Z",
            "metadata": {"delivery": {"email": {"delivered_at": "2026-12-01T09:00:00Z"}}},
        }
        assert _row_already_delivered(record, "email") is False


class TestChannelsList:
    def test_returns_lowered_stripped_channels(self):
        assert _channels_list({"channels": ["Email", " Telegram "]}) == ["email", "telegram"]

    def test_returns_empty_for_missing_key(self):
        assert _channels_list({}) == []

    def test_returns_empty_for_non_list(self):
        assert _channels_list({"channels": "email"}) == []

    def test_filters_empty_strings(self):
        assert _channels_list({"channels": ["email", "", "  "]}) == ["email"]


class TestFormatEmailHtml:
    def test_includes_severity_and_title(self):
        html = _format_email_html({"title": "Disk Full", "severity": "error"})
        assert "[ERROR]" in html
        assert "Disk Full" in html

    def test_uses_full_message_over_summary(self):
        html = _format_email_html({
            "full_message": "Full details here",
            "summary": "Short summary",
        })
        assert "Full details here" in html

    def test_falls_back_to_summary(self):
        html = _format_email_html({"summary": "Short summary"})
        assert "Short summary" in html

    def test_includes_metadata_table_rows(self):
        html = _format_email_html({
            "metadata": {"job_id": "cron-42", "component": "gateway"},
        })
        assert "job_id" in html
        assert "cron-42" in html
        assert "component" in html

    def test_no_table_when_no_metadata(self):
        html = _format_email_html({"title": "Simple"})
        assert "<table" not in html

    def test_includes_kind_when_present(self):
        html = _format_email_html({"kind": "cron_failure"})
        assert "cron_failure" in html

    def test_no_kind_when_absent(self):
        html = _format_email_html({"title": "NoKind"})
        assert "kind:" not in html

    def test_defaults_to_info_severity(self):
        html = _format_email_html({})
        assert "[INFO]" in html


class TestFormatEmailHtmlDiagnosticContext:
    """The lifecycle-miss enrichment: task/run/tool-call context + run.log tail.

    Regression guard for the bare ``[ERROR] Execution Missing Lifecycle
    Mutation`` email that carried no debuggable context (which task, which run,
    or that the run made 0 tool calls because of an upstream ZAI 429).
    """

    def test_surfaces_todo_execution_context_keys(self):
        html = _format_email_html({
            "kind": "execution_missing_lifecycle_mutation",
            "session_id": "daemon_simone_todo",
            "metadata": {
                "task_id": "vp_failure:vp-mission-abc",
                "run_id": "run_650aa29cbe7f",
                "workspace_dir": "/opt/universal_agent/AGENT_RUN_WORKSPACES/run_650aa29cbe7f",
                "tool_calls": 0,
            },
        })
        assert "vp_failure:vp-mission-abc" in html
        assert "run_650aa29cbe7f" in html
        # session_id is lifted from the record, not metadata.
        assert "daemon_simone_todo" in html
        # 0 tool calls is the tell that the run never started — must render.
        assert "tool_calls" in html
        assert ">0<" in html

    def test_invalid_task_ids_list_is_comma_joined(self):
        html = _format_email_html({
            "metadata": {"invalid_task_ids": ["task-a", "task-b"]},
        })
        assert "task-a, task-b" in html

    def test_renders_escaped_log_tail_in_pre(self):
        html = _format_email_html({
            "metadata": {
                "log_tail": "API Error: Request rejected (429) [1313] <b>FUP</b>",
            },
        })
        assert "<pre" in html
        assert "429" in html
        assert "[1313]" in html
        # arbitrary error text must be HTML-escaped, not rendered as markup.
        assert "<b>FUP</b>" not in html
        assert "&lt;b&gt;FUP&lt;/b&gt;" in html

    def test_truncates_oversized_log_tail(self):
        tail = "X" * 5000
        html = _format_email_html({"metadata": {"log_tail": tail}})
        assert "truncated" in html
        # only the trailing window is kept, not the full 5000 chars.
        assert html.count("X") <= 3001

    def test_no_context_table_when_only_unlisted_metadata(self):
        html = _format_email_html({"metadata": {"unrelated_key": "value"}})
        assert "<table" not in html

    def test_surfaces_likely_cause_stop_reason_and_response_empty(self):
        # The new plain-language verdict + turn diagnostics must render in the
        # context table so the operator gets the answer without SSHing the VPS.
        html = _format_email_html({
            "kind": "execution_missing_lifecycle_mutation",
            "metadata": {
                "likely_cause": "Upstream inference throttle — 429 / Fair Usage Policy.",
                "stop_reason": "end_turn",
                "response_empty": False,
                "tool_calls": 0,
            },
        })
        assert "likely_cause" in html
        assert "Upstream inference throttle" in html
        assert "stop_reason" in html
        assert "end_turn" in html
        # response_empty=False must still render (False is not the skip sentinel).
        assert "response_empty" in html

    def test_renders_final_response_block_above_run_log(self):
        # The model's actual final output (here a verbatim 429/FUP rejection)
        # gets its own escaped <pre> block, distinct from the run-log tail.
        html = _format_email_html({
            "kind": "execution_missing_lifecycle_mutation",
            "metadata": {
                "final_response": "API Error: Request rejected (429) · [1313] Fair Usage Policy",
                "log_tail": "[01:34:11] === Turn completed (0 tool calls) ===",
            },
        })
        assert "Model output (final turn)" in html
        assert "Fair Usage Policy" in html
        # Both blocks present, model output rendered before the run-log tail.
        assert html.index("Model output (final turn)") < html.index("Run log tail")

    def test_final_response_is_html_escaped_and_truncated(self):
        # Markup placed at the END so it survives the trailing-window truncation.
        html = _format_email_html({
            "metadata": {"final_response": "Y" * 5000 + "<script>x</script>"},
        })
        assert "<script>x</script>" not in html
        assert "&lt;script&gt;" in html
        assert "truncated" in html


class TestFormatTelegramText:
    def test_basic_format(self):
        text = _format_telegram_text({
            "severity": "warning",
            "title": "High CPU",
            "full_message": "CPU at 95%",
        })
        assert "[WARNING]" in text
        assert "High CPU" in text
        assert "CPU at 95%" in text

    def test_truncates_long_messages(self):
        long_msg = "A" * 900
        text = _format_telegram_text({"full_message": long_msg})
        assert len(text) < len(long_msg)
        assert text.endswith("...")

    def test_uses_summary_as_fallback(self):
        text = _format_telegram_text({"summary": "Brief note"})
        assert "Brief note" in text
