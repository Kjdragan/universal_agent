# Composio Search Tools - Test Prompts

These prompts can be copy-pasted directly into the Universal Agent to test COMPOSIO_SEARCH_TOOLS for task decomposition.

---

## TEST 1: Mixed Composio + Non-Composio Tasks (Most Important)

```
Call COMPOSIO_SEARCH_TOOLS with the following queries to test task decomposition:

{
  "queries": [
    {
      "use_case": "Search web for top 5 quantum computing companies and their recent funding rounds",
      "known_fields": "timeframe: 2024-2025, depth: comprehensive"
    },
    {
      "use_case": "Analyze search results and create a ranked comparison table of companies",
      "known_fields": "criteria: funding raised, technology focus; output: markdown table"
    },
    {
      "use_case": "Write executive summary paragraph synthesizing the competitive landscape",
      "known_fields": "length: 200 words, tone: professional"
    },
    {
      "use_case": "Convert markdown report to PDF document",
      "known_fields": ""
    },
    {
      "use_case": "Send final report via email to stakeholders",
      "known_fields": "recipients: team@company.com, attachment: pdf"
    }
  ],
  "session": {"generate_id": true}
}

Evaluate: Does COMPOSIO_SEARCH_TOOLS return a structured plan with tool recommendations for Composio tasks and "no tool needed" markers for non-Composio tasks?
```

---

## TEST 2: EXTREME - Vague Requests (No Details)

```
Call COMPOSIO_SEARCH_TOOLS with these vague, crazy requests:

{
  "queries": [
    {"use_case": "Go viral on social media", "known_fields": ""},
    {"use_case": "Make me rich", "known_fields": ""},
    {"use_case": "Write the next great American novel", "known_fields": ""},
    {"use_case": "Build a startup", "known_fields": ""}
  ],
  "session": {"generate_id": true}
}

Evaluate: Does COMPOSIO_SEARCH_TOOLS decompose these? Or does it require we supply more detail?
```

---

## TEST 3: Pure Non-Composio Creative Tasks

```
Call COMPOSIO_SEARCH_TOOLS with pure creative/analytical tasks:

{
  "queries": [
    {
      "use_case": "Write a professional haiku poem about artificial intelligence and consciousness",
      "known_fields": "style: traditional 5-7-5 syllable pattern, tone: philosophical"
    },
    {
      "use_case": "Analyze market positioning using SWOT framework",
      "known_fields": "format: executive summary, length: 500 words"
    },
    {
      "use_case": "Draft an investor pitch one-pager",
      "known_fields": "include: problem, solution, market, traction, team, ask"
    }
  ],
  "session": {"generate_id": true}
}

Evaluate: How does it handle tasks with NO Composio tools?
```

---

## TEST 4: Single Complex Mega-Request (Decomposition Test)

```
Call COMPOSIO_SEARCH_TOOLS with a single complex request:

{
  "queries": [
    {
      "use_case": "Research quantum computing companies, analyze their market positioning, write a comprehensive report with executive summary and detailed findings, include visual aids like comparison tables, convert to PDF format, upload to shared Google Drive, and send notification email to the research team with a preview of key findings.",
      "known_fields": "deadline: end of day, priority: high"
    }
  ],
  "session": {"generate_id": true}
}

Evaluate: Does it decompose this into multiple steps? Or return as single item?
```

---

## TEST 5: 10+ Query Stress Test

```
Call COMPOSIO_SEARCH_TOOLS with 10 sequential queries:

{
  "queries": [
    {"use_case": "Search for breaking news on AI regulation in the US and EU", "known_fields": "timeframe: last 30 days"},
    {"use_case": "Search Google Scholar for policy papers on AI governance", "known_fields": "year: 2023-2025"},
    {"use_case": "Compare and contrast US vs EU approaches to AI regulation", "known_fields": "format: structured analysis"},
    {"use_case": "Identify top 10 implications for AI startups", "known_fields": "categorize: legal, technical, operational"},
    {"use_case": "Write a detailed white paper on AI compliance strategies", "known_fields": "length: 3000 words"},
    {"use_case": "Create an infographic outline showing regulatory timeline", "known_fields": "format: timeline"},
    {"use_case": "Format white paper with professional styling", "known_fields": "style: corporate"},
    {"use_case": "Generate PDF version of the white paper", "known_fields": "format: A4"},
    {"use_case": "Upload white paper to Google Drive shared folder", "known_fields": "folder: Q1 Research"},
    {"use_case": "Send announcement email to mailing list with Drive link", "known_fields": "list: stakeholders"}
  ],
  "session": {"generate_id": true}
}

Evaluate: Rate limits? Response quality at scale? Processing time?
```

---

## Evaluation Criteria

For each test, note:

1. **Tool Matching** - Did it recommend correct Composio tools for matching tasks?
2. **No-Tool Handling** - How did it handle tasks without Composio equivalents?
3. **Decomposition** - Did single complex requests get broken down?
4. **Ordering** - Was sequential order preserved?
5. **Actionability** - Can we use this as a task queue for the harness?
