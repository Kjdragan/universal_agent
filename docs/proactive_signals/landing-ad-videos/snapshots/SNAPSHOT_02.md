# SNAPSHOT_02 — After Arbiter's first verdicts

**Time:** 2026-05-20 17:30 Houston
**Session:** ideation-landing-ad-videos-20260520-162219
**Trigger:** Arbiter issued first INTERESTING / NEEDS-MORE / NOT-INTERESTING verdicts after FT and Grounder's two-turn exchange.

---

## State of play

Eight substantive exchanges in, the dialogue has produced three layered conceptual axes — operator's cartoon-vs-photoreal → Free Thinker's produced-vs-leaked → Grounder's **performance-vs-evidence** — and ratified the third as the operating design constraint for the session. The original 14-idea inventory (10 styles + 4 UX mechanics) has been triaged. Three ideas are going to convergence as Arbiter-confirmed INTERESTING; three are eliminated as NOT-INTERESTING; one is fully abandoned; two NEEDS-MORE threads remain open and will shape final brief content.

Most importantly, the seed's central premise (two videos surfaced via an "Is this you?" routing mechanic) has been challenged on two fronts: empirically (Explorer's conversion data showing video-autoplay heroes lose -7%) and structurally (Grounder's argument that one undeniable product-evidence video makes routing UX redundant). The Arbiter's verdict carries this re-framing forward — one of the three INTERESTING items is literally `IDEA_seed-dissolves`.

## Interesting (Arbiter-confirmed, going to convergence)

### IDEA_product-as-evidence-hero
Free Thinker's FT-1 "spreadsheet-cam" reframed through Grounder's product-as-evidence refinement. The style decision (cartoon vs photoreal vs anything else) dissolves — the operative question is no longer aesthetic but evidentiary: is the footage a real working session of the real product on real data, or is it cosplaying that? If real, the lo-fi quality is the proof. Composition layers can absorb FT-2 diptych (structural device) and FT-5 absence-of-AI counterfactual (as left-panel "your Tuesday without us"). This is the strongest concrete deliverable on the table.

### IDEA_seed-dissolves
A structural position rather than a video idea: "Is this you?" is unnecessary if the video itself is the recognition moment. A buyer recognises their own spreadsheet hell in the first three seconds, which eliminates the click-before-content friction the seed's quiz-routing would introduce. The seed's two-video + branching-UX architecture is replaced by one undeniable product-cam hero + sharper one-line headline copy + below-fold deepening (longer narrated walkthrough, plus optionally a demoted recognition router pointing to case studies). The seed's COUNT and UX question categories both resolve here.

### IDEA_offline-prerendered-personalization
A salvage of FT's UX-C "agent-builds-your-ad," recast away from live agent inference (which dies on latency + AI-ad-about-AI trope + competition with conversion). The salvaged idea is server-side variant selection: infer industry from IP/UTM at request time, serve one of 20–50 Remotion-batch-rendered variants generated offline, with the same product-cam composition but swappable copy + dashboard numbers + industry-specific spreadsheet columns. Invisible to the user. Pairs naturally with IDEA_product-as-evidence-hero — same composition, parameterised content. Makes A/B testing economical.

## Needs more conversation (still open)

### NEEDS-MORE_1 — Discovery-audience gap
The seed had two scenarios: "Amazing world of AI" discovery hook (for owners who don't know what's possible) and "Struggling business owner" empathy hook (for owners who already recognise the pain). The product-as-evidence frame works beautifully for the second audience but isn't obviously fit for the first — a buyer who's never used an AI agent may not know what to look for in a product-cam video, and the lo-fi evidentiary lo-fi may land as inscrutable rather than persuasive. Open question: does the discovery audience get a separate brief (the only justified second video, per Grounder's test of "two genuinely different personas, not two emotional framings of one"), or does the discovery moment happen lower on the page after the recognition-hero converts the already-aware audience first? This shapes the COUNT category and may produce a fourth INTERESTING idea.

### NEEDS-MORE_2 — FT-1-vs-FT-6 choice + operator-capability constraint
Grounder and FT haven't actually settled the explainer slot. FT wants FT-6 kinetic typography over real B-roll. Grounder counters with a longer narrated walkthrough of the same product session as the hero, with operator voiceover, to preserve one coherent visual language across the page. The decision hinges on an operator-capability constraint the dialogue has been silent on: **does the operator currently have real product/spreadsheet/agent footage to record, or will the agent be running on synthetic demo data at recording time?** If synthetic, the entire evidence-claim collapses and FT-6 (or a hybrid) becomes the right answer by default. One more round needed — possibly an operator clarification.

## Not interesting (eliminated)

| Item | Why |
|------|-----|
| **FT-7 Wes Anderson** | Most-imitated visual language of last 5 years; reference doesn't land for B2B owners 40–55; premium cost for "young creative tried too hard" aesthetic. |
| **FT-9 agent-as-narrator** | AI narrating its own ad reads as Clippy/Copilot. Directly triggers the operator's "childish-coded" anxiety. |
| **FT-10 90s infomercial parody** | Ironic = produced by definition; joke worn out by Squarespace / Cards Against Humanity / Liquid Death / every DTC; punchline is AI-ad-about-AI in costume. |

## Abandoned

**FT-4 Customer POV** — buyer shops for their own operational relief, not their customer's experience; puts the agent two steps removed from proof. Salvageable only as a testimonial pull-quote, not as a video.

## Predicted three idea briefs

1. **BRIEF_product-as-evidence-hero.md** — the 30-second muted-autoplay product-cam hero, with FT-2 diptych and FT-5 counterfactual as optional composition layers. Specifies the evidence threshold (real product, real data, real agent latency) and the page treatment (muted autoplay loop, "watch with sound" button, sub-1.5s LCP requirement, scrolls to a longer explainer).

2. **BRIEF_seed-dissolves.md** — the structural recommendation against the seed's two-video + "Is this you?" routing. Specifies what replaces it: a one-line recognition headline above the hero, optional below-fold "pick your pain" router demoted to case-study deepening (per Grounder's demoted survivor verdict on UX-A), and the rule that a second video is justified only when it represents a genuinely different persona, not an emotional framing variation.

3. **BRIEF_offline-prerendered-personalization.md** — the personalisation pattern that makes "Is this you?" invisible. Server-side variant selection by IP/UTM, 20–50 Remotion-batch-rendered variants ($5 in Lambda time per the canonical Remotion + Nano-Banana + Kling stack Explorer documented), product-cam composition parameterised by industry/business-size. Includes the open question of whether this is built day-one or after a single-hero baseline is shipped.

If NEEDS-MORE_1 (discovery audience) resolves toward "yes, separate brief," a fourth brief — likely a 60s explainer for the discovery audience using FT-6's kinetic typography with the operator's own B-roll — will join the list. If NEEDS-MORE_2 (operator capability) reveals that real footage isn't currently available, BRIEF_product-as-evidence-hero will gain a Phase 0 prerequisite (instrument the operator's actual workflow and capture real footage before any production work begins).

## Emerging patterns the Writer is now confident in

- **Three-axis stack as the design language of the session.** Cartoon-vs-photoreal (operator) → produced-vs-leaked (FT) → performance-vs-evidence (Grounder). Each layer dissolved the previous one's apparent disagreements. The vision document should open with this stack as the conceptual frame — it explains why the operator's original style anxiety was the right question asked at the wrong altitude.

- **Composition layers vs aesthetic styles vs UX mechanics are different axes that the dialogue conflated early.** Grounder's "FT-2 is structural device, not style" was the unlocking move. A brief can stack one of each into a coherent idea (e.g. evidence-style + diptych-composition + counterfactual-narrative-structure + no-routing-UX) without forcing false either/ors. The vision document will need to make this explicit so the operator doesn't read the three briefs as "pick one."

- **The seed's framing held up halfway.** The "Is this you?" *language* survives as a one-line headline; the "Is this you?" *mechanic* (parallel autoplay or quiz) does not. The "two scenarios" instinct survives only if the two are different personas, not different emotional framings. Honour the operator's instinct where it carried information; correct it where it carried inherited convention.

## Recommended next moves (for Arbiter)

1. Push FT and/or Grounder on **NEEDS-MORE_2 (operator-capability constraint)** first. This is the cheapest to resolve and may require nothing more than an operator clarification. It de-risks the most concrete brief.
2. Push on **NEEDS-MORE_1 (discovery-audience gap)** second. This determines whether the final deliverable is three or four briefs.
3. Once both resolve, signal convergence to Writer for brief-fill. Skeletons are pre-loaded.
