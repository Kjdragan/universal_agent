<!-- Agent Capabilities Registry -->

<!-- Generated: 2026-03-14 12:00:26 -->

# 🧠 Agent Capabilities Registry

### 🧭 Capability Routing Doctrine
- Evaluate multiple capability lanes before selecting an execution path for non-trivial tasks.
- Do not default to research/report unless explicitly requested or clearly required.
- Browser tasks are Bowser-first: `claude-bowser-agent` (identity/session), `playwright-bowser-agent` (parallel/repeatable), `bowser-qa-agent` (UI validation).
- Use `browserbase` when Bowser lanes are unavailable or cloud-browser behavior is explicitly needed.

### 🔍 Decomposing Research Requests
The term 'research' is broad. You must decompose the user's intent and select the appropriate specialist:
- **General Web & News Research**: For finding articles, scraping sites, and building standard knowledge corporas, delegate to `research-specialist`.
- **Audio & Synthesis (NotebookLM)**: If the request involves generating podcasts, audio overviews, slide decks, or deep study guides, delegate to `notebooklm-operator` or use the `notebooklm-orchestration` skill.
- **Video Transcripts (YouTube)**: If the research requires analyzing YouTube content, delegate to `youtube-expert`.
Do not default blindly to one specialist. Chain them if required (e.g., use `research-specialist` to find URLs, then `notebooklm-operator` to synthesize them into a podcast).

### 📄 Report & PDF Workflow (Built-in MCP Tools)
When the user requests reports, PDFs, or email delivery of documents:
- **Research phase**: Use `mcp__internal__run_research_pipeline` or dispatch `Task(subagent_type='research-specialist', ...)` to gather data into task corpus files.
- **Report generation**: Use `mcp__internal__run_report_generation(task_name='<task>')` to delegate to the Report Writer sub-agent which handles outline → draft → cleanup → compile → PDF automatically.
- **HTML → PDF conversion**: Use `mcp__internal__html_to_pdf(html_path='<path>', output_path='<path>.pdf')`. Do NOT use Bash with chrome/wkhtmltopdf/weasyprint — the MCP tool handles fallback automatically.
- **Multiple reports**: Call `run_report_generation` once per topic, or write HTML via Write tool then convert each with `html_to_pdf`.
- **Email delivery**: Simone's own emails → use `agentmail` skill. Kevin's Gmail → use the `gmail` skill (gws CLI) with absolute file path as attachment.

### 🏭 External VP Control Plane
- For user requests that explicitly mention General/Coder VP delegation, route directly through internal `vp_*` tools.
- Primary lifecycle: `vp_dispatch_mission` -> `vp_wait_mission` -> `vp_get_mission`.
- Use `vp_read_result_artifacts` to summarize VP outputs from workspace URIs.
- Never wrap `vp_*` tools inside Composio multi-execute.

## 🤖 Specialist Agents (Micro-Agents)
Delegate full workflows to these specialists based on value-add.


### ⚙️ Engineering & Code
- **code-writer**: Focused code authoring agent for repo changes (features, refactors, bug fixes, tests). **WHEN TO DELEGATE:** - Implement a new feature or script inside this repo - Fix a failing test / bug / runtime error - Refactor code safely (with tests) - Add guardrails, tooling, or internal MCP tools **THIS SUB-AGENT:** - Reads/writes the local repo - Runs local commands (prefer `uv run ...`) - Produces small, reviewable diffs with tests
  -> Delegate: `Task(subagent_type='code-writer', ...)`
- **task-decomposer**: **Sub-Agent Purpose:** Decompose complex requests into phases for harness execution. **WHEN TO USE:** - URW Orchestrator delegates decomposition tasks here. - You analyze request complexity and create phased plans. - Output: `macro_tasks.json` with phases, tasks, and success criteria.
  -> Delegate: `Task(subagent_type='task-decomposer', ...)`

### 🌐 Browser Operations
- **bowser-qa-agent**: UI validation agent that executes user stories against web apps and reports pass/fail results with screenshots at every step. Use for QA, acceptance testing, user story validation, or UI verification. Supports parallel instances. Keywords - QA, validation, user story, UI testing, acceptance testing, bowser.
  -> Delegate: `Task(subagent_type='bowser-qa-agent', ...)`
- **browserbase**: Expert for browser automation using Browserbase cloud infrastructure. **WHEN TO DELEGATE:** - User asks to scrape website content - User wants to take screenshots of web pages - User needs to fill forms or interact with web pages - User asks to test website functionality - User mentions "automate browser", "headless chrome", "web automation" - User wants to navigate and extract data from dynamic web pages **THIS SUB-AGENT:** - Creates isolated browser sessions in the cloud - Navigates pages and interacts with DOM elements - Captures full-page or viewport screenshots - Extracts rendered HTML/text from JavaScript-heavy pages - Handles multi-step browser workflows autonomously - Saves artifacts to work_products/browser/
  -> Delegate: `Task(subagent_type='browserbase', ...)`
- **claude-bowser-agent**: Browser automation agent. Use when you need to browse websites, take screenshots, interact with web pages, or perform browser tasks. Cannot run in parallel — only one instance at a time. Keywords - browse, screenshot, browser, chrome, bowser, ui testing.
  -> Delegate: `Task(subagent_type='claude-bowser-agent', ...)`
- **playwright-bowser-agent**: Headless browser automation agent using Playwright CLI. Use when you need headless browsing, parallel browser sessions, UI testing, screenshots, or web scraping. Supports parallel instances. Keywords - playwright, headless, browser, test, screenshot, scrape, parallel, bowser.
  -> Delegate: `Task(subagent_type='playwright-bowser-agent', ...)`

### 🎨 Creative & Media
- **image-expert**: MANDATORY DELEGATION TARGET for ALL image generation and editing tasks. **WHEN TO DELEGATE:** - User asks to generate, create, or edit images - User mentions "picture", "graphic", "visual", "infographic" - User requests .png, .jpg, .jpeg, .webp file creation/editing - User wants iterative refinement of images through conversation - Report-writer needs visual assets for a report **THIS SUB-AGENT:** - Generates images using Gemini (default: `gemini-2.5-flash-image`; for infographics prefer `gemini-3-pro-image-preview` with review) - Edits existing images with natural language instructions - Creates infographics and visual content for reports - Writes an image manifest (`work_products/media/manifest.json`) so other agents can consume outputs - Saves all outputs to `work_products/media/`
  -> Delegate: `Task(subagent_type='image-expert', ...)`
- **video-creation-expert**: 🎬 MANDATORY DELEGATION TARGET for ALL video and audio tasks. **WHEN TO DELEGATE (MUST BE USED):** - User asks to download, process, or edit video/audio - User mentions YouTube, video, audio, MP3, MP4, trimming, cutting - User asks to create video content, transitions, effects - User asks to extract audio from video - User asks about video format conversion - User asks to combine, concatenate, or merge videos **THIS SUB-AGENT:** - Downloads YouTube videos/audio via yt-dlp (mcp-youtube) - Processes video/audio with FFmpeg (video-audio MCP) - Applies effects, transitions, overlays, text - Saves final outputs to work_products/media/ Main agent should pass video paths and desired operations in task description.
  -> Delegate: `Task(subagent_type='video-creation-expert', ...)`
- **video-remotion-expert**: 🎥 SPECIALIZED AGENT for programmatic video generation using Remotion. **WHEN TO DELEGATE:** - User asks to generate videos using code/React/Remotion - User wants to create data-driven videos (dynamic text, images, products) - User needs to render compositions locally or via AWS Lambda - User asks to specific Remotion tasks (scaffold, render, deploy) **THIS SUB-AGENT:** - Scaffolds Remotion projects with Zod schemas - Generates JSON props for video compositions - Renders videos using local CLI (subprocess) or Lambda - Manages "Hybrid Architecture" (Lambda primary, CLI fallback) **CAPABILITIES:** - Create React-based video compositions - Programmatic rendering with dynamic props - Cloud rendering (AWS Lambda) setup and execution
  -> Delegate: `Task(subagent_type='video-remotion-expert', ...)`
- **youtube-expert**: MANDATORY delegation target for YouTube-focused tasks. Use when: - User provides a YouTube URL/video ID and needs transcript + metadata. - A webhook/manual trigger contains YouTube payloads. - The task asks for tutorial creation artifacts (concept docs and optional implementation). This sub-agent: - Uses `youtube-transcript-metadata` as the **mandatory** ingestion primitive for ALL modes (never fetch transcripts inline). - Uses `youtube-tutorial-creation` for durable tutorial artifacts. - Supports degraded transcript-only completion when visual analysis fails. - For software/coding tutorials: creates an `implementation/` folder with a repo scaffold script and install script.
  -> Delegate: `Task(subagent_type='youtube-expert', ...)`

### 🏢 Operations & Communication
- **slack-expert**: Expert for Slack workspace interactions. **WHEN TO DELEGATE:** - User mentions 'slack', 'channel', '#channel-name' - User asks to 'post to slack', 'summarize messages', 'what was discussed in' **THIS SUB-AGENT:** - Lists channels to find IDs - Fetches conversation history - Posts formatted messages
  -> Delegate: `Task(subagent_type='slack-expert', ...)`
- **system-configuration-agent**: System configuration and runtime operations specialist for Universal Agent. Use when: - The request is about changing platform/runtime settings (not normal user-task execution). - The request asks to reschedule, enable/disable, pause/resume, or run Chron/Cron jobs. - The request asks to update heartbeat delivery/interval, ops config, or service-level behavior. - The request asks for operational diagnostics and controlled remediation. This sub-agent: - Interprets natural-language ops requests into structured operations. - Validates requested change against project/runtime constraints. - Applies safe changes through existing first-class APIs and config paths. - Produces auditable before/after summaries.
  -> Delegate: `Task(subagent_type='system-configuration-agent', ...)`

### 🔬 Research & Analysis
- **csi-trend-analyst**: CSI-first trend analyst that reviews CSI reports/bundles/loop state, scores mission relevance, and recommends focused follow-up actions.
  -> Delegate: `Task(subagent_type='csi-trend-analyst', ...)`
- **notebooklm-operator**: Dedicated NotebookLM execution sub-agent for UA. Use when: - A task requires NotebookLM operations through MCP tools or `nlm` CLI. - The request mentions NotebookLM notebooks, sources, research, chat queries, studio generation, artifact downloads, notes, sharing, or exports. - A hybrid MCP-first with CLI-fallback execution path is required. This sub-agent: - Performs NotebookLM auth preflight using Infisical-injected seed material. - Prefers NotebookLM MCP tools when available. - Falls back to `nlm` CLI when MCP is unavailable or unsuitable. - Enforces confirmation gates for destructive/share operations.
  -> Delegate: `Task(subagent_type='notebooklm-operator', ...)`
- **professor**: Academic oversight and skill creation.
  -> Delegate: `Task(subagent_type='professor', ...)`
- **research-specialist**: Sub-agent for multi-mode research with an LLM strategy decision and mode-specific execution policies.
  -> Delegate: `Task(subagent_type='research-specialist', ...)`
- **scribe**: Memory logging and fact recording.
  -> Delegate: `Task(subagent_type='scribe', ...)`
- **trend-specialist**: Sub-agent for dynamic discovery and "pulse" checks on current topics (Reddit, X, Trends).
  -> Delegate: `Task(subagent_type='trend-specialist', ...)`

### 🛠 General Tools
- **action-coordinator**: **Sub-Agent Purpose:** Multi-channel delivery and real-world side effects. **WHEN TO USE:** - Task requires delivering work products via email, Slack, Discord, or other channels - Task requires scheduling calendar events or follow-up reminders - Task requires multi-channel notification (email + Slack + calendar in one flow) - Task requires setting up monitoring or recurring actions via Cron
  -> Delegate: `Task(subagent_type='action-coordinator', ...)`
- **banana-squad-expert**: Banana Squad expert. Prompt-first MVP that generates multiple narrative prompt variations, and can collect a small capped set of style inspiration references. Use this agent when you want higher-quality infographic prompt variations that can later feed UA image generation tools.
  -> Delegate: `Task(subagent_type='banana-squad-expert', ...)`
- **claude-code-guide**: Use this agent when the user asks questions ("Can Claude...", "Does Claude...", "How do I...?") about: - Claude Code (CLI features, hooks, slash commands, MCP servers, settings, IDE integrations, keyboard shortcuts) - Claude Agent SDK (building custom agents) - Claude API (formerly Anthropic API) Prefer documentation-based guidance and include references to official Anthropic docs.
  -> Delegate: `Task(subagent_type='claude-code-guide', ...)`
- **config**: Internal specialized agent.
  -> Delegate: `Task(subagent_type='config', ...)`
- **critic**: Internal specialized agent.
  -> Delegate: `Task(subagent_type='critic', ...)`
- **csi-supervisor**: CSI monitoring supervisor for HQ operational visibility. Use when: - The user asks what CSI is doing, what signals are actionable, and why CSI queue volume is high. - The user wants delivery/SLO/source-health status in plain language. - The user wants a concise explanation of CSI flow into Task Hub. This sub-agent: - Produces CSI health and flow snapshots. - Explains signal volume vs actionable conversion. - Recommends suppression/tuning steps without mutating runtime state by default.
  -> Delegate: `Task(subagent_type='csi-supervisor', ...)`
- **data-analyst**: **Sub-Agent Purpose:** Statistical analysis, data processing, and visualization. **WHEN TO USE:** - Task requires numerical analysis, statistics, or data science - Research results need quantitative comparison or trend analysis - Charts, graphs, or data visualizations are needed - Data needs to be extracted, transformed, or modeled
  -> Delegate: `Task(subagent_type='data-analyst', ...)`
- **email-handler**: Handles inbound emails received in Simone's AgentMail inbox. Classifies intent, drafts replies, and delegates actionable requests to appropriate specialists.
  -> Delegate: `Task(subagent_type='email-handler', ...)`
- **evaluation-judge**: **Sub-Agent Purpose:** Evaluate task completion by inspecting workspace artifacts. **WHEN TO USE:** - URW Orchestrator calls you after a phase/task execution. - You inspect files and determine if success criteria are met. - Output: Structured verdict with confidence and reasoning.
  -> Delegate: `Task(subagent_type='evaluation-judge', ...)`
- **factory-supervisor**: Fleet-level operational supervisor for HQ visibility. Use when: - The user asks what factories are doing, what ran recently, or why queue pressure is growing. - The user wants a plain-language summary of headquarters vs local-worker posture. - The user asks for heartbeat cadence, delegation flow, or Task Hub pressure diagnostics. This sub-agent: - Produces a concise factory status brief with KPI and flow diagnostics. - Explains CSI-to-task routing pressure in operational terms. - Recommends tuning actions without mutating runtime state by default.
  -> Delegate: `Task(subagent_type='factory-supervisor', ...)`
- **Freelance-scout**: Internal specialized agent.
  -> Delegate: `Task(subagent_type='Freelance-scout', ...)`
- **integration**: Internal specialized agent.
  -> Delegate: `Task(subagent_type='integration', ...)`
- **logfire_reader**: Internal specialized agent.
  -> Delegate: `Task(subagent_type='logfire_reader', ...)`
- **mermaid-expert**: Create Mermaid diagrams for flowcharts, sequences, ERDs, and architectures. Masters syntax for all diagram types and styling. Use PROACTIVELY for visual documentation, system diagrams, or process flows.
  -> Delegate: `Task(subagent_type='mermaid-expert', ...)`
- **report-writer**: Multi-phase research report generator with image integration support.
  -> Delegate: `Task(subagent_type='report-writer', ...)`
- **runner**: Internal specialized agent.
  -> Delegate: `Task(subagent_type='runner', ...)`

### 🛠 Mandatory System Operations Routing
- **system-configuration-agent**: Platform/runtime operations specialist for Chron scheduling, heartbeat, and ops config.
  -> Delegate immediately for schedule and runtime parameter changes:
  `Task(subagent_type='system-configuration-agent', prompt='Apply this system change safely and verify it.')`
- Do not use OS-level crontab for product scheduling requests; use Chron APIs and runtime config paths.

## 📚 Standard Operating Procedures (Skills)
These organized guides are available to **ALL** agents and sub-agents. You should prioritize using these instead of improvising.
They represent the collective knowledge of the system. **Think about your capabilities** and how these guides can help you.

**Progressive Disclosure**:
1. **Scan**: Read the YAML frontmatter below to identifying relevant skills.
2. **Read**: If a skill seems useful, use `view_file` to read the full Markdown content (SOP).
3. **Execute**: Follow the procedure step-by-step.



### agent-browser
Automates browser interactions for web testing, form filling, screenshots, and data extraction. Use when the user needs to navigate websites, interact with web pages, fill forms, take screenshots, test web applications, or extract information from web pages.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/agent-browser/SKILL.md`
```yaml
name: agent-browser
description: Automates browser interactions for web testing, form filling, screenshots,
  and data extraction. Use when the user needs to navigate websites, interact with
  web pages, fill forms, take screenshots, test web applications, or extract information
  from web pages.
allowed-tools: Bash(agent-browser:*)
```

### agentmail
Simone's native email inbox via AgentMail. Use when Simone needs to send emails, deliver reports/artifacts to Kevin or external recipients, read inbound emails, reply to threads, or manage drafts. This is Simone's OWN email — not Gmail. Simone sends FROM her custom domain address directly.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/agentmail/SKILL.md`
```yaml
name: agentmail
description: "Simone's native email inbox via AgentMail. Use when Simone needs to\
  \ send emails, deliver reports/artifacts to Kevin or external recipients, read inbound\
  \ emails, reply to threads, or manage drafts. This is Simone's OWN email \u2014\
  \ not Gmail. Simone sends FROM her custom domain address directly."
```

### banana-squad
Banana Squad: prompt-first "design agency" workflow for high-quality infographic generation. Use when you want structured, narrative prompt variations (MVP) and, later, generate+critique loops.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/banana-squad/SKILL.md`
```yaml
name: banana-squad
description: 'Banana Squad: prompt-first "design agency" workflow for high-quality
  infographic generation.

  Use when you want structured, narrative prompt variations (MVP) and, later, generate+critique
  loops.

  '
metadata:
  clawdbot:
    requires:
      bins:
      - uv
```

### bowser-orchestration
Orchestrate browser-native execution using Bowser's layered stack (skills + subagents + commands) for UI validation, authenticated web operations, and parallel browser workflows.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/bowser-orchestration/SKILL.md`
```yaml
name: bowser-orchestration
description: Orchestrate browser-native execution using Bowser's layered stack (skills
  + subagents + commands) for UI validation, authenticated web operations, and parallel
  browser workflows.
commands:
- /ui-review
- /bowser:hop-automate
- /bowser:amazon-add-to-cart
- /bowser:blog-summarizer
agents:
- bowser-qa-agent
- claude-bowser-agent
- playwright-bowser-agent
skills:
- claude-bowser
- playwright-bowser
- just
```

### claude-bowser
Observable browser automation using Chrome MCP tools. Use when you need to browse websites, take screenshots, interact with web pages, or perform browser tasks in your current Chrome. Keywords - browse, screenshot, browser, chrome, bowser, ui testing, observable.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/claude-bowser/SKILL.md`
```yaml
name: claude-bowser
description: Observable browser automation using Chrome MCP tools. Use when you need
  to browse websites, take screenshots, interact with web pages, or perform browser
  tasks in your current Chrome. Keywords - browse, screenshot, browser, chrome, bowser,
  ui testing, observable.
```

### clean-code
Applies principles from Robert C. Martin's 'Clean Code'. Use this skill when writing, reviewing, or refactoring code to ensure high quality, readability, and maintainability. Covers naming, functions, comments, error handling, and class design.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/clean-code/SKILL.md`
```yaml
name: clean-code
description: Applies principles from Robert C. Martin's 'Clean Code'. Use this skill
  when writing, reviewing, or refactoring code to ensure high quality, readability,
  and maintainability. Covers naming, functions, comments, error handling, and class
  design.
user-invocable: true
risk: safe
source: ClawForge (https://github.com/jackjin1997/ClawForge)
```

### coding-agent
Run Codex CLI, Claude Code, OpenCode, or Pi Coding Agent via background process for programmatic control.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/coding-agent/SKILL.md`
```yaml
name: coding-agent
description: Run Codex CLI, Claude Code, OpenCode, or Pi Coding Agent via background
  process for programmatic control.
metadata:
  clawdbot:
    emoji: "\U0001F9E9"
    requires:
      anyBins:
      - claude
      - codex
      - opencode
      - pi
```

### dependency-management
Standardized protocol for managing project dependencies using `uv` and handling system-level requirements.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/dependency-management/SKILL.md`
```yaml
name: dependency-management
description: Standardized protocol for managing project dependencies using `uv` and
  handling system-level requirements.
```

### design-md
Analyze Stitch projects and synthesize a semantic design system into DESIGN.md files
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/stitch-skills/design-md/SKILL.md`
```yaml
name: design-md
description: Analyze Stitch projects and synthesize a semantic design system into
  DESIGN.md files
allowed-tools:
- stitch*:*
- Read
- Write
- web_fetch
```

### discord
Use when you need to control Discord from Clawdbot via the discord tool: send messages, react, post or upload stickers, upload emojis, run polls, manage threads/pins/search, create/edit/delete channels and categories, fetch permissions or member/role/channel info, or handle moderation actions in Discord DMs or channels.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/discord/SKILL.md`
```yaml
name: discord
description: 'Use when you need to control Discord from Clawdbot via the discord tool:
  send messages, react, post or upload stickers, upload emojis, run polls, manage
  threads/pins/search, create/edit/delete channels and categories, fetch permissions
  or member/role/channel info, or handle moderation actions in Discord DMs or channels.'
```

### enhance-prompt
Transforms vague UI ideas into polished, Stitch-optimized prompts. Enhances specificity, adds UI/UX keywords, injects design system context, and structures output for better generation results.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/stitch-skills/enhance-prompt/SKILL.md`
```yaml
name: enhance-prompt
description: Transforms vague UI ideas into polished, Stitch-optimized prompts. Enhances
  specificity, adds UI/UX keywords, injects design system context, and structures
  output for better generation results.
allowed-tools:
- Read
- Write
```

### excalidraw-diagram
Create Excalidraw diagram JSON files that make visual arguments. Use when the user wants to visualize workflows, architectures, or concepts.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/excalidraw-diagram/SKILL.md`
```yaml
name: excalidraw-diagram
description: Create Excalidraw diagram JSON files that make visual arguments. Use
  when the user wants to visualize workflows, architectures, or concepts.
```

### excalidraw-free
Create Excalidraw diagrams. USE WHEN user specifically asks for Excalidraw. WORKFLOWS - mind-maps, swimlane, process-flow.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/graph-draw/SKILL.md`
```yaml
name: excalidraw-free
description: Create Excalidraw diagrams. USE WHEN user specifically asks for Excalidraw.
  WORKFLOWS - mind-maps, swimlane, process-flow.
```

### gemini
Gemini CLI for one-shot Q&A, summaries, and generation.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/gemini/SKILL.md`
```yaml
name: gemini
description: Gemini CLI for one-shot Q&A, summaries, and generation.
homepage: https://ai.google.dev/
metadata:
  claude-007:
    emoji: "\u264A\uFE0F"
    requires:
      bins:
      - gemini
    install:
    - id: npm
      kind: npm
      package: '@google/gemini-cli'
      bins:
      - gemini
      label: Install Gemini CLI (npm)
```

### gemini-url-context-scraper
Fast URL/PDF/image content extraction using Gemini "URL Context" (built-in web/PDF reader) via google-genai. Use when the user wants to: scrape a URL, read/summarize a PDF, extract structured facts from public web content, or create an interim “scraped context” work product for downstream tasks. Writes interim outputs to CURRENT_SESSION_WORKSPACE/work_products by default, and can persist outputs under UA_ARTIFACTS_DIR on request. Produces runnable PEP 723 + `uv run` scripts with dotenv auto-loading (no hardcoded secrets).
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/gemini-url-context-scraper/SKILL.md`
```yaml
name: gemini-url-context-scraper
description: "Fast URL/PDF/image content extraction using Gemini \"URL Context\" (built-in\
  \ web/PDF reader) via google-genai.\nUse when the user wants to: scrape a URL, read/summarize\
  \ a PDF, extract structured facts from public web content, or create an interim\
  \ \u201Cscraped context\u201D work product for downstream tasks.\nWrites interim\
  \ outputs to CURRENT_SESSION_WORKSPACE/work_products by default, and can persist\
  \ outputs under UA_ARTIFACTS_DIR on request. Produces runnable PEP 723 + `uv run`\
  \ scripts with dotenv auto-loading (no hardcoded secrets).\n"
```

### gifgrep
Search GIF providers with CLI/TUI, download results, and extract stills/sheets.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/gifgrep/SKILL.md`
```yaml
name: gifgrep
description: Search GIF providers with CLI/TUI, download results, and extract stills/sheets.
homepage: https://gifgrep.com
metadata:
  openclaw:
    emoji: "\U0001F9F2"
    requires:
      bins:
      - gifgrep
    install:
    - id: brew
      kind: brew
      formula: steipete/tap/gifgrep
      bins:
      - gifgrep
      label: Install gifgrep (brew)
    - id: go
      kind: go
      module: github.com/steipete/gifgrep/cmd/gifgrep@latest
      bins:
      - gifgrep
      label: Install gifgrep (go)
```

### git-commit
Stage all changes, create a helpful commit message, and push to remote. Use this when the user wants to quickly commit and push their changes without manually staging files or writing a commit message.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/git-commit/SKILL.md`
```yaml
name: git-commit
description: Stage all changes, create a helpful commit message, and push to remote.
  Use this when the user wants to quickly commit and push their changes without manually
  staging files or writing a commit message.
```

### github
Interact with GitHub using the `gh` CLI. Use `gh issue`, `gh pr`, `gh run`, and `gh api` for issues, PRs, CI runs, and advanced queries.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/github/SKILL.md`
```yaml
name: github
description: Interact with GitHub using the `gh` CLI. Use `gh issue`, `gh pr`, `gh
  run`, and `gh api` for issues, PRs, CI runs, and advanced queries.
```

### gmail
Guide for using Gmail via gws MCP tools to send emails, manage drafts, and handle attachments. Use when the user asks to send emails, check inbox, or manage labels.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/gmail/SKILL.md`
```yaml
name: gmail
description: Guide for using Gmail via gws MCP tools to send emails, manage drafts,
  and handle attachments. Use when the user asks to send emails, check inbox, or manage
  labels.
```

### google_calendar
Manage calendar events using Google Calendar via gws MCP tools. Use when the user asks to check schedule, create events, or manage calendar.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/google_calendar/SKILL.md`
```yaml
name: google_calendar
description: Manage calendar events using Google Calendar via gws MCP tools. Use when
  the user asks to check schedule, create events, or manage calendar.
metadata:
  requires:
  - gws
```

### goplaces
Query Google Places API (New) via the goplaces CLI for text search, place details, resolve, and reviews. Use for human-friendly place lookup or JSON output for scripts.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/goplaces/SKILL.md`
```yaml
name: goplaces
description: Query Google Places API (New) via the goplaces CLI for text search, place
  details, resolve, and reviews. Use for human-friendly place lookup or JSON output
  for scripts.
homepage: https://github.com/steipete/goplaces
metadata:
  openclaw:
    emoji: "\U0001F4CD"
    requires:
      bins:
      - goplaces
      env:
      - GOOGLE_PLACES_API_KEY
    primaryEnv: GOOGLE_PLACES_API_KEY
    install:
    - id: brew
      kind: brew
      formula: steipete/tap/goplaces
      bins:
      - goplaces
      label: Install goplaces (brew)
```

### grok-x-trends
Get "what's trending" on X (Twitter) for a given query using Grok/xAI's `x_search` tool via the xAI Responses API. Use when the user asks for trending topics, hot takes, or high-engagement posts on X about a topic, and Composio X/Twitter tooling is unavailable or unreliable.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/grok-x-trends/SKILL.md`
```yaml
name: grok-x-trends
description: 'Get "what''s trending" on X (Twitter) for a given query using Grok/xAI''s
  `x_search` tool via the xAI Responses API.

  Use when the user asks for trending topics, hot takes, or high-engagement posts
  on X about a topic, and Composio X/Twitter tooling is unavailable or unreliable.

  '
```

### ideation
Launch multi-agent ideation to explore a concept through structured dialogue between a Free Thinker and a Grounder, arbitrated by a team lead (you), and documented by a Writer. USE THIS SKILL when the user wants to: - Explore an idea or brainstorm directions for a project, product, or problem - Develop a concept from a vague seed into concrete, actionable idea briefs - Get a multi-perspective creative exploration of a topic - Resume and build on a previous ideation session TRIGGER PHRASES: "ideate on this", "let's brainstorm", "explore the idea of", "develop this concept", "I want to think through", "help me think about X", "brainstorm directions for", "explore what this could be". Use "continue" mode to resume and build on a previous session.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/ideation/SKILL.md`
```yaml
name: ideation
description: 'Launch multi-agent ideation to explore a concept through structured
  dialogue between a Free Thinker and a Grounder, arbitrated by a team lead (you),
  and documented by a Writer. USE THIS SKILL when the user wants to: - Explore an
  idea or brainstorm directions for a project, product, or problem - Develop a concept
  from a vague seed into concrete, actionable idea briefs - Get a multi-perspective
  creative exploration of a topic - Resume and build on a previous ideation session
  TRIGGER PHRASES: "ideate on this", "let''s brainstorm", "explore the idea of", "develop
  this concept", "I want to think through", "help me think about X", "brainstorm directions
  for", "explore what this could be". Use "continue" mode to resume and build on a
  previous session.

  '
argument-hint: concept seed (file path or inline description). Use 'continue <path>'
  to resume a previous session.
user-invocable: true
```

### image-generation
AI-powered image generation and editing using Gemini. Use when Claude needs to: (1) Generate images from text descriptions, (2) Edit existing images with instructions, (3) Create infographics or charts, (4) Generate visual assets for reports/presentations, (5) Work with .png, .jpg, .jpeg, .webp files for editing.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/image-generation/SKILL.md`
```yaml
name: image-generation
description: 'AI-powered image generation and editing using Gemini. Use when Claude
  needs to: (1) Generate images from text descriptions, (2) Edit existing images with
  instructions, (3) Create infographics or charts, (4) Generate visual assets for
  reports/presentations, (5) Work with .png, .jpg, .jpeg, .webp files for editing.'
```

### just
Use `just` to save and run project-specific commands. Use when the user mentions `justfile`, `recipe`, or needs a simple alternative to `make` for task automation.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/just/SKILL.md`
```yaml
name: just
description: Use `just` to save and run project-specific commands. Use when the user
  mentions `justfile`, `recipe`, or needs a simple alternative to `make` for task
  automation.
```

### local-places
Search for nearby places (restaurants, cafes, gyms, etc.) via a local Google Places API proxy server running on localhost. Returns structured results with ratings, addresses, open status, price levels, and place details. USE when the user asks about nearby places, wants to find restaurants or businesses, asks "what's open near me", "find a coffee shop", "restaurants nearby", "best rated places in [area]", "where can I get [food type]", or any local discovery task. Requires the local server to be running first.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/local-places/SKILL.md`
```yaml
name: local-places
description: 'Search for nearby places (restaurants, cafes, gyms, etc.) via a local
  Google Places API proxy server running on localhost. Returns structured results
  with ratings, addresses, open status, price levels, and place details. USE when
  the user asks about nearby places, wants to find restaurants or businesses, asks
  "what''s open near me", "find a coffee shop", "restaurants nearby", "best rated
  places in [area]", "where can I get [food type]", or any local discovery task. Requires
  the local server to be running first.

  '
homepage: https://github.com/Hyaxia/local_places
metadata:
  openclaw:
    emoji: "\U0001F4CD"
    requires:
      bins:
      - uv
      env:
      - GOOGLE_PLACES_API_KEY
    primaryEnv: GOOGLE_PLACES_API_KEY
```

### logfire-eval
Evaluate Universal Agent runs by analyzing Logfire traces via the Logfire MCP. Use when the user asks to analyze a run, debug issues, review heartbeat activity, identify bottlenecks, or generate a post-run evaluation report. Triggers on phrases like "analyze the run", "trace analysis", "logfire eval", "what happened in that heartbeat", "check the traces".
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/logfire-eval/SKILL.md`
```yaml
name: logfire-eval
description: Evaluate Universal Agent runs by analyzing Logfire traces via the Logfire
  MCP. Use when the user asks to analyze a run, debug issues, review heartbeat activity,
  identify bottlenecks, or generate a post-run evaluation report. Triggers on phrases
  like "analyze the run", "trace analysis", "logfire eval", "what happened in that
  heartbeat", "check the traces".
```

### manim-composer
Trigger when: (1) User wants to create an educational/explainer video, (2) User has a vague concept they want visualized, (3) User mentions "3b1b style" or "explain like 3Blue1Brown", (4) User wants to plan a Manim video or animation sequence, (5) User asks to "compose" or "plan" a math/science visualization. Transforms vague video ideas into detailed scene-by-scene plans (scenes.md). Conducts research, asks clarifying questions about audience/scope/focus, and outputs comprehensive scene specifications ready for implementation with ManimCE or ManimGL. Use this BEFORE writing any Manim code. This skill plans the video; use manimce-best-practices or manimgl-best-practices for implementation.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/manim_skill/manim-composer/SKILL.md`
```yaml
name: manim-composer
description: 'Trigger when: (1) User wants to create an educational/explainer video,
  (2) User has a vague concept they want visualized, (3) User mentions "3b1b style"
  or "explain like 3Blue1Brown", (4) User wants to plan a Manim video or animation
  sequence, (5) User asks to "compose" or "plan" a math/science visualization.


  Transforms vague video ideas into detailed scene-by-scene plans (scenes.md). Conducts
  research, asks clarifying questions about audience/scope/focus, and outputs comprehensive
  scene specifications ready for implementation with ManimCE or ManimGL.


  Use this BEFORE writing any Manim code. This skill plans the video; use manimce-best-practices
  or manimgl-best-practices for implementation.

  '
```

### manim_skill
Create mathematical animations using Manim (Community Edition or ManimGL). Includes best practices, examples, and rules for creating high-quality videos.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/manim_skill/SKILL.md`
```yaml
name: manim_skill
description: Create mathematical animations using Manim (Community Edition or ManimGL).
  Includes best practices, examples, and rules for creating high-quality videos.
```

### manimce-best-practices
Trigger when: (1) User mentions "manim" or "Manim Community" or "ManimCE", (2) Code contains `from manim import *`, (3) User runs `manim` CLI commands, (4) Working with Scene, MathTex, Create(), or ManimCE-specific classes. Best practices for Manim Community Edition - the community-maintained Python animation engine. Covers Scene structure, animations, LaTeX/MathTex, 3D with ThreeDScene, camera control, styling, and CLI usage. NOT for ManimGL/3b1b version (which uses `manimlib` imports and `manimgl` CLI).
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/manim_skill/manimce-best-practices/SKILL.md`
```yaml
name: manimce-best-practices
description: 'Trigger when: (1) User mentions "manim" or "Manim Community" or "ManimCE",
  (2) Code contains `from manim import *`, (3) User runs `manim` CLI commands, (4)
  Working with Scene, MathTex, Create(), or ManimCE-specific classes.


  Best practices for Manim Community Edition - the community-maintained Python animation
  engine. Covers Scene structure, animations, LaTeX/MathTex, 3D with ThreeDScene,
  camera control, styling, and CLI usage.


  NOT for ManimGL/3b1b version (which uses `manimlib` imports and `manimgl` CLI).

  '
```

### manimgl-best-practices
Trigger when: (1) User mentions "manimgl" or "ManimGL" or "3b1b manim", (2) Code contains `from manimlib import *`, (3) User runs `manimgl` CLI commands, (4) Working with InteractiveScene, self.frame, self.embed(), ShowCreation(), or ManimGL-specific patterns. Best practices for ManimGL (Grant Sanderson's 3Blue1Brown version) - OpenGL-based animation engine with interactive development. Covers InteractiveScene, Tex with t2c, camera frame control, interactive mode (-se flag), 3D rendering, and checkpoint_paste() workflow. NOT for Manim Community Edition (which uses `manim` imports and `manim` CLI).
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/manim_skill/manimgl-best-practices/SKILL.md`
```yaml
name: manimgl-best-practices
description: 'Trigger when: (1) User mentions "manimgl" or "ManimGL" or "3b1b manim",
  (2) Code contains `from manimlib import *`, (3) User runs `manimgl` CLI commands,
  (4) Working with InteractiveScene, self.frame, self.embed(), ShowCreation(), or
  ManimGL-specific patterns.


  Best practices for ManimGL (Grant Sanderson''s 3Blue1Brown version) - OpenGL-based
  animation engine with interactive development. Covers InteractiveScene, Tex with
  t2c, camera frame control, interactive mode (-se flag), 3D rendering, and checkpoint_paste()
  workflow.


  NOT for Manim Community Edition (which uses `manim` imports and `manim` CLI).

  '
```

### mcp-builder
Guide for creating high-quality MCP (Model Context Protocol) servers that enable LLMs to interact with external services through well-designed tools. Use when building MCP servers to integrate external APIs or services, whether in Python (FastMCP) or Node/TypeScript (MCP SDK).
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/mcp-builder/SKILL.md`
```yaml
name: mcp-builder
description: Guide for creating high-quality MCP (Model Context Protocol) servers
  that enable LLMs to interact with external services through well-designed tools.
  Use when building MCP servers to integrate external APIs or services, whether in
  Python (FastMCP) or Node/TypeScript (MCP SDK).
license: Complete terms in LICENSE.txt
```

### media-processing
Process multimedia files with FFmpeg (video/audio encoding, conversion, streaming, filtering, hardware acceleration) and ImageMagick (image manipulation, format conversion, batch processing, effects, composition). Use when converting media formats, encoding videos with specific codecs (H.264, H.265, VP9), resizing/cropping images, extracting audio from video, applying filters and effects, optimizing file sizes, creating streaming manifests (HLS/DASH), generating thumbnails, batch processing images, creating composite images, or implementing media processing pipelines. Supports 100+ formats, hardware acceleration (NVENC, QSV), and complex filtergraphs.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/media-processing/SKILL.md`
```yaml
name: media-processing
description: Process multimedia files with FFmpeg (video/audio encoding, conversion,
  streaming, filtering, hardware acceleration) and ImageMagick (image manipulation,
  format conversion, batch processing, effects, composition). Use when converting
  media formats, encoding videos with specific codecs (H.264, H.265, VP9), resizing/cropping
  images, extracting audio from video, applying filters and effects, optimizing file
  sizes, creating streaming manifests (HLS/DASH), generating thumbnails, batch processing
  images, creating composite images, or implementing media processing pipelines. Supports
  100+ formats, hardware acceleration (NVENC, QSV), and complex filtergraphs.
license: MIT
```

### modular-research-report-expert
Generate a publication-quality research report from a research corpus using an Agent Team with progressive deepening, draft-critique-revise loops, and integrated visual design. Orchestrate specialized teammates (Narrative Architect, Deep Reader, Storyteller, Visual Director, Diagram Craftsman, Editorial Judge) through a multi-phase pipeline that extracts maximum value from both refined and original source materials. Use when: (1) a refined_corpus.md or research corpus exists, (2) user asks to "build a report" from research, (3) user wants a professional, visually-integrated HTML report exported to PDF. Adapts structure, tone, and component usage to the material — works across any topic domain. Requires Agent Teams: CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/modular-research-report-expert/SKILL.md`
```yaml
name: modular-research-report-expert
description: "Generate a publication-quality research report from a research corpus\
  \ using an Agent Team with progressive deepening, draft-critique-revise loops, and\
  \ integrated visual design. Orchestrate specialized teammates (Narrative Architect,\
  \ Deep Reader, Storyteller, Visual Director, Diagram Craftsman, Editorial Judge)\
  \ through a multi-phase pipeline that extracts maximum value from both refined and\
  \ original source materials. Use when: (1) a refined_corpus.md or research corpus\
  \ exists, (2) user asks to \"build a report\" from research, (3) user wants a professional,\
  \ visually-integrated HTML report exported to PDF. Adapts structure, tone, and component\
  \ usage to the material \u2014 works across any topic domain. Requires Agent Teams:\
  \ CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1.\n"
argument-hint: corpus path, topic description, or task name
user-invocable: true
```

### nano-banana-pro
Generate or edit images via Gemini 3 Pro Image (Nano Banana Pro).
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/nano-banana-pro/SKILL.md`
```yaml
name: nano-banana-pro
description: Generate or edit images via Gemini 3 Pro Image (Nano Banana Pro).
homepage: https://ai.google.dev/
metadata:
  openclaw:
    emoji: "\U0001F34C"
    requires:
      bins:
      - uv
      env:
      - GEMINI_API_KEY
    primaryEnv: GEMINI_API_KEY
    install:
    - id: uv-brew
      kind: brew
      formula: uv
      bins:
      - uv
      label: Install uv (brew)
```

### nano-pdf
Edit PDFs with natural-language instructions using the nano-pdf CLI.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/nano-pdf/SKILL.md`
```yaml
name: nano-pdf
description: Edit PDFs with natural-language instructions using the nano-pdf CLI.
homepage: https://pypi.org/project/nano-pdf/
metadata:
  openclaw:
    emoji: "\U0001F4C4"
    requires:
      bins:
      - nano-pdf
    install:
    - id: uv
      kind: uv
      package: nano-pdf
      bins:
      - nano-pdf
      label: Install nano-pdf (uv)
```

### nano-triple
Generate 3 images with Nano Banana Pro using the same prompt. Pick the best, or give feedback on any option to get 3 refined versions.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/nano-triple/skills.md`
```yaml
name: nano-triple
description: Generate 3 images with Nano Banana Pro using the same prompt. Pick the
  best, or give feedback on any option to get 3 refined versions.
triggers:
- make me an image
- generate an image
- create an image
metadata:
  clawdbot:
    emoji: "\U0001F3A8"
```

### notebooklm-orchestration
Orchestrate NotebookLM operations for UA with a hybrid MCP-first and CLI-fallback execution model, backed by Infisical-injected auth seed and VPS-safe guardrails. Use whenever the user mentions NotebookLM/notebooklm/nlm, notebooks, NotebookLM source ingestion, NotebookLM research, podcast/audio overview generation, report/quiz creation, flashcards, slide decks, infographics, downloads, sharing, or NotebookLM automation workflows. Route execution to `notebooklm-operator` by default.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/notebooklm-orchestration/SKILL.md`
```yaml
name: notebooklm-orchestration
description: 'Orchestrate NotebookLM operations for UA with a hybrid MCP-first and
  CLI-fallback execution model, backed by Infisical-injected auth seed and VPS-safe
  guardrails. Use whenever the user mentions NotebookLM/notebooklm/nlm, notebooks,
  NotebookLM source ingestion, NotebookLM research, podcast/audio overview generation,
  report/quiz creation, flashcards, slide decks, infographics, downloads, sharing,
  or NotebookLM automation workflows. Route execution to `notebooklm-operator` by
  default.

  '
```

### notion
Notion API for creating and managing pages, databases, and blocks.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/notion/SKILL.md`
```yaml
name: notion
description: Notion API for creating and managing pages, databases, and blocks.
homepage: https://developers.notion.com
metadata:
  clawdbot:
    emoji: "\U0001F4DD"
```

### ~~obsidian~~ (Unavailable)
> **Reason**: Missing binary: obsidian-cli
### openweather
Fetch current weather and forecasts for any location using the OpenWeather API. Use when an agent needs current conditions or a short-term forecast for a city/address/zip or coordinates, and the API key is available in `.env` as OPENWEATHER_API_KEY.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/openweather/SKILL.md`
```yaml
name: openweather
description: 'Fetch current weather and forecasts for any location using the OpenWeather
  API.

  Use when an agent needs current conditions or a short-term forecast for a city/address/zip
  or coordinates, and the API key is available in `.env` as OPENWEATHER_API_KEY.

  '
metadata:
  clawdbot:
    requires:
      bins:
      - python3
      - uv
```

### pdf
Comprehensive PDF manipulation toolkit for extracting text and tables, creating new PDFs, merging/splitting documents, and handling forms. When Claude needs to fill in a PDF form or programmatically process, generate, or analyze PDF documents at scale.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/pdf/SKILL.md`
```yaml
name: pdf
description: Comprehensive PDF manipulation toolkit for extracting text and tables,
  creating new PDFs, merging/splitting documents, and handling forms. When Claude
  needs to fill in a PDF form or programmatically process, generate, or analyze PDF
  documents at scale.
license: Proprietary. LICENSE.txt has complete terms
```

### playwright-bowser
Headless browser automation using Playwright CLI. Use when you need headless browsing, parallel browser sessions, UI testing, screenshots, web scraping, or browser automation that can run in the background. Keywords - playwright, headless, browser, test, screenshot, scrape, parallel.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/playwright-bowser/SKILL.md`
```yaml
name: playwright-bowser
description: Headless browser automation using Playwright CLI. Use when you need headless
  browsing, parallel browser sessions, UI testing, screenshots, web scraping, or browser
  automation that can run in the background. Keywords - playwright, headless, browser,
  test, screenshot, scrape, parallel.
allowed-tools: Bash
```

### playwright-cli
Automates browser interactions for web testing, form filling, screenshots, and data extraction. Use when the user needs to navigate websites, interact with web pages, fill forms, take screenshots, test web applications, or extract information from web pages.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/playwright-cli/SKILL.md`
```yaml
name: playwright-cli
description: Automates browser interactions for web testing, form filling, screenshots,
  and data extraction. Use when the user needs to navigate websites, interact with
  web pages, fill forms, take screenshots, test web applications, or extract information
  from web pages.
allowed-tools: Bash(playwright-cli:*)
```

### react:components
Converts Stitch designs into modular Vite and React components using system-level networking and AST-based validation.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/stitch-skills/react-components/SKILL.md`
```yaml
name: react:components
description: Converts Stitch designs into modular Vite and React components using
  system-level networking and AST-based validation.
allowed-tools:
- stitch*:*
- Bash
- Read
- Write
- web_fetch
```

### reddit-intel
Fetch compact, structured Reddit intelligence — top posts with engagement metrics (score, comments, author, permalink) — and save as an interim work product in the current session workspace for downstream agent reuse. USE when you need to understand what the Reddit community is saying about a topic, check trending discussion in a subreddit, or gather social signals for a research task. Trigger phrases: "what's trending on Reddit", "check Reddit for", "top Reddit posts about", "Reddit sentiment on", "what's r/X saying about", "check r/MachineLearning", "get Reddit intel on", "pull Reddit data for", "what's popular in this subreddit".
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/reddit-intel/SKILL.md`
```yaml
name: reddit-intel
description: "Fetch compact, structured Reddit intelligence \u2014 top posts with\
  \ engagement metrics (score, comments, author, permalink) \u2014 and save as an\
  \ interim work product in the current session workspace for downstream agent reuse.\
  \ USE when you need to understand what the Reddit community is saying about a topic,\
  \ check trending discussion in a subreddit, or gather social signals for a research\
  \ task. Trigger phrases: \"what's trending on Reddit\", \"check Reddit for\", \"\
  top Reddit posts about\", \"Reddit sentiment on\", \"what's r/X saying about\",\
  \ \"check r/MachineLearning\", \"get Reddit intel on\", \"pull Reddit data for\"\
  , \"what's popular in this subreddit\".\n"
allowed-tools: Bash
```

### remotion
Generate walkthrough videos from Stitch projects using Remotion with smooth transitions, zooming, and text overlays
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/stitch-skills/remotion/SKILL.md`
```yaml
name: remotion
description: Generate walkthrough videos from Stitch projects using Remotion with
  smooth transitions, zooming, and text overlays
allowed-tools:
- stitch*:*
- remotion*:*
- Bash
- Read
- Write
- web_fetch
```

### shadcn-ui
Expert guidance for integrating and building applications with shadcn/ui components, including component discovery, installation, customization, and best practices.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/stitch-skills/shadcn-ui/SKILL.md`
```yaml
name: shadcn-ui
description: Expert guidance for integrating and building applications with shadcn/ui
  components, including component discovery, installation, customization, and best
  practices.
allowed-tools:
- shadcn*:*
- mcp_shadcn*
- Read
- Write
- Bash
- web_fetch
```

### skill-creator
Create new skills, modify and improve existing skills, and measure skill performance. Use when users want to create a skill from scratch, update or optimize an existing skill, run evals to test a skill, benchmark skill performance with variance analysis, or optimize a skill's description for better triggering accuracy.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/skill-creator/SKILL.md`
```yaml
name: skill-creator
description: Create new skills, modify and improve existing skills, and measure skill
  performance. Use when users want to create a skill from scratch, update or optimize
  an existing skill, run evals to test a skill, benchmark skill performance with variance
  analysis, or optimize a skill's description for better triggering accuracy.
```

### skill-judge
Evaluate Agent Skill design quality against official specifications and best practices. Use when reviewing, auditing, or improving SKILL.md files and skill packages. Provides multi-dimensional scoring and actionable improvement suggestions.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/skill-judge/SKILL.md`
```yaml
name: skill-judge
description: Evaluate Agent Skill design quality against official specifications and
  best practices. Use when reviewing, auditing, or improving SKILL.md files and skill
  packages. Provides multi-dimensional scoring and actionable improvement suggestions.
```

### slack
Use when you need to control Slack from Clawdbot via the slack tool, including reacting to messages or pinning/unpinning items in Slack channels or DMs.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/slack/SKILL.md`
```yaml
name: slack
description: Use when you need to control Slack from Clawdbot via the slack tool,
  including reacting to messages or pinning/unpinning items in Slack channels or DMs.
```

### stitch-loop
Teaches agents to iteratively build websites using Stitch with an autonomous baton-passing loop pattern
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/stitch-skills/stitch-loop/SKILL.md`
```yaml
name: stitch-loop
description: Teaches agents to iteratively build websites using Stitch with an autonomous
  baton-passing loop pattern
allowed-tools:
- stitch*:*
- chrome*:*
- Read
- Write
- Bash
```

### ~~summarize~~ (Unavailable)
> **Reason**: Missing binary: summarize
### systematic-debugging
Use when encountering any bug, test failure, or unexpected behavior, before proposing fixes
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/systematic-debugging/SKILL.md`
```yaml
name: systematic-debugging
description: Use when encountering any bug, test failure, or unexpected behavior,
  before proposing fixes
```

### telegram
Send and receive messages via Telegram using a Bot.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/telegram/SKILL.md`
```yaml
name: telegram
description: Send and receive messages via Telegram using a Bot.
metadata:
  requires:
  - telegram
```

### tmux
Remote-control tmux sessions for interactive CLIs by sending keystrokes and scraping pane output.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/tmux/SKILL.md`
```yaml
name: tmux
description: Remote-control tmux sessions for interactive CLIs by sending keystrokes
  and scraping pane output.
metadata:
  clawdbot:
    emoji: "\U0001F9F5"
    os:
    - darwin
    - linux
    requires:
      bins:
      - tmux
```

### todoist-orchestration
Govern Todoist usage so reminders and brainstorm capture use internal Todoist tools first, while complex engineering/research work stays on the normal decomposition and specialist pipeline. Use when requests mention reminders, to-dos, brainstorm capture, backlog progression, heartbeat candidate triage, or proactive follow-up from ideas.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/todoist-orchestration/SKILL.md`
```yaml
name: todoist-orchestration
description: Govern Todoist usage so reminders and brainstorm capture use internal
  Todoist tools first, while complex engineering/research work stays on the normal
  decomposition and specialist pipeline. Use when requests mention reminders, to-dos,
  brainstorm capture, backlog progression, heartbeat candidate triage, or proactive
  follow-up from ideas.
```

### todoist-rich-handoff
Create high-context Todoist pause/resume reminders with explicit restart docs, due time, personal-only labels, and verification that tasks are excluded from heartbeat auto-work. Use when users ask to "remind me to resume this tomorrow", "capture handoff in Todoist", or "pause now and pick up later".
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/todoist-rich-handoff/SKILL.md`
```yaml
name: todoist-rich-handoff
description: Create high-context Todoist pause/resume reminders with explicit restart
  docs, due time, personal-only labels, and verification that tasks are excluded from
  heartbeat auto-work. Use when users ask to "remind me to resume this tomorrow",
  "capture handoff in Todoist", or "pause now and pick up later".
```

### trello
Manage Trello boards, lists, and cards via the Trello REST API.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/trello/SKILL.md`
```yaml
name: trello
description: Manage Trello boards, lists, and cards via the Trello REST API.
homepage: https://developer.atlassian.com/cloud/trello/rest/
metadata:
  clawdbot:
    emoji: "\U0001F4CB"
    requires:
      bins:
      - jq
      env:
      - TRELLO_API_KEY
      - TRELLO_TOKEN
```

### video-remotion
Comprehensive skill for programmatic video generation using the Remotion framework. Enables creating, scaffolding, and rendering React-based videos via Python orchestration. Supports both Local (CLI) and Cloud (Lambda) rendering.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/video-remotion/SKILL.md`
```yaml
name: video-remotion
description: 'Comprehensive skill for programmatic video generation using the Remotion
  framework.

  Enables creating, scaffolding, and rendering React-based videos via Python orchestration.

  Supports both Local (CLI) and Cloud (Lambda) rendering.

  '
```

### visual-explainer
Generate beautiful, self-contained HTML pages that visually explain systems, code changes, plans, and data. Use when the user asks for a diagram, architecture overview, diff review, plan review, project recap, comparison table, or any visual explanation of technical concepts. Also use proactively when you are about to render a complex ASCII table (4+ rows or 3+ columns) — present it as a styled HTML page instead.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/visual-explainer/SKILL.md`
```yaml
name: visual-explainer
description: "Generate beautiful, self-contained HTML pages that visually explain\
  \ systems, code changes, plans, and data. Use when the user asks for a diagram,\
  \ architecture overview, diff review, plan review, project recap, comparison table,\
  \ or any visual explanation of technical concepts. Also use proactively when you\
  \ are about to render a complex ASCII table (4+ rows or 3+ columns) \u2014 present\
  \ it as a styled HTML page instead."
license: MIT
compatibility: Requires a browser to view generated HTML files. Optional surf-cli
  for AI image generation.
metadata:
  author: nicobailon
  version: 0.2.0
```

### voice-call
Start voice calls via the OpenClaw voice-call plugin.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/voice-call/SKILL.md`
```yaml
name: voice-call
description: Start voice calls via the OpenClaw voice-call plugin.
metadata:
  openclaw:
    emoji: "\U0001F4DE"
    skillKey: voice-call
    requires:
      config:
      - plugins.entries.voice-call.enabled
```

### vp-orchestration
Operate external primary VP agents through tool-first mission control (`vp_*` tools) with deterministic lifecycle handling and artifact handoff. USE when work should be delegated to an external VP runtime — such as when the user says "send this to the VP", "have the coder VP do this", "run this as a VP mission", "delegate to general VP", "kick off a VP task", or "have the external agent handle this". VP runtimes available: `vp.general.primary` (research/content/analysis) and `vp.coder.primary` (code/build/refactor in external project paths).
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/vp-orchestration/SKILL.md`
```yaml
name: vp-orchestration
description: "Operate external primary VP agents through tool-first mission control\
  \ (`vp_*` tools) with deterministic lifecycle handling and artifact handoff. USE\
  \ when work should be delegated to an external VP runtime \u2014 such as when the\
  \ user says \"send this to the VP\", \"have the coder VP do this\", \"run this as\
  \ a VP mission\", \"delegate to general VP\", \"kick off a VP task\", or \"have\
  \ the external agent handle this\". VP runtimes available: `vp.general.primary`\
  \ (research/content/analysis) and `vp.coder.primary` (code/build/refactor in external\
  \ project paths).\n"
user-invocable: true
risk: medium
```

### weather
Get current weather and forecasts (no API key required).
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/weather/SKILL.md`
```yaml
name: weather
description: Get current weather and forecasts (no API key required).
homepage: https://wttr.in/:help
metadata:
  clawdbot:
    emoji: "\U0001F324\uFE0F"
    requires:
      bins:
      - curl
```

### webapp-testing
Toolkit for interacting with and testing local web applications using Playwright. Supports verifying frontend functionality, debugging UI behavior, capturing browser screenshots, and viewing browser logs.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/webapp-testing/SKILL.md`
```yaml
name: webapp-testing
description: Toolkit for interacting with and testing local web applications using
  Playwright. Supports verifying frontend functionality, debugging UI behavior, capturing
  browser screenshots, and viewing browser logs.
license: Complete terms in LICENSE.txt
```

### youtube-transcript-metadata
Fetch a YouTube video's full transcript text and rich metadata together in one parallel step. Use this skill whenever the user provides a YouTube URL or video ID and needs any of: transcript text, video title, channel name, upload date, view/like counts, description, or duration. Also use it for ingestion into larger workflows (tutorial creation, analysis, summarization) — it's the recommended first step for any YouTube content pipeline. Trigger on phrases like "get me the transcript", "grab the YouTube video", "fetch the content of this video", "what does this video say", "extract text from YouTube", "get the captions", or any time a YouTube URL appears in context and content access is needed.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/youtube-transcript-metadata/SKILL.md`
```yaml
name: youtube-transcript-metadata
description: "Fetch a YouTube video's full transcript text and rich metadata together\
  \ in one parallel step. Use this skill whenever the user provides a YouTube URL\
  \ or video ID and needs any of: transcript text, video title, channel name, upload\
  \ date, view/like counts, description, or duration. Also use it for ingestion into\
  \ larger workflows (tutorial creation, analysis, summarization) \u2014 it's the\
  \ recommended first step for any YouTube content pipeline. Trigger on phrases like\
  \ \"get me the transcript\", \"grab the YouTube video\", \"fetch the content of\
  \ this video\", \"what does this video say\", \"extract text from YouTube\", \"\
  get the captions\", or any time a YouTube URL appears in context and content access\
  \ is needed.\n"
```

### youtube-tutorial-creation
Convert a YouTube tutorial video into durable, referenceable learning artifacts stored under UA_ARTIFACTS_DIR. Always produces CONCEPT.md + manifest.json, and conditionally produces runnable implementation artifacts when the content is truly software/coding. USE when a user provides a YouTube URL and wants to learn, understand, implement from, or deeply study a video. Also trigger when a webhook/hook payload contains a YouTube URL with a learning/tutorial intent. Trigger phrases: "create a tutorial from this video", "help me learn from this YouTube link", "implement what's shown in this video", "turn this YouTube video into a guide", "make me study notes from this", "explain and implement this YouTube tutorial", "I want to implement this", "break down this video for me".
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/youtube-tutorial-creation/SKILL.md`
```yaml
name: youtube-tutorial-creation
description: 'Convert a YouTube tutorial video into durable, referenceable learning
  artifacts stored under UA_ARTIFACTS_DIR. Always produces CONCEPT.md + manifest.json,
  and conditionally produces runnable implementation artifacts when the content is
  truly software/coding. USE when a user provides a YouTube URL and wants to learn,
  understand, implement from, or deeply study a video. Also trigger when a webhook/hook
  payload contains a YouTube URL with a learning/tutorial intent. Trigger phrases:
  "create a tutorial from this video", "help me learn from this YouTube link", "implement
  what''s shown in this video", "turn this YouTube video into a guide", "make me study
  notes from this", "explain and implement this YouTube tutorial", "I want to implement
  this", "break down this video for me".

  '
```

### zread-dependency-docs
Read documentation and code from open source GitHub repositories using the ZRead MCP server
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/zread-dependency-docs/SKILL.md`
```yaml
name: zread-dependency-docs
description: Read documentation and code from open source GitHub repositories using
  the ZRead MCP server
```


## 🛠 Toolkits & Capabilities
- Core: Gmail, Calendar, Sheets, Docs, GitHub, Slack, Notion
- Discovery: Run `mcp__composio__get_actions` to find more tools.