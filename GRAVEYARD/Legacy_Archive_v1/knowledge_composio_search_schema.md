# Composio Search Result Schemas

This document defines the output schemas for various Composio search tools and how they are normalized by the Universal Agent's observer system.

## 1. COMPOSIO_SEARCH_WEB (Standard)
**Raw Output Key**: `organic_results` (list)
**Normalized Type**: `web`

### Normalized Schema
```json
{
  "type": "web",
  "timestamp": "ISO_DATE",
  "tool": "COMPOSIO_SEARCH_WEB",
  "results": [
    {
      "position": 1,
      "title": "Page Title",
      "url": "https://example.com",
      "snippet": "Description of the page content..."
    }
  ]
}
```

## 2. COMPOSIO_SEARCH_NEWS
**Raw Output Key**: `news_results` (list)
**Normalized Type**: `news`

### Normalized Schema
```json
{
  "type": "news",
  "timestamp": "ISO_DATE",
  "tool": "COMPOSIO_SEARCH_NEWS",
  "articles": [
    {
      "position": 1,
      "title": "News Headline",
      "url": "https://news.example.com",
      "source": "Publisher Name",
      "date": "2 hours ago",
      "snippet": "News summary..."
    }
  ]
}
```

## 3. COMPOSIO_SEARCH_SCHOLAR
**Raw Output Key**: `articles` (list) or `organic_results` (varies)
**Normalized Type**: `scholar` -> `web` (structure)

**Problem**: Scholar output often uses `articles` list and `link` field, preventing generic crawlers from finding `results` and `url`.

### Raw Schema (Example)
```json
{
  "articles": [
    {
      "title": "Paper Title",
      "link": "https://arxiv.org/...",
      "snippet": "Abstract..."
    }
  ]
}
```

### Normalized Schema (Target)
The agent normalizes this to match the `web` structure so tools like `crawl_parallel` can process it easily.

```json
{
  "type": "scholar",
  "timestamp": "ISO_DATE",
  "tool": "COMPOSIO_SEARCH_SCHOLAR",
  "results": [  // Renamed from 'articles'
    {
      "position": 1,
      "title": "Paper Title",
      "url": "https://arxiv.org/...", // Renamed from 'link'
      "snippet": "Abstract...",
      "source": "arxiv.org"
    }
  ]
}
```
