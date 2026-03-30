from __future__ import annotations

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
