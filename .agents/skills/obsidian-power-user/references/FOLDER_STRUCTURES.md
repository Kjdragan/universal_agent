# Folder Structures Reference

Vault organization archetypes with tree diagrams and setup scripts.

---

## Output Format

Always provide folder structures in BOTH formats:

1. **Tree diagram** — Visual representation
2. **Bash script** — `mkdir -p` commands

---

## PARA Method

Projects, Areas, Resources, Archives.

### Tree

```
vault/
├── 00-Inbox/              # Capture everything here first
│   └── quick-capture.md
├── 01-Projects/           # Active projects with deadlines
│   ├── project-alpha/
│   │   ├── notes.md
│   │   └── tasks.md
│   └── project-beta/
├── 02-Areas/              # Ongoing responsibilities
│   ├── health/
│   ├── finances/
│   ├── career/
│   └── relationships/
├── 03-Resources/          # Reference material
│   ├── articles/
│   ├── books/
│   ├── tutorials/
│   └── templates/
├── 04-Archives/           # Completed/inactive items
│   ├── projects/
│   └── old-notes/
├── Attachments/           # Images, PDFs, files
├── Daily Notes/           # Date-based notes
├── Templates/             # Note templates
└── MOCs/                  # Maps of Content
```

### Script

```bash
mkdir -p vault/{00-Inbox,01-Projects,02-Areas/{health,finances,career,relationships},03-Resources/{articles,books,tutorials,templates},04-Archives/{projects,old-notes},Attachments,Daily\ Notes,Templates,MOCs}
```

---

## Zettelkasten

Atomic notes with unique IDs.

### Tree

```
vault/
├── 00-Inbox/              # Quick capture
│   └── fleeting-notes.md
├── 01-Fleeting/           # Temporary notes to process
│   └── 20240115-idea.md
├── 02-Literature/         # Notes from sources
│   ├── books/
│   ├── articles/
│   └── papers/
├── 03-Permanent/          # Atomic idea notes
│   ├── 20240115-unique-id-concept.md
│   └── 20240116-unique-id-another.md
├── 04-Structure/          # MOCs and index notes
│   ├── topics/
│   └── themes/
├── References/            # Source bibliographies
├── Templates/
└── Daily Notes/
```

### Script

```bash
mkdir -p vault/{00-Inbox,01-Fleeting,02-Literature/{books,articles,papers},03-Permanent,04-Structure/{topics,themes},References,Templates,Daily\ Notes}
```

---

## Second Brain (Tiago Forte)

Extended PARA with knowledge management.

### Tree

```
vault/
├── 0-Inbox/               # Quick capture
├── 1-Projects/            # Active endeavors
│   ├── active/
│   ├── on-hold/
│   └── someday/
├── 2-Areas/               # Responsibility domains
│   ├── personal/
│   ├── professional/
│   └── household/
├── 3-Resources/           # Knowledge library
│   ├── concepts/
│   ├── people/
│   ├── companies/
│   ├── frameworks/
│   └── how-to/
├── 4-Archives/            # Past projects/areas
├── Meetings/              # Meeting notes
├── Daily Notes/           # Journal
├── Templates/
├── Kanban/                # Project boards
└── Attachments/
```

### Script

```bash
mkdir -p vault/{0-Inbox,1-Projects/{active,on-hold,someday},2-Areas/{personal,professional,household},3-Resources/{concepts,people,companies,frameworks,how-to},4-Archives,Meetings,Daily\ Notes,Templates,Kanban,Attachments}
```

---

## Work/Team Vault

Professional knowledge management.

### Tree

```
vault/
├── 00-Inbox/
├── 01-Active/             # Current work
│   ├── projects/
│   ├── sprints/
│   └── tasks/
├── 02-Knowledge/          # Team knowledge
│   ├── processes/
│   ├── guides/
│   ├── policies/
│   └── best-practices/
├── 03-Meetings/           # Meeting notes
│   ├── 1-on-1s/
│   ├── team/
│   └── clients/
├── 04-Clients/            # Client information
│   └── client-name/
├── 05-Products/           # Product documentation
│   ├── features/
│   ├── roadmaps/
│   └── specs/
├── 06-People/             # People/contacts
├── 07-Archives/           # Past work
├── Templates/
├── Daily Notes/
└── Attachments/
```

### Script

```bash
mkdir -p vault/{00-Inbox,01-Active/{projects,sprints,tasks},02-Knowledge/{processes,guides,policies,best-practices},03-Meetings/{1-on-1s,team,clients},04-Clients,05-Products/{features,roadmaps,specs},06-People,07-Archives,Templates,Daily\ Notes,Attachments}
```

---

## Content Creation

For writers, YouTubers, podcasters.

### Tree

```
vault/
├── 00-Ideas/              # Content ideas
│   ├── blog-ideas.md
│   ├── video-ideas.md
│   └── podcast-ideas.md
├── 01-In-Progress/        # Active content
│   ├── blog/
│   ├── videos/
│   └── podcast/
├── 02-Published/          # Finished content
│   ├── blog/
│   ├── videos/
│   └── podcast/
├── 03-Research/           # Content research
│   ├── topics/
│   ├── sources/
│   └── references/
├── 04-Assets/             # Media assets
│   ├── images/
│   ├── thumbnails/
│   ├── audio/
│   └── video/
├── 05-Calendar/           # Content calendar
├── Templates/             # Content templates
│   ├── blog-post.md
│   ├── video-script.md
│   └── podcast-outline.md
├── Daily Notes/
└── Analytics/             # Performance tracking
```

### Script

```bash
mkdir -p vault/{00-Ideas,01-In-Progress/{blog,videos,podcast},02-Published/{blog,videos,podcast},03-Research/{topics,sources,references},04-Assets/{images,thumbnails,audio,video},05-Calendar,Templates,Daily\ Notes,Analytics}
```

---

## Research Vault

Academic or professional research.

### Tree

```
vault/
├── 00-Inbox/              # Incoming material
├── 01-Sources/            # Source material
│   ├── papers/
│   ├── books/
│   ├── articles/
│   ├── datasets/
│   └── interviews/
├── 02-Notes/              # Reading notes
│   ├── by-source/
│   └── by-topic/
├── 03-Concepts/           # Key concepts
│   └── concept-name/
├── 04-Questions/          # Research questions
├── 05-Hypotheses/         # Working hypotheses
├── 06-Analysis/           # Data analysis
│   ├── methodology/
│   └── findings/
├── 07-Writing/            # Draft work
│   ├── outline.md
│   ├── drafts/
│   └── final/
├── 08-Bibliography/       # Citations
├── 09-Archive/            # Old versions
├── Templates/
├── Daily Notes/
└── Attachments/
```

### Script

```bash
mkdir -p vault/{00-Inbox,01-Sources/{papers,books,articles,datasets,interviews},02-Notes/{by-source,by-topic},03-Concepts,04-Questions,05-Hypotheses,06-Analysis/{methodology,findings},07-Writing/{drafts,final},08-Bibliography,09-Archive,Templates,Daily\ Notes,Attachments}
```

---

## Personal PKM

Simple personal knowledge management.

### Tree

```
vault/
├── Inbox/                 # Quick capture
├── Notes/                 # General notes
│   ├── personal/
│   ├── work/
│   └── random/
├── Journal/               # Daily/weekly reflection
│   ├── daily/
│   └── weekly/
├── Projects/              # Active projects
├── Reference/             # Reference material
│   ├── recipes/
│   ├── health/
│   ├── travel/
│   └── manuals/
├── Goals/                 # Goal tracking
│   ├── 2024/
│   └── archive/
├── People/                # People notes
├── Books/                 # Book notes
├── Templates/
└── Attachments/
```

### Script

```bash
mkdir -p vault/{Inbox,Notes/{personal,work,random},Journal/{daily,weekly},Projects,Reference/{recipes,health,travel,manuals},Goals/{2024,archive},People,Books,Templates,Attachments}
```

---

## Minimal Vault

Essential structure only.

### Tree

```
vault/
├── Inbox/
├── Notes/
├── Projects/
├── Archive/
├── Templates/
└── Attachments/
```

### Script

```bash
mkdir -p vault/{Inbox,Notes,Projects,Archive,Templates,Attachments}
```

---

## Best Practices

### Naming Conventions

- Use lowercase with hyphens: `project-alpha`
- Prefix with numbers for ordering: `01-Active`
- Be consistent across folders

### Folder Depth

- Keep 2-4 levels deep maximum
- Too deep = hard to navigate
- Use tags instead of deep nesting

### Special Folders

| Folder | Purpose |
|--------|---------|
| `.obsidian/` | Configuration (auto-created) |
| `Templates/` | Template files |
| `Attachments/` | Media files |
| `Daily Notes/` | Date-based notes |

### Cross-Platform

- Avoid special characters in folder names
- Use consistent case
- Test paths on all platforms
