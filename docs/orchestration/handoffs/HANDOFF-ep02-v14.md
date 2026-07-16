# HANDOFF — EP02 v14: the ~9:00 rebuild

> Written 2026-07-16 by session `c29aecb3` (the v12 regrammar + v13 pace passes).
> **You are a fresh session.** v13 (14:18) is signed into `main` and gated green.
> The operator has now taken the brakes off and asked for **~9:00 + a full graphics
> recut**. That is a **rebuild, not a pass** — it is why this handoff exists rather
> than a half-finished v14. Everything expensive has already been measured; the
> numbers below are load-bearing, don't re-derive them.

## 0. The operator's brief, verbatim

> *"You have my full permission to take artistic license. Cut the fluff, shrink it
> down, speed up the talk, shrink the gaps. All the things you're suggesting here.
> Just get it done. It'll be better. This video isn't too sexy. It's just about two
> different environmental approaches. They're important, but we shouldn't be going
> on forever. This thing could probably be cut by 50%, so go for it. That'll be a
> much easier effort for me to then decide how to edit it. And now that you have a
> totally rebuilt ClearSpring Studio mechanism, why don't you take a recut at all
> the graphics as well? We might as well try to see if we can make it much better
> based on where we currently stand, not whatever this was pinned to when we
> started it. Go ahead and go for it, and impress me."*

**Editorial key:** *useful topic, not a sexy one.* Every beat earns its seconds
against that. His stated reason for wanting it short: a tighter cut is easier for
him to review. **Target ~9:00.** 9:40 tight beats 9:00 gutted. 13:00 is failure.

## 1. THE UNLOCK — read this first, it is the whole plan

The two asks are not independent. **They unlock each other.**

- The **pause-trim** is the best lever we have: the Charon wavs are **21–27%
  silence** (`p2_premise`: 18.6s of >0.45s pauses inside 90s; `p5_pricing_v2`:
  12.5s inside 47s). It buys ~11% with **ZERO** speech alteration — strictly
  better than time-compression, because you remove gaps rather than squeeze words.
- It was **blocked** through v13 for one reason: every comp's GSAP beats are
  choreographed to the *current* reads, so pulling silence out shifts each word
  left and desyncs every fly-in.
- **The graphics recut re-times those comps anyway.** The blocker dissolves the
  moment you rebuild. Do the pause-trim *as part of* the rebuild, not after it.

**This is the only path to 9:00.** The arithmetic (measured, v13 = 857.5s):

| step | −s | running |
|---|---|---|
| v13 | — | **857.5** (14:17.5) |
| drop `agents_a` (the agent-era runway) | 111.0 | 746.5 |
| drop `access` (who-this-is-for cutaway) | 60.8 | 685.7 |
| drop `agents_b` (token bridge — dies with agents_a) | 14.2 | 671.5 |
| drop `receipt2` (concurrency caveat + dashboard) | 58.2 | 613.3 (10:13) |
| **pause-trim ~11% on what remains** | ~67 | **≈546s = 9:06** ✅ |

**Segment drops alone floor out at 10:13** — and that floor is crude, because it
gets there by deleting whole segments. The last 73 seconds only exist inside the
pause-trim. Hence: rebuild.

## 2. The measurement that governs every cut decision

Do not re-litigate this; it is measured across all 1,077s of v11:

- **~8 seconds** of true dead air in the entire original episode (static picture
  AND no voice). It is gone as of v12.
- **53%** near-static picture — but almost all of it is a held artifact *under
  narration*, which is legitimate.
- **78%** wall-to-wall narration by wav length (~99% by silence detection).

**There is no fat.** Every second comes out of *script*, *pace*, or *silence*.
Trimming held frames can never move this episode. That is why the levers are:
pause-trim → warp → content cuts → script rewrites.

## 3. What is already banked (do not redo)

- **v12 (#177)** — six grammar fixes, all still live: intro card rebuilt (it had
  shipped "FLIGHT DATA RECORDER" for 26s because the comp was fixed but its render
  never rebuilt), leaks → DATA-RECORDER, title `y 42→62`, mix separation
  (bed ×0.95 / VO ×1.05), EP02's own brand card with the real URL, premise's
  frozen tail trimmed. Also: `assemble_lib` gained the `async=1` fix it had never
  carried (EP02's own drifting-VO bug — a named backlog item, now closed),
  plus `bed_loop` and `vo_volume`. `verify_vo_onsets` no longer false-fails.
- **v13 (#178, #179)** — 1.10× per-segment warp + two content cuts.

## 4. Constraints that do NOT move

- **The branding signature is art-directed and unwarped**: the intro (avatar
  build→reveal arc — a calm 6.0s slice of the permanent loop that `build_intro.py`
  slows to fill *exactly* 26.2s — plus the DATA RECORDER terminal) and the brand
  card. The operator calls it "the canonical brand capture". v13 exempted both
  from the 1.10× after a frame-compare showed the warp re-times a composition
  built to a length, for 2.3s. **Keep them exempt. Do not restyle them.**
- Charon only. **"ZAI" is narrated as the letters Z-A-I.**
- "Black Box AI" early + in pleas; plain "Black Box" mid-flow.
- **No colour grade on synthetic terminal renders** (clearspring #123).
- All three gates green: frame · `verify_vo_onsets.py` · bed-RMS-to-end.
  **Never ship a degraded read to hit a number.**
- v11/v12/v13 renders are never overwritten. Suffix everything `_v14`.
- **Publishing is held by the operator.** Nothing goes public.

## 5. Execution plan

### 5a. Narration first (it drives everything)
1. **Rewrite the surviving script for the "useful, not sexy" bar.** A Charon
   re-cut is cheap (`scripts/tts_batch_gemini.py TEXTS.json OUT --voice Charon`,
   `--style` exists). Verbose beats get rewritten, not preserved. Known bloat that
   survived v13 and should be re-voiced tighter:
   - `p8a_modes_v2` (90s) — the "middle lane" personal-history detour (~25s).
   - `p8b_scaffold_v2` (53s) — the UV riff ("you don't have the scars", "cardigan", ~20s).
   - `p3d_kv` — already cut with the KV tangent; if the operator wants the *cache
     point* back (he may — it reverses his own v10 note), it is a 1-line re-record
     without the VPS story.
2. **Pause-trim every wav.** Port the logic from `scripts/cut_recording.py` — it
   already does exactly this for human takes (acoustic `silencedetect` unioned
   with transcript gaps, vetoed by word STARTS, asymmetric padding). TTS is the
   easy case: no room hum, no breathing. Trim each >0.45s pause to ~0.35s.
   **Measure the new wav durations — they are the spec the comps are built to.**

### 5b. Then the graphics, built to the new reads
Survey first (`git show origin/main:<path>` — **never `git checkout` in the live
tree**, see §7):
- `docs/CAPABILITY_AUTOMATION.md` + the cleared adoption queue (#112–#122):
  sub-composition correctness, kinetic_teaser rhythm chrome, declarative data-var
  templates, official-logo cascade.
- `templates/style_packs/CATALOG.md` — C1 nate-herk-editorial, C2 plain-talk-explainer.
  **Judgement:** a pack replaces a whole design language. EP02's look *is* the
  operator-ratified house baseline, and the signature is untouchable. Borrow
  *motion/pacing* ideas; do not swap the language wholesale.
- `scripts/build_outro.py` — the ratified outro standard (leak card with TRUE
  session receipts + snark → brand card). **This is the one adoption v13 left on
  the table and it is genuinely on-brand.** EP02's brand card already matches the
  standard's second half; it has no leak card. Do it.
- Standard BB framing chrome (#158) — EP02 already complies (brackets + REC +
  title). Verified on the v13 frame gate. Nothing to do.
- `exhibit_foundry/tools/terminal_treatment.py` + `highlight_callout.py` — the
  right tools for the long terminal holds (frame/zoom/annotate rather than a
  static full-frame hold). `PRODUCTION_LESSONS`: never leave a >20s hold static.
- `/frame-judge` — use it on your own render.
- Read `docs/PRODUCTION_LESSONS.md` before rendering. Two entries are mine:
  *a fix in a composition is not a fix until its render is rebuilt*, and
  *"static picture" ≠ "dead air" — measure both before cutting*.

**Bar:** every swap must visibly beat what's there. Presumption is rebuild, but
"modernised" and "better" are not synonyms.

### 5c. Assemble
`assemble_lib.assemble()` is the ONE house pattern — declare `SEGMENTS`, pass an
`overlay_fn`. **Do not fork it.** `assemble_ep2_v13.py` is a working thin caller;
copy its shape. Keep `bed_volume=0.399, vo_volume=1.05, bed_loop=True`.
In-segment offsets (leaks, the dial-up SFX) must ride any speed factor — v13
divides them by `SPEED`; get this right or the leaks land late.

### 5d. Gates
`gates_v13.py` is a working three-gate runner; copy it to `gates_v14.py` and point
it at the v14 plan. **Re-verify the bed-RMS explicitly** — shorter VO means a
shorter sidechain key, which is exactly the ep00 v6 dead-bed failure mode.

## 6. Reporting (the operator asked for this shape)
Report each lever's runtime contribution **separately** — pause-trim / warp /
cuts / rewrites — plus a **before-after on the graphics**, so he can see where the
9 minutes came from. Log every cut with a timestamp so he can argue any single
call. Flag loudly anything that reverses one of his own notes (the KV tangent
already does).

**Exhibit: update the SAME URL — `blackbox-ep02-v13-review` → rename the page to
v14 but keep the slug.** He has that link on his phone.
`--dir` publishes do NOT inject the review toolbar (the injector only runs in
`cmd_publish_file`) — pre-inject with
`scratch_publish._inject_review_toolbar()` before publishing, or he cannot mark it up.

## 7. Traps this session paid for — do not re-pay them
- **Never `git checkout` in `~/lrepos/clearspring-studio`.** It is parked on
  another session's branch (`ep01-v7-build`) and I staged 247 files over it by
  accident. Read with `git show origin/main:<path>`; work in a worktree off
  `origin/main`.
- **Symlink single FILES, not tracked directories.** `ln -sfn` over
  `projects/blackbox/assets` deleted six tracked files.
- `~/lrepos/universal_agent` is parked on a side branch whose `publish_scratch.sh`
  is 115 lines behind `origin/main` and has no toolbar injector. Publish from an
  `origin/main` worktree.
- The clearspring repo **rejects `--auto`** on `gh pr merge` (per the
  `/blackbox-episode` skill) — run `--squash --delete-branch` directly. headless
  accepts `--auto`.
- `renders/` and `narration_*/` are gitignored; commit source only.

## 8. Open operator questions (unanswered, carry them forward)
1. **Ear-check the 1.10×** — A/B clips (1.00/1.10/1.15) are in the v13 exhibit.
   1.10 came back indistinguishable and 1.15 clips plosives, but **that verdict is
   a model's ear, not a human's.** If he says 1.10 sounds off, drop to 1.05.
2. **The KV cache point** — cut with the tangent, reverses his own v10 note.
3. **EP01 v8's assembler is not on `main`** — `assemble_episode_v8.py` (`4fce2c2`)
   lives only on the unmerged `ep01-v7-build`, though EP01 is signed off at v8.1.
   Not EP02's lane, but it will bite someone.
