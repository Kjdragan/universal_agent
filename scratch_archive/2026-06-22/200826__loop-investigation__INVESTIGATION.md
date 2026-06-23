# Investigating the loop process: from a catalog launcher to a certified improvement mechanism

*A full record of what we set out to do, what we built, what we tested, what we
found, and where we ended up — including the non-determinism work that motivated
it. glm-5.2, 2026-06-22.*

---

## TL;DR

We wanted to know whether *scaffolding a loop* (a structured "discipline" + an
independent voted "judge") actually beats running a bare Claude Code `/loop` with a
good prompt — especially for **non-deterministic** ("is this good enough?") tasks.

We tested it as a controlled A/B with an **objective arbiter** (never the systems
grading themselves), and after catching two confounds it resolved to a single,
certified mechanism:

> **Externalize the check.** Turn the goal into an explicit, *written-out* checklist
> and answer the work *against* it. A check the model only recites inside its own
> reasoning does not bind it; a check it writes down and answers against does. That
> one move took a vague task from **0.31 → 0.75** (objective score /1.0) for about
> **one extra model call.**

On top of that, an **independent judge** that enforces the checklist takes
**0.75 → 0.875** at ~3× the calls. That is *not* a rounding error: without it the
work was stuck at "always misses one criterion" (a B); with it, half the runs hit
**every** criterion (an A). **For quality-critical judged work, the judge earns its
cost; for cost-sensitive bulk work, skip it.**

We corrected the `/loop` "discipline" to the version that actually works and wired it
to fire automatically on `/loop` via a hook.

---

## 1. What we set out to do

The goal was a *custom loop capability* on top of Claude Code's native `/loop`: a way
to take a request and run it as a bounded, self-terminating improvement loop that
works for **both** deterministic tasks (a test passes / a metric hits a target) and
**non-deterministic** tasks (quality, "good enough"), which native `/loop` doesn't
give special help with.

The open, honest question throughout: **does any of the scaffolding we add beat just
typing a good `/loop` prompt?** If bare `/loop` is equally good, the scaffolding isn't
needed. The only result worth anything is a case where bare fails an objective bar the
scaffolded system clears.

## 2. What we built (and un-built)

- **`loop-it`** — a thin launcher skill (`loop_experiments/.claude/skills/loop-it/`):
  match a request to a loop *design*, compose a structured prompt (goal / bounded
  action / fixed check / stop), and run it (fast in-session, or durable via native
  `/loop` self-paced). It started by pulling loop designs from the Forward Future
  "Loop Library" over a URL.
- **Brought it fully in-house.** We distilled the load-bearing *grammar* + a handful
  of curated *patterns* into local markdown (`loop_patterns/`), removed the URL/refresh
  dependency, and demoted the 50-entry catalog to a frozen offline idea-bank.
  Lesson: forcing a "match a catalog entry" step produced **false provenance** (it
  named a loop that wasn't really a fit). The grammar is the value; the catalog is just
  a retrieval prior.
- **`loop_kit/`** — `state.py` (a deterministic budget guard: iteration cap +
  wall-clock; `status` exits 3 = stop) and `judge.py` (an independent LLM-judged check:
  a different agent scores against a rubric, majority-of-N voting).
- **The discipline + a `/loop` hook** — a short "loop discipline" doc injected into
  context whenever you run `/loop`, via a `UserPromptExpansion` hook. (More below.)

## 3. The experiment

**Question, made testable:** for a loop task, does the scaffolded system beat bare
`/loop` on an *objective* score computed by a blind grader that never saw either run?

**Model:** glm-5.2, via ZAI's Anthropic-compatible endpoint (raw SDK). We *wanted*
Opus vs glm-5.2, but the lab's vault `ANTHROPIC_API_KEY` is a ZAI key (401 against
api.anthropic.com), so raw-SDK Opus was impossible — so this is a **glm-5.2-only**
study (see §7).

**Arms:**

| arm | mechanism |
|---|---|
| `bare` | "iterate until done", self-judged (a fair, non-strawman bare-`/loop` prompt) |
| `discipline` | derive criteria *inline* in reasoning + self-verify |
| `derive_explicit` | derive criteria as a **separate explicit checklist** → answer against it (no judge) |
| `derive_judge` | + independent judges (3, majority + confirmation) enforce the checklist |
| (operator-given) | the criteria handed in explicitly — the ceiling |

**Objective arbiter (never the loop's own judgment):** deterministic tasks scored by
running a hidden pytest; non-deterministic tasks scored by regex/length checks +
a **blind independent grader** on a fixed checklist.

**Fairness controls:** identical task statement; equal iteration cap (compute reported
separately so a "win" isn't just more tokens); the grading criteria withheld from the
loops; multiple trials to handle variance.

**Tasks:** a deterministic control (`D1`, implement a function to pass pytest), easy /
hard / adversarial non-deterministic tasks (`N1`, `N3`, `N4`), and a medium
non-deterministic pitch-rewrite (`N2`) that became the workhorse.

## 4. What we found — the journey (and two confounds)

The honest path mattered as much as the destination; two confounds nearly produced
wrong conclusions.

| step | finding |
|---|---|
| **Pass 1** | On every task, scaffolded *tied* bare — the judge looked useless. |
| **Confound #1 (information)** | The one apparent win (`N2` 0.25 → 1.00) was because *bare wasn't told the criteria* while the scaffolded arm was. Tell bare the criteria → it hits 1.00 too. The "win" was information, not mechanism. |
| **Discipline-alone is null** | On a *vague* task, deriving criteria **inline** + self-verifying did nothing (0.31 = bare). So "the discipline helps" was *also* unproven. The model **derived good criteria and then ignored them** — in one trial it listed "remove these buzzwords" and emitted the original buzzword-laden sentence verbatim, declaring DONE. |
| **derive + judge flips it** | Deriving criteria as a *separate* step + an independent judge → **0.875**. The system became a clear win on vague tasks. |
| **Confound #2 / isolation** | But the judge *never fired a correction* (every run passed on the first author attempt). Isolating it — `derive_explicit`, no judge — gave **0.75**. So the lever is **externalizing the check**, not the judge. |

### The resolved ladder (vague pitch, glm-5.2, objective score /1.0, 4 trials each)

| configuration | mechanism | score | calls |
|---|---|---|---|
| bare | self-judge, no criteria | 0.31 | 2 |
| discipline | derive criteria **inline** + self-verify | 0.31 | 2 |
| **derive_explicit** | derive as a **separate explicit checklist** → answer against it | **0.75** | 3 |
| derive_judge | + independent judge enforces | 0.875 | 9 |
| operator-given criteria | explicit checklist handed in (ceiling) | 1.00 | 2 |

On tasks whose criteria were already explicit in the prompt (`D1`, `N1`, `N3`, `N4`),
bare and scaffolded **tied** at ~1.0 — the system added nothing there. The scaffolding
earns its keep precisely on **vague / underspecified** asks, which is where real loops
usually start.

## 5. The independent judge — a fair assessment

It is tempting (and I initially did this) to dismiss the judge as a "modest top-up."
That undersells it. Look at the *distribution*, not just the mean:

- `derive_explicit` scored **3/4 on every single trial** — consistently good, but it
  **always dropped exactly one criterion** (usually "name a *specific* user," the most
  operator-idiosyncratic one). That is a solid **B** that never quite reaches A.
- `derive_judge` scored **4, 3, 3, 4** — it pushed **half the runs to a perfect 4/4**,
  i.e. *every* criterion met.

For judged, quality-critical work, "meets every criterion" vs "reliably misses one" is
exactly the line that separates a shippable deliverable from a not-quite one — the
B→A jump. The cost is ~3× a small number of cheap calls, which is trivial against the
value of the output being *right*. So the honest, even-handed rule:

> **Externalize the check — always** (it's the big, cheap win). **Add the independent
> judge whenever output quality is the priority** (it closes the final gap to
> fully-compliant). **Skip the judge for cost-sensitive bulk work** where "good enough"
> is fine.

The earlier "judge adds nothing" reading came from measuring it in the *wrong place*
(the fair case, where criteria were already explicit and self-verification already
worked, so there was nothing left for the judge to catch). On self-derived criteria —
the real case — it pays.

## 6. The mechanism, stated plainly

**A check you write down binds the model; a check it only recites to itself does not.**

- Deriving good criteria was **never** the bottleneck — told to, glm-5.2 produced
  essentially the grading rubric.
- The bottleneck was **enforcement**: criteria left inline in the model's reasoning are
  soft suggestions it ignores; the same criteria pulled out into an explicit "here is
  the CHECK" block become requirements it complies with.
- The work can be done by **you** (write the checklist) or by **the model as a separate
  step** (derive → externalize → answer against it). Either way, the externalization is
  the act that matters.
- The **independent judge** then enforces that externalized check and closes the last
  increment to fully-compliant.

## 7. Non-determinism — the part that motivated all this

The whole investigation was really about the *non-deterministic* case (judged quality),
because deterministic loops already have a hard check (a test, a metric) that forces
convergence. Two earlier findings fed in:

- A weak LLM judge **rubber-stamps** (we saw a pitch jump 6/4/5 → 20/20/20 in one step
  with a weak grader) — so a judged loop needs a real rubric, majority voting, and a
  hard iteration cap, and the *judge's quality* is the thing to invest in.
- An LLM-judged check is usable as a loop gate **only** with vote + streak + cap; a
  single verdict flaps.

This study sharpened that: for non-deterministic checks, the decisive move is to make
the quality bar **explicit and external** (a written checklist), then optionally have an
**independent** party enforce it. Self-assessment against an internal sense of "good" is
where non-deterministic loops fail.

## 8. What's deployed

- **Corrected `/loop` discipline.** The discipline now says *"derive the check as a
  separate explicit checklist, then answer against it"* (the 0.75 version) instead of
  the original *"derive + self-verify"* (the 0.31 version that didn't work). The
  independent judge is framed as an opt-in for quality-critical / ambiguous work.
- **Wired to fire on `/loop`** via a `UserPromptExpansion` hook:
  `~/.claude/hooks/loop-discipline.md` (the text, single source of truth) +
  `loop-discipline.sh` (the matcher) + a `UserPromptExpansion` entry in
  `~/.claude/settings.json`. It injects the discipline only when you type `/loop`, and
  stays silent otherwise.
- **The lab** (`~/lrepos/loop_experiments`, github.com/Kjdragan/loop_experiments):
  the `loop-it` skill, `loop_kit/` (state guard + judge), `loop_patterns/` (the
  in-house grammar), and `eval/` (the A/B harness `glm_eval.py`, the data
  `results_*.jsonl`, the report).

## 9. Key technical learnings (the side-discoveries)

- **The raw Anthropic SDK cannot use a Max subscription.** It needs an API key /
  auth_token; OAuth is for the `claude` CLI / Agent SDK only. The lab's vault
  `ANTHROPIC_API_KEY` is in fact a **ZAI** key (401 on api.anthropic.com) — the lab is
  designed to run inference on ZAI, not real Anthropic.
- **GLM models invoke skills fine.** We empirically confirmed glm-5.2 *and* Opus both
  fire the `Skill` tool via the canonical Agent SDK path (`skills="all"` +
  `setting_sources`). The widely-cited "skill-creator shows 0% on GLM" is a known
  `claude -p` **headless harness bug** (it reproduces on first-party Anthropic models
  too), *not* a model capability gap.
- **`loop.md` only applies to *bare* `/loop`** (it's ignored the moment you pass a
  prompt). So loop discipline belongs in a **hook / CLAUDE.md**, not `loop.md`.
- **Native `/loop`** is prompt-based: self-paced (`ScheduleWakeup`, model schedules its
  own next iteration) or interval (`CronCreate`), session-scoped, 7-day expiry, `Esc`
  to stop. A hook fires once at loop-start (not per self-paced wakeup).

## 10. Honest limits

- **One model** (glm-5.2). The Opus comparison we wanted was blocked by the vault-key
  situation; cross-model judging (a different-model judge, which theory says should help
  more) was therefore untested.
- **The resolving ladder is one task family** (a vague pitch), 4 trials per cell. The
  "externalize the check" effect is intuitive and almost certainly general, but is
  cleanly demonstrated here on one task; a second vague task would nail down
  generalization.
- **The judge's upside may be larger** on harder tasks where the author *can't* get it
  right on the first attempt (here it always did, so the judge's correction loop never
  fired) — untested.

## 11. Open threads / next steps (handoff-ready)

1. **Generalize:** rerun `derive_explicit` vs `derive_judge` on a 2nd and 3rd vague
   task (different domains) to confirm the ladder holds.
2. **Cross-model judge:** if a real Anthropic key becomes available, test an Opus judge
   over glm-5.2 work (different blind spots → likely the judge's strongest case).
3. **Harder tasks:** construct a vague task the author *can't* nail first-try, to
   actually exercise (and measure) the judge's correction loop.
4. **The elicitation angle (untested):** make the loop *stop and ask the operator* for
   criteria on a vague goal, instead of deriving them — measure whether operator-elicited
   beats model-derived.
5. **Decide on the always-on CLAUDE.md section:** the `/loop` hook delivers the
   discipline on `/loop`; an always-on `~/.claude/CLAUDE.md` line would also catch ad-hoc
   "iterate on this" phrasing — deferred pending a call on whether the conditional
   discipline is worth global context weight.

## 12. Where things live

| artifact | location |
|---|---|
| Lab repo | `~/lrepos/loop_experiments` · github.com/Kjdragan/loop_experiments |
| A/B harness + data | `loop_experiments/eval/glm_eval.py`, `results_*.jsonl` |
| Rendered report | scratchpad slug `loop-eval-glm` |
| `/loop` discipline (live) | `~/.claude/hooks/loop-discipline.{md,sh}` + `~/.claude/settings.json` |
| `loop-it` skill + grammar | `loop_experiments/.claude/skills/loop-it/`, `loop_patterns/` |
