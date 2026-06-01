# justfile — operator recipes for Universal Agent
#
# Install just: https://github.com/casey/just
#   Ubuntu/Debian: sudo apt install just
#   Or via cargo:  cargo install just
#
# Run `just` (with no args) to list all available recipes.

# Default recipe: list everything
default:
    @just --list

# Reset the IDE checkout to clean main + remove any merged worktrees.
#
# Same script the Stop hook runs at session end (see .claude/settings.json).
# Safe to run any time — refuses to touch unmerged work, uncommitted
# changes, or the worktree it's running in. Use this when the IDE
# source-control panel is showing phantom state and you just want to be
# back on a clean main.
cleanup:
    @bash scripts/end_session_cleanup.sh

# ---------------------------------------------------------------------------
# Local development environment
# ---------------------------------------------------------------------------

# Spin up the full local dev stack on Kevin's desktop.
#
# What runs: gateway (:8002), API (:8001), web-ui Next.js dev server (:3000).
# What does NOT run: heartbeat tick, cron registration, dispatch sweep,
#                    daemon sessions, VP event bridge, AgentMail polling,
#                    ClaudeDevs X intel polling. All autonomous loops are
#                    OFF by default in dev (`UA_RUNTIME_STAGE=development`).
#
# Prereqs: `bash scripts/bootstrap_local_hq_dev.sh` has been run once and
# `.env` exists at repo root with Infisical bootstrap creds.
#
# See: docs/06_Deployment_And_Environments/12_Local_Dev_Environment.md
dev:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ ! -f .env ]; then
        echo "❌ .env not found at repo root. Run 'bash scripts/bootstrap_local_hq_dev.sh' first."
        echo "   See docs/06_Deployment_And_Environments/12_Local_Dev_Environment.md"
        exit 1
    fi
    if ! grep -q "^INFISICAL_CLIENT_ID=" .env; then
        echo "❌ .env exists but lacks INFISICAL_CLIENT_ID. Re-run bootstrap script."
        exit 1
    fi
    echo "🚀 Starting local dev stack (autonomous loops OFF — UA_RUNTIME_STAGE=development)"
    echo "   Gateway: http://localhost:8002"
    echo "   API:     http://localhost:8001"
    echo "   Web UI:  http://localhost:3000"
    echo "   Ctrl-C to stop all services."
    echo
    # Source .env, then run three services in parallel with prefixed output.
    # `trap` ensures Ctrl-C kills the whole tree, not just the foreground job.
    set -a
    source .env
    set +a
    trap 'kill 0 2>/dev/null; wait 2>/dev/null; echo "🛑 Dev stack stopped."' INT TERM EXIT
    (PYTHONPATH=src .venv/bin/python -m universal_agent.gateway_server 2>&1 | sed 's/^/[gateway] /') &
    (PYTHONPATH=src .venv/bin/python -m universal_agent.api.server 2>&1 | sed 's/^/[api]     /') &
    (cd web-ui && npm run dev 2>&1 | sed 's/^/[web-ui]  /') &
    wait

# Run only the gateway (useful for debugging gateway-specific issues).
dev-gateway:
    #!/usr/bin/env bash
    set -euo pipefail
    set -a; source .env; set +a
    PYTHONPATH=src .venv/bin/python -m universal_agent.gateway_server

# Run only the web UI (useful for frontend-only iteration).
dev-webui:
    cd web-ui && npm run dev

# Re-bootstrap dev env (rewrites .env from Infisical development).
bootstrap:
    bash scripts/bootstrap_local_hq_dev.sh

# Tear down: kill any stray dev processes left over from a crashed `just dev`.
dev-kill:
    -pkill -f "universal_agent.gateway_server" 2>/dev/null
    -pkill -f "universal_agent.api.server" 2>/dev/null
    -pkill -f "web-ui.*npm" 2>/dev/null
    @echo "Stray dev processes killed (if any were running)."

# ---------------------------------------------------------------------------
# Tests / lint
# ---------------------------------------------------------------------------

# Run unit tests (fast, no external deps).
test:
    PYTHONPATH=src uv run --frozen pytest tests/unit -x -q

# Fast local unit run with an explicit per-test fail-fast timeout and no
# cache plugin. Use this — NOT a bare backgrounded `pytest -q` — when iterating
# locally. A bare backgrounded full-suite run buffers all output, so a hung
# test is indistinguishable from a slow one, and several concurrent sessions
# each launching the full suite saturate the host (load spikes turn every
# SQLite fsync into a multi-second stall).
#
# TARGET scopes the run (a path REPLACES the default `tests/unit`); ARGS are
# extra pytest flags. Examples:
#   just test-fast                                  # whole unit suite, fail-fast
#   just test-fast tests/unit/test_loop_control.py  # one file
#   just test-fast tests/unit -k agentmail          # keyword filter within unit
# See: docs/03_Operations/135_Test_Suite_Hardening_And_Local_Run_Runbook.md
test-fast TARGET="tests/unit" *ARGS:
    PYTHONPATH=src uv run --frozen pytest {{TARGET}} -x -q -p no:cacheprovider --timeout=60 {{ARGS}}

# Run a specific test file or test ID (e.g., `just test-one tests/unit/test_loop_control.py`).
test-one TEST:
    PYTHONPATH=src uv run --frozen pytest {{TEST}} -x -q --timeout=60

# Whole-repo ruff scan (all rules, all files). Surfaces pre-existing rot
# the codebase has accumulated. Use this when you want a full picture;
# do NOT chain it into preship — it includes ~8000 legacy findings the
# CI gate explicitly carves out.
lint:
    uv run --frozen ruff check .

# Lint with the SAME scope CI uses: errors-only rules, changed Python
# files only, evaluated against origin/main. This is what pr-validate.yml
# runs; mirroring it here means `just preship` actually matches CI.
# See: .github/workflows/pr-validate.yml § "Ruff check (changed files only)".
lint-pr-scope:
    #!/usr/bin/env bash
    set -euo pipefail
    git fetch origin main --quiet 2>/dev/null || true
    CHANGED=$(git diff --name-only --diff-filter=AMR origin/main...HEAD -- '*.py' || true)
    if [ -z "$CHANGED" ]; then
      echo "No Python files changed vs origin/main; nothing for pr-scope ruff to check."
      exit 0
    fi
    echo "Linting (pr-scope, errors-only) on changed .py files:"
    echo "$CHANGED" | sed 's/^/  /'
    uv run --frozen ruff check \
        --select E9,F \
        --ignore E402,F401,F541,F811,F841 \
        --no-cache \
        $CHANGED

# Auto-format.
format:
    uv run --frozen ruff format .

# Resolve dependencies against `uv.lock` so the worktree's `.venv`
# matches what CI provisions. Required as the first preship step
# because a stale local `.venv` (e.g. carried over from another branch
# or an older Python pin) makes pytest fail in ways CI doesn't
# reproduce. Mirrors pr-validate.yml's "Install project dependencies".
sync:
    uv sync --frozen 2>&1 | tail -5

# Run unit tests in a CI-equivalent environment. Specifically, this
# strips `UA_RUNTIME_STAGE` (which is set to `development` in operator
# shells via .env and flips production-default feature gates OFF for
# local dev — `should_run_loop("dispatch_stale_sweep", prod_default=True)`
# is the obvious case). CI never sets UA_RUNTIME_STAGE, so unit tests
# that rely on the production-default behavior pass there but fail
# locally in ways that look like "flaky tests" but are really a
# `.env`-vs-CI mismatch. Use this recipe whenever you want pytest to
# reproduce what pr-validate.yml will see.
test-ci-env:
    env -u UA_RUNTIME_STAGE -u UA_DEV_HEARTBEAT_FORCE_ON -u UA_DEV_DISPATCH_FORCE_ON -u UA_DEV_AGENTMAIL_FORCE_ON \
        PYTHONPATH=src uv run --frozen pytest tests/unit -x -q

# Pre-ship: venv sync + lint (changed-file scope) + unit tests in
# CI-equivalent env + architecture canvas pointer verify. These are the
# gates pr-validate.yml runs in CI; the whole-repo `lint` recipe and the
# regular `test` recipe are deliberately NOT chained here — the first
# includes pre-existing rot the CI gate carves out, the second inherits
# the operator's UA_RUNTIME_STAGE=development which flips production-
# default feature gates OFF and makes tests that pass in CI fail locally.
preship: sync lint-pr-scope test-ci-env canvas-verify
    @echo "✅ venv synced + pr-scope lint + ci-env unit tests + canvas pointers green. Safe to /ship."

# ---------------------------------------------------------------------------
# Architecture Canvas
# ---------------------------------------------------------------------------

# Rebuild the Architecture Canvas HTML (docs/architecture-view/output/ +
# web-ui/public/ mirror). Vendors rough.js + mermaid.min.js on first run.
# Exits non-zero on missing source pointers.
#
# See: docs/02_Subsystems/Architecture_Canvas_View.md
canvas:
    uv run scripts/build_architecture_view.py

# Verify pointers without re-rendering. Use as a pre-commit guard.
canvas-verify:
    uv run scripts/build_architecture_view.py --verify-only
