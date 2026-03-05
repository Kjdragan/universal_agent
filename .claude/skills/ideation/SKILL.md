---
name: ideation
description: >
  Launch multi-agent ideation to explore a concept through structured dialogue between a
  Free Thinker and a Grounder, arbitrated by a team lead (you), and documented by a Writer.
  USE THIS SKILL when the user wants to:
  - Explore an idea or brainstorm directions for a project, product, or problem
  - Develop a concept from a vague seed into concrete, actionable idea briefs
  - Get a multi-perspective creative exploration of a topic
  - Resume and build on a previous ideation session
  TRIGGER PHRASES: "ideate on this", "let's brainstorm", "explore the idea of",
  "develop this concept", "I want to think through", "help me think about X",
  "brainstorm directions for", "explore what this could be".
  Use "continue" mode to resume and build on a previous session.
argument-hint: "concept seed (file path or inline description). Use 'continue <path>' to resume a previous session."
user-invocable: true
---

# Ideation — Multi-Agent Concept Exploration

You are about to orchestrate a **multi-agent ideation session**. You are the **Arbiter** — you coordinate, evaluate, and signal convergence. You do NOT generate ideas yourself.

## Prerequisites

This skill requires **Agent Teams** (experimental, Claude Code + Opus 4.6).

```bash
echo $CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS
# If not set: claude config set env.CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS 1
# Then restart the Claude Code session.
```

---

## Session Modes

**New Mode (default):** The argument does NOT start with "continue" — proceed to Step 1.

**Continue Mode:** Argument starts with "continue" (e.g., `/ideation continue distributed-systems`).

1. Resolve the session directory: exact path → keyword search for `ideation-*<keyword>*` → ask user if ambiguous
2. Read key existing artifacts: `session/sources/manifest.md`, `session/VISION_<slug>.md`, `session/briefs/*.md`, `session/ideation-graph.md`
3. Skip source capture (already exists); skip directory creation (Step 2)
4. Still assess research needs — the user may have new questions
5. When spawning the team, tell each teammate: *"This is a continuation of a previous session. Build on the prior vision and briefs — don't start from scratch."*

---

## How This System Works

The system separates cognitive modes across distinct roles:

- **Free Thinker** — divergent generation, creative leaps, "what if..." exploration
- **Grounder** — editorial instincts, winnowing signal from noise, "keep going with that one"
- **Writer** — neutral synthesis, observation without perspective
- **Explorer** (conditional) — factual research only, "finding things out, not making things up"
- **Arbiter (you)** — coordination, evaluation, convergence signal

**Agent Teams vs. Subagents:** The distinction matters — teammates persist for the team lifetime and communicate peer-to-peer via `SendMessage`. Regular text output is NOT visible to teammates; they MUST use `SendMessage`.

---

## Step 1: Read Concept Seed and Capture Sources

Read the user's concept seed carefully — understand the intent behind it, not just the stated idea.

### Capture All Source Materials (`session/sources/`)

1. Save the user's request as `session/sources/request.md`. Copy any referenced file into `session/sources/`.
2. **Referenced documents** — copy (not link) all files into `session/sources/`.
3. **URLs** — fetch each with `WebFetch`, save as `session/sources/url_<domain>_<slug>.md`.
4. **Images** — copy any images into `session/sources/`.
5. **Manifest** — create `session/sources/manifest.md` listing every captured item (file, type, original location).

### Assess Research Needs

Decide one of three modes for the Explorer:

- **Pre-session**: Concept requires background research before thinkers can start. Spawn Explorer first, wait for its report, then spawn thinkers with the report as additional context.
- **Parallel**: Enough context for thinkers to start, but some URLs/domains need investigation. Spawn Explorer alongside thinkers.
- **No research needed**: Concept seed is self-contained. Skip Explorer (can be added later if mid-session research is requested).

---

## Step 2: Set Up Output Directories (skip in Continue Mode)

```bash
SESSION_DIR="ideation-$(echo '<concept-slug>' | tr ' ' '-' | tr '[:upper:]' '[:lower:]')-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$SESSION_DIR"/images "$SESSION_DIR"/session/sources "$SESSION_DIR"/session/research \
         "$SESSION_DIR"/session/idea-reports "$SESSION_DIR"/session/snapshots \
         "$SESSION_DIR"/session/briefs "$SESSION_DIR"/build
```

Output structure:

```
ideation-<slug>-<YYYYMMDD-HHMMSS>/
  index.html                      # Distribution page (primary browsing artifact)
  RESULTS_<concept>.pdf           # PDF of the distribution page
  CAPSULE_<concept>.pdf           # Comprehensive session archive
  PRESENTATION_<concept>.pptx     # Slide deck
  images/                         # Infographic images
  session/
    VISION_<concept>.md           # Consolidated vision (source of truth)
    SESSION_SUMMARY.md
    ideation-graph.md              # Writer's living graph
    sources/                       # All original input materials
    research/                      # Explorer's research reports
    briefs/                        # Final idea briefs
    idea-reports/                  # Raw idea reports from dialogue agents
    snapshots/                     # Writer's version snapshots
  build/
    build_capsule.py
    build_presentation.py
```

Store the resolved output path — all teammates need it in their spawn prompts.

---

## Step 3: Create the Team

Use `TeamCreate` with a descriptive team name (e.g., `ideation-<concept-slug>`).

---

## Step 4: Spawn Teammates

> **Read `references/teammate-prompts.md` now** for the full spawn prompts to copy into each `Task` call. Always replace `{session-output}` with the actual resolved path.

Spawn using the `Task` tool with the `team_name` parameter.

**Always spawn:**

1. **Free Thinker** — divergent generator
2. **Grounder** — editorial brainstorm partner
3. **Writer** — neutral observer and synthesizer

**Conditionally spawn (if Step 1 research assessment requires it):**
4. **Explorer** — factual research only

---

## Step 5: Create Initial Tasks

Use `TaskCreate` to create:

1. **"Read concept seed and begin ideation dialogue"** — Free Thinker broadcasts opening message, Grounder responds via broadcast
2. **"Initialize ideation graph and begin observation"** — Writer initializes `{session-output}/session/ideation-graph.md` from the template, monitors broadcasts
3. **"First idea report"** — blocked by task 1. After 2-3 directions explored, produce first report

**If Explorer is active:**
4. **"Research [topic]"** — Explorer investigates and broadcasts findings

- Pre-session mode: block task 1 on task 4 (thinkers wait for research)
- Parallel mode: no blocking

Do NOT create more than these initial tasks. Further tasks emerge organically.

### Mid-Session Research Requests

When the Free Thinker or Grounder sends you a research request:

1. Use `TaskCreate` to create a research task
2. Send a `message` to the Explorer pointing to the new task
3. Explorer broadcasts findings when done

---

## Step 6: Enter Delegate Mode

After setup, press Shift+Tab to enter delegate mode. Your tools in delegate mode: `SendMessage`, `TaskCreate`, `TaskUpdate`, `TaskList`, `Read`.

**Do not generate ideas. Do not write reports.** Wait for the first idea report.

---

## Your Arbiter Role

**Evaluate each idea report from dialogue agents and respond via `SendMessage`:**

| Verdict | When to use | Action |
|---------|-------------|--------|
| **"Needs more conversation"** | Promise but underdeveloped | Send back with specific guidance on what to explore |
| **"Interesting"** | Developed enough, genuine merit | Add to the interesting list. No further action. |
| **"Not interesting"** | Insufficient substance or novelty | Brief acknowledgment, move on |

**An idea is "Interesting" when it is:** compelling (a human would want to hear more), somewhat new (not an obvious rehash), a different take (not the first thing you'd think of), and substantive (the Grounder is genuinely excited).

**Convergence is emergent, not declared.** When the interesting list has sufficient density and range, you stop sending "needs more conversation" items back — that silence IS the convergence signal. Do not say "we're done."

---

## Communication Protocol

```
Free Thinker <──── broadcast ────> Grounder
     │                                  │
     │    (Writer + Explorer receive    │
     │     all broadcasts passively)   │
     └──── SendMessage(message) ───>  Arbiter (Team Lead)
                                         │
                     SendMessage to dialogue agents
                     "Needs more conversation" / "Interesting" / "Not interesting"
                                         │
                                    TaskCreate (research tasks for Explorer)
```

**SendMessage types used:**

| Type | When |
|------|------|
| `broadcast` | Dialogue exchanges between Free Thinker and Grounder |
| `message` | Idea reports to Arbiter, Arbiter feedback, Writer status |
| `task_completed` | Teammate finishes a task |
| `shutdown_request` | Arbiter requesting teammates to shut down |
| `shutdown_approved` | Teammate confirming ready to shut down |

---

## Convergence and Wrap-Up

When the dialogue agents sense convergence (Arbiter silence), they:

1. Review the "interesting" list together via `SendMessage`
2. Signal the Writer to begin final briefs

The Writer then produces (in order):

1. Final snapshot
2. One `BRIEF_<slug>.md` per "interesting" item (template: `templates/idea-brief.md`)
3. `session/SESSION_SUMMARY.md` (template: `templates/session-summary.md`)
4. `session/VISION_<concept-slug>.md` (template: `templates/vision-document.md`) — the **source of truth for production**
5. Sends `message` to Arbiter: **"Vision document complete"** with the file path

The vision document is the **most important deliverable** — it synthesizes all interesting ideas into a unified product vision, not separate feature descriptions. It must be rich enough that someone who knows the domain can build requirements from it, and someone who doesn't can understand what the product is trying to be.

---

## Production Phase

When you receive **"Vision document complete"** from the Writer:

> **Read `references/production-phase.md` now** for full spawn prompts for all four production agents and exact task dependency configuration.

1. Ensure `{session-output}/images/` and `{session-output}/build/` exist
2. Spawn four production teammates: **Image Agent**, **Presentation Agent**, **Web Page Agent** (blocked), **Archivist** (blocked)
3. Create four tasks with dependencies using `TaskCreate` + `TaskUpdate`

**Production dependency chain:** Image Agent + Presentation Agent (parallel) → Web Page Agent → Archivist

**Three distribution formats** (same content, different formats): HTML distribution page, PowerPoint presentation, Results PDF. A fourth **Session Capsule PDF** is a comprehensive layered archive of the entire session process.

---

## Cleanup

When the user confirms the session is done:

1. Send `shutdown_request` via `SendMessage` to all active teammates (ideation + production)
2. Wait for `shutdown_approved` from all teammates
3. Use `TeamDelete` to clean up team infrastructure

**Do NOT clean up until the user explicitly confirms they are done.** Convergence is the system's internal signal; the user may override it.

To start a completely new session, start a new Claude Code chat and invoke `/ideation` again. One team per chat.
