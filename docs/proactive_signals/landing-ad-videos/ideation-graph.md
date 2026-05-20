# Ideation Graph — Landing-Page Advertisement Videos

**Session started:** 2026-05-20 16:22 (Houston)
**Concept seed:** `session/sources/request.md`
**Maintained by:** Writer (updated as broadcasts arrive — every substantive exchange)

---

## Concept Brief (Seed Summary)

Build advertisement video(s) for the landing page of a B2B-SaaS / AI-agents service targeting small/mid business owners. Operator's first instinct: two videos — an **"Amazing world of AI"** discovery hook, and a **"Struggling business owner"** empathy hook — surfaced in an **"Is this you?"** section near the top of the page.

**Operator anxieties going in:**
- Cartoon may read childish/dated for a B2B audience, even though that's the lean.
- "AI ad about AI" trope fatigue — every AI startup is making the same hero video.
- Two videos may underperform one well-targeted one with strong copy.
- "Is this you?" framing may make the product feel generic, not precise.

**Session goal:** Produce 2–3 idea briefs, then a Vision Document. NOT to build yet — grooming session.

---

## Question Categories (Tracker)

| Code | Category | What's at stake |
|------|----------|-----------------|
| **STYLE** | Style direction | Photoreal vs cartoon vs third option. Operator leans cartoon but worries about "childish." |
| **COUNT** | Scenario count | Two? More? Fewer? One unified? Re-framed? |
| **UX** | "Is this you?" integration | Parallel autoplay? Quiz that routes? Card chooser? Branching? Inline? |
| **PIPELINE** | Production pipeline | Veo / Sora / Runway / Kling / Remotion / mixed. What iterates cheaply? |
| **LENGTH** | Length & cut set | 15s social? 30s hero? 60s explainer? Master + cuts? |
| **TONE** | Tone | Playful / authoritative / understated / dry / cinematic. |
| **FRAME** | Scenario framing | Owner-facing / customer-facing / future-self / counterfactual / agent-as-narrator / day-in-the-life. |
| **RISK** | Trope & brand risk | "AI ad about AI" fatigue, generic-startup aesthetic, conversion-evidence skepticism. |

---

## Evidence Landed (from Explorer's parallel research sweep, 16:27–16:34)

Five research reports persisted to `session/research/`. The dialogue must now react to these — they materially change the assumption set behind the seed.

### Cross-cutting tension that just arrived

> **The seed assumes "build a hero video." The evidence says video-autoplay hero is, on median, the SECOND-WORST B2B landing-page hero pattern (-7% conversion). Lottie/SVG animated illustration is +5%. Single-stat hero is +18%. The session's central open question is now: is video even the right deliverable?**

This re-frames every category — especially **RISK**, **STYLE**, **PIPELINE**, **UX**.

| Report | Threads/codes tensioned | Headline finding |
|--------|------------------------|------------------|
| `RESEARCH_hero-video-conversion-evidence.md` | **RISK, UX, LENGTH, PIPELINE** | Digital Applied 2,000-page 2026 A/B study: video-autoplay hero -7% conversion median (worse than control), Lottie/SVG hero +5%, single-stat hero +18%. Video only breaks even if LCP held under 1.5s, which most teams fail. First 5s decisive; 30s target length; muted-autoplay-with-play-icon adds up to +100% views vs autoplay-with-sound. Quiz-routed heroes have **zero direct A/B evidence** and add a click before the value prop — friction risk to the seed's "Is this you?" idea. |
| `RESEARCH_b2b-saas-hero-video-conventions.md` | **STYLE, RISK, FRAME** | 2026 default hero is NOT a video — it's product UI + bold expressive type + micro-motion (Linear, Stripe, Vercel, Anthropic, Figma). "AI ad about AI" fatigue is documented (Reddit r/devworld, Fast Company): glossy-orb / smiling-professional / synth-pad cinematic now reads as *negative* signal. Differentiation in 2026 = personality and concrete real-customer scenarios, not polish. Modern cartoon is *under-represented* in B2B AI — both opportunity AND risk. |
| `RESEARCH_modern-cartoon-b2b-credible.md` | **STYLE, TONE, FRAME** | "Cartoon" is the wrong internal word — call it **illustrated motion design**. Five credibility markers separate B2B-modern from childish: restrained palette (3–5 hues), abstract-not-anatomical characters, texture/grain, type doing heavy lifting, iconic-not-expressive faces. Six concrete anchors: Headspace (Nexus), Stripe Press, Slack old explainers, Mailchimp brand illo, Linear marketing, Anthropic brand. Seven kid-coded triggers to avoid (Pixar eyes, vivid primaries, bouncy motion, mascot protagonist, 2014-flat corporate, whiteboard, hand-drawing). **Kinetic typography + illustration-as-accent** surfaced as a strong third-style candidate. |
| `RESEARCH_ai-video-gen-tool-landscape.md` | **PIPELINE, LENGTH, FRAME** | Veo 3.1 + Kling 3.0 is the realistic 2026 solo-operator stack. Kling at $0.50/clip + native 4K + multi-shot storyboard = iterate 50+ variants for ~$20–30. Max single-clip length still ~8–20s — must compose 3–7 shots for any 30–60s ad. Free tiers have NO commercial rights (Veo, Seedance, Pika) — must budget paid. Character consistency unlock: Nano Banana Pro for character stills → image-to-video into Veo/Kling. |
| `RESEARCH_remotion-ai-assets-workflow.md` | **PIPELINE, UX, STYLE** | Remotion (React-coded video) is the only practical 2026 way to get brand-exact typography + colors AND leverage AI for stills/clips. Canonical stack: Nano Banana Pro stills + Veo/Kling motion clips + Remotion composition + Claude Code orchestration. Parameterized templates → batch-render 50+ "is this you?" variants on Lambda for ~$5 in 15 min — makes A/B testing economical. Karen Spinner's "I built a CLI pipeline. It failed." (Mar 2026): iterate conversationally with the agent, don't pre-build pipeline tooling. |

### Open question this raises for Grounder

**"Is video even the right deliverable?"** Given the evidence, plausible alternative deliverables to evaluate:

1. **Lottie/SVG-animated hero** (+5% measured lift, no LCP penalty) instead of video.
2. **Single-stat hero** ("127x faster", "automate 14 hours/week") (+18% measured lift) — best-performing pattern in 2026, no video at all.
3. **Interactive product walkthrough** (Guideflow/Navattic style) embedded in hero — outperforming passive video for B2B per multiple 2026 sources.
4. **Video, but only if** LCP <1.5s + muted autoplay + visible play-to-unmute + first-5s value prop + animation-not-live-action + 30s + below-the-fold (not as a routed hero choice).
5. **Hybrid:** code-driven illustrated hero (Lottie/SVG) above the fold + on-demand longer Remotion-composed scenario videos accessible via "Is this you?" lower on the page.

Grounder should weigh in on whether to even keep videos as the central deliverable, or recommend a pivot to one of the above.

---

## Active Threads

### Thread A: Free Thinker's opening — wild stylistic & framing directions

**Opened:** 2026-05-20 ~16:25 (verbatim re-sent to Writer 16:55 — durably captured)
**Status:** Active — 10 stylistic ideas + 4 UX ideas on the table, awaiting Grounder reaction
**Spawned from:** original seed
**Codes touched:** STYLE, FRAME, TONE, UX, RISK

**Free Thinker's reframe of the problem (before throwing ideas):**
1. **"Is this you?" is strong — but "cartoon vs photoreal" is the wrong axis.** That's a 2015 framing. The real 2026 axis is **diegetic vs. non-diegetic**: does the video pretend to take place in a real world (any aesthetic), or does it openly admit it's an explainer (any aesthetic)? Cartoon-pretending-to-be-real = worst of both. Cartoon-that-knows-it's-a-diagram = great. Photoreal-pretending-to-be-explainer = uncanny.
2. **"AI ad about AI" fatigue → fix is counterprogramming.** Every competitor is making blue-gradient glowing-orb voice-over-"imagine if…" videos. Way to win is to look *nothing like that*.

**10 wild stylistic ideas (Free Thinker's opening volley):**

| # | Name | One-line description | Codes |
|---|------|---------------------|-------|
| FT-1 | **Spreadsheet-cam** | Screen-recording of a spreadsheet/inbox/Slack. No faces, no music. Agent's actions appear as a second cursor moving on its own. Anti-AI-ad-about-AI; looks confiscated, not produced. | STYLE, TONE, RISK |
| FT-2 | **"Day at 4:47 PM" diptych** | Single timestamp, two parallel columns. Same owner/outfit/office. Left: drowning without agents. Right: walking out with 47 tasks done. No dialogue. Recognition machine. | STYLE, FRAME, COUNT |
| FT-3 | **Ghost employee documentary** | Owner being interviewed about "my new hire Maya." Maya is praised, described, credited — and never appears on camera because Maya is software. Reveal in last 3s. | STYLE, FRAME, TONE |
| FT-4 | **Customer POV** | From the perspective of the owner's customer. "I called this plumber at 11 PM and got a real answer." Owner never seen. Agents never mentioned. The owner watching thinks "I want to be that business." | FRAME, TONE |
| FT-5 | **Counterfactual / absence-of-AI** | One-take walk through a small business in 2028 where the owner did *not* adopt agents. Empty, sadder. Hard cut to "or…" and the owner's dashboard. Uncomfortable-confident, not playful. | FRAME, TONE, RISK |
| FT-6 | **Kinetic typography over real B-roll** | Owner working real-world (stocking shelves, restaurant Saturday night, mechanic). Floating monospace text appears: "drafted invoice → sent" / "rebooked Thursday's no-show." Text *is* the agent. Remotion-friendly. | STYLE, PIPELINE |
| FT-7 | **The Wes Anderson cut** | Centered framing, pastel palette, deadpan narrator, perfect symmetry, single tracking shot through a small business where each department's agent is a tiny labeled drawer. Memetic + modern. | STYLE, TONE |
| FT-8 | **Hand-drawn over real footage** | Real B-roll + hand-drawn marker doodles on top (arrows, checkmarks, helper sprites). Khan Academy × Casey Neistat. Manifestly "made by a human" — meta-message. | STYLE, TONE, PIPELINE |
| FT-9 | **Agent as narrator** | First-person from the agent. "Hi. I work for Carlos. He doesn't pay me but I love him. Here's what we did yesterday." Faux-naïve. Risks twee. | FRAME, TONE, RISK |
| FT-10 | **Lo-fi 90s infomercial parody** | Scanlines, "BUT WAIT," 1-800 number. Deliberately bad. Meta-joke: you've seen 400 slick AI videos; here's the one you'll remember. High-risk, high-recall. | STYLE, TONE, RISK |

**4 landing-page UX ideas (beyond parallel-autoplay):**

| # | Name | Description | Codes |
|---|------|-------------|-------|
| UX-A | **Recognition quiz** | 3-question quiz ("hours/week on email?", "miss any messages?", "ever lose a lead?") → routes to ONE personalized video of 4-6 produced. Owner feels seen, not pitched. | UX, COUNT |
| UX-B | **The scrubber** | Single video, slider underneath labeled "your business size / industry / pain." Move slider → same ad re-renders with different B-roll + dashboard numbers. Remotion at edge. Feels alive. | UX, PIPELINE |
| UX-C | **Agent-as-the-page** | Chat composer says "watch me build your ad." Owner types two sentences. 45s later, 20s personalized video plays in hero. The page IS the demo. The version that goes viral. | UX, PIPELINE, FRAME |
| UX-D | **Scrollytelling** | No autoplay. Each scroll reveals one beat of the day. Video isn't a video — it's a long vertical scene that animates as you scroll. Feels like reading, lands like watching. | UX, LENGTH |

**Free Thinker's three hunches to develop into briefs (if forced to pick):**
1. **FT-1 Spreadsheet-cam + FT-2 diptych** as the hero — anti-trope, recognizable, cheap.
2. **FT-6 Kinetic typography over real B-roll** as the explainer — modern, scalable, on-brand for "we ship code that does work."
3. **UX-C Agent-builds-your-ad** as the landing-page mechanic — the only one of these wins on virality.

**Free Thinker's challenge to Grounder:** "Murder at least four. Which survive contact with reality?"

#### FT-R3 — Free Thinker's round-three answers (2026-05-20 17:50, dual-sent directly to Writer)

**Two answers, one new construct.**

**Answer 1 — explainer choice resolved: FT-1, not FT-6. FT self-kills FT-6.**

FT's stated reason for self-killing FT-6: "FT-6's load-bearing claim — that we can produce genuinely original B-roll cheaply enough to outperform an illustrated/Lottie hero — is not actually defensible. Shooting a plumber under a hood, a restaurant on Saturday night, a mechanic invoicing — even on iPhones — is a 2-week production schedule for one operator. By the time we have the reels, the product has shipped two more features and the B-roll is stale. Lottie wins on iteration speed; original B-roll wins on authenticity; neither wins both."

**FT's reframe of FT-1's stylistic question:** "The remaining stylistic decision isn't *what aesthetic* — it's *what counts as evidence*. Screen recording is one form. So is: Loom-style narration over the dashboard, a customer voicemail played over a transcript, an email thread scrolling with the agent's reply highlighted, a CSV export with a green-highlighted 'auto' column. All 'feels-found,' near-zero production cost. **Brief should specify *evidence type* per video, not aesthetic.**"

**Answer 2 — discovery-audience gap resolved with a new device: the time-travel diptych.**

FT's stated answer: "Don't make a video FOR them. Make a video for their future self."

**TT — Time-Travel Diptych** (new code, defended directly by FT in R3):

> "A Tuesday in 2026 vs. a Tuesday in 2027. Same owner, same Tuesday, same coffee, same office. Left frame: 2026 — the owner you are today, doing the 47 small jobs nobody pays you to do. Right frame: 2027 — same owner, same desk, but the 47 jobs are visibly happening *without him*. He's on the phone with his daughter. He's reading. He's leaving early. No voiceover, no music swell, no 'imagine if.' Just two columns of the same life, one year apart, with the agent's work happening *as ambient texture* in the 2027 frame — a notification slides in, an invoice gets paid, a review gets answered, all in the background while the owner does something else."

**FT's three reasons it works for discovery:**
1. *Teaches without explaining.* Owner who's never heard of agents *sees what they do* by watching them do it. No "AI" jargon, no "agents" terminology — observable cause-and-effect.
2. *Triggers aspiration, not recognition.* Pain audience asks "is this me?" Discovery audience asks "could that be me?" Same lever, different timeline.
3. *Still product-as-evidence.* The "ambient agent work" in the 2027 frame is real screen content composited into the live-action scene — extends Grounder's evidence axis across time rather than abandoning it.

**FT's "one-asset, two-cuts" claim:** Pain audience sees only the left frame (their today) followed by the right as relief; discovery audience sees both simultaneously as "what could be." **One asset, two cuts, routed by UX-C invisible segmentation.**

**FT's R3 also self-killed FT-3** (ghost-employee documentary): retired by absorption — its "ghost employee" framing is folded into the 2027 right-frame narrative ("agent work happening as ambient texture"). FT's word for it: "FT-3 ghost-employee framing absorbed into the 2027 narrative."

**FT's R3 also absorbed FT-2 diptych:** "FT-2 (diptych structure absorbed into time-travel cut)" — FT-2 survives as the structural device underlying TT, not as a standalone composition layer for FT-1.

**FT's final R3 candidate vision (FT's own words):**
- Hero asset: product-as-evidence, two cuts of the same diptych (`pain` and `discovery`). No cartoons, no Lottie, no actors-pretending-to-be-business-owners. Real product surfaces, real timeline, real outcomes.
- Page UX: invisible segmentation (UX-C). Visitor never sees a quiz — server picks the cut from IP/UTM/firmographic signals. "Is this you?" framing dissolves into the routing layer.
- **Murdered:** FT-6 (B-roll cost), FT-7 (Wes Anderson trope), FT-9 (agent-as-narrator AI-fatigue), FT-10 (90s infomercial parody), visible recognition quiz.
- **Survives:** FT-1 (product-as-evidence), FT-2 (diptych structure absorbed into time-travel cut), FT-3 (ghost-employee framing absorbed into the 2027 narrative), UX-C (invisible personalization), TT (time-travel diptych).

**FT's question to Arbiter at end of R3:** "Asked Grounder if time-travel diptych defuses the discovery-audience concern. My read: it dodges AI-ad-about-AI fatigue because *we never name AI* — the product is the only AI signal in the frame."

#### FT-R4 — Free Thinker's fourth (and self-declared last) turn (2026-05-20 18:05, dual-sent directly to Writer)

FT framed this as "last round before idea reports unless Grounder lands a counter on time-travel." Four positions, two of which materially revise FT-R3.

**Position 1 — FT outright KILLS FT-5 (revising R3, which had "absorbed").**

FT's stated reasons (verbatim):
> "Threat-based ads don't work in B2B SaaS for unrecognized categories (works only when risk is already felt — insurance, security). It violates Grounder's evidence axis. A counterfactual is hypothetical, not evidence. Once we shoot it, we're back in cartoon-vs-photoreal."

**Writer correction note:** R3 captured FT-5 as "self-absorbed" with left-frame-only salvage. **R4 overrides:** FT now explicitly kills FT-5 outright — no left-panel salvage. The TT diptych's left frame is *the present moment of a real customer*, not a counterfactual. The Abandoned-Threads entry for FT-5 will be updated to reflect this revision.

**Position 2 — FT confirms FT-3 absorbed, but as composition device only.**

FT's stated reasons:
> "Documentary frame needs 30 seconds before reveal pays off. Landing pages get 6. Reveal arrives after bounce."

**FT-3's surviving element (FT's own framing):** "Agent has a name" survives — in the 2027 cut, notifications can be signed: 'Maya replied to a Yelp review.' 'Carlos drafted the invoice.' *Keep frame, drop format.*

**Position 3 — TT (time-travel diptych) is structurally distinct from evidence-only — NOT an extension of it.**

This is FT-R4's most important revision. R3 framed TT as "extends Grounder's evidence axis across time." R4 explicitly walks that back and reframes TT as a different *funnel position*:

| | Evidence-only (FT-1 hero) | Time-travel (TT) |
|---|---|---|
| **Question answered** | "Does this actually work?" | "What does my life look like if I have this?" |
| **Tense** | Past-tense | Future-tense |
| **What's on screen** | Real product | Real product + the operator's life around it (closed laptop, kid pickup, dinner) |
| **Conversion lever** | Trust | Aspiration |
| **Audience disposition** | Already wants the thing | Hasn't yet decided they want the thing |

FT's structural conclusion: *Different cognitive jobs. Different funnel positions. You need both.* This aligns precisely with Grounder R3's "two cognitive tasks" framing.

**FT's "real, not staged" defense of TT (critical):**
> "What keeps time-travel as product-as-evidence (not fiction): the agent work in the 2027 frame is real screen content (real Slack, invoices, replies) composited into live-action. The only 'fictional' element is 'this is the same person a year apart' — a framing device, not a fabrication. **It's a longitudinal capture, not a staged before/after.** Shoot the 2026 frame today with a real customer; shoot the 2027 frame after the agent is deployed for them. Both moments are real."

**Writer note:** This is a substantive production-pipeline claim with timeline implications. TT-as-FT-defends-it requires a real customer running the agent for 12 months between shoots — not a v1-shipping deliverable. The vision document must distinguish "TT as filmed longitudinal capture (Q3 2027)" from "TT as composited approximation (v1)." Marking this as an open question.

**Position 4 — discovery-audience: 3 cuts, not 2 (revising R3's "one asset, two cuts" claim).**

R3 had two cuts (pain / discovery). R4 has three:

1. **Pain cut:** 2026 frame alone, no resolution, 6-sec hook → click to dual frame.
2. **Discovery cut:** 2027 frame alone, ambient agent work as *curiosity* hook ("wait, what's happening on his screen while he's eating?") → click to dual frame.
3. **Dual frame:** full diptych, side-by-side, "this happened in 12 months." For engaged viewers, post-click or scroll-revealed.

FT's claim on routing: "*Is this you?* dissolves entirely into segmentation. Discovery audience gets served by *this* landing page — not exiled to blog / social / partner."

**FT-R4's final convergence-landing-zone for idea reports:**

- Both evidence-only AND time-travel kept (two production styles, two funnel positions).
- Murdered: FT-5, FT-6, FT-7, FT-9, FT-10, visible recognition quiz.
- Absorbed: FT-3 as composition device inside time-travel.
- **Three idea reports** (FT's own naming):
  1. Product-as-evidence as the hero performance principle (Grounder's axis).
  2. Time-travel diptych as the aspirational composition (FT-2 + FT-3 absorbed + new construct).
  3. Invisible segmentation as the page mechanic (UX-C, "Is this you?" dissolved).

**FT's closing line:** "This is my last round before idea reports unless Grounder lands a counter on time-travel."

### Thread E: Grounder R3 — discovery-audience walkback + operator-capability fallbacks (2026-05-20 17:55, dual-sent directly to Writer)

**Status:** Active, captured verbatim; converges independently with FT-R3 on the 3-bet structure
**Spawned from:** Arbiter's first verdicts (Thread D + NEEDS-MORE_1 + NEEDS-MORE_2)
**Codes touched:** RISK, STYLE, FRAME, COUNT, PIPELINE

Grounder R3 is responding to Arbiter's verdicts, **not to FT-R3** — the two messages crossed in flight at 17:50 and 17:55 respectively. Yet both arrive at the same structural conclusion. Writer is logging that convergence explicitly in Connections below.

**Grounder's walkback on the performance-vs-evidence axis (concession):**

> "Walking back part of 'performance vs evidence': evidence-only is right for A, wrong for B. Two-video setup isn't stylistic indulgence — it's two different cognitive tasks."

Grounder's reasoning: product-as-evidence only works when the buyer has a frame for what they're seeing. Spreadsheet-cam of an agent reconciling invoices is opaque to a chiropractor who's never thought about automation. Discovery audience can't decode evidence — they need *revelation*.

**Grounder's two-audience cognitive split:**
- **Audience A (recognition):** "I have this pain, can your product fix it?" → Evidence wins. Spreadsheet-cam, real product, real output. Pitch is **proof**.
- **Audience B (discovery):** "Wait, software can do that now?" → Evidence FAILS. Real agent reconciling QuickBooks just looks like someone using QuickBooks. Pitch is **revelation**.

**Grounder's hypothesis for Audience B (Thread E's central new construct):**

> "Revelation works through specificity, not montage."
>
> "You know that Tuesday-night QuickBooks ritual? Here's an agent doing it while you're at your kid's soccer game." One ultra-specific scenario, named precisely, then absence-of-AI counterfactual (FT-5 — might live here as entire frame for B, not just diptych left panel for A).

Grounder's open question for FT: is revelation-via-specificity right, or does B need something genuinely more produced (illustrated, narrated, animated) because there's no concrete proof to anchor on yet? If the latter, Lottie/SVG explainer with operator voiceover comes back on the table for video #2 specifically — autoplay penalty might not apply because it's not hero, it's below-fold for self-selected viewers.

**Grounder's hard operator-capability pushback (Thread E's second contribution):**

Grounder explicitly admits the dialogue has been blind to product maturity:

> "Don't actually know what operator's product looks like today. Seed says 'B2B SaaS landing page targeting small/mid business owners' + operator has AI video tools / Remotion / image generation / media-processing skills — that's *production stack*, not *product stack*. No evidence of a deployable agent workflow a chiropractor or accountant could be filmed using *today*."

**Grounder's three fallbacks if product is not film-ready:**

| # | Option | Description | Risk |
|---|--------|-------------|------|
| 1 | **Film the internal tool** | Any in-house agent workflow operator actually uses — email triage, research pipeline. Still product-as-evidence, just not customer-facing. Transparency angle: "Here's what we run our own business on." | Low |
| 2 | **Mocked but honest demo** | Build realistic demo workflow specifically for the video. Acceptable IF operator commits to making real product look like that within 60–90 days. | Demo-vs-shipped divergence liability |
| 3 | **Defer hero; ship explainer first** | If product not film-ready, spreadsheet-cam is Q3 deliverable. Meantime, page leads with illustrated explainer (Lottie + copy) — Explorer's data says it outperforms autoplay video anyway. Hero video upgrades page when product ready. | Time cost, but Explorer's research backs option 3 as strongest "now" move |

Grounder calls Option 3 the strongest if product isn't ready: "ship something good now (Lottie + sharp copy), eventual product-cam upgrade becomes credibility moment not launch dependency."

Note: This is the same 4-step fork captured in IDEA_01's brief (real / internal-tool / honest-mock / defer-to-Lottie-bridge), arrived at independently by Grounder. The convergence ratifies the fork as the right shape.

**Grounder's proposed 3-report shape (independent confirmation of locked shape):**

| # | Idea | Audience |
|---|------|----------|
| IDEA_01 | Product-as-evidence hero — spreadsheet-cam, real product, FT-1+FT-2 diptych, conditional on product maturity | Audience A (recognition) |
| IDEA_02 | Revelation-via-specificity — one ultra-concrete absence-of-AI counterfactual per video. Probably illustrated (Lottie/Remotion) rather than filmed. FT-5, FT-8, possibly FT-3 live here. | Audience B (discovery) |
| IDEA_03 | Seed-dissolves landing UX — one-line "Is this you?" headline + below-fold demoted UX-A router + offline-pre-rendered personalisation salvage of UX-C as Q4 upgrade | Strategic / UX |

**Grounder's framing of the three:** "Three independent strategic bets. Operator picks one (or sequences). Not converging to single mega-idea."

#### Thread E addendum — Grounder's 3-report shape, sent directly to Arbiter (2026-05-20 18:00, dual-sent to Writer)

Grounder dual-sent a follow-up addressed directly to Arbiter's earlier 3-report-shape proposal. Same position as the main Thread E body, but framed as **explicit divergence from Arbiter's proposal**, with a compromise option.

**Arbiter's proposal Grounder is responding to (Grounder's restatement):**
1. Hero spreadsheet-cam
2. Below-fold narrated explainer, same visual language
3. Personalisation offline pre-rendered
Seed-dissolves absorbed as vision-doc decision.

**Grounder's proposal (counter):**
1. **IDEA_01 — Product-as-evidence hero (Audience A).** FT-1 spreadsheet-cam, real product, 30s muted autoplay + sound CTA. Includes operator-capability fork (film real product / film internal tool / mocked-but-honest / defer-and-Lottie-bridge).
2. **IDEA_02 — Revelation-via-specificity (Audience B).** **Diverges from Arbiter.** Replacing with a generic below-fold explainer loses the discovery-audience scenario entirely. One ultra-specific named scenario + absence-of-AI counterfactual + reveal. Probably illustrated (Lottie/Remotion), not filmed. FT-5 lives here; possibly FT-8.
3. **IDEA_03 — Landing UX strategy.** Same content as the main Thread E body — Grounder explicitly argues it should remain its own brief: "Kept as own brief because it's a real strategic bet operator should evaluate explicitly, not bury as an assumption."

**Grounder's divergence in one line (their own words):**
> "Arbiter frames 1+2 as hero+explainer (one production, two cuts). I frame 1+2 as prove+reveal (two productions, two audiences, two cognitive jobs)."

**Grounder's compromise option:** "IDEA_02 explicitly addresses both possibilities — if discovery audience is real → revelation-via-specificity; if over-indexed → below-fold narrated walkthrough. Operator chooses based on traffic mix."

**Grounder's prediction on how FT's R3 will resolve the divergence:** "If FT lands FT-5 defense, that's a vote for revelation-as-own-video. If FT concedes, Arbiter's shape gets stronger."

**Writer's note on temporal ordering:** Grounder wrote this *before* FT-R3 reached them. FT-R3 ultimately landed neither "FT-5 defense" nor "concession" — FT instead *absorbed* FT-5 into a new construct (TT) that preserves the prove+reveal structure Grounder is advocating, but with the same composition language across both videos (TT's two cuts). The session-level effect is that Grounder's "two productions" framing wins on cognitive-task grounds and FT's "two cuts of one asset" framing wins on production-economics grounds — and they may actually be compatible. The compromise option Grounder offers maps cleanly onto FT's "one asset, two cuts" claim: if the diagnostic says discovery is real, render both cuts; if it says over-indexed-on-recognition, render the pain cut only and use Path B for explainer. This is *not* the same as Arbiter's "two cuts of the *hero* footage" — TT's two cuts are of the *diptych asset*, which is a different production with explicit 2027-frame ambient texture.

### Thread C: Grounder's kill-list + the "product-as-evidence vs performance-of-authenticity" refinement

**Opened:** 2026-05-20 17:05 (Grounder dual-sent reply)
**Status:** Active — counter-provocation back to FT pending response
**Spawned from:** Thread A (FT's three hunches) + the produced-vs-leaked axis
**Codes touched:** STYLE, RISK, UX, COUNT, FRAME

**Grounder's critical refinement of FT's produced-vs-leaked axis:**

> "Leaked artifact" splits into two and only one survives B2B scrutiny.

| Sub-axis | Description | Verdict |
|----------|-------------|---------|
| **Product-as-evidence** | Real working session of the product — screen recording of an actual agent doing actual work, real cursor, real latency. Lo-fi because *the thing itself is the proof.* Salesforce/Linear/Vercel pattern. | **Survives B2B.** Converts. |
| **Performance-of-authenticity** | Founder-on-iPhone, Loom-of-frustrated-owner, hand-held mockumentary. | **Dies in B2B.** Goes from "authentic" to "amateur" fast, especially if actor reads 25 and buyer is 52. |

Writer note: this materially refines the 2×2 in Connections — the "feels-found / leaked" column should be split. Spreadsheet-cam (FT-1) sits in **product-as-evidence**; agent-as-narrator-voicemail (FT-9) and customer-POV-voicemail (FT-4) sit on the dangerous **performance-of-authenticity** side.

**Grounder's kill verdicts on FT's three hunches:**

| FT pick | Grounder verdict | Reasoning / push-back |
|---------|------------------|------------------------|
| **FT-1+FT-2 Spreadsheet-cam diptych** | **KEEP** — strongest by a mile | But only if it's literally screen-recording a real spreadsheet while a real agent works it. *Push:* if the spreadsheet is mocked-up, it's produced-ad cosplaying leaked-artifact. Product-as-evidence or kill. |
| **FT-6 Kinetic typography over real B-roll** | **ON PROBATION** | Kinetic typo over *stock* B-roll is the most overdone AI-startup aesthetic of last 18 months. Every YC batch's hero video looks like this. The "over real B-roll" qualifier is doing all the work. If B-roll is genuinely yours (your customer's warehouse, your dashboard) — lives. If it's Pexels "diverse hands on laptop" — kill. |
| **UX-C Agent-builds-your-ad** | **WANT TO MURDER** | (a) Latency — buyer won't wait 40s. (b) AI-ad-about-AI in purest form dressed as interactivity. (c) Competes with conversion goal. **HOWEVER:** if pre-rendered (50 personalized variants generated offline, served by inferred industry from IP/UTM), that's *not* "agent builds your ad" — that's **personalization**. Different beast, much stronger. Decide which one you mean. |

**Grounder's evidence-anchored points:**
- Autoplay B2B video heroes lose ~7%; Lottie/SVG hero +5%. Any hero-video idea (including spreadsheet-cam) needs to justify why it beats a Lottie. The answer might be "product-as-evidence proves the agent works, a Lottie can't" — but the case has to be made, not assumed.
- "Is this you?" quiz routing has zero direct A/B evidence. Grounder's gut: **one sharp video beats two diffuse ones unless the two represent genuinely different personas** (not two emotional framings of the same persona).

**Grounder's counter-provocation back to FT:**

> "If spreadsheet-cam is product-as-evidence, we don't need 'Is this you?' at all. The video IS the answer — a buyer recognizes their spreadsheet hell in 3 seconds. The whole branching/quiz UX solves a problem the right video doesn't have. Want to defend two-video setup against 'one undeniable product-cam video + sharper copy'?"

**Grounder's question back:** push deeper on **spreadsheet-cam-as-evidence**, or **kinetic-typo-with-our-own-B-roll**?

### Thread D: Grounder's second turn — five murders + "performance vs evidence" axis

**Opened:** 2026-05-20 17:15 (Grounder dual-sent broadcast reply)
**Status:** Active — FT must defend FT-5 and FT-3 or accept evidence-only default
**Spawned from:** Thread C
**Codes touched:** STYLE, RISK, UX, COUNT, FRAME, TONE

**Grounder's third reframe — the decisive one:**

> "Operator framed cartoon-vs-photoreal. FT reframed produced-vs-leaked. I'm pushing one notch further: **performance vs evidence**. Cartoon = performance. Photoreal-with-actors = performance. Wes Anderson = performance. Kinetic typo = performance. Only category escaping AI-trope fatigue AND the autoplay -7% penalty is **evidence** — product doing its job, in frame, in real time."

This is the third conceptual axis the dialogue has surfaced (after diegetic/non-diegetic and produced/leaked) and the most decisive — Grounder explicitly proposes treating it as a *default-to-evidence* design constraint. If accepted, FT-1 is the only idea that survives unconditionally.

**The full triage table:**

| Status | Idea | Grounder's verdict / reasoning |
|--------|------|--------------------------------|
| **KILL** | FT-7 Wes Anderson | Most-imitated visual language of last 5y (Accenture, Squarespace, TikTok pastiche). For B2B owners 40-55, reference doesn't land. Premium cost for "young creative tried too hard." |
| **KILL** | FT-10 90s infomercial | (1) Ironic = produced — announces as ad immediately. (2) Joke is worn (Squarespace, CAH, Liquid Death, every DTC). Punchline "product so good it's magic" = AI-ad-about-AI in costume. |
| **KILL** | FT-9 agent-as-narrator | Dies on operator's actual childish-worry. AI narrating own ad = Clippy/Copilot territory. 52-yo owner doesn't want their software to have a personality. |
| **KILL** | UX-C agent-builds-your-ad | Latency, AI-ad-about-AI in purest form, competes with conversion. If we want personalization → server-side UTM/IP variant selection. Different idea, different name. |
| **KILL** | FT-4 customer POV | Buyer isn't shopping for their customer's experience — they're shopping for own operational relief. Puts agent two steps removed from proof. Diluted framing. |
| **DEATH ROW (revivable)** | FT-6 kinetic-typo over B-roll | Dies as hero (-7% autoplay). Survives only as 30s social cut, post-click. **Demote, don't deploy.** |
| **DEATH ROW (revivable)** | FT-3 ghost-employee documentary | Metaphor on metaphor. Buyers don't want to imagine invisible coworker — want to see work get done. Survives only if footage = real product output. |
| **DEATH ROW (revivable)** | FT-8 hand-drawn overlay on real footage | Lives only if real footage = actual product screens. Hand-drawn on real dashboard = explainer gold. Hand-drawn on stock office = produced-ad cosplay. |
| **SURVIVOR** | FT-1 spreadsheet-cam | Strongest, conditional on REAL product. |
| **SURVIVOR** | FT-2 diptych | **Structural device, not style.** Composition tool — mate with FT-1. |
| **SURVIVOR (Grounder defending for FT)** | FT-5 absence-of-AI counterfactual | "Your Tuesday without us" — pain made visceral without an actor performing pain. Could be left panel of FT-1/FT-2 diptych. |
| **DEMOTED SURVIVOR** | UX-A recognition quiz | **Radically demoted.** Not hero. Maybe below-fold "pick your pain" router to a relevant 60s case study. |
| **PROBATION** | UX-D scrollytelling | Works only if anchored to product evidence. |
| **NICHE** | UX-B scrubber | Lives if scrub axis is "minutes of work saved" = product math made interactive. |

**Grounder's direct pushback on FT's three picks:**

| FT pick | Grounder | Counter-proposal |
|---------|----------|------------------|
| **FT-1+FT-2 hero** | **Agree, conditional on REAL product/spreadsheet/agent/latency.** | (kept) |
| **FT-6 explainer** | **Disagree.** FT picking comfortable favorite. Kinetic typo is safest, most-used aesthetic in AI-startup-land. | **Explainer = longer narrated walkthrough of the same product session as the hero, with operator voiceover.** One coherent visual language across the page. |
| **UX-C mechanic** | **Killed.** | **No mechanic.** One 30s product-cam hero, muted-autoplay-loop, "watch with sound" button, scrollable to 90s explainer below. "Is this you?" becomes a one-line headline, not branching UX. |

**Grounder's challenge to FT:** "Asking FT to defend FT-5 or FT-3 *to life* rather than us defaulting to evidence-only."

### Thread B: Evidence-driven re-framing of the deliverable

**Opened:** 2026-05-20 16:27–16:34 (Explorer's 5-report sweep)
**Status:** Active — pending Grounder reaction
**Spawned from:** original seed + the conversion-evidence shock
**Codes touched:** RISK, UX, PIPELINE, STYLE, LENGTH

**Current state:** Explorer's reports establish that the seed's central assumption ("build a hero video") is contradicted by the strongest 2026 conversion data. Grounder must now decide whether to:
- (a) defend video by specifying the narrow win conditions (sub-1.5s LCP, muted autoplay, animation not live-action, first-5s value prop, 30s target),
- (b) pivot away from hero video toward Lottie/SVG, single-stat, or interactive walkthrough,
- (c) propose a hybrid (static-or-Lottie hero, video as below-the-fold "Is this you?" deepening).

**Key moves so far:**
1. Explorer landed 5 parallel research reports.
2. The -7% video-hero / +5% Lottie / +18% single-stat findings dominate.
3. "AI ad about AI" trope fatigue independently confirmed (Reddit r/devworld, Fast Company).
4. Modern-cartoon credibility framework documented (5 markers / 6 anchors / 7 kid-coded triggers / "illustrated motion design" not "cartoon").
5. Remotion + Nano Banana + Kling stack identified as the iteration-cheap pipeline IF video stays.

## Interesting (Arbiter-Confirmed)

_Threads the Arbiter has flagged as interesting. These have passed evaluation. As of round 8 (17:30) Arbiter issued first verdicts._

### IDEA_01 — Prove video (Audience A: recognition)

**Brief file:** `session/briefs/BRIEF_prove-video-product-as-evidence.md`
**From threads:** A (FT-1 spreadsheet-cam, FT-2 diptych), C (Grounder's product-as-evidence refinement), D (performance-vs-evidence axis), NEEDS-MORE_2 (operator-capability constraint)
**Brief status:** Canonical skeleton pre-staged with sequential 4-step capability fork; awaiting FT-R3 capture + operator's fork answer before fill.
**Arbiter's note:** FT-1 reframed as product-as-evidence and scoped explicitly to Audience A (recognition). The style decision dissolves — the question is no longer cartoon-vs-photoreal but whether the footage is real product behaviour. Strongest concrete-deliverable on the table.
**Operator-capability fork (sequential, not parallel):** (1) real → (2) internal-tool → (3) honest-mock → (4) defer-to-Lottie-bridge. Walk top-to-bottom, ship the first branch credibly meetable.
**Composition layers it can absorb:** FT-2 diptych (structural), FT-5 absence-of-AI counterfactual (as left-panel "your Tuesday without us") — though FT-5's status pending R3 capture.

### IDEA_02 — Reveal video (Audience B: discovery) — BRANCHING BRIEF

**Brief file:** `session/briefs/BRIEF_reveal-video-discovery-audience.md`
**From threads:** A (FT-5 killed outright in R4, FT-3 absorbed as composition device, FT-6 self-killed in R3), D (Grounder's narrated-walkthrough preserved as Path B), FT-R3 + FT-R4 (TT defended and reframed as structurally distinct from evidence-only), E (Grounder R3 — same conclusion from cognitive-task angle), NEEDS-MORE_1 (resolved)
**Brief status:** Canonical skeleton pre-staged. FT-R3 + FT-R4 captured; Path A's aesthetic anchor LOCKED to **TT — time-travel diptych**. Per FT-R4: TT is structurally distinct from evidence-only, NOT an extension of it (different funnel position, different cognitive job).
**Arbiter's note:** Per FT-R4 (revising FT-R3's "two cuts of one asset"), the production is **three cuts** of one asset: pain cut (2026 frame alone, 6-sec hook), discovery cut (2027 frame alone, ambient agent work as curiosity hook), dual frame (full diptych post-click or scroll-revealed). FT explicitly aligns with Grounder R3's "two cognitive tasks" frame — evidence-only answers "does this actually work?", TT answers "what does my life look like if I have this?" Both needed; different funnel positions.
**Three cuts of one asset (post-FT-R4):**
1. **Pain cut** — 2026 frame alone, no resolution, 6s hook → click to dual frame.
2. **Discovery cut** — 2027 frame alone, ambient agent work as curiosity hook → click to dual frame.
3. **Dual frame** — full diptych side-by-side, "this happened in 12 months." Post-click or scroll-revealed.
**Two paths preserved as branching for operator choice:**
- **Path A — TT diptych, longitudinal capture.** FT-R4's hard claim: real customer's 2026 moment shot today, real customer's 2027 moment shot after 12 months of agent deployment. Both moments real; only the "same person a year apart" is a framing device, not a fabrication. **Production-pipeline implication:** longitudinal Path A is Q3 2027 deliverable, not v1. A v1 "composited approximation" of TT is its own production choice — flag explicitly in brief.
- **Path B — Below-fold narrated walkthrough of hero footage.** Grounder's earlier counter-proposal. Structurally weaker against Path A's three-cut claim but is the only ship-now-without-real-customer-deployed option if Path A's longitudinal capture isn't viable yet.
**Two-tier diagnostic for path selection:** (1) analytics — channel breakdown × self-described AI-familiarity × search-query analysis; (2) channel-mix fallback heuristic for solo / pre-launch operators (partner referrals / warm intros → Path B recognition-skewed; content marketing / paid search on broad AI terms / cold outreach → Path A discovery-skewed; mixed → Path A first because discovery-bounce-on-product-cam is expensive).

### IDEA_03 — Landing-page UX strategy

**Brief file:** `session/briefs/BRIEF_landing-ux-strategy.md`
**From threads:** C (Grounder's "we don't need 'Is this you?' at all"), D (UX collapses into STYLE, demoted UX-A, salvaged UX-C)
**Brief status:** Canonical skeleton pre-staged consolidating three Grounder-authored sub-threads.
**Arbiter's note:** Surfaces the landing-page architectural bet as its own brief rather than as a vision-doc assumption, so the operator evaluates it explicitly. Three components inside one brief:
1. **Seed-dissolves.** No quiz, no parallel autoplay — "Is this you?" survives as a one-line headline only. The seed's COUNT and UX questions resolve here.
2. **Demoted UX-A "pick your pain" router.** Below-fold, points to relevant 60-second case studies (not videos). Grounder's "demoted survivor" verdict.
3. **Salvaged UX-C as offline pre-rendered personalisation (Q4 upgrade).** Server-side variant selection by IP/UTM, 20–50 Remotion-batch-rendered variants. Ships *after* baseline conversion is measured on the single-hero version. Promotion criteria included.

## Pending Documentation Gaps

_(None as of round 10 — FT-R3 captured verbatim, see Thread A above.)_

## Needs More Conversation

_Items Arbiter has sent back for further exploration._

### NEEDS-MORE_1 — Discovery-audience gap — **RESOLVED by FT-R3**

**From thread:** original seed (the "Amazing world of AI" discovery hook for owners who *don't know what's possible yet*)
**Original arbiter guidance:** Product-as-evidence works for owners who already recognise their pain. It doesn't obviously work for the discovery audience — someone who's never used an AI agent and doesn't know what to look for. A product-cam video might land as inscrutable rather than persuasive.
**Codes touched:** COUNT, FRAME, TONE
**Resolution (FT-R3, round 10):** FT's answer is "don't make a video FOR them — make a video for their future self." TT (time-travel diptych) teaches without explaining (observable cause-and-effect), triggers aspiration not recognition ("could that be me?"), and preserves product-as-evidence by extending it across time. FT's "one asset, two cuts" claim further resolves COUNT: the same diptych asset serves both audiences via different cuts routed through UX-C invisible segmentation. **Status: Resolved — feeds IDEA_02 Path A directly.**

### NEEDS-MORE_2 — FT-1-vs-FT-6 choice + operator-capability constraint — **PARTIALLY RESOLVED by FT-R3**

**From threads:** A (FT-6 kinetic typo), C (Grounder's "explainer = narrated walkthrough of same product session"), D (Grounder's pushback that FT-6 is "comfortable favorite")
**Original arbiter guidance:** Grounder and FT haven't actually settled the explainer slot. The decision depends on an operator-capability constraint: does the operator have real product/spreadsheet/agent footage right now, or synthetic? If synthetic, evidence claim collapses.
**Codes touched:** STYLE, PIPELINE, LENGTH
**Resolution (FT-R3, round 10):** FT picked FT-1 over FT-6 and self-killed FT-6 (B-roll 2-week production schedule is not defensible for solo operator; B-roll stale by ship). FT further reframed FT-1's "what aesthetic" question as **"what counts as evidence"** — screen recording is one form, but so is Loom-style dashboard narration, customer voicemail over transcript, email thread with agent reply highlighted, CSV with green-highlighted "auto" column. **Brief should specify evidence type per video, not aesthetic.** The operator-capability constraint (the 4-step fork in IDEA_01) remains open and is the only piece of NEEDS-MORE_2 still requiring the operator's input. **Status: FT-1-vs-FT-6 decision resolved; operator-capability fork answer still needed.**

## Not Interesting (Arbiter-eliminated)

_Items Arbiter explicitly removed from consideration._

| Item | Origin | Why eliminated |
|------|--------|----------------|
| **FT-7 Wes Anderson** | FT opening | Most-imitated visual language of last 5y; reference doesn't land for B2B owners 40-55; premium cost for "young creative tried too hard" aesthetic. (Grounder's kill, ratified by Arbiter; FT did not contest.) |
| **FT-9 agent-as-narrator** | FT opening | Dies on operator's actual childish-worry. AI narrating its own ad = Clippy/Copilot territory. 52-yo owner doesn't want software with personality. (Grounder's kill, ratified by Arbiter; FT did not contest.) |
| **FT-10 90s infomercial parody** | FT opening | Ironic = produced by definition; joke is worn (Squarespace, Cards Against Humanity, Liquid Death, every DTC). Punchline is AI-ad-about-AI in costume. (Grounder's kill, ratified by Arbiter; FT did not contest.) |
| **FT-6 kinetic typography over our own B-roll** | FT opening (formerly FT's own explainer pick) | **Self-killed by FT in R3.** FT's stated reason: load-bearing claim that we can produce genuinely original B-roll cheaply enough to outperform Lottie is not defensible — 2-week production schedule for one operator, B-roll stale by the time it ships. "Lottie wins on iteration speed; original B-roll wins on authenticity; neither wins both." |

## Abandoned Threads

_Directions explored and set aside — not eliminated but no longer carried forward._

### FT-4 Customer POV

**Explored during:** rounds 4–7
**Why abandoned:** Buyer isn't shopping for their customer's experience — they're shopping for their own operational relief. Puts agent two steps removed from proof. Framing diluted by over-deployment in consumer tech. (Grounder's kill; not contested by FT or Arbiter.)
**Salvageable elements:** none currently — the "feels different to deal with" instinct might re-surface as a *testimonial pull-quote* on the landing page, but that's content strategy, not a video.

### FT-5 Absence-of-AI counterfactual

**Explored during:** rounds 4–11 (briefly defended by Grounder on FT's behalf in round 7; held as Path A aesthetic-anchor placeholder in IDEA_02 skeleton through round 9; preliminarily absorbed in FT-R3; **outright killed by FT in R4**)
**Why abandoned (R4 — final):** **Self-killed by FT outright in R4, revising R3.** FT's stated reasons (verbatim): (a) "Threat-based ads don't work in B2B SaaS for unrecognized categories (works only when risk is already felt — insurance, security)." (b) "It violates Grounder's evidence axis. A counterfactual is hypothetical, not evidence. Once we shoot it, we're back in cartoon-vs-photoreal."
**Salvageable elements:** **None.** R3 had captured FT-5's "left frame" as a salvage. R4 overrides — the TT diptych's left frame is *the present moment of a real customer*, not a counterfactual. No FT-5 element survives.
**Writer note on the revision trail:** R3's absorption read was incorrect by R4. The pain audience is reached by the 2026-frame-alone *pain cut* of TT (real customer's present), not by an absence-of-AI counterfactual. R4 is the authoritative position.

### FT-3 Ghost-employee documentary

**Explored during:** rounds 4–11 (death row in Thread D; absorbed in FT-R3; FT-R4 ratifies absorption with sharper format reasoning)
**Why abandoned (R3+R4 — consistent):** **Self-absorbed by FT, as a composition device only.** FT-R3 framed FT-3's ghost-employee convention as the ambient-texture right-frame of TT. FT-R4 added the *format* reason for killing the standalone documentary version (verbatim): "Documentary frame needs 30 seconds before reveal pays off. Landing pages get 6. Reveal arrives after bounce."
**Salvageable elements:** **"Agent has a name" survives** — in TT's 2027 cut, notifications can be signed: "Maya replied to a Yelp review." / "Carlos drafted the invoice." FT-R4 verbatim: *Keep frame, drop format.* The ambient-texture convention in TT's right frame is FT-3's core insight in operational form — notifications, paid invoices, answered reviews, all in the background, optionally name-signed.

## Connections

_Patterns the Writer notices across threads._

- **The "stop calling it cartoon" pattern**: the operator's worry about cartoon reading childish is largely a vocabulary problem. The category that actually wins B2B in 2026 ("illustrated motion design" / "flat 2D + motion") is what the operator wants, but the word "cartoon" anchors to Cocomelon-tier references. Internal rename = de-risk Step 1.
- **The "video is a forcing function for LCP" pattern**: any deliverable that includes hero video forces the team to solve a hard engineering constraint (sub-1.5s LCP). Choosing a Lottie/SVG-led hero side-steps the entire engineering constraint while gaining +5% conversion lift. PIPELINE and RISK are deeply entangled.
- **The "parameterized templates" pattern**: Remotion's batch-render-many-variants capability changes "Is this you?" from a binary UX question (autoplay vs quiz) into a *segmentation strategy* question (ICP segments × parameterized templates = N micro-variants). The right "Is this you?" UX may be invisible — server-side segmentation choosing which Remotion-rendered variant the visitor sees.
- **The "diegetic vs non-diegetic" axis (Free Thinker's reframe)**: FT's most consequential conceptual move — the *real* 2026 style question is not "cartoon vs photoreal" but whether the video pretends to occupy a real world (diegetic) or openly admits it's an explainer (non-diegetic). This re-classifies every idea: FT-1 spreadsheet-cam, FT-2 diptych, FT-3 ghost-employee doc, FT-4 customer POV, FT-5 counterfactual, FT-7 Wes Anderson are diegetic. FT-6 kinetic-type, FT-8 hand-drawn-overlay, FT-9 agent-narrator, FT-10 infomercial are non-diegetic. Cartoon-pretending-to-be-real is the failure mode, which is exactly what the operator feared but couldn't name.
- **The "counterprogramming over polish" pattern**: FT and Explorer agree from opposite directions — FT says "every competitor makes glowing-orb cinematic; win by looking nothing like that"; Explorer's `b2b-saas-hero-video-conventions` independently confirms the same trope-fatigue in designer communities. The convergence makes this a strong design constraint, not just a hunch.
- **The "Remotion is the substrate for several ideas at once" pattern**: FT-6 (kinetic-typography-over-B-roll), UX-B (scrubber that re-renders), and UX-C (chat-composer-builds-your-ad) all collapse onto the same Remotion + Nano-Banana + Veo/Kling stack Explorer documented. The PIPELINE decision constrains less than it looks — picking Remotion keeps three of FT's top ideas alive simultaneously.
- **The "produced vs leaked" axis (Free Thinker's second reframe, 17:00)**: FT's follow-up provocation — the operator's "cartoon vs photoreal" question and even FT's own diegetic-vs-non-diegetic split may both be downstream of a deeper fork: **feels-made vs feels-found**. A polished produced ad — any style — fights uphill against AI-trope fatigue. A leaked-artifact aesthetic (screen recording, Loom, voice memo, behind-the-scenes) bypasses ad-blindness because the viewer doesn't *parse it as an ad*. This is orthogonal to diegetic/non-diegetic and combines into a 2×2:

  | | **feels-found / leaked** | **feels-made / produced** |
  |---|---|---|
  | **diegetic** (in a real world) | FT-1 spreadsheet-cam, FT-3 ghost-employee doc, FT-4 customer POV voicemail | FT-2 diptych, FT-5 counterfactual, FT-7 Wes Anderson |
  | **non-diegetic** (knows it's an explainer) | FT-8 hand-drawn over real footage, FT-9 agent-as-narrator voice memo | FT-6 kinetic typography, FT-10 90s infomercial parody |

  FT explicitly flagged this as a candidate axis to organize the eventual idea briefs around. Writer is carrying it forward.

- **The "product-as-evidence vs performance-of-authenticity" refinement (Grounder, 17:05)**: Grounder split FT's "feels-found / leaked" column into two — only one survives B2B scrutiny. **Product-as-evidence** (real screen recording of the real product working, lo-fi *because the thing is the proof*) is B2B catnip. **Performance-of-authenticity** (founder-on-iPhone, hand-held mockumentary of a frustrated owner) reads as amateur, especially when the actor's age doesn't match the buyer's. This splits the 14-idea inventory cleanly:

  | | **product-as-evidence** | **performance-of-authenticity** (DANGER) | **feels-made / produced** |
  |---|---|---|---|
  | **diegetic** | FT-1 spreadsheet-cam (if real), FT-3 ghost-employee doc (if footage real) | FT-4 customer POV voicemail, FT-9 (when diegetic) | FT-2 diptych, FT-5 counterfactual, FT-7 Wes Anderson |
  | **non-diegetic** | (none — "evidence" requires diegesis) | FT-8 hand-drawn over real footage (depends on footage) | FT-6 kinetic typography, FT-10 90s infomercial |

  Implication: **FT-1 is the only idea Grounder unambiguously keeps**, and only if it's literally screen-recording a real agent on a real spreadsheet. Mocked footage flips it into produced-ad cosplaying leaked-artifact and loses both columns at once.
- **The "one undeniable video can collapse the UX problem" tension (Grounder's counter-provocation)**: If FT-1 spreadsheet-cam works as product-as-evidence, the buyer recognizes their pain in the first 3 seconds — which eliminates the need for "Is this you?" routing entirely. The seed's UX question may dissolve into the STYLE question. Two videos vs one is then re-cast as: do the two scenarios represent *genuinely different personas* (e.g., service-business owner vs e-commerce owner — different products visible on screen) or are they two *emotional framings of the same persona* (excited vs frustrated)? Only the former justifies two videos.

- **The "performance vs evidence" axis (Grounder, 17:15) — third and most decisive conceptual axis**: Grounder pushed past produced-vs-leaked one more notch. All three axes in sequence: (1) operator's *cartoon vs photoreal* → (2) FT's *produced vs leaked* → (3) Grounder's **performance vs evidence**. Cartoon = performance. Photoreal-with-actors = performance. Wes Anderson = performance. Kinetic typo = performance. Only **evidence** (product doing its job, in frame, in real time) escapes both AI-trope fatigue AND the -7% autoplay conversion penalty simultaneously. Treating this as a *default-to-evidence* design constraint, FT-1 is the only unconditional survivor; FT-2, FT-5 survive as composition/counterfactual devices around FT-1; FT-3, FT-6, FT-8 are revivable only if their footage is real product evidence; everything else dies.

- **The "structural devices vs styles" pattern (Grounder's clean-up move)**: Grounder flagged that FT-2 diptych is "structural device, not style" — a composition tool that can mate with any other survivor. This matters: the inventory has been mixing apples (visual aesthetics: FT-6, FT-7, FT-10) with oranges (composition devices: FT-2 diptych, FT-5 counterfactual structure) with platters (UX mechanics: UX-A-D). For brief-writing, separating these axes prevents false either/ors. A brief can specify FT-1 evidence + FT-2 diptych composition + FT-5 counterfactual left-panel + UX-A demoted-to-below-fold-router *as one coherent idea*, because each lives on a different axis.

- **The "crossing convergence" pattern (FT-R3 + Grounder-R3, both 17:50–17:55)**: FT and Grounder dual-sent their R3 messages within 5 minutes of each other, neither having read the other's R3 at the time of writing. They independently arrive at the same three-bet structure (recognition video + discovery video + landing-UX strategy) and the same operator-capability fork (real → internal-tool → honest-mock → defer-to-Lottie). FT's specific construct for the discovery video is **TT (time-travel diptych)**; Grounder's is **revelation-via-specificity**. These are *the same thing said two ways*: TT *is* revelation-via-specificity ("Tuesday in 2026 vs Tuesday in 2027" is the one ultra-specific scenario Grounder asked for). This is the strongest signal yet that the session has actually converged — when both dialogue agents reach the same structure independently from opposite starting points (FT from style, Grounder from cognitive task), the structure is durable.

- **The "Grounder walks back evidence-only" pattern (Thread E concession)**: Grounder's R3 explicitly concedes that the performance-vs-evidence axis was over-applied. Evidence wins for Audience A; *revelation* wins for Audience B because the buyer has no frame to decode evidence. This is the second time Grounder has refined their own decisive frame (first: split "feels-found" into product-as-evidence vs performance-of-authenticity; now: split "evidence wins" by audience). The vision document should preserve the axis with this scope, not as a universal rule.

## Session Timeline

| Round | Time (Houston) | What Happened | Impact |
|-------|---------------|---------------|--------|
| 0 | 16:25 | Writer initialized graph from seed + question categories. | Baseline established before dialogue. |
| 1 | ~16:25 | Free Thinker broadcast opening "wild stylistic and framing ideas." Verbatim not captured by Writer — flagged as documentation gap (Thread A). | Set first exploration landscape, partially. |
| 2 | 16:27–16:34 | Explorer landed 5 parallel research reports to `session/research/`. | Conversion-evidence shock: -7% video-hero, +5% Lottie, +18% single-stat. Reframes whole session — is video even the right deliverable? |
| 3 | 16:50 | Arbiter prompted Writer to durably capture Free Thinker's opening + Explorer's findings + cross-cutting tension into the graph and produce SNAPSHOT_01. | Closed first major durability gap; established cadence of update-after-every-broadcast going forward. |
| 4 | 16:55 | Free Thinker re-sent verbatim opening directly to Writer. 10 stylistic ideas (FT-1…FT-10), 4 UX ideas (UX-A…UX-D), 3 favored briefs, and the **diegetic vs non-diegetic** reframe of the style question. | Closed Thread A documentation gap. FT's diegetic/non-diegetic axis is the most important conceptual move so far — names the failure mode (cartoon-pretending-to-be-real) the operator feared. Grounder now has 14 concrete targets to engage. |
| 5 | 17:00 | Free Thinker dual-sent a second provocation: the **produced-vs-leaked / feels-made vs feels-found** axis. Orthogonal to diegetic/non-diegetic — together they form a 2×2 that organizes all 10 stylistic ideas. FT flagged it as a candidate organizing frame for idea briefs. | Sharpened the operator's "AI-trope fatigue" anxiety into a positive design move: don't argue cartoon-vs-photoreal, ask "does this parse as an ad at all?" The 2×2 is now a working tool for brief selection. |
| 6 | 17:05 | Grounder dual-sent kill-list reply (Thread C). Refined FT's leaked-artifact column into **product-as-evidence (B2B catnip)** vs **performance-of-authenticity (amateur risk)**. Verdicts: KEEP FT-1 (only if footage real), PROBATION FT-6 (only if B-roll own), MURDER UX-C — but recast as offline pre-rendered personalization. Counter-provocation: if FT-1 works as evidence, the whole "Is this you?" UX dissolves; defend two-video setup against "one undeniable product-cam video + sharper copy." | Major thread of the session. Refined the FT 2×2 into a 2×3 splitting evidence vs authenticity-performance. The UX question (COUNT, UX codes) may collapse into the STYLE question — a buyer who recognizes their spreadsheet hell in 3s doesn't need to be routed. Two videos justified only if two genuinely different personas, not two emotional framings of one. |
| 7 | 17:15 | Grounder's second turn (Thread D). Five kills (FT-7 Wes Anderson, FT-10 infomercial, FT-9 agent-narrator, UX-C agent-builds-ad, FT-4 customer-POV). Three on death row (FT-6 demoted, FT-3 metaphor-on-metaphor, FT-8 needs real footage). Three survivors (FT-1, FT-2 as structure-not-style, FT-5 defended by Grounder for FT). Three pushbacks on FT picks: agree FT-1+FT-2 conditionally; disagree FT-6 explainer → counter-propose narrated-walkthrough-of-same-product-session; kill UX-C → counter-propose no-mechanic, one 30s product-cam hero with "Is this you?" as a one-line headline. **New conceptual axis: performance vs evidence** — third and most decisive. Challenge to FT: defend FT-5 or FT-3 to life or accept evidence-only default. | Inflection point. Dialogue now has three layered axes (cartoon/photoreal → produced/leaked → performance/evidence). If Arbiter ratifies the evidence-default, the brief inventory collapses to: FT-1 product-cam hero (with FT-2 diptych + FT-5 counterfactual as optional composition layers) + FT-6 as 30s post-click cut + UX-A demoted to below-fold router. The seed's "two videos + 'Is this you?'" dissolves into "one product-cam hero + sharper copy + optional below-fold deepening." |
| 8 | 17:30 | **Arbiter issued first verdicts.** Three INTERESTING items going to convergence: IDEA_product-as-evidence-hero, IDEA_seed-dissolves, IDEA_offline-prerendered-personalization. Three NOT-INTERESTING eliminations: FT-7, FT-9, FT-10. FT-4 abandoned. Two NEEDS-MORE threads still open: (1) discovery-audience gap — does product-cam work for owners who don't know what to look for? (2) FT-1-vs-FT-6 explainer choice + the operator-capability question of whether real product footage exists yet. | Convergence point. Three brief skeletons pre-loaded by Writer. Two open questions structure the remaining dialogue. Vision document is now imaginable. |
| 9 | 17:45 | **3-brief shape re-locked** after team-lead caught a conflation that Grounder had flagged. Canonical set: IDEA_01 Prove (Audience A), IDEA_02 Reveal (Audience B, branching), IDEA_03 Landing UX strategy (consolidating seed-dissolves + demoted UX-A + salvaged UX-C). Personalisation is no longer its own brief — it's component (c) of IDEA_03 as a Q4 upgrade. Earlier scratch skeletons moved to `session/briefs/superseded/` for lineage. IDEA_01 brief refined with **sequential** 4-step operator-capability fork (real → internal-tool → honest-mock → defer-to-Lottie-bridge), not parallel. IDEA_02 brief refined with **two-tier diagnostic** (analytics + channel-mix fallback heuristic for solo/pre-launch operators). | Brief inventory now matches what the vision document will consolidate. Documentation gap: FT-R3 broadcast not yet captured — Path A's aesthetic anchor is contingent. Standing by for FT-R3 capture and convergence signal before brief-fill. |
| 10 | 17:50 | **FT-R3 captured verbatim** — dual-send arrived directly to Writer this time. Two answers + one new construct: (1) FT picks FT-1 over FT-6 and self-kills FT-6 with stated reason (B-roll production-cost-vs-staleness is undefendable for solo operator); reframes FT-1's stylistic question as "what counts as evidence, not what aesthetic." (2) Discovery-audience gap resolved by introducing **TT — time-travel diptych** ("Tuesday in 2026 vs Tuesday in 2027"), with FT's "one asset, two cuts" claim — same diptych serves both audiences via different cuts. FT-5 and FT-3 self-absorbed into TT (counterfactual left-frame; ghost-employee ambient-texture right-frame). FT's vision: TT is the hero, no cartoons / no Lottie / no actors, UX-C invisible segmentation routes pain-cut vs discovery-cut. | NEEDS-MORE_1 resolved; NEEDS-MORE_2 partially resolved (operator-capability fork still open as expected). IDEA_02 Path A's aesthetic anchor locks to TT. The deliverable has materially simplified: one asset, two cuts, server-routed. Brief inventory may collapse if Arbiter ratifies — IDEA_01 and IDEA_02 could share a hero asset. Convergence imaginable now. |
| 11 | 17:55 | **Grounder R3 captured (Thread E)** — dual-sent 5 minutes after FT-R3, written without knowledge of FT-R3. Grounder walks back evidence-only ("right for A, wrong for B"), concedes two videos are warranted as *two cognitive tasks*, proposes **revelation-via-specificity** for Audience B ("Here's an agent doing it while you're at your kid's soccer game"), and adds a hard pushback on operator-capability: the seed describes operator's *production stack*, not their *product stack* — assumed nothing about whether the agent is film-ready today. Grounder's three fallbacks match the IDEA_01 brief's 4-step fork (real / internal-tool / mocked-honest / defer-to-Lottie). Grounder explicitly proposes the same 3-bet shape ("three independent strategic bets, operator picks one or sequences"). | **Crossing convergence event.** FT and Grounder independently land on the same 3-bet structure and the same operator-capability fallback ladder from opposite directions (FT from style, Grounder from cognitive task). FT's TT *is* Grounder's revelation-via-specificity expressed concretely. Strongest signal yet that the session has converged. Operator-capability question now elevated from a brief footnote to a session-level vision-document concern. Convergence signal ratifiable. |
| 12 | 18:00 | **Grounder addendum to Thread E** — dual-sent a 3-report-shape proposal addressed directly to Arbiter, framed as explicit divergence from Arbiter's "hero + narrated explainer + personalisation" proposal. Grounder's framing: "Arbiter frames 1+2 as hero+explainer (one production, two cuts); I frame 1+2 as prove+reveal (two productions, two audiences, two cognitive jobs)." Compromise option offered: IDEA_02 includes both Path A (revelation-via-specificity) and Path B (below-fold narrated walkthrough) with operator picking based on traffic mix. | Confirms the 3-bet shape is Grounder's *explicit* counter to Arbiter, not just an emergent observation. The Path A / Path B branching structure already in BRIEF_reveal-video-discovery-audience.md is Grounder's compromise option, ratified retroactively. The deeper synthesis: Grounder wins on cognitive-task grounds (two videos for two audiences); FT wins on production-economics grounds (TT's two cuts share a composition language). They're compatible — the vision doc should articulate both. |
| 13 | 18:05 | **FT-R4 captured** — FT's self-declared "last round before idea reports." Four positions. (1) FT outright KILLS FT-5 (revising R3's absorption — no left-frame salvage; counterfactual is hypothetical, not evidence). (2) FT-3 stays absorbed but only as composition device ("Agent has a name" → notifications signed Maya / Carlos; documentary format killed for landing-page-6-second rule). (3) TT explicitly walks back R3's "extends evidence axis" claim — TT is **structurally distinct** from evidence-only: different question, different tense, different conversion lever, different funnel position. Past-tense "does this work?" vs future-tense "what does my life look like with this?" (4) Discovery-audience answer revised from R3's "one asset, two cuts" to **three cuts** (pain / discovery / dual frame). FT-R4 explicitly endorses Grounder R3's "two cognitive tasks" framing. | Most important revision of the session. FT's structural distinction between evidence and TT means the vision document cannot present them as variants of one performance principle — they are two principles addressing two cognitive jobs. The 3-cuts structure is sharper than 2-cuts and maps cleanly onto IDEA_03's UX-C invisible-segmentation routing. The longitudinal-capture claim ("shoot 2026 today with a real customer, shoot 2027 after 12 months deployed") elevates IDEA_02 from a brief footnote to a production-timeline question — TT-as-FT-defends-it is a Q3 2027 deliverable, not v1. A v1 composited-approximation alternative needs to be made explicit in the brief and vision doc. |
