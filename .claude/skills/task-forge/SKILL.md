---
name: task-forge
description: >
  Build a structured task-skill to accomplish any goal, then execute and iterate until done.
  USE THIS SKILL when the user says "task forge this", "forge a skill for X", "build me a skill
  to do X", "figure out how to do X", "don't plan, just try it", "let the agent handle it",
  or any time they want to tackle a non-trivial task by letting the agent explore and discover
  the approach rather than pre-engineering a detailed plan. Also use when converting a vague
  idea into an actionable, executable skill package, or when the user wants to create a skill
  for a one-off task. Triggers on: "task forge", "forge", "build a skill", "create a skill for",
  "skill for this task", "package this as a skill", "make a skill", "one-off skill".
---

# Task Forge

**A skill for building skills to get things done.**

Task Forge takes a raw task description and packages it into a structured skill that you
(or another agent) can execute effectively. The skill format is the optimal packaging for
any non-trivial task — not because you'll reuse it, but because it gives you intent clarity,
a place for learnings, eval capability, and composability.

## The Philosophy (Read This First)

> **Don't over-plan. Don't engineer a happy path you haven't walked.**
> **Let the agent explore. Encode what works. Iterate if it matters.**

A task-skill can be 15 lines. That's fine. The agent fills the gaps with its own intelligence.
The skill just points it in the right direction. You are not building a permanent, polished
skill — you are packaging a task into a format that maximizes the chance of success.

**Why this works better than a detailed plan:**
- Plans are guesses. Execution is discovery. The agent sees the codebase, the tools, the context — often more than the human who wrote the plan.
- A plan constrains the solution space. A task-skill defines the *destination* and lets the agent find the *route*.
- If the first attempt fails, the skill format gives you a place to encode the failure as an anti-pattern, making the next attempt smarter.

**The maturity model** — skills earn optimization through use, not upfront design:

| Version | Name | What It Contains | Investment |
|---------|------|-----------------|------------|
| v0 | Intent | Goal + success criteria + constraints | 5 minutes |
| v1 | Observation | + Extracted scripts + noted anti-patterns from runs | 30 minutes |
| v2 | Framework | + Decision trees + thinking frameworks | 1-2 hours |
| v3 | Pipeline | + Deterministic scripts + full references | Half day |

**For one-off tasks, stop at v0.** For recurring tasks, let the skill mature naturally.

---

## Process

### Phase 1: Capture Intent (5 minutes)

Extract from the user — or infer from context — these four things:

1. **The Goal** — What needs to happen? Describe the outcome, not the process.
2. **Success Criteria** — What does "done" look like? Be concrete and verifiable.
3. **Hard Constraints** — What must NOT happen? (data loss, breaking production, cost limits)
4. **Context Pointers** — Relevant files, systems, or dependencies (if known).

If the user is vague, that's OK. Ask **at most 2-3 clarifying questions**. You can discover
context during execution. Don't interview them to death — the whole point is speed.

> **Anti-pattern: The Over-Interview.** If you find yourself asking more than 3 questions,
> stop. You have enough to build a v0. The agent executing the skill will figure out the rest.

### Phase 2: Quick Context Scan (10 minutes max)

Before scaffolding, do a fast reconnaissance — not deep research:

- **Grep the codebase** for files related to the task (find the landmines, not the blueprints)
- **Check existing skills** — is there already a skill that does part of this? Can you compose?
- **Scan relevant docs** — any READMEs or architecture docs that provide domain knowledge?

You're looking for things that would cause the executing agent to fail. Don't go deep.

> **Anti-pattern: The Research Rabbit Hole.** If your context scan takes more than 10 minutes,
> you're doing too much. Ship the v0 and let the executing agent discover what it needs.

### Phase 3: Scaffold the Task-Skill

> [!IMPORTANT]
> **The skill IS the output.** You are not just producing a result (a table, a report). You are
> producing a **reusable skill** that produces that result. Both the skill AND the result matter.
> Think of it as: you're building a factory, not hand-crafting a single product. Skipping this
> phase to run inline code is explicitly prohibited — it defeats the entire purpose of Task Forge.

Create the task-skill in the workspace:

```
task-skills/<task-name>-tf/
├── SKILL.md              ← The task-skill (REQUIRED — this is the PRIMARY output)
├── scripts/              ← Only if you found deterministic utilities needed
└── references/           ← Only if domain docs would help the executing agent
```

> [!IMPORTANT]
> **Naming convention:** Always append `-tf` to the skill directory name (e.g., `run-profiler-tf`,
> `health-check-tf`). This suffix marks the skill as Task Forge generated, distinguishing it
> from hand-crafted skills. The `-tf` suffix carries through to promotion — when a skill is
> promoted to `.claude/skills/`, it keeps its `-tf` suffix.

**Where to create task-skills:**
- Default: `task-skills/` directory at the project root
- If the user specifies a workspace or session directory, use that instead
- For UA system tasks: within the relevant session workspace

#### Writing the SKILL.md

Use the template in `templates/v0.md` as your starting point. The key sections:

```markdown
---
name: <task-name>
description: <one-line description of what this task-skill does>
---

# <Task Title>

## Goal
<What needs to happen — the outcome, not the process>

## Success Criteria
<Concrete, verifiable conditions that mean "done">
- Criterion 1
- Criterion 2
- ...

## Constraints
<Hard limits — things that must NOT happen>
- Constraint 1
- ...

## Context
<Relevant files, systems, pointers — anything the executing agent needs to know>
- Key file: `path/to/relevant/file.py`
- Related system: <description>
- ...

## Anti-Patterns (if known)
<Things the executing agent should avoid — learned from prior attempts or your context scan>
- Don't do X because Y
- ...
```

**That's a complete v0 task-skill.** Don't over-engineer it. If you're writing more than
50 lines for a v0, you're probably over-planning.

#### What makes a good skill vs. a bad one

A bare Python script is NOT a skill. Scripts are inflexible, opaque to agents, and can't be
composed, iterated, or evolved. The SKILL.md is what drives the agent — it describes the *what*
and *why*. Scripts are optional tools that assist with the *how*.

A good task-skill can be handed to a different agent in a different session and still produce a
useful result, because the intent, approach, and success criteria are captured in the SKILL.md.
A bare script can only be re-run identically — it can't adapt, compose, or evolve.

#### When to add scripts/

Add a `scripts/` directory only when:
- There's a deterministic, fragile operation (file format manipulation, API calls with exact params)
- You found that agents repeatedly write the same boilerplate code for this type of task
- The task involves a non-obvious command sequence that's easy to get wrong

Scripts should be **self-contained and runnable**. Include usage instructions in the SKILL.md.
The script is a tool inside the skill, not a replacement for the skill.

#### When to add references/

Add a `references/` directory only when:
- There's domain knowledge the executing agent genuinely wouldn't know
- You found API docs, schema definitions, or architecture notes during your context scan
- The reference is **short** (<300 lines) — otherwise link to the source instead

### Phase 4: Execute or Dispatch

After scaffolding, decide how to execute:

| Situation | Action |
|-----------|--------|
| Simple task, you can do it yourself | Execute the skill directly — read it and follow it |
| Code/build task needing external workspace | Dispatch to Cody VP via `vp_dispatch_mission` with skill path |
| Research/analysis task | Dispatch to Atlas VP or handle directly |
| User wants to run it themselves | Report the skill location and let them trigger it |

When dispatching to a VP, include the skill path in the mission objective:
```
Read and execute the task-skill at task-skills/<task-name>/SKILL.md
```

### Phase 5: Evaluate and Iterate (Optional)

After execution, check the result against the success criteria from the SKILL.md.

**If all criteria met:** Report success. Offer to archive or promote the skill.

**If some criteria failed:**
1. Identify what went wrong
2. Add the failure as an anti-pattern in the SKILL.md
3. If a script or reference would have prevented the failure, add it
4. Re-execute (max 3 iterations before escalating to the user)

> **The key insight:** Each failure makes the skill smarter. You're not just retrying —
> you're encoding knowledge into the skill so the next attempt is structurally better.

### Phase 5b: Skill Quality Gate

After the result passes Phase 5, evaluate **the skill itself** — not its output.
The result might be correct, but the skill that produced it might be garbage (a thin .md
wrapper around a hardcoded script). A good result from a bad skill is a one-time win;
a good result from a good skill is a reusable capability.

> [!IMPORTANT]
> **The quality gate must produce a traceable artifact.** You cannot just claim "passed
> quality gate" in your completion note. You must actually perform the audit and write the
> results to `task-skills/<task-name>/quality_gate.md`. This file IS the proof.

#### Step 1: Read the skill-creator writing guide

You MUST `Read` the file `.claude/skills/skill-creator/SKILL.md` and review the "Skill Writing
Guide" section. This is not optional — it's the standard you're auditing against. Without reading
it, you're guessing at quality, not measuring it.

#### Step 2: Evaluate each check

| Check | What to look for | Fail if... |
|-------|-----------------|------------|
| **Structure** | SKILL.md has frontmatter (name, description), Goal, Success Criteria, Context | Missing any of these core sections |
| **Not a script wrapper** | The SKILL.md describes *what* and *why*, not just "run this script" | The entire SKILL.md is basically "run scripts/do_thing.py" |
| **Composable** | References existing skills where relevant, uses context pointers | Reinvents capabilities that exist as skills |
| **Generalizable** | Could a different agent in a different session follow this and succeed? | Only works because of hardcoded paths or session-specific knowledge |
| **Progressive disclosure** | SKILL.md is lean (<100 lines); heavy content in references/ | Everything crammed into one massive file |
| **Functional accuracy** | If the skill has a scanner/script, run it and verify results against known-good data | Scanner passes structural checks but produces false positives/negatives |

#### Step 3: Write the quality gate artifact

Use the `Write` tool to save `task-skills/<task-name>/quality_gate.md` with this format:

```markdown
# Quality Gate: <task-name>
Date: <date>

## Structural Checklist
- [x] Structure: <1-line justification>
- [x] Not a script wrapper: <1-line justification>
- [ ] Composable: <1-line explanation of gap>
- [x] Generalizable: <1-line justification>
- [x] Progressive disclosure: <1-line justification>

## Improvements Made
- <what you fixed in the skill structure, if anything>

## Development Context
<!-- This is where you capture what you LEARNED while building this skill.
     Future agents running or iterating on this skill inherit this knowledge. -->

### What Was Discovered
- <file locations, directory structures, edge cases found during execution>
- <tools that worked well vs. tools that caused friction>
- <data format quirks, parsing issues, API behaviors>

### Environment & Dependencies
- <system state that matters: python version, available CLIs, path conventions>
- <hook interactions: any Bash denials, redirects, or rewrites encountered>

### What Worked / What Didn't
- <approaches that succeeded on first try vs. required iteration>
- <anti-patterns encountered during development>

## Process Patterns for Future Skill-Building
<!-- These are GENERALIZABLE lessons — patterns that apply not just to THIS skill
     but to building skills of this TYPE. This is the recursive learning loop:
     each skill you build teaches you to build better skills. -->
- <repeatable patterns discovered>
- <things every skill of this type should know>
- <common pitfalls for this category of task>

## Meta-Improvements
<!-- CRITICAL: This is the autonomous recursive learning loop.
     Review your Process Patterns and Development Context above.
     Ask yourself: do any of these observations improve the PIPELINE ITSELF?
     If so, document them here AND append to task-skills/_meta/improvement_log.md -->

### Pipeline-Level Observations
- <observations that would improve Task Forge for ALL future skills, not just this one>
- <new universal patterns discovered> (e.g., "always check for X before Y")
- <friction points in the Task Forge process itself>

### Proposed Changes
- <specific improvement to Task Forge SKILL.md, quality_gate template, or dispatch prompt>
- <which Phase would benefit, and what the change would look like>

(If no pipeline-level improvements were identified, write "None identified this run."
That's fine — not every run produces meta-insights. But you MUST ask the question.)
```

This artifact serves a quadruple purpose:
1. **Proof of audit** — proves the quality gate actually ran, not just self-certified
2. **Skill-specific memory** — what was discovered about THIS task (edge cases, file
   locations, environment quirks) travels with the skill for future runs
3. **Meta-skill learning** — process patterns that apply to building ANY skill of this
   type feed back into the skill-building practice itself
4. **Pipeline evolution** — the Meta-Improvements section is how the pipeline improves
   itself. Observations that would benefit ALL future skills get escalated here and
   accumulated in `task-skills/_meta/improvement_log.md` for periodic incorporation
   into the Task Forge SKILL.md itself. This closes the recursive learning loop:
   **the pipeline that builds skills also builds itself.**

#### Step 4: Escalate meta-improvements

After writing the quality_gate.md, check the Meta-Improvements section you just wrote.
If you identified pipeline-level improvements (not "None identified"), append them to
`task-skills/_meta/improvement_log.md` using this format:

```markdown
## <date> — from <task-name>
- **Observation:** <what you noticed>
- **Proposed change:** <specific edit to Task Forge SKILL.md or dispatch prompt>
- **Which Phase:** <Phase N>
- **Status:** proposed
```

This file accumulates across runs. It is the institutional memory of the pipeline itself.
Periodically, these proposals get reviewed and merged into the Task Forge SKILL.md,
completing the loop: observations → proposals → codified improvements → better runs.

> [!IMPORTANT]
> You are not just building a skill. You are building the system that builds skills.
> Every run is an opportunity to make the next run better. The Meta-Improvements section
> is how that happens without requiring a human to notice and intervene.

#### If checks fail

1. Identify the structural weakness
2. Improve the SKILL.md (refactor, add missing sections, extract hardcoded values)
3. Update the quality_gate.md with what you fixed under "Improvements Made"
4. This is a **one-time quality improvement pass** — don't re-execute the whole task,
   just fix the skill's structure so it's reusable

> **Why this matters:** The whole point of Task Forge is that the skill is the output.
> A structurally sound skill compounds in value — it can be rerun, composed, evolved.
> A structurally weak skill is just a script execution with extra steps. The quality gate
> ensures we're actually building the institutional knowledge flywheel, not just running code.
> The audit artifact itself becomes part of the skill's DNA — future developers and agents
> can read it to understand what worked, what didn't, and what to watch for.

### Phase 5c: Skill Improvement Pass (OPTIONAL — user-requested only)

> [!NOTE]
> This phase is **not automatic**. It runs only when the user's task description includes
> phrases like "and evaluate", "polish the skill", "improve it", "run skill-creator eval",
> or "make it production-quality". If not requested, skip to Phase 6.

When triggered, apply the skill-creator's quality standards for a single improvement pass.
This is the bridge between a v0 task-skill and a polished v1+.

#### Step 1: Re-read the standard

Read `.claude/skills/skill-creator/SKILL.md` — specifically the "Skill Writing Guide"
and "Writing Patterns" sections. These are the polish standards you're measuring against.

#### Step 2: Self-evaluate against skill-creator standards

- Is the description "pushy" enough for reliable triggering?
- Does the SKILL.md follow progressive disclosure (metadata → body → bundled resources)?
- Are there hardcoded values that should be parameterized?
- Could the skill benefit from a `references/` file for domain context?

#### Step 3: Apply universal improvement patterns

These patterns were learned from observing real Task Forge runs. Apply each one
as a checklist — they catch the most common v0 weaknesses:

| Pattern | What to check | Why it matters |
|---------|--------------|----------------|
| **Preserve ephemeral code** | Did you write scripts during Phase 4 execution (Python extraction, bash pipelines) that lived only in the tool-call stream? Save reusable logic to `scripts/`. | Ephemeral code dies with the session. Scripts in `scripts/` survive and make the skill re-executable without re-discovery. |
| **Specify reproducible methodology** | Does the Approach section define the methodology precisely enough that a different agent in a different session would produce *consistent* results? | Vague approaches like "scan the directory" produce different outputs each run. Explicit methodology (what to count, how to deduplicate, what constitutes a match) ensures reproducibility. |
| **Tighten scope definitions** | Are key terms defined unambiguously? (e.g., "what counts as a skill?", "what's a duplicate?", "what's the boundary of the dataset?") | Undefined scope leads to count discrepancies, missed items, or over-counting across runs. |
| **Track skill maturity** | Note the version (v0 → v1) when improvements are made. The quality_gate.md should record what changed between versions. | Version history makes the maturity model concrete, not just aspirational. Future agents can see what was tried and what worked. |
| **Externalize domain knowledge** | Is there domain knowledge embedded in the SKILL.md body that should be in `references/`? (e.g., category taxonomies, threshold values, API schemas) | Domain knowledge in `references/` can be updated independently of the skill logic. It also keeps the SKILL.md lean (<100 lines). |

#### Step 4: Make concrete improvements

Edit `task-skills/<task-name>/SKILL.md` with the improvements identified above:
- Sharpen the description for better trigger matching
- Save any reusable scripts to `scripts/`
- Add `references/` for domain knowledge
- Tighten scope definitions and methodology
- Ensure frontmatter description follows the "pushy" pattern from skill-creator

#### Step 5: Document the improvement

Append a `## Phase 5c: Improvement Pass` section to `quality_gate.md`:
- What was improved and why (cite which universal pattern applied)
- Before/after of any description or structural changes
- Version label (v0 → v1)
- Whether the skill is now ready for promotion to `.claude/skills/`

> **When to skip:** If the task is clearly one-off and the user didn't ask for polish,
> skip this phase entirely. Task Forge's default is "ship the v0, iterate if it matters."
> This phase exists for when the user explicitly wants to invest in quality upfront.

> [!NOTE]
> **Hook-denial workaround:** During Phase 5c, edit-protection hooks will block direct writes
> to `.claude/skills/`. This is intentional — production skills are protected during runs.
> Write your fixes to `task-skills/<task-name>-tf/` in your workspace instead, then Phase 6
> (auto-promote) will copy them over the existing promoted skill via `cp -r`.

### Phase 6: Auto-Promote (MANDATORY)

After the quality gate passes, **automatically promote the skill** to the permanent
skills directory so it's immediately discoverable for future runs.

```bash
# Copy the task-skill to the permanent skills directory
cp -r task-skills/<task-name>-tf/ .claude/skills/<task-name>-tf/
```

This is mandatory — every Task Forge skill gets promoted. The `-tf` suffix marks it as
machine-generated. Users can find and run it immediately without manual intervention.

> [!IMPORTANT]
> **Do not skip this step.** The whole point of Task Forge is to produce reusable skills.
> A skill that sits in a session workspace and never gets promoted is a one-time script
> with extra steps. Auto-promotion closes the loop: forge → execute → promote → reuse.

- **Worth polishing further?** Hand the promoted skill off to the skill-creator's full
  eval/iterate loop (`.claude/skills/skill-creator/SKILL.md`). Task Forge creates the v0;
  skill-creator polishes it to v2+.

---

## NEVER Do

- **NEVER skip Phase 3 (scaffolding).** Running inline code without creating a SKILL.md
  is the cardinal sin of Task Forge. The skill is the output, not just the result.
  Even for simple tasks, write the SKILL.md — it takes 5 minutes and creates a reusable asset.
- **NEVER produce a bare Python script as the deliverable.** A script is not a skill.
  If the task needs a script, it goes in `scripts/` inside the task-skill directory,
  referenced by the SKILL.md. The .md drives the process; the script assists it.
- **NEVER spend more than 20 minutes on Phase 1-3 combined.** The whole point is speed.
  A v0 that ships in 20 minutes beats a v2 that ships in 2 hours.
- **NEVER write a task-skill longer than 100 lines for a first attempt.** If you need more,
  put it in references/ — keep the SKILL.md lean and scannable.
- **NEVER pre-engineer scripts for a v0.** Let the executing agent write its own approach
  first. Only extract scripts in v1+ after you've observed what works.
- **NEVER iterate more than 3 times without human input.** If it's not converging, the
  problem is the task definition, not the execution — escalate.
- **NEVER confuse Task Forge with the skill-creator.** Skill-creator is for building
  polished, permanent skills with eval suites and benchmarks. Task Forge is for getting
  things done NOW with the option to refine later. However, Task Forge's Phase 5b (Skill
  Quality Gate) borrows the skill-creator's *structural standards* as a lightweight check.
  If a task-skill graduates to permanent status, hand it to the skill-creator for full polish.

---

## Composing with Existing Skills

Task-skills can reference existing permanent skills. If the task involves PDF creation,
image generation, web scraping, etc. — point the executing agent at the relevant skill:

```markdown
## Context
- For PDF generation, read and follow `.claude/skills/pdf/SKILL.md`
- For image creation, use `.claude/skills/image-generation/SKILL.md`
```

This is composability — task-skills don't need to reinvent capabilities that already exist
as polished skills. They just need to orchestrate them toward a specific goal.

---

## Quick Reference

```
User says "do X"
    ↓
Capture intent (5 min) → Context scan (10 min) → Scaffold SKILL.md (5 min)
    ↓
Execute directly or dispatch to VP
    ↓
Check success criteria (Phase 5)
    ↓
Pass? → Skill Quality Gate (Phase 5b) → Archive/promote/polish
Fail? → Add anti-pattern → retry (max 3x)
```

Total time from "do X" to first execution attempt: **~20 minutes.**
Skill Quality Gate adds ~5 minutes for a structural audit.

That's the promise of Task Forge: any non-trivial task, structured and executing,
in under 25 minutes — producing both a result AND a reusable skill.
The skill format isn't overhead — it's the fastest path
to both getting it done and getting it done *well*.
