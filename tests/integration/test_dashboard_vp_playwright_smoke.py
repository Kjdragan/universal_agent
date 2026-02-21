from __future__ import annotations

import os
import time

import pytest


def _env_true(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on"}


def test_dashboard_vp_dispatch_smoke_playwright() -> None:
    """
    Optional end-to-end smoke:
    - opens dashboard
    - dispatches one VP mission from UI
    - verifies queued status + objective appears in recent missions

    Guarded by UA_E2E_ENABLE_PLAYWRIGHT=1.
    """
    if not _env_true("UA_E2E_ENABLE_PLAYWRIGHT", False):
        pytest.skip("set UA_E2E_ENABLE_PLAYWRIGHT=1 to run browser smoke tests")

    playwright = pytest.importorskip("playwright.sync_api")
    sync_playwright = playwright.sync_playwright

    base_url = (os.getenv("UA_E2E_BASE_URL") or "http://127.0.0.1:3000").rstrip("/")
    dashboard_password = (os.getenv("UA_E2E_DASHBOARD_PASSWORD") or "").strip()
    headless = not _env_true("UA_E2E_HEADFUL", False)
    objective = f"playwright-vp-smoke-{int(time.time())}"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        try:
            page.goto(f"{base_url}/dashboard", wait_until="domcontentloaded", timeout=60_000)

            # If dashboard auth is enabled, allow login when password is provided.
            password_input = page.locator('input[type="password"]')
            if password_input.count() > 0 and password_input.first.is_visible():
                if not dashboard_password:
                    pytest.skip(
                        "dashboard login required; set UA_E2E_DASHBOARD_PASSWORD for this smoke test"
                    )
                password_input.first.fill(dashboard_password)
                page.get_by_role("button", name="Sign In").click()
                page.wait_for_load_state("networkidle", timeout=30_000)

            page.get_by_text("External Primary Agent Operations").first.wait_for(timeout=30_000)
            objective_input = page.get_by_placeholder("Objective for external primary agent...")
            objective_input.fill(objective)
            page.get_by_role("button", name="Dispatch").click()

            page.get_by_text("Queued mission").first.wait_for(timeout=30_000)
            page.get_by_text(objective).first.wait_for(timeout=60_000)
        finally:
            browser.close()
