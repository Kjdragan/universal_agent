# Handoff — continue the non-deterministic evaluation investigation

*This document SETS UP the next session; it does not do the investigatory work.
Read `loop_experiments/INVESTIGATION.md` first (the full record of where we are),
then this for the launch point, the new input (QLoop), and a ranked agenda.*

---

## 0. What the next session is for — and the strategy

Continue building a **non-deterministic evaluation capability** (judging report
quality, website look, agent output) that avoids the two failure modes we hit but did
not solve — **judge rubber-stamping** and **context contamination**.

**STRATEGY (operator decision — INVERT the relationship).** QLoop
(`kd_evaluator_harness`) is far more mature than our `loop_experiments` probe and
already ships solutions to both open problems. So we do **not** port bits of QLoop into
our lab. Instead **QLoop becomes the base**: clone it into a **new standalone repo** and
build our generator+evaluator module on top of it.

> **Hard ground rule: never touch the original `github.com/Kjdragan/kd_evaluator_harness`.**
> It must stay pristine. All work happens on the *clone* (a separate repo with its
> origin remote detached from the original). Read the original only.

The next session's job: stand up the clone, adapt it to our purpose, and **add** the
capabilities we still need (§4). §3 is the map of what the base already gives us for
free; §4 splits "already in the base (keep/adapt)" from "what we build onto it."

## 1. The problem we've been working through

We built a custom loop capability over native `/loop` and asked the honest question:
**does scaffolding (a structured "discipline" + an independent voted "judge") beat a
bare `/loop` with a good prompt — for non-deterministic tasks?** Tested as a
controlled A/B on glm-5.2 with an **objective arbiter** (pytest / regex / a blind
independent grader — never the loop grading itself), catching two confounds.

## 2. Where it stands — our resolved findings

(Full detail + journey: `INVESTIGATION.md`; report: scratch `loop-eval-glm`.)

| configuration | mechanism | score /1.0 | calls |
|---|---|---|---|
| bare | self-judge, no criteria | 0.31 | 2 |
| discipline | derive criteria **inline** + self-verify | 0.31 | 2 |
| **derive_explicit** | derive as a **separate explicit checklist** → answer against it | **0.75** | 3 |
| derive_judge | + independent judge enforces | 0.875 | 9 |
| operator-given criteria | explicit checklist handed in (ceiling) | 1.00 | 2 |

- **Lever: "externalize the check"** — a written-down checklist binds the model; one
  recited inline does not (0.31 → 0.75, ~1 call).
- **The independent judge is a B→A increment** (0.75 → 0.875) — worth its ~3× cost
  for quality work; skip for bulk.
- **Deployed:** corrected `/loop` discipline → "derive the check as a separate
  explicit checklist, then answer against it", wired via a `UserPromptExpansion` hook.
- **Two things we did NOT solve:** judge calibration/quality (a weak judge
  rubber-stamps — 6/4/5 → 20/20/20) and context contamination. **QLoop solved both.**

## 3. The new input — QLoop (`kd_evaluator_harness`), source-verified

QLoop is the **productized, hardened version of exactly the loop we probed**, for
non-deterministic artifacts (text *and* vision via Gemini). The loop:
`negotiate contract → generate → collect evidence → route → judge → deterministic
decide → loop`. Two packages, one-way dependency (core never imports the harness,
enforced by an AST test).

### 3a. The map — QLoop validates and extends our findings

| Our concept | QLoop equivalent | Citation |
|---|---|---|
| **Externalize the check** (0.31→0.75) | **AcceptanceContract** — a compiled spec (hard gates + weighted dims + per-dim floors + min score) rendered **verbatim into the judge prompt**, and *negotiated before generation* | `compiler/contract_compiler.py::compile_contract`; `evaluators/llm_evaluator.py::build_evaluation_prompt` |
| **Independent judge** (0.75→0.875) | **Two stacked independents**, each with a **deterministic veto above it**: a proposer↔critic negotiation that hardens the checklist, then the judge that scores it | `qloop_harness/negotiation.py::negotiate_contract`; `evaluators/llm_evaluator.py::evaluate_with_llm` |
| **Budget guard / stop** | **Deterministic controller** — pure ordered first-match-wins over (eval, policy, history): accept / human_review / reset / pivot / refine | `controller/decision.py::decide` |
| **Judge rubber-stamp (unsolved)** | **Calibration + discrimination gate** (solved the measurement + guardrail; same self-judging ceiling residual as us) | `calibration/lessons.py`; `docs/calibration_discrimination.md` |

**The crown-jewel principle: the judge only SCORES; the controller DECIDES.** In
`decide()` the judge's own `recommended_action` is consulted only at step 6, and only
to *reset* — it **cannot force an accept**. accept/pivot/human_review come from policy
+ history. *Re-introducing "let the judge's self-reported action drive the loop"
re-introduces the rubber-stamp* — this is why our self-judging arms failed.
Generalized rule: **never let the LLM that proposes be the sole acceptor; put a
deterministic veto above every agreeable LLM.**

### 3b. Contamination — the "sycophancy hole" and the evidence boundary

QLoop's named failure mode is exactly ours: a **same-context judge rubber-stamps the
generator's narration of "done"** (their `docs/plans/0007` explicitly rejects
`/goal`'s "the agent narrates done and the judge believes it"). This is *why* inline
criteria don't bind in our result. The fix is an **evidence boundary**: the judge
scores a collected `EvidenceBundle` (screenshots, DOM/ARIA, console/network health,
structured extraction) — **never the conversation**. (`evaluate_with_llm` has no
`transcript` parameter at all.) Other isolation boundaries, each enforced by a cheap
AST/invariant test, not by hoping a prompt holds:

- core never imports the harness; **controller is invariant to calibration**
  (`test_decide_is_invariant_to_calibration_for_a_fixed_result`); scorer ≠ reflector ≠
  curator; lesson-learning runs in a temp dir (a bad candidate never pollutes the live
  corpus); **two-provider boundary** (build=Anthropic, text judge=Z.ai, vision=Gemini —
  generator and evaluator are physically different accounts; `~/.claude` untouched).
- Cross-judge contamination prevented by **dimension partitioning** (vision judge owns
  only visual dims; lessons routed per `judge_type` AND `dimensions`).
- **Portable lesson:** enforce every isolation claim with an AST/invariant test —
  behavioral tests pass while a silent recontamination (an import re-coupling
  calibration to the controller) slips through.

### 3c. Calibration/discrimination — the fix for our rubber-stamp

The split (`docs/calibration_discrimination.md`): **calibration = level** (are absolute
scores right, `|judge−reference|` drift); **discrimination = resolution** (can it tell
tiers apart in the right order). **Our 6/4/5 → 20/20/20 is their documented failure
mode with a name:** a change that lowers *aggregate* drift by compressing scores toward
an extreme — improving average agreement while collapsing good-vs-bad resolution. Fix,
verified in code:

1. **`per_anchor_abs_drift`** — drift computed **per quality tier**, not pooled (the
   aggregate hides the trade; per-anchor exposes it). They rejected a *gap* metric
   because both good and bad lessons shrink the raw gap.
2. **`decide_keep` gate** — keep a judge/prompt change only if aggregate didn't
   regress **AND** the held-out anchor didn't regress **AND** no single tier regressed;
   an *unmeasurable* candidate is reverted, not admitted on faith.

**Sharp answer to our rubber-stamp:** a judge/gate must be measured against **both a
strong and a weak human-scored anchor, per-tier**, or it silently rubber-stamps; a
single-tier corpus makes the guard *literally inert*. **What QLoop did NOT solve = our
exact open item:** the self-judging ceiling — their anchors are **synthetic**
("replace with human scores to make learning meaningful"). The standing follow-up is
identical to ours: **human-scored anchors.**

## 4. Agenda — build on the base (don't rebuild what's there)

### 4a. Already in the base — keep / adapt, do NOT re-derive
These are QLoop's solutions to problems we hit; the clone gives them for free. Adapt
to our purpose, keep the architecture:
- **Calibration/discrimination guard** (`calibration/lessons.py`: `per_anchor_abs_drift`
  + `decide_keep`) — the antidote to our 6/4/5→20/20/20 rubber-stamp.
- **Deterministic controller** (`controller/decision.py::decide`) — judge SCORES,
  controller DECIDES; `recommended_action` can only reset, never accept.
- **Evidence boundary** (`evaluators/llm_evaluator.py::evaluate_with_llm`, no transcript
  param) — the contamination fix (judge sees evidence, not the generator's narration).
- **Isolation enforced by AST/invariant tests** (`tests/test_*_isolation.py`).
- **Adversarial contract negotiation** (`qloop_harness/negotiation.py`) + the
  **structural veto above every agreeable LLM** (`structural_issues`).
- **Multi-judge merge with veto** + **vision** judging
  (`evaluators/vision_evaluator.py::merge_vision_into_result`, Gemini).

### 4b. What we BUILD onto the base — our additions (ranked)
1. **A range-spanning, HUMAN-scored anchor set** (strong + weak tiers). The single
   highest-leverage NEW work and the gap *neither* QLoop nor we closed — QLoop's
   anchors are synthetic ("loop-validation only"). Without it the discrimination guard
   is plumbing-only and "the judge adds quality" stays unverifiable. **Start here.**
2. **Fold in our `loop_experiments` findings as first-class steps:** the
   *externalize-the-check* result (0.31→0.75) maps to making the AcceptanceContract the
   mandatory pre-generation step; carry our objective-arbiter eval methodology (pytest /
   regex / blind grader) as the *acceptance test for the harness itself*.
3. **Wire the harness to our `/loop` + discipline + native Claude Code** so it's usable
   from the loop workflow we built, not only as a standalone CLI.
4. **Cross-model judge** (if a real Anthropic key is available) — an Opus judge over
   glm-5.2 work; QLoop's two-provider boundary already supports this cleanly.
5. **Harder tasks / the elicitation angle / the always-on CLAUDE.md decision** — carried
   from `INVESTIGATION.md` §11.

**Landmines (QLoop's scar tissue — don't relearn):** a single-tier corpus makes the
discrimination guard inert; a *gap* metric can't distinguish a good lesson from a bad
one (use per-anchor drift); a GLM judge over-discriminated at the *bottom* (floored weak
artifacts) — don't assume "too lenient"; lessons over-shoot across tiers (tag with the
tier learned under); evidence truncation silently degrades the judge; `thinking` tokens
are billable output (our glm-5.2 thinking-on cost concern).

## 5. Environment & gotchas

- **Lab:** `~/lrepos/loop_experiments` (uv venv + Infisical; desktop-only). Harness
  `eval/glm_eval.py` (arms bare/discipline/derive_explicit/derive_judge/improved;
  objective scorers; `results_*.jsonl`).
- **Model routing:** glm-5.2 via ZAI raw SDK. **The raw SDK cannot use the Max
  subscription** (the vault `ANTHROPIC_API_KEY` here is a ZAI key → 401 on
  api.anthropic.com); for Anthropic from a script use the Agent SDK / `claude -p`.
  *QLoop's two-provider boundary (build=Anthropic, judge=Z.ai, vision=Gemini) is the
  clean pattern to copy.*
- **`loop.md`** only applies to bare `/loop` — discipline lives in the hook/CLAUDE.md.
- glm-5.2 invokes skills fine via the Agent SDK path; the "skill-creator 0% on GLM" was
  a `claude -p` headless harness bug, not a model gap.

## 6. Where everything lives

| artifact | location |
|---|---|
| Full investigation | `loop_experiments/INVESTIGATION.md` · scratch `loop-investigation` |
| A/B harness + data | `loop_experiments/eval/glm_eval.py`, `results_*.jsonl` |
| Eval report | scratch `loop-eval-glm` |
| `/loop` discipline (live) | `~/.claude/hooks/loop-discipline.{md,sh}` + `~/.claude/settings.json` |
| **QLoop** (the prior harness) | `github.com/Kjdragan/kd_evaluator_harness` |
| QLoop anchor files | `controller/decision.py::decide`, `calibration/lessons.py` (`decide_keep`, `per_anchor_abs_drift`), `compiler/contract_compiler.py`, `evaluators/llm_evaluator.py`, `evaluators/vision_evaluator.py::merge_vision_into_result`, `qloop_harness/negotiation.py`, `docs/calibration_discrimination.md`, `docs/plans/0007-*.md`, guard tests `tests/test_{controller_calibration,harness}_isolation.py`, `tests/test_discrimination_gate.py` |
| This handoff | `loop_experiments/HANDOFF.md` · scratch `loop-handoff` |
