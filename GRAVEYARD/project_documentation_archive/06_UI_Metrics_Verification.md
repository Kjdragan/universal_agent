# UI Metrics Verification and Key Work Products Fix (2026-01-21)

## Overview
This document details the successful debugging and resolution of two critical UI issues in the Universal Agent:
1.  **Token Count Display**: The "TOKENS" metric in the UI was persistently showing `0`.
2.  **Key Work Products Panel**: The panel was not consistently displaying files created by the agent, particularly those in the `work_products/` directory.

## Issue 1: Token Count Display (Zero Tokens)

### Root Cause
The `agent_core.py` module tracks token usage by examining the `usage` attribute of incoming messages from the LLM provider (Anthropic). However, two specific issues were preventing this data from being recorded:
1.  **Usage Logic Location**: The usage extraction logic was nested inside the `ToolUseBlock` handling loop. This meant that text-only responses (which account for a significant portion of tokens) were completely ignored.
2.  **Missing Message IDs**: The token accumulation logic relied on message IDs to prevent double-counting (`if msg_id not in self._processed_msg_ids`). However, `ResultMessage` objects (which often contain the final usage stats) frequently lack an `id` attribute. Logic that strictly required an ID caused these messages to be skipped.

### The Fix
*   **Moved Logic**: The token extraction logic was moved to the top of the `AssistantMessage` handling block in `_run_conversation`. This ensures it runs for *every* message, regardless of whether it contains tool calls or just text.
*   **Handle Missing IDs**: The logic was updated to process messages even if they lack an ID, assuming that such messages (like `ResultMessage`) are unique events in the stream that should be counted.
    ```python
    # Before
    if msg_id and msg_id not in self._processed_msg_ids: ...

    # After q
    if (msg_id and msg_id not in self._processed_msg_ids) or not msg_id: ...
    ```

### Verification
*   Created a `test_ws.py` script to connect to the WebSocket and listen for `token_usage` events.
*   Confirmed that usage is now correctly streaming and accumulating.
*   Browser automation test confirmed the UI displays the total count (e.g., ~121k tokens).

## Issue 2: Key Work Products Panel (Files Missing)

### Root Cause
The `Key Work Products` panel in the UI (`page.tsx`) uses a filter to decide which files to display.
1.  **Restrictive Filter**: The filter was too aggressive, only allowing specific extensions and names.
2.  **Source Discrimination**: It treated files from the `work_products/` directory with the same skepticism as root files.

### The Fix
*   **Relaxed Logic for `work_products/`**: Modified `web-ui/app/page.tsx` to automatically include *any* file found in the `work_products/` directory, regardless of its name or extension. The assumption is that if an agent explicitly writes to that folder, it is a user-facing deliverable.

### Verification
*   Browser automation test confirmed that folders (e.g., `media`) and files created in `work_products/` are now picked up by the UI and displayed in the panel.

## Conclusion
Both features are now fully functional. The system correctly tracks and displays token costs to the user, and reliable surfaces generated content in the "Key Work Products" area.
