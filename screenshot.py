from playwright.sync_api import sync_playwright
import time

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto('https://app.clearspringcg.com/?session_id=vp-mission-3080e1cbcd8d269f0f477150&attach=tail&role=viewer')
    time.sleep(5)
    page.screenshot(path='vp_mission_session.png')
    browser.close()
