# CSI Intelligence Pass — Implementation Plan

**Created:** 2026-05-07 (post-redesign session)
**Companion:** [`knowledge_extraction_redesign_context_2026-05-07.md`](knowledge_extraction_redesign_context_2026-05-07.md) — the architecture spec.
**This doc:** the executable task plan. Phases A–H below. Each phase has a deliverable and an exit criterion. Fast iteration, small batches, throw away test runs freely.
**Workflow:** tier-2 PR contract — branch `claude/csi-intelligence-pass-mvp`, isolated worktree, push to remote, open PR, `pr-validate.yml` + any review CI runs, operator merges. No direct commits to `feature/latest2` for the code work.

---

## Goal

A working `services/csi_intelligence_pass.py` that takes one tweet's action + classifier output + fetched linked sources + the current vault's entity list, and returns a structured `VaultDelta` an operator can read and judge as good.

Iterate the *prompt* until single-action quality is right, then expand to packet-level, then full corpus.

---

## Hard rules (from the redesign spec)

- **Zero regex in the analysis chain.** Plumbing-only regex (slugifying filenames, parsing JSON) is fine; meaning judgment is forbidden.
- **Model: GLM-5.1 via `resolve_opus()`.** No `thinking={...}`, no `reasoning_effort` — GLM-5.1 has no thinking mode.
- **Quality levers:** strong system prompt, rich context in user message, structured-output via tool_use, generous `max_tokens`. Iterate the prompt as the primary lever.
- **Vault contents are throwaway.** Don't preserve v2 partial state. `rm -rf` the v2 vault freely between iterations.

---

## Phase status table (updated as phases complete)

| Phase | Status | Deliverable | Exit criterion |
|---|---|---|---|
| A — Scaffold | not started | `services/csi_intelligence_pass.py` skeleton + dev probe script | Code compiles, types check, callable |
| B — First LLM run on ONE action | not started | Structured output for one tweet + my honest read | LLM produces structured JSON; eyeball quality recorded |
| C — Iterate prompt | not started | A system prompt that produces clean output on diverse actions | 5 consecutive runs on diverse actions: zero junk, zero misses |
| D — Expand to packet, then 3-5 packets | not started | Aggregate run on diverse packets | Quality holds across packet variety |
| E — Wire persistence | not started | `VaultDelta → memex_apply_action` helper + unit tests | Mocked-LLM tests pass, vault files written correctly |
| F — Replace `apply_memex_pass` body | not started | Old regex deleted; new pass wired | `apply_memex_pass` test packets produce same output as the probe phase |
| G — Full small backfill (gated on F) | not started | Fresh v2 vault from all 36 packets | 10/10 random sampled entities are real |
| H — Ship | not started | Tests + commit + PR + merge | CI green, operator merges |

---

## Phase A — Scaffold (~30 min, zero LLM cost)

### Files to create

1. **`src/universal_agent/services/csi_intelligence_pass.py`** — the new module.
   - Pydantic models:
     ```python
     class VaultAction(BaseModel):
         op: Literal["create", "extend", "revise"]
         kind: Literal["product", "feature", "concept", "person", "event"]
         name: str
         aliases: list[str] = []
         summary: str
         key_facts: list[str] = []
         source_post_ids: list[str] = []
         source_doc_urls: list[str] = []
         confidence: Literal["high", "medium", "low"]
         existing_slug: Optional[str] = None  # for extend/revise

     class VaultRelation(BaseModel):
         from_slug: str
         to_slug: str
         kind: Literal["uses", "feature-of", "alternative-to", "successor-to", "operates-on"]

     class VaultDelta(BaseModel):
         vault_actions: list[VaultAction] = []
         relations: list[VaultRelation] = []
         post_summary: str = ""
         post_tags: list[str] = []
     ```
   - System prompt as a module constant `_CSI_SYSTEM_PROMPT` — first draft. Includes:
     - Domain glossary (Anthropic / Claude / Claude Code / Opus / Sonnet / Haiku / MCP / managed agents / Agent SDK)
     - Taxonomy guidance (product vs feature vs concept vs person vs event)
     - Explicit "do not extract" examples (t.co URL slugs, English stopwords, joke words from tweet wordplay)
     - Few-shot examples
   - Public function:
     ```python
     def analyze_action(
         action: dict,
         linked_sources: list[str],
         existing_vault_entities: list[str],
     ) -> VaultDelta:
         """Single LLM call returning structured vault delta for one tweet's action."""
     ```
   - Internals: build prompt, call LLM via tool_use (mirror `csi_url_judge._call_llm_structured`), parse + validate JSON into `VaultDelta`, return.

2. **`scripts/dev/probe_csi_extractor.py`** — dev-only probe (NOT part of production deploy; exclude from packaged scripts).
   - Usage: `PYTHONPATH=src uv run python scripts/dev/probe_csi_extractor.py <packet_dir> [--action-index N]`
   - Loads `actions.json`, picks one or all tier-2/3 actions
   - Loads `linked_sources/*.md` content
   - Reads existing v1 vault entity slugs as the "existing entities" context
   - Calls `analyze_action`
   - Pretty-prints input + structured output to stdout
   - **No vault writes. No DB writes. Pure stdout.**

### Exit criterion for Phase A

- `python -m py_compile src/universal_agent/services/csi_intelligence_pass.py` returns 0
- Probe script can be invoked without runtime errors (will fail on actual LLM call, that's Phase B)
- Pydantic models import cleanly

---

## Phase B — First real LLM run on ONE action (~5 min, 1 LLM call)

### Test action selection

Pick the worst-junk-producing tweet from the failed v2 backfill. Top candidates from the diagnostic:

| Test case | Why | Expected output |
|---|---|---|
| `"Live now at https://t.co/EKyctqSCXB and on mobile. Tell us what you think."` | Produced `EKyctqSCXB` as entity (regex extracted t.co slug) | Empty `vault_actions` list — zero entities |
| `"✻ Flibbertigibetting…"` | Produced `flibbertigibetting` as entity (regex extracted joke word) | Empty `vault_actions` list — zero entities |
| `"In Claude Managed Agents, we've added multiagent orchestration, an outcomes loop for rubric-driven self-improvement, dreaming for self-learning, & webhooks"` | The umbrella feature post | 4 distinct features under "Claude Managed Agents" |

Recommendation: start with case #1 (the t.co tweet) — easiest to verify junk-rejection.

### Run command

```bash
PYTHONPATH=src uv run python scripts/dev/probe_csi_extractor.py \
    /opt/universal_agent/artifacts/proactive/claude_code_intel/packets/<date>/<packet_id> \
    --action-index 0
```

### Exit criterion for Phase B

LLM call completes, returns parseable JSON matching the Pydantic schema. **Whether the output is good is Phase C; this phase just needs to confirm the wiring works.**

---

## Phase C — Iterate prompt until quality is right (loop)

### Iteration discipline

For each test action:

1. Run probe.
2. Eyeball output. Categorize each `vault_action` as:
   - **Correct** — real entity, right kind, right canonical name
   - **Junk** — should not have been emitted (stopword, t.co slug, joke word)
   - **Wrong kind** — should be product but got concept, etc.
   - **Wrong canonical** — should be "Claude Managed Agents" but got "Agents"
   - **Missing** — a real entity that wasn't emitted
3. If any failure: refine `_CSI_SYSTEM_PROMPT` (add few-shot example of the failure case, tighten taxonomy guidance, add explicit "do not extract" example). Re-run.
4. Repeat until all output categories pass on the test action.
5. Move to the next test action. Keep prompt unified across all of them — refining the prompt for action #2 must not regress action #1.

### Test action set (5 actions, diverse failure modes)

| # | Tweet text snippet | Expected output |
|---|---|---|
| 1 | `"Live now at https://t.co/EKyctqSCXB and on mobile."` | `vault_actions: []` |
| 2 | `"✻ Flibbertigibetting…"` | `vault_actions: []` |
| 3 | `"In Claude Managed Agents, we've added multiagent orchestration, an outcomes loop, dreaming, & webhooks"` | 4 vault_actions: managed agents (existing — extend), multiagent orchestration (new feature), outcomes loop (new feature), webhooks (new feature) |
| 4 | `"Pair Opus 4.7 with Claude Managed Agents to build and deploy agents..."` | Opus 4.7 (product), Claude Managed Agents (extend if present), agents-as-product reference |
| 5 | `"New blog: Building agents that reach production systems with MCP"` | MCP (extend), maybe a `production agents` concept |

### Exit criterion for Phase C

5 consecutive runs over the 5 test actions: zero junk, zero misses, correct kinds, correct canonical names. Prompt is locked.

---

## Phase D — Expand to a full packet, then 3-5 representative packets (~20 min, ~30-50 LLM calls)

### Step 1 — Single packet, all actions

Run probe over all tier-2/3 actions in a single packet. Eyeball:
- No duplicate entity creates within the packet
- Correct CREATE-vs-EXTEND when two tweets reference the same entity
- Relations capture cross-tweet patterns

### Step 2 — Diverse packets

Pick 4-5 packets with different failure-mode profiles:
- `2026-05-06/210011__ClaudeDevs` — multi-feature umbrella post (failure mode: missing features)
- `2026-05-07/210014__bcherny` — single-author commentary (failure mode: extracting noise)
- `2026-04-29/130011__ClaudeDevs` — older release content (failure mode: stale aliases)
- One packet rich in linked-source content (failure mode: under-using fetched docs)
- One packet rich in t.co-only "live now" / "check this out" tweets (failure mode: junk)

### Exit criterion for Phase D

Aggregate quality across 4-5 packets ≥ Phase C's per-action standard. If quality regresses on a packet, return to Phase C and fix the prompt.

---

## Phase E — Wire persistence (~30 min, no LLM cost)

### What to build

Helper function `apply_vault_delta_to_vault(delta: VaultDelta, vault_root: Path, packet_id: str) -> None`:

For each `VaultAction` in `delta.vault_actions`:
- `op="create"` → call `wiki/core.py:memex_apply_action(action="CREATE", ...)`
- `op="extend"` → call `memex_apply_action(action="EXTEND", existing_slug=...)`
- `op="revise"` → call `memex_apply_action(action="REVISE", existing_slug=...)` (also writes `_history/` snapshot)

The body content for the page is constructed from the `VaultAction`'s `summary` + `key_facts` + provenance metadata. This is NOT regex/template synthesis of meaning — it's serialization of the LLM's already-structured output.

### Tests

Unit tests in `tests/unit/test_csi_intelligence_pass_persistence.py`:
- Mock `analyze_action` to return a fixture `VaultDelta`
- Run persistence helper against a temp vault directory
- Assert: file written at expected path, frontmatter contains expected fields, `log.md` has correct entry, `_history/` populated for REVISE

### Exit criterion for Phase E

All persistence tests pass. No LLM calls in tests (mocked).

---

## Phase F — Replace `apply_memex_pass` body (~15 min, no LLM cost)

### Code changes

Inside `services/claude_code_intel_replay.py`:

**Delete:**
- Lines 548-587: `_MEMEX_TERM_PATTERN`, `_MEMEX_TERM_STOPWORDS`
- Lines 589-627: `_memex_candidates_for_action`
- Lines 630-687: `_memex_body_for_create`, `_memex_body_for_extend`

**Rewrite:**
- `apply_memex_pass(...)` (line 689): now iterates over actions, calls `analyze_action` for each, then `apply_vault_delta_to_vault` for each result. Function signature, return shape, integration with the replay orchestrator stay the same.

**Also fix while in the file:**
- The `IsADirectoryError` bug at line 281 (`refine_actions_with_linked_sources`): change `if metadata_path.exists()` to `if metadata_path.is_file()`. ~1 line change.

### Test in throwaway vault

```bash
rm -rf /opt/universal_agent/artifacts/knowledge-vaults/claude-code-intelligence-v2
PYTHONPATH=src uv run python -m universal_agent.scripts.claude_code_intel_backfill_v2 \
    --dry-run  # confirm packet count unchanged at 36
```

Then for each test packet from Phase D, run a single-packet backfill via `scripts.claude_code_intel_replay_packet` (if it exists) or a thin invocation of `replay_packet` directly. Compare output to Phase D's probe output for the same packet — should be identical.

### Exit criterion for Phase F

Single-packet replay through the new code path produces vault files matching Phase D's probe output.

---

## Phase G — Full small backfill (gated on F passing) (~10-30 min, ~50-100 LLM calls)

```bash
rm -rf /opt/universal_agent/artifacts/knowledge-vaults/claude-code-intelligence-v2
PYTHONPATH=src uv run python -m universal_agent.scripts.claude_code_intel_backfill_v2
```

Wait for completion. Then:

```bash
ls /opt/universal_agent/artifacts/knowledge-vaults/claude-code-intelligence-v2/entities/ | wc -l
ls /opt/universal_agent/artifacts/knowledge-vaults/claude-code-intelligence-v2/sources/ | wc -l
ls /opt/universal_agent/artifacts/knowledge-vaults/claude-code-intelligence-v2/concepts/ | wc -l
```

Sample 10 random entities by name. **Exit criterion: all 10 are real concepts/products/features/people. Zero stub-only stopword pages. Zero t.co slugs.**

If any fail: identify the failure mode, return to Phase C with new test cases.

---

## Phase H — Ship (only after G passes)

### Tests

- `tests/unit/test_csi_intelligence_pass.py` — golden-set test for `analyze_action` on a fixture packet with a mocked LLM client returning a known `VaultDelta`
- `tests/unit/test_csi_intelligence_pass_persistence.py` — already written in Phase E

### Commit + PR

Branch: `claude/csi-intelligence-pass-mvp`

Commit message body:
```
feat(csi): replace regex memex extractor with LLM intelligence pass

The existing _memex_candidates_for_action regex was producing ~50%
junk entities (stopwords, t.co URL slugs, joke words). Replaced with
a single rich LLM analysis pass at replay time that takes full
context (tweet + classifier output + linked sources + existing
vault state) and returns a structured VaultDelta.

Architecture: docs/proactive_signals/knowledge_extraction_redesign_context_2026-05-07.md
Plan: docs/proactive_signals/csi_intelligence_pass_implementation_plan_2026-05-07.md

Deletes:
- _MEMEX_TERM_PATTERN, _MEMEX_TERM_STOPWORDS
- _memex_candidates_for_action
- _memex_body_for_create, _memex_body_for_extend

Adds:
- services/csi_intelligence_pass.py (analyze_action + VaultDelta schema)
- helper for VaultDelta → memex_apply_action persistence
- tests/unit/test_csi_intelligence_pass*.py

Also fixes:
- claude_code_intel_replay.py:281 IsADirectoryError (path.exists() →
  path.is_file()) — found during the failed v2 backfill 2026-05-07

Model: GLM-5.1 via resolve_opus(). No reasoning param (GLM-5.1 has
no thinking mode). Quality comes from prompt + context + structured
output via tool_use.
```

### CI gates

- `pr-validate.yml` runs: py_compile, ruff check, pytest tests/unit, no .py.bak/.swp/.orig
- Operator reviews + merges
- After merge, deploy workflow runs (production picks up the new code)
- Next scheduled CSI cron tick produces clean output

### Exit criterion for Phase H

PR merged. Deploy green. Next scheduled CSI cron fire produces a vault entity that's a real concept (eyeball check).

---

## Operating notes for the new code

- **Persistence-only fields** added by `apply_vault_delta_to_vault`:
  - `provenance_kind: "memex_create"` / `"memex_extend"` / `"memex_revise"` (matches existing convention)
  - `provenance_refs: [post_id]`
  - `confidence: <from VaultAction>`
  - `status: "active"` (default; can be overridden by future REVISE policy)
- **Dedup against existing vault**: `existing_vault_entities` parameter to `analyze_action` is the list of existing entity slugs + their summaries. The LLM uses this to choose CREATE-vs-EXTEND — code does NOT make this decision.
- **`_history/` snapshots** are written automatically by `wiki/core.py:memex_revise_page` whenever an op="revise" is applied. New code doesn't need to manage history manually.
- **Concept extraction** comes from the same LLM call (kind="concept" in the structured output). Don't add a separate concept pass.

---

## Pause points (operator-driven checkpoints)

The operator can pause-and-review at the natural transitions:

- **After Phase B:** Confirm the wiring works before iterating prompt
- **After Phase C:** Confirm the prompt is locked before expanding to packets
- **After Phase D:** Confirm packet-level quality before wiring persistence
- **After Phase F:** Confirm single-packet end-to-end before full backfill
- **After Phase G:** Confirm full-backfill quality before opening PR

If any phase fails its exit criterion three times in a row, stop and surface to operator. Don't tune indefinitely.
