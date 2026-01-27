#!/usr/bin/env python3
"""
Test script to inject the golden example prompt into the web UI and submit it.
Uses Playwright to automate the browser interaction.
"""

import sys
import time
from playwright.sync_api import sync_playwright

# The same prompt from the CLI golden example
GOLDEN_PROMPT = "search for the latest information from the Russia Ukraine war over the past 5 days, create a report, save that report as a pdf, and gmail me that pdf"

def main():
    with sync_playwright() as p:
        # Launch browser in headed mode so user can watch
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        
        # Capture console logs
        page.on("console", lambda msg: print(f"[BROWSER] {msg.type}: {msg.text}"))
        
        print("🌐 Navigating to web UI at http://localhost:3000...")
        page.goto("http://localhost:3000")
        
        # Wait for the page to fully load
        page.wait_for_load_state("networkidle")
        print("✅ Page loaded")
        
        # Take a screenshot to see what we're working with
        page.screenshot(path="/tmp/web_ui_initial.png", full_page=True)
        print("📸 Screenshot saved to /tmp/web_ui_initial.png")
        
        # Wait for WebSocket connection - look for "Connected" status or input becoming enabled
        print("⏳ Waiting for WebSocket connection...")
        max_wait_connection = 30
        for i in range(max_wait_connection):
            page_content = page.content()
            if "Connected" in page_content or "Disconnected" not in page_content:
                print("✅ WebSocket connected!")
                break
            # Also check if input is enabled
            try:
                input_el = page.locator('input[type="text"]').first
                if input_el.is_enabled(timeout=500):
                    print("✅ Input is enabled!")
                    break
            except:
                pass
            time.sleep(1)
            if i % 5 == 0:
                print(f"   Still waiting for connection... ({i}s)")
        else:
            print("⚠️ WebSocket may not be connected, proceeding anyway...")
        
        # Find the chat input - common selectors for chat inputs
        # Try various selectors that might match a chat input
        input_selectors = [
            'textarea[placeholder*="message"]',
            'textarea[placeholder*="Message"]',
            'input[placeholder*="message"]',
            'input[placeholder*="Message"]',
            'textarea',
            '[role="textbox"]',
            '.chat-input',
            '#chat-input',
            'input[type="text"]',
        ]
        
        chat_input = None
        for selector in input_selectors:
            try:
                element = page.locator(selector).first
                if element.is_visible(timeout=1000):
                    chat_input = element
                    print(f"✅ Found chat input with selector: {selector}")
                    break
            except Exception:
                continue
        
        if not chat_input:
            print("❌ Could not find chat input. Dumping page content for inspection...")
            print(page.content()[:2000])
            browser.close()
            sys.exit(1)
        
        # Type the golden prompt
        print(f"📝 Typing prompt: {GOLDEN_PROMPT[:50]}...")
        chat_input.fill(GOLDEN_PROMPT)
        
        # Take screenshot after filling
        page.screenshot(path="/tmp/web_ui_filled.png", full_page=True)
        print("📸 Screenshot saved to /tmp/web_ui_filled.png")
        
        # Press Enter to submit
        print("⏎ Pressing Enter to submit...")
        chat_input.press("Enter")
        
        # Wait a moment for the submission to register
        time.sleep(2)
        
        # Take screenshot after submission
        page.screenshot(path="/tmp/web_ui_submitted.png", full_page=True)
        print("📸 Screenshot saved to /tmp/web_ui_submitted.png")
        
        print("\n✅ Prompt submitted! The agent should now be processing.")
        print("🔍 Watching for activity... (will wait up to 10 minutes)")
        
        # Monitor for activity - look for tool calls, status updates, etc.
        start_time = time.time()
        max_wait = 600  # 10 minutes
        last_screenshot_time = 0
        screenshot_interval = 30  # Take screenshot every 30 seconds
        
        while time.time() - start_time < max_wait:
            elapsed = time.time() - start_time
            
            # Take periodic screenshots
            if elapsed - last_screenshot_time >= screenshot_interval:
                screenshot_path = f"/tmp/web_ui_progress_{int(elapsed)}s.png"
                page.screenshot(path=screenshot_path, full_page=True)
                print(f"📸 Progress screenshot at {int(elapsed)}s: {screenshot_path}")
                last_screenshot_time = elapsed
            
            # Check if we see completion indicators
            page_text = page.content()
            if "Task Complete" in page_text or "Successfully" in page_text:
                print("✅ Detected completion indicator!")
                break
            
            time.sleep(5)
        
        # Final screenshot
        page.screenshot(path="/tmp/web_ui_final.png", full_page=True)
        print("📸 Final screenshot saved to /tmp/web_ui_final.png")
        
        # Keep browser open for user to inspect
        print("\n🎯 Test complete. Browser will stay open for 30 seconds for inspection.")
        time.sleep(30)
        
        browser.close()
        print("🔒 Browser closed.")

if __name__ == "__main__":
    main()
