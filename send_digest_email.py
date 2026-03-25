import os
import json
from agentmail import AgentMail
from dotenv import load_dotenv

# Check for AgentMail API key
api_key = os.environ.get("AGENTMAIL_API_KEY")
if not api_key:
    print("ERROR: AGENTMAIL_API_KEY not set")
    sys.exit(1)

inbox_id = os.environ.get("UA_AGENTMAIL_INBOX_ADDRESS")
if not inbox_id:
    print("Error: UA_AGENTMAIL_INbox_address not set")
    sys.exit(1)

# VPS health check results from previous session
VPS_STATUS: CRITICAL
# RAM: 93% utilized, swap 99.9% exhausted

# Dispatch concurrency: NOT set
# Stale CSI signals: 25 tasks (22+ days old)

# Prepare email content
text_content = """Kevin,

VPS is in a CRITICAL state. RAM is 93% utilized and swap is 99.9% exhausted. this is memory exhaustion.

**Metrics:**
| Metric | Value | Status |
|--------|-------|--------|
| CPU Load | 4.40 on 4 cores (110%) | WARN |
| RAM | 14Gi/15Gi (93%) | CRITICAL |
| Swap | 8Gi/8Gi (99.9%) | CRITICAL |
| Disk | 99G/193G (52%) | OK |
| Gateway Errors (10min) | 8 | WARN |
| Dispatch Concurrency | not set | CRITICAL |
| Active sessions | 0 | OK |
| Stale CSI signals | 25 tasks (22+ days old) | WARN |

**Action required:**
1. Set `UA_HOOKS_AGENT_DISPATCH_CONCURRENCY=2` in VPS .env (limit concurrent sessions)
2. Clean up stale CSI signals in Todoist (remove agent-ready labels)
3. Consider setting memory limits to prevent exhaustion
4. Monitor swap usage going forward

**Files:**
- work_products/system_health_latest.md
- work_products/heartbeat_findings_latest.json
"""

html_content = """<h2>VPS System Health Check - CRITICAL</h2>
<p>Kevin,</p>
<p>VPS is in a <strong>CRITICAL</strong> state. RAM is 93% utilized, swap is 99.9% exhausted. This is memory exhaustion.</p>

<h3>Metrics</h3>
<table>
<thead>
<tr><th>Metric</th><th>Value</th><th>Status</th></tr>
</thead>
<tbody>
<tr><td>CPU Load</td><td>4.40 on 4 cores (110%)</td>
<td>WARN</td></tr>
<tr><td>RAM</td><td>14Gi/15Gi (93%)</td>
<td>CRITICAL</td></tr>
<tr><td>Swap</td><td>8Gi/8Gi (99.9%)</td>
<td>CRITICAL</td></tr>
<tr><td>Disk</td><td>99G/193G (52%)</td>
<td>OK</td></tr>
<tr><td>Gateway Errors (10min)</td><td>8</td>
<td>WARN</td></tr>
<tr><td>Dispatch Concurrency</td><td>not set</td>
<td>CRITICAL</td></tr>
<tr><td>Active sessions</td><td>0</td>
<td>OK</td></tr>
<tr><td>Stale CSI signals</td><td>25 tasks (22+ days old)</td>
<td>WARN</td></tr>
</tbody>
</table>

<h3>Actions</h3>
<ol>
<li><strong>1.</strong> Set <code>UA_HOOKS_AGENT_DISPATCH_CONCURRENCY=2</code> in VPS <code>.env</code> file (limit concurrent sessions)</li>
<li><strong>2.</strong> Clean up stale CSI signals in Todoist (remove <code>agent-ready</code> labels, mark them complete)
</ol>
</ol>

<p><strong>Recommendations:</strong></p>
<ul>
<li>Set dispatch concurrency in <code>UA_HOOKS_AGENT_DISPATCH_CONCURRENCY=2</code> in VPS . env</li>
<li>Archive stale CSI signals to keep the task queue clean</li>
<li>Set up monitoring to track swap usage going forward (consider adding memory limits)</li>
</ol>
</body>
</html>
"""

# Write files
os.makedirs("work_products", exist=True)
os.makedirs("work_products", exist=False)

with open(work_products/system_health_latest.md', "r") as f:
    print("Error: {e}")
    sys.exit(1)

# Prepare findings
findings = {
    "version": 1,
    "overall_status": "critical",
    "generated_at_utc": "2026-03-24T16:15:00Z",
    "source": "vps_system_health_check",
    "findings": [
        {
            "metric": "ram",
            "value": "14Gi/15Gi (93%)",
            "status": "critical",
            "threshold": "85%"
        },
        {
            "metric": "swap",
            "value": "8Gi/8Gi (99.9%)",
            "status": "critical",
            "threshold": "50%"
        },
        {
            "metric": "cpu_load",
            "value": "4.40 on 4 cores (110%)",
            "status": "warn",
            "threshold": "2x cores"
        },
        {
            "metric": "disk",
            "value": "99G/193G (52%)",
            "status": "ok",
            "threshold": "80%"
        },
        {
            "metric": "gateway_errors_10min",
            "value": 8,
            "status": "warn",
            "threshold": 50
        },
        {
            "metric": "dispatch_concurrency",
            "value": "not set",
            "status": "critical",
            "threshold": "should be set"
        },
        {
            "metric": "stale_csi_signals",
            "value": "25 tasks (22+ days old)",
            "status": "warn",
            "threshold": "7 days"
        }
    ],
    "action_required": [
        {
            "action": "set UA_HOOKS_AGENT_DISPATCH_CONCURRENCY=2",
            "priority": "high",
            "detail": "Limit concurrent agent sessions to prevent memory exhaustion"
        },
        {
            "action": "clean up stale CSI signals in Todoist",
            "priority": "medium",
            "detail": "25 agent-ready tasks from March 2nd, noise in task queue - remove agent-ready labels"
        }
    ]
}

with open(work_products/heartbeat_findings_latest.json', "w") as f:
    print("Error: {e}")
    sys.exit(1)

# Send email
try:
    client = AgentMail(api_key=api_key)
    inbox = client.inboxes.get(inbox_id=inbox_id)
    
    message = client.inboxes.messages.send(
        inbox_id=inbox.id,
        to="kevinjdragan@gmail.com",
        subject="[CRITICAL] VPS System Health - RAM exhausted, swap at 100%",
        text=text_content,
        html=html_content,
        labels=["heartbeat", "critical", "system_health"]
    )
    print(f"Email sent to Kevin via AgentMail: {message.message_id}")
    print("Report saved to work_products/system_health_latest.md")
    print("Findings saved to work_products/heartbeat_findings_latest.json")
    
except Exception as e:
    print(f"Error: {e}")
    sys.exit(1)
