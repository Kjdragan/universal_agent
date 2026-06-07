---
name: verification-before-completion
description: >
  Use when about to claim work is complete, fixed, or passing, before committing or creating PRs -
  requires running verification commands and confirming output before making any success claims;
  evidence before assertions always. Also use before any Edit/Write/patch/update/overwrite of a file
  you have not freshly Read this session - read before editing, fresh read of the exact target path -
  covering the read-before-write guard errors "File has not been read yet" and "File has been modified
  since read", and the proactive "let me just write/update/modify/patch/overwrite the file" moment
  before the guard error appears. Use when your read is stale - re-read before edit after a
  formatter/linter/hook (or build, prior tool call, or the user) touched the file. Use when a
  subagent's Edit fails because each session must read first and a parent agent's Read does not
  satisfy the guard for the child session. NOT for choosing what to build or deciding requirements,
  and not for generic "update the docs" prose unconnected to a file-mutation tool.
user-invocable: true
risk: safe
source: "UA skill-gap finder backlog (issue #796, reports/11_dotskills-build-handoff.md) — extends verification-before-completion with the read-before-write guard."
---

# Verification Before Completion

## Overview

Claiming work is complete without verification is dishonesty, not efficiency.

**Core principle:** Evidence before claims, always.

**Violating the letter of this rule is violating the spirit of this rule.**

## The Iron Law

```
NO COMPLETION CLAIMS WITHOUT FRESH VERIFICATION EVIDENCE
```

If you haven't run the verification command in this message, you cannot claim it passes.

## The Gate Function

```
BEFORE claiming any status or expressing satisfaction:

1. IDENTIFY: What command proves this claim?
2. RUN: Execute the FULL command (fresh, complete)
3. READ: Full output, check exit code, count failures
4. VERIFY: Does output confirm the claim?
   - If NO: State actual status with evidence
   - If YES: State claim WITH evidence
5. ONLY THEN: Make the claim

Skip any step = lying, not verifying
```

## Common Failures

| Claim | Requires | Not Sufficient |
|-------|----------|----------------|
| Tests pass | Test command output: 0 failures | Previous run, "should pass" |
| Linter clean | Linter output: 0 errors | Partial check, extrapolation |
| Build succeeds | Build command: exit 0 | Linter passing, logs look good |
| Bug fixed | Test original symptom: passes | Code changed, assumed fixed |
| Regression test works | Red-green cycle verified | Test passes once |
| Agent completed | VCS diff shows changes | Agent reports "success" |
| Requirements met | Line-by-line checklist | Tests passing |

## Red Flags - STOP

- Using "should", "probably", "seems to"
- Expressing satisfaction before verification ("Great!", "Perfect!", "Done!", etc.)
- About to commit/push/PR without verification
- Trusting agent success reports
- Relying on partial verification
- Thinking "just this once"
- Tired and wanting work over
- **ANY wording implying success without having run verification**

## Rationalization Prevention

| Excuse | Reality |
|--------|---------|
| "Should work now" | RUN the verification |
| "I'm confident" | Confidence ≠ evidence |
| "Just this once" | No exceptions |
| "Linter passed" | Linter ≠ compiler |
| "Agent said success" | Verify independently |
| "I'm tired" | Exhaustion ≠ excuse |
| "Partial check is enough" | Partial proves nothing |
| "Different words so rule doesn't apply" | Spirit over letter |

## Key Patterns

**Tests:**
```
✅ [Run test command] [See: 34/34 pass] "All tests pass"
❌ "Should pass now" / "Looks correct"
```

**Regression tests (TDD Red-Green):**
```
✅ Write → Run (pass) → Revert fix → Run (MUST FAIL) → Restore → Run (pass)
❌ "I've written a regression test" (without red-green verification)
```

**Build:**
```
✅ [Run build] [See: exit 0] "Build passes"
❌ "Linter passed" (linter doesn't check compilation)
```

**Requirements:**
```
✅ Re-read plan → Create checklist → Verify each → Report gaps or completion
❌ "Tests pass, phase complete"
```

**Agent delegation:**
```
✅ Agent reports success → Check VCS diff → Verify changes → Report actual state
❌ Trust agent report
```

## Read Before Write (Edit/Write Guard)

Same discipline, applied to input: the Edit and Write tools refuse to mutate a file unless a fresh Read of the *exact* target path exists in the current session. Verify your input state before mutating, just as you verify output before claiming. Two verbatim guard errors signal a skipped read:

```
File has not been read yet. Read it first before writing to it.
File has been modified since read, either by the user or by a linter. Read it again before attempting to write it.
```

Checklist:

1. Before ANY Edit/Write, Read the exact target path in this session.
2. Use the same path string you will edit (absolute; do not assume a sibling or symlink was read).
3. On "File has not been read yet" → Read the path, then re-issue the edit.
4. On "has been modified since read" → re-Read, reconcile against the new content, then re-edit.
5. After anything else may have touched the file (a formatter/linter hook, a build, a prior tool call, the user, or another subagent), re-Read before editing.

**Read before write:**
```
✅ Read(path) → Edit(path)   [same session, same exact path]
❌ Edit(path) with no prior Read / re-using a Read from before a linter or hook ran
```

A subagent's Read history is its own: a parent agent having read the file does not satisfy the guard for the child. Each session that edits must Read first.

## Why This Matters

From 24 failure memories:
- your human partner said "I don't believe you" - trust broken
- Undefined functions shipped - would crash
- Missing requirements shipped - incomplete features
- Time wasted on false completion → redirect → rework
- Violates: "Honesty is a core value. If you lie, you'll be replaced."

## When To Apply

**ALWAYS before:**
- ANY variation of success/completion claims
- ANY expression of satisfaction
- ANY positive statement about work state
- Committing, PR creation, task completion
- Moving to next task
- Delegating to agents

**Rule applies to:**
- Exact phrases
- Paraphrases and synonyms
- Implications of success
- ANY communication suggesting completion/correctness

## The Bottom Line

**No shortcuts for verification.**

Run the command. Read the output. THEN claim the result.

This is non-negotiable.
