# End-to-End Research & Report Workflow

**Version**: 1.0
**Last Updated**: 2025-12-22
**Status**: Active

## Overview

This document details the complete end-to-end workflow for research and report generation in the Universal Agent system. The workflow spans from user request initiation through search, extraction, synthesis, and final email delivery.

### High-Level Flow

```
User Request
    ↓
Query Classification (SIMPLE vs COMPLEX)
    ↓
[COMPLEX PATH] Search Phase (SERP via Composio)
    ↓
    ↓
Observer Pattern: Save search_results/*.json
    ↓
Delegation: Main Agent → report-creation-expert Sub-Agent
    ↓
Sub-Agent: crawl_parallel (Scrape 10+ URLs in ONE call)
    ↓
Sub-Agent: Reads extracted markdown files from search_results/
    ↓
Sub-Agent: Synthesize HTML Report (Quality Standards)
    ↓
SubAgent: write_local_file → work_products/report.html
    ↓
Main Agent: Receives Report, upload_to_composio → S3 Key
    ↓
Main Agent: GMAIL_SEND_EMAIL with S3 Key Attachment
    ↓
Final: Report Delivered to User
```

---

## 1. End-to-End Workflow Overview

```mermaid
sequenceDiagram
    autonumber
    participant User
    participant MainAgent as Main Agent<br/>(Claude SDK)
    participant ComposioMCP as Composio MCP<br/>(Remote Tools)
    participant Observer as Observer Pattern<br/>(Async Artifact Saver)
    participant SubAgent as report-creation-expert<br/>(Sub-Agent)

    participant LocalToolkit as Local Toolkit MCP<br/>(File Operations)
    participant RemoteWorkbench as Remote Workbench<br/>(Execution Sandbox)
    participant GmailService as Gmail Service<br/>(Composio)

    Note over User,GmailService: PHASE 1: INITIATION & CLASSIFICATION
    User->>MainAgent: "Research X and email me a comprehensive report"
    MainAgent->>MainAgent: classify_query()
    Note right of MainAgent: Determines COMPLEX<br/>due to "comprehensive"<br/>keyword + external need

    Note over User,GmailService: PHASE 2: SEARCH (SERP)
    MainAgent->>ComposioMCP: COMPOSIO_SEARCH_NEWS(query)
    ComposioMCP-->>MainAgent: JSON: [{title, link, snippet, source, date}]
    MainAgent->>Observer: Trigger: observe_and_save_search_results()
    Observer->>Observer: Parse SERP JSON<br/>Extract news_results
    Observer->>Observer: Save: search_results/composio_search_news_HHMMSS.json
    Note right of Observer: Artifact saved<br/>non-blocking

    Note over User,GmailService: PHASE 3: DELEGATION
    MainAgent->>SubAgent: Task(report-creation-expert)<br/>+ search results + workspace path
    Note right of MainAgent: Mandatory delegation<br/>for "comprehensive" reports

    Note over User,GmailService: PHASE 4: PARALLEL EXTRACTION
    SubAgent->>LocalToolkit: crawl_parallel(urls=[url1..url10])
    LocalToolkit->>FileSystem: Save 10 markdown files to search_results/
    LocalToolkit-->>SubAgent: {success: true, saved_files: [...]}

    Note over User,GmailService: PHASE 5: SYNTHESIS
    SubAgent->>FileSystem: read_local_file(search_results/crawl_hash.md)
    FileSystem-->>SubAgent: Markdown content
    SubAgent->>SubAgent: Analyze & Synthesize HTML Report
    SubAgent->>SubAgent: Analyze corpus<br/>Thematic synthesis
    SubAgent->>SubAgent: Generate HTML report<br/>(Exec Summary, ToC, Data Table, Sources)
    SubAgent->>LocalToolkit: write_local_file(path, content=HTML)
    LocalToolkit->>LocalToolkit: Write: work_products/report_topic_month_year.html
    LocalToolkit-->>SubAgent: "Successfully wrote N chars"
    SubAgent-->>MainAgent: Report complete

    Note over User,GmailService: PHASE 7: DELIVERY
    MainAgent->>LocalToolkit: upload_to_composio(local_path)
    LocalToolkit->>ComposioMCP: Upload file and get S3 key
    ComposioMCP-->>LocalToolkit: {s3_key: "...", s3_url: "..."}
    LocalToolkit-->>MainAgent: Upload success
    MainAgent->>GmailService: GMAIL_SEND_EMAIL(to, subject, body, attachments=[{"s3_key": "..."}])
    GmailService-->>MainAgent: Email sent successfully
    MainAgent-->>User: "Report emailed to [address]"
```

---

## 2. Detailed Phase: Search & SERP Processing

```mermaid
sequenceDiagram
    autonumber
    participant MainAgent as Main Agent
    participant ComposioMCP as Composio MCP<br/>(SEARCH_NEWS)
    participant Observer as Observer<br/>(observe_and_save_search_results)
    participant FileSystem as File System<br/>(search_results/)
    participant Logfire as Logfire<br/>(Tracing)

    Note over MainAgent,Logfire: STEP 1: Tool Invocation
    MainAgent->>ComposioMCP: COMPOSIO_SEARCH_NEWS(query="latest AI developments", num_results=10)
    Note right of MainAgent: Input: {query, num_results,<br/>country, language}

    ComposioMCP->>ComposioMCP: Execute search on remote server
    Note right of ComposioMCP: Uses SERP API<br/>(Google News/Bing)

    Note over MainAgent,Logfire: STEP 2: Response Processing
    ComposioMCP-->>MainAgent: TextBlock with JSON<br/>data.news_results[]
    Note right of ComposioMCP: Schema: [{position, title,<br/>link, snippet, source,<br/>date, ...}]

    MainAgent->>Logfire: Log: tool_result<br/>{content_size, preview}
    Logfire-->>MainAgent: Logged

    Note over MainAgent,Logfire: STEP 3: Observer Trigger (Non-Blocking)
    MainAgent->>Observer: asyncio.create_task(<br/>observe_and_save_search_results(<br/>tool_name="COMPOSIO_SEARCH_NEWS",<br/>content=TextBlock,<br/>workspace_dir))
    Note right of MainAgent: Fire-and-forget<br/>async task

    Observer->>Observer: Extract raw_json from TextBlock.text
    Observer->>Observer: json.loads(raw_json)

    Note over MainAgent,Logfire: STEP 4: SERP Parsing
    Observer->>Observer: Check for "data" wrapper
    Observer->>Observer: Check for "results" key (multi-execute)
    Observer->>Observer: Extract news_results or organic_results

    Observer->>Observer: safe_get_list(data, "news_results")
    Note right of Observer: Handles both dict<br/>and list formats

    Observer->>Observer: For each article: map fields<br/>{position, title, url, source,<br/>date (parsed), snippet}

    Note over MainAgent,Logfire: STEP 5: Artifact Creation
    Observer->>FileSystem: Create directory:<br/>AGENT_RUN_WORKSPACES/session_XXX/search_results/

    Observer->>FileSystem: Write file:<br/>composio_search_news_HHMMSS.json
    Note right of FileSystem: Content:<br/>{<br/>  type: "news",<br/>  timestamp: ISO,<br/>  tool: "composio_search_news",<br/>  articles: [...]<br/>}

    FileSystem-->>Observer: File created (size bytes)

    Note over MainAgent,Logfire: STEP 6: Confirmation
    Observer->>Logfire: Log: observer_artifact_saved<br/>{path, type, size}
    Observer->>MainAgent: Print: "Saved: {filename} ({size} bytes)"

    Note right of Observer: Artifact saved<br/>without blocking<br/>agent loop
```

### Search Results Schema

**File**: `search_results/composio_search_news_HHMMSS.json`

```json
{
  "type": "news",
  "timestamp": "2025-12-22T14:30:45.123456",
  "tool": "composio_search_news",
  "articles": [
    {
      "position": 1,
      "title": "OpenAI Announces GPT-5.2 with 70.7% GDPval Score",
      "url": "https://example.com/openai-gpt52",
      "source": "TechCrunch",
      "date": "2025-12-20",
      "snippet": "OpenAI released GPT-5.2 achieving 70.7% on GDPval..."
    }
  ]
}
```

**Code References**:
- Observer function: `main.py:218-413` (`observe_and_save_search_results`)
- Date parsing: `main.py:137-159` (`parse_relative_date`)
- SERP parsing: `main.py:323-368`

---

## 3. Detailed Phase: Parallel Extraction

```mermaid
sequenceDiagram
    participant SubAgent
    participant CrawlTool
    participant FS as FileSystem

    SubAgent->>CrawlTool: crawl_parallel(urls=[...])
    CrawlTool->>CrawlTool: Initialize crawl4ai browser
    CrawlTool->>CrawlTool: Parallel Fetch 10 URLs
    CrawlTool->>CrawlTool: Extract Markdown
    
    loop For each URL
        CrawlTool->>FS: write search_results/crawl_hash.md
    end

    CrawlTool-->>SubAgent: Returns summary JSON
```

---

## 4. Detailed Phase: Report Synthesis & Delivery

```mermaid
sequenceDiagram
    autonumber
    participant SubAgent as report-creation-expert
    participant Corpus as expanded_corpus.json
    participant LocalToolkit as Local Toolkit MCP
    participant WorkProducts as work_products/<br/>File System
    participant MainAgent as Main Agent
    participant WorkbenchBridge as WorkbenchBridge<br/>(Composio)
    participant RemoteWorkbench as Remote Workbench<br/>Sandbox
    participant GmailService as Gmail<br/>(Composio)
    participant User as User Email

    Note over SubAgent,User: STEP 1: Synthesis Analysis
    SubAgent->>Corpus: Read expanded_corpus.json
    Corpus-->>SubAgent: Articles (with FULL content)

    SubAgent->>SubAgent: Analyze corpus<br/>Identify themes
    SubAgent->>SubAgent: Extract specific numbers<br/>(70.7%, 9.19M, dates)
    SubAgent->>SubAgent: Find direct quotes<br/>("biggest dark horse")
    Note right of SubAgent: Thematic synthesis<br/>(NOT source-by-source)

    Note over SubAgent,User: STEP 2: HTML Generation (Quality Standards)
    SubAgent->>SubAgent: Generate structure:<br/>1. Executive Summary<br/>   (with key stats box)<br/>2. Table of Contents<br/>   (anchor links)<br/>3. Thematic Sections<br/>   (weave facts across sources)<br/>4. Summary Data Table<br/>   (Development/Org/Highlights)<br/>5. Sources<br/>   (clickable links)

    SubAgent->>SubAgent: Apply evidence standards:<br/>- Specific numbers<br/>- Direct quotes<br/>- Date citations<br/>- Source attribution<br/>(Source Name)

    SubAgent->>SubAgent: Apply HTML quality:<br/>- Modern CSS gradients<br/>- Info boxes for stats<br/>- Highlight boxes<br/>- Responsive design<br/>- Professional color scheme

    Note over SubAgent,User: STEP 3: Save Report
    SubAgent->>LocalToolkit: write_local_file(<br/>path="SESSION_WORKSPACE/work_products/<br/>ai_developments_december_2025.html",<br/>content="<html>...</html>"<br/>)

    LocalToolkit->>LocalToolkit: Create directory:<br/>work_products/
    LocalToolkit->>WorkProducts: Write: report_topic_month_year.html
    WorkProducts-->>LocalToolkit: File written
    LocalToolkit-->>SubAgent: "Successfully wrote N chars to path"

    Note over SubAgent,User: STEP 4: Return to Main Agent
    SubAgent-->>MainAgent: Task complete<br/>(includes report preview)

    MainAgent->>MainAgent: Verify sub-agent compliance<br/>check: expanded_corpus.json exists
    Note right of MainAgent: verify_subagent_compliance()<br/>Inject error if missing

    Note over SubAgent,User: STEP 5: Upload for Attachment
    MainAgent->>LocalToolkit: upload_to_composio(<br/>path="SESSION_WORKSPACE/<br/>work_products/report.html"<br/>)

    LocalToolkit->>ComposioMCP: Upload to Composio S3
    ComposioMCP-->>LocalToolkit: {<br/>"s3_key": "uploads/report.html",<br/>"s3_url": "..."<br/>}
    LocalToolkit-->>MainAgent: "Successfully uploaded"

    Note over SubAgent,User: STEP 6: Email Delivery
    MainAgent->>GmailService: GMAIL_SEND_EMAIL(<br/>to="user@example.com",<br/>subject="Comprehensive Report: AI Developments",<br/>body="Please find attached...",<br/>attachments=[{"s3_key": "uploads/report.html"}]<br/>)

    GmailService->>GmailService: Send via Gmail API<br/>with attachment
    GmailService-->>MainAgent: "Email sent successfully"

    Note over SubAgent,User: STEP 7: Final Confirmation
    MainAgent-->>User: "Comprehensive report emailed<br/>to user@example.com"
```

### Report Structure Requirements

**Mandatory Sections**:

```html
<!DOCTYPE html>
<html>
<head>
  <style>
    /* Modern CSS with gradients and shadows */
    .executive-summary {
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      color: white;
      padding: 2rem;
      border-radius: 8px;
    }
    .info-box {
      background: #f3f4f6;
      border-left: 4px solid #667eea;
      padding: 1rem;
      margin: 1rem 0;
    }
    .data-table {
      border-collapse: collapse;
      width: 100%;
    }
    .data-table th, .data-table td {
      border: 1px solid #e5e7eb;
      padding: 0.75rem;
    }
  </style>
</head>
<body>
  <!-- 1. Executive Summary with highlight box -->
  <section class="executive-summary">
    <h2>Executive Summary</h2>
    <div class="info-box">
      <strong>Key Stat:</strong> GPT-5.2 achieved 70.7% on GDPval (OpenAI)
    </div>
  </section>

  <!-- 2. Table of Contents with anchors -->
  <nav>
    <h2>Table of Contents</h2>
    <ul>
      <li><a href="#performance">Performance Breakthroughs</a></li>
      <li><a href="#efficiency">Efficiency Gains</a></li>
    </ul>
  </nav>

  <!-- 3. Thematic sections (weave facts across sources) -->
  <section id="performance">
    <h2>Performance Breakthroughs</h2>
    <p>OpenAI's GPT-5.2 achieved <strong>70.7% on GDPval</strong> (TechCrunch), representing a significant jump from previous models. Meanwhile, DeepSeek-V3 has emerged as the "biggest dark horse in the open-source LLM arena" (VentureBeat), with competitive performance on benchmarks.</p>
  </section>

  <!-- 4. Summary Data Table -->
  <table class="data-table">
    <thead>
      <tr>
        <th>Development</th>
        <th>Organization</th>
        <th>Key Highlights</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td>GPT-5.2</td>
        <td>OpenAI</td>
        <td>70.7% GDPval, December 11, 2025</td>
      </tr>
      <tr>
        <td>DeepSeek-V3</td>
        <td>DeepSeek</td>
        <td>Biggest dark horse in OSS arena</td>
      </tr>
    </tbody>
  </table>

  <!-- 5. Sources -->
  <section>
    <h2>Sources</h2>
    <ul>
      <li><a href="https://...">TechCrunch - OpenAI Announces GPT-5.2</a></li>
      <li><a href="https://...">VentureBeat - DeepSeek-V3 Release</a></li>
    </ul>
  </section>
</body>
</html>
```

### Quality Standards

| Evidence Type | Do This | Don't Do This |
|---------------|---------|---------------|
| Numbers | "70.7% on GDPval" | "Performed well" |
| Dates | "December 11, 2025" | "Recently released" |
| Quotes | "biggest dark horse in open-source LLM arena" | "DeepSeek is competitive" |
| Comparisons | "38% fewer hallucinations than GPT-5.1" | "Fewer hallucinations" |
| Attribution | "(TechCrunch)" after claim | No source attribution |

**Code References**:
- Quality standards: `.claude/agents/report-creation-expert.md:88-120`
- HTML template: `.claude/agents/report-creation-expert.md:114-120`
- File write: `mcp_server.py:53-66` (`write_local_file`)
- Workbench upload: `mcp_server.py:41-50` (`workbench_upload`)
- Compliance check: `main.py:629-672` (`verify_subagent_compliance`)

---

## 5. Workspace Artifacts

### Directory Structure

```
AGENT_RUN_WORKSPACES/
└── session_20251222_143045/
    ├── search_results/
    │   ├── composio_search_news_143046.json       # SERP results
    │   └── composio_search_news_143047_0.json     # Additional searches
    ├── extracted_articles/
    │   ├── example_com_article1_143100.json       # Individual articles
    │   └── techcrunch_com_gpt52_143105.json
    ├── expanded_corpus.json                        # MANDATORY CHECKPOINT
    ├── work_products/
    │   └── ai_developments_december_2025.html     # Final report
    ├── workbench_activity/
    │   └── workbench_143200.json                  # Code execution logs
    ├── run.log                                    # Full session log
    ├── trace.json                                 # Execution trace
    └── summary.txt                                # Session summary
```

### Artifact Lifecycles

| Artifact | Created By | Phase | Purpose |
|----------|------------|-------|---------|
| `search_results/*.json` | Observer | Search | SERP storage for sub-agent |
| `extracted_articles/*.json` | Observer | Extraction | Individual article storage |
| `expanded_corpus.json` | Sub-Agent | Extraction | MANDATORY checkpoint for audit |
| `work_products/*.html` | Sub-Agent | Synthesis | Final report output |
| `trace.json` | Main Agent | All phases | Execution observability |

---

## 6. Error Handling & Retry Logic

### Network Errors (Code 1234)

```
Error: "MCP error: Network error" (171 bytes)
Action: Queue for retry (max 1 retry per URL)
Retry Strategy: 2 at a time (lower concurrency than initial batch)
```

### Not Found Errors (Code 1214)

```
Error: "MCP error: Not found" (90 bytes)
Action: Mark as failed, NO retry
Side Effect: Update domain blacklist (3 strikes = blacklisted)
```

### Blacklist Tracking



```json
{
  "domains": {
    "paywall.example.com": {
      "failures": 3,
      "last_failure": "2025-12-22T14:30:00"
    }
  },
  "threshold": 3
}
```

**Code References**:
- Error handling: `.claude/agents/report-creation-expert.md:63-70`
- Blacklist logic: `main.py:167-205`
- Observer error detection: `main.py:508-540`

---

## 7. Observability & Tracing

### Logfire Spans

```
standalone_composio_test (root span)
├── conversation_iteration_1
│   ├── tool_call (COMPOSIO_SEARCH_NEWS)
│   ├── tool_result
│   ├── observer_artifact_saved
│   ├── conversation_iteration_2
│   │   ├── tool_call (Task / report-creation-expert)
│   │   └── tool_result
│   └── ...
└── session_complete
```

### Key Logfire Events

| Event | Level | Context |
|-------|-------|---------|
| `query_classification` | Info | SIMPLE vs COMPLEX decision |
| `tool_call` | Info | Tool name, input_size, is_subagent_call |
| `tool_result` | Info | Content size, is_error |
| `observer_artifact_saved` | Info | Path, type, size |
| `article_extracted` | Info | URL, title, content_length |
| `corpus_enriched` | Info | File, URL |
| `webreader_timeout_error` | Warning | URL, error_code 1234 |
| `webreader_not_found` | Warning | URL, error_code 1214 |
| `domain_blacklisted` | Warning | Domain, failures |
| `subagent_compliance_failed` | Warning | Missing expanded_corpus.json |

**Code References**:
- Logfire config: `main.py:49-111`
- Span creation: `main.py:696-704` (conversation iteration)
- Event logging: Throughout `main.py` (see `logfire.info`, `logfire.warning`)

---

## 8. Temporal Consistency

### Date Handling

```
System Prompt Injection:
"Result Date: Monday, December 22, 2025"
"TEMPORAL CONSISTENCY WARNING: You are operating in a timeline where it is December 2025."
```

### Relative Date Parsing

**Input**: "2 hours ago"
**Output**: "2025-12-22" (via `parse_relative_date()`)

**Code References**:
- Date parsing: `main.py:137-159`
- System prompt injection: `main.py:1027-1030`

---

## 9. Configuration & Environment Variables

### Required Environment Variables

| Variable | Purpose | Example |
|----------|---------|---------|
| `COMPOSIO_API_KEY` | Composio MCP authentication | `key_...` |

| `LOGFIRE_TOKEN` | Tracing backend | `logfire_...` |
| `LOGFIRE_PROJECT_SLUG` | Logfire project | `Kjdragan/composio-claudemultiagent` |

### MCP Server Configuration

**Code Reference**: `main.py:1075-1093`

```python
mcp_servers={
    "composio": {
        "type": "http",
        "url": session.mcp.url,
        "headers": {"x-api-key": COMPOSIO_API_KEY}
    },
    "local_toolkit": {
        "type": "stdio",
        "command": sys.executable,
        "args": ["src/mcp_server.py"]
    },
    "web_reader": {
        "type": "http",
        "url": "https://api.z.ai/api/mcp/web_reader/mcp",
        "headers": {"Authorization": f"Bearer {ZAI_API_KEY}"}
    }
}
```

---

## 10. Performance Considerations

### Concurrency Limits

| Phase | Concurrency | Rationale |
|-------|-------------|-----------|
| Parallel Crawl | 10 urls | Fast async scrape |
| Retry 1234 errors | 2 parallel | Lower concurrency for unstable URLs |
| Observer saves | Async non-blocking | Don't block agent loop |

### Hard Stop Limits

| Limit | Value | Reason |
|-------|-------|--------|
| Max successful extractions | 10 | Sufficient for comprehensive report |
| Max batches | 2 | Prevent infinite extraction loops |
| Max retries per URL | 1 | Avoid wasting time on failed domains |

### Data Flow Optimization

```python
# Default: Data returns directly to context (fast)
sync_response_to_workbench=False

# Only use True for massive data (>5MB)
if expected_size > 5_000_000:
    sync_response_to_workbench=True
    # Then: workbench_download to fetch full file
```

**Code Reference**: `main.py:1043-1047` (DATA FLOW POLICY)

---

## 11. Compliance & Verification

### Sub-Agent Compliance Check

**Trigger**: After Task (sub-agent) completion

**Check**: Does `expanded_corpus.json` exist in workspace?

**Failure Action**: Inject error message into response

```python
def verify_subagent_compliance(tool_name, tool_content, workspace_dir):
    if "task" not in tool_name.lower():
        return None

    corpus_path = os.path.join(workspace_dir, "expanded_corpus.json")
    if not os.path.exists(corpus_path):
        logfire.warning("subagent_compliance_failed", ...)
        return "❌ COMPLIANCE ERROR: The report-creation-expert did not save expanded_corpus.json..."
```

**Code Reference**: `main.py:629-672`

---

## 12. Troubleshooting Guide

### Issue: No articles extracted

**Symptoms**: `search_results/` is empty or lacks `.md` files after extraction phase.

**Possible Causes**:
1. `crawl4ai` extraction failed for all URLs
2. Network connectivity issues
3. URLs blocked or inaccessible

**Debug Steps**:
1. Check `search_results/` content
2. Review Logfire traces for `crawl_parallel` tool output

### Issue: Report not emailed

**Symptoms**: Report exists in `work_products/` but no email sent

**Possible Causes**:
1. `workbench_upload` failed
2. Gmail authentication required
3. Remote path incorrect in GMAIL_SEND_EMAIL

**Debug Steps**:
1. Check trace for `workbench_upload` result
2. Check for auth link in trace
3. Verify remote path matches upload destination



---

## 13. Key Files Reference

| File | Lines | Purpose |
|------|-------|---------|
| `src/universal_agent/main.py` | 1-1379 | Main agent with observer pattern |
| `.claude/agents/report-creation-expert.md` | 1-137 | Sub-agent instructions |
| `src/mcp_server.py` | 1-140 | Local toolkit MCP tools |
| `src/tools/workbench_bridge.py` | - | Workbench file transfer |

### Key Functions

| Function | Location | Purpose |
|----------|----------|---------|
| `classify_query()` | main.py:917-956 | SIMPLE vs COMPLEX routing |
| `run_conversation()` | main.py:691-914 | Main agent loop |
| `observe_and_save_search_results()` | main.py:218-413 | SERP artifact saver |
| `workbench_upload()` | mcp_server.py:41-50 | MCP tool for remote upload |
| `write_local_file()` | mcp_server.py:53-66 | MCP tool for local file write |

---

## 14. Summary Flowchart

```mermaid
flowchart TD
    Start([User Request]) --> Classify{Classify Query}
    Classify -->|SIMPLE| FastPath[Direct Answer]
    Classify -->|COMPLEX| Search[Search Phase]

    Search --> SERP[COMPOSIO_SEARCH_NEWS]
    SERP --> Observer1[Observer: Save SERP]
    Observer1 --> Delegate{Is Report?}

    Delegate -->|Yes| SubAgent[Task: report-creation-expert]
    Delegate -->|No| Continue[Continue Main Agent]

    SubAgent --> CheckType{Comprehensive?}
    CheckType -->|Yes| Extract[Parallel Crawl]
    CheckType -->|No| Direct[Use Search Snippets]

    Extract --> Synthesize[Synthesize Report]
    Direct --> Synthesize


    Synthesize --> WriteReport[write_local_file<br/>work_products/report.html]
    WriteReport --> ReturnReport[Return to Main Agent]

    ReturnReport --> Upload[upload_to_composio<br/>Get S3 Key]

    Upload --> Email[GMAIL_SEND_EMAIL<br/>with attachment s3_key]

    Email --> End([Report Delivered])

    FastPath --> End
    Continue --> End
```

---

## Appendix A: Tool Call Examples

### Example 1: Search

```json
{
  "name": "mcp__composio__SEARCH_NEWS",
  "input": {
    "query": "latest AI developments December 2025",
    "num_results": 10
  }
}
```

### Example 2: Article Extraction

```json
{
  "name": "mcp__local_toolkit__crawl_parallel",
  "input": {
    "urls": ["https://techcrunch.com/...", "https://example.com/..."],
    "session_dir": "/path/to/workspace"
  }
}
```



### Example 4: Email

```json
{
  "name": "mcp__composio__GMAIL_SEND_EMAIL",
  "input": {
    "to": "user@example.com",
    "subject": "Comprehensive Report: AI Developments",
    "body": "Please find attached the comprehensive report...",
    "attachments": [{"s3_key": "user/uploads/report.html"}]
  }
}
```

---

**Document Version**: 1.0
**Maintained By**: Universal Agent Team
**Related Docs**:
- `/docs/000_CURRENT_CONTEXT.md` - Current project status
- `/docs/012_LOCAL_VS_WORKBENCH_ARCHITECTURE.md` - Local vs remote architecture
- `/docs/004_HOOKS_ARCHITECTURE.md` - Observer pattern details
