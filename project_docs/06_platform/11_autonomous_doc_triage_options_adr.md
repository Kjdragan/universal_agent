---
title: "ADR: Autonomous doc-drift issue triage & fix — three delivery options"
status: active
canonical: true
subsystem: plat-doc-triage-automation
code_paths:
  - scripts/doc_accuracy_sweep.py
  - scripts/doc_audit.py
  - .github/workflows/doc-nightly.yml
  - .github/workflows/doc-audit.yml
  - .github/workflows/docfix-tripwire.yml
  - .github/workflows/pr-auto-merge.yml
  - src/universal_agent/services/cody_mode.py
  - src/universal_agent/vp/clients/claude_cli_client.py
  - src/universal_agent/backlog_triage.py
last_verified: 2026-06-10
---

# ADR: Autonomous doc-drift issue triage & fix — three delivery options

> **ADR status: DECISION (2026-06-10) = Option (a) as the production posture + "docs-only,
> stamp-and-escalate" fix scope.** The loop runs on Option (a) (desktop interactive Max, proven
> end-to-end on issue #870 → PR #900). **Operator decision (2026-06-10): the desktop is declared
> always-on for this loop**, so Option (a) is the production home — it keeps the triage/fix
> brain on **credit-free Max interactive inference** instead of moving to API billing on the
> VPS. Options **(b)** / **(c)** (always-on VPS forms) are demoted to recorded **contingency
> fallbacks** (§7). The fix scope is **docs-only**: the agent fixes drifted docs, **stamps
> `last_verified`** on every doc it touches (so a fixed doc leaves the audit rotation), and
> **escalates code-comment-rooted drift** to a backlog rather than touching `.py` files. The
> **hybrid (Option 3, §9)** — also fixing stale code comments/docstrings — is the documented
> *future* improvement, deferred until the code-comment backlog justifies it. §9 records the
> limitation that drove this decision and the path to the hybrid.

## 1. Context & problem

The nightly documentation-accuracy sweep (`scripts/doc_accuracy_sweep.py::main`, run by
`doc-nightly.yml`) judges a rotating batch of canonical docs against current code using an
LLM, and when it finds drift it opens a GitHub issue titled `doc-accuracy: drift in N
doc(s) [nightly]` labeled `documentation` (one bare `gh issue create` per run, **no dedup**).

**These issues are a write-only dead-letter.** No process consumes the `documentation`
label: `backlog_triage.py::LABELS` reads only `skill-gap` / `deslop-findings`; the
CI-failure consumer watches *workflow-run failures*, not the content issues a *successful*
sweep produces. So drift findings accumulate unread (e.g. #842, #870 open as of
2026-06-10 with zero engagement).

**Goal:** an autonomous agent that watches the repo's issues, investigates each new one
with **Opus-grade reasoning** (better than the GLM sweep that files them), triages it,
fixes what's genuinely drifted via a docs-only PR, auto-merges it, confirms it, and closes
the issue — or closes it as a false positive, or escalates if unfixable.

## 2. Constraints that shape the options

1. **Inference quality.** The sweep that *files* the issues runs on ZAI/GLM (`glm-5-turbo`,
   via `doc_accuracy_sweep.py::_load_zai_env` → ZAI proxy). Spot-checks put its true-positive
   rate at ~70%; it infers claims from doc *implications* and sometimes flips blame
   direction. The *triage/fix* step therefore wants stronger reasoning (Opus) plus a
   verification pass — not the same GLM that produced the finding.

2. **Inference substrate / Anthropic policy (as of 2026-06).** There is no Max **OAuth**
   on a GitHub Actions runner. Three substrates exist:
   - **Max subscription (interactive)** — real Opus, but only inside an interactive
     Claude Code session; the desktop has rock-solid Max auth, the VPS's is a separately
     minted `claude setup-token` token.
   - **Anthropic API key (Commercial Terms)** — real Opus, works anywhere (CI, VPS,
     unattended), pay-per-token. Anthropic explicitly steers *always-on / shared
     production automation* to an API key.
   - **ZAI/GLM** — cheap, always-on, already the default for UA autonomous work.
   - **Timely caveat:** effective **2026-06-15**, headless `claude -p` / Agent-SDK usage
     on a subscription stops drawing interactive limits and instead draws a *capped,
     non-rollover* monthly "Agent SDK credit" (Max 5x $100 / Max 20x $200), then halts
     unless API billing is enabled. This is why `cody_mode.py::_HARDCODED_FALLBACK_MODE`
     was flipped `anthropic → zai` on 2026-06-07 (commit reason: "Anthropic began
     API-billing the Claude-Code-via-Max SDK path"). See
     [Claude Max OAuth Credentials](07_claude_max_oauth_credentials.md) and
     [Environments](05_environments.md) (profile-3 there is now stale).

3. **Runtime-vs-dev contract — operator exception (2026-06-10).** The general contract says
   nothing operational runs durably on the desktop. The operator granted a **scoped exception
   for this loop**: the desktop is declared always-on, and the loop runs there deliberately —
   it is the only substrate where the triage/fix brain gets real Opus-grade inference
   **credit-free** (Max interactive; constraint 2). Moving it to the VPS would force API
   billing (no Max OAuth survives there reliably, and headless `-p`/SDK is metered from
   2026-06-15). The exception is for *this loop only* — it does not reopen the desktop for
   UA services, timers, or crons, all of which stay on the VPS.

4. **Safety — the docs-only firewall must be mechanical.** A prompt-only "don't touch
   source" rule was bypassed once (2026-04-24 `executing_sessions` deletion). The
   replacement is the standalone always-on `docfix-tripwire.yml` workflow ("Doc-fix PR
   touches docs only", a **required status check** on `main` since 2026-06-10; it lived as
   a path-filtered `doc-audit.yml` job before that, which could not be required): any PR
   on a `docfix/*` branch that
   touches a non-docs path **hard-fails** CI. `pr-auto-merge.yml` already auto-merges
   `docfix/*` (it is not on the manual-review blocklist), and `doc_audit.py::check_symbol_refs`
   enforces symbol citations on every doc PR. So **docs-only auto-merge is safe by
   construction** — the brain choosing *what* to fix is the only variable.

## 3. The three options

### Option (a) — Desktop interactive loop  ✅ *chosen — production posture (operator decision 2026-06-10)*

- **Mechanism.** A persistent **Monitor** (`gh` poll loop) in an interactive Claude Code
  session on the desktop emits an event when a new `documentation` issue appears. The same
  Opus session investigates, fixes docs in a worktree, opens a `docfix/*` PR, and (on
  approval) auto-merges + closes.
- **Inference.** Real Opus on the **Max subscription, interactive mode** — classified by
  Anthropic as *interactive* (stays on normal usage limits, **not** the 2026-06-15 metered
  credit), and fair use for one's own account/repo.
- **Cost.** None beyond Max; consumes interactive quota (5-hour / weekly caps).
- **Reliability.** Runs while the desktop is up and the session is alive. The operator has
  declared the desktop **always-on** (2026-06-10), so the remaining gap is session lifetime:
  a reboot or session exit stops the watcher until a new session re-arms it (poll
  `gh issue list --label documentation` for numbers above the baseline). Issues are durable —
  anything filed during a gap is picked up on re-arm, not lost.
- **Risk.** Sustained 24/7 machine-cadence traffic is a discretionary anti-abuse gray area
  (no documented ban for this exact pattern via the official CLI, no blessed safe-harbor
  either). Keep it strictly single-beneficiary.

### Option (b) — Always-on VPS agent on an Anthropic API key

- **Mechanism.** A VPS service/timer (or a Task Hub consumer) triggered when a
  `documentation` issue is filed, running the same triage→fix→PR loop unattended.
- **Inference.** Real Opus via **`ANTHROPIC_API_KEY`** (Commercial Terms) — the substrate
  Anthropic explicitly recommends for always-on automation; unaffected by the 2026-06-15
  subscription metering.
- **Cost.** Pay-per-token, but **bounded** — only the handful of drifted docs/night
  (~pennies-to-low-dollars/day). Predictable.
- **Reliability.** Always-on, architecture-correct (VPS), no desktop dependency, no Max
  quota contention.
- **Risk.** Real (small) spend; needs an API key provisioned and stored in Infisical.

### Option (c) — ZAI-detect + API-key-fix hybrid

- **Mechanism.** Keep nightly **detection** on the existing ZAI/GLM sweep (cheap, whole
  corpus). Spend the **API key only on the fix-generation + confirmation** step for the
  ~8 drifted docs that survive a verification pass.
- **Inference.** GLM for detection (status quo), Opus-via-API-key for fixes.
- **Cost.** Lowest of the always-on options — API spend only on confirmed fixes.
- **Reliability.** Always-on; best cost/quality split.
- **Risk.** Two-stage plumbing (detector → fixer handoff); detector's ~70% TP rate means
  the fixer's verification pass must reject false positives before editing.

### Comparison

| Axis | (a) Desktop prototype | (b) VPS API-key | (c) ZAI-detect + API-fix |
|---|---|---|---|
| Inference for triage/fix | Opus (Max, interactive) | Opus (API key) | GLM detect / Opus fix |
| Always-on? | Yes — desktop declared always-on (session-bound; re-arm after reboot) | Yes | Yes |
| Cost | Max quota only | Bounded API $ | Lowest API $ |
| Policy posture | Fair-use gray area | Clean (Commercial) | Clean (Commercial) |
| Affected by 6/15 metering? | No (interactive) | No (API key) | No (API key) |
| Best for | **Production (operator decision 2026-06-10)** | Contingency fallback | Contingency, cost-min |
| Build effort | Low (this session) | Medium | Medium-high |

## 4. Decision

**Run on (a) as production** (operator decision 2026-06-10: the desktop is declared
always-on, keeping the loop on credit-free Max interactive inference — moving to the VPS
would force API billing). **(b)/(c) are contingency fallbacks**, adopted only on a §7
trigger; prefer **(c)** over (b) if API cost matters at volume. The fallback migration is
intentionally cheap because all three share the same triage state machine and the same
mechanical safety rails (§5–6) — only the *trigger* (Monitor vs VPS event) and the
*inference substrate* (Max-interactive vs API key) change.

**Fix-scope decision (2026-06-10, operator): docs-only + stamp + escalate (hybrid deferred).**
The first dry run (issue #870 → PR #900) merged correct doc fixes, but the next sweep (#901)
re-flagged the same docs — exposing a structural limit (§9): a docs-only loop cannot converge on
drift whose root cause is a stale *code* comment/docstring. The operator chose to **keep the loop
docs-only**, adding `last_verified` stamping (so fixed docs leave the rotation) + a code-comment
escalation backlog, rather than expand to code edits now. The **hybrid** that also fixes
comment/docstring-only code (§9, "Option 3") is documented for later adoption.

## 5. Triage state machine (shared by all three options)

For each new `documentation` issue:

1. **Investigate** each finding against current `origin/main` code (Opus reads the doc claim +
   the cited code). **Never fix on the GLM's say-so** — verify the symbol/claim in code first; if
   the cited symbol can't be located or the excerpt was truncated, *defer*, don't guess.
2. **Classify** each finding: **REAL_DOC** (doc disagrees with code → fixable here) ·
   **CODE_COMMENT** (the doc is correct but a stale `.py` comment/docstring contradicts the code →
   **escalate**, §9) · **FALSE_POSITIVE** (inference artifact / doc already accurate) ·
   **OPERATIONAL/ASSERTED** (a still-true env/ops fact not in code — do **not** auto-edit) ·
   **UNVERIFIABLE** (cited symbol not found / truncated excerpt → defer).
3. **If any REAL_DOC fixes:** edit the doc(s) on a `docfix/<slug>` branch (docs-only) with symbol
   citations, **and bump `last_verified` to today on every doc touched** (so the doc leaves the
   oldest-verified rotation — skipping this is what made #901 re-flag #900's fixes 15 min after
   merge) → regenerate `README.md` → open PR → `pr-validate` + `docfix/*` tripwire + symbol check
   → auto-merge on green → confirm live on `main` → **close** with a per-finding disposition.
4. **CODE_COMMENT / UNVERIFIABLE / OPERATIONAL** findings: record each in the **code-comment
   escalation backlog** (§9) — never silently drop, never touch the `.py` file from this loop.
5. **If all findings are FALSE_POSITIVE / dispositioned:** close with the explanation.
6. **Never** re-fix a doc the loop already corrected just because the GLM re-flags it — a re-flag
   of a verified-correct doc is the §9 code-comment signal, not new doc drift.

## 6. Safety rails

| Rail | Status | Where |
|---|---|---|
| Docs-only mechanical tripwire | **built + required** | `docfix-tripwire.yml` (always-on, required status check on `main`; hard-fails non-docs paths on `docfix/*`) |
| Auto-merge for docs-only PRs | **built** | `pr-auto-merge.yml` (`docfix/*` not blocklisted) |
| Symbol-citation enforcement | **built** | `doc_audit.py::check_symbol_refs` |
| Structured per-doc findings | **built** | `doc_accuracy_sweep.py::_judge` JSON verdicts |
| `last_verified` stamping on fixed docs | **policy (§5)** | bump frontmatter + regen `README.md` so fixes leave the rotation |
| Code-comment drift → escalation backlog (no `.py` edits) | **policy (§9)** | the docs-only tripwire enforces the boundary |
| Second-pass confirmation (major drift) | **needed** | fix agent self-verifies before commit |
| Escalate-don't-close on unfixable | **policy (above)** | triage step 5 |
| Single-beneficiary only (no token sharing/resale) | **policy** | Consumer Terms §2/§3 |

## 7. Contingency triggers (when to fall back to (b)/(c))

(a) is the production posture; adopt (b) or (c) only when **any** holds:
- Desktop downtime causes a real miss (an issue sat unhandled > 1 day despite the
  always-on declaration — e.g. extended outage, nobody re-armed the watcher after a reboot).
- The loop meaningfully contends with interactive Max quota during coding hours.
- Anthropic tightens interactive-automation enforcement (the §3(a) gray-area risk lands).

## 8. Cross-references & known stale docs

- [Claude Max OAuth Credentials](07_claude_max_oauth_credentials.md) — `setup-token` /
  `CLAUDE_CODE_OAUTH_TOKEN` mechanics; **review needed** for the 2026-06-15 metering.
- [Environments](05_environments.md) — profile-3 now reads "Cody defaults to ZAI/GLM since
  2026-06-07" (corrected in PR #900; ground truth `cody_mode.py::_HARDCODED_FALLBACK_MODE = "zai"`).
  A stale *docstring* inside `cody_mode.py` still lists step-6 = "anthropic" → §9 backlog.
- [Deployment & CI/CD](04_deployment_and_cicd.md) — branch/PR/auto-merge model the
  `docfix/*` path rides on.

## 9. Known limitation: code-comment-rooted drift & the hybrid (Option 3) path

**The limit (found 2026-06-10, first dry run).** A docs-only loop cannot *converge* on any drift
whose root cause is a stale **code comment / docstring**. Concretely: PR #900 fixed
`05_environments.md` to say `_HARDCODED_FALLBACK_MODE = "zai"` (correct — it matches the variable).
The next sweep (#901) re-flagged the doc `major_drift` anyway, because `cody_mode.py`'s own module
docstring still lists *"6. `"anthropic"` — hardcoded last-resort fallback"* — the **code
contradicts itself**, and the GLM judge mis-attributes that to the (now-correct) doc. The docs-only
loop correctly refuses to thrash the doc and refuses to touch the `.py` file (the tripwire forbids
it), so the finding **recurs every rotation** until the *code* comment is fixed. Same shape as the
`intel_lanes.py` *"NOT yet wired"* docstring (`claude_code_intel.py` does wire `get_lane`).

**Why we stayed docs-only (Option 1) for now.** Fixing these means editing `.py` files, which
(1) crosses the docs-only tripwire and (2) triggers a full production deploy via `deploy.yml` even
for a behaviorally-inert comment change. Not worth it per-comment.

**The code-comment escalation backlog.** Until the hybrid ships, CODE_COMMENT / UNVERIFIABLE
findings are escalated (never dropped) to a tracking issue (label `doc-code-comment`). Seeded:
`cody_mode.py` module docstring (step-6 fallback still says "anthropic"); `intel_lanes.py` module
docstring ("NOT yet wired").

**Migration to the hybrid (Option 3) — adopt when the backlog justifies it.** Trigger: the
`doc-code-comment` backlog crosses ~10 open items, **or** the same doc re-flags from code-comment
drift across ≥3 rotations. Then add:
1. **A new mechanical guard** — a *comment/docstring-only diff* check: the PR may change only
   comments, docstrings, and string literals — **zero executable-line changes** (an AST/diff check,
   parallel to the docs-only tripwire). This lets an Opus *code* PR fix the stale comments safely.
2. **Batched, not per-finding** — accumulate the backlog and open **one** Opus code PR per cycle so
   an inert comment change doesn't fire a production deploy per comment. Gate it behind a light
   human approval (it touches `.py`, so it is **not** in the docs-only auto-merge lane).
3. **Keep the docs-only loop unchanged** — the hybrid only *adds* a second, lower-frequency
   code-comment lane; detection stays on the existing sweep. The safe, converging docs-only lane
   runs continuously while the riskier code-touching work stays batched, guarded, and human-gated.

## 10. Re-arming the loop on the desktop (runbook)

The watcher is session-bound: a desktop reboot or session exit stops it (issues filed in the
gap are durable on GitHub and get picked up on re-arm — nothing is lost). To re-arm, start an
interactive Claude Code session on the desktop (Max OAuth) and have it run a persistent
Monitor that polls for new doc-drift issues, e.g.:

```bash
baseline=$(gh issue list --label documentation --state open --json number --jq 'max_by(.number).number // 0')
while true; do
  gh issue list --label documentation --state open --json number,title \
    --jq ".[] | select(.number > ${baseline}) | \"NEW doc-drift issue #\(.number): \(.title)\"" || true
  sleep 300
done
```

On each event, the session triages per §5 (verify against real code → `docfix/*` PR →
`last_verified` stamp → auto-merge behind the required tripwire → close the issue). The
nightly sweep fires ~1:35 PM CT (`doc-nightly.yml`, cron `35 18 * * *` UTC, often delayed
by GitHub), so a same-day re-arm before early afternoon misses nothing.
