#!/usr/bin/env bash
# provision_gpu_ollama.sh — bring up a user-local, GPU-backed Ollama with a
# size-guarded demo model on a 6GB-class NVIDIA box. NO ROOT REQUIRED.
#
# Guards (all deterministic — code gates execution, per CLAUDE.md):
#   * per-model size ceiling  : refuse any model whose download is > MAX_MODEL_GB (default 5.0)
#   * disk model-count ceiling: refuse a new pull when >= MAX_MODELS already on disk (default 3)
#   * GPU required            : aborts if no nvidia-smi (this skill is for GPU boxes)
#
# Usage:
#   provision_gpu_ollama.sh [MODEL] [--dry-run]
# Env overrides: OLLAMA_MODEL MAX_MODEL_GB MAX_MODELS OLLAMA_HOME OLLAMA_HOST
# On success prints, on the LAST two lines, the env a demo should consume:
#   OLLAMA_URL=http://127.0.0.1:11434/api/generate
#   OLLAMA_MODEL=<model>
set -euo pipefail

DRY=0; ARGS=()
for a in "$@"; do [ "$a" = "--dry-run" ] && DRY=1 || ARGS+=("$a"); done
MODEL="${ARGS[0]:-${OLLAMA_MODEL:-qwen2.5-coder:7b}}"
MAX_MODEL_GB="${MAX_MODEL_GB:-5.0}"
MAX_MODELS="${MAX_MODELS:-3}"
OLL_HOME="${OLLAMA_HOME:-$HOME/.local/ollama}"
HOST="${OLLAMA_HOST:-127.0.0.1:11434}"
BIN="$OLL_HOME/bin/ollama"

# Approved <=5GB models for a 6GB-VRAM box (name -> role). Pulling one is pre-cleared.
# All verified to download <5GB. The 7B coder is the STANDARD for code-gen demos;
# it runs ~90% on a 6GB GPU (slight CPU spill) but completes in budget. The 3B
# coder fits 100% on-GPU and is ~3x faster when speed matters more than depth.
declare -A APPROVED=(
  ["qwen2.5-coder:7b"]="STANDARD code-gen demo model (~4.7GB, ~90% GPU on 6GB, ~15 tok/s)"
  ["qwen2.5-coder:3b"]="fast/fully-on-GPU fallback (~1.9GB, 100% GPU, ~45 tok/s)"
  ["qwen2.5:7b"]="generalist (~4.7GB)"
  ["llama3.1:8b"]="generalist alt (~4.9GB)"
  ["gemma3:4b"]="small/fast generalist (~3.3GB, 100% GPU)"
)

die(){ echo "ERROR: $*" >&2; exit 1; }
warn(){ echo "WARN: $*" >&2; }

# Best-effort registry size (GB) for off-list library models.
# ponytail: only handles registry.ollama.ai library models; unknowns fail safe (refuse).
registry_size_gb(){
  local m="$1" name tag
  name="${m%%:*}"; tag="${m#*:}"; [ "$tag" = "$m" ] && tag="latest"
  curl -fsSL -m 20 -H "Accept: application/vnd.docker.distribution.manifest.v2+json" \
    "https://registry.ollama.ai/v2/library/${name}/manifests/${tag}" 2>/dev/null \
  | python3 -c 'import sys,json
try:
    d=json.load(sys.stdin)
    tot=sum(l.get("size",0) for l in d.get("layers",[]))+d.get("config",{}).get("size",0)
    print(f"{tot/1e9:.2f}")
except Exception:
    pass'
}

# 1. GPU present?
command -v nvidia-smi >/dev/null 2>&1 || die "no nvidia-smi — this skill provisions a GPU box. Aborting."
VRAM_TOTAL="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -1 | tr -d ' ')"
GPU_NAME="$(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)"
echo ">> GPU: ${GPU_NAME} (${VRAM_TOTAL} MiB VRAM)"
[ "${VRAM_TOTAL:-0}" -ge 4000 ] || warn "VRAM ${VRAM_TOTAL}MiB is very low; only tiny (<=3B) models will fit."

# 2. size ceiling guard (BEFORE any download)
if [ -n "${APPROVED[$MODEL]:-}" ]; then
  echo ">> '${MODEL}' approved (<=${MAX_MODEL_GB}GB): ${APPROVED[$MODEL]}"
else
  warn "'${MODEL}' is NOT on the approved list — checking registry size..."
  SZ="$(registry_size_gb "$MODEL" || true)"
  [ -n "$SZ" ] || die "cannot determine size of '${MODEL}' — refusing (fail-safe). Pick an approved model or pull it manually."
  if awk "BEGIN{exit !($SZ > $MAX_MODEL_GB)}"; then
    die "'${MODEL}' is ${SZ}GB > ${MAX_MODEL_GB}GB ceiling — refusing (only ${VRAM_TOTAL}MiB VRAM)."
  fi
  warn "'${MODEL}' is ${SZ}GB (<=${MAX_MODEL_GB}GB) — proceeding off-list."
fi

if [ "$DRY" = 1 ]; then echo ">> --dry-run: guards passed for '${MODEL}', not installing/pulling."; exit 0; fi

# 3. install user-local ollama if missing (.tar.zst, current format; no root)
if [ ! -x "$BIN" ]; then
  echo ">> installing user-local Ollama into ${OLL_HOME} (no root)"
  command -v zstd >/dev/null 2>&1 || die "zstd not found (needed to extract Ollama's .tar.zst)."
  curl -fsSL -m 600 "https://ollama.com/download/ollama-linux-amd64.tar.zst" -o /tmp/ollama.tar.zst
  mkdir -p "$OLL_HOME"
  zstd -d -c /tmp/ollama.tar.zst | tar -xf - -C "$OLL_HOME"
fi
export PATH="$OLL_HOME/bin:$PATH" OLLAMA_HOST="$HOST"

# 4. serve up? (detached, survives this script — NOT a systemd/desktop daemon)
if ! curl -fsS -m3 "http://$HOST/api/tags" >/dev/null 2>&1; then
  echo ">> starting 'ollama serve' (setsid, foreground-spawned)"
  setsid nohup "$BIN" serve >/tmp/ollama_serve.log 2>&1 &
  for i in $(seq 1 40); do curl -fsS -m2 "http://$HOST/api/tags" >/dev/null 2>&1 && break; sleep 1; done
fi
echo ">> ollama $("$BIN" --version 2>&1 | tr '\n' ' ')"

# 5. pull (with disk model-count guard) unless already present
if "$BIN" list | awk 'NR>1{print $1}' | grep -qx "$MODEL"; then
  echo ">> '${MODEL}' already on disk — skipping pull"
else
  N="$("$BIN" list | awk 'NR>1' | grep -c . || true)"
  if [ "${N:-0}" -ge "$MAX_MODELS" ]; then
    echo "Models currently on disk (${N}, cap ${MAX_MODELS}):" >&2
    "$BIN" list >&2
    die "disk model cap reached (${N} >= ${MAX_MODELS}). Evict one first:  ollama rm <name>"
  fi
  echo ">> pulling '${MODEL}' (${N}/${MAX_MODELS} slots used)"
  "$BIN" pull "$MODEL"
fi

# 6. warm + report GPU placement
curl -sS -m 120 -X POST "http://$HOST/api/generate" \
  -d "{\"model\":\"$MODEL\",\"prompt\":\"OK\",\"stream\":false,\"keep_alive\":\"10m\",\"options\":{\"num_predict\":1}}" >/dev/null
echo "=== placement (PROCESSOR col = CPU/GPU split; want mostly GPU) ==="
"$BIN" ps

# 7. emit the env a demo should consume (LAST two lines, machine-readable)
echo "OLLAMA_URL=http://$HOST/api/generate"
echo "OLLAMA_MODEL=$MODEL"
