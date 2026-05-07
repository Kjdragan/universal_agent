#!/usr/bin/env bash
# Universal Agent — SessionStart hook for Claude Code on the web.
#
# Why this exists:
#   When a Claude Code web session boots, the harness gets a fresh
#   ephemeral container with the repo cloned but NO virtual environment.
#   Without one, the agent cannot run `uv run pytest`, `uv run ruff`, or
#   `uv run python -c "compile(...)"` — meaning the safety net the rest
#   of the team relies on (tests + lint before commit) silently doesn't
#   exist for the web sandbox. This hook closes the gap by populating
#   `.venv/` from `uv.lock` exactly once when the session starts.
#
# Scope:
#   Only fires inside Claude Code on the web (gated by $CLAUDE_CODE_REMOTE).
#   Local dev sessions (Antigravity, plain `claude`) keep their existing
#   workflow — they own their own env management.
#
# Idempotency:
#   `uv sync --frozen` is a no-op when the venv is already current.
#   We additionally skip even calling it when pytest + ruff are already
#   present, so warm container caches add ~50ms on session start.
#
# Failure mode:
#   Soft. We never `exit 1` — a sync failure prints a clear warning and
#   the session continues. Better to start with a degraded env than to
#   block the session entirely.

set -uo pipefail

# Web-only. Local dev manages its own env.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
    exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(pwd)}" || exit 0

# Sanity: only run when we're actually inside this repo.
if [ ! -f pyproject.toml ]; then
    echo "[session-start] no pyproject.toml in $(pwd); skipping uv sync."
    exit 0
fi

# Warm-start fast path.
if [ -x .venv/bin/pytest ] && [ -x .venv/bin/ruff ] && [ -x .venv/bin/python ]; then
    echo "[session-start] .venv warm (pytest + ruff present); skipping uv sync."
    exit 0
fi

# Cold start. Make sure uv is on PATH.
if ! command -v uv >/dev/null 2>&1; then
    echo "[session-start] WARNING: uv not on PATH. Install with 'pip install uv'."
    echo "[session-start] Continuing without sync; agent can read/edit but cannot run tests."
    exit 0
fi

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

exit 0
