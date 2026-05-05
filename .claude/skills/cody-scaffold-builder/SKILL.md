---
name: cody-scaffold-builder
description: >
  Simone's Phase 2 skill. Convert a vault entity page (Claude Code feature
  discovered by the ClaudeDevs intel pipeline) into a fully populated demo
  workspace under /opt/ua_demos/<demo-id>/. Reads the entity, copies relevant
  raw docs into SOURCES/, authors BRIEF.md / ACCEPTANCE.md / business_relevance.md
  starter templates, then provisions the workspace via demo_workspace.py with
  vanilla Claude Code settings. Pair with `cody-task-dispatcher` to enqueue
  Cody. USE when Simone decides an entity from the vault is demo-worthy and
  she's ready to put a workspace together for Cody.
---

# cody-scaffold-builder

> **Phase 2 of the ClaudeDevs Intel v2 pipeline.** This is the bridge from
> vault knowledge to a Cody-ready demo workspace.
>
> See [v2 design doc §7](../../docs/proactive_signals/claudedevs_intel_v2_design.md)
> for the full Phase 2 contract.

## When to use

- A `vault/entities/<feature>.md` page exists (created by Phase 1's Memex pass).
- Simone has decided the entity is **demo_worthy** (not informational, not deferred).
- The endpoint required is `anthropic_native` (Claude Code feature demo) — for
  category-2 (raw Anthropic API) demos see [Demo Execution Environments](../../../docs/06_Deployment_And_Environments/09_Demo_Execution_Environments.md).
- The Phase 0 dependency-currency layer says required versions are installed
  (check `vault/infrastructure/installed_versions.md` if uncertain).

## What this skill does (mechanical)

The `services/cody_scaffold.build_demo_scaffold` function does ALL of:

1. Reads `vault/entities/<feature>.md` (frontmatter + body).
2. Picks 0–6 relevant raw docs from `vault/raw/` and `vault/sources/` based on:
   - source_ids declared in the entity frontmatter
   - filename stem matches against the entity slug
   - filename stem matches against any entity tag
3. Calls `demo_workspace.provision_demo_workspace(demo_id)` to create
   `/opt/ua_demos/<demo-id>/` with the vanilla scaffold (PR 7's safety net
   asserts no settings.json pollution).
4. Copies the selected docs into `<workspace>/SOURCES/`.
5. Writes three Markdown templates:
   - `BRIEF.md` — feature briefing with discovery context, summary, and
     entity body verbatim. Has `_(Simone: synthesize ...)_` placeholders
     where prose synthesis is needed.
   - `ACCEPTANCE.md` — explicit success contract template with numbered
     requirements, anti-patterns, must_use_examples, endpoint_required,
     min_versions.
   - `business_relevance.md` — Kevin-facing rationale template with
     client value, reference-implementation shape, priority.

The Python is fully deterministic — no LLM call. The templates encode
everything the entity page already contains; Simone's job is to refine
the prose.

## What Simone does after running it

The skill creates STARTER templates with `_(Simone: ...)_` placeholders.
Simone MUST refine each:

1. **`BRIEF.md`**: replace placeholders with actual prose synthesizing the
   entity body + linked SOURCES/ docs into a feature briefing Cody can
   read in one pass. Don't just delete placeholders — substitute real content.

2. **`ACCEPTANCE.md`**: replace numbered requirement placeholders with
   concrete behaviors the demo MUST exercise. Each requirement should be
   verifiable: "demo MUST import X", "demo MUST call Y(...)", "demo's
   stdout MUST contain string Z".

3. **`business_relevance.md`**: write the Kevin-facing rationale. Why
   would a client engagement want this pattern? How should Cody structure
   the implementation so it can be lifted into client work later?

## How to invoke (deterministic part)

From a Simone session that has access to the universal_agent venv:

```python
from pathlib import Path
from universal_agent.services.cody_scaffold import build_demo_scaffold

result = build_demo_scaffold(
    entity_path=Path("artifacts/knowledge-vaults/claude-code-intelligence/entities/skills.md"),
    demo_id="skills__demo-1",
    vault_root=Path("artifacts/knowledge-vaults/claude-code-intelligence"),
    overwrite=False,        # set True if re-running with the same demo_id
    source_limit=6,
)
print(result.to_dict())
```

Output: `ScaffoldArtifacts` dataclass with `workspace_dir`, `brief_path`,
`acceptance_path`, `business_relevance_path`, `sources_dir`, `sources_copied`.

## Demo ID convention

Use `<entity-slug>__<short-id>`:

- `<entity-slug>` matches the vault filename stem (e.g., `skills`, `memorytool`,
  `claude-code`).
- `<short-id>` is anything Simone picks to disambiguate multiple demos of
  the same feature. A timestamp slug or sequence number works:
  `skills__2026-05-05`, `skills__v1`, `skills__retry-3`.

`demo_workspace.provision_demo_workspace` enforces traversal-safe slug
sanitization, so weird inputs are rejected loudly rather than silently
corrupted.

## What this skill does NOT do

- It does NOT call `cody-task-dispatcher`. Run that separately AFTER
  Simone is satisfied the prose in BRIEF/ACCEPTANCE/business_relevance is
  ready.
- It does NOT decide demo_worthy / informational / deferred. That's
  Simone's judgment, made before invoking this skill.
- It does NOT verify Phase 0 versions. Simone should check
  `installed_versions.md` against the entity's `min_versions` before
  scaffolding so a version-blocked demo doesn't get queued.
- It does NOT call `claude /login` or any auth setup — the workspace
  inherits the OAuth session set up once on the VPS (see
  [provisioning runbook](../../../docs/operations/demo_workspace_provisioning.md)).

## Related skills

- `cody-task-dispatcher` — runs AFTER this skill once Simone has refined
  the templates. Enqueues the actual Cody demo task.
- `cody-progress-monitor`, `cody-work-evaluator` — Phase 4 skills Simone
  uses after Cody returns output.

## Off switch

There is no off switch — this is operator-driven and only fires when Simone
explicitly invokes it. To pause demo generation across the board, mark
entity pages `briefing_status: deferred` in the vault.
