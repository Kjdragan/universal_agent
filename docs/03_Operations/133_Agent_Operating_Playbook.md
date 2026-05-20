# 133 — Agent Operating Playbook (for Claude Code)

**Last updated:** 2026-05-20
**Audience:** future Claude Code sessions picking up work in this repo
**Status:** living document — append observed failure modes; do not delete history

> Read this BEFORE planning any non-trivial task. It is the distilled lessons from the watchdog restoration session (PRs #367 → #392), where the same agent shipped four PRs of "working" code that each had at least one structural bug only caught by the operator pushing back. The point of this doc is to stop relying on the operator as the QA loop.

---

## 1. Recurring failure modes (observed, not theorized)

Each entry: example from this session → rule violated → preventive habit.

### 1.1 "Shipped code that compiles + passes unit tests but never ran on real data"

- **Example.** PR #367 (`feat(watchdog): proactive activity health framework with pipeline invariants`) shipped Layer-1 (cron registry) plus the first invariant. Tests were green. After deploy, the sidecar payload had `crons: []` because the gateway handler at `gateway_server.py:16019-16060` never passed `CronService.list_jobs()` into `build_proactive_health_payload`. Six PRs later (PR #392 close-out), the sidecar still had `crons: []` and nobody had noticed — see `plans/create-a-plan-to-gentle-pebble.md` § 2 P0a.
- **Rule violated.** CLAUDE.md § Production Verification Rules — Rule 2 ("Phase complete = real artifact on real disk") and Rule 6 ("End-of-PR production smoke is mandatory").
- **Prevent up front.** Before declaring a PR ready, `curl` the actual production endpoint and inspect the payload. If the PR adds field X, prove field X is populated on real data, not just on a fixture.

### 1.2 "Re-introduced the same class of bug in a follow-up PR" (PR #376 → PR #392 wrong-DB cycle)

- **Example.** PR #376 added the `proactive_artifact_digest_delivery` invariant pointing at `activity_events.db` — but the `proactive_artifacts` table lives in `runtime_state.db`. Invariant silently no-op'd (caught `sqlite3.Error`, returned `None`). PR #392 "fixed" it by adding a `runtime_conn` parameter pointing at `/opt/universal_agent/runtime_state.db` — but the *actual* production data lives in `/opt/universal_agent/AGENT_RUN_WORKSPACES/activity_state.db` (266 insight_briefs, 41 claude_code_intel_packets in the last 24h). The fix replaced one wrong DB with another wrong DB. Same class of bug, twice. See `plans/create-a-plan-to-gentle-pebble.md` § 2 P0b.
- **Rule violated.** CLAUDE.md § Code-Verified Answers — "Cite what you find. Reference specific files, functions, and line numbers." Plus Production Verification Rule 3 — "Diagnostic commands must read the canonical resolver, not your guess."
- **Prevent up front.** When the PR you are writing claims to fix a "wrong X" bug from a prior PR, the very first thing to do is verify what the canonical X actually is on production — do not trust the framing of the prior PR. Run `find /opt/universal_agent -name '*.db' -exec sqlite3 {} '.tables' \;` (or equivalent) and locate which DB actually holds the table you want. Cite the file path with a code or shell anchor in the PR body.

### 1.3 "Investigated one symptom while ignoring three other identical failures hiding nearby"

- **Example.** PR #390 chased the YouTube enrichment lag (`youtube_channel_rss` 42h stale). The watchdog also could have shown — but did not — that `reddit_discovery` had been silent ≥8 days, `threads_owned`, `threads_trends_seeded`, and `youtube_playlist` each silent ≥7 days, and `threads_trends_broad` was 45h stale. Of the six CSI adapters, only one had any invariant coverage, and the four dead adapters were invisible to the watchdog by design — see `plans/create-a-plan-to-gentle-pebble.md` § 3 P1a.
- **Rule violated.** CLAUDE.md § Problem-Solving Philosophy — "solve the root cause holistically — never just cure symptoms." Plus the LLM-Native Intelligence Design principle ("raw records → bounded retrieval → synthesis"): the watchdog had the raw records (`csi.db events`) but no probe synthesized "which sources have stopped emitting?"
- **Prevent up front.** Before fixing one red light, enumerate every sibling system of the same class and ask which others would silently fail under the same failure mode. If the answer is non-zero, write a *universal* probe (one invariant covering all N siblings) instead of N targeted probes. The plan's P1a / P1b "universal CSI adapter freshness" and "universal cron staleness" invariants are the canonical pattern.

### 1.4 "Treated 'sidecar written' as 'watchdog working' without verifying payload was non-empty"

- **Example.** Across PR #367, #372, #376, #392 the agent inspected the sidecar file existed and was parseable JSON. Nobody opened it and counted rows. `crons: []` survived four PRs of "verification" because the verification was structural, not semantic. See § 2 P0a in the plan.
- **Rule violated.** Production Verification Rule 5 — "Prove your claim before stating it... Function names lie; bodies don't."
- **Prevent up front.** When verifying a payload, always assert non-emptiness AND a representative invariant on the values (e.g. `len(payload["crons"]) >= 20`, `payload["invariants"][i]["observed"] > 0`). "File exists and parses" is not verification of "feature works."

### 1.5 "Trusted a prior PR's framing of a bug instead of re-deriving from source"

- **Example.** PR #392's commit message says the bug was that PR #376 queried `activity_events.db` when the tables live in `runtime_state.db`. The author of PR #392 — me — read that framing and wired up a `runtime_state.db` connection without independently verifying which DB actually holds production data. The right `runtime_state.db` (`/opt/universal_agent/workspaces/runtime_state.db` has 13 daily_digest rows; `/opt/universal_agent/AGENT_RUN_WORKSPACES/runtime_state.db` has older data) versus the path I picked (`/opt/universal_agent/runtime_state.db`, which has no rows) is the entire bug.
- **Rule violated.** Same as 1.2 — Code-Verified Answers ("Read before you speak") — except the source being trusted was *my own prior commit message*, which is no more trustworthy than a third party's.
- **Prevent up front.** Treat your own prior commit messages with the same skepticism as a stranger's. If a current PR depends on a claim from a prior PR, re-verify the claim against live state. Prior PR descriptions are an information source, not authority.

### 1.6 "Notification system that emits findings nothing consumes"

- **Example.** `proactive_health_notifier.py` emails only on `severity == "critical"`. The morning_briefing fired a `warn` finding that sat in the sidecar for 12+ hours; no email, no Task Hub item, no human action. The sidecar-to-Simone consumer loop was never verified to exist. See § 2 P0c.
- **Rule violated.** "Skill deployed ≠ skill invoked" (CLAUDE.md § Production Verification Rules — Rule 1). The watchdog was deployed, but the consumer half of the loop was not proven to invoke it.
- **Prevent up front.** For every signal produced, name the consumer. Grep for the consumer in `memory/HEARTBEAT.md` (Simone), `.claude/agents/*.md` (sub-agents), and Task Hub task types. If no consumer exists, the signal is dead.

---

## 2. Skills to load up front (and when each fires)

Skills live in `~/.claude/plugins/cache/addy-agent-skills/agent-skills/1.0.0/skills/`. Load them with `/<skill>` at the start of the relevant work, not after a mistake.

| Skill | When to load | What it would have prevented |
|---|---|---|
| `/tdd` (test-driven-development) | Every bug fix. Every behavior change. Always before writing code that claims to fix a prior PR. | Bug 1.4 — would have forced a failing test that asserts `len(payload["crons"]) > 0` on real data BEFORE the code is written, which forces wiring `CronService.list_jobs()` through. |
| `/source-driven-development` | Any time the task touches unfamiliar DB paths, file layouts, config keys, or external services. **Mandatory** when the PR claims to fix a "wrong-X" bug. | Bugs 1.2 and 1.5 — would have forced a `sqlite3 /opt/universal_agent/*.db '.tables'` enumeration before picking a connection target. |
| `/incremental-implementation` | Any multi-phase plan. Any PR that touches more than three pipelines. | Bug 1.3 — would have forced one invariant per merge instead of five invariants in one PR all sharing the same wrong-DB defect. |
| `/code-review-and-quality` | Before declaring any PR ready for merge. Treat it as a self-review pass, not a post-merge cleanup. | Bug 1.4 — would have re-read the sidecar payload and noticed `crons: []`. |
| `/debugging-and-error-recovery` | The instant a symptom is observed. Do NOT loop on grep-and-guess. | Bug 1.5 — would have forced a "reproduce → minimise → hypothesise → instrument" pass instead of trusting the prior commit message. |
| `/source-driven-development` (again) | Specifically before adding a `runtime_conn`/`activity_conn`/any DB connection — the third time this codebase has had the wrong-DB-path class of bug. | Bug 1.2/1.5 — would have made the agent open every candidate `*.db` file and grep for the target table before picking a target path. |
| `/verification-before-completion` | Before claiming a PR is shipped. Before posting "phase complete." | Bug 1.4 + bug 1.1 — `crons: []` and the wrong-DB invariants would have failed an "evidence before assertions" check. |

**Composition.** `/tdd` + `/source-driven-development` is the load-out for any PR in this repo that touches a database, a config path, or an external service. Load both.

---

## 3. Sub-agents to spawn up front (and when each fires)

The Agent tool exposes several sub-agents. Use them for breadth and second-opinion, not just for parallelism.

| Sub-agent | When to spawn | What it would have prevented |
|---|---|---|
| `Explore` | At the start of any task whose surface area exceeds five files. Goal: build a holistic inventory of related systems BEFORE proposing fixes. | Bug 1.3 — an Explore pass over `CSI_Ingester/.../adapters/` would have listed all six adapters, making the "fix one, ignore five" pattern obvious. |
| `Plan` | Before writing code for any change that crosses subsystem boundaries (e.g. cron-registration + invariant-registry + email-notifier). | Bug 1.6 — a planning pass would have asked "who consumes warn findings?" before shipping a notifier that drops them. |
| `general-purpose` (catch-all) | For multi-step research tasks where the question is "how does X actually work in production?" Spawn one agent to read the code, another to read the prod data. | Bug 1.5 — independent verification of the prior PR's framing. |
| `agent-skills:code-reviewer` | After the code is written, before opening the PR. Specifically ask it to check for "did the new code touch real production data on real DB paths?" | Bug 1.2 / 1.5 — a second pass focused on path correctness. |
| `agent-skills:test-engineer` | When test coverage strategy needs design, not just execution — e.g. how to write a test that asserts the cron-registry wire-in actually populates `crons[]` end-to-end. | Bug 1.1 / 1.4 — would have produced a payload-shape test that catches the empty-array regression. |

**Rule of thumb.** If the proposed change touches a system I have not read source for in this session, I do not write code; I spawn `Explore` first.

---

## 4. The workflow that should have been used for the watchdog initiative

What should have happened, in order. Actuals in parentheses.

1. **Holistic inventory (Phase 0).** Spawn `Explore` on `CSI_Ingester/`, `services/proactive_*`, `gateway_server.py` cron registration, and `csi.db`. Build the table of 30+ proactive systems and their last-event timestamps. Decide which ones the watchdog should cover.
   *(Actually: started writing invariants without inventorying. The full 30+ system inventory only got built on 2026-05-20, retrospectively, in `plans/create-a-plan-to-gentle-pebble.md` § 1.5.)*
   **Next time:** no invariant code until the inventory table exists in the PR description.

2. **Framework validation (Phase 1).** Wire Layer-1 (cron registry → sidecar) and verify on prod that `crons[]` is non-empty. Wire ONE invariant end-to-end (probe → sidecar → notifier → email or Task Hub) and verify a synthetic finding round-trips.
   *(Actually: PR #367 shipped the framework + the first invariant, but the `crons[]` wire-in was incomplete. The synthetic-finding round-trip was never verified end-to-end; we only verified the email path in PR #389 weeks later.)*
   **Next time:** the framework PR must include a deploy-time check that the sidecar payload is non-empty and a forced-trigger smoke test of the notifier.

3. **Coverage rollout (Phase 2).** Per-pipeline invariants, but always in pairs: a targeted invariant for the pipeline AND a universal sweep invariant for the class. So when adapter A breaks, the universal sweep catches it even before someone writes a targeted probe.
   *(Actually: only targeted invariants shipped, mostly tied to the one pipeline whose failure had been observed. Four dead adapters stayed invisible.)*
   **Next time:** every targeted invariant is paired with a class-level universal probe.

4. **Self-audit before close-out (Phase 3).** Before declaring the initiative done, run the watchdog against itself: are the invariants actually firing? Are the findings actually being consumed? Are there pipelines with zero coverage?
   *(Actually: WS3/WS4 was declared done in PR #392 with five new invariants pointed at an empty DB. The self-audit happened only after the operator pushed back.)*
   **Next time:** self-audit is a deliverable of the close-out PR, not a follow-up.

5. **Consumer-loop verification (Phase 4).** Confirm Simone's `HEARTBEAT.md` actually reads the sidecar and converts findings into Task Hub items. Confirm at least one warn finding has escalated through that loop in real operation.
   *(Actually: the consumer loop was assumed, not verified. The plan § 2 P0c flags this as still-open.)*
   **Next time:** the close-out PR includes a forced-warn smoke and a screenshot of the resulting Task Hub item.

---

## 5. Pre-flight checklist for the next major task

Paste this at the top of the session (or into a `/loop` run) before starting work.

```
Before starting [TASK]:

[ ] Read CLAUDE.md sections: "Pre-Implementation Reading", "Production Verification Rules",
    "Code-Verified Answers", "Problem-Solving Philosophy".
[ ] Read docs/03_Operations/133_Agent_Operating_Playbook.md (this doc).
[ ] Load skill: /source-driven-development
[ ] Load skill: /tdd
[ ] Load skill: /verification-before-completion
[ ] If surface area > 5 files: spawn Explore sub-agent for holistic inventory FIRST.
[ ] List every sibling system of the same class as the one I'm touching.
    For each sibling, write down: does it fail silently under the same failure mode?
[ ] Holistic ground-truth check on production state BEFORE proposing fixes.
    (Hit the endpoint, dump the table, count the rows. Do not trust prior PR framings.)
[ ] If this PR claims to fix a "wrong-X" bug from a prior PR:
      [ ] Independently verify what the canonical X actually is on prod.
      [ ] Do not trust the prior PR's claim about X.
      [ ] Cite the path/value with a verified shell command in the PR body.
[ ] State explicit assumptions in writing before writing code.
[ ] Write failing test first; only then write the fix.
[ ] After deploy: curl the real endpoint, inspect the real payload, assert non-emptiness.
    "File exists and parses" is not verification.
[ ] Name the consumer of any signal this PR produces. If no consumer exists, the signal is dead.
[ ] Self-audit: run the system against itself before declaring done.
```

---

## 6. Anti-pattern catalog (specific things from THIS session never to repeat)

| # | Anti-pattern (concrete example) | Safer pattern |
|---|---|---|
| 1 | **Shipping `crons: []` for four PRs because the handler never called `CronService.list_jobs()`.** | After every PR that adds a payload field, `curl` the production endpoint and assert the field is non-empty. Make it a checklist item, not an aspiration. |
| 2 | **Wiring `runtime_conn` to `/opt/universal_agent/runtime_state.db` based on a prior PR's framing without checking which DB actually holds `proactive_artifacts`.** | When picking a DB path, run `sqlite3 /opt/universal_agent/*.db '.tables'` (or the `AGENT_RUN_WORKSPACES/*.db` equivalent) and grep for the target table. The path that contains the table wins. |
| 3 | **Investigating youtube_channel_rss in isolation while five other adapters were equally silent in `csi.db`.** | First query: `SELECT source, max(occurred_at) FROM events GROUP BY source ORDER BY 2;`. Then look at the dead ones BEFORE fixing the slow one. |
| 4 | **Shipping five invariants in one PR (PR #392) where five of them share the same DB-path bug.** | Ship one invariant. Verify on prod. Then ship the next. `/incremental-implementation`. |
| 5 | **Declaring "WS3 shipped" while half the new invariants silently pass on an empty DB.** | A passing invariant on an empty table is no evidence at all. Assert `observed > 0` OR explicitly classify the invariant as "fail-open and emit `passed=true, observed=0` is a known no-op." Don't accept silent passes as green. |
| 6 | **Notifier that emails only on `critical` while the morning_briefing fires `warn` findings nobody reads.** | When designing a notifier, write the consumer mapping up front: every severity goes somewhere (email, Task Hub, dashboard, log-only). No severity is dropped silently. |
| 7 | **"Same class of bug, twice" — first PR #376 wrong DB, then PR #392 wrong DB again.** | When fixing a bug whose root cause is "we picked the wrong X," the diagnostic that finds the right X must be in the PR body, not a claim. If the PR body doesn't contain the diagnostic output, the PR is not ready. |
| 8 | **Trusting my own prior commit messages as authoritative.** | Prior commit messages are evidence, not authority. Re-derive the underlying claim against current state. The author of the prior PR (often me) was also making mistakes. |
| 9 | **Verifying "the sidecar file was written" instead of "the sidecar payload contains the data."** | Verification always asserts a semantic property (non-emptiness, expected counts, expected fields populated). Structural verification ("file exists, JSON parses") is necessary but never sufficient. |
| 10 | **No production smoke after merge — relying on the operator to notice when something is broken 12+ hours later.** | The PR is not done when CI is green. The PR is done when a `curl`-against-prod check confirms the new behavior is live. Schedule the smoke as part of the PR, not as a vague follow-up. |

---

## 7. What worked this session (PRs #395-#401) — proof the playbook holds up

The playbook above was authored mid-session as a retrospective on the bugs that motivated the holistic restoration. The SECOND half of the session (PRs #395-#401) then executed the actual fix using the playbook's prescriptions. This section captures what worked — so the next session has positive examples, not just failure modes.

### 7.1 Holistic inventory BEFORE proposing any fix

Started with an Explore subagent enumerating every proactive system in the codebase (cron jobs, CSI adapters, polling schedulers, heartbeat-driven activities, GHA workflows). Returned a compact 36-row table mapping each to its expected output evidence and current watchdog coverage. **Then** cross-referenced against live production: per-source 7-day event counts, sidecar JSON dump, DB scans for proactive_artifacts table location. Only after both passes was the plan written.

Result: caught structural bugs in the watchdog FRAMEWORK (P0a empty `crons[]`, P0b wrong DB) that two prior PRs had failed to detect. Without the inventory, the session would have repeated the "fix one finding at a time" pattern that put us in this hole.

**Habit:** for any restoration / refactor / coverage initiative, spawn `Explore` first. Do NOT propose anything until you have the inventory.

### 7.2 TDD with Prove-It Pattern caught real bugs before they shipped

Every one of the six PRs (#395-#400) was authored failing-test-first:
- P0a: 3 new tests RED with `TypeError: unexpected keyword 'cron_persistence_path'` before adding the parameter
- P0b: 2 new end-to-end tests proved the bug ("stale email row in activity_state.db → invariant should fire but doesn't") before the fix
- P0c: 4 tests RED with `TypeError: unexpected keyword 'task_hub_emit_fn'` before adding the parameter
- P1a: 9 tests covering fresh/stale/never_seen/missing-db states
- P1b: 12 tests covering all failure modes including malformed cron_expr resilience
- P3: 6 tests covering single-tick-no-emit, escalate-at-threshold, no-re-emit-on-4th, counter-reset-on-absence

Each PR's GREEN result was verified by running the watchdog test suite (74 → 80 → 89 → 101 → 106 tests). Not a single test was written after-the-fix.

**Habit:** RED test must produce a meaningful failure message (TypeError on missing param, AssertionError on wrong value). A test that fails for the wrong reason proves nothing.

### 7.3 Source-driven verification of writer → reader DB path

For P0b (the wrong-DB bug), the playbook's lesson said "verify against the actual code, not against a prior PR's framing." Concrete execution: grepped for `INSERT INTO proactive_artifacts` to find the writer in `services/proactive_artifacts.py:205`, traced its `conn` parameter up to `_activity_connect()` in `gateway_server.py:9277`, confirmed it opens `activity_state.db` via `get_activity_db_path()`. Only THEN was the fix written.

**Result:** ended a two-PR cycle (PR #376 → #392) of "fix the wrong DB" by reading code, not prior PR descriptions.

**Habit:** when reviewing a "fixed in PR #X" bug, grep for the ACTUAL function `PR #X` modified and read its current body. Function signatures lie if a PR was later modified; bodies don't.

### 7.4 Stack PRs by structural dependency, ship incrementally

Six PRs in strict order (P0a → P0b → P0c → P1a → P1b → P3), each shippable independently. P0a unblocked P1b (cron list now visible). P0c gave P3 a channel to escalate to. Without the strict order, P1a's findings would have landed on a watchdog that was still partially blind — exactly the failure mode the playbook warned about.

Each PR ~50-300 lines, single-concept, with its own test scope. Auto-merge fired on each as PR-Validate passed.

**Habit:** if the plan has phases, the PRs have phases. Resist the urge to bundle "while I'm in this file" changes. The Doc 132 update became its own PR (#401) for the same reason — code review effort scales with diff size, and bundling makes everything harder.

### 7.5 Production verification AFTER deploy, not just locally

Per CLAUDE.md Rule A (Ship-then-Verify): every merged PR was verified on live prod via SSH+SQL on the VPS, not just via `pytest` locally. Specific checks performed:
- `/api/v1/version` confirming the deployed SHA matched the merge
- `cat proactive_health_latest.json` parsed via Python — confirmed `crons: 22` (was 0), invariant findings shape, parked_tasks count
- `sqlite3 task_hub_items` query — confirmed 2 `proactive_health:*` rows with `needs_review` status created by P0c

The `csi_source_liveness` invariant from P1a was observed FIRING on prod within 3 minutes of deploy — that's the closest thing to a smoke test the system supports.

**Habit:** after auto-merge, wait for deploy SHA on prod, then re-run the same query you used to diagnose the bug. If it now returns the expected fix-state, declare done; if not, treat it as a new bug.

### 7.6 Doc updates as deliverable, not afterthought

Doc 132 (Proactive Health Watchdog) got its update IN THE SAME SESSION as the code, shipped as PR #401. Banner reflects the six-PR work, invariant table grew 10→13, new "Closed-loop notification architecture" section with Mermaid diagram. Doc 133 (this file) updated with this section in the same window.

Cost ~30 minutes of doc work. Avoided the doc-drift mode CLAUDE.md mandates against.

**Habit:** the same PR or a tightly-following PR. Never "I'll update the doc later" — that turns into stale docs that contradict shipped code, which is what the playbook's failure mode #1.5 was about.

---

## 8. Meta-rule: this doc is dynamic

When I make a new mistake in this codebase that the playbook didn't catch, the very next action — before declaring the bug fixed — is to add an entry to § 1 and § 6 covering the new failure mode. Similarly, when a habit from § 2-§ 7 demonstrably catches a bug or accelerates work, add a concrete example to § 7 so future sessions see what GOOD looks like, not just what BAD looks like.

The playbook only earns its keep if it grows with each session — both the mistakes and the wins.

---

## Related documents

- `CLAUDE.md` — § Pre-Implementation Reading, § Production Verification Rules, § Code-Verified Answers, § Problem-Solving Philosophy. These remain the canonical rules; this playbook is the field manual.
- `docs/03_Operations/130_Production_Verification_Rules.md` — the long-form companion to CLAUDE.md's verification section.
- `docs/03_Operations/131_Implementation_Plan_Quality_Standards.md` — Mermaid diagrams, code-verified citations, summary tables; what every implementation plan must include.
- `docs/03_Operations/132_Proactive_Health_Watchdog.md` — the watchdog framework whose construction generated the lessons here.
- `lrepos/universal_agent/plans/create-a-plan-to-gentle-pebble.md` — the holistic restoration plan that triggered this playbook.
