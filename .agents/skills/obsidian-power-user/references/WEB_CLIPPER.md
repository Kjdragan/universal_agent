# Obsidian Web Clipper Reference

Browser extension for capturing web content to Obsidian.

---

## Installation

1. Install extension from browser store (Chrome/Firefox/Edge)
2. Connect to Obsidian vault via extension settings
3. Configure default template and folder

---

## Clipping Modes

### Full Page
Clip entire page content as Markdown.

### Selection
Clip only highlighted text.

### Article
Clip main article content (strips navigation, ads).

### Screenshot
Capture visual screenshot to vault.

---

## Variables

### Basic Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `{{title}}` | Page title | "My Article" |
| `{{url}}` | Source URL | `https://example.com/article` |
| `{{date}}` | Clip date | `2024-01-15` |
| `{{time}}` | Clip time | `14:30` |
| `{{author}}` | Author name | "Jane Doe" |
| `{{description}}` | Meta description | "Article summary..." |
| `{{siteName}}` | Website name | "Example Blog" |
| `{{image}}` | OG image URL | `https://example.com/img.jpg` |
| `{{published}}` | Publication date | `2024-01-10` |
| `{{modified}}` | Last modified | `2024-01-12` |
| `{{lang}}` | Page language | "en" |
| `{{domain}}` | Domain name | "example.com" |

### Content Variables

| Variable | Description |
|----------|-------------|
| `{{content}}` | Main page content (Markdown) |
| `{{selection}}` | Selected text only |
| `{{html}}` | Raw HTML content |
| `{{text}}` | Plain text only |

### Derived Variables

| Variable | Description |
|----------|-------------|
| `{{date:YYYY-MM-DD}}` | Formatted date |
| `{{tags}}` | Auto-detected tags |
| `{{folder}}` | Suggested folder |

---

## Filters

Transform variable values using pipe syntax:

```
{{variable | filter}}
```

### Available Filters

| Filter | Example | Result |
|--------|---------|--------|
| `replace` | `{{title \| replace("-", " ")}}` | "My Title" |
| `trim` | `{{title \| trim}}` | "My Title" |
| `lower` | `{{title \| lower}}` | "my title" |
| `upper` | `{{title \| upper}}` | "MY TITLE" |
| `title` | `{{title \| title}}` | "My Title" |
| `slice` | `{{content \| slice(0, 200)}}` | First 200 chars |
| `date` | `{{date \| date("YYYY")}}` | "2024" |
| `default` | `{{author \| default("Unknown")}}` | "Unknown" |
| `escape` | `{{content \| escape}}` | HTML-safe string |
| `markdown` | `{{content \| markdown}}` | Convert to Markdown |

### Chained Filters

```
{{title | lower | replace(" ", "-") | trim}}
```

---

## Template Logic

### Conditionals

```
{% if author %}
By: {{author}}
{% endif %}

{% if image %}
![]({{image}})
{% else %}
![Default image](default.png)
{% endif %}
```

### Loops

```
{% for tag in tags %}
#{{tag}}
{% endfor %}

{% for item in list %}
- {{item}}
{% endfor %}
```

### Variables

```
{% set myvar = "value" %}
{{myvar}}
```

---

## Templates

### Article Template

```markdown
---
title: "{{title}}"
source: "{{url}}"
author: "{{author | default('Unknown')}}"
date: {{date}}
tags:
{% for tag in tags %}
  - {{tag}}
{% endfor %}
---

# {{title}}

{% if author %}By: {{author}}{% endif %}
{% if published %}Published: {{published | date("YYYY-MM-DD")}}{% endif %}

> Source: [{{siteName}}]({{url}})

{{content}}
```

### Quick Note Template

```markdown
# {{title}}

> Captured from: [{{domain}}]({{url}})

{{content | slice(0, 1000)}}{% if content.length > 1000 %}...{% endif %}

---
- Source: {{url}}
- Captured: {{date}} {{time}}
```

### Research Template

```markdown
---
title: "{{title}}"
type: research
url: "{{url}}"
domain: "{{domain}}"
date: {{date}}
tags: [research, web-clip]
---

# {{title}}

## Metadata
- **Author:** {{author | default("Unknown")}}
- **Published:** {{published | default("N/A")}}
- **Domain:** {{domain}}

## Summary

{{description}}

## Content

{{content}}

---
Clipped: {{date}} {{time}}
```

---

## Highlighting

### Enable Highlights

1. Click Web Clipper extension icon
2. Enable "Highlight mode"
3. Select text on page
4. Highlights preserved in clip

### Highlight Colors

- Yellow (default)
- Green
- Blue
- Pink

---

## AI Interpretation

### AI-Powered Extraction

Use AI to extract structured content:

1. Enable "Interpret with AI" in extension settings
2. Configure extraction schema
3. Clip with AI interpretation

### Custom Schemas

Define what to extract:

```json
{
  "title": "string",
  "author": "string",
  "mainPoints": "list",
  "summary": "string",
  "sentiment": "string"
}
```

---

## Folder Organization

### Dynamic Folders

```
{{domain}}/{{date:YYYY}}/{{date:MM}}
```

### Example Output

```
example.com/2024/01/article-title.md
```

### Folder Variables

| Pattern | Result |
|---------|--------|
| `{{domain}}` | `example.com` |
| `{{date:YYYY}}` | `2024` |
| `{{date:YYYY-MM}}` | `2024-01` |
| `{{tags.0}}` | First tag |

---

## Best Practices

1. **Use templates** for consistent formatting
2. **Add metadata** via frontmatter
3. **Include source URL** for attribution
4. **Use filters** to clean up captured content
5. **Organize folders** by domain or date
6. **Trim content** for large pages
