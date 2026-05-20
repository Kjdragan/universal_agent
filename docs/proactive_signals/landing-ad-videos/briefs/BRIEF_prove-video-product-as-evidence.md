# Idea Brief: IDEA_01 — The Prove Video (Audience A: Recognition)

**Status:** Interesting — Arbiter-confirmed, convergence-signal received 2026-05-20 18:15.
**Brief produced by:** Writer
**Based on:** Thread A (FT-1 spreadsheet-cam, FT-2 diptych, FT-R3 evidence-type reframe, FT-R4 evidence-vs-TT structural distinction), Thread C (Grounder's product-as-evidence refinement), Thread D (performance-vs-evidence axis), Thread E (Grounder R3 — operator-capability pushback, audience-scoped performance-vs-evidence walkback), NEEDS-MORE_2 resolved.

**Supersedes earlier scratch skeleton:** `superseded/BRIEF_product-as-evidence-hero.md` (kept for lineage).

---

## Idea

The hero deliverable for the landing-page **recognition audience** (Audience A — small/mid business owners who already feel the operational pain and just need to be shown that the agent works). A muted-autoplay 30-second screen recording of the real product doing real work on real data. The style decision dissolves into an evidence question: is the footage actually a working session of the real product on the operator's real data, or is it cosplaying that? The lo-fi quality is the proof. Per FT-R3, the brief specifies **evidence type** per video, not aesthetic — screen recording is one form; so is Loom-style narration over a dashboard, a customer voicemail played over a transcript, an email thread scrolling with the agent's reply highlighted, a CSV with a green-highlighted "auto" column. All feels-found, near-zero production cost.

Per FT-R4, IDEA_01 answers a specific cognitive question: **"Does this actually work?"** Past-tense, real product on screen, conversion lever is *trust*. This is structurally different from IDEA_02 (which answers "what does my life look like if I have this?"). The two briefs share **one capture** at the production-pipeline level but produce **two productions** — they are not "two cuts of one asset." The recognition cut leans on the screen-as-is. The discovery cut needs annotations, named-notification labels, time-jump composition, voiceover, and ambient-texture composition — real post-production work, not editing. IDEA_01's deliverable is the recognition cut and the raw screen-recording footage layer that IDEA_02 will also use.

### Operator-capability fork — sequential decision tree (not a parallel buffet)

NEEDS-MORE_2 turned out to be the load-bearing question for this brief. Grounder R3 elevated it from a brief footnote to a session-level concern: *"The seed describes operator's production stack, not their product stack."* The operator has Remotion, Nano Banana Pro, Veo/Kling, and media-processing skills (production stack); the seed does not establish whether they have a deployable agent workflow a customer is actually running today (product stack). This brief therefore ships with a four-step decision tree the operator must walk top-to-bottom, asking and answering one linear question at a time — not a buffet of options to pick from. Walk the questions in order; stop at the first **yes**.

**Q1 — Is there a working product surface a customer could use today?** If yes → ship **Step 1: Real.** Actual screen recording of the real product on a real customer's data, with real agent latency. Strongest evidence claim. Requires consented usable footage from a paying customer. This is what FT-1 originally described and what Grounder named as "strongest by a mile."

**Q2 — If Q1 is no: is there an internal agent workflow you use to run your own business?** If yes → ship **Step 2: Internal-tool.** Screen recording of the real product on the operator's own business data (the operator running the agent on themselves). Evidence claim is still real product / real data — operator-as-customer. Almost as strong. Headline copy must label honestly: *"Here's me running this on my own business."* The transparency angle is a feature, not a hedge.

**Q3 — If Q2 is no: can you build a realistic demo workflow with synthetic-but-realistic data that you'll commit to making the real product match within 60–90 days?** If yes → ship **Step 3: Honest-mock.** Real product running on a purpose-built synthetic-but-realistic demo dataset, labelled in page copy as a demo. Materially weaker than Steps 1–2 — the "real cursor on real data" signal is broken — but preserves diegesis if the product itself is real. **Honesty floor:** copy must NOT pretend the data is real. That's the produced-ad-cosplaying-leaked-artifact failure mode Grounder named — it flips both columns of the produced-vs-leaked × diegetic-vs-non-diegetic axis at once and loses both.

**Q4 — If Q3 is no: ship Step 4: Defer-to-Lottie-bridge.** Operator can't credibly run any of the above today. Ship a Lottie/SVG-animated hero instead (per Explorer's +5% measured lift on B2B landing pages — strictly better than autoplay video's -7% in this scenario). IDEA_01 becomes a v2 target, not a v1 deliverable. This is the honesty floor at the brief level: better to ship a working Lottie now than a fake-evidence video that destroys the brief's whole premise. The bridge isn't a hedge; it's a stage-appropriate deliverable, with a clear upgrade path back to Step 1 once product maturity arrives.

### Page treatment (applies to all four fork branches)

- **30-second loop**, muted autoplay, visible "watch with sound" button. Treepodia data via Explorer: play-icon overlay drives up to +100% views vs autoplay-with-sound. The operator's existing instinct for autoplay survives; the audio decision flips.
- **Sub-1.5 second LCP requirement.** Below this threshold the autoplay-video penalty (-7% conversion median, Digital Applied 2026) softens to roughly neutral. Above 2s the conversion curve breaks and the whole brief becomes net-negative. Engineering ownership is an Open Question.
- **First-5-seconds value-prop beat is decisive.** Explorer: 19.4% drop after 10s, 44% after 60s, decisive moment is seconds 0–5. The loop must show a recognisable pain-relief moment in the first beat, not a logo intro.
- **Optional composition layer: FT-2 diptych.** Left frame = the recognition trigger (the spreadsheet, the inbox, the unread queue); right frame = the agent's work. Ships in v1 if and only if the diptych shot is no harder to capture than the single frame. If it requires a second production day, defer to v2 A/B test. FT-R4 makes clear that the FT-2 diptych is a *composition device*, not the time-travel diptych — those are different productions despite using the same compositional word.
- **No FT-5 counterfactual.** FT killed FT-5 outright in R4 with the stated reason that a counterfactual is hypothetical, not evidence — violates the very axis this brief is built on. No left-frame salvage.

## Lineage

### Origin

FT-1 "spreadsheet-cam" from Free Thinker's opening volley (round 1): *"Screen-recording of a spreadsheet, an inbox, a Slack. No faces, no music, just cursor movement and text typing in real time. Agent's actions appear as a second cursor moving on its own. The horror/relief is watching the second cursor do the owner's job. This is the anti-AI-ad-about-AI move. Looks like a Loom, costs almost nothing, feels confiscated rather than produced."*

### Key Turns

1. **Free Thinker's diegetic-vs-non-diegetic reframe (round 4).** Reset the style question one level up. Cartoon vs photoreal was 2015 framing. The real 2026 axis is whether the video pretends to occupy a real world (diegetic) or openly admits it's an explainer (non-diegetic). Cartoon-pretending-to-be-real is the failure mode operator feared but couldn't name.
2. **Free Thinker's produced-vs-leaked second axis (round 5).** Named a deeper fork: feels-made vs feels-found. A leaked-artifact aesthetic bypasses ad-blindness because the viewer doesn't parse it as an ad. FT-1 sits on the feels-found side.
3. **Grounder's product-as-evidence-vs-performance-of-authenticity split (round 6).** Refined FT's leaked-artifact column into the B2B-credible half (real screen recording — product-as-evidence) and the amateur half (founder-on-iPhone — performance-of-authenticity). Only the former survives B2B scrutiny.
4. **Grounder's performance-vs-evidence axis (round 7).** The decisive frame. Cartoon = performance. Photoreal-with-actors = performance. Wes Anderson = performance. Kinetic typo = performance. Only **evidence** — product doing its job, in frame, in real time — escapes both AI-trope fatigue AND the -7% autoplay penalty simultaneously.
5. **FT-R3 (round 10) reframed FT-1's stylistic question.** *"The remaining stylistic decision isn't 'what aesthetic' — it's 'what counts as evidence.'"* This brief specifies evidence type, not aesthetic. FT-R3 also self-killed FT-6 (kinetic typography over B-roll) on production-economics grounds — a 2-week B-roll shoot for a solo operator produces stale footage by the time it ships.
6. **Grounder R3 (Thread E, round 11) walked back evidence-only as a universal rule.** *"Walking back part of 'performance vs evidence': evidence-only is right for A, wrong for B."* This walkback is critical — it scopes the IDEA_01 brief to Audience A specifically and creates the structural space for IDEA_02 to operate on a different cognitive task. Grounder also raised the operator-capability constraint that became this brief's load-bearing fork: *"That's a production stack, not a product stack. No evidence of deployable agent workflow a chiropractor or accountant could be filmed using today."*
7. **FT-R4 (round 13) — IDEA_01 and IDEA_02 are structurally distinct, not variants.** FT-R3 had framed TT as extending the evidence axis across time. FT-R4 walks that back: evidence answers "does this actually work?" / past-tense / trust lever; TT answers "what does my life look like with this?" / future-tense / aspiration lever. Different cognitive jobs. *"You need both."* This brief is the past-tense / trust answer.
8. **Arbiter convergence signal (round 14).** Three briefs locked: IDEA_01 (prove, Audience A), IDEA_02 (reveal, Audience B), IDEA_03 (landing-UX strategy). Operator-capability elevated to vision-document concern. Production language locked to *"one capture, two productions, shared raw footage."*

### Variations Explored

| Variation | What It Was | Why It Was Set Aside | Worth Revisiting? |
|-----------|------------|---------------------|-------------------|
| FT-1 with mocked-up spreadsheet | Stylised fake spreadsheet composition | Produced-ad cosplaying leaked-artifact — flips both axes at once and loses both | No as the strong version; survives as a labelled "honest-mock" in fork step 3 only |
| Founder-on-iPhone / mockumentary | Hand-held performance-of-authenticity with frustrated-owner actor | Grounder: amateur-coded for B2B, especially with actor age mismatching buyer age | No |
| FT-3 ghost-employee documentary | Owner interviewed about Maya the never-seen software hire | FT-R4 killed standalone documentary format: needs 30s before reveal pays off, landing pages get 6s. Absorbed only as composition device — "agent has a name" survives as signed notifications in IDEA_02 | No standalone; the name-signed convention is in IDEA_02 |
| FT-6 kinetic typography over real B-roll | Floating monospace text as "the agent" over operator B-roll | FT self-killed in R3 — 2-week B-roll production for solo operator, stale by ship; Lottie wins on iteration speed | No |
| FT-7 Wes Anderson cut, FT-9 agent-as-narrator, FT-10 90s infomercial | Various stylised aesthetics | Arbiter: NOT-INTERESTING — trope-imitated / Clippy territory / produced-by-definition | No |
| FT-2 diptych as a fixed v1 requirement | Both frames shipping in v1 | Adds production complexity without proven incremental lift over single-frame; depends on operator-capability fork outcome | Yes — as a v1.5 A/B test against single-frame |

## Free Thinker's Vision

From FT-1 in the opening volley: *"Screen-recording of a spreadsheet, an inbox, a Slack. No faces, no music, just cursor movement and text typing in real time. Agent's actions appear as a second cursor moving on its own. The horror/relief is watching the second cursor do the owner's job. This is the anti-AI-ad-about-AI move. Looks like a Loom, costs almost nothing, feels confiscated rather than produced."*

From FT-R3 sharpening: *"The remaining stylistic decision isn't 'what aesthetic' — it's 'what counts as evidence.' Screen recording is one form. So is: Loom-style narration over the dashboard, a customer voicemail played over a transcript, an email thread scrolling with the agent's reply highlighted, a CSV export with a green-highlighted 'auto' column. All 'feels-found,' near-zero production cost. Brief should specify evidence type per video, not aesthetic."*

From FT-R4 distinguishing IDEA_01 from IDEA_02: *"Evidence-only answers: 'Does this actually work?' Past-tense. Real product on screen. Conversion lever = trust. Converts buyers who already want the thing."*

## Grounder's Honest Read

### Why this one

From Thread C: *"Strongest by a mile if it's literally screen-recording a real spreadsheet while a real agent works it. Product-as-evidence, diptych is concrete, sidesteps cartoon question."* Grounder explicitly named this as the only idea that survives both axes of risk — AI-trope fatigue and the -7% autoplay conversion penalty — simultaneously.

From Thread D (performance-vs-evidence axis): *"Only category escaping AI-trope fatigue AND autoplay penalty is evidence — product doing its job, in frame, in real time."*

### How it connects to the brief

The seed asked for "is this you?" recognition. Audience A is the visitor for whom recognition lives inside three seconds of seeing their own working environment on screen. The seed's "Amazing world of AI" + "Struggling business owner" framing maps cleanly to two distinct audiences — IDEA_01 serves the second. The seed's lean toward cartoon is honoured by *dissolving* the style question into evidence-type and footage-type, which is what FT-R3 named explicitly.

Operator's anxieties from the seed: cartoon-reading-childish (resolved by dissolving the style question), AI-ad-about-AI trope fatigue (counterprogrammed by evidence), two videos may underperform one (resolved by the audience-scoped split — see IDEA_02 for the discovery half), "is this you?" framing may feel generic (resolved by IDEA_03's seed-dissolves position — *Is this you?* survives as one-line headline, dies as a routing mechanic).

### Where it could lose people

- Honest-mock fork branch can flip to produced-ad cosplay if the copy isn't explicit about the data being synthetic. Honesty floor is non-negotiable; the failure mode is the produced-ad-cosplaying-leaked-artifact one Grounder named — both axes lost at once.
- Doesn't serve Audience B (discovery) — that's IDEA_02's entire purpose, and Grounder's audience-scoped walkback of evidence-only in R3 (Thread E) makes it explicit. A discovery visitor watching IDEA_01 sees "someone using QuickBooks" with no frame to decode it.
- If the operator-capability fork lands on step 4 (defer-to-Lottie-bridge), the brief is a v2 deliverable. The operator must not push step 3 (honest-mock) when step 4 is the right answer just to ship a video — that violates Grounder's product-stack-vs-production-stack distinction.
- Sub-1.5s LCP is a hard engineering constraint. Without poster fallback, preload tuning, and CDN encoding, the conversion math goes negative against a plain image hero.

## Arbiter's Evaluation

Flagged INTERESTING after Thread D because the dialogue had collapsed multiple stylistic threads into a single coherent deliverable that resolves both the operator's stated anxieties and Explorer's strongest empirical finding (-7% autoplay penalty) simultaneously. The convergence signal at round 14 ratified it specifically as the Audience A deliverable, paired with the four-step operator-capability fork rather than as an unconditional v1 ship — this preserves the brief's evidence claim under all operator product-maturity scenarios. The pairing with IDEA_02 as a separate-production reveal (rather than as a second cut of the same asset) is explicit per FT-R4's structural distinction; the briefs share **one capture** at the footage layer but produce **two productions** in post.

## Open Questions

1. **Operator-capability fork resolution.** Which of the four steps is the first one the operator can credibly meet today? This is the load-bearing question for the entire brief. It is also the input to the analogous fork in IDEA_02 (Path A's longitudinal-capture claim needs at least step-1 capability with 12 months of customer deployment runway).
2. **First-5-seconds value-prop beat.** What pain-relief moment occupies seconds 0–5 of the loop? Without this, the loop is screen-rec curiosity, not a hero. Suggested: pick the highest-recognition single task type for the operator's ICP (invoice draft / inbox triage / Yelp reply / Tuesday-night reconciliation) and shoot that specific moment.
3. **Sub-1.5s LCP engineering ownership.** Who solves the page-load constraint? Poster fallback, preload-as=video, encoding pass (H.264 baseline + VP9 fallback), CDN distribution. Without an owner this brief silently degrades to its negative case.
4. **FT-2 diptych in v1 or v1.5.** Does the diptych composition ship with the first loop or as an A/B variant once the single-frame baseline has measurement? Suggested: single-frame in v1, diptych as v1.5 A/B unless the diptych shot is no harder to capture than the single frame.
5. **Length: 30s strict, or stretch?** Explorer's research is unambiguous that 30s is the landing-page sweet spot and 60s+ is tolerated only when first 30s have proven value. A 15s social cut is a natural derivative for paid distribution but lives outside this brief.

## Next Steps (If Pursued)

- Operator answers the four-step capability fork (the critical first move; everything below assumes step 1 or step 2 is meetable).
- Capture real working-session footage of the agent doing one concrete task on the operator's actual or internal-tool data — pick the highest-recognition task type per the operator's ICP.
- Specify the seconds 0–5 value-prop beat in headline copy and shot order.
- Engineering: poster image fallback + preload + LCP measurement on a staging deploy. Establish the LCP budget before any visual work ships.
- Decide diptych vs single-frame for v1 (default: single-frame; diptych as v1.5 A/B).
- Confirm the raw footage layer is captured with IDEA_02's needs in mind — see IDEA_02 for the post-production additions it will require (annotations, named-notification labels, time-jump composition).
