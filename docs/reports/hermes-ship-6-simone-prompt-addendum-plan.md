# Hermes Ship 6 — Simone Prompt Addendum for Verb Tools: Detailed Implementation Plan

**Created:** 2026-05-11 PM
**Sequencing:** Can ship in parallel with Ship 4 or 5; no code dependencies. Pure prompt content + minimal test.
**Purpose:** Give Simone explicit, structured guidance on when to call `task_re_evaluate` vs `task_request_revision` vs `task_redirect_to` vs simply signing off. The verb tools shipped in PR #234 are registered and discoverable, but Simone's heartbeat directive doesn't currently teach her _how to choose_ between them. Result: she likely uses one consistently (probably `re_evaluate`) instead of the right one for the situation.

This plan is self-contained for compaction-resilience.

---

## The problem

PR #234 (merged) registered three Simone-callable verb tools:
- `task_re_evaluate(task_id, reason)`
- `task_redirect_to(task_id, target_vp, reason)`
- `task_request_revision(task_id, feedback, max_extra_retries=1)`

Each tool has a `description` string in its `@tool` decorator that explains what it does. The Claude Agent SDK passes those descriptions to Simone as part of tool discovery. So technically she has the information.

But: tool descriptions are reference docs, not decision guidance. Simone needs the equivalent of a "playbook" — when looking at a completed Cody mission, _which path should she pick?_

Today she doesn't have that playbook in her system prompt / heartbeat directive. She'll figure something out, but it'll be inconsistent and probably miss the budget-bump semantics of `request_revision` vs `re_evaluate`.

---

## What to add

A new section in Simone's heartbeat directive that contains:

1. A short framing: "When you review a Cody mission outcome, decide which follow-up applies."
2. A decision table mapping situation → verb.
3. Concrete examples (the four scenarios from the conversation explanation).
4. The operator-baked invariant: `re_evaluate` never bumps retry budget; `request_revision` does (by design).
5. A note that the natural-language `objective` passed to `vp_dispatch_mission` is for INITIAL work; the verbs are for AFTER Cody finishes.

### The exact prompt content

Here's the exact text block to insert. It's tuned for Simone (her tone is operator-style, terse, action-oriented).

```markdown
## Reviewing Cody's completed missions

When Cody (vp.coder.primary) completes a mission, your job is to judge the
work product and decide if it's done or if it needs follow-up. Use this
decision tree:

**Did Cody nail it?**
→ No action. Task stays `completed`. Move on.

**Wrong output, but you can articulate exactly what to fix?**
→ Call `task_request_revision(task_id, feedback="...", max_extra_retries=1)`.
   The `feedback` is operator-style guidance Cody reads verbatim on his
   next claim. Bumps retry budget by 1 so he can actually attempt the
   revision without immediately hitting the consecutive-failure limit.
   Example: feedback="The column is there but the header should be
   'Output' with a capital O. Also add a footer row with the column total."

**Wrong output, but you can't pinpoint what's wrong?**
→ Call `task_re_evaluate(task_id, reason="...")`.
   This attaches the full prior-run history (errors, summaries, side
   effects) to the task so Cody sees the evidence on his next attempt
   and can figure it out. Does NOT bump retry budget — operates within
   his existing failure-count limit. Use when output looks off but
   you're not sure why (numbers don't add up, completion claim looks
   unverified, file written but content suspicious).
   Example: reason="The output column shows 0 for several days I know
   had transactions. Possibly reading from the wrong sheet?"

**Wrong agent, someone else should try?**
→ Call `task_redirect_to(task_id, target_vp="vp.general.primary", reason="...")`.
   Clears Cody-specific retry counters; sets `metadata.preferred_vp` so
   the next dispatch routes to Atlas (or other named VP). Use when the
   task isn't a coding problem in the first place, or when Cody's
   toolchain is the wrong shape (e.g. needs database access he doesn't
   have configured, or requires a generalist research lane that fits
   Atlas better).
   Example: target_vp="vp.general.primary", reason="This needs a
   research summary of the regulatory landscape, not code changes."

### Key invariants you should know

- `re_evaluate` does NOT bump retry budget. If a task has already hit
  its `max_retries` ceiling, `re_evaluate` will reset state but the
  next attempt-failure may immediately re-park. Use `request_revision`
  when you need to extend the budget.
- The natural-language `objective` you pass to `vp_dispatch_mission` is
  how you communicate the INITIAL work to Cody. The three verbs above
  are exclusively for after-the-fact follow-up.
- Sign-off is the default. Only invoke a follow-up verb when you've
  actually identified a problem with the work product. Don't reflexively
  re-evaluate every completed task.
```

Total: ~50 lines of markdown.

---

## Where to put it

### Discovery: where is Simone's heartbeat directive assembled?

The system has multiple prompts. Simone's primary directive lives in `memory/HEARTBEAT.md` (referenced in CLAUDE.md and other docs as the source of her cycle directive).

Verification path during implementation:
1. `grep -rn "memory/HEARTBEAT" src/universal_agent/ --include="*.py"` to find where it's read.
2. Inspect the heartbeat assembly path — likely `src/universal_agent/heartbeat_service.py` or `src/universal_agent/services/heartbeat_*` — to confirm where `HEARTBEAT.md` is loaded and whether any other prompt files are concatenated alongside.
3. If `HEARTBEAT.md` is the canonical Simone directive, insert the addendum there.
4. If the directive is split across multiple files, find the one that contains task-action guidance and add the section there.

The user's existing reference: CLAUDE.md says "Simone's directive file is `memory/HEARTBEAT.md`." Trust that until proven otherwise.

### Placement within HEARTBEAT.md

Find an existing section about task handling or Cody coordination. Insert the new "Reviewing Cody's completed missions" section there. If no logical place exists, add it as a new top-level section near the end of the file, before any closing notes.

---

## Files touched

- `memory/HEARTBEAT.md` — primary target, ~50 lines added.
- `tests/unit/test_simone_prompt_addendum.py` — new test file, ~30 LOC. Verifies the assembled heartbeat directive contains the addendum text. (Cheap regression guard against accidental deletion.)
- `docs/Documentation_Status.md` — append entry.

Estimated total LOC: ~85 (50 prompt text + 30 test + 5 doc).

---

## Tests

### `tests/unit/test_simone_prompt_addendum.py`

Minimal regression guard:

```python
"""Hermes Ship 6 — Simone prompt addendum regression guard.

Verifies the verb-tool decision guidance is present in the assembled
Simone heartbeat directive. Without this guidance Simone has no way to
choose between task_re_evaluate / task_request_revision / task_redirect_to
beyond the tool descriptions, and tends to use one inconsistently.
"""

from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
HEARTBEAT_PATH = REPO_ROOT / "memory" / "HEARTBEAT.md"


def test_heartbeat_directive_contains_verb_decision_tree():
    """The Cody-mission review decision tree must be present in HEARTBEAT.md."""
    assert HEARTBEAT_PATH.exists(), f"missing: {HEARTBEAT_PATH}"
    text = HEARTBEAT_PATH.read_text(encoding="utf-8")
    # Sentinel markers that must be present (text changes are fine; semantic
    # markers stay).
    required_markers = [
        "Reviewing Cody's completed missions",  # section heading
        "task_request_revision",                # all three verbs referenced
        "task_re_evaluate",
        "task_redirect_to",
        "does NOT bump retry budget",          # the operator-baked invariant
    ]
    missing = [m for m in required_markers if m not in text]
    assert not missing, (
        "Simone heartbeat directive is missing required verb-tool guidance markers: "
        f"{missing}. See docs/reports/hermes-ship-6-simone-prompt-addendum-plan.md"
    )
```

This test is intentionally minimal — it doesn't validate prompt _quality_, just that the guidance hasn't been accidentally deleted.

---

## Branch / commit / ship

- **Branch:** `claude/hermes-ship-6-simone-prompt-addendum`
- **Commit message:**

```
docs(simone): verb-tool decision guidance for Cody mission follow-up

Adds explicit playbook content to Simone's heartbeat directive that
teaches her when to call which of the three follow-up verb tools
(task_re_evaluate / task_request_revision / task_redirect_to) shipped
in PR #234.

Before: Simone had the tool descriptions from @tool decorators but no
decision-tree guidance. She'd figure out which verb to use case-by-case
and inconsistently (probably defaulting to re_evaluate for everything).

After: ~50 lines in memory/HEARTBEAT.md covering:
- Sign-off is the default (only invoke a follow-up when there's a
  real identified problem).
- request_revision: specific feedback + budget bump.
- re_evaluate: vague suspicion + prior-run evidence, no budget bump.
- redirect_to: wrong agent, give it to someone else.
- The operator-baked invariant: only request_revision bumps budget.
- The natural-language `objective` vs verb-tool distinction.

Includes a small regression guard test
(tests/unit/test_simone_prompt_addendum.py) that asserts the section
heading and three verb names + the budget invariant are present in
HEARTBEAT.md. Doesn't validate prompt quality — just protects against
accidental deletion.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

- **PR:** standard PAT-based auto-merge chain.
- **Post-merge verification:** open a wedged task in production, watch Simone's next heartbeat decision. She should pick the right verb (or sign off) based on the specific failure mode. Qualitative observation; no hard metric.

---

## Open questions / decisions baked in

| Decision | Why |
|---|---|
| Insert directly into `memory/HEARTBEAT.md` rather than a separate file | Single-source-of-truth for Simone's directives. Multiple files would fragment her context. |
| 50 lines of guidance, not 500 | Operator tone is terse. More prompt content = more tokens = slower / more expensive heartbeats. The decision tree is the load-bearing part. |
| Regression test guards markers, not full text | Allows future refinement of prompt phrasing without breaking the test. We just want to catch accidental deletion. |
| No new docs for this — the prompt is the artifact | Adding doc files for prompt content creates drift between doc and prompt. The HEARTBEAT.md IS the documentation. |
| Don't add guidance for `task_hub_task_action` (the older lifecycle-verb tool) | That tool has different semantics (claim/complete/block/etc.) and its guidance is separate. Don't bundle. |

---

## Coverage gap this does NOT close

This addendum gives Simone guidance for the THREE verb tools registered in PR #234. It does NOT teach her:

- When to use `vp_dispatch_mission` to send a NEW task to Cody (the initial-dispatch path). That guidance, if missing, would be a separate follow-up.
- How to evaluate Cody's natural-language output quality (subjective, hard to encode in a prompt). Today she relies on her own judgment + the `task_hub_runs` evidence.
- Cody-mode toggle decisions (anthropic vs zai). The default is now anthropic (PR #235); operator toggles via dashboard if cost is a concern. Simone doesn't need to think about this normally.

If those gaps surface in production observation, they're separate follow-up plans, not bundled here.

---

## Execution sequence (when resuming after compaction)

1. Confirm PR #234 (Simone-callable verb tools) is merged: `gh pr view 234 --json state` → MERGED.
2. Locate `memory/HEARTBEAT.md` — confirm it exists at `/home/kjdragan/lrepos/universal_agent/memory/HEARTBEAT.md`.
3. Read the file to understand its current structure and where to insert the new section.
4. Branch off `origin/main`: `git checkout -b claude/hermes-ship-6-simone-prompt-addendum`.
5. Insert the prompt content from this plan into `memory/HEARTBEAT.md` (the exact text block above is the source of truth — paste it in).
6. Create `tests/unit/test_simone_prompt_addendum.py` with the regression-guard content.
7. Run: `uv run pytest tests/unit/test_simone_prompt_addendum.py -x -q --no-header` — must pass.
8. Commit with the template above; PR; PAT-merge chain handles deploy.
9. Post-merge: heartbeat picks up the new directive on next tick (no service restart needed since `HEARTBEAT.md` is read every cycle). Operator observation over the next few days will confirm whether Simone is picking better verbs.
