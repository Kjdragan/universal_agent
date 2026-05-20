# Research: Remotion (Programmatic React Video) + AI Assets Workflow

**Requested by:** seed concept (landing-ad-videos)
**Date:** 2026-05-20

## Question
What is the canonical 2026 pattern for compositing AI-generated stills/clips with code-driven typography and transitions in Remotion? Strengths, limits, time-per-iteration for a solo operator.

## Findings

### What Remotion is, concretely
Remotion is a React framework — every frame is a React component rendered at a specific timestamp. Anything you can build in React (CSS, Canvas, SVG, Lottie, web fonts, video tags, audio tags, HTML5 video) becomes a video element. Outputs render to MP4 / WebM / GIF via headless Chromium. Compositions are **parameterized** — build a template once, feed JSON or props for chapter titles, colors, durations, image URLs, and the same animation reuses with different content.

This makes it the *exact* right tool for the seed's "is this you?" scenario set: you build one composition (intro, pain point, AI reveal, CTA), then render N variants by swapping image assets, headlines, and color palette.

### The canonical 2026 pipeline pattern (consensus across sources)
The dominant pattern in early 2026 is **AI-orchestrated Remotion editing** rather than pre-built CLI pipelines. Karen Spinner's "I built a video post-production pipeline. It failed." (Mar 2026) is the canonical writeup of why:

> "The CLI wasn't a bad idea, it was a premature idea. The micro-adjustments that derailed the CLI weren't a bug in my workflow. They're just what video post-production looks like."

The pipeline that wins in 2026:

```
1. Nano Banana Pro (Gemini 3 Pro Image) → generate stills, characters, backgrounds
   - $0.045/image at 1080p, ~$0.15/image at 4K
   - Best in class for text rendering accuracy
2. Recraft API → background removal (free tier)
   - Avatar stills → transparent PNGs
3. Veo 3.1 OR Kling 3.0 → optional 8-15s motion clips (per P2 research)
4. Remotion (React composition) → composites everything:
   - Code-driven kinetic typography (headlines animate per-frame)
   - Code-driven transitions (spring easing, fade, parallax)
   - Stills become layered backgrounds
   - AI motion clips become inserted accent shots
   - Lower thirds, captions, brand chrome — all code
5. Render via @remotion/cli render OR Lambda render
6. Optional: FFmpeg post-pass for audio normalization (-14 LUFS YouTube standard)
```

The most-cited orchestrator setup in 2026: **Remotion + Claude Code (or OpenCode / Codex) skill**. Remotion now ships official "AI agent skill" packages and a documented system prompt for LLMs (https://www.remotion.dev/docs/ai/system-prompt). Sabrina.dev's tutorial and a viral Instagram reel from @adamgoodyer demonstrate: "describe what you want → Claude writes Remotion components → render → iterate in conversation." A 48-second motion-graphic promo was built start-to-finish 100% with AI orchestration + Remotion per a Nov 2025 Reddit r/google_antigravity post.

### Strengths
1. **Parameterized templates = batch render variants cheaply.** Build the "is this you?" intro template once. Render 5 ad variants by passing different JSON. Critical for an A/B-testable landing page.
2. **Pixel-perfect typography and brand control.** Web fonts, real CSS, brand-exact colors. AI video tools (Veo/Sora/Kling) hallucinate text, mangle logos, and drift colors. Remotion guarantees brand fidelity for everything that must be brand-exact.
3. **Composable with AI video and AI image outputs.** Insert an 8s Veo clip as a `<Video>` element, layer code-driven captions on top, fade in a Nano Banana still as a parallax background. Best of both worlds — AI for the impossible-to-script stuff, code for everything that must be reliable.
4. **Git-versionable.** "Video as a Git commit" — A/B variants are pull requests. Stakeholder feedback turns into code diffs. Unique to Remotion among video tools.
5. **Renders to Lambda for parallel batch jobs.** Render 50 variants in parallel for ~$1-5 of AWS Lambda time. This is what makes the "build once, render many" promise actually pay off.

### Limits
1. **It's React. Solo operator must be comfortable writing JSX, props, and CSS.** Not a no-code tool — even with Claude Code orchestration, you need to debug React errors, understand Hooks, and reason about animation timing (`useCurrentFrame`, `interpolate`).
2. **Custom source-available license** — free for individuals AND companies with up to 3 people. Larger companies pay. Solo operator is fine; this is a no-issue for the project.
3. **Slower to iterate than a true AI video tool for "show me what this looks like" exploration.** A Veo prompt → 30s. A Remotion composition write + render → 5-30 minutes per iteration depending on complexity. Faster for variants AFTER template exists; slower for first concept.
4. **No motion-design tool ergonomics.** No timeline scrubber, no curve editor (without 3rd party plugin), no keyframe drag. Everything is `interpolate(frame, [0, 30], [0, 1])`. Engineers love it; designers from After Effects backgrounds hate it.
5. **Audio sync gotchas at concat boundaries.** Different audio configs between Remotion-rendered segments and external clips produce audible pops at join points. Always do a final FFmpeg audio re-encode pass at -14 LUFS.
6. **AI video clip integration is "video in video," not motion-graphic-merged.** An 8s Veo clip placed inside a Remotion composition stays as a discrete video element — you can crop it, mask it, layer text over it, but you can't actually animate INTO and OUT OF the AI-generated motion smoothly without manual compositing tricks.

### Time-per-iteration sweet spots (solo operator, Claude Code-orchestrated)

| Phase | Time | Cost | Notes |
|---|---|---|---|
| First composition setup | 2-6 hours | ~$5-20 (AI assets) | Worth it — template reusable |
| Per-variant render after template exists | 10-30 min | ~$1-5 | Includes asset swap + re-render |
| Single Remotion render locally | 30s-3min | Free | Depends on length/complexity |
| 50 variants on Lambda parallel | 10-15 min | ~$1-5 | Built-in feature |
| Pure AI video iteration (Kling 3.0 only) | 1-3 min per clip | $0.50/clip | Faster, less control, no brand-exact text |
| Pure AI video iteration (Veo 3.1) | 30-90s per clip | $1-5 per clip | Mid speed, premium quality |

### When to use what — decision framework for landing-ad-videos
- **Use Remotion when:** brand chrome / typography / logo / pricing-block / CTA copy must be pixel-exact, you need to render N parameterized variants, you want git-tracked iteration, you have a 3-7 shot composition where 2-3 shots are AI-generated.
- **Use AI video tools standalone when:** brainstorming/prototyping, single 8-15s shot, no precise text requirements, you want to test if a scenario "vibes" before committing.
- **Use BOTH (recommended for this project):**
  - Veo 3.1 or Kling 3.0 → generate 8-15s motion clips of the "amazing world of AI" or "struggling owner" scenarios
  - Nano Banana Pro → generate consistent character stills and background plates
  - Remotion → wrap with kinetic typography hero text, brand colors, real-product screenshots, CTA, render N variants for the "is this you?" different framings.

## Key Takeaways

1. **Remotion in 2026 = the "best of both worlds" tool for B2B landing-page ads.** It's the only practical way to get brand-exact typography and colors while still leveraging AI for character stills and motion clips. AI video tools standalone CANNOT do brand-exact text; Remotion can.
2. **Canonical 2026 stack: Nano Banana Pro (stills) + Veo 3.1 or Kling 3.0 (motion clips, optional) + Remotion (composition + typography + brand chrome) + Claude Code orchestration.** All of these are already available to the operator per the seed.
3. **Build parameterized templates once → batch-render 50+ "is this you?" variants in 15 min for ~$5 of Lambda time.** This makes A/B testing on the landing page actually economical. No other 2026 video pipeline matches this cost-per-variant.
4. **The pre-built-CLI trap is documented and real.** Don't build a "video pipeline tool" first. Iterate conversationally via Claude Code skill until you know which steps are repeatable, THEN tool what's stable. Same as the rest of UA's "code-verified answers" philosophy.
5. **Solo operator caveat: must be React-comfortable.** This is the highest-skill option in the stack. If operator isn't comfortable with JSX/props/Hooks, fall back to Kling 3.0 Multi-Shot Storyboard standalone (sacrifices brand-exact text but is no-code).

## Sources

| # | Source | URL | What it contributed |
|---|--------|-----|---------------------|
| 1 | Remotion homepage | https://www.remotion.dev/ | Official framework intro, licensing tiers |
| 2 | Remotion docs: AI Coding Agents | https://www.remotion.dev/docs/ai/coding-agents | Official setup for Claude Code / OpenCode / Codex |
| 3 | Remotion docs: System Prompt for LLMs | https://www.remotion.dev/docs/ai/system-prompt | LLM system prompt for generating Remotion code |
| 4 | Karen Spinner: I built a video pipeline. It failed. (Mar 2026) | https://wonderingaboutai.substack.com/p/i-built-a-video-post-production-pipeline | Canonical writeup of agent-orchestrated > pre-built-CLI pattern, FFmpeg + Puppeteer + Nano Banana stack |
| 5 | Medium: Claude Code + Remotion 2026 Stack | https://medium.com/aimonks/claude-code-remotion-the-2026-developer-stack-that-turned-video-production-into-a-git-commit-5ab44422b2d7 | "Video as Git commit" framing, 2026 stack overview |
| 6 | Sabrina.dev: Claude AI + Remotion Tutorial | https://www.sabrina.dev/p/claude-just-changed-content-creation-remotion-video | Step-by-step tutorial using the official Remotion skill |
| 7 | Reddit r/google_antigravity: 48-second 100% AI motion graphic | https://www.reddit.com/r/google_antigravity/comments/1rc1mal/i_built_this_48second_motion_graphic_promo_video/ | Concrete proof of end-to-end Antigravity + Gemini + Claude + Remotion pipeline |
| 8 | Reddit r/MotionDesign: Tried AI motion graphics with Remotion | https://www.reddit.com/r/MotionDesign/comments/1s5v3gi/tried_aigenerated_motion_graphics_with_remotion/ | Designer-community reaction, ergonomics critique |
| 9 | Qubika: Dynamic video creation with React and Remotion | https://qubika.com/blog/dynamic-video-creation-react-remotion/ | Engineering perspective on parameterized templates |
| 10 | LinkedIn: Adam Goodyer AI Video Editing Pipeline | https://www.linkedin.com/posts/adam-goodyer_ive-edited-every-video-on-my-youtube-channel-activity-7455012805759619072-v4I_ | Real-world solo-operator workflow report |
| 11 | Cutback Video: Hyperframes vs Remotion vs Selects | https://cutback.video/blog/hyperframes-vs-remotion-vs-selects | Comparison with adjacent 2026 AI video tools |

## Citation Log
- https://www.remotion.dev/
- https://www.remotion.dev/docs/ai/coding-agents
- https://www.remotion.dev/docs/ai/system-prompt
- https://github.com/remotion-dev/remotion
- https://yuv.ai/blog/remotion
- https://wonderingaboutai.substack.com/p/i-built-a-video-post-production-pipeline
- https://medium.com/aimonks/claude-code-remotion-the-2026-developer-stack-that-turned-video-production-into-a-git-commit-5ab44422b2d7
- https://www.sabrina.dev/p/claude-just-changed-content-creation-remotion-video
- https://www.reddit.com/r/google_antigravity/comments/1rc1mal/i_built_this_48second_motion_graphic_promo_video/
- https://www.reddit.com/r/MotionDesign/comments/1s5v3gi/tried_aigenerated_motion_graphics_with_remotion/
- https://qubika.com/blog/dynamic-video-creation-react-remotion/
- https://www.linkedin.com/posts/adam-goodyer_ive-edited-every-video-on-my-youtube-channel-activity-7455012805759619072-v4I_
- https://cutback.video/blog/hyperframes-vs-remotion-vs-selects
- https://growwstacks.com/blog/ai-agents-remotion-video-creation/
