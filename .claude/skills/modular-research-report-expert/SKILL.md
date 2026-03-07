---
name: modular-research-report-expert
description: >
  Generate a publication-quality research report from a research corpus using an Agent Team
  with progressive deepening, draft-critique-revise loops, and integrated visual design.
  Orchestrate specialized teammates (Narrative Architect, Deep Reader, Storyteller,
  Visual Director, Diagram Craftsman, Editorial Judge) through a multi-phase pipeline
  that extracts maximum value from both refined and original source materials.
  Use when: (1) a refined_corpus.md or research corpus exists, (2) user asks to
  "build a report" from research, (3) user wants a visual, thematic, magazine-quality
  HTML report exported to PDF.
  Requires Agent Teams: CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1.
argument-hint: "corpus path, topic description, or task name"
user-invocable: true
---

# Modular Research Report Expert — Progressive Deepening Pipeline

Transform research into publication-quality reports through multi-pass corpus extraction,
narrative drafting with editorial revision loops, and visuals refined through
generate-critique cycles.

## Prerequisites

This skill requires **Agent Teams** (experimental).

```bash
echo $CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS
```

If not set:
```bash
claude config set env.CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS 1
```
Then restart.

---

## Core Innovation: Why This Is Different

The old pipeline reads `refined_corpus.md` once, generates an outline, drafts sections
in parallel with 20K-char truncated context, runs one cleanup pass, and compiles.
The result is factually correct but often reads like a robotic enumeration of data points.

**This pipeline fixes three fundamental problems:**

1. **Information Loss** — Instead of one distilled corpus, we mine BOTH the refined
   summary AND original source articles, routing section-specific source material to
   each writer pass. No fact left behind.

2. **Single-Pass Flatness** — Instead of draft-once-compile, we run a
   **Draft → Critique → Revise** loop. The Editorial Judge evaluates coherence,
   narrative voice, and thematic structure before the Storyteller revises.

3. **Bolted-On Visuals** — Instead of generating images separately and stapling them
   in, the Visual Director works from a **visual blueprint** created alongside the
   outline, and images go through a **Generate → Critique → Regenerate** cycle
   (inspired by the banana-squad pattern) before integration.

---

## Your Role: Report Director

You are the **Report Director** — the orchestrating team lead. You coordinate
teammates, manage phase transitions, and make editorial decisions. You do NOT write
report content yourself. You operate primarily through `SendMessage`, `TaskCreate`,
and `TaskUpdate`.

---

## Input: Corpus-Agnostic Discovery

This skill does NOT require a specific file format. It accepts **any research
material** — a single file, multiple files, or a directory tree — and dynamically
evaluates how to work with it.

### What Can Be Provided

| Input Type | Example | How It's Handled |
|-----------|---------|-----------------|
| Single refined corpus | `refined_corpus.md` | Classic path — Architect reads directly |
| Multiple markdown files | `research_notes/*.md` | Architect reads all, synthesizes into outline |
| Directory tree | `tasks/ukraine_war/` | Discovery scan + inventory |
| Raw crawled articles | `filtered_corpus/` | Deep Reader mines for depth |
| Mixed formats | `.md` + `.txt` + `.json` | Corpus Evaluator classifies each |
| Structured JSON data | `findings.json` | Architect extracts data, maps to sections |
| A task_name | `"ukraine_war_feb2026"` | Resolves to `$WORKSPACE/tasks/{name}/` |

### Discovery Protocol

**Step 1: Resolve the input.**

Try in order:
1. User provides explicit path(s) — use directly
2. User provides a task_name — resolve to `$CURRENT_SESSION_WORKSPACE/tasks/{name}/`
3. Scan `$CURRENT_SESSION_WORKSPACE/tasks/*/` for directories with `.md` files
4. Ask the user

**Step 2: Corpus Evaluation (NEW — Phase 0).**

Before any teammate is spawned, the **Report Director** (you) performs a lightweight
evaluation of the input material. This is YOUR job, not a teammate's.

Read the input and classify it:

```json
{
  "corpus_profile": {
    "type": "structured_refined|raw_articles|mixed|single_document|data_heavy",
    "total_files": 12,
    "total_size_chars": 85000,
    "has_refined_corpus": true,
    "refined_corpus_path": "tasks/topic/refined_corpus.md",
    "has_original_sources": true,
    "original_sources_dir": "tasks/topic/filtered_corpus/",
    "original_source_count": 16,
    "has_research_overview": true,
    "overview_path": "tasks/topic/research_overview.md",
    "content_nature": "news_events|analytical|technical|narrative|data_driven",
    "estimated_depth": "shallow|moderate|deep",
    "key_themes_detected": ["theme1", "theme2", "theme3"],
    "recommended_approach": "full_pipeline|skip_deep_reader|single_pass|data_first"
  }
}
```

Save this to `build/corpus-profile.json`. This profile drives adaptive behavior
throughout the pipeline.

**Classification rules:**

- `structured_refined`: Has `refined_corpus.md` + supporting files → Full pipeline
- `raw_articles`: Collection of articles without refinement → Architect must
  synthesize themes before outlining; consider generating a working summary first
- `mixed`: Has both refined and raw → Full pipeline with Deep Reader
- `single_document`: One large document → Skip Deep Reader, Architect works directly
- `data_heavy`: Lots of JSON/CSV/structured data → Data visualization emphasis,
  Architect should plan charts/tables prominently

**`recommended_approach` determines pipeline shape:**

- `full_pipeline`: All 6 phases, all teammates active
- `skip_deep_reader`: No original sources worth mining → skip Phase 2
- `single_pass`: Small corpus (< 5K tokens) → simplified pipeline, fewer sections
- `data_first`: Heavy data content → Visual Director and Diagram Craftsman get
  priority; Storyteller focuses on data narrative

**Step 3: Locate companion materials.**

Even if the user only provides one file, check for siblings:
- `filtered_corpus/` directory alongside refined corpus
- `research_overview.md` (source index)
- `search_results/` directory
- Any `manifest.json` from previous image generation

These are bonus materials that enrich the pipeline if they exist.

---

## Workflow: Six Phases

### Phase 0: Setup & Corpus Evaluation

1. **Discover and classify input** — follow the Corpus-Agnostic Discovery protocol above
2. **Read the primary input material** — skim enough to understand scope, themes, and nature
3. **Produce `corpus-profile.json`** — classify the corpus and determine `recommended_approach`
4. **Create output directory**:
   ```bash
   REPORT_DIR="report-$(date +%Y%m%d-%H%M%S)"
   mkdir -p "$REPORT_DIR"/{images,diagrams,build/sections,build/source-packs,build/critiques}
   ```
5. **Copy template**:
   ```bash
   cp <skill-dir>/assets/report-template.html "$REPORT_DIR/build/template.html"
   ```
6. **Save `corpus-profile.json`** to `$REPORT_DIR/build/corpus-profile.json`
7. **Create the team** via `TeamCreate`
8. **Adapt the pipeline** based on `recommended_approach`:
   - If `skip_deep_reader`: Don't spawn the Deep Reader teammate
   - If `single_pass`: Reduce section count, skip critique loop
   - If `data_first`: Spawn Visual Director and Diagram Craftsman early

### Phase 1: Thematic Analysis & Outline (Narrative Architect)

Spawn the **Narrative Architect** with the refined corpus.

The Architect produces TWO artifacts:

**A) `build/outline.json`** — Structured outline with source mapping:
```json
{
  "title": "Report Title",
  "subtitle": "Descriptive subtitle",
  "narrative_arc": "Brief description of the story this report tells",
  "sections": [
    {
      "id": "section-slug",
      "title": "Section Title",
      "narrative_role": "setup|rising_action|climax|resolution|context|analysis",
      "summary": "What this section covers and WHY it matters",
      "source_keywords": ["keyword1", "keyword2"],
      "key_quotes_to_find": ["person or org to look for quotes from"],
      "data_opportunities": ["stat or comparison to visualize"],
      "visual_type": "infographic|chart|diagram|hero|photo_style|none",
      "visual_brief": "Specific description of what the visual should show"
    }
  ],
  "visual_blueprint": [
    {
      "id": "visual-id",
      "type": "hero|infographic|chart|diagram|accent",
      "section_id": "target-section",
      "brief": "Detailed visual description",
      "data_points": ["specific numbers/facts to include"],
      "style_notes": "Color mood, composition guidance"
    }
  ]
}
```

**B) `build/source-map.json`** — Maps each section to relevant original source files:
```json
{
  "section-slug": {
    "primary_sources": ["filtered_corpus/crawl_abc123.md", "..."],
    "relevant_passages": ["keyword hints for the Deep Reader to extract"]
  }
}
```

The Architect reads both the refined corpus AND the `research_overview.md` (which
indexes original sources) to build the source map. If no original sources exist,
`source-map.json` is empty and Phase 2 is skipped.

**Broadcast** the outline to all teammates when complete.

### Phase 2: Source Mining (Deep Reader) — PARALLEL with Visual Planning

If `filtered_corpus/` exists and `source-map.json` has entries:

The **Deep Reader** reads original source articles and extracts section-specific
**source packs** — curated excerpts, quotes, statistics, and narrative details that
the refined corpus may have compressed away.

For each section in the outline:
1. Read the original articles listed in `source-map.json`
2. Extract relevant passages, quotes with attribution, specific statistics
3. Identify narrative details (anecdotes, scene-setting, human interest)
4. Write a **source pack** to `build/source-packs/{section-id}.md`

Source pack format:
```markdown
# Source Pack: {Section Title}

## Key Quotes
> "Direct quote here" — Speaker, Organization (Source: article_filename.md)

## Statistics & Data
- Specific stat with context (Source: article_filename.md)

## Narrative Color
- Vivid details, scene descriptions, human elements from sources

## Additional Context
- Background information the refined corpus may have omitted
```

**PARALLEL**: While Deep Reader mines sources, the Visual Director can begin
planning prompts from the visual blueprint in the outline.

### Phase 3: Narrative Drafting (Storyteller)

The **Storyteller** writes each section as a polished HTML fragment.

**Input per section**:
- The outline entry (narrative role, summary)
- The relevant portion of `refined_corpus.md` (Storyteller reads the full corpus)
- The source pack from `build/source-packs/{section-id}.md` (if exists)

**Writing directives** (embedded in spawn prompt):
- Write for a READER, not a database. Lead with the story, weave in facts.
- Use the `narrative_role` from the outline: "setup" sections establish context,
  "rising_action" builds tension, "climax" delivers the key revelation, etc.
- Integrate direct quotes from source packs naturally within paragraphs.
- Use stat-cards and key-finding boxes for standout data, not for every number.
- Write transitions that connect sections into a coherent narrative.
- Leave `<div class="image-slot" data-section="{id}"></div>` placeholders
  where visuals should appear (guided by the visual blueprint).

Output: `build/sections/{section-id}.html` per section.

**Executive Summary**: Written LAST, after all other sections, as a true synthesis.

### Phase 4: First Critique Round (Editorial Judge)

The **Editorial Judge** reviews all drafted sections against a structured rubric.

Read [references/critique-rubrics.md](references/critique-rubrics.md) for the full
rubric. The Judge produces `build/critiques/draft-critique.json`:

```json
{
  "overall_assessment": "narrative|factual|mixed — brief overall verdict",
  "coherence_score": 7,
  "narrative_flow_score": 6,
  "sections": {
    "section-id": {
      "strengths": ["what works well"],
      "issues": [
        {
          "type": "voice|coherence|gap|redundancy|transition|accuracy|visual_placement",
          "severity": "critical|major|minor",
          "location": "paragraph or quote reference",
          "problem": "specific description",
          "suggestion": "how to fix it"
        }
      ],
      "rewrite_needed": false
    }
  },
  "cross_section_issues": [
    {"type": "redundancy|inconsistency|missing_thread", "details": "..."}
  ]
}
```

The Judge also reviews visual placement — are `image-slot` divs in sensible
locations? Do they break narrative flow?

**Broadcast** critique to Storyteller and Visual Director.

### Phase 4b: Visual Critique Round (Editorial Judge on Images)

**PARALLEL with text critique**: Once the Visual Director has generated initial images,
the Judge evaluates them against the visual blueprint using the visual critique rubric
from [references/critique-rubrics.md](references/critique-rubrics.md).

The Judge produces `build/critiques/visual-critique.json`:

```json
{
  "images": {
    "visual-id": {
      "relevance_score": 8,
      "quality_score": 7,
      "text_legibility": "pass|fail|na",
      "brand_consistency": "pass|fail",
      "verdict": "approve|revise|regenerate",
      "revision_notes": "specific changes needed"
    }
  },
  "diagram_review": {
    "diagram-id": {
      "accuracy": "pass|fail",
      "readability": "pass|fail",
      "verdict": "approve|revise",
      "notes": "..."
    }
  }
}
```

### Phase 5: Revision Round (Storyteller + Visual Director)

**Text Revision**: The Storyteller reads `draft-critique.json` and revises:
- Sections marked `rewrite_needed: true` are rewritten from scratch
- Critical/major issues are addressed; minor issues are fixed in place
- Cross-section issues (redundancy, missing threads) are resolved
- Revised sections overwrite originals in `build/sections/`

**Visual Revision**: The Visual Director reads `visual-critique.json` and:
- Images marked `regenerate` get new prompts incorporating revision notes
- Images marked `revise` get edited (if edit tool supports it) or regenerated
- Images marked `approve` are kept as-is
- Updated manifest written to `images/manifest.json`

**Diagram Revision**: The Diagram Craftsman fixes any flagged diagrams.

### Phase 6: Assembly, Final Polish & Export

**6a — Assembly** (Storyteller):
1. Read `build/template.html`
2. Inject sections in outline order
3. Read `images/manifest.json` — replace `image-slot` divs with `<figure>` elements
4. Read `diagrams/manifest.json` — replace `diagram-slot` divs with diagram SVGs
5. Generate table of contents from section headings
6. Write `report.html`

**6b — Final Polish** (Editorial Judge):
- Light review of assembled HTML
- Check visual integration: images contextually placed? Captions meaningful?
- Verify no broken references, placeholder text, or rendering issues
- Minor copy-editing fixes applied directly
- Mark review complete

**6c — PDF Export** (You, the Director):
```bash
google-chrome --headless --disable-gpu --print-to-pdf="$REPORT_DIR/report.pdf" \
  --no-margins --print-background "$REPORT_DIR/report.html"
```
Fallback:
```bash
python3 -c "from weasyprint import HTML; HTML(filename='$REPORT_DIR/report.html').write_pdf('$REPORT_DIR/report.pdf')"
```

---

## Team Roster (6 Teammates)

Read [references/team-prompts.md](references/team-prompts.md) for full spawn prompts.

| # | Teammate | Phase | Key Contribution |
|---|----------|-------|------------------|
| 1 | **Narrative Architect** | 1 | Thematic outline + source mapping + visual blueprint |
| 2 | **Deep Reader** | 2 | Mines original sources into section-specific source packs |
| 3 | **Storyteller** | 3, 5, 6a | Narrative drafting, revision, assembly |
| 4 | **Visual Director** | 2-5 | Image generation with critique-driven refinement |
| 5 | **Diagram Craftsman** | 3, 5 | Mermaid diagrams + data visualizations |
| 6 | **Editorial Judge** | 4, 4b, 5, 6b | Multi-pass critique of text AND visuals |

---

## Task Creation Plan

Use `TaskCreate` with dependencies. Example task graph:

```
Phase 1: [outline]  ← Architect
Phase 2: [source-mining, visual-planning]  ← Deep Reader, Visual Director (parallel)
Phase 3: [draft-sections, generate-images, generate-diagrams]  ← Storyteller, Visual Dir, Diagram (parallel, after Phase 2)
Phase 4: [text-critique, visual-critique]  ← Judge (after Phase 3)
Phase 5: [revise-text, revise-visuals, revise-diagrams]  ← Storyteller, Visual Dir, Diagram (after Phase 4)
Phase 6: [assemble-html, final-polish, export-pdf]  ← Storyteller, Judge, Director (sequential)
```

**IMPORTANT**: Phases are checkpoints. Do NOT create all tasks upfront. Create Phase N+1
tasks only after Phase N completes and you've reviewed the outputs. This lets you adapt
the plan based on actual results (e.g., skip source mining if no filtered_corpus exists,
skip visual revision if all images approved).

---

## Visual Design Philosophy

### NOT This (Old Style)
- Random stock images dropped between paragraphs
- Uniform grid layouts
- Every section looks identical
- Images as decoration, not information

### THIS (New Style)
- **Hero image** sets the visual tone for the entire report
- **Infographics** are designed for specific data points from the outline
- **Inline accent images** break up long text sections contextually
- **Stat cards** present key numbers with visual emphasis
- **Diagrams** explain processes and relationships
- **Pull quotes** in styled callout boxes add narrative texture
- Images are placed at **narrative breakpoints** — between major ideas, not randomly

The Visual Director receives explicit `visual_brief` descriptions from the outline,
not generic "make an image about X" instructions. Each visual has a PURPOSE.

### Generate → Critique → Regenerate Loop

Inspired by the banana-squad workflow:

1. Visual Director generates image from `visual_brief` + `data_points`
2. Judge evaluates: relevance to section, text legibility (for infographics),
   brand consistency, visual quality
3. If `verdict: regenerate` — Visual Director creates new prompt incorporating
   Judge's notes, regenerates with `generate_image_with_review` (max_attempts=3)
4. Maximum 2 regeneration cycles per image before accepting best version

---

## Adaptive Behavior

### When Corpus is Small (< 5K tokens)
- Skip Phase 2 (source mining) — refined corpus has enough detail
- Reduce to 3-5 sections
- Reduce to 2-4 images

### When No Original Sources Exist
- Skip Phase 2 entirely
- Storyteller works from refined corpus only
- Note in report footer that depth was limited

### When Corpus is News/Events (Many Disparate Facts)
- Narrative Architect should recognize this pattern
- Use `narrative_role: "context"` and chronological ordering
- Storyteller adopts journalistic voice: factual, attributed, timeline-driven
- Visual Director favors timelines and maps over abstract infographics

### When Corpus is Thematic/Analytical
- Use full narrative arc (setup → analysis → findings → implications)
- Storyteller adopts analytical voice with interpretation
- Visual Director favors charts, comparisons, process diagrams

---

## Image Manifest Protocol

Same schema as `image-expert` subagent for ecosystem compatibility:

```json
{
  "images": [
    {
      "path": "images/filename.png",
      "alt_text": "descriptive alt text",
      "section_hint": "section-id",
      "purpose": "Caption or context",
      "width": 1024,
      "height": 1024,
      "visual_id": "from outline visual_blueprint"
    }
  ],
  "generated_at": "ISO-timestamp",
  "model_used": "gemini-2.5-flash-image",
  "count": 6,
  "revision_history": [
    {"visual_id": "id", "attempt": 2, "reason": "text illegible in infographic"}
  ]
}
```

---

## Diagram Protocol

Mermaid diagrams rendered to SVG:

```bash
npx --yes @mermaid-js/mermaid-cli@latest -i diagram.mmd -o diagram.svg -b transparent
```

Diagram manifest at `diagrams/manifest.json`:
```json
{
  "diagrams": [
    {"source": "diagrams/name.mmd", "rendered": "diagrams/name.svg",
     "section_hint": "section-id", "description": "what this shows"}
  ],
  "count": 3
}
```

---

## Error Recovery

- **Teammate failure**: Create replacement task, reassign to same or new teammate
- **Image generation failure**: Skip that image slot; Writer handles gracefully
- **Mermaid render failure**: Include `.mmd` source as code block fallback
- **Critique timeout**: Accept drafts as-is, proceed to assembly with note
- **PDF export failure**: Deliver HTML only, inform user
- **No filtered_corpus**: Skip Deep Reader phase, proceed with refined corpus only

---

## Output Structure

```
$REPORT_DIR/
├── report.html                    # Final assembled report
├── report.pdf                     # PDF export
├── images/
│   ├── hero_banner.png
│   ├── infographic_*.png
│   ├── accent_*.png
│   └── manifest.json
├── diagrams/
│   ├── *.mmd                      # Source files
│   ├── *.svg                      # Rendered SVGs
│   └── manifest.json
└── build/                         # Working artifacts (reviewable)
    ├── template.html
    ├── outline.json
    ├── source-map.json
    ├── source-packs/
    │   └── {section-id}.md
    ├── sections/
    │   └── {section-id}.html
    └── critiques/
        ├── draft-critique.json
        └── visual-critique.json
```

---

## Reporting to User

When complete, return:
- Path to `report.html` and `report.pdf`
- Section count and image/diagram counts
- Brief narrative summary of the report's story
- Note any sections where depth was limited by missing sources
- Critique scores (coherence, narrative flow) from the Judge
