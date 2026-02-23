# RLM Experimental Module (Standalone)

This directory is an **experimental, manual-only** module for evaluating large-corpus distillation approaches without changing UA core flows.

## Goals

- Compare two distillation lanes on identical corpora:
  1. `ua_rom_baseline` (existing in-repo ROM flow from `RLM_Exploration/`)
  2. `fast_rlm_adapter` (adapter for upstream fast-rlm runtime)
- Generate a stable output contract for downstream report use:
  - `key_takeaways.md`
  - `key_takeaways.json`
  - `evidence_index.jsonl`
  - `run_metadata.json`

## Scope

- Manual invocation only.
- No automatic routing in UA core.
- Supports corpora from:
  - single file
  - directory
  - UA task folders (e.g. `tasks/<task_name>/filtered_corpus`)

## Provider compatibility

This module is designed for **Anthropic-compatible APIs** (including ZAI Anthropic-compatible base URL), not OpenRouter.

- `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN` or `ZAI_API_KEY`
- Optional `ANTHROPIC_BASE_URL`

## Quick start

```bash
python RLM/cli.py distill \
  --mode ua_rom_baseline \
  --source /absolute/path/to/corpus_dir \
  --topic "Your topic" \
  --report-title "Your report title"
```

Using an existing UA research task:

```bash
python RLM/cli.py distill \
  --mode ua_rom_baseline \
  --task-name russia_ukraine_war_jan_2026 \
  --workspace /absolute/path/to/session_workspace \
  --topic "Russia-Ukraine war: latest developments" \
  --report-title "Russia-Ukraine War - Distilled Takeaways"
```

Run both lanes and produce comparison outputs:

```bash
python RLM/cli.py compare \
  --source /absolute/path/to/corpus_dir \
  --topic "Your topic" \
  --report-title "Your report title"
```

## Notes

- Threshold defaults to `180000` estimated tokens (`chars / 4`).
- Use `--enforce-threshold` to fail runs below threshold.
- `fast_rlm_adapter` requires `fast_rlm` availability in your Python environment.
