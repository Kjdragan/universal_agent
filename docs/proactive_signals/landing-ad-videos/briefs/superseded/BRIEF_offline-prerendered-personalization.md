# Idea Brief: Offline Pre-Rendered Personalisation (Invisible "Is this you?")

**Status:** Interesting (Arbiter-confirmed) — SKELETON, awaiting NEEDS-MORE resolution
**Brief produced by:** Writer
**Based on idea report(s):** Thread A (UX-C agent-builds-your-ad), Thread C (Grounder's salvage of UX-C as personalisation)

---

## Summary

_[Pre-load.]_

Instead of asking the visitor "Is this you?" or generating an ad live in front of them, the landing page silently serves one of 20–50 pre-rendered variants of the hero based on inferred industry, business size, and pain-point from IP/UTM/referrer data. Each variant shares the same product-cam composition from BRIEF_product-as-evidence-hero but with parameterised content: industry-specific spreadsheet columns visible on screen, swappable dashboard numbers, persona-tailored headline copy. Variants are batch-rendered offline via Remotion + Nano Banana Pro + Veo/Kling for ~$5 of Lambda time per 50 variants. The personalisation is invisible to the user — the visitor never sees a quiz, never waits for inference, never parses the page as an "AI builds your ad" gimmick.

_[Add: relationship to IDEA_product-as-evidence-hero (same composition, parameterised); whether this is v1 or shipped after a single-hero baseline; A/B testing economics.]_

## What Makes This Interesting

_[Pre-load.]_

The salvage move converts what was originally a virality-optimised gimmick (UX-C agent-builds-your-ad-live, killed for latency + AI-ad-about-AI trope + competing with conversion) into a B2B-credible personalisation pattern with measurable conversion economics. The same architectural insight survives — "different visitors should see different ads" — but the visible mechanism that triggered the trope-fatigue concerns disappears. The Remotion parameterised-template + Lambda batch-render stack documented in Explorer's research makes this economical in a way no other personalisation pattern in 2026 matches.

## Lineage

### Origin
_[Fill.]_ Free Thinker's UX-C in his opening volley: "Bottom-right corner: a chat composer that says 'watch me build your ad.' Owner types two sentences. 45 seconds later, a 20-second personalised video plays in the hero. The page IS the demo. The version that goes viral."

### Key Turns

1. **Free Thinker's UX-C as his "virality pick"** — _[fill: the original ambitious version, generation-live]_
2. **Grounder's kill-with-salvage in round 6** — _[fill: latency + AI-ad-about-AI in purest form + competes with conversion → but recast as "personalisation," it survives as a different idea, different name]_
3. **Grounder's "different beast, much stronger" framing** — _[fill: forced the distinction between live-generation (kill) and offline-pre-rendered (keep)]_
4. **Explorer's Remotion + Lambda batch-render economics** — _[fill: 50 variants for ~$5 in 15 minutes makes this economical in a way previously unimagined]_
5. **Arbiter's INTERESTING verdict** — _[fill]_

### Variations Explored

| Variation | What It Was | Why It Was Set Aside | Worth Revisiting? |
|-----------|------------|---------------------|-------------------|
| _[UX-C live "agent builds your ad" in front of visitor]_ | _[45s generation time visible to user]_ | _[Latency kills conversion; AI-ad-about-AI in purest form; competes with conversion goal]_ | _[No]_ |
| _[Per-visitor real-time inference (no pre-render)]_ | _[Generate variant at request time]_ | _[Same latency problem at smaller scale; cost-per-visitor wrong economics]_ | _[No]_ |
| _[Server-side variant selection by manual quiz]_ | _[Visitor picks industry; serves matching variant]_ | _[Reintroduces the click-before-value-prop problem from BRIEF_seed-dissolves]_ | _[No]_ |
| _[A/B variant testing without personalisation]_ | _[Same 50 variants, randomly assigned]_ | _[Useful adjacent capability but not what this brief is about]_ | _[Yes — likely a precursor to personalised serving]_ |

## The Free Thinker's Vision

_[Fill from FT's verbatim: "The page IS the demo. The version that goes viral."]_

## The Grounder's Take

_[Pre-load.]_

### Why This One
- _[Grounder's verbatim: the live version dies on latency / trope / conversion competition; the pre-rendered version is a "different beast, much stronger" — same architectural insight, no visible gimmick.]_

### How It Connects to the Brief
- _[Resolves the seed's "Is this you?" question in a third way (after BRIEF_seed-dissolves resolves it negatively as a mechanic): the recognition is real and personal, but invisible. The seed's instinct that different visitors deserve different framings survives — the mechanism just becomes server-side and silent.]_

### Where It Could Lose People
- _[Risk: paralysis. Building 50 variants before validating one. Build a single product-cam hero first, prove it works, then add personalisation as v2.]_
- _[Risk: variants drift in quality. Need a content guardrail — every variant must clear the same evidence bar as the baseline.]_
- _[Risk: ICP inference is noisy. IP→industry is wrong often enough that visitors may see a mismatched variant; needs a fallback "generic" variant.]_

## What the Arbiter Flagged

_[Fill on convergence.]_

## Open Questions

1. **v1 or v2?** Ship a single product-cam hero first and add personalisation as v2 once baseline conversion is measured? Or build the parameterised template from day one so the first hero IS a variant?
2. **What's the inference data?** IP→industry (Clearbit-tier) vs UTM source vs referrer vs explicit cookie from a prior visit. Each has different accuracy and cost.
3. **Number of variants — 20? 50? 200?** Diminishing returns curve unknown. Start at 5 ICP segments and grow?
4. **Fallback variant** for cases where inference fails or is below confidence threshold — is it the "generic" version, or the highest-converting variant from data?
5. **Variants of what, exactly?** Headline copy + dashboard numbers + visible spreadsheet columns is one combinator; full per-industry product-cam recordings is another. Cost and quality tradeoff.

## Next Steps (If Pursued)

- _[Ship single-hero baseline first. Measure conversion. Then graduate to personalisation.]_
- _[Define 5 initial ICP segments with the operator.]_
- _[Build one parameterised Remotion template that can render all 5 baseline variants.]_
- _[Stand up IP/UTM→segment inference at the edge.]_
- _[Add fallback variant + confidence threshold.]_
