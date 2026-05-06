# SHIP_HANDOFF

**Summary of changes made:**
ClaudeDevs X intel cron: add 22:00 America/Chicago poll (3x-daily total).

**What ships:**
- `claude_code_intel_sync` schedule moves from `0 8,16 * * *` to `0 8,16,22 * * *` (8 AM / 4 PM / 10 PM Central).
- Takes effect on gateway restart: `_ensure_claude_code_intel_cron_job` calls `update_job` for the existing entry, so the live VPS cron registry rewrites itself with the new `cron_expr` automatically — no manual `cron_jobs.json` surgery needed.
- Mission Control CSI Ingester tile thresholds (12h green / 25h yellow / 48h red) are unchanged because worst-case poll gap drops from 16h → 10h, still well inside the green band.
- No env flips needed. No new feature flags. Operators can still pin a custom schedule via `UA_CLAUDE_CODE_INTEL_CRON_EXPR` if they want to override.

**Latest commit ready for /ship:**
- `cc14d94` — feat(csi): add 22:00 Central poll to ClaudeDevs intel cron

**Post-deploy smoke test:**
1. After the deploy, hit Mission Control or check `/opt/universal_agent/workspaces/cron_jobs.json` and confirm the `claude_code_intel_sync` entry shows `"cron_expr": "0 8,16,22 * * *"`.
2. Watch for the 22:00 Central run tonight. The operator email lands at `kevinjdragan@gmail.com` only when `action_count > 0` (default `email_policy=when_actions`); a quiet poll won't email but will still write a packet under `<UA_ARTIFACTS_DIR>/proactive/claude-code-intelligence/`.
3. If you want a guaranteed email regardless, set `UA_CLAUDE_CODE_INTEL_REPORT_EMAIL_POLICY=always` in the VPS env and restart the gateway — but this isn't part of this PR.

**Known risks:**
- None new. The added 22:00 run hits the X API once more per day on the same shared `X_BEARER_TOKEN`; rate-limit headroom is comfortable for the read endpoints used here.
