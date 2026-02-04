# YouTube Tutorial Learning Skill — PRD

## Overview

A Claude Code skill that transforms YouTube tutorials into working knowledge: educational documentation, functional implementations, and optionally new skills for the multi-agent system.

## Problem Statement

YouTube contains valuable technical tutorials, but the knowledge is locked in video format. Extracting actionable understanding requires:
- Watching the full video
- Taking notes on both what is *said* and what is *shown*
- Capturing code, diagrams, and UI interactions that appear on screen but aren't verbalized
- Filling gaps where the video is incomplete
- Actually implementing what was taught
- Organizing the output for future reference

**Critical insight:** Tutorials are visual media. Code on screen, terminal output, architecture diagrams, and UI demonstrations often aren't described in words—they're just shown. Transcript-only extraction misses a substantial portion of the teaching.

This skill automates the entire pipeline, producing durable artifacts from both the audio/transcript AND visual content of the video.

## Workflow

```
[User provides YouTube URL]
        │
        ▼
[Dual extraction]
   ├── youtube-transcript skill → spoken narration
   └── yt-dlp download → video file for visual analysis
        │
        ▼
[Video analysis via Z.AI Vision MCP Server]
   ├── Identify visual teaching moments (code on screen, diagrams, UI demos)
   ├── Extract text/code shown but not spoken (OCR)
   ├── Capture architecture diagrams, flowcharts, terminal output
   └── Describe UI interactions and demonstrations
        │
        ▼
[Agent synthesizes transcript + visual analysis]
   ├── Merges spoken explanation with on-screen content
   ├── Reconciles "what they said" vs "what they showed"
   ├── Identifies gaps or ambiguities
   └── Conducts supplementary research as needed
        │
        ▼
[Generates artifacts → artifacts/ directory]
   ├── manifest.json
   ├── README.md (summary + TOC)
   ├── CONCEPT.md (educational document)
   ├── IMPLEMENTATION.md (usage guide)
   ├── implementation/ (working code/scripts/configs)
   ├── research/ (gap-filling sources, citations)
   ├── visuals/ (key frames, extracted diagrams)
   └── transcript.txt
        │
        ▼
[Agent evaluates skill-worthiness]
   └── If relevant to agent/automation patterns:
       → Prompts user for approval to generate SKILL.md
        │
        ▼
[On approval: skill-creator generates skill → .claude/skills/yt-{slug}/]
```

## Output Location

Artifacts follow the repo-wide artifacts storage convention:

```
{repo-root}/artifacts/youtube-tutorial-learning/{YYYY-MM-DD}/{video-slug}/
```

Example:
```
/home/kjdragan/lrepos/universal_agent/artifacts/youtube-tutorial-learning/2025-02-04/claude-code-task-system/
├── manifest.json
├── README.md
├── CONCEPT.md
├── IMPLEMENTATION.md
├── implementation/
│   ├── example_task.py       # Runnable, validated code
│   └── config.yaml
├── research/
│   └── sources.md
├── visuals/
│   ├── code-extractions/
│   │   ├── index.md          # Summary with confidence ratings
│   │   ├── main_py_final.py  # Raw OCR with annotations
│   │   ├── config_yaml.yaml
│   │   └── fragments/
│   │       └── notes.md      # What's missing/uncertain
│   ├── diagrams/
│   │   └── architecture.png
│   └── key-frames/
│       └── key-frames.md     # Index with timestamps
├── transcript.txt (retention: temp)
└── video-segments/ (retention: temp, if downloaded)
```

**If a SKILL.md is generated** (with user approval), it goes to the actual skills directory:
```
/home/kjdragan/lrepos/universal_agent/.claude/skills/yt-{slug}/
├── SKILL.md
└── (symlink or reference to artifacts/ for examples)
```

This separation means:
- **Learning artifacts** accumulate in `artifacts/` with full provenance
- **Skills** only land in `.claude/skills/` when explicitly promoted
- Deleting a skill doesn't destroy the learning artifacts
- Multiple skill iterations can reference the same artifact set

## Artifact Specifications

### manifest.json
Required metadata for every run:

```json
{
  "created_at": "2025-02-04T14:32:00Z",
  "skill_name": "youtube-tutorial-learning",
  "artifact_id": "claude-code-task-system",
  "source_session_id": "session_abc123",
  "source_prompt": "Use our YouTube tutorial skill on this video: https://...",
  "inputs": {
    "video_url": "https://youtube.com/watch?v=...",
    "video_title": "Claude Code Task System Deep Dive",
    "video_duration_seconds": 847,
    "channel": "Anthropic",
    "linked_resources": {
      "github_repo": "https://github.com/...",
      "blog_post": null,
      "docs": "https://docs.anthropic.com/..."
    }
  },
  "extraction": {
    "transcript_available": true,
    "video_analyzed": true,
    "video_segments_analyzed": 3,
    "visual_content_type": ["screen-recording", "diagrams", "terminal"],
    "ocr_performed": true,
    "code_extraction": {
      "files_extracted": 3,
      "overall_confidence": "medium",
      "fragments_incomplete": true,
      "code_from_linked_repo": false,
      "reconstruction_required": true
    }
  },
  "outputs": {
    "concept": "CONCEPT.md",
    "implementation_guide": "IMPLEMENTATION.md",
    "code": "implementation/",
    "research": "research/sources.md",
    "visuals": "visuals/",
    "code_extractions": "visuals/code-extractions/",
    "transcript": "transcript.txt",
    "skill_generated": false,
    "skill_path": null
  },
  "tags": ["claude-code", "task-system", "agent-patterns"],
  "retention": {
    "default": "keep",
    "transcript.txt": "temp",
    "video-segments/": "temp"
  },
  "gaps_filled": true,
  "skill_proposed": true,
  "skill_approved": false
}
```

### README.md
Skill manifest and quick reference:
- Source video URL and title
- Date processed
- One-paragraph summary
- Table of contents for the directory
- Tags/categories for discoverability

### CONCEPT.md
Educational document capturing the video's teaching:
- Written as a standalone tutorial
- Explains the "what" and "why"
- Structured for someone who hasn't watched the video
- Includes diagrams/illustrations where helpful (described or generated)

### IMPLEMENTATION.md
Practical usage guide:
- How to use the implementation artifacts
- Prerequisites and dependencies
- Step-by-step instructions
- Expected outputs

### implementation/
Working code, scripts, or configurations:
- Functional examples demonstrating the technique
- Should run without modification (or with documented setup)
- Includes comments referencing relevant CONCEPT.md sections

### research/
Gap-filling documentation:
- sources.md: Links and citations for external research
- Any additional notes on ambiguities resolved
- Alternative approaches discovered

### visuals/
Extracted visual content from video analysis:

```
visuals/
├── code-extractions/     # Highest-priority: on-screen code (see detailed section below)
│   ├── index.md          # Summary of all extractions with confidence ratings
│   ├── {filename}.{ext}  # Individual extracted files
│   ├── fragments/        # Incomplete extractions
│   └── evolution/        # Code progression (if instructive)
├── diagrams/             # Architecture, flow, UML diagrams
├── key-frames/           # Important visual moments
│   └── key-frames.md     # Index with timestamps and descriptions
└── terminal-output/      # Command outputs, error messages
```

See **On-Screen Code Extraction** section for detailed handling of code artifacts.

### SKILL.md (conditional)
If the video teaches a technique relevant to Claude Code or multi-agent systems:
- Generated using the skill-creator skill
- Follows standard skill format with description, triggers, instructions
- References implementation/ for concrete examples

---

## Skill-Worthiness Criteria

The agent should propose SKILL.md generation when the video covers:

- Claude Code features, workflows, or patterns
- Agent architecture techniques (orchestration, context management, tool use)
- MCP server development or integration
- Prompt engineering patterns
- Automation workflows applicable to coding/research tasks
- API integrations useful for agent capabilities

The agent presents its assessment and asks:
> "This tutorial covers [X], which could be useful as a reusable skill for [Y use cases]. Should I generate a SKILL.md?"

User confirms or declines. No skill generated without explicit approval.

---

## Artifacts Storage Integration

This skill follows the repo-wide artifacts storage convention:

| Principle | Implementation |
|-----------|----------------|
| Persistent root | `{repo-root}/artifacts/youtube-tutorial-learning/` |
| Session workspace is scratch | Intermediate files (drafts, raw API responses) stay in session dir |
| Skill outputs go to artifacts/ | All durable outputs written directly to artifacts path |
| Folder convention | `artifacts/{skill_name}/{YYYY-MM-DD}/{short_slug}/` |
| Always write manifest | `manifest.json` in every artifact directory |
| Retention tagging | Large/ephemeral files (transcript, raw dumps) marked `retention: "temp"` |
| Referenceability | Later agents reference via `artifacts/...` paths, never session paths |

**Key implication:** When the agent performs gap-filling research or generates implementation code, it writes directly to the artifacts path—not to a session workspace that would need "promotion."

---

## Dependencies

This skill orchestrates:

1. **youtube-transcript** — Transcript extraction (already available at `/mnt/skills/user/youtube-transcript/SKILL.md`)

2. **Z.AI Vision MCP Server** — Video analysis via GLM-4.6V model
   - NPM package: `@z_ai/mcp-server`
   - Requires: `Z_AI_API_KEY` environment variable
   - Tools used:
     - `video_analysis` — Scene/content understanding (≤8 MB; MP4/MOV/M4V)
     - `extract_text_from_screenshot` — OCR for code, terminals, docs
     - `understand_technical_diagram` — Architecture, flow, UML diagrams
     - `ui_to_artifact` — Turn UI screenshots into specs/descriptions

3. **yt-dlp** — Video download for local analysis
   - Download at reduced quality to stay under 8MB limit when possible
   - Or segment extraction for longer videos

4. **Web search** — Gap-filling research via web_search tool

5. **Documentation fetching** — Official docs via web_fetch, Hugging Face docs, etc.

6. **skill-creator** — For generating SKILL.md when approved (at `/home/kjdragan/lrepos/universal_agent/.claude/skills/skill-creator/SKILL.md`)

---

## Video Analysis Strategy

### Why Visual Analysis Matters

YouTube tutorials are inherently visual. Critical information often appears on screen but is never spoken:
- Code being typed or edited
- Terminal output and error messages
- Architecture diagrams drawn in real-time
- UI navigation and button clicks
- Configuration files and settings panels
- Whiteboard explanations

**Transcript-only extraction misses 30-70% of tutorial content.**

### Handling the 8MB Video Limit

The Z.AI Vision MCP has an 8MB limit for video analysis. Strategies:

1. **Short videos (<10 min):** Download at 360p/480p quality, usually fits under 8MB
2. **Medium videos (10-30 min):** 
   - Download at lowest quality
   - If still over limit: extract key segments based on transcript timestamps
3. **Long videos (>30 min):**
   - Identify high-value segments from transcript (code demos, diagram explanations)
   - Extract and analyze segments individually
   - Stitch insights together

### When to Prioritize Visual Analysis

**Always attempt full visual analysis.** The Z.AI Vision MCP handles content type detection implicitly—it will extract what's useful regardless of whether the video is a screen recording, talking head, or slide presentation.

Don't try to pre-filter or skip analysis based on guessed content type. Let the vision model do the work and report what it finds.

### Visual Artifact Extraction

For each video, the skill should attempt to extract:
- **Key frames** — Screenshots of important moments (diagrams, final code state, UI)
- **Code blocks** — OCR'd code from screen recordings (see detailed guidance below)
- **Diagrams** — Architecture/flow diagrams for inclusion in CONCEPT.md

These go into `visuals/` directory with descriptive filenames.

---

## On-Screen Code Extraction (Critical)

Code shown on screen is the **highest-value visual artifact** in programming tutorials. However, extracting usable code from video requires careful handling because on-screen code is rarely clean or complete.

### Common Challenges

| Challenge | Description |
|-----------|-------------|
| **Incremental typing** | Code is typed progressively; early frames show incomplete snippets |
| **Scrolling** | File is scrolled up/down; no single frame shows the complete code |
| **Partial visibility** | Code extends beyond screen edges; line beginnings/endings cut off |
| **Multiple iterations** | Same code shown repeatedly as it's built up, refactored, or debugged |
| **Context switching** | Presenter jumps between files; fragments from different files interleaved |
| **Overlays and popups** | IDE autocomplete, tooltips, or other UI elements obscure code |
| **Transient states** | Code with intentional errors shown during debugging, then fixed |
| **Resolution/blur** | Low video quality makes characters ambiguous (0 vs O, l vs 1, etc.) |

### Extraction Strategy

1. **Identify the "final state"**
   - Track code evolution through the video
   - Prioritize the last/most complete version of each code block
   - Note if presenter explicitly marks something as "final" or "complete"
   - Be aware: the "final" code shown may still have bugs the presenter hasn't noticed

2. **Reconstruct from fragments**
   - When code is scrolled, stitch together visible portions from multiple frames
   - Use OCR on multiple frames and deduplicate/merge intelligently
   - Flag uncertainty: "Lines 15-20 reconstructed from partial views"
   - Don't invent code to fill gaps—mark them explicitly

3. **Distinguish files and contexts**
   - Track which file is being edited (look for filename in IDE title bar, tabs)
   - Separate code blocks by source file in output
   - Note relationships: "This function is called from main.py shown earlier"
   - Watch for presenter switching between terminal and editor

4. **Handle duplicates explicitly**
   - Don't just OCR every frame—recognize when the same code appears multiple times
   - Capture the evolution: v1 → v2 → final, if instructive
   - In CONCEPT.md, show the final version; note iterations in implementation notes if relevant
   - If the same code appears 10 times, you need ONE good extraction, not 10 duplicates

5. **Validate against transcript**
   - Cross-reference OCR'd code with any code the presenter reads aloud or describes
   - Use spoken variable names, function names to verify OCR accuracy
   - If presenter says "we'll add a timeout parameter" but OCR doesn't show it, flag the gap
   - Presenter's verbal description often clarifies ambiguous characters

6. **Handle incomplete/broken code**
   - Tutorials often show code that doesn't work yet (building up to working version)
   - Clearly distinguish "work in progress" from "final working code"
   - If presenter shows an error and then fixes it, capture both states with context
   - Don't extract intentionally broken code as if it were the solution

### Output Structure for Extracted Code

In `visuals/code-extractions/`:

```
visuals/
├── code-extractions/
│   ├── index.md              # Summary of all extracted code with confidence notes
│   ├── main_py_final.py      # Final state of main.py
│   ├── config_yaml.yaml      # Extracted config file
│   ├── fragments/            # Partial/uncertain extractions
│   │   ├── utils_partial.py  # Incomplete—only lines 1-30 visible
│   │   └── notes.md          # What's missing and why
│   └── evolution/            # If showing code progression is instructive
│       ├── handler_v1.py
│       ├── handler_v2.py
│       └── handler_final.py
├── diagrams/
└── key-frames/
```

### Confidence Annotations

Every extracted code block should include a confidence header:

```python
# SOURCE: tutorial-video @ 14:32-15:47
# FILE: src/main.py (inferred from IDE tab)
# CONFIDENCE: high | medium | low
# NOTES: Lines 45-50 partially obscured by autocomplete popup; reconstructed from context
# COMPLETE: yes | no | partial
# VALIDATED: yes | no (whether this code was tested/run)
# ---

def main():
    config = load_config("settings.yaml")
    # ... actual extracted code ...
```

**Confidence levels:**
- **High**: Code clearly visible, no ambiguity, matches verbal description
- **Medium**: Most code visible, some characters inferred from context
- **Low**: Significant reconstruction required, gaps present, or resolution issues

### Integration with CONCEPT.md

When incorporating extracted code into CONCEPT.md:
- Use the **highest-confidence, most-complete version**
- Note if code was reconstructed: "The following is assembled from multiple screen captures"
- If code is incomplete, say so explicitly and fill gaps via research if possible
- Don't present uncertain code as authoritative
- Reference the timestamp where this code appears for users who want to verify

### Integration with implementation/

Code in `implementation/` should be:
- **Runnable** — not raw OCR output
- **Complete** — gaps filled via research or marked with explicit TODOs
- **Validated** — if possible, tested to confirm it works
- **Clearly sourced** — cross-referenced to `visuals/code-extractions/` for provenance

The relationship between extracted and implemented code:
```
visuals/code-extractions/main_py_final.py  →  "raw" OCR output, annotated
         ↓ (gap-filling, validation, cleanup)
implementation/main.py  →  runnable code with comments
```

### When Extraction Fails

If code cannot be reliably extracted:
- Note the timestamp and describe what was shown
- Explain why extraction failed (blur, speed, obstructions)
- Search for the code in associated resources (video description, linked repo, blog post)
- If the presenter mentions a GitHub repo, fetch code from there instead
- Mark in manifest: `"code_extraction_confidence": "low"` or `"code_from_linked_repo": true`

---

## Invocation

User triggers with natural language:

```
"Use our YouTube tutorial skill on this video: [URL]"
"Process this tutorial: [URL]"
"Learn from this video and implement it: [URL]"
```

---

## Success Criteria

A successful run produces:

1. ✅ `manifest.json` with full provenance and output paths
2. ✅ Both transcript AND visual analysis attempted (with graceful degradation if one fails)
3. ✅ `CONCEPT.md` that someone unfamiliar with the video could learn from, incorporating both spoken and visual content
4. ✅ `visuals/code-extractions/` with annotated code, confidence ratings, and honest uncertainty flags
5. ✅ Working implementation in `implementation/` that is runnable (not just raw OCR output)
6. ✅ Clear provenance from extracted code → implemented code (gaps documented)
7. ✅ Clear documentation of any gaps filled via research
8. ✅ Skill proposal (if applicable) with user confirmation before generation
9. ✅ All artifacts in `artifacts/youtube-tutorial-learning/{date}/{slug}/`
10. ✅ Any generated skill properly placed in `.claude/skills/yt-{slug}/`

---

## Failure Modes & Handling

| Failure | Handling |
|---------|----------|
| Transcript unavailable | Fall back to video analysis + title/description; note limitation in manifest |
| Video download fails | Proceed with transcript-only; note "visual analysis unavailable" in CONCEPT.md |
| Video exceeds 8MB after compression | Extract key segments based on transcript timestamps; analyze in chunks |
| Z.AI Vision MCP unavailable | Proceed with transcript-only; flag that visual content may be missing |
| Code extraction low confidence | Use linked repo if available; flag uncertainty in implementation; add TODOs |
| Code incomplete/fragmented | Document what's missing; search linked resources; mark gaps explicitly |
| Code extraction fails entirely | Check video description for linked repo; use research to reconstruct; note provenance |
| Video too vague to implement | Produce CONCEPT.md only; note that implementation wasn't feasible and why |
| Implementation requires unavailable resources | Document what's needed; produce partial implementation with TODOs |
| Skill-creator unavailable | Skip SKILL.md generation; note in README that manual skill creation may be warranted |
| Both transcript AND video analysis fail | Report to user; offer to work from video title/description + web research only |

---

## Open Questions

1. **Video length limits?** Very long videos (2+ hours) may need chunked processing or selective focus. Should the skill ask user to specify focus areas for long content?

2. **Multiple videos?** Should the skill support batch processing (playlist URL, multiple URLs)? Or keep it single-video for v1?

3. **Update workflow?** If a video gets revisited with new context, should the skill support updating an existing directory vs. creating a new one?

---

## Next Steps

1. Review and approve this PRD
2. Read the skill-creator skill to understand output format
3. Draft the SKILL.md for youtube-tutorial-learning
4. Test with a real video
