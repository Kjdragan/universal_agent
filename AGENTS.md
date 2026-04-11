# Repository Agent Rules

## Problem-Solving Philosophy

When investigating or fixing issues, always solve the **root cause** holistically — never just cure symptoms with band-aids. Before implementing a fix, ask:

1. **Can we expand capabilities** rather than restrict them? (e.g., raise a system limit rather than cap our data to fit under it)
2. **Is there a proper architectural pattern** for this? (e.g., write large data to files instead of stuffing it into env vars)
3. **Are we losing information or functionality** with this approach? If yes, find a better way.

Defensive guards and safety nets are acceptable as a *last resort backstop*, but they must not be the primary fix. The primary fix should eliminate the problem at its source.

## Code-Verified Answers

When answering questions about how this system works — architecture, data flows, service interactions, agent pipelines, or any behavioral claim — you **MUST read the actual source code first** before responding. Do not answer from memory, assumptions, or general knowledge.

**Mandatory process:**

1. **Read before you speak.** If the user asks "how does X work?", open and read the relevant source files before forming your answer. Use `grep_search`, `view_file`, and `find_by_name` to locate the code.
2. **Cite what you find.** Reference specific files, functions, and line numbers that support your explanation. If you cannot point to actual code, say "I need to check the code" — do not guess.
3. **Never fabricate pipeline steps.** This system has complex multi-agent pipelines (email triage, heartbeat dispatch, daemon sessions, VP orchestration). These have specific intermediaries, classifiers, and routing logic. Do not simplify or omit steps you haven't verified exist or don't exist.
4. **Distinguish what you know from what you're inferring.** If you've read the code and it's clear, state it with confidence. If you're extrapolating beyond what the code shows, explicitly flag it as an inference.
5. **When in doubt, investigate more.** It is always better to spend an extra 30 seconds reading code than to give a wrong answer that wastes the user's time and erodes trust.

**Why this matters:** A confident but incorrect architectural explanation is worse than saying "let me check." The user relies on accurate descriptions of their own system to make decisions. Wrong answers about agent pipelines, email flows, or session lifecycle can lead to flawed design decisions downstream.

## Deployment Contract

This repository has exactly one supported application deployment path. Production is branch-driven and automated; **do not recommend or use ad hoc `ssh`, `rsync`, `git pull`, or manual VPS deployment flows**.

1. **Staging:** Merge reviewed feature work into `develop`. This deploys automatically to staging on the VPS checkout at `/opt/universal-agent-staging` via GitHub Actions. (Pull requests into `develop` constitute the single Codex review gate).
2. **Production:** Promote the validated `develop` SHA to `main` via the manual GitHub Actions promotion workflow. This is a direct, exact-SHA fast-forward; there is no second PR review on `main`. This deploys to production on the VPS checkout at `/opt/universal_agent` (or `/opt/universal_agent_repo` as a fallback).

**Canonical deployment docs:**

- `docs/deployment/architecture_overview.md`
- `docs/deployment/ci_cd_pipeline.md`
- `docs/deployment/infisical_factories.md`

**Canonical CI/CD workflow files:**

- `.github/workflows/deploy-staging.yml`
- `.github/workflows/deploy-prod.yml`

**CI/CD Access:**

- Tailscale CI access: GitHub Actions -> `TAILSCALE_OAUTH_CLIENT_ID` + `TAILSCALE_OAUTH_SECRET` -> tag identity `tag:ci-gha`.

**Legacy artifacts:**

- `scripts/vpsctl.sh` is a break-glass diagnostics helper, not the normal deployment path.

## Local Development Mode

This repo has a dedicated local-dev workflow for running the full stack on Kevin's workstation against real Infisical secrets, without persistently modifying the VPS. This section applies to **every** agent working in this repo — Claude Code, Antigravity IDE, Codex, or any other harness.

**Canonical guide:** `docs/development/LOCAL_DEV.md`. Read it before writing anything about local dev.

### Two states

The system is in exactly one of two states at any moment:

- **State A (NORMAL):** VPS runs everything. Default. This is the production path. CI/CD deploys land here.
- **State B (DEV):** `scripts/dev_up.sh` was run on Kevin's desktop. VPS conflict services are **paused** via SSH hot-swap, and an equivalent local stack runs on 8001/8002/3000 wrapped in `infisical run --env=local`. A pause-stamp file on the VPS (`/etc/universal-agent/dev_pause.stamp`) marks the window and is auto-released by a VPS-side reconciler timer if the user forgets to run `dev_down.sh`.

### Entry points

- `scripts/dev_up.sh` — enter State B.
- `scripts/dev_down.sh` — exit State B. Always run this before `git push`.
- `scripts/dev_status.sh` — read-only snapshot.
- `scripts/dev_reset.sh` — destructive, wipes `~/lrepos/universal_agent_local_data/`. Gated by a confirmation phrase.
- `scripts/install_vps_dev_pause_reconciler.sh` — one-time VPS-side safety-net timer installer.

In Claude Code, these are also available as slash commands: `/devup`, `/devdown`, `/devstatus`, `/devreset` (see `.claude/commands/dev*.md`).

### Rules every agent must enforce

1. **Never push to `develop` or `main` while State B is active.** A deploy will restart the paused VPS services and collide with the local stack (Telegram long-poll, Discord bot, queue workers). If the user asks to push, first confirm they have run `scripts/dev_down.sh` (or suggest running it). Check `scripts/dev_status.sh` if unsure.

2. **Never recommend writing Infisical credentials to `.env`, `.env.local`, or any other file on disk.** The only credentials on disk are the three bootstrap values in the user's `~/.bashrc` (`INFISICAL_CLIENT_ID`, `INFISICAL_CLIENT_SECRET`, `INFISICAL_PROJECT_ID`). Every runtime secret is injected in-memory by `infisical run --env=local --projectId=$INFISICAL_PROJECT_ID --`. The legacy `scripts/bootstrap_local_hq_dev.sh` is deprecated precisely because it writes secrets to `.env`.

3. **Treat the `local` Infisical env as production-adjacent.** It was seeded as a one-time copy of `production`, so local dev hits real Slack/Discord/Telegram/AgentMail/Redis/Postgres unless the user has explicitly overridden those env vars. Never suggest code that would spam a production channel, DB, or API from local dev without making the risk explicit.

4. **Do not edit `.github/workflows/`** as part of the local-dev workflow. Local dev is a runtime concern, not a CI/CD concern. The deploy pipeline should have zero knowledge of State B.

5. **Keep the VPS unit list in sync.** If you change the `VPS_CONFLICT_SERVICES` / `VPS_CONFLICT_TIMERS` arrays in any of `scripts/dev_up.sh`, `scripts/dev_down.sh`, `scripts/dev_status.sh`, or `scripts/install_vps_dev_pause_reconciler.sh`, update all four.

6. **Before claiming the Web UI is fixed, verify it in a browser.** The standard browser-debugging rule applies: navigate to `http://localhost:3000`, reproduce, then edit. Do not claim a UI-visible bug is fixed on inspection of code alone.

## Documentation

All documentation for this project MUST reside exclusively within the `docs/` directory. Creating any other documentation directories (such as `OFFICIAL_PROJECT_DOCUMENTATION/`) is strictly prohibited.

When asked to update or create documentation:

1. **Always Check the Indexes First**: You must consult `docs/README.md` and `docs/Documentation_Status.md` before proceeding.
2. **Update Over Create**: If a document already exists for your topic, update the existing file rather than creating a new one.
3. **Log New Documents**: If you must create a new file, you are required to add a link and description of that new file to both `docs/README.md` and `docs/Documentation_Status.md`.
4. **No Unindexed Files**: No document should exist in `docs/` without being linked from one of the two index files.

## Review guidelines

These guidelines apply when Codex reviews pull requests targeting `develop`.

- Flag any code that logs, stores, or transmits PII or secrets without explicit redaction.
- Verify that every new or modified API route is wrapped by the appropriate authentication/authorization middleware.
- Flag blocking I/O (database calls, HTTP requests) that runs inside an async event loop without `await` or proper executor offloading.
- Verify that background tasks and service loops handle exceptions so they don't silently die.
- Flag Python code that imports secrets or API keys from environment variables directly instead of using the Infisical secret service (our canonical secrets provider — never `.env` files or `os.getenv` for secrets).
- Flag changes that touch `.github/workflows/deploy-staging.yml` or `.github/workflows/deploy-prod.yml` if the corresponding canonical docs in `docs/deployment/` were not updated in the same PR.
- Do not flag formatting-only issues (whitespace, line length) unless they break a linter gate.
- Treat typos in user-facing strings or documentation as P1.

If you are the codex agent, then: """# Browser debugging rules

When working on frontend bugs, local web apps, or browser-based verification:

1. Use the browser MCP tools instead of guessing.
2. Start by navigating to the local app URL.
3. Reproduce the bug in the browser.
4. Inspect screenshots and page state.
5. Inspect failed network requests if relevant.
6. Only then edit code.
7. After edits, retest in the browser to confirm the fix.

Do not claim a UI bug is fixed unless it has been verified through the browser tools."""
