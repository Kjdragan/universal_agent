from __future__ import annotations

from universal_agent import gateway_server
from universal_agent.services.email_task_bridge import infer_delivery_mode


def test_delivery_mode_defaults_to_standard_for_comprehensive_report():
    mode = infer_delivery_mode(
        subject="Research request",
        body="Please create a comprehensive report with detailed analysis and citations.",
    )

    assert mode == "standard_report"


def test_delivery_mode_detects_fast_summary_language():
    mode = infer_delivery_mode(
        subject="Quick update",
        body="Give me a quick summary right away. Keep it brief.",
    )

    assert mode == "fast_summary"


def test_delivery_mode_detects_enhanced_multimedia_requests():
    mode = infer_delivery_mode(
        subject="Advanced package",
        body="Use NotebookLM and include an infographic plus audio overview.",
    )

    assert mode == "enhanced_report"


def test_tracked_chat_email_request_prefers_interactive_email_for_simple_creative_work():
    mode = gateway_server._infer_tracked_chat_delivery_mode(
        "Create a story about a chocolate Easter bunny and email it to me."
    )

    assert mode == "interactive_email"


def test_tracked_chat_email_request_keeps_report_mode_for_report_language():
    mode = gateway_server._infer_tracked_chat_delivery_mode(
        "Research the market and email me a detailed report with an executive brief."
    )

    assert mode == "standard_report"
