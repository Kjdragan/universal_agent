# 🧠 Agent Capabilities Registry

Generated: 2026-02-08 15:50:29

## 📚 Skills (Standard Operating Procedures)
- **gmail**: Comprehensive guide for using Gmail tools to send emails, manage drafts, and handle attachments. Use when the user asks to send emails, check inbox, search contacts, or manage labels.
  - Path: `/home/kjdragan/lrepos/universal_agent/.claude/skills/gmail/SKILL.md`
- **nano-banana-pro**: Generate or edit images via Gemini 3 Pro Image (Nano Banana Pro).
  - Path: `/home/kjdragan/lrepos/universal_agent/.claude/skills/nano-banana-pro/SKILL.md`
- **voice-call**: Start voice calls via the OpenClaw voice-call plugin.
  - Path: `/home/kjdragan/lrepos/universal_agent/.claude/skills/voice-call/SKILL.md`
- **excalidraw-free**: Create Excalidraw diagrams. USE WHEN user specifically asks for Excalidraw. WORKFLOWS - mind-maps, swimlane, process-flow.
  - Path: `/home/kjdragan/lrepos/universal_agent/.claude/skills/graph-draw/SKILL.md`
- **playwright-cli**: Automates browser interactions for web testing, form filling, screenshots, and data extraction. Use when the user needs to navigate websites, interact with web pages, fill forms, take screenshots, test web applications, or extract information from web pages.
  - Path: `/home/kjdragan/lrepos/universal_agent/.claude/skills/playwright-cli/SKILL.md`
- **coding-agent**: Run Codex CLI, Claude Code, OpenCode, or Pi Coding Agent via background process for programmatic control.
  - Path: `/home/kjdragan/lrepos/universal_agent/.claude/skills/coding-agent/SKILL.md`
- **skill-creator**: Guide for creating effective skills. This skill should be used when users want to create a new skill (or update an existing skill) that extends Claude's capabilities with specialized knowledge, workflows, or tool integrations.
  - Path: `/home/kjdragan/lrepos/universal_agent/.claude/skills/skill-creator/SKILL.md`
- **slack**: Use when you need to control Slack from Clawdbot via the slack tool, including reacting to messages or pinning/unpinning items in Slack channels or DMs.
  - Path: `/home/kjdragan/lrepos/universal_agent/.claude/skills/slack/SKILL.md`
- **clawhub**: Use the ClawHub CLI to search, install, update, and publish agent skills from clawhub.com. Use when you need to fetch new skills on the fly, sync installed skills to latest or a specific version, or publish new/updated skill folders with the npm-installed clawhub CLI.
  - Path: `/home/kjdragan/lrepos/universal_agent/.claude/skills/clawhub/SKILL.md`
- **image-generation**: AI-powered image generation and editing using Gemini. Use when Claude needs to: (1) Generate images from text descriptions, (2) Edit existing images with instructions, (3) Create infographics or charts, (4) Generate visual assets for reports/presentations, (5) Work with .png, .jpg, .jpeg, .webp files for editing.
  - Path: `/home/kjdragan/lrepos/universal_agent/.claude/skills/image-generation/SKILL.md`
- **video-remotion**: Comprehensive skill for programmatic video generation using the Remotion framework.
Enables creating, scaffolding, and rendering React-based videos via Python orchestration.
Supports both Local (CLI) and Cloud (Lambda) rendering.

  - Path: `/home/kjdragan/lrepos/universal_agent/.claude/skills/video-remotion/SKILL.md`
- **logfire-eval**: Evaluate Universal Agent runs by analyzing Logfire traces via the Logfire MCP. Use when the user asks to analyze a run, debug issues, review heartbeat activity, identify bottlenecks, or generate a post-run evaluation report. Triggers on phrases like "analyze the run", "trace analysis", "logfire eval", "what happened in that heartbeat", "check the traces".
  - Path: `/home/kjdragan/lrepos/universal_agent/.claude/skills/logfire-eval/SKILL.md`
- **weather**: Get current weather and forecasts (no API key required).
  - Path: `/home/kjdragan/lrepos/universal_agent/.claude/skills/weather/SKILL.md`
- **gemini**: Gemini CLI for one-shot Q&A, summaries, and generation.
  - Path: `/home/kjdragan/lrepos/universal_agent/.claude/skills/gemini/SKILL.md`
- **media-processing**: Process multimedia files with FFmpeg (video/audio encoding, conversion, streaming, filtering, hardware acceleration) and ImageMagick (image manipulation, format conversion, batch processing, effects, composition). Use when converting media formats, encoding videos with specific codecs (H.264, H.265, VP9), resizing/cropping images, extracting audio from video, applying filters and effects, optimizing file sizes, creating streaming manifests (HLS/DASH), generating thumbnails, batch processing images, creating composite images, or implementing media processing pipelines. Supports 100+ formats, hardware acceleration (NVENC, QSV), and complex filtergraphs.
  - Path: `/home/kjdragan/lrepos/universal_agent/.claude/skills/media-processing/SKILL.md`
- **youtube-tutorial-learning**: Turn a YouTube tutorial into durable learning artifacts (concept doc + runnable implementation) stored under UA_ARTIFACTS_DIR.
USE WHEN user provides a YouTube URL and wants to learn/implement from it.

  - Path: `/home/kjdragan/lrepos/universal_agent/.claude/skills/youtube-tutorial-learning/SKILL.md`
- **pdf**: Comprehensive PDF manipulation toolkit for extracting text and tables, creating new PDFs, merging/splitting documents, and handling forms. When Claude needs to fill in a PDF form or programmatically process, generate, or analyze PDF documents at scale.
  - Path: `/home/kjdragan/lrepos/universal_agent/.claude/skills/pdf/SKILL.md`
- **discord**: Use when you need to control Discord from Clawdbot via the discord tool: send messages, react, post or upload stickers, upload emojis, run polls, manage threads/pins/search, create/edit/delete channels and categories, fetch permissions or member/role/channel info, or handle moderation actions in Discord DMs or channels.
  - Path: `/home/kjdragan/lrepos/universal_agent/.claude/skills/discord/SKILL.md`
- **goplaces**: Query Google Places API (New) via the goplaces CLI for text search, place details, resolve, and reviews. Use for human-friendly place lookup or JSON output for scripts.
  - Path: `/home/kjdragan/lrepos/universal_agent/.claude/skills/goplaces/SKILL.md`
- **google_calendar**: Manage calendar events using Google Calendar via Composio.
  - Path: `/home/kjdragan/lrepos/universal_agent/.claude/skills/google_calendar/SKILL.md`
- **zread-dependency-docs**: Read documentation and code from open source GitHub repositories using the ZRead MCP server
  - Path: `/home/kjdragan/lrepos/universal_agent/.claude/skills/zread-dependency-docs/SKILL.md`
- **taskwarrior**: Manage tasks and reminders using Taskwarrior (CLI).
  - Path: `/home/kjdragan/lrepos/universal_agent/.claude/skills/taskwarrior/SKILL.md`
- **github**: Interact with GitHub using the `gh` CLI. Use `gh issue`, `gh pr`, `gh run`, and `gh api` for issues, PRs, CI runs, and advanced queries.
  - Path: `/home/kjdragan/lrepos/universal_agent/.claude/skills/github/SKILL.md`
- **manim_skill**: Create mathematical animations using Manim (Community Edition or ManimGL). Includes best practices, examples, and rules for creating high-quality videos.
  - Path: `/home/kjdragan/lrepos/universal_agent/.claude/skills/manim_skill/SKILL.md`
- **gifgrep**: Search GIF providers with CLI/TUI, download results, and extract stills/sheets.
  - Path: `/home/kjdragan/lrepos/universal_agent/.claude/skills/gifgrep/SKILL.md`
- **trello**: Manage Trello boards, lists, and cards via the Trello REST API.
  - Path: `/home/kjdragan/lrepos/universal_agent/.claude/skills/trello/SKILL.md`
- **local-places**: Search for places (restaurants, cafes, etc.) via Google Places API proxy on localhost.
  - Path: `/home/kjdragan/lrepos/universal_agent/.claude/skills/local-places/SKILL.md`
- **git-commit**: Stage all changes, create a helpful commit message, and push to remote. Use this when the user wants to quickly commit and push their changes without manually staging files or writing a commit message.
  - Path: `/home/kjdragan/lrepos/universal_agent/.claude/skills/git-commit/SKILL.md`
- **dependency-management**: Standardized protocol for managing project dependencies using `uv` and handling system-level requirements.
  - Path: `/home/kjdragan/lrepos/universal_agent/.claude/skills/dependency-management/SKILL.md`
- **notion**: Notion API for creating and managing pages, databases, and blocks.
  - Path: `/home/kjdragan/lrepos/universal_agent/.claude/skills/notion/SKILL.md`
- **mcp-builder**: Guide for creating high-quality MCP (Model Context Protocol) servers that enable LLMs to interact with external services through well-designed tools. Use when building MCP servers to integrate external APIs or services, whether in Python (FastMCP) or Node/TypeScript (MCP SDK).
  - Path: `/home/kjdragan/lrepos/universal_agent/.claude/skills/mcp-builder/SKILL.md`
- **agent-browser**: Automates browser interactions for web testing, form filling, screenshots, and data extraction. Use when the user needs to navigate websites, interact with web pages, fill forms, take screenshots, test web applications, or extract information from web pages.
  - Path: `/home/kjdragan/lrepos/universal_agent/.claude/skills/agent-browser/SKILL.md`
- **telegram**: Send and receive messages via Telegram using a Bot.
  - Path: `/home/kjdragan/lrepos/universal_agent/.claude/skills/telegram/SKILL.md`
- **browser-debugging**: Dynamic guide for debugging Web UI issues using the Browser Subagent.
  - Path: `/home/kjdragan/lrepos/universal_agent/.claude/skills/browser-debugging/SKILL.md`
- **webapp-testing**: Toolkit for interacting with and testing local web applications using Playwright. Supports verifying frontend functionality, debugging UI behavior, capturing browser screenshots, and viewing browser logs.
  - Path: `/home/kjdragan/lrepos/universal_agent/.claude/skills/webapp-testing/SKILL.md`
- **nano-pdf**: Edit PDFs with natural-language instructions using the nano-pdf CLI.
  - Path: `/home/kjdragan/lrepos/universal_agent/.claude/skills/nano-pdf/SKILL.md`
- **gemini-url-context-scraper**: Fast URL/PDF/image content extraction using Gemini "URL Context" (built-in web/PDF reader) via google-genai.
Use when the user wants to: scrape a URL, read/summarize a PDF, extract structured facts from public web content, or create an interim “scraped context” work product for downstream tasks.
Writes interim outputs to CURRENT_SESSION_WORKSPACE/work_products by default, and can persist outputs under UA_ARTIFACTS_DIR on request. Produces runnable PEP 723 + `uv run` scripts with dotenv auto-loading (no hardcoded secrets).

  - Path: `/home/kjdragan/lrepos/universal_agent/.claude/skills/gemini-url-context-scraper/SKILL.md`

## 🤖 Specialist Agents
Use these agents for specialized tasks. Explicitly delegate to them.
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

- **evaluation-judge**: **Sub-Agent Purpose:** Evaluate task completion by inspecting workspace artifacts.

**WHEN TO USE:**
- URW Orchestrator calls you after a phase/task execution.
- You inspect files and determine if success criteria are met.
- Output: Structured verdict with confidence and reasoning.

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

- **mermaid-expert**: Create Mermaid diagrams for flowcharts, sequences, ERDs, and architectures. Masters syntax for all diagram types and styling. Use PROACTIVELY for visual documentation, system diagrams, or process flows.
- **report-writer**: Multi-phase research report generator.
- **research-specialist**: Sub-agent for a unified research pipeline: Search followed by automated Crawl & Refine.

- **slack-expert**: Expert for Slack workspace interactions.

**WHEN TO DELEGATE:**
- User mentions 'slack', 'channel', '#channel-name'
- User asks to 'post to slack', 'summarize messages', 'what was discussed in'

**THIS SUB-AGENT:**
- Lists channels to find IDs
- Fetches conversation history
- Posts formatted messages

- **task-decomposer**: **Sub-Agent Purpose:** Decompose complex requests into phases for harness execution.

**WHEN TO USE:**
- URW Orchestrator delegates decomposition tasks here.
- You analyze request complexity and create phased plans.
- Output: `macro_tasks.json` with phases, tasks, and success criteria.

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


## 🛠 Tools & Apps
### 🔌 Connected Toolkits
- **GitHub** (`github`): Code hosting and collaboration platform.
  - Categories: Developer Tools & DevOps
- **Google Calendar** (`googlecalendar`)
  - Categories: Scheduling & Booking
- **Google Docs** (`googledocs`)
  - Categories: Productivity & Project Management, Document & File Management
- **Google Drive** (`googledrive`)
  - Categories: Document & File Management
- **Notion** (`notion`)
  - Categories: Productivity & Project Management
- **Slack** (`slack`)
  - Categories: productivity, popular
- **Telegram** (`telegram`)
  - Categories: Communication & Messaging
- **Twitter** (`twitter`)
  - Categories: social, marketing, popular
- **Gmail** (`gmail`): Google's email service.
  - Categories: Collaboration & Communication
- **Google Sheets** (`googlesheets`)
  - Categories: Productivity & Project Management
- **Discord** (`discord`)
  - Categories: gaming, social, popular
- **Reddit** (`reddit`)
  - Categories: Marketing & Social Media, Entertainment & Media
- **Figma** (`figma`)
  - Categories: Design & Creative Tools, Productivity & Project Management

### 🧰 Core Utilities
- **Code Interpreter** (`codeinterpreter`): Executes Python code in a sandboxed environment for calculation, data analysis, and logic.
- **Composio Search** (`composio_search`): Search engine for finding appropriate tools and actions within the Composio ecosystem.
- **Filetool** (`filetool`): Read, write, and manage files in the local workspace.
- **Sqltool** (`sqltool`): Execute SQL queries against connected databases.
- **Browserbase** (`browserbase`): Headless browser for web scraping and interaction.

### 🧩 MCP Servers
- **composio** (http)
- **internal** (sdk)
  - `run_report_generation`
  - `run_research_pipeline`
  - `crawl_parallel`
  - `run_research_phase`
  - `generate_outline`
  - `draft_report_parallel`
  - `cleanup_report`
  - `compile_report`
  - `upload_to_composio`
  - `list_directory`
  - `append_to_file`
  - `write_text_file`
  - `finalize_research`
  - `generate_image`
  - `describe_image`
  - `preview_image`
  - `html_to_pdf`
  - `core_memory_replace`
  - `core_memory_append`
  - `archival_memory_insert`
  - `archival_memory_search`
  - `get_core_memory_blocks`
  - `ask_user_questions`
  - `batch_tool_execute`
  - `ua_memory_get`
  - `ua_memory_search`
- **taskwarrior** (stdio)
- **telegram** (stdio)
- **zai_vision** (stdio)