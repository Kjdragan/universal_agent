# Bowser Integration: Strategic Capability Expansion (2026-02-16)

## 1) Executive Summary

We installed Bowser's browser-automation stack into this repository's `.claude` surface area (agents, skills, commands). This is not just "more tools." It adds a **new execution layer** for the Universal Agent:

- **Interactive web execution** (not only API/tool abstraction)
- **Observable + authenticated browser tasks** (Claude Chrome mode)
- **Parallel browser validation pipelines** (Playwright sessions + QA fan-out)
- **Reusable orchestration patterns** (command + workflow composition)

In practical terms, this materially expands UA from "research/report/delivery" into:

1. **Digital operator** (performs user-like browser tasks)
2. **Agentic QA system** (parallel story validation with artifact trails)
3. **Composable browser-workflow orchestrator** (higher-order prompts + reusable workflows)

Bowser's core architectural value is its layered pattern: **Skill -> Subagent -> Command -> Reusability**, which is directly compatible with UA's multi-agent coordinator model.

---

## 2) What Was Installed (Concrete Inventory)

### Agents (Subagent layer)
- `bowser-qa-agent` @.claude/agents/bowser-qa-agent.md#1-120
- `claude-bowser-agent` @.claude/agents/claude-bowser-agent.md#1-20
- `playwright-bowser-agent` @.claude/agents/playwright-bowser-agent.md#1-20

### Skills (Capability layer)
- `claude-bowser` @.claude/skills/claude-bowser/SKILL.md#1-29
- `playwright-bowser` @.claude/skills/playwright-bowser/SKILL.md#1-115
- `just` @.claude/skills/just/SKILL.md#1-127

### Commands (Orchestration layer)
- `ui-review` @.claude/commands/ui-review.md#1-156
- `build` @.claude/commands/build.md#1-22
- `list-tools` @.claude/commands/list-tools.md#1-41
- `prime` @.claude/commands/prime.md#1-15
- `bowser/hop-automate` @.claude/commands/bowser/hop-automate.md#1-60
- `bowser/amazon-add-to-cart` @.claude/commands/bowser/amazon-add-to-cart.md#1-31
- `bowser/blog-summarizer` @.claude/commands/bowser/blog-summarizer.md#1-28

---

## 3) Strategic Intent of Bowser (from README)

Bowser explicitly frames itself as a **four-layer composable architecture**: skill, subagent, command, justfile @/home/kjdragan/repos/bowser/README.md#15-26 and @/home/kjdragan/repos/bowser/README.md#17-23.

This is strategically aligned with UA's coordinator pattern because it introduces:

1. **Separation of concerns per layer** (capability vs scale vs orchestration vs run UX)
2. **Independent testability** of each layer @/home/kjdragan/repos/bowser/README.md#168-175
3. **Parallelizable workflow execution** via one-agent-per-story fan-out @/home/kjdragan/repos/bowser/README.md#170-174
4. **Dual browser strategy** for different job classes:
   - Real session + identity (`claude-bowser`) @/home/kjdragan/repos/bowser/README.md#194-219
   - Isolated scalable automation (`playwright-bowser`) @/home/kjdragan/repos/bowser/README.md#220-251

---

## 4) Capability Delta for Universal Agent

## Before (dominant pattern)
UA frequently converges to a predictable loop:
- gather information
- synthesize output
- generate assets
- deliver via Slack/email

This is powerful, but mostly **knowledge transformation + delivery**.

## After (new pattern)
UA can now execute **browser-native operational workflows** as first-class mission steps:

1. **Authentic user-level interaction**
   - click/type/navigate/checkout-flow execution
   - handling dynamic JS-heavy pages
2. **Agentic UI acceptance testing**
   - structured pass/fail reports
   - screenshot trail per step
3. **Reusable browser workflow libraries**
   - orchestrate via `/bowser:hop-automate`
   - parameterize tasks across many targets
4. **Parallel validation scale-out**
   - one subagent per story, aggregate outcomes (`/ui-review`)

This turns UA into a hybrid:
- **Planner/Coordinator** (existing strength)
- **Digital browser operator** (new strength)
- **UI verification swarm** (new strength)

---

## 5) Role of Each Bowser Asset in UA Terms

## 5.1 `claude-bowser` + `claude-bowser-agent`
Use when task needs **your current logged-in browser identity**.

- Uses Chrome MCP tools and real profile/cookies/extensions @.claude/skills/claude-bowser/SKILL.md#10-10
- Requires pre-flight for `mcp__claude_in_chrome__*` @.claude/skills/claude-bowser/SKILL.md#12-17
- Single-instance only (no parallel) @.claude/skills/claude-bowser/SKILL.md#25-28

Best for:
- Personal ops (Amazon/Gmail/internal dashboards)
- Workflows where existing auth state is the main value

## 5.2 `playwright-bowser` + `playwright-bowser-agent`
Use when task needs **scale, repeatability, and isolation**.

- Headless by default, named sessions, persistent profiles @.claude/skills/playwright-bowser/SKILL.md#15-23
- Explicit session lifecycle management and mandatory close @.claude/skills/playwright-bowser/SKILL.md#90-93

Best for:
- QA/regression flows
- Repeatable scraping/validation
- Multi-target browser tasks in parallel

## 5.3 `bowser-qa-agent`
This is the **opinionated validation specialist**.

- Parses stories, executes stepwise, screenshots each step, returns pass/fail report @.claude/agents/bowser-qa-agent.md#14-35
- Includes failure triage with console errors @.claude/agents/bowser-qa-agent.md#31-75

Best for:
- Acceptance testing
- release gates
- demo verifiability/auditability

## 5.4 `hop-automate` command family
Acts as a **higher-order workflow router**:
- resolves workflow + skill + mode + vision
- loads workflow prompt template
- executes selected browser skill @.claude/commands/bowser/hop-automate.md#31-59

This is strategically important because it gives UA a reusable API-style contract for browser automation.

---

## 6) High-Leverage Multi-Agent Chains Enabled Now

These are examples of what UA can now do beyond "report + email":

1. **Autonomous Procurement Preparation (Human-in-the-loop final approval)**
   - `action-coordinator` plans item set
   - `claude-bowser-agent` executes cart prep and stops at checkout
   - `data-analyst` compares prices/specs
   - `slack-expert` delivers shortlist + links + screenshots

2. **UI Release Gate Pipeline**
   - `code-writer` ships feature branch
   - `/ui-review` fans out QA stories
   - `bowser-qa-agent` produces evidence trail
   - coordinator decides release readiness + files issue summaries

3. **Web Ops + Reporting Fusion**
   - `research-specialist` generates hypotheses
   - `playwright-bowser-agent` collects structured page evidence/screenshots
   - `report-writer` creates artifact
   - `action-coordinator` sends multichannel briefing

4. **Continuous Experience Monitoring**
   - schedule run
   - run predefined Bowser workflows
   - detect diffs/failures
   - alert to Slack/Telegram with screenshot evidence and impact notes

---

## 7) Operational Guardrails (Critical)

1. **Mode selection is mandatory**
   - Need existing login/profile/extensions -> `claude-bowser`
   - Need scale/repeatability/parallel -> `playwright-bowser`

2. **Safety for high-risk workflows**
   - For purchase/payment workflows, keep "stop before final submit" patterns (as in amazon workflow) @.claude/commands/bowser/amazon-add-to-cart.md#29-30

3. **Session hygiene**
   - For Playwright, always close named sessions @.claude/skills/playwright-bowser/SKILL.md#90-93

4. **Concurrency reality**
   - Chrome path is single-instance only @.claude/skills/claude-bowser/SKILL.md#27-28
   - Fan-out workloads should route to Playwright-based agents/commands

5. **Traceability by default**
   - QA flows should preserve screenshots and structured reports for audit and debugging

---

## 8) Capabilities.md Registration Status (What is auto-recognized)

From runtime generation logic @src/universal_agent/agent_setup.py#552-731 and skill discovery logic @src/universal_agent/prompt_assets.py#124-221:

- **Agents** in `.claude/agents/*.md` are auto-discovered and included in generated `capabilities.md`.
- **Skills** in `.claude/skills/**/SKILL.md` are auto-discovered and included in generated `capabilities.md` + skills section of system prompt.
- **Commands** in `.claude/commands/` are available to Claude command surface, but are **not currently enumerated** by the capabilities generator.

Implication: Bowser agents/skills are fully represented in generated capabilities; command awareness must come from operational knowledge guidance (see Section 9 and `.claude/knowledge/bowser_capability_expansion.md`).

---

## 9) Prompt Injection Strategy (Beyond Capabilities.md)

To push broader capability reasoning (not just listing assets), we add a dedicated knowledge injection document:

- `.claude/knowledge/bowser_capability_expansion.md`

This gives the coordinator explicit reasoning heuristics:
- when to choose browser-native execution vs API/tool-only approach
- when to route to each Bowser agent/skill
- how to compose browser ops with existing research/analysis/delivery specialists
- what "amazing showcase" should include (interaction + validation + orchestration), not only report generation

---

## 10) Recommended Working Mental Model for UA

Treat Bowser as a **Browser Operations Subsystem** inside UA:

- **Capability Layer**: browser primitives (Playwright/Chrome)
- **Scale Layer**: parallel subagents for workload distribution
- **Orchestration Layer**: reusable command workflows (HOP + UI review)
- **Reusability Layer**: one-liner invocation patterns (just-style semantics)

This is a multiplier for the existing UA architecture, not a side feature.

If we use it correctly, UA transitions from "informational assistant with tool integrations" to an **execution-grade agent team that can act, verify, and coordinate in the live web environment**.
