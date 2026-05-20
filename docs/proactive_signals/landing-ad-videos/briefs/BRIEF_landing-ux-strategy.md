# Idea Brief: IDEA_03 — Landing-Page UX Strategy

**Status:** Interesting — Arbiter-confirmed, convergence-signal received 2026-05-20 18:15. Consolidates three Grounder-authored sub-threads into one strategic-bet brief.
**Brief produced by:** Writer
**Based on:** Thread C (Grounder's counter-provocation that the seed dissolves), Thread D (UX-collapses-into-STYLE pattern, demoted UX-A, salvaged UX-C), Thread E + addendum (Grounder R3 — operator should evaluate this as its own bet, not bury it as an assumption).

**Supersedes earlier scratch skeletons:** `superseded/BRIEF_seed-dissolves.md` and `superseded/BRIEF_offline-prerendered-personalization.md` (kept for lineage).

---

## Idea

The landing-page UX architecture that surrounds IDEA_01 and IDEA_02. This brief is *not* a video — it is the set of structural decisions about how the videos are surfaced, what UI furniture replaces the seed's "Is this you?" routing mechanic, and when (if ever) personalisation graduates from a fallback to a destination. The deliverable is a strategic bet the operator evaluates explicitly, not an assumption that gets buried inside the video briefs.

Grounder's editorial read (R3 reshape Q2) gives the cleanest single-paragraph framing of the whole brief: *"Two-cut routed system is the right destination. v1 ship is one cut, chosen by channel-mix heuristic. Second cut and routing land together in v1.5 once outbound channels are live and tagged."* That's the spine; the three components below are how it lands in practice.

Three components, listed in v1-to-v1.5 sequencing order:

### Component 1 — The seed dissolves

The seed's "Is this you?" routing UX — parallel autoplay, quiz, or branching narrative — is replaced by a one-line headline above IDEA_01's hero. *"Is this you?"* survives as **language**, dies as **mechanic**. Two independent lines of argument support this:

- *Empirical.* Explorer's research found zero direct A/B evidence for quiz-routed heroes on B2B SaaS landing pages, and substantial evidence that adding a click before the value-prop is visible drags conversion (Hotjar / CXL data: "the first interaction must be the conversion"). Quiz routing is asking the visitor to do work before they have seen anything worth doing work for.
- *Structural.* Grounder's counter-provocation from Thread C: *"If spreadsheet-cam is product-as-evidence, the buyer recognises their pain in 3 seconds. The whole branching/quiz UX solves a problem the right video doesn't have."* The video is the recognition moment; the quiz duplicates work the video does better.

The seed's instinct that visitors deserve differentiated treatment survives. The mechanic that delivers it changes — from a visible quiz to invisible server-side routing (component 3 below).

The page architecture at v1: a one-line "Is this you?"-style headline → IDEA_01 hero (chosen cut, per the channel-mix heuristic below) → IDEA_02 deepening, if path resolves to one → demoted UX-A case-study router → social proof / pricing / CTA. No visible quiz. No parallel autoplay. No branching narrative.

### Component 2 — Demoted UX-A: below-fold case-study router

UX-A "recognition quiz" survives at radically reduced scope per Grounder's "demoted survivor" verdict from Thread D. Below-fold, it becomes a *"pick your pain"* router that points to written 60-second case studies — not videos competing for hero attention. The original recognition-quiz energy is honoured; the conversion-cost is removed. Lives below IDEA_01's hero (and below IDEA_02's deepening if Path B ships).

Critical for this brief: the router fails if there is no content for it to point at. Authoring 3–5 case studies (one per primary ICP segment) is a v1 prerequisite, not a v2 nicety. Without content, the router is a broken affordance before launch.

### Component 3 — UX-C: v1 is right-sized, v1.5 unlocks when channels and data exist

Per Arbiter's locked positions, UX-C (server-side variant selection / invisible personalisation) operates at two levels. Framing matters here: **v1 is not "incomplete work to upgrade later" — it is the correctly-sized first ship for an operator without traffic data. v1.5 unlocks when outbound channels are live and tagged data exists.** That is correct sequencing, not deferred scope.

**v1 — channel-mix heuristic as the routing mechanism.**
For solo / pre-launch operators with no analytics, the operator's go-to-market channel mix becomes the implicit routing signal:

| Channel category | Implied audience | Cut served at v1 |
|---|---|---|
| Partner referrals, warm intros, founder-network outreach, niche-community DMs, "we built this for you" cold lists | Audience A dominant | IDEA_01 recognition cut |
| Content marketing on broad AI / automation terms; paid search on "what can AI do for my business" / "AI tools small business"; LinkedIn outreach with generic AI-savings hook; cold outreach with no pain signal | Audience B dominant | IDEA_02 discovery cut (Path A, A-bridge, or B depending on IDEA_02's path selection) |

At v1 there is one cut per page. The operator picks which cut to ship based on which channel mix dominates their acquisition. No personalisation; no routing; one page, one cut.

**v1.5 — both cuts shipped, invisible UX-C routing live.**
Once IDEA_01 and IDEA_02 are both produced and a baseline conversion rate exists on the v1 single-cut version, UX-C is promoted from fallback to destination. Server-side variant selection: infer industry / audience-disposition from IP + UTM + referrer + cookie signals; serve the matching cut. The visitor never sees a quiz. The page IS the personalisation; the mechanism is invisible.

**Grounder's three risks for UX-C (must be flagged in the operator handoff):**

1. **Latency.** Inference must happen at edge or before-first-paint or it shows the wrong cut briefly then flickers to the right one — worse than no personalisation.
2. **ICP inference quality.** IP → industry / firmographics is noisy. Clearbit-tier signals are ~80% accurate on the company-domain match and substantially worse on individual visitor segmentation. A confidence threshold is required; below it, serve the higher-volume cut as a generic fallback.
3. **No pre-launch testability.** UX-C can't be A/B tested before there's enough traffic to populate the segments. Don't ship v1.5 against a hypothetical performance lift; ship it against a measured v1 baseline.

**Promotion criteria from v1 to v1.5:** ship single-cut v1; measure conversion against a Lottie/SVG control hero (Explorer's +5% baseline) over 4–6 weeks; promote to v1.5 only if (a) the single-cut version meets or beats the Lottie control AND (b) the segmentation traffic mix justifies the inference effort (i.e. neither audience is <15% of qualified traffic, which would make routing wasted complexity).

## Lineage

### Origin

This brief did *not* originate from a Free Thinker stylistic idea. Three independent Grounder threads converged on it:

- Grounder's counter-provocation in Thread C: *"We don't need 'Is this you?' at all."*
- Grounder's demoted-survivor verdict on UX-A in Thread D: *"Radically demoted. Not hero. Maybe below-fold 'pick your pain' router to relevant 60s case study."*
- Grounder's salvage-with-rename of UX-C in Thread D: *"If pre-rendered, that's not 'agent builds your ad,' that's personalisation. Different beast, much stronger."*

Grounder's addendum at round 12 surfaced this as an explicit divergence from Arbiter's proposal to bury the strategy as a vision-document assumption: *"Kept as own brief because it's a real strategic bet operator should evaluate explicitly, not bury as an assumption."* That framing is what the Arbiter convergence signal at round 14 ratified.

### Key Turns

1. **Original seed (round 0).** Two videos surfaced via an "Is this you?" mechanic, near the top of the landing page. Mechanic unspecified.
2. **Explorer's hero-video conversion research (rounds 2 in research).** Quiz-routed heroes have zero direct A/B evidence; adding a click before value drags conversion per CXL/Hotjar data. The seed's mechanic was unsupported by 2026 evidence.
3. **FT-1 product-as-evidence reframing (round 4).** Made the routing redundant by collapsing the recognition moment into the video itself.
4. **Grounder's counter-provocation (round 6, Thread C).** *"If FT-1 works as product-as-evidence, the buyer recognises their pain in 3 seconds. The whole branching/quiz UX solves a problem the right video doesn't have."*
5. **Grounder's "two genuinely different personas, not two emotional framings" rule (round 7, Thread D).** When a second video is justified at all: only when the two audiences are cognitively distinct, not when they're two emotional shadings of the same audience.
6. **Grounder's demoted-survivor verdict on UX-A (round 7).** Quiz survives, but only as below-fold case-study router pointing to written deepening, not as hero-level routing.
7. **Grounder's kill-with-salvage of UX-C (round 7).** Live "agent builds your ad" dies (latency + AI-ad-about-AI + competes with conversion); offline pre-rendered variant selection survives. *"Different beast, much stronger."*
8. **Grounder's three risks for UX-C surfaced in Thread E (round 11).** Latency, ICP inference quality, no pre-launch testability. These risks are why UX-C is v1.5, not v1.
9. **Grounder's addendum (round 12, Thread E).** Explicitly proposed this as a standalone brief: *"Kept as own brief because it's a real strategic bet operator should evaluate explicitly, not bury as an assumption."*
10. **Arbiter convergence signal (round 14).** Locked the v1-fallback + v1.5-destination sequencing for UX-C; ratified the demoted UX-A below-fold; ratified the seed-dissolves headline-only position. Codified the channel-mix heuristic.

### Variations Explored

| Variation | What It Was | Why It Was Set Aside | Worth Revisiting? |
|-----------|------------|---------------------|-------------------|
| Original seed: two videos + parallel autoplay | Both videos play side-by-side in hero | Double LCP cost, double production, no A/B evidence, viewer choice-paralysis | No |
| Original seed: two videos + quiz routing | 3-question quiz routes to one video | Zero direct A/B evidence; adds click before value prop; CXL/Hotjar data on first-interaction-must-be-conversion | No as hero; survives only as demoted below-fold case-study router (component 2) |
| UX-B scrubber | Slider re-renders the same ad with different B-roll + dashboard numbers | Niche; lives only if scrub axis is "minutes of work saved" — product math made interactive | Maybe as a v2 personalisation surface, not v1 or v1.5 |
| UX-C live "agent builds your ad" | Chat composer + 45s live generation in hero | Latency + AI-ad-about-AI trope + competes with conversion goal | No live; survives offline as UX-C v1.5 |
| UX-D scrollytelling | Scroll-triggered reveal of beats | Redundant with one-shot product-cam hero | Maybe for below-fold IDEA_02 deepening if Path B ships |
| UX-C v1 (no v1-fallback, ship personalisation day one) | Build 20–50 variants offline before measuring single-cut conversion | Builds complexity before validating the unit case; engineering time burned if single-cut doesn't meet baseline | No — promotion criteria preserve this learning |

## Free Thinker's Vision

FT's direct contribution to this brief is the four UX ideas from the opening volley — UX-A recognition quiz, UX-B scrubber, UX-C agent-as-the-page, UX-D scrollytelling. Most were demoted or killed; the architectural insight underneath UX-C survives.

FT's most ambitious version (UX-C, round 4): *"Bottom-right corner: a chat composer that says 'watch me build your ad.' Owner types two sentences. 45 seconds later, a 20-second personalised video plays in the hero. The page IS the demo. The version that goes viral."*

That version doesn't survive — latency and trope-fatigue killed it. But the architectural insight — *different visitors should see different ads* — survives in component 3's invisible UX-C v1.5 destination. Grounder's recasting from "agent builds your ad" to "personalisation" preserved what FT was reaching for and stripped the parts that fought conversion.

FT-R4's "Is this you?" position aligns with this brief's seed-dissolves component: *"Routing invisible via UX-C — server picks entry cut from ICP/UTM/firmographic signals. 'Is this you?' dissolves entirely into segmentation."*

## Grounder's Honest Read

### Why this one

From Thread C: *"If spreadsheet-cam is product-as-evidence, the buyer recognises their pain in 3 seconds. The whole branching/quiz UX solves a problem the right video doesn't have."*

From Thread D on UX-A: *"Radically demoted. Not hero. Maybe below-fold 'pick your pain' router to relevant 60s case study."*

From Thread D on UX-C: *"If pre-rendered, that's not 'agent builds your ad,' that's personalisation. Different beast, much stronger."*

From Thread E addendum: *"Kept as own brief because it's a real strategic bet operator should evaluate explicitly, not bury as an assumption."*

Grounder authored most of the substantive moves in this brief and explicitly argued for keeping it visible to the operator rather than absorbing it as a hidden vision-document assumption. That argument is the brief's reason for existing.

### How it connects to the brief

Resolves the seed's COUNT and UX question categories simultaneously. Preserves the operator's "Is this you?" *language* (it survives as a headline) while correcting the *mechanic* (it dies as a quiz). Honours FT's instinct that visitors deserve differentiated treatment by routing it to server-side personalisation rather than visible quiz friction. The v1 fallback / v1.5 destination split addresses the operator's product-maturity reality (no traffic data + no measured baseline at launch → ship channel-mix fallback) while preserving the path to the architecturally ambitious version.

### Where it could lose people

- **Operator reads "seed dissolves" as a rejection of their original instinct.** The brief must frame this as honouring the insight while correcting an inherited landing-page convention. The "Is this you?" language survives prominently as the page's headline.
- **v1.5 personalisation gets built before v1 baseline measurement.** Building 20–50 Remotion variants without validating that one cut works converts engineering time to waste. The promotion criteria are non-negotiable.
- **Demoted UX-A below-fold router becomes a forgotten orphan.** Without an owner and a case-study authoring cadence, the router is a broken affordance. Treat the case-study content as a v1 deliverable, not v2.
- **Channel-mix heuristic gets ignored once analytics exist.** The heuristic is a v1 fallback. Once tier-1 analytics exist for the operator, the diagnostic in IDEA_02 (and the v1.5 promotion criteria) should take over. The heuristic is not the destination.
- **UX-C v1.5 risks (latency / inference quality / no pre-launch testability) get filed and forgotten.** Each risk has mitigations spelled out in component 3; they need to be explicit operator-handoff items, not buried in this section.

## Arbiter's Evaluation

Flagged INTERESTING during round 8 first-verdicts and ratified at the convergence signal in round 14 with three specific structural decisions: (a) the three components consolidated into one brief, not three separate ones, so the operator evaluates them as one strategic bet; (b) UX-C upgraded from "Q4 nice-to-have" to "v1 fallback + v1.5 destination" so the brief carries a concrete sequencing the operator can plan against; (c) Grounder's three UX-C risks (latency / inference quality / no pre-launch testability) explicit in the brief rather than hidden, because the v1-fallback model only works if those risks shape the v1.5 promotion criteria. The crossing-convergence pattern noted in the vision document is what made this brief's structural shape obvious — when FT-R3 and Grounder-R3 independently arrived at "invisible segmentation routes the audience-specific cut," the only remaining decision was whether to ship the routing in v1 (it can't be, per Grounder's three risks) or v1.5 (it can be, against a measured v1 baseline).

## Open Questions

1. **Page-level information architecture.** Final ordering of components on the page. Suggested at v1: one-line "Is this you?" headline → IDEA_01 hero (single chosen cut per channel-mix heuristic) → optional below-fold IDEA_02 deepening if Path B ships → demoted UX-A case-study router → social proof / pricing / CTA. Operator must agree on the order before any component ships.
2. **Channel-mix heuristic application.** Which channel category does the operator's actual go-to-market dominate today? This determines which cut ships at v1. Should be revisited every 30 days for the first 90 days; the answer may shift as acquisition mix evolves.
3. **v1 → v1.5 promotion threshold.** Conversion-lift threshold against the Lottie control before promoting to v1.5. Suggested: meet or beat Lottie's +5% baseline on the chosen cut for 4–6 weeks, plus both audiences ≥ 15% of qualified traffic. Operator may want a more or less aggressive bar.
4. **ICP inference data source for UX-C v1.5.** IP → industry (Clearbit-tier), UTM source, referrer, explicit-cookie from prior visit, or a combination weighted by confidence. Each has accuracy / cost / privacy trade-offs. Mitigates Grounder's risk #2 (inference quality).
5. **Fallback variant when inference fails or is below confidence threshold.** Generic / highest-converting / longest-tail? Specification needed before v1.5 ships. Mitigates Grounder's risk #2.
6. **UX-C v1.5 routing latency mitigation.** Where does the inference happen — edge worker (lowest latency, highest cost), origin server before first paint (medium), or client-side after first paint (lowest cost, flickers between cuts)? Mitigates Grounder's risk #1.
7. **Demoted UX-A case-study content sourcing.** Who authors the 3–5 case studies the router points to? Without content, the router is broken at launch. Without an owner, the content rots over time. Cadence: at least one fresh case study per quarter.
8. **What if v1 hits its goals on the single cut?** If the channel-mix-driven single-cut v1 outperforms target by a wide margin, is v1.5 still worth building? The operator should agree in advance that v1.5 is conditional on a specific signal (segmentation traffic mix justifying inference effort) and not an automatic next step.

## Next Steps (If Pursued)

- Lock the page-level information architecture with the operator (open question 1).
- Apply the channel-mix heuristic to the operator's actual acquisition data — produces the v1 cut choice that feeds IDEA_02's path selection.
- Ship IDEA_01 (or IDEA_02-Path-B if channel mix is recognition-skewed enough that IDEA_02 is the v1 ship) as the single-cut v1. Instrument: LCP, conversion, scroll depth, watch-with-sound rate, time-on-page, demoted-UX-A click-through.
- Author the 3–5 case studies the demoted UX-A router points to before launch. Without these, the router is broken at v1.
- Measure for 4–6 weeks against a Lottie/SVG control hero baseline. Track against the v1.5 promotion criteria.
- If baseline meets target AND both audiences ≥ 15%: start v1.5 personalisation pipeline (Remotion parameterised template, IP/UTM inference, fallback variant, latency tooling).
- If baseline misses target: revisit IDEA_01's operator-capability fork before adding personalisation complexity. The fix is upstream of UX, not in UX.
- Document Grounder's three risks (latency / inference quality / no pre-launch testability) as explicit operator-handoff items with mitigation owners.
