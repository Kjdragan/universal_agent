---
name: adversarial-code-verification
description: >
  Stand up a SEPARATE, read-only verifier whose job is to REFUTE a code claim, not confirm it —
  it defaults to refuted (is_real=false) when it cannot prove the claim, verifies independently
  against the code AND the runtime rather than trusting the author/implementer's report, and
  prefers a few high-confidence findings over many speculative ones. Use this skill whenever you
  hear: "adversarial verification", "adversarial verifier", "try to refute this claim", "refute
  this fix", "red-team this PR", "red-team this claim/fix/change", "red-team the heartbeat claim",
  "play devil's advocate", "devil's advocate", "default to skepticism", "default to refuted",
  "verify independently / don't trust the author", "don't take the author's/implementer's word",
  "independently confirm", "validate the claim yourself", "re-derive it from the code and runtime",
  "a second pair of eyes", "TRY TO BREAK IT", "find the failure mode the implementer missed",
  "skeptic panel", "panel of independent reviewers/verifiers", "reach consensus / go with the
  consensus", "independent votes (each tries to poke holes, fail independently)", "majority vote
  among verifiers", "facet investigator", "parallel facet investigators (read-only, code+runtime
  verified)", "run a vision-vs-reality audit / reality audit", "audit the vision-vs-reality gap"
  (reality-vs-vision), "classify real vs aspirational", "which parts are actually built and
  running vs just planned/documented/stubbed/scaffolded", "actually built vs documented", "real vs
  planned/stubbed", "wired vs aspirational", "is_real / reality_status", "REAL_AND_RUNNING vs
  PARTIAL vs ASPIRATIONAL", "cite file::symbol (no line numbers)", "cite code as module::function
  / Class::method", "use a qualified symbol path, no line-number citations (they drift)", "cite the
  symbol not the line number", or any claim/evidence/verdict (refuted true|false) structure. Also
  use to fan out N read-only investigators that each own one facet and classify it real vs
  aspirational, and to aggregate several independent verifiers by consensus / majority vote. Keep
  the neutral-framing triggers (validate the claim, independently confirm, re-derive,
  second pair of eyes) paired with the refute-by-default / skepticism posture so they don't fire on
  plain self-checks. NOT for checking your OWN work before completion (use
  verification-before-completion — that is a confirmation-oriented self-check); NOT for scoring
  SKILL.md design quality (use skill-judge); NOT for the Cody demo Phase-4 pass/iterate/defer
  verdict (use cody-work-evaluator).
user-invocable: true
risk: safe
source: "Derived from the UA skill-gap finder backlog (issue #796) -- adversarial-code-verification."
---

# Adversarial Code Verification

Run a **separate** verifier whose entire purpose is to **refute** a claim — "this fix works", "this
feature is wired", "the heartbeat actually runs" — rather than to confirm it. The verifier is
**read-only**, defaults to **refuted** when it cannot prove the claim, and checks the claim against
the **code AND the runtime itself** instead of trusting the author's report. This is the inverse of a
self-check: the implementer's job is to make the claim true; the verifier's job is to break it.

The single rule that makes this work: **the default verdict is refuted.** A claim is marked real only
when the verifier can point at concrete evidence; uncertainty resolves to `is_real=false`, never to
"pass".

## The adversarial posture (the four invariants)

Every adversarial-verification prompt re-specifies the same four things. Encode them once:

1. **Job = refute.** "Your job: TRY TO REFUTE this." "Spawn verifiers to TRY TO BREAK IT — find the
   failure mode the implementer missed." Only return `pass` if you genuinely cannot find a defect
   *after actively hunting*. A verifier that sets out to confirm is not a verifier.
2. **Default to refuted on uncertainty.** If you cannot prove the claim, mark `is_real=false` /
   `refuted=true` and explain why. Absence of evidence is a refutation, not a pass.
3. **Verify independently, READ-ONLY.** Do not edit files. Do not trust the implementer's "it works"
   report — re-derive it yourself from the code and the live system (grep the code, read the SQL, run
   the query, check the running process). The author's word is the *claim under test*, never evidence.
4. **Few high-confidence findings over many speculative ones.** A short list of proven findings beats
   a long list of guesses. Do not pad. One concrete refutation outweighs ten "this might be wrong".

## Citation format — `file::symbol`, NO line numbers

Cite code as `file::symbol`, with **no line numbers** — line numbers drift as the file changes, so a
line-anchored citation rots within a commit or two. Cite the stable symbol path instead, and pair
**every** citation with concrete runtime/SQL evidence.

```
# Good — stable symbol path + runtime evidence
heartbeat_service.py::HeartbeatService::_scheduler_loop
  runtime: `ps aux | grep heartbeat` shows the loop running; activity_state.db
           last_heartbeat updated 4s ago

# Bad — line number (drifts) and no runtime evidence
heartbeat_service.py:412   "looks like it schedules things"
```

Mark a claim real **only** when you can point at both the symbol **and** the evidence. A symbol with
no runtime proof, or runtime behavior you cannot trace to a symbol, is not enough — that is `is_real=false`.

## How to structure a verdict

Use the per-claim schema for a single claim:

```json
{
  "claim": "the scheduler loop runs every 30s in prod",
  "evidence": "heartbeat_service.py::HeartbeatService::_scheduler_loop; runtime: activity_state.db last_heartbeat 4s old, interval=30",
  "verdict": { "refuted": false }
}
```

Use the richer **facet** schema when classifying a feature as real vs aspirational:

```json
{
  "facet": "heartbeat keeps sessions alive",
  "reality_status": "REAL_AND_RUNNING",        // or PARTIAL | ASPIRATIONAL
  "is_real": true,                              // default false until proven
  "what_actually_happens": "loop writes last_heartbeat every 30s",
  "key_code": "heartbeat_service.py::HeartbeatService::_scheduler_loop",
  "runtime_evidence": "activity_state.db last_heartbeat 4s old",
  "gap_vs_vision": "no backoff on DB write failure; silently drops a beat"
}
```

`reality_status` / `is_real` default to ASPIRATIONAL / `false`. Promote to PARTIAL or
REAL_AND_RUNNING only with both a `key_code` symbol and `runtime_evidence`.

## Reusable adversarial-verification prompt skeleton

Copy-paste this into a subagent prompt. Fill in the claim; leave the posture rules verbatim.

```
You are an ADVERSARIAL VERIFIER. Your job is to TRY TO REFUTE the claim below — not to confirm it.

CLAIM: <paste the implementer's claim, e.g. "the heartbeat loop runs in prod">

RULES:
- You are READ-ONLY. Do not edit any file.
- Default to skepticism. If you cannot prove the claim, mark it refuted (is_real=false) and explain.
- Do NOT trust the author's report. Re-derive everything yourself from the code AND the runtime
  (read the code, grep for the symbol, read/run the relevant SQL or command).
- Cite code as file::symbol with NO line numbers (line numbers drift). Pair every citation with
  concrete runtime/SQL evidence.
- Prefer a few high-confidence findings over many speculative ones. Do not pad.
- Only return "pass" if you genuinely cannot find a defect after actively hunting for one.

OUTPUT (per claim):
{ "claim": ..., "evidence": "<file::symbol> + <runtime/SQL>", "verdict": { "refuted": true|false } }
```

## The facet-investigator (fan-out) pattern

When a claim has several independent parts ("is feature X real?" decomposes into N facets), fan out:
spawn **N parallel, read-only investigators, one facet each.** Each investigator classifies its facet
**real vs aspirational**, code+runtime verified, and returns the facet schema above.

- One facet per investigator — do not let one agent own the whole surface; parallel narrow beats
  serial broad.
- Each is read-only and refute-by-default, exactly like the single-verifier skeleton.
- The required keys per facet: `facet`, `reality_status` (or `is_real`), `what_actually_happens`,
  `key_code` (file::symbol), `runtime_evidence`, `gap_vs_vision`.
- Aggregate the facets into one real-vs-aspirational map of the feature.

## Multi-verifier panels — decide by majority vote

For a high-stakes claim, run **several independent verifiers** on the *same* claim and decide by
**MAJORITY VOTE**. Each verifier follows the skeleton independently (no shared notes, so they fail
independently).

- The panel verdict is the aggregate, not any single voter.
- A **single** refute among passes is a **signal to investigate**, not an automatic fail — but it is
  never ignored: re-run or dig into the dissent before accepting the majority.
- Keep verifiers independent: a verifier that reads another's output is no longer an independent vote.

## When to use

- Refuting a fix or a PR claim ("red-team this PR", "refute this fix").
- Classifying features as real vs aspirational (REAL_AND_RUNNING / PARTIAL / ASPIRATIONAL).
- Red-teaming an implementer's "it works" report — verifying the code and runtime yourself.
- Auditing a vision-vs-reality gap across many facets (fan-out facet investigators).
- High-stakes claims that warrant a panel of verifiers and a majority vote.

## When NOT to use

- **Checking your OWN work before completion** → use `verification-before-completion`. That is a
  single-agent, confirmation-oriented self-check ("prove your own claim with a fresh command"); this
  skill is a *separate* party that refutes by default.
- **Scoring SKILL.md design quality** → use `skill-judge`. That scores skill authoring against a
  rubric, not a code claim.
- **The Cody demo Phase-4 verdict** → use `cody-work-evaluator`. That is the pass/iterate/defer step
  tied to the Cody demo pipeline, not a reusable refute-the-claim posture.

## NEVER

- NEVER trust the author's claim without independent code **and** runtime evidence — the claim is the
  thing under test, not the proof.
- NEVER cite line numbers; they drift. Cite `file::symbol`.
- NEVER default to "pass" / `is_real=true` when uncertain. The default verdict is **refuted**.
- NEVER edit files while verifying — stay READ-ONLY.
- NEVER pad the report with speculative findings. A few high-confidence findings beat many guesses.
- NEVER let panel verifiers share notes — independence is what makes the majority vote meaningful.
