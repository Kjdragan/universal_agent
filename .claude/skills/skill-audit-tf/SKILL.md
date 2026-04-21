---
name: skill-audit-tf
description: >
  Audit the Universal Agent skill library for health, completeness, and hygiene.
  Scans all skill directories across .claude/skills/, .agents/skills/, and nested locations.
  Counts totals, identifies duplicates (excluding symlinks), flags missing SKILL.md files,
  skills without descriptions, oversized skills over 200 lines, orphaned directories,
  and stale legacy copies. Outputs a structured markdown health report with prioritized
  recommendations. Use this skill whenever you hear: "audit skills", "skill health report",
  "skill inventory", "how many skills do we have", "check the skill library", "skill quality",
  "skill cleanup", "skill hygiene", "are our skills healthy", "skill report card",
  or any time you need a systematic assessment of the skill library's condition.
  Also use proactively during maintenance windows or before major skill changes.
---

# Skill Library Audit

## Goal
Produce a comprehensive health report for the UA skill library across all storage locations. The report should be immediately actionable: each issue includes a specific fix recommendation, priority, and effort estimate.

## Success Criteria
- Total unique skill count with breakdown by location
- True duplicate detection (canonical copies in multiple locations, excluding symlinks)
- Complete list of skills missing SKILL.md
- Complete list of skills with empty/missing frontmatter descriptions
- All skills over 200 lines flagged with severity assessment
- Full symlink map with directionality
- Prioritized recommendation table (P0/P1/P2/P3)

## Constraints
- **Read-only audit** — never modify any skill files
- Symlinks are intentional cross-references, NOT duplicates
- The `skills/skills/` nested directory is a legacy ClawHub sync artifact
- Report output must be structured markdown tables (machine-parseable)
- Health score on a 1-10 scale for quick comparison across runs

## Context
- **Primary location:** `.claude/skills/` — the authoritative skill directory
- **Secondary location:** `.agents/skills/` — runtime access for agent processes; many entries are symlinks back to primary
- **Legacy nested:** `.claude/skills/skills/` — stale ClawHub sync copy from Feb 2026
- **Skill definition:** `SKILL.md` with YAML frontmatter (`name` and `description` required)
- **Size threshold:** 200 lines (adjustable via OVERSIZE_THRESHOLD env var)
- **Description validity:** frontmatter must have a non-empty string after `description:` or `description: >`

## Approach

### 1. Enumerate directories
For each of the 3 locations, list all subdirectories. Classify each as:
- **Canonical** — real directory with its own content
- **Symlink** — pointer to another location (record target)
- **Legacy** — entry in `.claude/skills/skills/` (stale copy)

### 2. Check SKILL.md presence
For every skill directory, verify SKILL.md exists. Flag absences. Distinguish between package containers (like `stitch-skills/` with sub-skills) and true orphans.

### 3. Parse frontmatter and measure
For each SKILL.md:
- Extract `description` from YAML frontmatter (handle `>` and `|` block scalars)
- Flag if description is missing, empty, or only contains block indicator
- Count total lines; flag if over threshold

### 4. Cross-reference for duplicates
Compare skill names across locations using `comm`:
- **True duplicate:** same name as canonical (non-symlink) in both `.claude/skills/` and `.agents/skills/`
- **Cross-reference:** symlink in one location pointing to canonical in another (healthy)
- **Stale copy:** exists in both `.claude/skills/` and `.claude/skills/skills/` with different line counts

### 5. Generate report
Output structured markdown:
- Executive summary table (counts, health score)
- Issue tables (one per issue type: missing SKILL.md, no description, oversized, duplicates, legacy)
- Full symlink map with directionality
- Prioritized recommendation table (P0-P3 with impact and effort)

## Anti-Patterns
- **Symlink != duplicate.** The symlink bridge between `.claude/skills/` and `.agents/skills/` is the intended architecture. Never flag symlinks as duplicates.
- **Don't count parent dirs.** The `skills/` directory itself, `stitch-skills/`, `nano-triple/` (package wrappers) are not individual skills.
- **Don't over-count nested.** The `skills/skills/` entries are stale copies, not additional skills. Count them separately as "legacy" items.
- **YAML block scalars need special handling.** Both `>` (folded) and `|` (literal) indicators have content on the NEXT indented line. The v1 awk parser captured only the indicator, causing false positives. The v2 bash regex parser reads the next line correctly.

## Scripts
- `scripts/audit_skills.sh` — Bash scanner that produces raw tab-delimited data for all checks. Run with `bash scripts/audit_skills.sh [project_root]`. Output is structured for easy parsing: `TYPE\tname\tfield1\tfield2`. v3 uses tab delimiter (avoids collision with YAML `|` literal scalar) and checks both `.claude/skills/` and `.agents/skills/` for description completeness.

## Version
- v1 (2026-04-21): Initial skill with polished methodology, references, and configurable thresholds
- v2 (2026-04-21): Fixed YAML frontmatter parser (awk → bash regex), eliminating 21 false-positive description flags
- v3 (2026-04-21): Switched to tab delimiter, added `.agents/skills/` description check, extracted description parser into reusable function
