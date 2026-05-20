# SNAPSHOT_01 — After Free Thinker opening + Explorer 5-report research sweep

**Time:** 2026-05-20 16:50 Houston
**Session:** ideation-landing-ad-videos-20260520-162219
**Trigger:** Arbiter requested durable capture; first major evidence drop just landed.

---

## State of play

The session opened on the seed's framing — "build 1–2 landing-page ad videos for a B2B AI-agents service, leaning cartoon, surfaced via an 'Is this you?' section." Two substantive moves have happened since: Free Thinker proposed an opening volley of stylistic/framing directions, and Explorer ran a parallel research sweep that delivered five reports challenging the seed's core assumption that video is the right deliverable at all. The session is now at a decision point: defend video with narrow win conditions, pivot to a non-video hero, or hybridize.

## Active threads

### Thread A — Free Thinker's opening directions (STYLE, FRAME, TONE)

**Status:** Active but **documentation gap.** Free Thinker broadcast around 16:25 with "wild stylistic and framing ideas." The verbatim never reached Writer's context — only team-lead's secondhand summary. Per the seed's explicit asks, the expected territory includes stylized 3D, mixed media, lo-fi, kinetic typography hybrids, vector/SVG, paper-cutout, motion-design abstract, and reframings like future-self / day-in-the-life / customer-facing / agent-as-narrator / counterfactual-without-AI. The specific proposals Free Thinker actually surfaced need to be re-broadcast directly to Writer or reconstructed by team-lead, otherwise this thread cannot be turned into an idea brief later. Flagged as the highest-priority recovery item.

### Thread B — Evidence-driven re-framing of the deliverable (RISK, UX, PIPELINE, STYLE, LENGTH)

**Status:** Active, pending Grounder reaction. Explorer's five reports between 16:27 and 16:34 collectively force the question: is video even the right deliverable? The dominant finding is the Digital Applied 2,000-page A/B study (Q4 2025–Q1 2026): video-autoplay hero is -7% conversion median against a plain image control on B2B SaaS pages — the second-worst hero pattern, beaten only by generic stock photography. Animated illustration / Lottie hero is +5%. Single-stat hero ("127x faster") is +18%. Video only breaks even if LCP holds under 1.5s, which most teams fail. The seed's "Is this you?" quiz-routed hero has zero direct A/B evidence and adds friction before the value prop is visible.

The other four reports reinforce the same conclusion from different angles. B2B SaaS 2026 conventions have moved away from standalone hero video toward product-UI + bold expressive type + micro-motion (Linear, Stripe, Vercel, Anthropic). "AI ad about AI" trope fatigue is documented in designer communities. The category the operator intuitively wants ("cartoon") is best renamed internally to "illustrated motion design" — Stripe Press, Headspace, Mailchimp, Slack-old-explainers are the credibility anchors. The Veo + Kling + Nano-Banana + Remotion stack makes parameterized iteration cheap (~$20–30 for 50 variants) IF video stays.

## Interesting (Arbiter-confirmed)

None yet — Arbiter has not issued first verdicts.

## Abandoned threads

None yet.

## Cross-cutting tension

> **The seed assumes "build a hero video." The evidence says video-autoplay hero is, on median, the second-worst B2B landing-page hero pattern. Lottie/SVG animated illustration outperforms it. Single-stat hero outperforms both. Is video even the right deliverable?**

This tension touches every question category — most acutely RISK, STYLE, PIPELINE, UX. The five plausible alternative deliverables on the table:

1. **Lottie/SVG-animated hero** (+5% measured lift, no LCP penalty)
2. **Single-stat hero** (+18% measured lift, no video at all)
3. **Interactive product walkthrough** (Guideflow/Navattic style) — outperforms passive video for B2B
4. **Video, but only with narrow win conditions** — LCP <1.5s, muted autoplay + visible play-to-unmute, first-5s value prop, animation-not-live-action, 30s, and ideally *below* the fold not as the hero
5. **Hybrid** — Lottie/SVG hero above the fold + Remotion-composed scenario videos accessible via "Is this you?" lower on the page

## Open question this raises for Grounder

Is video even the right deliverable? If yes, under what narrow win conditions does it not cost conversion? If no, which non-video alternative best preserves the spirit of the seed (the "is this you?" recognition moment, the discovery hook for AI-naive owners)? Grounder should also weigh whether the seed's "is this you?" framing itself survives — the conversion evidence suggests a single well-targeted message often outperforms a quiz that adds a click before content.

## Emerging patterns the Writer is noticing

- **"Stop calling it cartoon."** The operator's anxiety about cartoon-reading-childish is largely a vocabulary problem. The B2B-credible category exists, it's just called "illustrated motion design" — anchored by Stripe Press, Headspace, Slack, Mailchimp, Linear, Anthropic. Internal vocabulary swap de-risks the style decision immediately.
- **"Video is a forcing function for LCP."** Any deliverable that includes hero video forces a hard engineering constraint (sub-1.5s LCP). Choosing a Lottie/SVG-led hero sidesteps the engineering problem while gaining +5% conversion. PIPELINE and RISK are deeply entangled.
- **"Parameterized templates change the UX question."** Remotion's batch-render-many-variants capability turns "Is this you?" from a binary UX choice (autoplay vs quiz) into a segmentation strategy — server-side variant selection by visitor ICP, with the same composition rendered N ways. The "Is this you?" decision may be invisible to the user.

## Recommended next moves (for Arbiter's consideration)

1. **Recover Thread A.** Ask Free Thinker (or team-lead's notes) to re-broadcast the verbatim opening proposals directly to Writer so they're on disk.
2. **Direct Grounder to react to Thread B's tension.** Grounder's seed-prescribed pushback list already includes "AI ad about AI trope fatigue" and "whether two videos outperform one well-targeted one with strong copy" — the evidence is now in hand to support a strong position. Grounder should explicitly take one: defend, pivot, or hybridize.
3. **Have Free Thinker explore the non-video reframings** in light of evidence — particularly the single-stat-hero pattern and the interactive-walkthrough pattern. Both might be more interesting than another illustrated-motion video.
