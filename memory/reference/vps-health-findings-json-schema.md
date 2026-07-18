# VPS System Health Check — `heartbeat_findings_latest.json` schema template

> Moved verbatim from `memory/HEARTBEAT.md` (R4 context diet, 2026-07-18). Read this
> before writing `work_products/heartbeat_findings_latest.json` during a VPS System
> Health Check tick, if you need the exact contract shape.

The JSON contract must use this schema:

```json
{
  "version": 1,
  "overall_status": "ok|warn|critical",
  "generated_at_utc": "ISO-8601 UTC timestamp",
  "source": "vps_system_health_check",
  "summary": "Short one-paragraph summary of the most important finding set.",
  "findings": [
    {
      "finding_id": "stable_snake_case_id",
      "category": "gateway|system|disk|memory|cpu|dispatch|database|unknown",
      "severity": "ok|warn|critical",
      "metric_key": "recent_errors_30m",
      "observed_value": 67,
      "threshold_text": ">50",
      "known_rule_match": true,
      "confidence": "low|medium|high",
      "title": "Gateway Errors Elevated",
      "recommendation": "Inspect gateway logs for root cause.",
      "runbook_command": "journalctl -u universal-agent-gateway --since '30 min ago' --no-pager",
      "metadata": {
        "service": "universal-agent-gateway"
      }
    }
  ]
}
```

Include at least one `findings[]` entry whenever `overall_status` is `warn` or `critical`.
Use `known_rule_match=true` only when the issue clearly maps to a stable runbookable
condition. Unknown edge cases should still be emitted with `known_rule_match=false`.
