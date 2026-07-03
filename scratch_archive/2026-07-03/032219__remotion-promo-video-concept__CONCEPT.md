# CONCEPT — Screenshots + Prompts → Software Promo Video (the "Remotion skill" workflow)

**Seed:** ["Bye bye, Premiere Pro? | This Claude skill is destroying every video editing tool"](https://youtu.be/AfVIVVLwlmY) — CenteIA Education En, 2026-06-03, 15m.

## The claim being demoed

With Claude + a "Remotion skill", anyone can turn a few product screenshots and
a plain-English spec into a professional software promo video — no Premiere, no
After Effects, no code ever seen — and then *improve it by talking to it*:
"it feels slow, make it more dynamic, show more of the product" → a better cut,
minutes later.

**Research finding:** there is no official Remotion-published Claude skill — in
the seed, Claude *self-authors* the skill when asked (remotion.dev only ships
AI-readable docs; GitHub has only community variants). The capability is the
workflow, and the factory's accrued skills are exactly that class of artifact —
the vault preflight correctly said REUSE the `/dragan:vox-explainer` machinery,
and this build does.

## What the demo builds

`demo-remotion-promo-video` produces a promo for a real product — **Demo
Factory itself** — in one command (`uv run python main.py`):

1. **Real screenshots, captured live** — headless Chrome shoots the factory's
   actual served pages (capability catalog, artifact archive, exhibit) at
   1920×1080 at render time; PIL draws the brand wordmark in the catalog's own
   indigo (`#7C6FE8`).
2. **The seed's v1 spec** — 15s, 30fps, 4 scenes: logo entrance animation →
   app screenshot → fade-to-black logo → CTA, dark palette + brand color.
3. **Feedback round 1** (the seed's words): "feels slow… more alive and
   dynamic… show more of the product" → 7 scenes, snappier logo, staggered
   taglines (RESEARCH. BUILD. VERIFY.), Ken Burns/slide/pan motion on
   browser-framed screenshots.
4. **Feedback round 2**: "eliminate the outro logo… shorten scenes three and
   four… live example to 2 seconds" → the final 13.5s cut.
5. **All three versions rendered and kept** (`out/promo_v1.mp4`, `promo_v2.mp4`,
   `promo.mp4`) — the natural-language feedback loop is *inspectable*, exactly
   the before/after the seed shows on screen. Duration derives from each spec
   via Remotion's `calculateMetadata` (input-props-driven — the doc-verified
   pattern from the previous run's research).

## Why it matters to us

- **Promo genre unlocked** as a sibling to the vox-explainer documentary genre:
  any product/dashboard/demo with a URL gets a 15-second branded promotional
  cut from live screenshots + one sentence of intent.
- **The feedback loop is the durable interface** — specs are tiny JSON diffs,
  so "make scene 3 shorter" is a one-line change any agent (or Kevin, by
  saying it) can apply and re-render in ~40 seconds.
- **Skill reuse proven**: this build stands on the vox-explainer skill's
  template, render mechanics, music synth, and selfcheck pattern — the factory
  compounding its own capabilities (skills that build better skills).

## Verification

- **DEMO_VERIFY** (deterministic): 12 `DEMO_SELFCHECK` asserts — ≥3 real
  1920px live captures, feedback progression (v2 scenes > v1; v3 < v2 with the
  outro logo eliminated), three 1080p h264+aac renders matching their specs,
  spring/interpolate motion, audible music bed.
- **DEMO_EVAL** (independent fidelity judge): watches the seed and the
  artifacts, derives the observable behaviors itself (logo entrance, real UI
  in motion, taglines, dark brand palette, CTA card, the before/after loop),
  and confirms each is reproduced.

## The reusable skill

A sibling skill to `/dragan:vox-explainer` capturing the promo recipe:
live screenshot capture → spec JSON → render → plain-English feedback rounds
as spec diffs → final cut. Free and keyless end to end (Chrome + PIL + numpy +
Remotion).
