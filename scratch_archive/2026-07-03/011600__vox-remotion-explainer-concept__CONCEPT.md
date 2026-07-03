# CONCEPT — Vox-Style Motion Graphics, Entirely From Code (Remotion)

**Seed:** ["I Made Vox-Style Motion Graphics Using Only Claude Code & Remotion"](https://youtu.be/7wuYBfE131U) — MoSidd | AI Made Easy, 2026-06-28, 44k views, 13m.

## The claim being demoed

The signature "Vox documentary" motion-graphics language — paper-cutout collages
popping in over a locked background, self-drawing charts, big animated stats,
narration-synced cuts, music underneath — can be produced **entirely as code**
with Remotion (the React video framework), authored by an AI agent from plain
English. No After Effects, no Premiere, no timeline editor, no motion designer.

## What the demo builds

`demo-vox-remotion-explainer` renders an original 39-second, 5-scene explainer —
***"The Box That Ate the World"*** (how the shipping container remade the world
economy) — in one command (`uv run python main.py`):

1. **Halftone paper cutouts, generated** — PIL draws a container ship, gantry
   cranes, dock workers, container stacks and waves, then screens them into
   black-and-white halftone dots on warm paper (the seed's "magazine papery
   feel"). One shared paper-texture background sits under every scene — the
   seed's locked-background trick that makes cuts feel like a single shot.
2. **Script AS the timeline** — each narration line is TTS-rendered
   (edge-tts, British-RP neural voice, standing in for the seed's ElevenLabs
   narrator); the measured audio durations *become* the scene durations in
   `timeline.json`. Every scene starts and ends on its own narration line.
3. **Animate with intent** — exactly the seed's two functions: `spring()` for
   staggered pop-ins (each cutout carrying the signature **red offset marker
   stroke**), `interpolate()` for drifts, count-ups, and a **line chart that
   draws itself**. Big stats stamp in ($5.86 → $0.16/ton, 50×, 90 sec, 90%).
4. **One real render** — `npx remotion render` (headless) produces
   `out/explainer.mp4`, 1920×1080 h264 + aac, narration and synthesized music
   bed mixed in.

Everything is generated in-repo — no stock assets, no paid APIs, no keys.

## Why it matters to us

- Agent-produced explainer videos become a **pipeline primitive**: point the
  technique at any topic (an intel brief, a demo, a product) and get a
  broadcast-styled 40s film with narration, data visuals, and music.
- The visual system is **data-drivable** — the chart is a real dataset, the
  stats are real numbers; a briefing cron could render its own weekly explainer.
- Zero-cost, fully deterministic asset path (PIL halftone + edge-tts + numpy
  music) means it runs anywhere the factory runs, including autonomously.

## Verification

- **DEMO_VERIFY** (deterministic): the pipeline runs end-to-end; 13
  `DEMO_SELFCHECK` asserts on the artifact (1080p h264, duration matches the
  narration timeline, audible mixed audio, halftone cutouts present,
  spring/interpolate-only animation, self-drawing chart, red offset stroke).
- **DEMO_EVAL** (independent fidelity judge): watches the seed video and the
  rendered MP4, derives the source's observable behaviors itself, and confirms
  each is reproduced.

## The reusable skill

The durable product is a skill that captures the recipe — script-as-timeline →
halftone cutout generation → layered spring scenes → VO-synced master sequence
→ headless render — so any future session can produce a Vox-style explainer on
any topic with one invocation.

## Notable build facts

- Remotion 4.0.484 / React 19.2.7 / Node 22 — current API, empirically verified
  by the render; Remotion auto-provisions its own headless Chrome.
- Remotion license: free tier (individuals / orgs ≤3 people) — verified.
- First full pipeline run passed all 13 selfchecks on the first attempt;
  the polish pass (caption backing, CRF 27 for judge-friendly file size) came
  after frame review.
