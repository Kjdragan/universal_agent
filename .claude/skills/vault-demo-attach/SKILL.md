---
name: vault-demo-attach
description: >
  Simone's Phase 4 vault-mutation skill. After `cody-work-evaluator`
  judges a demo passing, this skill appends a `## Demos` section to
  the vault entity page pointing at the workspace, with the demo_id,
  workspace path, endpoint hit, and timestamp. Idempotent — appends
  bullets to an existing section, never duplicates. Also supports
  detaching demos that get retired. USE only after a pass verdict.
---

# vault-demo-attach

> **Phase 4 of the ClaudeDevs Intel v2 pipeline (vault piece).** The
> mechanical write that closes the loop on a successful demo by
> linking it back into the vault entity page.

## When to use

- `cody-work-evaluator` returned a `pass` verdict.
- The demo's manifest.json shows `endpoint_hit` matches `endpoint_required`.
- Simone is ready to mark the entity page as having a demonstrated
  capability.

## How to invoke

```python
from pathlib import Path
from universal_agent.services.cody_evaluation import attach_demo_to_vault_entity
from universal_agent.services.cody_implementation import read_manifest

workspace = Path("/opt/ua_demos/skills__demo-1")
manifest = read_manifest(workspace)
vault_root = Path("artifacts/knowledge-vaults/claude-code-intelligence")

entity_path = attach_demo_to_vault_entity(
    workspace_dir=workspace,
    vault_root=vault_root,
    entity_slug="skills",
    manifest=manifest,
)
print(f"Updated {entity_path}")
```

## What this skill does

`attach_demo_to_vault_entity`:

1. Locates `vault/entities/<entity_slug>.md`. Raises if missing
   (catch this — it usually means the slug is wrong).
2. Builds a bullet line:
   ```
   - `<demo_id>` — `<workspace_dir>` — endpoint: `<endpoint_hit>` — attached <iso>
   ```
3. If the entity page already has a `## Demos` section, appends a new
   bullet to it. If not, creates the section at the end of the page.
4. Writes the modified file.

Idempotent: running twice with the same demo_id appends two bullets
(no automatic dedup). If you need to clean up a previous attachment,
use `detach_demo_from_vault_entity`:

```python
from universal_agent.services.cody_evaluation import detach_demo_from_vault_entity

detach_demo_from_vault_entity(
    vault_root=vault_root,
    entity_slug="skills",
    demo_id="skills__demo-1",  # bullet whose `demo_id` text matches gets removed
)
```

## What this skill does NOT do

- It does NOT update the capability library at `agent_capability_library/`.
  That happens automatically on the next `build_rolling_assets` tick
  (PR 5's full-corpus mode picks up the new entity content).
- It does NOT verify the demo actually passed. Trust your verdict from
  `cody-work-evaluator`. If you're unsure, don't attach.
- It does NOT write a `_history` snapshot of the entity page before
  modifying. The Memex EXTEND path doesn't snapshot (see PR 2 §4.2 —
  EXTEND is dated-section append, not REVISE).

## Operator notes

The `## Demos` section ordering: bullets accumulate top-down, newest
first if you read the file front-to-back. That matches the v2 design's
"newest demo on top" surfacing convention. If you ever want to reorder
or curate, just edit the markdown directly — there's nothing magical
about the structure beyond the section header.

## Related skills

- `cody-work-evaluator` (PR 10) — what tells you a demo is ready to attach.
- The Memex primitives in `wiki/core.py` (PR 2) — semantically this
  skill IS an EXTEND on an entity page, just specialized for the
  Demos section. We don't route through `memex_apply_action` because
  Demos sections have a specific shape that doesn't benefit from the
  generic CREATE/EXTEND/REVISE machinery.
