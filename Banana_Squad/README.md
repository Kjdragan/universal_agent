# Banana Squad

Banana Squad is a multi-step "design agency" workflow for producing high-quality infographic-style images.

This directory is intentionally kept lightweight and merge-friendly with Universal Agent:
- Execution is driven via a Universal Agent **sub-agent** + **skill** (see `.claude/agents/banana-squad-expert.md`
  and `.claude/skills/banana-squad/`).
- Outputs are written into the active session workspace under `work_products/banana_squad/`.
- Reference inspiration images (optional) are stored under `Banana_Squad/reference_images/`.

## Goals

- Produce better prompts (narrative prompts, not keyword soup)
- Support a full pipeline later (generate -> critique -> revise)
- Keep artifacts and manifests compatible with UA conventions

## What Exists Today

- Prompt-only pipeline: generate `N` narrative prompt variations from a structured request.
- Reference collector: download a small capped set of "style inspiration" images + attribution metadata.

Image generation and critique loops will be added after prompt-only is stable.

## Repo Integration

To invoke in-system, delegate to the sub-agent:
- `Task` -> `subagent_type="banana-squad-expert"`

Or run scripts directly:
```bash
uv run .claude/skills/banana-squad/scripts/bananasquad_prompts.py --help
uv run .claude/skills/banana-squad/scripts/collect_visualcapitalist.py --help
```

