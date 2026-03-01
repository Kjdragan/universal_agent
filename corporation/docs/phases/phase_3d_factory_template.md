# Phase 3d: Factory Template & Self-Update

**Status:** Not Started
**Priority:** Medium — enables ongoing factory maintenance at scale
**Depends on:** Phase 3a (consumer), Phase 3c (local factory deployed)

---

## Objective

Create a mechanism so that when you push changes to `main`, every deployed factory can be updated without manual SSH. This includes both a manual update script and a delegation-driven self-update mission type.

## Design

### Update Flow (Manual)
```
Developer pushes to main
  → SSH to factory or run locally
  → bash scripts/update_factory.sh
  → git pull, uv sync, restart consumer service
  → Factory re-registers with updated capabilities
```

### Update Flow (Delegated)
```
Developer pushes to main
  → HQ Simone (or operator) publishes system:update_factory mission
  → Local factory consumer receives mission
  → Consumer triggers self-update subprocess
  → Consumer restarts (systemd auto-restart on exit)
  → Factory re-registers after restart
```

## Files to Create

### 1. `scripts/update_factory.sh`

```bash
#!/usr/bin/env bash
# Update a deployed factory to latest main.
#
# Usage: bash scripts/update_factory.sh [--branch main] [--restart]
#
# Steps:
#   1. git fetch origin
#   2. git checkout <branch> && git pull --rebase origin <branch>
#   3. uv sync
#   4. If --restart: systemctl --user restart universal-agent-local-factory
#   5. Verify: consumer starts and re-registers with HQ

set -euo pipefail

FACTORY_DIR="${UA_FACTORY_DIR:-$(pwd)}"
BRANCH="${1:-main}"
RESTART="${2:-}"

cd "$FACTORY_DIR"

echo "[update] Fetching origin..."
git fetch origin

echo "[update] Checking out $BRANCH..."
git checkout "$BRANCH"
git pull --rebase origin "$BRANCH"

echo "[update] Installing dependencies..."
uv sync

if [[ "$RESTART" == "--restart" ]]; then
    echo "[update] Restarting factory service..."
    systemctl --user restart universal-agent-local-factory || true
fi

echo "[update] Factory updated to $(git rev-parse --short HEAD)"
```

### 2. `src/universal_agent/delegation/handlers/system_update.py`

```python
"""Handler for system:update_factory delegation missions."""

import logging
import os
import subprocess
import sys
from pathlib import Path

from universal_agent.delegation.consumer import ConsumerContext, MissionHandler, MissionResult
from universal_agent.delegation.schema import MissionEnvelope

logger = logging.getLogger(__name__)


class SystemUpdateHandler:
    mission_kind = "system:update_factory"

    async def handle(self, envelope: MissionEnvelope, context: ConsumerContext) -> MissionResult:
        """
        Pull latest code and restart.
        
        Strategy: run update_factory.sh, then exit the consumer process.
        Systemd will auto-restart, picking up the new code.
        """
        factory_dir = context.workspace_dir
        branch = envelope.payload.context.get("branch", "main")
        update_script = factory_dir / "scripts" / "update_factory.sh"
        
        if not update_script.exists():
            return MissionResult(
                status="FAILED",
                error=f"Update script not found: {update_script}",
            )
        
        try:
            proc = subprocess.run(
                ["bash", str(update_script), branch],
                cwd=str(factory_dir),
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
            
            if proc.returncode != 0:
                return MissionResult(
                    status="FAILED",
                    error=f"Update script failed (exit {proc.returncode}): {proc.stderr[-500:]}",
                    result={"stdout": proc.stdout[-1000:], "stderr": proc.stderr[-1000:]},
                )
            
            new_commit = proc.stdout.strip().split("\n")[-1] if proc.stdout else "unknown"
            
            # Schedule graceful exit so systemd restarts us with new code
            logger.info("Factory updated to %s. Scheduling consumer restart.", new_commit)
            # The consumer loop should check a "restart_requested" flag and exit cleanly
            
            return MissionResult(
                status="SUCCESS",
                result={"updated_to": new_commit, "restart_scheduled": True},
            )
        except subprocess.TimeoutExpired:
            return MissionResult(status="FAILED", error="Update script timed out after 300s")
        except Exception as exc:
            return MissionResult(status="FAILED", error=str(exc))
```

### 3. HQ publish helper: `POST /api/v1/ops/factory/update`

Add a convenience endpoint on the gateway that publishes a `system:update_factory` mission to the Redis bus, targeting a specific factory or all factories:

```python
@app.post("/api/v1/ops/factory/update")
async def trigger_factory_update(request: Request, payload: FactoryUpdateRequest):
    """Publish a system:update_factory mission to the delegation bus."""
    _require_ops_auth(request)
    _require_delegation_publish_allowed()
    # Build MissionEnvelope with mission_kind="system:update_factory"
    # Publish to Redis stream
    # Return job_id
```

## Files to Modify

### `src/universal_agent/delegation/consumer.py`
- Add `restart_requested: bool` flag on `MissionConsumer`
- After processing a `system:update_factory` mission that returns SUCCESS with `restart_scheduled=True`, set the flag
- Main loop checks flag and exits cleanly (systemd restarts with new code)

### `src/universal_agent/gateway_server.py`
- Add `POST /api/v1/ops/factory/update` endpoint
- Add `FactoryUpdateRequest` Pydantic model (target_factory_id, branch)

## Tests to Create

### `tests/delegation/test_system_update_handler.py`

```python
# Test cases:
# 1. Handler runs update script successfully
# 2. Handler returns FAILED when script doesn't exist
# 3. Handler returns FAILED when script exits non-zero
# 4. Handler respects timeout
# 5. MissionResult includes restart_scheduled flag
```

### `tests/gateway/test_factory_update_endpoint.py`

```python
# Test cases:
# 1. Endpoint publishes mission to Redis bus
# 2. Endpoint requires ops auth
# 3. Endpoint requires delegation publish permission (HQ-only)
# 4. Endpoint returns job_id
```

## Validation Commands

```bash
# Unit tests
uv run pytest tests/delegation/test_system_update_handler.py -q
uv run pytest tests/gateway/test_factory_update_endpoint.py -q

# Manual update
bash scripts/update_factory.sh main --restart

# Delegated update (from HQ)
curl -X POST -H "x-ua-ops-token: <token>" \
  -H "Content-Type: application/json" \
  -d '{"branch": "main"}' \
  https://api.clearspringcg.com/api/v1/ops/factory/update
```

## Acceptance Criteria

- [ ] `scripts/update_factory.sh` pulls latest, syncs deps, optionally restarts
- [ ] `SystemUpdateHandler` executes update script and signals restart
- [ ] Consumer exits cleanly after update, systemd restarts it
- [ ] Factory re-registers with HQ after restart (updated capabilities)
- [ ] `POST /api/v1/ops/factory/update` endpoint publishes update mission
- [ ] Unit tests pass

## Safety Considerations

- Update script must not delete local `.env` or factory-specific config
- Self-update should be atomic: if `git pull` fails, do not proceed to `uv sync`
- Consumer must ack the mission before exiting (otherwise it retries on restart)
- Rate-limit: don't allow multiple update missions to stack up
