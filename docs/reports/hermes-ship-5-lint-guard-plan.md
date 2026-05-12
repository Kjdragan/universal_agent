# Hermes Ship 5 — Subprocess Lint Guard: Detailed Implementation Plan

**Created:** 2026-05-11 PM
**Sequencing:** Ships AFTER Ship 4 (Task Hub Observability Protocol doc must exist as the reference target).
**Purpose:** Enforce the Task Hub Observability Protocol at CI time. New subprocess spawns in `src/universal_agent/` that don't import from `worker_exit_classifier` or `task_hub`'s observability helpers fail PR-validate. Existing unwired spawns are grandfathered via an allowlist; the test acts as a ratchet so coverage only ever increases.

This plan is self-contained for compaction-resilience.

---

## Why this guard exists

The Task Hub Observability Protocol (`docs/03_Operations/108_Task_Hub_Observability_Protocol.md`, shipped in Ship 4) requires that every new async unit of work in `src/universal_agent/` uses the six-rule observability + recovery pattern. Without enforcement, a future contributor (Claude or human) can write a new cron job, webhook handler, or subprocess spawn that skips the protocol — and the operator won't see the gap until something goes silently wrong in production.

The lint guard is a CI-time check that:

1. AST-walks `src/universal_agent/` for subprocess-spawning call sites.
2. For each file with such a call, verifies it imports from `worker_exit_classifier` (the observability module) or is on a documented allowlist.
3. Fails PR-validate with a pointer to the 108 doc when a new violation appears.

Existing-but-unwired spawn sites are grandfathered via the allowlist so the guard can ship without requiring a giant backfill PR. The allowlist is a regular text file; shrinking it (i.e., wiring an existing site) is the natural path forward.

---

## Detection scope

The guard flags these call patterns:

```python
asyncio.create_subprocess_exec(...)
asyncio.create_subprocess_shell(...)
subprocess.run(...)
subprocess.call(...)
subprocess.check_call(...)
subprocess.check_output(...)
subprocess.Popen(...)
os.system(...)               # crude but catches sloppy spawns
os.popen(...)                # ditto
```

These can be aliased (`sp.run`, `asyncio.create_subprocess_exec`, etc.). The AST walker must match by call attribute, not name string, so aliases are detected correctly via the import binding.

### Compliant import patterns

A file is considered compliant if it imports any of:

```python
from universal_agent.services.worker_exit_classifier import classify_worker_exit
from universal_agent.services.worker_exit_classifier import WorkerExit
from universal_agent.services.worker_exit_classifier import park_task_for_protocol_violation
from universal_agent.task_hub import record_worker_pid
from universal_agent.task_hub import resolve_max_runtime_seconds
```

Any one of the above is sufficient to consider the file "wired into the protocol" (the check doesn't try to prove every spawn site within the file uses the helper — that's beyond AST scope). Files that have multiple spawns where SOME are wired and SOME aren't are still considered compliant; the protocol assumes per-file ownership.

### Out-of-scope (always excluded)

- `tests/` — test code routinely spawns subprocesses for fixtures; not async work units.
- `scripts/` — operator-tier installation / setup scripts; not async work units.
- `.venv/`, `node_modules/`, vendored libs.
- Generated code (none currently in `src/universal_agent/` but defensive exclusion).

Only `src/universal_agent/` is walked.

---

## Allowlist design

**File:** `tests/unit/task_observability_coverage_allowlist.txt`

**Format:** one relative file path per line, with optional `#` comments. Example:

```
# Files that spawn subprocesses but predate the Task Hub Observability Protocol.
# Adding a new entry here requires explicit operator approval — the protocol
# is the long-term standard; the allowlist exists only to make adoption
# incremental, not to permit drift.
#
# Format: <repo-relative path>     # <reason>

src/universal_agent/services/some_legacy_runner.py  # subprocess used for one-off CLI; not part of task lifecycle
src/universal_agent/some_other_file.py              # health-check fork, no task semantics
```

**Ratchet semantics:** the test loads the allowlist, computes the actual set of violating files in the current tree, and:

- Fails if any violating file is NOT in the allowlist.
- Warns (but does not fail) if any allowlisted file is no longer a violation (so the allowlist can be tightened).

The "warn don't fail" on dead allowlist entries is deliberate — we don't want a passing-then-failing CI flake when someone wires a legacy site.

### Initial allowlist contents

Computed at Ship 5 implementation time by running the AST walker against the current tree and snapshotting the violations. Each entry must be reviewed: is this file actually a task unit that should be wired (defer to follow-up PR), or is it infrastructure that legitimately doesn't fit the protocol (permanent allowlist)?

Likely allowlist candidates (to be verified during implementation):

- Cron service itself (`src/universal_agent/cron_service.py`) — spawns the work; IS the wiring; importing `classify_worker_exit` for use elsewhere in the file should remove this from the allowlist
- VP CLI client (`src/universal_agent/vp/clients/claude_cli_client.py`) — already wired, should pass without allowlist entry
- Demo workspace (`src/universal_agent/services/cody_implementation.py`) — already wired, should pass

For any spawn site where the file is NOT in the allowlist AND does NOT import the helpers, the test must fail. That's the whole point.

---

## Implementation details

### Test file: `tests/unit/test_task_observability_coverage.py`

Structure:

```python
"""Hermes Ship 5 — Task Hub Observability Protocol enforcement test.

Ratchets coverage of the observability protocol across src/universal_agent/.
New spawn-call sites must either import from worker_exit_classifier (and
implicitly call its helpers) OR be explicitly listed in
tests/unit/task_observability_coverage_allowlist.txt.

This is the long-term gate for the protocol; see
docs/03_Operations/108_Task_Hub_Observability_Protocol.md for the rules
this test enforces.
"""

from __future__ import annotations

import ast
import pathlib
from typing import Iterable

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "universal_agent"
ALLOWLIST_FILE = REPO_ROOT / "tests" / "unit" / "task_observability_coverage_allowlist.txt"

# Call expressions that count as "spawning a subprocess."
SUBPROCESS_CALLS = {
    "subprocess": {"run", "call", "check_call", "check_output", "Popen"},
    "sp": {"run", "call", "check_call", "check_output", "Popen"},  # common alias
    "asyncio": {"create_subprocess_exec", "create_subprocess_shell"},
    "os": {"system", "popen"},
}

# Imports that signal "this file is wired into the observability protocol."
COMPLIANT_IMPORTS = {
    ("universal_agent.services.worker_exit_classifier", "classify_worker_exit"),
    ("universal_agent.services.worker_exit_classifier", "WorkerExit"),
    ("universal_agent.services.worker_exit_classifier", "park_task_for_protocol_violation"),
    ("universal_agent.task_hub", "record_worker_pid"),
    ("universal_agent.task_hub", "resolve_max_runtime_seconds"),
}


def _iter_src_files() -> Iterable[pathlib.Path]:
    for path in SRC_ROOT.rglob("*.py"):
        # Skip __pycache__ etc.
        if "__pycache__" in path.parts:
            continue
        yield path


def _file_has_subprocess_call(tree: ast.AST) -> bool:
    """Walk the AST for any Call node whose attribute chain matches SUBPROCESS_CALLS."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                module = func.value.id
                attr = func.attr
                if module in SUBPROCESS_CALLS and attr in SUBPROCESS_CALLS[module]:
                    return True
            # Also catch directly-imported names like `Popen(...)` after
            # `from subprocess import Popen`.
            if isinstance(func, ast.Name):
                if func.id in {"run", "Popen", "call", "check_call", "check_output", "create_subprocess_exec", "create_subprocess_shell"}:
                    # Could be a false-positive (different `run` function in scope).
                    # Conservative: include for now; allowlist absorbs noise.
                    return True
    return False


def _file_imports_compliant_helper(tree: ast.AST) -> bool:
    """Detect imports from worker_exit_classifier or the task_hub observability helpers."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                if (module, alias.name) in COMPLIANT_IMPORTS:
                    return True
    return False


def _load_allowlist() -> set[str]:
    if not ALLOWLIST_FILE.exists():
        return set()
    out: set[str] = set()
    for line in ALLOWLIST_FILE.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.add(line)
    return out


def test_subprocess_spawns_use_observability_protocol():
    """Every file in src/universal_agent/ that spawns a subprocess must
    either import from worker_exit_classifier (implicit protocol use) or
    be listed in the allowlist. New violations should fix the spawn site
    rather than add to the allowlist."""
    allowlist = _load_allowlist()
    violations: list[str] = []
    stale_allowlist: list[str] = []

    actual_violating: set[str] = set()
    for path in _iter_src_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        if not _file_has_subprocess_call(tree):
            continue
        if _file_imports_compliant_helper(tree):
            continue
        rel = str(path.relative_to(REPO_ROOT))
        actual_violating.add(rel)
        if rel not in allowlist:
            violations.append(rel)

    for rel in sorted(allowlist):
        if rel not in actual_violating:
            stale_allowlist.append(rel)

    # Stale entries are a soft warning (printed for visibility) but don't fail.
    if stale_allowlist:
        print("\nAllowlist entries no longer needed (file is now compliant):")
        for entry in stale_allowlist:
            print(f"  - {entry}")

    assert not violations, (
        "The following files spawn subprocesses without importing the Task Hub "
        "Observability Protocol helpers. Wire them via "
        "`services.worker_exit_classifier` or add them to "
        f"{ALLOWLIST_FILE.relative_to(REPO_ROOT)} with justification.\n"
        "See docs/03_Operations/108_Task_Hub_Observability_Protocol.md for "
        "the protocol rules.\n\n"
        + "\n".join(f"  - {v}" for v in sorted(violations))
    )
```

### Allowlist file: `tests/unit/task_observability_coverage_allowlist.txt`

Initial contents — computed at implementation time by running the AST walker once and inspecting the output. The plan target is **as few entries as possible** — every entry is a known coverage gap.

### Verification

Two-stage local verification:

1. **Baseline:** with the allowlist set to the empty set, run the test and capture every file it flags. Inspect each one — for each:
   - If the file is actually wired (imports the helpers) but a different name shadowed the AST detection: investigate; fix the detection (likely a missed alias) and re-run.
   - If the file is genuinely a coverage gap: add it to the allowlist with a comment explaining why it's allowed today.
2. **Re-run:** with the final allowlist, the test must pass.

Then `uv run pytest tests/unit/test_task_observability_coverage.py -x -q --no-header` should pass cleanly.

---

## Files touched

- `tests/unit/test_task_observability_coverage.py` — new file, ~150 LOC including the AST walker + assertion.
- `tests/unit/task_observability_coverage_allowlist.txt` — new file, initial allowlist + header comment.
- `docs/03_Operations/108_Task_Hub_Observability_Protocol.md` — add a section "Enforcement" referencing the new test + allowlist.
- `docs/Documentation_Status.md` — append Ship 5 entry to rolling log.

Estimated total LOC: ~200 (test + allowlist + doc additions).

---

## Branch / commit / ship

- **Branch:** `claude/hermes-ship-5-observability-lint-guard`
- **Commit message:**

```
feat(hermes-f-enforce): Task Hub Observability Protocol lint guard

Ratchets enforcement of the protocol shipped in Ship 4. CI now fails
on any new subprocess spawn in src/universal_agent/ that doesn't
import from worker_exit_classifier or appear on an explicit allowlist.

* New tests/unit/test_task_observability_coverage.py — AST-walks
  src/universal_agent/ for asyncio.create_subprocess_exec /
  subprocess.run|call|check_call|check_output|Popen / os.system / os.popen
  calls; for each file with such a call, verifies it imports
  classify_worker_exit / WorkerExit / park_task_for_protocol_violation
  from services.worker_exit_classifier OR record_worker_pid /
  resolve_max_runtime_seconds from task_hub. Files violating both
  conditions must be on the allowlist.
* New tests/unit/task_observability_coverage_allowlist.txt — initial
  set of known coverage gaps. Each entry justified inline. Entries
  should shrink over time as gaps are wired.
* docs/03_Operations/108_Task_Hub_Observability_Protocol.md gains
  an "Enforcement" section.

Ratchet semantics: new violations fail; stale allowlist entries warn
but don't fail (avoids CI flake when a legacy site gets wired).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

- **PR:** standard PAT-based auto-merge chain.
- **Post-merge verification:** none beyond CI green — the guard runs as part of PR-Validate, so once it ships, every subsequent PR is checked.

---

## Open questions / decisions baked in

| Decision | Why |
|---|---|
| Ratchet (allowlist of existing violations) vs strict (everything must comply on day 1) | Strict requires a backfill PR for every existing subprocess site; ratchet ships immediately and improves over time. Lower friction, same long-run outcome. |
| AST walk vs grep | AST is more precise; grep would have false positives (e.g. comments mentioning `subprocess.run`). Slight implementation cost worth it. |
| Run as part of pytest tests/unit | Already part of pr-validate gate. No extra workflow needed. |
| Stale allowlist entries warn, don't fail | If a contributor wires a site and the file becomes compliant, we don't want CI to fail on the same PR demanding allowlist removal. Warn → operator can clean up in a follow-up. |
| Check imports at file level, not call-site level | Too noisy to demand every spawn call within a file proves it uses the helper. File-level check is the right granularity. |
| Don't lint `tests/` or `scripts/` | Test fixtures legitimately spawn subprocesses; scripts are not async task units. |
| Detect direct imports like `from subprocess import Popen` | Yes — false positives caught by allowlist. |

---

## Execution sequence (when resuming after compaction)

1. Verify Ship 4 has shipped: `gh pr list --state merged --search "feat(hermes-f-final)" --limit 5` should show the protocol-doc PR merged. If not, ship Ship 4 first.
2. Branch off latest `origin/main`.
3. Implement the test file. Run it with an empty allowlist to compute the initial violation set.
4. Inspect each violation — wire it (preferred) OR add it to the allowlist with explicit comment justification.
5. Re-run test → must pass.
6. Run `uv run pytest tests/unit/test_task_observability_coverage.py -x -q --no-header` and the broader observability test suite (same suite from Ship 4 plan) — all must pass.
7. Update `108_Task_Hub_Observability_Protocol.md` with the Enforcement section.
8. Commit with the message template above; PR; PAT-merge chain handles deploy.
9. After merge: confirm next un-related PR validates against the new guard (look for the test in the PR-Validate output of any subsequent PR).
