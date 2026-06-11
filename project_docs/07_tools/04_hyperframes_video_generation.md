---
title: HyperFrames Video Generation (Studio + Pipeline)
status: active
canonical: true
subsystem: hyperframes-video-generation
code_paths: []
last_verified: 2026-06-11
---

# HyperFrames Video Generation (Studio + Pipeline)

> The capability lives **outside this repo** at
> `/home/kjdragan/lrepos/Cody_Code_Generations/hyperframes_video_generation/`
> (per the operator's rule that generated standalone projects live in
> `Cody_Code_Generations/`, not `universal_agent`). `code_paths` is therefore
> empty; this doc is the canonical UA-side description of what the capability
> is, how it came to exist, where it stands, and how UA is expected to adopt
> it. The project's own `PLAYBOOK.md` and `README.md` are the operational
> source of truth — when this doc and those files disagree, those files win.

## What it is

An **interview-driven video-generation system** built on
[HyperFrames](https://github.com/heygen-com/hyperframes) (HeyGen, Apache 2.0):
compositions are plain HTML + CSS + seekable GSAP timelines, rendered to
deterministic MP4 by headless Chrome + FFmpeg. On top of that open-source
renderer sits a pipeline and a Claude Agent SDK app ("HyperFrames Studio"):

- **Pipeline** — the substitutable creative artifact is `beat_sheet.json`
  (scenes: narration line + visual concept + archetype). Deterministic scripts
  turn it into per-scene TTS narration with measured timing
  (`scripts/generate_narration.py`; Gemini 3.1 Flash TTS default via the
  `gemini-tts-narrator-tf` skill's Cloud TTS script, Kokoro offline fallback)
  and generated image assets (`scripts/generate_image.py`; Gemini
  `pro`=gemini-3-pro-image-preview / `flash`=gemini-3.1-flash-image, up to 14
  reference images for character consistency). The LLM authors the composition
  HTML guided by a scene-archetype library, then a deterministic QA loop runs
  (`hyperframes lint`/`validate`/`inspect`), frames are extracted and
  vision-reviewed, and themes are switched at render time via a composition
  variable (`--variables '{"theme":"..."}'`).
- **Studio app** — `app/` wraps the pipeline in a `claude-agent-sdk` session:
  terminal chat (`uv run studio-chat`) and a FastAPI websocket web UI
  (`uv run studio-web`, `localhost:7340`, runs as the `hyperframes-studio`
  systemd **user** service on the desktop — deliberately NOT a UA runtime
  unit). Model presets `fable-5` / `opus-4.8` / `sonnet` / `haiku` with
  mid-session switching via SDK session resume; auth rides the Max-plan OAuth
  (`ANTHROPIC_*` env scrubbed; `STUDIO_KEEP_ANTHROPIC_ENV=1` opts into
  API-key billing).
- **Learning loop** — `uv run studio-review` compacts a session transcript
  deterministically and has a model write a debrief (friction, protocol
  adherence, proposed patches, durable lessons) into `reviews/`; accepted
  lessons are folded into the protocol and `PLAYBOOK.md`'s Lessons section.

## How it came to exist (2026-06-10 → 06-11)

1. Operator spotted HyperFrames; investigation + grilling session settled
   topic, voice, themes, and the local-CLI path (the hosted HyperFrames MCP
   exists but outsources authoring to HeyGen's agent and bills credits).
2. First production: **"The Life of a Task in Universal Agent"** (73s, 8
   scenes, Aoede narration, 3 theme renders). Root-caused one major rendering
   bug en route: outgoing scene transitions must use `tl.to()` — a
   `tl.fromTo(old, {opacity:1}, …)` immediateRenders opacity onto later scenes
   at construction and blacks out earlier scenes.
3. The pipeline was generalized (beat-sheet schema, narration timing manifest,
   8 interview "option-card" docs distilled from HyperFrames' own skills) and
   wrapped in the Studio app; an SDK-verifier pass and two real operator runs
   ("miss_m_meets_fiona" story video with 10 generated image assets;
   "baked_mac_and_cheese" template fast-path, idea → clean video in ~16 min)
   drove ~15 protocol/tool fixes, all captured as PLAYBOOK lessons.

## Where it stands (2026-06-11)

Working v1: three finished videos, three switchable themes, narration-driven
timing with a duration gate, image assets with character consistency, visual
QA with vision, the studio-review loop, and a durable desktop service. Known
seams: pacing is per-voice (measured table in `docs/narration.md`), the
`validate` contrast auditor can't resolve `var()`/`color-mix()` (verify
visually), Vertex TTS content-filters terse sentence fragments.

## Strategic role and UA adoption status

This is the reference **service widget** in the operator's portfolio strategy
(see `WIDGET_CATALOG.md` at the `Cody_Code_Generations` root): ad/promo video
generation for small-business clients — tier *charged*, ops model
*Kevin-managed/gatekept*, with per-client brand specs (`frame.md`) as the
recurring-value mechanism. Simone's `memory/HEARTBEAT.md` carries the standing
"Service-Widget Portfolio" directive (PR #927) that generates further widget
candidates through the insight-brief → Task Hub funnel.

**UA adoption is design-only so far** — no UA skill, cron, or Task Hub lane
invokes this capability yet (per the verification rules, it is not "deployed"
into UA). The agreed adoption path, pending operator go: (A) vendor a skill
into `.claude/skills/` that consumes the project's own PLAYBOOK/docs (no
forked protocol), interactive-on-desktop first; (B) provision VPS render
prerequisites (Node 22, headless Chrome, FFmpeg) through the deploy pipeline
and measure; (C) wire one invoker end-to-end (email → Task Hub → Simone →
skill → scratchpad link) with full Task Hub observability; (D) autonomous
producers (CSI recap video, demo walkthroughs) behind flags, dormancy-gated.
Inference binding for composition authoring should be Anthropic-native (the
load-bearing GSAP rules are subtle; GLM-via-ZAI authoring is untested).
Next planned production: the ClearSpring ad dogfood (deliverables land in
`clearspringcg-landing` per repo scoping).
