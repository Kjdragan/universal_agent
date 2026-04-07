# LLM Wiki Smoke Test (2026-04-07)

## Purpose

This document records the first manual smoke-test pass for the LLM Wiki System after the initial implementation landing.

The goal was to exercise the recommended workflow:

1. initialize an external vault
2. ingest one small local source
3. query the vault
4. run wiki lint
5. run or trigger internal memory sync and inspect `memory/wiki/`

## Commands Run

### External vault smoke

```bash
PYTHONPATH=src uv run python - <<'PY'
import json
import tempfile
from pathlib import Path
from universal_agent.wiki.core import ensure_vault, ingest_external_source, query_vault, lint_vault

work_dir = Path(tempfile.mkdtemp(prefix='llm_wiki_external_smoke_'))
external_root = work_dir / 'external_roots'
source_path = work_dir / 'sample_source.md'
source_path.write_text(
    "# LLM Wiki Smoke Source\n\n"
    "Universal Agent can maintain a persistent wiki with immutable raw sources, "
    "an index, a log, and Obsidian-friendly markdown pages. NotebookLM is optional "
    "and derivative, not canonical. Agents should query the wiki before raw sources.\n",
    encoding='utf-8',
)
ctx = ensure_vault('external', 'smoke-test-vault', root_override=str(external_root))
ingest = ingest_external_source(vault_slug='smoke-test-vault', source_path=str(source_path), title='Smoke Source', root_override=str(external_root))
query = query_vault(vault_kind='external', vault_slug='smoke-test-vault', query='What is canonical and how should agents query it?', save_answer=True, answer_title='Smoke Query Result', root_override=str(external_root))
lint = lint_vault(vault_kind='external', vault_slug='smoke-test-vault', root_override=str(external_root))
print(json.dumps({'vault_path': str(ctx.path), 'ingest': ingest, 'query': query, 'lint': lint}, indent=2))
PY
```

### Internal sync timeout test

```bash
timeout 20s bash -lc 'PYTHONPATH=src uv run python - <<\"PY\"
import json
from universal_agent.wiki.core import sync_internal_memory_vault
result = sync_internal_memory_vault(trigger=\"smoke_timeout_test\")
print(json.dumps(result, indent=2))
PY' ; echo EXIT_CODE:$?
```

### Internal vault inspection

```bash
PYTHONPATH=src uv run python - <<'PY'
from pathlib import Path
from universal_agent.memory.paths import resolve_shared_memory_workspace
root = Path(resolve_shared_memory_workspace()) / 'memory' / 'wiki'
print(root)
for item in sorted(root.iterdir()):
    print(item.name)
PY
```

## Results

## External Vault

Status: **pass with quality issues**

Observed successful behavior:

- external vault scaffold was created successfully
- source ingest succeeded
- immutable raw source file was created
- source page was created under `sources/`
- query succeeded and returned cited page matches
- query result was successfully filed into `analyses/`
- lint succeeded and wrote a lint report

Observed output highlights:

- source page: `sources/smoke-source.md`
- raw path: `raw/c09e87be2a0c.md`
- query filed: `analyses/smoke-query-result.md`

## Internal Vault

Status: **fail for runtime readiness**

Observed behavior:

- the internal vault scaffold exists on disk at `Memory_System/ua_shared_workspace/memory/wiki`
- a real-data internal sync run did **not** complete within 20 seconds
- the timeout command exited with `EXIT_CODE:124`
- after the timed run, only scaffold files were present at shallow depth:
  - `AGENTS.md`
  - `index.md`
  - `log.md`
  - `overview.md`
  - `vault_manifest.json`
- compiled ledger pages such as `decisions/decision-ledger.md` and `projects/project-memory.md` were not materialized in the inspected run

## Findings

### 1. External smoke path works

The external vault flow is operational enough for controlled experimentation.

### 2. Entity/concept generation quality is weak

The heuristic extraction produced poor-quality pages and labels, including:

- `kind: entitie`
- low-value concept pages like `concepts/source.md`, `concepts/sources.md`, `concepts/universal.md`
- suspect entity candidates such as `Summary\n\nUniversal Agent`

This indicates the current extraction logic is too naive for production-quality page creation.

### 3. Lint is correctly flagging integrity problems, but many are self-inflicted

External lint reported:

- orphan pages for nearly all auto-generated entity/concept pages
- missing entity pages from the source page despite related auto-generated pages already existing

This means the current page generation does not create enough inbound/outbound linking to satisfy its own integrity rules.

### 4. Internal sync is the main blocker

The internal memory projection path is not ready for real use because:

- it times out against real project data
- it does not reliably materialize the compiled pages expected from a successful sync

This is currently the highest-priority hardening area.

## Opportunities For Improvement

### External vault

1. Replace naive title/entity/concept extraction with stricter heuristics or a structured synthesis pass.
2. Normalize singular/plural page kinds correctly instead of generating `entitie`.
3. Create reciprocal links when generating entity/concept pages so lint does not immediately classify them as orphans.
4. Tighten missing-entity detection so it does not emit malformed candidates.
5. Improve query ranking so source pages remain primary and low-value auto-pages do not dominate result sets.

### Internal vault

1. Add timing/telemetry inside `sync_internal_memory_vault()` to identify the slowest phase.
2. Bound scanning of `AGENT_RUN_WORKSPACES` and checkpoint files rather than walking too much history on each sync.
3. Add partial-progress logging so we can distinguish “slow” from “stuck.”
4. Make sync incremental rather than full-scan on every invocation.
5. Add a dedicated smoke/integration test for the real shared-memory path, not just temporary fixtures.

## Verdict

- **External Knowledge Vault**: usable for limited experimentation
- **Internal Memory Vault**: not yet runtime-ready

## Recommended Next Step

Continue the build-out before broader usage.

Priority order:

1. fix and instrument `sync_internal_memory_vault()`
2. improve entity/concept extraction and page-linking quality
3. rerun the smoke test
4. only then consider broader hands-on usage
