# PRD: Standalone RLM Module for Large-Corpus Distillation (Experimental)

This PRD defines a manual-only experimental `RLM/` module to evaluate whether ROM/RLM runtime distillation outperforms the existing UA refined-corpus flow for corpora that exceed practical context windows.

## 1) Problem

UA research workflows can generate corpora larger than practical model context limits. We need a reliable way to distill large corpus data into high-signal, evidence-grounded artifacts usable for downstream report generation.

## 2) Goals (v0)

- Keep implementation fully isolated under `RLM/`.
- Support manual execution only (no UA core routing changes).
- Accept corpus input from:
  - single file,
  - directory,
  - UA task source (`workspace + task_name`).
- Produce stable output contract:
  - `key_takeaways.md`
  - `key_takeaways.json`
  - `evidence_index.jsonl`
  - `run_metadata.json`
- Evaluate two lanes on identical corpus:
  1. `ua_rom_baseline` (in-repo ROM baseline)
  2. `fast_rlm_adapter` (candidate runtime adapter)

## 3) Non-goals (v0)

- No automatic UA pipeline integration.
- No agent routing/gating updates in `src/universal_agent/*`.
- No replacing current `refined_corpus.md` path yet.

## 4) Provider & runtime constraints

- Must be Anthropic/ZAI compatible.
- OpenRouter is out of scope.
- Use these env vars:
  - `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN` or `ZAI_API_KEY`
  - optional `ANTHROPIC_BASE_URL`

## 5) Primary trigger target

- Intended for corpora around **180,000+ estimated tokens**.
- Token estimate approximation in v0: `total_chars / 4`.

## 6) Acceptance criteria

### Functional
- `distill` command produces output contract files for selected lane.
- `compare` command runs both lanes and writes side-by-side summary artifacts.
- Input resolution works for single file, directory, and `workspace + task_name` task corpus.

### Quality
- Evidence output includes source path traceability.
- Key findings are concise and grounded in evidence snippets.
- Runs are reproducible with saved run metadata and raw lane artifacts.

### Safety
- Manual only.
- Read-only with respect to source corpus.
- No writes outside run output directory.

## 7) Experiment scorecard

For each lane and corpus:
- Evidence count and source diversity.
- Compression ratio: source words vs key takeaways size.
- Hallucination risk proxy: unsupported claims in key findings.
- Runtime and token/cost stats (where available).

## 8) Open risks

- `fast_rlm_adapter` depends on external runtime availability and response shape consistency.
- Quality variance may come from prompting differences rather than runtime architecture.
- Token estimate is approximate and may under/over-shoot actual model tokenization.

## 9) Deliverables

- Code under `RLM/`:
  - `cli.py`, `runner.py`, corpus adapters, lane runners, output contract helpers.
- Docs:
  - `RLM/README.md`
  - `RLM/PRD.md` (this file)
