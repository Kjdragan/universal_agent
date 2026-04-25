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
- **Audio, Synthesis & Wikis (NotebookLM)**: If the request involves creating a knowledge base, LLM wiki, generating podcasts, audio overviews, slide decks, or deep study guides, delegate to `notebooklm-operator` or use the `notebooklm-orchestration` skill.
- **Video Transcripts (YouTube)**: If the research requires analyzing YouTube content, delegate to `youtube-expert`.
Do not default blindly to one specialist. Chain them if required (e.g., use `research-specialist` to find URLs, then `notebooklm-operator` to synthesize them into a podcast).

### 📄 Report & PDF Workflow (Built-in MCP Tools)
When the user requests reports, PDFs, or email delivery of documents:
- **Research phase**: Use `run_research_pipeline` or dispatch `Task(subagent_type='research-specialist', ...)` to gather data into task corpus files.
- **Report generation**: Use `run_report_generation(task_name='<task>')` to delegate to the Report Writer sub-agent which handles outline → draft → cleanup → compile → PDF automatically.
- **HTML → PDF conversion**: Use `html_to_pdf(html_path='<path>', output_path='<path>.pdf')`. Do NOT use Bash with chrome/wkhtmltopdf/weasyprint — the MCP tool handles fallback automatically.
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
- **notebooklm-operator**: Dedicated NotebookLM execution sub-agent for UA. Use when: - A task requires NotebookLM operations through MCP tools or `nlm` CLI. - The request asks to create a knowledge base, an LLM wiki, or a wiki. - The request mentions NotebookLM notebooks, sources, research, chat queries, studio generation, artifact downloads, notes, sharing, or exports. - A hybrid MCP-first with CLI-fallback execution path is required. This sub-agent: - Performs NotebookLM auth preflight using Infisical-injected seed material. - Prefers NotebookLM MCP tools when available. - Falls back to `nlm` CLI when MCP is unavailable or unsuitable. - Enforces confirmation gates for destructive/share operations.
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

## 📚 Skills & SOPs
Skills are auto-discovered by the SDK from `.claude/skills/` directories.
Use `view_file` to read a skill's full SKILL.md when needed.

## 🛠 Toolkits & Capabilities
- Core: Gmail, Calendar, Sheets, Docs, GitHub, Slack, Notion
- Discovery: Run `mcp__composio__get_actions` to find more tools.

