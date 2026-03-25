---
name: modular-research-report-expert
description: >
  Generate a publication-quality research report from a research corpus using an Agent Team
  with progressive deepening, draft-critique-revise loops, and integrated visual design.
  Orchestrate specialized teammates (Narrative Architect, Deep Reader, Storyteller,
  Visual Director, Diagram Craftsman, Editorial Judge) through a multi-phase pipeline
  that extracts maximum value from both refined and original source materials.
  Use when: (1) a refined_corpus.md or research corpus exists, (2) user asks to
  "build a report" from research, (3) user wants a professional, visually-integrated
  HTML report exported to PDF. Adapts structure, tone, and component usage to the
  material — works across any topic domain.
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
2. User provides a task_name — resolve to `$CURRENT_RUN_WORKSPACE/tasks/{name}/`
3. Scan `$CURRENT_RUN_WORKSPACE/tasks/*/` for directories with `.md` files
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

## Concurrency Governance

Agent Teams run multiple teammates in parallel, but each active teammate consumes
an LLM inference slot. Unbounded parallelism hits rate limits and degrades quality
(retries, timeouts, truncated outputs). This skill enforces a **concurrency ceiling**.

### Parameter: `MAX_CONCURRENT_AGENTS`

```
Default: 4
Range: 2–8
```

Set this in the corpus profile or override via environment:
```bash
export REPORT_MAX_CONCURRENT_AGENTS=5
```

The Director reads this value during Phase 0 and records it in `corpus-profile.json`:
```json
{
  "corpus_profile": { ... },
  "concurrency": {
    "max_concurrent_agents": 4,
    "source": "default|env_override|user_specified"
  }
}
```

### How It Works

The Director is the **concurrency scheduler**. Before creating tasks for a new phase,
count how many teammates currently have `in_progress` tasks (check via `TaskList`).
Only assign new work if `active_count < MAX_CONCURRENT_AGENTS`.

**Concurrency is managed at phase transitions, not mid-phase.** Within a phase, the
Director assigns up to `MAX_CONCURRENT_AGENTS` parallel tasks and waits for all to
complete before moving on. Between phases, the Director reassesses.

### Phase-by-Phase Concurrency Map

This table shows the **maximum active agents** at each phase boundary and how to
stay within the ceiling. Adjust by serializing phases when the ceiling is low.

| Phase | Active Teammates | Concurrency | Notes |
|-------|-----------------|-------------|-------|
| 0 | Director only | 1 | Corpus evaluation — you do this yourself |
| 1 | Architect | 1 | Sequential — must complete before anything else |
| 2 | Deep Reader + Visual Director | 2 | Parallel pair — always safe |
| 3 | Storyteller + Visual Director + Diagram Craftsman | 3 | Core parallel phase |
| 4 | Editorial Judge | 1 | Judge works alone (reads all outputs) |
| 4b | Editorial Judge (visuals) | 1 | Can overlap with text critique if ceiling >= 2 |
| 5 | Storyteller + Visual Director + Diagram Craftsman | 3 | Revision — same as Phase 3 |
| 6a | Storyteller | 1 | Assembly is sequential |
| 6b | Editorial Judge | 1 | Final polish |
| 6c | Director | 1 | PDF export |

**Peak concurrency: Phase 3** — Storyteller + Visual Director + Diagram Craftsman = 3 active.
This is well within the default ceiling of 4.

### When `MAX_CONCURRENT_AGENTS = 2` (Conservative)

Serialize Phase 3:
1. Storyteller drafts all sections (alone)
2. Then Visual Director generates images (alone)
3. Then Diagram Craftsman creates diagrams (alone)

Serialize Phase 5 similarly. This is slower but avoids rate limit pressure entirely.

### When `MAX_CONCURRENT_AGENTS >= 5` (Aggressive)

Overlap Phase 2 and Phase 3 partially:
- Deep Reader starts source mining for early sections
- Storyteller begins drafting sections that don't need source packs
- Visual Director begins hero image + section-independent visuals

Also overlap Phase 4 text critique with Phase 4b visual critique (Judge runs both).

### When `MAX_CONCURRENT_AGENTS >= 6` (Full Parallel)

All 6 teammates can be active simultaneously during peak phases. The Director can:
- Run Phase 3 with all three producers + Deep Reader finishing late sections
- Run Phase 4 text + visual critique simultaneously
- Start Phase 5 revision for early-critiqued sections while Judge still reviewing others

**Recommendation**: Start with the default of 4. If you observe no rate limit errors
in the first run, try 5. If you see 429s or timeouts, drop to 3.

### Team Size vs. Concurrency Ceiling

The team has 6 teammates, but `MAX_CONCURRENT_AGENTS` may be lower than 6. This is
fine — **larger teams run sequentially within the ceiling, not in violation of it.**

If the pipeline needs more parallelism than the ceiling allows, the Director simply
serializes the excess work. The team size is about *specialization* (each teammate
has a focused role), not about running everyone simultaneously.

Example with ceiling = 3 during Phase 3 (3 producers):
- Storyteller, Visual Director, Diagram Craftsman all active → 3 = ceiling, OK
- If future pipeline adds a 4th producer → queue it until one of the 3 finishes

Example with ceiling = 2 during Phase 3:
- Storyteller runs alone → completes → Visual Director runs → completes → Diagram runs
- Same work, same quality, just sequential

**The ceiling constrains concurrency, never team composition.** Add more specialized
teammates if the report quality benefits — just schedule them within the ceiling.

### Teammate Lifecycle & Slot Tracking

Teammates are **spawned once** and reused across phases. A teammate is "active" when
it has an `in_progress` task. Between phases, teammates are idle (not consuming slots).

Track active slots in a mental model:
```
Phase 3 start:
  [ACTIVE] Storyteller: draft-sections
  [ACTIVE] Visual Director: generate-images
  [ACTIVE] Diagram Craftsman: generate-diagrams
  [IDLE]   Narrative Architect (done in Phase 1)
  [IDLE]   Deep Reader (done in Phase 2)
  [IDLE]   Editorial Judge (waiting for Phase 4)
  Active: 3 / MAX: 4 ✓

Phase 3→4 transition:
  Storyteller completes → [IDLE]
  Visual Director completes → [IDLE]
  Diagram Craftsman completes → [IDLE]
  Judge starts text-critique → [ACTIVE]
  Active: 1 / MAX: 4 ✓
```

If a teammate's task is taking too long and you need to start the next phase,
you can proceed with available slots as long as `active_count < MAX_CONCURRENT_AGENTS`.
For example, if Visual Director is still generating the last image but Phase 4 text
critique can start, go ahead — that's 2 active, well within ceiling.

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
> "Direct quote here" — Speaker, Organization

## Statistics & Data
- Specific stat with context

## Narrative Color
- Vivid details, scene descriptions, human elements from sources

## Additional Context
- Background information the refined corpus may have omitted

## Sources Used (for end-of-report bibliography)
- filename.md | Title: Article Title | URL: https://...
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
- **University-level writing quality.** Clear, substantive prose with logical flow
  and precise language. The reader is intelligent and engaged.
- **Tone follows material.** Read `tone` and `material_type` from the outline.
  A market analysis needs authority. A profile needs narrative warmth. A scientific
  review needs methodical precision. Do not default to one voice for every topic.
- **Components follow `component_guidance`.** The outline specifies which components
  (stat-cards, pull-quotes, callouts, etc.) suit THIS report. Use only what's
  specified — these are a toolkit, not a checklist.
- Write for a READER, not a database. Lead with substance, weave in facts.
- Use the `narrative_role` from the outline flexibly: "setup", "analysis",
  "comparison", "synthesis", "implications" etc.
- Integrate direct quotes from source packs naturally within paragraphs.
- Use stat-cards and key-finding boxes for standout data, not for every number.
- Write transitions that connect sections into a coherent narrative.
- Leave `<div class="image-slot" data-section="{id}"></div>` placeholders
  where visuals should appear (guided by the visual blueprint).
- **NO inline citations.** No footnote numbers, superscripts, or `(Source: ...)`
  in body text. All sources are collected in an end-of-report bibliography.
- **Conflicting data/perspectives**: When sources present different data, conclusions,
  or viewpoints, discuss them in the report — don't silently pick one. Present the
  prevailing view as the main thread and note dissenting perspectives. Do NOT ignore
  prevailing opinion just because an alternative source exists; equally, do NOT suppress
  minority viewpoints. The reader benefits from seeing the landscape.

Output: `build/sections/{section-id}.html` per section.

**Executive Summary**: Written LAST, after all other sections, as a true synthesis.

### Phase 4: First Critique Round (Editorial Judge)

The **Editorial Judge** reviews all drafted sections against a structured rubric.

Read [references/critique-rubrics.md](references/critique-rubrics.md) for the full
rubric. The Judge produces `build/critiques/draft-critique.json`:

```json
{
  "overall_assessment": "narrative|source-fidelity|mixed — brief overall verdict",
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

Use `TaskCreate` with dependencies. **Respect `MAX_CONCURRENT_AGENTS`** — check
`TaskList` for active tasks before assigning new ones.

### Task Graph (default ceiling = 4)

```
Phase 1: [outline]                                          ← Architect         (active: 1)
    ↓
Phase 2: [source-mining, visual-planning]                   ← Reader, Visual    (active: 2)
    ↓
Phase 3: [draft-sections, generate-images, generate-diagrams] ← Story, Visual, Diagram (active: 3)
    ↓
Phase 4: [text-critique, visual-critique]                   ← Judge             (active: 1-2)
    ↓
Phase 5: [revise-text, revise-visuals, revise-diagrams]     ← Story, Visual, Diagram (active: 3)
    ↓
Phase 6: [assemble-html] → [final-polish] → [export-pdf]   ← Story → Judge → Director (active: 1)
```

### Task Graph (conservative ceiling = 2)

```
Phase 1: [outline]                    ← Architect         (active: 1)
    ↓
Phase 2a: [source-mining]            ← Reader             (active: 1)
Phase 2b: [visual-planning]          ← Visual             (active: 1)
    ↓
Phase 3a: [draft-sections]           ← Storyteller        (active: 1)
Phase 3b: [generate-images]          ← Visual Director    (active: 1)
Phase 3c: [generate-diagrams]        ← Diagram Craftsman  (active: 1)
    ↓
Phase 4: [text-critique]             ← Judge              (active: 1)
Phase 4b: [visual-critique]          ← Judge              (active: 1)
    ↓
Phase 5a: [revise-text]              ← Storyteller        (active: 1)
Phase 5b: [revise-visuals]           ← Visual Director    (active: 1)
    ↓
Phase 6: [assemble] → [polish] → [pdf]                    (active: 1)
```

### Task Graph (aggressive ceiling = 6)

```
Phase 1: [outline]                                          ← Architect         (active: 1)
    ↓
Phase 2+3 overlap:
  [source-mining]           ← Reader        ┐
  [visual-planning]         ← Visual        │
  [draft-early-sections]    ← Storyteller   ├ (active: 4-5)
  [generate-hero-image]     ← Visual        │
  [generate-diagrams]       ← Diagram       ┘
    ↓
Phase 4: [text-critique + visual-critique]  ← Judge (active: 1-2)
    ↓
Phase 5: [revise-text, revise-visuals, revise-diagrams]     (active: 3)
    ↓
Phase 6: sequential                                          (active: 1)
```

**IMPORTANT**: Phases are checkpoints. Do NOT create all tasks upfront. Create Phase N+1
tasks only after Phase N completes and you've reviewed the outputs. This lets you adapt
the plan based on actual results (e.g., skip source mining if no filtered_corpus exists,
skip visual revision if all images approved).

**Before each phase transition**: Run `TaskList`, count active tasks, compare to
`MAX_CONCURRENT_AGENTS`. Only proceed when slots are available.

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

**Core principle: the report adapts to the material, not the other way around.**

Reports span every domain — technology, finance, health, policy, culture, science,
history, and more. No single template, tone, or component mix fits all. The Narrative
Architect's `material_type` and `component_guidance` fields in `outline.json` drive
adaptation decisions downstream. All teammates read these fields and adjust accordingly.

### Material-Driven Adaptation

The Narrative Architect classifies the material and sets `component_guidance`:

| Material Pattern | Structure | Tone | Stat-cards | Pull-quotes | Diagrams |
|---|---|---|---|---|---|
| Data-heavy analysis | Logical progression | Analytical | Heavy | Light | Essential |
| News/events | Chronological | Journalistic | Moderate | Heavy (expert voices) | Supplementary |
| Technology review | Problem → approaches → comparison | Explanatory | Moderate | Moderate | Essential |
| Policy/regulation | Context → landscape → implications | Authoritative | Light | Heavy (officials) | Supplementary |
| Profile/case study | Origin → development → impact | Narrative | Light | Heavy | Minimal |
| Scientific review | Background → methods → findings → gaps | Analytical | Moderate | Light | Essential |
| Market landscape | Overview → segments → trends → outlook | Authoritative | Heavy | Moderate | Essential |
| Historical/cultural | Thematic or chronological | Narrative | Rare | Heavy | Supplementary |
| Comparative review | Framework → candidates → synthesis | Explanatory | Moderate (tables) | Moderate | Moderate |

### Component Toolkit (Not Checklist)

The HTML template provides a toolkit of components. **Not every report uses all of them.**
The Narrative Architect decides which components earn their place in THIS report:

- **Stat-cards**: Use when the material has standout quantitative data. Skip for
  narrative/qualitative topics.
- **Key-finding boxes**: Use when a section has a clear, concrete takeaway. Skip
  when the value is in the nuance, not a headline.
- **Pull-quotes**: Use when compelling voices are in the sources. Skip when the
  material is data/analysis without attributable quotes.
- **Callouts**: Use for caveats, definitions, or highlighted recommendations.
  Skip when the prose handles nuance inline.
- **Tables**: Use for structured comparisons. Don't force tabular format on
  material that reads better as prose.
- **Diagrams**: Use when relationships, flows, or hierarchies need visual
  explanation. Skip when the material is narrative or descriptive.
- **Images/infographics**: Use when data visualization or scene-setting adds
  real value. Reduce count for topics where visuals would be decorative filler.

### Corpus-Driven Scaling

| Corpus Size | Sections | Images | Diagrams |
|---|---|---|---|
| Small (< 5K tokens) | 3-5 | 2-4 | 0-1 |
| Medium (5K-20K tokens) | 4-6 | 3-5 | 1-2 |
| Large (20K+ tokens) | 5-8 | 4-8 | 2-4 |

### Source Availability

- **No original sources (filtered_corpus/)**: Skip Phase 2 entirely. Storyteller
  works from refined corpus only.
- **Few original sources (1-5)**: Phase 2 is quick — focus on the most quote-rich
  and data-rich articles.
- **Many original sources (10+)**: Phase 2 is thorough — Deep Reader should
  prioritize based on source-map relevance rankings.

### What NOT to Do

- Don't force a "dramatic narrative arc" on a straightforward analytical topic.
  A market overview doesn't need "rising_action" — it needs clear segmentation.
- Don't add stat-cards to a topic with no meaningful quantitative data. A
  cultural analysis may have zero numbers and that's fine.
- Don't generate images for the sake of having images. If the topic is abstract
  and every image would be generic stock-photo-style filler, use fewer or none.
- Don't use pull-quotes when the corpus has no attributable voices. A single-source
  dataset or statistical report may have nothing quotable.
- Don't assume "magazine style" for every topic. A technical comparison may read
  better with a methodical, reference-guide voice than a narrative one.

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
