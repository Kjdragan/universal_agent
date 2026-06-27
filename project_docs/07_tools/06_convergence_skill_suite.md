---
title: Convergence Skill Suite
status: active
canonical: true
subsystem: convergence-skill-suite
code_paths: []
last_verified: 2026-06-27
---

# Convergence Skill Suite

> The capability lives **outside this repo** in the operator's personal Claude Code
> plugin, `Kjdragan/dragan-plugins` (cloned for development at
> `~/lrepos/dragan-plugins`), with each skill built and verified in its own
> `~/lrepos/demo-*` repo. `code_paths` is therefore empty; this doc is the
> canonical UA-side description of what the suite is, how it routes, and how it is
> built. The plugin's `CONVERGENCE_SUITE.md` and each skill's `SKILL.md` are the
> operational source of truth — when this doc and those files disagree, those files
> win. This is **not** a `universal_agent` runtime subsystem and does **not** deploy
> to the production VPS; it is interactive tooling that loads into Claude Code on the
> operator's desktop via the `dragan` plugin (refreshed on `SessionStart`).

A family of loops for **driving an artifact to a bar**. The distinction the suite
encodes: call a **primitive** when you already know the shape of the evaluation;
call a **master** when you don't, or when the work is a multi-step pipeline. It is
the productized form of the convergence-loop research tracked in
[`04_intelligence`](../04_intelligence/) experiments and the operator's
`loop_experiments` lab. For how UA's own internal skills load and are invoked by
principals (a separate mechanism), see [Skills System](03_skills_system.md).

## Primitives — call these when you know the loop

| Skill | The loop | Fires on |
|---|---|---|
| `autoresearch-loop` | scalar hill-climb; keep-if-better / revert | "maximize/minimize a number", tune hyperparameters, optimize a metric |
| `pass-fail-repair` | run a real check, repair on real failure | "until the tests pass", fix until a runnable check is green |
| `judge-panel` | N independent LLM judges, pairwise, noise-floor margin | "which is better", "rate the quality", subjective judgment with no number/check |
| `optimize-then-verify` | optimize the soft objective, then verify the hard one | "optimize X **then** make sure it still passes Y" — the structure stated outright |
| `subagent-fanout` | parallel fan-out → fan-in synthesis | "fan out N agents", parallel exploration, merge independent findings |

## Masters — call these when you don't

| Skill | What it adds | Fires on |
|---|---|---|
| `converge` | routes the gate (check / score / judge / hybrid), **gate-first**, then escalates repair → optimize-then-verify → fan-out, re-gating each tier | ONE artifact to a bar when the path is unclear or multi-dimensional |
| `converge-flow` | a **sequence** of gated checkpoints; the improved artifact chains forward; a final-outcome gate | a multi-phase pipeline (build factory, the `/dragan:demo` flow) — manual-invocation by design |

### How `converge` works (the single-artifact master)

1. **Routes the gate.** A `Gate` carries whatever you can measure — a `check`
   (runnable pass/fail), a `score` (with a `target`/`direction`), or `judges` (a
   judge-panel). The router infers the kind: check / score / judge, or **hybrid**
   when a hard check and a soft score both apply. The caller never names the kind.
2. **Gate-first.** It evaluates the seed before spending anything on improvement —
   the cheapest convergence is discovering you're already done.
3. **Escalates in cost order**, re-gating after each tier and stopping at the first
   that passes:

   | Tier | Move | Maps to (primitive) |
   |---|---|---|
   | 0 | repair against the gate's feedback | `pass-fail-repair` |
   | 1 | hill-climb a stronger draft, then verify | `autoresearch-loop` + `optimize-then-verify` |
   | 2 | fan-out N parallel attempts, keep one that passes | `subagent-fanout` |

The accept decision for a subjective gate uses the judge-panel's **noise-floor
margin**: a candidate is kept only when the panel is *decisively* better (a
supermajority of judges, with ties diluting the verdict), so a subjective
optimization can't drift sideways inside the judge's own jitter.

### How `converge-flow` works (the multi-step master)

```
seed ─▶ Step 1 [gate] ─▶ Step 2 [gate] ─▶ … ─▶ final-outcome gate ─▶ done
          │ converge        │ converge              │ converge
          └─ improved artifact chains forward ──────┘
```

Each `Step` carries its own `Gate` (any kind) and its own improver. Every step runs
the full `converge` machinery, so a checkpoint that already passes is nearly free
and one that doesn't escalates on its own. `stop_on_fail` (default) halts at the
first checkpoint that can't converge; turned off, it attempts every step and
reports where the pipeline stands (a dry-run audit). `converge-flow` is
`disable-model-invocation` — a whole orchestrated pipeline is a deliberate reach,
not an auto-grab; the `/dragan:demo` factory composes its engine from a playbook
rather than auto-triggering it.

## How overlap is resolved — the downward-deferral chain

There is **no skill-to-skill call API**, so composition is done two ways: a master
*description* defers downward in prose so the router picks the leaner tool when it
fits, and a master *engine* imports the primitive engines and injects them
(engine-injection). The deferral chain:

```
converge      ──defers──▶  pass-fail-repair       you know it's a runnable check
              ──defers──▶  autoresearch-loop       you know it's a single number
              ──defers──▶  judge-panel             pure subjective quality
              ──defers──▶  optimize-then-verify    explicit "optimize-then-verify" structure

converge-flow ──defers──▶  converge                one artifact, not a sequence
```

`converge` is for a single artifact when the evaluation path is genuinely unclear
or mixes kinds; `converge-flow` is for a sequence of gated checkpoints.

## Trigger-routing validation

Routing was checked empirically: a model picks one skill from the seven live
descriptions over fourteen realistic phrasings. Result: **fourteen of fourteen**
route to the intended skill. The one apparent miss — "must compile AND score above
the bar, drive it there" routing to `optimize-then-verify` rather than `converge` —
is the deferral chain working as designed: that phrasing states the hard-plus-soft
structure explicitly, which `optimize-then-verify` owns. The descriptions are
mutually disjoint; no two claim the same phrasing without a deferral. For the
automated description-tuning tooling itself, see
[Skill Description Optimizer](05_skill_description_optimizer.md).

## How it's built and verified

Each skill is developed and proven in its own `demo-*` repo before being promoted
into the plugin:

| Repo | Ships |
|---|---|
| `demo-judge-panel` | the judge-panel engine + skill — parallel judges, pairwise/absolute, ties-dilute noise floor |
| `demo-converge` | both masters — the router/gate-first/tier engine and the gated-checkpoint orchestrator |
| `demo-autoresearch-engine`, `demo-self-correcting-loop`, `demo-optimize-then-verify`, `demo-subagent-orchestration` | the primitives |

Each repo carries a deterministic offline gate (a self-check that prints a
`*_OK` line) plus, where relevant, a real ZAI/GLM path, and is **born
dark-factory**: a self-contained `auto-merge` workflow validates and squash-merges
every PR with no manual review (GitHub's queued auto-merge is unavailable on
Free-plan private repos, so the workflow performs the merge itself). Promotion
copies each skill's `SKILL.md` and bundled engine scripts into
`dragan-plugins/skills/<name>/` and registers the path in the plugin manifest.

A rendered overview of the suite is published to the operator's tailnet scratchpad
(see [Networking & Scratchpad](../06_platform/06_networking_tailscale_proxy_sshfs.md))
and archived under `scratch_archive/`.

## Operator-facing notes

- Commands are namespaced by the plugin: `/dragan:converge`,
  `/dragan:converge-flow`, `/dragan:judge-panel`, and the renamed primitives
  (`/dragan:autoresearch-loop`, `/dragan:pass-fail-repair`,
  `/dragan:optimize-then-verify`, `/dragan:subagent-fanout`).
- Renamed/new commands go live on the next plugin refresh (the `SessionStart`
  refresh hook pulls the marketplace clone).
- Scaffold a fresh workspace for any of these with `/dragan:new-repo`.
