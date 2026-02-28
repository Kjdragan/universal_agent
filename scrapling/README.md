# Scrapling Experiment Workspace

This directory is the canonical home for the Scrapling scraping experiments.

## Layout

- `scripts/` - runnable experiment scripts
- `inputs/` - JSON input corpora for experiments
- `runs/` - run artifacts (`inbox/`, `processed/`, `logs/`, `run_report.json`)
- `documentation/` - implementation notes, capabilities, and runbook

## Quick Start

Run an evaluation from an input corpus:

```bash
cd /home/kjdragan/lrepos/universal_agent
uv run --with 'scrapling[fetchers]' --with python-dotenv \
  python3 scrapling/scripts/run_scrapling_eval.py \
  --source-json-dir scrapling/inputs/strategy_probe/force_basic \
  --run-name force_basic_example \
  --per-url-delay 0 \
  --escalation-delay 1.0
```

