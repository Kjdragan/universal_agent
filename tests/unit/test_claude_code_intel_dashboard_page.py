from __future__ import annotations

from pathlib import Path

_PAGE = Path("web-ui/app/dashboard/claude-code-intel/page.tsx")


def test_claude_code_intel_dashboard_uses_gateway_api_and_markdown_viewer():
    content = _PAGE.read_text(encoding="utf-8")
    assert 'const API_BASE = "/api/dashboard/gateway";' in content
    assert "/api/v1/dashboard/claude-code-intel?limit=50" in content
    assert "ReactMarkdown" in content
    assert "Search vault pages" in content
    assert "Recent intelligence runs" in content


def test_claude_code_intel_dashboard_has_packet_and_knowledge_sections():
    content = _PAGE.read_text(encoding="utf-8")
    assert "Rolling builder intelligence for ClaudeDevs" in content
    assert "Latest Report" in content
    assert "Rolling 14-Day Narrative" in content
    assert "Capability Bundles" in content
    assert "Knowledge Base" in content
    assert "Packet History" in content
