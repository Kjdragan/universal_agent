# Knowledge-Extraction Redesign — Context for Fresh Session

**Created:** 2026-05-07 23:15 UTC
**Audience:** Fresh Claude Code session that picks up the knowledge-extraction-quality work
**Goal of the next session:** Diagnose root cause of low-quality entity extraction in the CSI v2 pipeline → redesign the extractor → verify in a tight test loop until it produces high-signal entities. The vault content itself is throwaway right now; the system is what matters.

---

## TL;DR for the new agent

1. **The CSI knowledge-extraction system is producing low-signal output.** The v2 backfill (and the production v1 path) emits entities like `the.md`, `and.md`, `for.md`, `flibbertigibetting.md`, `ekyctqscxb.md` (a Twitter t.co URL slug). Roughly 50% of generated entities are junk.
2. **It's a pure regex, not an LLM — and that's the load-bearing problem.** The producer is `_memex_candidates_for_action` at `src/universal_agent/services/claude_code_intel_replay.py:589-627`. Regex `[A-Z][a-zA-Z0-9_]{2,}|[a-z][a-zA-Z0-9_]+_[a-zA-Z0-9_]+` + a tiny ~30-word stopword list. **The operator has explicitly ruled out keeping a regex-based design.** The fix must be LLM-based extraction with a domain-specific prompt. Regex tuning is at best a 30-minute backstop, not the goal.
3. **CLAUDE.md "LLM-Native Intelligence Design" applies.** *"Prefer using LLM reasoning over building custom Pythonic pseudo-reasoning systems... avoid creating elaborate programmatic trend/theme/scoring systems that attempt to imitate reasoning over small-to-medium corpora."* The current regex extractor is the canonical anti-pattern that rule was written to prevent. The redesign should let an LLM do the meaning-extraction with code only enforcing structure / dedup / persistence.
4. **There's an LLM-based extractor scaffold at `src/universal_agent/wiki/llm.py:115` (`extract_entities`)** — used for `wiki_ingest_external_source` but NOT wired into CSI packet replay. Its prompt is too generic for our domain (just *"extract the most important named entities"*). The new session should either wire it in with a much better domain-specific prompt, or build a new CSI-specific extractor in a separate module.
5. **The slowness in the v2 backfill was NOT from extraction.** 964 z.ai LLM calls in 58 minutes — almost all of those were `csi_url_judge._call_llm_structured` (one LLM call per linked URL, ~15 URLs/packet × 36 packets ≈ 540 calls + retries). Plus URL fetching. The regex extractor was effectively free in compute terms — that's *why* its output is shallow. Adding an LLM extraction step adds maybe 30-100 calls per backfill on top of the existing budget — small marginal cost.
6. **The vault contents are throwaway.** Don't preserve them. Don't run a full backfill. Don't swap. The user has explicitly said: rebuild the system, iterate small, throw away test runs freely. Production v1 vault stays canonical until a *good* extractor is built.
7. **You're working on `feature/latest2` in the dev tree** at `/home/ua/dev/universal_agent`. Production at `/opt/universal_agent` was just recovered to `main @ f4c793e2` and is healthy — leave it alone except as a code-reading reference.

---

## Cost / latency budget (where the time actually went on the failed backfill)

To pre-empt a question that comes up early: the v2 backfill ran for 58 minutes and made 964 z.ai LLM calls. Almost none of that was extraction. Breakdown:

| Step | Module | LLM calls per packet | Notes |
|---|---|---|---|
| Per-URL judging (which linked URLs are worth fetching) | `services/csi_url_judge.py:284 _call_llm_structured` | ~10-20 | Each tweet has multiple linked URLs (anthropic docs, github, t.co); each gets a structured-output LLM call to judge worth-fetching |
| URL fetching (HTTP) | `services/csi_url_judge.py:479 _fetch_with_defuddle` / `_fetch_with_httpx` | 0 LLM, ~10-20 HTTP | Often 2 redirects (`docs.anthropic.com → platform.claude.com`) |
| Entity *selection* (which terms become entities) | `services/claude_code_intel_replay.py:589 _memex_candidates_for_action` | **0** (pure regex) | This is the broken path |
| Entity *body* (the .md content) | `services/claude_code_intel_replay.py:630 _memex_body_for_create` | **0** (pure template assembly) | Just concatenates post text + classifier rationale + linked URLs |
| Classifier reasoning (tier classification, action_type) | n/a (cached in packet at original-poll time) | 0 | Already done when CSI cron created the packet originally |

So 36 packets × ~15 URL judge calls/packet ≈ **540 LLM calls just for URL judging**. The remaining ~400 calls are URL judge retries + linked-source enrichment + research_grounding (if it fired). **Adding LLM-based entity extraction adds at most ~5-10 more calls per packet (~180-360 calls per backfill) — small marginal cost on top of the existing budget.**

So the new design should not worry about cost-of-LLM-extraction. The dominant cost is already there.

---

## What just happened (1-paragraph context)

A 2026-05-07 production incident (rogue Codie/Simone branch, CSI cron crash, full recovery) is documented in `docs/operations/2026-05-07_codie_rogue_branch_recovery.md` and `docs/operations/2026-05-07_open_followups.md`. Production was restored to `main @ f4c793e2` and CSI cron is firing cleanly. As an aftermath task, the operator approved running the v2 historical backfill (Item 6 from `docs/operations/2026-05-07_handoff_followups.md`). The backfill ran for ~58 minutes, processed 35 of 36 packets (1 failed with `IsADirectoryError`), and produced a parallel vault at `/opt/universal_agent/artifacts/knowledge-vaults/claude-code-intelligence-v2/` with 100 entities, 90 sources, 0 concepts. **Inspection revealed that ~50% of those entities are stopwords or URL slugs, not real concepts.** The user stopped the backfill mid-flight and pivoted: "fix the system, the vault is irrelevant." The previous session was killed; THIS doc is the handoff to a fresh session.

---

## The two extraction paths

### Path A — LLM-based (used for external-ingest, NOT for CSI)

```
src/universal_agent/wiki/llm.py:115         extract_entities(text)        # LLM call
src/universal_agent/wiki/llm.py:135         extract_concepts(text)        # LLM call
src/universal_agent/wiki/core.py:723        called from wiki_ingest_external_source
```

Prompt at `wiki/llm.py:94`:

```
You are an expert NLP system. Given a text, extract the most important named entities
(people, organizations, locations, products, specific tools).
Return ONLY a JSON array of strings, e.g. ["OpenAI", "Alice", "Project X"].
Exclude generic words or extremely common stopwords.
```

Has a heuristic regex fallback at `llm.py:130-132` if the LLM call fails.

This path is used when external content is ingested via `wiki_ingest_external_source` — but **the CSI replay flow does NOT call this**. CSI packets go through Path B instead.

### Path B — Pure regex (THIS is what's producing the junk)

```
src/universal_agent/services/claude_code_intel_replay.py:548-587   regex + stopwords
src/universal_agent/services/claude_code_intel_replay.py:589-627   _memex_candidates_for_action
src/universal_agent/services/claude_code_intel_replay.py:689       apply_memex_pass (the orchestrator)
```

The relevant code (verbatim from `claude_code_intel_replay.py:548-587`):

```python
_MEMEX_TERM_PATTERN = re.compile(
    r"\b([A-Z][a-zA-Z0-9_]{2,}|[a-z][a-zA-Z0-9_]+_[a-zA-Z0-9_]+)\b"
)
_MEMEX_TERM_STOPWORDS = frozenset(
    {
        "claude", "anthropic", "agents", "agent", "tool", "tools",
        "this", "that", "with", "from", "into", "what", "when",
        "have", "been", "more", "https", "http", "url", "post",
        "github", "discord", "twitter", "reddit", "demo", "demos",
        "build", "release", "released", "today", "support", "supports",
    }
)
```

Selection logic at `claude_code_intel_replay.py:589-627`:
- Release announcements → use `release_info.package` as entity (good — this is reliable)
- Otherwise → regex over `action.text`, take up to 5 matches that aren't in the stopword list, length ≥ 3 chars
- No t.co URL exclusion, no corroboration check, no length floor beyond 3

Result: tweets like `Live now at https://t.co/EKyctqSCXB and on mobile.` produce the entity `EKyctqSCXB` because the regex matches it. Tweets like `✻ Flibbertigibetting…` produce the entity `Flibbertigibetting` because it's a capitalized 17-char word and not in the stopword list.

---

## Concrete junk evidence

From the 100 entities produced by the v2 backfill at `/opt/universal_agent/artifacts/knowledge-vaults/claude-code-intelligence-v2/entities/`:

**Pure stopwords / English words created as entities:**
```
all.md, also.md, and.md, any.md, appreciate.md, auto.md, building.md,
check.md, code.md, cowork.md, enjoy.md, everyone.md, fair.md,
findings.md, fix.md, follow.md, for.md, full.md, happy.md, hear.md,
here.md, hmm.md, improving.md, join.md, just.md, learn.md, let.md,
live.md, max.md, memories.md, new.md, older.md, our.md, over.md,
read.md, run.md, same.md, see.md, separately.md, some.md, speed.md,
start.md, starting.md, sunday.md, team.md, tell.md, thank.md,
thanks.md, the.md, then.md, there.md, trigger.md, tuesday.md, use.md,
work.md, would.md
```

**T.co URL slugs created as entities** (these come from `https://t.co/<slug>` in tweet text):
```
ekyctqscxb.md, gw9d0wedni.md, irghzxmkya.md, nhiw6ayyxz.md,
nvqzhlkrud.md, sah9klmj0z.md
```

**Joke / nonsense words**:
```
flibbertigibetting.md  (← captured from a tweet "✻ Flibbertigibetting…")
```

**Genuinely useful entities** (smaller subset, ~25-30 of the 100):
```
claude-code.md, claudedevs.md, console.md, desktop.md, enterprise.md,
haiku.md, mcp.md, opus.md, platform.md, sdk.md, sonnet.md, vscode.md,
ttft.md, javascript.md, cli.md, cache.md, memory.md, ...
plus Twitter handles: armstrong-k, dsjayatillake, hackingdave,
haneeefshiraz, readysetbrian, rlancemartin, sauravv-x, sergey-moloman
```

**v1 has the same problem.** Production v1 vault at `/opt/universal_agent/artifacts/knowledge-vaults/claude-code-intelligence/` has 44 entities including `behind`, `building`, `chief`, `each`, `full`, `get`, `hello`, `keynote`, `love`, `multi`, `oss`, `pro`, `rounded`, `see`, `team`, `thanks`, `two`, `usage`, `working`, `yes` plus 5 t.co slugs (`e3rneinuzx`, `he19qz8icv`, `hwfg22yips`, `ip4pglaesp`, `lsyiaws0sk`). So this is **a long-standing extraction-quality problem, not a v2 regression**. Both vaults run on the same code path.

---

## Sample entity bodies for ground-truth reference

### Junk entity (frontmatter only, no real content):

`/opt/universal_agent/artifacts/knowledge-vaults/claude-code-intelligence-v2/entities/the.md`:
```yaml
---
title: The
kind: entity
updated_at: '2026-05-07T22:47:50.148004+00:00'
tags: [claude-code, claude-devs, strategic_follow_up]
source_ids: [x_post_2047371124238062069, x_post_2047376371517964549, ...]
provenance_kind: memex_create
confidence: medium
status: active
summary: ClaudeDevs post 2047371124238062069
---

## Discovery context
- handle: `@ClaudeDevs`
- post: https://x.com/ClaudeDevs/status/2047371124238062069
- post_id: `2047371124238062069`
```

### Plausible entity (rich body with classifier rationale + linked sources):

`/opt/universal_agent/artifacts/knowledge-vaults/claude-code-intelligence-v2/entities/mcp.md`:
```yaml
---
title: MCP
kind: entity
tags: [claude-code, claude-devs, kb_update]
source_ids: [x_post_2047086372666921217]
confidence: medium
status: active
summary: ClaudeDevs post 2047086372666921217
---

### Tweet text
New blog: Building agents that reach production systems with MCP.
When should agents use direct APIs vs CLIs vs MCP? Plus patterns for building MCP
servers, context-efficient clients and pairing MCP with skills.
https://t.co/JEogw5vWly

### Classifier rationale
This is a blog post announcing reference content about MCP architecture patterns
(API vs CLI vs MCP tradeoffs, server building patterns, context-efficient clients)...

### Linked official sources
- [Building agents that reach production systems with MCP | Claude](https://claude.com/blog/...)
```

The body template is good when the entity is real. The bug is purely in the *selection* of which terms become entities.

---

## Test corpus (use these for the iteration loop)

The 36 packets sit at `/opt/universal_agent/artifacts/proactive/claude_code_intel/packets/`:

```
2026-04-20/130011__ClaudeDevs        ← FAILS during backfill (IsADirectoryError, see below)
2026-04-20/130011__bcherny
... (33 more packets) ...
2026-05-07/210014__bcherny           ← latest, from the 16:00 CDT scheduled cron
```

Each packet directory contains:
- `actions.json` — the classified actions (tier 1/2/3) for tweets in this poll
- `linked_sources/` — fetched external content
- `posts.json` — raw tweet payloads
- Other metadata

The actions.json file is the **input to `_memex_candidates_for_action`**. It's text-only — no LLM needed to test extraction changes. Read one packet's actions.json, run the extractor, eyeball the output, fix, repeat.

---

## Known bug to inherit (separate from the extraction quality issue)

The 2026-04-20 ClaudeDevs packet fails replay with:

```
File ".../services/claude_code_intel_replay.py:281" in refine_actions_with_linked_sources
    metadata = _load_json(metadata_path) if metadata_path.exists() else {}
File ".../services/claude_code_intel_replay.py:1288" in _load_json
    return json.loads(path.read_text(encoding="utf-8"))
IsADirectoryError: [Errno 21] Is a directory: '.'
```

`metadata_path` is being mis-computed as `Path('.')`. The `.exists()` check returns True for a directory; `read_text()` fails. Fix is `if metadata_path.is_file()` instead. Independent of the extraction-quality work but worth fixing in the same session if you're already in `claude_code_intel_replay.py`.

---

## Suggested investigation + redesign plan (for the new session to follow or override)

The user's stated discipline: *"figure out why it's being produced in a non-quality way and then deciding how we change our knowledge extraction process to work better and then run it very briefly to see if it actually then will generate proper extractions and continue that loop until we are satisfied."*

### Phase 1 — Map the problem (read-only, ~30 min)

1. Read `claude_code_intel_replay.py:530-700` end to end. Understand `_memex_candidates_for_action`, `_memex_body_for_create`, `apply_memex_pass`, and how they're wired into `replay_packet`.
2. Read `wiki/llm.py:80-150` (the LLM-based extractor) and `wiki/core.py:715-740` (where it's invoked from `wiki_ingest_external_source`). This is the alternative architecture if you decide to replace the regex with LLM.
3. Read 3-5 actions.json files from the test-corpus packets. Pick at least one tweet that produced a junk entity — note exactly which token in the text triggered it.

### Phase 2 — Architectural direction (operator-mandated)

**The operator has explicitly ruled the design: LLM-based extraction with a domain-specific prompt.** Regex is rejected as fundamentally inadequate for intelligence extraction. Per CLAUDE.md's "LLM-Native Intelligence Design" rule, code's job is to *collect and preserve evidence + gate execution + enforce structure*, not to imitate reasoning.

So Phase 2 is about **how** to do LLM extraction well, not whether to do it.

**Sub-option B1 — Wire in the existing `wiki/llm.py:extract_entities` with a better prompt.**
- Replace the call to `_memex_candidates_for_action` with a call into `extract_entities` (or a CSI-specific cousin of it).
- The current prompt is too generic — *"extract the most important named entities (people, organizations, locations, products, specific tools)"*. We need a domain-specific prompt that knows about Anthropic's product family (Claude, Claude Code, Opus/Sonnet/Haiku, MCP, managed agents, the Agent SDK, etc.), distinguishes products from features from concepts from people-handles, and explicitly rejects URL fragments / token IDs / English stopwords.
- Pros: smallest delta to current code; reuses existing LLM client wiring; one call per tweet.
- Cons: prompt has to be carefully tuned; structured output likely needed (use the SDK's `tools` / structured output mode).

**Sub-option B2 — Build a new CSI-specific extraction module.**
- New module e.g. `src/universal_agent/services/csi_entity_extractor.py` that takes a packet (multiple actions + linked sources + classifier rationale) and returns a structured entity list with kinds (product / feature / person / concept / event), aliases, and source attribution.
- Single LLM call per packet (not per tweet) — gets richer context, can deduplicate / canonicalize within the packet, better at multi-word entities.
- Pros: more coherent within a packet; potentially produces concepts (not just entities); explicit taxonomy.
- Cons: bigger code change; more prompt-engineering work; one bad LLM response affects more entities.

**Sub-option B3 — Multi-stage extraction (candidate → verify → canonicalize).**
- Stage 1: cheap candidate gathering (could even keep the current regex as a wide net).
- Stage 2: LLM filters/canonicalizes the candidates with full packet context.
- Stage 3: dedup / merge with existing vault entities.
- Pros: most robust to bad LLM outputs (regex provides recall, LLM provides precision).
- Cons: most code to write and reason about.

**Recommendation for the new session:** Start with B1 (lowest delta) and a CSI-tuned prompt. Iterate via the test loop in Phase 3 below. If single-tweet-context produces fragmented or duplicative entities, escalate to B2. Don't pre-engineer for B3 unless B1 and B2 both prove insufficient. Either way, the deliverable is *"an LLM produces a structured list of real entities with kinds and aliases; code persists them"* — not *"a regex picks tokens; a body template wraps them."*

### Phase 2.5 — Backstop note (only if LLM extraction proves unworkable)

If for some reason LLM-based extraction can't be made to work (model cost prohibitive, latency unworkable, JSON quality too inconsistent across runs), the fallback would be improving the regex (broader stopwords, t.co URL-fragment exclusion, length floor, corroboration requirement). But the operator's stated direction is that this is **not** acceptable as the primary design — only as an emergency backstop. Don't build the regex backstop unless you've genuinely exhausted the LLM path.

### Phase 3 — Tight iteration loop

User wants this discipline:
- Pick a single test packet (e.g. `2026-05-06/210011__ClaudeDevs` — the one that produced `flibbertigibetting`, `EKyctqSCXB` entities).
- Run the *new* extractor on just that packet. Get the entity list. Eyeball.
- If junk slips through: refine the prompt. If real entities are dropped: refine. Re-run.
- Continue until eyeball-quality is high.
- THEN expand to 3 packets, then 10, then full 36.
- Throw away the parallel vault between iterations: `rm -rf /opt/universal_agent/artifacts/knowledge-vaults/claude-code-intelligence-v2/`.

A reasonable fast-test scaffold the new session could write:

```python
# scripts/dev/probe_csi_extractor.py (run with PYTHONPATH=src uv run)
import json, sys
from pathlib import Path
# from universal_agent.services.csi_entity_extractor import extract_entities_for_packet
# (or whichever module you build)

packet_dir = Path(sys.argv[1])  # e.g. .../packets/2026-05-06/210011__ClaudeDevs
actions = json.loads((packet_dir / "actions.json").read_text())

# Probe one tweet at a time so the LLM call is cheap and the output is auditable
for action in actions.get("actions", []):
    print(f"\n=== post {action.get('post_id')} (tier={action.get('tier')}) ===")
    print(f"text: {action.get('text', '')[:200]!r}")
    print(f"classifier reasoning: {(action.get('classifier') or {}).get('reasoning', '')[:200]!r}")
    
    entities = extract_entities_for_action(action)   # ← your new LLM-backed function
    
    print(f"extracted entities: {entities}")
    # Expected output for this tweet's text "Live now at https://t.co/EKyctqSCXB and on mobile.":
    #   []  ← because there's nothing real to extract; previous regex extracted "EKyctqSCXB"
```

That's the iteration kernel. One LLM call per tweet, cheap, fully auditable. Iterate the *prompt* (not the regex) until the printed output is high-signal. Then turn it into a unit test (`tests/unit/test_csi_entity_extraction.py`) with a curated golden set of `(action_text, expected_entities)` pairs so the prompt can't regress.

**Specific golden-set examples to include in tests** (drawn from observed failures and successes):

| Tweet text snippet | Expected entities |
|---|---|
| `"Live now at https://t.co/EKyctqSCXB and on mobile. Tell us what you think."` | `[]` (nothing meaningful — kill `EKyctqSCXB`, `Live`, `Tell`) |
| `"✻ Flibbertigibetting…"` | `[]` (joke / nonsense — kill `Flibbertigibetting`) |
| `"New blog: Building agents that reach production systems with MCP. ..."` | `[{"name": "MCP", "kind": "concept"}, {"name": "managed agents" or "agents", "kind": "concept"}]` |
| `"Pair Opus 4.7 with Claude Managed Agents to build and deploy agents..."` | `[{"name": "Opus 4.7", "kind": "product"}, {"name": "Claude Managed Agents", "kind": "product"}]` (both multi-word; both should be canonicalized) |
| `"In Claude Managed Agents, we've added multiagent orchestration, an outcomes loop..."` | `[{"name": "Claude Managed Agents", "kind": "product"}, {"name": "multiagent orchestration", "kind": "feature"}, {"name": "outcomes loop", "kind": "feature"}, ...]` |
| `"Two big changes for Claude Code today: 5-hour rate limits doubled..."` | `[{"name": "Claude Code", "kind": "product"}]` (the rate-limit detail is a fact about Claude Code, not its own entity) |

### Phase 4 — Verify on the full corpus

Once the unit test passes on hand-curated cases, run `apply_memex_pass` against all 36 packets in a throwaway parallel vault (`--no-vault-write` flag if it exists, or `--dry-run`, or just `rm -rf` after). Inspect the entity list. If quality looks right, the fix is shippable.

### Phase 5 — Ship

- Commit on `feature/latest2`.
- Tests should cover: stopword filter, URL-fragment rejection, length floor, corroboration (if you went there), specific known-junk inputs producing zero candidates.
- The fix lands in production via the normal `feature/latest2 → develop → main` deploy path.
- After deploy, the next CSI cron tick will produce clean output. No backfill needed unless the user asks for one — the v1 vault's existing junk entities can be left alone (or cleaned up in a separate sweep).

---

## Hard constraints from the operator

- **The vault is throwaway.** Don't try to preserve v2 partial state. Don't try to clean v1 retroactively. Both vaults can be `rm -rf`'d if needed. Production CSI cron will rebuild fresh state on its own schedule (08:00, 16:00, 22:00 CT) once the extraction is fixed.
- **Don't swap.** Do not run `claude_code_intel_backfill_v2 --swap-only` or `--revert-swap` until the extraction is fixed AND the operator explicitly approves a fresh full backfill.
- **Don't touch production directly.** Work in `/home/ua/dev/universal_agent` on `feature/latest2`. Read `/opt/universal_agent` only as reference. Production is currently healthy at `main @ f4c793e2`.
- **Tier-2 work goes through PR.** Per `docs/deployment/ai_coder_instructions.md`, autonomous code-mutation work in this codebase is supposed to go through worktree → patch → PR → merge, not direct push to `feature/latest2`. If you're a top-level Claude Code session driven by the operator (tier-1), direct commits to `feature/latest2` are fine and the operator runs `/ship` to promote.

---

## Open questions the new session should resolve early

1. **What domain taxonomy do we want?** The current code only emits `kind="entity"` (or sometimes `kind="concept"`, when called explicitly). For CSI we likely want at least: `product` (Claude Code, Claude Managed Agents, Claude Opus 4.7), `feature` (outcomes loop, rate limits, MCP webhooks), `concept` (multiagent orchestration, prompt caching), `person` / `handle` (rlancemartin, hackingdave), `event` (Code with Claude conference). Decide the taxonomy before writing the prompt.
2. **What model to call?** Production runtime uses ZAI-mapped Claude Sonnet for cheap inference. CSI URL judging already runs through `csi_url_judge._call_llm_structured` which uses the Anthropic SDK with the production base_url. The new extractor should follow the same pattern. Check: does the new model need the structured-output / `tools` mode for reliable JSON, or can it return JSON in plain text?
3. **Should we also fix the v2 backfill `IsADirectoryError` in the same session?** Probably yes — same file (`claude_code_intel_replay.py:281`), ~5 LoC fix, removes a confound for future backfill runs and is a free win.
4. **Should concepts come from the same call or a separate pass?** A single extraction call could return both entities and concepts in one structured response (e.g. `{"products": [...], "features": [...], "concepts": [...], "people": [...]}`). Probably cleaner than the existing two-call pattern in `wiki/llm.py`.
5. **What does "good output" look like at scale?** Define the eyeball-quality bar before iterating. E.g.: "running over a representative packet, ≥80% of returned entities are something a human reader would also flag as worth a vault page; <5% junk; multi-word entities preserved as multi-word; no t.co slugs; no English stopwords." If the new session can't articulate this, it'll iterate forever.

---

## Production state at the time of writing

- `/opt/universal_agent` on `main @ f4c793e2` — healthy, services up
- `feature/latest2` tip: `2aa096d5` (recovery postmortem + Followups #1-3 doc commits)
- Production CSI cron: enabled, schedule `0 8,16,22 * * *` America/Chicago, last successful fire at 16:00 CDT 2026-05-07 (cron_result_31.md should exist), next fire at 22:00 CDT 2026-05-07
- v1 canonical vault: `/opt/universal_agent/artifacts/knowledge-vaults/claude-code-intelligence/` — 44 entities (15-20 plausible, ~24 junk), 102 sources, 1 concept
- v2 partial vault: `/opt/universal_agent/artifacts/knowledge-vaults/claude-code-intelligence-v2/` — 100 entities (~30 plausible, ~70 junk), 90 sources, 0 concepts. **Throwaway.**
- vp-mission `df2c39bb` (the rogue docstring cleanup): parked / parked_manual / agent_ready=0
- Codie capture branch: `origin/codie/docstring-cleanup-task-hub @ 57c6d4e6` (preserved, not merged)

---

## Files this session worked in

- `docs/operations/2026-05-07_codie_rogue_branch_recovery.md` — the incident postmortem (committed)
- `docs/operations/2026-05-07_open_followups.md` — three follow-ups, including the verified Followup #3 root cause (committed)
- `docs/proactive_signals/knowledge_extraction_redesign_context_2026-05-07.md` — **THIS FILE**

The next session should add its own working notes / decision log. Suggested location: `docs/proactive_signals/knowledge_extraction_redesign_<date>.md` or similar.

---

## One-liner for the new session's first action

Read this file, then:

```bash
cd /home/ua/dev/universal_agent
git status
git log --oneline -5

# Read the bad extractor first
sed -n '530,700p' /opt/universal_agent/src/universal_agent/services/claude_code_intel_replay.py

# Read the LLM alternative
sed -n '80,150p' /opt/universal_agent/src/universal_agent/wiki/llm.py

# Pick a test packet
ls /opt/universal_agent/artifacts/proactive/claude_code_intel/packets/2026-05-06/

# Read its actions.json
cat /opt/universal_agent/artifacts/proactive/claude_code_intel/packets/2026-05-06/210011__ClaudeDevs/actions.json | head -100
```

Good luck.
