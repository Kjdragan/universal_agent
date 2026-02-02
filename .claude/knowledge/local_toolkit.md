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

## mcp__internal__run_research_pipeline
```json
{
  "query": "Research Topic context",
  "task_name": "task_identifier_string"
}
```
> [!IMPORTANT]
> This is the **STANDARD EFFICIENCY PATH**. Use this to execute the entire Crawl -> Refine -> Report flow in one turn. Avoid calling individual phases unless recovering from a specific failure.


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
> Use the returned `s3key` in `GMAIL_SEND_EMAIL.attachment`. Never call the Composio SDK in Bash/Python for uploads.

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
