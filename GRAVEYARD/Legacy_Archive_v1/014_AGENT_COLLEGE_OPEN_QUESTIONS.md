# Agent College: Open Questions & Design Decisions

> **Purpose**: This document captures all open design questions, architectural decisions pending, and implementation challenges for the Agent College self-improvement subsystem. It serves as the exploration agenda for continued development.

## Context: LangSmith-Fetch Inspiration

The Agent College architecture is inspired by [LangSmith-Fetch](https://github.com/langchain-ai/langsmith-fetch), a project that provides an API for retrieving and analyzing traces from LangSmith.

**Key Differences**:
| Aspect | LangSmith-Fetch | Our Implementation |
|--------|-----------------|-------------------|
| **Tracing Provider** | LangSmith | Logfire (Pydantic) |
| **API Access** | LangSmith has active REST API | Logfire has `LogfireQueryClient` (SQL-based) |
| **Data Transport** | HTTP API calls | SQL queries via DataFusion |

**Exploration Needed**:
1. Can we emulate LangSmith-Fetch's API-driven approach for Logfire?
2. Should we build a FastAPI layer that mirrors LangSmith-Fetch endpoints?
3. Or is polling + SQL queries sufficient for our use case?

**Reference**: See `AgentCollege/LANGSMITH_FETCH_ANALYSIS.md` for initial research.

---

## Logfire Capabilities Investigation

> [!IMPORTANT]
> **Action Item**: Use DeepWiki to investigate Logfire's full capabilities for Agent College use cases.

### The Problem

We're currently instrumenting the whole project with Logfire, which generates a LOT of trace data. For Agent College, we need a **focused trace review system** — not the entire firehose.

### Questions to Investigate (via DeepWiki / Logfire Docs)

1. **Saved Searches / Dashboards**:
   - Does Logfire support saved/named queries?
   - Can we create a dashboard specifically for "Agent College Review"?
   - Are there pre-built filters for errors/exceptions only?

2. **Logfire MCP Integration**:
   - Can we describe a custom query/filter to the Logfire MCP server?
   - Is there a way to create a "virtual view" that filters for Agent College-relevant traces?
   - Can we tag specific spans as "agent-college-relevant" during instrumentation?

3. **Alerting / Webhooks (Native)**:
   - Does Logfire have built-in alerting that can push to webhooks?
   - Can we define alert rules (e.g., "any span with exception_type IS NOT NULL")?
   - Would this eliminate the need for polling entirely?

4. **Custom Instrumentation**:
   - Should we add a specific `logfire.span("agent_college_event")` for important events?
   - Can we use tags/attributes to mark spans for Agent College filtering?
   - Example: `logfire.info("task_completed", tags=["agent_college"])`

5. **Trace Sampling / Filtering**:
   - Can we configure Logfire to only store certain span types in detail?
   - Is there a way to reduce noise at the source?

### Proposed Exploration Path

1. **DeepWiki Query**: Ask about Logfire's alerting, saved searches, and MCP capabilities
2. **Logfire Dashboard**: Manually explore the UI for dashboard/saved search features
3. **Documentation Review**: Read Logfire docs on webhooks, alerts, and filtering
4. **Prototype**: If promising features exist, build a proof-of-concept integration

---

## Unified Hooks & Triggers Architecture

> [!WARNING]
> **Integration Challenge**: As our system grows, we have hooks/triggers scattered across multiple systems. We need a unified approach.

### The Problem

We have event-driven capabilities in multiple places:
- **Logfire**: Trace events, potentially webhooks/alerts
- **Composio**: Triggers system (GitHub commits, Slack messages, etc.)
- **Agent SDK**: `SubagentStop`, `before_execute`, `after_execute` hooks
- **LogfireFetch**: Our custom webhook endpoint

These are currently disconnected. For Agent College to work properly, we may need to unify them.

### Composio Triggers (Reference)

Composio has a full triggers system with webhooks. Key capabilities:
- **Event Types**: GitHub commits, Slack messages, Gmail new email, etc.
- **Webhook Delivery**: Sends to your endpoint with signed payloads
- **SDK Subscription**: Can subscribe directly in Python for prototyping
- **Dashboard Management**: Enable/disable triggers via UI

**Example Use Case for Agent College**:
```python
# Subscribe to "agent crashed" or "task failed" events
@subscription.handle(trigger_id="agent_error_trigger")
def handle_agent_error(data):
    critic.propose_correction(data['trace_id'], data['error'])
```

**Setup Requirements**:
- Public webhook URL (ngrok for local dev, or cloud deployment)
- Webhook signature verification (HMAC-SHA256)
- Dashboard configuration at platform.composio.dev

### Questions to Explore

1. **Hook Unification**:
   - Should we create a central event bus that all hooks feed into?
   - Or keep them separate with Agent College polling each source?

2. **Composio Triggers for Agent Events**:
   - Can we create a custom Composio trigger for "agent run completed"?
   - Would this allow Agent College to react to any agent event?

3. **Logfire → Composio Bridge**:
   - Can Logfire alerts trigger Composio actions?
   - Would this create a unified alerting pipeline?

4. **Local Development (ngrok)**:
   - For webhook-based approaches, we need public URLs
   - ngrok setup for local testing
   - Alternative: polling mode that doesn't require public endpoint

5. **Production Architecture**:
   - If we deploy Agent College to cloud, can it receive webhooks from:
     - Logfire (if they support it)
     - Composio (definitely supports)
     - Custom sources

### Proposed Exploration Path

1. **Inventory Current Hooks**: Document all hook points in the system
2. **Test Composio Triggers**: Create a trigger and verify webhook receipt
3. **Design Event Schema**: Standardize event format across sources
4. **Build Adapter Layer**: If needed, create adapters that normalize events
5. **Decide Push vs Pull**: Webhooks (push) vs Polling (pull) for each source

---


### The Problem
There may be significant latency between when issues are stored in `[AGENT_COLLEGE_NOTES]` and when they're surfaced to the user. By the time the user sees them, the issue may have already been resolved.

### Questions to Explore
1. **Staleness Detection**: How do we check if an issue has already been fixed?
   - Compare error signatures against recent successful runs?
   - Check if the code file has been modified since the error?
   - Query Logfire for "same error, now passing"?

2. **Auto-Expiration**: Should notes expire after N hours/days if not addressed?

3. **Resolution Tracking**: Should we mark notes as "resolved" vs "pending" vs "graduated"?

### Proposed Exploration
- Define what "resolved" means (same code path succeeds? user dismissed? skill created?)
- Implement a `status` field on each note entry
- Consider a "recently resolved" filter when surfacing

---

## 2. Critic Filtering & Thresholds

### The Problem
Not every error or warning is worth surfacing. Minor issues (e.g., `subagent_no_artifacts`) may be noise. We need a filtering mechanism to determine what rises to the level of being added to Agent College Notes.

### Questions to Explore
1. **Severity Thresholds**: What log levels should trigger Critic?
   - Level 13 (WARNING)? Level 15 (ERROR)? Level 17 (CRITICAL)?
   - Should we use exception presence vs log level?

2. **Frequency Filtering**: Should we only surface recurring errors?
   - "This error occurred 5 times in the last 10 runs" vs one-off

3. **Impact Assessment**: Can we estimate user impact?
   - Errors that caused task failure vs recoverable warnings

4. **Deduplication**: Same error across runs — store once or every time?

### Proposed Exploration
- Create a `CriticFilterConfig` with tunable thresholds
- Experiment with different severity levels
- Consider a "confidence score" for each critique

---

## 3. Human-in-the-Loop Triggers

### The Problem
How does the human actually see and act on Agent College Notes? What are the triggers for surfacing this information?

### Questions to Explore

#### A. Explicit User Command
- What is the command? `/review-notes`? `/agent-college-report`?
- Should this be a skill? A CLI flag? A workflow?
- Should it produce a formatted report or interactive dialogue?

#### B. Periodic/Scheduled Surfacing
- Should there be a cron-like job that runs daily/weekly?
- If the agent isn't running, where does the output go?
- Email? Slack? File in workspace?

#### C. On-Startup Check
- When the agent starts, should it check for pending notes?
- If notes exist, should it print a message like:
  ```
  ⚠️ Agent College has 3 pending suggestions. Run /review-notes to see them.
  ```
- Should it block or just inform?

#### D. Proactive Surfacing
- Can the Professor agent interrupt a session to surface critical issues?
- Risk: Annoying the user mid-task

### Proposed Exploration
- Define a `/review-notes` workflow
- Implement startup check with count display
- Consider a summary file written on each session end

---

## 4. Project vs Agent College Running State

### The Problem
The main agent script and Agent College (LogfireFetch) may not always be running together. We need to handle:
- Agent running, LogfireFetch stopped
- LogfireFetch running, Agent stopped
- Both stopped
- Both running

### Questions to Explore

1. **Decoupled Operation**: Should LogfireFetch run independently as a daemon?
   - Always-on service that accumulates notes
   - Agent consumes notes when it starts

2. **Catch-Up Mechanism**: If LogfireFetch was stopped, how do we backfill?
   - On startup, query Logfire for errors since last poll
   - Store `last_poll_timestamp` persistently

3. **Flag Files**: Should we use flag files to communicate state?
   - `AGENT_COLLEGE_NOTES_PENDING.flag` created when notes added
   - Agent checks for flag on startup
   - Deleted after user reviews

4. **Docker/Always-On**: Package entire system in Docker containers
   - LogfireFetch as one container (always running)
   - Agent as another (on-demand)
   - Shared volume for database

### Proposed Exploration
- Design a "catch-up" query for LogfireFetch startup
- Implement persistent `last_poll_timestamp`
- Create a simple flag file mechanism
- (Future) Dockerize for always-on operation

---

## 5. Scribe Agent: Success Pattern Recognition

### The Problem
The Scribe is the counterpart to the Critic — it captures successful execution patterns that could become skills. But "success" is common; we need sophisticated filtering.

### What the Scribe Does
- Analyzes successful traces
- Identifies patterns worth replicating
- Proposes facts/skills to the sandbox

### Questions to Explore

1. **Uniqueness Detection**: How do we identify a "novel" success?
   - First time using a particular tool combination?
   - Successful completion of a task type we previously failed?
   - User explicitly praised the output?

2. **Pattern Recognition**: What makes a success worthy of skill creation?
   - Multi-step workflows that worked
   - Error recovery that succeeded
   - Performance improvements

3. **False Positive Prevention**: How do we avoid "You ran a search. Congrats!" noise?
   - Require multi-tool chains
   - Require explicit user satisfaction signal
   - Only track task types, not individual runs

4. **Skill Generation Trigger**: When does a success become a skill proposal?
   - After N similar successes?
   - After user reviews and approves?
   - Automatic with human review of output?

### Proposed Exploration
- Define "noteworthy success" criteria
- Consider a scoring system (complexity × novelty × user_feedback)
- Start with high threshold (only very notable successes)

---

## 6. Professor Agent: Skill Graduation

### The Problem
The Professor reviews notes and proposes skill creation. But when and how?

### Questions to Explore

1. **Trigger Mechanism**: When does the Professor run?
   - On user command (`/graduate-skills`)?
   - Periodically (if Agent College is always-on)?
   - At session end?

2. **Independence**: Can Professor work without the main agent running?
   - Read sandbox from database
   - Generate skill files
   - Leave for user to review next session

3. **Approval Workflow**: How does HITL work?
   - Professor creates PR-like proposal?
   - User reviews and approves in next session?
   - Skill remains "draft" until approved?

4. **Skill Validation**: How do we test generated skills?
   - Run `quick_validate.py`
   - Attempt to use skill in sandbox environment
   - User manually tests

### Proposed Exploration
- Define Professor invocation command
- Consider "draft skills" directory
- Implement approval workflow with status tracking

---

## 7. Deployment Architecture

### The Problem
For Agent College to be truly useful, it may need to be "always on" — not dependent on the user running the main script.

### Options to Explore

1. **Local Daemon**: LogfireFetch as systemd service or background process

2. **Docker Compose**: Multi-container setup
   - Container A: LogfireFetch (always on)
   - Container B: Agent (on-demand)
   - Shared volume: Memory_System_Data

3. **Cloud Deployment**: (Future)
   - Deploy LogfireFetch to cloud
   - Configure Logfire webhooks
   - True always-on operation

4. **Hybrid**: Local for development, cloud for production

### Proposed Exploration
- Start with Docker Compose for local always-on
- Document deployment options
- Consider cloud deployment as future milestone

---

## 8. Polling Implementation Details

### The Problem
We chose polling over webhooks for local operation. Need to implement correctly.

### Questions to Explore

1. **Poll Interval**: How often?
   - 30 seconds? 1 minute? 5 minutes?
   - Configurable via environment variable?

2. **State Persistence**: Track last poll time
   - In-memory (lost on restart)?
   - File-based (simple)?
   - Database (consistent with Memory System)?

3. **Error Handling**: What if Logfire is unavailable?
   - Retry with backoff?
   - Log and continue?

4. **Query Optimization**: What to query?
   - Only errors with exceptions?
   - Include warnings?
   - Filter by service name?

### Proposed Implementation
```python
# Poll every 30 seconds
# Store last_poll in .agent_college_state.json
# Query: SELECT * FROM records WHERE level >= 15 AND start_timestamp > last_poll
```

---

## Summary: Prioritized Exploration Agenda

| Priority | Topic | Key Question |
|----------|-------|--------------|
| 1 | Polling Implementation | How to implement background polling |
| 2 | Critic Thresholds | What severity level triggers notes |
| 3 | HITL Triggers | `/review-notes` command and startup check |
| 4 | Staleness Detection | How to mark resolved issues |
| 5 | Scribe Filtering | Uniqueness detection for successes |
| 6 | Professor Workflow | Skill graduation process |
| 7 | Deployment | Docker/always-on architecture |

---

## Related Documentation

- [035_AGENT_COLLEGE_ARCHITECTURE.md](file:///home/kjdragan/lrepos/universal_agent/Project_Documentation/035_AGENT_COLLEGE_ARCHITECTURE.md) — Current implementation
- [034_LETTA_MEMORY_SYSTEM_MANUAL.md](file:///home/kjdragan/lrepos/universal_agent/Project_Documentation/034_LETTA_MEMORY_SYSTEM_MANUAL.md) — Memory System
- [AgentCollege/ARCHITECTURE.md](file:///home/kjdragan/lrepos/universal_agent/AgentCollege/ARCHITECTURE.md) — Design notes
