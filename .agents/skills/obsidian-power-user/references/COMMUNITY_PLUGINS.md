# Community Plugins Reference

Popular third-party plugins for Obsidian.

> **Note:** These are community plugins, not core plugins. Install via Settings → Community plugins → Browse.

---

## Dataview

Query and display notes like a database.

### Query Types

| Type | Description |
|------|-------------|
| `TABLE` | Spreadsheet-style table |
| `LIST` | Simple list |
| `TASK` | Task query |
| `CALENDAR` | Calendar view by date |

### Basic Syntax

```dataview
TABLE column1, column2
FROM #tag
WHERE condition
SORT column ASC
```

### Clauses

| Clause | Description | Example |
|--------|-------------|---------|
| `FROM` | Source filter | `FROM #project` |
| `WHERE` | Row filter | `WHERE status = "done"` |
| `SORT` | Ordering | `SORT due ASC` |
| `GROUP BY` | Grouping | `GROUP BY status` |
| `LIMIT` | Max results | `LIMIT 10` |
| `FLATTEN` | Array expansion | `FLATTEN tags AS tag` |

### Implicit Fields

| Field | Description |
|-------|-------------|
| `file.name` | Filename |
| `file.folder` | Parent folder |
| `file.path` | Full path |
| `file.ext` | Extension |
| `file.link` | Link to file |
| `file.size` | File size |
| `file.ctime` | Created time |
| `file.mtime` | Modified time |
| `file.tags` | All tags |
| `file.inlinks` | Incoming links |
| `file.outlinks` | Outgoing links |

### Examples

**Project table:**
```dataview
TABLE status, priority, due
FROM #project
WHERE status != "done"
SORT due ASC
```

**Recent notes:**
```dataview
LIST
FROM "Daily Notes"
SORT file.ctime DESC
LIMIT 7
```

**Tasks query:**
```dataview
TASK
FROM #project
WHERE !completed
SORT due ASC
```

**Grouped by status:**
```dataview
TABLE rows.file.link AS Notes
FROM #project
GROUP BY status
```

### Inline Queries

```
`= this.due`
`= date(today) - this.created`
`= filter(file.tags, (t) => t != "#project")`
```

### DataviewJS

JavaScript queries for advanced use:

```dataviewjs
dv.table(
  ["Note", "Status"],
  dv.pages("#project")
    .map(p => [p.file.link, p.status])
)
```

---

## Templater

Dynamic templates with JavaScript.

### Basic Syntax

```javascript
<%*
// JavaScript code here
-%>
```

### Template Variables

| Variable | Description |
|----------|-------------|
| `tp.file.title` | Note title |
| `tp.file.name` | Filename |
| `tp.file.folder` | Parent folder |
| `tp.file.path` | Full path |
| `tp.file.creation_date` | Created date |
| `tp.file.last_modified_date` | Modified date |
| `tp.file.tags` | All tags |

### Date Functions

```javascript
<% tp.date.now("YYYY-MM-DD") %>           // Today
<% tp.date.now("YYYY-MM-DD", 7) %>        // Week from now
<% tp.date.now("YYYY-MM-DD", -7) %>       // Week ago
<% tp.date.tomorrow("YYYY-MM-DD") %>      // Tomorrow
<% tp.date.yesterday("YYYY-MM-DD") %>     // Yesterday
```

### User Input

```javascript
<% tp.system.prompt("Enter value") %>
<% tp.system.suggester(["Option 1", "Option 2"], ["val1", "val2"]) %>
```

### File Operations

```javascript
<% tp.file.create_new("Title", "Content") %>
<% tp.file.rename("New Name") %>
<% tp.file.move("Folder/Subfolder") %>
```

### Hooks

```javascript
<%*
// Run on template creation
tp.hooks.on_all_templates_executed(async () => {
  // Code runs after all templates processed
});
-%>
```

### Example Templates

**Meeting note:**
```javascript
---
title: "<% tp.file.title %>"
date: <% tp.date.now("YYYY-MM-DD") %>
time: <% tp.date.now("HH:mm") %>
attendees: []
---

# <% tp.file.title %>

## Attendees
<%* attendees = tp.system.prompt("Attendees (comma-separated)").split(",") %>
<%* for (let a of attendees) { %>
- <% a.trim() %>
<%* } %>

## Agenda

## Notes

## Action Items
```

**Daily note:**
```javascript
---
title: <% tp.date.now("YYYY-MM-DD") %>
date: <% tp.date.now("YYYY-MM-DD") %>
---

# <% tp.date.now("dddd, MMMM D, YYYY") %>

## Tasks
- [ ]

## Notes

## Journal

---
Yesterday: [[<% tp.date.yesterday("YYYY-MM-DD") %>]]
Tomorrow: [[<% tp.date.tomorrow("YYYY-MM-DD") %>]]
```

**Project note:**
```javascript
---
title: "<% tp.file.title %>"
status: active
created: <% tp.date.now("YYYY-MM-DD") %>
priority: <% tp.system.suggester(["High", "Medium", "Low"], [1, 2, 3]) %>
tags: [project]
---

# <% tp.file.title %>

## Overview
<% tp.system.prompt("Project description") %>

## Goals
-

## Tasks
- [ ]

## Resources
-

## Notes

## Related
```

---

## Tasks Plugin

Advanced task management.

### Task Syntax

```markdown
- [ ] Task description 📅 2024-03-15 ⏫ 🔁 every week
```

### Task Metadata

| Emoji | Field | Description |
|-------|-------|-------------|
| 📅 | Due date | `📅 YYYY-MM-DD` |
| ⏳ | Scheduled | `⏳ YYYY-MM-DD` |
| 🛫 | Start date | `🛫 YYYY-MM-DD` |
| 🔁 | Recurrence | `🔁 every day` |
| ⏫ | Priority | High |
| 🔼 | Priority | Medium |
| 🔽 | Priority | Low |
| ✅ | Completed | `✅ YYYY-MM-DD` |

### Recurrence Patterns

```
🔁 every day
🔁 every week
🔁 every 2 weeks
🔁 every month
🔁 every weekday
🔁 every 3 days when done
```

### Tasks Query Block

```tasks
not done
due before tomorrow
sort by due
limit 10
```

### Query Filters

| Filter | Description |
|--------|-------------|
| `not done` | Incomplete tasks |
| `done` | Completed tasks |
| `due before YYYY-MM-DD` | Due before date |
| `due after YYYY-MM-DD` | Due after date |
| `due today` | Due today |
| `scheduled before` | Scheduled before |
| `has due date` | Has due date |
| `no due date` | No due date |
| `priority is high` | High priority |
| `path includes Folder` | In folder |
| `tag includes #tag` | Has tag |
| `description includes text` | Contains text |

### Sorting

```
sort by due
sort by priority
sort by scheduled
sort by status
sort by description
```

### Grouping

```
group by due
group by priority
group by folder
group by status
```

### Examples

**Overdue tasks:**
```tasks
not done
due before today
sort by due
```

**This week's tasks:**
```tasks
not done
due after today
due before in one week
sort by due
```

**High priority tasks:**
```tasks
not done
priority is high
sort by due
```

**Tasks by folder:**
```tasks
not done
group by folder
```

---

## Other Popular Plugins

### Calendar

Visual calendar for daily notes.

### Periodic Notes

Weekly, monthly, quarterly, yearly notes.

### Excalidraw

Hand-drawn style diagrams and drawings.

### Kanban

Kanban board in Obsidian.

### Advanced Tables

Better table editing with formatting.

### Outliner

Outliner-style note editing.

### Remotely Save

Sync via S3, Dropbox, WebDAV.

### Git

Version control with Git.

### Advanced URI

Extended URI actions for automation.

### MetaEdit

Edit frontmatter properties easily.

### Tracker

Track habits and data over time.

### Charts

Create charts from notes data.

### Breadcrumbs

Navigate note hierarchies.
