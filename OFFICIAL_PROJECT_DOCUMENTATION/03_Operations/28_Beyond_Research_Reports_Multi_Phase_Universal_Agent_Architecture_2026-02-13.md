# 28 — Beyond Research Reports: Multi-Phase Universal Agent Architecture

**Date:** 2026-02-13  
**Status:** Strategic Vision + Implementation Roadmap  
**Scope:** Expanding the Universal Agent from a linear research-report pipeline into a true multi-phase, recursive, long-running autonomous system.

---

## 1. The Problem: Report-Writer Gravity Well

### 1.1 Current Default Behavior

When given an open-ended "do something amazing" prompt, the agent consistently collapses into this single linear pipeline:

```
User Input → Research → Generate Images → Write Report → Convert to PDF → Email
```

This happens because:

1. **The system prompt's strongest examples are research-oriented.** The capabilities registry lists `research-specialist` and `report-writer` as the most detailed, best-documented subagents.
2. **Task decomposition only knows two delegates.** The `task-decomposer` agent's "Sub-Agent Awareness" table lists exactly two specialists: `research-specialist` and `report-writer`. It literally cannot plan tasks for any other capability.
3. **No "action" vocabulary.** The system prompt describes what the agent *can observe and produce* (reports, images, videos) but not what it *can do in the world* (send messages, manage calendars, interact with services, process data, execute code).
4. **Single-phase mental model.** Even with URW orchestration, the default decomposition pattern is Research → Synthesis → Report. There's no template for iterative, branching, or recursive flows.

### 1.2 The Architecture Flow Diagram

The current architecture flow (from `cron_56d7add84a`) illustrates this perfectly:

```
User Input
    │
    ▼
Intent Classification / NLP Analysis
    │
    ▼
Tool Selection Decision
    │
    ├──────────────────┼──────────────────┐
    ▼                  ▼                  ▼
Research Module    Media Module    Communication Module
(Web Search →     (Image Gen →    (Email → Chat →
 Doc Analysis →    Audio Proc →    Notifications)
 Data Extract)     Video Create)
    │                  │                  │
    └──────────────────┼──────────────────┘
                       ▼
            Result Aggregation & Synthesis
                       │
                       ▼
            Response Generation / LLM Processing
                       │
                       ▼
                  User Output
```

**What's missing from this diagram:**
- No feedback loops (results don't trigger new research)
- No branching (one path chosen, others ignored)
- No phased execution (everything runs in one pass)
- No real-world actions (ordering, purchasing, scheduling, deploying)
- No data analysis pipeline (statistical analysis, data science, code execution)
- No recursive refinement (generate → evaluate → regenerate)

---

## 2. The Vision: Multi-Phase Recursive Execution

### 2.1 Expanded Architecture

```
                         ┌─────────────────────┐
                         │    USER REQUEST      │
                         │  (or Cron/Webhook)   │
                         └────────┬────────────┘
                                  │
                                  ▼
                    ┌─────────────────────────────┐
                    │  TASK DECOMPOSER (Enhanced)  │
                    │  → Analyze complexity        │
                    │  → Identify capability needs │
                    │  → Create phase DAG          │
                    │  → Estimate duration/budget   │
                    └────────┬────────────────────┘
                             │
                             ▼
                 ┌───────────────────────────┐
                 │   PHASE ORCHESTRATOR      │
                 │   (URW + Checkpointing)   │
                 └─────┬─────────────┬───────┘
                       │             │
            ┌──────────┘             └──────────┐
            ▼                                   ▼
    ┌───────────────┐                  ┌───────────────┐
    │   PHASE 1     │                  │   PHASE 2     │
    │  (parallel    │─────────────────▶│  (depends on  │
    │   tasks)      │   artifacts      │   Phase 1)    │
    └───┬───┬───┬───┘                  └───┬───┬───┬───┘
        │   │   │                          │   │   │
        ▼   ▼   ▼                          ▼   ▼   ▼
      [T1] [T2] [T3]                    [T4] [T5] [T6]
        │   │   │                          │   │   │
        └───┼───┘                          └───┼───┘
            ▼                                  ▼
    ┌───────────────┐                  ┌───────────────┐
    │  EVALUATION   │                  │  EVALUATION   │
    │  CHECKPOINT   │                  │  CHECKPOINT   │
    └───────┬───────┘                  └───────┬───────┘
            │                                  │
            │         ┌──────────┐             │
            └────────▶│ DECISION │◀────────────┘
                      │  GATE    │
                      └────┬─────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
         [Continue]   [Recurse]    [Pivot]
         to Phase 3   back to      to new
                      Phase 1      approach
                      with new
                      inputs
```

### 2.2 Key Differences from Current Architecture

| Aspect | Current | Proposed |
|--------|---------|----------|
| **Flow** | Linear pipeline | Directed Acyclic Graph (DAG) with cycles |
| **Phases** | 1 (implicit) | N phases, each with parallel tasks |
| **Duration** | Minutes | Minutes to 24+ hours |
| **Recursion** | None | Evaluation gates can loop back |
| **Task types** | Research + Report | 8+ capability domains |
| **Decision points** | Tool Selection only | After every phase |
| **Checkpointing** | End of run | After every phase |
| **Budget** | Unlimited single run | Per-phase budgets with total cap |

---

## 3. The Eight Capability Domains

The current system treats everything as "research or report." In reality, the Universal Agent has **eight distinct capability domains** that should be first-class citizens in task decomposition:

### Domain 1: Intelligence Gathering
*What exists today, but should be one domain among many.*
- Web search (Composio search)
- News monitoring
- URL scraping (Gemini URL context)
- Document crawling and extraction
- YouTube transcript analysis

### Domain 2: Data Analysis & Computation
*Severely underutilized. The agent has a code interpreter but rarely uses it proactively.*
- Python code execution (Composio CodeInterpreter)
- Statistical analysis
- Data visualization (matplotlib, plotly via code)
- Numerical modeling
- CSV/JSON data processing
- SQL queries against datasets

**Example flow:** Research crypto market → download price data → run statistical analysis → identify trends → visualize findings → draw conclusions

### Domain 3: Creative Media Production
*Used only as a garnish on reports. Should be a primary output modality.*
- Image generation (Gemini)
- Video creation (FFmpeg, Remotion)
- Audio processing
- Mathematical animations (Manim)
- Diagram generation (Mermaid, Excalidraw)
- PDF creation and manipulation

**Example flow:** Analyze a scientific paper → create a 3Blue1Brown-style explainer video → generate supporting diagrams → produce a shareable media package

### Domain 4: Communication & Delivery
*Currently limited to "email the PDF." Should encompass all channels.*
- Gmail (send, draft, search)
- Slack (post, thread, react)
- Discord (messages, channels)
- Telegram (messages)
- Calendar events (schedule meetings, reminders)
- Notifications

**Example flow:** Research team's project status → post summary to Slack → schedule follow-up meeting on Google Calendar → email stakeholders with attachments

### Domain 5: Real-World Interaction
*Almost completely absent. This is what separates a "report writer" from a "universal agent."*
- Place search (GoPlaces, Google Places)
- Browser automation (Browserbase, Playwright)
- Form filling and web interaction
- Service ordering and booking
- Price comparison and shopping

**Example flow:** "Find the best Italian restaurant near me, check reviews, make a reservation for Friday, and add it to my calendar"

### Domain 6: Software Engineering
*Available but never proactively used.*
- Code generation and editing
- GitHub operations (issues, PRs, repos)
- Coding agent delegation (Claude Code, Codex)
- Test execution
- Deployment operations

**Example flow:** Research a new library → create a proof-of-concept project → write tests → push to GitHub → create a PR with documentation

### Domain 7: Knowledge Management
*Memory and learning — the agent should build on previous work.*
- Letta memory (long-term storage)
- Notion pages and databases
- Skill creation (professor agent)
- Session workspace artifacts
- Cross-session knowledge transfer

**Example flow:** Complete a research project → extract key learnings → store in Notion database → create a reusable skill for similar future tasks

### Domain 8: System Operations
*Self-management and meta-capabilities.*
- Cron job scheduling
- Heartbeat configuration
- Webhook management
- Self-monitoring and diagnostics
- Budget and resource management

**Example flow:** "Monitor this stock every hour, alert me if it drops 5%, and automatically research what caused the drop"

---

## 4. Multi-Phase Execution Patterns

The current system only knows one pattern: **Linear Pipeline**. Here are six additional execution patterns the agent should be able to employ:

### Pattern 1: Linear Pipeline (Current Default)
```
[Research] → [Analyze] → [Report] → [Deliver]
```
**When to use:** Simple factual queries, straightforward report requests.  
**Duration:** 5-30 minutes.

### Pattern 2: Fan-Out / Fan-In
```
                    ┌─[Research Topic A]─┐
[Decompose Task] ──┼─[Research Topic B]─┼──[Synthesize All]──[Report]
                    └─[Research Topic C]─┘
```
**When to use:** Comparative analysis, multi-perspective investigation.  
**Duration:** 15-60 minutes.  
**Example:** "Compare the top 5 AI coding assistants across performance, cost, and user satisfaction."

### Pattern 3: Iterative Deepening
```
[Broad Research] → [Evaluate Gaps] → [Targeted Research] → [Evaluate] → ... → [Synthesize]
```
**When to use:** Deep investigative tasks where initial research reveals new questions.  
**Duration:** 30 minutes - 4 hours.  
**Example:** "Investigate why Company X's stock dropped 20% today — find the root cause, not just the headline."

### Pattern 4: Pipeline with Side Effects
```
[Research] → [Analyze] → [Report]
                │              │
                ├─[Schedule Follow-up Cron]
                ├─[Post to Slack]
                └─[Add Calendar Event]
```
**When to use:** Tasks that require both analysis AND real-world actions.  
**Duration:** 10-60 minutes.  
**Example:** "Research competitors' pricing changes, update our comparison doc, and alert the sales team on Slack."

### Pattern 5: Recursive Refinement Loop
```
[Generate Draft] → [Evaluate Quality] → [Identify Weaknesses] → [Targeted Improvement] → [Re-evaluate] → ...
```
**When to use:** Creative production, high-quality output requirements.  
**Duration:** 30 minutes - 6 hours.  
**Example:** "Create a 5-minute explainer video about quantum computing that a 10-year-old could understand."

### Pattern 6: Monitor-React-Act (Long-Running)
```
[Set Up Monitor] → [Wait for Trigger] → [React with Analysis] → [Take Action] → [Report] → [Continue Monitoring]
```
**When to use:** Ongoing surveillance, automated responses to events.  
**Duration:** Hours to days (via Cron + Heartbeat).  
**Example:** "Watch for any SEC filings from Company X. When one appears, analyze it, compare to previous filings, and email me a brief."

### Pattern 7: Multi-Domain Orchestration (The Grand Vision)
```
Phase 1: Intelligence
  ├─ [Research Market Data]
  ├─ [Scrape Competitor Sites]
  └─ [Download Financial Reports]

Phase 2: Analysis
  ├─ [Statistical Analysis on Market Data]
  ├─ [Sentiment Analysis on News]
  └─ [Comparative Pricing Analysis]

Phase 3: Synthesis + Creation
  ├─ [Write Executive Summary]
  ├─ [Generate Data Visualizations]
  ├─ [Create Presentation Slides]
  └─ [Produce Explainer Video]

Phase 4: Distribution
  ├─ [Email Report to Stakeholders]
  ├─ [Post Key Findings to Slack]
  ├─ [Schedule Follow-up Meeting]
  └─ [Create Cron Job for Weekly Update]

Phase 5: Knowledge Capture
  ├─ [Store Key Metrics in Notion]
  ├─ [Update Long-Term Memory]
  └─ [Create Reusable Analysis Skill]
```
**When to use:** Major strategic initiatives, comprehensive projects.  
**Duration:** 2-24 hours.

---

## 5. What Needs to Change

### 5.1 Task Decomposer Must Know All Domains

**Current state:** The task-decomposer only lists `research-specialist` and `report-writer` as available delegates.

**Required change:** Expand the sub-agent awareness table to include ALL capability domains:

```json
{
  "available_delegates": {
    "research-specialist": "Web search, crawling, corpus creation",
    "report-writer": "HTML/PDF report generation from corpus",
    "image-expert": "Image generation, editing, infographics",
    "video-creation-expert": "Video/audio download, processing, effects",
    "video-remotion-expert": "Programmatic video generation with React",
    "mermaid-expert": "Diagram and flowchart creation",
    "slack-expert": "Slack workspace interactions",
    "browserbase": "Browser automation, web scraping, form filling",
    "coding-agent": "Code generation, testing, deployment",
    "system-configuration-agent": "Cron jobs, scheduling, runtime config"
  },
  "available_tools_direct": {
    "codeinterpreter": "Python execution for data analysis, statistics, computation",
    "gmail": "Email sending, drafting, inbox management",
    "google_calendar": "Calendar event creation and management",
    "goplaces": "Location search, restaurant/business discovery",
    "github": "Repository management, issues, PRs"
  }
}
```

### 5.2 Phase DAG Instead of Linear Phase List

**Current state:** `macro_tasks.json` has a flat list of sequential phases.

**Required change:** Support a DAG (directed acyclic graph) with:
- Parallel phases that can run concurrently
- Conditional branches (if research reveals X, do Y; otherwise do Z)
- Loop-back edges (re-run Phase 1 with refined queries after Phase 2 evaluation)
- Budget constraints per phase

**Proposed `macro_tasks.json` v2 schema:**

```json
{
  "request_summary": "...",
  "estimated_duration_minutes": 120,
  "execution_pattern": "iterative_deepening",
  "phases": [
    {
      "phase_id": 1,
      "name": "Intelligence Gathering",
      "parallel": true,
      "tasks": [
        {"task_id": "1.1", "delegate_to": "research-specialist", "...": "..."},
        {"task_id": "1.2", "delegate_to": "browserbase", "...": "..."}
      ],
      "on_complete": {"goto": 2}
    },
    {
      "phase_id": 2,
      "name": "Analysis & Computation",
      "tasks": [
        {"task_id": "2.1", "tool": "codeinterpreter", "...": "..."}
      ],
      "evaluation_gate": {
        "criteria": "Statistical significance achieved",
        "on_pass": {"goto": 3},
        "on_fail": {"goto": 1, "reason": "Need more data", "refinement_prompt": "..."}
      }
    },
    {
      "phase_id": 3,
      "name": "Production & Delivery",
      "parallel": true,
      "tasks": [
        {"task_id": "3.1", "delegate_to": "report-writer"},
        {"task_id": "3.2", "delegate_to": "image-expert"},
        {"task_id": "3.3", "tool": "gmail"}
      ]
    }
  ]
}
```

### 5.3 System Prompt Must Break the Report Mold

**Current state:** The main system prompt heavily emphasizes research and report capabilities.

**Required change:** Add a "Creativity and Autonomy" section that explicitly tells the agent:

```
## AUTONOMY GUIDELINES

When given an open-ended task, you are NOT limited to research and reports.

Consider ALL your capabilities:
- Can you COMPUTE something? (statistics, modeling, data analysis)
- Can you CREATE something? (video, animation, music, code, diagrams)
- Can you DO something in the real world? (send messages, schedule events, search places, automate browsers)
- Can you LEARN something? (store knowledge, create skills, build on past work)
- Can you MONITOR something? (set up cron jobs, create alerts, watch for changes)

For complex tasks, think in PHASES:
1. What information do I need? (Intelligence)
2. What analysis should I perform? (Computation)
3. What should I create? (Production)
4. Who needs to know? (Distribution)
5. What should I remember? (Knowledge Capture)

Do NOT default to "research and write a report" unless that's specifically what was asked.
```

### 5.4 Duration-Aware Execution

**Current state:** All tasks assumed to complete in one agent turn (minutes).

**Required change:** The system needs to understand time horizons:

| Duration | Mechanism | Phases |
|----------|-----------|--------|
| < 15 min | Single agent turn | 1 |
| 15-60 min | URW with checkpoints | 2-3 |
| 1-4 hours | URW with cron continuation | 3-6 |
| 4-24 hours | Cron-chained phases with heartbeat monitoring | 6-20 |
| Days | Heartbeat-driven with daily progress reports | 20+ |

For long-running tasks, each phase should:
1. Execute as a separate cron job or agent session
2. Checkpoint all artifacts to the workspace
3. Write progress to `session_progress.md`
4. Schedule the next phase as a cron job
5. Send status updates via the user's preferred channel

---

## 6. Concrete Showcase Scenarios

These are "impressive demo" tasks that would exercise the full breadth of the system:

### Scenario A: "Competitive Intelligence Dashboard" (2-4 hours)

```
Phase 1: Intelligence (parallel, 30 min)
  - Research 5 competitor companies via web search
  - Scrape their pricing pages via Browserbase
  - Download their recent SEC filings

Phase 2: Analysis (sequential, 45 min)
  - Extract pricing data into structured format (CodeInterpreter)
  - Run comparative statistical analysis
  - Perform sentiment analysis on news coverage
  - Generate trend charts and visualizations

Phase 3: Production (parallel, 45 min)
  - Create comprehensive HTML report with embedded charts
  - Generate infographic comparing key metrics
  - Create a 2-minute Manim animation showing market position changes
  - Build a Notion database with all structured data

Phase 4: Distribution (parallel, 15 min)
  - Email PDF report to stakeholders
  - Post executive summary to Slack #strategy channel
  - Schedule weekly cron job to re-run this analysis
  - Add "Quarterly Review" to Google Calendar
```

### Scenario B: "Personal Event Planner" (30-60 min)

```
Phase 1: Discovery (parallel, 15 min)
  - Search for top-rated restaurants near user's location (GoPlaces)
  - Check weather forecast for the date
  - Research event ideas matching user preferences

Phase 2: Planning (sequential, 15 min)
  - Compare restaurant options (ratings, price, cuisine)
  - Create an itinerary with timing
  - Generate a mood board with images for the theme

Phase 3: Execution (parallel, 15 min)
  - Create Google Calendar event with itinerary details
  - Send invitation emails to guests via Gmail
  - Post event details to relevant Slack/Discord channel
  - Generate a shareable PDF invitation with event details

Phase 4: Follow-up (scheduled)
  - Set reminder cron job for day-before prep checklist
  - Schedule post-event feedback collection
```

### Scenario C: "Deep Market Analysis with Recursive Refinement" (4-12 hours)

```
Phase 1: Broad Scan (45 min)
  - Search 20+ sources across news, academic, and financial databases
  - Identify top 10 most relevant themes

Phase 2: Deep Dive (2 hours, parallel)
  - For each theme: dedicated research + data extraction
  - Download relevant datasets
  - Scrape live market data

→ EVALUATION GATE: Are there gaps in the data?
  → YES: Return to Phase 1 with refined queries targeting gaps
  → NO: Continue to Phase 3

Phase 3: Statistical Analysis (1-2 hours)
  - Run correlation analysis across datasets
  - Build predictive models
  - Generate confidence intervals
  - Create 15+ data visualizations

Phase 4: Synthesis (1 hour)
  - Write 30-page comprehensive report
  - Create executive summary video (Manim/Remotion)
  - Build interactive Notion dashboard
  - Generate presentation slides

Phase 5: Distribution (30 min)
  - Email report + video to stakeholders
  - Post key findings to Slack
  - Store analysis methodology as reusable skill
  - Schedule monthly re-analysis cron job
```

### Scenario D: "Autonomous Skill Builder" (1-2 hours)

```
Phase 1: Research the Target Domain
  - Identify the API/service to integrate
  - Study documentation and examples
  - Find existing community solutions

Phase 2: Design & Implement
  - Design the skill's interface (SKILL.md)
  - Write the implementation code
  - Create test cases

Phase 3: Validate & Iterate
  - Execute tests via CodeInterpreter
  - Evaluate results
  → LOOP: If tests fail, refine implementation and re-test

Phase 4: Package & Deploy
  - Write documentation
  - Register skill in the skills directory
  - Push to GitHub as PR
  - Notify user of new capability
```

---

## 7. New Skills and Workflows to Build

### 7.1 Proposed New Skills

| Skill | Description | Priority |
|-------|-------------|----------|
| `data-analysis` | Statistical analysis, visualization, and modeling via CodeInterpreter | **P0** |
| `competitive-intelligence` | Multi-source competitor research + structured comparison | **P0** |
| `event-planner` | Discover venues, create itineraries, send invitations | **P1** |
| `monitoring-setup` | Create long-running monitors with cron + alerting | **P1** |
| `skill-creator` | Autonomously design and implement new skills | **P1** |
| `presentation-builder` | Create slide decks from research/analysis outputs | **P2** |
| `web-automation` | Multi-step browser workflows (fill forms, extract data, screenshots) | **P2** |
| `project-manager` | Decompose large projects, track progress, report status | **P2** |

### 7.2 Proposed New Workflows

#### Workflow: `deep-investigation`
```yaml
description: Multi-phase investigative analysis with recursive refinement
phases:
  - intelligence_gathering (parallel research across multiple sources)
  - data_extraction (structured data from raw sources)
  - statistical_analysis (CodeInterpreter for computation)
  - evaluation_gate (assess completeness, loop if needed)
  - synthesis (report + visualizations + media)
  - distribution (email + slack + calendar)
```

#### Workflow: `autonomous-project`
```yaml
description: Long-running autonomous project execution over hours/days
mechanism: Cron-chained phases with heartbeat monitoring
features:
  - Phase checkpointing after each step
  - Progress reports via preferred channel
  - Automatic retry on failure
  - Human approval gates for critical decisions
  - Budget tracking per phase
```

#### Workflow: `real-world-action`
```yaml
description: Tasks that interact with the physical/digital world beyond research
capabilities:
  - Location-based actions (find places, get directions)
  - Scheduling (calendar events, reminders)
  - Communication (multi-channel messaging)
  - Purchasing/Booking (via browser automation)
  - Monitoring (set up watchers and alerts)
```

---

## 8. Implementation Roadmap

### Phase 1: Expand Task Decomposer (1-2 days)
- Update `task-decomposer.md` with all 8 capability domains
- Add the full delegate/tool awareness table
- Add execution pattern selection logic
- Support `evaluation_gate` and `parallel` flags in `macro_tasks.json`

### Phase 2: Update System Prompt (1 day)
- Add "Autonomy Guidelines" section to main system prompt
- Add "Capability Domains" reference to capabilities.md
- Add "Execution Patterns" examples to prompt assets
- Remove implicit bias toward research-only flows

### Phase 3: Build `data-analysis` Skill (1-2 days)
- CodeInterpreter-based statistical analysis
- Chart/visualization generation
- Structured data extraction and transformation
- This is the single most impactful missing capability

### Phase 4: Enhance URW Orchestrator (2-3 days)
- Support DAG-based phase execution (not just linear)
- Implement evaluation gates with loop-back
- Add parallel task execution within phases
- Budget tracking per phase
- Long-running task support via cron chaining

### Phase 5: Build Showcase Demos (ongoing)
- Implement Scenario A (Competitive Intelligence) as proof of concept
- Iterate on flow quality and reliability
- Document patterns that work well

---

## 9. Diagrams

### 9.1 Current vs. Proposed Architecture

**Current (Single-Phase Linear):**
```
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│ Research  │───▶│ Analyze  │───▶│ Generate │───▶│  Report  │───▶│  Email   │
│          │    │          │    │ Images   │    │  (PDF)   │    │          │
└──────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘
```

**Proposed (Multi-Phase DAG):**
```
                              ┌──────────────┐
                              │  DECOMPOSE   │
                              │  (Phase DAG) │
                              └──────┬───────┘
                                     │
                    ┌────────────────┬┴───────────────┐
                    ▼                ▼                 ▼
             ┌────────────┐  ┌────────────┐   ┌────────────┐
             │ Research   │  │ Scrape     │   │ Download   │
             │ (parallel) │  │ (browser)  │   │ Data Sets  │
             └─────┬──────┘  └─────┬──────┘   └─────┬──────┘
                   └───────────────┼─────────────────┘
                                   ▼
                          ┌────────────────┐
                          │   EVALUATION   │◄──────────────┐
                          │   GATE         │               │
                          └───────┬────────┘               │
                                  │                        │
                    ┌─────────────┼─────────────┐          │
                    ▼             ▼              ▼          │
             ┌────────────┐ ┌─────────┐  ┌───────────┐    │
             │ Statistics │ │ Visualize│  │ Sentiment │    │
             │ (code)     │ │ (charts) │  │ Analysis  │    │
             └─────┬──────┘ └────┬─────┘  └─────┬─────┘   │
                   └─────────────┼───────────────┘         │
                                 ▼                         │
                        ┌────────────────┐                 │
                        │   EVALUATE     │─── gaps? ───────┘
                        │   QUALITY      │
                        └───────┬────────┘
                                │ pass
                   ┌────────────┼─────────────┐
                   ▼            ▼             ▼
            ┌───────────┐ ┌──────────┐ ┌───────────┐
            │  Report   │ │  Video   │ │  Notion   │
            │  (HTML)   │ │  (Manim) │ │  Database │
            └─────┬─────┘ └────┬─────┘ └─────┬─────┘
                  └────────────┼──────────────┘
                               ▼
                   ┌────────────────────────┐
                   │     DISTRIBUTE         │
                   │  Email + Slack + Cal   │
                   │  + Schedule Follow-up  │
                   └────────────────────────┘
```

### 9.2 Capability Domain Map

```
                        ┌──────────────────────────────────┐
                        │       UNIVERSAL AGENT            │
                        │      Capability Domains          │
                        └──────────────┬───────────────────┘
                                       │
        ┌──────────┬──────────┬────────┼────────┬──────────┬──────────┐
        ▼          ▼          ▼        ▼        ▼          ▼          ▼
   ┌─────────┐┌─────────┐┌────────┐┌────────┐┌────────┐┌─────────┐┌─────────┐
   │INTEL    ││DATA     ││MEDIA   ││COMMS   ││REAL    ││SOFTWARE││KNOWLEDGE│
   │GATHER  ││ANALYSIS ││PRODUCE ││DELIVER ││WORLD   ││ENGINEER││MANAGE  │
   │         ││         ││        ││        ││ACTION  ││        ││         │
   │• Search ││• Stats  ││• Image ││• Email ││• Places││• Code  ││• Memory │
   │• Crawl  ││• Model  ││• Video ││• Slack ││• Browse││• GitHub││• Notion │
   │• Scrape ││• Charts ││• Audio ││• Discord││• Book  ││• Test  ││• Skills │
   │• Extract││• SQL    ││• Manim ││• Telegram│• Order ││• Deploy││• Learn  │
   │• YouTube││• Code   ││• PDF   ││• Calendar│       ││       ││         │
   └─────────┘└─────────┘└────────┘└────────┘└────────┘└─────────┘└─────────┘
```

### 9.3 Long-Running Execution Timeline

```
Hour 0          Hour 4          Hour 8          Hour 12         Hour 24
│               │               │               │               │
▼               ▼               ▼               ▼               ▼
┌───────┐ ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌────────────┐
│Phase 1│ │ Phase 2 │ │ Phase 3  │ │ Phase 4  │ │  Phase 5   │
│Intel  │ │Analysis │ │Production│ │Distribute│ │Knowledge   │
│Gather │ │+ Eval   │ │+ Refine  │ │+ Actions │ │Capture     │
└───┬───┘ └────┬────┘ └────┬─────┘ └────┬─────┘ └─────┬──────┘
    │          │           │            │              │
    ▼          ▼           ▼            ▼              ▼
 [Cron]     [Cron]     [Cron+Loop]   [Cron]       [Cron]
 [Checkpoint][Checkpoint][Checkpoint] [Checkpoint]  [Final]
    │          │           │            │              │
    ▼          ▼           ▼            ▼              ▼
 📊 Status  📊 Status   📊 Status   📊 Status    📊 Complete
 via Slack  via Slack   via Slack   via Slack    via Email
```

---

## 10. Summary

The Universal Agent has the infrastructure for genuinely universal capabilities: URW orchestration, cron scheduling, heartbeat monitoring, 40+ skills, 15+ subagents, and Composio integrations spanning email, calendar, search, code execution, and more.

**The bottleneck is not infrastructure — it's imagination.** The task decomposer, system prompt, and default mental model all funnel every request into the same research-report pipeline. Breaking out of this requires:

1. **Teaching the decomposer about all 8 capability domains** (not just research + report)
2. **Supporting non-linear execution patterns** (DAG, recursive, long-running)
3. **Explicitly telling the agent it can DO things**, not just KNOW things
4. **Building showcase skills** that demonstrate breadth (data analysis, event planning, monitoring)
5. **Duration-aware execution** that can span hours or days via cron chaining

The goal is simple: when someone says "do something amazing," the agent should think "what combination of research, computation, creation, communication, and real-world action would be most impressive?" — not "let me write another report."

---

## 11. Implementation Status (2026-02-13)

All critical-path changes have been implemented:

### Completed Changes

| Step | File(s) | What Changed |
|------|---------|--------------|
| 1. Task Decomposer Rewrite | `.claude/agents/task-decomposer.md` | Full capability matrix (20+ Composio toolkits, 12 subagent coordinators, 8 local MCP tools), 6 execution patterns, `macro_tasks.json` v2 schema with `tool_type`, `handoff`, `evaluation_gate`, `parallel` fields |
| 2. Decomposition Prompt | `src/universal_agent/urw/decomposer.py` | Expanded from 2 delegates to full list. Added Composio-anchored decomposition principle and all toolkit slugs. |
| 3. System Prompt | `src/universal_agent/main.py` | Added "Capability Domains" block with 8 domains. Softened mandatory report delegation to be one pattern among many. Added autonomy guidelines. |
| 4. Data Analyst Agent | `.claude/agents/data-analyst.md` (NEW) | Composio CodeInterpreter-based analysis, charts, structured findings. Handoff-aware. |
| 5. Action Coordinator Agent | `.claude/agents/action-coordinator.md` (NEW) | Multi-channel delivery via Composio Gmail/Calendar/Slack/Drive. Bridges local files to Composio backbone. |
| 6. Post-Task Hook | `src/universal_agent/main.py` `on_post_task_guidance` | Graph-aware next-step guidance per subagent type. After research-specialist completes, suggests data-analyst/report-writer/image-expert instead of defaulting to report. |
| 7. Capabilities Registry | `src/universal_agent/prompt_assets/capabilities.md` | Added `data-analyst` and `action-coordinator` entries with delegation instructions. |
| 8. Skills Map | `src/universal_agent/main.py` `SUBAGENT_EXPECTED_SKILLS` | Registered both new subagents in the hook system. |

### Core Architectural Decision: Composio-Anchored Decomposition

The decomposition philosophy preserves the existing Composio-first design:

```
Composio Tools = Deterministic Hands (OAuth-authenticated atomic actions)
Local MCP Tools = Processing Brain (crawl, compile, render, generate)
Subagents       = Workflow Coordinators (orchestrate Composio + local sequences)
```

**Not every phase uses Composio.** Pure-local phases (Manim rendering, image generation, PDF compilation) are first-class. The key is that local-only phases define **handoff points** — explicit artifact bridges back into the Composio backbone for downstream delivery phases.

### How Execution Patterns Map to Task() Dispatch

Each pattern is implemented through the existing Claude Agent SDK `Task(subagent_type=...)` dispatch:

| Pattern | Task() Sequence |
|---------|----------------|
| Linear Pipeline | `Task(research-specialist)` → `Task(report-writer)` → `GMAIL_SEND_EMAIL` |
| Fan-Out | Multiple parallel `Task(research-specialist)` → `Task(data-analyst)` → `Task(report-writer)` |
| Iterative Deepening | `Task(research-specialist)` → `Task(data-analyst)` → evaluate → loop back or continue |
| Pipeline + Side Effects | `Task(research-specialist)` → `Task(report-writer)` + `GMAIL_*` + `SLACK_*` + `GOOGLECALENDAR_*` |
| Recursive Refinement | `Task(image-expert)` → evaluate → `Task(image-expert)` with feedback → ... |
| Monitor-React | `Task(system-configuration-agent)` to create cron → cron triggers → `Task(research-specialist)` → `Task(action-coordinator)` |
