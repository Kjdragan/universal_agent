# Autonomous PR + Deploy Flow — Briefing for Other AI Coder

**Date:** 2026-05-11
**Audience:** AI coders working in `kjdragan/universal_agent`. Read this to understand how shipping code from your desktop to production now happens with minimal operator hand-holding.

> **TL;DR:** Push a branch → PR opens → CI runs → auto-merge fires when green → main merges → deploy fires → prod restarts on new SHA. **The `/ship` command is convenient but not strictly required anymore** — most of what it did is now automated by GitHub workflows.

---

## 1. The end-to-end flow in 60 seconds

```
[Kevin's desktop]                          [GitHub]                            [VPS prod]
─────────────────                          ─────────                            ──────────
just dev                                   ────                                  ────
(local dev, autonomous loops off)
edit code
git add . && git commit
git push origin <branch>           ──→     PR opens (manually OR via /ship)
                                           │
                                           ├─ pr-validate.yml runs
                                           │   • py_compile on changed .py
                                           │   • ruff check
                                           │   • pytest tests/unit
                                           │   • artifact tripwire (.py.bak etc.)
                                           │
                                           ├─ pr-auto-merge.yml runs (for claude/* branches)
                                           │   • auto-enables auto-merge (squash)
                                           │
                                           └─ when PR-Validate green + auto-merge enabled
                                              → GitHub squash-merges into main
                                                 │
                                                 └─ deploy.yml fires on push to main
                                                    • Tailscale-connect to VPS
                                                    • rsync code + uv sync deps
                                                    • restart universal-agent-{gateway,api,webui}
                                                    • post-restart health check (8min window)
                                                    │
                                                    └─→ prod healthy on new SHA   ✓
```

Operator's only required action: **push the branch and (if needed) open the PR.** Everything else is automated.

---

## 2. The `/ship` command — what it does now

Pre-2026-05-10, `/ship` was a ~240-line script that orchestrated `feature/latest2 → develop → main` fast-forward merge chain to land code on `main`. After PR #189 (2026-05-10), it was slimmed to ~117 lines:

```bash
/ship       # from any feature branch, inside Claude Code
```

What it does (in order):
1. **Branch guard** — refuses to run from `main` (and from the retired `develop`)
2. **Pre-flight: artifact tripwire** — refuses if `.py.bak`, `.swp`, `.orig` files exist in the working tree (catches half-finished autonomous patches)
3. **Pre-flight: `python compile()` check** — every changed `.py` file must syntax-check (catches `SyntaxError` before CI burns minutes)
4. **Auto-commit** any pending changes (`chore: /ship auto-commit`)
5. **`git push -u origin <branch>` + verify** — re-reads `origin/<branch>` after push and confirms it advanced to local HEAD (silent-no-op-push guard from 2026-05-08)
6. **`gh pr create --base main`** — or if no `gh` CLI, prints the GitHub compare-URL for manual creation
7. **`gh pr merge <PR> --auto --merge`** — enables auto-merge
8. **Exits** — operator walks away

What it explicitly does NOT do (anymore):
- ❌ Watch CI (was `gh run watch` polling loop; now redundant because auto-merge handles it)
- ❌ Merge anything itself (auto-merge does that)
- ❌ Touch develop, feature/latest2 in any chain (develop retired 2026-05-10)
- ❌ Trigger deploy (the merge to main triggers `deploy.yml` automatically)

**Defined at:** `.claude/commands/ship.md` (kept in sync with `.agents/workflows/ship.md`; both files asserted identical by `tests/unit/test_ship_command_pr_only.py`).

**Is `/ship` still necessary?** Strictly no — you can replicate it manually:

```bash
# Manual equivalent
git add .
git commit -m "feat: my change"
git push -u origin my-branch
# Then open PR in GitHub UI and click "Enable auto-merge"
# (Or for claude/* branches, pr-auto-merge.yml does that automatically)
```

`/ship` just bundles those steps and adds the pre-flight syntax/artifact checks that catch dumb mistakes before they burn a CI minute. **Use it when convenient; skip it when you have your own flow.**

---

## 3. The auto-merge automation

Two workflows make PR merging hands-off:

### `.github/workflows/pr-auto-merge.yml`

Triggers on every PR opened against `main` from a `claude/*` branch. Auto-enables GitHub's native auto-merge (squash method) on that PR. Skips PRs from non-`claude/*` branches (you'd have to enable manually for those, OR use `/ship` which does it explicitly).

Why `claude/*` specifically: that prefix is what `claude.ai/code` agents push to by default, so it captures the autonomous-agent ship flow without auto-enabling on operator-driven PRs from arbitrary branches.

### `.github/workflows/pr-validate.yml`

Runs on every PR to `main` (and `feature/latest2`). The gate auto-merge waits for. Steps:
- `py_compile` on every changed `.py` file
- `ruff check .` (whole repo)
- `pytest tests/unit -x -q`
- Tripwire: refuses to pass if `.py.bak`/`.swp`/`.orig` artifacts present

This is the **only pre-deploy gate now** — `develop` used to be a second integration layer; it's gone (see § 4 below).

### Branch protection setting that matters

GitHub repo settings → Branches → `main` rule → **"Require branches to be up to date before merging" is OFF**.

Why: with this ON, auto-merge gets stuck "behind" when main moves after the PR was created — operator would have to click "Update branch" manually on every PR. With it OFF, auto-merge fires the moment CI is green on the PR's current HEAD. Tiny risk of "PR passed its own CI but main moved meanwhile and we don't re-check"; for a solo-dev repo where main moves rarely between CI run and merge, the risk is essentially zero.

---

## 4. The branch model changed (2026-05-10)

**Pre-2026-05-10:**
```
feature branch → develop → main → deploy
```
Operator ship-chained: ff-merge feature into develop, ff-merge develop into main. Each step was a separate `git push`. The `develop` branch was supposed to be staging-mirrored to a separate environment that never materialized.

**Post-2026-05-10 (current):**
```
any branch → PR → main → deploy
```
The `develop` branch was retired (PR #181). `main` is now the only deploy-firing branch. Everything PRs directly there. `feature/latest2` still exists as Kevin's pseudo-trunk by convention, but it's not part of any required chain.

If you see references to `develop` in any current doc, it's a stale reference — flag it.

---

## 5. The deploy workflow

`.github/workflows/deploy.yml` fires on every push to `main`. Steps:
- Connect to VPS over Tailscale
- `rsync` the new code to `/opt/universal_agent/`
- `uv sync` to refresh `.venv` deps
- `systemctl restart universal-agent-{gateway,api,webui,...}`
- Post-restart health check loop: 96 attempts × 5s = 8-minute window, must see `/api/v1/health` return 200

**Important `paths-ignore` filter (PR #181):**

```yaml
paths-ignore:
  - 'docs/**'
  - '**.md'
  - 'reports/**'
  - 'state/**'
  - 'artifacts/**'
  - 'memory/**'
```

Means: docs-only / state-only / memory-snapshot commits to main do **NOT** trigger a deploy. This keeps unrelated PRs (drift sweeps, memory snapshots, etc.) from restarting prod. Mixed code+docs commits still trigger deploy — the safe default.

If you're shipping a code change and want to verify deploy fired: `gh run list --workflow=deploy.yml --limit 3` (GitHub Actions UI also works).

**Deploy failure mode that bit us today:** if `/opt/universal_agent/.venv` exists but was built against a different Python version (e.g., 3.12 instead of 3.13), `uv sync --python 3.13` refuses with "no Python executable was found." Fix: `scripts/deploy_validate_runtime.sh:ensure_existing_venv_is_usable()` (PR #204) now detects this and recreates the venv. Document this for future operators.

---

## 6. Local dev environment (tied into the flow above)

This is the FRONT END of the pipeline — where code is authored before it ever sees GitHub.

### Where dev happens

**Desktop, not VPS.** Kevin works in `/home/kjdragan/lrepos/universal_agent/`. The VPS is production-only.

### How to start dev

```bash
cd /home/kjdragan/lrepos/universal_agent
git fetch origin && git pull --ff-only origin main
uv sync
just dev
```

Gateway (`:8002`) + API (`:8001`) + web-ui (`:3000`) boot in parallel. Ctrl-C tears down.

**All autonomous loops are OFF in dev by default** (heartbeat, cron, dispatch sweep, AgentMail polling, ClaudeDevs intel, etc.). This is critical — it means dev doesn't burn ZAI quota and doesn't collide with prod state.

### To opt a specific loop ON for testing

```bash
echo "UA_DEV_HEARTBEAT_FORCE_ON=1" >> .env
just dev   # now heartbeat ticks; everything else off
```

**Don't use `UA_HEARTBEAT_ENABLED=1` for this** — that variable is IGNORED in dev (defensive against Infisical prod-parity injection). Use `UA_DEV_<NAME>_FORCE_ON=1`.

Full table of dev opt-in vars: `docs/06_Deployment_And_Environments/12_Local_Dev_Environment.md`.

### To check what's gated

```bash
PYTHONPATH=src python -m universal_agent.dev_tools env-report
PYTHONPATH=src python -m universal_agent.dev_tools loop-status heartbeat
PYTHONPATH=src python -m universal_agent.dev_tools cron-list
```

### To pull realistic prod data for local debugging

```bash
python scripts/snapshot_prod_to_dev.py --dry-run    # preview
python scripts/snapshot_prod_to_dev.py              # actual
```

Uses SQLite's online `.backup` over SSH. Refuses to run in production. UA is 100% SQLite end-to-end.

---

## 7. The full lifecycle of a typical change

```
1. Pull latest main:
     git pull --ff-only origin main

2. Cut a branch:
     git checkout -b claude/my-feature
     # or any branch name; "claude/" prefix triggers pr-auto-merge.yml

3. Local dev:
     just dev
     # edit code; gateway/web-ui hot-reload
     # test in browser at localhost:3000
     # Ctrl-C when done

4. Pre-ship sanity (optional but cheap):
     uv run pytest tests/unit -x -q
     uv run ruff check .

5. Commit:
     git add .
     git commit -m "feat: my change"

6. Ship:
     /ship                          # convenience: commits any leftovers, pushes, opens PR, enables auto-merge
     # OR manually:
     git push -u origin claude/my-feature
     # If branch is claude/*, pr-auto-merge.yml auto-enables auto-merge.
     # Otherwise click "Enable auto-merge" in GitHub UI.

7. Wait (auto):
     - pr-validate.yml runs (~30-90s)
     - When green, GitHub squash-merges PR into main
     - deploy.yml fires on the push to main (~1-3 minutes)

8. Verify (optional):
     curl https://app.clearspringcg.com/api/v1/version
     # commit_sha should match the squashed merge commit
     # /api/v1/health should return 200
```

Operator effort post-push: **near zero** for clean changes. Watch your subscribed-PR webhook events if you want notifications; otherwise check back later.

---

## 8. What can fail and how it surfaces

| Failure | Where it surfaces | What to do |
|---|---|---|
| Pre-flight syntax error | `/ship` blocks before push | Fix the syntax in editor, re-run /ship |
| Artifact tripwire (`.py.bak` etc.) | `/ship` or `pr-validate.yml` | `rm` the artifacts, re-ship |
| `pytest` or `ruff` failure | `pr-validate.yml` fails on PR | Push a fix commit to same branch; auto-merge re-evaluates |
| PR Validate flaky | Manual re-run from Actions UI | Re-run job button on the failed check |
| Deploy fails (venv corrupt, network, etc.) | `deploy.yml` red, prod stays on old SHA | Check Actions logs; if venv issue, fix with `scripts/deploy_validate_runtime.sh` logic |
| Stacked PR conflict after parent squash-merges | `mergeable_state: dirty` on child PR | Rebase child onto current `main` (drop the squashed parent's commit) |
| Branch protection "up to date" turned back on | Auto-merge stalls | Turn it OFF in GitHub repo settings → Branches |

You're subscribed to webhook events on PRs you create — CI failures and review comments wake your session automatically. **No polling needed.**

---

## 9. Canonical docs (for deeper reading)

| Topic | Doc |
|---|---|
| One-page operator index | `docs/WORKFLOW.md` |
| Local dev runbook (canonical) | `docs/06_Deployment_And_Environments/12_Local_Dev_Environment.md` |
| Infisical dev env hygiene | `docs/06_Deployment_And_Environments/13_Infisical_Dev_Env_Hygiene.md` |
| Branching + deploy contract | `docs/06_Deployment_And_Environments/04_Branching_And_Release_Workflow.md` |
| CI/CD pipeline (workflow internals + Mermaid) | `docs/deployment/ci_cd_pipeline.md` |
| Task Hub + auto-merge unstick verbs | `docs/03_Operations/107_Task_Hub_And_Multi_Channel_Execution_Master_Reference_2026-03-31.md` § 13.0 |
| `/ship` source | `.claude/commands/ship.md` |
| `/ship` regression guards (proves what /ship does/doesn't do) | `tests/unit/test_ship_command_pr_only.py` |
| Deploy venv-mismatch fix | `scripts/deploy_validate_runtime.sh:ensure_existing_venv_is_usable()` |

---

## 10. Anti-patterns to avoid

1. **Don't push directly to `main`.** It's protected; you can't anyway. Use a feature branch + PR.
2. **Don't run `/ship` from `main` or `develop`.** It refuses (develop has been retired).
3. **Don't expect `/ship` to merge anything.** It opens the PR and enables auto-merge; GitHub does the actual merge.
4. **Don't manually watch CI in a loop.** Auto-merge handles it. Walk away.
5. **Don't develop on the VPS as your default path.** Use desktop + `just dev`. VPS-as-dev (via Antigravity Remote-SSH, see Doc 11) is the fallback when desktop isn't available.
6. **Don't put `UA_HEARTBEAT_ENABLED=1` in your dev `.env`.** It's ignored. Use `UA_DEV_HEARTBEAT_FORCE_ON=1`.
7. **Don't add files to `paths-ignore` in `deploy.yml` without thinking carefully.** The current set (`docs/`, `**.md`, `reports/`, `state/`, `artifacts/`, `memory/`) is deliberate — adding code paths would silently skip prod restarts on real code changes.

---

## 11. PR series that shipped today's flow

| PR | Title | What it changed |
|---|---|---|
| #181 | Retire `develop` branch + add `paths-ignore` to `deploy.yml` | Branch model collapsed to `any → PR → main → deploy`; docs/state changes no longer trigger deploy |
| #189 | Slim `/ship` to commit + push + open PR + enable auto-merge | `/ship` rewritten to minimal modern form; CI-watch loop removed |
| (pre-this-session) | `pr-auto-merge.yml` workflow | Auto-enables auto-merge on `claude/*` PRs to main |
| #199, #200, #202, #206, #211 | Local dev / prod separation initiative | Desktop dev with `just dev`, autonomous loops off, defensive dev-safe semantics |
| #204 | Deploy venv-version-mismatch fix | `ensure_existing_venv_is_usable` now detects and recreates stale venv |
| #212 | Documentation drift sweep | Updated all docs to reflect the new flow (this briefing's source material) |

All merged to `main` and live in production.

---

## Quick test: does the flow actually work?

If you've just absorbed the above and want to verify, do this:

```bash
# Push a tiny doc-only change
echo "" >> docs/WORKFLOW.md
git add docs/WORKFLOW.md
git commit -m "test: trivial doc edit to exercise auto-merge flow"
git push -u origin claude/test-auto-merge-flow

# Then in GitHub:
gh pr create --base main --head claude/test-auto-merge-flow --title "test: auto-merge flow" --body "Testing the pipeline."
# (or open via UI)

# Watch:
gh pr checks <PR-number> --watch
# Should see: PR-Validate runs, goes green, auto-merge fires, PR closes as merged.
# Because of paths-ignore: deploy.yml does NOT run (docs-only change).
```

That's the canonical happy path. If anything stalls, check § 8 above.
