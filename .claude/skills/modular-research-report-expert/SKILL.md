---
name: modular-research-report-expert
description: >
  Generate a polished, visually rich research report from a refined research corpus using
  an Agent Team. Orchestrate specialized teammates (Architect, Writer, Visual Designer,
  Diagram Specialist, Editor) to produce a modern HTML report with AI-generated images,
  infographics, Mermaid diagrams, and data visualizations, then export as PDF.
  Use when: (1) a refined_research.md or large research corpus exists and needs to become
  a publication-quality report, (2) user asks to "build a report" from research findings,
  (3) user wants a visual, multi-section HTML report with charts/infographics exported to PDF.
  Requires Agent Teams: CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1.
---

# Modular Research Report Expert

Orchestrate an Agent Team to transform a research corpus into a publication-quality
HTML report with rich visuals, then export as PDF.

## Prerequisites

This skill requires **Agent Teams** (experimental).

```bash
echo $CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS
```

If not set, the user must run:
```bash
claude config set env.CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS 1
```
Then restart the session.

---

## Input Requirements

The primary input is a **refined research corpus** — typically `refined_research.md` or
`refined_corpus.md` produced by the `research-specialist` subagent. The corpus path is
provided as the skill argument or discovered in `$CURRENT_SESSION_WORKSPACE`.

If no corpus path is given, search for it:
1. `$CURRENT_SESSION_WORKSPACE/tasks/*/refined_corpus.md`
2. `$CURRENT_SESSION_WORKSPACE/tasks/*/refined_research.md`
3. Ask the user.

---

## Your Role: Report Director

You are the **Report Director** — the team lead. You coordinate teammates, you do NOT
write report content yourself. You operate in delegate mode after spawning the team.

### Responsibilities

1. Read and analyze the research corpus
2. Create the team and output directory
3. Spawn five specialized teammates with detailed prompts
4. Create phased tasks with dependencies
5. Enter delegate mode (Shift+Tab)
6. Route deliverables between teammates via `SendMessage`
7. Trigger PDF export when HTML is complete
8. Report final paths to the user

---

## Workflow

### Step 1: Analyze the Corpus

Read the research corpus. Identify:
- **Key themes** (3-8 major topics) — these become report sections
- **Data points** — statistics, comparisons, trends needing visualization
- **Process/architecture concepts** — candidates for Mermaid diagrams
- **Narrative arc** — logical ordering of themes

Produce a mental outline: title, sections, visual opportunities.

### Step 2: Set Up Output Directory

```bash
REPORT_DIR="report-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$REPORT_DIR"/{images,diagrams,build}
```

Copy the HTML template into the report directory:
```bash
cp <skill-dir>/assets/report-template.html "$REPORT_DIR/build/template.html"
```

Store the resolved absolute path — all teammates need it.

### Step 3: Create the Team

Use `TeamCreate` with a descriptive name (e.g., `report-<topic-slug>`).

### Step 4: Spawn Teammates

Spawn all five teammates using `Task` with `team_name` set. Read
[references/team-prompts.md](references/team-prompts.md) for the **full spawn prompts**
for each teammate before spawning.

| # | Teammate | Role | Key Tools |
|---|----------|------|-----------|
| 1 | **Report Architect** | Create structured JSON outline from corpus | Read, Write |
| 2 | **Content Writer** | Write each section as polished HTML fragments | Read, Write |
| 3 | **Visual Designer** | Generate hero images, infographics, charts | image-generation tools |
| 4 | **Diagram Specialist** | Create Mermaid diagrams rendered to SVG | Write, Bash (mermaid-cli) |
| 5 | **Quality Editor** | Review, fact-check, polish final HTML | Read, Write |

### Step 5: Create Phased Tasks

Use `TaskCreate` to create tasks in three phases:

**Phase 1 — Foundation (parallel)**
- `outline`: Architect analyzes corpus → produces `build/outline.json`
- `visuals-plan`: You broadcast the corpus themes so Visual Designer can begin planning

**Phase 2 — Production (after outline, parallel)**
- `write-sections`: Writer produces HTML fragments per section in `build/sections/`
- `generate-images`: Visual Designer creates 4-8 images → `images/` + `images/manifest.json`
- `generate-diagrams`: Diagram Specialist creates 2-4 Mermaid diagrams → `diagrams/`

**Phase 3 — Assembly (after Phase 2)**
- `compile-html`: Writer assembles fragments + images + diagrams into `report.html`
  using the HTML template from `build/template.html`
- `quality-review`: Editor reviews and polishes `report.html`
- `export-pdf`: You export `report.html` to PDF

### Step 6: Enter Delegate Mode

Press Shift+Tab to enter delegate mode. From here, use only coordination tools:
`TaskList`, `TaskUpdate`, `TaskCreate`, `SendMessage`.

### Step 7: Coordinate Production

Monitor task completion. When Phase 2 completes:
1. Instruct the Writer (via `SendMessage`) to assemble the final HTML
2. After assembly, instruct the Editor to review
3. After review, proceed to PDF export

### Step 8: Export to PDF

After the Editor marks the review complete, export to PDF using Chrome headless:

```bash
google-chrome --headless --disable-gpu --print-to-pdf="$REPORT_DIR/report.pdf" \
  --no-margins --print-background "$REPORT_DIR/report.html"
```

If Chrome is unavailable, fall back to WeasyPrint:
```bash
python3 -c "from weasyprint import HTML; HTML(filename='$REPORT_DIR/report.html').write_pdf('$REPORT_DIR/report.pdf')"
```

### Step 9: Report to User

Return:
- Path to `report.html` (browseable)
- Path to `report.pdf` (shareable)
- Image count and diagram count
- Section summary

---

## Image Manifest Protocol

The Visual Designer writes `images/manifest.json` following the same schema used by
the `image-expert` subagent:

```json
{
  "images": [
    {
      "path": "images/hero_banner.png",
      "alt_text": "description",
      "section_hint": "header",
      "purpose": "Report hero image",
      "width": 1024, "height": 1024
    }
  ],
  "generated_at": "ISO-timestamp",
  "count": 4
}
```

The Writer reads this manifest during assembly to inject `<img>` tags into matching sections.

---

## Diagram Protocol

The Diagram Specialist writes `.mmd` source files and renders to SVG:

```bash
npx --yes @mermaid-js/mermaid-cli@latest -i diagram.mmd -o diagram.svg -b transparent
```

SVGs are referenced in the HTML via `<img src="diagrams/name.svg">`.

---

## HTML Design

The report uses a modern, professional HTML design. See
[references/html-design.md](references/html-design.md) for the full CSS design system
and section patterns. The base template is at
[assets/report-template.html](assets/report-template.html).

Key design principles:
- Clean typography (system font stack)
- Responsive layout with max-width content area
- Cards for key findings and callout boxes for statistics
- Full-bleed hero image header
- Print-optimized CSS for PDF export

---

## Error Recovery

- If a teammate fails, create a replacement task and notify via `SendMessage`
- If image generation fails, the Writer skips that image slot gracefully
- If Mermaid rendering fails, include the `.mmd` source as a code block fallback
- If PDF export fails with both Chrome and WeasyPrint, deliver HTML only and inform user
