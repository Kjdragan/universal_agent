"""
Registry drift check — keep the Task Type & Mission System Registry honest about
which systems are LIVE vs DEAD, so a stale status claim FAILS CI instead of
misleading someone (human or agent) months later.

Target doc: ``project_docs/01_architecture/07_task_type_registry.md``.

``scripts/doc_audit.py`` already verifies that EVERY ``file::symbol`` citation in
every doc resolves in code. This script adds the STATUS-CONDITIONAL assertions
doc_audit structurally cannot make — the inverse-drift guards that protect the
registry's whole reason for existing ("which one is canonical, and why is *that*
one still in the tree if it's dead?"):

  1. REMOVED-BUT-ALIVE  — a row marked ``removed`` must NOT cite a ``file::symbol``
     that still resolves as live code, UNLESS the row itself says the residue is a
     stub / backfill / dead-module / no-op (the doc's own words for "intentionally
     still present but dead"). This catches the drift doc_audit can't: the doc says
     X is dead, but X was quietly revived — the exact "misled by dead code" trap.

  2. CANONICAL-BUT-GONE — a row marked ``canonical`` / ``active_secondary`` /
     ``deprecated`` must cite symbols that DO resolve. (Defense in depth: this also
     runs via the pr-validate unit test on EVERY PR — including code-only PRs that
     never touch project_docs/ and so never trip doc-audit's path filter — so a
     deletion that orphans a registry citation is caught at the source.)

  3. FROZENSET POINTER  — the scheduling section now DEFERS to the
     ``SYSTEMD_MIGRATED_SYSTEM_JOBS`` frozenset + ``is_migrated_to_systemd()`` in
     ``systemd_migrated_jobs.py`` instead of re-enumerating migrated jobs (the
     Step-1 reshape). Guard that pointer target: the frozenset must exist and be
     non-empty and the predicate must be defined, so the deferral can't silently
     rot into a dangling reference.

Stdlib-only. Reuses doc_audit's citation resolver so resolution semantics stay
identical (same search roots, vendored-dir pruning, basename index).

Usage:
    python scripts/registry_drift_check.py                 # exit 1 on drift
    python scripts/registry_drift_check.py --warn-only      # report only
    python scripts/registry_drift_check.py --registry PATH  # audit a specific file (tests)
"""

from __future__ import annotations

import argparse
import ast
import os
from pathlib import Path
import re
import sys

# Reuse doc_audit's resolver verbatim so "resolves" means the same thing in both
# gates. doc_audit lives beside this file in scripts/; make the import CWD-proof.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from doc_audit import (  # noqa: E402  (import after sys.path tweak by design)
    REPO_ROOT,
    SYMBOL_REF,
    _resolve_cited_files,
)

REGISTRY_REL = "project_docs/01_architecture/07_task_type_registry.md"
FROZENSET_REL = "src/universal_agent/systemd_migrated_jobs.py"

STATUSES = {"canonical", "active_secondary", "deprecated", "removed", "unclear"}
MUST_RESOLVE = {"canonical", "active_secondary", "deprecated"}

# Words the registry uses to mean "intentionally still present in the tree but
# dead" — when a `removed` row carries one of these, a still-resolving symbol is
# expected (a backfill list entry, a stub, a dead module pending deletion), NOT
# drift. Matched case-insensitively as substrings of the row text.
DEAD_RESIDUE_MARKERS = ("backfill", "stub", "dead module", "no-op", "no op")


def _clean_status_cell(cell: str) -> str:
    """Normalize a markdown table cell to its bare status keyword, if it is one."""
    return cell.strip().strip("*").strip("`").strip().lower()


def _symbol_resolves(fil: str, sym: str) -> bool:
    """True if `file::symbol` resolves — file found AND symbol token defined.
    Mirrors doc_audit.check_symbol_refs (leading token for Class.method)."""
    candidates = _resolve_cited_files(fil)
    if not candidates:
        return False
    base = sym.split(".")[0]
    for target in candidates:
        try:
            content = target.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if re.search(rf"\b{re.escape(base)}\b", content):
            return True
    return False


def _frozenset_job_count(src: str) -> int | None:
    """Count string-literal members of the SYSTEMD_MIGRATED_SYSTEM_JOBS assignment.
    Returns None if the name is never assigned (only referenced). AST-based so a
    `(#753)` in a comment can't confuse it the way text-splitting would."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets, value = [node.target.id], node.value
        elif isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            value = node.value
        else:
            continue
        if "SYSTEMD_MIGRATED_SYSTEM_JOBS" in targets and value is not None:
            return sum(
                1 for n in ast.walk(value)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
            )
    return None


def _table_rows(body: str):
    """Yield (lineno, cells) for every markdown table row, skipping the header
    separator (``|---|---|``). Cells are the raw between-pipe segments."""
    for i, line in enumerate(body.splitlines(), start=1):
        s = line.strip()
        if not s.startswith("|"):
            continue
        if re.fullmatch(r"\|[\s:|-]+\|", s):  # separator row
            continue
        cells = [c for c in s.strip("|").split("|")]
        yield i, cells


def check_registry(registry_path: Path) -> list[str]:
    """Return a list of drift messages (empty == clean)."""
    drift: list[str] = []
    body = registry_path.read_text(encoding="utf-8", errors="ignore")

    # --- Assertions 1 & 2: status-conditional symbol resolution, per table row.
    for lineno, cells in _table_rows(body):
        statuses_in_row = [
            _clean_status_cell(c) for c in cells if _clean_status_cell(c) in STATUSES
        ]
        if not statuses_in_row:
            continue
        status = statuses_in_row[0]
        if status == "unclear":
            continue
        row_text = " | ".join(cells)
        refs = [(m.group(1), m.group(2)) for m in SYMBOL_REF.finditer(row_text)]
        if not refs:
            continue
        row_lc = row_text.lower()
        for fil, sym in refs:
            resolves = _symbol_resolves(fil, sym)
            if status in MUST_RESOLVE and not resolves:
                drift.append(
                    f"line {lineno}: row marked `{status}` cites `{fil}::{sym}` "
                    f"which does NOT resolve in code — either it was removed (mark "
                    f"the row `removed`) or the citation is wrong."
                )
            elif status == "removed" and resolves:
                if not any(mk in row_lc for mk in DEAD_RESIDUE_MARKERS):
                    drift.append(
                        f"line {lineno}: row marked `removed` cites `{fil}::{sym}` "
                        f"which STILL resolves as live code — the system is not dead. "
                        f"Update the status, or, if it's a stub/backfill/dead module, "
                        f"say so in the row."
                    )

    # --- Assertion 3: the scheduling pointer target must be intact.
    frozenset_path = REPO_ROOT / FROZENSET_REL
    if not frozenset_path.exists():
        drift.append(
            f"scheduling pointer broken: `{FROZENSET_REL}` is missing — the registry "
            f"defers its migrated-job set to this file."
        )
    else:
        src = frozenset_path.read_text(encoding="utf-8", errors="ignore")
        job_count = _frozenset_job_count(src)
        if job_count is None:
            drift.append(
                "scheduling pointer broken: `SYSTEMD_MIGRATED_SYSTEM_JOBS` is not "
                f"assigned in `{FROZENSET_REL}` (the registry's machine source of truth)."
            )
        elif job_count == 0:
            drift.append(
                "scheduling pointer suspicious: `SYSTEMD_MIGRATED_SYSTEM_JOBS` "
                "appears empty — expected the migrated job-name set."
            )
        if "def is_migrated_to_systemd" not in src:
            drift.append(
                "scheduling pointer broken: `is_migrated_to_systemd()` not defined "
                f"in `{FROZENSET_REL}` (the predicate every surface uses)."
            )

    return drift


def main() -> None:
    ap = argparse.ArgumentParser(description="Task-type registry drift check")
    ap.add_argument("--warn-only", action="store_true", help="report only; never exit nonzero")
    ap.add_argument("--registry", default=str(REPO_ROOT / REGISTRY_REL),
                    help="path to the registry markdown (default: the canonical doc)")
    args = ap.parse_args()

    registry_path = Path(args.registry)
    if not registry_path.exists():
        print(f"registry_drift_check: registry not found at {registry_path}", file=sys.stderr)
        sys.exit(2)

    drift = check_registry(registry_path)
    print(f"== registry_drift_check: {registry_path.name} ==")
    if not drift:
        print("   no drift — every status claim matches code reality.")
        sys.exit(0)
    print(f"   DRIFT: {len(drift)} stale claim(s)")
    for d in drift:
        print(f"  [drift] {d}")
    sys.exit(0 if args.warn_only else 1)


if __name__ == "__main__":
    main()
