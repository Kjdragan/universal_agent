import sys
import time

from playwright.sync_api import sync_playwright


def main():
    print("Starting Playwright test...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        # Capture console errors
        def handle_console(msg):
            if msg.type == "error":
                print(f"BROWSER ERROR: {msg.text}")
            elif "Application error" in msg.text:
                print(f"BROWSER LOG: {msg.text}")
                
        def handle_error(err):
            print(f"PAGE ERROR: {err}")
            
        page.on("console", handle_console)
        page.on("pageerror", handle_error)
        
        try:
            print("Navigating to Dashboard...")
            page.goto('http://localhost:3000/dashboard', wait_until='networkidle')
            print("Dashboard loaded")
            
            # Click ToDo list link
            print("Clicking ToDo List link...")
            # finding the href="/dashboard/todolist"
            todo_link = page.locator('a[href="/dashboard/todolist"]')
            todo_link.wait_for()
            todo_link.click()
            
            print("Waiting for ToDo List to load...")
            page.wait_for_selector('text=To Do List')
            time.sleep(1) # just to let things settle
            
            print("Clicking Dashboard link to go back...")
            dash_link = page.locator('a[href="/dashboard"]')
            # click the first one that is visible
            dash_link.first.click()
            
            print("Waiting on dashboard...")
            # We wait a bit to see if console errors are emitted
            time.sleep(3)
            print("Finished.")
        except Exception as e:
            print(f"Script exception: {e}")
        finally:
            browser.close()

if __name__ == '__main__':
    main()
