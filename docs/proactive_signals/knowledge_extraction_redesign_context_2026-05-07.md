# Knowledge-Extraction Redesign — Context for Fresh Session

**Created:** 2026-05-07 23:15 UTC
**Updated:** 2026-05-07 23:50 UTC (architecture locked: single rich LLM analysis pass at replay time; all regex-based meaning extraction is deleted, not tuned)
**Audience:** Fresh Claude Code session that picks up the knowledge-extraction-quality work
**Goal of the next session:** Replace the current regex-based "memex" extractor with a single rich LLM analysis pass that takes the full available context (tweet + classifier output + fetched linked sources + existing vault state) and emits structured vault deltas (CREATE / EXTEND / REVISE for entities and concepts, plus relations and summary). Iterate the prompt on a single test packet until output quality is high, then expand to the full corpus. The vault content itself is throwaway right now; the *system* is what matters.

---

## TL;DR for the new agent

1. **The CSI knowledge-extraction system produces low-signal output because it does no meaning extraction at all.** What we call "extraction" today is a regex over tweet text — `[A-Z][a-zA-Z0-9_]{2,}|[a-z][a-zA-Z0-9_]+_[a-zA-Z0-9_]+` plus a 30-word stopword list. It produces entity pages for `the.md`, `and.md`, `for.md`, `flibbertigibetting.md`, `ekyctqscxb.md` (a Twitter t.co URL slug), and so on. The body of each page is the same regex-selected token stitched together with the tweet text and classifier rationale via Python string concatenation. There is no judgment anywhere in the chain.
2. **Operator-mandated architecture: pure LLM-driven analysis. No exceptions, no hedges, no fallbacks.** The redesign is *not* "tune the regex" or "regex pre-filter + LLM validator". Both are forms of code-side meaning judgment, which is the anti-pattern. The redesign is: gather raw materials in code → pass everything to an LLM → LLM returns structured intelligence (entities + concepts + relations + summary) → code persists the structured output. Code never decides what's meaningful. RegEx exists in this design only for *plumbing* (slugifying filenames, parsing JSON, hostname matching) — never for meaning.
3. **CLAUDE.md "LLM-Native Intelligence Design" rule is the governing doctrine.** *"Prefer using LLM reasoning over building custom Pythonic pseudo-reasoning systems... avoid creating elaborate programmatic trend/theme/scoring systems that attempt to imitate reasoning over small-to-medium corpora."* The current regex extractor is the canonical anti-pattern that rule was written to prevent. Read that section of CLAUDE.md before writing any code.
4. **The architectural insight: don't add a separate "extractor" step.** The pipeline already has two LLM passes (classifier at poll time, URL judge at replay time). The fix is to add **one more rich LLM analysis pass at replay time** whose structured output IS the vault delta — entities, concepts, relations, summary, all together. The regex memex extractor is *deleted*, not replaced piece-for-piece. See "Target architecture" below.
5. **The slowness in the v2 backfill was NOT from extraction.** 964 z.ai LLM calls in 58 minutes — almost all of those were `csi_url_judge._call_llm_structured` (one LLM call per linked URL, ~15 URLs/packet × 36 packets ≈ 540 calls + retries). Plus URL fetching. The regex extractor was effectively free in compute terms — that's *why* its output is shallow. Adding the new rich analysis call adds ~30-50 LLM calls per backfill on top of the existing budget — small marginal cost.
6. **The vault contents are throwaway.** Don't preserve them. Don't run a full backfill. Don't swap. The operator has explicitly said: rebuild the system, iterate small, throw away test runs freely. Production v1 vault stays canonical until a *good* extractor is built. After the new system is verified, a fresh full backfill produces the real v2 vault.
7. **Model: GLM-5.1, no reasoning/thinking param.** Use `model=resolve_opus()` from `utils/model_resolution.py` — that resolves to `glm-5.1` via the ZAI map. GLM-5.1 has **no** thinking mode (operator-confirmed). Do NOT pass `thinking={...}` or `reasoning_effort` on these calls. Quality comes from a strong system prompt + rich context + structured-output via tool_use, not from a reasoning param. See Phase 2.1 below.
8. **You're working on `feature/latest2` in the dev tree** at `/home/ua/dev/universal_agent`. Production at `/opt/universal_agent` was just recovered to `main @ f4c793e2` and is healthy — leave it alone except as a code-reading reference.

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

### Phase 2 — Target architecture (locked, operator-approved)

**One rich LLM analysis pass at replay time. Its structured output IS the vault delta. The regex memex extractor is deleted, not replaced piece-for-piece.**

#### Pipeline shape after the redesign

```
Poll time (cron) — unchanged:
  Fetch tweets → LLM classifier → save actions.json + posts.json verbatim
                 (emits tier + action_type + reasoning per action)

Replay time — modified:
  For each tier-2/3 action:
    1. URL judge (existing, unchanged) → fetch worthy linked URLs to linked_sources/
    2. NEW: rich-analysis LLM call:
         input  = tweet text
                + classifier output (tier/reasoning, already analyzed)
                + linked_sources/*.md (full text of fetched anthropic docs / blog posts)
                + existing vault entity list (slugs + summaries, for dedup/canonicalization)
         output = structured vault delta (see schema below)
    3. Code: parse the structured output, persist to vault
       (slugify names → filenames, write CREATE/EXTEND/REVISE bodies,
        append to log.md, write _history/ snapshots on REVISE)

  Source pages (sources/*.md): unchanged — verbatim copy of fetched content
```

The `_memex_candidates_for_action` regex extractor and the `_memex_body_for_create` / `_memex_body_for_extend` template synthesizers (`claude_code_intel_replay.py:548-687`) are deleted entirely. They're replaced by:
- A new module — call it `services/csi_intelligence_pass.py` or similar — that builds the prompt, calls the LLM, parses the structured output, and returns a typed `VaultDelta` object.
- A persistence helper that takes the `VaultDelta` and writes/extends/revises files in the vault using existing `wiki/core.py:memex_apply_action` primitives (which DO already exist for CREATE/EXTEND/REVISE — they just have nothing good feeding them).

#### Code's role (strictly plumbing)

| Responsibility | Module |
|---|---|
| Read the packet's `actions.json` + `linked_sources/*.md` | new `csi_intelligence_pass.py` |
| Read existing vault entity list (slug + summary) for context | new helper, or reuse `wiki/core.py` index reader |
| Format the prompt | new |
| Call the LLM (structured output / tools mode) | reuse the same client pattern as `csi_url_judge._call_llm_structured` |
| Parse + validate the JSON response | new (Pydantic model recommended) |
| Persist: slugify names, write/append/revise files | reuse `wiki/core.py:memex_apply_action` (already supports CREATE/EXTEND/REVISE) |
| Append to `log.md` and `_history/` | already done by `memex_apply_action` |

Code does no meaning judgment. The LLM produces the vault delta with full context; the code does the file I/O.

#### LLM structured output schema (proposed; the new session can refine)

```jsonc
{
  "vault_actions": [
    {
      "op": "create",            // create | extend | revise
      "kind": "product",         // product | feature | concept | person | event
      "name": "Claude Managed Agents",     // canonical name (LLM picks from context + existing vault list)
      "aliases": ["managed agents"],       // for dedup against future references
      "summary": "Anthropic's hosted agent runtime that lets developers deploy autonomous agents...",
      "key_facts": [
        "supports webhook subscriptions for event delivery",
        "exposes outcomes-loop primitive for rubric-driven self-improvement"
      ],
      "source_post_ids": ["x_post_2052069321355182447"],
      "source_doc_urls": ["https://platform.claude.com/docs/en/managed-agents"],
      "confidence": "high"       // high | medium | low — LLM's own confidence
    },
    {
      "op": "extend",
      "existing_slug": "mcp",    // LLM matched a tweet about MCP to existing vault entity
      "new_facts": ["webhook delivery now supported in MCP servers"],
      "source_post_ids": ["..."],
      "source_doc_urls": ["..."],
      "confidence": "high"
    }
    // … or "op": "revise" with what_changed + why
    // No vault_action emitted for tweets that contain nothing entity-worthy.
    // (e.g. "Live now at https://t.co/EKyctqSCXB" produces zero vault_actions.)
  ],
  "relations": [
    {"from": "claude-managed-agents", "to": "mcp", "kind": "uses"},
    {"from": "outcomes-loop", "to": "claude-managed-agents", "kind": "feature-of"}
  ],
  "post_summary": "ClaudeDevs announces multiagent orchestration, outcomes loop, dreaming, and webhooks for Claude Managed Agents.",
  "post_tags": ["release_announcement", "managed_agents", "webhooks"]
}
```

Notes on the schema:
- `op: "create"` requires `kind` + `name` + `summary`. The LLM picks the canonical name (multi-word OK, capitalized appropriately).
- `op: "extend"` requires `existing_slug` (LLM is told the existing vault's slugs in the prompt and chooses one) + `new_facts`. Avoids duplicate entity pages.
- `op: "revise"` requires `existing_slug` + `what_changed` + `why`. Triggers a `_history/` snapshot.
- `confidence` is the LLM's self-rating. Code can use it as a routing signal (e.g. low-confidence creates land in a review queue rather than the live vault).
- An empty `vault_actions` list is valid and expected for tweets that contain no entity-worthy content (greetings, retweets of unrelated content, t.co-only posts, joke tweets like the "flibbertigibetting" one).

#### What gets deleted

- `claude_code_intel_replay.py:548-587` — `_MEMEX_TERM_PATTERN`, `_MEMEX_TERM_STOPWORDS`. Gone.
- `claude_code_intel_replay.py:589-627` — `_memex_candidates_for_action`. Gone.
- `claude_code_intel_replay.py:630-687` — `_memex_body_for_create`, `_memex_body_for_extend`. Gone (the LLM now produces the body content as `summary` + `key_facts`).
- `claude_code_intel_replay.py:689-…` — `apply_memex_pass` is rewritten to call the new analysis pass and persist its output via `memex_apply_action`. The function name can stay; only the body changes.
- `wiki/llm.py:extract_entities`, `extract_concepts` — keep (used elsewhere by `wiki_ingest_external_source`), but DON'T wire them into CSI replay. The CSI replay path gets its own purpose-built rich-analysis call. Generic entity/concept extractors are not what we want here.

#### What gets added

- `services/csi_intelligence_pass.py` (or similar) — the new module.
- A Pydantic model file or dataclass module defining the `VaultDelta` schema.
- A prompt-template file (probably under `prompts/` or inline in the module — operator's call) — the CSI-domain-specific system prompt.
- Unit tests in `tests/unit/test_csi_intelligence_pass.py` covering: prompt-building, JSON-parsing, VaultDelta-to-memex-actions conversion, golden-set expectations (see Phase 3).

### Phase 2.1 — Model selection (operator-mandated, hard requirement)

**The new intelligence pass MUST run on GLM-5.1.** This is not negotiable.

GLM-5.1 does **not** have a thinking / reasoning mode. Do not pass `thinking={...}`, `reasoning_effort`, or any extended-thinking parameter on the CSI calls — those are Anthropic-Claude features that don't apply to GLM-5.1. (The operator confirmed this directly. An earlier draft of this doc incorrectly prescribed a thinking budget; that was wrong and has been removed.)

#### Status (already correct in code — no change needed)

`utils/model_resolution.py:31-34` defines the ZAI model map:

```python
ZAI_MODEL_MAP = {
    "haiku":  "glm-5-turbo",
    "sonnet": "glm-5-turbo",
    "opus":   "glm-5.1",       # ← THIS is what we want for CSI analysis
}
```

Both existing CSI LLM call sites already use `resolve_opus()`:

- `services/csi_url_judge.py:306` — `model=resolve_opus()` (URL judging)
- `wiki/llm.py:58` — `model=model or resolve_opus()` (the latent extractor)

The new `csi_intelligence_pass.py` should follow the same convention: `model=resolve_opus()`. That gives you GLM-5.1, end of model-selection story.

#### What you actually need from GLM-5.1

The way to get high-quality output from GLM-5.1 (in the absence of a thinking mode) is through:

- **A strong system prompt** that lays out the taxonomy, examples, and explicit "do not extract" guidance.
- **Rich context in the user message** — full tweet text, classifier reasoning, fetched linked sources, existing vault entity list. The more relevant context the model sees, the less it has to guess.
- **Structured output via tool_use** (same pattern as `csi_url_judge._call_llm_structured`). Forces the model to emit a typed schema instead of free-form prose, which constrains hallucination.
- **Generous `max_tokens` for output** — 4096 is fine; larger if the analysis needs to emit many vault_actions for a packet.

Those four levers are the "reasoning quality" knobs available with GLM-5.1. Tune the system prompt as the primary lever during Phase 3 iteration.

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

1. **What domain taxonomy do we want?** The schema above proposes `product / feature / concept / person / event` — confirm or refine before writing the prompt. Specifically: should `tool` be a separate kind, or just a sub-type of `product`? Should `team` (e.g. "the Claude Code team") be its own kind?
2. **Token budget for the rich-analysis call.** With full packet context (tweet + classifier reasoning + 2-5 fetched linked sources at ~5K tokens each + existing vault entity list), the input prompt may run 20-30K tokens. Add 4096 max_tokens output. Verify GLM-5.1 supports an input context window that big through the Z.AI proxy (Z.AI's docs say GLM-5.1 supports up to 200K context, but verify empirically against `api.z.ai/api/anthropic`). If the prompt overflows, decide whether to truncate linked sources, chunk the analysis, or call per-action instead of per-packet.
3. **Should we also fix the v2 backfill `IsADirectoryError` in the same session?** Probably yes — same file (`claude_code_intel_replay.py:281`), ~5 LoC fix, removes a confound for future backfill runs and is a free win. Use `if metadata_path.is_file()` instead of `if metadata_path.exists()`.
4. **What does "good output" look like at scale?** Define the eyeball-quality bar before iterating. E.g.: "running over a representative packet, every emitted entity is something a human reader would also flag as worth a vault page; zero stub-only entities; multi-word entities preserved as multi-word; no t.co slugs; no English stopwords; aliases captured for canonicalization." If the new session can't articulate this, it'll iterate forever.
5. **Should the rich-analysis pass replace or augment the existing classifier?** Today the classifier runs at poll time and emits tier + reasoning per action. The new analysis runs at replay time. They're separate calls — but they overlap in what they read. Decide whether to: (a) keep both as-is, classifier output is just additional input to analysis (recommended — minimal disruption); (b) extend the classifier to also emit the rich analysis at poll time (more efficient but ignores linked-source content which isn't fetched yet); (c) eliminate the classifier and let the rich analysis do tier classification too (biggest change, biggest risk).

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

# Read the (about-to-be-deleted) regex extractor + body templater
sed -n '530,700p' /opt/universal_agent/src/universal_agent/services/claude_code_intel_replay.py

# Read the existing memex_apply_action persistence helper (KEEP — the new pass will call it)
grep -n "def memex_apply_action\|def memex_create_page\|def memex_extend_page\|def memex_revise_page" \
    /opt/universal_agent/src/universal_agent/wiki/core.py

# Read the existing structured-output LLM call pattern (model the new pass on this)
sed -n '280,330p' /opt/universal_agent/src/universal_agent/services/csi_url_judge.py

# Read the model resolver (confirms glm-5.1 mapping is in place)
cat /opt/universal_agent/src/universal_agent/utils/model_resolution.py

# Pick a test packet — the one that produced "flibbertigibetting" / "EKyctqSCXB" entities
ls /opt/universal_agent/artifacts/proactive/claude_code_intel/packets/2026-05-06/

# Read its actions.json — this is the input shape the new pass will receive
cat /opt/universal_agent/artifacts/proactive/claude_code_intel/packets/2026-05-06/210011__ClaudeDevs/actions.json | head -100

# See what linked sources were fetched for that packet
ls /opt/universal_agent/artifacts/proactive/claude_code_intel/packets/2026-05-06/210011__ClaudeDevs/linked_sources/
```

Then propose: prompt sketch + Pydantic schema. Don't write production code until that's been operator-reviewed.

Good luck.
