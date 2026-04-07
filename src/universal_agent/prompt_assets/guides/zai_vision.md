## 👁️ IMAGE & VIDEO ANALYSIS (ZAI VISION MCP)
You have access to `mcp__zai_vision__*` tools powered by ZAI GLM-4.6V for analyzing images and video.

**Available tools:**
- `mcp__zai_vision__image_analysis` — General image analysis and description
- `mcp__zai_vision__extract_text_from_screenshot` — OCR / text extraction from screenshots
- `mcp__zai_vision__diagnose_error_screenshot` — Diagnose errors shown in screenshots
- `mcp__zai_vision__understand_technical_diagram` — Interpret technical diagrams and flowcharts
- `mcp__zai_vision__analyze_data_visualization` — Analyze charts, graphs, and data visualizations
- `mcp__zai_vision__ui_diff_check` — Compare UI screenshots for differences
- `mcp__zai_vision__ui_to_artifact` — Convert UI screenshots to code artifacts
- `mcp__zai_vision__video_analysis` — Analyze video content

**When to use:**
- When the user attaches an image file to chat, the file path will appear in the message (e.g., `uploads/screenshot.png`).
- Pass the **absolute file path** to the appropriate ZAI vision tool. The path is relative to `CURRENT_RUN_WORKSPACE` (`CURRENT_SESSION_WORKSPACE` is a legacy alias).
- For screenshots with text/lists/tables: prefer `extract_text_from_screenshot`.
- For error screenshots: prefer `diagnose_error_screenshot`.
- For general images: use `image_analysis`.

**IMPORTANT**: Do NOT try to view image files with `Read` or `cat`. You cannot see images natively. Always use ZAI Vision MCP tools for image understanding.
