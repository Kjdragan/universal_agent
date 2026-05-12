"""Hermes Ship 5 — Task Hub Observability Protocol enforcement test.

Ratchets coverage of the observability protocol across
``src/universal_agent/``.  New spawn-call sites must either import from
``services/worker_exit_classifier.py`` (and implicitly call its
helpers) OR be explicitly listed in
``tests/unit/task_observability_coverage_allowlist.txt``.

This is the long-term CI gate for the protocol; see
``docs/03_Operations/129_Task_Hub_Observability_Protocol.md`` for the
rules this test enforces.

Ratchet semantics:

* **New violation:** any file with a subprocess spawn that does NOT
  import from the protocol helpers AND is NOT on the allowlist fails
  the test.  Wire the file via ``worker_exit_classifier`` OR add it
  to the allowlist with explicit rationale.
* **Stale allowlist entry:** any allowlist entry that no longer
  corresponds to a real violation is WARNED but does not fail (avoids
  the "wiring a legacy site fails the same PR" footgun).  Operator
  can remove the entry in a follow-up PR.
"""

from __future__ import annotations

import ast
import pathlib
from typing import Iterable

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "universal_agent"
ALLOWLIST_FILE = REPO_ROOT / "tests" / "unit" / "task_observability_coverage_allowlist.txt"

# Module-attribute call expressions that count as "spawning a subprocess."
# Keyed by the module-binding name; values are the attribute set.
SUBPROCESS_CALLS: dict[str, set[str]] = {
    "subprocess": {"run", "call", "check_call", "check_output", "Popen"},
    # Common aliases used in this codebase (``import subprocess as sp``).
    "sp": {"run", "call", "check_call", "check_output", "Popen"},
    "asyncio": {"create_subprocess_exec", "create_subprocess_shell"},
    "os": {"system", "popen"},
}

# Bare-name calls that survive ``from subprocess import Popen`` style
# imports.  Conservative: may produce false positives (a different
# function with the same name in scope).  The allowlist absorbs noise.
BARE_SUBPROCESS_NAMES: set[str] = {
    "Popen",
    "create_subprocess_exec",
    "create_subprocess_shell",
}

# (module, name) tuples that signal "this file is wired into the
# observability protocol" — the explicit ``from M import N`` form.
COMPLIANT_IMPORTS: set[tuple[str, str]] = {
    ("universal_agent.services.worker_exit_classifier", "classify_worker_exit"),
    ("universal_agent.services.worker_exit_classifier", "WorkerExit"),
    ("universal_agent.services.worker_exit_classifier", "park_task_for_protocol_violation"),
    ("universal_agent.services.worker_exit_classifier", "PROTOCOL_VIOLATION_REASONS"),
    ("universal_agent.services.worker_exit_classifier", "task_was_closed_normally"),
    ("universal_agent.services.worker_exit_classifier", "find_active_assignment_for_task"),
    ("universal_agent.task_hub", "record_worker_pid"),
    ("universal_agent.task_hub", "resolve_max_runtime_seconds"),
    # cron_task_hub_link is the cron-pattern helper; counts as compliant.
    ("universal_agent.services.cron_task_hub_link", "ensure_cron_task_link"),
    ("universal_agent.services.cron_task_hub_link", "close_cron_task_link"),
}

# Helper-function attribute names that indicate the file is wired into
# the protocol, even when imported as a module attribute (e.g., after
# ``from universal_agent import task_hub`` the file calls
# ``task_hub.record_worker_pid(...)``).  Matched via AST Attribute
# nodes ``<x>.<helper>``.
COMPLIANT_HELPER_ATTRS: set[str] = {
    "record_worker_pid",
    "resolve_max_runtime_seconds",
    "classify_worker_exit",
    "park_task_for_protocol_violation",
    "task_was_closed_normally",
    "find_active_assignment_for_task",
    "ensure_cron_task_link",
    "close_cron_task_link",
    # The pre-Hermes-F primitives ``_open_run`` / ``_close_run`` are
    # also part of the protocol run-history surface.  Files that call
    # them directly (e.g., the cron service after Ship 4) are compliant.
    "_open_run",
    "_close_run",
}

# Module-name suffixes that, if imported (``import X.Y.M`` or
# ``from X import M``), indicate protocol awareness even without
# naming a specific helper.  Counts as compliant.
COMPLIANT_MODULE_SUFFIXES: set[str] = {
    "worker_exit_classifier",
    "cron_task_hub_link",
}


def _iter_src_files() -> Iterable[pathlib.Path]:
    for path in SRC_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        yield path


def _file_has_subprocess_call(tree: ast.AST) -> bool:
    """Walk the AST for any Call node whose attribute chain matches
    SUBPROCESS_CALLS, OR a bare-name call matching BARE_SUBPROCESS_NAMES."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            module = func.value.id
            attr = func.attr
            if module in SUBPROCESS_CALLS and attr in SUBPROCESS_CALLS[module]:
                return True
        if isinstance(func, ast.Name) and func.id in BARE_SUBPROCESS_NAMES:
            return True
    return False


def _file_imports_compliant_helper(tree: ast.AST) -> bool:
    """Detect any of three flavors of "this file is wired into the
    observability protocol":

    1. Explicit ``from M import N`` for a (module, name) in
       COMPLIANT_IMPORTS.
    2. Module import whose final dotted segment is in
       COMPLIANT_MODULE_SUFFIXES — covers
       ``from universal_agent.services import worker_exit_classifier``
       and ``from universal_agent.services.cron_task_hub_link import *``
       style usage.
    3. Attribute call ``<x>.<helper>(...)`` where ``<helper>`` is in
       COMPLIANT_HELPER_ATTRS.  Covers the common pattern of
       ``from universal_agent import task_hub`` followed by
       ``task_hub.record_worker_pid(...)``.

    Any one of the three is sufficient.  Walks the whole tree so
    lazy/function-local imports are detected.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            # Flavor 2: module-level "from X.Y import Z" where Z is a
            # compliant submodule.
            for alias in node.names:
                if alias.name in COMPLIANT_MODULE_SUFFIXES:
                    return True
                if (module, alias.name) in COMPLIANT_IMPORTS:
                    return True
            # Flavor 2b: "from X.Y.M import ..." where M is compliant.
            if module:
                final_segment = module.rsplit(".", 1)[-1]
                if final_segment in COMPLIANT_MODULE_SUFFIXES:
                    return True
        elif isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name or ""
                final_segment = name.rsplit(".", 1)[-1]
                if final_segment in COMPLIANT_MODULE_SUFFIXES:
                    return True
        elif isinstance(node, ast.Attribute):
            if node.attr in COMPLIANT_HELPER_ATTRS:
                return True
    return False


def _load_allowlist() -> set[str]:
    if not ALLOWLIST_FILE.exists():
        return set()
    out: set[str] = set()
    for raw in ALLOWLIST_FILE.read_text(encoding="utf-8").splitlines():
        # Strip inline comments + trim.
        line = raw.split("#", 1)[0].strip()
        if line:
            out.add(line)
    return out


def test_subprocess_spawns_use_observability_protocol(capsys) -> None:
    """Every file in src/universal_agent/ that spawns a subprocess must
    either import from worker_exit_classifier / cron_task_hub_link /
    task_hub observability helpers (implicit protocol use) OR be
    listed in the allowlist.

    Wire new spawn sites via the helpers; only allowlist with
    operator-approved rationale.
    """
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

    # Stale entries are a soft warning — printed for visibility, no fail.
    if stale_allowlist:
        print("\nAllowlist entries no longer needed (file is now compliant):")
        for entry in stale_allowlist:
            print(f"  - {entry}")
        print(
            "Tighten the allowlist in a follow-up PR; this run still "
            "passes."
        )

    assert not violations, (
        "The following files spawn subprocesses without importing the "
        "Task Hub Observability Protocol helpers. Wire them via "
        "`services.worker_exit_classifier` / `services.cron_task_hub_link` "
        "/ `task_hub.record_worker_pid` OR add them to "
        f"{ALLOWLIST_FILE.relative_to(REPO_ROOT)} with explicit "
        "operator-approved justification.\n"
        "See docs/03_Operations/129_Task_Hub_Observability_Protocol.md "
        "for the protocol rules.\n\n"
        + "\n".join(f"  - {v}" for v in sorted(violations))
    )


def test_allowlist_format_is_valid() -> None:
    """Sanity check the allowlist file's format: every non-comment line
    must reference a file path that exists under the repo root.  This
    catches typos when an allowlist entry is added."""
    allowlist = _load_allowlist()
    missing: list[str] = []
    for rel in allowlist:
        full = REPO_ROOT / rel
        if not full.exists():
            missing.append(rel)
    assert not missing, (
        "Allowlist entries reference non-existent files (typo? deleted?):\n"
        + "\n".join(f"  - {m}" for m in sorted(missing))
    )


def test_compliant_imports_dict_is_non_empty() -> None:
    """Regression guard: COMPLIANT_IMPORTS must list at least
    ``classify_worker_exit``.  If the set is ever accidentally emptied
    (e.g., bad refactor), this test fails fast.  Without it, every
    spawn-site file would suddenly be a violation."""
    expected_keys = {
        "classify_worker_exit",
        "park_task_for_protocol_violation",
        "ensure_cron_task_link",
        "record_worker_pid",
    }
    actual_keys = {name for (_module, name) in COMPLIANT_IMPORTS}
    missing = expected_keys - actual_keys
    assert not missing, (
        f"COMPLIANT_IMPORTS lost key entries (refactor regression?): {missing}"
    )
