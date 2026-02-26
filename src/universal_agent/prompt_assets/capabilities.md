# 🧠 Agent Capabilities Registry

Generated: 2026-02-26 14:54:12

## 🤖 Specialist Agents (Micro-Agents)
Delegate full workflows to these specialists based on value-add.

### 🛠 General Tools
- **Freelance-scout**: Internal specialized agent.
  -> Delegate: `Task(subagent_type='Freelance-scout', ...)`
- **action-coordinator**: **Sub-Agent Purpose:** Multi-channel delivery and real-world side effects.

**WHEN TO USE:**
- Task requires delivering work products via email, Slack, Discord, or other channels
- Task requires scheduling calendar events or follow-up reminders
- Task requires multi-channel notification (email + Slack + calendar in one flow)
- Task requires setting up monitoring or recurring actions via Cron

  -> Delegate: `Task(subagent_type='action-coordinator', ...)`
- **banana-squad-expert**: Banana Squad expert. Prompt-first MVP that generates multiple narrative prompt variations,
and can collect a small capped set of style inspiration references.

Use this agent when you want higher-quality infographic prompt variations that can later feed
UA image generation tools.

  -> Delegate: `Task(subagent_type='banana-squad-expert', ...)`
- **bowser-qa-agent**: UI validation agent that executes user stories against web apps and reports pass/fail results with screenshots at every step. Use for QA, acceptance testing, user story validation, or UI verification. Supports parallel instances. Keywords - QA, validation, user story, UI testing, acceptance testing, bowser.
  -> Delegate: `Task(subagent_type='bowser-qa-agent', ...)`
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
- **claude-bowser-agent**: Browser automation agent. Use when you need to browse websites, take screenshots, interact with web pages, or perform browser tasks. Cannot run in parallel — only one instance at a time. Keywords - browse, screenshot, browser, chrome, bowser, ui testing.
  -> Delegate: `Task(subagent_type='claude-bowser-agent', ...)`
- **claude-code-guide**: Use this agent when the user asks questions ("Can Claude...", "Does Claude...", "How do I...?") about:
- Claude Code (CLI features, hooks, slash commands, MCP servers, settings, IDE integrations, keyboard shortcuts)
- Claude Agent SDK (building custom agents)
- Claude API (formerly Anthropic API)

Prefer documentation-based guidance and include references to official Anthropic docs.

  -> Delegate: `Task(subagent_type='claude-code-guide', ...)`
- **code-writer**: Focused code authoring agent for repo changes (features, refactors, bug fixes, tests).

**WHEN TO DELEGATE:**
- Implement a new feature or script inside this repo
- Fix a failing test / bug / runtime error
- Refactor code safely (with tests)
- Add guardrails, tooling, or internal MCP tools

**THIS SUB-AGENT:**
- Reads/writes the local repo
- Runs local commands (prefer `uv run ...`)
- Produces small, reviewable diffs with tests

  -> Delegate: `Task(subagent_type='code-writer', ...)`
- **data-analyst**: **Sub-Agent Purpose:** Statistical analysis, data processing, and visualization.

**WHEN TO USE:**
- Task requires numerical analysis, statistics, or data science
- Research results need quantitative comparison or trend analysis
- Charts, graphs, or data visualizations are needed
- Data needs to be extracted, transformed, or modeled

  -> Delegate: `Task(subagent_type='data-analyst', ...)`
- **evaluation-judge**: **Sub-Agent Purpose:** Evaluate task completion by inspecting workspace artifacts.

**WHEN TO USE:**
- URW Orchestrator calls you after a phase/task execution.
- You inspect files and determine if success criteria are met.
- Output: Structured verdict with confidence and reasoning.

  -> Delegate: `Task(subagent_type='evaluation-judge', ...)`
- **mermaid-expert**: Create Mermaid diagrams for flowcharts, sequences, ERDs, and architectures. Masters syntax for all diagram types and styling. Use PROACTIVELY for visual documentation, system diagrams, or process flows.
  -> Delegate: `Task(subagent_type='mermaid-expert', ...)`
- **playwright-bowser-agent**: Headless browser automation agent using Playwright CLI. Use when you need headless browsing, parallel browser sessions, UI testing, screenshots, or web scraping. Supports parallel instances. Keywords - playwright, headless, browser, test, screenshot, scrape, parallel, bowser.
  -> Delegate: `Task(subagent_type='playwright-bowser-agent', ...)`
- **report-writer**: Multi-phase research report generator with image integration support.
  -> Delegate: `Task(subagent_type='report-writer', ...)`
- **system-configuration-agent**: System configuration and runtime operations specialist for Universal Agent.

Use when:
- The request is about changing platform/runtime settings (not normal user-task execution).
- The request asks to reschedule, enable/disable, pause/resume, or run Chron/Cron jobs.
- The request asks to update heartbeat delivery/interval, ops config, or service-level behavior.
- The request asks for operational diagnostics and controlled remediation.

This sub-agent:
- Interprets natural-language ops requests into structured operations.
- Validates requested change against project/runtime constraints.
- Applies safe changes through existing first-class APIs and config paths.
- Produces auditable before/after summaries.

  -> Delegate: `Task(subagent_type='system-configuration-agent', ...)`
- **youtube-expert**: MANDATORY delegation target for YouTube-focused tasks.

Use when:
- User provides a YouTube URL/video ID and needs transcript + metadata.
- A webhook/manual trigger contains YouTube payloads.
- The task asks for tutorial creation artifacts (concept docs and optional implementation).

This sub-agent:
- Uses `youtube-transcript-metadata` as the core ingestion capability.
- Uses `youtube-tutorial-creation` for durable tutorial artifacts.
- Supports degraded transcript-only completion when visual analysis fails.
- Legacy alias support: `youtube-explainer-expert` remains accepted during migration.

  -> Delegate: `Task(subagent_type='youtube-expert', ...)`
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
- Report-writer needs visual assets for a report

**THIS SUB-AGENT:**
- Generates images using Gemini (default: `gemini-2.5-flash-image`; for infographics prefer `gemini-3-pro-image-preview` with review)
- Edits existing images with natural language instructions
- Creates infographics and visual content for reports
- Writes an image manifest (`work_products/media/manifest.json`) so other agents can consume outputs
- Saves all outputs to `work_products/media/`

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
- **research-specialist**: Sub-agent for multi-mode research with an LLM strategy decision and mode-specific execution policies.

  -> Delegate: `Task(subagent_type='research-specialist', ...)`
- **trend-specialist**: Sub-agent for dynamic discovery and "pulse" checks on current topics (Reddit, X, Trends).

  -> Delegate: `Task(subagent_type='trend-specialist', ...)`
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
- **clean-code**: Applies principles from Robert C. Martin's 'Clean Code'. Use this skill when writing, reviewing, or refactoring code to ensure high quality, readability, and maintainability. Covers naming, functions, comments, error handling, and class design.
- **ideation**: Launch multi-agent ideation to explore a concept through structured dialogue between a Free Thinker and a Grounder, arbitrated by the team lead, and documented by a Writer. Use this when the user wants to explore an idea, brainstorm directions, or develop a concept from a seed into actionable idea briefs. Use "continue" mode to resume and build on a previous session.

- **gmail**: Comprehensive guide for using Gmail tools to send emails, manage drafts, and handle attachments. Use when the user asks to send emails, check inbox, search contacts, or manage labels.
- **nano-banana-pro**: Generate or edit images via Gemini 3 Pro Image (Nano Banana Pro).
- **just**: Use `just` to save and run project-specific commands. Use when the user mentions `justfile`, `recipe`, or needs a simple alternative to `make` for task automation.
- **voice-call**: Start voice calls via the OpenClaw voice-call plugin.
- **playwright-bowser**: Headless browser automation using Playwright CLI. Use when you need headless browsing, parallel browser sessions, UI testing, screenshots, web scraping, or browser automation that can run in the background. Keywords - playwright, headless, browser, test, screenshot, scrape, parallel.
- **tmux**: Remote-control tmux sessions for interactive CLIs by sending keystrokes and scraping pane output.
- **excalidraw-free**: Create Excalidraw diagrams. USE WHEN user specifically asks for Excalidraw. WORKFLOWS - mind-maps, swimlane, process-flow.
- **playwright-cli**: Automates browser interactions for web testing, form filling, screenshots, and data extraction. Use when the user needs to navigate websites, interact with web pages, fill forms, take screenshots, test web applications, or extract information from web pages.
- **skill-judge**: Evaluate Agent Skill design quality against official specifications and best practices. Use when reviewing, auditing, or improving SKILL.md files and skill packages. Provides multi-dimensional scoring and actionable improvement suggestions.
- **coding-agent**: Run Codex CLI, Claude Code, OpenCode, or Pi Coding Agent via background process for programmatic control.
- **bowser-orchestration**: Orchestrate browser-native execution using Bowser's layered stack (skills + subagents + commands) for UI validation, authenticated web operations, and parallel browser workflows.
- **skill-creator**: Guide for creating effective skills. This skill should be used when users want to create a new skill (or update an existing skill) that extends Claude's capabilities with specialized knowledge, workflows, or tool integrations.
- **banana-squad**: Banana Squad: prompt-first "design agency" workflow for high-quality infographic generation.
Use when you want structured, narrative prompt variations (MVP) and, later, generate+critique loops.

- **todoist-orchestration**: Govern Todoist usage so reminders and brainstorm capture use internal Todoist tools first, while complex engineering/research work stays on the normal decomposition and specialist pipeline. Use when requests mention reminders, to-dos, brainstorm capture, backlog progression, heartbeat candidate triage, or proactive follow-up from ideas.
- **slack**: Use when you need to control Slack from Clawdbot via the slack tool, including reacting to messages or pinning/unpinning items in Slack channels or DMs.
- **youtube-transcript-metadata**: Fetch YouTube transcript text and video metadata together in one step (parallel extraction), with optional Webshare residential proxy support and quality/error classification. Use when any agent needs reliable YouTube transcript + metadata retrieval, either as a standalone task or as the ingestion stage for larger YouTube workflows.
- ~~**obsidian**~~ (Unavailable: Missing binary: obsidian-cli)
- **1password**: Set up and use 1Password CLI (op). Use when installing the CLI, enabling desktop app integration, signing in (single or multi-account), or reading/injecting/running secrets via op.
- **image-generation**: AI-powered image generation and editing using Gemini. Use when Claude needs to: (1) Generate images from text descriptions, (2) Edit existing images with instructions, (3) Create infographics or charts, (4) Generate visual assets for reports/presentations, (5) Work with .png, .jpg, .jpeg, .webp files for editing.
- **grok-x-trends**: Get "what's trending" on X (Twitter) for a given query using Grok/xAI's `x_search` tool via the xAI Responses API.
Use when the user asks for trending topics, hot takes, or high-engagement posts on X about a topic, and Composio X/Twitter tooling is unavailable or unreliable.

- **video-remotion**: Comprehensive skill for programmatic video generation using the Remotion framework.
Enables creating, scaffolding, and rendering React-based videos via Python orchestration.
Supports both Local (CLI) and Cloud (Lambda) rendering.

- **logfire-eval**: Evaluate Universal Agent runs by analyzing Logfire traces via the Logfire MCP. Use when the user asks to analyze a run, debug issues, review heartbeat activity, identify bottlenecks, or generate a post-run evaluation report. Triggers on phrases like "analyze the run", "trace analysis", "logfire eval", "what happened in that heartbeat", "check the traces".
- **weather**: Get current weather and forecasts (no API key required).
- **gemini**: Gemini CLI for one-shot Q&A, summaries, and generation.
- **media-processing**: Process multimedia files with FFmpeg (video/audio encoding, conversion, streaming, filtering, hardware acceleration) and ImageMagick (image manipulation, format conversion, batch processing, effects, composition). Use when converting media formats, encoding videos with specific codecs (H.264, H.265, VP9), resizing/cropping images, extracting audio from video, applying filters and effects, optimizing file sizes, creating streaming manifests (HLS/DASH), generating thumbnails, batch processing images, creating composite images, or implementing media processing pipelines. Supports 100+ formats, hardware acceleration (NVENC, QSV), and complex filtergraphs.
- **reddit-intel**: Fetch compact, structured Reddit evidence (top posts / engagement) and save it as an interim work product
inside the current session workspace for downstream agent reuse.

- **stitch-loop**: Teaches agents to iteratively build websites using Stitch with an autonomous baton-passing loop pattern
- **enhance-prompt**: Transforms vague UI ideas into polished, Stitch-optimized prompts. Enhances specificity, adds UI/UX keywords, injects design system context, and structures output for better generation results.
- **react:components**: Converts Stitch designs into modular Vite and React components using system-level networking and AST-based validation.
- **design-md**: Analyze Stitch projects and synthesize a semantic design system into DESIGN.md files
- **shadcn-ui**: Expert guidance for integrating and building applications with shadcn/ui components, including component discovery, installation, customization, and best practices.
- **remotion**: Generate walkthrough videos from Stitch projects using Remotion with smooth transitions, zooming, and text overlays
- **youtube-tutorial-creation**: Turn a YouTube tutorial into durable learning artifacts (concept doc + runnable implementation) stored under UA_ARTIFACTS_DIR.
USE WHEN user provides a YouTube URL and wants to learn/implement from it.

- **pdf**: Comprehensive PDF manipulation toolkit for extracting text and tables, creating new PDFs, merging/splitting documents, and handling forms. When Claude needs to fill in a PDF form or programmatically process, generate, or analyze PDF documents at scale.
- **systematic-debugging**: Use when encountering any bug, test failure, or unexpected behavior, before proposing fixes
- **discord**: Use when you need to control Discord from Clawdbot via the discord tool: send messages, react, post or upload stickers, upload emojis, run polls, manage threads/pins/search, create/edit/delete channels and categories, fetch permissions or member/role/channel info, or handle moderation actions in Discord DMs or channels.
- **goplaces**: Query Google Places API (New) via the goplaces CLI for text search, place details, resolve, and reviews. Use for human-friendly place lookup or JSON output for scripts.
- **google_calendar**: Manage calendar events using Google Calendar via Composio.
- **zread-dependency-docs**: Read documentation and code from open source GitHub repositories using the ZRead MCP server
- **nano-triple**: Generate 3 images with Nano Banana Pro using the same prompt. Pick the best, or give feedback on any option to get 3 refined versions.
- **spotify-player**: Terminal Spotify playback/search via spogo (preferred) or spotify_player.
- **github**: Interact with GitHub using the `gh` CLI. Use `gh issue`, `gh pr`, `gh run`, and `gh api` for issues, PRs, CI runs, and advanced queries.
- **manim_skill**: Create mathematical animations using Manim (Community Edition or ManimGL). Includes best practices, examples, and rules for creating high-quality videos.
- **manimce-best-practices**: Trigger when: (1) User mentions "manim" or "Manim Community" or "ManimCE", (2) Code contains `from manim import *`, (3) User runs `manim` CLI commands, (4) Working with Scene, MathTex, Create(), or ManimCE-specific classes.

Best practices for Manim Community Edition - the community-maintained Python animation engine. Covers Scene structure, animations, LaTeX/MathTex, 3D with ThreeDScene, camera control, styling, and CLI usage.

NOT for ManimGL/3b1b version (which uses `manimlib` imports and `manimgl` CLI).

- **manimgl-best-practices**: Trigger when: (1) User mentions "manimgl" or "ManimGL" or "3b1b manim", (2) Code contains `from manimlib import *`, (3) User runs `manimgl` CLI commands, (4) Working with InteractiveScene, self.frame, self.embed(), ShowCreation(), or ManimGL-specific patterns.

Best practices for ManimGL (Grant Sanderson's 3Blue1Brown version) - OpenGL-based animation engine with interactive development. Covers InteractiveScene, Tex with t2c, camera frame control, interactive mode (-se flag), 3D rendering, and checkpoint_paste() workflow.

NOT for Manim Community Edition (which uses `manim` imports and `manim` CLI).

- **manim-composer**: Trigger when: (1) User wants to create an educational/explainer video, (2) User has a vague concept they want visualized, (3) User mentions "3b1b style" or "explain like 3Blue1Brown", (4) User wants to plan a Manim video or animation sequence, (5) User asks to "compose" or "plan" a math/science visualization.

Transforms vague video ideas into detailed scene-by-scene plans (scenes.md). Conducts research, asks clarifying questions about audience/scope/focus, and outputs comprehensive scene specifications ready for implementation with ManimCE or ManimGL.

Use this BEFORE writing any Manim code. This skill plans the video; use manimce-best-practices or manimgl-best-practices for implementation.

- **gifgrep**: Search GIF providers with CLI/TUI, download results, and extract stills/sheets.
- **trello**: Manage Trello boards, lists, and cards via the Trello REST API.
- ~~**summarize**~~ (Unavailable: Missing binary: summarize)
- **vp-orchestration**: Operate external primary VP agents through tool-first mission control (`vp_*` tools) with deterministic lifecycle handling and artifact handoff.
- **local-places**: Search for places (restaurants, cafes, etc.) via Google Places API proxy on localhost.
- **git-commit**: Stage all changes, create a helpful commit message, and push to remote. Use this when the user wants to quickly commit and push their changes without manually staging files or writing a commit message.
- **dependency-management**: Standardized protocol for managing project dependencies using `uv` and handling system-level requirements.
- **notion**: Notion API for creating and managing pages, databases, and blocks.
- **mcp-builder**: Guide for creating high-quality MCP (Model Context Protocol) servers that enable LLMs to interact with external services through well-designed tools. Use when building MCP servers to integrate external APIs or services, whether in Python (FastMCP) or Node/TypeScript (MCP SDK).
- **agent-browser**: Automates browser interactions for web testing, form filling, screenshots, and data extraction. Use when the user needs to navigate websites, interact with web pages, fill forms, take screenshots, test web applications, or extract information from web pages.
- **telegram**: Send and receive messages via Telegram using a Bot.
- **webapp-testing**: Toolkit for interacting with and testing local web applications using Playwright. Supports verifying frontend functionality, debugging UI behavior, capturing browser screenshots, and viewing browser logs.
- **nano-pdf**: Edit PDFs with natural-language instructions using the nano-pdf CLI.
- **gemini-url-context-scraper**: Fast URL/PDF/image content extraction using Gemini "URL Context" (built-in web/PDF reader) via google-genai.
Use when the user wants to: scrape a URL, read/summarize a PDF, extract structured facts from public web content, or create an interim “scraped context” work product for downstream tasks.
Writes interim outputs to CURRENT_SESSION_WORKSPACE/work_products by default, and can persist outputs under UA_ARTIFACTS_DIR on request. Produces runnable PEP 723 + `uv run` scripts with dotenv auto-loading (no hardcoded secrets).

- **openweather**: Fetch current weather and forecasts for any location using the OpenWeather API.
Use when an agent needs current conditions or a short-term forecast for a city/address/zip or coordinates, and the API key is available in `.env` as OPENWEATHER_API_KEY.

- **claude-bowser**: Observable browser automation using Chrome MCP tools. Use when you need to browse websites, take screenshots, interact with web pages, or perform browser tasks in your current Chrome. Keywords - browse, screenshot, browser, chrome, bowser, ui testing, observable.
- **agentation**: Add Agentation visual feedback toolbar to a Next.js project
- **gemini-api-dev**: Use this skill when building applications with Gemini models, Gemini API, working with multimodal content (text, images, audio, video), implementing function calling, using structured outputs, or needing current model specifications. Covers SDK usage (google-genai for Python, @google/genai for JavaScript/TypeScript, com.google.genai:google-genai for Java, google.golang.org/genai for Go), model selection, and API capabilities.
- **find-skills**: Helps users discover and install agent skills when they ask questions like "how do I do X", "find a skill for X", "is there a skill that can...", or express interest in extending capabilities. This skill should be used when the user is looking for functionality that might exist as an installable skill.
- **agentation-self-driving**: Autonomous design critique mode using the Agentation annotation toolbar. Use when the user asks to "critique this page," "add design annotations," "review the UI," "self-driving mode," "auto-annotate," or wants an AI agent to autonomously add design feedback annotations to a web page via the browser. Requires the Agentation toolbar to be installed on the target page and agent-browser skill to be available.

## 🛠 Toolkits & Capabilities
- Core: Gmail, Calendar, Sheets, Docs, GitHub, Slack, Notion
- Discovery: Run `mcp__composio__get_actions` to find more tools.
