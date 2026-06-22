---
name: provision-local-gpu-ollama
description: Set up and run a local LLM on a GPU via Ollama — install Ollama (user-local, no root), pull a VRAM-fitting model, and hand the caller the local endpoint to hit. Reach for this WHENEVER the work involves running a model LOCALLY instead of a hosted API, even if "Ollama" isn't named: "set up Ollama / a local model", "install ollama", "pull/run a model locally", "run an LLM on the GPU", "spin up a local-LLM / llama.cpp / gguf / on-device / offline endpoint", building or verifying a demo that hits localhost:11434, or choosing the standard small model that fits a 6GB GPU. Provisions Ollama on the desktop GTX 1660 Ti (6GB), enforces hard guards (each model's download ≤5GB, at most 3 models on disk — it refuses, not warns), standardizes on qwen2.5-coder:7b, and prints the OLLAMA_URL/OLLAMA_MODEL a demo should consume. NOT for hosted-API inference (Anthropic Max / ZAI-GLM) and NOT on the VPS (it has no GPU — local-GPU work is desktop-only).
---

# Provision Local GPU Ollama

Brings up a **user-local** (no-root) Ollama server on a GPU box and pulls a
**size-guarded** model, then hands a demo the `OLLAMA_URL` / `OLLAMA_MODEL` it
should use. Built for the constraint that the only GPU in this system is **Kevin's
desktop GTX 1660 Ti (6GB VRAM)** — the VPS has no GPU.

## When to use

- A demo or task needs a **local LLM on a GPU** (an Ollama demo) and would stall
  on CPU-only inference (the failure mode that killed the Ponytail/Ollama demo on
  the VPS: `gemma3:12b` at ~7 tok/s CPU → 15-20 min run → watchdog kill).
- You're on the **desktop** (the GPU box) and want to set up / verify GPU inference.

Do **not** use for hosted-API demos (Anthropic/ZAI) or on the VPS.

## The standard model

**`qwen2.5-coder:7b`** (Q4_K_M, ~4.7GB on disk). Best code-generation quality that
stays under the 5GB ceiling. On the 6GB GTX 1660 Ti it runs **~90% on-GPU** (the
last ~10% spills to CPU because 4.7GB model + KV/compute buffers just exceed 6GB)
at **~15 tok/s warm** — slower than a big card but it **completes demos in budget**
(the 3-pass Ponytail demo finishes in ~250s vs an infinite stall on VPS CPU).

Override with a different model only if someone names one we know fits. Approved
≤5GB alternatives (all pre-cleared):

| Model | ~Size | Notes |
|---|---|---|
| `qwen2.5-coder:7b` | 4.7GB | **STANDARD** — best code quality under the ceiling |
| `qwen2.5-coder:3b` | 1.9GB | fast fallback — **100% on-GPU**, ~3× faster, lighter quality |
| `gemma3:4b` | 3.3GB | small/fast generalist, 100% on-GPU |
| `qwen2.5:7b` | 4.7GB | generalist (non-coder) |
| `llama3.1:8b` | 4.9GB | generalist alt |

## Guards (deterministic — refuse, don't warn-and-proceed)

- **Per-model size ≤ 5GB** (`MAX_MODEL_GB`). Approved models are pre-cleared; any
  off-list model has its registry size checked and is **refused** if >5GB or if the
  size can't be determined (fail-safe). 6GB VRAM can't hold more.
- **≤ 3 models on disk** (`MAX_MODELS`). A new pull is refused when 3 are already
  present — evict one first (`ollama rm <name>`). Stops local models eating disk.
- **GPU required** — aborts if there's no `nvidia-smi`.

## Run

```bash
# default (standard model), from anywhere:
bash .claude/skills/provision-local-gpu-ollama/provision_gpu_ollama.sh

# pick an approved model / dry-run the guards without downloading:
bash .../provision_gpu_ollama.sh qwen2.5-coder:3b
bash .../provision_gpu_ollama.sh gemma3:27b --dry-run   # -> REFUSED (17GB > 5GB)
```

It installs Ollama user-local into `~/.local/ollama` (downloads the `.tar.zst`,
no root), starts `ollama serve` (a plain `setsid` process — **not** a systemd unit
or any desktop daemon), pulls the guarded model, warms it, and prints its
GPU/CPU placement. The **last two stdout lines** are the env a demo consumes:

```
OLLAMA_URL=http://127.0.0.1:11434/api/generate
OLLAMA_MODEL=qwen2.5-coder:7b
```

A demo written to read `OLLAMA_URL` / `OLLAMA_MODEL` (see the Ponytail demo) then
runs unchanged on the GPU.

## Contract note

This skill only ever runs when **you invoke it interactively on the desktop**. It
installs nothing as a service and starts no poller/timer — consistent with the
platform rule that *nothing operational runs autonomously on the desktop*. The
GPU box is brought up on demand and the `serve` process is yours to stop
(`pkill -f 'ollama serve'`).
