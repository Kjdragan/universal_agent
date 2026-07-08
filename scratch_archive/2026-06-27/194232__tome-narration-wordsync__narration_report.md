## Can we match the narration highlight to the voice word-by-word?

Short answer: yes, and the highest-leverage fix is almost free — the data needed for accurate word-sync is **already computed and already sent to the browser, then thrown away**. Below is how it works today, why it drifts, the options grouped by the four dimensions, and a recommended sequence. Nothing here is implemented; this is options + recommendations only.

---

### 1. How narration word-sync works today

**Synthesis (server).** `POST /tts` (`routes.py::tts`, capped at `MAX_CHARS=1000`) calls `tts.py::synth`, which (a) normalizes the written text to read-aloud form via `text_normalize.py::normalize_for_speech` (numbers, dates, currency, abbreviations → words) producing `spoken`; (b) synthesizes audio with a **local CPU neural TTS** — Kokoro (`kokoro-onnx`) by default — in `tts.py::_kokoro_synth`. Critically, the engine returns **audio samples only**: no per-word marks, no SSML timepoints, no boundary events.

**Timing estimation (server).** Because the engine emits no timing, every per-word time is *estimated* in `timings.py::estimate_word_timings`: it builds an RMS energy envelope (25 ms frame / 10 ms hop), and places each word's end where cumulative energy reaches that word's *syllable-weighted* fraction of total energy (`_weights`/`_syllables` = vowel-group count). The module docstring states it outright: "an estimate, not phoneme-exact (that needs a forced aligner)." The response returns `words:[{i,start,end}]` (ms, indexed to the normalized `spoken` text) plus `spoken`.

**Highlighting (browser).** Here is the surprise. `+page.svelte::narrate` folds those server timestamps into a global timeline with a `baseMs` offset — and then **never uses them for the highlight** (`words` is read only as a non-empty gate in `onTime`; `activeIdx` stays `-1`; the transcript is built but never rendered). Instead, `syncPageLive` derives the active word by **linear char interpolation**: `charPos = narrChunkStart + (audioEl.currentTime/duration) * narrChunkLen`, then binary-searches a char-keyed `wordMap` (built by `buildWordMap`/`walkWordRanges`). The highlight itself is drawn as an SVG overlay via foliate's Overlayer (`drawWordHighlight`); page-turns use foliate's exact `scrollToAnchor`. The only clock is the `<audio on:timeupdate>` event, which fires at ~4 Hz.

So the live conversion chain is: **timeupdate (~4 Hz) → audio fraction of the current chunk → proportional char position → word.** The accurate, energy-anchored per-word times are dead code.

---

### 2. Why word-matching is imperfect (grounded causes)

1. **The real timings are ignored.** The dominant error: the highlight uses a *uniform chars-per-second* assumption, but speech is not uniform — pauses, emphasis, and long vs. short words all bend the true timing. Error grows within a chunk and only re-anchors at chunk boundaries. (`+page.svelte::syncPageLive` vs. the unused `words[]`.)
2. **Written length ≠ spoken length.** `narrChunkLen` is the *source* char count, but `audioEl.duration` is the duration of the *normalized* spoken text. On chunks with numbers/currency/abbreviations ('1999'→'nineteen ninety-nine', '$3.50'→'three dollars and fifty cents') the audio dwells far longer than the tiny written share predicts, so the highlight races then stalls.
3. **Coarse, jittery clock.** `timeupdate` ticks ~4 Hz, so at normal/fast speech multiple words elapse per tick and intermediate words are skipped — sub-250 ms transitions are unresolvable.
4. **The timing source itself is an estimate.** Even if consumed, the energy+syllable heuristic is the accuracy ceiling: energy≠progress (whispered/soft passages, breaths, trailing silence), and the syllable proxy mishandles silent-e, diphthongs, acronyms, and punctuation-only tokens.
5. **A coordinate gap with no bridge.** Normalization is a one-way string transform with no record mapping a displayed word to the spoken word(s) it produced, so nothing downstream can reconstruct the correspondence.
6. **Minor server desync:** the sanitize-retry path (`_backend_synth_safe`) can synthesize audio for a sanitized string while timings are computed on the pre-sanitization text.

---

### 3. Options to improve, grouped by dimension

Effort/payoff are carried from the per-dimension analysis. Debunked/infeasible items are excluded with a note.

#### A. Frontend sync precision (cheapest, highest ratio)
- **Consume the real timestamps (timestamp lookup, not char interpolation).** *Low-med effort, High payoff.* Drive the highlight from `words[].start/end` (already sent), bridging spoken-token index → `wordMap` range. Removes within-chunk drift and the length skew at once. Grounding: `syncPageLive`, `narrate` (timestamps folded then unused).
- **rAF tick instead of timeupdate.** *Low effort, Med-high payoff.* ~60 Hz polling of `currentTime` removes the ~250 ms quantization; existing per-word throttle keeps it cheap. Best paired with the above.
- **Audio output-latency compensation.** *Low effort, Small-med payoff.* Subtract a small lead (`outputLatency` or a fixed ~80–120 ms) so the highlight matches what's heard, not the decode clock — larger gain on Bluetooth. Only worth it after the base mapping is fixed.

#### B. Text↔audio token alignment (fix the coordinate gap)
- **Span-aware normalizer (displayed↔spoken provenance map).** *Med effort, High payoff.* Make `normalize_for_speech` also emit, per output span, the input span it came from; carry it through `synth` into the `/tts` response. Fixes the 1→N expansion drift at the root and makes the token bridge in (A) exact.
- **Quick mitigation: scale interpolation by spoken length.** *Low effort, Med payoff.* Interpolate over the spoken text length rather than the source length. A genuine stopgap, but strictly inferior to consuming the actual timestamps — only relevant if (A) is deferred.
- *(Debunked — excluded):* **"Unify the server/client tokenization rule."** Verification found `premise_real=false`: the client never transfers the server token index (it uses char interpolation), so unifying tokenization fixes a correspondence nothing currently uses. Not a sync win on the live code path.

#### C. Timing source fidelity (raise the ceiling)
- **CTC forced alignment at synth time (torchaudio MMS_FA / wav2vec2), CPU.** *Med effort, High payoff.* After `_backend_synth_safe`, align the in-memory audio against the synthesized transcript — the alignment half of WhisperX with no ASR (we already know the words). Replaces the energy heuristic with phoneme-grounded boundaries and fixes the sanitize-retry desync for free. CPU-runnable (no GPU needed).
- **aeneas DTW aligner (CPU, no torch).** *Low-med effort, Med payoff.* Lighter dependency, but fundamentally sentence-granular — word times are interpolated, so only modestly better than today.
- **Re-export the Kokoro ONNX graph to expose `pred_dur`.** *High effort, High payoff.* True per-phoneme durations with no torch runtime, but ONNX surgery + phoneme→grapheme→token mapping; mostly the same payoff as the CTC route for more work.
- *(Caveat — keep but note):* **Switch to a TTS that emits word/`<mark>` boundaries (cloud Polly/Google/Azure, reviving foliate's SSML path).** *Med-high effort, High payoff.* Verification: feasible **only as a cloud A/B**, not as a revival on the current engine (Kokoro/MeloTTS ingest no SSML and emit no marks). Abandons the deliberate local/private/offline/zero-cost design — warranted only if engine-native timepoints matter more than locality.

#### D. Forced alignment latency / persistence (the enabler)
- **Content-addressed `(spoken,voice,speed,engine)→{wav,words}` cache + per-chapter prewarm.** *Low-med effort, High leverage.* No quality gain by itself, but it removes the repeated ~12–18 s re-synth pain AND makes any heavier aligner (C) latency-invisible by amortizing it to a one-time per-chapter cost. Turns "forced alignment is too slow on CPU" into "runs once, then free."
- **Offline MFA/Gentle batch pre-pass.** *High effort, High quality.* Gold-standard boundaries with zero live latency, but heaviest install/build burden; only sensible paired with persistence + prewarm.

---

### 4. Recommendation — what to do first, and the sequence

The verifications make the ordering unusually clean: **the system already pays to compute good per-word timings and then ignores them.** So fix consumption before improving the source.

1. **First — consume the timestamps + rAF tick (Dimension A).** Low effort, no server change, no new dependency, and it removes the *dominant* drift (uniform chars-per-second) plus the written-vs-spoken length skew, and the ~4 Hz quantization. This alone should make word-matching visibly better. Add a low-confidence fallback to today's char-interp (reuse the existing `wordmap_misalign` signal) so it degrades gracefully.
2. **Second — span-aware normalizer (Dimension B).** Makes the spoken-token→displayed-word bridge in step 1 exact instead of heuristic, killing the number/currency/abbreviation drift class at the root. Medium effort, all CPU.
3. **Third — cache + per-chapter prewarm (Dimension D).** Independently valuable (kills the ~12–18 s re-synth wait) and the prerequisite that makes step 4 painless.
4. **Fourth, optional — CTC forced alignment (Dimension C).** Only now does raising the timing *source* quality pay off, because the client finally consumes it. Phoneme-grounded boundaries are the true ceiling; the cache hides the CPU cost.

**Tradeoffs / constraints to be honest about.** Steps 1–3 are pure CPU/client work and respect the VPS-has-no-GPU constraint with no quality compromise. Step 4 adds a torch dependency and per-chunk CPU latency to a lean ONNX server — acceptable only behind the step-3 cache. The cloud-`<mark>` engine route is the only path to *engine-native* timepoints, but it sacrifices the deliberate local/private/offline design and is overkill versus steps 1–4 for this app. Net: the first two steps are where almost all the perceived improvement lives, for a fraction of the effort of the rest.