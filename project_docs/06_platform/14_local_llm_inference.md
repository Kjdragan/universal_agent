---
title: Local LLM Inference (Ollama on the Desktop GPU)
status: active
canonical: true
subsystem: local-llm-inference
code_paths:
  - .claude/skills/provision-local-gpu-ollama/
  - .claude/commands/gpu-demo-build.md
  - src/universal_agent/feature_flags.py
  - src/universal_agent/services/proactive_tutorial_builds.py
  - src/universal_agent/gateway_server.py
last_verified: 2026-06-22
---

# Local LLM Inference (Ollama on the Desktop GPU)

Most UA inference runs on hosted backends — Anthropic Max (interactive) or the
ZAI/GLM proxy (autonomous). This doc covers the **third path: running an LLM
locally on a GPU via Ollama**, and the reusable machinery for it. Reach for a
local LLM when the work *is* about local inference — a tutorial/demo that
reproduces a "run it on your own machine with Ollama / llama.cpp" workflow, an
offline experiment, or a cost-free throwaway — not for general agent reasoning
(use the hosted backends for that; see
[`05_environments.md`](05_environments.md)).

## The one hard constraint: where the GPU is

| | Desktop (`mint-desktop`) | VPS (`uaonvps`, Hostinger) |
|---|---|---|
| GPU | **NVIDIA GTX 1660 Ti, 6GB VRAM** (driver 580 / CUDA 13) | **none — ever** |
| Local LLM | Ollama, user-local | CPU-only (too slow for multi-pass) |
| Role | Kevin's interactive cockpit | the always-on runtime |

The two are **decoupled** — a desktop GPU cannot accelerate VPS-side inference.
So local-GPU work happens on the **desktop only**. A demo that drives a local
Ollama server can be *built* by the autonomous pipeline but cannot be *verified*
on the VPS: CPU inference runs at ~7 tok/s and a multi-pass run exceeds the 600s
no-progress watchdog (`timeout_policy.py::LivenessWatchdog`). This is exactly
what stalled the `ponytail-yagni-ladder-ollama` demo on 2026-06-22 and motivated
the desktop-build approval gate (below).

## Ollama — user-local, no root

Ollama is installed into `~/.local/ollama` (the current distribution is a
`.tar.zst` from `https://ollama.com/download/ollama-linux-amd64.tar.zst`; the
older `.tgz` URL 404s). It bundles its own CUDA runtime, so it needs only the
NVIDIA driver — **no root, no CUDA toolkit, no torch**. `ollama serve` is a plain
process (started via `setsid`), **never** a systemd unit or any desktop daemon —
consistent with the platform rule that nothing operational runs autonomously on
the desktop.

## The standard model + the 6GB guards

**Standard model: `qwen2.5-coder:7b`** (Q4_K_M, ~4.7GB on disk). It's the best
code-generation model that stays under the 5GB ceiling; on the 6GB card it runs
~90% on-GPU (the last ~10% spills to CPU because model + KV/compute buffers just
exceed 6GB) at ~15 tok/s warm, and **completes demos in budget**. Don't change
the standard unless someone names a model we know fits.

| Model | ~Size | Fit / role |
|---|---|---|
| `qwen2.5-coder:7b` | 4.7GB | **STANDARD** — best code quality under the ceiling |
| `qwen2.5-coder:3b` | 1.9GB | fast fallback — 100% on-GPU, ~3× faster |
| `gemma3:4b` | 3.3GB | small/fast generalist |
| `qwen2.5:7b` | 4.7GB | generalist (non-coder) |
| `llama3.1:8b` | 4.9GB | generalist alt |
| `gemma3:12b` | 8.1GB | **does not fit** — refused |

Two **deterministic guards** keep a 6GB box (and its disk) safe — they *refuse*,
they don't warn-and-proceed:

- **Per-model size ≤ 5GB.** 6GB VRAM can't hold more. Approved models are
  pre-cleared; an off-list model's `registry.ollama.ai` size is checked and the
  pull is refused if it exceeds the ceiling or the size can't be determined
  (fail-safe).
- **≤ 3 models on disk.** A new pull is refused when three are already present —
  evict one first (`ollama rm <name>`). Stops local models silently eating disk.

## Provisioning: the `provision-local-gpu-ollama` skill

The reusable entry point is the **`provision-local-gpu-ollama` skill**
(`.claude/skills/provision-local-gpu-ollama/`). It detects the GPU, installs
user-local Ollama if missing, starts `serve`, enforces both guards, pulls the
(guarded) model, warms it, reports the GPU/CPU placement, and prints the
`OLLAMA_URL` / `OLLAMA_MODEL` a demo should consume on its last two stdout lines:

```bash
bash .claude/skills/provision-local-gpu-ollama/provision_gpu_ollama.sh           # standard model
bash .../provision_gpu_ollama.sh qwen2.5-coder:3b                                 # an approved alternative
bash .../provision_gpu_ollama.sh gemma3:27b --dry-run                            # -> REFUSED (17GB > 5GB)
```

A demo wires itself to the local server by reading those env vars (the Ponytail
demo reads `OLLAMA_URL`/`OLLAMA_MODEL`/`OLLAMA_TIMEOUT`), so the same demo runs
on the desktop GPU or, with a faster tag, on the VPS CPU.

## GPU-bound demos: the desktop-build approval gate

Because GPU-bound demos can't be verified on the VPS, they take a human-in-the-loop
branch instead of failing on the VPS watchdog. When the demo-build sweep
classifies a candidate as GPU-bound
(`proactive_tutorial_builds.py::gpu_bound_from_candidate` — a keyword pre-filter
for `ollama`, `gguf`, `llama.cpp`, `localhost:11434`, etc.; `vllm` was removed
2026-07-14),
`proactive_tutorial_builds.py::classify_and_gate_gpu_demo` parks the task
(`agent_ready=False` so CODIE never auto-claims it), stamps
`metadata.gpu_approval.state="pending"`, and emails Kevin an HMAC-signed approve
link. The link token is `{exp}.{sig}` and **expires** (default 7 days,
`GPU_DEMO_TOKEN_TTL_SECONDS`) so a leaked/forwarded email doesn't stay actionable
forever. Approving hits `gateway_server.py::gpu_demo_approve_get` on the always-on
VPS gateway, which records `state="approved"` and prints the
`/gpu-demo-build <task_id>` command. The endpoint is **single-use**: once a
terminal state (`approved`/`rejected`/`built`) is recorded, a replayed link shows
the standing decision instead of flipping it. Kevin runs that **interactively** on the
desktop; it provisions via the skill, builds the demo locally, and finalizes the
task. Gated by `feature_flags.py::gpu_demo_desktop_approval_enabled`
(`UA_GPU_DEMO_DESKTOP_APPROVAL_ENABLED`).

> **Status 2026-07-14 — the proactive GPU-demo approval flow is OFF.**
> `UA_GPU_DEMO_DESKTOP_APPROVAL_ENABLED` was set to `0` in Infisical (operator
> decision): the CSI/YouTube intel feed is saturated with local-AI content, so the
> keyword net was queuing a flood of approval emails (50 pending + 12 rejected).
> With the flag off, `classify_and_gate_gpu_demo` returns early — no
> classification, no emails — and GPU-bound candidates flow through the normal
> (gated) path. The local GPU stays fully available **on demand**: Kevin runs
> `/demo` and tells it to use the GPU when a genuinely interesting GPU demo appears.
> Re-enable by flipping the Infisical flag back to `1`.

The full lifecycle, states, and contract-safety analysis live in the demo
pipeline ADR — see
[`../04_intelligence/15_demo_tutorial_pipeline_adr.md`](../04_intelligence/15_demo_tutorial_pipeline_adr.md)
§ "GPU-bound demos — desktop-build approval gate". Desktop GPU facts also appear
in [`05_environments.md`](05_environments.md) § "Desktop GPU".

## Why not torch / CUDA toolkit?

The desktop venv's `torch` is a CUDA-13 wheel without the matching CUDA libraries
(`libcublasLt.so.13` missing), so `import torch` fails — but **nothing in the
local-LLM path needs it**. Ollama ships its own CUDA runtime and the demos talk
to it over HTTP. Only fix torch if a future feature genuinely needs GPU torch on
the desktop; it is not a prerequisite for local LLM inference here.
