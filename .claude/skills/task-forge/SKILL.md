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

Create the task-skill in the workspace:

```
task-skills/<task-name>/
├── SKILL.md              ← The task-skill (REQUIRED)
├── scripts/              ← Only if you found deterministic utilities needed
└── references/           ← Only if domain docs would help the executing agent
```

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

#### When to add scripts/

Add a `scripts/` directory only when:
- There's a deterministic, fragile operation (file format manipulation, API calls with exact params)
- You found that agents repeatedly write the same boilerplate code for this type of task
- The task involves a non-obvious command sequence that's easy to get wrong

Scripts should be **self-contained and runnable**. Include usage instructions in the SKILL.md.

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

### Phase 6: Archive or Promote

After successful execution:

- **One-off task?** Archive to `task-skills/archive/<task-name>/` for future reference
- **Might recur?** Leave in `task-skills/` for easy re-use
- **Definitely recurring?** Promote to `.claude/skills/` as a permanent system skill:
  1. Move the skill directory to `.claude/skills/<task-name>/`
  2. Optimize the description for triggering (follow skill-creator guidance)
  3. Symlink from `.agents/skills/` if needed for UA discovery
  4. Update capabilities registry

---

## NEVER Do

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
  things done NOW with the option to refine later.

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
Check success criteria
    ↓
Pass? → Archive/promote    Fail? → Add anti-pattern → retry (max 3x)
```

Total time from "do X" to first execution attempt: **~20 minutes.**

That's the promise of Task Forge: any non-trivial task, structured and executing,
in under 20 minutes. The skill format isn't overhead — it's the fastest path
to both getting it done and getting it done *well*.
