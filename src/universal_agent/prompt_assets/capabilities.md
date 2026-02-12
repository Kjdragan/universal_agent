<!-- Agent Capabilities Registry -->

<!-- Generated: 2026-02-10 07:37:55 -->

### 🤖 Specialist Agents (Micro-Agents)
Delegate full workflows to these specialists based on value-add.

#### 🛠 General Tools
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

#### 🎨 Creative & Media
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

#### 🔬 Research & Analysis
- **research-specialist**: Sub-agent for a unified research pipeline: Search followed by automated Crawl & Refine.

  -> Delegate: `Task(subagent_type='research-specialist', ...)`
- **professor**: Academic oversight and skill creation.
  -> Delegate: `Task(subagent_type='professor', ...)`
- **scribe**: Memory logging and fact recording.
  -> Delegate: `Task(subagent_type='scribe', ...)`

#### 🏢 Operations & Communication
- **system-configuration-agent**: System configuration and runtime operations specialist for Universal Agent.

**WHEN TO DELEGATE (MUST BE USED):**
- User asks to create/reschedule/pause/resume a Chron/Cron job
- User asks to change scheduling or automation behavior in natural language
- User asks to update heartbeat interval/delivery behavior
- User asks to change ops/runtime/service configuration

**THIS SUB-AGENT:**
- Interprets natural-language system-change requests into structured operations
- Applies scheduling/runtime changes through first-class APIs and config paths
- Verifies before/after state and returns auditable summaries

  -> Delegate: `Task(subagent_type='system-configuration-agent', ...)`
- **Routing rule**: Never use OS-level `crontab` for product scheduling requests. Route through `system-configuration-agent` and Chron APIs.

- **slack-expert**: Expert for Slack workspace interactions.

**WHEN TO DELEGATE:**
- User mentions 'slack', 'channel', '#channel-name'
- User asks to 'post to slack', 'summarize messages', 'what was discussed in'

**THIS SUB-AGENT:**
- Lists channels to find IDs
- Fetches conversation history
- Posts formatted messages

  -> Delegate: `Task(subagent_type='slack-expert', ...)`

#### ⚙️ Engineering & Code
- **task-decomposer**: **Sub-Agent Purpose:** Decompose complex requests into phases for harness execution.

**WHEN TO USE:**
- URW Orchestrator delegates decomposition tasks here.
- You analyze request complexity and create phased plans.
- Output: `macro_tasks.json` with phases, tasks, and success criteria.

  -> Delegate: `Task(subagent_type='task-decomposer', ...)`

### 📚 Standard Operating Procedures (Skills)
These organized guides are available to **ALL** agents and sub-agents. You should prioritize using these instead of improvising.
They represent the collective knowledge of the system. **Think about your capabilities** and how these guides can help you.

**Progressive Disclosure**:
1. **Scan**: Read the YAML frontmatter below to identifying relevant skills.
2. **Read**: If a skill seems useful, use `mcp__internal__read_file` to read the full Markdown content (SOP).
3. **Execute**: Follow the procedure step-by-step.

- No skills discovered.

### 🛠 Toolkits & Capabilities
