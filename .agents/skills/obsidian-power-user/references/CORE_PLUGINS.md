# Core Plugins Reference

All core plugins are configured in Settings → Core plugins. Enable/disable individually.

---

## Audio Recorder

Record audio notes directly into the vault.

**Configuration:**
- Save location folder
- File naming format

**Usage:**
- Click microphone icon in ribbon or use command palette
- Recording saves as `.webm` or `.mp3` in configured folder

---

## Backlinks

Show notes that link to the current note.

**Features:**
- Backlinks pane (right sidebar)
- Unlinked mentions (detects text matching note names)
- Toggle backlinks section in document footer

**Configuration:**
- Show backlinks in document
- Show unlinked mentions

---

## Bookmarks

Save notes, headings, searches, graph views, and URLs.

**Features:**
- Organize into groups
- Pin frequently used items
- Quick access via Bookmarks pane

**Usage:**
- Right-click tab → Bookmark
- Command: "Bookmark current note"
- Access via Bookmarks icon in ribbon

---

## Canvas

Visual whiteboard for notes, images, and connections. See `json-canvas` skill for complete documentation.

**Features:**
- Text cards with Markdown
- File nodes (embed notes)
- Link nodes (external URLs)
- Group containers
- Labeled edges between nodes

---

## Command Palette

Quick access to all commands via keyboard.

**Shortcut:** `Ctrl/Cmd + P`

**Features:**
- Fuzzy search commands
- Pin frequently used commands
- Shows keyboard shortcuts

---

## Daily Notes

Automated daily note creation.

**Configuration:**
- Date format: `YYYY-MM-DD` (default)
- Template file path
- Default folder (e.g., `Daily Notes/`)
- Open on startup (toggle)

**Template tokens:**
```markdown
{{title}}     # Note title
{{date}}      # Current date
{{time}}      # Current time
```

---

## File Explorer

Browse vault files and folders.

**Features:**
- Create notes and folders
- Drag-and-drop organization
- Right-click context menu
- Reveal in file manager
- Sort options (name, modified, created)

---

## File Recovery

Backup and restore vault snapshots.

**Configuration:**
- Snapshot interval (minutes)
- Retention period (days)

**Usage:**
- Settings → File Recovery → View snapshots
- Restore deleted or corrupted notes

---

## Format Converter

Convert legacy Markdown formats.

**Conversions:**
- Wikilinks → Standard Markdown links
- Legacy highlight syntax
- Other formatting migrations

---

## Graph View

Visualize note connections.

**Views:**
- Global graph (entire vault)
- Local graph (current note's connections)

**Display settings:**
- Arrow direction
- Show orphans (unconnected notes)
- Show attachments
- Show tags
- Node size (incoming/outgoing links)

**Groups:**
- Color-code by folder, tag, or property
- Create visual clusters

**Physics:**
- Repel force
- Link force
- Center force

---

## Note Composer

Restructure notes.

**Operations:**
- **Merge:** Combine two notes into one
- **Extract:** Move selected text to new note
- **Split:** Create new note from selection

**Configuration:**
- Template for extracted notes
- Update links automatically

---

## Outgoing Links

Show links from current note.

**Features:**
- Outgoing links pane
- Unlinked mentions (potential links)
- Convert unlinked to linked with click

---

## Outline

Heading-based navigation panel.

**Features:**
- Shows all headings in current note
- Click to jump to section
- Collapse/expand hierarchy

---

## Page Preview

Hover preview of linked notes.

**Usage:**
- Hover over wikilink with `Ctrl/Cmd` held
- Shows preview popup

**Configuration:**
- Enable/disable hover preview
- Preview delay

---

## Properties View

Sidebar panel for frontmatter management.

**Features:**
- Browse all properties across vault
- Edit property types
- Filter by property value

---

## Quick Switcher

Fuzzy search for notes.

**Shortcut:** `Ctrl/Cmd + O`

**Features:**
- Open existing notes
- Create new notes from search
- Search by path, alias, or content

**Modifiers:**
- `#tag` — Filter by tag
- `folder:` — Filter by folder
- `path:` — Search by path

---

## Random Note

Serendipitous discovery.

**Usage:**
- Command: "Open random note"
- Opens a random note from vault

**Use cases:**
- Review old notes
- Discover forgotten connections
- Creative inspiration

---

## Search

Full-text search with operators.

**Basic Search:**
```
search term
```

**Search Operators:**

| Operator | Example | Description |
|----------|---------|-------------|
| `path:` | `path:Projects/` | Search in folder |
| `tag:` | `tag:project` | Search by tag |
| `file:` | `file:meeting` | Search filenames |
| `line:` | `line:TODO` | Match on same line |
| `block:` | `block:quote` | Match in block |
| `section:` | `section:## Tasks` | Match in section |
| `content:` | `content:exact phrase` | Search content only |

**Advanced:**
- Regex: `/pattern/`
- Case-sensitive: `match-case:term`
- Exclusion: `-tag:archived`

**Embed search results:**
````markdown
```query
tag:#project status:active
```
````

---

## Slash Commands

Type `/` in editor to access commands.

**Configuration:**
- Enable/disable in editor settings

**Usage:**
- Type `/` while editing
- Select command from dropdown

---

## Slides

Present notes as slideshows.

**Syntax:**
```markdown
# Slide 1

Content for first slide

---

# Slide 2

Content for second slide
```

**Usage:**
- Use `---` as slide separator
- Start presentation from command palette

**Navigation:**
- Arrow keys or space to advance
- Escape to exit

---

## Tags View

Browse all tags in vault.

**Features:**
- Nested tag hierarchy
- Click to filter notes
- Sort by count or name

---

## Templates

Insert static template content.

**Configuration:**
- Template folder location

**Tokens:**
```markdown
{{title}}      # Note title
{{date}}       # Current date (YYYY-MM-DD)
{{time}}       # Current time (HH:mm)
```

**Usage:**
- Command: "Insert template"
- Select from template folder

---

## Unique Note Creator

Zettelkasten-style timestamped notes.

**Configuration:**
- Prefix format (e.g., timestamp)
- Default folder
- Template file

**Naming convention:**
```
202401151430-unique-id-note-title.md
```

---

## Web Viewer

Open web pages inside Obsidian.

**Features:**
- Browse web in split pane
- Save pages as notes
- Clip content to vault

---

## Word Count

Status bar word count display.

**Features:**
- Current note word count
- Total vault statistics

---

## Workspaces

Save and restore pane layouts.

**Usage:**
- Arrange panes as desired
- Save as named workspace
- Switch between layouts

**Features:**
- Multiple workspace presets
- Quick switching via command palette
- Auto-save current layout
