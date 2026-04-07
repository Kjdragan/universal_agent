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

This report now includes five smoke passes:

- **Pass 1**: initial runtime evaluation
- **Pass 2**: follow-up evaluation after hardening internal sync and reciprocal-link handling
- **Pass 3**: follow-up evaluation after backlinking and candidate-check hardening
- **Pass 4**: warm internal-sync rerun validating incremental metadata
- **Pass 5**: warm internal-sync rerun after adding readable progress output and excluding evidence from managed scans

## External Vault

### Pass 1

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

### Pass 2

Status: **pass with minor quality issues**

Observed successful behavior after follow-up fixes:

- external ingest still succeeds
- query still succeeds
- lint finding count dropped from `9` to `1`
- reciprocal path links are now recognized by lint

Observed remaining issue:

- the filed analysis page still appears as an orphan because nothing links back to it yet

### Pass 3

Status: **clean pass**

Observed successful behavior after the latest follow-up fixes:

- external ingest succeeds
- query succeeds
- saved analysis pages are backlinked from cited source/entity pages
- lint finding count dropped to `0`

## Internal Vault

### Pass 1

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

### Pass 2

Status: **pass with bounded scope**

Observed successful behavior after hardening:

- internal sync completed inside the `20s` timeout window
- output summary reported:
  - `memory_files_count: 13`
  - `session_files_count: 12`
  - `checkpoint_files_count: 0`
- generated pages included:
  - `decisions/decision-ledger.md`
  - `preferences/preferences-ledger.md`
  - `incidents/incidents-ledger.md`
  - `threads/recent-threads.md`
  - `projects/project-memory.md`
- `decision-ledger.md` and `project-memory.md` were both confirmed present on disk

### Pass 3

Status: **pass with telemetry**

Observed successful behavior:

- internal sync completed inside the `20s` timeout window again
- generated pages remained stable
- timing telemetry is now returned from the sync operation

Observed output summary:

- `memory_files_count: 13`
- `session_files_count: 12`
- `checkpoint_files_count: 0`
- `total_duration_ms: 7071`
- phase timings:
  - `discover_sources_ms: 654`
  - `copy_memory_ms: 3`
  - `copy_sessions_ms: 37`
  - `copy_checkpoints_ms: 0`
  - `compile_ledgers_ms: 300`
  - `finalize_ms: 3003`

## Findings

### 1. External smoke path works

The external vault flow is operational enough for controlled experimentation.

### 2. Entity/concept generation quality still needs improvement

Pass 1 produced poor-quality pages and labels, including:

- `kind: entitie`
- low-value concept pages like `concepts/source.md`, `concepts/sources.md`, `concepts/universal.md`
- suspect entity candidates such as `Summary\n\nUniversal Agent`

After the first follow-up patch:

- singularization issues like `entitie` were removed
- reciprocal-link handling improved enough to clear most false orphan reports

After the second follow-up patch:

- low-value entity generation was reduced
- source-page lint false positives from generated sections were removed

Remaining issue:

- concept extraction is still simplistic; in the sample it still creates `concepts/sources.md`, which is acceptable for a heuristic v1 but not yet high-quality knowledge modeling

### 3. Lint is correctly flagging integrity problems, but most self-inflicted failures were reduced

Pass 1 external lint reported:

- orphan pages for nearly all auto-generated entity/concept pages
- missing entity pages from the source page despite related auto-generated pages already existing

Pass 2 external lint reported only the orphan analysis page.

Pass 3 external lint reported:

- zero findings

This means the backlink strategy for saved analyses and the narrower source-page candidate extraction removed the remaining smoke-level integrity issues.

### 4. Internal sync now has both telemetry and incremental copy-skipping

Pass 4 confirmed:

- a warm internal sync also completed inside the `20s` timeout window
- copied/skipped counts are now visible in the result
- the sync state can skip previously copied evidence on reruns

Pass 4 output summary:

- `copied_counts`
  - `memory: 0`
  - `sessions: 0`
  - `checkpoints: 0`
- `skipped_counts`
  - `memory: 13`
  - `sessions: 12`
  - `checkpoints: 0`
- `total_duration_ms: 6329`

This means incremental sync metadata is implemented and active, even though overall runtime is still dominated by ledger compilation and finalization.

### 5. Internal sync now has readable progress output and lower warm-run overhead

Pass 5 confirmed:

- `sync_progress.md` is written to disk and is human-readable
- a later warm internal sync rerun still completed inside the `20s` timeout window
- excluding `evidence/` pages from managed-page scans substantially reduced warm-run total duration

Pass 5 output summary:

- `total_duration_ms: 2398`
- phase timings:
  - `discover_sources_ms: 297`
  - `copy_memory_ms: 30`
  - `copy_sessions_ms: 3`
  - `copy_checkpoints_ms: 0`
  - `compile_ledgers_ms: 302`
  - `finalize_ms: 845`
- copied/skipped counts:
  - copied: `0 / 0 / 0`
  - skipped: `13 / 12 / 0`

This indicates the remaining internal-sync cost is now much more manageable in warm runs.

### 6. Internal sync was the main blocker, and is now materially improved

Pass 1 showed the internal memory projection path was not ready for real use because:

- it timed out against real project data
- it did not reliably materialize the compiled pages expected from a successful sync

Pass 2 addressed that by bounding the scan to recent evidence sources and producing the expected compiled pages.

Pass 3 added explicit timing telemetry, so the sync is now both bounded and observable.

## Opportunities For Improvement

### External vault

1. Improve entity/concept extraction quality beyond simple heuristics.
2. Improve query ranking so source pages stay dominant and low-value auto-pages rank lower.
3. Consider a “minimum confidence” threshold before auto-creating entity/concept pages.

### Internal vault

1. Add a dedicated smoke/integration test for the real shared-memory path, not just temporary fixtures.
2. Continue improving semantic quality of compiled memory pages as the real corpus gets larger.
3. Consider promoting sync telemetry into a more operator-facing runtime surface later.

## Verdict

- **External Knowledge Vault**: smoke-clean and usable for controlled experimentation
- **Internal Memory Vault**: runtime-usable in bounded form, with telemetry, incremental copy-skipping, and readable progress output now available

## Recommended Next Step

Continue the build-out, but the system is now usable enough for more targeted controlled experimentation.

Priority order:

1. improve entity/concept extraction quality
2. add a real shared-memory integration test
3. rerun smoke tests after each semantic-hardening pass
4. then expand hands-on usage
