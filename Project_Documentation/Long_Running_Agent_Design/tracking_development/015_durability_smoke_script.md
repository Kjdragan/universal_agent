# 015: Durability Smoke Test Script

Date: 2026-01-02  
Scope: One-command crash → resume → verify workflow

## Summary
Added a single script to run a durability smoke test end-to-end:
- start a job
- optionally crash via crash hooks
- resume
- verify DB invariants and workspace artifacts

## Script Location
- `scripts/durability_smoke.py`

## Usage
Default (quick resume job):
```
export UV_CACHE_DIR=/home/kjdragan/lrepos/universal_agent/.uv-cache
PYTHONPATH=src uv run python scripts/durability_smoke.py
```

Full relaunch workflow (explicit artifacts):
```
export UV_CACHE_DIR=/home/kjdragan/lrepos/universal_agent/.uv-cache
PYTHONPATH=src uv run python scripts/durability_smoke.py \
  --job tmp/relaunch_resume_job.json \
  --email-to kevin.dragan@outlook.com \
  --expected-artifact work_products/relaunch_report.html \
  --expected-artifact work_products/relaunch_report.pdf
```

Use relaunch + email workflow:
```
export UV_CACHE_DIR=/home/kjdragan/lrepos/universal_agent/.uv-cache
PYTHONPATH=src uv run python scripts/durability_smoke.py \
  --job tmp/relaunch_resume_job.json \
  --email-to kevin.dragan@outlook.com
```

Crash at a tool boundary:
```
export UV_CACHE_DIR=/home/kjdragan/lrepos/universal_agent/.uv-cache
PYTHONPATH=src uv run python scripts/durability_smoke.py \
  --job tmp/relaunch_resume_job.json \
  --email-to kevin.dragan@outlook.com \
  --crash-after-tool GMAIL_SEND_EMAIL
```

## Verification
The script verifies:
1) **Artifacts exist** in the workspace:
   - `job_completion_<run_id>.md`
   - `work_products/`
2) **No duplicate idempotency keys** for the run.
3) Prints side-effect tool counts for manual review.

## Notes
- Uses `uv run` internally for consistent Python tooling.
- Crash hooks are optional and map to existing Ticket 1 env vars.
- Resume is automatic when a crash is configured or the initial run exits non-zero.
