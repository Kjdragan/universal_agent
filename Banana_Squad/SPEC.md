# Banana Squad Spec (MVP)

## Summary

MVP is prompt-only. It generates a set of narrative prompt variations suitable for downstream image generation tools.

## Inputs

Structured request:
- `subject` (required)
- `title` (optional)
- `style` (optional, defaults to `visual_capitalist_like`)
- `data_points` (optional list)
- `constraints` (optional list)
- `count` (int, default 5; hard cap 10)

Optional reference context:
- `Banana_Squad/reference_images/style_inspiration/visualcapitalist/sources.json`
  can be used by downstream steps (future) to bias style.

## Outputs (MVP)

Written under the active session workspace:
- `work_products/banana_squad/runs/<timestamp>/prompts.json`
- `work_products/banana_squad/runs/<timestamp>/prompts.md`
- `work_products/banana_squad/runs/<timestamp>/request.json`

Output schema: `prompts.json`
```json
{
  "version": 1,
  "generated_at": "2026-02-14T00:00:00Z",
  "request": {
    "subject": "...",
    "title": "...",
    "style": "visual_capitalist_like",
    "data_points": ["..."],
    "constraints": ["..."],
    "count": 5
  },
  "prompts": [
    {
      "id": "p1",
      "style": "visual_capitalist_like",
      "prompt": "A narrative paragraph prompt..."
    }
  ]
}
```

## Future Outputs (post-MVP)

Once image generation is added, each run will also write:
- `work_products/banana_squad/runs/<timestamp>/rankings.json`
- `work_products/banana_squad/runs/<timestamp>/run.json` (full provenance)
- `work_products/media/manifest.json` (UA-compatible image manifest)

