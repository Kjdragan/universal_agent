# Operator Runbook — 2026-05-07 Handoff Follow-ups

**Audience:** Kevin / on-call operator with `ssh ua@uaonvps` access and the
GitHub admin UI for `Kjdragan/universal_agent`.

**Why this doc exists:** the 2026-05-07 session handoff catalogues seven
follow-up items. Two of them (Item 7, Item 3) are landed in this branch
as code changes. The remaining four (Items 1, 4, 5, 6) require live
side-effects on the VPS or in the GitHub admin UI that the dev sandbox
cannot perform. This runbook is the authoritative checklist for them.

Treat each section as independently runnable. Items are listed in
priority order: complete Item 1 first (Phase 2/3 verification gates
Item 6), then Items 4 and 5 in any order, then Item 6 if Item 1
returns outcome (a).

> **Cross-ref:** the original handoff narrative lives in
> `docs/proactive_signals/csi_v2_next_session_priorities_2026-05-06.md`
> and the session handoff notes for 2026-05-07. The Agent-Type → Workflow
> Matrix referenced in Item 5 is in
> `docs/deployment/ai_coder_instructions.md`.

---

## Item 1 — CSI v2 Phase 2/3 smoke verification

**Status at handoff:** unverified. A bash one-shot smoke fires roughly
one hour after commit `5682fc5` deploys; it writes to
`/tmp/csi_smoke_result.log` on the VPS. Until that file is read and
interpreted, Phase 2 → Phase 3 has not been proven end-to-end on
production (the CLAUDE.md "Production Verification Rule #2" gate is
open).

### Step 1.1 — Read the smoke log

```bash
ssh ua@uaonvps 'cat /tmp/csi_smoke_result.log'
```

The log records, in order:

* timestamp + the JSON returned by the manual CSI fire endpoint
* a `sqlite3` query against `/opt/universal_agent/state/task_hub.db`
* a directory listing of `/opt/ua_demos/`

### Step 1.2 — Confirm the Task Hub producer fired

If the smoke log is missing (e.g. `/tmp` was wiped by a reboot), run
the queries inline:

```bash
ssh ua@uaonvps "sqlite3 /opt/universal_agent/state/task_hub.db \
  \"SELECT task_id, source_kind, status, title \
    FROM task_hub_items \
    WHERE source_kind = 'cody_scaffold_request' \
    ORDER BY created_at DESC LIMIT 5;\""
```

### Step 1.3 — Confirm Phase 2 wrote a workspace

```bash
ssh ua@uaonvps 'ls -la /opt/ua_demos/'
```

### Step 1.4 — Decide outcome

| Outcome | What it means | Next action |
|--------|---------------|-------------|
| (a) Rows present **and** new `<entity>__<id>/` directory beyond `_smoke` | Phase 2 → Phase 3 chain executed end-to-end on production for the first time. v2 is live. | Mark v2 verified. Proceed to Item 6 (backfill). |
| (b) Rows present, only `_smoke` in `/opt/ua_demos/` | Producer worked. Simone failed to claim, OR the `cody-scaffold-builder` skill failed mid-run. | Investigate `services/cody_scaffold.py` and the latest Simone heartbeat session log. Do NOT run Item 6. |
| (c) No rows at all | No tier-3 organic post entered the system in the smoke window. | Wait for organic tier-3 announcement (Code with Claude posts likely within 24h) or run a synthetic positive test. Re-check Item 1 after 24h. |

### Step 1.5 — Record the result back in the session handoff doc

Edit
`docs/proactive_signals/csi_v2_next_session_priorities_2026-05-06.md`
to mark Item 1 with outcome (a/b/c) and the verification timestamp.

---

## Item 4 — VPS `gh` CLI cleanup

**Status at handoff:** something at `/home/ua/.local/bin/gh` is
shadowing the real `/usr/bin/gh`. Argparse-style CLI of unknown
provenance. Cleanup is non-destructive (move, not delete).

```bash
ssh ua@uaonvps
# Drop the shell's cached resolution before any path edits.
hash -d gh
# Move (don't delete) the wrong gh out of the way.
mv ~/.local/bin/gh ~/.local/bin/gh-broken
# Force /usr/bin/gh to win even if PATH order regresses.
echo 'alias gh="/usr/bin/gh"' >> ~/.bashrc
source ~/.bashrc
# Verify.
which gh                  # expected: /usr/bin/gh
gh --version              # expected: gh version 2.x.x
gh auth status            # expected: logged in to github.com as Kjdragan
```

If `gh auth status` reports "not logged in", run
`gh auth login --hostname github.com --git-protocol https` and follow
the device-flow prompt with the `Kjdragan` account.

---

## Item 5 — GitHub branch protection (UI configuration)

**Status at handoff:** no branch protection enforced. The
Agent-Type → Workflow Matrix in `docs/deployment/ai_coder_instructions.md`
relies on these rules to keep tier-2 (autonomous) bots from pushing
directly to `feature/latest2`.

Open
[Settings → Branches](https://github.com/Kjdragan/universal_agent/settings/branches)
and apply the following classic ruleset (or migrate to the new
"Repository rules" UI — semantics are equivalent):

### `main`

* Restrict push: deploy bot only (the GitHub Actions deploy workflow
  runs as `github-actions[bot]`).
* Require status checks: `pr-validate.yml` (deploys are PR-based via
  the merge from `develop` → `main`).
* Require linear history: yes.
* Allow force pushes: no.

### `develop`

* Require pull request before merging: yes.
* Require status checks: `pr-validate.yml` must pass.
* Require approvals: 1 (Kevin or another core maintainer).
* Allow force pushes: no.

### `feature/latest2`

* Allow direct push: only `kjdragan` and the Claude Code GitHub app
  (`claude-app[bot]` / similar — confirm the exact app login name in
  the GitHub Apps page).
* Require pull request for everyone else (this is the gate that
  forces autonomous-mission service accounts onto the tier-2 PR
  workflow).
* Require status checks: `pr-validate.yml` must pass.
* Allow force pushes: no.

When the new "Repository rules" UI is preferred, encode the same
constraints there. Document the chosen ruleset name in this section
once applied so future audits do not reapply.

---

## Item 6 — v2 historical backfill (gated on Item 1 outcome a)

**Do NOT run** until Item 1 returns outcome (a). Backfill performs an
atomic vault swap and can park the live `claude-code-intelligence`
vault if the parallel one is bad.

The script lives at
`src/universal_agent/scripts/claude_code_intel_backfill_v2.py`. The
atomic swap helpers are `swap_vaults` (lines 279-331) and `revert_swap`
(lines 334-368).

### Recommended sequence

```bash
ssh ua@uaonvps
cd /opt/universal_agent

# 1. Count the packets that will be replayed (no writes).
uv run python -m universal_agent.scripts.claude_code_intel_backfill_v2 --dry-run

# 2. Replay packets through the new prompt + trust_source bypass into a
#    parallel vault at /artifacts/knowledge-vaults/claude-code-intelligence-v2/.
uv run python -m universal_agent.scripts.claude_code_intel_backfill_v2

# 3. Diff canonical vs parallel (file counts).
uv run python -m universal_agent.scripts.claude_code_intel_backfill_v2 --diff-only

# 4. (manual) Inspect the parallel vault by hand.
ls -la /artifacts/knowledge-vaults/claude-code-intelligence-v2/
# Spot-check a few entity pages for grounded content.
```

Only after the manual inspection looks healthy:

```bash
# 5. Atomic swap: parallel becomes canonical, old canonical becomes
#    -v1-archive.
uv run python -m universal_agent.scripts.claude_code_intel_backfill_v2 --swap-only
```

If the swapped vault behaves badly (downstream agents emit junk):

```bash
# 6. Revert: archive becomes canonical again, current is parked as
#    -rolledback.
uv run python -m universal_agent.scripts.claude_code_intel_backfill_v2 --revert-swap
```

### Other flags

| Flag | Meaning |
|------|---------|
| `--profile <name>` | Infisical deployment profile |
| `--queue-task-hub` | Re-queue Task Hub items (off by default to avoid duplicates) |
| `--no-vault-write` | Run the replay without writing the parallel vault — useful for timing |
| `--stop-on-error` | Halt on the first packet failure |
| `--overwrite-archive` | Allow `--swap-only` to clobber an existing archive |

---

## Verification checklist (when all four are done)

* [ ] `/tmp/csi_smoke_result.log` read; outcome (a/b/c) recorded.
* [ ] `which gh` returns `/usr/bin/gh` on the VPS as user `ua`.
* [ ] Branch protection rules applied to `main`, `develop`,
  `feature/latest2`.
* [ ] (If Item 1 outcome (a)) backfill swap performed; canonical vault
  is the v2-grounded one.

When all four are checked, this doc has done its job. Future operator
runbooks for unrelated handoffs go in their own dated file under
`docs/operations/`.
