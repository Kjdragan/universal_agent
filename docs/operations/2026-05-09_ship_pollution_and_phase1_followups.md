# /ship pollution & Phase 1 Hacker News follow-ups (2026-05-09)

**Status:** Highly-visible follow-up index. Read this first when picking the work back up.
**Author context:** Phase 1 of the Hacker News dashboard tab integration shipped to `main` at SHA `c5f91c57` on 2026-05-09 (~01:16 CDT). Two `/ship` cycles during the work both hit working-tree-pollution issues serious enough that the canonical `/ship` workflow couldn't run cleanly and we had to fall back to manual ref-pointer ff-merges. This document captures **why** that happened, what to fix, and what's left of the Hacker News work.

---

## TL;DR

- ✅ Phase 1 Hacker News tab is **shipped and live**. Cron `*/30 * * * *` will populate it on its own. No further action required for Phase 1 to start working.
- ⚠️ **`/ship` from `/opt/universal_agent/` is broken in practice** because that checkout is being used as both the production target *and* an agent scratch directory. Until that's fixed, run `/ship` from `~/dev/universal_agent` instead.
- 🐛 Two pre-existing **real runtime bugs** in `gateway_server.py` were surfaced during PR review and tracked as GitHub issues #176 (`should_mirror`) and #177 (`resolve_opus`). Neither is introduced by Phase 1.
- 🔧 Two **Phase 1 implementation follow-ups** were called out during PR-174 review but explicitly deferred: `page.tsx` size (1349 LOC) and `refresh_now` synchronous handler.

---

## What shipped tonight

| SHA | Description | PR |
|---|---|---|
| `109024c6` | Phase 1 implementation plan committed to docs | (squashed/inline) |
| `06e89d88` | CI workflow fix: ruff ignore + F821 noqas to unblock PR-174 | [#175](https://github.com/Kjdragan/universal_agent/pull/175) |
| `752bbc7a` | Phase 1 Hacker News dashboard tab + snapshot pipeline | [#174](https://github.com/Kjdragan/universal_agent/pull/174) |
| `0c6a9cf3` | Install script fix: download raw binary, not `.tar.gz` | (direct push to `feature/latest2`) |
| `c5f91c57` | Override `$HOME` for CLI to honor Phase 1 storage decision | (direct push to `feature/latest2`) |

`origin/main` advanced from `109024c6 → c5f91c57`. Deploy workflow run [25593879709](https://github.com/Kjdragan/universal_agent/actions/runs/25593879709) succeeded.

---

## Issue 1: `/ship` pollution from `/opt/universal_agent`

### What kept happening

On both `/ship` attempts during this work session, the script's **auto-commit step** tried to sweep large numbers of untracked files (1,358 files the first time, 245 the second time) into a `chore: deployment auto-commit via /ship` commit. The pre-flight syntax check also failed once on five broken untracked test files in `tests/unit/` that were left over from an earlier autonomous patch run.

The recovery both times was identical: reset local branch refs back to origin, manually fast-forward `develop` and `main` to the SHA we wanted to ship via `git update-ref`, then push the refs (no working-tree commits, no `git checkout`). That worked cleanly because **everything we wanted to ship was already on `origin/feature/latest2`** — only ref-pointer advances were needed.

### Root cause: production checkout is also a development scratch directory

`/opt/universal_agent/` is the production target — it's where the deploy workflow syncs `origin/main` into. But it's *also* used by:

| Source | What it leaves behind |
|---|---|
| **CODIE / autonomous-mission branches** that ran in-place against `/opt/universal_agent/` | `.py.bak` files, half-written test stubs, debug scripts |
| **Multiple Claude Code sessions** working in `/opt/universal_agent/` simultaneously | `.claude/session_work_products/`, scratch markdown, exploration scripts, work products from running tasks |
| **MCP servers writing state** | `.agent/run_workspace/`, various caches |
| **Manual operator exploration** | PNGs, scratch `.md` files at the root |
| **Crashed / killed agents** | Half-finished outputs that never got cleaned up |

`.gitignore` doesn't cover most of this. `git status --short` shows them as `??` untracked. **Nothing automatically deletes them.** The pollution accumulates indefinitely.

### Why `/ship` becomes hostile in this state

The `/ship` script has this step:

```bash
if [ -n "$(git status --porcelain)" ]; then
    git add .
    git commit -m "chore: deployment auto-commit via /ship"
```

This is designed for the "AI coder forgot to commit a small change" case — it assumes the working tree is **mostly clean**. In `/opt/universal_agent/`, the working tree is **never clean** — it's been accumulating untracked junk for months. So `/ship`'s auto-commit grabs *everything* and tries to ship it as one mega-commit, which then includes:

- Embedded git repos (`.claude/skills/last30days`, etc.) that produce "adding embedded git repository" warnings
- Half-finished agent work products
- Random screenshots
- Scratch debug scripts

Plus there's the **multi-session race condition**: if a second Claude Code session is also working in `/opt/universal_agent/` (writing files for its own task), those writes show up as `??` files when `/ship` runs. They get swept into the chore commit. The two sessions don't collide directly, but their *effects* collide through git state.

### Recommended fix: three options ranked by effort

#### Option A — Habit change: stop running `/ship` from `/opt/universal_agent` (zero code changes, do this now)

`/ship` is documented as **checkout-agnostic** — it works from any clone with the right git remote and a working `gh` CLI session (see `.claude/commands/ship.md` header). Run `/ship` from `~/dev/universal_agent/` instead, where the working tree is clean.

**Practical:** Start a Claude Code session rooted in `~/dev/universal_agent/` when you intend to `/ship`. Or use Antigravity Remote-SSH pointed at that directory. The session that does code changes can stay in `/opt/universal_agent/`, but the session that runs `/ship` should be in the dev tree.

This solves the immediate problem with zero engineering effort.

#### Option B — Add a safety gate to `/ship` (small workflow PR)

Modify `/ship` to refuse running if the working tree has more than N untracked files (say, 50). The error would tell you to either clean the tree or run from a different checkout. Single-file change to `.claude/commands/ship.md` and `.agents/workflows/ship.md`.

The pre-flight `STRAY=$(find ... .py.bak ...)` check is already there — extending it to count untracked files generally is straightforward.

#### Option C — Improve `.gitignore` + periodic cleanup (real systems work)

Audit the 245+ untracked files in `/opt/universal_agent/`. Decide which are legitimately project state (commit them) vs ephemeral debris (add to `.gitignore` or delete). Stand up a janitor cron that prunes:

- `/opt/universal_agent/.claude/session_work_products/` past N days
- `.agent/run_workspace/` past N days
- Root-level scratch files

Document where AI agents are *supposed* to write scratch files (per CLAUDE.md `CURRENT_RUN_WORKSPACE` / `UA_ARTIFACTS_DIR` patterns).

This is the "real fix" but it's days of work and depends on understanding what every agent in this system writes where.

### Recommendation

- **Tonight (already done):** Phase 1 shipped via manual ff-merge workaround.
- **Tomorrow / immediate:** Adopt Option A as a habit — `/ship` runs from `~/dev/universal_agent/`, never from `/opt/universal_agent/`.
- **Next session of similar size:** Implement Option B as a small workflow PR. ~30 minutes of work.
- **Future quarterly maintenance:** Option C as a janitor task.

---

## Issue 2: Pre-existing runtime bugs in `gateway_server.py` (filed as separate issues)

During PR-174 review, ruff's F821 (undefined name) gate surfaced **three findings** in `gateway_server.py`. After investigation:

| Line | Symbol | Verdict |
|---|---|---|
| `~24309` | `_activity_db_path` | Defensive `if "_activity_db_path" in globals() else None` pattern. Safe at runtime; ruff just can't see it statically. Suppressed with `# noqa: F821`. |
| `~25445` | `should_mirror` | **Real bug.** `dashboard_system_command()` references the variable but never assigns it. This code path raises `NameError` at runtime if exercised. Filed as **issue [#176](https://github.com/Kjdragan/universal_agent/issues/176)**. |
| `~26795` | `resolve_opus` | **Real bug.** `vision_describe()` calls `resolve_opus()` but the function (defined at `utils/model_resolution.py:82`) is **not imported** in this file. `vision_describe` raises `NameError` if called. Filed as **issue [#177](https://github.com/Kjdragan/universal_agent/issues/177)**. |

PR #175's noqas are documented gaps with FIXME comments pointing to these issues, **not** silent suppressions. Both bugs are pre-existing — not introduced by the HN work.

The fact that production hasn't crashed because of either suggests these specific code paths haven't been exercised recently. They should still be fixed.

---

## Issue 3: Phase 1 Hacker News implementation follow-ups (deferred per PR review)

Two concerns called out during PR-174 review but explicitly deferred to Phase 1.5:

### `web-ui/app/dashboard/hackernews/page.tsx` is 1,349 LOC

The Phase 1 plan estimated ~280 LOC for this file. The agent honored the visual contract 1:1 with all 8 panels rendered inline (top stories, movers, heated, pulses, show/ask, hiring, status). The result is one large file rather than the typical multi-component breakdown.

**Acceptable for Phase 1.** A follow-up PR could split into:

```
web-ui/app/dashboard/hackernews/
├── page.tsx              (~150 LOC — page composition)
├── _components/
│   ├── TopStoriesPanel.tsx
│   ├── MoversPanel.tsx
│   ├── HeatedPanel.tsx
│   ├── PulseTile.tsx
│   ├── ShowHnPanel.tsx
│   ├── AskHnPanel.tsx
│   ├── HiringPanel.tsx
│   └── StatusPill.tsx
└── _theme.ts             (color tokens, fonts)
```

Cosmetic — improves maintainability. **No functional impact.**

### `POST /refresh` runs `build_snapshot` synchronously inside the FastAPI handler

The "Refresh now" button in the Hacker News tab calls `POST /api/v1/hackernews/refresh`, which runs the full `build_snapshot()` cycle (sync + 8 read commands) inside the request handler. Worst-case wall time is ~90 seconds.

The dashboard proxy at `web-ui/app/api/dashboard/gateway/[...path]/route.ts` has a 30-second default attempt timeout (`UA_DASHBOARD_GATEWAY_PROXY_ATTEMPT_TIMEOUT_MS`). **Therefore "Refresh now" will time out for slow ticks** and the user will see an error, even though the cron itself works fine.

**Fix options:**

1. **Bump `UA_DASHBOARD_GATEWAY_PROXY_ATTEMPT_TIMEOUT_MS`** for this endpoint specifically (cleanest)
2. **Make refresh fire-and-forget** — return 202 Accepted immediately, run `build_snapshot()` in a background task. UI polls `/health` for completion.
3. **Use FastAPI's `BackgroundTasks`** dependency — same idea, framework-native.

**Acceptable for Phase 1** because the cron handles 99% of the actual work — Refresh-now is a "nice to have" that just isn't worth blocking Phase 1 to get right.

---

## Tomorrow morning: verification checklist

Run these on the VPS (in any session, doesn't matter where) to confirm Phase 1 is working as expected:

```bash
# 1. Confirm the deploy actually landed
ls /opt/universal_agent/scripts/install_hackernews_cli.sh   # should exist
ls /opt/universal_agent/src/universal_agent/api/routers/hackernews.py
ls /opt/universal_agent/src/universal_agent/services/hackernews_snapshot_service.py

# 2. Confirm the binary is in place
/opt/universal_agent/bin/hackernews-pp-cli --version  # should print 1.0.0

# 3. Confirm the cron registered. Visit /dashboard/cron-jobs and look for
#    `hackernews_snapshot` in the list. Should have a recent run.

# 4. Confirm the snapshot file exists (created by the first cron tick)
ls -la /opt/universal_agent/artifacts/hackernews/latest.json
ls /opt/universal_agent/artifacts/hackernews/snapshots/ | head -5

# 5. Visit /dashboard/hackernews — should render all 8 panels with real HN data.
#    The status pill in the top bar should say "idle · synced Nm ago" in green.

# 6. Check the CLI's local SQLite store landed in the project tree
ls -la /opt/universal_agent/var/hackernews/.local/share/hackernews-pp-cli/data.db
ls -la /opt/universal_agent/var/hackernews/.config/hackernews-pp-cli/

# 7. If the UI shows "cold start — awaiting first sync" hours after deploy,
#    the cron may not have fired. Check the gateway log:
tail -100 /opt/universal_agent/gateway.log | grep -i hackernews
```

### If something is wrong

| Symptom | Likely cause | Fix |
|---|---|---|
| Tab missing from sidebar | UI didn't deploy cleanly | Check Next.js build / restart `web-ui` |
| Tab loads but pill shows "cold start" >2h | Cron didn't fire | Check `/dashboard/cron-jobs` for the `hackernews_snapshot` job; manually trigger if needed |
| Pill shows "error · last good Xh" | CLI binary or sync failing | `tail -200 /opt/universal_agent/gateway.log | grep -i 'hackernews\|cli'` for stderr |
| `binary missing` in logs | Install script wasn't run on production | `bash /opt/universal_agent/scripts/install_hackernews_cli.sh` |

If everything looks broken: trigger the cron manually via the API endpoint:

```bash
curl -X POST -b "<your dashboard cookie>" \
  https://uaonvps/dashboard/api/dashboard/gateway/api/v1/hackernews/refresh
```

Or directly via the CLI (bypasses gateway / cron entirely, just to prove the binary works):

```bash
HOME=/opt/universal_agent/var/hackernews \
  /opt/universal_agent/bin/hackernews-pp-cli sync
```

---

## Pointer references

| Topic | Location |
|---|---|
| Phase 1 implementation plan (canonical contract) | [`docs/integrations/hackernews_phase1_plan.md`](../integrations/hackernews_phase1_plan.md) |
| Visual contract (HTML prototype) | `work_products/media/stitch/hackernews/index.html` |
| Phase 1 PR | [#174](https://github.com/Kjdragan/universal_agent/pull/174) (merged at `752bbc7a`) |
| CI workflow fix PR | [#175](https://github.com/Kjdragan/universal_agent/pull/175) (merged at `06e89d88`) |
| Issue: `should_mirror` NameError | [#176](https://github.com/Kjdragan/universal_agent/issues/176) |
| Issue: `resolve_opus` missing import | [#177](https://github.com/Kjdragan/universal_agent/issues/177) |
| Deploy run | [25593879709](https://github.com/Kjdragan/universal_agent/actions/runs/25593879709) (success) |
| Phase 2 ideas (deferred) | [`docs/integrations/hackernews_phase1_plan.md` § 9](../integrations/hackernews_phase1_plan.md#9-phase-2--explicitly-deferred-catalog-only) |

---

## Sign-off

Phase 1 is shipped. The cron will run on its own. The `/ship` pollution issue is a habit/tooling problem, not a code problem — it has clear options to fix tomorrow without blocking anything tonight.
