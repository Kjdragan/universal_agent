# Bowser Capability Expansion: Browser Operations Doctrine

> [!IMPORTANT]
> Treat Bowser as a **capability multiplier**, not a standalone skill pack.
> It upgrades the system from "analyze + report" into "plan + act + verify" in real web environments.

## Mission-Level Routing Rule

When a user request involves websites, UI behavior, account-state interactions, checkout/form workflows, or proof via screenshots,
**actively consider Bowser routing** before defaulting to research-only flows.

Do not reduce browser tasks to text summaries if direct browser execution would produce stronger evidence or outcomes.

---

## What Bowser Adds to Universal Agent

1. **Interactive browser execution** (perform actions, not only describe actions)
2. **Dual execution modes**:
   - **`claude-bowser`** for real Chrome identity/session (logged-in workflows)
   - **`playwright-bowser`** for isolated, repeatable, parallel automation
3. **Parallel QA validation** via `bowser-qa-agent`
4. **Reusable browser orchestration** via `/bowser:hop-automate` and `/ui-review`
5. **Evidence-first operations** with screenshot trails and step-level pass/fail outputs

---

## Mode Selection Heuristic (Mandatory)

## Choose `claude-bowser` / `claude-bowser-agent` when:
- Existing login/session/cookies/extensions are required
- Work is personal/interactive/observable
- Example classes: account checks, authenticated purchasing flows, dashboard actions

Constraint: single active Chrome controller; do not parallelize this lane.

## Choose `playwright-bowser` / `playwright-bowser-agent` when:
- Task needs reproducibility, isolation, and/or scale
- You need N concurrent browser runs
- You need deterministic QA-style evidence collection

Constraint: close named sessions when done.

## Choose `bowser-qa-agent` when:
- User asks for validation, acceptance testing, regression checks, or "prove this UI works"
- Need step-by-step pass/fail output and screenshots for auditability

---

## Command Awareness (Operational Surface)

Available Bowser orchestration commands include:
- `/ui-review`
- `/bowser:hop-automate`
- `/bowser:amazon-add-to-cart`
- `/bowser:blog-summarizer`

Interpret these as **workflow APIs**:
- `hop-automate` = higher-order router (workflow + skill + mode + vision)
- `ui-review` = fan-out/fan-in QA orchestrator
- leaf workflow commands = reusable domain procedures

---

## "Do Something Amazing" Expansion Pattern

If user asks for a showcase, do not stop at research/report artifacts.
Compose multi-phase execution:

1. **Plan** (what to test/operate and why)
2. **Act in browser** (Bowser lane)
3. **Collect evidence** (screenshots, step results, console/network if needed)
4. **Analyze** (compare outcomes, detect risks, summarize confidence)
5. **Deliver** (Slack/email/Notion/etc. with proof links)

High-impact examples:
- Parallel UI story validation + defect digest + team notification
- Authenticated web operation + verification screenshots + next-action recommendations
- Competitive web checks + structured extraction + decision memo

---

## Safety & Risk Controls

- For purchase/payment workflows, stop before irreversible submission unless user explicitly requests completion.
- Prefer Playwright for scale and repeatability; reserve real Chrome for identity-dependent tasks.
- Never claim validation without execution evidence.
- Report uncertainty explicitly when a workflow cannot be fully verified.

---

## Strategic Reminder

Bowser is not merely "web scraping" and not merely "UI testing."
It is a composable browser execution stack that enables **agentic operations**:

- capability (skills)
- scale (subagents)
- orchestration (commands)
- repeatability (just-style entrypoints)

Use it to expand from static outputs into verified, real-world task completion.
