"""
Unit tests for live_chrome_bridge.py

All Playwright interactions are mocked — no real Chrome required.
"""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_text(result: dict) -> dict:
    """Extract the JSON payload from a standard tool response."""
    content = result.get("content", [])
    for block in content:
        if block.get("type") == "text":
            try:
                return json.loads(block["text"])
            except json.JSONDecodeError:
                return {}
    return {}


def _is_error(result: dict) -> bool:
    content = result.get("content", [])
    for block in content:
        if block.get("type") == "text" and block["text"].startswith("error:"):
            return True
    return False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_state():
    """Reset module-level state between tests."""
    from universal_agent.tools.live_chrome_bridge import _state
    _state["playwright"] = None
    _state["browser"] = None
    _state["connected"] = False
    _state["cdp_url"] = None
    yield
    _state["playwright"] = None
    _state["browser"] = None
    _state["connected"] = False
    _state["cdp_url"] = None


@pytest.fixture(autouse=True)
def _enable_feature(monkeypatch):
    """Enable the live-chrome feature flag for all tests."""
    monkeypatch.setenv("UA_ENABLE_LIVE_CHROME", "1")


# ---------------------------------------------------------------------------
# Feature gate tests
# ---------------------------------------------------------------------------

class TestFeatureGate:
    """Tests that the feature gate blocks/allows correctly."""

    def test_gate_blocks_when_disabled(self, monkeypatch):
        monkeypatch.setenv("UA_ENABLE_LIVE_CHROME", "0")
        from universal_agent.tools.live_chrome_bridge import _gate
        result = _gate()
        assert result is not None
        assert _is_error(result)

    def test_gate_allows_when_enabled(self, monkeypatch):
        monkeypatch.setenv("UA_ENABLE_LIVE_CHROME", "1")
        from universal_agent.tools.live_chrome_bridge import _gate
        assert _gate() is None


# ---------------------------------------------------------------------------
# chrome_connect
# ---------------------------------------------------------------------------

class TestChromeConnect:
    """Tests for _chrome_connect_impl."""

    @pytest.mark.asyncio
    async def test_connect_success(self):
        from universal_agent.tools.live_chrome_bridge import (
            _chrome_connect_impl,
            _state,
        )

        mock_page = MagicMock()
        mock_page.title.return_value = "Test Page"
        mock_page.url = "https://example.com"

        mock_context = MagicMock()
        mock_context.pages = [mock_page]

        mock_browser = MagicMock()
        mock_browser.contexts = [mock_context]

        mock_chromium = MagicMock()
        mock_chromium.connect_over_cdp = AsyncMock(return_value=mock_browser)

        mock_pw = MagicMock()
        mock_pw.chromium = mock_chromium
        mock_pw.stop = AsyncMock()

        mock_async_pw_cls = MagicMock()
        mock_async_pw_cls.return_value.start = AsyncMock(return_value=mock_pw)

        with patch(
            "universal_agent.tools.live_chrome_bridge._async_playwright",
            new=mock_async_pw_cls,
            create=True,
        ):
            # We need to patch the import inside the function
            import universal_agent.tools.live_chrome_bridge as lcb
            original_impl = lcb._chrome_connect_impl

            async def patched_connect(args):
                # Mock the deferred import
                import sys
                mock_pw_module = MagicMock()
                mock_pw_module.async_playwright = mock_async_pw_cls
                sys.modules["playwright.async_api"] = mock_pw_module
                try:
                    return await original_impl(args)
                finally:
                    del sys.modules["playwright.async_api"]

            result = await patched_connect({"cdp_url": "http://localhost:9222"})

        payload = _parse_text(result)
        assert payload["status"] == "connected"
        assert payload["cdp_url"] == "http://localhost:9222"
        assert len(payload["tabs"]) == 1
        assert _state["connected"] is True

    @pytest.mark.asyncio
    async def test_connect_default_url(self):
        from universal_agent.tools.live_chrome_bridge import _chrome_connect_impl

        mock_browser = MagicMock()
        mock_browser.contexts = []
        mock_browser.close = AsyncMock()

        mock_chromium = MagicMock()
        mock_chromium.connect_over_cdp = AsyncMock(return_value=mock_browser)

        mock_pw = MagicMock()
        mock_pw.chromium = mock_chromium
        mock_pw.stop = AsyncMock()

        mock_async_pw_cls = MagicMock()
        mock_async_pw_cls.return_value.start = AsyncMock(return_value=mock_pw)

        import sys
        mock_pw_module = MagicMock()
        mock_pw_module.async_playwright = mock_async_pw_cls
        sys.modules["playwright.async_api"] = mock_pw_module
        try:
            result = await _chrome_connect_impl({})
        finally:
            del sys.modules["playwright.async_api"]

        payload = _parse_text(result)
        assert payload["cdp_url"] == "http://localhost:9222"


# ---------------------------------------------------------------------------
# chrome_list_tabs
# ---------------------------------------------------------------------------

class TestChromeListTabs:
    """Tests for tab listing."""

    def test_build_tab_list(self):
        from universal_agent.tools.live_chrome_bridge import _build_tab_list

        mock_page1 = MagicMock()
        mock_page1.title.return_value = "Gmail"
        mock_page1.url = "https://mail.google.com"

        mock_page2 = MagicMock()
        mock_page2.title.return_value = "GitHub"
        mock_page2.url = "https://github.com"

        mock_context = MagicMock()
        mock_context.pages = [mock_page1, mock_page2]

        mock_browser = MagicMock()
        mock_browser.contexts = [mock_context]

        tabs = _build_tab_list(mock_browser)
        assert len(tabs) == 2
        assert tabs[0]["url"] == "https://mail.google.com"
        assert tabs[1]["url"] == "https://github.com"
        assert tabs[0]["index"] == 0
        assert tabs[1]["index"] == 1

    def test_build_tab_list_empty(self):
        from universal_agent.tools.live_chrome_bridge import _build_tab_list

        mock_browser = MagicMock()
        mock_browser.contexts = []
        assert _build_tab_list(mock_browser) == []

    def test_build_tab_list_none(self):
        from universal_agent.tools.live_chrome_bridge import _build_tab_list
        assert _build_tab_list(None) == []


# ---------------------------------------------------------------------------
# chrome_navigate
# ---------------------------------------------------------------------------

class TestChromeNavigate:
    """Tests for _chrome_navigate_impl."""

    @pytest.mark.asyncio
    async def test_navigate_success(self):
        from universal_agent.tools.live_chrome_bridge import (
            _chrome_navigate_impl,
            _state,
        )

        mock_page = MagicMock()
        mock_page.goto = AsyncMock()
        mock_page.url = "https://example.com/after"
        mock_page.title = AsyncMock(return_value="After Nav")

        mock_context = MagicMock()
        mock_context.pages = [mock_page]

        mock_browser = MagicMock()
        mock_browser.contexts = [mock_context]

        _state["browser"] = mock_browser
        _state["connected"] = True

        result = await _chrome_navigate_impl({"url": "https://example.com/after"})
        payload = _parse_text(result)
        assert payload["navigated"] is True
        assert payload["url"] == "https://example.com/after"
        mock_page.goto.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_navigate_no_url(self):
        from universal_agent.tools.live_chrome_bridge import (
            _chrome_navigate_impl,
            _state,
        )
        _state["browser"] = MagicMock()
        _state["connected"] = True

        result = await _chrome_navigate_impl({"url": ""})
        assert _is_error(result)

    @pytest.mark.asyncio
    async def test_navigate_bad_tab_index(self):
        from universal_agent.tools.live_chrome_bridge import (
            _chrome_navigate_impl,
            _state,
        )

        mock_context = MagicMock()
        mock_context.pages = []

        mock_browser = MagicMock()
        mock_browser.contexts = [mock_context]

        _state["browser"] = mock_browser
        _state["connected"] = True

        # Should raise due to bad index, caught by wrapper → but we're calling impl directly
        with pytest.raises(IndexError):
            await _chrome_navigate_impl({"url": "https://foo.com", "tab_index": 5})


# ---------------------------------------------------------------------------
# chrome_click
# ---------------------------------------------------------------------------

class TestChromeClick:
    """Tests for _chrome_click_impl."""

    @pytest.mark.asyncio
    async def test_click_success(self):
        from universal_agent.tools.live_chrome_bridge import _chrome_click_impl, _state

        mock_page = MagicMock()
        mock_page.click = AsyncMock()
        mock_page.url = "https://example.com"

        mock_context = MagicMock()
        mock_context.pages = [mock_page]

        mock_browser = MagicMock()
        mock_browser.contexts = [mock_context]

        _state["browser"] = mock_browser
        _state["connected"] = True

        result = await _chrome_click_impl({"selector": "button#submit"})
        payload = _parse_text(result)
        assert payload["clicked"] is True
        mock_page.click.assert_awaited_once_with("button#submit", timeout=10_000)

    @pytest.mark.asyncio
    async def test_click_no_selector(self):
        from universal_agent.tools.live_chrome_bridge import _chrome_click_impl, _state
        _state["browser"] = MagicMock()
        _state["connected"] = True

        result = await _chrome_click_impl({"selector": ""})
        assert _is_error(result)


# ---------------------------------------------------------------------------
# chrome_fill
# ---------------------------------------------------------------------------

class TestChromeFill:
    """Tests for _chrome_fill_impl."""

    @pytest.mark.asyncio
    async def test_fill_success(self):
        from universal_agent.tools.live_chrome_bridge import _chrome_fill_impl, _state

        mock_page = MagicMock()
        mock_page.fill = AsyncMock()

        mock_context = MagicMock()
        mock_context.pages = [mock_page]

        mock_browser = MagicMock()
        mock_browser.contexts = [mock_context]

        _state["browser"] = mock_browser
        _state["connected"] = True

        result = await _chrome_fill_impl({"selector": "input#email", "value": "test@test.com"})
        payload = _parse_text(result)
        assert payload["filled"] is True
        mock_page.fill.assert_awaited_once_with("input#email", "test@test.com", timeout=10_000)


# ---------------------------------------------------------------------------
# chrome_evaluate
# ---------------------------------------------------------------------------

class TestChromeEvaluate:
    """Tests for _chrome_evaluate_impl."""

    @pytest.mark.asyncio
    async def test_evaluate_returns_result(self):
        from universal_agent.tools.live_chrome_bridge import (
            _chrome_evaluate_impl,
            _state,
        )

        mock_page = MagicMock()
        mock_page.evaluate = AsyncMock(return_value={"users": 42})

        mock_context = MagicMock()
        mock_context.pages = [mock_page]

        mock_browser = MagicMock()
        mock_browser.contexts = [mock_context]

        _state["browser"] = mock_browser
        _state["connected"] = True

        result = await _chrome_evaluate_impl({"expression": "document.title"})
        payload = _parse_text(result)
        assert payload["result"] == {"users": 42}

    @pytest.mark.asyncio
    async def test_evaluate_no_expression(self):
        from universal_agent.tools.live_chrome_bridge import (
            _chrome_evaluate_impl,
            _state,
        )
        _state["browser"] = MagicMock()
        _state["connected"] = True

        result = await _chrome_evaluate_impl({"expression": ""})
        assert _is_error(result)


# ---------------------------------------------------------------------------
# chrome_snapshot
# ---------------------------------------------------------------------------

class TestChromeSnapshot:
    """Tests for _chrome_snapshot_impl."""

    @pytest.mark.asyncio
    async def test_snapshot_returns_tree(self):
        from universal_agent.tools.live_chrome_bridge import (
            _chrome_snapshot_impl,
            _state,
        )

        mock_accessibility = MagicMock()
        mock_accessibility.snapshot = AsyncMock(return_value={"role": "document", "children": []})

        mock_page = MagicMock()
        mock_page.accessibility = mock_accessibility
        mock_page.title = AsyncMock(return_value="Test")
        mock_page.url = "https://example.com"

        mock_context = MagicMock()
        mock_context.pages = [mock_page]

        mock_browser = MagicMock()
        mock_browser.contexts = [mock_context]

        _state["browser"] = mock_browser
        _state["connected"] = True

        result = await _chrome_snapshot_impl({"tab_index": 0})
        payload = _parse_text(result)
        assert payload["title"] == "Test"
        assert "accessibility_tree" in payload


# ---------------------------------------------------------------------------
# chrome_close
# ---------------------------------------------------------------------------

class TestChromeClose:
    """Tests for _chrome_close_impl."""

    @pytest.mark.asyncio
    async def test_close_disconnects(self):
        from universal_agent.tools.live_chrome_bridge import _chrome_close_impl, _state

        mock_browser = MagicMock()
        mock_browser.close = AsyncMock()

        mock_pw = MagicMock()
        mock_pw.stop = AsyncMock()

        _state["browser"] = mock_browser
        _state["playwright"] = mock_pw
        _state["connected"] = True

        result = await _chrome_close_impl({})
        payload = _parse_text(result)
        assert payload["status"] == "disconnected"
        assert _state["connected"] is False
        assert _state["browser"] is None

    @pytest.mark.asyncio
    async def test_close_idempotent(self):
        from universal_agent.tools.live_chrome_bridge import _chrome_close_impl

        result = await _chrome_close_impl({})
        payload = _parse_text(result)
        assert payload["status"] == "disconnected"


# ---------------------------------------------------------------------------
# _get_page edge cases
# ---------------------------------------------------------------------------

class TestGetPage:
    """Tests for _get_page helper."""

    def test_not_connected_raises(self):
        from universal_agent.tools.live_chrome_bridge import _get_page
        with pytest.raises(RuntimeError, match="Not connected"):
            _get_page(0)

    def test_no_contexts_raises(self):
        from universal_agent.tools.live_chrome_bridge import _get_page, _state
        mock_browser = MagicMock()
        mock_browser.contexts = []
        _state["browser"] = mock_browser
        _state["connected"] = True

        with pytest.raises(RuntimeError, match="No browser contexts"):
            _get_page(0)

    def test_bad_index_raises(self):
        from universal_agent.tools.live_chrome_bridge import _get_page, _state
        mock_context = MagicMock()
        mock_context.pages = [MagicMock()]
        mock_browser = MagicMock()
        mock_browser.contexts = [mock_context]
        _state["browser"] = mock_browser
        _state["connected"] = True

        with pytest.raises(IndexError, match="out of range"):
            _get_page(5)


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------

class TestRegistry:
    """Verify live chrome tools are registered correctly."""

    def test_tools_included_when_enabled(self, monkeypatch):
        monkeypatch.setenv("UA_ENABLE_LIVE_CHROME", "1")
        from universal_agent.tools.internal_registry import get_live_chrome_tools
        tools = get_live_chrome_tools()
        assert len(tools) == 9  # 9 tool wrappers

    def test_tools_excluded_when_disabled(self, monkeypatch):
        monkeypatch.setenv("UA_ENABLE_LIVE_CHROME", "0")
        from universal_agent.tools.internal_registry import get_live_chrome_tools
        tools = get_live_chrome_tools()
        assert len(tools) == 0
