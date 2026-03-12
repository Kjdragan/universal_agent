# CSI Notification Quality Feedback

**Date:** 2026-03-12
**Source:** Kevin Dragan (kevin.dragan@outlook.com)
**Thread:** CSI Alert: 3 AI Signals Requiring Action ($550-1,700/mo potential)

## Feedback Summary

Kevin provided positive feedback on a high-quality CSI alert email, noting that it represents the "kind of quality analysis" he wants to see. However, he also indicated that he's receiving **too many CSI notifications** that should be handled programmatically rather than surfacing to his attention.

## Key Requirements

### What Kevin WANTS to See (High-Signal Notifications)
- **Curated, high-value opportunities** like the 3 AI signals alert (DeepSeek V4, Claude Code video, Gemini update)
- **Actionable intelligence** with clear monetization potential (e.g., "$550-1,700/mo potential")
- **Strategic insights** that require human decision-making or resource allocation
- **Exception handling** for anomalies that need manual intervention

### What Kevin Does NOT Want (Noise to Suppress)
- Routine operational events that should be handled programmatically
- Low-signal status updates
- Repetitive notifications about known issues
- Informational alerts that don't require action
- Alerts that can be auto-remediated without human input

## Notification Quality Bar

**The standard-bearer CSI alert** (the one Kevin liked) had these characteristics:
1. **Clear subject line** with specific signal count and value range
2. **Prioritized opportunities** with specific dollar values attached
3. **Concise analysis** of each opportunity
4. **Action items** clearly identified
5. **Strategic context** (why these matter now)

## Implementation Guidance

### For CSI Trend Analyst Agent
- **Raise the threshold** for what constitutes a "notification-worthy" signal
- **Filter out** routine delivery health issues, source flapping, transient errors
- **Focus on** high-confidence, high-value opportunities with clear next actions
- **Bundle multiple low-value signals** into weekly digests rather than real-time alerts

### For CSI Supervisor Agent
- **Suppress auto-remediable issues** from notifications (handle internally, log only)
- **Only escalate** when:
  - SLO breach is persistent despite auto-remediation
  - Multiple sources failing simultaneously
  - New, unknown failure patterns emerge
  - Manual intervention is clearly required

### For Notification Delivery
- **Consider a notification tiering system:**
  - **Tier 1 (Immediate):** Critical SLO breaches, high-value monetization opportunities
  - **Tier 2 (Digest):** Routine health updates, lower-priority opportunities
  - **Tier 3 (Log only):** Auto-remediated issues, transient errors, routine status

## Next Actions

1. **Review current CSI filtering logic** to identify what's generating excessive notifications
2. **Implement stricter signal-to-noise thresholds** in CSI trend analysis
3. **Add notification bundling** for lower-priority items
4. **Consider implementing notification preferences/tiers** in ops_config
5. **Validate changes** with Kevin by comparing notification volume before/after

## Related Files
- CSI Supervisor: `/home/kjdragan/lrepos/universal_agent/.claude/agents/csi-supervisor.md`
- CSI Trend Analyst: `/home/kjdragan/lrepos/universal_agent/.claude/agents/csi-trend-analyst.md`
- Ops Config: `/home/kjdragan/lrepos/universal_agent/src/universal_agent/ops_config.py`

## Metrics to Track
- Total CSI notifications per day (current vs. target)
- Percentage of notifications that lead to action
- Kevin's subjective satisfaction with signal quality
- False positive rate (notifications that didn't require action)
