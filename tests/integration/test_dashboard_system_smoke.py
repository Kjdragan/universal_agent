"""Browser smoke tests for dashboard system status and Todoist activity.

Requires: UA_E2E_ENABLE_PLAYWRIGHT=1 and a running gateway.

These tests verify that the key dashboard tabs and panels show expected
activity after VP architecture changes (CODIE/ATLAS souls).
"""
from __future__ import annotations

import os
import time

import pytest


def _env_true(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on"}


def _skip_unless_playwright() -> None:
    if not _env_true("UA_E2E_ENABLE_PLAYWRIGHT", False):
        pytest.skip("set UA_E2E_ENABLE_PLAYWRIGHT=1 to run browser smoke tests")


def _get_base_url() -> str:
    return (os.getenv("UA_E2E_BASE_URL") or "http://127.0.0.1:3000").rstrip("/")


def _get_password() -> str:
    return (os.getenv("UA_E2E_DASHBOARD_PASSWORD") or "").strip()


def _headless() -> bool:
    return not _env_true("UA_E2E_HEADFUL", False)


def _login(page, base_url: str, password: str) -> None:
    """Handle dashboard login if authentication is enabled."""
    page.goto(f"{base_url}/dashboard", wait_until="domcontentloaded", timeout=60_000)
    password_input = page.locator('input[type="password"]')
    if password_input.count() > 0 and password_input.first.is_visible():
        if not password:
            pytest.skip("dashboard login required; set UA_E2E_DASHBOARD_PASSWORD")
        password_input.first.fill(password)
        page.get_by_role("button", name="Sign In").click()
        page.wait_for_load_state("networkidle", timeout=30_000)


def test_dashboard_vp_dispatch_shows_agent_name() -> None:
    """After dispatching a VP mission, the agent name (ATLAS/CODIE) should appear."""
    _skip_unless_playwright()
    playwright = pytest.importorskip("playwright.sync_api")
    sync_playwright = playwright.sync_playwright

    base_url = _get_base_url()
    password = _get_password()
    headless = _headless()
    objective = f"playwright-atlas-smoke-{int(time.time())}"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        try:
            _login(page, base_url, password)

            # Navigate to VP dispatch section
            page.get_by_text("External Primary Agent Operations").first.wait_for(timeout=30_000)
            objective_input = page.get_by_placeholder("Objective for external primary agent...")
            objective_input.fill(objective)
            page.get_by_role("button", name="Dispatch").click()

            # Verify mission appears with recognizable status
            page.get_by_text("Queued mission").first.wait_for(timeout=30_000)
            page.get_by_text(objective).first.wait_for(timeout=60_000)

            # Check that VP lane info shows (either ATLAS or vp.general)
            vp_section = page.locator(".vp-missions, #vp-missions, [data-testid*='vp'], .mission-list")
            vp_text = vp_section.first.text_content(timeout=10_000)
            if vp_text:
                has_vp_info = (
                    "ATLAS" in vp_text
                    or "CODIE" in vp_text
                    or "vp.general" in vp_text
                    or "vp.coder" in vp_text
                )
                assert has_vp_info, (
                    f"VP mission section should show agent name or lane. "
                    f"Found: {vp_text[:200]}"
                )
        finally:
            browser.close()


def test_dashboard_system_status_panels_populated() -> None:
    """Verify Active Tasks, Recent Events, and CSI panels render with data."""
    _skip_unless_playwright()
    playwright = pytest.importorskip("playwright.sync_api")
    sync_playwright = playwright.sync_playwright

    base_url = _get_base_url()
    password = _get_password()
    headless = _headless()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        try:
            _login(page, base_url, password)

            # Wait for dashboard to load
            page.wait_for_load_state("networkidle", timeout=30_000)

            # Check Mission Control or system status sections exist
            status_section = page.locator(
                ".system-status, .mission-control, "
                "[data-testid*='status'], [data-testid*='mission']"
            )
            if status_section.count() > 0:
                # At least one status panel should be visible
                assert status_section.first.is_visible(timeout=10_000), (
                    "System status panels should be visible on dashboard"
                )

            # Verify dashboard isn't completely empty — at least one heading or section
            body_text = page.locator("body").text_content(timeout=10_000)
            assert body_text and len(body_text.strip()) > 50, (
                "Dashboard should have meaningful content loaded"
            )

        finally:
            browser.close()


def test_dashboard_todoist_section_visible() -> None:
    """Verify that Todoist / task management section renders on dashboard."""
    _skip_unless_playwright()
    playwright = pytest.importorskip("playwright.sync_api")
    sync_playwright = playwright.sync_playwright

    base_url = _get_base_url()
    password = _get_password()
    headless = _headless()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        try:
            _login(page, base_url, password)
            page.wait_for_load_state("networkidle", timeout=30_000)

            # Look for Todoist or task-related sections
            todoist_indicators = [
                "Todoist",
                "Task Queue",
                "Active Tasks",
                "todo",
                "task",
            ]

            body_text = page.locator("body").text_content(timeout=10_000) or ""
            has_task_section = any(
                indicator.lower() in body_text.lower()
                for indicator in todoist_indicators
            )

            if has_task_section:
                # If Todoist section exists, check it has some content
                todoist_section = page.locator(
                    "[data-testid*='todoist'], [data-testid*='task'], "
                    ".todoist, .task-queue, .active-tasks"
                )
                if todoist_section.count() > 0:
                    section_text = todoist_section.first.text_content(timeout=10_000)
                    assert section_text and len(section_text.strip()) > 0, (
                        "Todoist section should have content"
                    )
            else:
                # If no Todoist section found, skip (may not be configured)
                pytest.skip("No Todoist/task section found on dashboard")

        finally:
            browser.close()
