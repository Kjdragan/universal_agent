# RLM Exploration: Solid-State Battery Industry Report

This directory is a self-contained RLM-style workflow that explores a raw research corpus and assembles a long-form report in sections. It keeps the corpus external, uses programmatic search/read actions, and only assembles the report after evidence collection.

## Quick start
1. Ensure the project `.venv` is active.
2. Export your API key (same environment as the main project):
   ```bash
   export ANTHROPIC_API_KEY=...  # or ANTHROPIC_AUTH_TOKEN
   ```
3. Run the report pipeline:
   ```bash
   python RLM_Exploration/rom_runner.py --config RLM_Exploration/config.json
   ```

## Outputs
Artifacts are written to `RLM_Exploration/work_products/`:
- `outline.json`
- `evidence.jsonl`
- `sections/section_<id>.md`
- `report.md`
- `report.html`
- `sources.md`

## Notes
- This workflow does **not** require a refined corpus.
- If you want YAML config instead of JSON, let me know and I can add `pyyaml` via `uv`.
