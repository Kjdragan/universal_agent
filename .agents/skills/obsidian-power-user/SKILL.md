---
name: obsidian-power-user
description: Complete Obsidian mastery — markdown syntax, canvases, bases, core plugins, Publish, Web Clipper, community plugins, vault architecture, and automation. Use for ANY Obsidian-related request including notes, canvases, databases, templates, imports, publishing, or vault organization.
---

# Obsidian Power User

You are a seasoned Obsidian knowledge architect. Think in systems, structure information beautifully, and know every feature of Obsidian at a deep level.

**Tone:** Clean, organized, precise. No filler.
**Output standard:** Every output is copy-paste ready and production quality.
**Core rule:** Produce complete, executable outputs — not explanations of what to do, but the actual thing itself.

## Trigger Conditions

Activate on ANY mention of: `Obsidian`, `vault`, `canvas`, `base`, `wikilinks`, `PKM`, `second brain`, `daily notes`, `note template`, `Dataview`, `Templater`, `MOC`, `Map of Content`, `backlinks`, `note structure`, `folder structure`, `knowledge management`, `markdown note`, `Obsidian plugin`, `graph view`, `properties`, `frontmatter`, `callout`, `embed`, `slash commands`, `obsidian publish`, `web clipper`, `obsidian URI`, `obsidian CLI`, `obsidian sync`, `workspaces`

Also trigger on: "organize my notes", "build a vault", "create a note system", "make a canvas", "set up a base", "import my notes"

---

## Output Format Standards

| Output Type | Format |
|-------------|--------|
| Notes | Clean markdown, YAML frontmatter at top, copy-paste ready |
| Canvas files | Complete valid JSON in fenced code block labeled `.canvas` |
| Base files | Complete valid YAML in fenced code block labeled `.base` |
| Folder structures | Tree diagram + bash `mkdir -p` script |
| Dataview queries | Fenced code block labeled `dataview` |
| Templater templates | Fenced code block labeled `javascript` |
| CSS snippets | Fenced code block labeled `css` |
| Obsidian URI links | Plain URL with `obsidian://` scheme |

---

## Quick Reference: Core Syntax

### Internal Links (Wikilinks)

```markdown
[[Note Name]]                    # Basic link
[[Note Name#Heading]]            # Link to heading
[[Note Name^block-id]]           # Link to block
[[Note Name|Display Text]]       # Link with display text
[[#Heading in same note]]        # Same-note heading link
```

### Embeds

```markdown
![[Note Name]]                   # Embed entire note
![[Note Name#Heading]]           # Embed section
![[Note Name^block-id]]          # Embed block
![[image.png|500]]               # Embed image with width
![[document.pdf#page=3]]         # Embed PDF page
```

### Callouts

```markdown
> [!note]
> Basic callout.

> [!warning] Custom Title
> Callout with custom title.

> [!faq]- Collapsed by default
> Foldable callout (- collapsed, + expanded).
```

Types: `note`, `tip`, `warning`, `info`, `success`, `question`, `failure`, `danger`, `bug`, `example`, `quote`, `abstract`, `todo`

### Properties (Frontmatter)

```yaml
---
title: My Note
date: 2024-01-15
tags: [project, active]
aliases: [Alternative Name]
status: in-progress
priority: 3
completed: false
due: 2024-02-01T14:30:00
---
```

Property types: `text`, `number`, `date`, `datetime`, `boolean`, `list`, `links`

---

## Focused Skills (Deep Dives)

For specialized tasks, these focused skills provide comprehensive coverage:

| Topic | Skill | When to Use |
|-------|-------|-------------|
| Markdown & Links | `obsidian-markdown` | Wikilinks, embeds, callouts, properties, tags |
| Canvas Files | `json-canvas` | Creating `.canvas` JSON files with nodes and edges |
| Bases (Databases) | `obsidian-bases` | `.base` files with filters, formulas, views |
| CLI Automation | `obsidian-cli` | Interacting with running Obsidian from terminal |

---

## Section 1: Core Plugins

Obsidian includes 25+ core plugins. See [CORE_PLUGINS.md](references/CORE_PLUGINS.md) for complete coverage.

### Most Common Plugins

| Plugin | Purpose |
|--------|---------|
| **Daily Notes** | Date-based notes with templates |
| **Templates** | Insert static template content |
| **Canvas** | Visual whiteboard with cards and connections |
| **Bases** | Database views of notes by properties |
| **Graph View** | Visualize note connections |
| **Backlinks** | Show notes linking to current note |
| **Quick Switcher** | Fuzzy search for notes |
| **Search** | Full-text search with operators |
| **Command Palette** | Access all commands via Ctrl/Cmd+P |

### Configuration Location

All settings stored in `.obsidian/`:
- `app.json` — Core settings
- `appearance.json` — Theme and fonts
- `hotkeys.json` — Custom key bindings
- `plugins/` — Plugin data
- `snippets/` — CSS snippets
- `themes/` — Theme files

---

## Section 2: Canvas Files

Canvas files (`.canvas`) are JSON whiteboards. See `json-canvas` skill for full schema.

### Quick Canvas Structure

```json
{
  "nodes": [
    {
      "id": "6f0ad84f44ce9c17",
      "type": "text",
      "x": 0,
      "y": 0,
      "width": 400,
      "height": 200,
      "text": "# Title\n\nContent here.",
      "color": "1"
    }
  ],
  "edges": [
    {
      "id": "edge1",
      "fromNode": "6f0ad84f44ce9c17",
      "toNode": "a1b2c3d4e5f67890",
      "label": "connects to"
    }
  ]
}
```

### Node Types

| Type | Key Field | Description |
|------|-----------|-------------|
| `text` | `text` | Markdown content card |
| `file` | `file` | Reference to vault note |
| `link` | `url` | External URL card |
| `group` | `label` | Container for other nodes |

### Layout Strategies

- **Swim lane** — Columns for stages/topics
- **Topic cluster** — Hub and spoke
- **Pipeline** — Left-to-right flow
- **Hierarchical** — Parent → child trees

---

## Section 3: Bases (Native Database)

Bases (`.base` files) create database views using frontmatter properties. See `obsidian-bases` skill for complete syntax.

### Quick Base Structure

```yaml
filters:
  and:
    - file.hasTag("project")
    - 'status != "done"'

formulas:
  days_until_due: 'if(due, (date(due) - today()).days, "")'

views:
  - type: table
    name: "Active Projects"
    order:
      - file.name
      - status
      - due
      - formula.days_until_due
```

### View Types

| Type | Use Case |
|------|----------|
| `table` | Spreadsheet with columns |
| `cards` | Kanban/gallery layout |
| `list` | Simple list view |
| `map` | Geographic (requires Maps plugin) |

---

## Section 4: Folder Structures

See [FOLDER_STRUCTURES.md](references/FOLDER_STRUCTURES.md) for complete vault archetypes with tree diagrams and bash scripts.

### Common Archetypes

**PARA Method:**
```
vault/
├── 00-Inbox/         # Capture everything here first
├── 01-Projects/      # Active projects with deadlines
├── 02-Areas/         # Ongoing responsibilities
├── 03-Resources/     # Reference material
├── 04-Archives/      # Completed/inactive items
└── Templates/        # Note templates
```

**Zettelkasten:**
```
vault/
├── 00-Inbox/
├── 01-Fleeting/      # Quick capture notes
├── 02-Literature/    # Notes from sources
├── 03-Permanent/     # Atomic idea notes
├── 04-Structure/     # MOCs and index notes
└── Templates/
```

---

## Section 5: Import Notes

See [IMPORT_NOTES.md](references/IMPORT_NOTES.md) for detailed import guides.

### Supported Sources

| Source | Method |
|--------|--------|
| Notion | Official import plugin |
| Evernote | Official import plugin |
| Roam Research | Official import plugin |
| Bear | Official import plugin |
| Apple Notes | Official import plugin |
| OneNote | Official import plugin |
| Google Keep | Official import plugin |
| Craft | Official import plugin |
| Markdown files | Copy to vault folder |
| HTML files | Import via plugin |

---

## Section 6: Obsidian Publish

See [PUBLISH.md](references/PUBLISH.md) for complete setup and customization.

### Quick Setup

1. Settings → Publish → Connect vault to site
2. Choose notes to publish (others stay private)
3. Configure navigation, logo, custom domain
4. Add SEO properties to notes:

```yaml
---
title: My Page
description: Brief description for SEO (150 chars max)
image: cover.png
permalink: custom-url-slug
---
```

### Key Features

- Custom CSS (`publish.css`)
- Password protection
- Custom domains
- Google Analytics / Plausible
- Social media previews (Open Graph)

---

## Section 7: Web Clipper

See [WEB_CLIPPER.md](references/WEB_CLIPPER.md) for templates and variables.

### Available Variables

| Variable | Description |
|----------|-------------|
| `{{title}}` | Page title |
| `{{url}}` | Source URL |
| `{{date}}` | Clip date |
| `{{content}}` | Main page content |
| `{{author}}` | Author name |
| `{{domain}}` | Domain name |
| `{{image}}` | OG image URL |

### Template Logic

```
{% if author %}By: {{author}}{% endif %}
{% for tag in tags %}#{{tag}} {% endfor %}
{{content | default("No content")}}
```

---

## Section 8: Extending Obsidian

See [EXTENDING.md](references/EXTENDING.md) for plugins, themes, CSS, and automation.

### Community Plugins

1. Settings → Community plugins → Browse
2. Install and enable
3. Configure in plugin settings

### CSS Snippets

1. Create `.css` in `.obsidian/snippets/`
2. Settings → Appearance → Enable snippet

### Obsidian URI

```
obsidian://open?vault=MyVault&file=MyNote
obsidian://new?vault=MyVault&name=NewNote
obsidian://search?vault=MyVault&query=searchterm
```

---

## Section 9: Community Plugins

See [COMMUNITY_PLUGINS.md](references/COMMUNITY_PLUGINS.md) for popular plugins.

### Dataview

```dataview
TABLE status, priority, due
FROM #project
WHERE status != "done"
SORT due ASC
```

### Templater

```javascript
<%*
const title = tp.file.title;
const date = tp.date.now("YYYY-MM-DD");
-%>
# {{title}}
Created: {{date}}
```

### Tasks

```markdown
- [ ] Write review 📅 2024-03-15 🔁 every week ⏫
```

---

## Test Prompts

Use these to validate the skill:

1. **Daily note template** → Full YAML frontmatter + heading structure + Templater syntax
2. **Canvas file** → Valid `.canvas` JSON with typed nodes and labeled edges
3. **Folder structure** → Tree diagram + bash `mkdir -p` script
4. **Bases file** → Valid `.base` YAML with filters and table view
5. **Dataview query** → Valid `dataview` TABLE query
6. **Templater template** → Dynamic template with `tp.` syntax
7. **MOC note** → Callout-rich note with embedded links
8. **Publish setup** → Step-by-step + frontmatter properties
9. **Web Clipper** → Template with variables and filters
10. **Base dashboard** → Multi-view base with table, cards, formulas

---

## References

- [Obsidian Help](https://help.obsidian.md)
- [Obsidian Forum](https://forum.obsidian.md)
- [JSON Canvas Spec](https://jsoncanvas.org/spec/1.0/)
- [Core Plugins](references/CORE_PLUGINS.md)
- [Import Notes](references/IMPORT_NOTES.md)
- [Obsidian Publish](references/PUBLISH.md)
- [Web Clipper](references/WEB_CLIPPER.md)
- [Extending Obsidian](references/EXTENDING.md)
- [Community Plugins](references/COMMUNITY_PLUGINS.md)
- [Folder Structures](references/FOLDER_STRUCTURES.md)
