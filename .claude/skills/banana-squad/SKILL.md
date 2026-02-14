---
name: banana-squad
description: |
  Banana Squad: prompt-first "design agency" workflow for high-quality infographic generation.
  Use when you want structured, narrative prompt variations (MVP) and, later, generate+critique loops.
metadata:
  clawdbot:
    requires:
      bins: ["uv"]
---

# Banana Squad Skill (Prompt-First MVP)

## What This Skill Does Today

- Generates multiple **narrative prompt variations** for infographic-style image generation.
- Optionally collects a **small capped reference set** of "style inspiration" images from VisualCapitalist and
  writes attribution metadata to `sources.json`.

Image generation and critique loops will be layered on top once prompt generation is stable.

## Commands

### 1) Generate Prompt Variations (MVP)

```bash
uv run .claude/skills/banana-squad/scripts/bananasquad_prompts.py \
  --subject "AI investment trends in 2026" \
  --title "AI Investment Trends 2026" \
  --style visual_capitalist_like \
  --data '$50B total investment' \
  --data "35% YoY growth" \
  --count 5
```

Outputs:
- `$CURRENT_SESSION_WORKSPACE/work_products/banana_squad/runs/<timestamp>/prompts.json`
- `$CURRENT_SESSION_WORKSPACE/work_products/banana_squad/runs/<timestamp>/prompts.md`

### 2) Collect VisualCapitalist Style Inspiration (Capped + Rate Limited)

```bash
uv run .claude/skills/banana-squad/scripts/collect_visualcapitalist.py --max-images 30
```

Outputs:
- `Banana_Squad/reference_images/style_inspiration/visualcapitalist/downloads/*`
- `Banana_Squad/reference_images/style_inspiration/visualcapitalist/sources.json`

## Usage Guidance (Important)

- Prefer "prompt-only" mode when iterating on structure; it is cheap and fast.
- Keep tool calls sequential when running in-agent to avoid sibling error cascades.
- Do not attempt to write JSON via image generation tools. Write JSON with `Write` / filesystem tools.

## Future (Not In MVP)

Once prompt quality is stable, add a second step that feeds the best prompt(s) into UA image generation:
- `Task(subagent_type='image-expert', ...)` using `mcp__internal__generate_image_with_review`
- Or the `nano-banana-pro` skill for Gemini 3 Pro Image generation
