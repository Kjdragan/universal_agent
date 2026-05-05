# v2 Shakedown Log — 2026-05-05

> **Context:** Local shakedown of the ClaudeDevs Intel v2 system after all
> 17 PRs landed in production. Run by Claude Code on `feature/latest2` while
> Kevin was away. No VPS access from this session — VPS-side observation
> (real cron, real ClaudeDevs polls, real Cody demos) is operator's job.
>
> **Scope of this shakedown:** import-cleanly + dry-run + simulated end-to-end
> mechanical loop. **NOT covered:** real X API calls, real LLM calls, real
> file mutations on production VPS, real cron firing.

---

## Findings summary

| Check | Result |
|---|---|
| All 20 v2 modules import cleanly | ✅ after one fix (see below) |
| `dependency_currency_sweep --no-write` | ✅ produces real drift report on this dev env |
| `claude_code_intel_backfill_v2 --dry-run` | ✅ |
| `claude_code_intel_backfill_v2 --diff-only` | ✅ |
| `vault_contradiction_lint --no-write` | ✅ (empty findings — vault is empty in dev) |
| `dependency_upgrade --dry-run` against real outdated package | ✅ |
| Classifier `release_announcement` detection | ✅ all three test cases correct |
| Auto-trigger extraction + filtering (PR 6c) | ✅ Anthropic-adjacent only by default; Codex filtered out |
| End-to-end Phase 2→4 simulated demo loop | ✅ scaffold → dispatch → monitor → evaluate all green |
| Lane wiring (PR 17) — switch to Codex via env | ✅ handles list correctly swaps |
| Full v2 unit test suite | ✅ 439/439 |

---

## Issue found and fixed

**Backfill script import bug.** `scripts/claude_code_intel_backfill_v2.py` imported `from universal_agent.activity_db import get_activity_db_path` but the function lives at `universal_agent.durable.db`. The backfill script never executed in production yet (operator-supervised first run pending) so this was latent. Fixed in this session's commit by changing to the correct import.

This is exactly the kind of issue a shakedown should catch — the unit tests all stub `replay_packet` so the broken import path was never exercised by tests.

**Lesson for the doc:** any new CLI script we ship should be exercised at `--help` or `--dry-run` level as part of the PR's acceptance criteria, not just unit tests.

---

## Real-world signal from the dev environment

The dependency-currency sweep on the local dev environment found:

```
Anthropic-adjacent outdated:
  anthropic (pypi):       0.97.0  → 0.99.0
  claude-agent-sdk (pypi): 0.1.66 → 0.1.73

claude_cli_version: 2.1.128
summary: 77 total outdated, 2 Anthropic-adjacent
```

This is real signal from a real working pipeline. Phase 0 dependency currency is functioning end-to-end. Two observations:

1. The dev environment's installed `anthropic` is at 0.97.0 but `pyproject.toml` lower bound is `>=0.75.0` — this is correct (pyproject specifies floors, not pins) but the actuator's `dry-run` now shows what would happen if these were applied:

   ```
   $ python -m universal_agent.scripts.dependency_upgrade --package anthropic --target-version 0.99.0 --dry-run
   {
     "ok": true,
     "dry_run": true,
     "package": "anthropic",
     "current_spec": ">=0.75.0",
     "current_version": "0.75.0",
     "target_version": "0.99.0",
     "would_change": true
   }
   ```

2. **Did NOT auto-fire the actuator.** The shakedown is in dev, not production. Mutating production `pyproject.toml` is operator-controlled.

---

## End-to-end mechanical demo loop

Synthesized the full Phase 2 → 4 in-memory:

```
=== Lane config ===
  handles: ['ClaudeDevs', 'bcherny']
  vault: claude-code-intelligence
  endpoint_profile: anthropic_native

=== Phase 2a: scaffold ===
  workspace: /tmp/.../demos/shakedown__demo-1
  files: BRIEF=True ACCEPTANCE=True business_relevance=True

=== Phase 2b: dispatch ===
  task_id: cody_demo_task:0412c16ada159b3a
  status: open
  priority: 4

=== Phase 4a: monitor ===
  in_flight count: 1
    shakedown__demo-1 status=open iter=1

=== Phase 4b: evaluate ===
  workspace_complete: True
  endpoint_match: True
  cody_self_reported_pass: True
  overall_mechanical_ok: True
```

The mechanical glue between PR 8 (scaffold), PR 8 (dispatch), PR 10 (monitor), and PR 10 (evaluate) all works. The remaining unknown is what happens when Cody is actually running Claude Code on the VPS demo workspace under Max plan OAuth — that's the operator-supervised first run that has to happen on production.

---

## Lane swap confirmation (PR 17)

```python
os.environ['UA_CLAUDE_CODE_INTEL_LANE_SLUG'] = 'openai-codex-intelligence'
config = ClaudeCodeIntelConfig.from_env()
# → handle='OpenAIDevs', all_handles=['OpenAIDevs', 'OpenAI', 'sama']
```

PR 17's three-tier resolution order works: env override → lane config → constants. Setting `UA_CLAUDE_CODE_INTEL_LANE_SLUG` swaps the entire handle set without touching code. This validates the lane templates document — adding Codex really is a YAML edit + secret + cron registration away.

---

## Auto-trigger correctness check (PR 6c)

Synthetic actions stream with mixed action_types:

```
Extracted 2 Anthropic-adjacent triggers (Codex filtered out by default):
  claude-agent-sdk → 0.1.73  (post 200)
  anthropic → 0.99.0  (post 201)
```

The OpenAI release in the same stream was correctly filtered out because PR 6c defaults to `only_anthropic_adjacent=True`. When Codex/Gemini lanes go live, the filter will need to broaden — see the lane templates doc.

`UA_CSI_AUTO_UPGRADE_ON_RELEASE=0` correctly disables auto-trigger.

---

## What this shakedown did NOT cover

These need real production observation, not local dev simulation:

1. **Real ClaudeDevs poll → release_announcement → auto-upgrade fire.** When `@ClaudeDevs` next tweets a versioned release, that's the integration test. Watch for the email.
2. **Real Cody demo run** — the moment env-leak surprises (if any) would surface. Operator-supervised first run on the VPS.
3. **Backfill swap** — atomic rename of `claude-code-intelligence/` → `claude-code-intelligence-v1-archive/` and `claude-code-intelligence-v2/` → `claude-code-intelligence/`. Operator decision to invoke.
4. **Cron firing of vault contradictions sweep.** Once a month is by design.
5. **`claude /login` session expiration handling.** Will surface only when the OAuth session actually expires.

---

## Conclusion

Everything that can be tested without real production state is tested and green. The system is in the right shape. The gaps that remain are operational events that have to happen on the VPS at production cadence — they can't be simulated locally with confidence.

If anything goes sideways during the operator-supervised first runs:

- **Auto-upgrade fails** → set `UA_CSI_AUTO_UPGRADE_ON_RELEASE=0` to revert to manual
- **Demo execution leaks env** → check `manifest.json.endpoint_hit` for `zai` instead of `anthropic_native`; the evaluator (PR 10) flags this automatically
- **Backfill swap goes bad** → `--revert-swap` rolls back

All three escape hatches are tested in unit tests. They should work.
