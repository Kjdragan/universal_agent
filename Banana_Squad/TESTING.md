# Banana Squad Testing

## Prompt-Only Tests (MVP)

1. Generate prompts (no references):
```bash
uv run .claude/skills/banana-squad/scripts/bananasquad_prompts.py \
  --subject "AI investment trends in 2026" \
  --title "AI Investment Trends 2026" \
  --style visual_capitalist_like \
  --data '$50B total investment' \
  --data "35% YoY growth" \
  --count 5
```

2. Collect style inspiration references (capped):
```bash
uv run .claude/skills/banana-squad/scripts/collect_visualcapitalist.py \
  --max-images 30 \
  --sleep-seconds 1.0
```

3. Generate prompts after collecting references (future behavior; MVP ignores):
Run test #1 again; prompts should still generate even if refs are missing.

## In-System (Sub-Agent) Smoke Prompt

Use the system UI / chat to delegate:
- "Use `banana-squad-expert` to generate 5 narrative prompt variations for a Visual Capitalist-like infographic about AI investment trends in 2026. Include these data points: ..."
