# Video Creation & Editing Capabilities

## Overview
We have introduced a **Video Creation Expert** sub-agent to the Universal Agent ecosystem. This expert allows the agent to download, edit, and process video content directly within the session workspace.

## Core Capabilities
The Video Creation Expert uses a combination of `yt-dlp` for media acquisition and `ffmpeg` (via `ffmpeg-python` and direct shell commands) for processing.

### Key Features
1.  **YouTube Download**: Downloads videos in high quality (1080p typically) using `yt-dlp`.
2.  **Trimming**: Accurately cuts video segments based on start/end timestamps.
3.  **Text Overlays**: Adds custom text annotations to video frames.
4.  **Transitions**: Applies effects like Fade In and Fade Out.
5.  **Audio Processing**: Capability to extract or manipulate audio tracks (implied by `video-audio-mcp`).

## Architecture & Dependencies
-   **Sub-agent**: `video-creation-expert` (Prompt definition in `.claude/agents/video-creation-expert.md`)
-   **Dependencies**:
    -   `yt-dlp`: Added to `pyproject.toml` for reliable video downloading.
    -   `ffmpeg-python`: Python bindings for constructing complex filter graphs.
    -   `ffmpeg` (Static Build): The underlying engine performing the media processing.
-   **MCP Server**: `external_mcps/video-audio-mcp/server.py` handles the tool logic.

## Known Issues & Workarounds

### 1. FFmpeg Font Handling (Text Overlay)
**Issue**: The static build of `ffmpeg` often lacks `fontconfig` support, meaning it cannot access system fonts by name (e.g., `font='Arial'`). This results in errors when using the `drawtext` filter.

**Solution**:
We implemented a robust two-layer fix:
1.  **Auto-Detection Patch**: The `server.py` was patched to scan common Linux font directories (e.g., `/usr/share/fonts/truetype/liberation/`) and inject the **absolute path** to a valid font file if `font_file` is missing in the request.
2.  **Manual Fallback**: If the tool fails or the agent constructs the request incorrectly, specific instructions are provided to the agent to:
    -   Verify font existence (`ls -F /usr/share/fonts/...`).
    -   Pass the **explicit absolute path** to the font file (e.g., `/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf`) in the tool call.
    -   Use direct `bash` execution of `ffmpeg` as a final fallback if the Python wrapper obfuscates the parameters.

### 2. File Paths
**Requirement**: The agent uses absolute paths for reliability. Output videos are stored in `work_products/media/` to ensure they persist and are tracked by the session observer.

## Usage Example
To use these capabilities, simply ask the agent:
> "Download the video at [URL], trim it to the first 20 seconds, and add a text overlay 'Demo' in the center."

The routing logic will dispatch this request to the `video-creation-expert`.
