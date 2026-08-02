#!/usr/bin/env bash
# Universal Agent — SessionStart hook for Claude Code on the web.
#
# Why this exists:
#   When a Claude Code web session boots, the harness gets a fresh
#   ephemeral container with the repo cloned but NO virtual environment
#   and NO /opt/universal_agent/.env. Without a venv, the agent cannot
#   run `uv run pytest`, `uv run ruff`, or `uv run python -c
#   "compile(...)"` — meaning the safety net the rest of the team relies
#   on (tests + lint before commit) silently doesn't exist for the web
#   sandbox. Without secrets, every Infisical-backed tool is dead. This
#   hook closes both gaps when the session starts.
#
# Scope:
#   Only fires inside Claude Code on the web (gated by $CLAUDE_CODE_REMOTE).
#   Local dev sessions (Antigravity, plain `claude`) keep their existing
#   workflow — they own their own env management.
#
# Steps:
#   1. venv     — `uv sync --frozen` (skipped when warm; idempotent).
#   2. secrets  — if the claude.ai environment config supplies the
#                 INFISICAL_* machine-identity vars, run
#                 scripts/cloud_env_export.py and append its shell-quoted
#                 `export KEY=...` lines to $CLAUDE_ENV_FILE, which the
#                 harness sources before each subsequent Bash command.
#                 (ANTHROPIC_* and GH_TOKEN/GITHUB_TOKEN are excluded —
#                 see the exporter's docstring.)
#
# Failure mode:
#   Soft. We never `exit 1` — a sync or secret failure prints a clear
#   warning and the session continues. Better to start with a degraded
#   env than to block the session entirely.

set -uo pipefail

cd "${CLAUDE_PROJECT_DIR:-$(pwd)}" || exit 0

# --- git hygiene (ALL sessions, local and web) -------------------------------
# Keeps local `main` at origin/main, prunes dead worktrees, and deletes branches
# whose work already landed. Without this the desktop checkout drifts — it hit
# 339 commits behind, 35 worktrees and 413 branches — and agents then read STALE
# files and report stale conclusions from them.
#
# The rest of the tooling only ever DEFENDED against that drift (CLAUDE.md says
# "branch from origin/main instead"; guard-fresh-branch.sh denies branching off a
# stale base). Defending is not fixing. This actually removes the drift.
# Safety contract and per-step guards: scripts/git_hygiene.sh.
#
# The origin/main fallback is load-bearing, not belt-and-braces. This checkout is
# routinely PARKED on an old task branch (it sat on fix/scratch-index-dir-links
# with 40 dirty files while main was 339 commits ahead). On such a branch
# scripts/git_hygiene.sh does not exist in the working tree, so a bare
# `[ -f ... ]` test silently skips — leaving the sweep disabled on precisely the
# checkout that drifts. Run the canonical copy from origin/main instead, and
# fetch first so that ref is current on a cold session.
if [ -f scripts/git_hygiene.sh ]; then
    timeout 60 bash scripts/git_hygiene.sh 2>&1 || \
        echo "[session-start] git hygiene skipped (non-fatal)"
else
    git fetch --quiet origin 2>/dev/null || true
    if git cat-file -e origin/main:scripts/git_hygiene.sh 2>/dev/null; then
        echo "[session-start] working tree predates scripts/git_hygiene.sh; running the origin/main copy"
        git show origin/main:scripts/git_hygiene.sh 2>/dev/null \
            | timeout 60 bash -s 2>&1 \
            || echo "[session-start] git hygiene skipped (non-fatal)"
    fi
fi

# Web-only below this line. Local dev manages its own venv.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
    exit 0
fi

# Sanity: only run when we're actually inside this repo.
if [ ! -f pyproject.toml ]; then
    echo "[session-start] no pyproject.toml in $(pwd); skipping."
    exit 0
fi

# --- 1. venv -----------------------------------------------------------------
if [ -x .venv/bin/pytest ] && [ -x .venv/bin/ruff ] && [ -x .venv/bin/python ]; then
    # Warm-start fast path: warm container caches add ~50ms on session start.
    echo "[session-start] .venv warm (pytest + ruff present); skipping uv sync."
elif ! command -v uv >/dev/null 2>&1; then
    echo "[session-start] WARNING: uv not on PATH. Install with 'pip install uv'."
    echo "[session-start] Continuing without sync; agent can read/edit but cannot run tests."
else
    echo "[session-start] cold start — running 'uv sync --frozen' (timeout 5 minutes)..."
    sync_start=$(date +%s)

    # Capture exit code separately from the pipe so a failure isn't masked
    # by tail's exit code (the same pipe-to-tail bug that masked 12 broken
    # tests in pr-validate.yml until 2026-05-07).
    sync_output=$(timeout 300 uv sync --frozen --no-progress 2>&1)
    sync_exit=$?
    echo "$sync_output" | tail -10

    sync_elapsed=$(( $(date +%s) - sync_start ))
    if [ "$sync_exit" -eq 0 ]; then
        echo "[session-start] uv sync OK (${sync_elapsed}s)"
    else
        echo "[session-start] WARNING: uv sync exited ${sync_exit} after ${sync_elapsed}s"
        echo "[session-start] Continuing — agent can still read/edit; tests + lint will fail until sync succeeds."
    fi

    # Spot-check that the tools we care about resolve. This catches a venv
    # that uv populated but in some pathological state where pytest/ruff
    # are missing (e.g. partial install).
    for tool in pytest ruff python; do
        if [ ! -x ".venv/bin/${tool}" ]; then
            echo "[session-start] WARNING: .venv/bin/${tool} missing after sync."
        fi
    done
fi

# --- 2. secrets -> $CLAUDE_ENV_FILE -------------------------------------------
if [ -z "${INFISICAL_CLIENT_ID:-}" ]; then
    echo "[session-start] no INFISICAL_CLIENT_ID in the environment config; skipping secret export."
elif [ -z "${CLAUDE_ENV_FILE:-}" ]; then
    echo "[session-start] CLAUDE_ENV_FILE unset by harness; skipping secret export."
elif [ -x .venv/bin/python ]; then
    if .venv/bin/python scripts/cloud_env_export.py >> "$CLAUDE_ENV_FILE"; then
        echo "[session-start] secrets appended to CLAUDE_ENV_FILE (see the [cloud-env] line for the fetch result)."
    else
        echo "[session-start] WARNING: secret export failed; continuing without vault secrets."
    fi
else
    echo "[session-start] WARNING: .venv missing; cannot run the secret export."
fi

exit 0
