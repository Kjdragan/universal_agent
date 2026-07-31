"""Regression guard: every public function in the rest of the ``tgtg`` package is typed.

``tests/unit/test_tgtg_cli_type_hints.py`` already pins the convention onto
``tgtg/cli.py``. The package's own docstring states that config/scanner/
targets/purchaser are fully annotated, and this test extends the same pin to the
remaining public-API modules — ``dashboard.py``, ``monitor.py``, and
``notifier.py`` — so the annotations cannot silently regress.

AST/text based (not import based) for the same reason as the cli test:
``monitor.py`` imports the third-party ``tgtg`` client, which is not installed in
CI, so the module is not importable here. Parsing the source as text lets us
assert the annotations exist without executing any imports.
"""
from __future__ import annotations

import ast
from pathlib import Path

TGTG_DIR = (
    Path(__file__).resolve().parents[2] / "src" / "universal_agent" / "tgtg"
)

# Modules whose public-API surface we pin to "fully annotated". cli.py has its
# own dedicated test (with an expected-handler allowlist); these cover the rest.
MODULES = ["dashboard.py", "monitor.py", "notifier.py"]


def _module_level_public_functions(
    module_rel: str,
) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    tree = ast.parse((TGTG_DIR / module_rel).read_text(encoding="utf-8"))
    return [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    ]


def _collect_offenders() -> dict[str, list[str]]:
    offenders: dict[str, list[str]] = {}
    for module_rel in MODULES:
        for fn in _module_level_public_functions(module_rel):
            issues: list[str] = []
            if fn.returns is None:
                issues.append(f"{fn.name}: missing return annotation")
            args = fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs
            for arg in args:
                if arg.arg in ("self", "cls"):
                    continue
                if arg.annotation is None:
                    issues.append(f"{fn.name}: parameter '{arg.arg}' missing annotation")
            if issues:
                offenders.setdefault(module_rel, []).extend(issues)
    return offenders


def test_all_tgtg_public_functions_have_full_type_hints() -> None:
    offenders = _collect_offenders()
    assert not offenders, "untyped public signatures in tgtg package:\n" + "\n".join(
        f"  {mod}: {issue}" for mod, issues in offenders.items() for issue in issues
    )
