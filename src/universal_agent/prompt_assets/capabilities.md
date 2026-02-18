<!-- Agent Capabilities Registry -->

<!-- Generated: 2026-02-13 20:34:00 -->

### 🤖 Specialist Agents (Micro-Agents)
Delegate full workflows to these specialists based on value-add.

#### 🛠 General Tools
- **Freelance-scout**: Internal specialized agent.
  -> Delegate: `Task(subagent_type='Freelance-scout', ...)`
- **action-coordinator**: **Sub-Agent Purpose:** Multi-channel delivery and real-world side effects.

**WHEN TO USE:**
- Task requires delivering work products via email, Slack, Discord, or other channels
- Task requires scheduling calendar events or follow-up reminders
- Task requires multi-channel notification (email + Slack + calendar in one flow)
- Task requires setting up monitoring or recurring actions via Cron

  -> Delegate: `Task(subagent_type='action-coordinator', ...)`
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
- **report-writer**: Multi-phase research report generator with image integration support.
  -> Delegate: `Task(subagent_type='report-writer', ...)`
- **youtube-explainer-expert**: MANDATORY delegation target for YouTube tutorial learning runs, including webhook-triggered playlist events.

Use when:
- User provides a YouTube URL and wants a tutorial/summary.
- A webhook event contains a YouTube video URL/video ID.
- The task asks for tutorial docs and optional implementation code.

This sub-agent:
- Applies the `youtube-tutorial-learning` skill workflow.
- Produces durable learning artifacts (`CONCEPT.md`, `IMPLEMENTATION.md`, `implementation/`, `manifest.json`).
- Includes runnable implementation code when `learning_mode=concept_plus_implementation`.
- Supports degraded transcript-only completion when video/vision fails.

  -> Delegate: `Task(subagent_type='youtube-explainer-expert', ...)`
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

#### ⚙️ Engineering & Code
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
- **task-decomposer**: **Sub-Agent Purpose:** Decompose complex requests into phases for harness execution.

**WHEN TO USE:**
- URW Orchestrator delegates decomposition tasks here.
- You analyze request complexity and create phased plans.
- Output: `macro_tasks.json` with phases, tasks, and success criteria.

  -> Delegate: `Task(subagent_type='task-decomposer', ...)`

#### 🎨 Creative & Media
- **banana-squad-expert**: Prompt-first "design agency" workflow for high-quality infographic generation (Banana Squad).
  -> Delegate: `Task(subagent_type='banana-squad-expert', ...)`
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

#### 🔬 Research & Analysis
- **research-specialist**: Sub-agent for a unified research pipeline: Search followed by automated Crawl & Refine.

  -> Delegate: `Task(subagent_type='research-specialist', ...)`
- **professor**: Academic oversight and skill creation.
  -> Delegate: `Task(subagent_type='professor', ...)`
- **scribe**: Memory logging and fact recording.
  -> Delegate: `Task(subagent_type='scribe', ...)`

#### 🏢 Operations & Communication
- **slack-expert**: Expert for Slack workspace interactions.

**WHEN TO DELEGATE:**
- User mentions 'slack', 'channel', '#channel-name'
- User asks to 'post to slack', 'summarize messages', 'what was discussed in'

**THIS SUB-AGENT:**
- Lists channels to find IDs
- Fetches conversation history
- Posts formatted messages

  -> Delegate: `Task(subagent_type='slack-expert', ...)`
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

#### 🛠 Mandatory System Operations Routing
- **system-configuration-agent**: Platform/runtime operations specialist for Chron scheduling, heartbeat, and ops config.
  -> Delegate immediately for schedule and runtime parameter changes:
  `Task(subagent_type='system-configuration-agent', prompt='Apply this system change safely and verify it.')`
- Do not use OS-level crontab for product scheduling requests; use Chron APIs and runtime config paths.

### 📚 Standard Operating Procedures (Skills)
These organized guides are available to **ALL** agents and sub-agents. You should prioritize using these instead of improvising.
They represent the collective knowledge of the system. **Think about your capabilities** and how these guides can help you.

**Progressive Disclosure**:
1. **Scan**: Read the YAML frontmatter below to identifying relevant skills.
2. **Read**: If a skill seems useful, use `mcp__internal__read_file` to read the full Markdown content (SOP).
3. **Execute**: Follow the procedure step-by-step.

#### 1password
Set up and use 1Password CLI (op). Use when installing the CLI, enabling desktop app integration, signing in (single or multi-account), or reading/injecting/running secrets via op.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/1password/SKILL.md`
```yaml
name: 1password
description: Set up and use 1Password CLI (op). Use when installing the CLI, enabling
  desktop app integration, signing in (single or multi-account), or reading/injecting/running
  secrets via op.
homepage: https://developer.1password.com/docs/cli/get-started/
metadata:
  clawdbot:
    emoji: "\U0001F510"
    requires:
      bins:
      - op
    install:
    - id: brew
      kind: brew
      formula: 1password-cli
      bins:
      - op
      label: Install 1Password CLI (brew)
```

#### agent-browser
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

#### clawhub
Use the ClawHub CLI to search, install, update, and publish agent skills from clawhub.com. Use when you need to fetch new skills on the fly, sync installed skills to latest or a specific version, or publish new/updated skill folders with the npm-installed clawhub CLI.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/clawhub/SKILL.md`
```yaml
name: clawhub
description: Use the ClawHub CLI to search, install, update, and publish agent skills
  from clawhub.com. Use when you need to fetch new skills on the fly, sync installed
  skills to latest or a specific version, or publish new/updated skill folders with
  the npm-installed clawhub CLI.
metadata:
  openclaw:
    requires:
      bins:
      - clawhub
    install:
    - id: node
      kind: node
      package: clawhub
      bins:
      - clawhub
      label: Install ClawHub CLI (npm)
```

#### coding-agent
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

#### dependency-management
Standardized protocol for managing project dependencies using `uv` and handling system-level requirements.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/dependency-management/SKILL.md`
```yaml
name: dependency-management
description: Standardized protocol for managing project dependencies using `uv` and
  handling system-level requirements.
```

#### design-md
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

#### discord
Use when you need to control Discord from Clawdbot via the discord tool: send messages, react, post or upload stickers, upload emojis, run polls, manage threads/pins/search, create/edit/delete channels and categories, fetch permissions or member/role/channel info, or handle moderation actions in Discord DMs or channels.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/discord/SKILL.md`
```yaml
name: discord
description: 'Use when you need to control Discord from Clawdbot via the discord tool:
  send messages, react, post or upload stickers, upload emojis, run polls, manage
  threads/pins/search, create/edit/delete channels and categories, fetch permissions
  or member/role/channel info, or handle moderation actions in Discord DMs or channels.'
```

#### enhance-prompt
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

#### excalidraw-free
Create Excalidraw diagrams. USE WHEN user specifically asks for Excalidraw. WORKFLOWS - mind-maps, swimlane, process-flow.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/graph-draw/SKILL.md`
```yaml
name: excalidraw-free
description: Create Excalidraw diagrams. USE WHEN user specifically asks for Excalidraw.
  WORKFLOWS - mind-maps, swimlane, process-flow.
```

#### gemini
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

#### gemini-url-context-scraper
Fast URL/PDF/image content extraction using Gemini "URL Context" (built-in web/PDF reader) via google-genai.
Use when the user wants to: scrape a URL, read/summarize a PDF, extract structured facts from public web content, or create an interim “scraped context” work product for downstream tasks.
Writes interim outputs to CURRENT_SESSION_WORKSPACE/work_products by default, and can persist outputs under UA_ARTIFACTS_DIR on request. Produces runnable PEP 723 + `uv run` scripts with dotenv auto-loading (no hardcoded secrets).

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

#### gifgrep
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

#### git-commit
Stage all changes, create a helpful commit message, and push to remote. Use this when the user wants to quickly commit and push their changes without manually staging files or writing a commit message.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/git-commit/SKILL.md`
```yaml
name: git-commit
description: Stage all changes, create a helpful commit message, and push to remote.
  Use this when the user wants to quickly commit and push their changes without manually
  staging files or writing a commit message.
```

#### github
Interact with GitHub using the `gh` CLI. Use `gh issue`, `gh pr`, `gh run`, and `gh api` for issues, PRs, CI runs, and advanced queries.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/github/SKILL.md`
```yaml
name: github
description: Interact with GitHub using the `gh` CLI. Use `gh issue`, `gh pr`, `gh
  run`, and `gh api` for issues, PRs, CI runs, and advanced queries.
```

#### gmail
Comprehensive guide for using Gmail tools to send emails, manage drafts, and handle attachments. Use when the user asks to send emails, check inbox, search contacts, or manage labels.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/gmail/SKILL.md`
```yaml
name: gmail
description: Comprehensive guide for using Gmail tools to send emails, manage drafts,
  and handle attachments. Use when the user asks to send emails, check inbox, search
  contacts, or manage labels.
```

#### google_calendar
Manage calendar events using Google Calendar via Composio.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/google_calendar/SKILL.md`
```yaml
name: google_calendar
description: Manage calendar events using Google Calendar via Composio.
metadata:
  requires:
  - composio
```

#### goplaces
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

#### grok-x-trends
Get "what's trending" on X (Twitter) for a given query using Grok/xAI's `x_search` tool via the xAI Responses API.
Use when the user asks for trending topics, hot takes, or high-engagement posts on X about a topic, and Composio X/Twitter tooling is unavailable or unreliable.

Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/grok-x-trends/SKILL.md`
```yaml
name: grok-x-trends
description: 'Get "what''s trending" on X (Twitter) for a given query using Grok/xAI''s
  `x_search` tool via the xAI Responses API.

  Use when the user asks for trending topics, hot takes, or high-engagement posts
  on X about a topic, and Composio X/Twitter tooling is unavailable or unreliable.

  '
```

#### image-generation
AI-powered image generation and editing using Gemini. Use when Claude needs to: (1) Generate images from text descriptions, (2) Edit existing images with instructions, (3) Create infographics or charts, (4) Generate visual assets for reports/presentations, (5) Work with .png, .jpg, .jpeg, .webp files for editing.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/image-generation/SKILL.md`
```yaml
name: image-generation
description: 'AI-powered image generation and editing using Gemini. Use when Claude
  needs to: (1) Generate images from text descriptions, (2) Edit existing images with
  instructions, (3) Create infographics or charts, (4) Generate visual assets for
  reports/presentations, (5) Work with .png, .jpg, .jpeg, .webp files for editing.'
```

#### last30days
Research a topic from the last 30 days on Reddit + X + Web, become an expert, and write copy-paste-ready prompts for the user's target tool.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/last30days/SKILL.md`
```yaml
name: last30days
description: Research a topic from the last 30 days on Reddit + X + Web, become an
  expert, and write copy-paste-ready prompts for the user's target tool.
argument-hint: nano banana pro prompts, NVIDIA news, best AI video tools
allowed-tools: Bash, Read, Write, AskUserQuestion, WebSearch
```

#### local-places
Search for places (restaurants, cafes, etc.) via Google Places API proxy on localhost.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/local-places/SKILL.md`
```yaml
name: local-places
description: Search for places (restaurants, cafes, etc.) via Google Places API proxy
  on localhost.
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

#### logfire-eval
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

#### manim-composer
Trigger when: (1) User wants to create an educational/explainer video, (2) User has a vague concept they want visualized, (3) User mentions "3b1b style" or "explain like 3Blue1Brown", (4) User wants to plan a Manim video or animation sequence, (5) User asks to "compose" or "plan" a math/science visualization.

Transforms vague video ideas into detailed scene-by-scene plans (scenes.md). Conducts research, asks clarifying questions about audience/scope/focus, and outputs comprehensive scene specifications ready for implementation with ManimCE or ManimGL.

Use this BEFORE writing any Manim code. This skill plans the video; use manimce-best-practices or manimgl-best-practices for implementation.

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

#### manim_skill
Create mathematical animations using Manim (Community Edition or ManimGL). Includes best practices, examples, and rules for creating high-quality videos.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/manim_skill/SKILL.md`
```yaml
name: manim_skill
description: Create mathematical animations using Manim (Community Edition or ManimGL).
  Includes best practices, examples, and rules for creating high-quality videos.
```

#### manimce-best-practices
Trigger when: (1) User mentions "manim" or "Manim Community" or "ManimCE", (2) Code contains `from manim import *`, (3) User runs `manim` CLI commands, (4) Working with Scene, MathTex, Create(), or ManimCE-specific classes.

Best practices for Manim Community Edition - the community-maintained Python animation engine. Covers Scene structure, animations, LaTeX/MathTex, 3D with ThreeDScene, camera control, styling, and CLI usage.

NOT for ManimGL/3b1b version (which uses `manimlib` imports and `manimgl` CLI).

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

#### manimgl-best-practices
Trigger when: (1) User mentions "manimgl" or "ManimGL" or "3b1b manim", (2) Code contains `from manimlib import *`, (3) User runs `manimgl` CLI commands, (4) Working with InteractiveScene, self.frame, self.embed(), ShowCreation(), or ManimGL-specific patterns.

Best practices for ManimGL (Grant Sanderson's 3Blue1Brown version) - OpenGL-based animation engine with interactive development. Covers InteractiveScene, Tex with t2c, camera frame control, interactive mode (-se flag), 3D rendering, and checkpoint_paste() workflow.

NOT for Manim Community Edition (which uses `manim` imports and `manim` CLI).

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

#### mcp-builder
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

#### media-processing
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

#### nano-banana-pro
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

#### nano-pdf
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

#### nano-triple
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

#### notion
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

#### ~~obsidian~~ (Unavailable)
> **Reason**: Missing binary: obsidian-cli
#### openweather
Fetch current weather and forecasts for any location using the OpenWeather API.
Use when an agent needs current conditions or a short-term forecast for a city/address/zip or coordinates, and the API key is available in `.env` as OPENWEATHER_API_KEY.

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

#### pdf
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

#### playwright-cli
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

#### react:components
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

#### remotion
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

#### shadcn-ui
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

#### skill-creator
Guide for creating effective skills. This skill should be used when users want to create a new skill (or update an existing skill) that extends Claude's capabilities with specialized knowledge, workflows, or tool integrations.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/skill-creator/SKILL.md`
```yaml
name: skill-creator
description: Guide for creating effective skills. This skill should be used when users
  want to create a new skill (or update an existing skill) that extends Claude's capabilities
  with specialized knowledge, workflows, or tool integrations.
license: Complete terms in LICENSE.txt
```

#### slack
Use when you need to control Slack from Clawdbot via the slack tool, including reacting to messages or pinning/unpinning items in Slack channels or DMs.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/slack/SKILL.md`
```yaml
name: slack
description: Use when you need to control Slack from Clawdbot via the slack tool,
  including reacting to messages or pinning/unpinning items in Slack channels or DMs.
```

#### spotify-player
Terminal Spotify playback/search via spogo (preferred) or spotify_player.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/spotify-player/SKILL.md`
```yaml
name: spotify-player
description: Terminal Spotify playback/search via spogo (preferred) or spotify_player.
homepage: https://www.spotify.com
metadata:
  clawdbot:
    emoji: "\U0001F3B5"
    requires:
      anyBins:
      - spogo
      - spotify_player
    install:
    - id: brew
      kind: brew
      formula: spogo
      tap: steipete/tap
      bins:
      - spogo
      label: Install spogo (brew)
    - id: brew
      kind: brew
      formula: spotify_player
      bins:
      - spotify_player
      label: Install spotify_player (brew)
```

#### stitch-loop
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

#### ~~summarize~~ (Unavailable)
> **Reason**: Missing binary: summarize
#### telegram
Send and receive messages via Telegram using a Bot.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/telegram/SKILL.md`
```yaml
name: telegram
description: Send and receive messages via Telegram using a Bot.
metadata:
  requires:
  - telegram
```

#### tmux
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

#### trello
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

#### video-remotion
Comprehensive skill for programmatic video generation using the Remotion framework.
Enables creating, scaffolding, and rendering React-based videos via Python orchestration.
Supports both Local (CLI) and Cloud (Lambda) rendering.

Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/video-remotion/SKILL.md`
```yaml
name: video-remotion
description: 'Comprehensive skill for programmatic video generation using the Remotion
  framework.

  Enables creating, scaffolding, and rendering React-based videos via Python orchestration.

  Supports both Local (CLI) and Cloud (Lambda) rendering.

  '
```

#### voice-call
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

#### weather
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

#### webapp-testing
Toolkit for interacting with and testing local web applications using Playwright. Supports verifying frontend functionality, debugging UI behavior, capturing browser screenshots, and viewing browser logs.
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/webapp-testing/SKILL.md`
```yaml
name: webapp-testing
description: Toolkit for interacting with and testing local web applications using
  Playwright. Supports verifying frontend functionality, debugging UI behavior, capturing
  browser screenshots, and viewing browser logs.
license: Complete terms in LICENSE.txt
```

#### youtube-tutorial-explainer
Create an explainer-first tutorial artifact from a YouTube video so the user can learn without watching the full video.
Use when input is a YouTube URL or YouTube trigger payload (manual webhook or Composio trigger), and produce concise teachable notes with optional code only when it materially improves learning.

Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/youtube-tutorial-explainer/SKILL.md`
```yaml
name: youtube-tutorial-explainer
description: 'Create an explainer-first tutorial artifact from a YouTube video so
  the user can learn without watching the full video.

  Use when input is a YouTube URL or YouTube trigger payload (manual webhook or Composio
  trigger), and produce concise teachable notes with optional code only when it materially
  improves learning.

  '
```

#### youtube-tutorial-learning
Turn a YouTube tutorial into durable learning artifacts (concept doc + runnable implementation) stored under UA_ARTIFACTS_DIR.
USE WHEN user provides a YouTube URL and wants to learn/implement from it.

Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/youtube-tutorial-learning/SKILL.md`
```yaml
name: youtube-tutorial-learning
description: 'Turn a YouTube tutorial into durable learning artifacts (concept doc
  + runnable implementation) stored under UA_ARTIFACTS_DIR.

  USE WHEN user provides a YouTube URL and wants to learn/implement from it.

  '
```

#### zread-dependency-docs
Read documentation and code from open source GitHub repositories using the ZRead MCP server
Source: `/home/kjdragan/lrepos/universal_agent/.claude/skills/zread-dependency-docs/SKILL.md`
```yaml
name: zread-dependency-docs
description: Read documentation and code from open source GitHub repositories using
  the ZRead MCP server
```


### 🛠 Toolkits & Capabilities

#### 🛠 General Tools
- **Airtable** (`airtable`): Cloud database and spreadsheet hybrid for structured records and workflows.
- **Browserbase** (`browserbase_tool`): Browser automation and scraping via Browserbase.
- **Composio Search** (`composio_search`): Search engine for finding appropriate tools and actions within the Composio ecosystem.
- **Discord** (`discord`): Community chat, messaging, and server management in Discord.
- **Filetool** (`filetool`): Read, write, and manage files in the local workspace.
- **Google Maps** (`google_maps`): Maps, geocoding, and place lookup via Google Maps APIs.
- **Google Docs** (`googledocs`): Create and edit Google Docs documents.
- **Google Drive** (`googledrive`): Manage files and folders in Google Drive.
- **Google Sheets** (`googlesheets`): Read and update Google Sheets spreadsheets.
- **Google Super** (`googlesuper`): Google Workspace super admin and directory operations.
- **HubSpot** (`hubspot`): CRM platform for contacts, deals, pipelines, and marketing automation.
- **OpenWeather API** (`openweather_api`): Weather forecasts and current conditions from OpenWeather.
- **Perplexity AI** (`perplexityai`): Web research and question answering via Perplexity.
- **Reddit** (`reddit`): Read and post content on Reddit communities.
- **Slack** (`slack`): Team messaging and collaboration in Slack workspaces.
- **Sqltool** (`sqltool`): Execute SQL queries against connected databases.
- **YouTube** (`youtube`): Search, manage, and publish content on YouTube.
- **Telegram** (`telegram`): Send and receive messages via Telegram bots.
- **Figma** (`figma`): Design file access and collaboration via Figma.
- **Browserbase** (`browserbase`): Headless browser for web scraping and interaction.

#### ⚙️ Engineering & Code
- **Code Interpreter** (`codeinterpreter`): Executes Python code in a sandboxed environment for calculation, data analysis, and logic.
- **GitHub** (`github`): Code hosting and collaboration platform.

#### 🏢 Operations & Communication
- **Gmail** (`gmail`): Google's email service.
- **Google Calendar** (`googlecalendar`): Google Calendar for scheduling, events, and reminders.
- **Linear** (`linear`): Issue tracking and product planning for engineering teams.
- **Notion** (`notion`): Workspace for docs, tasks, and knowledge bases in Notion.
