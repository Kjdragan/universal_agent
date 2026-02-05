# 042 — Telegram Bot Investigation Report (2026‑02‑02)

## Scope
Investigate the existing Telegram bot deployment (Railway) and the reported build failure. No code changes requested.

## Findings (Code + Deployment Topology)

### 1) Telegram bot architecture (current)
- **Entry point**: `src/universal_agent/bot/main.py`
  - FastAPI app + PTB `Application` lifecycle.
  - Supports **webhook** (preferred) and **polling** fallback.
  - Enforces webhook secret (`X-Telegram-Bot-Api-Secret-Token`).
  - Uses `TaskManager` worker to avoid blocking handlers.
- **Adapter**: `src/universal_agent/bot/agent_adapter.py`
  - Uses **InProcessGateway** by default.
  - If `UA_GATEWAY_URL` is set, uses **ExternalGateway**.
  - Sessions map to `tg_{user_id}` (workspace under `AGENT_RUN_WORKSPACES`).
- **Startup**: `start.sh`
  - Runs **AgentCollege** and the **Telegram bot** in one container.
  - Does **not** start the gateway API; bot uses gateway classes internally (in‑process unless `UA_GATEWAY_URL` is set).

### 2) Railway build/deploy configuration
- Railway build is **Dockerfile‑based** (`Dockerfile` in repo root).
- Dockerfile runs:
  - `uv sync --frozen --no-dev` using `pyproject.toml`.
  - This installs **all** dependencies (including heavy optional tooling).

### 3) Reported build failure
**Observed error:** `pycairo==1.29.0` build failure.

Root cause from log:
- Meson build failed: **no compiler** present (`cc/gcc/clang` missing).
- Dependency path:
  - `pycairo` pulled by `manim>=0.19.2`.

**Why this breaks Railway builds**:
- The Docker image does not install build tools (no `build-essential`, no `pkg-config`, no `libcairo2-dev` headers).
- On Railway, `uv sync` builds `pycairo` from source, which requires a compiler toolchain.

### 4) Extra weight/latency in Telegram build
Even when the build succeeds, the Telegram bot image currently installs many heavy packages (torch + CUDA, transformers, manim, etc.) that are not needed for a bot surface. This increases build time, image size, and cold-start latency.

## Summary of Likely Root Causes
1. **Compiler toolchain missing** in the Docker build environment.
2. **Manim dependency** pulls in `pycairo` (source build required).
3. **Single dependency set** for all surfaces (CLI/Gateway/Bot), causing large installs on Railway.

## Recommendations (No code changes applied here)

### A) Quick unblock for Railway builds
- Add a compiler toolchain and Cairo dev headers to the Docker image:
  - `build-essential`, `pkg-config`, `libcairo2-dev`, `libffi-dev`, `libpango1.0-dev`, etc.
- This will allow `pycairo` to compile during `uv sync`.

### B) Medium‑term cleanup (reduces build size/latency)
- Split dependencies into **groups/extras**:
  - `bot` group: telegram + gateway + minimal runtime
  - `full` group: manim, GPU/ML, media processing
- Build the Telegram service using only `bot` extras.

### C) Telegram‑gateway parity (already good, but ensure consistency)
- If Telegram should use **the same execution engine** as Web UI:
  - Set `UA_GATEWAY_URL` and make bot act as a gateway client.
  - This avoids running a second engine inside the bot container.
- If you keep **in‑process** execution in the bot:
  - Ensure its tool registry and hooks match the gateway runtime.

## Proposed Next Actions (If You Want Me To Implement)
1. Decide whether to **keep** or **remove** `manim` from core dependencies.
2. Decide whether Telegram should be **gateway‑client** or **in‑process**.
3. Add a **bot‑only dependency group** and update Railway build to use it.
4. Adjust Dockerfile accordingly (build tools only if needed).

## Files Reviewed
- `pyproject.toml`
- `Dockerfile`
- `start.sh`
- `src/universal_agent/bot/main.py`
- `src/universal_agent/bot/agent_adapter.py`
- `src/universal_agent/bot/config.py`

