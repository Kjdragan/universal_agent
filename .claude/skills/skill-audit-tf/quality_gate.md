# Quality Gate: skill-audit-tf
Date: 2026-04-21

## Structural Checklist
- [x] Structure: SKILL.md has frontmatter (name, description), Goal, Success Criteria, Constraints, Context, Approach, Anti-Patterns. All core sections present.
- [x] Not a script wrapper: The SKILL.md describes what to audit, why each check matters, and the methodology for each step. The script in `scripts/` is a data-gathering tool, not the skill itself.
- [x] Composable: References existing skill-creator standards for description quality checks. Uses standard filesystem tools (ls, find, wc, awk) that any agent can access.
- [x] Generalizable: A different agent in a different session could follow this skill and produce a comparable audit. The methodology defines what counts as a duplicate (excluding symlinks), what threshold constitutes "oversized" (200 lines), and what constitutes a missing description.
- [x] Progressive disclosure: SKILL.md is ~85 lines. The audit script lives in `scripts/` and handles the deterministic scanning logic. Heavy data goes into the report output, not the skill definition.

## Improvements Made
- v0→v1: Added pushy description, explicit methodology, scope definitions, version tracking
- v1→v2: Fixed YAML frontmatter parser bug in scanner script

## Development Context

### What Was Discovered
- The skill library spans 3 physical locations: `.claude/skills/` (primary), `.agents/skills/` (secondary), and `.claude/skills/skills/` (legacy stale copy).
- The two main locations use a symlink bridge architecture: 9 skills in `.claude/skills/` are symlinks to `.agents/skills/`, and 38+ skills in `.agents/skills/` are symlinks back to `.claude/skills/`. Zero true duplicates (except 3 with version drift).
- The nested `skills/skills/` directory is a ClawHub sync artifact from Feb 2026 that was never cleaned up. Contains 53 stale entries with older versions of existing skills.
- **v1 scanner bug:** The awk-based YAML frontmatter parser captured the block scalar indicator (`>` or `|`) instead of the actual content on the next line. This caused 21 false-positive "missing description" flags. All 90 canonical skills actually have valid descriptions.
- `nano-triple` is not a skill but a package wrapper — its content is in `skills.md` not `SKILL.md`.

### Environment & Dependencies
- Standard bash tools: `ls`, `find`, `wc -l`, `readlink -f`, `comm`, `sort`
- v2 uses bash regex instead of awk for frontmatter parsing (more reliable for multi-line YAML)
- No Python dependencies needed for the audit
- The scanner runs in ~2 seconds for 95 skills

### What Worked / What Didn't
- The `comm -12` approach for duplicate detection worked well but requires sorted input
- **v1 YAML parsing via awk failed** for multi-line descriptions using `>` (folded) or `|` (literal) block scalars. The awk `print; exit` pattern captured only the block indicator line.
- **v2 bash regex approach works** — reads the frontmatter line by line, detects block indicators, and reads the next indented line for actual content.
- Categorizing symlink directionality (which direction the symlink points) was critical for accurate duplicate analysis

## Process Patterns for Future Skill-Building
- When building audit/inventory skills, always distinguish between "canonical" and "referenced" entries (symlinks, aliases, redirects). Counting symlinks as separate entries inflates totals and creates false duplicates.
- Line-count thresholds should be configurable or at least documented in the SKILL.md so future runs can adjust without editing code.
- Report outputs should include both a summary table (for quick scanning) and a detailed table (for action items). The executive summary + detailed tables pattern works well.
- Always run the scanner on the actual live filesystem rather than relying on git status or cached data — the symlink landscape can change between runs.
- **YAML parsing is fragile in bash.** Use line-by-line bash regex instead of single-line awk for frontmatter extraction. The awk approach cannot handle multi-line YAML values.

## Meta-Improvements

### Pipeline-Level Observations
- Task Forge's Phase 4 (Execute) could benefit from an explicit "data collection" sub-phase for skills that produce reports. The pattern of: scan → parse → classify → report is generic enough to be a reusable workflow.
- The quality gate template should include a "Scanner Validation" check: does the scanner produce accurate results when tested against known-good data? The v1 scanner passed all structural checks but had a functional bug that produced 21 false positives.

### Proposed Changes
- **Phase 4 sub-step:** Add an optional "data collection" pattern to Task Forge SKILL.md for skills that need to gather information before synthesizing. Current guidance jumps straight to "execute or dispatch" which is fine for code tasks but vague for analytical tasks.
- **Quality gate addition:** Add a "Functional Accuracy" check after the structural checks. Run the scanner against a small known-good dataset and verify zero false positives.
- **Which Phase:** Phase 5b
- **Status:** proposed

---
## Phase 5c: Improvement Pass

**Version:** v1 → v2
**Date:** 2026-04-21

### Improvements Applied

| Universal Pattern | What Changed | Why |
|-------------------|-------------|-----|
| **Preserve ephemeral code** | Replaced awk-based YAML parser with bash regex in `scripts/audit_skills.sh`. The awk parser captured block scalar indicators instead of content. New parser reads line-by-line, detects `>` and `|` indicators, and reads next indented line for actual content. | The v1 scanner produced 21 false-positive "missing description" flags. The fix reduced real description issues from 21 to 0, improving health score from 6/10 to 7/10. |
| **Specify reproducible methodology** | Added "v2 changelog" header to scanner script documenting the YAML parser fix and behavior change. | Future agents running the scanner need to know v1→v2 behavior differences to avoid regression. |
| **Tighten scope definitions** | No change — v1 scope definitions were already tight and correct. | Scope was not the source of the bug; parser implementation was. |

### Before/After Summary

| Aspect | v1 | v2 |
|--------|----|----|
| YAML parser | awk single-line extraction | bash regex line-by-line |
| Block scalar handling | Captured indicator (`>` or `|`) | Reads next content line |
| False positives | 21 description issues reported | 0 description issues reported |
| Health score accuracy | 6/10 (inflated deductions) | 7/10 (accurate) |
| Scanner lines | 97 | 120 |

### Ready for Promotion
Yes. The v2 skill with the fixed scanner passes all quality gate checks and produces accurate results. Auto-promote to `.claude/skills/skill-audit-tf/`.
