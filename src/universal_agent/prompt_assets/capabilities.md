# 🧠 Agent Capabilities Registry

Generated: 2026-02-09 13:11:50

## 🤖 Specialist Agents (Micro-Agents)
Delegate full workflows to these specialists based on value-add.

### 🛠 General Tools
- **browserbase**: Expert for browser automation using Browserbase cloud infrastructure.

**WHEN TO DELEGATE:**
- User asks to scrape website content
- User wants to take screenshots of web pages
- User needs to fill forms or interact with web pages
- User asks to test website functionality
- User mentions "automate browser", "headless chrome", "web automation"
- User wants to navigate and extract data from dynamic web pages

**THIS SUB-AGENT:**
- Creates isolated browser sessions in the cloud
- Navigates pages and interacts with DOM elements
- Captures full-page or viewport screenshots
- Extracts rendered HTML/text from JavaScript-heavy pages
- Handles multi-step browser workflows autonomously
- Saves artifacts to work_products/browser/

  -> Delegate: `Task(subagent_type='browserbase', ...)`
- **evaluation-judge**: **Sub-Agent Purpose:** Evaluate task completion by inspecting workspace artifacts.

**WHEN TO USE:**
- URW Orchestrator calls you after a phase/task execution.
- You inspect files and determine if success criteria are met.
- Output: Structured verdict with confidence and reasoning.

  -> Delegate: `Task(subagent_type='evaluation-judge', ...)`
- **mermaid-expert**: Create Mermaid diagrams for flowcharts, sequences, ERDs, and architectures. Masters syntax for all diagram types and styling. Use PROACTIVELY for visual documentation, system diagrams, or process flows.
  -> Delegate: `Task(subagent_type='mermaid-expert', ...)`
- **report-writer**: Multi-phase research report generator.
  -> Delegate: `Task(subagent_type='report-writer', ...)`
- **config**: Internal specialized agent.
  -> Delegate: `Task(subagent_type='config', ...)`
- **critic**: Internal specialized agent.
  -> Delegate: `Task(subagent_type='critic', ...)`
- **integration**: Internal specialized agent.
  -> Delegate: `Task(subagent_type='integration', ...)`
- **logfire_reader**: Internal specialized agent.
  -> Delegate: `Task(subagent_type='logfire_reader', ...)`
- **runner**: Internal specialized agent.
  -> Delegate: `Task(subagent_type='runner', ...)`

### 🎨 Creative & Media
- **image-expert**: MANDATORY DELEGATION TARGET for ALL image generation and editing tasks.

**WHEN TO DELEGATE:**
- User asks to generate, create, or edit images
- User mentions "picture", "graphic", "visual", "infographic"
- User requests .png, .jpg, .jpeg, .webp file creation/editing
- User wants iterative refinement of images through conversation

**THIS SUB-AGENT:**
- Generates images from text using Gemini 2.5 Flash
- Edits existing images with natural language instructions
- Creates infographics and visual content for reports
- Saves outputs to work_products/media/

  -> Delegate: `Task(subagent_type='image-expert', ...)`
- **video-creation-expert**: 🎬 MANDATORY DELEGATION TARGET for ALL video and audio tasks.

**WHEN TO DELEGATE (MUST BE USED):**
- User asks to download, process, or edit video/audio
- User mentions YouTube, video, audio, MP3, MP4, trimming, cutting
- User asks to create video content, transitions, effects
- User asks to extract audio from video
- User asks about video format conversion
- User asks to combine, concatenate, or merge videos

**THIS SUB-AGENT:**
- Downloads YouTube videos/audio via yt-dlp (mcp-youtube)
- Processes video/audio with FFmpeg (video-audio MCP)
- Applies effects, transitions, overlays, text
- Saves final outputs to work_products/media/

Main agent should pass video paths and desired operations in task description.

  -> Delegate: `Task(subagent_type='video-creation-expert', ...)`
- **video-remotion-expert**: 🎥 SPECIALIZED AGENT for programmatic video generation using Remotion.

**WHEN TO DELEGATE:**
- User asks to generate videos using code/React/Remotion
- User wants to create data-driven videos (dynamic text, images, products)
- User needs to render compositions locally or via AWS Lambda
- User asks to specific Remotion tasks (scaffold, render, deploy)

**THIS SUB-AGENT:**
- Scaffolds Remotion projects with Zod schemas
- Generates JSON props for video compositions
- Renders videos using local CLI (subprocess) or Lambda
- Manages "Hybrid Architecture" (Lambda primary, CLI fallback)

**CAPABILITIES:**
- Create React-based video compositions
- Programmatic rendering with dynamic props
- Cloud rendering (AWS Lambda) setup and execution

  -> Delegate: `Task(subagent_type='video-remotion-expert', ...)`

### 🔬 Research & Analysis
- **research-specialist**: Sub-agent for a unified research pipeline: Search followed by automated Crawl & Refine.

  -> Delegate: `Task(subagent_type='research-specialist', ...)`
- **professor**: Academic oversight and skill creation.
  -> Delegate: `Task(subagent_type='professor', ...)`
- **scribe**: Memory logging and fact recording.
  -> Delegate: `Task(subagent_type='scribe', ...)`

### 🏢 Operations & Communication
- **slack-expert**: Expert for Slack workspace interactions.

**WHEN TO DELEGATE:**
- User mentions 'slack', 'channel', '#channel-name'
- User asks to 'post to slack', 'summarize messages', 'what was discussed in'

**THIS SUB-AGENT:**
- Lists channels to find IDs
- Fetches conversation history
- Posts formatted messages

  -> Delegate: `Task(subagent_type='slack-expert', ...)`

### ⚙️ Engineering & Code
- **task-decomposer**: **Sub-Agent Purpose:** Decompose complex requests into phases for harness execution.

**WHEN TO USE:**
- URW Orchestrator delegates decomposition tasks here.
- You analyze request complexity and create phased plans.
- Output: `macro_tasks.json` with phases, tasks, and success criteria.

  -> Delegate: `Task(subagent_type='task-decomposer', ...)`

## 📚 Skills (Standard Operating Procedures)
- **gmail**: Comprehensive guide for using Gmail tools to send emails, manage drafts, and handle attachments. Use when the user asks to send emails, check inbox, search contacts, or manage labels.
- **nano-banana-pro**: Generate or edit images via Gemini 3 Pro Image (Nano Banana Pro).
- **voice-call**: Start voice calls via the OpenClaw voice-call plugin.
- ~~**tmux**~~ (Unavailable: Missing binary: tmux)
- **excalidraw-free**: Create Excalidraw diagrams. USE WHEN user specifically asks for Excalidraw. WORKFLOWS - mind-maps, swimlane, process-flow.
- **playwright-cli**: Automates browser interactions for web testing, form filling, screenshots, and data extraction. Use when the user needs to navigate websites, interact with web pages, fill forms, take screenshots, test web applications, or extract information from web pages.
- **coding-agent**: Run Codex CLI, Claude Code, OpenCode, or Pi Coding Agent via background process for programmatic control.
- **skill-creator**: Guide for creating effective skills. This skill should be used when users want to create a new skill (or update an existing skill) that extends Claude's capabilities with specialized knowledge, workflows, or tool integrations.
- **slack**: Use when you need to control Slack from Clawdbot via the slack tool, including reacting to messages or pinning/unpinning items in Slack channels or DMs.
- **clawhub**: Use the ClawHub CLI to search, install, update, and publish agent skills from clawhub.com. Use when you need to fetch new skills on the fly, sync installed skills to latest or a specific version, or publish new/updated skill folders with the npm-installed clawhub CLI.
- ~~**obsidian**~~ (Unavailable: Missing binary: obsidian-cli)
- ~~**1password**~~ (Unavailable: Missing binary: op)
- **image-generation**: AI-powered image generation and editing using Gemini. Use when Claude needs to: (1) Generate images from text descriptions, (2) Edit existing images with instructions, (3) Create infographics or charts, (4) Generate visual assets for reports/presentations, (5) Work with .png, .jpg, .jpeg, .webp files for editing.
- **video-remotion**: Comprehensive skill for programmatic video generation using the Remotion framework.
Enables creating, scaffolding, and rendering React-based videos via Python orchestration.
Supports both Local (CLI) and Cloud (Lambda) rendering.

- **logfire-eval**: Evaluate Universal Agent runs by analyzing Logfire traces via the Logfire MCP. Use when the user asks to analyze a run, debug issues, review heartbeat activity, identify bottlenecks, or generate a post-run evaluation report. Triggers on phrases like "analyze the run", "trace analysis", "logfire eval", "what happened in that heartbeat", "check the traces".
- **weather**: Get current weather and forecasts (no API key required).
- **gemini**: Gemini CLI for one-shot Q&A, summaries, and generation.
- **media-processing**: Process multimedia files with FFmpeg (video/audio encoding, conversion, streaming, filtering, hardware acceleration) and ImageMagick (image manipulation, format conversion, batch processing, effects, composition). Use when converting media formats, encoding videos with specific codecs (H.264, H.265, VP9), resizing/cropping images, extracting audio from video, applying filters and effects, optimizing file sizes, creating streaming manifests (HLS/DASH), generating thumbnails, batch processing images, creating composite images, or implementing media processing pipelines. Supports 100+ formats, hardware acceleration (NVENC, QSV), and complex filtergraphs.
- **youtube-tutorial-learning**: Turn a YouTube tutorial into durable learning artifacts (concept doc + runnable implementation) stored under UA_ARTIFACTS_DIR.
USE WHEN user provides a YouTube URL and wants to learn/implement from it.

- **pdf**: Comprehensive PDF manipulation toolkit for extracting text and tables, creating new PDFs, merging/splitting documents, and handling forms. When Claude needs to fill in a PDF form or programmatically process, generate, or analyze PDF documents at scale.
- **discord**: Use when you need to control Discord from Clawdbot via the discord tool: send messages, react, post or upload stickers, upload emojis, run polls, manage threads/pins/search, create/edit/delete channels and categories, fetch permissions or member/role/channel info, or handle moderation actions in Discord DMs or channels.
- **goplaces**: Query Google Places API (New) via the goplaces CLI for text search, place details, resolve, and reviews. Use for human-friendly place lookup or JSON output for scripts.
- **google_calendar**: Manage calendar events using Google Calendar via Composio.
- **zread-dependency-docs**: Read documentation and code from open source GitHub repositories using the ZRead MCP server
- ~~**spotify-player**~~ (Unavailable: Missing any of: ['spogo', 'spotify_player'])
- **taskwarrior**: Manage tasks and reminders using Taskwarrior (CLI).
- **github**: Interact with GitHub using the `gh` CLI. Use `gh issue`, `gh pr`, `gh run`, and `gh api` for issues, PRs, CI runs, and advanced queries.
- **manim_skill**: Create mathematical animations using Manim (Community Edition or ManimGL). Includes best practices, examples, and rules for creating high-quality videos.
- **gifgrep**: Search GIF providers with CLI/TUI, download results, and extract stills/sheets.
- **trello**: Manage Trello boards, lists, and cards via the Trello REST API.
- ~~**summarize**~~ (Unavailable: Missing binary: summarize)
- **local-places**: Search for places (restaurants, cafes, etc.) via Google Places API proxy on localhost.
- **git-commit**: Stage all changes, create a helpful commit message, and push to remote. Use this when the user wants to quickly commit and push their changes without manually staging files or writing a commit message.
- **dependency-management**: Standardized protocol for managing project dependencies using `uv` and handling system-level requirements.
- **notion**: Notion API for creating and managing pages, databases, and blocks.
- **mcp-builder**: Guide for creating high-quality MCP (Model Context Protocol) servers that enable LLMs to interact with external services through well-designed tools. Use when building MCP servers to integrate external APIs or services, whether in Python (FastMCP) or Node/TypeScript (MCP SDK).
- **agent-browser**: Automates browser interactions for web testing, form filling, screenshots, and data extraction. Use when the user needs to navigate websites, interact with web pages, fill forms, take screenshots, test web applications, or extract information from web pages.
- **telegram**: Send and receive messages via Telegram using a Bot.
- **browser-debugging**: Dynamic guide for debugging Web UI issues using the Browser Subagent.
- **webapp-testing**: Toolkit for interacting with and testing local web applications using Playwright. Supports verifying frontend functionality, debugging UI behavior, capturing browser screenshots, and viewing browser logs.
- **nano-pdf**: Edit PDFs with natural-language instructions using the nano-pdf CLI.
- **gemini-url-context-scraper**: Fast URL/PDF/image content extraction using Gemini "URL Context" (built-in web/PDF reader) via google-genai.
Use when the user wants to: scrape a URL, read/summarize a PDF, extract structured facts from public web content, or create an interim “scraped context” work product for downstream tasks.
Writes interim outputs to CURRENT_SESSION_WORKSPACE/work_products by default, and can persist outputs under UA_ARTIFACTS_DIR on request. Produces runnable PEP 723 + `uv run` scripts with dotenv auto-loading (no hardcoded secrets).


## 🛠 Toolkits & Capabilities
- Core: Gmail, Calendar, Sheets, Docs, GitHub, Slack, Notion
- Discovery: Run `mcp__composio__get_actions` to find more tools.