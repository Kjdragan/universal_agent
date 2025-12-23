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
Observer Pattern: Save search_results/*.json
    ↓
Delegation: Main Agent → report-creation-expert Sub-Agent
    ↓
Sub-Agent: Parallel Article Extraction (webReader, 5 at a time)
    ↓
Observer Pattern: Save extracted_articles/*.json, Enrich Corpus
    ↓
Sub-Agent: save_corpus → expanded_corpus.json (MANDATORY CHECKPOINT)
    ↓
Sub-Agent: Synthesize HTML Report (Quality Standards)
    ↓
Sub-Agent: write_local_file → work_products/report.html
    ↓
Main Agent: Receives Report, workbench_upload to Remote
    ↓
Main Agent: GMAIL_SEND_EMAIL with Remote Attachment
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
    participant WebReaderMCP as webReader MCP<br/>(Z.AI)
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

    Note over User,GmailService: PHASE 4: EXTRACTION (Sub-Agent)
    SubAgent->>WebReaderMCP: webReader(url1, retain_images=false)
    SubAgent->>WebReaderMCP: webReader(url2, retain_images=false)
    SubAgent->>WebReaderMCP: webReader(url3, retain_images=false)
    SubAgent->>WebReaderMCP: webReader(url4, retain_images=false)
    SubAgent->>WebReaderMCP: webReader(url5, retain_images=false)
    Note right of SubAgent: BATCH 1: 5 parallel calls

    WebReaderMCP-->>SubAgent: Articles 1-5 (markdown content)
    SubAgent->>Observer: Trigger: observe_and_enrich_corpus()
    Observer->>Observer: Save: extracted_articles/*.json
    Observer->>Observer: Enrich: Add content to<br/>search_results/*.json

    SubAgent->>SubAgent: Count successes<br/>(< 10? Continue)
    SubAgent->>WebReaderMCP: webReader(url6-10) [if needed]
    Note right of SubAgent: BATCH 2: Another 5 parallel<br/>HARD STOP at 10 successes<br/>or 2 batches

    Note over User,GmailService: PHASE 5: CORPUS SAVE (MANDATORY)
    SubAgent->>LocalToolkit: save_corpus(articles=[...], workspace_path)
    LocalToolkit->>LocalToolkit: Validate FULL content<br/>(NOT summaries)
    LocalToolkit->>LocalToolkit: Write: expanded_corpus.json
    LocalToolkit-->>SubAgent: {success: true, corpus_path, articles_saved}
    Note right of SubAgent: CHECKPOINT: Must verify<br/>before proceeding

    Note over User,GmailService: PHASE 6: SYNTHESIS
    SubAgent->>SubAgent: Analyze corpus<br/>Thematic synthesis
    SubAgent->>SubAgent: Generate HTML report<br/>(Exec Summary, ToC, Data Table, Sources)
    SubAgent->>LocalToolkit: write_local_file(path, content=HTML)
    LocalToolkit->>LocalToolkit: Write: work_products/report_topic_month_year.html
    LocalToolkit-->>SubAgent: "Successfully wrote N chars"
    SubAgent-->>MainAgent: Report complete

    Note over User,GmailService: PHASE 7: DELIVERY
    MainAgent->>LocalToolkit: workbench_upload(local_path, remote_path)
    LocalToolkit->>RemoteWorkbench: Upload file via WorkbenchBridge
    RemoteWorkbench-->>LocalToolkit: Upload success
    LocalToolkit-->>MainAgent: "Successfully uploaded to remote"
    MainAgent->>GmailService: GMAIL_SEND_EMAIL(to, subject, body, attachments=[remote_path])
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

## 3. Detailed Phase: Article Extraction & Corpus Enrichment

```mermaid
sequenceDiagram
    autonumber
    participant SubAgent as report-creation-expert<br/>(Sub-Agent)
    participant WebReaderMCP as webReader MCP<br/>(Z.AI)
    participant Observer as Observer<br/>(observe_and_enrich_corpus)
    participant ArticlesDir as extracted_articles/<br/>File System
    participant SearchResults as search_results/<br/>Existing SERP Files
    participant CorpusFile as expanded_corpus.json<br/>(Final Corpus)
    participant LocalToolkit as Local Toolkit MCP
    participant Logfire as Logfire

    Note over SubAgent,Logfire: BATCH 1: First 5 URLs (Parallel)
    SubAgent->>SubAgent: Parse search results<br/>Extract URLs 1-5

    par Parallel Extraction
        SubAgent->>WebReaderMCP: webReader(url1, retain_images=false)
    and
        SubAgent->>WebReaderMCP: webReader(url2, retain_images=false)
    and
        SubAgent->>WebReaderMCP: webReader(url3, retain_images=false)
    and
        SubAgent->>WebReaderMCP: webReader(url4, retain_images=false)
    and
        SubAgent->>WebReaderMCP: webReader(url5, retain_images=false)
    end

    Note right of SubAgent: Optimization: retain_images=false<br/>reduces payload size

    Note over SubAgent,Logfire: Response Processing (Per Article)
    WebReaderMCP-->>SubAgent: TextBlock (Success or Error)
    SubAgent->>Observer: Trigger: observe_and_enrich_corpus()<br/>for each result

    Observer->>Observer: Extract raw_json from TextBlock
    Observer->>Observer: Check for MCP error codes

    alt Error Code 1234 (Network Timeout)
        Observer->>Logfire: Warning: webreader_timeout_error<br/>{url, error_code}
        Observer->>Observer: Queue for retry<br/>(after all batches)
        Note right of Observer: Retryable error

    alt Error Code 1214 (Not Found)
        Observer->>Logfire: Warning: webreader_not_found<br/>{url, error_code}
        Observer->>Observer: _update_domain_blacklist()<br/>Track 3+ failures
        Observer->>Observer: Mark as failed, NO retry
        Note right of Observer: Permanent failure

    alt Success
        Observer->>Observer: Parse JSON: data.reader_result
        Observer->>Observer: Extract: {title, content,<br/>description, url}

        Note over SubAgent,Logfire: Save Individual Article
        Observer->>ArticlesDir: Create: extracted_articles/
        Observer->>ArticlesDir: Write: {safe_name}_{timestamp}.json
        Note right of ArticlesDir: {<br/>timestamp, source_url,<br/>title, description,<br/>content (markdown),<br/>extraction_success<br/>}

        Observer->>Logfire: Info: article_extracted<br/>{url, title, content_length}

        Note over SubAgent,Logfire: Enrich Existing Corpus
        Observer->>SearchResults: Find matching SERP file<br/>by URL
        Observer->>SearchResults: Add "content" field<br/>Add "extraction_timestamp"
        Observer->>SearchResults: Update: search_results/*.json
        Observer->>Logfire: Info: corpus_enriched<br/>{file, url}

    end

    Note over SubAgent,Logfire: BATCH 1 Complete
    SubAgent->>SubAgent: Count successes
    SubAgent->>SubAgent: if successes < 10: Continue to BATCH 2

    Note over SubAgent,Logfire: BATCH 2: URLs 6-10 (if needed)
    SubAgent->>WebReaderMCP: webReader(url6-10)
    Note right of SubAgent: Same parallel pattern<br/>HARD STOP after this batch

    Note over SubAgent,Logfire: Retry Failed 1234 Errors
    SubAgent->>SubAgent: Identify queued 1234 errors
    SubAgent->>WebReaderMCP: Retry 2 at a time<br/>(lower concurrency)
    Note right of SubAgent: Max 1 retry per URL

    Note over SubAgent,Logfire: CHECKPOINT: Save Corpus (MANDATORY)
    SubAgent->>SubAgent: Compile articles list<br/>[{url, title, content, status}]
    Note right of SubAgent: Must pass FULL content<br/>NOT summaries

    SubAgent->>LocalToolkit: save_corpus(<br/>articles=[...],<br/>workspace_path="SESSION_WORKSPACE"<br/>)

    LocalToolkit->>LocalToolkit: Count successes/failures
    LocalToolkit->>LocalToolkit: Build corpus JSON:<br/>{<br/>extraction_timestamp,<br/>total_articles,<br/>successful, failed,<br/>articles[...]<br/>}

    LocalToolkit->>CorpusFile: Write: expanded_corpus.json
    CorpusFile-->>LocalToolkit: File written

    LocalToolkit-->>SubAgent: {<br/>success: true,<br/>corpus_path, articles_saved,<br/>successful, failed,<br/>total_content_bytes<br/>}

    SubAgent->>SubAgent: Verify success == true
    Note right of SubAgent: DO NOT PROCEED without<br/>successful save_corpus
```

### Hard Stop Rules

| Rule | Condition | Action |
|------|-----------|--------|
| **10 successes** | `success_count >= 10` | STOP immediately, call save_corpus |
| **2 batches** | `batch_count >= 2` | STOP even if < 10 successes |
| **Error 1234** | Network timeout | Queue for retry (1 max) |
| **Error 1214** | 404 Not found | Skip, no retry |

### expanded_corpus.json Schema

**File**: `AGENT_RUN_WORKSPACES/session_XXX/expanded_corpus.json`

```json
{
  "extraction_timestamp": "2025-12-22T14:35:12.123456Z",
  "total_articles": 12,
  "successful": 10,
  "failed": 2,
  "articles": [
    {
      "url": "https://example.com/article1",
      "title": "Article Title Here",
      "content": "# Full Markdown Content\n\nThis is the complete article...",
      "status": "success"
    },
    {
      "url": "https://blocked.com/article2",
      "title": "",
      "content": "MCP error code 1214",
      "status": "failed"
    }
  ]
}
```

**Code References**:
- Sub-agent instructions: `.claude/agents/report-creation-expert.md:26-38` (Hard stop rules)
- Extraction workflow: `.claude/agents/report-creation-expert.md:48-76` (Batching strategy)
- Corpus save: `mcp_server.py:69-135` (`save_corpus` tool)
- Observer: `main.py:479-626` (`observe_and_enrich_corpus`)
- Blacklist: `main.py:167-205` (`_update_domain_blacklist`)

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

    Note over SubAgent,User: STEP 5: Upload to Remote Workbench
    MainAgent->>LocalToolkit: workbench_upload(<br/>local_path="SESSION_WORKSPACE/<br/>work_products/report.html",<br/>remote_path="/home/user/<br/>uploads/report.html"<br/>)

    LocalToolkit->>WorkbenchBridge: bridge.upload(<br/>local_path, remote_path)
    WorkbenchBridge->>WorkbenchBridge: Read local file
    WorkbenchBridge->>RemoteWorkbench: Upload to remote<br/>via Composio SDK
    RemoteWorkbench-->>WorkbenchBridge: Upload success
    WorkbenchBridge-->>LocalToolkit: {local_path, remote_path}
    LocalToolkit-->>MainAgent: "Successfully uploaded"

    Note over SubAgent,User: STEP 6: Email Delivery
    MainAgent->>GmailService: GMAIL_SEND_EMAIL(<br/>to="user@example.com",<br/>subject="Comprehensive Report: AI Developments",<br/>body="Please find attached...",<br/>attachments=["/home/user/uploads/report.html"]<br/>)

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

**File**: `AGENT_RUN_WORKSPACES/webReader_blacklist.json` (persistent)

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
| `ZAI_API_KEY` | webReader MCP authentication | `Bearer token` |
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
| webReader extraction | 5 parallel | Balance speed vs. reliability |
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

**Symptoms**: `expanded_corpus.json` shows `successful: 0`

**Possible Causes**:
1. All URLs returned error 1214 (404)
2. Network timeout (1234) on all URLs
3. webReader MCP unavailable

**Debug Steps**:
1. Check `extracted_articles/` for error patterns
2. Check `webReader_blacklist.json` for blacklisted domains
3. Review Logfire traces for `webreader_mcp_error` events

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

### Issue: Sub-agent didn't save corpus

**Symptoms**: Compliance error injected, `expanded_corpus.json` missing

**Possible Causes**:
1. Sub-agent bypassed save_corpus step
2. File write permission error
3. Workspace path incorrect

**Debug Steps**:
1. Check sub-agent instructions in `.claude/agents/report-creation-expert.md`
2. Verify `CURRENT_SESSION_WORKSPACE` was injected
3. Check `run.log` for save_corpus call

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
| `observe_and_enrich_corpus()` | main.py:479-626 | Article extraction observer |
| `verify_subagent_compliance()` | main.py:629-672 | Corpus checkpoint verification |
| `save_corpus()` | mcp_server.py:69-135 | MCP tool for corpus save |
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
    CheckType -->|Yes| Extract[Batch Extraction<br/>webReader x5]
    CheckType -->|No| Direct[Use Search Snippets]

    Extract --> Observer2[Observer: Save Articles<br/>Enrich Corpus]
    Observer2 --> Count{Count Successes}

    Count -->|< 10 & Batch < 2| Extract
    Count -->|>= 10 OR Batch >= 2| SaveCorpus[save_corpus<br/>MANDATORY]

    Direct --> Synthesize[Synthesize Report]
    SaveCorpus --> Synthesize

    Synthesize --> WriteReport[write_local_file<br/>work_products/report.html]
    WriteReport --> ReturnReport[Return to Main Agent]

    ReturnReport --> Verify{Corpus Exists?}
    Verify -->|No| Error[Inject Compliance Error]
    Verify -->|Yes| Upload[workbench_upload<br/>to Remote]

    Upload --> Email[GMAIL_SEND_EMAIL<br/>with attachment]
    Error --> Email
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
  "name": "mcp__web_reader__webReader",
  "input": {
    "url": "https://techcrunch.com/2025/12/11/openai-gpt52",
    "retain_images": false
  }
}
```

### Example 3: Corpus Save

```json
{
  "name": "mcp__local_toolkit__save_corpus",
  "input": {
    "articles": [
      {
        "url": "https://...",
        "title": "...",
        "content": "# Full markdown...",
        "status": "success"
      }
    ],
    "workspace_path": "/path/to/AGENT_RUN_WORKSPACES/session_XXX"
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
    "attachments": ["/home/user/uploads/report.html"]
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
