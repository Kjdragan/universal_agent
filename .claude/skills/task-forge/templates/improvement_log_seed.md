# Task Forge: Pipeline Improvement Log

> This file accumulates meta-improvement proposals from individual Task Forge runs.
> Each entry was identified by the executing agent during the quality gate (Phase 5b)
> as an observation that would benefit ALL future skills, not just the one being built.
>
> **How it works:**
> 1. Agent builds a skill via Task Forge
> 2. During Phase 5b, the quality_gate.md template prompts for Meta-Improvements
> 3. If the agent identifies pipeline-level insights, they append them here
> 4. Periodically, a human or agent reviews this log and merges accepted proposals
>    into the Task Forge SKILL.md, closing the recursive learning loop
>
> **Status values:** `proposed` → `accepted` → `merged` | `declined`

---

## 2026-04-20 — from skill-inventory (Runs #4-5)
- **Observation:** Python extraction scripts written during Phase 4 died with the session
- **Proposed change:** Add "Preserve ephemeral code" to Phase 5c universal patterns
- **Which Phase:** Phase 5c, Step 3
- **Status:** merged (codified in commit acff224a)

## 2026-04-20 — from skill-inventory (Runs #4-5)
- **Observation:** Run #4 found 82 skills, Run #5 found 61 — different counting methodologies
- **Proposed change:** Add "Specify reproducible methodology" to Phase 5c universal patterns
- **Which Phase:** Phase 5c, Step 3
- **Status:** merged (codified in commit acff224a)

## 2026-04-20 — from skill-inventory (Runs #4-5)
- **Observation:** "What counts as a skill?" was ambiguous across runs
- **Proposed change:** Add "Tighten scope definitions" to Phase 5c universal patterns
- **Which Phase:** Phase 5c, Step 3
- **Status:** merged (codified in commit acff224a)

## 2026-04-20 — from skill-inventory (Runs #4-5)
- **Observation:** No version tracking between improvements to the same skill
- **Proposed change:** Add "Track skill maturity" to Phase 5c universal patterns
- **Which Phase:** Phase 5c, Step 3
- **Status:** merged (codified in commit acff224a)

## 2026-04-20 — from skill-inventory (Runs #4-5)
- **Observation:** Domain knowledge (category taxonomies) embedded inline instead of in references/
- **Proposed change:** Add "Externalize domain knowledge" to Phase 5c universal patterns
- **Which Phase:** Phase 5c, Step 3
- **Status:** merged (codified in commit acff224a)
