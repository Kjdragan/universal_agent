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

# Run a specific test file or test ID (e.g., `just test-one tests/unit/test_loop_control.py`).
test-one TEST:
    PYTHONPATH=src uv run --frozen pytest {{TEST}} -x -q

# Lint.
lint:
    uv run --frozen ruff check .

# Auto-format.
format:
    uv run --frozen ruff format .

# Pre-ship: lint + unit tests + architecture canvas pointer verify, the
# same gates as pr-validate.yml.
preship: lint test canvas-verify
    @echo "✅ Lint + unit tests + canvas pointers green. Safe to /ship."

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
