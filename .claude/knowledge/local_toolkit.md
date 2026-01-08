# Local Toolkit Tool Schemas

## mcp__local_toolkit__list_directory
```json
{
  "path": "/absolute/or/relative/path"
}
```

## mcp__local_toolkit__read_research_files
```json
{
  "file_paths": [
    "/path/to/tasks/ai_news/filtered_corpus/article_001.md",
    "/path/to/tasks/ai_news/filtered_corpus/article_002.md"
  ]
}
```

## mcp__local_toolkit__finalize_research
```json
{
  "session_dir": "/home/.../AGENT_RUN_WORKSPACES/session_YYYYMMDD_HHMMSS",
  "task_name": "ai_news_summary"
}
```

## mcp__local_toolkit__append_to_file
```json
{
  "path": "/home/.../work_products/report.html",
  "content": "<html>...next chunk...</html>"
}
```

## mcp__local_toolkit__upload_to_composio
```json
{
  "path": "/home/.../work_products/report.pdf",
  "tool_slug": "GMAIL_SEND_EMAIL",
  "toolkit_slug": "gmail"
}
```

## mcp__local_toolkit__ask_user_questions
```json
{
  "questions": [
    {
      "question": "What timeframe should I cover?",
      "header": "Timeframe",
      "options": [
        {"label": "Last 7 days", "description": "Recent news"},
        {"label": "Last 30 days", "description": "Broader context"}
      ],
      "multiSelect": false
    }
  ]
}
```
