# Obsidian Publish Reference

Obsidian Publish lets you host notes as a public website.

---

## Setup

### Connect Vault

1. Settings → Publish → Connect to Publish
2. Sign in with Obsidian account
3. Name your site

### Choose What to Publish

- Each note can be individually published or kept private
- Use the "Publish changes" pane to manage publication status
- Published notes show a globe icon

---

## Site Configuration

### Navigation

Configure in Site options:

```yaml
# Set navigation order in frontmatter
nav_order: 1
```

Or use drag-and-drop in the Publish pane.

### Logo and Favicon

1. Settings → Publish → Site options
2. Upload logo image
3. Upload favicon (`.ico` or `.png`)

### Custom CSS

Create `publish.css` in vault root:

```css
/* Custom publish styles */
.theme-dark {
  --background-primary: #1a1a2e;
  --text-normal: #e0e0e0;
}

/* Custom callout colors */
.callout[data-callout="custom"] {
  --callout-color: 200, 100, 50;
}
```

---

## SEO & Social

### Meta Properties

```yaml
---
title: My Page Title
description: Brief description for SEO (150 chars max)
image: assets/og-image.png
permalink: custom-url-slug
---
```

### Open Graph

| Property | Purpose |
|----------|---------|
| `title` | Social card title |
| `description` | Social card description |
| `image` | Social card image |

---

## Custom Domains

1. Settings → Publish → Site options → Custom domain
2. Enter your domain
3. Configure DNS:

```
CNAME your-subdomain.obsidian.pub
```

Or for root domain:
```
A record pointing to Obsidian's servers
```

4. Wait for SSL certificate provisioning

---

## Access Control

### Password Protection

1. Settings → Publish → Site options
2. Enable "Password protect site"
3. Set password

### Per-Note Privacy

Notes not marked for publish remain private automatically.

### Collaborators

1. Settings → Publish → Site options
2. Add collaborator email
3. Collaborators can publish/unpublish notes

---

## Analytics

### Google Analytics

1. Settings → Publish → Site options
2. Enter Google Analytics tracking ID

### Plausible Analytics

1. Settings → Publish → Site options
2. Enter Plausible domain

---

## Permalinks

Control URL structure with frontmatter:

```yaml
---
permalink: blog/my-post-title
---
```

Result: `https://yoursite.com/blog/my-post-title`

---

## File Limits

| Type | Limit |
|------|-------|
| Images | 50 MB per file |
| Video | 50 MB per file |
| Audio | 50 MB per file |
| PDF | 50 MB per file |
| Total site | 4 GB (free), 10 GB (paid) |

---

## Supported Features

### Supported
- Standard Markdown
- Wikilinks (converted to links)
- Callouts
- Images, audio, video, PDF
- Graph view (optional)
- Backlinks

### Not Supported
- Most community plugins
- Custom JavaScript
- Canvas files (limited)
- Bases (limited)

---

## Publishing Workflow

### Initial Publish

1. Select notes in "Publish changes" pane
2. Click "Publish"
3. Review live site

### Updates

1. Make changes to published notes
2. Open "Publish changes" pane
3. Select notes with changes
4. Click "Publish changes"

### Unpublish

1. Select published notes
2. Click "Unpublish"
3. Note remains in vault, removed from site

---

## Best Practices

### Content Structure

```yaml
---
title: Clear, Descriptive Title
description: SEO-friendly summary under 150 characters
image: path/to/social/image.png
nav_order: 10
---
```

### Navigation Hierarchy

- Use `nav_order` to control menu position
- Lower numbers appear first
- Group related pages with similar prefixes

### Performance

- Optimize images before upload
- Use appropriate image formats (WebP preferred)
- Keep total media under site limit
