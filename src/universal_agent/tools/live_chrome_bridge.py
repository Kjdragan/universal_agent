"""
Live Chrome Bridge — connect UA agents to a running Chrome session via CDP.

Uses Playwright's connect_over_cdp() to attach to a Chrome instance launched with
--remote-debugging-port=9222.  This lets agents browse as the logged-in user,
interact with authenticated sites, and orchestrate across tabs.

Feature-gated: requires UA_ENABLE_LIVE_CHROME=1 (env or Infisical).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from typing import Any, Dict, List, Optional

from claude_agent_sdk import tool

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level session state
# ---------------------------------------------------------------------------

_state: Dict[str, Any] = {
    "playwright": None,
    "browser": None,
    "connected": False,
    "cdp_url": None,
}


def _enabled() -> bool:
    """Check whether the live-chrome feature flag is on."""
    return os.getenv("UA_ENABLE_LIVE_CHROME", "0") == "1"


def _ok(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2, ensure_ascii=True)}]}


def _err(message: str) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": f"error: {message}"}]}


def _gate() -> Optional[Dict[str, Any]]:
    """Return an error response if the feature is disabled, else None."""
    if not _enabled():
        return _err(
            "Live Chrome is disabled. Set UA_ENABLE_LIVE_CHROME=1 to enable. "
            "Also ensure Chrome is running with --remote-debugging-port=9222"
        )
    return None


def _get_page(tab_index: int = 0):
    """Return the Playwright Page for the given tab index, or raise."""
    browser = _state.get("browser")
    if not browser or not _state.get("connected"):
        raise RuntimeError("Not connected to Chrome. Call chrome_connect first.")

    contexts = browser.contexts
    if not contexts:
        raise RuntimeError("No browser contexts found. Is Chrome running?")

    pages = contexts[0].pages
    if tab_index < 0 or tab_index >= len(pages):
        raise IndexError(
            f"Tab index {tab_index} out of range. "
            f"Open tabs: {len(pages)} (indices 0-{len(pages) - 1})"
        )
    return pages[tab_index]


# ---------------------------------------------------------------------------
# Tool: chrome_connect
# ---------------------------------------------------------------------------

@tool(
    name="chrome_connect",
    description=(
        "Attach to a running Chrome browser via Chrome DevTools Protocol (CDP). "
        "Chrome must be launched with --remote-debugging-port=9222. "
        "Returns session info and a list of currently open tabs. "
        "This lets you browse websites where the user is already signed in."
    ),
    input_schema={
        "cdp_url": str,  # default http://localhost:9222
    },
)
async def chrome_connect_wrapper(args: Dict[str, Any]) -> Dict[str, Any]:
    gate = _gate()
    if gate:
        return gate
    try:
        return await _chrome_connect_impl(args)
    except Exception as exc:
        log.exception("chrome_connect failed")
        return _err(str(exc))


async def _chrome_connect_impl(args: Dict[str, Any]) -> Dict[str, Any]:
    # Check args first, then Infisical environment, then default to localhost
    configured_url = str(os.getenv("LIVE_CHROME_CDP_URL", "")).strip()
    cdp_url = str(args.get("cdp_url", "") or "").strip() or configured_url or "http://localhost:9222"

    # Disconnect existing session if any
    if _state.get("connected"):
        await _chrome_close_impl({})

    from playwright.async_api import async_playwright as _async_playwright

    pw = await _async_playwright().start()
    browser = await pw.chromium.connect_over_cdp(
        cdp_url,
        timeout=15_000,
    )

    _state["playwright"] = pw
    _state["browser"] = browser
    _state["connected"] = True
    _state["cdp_url"] = cdp_url

    tabs = _build_tab_list(browser)

    return _ok({
        "status": "connected",
        "cdp_url": cdp_url,
        "contexts": len(browser.contexts),
        "tabs": tabs,
    })


# ---------------------------------------------------------------------------
# Tool: chrome_list_tabs
# ---------------------------------------------------------------------------

@tool(
    name="chrome_list_tabs",
    description="List all open tabs in the connected Chrome session with their titles and URLs.",
    input_schema={},
)
async def chrome_list_tabs_wrapper(args: Dict[str, Any]) -> Dict[str, Any]:
    gate = _gate()
    if gate:
        return gate
    try:
        tabs = _build_tab_list(_state.get("browser"))
        return _ok({"tabs": tabs})
    except Exception as exc:
        log.exception("chrome_list_tabs failed")
        return _err(str(exc))


def _build_tab_list(browser) -> List[Dict[str, Any]]:
    """Build a JSON-serializable list of open tabs."""
    if not browser or not browser.contexts:
        return []
    tabs = []
    for i, page in enumerate(browser.contexts[0].pages):
        tabs.append({
            "index": i,
            "title": page.title() if not asyncio.iscoroutinefunction(page.title) else "(async)",
            "url": page.url,
        })
    return tabs


# ---------------------------------------------------------------------------
# Tool: chrome_navigate
# ---------------------------------------------------------------------------

@tool(
    name="chrome_navigate",
    description="Navigate to a URL in an open tab of the connected Chrome session.",
    input_schema={
        "url": str,
        "tab_index": int,  # default 0
    },
)
async def chrome_navigate_wrapper(args: Dict[str, Any]) -> Dict[str, Any]:
    gate = _gate()
    if gate:
        return gate
    try:
        return await _chrome_navigate_impl(args)
    except Exception as exc:
        log.exception("chrome_navigate failed")
        return _err(str(exc))


async def _chrome_navigate_impl(args: Dict[str, Any]) -> Dict[str, Any]:
    url = str(args.get("url", "") or "").strip()
    if not url:
        return _err("url is required")

    tab_index = int(args.get("tab_index", 0) or 0)
    page = _get_page(tab_index)

    await page.goto(url, wait_until="domcontentloaded", timeout=30_000)

    return _ok({
        "navigated": True,
        "url": page.url,
        "title": await page.title(),
        "tab_index": tab_index,
    })


# ---------------------------------------------------------------------------
# Tool: chrome_screenshot
# ---------------------------------------------------------------------------

@tool(
    name="chrome_screenshot",
    description=(
        "Take a screenshot of the current page in the connected Chrome session. "
        "Returns a base64-encoded PNG image."
    ),
    input_schema={
        "tab_index": int,  # default 0
        "full_page": bool,  # default False
    },
)
async def chrome_screenshot_wrapper(args: Dict[str, Any]) -> Dict[str, Any]:
    gate = _gate()
    if gate:
        return gate
    try:
        return await _chrome_screenshot_impl(args)
    except Exception as exc:
        log.exception("chrome_screenshot failed")
        return _err(str(exc))


async def _chrome_screenshot_impl(args: Dict[str, Any]) -> Dict[str, Any]:
    tab_index = int(args.get("tab_index", 0) or 0)
    full_page = bool(args.get("full_page", False))

    page = _get_page(tab_index)
    screenshot_bytes = await page.screenshot(full_page=full_page, type="png")
    b64 = base64.b64encode(screenshot_bytes).decode("ascii")

    return {
        "content": [
            {"type": "text", "text": json.dumps({
                "tab_index": tab_index,
                "url": page.url,
                "title": await page.title(),
                "full_page": full_page,
                "image_format": "png",
                "image_size_bytes": len(screenshot_bytes) if screenshot_bytes else 0,
            })},
            {"type": "image", "data": b64, "mimeType": "image/png"},
        ]
    }


# ---------------------------------------------------------------------------
# Tool: chrome_click
# ---------------------------------------------------------------------------

@tool(
    name="chrome_click",
    description=(
        "Click an element on the page in the connected Chrome session. "
        "Use a CSS selector or text-based selector like 'text=Submit'."
    ),
    input_schema={
        "selector": str,
        "tab_index": int,  # default 0
    },
)
async def chrome_click_wrapper(args: Dict[str, Any]) -> Dict[str, Any]:
    gate = _gate()
    if gate:
        return gate
    try:
        return await _chrome_click_impl(args)
    except Exception as exc:
        log.exception("chrome_click failed")
        return _err(str(exc))


async def _chrome_click_impl(args: Dict[str, Any]) -> Dict[str, Any]:
    selector = str(args.get("selector", "") or "").strip()
    if not selector:
        return _err("selector is required (CSS selector or text=... locator)")

    tab_index = int(args.get("tab_index", 0) or 0)
    page = _get_page(tab_index)

    await page.click(selector, timeout=10_000)

    return _ok({
        "clicked": True,
        "selector": selector,
        "tab_index": tab_index,
        "url": page.url,
    })


# ---------------------------------------------------------------------------
# Tool: chrome_fill
# ---------------------------------------------------------------------------

@tool(
    name="chrome_fill",
    description=(
        "Fill an input field on the page in the connected Chrome session. "
        "Provide a CSS selector targeting the input and the value to type."
    ),
    input_schema={
        "selector": str,
        "value": str,
        "tab_index": int,  # default 0
    },
)
async def chrome_fill_wrapper(args: Dict[str, Any]) -> Dict[str, Any]:
    gate = _gate()
    if gate:
        return gate
    try:
        return await _chrome_fill_impl(args)
    except Exception as exc:
        log.exception("chrome_fill failed")
        return _err(str(exc))


async def _chrome_fill_impl(args: Dict[str, Any]) -> Dict[str, Any]:
    selector = str(args.get("selector", "") or "").strip()
    value = str(args.get("value", "") or "")
    if not selector:
        return _err("selector is required")

    tab_index = int(args.get("tab_index", 0) or 0)
    page = _get_page(tab_index)

    await page.fill(selector, value, timeout=10_000)

    return _ok({
        "filled": True,
        "selector": selector,
        "tab_index": tab_index,
    })


# ---------------------------------------------------------------------------
# Tool: chrome_snapshot
# ---------------------------------------------------------------------------

@tool(
    name="chrome_snapshot",
    description=(
        "Return an accessibility tree snapshot of the current page for AI reasoning. "
        "This gives a structured text view of all interactive elements on the page."
    ),
    input_schema={
        "tab_index": int,  # default 0
    },
)
async def chrome_snapshot_wrapper(args: Dict[str, Any]) -> Dict[str, Any]:
    gate = _gate()
    if gate:
        return gate
    try:
        return await _chrome_snapshot_impl(args)
    except Exception as exc:
        log.exception("chrome_snapshot failed")
        return _err(str(exc))


async def _chrome_snapshot_impl(args: Dict[str, Any]) -> Dict[str, Any]:
    tab_index = int(args.get("tab_index", 0) or 0)
    page = _get_page(tab_index)

    # Use Playwright's accessibility snapshot
    snapshot = await page.accessibility.snapshot()
    title = await page.title()

    return _ok({
        "tab_index": tab_index,
        "url": page.url,
        "title": title,
        "accessibility_tree": snapshot,
    })


# ---------------------------------------------------------------------------
# Tool: chrome_evaluate
# ---------------------------------------------------------------------------

@tool(
    name="chrome_evaluate",
    description=(
        "Execute a JavaScript expression in the page context of the connected Chrome session. "
        "Returns the result of the expression. Use for reading page data or state."
    ),
    input_schema={
        "expression": str,
        "tab_index": int,  # default 0
    },
)
async def chrome_evaluate_wrapper(args: Dict[str, Any]) -> Dict[str, Any]:
    gate = _gate()
    if gate:
        return gate
    try:
        return await _chrome_evaluate_impl(args)
    except Exception as exc:
        log.exception("chrome_evaluate failed")
        return _err(str(exc))


async def _chrome_evaluate_impl(args: Dict[str, Any]) -> Dict[str, Any]:
    expression = str(args.get("expression", "") or "").strip()
    if not expression:
        return _err("expression is required")

    tab_index = int(args.get("tab_index", 0) or 0)
    page = _get_page(tab_index)

    result = await page.evaluate(expression)

    # Ensure JSON-serializable
    try:
        json.dumps(result)
    except (TypeError, ValueError):
        result = str(result)

    return _ok({
        "tab_index": tab_index,
        "expression": expression,
        "result": result,
    })


# ---------------------------------------------------------------------------
# Tool: chrome_close
# ---------------------------------------------------------------------------

@tool(
    name="chrome_close",
    description=(
        "Disconnect from the Chrome session. "
        "This does NOT close the user's browser — it only releases the Playwright CDP handle."
    ),
    input_schema={},
)
async def chrome_close_wrapper(args: Dict[str, Any]) -> Dict[str, Any]:
    gate = _gate()
    if gate:
        return gate
    try:
        return await _chrome_close_impl(args)
    except Exception as exc:
        log.exception("chrome_close failed")
        return _err(str(exc))


async def _chrome_close_impl(args: Dict[str, Any]) -> Dict[str, Any]:
    browser = _state.get("browser")
    pw = _state.get("playwright")

    if browser:
        try:
            # disconnect() releases the CDP handle without closing Chrome
            await browser.close()
        except Exception:
            pass  # Best-effort cleanup

    if pw:
        try:
            await pw.stop()
        except Exception:
            pass

    _state["browser"] = None
    _state["playwright"] = None
    _state["connected"] = False
    _state["cdp_url"] = None

    return _ok({"status": "disconnected"})


# ---------------------------------------------------------------------------
# Collect all wrappers for registry
# ---------------------------------------------------------------------------

LIVE_CHROME_TOOLS = [
    chrome_connect_wrapper,
    chrome_list_tabs_wrapper,
    chrome_navigate_wrapper,
    chrome_screenshot_wrapper,
    chrome_click_wrapper,
    chrome_fill_wrapper,
    chrome_snapshot_wrapper,
    chrome_evaluate_wrapper,
    chrome_close_wrapper,
]
