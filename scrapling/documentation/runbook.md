# Scrapling Runbook

## 1) Choose Input Corpus

Examples:

- `scrapling/inputs/strategy_probe/force_basic`
- `scrapling/inputs/strategy_probe/adaptive_basic`
- `scrapling/inputs/nyt_probe`

## 2) Execute Run

```bash
cd /home/kjdragan/lrepos/universal_agent
uv run --with 'scrapling[fetchers]' --with python-dotenv \
  python3 scrapling/scripts/run_scrapling_eval.py \
  --source-json-dir scrapling/inputs/strategy_probe/force_basic \
  --run-name my_run_name \
  --per-url-delay 0 \
  --escalation-delay 1.0
```

## 3) Inspect Artifacts

- Run report: `scrapling/runs/<run_name>/run_report.json`
- Logs: `scrapling/runs/<run_name>/logs/run.log`
- Markdown output: `scrapling/runs/<run_name>/processed/*.md`

## 4) Compare Strategy Performance

Primary comparison artifact:

- `scrapling/runs/reports/strategy_probe_comparison_metrics.json`

## 5) Cleaning Controls (per input JSON)

```json
{
  "options": {
    "clean_markdown": true,
    "include_structure": false,
    "include_links": false
  }
}
```
